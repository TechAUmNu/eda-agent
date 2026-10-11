# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Popup-free changes one fixer needed in a file another fixer owned.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs, so the cure is to check
first. Each per-file fix stage found the same hazard in a file it did not
own and handed it on; these are those hand-offs, each EXECUTED where the
evaluator can run it:

* Generic.pas read the held library component (LastCreatedLibComponent)
  straight off the global in the library parameter delete, bypassing the
  liveness check Library.pas now applies, so a component whose library had
  closed went to the parameter iterator - the first member read there was
  the modal. It now goes through GetTargetLibComponent.
* obj_modify LibReference renamed the held component without its recorded
  name following, so FindLibComponentInMemory step 3 answered the renamed
  symbol for its OLD name. NoteLibComponentRenamed keeps it true.
* PCB_Place3DBody handed any file to ModelFactory_FromFilename inside the
  transaction; it now refuses a format the loader cannot read first, with
  the Model = Nil branch's own reply (the Lib_Link3DModel rule).
* Audit.pas handed a MkSet SET to AddFilter_IPCB_LayerSet (an OBJECT
  overload): the type-cast modal PCB_FilletCorners measured.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments
from tests.test_library_popup_free import (
    A, Client, Comp, Dead, Doc, SchLib, _Freed, _held, _ns)

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


def _bodies(name):
    return load(_ALTIUM / name, minimum=10)


# ---------------------------------------------------------------------------
# Generic.pas: the library parameter delete
# ---------------------------------------------------------------------------

def _lib_branch_source():
    """DeleteParametersAnyOwner's library branch, as a procedure of its own
    (the routine itself takes a Var parameter, outside the evaluator)."""
    body = strip_comments(_bodies("Generic.pas")["DeleteParametersAnyOwner"])
    start = body.index("If IsLib Then")
    end = body.index("If (OwnerDesig <> '') Or (ReducedFilter <> '') Then")
    branch = body[start:end]
    return ("Procedure LibBranch(SchDoc : ISch_Document; "
            "ReducedFilter : String);\n"
            "Var\n    Comp : ISch_Component;\n    IsLib : Boolean;\n"
            "Begin\n    IsLib := True;\n" + branch + "End;\n")


def _run_lib_branch(ns, lib):
    got = []
    ns["RemoveMatchingParamsFromComponent"] = (
        lambda comp, flt, total: got.append(comp))
    ns["TotalMatched"] = 0
    ns.setdefault("NoteNextStep", lambda text: None)
    build({"LibBranch": _lib_branch_source()}, ["LibBranch"], ns)
    ns["LibBranch"](lib, "Name=Value")
    return got


def test_library_parameter_delete_drops_a_held_component_whose_library_closed():
    client = Client({A: Doc()})
    ns = _ns(client)
    lib = SchLib(A, current=Dead())          # the editor's selection: never read
    held = Comp("MADE")
    _held(ns, lib, held, "MADE")
    assert _run_lib_branch(ns, lib) == [held], "a live held one is used"

    client.docs.clear()                      # the library closed
    ns["LastCreatedLibComponent"] = Dead()   # its components freed
    assert _run_lib_branch(ns, lib) == [], "nothing held: nothing matched"
    assert ns["LastCreatedLibComponent"] is None


def test_no_unit_but_library_reads_the_held_component_directly():
    """The liveness rules live in Library.pas's accessors; a unit that reads
    the global itself skips them (the Generic.pas case above)."""
    offenders = []
    for path in sorted(_ALTIUM.glob("*.pas")):
        if path.name in ("Library.pas", "Main.pas", "Altium_MCP.pas"):
            continue
        text = strip_comments(path.read_text(encoding="utf-8"))
        if re.search(r"\bLastCreatedLibComponent\b(?!Name)", text):
            offenders.append(path.name)
    assert offenders == []


# ---------------------------------------------------------------------------
# Generic.pas obj_modify LibReference -> Library.pas NoteLibComponentRenamed
# ---------------------------------------------------------------------------

def _rename_ns(client):
    ns = _ns(client)
    build(_bodies("Library.pas"), ["NoteLibComponentRenamed"], ns)
    return ns


