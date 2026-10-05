# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""draw_style="stub_labels": dense parts drawn as stubs + labels by the engine.

The carrier's eFuse, USB-PD controller and power MOSFETs were excluded from
their plans and drawn by hand after execute (one 1100-mil stub and label per
pin, supply runs on a rail with one label, ground runs on a rail with one
outward ground glyph, No-ERC on unused pins), so previews could not show them.
These tests pin the engine version of that style and the contract that a plan
which does not ask for it is drawn exactly as before.
"""

from __future__ import annotations

import re

import pytest

from eda_agent.design.canvas import (
    NetLabel, NoErc, PowerPort, SchematicCanvas, Sheet, SymbolInstance)
from eda_agent.design.pipeline import (
    build_best_canvas_from_plan, build_canvas_from_plan)
from eda_agent.design.plan import DesignPlan, Part
from eda_agent.design.stub_labels import (
    RAIL_INSET_MILS, STUB_LEN_MILS, apply_draw_style_hints,
    build_stub_label_macro, pin_nets)
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin
from eda_agent.fileio.netlist_solver import solve_nets

from tests.design.test_pipeline import MockExtractor, _LIB, _passive


def _efuse() -> SymbolModel:
    """eFuse-shaped: left IN x3, EN, LDSTRT, GND x3, one NC; right OUT x2, PG.

    Origin top-left like the real TPS259827 symbol: pins attach at x=200 /
    x=1600, electrical ends at x=0 / x=1800.
    """
    left = [("1", "IN"), ("2", "IN"), ("3", "IN"), ("6", "EN"),
            ("12", "LDSTRT"), ("4", "GND"), ("5", "GND"), ("14", "GND"),
            ("9", "NC")]
    right = [("17", "OUT"), ("18", "OUT"), ("13", "PG")]
    pins = [SymbolPin(designator=d, name=n, x=200, y=-100 * i, orientation=2,
                      length=200, electrical_type="power")
            for i, (d, n) in enumerate(left)]
    pins += [SymbolPin(designator=d, name=n, x=1600, y=-100 * i, orientation=0,
                       length=200, electrical_type="power")
             for i, (d, n) in enumerate(right)]
    return SymbolModel(lib_path=_LIB, lib_ref="EFUSE", pins=tuple(pins),
                       body_bbox=SymbolBBox(x_min=150, y_min=-850,
                                            x_max=1650, y_max=50))


def _plan(style: bool = True, extra_parts=()) -> DesignPlan:
    u1 = {"refdes": "U1", "lib_ref": "EFUSE", "lib_path": _LIB,
          "value": "TPS259827", "sheet": "main"}
    if style:
        u1["draw_style"] = "stub_labels"
    parts = [u1] + [
        {"refdes": r, "lib_ref": "RES" if r[0] == "R" else "CAP",
         "lib_path": _LIB, "value": "1k", "sheet": "main"}
        for r in ("R1", "R2", "C1", "C2", "R3", *extra_parts)]
    return DesignPlan.model_validate({
        "spec": "efuse", "summary": "efuse with stub-label style",
        "sheets": [{"name": "main", "size": "A3"}],
        "parts": parts,
        "nets": [
            {"name": "VIN", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "1"}, {"refdes": "U1", "pin": "2"},
                {"refdes": "U1", "pin": "3"}, {"refdes": "C1", "pin": "1"},
                {"refdes": "R1", "pin": "1"}]},
            {"name": "VOUT", "is_power": True, "pins": [
                {"refdes": "U1", "pin": "17"}, {"refdes": "U1", "pin": "18"},
                {"refdes": "C2", "pin": "1"}]},
            {"name": "GND", "is_ground": True, "pins": [
                {"refdes": "U1", "pin": "12"}, {"refdes": "U1", "pin": "4"},
                {"refdes": "U1", "pin": "5"}, {"refdes": "U1", "pin": "14"},
                {"refdes": "C1", "pin": "2"}, {"refdes": "C2", "pin": "2"},
                {"refdes": "R2", "pin": "2"}]},
            # Two other pins: stays a wire, named once.
            {"name": "UVLO", "pins": [
                {"refdes": "U1", "pin": "6"}, {"refdes": "R1", "pin": "2"},
                {"refdes": "R2", "pin": "1"}]},
            # One other pin: that pin is labelled, never a stranded wire
            # (R3's other pin is on no net, so R3 is "all labels" and the
            # no-stranded rule must NOT promote PG to a one-pin wire).
            {"name": "PG", "pins": [
                {"refdes": "U1", "pin": "13"}, {"refdes": "R3", "pin": "1"}]},
        ],
    })


def _syms():
    return {(_LIB, "EFUSE"): _efuse(), (_LIB, "RES"): _passive("RES"),
            (_LIB, "CAP"): _passive("CAP")}


_HINTS = {"U1": {"x": 5000, "y": 6000, "rotation": 0},
          "R1": {"x": 1500, "y": 8000, "rotation": 90},
          "R2": {"x": 1500, "y": 4000, "rotation": 90},
          "C1": {"x": 2800, "y": 8800, "rotation": 90},
          "C2": {"x": 9000, "y": 7000, "rotation": 90},
          "R3": {"x": 9000, "y": 4500, "rotation": 0}}


# --------------------------------------------------------------------- schema

def test_part_accepts_the_style_and_defaults_to_none():
    assert Part(refdes="U1", lib_ref="X").draw_style is None
    assert Part(refdes="U1", lib_ref="X",
                draw_style="stub_labels").draw_style == "stub_labels"
    with pytest.raises(Exception):
        Part(refdes="U1", lib_ref="X", draw_style="fancy")


def test_no_hint_key_returns_the_same_plan_object():
    """The zero-change contract: a caller that never uses the option gets
    the identical plan back (no copy, nothing re-validated)."""
    plan = _plan(style=False)
    out, notes = apply_draw_style_hints(plan, _HINTS)
    assert out is plan and notes == []
    out, notes = apply_draw_style_hints(plan, None)
    assert out is plan


def test_unknown_hint_value_is_reported_not_applied():
    plan = _plan(style=False)
    out, notes = apply_draw_style_hints(plan, {"U1": {"draw_style": "fancy"}})
    assert out is plan and any("unknown draw_style" in n for n in notes)


# ------------------------------------------------------------------- geometry

def test_macro_matches_the_hand_drawn_style():
    plan = _plan()
    sym = _efuse()
    inst = SymbolInstance(refdes="U1", symbol=sym, x=0, y=0, rotation=0)
    m = build_stub_label_macro(inst, pin_nets(plan, "U1", sym))
    wires = {(w[0], w[1], w[2], w[3]): w[4] for w in m.wires}
    L, R = STUB_LEN_MILS, RAIL_INSET_MILS

    # VIN run (pins at y 0/-100/-200, ends at x=0): top pin full stub, the
    # rest short stubs to the rail, one rail, ONE label at the far end.
    assert wires[(0, 0, -L, 0)] == "VIN"
    assert wires[(0, -100, -R, -100)] == "VIN"
    assert wires[(0, -200, -R, -200)] == "VIN"
    assert wires[(-R, 0, -R, -200)] == "VIN"
    assert [lb for lb in m.labels if lb.text == "VIN"] == [
        NetLabel(text="VIN", x=-L, y=0, orientation=0, sheet="main")]

    # GND run starts at LDSTRT (pin 12, y=-400): one glyph at the end of its
    # stub pointing OUTWARD (left), rail down to the last GND pin.
    assert wires[(0, -400, -L, -400)] == "GND"
    assert wires[(-R, -400, -R, -700)] == "GND"
    assert m.ports == [PowerPort(text="GND", x=-L, y=-400, style="gnd_power",
                                 sheet="main", orientation=2)]

    # Signal on the left: label at the stub's far end, reading right.
    assert NetLabel(text="UVLO", x=-L, y=-300, orientation=0,
                    sheet="main") in m.labels
    # Signal on the right: label 100 out from the pin; the stub runs on and
    # its far end is an anchor the dangling-wire trim must keep.
    assert wires[(1800, -200, 1800 + L, -200)] == "PG"
    assert NetLabel(text="PG", x=1900, y=-200, orientation=0,
                    sheet="main") in m.labels
    assert (1800 + L, -200) in m.anchors

    # Unused pin: No-ERC on its end, no stub.
    assert m.no_ercs == [NoErc(x=0, y=-800, sheet="main")]
    assert not any((0, -800) in ((a, b), (c, d)) for (a, b, c, d) in wires)

    # Keep-out spans body, both stub fields and the outward glyph.
    x0, y0, x1, y1 = m.keepout
    assert x0 < -L and x1 >= 1800 + L and y0 <= -850 and y1 >= 50


def test_down_facing_ground_pins_run_along_x():
    pins = tuple(SymbolPin(designator=str(i + 1), name="GND", x=100 * i, y=-300,
                           orientation=3, length=100, electrical_type="power")
                 for i in range(3))
    sym = SymbolModel(lib_path=_LIB, lib_ref="B", pins=pins,
                      body_bbox=SymbolBBox(x_min=-50, y_min=-300, x_max=250,
                                           y_max=0))
    inst = SymbolInstance(refdes="U9", symbol=sym, x=0, y=0, rotation=0)
    m = build_stub_label_macro(inst, {str(i + 1): ("GND", True, True)
                                      for i in range(3)})
    L, R = STUB_LEN_MILS, RAIL_INSET_MILS
    segs = {(w[0], w[1], w[2], w[3]) for w in m.wires}
    assert (0, -400, 0, -400 - L) in segs
    assert (0, -400 - R, 200, -400 - R) in segs
    # Down is the natural ground direction, so the field stays None (the
    # PowerPort contract: only a non-natural direction is stored).
    from eda_agent.design.port_glyphs import port_orientation
    assert m.ports[0].orientation is None
    assert port_orientation(m.ports[0]) == 3
    assert (m.ports[0].x, m.ports[0].y) == (0, -400 - L)


def test_up_facing_ground_pins_get_a_label_not_an_upward_glyph():
    """Review L2: a ground symbol never points up. An up-facing ground run
    is named by a label, like a supply run."""
    pins = tuple(SymbolPin(designator=str(i + 1), name="GND", x=100 * i, y=0,
                           orientation=1, length=100, electrical_type="power")
                 for i in range(3))
    sym = SymbolModel(lib_path=_LIB, lib_ref="T", pins=pins,
                      body_bbox=SymbolBBox(x_min=-50, y_min=-300, x_max=250,
                                           y_max=0))
    inst = SymbolInstance(refdes="U9", symbol=sym, x=0, y=0, rotation=0)
    m = build_stub_label_macro(inst, {str(i + 1): ("GND", True, True)
                                      for i in range(3)})
    assert m.ports == []
    assert [lb.text for lb in m.labels] == ["GND"]
    lb = m.labels[0]
    assert lb.orientation == 1 and (lb.x, lb.y) == (0, 100 + 100)
    # The keep-out covers the rotated label (text left of the wire).
    assert m.keepout[0] <= lb.x - 110 and m.keepout[3] >= lb.y + 55 * 3


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("flipped", [False, True])
def test_macro_grounds_never_point_up_at_any_rotation(rotation, flipped):
    """The reviewer's gap: the macro at 90/270 (and mirrored). Wherever the
    eFuse's ground run ends up, its symbol points down or sideways (the
    outward hand style), never up -- and an up-facing run is a label."""
    from eda_agent.design.port_glyphs import port_orientation

    plan = _plan()
    sym = _efuse()
    inst = SymbolInstance(refdes="U1", symbol=sym, x=5000, y=5000,
                          rotation=rotation, flipped=flipped)
    m = build_stub_label_macro(inst, pin_nets(plan, "U1", sym))
    assert all(port_orientation(p) != 1 for p in m.ports), m.ports
    assert all(p.orientation != 3 for p in m.ports)   # natural = None
    gnd_names = [p for p in m.ports if p.text == "GND"] + [
        lb for lb in m.labels if lb.text == "GND"]
    assert len(gnd_names) == 1


# ------------------------------------------ plan/symbol mismatch (review H1)

def _mismatch_plan(pin: str) -> DesignPlan:
    raw = _plan().model_dump()
    for n in raw["nets"]:
        if n["name"] == "UVLO":
            for pr in n["pins"]:
                if pr["refdes"] == "U1":
                    pr["pin"] = pin
    return DesignPlan.model_validate(raw)


def test_a_pin_the_symbol_lacks_fails_the_build_on_a_styled_part():
    """H1: the macro branch skipped the pin check, so a PinRef naming no
    pin vanished, the real pin got a No-ERC, and the run said ok."""
    plan = _mismatch_plan("99")
    res = build_canvas_from_plan(plan, MockExtractor(_syms()),
                                 placement_hints=_HINTS)
    assert not res.ok
    assert any("pin '99' not found on U1 (symbol mismatch)" in f.text
               for f in res.failures)
    # Reported once, not again by the canvas validator.
    assert sum("pin '99' not found" in f.text for f in res.failures) == 1
    # The intended pin is unknown, so no pin of U1 is marked as unused.
    assert res.canvas.no_ercs == []


def test_the_plain_part_reports_the_same_mismatch():
    """Control: the same plan without the style always failed."""
    raw = _mismatch_plan("99").model_dump()
    for p in raw["parts"]:
        p.pop("draw_style", None)
    res = build_canvas_from_plan(DesignPlan.model_validate(raw),
                                 MockExtractor(_syms()), placement_hints=_HINTS)
    assert not res.ok
    assert any("pin '99' not found on U1" in f.text for f in res.failures)


def test_a_pin_name_shared_by_several_pins_never_gets_no_erc():
    """'GND' names pins 4, 5 and 14. pin_by_id resolves the name to pin 4
    only, so 5 and 14 are unconnected -- and must stay visible to ERC
    rather than be marked deliberately unused."""
    raw = _plan().model_dump()
    for n in raw["nets"]:
        if n["name"] == "GND":
            n["pins"] = [pr for pr in n["pins"]
                         if not (pr["refdes"] == "U1"
                                 and pr["pin"] in ("4", "5", "14"))]
            n["pins"].append({"refdes": "U1", "pin": "GND"})
    plan = DesignPlan.model_validate(raw)
    res = build_canvas_from_plan(plan, MockExtractor(_syms()),
                                 placement_hints=_HINTS)
    u1 = res.canvas.instance_by_refdes("U1")
    marked = {(m.x, m.y) for m in res.canvas.no_ercs}
    for pid in ("4", "5", "14"):
        ep = u1.pin_world(pid)
        assert (ep.x, ep.y) not in marked, pid
    # The genuinely unused NC pin still gets its mark.
    nc = u1.pin_world("9")
    assert (nc.x, nc.y) in marked
    assert any("pin name 'GND'" in n.text and "matches 3 pins" in n.text
               for n in res.notes)


def test_no_erc_exempt_lists_name_and_designator_targets():
    from eda_agent.design.stub_labels import no_erc_exempt
    sym = _efuse()
    exempt, problems = no_erc_exempt(_plan(), "U1", sym)
    assert {"1", "2", "3", "17", "18", "4", "5", "14", "12", "6", "13"} <= exempt
    assert "9" not in exempt and problems == []
    exempt, _ = no_erc_exempt(_mismatch_plan("99"), "U1", sym)
    assert exempt == {p.designator for p in sym.pins}


# ------------------------------------------------------------------- pipeline

def _solve(canvas, plan):
    pins, want = [], {}
    for n in plan.nets:
        for pr in n.pins:
            ep = canvas.pin_world(pr.refdes, pr.pin)
            want[f"{pr.refdes}.{ep.pin_id}"] = n.name
    for inst in canvas.instances:
        for ep in inst.all_pin_endpoints():
            pins.append({"component": inst.refdes, "pin": ep.pin_id,
                         "x": ep.x, "y": ep.y})
    got = solve_nets(
        pins,
        [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2} for w in canvas.wires],
        [{"x": p.x, "y": p.y, "name": p.text} for p in canvas.power_ports],
        [{"x": j.x, "y": j.y} for j in canvas.junctions],
        [{"x": lb.x, "y": lb.y, "name": lb.text} for lb in canvas.labels])
    return want, got


@pytest.fixture(scope="module")
def styled():
    plan = _plan()
    return plan, build_best_canvas_from_plan(plan, MockExtractor(_syms()),
                                             placement_hints=_HINTS, n_tries=2)


def test_pipeline_draws_the_macro_and_the_netlist_is_intact(styled):
    plan, res = styled
    assert res.ok, [f.text for f in res.failures]
    cv = res.canvas
    u1 = cv.instance_by_refdes("U1")
    assert u1.stub_macro is not None
    drawn = {(w.x1, w.y1, w.x2, w.y2) for w in cv.wires}
    # every macro segment survived routing, the cull and the trim
    for w in u1.stub_macro.wires:
        a, b = (w[0], w[1]), (w[2], w[3])
        assert any({a, b} <= {(s[0], s[1]), (s[2], s[3])} or
                   (s[0] == s[2] == a[0] == b[0] and min(s[1], s[3]) <= min(a[1], b[1])
                    and max(s[1], s[3]) >= max(a[1], b[1])) or
                   (s[1] == s[3] == a[1] == b[1] and min(s[0], s[2]) <= min(a[0], b[0])
                    and max(s[0], s[2]) >= max(a[0], b[0]))
                   for s in drawn), w
    assert cv.no_ercs == [NoErc(x=u1.pin_world("9").x, y=u1.pin_world("9").y,
                                sheet="main")]
    want, got = _solve(cv, plan)
    for pid, net in want.items():
        assert got["pin_nets"][pid] == net, (pid, net, got["pin_nets"][pid])
    assert got["name_conflicts"] == []


def test_nets_reach_the_part_by_label(styled):
    plan, res = styled
    cv = res.canvas
    texts = [lb.text for lb in cv.labels]
    # UVLO: wired R1-R2 plus ONE label on that wire, besides U1's own.
    assert texts.count("UVLO") == 2
    # PG: R3's single pin is labelled, so R3 is not stranded.
    assert texts.count("PG") == 2
    # No wire of another net enters the keep-out.
    x0, y0, x1, y1 = cv.instance_by_refdes("U1").keepout
    own = {w[4] for w in cv.instance_by_refdes("U1").stub_macro.wires}
    from eda_agent.design.router import _segment_crosses_rect
    for w in cv.wires:
        if w.net in own:
            continue
        assert not _segment_crosses_rect(w.x1, w.y1, w.x2, w.y2, x0, y0, x1, y1), w


def test_hint_and_plan_field_draw_the_same_sheet(styled):
    _, res = styled
    hinted = dict(_HINTS, U1=dict(_HINTS["U1"], draw_style="stub_labels"))
    res2 = build_best_canvas_from_plan(_plan(style=False), MockExtractor(_syms()),
                                       placement_hints=hinted, n_tries=2)
    assert res2.canvas.to_dict() == res.canvas.to_dict()


def test_a_part_in_the_stubs_counts_as_an_overlap():
    from eda_agent.design.quality import score_canvas
    plan = _plan()
    hints = dict(_HINTS, R3={"x": 7000, "y": 5900, "rotation": 0})  # inside PG's stub field
    res = build_canvas_from_plan(plan, MockExtractor(_syms()),
                                 placement_hints=hints, strict_shorts=False)
    assert score_canvas(res.canvas, plan).body_overlaps >= 1


def test_without_the_style_the_canvas_dict_has_no_new_keys():
    res = build_canvas_from_plan(_plan(style=False), MockExtractor(_syms()),
                                 placement_hints=_HINTS)
    d = res.canvas.to_dict()
    assert "no_ercs" not in d
    assert all("orientation" not in p for p in d["power_ports"])
    assert all(i.stub_macro is None and i.keepout is None
               for i in res.canvas.instances)


# --------------------------------------------------------------- emit/render

class _Bridge:
    def __init__(self):
        self.sent = []

    def send_command(self, command, params=None, **kw):
        self.sent.append((command, params or {}))
        return {"success": True}

    def of(self, command):
        return [p for c, p in self.sent if c == command]


def test_emitter_aims_ports_and_places_no_ercs():
    from eda_agent.design.emitter import EmitResult, _emit_power_ports, _emit_no_ercs
    b, r = _Bridge(), EmitResult(project_path="p.PrjPcb")
    _emit_power_ports([PowerPort(text="GND", x=0, y=0, style="gnd_power",
                                 orientation=2),
                       PowerPort(text="GND", x=9, y=9, style="gnd_power")],
                      b, r, "main")
    payload = b.of("generic.place_power_ports")[0]["ports"]
    assert "x=0;y=0;style=gnd_power;orientation=2" in payload
    assert "x=9;y=9;style=gnd_power;orientation=3" in payload   # unchanged default
    _emit_no_ercs([NoErc(x=100, y=-800)], b, r, "main")
    assert b.of("generic.place_no_erc") == [{"x": "100", "y": "-800"}]


def test_delta_clears_only_the_engines_previous_no_ercs():
    from eda_agent.design.emitter import _COPPER_TYPES, emit_canvas_delta
    assert "eNoERC" not in _COPPER_TYPES     # a hand-placed No-ERC survives
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_no_ercs([NoErc(x=5, y=6, sheet="main")])
    b = _Bridge()
    emit_canvas_delta(cv, {"instances": [], "no_ercs": [
        {"x": 1, "y": 2, "sheet": "main"}]}, "p.PrjPcb", b)
    dels = [p for p in b.of("generic.delete_objects")
            if p["object_type"] == "eNoERC"]
    assert dels == [{"scope": "active_doc", "object_type": "eNoERC",
                     "filter": "Location.X=1|Location.Y=2"}]
    assert b.of("generic.place_no_erc") == [{"x": "5", "y": "6"}]


def test_render_shows_no_erc_and_sideways_ground(styled):
    from eda_agent.design.render_svg import render_canvas_svg
    svg = render_canvas_svg(styled[1].canvas)
    assert svg.count('class="no-erc"') == 1
    assert 'text-anchor="end">GND</text>' in svg      # left-pointing glyph


def test_mcp_hint_annotation_accepts_a_string_value():
    import inspect
    from pydantic import TypeAdapter
    from eda_agent.tools import design as tools_design
    src = inspect.getsource(tools_design)
    assert "dict[str, dict[str, int]]" not in src
    TypeAdapter(dict[str, dict[str, object]]).validate_python(
        {"U1": {"x": 1, "draw_style": "stub_labels"}})


def test_a_promoted_macro_net_still_carries_its_name():
    """PG reaches U1 by label. Its two other pins sit on parts whose every
    other pin is unconnected, so the no-stranded rule promotes PG to a
    wire; that wire must still carry PG's name or it splits from U1."""
    raw = _plan().model_dump()
    raw["parts"].append({"refdes": "R4", "lib_ref": "RES", "lib_path": _LIB,
                         "value": "1k", "sheet": "main"})
    for n in raw["nets"]:
        if n["name"] == "PG":
            n["force_label"] = True
            n["pins"].append({"refdes": "R4", "pin": "1"})
    plan = DesignPlan.model_validate(raw)
    hints = dict(_HINTS, R4={"x": 9000, "y": 3500, "rotation": 0})
    res = build_canvas_from_plan(plan, MockExtractor(_syms()), placement_hints=hints)
    assert res.ok, [f.text for f in res.failures]
    want, got = _solve(res.canvas, plan)
    for pid, net in want.items():
        assert got["pin_nets"][pid] == net, (pid, net, got["pin_nets"][pid])


