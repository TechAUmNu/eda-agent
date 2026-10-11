# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The dirty mark and the bridge's record act on ONE file, never on its namesake.

REVIEW OF 4c66a18, 2026-10-11 (the fix for the 2026-10-10 field run, where
lib_set_footprint_height and obj_modify on FREE documents under a Temp path
left the modified flag reading false while the tabs showed '*'):

1. FindOpenServerDoc could return a DIFFERENT file with the same name. A
   free document's DM_FullPath is a bare name and usually does not resolve,
   so a scratch copy of ``multiviewer__MCU_UI.SchDoc`` was skipped and the
   project's same-named sheet was the only "name hit": obj_modify on the
   focused scratch copy marked the REAL carrier sheet, and app_save_all then
   wrote it. An absolute request for a closed file could name-match an open
   free copy, and DocIsModified on a closed project member could read the
   free copy's flag.
2. The record kept bare names, and a bare entry matched EVERY file with that
   name, so the clean carrier sheet went on the record and was rewritten.
3. A free document the save walk cannot resolve was never attempted, and the
   record changed nothing for exactly the field case.
4. A record entry outlived the write that made it stale (the user's Ctrl+S,
   a reload, a sheet reload), so a clean library was rewritten - the
   2026-10-06 mtime harm the skip policy exists to prevent.
5. app_close_document consulted only the flag, closed a bridge-edited
   document without discard and threw the record away.
6. FileAge has 2 s resolution: a write in the same tick as the previous one
   read as "not written", a false alarm. The flag cannot settle it alone
   (on the field documents it never registered, so a DECLINED save also
   reads clean): "same tick" also needs the file's previous write to lie
   inside the current tick.
7. A close that wrote nothing (proj_close save=false, a discarding sheet
   delete, a reload, a library refresh) left the entry behind, so the
   reopened clean file was rewritten; the reload and the sheet delete
   also dropped an unwritten bridge edit on the flag's word.
8. The save walk looked a free document's bare listing up with a plain
   GetDocumentByPath, which can return a NAMESAKE; it now refuses to
   guess and lists what it could not reach.

