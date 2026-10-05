# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Power-port glyph geometry, its collision test, and the post-selection pass.

The two paths that create glyphs (``_emit_port_cluster`` and the floating-pin
repair, then ``upgrade_repair_ports_to_stubs``) reason about a glyph's HOTSPOT
point only. None of them models where the glyph's bar and name are DRAWN, and
none tests that drawing against wires. Measured on the multiviewer carrier's
power sheets, seven glyphs landed with their bar or name on other copper:

* PWR_IN_BARREL: U102 OUT's PWR_VIN_PROT bar sat 20 mil under the
  PWR_BARREL_VS row, with the VIN_FUSED riser through its name, and its
  800-mil riser made four of the sheet's six crossings;
* PWR_IN_USB: U115 OUT's repair glyph, bar 20 mil under the EN wire and name
  across the OV wire;
* PWR_3V3: a second GND glyph per buck sitting ON the PGND16 wire, bars
  across the EP wire, although that wire already reached another GND glyph;
* PWR_1V8: three SPSP GND drops ending 100 mil short of the AGND/PGND stubs.

``resolve_port_glyph_collisions`` runs ONCE, on the winning canvas, after the
stub upgrade. Running anything that draws wire inside the per-candidate build
would enter ``score_canvas`` and could change which candidate wins on sheets
that have no defect -- the stub-upgrade docstring and the loose-wire trim
both record that exact failure. It visits only glyphs that collide, scores a
small set of alternatives for each with the engine's own weights, and applies
the best only if the sheet's collisions strictly fall and its crossings do
not rise. A sheet with no colliding glyph is not touched at all.

This module must not import ``pipeline`` (the pipeline imports it).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Any, Callable, Iterable, Optional

from eda_agent.design._wiring import (
    _cross_net_meeting_counts,
    _point_on_segment,
    _wire_arms_at,
)
from eda_agent.design.canvas import Junction, PowerPort, SchematicCanvas, WireSegment
from eda_agent.design.quality import (
    _W_CROSSINGS,
    _W_FLOW_REVERSAL,
    _W_LENGTH,
    _W_PORTS,
    _segment_crosses_bbox_interior,
)
from eda_agent.design.text_placement import CHAR_W, LINE_H, label_text_box

logger = logging.getLogger(__name__)

Seg = tuple[int, int, int, int]
Box = tuple[int, int, int, int]

#: Placement grid of every candidate the pass generates.
GRID = 100
#: Bar/stem-to-wire clearance. Under half a grid step, so a bar 20 mil from
#: the next row is flagged and one 120 mil away is not.
INK_CLR = 40
#: Name text metrics above / below its baseline, in mils.
ASCENT = 80
DESCENT = 20
#: Nothing the pass draws may come closer than this to the sheet edge.
SHEET_MARGIN = 200
#: Furthest straight run out along the anchor pin before a turn.
MAX_RUN = 1200
#: Shortest / longest leg after a turn.
MIN_TURN_LEG = 200
MAX_TURN_LEG = 1000
#: Extra clearance used ONLY to RANK candidates that already clear the
#: unpadded test, never to decide which glyphs need fixing: padding the test
#: itself starts "fixing" glyphs nobody flagged. Among clearing positions it
#: prefers one whose bar is not 38 mil from the next name over the shortest
#: wire.
RANK_PAD = 100

#: ``allow_supply_flip`` modes. A ground symbol only ever points down.
#: "down" (the default) lets a supply bar point down as a last resort;
#: "any" also allows sideways; "off" keeps every bar pointing up.
SUPPLY_FLIP_MODES = ("off", "down", "any")

_DIRS = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}


def is_ground_style(style: str) -> bool:
    """A ground-family glyph (gnd_power / gnd_signal / gnd_earth)."""
    s = style.lower()
    return "gnd" in s or "earth" in s


def natural_orientation(style: str) -> int:
    """The direction a glyph of this style points when nothing says otherwise.

    Ground hangs down (3), every supply bar points up (1): the convention
    ``_power_port_orientation`` and the Altium audit both encode.
    """
    return 3 if is_ground_style(style) else 1


def port_orientation(port: Any) -> int:
    """The direction a canvas glyph actually points (its field, else natural)."""
    o = getattr(port, "orientation", None)
    return natural_orientation(port.style) if o is None else o % 4


def glyph_geometry(
    x: int, y: int, text: str, style: str, orientation: Optional[int] = None,
) -> tuple[list[Seg], Box]:
    """``(ink_segments, text_box)`` of a glyph whose hotspot is (x, y).

    Mirrors ``render_svg._svg_power_port_body`` at 0.1 px/mil so the check
    and the preview agree (a unit test pins the two together):

    * bar: stem 80 mil out along the orientation, a bar +-80 across it,
      name baseline 160 mil out;
    * ground: stem 0..60, bars at 60 / 100 / 140 with half-widths
      80 / 50 / 20, name baseline 280 mil out.

    The name box is CHAR_W per character with ASCENT above and DESCENT
    below its baseline. Up / down glyphs centre it on the stem; left /
    right glyphs start it (baseline - 40) mil out, 50 mil either side of
    the hotspot's line.
    """
    gnd = is_ground_style(style)
    o = natural_orientation(style) if orientation is None else orientation % 4
    dx, dy = _DIRS[o]
    px, py = -dy, dx
    ink: list[Seg] = []
    if gnd:
        ink.append((x, y, x + 60 * dx, y + 60 * dy))
        for d, h in ((60, 80), (100, 50), (140, 20)):
            cx, cy = x + d * dx, y + d * dy
            ink.append((cx - h * px, cy - h * py, cx + h * px, cy + h * py))
        base = 280
    else:
        ink.append((x, y, x + 80 * dx, y + 80 * dy))
        cx, cy = x + 80 * dx, y + 80 * dy
        ink.append((cx - 80 * px, cy - 80 * py, cx + 80 * px, cy + 80 * py))
        base = 160
    w = CHAR_W * max(1, len(text))
    if o == 1:
        tb = (x - w // 2, y + base - DESCENT, x + w - w // 2, y + base + ASCENT)
    elif o == 3:
        tb = (x - w // 2, y - base - DESCENT, x + w - w // 2, y - base + ASCENT)
    elif o == 0:
        tb = (x + base - 40, y - 50, x + base - 40 + w, y + 50)
    else:
        tb = (x - base + 40 - w, y - 50, x - base + 40, y + 50)
    return ink, tb


def glyph_extent(port: Any) -> Box:
    """Union box of a canvas glyph's ink and name (the text placer's obstacle)."""
    ink, tb = glyph_geometry(port.x, port.y, port.text, port.style,
                             getattr(port, "orientation", None))
    xs = [v for s in ink for v in (s[0], s[2])] + [tb[0], tb[2]]
    ys = [v for s in ink for v in (s[1], s[3])] + [tb[1], tb[3]]
    return (min(xs), min(ys), max(xs), max(ys))


# ------------------------------------------------------------------ geometry

def _norm(s: Seg) -> Box:
    x1, y1, x2, y2 = s
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))