def test_renaming_the_held_component_renames_its_record():
    client = Client({A: Doc()})
    ns = _rename_ns(client)
    lib = SchLib(A)
    held = Comp("OLD")
    _held(ns, lib, held, "OLD")
    ns["NoteLibComponentRenamed"](Comp("OTHER"), "ZZZ")
    assert ns["LastCreatedLibComponentName"] == "OLD", "another component"
    ns["NoteLibComponentRenamed"](held, "NEW")
    assert ns["LastCreatedLibComponentName"] == "NEW"
    assert ns["LastCreatedLibComponent"] is held


def test_rename_never_compares_a_held_component_whose_library_closed():
    client = Client({A: Doc()})
    ns = _rename_ns(client)
    _held(ns, SchLib(A), Comp("OLD"), "OLD")
    ns["LastCreatedLibComponent"] = _Freed()
    client.docs.clear()
    ns["NoteLibComponentRenamed"](Comp("X"), "NEW")
    assert ns["LastCreatedLibComponent"] is None
    assert ns["LastCreatedLibComponentName"] == ""


def test_the_libreference_setter_notes_the_rename():
    setter = strip_comments(_bodies("Generic.pas")["SetSchProperty"])
    m = re.search(r"PropName = 'LibReference'\s*Then\s*Begin(.*?)End", setter,
                  re.S)
    assert m, "the LibReference write is a single statement again"
    assert "Obj.LibReference := Value" in m.group(1)
    assert "NoteLibComponentRenamed(Obj, Value)" in m.group(1)


# ---------------------------------------------------------------------------
# PCB.pas PCB_Place3DBody: the model format is asked before the transaction
# ---------------------------------------------------------------------------

def test_place_3d_body_refuses_an_unloadable_format_before_preprocess():
    body = strip_comments(_bodies("PCB.pas")["PCB_Place3DBody"])
    guard = body.find("If Not Model3DExtMayLoad(ModelPath) Then")
    pre = body.find("PCBServer.PreProcess")
    factory = body.find("ModelFactory_FromFilename")
    assert guard != -1, "no format check"
    assert guard < pre < factory, "the check must come before the transaction"

    def reply(chunk):
        m = re.search(r"'MODEL_LOAD_FAILED'.*?'Could not load a 3D model from '"
                      r"\s*\+\s*ModelPath\s*\+\s*'([^']*)'", chunk, re.S)
        return m.group(1) if m else None

    early = reply(body[guard:pre])
    late = reply(body[factory:])
    assert early is not None and early == late, (early, late)


def test_model_formats_the_guard_admits():
    ns = build(_bodies("Library.pas"), ["Model3DExtMayLoad"],
               {"ExtractFileExt": lambda s: Path(s).suffix})
    ok = ns["Model3DExtMayLoad"]
    for good in (r"C:\m\a.step", r"C:\m\a.STP", r"C:\m\a.x_t", r"C:\m\a.X_B",
                 r"C:\m\a.SLDPRT", r"C:\m\a.wrl"):
        assert ok(good), good
    for bad in (r"C:\m\a.txt", r"C:\m\a", ""):
        assert not ok(bad), bad


# ---------------------------------------------------------------------------
# Audit.pas: the MkSet-into-IPCB_LayerSet cast
# ---------------------------------------------------------------------------

def test_no_unit_hands_a_mkset_set_to_the_layerset_object_overload():
    hits = {}
    for path in sorted(_ALTIUM.glob("*.pas")):
        if path.name == "Altium_MCP.pas":
            continue
        text = strip_comments(path.read_text(encoding="utf-8"))
        n = len(re.findall(r"AddFilter_IPCB_LayerSet\s*\(\s*MkSet\s*\(", text))
        if n:
            hits[path.name] = n
    assert hits == {}
    acute = strip_comments(
        _bodies("Audit.pas")["Audit_FindAcuteAngles"])
    assert re.search(r"SpatIter\.AddFilter_LayerSet\s*\(\s*MkSet\s*\(\s*"
                     r"Track\.Layer\s*\)\s*\)", acute), "still filters by layer"


# ---------------------------------------------------------------------------
# Python docstrings that described the old behaviour
# ---------------------------------------------------------------------------

