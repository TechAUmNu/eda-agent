# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One net label per connected wire group, never two on one point.

Two generators were measured before the fix:
  - the label-per-pin branch of _wire_sheet labels every stub end, and two
    same-net pins facing each other closer than two stub lengths get stubs
    that meet (ends coincide) or overlap (merged into one wire) -- two labels
    on one wire. PWR_1V8 PWR_SEQ_DLY (same point), PWR_EFUSE PWR_EFUSE_UVLO /
    PWR_IMON_RAW (200 mil apart), buck FB, mcu LED1_DRV;
  - the cross-net cull relabels a net the label-per-pin branch had already
    labelled, so every re-admitted full-length stub carries two identical
    labels. mcu BTN2 (five points) and GPIO2_M (two).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pytest

from eda_agent.design import pipeline
from eda_agent.design.canvas import (
    BusSegment,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)

PLANS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"
_LIB = "/fake/lib.SchLib"


def _canvas(*sheets: str) -> SchematicCanvas:
    cv = SchematicCanvas()
    for s in sheets or ("main",):
        cv.add_sheet(Sheet(name=s))
    return cv


def _lab(text, x, y, sheet="main", just=0):
    return NetLabel(text=text, x=x, y=y, orientation=0, sheet=sheet,
                    justification=just)


def _texts(cv):
    return sorted((l.text, l.x, l.y) for l in cv.labels)


# ------------------------------------------------------------------ unit ---

def test_two_identical_labels_on_one_point_become_one():
    """PWR_1V8's shape: two 300-mil stubs meet end to end at the joint."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 300, net="N"),
                  WireSegment(0, 300, 0, 600, net="N")])
    cv.add_labels([_lab("N", 0, 300), _lab("N", 0, 300)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1
    assert _texts(cv) == [("N", 0, 300)]
    assert len(cv.wires) == 2          # copper is never touched


def test_overlapping_stubs_keep_one_label():
    """PWR_EFUSE's shape: the flush merged two overlapping stubs into one
    wire, leaving both stub-end labels on it 200 mil apart."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 400, net="UVLO")])
    cv.add_labels([_lab("UVLO", 0, 100), _lab("UVLO", 0, 300)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1
    assert len([l for l in cv.labels if l.text == "UVLO"]) == 1


def test_a_t_join_is_one_group():
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 600, 0, net="N"),
                  WireSegment(300, -300, 300, 0, net="N")])   # ends ON the span
    cv.add_labels([_lab("N", 600, 0), _lab("N", 300, -300)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1


def test_labels_on_separate_groups_all_stay():
    """PWR_EFUSE_RDLY / PWR_EFUSE_PG: one label per pin on stubs that do not
    touch is exactly what label-per-pin means."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 300, net="N"),
                  WireSegment(1000, 0, 1000, 300, net="N")])
    cv.add_labels([_lab("N", 0, 300), _lab("N", 1000, 300)])
    before = list(cv.labels)
    assert pipeline._dedupe_net_labels(cv, "main") == 0
    assert cv.labels == before


def test_a_plain_crossing_is_not_a_connection():
    """Altium does not join two wires that merely cross, so neither does the
    grouping: both labels are load-bearing."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 300, 600, 300, net="N"),
                  WireSegment(300, 0, 300, 600, net="N")])
    cv.add_labels([_lab("N", 0, 300), _lab("N", 300, 0)])
    assert pipeline._dedupe_net_labels(cv, "main") == 0


