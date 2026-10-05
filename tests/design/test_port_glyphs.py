# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A power-port glyph's bar and name must not land on other copper.

The paths that create glyphs reason about the HOTSPOT only, so on the
multiviewer carrier's power sheets seven glyphs were drawn across wires:

* PWR_IN_BARREL: U102 OUT's PWR_VIN_PROT bar 20 mil under the
  PWR_BARREL_VS row, its 800-mil riser making 4 of the sheet's 6 crossings;
* PWR_IN_USB: U115 OUT's repair glyph, bar under the EN wire, name on OV;
* PWR_3V3: a redundant GND glyph sitting ON the PGND16 wire;
* PWR_1V8: SPSP GND drops ending 100 mil short of same-net stubs.

``port_glyphs.resolve_port_glyph_collisions`` runs on the winner only, after
the stub upgrade. The real cases below are frozen fixtures (a canvas window
with its symbols embedded and the sheet's plan), so they need no Altium and
no symbol cache.
"""

from __future__ import annotations

import copy
import functools
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from eda_agent.design import port_glyphs as G
from eda_agent.design.canvas import (
    Junction,
    NetLabel,
    PowerPort,
    SchematicCanvas,
    Sheet,
    SymbolInstance,
    WireSegment,
)
from eda_agent.design.plan import DesignPlan
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin
from eda_agent.design._wiring import _point_on_segment

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "port_glyphs"
PLANS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"
_NO_NETS = SimpleNamespace(nets=[])


# ----------------------------------------------------------------- helpers

def _load(name: str):
    fx = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    sheet = fx["sheet"]["name"]
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name=sheet, size=fx["sheet"]["size"]))
    syms = {k: SymbolModel.from_dict(v) for k, v in fx["symbols"].items()}
    for i in fx["instances"]:
        cv.add_instance(SymbolInstance(
            refdes=i["refdes"], x=i["x"], y=i["y"], rotation=i["rotation"],
            symbol=syms[i["lib"]], sheet=sheet,
            flipped=bool(i.get("flipped", False))))
    cv.add_wires([WireSegment(**w) for w in fx["wires"]])
    cv.add_labels([NetLabel(**lb) for lb in fx["labels"]])
    cv.add_power_ports([PowerPort(**p) for p in fx["power_ports"]])
    cv.add_junctions([Junction(**j) for j in fx["junctions"]])
    return cv, DesignPlan.model_validate(fx["plan"]), sheet


def _blank(width: int = 6000, height: int = 6000) -> SchematicCanvas:
    cv = SchematicCanvas()
    cv.add_sheet(Sheet(name="main", width_mils=width, height_mils=height))
    return cv


def _seg(w) -> tuple:
    a, b = (w[0], w[1]), (w[2], w[3])
    return (*min(a, b), *max(a, b))


def _wires(cv, net=None) -> set:
    return {_seg((w.x1, w.y1, w.x2, w.y2)) + (w.net,) for w in cv.wires
            if net is None or w.net == net}


def _contacts(canvas, plan) -> set:
    """Every place two DIFFERENT nets' geometry touches, as Altium bonds."""
    net_of_pin = {(pr.refdes, pr.pin): n.name for n in plan.nets for pr in n.pins}
    owned: dict = {}
    for inst in canvas.instances:
        for ep in inst.all_pin_endpoints():
            name = net_of_pin.get((inst.refdes, ep.pin_id))
            if name:
                owned[(ep.x, ep.y, inst.sheet)] = name
    for p in canvas.power_ports:
        owned.setdefault((p.x, p.y, p.sheet), p.text)
    out = set()
    wires = [w for w in canvas.wires if w.net]
    for w in wires:
        for (px, py, sh), other in owned.items():
            if sh == w.sheet and other != w.net and _point_on_segment(
                    px, py, w.x1, w.y1, w.x2, w.y2):
                out.add((px, py, *sorted((other, w.net))))
        for o in wires:
            if o.net == w.net or o.sheet != w.sheet:
                continue
            for (ex, ey) in ((w.x1, w.y1), (w.x2, w.y2)):
                if _point_on_segment(ex, ey, o.x1, o.y1, o.x2, o.y2):
                    out.add((ex, ey, *sorted((w.net, o.net))))
    return out


def _reaches_glyph(canvas, plan, sheet: str, net: str) -> set:
    """Own-net pins with a wire path to a glyph of ``net`` (independent of
    the module under test: plain union-find on endpoints and T-joins)."""
    wires = [w for w in canvas.wires if w.sheet == sheet and w.net == net]
    pins = {}
    for inst in canvas.instances_on(sheet):
        for ep in inst.all_pin_endpoints():
            if any(pr.refdes == inst.refdes and pr.pin == ep.pin_id
                   for n in plan.nets if n.name == net for pr in n.pins):
                pins[(inst.refdes, ep.pin_id)] = (ep.x, ep.y)
    glyphs = [(p.x, p.y) for p in canvas.power_ports
              if p.sheet == sheet and p.text == net]
    nodes = [("w", i) for i in range(len(wires))]
    parent = {n: n for n in nodes}

    def find(n):
        while parent[n] != n:
            n = parent[n]
        return n

    def on(pt, w):
        return _point_on_segment(pt[0], pt[1], w.x1, w.y1, w.x2, w.y2)

    for i, a in enumerate(wires):
        for j in range(i + 1, len(wires)):
            b = wires[j]
            if (on((a.x1, a.y1), b) or on((a.x2, a.y2), b)
                    or on((b.x1, b.y1), a) or on((b.x2, b.y2), a)):
                parent[find(("w", i))] = find(("w", j))
    glyph_groups = {find(("w", i)) for i, w in enumerate(wires)
                    for g in glyphs if on(g, w)}
    out = set()
    for key, pt in pins.items():
        if pt in glyphs:
            out.add(key)
            continue
        groups = {find(("w", i)) for i, w in enumerate(wires) if on(pt, w)}
        if groups & glyph_groups:
            out.add(key)
    return out


def _crossings(canvas) -> int:
    from eda_agent.design.quality import _count_wire_crossings

    return sum(
        _count_wire_crossings(
            [(w.x1, w.y1, w.x2, w.y2) for w in canvas.wires if w.sheet == s.name],
            [w.net for w in canvas.wires if w.sheet == s.name])
        for s in canvas.sheets)


# --------------------------------------------------------------- geometry

def _norm_line(v) -> tuple:
    a, b = (round(v[0], 1), round(v[1], 1)), (round(v[2], 1), round(v[3], 1))
    return (*min(a, b), *max(a, b))


def _svg_lines(svg: str) -> set:
    return {_norm_line([float(x) for x in m])
            for m in re.findall(
                r'<line x1="([-\d.]+)" y1="([-\d.]+)" x2="([-\d.]+)" y2="([-\d.]+)"',
                svg)}


@pytest.mark.parametrize("style", ["bar", "gnd_power"])
def test_geometry_matches_the_renderer(style):
    """The check and the preview must agree on where a glyph is drawn."""
    from eda_agent.design import render_svg

    sheet = Sheet(name="main")
    opts = render_svg.RenderOptions()
    port = PowerPort(text="NET", x=3000, y=3000, style=style)
    svg = render_svg._svg_power_port_body(port, sheet, opts)

    ink, tb = G.glyph_geometry(port.x, port.y, port.text, port.style)

    def to_svg(s):
        a = render_svg._mils_to_svg(s[0], s[1], sheet, opts)
        b = render_svg._mils_to_svg(s[2], s[3], sheet, opts)
        return _norm_line((*a, *b))

    drawn = _svg_lines(svg)
    modelled = {to_svg(s) for s in ink}
    assert drawn and drawn <= modelled, (drawn, modelled)
    # The name: renderer baseline vs the model's (box bottom + descent).
    m = re.search(r'<text x="([-\d.]+)" y="([-\d.]+)"', svg)
    _bx, by = render_svg._mils_to_svg(port.x, tb[1] + G.DESCENT, sheet, opts)
    assert round(float(m.group(2)), 1) == round(by, 1)
    cx, _ = render_svg._mils_to_svg(port.x, port.y, sheet, opts)
    assert abs(float(m.group(1)) - cx) < 0.6


def _hits_on(cv, port) -> int:
    cv.add_power_ports([port])
    return len(G.glyph_collisions(cv, _NO_NETS, "main"))


def test_bar_on_the_next_grid_row_collides():
    port = PowerPort(text="VCC", x=1000, y=1000, style="bar")
    ink, tb = G.glyph_geometry(1000, 1000, "VCC", "bar")
    for y, expect in ((1100, 1), (1200, 1), (1400, 0)):
        cv = _blank()
        cv.add_wires([WireSegment(700, y, 1300, y, net="OTHER")])
        assert _hits_on(cv, port) == expect, y
    # y=1100 is the bar's clearance, y=1200 is the name box.
    assert any(abs(s[1] - 1100) <= G.INK_CLR for s in ink if s[1] == s[3])
    assert tb[1] < 1200 < tb[3]
    # A wire ENDING at the hotspot is the glyph's own connection.
    cv = _blank()
    cv.add_wires([WireSegment(1000, 600, 1000, 1000, net="VCC")])
    assert _hits_on(cv, port) == 0


def test_same_net_wire_through_the_glyph_counts():
    """PWR_3V3's shape: a GND glyph on a GND wire's interior reads as a
    glyph sitting on copper, even though the net is its own."""
    port = PowerPort(text="GND", x=1000, y=1000, style="gnd_power")
    cv = _blank()
    cv.add_wires([WireSegment(700, 1000, 1300, 1000, net="GND")])
    assert _hits_on(cv, port) == 1


def _resistor() -> SymbolModel:
    return SymbolModel(
        lib_path="t.SchLib", lib_ref="RES", designator_prefix="R",
        pins=(SymbolPin("1", "1", 0, 0, 2, 100, "passive"),
              SymbolPin("2", "2", 400, 0, 0, 100, "passive")),
        body_bbox=SymbolBBox(x_min=100, y_min=-50, x_max=300, y_max=50))


def test_collision_free_glyph_is_never_touched():
    from eda_agent.design.text_placement import place_instance_text

    cv = _blank()
    cv.add_instance(SymbolInstance(refdes="R1", x=2000, y=3000, rotation=0,
                                   symbol=_resistor()))
    cv.add_wires([WireSegment(2000, 3000, 2000, 2600, net="GND"),
                  WireSegment(2400, 3000, 2400, 3400, net="VCC")])
    cv.add_power_ports([PowerPort("GND", 2000, 2600, "gnd_power"),
                        PowerPort("VCC", 2400, 3400, "bar")])
    place_instance_text(cv)
    plan = SimpleNamespace(nets=[
        SimpleNamespace(name="GND", pins=[SimpleNamespace(refdes="R1", pin="1")]),
        SimpleNamespace(name="VCC", pins=[SimpleNamespace(refdes="R1", pin="2")])])
    before = (cv.to_dict(), [(i.designator_pos, i.value_pos) for i in cv.instances])
    assert G.glyph_collisions(cv, plan, "main") == {}
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    after = (cv.to_dict(), [(i.designator_pos, i.value_pos) for i in cv.instances])
    assert after == before


# ------------------------------------------------------------ real cases

def test_barrel_out_glyph_drops_down():
    """H1, the human-approved hand fix: turn at x=9000 and point the bar
    DOWN. 500 mil rather than H1's 800: 400 is the first height whose name
    clears the pin-7 GND name, but its bar sits 38 mil from that name, so
    the RANK_PAD clearance ranking takes the next grid step."""
    cv, plan, sheet = _load("barrel_u102_vin_prot")
    contacts0, cross0 = _contacts(cv, plan), _crossings(cv)
    assert G.resolve_port_glyph_collisions(cv, plan) == 1
    glyph = [p for p in cv.power_ports if p.text == "PWR_VIN_PROT"]
    assert [(p.x, p.y, p.orientation) for p in glyph] == [(9000, 4800, 3)]
    assert _wires(cv, "PWR_VIN_PROT") >= {
        (9000, 5300, 9400, 5300, "PWR_VIN_PROT"),
        (9000, 4800, 9000, 5300, "PWR_VIN_PROT")}
    ctx = G._GlyphSheet(cv, plan, sheet)
    gi = next(i for i, p in ctx.ports() if p.text == "PWR_VIN_PROT")
    g = cv.power_ports[gi]
    assert ctx.hits(gi, g.x, g.y, g.text, g.style, g.orientation, ctx.wires(),
                    ctx.ports(), pad=G.RANK_PAD)[0] == 0
    assert (8800, 5300, 8800, 5900, "PWR_VIN_PROT") not in _wires(cv)
    assert G.glyph_collisions(cv, plan, sheet) == {}
    assert _contacts(cv, plan) <= contacts0
    assert _crossings(cv) < cross0


def test_repair_glyph_between_rows_is_rerouted():
    cv, plan, sheet = _load("usb_u115_vin_prot")
    reach0 = _reaches_glyph(cv, plan, sheet, "PWR_VIN_PROT")
    contacts0 = _contacts(cv, plan)
    assert G.resolve_port_glyph_collisions(cv, plan) == 1
    # (18000, 9300), not the shortest clearing (18100, 9300): there the
    # name ends ~70 mil from the GND riser at x=18500 (RANK_PAD ranking).
    moved = [p for p in cv.power_ports
             if p.text == "PWR_VIN_PROT" and (p.x, p.y) == (18000, 9300)]
    assert moved and moved[0].orientation == 3
    assert G.glyph_collisions(cv, plan, sheet) == {}
    assert _contacts(cv, plan) <= contacts0
    assert ("U115", "9") in reach0      # the anchor pin (OUT)
    assert _reaches_glyph(cv, plan, sheet, "PWR_VIN_PROT") >= reach0


def test_redundant_glyph_on_same_net_wire_is_shared():
    """Literally steps (1) and (2) of the layout agent's manual cleanup --
    and step (3), the AGND spoke: its glyph's ink touches nothing, but its
    drop crossed the PGND15/PGND16/EP rows with no junction. It is cut at
    the first crossing, with a dot, leaving ONE GND glyph per converter."""
    cv, plan, sheet = _load("3v3_u105_gnd")
    reach0 = _reaches_glyph(cv, plan, sheet, "GND")
    all0 = sum(1 for a in cv.wires for b in cv.wires
               if a.net == b.net == "GND" and G._cross(
                   (a.x1, a.y1, a.x2, a.y2), (b.x1, b.y1, b.x2, b.y2))) // 2
    assert all0 >= 3
    assert G.resolve_port_glyph_collisions(cv, plan) == 2
    assert (4300, 8400) not in {(p.x, p.y) for p in cv.power_ports}
    assert (4500, 8200) not in {(p.x, p.y) for p in cv.power_ports}
    assert (4500, 8500, 4500, 8600, "GND") in _wires(cv)
    assert (4500, 8500) in {(j.x, j.y) for j in cv.junctions}
    assert not any(a.net == b.net == "GND" and G._cross(
        (a.x1, a.y1, a.x2, a.y2), (b.x1, b.y1, b.x2, b.y2))
        for a in cv.wires for b in cv.wires)
    assert (4300, 8500, 4300, 8900, "GND") in _wires(cv)
    junctions = {(j.x, j.y) for j in cv.junctions}
    assert (4300, 8500) in junctions and (4300, 8400) not in junctions
    assert _reaches_glyph(cv, plan, sheet, "GND") >= reach0
    assert G.glyph_collisions(cv, plan, sheet) == {}


def test_share_needs_a_glyph_at_the_other_end():
    """Negative control for the connectivity guard: with the glyph the
    trunk leads to removed, cutting the drop would strand the pins."""
    cv, plan, _sheet = _load("3v3_u105_gnd")
    cv.power_ports[:] = [p for p in cv.power_ports if (p.x, p.y) != (4100, 7900)]
    before = list(cv.power_ports)
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    assert cv.power_ports == before


def test_share_never_cuts_at_its_own_drop():
    """A merged drop wire running past its hub is the glyph's OWN copper,
    not something to share with: no zero-length cut at the hotspot."""
    cv, plan, sheet = _load("3v3_u105_gnd")
    ctx = G._GlyphSheet(cv, plan, sheet)
    wires, ports = ctx.wires(), ctx.ports()
    gi = next(i for i, p in ports if (p.x, p.y) == (4100, 7900))
    port = cv.power_ports[gi]
    kind, _root, path = ctx.topology(gi, port, wires, ports)
    assert kind == "B" and path[0] == (4100, 7900)
    assert G._share_candidate(ctx, gi, port, kind, path, wires, ports) is None


def test_the_1v8_spsp_drop_ends_on_pgnd():
    cv, plan, sheet = _load("1v8_u111_gnd")
    reach0 = _reaches_glyph(cv, plan, sheet, "GND")
    n_ports = len(cv.power_ports)
    assert G.resolve_port_glyph_collisions(cv, plan) == 1
    assert len(cv.power_ports) == n_ports - 1
    assert G.glyph_collisions(cv, plan, sheet) == {}
    assert _reaches_glyph(cv, plan, sheet, "GND") >= reach0


@pytest.mark.parametrize("name", ["barrel_u102_vin_prot", "usb_u115_vin_prot",
                                  "3v3_u105_gnd", "1v8_u111_gnd"])
def test_pass_is_idempotent(name):
    cv, plan, _sheet = _load(name)
    assert G.resolve_port_glyph_collisions(cv, plan) >= 1
    snap = cv.to_dict()
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    assert cv.to_dict() == snap


def test_port_hint_glyph_is_left_alone():
    cv, plan, _sheet = _load("barrel_u102_vin_prot")
    before = cv.to_dict()
    assert G.resolve_port_glyph_collisions(
        cv, plan, port_hints={"PWR_VIN_PROT": {"x": 8800, "y": 5900}}) == 0
    assert cv.to_dict() == before


def test_supply_flip_can_be_switched_off():
    """Convention-strict mode: supply bars only point up, so USB's repair
    glyph has no fix and stays as drawn."""
    cv, plan, _sheet = _load("usb_u115_vin_prot")
    before = cv.to_dict()
    assert G.resolve_port_glyph_collisions(cv, plan, allow_supply_flip=False) == 0
    assert cv.to_dict() == before


# ---------------------------------------------------- serialise and emit

def test_to_dict_omits_orientation_when_none():
    cv = _blank()
    cv.add_power_ports([PowerPort("GND", 0, 0, "gnd_power"),
                        PowerPort("VCC", 100, 0, "bar", orientation=3)])
    ports = cv.to_dict()["power_ports"]
    assert "orientation" not in ports[0]
    assert ports[1]["orientation"] == 3


class _RecordingBridge:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def send_command(self, command, params=None, **_kw):
        self.calls.append((command, dict(params or {})))
        return {"success": True}


def test_emitter_sends_the_port_orientation():
    from eda_agent.design.emitter import EmitResult, _emit_power_ports

    bridge = _RecordingBridge()
    _emit_power_ports([PowerPort("GND", 0, 0, "gnd_power"),
                       PowerPort("VCC", 100, 0, "bar"),
                       PowerPort("V5", 200, 0, "bar", orientation=3)],
                      bridge, EmitResult(), "main")
    (cmd, params), = bridge.calls
    assert cmd == "generic.place_power_ports"
    ops = params["ports"].split("~~")
    assert ops[0].endswith("orientation=3")      # unchanged default
    assert ops[1].endswith("orientation=1")      # unchanged default
    assert ops[2].endswith("orientation=3")      # explicit, re-aimed


def test_render_down_bar_draws_below_the_hotspot():
    from eda_agent.design import render_svg

    sheet = Sheet(name="main")
    opts = render_svg.RenderOptions()
    port = PowerPort(text="PWR_VIN_PROT", x=3000, y=3000, style="bar",
                     orientation=3)
    svg = render_svg._svg_power_port_body(port, sheet, opts)
    _hx, hy = render_svg._mils_to_svg(port.x, port.y, sheet, opts)
    lines = _svg_lines(svg)
    assert lines and all(min(l[1], l[3]) >= hy - 0.01 for l in lines)
    ty = float(re.search(r'<text x="[-\d.]+" y="([-\d.]+)"', svg).group(1))
    assert ty > hy
    # The natural bar (orientation None) still takes the old path, unchanged.
    natural = PowerPort(text="PWR_VIN_PROT", x=3000, y=3000, style="bar")
    assert render_svg._svg_power_port_body(natural, sheet, opts) == \
        render_svg._svg_power_port_body(
            PowerPort(text="PWR_VIN_PROT", x=3000, y=3000, style="bar",
                      orientation=1), sheet, opts)


# ------------------------------------------------------ benchmark boards
#
# Each board is built ONCE with the pass switched off; "on" is that same
# canvas with the pass applied, exactly as build_best_canvas_from_plan does
# last. That isolates the pass as the only variable.

@functools.lru_cache(maxsize=None)
def _board_off(name: str):
    from eda_agent.design.benchmark import SyntheticSymbolExtractor
    from eda_agent.design.pipeline import build_best_canvas_from_plan

    plan = DesignPlan.model_validate(
        json.loads((PLANS_DIR / f"{name}.json").read_text(encoding="utf-8")))
    with mock.patch("eda_agent.design.pipeline.resolve_port_glyph_collisions",
                    return_value=0):
        canvas = build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan)).canvas
    return canvas, plan


