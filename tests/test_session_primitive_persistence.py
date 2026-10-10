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
first (``reload_first``, default on), park the footprint so it is not the
current one (a review: the evidence fits "the save rebuilds the CURRENT
footprint" too), then delete, then save again and read the FILE
(``verify_on_disk``). These guards pin:

* the order on the wire: read-only prechecks, save, reload, THEN the
  removal, and nothing removed when the save does not reach disk or the
  reopen fails;
* a delete the handler would refuse (unknown object type, missing
  footprint, unresolvable selector, missing STEP) costs no save;
* the focused .PcbLib is used when library_path is empty, and a skip is
  said at the TOP of the reply;
* park_current is asked for by default, with or without reload_first
  (measured 2026-10-05: being CURRENT is enough to bring a removed
  primitive back), never with remove_from_board;
* the same rule on lib_delete_footprint_primitives and on
  lib_link_3d_model / lib_auto_link_3d_models with replace (which replace
  through the delete, parked, with or without reload_first);
* the handler-side diagnostics one live call needs (target_was_current,
  parked_elsewhere, target_current_during_removal, board_still_listed,
  marked_modified), read-only.

The on-disk verification itself is in tests/test_delete_verify_on_disk.py.
Each guard names the mutation that makes it fail.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: Read-only calls the tools may make before the save.
_READS = {"library.get_footprints", "library.get_pad_geometry",
          "application.get_active_document",
          "library.get_footprint_bodies"}


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
        reply = self.replies.get(command, {"success": True})
        if isinstance(reply, Exception):
            raise reply
        return reply

    def names(self):
        return [c for c, _p in self.commands]

    def writes(self):
        """The command order without the read-only prechecks."""
        return [c for c in self.names() if c not in _READS]

    def params(self, command):
        return [p for c, p in self.commands if c == command]


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


@pytest.fixture
def step_file(tmp_path):
    path = tmp_path / "m.step"
    path.write_text("x")
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
    assert bridge.writes() == ["library.save_library",
                               "library.reload_library", command]
    first_save = bridge.names().index("library.save_library")
    assert all(c in _READS for c in bridge.names()[:first_save]), (
        "only read-only prechecks may run before the save")
    rec = out["reload_first"]
    assert rec["ran"] and rec["saved"] and rec["reloaded"]


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_the_footprint_is_parked_by_default_never_with_the_board_half(
        monkeypatch, tmp_path, lib_file, tool):
    """Mutation: drop park_current from either route, or send it with
    remove_from_board (whose board half needs the footprint current).

    Measured 2026-10-05 (step 31 item 3b): a disk-loaded pad removed while
    its footprint was CURRENT came back with the save, so the no-reload
    route parks too."""
    kwargs, command = _DELETES[tool]
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()[tool](library_path=lib_file, **kwargs))
    assert bridge.params(command)[-1].get("park_current") == "true"

    bridge.commands.clear()
    asyncio.run(_tools()[tool](library_path=lib_file, reload_first=False,
                               **kwargs))
    assert bridge.params(command)[-1].get("park_current") == "true"

    bridge.commands.clear()
    asyncio.run(_tools()[tool](library_path=lib_file, remove_from_board=True,
                               **kwargs))
    sent = bridge.params(command)[-1]
    assert "park_current" not in sent and sent["remove_from_board"] == "true"


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
    assert bridge.writes() == ["library.save_library"]
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
def test_without_a_library_path_or_focused_pcblib_it_is_skipped_loudly(
        monkeypatch, tmp_path, tool):
    """Mutation: bury the skip in reload_first only.

    A path-less delete is the common one; when no focused .PcbLib resolves
    either, the reply must say at the top that the measured failure is
    still possible.
    """
    bridge = _Bridge(replies={"application.get_active_document":
                              {"file_path": r"C:\x\sheet.SchDoc"}})
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](**kwargs))
    assert bridge.writes() == [command]
    assert out["reload_first"]["ran"] is False
    assert "not a .PcbLib" in out["reload_first"]["skipped"]
    assert out["warning"].startswith("reload_first was SKIPPED")
    assert out["note"].startswith("reload_first was SKIPPED")


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_without_a_library_path_the_focused_pcblib_is_used(
        monkeypatch, tmp_path, lib_file, tool):
    """Mutation: skip reload_first whenever library_path is empty."""
    bridge = _Bridge(replies={"application.get_active_document":
                              {"file_path": lib_file}})
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](**kwargs))
    assert bridge.writes() == ["library.save_library",
                               "library.reload_library", command]
    assert bridge.params("library.save_library")[0]["library_path"] == \
        lib_file
    assert bridge.params(command)[-1]["library_path"] == lib_file, (
        "the delete must be pinned to the library that was reopened")
    assert out["reload_first"]["ran"] is True


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


