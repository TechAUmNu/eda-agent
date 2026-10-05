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
CURRENT is what brings a removed primitive back), sent with or without
reload_first. Each guard names the mutation that makes it fail.
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
