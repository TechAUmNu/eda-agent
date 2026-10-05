# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_auto_link_3d_models: layer, model_map, per-footprint persistence.

Live use (2026-10-05, docs/RELEASE_VERIFICATION.md step 31) showed two
gaps in the batch link: a footprint with no body to inherit from got
Altium's default layer (Mechanical 1) where the house convention is
Mechanical 13, and models whose file stem differs from the footprint (or
that live in per-footprint folders, ``<dir>/<fp>/oriented/<fp>.step``)
could not be linked at all. These guards pin:

* ``layer`` reaches every link and WINS over the replaced body's layer;
* ``model_map`` resolves names case-insensitively to the library's own
  spelling, relative paths against model_dir, and overrides a stem match;
* a map file that is not there refuses the WHOLE call before anything
  (not even the footprint read) is sent;
* a map name the library does not hold is reported, the rest linked;
* with reload_first, ONE verifying save after the last link and each
  footprint read back from the saved file (``persisted``);
* an UNKNOWN_LAYER refusal stops the batch.

Plus the deletes' ``park_current`` default (measured 2026-10-05: being
CURRENT is enough to bring a removed primitive back), sent with or without
reload_first; a replace with or without reload_first going through the
parked delete; the layer checked in memory and in the saved file;
relative paths sent absolute; and the batch's failure isolation, skipped
verifying save and ``unverified`` count. Each guard names the mutation
that makes it fail.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_session_primitive_persistence import _Bridge, _install, _tools

_EXPECTED = {"model_2d_x_mils": 0.0, "model_2d_y_mils": 0.0,
             "model_3d_rotx": 0.0, "model_3d_roty": 0.0,
             "model_3d_rotz": 0.0, "model_3d_dz_mils": 0.0}

_LINK_OK = {"success": True, "placement_ok": True,
            "placement": {"ok": True, "gap_mils": 0},
            "applied": {"layer": True, "layer_source": "argument"},
            "body": {"layer": "Mechanical 13", "standoff_height_mils": 0,
                     "overall_height_mils": 39.37,
                     "bbox": {"x1": -10, "y1": -10, "x2": 10, "y2": 10}},
            "expected_in_file": _EXPECTED}


@pytest.fixture
def lib_file(tmp_path):
    path = tmp_path / "carrier.PcbLib"
    path.write_bytes(b"x")
    return str(path)


def _models(tmp_path, *names):
    d = tmp_path / "models"
    d.mkdir(exist_ok=True)
    for name in names:
        (d / f"{name}.step").write_text("x")
    return d


def _bridge(footprints, **replies):
    base = {"library.get_footprints": {
        "footprints": [{"name": n} for n in footprints]},
        "library.link_3d_model": _LINK_OK}
    base.update(replies)
    return _Bridge(replies=base)


def _links(bridge):
    return bridge.params("library.link_3d_model")


# ------------------------------------------------------------------ layer

def test_layer_is_forwarded_to_every_link(monkeypatch, tmp_path):
    """Mutation: drop layer from link_params (bare footprints then land on
    Altium's default Mechanical 1)."""
    d = _models(tmp_path, "SOIC8", "QFN16")
    bridge = _bridge(["SOIC8", "QFN16"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), layer="Mechanical 13"))
    assert [p["layer"] for p in _links(bridge)] == ["Mechanical 13"] * 2
    assert out["layer"] == "Mechanical 13"
    assert out["summary"]["linked"] == 2

    bridge.commands.clear()
    asyncio.run(_tools()["lib_auto_link_3d_models"](model_dir=str(d)))
    assert all("layer" not in p for p in _links(bridge)), (
        "no layer asked for: Altium's default (or the replaced body's)")


