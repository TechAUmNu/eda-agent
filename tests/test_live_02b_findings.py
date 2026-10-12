# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""N1-N4 of the 2026-10-11 .02b live run (bridge 2026.10.11.02, AD
26.10.1.5; docs/RELEASE_VERIFICATION.md sections 38 and 39 record it).

N1  A 0-byte .PcbDoc reached Client.OpenDocument('PCB', ...): Altium put up
    its OWN uncounted 'Warning - File type not recognised.' box, blocked the
    loop ~13 s, and after OK handed back a NON-Nil blank document that
    pcb_place_components placed a part into and app_get_preferences
    reported. A file on disk that cannot be loaded is now judged BEFORE the
    open (FileLooksLoadable / UnopenedFileUnloadable): board_path refuses it
    BOARD_FILE_UNREADABLE, the board walk skips it, OpenDocumentIfPresent
    answers Nil.
N2  A no-argument app_save_all walked the bridge's own Altium_API.PrjScr
    and would have SetModified + DoFileSave'd a loaded script unit
    (Library.pas); script units also padded documents_seen / still_dirty.
N3  pcb_place_3d_body answered success (bbox null, height 0) for a model
    that loaded EMPTY and left the empty body on the board;
    lib_link_3d_model left it on the footprint.
N4  proj_save / proj_close save=true replied only {success, project_path}.

Every test EXECUTES the real Pascal (tests/pascal_eval.py) and fails on
407c33f for the field reason. The Altium fixtures fail a test OUTRIGHT
where Altium shows its own box (AssertionError, which no translated
Except catches), rather than raising something an Except could swallow.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.live_2026_10_11 import (
    OLE_HEAD_CP1252, OLE_HEAD_UTF8, LockedFile, _MAIN, _caught, _with,
    file_reader)
from tests.pascal_eval import PascalError, build, translate
from tests.pascal_source import load, strip_comments
from tests.test_bridge_record_names_one_file import (
    LIB, SHEET, Listed, Project, ServerDoc, World, _ALTIUM, _key)

_PCB = load(_ALTIUM / "PCB.pas", minimum=40)
_LIBRARY = load(_ALTIUM / "Library.pas", minimum=40)
_PROJECT = load(_ALTIUM / "Project.pas", minimum=40)

ZERO = r"C:\scratch\s2\scratch.PcbDoc"
BOARD = r"C:\scratch\s2\s2_board.PcbDoc"
SHEET_ZERO = r"C:\scratch\s2\zero.SchDoc"
MODAL = "Altium modal 'Warning - File type not recognised.'"


# ---------------------------------------------------------------------------
# N1 fixtures: a disk, a client whose OpenDocument of an unloadable file is
# Altium's own box, and the PCB server answering the board last shown
# ---------------------------------------------------------------------------

class _Srv:
    def __init__(self, path):
        self.FileName = path


class _Client:
    def __init__(self, on_disk, unloadable=(), open_docs=(), view=None):
        self.on_disk = {p.upper() for p in on_disk}
        self.unloadable = {p.upper() for p in unloadable}
        self.open_docs = {p.upper() for p in open_docs}
        self.view = view
        self.opened: list = []
        self.shown: list = []

    def GetDocumentByPath(self, p):
        return _Srv(p) if str(p).upper() in self.open_docs else None

    def OpenDocument(self, kind, p):
        self.opened.append((kind, p))
        if str(p).upper() in self.unloadable:
            raise AssertionError(MODAL)
        if str(p).upper() in self.on_disk | self.open_docs:
            self.open_docs.add(str(p).upper())
            return _Srv(p)
        return None

    def ShowDocument(self, doc):
        self.shown.append(doc.FileName)

    @property
    def GetCurrentView(self):
        return self.view


class _PCBServer:
    def __init__(self, client):
        self.client = client

    @property
    def GetCurrentPCBBoard(self):
        if not self.client.shown:
            return None
        return _Srv(self.client.shown[-1])


def _n1_ns(client, contents, workspace=None, loads=None):
    ns = {"Client": client, "PCBServer": _PCBServer(client),
          "FileExists": lambda p: str(p).upper() in client.on_disk,
          "GetWorkspace": workspace}
    ns.update(file_reader(contents, loads=loads))
    _caught(ns)
    names = ["LooksAbsolutePath", "FileLooksLoadable", "UnopenedFileUnloadable",
             "DocumentPresent", "OpenDocumentIfPresent", "BoardPathLibraryRefusal",
             "BoardPathFileRefusal", "GetPCBBoardAnywhere", "ResolvePCBBoard"]
    build(_MAIN, [n for n in names if n in _MAIN], ns)
    return ns


