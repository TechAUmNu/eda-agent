# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One removal rule for every PcbLib footprint delete.

Four paths take primitives out of a library footprint:
lib_delete_footprint_primitives, lib_delete_footprint_pads,
lib_link_3d_model with replace=true (Body3D_RemoveAll) and the
duplicate cleanup in lib_add_footprint_text. A review of the branch
that added a library-BOARD removal found two problems, and these guards
pin the fix:

* The board removal had been made the DEFAULT of a delete tool that
  worked, on the strength of a premise no live session had checked, and
  it ran on the very path whose comment records an uncatchable access
  violation in ScriptingSystem.DLL. It is now opt-in
  (``remove_from_board=true``) everywhere.
* The paths disagreed: one comment said a footprint-only removal comes
  back after save and reload, while the pad delete and the 3D replace
  relied on exactly that removal. They now share one helper
  (``LibBoardRemoveEntries``) and one rule for when the footprint is made
  current, so the live measurement in docs/RELEASE_VERIFICATION.md
  step 24 settles all four at once.

The rule, as the guards read it:

1. the footprint half is the default, and stays inline in each caller;
2. ``Board.RemovePCBObject`` appears in Library.pas ONLY inside the
   shared helper, and the helper is reached only behind ``FromBoard``,
   which is true only for the exact word ``'true'``;
3. the footprint is made current only for the board half, and before
   anything is removed;
4. the board half runs after the footprint half, skips anything the
   footprint still lists, and removes only what a fresh board iterator
   has just yielded, while that iterator is open.

Each guard names the mutation that makes it fail.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: Each removal path, and the snippet that starts its board half.
_CALLERS = {
    "Lib_DeleteFootprintPrimitives": "LibBoardRemoveEntries(PcbLib.Board",
    "Lib_DeleteFootprintPads": "LibBoardRemoveEntries(PcbLib.Board",
    "Lib_AddFootprintText": "LibBoardRemoveEntries(PcbLib.Board",
    "Lib_Link3DModel": "LibBoardRemoveEntries(PcbLib.Board",
}


@pytest.fixture(scope="module")
def lib() -> dict[str, str]:
    return load(_PAS / "Library.pas", minimum=100)


def test_the_scan_sees_every_path(lib):
    """The guards below are vacuous if a routine they read is renamed."""
    for name in list(_CALLERS) + ["LibBoardRemoveEntries",
                                  "LibBoardListsAddress",
                                  "LibMakeCurrentVerified",
                                  "LibAddRemovalEntry",
                                  "RemoveLibTextByAddress",
                                  "Body3D_RemoveAll"]:
        assert name in lib, f"{name} is gone from Library.pas"


def test_only_the_shared_helper_removes_from_the_library_board(lib):
    """Mutation: put PcbLib.Board.RemovePCBObject back in any handler."""
    offenders = [name for name, body in lib.items()
                 if "Board.RemovePCBObject" in body
                 and name != "LibBoardRemoveEntries"]
    assert not offenders, (
        f"{offenders} remove from the library board outside "
        f"LibBoardRemoveEntries, so that removal escapes the opt-in rule")
    assert "Board.RemovePCBObject(Obj)" in lib["LibBoardRemoveEntries"]


@pytest.mark.parametrize("name", sorted(_CALLERS))
def test_the_board_half_is_reached_only_on_request(lib, name):
    """Mutation: drop the FromBoard guard, or parse the flag as <> 'false'.

    The flag must be read as opt-in: only the exact word 'true' turns it
    on, so an absent key, a typo or an older Python side all mean off.
    """
    body = lib[name]
    assert re.search(r"FromBoard\s*:=[^;]*ExtractJsonValue\(Params,\s*"
                     r"'remove_from_board'\)\)\)\s*=\s*'true'", body), (
        f"{name} does not read remove_from_board as an explicit opt-in")
    calls = [m.start() for m in
             re.finditer(re.escape(_CALLERS[name]), body)]
    assert calls, f"{name} no longer calls the shared board half"
    for at in calls:
        # The ONE exception (2026.10.12.01, live P7): lib_link_3d_model's
        # undo of the EMPTY body it has just added itself takes it off the
        # library board too - only that body (EmptyEntries), only while
        # the footprint is current (rule 3) and no longer lists it (rule 4).
        if body[at:at + 80].find("EmptyEntries") >= 0:
            guard = body.rfind("If (EmptyEntries <> '') And EmptyCurrent "
                               "And (Not EmptyFpStill) Then", 0, at)
            assert guard >= 0 and at - guard < 200, (
                f"{name}: the empty-body undo reaches the board half "
                f"without its rule-3/rule-4 guard")
            continue
        guard = body.rfind("If FromBoard", 0, at)
        assert guard >= 0 and at - guard < 300, (
            f"{name} reaches LibBoardRemoveEntries without an "
            f"`If FromBoard` guard right in front of it")


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPrimitives",
                                  "Lib_DeleteFootprintPads"])
