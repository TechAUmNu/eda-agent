"""2026.10.17.02: pcb.place_arcs (decimal mils, with nets) and the
polygon_connect rule type.

A rounded 100-ohm pair corner needs concentric P/N arcs whose radii differ
by fractions of a mil, and each arc needs its net; PCB_PlaceArc parses
integers and binds no net. Stitching vias want a direct pour connection,
which create_design_rule could not make.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _src():
    return SRC.read_text(encoding="utf-8")


def _body(start, end):
    s = _src()
    i = s.index(start)
    return s[i:s.index(end, i)]


def test_place_arcs_is_dispatched():
    assert "'place_arcs':              Result := PCB_PlaceArcs(Params, RequestId);" in _src()


def test_place_arcs_keeps_decimals_and_binds_nets():
    b = _body("Function PCB_PlaceArcs(", "{ PCB_PlaceArc - ")
    assert "StrToIntDef" not in b
    assert "MilsToCoordF(ARad)" in b and "MilsToCoordF(AXC)" in b
    assert "BindPrimitiveToNet(FoundNet, Arc)" in b
    assert "If ARad <= 0 Then" in b


def test_polygon_connect_uses_ordinals_not_enum_names():
    b = _body("Else If RuleTypeStr = 'polygon_connect' Then", "Else If RuleTypeStr = 'vias_under_smd' Then")
    assert "PCBRuleFactory(20)" in b
    assert "eDirectConnectToPlane :" not in b and ":= eDirectConnectToPlane" not in b
    assert "ConnectStr = 'direct' Then ConnectVal := 1" in b


def test_python_tool_sends_connect_style():
    py = (Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "tools" / "pcb.py").read_text(encoding="utf-8")
    assert 'params["connect_style"] = connect_style' in py
    assert '"pcb.place_arcs"' in py