def _two_boards():
    client = _Client(on_disk=[ZERO, BOARD], unloadable=[ZERO])
    return client, {ZERO: ""}


# ---------------------------------------------------------------------------
# N1. The predicate
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,text,loadable", [
    (BOARD, OLE_HEAD_CP1252, True),            # binary board, cp1252 decode
    (BOARD, OLE_HEAD_UTF8, True),              # the same bytes, UTF-8 code page
    (BOARD, "|RECORD=Board|VERSION=2.0", True),  # ASCII board
    (r"C:\s\a.SchDoc", "|HEADER=Protel for Windows", True),
    (ZERO, "", False),                         # 0 bytes: the P6b file
    (BOARD, "hello, this is not a board", False),
    (r"C:\s\a.SchDoc", "PK\x03\x04", False),
    (r"C:\s\a.OutJob", "[OutputJobFile]", True),   # judged on non-empty only
    (r"C:\s\a.OutJob", "", False),
    (r"C:\s\old.Pcb", "PCB FILE 4", True),     # legacy board: no head test
])
def test_file_looks_loadable(path, text, loadable):
    client = _Client(on_disk=[path])
    ns = _n1_ns(client, {path: text})
    assert ns["FileLooksLoadable"](path) is loadable


def test_a_file_that_cannot_be_read_is_not_loadable_after_the_retries():
    loads = []
    client = _Client(on_disk=[BOARD])
    ns = _n1_ns(client, {BOARD: LockedFile}, loads=loads)
    assert ns["FileLooksLoadable"](BOARD) is False
    assert len(loads) == 12                      # ReadFileContent's retry count
    assert ns["_caught"] == []                   # the read's own Except, uncounted


def test_an_open_document_and_a_missing_file_are_never_judged():
    """Only a file that is on disk and NOT open is read: an open board is
    answered from memory, a missing one is the fallback's business."""
    loads = []
    client = _Client(on_disk=[ZERO], open_docs=[ZERO])
    ns = _n1_ns(client, {ZERO: ""}, loads=loads)
    assert ns["UnopenedFileUnloadable"](ZERO) is False
    assert ns["UnopenedFileUnloadable"](r"C:\nope.PcbDoc") is False
    assert ns["UnopenedFileUnloadable"]("") is False
    assert loads == []


# ---------------------------------------------------------------------------
# N1. ResolvePCBBoard and the board_path handlers
# ---------------------------------------------------------------------------

def test_resolve_never_opens_a_zero_byte_board_and_never_falls_back():
    client, contents = _two_boards()
    client.shown.append(BOARD)                  # another board is current
    ns = _n1_ns(client, contents)
    ns["GetPCBBoardAnywhere"] = lambda _d: "FALLBACK_BOARD"
    assert ns["ResolvePCBBoard"](ZERO) is None, (
        "a named unloadable board must not fall back to another board")
    assert client.opened == []


def test_resolve_still_opens_a_loadable_board():
    client, contents = _two_boards()
    ns = _n1_ns(client, contents)
    assert ns["ResolvePCBBoard"](BOARD).FileName == BOARD
    assert client.opened == [("PCB", BOARD)]


def _handler_ns(client, contents):
    ns = _n1_ns(client, contents)
    errors = []
    ns.update(
        ExtractJsonValue=lambda p, k: str(json.loads(p).get(k, "")),
        BuildErrorResponse=lambda rid, code, msg: errors.append((code, msg)) or "ERR",
        BuildSuccessResponse=lambda rid, data: "OK:" + data,
        EscapeJsonString=lambda s: s.replace("\\", "\\\\"))
    return ns, errors