def test_an_unknown_object_type_is_refused_before_anything(monkeypatch,
                                                           tmp_path,
                                                           lib_file):
    """Mutation: let the handler refuse it, after the save and reopen."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_delete_footprint_primitives"](
        footprint_name="FP", object_type="trak", confirm=True,
        library_path=lib_file))
    assert out["ok"] is False and "unknown object_type" in out["reason"]
    assert bridge.names() == []
    for word in ("eTrackObject", "component body", "3D_body", "Region"):
        bridge.commands.clear()
        asyncio.run(_tools()["lib_delete_footprint_primitives"](
            footprint_name="FP", object_type=word, confirm=True,
            library_path=lib_file, reload_first=False))
        assert bridge.names() == ["library.delete_footprint_primitives"], word


def test_the_object_type_words_match_the_pascal_resolver():
    """Mutation: add a word to ObjectTypeFromStringPCB and not here (or
    the reverse): the Python check would then refuse a valid type, or let
    an invalid one cost a save."""
    from eda_agent.tools.library import _PCB_OBJECT_TYPE_WORDS

    utils = load(_PAS / "Utils.pas", minimum=10)
    body = utils["ObjectTypeFromStringPCB"]
    words = set(re.findall(r"N\s*=\s*'([^']+)'", body))
    assert words == set(_PCB_OBJECT_TYPE_WORDS)


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_a_missing_footprint_is_refused_before_the_save(monkeypatch,
                                                        tmp_path, lib_file,
                                                        tool):
    """Mutation: drop the read-only footprint precheck."""
    bridge = _Bridge(replies={"library.get_footprints": {
        "footprints": [{"name": "SOIC8"}, {"name": "FP2"}]}})
    _install(monkeypatch, tmp_path, bridge)
    kwargs, command = _DELETES[tool]
    out = asyncio.run(_tools()[tool](library_path=lib_file, **kwargs))
    assert out["ok"] is False and "is not in" in out["reason"]
    assert "library.save_library" not in bridge.names()


def test_an_unresolvable_pad_selector_is_refused_before_the_save(
        monkeypatch, tmp_path, lib_file):
    """Mutation: let the handler refuse SELECTOR_UNRESOLVED after the save.

    Same rules as the handler: no pad named so, several named so with no
    index, an index that does not exist.
    """
    pads = {"pads": [{"name": "1"}, {"name": "2"}, {"name": "2"}]}
    bridge = _Bridge(replies={
        "library.get_footprints": {"footprints": [{"name": "FP"}]},
        "library.get_pad_geometry": pads})
    _install(monkeypatch, tmp_path, bridge)
    tool = _tools()["lib_delete_footprint_pads"]
    for selector, fragment in (({"designator": "9"}, 'no pad named "9"'),
                               ({"designator": "2"}, "pass index"),
                               ({"designator": "2", "index": 2},
                                "index 2 asked for")):
        bridge.commands.clear()
        out = asyncio.run(tool(footprint_name="fp", pads=[selector],
                               confirm=True, library_path=lib_file))
        assert out["ok"] is False and fragment in out["reason"], out
        assert "library.save_library" not in bridge.names()
    bridge.commands.clear()
    out = asyncio.run(tool(footprint_name="fp",
                           pads=[{"designator": "2", "all": True}],
                           confirm=True, library_path=lib_file))
    assert "library.delete_footprint_pads" in bridge.names()
    assert out["precheck"]["selected"] == {"2": 2}
    geo = bridge.params("library.get_pad_geometry")[0]
    assert geo["footprint_name"] == "FP", (
        "the pad read must use the library's own spelling of the name")


def test_a_replace_link_reopens_then_deletes_parked_then_links(
        monkeypatch, tmp_path, lib_file, step_file):
    """Mutation: let the handler's own replace remove the old bodies.

    It removes while the footprint is CURRENT (it must be, to add), the
    untested combination; once reload_first has run the old bodies go
    through the delete with park_current, and the link runs without
    replace, inheriting the replaced body's layer.
    """
    bridge = _Bridge(replies={
        "library.get_footprint_bodies": {"bodies": [
            {"layer": "Mechanical 13"}]},
        "library.delete_footprint_primitives": {"removed": 1,
                                                "still_listed": 0,
                                                "parked_elsewhere": True},
        "library.link_3d_model": {"success": True, "applied": {
            "layer_source": "argument"}, "replaced": {"requested": False}}})
    _install(monkeypatch, tmp_path, bridge)
    tools = _tools()
    out = asyncio.run(tools["lib_link_3d_model"](
        component_name="FP", model_path=step_file, library_path=lib_file,
        replace=True))
    assert bridge.writes() == ["library.save_library",
                               "library.reload_library",
                               "library.delete_footprint_primitives",
                               "library.link_3d_model"]
    delete = bridge.params("library.delete_footprint_primitives")[0]
    assert delete["object_type"] == "componentbody"
    assert delete["park_current"] == "true" and delete["confirm"] == "true"
    link = bridge.params("library.link_3d_model")[0]
    assert link["replace"] == "false"
    assert link["layer"] == "Mechanical 13"
    assert out["replaced"]["route"] == "delete_then_link"
    assert out["replaced"]["removed_bodies"] == 1
    assert out["applied"]["layer_source"] == "replaced body"

    bridge.commands.clear()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="FP", model_path="m.step", library_path=lib_file))
    assert bridge.names() == ["library.link_3d_model"], (
        "a link that replaces nothing has nothing to settle")


def test_a_failed_link_after_the_delete_says_how_to_restore(
        monkeypatch, tmp_path, lib_file, step_file):
    from eda_agent.bridge.exceptions import AltiumCommandError

    bridge = _Bridge(replies={
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": AltiumCommandError(
            "bad STEP", code="MODEL_LOAD_FAILED")})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=step_file, library_path=lib_file,
        replace=True))
    assert out["ok"] is False and out["success"] is False
    assert out["code"] == "MODEL_LOAD_FAILED"
    assert "lib_reload_library" in out["reason"]


def test_a_missing_step_is_refused_before_the_save(monkeypatch, tmp_path,
                                                   lib_file):
    """Mutation: let MODEL_LOAD_FAILED come back after the save and reopen."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=str(tmp_path / "nope.step"),
        library_path=lib_file, replace=True))
    assert out["ok"] is False and "not a file" in out["reason"]
    assert bridge.names() == []