def test_the_footprint_is_made_current_only_for_the_board_half(lib, name):
    """Mutation: make the footprint current unconditionally again.

    The base build never touched CurrentComponent on a delete; only the
    board half needs it, because the library board holds the CURRENT
    footprint's primitives. The switch must come before any removal and
    refuse when it did not take.
    """
    body = lib[name]
    assert "PcbLib.CurrentComponent :=" not in body, (
        f"{name} switches the footprint itself instead of through "
        f"LibMakeCurrentVerified")
    switch = body.index("LibMakeCurrentVerified(PcbLib, Target)")
    guard = body.rfind("If FromBoard Then", 0, switch)
    assert guard >= 0 and switch - guard < 120, (
        f"{name} makes the footprint current without remove_from_board")
    assert "'SELECT_FAILED'" in body[switch:switch + 600]
    assert switch < body.index("Target.RemovePCBObject(Prim)")


def test_the_switch_is_read_back(lib):
    body = lib["LibMakeCurrentVerified"]
    write = body.index("PcbLib.CurrentComponent := Footprint")
    assert "(PcbLib.CurrentComponent = Footprint)" in body[write:]


@pytest.mark.parametrize("name", ["Lib_DeleteFootprintPrimitives",
                                  "Lib_DeleteFootprintPads"])
def test_the_board_half_runs_after_the_footprint_read_back(lib, name):
    """Mutation: call LibBoardRemoveEntries inside the removal loop.

    The footprint must be finished with every object, and its read-back
    taken, before the board sees any of them: that is what keeps a
    double-registered primitive's second footprint entry from being
    handed back after the board let it go.
    """
    body = lib[name]
    last_fp_removal = body.rindex("Target.RemovePCBObject(Prim)")
    readback = body.index("StillAddrs", last_fp_removal)
    board = body.index("LibBoardRemoveEntries(", last_fp_removal)
    assert last_fp_removal < readback < board
    assert "StillAddrs, MadeCurrent" in body[board:board + 200], (
        f"{name} does not hand the footprint's still-listed set to the "
        f"board half, so it could touch an object the footprint holds")


def test_the_replace_runs_its_board_half_after_the_footprint_is_done(lib):
    """Mutation: run the board half before Body3D_RemoveAll returns."""
    body = lib["Lib_Link3DModel"]
    remove = body.index("Body3D_RemoveAll(")
    board = body.index("LibBoardRemoveEntries(")
    add = body.index("Footprint.AddPCBObject(Body)")
    assert remove < board < add
    assert "Body3D_BodyAddresses(Footprint)" in body[board:board + 200]
    assert re.search(r"FromBoard\s*:=\s*Replace\s+And", body), (
        "remove_from_board without replace must do nothing")


def test_the_helper_removes_only_what_a_fresh_board_walk_just_yielded(lib):
    """Mutation: remove after BoardIterator_Destroy, or skip the skip set.

    The skip test (objects the footprint still lists) must come before
    the walk; the removal must sit between the iterator's create and its
    destroy; and nothing runs when the footprint is not current.
    """
    body = lib["LibBoardRemoveEntries"]
    skip = body.index("Pos('|' + Addr + '|', Skip) > 0")
    create = body.index("Board.BoardIterator_Create")
    remove = body.index("Board.RemovePCBObject(Obj)")
    destroy = body.index("Board.BoardIterator_Destroy(Iter)")
    assert skip < create < remove < destroy
    match = body.index("If ThisAddr = Addr Then")
    assert match < remove, "the removal must follow the by-address match"
    assert "Not MadeCurrent" in body and "If NotRun <> '' Then Continue" in body


