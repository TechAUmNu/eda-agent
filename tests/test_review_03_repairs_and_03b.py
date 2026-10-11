# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Review of the 2026.10.11.03 repair commits (c0b8045, 407c33f) and of the
.02b-run fix (498e5ca, N1-N5). Each test EXECUTES the real Pascal
(tests/pascal_eval.py) or the real bridge, and fails on 498e5ca for the
reason named; the rest are guards on the same paths.

1. PCB_NormalizeVias called Board.FindDominantRuleForObject - the ONE
   identifier on the path that has never run (P23) - INSIDE the
   PCBServer.PreProcess window: a halt there is fatal class (1) AND leaves
   PreProcess open with the Finally skipped. The lookup now runs in a
   read-only pass before PreProcess, only when some rule gave values.
2. ViaRuleValue put no ceiling on the number before Round: a huge
   descriptor number overflows MilsToCoordF / MMToCoord outside any Try.
3. PCB_NormalizeVias marked the board modified on every run, changed 0
   included.
4. The heartbeat-limit timeout recorded dead_loop for a script_fatal box
   but raised with stuck_handler's recovery text and details.
5. obj_create / obj_batch_create container=component flagged nothing and
   redrew nothing (SchDoc stays Nil on that route).
6. lib_link_3d_model said the empty body was "taken off the footprint
   again" - in memory only; the library board still holds the copy that
   was measured to come back on save (2026-10-04).
7. N2's ALLOW-list dropped every other document kind from the save walks
   (written, listed, counted): a dirty .BomDoc was left unwritten,
   app_save_all counted still_dirty 0, and proj_close save=true closed
   with it dirty - Altium's own save prompt. Now a script-unit DENY test.
8. proj_close save=true never checked what still read modified before
   CloseObject (save=false always did).
9. bridge_record_pending was the whole workspace's record, not the
   project's; proj_close read it before the close let go of its members.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from tests.live_2026_10_11 import _caught, _with
from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments
from tests.test_bridge_record_names_one_file import (
    SCRATCH, SHEET, Listed, ServerDoc, World, _ALTIUM, _key)
from tests.test_live_editor_component_is_never_read import (
    A, BaseRule, Board, EditLib, GenLib, NComp, _GENERIC, _IList,
    _StrListClass, _client, _gen_ns, _via)

_PCB = load(_ALTIUM / "PCB.pas", minimum=40)
_LIBRARY = load(_ALTIUM / "Library.pas", minimum=40)
_PROJECT = load(_ALTIUM / "Project.pas", minimum=40)

_DESC = ("Routing Via (PreferredHoleWidth=10mil) (MinWidth=20mil) "
         "(PreferedWidth=20mil) (All)")
_TEMPLATE = "Routing Via (Templates Used To Check Via: v30h10m0mx0) (All)"


# ---------------------------------------------------------------------------
# 1-3. PCB_NormalizeVias / ViaRuleValue
# ---------------------------------------------------------------------------

class _Halt(AssertionError):
    """The engine's 'Undeclared identifier' halt: no translated Except
    catches an AssertionError, and the Finally the engine would skip still
    runs here - so the test reads the server's state AT the call."""