def test_an_explicit_layer_wins_over_the_replaced_body(monkeypatch, tmp_path,
                                                       lib_file):
    """Mutation: let _replace_then_link's inheritance overwrite the
    caller's layer, as it does when none is given."""
    d = _models(tmp_path, "SOIC8")
    bridge = _bridge(["SOIC8"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "Mechanical 1"}]},
        "library.delete_footprint_primitives": {"removed": 1}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        layer="Mechanical 13", verify_on_disk=False))
    link = _links(bridge)[0]
    assert link["layer"] == "Mechanical 13" and link["replace"] == "false"
    assert out["linked"][0]["applied"]["layer_source"] == "argument"

    bridge.commands.clear()
    asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        verify_on_disk=False))
    assert _links(bridge)[0]["layer"] == "Mechanical 1", (
        "without a layer the replaced body's layer is still inherited")


def test_an_unknown_layer_stops_the_batch(monkeypatch, tmp_path):
    """Mutation: continue after UNKNOWN_LAYER. With replace every later
    link would remove its old bodies and then be refused the same way."""
    from eda_agent.bridge.exceptions import AltiumCommandError

    d = _models(tmp_path, "A", "B", "C")
    bridge = _bridge(["A", "B", "C"], **{
        "library.link_3d_model": AltiumCommandError(
            "Unknown layer name: Mech13", code="UNKNOWN_LAYER")})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), layer="Mech13"))
    assert len(_links(bridge)) == 1
    assert out["stopped"]["at"] == "A"
    assert out["failed"][0]["code"] == "UNKNOWN_LAYER"
    assert out["success"] is False
    assert out["summary"]["matched"] == 3, (
        "a stopped batch still reports what it matched")


# -------------------------------------------------------------- model_map

def test_model_map_resolves_names_and_relative_paths(monkeypatch, tmp_path):
    """Mutation: match the map by exact case, send the caller's spelling,
    or resolve a relative path against the working directory."""
    d = _models(tmp_path, "SOIC8")          # a stem match the map overrides
    sub = d / "SOIC8" / "oriented"
    sub.mkdir(parents=True)
    (sub / "SOIC8.step").write_text("x")
    other = tmp_path / "elsewhere" / "qfn_16_wrapper.stp"
    other.parent.mkdir()
    other.write_text("x")
    bridge = _bridge(["SOIC8", "QFN16", "TSSOP20"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), model_map={
            "soic8": "SOIC8/oriented/SOIC8.step",
            "qfn16": str(other)}))
    sent = {p["component_name"]: p["model_path"] for p in _links(bridge)}
    assert sent == {"SOIC8": str(sub / "SOIC8.step"), "QFN16": str(other)}
    assert {r["footprint"]: r["model_source"] for r in out["linked"]} == {
        "SOIC8": "map", "QFN16": "map"}
    assert out["unmatched_footprints"] == ["TSSOP20"]
    assert out["unused_models"] == [str(d / "SOIC8.step")], (
        "the stem match the map overrode was not used")


def test_model_map_needs_no_model_dir(monkeypatch, tmp_path):
    step = tmp_path / "x.step"
    step.write_text("x")
    bridge = _bridge(["SOIC8"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_map={"SOIC8": str(step)}))
    assert out["success"] is True and out["summary"]["linked"] == 1

    bridge.commands.clear()
    out = asyncio.run(_tools()["lib_auto_link_3d_models"]())
    assert out["ok"] is False and bridge.commands == []


def test_a_missing_map_file_is_refused_before_anything(monkeypatch, tmp_path,
                                                       lib_file):
    """Mutation: check the files only when each link runs. A missing file
    would then cost reload_first's save and reopen and leave the batch
    half linked."""
    good = tmp_path / "good.step"
    good.write_text("x")
    bridge = _bridge(["A", "B"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_map={"A": str(good), "B": str(tmp_path / "gone.step")},
        library_path=lib_file, replace=True))
    assert out["ok"] is False
    assert [m["footprint"] for m in out["missing_models"]] == ["B"]
    assert "Nothing was saved" in out["reason"]
    assert bridge.commands == [], "nothing may reach the bridge"


