# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The no-stranded-parts rule may never take a net's last name off a pin.

Found on the multiviewer carrier's PWR_EFUSE sheet: R105 and R146 each sit
between two force_label nets. The rule promoted the net with fewer plan pins
to "wire", but that net had ONE pin on the sheet (its other pin was on a
second sheet), so the stub had nothing to route to, _trim_dangling_wires cut
it, and R105.2 / R146.2 emitted with no wire and no label. A promoted net
that does reach several pins lost its name instead (PWR_3V3 FPGA_RST,
PWR_MECH CHASSIS), which for a net that leaves the sheet is the cross-sheet
connection itself.

The oracle is the geometric net solver, written apart from the pipeline:
every pin of a net that leaves its sheet, or is force_label, must solve to
that net's NAME on its own sheet, not just to some group of pins.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eda_agent.design import pipeline
from eda_agent.design._wiring import _apply_no_stranded_parts_rule
from eda_agent.design.benchmark import SyntheticSymbolExtractor
from eda_agent.design.plan import DesignPlan, Net, Part, PinRef, Sheet
from eda_agent.fileio.netlist_solver import solve_nets


def _p(ref: str, pin: str) -> PinRef:
    return PinRef(refdes=ref, pin=pin)


def _efuse_plan() -> DesignPlan:
    """R2 between RAW (3 pins here + 1 off-sheet) and OUT (1 pin here + 1
    off-sheet), both force_label: the R105 shape."""
    return DesignPlan(
        spec="lost labels", summary="R105 shape",
        sheets=[Sheet(name="main"), Sheet(name="stubs")],
        parts=[
            Part(refdes="R1", lib_ref="RES", lib_path="test.SchLib", sheet="main"),
            Part(refdes="R2", lib_ref="RES", lib_path="test.SchLib", sheet="main"),
            Part(refdes="C1", lib_ref="CAP", lib_path="test.SchLib", sheet="main"),
            Part(refdes="TP1", lib_ref="TP", lib_path="test.SchLib", sheet="stubs"),
            Part(refdes="TP2", lib_ref="TP", lib_path="test.SchLib", sheet="stubs"),
        ],
        nets=[
            Net(name="GND", is_ground=True, pins=[_p("R1", "2"), _p("C1", "2")]),
            Net(name="RAW", force_label=True, pins=[
                _p("R1", "1"), _p("R2", "1"), _p("C1", "1"), _p("TP1", "1")]),
            Net(name="OUT", force_label=True, pins=[_p("R2", "2"), _p("TP2", "1")]),
        ],
    )


def _one_pin_part_plan() -> DesignPlan:
    """TP9 has one net, SIG, which reaches three pins here and leaves the
    sheet: the FPGA_RST / CHASSIS shape (promoted, wired, name lost)."""
    return DesignPlan(
        spec="lost labels", summary="FPGA_RST shape",
        sheets=[Sheet(name="main"), Sheet(name="stubs")],
        parts=[
            Part(refdes="TP9", lib_ref="TP", lib_path="test.SchLib", sheet="main"),
            Part(refdes="R3", lib_ref="RES", lib_path="test.SchLib", sheet="main"),
            Part(refdes="R4", lib_ref="RES", lib_path="test.SchLib", sheet="main"),
            Part(refdes="TP10", lib_ref="TP", lib_path="test.SchLib", sheet="stubs"),
        ],
        nets=[
            Net(name="GND", is_ground=True, pins=[_p("R3", "2"), _p("R4", "2")]),
            Net(name="SIG", force_label=True, pins=[
                _p("TP9", "1"), _p("R3", "1"), _p("R4", "1"), _p("TP10", "1")]),
        ],
    )


def _solved_names(plan: DesignPlan, canvas, sheet: str) -> dict[str, str]:
    on = {p.refdes for p in plan.parts if p.sheet == sheet}
    pins = []
    for net in plan.nets:
        for ref in net.pins:
            if ref.refdes in on:
                ep = canvas.pin_world(ref.refdes, ref.pin)
                pins.append({"component": ref.refdes, "pin": ref.pin,
                             "x": ep.x, "y": ep.y})
    return solve_nets(
        pins,
        [{"x1": w.x1, "y1": w.y1, "x2": w.x2, "y2": w.y2}
         for w in canvas.wires_on(sheet)],
        [{"x": p.x, "y": p.y, "name": p.text} for p in canvas.power_ports_on(sheet)],
        [{"x": j.x, "y": j.y} for j in canvas.junctions_on(sheet)],
        [{"x": lb.x, "y": lb.y, "name": lb.text} for lb in canvas.labels_on(sheet)],
    )["pin_nets"]