# ------------------------------------------------- interplay with E1 / E3

def test_every_named_net_still_reaches_its_name(styled):
    """The last-mile pin check (wire_named / _unnamed_pins) on the styled
    sheet: no pin of a net that must carry its name is cut off from it."""
    from eda_agent.design.pipeline import _unnamed_pins

    plan, res = styled
    assert _unnamed_pins(res.canvas, plan, "main") == []
    assert not [n for n in res.notes if "does not reach a" in n.text]


def _one_ground_pin_part():
    """A part whose single left-facing GND pin makes a one-pin ground run:
    its glyph hangs off a private stub, the shape the glyph collision pass
    would otherwise re-route."""
    from eda_agent.design.canvas import WireSegment

    sym = SymbolModel(
        lib_path=_LIB, lib_ref="G1",
        pins=(SymbolPin(designator="1", name="GND", x=0, y=0, orientation=2,
                        length=100, electrical_type="power"),),
        body_bbox=SymbolBBox(x_min=0, y_min=-100, x_max=400, y_max=100))
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main", width_mils=8000, height_mils=8000))
    inst = SymbolInstance(refdes="U9", symbol=sym, x=4000, y=4000, rotation=0)
    cv.add_instance(inst)
    macro = build_stub_label_macro(inst, {"1": ("GND", True, True)})
    inst.stub_macro, inst.keepout = macro, macro.keepout
    cv.add_wires([WireSegment(*w[:4], net=w[4]) for w in macro.wires])
    cv.add_power_ports(macro.ports)
    glyph = macro.ports[0]
    # Another net's wire straight through the sideways glyph's name.
    cv.add_wires([WireSegment(glyph.x - 300, glyph.y - 200,
                              glyph.x - 300, glyph.y + 200, net="OTHER")])
    # The pass reads only the net -> pin map (a one-pin net is not a valid
    # DesignPlan, so a plain namespace stands in).
    from types import SimpleNamespace
    plan = SimpleNamespace(nets=[SimpleNamespace(
        name="GND", pins=[SimpleNamespace(refdes="U9", pin="1")])])
    return cv, plan, glyph


