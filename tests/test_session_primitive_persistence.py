# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A primitive created this session must not come back after its delete.

MEASURED LIVE 2026-10-04 (AD26, bridge 2026.10.04.9, carrier.PcbLib,
throwaway footprint ZZ_BRIDGE_TEST): lib_add_footprint_pads added pad 3,
lib_delete_footprint_pads removed it (still_listed 0), and the next
lib_save_library wrote it to the file anyway. With remove_from_board=True
the board half found it, "removed" it and still listed it, and the save
wrote it again. After lib_reload_library the same footprint-only delete
persisted. A disk-loaded pad deletes; a session-created one does not.

No proven script member removes the session-created object's library
board registration, so the fix is in the TOOL: save and reopen the library
first (``reload_first``, default on), then delete. These guards pin:

* the order on the wire: save, reload, THEN the removal, and nothing
  removed when the save does not reach disk or the reopen fails;
* the skip without library_path, said so in the reply;
* the same rule on lib_delete_footprint_primitives and on
  lib_link_3d_model / lib_auto_link_3d_models with replace;
* the handler-side diagnostics one live call needs (target_was_current,
  board_still_listed, marked_modified), read-only.

Each guard names the mutation that makes it fail.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


class _Bridge:
    """Answers save/reload the way the handlers do, and records the order."""

    def __init__(self, save_ok=True, reload_ok=True, replies=None):
        self.commands: list[tuple[str, dict]] = []
        self.save_ok = save_ok
        self.reload_ok = reload_ok
        self.replies = replies or {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.commands.append((command, dict(params or {})))
        if command == "library.save_library":
            return {"library_path": params.get("library_path"),
                    "was_modified": True, "still_modified": False,
                    "file_age_changed": self.save_ok}
        if command == "library.reload_library":
            return {"reloaded": self.reload_ok}
        return self.replies.get(command, {"success": True})

    def names(self):
        return [c for c, _p in self.commands]


def _tools() -> dict:
    from eda_agent.tools.library import register_library_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    return captured


@pytest.fixture
def lib_file(tmp_path):
    path = tmp_path / "carrier.PcbLib"
    path.write_bytes(b"x")
    return str(path)


def _install(monkeypatch, tmp_path, bridge):
    from tests.conftest import install_bridge_fake
    install_bridge_fake(monkeypatch, tmp_path, bridge)


_DELETES = {
    "lib_delete_footprint_pads": (
        dict(footprint_name="FP", pads=[{"designator": "3"}], confirm=True),
        "library.delete_footprint_pads"),
    "lib_delete_footprint_primitives": (
        dict(footprint_name="FP", object_type="pad", include_pads=True,
             confirm=True),
        "library.delete_footprint_primitives"),
}


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_the_library_is_saved_and_reopened_before_the_delete(
        monkeypatch, tmp_path, lib_file, tool):
    """Mutation: send the delete first, or drop reload_first's default."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](library_path=lib_file, **kwargs))
    assert bridge.names() == ["library.save_library",
                              "library.reload_library", command]
    rec = out["reload_first"]
    assert rec["ran"] and rec["saved"] and rec["reloaded"]


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_a_save_that_did_not_land_removes_nothing(monkeypatch, tmp_path,
                                                  lib_file, tool):
    """Mutation: reload (or delete) after a save that did not reach disk.

    Reopening a dirty library raises Altium's Unsaved Changes prompt and
    blocks the bridge; deleting from it would be the measured failure.
    """
    bridge = _Bridge(save_ok=False)
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](library_path=lib_file, **kwargs))
    assert out["ok"] is False and "reload_first" in out["reason"]
    assert bridge.names() == ["library.save_library"]
    assert out["reload_first"]["saved"] is False


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_a_reopen_that_failed_removes_nothing(monkeypatch, tmp_path,
                                              lib_file, tool):
    bridge = _Bridge(reload_ok=False)
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](library_path=lib_file, **kwargs))
    assert out["ok"] is False and "did not reopen" in out["reason"]
    assert command not in bridge.names()


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_without_a_library_path_it_is_skipped_and_said_so(
        monkeypatch, tmp_path, tool):
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](**kwargs))
    assert bridge.names() == [command]
    assert out["reload_first"]["ran"] is False
    assert "library_path" in out["reload_first"]["skipped"]


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_reload_first_false_is_the_old_single_call(monkeypatch, tmp_path,
                                                   lib_file, tool):
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](library_path=lib_file,
                                     reload_first=False, **kwargs))
    assert bridge.names() == [command]
    assert "reload_first" not in out


def test_an_unconfirmed_primitive_delete_saves_nothing(monkeypatch, tmp_path,
                                                       lib_file):
    """A delete the handler will refuse must not save and reopen first."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()["lib_delete_footprint_primitives"](
        footprint_name="FP", object_type="track", library_path=lib_file))
    assert "library.save_library" not in bridge.names()