def test_an_unknown_map_footprint_is_reported(monkeypatch, tmp_path):
    """Mutation: drop names the library does not hold silently."""
    step = tmp_path / "x.step"
    step.write_text("x")
    bridge = _bridge(["SOIC8"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_map={"SOIC8": str(step), "NOPE_FP": str(step)}))
    assert out["unknown_footprints"] == ["NOPE_FP"]
    assert out["summary"]["unknown_footprints"] == 1
    assert [r["footprint"] for r in out["linked"]] == ["SOIC8"]
    assert out["success"] is False


# ------------------------------------------------------------ persistence

def _fake_files(monkeypatch, snapshots):
    """``snapshots[footprint]`` = [baseline, after]; each read pops one."""
    from eda_agent.tools import library

    queues = {k: list(v) for k, v in snapshots.items()}

    def fake(path, footprint):
        entry = queues[footprint].pop(0)
        return (entry, None) if entry is not None else (None, "unreadable")

    monkeypatch.setattr(library, "_saved_footprint", fake)
    return queues


def _bodies(*models):
    return {"complete": True, "bodies": [
        {"model": m, "layer": "Mechanical 13", "standoff_height_mils": 0,
         "overall_height_mils": 39.37} for m in models]}


_PLACED = {"x_mils": 0.0, "y_mils": 0.0, "rot_x": 0, "rot_y": 0,
           "rot_z": 0, "dz_mils": 0}
_PLACEHOLDER = {"x_mils": 0.0, "y_mils": 0.0, "dz_mils": 5}


def test_one_verifying_save_and_every_footprint_read_back(monkeypatch,
                                                          tmp_path, lib_file):
    """Mutation: save once per footprint, skip the read-back, or report a
    link as persisted without reading the file."""
    d = _models(tmp_path, "A", "B")
    bridge = _bridge(["A", "B"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1}})
    _install(monkeypatch, tmp_path, bridge)
    left = _fake_files(monkeypatch, {
        "A": [_bodies(_PLACEHOLDER), _bodies(_PLACED)],
        "B": [_bodies(_PLACEHOLDER), _bodies(_PLACED)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        layer="Mechanical 13"))
    writes = bridge.writes()
    assert writes.count("library.save_library") == 2
    assert writes[-1] == "library.save_library", "the verify save is last"
    assert all(not q for q in left.values()), "every footprint read twice"
    for row in out["linked"]:
        assert row["persisted"]["ok"] is True
        assert row["persisted"]["bodies_after"] == 1
        assert row["body"]["layer"] == "Mechanical 13"
    assert out["summary"]["persisted_ok"] == 2
    assert out["verify"]["saved"] is True and out["success"] is True


def test_a_footprint_the_file_contradicts_moves_to_failed(monkeypatch,
                                                          tmp_path, lib_file):
    """Mutation: keep a link whose saved file disagrees under linked."""
    d = _models(tmp_path, "A", "B")
    bridge = _bridge(["A", "B"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1}})
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {
        "A": [_bodies(_PLACEHOLDER), _bodies(_PLACED)],
        # B: the placeholder came back beside the new body.
        "B": [_bodies(_PLACEHOLDER), _bodies(_PLACEHOLDER, _PLACED)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True))
    assert [r["footprint"] for r in out["linked"]] == ["A"]
    bad = out["failed"][0]
    assert bad["footprint"] == "B" and bad["code"] == "NOT_PERSISTED"
    assert bad["persisted"]["expected_bodies_after"] == 1
    assert out["summary"] == dict(out["summary"], linked=1, failed=1,
                                  persisted_checked=1, persisted_ok=1)
    assert out["success"] is False


def test_no_verifying_save_after_a_failed_link(monkeypatch, tmp_path,
                                               lib_file):
    """Mutation: save to verify after a failure. A misplaced body would be
    written, and bodies removed before a refused link could no longer be
    restored by lib_reload_library."""
    d = _models(tmp_path, "A")
    bridge = _bridge(["A"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": {"placement_ok": False, "placement": {
            "ok": False, "problem": "far"}}})
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {"A": [_bodies(_PLACEHOLDER)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True))
    assert bridge.writes().count("library.save_library") == 1
    assert "verify" not in out or out["verify"]["ran"] is False