def test_a_label_joining_two_groups_leaves_the_net_alone():
    """A label sitting where two groups cross is what joins them in Altium;
    dropping it -- or anything beside it -- could split the net."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 300, 600, 300, net="N"),
                  WireSegment(300, 0, 300, 600, net="N")])
    cv.add_labels([_lab("N", 300, 300), _lab("N", 600, 300)])
    assert pipeline._dedupe_net_labels(cv, "main") == 0


def test_other_nets_and_other_sheets_are_independent():
    cv = _canvas("main", "other")
    cv.add_wires([WireSegment(0, 0, 0, 400, net="A"),
                  WireSegment(0, 0, 0, 400, net="A", sheet="other")])
    cv.add_labels([_lab("A", 0, 100), _lab("A", 0, 300, sheet="other"),
                   _lab("B", 0, 100)])            # foreign text: shorts check's job
    assert pipeline._dedupe_net_labels(cv, "main") == 0
    assert pipeline._dedupe_net_labels(cv, "other") == 0


def test_unattached_labels_are_left_alone_except_exact_repeats():
    """A label on no wire of its own net (a bus name on its bus line, or a
    stray) is not this pass's business -- unless it is an exact repeat."""
    cv = _canvas()
    cv.add_buses([BusSegment(0, 0, 0, 800)])
    cv.add_labels([_lab("D[0..7]", 0, 900), _lab("D[0..7]", 0, -100),
                   _lab("X", 500, 500), _lab("X", 500, 500)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1
    assert _texts(cv) == [("D[0..7]", 0, -100), ("D[0..7]", 0, 900),
                          ("X", 500, 500)]


def test_the_clear_label_survives_over_one_that_collides():
    """Best-placed means fewest text collisions; only a tie falls back to
    emission order. The FIRST label's text runs over a power-port glyph."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 400, net="N")])
    cv.add_power_ports([PowerPort(text="GND", x=50, y=150, style="gnd_power")])
    cv.add_labels([_lab("N", 0, 100), _lab("N", 0, 300, just=2)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1
    assert [(l.x, l.y, l.justification) for l in cv.labels] == [(0, 300, 2)]


def test_ties_keep_the_first_emitted_label():
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 400, net="N")])
    cv.add_labels([_lab("N", 0, 300), _lab("N", 0, 100)])
    pipeline._dedupe_net_labels(cv, "main")
    assert _texts(cv) == [("N", 0, 300)]


def test_same_point_labels_keep_the_side_that_reads_clear():
    """Two stubs meeting head-on give two labels on the joint with OPPOSITE
    justifications (each grows away from its own pin). The one whose text
    lands on a body loses, even though it was emitted first."""
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 300, 0, net="N"),
                  WireSegment(300, 0, 600, 0, net="N")])
    sym = SymbolModel(lib_path=_LIB, lib_ref="BOX", pins=(),
                      body_bbox=SymbolBBox(x_min=0, y_min=0, x_max=200, y_max=200))
    cv.add_instance(SymbolInstance(refdes="U1", symbol=sym, x=330, y=0, rotation=0))
    cv.add_labels([_lab("N", 300, 0, just=0), _lab("N", 300, 0, just=2)])
    assert pipeline._dedupe_net_labels(cv, "main") == 1
    assert [(l.x, l.y, l.justification) for l in cv.labels] == [(300, 0, 2)]


def test_nothing_to_do_returns_zero_and_keeps_order():
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 300, net="A"),
                  WireSegment(500, 0, 500, 300, net="B")])
    cv.add_labels([_lab("B", 500, 300), _lab("A", 0, 300)])
    before = list(cv.labels)
    assert pipeline._dedupe_net_labels(cv, "main") == 0
    assert cv.labels == before


def test_idempotent():
    cv = _canvas()
    cv.add_wires([WireSegment(0, 0, 0, 400, net="N")])
    cv.add_labels([_lab("N", 0, 100), _lab("N", 0, 300), _lab("N", 0, 300)])
    assert pipeline._dedupe_net_labels(cv, "main") == 2
    after = list(cv.labels)
    assert pipeline._dedupe_net_labels(cv, "main") == 0
    assert cv.labels == after


# ----------------------------------------------------------- integration ---

def _passive(lib_ref: str) -> SymbolModel:
    # Hotspots at x = -200 (pin 1, faces left) and +200 (pin 2, faces right).
    return SymbolModel(
        lib_path=_LIB, lib_ref=lib_ref,
        pins=(SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                        length=100, electrical_type="passive"),
              SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                        length=100, electrical_type="passive")),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30))


class _Mock(SymbolExtractor):
    def __init__(self):
        self._s = {(_LIB, "RES"): _passive("RES")}

    def extract_one(self, lib_path, lib_ref) -> Optional[SymbolModel]:
        return self._s.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {r: self._s[r] for r in refs if r in self._s}


def _facing_pair_plan(gap_x: int):
    """J1 - R1 = R2 - J2 in a row; R1.2 and R2.1 share a force_label net and
    face each other. ``gap_x`` is R2's centre x with R1 at 0."""
    parts = [{"refdes": r, "lib_ref": "RES", "lib_path": _LIB,
              "status": "existing", "sheet": "main"}
             for r in ("J1", "R1", "R2", "J2")]
    nets = [
        {"name": "A", "pins": [{"refdes": "J1", "pin": "2"}, {"refdes": "R1", "pin": "1"}]},
        {"name": "MID", "force_label": True,
         "pins": [{"refdes": "R1", "pin": "2"}, {"refdes": "R2", "pin": "1"}]},
        {"name": "B", "pins": [{"refdes": "R2", "pin": "2"}, {"refdes": "J2", "pin": "1"}]},
    ]
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": parts, "nets": nets})
    hints = {"J1": {"x": 3000, "y": 4000, "rotation": 0},
             "R1": {"x": 4000, "y": 4000, "rotation": 0},
             "R2": {"x": 4000 + gap_x, "y": 4000, "rotation": 0},
             "J2": {"x": 5000 + gap_x, "y": 4000, "rotation": 0}}
    return plan, hints


@pytest.mark.parametrize("gap_x,shape", [(1000, "stub ends coincide"),
                                         (800, "stubs overlap")])
def test_facing_force_label_pins_get_one_label(gap_x, shape):
    plan, hints = _facing_pair_plan(gap_x)
    res = pipeline.build_canvas_from_plan(plan, _Mock(), placement_hints=hints)
    assert res.ok, [f.text for f in res.failures]
    r1 = res.canvas.pin_world("R1", "2")
    r2 = res.canvas.pin_world("R2", "1")
    assert r2.x - r1.x == gap_x - 400, "fixture geometry drifted"
    mid = [l for l in res.canvas.labels if l.text == "MID"]
    assert len(mid) == 1, (shape, [(l.x, l.y) for l in mid])
    assert res.label_count == len(res.canvas.labels)
    # Still bonded: the survivor sits on MID copper that reaches both pins.
    w = [s for s in res.canvas.wires if s.net == "MID"]
    assert any(pipeline._point_on_segment(mid[0].x, mid[0].y, s.x1, s.y1, s.x2, s.y2)
               for s in w)


# ----------------------------------------------------- golden benchmarks ---

def _wire_group_label_repeats(canvas, sheet):
    """(text, points) for every same-text label pair sharing a point or one
    connected group of that net's wires."""
    from collections import defaultdict
    out = []
    labs = canvas.labels_on(sheet)
    pts = defaultdict(int)
    for l in labs:
        pts[(l.text, l.x, l.y)] += 1
    out += [k for k, n in pts.items() if n > 1]
    by_net = defaultdict(list)
    for w in canvas.wires_on(sheet):
        by_net[w.net].append(w)
    for text in {l.text for l in labs}:
        own = by_net.get(text, [])
        if not own:
            continue
        parent = list(range(len(own)))

        def find(i):
            while parent[i] != i:
                i = parent[i]
            return i
        for i, a in enumerate(own):
            for j, b in enumerate(own[i + 1:], i + 1):
                if any(pipeline._point_on_segment(px, py, c.x1, c.y1, c.x2, c.y2)
                       for (px, py, c) in ((a.x1, a.y1, b), (a.x2, a.y2, b),
                                           (b.x1, b.y1, a), (b.x2, b.y2, a))):
                    parent[find(i)] = find(j)
        seen = defaultdict(set)
        for l in labs:
            if l.text != text:
                continue
            for k, w in enumerate(own):
                if pipeline._point_on_segment(l.x, l.y, w.x1, w.y1, w.x2, w.y2):
                    seen[find(k)].add((l.x, l.y))
        out += [(text, sorted(v)) for v in seen.values() if len(v) > 1]
    return out


@pytest.mark.parametrize("name", ["buck", "mcu"])
def test_golden_plans_draw_no_stacked_labels(name):
    """Before the fix: buck FB twice on (6400,3900); mcu 8 redundant labels
    (BTN2 x5 and GPIO2_M x2 repeated by the cull, LED1_DRV twice on one wire)."""
    from eda_agent.design.benchmark import SyntheticSymbolExtractor
    plan = DesignPlan.model_validate(
        json.loads((PLANS_DIR / f"{name}.json").read_text(encoding="utf-8")))
    res = pipeline.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))
    for sheet in res.canvas.sheets:
        assert _wire_group_label_repeats(res.canvas, sheet.name) == []