def test_pcb_focus_board_refuses_a_zero_byte_board_before_anything_opens():
    client, contents = _two_boards()
    ns, errors = _handler_ns(client, contents)
    build(_PCB, ["PCB_FocusBoard"], ns)
    reply = ns["PCB_FocusBoard"](json.dumps({"board_path": ZERO}), "r")
    assert reply == "ERR"
    assert errors and errors[0][0] == "BOARD_FILE_UNREADABLE"
    assert ZERO in errors[0][1] and "Nothing was opened" in errors[0][1]
    assert client.opened == []
    ns["PCB_FocusBoard"](json.dumps({"board_path": BOARD}), "r")
    assert client.opened == [("PCB", BOARD)]


@pytest.mark.parametrize("handler", ["PCB_PlaceComponent", "PCB_PlaceComponents",
                                     "PCB_FocusBoard"])
def test_every_board_path_handler_refuses_an_unloadable_file_first(handler):
    """pcb_place_components placed its part INTO the blank document Altium
    handed back for the 0-byte file. The refusal sits before
    ResolvePCBBoard in every handler that takes board_path."""
    body = strip_comments(_PCB[handler])
    refuse = body.find("BoardRefusal := BoardPathFileRefusal(BoardPathStr);")
    resolve = body.find("ResolvePCBBoard(BoardPathStr)")
    assert refuse != -1, f"{handler} hands an unloadable board file to the open"
    assert refuse < resolve
    assert "'BOARD_FILE_UNREADABLE'" in body[refuse:resolve]


def test_the_refusal_is_empty_for_everything_but_an_unloadable_board_file():
    client = _Client(on_disk=[ZERO, BOARD, SHEET_ZERO], open_docs=[])
    ns = _n1_ns(client, {ZERO: "", SHEET_ZERO: ""})
    assert ns["BoardPathFileRefusal"](ZERO) != ""
    for path in (BOARD, r"C:\nope.PcbDoc", "", SHEET_ZERO):
        assert ns["BoardPathFileRefusal"](path) == "", path


# ---------------------------------------------------------------------------
# N1. The board walk (app_get_preferences and every read) and the SCH opens
# ---------------------------------------------------------------------------

class _Doc:
    def __init__(self, path, kind):
        self.DM_FullPath = path
        self.DM_DocumentKind = kind


class _Proj:
    def __init__(self, docs):
        self.docs = docs
        self.DM_LogicalDocumentCount = len(docs)

    def DM_LogicalDocuments(self, i):
        return self.docs[i]


class _Ws:
    def __init__(self, projects, focused=None):
        self.projects = projects
        self.DM_ProjectCount = len(projects)
        self.DM_FocusedDocument = focused

    def DM_Projects(self, i):
        return self.projects[i]


def test_the_board_walk_skips_a_zero_byte_listing_and_reads_the_real_board():
    client, contents = _two_boards()
    ws = _Ws([_Proj([_Doc(ZERO, "PCB"), _Doc(BOARD, "PCB")])],
             focused=_Doc(r"C:\scratch\s2\z.SchDoc", "SCH"))
    ns = _n1_ns(client, contents, workspace=ws)
    board = ns["GetPCBBoardAnywhere"](0)
    assert board.FileName == BOARD
    assert ("PCB", ZERO) not in client.opened


def test_the_board_walk_with_only_a_zero_byte_board_answers_nil():
    """app_get_preferences then answers "pcb": null instead of a blank
    document's settings."""
    client, contents = _two_boards()
    ws = _Ws([_Proj([_Doc(ZERO, "PCB")])], focused=_Doc(r"C:\s\z.SchDoc", "SCH"))
    ns = _n1_ns(client, contents, workspace=ws)
    assert ns["GetPCBBoardAnywhere"](0) is None
    assert client.opened == []


def test_open_document_if_present_answers_nil_for_an_unloadable_sheet():
    """proj_load_sheets ('SCH') and proj_run_outjob go through it."""
    good = r"C:\scratch\s2\good.SchDoc"
    client = _Client(on_disk=[SHEET_ZERO, good], unloadable=[SHEET_ZERO])
    ns = _n1_ns(client, {SHEET_ZERO: ""})
    assert ns["OpenDocumentIfPresent"]("SCH", SHEET_ZERO) is None
    assert client.opened == []
    assert ns["OpenDocumentIfPresent"]("SCH", good) is not None


def test_the_new_helpers_never_reference_an_editor_server():
    for name in ("FileLooksLoadable", "UnopenedFileUnloadable",
                 "BoardPathFileRefusal"):
        assert not re.search(r"\b(PCBServer|SchServer)\b",
                             strip_comments(_MAIN[name])), name