These tests EXECUTE the real Pascal (tests/pascal_eval.py translates it)
on a fixture workspace modelled on the field: a free scratch copy whose
DM_FullPath is a bare name, plus the carrier project's sheet of the same
name. Each fails on 4c66a18 for the reason above.
"""
from __future__ import annotations

import json
import ntpath
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

SCRATCH = r"C:\Users\user\AppData\Local\Temp\claude\scratch\multiviewer__MCU_UI.SchDoc"
SHEET = r"H:\git\multiviewer\hardware\multiviewer\multiviewer__MCU_UI.SchDoc"
VENDOR = r"C:\vendor\copies\multiviewer__MCU_UI.SchDoc"
BARE = "multiviewer__MCU_UI.SchDoc"
LIB = r"C:\Users\user\AppData\Local\Temp\claude\scratch\height_copy.PcbLib"


def _key(p: str) -> str:
    return p.replace("/", "\\").strip().upper()


def _absolute(p: str) -> bool:
    return p[1:2] == ":" or p[:2] == "\\\\"


# ---------------------------------------------------------------------------
# A fixture workspace
# ---------------------------------------------------------------------------

class Disk:
    """FileAge as Windows reports it: a DOS time in 2 s ticks. ``clock`` is
    the current tick; every file starts written long ago (tick 1000). A
    write stamps the file with the CURRENT tick after time has passed
    (``touch``), so two writes inside one tick (``touch_same_tick``) leave
    the age where it was - the FileAge resolution limit."""

    def __init__(self):
        self.ages: dict[str, int] = {}
        self.clock = 5000

    def add(self, path, age=1000):
        self.ages[_key(path)] = age

    def age(self, path):
        if not _absolute(str(path).strip()):
            return -1
        return self.ages.get(_key(path), -1)

    def touch(self, path):
        self.clock += 5
        self.ages[_key(path)] = self.clock

    def touch_same_tick(self, path):
        pass                               # the age cannot move inside its tick


class Now:
    """Pascal's parameterless ``Now`` (a TDateTime in days) on the disk's
    clock; ``DateTimeToFileDate`` maps it back to 2 s ticks."""

    def __init__(self, disk):
        self.disk = disk

    def __sub__(self, days):
        return self.disk.clock * 2.0 / 86400.0 - days


def _file_date(days):
    return int(days * 86400.0 / 2.0 + 1e-6)


class ServerDoc:
    """flag_sticks=False is the field behaviour: SetModified(True) does not
    register, so Modified keeps reading False with '*' on the tab."""

    def __init__(self, disk, filename, flag_sticks=True, save_moves=True):
        self.disk = disk
        self.FileName = filename
        self.Modified = False
        self.flag_sticks = flag_sticks
        self.save_moves = save_moves
        self.saves = 0
        self.marks = 0

    def SetModified(self, value):
        if value:
            self.marks += 1
        if self.flag_sticks or not value:
            self.Modified = value

    def DoFileSave(self, _kind):
        self.saves += 1
        if self.save_moves:
            self.disk.touch(self.FileName)
        self.Modified = False


class Listed:
    def __init__(self, full, kind="SCH"):
        self.DM_FullPath = full
        self.DM_DocumentKind = kind


class Project:
    def __init__(self, path, docs):
        self.DM_ProjectFullPath = path
        self.docs = docs

    @property
    def DM_LogicalDocumentCount(self):
        return len(self.docs)

    def DM_LogicalDocuments(self, j):
        return self.docs[j]


class Workspace:
    def __init__(self, projects, free):
        self.projects = projects
        self.DM_FreeDocumentsProject = free

    @property
    def DM_ProjectCount(self):
        return len(self.projects)

    def DM_Projects(self, i):
        return self.projects[i]


class View:
    def __init__(self, doc):
        self.OwnerDocument = doc


class Client:
    """GetDocumentByPath: an absolute path resolves the open document with
    that file name (case-insensitively); a BARE name either resolves to
    nothing (bare='nil') or to the first open document of that name in
    open order (bare='first') - both have been seen to fit the field."""

    def __init__(self, bare="nil"):
        self.open: list[ServerDoc] = []
        self.focused = None
        self.bare = bare

    def GetDocumentByPath(self, path):
        path = str(path)
        if _absolute(path.strip()):
            for d in self.open:
                if _key(d.FileName) == _key(path):
                    return d
            return None
        if self.bare == "first":
            for d in self.open:
                if ntpath.basename(d.FileName).upper() == path.strip().upper():
                    return d
        return None

    @property
    def GetCurrentView(self):
        return View(self.focused) if self.focused is not None else None

    def CloseDocument(self, doc):
        self.open = [d for d in self.open if d is not doc]
        if self.focused is doc:
            self.focused = None
        self.on_close(doc)

    def on_close(self, doc):
        pass


_MAIN_NAMES = [
    "LooksAbsolutePath", "DocPathKey", "DocNameKey", "DocPathsMatch",
    "ServerDocFileName", "FocusedServerDoc", "ListedDocKey", "DocNameCount",
    "FindOpenServerDoc", "FileAgeOrNone", "BridgeEntryKey",
    "BridgeEntryStamp", "BridgeRecordWithout", "PathListWith",
    "BridgeEditRecorded", "RecordBridgeEdit", "ForgetBridgeEdit",
    "BridgeRecordKeys", "ForgetBridgeEditsIfClosed", "ProjectMemberPaths",
    "MarkDocDirty", "MarkDocDirtyByPath",
    "DocIsModified", "ListedServerDoc", "AgeIsThisTick", "WriteDocAndNote",
    "SaveOneDocByDocRef", "SaveProjectMembers", "SaveAllDirty",
    "SaveBridgeRecordSweep",
]

_GLOBALS = {
    "BridgeEditedPaths": "", "SaveBridgeRecordPaths": "",
    "SaveBridgeRecordUnwritten": "", "SaveBridgeRecordSameTick": "",
    "SaveBridgeRecordClosed": "", "SaveAttemptedKeys": "",
    "SaveUnresolvedPaths": "",
    "SaveAttempts": 0, "SaveSkipped": 0, "SaveSkippedPaths": "",
    "SaveSkipCleanLibs": False, "SaveSkipCleanSheets": False,
    "FindDocVia": "none", "MarkLastPath": "", "MarkLastFile": "",
    "MarkLastVia": "none", "MarkLastFound": False,
    "MarkLastModified": False, "MarkLastFocused": -1, "MarkLastError": "",
}


class World:
    """The field: a carrier project whose sheet is open, and a FREE scratch
    copy of that sheet (bare DM_FullPath) plus a free library copy."""

    def __init__(self, bare="nil", focus="scratch", flag_sticks=True,
                 sheet_open=True):
        self.disk = Disk()
        for p in (SCRATCH, SHEET, VENDOR, LIB):
            self.disk.add(p)
        self.client = Client(bare)
        self.sheet = ServerDoc(self.disk, SHEET, flag_sticks)
        self.scratch = ServerDoc(self.disk, SCRATCH, flag_sticks)
        self.lib = ServerDoc(self.disk, LIB, flag_sticks)
        # Open order: the project's sheet first (opened with the project).
        if sheet_open:
            self.client.open.append(self.sheet)
        self.client.open += [self.scratch, self.lib]
        self.free = Project("", [Listed(BARE), Listed("height_copy.PcbLib", "PCBLIB")])
        self.project = Project(r"H:\git\multiviewer\hardware\multiviewer\carrier.PrjPcb",
                               [Listed(SHEET)])
        self.client.focused = {"scratch": self.scratch, "sheet": self.sheet,
                               "lib": self.lib, None: None}[focus]

        def forget_free(doc):
            if doc is self.scratch:
                self.free.docs = [d for d in self.free.docs if d.DM_FullPath != BARE]
        self.client.on_close = forget_free
        ns = dict(_GLOBALS)
        ns.update(Client=self.client,
                  GetWorkspace=Workspace([self.project], self.free),
                  FileAge=self.disk.age,
                  Now=Now(self.disk), DateTimeToFileDate=_file_date,
                  FileExists=lambda p: self.disk.age(p) != -1)
        bodies = load(_ALTIUM / "Main.pas", minimum=20)
        self.ns = build(bodies, [n for n in _MAIN_NAMES if n in bodies], ns)

    def __getattr__(self, name):
        ns = self.__dict__.get("ns")
        if ns is not None and name in ns:
            return ns[name]
        raise AttributeError(name)

    def record_keys(self):
        entries = [e for e in self.ns["BridgeEditedPaths"].split("|") if e]
        return {e.split("*", 1)[0] for e in entries}

    def save_all(self, only_modified=False, include_libraries=False):
        """App_SaveAll's sequence around the walk (pinned below)."""
        ns = self.ns
        for k in ("SaveBridgeRecordPaths", "SaveBridgeRecordUnwritten",
                  "SaveBridgeRecordSameTick", "SaveBridgeRecordClosed",
                  "SaveAttemptedKeys", "SaveSkippedPaths",
                  "SaveUnresolvedPaths"):
            ns[k] = ""
        ns["SaveAttempts"] = 0
        ns["SaveSkipped"] = 0
        ns["SaveSkipCleanLibs"] = not include_libraries
        ns["SaveSkipCleanSheets"] = only_modified
        ns["SaveAllDirty"](0)
        ns["SaveSkipCleanLibs"] = False
        ns["SaveSkipCleanSheets"] = False
        if "SaveBridgeRecordSweep" in ns:
            ns["SaveBridgeRecordSweep"]("")