def _box_hit(a: Box, b: Box, pad: int = 0) -> bool:
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0]
                or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def _seg_box(s: Seg, pad: int) -> Box:
    a = _norm(s)
    return (a[0] - pad, a[1] - pad, a[2] + pad, a[3] + pad)


def _near(a: Box, b: Box, margin: int) -> bool:
    """Inclusive bbox proximity: a cheap, conservative prefilter."""
    return not (a[2] + margin < b[0] or b[2] + margin < a[0]
                or a[3] + margin < b[1] or b[3] + margin < a[1])


def _union(boxes: Iterable[Box]) -> Box:
    bs = list(boxes)
    return (min(b[0] for b in bs), min(b[1] for b in bs),
            max(b[2] for b in bs), max(b[3] for b in bs))


def _seglen(s: Seg) -> int:
    return abs(s[2] - s[0]) + abs(s[3] - s[1])


def _on_seg(px: int, py: int, s: Seg) -> bool:
    return _point_on_segment(px, py, s[0], s[1], s[2], s[3])


def _cross(a: Seg, b: Seg) -> bool:
    """Strict interior crossing (``quality._count_wire_crossings`` semantics)."""
    av, bv = a[0] == a[2], b[0] == b[2]
    if av == bv:
        return False
    v, h = (a, b) if av else (b, a)
    vx, vlo, vhi = v[0], min(v[1], v[3]), max(v[1], v[3])
    hlo, hhi, hy = min(h[0], h[2]), max(h[0], h[2]), h[1]
    return hlo < vx < hhi and vlo < hy < vhi


def _collinear_overlap(a: Seg, b: Seg) -> bool:
    if a[0] == a[2] == b[0] == b[2]:
        return (min(max(a[1], a[3]), max(b[1], b[3]))
                > max(min(a[1], a[3]), min(b[1], b[3])))
    if a[1] == a[3] == b[1] == b[3]:
        return (min(max(a[0], a[2]), max(b[0], b[2]))
                > max(min(a[0], a[2]), min(b[0], b[2])))
    return False


def _orient(dx: int, dy: int) -> int:
    return {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}[(dx, dy)]


def _path_segments(pts: list[tuple[int, int]]) -> list[Seg]:
    return [(a[0], a[1], b[0], b[1]) for a, b in zip(pts, pts[1:])]


def _subtract(ws: Seg, spans: list[Seg]) -> list[Seg]:
    """The parts of axis-aligned ``ws`` not covered by any collinear span."""
    pieces = [ws]
    for sp in spans:
        nxt: list[Seg] = []
        for p in pieces:
            if not _collinear_overlap(p, sp):
                nxt.append(p)
                continue
            if p[0] == p[2]:
                lo, hi = min(p[1], p[3]), max(p[1], p[3])
                slo, shi = min(sp[1], sp[3]), max(sp[1], sp[3])
                if lo < slo:
                    nxt.append((p[0], lo, p[0], slo))
                if shi < hi:
                    nxt.append((p[0], shi, p[0], hi))
            else:
                lo, hi = min(p[0], p[2]), max(p[0], p[2])
                slo, shi = min(sp[0], sp[2]), max(sp[0], sp[2])
                if lo < slo:
                    nxt.append((lo, p[1], slo, p[1]))
                if shi < hi:
                    nxt.append((shi, p[1], hi, p[1]))
        pieces = nxt
    return pieces


# --------------------------------------------------------------- sheet state

