# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""verify_on_disk: one call settles whether a library removal persisted.

A review of the 2026-10-04 fix pointed out that the delete's own
counters cannot settle it: board_still_listed is 0 by construction for a
footprint that is not current, and still_listed 0 is exactly what the
failing delete reported. Persistence was proven only by three calls
(delete, lib_save_library, lib_read_pcblib_file). Now, once reload_first
has run, the tool reads the SAVED FILE right after that save (the state
before the removal), saves again after it, reads the file again, and
compares. ``ok`` and ``success`` are false when a removed primitive is
still in the file.

The file reads are faked here (``_saved_footprint``); the reader itself
is pinned in tests/test_pcblib_file_reader.py. Each guard names the
mutation that makes it fail.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.test_session_primitive_persistence import _Bridge, _install, _tools


def _pads(*names):
    return {"name": "FP", "complete": True, "other_records": {},
            "pads": [{"designator": n} for n in names]}


def _fp(**counts):
    entry = {"name": "FP", "complete": True, "other_records": {}}
    for kind in ("pads", "tracks", "arcs", "vias", "fills", "texts",
                 "regions", "bodies"):
        entry[kind] = [{}] * counts.get(kind, 0)
    return entry


@pytest.fixture
def lib_file(tmp_path):
    path = tmp_path / "carrier.PcbLib"
    path.write_bytes(b"x")
    return str(path)


def _fake_file(monkeypatch, *snapshots):
    """Each _saved_footprint call returns the next snapshot."""
    from eda_agent.tools import library

    queue = list(snapshots)
    calls: list = []

    def fake(path, footprint):
        calls.append((path, footprint))
        entry = queue.pop(0)
        return (entry, None) if entry is not None else (None, "unreadable")

    monkeypatch.setattr(library, "_saved_footprint", fake)
    return calls


_PAD_REPLY = {"removed": 1, "still_listed": 0,
              "results": [{"designator": "3", "selected": 1}]}


def test_a_pad_the_save_wrote_back_fails_the_call(monkeypatch, tmp_path,
                                                  lib_file):
    """Mutation: report the delete's in-memory counters as the answer.

    The measured failure: the delete reads still_listed 0, the save writes
    pad 3 back. The file after the verifying save still holds it.
    """
    bridge = _Bridge(replies={"library.delete_footprint_pads": _PAD_REPLY})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, _pads("1", "2", "3"), _pads("3", "1", "2"))
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "3"}], confirm=True,
        library_path=lib_file))
    assert out["ok"] is False and out["success"] is False
    assert out["persisted"]["still_present"] == ["3"]
    assert out["persisted"]["checked"] is True
    assert "'3'" in out["reason"]
    assert bridge.writes() == ["library.save_library",
                               "library.reload_library",
                               "library.delete_footprint_pads",
                               "library.save_library"]


def test_a_pad_that_stayed_deleted_passes(monkeypatch, tmp_path, lib_file):
    bridge = _Bridge(replies={"library.delete_footprint_pads": _PAD_REPLY})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, _pads("1", "2", "3"), _pads("1", "2"))
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "3"}], confirm=True,
        library_path=lib_file))
    assert out.get("ok") is not False
    persisted = out["persisted"]
    assert persisted["ok"] is True and persisted["absent"] == ["3"]
    assert persisted["by_designator"] == [{
        "designator": "3", "before": 1, "removed": 1, "after": 0,
        "expected_after": 0}]


def test_a_duplicate_designator_counts_not_presence(monkeypatch, tmp_path,
                                                    lib_file):
    """Mutation: test 'is the designator still in the file'. Two pads named
    2, one removed: one must remain, and two remaining is the failure."""
    reply = {"removed": 1, "results": [{"designator": "2", "selected": 1}]}
    bridge = _Bridge(replies={"library.delete_footprint_pads": reply})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, _pads("1", "2", "2"), _pads("1", "2"))
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "2", "index": 1}],
        confirm=True, library_path=lib_file))
    assert out["persisted"]["ok"] is True

    _fake_file(monkeypatch, _pads("1", "2", "2"), _pads("1", "2", "2"))
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "2", "index": 1}],
        confirm=True, library_path=lib_file))
    assert out["ok"] is False and out["persisted"]["still_present"] == ["2"]


def test_a_verifying_save_that_did_not_land_fails_the_call(
        monkeypatch, tmp_path, lib_file):
    """The removal is then in memory only, which is not what was asked."""
    class _SecondSaveFails(_Bridge):
        saves = 0

        async def send_command_async(self, command, params=None, **kw):
            if command == "library.save_library":
                type(self).saves += 1
                self.save_ok = type(self).saves == 1
            return await super().send_command_async(command, params, **kw)

    bridge = _SecondSaveFails(replies={
        "library.delete_footprint_pads": _PAD_REPLY})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, _pads("1", "3"))
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "3"}], confirm=True,
        library_path=lib_file))
    assert out["ok"] is False and out["persisted"]["saved"] is False
    assert "in memory only" in out["reason"]


def test_no_baseline_is_reported_unchecked_and_saves_nothing_more(
        monkeypatch, tmp_path, lib_file):
    bridge = _Bridge(replies={"library.delete_footprint_pads": _PAD_REPLY})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, None)
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "3"}], confirm=True,
        library_path=lib_file))
    assert out["persisted"]["checked"] is False
    assert out.get("ok") is not False
    assert bridge.writes().count("library.save_library") == 1


