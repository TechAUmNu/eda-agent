# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Re-review of a0c9da9 (the dirty mark and the bridge record).

Each test EXECUTES the real Pascal on the fixture workspace of
tests/test_bridge_record_names_one_file.py and fails on a0c9da9:

1. Under a subst drive the project lists F:\\ while the open document's
   FileName is H:\\. DocNameCount keyed project members by their LISTING
   only, so IsProjectMemberFile (which compares FileName keys) let the
   focused carrier sheet answer a bare request for its free copy - the
   c8ee69e defect back: a bare mark marked the sheet, and a hand-edited
   copy was skipped as unmodified.
2. FindOpenServerDoc's absolute walk accepted any listed document's
   lookup answer on its LISTING alone, so the very stranger (empty or bare
   FileName) the exact lookup rejected came back via 'walk'; and
   ListedServerDoc's absolute branch had no check at all, so app_save_all
   wrote the stranger and filed it under the member's path.
3. Proj_SetDocumentParameter edits the document its RAW file_path
   resolves to and marked that raw path; for a bare name naming a project
   member the mark (which answers bare names for free documents only) was
   lost silently. It now marks the resolved sheet's own name, and refuses
   an ambiguous bare name before editing.
4. Seven sheet_path handlers in Generic.pas marked with a raw
   GetDocumentByPath(SchDoc.DocumentName).SetModified: a namesake for a
   free copy's bare name, nothing recorded, nothing read back.
5. sch_reload_document save=true called a declined save "saved" when the
   file had been written inside the current 2 s FileAge tick (flag clean,
   age unmoved), closed the sheet and reopened it from disk: the edit
   lost.
6. A record entry spelled F:\\ while listing and FileName say H:\\ was
   filed under bridge_record_closed ('edit is lost') although the document
   is open.
"""
from __future__ import annotations

import json
import ntpath

import pytest

from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments
from tests.test_bridge_record_names_one_file import (
    BARE, F_SHEET, LIB, SCRATCH, SHEET, Listed, ServerDoc, World, _ALTIUM,
    _key, _pipe_list)


def _subst(bare="nil", focus="sheet"):
    """The carrier project listed under F: while its sheet's FileName is H:."""
    w = World(bare=bare, focus=focus, flag_sticks=False)
    w.project.docs = [Listed(F_SHEET)]
    plain_client = w.client.GetDocumentByPath
    plain_age = w.disk.age

    def via(p):
        p = str(p)
        return plain_client(SHEET) if _key(p) == _key(F_SHEET) else plain_client(p)

    def age(p):
        return plain_age(SHEET) if _key(str(p)) == _key(F_SHEET) else plain_age(p)
    w.client.GetDocumentByPath = via
    w.ns["FileAge"] = age
    w.ns["FileExists"] = lambda p: age(p) != -1
    return w


# ---------------------------------------------------------------------------
# 1. A subst-listed member is still a member
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bare", ["nil", "first"])
def test_a_subst_listed_focused_sheet_never_answers_its_copys_bare_name(bare):
    w = _subst(bare)
    assert w.FindOpenServerDoc(BARE) is not w.sheet
    w.MarkDocDirty(BARE)
    assert w.sheet.marks == 0, "the carrier sheet was marked for its copy"
    assert _key(SHEET) not in w.record_keys()
    assert w.MarkLastVia == "ambiguous_name"


@pytest.mark.parametrize("bare", ["nil", "first"])
def test_a_hand_edited_copy_is_not_called_unmodified_under_subst(bare):
    w = _subst(bare)
    w.scratch.Modified = True
    w.save_all(only_modified=True)
    assert w.sheet.saves == 0
    assert BARE.upper() not in _pipe_list(w.SaveSkippedPaths), (
        "the edited copy was reported as skipped_unmodified")
    assert (w.scratch.saves == 1
            or BARE.upper() in _pipe_list(w.SaveUnresolvedPaths))


