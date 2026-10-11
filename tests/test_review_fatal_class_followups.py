# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Review of 6ff9e36 (the fix for the 2026-10-11 dead loop), executed.

The 2026-10-11 class: a member that does not resolve on an interface the
script holds is the script engine's "Undeclared identifier" halt. No Except
runs, the Finally that would PostProcess is skipped, and after OK the
polling loop is DEAD. So every fixture member the engine would refuse here
raises AssertionError, which no translated Except catches - exactly as no
Pascal Except catches the halt. Each test fails on 6ff9e36 (the review's
target; most of these routes did not exist on 3aca481) and passes after.

* obj_batch_create(component_name=...) resolved through ActiveDoc, a local
  DECLARED ISch_Document, while the named route reads ISch_Lib-only
  members. DelphiScript narrows a reference to the declared type of the
  variable a call's result is assigned to, and a typed copy does not narrow
  it again (PCB.pas PCB_SetRuleProperties); the model below applies that
  rule to the real source text.
* A component found by name and sitting on part 0 is moved to part 1, as
  lib_set_current_component would; a multi-part symbol on part N stays.
* lib_batch_set_params writes a new PARAMETER's owner part as read, 0
  included (the primitive clamp to 1 does not apply to parameters).
* A by-name edit of a symbol that is not held redraws THAT symbol and never
  touches the held reference.
* audit_find_net_label_conflicts finds the pin's owner through a component
  iterator, never Pin.OwnerSchComponent.
* The bridge records dead_loop the moment a script_fatal box is seen,
  stamps last_fault.when, removes a fault recorded by another server on
  any success, and words loop_note by fault kind.
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments
from tests.test_library_popup_free import A, Comp, Dead, _held
from tests.test_live_editor_component_is_never_read import (
    E_LINE, EditLib, GenLib, NComp, Prim, _client, _edit_ns, _gen_ns)

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"
_LIB = load(_ALTIUM / "Library.pas", minimum=10)
_GENERIC = load(_ALTIUM / "Generic.pas", minimum=10)
_AUDIT = load(_ALTIUM / "Audit.pas", minimum=10)
_UNITS = sorted(p for p in _ALTIUM.glob("*.pas") if p.name != "Altium_MCP.pas")


# ---------------------------------------------------------------------------
# 1. Declared types: a document narrowed to ISch_Document is not an ISch_Lib
# ---------------------------------------------------------------------------

#: Members that exist on ISch_Lib and not on ISch_Document (the ones the
#: library resolvers read).
_LIB_ONLY = {"GetState_SchComponentByLibRef", "SchLibIterator_Create",
             "SchIterator_Destroy", "CurrentSchComponent",
             "AddSchComponent", "RemoveSchComponent"}