def test_verify_on_disk_false_saves_only_once(monkeypatch, tmp_path,
                                              lib_file):
    d = _models(tmp_path, "A")
    bridge = _bridge(["A"], **{
        "library.get_footprint_bodies": {"bodies": []}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        verify_on_disk=False))
    assert bridge.writes().count("library.save_library") == 1
    assert "persisted" not in out["linked"][0] and "verify" not in out


# ----------------------------------------------------- deletes park_current

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
def test_park_current_is_sent_without_reload_first(monkeypatch, tmp_path,
                                                   lib_file, tool):
    """Mutation: park only after reload_first. Measured 2026-10-05: a
    DISK-LOADED pad removed while its footprint was current came back with
    the save, so a no-reload delete must park too."""
    kwargs, command = _DELETES[tool]
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()[tool](library_path=lib_file, reload_first=False,
                               **kwargs))
    assert bridge.names() == [command]
    assert bridge.params(command)[-1].get("park_current") == "true"


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_park_current_false_is_honoured(monkeypatch, tmp_path, lib_file,
                                        tool):
    kwargs, command = _DELETES[tool]
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()[tool](library_path=lib_file, reload_first=False,
                               park_current=False, **kwargs))
    assert "park_current" not in bridge.params(command)[-1]


@pytest.mark.parametrize("tool", sorted(_DELETES))
def test_park_current_with_remove_from_board_is_refused(monkeypatch, tmp_path,
                                                        lib_file, tool):
    """The board half needs the footprint current; parking contradicts it."""
    kwargs, _command = _DELETES[tool]
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()[tool](library_path=lib_file,
                                     remove_from_board=True,
                                     park_current=True, **kwargs))
    assert out["ok"] is False and "contradict" in out["reason"]
    assert bridge.commands == []


# ------------------------------------------- review 2026-10-05 follow-ups

class _PerFootprintBridge(_Bridge):
    """A _Bridge whose link reply depends on the footprint linked."""

    def __init__(self, link_replies, **kwargs):
        super().__init__(**kwargs)
        self.link_replies = link_replies

    async def send_command_async(self, command, params=None, **kwargs):
        if command == "library.link_3d_model":
            self.commands.append((command, dict(params or {})))
            reply = self.link_replies.get(params.get("component_name"),
                                          _LINK_OK)
            if isinstance(reply, Exception):
                raise reply
            return reply
        return await super().send_command_async(command, params, **kwargs)


def _replace_replies():
    return {"library.get_footprints": {
                "footprints": [{"name": "A"}, {"name": "B"}]},
            "library.get_footprint_bodies": {"bodies": [
                {"layer": "Mechanical 13"}]},
            "library.delete_footprint_primitives": {"removed": 1}}


@pytest.mark.parametrize("bad", ["A", "B"])
def test_one_failure_skips_the_verifying_save_and_isolates(monkeypatch,
                                                          tmp_path, lib_file,
                                                          bad):
    """Mutations: ``if failed:`` -> ``if False:`` (the verifying save then
    writes the failed footprint bare), or stop the loop at the first
    failure (the other footprint is then never linked).

    Two footprints so ``linked`` is not empty: with one, the empty list
    alone already stops the save and the guard is never exercised.
    """
    from eda_agent.bridge.exceptions import AltiumCommandError

    d = _models(tmp_path, "A", "B")
    bridge = _PerFootprintBridge(
        {bad: AltiumCommandError("bad STEP", code="MODEL_LOAD_FAILED")},
        replies=_replace_replies())
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {"A": [_bodies(_PLACEHOLDER)],
                              "B": [_bodies(_PLACEHOLDER)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True))
    good = "B" if bad == "A" else "A"
    assert [r["footprint"] for r in out["linked"]] == [good]
    assert [f["footprint"] for f in out["failed"]] == [bad]
    assert len(_links(bridge)) == 2, "a failure must not stop the batch"
    assert bridge.writes().count("library.save_library") == 1, (
        "only reload_first's save: the verifying save must be skipped")
    assert out["verify"]["ran"] is False
    reason = out["verify"]["reason"]
    assert "lib_reload_library" in reason and "throws away" in reason
    assert "1 successful link" in reason
    assert out["linked"][0]["persisted"]["checked"] is False
    assert out["summary"]["unverified"] == 1 and out["warning"]
    assert out["success"] is False