# ---------------------------------------------------------------------------
# 2. The absolute walk and ListedServerDoc accept only the file itself
# ---------------------------------------------------------------------------

def _stranger_world():
    w = World(focus=None, sheet_open=False)
    stranger = ServerDoc(w.disk, "")
    stranger.Modified = True
    plain = w.client.GetDocumentByPath
    w.client.GetDocumentByPath = (
        lambda p: stranger if _key(str(p)) == _key(SHEET) else plain(p))
    return w, stranger


def test_the_walk_rejects_the_stranger_the_exact_lookup_rejected():
    w, _stranger = _stranger_world()
    assert w.FindOpenServerDoc(SHEET) is None


def test_save_all_never_writes_a_stranger_for_a_listed_member():
    w, stranger = _stranger_world()
    w.save_all(only_modified=True)
    assert stranger.saves == 0, "a document that is not the member was written"


def test_the_subst_alias_is_still_found_by_its_listing():
    w = _subst(focus="lib")
    assert w.FindOpenServerDoc(F_SHEET) is w.sheet
    assert w.FindDocVia == "walk"
    assert w.ListedServerDoc(Listed(F_SHEET)) is w.sheet


def test_listed_server_doc_rejects_a_stranger_at_an_absolute_listing():
    w, _stranger = _stranger_world()
    assert w.ListedServerDoc(Listed(SHEET)) is None


# ---------------------------------------------------------------------------
# 3. Proj_SetDocumentParameter marks the sheet it edited
# ---------------------------------------------------------------------------

class _Iter:
    def __init__(self, items):
        self.items = list(items)
        self.i = 0

    def SetState_IterationDepth(self, _d):
        pass

    def AddFilter_ObjectSet(self, _s):
        pass

    @property
    def FirstSchObject(self):
        self.i = 0
        return self.NextSchObject

    @property
    def NextSchObject(self):
        if self.i < len(self.items):
            self.i += 1
            return self.items[self.i - 1]
        return None


class _Param:
    def __init__(self, name="", text=""):
        self.Name = name
        self.Text = text


class _Ctl:
    def PreProcess(self, *_a):
        pass

    def PostProcess(self, *_a):
        pass


class _SchDoc:
    def __init__(self, name, params=(), path=None):
        self.DocumentName = name
        self.path = path or name          # where it lives on disk
        self.params = list(params)
        self.registered = []
        self.ObjectId = 0

    @property
    def SchIterator_Create(self):
        return _Iter(self.params)

    def SchIterator_Destroy(self, _it):
        pass

    def RegisterSchObjectInContainer(self, obj):
        self.registered.append(obj)

    @property
    def GraphicallyInvalidate(self):
        return None


def _sch_server(w, docs):
    """GetSchDocumentByPath: an absolute path by key; a bare name the FIRST
    open sheet of that name (an arbitrary namesake), as on the field."""
    class Srv:
        ProcessControl = _Ctl()

        @staticmethod
        def GetSchDocumentByPath(p):
            p = str(p)
            for d in docs:
                if (_key(d.path) == _key(p)
                        or ntpath.basename(d.path).upper()
                        == p.strip().upper()):
                    return d
            return None

        @staticmethod
        def SchObjectFactory(_kind, _how):
            return _Param()

        @staticmethod
        def GetCurrentSchDocument():
            return None
    return Srv


def _common(w):
    ns = w.ns
    ns.update(
        ExtractJsonValue=lambda params, key: json.loads(params).get(key, ""),
        BuildErrorResponse=lambda rid, code, msg: {"error": code, "msg": msg},
        BuildSuccessResponse=lambda rid, body: {"ok": json.loads(body)},
        EscapeJsonString=lambda s: json.dumps(s)[1:-1],
        BoolToJsonStr=lambda b: "true" if b else "false",
        MarkOutcomeJson=lambda d: '{"found":%s}' % (
            "true" if ns["MarkLastFound"] else "false"),
        MkSet=lambda *a: set(a), eParameter=1, eIterateFirstLevel=1,
        eCreate_Default=0, eNetLabel=2,
        SchBeginModify=lambda o: None, SchEndModify=lambda o: None,
        SchRegisterObject=lambda d, o: None)
    main = load(_ALTIUM / "Main.pas", minimum=20)
    build(main, ["SchDocMarkPath"], ns)
    return ns


