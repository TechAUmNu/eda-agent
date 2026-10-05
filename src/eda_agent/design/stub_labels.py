# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Stub-label draw style for dense parts (``Part.draw_style="stub_labels"``).

A dense IC -- a USB-PD controller with 13 ground pins, an eFuse with five IN
and eight OUT pins, a power MOSFET with 3 S and 4 D pins -- does not read well
routed: the engine fans every same-net pin into a cluster and threads the
signal pins across the sheet. The way such a part is drawn by hand is
self-contained instead:

* every used pin gets a straight stub (``STUB_LEN_MILS``) and a net label on it;
* a run of adjacent pins (100-mil pitch) on ONE supply net shares a short rail
  ``RAIL_INSET_MILS`` out from the pins, and only the run's first pin carries
  the full stub and the single label;
* a run of adjacent ground pins does the same with ONE ground glyph at the end
  of the first pin's stub, pointing outward -- except on an UP-facing side,
  where a ground symbol would point up, so that run carries a label instead;
* a pin no plan net uses gets a No-ERC mark.

The part's nets then reach the rest of the sheet by name. This module only
computes that geometry for one placed instance; ``pipeline`` decides where it
is used (it replaces the pass-1 stubs of the part's pins, is an obstacle for
everything else, and is never culled, trimmed or re-labelled).

Geometry is the generalisation of the hand-drawing script the multiviewer
carrier used for exactly these parts (one 1100-mil stub per pin, label on the
stub reading +x, rails 400 mils out, ground glyph pointing outward), extended
to up/down-facing pins.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional

from eda_agent.design.canvas import NetLabel, NoErc, PowerPort, SymbolInstance
from eda_agent.design.symbols import pin_direction

STYLE = "stub_labels"
STUB_LEN_MILS = 1100
RAIL_INSET_MILS = 400
LABEL_INSET_MILS = 100
PIN_PITCH_MILS = 100
# Text metrics: the same estimate text_placement uses, so the keep-out and
# the annotation placer agree on how wide a label is.
CHAR_W = 55
LABEL_H = 110
NOERC_HALF = 50
# Clearance the PLACEMENT passes keep around the footprint (the routing and
# scoring keep-out stays exact): without it a neighbour's body and label end
# up abutting the stub field, since the shove's gap is sized for bodies.
PLACEMENT_MARGIN_MILS = 200


@dataclass
class StubLabelMacro:
    """Everything one stub-label part puts on the sheet."""

    refdes: str
    sheet: str
    wires: list[tuple[int, int, int, int, str]] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    ports: list[PowerPort] = field(default_factory=list)
    no_ercs: list[NoErc] = field(default_factory=list)
    # Wire ends that touch nothing but are part of the drawing (a right-
    # facing stub runs on past its label). The dangling-wire trim keeps them.
    anchors: set[tuple[int, int]] = field(default_factory=set)
    keepout: tuple[int, int, int, int] = (0, 0, 0, 0)


def stub_label_refdes(plan: Any) -> set[str]:
    """Refdes of every part the plan asks to draw in this style."""
    return {p.refdes for p in plan.parts
            if getattr(p, "draw_style", None) == STYLE}


def apply_draw_style_hints(plan: Any, hints: Optional[dict]) -> tuple[Any, list[str]]:
    """Fold ``placement_hints[refdes]["draw_style"]`` into the plan.

    Returns ``(plan, notes)``. The SAME plan object comes back when no hint
    carries the key or every hinted value already matches, so callers that
    never use the option pay nothing and see an identical plan. A hint value
    of ``None`` / ``"auto"`` / ``""`` clears a style the plan set.
    """
    notes: list[str] = []
    if not hints:
        return plan, notes
    wanted: dict[str, Optional[str]] = {}
    for refdes, h in hints.items():
        if not isinstance(h, dict) or "draw_style" not in h:
            continue
        v = h.get("draw_style")
        v = None if v in (None, "", "auto") else str(v)
        if v not in (None, STYLE):
            notes.append(f"placement_hint {refdes!r}: unknown draw_style {v!r} "
                         f"(known: {STYLE!r}, 'auto'); ignored")
            continue
        wanted[refdes] = v
    if not wanted:
        return plan, notes
    by_ref = {p.refdes: p for p in plan.parts}
    for refdes in [r for r in wanted if r not in by_ref]:
        notes.append(f"placement_hint {refdes!r} sets draw_style but the plan "
                     f"has no such part; ignored")
        del wanted[refdes]
    if all(getattr(by_ref[r], "draw_style", None) == v for r, v in wanted.items()):
        return plan, notes
    new = plan.model_copy(deep=True)
    for p in new.parts:
        if p.refdes in wanted:
            p.draw_style = wanted[p.refdes]
    return new, notes