def test_set_plane_net_docstring_matches_the_bridge():
    src = (_ROOT / "src" / "eda_agent" / "tools" / "pcb.py").read_text(
        encoding="utf-8")
    start = src.index("async def pcb_set_plane_net")
    doc = src[start:src.index('"""', src.index('"""', start) + 3)]
    assert "READ BACK" not in doc
    assert "CANNOT TIE THE NET YET" in doc
    body = strip_comments(_bodies("PCB.pas")["PCB_SetPlaneNet"])
    assert "LayerObj.Net" not in body


def test_label_orientation_is_documented_as_the_ordinal():
    src = (_ROOT / "src" / "eda_agent" / "tools" / "library.py").read_text(
        encoding="utf-8")
    assert "# optional, 0/90/180/270" not in src
    assert '"orientation": <int>,           # optional, 0..3' in src


# ---------------------------------------------------------------------------
# Bare PCBServer.GetCurrentPCBLibrary: asked only when it can answer
# ---------------------------------------------------------------------------

class _NoPcbServer:
    """Naming PCBServer before the PCB editor server loaded: the modal."""

    def __getattr__(self, name):
        raise AssertionError(f"PCBServer.{name} named with no PcbLib current")


class _App:
    def ProcessMessages(self):
        return None


def _pcblib_ns(focus_kind, open_kinds):
    from tests.test_popup_free_pcb import _workspace
    return {
        "GetWorkspace": _workspace(focus_kind, open_kinds),
        "PCBServer": _NoPcbServer(),
        "BuildErrorResponse": lambda r, c, m: ("ERR", c, m),
        "ExtractJsonValue": lambda p, k: p.get(k, ""),
        "StrToFloatDef": lambda s, d: float(s) if s else d,
        "FileExists": lambda p: True,
        "ResetParameters": lambda: None,
        "AddStringParameter": lambda k, v: None,
        "RunProcess": lambda p: None,
        "Application": _App(),
        "Client": Client(),
    }


_PCBLIB_LOOKUPS = [
    ("Lib_CreateFootprint", {}, "No PCB library is active"),
    ("Lib_ReloadLibrary", {"library_path": r"C:\L\a.PcbLib"},
     r"Library did not reopen: C:\L\a.PcbLib"),
    ("Lib_UpdateFootprintHeightsFrom3D", {}, "No PCB Library document focused"),
    ("Lib_SetFootprintHeight", {"height_mm": "1.5"},
     "No PCB Library document focused"),
]



@pytest.mark.parametrize("routine,params,msg", _PCBLIB_LOOKUPS)
@pytest.mark.parametrize("focus,open_kinds", [
    ("SCH", ("PCBLIB",)), ("SCHLIB", ()), (None, ()), (None, ("SCH",))])
def test_pcblib_lookup_is_not_named_when_it_answers_nil_anyway(
        routine, params, msg, focus, open_kinds):
    ns = _pcblib_ns(focus, open_kinds)
    build(_bodies("Main.pas"), ["PcbEditorDocOpen", "PcbLibMayBeCurrent",
                                "PcbLibMayBeCurrentAt"], ns)
    build(_bodies("Library.pas"), [routine], ns)
    assert ns[routine](params, "r") == ("ERR", "NO_PCBLIB", msg)


class _Lib:
    """A PcbLib that answers; any member read past the lookup ends the run."""

    def __getattr__(self, name):
        raise _Reached(name)


class _Reached(Exception):
    pass


class _PcbServer:
    def __init__(self):
        self.asked = 0

    @property
    def GetCurrentPCBLibrary(self):
        self.asked += 1
        return _Lib()

    def __getattr__(self, name):
        raise _Reached(name)


@pytest.mark.parametrize("routine,params,msg", _PCBLIB_LOOKUPS)
@pytest.mark.parametrize("focus", ["PCBLIB", None])
def test_pcblib_lookup_still_asked_when_a_library_may_be_current(
        routine, params, msg, focus):
    ns = _pcblib_ns(focus, ("PCBLIB",))
    ns["PCBServer"] = server = _PcbServer()
    ns["BuildSuccessResponse"] = lambda r, d: ("OK", d)
    ns["EscapeJsonString"] = lambda s: s
    build(_bodies("Main.pas"), ["PcbEditorDocOpen", "PcbLibMayBeCurrent",
                                "PcbLibMayBeCurrentAt"], ns)
    build(_bodies("Library.pas"), [routine], ns)
    try:
        ns[routine](params, "r")
    except _Reached:
        pass
    assert server.asked >= 1