# ---------------------------------------------------------------------------
# N2. The bridge's own script project is never saved, listed or counted
# ---------------------------------------------------------------------------

PRJSCR = r"C:\Users\user\EDA Agent\scripts\Altium_API.PrjScr"
LIBPAS = r"C:\Users\user\EDA Agent\scripts\Library.pas"
FREEPAS = r"C:\Users\user\Desktop\notes.pas"


def _script_world():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.unit = ServerDoc(w.disk, LIBPAS)
    w.free_unit = ServerDoc(w.disk, FREEPAS)
    w.disk.add(LIBPAS)
    w.disk.add(FREEPAS)
    w.client.open += [w.unit, w.free_unit]
    w.script = Project(PRJSCR, [Listed(LIBPAS, "EditScriptDS")])
    w.ns["GetWorkspace"].projects = [w.script, w.project]
    w.free.docs.append(Listed(FREEPAS, "TEXT"))
    return w


def test_a_no_argument_save_all_never_writes_a_loaded_script_unit():
    """Live: every save-all reply listed Library.pas in skipped_unmodified,
    and the no-argument default (only_modified false) runs SetModified(True)
    + DoFileSave on every clean non-library document - the running bridge's
    own source."""
    w = _script_world()
    w.save_all(only_modified=False)
    for unit in (w.unit, w.free_unit):
        assert unit.saves == 0 and unit.marks == 0, unit.FileName
    assert w.sheet.saves == 1                  # the design sheet still is
    listed = "|".join([w.SaveSkippedPaths, w.SaveAttemptedKeys,
                       w.SaveUnresolvedPaths]).upper()
    assert "LIBRARY.PAS" not in listed and "NOTES.PAS" not in listed


def test_only_modified_lists_no_script_unit_as_skipped():
    w = _script_world()
    w.save_all(only_modified=True)
    assert "LIBRARY.PAS" not in w.SaveSkippedPaths.upper()
    assert "NOTES.PAS" not in w.SaveSkippedPaths.upper()
    assert _key(SHEET) in w.SaveSkippedPaths.upper()


def test_a_proj_save_of_the_script_project_writes_nothing():
    w = _script_world()
    w.unit.Modified = True
    w.ns["SaveProjectMembers"](w.script)
    assert w.unit.saves == 0 and w.unit.marks == 0


def test_documents_seen_and_still_dirty_count_design_documents_only():
    w = _script_world()
    _with(w, ["WorkspaceDocPaths", "CountDirtyInProject", "CountDirtyDocuments"])
    paths = w.ns["WorkspaceDocPaths"](0).upper()
    assert "LIBRARY.PAS" not in paths and "NOTES.PAS" not in paths
    assert _key(SHEET) in paths
    w.unit.Modified = True
    w.free_unit.Modified = True
    assert w.ns["CountDirtyDocuments"](0) == 0
    w.sheet.Modified = True
    assert w.ns["CountDirtyDocuments"](0) == 1


@pytest.mark.parametrize("kind,path,saveable", [
    ("SCH", SHEET, True), ("SCHLIB", r"C:\l\a.SchLib", True),
    ("PCB", BOARD, True), ("PCBLIB", LIB, True), ("OUTPUTJOB", r"C:\a.OutJob", True),
    ("EditScriptDS", LIBPAS, False), ("TEXT", FREEPAS, False),
    ("", r"C:\x\b.PcbDoc", True), ("", LIBPAS, False),
])
def test_the_save_allow_list(kind, path, saveable):
    w = World(bare="nil", focus=None)
    assert w.ns["IsSaveableDesignDoc"](Listed(path, kind)) is saveable


# ---------------------------------------------------------------------------
# N3. A model that loads EMPTY is MODEL_LOAD_FAILED and leaves no body
# ---------------------------------------------------------------------------

class _Body:
    def __init__(self, overall):
        self.OverallHeight = overall
        self.I_ObjectAddress = 4242
        self.ObjectId = 12
        self.Model = None
        self.Layer = None
        self.SetState_FromModel = None

    def ModelFactory_FromFilename(self, _path, _flag):
        return object()                          # NON-Nil, as live for the stubs

    def MoveByXY(self, _dx, _dy):
        pass