@functools.lru_cache(maxsize=None)
def _board_pair(name: str):
    from eda_agent.design.text_placement import place_instance_text

    off, plan = _board_off(name)
    on = copy.deepcopy(off)
    if G.resolve_port_glyph_collisions(on, plan):
        place_instance_text(on)
    return off, on, plan


_BOARDS = ("blinker555", "buck", "mcu")


@pytest.mark.parametrize("board", _BOARDS)
def test_ground_never_faces_up_or_sideways(board):
    _off, on, _plan = _board_pair(board)
    for p in on.power_ports:
        if G.is_ground_style(p.style):
            assert p.orientation in (None, 3), p


def test_fixture_grounds_never_face_up_or_sideways():
    for name in ("3v3_u105_gnd", "1v8_u111_gnd", "barrel_u102_vin_prot",
                 "usb_u115_vin_prot"):
        cv, plan, _sheet = _load(name)
        G.resolve_port_glyph_collisions(cv, plan)
        for p in cv.power_ports:
            if G.is_ground_style(p.style):
                assert p.orientation in (None, 3), (name, p)


def _signal_wires(canvas, plan) -> list:
    rails = {n.name for n in plan.nets if n.is_power or n.is_ground}
    rails |= {p.text for p in canvas.power_ports}
    return sorted((w.x1, w.y1, w.x2, w.y2, w.net) for w in canvas.wires
                  if w.net not in rails)


