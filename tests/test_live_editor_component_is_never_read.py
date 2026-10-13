# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""No symbol edit reads a member off the editor's CurrentSchComponent.

FIELD (2026-10-11 20:20, bridge 2026.10.11.02, AD 26.10.1.5; the
RELEASE_VERIFICATION 39.4 P8 step). A fresh session, a SchLib opened,
nothing created through the bridge: ``lib_add_symbol_lines`` resolved its
target through GetTargetLibComponent, which fell back to
``SchLib.CurrentSchComponent``, and SetOwnerPart read
``Component.CurrentPartID`` inside a Try. The script engine showed
"Undeclared identifier: CurrentPartID" (a TMessageForm titled "Error"),
the Script IDE parked on Library.pas:122, and after OK the polling loop
was DEAD. A member that does not resolve on an un-narrowed interface is
not an exception: no Except runs, nothing is counted, the Finally that
would PostProcess is skipped. The 2026-08-08 Lib_GetPinList measurement
(SchIterator_Create on the same reference) was the same class.

CORRECTED 2026-10-12 (finding D-F2-1, bridge 2026.10.12.02): the same
halt came back with every CurrentSchComponent read removed. The cause
read from the source is the held global itself: never assigned at pump
start, so it held Empty, which is not Nil, and the old ``If
LastCreatedLibComponent <> Nil`` branch would hand Empty on. That is
consistent with both earlier halts (nothing records whether the
2026-08-08 session had ever set the global) and is to be confirmed by
session A (P8 on 2026.10.12.03). That the editor's
selection cannot be read is therefore UNPROVEN; not reading it stays
(harmless and conservative). tests/test_live_fresh_pump_globals.py owns
the fresh-pump case.

So every test here models the editor's component as ``Dead``: ANY member
access is an AssertionError, which no translated Except catches, exactly
as no Pascal Except catches the engine's halt. The routines are EXECUTED
through tests/pascal_eval.py (whole handlers where the evaluator can run
them), and each test fails on 3aca481.

* GetTargetLibComponent answers the held component or Nil, never the
  editor's selection; TargetLibComponentName answers '' with nothing held.
* Every Lib_Add* handler resolves through ResolveEditLibComponent: the held
  component (only in ITS library) or component_name found through the
  library and then held; NO_COMPONENT naming both otherwise, before any
  PreProcess.
* The owner part is read ONCE from the narrowed component BEFORE the
  PreProcess; SetOwnerPart takes plain integers and reads no component.
* MarkLibDirty touches no component at all; MarkLibDirtyComp redraws the
  component the handler itself edited (review of 6ff9e36: the shared path
  dereferenced the held global on every library edit).
* obj_create / obj_batch_create container=component and the library
  parameter delete take the same route.
* PCB_NormalizeVias reads no constraint member off any rule (values from
  the Descriptor; tests/test_popup_free_pcb.py has the full set).
* A static guard: no unit reads, copies or passes the CurrentSchComponent
  GETTER (only setters and Nil compares remain), and no IPCB_Rule-typed
  variable, parameter or rule-returning call is read past the base
  interface.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load, strip_comments
from tests.test_library_popup_free import (
    A, B, Client, Comp, Dead, Doc, SchLib, _held, _ns)

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"

E_LINE, E_PIN, E_RECT, E_ARC, E_POLY, E_LABEL = 1, 2, 3, 4, 5, 6


def _bodies(unit):
    return load(_ALTIUM / unit, minimum=10)


_LIB = _bodies("Library.pas")
_MAIN = _bodies("Main.pas")
_GENERIC = _bodies("Generic.pas")
_PCB = _bodies("PCB.pas")


def _have(bodies, names):
    """Only what the unit defines: on the old code a helper that did not
    exist yet is simply absent, and the handler then runs its old route."""
    return [n for n in names if n in bodies]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

class LiveDoc(Doc):
    def __init__(self):
        self.modified = 0

    def SetModified(self, v):
        self.modified += 1


class EditLib(SchLib):
    """A focused SchLib whose editor selection is whatever ``current`` is
    (Dead by default: any member read on it fails the test)."""

    def __init__(self, name, comps=(), current=None, events=None):
        super().__init__(name, comps=comps,
                         current=Dead() if current is None else current)
        self.events = events if events is not None else []

    @property
    def GraphicallyInvalidate(self):
        self.events.append("lib_invalidate")


class NComp(Comp):
    """A NARROWED component (the factory, the index or the iterator handed
    it over): every member works, and each read the tests care about is
    logged in order with the transaction events."""

    def __init__(self, name, events, part=1, mode=0):
        object.__setattr__(self, "events", events)
        object.__setattr__(self, "_part", part)
        object.__setattr__(self, "DisplayMode", mode)
        object.__setattr__(self, "LibReference", name)
        object.__setattr__(self, "added", [])
        object.__setattr__(self, "I_ObjectAddress", f"comp:{name}")
        object.__setattr__(self, "PartCount", 4)

    @property
    def CurrentPartID(self):
        self.events.append(f"part:{self.LibReference}")
        return self._part

    def AddSchObject(self, obj):
        self.events.append(f"add:{self.LibReference}")
        self.added.append(obj)

    @property
    def GraphicallyInvalidate(self):
        self.events.append(f"invalidate:{self.LibReference}")