def _set_doc_param(w, file_path, docs):
    ns = _common(w)
    ns["SchServer"] = _sch_server(w, docs)
    build(load(_ALTIUM / "Project.pas", minimum=20),
          ["Proj_SetDocumentParameter"], ns)
    return ns["Proj_SetDocumentParameter"](
        json.dumps({"file_path": file_path, "name": "Rev", "value": "B"}), "1")


def test_a_bare_file_path_naming_only_a_member_marks_and_records_it():
    """a0c9da9: found:false, nothing recorded, the edit left only in memory."""
    w = World(bare="first", focus="lib", flag_sticks=False)
    w.free.docs = [d for d in w.free.docs if d.DM_FullPath != BARE]
    w.client.open.remove(w.scratch)
    sheet = _SchDoc(SHEET)
    reply = _set_doc_param(w, BARE, [sheet])
    assert "ok" in reply, reply
    assert len(sheet.registered) == 1, "the parameter was not added"
    assert w.sheet.marks >= 1, "the edited member was not marked"
    assert _key(SHEET) in w.record_keys(), "the edit is not on the record"
    assert reply["ok"]["mark"]["found"] is True


def test_an_ambiguous_bare_file_path_is_refused_before_the_edit():
    w = World(bare="first", focus="lib", flag_sticks=False)
    sheet, copy = _SchDoc(SHEET), _SchDoc(SCRATCH)
    reply = _set_doc_param(w, BARE, [sheet, copy])
    assert reply.get("error") == "AMBIGUOUS_NAME"
    assert sheet.registered == [] and copy.registered == []


def test_an_absolute_file_path_marks_that_file_even_with_its_namesake_focused():
    w = World(bare="first", focus="sheet", flag_sticks=False)
    copy = _SchDoc(SCRATCH)
    reply = _set_doc_param(w, SCRATCH, [_SchDoc(SHEET), copy])
    assert "ok" in reply and len(copy.registered) == 1
    assert w.sheet.marks == 0 and w.scratch.marks >= 1
    assert w.record_keys() == {_key(SCRATCH)}


# ---------------------------------------------------------------------------
# 4. The sheet_path handlers mark through MarkDocDirty
# ---------------------------------------------------------------------------

_SHEET_PATH_HANDLERS = [
    "Gen_PlaceSchComponentFromLibrary", "Gen_PlaceNetLabel",
    "Gen_PlacePowerPort", "Gen_SetSchComponentParameters",
    "Gen_SetSchComponentsParameters", "Gen_SetSchTextPositions",
    "Gen_ClearSchSourceLibrary"]


@pytest.mark.parametrize("name", _SHEET_PATH_HANDLERS)
def test_a_sheet_path_handler_refuses_an_ambiguous_name_and_marks_the_file(name):
    body = strip_comments(load(_ALTIUM / "Generic.pas", minimum=100)[name])
    assert "SrvDoc.SetModified(True)" not in body, "the raw mark is back"
    refuse = body.index("If AmbiguousBareDocName(SheetPath) Then")
    assert refuse < body.index("GetSchDocumentByPath(SheetPath)")
    assert "Marked := MarkDocDirty(SchDocMarkPath(SheetPath, SchDoc));" in body
    assert "',\"marked_modified\":' + BoolToJsonStr(Marked)" in body
    assert "',\"mark\":' + MarkJson" in body


class _Loc:
    X = 0
    Y = 0


class _NetLabel:
    def __init__(self):
        self.Location = _Loc()
        self.Text = ""
        self.Orientation = 0
        self.FontId = 0
        self.Color = 0


