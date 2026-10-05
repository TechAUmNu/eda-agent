# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Live sheet positions come back in the frame placement_hints expects.

The conversion is the whole point of this code path. Altium reports a
component's ``Location`` as the symbol ORIGIN, which is what the emitter
wrote; ``placement_hints`` are read as ``PlacedPart`` BODY CENTRES.
Passing one straight through as the other shifts every part by its own
centre offset, silently, and by a different amount per symbol, so a
"keep everything where it is" re-run would move the whole sheet.
"""
from __future__ import annotations

import json

from eda_agent.design.canvas import SymbolInstance, altium_orientation
from eda_agent.design.orchestrator import hints_from_sheet
from eda_agent.design.pipeline import _center_offset
from eda_agent.design.symbols import SymbolCache
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin


def _symbol(lib_ref: str, lib_path: str = "LIB.SchLib") -> SymbolModel:
    # Pins deliberately off-centre so the body centre is NOT the origin;
    # a symmetric symbol would hide exactly the bug this guards.
    return SymbolModel(
        lib_path=lib_path,
        lib_ref=lib_ref,
        pins=[
            SymbolPin(designator="1", name="A", x=0, y=400,
                      orientation=1, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="K", x=0, y=-100,
                      orientation=3, length=100, electrical_type="passive"),
        ],
        # Body sits ABOVE the origin, so the centre is not the origin.
        body_bbox=SymbolBBox(x_min=-100, y_min=0, x_max=100, y_max=400),
    )


class _Bridge:
    def __init__(self, rows):
        self._rows = rows

    def send_command(self, command, params=None, **kw):
        assert command == "generic.query_objects"
        return {"objects": self._rows}


def _lib(tmp_path) -> str:
    """A real file: SymbolCache.put stats lib_path and skips if it is gone."""
    path = tmp_path / "LIB.SchLib"
    path.write_text("", encoding="utf-8")
    return str(path)


def _write_snapshot(tmp_path, refdes_to_ref):
    project = tmp_path / "board.PrjPcb"
    project.write_text("", encoding="utf-8")
    snap = {
        "plan": {},
        "canvas": {"instances": [
            {"refdes": r, "lib_path": _lib(tmp_path), "lib_ref": ref,
             "x": 0, "y": 0, "rotation": 0}
            for r, ref in refdes_to_ref.items()
        ]},
    }
    project.with_suffix(".canvas.json").write_text(
        json.dumps(snap), encoding="utf-8")
    return project


def _cache(tmp_path, models):
    cache = SymbolCache(tmp_path / "symcache")
    for m in models:
        cache.put(m)
    return tmp_path / "symcache"


def test_positions_convert_from_origin_to_body_centre(tmp_path):
    model = _symbol("DIODE", _lib(tmp_path))
    project = _write_snapshot(tmp_path, {"D1": "DIODE"})
    cache_dir = _cache(tmp_path, [model])
    bridge = _Bridge([
        {"Designator.Text": "D1", "Location.X": 5000, "Location.Y": 3000,
         "Orientation": 0},
    ])

    out = hints_from_sheet(str(project), bridge=bridge, cache_dir=cache_dir)
    assert out["ok"] is True
    hint = out["hints"]["D1"]

    off_x, off_y = _center_offset(model, 0, False)
    assert (off_x, off_y) != (0, 0), (
        "fixture is wrong: a symbol whose centre IS its origin cannot "
        "detect the frame bug this test exists for")
    assert hint == {"x": 5000 + off_x, "y": 3000 + off_y, "rotation": 0}


def test_the_hint_round_trips_back_to_the_reported_location(tmp_path):
    """Feeding the hint through the emitter's own conversion returns it.

    This is the property that matters: hint the sheet at its current
    positions, re-run, and nothing moves.
    """
    model = _symbol("DIODE", _lib(tmp_path))
    project = _write_snapshot(tmp_path, {"D1": "DIODE"})
    cache_dir = _cache(tmp_path, [model])
    for rotation in (0, 90, 180, 270):
        bridge = _Bridge([
            {"Designator.Text": "D1", "Location.X": 5000, "Location.Y": 3000,
             "Orientation": rotation // 90},
        ])
        out = hints_from_sheet(str(project), bridge=bridge,
                               cache_dir=cache_dir)
        hint = out["hints"]["D1"]
        # The pipeline turns a placement centre back into an instance
        # origin exactly this way.
        off_x, off_y = _center_offset(model, hint["rotation"], False)
        assert (hint["x"] - off_x, hint["y"] - off_y) == (5000, 3000), (
            f"round trip lost the position at rotation {rotation}")


def test_a_part_missing_from_the_sheet_is_reported_not_guessed(tmp_path):
    project = _write_snapshot(tmp_path, {"D1": "DIODE", "R9": "DIODE"})
    cache_dir = _cache(tmp_path, [_symbol("DIODE", _lib(tmp_path))])
    bridge = _Bridge([
        {"Designator.Text": "D1", "Location.X": 100, "Location.Y": 200,
         "Orientation": 0},
    ])
    out = hints_from_sheet(str(project), bridge=bridge, cache_dir=cache_dir)
    assert "D1" in out["hints"]
    assert out["unmatched"] == ["R9"]
    assert "R9" not in out["hints"], (
        "an unreadable part must be left for the placer, never hinted at a "
        "guessed position")
    assert any("unhinted" in n for n in out["notes"])


def test_no_snapshot_says_what_to_run(tmp_path):
    project = tmp_path / "nothing.PrjPcb"
    project.write_text("", encoding="utf-8")
    out = hints_from_sheet(str(project), bridge=_Bridge([]))
    assert out["ok"] is False
    assert any("design_execute_plan" in n for n in out["notes"])


# ------------------------------------------------------------- mirror ---

def _dual_fet(lib_path: str = "/fake/lib.SchLib") -> SymbolModel:
    """The BSS138PS geometry from the multiviewer carrier library: a box
    whose origin is NOT the body centre, four pins left, two right."""
    def p(d, n, x, y, o):
        return SymbolPin(designator=d, name=n, x=x, y=y, orientation=o,
                         length=200, electrical_type="passive")
    return SymbolModel(
        lib_path=lib_path, lib_ref="BSS138PS",
        pins=(p("1", "S1", 200, 0, 2), p("4", "S2", 200, -100, 2),
              p("2", "G1", 200, -200, 2), p("5", "G2", 200, -300, 2),
              p("6", "D1", 800, 0, 0), p("3", "D2", 800, -100, 0)),
        body_bbox=SymbolBBox(x_min=150, y_min=-350, x_max=850, y_max=50),
    )


def test_hints_from_sheet_reads_the_live_mirror(tmp_path):
    """The LIVE IsMirrored, not the snapshot: a part mirrored by hand after
    the emit has a centre offset of the opposite sign in x."""
    lib = tmp_path / "LIB.SchLib"
    lib.write_text("", encoding="utf-8")
    model = _dual_fet(str(lib))
    cache = SymbolCache(tmp_path / "c")
    cache.put(model)
    project = tmp_path / "b.PrjPcb"
    project.write_text("", encoding="utf-8")
    project.with_suffix(".canvas.json").write_text(json.dumps({"plan": {}, "canvas": {
        "instances": [{"refdes": "Q1", "lib_path": str(lib),
                       "lib_ref": "BSS138PS", "x": 0, "y": 0, "rotation": 0}]}}),
        encoding="utf-8")

    for r in (0, 90, 180, 270):
        class B:
            def send_command(self, command, params=None, **kw):
                assert "IsMirrored" in params["properties"]
                return {"objects": [{"Designator.Text": "Q1",
                                     "Location.X": 5000, "Location.Y": 3000,
                                     "Orientation": altium_orientation(r, True) // 90,
                                     "IsMirrored": "true"}]}
        out = hints_from_sheet(str(project), bridge=B(), cache_dir=tmp_path / "c")
        h = out["hints"]["Q1"]
        assert h["mirror"] is True and h["rotation"] == r
        ox, oy = _center_offset(model, r, True)
        assert (h["x"] - ox, h["y"] - oy) == (5000, 3000)


def test_learner_reads_a_mirrored_part_at_rest_as_unrotated():
    """learn_from_layout diffs live rotation against the snapshot's canvas
    rotation; a mirrored 90 part is stored at Orientation 270 and must not
    read as a 180-degree edit."""
    from eda_agent.design.learner import _query_post_edit_positions

    class B:
        def send_command(self, command, params=None, **kw):
            return {"objects": [{"Designator.Text": "Q1", "Location.X": 0,
                                 "Location.Y": 0, "Orientation": 3,
                                 "IsMirrored": "true"},
                                {"Designator.Text": "Q2", "Location.X": 0,
                                 "Location.Y": 0, "Orientation": 1,
                                 "IsMirrored": "false"}]}
    pos = _query_post_edit_positions("p.PrjPcb", B(), {"ok": True, "notes": []})
    assert pos["Q1"] == {"x": 0, "y": 0, "rotation": 90, "mirror": True,
                         "altium_orientation": 270}
    assert pos["Q2"] == {"x": 0, "y": 0, "rotation": 90,
                         "altium_orientation": 90}


# ------------------------------------------- the unmeasured half (review M1)

def _snapshot_project(tmp_path, inst_extra):
    lib = tmp_path / "LIB.SchLib"
    lib.write_text("", encoding="utf-8")
    cache = SymbolCache(tmp_path / "c")
    cache.put(_dual_fet(str(lib)))
    project = tmp_path / "m.PrjPcb"
    project.write_text("", encoding="utf-8")
    inst = {"refdes": "Q1", "lib_path": str(lib), "lib_ref": "BSS138PS",
            "x": 0, "y": 0, "rotation": 0, **inst_extra}
    project.with_suffix(".canvas.json").write_text(json.dumps(
        {"plan": {}, "canvas": {"instances": [inst]}}), encoding="utf-8")
    return project


def _live(orientation, mirrored):
    class B:
        def send_command(self, command, params=None, **kw):
            return {"objects": [{"Designator.Text": "Q1", "Location.X": 5000,
                                 "Location.Y": 3000, "Orientation": orientation,
                                 "IsMirrored": "true" if mirrored else "false"}]}
    return B()


def test_a_hand_mirror_at_90_or_270_is_flagged(tmp_path):
    """altium_orientation assumes a world-frame mirror, measured at
    Orientation 0 only. A part mirrored BY HAND at 90/270 reads back on
    that assumption, so the hint carries a warning note."""
    project = _snapshot_project(tmp_path, {})
    for o, flagged in ((0, False), (2, False), (1, True), (3, True)):
        out = hints_from_sheet(str(project), bridge=_live(o, True),
                               cache_dir=tmp_path / "c")
        notes = [n for n in out["notes"] if "Orientation 90/270" in n]
        assert bool(notes) is flagged, (o, out["notes"])
    out = hints_from_sheet(str(project), bridge=_live(1, False),
                           cache_dir=tmp_path / "c")
    assert not [n for n in out["notes"] if "Orientation 90/270" in n]


def test_an_engine_written_mirror_at_90_reads_back_without_a_note(tmp_path):
    """The engine wrote Orientation altium_orientation(90, True) itself, so
    reading the same value back is self-consistent whatever Altium's model:
    no warning for the case the engine controls."""
    project = _snapshot_project(tmp_path, {"rotation": 90, "flipped": True})
    out = hints_from_sheet(
        str(project), bridge=_live(altium_orientation(90, True) // 90, True),
        cache_dir=tmp_path / "c")
    assert out["hints"]["Q1"]["rotation"] == 90
    assert not [n for n in out["notes"] if "Orientation 90/270" in n]


def _learn(tmp_path, snap_inst, live_bridge):
    from eda_agent.design.learner import learn_from_layout

    plan = {"spec": "s", "summary": "m", "sheets": [{"name": "main"}],
            "parts": [{"refdes": "Q1", "lib_ref": "X", "role": "switch"},
                      {"refdes": "U1", "lib_ref": "Y", "role": "ic"}],
            "nets": [{"name": "N1", "pins": [{"refdes": "Q1", "pin": "1"},
                                             {"refdes": "U1", "pin": "1"}]}]}
    project = tmp_path / "l.PrjPcb"
    project.write_text("", encoding="utf-8")
    project.with_suffix(".canvas.json").write_text(json.dumps({
        "plan": plan, "canvas": {"instances": [
            {"refdes": "Q1", "x": 0, "y": 0, "rotation": 0, **snap_inst},
            {"refdes": "U1", "x": 9000, "y": 9000, "rotation": 0}]}}),
        encoding="utf-8")

    class B:
        def send_command(self, command, params=None, **kw):
            rows = live_bridge.send_command(command, params)["objects"]
            rows.append({"Designator.Text": "U1", "Location.X": 9000,
                         "Location.Y": 9000, "Orientation": 0,
                         "IsMirrored": "false"})
            return {"objects": rows}
    return learn_from_layout(str(project), bridge=B(),
                             log_path=tmp_path / "edits.jsonl")


def test_the_learner_does_not_record_a_mirror_change(tmp_path):
    """Review L8: a hand mirror at 0 used to record nothing silently, and
    at 90/270 a bogus 180-degree rot_delta. Both are skipped, with a note."""
    for o in (0, 1):
        out = _learn(tmp_path, {}, _live(o, True))
        assert out["rows_appended"] == 0, out
        assert out.get("rows_skipped_mirror") == 1
        assert any("mirrored or un-mirrored by hand" in n for n in out["notes"])


def test_the_learner_skips_a_hand_turned_mirrored_part_at_90(tmp_path):
    out = _learn(tmp_path, {"flipped": True}, _live(1, True))
    assert out["rows_appended"] == 0
    assert any("Orientation 90/270" in n for n in out["notes"])