class _GlyphSheet:
    """Static obstacles of one sheet plus the collision and topology tests.

    Wires and glyphs are passed in explicitly (as ``(x1, y1, x2, y2, net)``
    tuples and ``(index, port)`` pairs) so a candidate can be scored against
    a hypothetical state without touching the canvas.
    """

    def __init__(self, canvas: SchematicCanvas, plan: Any, sheet: str) -> None:
        self.canvas, self.sheet = canvas, sheet
        so = next(s for s in canvas.sheets if s.name == sheet)
        self.W, self.H = so.width_mils, so.height_mils
        pin_net: dict[tuple[str, str], str] = {}
        for n in plan.nets:
            for pr in n.pins:
                pin_net[(pr.refdes, pr.pin)] = n.name
        self.bodies: list[tuple[str, Box]] = []
        self.pins: list[dict[str, Any]] = []
        for inst in canvas.instances_on(sheet):
            bb = inst.world_bbox()
            self.bodies.append((inst.refdes, (bb.x_min, bb.y_min, bb.x_max, bb.y_max)))
            for ep in inst.all_pin_endpoints():
                dx, dy = _DIRS[ep.orientation % 4]
                line = (ep.x, ep.y, ep.x - ep.length * dx, ep.y - ep.length * dy)
                self.pins.append(dict(
                    ref=inst.refdes, pid=ep.pin_id, x=ep.x, y=ep.y,
                    net=pin_net.get((inst.refdes, ep.pin_id), ""),
                    line=line, box=_norm(line), u=(dx, dy)))
        self.labels: list[Box] = []
        self.label_pts: list[tuple[str, int, int]] = []
        for lab in canvas.labels:
            if lab.sheet != sheet:
                continue
            self.labels.append(label_text_box(lab))
            self.label_pts.append((lab.text, lab.x, lab.y))

    # -------------------------------------------------------------- state
    def wires(self) -> list[tuple[int, int, int, int, str]]:
        return [(w.x1, w.y1, w.x2, w.y2, w.net)
                for w in self.canvas.wires if w.sheet == self.sheet]

    def ports(self) -> list[tuple[int, PowerPort]]:
        return [(i, p) for i, p in enumerate(self.canvas.power_ports)
                if p is not None and p.sheet == self.sheet]

    # --------------------------------------------------------- collisions
    def hits(self, gi: int, x: int, y: int, text: str, style: str,
             o: Optional[int], wires, ports,
             pad: int = 0) -> tuple[int, list[tuple]]:
        """Collision count of one glyph at (x, y, o) against a state.

        One per object: a wire not ending at the hotspot (ANY net -- a
        same-net wire that is not the glyph's own connection still reads as
        a glyph sitting on copper), a body, a foreign pin line, a net label,
        another glyph. ``pad`` grows every clearance; it is only ever used
        to RANK candidates (``RANK_PAD``), never to flag a glyph.
        """
        ink, tb = glyph_geometry(x, y, text, style, o)
        if pad:
            tb = (tb[0] - pad, tb[1] - pad, tb[2] + pad, tb[3] + pad)
        region = _union([_seg_box(s, INK_CLR + pad) for s in ink] + [tb])
        n = 0
        out: list[tuple] = []
        for (x1, y1, x2, y2, net) in wires:
            ws = (x1, y1, x2, y2)
            if not _near(region, _norm(ws), 1):
                continue
            if (x, y) in ((x1, y1), (x2, y2)):
                continue                  # the glyph's own connection
            if (any(_box_hit(_seg_box(s, INK_CLR + pad), _norm(ws))
                    for s in ink)
                    or _box_hit(tb, _seg_box(ws, 0))):
                n += 1
                out.append(("wire", net, ws))
        for ref, bb in self.bodies:
            if not _near(region, bb, 1):
                continue
            if (any(_box_hit(_seg_box(s, pad), bb, -1) for s in ink)
                    or _box_hit(tb, bb, -1)):
                n += 1
                out.append(("body", ref, bb))
        for p in self.pins:
            if p["net"] == text or not _near(region, p["box"], 45 + pad):
                continue
            if any(_box_hit(_seg_box(s, 20 + pad), _seg_box(p["line"], 20), -1)
                   for s in ink):
                n += 1
                out.append(("pin", f"{p['ref']}.{p['pid']}", p["line"]))
        for lb in self.labels:
            if not _near(region, lb, 15 + pad):
                continue
            if (_box_hit(tb, lb)
                    or any(_box_hit(_seg_box(s, 10 + pad), lb) for s in ink)):
                n += 1
                out.append(("label", None, lb))
        mine = [_seg_box(s, 10 + pad) for s in ink] + [tb]
        for j, q in ports:
            if j == gi:
                continue
            qi, qt = glyph_geometry(q.x, q.y, q.text, q.style,
                                    getattr(q, "orientation", None))
            theirs = [_seg_box(s, 10) for s in qi] + [qt]
            if not _near(region, _union(theirs), 15 + pad):
                continue
            if any(_box_hit(a, b) for a in mine for b in theirs):
                n += 1
                out.append(("glyph", q.text, (q.x, q.y)))
        return n, out

    def wire_hits_on_glyph(self, q: PowerPort, segs) -> int:
        """How many of ``segs`` collide with glyph ``q`` (the other side)."""
        ink, tb = glyph_geometry(q.x, q.y, q.text, q.style,
                                 getattr(q, "orientation", None))
        n = 0
        for s in segs:
            if (q.x, q.y) in ((s[0], s[1]), (s[2], s[3])):
                continue
            if (any(_box_hit(_seg_box(i, INK_CLR), _norm(s[:4])) for i in ink)
                    or _box_hit(tb, _seg_box(s[:4], 0))):
                n += 1
        return n

    # ----------------------------------------------------------- topology
    def net_graph(self, net: str, wires, extra_nodes=()) -> dict:
        """Same-net copper as a graph, split at every same-net endpoint, pin
        and glyph hotspot lying on it."""
        segs = [w[:4] for w in wires if w[4] == net]
        pts = {(s[0], s[1]) for s in segs} | {(s[2], s[3]) for s in segs}
        pts |= {(p["x"], p["y"]) for p in self.pins if p["net"] == net}
        pts |= set(extra_nodes)
        adj: dict[tuple[int, int], set] = {}
        for s in segs:
            on = sorted({pt for pt in pts if _on_seg(pt[0], pt[1], s)})
            for a, b in zip(on, on[1:]):
                adj.setdefault(a, set()).add(b)
                adj.setdefault(b, set()).add(a)
        return adj

    def topology(self, gi: int, port: PowerPort, wires, ports):
        """``('A', anchor_pin, path_pts)`` | ``('B', hub, path_pts)`` | None.

        Kind A: the degree-2 chain from the hotspot ends on an own-net pin
        nothing else touches -- a private path the glyph may re-route.
        Kind B: it ends at a hub of three or more arms, or the hotspot sits
        on copper; the glyph owns only its drop from that hub.
        """
        hot = (port.x, port.y)
        net = port.text
        glyph_pts = {(q.x, q.y) for j, q in ports if q.text == net and j != gi}
        adj = self.net_graph(net, wires, extra_nodes=[hot] + list(glyph_pts))
        own_pins = {(p["x"], p["y"]): p for p in self.pins if p["net"] == net}
        deg = len(adj.get(hot, ()))
        if deg == 0:
            if hot in own_pins:
                return ("A", own_pins[hot], [])
            return None
        if deg >= 2:
            drops = [w for w in wires if w[4] == net
                     and hot in ((w[0], w[1]), (w[2], w[3]))]
            if len(drops) != 1:
                return None
            d = drops[0]
            far = (d[2], d[3]) if (d[0], d[1]) == hot else (d[0], d[1])
            sx = (far[0] > hot[0]) - (far[0] < hot[0])
            sy = (far[1] > hot[1]) - (far[1] < hot[1])
            nb = [m for m in adj[hot]
                  if ((m[0] - hot[0]) * sy == 0 and (m[1] - hot[1]) * sx == 0
                      and ((m[0] - hot[0]) * sx + (m[1] - hot[1]) * sy) > 0)]
            if len(nb) != 1:
                return None
            path_pts, prev, cur = [hot, nb[0]], hot, nb[0]
            while (len(adj[cur]) == 2 and cur not in own_pins
                   and cur not in glyph_pts):
                nxt = [m for m in adj[cur] if m != prev][0]
                path_pts.append(nxt)
                prev, cur = cur, nxt
            return ("B", cur, path_pts)
        path_pts = [hot]
        prev, cur = None, hot
        while True:
            nxt = [m for m in adj[cur] if m != prev]
            if len(nxt) != 1:
                break
            prev, cur = cur, nxt[0]
            path_pts.append(cur)
            if cur in own_pins or cur in glyph_pts or len(adj[cur]) != 2:
                break
        end = path_pts[-1]
        if end in own_pins and len(adj[end]) == 1:
            return ("A", own_pins[end], path_pts)
        if len(adj.get(end, ())) >= 3:
            return ("B", end, path_pts)
        return None

    # ------------------------------------------------------------- safety
    def safe_path(self, segs: list[Seg], net: str, wires, ports,
                  allow_same_net_at=()) -> bool:
        """Every new segment: axis-aligned, inside the sheet margin, through
        no foreign pin, meeting no foreign wire (end on it, T into it, or
        collinear overlap), touching own-net copper only at the allowed
        points, and crossing no body interior."""
        for s in segs:
            if s[0] != s[2] and s[1] != s[3]:
                return False
            if not (SHEET_MARGIN <= min(s[0], s[2])
                    and max(s[0], s[2]) <= self.W - SHEET_MARGIN
                    and SHEET_MARGIN <= min(s[1], s[3])
                    and max(s[1], s[3]) <= self.H - SHEET_MARGIN):
                return False
            sb = _norm(s)
            for p in self.pins:
                if not _near(sb, p["box"], 1):
                    continue
                if p["net"] == net and (p["x"], p["y"]) == (s[0], s[1]):
                    continue
                if p["net"] != net:
                    if _on_seg(p["x"], p["y"], s):
                        return False
                    pl = p["line"]
                    if _cross(s, pl) or _collinear_overlap(s, pl):
                        return False
                elif (_on_seg(p["x"], p["y"], s)
                      and (p["x"], p["y"]) not in ((s[0], s[1]), (s[2], s[3]))):
                    return False      # through an own-net pin: an unintended merge
                elif _collinear_overlap(s, p["line"]):
                    return False      # drawn over an own-net pin's line
            for _j, q in ports:
                if q.text != net and _on_seg(q.x, q.y, s):
                    return False
            for (x1, y1, x2, y2, wn) in wires:
                w = (x1, y1, x2, y2)
                if not _near(sb, _norm(w), 1):
                    continue
                touch = (_on_seg(s[0], s[1], w) or _on_seg(s[2], s[3], w)
                         or _on_seg(x1, y1, s) or _on_seg(x2, y2, s)
                         or _collinear_overlap(s, w))
                if not touch:
                    # A plain crossing of the glyph's OWN net reads as an
                    # ambiguous junction (no dot, the same name on both
                    # wires), so it is refused like a touch. Foreign
                    # crossings are counted by _evaluate and may not rise.
                    if wn == net and _cross(s, w):
                        return False
                    continue
                if wn != net:
                    return False
                pts = [pt for pt in ((s[0], s[1]), (s[2], s[3]), (x1, y1), (x2, y2))
                       if _on_seg(pt[0], pt[1], s) and _on_seg(pt[0], pt[1], w)]
                if not all(pt in allow_same_net_at for pt in pts):
                    return False
            for _ref, bb in self.bodies:
                if _segment_crosses_bbox_interior(*s, *bb):
                    return False
        return True

    # ------------------------------------------------------- connectivity
    def pins_reaching_a_name(self, net: str, wires, ports) -> set:
        """Own-net pins with a wire path to a glyph or label of ``net``."""
        targets = {(q.x, q.y) for _j, q in ports if q.text == net}
        targets |= {(x, y) for (t, x, y) in self.label_pts if t == net}
        adj = self.net_graph(net, wires, extra_nodes=list(targets))
        reached: set = set()
        for p in self.pins:
            if p["net"] != net:
                continue
            start = (p["x"], p["y"])
            seen, stack = {start}, [start]
            while stack:
                n = stack.pop()
                if n in targets:
                    reached.add((p["ref"], p["pid"]))
                    break
                for m in adj.get(n, ()):
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
        return reached