def _assert_every_pin_named(plan: DesignPlan, canvas) -> None:
    for sheet in sorted({p.sheet for p in plan.parts}):
        names = _solved_names(plan, canvas, sheet)
        on = {p.refdes for p in plan.parts if p.sheet == sheet}
        for net in plan.nets:
            here = [r for r in net.pins if r.refdes in on]
            if not here or net.is_ground or net.is_power:
                continue
            if len(here) == len(net.pins) and not net.force_label:
                continue
            for r in here:
                got = names[f"{r.refdes}.{r.pin}"]
                assert got == net.name, (
                    f"{r.refdes}.{r.pin} on {sheet!r} solves to {got!r}, not "
                    f"{net.name!r}: the pin lost its net's name")


# --- the rule itself ----------------------------------------------------------

def test_a_net_with_one_pin_on_the_sheet_is_never_promoted() -> None:
    """R2's cheapest net (OUT, 2 plan pins) has only R2.2 on this sheet:
    promoting it can draw nothing, so R2 keeps both labels instead."""
    plan = _efuse_plan()
    reps = {"GND": "port", "RAW": "label_per_pin", "OUT": "label_per_pin"}
    left: list[str] = []
    promoted = _apply_no_stranded_parts_rule(
        list(plan.nets), reps, {"R1", "R2", "C1"}, left)
    assert promoted == []
    assert reps == {"GND": "port", "RAW": "label_per_pin", "OUT": "label_per_pin"}
    assert left == ["R2"]


def test_a_promoted_net_that_leaves_the_sheet_keeps_its_name() -> None:
    plan = _one_pin_part_plan()
    reps = {"GND": "port", "SIG": "label_per_pin"}
    promoted = _apply_no_stranded_parts_rule(
        list(plan.nets), reps, {"TP9", "R3", "R4"})
    assert promoted == ["SIG"]
    assert reps["SIG"] == "wire_named"


def test_a_force_label_net_promoted_on_one_sheet_keeps_its_name() -> None:
    plan = _one_pin_part_plan()
    sig = next(n for n in plan.nets if n.name == "SIG")
    local = sig.model_copy(update={"pins": sig.pins[:3]})   # force_label, all here
    reps = {"SIG": "label_per_pin"}
    assert _apply_no_stranded_parts_rule([local], reps, {"TP9", "R3", "R4"}) == ["SIG"]
    assert reps["SIG"] == "wire_named"


def test_a_part_with_no_wireable_net_keeps_its_labels() -> None:
    plan = _efuse_plan()
    reps = {"GND": "port", "RAW": "label_per_pin", "OUT": "label_per_pin"}
    left: list[str] = []
    promoted = _apply_no_stranded_parts_rule(
        list(plan.nets), reps, {"TP1", "TP2"}, left)
    assert promoted == []
    assert reps == {"GND": "port", "RAW": "label_per_pin", "OUT": "label_per_pin"}
    assert left == ["TP1", "TP2"]


def test_a_local_net_without_force_label_is_still_promoted_to_plain_wire() -> None:
    """The single-sheet behaviour the existing divider test pins down."""
    plan = _one_pin_part_plan()
    sig = next(n for n in plan.nets if n.name == "SIG")
    local = sig.model_copy(update={"force_label": False,
                                   "pins": sig.pins[:3]})
    reps = {"SIG": "label_per_pin"}
    assert _apply_no_stranded_parts_rule([local], reps, {"TP9", "R3", "R4"}) == ["SIG"]
    assert reps["SIG"] == "wire"


# --- the drawn sheet --------------------------------------------------------

@pytest.mark.parametrize("make", [_efuse_plan, _one_pin_part_plan])
def test_every_pin_of_a_named_net_solves_to_its_name(make) -> None:
    plan = make()
    res = pipeline.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))
    assert res.ok, res.failures
    _assert_every_pin_named(plan, res.canvas)
    assert not [n for n in res.notes if "does not reach a" in n.text]


def test_a_promoted_net_that_leaves_the_sheet_is_wired_and_labelled_once() -> None:
    plan = _one_pin_part_plan()
    res = pipeline.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))
    sig_wires = [w for w in res.canvas.wires_on("main") if w.net == "SIG"]
    sig_labels = [l for l in res.canvas.labels_on("main") if l.text == "SIG"]
    assert sig_wires, "SIG was promoted to wire and must still be drawn as one"
    assert len(sig_labels) == 1


