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

THE MECHANISM, read from the source and matching all three live records
(the 2026-10-11 Library.pas:122 halt, which was blamed on the editor's
CurrentSchComponent, has the same shape): the held-symbol globals in
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
* The GENERAL guard: every global declared in a unit StartMCPPump can see
  is assigned in StartMCPPump, or in a routine it calls, before the poll
  timer is enabled. A global of a unit compiled AFTER StatusForm.pas (the
  SelfTest counters) must be assigned at the top of an entry routine of
  its own unit instead. Fails on 7af6747 listing exactly the globals
  that were left unassigned, each with how it is used (compared with
  Nil, read as a flag, counted, or read).
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pascal_eval import build
from tests.pascal_source import functions, load, strip_comments
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
# Reading the units: globals, compile order, the pump's reach
# ---------------------------------------------------------------------------

def _unit_order() -> list[str]:
    prj = (_ALTIUM / "Altium_API.PrjScr").read_text(encoding="utf-8",
                                                     errors="replace")
    order = re.findall(r"(?m)^DocumentPath=(\w+\.pas)\s*$", prj)
    assert "StatusForm.pas" in order and "Main.pas" in order, order
    return order


def _code(unit: str) -> str:
    return strip_comments((_ALTIUM / unit).read_text(encoding="utf-8-sig",
                                                     errors="replace"))


_STOP = re.compile(r"(?mi)^\s*(Function|Procedure|Const|Begin|Var|Type)\b")


def unit_globals(unit: str) -> list[tuple[str, str]]:
    """``[(name, type)]`` for the unit-level Var blocks of one unit.

    A Var block at column 0 that runs into a ``Begin`` is the local block
    of a routine with nested routines (PCB.pas, Audit.pas), not a unit
    block: a unit block runs into the next routine, Const, Type or EOF.
    """
    code = _code(unit)
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"(?mi)^Var\b", code):
        rest = code[m.end():]
        stop = _STOP.search(rest)
        if stop and stop.group(1).lower() == "begin":
            continue
        block = rest[:stop.start()] if stop else rest
        for decl in re.finditer(r"([\w\s,]+?)\s*:\s*(\w+)\s*;", block):
            for name in decl.group(1).split(","):
                out.append((name.strip(), decl.group(2)))
    return out


def _strip_strings(code: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", code)


def _all_bodies() -> dict[str, str]:
    bodies: dict[str, str] = {}
    for unit in _unit_order():
        for name, body in functions(_code(unit)).items():
            bodies[name] = _strip_strings(body)
    return bodies


def _body_only(body: str) -> str:
    """A routine without its header line, so it does not 'call' itself."""
    return body.split("\n", 1)[1] if "\n" in body else ""


def pump_reach(bodies: dict[str, str]) -> dict[str, str]:
    """StartMCPPump up to the statement that arms the poll timer, plus every
    routine it calls, transitively. Nothing the request path runs can come
    before the timer ticks, so that is the window an assignment must sit in.
    """
    start = bodies["StartMCPPump"]
    cut = start.find("tmr_Poll.Enabled")
    assert cut > 0, "StartMCPPump no longer arms tmr_Poll.Enabled"
    reach = {"StartMCPPump": start[:cut]}
    todo = [reach["StartMCPPump"]]
    names = sorted(bodies, key=len, reverse=True)
    while todo:
        text = _body_only(todo.pop())
        for n in names:
            if n in reach:
                continue
            if re.search(r"\b%s\b" % re.escape(n), text):
                reach[n] = bodies[n]
                todo.append(bodies[n])
    return reach


def _assigns(text: str, name: str) -> bool:
    return bool(re.search(r"(?<![\w.])%s\s*:=" % re.escape(name), text)
                or re.search(r"\b(?:Inc|Dec)\(\s*%s\b" % re.escape(name), text))


def _how_used(name: str, every: str) -> str:
    n = re.escape(name)
    kinds = []
    if re.search(r"\b%s\s*(?:=|<>)\s*Nil\b|\bNil\s*(?:=|<>)\s*%s\b" % (n, n),
                 every, re.I):
        kinds.append("compared with Nil")
    if re.search(r"(?i)\b(?:If|Not|And|Or|While|Until)\s+\(?%s\b"
                 r"(?!\s*(?::=|=|<>|<|>|\.|\(|\[))" % n, every):
        kinds.append("read as a flag")
    if re.search(r"\b(?:Inc|Dec)\(\s*%s\b|(?<![\w.])%s\s*:=\s*%s\s*[+\-]"
                 % (n, n, n), every):
        kinds.append("counted/accumulated")
    return ", ".join(kinds) or "read"


def _entry_resets(unit: str, names: set[str]) -> bool:
    """Some routine of ``unit`` opens with ``Name := <literal>;`` for every
    one of ``names`` before its first other statement (RunSelfTest)."""
    for body in functions(_code(unit)).values():
        m = re.search(r"(?i)\bBegin\b(.*)", body, re.S)
        if not m:
            continue
        seen: set[str] = set()
        for stmt in m.group(1).split(";"):
            a = re.fullmatch(r"\s*(\w+)\s*:=\s*(?:'[^']*'|-?\d+|True|False|Nil)"
                             r"\s*", stmt, re.I)
            if not a:
                break
            seen.add(a.group(1))
        if names <= seen:
            return True
    return False


# ---------------------------------------------------------------------------
# 1. The general guard
# ---------------------------------------------------------------------------

def test_every_global_is_assigned_before_the_pump_serves_a_request():
    order = _unit_order()
    bodies = _all_bodies()
    reach = pump_reach(bodies)
    reached = "\n".join(reach.values())
    every = "\n".join(bodies.values())
    after = order[order.index("StatusForm.pas") + 1:]

    found = 0
    missing = []
    for unit in order:
        decls = unit_globals(unit)
        found += len(decls)
        if unit in after:
            names = {n for n, _ in decls}
            if names and not _entry_resets(unit, names):
                missing += [f"{unit}:{n} ({_how_used(n, every)}; compiled "
                            f"after StatusForm.pas and no entry routine of "
                            f"{unit} assigns it first)" for n in sorted(names)]
            continue
        for name, typ in decls:
            if not _assigns(reached, name):
                missing.append(f"{unit}:{name} : {typ} "
                               f"({_how_used(name, every)})")
    # The parser's floor: a stripper or splitter that stops working must
    # not turn this into a pass over nothing.
    assert found >= 80, f"only {found} globals parsed"
    assert not missing, (
        "these globals are never assigned by StartMCPPump (or a routine it "
        "calls before arming the poll timer). On a fresh pump they hold "
        "Empty, which is NOT Nil, so every '= Nil' guard passes it and the "
        "first member read halts the engine (D-F2-1):\n  "
        + "\n  ".join(missing))


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
    build(_STATUS, ["StartMCPPump"], ns)
    ns["StartMCPPump"](0)
    assert ns["Running"] is True and ns["tmr_Poll"].Enabled is True
    return ns


def _client():
    return Client({A: LiveDoc()})


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
