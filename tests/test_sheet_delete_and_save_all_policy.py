# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two defects measured live on 2026-10-06 (Altium 26.10.1.5, bridge
2026.10.04.11), each guarded from both sides: the Pascal handler and the
Python payload that reaches it.

proj_delete_sheet CLOSED THE WRONG DOCUMENT AND REMOVED NOTHING. Asked to
delete STUBS.SchDoc while PWR_IN_BARREL.SchDoc had the focus, it replied
``{success: true, removed: STUBS}``. PWR_IN_BARREL was closed (the next
set_active said "Document not loaded"), STUBS stayed a loaded project
member after a project save, and the next app_save_all wrote STUBS back
to disk. Gen_DeleteSheet ran WorkspaceManager:CloseObject and
RemoveObjectFromProject with ObjectKind=Document|FileName=<path> and never
looked again; CloseObject acts on the FOCUSED document. What worked every
time: DM_RemoveSourceDocument with a membership re-read
(proj_remove_document), then closing the named document through its own
handle (app_close_document), then a project save.

app_save_all WROTE EVERY OPEN DOCUMENT WITH NOTHING EDITED: documents_seen
45, attempted 29, written 29. Writing carrier.SchLib moved its mtime, the
key of the offline symbol cache, and broke a parallel tuning run; every
SchDoc came back with fresh parameter UniqueIDs. The forced write exists
because the modified flag does not propagate on some edit paths, so the
fix is a POLICY with an opt-out, not a return to trusting the flag:
libraries that read unmodified are skipped unless include_libraries.

The Pascal cannot run here, so its guards pin the calls, their ORDER and
the read-back fields whose absence was the measured failure. Each one
fails against the 2026.10.04.11 source.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import application as app_mod
from eda_agent.tools import generic as generic_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic():
    return load(_ALTIUM / "Generic.pas", minimum=100)


def _application():
    return load(_ALTIUM / "Application.pas", minimum=12)


def _main():
    return load(_ALTIUM / "Main.pas", minimum=50)


def _at(body: str, needle: str) -> int:
    pos = body.find(needle)
    assert pos != -1, f"{needle!r} is not in the handler"
    return pos


# ---------------------------------------------------------------------------
# proj_delete_sheet, the handler
# ---------------------------------------------------------------------------

def test_delete_sheet_no_longer_uses_processes_that_act_on_the_focus():
    """THE MEASURED DEFECT. CloseObject closed the focused sheet, and
    RemoveObjectFromProject removed nothing. Neither may come back."""
    body = _generic()["Gen_DeleteSheet"]
    assert "WorkspaceManager:CloseObject" not in body, (
        "Gen_DeleteSheet closes through WorkspaceManager:CloseObject, which "
        "acts on the FOCUSED document whatever FileName says. Measured: it "
        "closed PWR_IN_BARREL when asked to delete STUBS")
    assert "RemoveObjectFromProject" not in body, (
        "Gen_DeleteSheet removes through WorkspaceManager:"
        "RemoveObjectFromProject, which left STUBS a member every time")
    assert "RunProcess(" not in body, (
        "no workspace-manager process in the delete: every one measured "
        "here acts on the focus, not the path it is given")


def test_delete_sheet_removes_through_the_project_api_and_rereads():
    body = _generic()["Gen_DeleteSheet"]
    remove = _at(body, ".DM_RemoveSourceDocument(MemberPath)")
    reread = _at(body, "Gen_SheetIsMember(Project, MemberPath)")
    assert reread > remove, (
        "membership must be re-read AFTER DM_RemoveSourceDocument; issuing "
        "a removal is not evidence that it happened")
    assert '"still_member":true' in body and '"still_member":false' in body, (
        "the reply must carry still_member from the re-read in both "
        "directions")

    helper = _generic()["Gen_SheetIsMember"]
    assert "DM_LogicalDocuments" in helper and "UpperCase" in helper