class Prim(SimpleNamespace):
    """A factory primitive: a writable bag with by-value records."""


class Control:
    def __init__(self, events):
        self.events = events

    def PreProcess(self, doc, s):
        self.events.append("pre")

    def PostProcess(self, doc, s):
        self.events.append("post")


class Server:
    def __init__(self, lib, events):
        self.GetCurrentSchDocument = lib
        self.ProcessControl = Control(events)
        self.RobotManager = SimpleNamespace(SendMessage=lambda *a: None)
        self.FontManager = SimpleNamespace(GetFontID=lambda *a: 7)
        self.made = []
        self.destroyed = []

    def SchObjectFactory(self, kind, mode):
        p = Prim(kind=kind, I_ObjectAddress=f"prim{len(self.made)}",
                 Location=SimpleNamespace(X=0, Y=0),
                 Corner=SimpleNamespace(X=0, Y=0), Vertex={})
        self.made.append(p)
        return p

    def DestroySchObject(self, obj):
        self.destroyed.append(obj)


class _StrList(list):
    Free = None

    def Add(self, s):
        self.append(s)

    @property
    def Count(self):
        return len(self)

    def IndexOf(self, s):
        return self.index(s) if s in self else -1


class _StrListClass:
    @property
    def Create(self):
        return _StrList()


_HANDLERS = {
    "Lib_AddPin": ({"designator": "1", "name": "A"}, None),
    "Lib_AddSymbolRectangle": ({"x1": "0", "y1": "0", "x2": "100",
                                "y2": "100"}, None),
    "Lib_AddSymbolLine": ({"x1": "0", "x2": "100"}, None),
    "Lib_AddSymbolArc": ({"radius": "50"}, None),
    "Lib_AddSymbolPolygon": ({"vertices": "0,0,100,0,100,100"}, None),
    "Lib_AddPins": ({"pins": "q"}, [{"designator": "1", "name": "A"}]),
    "Lib_AddSymbolText": ({"texts": "q"}, [{"text": "HI"}]),
    "Lib_AddSymbolLines": ({"lines": "q"}, [{"x1": "0", "x2": "100"}]),
}

_LIB_HELPERS = ["SetOwnerPart", "OwnerPartIdOf", "OwnerDisplayModeOf",
                "NoEditComponentMessage", "ResolveEditLibComponent",
                "MarkLibDirty", "MarkLibDirtyComp", "SetParamOwnerPart",
                "SetOwnerPartUnlessGiven"]


def _edit_ns(lib, client, queue=None):
    """The namespace a handler runs in: the real library helpers (from
    tests/test_library_popup_free._ns), the real SetOwnerPart / MarkLibDirty
    / SchRegisterObject, and fixtures for everything Altium answers."""
    events = lib.events
    server = Server(lib, events)
    notes: list[str] = []
    ops = list(queue or [])

    def next_op(_remaining):
        return ops.pop(0) if ops else ""

    ns = _ns(client)
    ns.update({
        "SchServer": server,
        "Application": SimpleNamespace(ProcessMessages=None),
        "NoteNextStep": notes.append,
        "ExtractJsonValue": lambda p, k: p.get(k, ""),
        "BuildErrorResponse": lambda rid, code, msg: ("ERR", code, msg),
        "BuildSuccessResponse": lambda rid, data: ("OK", data),
        "EscapeJsonString": lambda s: s,
        "MilsToCoord": lambda m: int(m) * 10000,
        "StrToFloatDef": lambda s, d: float(s) if s else d,
        "Point": lambda x, y: (x, y),
        "StrToPinElectrical": lambda s: 0,
        "StrToIeeeSymbol": lambda s: 0,
        "NextBatchOp": next_op,
        "GetBatchField": lambda op, k: op.get(k, ""),
        "TStringList": _StrListClass(),
        "eLine": E_LINE, "ePin": E_PIN, "eRectangle": E_RECT, "eArc": E_ARC,
        "ePolygon": E_POLY, "eLabel": E_LABEL, "eCreate_Default": 0,
        "eSmall": 1, "SCHM_PrimitiveRegistration": 1,
        "_notes": notes, "_server": server,
    })
    build(_MAIN, ["SchRegisterObject"], ns)
    build(_LIB, _have(_LIB, _LIB_HELPERS), ns)
    return ns


def _run(handler, lib, client, params, queue=None):
    ns = _edit_ns(lib, client, queue)
    build(_LIB, [handler], ns)
    return ns, ns[handler](params, "r")


def _client():
    return Client({A: LiveDoc(), B: LiveDoc()})


# ---------------------------------------------------------------------------
# 1. The resolvers never answer the editor's selection
# ---------------------------------------------------------------------------