class _Board:
    def __init__(self):
        self.objects: list = []
        self.I_ObjectAddress = 7
        self.FileName = BOARD

    def AddPCBObject(self, obj):
        self.objects.append(obj)

    def RemovePCBObject(self, obj):
        self.objects.remove(obj)


class _Server:
    def __init__(self, body):
        self.body = body
        self.open = 0
        self.messages: list = []

    @property
    def PreProcess(self):
        self.open += 1

    @property
    def PostProcess(self):
        self.open -= 1

    def PCBObjectFactory(self, *_a):
        return self.body

    def SendMessageToRobots(self, *a):
        self.messages.append(a)


def _place_3d(overall):
    body, board = _Body(overall), _Board()
    server = _Server(body)
    errors, ok = [], []
    params = {"model_path": r"C:\scratch\stub.wrl", "x": "1000", "y": "1000"}
    ns = {
        "ExtractJsonValue": lambda p, k: str(params.get(k, "")),
        "FileExists": lambda p: True,
        "LowerCase": str.lower,
        "GetPCBBoardForMutation": lambda why: board,
        "StrToFloatDef": lambda s, d: float(s) if s else d,
        "ResolveLayerId": lambda b, name: 1, "eNoLayer": 0,
        "Model3DExtMayLoad": lambda p: True,
        "PCBServer": server, "eComponentBodyObject": 12, "eNoDimension": 0,
        "eCreate_Default": 0, "c_Broadcast": 0, "PCBM_BoardRegisteration": 0,
        "Body3D_ApplyModelState": lambda *a: True,
        "Body3D_ReadRect": lambda *a: None,      # Var outs: the box stays unread
        "MilsToCoordF": lambda v: int(v * 10000),
        "CoordToMilsF": lambda v: v / 10000.0,
        "Body3D_RectJson": lambda *a: "{}",
        "Body3D_RotationReadbackJson": lambda f: "{}",
        "MarkDocDirtyByPath": lambda p: True, "NoteNextStep": lambda s: None,
        "JsonObj": lambda s: "{" + s + "}",
        "JsonBool": lambda k, v: json.dumps(k) + ":" + json.dumps(bool(v)),
        "JsonStr": lambda k, v: json.dumps(k) + ":" + json.dumps(v),
        "JsonFloat": lambda k, v: json.dumps(k) + ":" + json.dumps(v),
        "JsonRaw": lambda k, v: json.dumps(k) + ":" + v,
        "JsonNull": lambda k: json.dumps(k) + ":null",
        "BuildErrorResponse": lambda rid, code, msg: errors.append((code, msg)) or "ERR",
        "BuildSuccessResponse": lambda rid, data: ok.append(json.loads(data)) or "OK",
        "BoardLayerNamesHint": lambda b: "",
    }
    _caught(ns)
    build(_PCB, ["PCB_Place3DBody"], ns)
    ns["PCB_Place3DBody"](json.dumps(params), "r")
    return board, server, errors, ok, ns


def test_pcb_place_3d_body_refuses_an_empty_model_and_removes_its_body():
    """Live P7: .wrl / .igs / .stl / .dat stubs answered success with bbox
    null and overall height 0, one empty body left on the board per call."""
    board, server, errors, ok, ns = _place_3d(overall=0)
    assert ok == [], "an empty model was reported as a placement: %r" % ok
    assert errors and errors[0][0] == "MODEL_LOAD_FAILED"
    assert "empty model" in errors[0][1]
    assert board.objects == [], "the empty body was left on the board"
    assert server.open == 0, "the transaction was left open"
    assert ns["_caught"] == []


def test_pcb_place_3d_body_keeps_a_model_with_a_height():
    board, server, errors, ok, _ = _place_3d(overall=2283900)
    assert errors == [] and ok and ok[0]["success"] is True
    assert len(board.objects) == 1 and server.open == 0


class _GrpIter:
    def __init__(self, items):
        self.items = list(items)
        self.i = 0

    def AddFilter_ObjectSet(self, _s):
        pass

    @property
    def FirstPCBObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextPCBObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


class _Footprint:
    def __init__(self, prims):
        self.prims = list(prims)
        self.I_ObjectAddress = 9

    @property
    def GroupIterator_Create(self):
        return _GrpIter(self.prims)

    def GroupIterator_Destroy(self, _it):
        pass

    def RemovePCBObject(self, prim):
        self.prims.remove(prim)