def _crossings(segs, net: str, wires) -> int:
    return sum(1 for s in segs for w in wires if w[4] != net and _cross(s, w[:4]))


def _evaluate(ctx: _GlyphSheet, gi: int, port: PowerPort, old_segs, new_segs,
              new_glyph, wires, ports):
    """``(dcollisions, dcrossings, cost)`` of replacing ``old_segs`` + glyph
    by ``new_segs`` + ``new_glyph`` (``None`` = delete the glyph)."""
    net = port.text
    old_set = set(old_segs)
    base_wires = [w for w in wires if w[:4] not in old_set or w[4] != net]
    new_wires = base_wires + [(*s, net) for s in new_segs]
    o_old = getattr(port, "orientation", None)
    c_old, _ = ctx.hits(gi, port.x, port.y, port.text, port.style, o_old,
                        wires, ports)
    if new_glyph is None:
        c_new = 0
    else:
        gx, gy, go = new_glyph
        moved = replace(port, x=gx, y=gy, orientation=go)
        ports_after = [(j, (moved if j == gi else q)) for j, q in ports]
        c_new, _ = ctx.hits(gi, gx, gy, port.text, port.style, go,
                            new_wires, ports_after)
    # Other glyphs: wire hits from the removed and added segments.
    d_other = 0
    for j, q in ports:
        if j == gi:
            continue
        d_other += (ctx.wire_hits_on_glyph(q, [(*s, net) for s in new_segs])
                    - ctx.wire_hits_on_glyph(q, [(*s, net) for s in old_segs]))

    # Glyph-glyph pairs are symmetric: hits() counted this glyph's side,
    # count the partners' side too.
    def gg(x: int, y: int, o: Optional[int]) -> int:
        ink, tb = glyph_geometry(x, y, port.text, port.style, o)
        mine = [_seg_box(s, 10) for s in ink] + [tb]
        n = 0
        for j, q in ports:
            if j == gi:
                continue
            qi, qt = glyph_geometry(q.x, q.y, q.text, q.style,
                                    getattr(q, "orientation", None))
            if any(_box_hit(a, b) for a in mine
                   for b in [_seg_box(s, 10) for s in qi] + [qt]):
                n += 1
        return n

    d_gg = (gg(*new_glyph) if new_glyph else 0) - gg(port.x, port.y, o_old)
    dcoll = (c_new - c_old) + d_other + d_gg
    dcross = (_crossings(new_segs, net, base_wires)
              - _crossings(old_segs, net, base_wires))
    dlen = sum(map(_seglen, new_segs)) - sum(map(_seglen, old_segs))
    dports = 0 if new_glyph else -1
    nat = natural_orientation(port.style)
    flip = 0
    if new_glyph is not None and new_glyph[2] != nat:
        flip = 1
    if o_old is not None and o_old % 4 != nat:
        flip -= 1
    cost = (_W_CROSSINGS * dcross + _W_LENGTH * dlen + _W_PORTS * dports
            + _W_FLOW_REVERSAL * flip)
    return dcoll, dcross, cost


def supply_flip_mode(value: Any) -> str:
    """Normalise ``allow_supply_flip``: ``"off"`` | ``"down"`` | ``"any"``.

    ``True`` keeps its original meaning (any direction) and ``False`` means
    off, so a caller written against the boolean is unchanged; the default
    everywhere is ``"down"``.
    """
    if value is True:
        return "any"
    if value is False or value is None:
        return "off"
    v = str(value).strip().lower()
    if v in ("none", "no", "false"):
        v = "off"
    if v not in SUPPLY_FLIP_MODES:
        raise ValueError(f"allow_supply_flip must be one of {SUPPLY_FLIP_MODES} "
                         f"(or a bool); got {value!r}")
    return v