def test_target_component_is_the_held_one_or_nil_never_the_editor_selection():
    client = _client()
    ns = _ns(client)
    lib = EditLib(A)                       # editor selection: Dead
    assert ns["GetTargetLibComponent"](lib) is None
    held = Comp("MADE")
    _held(ns, lib, held, "MADE")
    assert ns["GetTargetLibComponent"](lib) is held
    client.docs.clear()                    # its library closed
    ns["LastCreatedLibComponent"] = Dead()
    assert ns["GetTargetLibComponent"](lib) is None
    assert ns["LastCreatedLibComponent"] is None


def test_target_name_is_empty_with_nothing_held():
    ns = _ns(_client())
    assert ns["TargetLibComponentName"](EditLib(A)) == ""


# ---------------------------------------------------------------------------
# 2. Every Lib_Add* handler, executed whole
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("handler", sorted(_HANDLERS))
def test_an_edit_with_nothing_held_answers_no_component_before_preprocess(
        handler):
    """The A4 step: fresh session, nothing held, the editor's selection the
    only candidate. 3aca481 handed it to SetOwnerPart (the dead loop)."""
    params, queue = _HANDLERS[handler]
    lib = EditLib(A)
    ns, reply = _run(handler, lib, _client(), dict(params), queue)
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert "component_name" in reply[2]
    assert "lib_set_current_component" in reply[2]
    assert "pre" not in lib.events, "a PreProcess was opened with no target"
    assert ns["_server"].made == [], "a primitive was made with no target"


@pytest.mark.parametrize("handler", sorted(_HANDLERS))
def test_component_name_lands_the_edit_on_the_named_symbol(handler):
    """component_name is found THROUGH THE LIBRARY (the index here), made
    current and held as lib_set_current_component does; the owner part is
    read from it once, BEFORE the PreProcess, and the transaction balances."""
    params, queue = _HANDLERS[handler]
    events: list[str] = []
    named = NComp("X", events, part=2)
    lib = EditLib(A, comps=[named], events=events)
    ns, reply = _run(handler, lib, _client(),
                     dict(params, component_name="X"), queue)
    assert reply[0] == "OK", reply
    assert len(named.added) == 1
    prim = named.added[0]
    assert (prim.OwnerPartId, prim.OwnerPartDisplayMode) == (2, 0)
    assert events.count("pre") == 1 and events.count("post") == 1
    assert events.index("part:X") < events.index("pre"), (
        "the owner part was read inside the PreProcess window")
    assert lib.CurrentSchComponent is named
    assert ns["LastCreatedLibComponent"] is named
    assert ns["LastCreatedLibComponentName"] == "X"
    assert "invalidate:X" in events, "MarkLibDirty did not redraw the target"


@pytest.mark.parametrize("handler", sorted(_HANDLERS))
def test_the_held_component_still_takes_an_edit_without_a_name(handler):
    params, queue = _HANDLERS[handler]
    events: list[str] = []
    held = NComp("MADE", events, part=1)
    lib = EditLib(A, events=events)
    ns = _edit_ns(lib, _client(), queue)
    _held(ns, lib, held, "MADE")
    build(_LIB, [handler], ns)
    reply = ns[handler](dict(params), "r")
    assert reply[0] == "OK", reply
    assert len(held.added) == 1
    assert events.index("part:MADE") < events.index("pre")
    assert "invalidate:MADE" in events


@pytest.mark.parametrize("handler", sorted(_HANDLERS))
def test_a_component_held_from_another_library_is_not_edited(handler):
    """Held from B while A is focused: the edit used to land in B while A
    was marked dirty. Now NO_COMPONENT, nothing added anywhere."""
    params, queue = _HANDLERS[handler]
    events: list[str] = []
    held = NComp("MADE", events)
    lib_a = EditLib(A, events=events)
    ns = _edit_ns(lib_a, _client(), queue)
    _held(ns, EditLib(B, events=events), held, "MADE")
    build(_LIB, [handler], ns)
    reply = ns[handler](dict(params), "r")
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert held.added == [] and "pre" not in events


def test_an_unknown_component_name_says_so():
    lib = EditLib(A)
    _ns_, reply = _run("Lib_AddSymbolLine", lib, _client(),
                       {"component_name": "NOPE"})
    assert reply[:2] == ("ERR", "NO_COMPONENT")
    assert "NOPE" in reply[2]
    assert "pre" not in lib.events


def test_an_explicit_owner_part_is_still_written_as_given():
    events: list[str] = []
    named = NComp("X", events, part=3)
    lib = EditLib(A, comps=[named], events=events)
    _ns_, reply = _run("Lib_AddPins", lib, _client(),
                       {"pins": "q", "component_name": "X"},
                       [{"designator": "1", "owner_part_id": "0"},
                        {"designator": "2"}])
    assert reply[0] == "OK"
    assert [p.OwnerPartId for p in named.added] == [0, 3]


# ---------------------------------------------------------------------------
# 3. SetOwnerPart reads no component; the part is read once, before
# ---------------------------------------------------------------------------