class AsDocument:
    """The SchLib as a variable DECLARED ``ISch_Document`` holds it after
    ``V := SchServer.GetCurrentSchDocument``. Base members pass through;
    an ISch_Lib-only member does not resolve (the engine halt)."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def __getattr__(self, name):
        if name in _LIB_ONLY:
            raise AssertionError(
                f"ISch_Lib member {name} read through an ISch_Document "
                f"reference (a typed copy does not narrow)")
        return getattr(object.__getattribute__(self, "_target"), name)

    def __setattr__(self, name, value):
        if name in _LIB_ONLY:
            raise AssertionError(
                f"ISch_Lib member {name} written through an ISch_Document "
                f"reference")
        setattr(object.__getattribute__(self, "_target"), name, value)


def _declared(body):
    """{variable (lower case): declared type} from a routine's Var block
    and parameter list."""
    head = strip_comments(body).split("\nBegin", 1)[0]
    out = {}
    for m in re.finditer(r"([\w ,]+?)\s*:\s*(\w+)\s*[;)]", head):
        decl = re.split(r"[(;]", m.group(1))[-1]
        decl = re.sub(r"^\s*(?:Var|Const)\b", "", decl, flags=re.I)
        for v in decl.split(","):
            if v.strip():
                out[v.strip().lower()] = m.group(2)
    return out


def _with_declared_narrowing(body):
    """The routine's source with every ``X := SchServer.GetCurrentSchDocument``
    into a variable DECLARED ISch_Document wrapped in AsDocument: the
    narrowing DelphiScript applies at that assignment."""
    types = _declared(body)

    def repl(m):
        if types.get(m.group(1).lower(), "").lower() == "isch_document":
            return f"{m.group(1)} := AsDocument(SchServer.GetCurrentSchDocument)"
        return m.group(0)

    return re.sub(r"\b(\w+)\s*:=\s*SchServer\.GetCurrentSchDocument\b",
                  repl, body)


def test_the_narrowing_model_is_applied_to_the_real_source():
    """The model must see the declaration, or the tests below prove nothing."""
    src = _with_declared_narrowing(_GENERIC["Gen_BatchCreate"])
    assert "ActiveDoc := AsDocument(SchServer.GetCurrentSchDocument)" in src


def _batch_queue():
    return [{"object_type": "eLine", "properties": "X=1",
             "container": "component"},
            {"object_type": "eLine", "properties": "X=2",
             "container": "component"}]


def test_obj_batch_create_by_name_resolves_through_an_isch_lib_local():
    """Review finding: Gen_BatchCreate passed ActiveDoc (ISch_Document) into
    ResolveEditLibComponent(SchLib : ISch_Lib, ...); the named route then
    read GetState_SchComponentByLibRef through it - the uncatchable halt,
    reached by the new obj_batch_create(component_name=...) argument."""
    events: list[str] = []
    named = NComp("X", events)
    lib = GenLib(A, comps=[named], events=events)
    ns = _gen_ns(lib, _client())
    ns["AsDocument"] = AsDocument
    queue = _batch_queue()
    ns["NextBatchOp"] = lambda _r: queue.pop(0) if queue else ""
    build({"Gen_BatchCreate":
           _with_declared_narrowing(_GENERIC["Gen_BatchCreate"])},
          ["Gen_BatchCreate"], ns)
    reply = ns["Gen_BatchCreate"]({"operations": "q", "component_name": "X"},
                                  "r")
    assert reply[0] == "OK", reply
    assert len(named.added) == 2
    assert events.count("pre") == events.count("post") == 1


def test_obj_batch_create_without_a_name_still_uses_the_held_symbol():
    events: list[str] = []
    held = NComp("MADE", events)
    lib = GenLib(A, events=events)
    ns = _gen_ns(lib, _client())
    ns["AsDocument"] = AsDocument
    _held(ns, lib, held, "MADE")
    queue = _batch_queue()
    ns["NextBatchOp"] = lambda _r: queue.pop(0) if queue else ""
    build({"Gen_BatchCreate":
           _with_declared_narrowing(_GENERIC["Gen_BatchCreate"])},
          ["Gen_BatchCreate"], ns)
    reply = ns["Gen_BatchCreate"]({"operations": "q"}, "r")
    assert reply[0] == "OK" and len(held.added) == 2


#: Resolvers that read ISch_Lib-only members off their first argument.
_LIB_RESOLVERS = ("ResolveEditLibComponent", "FindLibComponentInMemory",
                  "LookupLibComponent", "ScanLibForComponent")


def test_every_library_resolver_is_handed_an_isch_lib_typed_reference():
    """The class, not the site: the first argument of every resolver that
    reads ISch_Lib-only members is a local or parameter DECLARED ISch_Lib.
    6ff9e36's Gen_BatchCreate handed one its ISch_Document."""
    bad = []
    for path in _UNITS:
        for fname, body in load(path, minimum=1).items():
            code = strip_comments(body)
            types = _declared(body)
            for f in _LIB_RESOLVERS:
                for m in re.finditer(rf"\b{f}\s*\(\s*(\w+)\s*,", code):
                    if types.get(m.group(1).lower(), "").lower() != "isch_lib":
                        bad.append(f"{path.name} {fname}: {f}({m.group(1)}, "
                                   f"...) - {m.group(1)} is "
                                   f"{types.get(m.group(1).lower())}")
    assert bad == []


# ---------------------------------------------------------------------------
# 2. A symbol found by name on part 0 is moved to part 1
# ---------------------------------------------------------------------------

class PartComp(NComp):
    """A narrowed component whose CurrentPartID and DisplayMode are
    writable, logging each write."""

    def __init__(self, name, events, part=1, mode=0):
        super().__init__(name, events, part=part, mode=mode)
        object.__setattr__(self, "writes", [])

    @property
    def CurrentPartID(self):
        self.events.append(f"part:{self.LibReference}")
        return self._part

    @CurrentPartID.setter
    def CurrentPartID(self, v):
        self.writes.append(("CurrentPartID", v))
        object.__setattr__(self, "_part", v)

    def __setattr__(self, name, value):
        if name == "DisplayMode":
            self.writes.append(("DisplayMode", value))
        object.__setattr__(self, name, value)