# ---------------------------------------------------------------------------
# 1. The lookup never returns a namesake
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bare", ["nil", "first"])
def test_obj_modify_on_the_focused_scratch_copy_marks_only_the_copy(bare):
    """ProcessActiveDoc passes the bare DocumentName of the focused scratch
    sheet. On 4c66a18 the walk skipped the unresolvable free copy, found
    the project's sheet as the one 'name hit', and marked the REAL sheet."""
    w = World(bare=bare, focus="scratch")
    assert w.MarkDocDirty(BARE) is True
    assert w.scratch.Modified is True
    assert w.sheet.marks == 0 and w.sheet.Modified is False, (
        "the carrier sheet was marked for an edit made to its scratch copy")
    assert _key(w.MarkLastFile) == _key(SCRATCH)


@pytest.mark.parametrize("bare", ["nil", "first"])
def test_a_bare_name_nothing_focused_carries_is_refused_not_guessed(bare):
    w = World(bare=bare, focus="lib")
    w.MarkDocDirty(BARE)
    assert w.sheet.marks == 0 and w.scratch.marks == 0
    assert w.MarkLastFound is False
    assert w.MarkLastVia == "ambiguous_name"


def test_an_absolute_request_for_a_closed_copy_finds_nothing():
    """A caller-supplied path to a file that is not open: on 4c66a18 the
    free copy's bare DM_FullPath name-matched it, GetDocumentByPath(bare)
    resolved, and the walk returned an unrelated open document."""
    w = World(bare="first", focus=None)
    assert w.FindOpenServerDoc(VENDOR) is None