def test_delete_sheet_closes_the_named_document_and_only_that_one():
    body = _generic()["Gen_DeleteSheet"]
    remove = _at(body, ".DM_RemoveSourceDocument(MemberPath)")
    close = _at(body, "Client.CloseDocument(ServerDoc)")
    assert close > remove, (
        "the close must come after the removal is confirmed: the route "
        "measured to work is remove, then close")

    # The handle closed is the one found by the member's own path, and its
    # FileName is checked against that path before it is closed.
    lookup = body.rfind("Client.GetDocumentByPath(MemberPath)", 0, close)
    assert lookup != -1, "the document closed is not looked up by its path"
    guard = body.find("ServerDoc.FileName", lookup)
    assert lookup < guard < close, (
        "the closed document's FileName must be checked against the "
        "member's path before Client.CloseDocument, so no other document "
        "can be the one that goes")

    # And looked up again afterwards: a close is reported, not assumed.
    after = body.find("Client.GetDocumentByPath(MemberPath)", close)
    assert after != -1, "no lookup after the close, so 'closed' is a guess"
    assert '"closed":' in body


def test_delete_sheet_proves_the_focused_document_survived():
    """The visible symptom of the old bug was the OTHER sheet vanishing."""
    body = _generic()["Gen_DeleteSheet"]
    assert "DM_FocusedDocument" in body
    assert '"previously_focused_still_open":' in body
    focus_check = _at(body, "Client.GetDocumentByPath(FocusedBefore)")
    assert focus_check > _at(body, "Client.CloseDocument(ServerDoc)")
    first = body[_at(body, "Success := "):].split(";", 1)[0]
    assert "FocusedStillOpen" in first, (
        "success must depend on the previously focused document still "
        "being loaded")


def test_delete_sheet_refuses_before_it_changes_anything():
    body = _generic()["Gen_DeleteSheet"]
    remove = _at(body, ".DM_RemoveSourceDocument(MemberPath)")
    for refusal in ("'NOT_FOUND'", "'SAFETY_CHECK'", "'MODIFIED'",
                    "'NOT_A_SHEET'"):
        assert _at(body, refusal) < remove, (
            f"{refusal} is decided after the removal, so a refused delete "
            f"would leave the project changed")
    # The last-sheet safety check counts schematic members.
    assert "SchCount <= 1" in body


def test_delete_sheet_reads_the_project_file_back_off_disk():
    body = _generic()["Gen_DeleteSheet"]
    # The write itself lives in Gen_WriteProjectFile since 2026.10.07.02
    # (tests/test_project_file_write_route.py pins its routes).
    write = _at(body, "Gen_WriteProjectFile(Workspace, Project, ProjFile, MemberPath, 0, WasFocusedProject)")
    assert write < _at(body, "FileState := Gen_ProjectFileState(ProjFile, MemberPath)")
    assert ".DoFileSave(''" in _generic()["Gen_WriteProjectFile"]
    assert '"project_file_checked":' in body
    assert '"project_file_lists_sheet":' in body
    assert '"file_still_on_disk":' in body
    assert "FileExists(MemberPath)" in body

    helper = _generic()["Gen_ProjectFileState"]
    assert "ReadFileContent(" in helper and "ExtractFileName(" in helper


def test_an_unread_project_file_is_unknown_not_unlisted():
    """REVIEW 2026-10-06. A Boolean helper answered "not listed" whenever
    it could not look (no project path, no file, an empty read), so success
    was reported for a file never read and delete_file ran behind a check
    that never happened. Every can't-tell exit must return the UNKNOWN
    answer, and only a real read may set "not listed"."""
    helper = _generic()["Gen_ProjectFileState"]
    assert ") : Integer;" in helper.split(chr(10), 1)[0]
    unknown = _at(helper, "Result := 2;")
    not_listed = _at(helper, "Result := 0;")
    for exit_on in ("If ProjectPath = '' Then Exit;",
                    "If Not FileExists(ProjectPath) Then Exit;",
                    "If Content = '' Then Exit;",
                    "If Pos('DOCUMENTPATH=', Content) = 0 Then Exit;"):
        at = _at(helper, exit_on)
        assert unknown < at < not_listed, (
            f"{exit_on} must exit while the answer is still UNKNOWN (2)")

    body = _generic()["Gen_DeleteSheet"]
    assert "FileChecked := FileState <> 2;" in body
    assert "ListedOnDisk := FileState = 1;" in body
    assert "Else ListedJson := 'null';" in body, (
        "an unread file must report project_file_lists_sheet null, not "
        "false: false claims a check that did not happen")


