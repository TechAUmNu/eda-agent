"""A culled signal net must not silently lose a pin whose stub was ZERO long.

Pass 1 of _wire_sheet adds no stub segment for a pin whose stub clipped to
zero length (no room at all; the hotspot serves as the stub end). When that
net later lost the cross-net cull, _try_cull re-admitted and labelled only
the pins that HAD stub segments, so the zero-stub pin ended with neither
wire nor label: the net was split while the build reported ok=True with no
failure. Found by a 600-plan differential fuzz (seeds 578 and 134); this is
seed 578 frozen. Fails without the zero_stub_pins handling in _try_cull.
"""
from __future__ import annotations

from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
from eda_agent.design.symbols import SymbolBBox, SymbolModel, SymbolPin
from eda_agent.fileio.netlist_solver import solve_nets

_LIB = "/fake/lib.SchLib"


def _pin(d, x, y, o):
    return SymbolPin(designator=d, name=d, x=x, y=y, orientation=o, length=100,
                     electrical_type="passive")


_SYMS = {
    (_LIB, "RES"): SymbolModel(
        lib_path=_LIB, lib_ref="RES", pins=(_pin("1", -100, 0, 2), _pin("2", 100, 0, 0)),
        body_bbox=SymbolBBox(x_min=-50, y_min=-30, x_max=50, y_max=30)),
    (_LIB, "IC"): SymbolModel(
        lib_path=_LIB, lib_ref="IC", pins=(
            _pin("1", -300, 100, 2), _pin("2", -300, 0, 2), _pin("3", -300, -100, 2),
            _pin("4", 300, 100, 0), _pin("5", 300, 0, 0), _pin("6", 300, -100, 0),
            _pin("7", 0, 300, 1), _pin("8", 0, -300, 3)),
        body_bbox=SymbolBBox(x_min=-200, y_min=-200, x_max=200, y_max=200)),
}


class _Ext:
    def extract_one(self, lp, lr):
        return _SYMS.get((lp, lr))

    def extract_many(self, refs):
        return {k: _SYMS[k] for k in refs if k in _SYMS}


def test_a_culled_net_keeps_its_zero_length_stub_pin_connected(monkeypatch):
    from eda_agent.design import pipeline

    nets = {
        "GND": ({"is_ground": True}, [("R4", "2"), ("R5", "1"), ("U2", "5"), ("U2", "4")]),
        "VCC": ({"is_power": True}, [("R2", "1"), ("R4", "1")]),
        "S0": ({}, [("U1", "3"), ("U1", "6")]),
        "S1": ({}, [("R3", "1"), ("R5", "2")]),
        "S2": ({}, [("U1", "2"), ("U2", "1"), ("R1", "2")]),
        "S3": ({}, [("U1", "7"), ("U2", "3")]),
        "S4": ({}, [("U1", "1"), ("R3", "2"), ("U2", "6")]),
        "S5": ({}, [("U1", "5"), ("U1", "8"), ("R1", "1")]),
        "S6": ({}, [("U2", "7"), ("U1", "4"), ("U2", "8")]),
        "S7": ({}, [("U2", "2"), ("R2", "2")]),
    }
    parts = [("U1", "IC"), ("U2", "IC")] + [(f"R{i}", "RES") for i in range(1, 6)]
    plan = DesignPlan(
        spec="x", summary="x", sheets=[Sheet(name="main", size="A4")],
        parts=[Part(refdes=r, lib_ref=lr, lib_path=_LIB, status="existing", sheet="main")
               for r, lr in parts],
        nets=[Net(name=n, pins=[PinRef(refdes=r, pin=p) for r, p in pins], **kw)
              for n, (kw, pins) in nets.items()])
    hints = {"U1": {"x": 2300, "y": 2700, "rotation": 0},
             "U2": {"x": 2300, "y": 2100, "rotation": 0},
             "R1": {"x": 3400, "y": 3000, "rotation": 270},
             "R2": {"x": 2300, "y": 3400, "rotation": 270},
             "R3": {"x": 3700, "y": 2200, "rotation": 90},
             "R4": {"x": 3000, "y": 3600, "rotation": 90},
             "R5": {"x": 3700, "y": 3700, "rotation": 0}}

    # The fixture must still exercise the case: U1.7's stub clips to zero
    # length and S3 is culled.
    zero, culled = [], []
    real_se, real_pick = pipeline._stub_endpoints, pipeline._pick_cull_candidate

    def se(*a, **k):
        r = real_se(*a, **k)
        if r[0] == r[1]:
            zero.append(r[0])
        return r

    def pick(offenders, try_cull):
        r = real_pick(offenders, try_cull)
        culled.append(r[0])
        return r
    monkeypatch.setattr(pipeline, "_stub_endpoints", se)
    monkeypatch.setattr(pipeline, "_pick_cull_candidate", pick)

    res = pipeline.build_canvas_from_plan(plan, _Ext(), placement_hints=hints,
                                          strict_shorts=True)
    cv = res.canvas
    u1_7 = cv.pin_world("U1", "7")
    assert (u1_7.x, u1_7.y) in zero and "S3" in culled, (zero, culled)

    pins = [{"component": i.refdes, "pin": ep.pin_id, "x": ep.x, "y": ep.y}
            for i in cv.instances for ep in i.all_pin_endpoints()]
    sol = solve_nets(
        pins, [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2} for w in cv.wires],
        [{"x": p.x, "y": p.y, "name": p.text} for p in cv.power_ports],
        [{"x": j.x, "y": j.y} for j in cv.junctions],
        [{"x": lb.x, "y": lb.y, "name": lb.text} for lb in cv.labels])
    if res.ok:
        # ok must mean the netlist is right: every planned net is one net.
        for n, (_kw, members) in nets.items():
            got = {sol["pin_nets"].get(f"{r}.{p}") for r, p in members}
            assert len(got) == 1, f"{n} split into {got}"
    else:
        # Or the build must say it could not draw the net -- never ok=True on
        # a split netlist.
        assert any("cannot be drawn" in f.text for f in res.failures), res.failures
