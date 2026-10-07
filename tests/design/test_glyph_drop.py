"""A power-port glyph must never land on another net's copper.

When it does, the cross-net cull sees a meeting, and a RAIL that loses the
cull has ALL of its sheet wiring dropped (pipeline._wire_sheet._try_cull:
rails take the all-segments drop), leaving one repair glyph per pin. The
meeting is symmetric and ties break alphabetically, so the net that was run
over loses as often as the net that ran over it. Measured on the
multiviewer carrier: J802/J807 (CHASSIS dropped), AUDIO (AUD_LDO_3V3
dropped), PWR_IN_USB (PWR_VIN_PROT dropped), AUDIO_PWR (GND dropped), in
every best-of build. Every test here fails without
_cluster_meets_foreign / _relocate_port_cluster / _revalidate_port_clusters.
"""
from __future__ import annotations

from eda_agent.design._wiring import _cross_net_meeting_counts, _point_on_segment
from eda_agent.design.canvas import SchematicCanvas, Sheet as CSheet
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin

_LIB = "/fake/lib.SchLib"


def test_solo_drop_does_not_land_on_a_foreign_stub():
    """Unit: the single-pin drop's clearance test must see stub SEGMENTS,
    not only stub END boxes. GND pin stub ends at (500,400); the 400-mil drop
    lands at (500,0), mid-span on a CHASSIS stub (200,0)->(600,0)."""
    from eda_agent.design.pipeline import _emit_port_cluster

    canvas = SchematicCanvas(sheets=[CSheet(name="main", size="A4")])
    gnd = Net(name="GND", is_ground=True,
              pins=[PinRef(refdes="J1", pin="1"), PinRef(refdes="R1", pin="2")])
    segs = [(200, 3400, 500, 3400, "GND"),        # GND pin stub
            (200, 3000, 900, 3000, "CHASSIS")]    # longer (staggered) foreign stub
    obstacles = [(850, 2950, 950, 3050)]          # what _wire_sheet passes: the END box only
    _emit_port_cluster(
        canvas=canvas, sheet_name="main", net=gnd,
        net_actions=[(PinRef(refdes="J1", pin="1"), (500, 3400), 0)],
        sheet_wire_segments=segs, routing_obstacles=obstacles,
        other_net_pin_points=[(200, 3000)],
        sheet_width_mils=11690, sheet_height_mils=8270)
    assert _cross_net_meeting_counts(segs) == {}, segs
    for p in canvas.power_ports:
        assert not any(_point_on_segment(p.x, p.y, *s[:4])
                       for s in segs if s[4] != p.text), (p, segs)


def test_two_rails_never_share_a_glyph_point():
    """Unit (the AUDIO_PWR shape): a GND pin 800 mil above a VDD_5V pin in the
    same column. GND drops 400 down, VDD_5V rises 400 up, and both glyphs land
    on (x, y-400) == (x, y'+400): an END-END meeting, and GND was culled
    sheet-wide on AUDIO_PWR for it."""
    from eda_agent.design.pipeline import _emit_port_cluster

    canvas = SchematicCanvas(sheets=[CSheet(name="main", size="A4")])
    segs: list = []
    for name, kw, pt in (("VDD_5V", {"is_power": True}, (7200, 8100)),
                         ("GND", {"is_ground": True}, (7200, 8900))):
        net = Net(name=name, pins=[PinRef(refdes="U1", pin="1"),
                                   PinRef(refdes="U2", pin="1")], **kw)
        _emit_port_cluster(
            canvas=canvas, sheet_name="main", net=net,
            net_actions=[(PinRef(refdes="U1", pin="1"), pt, 0)],
            sheet_wire_segments=segs, routing_obstacles=[],
            other_net_pin_points=[], sheet_width_mils=16540,
            sheet_height_mils=11690)
    assert _cross_net_meeting_counts(segs) == {}, segs
    pts = [(p.x, p.y) for p in canvas.power_ports]
    assert len(set(pts)) == len(pts), canvas.power_ports


def _jack() -> SymbolModel:
    """Right-side pins: SLEEVE (1) 400 above shield SH4 (2), shields SH3..SH1
    (3..5) below it -- the JA23331-HA6Q-4F shape on the carrier's J802/J807."""
    def pin(d, y):
        return SymbolPin(designator=d, name=d, x=300, y=y, orientation=0,
                         length=100, electrical_type="passive")
    return SymbolModel(lib_path=_LIB, lib_ref="JACK",
                       pins=(pin("1", 400), pin("2", 0), pin("3", -100), pin("4", -200), pin("5", -300)),
                       body_bbox=SymbolBBox(x_min=-300, y_min=-300, x_max=300, y_max=500))