def test_a_replace_on_a_missing_footprint_is_refused_before_the_save(
        monkeypatch, tmp_path, lib_file, step_file):
    """Mutation: drop the footprint precheck from lib_link_3d_model.

    The link handler refuses NO_FOOTPRINT only after reload_first has
    saved and reopened the whole library.
    """
    bridge = _Bridge(replies={"library.get_footprints": {
        "footprints": [{"name": "SOIC8"}]}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="NOPE", model_path=step_file, library_path=lib_file,
        replace=True))
    assert out["ok"] is False and "not in" in out["reason"]
    assert bridge.writes() == []


def test_a_replace_sends_the_library_spelling_to_delete_and_link(
        monkeypatch, tmp_path, lib_file, step_file):
    """Mutation: send the caller's spelling on.

    The delete matches a footprint name case-insensitively and the link
    handler exactly: a case-different name would remove the old bodies
    and then fail to add the new one.
    """
    bridge = _Bridge(replies={
        "library.get_footprints": {"footprints": [{"name": "SOIC8"}]},
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": {"success": True}})
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()["lib_link_3d_model"](
        component_name="soic8", model_path=step_file, library_path=lib_file,
        replace=True))
    assert bridge.params("library.delete_footprint_primitives")[0][
        "footprint_name"] == "SOIC8"
    assert bridge.params("library.link_3d_model")[0][
        "component_name"] == "SOIC8"


def test_a_replace_without_a_footprint_name_skips_the_reopen(
        monkeypatch, tmp_path, lib_file, step_file):
    """A reopen changes which footprint is current, and an empty
    component_name targets the current one: reopening would move the
    link to another footprint."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="", model_path=step_file, library_path=lib_file,
        replace=True))
    assert "library.save_library" not in bridge.names()
    assert "component_name is empty" in out["reload_first"]["skipped"]
    assert bridge.params("library.link_3d_model")[0]["replace"] == "true"


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
    writes = bridge.writes()
    assert writes[:2] == ["library.save_library", "library.reload_library"]
    # reload_first's save, then ONE verifying save after the last link.
    assert writes.count("library.save_library") == 2
    assert writes[-1] == "library.save_library"
    assert "library.reload_library" not in writes[2:]
    assert writes.count("library.link_3d_model") == 2
    assert writes.count("library.delete_footprint_primitives") == 2
    assert all(p["replace"] == "false"
               for p in bridge.params("library.link_3d_model"))
    assert out["reload_first"]["reloaded"] is True


def test_auto_link_saves_nothing_when_no_footprint_matches(monkeypatch,
                                                           tmp_path,
                                                           lib_file):
    """Mutation: run reload_first before matching models to footprints."""
    models = tmp_path / "models"
    models.mkdir()
    (models / "OTHER.step").write_text("x")
    bridge = _Bridge(replies={"library.get_footprints": {
        "footprints": [{"name": "SOIC8"}]}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(models), library_path=lib_file, replace=True))
    assert "library.save_library" not in bridge.names()
    assert out["linked"] == [] and "reload_first" not in out


# ------------------------------------------------------------- Pascal side

@pytest.fixture(scope="module")
def lib() -> dict[str, str]:
    return load(_PAS / "Library.pas", minimum=100)


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_the_delete_reports_what_one_live_call_needs(lib, name):
    """Mutation: drop any of the diagnostics from the reply."""
    body = lib[name]
    for key in ("board_still_listed", "target_was_current",
                "parked_elsewhere", "target_current_during_removal",
                "marked_modified"):
        assert key in body, f"{name} no longer reports {key}"
    assert "LibBoardStillListsCount(PcbLib.Board, Entries)" in body


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_target_was_current_is_read_and_the_park_is_opt_in(lib, name):
    """Mutation: assign CurrentComponent inline, or park unasked.

    The delete may READ CurrentComponent. It changes it only through the
    shipped helpers: LibMakeCurrentVerified behind remove_from_board, and
    LibParkCurrentElsewhere behind park_current (which the Python side
    sends only after reload_first ran), never both.
    """
    body = lib[name]
    assert "PcbLib.CurrentComponent :=" not in body
    assert re.search(r"TargetWasCurrent\s*:=\s*\(PcbLib\.CurrentComponent\s*"
                     r"=\s*Target\)", body)
    assert re.search(r"If\s+ParkWanted\s+And\s+\(Not\s+FromBoard\)\s+And\s+"
                     r"TargetWasCurrent\s+Then\s+Parked\s*:=\s*"
                     r"LibParkCurrentElsewhere\(PcbLib,\s*Target\)", body)
    assert "ExtractJsonValue(Params, 'park_current')" in body


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPads",
                                  "Lib_DeleteFootprintPrimitives"])
def test_the_board_is_walked_only_while_the_footprint_is_current(lib, name):
    """Mutation: walk the board for a footprint that is not current.

    The board holds only the current footprint's primitives, so that walk
    is 0 by construction (a 0 that proves nothing, and a walk over objects
    the footprint has just released). It is null in the reply instead.
    """
    body = lib[name]
    assert re.search(r"If\s+\(Entries\s*<>\s*''\)\s+And\s+CurrentDuring\s+"
                     r"Then", body)
    assert "BoardStillJson := 'null';" in body


def test_the_park_helper_uses_only_shipped_members(lib):
    """Mutation: assign CurrentComponent directly, or pick the target."""
    body = lib["LibParkCurrentElsewhere"]
    assert "PcbLib.LibraryIterator_Create" in body
    assert "PcbLib.LibraryIterator_Destroy(Iter)" in body
    assert "If Footprint <> Target Then" in body
    assert "LibMakeCurrentVerified(PcbLib, Other)" in body
    assert "CurrentComponent :=" not in body


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
    # Both paths are still marked; since 2026.10.11.01 the flag comes from
    # the marks' own read-back rather than a separate DocIsModified.
    assert "MarkedModified := MarkDocDirty(PcbLib.Board.FileName);" in body
    assert "If MarkDocDirty(LibPath) And (Not MarkedModified) Then" in body


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
    gate = body.rindex("If CurrentAtRemove Then", 0, count)
    assert count - gate < 120, "walk the board only while it is current"
    assert "JsonRaw('board_still_listed', BoardStillJson)" in body


def test_the_version_was_bumped_for_these_edits():
    main = (_PAS / "Main.pas").read_text(encoding="utf-8-sig")
    version = re.search(r"SCRIPT_VERSION\s*=\s*'([^']+)'", main).group(1)
    assert tuple(int(x) for x in version.split(".")) >= (2026, 10, 4, 11), (
        version)


# ------------------------------------------------- 3D placement, Python side

def test_a_body_off_its_footprint_is_not_reported_ok(monkeypatch, tmp_path):
    """Mutation: return the handler's reply as-is when placement_ok is false,
    or set ok=false and leave success=true.

    The 2026-10-04 body sat 50,000 mil from its pads while the reply's own
    check passed; the wrapper must turn the new check into ok=false AND
    success=false, because a caller that checks success would accept it.
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
    assert out["success"] is False


def test_an_unverified_placement_is_not_a_failure(monkeypatch, tmp_path):
    """A stale box read makes placement_ok null (unverified), not false."""
    reply = {"success": True, "placement_ok": None,
             "placement": {"ok": None, "basis": "bbox_expected"}}
    bridge = _Bridge(replies={"library.link_3d_model": reply})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path="m.step"))
    assert out.get("ok") is not False and out["success"] is True


def test_auto_link_lists_a_misplaced_body_as_failed(monkeypatch, tmp_path):
    """Mutation: drop body_added_in_memory / undo from the failed entry.

    Listed as failed, the body is still on the footprint in memory, and
    the next save writes it.
    """
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
    failed = out["failed"][0]
    assert failed["code"] == "PLACEMENT_FAR_FROM_FOOTPRINT"
    assert failed["body_added_in_memory"] is True
    assert "lib_delete_footprint_primitives" in failed["undo"]
    assert "componentbody" in failed["undo"]
