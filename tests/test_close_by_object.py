# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D-A-1, D-A-2 and D-A-4, measured live on bridge 2026.10.13.01.

D-A-4: lib_reload_library(library_path=X) with a DIFFERENT document Y
focused closed Y (an unmodified SchLib). Lib_ReloadLibrary ran
RunProcess('WorkspaceManager:CloseObject') with FileName=X, and CloseObject
acts on the FOCUSED document whatever FileName says. RefreshSchLibFromDisk
had the same close. Both now close the named server document itself
(CloseServerDocByObject, App_CloseDocument's route) and look it up again;
the reload refuses a modified or bridge-edited library before anything
closes, and names any other document the close took.

D-A-1: obj_create / obj_batch_create with container="component" wrote the
primitive with OwnerPartId=-1 (IsNotAccesible=T) while lib_add_symbol_lines
wrote the symbol's current part. Both generic creates now read the owner
part from the narrowed component (OwnerPartIdOf / OwnerDisplayModeOf) and
write it with SetOwnerPartUnlessGiven before AddSchObject.

D-A-2: obj_count(eLine, scope="lib_component:TP") read 0 with 2 lines on TP.
Two causes: (a) Gen_GetObjectCount never called ApplyLibComponentScope, so
it counted whatever symbol was CURRENT, not TP; (b) the SchLib iterator
yields the current part's primitives only, so the D-A-1 -1-owned lines were
invisible even with TP current (the second measurement) - D-A-1's fix
resolves (b).
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"

_SCHLIB = r"C:\L\s.SchLib"
_PCBLIB = r"C:\L\a.PcbLib"
_SHEET = r"C:\P\top.SchDoc"


def _bodies(name):
    return load(_ALTIUM / name, minimum=10)


def _body(unit, func):
    return strip_comments(_bodies(unit)[func])


# ---------------------------------------------------------------------------
# A workspace whose Client closes the document OBJECT it is given, and a
# RunProcess that does what Altium does: CloseObject closes the FOCUSED one.
# ---------------------------------------------------------------------------

class _SrvDoc:
    def __init__(self, path):
        self.FileName = path


class _DMDoc:
    def __init__(self, path):
        self.DM_FullPath = path


class _FreeProj:
    def __init__(self, client):
        self.client = client

    @property
    def DM_LogicalDocumentCount(self):
        return len(self.client.listed)

    def DM_LogicalDocuments(self, i):
        return _DMDoc(self.client.listed[i])


class _Workspace:
    def __init__(self, client):
        self.DM_ProjectCount = 0
        self.DM_FreeDocumentsProject = _FreeProj(client)
        self.DM_FocusedDocument = _DMDoc(client.focus)

    def DM_Projects(self, i):                    # pragma: no cover
        raise AssertionError("no projects in this fixture")


class _Client:
    def __init__(self, open_paths, focus):
        self.listed = list(open_paths)
        self.docs = {p.upper(): _SrvDoc(p) for p in open_paths}
        self.focus = focus
        self.closed: list[str] = []

    def GetDocumentByPath(self, p):
        return self.docs.get(p.upper())

    def CloseDocument(self, doc):
        self.closed.append(doc.FileName)
        self.docs.pop(doc.FileName.upper(), None)

    def reopen(self, p):
        self.docs[p.upper()] = _SrvDoc(p)
        if p not in self.listed:
            self.listed.append(p)


class _App:
    def ProcessMessages(self):
        return None


class _PcbServer:
    def __init__(self):
        self.asked = 0

    @property
    def GetCurrentPCBLibrary(self):
        self.asked += 1
        return object()


def reload_ns(open_paths, focus=_SCHLIB, modified=(), edited=(), server=None):
    """Lib_ReloadLibrary built with its real helpers (CloseServerDocByObject,
    OpenDocumentPaths, PathsGoneOtherThan, PipePathsToJsonArray). Returns
    (ns, client, processes run)."""
    client = _Client(open_paths, focus)
    params: dict = {}
    ran: list = []

    def run_process(name):
        ran.append((name, dict(params)))
        if name == "WorkspaceManager:CloseObject":       # Altium's behaviour
            client.docs.pop(client.focus.upper(), None)
        elif name == "WorkspaceManager:OpenObject":
            client.reopen(params["FileName"])

    caught: list = []
    ns = {
        "GetWorkspace": _Workspace(client),
        "Client": client,
        "PCBServer": server or _PcbServer(),
        "ExtractJsonValue": lambda p, k: p.get(k, ""),
        "BuildErrorResponse": lambda r, c, m: ("ERR", c, m),
        "BuildSuccessResponse": lambda r, d: ("OK", d),
        "EscapeJsonString": lambda s: s.replace("\\", "\\\\"),
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "FileExists": lambda p: True,
        "ResetParameters": params.clear,
        "AddStringParameter": params.__setitem__,
        "RunProcess": run_process,
        "Application": _App(),
        "RecordCaughtException": caught.append,
        "PcbLibLoadable": lambda p: False,
        "ModifiedFlagOf": lambda d: d.FileName in modified,
        "BridgeEditRecorded": lambda p: p in edited,
    }
    build(_bodies("Main.pas"), ["PcbEditorDocOpen", "PcbLibMayBeCurrent",
                                "PcbLibMayBeCurrentAt"], ns)
    build(_bodies("Application.pas"), ["CloseServerDocByObject"], ns)
    build(_bodies("Project.pas"), ["OpenDocumentPaths", "PathsGoneOtherThan",
                                   "PipePathsToJsonArray"], ns)
    build(_bodies("Library.pas"), ["Lib_ReloadLibrary"], ns)
    return ns, client, ran


# ---------------------------------------------------------------------------
# D-A-4
# ---------------------------------------------------------------------------

def test_neither_close_route_uses_closeobject():
    """THE MEASURED DEFECT: CloseObject acts on the focused document."""
    for func in ("Lib_ReloadLibrary", "RefreshSchLibFromDisk"):
        body = _body("Library.pas", func)
        assert "WorkspaceManager:CloseObject" not in body, (
            f"{func} closes through WorkspaceManager:CloseObject, which acts "
            f"on the FOCUSED document whatever FileName says (D-A-4)")
        assert "CloseServerDocByObject(" in body, func


def test_the_shared_close_is_app_close_documents_route():
    body = _body("Application.pas", "CloseServerDocByObject")
    assert "Client.CloseDocument(ServerDoc);" in body
    assert body.index("Client.CloseDocument(ServerDoc);") < body.index(
        "Again := Client.GetDocumentByPath(Path);")
    assert "Result := Again = Nil;" in body
    assert "RecordCaughtException('CloseServerDocByObject:CloseDocument');" in body


def test_reload_with_another_document_focused_closes_only_the_library():
    ns, client, ran = reload_ns([_SCHLIB, _PCBLIB, _SHEET], focus=_SCHLIB)
    kind, data = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert kind == "OK", data
    assert client.closed == [_PCBLIB]
    assert client.GetDocumentByPath(_SCHLIB) is not None, (
        "the focused SchLib was closed - the D-A-4 defect")
    assert client.GetDocumentByPath(_SHEET) is not None
    assert client.GetDocumentByPath(_PCBLIB) is not None, "reopened"
    assert '"was_open":true' in data and '"closed":true' in data
    assert '"other_documents_closed":[]' in data
    assert all(name != "WorkspaceManager:CloseObject" for name, _ in ran)


def test_reload_of_a_modified_library_is_refused_and_closes_nothing():
    ns, client, ran = reload_ns([_SCHLIB, _PCBLIB], modified={_PCBLIB})
    got = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert got[0] == "ERR" and got[1] == "MODIFIED", got
    assert client.closed == [] and ran == []


def test_reload_of_a_bridge_edited_library_is_refused():
    """The flag has been measured reading clean with '*' on the tab; the
    bridge's record is the witness (App_CloseDocument's rule)."""
    ns, client, ran = reload_ns([_SCHLIB, _PCBLIB], edited={_PCBLIB})
    got = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert got[0] == "ERR" and got[1] == "MODIFIED", got
    assert client.closed == [] and ran == []


def test_reload_of_a_library_not_open_only_opens_it():
    """It used to run CloseObject anyway - which closed the focused one."""
    ns, client, ran = reload_ns([_SCHLIB], focus=_SCHLIB)
    kind, data = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert kind == "OK", data
    assert client.closed == []
    assert client.GetDocumentByPath(_SCHLIB) is not None
    assert '"was_open":false' in data and '"closed":false' in data
    assert [name for name, _ in ran] == ["WorkspaceManager:OpenObject"]


def test_reload_names_another_document_the_close_took():
    """A close that takes something else is reported, not hidden."""
    ns, client, _ = reload_ns([_SCHLIB, _PCBLIB], focus=_SCHLIB)
    real_close = client.CloseDocument

    def close_two(doc):
        real_close(doc)
        client.docs.pop(_SCHLIB.upper(), None)
    client.CloseDocument = close_two
    kind, data = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert kind == "OK"
    assert '"other_documents_closed":["C:\\\\L\\\\s.SchLib"]' in data, data


def test_a_close_that_does_not_take_is_an_error():
    ns, client, ran = reload_ns([_SCHLIB, _PCBLIB])
    client.CloseDocument = lambda doc: None
    got = ns["Lib_ReloadLibrary"]({"library_path": _PCBLIB}, "r")
    assert got[0] == "ERR" and got[1] == "CLOSE_FAILED", got
    assert all(name != "WorkspaceManager:OpenObject" for name, _ in ran)


def test_refresh_closes_the_library_it_flushed_by_object():
    body = _body("Library.pas", "RefreshSchLibFromDisk")
    flush = body.index("DoFileSave")
    close = body.index("CloseServerDocByObject(ServerDoc, LibPath);")
    reopen = body.index("RunProcess('WorkspaceManager:OpenObject');")
    assert flush < close < reopen


# ---------------------------------------------------------------------------
# D-A-1
# ---------------------------------------------------------------------------

def test_both_generic_component_creates_write_the_owner_part():
    for func in ("Gen_CreateObject", "Gen_BatchCreate"):
        body = _body("Generic.pas", func)
        assert "OwnerPart := OwnerPartIdOf(Component);" in body, func
        assert "OwnerMode := OwnerDisplayModeOf(Component);" in body, func
        assert re.search(r"SetOwnerPartUnlessGiven\(NewObj,\s*PropsStr,\s*"
                         r"OwnerPart,\s*OwnerMode\);", body), func
        set_at = body.index("SetOwnerPartUnlessGiven(")
        assert set_at < body.index("Component.AddSchObject(NewObj);"), func
        # read before the PreProcess, as the Lib_Add* handlers do
        assert body.index("OwnerPart := OwnerPartIdOf(Component);") < \
            body.index("SchServer.ProcessControl.PreProcess("), func


def test_lib_add_symbol_line_uses_the_same_owner_part_reads():
    body = _body("Library.pas", "Lib_AddSymbolLine")
    assert "OwnerPart := OwnerPartIdOf(Component);" in body
    assert "SetOwnerPart(Line, OwnerPart, OwnerMode);" in body


class _Prim:
    def __init__(self):
        self.OwnerPartId = -1
        self.OwnerPartDisplayMode = -1


def _owner_ns():
    ns: dict = {}
    build(_bodies("Library.pas"), ["SetOwnerPartUnlessGiven"], ns)
    return ns


def test_owner_part_is_written_when_the_caller_gave_none():
    ns, obj = _owner_ns(), _Prim()
    ns["SetOwnerPartUnlessGiven"](obj, "Location.X=10|Corner.X=20", 1, 0)
    assert (obj.OwnerPartId, obj.OwnerPartDisplayMode) == (1, 0)


def test_an_explicit_owner_part_is_the_callers_to_keep():
    ns, obj = _owner_ns(), _Prim()
    obj.OwnerPartId = 2                     # ApplySetProperties ran first
    ns["SetOwnerPartUnlessGiven"](obj, "OwnerPartId=2|Location.X=10", 1, 0)
    assert (obj.OwnerPartId, obj.OwnerPartDisplayMode) == (2, 0)
    obj2 = _Prim()
    obj2.OwnerPartDisplayMode = 1
    ns["SetOwnerPartUnlessGiven"](obj2, "Location.X=1|ownerpartdisplaymode=1",
                                  3, 0)
    assert (obj2.OwnerPartId, obj2.OwnerPartDisplayMode) == (3, 1)


# ---------------------------------------------------------------------------
# D-A-2
# ---------------------------------------------------------------------------

def test_count_applies_the_lib_component_scope_like_query():
    body = _body("Generic.pas", "Gen_GetObjectCount")
    apply = "If Not ApplyLibComponentScope(ScopeType, ScopePath) Then"
    assert apply in body, (
        "obj_count ignores lib_component:NAME and counts whatever symbol is "
        "current (D-A-2)")
    assert body.index(apply) < body.index("Iterator := SchDoc.SchIterator_Create;")
    assert body.index("ParseScope(Scope, ScopeType, ScopePath);") < body.index(apply)
    q = _body("Generic.pas", "Gen_QueryObjects")
    assert apply in q


def test_count_refuses_a_scoped_pcb_type_like_query():
    body = _body("Generic.pas", "Gen_GetObjectCount")
    refuse = body.index("'SCOPE_NOT_SUPPORTED'")
    assert body.index("If ScopeType <> 'active_doc' Then") < refuse < \
        body.index("ProcessActivePCBDoc(")