def test_the_glyph_pass_never_moves_a_stub_label_glyph():
    from eda_agent.design import port_glyphs as G

    cv, plan, glyph = _one_ground_pin_part()
    assert G.glyph_collisions(cv, plan, "main"), "fixture must collide"
    before = cv.to_dict()
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    assert cv.to_dict() == before

    # Control: the same drawing without the macro marker IS re-routed, so
    # the guard (not a lack of candidates) is what keeps it in place.
    cv2, plan2, _ = _one_ground_pin_part()
    cv2.instance_by_refdes("U9").stub_macro = None
    assert G.resolve_port_glyph_collisions(cv2, plan2) >= 1


def test_a_draw_style_hint_is_not_reported_as_an_unknown_key(styled):
    """The hint path must not trip the unknown-hint-key warning: the key is
    read, just earlier, by apply_draw_style_hints."""
    hinted = dict(_HINTS, U1=dict(_HINTS["U1"], draw_style="stub_labels"))
    res = build_canvas_from_plan(_plan(style=False), MockExtractor(_syms()),
                                 placement_hints=hinted)
    assert not [n for n in res.notes if "unknown key" in n.text]


# ------------------------------------------ vertical labels (review L4)

def test_one_label_box_definition_respects_orientation():
    from eda_agent.design.pipeline import _label_text_box
    from eda_agent.design.port_glyphs import _GlyphSheet
    from eda_agent.design.text_placement import label_text_box

    up = NetLabel(text="GND", x=1000, y=2000, orientation=1, sheet="main")
    down = NetLabel(text="GND", x=1000, y=2000, orientation=3, sheet="main")
    flat = NetLabel(text="GND", x=1000, y=2000, orientation=0, sheet="main")
    assert label_text_box(up) == (890, 2000, 1000, 2165)
    assert label_text_box(down) == (1000, 1835, 1110, 2000)
    assert label_text_box(flat) == (1000, 2000, 1165, 2110)
    assert _label_text_box(up) == label_text_box(up)
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_labels([up])
    assert _GlyphSheet(cv, type("P", (), {"nets": []})(), "main").labels == [
        label_text_box(up)]


def test_render_draws_a_vertical_label_rotated():
    from eda_agent.design.render_svg import render_canvas_svg

    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main"))
    cv.add_labels([NetLabel(text="UP", x=1000, y=2000, orientation=1,
                            sheet="main"),
                   NetLabel(text="FLAT", x=3000, y=2000, orientation=0,
                            sheet="main")])
    svg = render_canvas_svg(cv)
    up = re.search(r'<text[^>]*>UP</text>', svg).group(0)
    assert 'transform="rotate(-90 ' in up and 'text-anchor="start"' in up
    flat = re.search(r'<text[^>]*>FLAT</text>', svg).group(0)
    assert "rotate" not in flat