def test_a_named_symbol_with_no_part_is_moved_to_part_one():
    """SelectLibComponentPart records that after a fresh open CurrentPartID
    can read 0, and lib_set_current_component writes part 1 / mode 0 for
    exactly that reason. 6ff9e36's named route only clamped the new
    primitive's owner, leaving the component itself on part 0."""
    events: list[str] = []
    named = PartComp("X", events, part=0, mode=1)
    lib = EditLib(A, comps=[named], events=events)
    ns = _edit_ns(lib, _client())
    build(_LIB, ["Lib_AddSymbolLine"], ns)
    reply = ns["Lib_AddSymbolLine"]({"x1": "0", "x2": "100",
                                     "component_name": "X"}, "r")
    assert reply[0] == "OK", reply
    assert named.writes == [("CurrentPartID", 1), ("DisplayMode", 0)]
    assert named._part == 1 and named.DisplayMode == 0
    assert named.added[0].OwnerPartId == 1
    assert events.index("part:X") < events.index("pre")


def test_a_named_multi_part_symbol_stays_on_its_part():
    events: list[str] = []
    named = PartComp("X", events, part=3, mode=0)
    lib = EditLib(A, comps=[named], events=events)
    ns = _edit_ns(lib, _client())
    build(_LIB, ["Lib_AddSymbolLine"], ns)
    reply = ns["Lib_AddSymbolLine"]({"x1": "0", "x2": "100",
                                     "component_name": "X"}, "r")
    assert reply[0] == "OK"
    assert named.writes == [], "a symbol already on a part must stay there"
    assert named.added[0].OwnerPartId == 3


# ---------------------------------------------------------------------------
# 3. lib_batch_set_params: a parameter's owner part is written as read
# ---------------------------------------------------------------------------

def _new_param_block():
    """Lib_BatchSetParams' "create the parameter" branch, executed as a
    routine of its own (the handler reads a text file line by line, which
    the evaluator does not model)."""
    body = strip_comments(_LIB["Lib_BatchSetParams"])
    start = body.index("NewParam := SchServer.SchObjectFactory(eParameter")
    end = body.index("Inc(Failed);", start) + len("Inc(Failed);")
    return ("Procedure NewParamBlock(Component : ISch_Component; "
            "ParamName : String; ParamValue : String);\nVar\n"
            "    NewParam : ISch_Parameter;\nBegin\n    "
            + body[start:end] + "\nEnd;\n")


@pytest.mark.parametrize("part,want", [(0, 0), (2, 2), (1, 1)])
def test_a_new_parameter_takes_the_part_as_read_zero_included(part, want):
    """Review finding: 6ff9e36 routed parameters through OwnerPartIdOf, the
    primitive clamp, so the part-0 reading LookupLibComponent's reopen
    produces pinned a whole-component parameter to part 1. 3aca481 wrote
    CurrentPartID as read."""
    events: list[str] = []
    comp = NComp("X", events, part=part, mode=0)
    lib = EditLib(A, comps=[comp], events=events)
    ns = _edit_ns(lib, _client())
    ns["eParameter"] = 9
    ns["Created"] = 0
    ns["Failed"] = 0
    build({"NewParamBlock": _new_param_block()}, ["NewParamBlock"], ns)
    ns["NewParamBlock"](comp, "MPN", "ABC")
    param = comp.added[0]
    assert (param.Name, param.Text) == ("MPN", "ABC")
    assert param.OwnerPartId == want
    assert param.OwnerPartDisplayMode == 0


def test_a_parameter_whose_part_cannot_be_read_gets_no_part_written():
    ns = {}
    build(_LIB, ["OwnerDisplayModeOf", "SetParamOwnerPart"], ns)

    class NoPart:
        DisplayMode = 1

        @property
        def CurrentPartID(self):
            from tests.pascal_eval import PascalError
            raise PascalError("raise")

    p = Prim()
    ns["SetParamOwnerPart"](p, NoPart())
    assert not hasattr(p, "OwnerPartId")
    assert p.OwnerPartDisplayMode == 1


# ---------------------------------------------------------------------------
# 4. A by-name edit redraws the symbol it edited, never the held reference
# ---------------------------------------------------------------------------