def _reroute_candidates(port: PowerPort, anchor: dict, flip_mode: Any):
    """Glyph positions reached from the anchor by a straight run, or a run
    then one turn. A ground symbol only ever points DOWN; a supply bar
    points up unless the flip mode lets it turn as a last resort ("down":
    down only; "any": sideways too). The flip is penalised in the score, so
    it wins only when no natural candidate clears as much."""
    qx, qy = anchor["x"], anchor["y"]
    ux, uy = anchor["u"]
    px, py = -uy, ux
    nat = natural_orientation(port.style)
    gnd = is_ground_style(port.style)
    mode = supply_flip_mode(flip_mode)

    def allowed(o: int) -> bool:
        if gnd:
            return o == 3
        if mode == "any":
            return True
        if mode == "down":
            return o in (nat, 3)
        return o == nat

    for k in range(GRID, MAX_RUN + GRID, GRID):
        tx, ty = qx + k * ux, qy + k * uy
        if allowed(_orient(ux, uy)):
            yield ([(qx, qy, tx, ty)], (tx, ty, _orient(ux, uy)))
        for sgn in (1, -1):
            dx, dy = sgn * px, sgn * py
            if not allowed(_orient(dx, dy)):
                continue
            for leg in range(MIN_TURN_LEG, MAX_TURN_LEG + GRID, GRID):
                gx, gy = tx + leg * dx, ty + leg * dy
                yield ([(qx, qy, tx, ty), (tx, ty, gx, gy)],
                       (gx, gy, _orient(dx, dy)))


def _own_defects(ctx: _GlyphSheet, net: str, own: Iterable[Seg],
                 state) -> int:
    """Defects of a glyph's OWN copper (its drop or private path).

    * a plain crossing of another wire of the SAME net -- no junction dot,
      the same name on both sides, so it reads as an ambiguous joint
      (PWR_3V3's AGND spoke across the PGND/EP rows);
    * a run through a net label's TEXT, which makes the name unreadable
      (the MCU_V3V3 riser through MCU_I2C_SDA/SCL on PWR_IN_USB).

    Foreign crossings are not counted here; _evaluate keeps those from
    rising on their own.
    """
    own = list(own)
    mine = set(own)
    n = 0
    for sg in own:
        sb = _norm(sg)
        for w in state:
            if (w[4] == net and w[:4] not in mine and _near(sb, _norm(w[:4]), 1)
                    and _cross(sg, w[:4])):
                n += 1
        for lb in ctx.labels:
            if _box_hit(lb, sb):
                n += 1
    return n


def _chain_wires(net: str, path_pts, wires) -> tuple[list, list[Seg], list[Seg]]:
    """``(chain_wires, chain_segs, kept)`` for a glyph's own drop.

    ``chain_wires`` are the same-net wires lying along the drop; ``kept`` is
    what of them remains outside it (a merged wire that runs on past its
    hub), which must survive any change to the drop.
    """
    chain_segs = _path_segments(list(reversed(path_pts))) if path_pts else []
    chain_wires = [w for w in wires if w[4] == net
                   and any(_collinear_overlap(w[:4], sg) for sg in chain_segs)]
    kept: list[Seg] = []
    for w in chain_wires:
        kept += _subtract(w[:4], chain_segs)
    return chain_wires, chain_segs, kept


def _arm_dirs(pt: tuple[int, int], net: str, state) -> set:
    """Directions in which same-net copper of ``state`` leaves ``pt``."""
    out = set()
    for w in state:
        if w[4] != net:
            continue
        a, b = (w[0], w[1]), (w[2], w[3])
        if not _on_seg(pt[0], pt[1], w[:4]):
            continue
        for end in (a, b):
            if end == pt:
                continue
            sx = (end[0] > pt[0]) - (end[0] < pt[0])
            sy = (end[1] > pt[1]) - (end[1] < pt[1])
            if (sx, sy) in _DIRS.values():
                out.add((sx, sy))
    return out


def _hub_anchors(net: str, hub: tuple[int, int], drop_dir: tuple[int, int],
                 state) -> list[dict]:
    """Starting points for a kind-B glyph's new drop.

    The glyph owns only its drop from the hub, so the drop may leave the
    hub in any direction no other arm uses (the old drop's own direction is
    the "slide" case), or -- "the other side of the cluster" -- from the
    far end of the straight copper run through the hub, continuing away
    from where the old drop was.
    """
    out = []
    used = _arm_dirs(hub, net, state)
    for d in _DIRS.values():
        if d not in used:
            out.append({"x": hub[0], "y": hub[1], "u": d})
    back = (-drop_dir[0], -drop_dir[1])
    if back in used:
        cur = hub
        for _ in range(200):
            nxt = None
            for w in state:
                if w[4] != net or not _on_seg(cur[0], cur[1], w[:4]):
                    continue
                for end in ((w[0], w[1]), (w[2], w[3])):
                    sx = (end[0] > cur[0]) - (end[0] < cur[0])
                    sy = (end[1] > cur[1]) - (end[1] < cur[1])
                    if (sx, sy) == back:
                        dist = abs(end[0] - cur[0]) + abs(end[1] - cur[1])
                        if nxt is None or dist > nxt[0]:
                            nxt = (dist, end)
            if nxt is None:
                break
            cur = nxt[1]
        if cur != hub and back not in _arm_dirs(cur, net, state):
            out.append({"x": cur[0], "y": cur[1], "u": back})
    return out


#: How many of a glyph's best-ranked candidates are run through the text
#: placer before it is left as it is.
_TEXT_GATE_TRIES = 8