def test_set_owner_part_writes_the_integers_it_is_given():
    ns = {}
    build(_LIB, ["SetOwnerPart"], ns)
    o = Prim()
    ns["SetOwnerPart"](o, 3, 1)
    assert (o.OwnerPartId, o.OwnerPartDisplayMode) == (3, 1)


def test_owner_part_of_a_component_defaults_safely():
    ns = {}
    build(_LIB, ["OwnerPartIdOf", "OwnerDisplayModeOf"], ns)
    ev: list[str] = []
    assert ns["OwnerPartIdOf"](None) == 1
    assert ns["OwnerDisplayModeOf"](None) == 0
    assert ns["OwnerPartIdOf"](NComp("X", ev, part=2)) == 2
    assert ns["OwnerPartIdOf"](NComp("X", ev, part=0)) == 1, (
        "part 0 is the invisible bucket SelectLibComponentPart records")

    class Raises:
        @property
        def CurrentPartID(self):
            raise PascalError("raise")

        @property
        def DisplayMode(self):
            raise PascalError("raise")

    assert ns["OwnerPartIdOf"](Raises()) == 1
    assert ns["OwnerDisplayModeOf"](Raises()) == 0


def test_no_set_owner_part_caller_hands_it_a_component():
    for unit in ("Library.pas", "Generic.pas"):
        code = strip_comments((_ALTIUM / unit).read_text(encoding="utf-8"))
        for m in re.finditer(r"\bSetOwnerPart\((\w+),\s*(\w+)\s*\)", code):
            raise AssertionError(f"{unit}: {m.group(0)} passes a component")


# ---------------------------------------------------------------------------
# 4. MarkLibDirty touches no component; MarkLibDirtyComp the edited one
# ---------------------------------------------------------------------------

def test_mark_lib_dirty_never_touches_the_editor_selection():
    events: list[str] = []
    lib = EditLib(A, events=events)
    client = _client()
    ns = _edit_ns(lib, client)
    ns["MarkLibDirty"](lib)
    assert events == ["lib_invalidate"]
    assert client.docs[A.upper()].modified == 1


def test_mark_lib_dirty_never_dereferences_the_held_component():
    """Review of 6ff9e36: MarkLibDirty read
    LastCreatedLibComponent.GraphicallyInvalidate on EVERY library edit,
    gated only by LastCreatedLibIsLive, which answers True whenever it is
    unsure. A held symbol freed behind its back (closed and reopened outside
    the bridge, the Needs-probe case) then halted every later edit in that
    library. Here the held reference is Dead while its library still looks
    open: nothing may touch it."""
    events: list[str] = []
    client = _client()
    lib_a = EditLib(A, events=events)
    ns = _edit_ns(lib_a, client)
    _held(ns, lib_a, Comp("MADE"), "MADE")
    ns["LastCreatedLibComponent"] = Dead()     # freed, liveness unsure
    assert ns["LastCreatedLibIsLive"](0) is True
    ns["MarkLibDirty"](lib_a)
    assert events == ["lib_invalidate"]
    assert client.docs[A.upper()].modified == 1


def test_mark_lib_dirty_comp_redraws_the_component_it_is_handed():
    events: list[str] = []
    client = _client()
    lib_a = EditLib(A, events=events)
    ns = _edit_ns(lib_a, client)
    ns["LastCreatedLibComponent"] = Dead()     # never consulted
    edited = NComp("X", events)
    ns["MarkLibDirtyComp"](lib_a, edited)
    assert events == ["invalidate:X", "lib_invalidate"]
    events.clear()
    ns["MarkLibDirtyComp"](lib_a, None)
    assert events == ["lib_invalidate"]


# ---------------------------------------------------------------------------
# 5. Generic.pas: container=component and the library parameter delete
# ---------------------------------------------------------------------------

def _gen_ns(lib, client):
    ns = _edit_ns(lib, client)
    dirty: list[str] = []
    ns.update({
        "ResetPropertyDiag": lambda d: None,
        "ObjectTypeFromString": lambda s: E_LINE,
        "UnknownObjectTypeMessage": lambda s: "unknown",
        "UnsupportedSchProperty": lambda o, p: "",
        "ApplySetProperties": lambda o, p: None,
        "MarkDocDirtyByPath": dirty.append,
        "IntToStr": lambda n: str(int(n)),
    })
    return ns


def test_obj_create_into_a_component_with_nothing_held_refuses_first():
    lib = EditLib(A)
    ns = _gen_ns(lib, _client())
    build(_GENERIC, ["Gen_CreateObject"], ns)
    reply = ns["Gen_CreateObject"](
        {"object_type": "eLine", "properties": "X=1",
         "container": "component"}, "r")
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert "pre" not in lib.events
    assert ns["_server"].destroyed == ns["_server"].made