def test_unknown_layer_after_the_parked_delete_stops_the_batch(monkeypatch,
                                                               tmp_path,
                                                               lib_file):
    """Mutation: continue after an UNKNOWN_LAYER that comes back AFTER the
    delete removed the old bodies (the replace route's reply, not the
    exception path): every later footprint would lose its bodies too."""
    from eda_agent.bridge.exceptions import AltiumCommandError

    d = _models(tmp_path, "A", "B", "C")
    replies = _replace_replies()
    replies["library.get_footprints"] = {
        "footprints": [{"name": n} for n in "ABC"]}
    replies["library.link_3d_model"] = AltiumCommandError(
        "Unknown layer name: Mech99", code="UNKNOWN_LAYER")
    bridge = _Bridge(replies=replies)
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {n: [_bodies(_PLACEHOLDER)] for n in "ABC"})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        layer="Mech99"))
    assert len(_links(bridge)) == 1
    assert len(bridge.params("library.delete_footprint_primitives")) == 1
    assert out["stopped"]["at"] == "A"
    assert out["failed"][0]["code"] == "UNKNOWN_LAYER"
    assert "lib_reload_library" in out["stopped"]["reason"]


def test_a_missing_baseline_is_unverified_not_ok(monkeypatch, tmp_path,
                                                lib_file):
    """Mutation: compare against a missing baseline, or count the row as
    persisted. It stays linked, unchecked, and the reply says so."""
    d = _models(tmp_path, "A")
    bridge = _bridge(["A"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1}})
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {"A": [None]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True))
    row = out["linked"][0]
    assert row["persisted"]["checked"] is False
    assert "no baseline" in row["persisted"]["reason"]
    assert out["summary"]["persisted_checked"] == 0
    assert out["summary"]["unverified"] == 1
    assert out["unverified"] == ["A"] and "IN MEMORY" in out["warning"]


def test_a_batch_without_verification_says_so(monkeypatch, tmp_path):
    """No replace, so nothing is saved or read back: success must not read
    as proof on disk."""
    d = _models(tmp_path, "A")
    bridge = _bridge(["A"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](model_dir=str(d)))
    assert out["success"] is True
    assert out["summary"]["unverified"] == 1
    assert "lib_read_pcblib_file" in out["warning"]


# ---------------------------------------------- replace without a reload

def test_auto_link_replace_without_reload_parks_then_links(monkeypatch,
                                                           tmp_path,
                                                           lib_file):
    """Mutation: send the handler's own replace when reload_first did not
    run. It removes from the CURRENT footprint, measured (2026-10-05,
    item 3b) to come back with the next save."""
    d = _models(tmp_path, "A", "B")
    bridge = _Bridge(replies=dict(_replace_replies(), **{
        "library.link_3d_model": _LINK_OK}))
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        reload_first=False))
    assert "library.save_library" not in bridge.names()
    assert bridge.writes() == ["library.delete_footprint_primitives",
                               "library.link_3d_model"] * 2
    assert all(p["park_current"] == "true" for p in
               bridge.params("library.delete_footprint_primitives"))
    assert all(p["replace"] == "false" for p in _links(bridge))
    assert out["linked"][0]["replaced"]["saved_first"] is False