def _annotation_overlaps_after(ctx: _GlyphSheet, wires, ports) -> int:
    """Annotation overlaps on ``ctx``'s sheet once designator/value text is
    re-placed against ``wires`` / ``ports`` -- which is what the pipeline
    does after this pass (``place_instance_text``).

    Runs the placer on a private copy of the sheet (instances copied, so
    the real text positions are untouched) and counts every designator or
    value line overlapping a wire, another body, a glyph, a net label or
    another text line.
    """
    import copy

    from eda_agent.design.text_placement import place_instance_text

    canvas = ctx.canvas
    sheet = next(sh for sh in canvas.sheets if sh.name == ctx.sheet)
    sub = SchematicCanvas()
    sub.add_sheet(sheet)
    sub.instances = [copy.copy(i) for i in canvas.instances_on(ctx.sheet)]
    sub.wires = [WireSegment(x1=w[0], y1=w[1], x2=w[2], y2=w[3],
                             sheet=ctx.sheet, net=w[4]) for w in wires]
    sub.labels = list(canvas.labels_on(ctx.sheet))
    sub.power_ports = [q for _j, q in ports if q is not None]
    place_instance_text(sub)
    texts: list[tuple[str, Box]] = []
    for inst in sub.instances:
        for pos, txt in ((inst.designator_pos, inst.refdes),
                         (inst.value_pos, inst.value or "")):
            if pos is not None and txt:
                texts.append((inst.refdes, (pos[0], pos[1],
                                            pos[0] + CHAR_W * len(txt),
                                            pos[1] + LINE_H)))
    obstacles: list[tuple[str, Box]] = []
    obstacles += [("", _norm(w[:4])) for w in wires]
    obstacles += [(i.refdes, (b.x_min, b.y_min, b.x_max, b.y_max))
                  for i in sub.instances for b in (i.world_bbox(),)]
    obstacles += [("", glyph_extent(q)) for q in sub.power_ports]
    obstacles += [("", label_text_box(lb)) for lb in sub.labels]
    n = 0
    for k, (ref, box) in enumerate(texts):
        n += sum(1 for oref, ob in obstacles
                 if oref != ref and _box_hit(box, ob))
        n += sum(1 for _r2, other in texts[k + 1:] if _box_hit(box, other))
    return n


def _share_candidate(ctx: _GlyphSheet, gi: int, port: PowerPort, kind: str,
                     path_pts, wires, ports):
    """Cut the glyph's own drop at the first same-net contact (walking from
    the cluster side) whose copper still reaches ANOTHER glyph of the net,
    and delete the glyph. Never adds wire.

    "Other copper" excludes the glyph's own drop wires: a merged drop wire
    that runs on past its hub is still the glyph's own copper, and cutting
    there would be a zero-length share (a bug the prototype had).
    """
    net = port.text
    hot = (port.x, port.y)
    if kind == "B" and not path_pts:
        return None
    chain = list(reversed(path_pts))     # anchor / hub -> ... -> hotspot
    if len(chain) < 2:
        return None
    chain_segs = _path_segments(chain)
    chain_wires = [w for w in wires if w[4] == net
                   and any(_collinear_overlap(w[:4], s) for s in chain_segs)]
    others = [w for w in wires if w not in chain_wires]
    other_glyphs = [(q.x, q.y) for j, q in ports if q.text == net and j != gi]
    if not other_glyphs:
        return None
    for si, s in enumerate(chain_segs):
        a, b = (s[0], s[1]), (s[2], s[3])
        contacts = []
        for w in others:
            if w[4] != net:
                continue
            ws = w[:4]
            if _cross(s, ws):
                contacts.append((ws[0], s[1]) if s[1] == s[3] else (s[0], ws[1]))
            for pt in ((ws[0], ws[1]), (ws[2], ws[3])):
                if _on_seg(pt[0], pt[1], s) and pt != a:
                    contacts.append(pt)
            if _on_seg(b[0], b[1], ws) and b == hot:
                contacts.append(b)
        contacts = sorted(set(contacts),
                          key=lambda pt: abs(pt[0] - a[0]) + abs(pt[1] - a[1]))
        for cut in contacts:
            if cut == a:
                continue
            if cut == hot and not any(w[4] == net and _on_seg(hot[0], hot[1], w[:4])
                                      for w in others):
                continue
            cut_span = [(cut[0], cut[1], b[0], b[1])] + chain_segs[si + 1:]
            removed: list[Seg] = []
            kept: list[Seg] = []
            for w in chain_wires:
                removed.append(w[:4])
                kept += _subtract(w[:4], cut_span)
            new_state = ([w for w in wires if w not in chain_wires]
                         + [(*p, net) for p in kept])
            if any(w[4] != net and _on_seg(cut[0], cut[1], w[:4]) for w in wires):
                continue
            if any(p["net"] != net and (p["x"], p["y"]) == cut for p in ctx.pins):
                continue
            adj = ctx.net_graph(net, new_state, extra_nodes=other_glyphs + [cut])
            seen, stack, ok = {cut}, [cut], False
            while stack:
                n = stack.pop()
                if n in other_glyphs:
                    ok = True
                    break
                for m in adj.get(n, ()):
                    if m not in seen:
                        seen.add(m)
                        stack.append(m)
            if ok:
                return removed, kept, cut
    return None


def _apply(canvas: SchematicCanvas, sheet: str, gi: int, port: PowerPort,
           removed, added, glyph) -> None:
    rm = set(removed)
    touched: set = set()
    keep_w = []
    for w in canvas.wires:
        if (w.sheet == sheet and w.net == port.text
                and (w.x1, w.y1, w.x2, w.y2) in rm):
            touched |= {(w.x1, w.y1), (w.x2, w.y2)}
            continue
        keep_w.append(w)
    canvas.wires[:] = keep_w
    canvas.add_wires([WireSegment(x1=s[0], y1=s[1], x2=s[2], y2=s[3],
                                  sheet=sheet, net=port.text) for s in added])
    for s in added:
        touched |= {(s[0], s[1]), (s[2], s[3])}
    touched.add((port.x, port.y))
    if glyph is None:
        canvas.power_ports[gi] = None  # type: ignore[call-overload]
    else:
        gx, gy, go = glyph
        canvas.power_ports[gi] = replace(
            port, x=gx, y=gy,
            orientation=None if go == natural_orientation(port.style) else go)
    # Junctions at the touched points, by the same arms rule _wire_sheet uses.
    allw = [(w.x1, w.y1, w.x2, w.y2, w.net) for w in canvas.wires
            if w.sheet == sheet]
    canvas.junctions[:] = [j for j in canvas.junctions
                           if not (j.sheet == sheet and (j.x, j.y) in touched)]
    for (tx, ty) in sorted(touched):
        nets_here = {w[4] for w in allw
                     if _on_seg(tx, ty, w[:4])}
        if any(_wire_arms_at(allw, tx, ty, n) >= 3 for n in nets_here):
            canvas.junctions.append(Junction(x=tx, y=ty, sheet=sheet))


def glyph_collisions(canvas: SchematicCanvas, plan: Any, sheet: str) -> dict[int, list[tuple]]:
    """``{power_port_index: hits}`` for every colliding glyph on ``sheet``."""
    ctx = _GlyphSheet(canvas, plan, sheet)
    wires, ports = ctx.wires(), ctx.ports()
    out: dict[int, list[tuple]] = {}
    for gi, p in ports:
        n, why = ctx.hits(gi, p.x, p.y, p.text, p.style,
                          getattr(p, "orientation", None), wires, ports)
        if n:
            out[gi] = why
    return out


