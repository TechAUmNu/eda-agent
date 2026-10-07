# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""THE PROJECT DOCUMENT'S OWN SAVE WRITES A REMOVED SHEET BACK IN.

Measured live 2026-10-07 (Altium 26.10.1.5, the first bridge build of that
date) while running RELEASE_VERIFICATION step 33. proj_delete_sheet removed
a sheet that the .PrjPcb on disk already named: membership re-read false,
sheet closed, then SetModified + DoFileSave on the project's own server
document. The file's timestamp MOVED and it STILL NAMED the sheet. Twice:
once a second after another project save, once isolated by four seconds.
WorkspaceManager:SaveObject ObjectKind=FocusedProject, run straight after,
wrote the file without the sheet, byte-identical to the committed project,
and rewrote no sheet.

Every earlier live delete had passed only because the sheet was never in
the file: proj_add_sheet did not write the project until that same build,
so the removal route had never met a sheet the file listed. The handler
stayed honest (success false, delete_file refused, reason given) but could
not finish the job.

The same document save DID write a new member after DM_AddSourceDocument,
so Gen_WriteProjectFile keeps it first (it reaches any open project, focused
or not), reads the file back, and falls back to SaveObject on the focused
project only when the file disagrees. A review of the first cut added: the
add's read-back matches the EXACT path (a same-named sheet elsewhere must
not confirm it), the add writes the project only for a sheet on disk, and
when closing the focused sheet moves the focus out of the project, another
of its sheets is shown first - but only for a project that was focused when
the call began.

Pascal guards, as in the sibling files. The route guards fail against the
source before the fallback existed.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic():
    return load(_ALTIUM / "Generic.pas", minimum=100)


def _at(body: str, needle: str, start: int = 0) -> int:
    pos = body.find(needle, start)
    assert pos != -1, f"{needle!r} is not in the routine"
    return pos


_WANTED = "Gen_ProjectFileWanted(ProjFile, MemberPath, WantListed)"


def test_the_document_save_is_judged_by_reading_the_file():
    helper = _generic()["Gen_WriteProjectFile"]
    save = _at(helper, "ProjDoc.DoFileSave(''")
    first = _at(helper, _WANTED)
    assert save < first
    assert re.search(r"Result := 'document_save';\s*Exit;", helper), (
        "a confirmed document save must RETURN; without the Exit the "
        "fallback would run on every call")


def test_a_disagreeing_file_falls_back_to_save_object_on_the_focused_project():
    """THE MEASURED DEFECT. Without the fallback a removal can never reach
    the file, because the document save writes the removed member back."""
    helper = _generic()["Gen_WriteProjectFile"]
    first = _at(helper, _WANTED)
    kind = _at(helper, "AddStringParameter('ObjectKind', 'FocusedProject');")
    run = _at(helper, "RunProcess('WorkspaceManager:SaveObject');")
    second = _at(helper, _WANTED, first + 1)
    assert first < kind < run < second, (
        "the fallback runs only after the first read-back disagrees, and its "
        "own result is read back too")
    assert "ResetParameters;" in helper[first:run]
    tail = helper[second:]
    assert "Result := 'save_object';" in tail
    assert "Result := 'save_object_refocused'" in tail


def test_the_fallback_never_saves_some_other_project():
    """SaveObject ObjectKind=FocusedProject saves whatever project has the
    focus. The guard must sit before it AND end the routine."""
    helper = _generic()["Gen_WriteProjectFile"]
    guard = _at(helper, "If UpperCase(FocusedPath) <> UpperCase(ProjFile) Then\n")
    assert guard < _at(helper, "RunProcess('WorkspaceManager:SaveObject');")
    assert re.search(r"Result := 'not_focused';\s*Exit;", helper[guard:]), (
        "the not_focused answer must RETURN before the fallback; without the "
        "Exit, SaveObject would save whichever project has the focus")
    assert helper.find("Result := 'not_confirmed';") < guard, (
        "the default answer must be the refusal, set before any route runs")


