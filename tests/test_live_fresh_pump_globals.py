# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every global the request path reads is assigned when the pump starts.

FIELD (2026-10-12 19:35, bridge 2026.10.12.02, AD 26.10.1.5; the
RELEASE_VERIFICATION 39.4 P8 step, finding D-F2-1). A freshly restarted
Altium, a SchLib opened, nothing created or selected through the bridge
(activity.log since ``_session_start`` lists no caller of
RememberLibComponent): ``lib_add_symbol_lines`` with no component_name
still got a NON-Nil component out of ResolveEditLibComponent, and
OwnerPartIdOf's ``Component.CurrentPartID`` (Library.pas:146, inside a
Try) was the script engine's "Undeclared identifier: CurrentPartID" box.
The loop was dead.

THE MECHANISM, read from the source and CONSISTENT WITH all three live
records (the 2026-10-11 Library.pas:122 halt, which was blamed on the
editor's CurrentSchComponent, has the same shape; session A, P8 on
2026.10.12.03, is the live proof): the held-symbol globals in
Main.pas were NEVER assigned at pump start. StartMCPPump reset some forty
other globals and not these. In DelphiScript every variable is a Variant,
so an unassigned global is Empty, and Empty does NOT compare equal to Nil:
every ``= Nil`` exit is skipped, every ``<> Nil`` branch is taken, and the
member read runs on Empty, which the engine reports as an undeclared
identifier. No Try catches it.

So the fixtures here model a FRESH pump: every unit-level global starts as
``Unassigned`` - unequal to Nil (measured: the guards above all passed it),
False as a Boolean (``If Running Then Exit`` has always started the pump),
'' / 0 in a string or number compare, and ANY member read on it is an
AssertionError, which no translated Except catches, exactly as no Pascal
Except catches the engine's halt.