def test_obj_create_into_a_named_component_lands_there():
    events: list[str] = []
    named = NComp("X", events)
    lib = EditLib(A, comps=[named], events=events)
    ns = _gen_ns(lib, _client())
    build(_GENERIC, ["Gen_CreateObject"], ns)
    reply = ns["Gen_CreateObject"](
        {"object_type": "eLine", "properties": "X=1",
         "container": "component", "component_name": "X"}, "r")
    assert reply[0] == "OK", reply
    assert len(named.added) == 1
    assert events.count("pre") == events.count("post") == 1


def test_generic_component_creates_write_the_symbols_current_part():
    """D-A-1 (live 2026-10-13): container=component creates saved
    OwnerPartId=-1, a primitive nothing draws or counts. Both now write the
    narrowed component's current part, read BEFORE the PreProcess, as
    lib_add_symbol_lines does."""
    events: list[str] = []
    named = NComp("X", events, part=2, mode=1)
    lib = EditLib(A, comps=[named], events=events)
    ns = _gen_ns(lib, _client())
    build(_GENERIC, ["Gen_CreateObject"], ns)
    reply = ns["Gen_CreateObject"](
        {"object_type": "eLine", "properties": "X=1",
         "container": "component", "component_name": "X"}, "r")
    assert reply[0] == "OK", reply
    prim = named.added[0]
    assert (prim.OwnerPartId, prim.OwnerPartDisplayMode) == (2, 1)
    assert events.index("part:X") < events.index("pre")

    events2: list[str] = []
    named2 = NComp("X", events2, part=3)
    lib2 = GenLib(A, comps=[named2], events=events2)
    ns2 = _gen_ns(lib2, _client())
    queue = [{"object_type": "eLine", "properties": "X=1",
              "container": "component"},
             {"object_type": "eLine", "properties": "OwnerPartId=1",
              "container": "component"}]
    ns2["NextBatchOp"] = lambda _r: queue.pop(0) if queue else ""
    build(_GENERIC, ["Gen_BatchCreate"], ns2)
    reply = ns2["Gen_BatchCreate"]({"operations": "q", "component_name": "X"},
                                   "r")
    assert reply[0] == "OK", reply
    assert named2.added[0].OwnerPartId == 3
    assert getattr(named2.added[1], "OwnerPartId", None) != 3, (
        "an explicit OwnerPartId in the properties is the caller's to keep")
    assert events2.index("part:X") < events2.index("pre")


class GenLib(EditLib):
    """The batch path: the document it PreProcesses is the library."""

    @property
    def GraphicallyInvalidate(self):
        self.events.append("lib_invalidate")


def test_obj_batch_create_resolves_the_component_once_before_preprocess():
    events: list[str] = []
    named = NComp("X", events)
    lib = GenLib(A, comps=[named], events=events)
    queue = [{"object_type": "eLine", "properties": "X=1",
              "container": "component"},
             {"object_type": "eLine", "properties": "X=2",
              "container": "component"}]
    ns = _gen_ns(lib, _client())
    ns["NextBatchOp"] = (lambda q=list(queue): (lambda _r: q.pop(0)
                                                if q else ""))()
    build(_GENERIC, ["Gen_BatchCreate"], ns)
    reply = ns["Gen_BatchCreate"]({"operations": "q", "component_name": "X"},
                                  "r")
    assert reply[0] == "OK", reply
    assert len(named.added) == 2
    assert events.count("pre") == events.count("post") == 1

    # Nothing held, no name: each item NO_COMPONENT, the editor untouched.
    events2: list[str] = []
    lib2 = GenLib(A, events=events2)
    ns2 = _gen_ns(lib2, _client())
    ns2["NextBatchOp"] = (lambda q=list(queue): (lambda _r: q.pop(0)
                                                 if q else ""))()
    build(_GENERIC, ["Gen_BatchCreate"], ns2)
    reply2 = ns2["Gen_BatchCreate"]({"operations": "q"}, "r")
    assert reply2[0] == "OK"
    assert reply2[1].count("NO_COMPONENT") == 2
    assert events2.count("pre") == events2.count("post")


def _lib_branch(ns):
    body = strip_comments(_GENERIC["DeleteParametersAnyOwner"])
    start = body.index("If IsLib Then")
    end = body.index("If (OwnerDesig <> '') Or (ReducedFilter <> '') Then")
    src = ("Procedure LibBranch(SchDoc : ISch_Document; "
           "ReducedFilter : String);\nVar\n    Comp : ISch_Component;\n"
           "    IsLib : Boolean;\nBegin\n    IsLib := True;\n"
           + body[start:end] + "End;\n")
    got: list = []
    ns["RemoveMatchingParamsFromComponent"] = (
        lambda comp, flt, total: got.append(comp))
    ns["TotalMatched"] = 0
    build({"LibBranch": src}, ["LibBranch"], ns)
    return got


def test_library_parameter_delete_with_nothing_held_matches_nothing_and_says_why():
    lib = EditLib(A)
    ns = _edit_ns(lib, _client())
    got = _lib_branch(ns)
    ns["LibBranch"](lib, "Name=Value")
    assert got == []
    assert any("lib_component" in n for n in ns["_notes"]), ns["_notes"]