def test_a_closed_project_member_does_not_read_its_namesakes_flag():
    """DocIsModified backs app_context, proj_list_documents and the dirty
    project member list. A closed member must read False, not the flag of
    an open free copy that shares its name."""
    w = World(bare="first", focus=None, sheet_open=False)
    w.scratch.Modified = True
    assert w.DocIsModified(SHEET) is False
    assert w.DocIsModified(SCRATCH) is True


def test_an_absolute_request_still_finds_the_free_copy():
    w = World(bare="nil", focus="sheet")
    assert w.FindOpenServerDoc(SCRATCH.lower().replace("\\", "/")) is w.scratch


# ---------------------------------------------------------------------------
# 2. The record holds resolved absolute paths
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bare", ["nil", "first"])
def test_editing_the_scratch_copy_puts_only_the_copy_on_the_record(bare):
    w = World(bare=bare, focus="scratch", flag_sticks=False)
    w.MarkDocDirty(BARE)
    assert w.record_keys() == {_key(SCRATCH)}, (
        "a bare entry, or the carrier sheet, went on the record")
    assert w.BridgeEditRecorded(SCRATCH) is True
    assert w.BridgeEditRecorded(SHEET) is False


@pytest.mark.parametrize("bare", ["nil", "first"])
def test_save_all_does_not_rewrite_the_clean_carrier_sheet(bare):
    """The 2026-10-06 harm through the new door: only_modified wrote the
    clean carrier sheet from a bare record entry."""
    w = World(bare=bare, focus="scratch", flag_sticks=False)
    w.MarkDocDirty(BARE)
    w.save_all(only_modified=True)
    assert w.sheet.saves == 0
    assert w.scratch.saves == 1


def test_writing_one_namesake_does_not_forget_the_other():
    w = World(bare="nil", focus="scratch", flag_sticks=False)
    w.MarkDocDirty(BARE)
    w.ForgetBridgeEdit(SHEET)
    assert w.BridgeEditRecorded(SCRATCH) is True


def test_two_same_named_files_in_different_folders_stay_apart():
    w = World()
    w.RecordBridgeEdit(SCRATCH)
    assert w.BridgeEditRecorded(VENDOR) is False
    w.ForgetBridgeEdit(VENDOR)
    assert w.BridgeEditRecorded(SCRATCH) is True
    w.ForgetBridgeEdit(SCRATCH)
    assert w.BridgeEditRecorded(SCRATCH) is False


def test_recorded_for_save_all_means_an_absolute_key():
    """It was BridgeEditRecorded(MarkLastPath), always true because the
    caller's spelling went on the record before the lookup - so section
    38 step 2's check on it proved nothing. Now it says whether the mark
    put an ABSOLUTE key on the record, the only kind a save-all writes."""
    body = load(_ALTIUM / "Utils.pas", minimum=20)["MarkOutcomeJson"]
    assert "',\"recorded_for_save_all\":' + BoolToJsonStr(MarkLastRecorded)" in body
    w = World(bare="nil", focus="lib", flag_sticks=False)
    w.MarkDocDirty(BARE)                   # ambiguous: nothing resolved
    assert w.MarkLastRecorded is False
    w.MarkDocDirty(SCRATCH)
    assert w.MarkLastRecorded is True


# ---------------------------------------------------------------------------
# 3. A free document the walk cannot reach is still written
# ---------------------------------------------------------------------------

