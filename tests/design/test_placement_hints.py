# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Tests for the agent-in-loop placement_hints API.

The hints API lets an agent (or any caller) override specific refdes
positions after compute_layout has run, without having to re-author
the whole plan. This is the contract every iteration of the
agent-in-loop refinement loop depends on.
"""

from __future__ import annotations

from typing import Optional

import pytest

from eda_agent.design.canvas import SymbolInstance
from eda_agent.design.layout import PlacedPart
from eda_agent.design.pipeline import (
    PipelineNote,
    PipelineResult,
    _apply_placement_hints,
    _canvas_instance_to_placement,
    _center_offset,
    build_canvas_from_plan,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import (
    SymbolBBox,
    SymbolExtractor,
    SymbolModel,
    SymbolPin,
)


_LIB = "/fake/lib.SchLib"


class MockExtractor(SymbolExtractor):
    def __init__(self, symbols: dict[tuple[str, str], SymbolModel]) -> None:
        self._symbols = symbols

    def extract_one(self, lib_path: str, lib_ref: str) -> Optional[SymbolModel]:
        return self._symbols.get((lib_path, lib_ref))

    def extract_many(self, refs):
        return {
            (lp, lr): self._symbols[(lp, lr)]
            for (lp, lr) in refs
            if (lp, lr) in self._symbols
        }


def _passive(lib_ref: str) -> SymbolModel:
    return SymbolModel(
        lib_path=_LIB, lib_ref=lib_ref,
        pins=(
            SymbolPin(designator="1", name="1", x=-100, y=0,
                      orientation=2, length=100, electrical_type="passive"),
            SymbolPin(designator="2", name="2", x=100, y=0,
                      orientation=0, length=100, electrical_type="passive"),
        ),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30),
    )


# ---------------------- _apply_placement_hints ----------------------


def test_hint_overrides_x_y_rotation():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
        PlacedPart(refdes="R2", sheet="main", x_mils=2000, y_mils=2000, rotation=0),
    ]
    hints = {"R1": {"x": 5000, "y": 5000, "rotation": 90}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    by_refdes = {p.refdes: p for p in out}
    assert (by_refdes["R1"].x_mils, by_refdes["R1"].y_mils, by_refdes["R1"].rotation) == (5000, 5000, 90)
    # R2 unchanged.
    assert (by_refdes["R2"].x_mils, by_refdes["R2"].y_mils, by_refdes["R2"].rotation) == (2000, 2000, 0)


def test_partial_hint_preserves_unspecified_fields():
    """Hint with only x specified should keep existing y + rotation."""
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1500, rotation=90),
    ]
    hints = {"R1": {"x": 3000}}  # y and rotation omitted
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    p = out[0]
    assert p.x_mils == 3000
    assert p.y_mils == 1500  # preserved
    assert p.rotation == 90  # preserved


def test_hint_snaps_to_100_mil_grid():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    hints = {"R1": {"x": 5047, "y": 5083}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    assert out[0].x_mils == 5000
    assert out[0].y_mils == 5000


def test_unknown_refdes_hint_logs_warning():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    hints = {"R99": {"x": 5000, "y": 5000}}
    result = PipelineResult()
    out = _apply_placement_hints(placements, hints, result)
    # R1 unchanged.
    assert out[0].x_mils == 1000
    # Warning surfaced.
    warnings = [n for n in result.notes if n.severity == "warning"]
    assert any("R99" in w.text for w in warnings)


def test_no_hints_passes_placements_through():
    placements = [
        PlacedPart(refdes="R1", sheet="main", x_mils=1000, y_mils=1000, rotation=0),
    ]
    result = PipelineResult()
    out = _apply_placement_hints(placements, {}, result)
    assert out == placements
    # No info note added either (zero hints = no-op).
    assert not any("placement hint" in n.text for n in result.notes)


# ---------------------- build_canvas_from_plan integration ----------------------


def test_pipeline_respects_placement_hints():
    """End-to-end: passing placement_hints to build_canvas_from_plan
    anchors the hinted refdes at the requested coordinate on the canvas."""
    symbols = {(_LIB, "RES"): _passive("RES")}
    plan = DesignPlan.model_validate({
        "spec": "x", "summary": "x",
        "sheets": [{"name": "main", "size": "A4"}],
        "parts": [
            {"refdes": "R1", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
            {"refdes": "R2", "lib_ref": "RES", "lib_path": _LIB,
             "status": "existing", "sheet": "main"},
        ],
        "nets": [
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "R1", "pin": "1"},
                {"refdes": "R2", "pin": "1"}]},
            {"name": "SIG", "pins": [
                {"refdes": "R1", "pin": "2"},
                {"refdes": "R2", "pin": "2"}]},
        ],
    })
    hints = {"R1": {"x": 4000, "y": 4000, "rotation": 0}}
    result = build_canvas_from_plan(
        plan, MockExtractor(symbols), placement_hints=hints,
    )
    assert result.ok, [f.text for f in result.failures]
    r1 = next(i for i in result.canvas.instances if i.refdes == "R1")
    assert r1.x == 4000
    assert r1.y == 4000
    # And the pipeline noted that hints were applied.
    assert any("placement hint" in n.text.lower() for n in result.notes)


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


def _pp(**kw):
    base = dict(refdes="Q1", sheet="main", x_mils=1000, y_mils=1000, rotation=0)
    base.update(kw)
    return PlacedPart(**base)


@pytest.mark.parametrize("key", ["mirror", "flipped"])
def test_mirror_hint_sets_the_flag(key):
    """``mirror`` is the documented key; ``flipped`` (the canvas field's own
    name, which the drag editor sends) stays accepted."""
    res = PipelineResult()
    out = _apply_placement_hints([_pp()], {"Q1": {key: True}}, res)
    assert out[0].flipped is True
    assert not [n for n in res.notes if n.severity == "warning"]


def test_mirror_hint_accepts_the_int_the_mcp_schema_coerces_to():
    # dict[str, dict[str, int]] turns JSON true into 1 before the pipeline
    out = _apply_placement_hints([_pp()], {"Q1": {"mirror": 1}}, PipelineResult())
    assert out[0].flipped is True


def test_a_hint_without_mirror_keeps_the_current_flag():
    out = _apply_placement_hints([_pp(flipped=True)], {"Q1": {"x": 3000}},
                                 PipelineResult())
    assert out[0].flipped is True and out[0].x_mils == 3000


def test_mirror_false_unmirrors():
    out = _apply_placement_hints([_pp(flipped=True)], {"Q1": {"mirror": False}},
                                 PipelineResult())
    assert out[0].flipped is False


def test_a_string_mirror_is_refused_not_truth_tested():
    res = PipelineResult()
    out = _apply_placement_hints([_pp()], {"Q1": {"mirror": "false"}}, res)
    assert out[0].flipped is False, 'bool("false") is True; must not mirror'
    assert any("not a boolean" in n.text for n in res.notes)


def test_conflicting_mirror_and_flipped_warn_and_mirror_wins():
    res = PipelineResult()
    out = _apply_placement_hints(
        [_pp()], {"Q1": {"mirror": True, "flipped": False}}, res)
    assert out[0].flipped is True
    assert any("mirror wins" in n.text for n in res.notes)


def test_an_unknown_hint_key_is_reported():
    """The reason this matters: "mirror" itself used to vanish in silence,
    and the layout agents concluded the engine could not mirror at all."""
    res = PipelineResult()
    _apply_placement_hints([_pp()], {"Q1": {"x": 1000, "mirrored": True}}, res)
    assert any("unknown key" in n.text and "mirrored" in n.text
               for n in res.notes), "a key nobody reads must not vanish"


def test_plain_hints_produce_no_new_notes():
    res = PipelineResult()
    _apply_placement_hints([_pp()], {"Q1": {"x": 1, "y": 2, "rotation": 90}}, res)
    assert [n.severity for n in res.notes] == ["info"]


def test_instance_to_placement_round_trip_keeps_mirror_and_centre():
    for r in (0, 90, 180, 270):
        inst = SymbolInstance(refdes="Q1", symbol=_dual_fet(), x=4000, y=3000,
                              rotation=r, flipped=True)
        p = _canvas_instance_to_placement(inst)
        assert p.flipped is True
        ox, oy = _center_offset(inst.symbol, r, True)
        assert (p.x_mils - ox, p.y_mils - oy) == (4000, 3000)


def test_two_pin_rotation_correction_keeps_the_mirror():
    from eda_agent.design.layout import correct_two_pin_rotation
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "C1", "lib_ref": "CAP", "lib_path": _LIB},
                  {"refdes": "C2", "lib_ref": "CAP", "lib_path": _LIB}],
        "nets": [
            {"name": "VCC", "is_power": True, "pins": [
                {"refdes": "C1", "pin": "1"}, {"refdes": "C2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"}]},
        ]})
    out = correct_two_pin_rotation(
        plan, [_pp(refdes="C1", rotation=0, flipped=True)], {"C1": _passive("CAP")})
    assert out[0].rotation == 270          # the correction did fire
    assert out[0].flipped is True          # ...and kept the hinted mirror


def test_pipeline_mirrors_a_hinted_part_end_to_end():
    """The PWR_IN_USB case: Q105 at rot 180 and Q108 at rot 0 + mirror nest
    their drain loops, so SDA_ISO and SCL_ISO no longer cross."""
    plan = DesignPlan.model_validate({
        "spec": "t", "summary": "t", "sheets": [{"name": "main"}],
        "parts": [{"refdes": "Q1", "lib_ref": "BSS138PS", "lib_path": _LIB},
                  {"refdes": "Q2", "lib_ref": "BSS138PS", "lib_path": _LIB}],
        "nets": [
            {"name": "SDA_ISO", "pins": [{"refdes": "Q1", "pin": "6"},
                                         {"refdes": "Q2", "pin": "6"}]},
            {"name": "SCL_ISO", "pins": [{"refdes": "Q1", "pin": "3"},
                                         {"refdes": "Q2", "pin": "3"}]},
        ]})
    ex = MockExtractor({(_LIB, "BSS138PS"): _dual_fet()})
    hints = {"Q1": {"x": 4000, "y": 4000, "rotation": 180},
             "Q2": {"x": 4000, "y": 2400, "rotation": 0, "mirror": True}}
    res = build_canvas_from_plan(plan, ex, placement_hints=hints)
    q1, q2 = res.canvas.instance_by_refdes("Q1"), res.canvas.instance_by_refdes("Q2")
    assert q2.flipped is True and q1.flipped is False
    # Both drains face LEFT, and the mirrored part has D1 ABOVE D2 while
    # the rot-180 one has D2 above D1: the nested order that lets the two
    # drain nets run without crossing.
    d1, d2 = q2.pin_world("6"), q2.pin_world("3")
    assert d1.orientation == 2 and d2.orientation == 2 and d1.y > d2.y
    e1, e2 = q1.pin_world("6"), q1.pin_world("3")
    assert e1.orientation == 2 and e2.y > e1.y
