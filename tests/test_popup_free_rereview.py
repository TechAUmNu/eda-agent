# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The popup-free work's re-review findings, each EXECUTED where it can be.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs, so the cure is to check
first, and a check must never change a result that succeeds today. The
re-review found sites the first pass left raising and two checks that
were not conservative:

* bare ``PCBServer.GetCurrentPCBLibrary`` lookups a focused SchLib walks
  straight into (naming PCBServer before the PCB server loaded is the
  uncatchable modal) - ResolveLibFootprint, FocusPcbLibForEdit,
  Lib_CopyFootprint and every handler that defaults its library path to
  the focused document;
* a gate (PcbLibMayBeCurrent) that a STALE DM focus after a programmatic
  close/reopen could fool into refusing lib_reload_library;
* a 3D-model format ALLOW-list that would refuse a format Altium loads;
* ResolvePCBBoard skipping the open of an existing .PcbLib board_path;
* fault-only backstops dropped (LoadMCPConfig) or never added (the new
  helpers' GetDocumentByPath);
* Gen_GetConstraintGroups' newly-reachable member reads, uncounted;
* read_last_caught reporting a catch from an earlier pump session;
* Proj_ImportDocument's folder refusal depending on how CopyFile fails.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build, translate
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


def _bodies(name):
    return load(_ALTIUM / name, minimum=10)


# ---------------------------------------------------------------------------
# Fixtures: a DM workspace, a Client, and the two PCBServer behaviours
# ---------------------------------------------------------------------------

class _DMDoc:
    def __init__(self, path, kind):
        self.DM_FullPath = path
        self.DM_DocumentKind = kind


class _Proj:
    def __init__(self, docs):
        self.docs = list(docs)
        self.DM_LogicalDocumentCount = len(self.docs)

    def DM_LogicalDocuments(self, i):
        return self.docs[i]


class _Workspace:
    """DM focus ``focus`` (a _DMDoc or None); ``free`` are free documents,
    which are open by definition."""

    def __init__(self, focus, free=()):
        self.DM_FocusedDocument = focus
        self.DM_ProjectCount = 0
        self.DM_FreeDocumentsProject = _Proj(free)

    def DM_Projects(self, i):                    # pragma: no cover
        raise AssertionError("no projects in this fixture")


class _Client:
    def __init__(self, open_paths=(), raises=False):
        self.open = {p.upper() for p in open_paths}
        self.raises = raises

    def GetDocumentByPath(self, p):
        if self.raises:
            raise PascalError("GetDocumentByPath on an odd path")
        return object() if p.upper() in self.open else None


class _NoPcbServer:
    """Naming PCBServer before the PCB editor server loaded: the modal. An
    AssertionError, so no translated Except swallows it."""

    def __getattr__(self, name):
        raise AssertionError(f"PCBServer.{name} named with no PcbLib current")


class _PcbServer:
    def __init__(self):
        self.asked = 0

    @property
    def GetCurrentPCBLibrary(self):
        self.asked += 1
        return object()

    def __getattr__(self, name):                 # pragma: no cover
        raise AssertionError(f"unexpected PCBServer.{name}")


class _App:
    def ProcessMessages(self):
        return None


_SCHLIB = r"C:\L\s.SchLib"
_PCBLIB = r"C:\L\a.PcbLib"


def _lib_ns(focus, free, server, client=None):
    caught: list = []
    ns = {
        "GetWorkspace": _Workspace(focus, free),
        "Client": client or _Client(),
        "PCBServer": server,
        "ExtractJsonValue": lambda p, k: p.get(k, ""),
        "BuildErrorResponse": lambda r, c, m: ("ERR", c, m),
        "BuildSuccessResponse": lambda r, d: ("OK", d),
        "EscapeJsonString": lambda s: s,
        "StrToFloatDef": lambda s, d: float(s) if s else d,
        "StrToIntDef": lambda s, d: int(s) if s else d,
        "FileExists": lambda p: True,
        "ResetParameters": lambda: None,
        "AddStringParameter": lambda k, v: None,
        "RunProcess": lambda p: None,
        "Application": _App(),
        "RecordCaughtException": caught.append,
        "PcbLibLoadable": lambda p: False,
        "_caught": caught,
    }
    build(_bodies("Main.pas"), ["PcbEditorDocOpen", "PcbLibMayBeCurrent",
                                "PcbLibMayBeCurrentAt"], ns)
    return ns


_NIL_BLOCK = re.compile(r"If\s+(\w+)\s*=\s*Nil\s+Then\s+Begin.*?\bEnd;", re.S)


def _through_lookup(routine):
    """The routine cut just after the Nil test that follows its library
    lookup, ending 'REACHED' when the lookup answered. A Var parameter (the
    evaluator has none) becomes a value one, and the routine reports its
    ErrCode / message through the Result instead."""
    body = strip_comments(_bodies("Library.pas")[routine])
    k = body.index("GetCurrentPCBLibrary")
    m = _NIL_BLOCK.search(body, k)
    head, block = body[:m.start()], m.group(0)
    if routine == "ResolveLibFootprint":
        head = head.replace("Var ErrCode : String; Var Why : String",
                            "ErrCode : String; Why : String")
        head = head.replace("Exit;", "Result := ErrCode + '|' + Why; Exit;")
        block = block.replace("Exit;", "Result := ErrCode + '|' + Why; Exit;")
    elif routine == "FocusPcbLibForEdit":
        head = head.replace("Var LibPath : String; Var ErrCode : String; "
                            "Var ErrMsg : String",
                            "LibPath : String; ErrCode : String; "
                            "ErrMsg : String")
        head = head.replace("Exit;", "Result := ErrCode + '|' + ErrMsg; Exit;")
        return head + block + ("\n    If Result = Nil Then Result := ErrCode + "
                               "'|' + ErrMsg Else Result := 'REACHED';\nEnd;\n")
    return head + block + "\n    Result := 'REACHED';\nEnd;\n"


def _run(routine, ns, params):
    src = _through_lookup(routine)
    build({routine: src}, [routine], ns)
    if routine in ("ResolveLibFootprint",):
        return ns[routine](params, "", "")
    if routine == "FocusPcbLibForEdit":
        return ns[routine](params.get("library_path", ""), "", "")
    return ns[routine](params, "r")


# routine -> extra params it needs to reach its lookup
_PATH_DEFAULTING = {
    "ResolveLibFootprint": {},
    "FocusPcbLibForEdit": {},
    "Lib_GetFootprints": {},
    "Lib_GetFootprintPads": {},
    "Lib_GetLibraryGeometry": {},
    "Lib_SetDesignator": {"footprint_name": "F"},
    "Lib_ConvertDesignatorsToStroke": {},
    "Lib_SetDesignators": {"edits_path": r"C:\e\edits.json"},
    "Lib_ProbeDesignator": {},
    "Lib_Link3DModel": {"model_path": r"C:\m\a.step"},
    "Lib_GetFootprintBodies": {},
    "Lib_DeleteFootprint": {"footprint_name": "F", "name": "F"},
    "Lib_RenameFootprint": {"old_name": "F", "new_name": "G",
                            "footprint_name": "F", "name": "F"},
    "Lib_ProbeFootprint": {"footprint_name": "F"},
    "Lib_CopyFootprint": {"source_name": "F", "new_name": "G"},
    "Lib_GetPadGeometry": {"footprint_name": "F"},
    "Lib_DeleteFootprintPrimitives": {"footprint_name": "F", "confirm": "true"},
    "Lib_SetMechLayers": {},
}


def _nil_reply(routine):
    """The (code, message start) the routine's Nil branch answers."""
    body = strip_comments(_bodies("Library.pas")[routine])
    m = _NIL_BLOCK.search(body, body.index("GetCurrentPCBLibrary"))
    code = re.search(r"'([A-Z_]+)'", m.group(0)).group(1)
    return code


@pytest.mark.parametrize("routine", sorted(_PATH_DEFAULTING))
def test_a_focused_schlib_never_reaches_a_bare_pcblib_lookup(routine):
    """Fresh session (no PcbDoc / PcbLib opened), a SchLib focused, no
    library path: the path defaults to the SchLib, its focus check passes,
    and the lookup used to be named - the modal. Now the Nil branch's own
    reply comes back without it."""
    ns = _lib_ns(_DMDoc(_SCHLIB, "SCHLIB"), [_DMDoc(_SCHLIB, "SCHLIB")],
                 _NoPcbServer())
    got = _run(routine, ns, dict(_PATH_DEFAULTING[routine]))
    assert got != "REACHED"
    code = _nil_reply(routine)
    if isinstance(got, tuple):
        assert got[0] == "ERR" and got[1] == code, got
    else:
        assert got.split("|")[0] == code, got


@pytest.mark.parametrize("routine", sorted(_PATH_DEFAULTING))
def test_the_lookup_is_still_asked_with_a_pcblib_focused(routine):
    ns = _lib_ns(_DMDoc(_PCBLIB, "PCBLIB"), [_DMDoc(_PCBLIB, "PCBLIB")],
                 server := _PcbServer())
    assert _run(routine, ns, dict(_PATH_DEFAULTING[routine])) == "REACHED"
    assert server.asked == 1


@pytest.mark.parametrize("routine", sorted(set(_PATH_DEFAULTING)
                                           - {"Lib_CopyFootprint"}))
def test_a_stale_focus_does_not_refuse_a_pcblib_open_at_the_path(routine):
    """A programmatic OpenObject can leave the DM focus on the PREVIOUS
    document (stale, not Nil). With the asked-for PcbLib open at its path
    the server is loaded, so the gate must not refuse what works today
    (a focus check of the handler's own may still refuse it)."""
    params = dict(_PATH_DEFAULTING[routine], library_path=_PCBLIB)
    ns = _lib_ns(_DMDoc(_SCHLIB, "SCHLIB"), [_DMDoc(_SCHLIB, "SCHLIB")],
                 server := _PcbServer(), client=_Client([_PCBLIB]))
    got = _run(routine, ns, params)
    if got == "REACHED":
        assert server.asked == 1
    else:
        reply = got[1] if isinstance(got, tuple) else got.split("|")[0]
        assert reply == "WRONG_DOCUMENT_FOCUSED", got


def test_reload_library_with_a_stale_focus_reloads():
    """Lib_ReloadLibrary closes and reopens the library, then gates the
    lookup: a SchLib focused before the call stays the DM focus."""
    ns = _lib_ns(_DMDoc(_SCHLIB, "SCHLIB"), [_DMDoc(_SCHLIB, "SCHLIB")],
                 server := _PcbServer(), client=_Client([_PCBLIB]))
    build(_bodies("Library.pas"), ["Lib_ReloadLibrary"], ns)
    kind, data = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert kind == "OK" and '"reloaded":true' in data
    assert server.asked == 1


def test_copy_footprint_by_source_path_is_not_gated():
    """The gate is on the focused-library branch only: a named source
    library still takes the by-path route."""
    body = strip_comments(_bodies("Library.pas")["Lib_CopyFootprint"])
    m = re.search(r"Else\s+If\s+PcbLibMayBeCurrent(?:At)?\s*\([^()]*\)\s+Then"
                  r"\s+SourceLib\s*:=\s*PCBServer\.GetCurrentPCBLibrary;", body)
    assert m, "the focused-source lookup is not gated"


@pytest.mark.parametrize("focus,free,want", [
    (_DMDoc(_SCHLIB, "SCHLIB"), [_DMDoc(_PCBLIB, "PCBLIB")], True),
    (_DMDoc(_SCHLIB, "SCHLIB"), [], True),
    (None, [], True),
])
def test_the_path_gate_accepts_an_open_pcblib_whatever_the_focus(focus, free,
                                                                 want):
    ns = _lib_ns(focus, free, _NoPcbServer(), client=_Client([_PCBLIB]))
    assert ns["PcbLibMayBeCurrentAt"](_PCBLIB) is want


@pytest.mark.parametrize("path", ["", _SCHLIB, r"C:\L\closed.PcbLib"])
def test_the_path_gate_is_the_plain_gate_otherwise(path):
    for focus, free in [(_DMDoc(_SCHLIB, "SCHLIB"), []),
                        (_DMDoc(_PCBLIB, "PCBLIB"), [_DMDoc(_PCBLIB, "PCBLIB")]),
                        (None, [_DMDoc(_PCBLIB, "PCBLIB")]), (None, [])]:
        ns = _lib_ns(focus, free, _NoPcbServer(), client=_Client([_SCHLIB]))
        assert ns["PcbLibMayBeCurrentAt"](path) is \
            ns["PcbLibMayBeCurrent"](0), (path, focus, free)


# ---------------------------------------------------------------------------
# The ratchet's PCB-library pattern no longer accepts a guard it cannot see
# ---------------------------------------------------------------------------

def _pcblib_counter():
    from tests.test_no_modal_raising_patterns import PATTERNS
    return next(c for n, _w, c, _b in PATTERNS
                if n == "pcblib_lookup_before_focus_check")


@pytest.mark.parametrize("sample", [
    "If A Then X := Lib.GetPCBLibraryByPath(P) Else X := "
    "PCBServer.GetCurrentPCBLibrary;",
    "If PcbLibMayBeCurrent(0) Then Y := 1; X := PCBServer.GetCurrentPCBLibrary;",
    "D := Workspace.DM_FocusedDocument; X := PCBServer.GetCurrentPCBLibrary;",
    "Client.OpenDocument(K, P); X := PCBServer.GetCurrentPCBLibrary;",
    "F := ResolveLibFootprint(Params, E, W); X := PCBServer.GetCurrentPCBLibrary;",
])
def test_a_guard_elsewhere_in_the_routine_does_not_count(sample):
    assert _pcblib_counter()("R", sample) == 1


@pytest.mark.parametrize("sample", [
    "If PcbLibMayBeCurrent(0) Then X := PCBServer.GetCurrentPCBLibrary;",
    "Else If PcbLibMayBeCurrentAt(LibPath) Then\n    X := "
    "PCBServer.GetCurrentPCBLibrary;",
    "If Not PcbLibMayBeCurrent(0) Then\n    Begin\n        Result := "
    "BuildErrorResponse(RequestId, '', '');\n        Exit;\n    End;\n"
    "    Try X := PCBServer.GetCurrentPCBLibrary; Except End;",
    "Footprint := ResolveLibFootprint(Params, ErrCode, Why);\n    If Footprint "
    "= Nil Then\n    Begin\n        Result := BuildErrorResponse(RequestId, "
    "ErrCode, Why);\n        Exit;\n    End;\n    PcbLib := "
    "PCBServer.GetCurrentPCBLibrary;",
])
def test_the_strict_guard_forms_count_zero(sample):
    assert _pcblib_counter()("R", sample) == 0


def _conversion_counter():
    from tests.test_no_modal_raising_patterns import PATTERNS
    return next(c for n, _w, c, _b in PATTERNS
                if n == "conversion_without_range_check")


@pytest.mark.parametrize("sample,want", [
    ("If IsFloatStr(A) Then X := 1; Y := StrToFloat(B);", 1),
    ("If CfgIntOk(V) Then N := 1; V := W; N := StrToInt(V);", 1),
    ("If IsFloatStr(A) Then Y := StrToFloat(A);", 0),
    ("V := W; If CfgIntOk(V) Then Try N := StrToInt(V); Except End;", 0),
    ("If (Not IsInt32Str(S)) Or (X) Then R := D Else R := StrToInt(S);", 0),
])
def test_a_conversion_counts_as_guarded_only_by_a_check_of_its_own_argument(
        sample, want):
    assert _conversion_counter()("R", sample) == want


# ---------------------------------------------------------------------------
# 3D model formats: a deny-list, so no format Altium loads is refused
# ---------------------------------------------------------------------------

def _may_load():
    return build(_bodies("Library.pas"), ["Model3DExtMayLoad"],
                 {"ExtractFileExt": lambda s: Path(s).suffix})["Model3DExtMayLoad"]


@pytest.mark.parametrize("path", [
    r"C:\m\a.step", r"C:\m\a.STP", r"C:\m\a.x_t", r"C:\m\a.X_B",
    r"C:\m\a.SLDPRT", r"C:\m\a.wrl", r"C:\m\a.vrml", r"C:\m\a.igs",
    r"C:\m\a.IGES", r"C:\m\a.stl", r"C:\m\a.3ds", r"C:\m\a.anything"])
def test_a_format_that_may_be_a_model_still_reaches_the_loader(path):
    """Refusing a format Altium loads would change a successful result."""
    assert _may_load()(path) is True


@pytest.mark.parametrize("path", [
    r"C:\m\a.txt", r"C:\m\a.PDF", r"C:\m\a.csv", r"C:\m\a.json",
    r"C:\m\a.png", r"C:\m\a.JPG", r"C:\m\a", ""])
def test_a_format_that_is_certainly_not_a_model_is_refused(path):
    assert _may_load()(path) is False


@pytest.mark.parametrize("unit,routine", [("Library.pas", "Lib_Link3DModel"),
                                          ("PCB.pas", "PCB_Place3DBody")])
def test_a_raising_model_load_is_counted_and_answers_the_nil_branch(unit,
                                                                    routine):
    """A format the deny-list admits still goes to the loader as today; if
    the loader raises on it, the catch is COUNTED (live probe P7) and the
    Model = Nil branch answers."""
    body = strip_comments(_bodies(unit)[routine])
    m = re.search(r"Try\s+Model\s*:=\s*Body\.ModelFactory_FromFilename\("
                  r"ModelPath,\s*False\);\s*Except.*?End;", body, re.S)
    assert m, f"{routine}: the loader call has no counted Except"
    snip = ("Function Snip(Body : Integer; ModelPath : String) : Integer;\n"
            "Var\n    Model : Integer;\nBegin\n    Model := 7;\n    "
            + m.group(0) + "\n    Result := Model;\nEnd;\n")
    caught: list = []

    class _Body:
        def ModelFactory_FromFilename(self, p, f):
            raise PascalError("loader refused")

    ns = build({"Snip": snip}, ["Snip"],
               {"RecordCaughtException": caught.append})
    assert ns["Snip"](_Body(), r"C:\m\a.wrl") is None
    assert caught == [f"{routine}:ModelFactory_FromFilename"]


# ---------------------------------------------------------------------------
# ResolvePCBBoard: an existing .PcbLib board_path keeps today's open
# ---------------------------------------------------------------------------

class _BoardClient:
    def __init__(self, on_disk=()):
        self.on_disk = set(on_disk)
        self.opened: list = []
        self.shown: list = []

    def GetDocumentByPath(self, p):
        return None

    def OpenDocument(self, kind, p):
        self.opened.append(p)
        if p in self.on_disk:
            return type("S", (), {"path": p})()
        raise AssertionError(f"OpenDocument raised on {p}")

    def ShowDocument(self, d):
        self.shown.append(d.path)


class _BoardServer:
    def __init__(self, client):
        self.client = client

    @property
    def GetCurrentPCBBoard(self):
        return type("B", (), {"FileName": self.client.shown[-1]})()


def _resolve(client):
    ns = {"Client": client, "PCBServer": _BoardServer(client),
          "FileExists": lambda p: p in client.on_disk,
          "GetPCBBoardAnywhere": lambda d: "ANYWHERE",
          "RecordCaughtException": lambda w: None}
    build(_bodies("Main.pas"), ["LooksAbsolutePath", "DocumentPresent",
                                "PcbPathOpenable", "ResolvePCBBoard"], ns)
    return ns


def test_an_existing_pcblib_board_path_is_never_opened():
    """WITHDRAWN PREMISE (live 2026-10-11): this test said a PcbLib "is a
    PCB-server document: today's open answers the library's board". It is
    not what Altium does: OpenDocument('PCB', <a .PcbLib>) shows Altium's
    OWN modal 'File type not recognised.', which no Except sees, and the
    bridge blocked (it did the same at eec334c). Skipping it must not
    target another board either, so it answers Nil and the handlers refuse
    BOARD_PATH_IS_LIBRARY first."""
    lib = r"C:\x\lib.PcbLib"
    client = _BoardClient(on_disk={lib})
    got = _resolve(client)["ResolvePCBBoard"](lib)
    assert client.opened == []
    assert got is None


def test_a_missing_pcblib_board_path_is_not_opened_and_does_not_fall_back():
    client = _BoardClient()
    assert _resolve(client)["ResolvePCBBoard"](r"C:\x\gone.PcbLib") is None
    assert client.opened == []


# ---------------------------------------------------------------------------
# LoadMCPConfig: the fault-only backstop behind each check
# ---------------------------------------------------------------------------

def test_a_conversion_fault_cfgintok_admitted_keeps_the_default():
    """Should CfgIntOk ever admit a value this engine's StrToInt rejects,
    the default stays and the exception never reaches StartMCPPump."""
    defaults = {"PollIntervalActiveMs": 10, "PollIntervalIdleMs": 30,
                "IdleThreshold": 150, "AutoShutdownMs": 600000,
                "YieldIterations": 5, "YieldEveryNActive": 5}
    values = {"poll_interval_active_ms": "77", "poll_interval_idle_ms": "40"}

    def strtoint(s):
        if s == "77":
            raise PascalError("this engine refused it")
        return int(s)

    ns = dict(defaults)
    ns.update({"WorkspaceDir": "W\\", "CONFIG_FILE": "c.json",
               "FileExists": lambda p: True,
               "ReadFileContent": lambda p: "{...}",
               "ExtractJsonValue": lambda c, k: values.get(k, ""),
               "StrToInt": strtoint})
    build(_bodies("Main.pas"), ["CfgIntOk", "LoadMCPConfig"], ns)
    ns["LoadMCPConfig"](0)
    assert ns["PollIntervalActiveMs"] == 10
    assert ns["PollIntervalIdleMs"] == 40, "later keys still load"


# ---------------------------------------------------------------------------
# Gen_GetConstraintGroups: the newly reachable member reads are counted
# ---------------------------------------------------------------------------

def _raise(name):
    def get(self):
        raise PascalError(f"undeclared {name}")
    return property(get)


class _G1:                                  # count and target reads raise
    DM_ConstraintCount = _raise("DM_ConstraintCount")
    DM_TargetKindString = _raise("DM_TargetKindString")
    DM_TargetId = _raise("DM_TargetId")


class _C:
    DM_Kind = _raise("DM_Kind")
    DM_Data = _raise("DM_Data")


class _G2:
    DM_ConstraintCount = 2
    DM_TargetKindString = "Net"
    DM_TargetId = "CLK"

    def DM_Constraints(self, j):
        if j == 0:
            raise PascalError("undeclared DM_Constraints")
        return _C()


class _CGDoc:
    DM_ConstraintGroupCount = 3

    def DM_ConstraintGroups(self, i):
        if i == 0:
            raise PascalError("undeclared DM_ConstraintGroups")
        return _G1() if i == 1 else _G2()


def test_every_constraint_group_member_read_is_counted():
    caught: list = []
    sch = type("S", (), {"DocumentName": r"C:\P\Top.SchDoc"})()
    ns = {"SchServer": type("SS", (), {"GetCurrentSchDocument": sch})(),
          "ResolveLoadedDocPath": lambda p: p,
          "FindDMDocumentByPath": lambda p: _CGDoc(),
          "BuildSuccessResponse": lambda r, b: b,
          "BuildErrorResponse": lambda r, c, m: c,
          "EscapeJsonString": lambda s: s,
          "RecordCaughtException": caught.append}
    build(_bodies("Generic.pas"), ["Gen_GetConstraintGroups"], ns)
    reply = ns["Gen_GetConstraintGroups"]("{}", "r")
    assert caught == ["Gen_GetConstraintGroups:" + m for m in (
        "DM_ConstraintGroups", "DM_ConstraintCount", "DM_TargetKindString",
        "DM_TargetId", "DM_Constraints", "DM_Kind", "DM_Data")]
    assert reply == ('{"groups":[{"target_kind":"","target_id":"",'
                     '"constraint_count":0,"constraints":[]},'
                     '{"target_kind":"Net","target_id":"CLK",'
                     '"constraint_count":2,"constraints":[{"kind":"",'
                     '"data":""}]}],"count":3}')


# ---------------------------------------------------------------------------
# The new helpers' GetDocumentByPath: a fault-only backstop each
# ---------------------------------------------------------------------------

def test_document_present_survives_a_raising_lookup():
    ns = {"Client": _Client(raises=True), "FileExists": lambda p: p == "C:\\a"}
    build(_bodies("Main.pas"), ["DocumentPresent"], ns)
    assert ns["DocumentPresent"]("C:\\a") is True
    assert ns["DocumentPresent"]("C:\\b") is False


def test_pcb_editor_doc_open_survives_a_raising_lookup():
    member = _DMDoc(r"C:\P\b.PcbDoc", "PCB")
    ws = _Workspace(None, [])
    ws.DM_ProjectCount = 1
    ws.DM_Projects = lambda i: _Proj([member])
    ns = {"GetWorkspace": ws, "Client": _Client(raises=True)}
    build(_bodies("Main.pas"), ["PcbEditorDocOpen"], ns)
    assert ns["PcbEditorDocOpen"](0) is False


def _held_ns(client):
    from tests.test_library_popup_free import _ns
    return _ns(client)


def test_remember_lib_component_survives_a_raising_lookup():
    from tests.test_library_popup_free import Comp, SchLib
    ns = _held_ns(_Client(raises=True))
    ns["RememberLibComponent"](SchLib(_SCHLIB), Comp("X"), "X")
    assert ns["LastCreatedLibComponentName"] == "X"
    assert ns["LastCreatedLibDoc"] is None


def test_liveness_stays_true_when_the_lookup_raises():
    """CONSERVATIVE: a raising lookup is a doubt, not evidence the library
    has gone, so the answer stays today's True."""
    from tests.test_library_popup_free import Comp
    ns = _held_ns(_Client(raises=True))
    ns["LastCreatedLibComponent"] = Comp("X")
    ns["LastCreatedLibPath"] = _SCHLIB
    ns["LastCreatedLibDoc"] = object()
    assert ns["LastCreatedLibIsLive"](0) is True


def test_liveness_stays_true_when_only_the_second_lookup_raises():
    from tests.test_library_popup_free import Comp
    calls = []
    now = object()

    class _Flaky:
        def GetDocumentByPath(self, p):
            calls.append(p)
            if len(calls) == 2:
                raise PascalError("second lookup raised")
            return now

    ns = _held_ns(_Flaky())
    ns["LastCreatedLibComponent"] = Comp("X")
    ns["LastCreatedLibPath"] = _SCHLIB
    ns["LastCreatedLibDoc"] = object()          # a different object
    assert ns["LastCreatedLibIsLive"](0) is True
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# read_last_caught: this pump session only
# ---------------------------------------------------------------------------

def test_last_caught_ignores_a_catch_from_an_earlier_session(tmp_path):
    from eda_agent.bridge.inflight import read_last_caught
    (tmp_path / "activity.log").write_text(
        "T1,0,_session_start,version=A,protocol=1\n"
        "T2,0,_caught_exception,ReadMechKind:Kind,pcb.get_layers,r1\n"
        "T3,0,_session_end,\n"
        "T4,0,_session_start,version=A,protocol=1\n"
        "T5,7,application.ping,OK,10,{}\n", encoding="utf-8")
    assert read_last_caught(tmp_path) is None
    with open(tmp_path / "activity.log", "a", encoding="utf-8") as f:
        f.write("T6,0,_caught_exception,dispatcher,app.save_all,r2\n")
    assert read_last_caught(tmp_path) == {
        "at": "T6", "where": "dispatcher", "command": "app.save_all",
        "request_id": "r2"}


# ---------------------------------------------------------------------------
# Proj_ImportDocument: the copy is checked by its outcome
# ---------------------------------------------------------------------------

def test_a_copy_that_returns_false_silently_is_copy_failed():
    """Whether CopyFile raises or returns False on failure is probe P31;
    checking that the file arrived makes both agree, and keeps a missing
    folder's COPY_FAILED independent of which it is."""
    from tests.test_popup_free_audit_project import (
        _FS, _Client as _PClient, _Obj, _Recorder, _project_handler,
        _project_world)
    for dirs, want in [(("C:\\proj",), "COPY_FAILED"), ((), "COPY_FAILED")]:
        rec = _Recorder()
        src = "C:\\lib\\x.SchDoc"
        fs = _FS(rec, dirs=("C:\\lib",) + dirs, files={src: "DATA"})
        fs.CopyFile = lambda s, d, f: False          # no raise, no file
        added: list = []
        project = _Obj(DM_ProjectFullPath="C:\\proj\\p.PrjPcb",
                       DM_AddSourceDocument=added.append)
        ns = _project_world(rec, fs, _PClient(rec, fs),
                            workspace=_Obj(DM_FocusedProject=project))
        reply = _project_handler(ns, "Proj_ImportDocument")(
            json.dumps({"source_path": src}), "rid")
        assert reply == ("err", want,
                         "Failed to copy file to project directory"), dirs
        assert added == [] and rec.modals == []