def _vias(board, dominant_halts=False):
    state = {"open": 0, "calls_inside": 0, "calls": 0}
    marked: list = []
    msgs: list = []

    class Server:
        @property
        def PreProcess(self):
            state["open"] += 1

        @property
        def PostProcess(self):
            state["open"] -= 1

        def SendMessageToRobots(self, *a):
            msgs.append(a[2])

    real = board.FindDominantRuleForObject

    def dominant(via, kind):
        state["calls"] += 1
        if state["open"]:
            state["calls_inside"] += 1
        if dominant_halts:
            raise _Halt("Undeclared identifier: FindDominantRuleForObject")
        return real(via, kind)

    board.FindDominantRuleForObject = dominant
    ns = {
        "GetPCBBoardAnywhere": lambda d: board,
        "BuildErrorResponse": lambda rid, code, msg: ("ERR", code, msg),
        "BuildSuccessResponse": lambda rid, data: ("OK", json.loads(data)),
        "MarkDocDirtyByPath": marked.append,
        "RecordCaughtException": lambda w: None,
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
    build(_PCB, ["ViaRuleValue", "PCB_NormalizeVias"], ns)
    return ns, state, marked, msgs


def test_the_unproven_rule_lookup_never_runs_inside_preprocess():
    """Review: the repair passed the literal 11 but left the call under
    PCBServer.PreProcess, against the sweep's own rule."""
    vias = [_via(n) for n in range(3)]
    board = Board(vias, [BaseRule("RoutingVias", _DESC)],
                  lambda v: BaseRule("RoutingVias"))
    ns, state, marked, _ = _vias(board)
    reply = ns["PCB_NormalizeVias"]({}, "r")
    assert reply[0] == "OK" and reply[1]["changed"] == 3
    assert state["calls"] == 3
    assert state["calls_inside"] == 0, (
        "FindDominantRuleForObject ran with PCBServer.PreProcess open")
    assert state["open"] == 0


def test_a_halt_on_the_rule_lookup_leaves_no_preprocess_open():
    """On the engine a halt skips Finally: a PreProcess opened before the
    call would stay open. Here nothing may have been opened when it halts."""
    vias = [_via(n) for n in range(2)]
    board = Board(vias, [BaseRule("RoutingVias", _DESC)],
                  lambda v: BaseRule("RoutingVias"))
    ns, state, _, msgs = _vias(board, dominant_halts=True)
    with pytest.raises(_Halt):
        ns["PCB_NormalizeVias"]({}, "r")
    assert state["calls_inside"] == 0
    assert msgs == [], "a via was modified before the unproven call ran"


def test_a_template_descriptor_board_never_makes_the_unproven_call():
    vias = [_via(n) for n in range(2)]
    board = Board(vias, [BaseRule("RoutingVias", _TEMPLATE)],
                  lambda v: BaseRule("RoutingVias"))
    ns, state, marked, msgs = _vias(board, dominant_halts=True)
    reply = ns["PCB_NormalizeVias"]({}, "r")
    assert reply[1] == {"checked": 2, "changed": 0, "free": 2,
                        "no_rule_values": 2, "via_rule_values": 0}
    assert state["calls"] == 0 and msgs == []


@pytest.mark.parametrize("vias,rules,label", [
    ([], [BaseRule("RoutingVias", _DESC)], "no vias at all"),
    ([SimpleNamespace(InComponent=True, InPolygon=False, InDimension=False,
                      I_ObjectAddress="V0", Size=1, HoleSize=1)],
     [BaseRule("RoutingVias", _DESC)], "only a component via"),
    ([_via(0)], [BaseRule("RoutingVias", _TEMPLATE)], "template descriptor"),
    ([_via(0)], [BaseRule("RoutingVias", _DESC)], "rule not in the cache"),
])
def test_a_run_that_changes_nothing_does_not_mark_the_board(vias, rules, label):
    board = Board(vias, rules, lambda v: BaseRule("SomeOtherRule"))
    ns, _, marked, _ = _vias(board)
    reply = ns["PCB_NormalizeVias"]({}, "r")
    assert reply[1]["changed"] == 0, label
    assert marked == [], f"{label}: an untouched board was marked modified"


def test_a_run_that_changes_a_via_marks_the_board():
    board = Board([_via(0)], [BaseRule("RoutingVias", _DESC)],
                  lambda v: BaseRule("RoutingVias"))
    ns, _, marked, _ = _vias(board)
    assert ns["PCB_NormalizeVias"]({}, "r")[1]["changed"] == 1
    assert marked == ["b.PcbDoc"]


def _via_value_ns():
    seen: list = []

    def guarded(scale):
        def conv(v):
            seen.append(v)
            if v * scale > 2 ** 31:
                raise _Halt("EInvalidOp: Round overflow (the IDE modal)")
            return round(v * scale)
        return conv

    ns = {"MilsToCoordF": guarded(10000), "MMToCoord": guarded(1e7 / 25.4),
          "StrToFloatDef": lambda s, d: float(s) if s else d}
    build(_PCB, ["ViaRuleValue"], ns)
    return ns, seen


@pytest.mark.parametrize("desc,key,want", [
    ("(PreferedWidth=99999999999999999mil)", "PreferedWidth", 0),
    ("(PreferedWidth=1000000000000000000000mm)", "PreferedWidth", 0),
    ("(PreferedWidth=10001mil)", "PreferedWidth", 0),       # past 10000 mil
    ("(PreferedWidth=254.5mm)", "PreferedWidth", 0),        # past 254 mm
    ("(PreferedWidth=1234567890123mil)", "PreferedWidth", 0),  # 13 chars
    ("(PreferedWidth=10000mil)", "PreferedWidth", 100000000),  # the ceiling
    ("(PreferedWidth=254mm)", "PreferedWidth", 100000000),
    ("(PreferedWidth=20mil)", "PreferedWidth", 200000),
])
def test_via_rule_value_never_hands_round_an_overflowing_number(desc, key, want):
    """Review: IsFloatStr-style parsing accepts magnitudes far past what
    Round can take; the EInvalidOp would be the IDE's modal, outside any Try."""
    ns, seen = _via_value_ns()
    assert ns["ViaRuleValue"](desc, key) == want
    assert all(v <= 10000 for v in seen)


# ---------------------------------------------------------------------------
# 4. The heartbeat-limit timeout names ONE fault kind
# ---------------------------------------------------------------------------

def _bridge(tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    return AltiumBridge()


def _heartbeat_timeout(tmp_path, kinds):
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    from eda_agent.bridge.fault_state import read_fault
    bridge = _bridge(tmp_path)
    bridge._dialog_probe = lambda: {
        "blocked": True, "dialog_count": 1, "kinds": kinds,
        "summary": f"{kinds[0]}: 'Error' says 'Undeclared identifier'"}
    progress = tmp_path / "progress_x.json"
    progress.write_text("{}", encoding="utf-8")
    start = time.monotonic()
    with pytest.raises(AltiumTimeoutError) as err:
        bridge._poll_loop("r" * 32, tmp_path / "response_x.json", progress,
                          tmp_path, 0.0005, start, 0.0005, start, None)
    return err.value, read_fault(tmp_path)


def test_a_script_fatal_box_at_the_heartbeat_limit_raises_dead_loop_guidance(tmp_path):
    """Review: last_fault said dead_loop while the caller's own error named
    stuck_handler's recovery ("stuck in a long operation")."""
    err, fault = _heartbeat_timeout(tmp_path, ["script_fatal"])
    assert fault["recovery"]["fault"] == "dead_loop"
    assert err.details["recovery"]["fault"] == "dead_loop"
    assert "stuck in a long operation" not in str(err)
    assert "HALTED" in str(err)


def test_an_ordinary_box_at_the_heartbeat_limit_stays_stuck_handler(tmp_path):
    err, fault = _heartbeat_timeout(tmp_path, ["confirm"])
    assert fault["recovery"]["fault"] == "stuck_handler"
    assert err.details["recovery"]["fault"] == "stuck_handler"
    assert "stuck in a long operation" in str(err)


# ---------------------------------------------------------------------------
# 5. container=component creates flag the library and redraw the symbol
# ---------------------------------------------------------------------------

def test_obj_create_into_a_component_flags_the_library_and_redraws_it():
    events: list[str] = []
    named = NComp("X", events)
    lib = EditLib(A, comps=[named], events=events)
    client = _client()
    ns = _gen_ns(lib, client)
    build(_GENERIC, ["Gen_CreateObject"], ns)
    reply = ns["Gen_CreateObject"](
        {"object_type": "eLine", "properties": "X=1",
         "container": "component", "component_name": "X"}, "r")
    assert reply[0] == "OK", reply
    assert client.docs[A.upper()].modified >= 1, (
        "the library still reads clean after the create")
    assert "invalidate:X" in events[events.index("post"):], (
        "the edited symbol was not redrawn after the transaction")


def _batch(queue, comps=(), name="X"):
    events: list[str] = []
    named = [NComp(c, events) for c in comps]
    lib = GenLib(A, comps=named, events=events)
    client = _client()
    ns = _gen_ns(lib, client)
    q = list(queue)
    ns["NextBatchOp"] = lambda _r: q.pop(0) if q else ""
    build(_GENERIC, ["Gen_BatchCreate"], ns)
    params = {"operations": "q"}
    if name:
        params["component_name"] = name
    return ns["Gen_BatchCreate"](params, "r"), events, client


def test_obj_batch_create_into_a_component_flags_the_library_once():
    line = {"object_type": "eLine", "properties": "X=1",
            "container": "component"}
    reply, events, client = _batch([line, dict(line)], comps=["X"])
    assert reply[0] == "OK" and '"created":2' in reply[1]
    assert client.docs[A.upper()].modified >= 1
    assert events[events.index("post"):].count("invalidate:X") == 1


def test_obj_batch_create_with_nothing_created_in_a_component_flags_nothing():
    line = {"object_type": "eLine", "properties": "X=1",
            "container": "component"}
    reply, events, client = _batch([line], comps=[], name="")
    assert '"created":0' in reply[1]
    assert client.docs[A.upper()].modified == 0
    assert not any(e.startswith("invalidate:") for e in events)


# ---------------------------------------------------------------------------
# 6. lib_link_3d_model's empty-model reply never claims the FILE is clean
# ---------------------------------------------------------------------------

class _Body:
    def __init__(self):
        self.OverallHeight = 0
        self.I_ObjectAddress = 4242
        self.ObjectId = 12


class _GrpIter:
    def __init__(self, items):
        self.items, self.i = list(items), 0

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


def _snip():
    body = strip_comments(_LIBRARY["Lib_Link3DModel"])
    start = body.index("Body3D_ReadRect(Body, X1, Y1, X2, Y2, HaveRect);")
    start = body.index(";", start) + 1
    end = body.index("If AlignStr = 'none' Then", start)
    return ("Procedure Snip(Body : Integer; Footprint : Integer; ModelPath : String;"
            " Replace : Boolean; RemovedDistinct : Integer; HaveRect : Boolean;"
            " PcbLib : Integer);\nBegin\n" + body[start:end] + "\nEnd;")


def _link_empty(current=True, board_lists=1):
    from tests.pascal_eval import translate
    body = _Body()
    fp = _Footprint([body])
    board_reads: list = []
    lib = SimpleNamespace(CurrentComponent=fp if current else object(),
                          Board=object())

    def still(board, entries):
        board_reads.append(entries)
        return board_lists

    ns = {"PCBServer": SimpleNamespace(SendMessageToRobots=lambda *a: None),
          "c_Broadcast": 0, "PCBM_BeginModify": 0, "PCBM_EndModify": 0,
          "c_NoEventData": 0, "eComponentBodyObject": 12, "MkSet": lambda *a: a,
          "Failure": "", "FailCode": "", "LibBoardStillListsCount": still}
    _caught(ns)
    build(_LIBRARY, ["LibAddRemovalEntry"], ns)
    build({"R": _LIBRARY["RemoveLibBodyByAddress"].replace(
        "Var Entries : String", "Entries : String")}, ["R"], ns)
    src = translate(_snip())[1]
    exec(compile(src, "<pascal Snip>", "exec"), ns)  # noqa: S102
    ns["Snip"](body, fp, r"C:\scratch\stub.igs", False, 0, False, lib)
    return ns, fp, body, board_reads


def test_the_empty_body_removal_is_reported_as_in_memory_only():
    """Review: 'The empty body was taken off the footprint again' while the
    library board still holds the copy measured to come back on save."""
    ns, fp, body, reads = _link_empty(current=True, board_lists=1)
    msg = ns["Failure"]
    assert ns["FailCode"] == "MODEL_LOAD_FAILED"
    assert body not in fp.prims
    assert "IN MEMORY only" in msg
    assert "board_still_lists=1" in msg
    assert "Do NOT save this library" in msg
    assert "lib_reload_library" in msg
    assert "taken off the footprint again." not in msg
    # One read of the board (the evaluator passes RemoveLibBodyByAddress's
    # Var Entries by value, so the entry text itself is not visible here).
    assert len(reads) == 1


def test_the_board_count_is_not_read_when_the_footprint_is_not_current():
    ns, _fp, _body, reads = _link_empty(current=False)
    assert "board_still_lists=null" in ns["Failure"]
    assert reads == [], "the board was walked for a footprint that is not current"


# ---------------------------------------------------------------------------
# 7. The save walks skip script units only: every other kind is written
# ---------------------------------------------------------------------------

BOM = r"H:\git\multiviewer\hardware\multiviewer\carrier.BomDoc"
DWF = r"H:\git\multiviewer\hardware\multiviewer\carrier.PcbDwf"
LIBPAS = r"C:\Users\user\EDA Agent\scripts\Library.pas"


def _bom_world():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.bom = ServerDoc(w.disk, BOM)
    w.disk.add(BOM)
    w.client.open.append(w.bom)
    w.project.docs.append(Listed(BOM, "BOMDOC"))
    _with(w, ["IsScriptUnitDoc", "IsSaveableDesignDoc", "SaveOneDocByDocRef",
              "SaveProjectMembers", "SaveAllDirty", "CountDirtyInProject",
              "CountDirtyDocuments", "WorkspaceDocPaths"])
    return w


def test_a_dirty_bom_document_is_still_written_by_save_all():
    w = _bom_world()
    w.bom.Modified = True
    w.save_all(only_modified=True)
    assert w.bom.saves == 1, "a dirty ActiveBOM document was left unwritten"


def test_a_dirty_bom_document_still_counts_in_still_dirty():
    w = _bom_world()
    w.bom.Modified = True
    assert w.ns["CountDirtyDocuments"](0) == 1, (
        "app_save_all would answer still_dirty 0 with a dirty document open")
    assert _key(BOM) in w.ns["WorkspaceDocPaths"](0).upper()


@pytest.mark.parametrize("kind,path,saveable", [
    ("SCH", SHEET, True), ("PCB", r"C:\x\b.PcbDoc", True),
    ("BOMDOC", BOM, True), ("", BOM, True), ("PCBDWF", DWF, True),
    ("HARNESS", r"C:\x\a.Harness", True), ("TEXT", r"C:\x\notes.txt", True),
    ("EditScriptDS", LIBPAS, False), ("TEXT", r"C:\x\notes.pas", False),
    ("", r"C:\x\form.dfm", False), ("", r"C:\x\a.vbs", False),
    ("", r"C:\x\a.js", False), ("", r"C:\x\a.bas", False),
    ("", r"C:\x\a.tcl", False),
])
def test_only_a_script_unit_is_kept_out_of_the_save_walks(kind, path, saveable):
    w = _bom_world()
    assert w.ns["IsSaveableDesignDoc"](Listed(path, kind)) is saveable


# ---------------------------------------------------------------------------
# 8-9. proj_close save=true: refused while anything reads modified; the
#      record pending is this project's, read after the close
# ---------------------------------------------------------------------------

class _Prompt(AssertionError):
    """Altium's own 'Save changes?' box: no Except catches it."""


def _close_world(extra_member=None):
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    ws = w.ns["GetWorkspace"]
    ws.DM_FocusedProject = w.project
    closes: list = []
    if extra_member is not None:
        path, kind = extra_member
        w.extra = ServerDoc(w.disk, path)
        w.disk.add(path)
        w.client.open.append(w.extra)
        w.project.docs.append(Listed(path, kind))

    def find(_ws, p):
        for proj in ws.projects:
            if _key(proj.DM_ProjectFullPath) == _key(p):
                return proj
        return None

    def run_process(name):
        if name != "WorkspaceManager:CloseObject":
            return
        for doc in w.project.docs:
            sd = w.client.GetDocumentByPath(doc.DM_FullPath)
            if sd is not None and sd.Modified:
                raise _Prompt(f"Save changes to {doc.DM_FullPath}?")
        closes.append(w.project.DM_ProjectFullPath)
        ws.projects = [p for p in ws.projects if p is not w.project]
        w.client.open = [d for d in w.client.open
                         if _key(d.FileName) not in
                         {_key(x.DM_FullPath) for x in w.project.docs}]

    w.ns.update(
        FindProjectByPath=find,
        FocusedProjectPathIs=lambda p: True,
        ResetParameters=lambda: None, AddStringParameter=lambda k, v: None,
        RunProcess=run_process,
        ExtractJsonValue=lambda p, k: (lambda v: "" if v is None else
                                       (str(v).lower() if isinstance(v, bool)
                                        else str(v)))(json.loads(p).get(k)),
        LowerCase=str.lower,
        EscapeJsonString=lambda s: json.dumps(s)[1:-1],
        BoolToJsonStr=lambda b: "true" if b else "false",
        PipeListToJsonArray=lambda s: json.dumps([e for e in s.split("|") if e]),
        BuildSuccessResponse=lambda rid, data: json.loads(data),
        BuildErrorResponse=lambda rid, code, msg: {"error": code})
    _with(w, ["IsScriptUnitDoc", "IsSaveableDesignDoc", "SaveOneDocByDocRef",
              "SaveProjectMembers", "AgesForPaths", "CountChangedAges",
              "BridgeRecordKeysIn"])
    build(_PROJECT, [n for n in (
        "DirtyProjectMembers", "ClearDocModifiedByPath", "DiscardProjectMembers",
        "PipePathsToJsonArray", "OpenProjectPaths", "PathsGoneOtherThan",
        "FocusProjectForClose", "SaveProjectMembersReport",
        "ProjectRecordPendingJson", "Proj_Save", "Proj_Close")
        if n in _PROJECT], w.ns)
    w.closes = closes
    return w


_FIELDS = ("only_modified", "documents_attempted", "documents_written",
           "skipped_unmodified", "bridge_record_pending")


def test_proj_close_save_true_closes_and_carries_the_counters():
    w = _close_world()
    w.sheet.Modified = True
    reply = w.ns["Proj_Close"](json.dumps({"save": True}), "r")
    assert reply["success"] is True and reply["closed"] is True
    for field in _FIELDS:
        assert field in reply, field
    assert reply["documents_attempted"] == 1 and reply["documents_written"] == 1
    assert reply["bridge_record_pending"] == []


def test_proj_close_save_true_refuses_while_a_member_still_reads_modified():
    """A declined save left the sheet dirty: the close must not reach
    CloseObject (whose save prompt blocks the loop)."""
    w = _close_world()
    w.sheet.Modified = True

    def declined(_kind):
        w.sheet.saves += 1                     # the editor refused it

    w.sheet.DoFileSave = declined
    reply = w.ns["Proj_Close"](json.dumps({"save": True}), "r")
    assert w.closes == []
    assert reply["success"] is False and reply["closed"] is False
    assert [_key(p) for p in reply["still_modified"]] == [_key(SHEET)]
    for field in _FIELDS:
        assert field in reply, field


def test_proj_close_save_true_with_a_dirty_bom_member_writes_it_and_closes():
    """Review of 498e5ca: under the allow-list the .BomDoc was skipped by the
    save and CloseObject then raised Altium's prompt."""
    w = _close_world(extra_member=(BOM, "BOMDOC"))
    w.extra.Modified = True
    reply = w.ns["Proj_Close"](json.dumps({"save": True}), "r")
    assert w.extra.saves == 1
    assert reply["closed"] is True


def test_proj_close_only_modified_refuses_a_skipped_member_that_reads_dirty():
    """only_modified skips clean-reading members; one that reads modified but
    whose save is declined is still refused, never closed into a prompt."""
    w = _close_world(extra_member=(LIBPAS, "EditScriptDS"))
    w.extra.Modified = True                    # a script unit is never written
    reply = w.ns["Proj_Close"](json.dumps({"save": True,
                                           "only_modified": True}), "r")
    assert w.closes == [] and w.extra.saves == 0
    assert reply["closed"] is False
    assert [_key(p) for p in reply["still_modified"]] == [_key(LIBPAS)]


def test_bridge_record_pending_names_this_project_only():
    """Review: proj_save of the carrier project listed the free scratch
    sheet's pending edit (BridgeRecordKeys(0) = the workspace's record)."""
    w = _close_world()
    w.ns["RecordBridgeEdit"](SCRATCH)          # a free document, not a member
    reply = w.ns["Proj_Save"](json.dumps({}), "r")
    assert _key(SCRATCH) not in [_key(p) for p in reply["bridge_record_pending"]]


def test_bridge_record_pending_lists_a_member_the_save_left_unwritten():
    w = _close_world()
    w.sheet.Modified = True
    w.ns["RecordBridgeEdit"](SHEET)

    def declined(_kind):
        w.sheet.saves += 1

    w.sheet.DoFileSave = declined
    reply = w.ns["Proj_Save"](json.dumps({}), "r")
    assert [_key(p) for p in reply["bridge_record_pending"]] == [_key(SHEET)]


def test_proj_close_reads_the_pending_list_after_the_close():
    """A member entry the close let go of (ForgetBridgeEditsIfClosed) is not
    reported as still pending."""
    w = _close_world()
    w.ns["RecordBridgeEdit"](SCRATCH)
    w.sheet.Modified = True
    w.ns["RecordBridgeEdit"](SHEET)
    reply = w.ns["Proj_Close"](json.dumps({"save": True}), "r")
    assert reply["closed"] is True
    assert reply["bridge_record_pending"] == []