def _link_snippet():
    """Lib_Link3DModel from the box read after the add to the move: the part
    a model that loaded empty must stop at."""
    body = strip_comments(_LIBRARY["Lib_Link3DModel"])
    start = body.index("Body3D_ReadRect(Body, X1, Y1, X2, Y2, HaveRect);")
    start = body.index(";", start) + 1
    end = body.index("If AlignStr = 'none' Then", start)
    return ("Procedure Snip(Body : Integer; Footprint : Integer; ModelPath : String;"
            " Replace : Boolean; RemovedDistinct : Integer; HaveRect : Boolean;"
            " PcbLib : Integer);\n"
            "Begin\n" + body[start:end] + "\nEnd;")


def _run_link(overall, prims_before=(), replace=False, removed=0):
    body = _Body(overall)
    old = [type("P", (), {"I_ObjectAddress": 1000 + i, "ObjectId": 12})()
           for i, _ in enumerate(prims_before)]
    fp = _Footprint(old + [body])
    ns = {"PCBServer": _Server(body), "c_Broadcast": 0, "PCBM_BeginModify": 0,
          "PCBM_EndModify": 0, "c_NoEventData": 0, "eComponentBodyObject": 12,
          "eTextObject": 5, "MkSet": lambda *a: a, "Failure": "", "FailCode": "",
          # Since the review of 498e5ca the empty-model reply reads what the
          # library board still lists (tests/test_review_03_repairs_and_03b.py).
          "LibBoardStillListsCount": lambda board, entries: 0,
          # 2026.10.12.01: the undo also takes the body off the library
          # board and reports both reads (tests/test_live_03_findings.py).
          "LibBoardRemoveEntries": lambda *a: '{"requested":true}',
          "EscapeJsonString": lambda s: s,
          "BoolToJsonStr": lambda b: "true" if b else "false"}
    _caught(ns)
    build(load(_ALTIUM / "Utils.pas", minimum=20),
          ["JsonStr", "JsonInt", "JsonBool", "JsonRaw", "JsonObj"], ns)
    build(_LIBRARY, ["LibAddRemovalEntry", "Body3D_BodyAddresses"], ns)
    if "RemoveLibBodyByAddress" in _LIBRARY:
        # The evaluator takes value parameters only; Entries is an OUT list
        # nothing here reads, so it is passed by value.
        build({"R": _LIBRARY["RemoveLibBodyByAddress"].replace(
            "Var Entries : String", "Entries : String")}, ["R"], ns)
    snip = _link_snippet()
    # The slice ends with the End; that closes the If around the add, which
    # closes Snip here; the evaluator ignores the tokens after it.
    src = translate(snip)[1]
    exec(compile(src, "<pascal Snip>", "exec"), ns)  # noqa: S102
    lib = type("L", (), {"CurrentComponent": fp, "Board": object()})()
    ns["Snip"](body, fp, r"C:\scratch\stub.igs", replace, removed, False, lib)
    return ns, fp, body


def test_lib_link_3d_model_refuses_an_empty_model_and_takes_its_body_off():
    """Live P7: lib_link_3d_model answered ok false for the stubs but left
    the empty body on the footprint."""
    ns, fp, body = _run_link(overall=0, prims_before=[1])
    assert ns["FailCode"] == "MODEL_LOAD_FAILED"
    assert "empty model" in ns["Failure"]
    assert body not in fp.prims, "the empty body is still on the footprint"
    assert len(fp.prims) == 1, "an existing body was removed with it"


def test_lib_link_3d_model_says_what_replace_already_removed():
    ns, _fp, _body = _run_link(overall=0, replace=True, removed=2)
    assert "replace=true had ALREADY removed 2" in ns["Failure"]


def test_lib_link_3d_model_keeps_a_model_with_a_height():
    ns, fp, body = _run_link(overall=1500000)
    assert ns["Failure"] == "" and body in fp.prims


def test_the_body_removal_is_the_text_removal_line_for_line():
    """Rule 1 beside RemoveLibTextByAddress: the footprint removal keeps the
    shape that ran, with only the object set changed."""
    text = strip_comments(_LIBRARY["RemoveLibTextByAddress"]).strip()
    body = strip_comments(_LIBRARY["RemoveLibBodyByAddress"]).strip()
    assert body.replace("RemoveLibBodyByAddress", "RemoveLibTextByAddress") \
        .replace("eComponentBodyObject", "eTextObject") == text