def _resolve_sheet(canvas: SchematicCanvas, plan: Any, sheet: str,
                   skip_nets: set, flip_mode: Any,
                   log: Optional[Callable[[str], None]],
                   flipped: Optional[list] = None) -> tuple[int, set]:
    """Resolve one sheet; returns (glyphs changed, nets they belong to).

    A glyph is visited when its bar or name collides (``hits``) OR its own
    drop crosses same-net copper or runs through label text
    (``_own_defects``). Candidates are ranked on the change in that total,
    then on clearance with ``RANK_PAD`` (ranking only), then on the
    engine's weights; one is applied only if the total strictly falls, the
    sheet's collisions do not rise and its crossings do not rise, and the
    first of the best ``_TEXT_GATE_TRIES`` whose re-placed annotation text
    overlaps no more objects than before (``_annotation_overlaps_after``)
    is the one taken.
    """
    ctx = _GlyphSheet(canvas, plan, sheet)
    changed = 0
    touched_nets: set = set()
    # A stub-label part's drawing (stub_labels.py) is the style the plan
    # asked for: its glyphs and copper are never moved, re-aimed or cut.
    macro_ports: set = set()
    macro_segs: list[Seg] = []
    for inst in canvas.instances_on(sheet):
        macro = getattr(inst, "stub_macro", None)
        if macro is not None:
            macro_ports |= {(q.text, q.x, q.y) for q in macro.ports}
            macro_segs += [tuple(w[:4]) for w in macro.wires]

    def _cuts_macro(removed) -> bool:
        return any(r == m or _collinear_overlap(r, m)
                   for r in removed for m in macro_segs)

    for gi in [i for i, _p in ctx.ports()]:
        port = canvas.power_ports[gi]
        if port is None or port.text in skip_nets:
            continue
        if (port.text, port.x, port.y) in macro_ports:
            continue
        net = port.text
        wires = ctx.wires()
        ports = ctx.ports()
        c_old, _why = ctx.hits(gi, port.x, port.y, port.text, port.style,
                               getattr(port, "orientation", None), wires, ports)
        topo = ctx.topology(gi, port, wires, ports)
        own_old = 0
        if topo is not None:
            own_old = _own_defects(
                ctx, net, _path_segments(list(reversed(topo[2]))), wires)
        if c_old == 0 and own_old == 0:
            continue                      # clean: never touched
        if topo is None:
            if log:
                log(f"skip {net}@({port.x},{port.y}): unsupported topology")
            continue
        kind, root, path_pts = topo
        best = None   # (key, description, (removed, added, glyph))

        cands: list = []   # (key, description, change, after_wires, after_ports)

        def consider(removed, added, glyph, new_own, label) -> None:
            dcoll, dcross, cost = _evaluate(ctx, gi, port, removed, added,
                                            glyph, wires, ports)
            if dcross > 0 or dcoll > 0:
                return
            rm = set(removed)
            after = [w for w in wires if not (w[4] == net and w[:4] in rm)]
            after += [(*sg, net) for sg in added]
            down = (_own_defects(ctx, net, new_own, after)
                    - _own_defects(ctx, net, removed, wires))
            total = dcoll + down
            if total >= 0:
                return
            pad = 0
            ports_after = [(j, q) for j, q in ports if j != gi]
            if glyph is not None:
                gx, gy, go = glyph
                stored = None if go == natural_orientation(port.style) else go
                moved = replace(port, x=gx, y=gy, orientation=stored)
                ports_after.append((gi, moved))
                pad, _ = ctx.hits(gi, gx, gy, port.text, port.style, go, after,
                                  [(j, (moved if j == gi else q))
                                   for j, q in ports], pad=RANK_PAD)
                # The new drop itself: a riser that clears a label's text
                # by 40 mil reads as touching it (the label-width estimate
                # is approximate), so a run within half the pad ranks lower.
                half = RANK_PAD // 2
                pad += sum(1 for sg in new_own for lb in ctx.labels
                           if _box_hit((lb[0] - half, lb[1] - half,
                                        lb[2] + half, lb[3] + half), _norm(sg)))
            cands.append(((total, pad, cost), label,
                          (removed, added, glyph), after, ports_after))

        shared = _share_candidate(ctx, gi, port, kind, path_pts, wires, ports)
        if shared is not None:
            removed, added, cut = shared
            if not _cuts_macro(removed):
                consider(removed, added, None, added, f"share@{cut}")
        if kind == "A":
            old_segs = _path_segments(list(reversed(path_pts))) if path_pts else []
            old_wires = [w[:4] for w in wires if w[4] == net
                         and any(_collinear_overlap(w[:4], sg) for sg in old_segs)]
            if not _cuts_macro(old_wires):
                anchor = root
                base = [w for w in wires
                        if not (w[4] == net and w[:4] in old_wires)]
                others = [(j, q) for j, q in ports if j != gi]
                for segs, glyph in _reroute_candidates(port, anchor, flip_mode):
                    if not ctx.safe_path(
                            segs, net, base, others,
                            allow_same_net_at=((anchor["x"], anchor["y"]),)):
                        continue
                    consider(old_wires, segs, glyph, segs, f"move -> {glyph}")
        elif kind == "B" and len(path_pts) >= 2:
            chain_wires, chain_segs, kept = _chain_wires(net, path_pts, wires)
            removed_b = [w[:4] for w in chain_wires]
            if chain_wires and not _cuts_macro(removed_b):
                hub = path_pts[-1]
                nb = path_pts[-2]
                drop_dir = ((nb[0] > hub[0]) - (nb[0] < hub[0]),
                            (nb[1] > hub[1]) - (nb[1] < hub[1]))
                base = ([w for w in wires if w not in chain_wires]
                        + [(*k, net) for k in kept])
                others = [(j, q) for j, q in ports if j != gi]
                for anchor in _hub_anchors(net, hub, drop_dir, base):
                    start = (anchor["x"], anchor["y"])
                    for segs, glyph in _reroute_candidates(port, anchor,
                                                           flip_mode):
                        if not ctx.safe_path(segs, net, base, others,
                                             allow_same_net_at=(start,)):
                            continue
                        consider(removed_b, kept + segs, glyph, kept + segs,
                                 f"move from hub {start} -> {glyph}")
        # THE CONSEQUENCE, NOT A PROXY. Designator/value text is re-placed
        # after this pass, so a glyph parked where text sat moves the text,
        # and on a crowded spot the placer's next-best side can be across a
        # wire (PWR_1V8: a down-pointing VDD_5V took C145's spot and its
        # text landed on C145's own wire). So the best few candidates are
        # run through the placer on a copy of the sheet, and the first that
        # leaves annotation overlaps no worse than doing nothing wins.
        cands.sort(key=lambda c: c[0])
        if cands:
            text0 = _annotation_overlaps_after(ctx, wires, ports)
            for cand in cands[:_TEXT_GATE_TRIES]:
                if _annotation_overlaps_after(ctx, cand[3], cand[4]) <= text0:
                    best = cand
                    break
        if best is None:
            if log:
                why = ("no improving candidate" if not cands else
                       f"{len(cands)} improving candidate(s), each would push "
                       f"annotation text onto other objects")
                log(f"keep {net}@({port.x},{port.y}) [{kind}] "
                    f"collisions={c_old} own={own_old}: {why}")
            continue
        if log:
            log(f"fix {net}@({port.x},{port.y}) [{kind}] "
                f"collisions={c_old} own={own_old}: {best[1]}")
        removed, added, glyph = best[2]
        _apply(canvas, sheet, gi, port, removed, added, glyph)
        if (flipped is not None and glyph is not None
                and not is_ground_style(port.style)
                and glyph[2] != natural_orientation(port.style)):
            flipped.append((sheet, net, glyph[0], glyph[1], glyph[2]))
        touched_nets.add(net)
        changed += 1
    return changed, touched_nets