def test_refocus_only_for_a_project_that_was_focused_at_the_start():
    """Deleting the FOCUSED sheet closes it and Altium moves the focus,
    possibly out of the project; the fallback would then answer
    not_focused for the caller's own project. Showing another loaded sheet
    of the project brings it back - but never for a project_path the caller
    did not have focused."""
    helper = _generic()["Gen_WriteProjectFile"]
    cond = _at(helper, "If (UpperCase(FocusedPath) <> UpperCase(ProjFile)) And MayRefocus")
    show = _at(helper, "Client.ShowDocument(Shown)")
    guard = _at(helper, "If UpperCase(FocusedPath) <> UpperCase(ProjFile) Then\n")
    assert cond < show < guard < _at(helper, "RunProcess('WorkspaceManager:SaveObject');")
    assert "If (P = '') Or (UpperCase(P) = UpperCase(MemberPath)) Then Continue;" in helper, (
        "the sheet being removed is never the one shown")

    delete = _generic()["Gen_DeleteSheet"]
    decided = _at(delete, "WasFocusedProject := UpperCase(FocusedProj.DM_ProjectFullPath) = UpperCase(ProjFile)")
    assert decided < _at(delete, "Client.CloseDocument("), (
        "whether the project was focused must be read BEFORE the close moves "
        "the focus")
    assert "Gen_WriteProjectFile(Workspace, Project, ProjFile, MemberPath, 0, WasFocusedProject)" in delete


def test_each_direction_uses_the_matcher_that_errs_toward_refusing():
    wanted = _generic()["Gen_ProjectFileWanted"]
    assert "Gen_ProjectFileStateExact(ProjFile, MemberPath) = 1" in wanted, (
        "an add needs the exact path, or a same-named sheet in another folder "
        "confirms a write that never happened")
    assert "Gen_ProjectFileState(ProjFile, MemberPath) = 0" in wanted, (
        "a removal keeps the loose matcher: a stray match reads 'still "
        "listed' and blocks delete_file, the safe direction")

    exact = _generic()["Gen_ProjectFileStateExact"]
    assert "If Value = Want Then Result := 1;" in exact
    assert "Tail" not in exact, "the exact matcher must not accept a folder suffix"
    unknown = _at(exact, "Result := 2;")
    not_listed = _at(exact, "Result := 0;")
    for exit_on in ("If ProjectPath = '' Then Exit;",
                    "If Not FileExists(ProjectPath) Then Exit;",
                    "If Content = '' Then Exit;",
                    "If Pos('DOCUMENTPATH=', Content) = 0 Then Exit;"):
        assert unknown < _at(exact, exit_on) < not_listed


def test_both_handlers_write_through_the_helper_and_name_the_route():
    gen = _generic()
    delete = gen["Gen_DeleteSheet"]
    add = gen["Gen_AddSheet"]
    assert "DoFileSave" not in delete, (
        "Gen_DeleteSheet saves the project itself again instead of through "
        "the helper, which is the route measured to write a removed sheet back")
    assert add.count("DoFileSave") == 1, (
        "Gen_AddSheet's only DoFileSave is the new SHEET's own save")
    assert _at(add, "ServerDoc.DoFileSave('')") < _at(add, "Project.DM_AddSourceDocument(NewDocPath)")
    assert "Gen_WriteProjectFile(Workspace, Project, ProjFile, NewDocPath, 1, True)" in add
    for name, body in (("Gen_DeleteSheet", delete), ("Gen_AddSheet", add)):
        assert '"project_save_route":"' in body, f"{name} lost project_save_route"


def test_the_add_writes_the_project_only_for_a_sheet_on_disk():
    """REVIEW. The write was gated on membership alone, so a sheet whose
    own save failed was persisted into the project file as a missing file."""
    add = _generic()["Gen_AddSheet"]
    assert "If SaveProject And Saved And Added And (ProjFile <> '') Then" in add
    assert "FileState := Gen_ProjectFileStateExact(ProjFile, NewDocPath);" in add


def test_an_unreachable_fallback_says_what_to_do():
    delete = _generic()["Gen_DeleteSheet"]
    assert "SaveRoute = 'not_focused'" in delete
    assert "ObjectKind=FocusedProject" in delete, (
        "the reason must name the save that works, so the caller can run it")
    assert "proj_save does not write the project file" in delete, (
        "the reason must not send the caller to proj_save, which cannot drop it")


def test_the_helpers_sit_between_the_file_check_and_the_handlers():
    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    names = ("Gen_ProjectFileState(", "Gen_ProjectFileStateExact(",
             "Gen_ProjectFileWanted(", "Gen_WriteProjectFile(",
             "Gen_AddSheet(", "Gen_DeleteSheet(")
    pos = [text.index("Function " + n) for n in names]
    assert pos == sorted(pos), "DelphiScript wants each declared before use"
    for n in names:
        assert text.count("Function " + n) == 1, n