def _place_net_label(w, sheet_path, docs):
    ns = _common(w)
    srv = _sch_server(w, docs)
    srv.SchObjectFactory = staticmethod(lambda kind, how: _NetLabel())
    ns.update(SchServer=srv, MilsToCoord=lambda m: m, MilsToCoordF=lambda m: m,
              StrToFloatDef=lambda s, d: float(s) if str(s).strip() else d,
              FloatToJsonStr=lambda v: json.dumps(float(v)),
              InferNetLabelStyle=lambda d, f, c: False)
    build(load(_ALTIUM / "Generic.pas", minimum=100), ["Gen_PlaceNetLabel"], ns)
    return ns["Gen_PlaceNetLabel"](
        json.dumps({"text": "N1", "sheet_path": sheet_path, "x": "100",
                    "y": "200"}), "1")


def test_place_net_label_on_the_free_copy_marks_the_copy_not_the_focused_sheet():
    """The raw GetDocumentByPath(DocumentName).SetModified handed the copy's
    BARE name to the plain lookup, which on the field answers with the first
    open document of that name: the carrier sheet, opened first."""
    w = World(bare="first", focus="sheet", flag_sticks=False)
    copy = _SchDoc(BARE, path=SCRATCH)   # a free document: bare DocumentName
    reply = _place_net_label(w, SCRATCH, [copy])
    assert "ok" in reply, reply
    assert copy.registered, "the label was not placed"
    assert w.sheet.marks == 0, "the focused carrier sheet was marked"
    assert w.scratch.marks >= 1 and w.record_keys() == {_key(SCRATCH)}
    assert reply["ok"]["marked_modified"] is False     # flag never registers
    assert reply["ok"]["mark"]["found"] is True


def test_place_net_label_refuses_an_ambiguous_bare_sheet_path():
    w = World(bare="first", focus="sheet", flag_sticks=False)
    sheet, copy = _SchDoc(SHEET), _SchDoc(BARE, path=SCRATCH)
    reply = _place_net_label(w, BARE, [sheet, copy])
    assert reply.get("error") == "AMBIGUOUS_NAME"
    assert sheet.registered == [] and copy.registered == []


# ---------------------------------------------------------------------------
# 5. The reload waits out the tick instead of trusting the flag
# ---------------------------------------------------------------------------

def _reload(w, *, advance=True):
    class Sch:
        @staticmethod
        def GetSchDocumentByPath(_path):
            return None

    class App:
        @staticmethod
        def ProcessMessages():
            return None

    calls = []
    ns = w.ns

    def sleep(_ms):
        if advance:
            w.disk.clock += 1                # time passes while it waits
        calls.append("sleep")
    ns.update(
        ExtractJsonValue=lambda params, key: json.loads(params).get(key, ""),
        CurrentFocusedDocPath=lambda d: LIB, ResolveLoadedDocPath=lambda p: p,
        SchServer=Sch, Application=App, Sleep=sleep,
        SchDocObjectCountsJson=lambda d: "{}",
        ProjectHoldingDoc=lambda p: None,
        BuildErrorResponse=lambda rid, code, msg: {"error": code, "msg": msg},
        BuildErrorResponseDetailed=lambda rid, code, msg, det: {"error": code},
        BuildSuccessResponse=lambda rid, body: {"ok": body},
        EscapeJsonString=lambda s: s,
        BoolToJsonStr=lambda b: "true" if b else "false",
        ResetParameters=lambda: None, AddStringParameter=lambda k, v: None,
        RunProcess=lambda p: calls.append(p),
        MatchDocPathInProject=lambda proj, name: "",
        InvalidateCompileCache=lambda d: None, SmartCompile=lambda p: None,
        RestoreFocusedDoc=lambda p: None, NoteNextStep=lambda s: None)
    build(load(_ALTIUM / "Generic.pas", minimum=100), ["Gen_ReloadSchDocument"], ns)
    reply = ns["Gen_ReloadSchDocument"](json.dumps({"file_path": SCRATCH}), "1")
    return reply, calls