def test_auto_link_without_reload_pins_the_focused_library(monkeypatch,
                                                           tmp_path,
                                                           lib_file):
    """The replace DELETES, so with no library_path the deletes and links
    are pinned to the focused .PcbLib rather than whatever has focus after
    each read."""
    d = _models(tmp_path, "A")
    bridge = _Bridge(replies=dict(_replace_replies(), **{
        "library.get_footprints": {"footprints": [{"name": "A"}]},
        "application.get_active_document": {"file_path": lib_file},
        "library.link_3d_model": _LINK_OK}))
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), replace=True, reload_first=False))
    assert bridge.params("library.delete_footprint_primitives")[0][
        "library_path"] == lib_file
    assert _links(bridge)[0]["library_path"] == lib_file


def test_link_replace_without_reload_parks_then_links(monkeypatch, tmp_path,
                                                      lib_file):
    step = tmp_path / "m.step"
    step.write_text("x")
    bridge = _Bridge(replies={
        "library.get_footprints": {"footprints": [{"name": "FP"}]},
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": {"success": True}})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=str(step), library_path=lib_file,
        replace=True, reload_first=False))
    assert bridge.writes() == ["library.delete_footprint_primitives",
                               "library.link_3d_model"]
    assert bridge.params("library.delete_footprint_primitives")[0][
        "park_current"] == "true"
    assert bridge.params("library.link_3d_model")[0]["replace"] == "false"
    assert out["replaced"]["saved_first"] is False
    assert "warning" not in out


def test_link_replace_without_reload_failure_names_the_last_save(
        monkeypatch, tmp_path, lib_file):
    """Without reload_first nothing was saved first: 'lib_reload_library
    restores' would discard every unsaved edit, and the reply must say so."""
    from eda_agent.bridge.exceptions import AltiumCommandError

    step = tmp_path / "m.step"
    step.write_text("x")
    bridge = _Bridge(replies={
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": AltiumCommandError(
            "bad STEP", code="MODEL_LOAD_FAILED")})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=str(step), library_path=lib_file,
        replace=True, reload_first=False))
    assert out["ok"] is False and "LAST SAVE" in out["reason"]
    assert "Do NOT save" in out["reason"]


def test_link_replace_without_reload_refuses_a_missing_model_first(
        monkeypatch, tmp_path, lib_file):
    """The route removes before it links: a missing model must cost
    nothing."""
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=str(tmp_path / "nope.step"),
        library_path=lib_file, replace=True, reload_first=False))
    assert out["ok"] is False and "not a file" in out["reason"]
    assert bridge.writes() == []


def test_link_replace_without_a_footprint_name_warns_at_the_top(
        monkeypatch, tmp_path, lib_file):
    """No footprint to park: the handler's own replace runs, and the reply
    says at the top that its removal may come back."""
    step = tmp_path / "m.step"
    step.write_text("x")
    bridge = _Bridge()
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="", model_path=str(step), library_path=lib_file,
        replace=True, reload_first=False))
    assert bridge.params("library.link_3d_model")[0]["replace"] == "true"
    assert "handler's own replace" in out["warning"]


# ------------------------------------------------------------------ layers

@pytest.mark.parametrize("wanted,got,verdict", [
    ("Mechanical 13", "Mechanical13", True),
    ("Mech13", "MECHANICAL 13", True),
    ("Mechanical 13", "Mechanical1", False),
    ("Top Overlay", "TopOverlay", True),
    ("3D Body", "Mechanical13", None),
    ("Mechanical 13", "", None),
])
def test_layer_comparison(wanted, got, verdict):
    from eda_agent.tools.library import _layer_agrees
    assert _layer_agrees(wanted, got) is verdict


def _saved_on(layer, *models):
    return {"complete": True, "bodies": [
        {"model": m, "layer": layer, "standoff_height_mils": 0,
         "overall_height_mils": 39.37} for m in models]}