@pytest.mark.parametrize("board", _BOARDS)
def test_pass_adds_no_crossing_and_no_contact(board):
    off, on, plan = _board_pair(board)
    assert _crossings(on) <= _crossings(off)
    assert not (_contacts(on, plan) - _contacts(off, plan))
    assert ({i.refdes: (i.x, i.y, i.rotation, i.flipped) for i in on.instances}
            == {i.refdes: (i.x, i.y, i.rotation, i.flipped) for i in off.instances})
    assert _signal_wires(on, plan) == _signal_wires(off, plan)


def _drawn_label_overlaps(canvas) -> int:
    from eda_agent.design.text_placement import CHAR_W, LINE_H

    n = 0
    for p in canvas.power_ports:
        ink, tb = G.glyph_geometry(p.x, p.y, p.text, p.style, p.orientation)
        for lab in canvas.labels:
            if lab.sheet != p.sheet:
                continue
            w = CHAR_W * max(1, len(lab.text))
            x1 = lab.x - w if getattr(lab, "justification", 0) == 2 else lab.x
            lb = (x1, lab.y, x1 + w, lab.y + LINE_H)
            if G._box_hit(tb, lb) or any(G._box_hit(G._seg_box(s, 0), lb, -1)
                                         for s in ink):
                n += 1
    return n