def test_verify_on_disk_false_leaves_the_delete_unsaved(monkeypatch,
                                                         tmp_path, lib_file):
    bridge = _Bridge(replies={"library.delete_footprint_pads": _PAD_REPLY})
    _install(monkeypatch, tmp_path, bridge)
    calls = _fake_file(monkeypatch)
    out = asyncio.run(_tools()["lib_delete_footprint_pads"](
        footprint_name="FP", pads=[{"designator": "3"}], confirm=True,
        library_path=lib_file, verify_on_disk=False))
    assert "persisted" not in out and calls == []
    assert bridge.writes().count("library.save_library") == 1


def test_primitives_compare_the_record_total(monkeypatch, tmp_path,
                                             lib_file):
    """Mutation: compare only one kind, or trust 'removed'."""
    reply = {"removed": 2, "still_listed": 0}
    bridge = _Bridge(replies={"library.delete_footprint_primitives": reply})
    _install(monkeypatch, tmp_path, bridge)
    tool = _tools()["lib_delete_footprint_primitives"]
    kwargs = dict(footprint_name="FP", object_type="track", confirm=True,
                  library_path=lib_file)

    _fake_file(monkeypatch, _fp(pads=2, tracks=3), _fp(pads=2, tracks=1))
    out = asyncio.run(tool(**kwargs))
    assert out.get("ok") is not False and out["persisted"]["ok"] is True

    _fake_file(monkeypatch, _fp(pads=2, tracks=3), _fp(pads=2, tracks=3))
    out = asyncio.run(tool(**kwargs))
    assert out["ok"] is False and out["success"] is False
    assert out["persisted"]["still_present"] == 2
    assert out["persisted"]["by_kind"]["tracks"] == {"before": 3, "after": 3}


_EXPECTED = {"model_2d_x_mils": 10.5, "model_2d_y_mils": -20.0,
             "model_3d_rotx": 90.0, "model_3d_roty": 0.0,
             "model_3d_rotz": 45.0, "model_3d_dz_mils": 3.0}


def _body(x=10.5, y=-20.0, rx=90.0, rz=45.0, dz=3.0):
    return {"layer": "Mechanical13", "standoff_height_mils": -36.37,
            "overall_height_mils": 42.37,
            "model": {"x_mils": x, "y_mils": y, "rot_x": rx, "rot_y": 0.0,
                      "rot_z": rz, "dz_mils": dz}}


def _link_bridge(step_reply=None):
    return _Bridge(replies={
        "library.get_footprint_bodies": {"bodies": [{"layer": "M13"}]},
        "library.delete_footprint_primitives": {"removed": 1},
        "library.link_3d_model": step_reply or {
            "success": True, "placement_ok": True,
            "expected_in_file": _EXPECTED, "applied": {}}})


@pytest.fixture
def step_file(tmp_path):
    path = tmp_path / "m.step"
    path.write_text("x")
    return str(path)


def test_a_replaced_body_is_checked_in_the_saved_file(monkeypatch, tmp_path,
                                                      lib_file, step_file):
    """Mutation: skip the count, or skip the expected_in_file match."""
    bridge = _link_bridge()
    _install(monkeypatch, tmp_path, bridge)
    before = {"name": "FP", "complete": True, "bodies": [_body(0, 0, 0, 0, 0)]}
    after = {"name": "FP", "complete": True, "bodies": [_body()]}
    _fake_file(monkeypatch, before, after)
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=step_file, library_path=lib_file,
        replace=True, offset_x=10.5, offset_y=-20, offset_z=3,
        rotation_x=90, rotation_z=45))
    persisted = out["persisted"]
    assert persisted["ok"] is True, persisted
    assert persisted["expected_bodies_after"] == 1
    assert persisted["matching_bodies"][0]["overall_height_mils"] == 42.37
    assert bridge.writes()[-1] == "library.save_library"


@pytest.mark.parametrize("after", [
    [_body(), _body(0, 0, 0, 0, 0)],      # the old body came back
    [_body(dz=0.0)],                      # Dz did not land
    [_body(x=-49989.5, y=-50020.0)],      # the 2026-10-04 misplacement
])
def test_a_replace_the_file_contradicts_fails(monkeypatch, tmp_path,
                                              lib_file, step_file, after):
    bridge = _link_bridge()
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, {"name": "FP", "complete": True,
                             "bodies": [_body(0, 0, 0, 0, 0)]},
               {"name": "FP", "complete": True, "bodies": after})
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=step_file, library_path=lib_file,
        replace=True))
    assert out["ok"] is False and out["success"] is False
    assert out["persisted"]["ok"] is False


def test_a_misplaced_body_is_not_saved_to_verify_it(monkeypatch, tmp_path,
                                                    lib_file, step_file):
    """Mutation: verify (= save) a body the placement check failed."""
    bridge = _link_bridge({"success": True, "placement_ok": False,
                           "placement": {"problem": "far"}})
    _install(monkeypatch, tmp_path, bridge)
    _fake_file(monkeypatch, {"name": "FP", "complete": True, "bodies": []})
    out = asyncio.run(_tools()["lib_link_3d_model"](
        component_name="FP", model_path=step_file, library_path=lib_file,
        replace=True))
    assert out["ok"] is False and "persisted" not in out
    assert bridge.writes().count("library.save_library") == 1