def test_a_by_name_edit_redraws_that_symbol_and_never_the_held_one():
    """Review finding: 6ff9e36's MarkLibDirty invalidated the HELD global on
    every edit, by-name edits of OTHER symbols included, and a by-name edit
    of an unheld displayed symbol got no component redraw at all. The held
    reference here is Dead with its library still looking open."""
    events: list[str] = []
    edited = NComp("OTHER", events)
    lib = EditLib(A, comps=[edited], events=events)
    ns = _edit_ns(lib, _client())
    _held(ns, lib, Comp("MADE"), "MADE")
    ns["LastCreatedLibComponent"] = Dead()
    ns["LookupLibComponent"] = ns["FindLibComponentInMemory"]
    ns["SchBeginModify"] = lambda o: None
    ns["SchEndModify"] = lambda o: None
    build(_LIB, ["Lib_SetComponentDescription"], ns)
    reply = ns["Lib_SetComponentDescription"](
        {"component_name": "OTHER", "description": "d"}, "r")
    assert reply[0] == "OK", reply
    assert edited.ComponentDescription == "d"
    assert "invalidate:OTHER" in events


# ---------------------------------------------------------------------------
# 5. Audit: the pin's owner through a component iterator
# ---------------------------------------------------------------------------

class _SchIter:
    def __init__(self, items):
        self.items, self.i = list(items), 0

    def AddFilter_ObjectSet(self, s):
        pass

    @property
    def FirstSchObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextSchObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


class _Pin:
    """A pin from its component's own iterator. It has NO OwnerSchComponent:
    reading one is the un-narrowed-accessor halt."""

    def __init__(self, num, x, y):
        self.Designator = num
        self.Location = SimpleNamespace(X=x, Y=y)

    def __getattr__(self, name):
        raise AssertionError(f"{name} read off a pin")


class _Comp:
    def __init__(self, desig, pins):
        self.Designator = SimpleNamespace(Text=desig)
        self.pins = pins
        self.destroyed = 0

    @property
    def SchIterator_Create(self):
        return _SchIter(self.pins)

    def SchIterator_Destroy(self, it):
        self.destroyed += 1


class _Sheet:
    def __init__(self, comps):
        self.comps = comps
        self.destroyed = 0

    @property
    def SchIterator_Create(self):
        return _SchIter(self.comps)

    def SchIterator_Destroy(self, it):
        self.destroyed += 1


def _owner_ns():
    ns = {"MkSet": lambda *a: set(a), "eSchComponent": 1, "ePin": 2}
    build(_AUDIT, ["PinOwnerDesignatorAt"], ns)
    return ns["PinOwnerDesignatorAt"]


def test_the_pins_owner_is_found_through_the_component_iterator():
    find = _owner_ns()
    r1 = _Comp("R1", [_Pin("1", 0, 0), _Pin("2", 100, 0)])
    u1 = _Comp("U1", [_Pin("1", 500, 500), _Pin("2", 100, 0)])
    sheet = _Sheet([r1, u1])
    assert find(sheet, 500, 500, "1") == "U1"
    assert find(sheet, 100, 0, "2") == "R1"       # the first owner found
    assert find(sheet, 100, 0, "7") == ""
    assert find(None, 0, 0, "1") == ""
    assert sheet.destroyed == 3
    assert r1.destroyed + u1.destroyed == 5


def test_no_unit_reads_owner_sch_component():
    """6ff9e36 still read Pin.OwnerSchComponent.Designator.Text (probe P36):
    an identifier that had run nowhere else, and an accessor handing back a
    component the script never obtained - the CurrentSchComponent class."""
    for path in _UNITS:
        code = strip_comments(path.read_text(encoding="utf-8-sig",
                                             errors="replace"))
        assert "OwnerSchComponent" not in code, path.name


# ---------------------------------------------------------------------------
# 6. The bridge: the recorded fault
# ---------------------------------------------------------------------------