def test_the_guard_reports_a_bare_pin(monkeypatch) -> None:
    """The last-mile check must see the defect when the fix is undone."""
    import eda_agent.design._wiring as wiring

    def old_rule(nets, reps, sheet_refdes, left_labelled=None):
        promoted = []
        by_part = {}
        for net in nets:
            for pr in net.pins:
                if pr.refdes in sheet_refdes:
                    by_part.setdefault(pr.refdes, []).append(net)
        for ref in sorted(by_part):
            inc = by_part[ref]
            if any(reps.get(n.name) != "label_per_pin" for n in inc):
                continue
            best = min(inc, key=lambda n: (len(n.pins), n.name))
            reps[best.name] = "wire"
            promoted.append(best.name)
        return promoted

    monkeypatch.setattr(pipeline, "_apply_no_stranded_parts_rule", old_rule)
    plan = _efuse_plan()
    res = pipeline.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))
    assert ("OUT", "R2.2") in pipeline._unnamed_pins(res.canvas, plan, "main")
    assert any("R2.2" in n.text for n in res.notes if "does not reach a" in n.text)


PLANS_DIR = Path(__file__).resolve().parents[1] / "benchmarks" / "plans"


def test_single_sheet_plans_never_take_the_named_wire_path(monkeypatch) -> None:
    """Zero-change guard: no benchmark plan (single sheet, no force_label)
    may reach the anchor-label code."""
    def boom(*_a, **_k):
        raise AssertionError("wire_named reached on a single-sheet plan")

    monkeypatch.setattr(pipeline, "_name_anchor", boom)
    plans = [DesignPlan.model_validate(json.loads(p.read_text(encoding="utf-8")))
             for p in sorted(PLANS_DIR.glob("*.json"))]
    assert len(plans) >= 3
    for plan in plans:
        res = pipeline.build_best_canvas_from_plan(plan, SyntheticSymbolExtractor(plan))
        assert res.canvas.wires


# --------------------------- where the one label goes (second review, L1)

def _fan_canvas():
    """The PWR_FAN_MON PWR_IMON shape: the net's stub end sits in an IC's
    100-mil pin fan, right under the next pin's wire, while the net runs
    4800 mil of empty straight wire to its other pin."""
    from eda_agent.design.canvas import NetLabel, SchematicCanvas, Sheet as CSheet

    cv = SchematicCanvas()
    cv.add_sheet(CSheet(name="main", width_mils=20000, height_mils=15000))
    segs = [(10700, 7500, 10700, 9500, "SIG"), (10700, 9500, 15500, 9500, "SIG"),
            (15500, 9500, 16000, 9500, "SIG"),
            (15400, 9600, 16000, 9600, "VDD")]          # the next pin's row
    anchor = NetLabel(text="SIG", x=15500, y=9500, orientation=0,
                      sheet="main", justification=2)
    cv.add_labels([anchor])
    return cv, segs, anchor


def test_a_cramped_named_label_moves_to_the_start_of_its_longest_run():
    cv, segs, anchor = _fan_canvas()
    named = {"SIG": anchor}
    assert pipeline._reanchor_named_labels(cv, "main", named, segs, [], []) == 1
    lab = named["SIG"]
    # One grid step into the run: at the corner itself the riser's copper
    # is inside the clearance pad.
    assert (lab.x, lab.y, lab.justification) == (10800, 9500, 0)
    assert cv.labels == [lab]


def test_a_clean_named_label_stays_where_it_is():
    """Control: without the cramping wire the stub-end anchor is clean
    (even with padding), so nothing moves."""
    cv, segs, anchor = _fan_canvas()
    named = {"SIG": anchor}
    assert pipeline._reanchor_named_labels(cv, "main", named, segs[:3], [], []) == 0
    assert named["SIG"] is anchor


def test_a_named_label_never_lands_on_another_nets_copper():
    """The run's start is crossed by a foreign wire there: the candidate is
    illegal (a label on two nets' copper is a short), so the next one is
    taken."""
    cv, segs, anchor = _fan_canvas()
    segs = segs + [(10700, 9000, 10700, 9500, "OTHER")]
    named = {"SIG": anchor}
    pipeline._reanchor_named_labels(cv, "main", named, segs, [], [])
    lab = named["SIG"]
    assert (lab.x, lab.y) != (10700, 9500)
    assert lab.y == 9500 and 10700 < lab.x < 16000