@pytest.mark.parametrize("board", _BOARDS)
def test_drawn_label_overlaps_never_increase(board):
    """Measured with the geometry that is actually DRAWN. The stub-upgrade
    test's symmetric proxy box also covers the wire side of a glyph, where
    nothing is drawn, so it is the wrong yardstick for a pass that re-aims."""
    off, on, _plan = _board_pair(board)
    assert _drawn_label_overlaps(on) <= _drawn_label_overlaps(off)


def test_the_pass_reduces_collisions_on_the_dense_board():
    off, on, plan = _board_pair("mcu")

    def colliding(cv):
        return sum(len(G.glyph_collisions(cv, plan, s.name)) for s in cv.sheets)

    assert colliding(on) < colliding(off)


def test_a_sheet_that_would_gain_a_cross_net_meeting_is_put_back(monkeypatch):
    """The belt-and-braces guard: whatever the candidate filters missed, a
    sheet never leaves the pass with a new place where two nets bond."""
    cv, plan, _sheet = _load("barrel_u102_vin_prot")
    before = cv.to_dict()
    calls = iter([{}, {"PWR_VIN_PROT": 1, "OTHER": 1}])
    monkeypatch.setattr(G, "_cross_net_meeting_counts", lambda _w: next(calls))
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    assert cv.to_dict() == before


