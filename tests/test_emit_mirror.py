# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A mirrored part must reach Altium mirrored.

The canvas has always mirrored correctly (``SymbolInstance.flipped``), but
the flag never left Python: the emitter placed the part unmirrored, so the
sheet was wired for mirrored pins on an unmirrored part. The emitter now
places a mirrored part at ``altium_orientation(rotation, True)`` and then
calls the existing ``generic.mirror_component`` handler.

The mapping exists because the two frames disagree at 90/270: the canvas
mirrors in the SYMBOL frame and then rotates (world = Rot(r) . Mx), while
the handler reflects about Location.X in the WORLD frame and leaves
Orientation alone (world = Mx . Rot(p) = Rot(-p) . Mx), so p = -r.
"""

from __future__ import annotations

import pytest

from eda_agent.design.canvas import (
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    altium_orientation,
)
from eda_agent.design.emitter import EmitResult, _emit_placements, emit_canvas_delta
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin

_LIB = "/fake/lib.SchLib"
def _dual_fet(lib_path: str = _LIB) -> SymbolModel:
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


def _altium_mirror(inst_pins, origin_x):
    """What Gen_MirrorSchComponent does, transcribed from Generic.pas: X is
    reflected about the component's Location.X, a pin pointing left/right
    swaps, Orientation is left alone."""
    return sorted((ep.pin_id, 2 * origin_x - ep.x, ep.y,
                   {0: 2, 2: 0}.get(ep.orientation, ep.orientation))
                  for ep in inst_pins)


@pytest.mark.parametrize("r", [0, 90, 180, 270])
def test_place_then_altium_mirror_lands_on_the_canvas_pins(r):
    sym = _dual_fet()
    canvas = SymbolInstance(refdes="Q1", symbol=sym, x=5000, y=3000,
                            rotation=r, flipped=True)
    placed = SymbolInstance(refdes="Q1", symbol=sym, x=5000, y=3000,
                            rotation=altium_orientation(r, True))
    want = sorted((ep.pin_id, ep.x, ep.y, ep.orientation)
                  for ep in canvas.all_pin_endpoints())
    assert _altium_mirror(placed.all_pin_endpoints(), 5000) == want


def test_the_naive_mapping_is_wrong_at_90_and_270():
    """Why altium_orientation exists: passing the canvas rotation straight
    through puts every pin of a mirrored 90/270 part in the wrong place."""
    sym = _dual_fet()
    for r in (90, 270):
        canvas = SymbolInstance(refdes="Q1", symbol=sym, x=0, y=0,
                                rotation=r, flipped=True)
        naive = SymbolInstance(refdes="Q1", symbol=sym, x=0, y=0, rotation=r)
        want = sorted((ep.pin_id, ep.x, ep.y, ep.orientation)
                      for ep in canvas.all_pin_endpoints())
        assert _altium_mirror(naive.all_pin_endpoints(), 0) != want


class _Bridge:
    def __init__(self, mirror_reply=None, failed=""):
        self.sent = []
        self._mirror = mirror_reply
        self._failed = failed

    def send_command(self, command, params=None, **kw):
        self.sent.append((command, params or {}))
        if command == "generic.mirror_component":
            return self._mirror if self._mirror is not None else {
                "designator": params["designator"],
                "mirrored": params.get("mirrored") == "true",
                "primitives_moved": 9, "primitives_skipped": 0,
                "skipped_kinds": "", "changed": True}
        if command == "generic.place_sch_components_from_library":
            return {"placed": 2, "failed_refdes": self._failed}
        return {"success": True}

    def of(self, c):
        return [p for k, p in self.sent if k == c]


def _insts(*spec):
    return [SymbolInstance(refdes=r, symbol=_dual_fet(), x=1000, y=1000,
                           rotation=rot, flipped=f, sheet="main")
            for r, rot, f in spec]


def test_an_unmirrored_sheet_emits_exactly_what_it_did_before():
    br, res = _Bridge(), EmitResult()
    _emit_placements(_insts(("Q1", 90, False), ("Q2", 270, False)), br, res)
    assert [c for c, _ in br.sent] == ["generic.place_sch_components_from_library"]
    assert "rotation=90;" in br.sent[0][1]["placements"]
    assert "rotation=270;" in br.sent[0][1]["placements"]
    assert res.notes == []


def test_a_mirrored_part_is_placed_at_the_mapped_rotation_then_mirrored():
    br, res = _Bridge(), EmitResult()
    _emit_placements(_insts(("Q1", 90, True), ("Q2", 0, False)), br, res)
    ops = br.sent[0][1]["placements"].split("~~")
    assert "designator=Q1;rotation=270;" in ops[0]
    assert br.of("generic.mirror_component") == [
        {"designator": "Q1", "mirrored": "true"}]
    assert res.failures == []


def test_a_diagonal_mirror_says_its_stored_convention_is_unmeasured():
    """The geometry is exact at every rotation; how Altium STORES a
    mirrored 90/270 part (which Update From Libraries re-reads) is not yet
    measured, so the emit says so instead of staying silent."""
    br, res = _Bridge(), EmitResult()
    _emit_placements(_insts(("Q1", 90, True)), br, res)
    assert any("Q1" in n and "90" in n for n in res.notes)
    br, res = _Bridge(), EmitResult()
    _emit_placements(_insts(("Q1", 0, True)), br, res)
    assert res.notes == []


def test_a_mirror_the_bridge_does_not_confirm_is_a_failure():
    br, res = _Bridge(mirror_reply={"error": "NOT_FOUND"}), EmitResult()
    _emit_placements(_insts(("Q1", 0, True)), br, res)
    assert [f.code for f in res.failures] == ["MIRROR_FAILED"]


def test_a_part_that_failed_to_place_is_not_mirrored():
    br, res = _Bridge(failed="Q1:LOAD_FAILED"), EmitResult()
    _emit_placements(_insts(("Q1", 0, True)), br, res)
    assert br.of("generic.mirror_component") == []


def test_skipped_primitives_are_reported_not_failed():
    reply = {"mirrored": True, "primitives_skipped": 2, "skipped_kinds": "13"}
    br, res = _Bridge(mirror_reply=reply), EmitResult()
    _emit_placements(_insts(("Q1", 0, True)), br, res)
    assert res.failures == [] and any("13" in n for n in res.notes)


def _cv(rot, flip):
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_instance(SymbolInstance(refdes="Q1", symbol=_dual_fet(), x=1000,
                                   y=1000, rotation=rot, flipped=flip,
                                   sheet="main"))
    return cv


def _snap(rot, flip):
    d = {"refdes": "Q1", "lib_path": _LIB, "lib_ref": "BSS138PS",
         "x": 1000, "y": 1000, "rotation": rot}
    if flip:
        d["flipped"] = True
    return {"instances": [d]}


def _component_ops(br):
    return [(c, p.get("set") or p.get("mirrored")) for c, p in br.sent
            if c == "generic.mirror_component"
            or (c == "generic.modify_objects"
                and p.get("object_type") == "eSchComponent")]


def test_delta_a_mirror_change_alone_is_a_move():
    br = _Bridge()
    res = emit_canvas_delta(_cv(0, True), _snap(0, False), "p.PrjPcb", br)
    assert res.delta["moved"] == ["Q1"]
    assert _component_ops(br) == [
        ("generic.modify_objects", "Location.X=1000|Location.Y=1000"),
        ("generic.mirror_component", "true")]


def test_delta_a_mirrored_part_is_unmirrored_before_it_is_turned():
    br = _Bridge()
    emit_canvas_delta(_cv(90, True), _snap(0, True), "p.PrjPcb", br)
    assert _component_ops(br) == [
        ("generic.mirror_component", "false"),
        ("generic.modify_objects", "Orientation=3"),   # altium_orientation(90, True)
        ("generic.modify_objects", "Location.X=1000|Location.Y=1000"),
        ("generic.mirror_component", "true")]


def test_delta_an_unmirrored_snapshot_without_the_key_is_untouched():
    br = _Bridge()
    res = emit_canvas_delta(_cv(0, False), _snap(0, False), "p.PrjPcb", br)
    assert res.delta["untouched"] == ["Q1"] and _component_ops(br) == []


def test_delta_an_unconfirmed_mirror_is_a_failure():
    """Review L6: the full emit records MIRROR_FAILED when the bridge does
    not confirm; the delta path used to ignore the reply."""
    br = _Bridge(mirror_reply={"error": "NOT_FOUND"})
    res = emit_canvas_delta(_cv(0, True), _snap(0, False), "p.PrjPcb", br)
    assert [f.code for f in res.failures] == ["MIRROR_FAILED"]
    assert res.ok is False


def test_delta_an_unconfirmed_unmirror_stops_before_turning():
    """Turning a part that is still mirrored is the unmeasured path the
    un-mirror-first ordering exists to avoid, so the part is left alone."""
    br = _Bridge(mirror_reply={"mirrored": True})     # did not un-mirror
    res = emit_canvas_delta(_cv(90, True), _snap(0, True), "p.PrjPcb", br)
    assert [f.code for f in res.failures] == ["MIRROR_FAILED"]
    assert _component_ops(br) == [("generic.mirror_component", "false")]
