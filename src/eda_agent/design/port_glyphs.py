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
from eda_agent.design.text_placement import CHAR_W, LINE_H

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
            w = CHAR_W * len(lab.text)
            self.labels.append(
                (lab.x - w, lab.y, lab.x, lab.y + LINE_H)
                if getattr(lab, "justification", 0) == 2
                else (lab.x, lab.y, lab.x + w, lab.y + LINE_H))
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
             o: Optional[int], wires, ports) -> tuple[int, list[tuple]]:
        """Collision count of one glyph at (x, y, o) against a state.

        One per object: a wire not ending at the hotspot (ANY net -- a
        same-net wire that is not the glyph's own connection still reads as
        a glyph sitting on copper), a body, a foreign pin line, a net label,
        another glyph.
        """
        ink, tb = glyph_geometry(x, y, text, style, o)
        region = _union([_seg_box(s, INK_CLR) for s in ink] + [tb])
        n = 0
        out: list[tuple] = []
        for (x1, y1, x2, y2, net) in wires:
            ws = (x1, y1, x2, y2)
            if not _near(region, _norm(ws), 1):
                continue
            if (x, y) in ((x1, y1), (x2, y2)):
                continue                  # the glyph's own connection
            if (any(_box_hit(_seg_box(s, INK_CLR), _norm(ws)) for s in ink)
                    or _box_hit(tb, _seg_box(ws, 0))):
                n += 1
                out.append(("wire", net, ws))
        for ref, bb in self.bodies:
            if not _near(region, bb, 1):
                continue
            if (any(_box_hit(_seg_box(s, 0), bb, -1) for s in ink)
                    or _box_hit(tb, bb, -1)):
                n += 1
                out.append(("body", ref, bb))
        for p in self.pins:
            if p["net"] == text or not _near(region, p["box"], 45):
                continue
            if any(_box_hit(_seg_box(s, 20), _seg_box(p["line"], 20), -1)
                   for s in ink):
                n += 1
                out.append(("pin", f"{p['ref']}.{p['pid']}", p["line"]))
        for lb in self.labels:
            if not _near(region, lb, 15):
                continue
            if _box_hit(tb, lb) or any(_box_hit(_seg_box(s, 10), lb) for s in ink):
                n += 1
                out.append(("label", None, lb))
        mine = [_seg_box(s, 10) for s in ink] + [tb]
        for j, q in ports:
            if j == gi:
                continue
            qi, qt = glyph_geometry(q.x, q.y, q.text, q.style,
                                    getattr(q, "orientation", None))
            theirs = [_seg_box(s, 10) for s in qi] + [qt]
            if not _near(region, _union(theirs), 15):
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