# ---------------------------------------------------------------------------
# 6. PCB_NormalizeVias: no constraint member off any rule
# ---------------------------------------------------------------------------

class BaseRule:
    """What FindDominantRuleForObject or the rule iterator answers: an
    IPCB_Rule binding. Its base Name and Descriptor resolve; a via-style
    member does not, and on the engine that is the uncatchable halt (the
    measured room-rule failure)."""

    def __init__(self, name, descriptor=""):
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_desc", descriptor)

    def __getattribute__(self, attr):
        if attr == "Name":
            return object.__getattribute__(self, "_name")
        if attr == "Descriptor":
            return object.__getattribute__(self, "_desc")
        if attr.startswith("__"):
            return object.__getattribute__(self, attr)
        raise AssertionError(f"member {attr} read off an IPCB_Rule")


class _It:
    def __init__(self, by_set):
        self.by_set, self.items, self.i = by_set, [], 0

    def AddFilter_ObjectSet(self, s):
        for k in s:
            self.items = list(self.by_set.get(k, []))

    def AddFilter_LayerSet(self, s):
        pass

    AddFilter_Method = AddFilter_LayerSet

    @property
    def FirstPCBObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextPCBObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


class _IList(list):
    def Add(self, o):
        self.append(o)

    @property
    def Count(self):
        return len(self)

    @property
    def Items(self):
        return self


class Board:
    def __init__(self, vias, rules, dominant):
        self.vias, self.rules, self.dominant = vias, rules, dominant
        self.FileName = "b.PcbDoc"

    @property
    def BoardIterator_Create(self):
        return _It({"via": self.vias, "rule": self.rules})

    def BoardIterator_Destroy(self, it):
        pass

    def FindDominantRuleForObject(self, via, kind):
        assert kind == 11, "the rule kind must be the literal 11"
        return self.dominant(via)


def _via(n):
    return SimpleNamespace(InComponent=False, InPolygon=False,
                           InDimension=False, I_ObjectAddress=f"V{n}",
                           Size=1, HoleSize=1)


def normalize_vias_helpers(ns):
    """What the merged PCB_NormalizeVias (2026.10.13: upstream's
    size/hole/net filters, histograms and refusals over carrier-local's
    read-only rule pass) calls besides itself, added to ``ns``.

    Built from the real Pascal: ViaPassesFilter and SetViaGeometry (the
    via writes, whose PCBM_BeginModify / EndModify the tests count).
    Modelled: ViaParamCoord (its Problem is a Var parameter, outside the
    evaluator's subset; absent keys answer 0 and leave Problem alone, the
    only case these fixtures send) and the TStringList Name=Value
    histogram helpers HistAdd / HistJson / ViaSizeKey (report formatting,
    no Altium member)."""
    import json as _json
    hist: dict = {}

    def hist_add(lst, key):
        d = hist.setdefault(id(lst), {})
        d[key] = d.get(key, 0) + 1

    ns.setdefault("Abs", abs)
    ns.setdefault("BoolToJsonStr", lambda b: "true" if b else "false")
    ns.setdefault("ExtractJsonValue", lambda params, key: (
        params.get(key, "") if isinstance(params, dict) else ""))
    ns.update({
        "ViaParamCoord": lambda params, key, problem: 0,
        "HistAdd": hist_add,
        "HistJson": lambda lst: _json.dumps(hist.get(id(lst), {})),
        "ViaSizeKey": lambda size, hole: f"{size / 10000:g}/{hole / 10000:g}",
    })
    build(_PCB, ["ViaPassesFilter"], ns)
    build(_bodies("PCBGeneric.pas"), ["SetViaGeometry"], ns)
    return ns


def _vias_run(board):
    msgs: list = []
    caught: list = []
    pre = []

    class Server:
        @property
        def PreProcess(self):
            pre.append("pre")

        @property
        def PostProcess(self):
            pre.append("post")

        def SendMessageToRobots(self, *a):
            msgs.append(a[2])

    ns = {
        "GetPCBBoardAnywhere": lambda d: board,
        "BuildErrorResponse": lambda rid, code, msg: ("ERR", code, msg),
        "BuildSuccessResponse": lambda rid, data: ("OK", data),
        "MarkDocDirtyByPath": lambda p: None,
        "RecordCaughtException": caught.append,
        "MkSet": lambda *a: list(a), "eViaObject": "via",
        "eRuleObject": "rule", "AllLayers": None, "eProcessAll": None,
        "CreateObject": lambda cls: _IList(), "TInterfaceList": None,
        "TStringList": _StrListClass(), "PCBServer": Server(),
        "c_Broadcast": 0, "c_NoEventData": 0,
        "PCBM_BeginModify": "Begin", "PCBM_EndModify": "End",
        "MilsToCoordF": lambda m: round(m * 10000),
        "MMToCoord": lambda mm: round(mm * 10000000 / 25.4),
        "StrToFloatDef": lambda s, d: float(s) if s else d,
    }
    normalize_vias_helpers(ns)
    build(_PCB, ["ViaRuleValue", "PCB_NormalizeVias"], ns)
    return ns["PCB_NormalizeVias"]({}, "r"), msgs, caught, pre