def test_a_replace_link_reopens_first_and_a_plain_link_does_not(
        monkeypatch, tmp_path, lib_file):
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    tools = _tools()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="FP", model_path="m.step", library_path=lib_file,
        replace=True))
    assert bridge.names() == ["library.save_library",
                              "library.reload_library",
                              "library.link_3d_model"]
    bridge.commands.clear()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="FP", model_path="m.step", library_path=lib_file))
    assert bridge.names() == ["library.link_3d_model"], (
        "a link that replaces nothing has nothing to settle")


def test_auto_link_reopens_once_before_the_first_replace(monkeypatch,
                                                         tmp_path, lib_file):
    models = tmp_path / "models"
    models.mkdir()
    for name in ("SOIC8", "QFN16"):
        (models / f"{name}.step").write_text("x")
    bridge = _Bridge(replies={"library.get_footprints": {
        "footprints": [{"name": "SOIC8"}, {"name": "QFN16"}]}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(models), library_path=lib_file, replace=True))
    names = bridge.names()
    assert names[:2] == ["library.save_library", "library.reload_library"]
    assert names.count("library.save_library") == 1
    assert names.count("library.link_3d_model") == 2
    assert out["reload_first"]["reloaded"] is True


# ------------------------------------------------------------- Pascal side

@pytest.fixture(scope="module")
def lib() -> dict[str, str]:
    return load(_PAS / "Library.pas", minimum=100)


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_the_delete_reports_what_one_live_call_needs(lib, name):
    """Mutation: drop any of the three diagnostics from the reply."""
    body = lib[name]
    for key in ("board_still_listed", "target_was_current",
                "marked_modified"):
        assert key in body, f"{name} no longer reports {key}"
    assert "LibBoardStillListsCount(PcbLib.Board, Entries)" in body


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_target_was_current_is_read_never_written(lib, name):
    """Mutation: make the footprint current to measure it.

    The default delete must keep the base build's Altium calls: it may
    READ CurrentComponent, never assign it (only LibMakeCurrentVerified,
    behind FromBoard, does that).
    """
    body = lib[name]
    assert "PcbLib.CurrentComponent :=" not in body
    assert re.search(r"TargetWasCurrent\s*:=\s*\(PcbLib\.CurrentComponent\s*"
                     r"=\s*Target\)", body)


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_the_delete_marks_the_library_by_its_own_path_too(lib, name):
    """Mutation: mark only the caller's path again.

    Measured 2026-10-04: after a delete, lib_save_library read
    was_modified FALSE. The add handlers mark PcbLib.Board.FileName, the
    path Altium itself holds, and the delete now does both and reads the
    flag back.
    """
    body = lib[name]
    assert "MarkDocDirtyByPath(PcbLib.Board.FileName)" in body
    assert "MarkDocDirtyByPath(LibPath)" in body
    assert "DocIsModified(PcbLib.Board.FileName)" in body


def test_the_board_count_is_read_only(lib):
    """Mutation: remove from the board inside the count."""
    body = lib["LibBoardStillListsCount"]
    assert "RemovePCBObject" not in body
    assert "LibBoardListsAddress(Board, Oid, Addr)" in body


def test_the_replace_reports_what_the_board_still_holds(lib):
    body = lib["Lib_Link3DModel"]
    remove = body.index("Body3D_RemoveAll(")
    count = body.index("LibBoardStillListsCount(PcbLib.Board, RemovedEntries)")
    add = body.index("Footprint.AddPCBObject(Body)")
    assert remove < count < add, (
        "count what the board holds of the REPLACED bodies, before the new "
        "body joins it")
    assert "JsonInt('board_still_listed', BoardStill)" in body


def test_the_version_was_bumped_for_these_edits():
    main = (_PAS / "Main.pas").read_text(encoding="utf-8-sig")
    version = re.search(r"SCRIPT_VERSION\s*=\s*'([^']+)'", main).group(1)
    assert tuple(int(x) for x in version.split(".")) >= (2026, 10, 4, 10), (
        version)


# ------------------------------------------------- 3D placement, Python side

def test_a_body_off_its_footprint_is_not_reported_ok(monkeypatch, tmp_path):
    """Mutation: return the handler's reply as-is when placement_ok is false.

    The 2026-10-04 body sat 50,000 mil from its pads while the reply's own
    check passed; the wrapper must turn the new check into ok=false.
    """
    reply = {"success": True, "placement_ok": False,
             "placement": {"ok": False, "gap_mils": 49800.0,
                           "problem": "the body's box is 49800 mil from the "
                                      "footprint's pads_bbox"}}
    bridge = _Bridge(replies={"library.link_3d_model": reply})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path="m.step"))
    assert out["ok"] is False and "49800" in out["reason"]


def test_auto_link_lists_a_misplaced_body_as_failed(monkeypatch, tmp_path):
    models = tmp_path / "models"
    models.mkdir()
    (models / "SOIC8.step").write_text("x")
    bridge = _Bridge(replies={
        "library.get_footprints": {"footprints": [{"name": "SOIC8"}]},
        "library.link_3d_model": {"placement_ok": False, "placement": {
            "ok": False, "problem": "far"}}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(models)))
    assert out["linked"] == []
    assert out["failed"][0]["code"] == "PLACEMENT_FAR_FROM_FOOTPRINT"
