# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The core units ask before they call, and say which call raised.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press
OK' dialog BEFORE the surrounding Try/Except runs (measured; recorded
beside WriteFileContent and StrToFloatDef). A Try around a call that
raises on an ordinary bad input is therefore not a guard. These tests
cover the sites in Main, Utils, Application, StatusForm and SelfTest that
used to raise on such an input, and the diagnostics that name the command
when a modal fires anyway.

HOW. Every Pascal test EXECUTES the real source through tests/pascal_eval.
A call that Altium would raise on is modelled by a fixture raising
``Modal``, which a translated Except does NOT catch (it catches only the
deliberate ``PascalError``): so a site that still makes the raising call
fails here exactly as it would put a dialog up in Altium. On the false
branch of every guard the result must be what the old Except produced,
and the 'still works' cases pin that the guard never refuses something
that succeeded before.
"""
from __future__ import annotations

import asyncio
import json
import ntpath
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pascal_eval import PascalError, build, translate
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


class Modal(Exception):
    """An exception Altium surfaces as a modal before the Except runs.

    Deliberately NOT a PascalError, so no translated Try/Except catches it:
    reaching one fails the test the way the call would stop the user."""


def _main():
    return load(_ALTIUM / "Main.pas", minimum=40)


def _utils():
    return load(_ALTIUM / "Utils.pas", minimum=40)


def _app():
    return load(_ALTIUM / "Application.pas", minimum=10)


def _form():
    return load(_ALTIUM / "StatusForm.pas", minimum=30)


# ---------------------------------------------------------------------------
# Delphi RTL models
# ---------------------------------------------------------------------------

def _delphi_trim(s: str) -> str:
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


def _is_int_str(s: str) -> bool:
    """Utils.pas IsIntStr (indexes S[1], outside the evaluator's subset)."""
    t = _delphi_trim(s)
    if t == "":
        return False
    body = t[1:] if t[0] == "-" else t
    return body != "" and all("0" <= c <= "9" for c in body)


def _strtoint(s: str) -> int:
    """SysUtils.StrToInt: leading SPACES skipped, nothing trailing, 32 bits.
    Anything else raises, which in Altium is a modal."""
    t = s.lstrip(" ")
    if not re.fullmatch(r"[-+]?\d+", t):
        raise Modal(f"EConvertError: {s!r} is not a valid integer value")
    n = int(t)
    if not -2**31 <= n <= 2**31 - 1:
        raise Modal(f"EConvertError: {s!r} is out of range")
    return n


def _strtofloat(s: str) -> float:
    """SysUtils.StrToFloat with '.' as the separator: spaces skipped at both
    ends and nothing else; overflow raises."""
    t = s.strip(" ")
    if not re.fullmatch(r"[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", t):
        raise Modal(f"EConvertError: {s!r} is not a valid floating point value")
    v = float(t)
    if v in (float("inf"), float("-inf")):
        raise Modal(f"EOverflow: {s!r}")
    return v


def _extract_file_path(s: str) -> str:
    k = max(s.rfind("\\"), s.rfind(":"))
    return s[:k + 1]


class _Recorder:
    def __init__(self):
        self.calls: list = []

    def __call__(self, *args):
        self.calls.append(args)


# ---------------------------------------------------------------------------
# Utils: StrToIntDef / StrToFloatDef
# ---------------------------------------------------------------------------

def _strtointdef():
    ns = {"IsIntStr": _is_int_str, "StrToInt": _strtoint,
          "Trim": _delphi_trim}
    return build(_utils(), ["IsInt32Str", "StrToIntDef"], ns)["StrToIntDef"]


@pytest.mark.parametrize("s,want", [
    ("99999999999", 7), ("4294967295", 7), ("2147483648", 7),
    ("-2147483649", 7), ("12 ", 7), ("5\r", 7), ("5\n", 7), ("\t5", 7),
    ("abc", 7), ("", 7), ("null", 7)])
def test_strtointdef_never_hands_strtoint_a_value_it_raises_on(s, want):
    assert _strtointdef()(s, 7) == want


@pytest.mark.parametrize("s,want", [
    ("2147483647", 2147483647), ("-2147483648", -2147483648),
    ("00000000012", 12), (" 5", 5), ("0", 0), ("-12", -12), ("42", 42)])
def test_strtointdef_still_converts_what_it_converted(s, want):
    assert _strtointdef()(s, 7) == want


def _strtofloatdef():
    ns = {"StrToFloat": _strtofloat, "DecimalSeparator": "."}
    return build(_utils(), ["IsFloatStr", "StrToFloatDef"], ns)["StrToFloatDef"]


@pytest.mark.parametrize("s", ["1.5\r", "1.5\n", "2\t", "\t2", "\r\n3.0",
                               "1e999", "abc", ""])
def test_strtofloatdef_never_hands_strtofloat_a_value_it_raises_on(s):
    assert _strtofloatdef()(s, -1.0) == -1.0


@pytest.mark.parametrize("s,want", [("1.5", 1.5), (" 1.5 ", 1.5),
                                    ("-2", -2.0), ("1e05", 1e5)])
def test_strtofloatdef_still_converts_what_it_converted(s, want):
    assert _strtofloatdef()(s, -1.0) == want


# ---------------------------------------------------------------------------
# Main: LoadMCPConfig
# ---------------------------------------------------------------------------

_CFG_DEFAULTS = {"PollIntervalActiveMs": 10, "PollIntervalIdleMs": 30,
                 "IdleThreshold": 150, "AutoShutdownMs": 600000,
                 "YieldIterations": 5, "YieldEveryNActive": 5}


def _load_config(values: dict) -> dict:
    ns = dict(_CFG_DEFAULTS)
    ns.update({
        "WorkspaceDir": "W\\", "CONFIG_FILE": "mcp_config.json",
        "FileExists": lambda p: True, "ReadFileContent": lambda p: "{...}",
        "ExtractJsonValue": lambda content, key: values.get(key, ""),
        "StrToInt": _strtoint})
    build(_main(), ["CfgIntOk", "LoadMCPConfig"], ns)
    ns["LoadMCPConfig"](0)
    return {k: ns[k] for k in _CFG_DEFAULTS}


@pytest.mark.parametrize("bad", ["600000.0", "1e6", "3000000000", "10 ",
                                 "true", "null"])
def test_a_hand_edited_config_value_keeps_the_default_without_raising(bad):
    keys = ["poll_interval_active_ms", "poll_interval_idle_ms",
            "idle_threshold", "auto_shutdown_ms", "yield_iterations",
            "yield_every_n_active"]
    assert _load_config({k: bad for k in keys}) == _CFG_DEFAULTS


def test_a_good_config_value_still_applies():
    got = _load_config({"poll_interval_active_ms": "20",
                        "auto_shutdown_ms": "0", "idle_threshold": "-4"})
    assert got["PollIntervalActiveMs"] == 20
    assert got["AutoShutdownMs"] == 0          # N >= 0 kept for this one
    assert got["IdleThreshold"] == 150         # N > 0 kept for the rest


# ---------------------------------------------------------------------------
# Main: ResolvePCBBoard / GetPCBBoardAnywhere
# ---------------------------------------------------------------------------

class _SrvDoc:
    def __init__(self, path):
        self.path = path


class _Client:
    """OpenDocument raises on a path that is neither open nor on disk, the
    way Altium put up a dialog for it; a non-PCB path opens as itself."""

    def __init__(self, on_disk=(), open_docs=(), bare_ok=(), view=None):
        self.on_disk = set(on_disk)
        self.open_docs = set(open_docs)
        self.bare_ok = set(bare_ok)
        self.view = view
        self.opened: list = []
        self.shown: list = []

    def GetDocumentByPath(self, p):
        return _SrvDoc(p) if p in self.open_docs else None

    def OpenDocument(self, kind, p):
        self.opened.append((kind, p))
        if p in self.on_disk or p in self.open_docs or p in self.bare_ok:
            return _SrvDoc(p)
        raise Modal(f"cannot open {p}")

    def ShowDocument(self, doc):
        if doc is None:
            raise Modal("ShowDocument(Nil)")
        self.shown.append(doc.path)

    @property
    def GetCurrentView(self):
        return self.view


class _PCBServer:
    """GetCurrentPCBBoard answers the board last shown, and raises when the
    document shown is not a PCB (the undeclared-server modal)."""

    def __init__(self, client):
        self.client = client

    @property
    def GetCurrentPCBBoard(self):
        if not self.client.shown:
            return None
        last = self.client.shown[-1]
        if not last.lower().endswith((".pcbdoc", ".pcb")):
            raise Modal("Undeclared identifier: GetCurrentPCBBoard")
        return SimpleNamespace(FileName=last)


def _board_ns(client, **extra):
    rec = _Recorder()
    ns = {"Client": client, "PCBServer": _PCBServer(client),
          "FileExists": lambda p: p in client.on_disk,
          "Trim": _delphi_trim, "RecordCaughtException": rec, "_rec": rec}
    ns.update(extra)
    return ns


def _resolve(client):
    ns = _board_ns(client, GetPCBBoardAnywhere=lambda d: "ANYWHERE")
    build(_main(), ["LooksAbsolutePath", "DocumentPresent", "PcbPathOpenable",
                    "ResolvePCBBoard"], ns)
    return ns


@pytest.mark.parametrize("path", [r"C:\nope.PcbDoc", r"C:\x\sheet.SchDoc",
                                  r"C:\x\gone.PcbLib", r"\\srv\gone.PcbDoc"])
def test_resolve_pcb_board_falls_back_without_opening_what_cannot_be_a_board(
        path):
    client = _Client(on_disk={r"C:\x\sheet.SchDoc", r"C:\x\lib.PcbLib"})
    ns = _resolve(client)
    assert ns["ResolvePCBBoard"](path) == "ANYWHERE"
    assert client.opened == [], "OpenDocument was handed " + path


def test_resolve_pcb_board_still_opens_a_real_board():
    client = _Client(on_disk={r"C:\b.PcbDoc"})
    ns = _resolve(client)
    assert ns["ResolvePCBBoard"](r"C:\b.PcbDoc").FileName == r"C:\b.PcbDoc"
    client2 = _Client(open_docs={r"C:\unsaved.PcbDoc"})
    ns2 = _resolve(client2)
    assert ns2["ResolvePCBBoard"](r"C:\unsaved.PcbDoc").FileName == \
        r"C:\unsaved.PcbDoc"


def test_resolve_pcb_board_keeps_todays_attempt_for_a_bare_board_name():
    """GetDocumentByPath misses bare names, so the guard does not judge one."""
    client = _Client(bare_ok={"b.PcbDoc"})
    ns = _resolve(client)
    assert ns["ResolvePCBBoard"]("b.PcbDoc").FileName == "b.PcbDoc"
    assert ns["ResolvePCBBoard"]("") == "ANYWHERE"


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


def _anywhere(client, ws):
    ns = _board_ns(client, GetWorkspace=ws)
    build(_main(), ["LooksAbsolutePath", "DocumentPresent",
                    "GetPCBBoardAnywhere"], ns)
    return ns


def test_the_board_walk_skips_a_listed_board_that_is_not_on_disk():
    client = _Client(on_disk={r"C:\p\b.PcbDoc"},
                     view=SimpleNamespace(OwnerDocument=_SrvDoc(r"C:\p\s.SchDoc")))
    ws = _Ws([_Proj([_Doc(r"C:\p\gone.PcbDoc", "PCB"),
                     _Doc(r"C:\p\b.PcbDoc", "PCB")])],
             focused=_Doc(r"C:\p\s.SchDoc", "SCH"))
    ns = _anywhere(client, ws)
    board = ns["GetPCBBoardAnywhere"](0)
    assert board.FileName == r"C:\p\b.PcbDoc"
    assert ("PCB", r"C:\p\gone.PcbDoc") not in client.opened
    assert client.shown[-1] == r"C:\p\s.SchDoc", "the prior view is restored"


def test_the_board_walk_with_only_a_dead_board_answers_nil_without_a_dialog():
    client = _Client()
    ws = _Ws([_Proj([_Doc(r"C:\p\gone.PcbDoc", "PCB")])])
    ns = _anywhere(client, ws)
    assert ns["GetPCBBoardAnywhere"](0) is None
    assert client.opened == []


def test_the_board_walk_restores_nothing_from_a_view_with_no_document():
    """The Home page or a web tab: OwnerDocument is Nil, and ShowDocument(Nil)
    raised. Restoring nothing is what the old Except left."""
    client = _Client(on_disk={r"C:\p\b.PcbDoc"},
                     view=SimpleNamespace(OwnerDocument=None))
    ws = _Ws([_Proj([_Doc(r"C:\p\b.PcbDoc", "PCB")])])
    ns = _anywhere(client, ws)
    assert ns["GetPCBBoardAnywhere"](0).FileName == r"C:\p\b.PcbDoc"
    assert client.shown == [r"C:\p\b.PcbDoc"]


def test_an_owner_read_that_raises_is_counted_not_silent():
    class _View:
        @property
        def OwnerDocument(self):
            raise PascalError("not a document view")

    client = _Client(on_disk={r"C:\p\b.PcbDoc"}, view=_View())
    ws = _Ws([_Proj([_Doc(r"C:\p\b.PcbDoc", "PCB")])])
    ns = _anywhere(client, ws)
    assert ns["GetPCBBoardAnywhere"](0).FileName == r"C:\p\b.PcbDoc"
    assert ns["_rec"].calls == [("GetPCBBoardAnywhere:OwnerDocument",)]


def test_the_board_walk_keeps_todays_attempt_for_a_bare_listing():
    client = _Client(bare_ok={"b.PcbDoc"})
    ws = _Ws([_Proj([_Doc("b.PcbDoc", "PCB")])])
    ns = _anywhere(client, ws)
    assert ns["GetPCBBoardAnywhere"](0).FileName == "b.PcbDoc"


# ---------------------------------------------------------------------------
# Utils: ReadMechKind, MechLayerFromNumber, RestoreFocusedDoc
# ---------------------------------------------------------------------------

class _Layer:
    def __init__(self, raises=False):
        self.raises, self.reads = raises, 0

    @property
    def Kind(self):
        self.reads += 1
        if self.raises:
            raise Modal("no mechanical layer kinds on this build")
        return 7


@pytest.mark.parametrize("major,want_read", [(17, False), (18, False),
                                             (19, True), (26, True),
                                             (-1, True)])
def test_read_mech_kind_is_not_attempted_below_ad19(major, want_read):
    rec = _Recorder()
    ns = {"AltiumMajorVersion": lambda d: major, "RecordCaughtException": rec}
    build(_utils(), ["ReadMechKind"], ns)
    layer = _Layer(raises=not want_read)
    got = ns["ReadMechKind"](layer)
    assert got == (7 if want_read else -1)
    assert layer.reads == (1 if want_read else 0)
    assert ns["ReadMechKind"](None) == -1


def test_a_mech_layer_accessor_failure_is_counted():
    rec = _Recorder()

    class _LU:
        def MechanicalLayer(self, n):
            raise PascalError("LayerUtils refused")

    ns = {"LayerUtils": _LU(), "eNoLayer": 0, "RecordCaughtException": rec,
          "GetLayerFromString": lambda s: 1000 + int(s[len("Mechanical"):])}
    build(_utils(), ["MechLayerFromNumber"], ns)
    assert ns["MechLayerFromNumber"](21) == 0
    assert rec.calls == [("MechLayerFromNumber:LayerUtils.MechanicalLayer",)]
    assert ns["MechLayerFromNumber"](5) == 1005
    assert len(rec.calls) == 1


def _restore(on_disk=(), open_docs=(), focused=""):
    params: list = []
    client = _Client(on_disk=on_disk, open_docs=open_docs)

    def run_process(name):
        path = dict(params).get("FileName", "")
        if ntpath.isabs(path) and path not in client.on_disk \
                and path not in client.open_docs:
            raise Modal("file not found: " + path)
        params.append(("RAN", name))

    ns = {"Client": client, "FileExists": lambda p: p in client.on_disk,
          "Trim": _delphi_trim, "CurrentFocusedDocPath": lambda d: focused,
          "ResetParameters": lambda: params.clear(),
          "AddStringParameter": lambda k, v: params.append((k, v)),
          "RunProcess": run_process}
    build(_main(), ["LooksAbsolutePath", "DocumentPresent"], ns)
    build(_utils(), ["RestoreFocusedDoc"], ns)
    return ns, params


def test_restore_focused_doc_skips_a_path_that_is_gone():
    ns, params = _restore(focused=r"C:\p\other.SchDoc")
    ns["RestoreFocusedDoc"](r"C:\p\deleted.SchDoc")
    assert ("RAN", "WorkspaceManager:OpenObject") not in params


@pytest.mark.parametrize("saved,kw", [
    (r"C:\p\a.SchDoc", {"on_disk": {r"C:\p\a.SchDoc"}}),
    (r"C:\p\new.SchDoc", {"open_docs": {r"C:\p\new.SchDoc"}}),
    ("Sheet1.SchDoc", {})])
def test_restore_focused_doc_still_restores_what_it_restored(saved, kw):
    ns, params = _restore(focused=r"C:\p\other.SchDoc", **kw)
    ns["RestoreFocusedDoc"](saved)
    assert ("RAN", "WorkspaceManager:OpenObject") in params


# ---------------------------------------------------------------------------
# Main: progress heartbeat, purge counts
# ---------------------------------------------------------------------------

def _start_progress():
    writes: list = []
    ns = {"IsValidRequestId": lambda r: bool(re.fullmatch(r"[\w-]{1,64}", r)),
          "ProgressFilePath": lambda r: "W\\progress_" + r + ".json",
          "WriteFileContent": lambda p, c: writes.append((p, c)),
          "GetTickCount": 4242}
    build(_main(), ["ProgressSafeText", "StartProgress"], ns)
    return ns, writes


def test_the_heartbeat_names_the_command_and_the_request():
    ns, writes = _start_progress()
    ns["StartProgress"]("ab12-cd", "application.save_all")
    path, content = writes[-1]
    assert path == "W\\progress_ab12-cd.json"
    assert json.loads(content) == {"started_ms": 4242,
                                   "command": "application.save_all",
                                   "request_id": "ab12-cd"}


def test_the_heartbeat_command_is_always_valid_json():
    ns, writes = _start_progress()
    ns["StartProgress"]("r1", 'odd"cmd\\x y{}')
    data = json.loads(writes[-1][1])
    assert data["command"] == "odd_cmd_x_y__"
    ns["StartProgress"]("bad id!", "application.ping")
    assert len(writes) == 1, "an invalid request id writes nothing, as before"


class _Files(list):
    @property
    def Count(self):
        return len(self)

    @property
    def Free(self):
        return None


@pytest.mark.parametrize("name,pattern", [
    ("CleanupOrphanRequests", "request_*.json"),
    ("CleanupOrphanResponses", "response_*.json")])
def test_the_purge_count_counts_only_files_that_went(name, pattern):
    logged: list = []
    deleted: list = []
    listing = ["W\\a.json", "W\\locked.json", "W\\b.json"]
    ns = {"WorkspaceDir": "W\\",
          "TStringList": SimpleNamespace(Create=_Files()),
          "FindFiles": lambda d, pat, attrs, rec, out: (
              out.extend(listing) if pat == pattern else None),
          "DeleteFile": lambda p: (deleted.append(p) or True)
          if "locked" not in p else False,
          "AppendLog": logged.append, "FormatLogStamp": lambda d: "T"}
    build(_main(), [name], ns)
    ns[name](0)
    assert len(deleted) == 2
    assert logged and "count=2," in logged[-1], logged


# ---------------------------------------------------------------------------
# Application: App_Ping, SchServer Nil, ExitActiveCommand, CreateDocument
# ---------------------------------------------------------------------------

def _json_escape(s: str) -> str:
    return json.dumps(s)[1:-1]


def _app_ns(**extra):
    rec = _Recorder()
    ns = {"EscapeJsonString": _json_escape,
          "BuildSuccessResponse": lambda rid, data: data,
          "BuildErrorResponse": lambda rid, code, msg: json.dumps(
              {"error": code, "message": msg}),
          "BoolToJsonStr": lambda b: "true" if b else "false",
          "RecordCaughtException": rec, "_rec": rec}
    ns.update(extra)
    build(_utils(), ["JsonStr", "JsonNull", "JsonRaw", "JsonObj"], ns)
    return ns


def _ping(**state):
    ns = _app_ns(Client=SimpleNamespace(GetProductVersion="26.10.1.6"),
                 SCRIPT_VERSION="S", PROTOCOL_VERSION=2, CastErrorCount=1,
                 CaughtExceptionCount=0, LastCaughtStamp="",
                 LastCaughtWhere="", LastCaughtCommand="",
                 LastCaughtRequestId="")
    ns.update(state)
    build(_app(), ["App_Ping"], ns)
    return json.loads(ns["App_Ping"]("rid"))


def test_ping_reports_the_caught_exception_count_and_the_last_one():
    quiet = _ping()
    assert quiet["caught_exceptions"] == 0 and quiet["last_caught"] is None
    assert quiet["cast_errors"] == 1 and quiet["pong"] is True
    busy = _ping(CaughtExceptionCount=3, LastCaughtStamp="2026-10-11 10:00",
                 LastCaughtWhere="dispatcher",
                 LastCaughtCommand="application.save_all",
                 LastCaughtRequestId="r9")
    assert busy["caught_exceptions"] == 3
    assert busy["last_caught"] == {"where": "dispatcher",
                                   "command": "application.save_all",
                                   "request_id": "r9",
                                   "at": "2026-10-11 10:00"}


class _SchDoc:
    DocumentName = r"C:\p\s.SchDoc"
    VisibleGridSize = 10
    SnapGridSize = 5


def _active(sch_server):
    board = SimpleNamespace(FileName=r"C:\p\b.PcbDoc")
    ns = _app_ns(GetWorkspace=_Ws([], focused=None), SchServer=sch_server,
                 GetPCBBoardAnywhere=lambda d: board,
                 DocIsModified=lambda p: False,
                 ExtractFileName=ntpath.basename)
    build(_app(), ["App_GetActiveDocument"], ns)
    return json.loads(ns["App_GetActiveDocument"]("rid"))


def test_active_document_with_no_schematic_server_falls_through_to_the_board():
    got = _active(None)
    assert got["document_kind"] == "PCB"
    assert got["file_path"] == r"C:\p\b.PcbDoc"


def test_active_document_still_reports_a_current_sheet():
    got = _active(SimpleNamespace(GetCurrentSchDocument=_SchDoc()))
    assert got["document_kind"] == "SCH"


def _prefs(sch_server, board=None):
    ns = _app_ns(SchServer=sch_server, GetPCBBoardAnywhere=lambda d: board,
                 CoordToMils=lambda c: c)
    build(_app(), ["App_GetPreferences"], ns)
    return json.loads(ns["App_GetPreferences"]("rid"))


def test_preferences_with_no_schematic_server_say_null_without_raising():
    assert _prefs(None) == {"pcb": None, "schematic": None}


def test_preferences_still_read_a_current_sheet():
    got = _prefs(SimpleNamespace(GetCurrentSchDocument=_SchDoc()))
    assert got["schematic"] == {"visible_grid_size": 10, "snap_grid_size": 5}


class _Pcb:
    def __init__(self, raises):
        self.raises, self.calls = raises, 0

    @property
    def PostProcess(self):
        self.calls += 1
        if self.raises:
            raise PascalError("PCBServer is not available")
        return None


def _exit_cmd(pcb, sch_server):
    ns = _app_ns(PCBServer=pcb, SchServer=sch_server,
                 CountDirtyDocuments=lambda d: 0)
    build(_app(), ["App_ExitActiveCommand"], ns)
    return ns, json.loads(ns["App_ExitActiveCommand"]("rid"))


def test_exit_active_command_stops_at_the_first_pcb_raise_and_counts_it():
    pcb = _Pcb(raises=True)
    ns, got = _exit_cmd(pcb, None)
    assert pcb.calls == 1, "seven more modals bought nothing"
    assert got["pcb_post_process"] is False
    assert got["sch_post_process"] is False
    assert ns["_rec"].calls == [("App_ExitActiveCommand:PCBServer.PostProcess",)]


def test_exit_active_command_still_unwinds_eight_levels_when_it_can():
    pcb = _Pcb(raises=False)
    posted: list = []
    sch = SimpleNamespace(GetCurrentSchDocument=None, ProcessControl=SimpleNamespace(
        PostProcess=lambda doc, s: posted.append(doc)))
    ns, got = _exit_cmd(pcb, sch)
    assert pcb.calls == 8
    assert got["pcb_post_process"] is True and got["sch_post_process"] is True
    assert posted == [None]
    assert ns["_rec"].calls == []


class _NewDoc:
    def __init__(self, client):
        self.client = client
        self.file = ""

    def SetFileName(self, p):
        self.file = p

    def SetModified(self, b):
        pass

    def DoFileSave(self, s):
        folder = _extract_file_path(self.file)
        if folder and folder not in self.client.dirs:
            raise Modal("EFCreateError: cannot create " + self.file)
        self.client.disk.add(self.file)

    @property
    def Focus(self):
        return None


class _CreateClient:
    def __init__(self, dirs):
        self.dirs = set(dirs)
        self.disk: set = set()

    def OpenNewDocument(self, kind, path, name, reuse):
        return _NewDoc(self)


def _create(params, dirs):
    client = _CreateClient(dirs)
    ran: list = []

    def run_process(name):
        raise Modal("Save As on a missing folder")

    ns = _app_ns(Client=client, GetWorkspace=None,
                 ExtractJsonValue=lambda p, k: params.get(k, ""),
                 ExtractFileName=ntpath.basename,
                 FileExists=lambda p: p in client.disk,
                 DirectoryExists=lambda d: d in client.dirs,
                 ExtractFilePath=_extract_file_path, Trim=_delphi_trim,
                 ResetParameters=lambda: None,
                 AddStringParameter=lambda k, v: None,
                 RunProcess=run_process)
    build(_utils(), ["TargetDirExists"], ns)
    build(_app(), ["App_CreateDocument"], ns)
    return json.loads(ns["App_CreateDocument"](json.dumps(params), "rid"))


def test_create_document_into_a_missing_folder_reports_unsaved_without_saving():
    got = _create({"kind": "SCH", "file_path": r"C:\no_such_dir\x.SchDoc",
                   "add_to_project": "false"}, dirs={r"C:\p" + "\\"})
    assert got["success"] is True and got["saved"] is False


def test_create_document_into_a_real_folder_still_saves():
    got = _create({"kind": "SCH", "file_path": r"C:\p\x.SchDoc",
                   "add_to_project": "false"}, dirs={r"C:\p" + "\\"})
    assert got["saved"] is True


# ---------------------------------------------------------------------------
# StatusForm: the in-flight readout and the dispatcher's bookkeeping
# ---------------------------------------------------------------------------

def _form_ns(**extra):
    font = SimpleNamespace(Color=None)
    ns = {"lbl_Status": SimpleNamespace(Caption="", Font=font),
          "lbl_Spinner": SimpleNamespace(Caption=""),
          "pnl_StatusDot": SimpleNamespace(Color=None),
          "tmr_Spinner": SimpleNamespace(Enabled=False),
          "COLOR_ACCENT_AMBER": 1, "COLOR_TEXT_BODY": 2,
          "COLOR_TEXT_FAINT": 3, "COLOR_TEXT_MUTED": 4,
          "COLOR_ACCENT_GREEN": 5, "SPINNER_FRAMES": 4,
          "SpinnerGlyph": lambda f: "*", "PausedFlag": False,
          "FormatDateTime": lambda fmt, now: "12:34:56", "Now": None,
          "GetTickCount": 1000, "InFlightActive": False,
          "InFlightRequestId": "", "InFlightSince": "", "SpinnerFrame": 0}
    ns.update(extra)
    build(_form(), ["InFlightLabel", "SetInFlight", "ResetInFlight",
                    "tmr_SpinnerTimer"], ns)
    return ns


def test_the_status_line_names_the_request_and_its_wall_clock_start():
    ns = _form_ns()
    ns["SetInFlight"]("application.save_all", "abcdef0123456789")
    cap = ns["lbl_Status"].Caption
    assert cap.startswith("application.save_all  #abcdef01  since 12:34:56")
    ns["GetTickCount"] = 4500
    ns["tmr_SpinnerTimer"](None)
    cap = ns["lbl_Status"].Caption
    assert "#abcdef01" in cap and "since 12:34:56" in cap and "(3.5 s)" in cap
    ns["ResetInFlight"](0)
    assert ns["InFlightRequestId"] == "" and ns["InFlightSince"] == ""
    assert ns["lbl_Status"].Caption == "idle"


def _extract_json_value(content, key):
    """Main.pas ExtractJsonValue, for the shapes this test feeds it: a
    nested object comes back as its JSON text, a scalar as its text."""
    try:
        data = json.loads(content)
    except (TypeError, ValueError):
        return ""
    if not isinstance(data, dict) or key not in data:
        return ""
    value = data[key]
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return "" if value is None else str(value)


def _dispatch(handler):
    seen: dict = {"set_in_flight": [], "start_progress": [], "caught": [],
                  "written": []}
    body = {"id": "req1", "command": "application.save_all", "params": "{}",
            "protocol_version": "2"}

    def record(where):
        seen["caught"].append((where, ns["CurrentCommand"],
                               ns["CurrentRequestId"]))

    def process(command, params, rid):
        seen["during"] = (ns["CurrentCommand"], ns["CurrentRequestId"])
        return handler()

    ns = {"EnsureWorkspaceDir": lambda d: None,
          "ScanForRequestFile": lambda p, r: True,
          "ReadFileContent": lambda p: json.dumps(body),
          "DeleteFile": lambda p: True,
          "ExtractJsonValue": _extract_json_value,
          "ValidateRequestEnvelope": lambda r, c: "",
          "IsValidRequestId": lambda r: True, "PROTOCOL_VERSION": 2,
          "StatusRequestCount": 0, "StatusLastCommand": "",
          "StatusTotalAltiumMs": 0, "LastPingMs": 0, "GetTickCount": 10,
          "SetInFlight": lambda *a: seen["set_in_flight"].append(a),
          "CurrentFocusedDocPath": lambda d: "X", "ResetNextStep": lambda d: None,
          "StartProgress": lambda *a: seen["start_progress"].append(a),
          "ProcessCommand": process, "RecordCaughtException": record,
          "BuildErrorResponse": lambda rid, code, msg: json.dumps(
              {"error": {"code": code, "message": msg}}),
          "CommandIsReadOnly": lambda c: True,
          "InvalidateCompileCache": lambda d: None,
          "PendingNextStep": lambda d: "",
          "AppendEnvelopeField": lambda r, f: r,
          "JsonObj": lambda b: "{" + b + "}", "JsonStr": lambda k, v: "",
          "WriteResponseFile": lambda rid, c: seen["written"].append(c),
          "EndProgress": lambda rid: None, "AppendLog": lambda line: None,
          "FormatLogStamp": lambda d: "T",
          "AppendLogLine": lambda *a: None, "ResetInFlight": lambda d: None,
          "CurrentCommand": "", "CurrentRequestId": ""}
    build(_form(), ["ProcessSingleRequest"], ns)
    assert ns["ProcessSingleRequest"](0) is True
    return ns, seen


def test_the_dispatcher_names_the_call_before_the_handler_and_counts_its_raise():
    def raises():
        raise PascalError("handler raised")

    ns, seen = _dispatch(raises)
    assert seen["during"] == ("application.save_all", "req1")
    assert seen["caught"] == [("dispatcher", "application.save_all", "req1")]
    assert seen["set_in_flight"] == [("application.save_all", "req1")]
    assert seen["start_progress"] == [("req1", "application.save_all")]
    assert "INTERNAL_ERROR" in seen["written"][-1]
    assert ns["CurrentCommand"] == "" and ns["CurrentRequestId"] == ""


def test_a_clean_handler_records_nothing():
    ns, seen = _dispatch(lambda: '{"success":true}')
    assert seen["caught"] == []
    assert seen["written"] == ['{"success":true}']
    assert ns["CurrentCommand"] == ""


def test_the_pump_starts_each_session_with_nothing_counted():
    body = _form()["StartMCPPump"]
    i = body.index("InitDefaultConfig(0)")
    j = body.index("LoadMCPConfig(0)")
    window = body[i:j]
    assert "ResetCaughtExceptions(0)" in window
    assert re.search(r"CastErrorCount\s*:=\s*0\s*;", window)


# ---------------------------------------------------------------------------
# SelfTest: stop_server is not run live; its branch is checked here instead
# ---------------------------------------------------------------------------

def test_selftest_no_longer_runs_stop_server_live():
    tests = load(_ALTIUM / "SelfTest.pas", minimum=15)
    for name, body in tests.items():
        assert "'stop_server'" not in body, (
            f"SelfTest {name} still runs stop_server: SaveAllDirty writes "
            f"every open document and can meet a modal prompt")


def test_the_stop_server_branch_saves_and_stops():
    """The Case branch, executed: the offline twin of the removed live step."""
    body = _app()["HandleApplicationCommand"]
    m = re.search(r"'stop_server'\s*:\s*(Begin\b.*?\bEnd)\s*;", body, re.S)
    assert m, "stop_server branch not found in HandleApplicationCommand"
    src = ("Function StopServerBranch(RequestId : String) : String;\n"
           "Begin\n" + m.group(1) + ";\nEnd;")
    saved: list = []
    ns = {"SaveAllDirty": lambda d: saved.append(d), "Running": True,
          "BuildSuccessResponse": lambda rid, data: rid + "|" + data}
    build({"StopServerBranch": src}, ["StopServerBranch"], ns)
    assert ns["StopServerBranch"]("r1") == 'r1|{"stopped":true}'
    assert saved == [0] and ns["Running"] is False


# ---------------------------------------------------------------------------
# Python: ping schema, app_ping, status, stuck-handler guidance
# ---------------------------------------------------------------------------

def test_the_ping_schema_keeps_the_new_fields():
    from eda_agent.schemas.commands import PingResponse
    r = PingResponse(pong=True, script_version="s", protocol_version=2,
                     caught_exceptions=2,
                     last_caught={"where": "dispatcher"})
    assert r.caught_exceptions == 2
    assert r.last_caught == {"where": "dispatcher"}
    assert PingResponse(pong=True, script_version="s",
                        protocol_version=2).caught_exceptions == 0


def _tools():
    from eda_agent.tools import application as app_mod
    from eda_agent.tools.registry import ToolRegistry
    registry = ToolRegistry()
    app_mod.register_application_tools(registry)
    return app_mod, {t.name: t.fn for t in asyncio.run(registry.list_tools())}


def test_app_ping_passes_the_counters_through(monkeypatch):
    app_mod, tools = _tools()

    class _B:
        def is_altium_running(self):
            return True

        def ping_with_version(self):
            return {"pong": True, "script_version": "x", "cast_errors": 0,
                    "caught_exceptions": 4,
                    "last_caught": {"where": "dispatcher"}}

    monkeypatch.setattr(app_mod, "get_bridge", lambda: _B())
    out = asyncio.run(tools["app_ping"]())
    assert out["caught_exceptions"] == 4
    assert out["last_caught"] == {"where": "dispatcher"}
    assert out["cast_errors"] == 0


def test_app_ping_has_the_keys_when_altium_is_down(monkeypatch):
    app_mod, tools = _tools()

    class _B:
        def is_altium_running(self):
            return False

    monkeypatch.setattr(app_mod, "get_bridge", lambda: _B())
    out = asyncio.run(tools["app_ping"]())
    assert out["caught_exceptions"] is None and out["last_caught"] is None


def _progress(ws: Path, rid: str, payload, age: float):
    import os
    import time
    p = ws / f"progress_{rid}.json"
    p.write_text(payload if isinstance(payload, str) else json.dumps(payload),
                 encoding="utf-8")
    t = time.time() - age
    os.utime(p, (t, t))


def test_read_in_flight_names_the_commands_newest_first(tmp_path):
    from eda_agent.bridge.inflight import read_in_flight
    assert read_in_flight(tmp_path) == []
    _progress(tmp_path, "old1", {"started_ms": 5}, age=30)
    _progress(tmp_path, "new2", {"started_ms": 9, "command": "pcb.get_nets",
                                 "request_id": "new2"}, age=2)
    _progress(tmp_path, "torn3", "{not json", age=10)
    got = read_in_flight(tmp_path)
    assert [g["request_id"] for g in got] == ["new2", "torn3", "old1"]
    assert got[0]["command"] == "pcb.get_nets"
    assert got[1]["command"] == "" and got[2]["command"] == ""
    assert 1.0 <= got[0]["age_s"] <= 10.0
    assert read_in_flight(tmp_path / "missing") == []


def test_read_last_caught_takes_the_last_caught_row(tmp_path):
    from eda_agent.bridge.inflight import read_last_caught
    assert read_last_caught(tmp_path) is None
    (tmp_path / "activity.log").write_text(
        "T1,5,application.ping,OK,10,{}\n"
        "T2,0,_caught_exception,ReadMechKind:Kind,pcb.get_layers,r1\n"
        "T3,0,_caught_exception,dispatcher,app.save_all,r2\n"
        "T4,7,application.ping,OK,10,{}\n", encoding="utf-8")
    assert read_last_caught(tmp_path) == {
        "at": "T3", "where": "dispatcher", "command": "app.save_all",
        "request_id": "r2"}


def test_status_reports_what_is_in_flight_and_the_last_catch(tmp_path):
    from unittest.mock import patch
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.bridge.process_manager import AltiumSelection
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    _progress(tmp_path, "rq", {"command": "application.save_all",
                               "request_id": "rq"}, age=4)
    (tmp_path / "activity.log").write_text(
        "T9,0,_caught_exception,dispatcher,application.save_all,rq\n",
        encoding="utf-8")
    bridge = AltiumBridge()
    with patch.object(bridge.process_manager, "select_altium_process",
                      return_value=AltiumSelection(None, [], "none")):
        status = bridge.get_altium_status()
    assert status["in_flight"][0]["command"] == "application.save_all"
    assert status["last_caught"]["where"] == "dispatcher"


def test_the_stuck_handler_guidance_names_the_call_and_the_dialog():
    from eda_agent.bridge.recovery import (
        STUCK_HANDLER, recovery_guidance, recovery_message)
    plain = recovery_guidance(STUCK_HANDLER)
    text = plain["diagnosis"] + " " + " ".join(plain["steps"])
    assert "dialog" in text and "INTERNAL_ERROR" in text
    assert "press OK" in text
    flying = {"command": "application.save_all", "request_id": "abcdef0123"}
    named = recovery_guidance(STUCK_HANDLER, flying)
    assert "application.save_all" in named["diagnosis"]
    assert "abcdef01" in named["diagnosis"]
    assert named["in_flight"]["command"] == "application.save_all"
    msg = recovery_message(STUCK_HANDLER, flying)
    assert "application.save_all" in msg
    for step in named["steps"]:
        assert step in msg


def test_the_stuck_handler_timeout_names_the_command_in_flight(
        tmp_path, monkeypatch):
    """The whole path: a heartbeat that never ends, extensions exhausted,
    and the error the caller reads names the command from the heartbeat."""
    from eda_agent.bridge import altium_bridge as ab
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    monkeypatch.setattr(ab, "_MAX_HEARTBEAT_EXTENSIONS", 1)
    bridge = ab.AltiumBridge()
    monkeypatch.setattr(bridge, "_dialog_probe", lambda: None)
    monkeypatch.setattr(bridge, "_note_fault", lambda ws, g: None)
    rid = "stuck01"
    _progress(tmp_path, rid, {"command": "application.save_all",
                              "request_id": rid}, age=0)
    with pytest.raises(AltiumTimeoutError) as exc:
        bridge._poll_loop(rid, tmp_path / f"response_{rid}.json",
                          tmp_path / f"progress_{rid}.json", tmp_path,
                          0.01, 0.0, 0.05, 0.0, None)
    assert "application.save_all" in str(exc.value)
    assert exc.value.details["recovery"]["in_flight"]["request_id"] == rid


# ---------------------------------------------------------------------------
# RELEASE_VERIFICATION section 39: every counted site has a live probe
# ---------------------------------------------------------------------------

#: Every unit is held to the rule (widened at integration, once the
#: per-file stages' sites filled section 39.4).
_COUNTED_UNITS = tuple(sorted(
    p.name for p in _ALTIUM.glob("*.pas") if p.name != "Altium_MCP.pas"))

#: A literal ``where``, or the literal PREFIX of one built at run time
#: (``'GetSchProperty:' + PropName + ...``): group 2 is the ``+``.
_WHERE = re.compile(r"RecordCaughtException\('([^']+)'(\s*\+)?")


def _section_39() -> str:
    doc = (_ROOT / "docs" / "RELEASE_VERIFICATION.md").read_text(
        encoding="utf-8")
    m = re.search(r"(?ms)^## 39\. Popup-free run.*?(?=^## )", doc)
    assert m, "RELEASE_VERIFICATION.md has no section 39 'Popup-free run'"
    return m.group(0)


def _counted_wheres() -> tuple[set[str], set[str]]:
    exact, prefixes = set(), set()
    for unit in _COUNTED_UNITS:
        code = strip_comments((_ALTIUM / unit).read_text(
            encoding="utf-8-sig", errors="replace"))
        for m in _WHERE.finditer(code):
            (prefixes if m.group(2) else exact).add(m.group(1))
    return exact, prefixes


def test_every_counted_site_has_a_live_probe_in_section_39():
    sec = _section_39()
    exact, prefixes = _counted_wheres()
    assert "dispatcher" in exact and len(exact) >= 40, exact
    assert len(prefixes) >= 6, prefixes
    assert len(_COUNTED_UNITS) >= 11, _COUNTED_UNITS
    missing = sorted(w for w in exact if f"`{w}`" not in sec)
    # A run-time where is probed by a row naming its literal prefix inside a
    # code span, e.g. `GetSchProperty:<prop>:<id>`.
    missing += sorted(w + "..." for w in prefixes if f"`{w}" not in sec)
    assert not missing, (
        f"these counted sites have no probe row in RELEASE_VERIFICATION "
        f"section 39: {missing}")
    assert "P8+" not in sec and "INTEGRATE" not in sec, \
        "the integration placeholder is still in section 39.4"
    assert "caught_exceptions" in sec and "in_flight" in sec


def test_the_dashboard_shows_a_caught_exception_row():
    """RecordCaughtException's row has a command where the byte count goes,
    so the command-line pattern dropped it and the dashboard never showed
    the one line that says a modal fired."""
    from eda_agent.web.dashboard import LogEntry, _aggregate, _parse_line
    row = ("2026-10-11 10:00:00.123,0,_caught_exception,"
           "App_ExitActiveCommand:PCBServer.PostProcess,"
           "application.exit_active_command,abcdef0123")
    e = _parse_line(row)
    assert isinstance(e, LogEntry)
    assert e.severity() == "error"
    assert e.command == "_caught_exception"
    assert e.request_id == "abcdef01"
    assert "App_ExitActiveCommand:PCBServer.PostProcess" in e.error_msg
    assert "application.exit_active_command" in e.error_msg
    real = _parse_line("2026-10-11 10:00:00.200,40,"
                       "application.exit_active_command,OK,12,{}")
    stats = {r["command"]: r for r in _aggregate([e, real])}
    assert stats["application.exit_active_command"]["n"] == 1