def _passive() -> SymbolModel:
    return SymbolModel(lib_path=_LIB, lib_ref="RES", pins=(
        SymbolPin(designator="1", name="1", x=-100, y=0, orientation=2,
                  length=100, electrical_type="passive"),
        SymbolPin(designator="2", name="2", x=100, y=0, orientation=0,
                  length=100, electrical_type="passive")),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30))


def test_a_glyph_collision_does_not_drop_a_rails_wiring(monkeypatch):
    """End to end (D1): the GND port of the sleeve must not make the engine
    cull CHASSIS. CHASSIS's two shield pins must stay wired to each other."""
    from eda_agent.design import pipeline
    from eda_agent.design.pipeline import build_best_canvas_from_plan

    syms = {(_LIB, "JACK"): _jack(), (_LIB, "RES"): _passive()}

    class Ext:
        def extract_one(self, lp, lr):
            return syms.get((lp, lr))

        def extract_many(self, refs):
            return {k: syms[k] for k in refs if k in syms}

    culled = []
    real = pipeline._pick_cull_candidate

    def spy(offenders, try_cull):
        r = real(offenders, try_cull)
        culled.append(r[0])
        return r
    monkeypatch.setattr(pipeline, "_pick_cull_candidate", spy)

    plan = DesignPlan(
        spec="x", summary="x", sheets=[Sheet(name="main", size="A4")],
        parts=[Part(refdes="J1", lib_ref="JACK", lib_path=_LIB, status="existing", sheet="main"),
               Part(refdes="R1", lib_ref="RES", lib_path=_LIB, status="existing", sheet="main"),
               Part(refdes="R2", lib_ref="RES", lib_path=_LIB, status="existing", sheet="main")],
        nets=[Net(name="GND", is_ground=True, pins=[PinRef(refdes="J1", pin="1"),
                                                    PinRef(refdes="R1", pin="2")]),
              # Plan order SH1..SH4, as on the carrier: the per-side stub
              # stagger then makes SH4's stub 400 longer than the sleeve's.
              Net(name="CHASSIS", is_ground=True, pins=[PinRef(refdes="J1", pin=p)
                                                        for p in ("5", "4", "3", "2")]),
              Net(name="SIG", pins=[PinRef(refdes="R1", pin="1"), PinRef(refdes="R2", pin="1")])])
    hints = {"J1": {"x": 5000, "y": 4000, "rotation": 0},
             "R1": {"x": 1500, "y": 6500, "rotation": 0},
             "R2": {"x": 1500, "y": 7000, "rotation": 0}}
    res = build_best_canvas_from_plan(plan, Ext(), n_tries=1, placement_hints=hints,
                                      strict_shorts=False)
    segs = [(w.x1, w.y1, w.x2, w.y2, w.net) for w in res.canvas.wires]
    assert _cross_net_meeting_counts(segs) == {}
    pins = {p: res.canvas.pin_world("J1", p) for p in ("2", "3", "4", "5")}
    ch = [s for s in segs if s[4] == "CHASSIS"]
    assert all(any(_point_on_segment(e.x, e.y, *s[:4]) for s in ch) for e in pins.values()), \
        "a CHASSIS shield pin is not wired (its net was culled to repair glyphs)"
    assert culled == [], f"cull loop ran on {sorted(set(culled))} ({len(culled)} culls over the best-of builds)"
    # The move is visible in the notes (a tuner otherwise cannot tell why a
    # glyph is not where the default rule puts it).
    assert any("glyph moved from" in n.text for n in res.notes),         [n.text for n in res.notes]