def pin_nets(plan: Any, refdes: str, symbol: Any) -> dict[str, tuple[str, bool, bool]]:
    """``{pin designator: (net, is_rail, is_ground)}`` for one part.

    Plan pins may name a pin by designator or by name; both resolve to the
    designator the canvas reports, so the macro and the netlist agree.
    """
    from eda_agent.design._wiring import _is_ground_net, _is_power_net

    out: dict[str, tuple[str, bool, bool]] = {}
    for net in plan.nets:
        g = _is_ground_net(net)
        r = g or _is_power_net(net)
        for pr in net.pins:
            if pr.refdes != refdes:
                continue
            pin = symbol.pin_by_id(pr.pin)
            if pin is not None:
                out[pin.designator] = (net.name, r, g)
    return out


def no_erc_exempt(plan: Any, refdes: str, symbol: Any) -> tuple[set[str], list[str]]:
    """``(exempt, problems)``: pins that must NOT get a No-ERC mark, and why.

    A No-ERC mark tells ERC "this pin is deliberately unused". Placing one
    on a pin the plan meant to connect hides exactly the warning that would
    have caught a plan/symbol mismatch, so:

    * every pin a plan PinRef of this part names, by designator OR by name,
      is exempt -- a name shared by several pins resolves to the first one
      only (``SymbolModel.pin_by_id``), and the others must stay visible to
      ERC as unconnected rather than be marked as intentionally so;
    * if any PinRef of this part names no pin at all, the intended pin is
      unknown, so EVERY pin is exempt (the pipeline reports the mismatch as
      a failure).

    ``problems`` lists one human-readable line per ambiguous name; the
    unresolved PinRefs are reported by the pipeline itself.
    """
    exempt: set[str] = set()
    problems: list[str] = []
    unresolved = False
    for net in plan.nets:
        for pr in net.pins:
            if pr.refdes != refdes:
                continue
            hit = [p for p in symbol.pins
                   if p.designator == pr.pin or p.name == pr.pin]
            if not hit:
                unresolved = True
                continue
            exempt |= {p.designator for p in hit}
            by_name = [p for p in hit if p.designator != pr.pin]
            if len(by_name) > 1 and not any(p.designator == pr.pin for p in hit):
                chosen = symbol.pin_by_id(pr.pin)
                problems.append(
                    f"{refdes}: pin name {pr.pin!r} (net {net.name!r}) matches "
                    f"{len(by_name)} pins ({', '.join(p.designator for p in by_name)}); "
                    f"only pin {chosen.designator} is connected -- name the "
                    f"others by designator")
    if unresolved:
        exempt = {p.designator for p in symbol.pins}
    return exempt, problems


def _label(text: str, hx: int, hy: int, ex: int, ey: int, dx: int, dy: int,
           sheet: str) -> NetLabel:
    """A label lying ON the stub from (hx,hy) to (ex,ey), reading +x / +y.

    Horizontal stubs carry orientation-0 text growing right: anchored at the
    stub's far end on a left-facing pin (the text runs back toward the pin)
    and ``LABEL_INSET_MILS`` out from the pin on a right-facing one. Vertical
    stubs do the same with orientation-1 (rotated) text growing up.
    """
    if dy == 0:
        x = ex if dx < 0 else hx + LABEL_INSET_MILS
        return NetLabel(text=text, x=x, y=hy, orientation=0, sheet=sheet,
                        justification=0)
    y = ey if dy < 0 else hy + LABEL_INSET_MILS
    return NetLabel(text=text, x=hx, y=y, orientation=1, sheet=sheet,
                    justification=0)


def _label_box(lb: NetLabel) -> tuple[int, int, int, int]:
    w = CHAR_W * len(lb.text)
    if lb.orientation % 2 == 0:
        return (lb.x, lb.y, lb.x + w, lb.y + LABEL_H)
    return (lb.x - LABEL_H, lb.y, lb.x, lb.y + w)


def _port_box(p: PowerPort) -> tuple[int, int, int, int]:
    """The glyph's DRAWN extent (bars and name), from the same geometry the
    renderer and the glyph collision check use, so the keep-out covers the
    name of a sideways ground glyph as far as it is actually drawn."""
    from eda_agent.design.port_glyphs import glyph_extent

    return glyph_extent(p)