def test_a_failure_mid_sheet_leaves_the_sheet_as_it_was(monkeypatch):
    cv, plan, _sheet = _load("barrel_u102_vin_prot")
    before = cv.to_dict()
    real = G._apply

    def apply_then_fail(*a, **k):
        real(*a, **k)
        raise RuntimeError("boom")

    monkeypatch.setattr(G, "_apply", apply_then_fail)
    report: dict = {}
    assert G.resolve_port_glyph_collisions(cv, plan, report=report) == 0
    assert cv.to_dict() == before
    assert report["errors"] == [("PWR_IN_BARREL", "RuntimeError: boom")]


def test_a_failing_sheet_does_not_stop_the_sheets_after_it(monkeypatch):
    """Review L1: the exception used to propagate, so sheets after the
    failing one were never visited while sheets before it kept their
    changes -- and the pipeline then skipped the text re-placement. Now
    the failing sheet is restored and the rest still run."""
    import itertools

    cv, plan, sheet = _load("barrel_u102_vin_prot")
    cv2, _plan2, _ = _load("barrel_u102_vin_prot")
    # Two copies of the same sheet under different names.
    from eda_agent.design.canvas import Sheet as _Sheet
    cv.add_sheet(_Sheet(name="COPY", size="A3"))
    for inst in cv2.instances:
        cv.add_instance(SymbolInstance(refdes=inst.refdes + "Z",
                                       symbol=inst.symbol, x=inst.x, y=inst.y,
                                       rotation=inst.rotation, sheet="COPY"))
    cv.add_wires([WireSegment(w.x1, w.y1, w.x2, w.y2, sheet="COPY", net=w.net)
                  for w in cv2.wires])
    cv.add_power_ports([PowerPort(p.text, p.x, p.y, p.style, sheet="COPY")
                        for p in cv2.power_ports])
    raw = plan.model_dump()
    for n in raw["nets"]:
        n["pins"] += [{"refdes": pr["refdes"] + "Z", "pin": pr["pin"]}
                      for pr in n["pins"]]
    raw["parts"] += [dict(p, refdes=p["refdes"] + "Z", sheet="COPY")
                     for p in raw["parts"]]
    raw["sheets"] = raw["sheets"] + [{"name": "COPY"}]
    plan = DesignPlan.model_validate(raw)
    first = [s for s in cv.to_dict()["power_ports"] if s["sheet"] == sheet]
    real = G._resolve_sheet
    calls = itertools.count()

    def fail_first(canvas, plan_, name, *a, **k):
        if next(calls) == 0:
            real(canvas, plan_, name, *a, **k)    # half-apply, then raise
            raise RuntimeError("boom")
        return real(canvas, plan_, name, *a, **k)

    monkeypatch.setattr(G, "_resolve_sheet", fail_first)
    report: dict = {}
    assert G.resolve_port_glyph_collisions(cv, plan, report=report) == 1
    assert [e[0] for e in report["errors"]] == [sheet]
    assert [s for s in cv.to_dict()["power_ports"] if s["sheet"] == sheet] == first
    copy_vin = [(p.x, p.y, p.orientation) for p in cv.power_ports
                if p.sheet == "COPY" and p.text == "PWR_VIN_PROT"]
    assert copy_vin == [(9000, 4800, 3)]