_DESC = ("Routing Via (PreferredHoleWidth=10mil) (MinWidth=20mil) "
         "(PreferedWidth=20mil) (All)")


def test_normalize_vias_reads_values_from_the_descriptor_not_a_rule_member():
    vias = [_via(n) for n in range(3)]
    rules = [BaseRule("Clear", "Clearance Constraint (Gap=10mil) (All)"),
             BaseRule("RoutingVias", _DESC)]
    board = Board(vias, rules, lambda v: BaseRule("RoutingVias"))
    reply, msgs, caught, pre = _vias_run(board)
    assert reply[0] == "OK"
    assert '"changed":3' in reply[1] and '"no_rule_values":0' in reply[1]
    assert [(v.Size, v.HoleSize) for v in vias] == [(200000, 100000)] * 3
    assert caught == [] and pre == ["pre", "post"]


def test_normalize_vias_leaves_a_via_whose_rule_has_no_values():
    vias = [_via(n) for n in range(2)]
    rules = [BaseRule("Thin", _DESC)]
    board = Board(vias, rules,
                  lambda v: BaseRule("Thin" if v.I_ObjectAddress == "V0"
                                     else "Unlisted"))
    reply, msgs, _caught, _pre = _vias_run(board)
    assert '"changed":1' in reply[1] and '"no_rule_values":1' in reply[1]
    assert [(v.Size, v.HoleSize) for v in vias] == [(200000, 100000), (1, 1)]
    assert msgs == ["Begin", "End"]


# ---------------------------------------------------------------------------
# 6b. PcbLib.CurrentComponent: the iterator's reference when it compares equal
# ---------------------------------------------------------------------------

class _Wrapper:
    """An iterator-returned footprint: a different object that compares
    EQUAL to the one it wraps (the pointer compare LibMakeCurrentVerified
    measured), touching nothing on the other side."""

    def __init__(self, target):
        self.target = target

    def __eq__(self, other):
        return other is self.target

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = object.__hash__


class _LibIter:
    def __init__(self, items):
        self.items, self.i = list(items), 0

    @property
    def FirstPCBObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextPCBObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


def _cur_fp(current, items):
    lib = SimpleNamespace(CurrentComponent=current,
                          LibraryIterator_Create=_LibIter(items),
                          LibraryIterator_Destroy=lambda it: None)
    ns = {}
    build(_LIB, ["CurrentFootprintNarrowed"], ns)
    return ns["CurrentFootprintNarrowed"](lib)


def test_current_footprint_is_the_library_iterators_reference():
    """U2 of the 2026-10-11 sweep, CONSERVATIVE: the member reads that
    followed PcbLib.CurrentComponent now run on the iterator's reference
    when one compares equal, and on CurrentComponent exactly as before when
    none does. No member of CurrentComponent is read either way."""
    cur = Dead()
    hit = _Wrapper(cur)
    assert _cur_fp(cur, [_Wrapper(object()), hit]) is hit
    assert _cur_fp(cur, [_Wrapper(object())]) is cur, "no match: today's"
    assert _cur_fp(None, [hit]) is None


def test_the_footprint_default_paths_go_through_the_iterator():
    lib = strip_comments((_ALTIUM / "Library.pas").read_text(encoding="utf-8"))
    pcb = strip_comments((_ALTIUM / "PCB.pas").read_text(encoding="utf-8"))
    assert len(re.findall(r":=\s*CurrentFootprintNarrowed\(PcbLib\)",
                          lib)) == 3
    assert len(re.findall(r":=\s*CurrentFootprintNarrowed\(PcbLib\)",
                          pcb)) == 1
    for code in (lib, pcb):
        assert not re.search(
            r"\b(Target|Footprint)\s*:=\s*PcbLib\.CurrentComponent\b", code)


# ---------------------------------------------------------------------------
# 7. The static guard (the class, not the sites)
# ---------------------------------------------------------------------------

_UNITS = sorted(p for p in _ALTIUM.glob("*.pas") if p.name != "Altium_MCP.pas")


def _getter_uses():
    """Every use of the CurrentSchComponent GETTER: anything but a setter
    (``X.CurrentSchComponent := Y``) or a Nil compare. A getter value read,
    copied into a variable or passed on is how a member read reaches the
    un-narrowed reference, and copying does not narrow it (Library.pas
    GetTargetLibComponent)."""
    bad = []
    for path in _UNITS:
        code = strip_comments(path.read_text(encoding="utf-8-sig",
                                             errors="replace"))
        for m in re.finditer(r"\bCurrentSchComponent\b", code):
            after = code[m.end():m.end() + 40]
            if re.match(r"\s*:=", after) or re.match(r"\s*(<>|=)\s*Nil\b",
                                                     after, re.I):
                continue
            line = code.count("\n", 0, m.start()) + 1
            bad.append(f"{path.name}:{line}: "
                       + code[max(0, m.start() - 40):m.end() + 20]
                       .replace("\n", " ").strip())
    return bad