def test_a_declined_save_just_after_a_write_does_not_close_the_sheet():
    """The probe on a0c9da9: written this tick, then edited, then a save the
    editor declines - 'saved', closed, reopened from disk, edit gone."""
    w = World(focus="lib", flag_sticks=False)
    w.disk.touch(SCRATCH)                    # an app_save_all just wrote it
    w.MarkDocDirty(SCRATCH)

    def declined(_kind):
        w.scratch.saves += 1
    w.scratch.DoFileSave = declined
    reply, calls = _reload(w)
    assert reply.get("error") == "SAVE_FAILED", reply
    assert w.scratch in w.client.open, "closed with its edit unwritten"
    assert w.BridgeEditRecorded(SCRATCH) is True
    assert w.scratch.saves == 2, "the save was not retried after the tick"
    assert "sleep" in calls


def test_a_real_save_just_after_a_write_is_proved_by_the_second_write():
    """Written this tick and saved again: the first write cannot move the
    age, so the call waits the tick out and writes once more."""
    w = World(focus="lib", flag_sticks=False)
    w.disk.touch(SCRATCH)
    w.MarkDocDirty(SCRATCH)
    first = []

    def save(_kind):
        w.scratch.saves += 1
        w.scratch.Modified = False
        if first:
            w.disk.touch(SCRATCH)            # a later tick: the age moves
        first.append(1)                      # first write: same tick
    w.scratch.DoFileSave = save
    reply, _calls = _reload(w)
    assert reply.get("error") != "SAVE_FAILED", reply
    assert w.scratch.saves == 2


def test_the_wait_is_bounded():
    w = World(focus="lib", flag_sticks=False)
    w.disk.touch(SCRATCH)
    w.MarkDocDirty(SCRATCH)
    w.scratch.DoFileSave = lambda _k: None
    reply, calls = _reload(w, advance=False)   # the clock never moves
    assert reply.get("error") == "SAVE_FAILED"
    assert calls.count("sleep") == 500


# ---------------------------------------------------------------------------
# 6. An entry open under another spelling is kept, not called lost
# ---------------------------------------------------------------------------

def _caller_spells_f():
    w = World(focus="lib", flag_sticks=False)          # project lists H:
    plain_client = w.client.GetDocumentByPath
    plain_age = w.disk.age

    def via(p):
        p = str(p)
        return plain_client(SHEET) if _key(p) == _key(F_SHEET) else plain_client(p)

    def age(p):
        return plain_age(SHEET) if _key(str(p)) == _key(F_SHEET) else plain_age(p)
    w.client.GetDocumentByPath = via
    w.ns["FileAge"] = age
    w.ns["FileExists"] = lambda p: age(p) != -1
    return w


def test_an_f_spelled_entry_for_an_open_h_document_is_kept_and_listed():
    w = _caller_spells_f()
    w.MarkDocDirty(F_SHEET)
    assert w.record_keys() == {_key(F_SHEET)}
    w.save_all(only_modified=True)
    assert w.SaveBridgeRecordClosed == "", "an open document's edit was called lost"
    assert _pipe_list(w.SaveBridgeRecordUnresolved) == [_key(F_SHEET)]
    assert w.record_keys() == {_key(F_SHEET)}, "the entry was dropped"


def test_an_entry_whose_document_really_closed_is_still_called_closed():
    w = _caller_spells_f()
    w.MarkDocDirty(F_SHEET)
    w.client.CloseDocument(w.sheet)
    w.save_all(only_modified=True)
    assert _pipe_list(w.SaveBridgeRecordClosed) == [_key(F_SHEET)]
    assert w.SaveBridgeRecordUnresolved == ""


def test_save_all_reports_the_unresolved_list():
    body = strip_comments(load(_ALTIUM / "Application.pas", minimum=10)["App_SaveAll"])
    assert "',\"bridge_record_unresolved\":' + PipeListToJsonArray(SaveBridgeRecordUnresolved)" in body
    assert "SaveBridgeRecordUnresolved := '';" in body