def _union(boxes: Iterable[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    bs = list(boxes)
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def build_stub_label_macro(
    inst: SymbolInstance,
    nets_by_pin: dict[str, tuple[str, bool, bool]],
    no_erc_exempt: Optional[set[str]] = None,
) -> StubLabelMacro:
    """The stub-label drawing of one placed instance.

    ``nets_by_pin`` comes from :func:`pin_nets`. A pin with no net gets a
    No-ERC mark unless it is in ``no_erc_exempt`` (see
    :func:`no_erc_exempt`): such a pin is left bare so ERC reports it.
    Pure geometry over the instance's world pin ends; deterministic (pins
    visited in symbol order, runs in side order).
    """
    from eda_agent.design._wiring import _ground_style

    sheet = inst.sheet
    m = StubLabelMacro(refdes=inst.refdes, sheet=sheet)
    hidden = {p.designator for p in inst.symbol.pins if p.hidden}
    eps = [ep for ep in inst.all_pin_endpoints() if ep.pin_id not in hidden]
    longest = max((len(nets_by_pin[ep.pin_id][0]) for ep in eps
                   if ep.pin_id in nets_by_pin), default=0)
    # One stub length for the whole part, as drawn by hand; it only grows
    # past 1100 for a label too long to sit on the stub.
    need = CHAR_W * longest + LABEL_INSET_MILS
    L = max(STUB_LEN_MILS, -(-need // PIN_PITCH_MILS) * PIN_PITCH_MILS)

    rail_pins: dict[int, list] = {}
    for ep in eps:
        info = nets_by_pin.get(ep.pin_id)
        if info is None:
            if not no_erc_exempt or ep.pin_id not in no_erc_exempt:
                m.no_ercs.append(NoErc(x=ep.x, y=ep.y, sheet=sheet))
            continue
        net, is_rail, _ = info
        if is_rail:
            rail_pins.setdefault(ep.orientation % 4, []).append(ep)
            continue
        dx, dy = pin_direction(ep.orientation)
        ex, ey = ep.x + dx * L, ep.y + dy * L
        m.wires.append((ep.x, ep.y, ex, ey, net))
        m.labels.append(_label(net, ep.x, ep.y, ex, ey, dx, dy, sheet))
        m.anchors.add((ex, ey))

    for orient in sorted(rail_pins):
        dx, dy = pin_direction(orient)
        if dy == 0:      # left/right side: top pin first, as drawn by hand
            side = sorted(rail_pins[orient], key=lambda e: (-e.y, e.x))
        else:            # top/bottom side: left pin first
            side = sorted(rail_pins[orient], key=lambda e: (e.x, -e.y))
        runs: list[list] = []
        for ep in side:
            net = nets_by_pin[ep.pin_id][0]
            if runs:
                prev = runs[-1][-1]
                step = ((prev.y - ep.y) if dy == 0 else (ep.x - prev.x))
                same_line = (prev.x == ep.x) if dy == 0 else (prev.y == ep.y)
                if (nets_by_pin[prev.pin_id][0] == net and same_line
                        and step == PIN_PITCH_MILS):
                    runs[-1].append(ep)
                    continue
            runs.append([ep])
        for run in runs:
            net, _, is_ground = nets_by_pin[run[0].pin_id]
            top = run[0]
            ex, ey = top.x + dx * L, top.y + dy * L
            m.wires.append((top.x, top.y, ex, ey, net))
            # A ground symbol never points UP (the convention the glyph pass
            # and audit_power_port_orientation enforce). An up-facing ground
            # run is named by a label like any supply run instead; the
            # orientation field stays None for the natural (down) direction,
            # which is the PowerPort contract.
            if is_ground and orient != 1:
                m.ports.append(PowerPort(text=net, x=ex, y=ey,
                                         style=_ground_style(net), sheet=sheet,
                                         orientation=None if orient == 3 else orient))
            else:
                m.labels.append(_label(net, top.x, top.y, ex, ey, dx, dy, sheet))
                m.anchors.add((ex, ey))
            if len(run) > 1:
                jx0, jy0 = top.x + dx * RAIL_INSET_MILS, top.y + dy * RAIL_INSET_MILS
                for ep in run[1:]:
                    m.wires.append((ep.x, ep.y, ep.x + dx * RAIL_INSET_MILS,
                                    ep.y + dy * RAIL_INSET_MILS, net))
                last = run[-1]
                m.wires.append((jx0, jy0, last.x + dx * RAIL_INSET_MILS,
                                last.y + dy * RAIL_INSET_MILS, net))

    bb = inst.world_bbox()
    boxes = [(bb.x_min, bb.y_min, bb.x_max, bb.y_max)]
    boxes += [(min(w[0], w[2]), min(w[1], w[3]), max(w[0], w[2]), max(w[1], w[3]))
              for w in m.wires]
    boxes += [_label_box(lb) for lb in m.labels]
    boxes += [_port_box(p) for p in m.ports]
    boxes += [(n.x - NOERC_HALF, n.y - NOERC_HALF, n.x + NOERC_HALF, n.y + NOERC_HALF)
              for n in m.no_ercs]
    m.keepout = _union(boxes)
    return m


def stub_label_half_extents(symbol: Any, nets_by_pin: dict) -> tuple[int, int]:
    """Half extents of the drawn footprint about the BODY CENTRE, native
    rotation -- the frame ``body_half`` / ``motif_half`` use for every part."""
    probe = SymbolInstance(refdes="_probe", symbol=symbol, x=0, y=0, rotation=0)
    x0, y0, x1, y1 = build_stub_label_macro(probe, nets_by_pin).keepout
    bb = symbol.body_bbox
    cx, cy = (bb.x_min + bb.x_max) / 2.0, (bb.y_min + bb.y_max) / 2.0
    return (int(max(cx - x0, x1 - cx)) + PLACEMENT_MARGIN_MILS,
            int(max(cy - y0, y1 - cy)) + PLACEMENT_MARGIN_MILS)


__all__ = [
    "STYLE", "STUB_LEN_MILS", "RAIL_INSET_MILS", "StubLabelMacro",
    "apply_draw_style_hints", "build_stub_label_macro", "no_erc_exempt", "pin_nets",
    "stub_label_half_extents", "stub_label_refdes",
]