def test_save_all_writes_a_recorded_free_copy_the_walk_cannot_resolve():
    """The exact field case: the scratch copy is a free document whose bare
    DM_FullPath resolves to nothing, and the focused tab is the same-named
    project sheet. Its flag reads clean. 4c66a18 never attempted it."""
    w = World(bare="nil", focus="sheet", flag_sticks=False)
    w.MarkDocDirty(SCRATCH)
    w.save_all(only_modified=True)
    assert w.scratch.saves == 1, "the bridge-edited free copy was never written"
    assert w.sheet.saves == 0
    assert _key(SCRATCH) in _key(w.SaveBridgeRecordPaths)
    assert w.record_keys() == set()


def test_the_walk_never_writes_a_namesake_for_a_free_listing():
    """Where a bare name DOES resolve, it resolves to the first open
    document of that name - here the carrier sheet, opened with its project.
    4c66a18's walk looked the free copy's listing up that way, so the
    carrier sheet was written a second time on the free copy's behalf
    while the free copy itself was never reached, and nothing said so."""
    w = World(bare="first", focus=None)
    w.scratch.Modified = True
    w.save_all()
    assert w.sheet.saves == 1, "the carrier sheet was written for its namesake"
    assert w.scratch.saves == 0
    assert BARE.upper() in w.ns.get("SaveUnresolvedPaths", "").upper(), (
        "a free document the walk could not reach was skipped silently")


def test_save_all_reports_the_free_documents_it_could_not_reach():
    body = load(_ALTIUM / "Application.pas", minimum=12)["App_SaveAll"]
    assert "SaveUnresolvedPaths := '';" in body
    assert ("',\"free_documents_not_reached\":' + "
            "PipeListToJsonArray(SaveUnresolvedPaths)") in body


def test_save_all_calls_the_sweep_after_the_walk():
    body = load(_ALTIUM / "Application.pas", minimum=12)["App_SaveAll"]
    assert body.index("SaveAllDirty(0);") < body.index("SaveBridgeRecordSweep(")
    for key in ('"bridge_record_closed":', '"bridge_record_same_tick":'):
        assert key in body, key


def test_a_recorded_document_closed_unwritten_is_reported_and_dropped():
    """proj_close (or any close) of a document whose edit was never written:
    the edit is gone, and a clean reopen must not be rewritten."""
    w = World(bare="nil", focus="sheet", flag_sticks=False)
    w.MarkDocDirty(SCRATCH)
    w.client.CloseDocument(w.scratch)
    w.save_all()
    assert _key(SCRATCH) in _key(w.ns.get("SaveBridgeRecordClosed", ""))
    assert w.record_keys() == set()


# ---------------------------------------------------------------------------
# 4. A file written by any route leaves the record
# ---------------------------------------------------------------------------

def test_a_file_saved_outside_the_bridge_is_not_rewritten():
    """lib_set_footprint_height marks the library; the user presses Ctrl+S
    (or RefreshSchLibFromDisk saves and reopens). The default app_save_all
    must leave the now-clean library alone."""
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)
    w.disk.touch(LIB)                      # written by a route outside the record
    assert w.BridgeEditRecorded(LIB) is False
    w.save_all()
    assert w.lib.saves == 0, "a clean library was rewritten from a stale record"


def test_a_new_edit_after_an_outside_save_is_recorded_again():
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)
    w.disk.touch(LIB)
    w.MarkDocDirty(LIB)
    assert w.BridgeEditRecorded(LIB) is True
    w.save_all()
    assert w.lib.saves == 1


# ---------------------------------------------------------------------------
# 4b. A close that wrote nothing also ends the entry
# ---------------------------------------------------------------------------

def test_a_document_closed_unwritten_and_reopened_clean_is_not_rewritten():
    """proj_close save=false (or a library refresh whose flush was
    declined) closes a bridge-edited document WITHOUT writing it, so its
    age stamp still matches. Reopened clean before the next save-all, the
    walk would rewrite it from the stale entry - the 2026-10-06 harm. The
    close routes now let the record go for every document that is gone."""
    w = World(focus="sheet", flag_sticks=False)
    w.MarkDocDirty(LIB)
    w.client.CloseDocument(w.lib)
    w.ForgetBridgeEditsIfClosed(LIB + "|" + SCRATCH)
    assert w.record_keys() == set()
    w.client.open.append(w.lib)            # reopened from the unchanged file
    w.save_all()
    assert w.lib.saves == 0


