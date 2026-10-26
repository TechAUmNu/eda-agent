# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""proj_add_sheet SAID saved:true AND LEFT THE PROJECT FILE UNWRITTEN.

Measured live 2026-10-07 (Altium 26.10.1.5, bridge 2026.10.06.02), while
verifying the proj_delete_sheet fix: proj_add_sheet name=ZZ_T10 replied
``{success: true, saved: true, added_to_project: true}``, but the .PrjPcb
kept the timestamp of an earlier save and did not name the sheet. "saved"
was the new SHEET's DoFileSave; DM_AddSourceDocument changes membership in
memory only. A reload before some other project save would have dropped
the sheet. proj_delete_sheet's own read-back is what showed it: deleting
the fresh sheet with save_project=false answered project_file_lists_sheet
false, because the file had never named it.

Two more holes in the same handler: membership was asserted (Added :=
True after the call) rather than re-read, and nothing looked at the
target path first, so a name whose file existed would have had it
replaced by a blank sheet.

As in test_sheet_delete_and_save_all_policy, the Pascal cannot run here:
the guards pin the calls, their order and the read-back fields. Measured
against the 2026.10.06.02 source: every handler guard and the
save_project payload test fail there (7 of 9); the default payload and
the partial-failure passthrough are contract guards and pass on both.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import generic as generic_mod
from eda_agent.tools.registry import ToolRegistry

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _generic():
    return load(_ALTIUM / "Generic.pas", minimum=100)


def _at(body: str, needle: str) -> int:
    pos = body.find(needle)
    assert pos != -1, f"{needle!r} is not in the handler"
    return pos


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------

def test_add_sheet_refuses_an_existing_file_before_creating_anything():
    body = _generic()["Gen_AddSheet"]
    refuse = _at(body, "If FileExists(NewDocPath) Then")
    assert "'FILE_EXISTS'" in body
    assert refuse < _at(body, "Client.OpenNewDocument("), (
        "the existence check must come before the new document is opened at "
        "that path, or the save that follows replaces the file")


def test_add_sheet_rereads_membership_instead_of_asserting_it():
    body = _generic()["Gen_AddSheet"]
    add = _at(body, "Project.DM_AddSourceDocument(NewDocPath)")
    reread = _at(body, "Gen_SheetIsMember(Project, NewDocPath)")
    assert add < reread
    assert "Added := True;" not in body, (
        "membership was written as True after the call, never looked at")


def test_add_sheet_writes_the_project_file_and_reads_it_back():
    """THE MEASURED DEFECT."""
    body = _generic()["Gen_AddSheet"]
    save = _at(body, "Gen_WriteProjectFile(Workspace, Project, ProjFile, NewDocPath, 1, True)")
    check = _at(body, "Gen_ProjectFileStateExact(ProjFile, NewDocPath)")
    assert _at(body, "Gen_SheetIsMember(Project, NewDocPath)") < save < check, (
        "the project file is written after membership is confirmed and read "
        "back after it is written")
    for field in ('"sheet_saved":', '"project_saved":',
                  '"project_file_checked":', '"project_file_lists_sheet":'):
        assert field in body, f"the reply lost {field}"
    assert "LowerCase(ExtractJsonValue(Params, 'save_project')) <> 'false'" in body, (
        "saving the project must be the default")


def test_add_sheet_unread_project_file_is_null_not_false():
    body = _generic()["Gen_AddSheet"]
    assert "FileChecked := FileState <> 2;" in body
    assert "ListedOnDisk := FileState = 1;" in body
    assert "Else ListedJson := 'null';" in body


def test_add_sheet_success_is_computed_not_written():
    body = _generic()["Gen_AddSheet"]
    assert '"success":true' not in body
    assert "BoolToJsonStr(Success)" in body
    assert "Success := Saved And Added;" in body
    assert ("If SaveProject And ((Not FileChecked) Or (Not ListedOnDisk)) "
            "Then Success := False;") in body, (
        "with save_project, success needs a project file that was read and "
        "names the new sheet")


def test_the_shared_helpers_are_declared_before_both_handlers():
    """DelphiScript wants a function declared before use, and the helpers
    used to sit between the two handlers, after Gen_AddSheet."""
    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    member = text.index("Function Gen_SheetIsMember(")
    state = text.index("Function Gen_ProjectFileState(")
    add = text.index("Function Gen_AddSheet(")
    delete = text.index("Function Gen_DeleteSheet(")
    assert member < add and state < add and add < delete
    assert text.count("Function Gen_SheetIsMember(") == 1
    assert text.count("Function Gen_ProjectFileState(") == 1


# ---------------------------------------------------------------------------
# the Python payload
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
    def run(tool, bridge, **kwargs):
        monkeypatch.setattr(generic_mod, "get_bridge", lambda: bridge)
        fn = _tools(generic_mod.register_generic_tools)[tool]
        return asyncio.run(fn(**kwargs))
    return run


def test_add_sheet_default_payload(generic_tool):
    bridge = _RecordingBridge()
    generic_tool("proj_add_sheet", bridge, name="ZZ")
    (command, args, _), = bridge.calls
    assert command == "generic.add_sheet"
    assert args[0] == {"name": "ZZ"}, (
        "the default (save the project) must reach the handler as ITS default")


def test_add_sheet_can_leave_the_project_file_alone(generic_tool):
    bridge = _RecordingBridge()
    generic_tool("proj_add_sheet", bridge, name="ZZ", save_project=False)
    (_, args, _), = bridge.calls
    assert args[0] == {"name": "ZZ", "save_project": "false"}


def test_add_sheet_passes_a_partial_failure_through(generic_tool):
    reply = {"success": False, "saved": True, "sheet_saved": True,
             "added_to_project": True, "project_file_lists_sheet": False,
             "reason": "not named on disk"}
    out = generic_tool("proj_add_sheet", _RecordingBridge(reply), name="ZZ")
    assert out == reply