def test_only_documentpath_keys_count_as_membership():
    """REVIEW 2026-10-06. Any line ending in =NAME or a backslash + NAME
    used to match, so an output-path key naming the sheet read as
    membership."""
    helper = _generic()["Gen_ProjectFileState"]
    assert "Copy(Line, 1, 13) = 'DOCUMENTPATH='" in helper
    assert "Value := Copy(Line, 14, Length(Line) - 13);" in helper
    assert "If Value = Name Then Result := 1;" in helper
    backslash = chr(92)
    assert ("(Tail = '" + backslash + "' + Name) Or (Tail = '/' + Name)") in helper
    assert "Pos('=' + Name" not in helper, (
        "the whole-file '=NAME' search matches any key, not just "
        "DocumentPath=")


def test_delete_sheet_deletes_the_file_only_after_close_and_unlisting():
    body = _generic()["Gen_DeleteSheet"]
    delete = _at(body, "DeleteFile(MemberPath)")
    gate = body.rfind("If Closed And FileChecked And (Not ListedOnDisk) Then",
                      0, delete)
    assert gate != -1, (
        "DeleteFile must sit behind the confirmed close AND a project file "
        "that was READ and no longer names the sheet, or a failed removal "
        "(or an unread file) leaves the project pointing at a missing file")
    assert _at(body, "Gen_ProjectFileState(ProjFile, MemberPath)") < gate
    assert "LowerCase(ExtractJsonValue(Params, 'delete_file')) = 'true'" in body, (
        "deleting the file must be opt-in")


def test_delete_sheet_success_is_computed_not_written():
    """The old reply was a literal success:true with nothing behind it."""
    body = _generic()["Gen_DeleteSheet"]
    assert '"success":true' not in body
    assert "BoolToJsonStr(Success)" in body
    tail = body[_at(body, "Success := "):]
    assert "Closed" in tail.split(";", 1)[0]
    assert ("If SaveProject And ((Not FileChecked) Or ListedOnDisk) Then "
            "Success := False;") in body, (
        "with save_project, success needs a project file that was actually "
        "read and does not name the sheet")
    assert "If DeleteIt And Not Deleted Then Success := False;" in body


def test_delete_sheet_is_still_dispatched():
    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    assert re.search(r"'delete_sheet':\s*Result := Gen_DeleteSheet\(Params, RequestId\)",
                     text)


# ---------------------------------------------------------------------------
# proj_delete_sheet, the Python payload
# ---------------------------------------------------------------------------

class _RecordingBridge:
    def __init__(self, reply=None):
        self.calls = []
        self._reply = reply if reply is not None else {"success": True}

    async def send_command_async(self, command, *args, **kwargs):
        self.calls.append((command, args, kwargs))
        return self._reply