# ---------------------------------------------------------------------------
# N4. proj_save / proj_close save=true report what reached disk
# ---------------------------------------------------------------------------

def _proj_world():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.ns["GetWorkspace"].DM_FocusedProject = w.project
    w.ns.update(
        FindProjectByPath=lambda ws, p: w.project,
        ExtractJsonValue=lambda p, k: (lambda v: "" if v is None else
                                       (str(v).lower() if isinstance(v, bool)
                                        else str(v)))(json.loads(p).get(k)),
        LowerCase=str.lower,
        EscapeJsonString=lambda s: json.dumps(s)[1:-1],
        BoolToJsonStr=lambda b: "true" if b else "false",
        PipeListToJsonArray=lambda s: json.dumps([e for e in s.split("|") if e]),
        BuildSuccessResponse=lambda rid, data: json.loads(data),
        BuildErrorResponse=lambda rid, code, msg: {"error": code})
    _with(w, ["AgesForPaths", "CountChangedAges", "BridgeRecordKeysIn"])
    build(_PROJECT, [n for n in ("SaveProjectMembersReport",
                                 "ProjectRecordPendingJson", "Proj_Save")
                     if n in _PROJECT], w.ns)
    return w


def test_proj_save_reports_attempted_written_and_pending():
    """Live 12b: the reply was {"success":true,"project_path":...} with no
    counters, so the D2 re-check had to read mtimes."""
    w = _proj_world()
    w.sheet.Modified = True
    reply = w.ns["Proj_Save"](json.dumps({}), "r")
    assert reply["success"] is True
    assert reply["documents_attempted"] == 1
    assert reply["documents_written"] == 1
    assert reply["skipped_unmodified"] == []
    assert reply["bridge_record_pending"] == []
    assert reply["only_modified"] is False


def test_proj_save_default_still_writes_a_clean_member():
    w = _proj_world()
    reply = w.ns["Proj_Save"](json.dumps({}), "r")
    assert w.sheet.saves == 1 and reply["documents_written"] == 1


def test_proj_save_only_modified_leaves_a_clean_member_alone():
    w = _proj_world()
    reply = w.ns["Proj_Save"](json.dumps({"only_modified": True}), "r")
    assert w.sheet.saves == 0
    assert reply["documents_attempted"] == 0 and reply["documents_written"] == 0
    assert [_key(p) for p in reply["skipped_unmodified"]] == [_key(SHEET)]
    assert w.SaveSkipCleanSheets is False and w.SaveSkipCleanLibs is False


def test_proj_save_counts_a_declined_save_as_attempted_not_written():
    w = _proj_world()
    w.sheet.Modified = True
    w.sheet.save_moves = False                  # the editor declined
    reply = w.ns["Proj_Save"](json.dumps({}), "r")
    assert reply["documents_attempted"] == 1 and reply["documents_written"] == 0


# proj_close save=true's counters are EXECUTED through a closing project in
# tests/test_review_03_repairs_and_03b.py (closed and refused branches, the
# pending list read after the close). The source-text check that stood here
# never ran Proj_Close (review of 498e5ca).


@pytest.mark.asyncio
async def test_the_python_tools_pass_only_modified(monkeypatch):
    sent = []

    class FakeBridge:
        async def send_command_async(self, command, params=None, timeout=None):
            sent.append((command, dict(params or {})))
            return {"success": True}

    monkeypatch.setattr("eda_agent.tools.project.get_bridge", lambda: FakeBridge())
    from eda_agent.tools import project
    tools = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                tools[fn.__name__] = fn
                return fn
            return decorator

    project.register_project_tools(DummyMcp())
    await tools["proj_save"](project_path=r"C:\p\a.PrjPcb", only_modified=True)
    await tools["proj_save"](project_path=r"C:\p\a.PrjPcb")
    await tools["proj_close"](project_path=r"C:\p\a.PrjPcb", save=True,
                              only_modified=True)
    assert sent[0] == ("project.save", {"project_path": r"C:\p\a.PrjPcb",
                                        "only_modified": True})
    assert "only_modified" not in sent[1][1]
    assert sent[2][1].get("only_modified") is True