def _bridge(tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    return AltiumBridge()


def test_a_script_fatal_box_seen_early_records_dead_loop_at_once(tmp_path):
    """Review finding: the early dialog probe raised without recording a
    fault, so right after the box (and after OK) app_get_status showed
    last_fault None with attached true - until a LATER call timed out."""
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    from eda_agent.bridge.fault_state import read_fault
    bridge = _bridge(tmp_path)
    probe = {"blocked": True, "dialog_count": 1, "kinds": ["script_fatal"],
             "summary": "script_fatal: 'Error' says 'Undeclared identifier'"}
    bridge._dialog_probe = lambda: probe
    start = time.monotonic() - 60
    with pytest.raises(AltiumTimeoutError) as err:
        bridge._poll_loop("r" * 32, tmp_path / "response_x.json",
                          tmp_path / "progress_x.json", tmp_path, 0.01,
                          start + 3600, 30, start, None)
    assert "will NOT resume" in str(err.value)
    fault = read_fault(tmp_path)
    assert fault is not None and fault["recovery"]["fault"] == "dead_loop"
    assert fault["when"], "the fault must say when it was found"


def test_a_script_fatal_box_at_the_heartbeat_limit_records_dead_loop(tmp_path):
    """The same box found when the heartbeat extensions run out: the engine
    HALTED, so the record is dead_loop (Stop + rerun StartMCPServer), not
    stuck_handler (wait)."""
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    from eda_agent.bridge.fault_state import read_fault
    bridge = _bridge(tmp_path)
    bridge._dialog_probe = lambda: {
        "blocked": True, "dialog_count": 1, "kinds": ["script_fatal"],
        "summary": "script_fatal: 'Error' says 'Undeclared identifier'"}
    progress = tmp_path / "progress_x.json"
    progress.write_text("{}", encoding="utf-8")   # a heartbeat, every time
    start = time.monotonic()                      # no early probe (3 s)
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_loop("r" * 32, tmp_path / "response_x.json", progress,
                          tmp_path, 0.0005, start, 0.0005, start, None)
    assert read_fault(tmp_path)["recovery"]["fault"] == "dead_loop"


def test_an_ordinary_dialog_seen_early_records_nothing(tmp_path):
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    from eda_agent.bridge.fault_state import read_fault
    bridge = _bridge(tmp_path)
    bridge._dialog_probe = lambda: {"blocked": True, "kinds": ["confirm"],
                                    "summary": "confirm: 'Save?'"}
    start = time.monotonic() - 60
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_loop("r" * 32, tmp_path / "response_x.json",
                          tmp_path / "progress_x.json", tmp_path, 0.01,
                          start + 3600, 30, start, None)
    assert read_fault(tmp_path) is None, (
        "a question waiting for a human is not a dead loop")


def test_a_noted_fault_carries_the_time_it_was_found(tmp_path):
    """Review finding: both _note_fault sites passed no time, so
    last_fault.when was always '' in production (the status test injected a
    value production never wrote)."""
    from eda_agent.bridge.fault_state import read_fault
    from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance
    bridge = _bridge(tmp_path)
    before = datetime.now().replace(microsecond=0)
    bridge._note_fault(tmp_path, recovery_guidance(DEAD_LOOP))
    when = datetime.fromisoformat(read_fault(tmp_path)["when"])
    assert before <= when <= datetime.now()


def test_status_shows_the_time_through_note_fault(tmp_path):
    from unittest.mock import patch
    from eda_agent.bridge.process_manager import AltiumSelection
    from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance
    bridge = _bridge(tmp_path)
    bridge._note_fault(tmp_path, recovery_guidance(DEAD_LOOP))
    bridge._attached = True
    with patch.object(bridge.process_manager, "select_altium_process",
                      return_value=AltiumSelection(None, [], "none")):
        status = bridge.get_altium_status()
    when = status["last_fault"]["when"]
    assert when and when in status["loop_note"]
    assert "NOT ANSWERING" in status["loop_note"]


def test_any_success_removes_a_fault_another_server_recorded(tmp_path):
    """Review finding: _clear_fault_if_any removed the file only when THIS
    process recorded the fault (or on its own first success), so a fault
    recorded by another MCP server sharing the workspace outlived every
    later success here, and status kept saying the loop was down."""
    from eda_agent.bridge.fault_state import read_fault
    from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance
    here = _bridge(tmp_path)
    here._clear_fault_if_any(tmp_path)            # its first success: swept
    other = _bridge(tmp_path)
    other._note_fault(tmp_path, recovery_guidance(DEAD_LOOP))
    assert read_fault(tmp_path) is not None
    here._clear_fault_if_any(tmp_path)            # a later success here
    assert read_fault(tmp_path) is None


def test_loop_note_does_not_call_a_stuck_handler_a_dead_loop():
    """A stuck_handler fault means a handler outran its heartbeat limit;
    the loop may only have been busy. The note used to say 'the polling
    loop was down' for every kind."""
    from eda_agent.bridge.altium_bridge import _loop_note
    stuck = _loop_note({"fault": "stuck_handler", "when": "T"})
    assert "busy" in stuck and "down" not in stuck and "at T" in stuck
    dead = _loop_note({"fault": "dead_loop", "when": ""})
    assert "NOT ANSWERING" in dead and " at " not in dead
    assert "app_ping" in _loop_note({"fault": "corrupt_response"})
