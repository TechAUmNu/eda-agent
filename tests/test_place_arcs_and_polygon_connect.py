# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
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


def _split_top_level_args(call):
    """Split the text between a call's outer parentheses at top-level
    commas, skipping quoted Pascal strings ('' is an escaped quote)."""
    open_at = call.index("(")
    args, cur, depth, i, in_str = [], "", 0, open_at + 1, False
    while i < len(call):
        ch = call[i]
        if in_str:
            cur += ch
            if ch == "'":
                if i + 1 < len(call) and call[i + 1] == "'":
                    cur += "'"
                    i += 1
                else:
                    in_str = False
        elif ch == "'":
            in_str = True
            cur += ch
        elif ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            if depth == 0:
                args.append(cur)
                return args
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            args.append(cur)
            cur = ""
        else:
            cur += ch
        i += 1
    raise AssertionError("unbalanced call")


def _render_pascal_concat(expr, values):
    """Render a Pascal `'a' + F(x) + 'b'` expression, putting values[F] in
    place of each helper call. Fails on anything else, such as a leading
    unary plus or a second argument hiding in the expression."""
    import re
    out, rest = "", expr.strip()
    first = True
    while rest:
        if not first:
            assert rest.startswith("+"), "expected '+' at: " + rest[:40]
            rest = rest[1:].lstrip()
        first = False
        if rest.startswith("'"):
            m = re.match(r"'((?:[^']|'')*)'", rest)
            out += m.group(1).replace("''", "'")
            rest = rest[m.end():].lstrip()
            continue
        m = re.match(r"(\w+)\(", rest)
        assert m, "unexpected token at: " + rest[:40]
        depth, j = 0, m.end() - 1
        while True:
            if rest[j] == "(":
                depth += 1
            elif rest[j] == ")":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out += values[m.group(1)]
        rest = rest[j + 1:].lstrip()
    return out


def test_place_arc_reply_is_one_valid_json_argument():
    # Upstream 0c478b1 left a stray comma after the width field, which made
    # the keepout/layer tail a third (unary-plus) argument to the two-argument
    # BuildSuccessResponse: the reply raised or ended in `"width":N,`.
    import json
    b = _body("Function PCB_PlaceArc(", "\nEnd;")
    i = b.index("Result := BuildSuccessResponse(RequestId,")
    call = b[i + len("Result := "):]
    args = _split_top_level_args(call)
    assert len(args) == 2, f"BuildSuccessResponse takes 2 arguments, got {len(args)}"
    data = _render_pascal_concat(args[1], {
        "FloatToJsonStr": "1.5", "BoolToJsonStr": "false",
        "EscapeJsonString": "Top Layer", "GetLayerString": "Top Layer",
    })
    obj = json.loads(data)
    assert obj["placed"] is True
    assert "keepout" in obj and "layer" in obj and "width" in obj