@pytest.mark.parametrize("name", ["RemoveLibTextByAddress",
                                  "Body3D_RemoveAll"])
def test_the_shared_footprint_removals_record_what_they_removed(lib, name):
    """Mutation: drop the LibAddRemovalEntry call; the board half then
    has nothing to sweep and reports a clean board it never walked."""
    body = lib[name]
    assert "LibAddRemovalEntry(Entries" in body
    assert "Board.RemovePCBObject" not in body


def test_a_removal_entry_is_recorded_once_per_address(lib):
    """Mutation: drop the ':' anchor, and 1234 would match inside 11234."""
    body = lib["LibAddRemovalEntry"]
    assert "Pos(':' + Addr + '|', Entries) = 0" in body


# --------------------------------------------------------- Python side

class _CapturingBridge:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.commands.append((command, dict(params or {})))
        if command == "library.get_footprints":
            return {"footprints": [{"name": "SOIC8"}]}
        return {"success": True}


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
def fake(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    bridge = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    return bridge


_PY_CALLS = {
    "lib_delete_footprint_primitives": dict(
        footprint_name="FP", object_type="track", confirm=True),
    "lib_delete_footprint_pads": dict(
        footprint_name="FP", pads=[{"designator": "1"}], confirm=True),
    "lib_add_footprint_text": dict(text="REF"),
    "lib_link_3d_model": dict(component_name="FP", model_path="m.step",
                              replace=True),
}


def _removal_params(fake, tool):
    """The params of the call that REMOVES. A replace link removes through
    lib_delete_footprint_primitives (parked delete, then a plain link), so
    for it that is the delete, not the link."""
    if tool == "lib_link_3d_model":
        deletes = [p for c, p in fake.commands
                   if c == "library.delete_footprint_primitives"]
        assert deletes, "a replace link must remove through the delete"
        return deletes[-1]
    return fake.commands[-1][1]


@pytest.mark.parametrize("tool", sorted(_PY_CALLS))
def test_remove_from_board_is_off_unless_asked_for(fake, tool, tmp_path):
    """Mutation: default remove_from_board to True in any tool."""
    tools = _tools()
    kwargs = dict(_PY_CALLS[tool])
    if "model_path" in kwargs:
        step = tmp_path / "m.step"
        step.write_text("x")
        kwargs["model_path"] = str(step)
    asyncio.run(tools[tool](**kwargs))
    params = _removal_params(fake, tool)
    assert "remove_from_board" not in params, (
        f"{tool} sends remove_from_board without being asked")

    fake.commands.clear()
    asyncio.run(tools[tool](**kwargs, remove_from_board=True))
    params = _removal_params(fake, tool)
    assert params.get("remove_from_board") == "true"


@pytest.mark.parametrize("tool", ["lib_link_3d_model",
                                  "lib_auto_link_3d_models"])
def test_remove_from_board_needs_replace(fake, tool, tmp_path):
    tools = _tools()
    kwargs = ({"component_name": "FP", "model_path": "m.step"}
              if tool == "lib_link_3d_model"
              else {"model_dir": str(tmp_path)})
    result = asyncio.run(tools[tool](**kwargs, remove_from_board=True))
    assert result.get("ok") is False, result
    assert fake.commands == []


def test_auto_link_forwards_remove_from_board(fake, tmp_path):
    (tmp_path / "SOIC8.step").write_text("x")
    tools = _tools()
    asyncio.run(tools["lib_auto_link_3d_models"](
        model_dir=str(tmp_path), replace=True, remove_from_board=True))
    # The replace removes through the delete (the board half needs the
    # footprint current, so it is not parked), then links without replace.
    deletes = [p for c, p in fake.commands
               if c == "library.delete_footprint_primitives"]
    assert deletes and deletes[0]["remove_from_board"] == "true"
    assert "park_current" not in deletes[0]
    links = [p for c, p in fake.commands if c == "library.link_3d_model"]
    assert links and links[0]["replace"] == "false"

    fake.commands.clear()
    asyncio.run(tools["lib_auto_link_3d_models"](
        model_dir=str(tmp_path), replace=True))
    deletes = [p for c, p in fake.commands
               if c == "library.delete_footprint_primitives"]
    assert deletes and "remove_from_board" not in deletes[0]
    assert deletes[0]["park_current"] == "true"