def _reroute_candidates(port: PowerPort, anchor: dict, allow_supply_flip: bool):
    """Glyph positions reached from the anchor pin by a straight run, or a
    run then one turn. A ground symbol only ever points DOWN; a supply bar
    points up unless ``allow_supply_flip`` lets it turn as a last resort
    (the flip is penalised in the score, so it wins only when no natural
    candidate clears as many collisions)."""
    qx, qy = anchor["x"], anchor["y"]
    ux, uy = anchor["u"]
    px, py = -uy, ux
    nat = natural_orientation(port.style)
    gnd = is_ground_style(port.style)

    def allowed(o: int) -> bool:
        if gnd:
            return o == 3
        return allow_supply_flip or o == nat

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
                   skip_nets: set, allow_supply_flip: bool,
                   log: Optional[Callable[[str], None]]) -> tuple[int, set]:
    """Resolve one sheet; returns (glyphs changed, nets they belong to)."""
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
        wires = ctx.wires()
        ports = ctx.ports()
        c_old, _why = ctx.hits(gi, port.x, port.y, port.text, port.style,
                               getattr(port, "orientation", None), wires, ports)
        if c_old == 0:
            continue                      # collision-free: never touched
        topo = ctx.topology(gi, port, wires, ports)
        if topo is None:
            if log:
                log(f"skip {port.text}@({port.x},{port.y}): unsupported topology")
            continue
        kind, root, path_pts = topo
        best = None   # (key, description, (removed, added, glyph))
        shared = _share_candidate(ctx, gi, port, kind, path_pts, wires, ports)
        if shared is not None:
            removed, added, cut = shared
            dcoll, dcross, cost = _evaluate(ctx, gi, port, removed, added,
                                            None, wires, ports)
            if dcross <= 0 and dcoll < 0 and not _cuts_macro(removed):
                best = ((dcoll, cost), f"share@{cut}", (removed, added, None))
        old_wires: list[Seg] = []
        if kind == "A":
            old_segs = _path_segments(list(reversed(path_pts))) if path_pts else []
            old_wires = [w[:4] for w in wires if w[4] == port.text
                         and any(_collinear_overlap(w[:4], s) for s in old_segs)]
        if kind == "A" and not _cuts_macro(old_wires):
            anchor = root
            base = [w for w in wires
                    if not (w[4] == port.text and w[:4] in old_wires)]
            others = [(j, q) for j, q in ports if j != gi]
            for segs, glyph in _reroute_candidates(port, anchor, allow_supply_flip):
                if not ctx.safe_path(segs, port.text, base, others,
                                     allow_same_net_at=((anchor["x"], anchor["y"]),)):
                    continue
                dcoll, dcross, cost = _evaluate(ctx, gi, port, old_wires, segs,
                                                glyph, wires, ports)
                if dcross > 0 or dcoll >= 0:
                    continue
                key = (dcoll, cost)
                if best is None or key < best[0]:
                    best = (key, f"move -> {glyph}", (old_wires, segs, glyph))
        if best is None:
            if log:
                log(f"keep {port.text}@({port.x},{port.y}) [{kind}] "
                    f"collisions={c_old}: no improving candidate")
            continue
        if log:
            log(f"fix {port.text}@({port.x},{port.y}) [{kind}] "
                f"collisions={c_old}: {best[1]}")
        removed, added, glyph = best[2]
        _apply(canvas, sheet, gi, port, removed, added, glyph)
        touched_nets.add(port.text)
        changed += 1
    return changed, touched_nets


def resolve_port_glyph_collisions(
    canvas: SchematicCanvas,
    plan: Any,
    *,
    port_hints: Optional[dict[str, Any]] = None,
    allow_supply_flip: bool = True,
    log: Optional[Callable[[str], None]] = None,
) -> int:
    """Move, re-aim or share every glyph whose bar or name sits on other copper.

    Runs on the FINISHED canvas, after selection and after
    ``upgrade_repair_ports_to_stubs`` (it repairs what that pass produced).
    For each colliding glyph two kinds of candidate are scored:

    * re-route its private wire (only when the glyph hangs off one pin that
      nothing else touches): out along the pin 100..1200 mil, then
      optionally turn 200..1000 mil, glyph facing the last leg;
    * share: cut its own drop where it first meets same-net copper that
      still leads to another glyph of the net, and delete it.

    Ranked first on the change in the sheet's total glyph collisions, then
    on the engine's own weights (crossings, wire length, ports) plus the
    flow-reversal weight for a glyph not facing its natural way. A
    candidate is applied only if collisions strictly fall and crossings do
    not rise. Glyphs of nets with a ``port_hint`` are never touched.

    Belt and braces: if a sheet ends up with a new cross-net wire meeting,
    or a power pin that reached its glyph no longer does, that sheet is
    put back exactly as it was. Returns the number of glyphs changed.
    """
    skip = set(port_hints or {})
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

        try:
            n, nets = _resolve_sheet(canvas, plan, sh.name, skip,
                                     allow_supply_flip, log)
        except Exception:
            _restore()          # never leave a half-applied sheet behind
            raise
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
            logger.warning("glyph pass reverted on sheet %r: it would have "
                           "added a cross-net meeting or cut a power pin "
                           "off its glyph", sh.name)
            continue
        total += n
    return total