# --------------------------------------- supply flip modes (review M2)

def test_supply_flip_modes():
    assert G.supply_flip_mode(True) == "any"
    assert G.supply_flip_mode(False) == "off"
    assert G.supply_flip_mode("down") == "down"
    with pytest.raises(ValueError):
        G.supply_flip_mode("sideways")
    port = PowerPort("VCC", 0, 0, "bar")
    anchor = {"x": 1000, "y": 1000, "u": (1, 0)}
    orient = lambda mode: {g[2] for _s, g in G._reroute_candidates(port, anchor, mode)}
    assert orient("off") == {1}
    assert orient("down") == {1, 3}
    assert orient("any") == {0, 1, 3}
    gnd = PowerPort("GND", 0, 0, "gnd_power")
    assert {g[2] for _s, g in G._reroute_candidates(gnd, anchor, "any")} == {3}


@pytest.mark.parametrize("board", _BOARDS)
def test_default_mode_never_points_a_supply_bar_sideways(board):
    """The approved hand fix was "point the bar down"; sideways was never
    asked for, so it is opt-in ("any") and the default cannot produce it."""
    _off, on, _plan = _board_pair(board)
    for p in on.power_ports:
        assert G.port_orientation(p) in (1, 3), p


def test_build_best_canvas_passes_the_flip_mode_through():
    from eda_agent.design import pipeline

    seen = []
    real = G.resolve_port_glyph_collisions

    def spy(canvas, plan, **kw):
        seen.append(kw.get("allow_supply_flip"))
        return real(canvas, plan, **kw)

    plan = DesignPlan.model_validate(
        json.loads((PLANS_DIR / "blinker555.json").read_text(encoding="utf-8")))
    from eda_agent.design.benchmark import SyntheticSymbolExtractor
    with mock.patch.object(pipeline, "resolve_port_glyph_collisions", spy):
        pipeline.build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan), n_tries=1,
            allow_supply_flip="off")
        pipeline.build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan), n_tries=1)
    assert seen == ["off", "down"]


