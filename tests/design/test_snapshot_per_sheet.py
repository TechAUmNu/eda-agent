"""Per-sheet canvas snapshot + full-redraw clearing (2026-10-17 duplication bug)."""
import json

from eda_agent.design import orchestrator
from eda_agent.design import emitter


def _snap(tmp_path, payload):
    p = tmp_path / "proj.canvas.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _canvas(sheet, refs):
    return {"sheets": [{"name": sheet}],
            "instances": [{"refdes": r, "sheet": sheet} for r in refs]}


def test_prior_of_another_sheet_is_not_a_prior(tmp_path):
    p = _snap(tmp_path, {"canvas": _canvas("CLK", ["Y1"])})
    assert orchestrator._prior_canvas(p, ["PD"]) is None
    assert orchestrator._prior_canvas(p, ["CLK"])["instances"][0]["refdes"] == "Y1"


def test_by_sheet_entry_wins_for_an_older_sheet(tmp_path):
    p = _snap(tmp_path, {"canvas": _canvas("CLK", ["Y1"]),
                         "by_sheet": {"PD": {"canvas": _canvas("PD", ["U1"])}}})
    got = orchestrator._prior_canvas(p, ["PD"])
    assert got["instances"][0]["refdes"] == "U1"


def test_legacy_call_without_sheets_still_reads_top_level(tmp_path):
    p = _snap(tmp_path, {"canvas": _canvas("CLK", ["Y1"])})
    assert orchestrator._prior_canvas(p)["instances"][0]["refdes"] == "Y1"


class _Inst:
    def __init__(self, refdes):
        self.refdes = refdes


class _Canvas:
    def instances_on(self, sheet):
        return [_Inst("R1"), _Inst("C2")]


class _Bridge:
    def __init__(self):
        self.calls = []

    def send_command(self, cmd, params, **kw):
        self.calls.append((cmd, params))
        return {"success": True}


def test_full_redraw_clears_plan_parts_and_copper():
    b = _Bridge()
    res = emitter.EmitResult(project_path="x")
    emitter._clear_for_full_redraw(_Canvas(), "S", b, res)
    comps = [p["filter"] for c, p in b.calls if p["object_type"] == "eSchComponent"]
    assert comps == ["Designator=R1", "Designator=C2"]
    types = {p["object_type"] for c, p in b.calls if p["filter"] == ""}
    assert {"eWire", "eNetLabel", "ePowerObject", "eBus", "eBusEntry",
            "eNoERC"} <= types
    assert not res.failures