def test_a_body_saved_on_another_layer_is_not_persisted(monkeypatch,
                                                        tmp_path, lib_file):
    """Mutation: compare count and placement only. The body saved on
    Mechanical 1 instead of Mechanical 13 then reports ok."""
    d = _models(tmp_path, "A")
    bridge = _bridge(["A"], **{
        "library.get_footprint_bodies": {"bodies": [{"layer": "M1"}]},
        "library.delete_footprint_primitives": {"removed": 1}})
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {"A": [_bodies(_PLACEHOLDER),
                                    _saved_on("Mechanical1", _PLACED)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        layer="Mechanical 13"))
    assert out["linked"] == []
    bad = out["failed"][0]
    assert bad["code"] == "NOT_PERSISTED"
    assert bad["persisted"]["layer_ok"] is False
    assert "layer_ok False" in bad["error"]


def test_a_library_specific_layer_name_is_unverified_not_failed(
        monkeypatch, tmp_path, lib_file):
    d = _models(tmp_path, "A")
    reply = dict(_LINK_OK, body=dict(_LINK_OK["body"], layer="3D Body"))
    bridge = _bridge(["A"], **{
        "library.get_footprint_bodies": {"bodies": []},
        "library.link_3d_model": reply})
    _install(monkeypatch, tmp_path, bridge)
    _fake_files(monkeypatch, {"A": [_saved_on("Mechanical13"),
                                    _saved_on("Mechanical13", _PLACED)]})
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), library_path=lib_file, replace=True,
        layer="3D Body"))
    persisted = out["linked"][0]["persisted"]
    assert persisted["ok"] is True and persisted["layer_ok"] is None
    assert "layer_unverified" in persisted


def test_a_body_on_another_layer_in_memory_is_failed(monkeypatch, tmp_path):
    """Mutation: drop the in-memory layer check. The body Altium put on
    Mechanical 1 would be listed as linked (and saved by the verify)."""
    d = _models(tmp_path, "A")
    reply = dict(_LINK_OK, body=dict(_LINK_OK["body"], layer="Mechanical 1"))
    bridge = _bridge(["A"], **{"library.link_3d_model": reply})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(d), layer="Mechanical 13"))
    assert out["linked"] == []
    bad = out["failed"][0]
    assert bad["code"] == "LAYER_MISMATCH"
    assert bad["body_added_in_memory"] is True


def test_link_reports_a_body_on_another_layer_as_failed(monkeypatch,
                                                        tmp_path):
    reply = dict(_LINK_OK, body=dict(_LINK_OK["body"], layer="Mechanical 1"))
    bridge = _Bridge(replies={"library.link_3d_model": reply})
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path="m.step", layer="Mechanical 13"))
    assert out["ok"] is False and out["success"] is False
    assert "Mechanical 1" in out["reason"]


# ------------------------------------------------------------------- paths

def test_relative_paths_are_sent_absolute(monkeypatch, tmp_path):
    """Mutation: send a relative path as given. It was checked against
    THIS server's working directory and Altium resolves it against its own,
    so the up-front check would mean nothing."""
    monkeypatch.chdir(tmp_path)
    _models(tmp_path, "A")
    (tmp_path / "b_model.step").write_text("x")
    bridge = _bridge(["A", "B"])
    _install(monkeypatch, tmp_path, bridge)
    asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir="models", model_map={"B": "../b_model.step"}))
    sent = {p["component_name"]: p["model_path"] for p in _links(bridge)}
    assert sent["A"] == str((tmp_path / "models" / "A.step").resolve())
    assert sent["B"] == str((tmp_path / "b_model.step").resolve())

    bridge.commands.clear()
    asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_map={"B": "b_model.step"}))
    assert _links(bridge)[0]["model_path"] == str(
        (tmp_path / "b_model.step").resolve())


def test_a_map_file_that_is_not_a_step_is_refused(monkeypatch, tmp_path):
    other = tmp_path / "notes.txt"
    other.write_text("x")
    bridge = _bridge(["A"])
    _install(monkeypatch, tmp_path, bridge)
    out = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_map={"A": str(other)}))
    assert out["ok"] is False
    assert out["missing_models"][0]["problem"] == "not a .step/.stp file"
    assert bridge.commands == []