# ------------------------------------- kind-B moves and own-drop defects

def test_a_hub_glyph_whose_bar_sits_on_a_label_moves_off_its_hub():
    """Review (second) M1: the three PWR_1V8 VDD_5V glyphs hang off the
    BIAS row (kind B), so only a share was ever tried and they were left
    colliding with the SYNC label text -- and nothing said so. A kind-B
    glyph may now move its drop: here down from the hub, the one direction
    that clears the label, pointed down as the last resort it is."""
    cv, plan, sheet = _load("1v8_u111_vdd5v")
    hits0 = G.glyph_collisions(cv, plan, sheet)
    assert any(cv.power_ports[i].text == "VDD_5V" for i in hits0)
    report: dict = {}
    assert G.resolve_port_glyph_collisions(cv, plan, report=report) >= 1
    vdd = [p for p in cv.power_ports if p.text == "VDD_5V"]
    assert [(p.x, p.y, p.orientation) for p in vdd] == [(6500, 8200, 3)]
    assert (6500, 8400, 6500, 8800, "VDD_5V") not in _wires(cv)
    assert (6500, 8200, 6500, 8400, "VDD_5V") in _wires(cv)
    assert G.glyph_collisions(cv, plan, sheet) == {}
    assert report["remaining"] == []
    assert ("PWR_1V8", "VDD_5V", 6500, 8200, 3) in report["flipped"]


def test_a_move_that_would_push_text_onto_copper_is_not_taken(monkeypatch):
    """The text gate: designator/value text is re-placed after the pass,
    so every improving candidate is run through the placer on a copy and
    one that leaves annotation overlaps worse is refused (on the real
    PWR_1V8 sheet, U103's VDD_5V: each down position pushed C145's text
    onto a wire). With every candidate refused the glyph stays and is
    reported."""
    cv, plan, sheet = _load("1v8_u111_vdd5v")
    calls = []

    def gate(ctx, wires, ports):
        calls.append(len(wires))
        # Baseline (the sheet as it is) 0; every candidate makes it worse.
        return 0 if wires == ctx.wires() else 1

    monkeypatch.setattr(G, "_annotation_overlaps_after", gate)
    report: dict = {}
    G.resolve_port_glyph_collisions(cv, plan, report=report)
    assert (6500, 8800) in {(p.x, p.y) for p in cv.power_ports
                            if p.text == "VDD_5V"}
    assert any(r[1] == "VDD_5V" for r in report["remaining"])
    assert len(calls) >= 2


def test_the_text_gate_counts_what_the_placer_produces():
    """_annotation_overlaps_after runs the real placer on a COPY: a part
    hemmed in by wires on every side scores > 0, and the canvas's own text
    positions are left untouched."""
    cv = _blank()
    r1 = SymbolInstance(refdes="R1", x=2000, y=3000, rotation=0,
                        symbol=_resistor(), value="10k")
    cv.add_instance(r1)
    plan = SimpleNamespace(nets=[])
    ctx = G._GlyphSheet(cv, plan, "main")
    assert G._annotation_overlaps_after(ctx, [], []) == 0
    cage = [(1500, y, 3500, y, "N") for y in range(2500, 3600, 50)]
    assert G._annotation_overlaps_after(ctx, cage, []) > 0
    assert r1.designator_pos is None


def test_a_riser_through_label_text_is_moved_clear():
    """Review (second) M2: with Q108 mirrored, MCU_V3V3's riser ran through
    the MCU_I2C_SDA and MCU_I2C_SCL texts. Neither the score nor the glyph
    test saw it (the ink touches nothing). Counted now as an own-drop
    defect; the drop leaves its hub to the right and rises clear, bar UP."""
    cv, plan, sheet = _load("usb_q108_mirror_v3v3")
    assert G.glyph_collisions(cv, plan, sheet) == {}
    assert G.resolve_port_glyph_collisions(cv, plan) == 1
    v = [p for p in cv.power_ports if p.text == "MCU_V3V3"]
    assert [(p.x, p.y, p.orientation) for p in v] == [(11000, 2000, None)]
    ctx = G._GlyphSheet(cv, plan, sheet)
    own = [w[:4] for w in ctx.wires() if w[4] == "MCU_V3V3"]
    assert G._own_defects(ctx, "MCU_V3V3", own, ctx.wires()) == 0


