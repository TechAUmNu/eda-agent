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


def test_place_polygon_is_dispatched_and_checks_its_input():
    assert "'place_polygon':           Result := PCB_PlacePolygon(Params, RequestId);" in _src()
    b = _body("Function PCB_PlacePolygon(", "{ PCB_PlacePolygonRect - ")
    assert "IsFloatStr(XTok)" in b and "(N < 3)" in b
    assert "UNKNOWN_NET" in b
    assert "Polygon.PourIndex := PourIdx" in b and "GotIdx := Polygon.PourIndex" in b


def test_modify_polygon_reads_back_pour_index():
    b = _body("Function PCB_ModifyPolygon(", "{ PCB_PlacePolygon - ")  if False else _src()
    assert "If FoundPoly.PourIndex = PourIdx Then" in b


def test_get_polygon_pieces_is_dispatched_and_reports_location():
    assert "'get_polygon_pieces':      Result := PCB_GetPolygonPieces(Params, RequestId);" in _src()
    b = _body("Function PCB_GetPolygonPieces(", "{ PCB_PlacePolygonRect - ")
    assert "Region.BoundingRectangle" in b and "RegionCopperSqMils(Region, Exact)" in b



def test_repour_documents_the_overlap_shrink():
    b = _body("Function PCB_RepourPolygons(", "{ PCB_PlaceVia - ")
    assert "IN-PLACE REBUILD SHRINKS OVERLAPPING POURS" in b
    assert "ePolyNoHatch" not in b and "Poured := False" not in b.replace("{ := False", "")