* StartMCPPump, EXECUTED through tests/pascal_eval.py on that fixture,
  must leave every Lib_Add* handler answering NO_COMPONENT for a nameless
  edit (fails on 7af6747 with the field's own CurrentPartID read).
* A held component from a PREVIOUS pump session (StartMCPServer re-run in
  the same Altium) is dropped too.
* The GENERAL guard (tests/pascal_init_order.py): StartMCPPump is WALKED
  IN EXECUTION ORDER, every routine it calls inlined at the call, up to
  the statement that arms the poll timer. Every global declared in a unit
  StartMCPPump can see must be assigned by an UNCONDITIONAL statement
  (not under an If, a loop, a Try body or an Except, and not after a
  conditional Exit) BEFORE anything reads it - an ``= Nil`` test is a
  read. The first version of this guard counted any ``Name :=`` anywhere
  in reach, which accepted ``If X = Nil Then X := ...`` (skipped on Empty:
  the D-F2-1 class itself) and an assignment that came after the first
  read (the perf lists, Nil-tested by EnsureStatusBuffers before
  ShowStatusForm's Try created them); the negative tests here run the
  guard on exactly those injected shapes. Two globals are read first BY
  DESIGN and are listed, with the reason, in ``_READ_FIRST``. A global of
  a unit compiled AFTER StatusForm.pas (the SelfTest counters) must be
  assigned at the top of an entry routine of its own unit instead.
* RunSelfTest, the other route that runs handlers without the pump, calls
  the same ResetSessionGlobals before its first test.

Unit order is lint.PAS_FILES (the order build.py concatenates and the
linter checks) with SelfTest.pas, the one PrjScr-only unit, appended.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pascal_eval import build
from tests.pascal_init_order import (
    InitWalk, routine_texts, unit_code, unit_globals_of)
from tests.pascal_source import functions, load
from tests.test_library_popup_free import A, Client, Dead, _held
from tests.test_live_editor_component_is_never_read import (
    _HANDLERS, _LIB, EditLib, LiveDoc, NComp, _edit_ns)

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


# ---------------------------------------------------------------------------
# The engine's unassigned global
# ---------------------------------------------------------------------------

class Unassigned:
    """DelphiScript's Empty: what an unassigned global holds.

    ``= Nil`` is False (the field: GetTargetLibComponent's ``= Nil`` exit
    and OwnerPartIdOf's ``Component = Nil`` guard both let it through);
    as a Boolean it is False; in a string or number compare it is '' or 0;
    concatenated it is ''. A member read is the engine's uncatchable halt.
    """

    __slots__ = ()

    def __eq__(self, other):
        if other is None:
            return False
        if isinstance(other, Unassigned):
            return True
        return other in ("", 0, False)

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = object.__hash__

    def __bool__(self):
        return False

    def __len__(self):
        return 0

    def __getitem__(self, key):
        return ""

    def __add__(self, other):
        return other

    def __radd__(self, other):
        return other

    def __getattr__(self, name):
        raise AssertionError(
            f"Undeclared identifier: {name} (a member read on an UNASSIGNED "
            f"global - the engine halts on the line, no Except runs)")


# ---------------------------------------------------------------------------
# Reading the units: globals, compile order
# ---------------------------------------------------------------------------

def _lint():
    path = _ALTIUM / "lint.py"
    spec = importlib.util.spec_from_file_location("_altium_lint_fpg", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_fpg"] = mod
    spec.loader.exec_module(mod)
    return mod


def _unit_order() -> list[str]:
    """lint.PAS_FILES (what build.py concatenates and lint checks, in that
    order) plus SelfTest.pas, the one unit only the PrjScr compiles. The
    PrjScr must list exactly the same units: the order the guard reasons
    about is the order the build uses."""
    order = list(_lint().PAS_FILES) + ["SelfTest.pas"]
    prj = (_ALTIUM / "Altium_API.PrjScr").read_text(encoding="utf-8",
                                                     errors="replace")
    listed = re.findall(r"(?m)^DocumentPath=(\w+\.pas)\s*$", prj)
    assert sorted(listed) == sorted(order), (listed, order)
    assert order.index("StatusForm.pas") < order.index("Dispatcher.pas")
    return order


def _sources(order=None) -> dict[str, str]:
    return {u: (_ALTIUM / u).read_text(encoding="utf-8-sig", errors="replace")
            for u in (order or _unit_order())}


def unit_globals(unit: str, sources=None) -> list[tuple[str, str]]:
    src = (sources or {}).get(unit)
    if src is None:
        src = (_ALTIUM / unit).read_text(encoding="utf-8-sig",
                                         errors="replace")
    return unit_globals_of(unit_code(src))


# Read before any assignment ON PURPOSE, each with the reason. Anything
# else read first is a finding.
_READ_FIRST = {
    "Running": "the re-entry flag: 'If Running Then Exit' is StartMCPPump's "
               "first statement; on a fresh engine Empty reads False and the "
               "pump starts, and Running := True follows unconditionally",
    "WorkspaceDir": "EnsureWorkspaceDir's \"If WorkspaceDir = '' Then\" "
                    "assigns it on Empty (Empty = '' holds; every live "
                    "session has started this way) and a configured value "
                    "is kept",
}


def _entry_resets(unit: str, names: set[str], sources=None) -> bool:
    """Some routine of ``unit`` opens with ``Name := <literal>;`` for every
    one of ``names`` before its first other statement (RunSelfTest)."""
    code = unit_code((sources or _sources())[unit])
    for body in functions(code).values():
        m = re.search(r"(?i)\bBegin\b(.*)", body, re.S)
        if not m:
            continue
        seen: set[str] = set()
        for stmt in m.group(1).split(";"):
            a = re.fullmatch(r"\s*(\w+)\s*:=\s*(?:''|-?\d+|True|False|Nil)"
                             r"\s*", stmt, re.I)
            if not a:
                break
            seen.add(a.group(1))
        if names <= seen:
            return True
    return False


def pump_findings(sources: dict[str, str], order: list[str]):
    """The guard, on any source text (the negative tests inject into it).

    Returns ``(findings, walk)``: every global of a unit up to and
    including StatusForm.pas that StartMCPPump does not assign
    unconditionally before its first read, walking up to the statement
    that arms the poll timer; plus, for the units compiled after
    StatusForm.pas, any global no entry routine of its own unit resets
    first."""
    upto = order[:order.index("StatusForm.pas") + 1]
    watched = {}
    for unit in upto:
        for name, typ in unit_globals(unit, sources):
            watched[name] = (unit, typ)
    walk = InitWalk(routine_texts(sources, upto), watched,
                    exempt=dict(_READ_FIRST))
    found = [str(f) for f in walk.run("StartMCPPump", cut="tmr_Poll.Enabled",
                                      gate="Running")]
    for unit in order[order.index("StatusForm.pas") + 1:]:
        names = {n for n, _ in unit_globals(unit, sources)}
        if names and not _entry_resets(unit, names, sources):
            found += [f"{unit}:{n} (compiled after StatusForm.pas and no "
                      f"entry routine of {unit} assigns it first)"
                      for n in sorted(names)]
    return found, walk


# ---------------------------------------------------------------------------
# 1. The general guard
# ---------------------------------------------------------------------------

def test_every_global_is_assigned_before_the_pump_serves_a_request():
    order = _unit_order()
    sources = _sources(order)
    found, walk = pump_findings(sources, order)
    # The parser's floor: a stripper or splitter that stops working must
    # not turn this into a pass over nothing.
    total = sum(len(unit_globals(u, sources)) for u in order)
    assert total >= 80, f"only {total} globals parsed"
    assert len(walk.watched) >= 80, len(walk.watched)
    assert not found, (
        "these globals are read before StartMCPPump assigns them, or are "
        "assigned only CONDITIONALLY (under an If, a loop, a Try body, or "
        "after a conditional Exit), walking StartMCPPump in order up to "
        "the statement that arms the poll timer. On a fresh pump they hold "
        "Empty, which is NOT Nil, so every '= Nil' guard passes it and the "
        "first member read halts the engine (D-F2-1). Assign each as a "
        "top-level statement of ResetSessionGlobals:\n  "
        + "\n  ".join(found))
    # And not vacuously: the walk reached the reset routines and set most
    # globals unconditionally.
    assert len(walk.state) >= 70, len(walk.state)
    assert {"ResetSessionGlobals", "ShowStatusForm", "ResetPerfStats",
            "ForgetRememberedLibComponent"} <= set(walk.order), walk.order


def test_the_read_first_exceptions_are_still_read_first():
    """An exception that no longer applies must leave the list, so the list
    cannot quietly grow into a place to park new findings."""
    order = _unit_order()
    sources = _sources(order)
    upto = order[:order.index("StatusForm.pas") + 1]
    watched = {n: (u, t) for u in upto for n, t in unit_globals(u, sources)}
    walk = InitWalk(routine_texts(sources, upto), watched)
    found = walk.run("StartMCPPump", cut="tmr_Poll.Enabled", gate="Running")
    first = {f.name for f in found if "read while still unassigned" in f.why}
    assert first == set(_READ_FIRST), (first, set(_READ_FIRST))


def _inject(sources, unit, old, new, count=1):
    text = sources[unit]
    assert text.count(old) == count, (unit, old, text.count(old))
    out = dict(sources)
    out[unit] = text.replace(old, new)
    return out


_SF = "StatusForm.pas"


def test_guard_catches_a_lazy_nil_test_initialisation():
    """inj_b: a new global created ONLY by ``If X = Nil Then X := ...``.
    On Empty the test is False and the global stays Empty; the first guard
    passed it because an assignment existed in reach."""
    order = _unit_order()
    src = _inject(_sources(order), _SF,
                  "    PerfMaxStrs      : TStringList;",
                  "    PerfMaxStrs      : TStringList;\n"
                  "    ProbeList        : TStringList;")
    src = _inject(src, _SF,
                  "    If PerfMaxStrs   = Nil Then PerfMaxStrs   := "
                  "TStringList.Create;",
                  "    If PerfMaxStrs   = Nil Then PerfMaxStrs   := "
                  "TStringList.Create;\n"
                  "    If ProbeList = Nil Then ProbeList := "
                  "TStringList.Create;")
    found, _ = pump_findings(src, order)
    assert any(f.startswith(_SF + ":ProbeList ") and "compared with Nil" in f
               for f in found), found


def test_guard_catches_the_perf_lists_without_the_unconditional_create():
    """inj_c: the unconditional ResetPerfStats(0) of ResetSessionGlobals
    removed. The lists are then Nil-tested by EnsureStatusBuffers (skipped
    on Empty) and created only inside ShowStatusForm's Try body, after
    statements that may raise. The first guard passed this; on hardware the
    first PerfNames.IndexOf would halt every request."""
    order = _unit_order()
    sources = _sources(order)
    body = functions(sources[_SF])["ResetSessionGlobals"]
    assert body.count("ResetPerfStats(0);") == 1
    src = dict(sources)
    src[_SF] = sources[_SF].replace(
        body, body.replace("    ResetPerfStats(0);\n", ""))
    assert src[_SF] != sources[_SF]
    found, _ = pump_findings(src, order)
    names = {f.split(" ")[0] for f in found}
    assert {_SF + ":PerfNames", _SF + ":PerfCountStrs",
            _SF + ":PerfTotalStrs", _SF + ":PerfMaxStrs"} <= names, found


def test_guard_catches_an_assignment_inside_a_try_or_after_a_guarded_exit():
    order = _unit_order()
    sources = _sources(order)
    src = _inject(sources, _SF, "    RefreshingLib := False;\n",
                  "    Try RefreshingLib := False; Except End;\n")
    found, _ = pump_findings(src, order)
    assert any(f.startswith("Main.pas:RefreshingLib ") and "CONDITIONALLY"
               in f for f in found), found
    src = _inject(sources, _SF, "    LastCompileTick := 0;\n",
                  "    If LastActivityTick > 5 Then Exit;\n"
                  "    LastCompileTick := 0;\n")
    found, _ = pump_findings(src, order)
    assert any(f.startswith("Main.pas:LastCompileTick ") for f in found), found


def test_guard_catches_a_read_before_the_assignment():
    order = _unit_order()
    src = _inject(_sources(order), _SF,
                  "    ForgetRememberedLibComponent(0);\n",
                  "    If LastCompiledProject <> Nil Then StatusLastCommand "
                  ":= '';\n    ForgetRememberedLibComponent(0);\n")
    found, _ = pump_findings(src, order)
    assert any(f.startswith("Main.pas:LastCompiledProject ")
               and "compared with Nil" in f for f in found), found


def test_run_self_test_resets_the_session_globals_first():
    """RunSelfTest runs handlers (ProcessCommand, Lib_GetComponents,
    Gen_ModifyObjects, ...) without StartMCPPump, so on a fresh engine
    every global would still be Empty. Walked in order up to its first
    test, it must have assigned every global ResetSessionGlobals assigns."""
    order = _unit_order()
    sources = _sources(order)
    upto = order[:order.index("StatusForm.pas") + 1]
    watched = {n: (u, t) for u in upto for n, t in unit_globals(u, sources)}
    session = InitWalk(routine_texts(sources, upto), watched)
    session.run("ResetSessionGlobals")
    assigned = {n for n, v in session.state.items() if v == "set"}
    assert len(assigned) >= 60, len(assigned)
    st = InitWalk(routine_texts(sources, order), watched,
                  exempt=dict(_READ_FIRST))
    st.run("RunSelfTest", cut="TestJsonParsing(0)")
    missing = sorted(assigned - {n for n, v in st.state.items()
                                 if v == "set"})
    assert not missing, ("RunSelfTest reaches its first test without "
                         "assigning: " + ", ".join(missing))


def test_the_globals_parser_sees_the_known_globals():
    """Pins the parser itself: known globals of each kind are found, and a
    nested routine's local block is not mistaken for a unit block."""
    names = {n for u in _unit_order() for n, _ in unit_globals(u)}
    for known in ("LastCreatedLibComponent", "LastCreatedLibPath",
                  "RefreshingLib", "LastCompiledProject", "PerfNames",
                  "PumpQuitting", "_NextStepStr", "SelfTest_Passed"):
        assert known in names, known
    assert "BothRouted" not in names and "HasTracks" not in names


# ---------------------------------------------------------------------------
# 2. StartMCPPump, executed on a fresh-pump fixture
# ---------------------------------------------------------------------------

_STATUS = load(_ALTIUM / "StatusForm.pas", minimum=20)
_MAIN = load(_ALTIUM / "Main.pas", minimum=40)
_UTILS = load(_ALTIUM / "Utils.pas", minimum=40)


def _fresh_pump(ns: dict, keep=()) -> dict:
    """Every unit-level global Unassigned (except ``keep``: what a previous
    pump session left), then StartMCPPump run for real (its own statements;
    the callees that only touch the disk or the form are no-ops, the ones
    that reset state are the real Pascal)."""
    for unit in _unit_order():
        for name, _typ in unit_globals(unit):
            if name not in keep:
                ns[name] = Unassigned()
    ns.update({
        "SCRIPT_VERSION": "test", "PROTOCOL_VERSION": 2,
        "GetTickCount": lambda: 1000,
        "FileExists": lambda p: False,
        "DeleteFile": lambda p: None,
        "EnsureWorkspaceDir": lambda d: None,
        "LoadMCPConfig": lambda d: None,
        "CleanupOrphanRequests": lambda d: None,
        "CleanupOrphanResponses": lambda d: None,
        "CleanupOrphanProgress": lambda d: None,
        "ShowStatusForm": lambda d: None,
        "UpdateStatusHeader": lambda s: None,
        "UpdateStatsLine": lambda *a: None,
        "AppendLog": lambda s: None,
        "FormatLogStamp": lambda d: "",
        "tmr_Poll": SimpleNamespace(Interval=0, Enabled=False),
    })
    build(_MAIN, ["InitDefaultConfig", "ResetCaughtExceptions"], ns)
    build(_UTILS, [n for n in ("ResetNextStep", "ResetPropertyDiag")
                   if n in _UTILS], ns)
    # ResetSessionGlobals exists from 2026.10.12.03 on; building only what a
    # tree has lets this fixture run an older tree for the fails-before proof.
    build(_STATUS, [n for n in ("ResetPerfStats", "ResetSessionGlobals",
                                "StartMCPPump") if n in _STATUS], ns)
    ns["StartMCPPump"](0)
    assert ns["Running"] is True and ns["tmr_Poll"].Enabled is True
    return ns


def _client():
    return Client({A: LiveDoc()})


def test_the_perf_lists_exist_even_when_the_status_form_did_not_create_them():
    """Review of 2026.10.12.03: the four perf lists were created only by
    ResetPerfStats inside ShowStatusForm's single Try, after statements that
    may raise. EnsureStatusBuffers' ``= Nil`` tests do nothing on Empty, so
    if that Try had raised first, FindOrAddPerf's PerfNames.IndexOf (every
    request, through AppendLogLine) would have been the engine's halt.
    The fixture's ShowStatusForm does nothing - the Try that raised before
    ResetPerfStats - and the first perf row must still be recorded."""
    lib = EditLib(A)
    ns = _edit_ns(lib, _client())
    _fresh_pump(ns)
    build(_STATUS, ["EnsureStatusBuffers", "FindOrAddPerf"], ns)
    assert ns["FindOrAddPerf"]("application.ping") == 0
    assert ns["FindOrAddPerf"]("application.ping") == 0
    assert list(ns["PerfNames"]) == ["application.ping"]
    assert list(ns["PerfCountStrs"]) == ["0"]


def test_the_fixture_reproduces_the_field_halt():
    """The control: without StartMCPPump the unassigned held global reaches
    OwnerPartIdOf's CurrentPartID read - the field's line, Library.pas:146.
    Passes before and after the fix; it proves the model, not the code."""
    lib = EditLib(A)
    ns = _edit_ns(lib, _client(), [{"x1": "0", "x2": "100"}])
    for unit in _unit_order():
        for name, _typ in unit_globals(unit):
            ns[name] = Unassigned()
    build(_LIB, ["Lib_AddSymbolLines"], ns)
    with pytest.raises(AssertionError, match="CurrentPartID"):
        ns["Lib_AddSymbolLines"]({"lines": "q"}, "r")


def test_p8_first_call_of_a_fresh_pump_answers_no_component():
    """D-F2-1 exactly: fresh pump, SchLib focused, nothing held,
    lib_add_symbol_lines with no component_name. 7af6747 halts on
    CurrentPartID; now NO_COMPONENT, before any PreProcess."""
    lib = EditLib(A)
    ns = _edit_ns(lib, _client(), [{"x1": "0", "x2": "100"}])
    _fresh_pump(ns)
    build(_LIB, ["Lib_AddSymbolLines"], ns)
    reply = ns["Lib_AddSymbolLines"]({"lines": "q"}, "r")
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert "component_name" in reply[2]
    assert "pre" not in lib.events and ns["_server"].made == []
    assert ns["LastCreatedLibComponent"] is None
    assert ns["LastCreatedLibDoc"] is None
    assert ns["LastCreatedLibComponentName"] == ""
    assert ns["LastCreatedLibPath"] == ""
    assert ns["RefreshingLib"] is False


@pytest.mark.parametrize("handler", sorted(_HANDLERS))
def test_every_nameless_edit_of_a_fresh_pump_answers_no_component(handler):
    params, queue = _HANDLERS[handler]
    lib = EditLib(A)
    ns = _edit_ns(lib, _client(), queue)
    _fresh_pump(ns)
    build(_LIB, [handler], ns)
    reply = ns[handler](dict(params), "r")
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert "pre" not in lib.events and ns["_server"].made == []


def test_a_component_held_by_a_previous_pump_session_is_dropped():
    """StartMCPServer re-run in the same Altium: the globals keep the last
    session's values. A held component from then (here one whose every
    member read is the halt) must not take the next session's nameless
    edit; the caller names the symbol instead."""
    events: list[str] = []
    lib = EditLib(A, events=events)
    ns = _edit_ns(lib, _client(), [{"x1": "0", "x2": "100"}])
    _held(ns, lib, NComp("OLD", events), "OLD")
    ns["LastCreatedLibComponent"] = Dead()
    _fresh_pump(ns, keep=("LastCreatedLibComponent",
                          "LastCreatedLibComponentName",
                          "LastCreatedLibDoc", "LastCreatedLibPath"))
    build(_LIB, ["Lib_AddSymbolLines"], ns)
    reply = ns["Lib_AddSymbolLines"]({"lines": "q"}, "r")
    assert reply[:2] == ("ERR", "NO_COMPONENT"), reply
    assert ns["LastCreatedLibComponent"] is None


def test_the_tools_say_the_held_symbol_does_not_survive_a_recovery():
    """User-visible since 2026.10.12.03: StartMCPPump drops the held symbol,
    so after the DEAD_LOOP recovery the tool itself prescribes (OK, Stop,
    StartMCPServer) a nameless edit answers NO_COMPONENT. Every docstring
    that promises the held symbol, and lib_set_current_component, says so,
    and so does the script_fatal recovery."""
    import ast
    from eda_agent.bridge.recovery import (
        DEAD_LOOP, SCRIPT_FATAL_SEEN, recovery_guidance)
    src = (_ROOT / "src" / "eda_agent" / "tools" / "library.py").read_text(
        encoding="utf-8")
    docs = {n.name: ast.get_docstring(n) or "" for n in ast.walk(ast.parse(src))
            if isinstance(n, ast.AsyncFunctionDef)}
    promising = [n for n, d in docs.items()
                 if "with none held the reply" in d and "NO_COMPONENT" in d]
    assert len(promising) >= 6, promising
    silent = [n for n in promising + ["lib_set_current_component"]
              if "StartMCPServer re-run" not in " ".join(docs[n].split())]
    assert not silent, silent
    steps = " ".join(recovery_guidance(DEAD_LOOP,
                                       found_by=SCRIPT_FATAL_SEEN)["steps"])
    assert "lib_set_current_component" in steps and "component_name" in steps