def resolve_port_glyph_collisions(
    canvas: SchematicCanvas,
    plan: Any,
    *,
    port_hints: Optional[dict[str, Any]] = None,
    allow_supply_flip: Any = "down",
    log: Optional[Callable[[str], None]] = None,
    report: Optional[dict] = None,
) -> int:
    """Move, re-aim or share every glyph whose bar or name sits on other
    copper, or whose own drop crosses its own net or runs through a label.

    Runs on the FINISHED canvas, after selection and after
    ``upgrade_repair_ports_to_stubs`` (it repairs what that pass produced).
    For each such glyph three kinds of candidate are scored:

    * re-route its private wire (only when the glyph hangs off one pin that
      nothing else touches): out along the pin 100..1200 mil, then
      optionally turn 200..1000 mil, glyph facing the last leg;
    * move its drop (when the glyph hangs off a hub of three or more arms):
      from the hub in any free direction, or from the far end of the
      straight run through the hub, the same runs and turns;
    * share: cut its own drop where it first meets same-net copper that
      still leads to another glyph of the net, and delete it.

    Ranked first on the change in the glyph's defects (collisions plus
    own-drop defects), then on clearance padded by ``RANK_PAD``, then on
    the engine's own weights (crossings, wire length, ports) plus the
    flow-reversal weight for a glyph not facing its natural way. A
    candidate is applied only if the defects strictly fall, collisions and
    crossings do not rise, and the designator/value text the pipeline then
    re-places would overlap no more objects than before (checked by running
    the text placer on a copy). Glyphs of nets with a ``port_hint`` are
    never touched. ``allow_supply_flip``: "down" (default) lets a supply bar point
    down as a last resort, "any" sideways too, "off" never (True/False are
    accepted as "any"/"off").

    Belt and braces: if a sheet ends up with a new cross-net wire meeting,
    or a power pin that reached its glyph no longer does, that sheet is
    put back exactly as it was. A sheet whose pass RAISES is put back too
    and the others still run. Returns the number of glyphs changed.

    ``report``, when given, is filled with ``errors`` [(sheet, message)],
    ``reverted`` [sheet], ``flipped`` [(sheet, net, x, y, orientation)] for
    supply bars left pointing away from up, and ``remaining``
    [(sheet, net, x, y, [hit kinds])] for glyphs that still collide after
    the pass, so the caller can say so instead of implying a clean sheet.
    """
    mode = supply_flip_mode(allow_supply_flip)
    skip = set(port_hints or {})
    rep: dict = report if report is not None else {}
    for key in ("errors", "reverted", "flipped", "remaining"):
        rep.setdefault(key, [])
    total = 0
    for sh in canvas.sheets:
        if not canvas.instances_on(sh.name):
            continue
        snap_wires = list(canvas.wires)
        snap_ports = list(canvas.power_ports)
        snap_junctions = list(canvas.junctions)

        def _restore() -> None:
            canvas.wires[:] = snap_wires
            canvas.power_ports[:] = snap_ports
            canvas.junctions[:] = snap_junctions

        flipped_here: list = []
        try:
            n, nets = _resolve_sheet(canvas, plan, sh.name, skip, mode, log,
                                     flipped_here)
        except Exception as exc:          # noqa: BLE001 - one sheet only
            # Never leave a half-applied sheet behind, and never let one
            # sheet's failure leave the sheets after it unvisited.
            _restore()
            rep["errors"].append((sh.name, f"{type(exc).__name__}: {exc}"))
            logger.warning("glyph pass failed on sheet %r and left it as it "
                           "was: %s", sh.name, exc)
            continue
        canvas.power_ports[:] = [p for p in canvas.power_ports if p is not None]
        if not n:
            continue
        ctx = _GlyphSheet(canvas, plan, sh.name)   # instances/labels unchanged
        w0 = [(w.x1, w.y1, w.x2, w.y2, w.net) for w in snap_wires
              if w.sheet == sh.name]
        p0 = [(i, q) for i, q in enumerate(snap_ports) if q.sheet == sh.name]
        w1, p1 = ctx.wires(), ctx.ports()
        meet0 = _cross_net_meeting_counts(w0)
        meet1 = _cross_net_meeting_counts(w1)
        worse = any(meet1.get(k, 0) > meet0.get(k, 0) for k in meet1)
        if not worse:
            for net in sorted(nets):
                before = ctx.pins_reaching_a_name(net, w0, p0)
                after = ctx.pins_reaching_a_name(net, w1, p1)
                if before - after:
                    worse = True
                    break
        if worse:
            _restore()
            rep["reverted"].append(sh.name)
            logger.warning("glyph pass reverted on sheet %r: it would have "
                           "added a cross-net meeting or cut a power pin "
                           "off its glyph", sh.name)
            continue
        rep["flipped"] += flipped_here
        total += n
    for sh in canvas.sheets:
        if not canvas.instances_on(sh.name):
            continue
        try:
            left = glyph_collisions(canvas, plan, sh.name)
        except Exception:                 # noqa: BLE001 - report only
            continue
        for gi, why in sorted(left.items()):
            q = canvas.power_ports[gi]
            rep["remaining"].append(
                (sh.name, q.text, q.x, q.y,
                 sorted({f"{h[0]} {h[1]}" if h[1] else h[0] for h in why})))
    return total