def test_padding_only_ranks_it_never_flags():
    """A bar 60 mil from a wire clears the unpadded test, so the pass must
    leave it alone even though it is inside RANK_PAD."""
    cv = _blank()
    cv.add_wires([WireSegment(1000, 600, 1000, 1000, net="VCC"),
                  WireSegment(600, 1140, 1400, 1140, net="OTHER")])
    cv.add_power_ports([PowerPort("VCC", 1000, 1000, "bar")])
    plan = SimpleNamespace(nets=[])
    assert G.glyph_collisions(cv, plan, "main") == {}
    before = cv.to_dict()
    assert G.resolve_port_glyph_collisions(cv, plan) == 0
    assert cv.to_dict() == before


def test_a_new_path_never_crosses_its_own_net():
    """Review L9: a same-net crossing reads as an ambiguous joint (no dot,
    one name), so safe_path refuses it like a touch."""
    cv = _blank()
    ctx = G._GlyphSheet(cv, SimpleNamespace(nets=[]), "main")
    wires = [(1500, 500, 1500, 2500, "VCC")]
    assert not ctx.safe_path([(1000, 1000, 2000, 1000)], "VCC", wires, [])
    assert ctx.safe_path([(1000, 1000, 2000, 1000)], "OTHER", wires, [])


def test_the_report_lists_what_still_collides():
    """Review (second) M1: a glyph the pass cannot fix is reported, so the
    notes no longer imply a clean sheet."""
    cv = _blank()
    # A lone glyph on foreign copper with no wire of its own: no topology
    # to work with, so no candidate, so it must be reported.
    cv.add_instance(SymbolInstance(refdes="R1", x=4000, y=4000, rotation=0,
                                   symbol=_resistor()))
    cv.add_wires([WireSegment(700, 1100, 1300, 1100, net="OTHER")])
    cv.add_power_ports([PowerPort("VCC", 1000, 1000, "bar")])
    report: dict = {}
    G.resolve_port_glyph_collisions(cv, SimpleNamespace(nets=[]), report=report)
    assert report["remaining"] == [("main", "VCC", 1000, 1000, ["wire OTHER"])]


def test_the_pipeline_turns_the_glyph_report_into_notes():
    """What the pass could not fix, what it pointed away from up, and a
    sheet it had to put back all reach the notes: an agent reading only
    "N glyphs moved" assumed a clean sheet (second review M1, L5)."""
    from eda_agent.design import pipeline
    from eda_agent.design.benchmark import SyntheticSymbolExtractor

    def fake(canvas, plan, **kw):
        kw["report"].update(
            errors=[("S1", "RuntimeError: boom")], reverted=["S2"],
            flipped=[("S3", "VDD_5V", 6500, 13400, 3)],
            remaining=[("S4", "GND", 100, 200, ["label"])])
        return 1

    plan = DesignPlan.model_validate(
        json.loads((PLANS_DIR / "blinker555.json").read_text(encoding="utf-8")))
    with mock.patch.object(pipeline, "resolve_port_glyph_collisions", fake):
        res = pipeline.build_best_canvas_from_plan(
            plan, SyntheticSymbolExtractor(plan), n_tries=1)
    texts = [n.text for n in res.notes]
    assert any("failed on sheet 'S1'" in t and "boom" in t for t in texts)
    assert any("reverted its changes on sheet 'S2'" in t for t in texts)
    assert any("VDD_5V@(6500,13400)" in t and "points DOWN on purpose" in t
               and "audit_power_port_orientation" in t for t in texts)
    assert any(t.startswith("1 power port glyph(s) still collide")
               and "GND@(100,200)" in t for t in texts)


def test_build_notes_are_listed_once():
    """Review L7: an accepted polish rebuild's notes were merged with the
    winner's, so every build note appeared twice."""
    from eda_agent.design import pipeline
    from eda_agent.design.benchmark import SyntheticSymbolExtractor

    plan = DesignPlan.model_validate(
        json.loads((PLANS_DIR / "mcu.json").read_text(encoding="utf-8")))
    ref = plan.parts[0].refdes
    # A bad hint value is reported by each of the build's two hint passes
    # and again by every rebuild: the dedupe is what keeps it to one line.
    res = pipeline.build_best_canvas_from_plan(
        plan, SyntheticSymbolExtractor(plan), n_tries=1,
        placement_hints={ref: {"rotation": "sideways"}})
    keys = [(n.severity, n.text) for n in res.notes]
    assert len(keys) == len(set(keys))
    assert sum("is not an integer" in t for _s, t in keys) == 1
    assert pipeline._dedupe_notes(
        [pipeline.PipelineNote("info", "a"), pipeline.PipelineNote("info", "a"),
         pipeline.PipelineNote("warning", "a")]
    ) == [pipeline.PipelineNote("info", "a"), pipeline.PipelineNote("warning", "a")]