def test_a_trunk_routed_after_a_glyph_does_not_cull_its_net(monkeypatch):
    """End to end (the order-dependent trigger): VCC comes first in plan
    order and its glyph sits 400 above R3's stub, exactly on the straight
    line SIG is routed along LATER. The router is never told about glyphs,
    so the trunk ran through the hotspot and the cull demoted SIG to labels
    on every build. The glyph must be moved once the sheet is routed."""
    from eda_agent.design import pipeline
    from eda_agent.design.pipeline import build_best_canvas_from_plan

    syms = {(_LIB, "RES"): _passive()}

    class Ext:
        def extract_one(self, lp, lr):
            return syms.get((lp, lr))

        def extract_many(self, refs):
            return {k: syms[k] for k in refs if k in syms}

    culled = []
    real = pipeline._pick_cull_candidate

    def spy(offenders, try_cull):
        r = real(offenders, try_cull)
        culled.append(r[0])
        return r
    monkeypatch.setattr(pipeline, "_pick_cull_candidate", spy)

    def res(r):
        return Part(refdes=r, lib_ref="RES", lib_path=_LIB, status="existing", sheet="main")

    plan = DesignPlan(
        spec="x", summary="x", sheets=[Sheet(name="main", size="A4")],
        parts=[res("R1"), res("R2"), res("R3"), res("R4")],
        nets=[Net(name="VCC", is_power=True, pins=[PinRef(refdes="R3", pin="2"),
                                                   PinRef(refdes="R4", pin="2")]),
              Net(name="SIG", pins=[PinRef(refdes="R1", pin="2"),
                                    PinRef(refdes="R2", pin="1")])])
    hints = {"R1": {"x": 1000, "y": 5000, "rotation": 0},
             "R2": {"x": 4000, "y": 5000, "rotation": 0},
             # Pin 2 points up; its stub ends at y=4600, so the default
             # VCC glyph lands at (2500, 5000), on SIG's trunk.
             "R3": {"x": 2500, "y": 4100, "rotation": 90},
             "R4": {"x": 8000, "y": 2000, "rotation": 0}}
    out = build_best_canvas_from_plan(plan, Ext(), n_tries=1, placement_hints=hints,
                                      strict_shorts=False)
    cv = out.canvas
    segs = [(w.x1, w.y1, w.x2, w.y2, w.net) for w in cv.wires]
    assert _cross_net_meeting_counts(segs) == {}
    assert culled == [], f"cull loop ran on {sorted(set(culled))}"
    assert not [lb for lb in cv.labels if lb.text == "SIG"], "SIG fell back to labels"
    a, b = cv.pin_world("R1", "2"), cv.pin_world("R2", "1")
    sig = [s for s in segs if s[4] == "SIG"]
    assert any(_point_on_segment(a.x, a.y, *s[:4]) for s in sig)
    assert any(_point_on_segment(b.x, b.y, *s[:4]) for s in sig)
    for p in cv.power_ports:
        assert not any(_point_on_segment(p.x, p.y, *s[:4])
                       for s in segs if s[4] != p.text), p


def test_a_supply_glyph_does_not_rise_onto_an_input_stub():
    """Unit (D2, the U823 shape): VCC pin 14 sits 400 mil below input 3A on
    the same side of the IC. VCC's glyph rises 400 and lands mid-span on 3A's
    (staggered, longer) stub, and the cull then dropped ALL of AUD_LDO_3V3's
    wiring on AUDIO. The glyph must be moved off the stub instead."""
    from eda_agent.design.pipeline import _emit_port_cluster

    canvas = SchematicCanvas(sheets=[CSheet(name="main", size="A4")])
    segs = [(1000, 5000, 1200, 5000, "VCC"),       # VCC pin stub
            (1000, 5400, 1300, 5400, "IN_3A")]     # 3A's stub, 100 longer
    recs: list = []
    vcc = Net(name="VCC", is_power=True,
              pins=[PinRef(refdes="U1", pin="14"), PinRef(refdes="C1", pin="1")])
    _emit_port_cluster(
        canvas=canvas, sheet_name="main", net=vcc,
        net_actions=[(PinRef(refdes="U1", pin="14"), (1200, 5000), 0)],
        sheet_wire_segments=segs, routing_obstacles=[],
        other_net_pin_points=[(1000, 5400)],
        sheet_width_mils=11690, sheet_height_mils=8270, records=recs)
    assert _cross_net_meeting_counts(segs) == {}, segs
    (port,) = canvas.power_ports
    assert not any(_point_on_segment(port.x, port.y, *s[:4])
                   for s in segs if s[4] != "VCC"), (port, segs)
    assert recs[0]["relocated_from"] == (1200, 5400), recs[0]
    assert recs[0]["unresolved"] is False