def test_a_close_that_did_not_take_keeps_the_entry():
    w = World(focus="sheet", flag_sticks=False)
    w.MarkDocDirty(LIB)
    w.ForgetBridgeEditsIfClosed(LIB)       # still open: the close failed
    assert w.BridgeEditRecorded(LIB) is True


def test_project_member_paths_lists_every_member():
    w = World()
    assert w.ProjectMemberPaths(w.project) == SHEET


@pytest.mark.parametrize("unit, func, before, call", [
    ("Project.pas", "Proj_Close", "MemberPaths := ProjectMemberPaths(Project);",
     "ForgetBridgeEditsIfClosed(MemberPaths);"),
    ("Library.pas", "RefreshSchLibFromDisk",
     "RunProcess('WorkspaceManager:CloseObject');",
     "ForgetBridgeEditsIfClosed(LibPath);"),
    ("Generic.pas", "Gen_ReloadSchDocument", "Client.CloseDocument(ServerDoc);",
     "ForgetBridgeEdit(FilePath);"),
    ("Generic.pas", "Gen_DeleteSheet", "Try Client.CloseDocument(ServerDoc); Except End;",
     "If Closed Then ForgetBridgeEdit(MemberPath);"),
])
def test_every_close_route_lets_the_record_go(unit, func, before, call):
    from tests.pascal_source import strip_comments
    body = strip_comments(load(_ALTIUM / unit, minimum=10)[func])
    assert before in body and call in body, (func, call)
    assert body.index(before) < body.index(call)
    if func == "RefreshSchLibFromDisk":
        assert body.index(call) < body.index("RunProcess('WorkspaceManager:OpenObject');"), (
            "after the close, before the reopen makes it open again")


@pytest.mark.parametrize("func, refusal", [
    ("Gen_ReloadSchDocument", "Else If WasModified Or BridgeEditRecorded(FilePath) Then"),
    ("Gen_DeleteSheet", "If (WasModified Or BridgeEdited) And Not Discard Then"),
])
def test_a_reload_or_sheet_delete_refuses_an_unwritten_bridge_edit(func, refusal):
    """The F5 rule on the two other routes that close a document: a bridge
    edit whose flag reads clean is unsaved work, so save=false (reload) or
    no discard (delete) is refused instead of dropping it."""
    from tests.pascal_source import strip_comments
    body = strip_comments(load(_ALTIUM / "Generic.pas", minimum=100)[func])
    assert refusal in body
    if func == "Gen_DeleteSheet":
        assert "BridgeEdited := WasLoaded And BridgeEditRecorded(MemberPath);" in body
        assert "Else If WasModified Or BridgeEdited Then DiscardedJson := 'true'" in body
        assert "',\"bridge_edited\":' + BoolToJsonStr(BridgeEdited)" in body


# ---------------------------------------------------------------------------
# 5. A close consults the record
# ---------------------------------------------------------------------------

def _close(w: World, path: str, discard: bool):
    ns = w.ns
    ns.update(
        ExtractJsonValue=lambda params, key: json.loads(params).get(key, ""),
        BuildErrorResponse=lambda rid, code, msg: {"error": code, "msg": msg},
        BuildSuccessResponse=lambda rid, body: {"ok": json.loads(body)},
        BoolToJsonStr=lambda b: "true" if b else "false",
        EscapeJsonString=lambda s: s.replace("\\", "\\\\").replace('"', '\\"'))
    build(load(_ALTIUM / "Application.pas", minimum=12), ["App_CloseDocument"], ns)
    params = json.dumps({"file_path": path, "discard": "true" if discard else "false"})
    return ns["App_CloseDocument"](params, "1")


def test_close_refuses_a_bridge_edited_document_whose_flag_reads_clean():
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)
    reply = _close(w, LIB, discard=False)
    assert reply.get("error") == "MODIFIED", (
        "the edit was closed away with discarded:false")
    assert w.lib in w.client.open
    assert w.BridgeEditRecorded(LIB) is True


def test_a_discarding_close_says_so_and_lets_the_record_go():
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)
    reply = _close(w, LIB, discard=True)["ok"]
    assert reply["discarded"] is True
    assert reply["bridge_edited"] is True
    assert reply["modified_read"] is False
    assert w.record_keys() == set()