def _tools(register):
    registry = ToolRegistry()
    register(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


@pytest.fixture()
def generic_tool(monkeypatch):
    def run(name, bridge, **kwargs):
        monkeypatch.setattr(generic_mod, "get_bridge", lambda: bridge)
        fn = _tools(generic_mod.register_generic_tools)[name]
        return asyncio.run(fn(**kwargs))
    return run


@pytest.fixture()
def app_tool(monkeypatch):
    def run(name, bridge, **kwargs):
        monkeypatch.setattr(app_mod, "get_bridge", lambda: bridge)
        fn = _tools(app_mod.register_application_tools)[name]
        return asyncio.run(fn(**kwargs))
    return run


def test_delete_sheet_default_payload(generic_tool):
    bridge = _RecordingBridge()
    generic_tool("proj_delete_sheet", bridge, file_path="C:\\p\\STUBS.SchDoc")
    (command, args, _), = bridge.calls
    assert command == "generic.delete_sheet"
    assert args[0] == {"file_path": "C:\\p\\STUBS.SchDoc"}, (
        "the defaults must reach the handler as ITS defaults: save the "
        "project, keep the file, refuse a modified sheet")


def test_delete_sheet_options_reach_the_handler(generic_tool):
    bridge = _RecordingBridge()
    generic_tool("proj_delete_sheet", bridge,
                 file_path="C:\\p\\STUBS.SchDoc",
                 project_path="C:\\p\\x.PrjPcb",
                 discard=True, save_project=False, delete_file=True)
    (_, args, _), = bridge.calls
    assert args[0] == {
        "file_path": "C:\\p\\STUBS.SchDoc",
        "project_path": "C:\\p\\x.PrjPcb",
        "discard": "true",
        "save_project": "false",
        "delete_file": "true",
    }


def test_delete_sheet_passes_a_partial_failure_through(generic_tool):
    """The handler now says which step fell short; the wrapper must not
    flatten that back into a bare success."""
    reply = {"success": False, "removed": True, "still_member": False,
             "closed": True, "project_file_lists_sheet": True,
             "file_still_on_disk": True, "reason": "still listed"}
    out = generic_tool("proj_delete_sheet", _RecordingBridge(reply),
                       file_path="C:\\p\\STUBS.SchDoc")
    assert out == reply


# ---------------------------------------------------------------------------
# app_save_all, the handler
# ---------------------------------------------------------------------------

def test_the_save_pass_can_skip_an_unmodified_library():
    body = _main()["SaveOneDocByDocRef"]
    assert "ModifiedFlagOf(ServerDoc)" in body, "the pass never reads the modified flag"
    assert "'.SCHLIB'" in body and "'.PCBLIB'" in body
    assert "SaveSkipCleanLibs" in body and "SaveSkipCleanSheets" in body
    skip = _at(body, "If Skip Then")
    # The write itself lives in WriteDocAndNote since 2026-10-11 (shared
    # with the record sweep); its call is what must sit on the branch.
    forced = _at(body, "WriteDocAndNote(ServerDoc, DiskPath, DocPath, InRecord, ByRecord)")
    assert forced > _at(body[skip:], "Else") + skip, (
        "the forced write (SetModified(True) + DoFileSave) must be on the "
        "NOT-skipped branch; otherwise a skipped library is still written "
        "and its mtime still moves")
    write = _main()["WriteDocAndNote"]
    assert _at(write, "ServerDoc.SetModified(True)") < _at(write, "ServerDoc.DoFileSave('')")
    assert "SaveSkippedPaths" in body, "a skip must be recorded, not silent"
    # One entry per file: a library open in two projects is reached twice.
    # Keyed by DocPathKey since 2026.10.11.02 (both slash spellings of one
    # file were listed live); executed in
    # test_live_save_all_visits_a_file_once.py.
    dedup = _at(body, "If (Pos('|' + DocPathKey(DocPath) + '|', "
                      "'|' + DocPathKey(SaveSkippedPaths) + '|') = 0)")
    assert dedup < _at(body, "SaveSkipped := SaveSkipped + 1;"), (
        "the skip count must sit behind the already-listed check, or a "
        "library open twice is counted and listed twice")
    # Doubt writes: an unreadable flag counts as modified.
    assert "Except WasModified := True;" in body


def test_save_all_reads_its_policy_and_restores_the_default():
    body = _application()["App_SaveAll"]
    assert "Function App_SaveAll(Params : String; RequestId : String)" in body
    assert "ExtractJsonValue(Params, 'include_libraries')" in body
    assert "ExtractJsonValue(Params, 'only_modified')" in body
    save = _at(body, "SaveAllDirty(0)")
    arm = _at(body, "SaveSkipCleanLibs := Not IncludeLibs;")
    assert arm < save, "the policy must be set before the save pass runs"
    restore = body.find("SaveSkipCleanLibs := False;", save)
    assert restore != -1, (
        "SaveSkipCleanLibs is not put back after the pass, so stop_server, "
        "proj_save and proj_close would inherit the skip")
    except_at = _at(body, "Except")
    assert body.find("SaveSkipCleanLibs := False;", except_at) != -1, (
        "an exception in the pass must not leave the skip armed")
    assert '"skipped_unmodified":[' in body
    assert '"include_libraries":' in body and '"documents_skipped":' in body
    # The existing refusal / still-dirty detection stays.
    assert '"saved":false' in body and "CountChangedAges" in body
    assert "CountDirtyDocuments(0)" in body


def test_a_skipped_library_is_flagged_on_every_branch():
    """REVIEW 2026-10-06. In the usual default case the reply is
    saved:true still_dirty:0 with the library mixed into
    skipped_unmodified, and the only warning was on the attempted-nothing
    branch. A library edit whose flag never propagated would be dropped
    under saved:true. It now has its own key and a warning, both carried
    by Policy, which every reply branch appends."""
    body = _application()["App_SaveAll"]
    assert '"libraries_skipped":[' in body
    guard = _at(body, "If LibSkipped > 0 Then")
    warning = _at(body, ',"warning":"')
    assert guard < warning
    assert "Policy := Policy + " in body[guard:warning + 20]
    assert "'.SCHLIB'" in body and "'.PCBLIB'" in body
    branches = body.split("Result := BuildSuccessResponse(")[1:]
    assert len(branches) == 5
    for b in branches:
        assert "+ Policy" in b.split(");", 1)[0], (
            "a reply branch does not carry Policy, so a skipped library "
            "goes unflagged there")


def test_save_all_is_dispatched_with_its_params():
    text = (_ALTIUM / "Application.pas").read_text(encoding="utf-8")
    assert "'save_all':            Result := App_SaveAll(Params, RequestId);" in text


def test_only_save_all_ever_arms_the_skip():
    """Every other caller of the save pass (the compile paths) must keep
    writing everything. proj_save / proj_close save=true arm it ONLY on
    their opt-in only_modified (2026.10.11.03, N4 of the .02 live run):
    SaveProjectMembersReport arms both flags from its OnlyModified
    argument, and both handlers pass a value that is True only for an
    explicit only_modified=true, so their default still writes everything.

    stop_server (App_StopServer) is the one other caller allowed to arm
    them, with literal True: the 2026-10-25 merge ported upstream's
    "detach rewrites nothing clean" onto this fork's record, so a detach
    writes only documents that read modified or that the bridge recorded
    as edited (BridgeEditRecorded overrides the skip in
    SaveOneDocByDocRef). It must put both flags back to False after its
    pass, or every later save inherits the skip."""
    armed = []
    for pas in sorted(_ALTIUM.glob("*.pas")):
        if pas.name == "Altium_MCP.pas":
            continue                       # the build output
        for name, body in load(pas, minimum=1).items():
            for m in re.finditer(r"SaveSkipClean(?:Libs|Sheets)\s*:=\s*([^;]+);", body):
                if m.group(1).strip() != "False":
                    armed.append((pas.name, name, m.group(1).strip()))
    assert armed and all(
        fn == "App_SaveAll"
        or (fn == "SaveProjectMembersReport" and value == "OnlyModified")
        or (fn == "App_StopServer" and value == "True")
        for _, fn, value in armed), armed
    stop = load(_ALTIUM / "Application.pas", minimum=10)["App_StopServer"]
    run = _at(stop, "SaveAllDirty(0)")
    for flag in ("SaveSkipCleanLibs", "SaveSkipCleanSheets"):
        assert _at(stop, f"{flag} := True;") < run, flag
        assert stop.find(f"{flag} := False;", run) != -1, (
            f"stop_server leaves {flag} armed after its pass")
    project = load(_ALTIUM / "Project.pas", minimum=10)
    for handler in ("Proj_Save", "Proj_Close"):
        body = project[handler]
        assert ("OnlyModified := LowerCase(ExtractJsonValue(Params, "
                "'only_modified')) = 'true';") in body, handler
        assert "SaveProjectMembersReport(Project, OnlyModified)" in body, handler

    form = load(_ALTIUM / "StatusForm.pas", minimum=10)
    # Set by ResetSessionGlobals, which StartMCPPump calls (2026.10.12.03).
    assert "ResetSessionGlobals(0);" in form["StartMCPPump"]
    reset = form["ResetSessionGlobals"]
    assert "SaveSkipCleanLibs := False;" in reset
    assert "SaveSkipCleanSheets := False;" in reset


# ---------------------------------------------------------------------------
# app_save_all, the Python payload
# ---------------------------------------------------------------------------

def test_save_all_default_payload_leaves_libraries_to_the_handler(app_tool):
    bridge = _RecordingBridge({"saved": True})
    app_tool("app_save_all", bridge)
    (command, args, kwargs), = bridge.calls
    assert command == "application.save_all"
    assert args and args[0] == {}, (
        "the default call must send an empty policy, which the handler "
        "reads as: skip unmodified libraries, write every sheet")
    assert kwargs.get("timeout") == 60.0


def test_save_all_flags_reach_the_handler(app_tool):
    bridge = _RecordingBridge({"saved": True})
    app_tool("app_save_all", bridge, include_libraries=True, only_modified=True)
    (_, args, _), = bridge.calls
    assert args[0] == {"include_libraries": "true", "only_modified": "true"}


def test_save_all_docstring_states_the_trade_off():
    src = Path(app_mod.__file__).read_text(encoding="utf-8")
    body = src.split("async def app_save_all", 1)[1].split("    @mcp.tool()", 1)[0]
    assert "include_libraries" in body and "lib_save_library" in body
    assert "libraries_skipped" in body and "warning" in body
    flat = " ".join(body.split())
    assert "This writes every open document" not in flat, (
        "stale: by default it no longer writes clean libraries")
    assert "lib_add_footprint_tracks" in body, (
        "the docstring must name the edit paths whose flag does not "
        "propagate, because those are what the default leaves unwritten")


def test_exit_active_command_docstring_is_not_stale():
    src = Path(app_mod.__file__).read_text(encoding="utf-8")
    body = src.split("async def app_exit_active_command", 1)[1].split(
        "    @mcp.tool()", 1)[0]
    flat = " ".join(body.split())
    assert "only when every open document should be written" not in flat
    assert "libraries_skipped" in flat


def test_checkpoint_still_flushes_libraries(app_tool, monkeypatch, tmp_path):
    """A checkpoint is a safety net: it must hold a library edit whose
    modified flag never propagated.

    Since the 2026-10-25 merge it saves the checkpointed project with
    project.save (upstream, issue #94: save_all rewrote other projects'
    sheets to snapshot one) instead of application.save_all with
    include_libraries=true. The safety net holds because proj_save without
    only_modified runs SaveProjectMembersReport(Project, False), which
    writes every open member, libraries included, whatever its flag reads.
    So the request must not carry only_modified."""

    proj = tmp_path / "P"
    proj.mkdir()
    (proj / "P.PrjPcb").write_text("stub", encoding="utf-8")
    from eda_agent import config as config_module
    monkeypatch.setattr(config_module, "config", config_module.config.model_copy(
        update={"workspace_dir": tmp_path / "ws"}))

    class _Bridge(_RecordingBridge):
        async def send_command_async(self, command, *args, **kwargs):
            self.calls.append((command, args, kwargs))
            if command == "project.get_project_path":
                return {"project_dir": str(proj), "project_name": "P.PrjPcb"}
            return {"success": True}

    bridge = _Bridge()
    app_tool("app_checkpoint", bridge)
    assert not [c for c in bridge.calls if c[0] == "application.save_all"]
    save = [c for c in bridge.calls if c[0] == "project.save"]
    assert save, "app_checkpoint no longer saves first"
    (_, args, _), = save
    assert "only_modified" not in args[0]


# ---------------------------------------------------------------------------
# proj_delete_sheet, the leftover [DocumentN] sections (merge review
# 2026-10-25, F1). Upstream's wrapper ran finish_removal on a reply that
# carried the members Altium holds; carrier-local's handler (kept) sent no
# members, so the prune never ran and a sheet removed from the middle of
# the list left a shifted copy of the last member in the file.
# ---------------------------------------------------------------------------

def test_delete_sheet_reply_carries_the_members():
    body = _generic()["Gen_DeleteSheet"]
    assert ",\"members\":[' + Members + ']'" in body
    # Read after the membership is confirmed gone, never before.
    assert _at(body, "StillMember := Gen_SheetIsMember(Project, MemberPath)") \
        < _at(body, "Members := '';")


def _sheet_project(tmp_path, docs):
    for d in set(docs):
        (tmp_path / d).write_bytes(b"x")
    parts = ["[Design]\r\nVersion=1.0\r\n\r\n"]
    for i, d in enumerate(docs, start=1):
        parts.append(f"[Document{i}]\r\nDocumentPath={d}\r\n\r\n")
    path = tmp_path / "P.PrjPcb"
    path.write_bytes("".join(parts).encode("utf-8"))
    return path


def _sheet_reply(tmp_path, path, removed, **over):
    reply = {"success": True, "removed": True, "still_member": False,
             "file_path": str(tmp_path / removed), "was_loaded": True,
             "closed": True, "previously_focused": "",
             "previously_focused_still_open": True,
             "project_path": str(path), "project_saved": True,
             "project_save_route": "document_save",
             "project_file_checked": True, "project_file_lists_sheet": False,
             "file_deleted": False, "file_still_on_disk": True,
             "members": [str(tmp_path / "Main.SchDoc"), str(tmp_path / "H.SchDoc")]}
    reply.update(over)
    return reply


@pytest.fixture()
def _backups(monkeypatch, tmp_path):
    from eda_agent import config as config_mod

    class _Cfg:
        workspace_dir = tmp_path / "ws"

    monkeypatch.setattr(config_mod, "get_config", lambda: _Cfg())


def test_delete_sheet_prunes_a_shifted_copy_of_the_last_member(
        generic_tool, tmp_path, _backups):
    """G removed from Main, G, H: Altium wrote Main, H, H. The removed name
    is gone, so the handler's read-back says success, and the trailing H
    stayed in the file until this prune."""
    path = _sheet_project(tmp_path, ["Main.SchDoc", "H.SchDoc", "H.SchDoc"])
    out = generic_tool("proj_delete_sheet", _RecordingBridge(
        _sheet_reply(tmp_path, path, "G.SchDoc")),
        file_path=str(tmp_path / "G.SchDoc"))
    assert out["stale_sections"]["removed_sections"] == ["Document3"]
    assert "members" not in out
    assert out["success"] is True
    assert b"[Document3]" not in path.read_bytes()


def test_delete_sheet_prune_does_not_override_a_failed_close(
        generic_tool, tmp_path, _backups):
    """finish_removal would force success once the file stops listing the
    sheet; a sheet that did not close is still a failure."""
    path = _sheet_project(tmp_path, ["Main.SchDoc", "H.SchDoc", "G.SchDoc"])
    reply = _sheet_reply(tmp_path, path, "G.SchDoc", success=False,
                         closed=False, project_file_lists_sheet=True,
                         reason="the sheet is still loaded after Client.CloseDocument")
    out = generic_tool("proj_delete_sheet", _RecordingBridge(reply),
                       file_path=str(tmp_path / "G.SchDoc"))
    assert out["stale_sections"]["removed_sections"] == ["Document3"]
    assert out["project_file_lists_sheet"] is False
    assert out["success"] is False
    assert out["reason"] == "the sheet is still loaded after Client.CloseDocument"


def test_delete_sheet_a_pruned_listing_completes_the_removal(
        generic_tool, tmp_path, _backups):
    path = _sheet_project(tmp_path, ["Main.SchDoc", "H.SchDoc", "G.SchDoc"])
    reply = _sheet_reply(tmp_path, path, "G.SchDoc", success=False,
                         project_save_route="not_focused",
                         project_file_lists_sheet=True, reason="manual steps")
    out = generic_tool("proj_delete_sheet", _RecordingBridge(reply),
                       file_path=str(tmp_path / "G.SchDoc"))
    assert out["success"] is True and out["project_file_lists_sheet"] is False
    assert "leftover" in out["reason"]


def test_delete_sheet_without_save_project_leaves_the_file(
        generic_tool, tmp_path, _backups):
    path = _sheet_project(tmp_path, ["Main.SchDoc", "H.SchDoc", "H.SchDoc"])
    before = path.read_bytes()
    reply = _sheet_reply(tmp_path, path, "G.SchDoc", project_save_route="none")
    out = generic_tool("proj_delete_sheet", _RecordingBridge(reply),
                       file_path=str(tmp_path / "G.SchDoc"), save_project=False)
    assert path.read_bytes() == before
    assert "stale_sections" not in out and "members" not in out