def test_no_clean_site_keeps_the_default_and_flags_it():
    """Unit (the safety-critical fallback): a two-pin GND cluster boxed in by
    foreign wires on every grid row has no clean glyph site. The relocation
    search must say so (None), the default geometry must be kept unchanged
    -- the cull loop, not the glyph search, then guarantees the netlist --
    and the cluster must be recorded as unresolved so a note can say why."""
    from eda_agent.design.pipeline import (_emit_port_cluster,
                                           _relocate_port_cluster)

    W, H = 11690, 8270
    # A foreign wire on every 100-mil row except the pins' own: any glyph
    # site the search can produce sits on one.
    fence = [(0, y, W, y, "SIG") for y in range(100, H, 100) if y != 5000]
    pts = [(2000, 5000), (2300, 5000)]
    assert _relocate_port_cluster(
        net_name="GND", style="gnd_power", is_gnd=True, cluster_pts=pts,
        cluster_orients=[0, 0], spoke_obstacles=[], body_boxes=[],
        foreign_segs=fence, foreign_points=set(),
        sheet_width_mils=W, sheet_height_mils=H) is None

    canvas = SchematicCanvas(sheets=[CSheet(name="main", size="A4")])
    segs = list(fence)
    recs: list = []
    gnd = Net(name="GND", is_ground=True,
              pins=[PinRef(refdes="R1", pin="2"), PinRef(refdes="R2", pin="2")])
    _emit_port_cluster(
        canvas=canvas, sheet_name="main", net=gnd,
        net_actions=[(PinRef(refdes="R1", pin="2"), pts[0], 0),
                     (PinRef(refdes="R2", pin="2"), pts[1], 0)],
        sheet_wire_segments=segs, routing_obstacles=[],
        other_net_pin_points=[], sheet_width_mils=W, sheet_height_mils=H,
        records=recs)
    (port,) = canvas.power_ports
    assert (port.x, port.y) == (2100, 4600), port      # the default site
    assert recs[0]["unresolved"] is True
    assert recs[0]["relocated_from"] is None


def test_the_at_stub_candidate_respects_the_sheet_margin():
    """A solo pin that is itself off the sheet (early best-of builds put pins
    at negative y on PWR_IN_USB) must not be handed an off-sheet glyph: the
    'pin, stub, glyph' candidate gets the same margin test as every other."""
    from eda_agent.design.pipeline import _relocate_port_cluster

    got = _relocate_port_cluster(
        net_name="MCU_V3V3", style="bar", is_gnd=False,
        cluster_pts=[(10000, -800)], cluster_orients=[1], spoke_obstacles=[],
        body_boxes=[], foreign_segs=[], foreign_points=set(),
        sheet_width_mils=16540, sheet_height_mils=11690)
    if got is not None:
        (x, y), _ = got
        assert 200 <= x <= 16540 - 200 and 200 <= y <= 11690 - 200, got


def test_relocation_scorer_prefers_the_default_drop_and_sees_touching_names():
    """Two ranking rules a review measured missing: (1) with nothing in the
    way a moved glyph should sit at the engine's default 400-mil drop, not at
    the shortest clean one (moved glyphs at 200 among neighbours at 400 read
    as uneven drops); (2) a glyph NAME whose box edge only touches a wire
    still reads as struck through and must be charged."""
    from eda_agent.design.pipeline import _score_port_site
    from eda_agent.design.port_glyphs import glyph_geometry

    kw = dict(net_name="GND", style="gnd_power", cluster_pts=[(3000, 5000)],
              spoke_obstacles=[], body_boxes=[], foreign_segs=[],
              foreign_points=set(), is_gnd=True)
    c400 = _score_port_site(3000, 4600, **kw)[0]
    c200 = _score_port_site(3000, 4800, **kw)[0]
    assert c400 < c200, (c400, c200)

    _ink, tb = glyph_geometry(3000, 4600, "GND", "gnd_power")
    edge = (tb[2], tb[1], tb[2], tb[3], "VBUS")   # exactly on the box's right edge
    kw2 = dict(kw, foreign_segs=[edge])
    scored = _score_port_site(3000, 4600, **kw2)
    assert scored is not None and scored[2]["text"] > 0, scored


def test_a_solo_drop_into_a_body_shortens_instead_of_marching_past_it():
    """The C896 shape on AUDIO: a cap's supply pin sits just below an IC
    (U823) whose keepout starts 350 mil above the pin. The default 400-mil
    rise lands in the keepout, and the body march then pushed the glyph past
    the WHOLE IC, so the spoke ran up the IC's outline and through its text.
    A shorter drop that stops clear of the body is the drawing a person
    makes; the march is only the fallback."""
    from eda_agent.design.pipeline import _emit_port_cluster

    canvas = SchematicCanvas(sheets=[CSheet(name="main", size="A3")])
    segs: list = []
    net = Net(name="AUD_LDO_3V3", is_power=True,
              pins=[PinRef(refdes="C896", pin="1"), PinRef(refdes="U1", pin="1")])
    body = (5850, 9850, 6550, 11050)
    _emit_port_cluster(
        canvas=canvas, sheet_name="main", net=net,
        net_actions=[(PinRef(refdes="C896", pin="1"), (6200, 9500), 1)],
        sheet_wire_segments=segs, routing_obstacles=[body],
        other_net_pin_points=[], sheet_width_mils=16540,
        sheet_height_mils=11690, body_obstacles=[body])
    (port,) = canvas.power_ports
    assert (port.x, port.y) == (6200, 9700), port
    assert segs == [(6200, 9500, 6200, 9700, "AUD_LDO_3V3")], segs