# ---------------------------------------------------------------------------
# 6. A write inside FileAge's 2 s tick is not a failure
# ---------------------------------------------------------------------------

def _same_tick_save(doc, disk):
    def save(_kind):
        doc.saves += 1
        disk.touch_same_tick(doc.FileName)
        doc.Modified = False
    doc.DoFileSave = save


@pytest.mark.parametrize("bare", ["nil", "first"])
def test_a_same_tick_write_is_listed_apart_from_a_failed_one(bare):
    """The library was written a moment ago (lib_save_library, then a new
    edit), and the save-all lands in the same 2 s FileAge tick: the age
    cannot move. On 4c66a18 that was bridge_record_not_written plus the
    warning - a false alarm (bare='first', where its walk reached the
    library at all)."""
    w = World(bare=bare, focus="lib", flag_sticks=False)
    w.disk.touch(LIB)                      # written just now
    w.MarkDocDirty(LIB)
    _same_tick_save(w.lib, w.disk)
    w.save_all(only_modified=True)         # nothing else written meanwhile
    assert w.lib.saves == 1
    assert _key(LIB) in _key(w.ns.get("SaveBridgeRecordSameTick", ""))
    assert w.SaveBridgeRecordUnwritten == ""


def test_a_save_that_really_failed_is_still_a_failure():
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)

    def refused(_kind):                    # editor declined: nothing changes
        w.lib.saves += 1
        w.lib.Modified = True
    w.lib.DoFileSave = refused
    w.save_all()
    assert _key(LIB) in _key(w.SaveBridgeRecordUnwritten)


def test_a_declined_save_whose_flag_never_registers_is_not_called_written():
    """THE FIELD FLAG: SetModified(True) never registered on the
    2026-10-10 documents, so Modified reads False before AND after a save
    the editor declined (an active command). Judged by the flag alone that
    is 'probably written'. But the library was last written long before
    this tick, so a real write would have moved its age: it was not
    written, and the reply must say so."""
    w = World(focus="lib", flag_sticks=False)
    w.MarkDocDirty(LIB)

    def declined(_kind):                   # nothing written, flag stays False
        w.lib.saves += 1
    w.lib.DoFileSave = declined
    w.save_all()
    assert w.lib.saves == 1
    assert _key(LIB) in _key(w.SaveBridgeRecordUnwritten), (
        "a declined save was reported as probably written")
    assert _key(LIB) not in _key(w.ns.get("SaveBridgeRecordSameTick", ""))
    assert w.BridgeEditRecorded(LIB) is True, "the edit must stay on the record"


# ---------------------------------------------------------------------------
# Unedited documents are not marked
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ProcessActiveDoc", "ProcessDocByPath"])
def test_a_mutation_that_matched_nothing_marks_nothing(name):
    from tests.pascal_source import strip_comments
    body = strip_comments(load(_ALTIUM / "Generic.pas", minimum=100)[name])
    assert "If IsMutating And (TotalMatched > 0) Then" in body
    assert "BoolToJsonStr(IsMutating And (TotalMatched > 0))" in body


def test_project_scope_marks_only_sheets_that_matched():
    from tests.pascal_source import strip_comments
    body = strip_comments(load(_ALTIUM / "Generic.pas", minimum=100)["IterateProjectDocs"])
    before = body.index("SheetMatchedBefore := TotalMatched;")
    proc = body.index("ProcessSchDocObjects(SchDoc", before)
    gate = body.index("If IsMutating And (TotalMatched > SheetMatchedBefore) Then", proc)
    assert before < proc < gate < body.index("MarkDocDirty(FilePath)", gate)


def test_the_evaluator_does_not_swallow_python_errors():
    """The translated Try/Except catches only what a fixture raises on
    purpose; anything else must fail the test."""
    ns = build({"F": "Function F(X : Integer) : Integer;\nBegin\n"
                     "    Try Result := X.Missing; Except Result := 7; End;\nEnd;\n"},
               ["F"], {})
    with pytest.raises(AttributeError):
        ns["F"](1)

    class Raises:
        @property
        def Missing(self):
            raise PascalError("altium raised")
    assert ns["F"](Raises()) == 7