def test_no_unit_reads_copies_or_passes_the_current_sch_component_getter():
    """3aca481: Library.pas GetTargetLibComponent / TargetLibComponentName
    / MarkLibDirty and Generic.pas Gen_CreateObject / Gen_BatchCreate."""
    assert _getter_uses() == []


def test_the_resolvers_never_name_current_sch_component():
    for fname in ("GetTargetLibComponent", "TargetLibComponentName",
                  "ResolveEditLibComponent", "MarkLibDirty"):
        body = strip_comments(_LIB[fname])
        assert "CurrentSchComponent" not in body.replace(
            "SchLib.CurrentSchComponent := Comp", ""), fname


_RULE_BASE = {"Name", "RuleKind", "Enabled", "Priority", "Scope1Expression",
              "Scope2Expression", "Descriptor", "Comment", "DRCEnabled",
              "I_ObjectAddress", "UniqueId"}

#: Calls whose answer is a base IPCB_Rule binding: a member read chained
#: straight onto one is the same read as through an IPCB_Rule variable.
_RULE_CALLS = ("FindDominantRuleForObject", "PCB_FindRuleByName")


def _rule_names(code):
    """Every name declared IPCB_Rule in a routine: Var-block locals AND
    parameters, however the declaration is laid out (``A, B : IPCB_Rule``
    in a Var block, ``(Board : IPCB_Board; Rule : IPCB_Rule)`` in a header,
    with or without a trailing ``;`` or ``)``)."""
    head = code.split("\nBegin", 1)[0]
    names = set()
    for m in re.finditer(r"([\w ,]+?)\s*:\s*IPCB_Rule\s*[;)]", head, re.I):
        decl = m.group(1)
        decl = re.split(r"[(;]", decl)[-1]
        decl = re.sub(r"^\s*(?:Var|Const)\b", "", decl, flags=re.I)
        names.update(v.strip() for v in decl.split(",") if v.strip())
    return names


def _rule_reads(code):
    """``Name.Member`` past the base interface through an IPCB_Rule name,
    and ``Call(...).Member`` chained on a rule-returning call."""
    bad = []
    for v in _rule_names(code):
        for m in re.finditer(rf"\b{re.escape(v)}\.(\w+)", code):
            if m.group(1) not in _RULE_BASE:
                bad.append(f"{v}.{m.group(1)}")
    for call in _RULE_CALLS:
        for m in re.finditer(rf"\b{call}\s*\(", code):
            depth, i = 1, m.end()
            while i < len(code) and depth:
                depth += {"(": 1, ")": -1}.get(code[i], 0)
                i += 1
            chained = re.match(r"\s*\.(\w+)", code[i:])
            if chained and chained.group(1) not in _RULE_BASE:
                bad.append(f"{call}(...).{chained.group(1)}")
    return bad


def test_no_ipcb_rule_typed_variable_is_read_past_the_base_interface():
    """A constraint member through an IPCB_Rule binding is the measured
    room-rule failure (PCB_GetRoomRules); 3aca481 read PreferedWidth /
    PreferedHoleWidth that way in PCB_NormalizeVias. Locals, parameters and
    reads chained onto a rule-returning call are all covered."""
    bad = []
    for path in _UNITS:
        for fname, body in load(path, minimum=1).items():
            for hit in _rule_reads(strip_comments(body)):
                bad.append(f"{path.name} {fname}: {hit}")
    assert bad == []


@pytest.mark.parametrize("src,hit", [
    ("Function F(Board : IPCB_Board; Rule : IPCB_Rule) : Integer;\n"
     "Begin\n    Result := Rule.Gap;\nEnd;", "Rule.Gap"),
    ("Function F(R : IPCB_Rule) : Integer;\nBegin\n"
     "    Result := R.PreferedWidth;\nEnd;", "R.PreferedWidth"),
    ("Function F(B : IPCB_Board) : Integer;\nVar\n    A, B2 : IPCB_Rule;\n"
     "Begin\n    Result := B2.MinWidth(1);\nEnd;", "B2.MinWidth"),
    ("Function F(B : IPCB_Board) : Integer;\nBegin\n    Result := "
     "B.FindDominantRuleForObject(V, 11).PreferedWidth;\nEnd;",
     "FindDominantRuleForObject(...).PreferedWidth"),
    ("Function F(B : IPCB_Board) : Integer;\nBegin\n    Result := "
     "PCB_FindRuleByName(B, 'X').Gap;\nEnd;", "PCB_FindRuleByName(...).Gap"),
])
def test_the_rule_guard_sees_parameters_and_chained_reads(src, hit):
    """The guard itself, on the shapes the review named: a parameter typed
    IPCB_Rule and a member chained onto a rule-returning call."""
    assert hit in _rule_reads(src)
    assert _rule_reads(src.replace(hit.split(".")[-1], "Name")) == []
