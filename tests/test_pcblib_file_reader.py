# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_read_pcblib_file: the saved .PcbLib, read with no Altium involved.

It exists because Altium's read-backs answer from memory, and memory and
file disagreed twice on 2026-10-04 (a deleted pad written back by the
save; a 3D body stored 50,000 mil from its footprint while its self-check
passed). The record layout is pinned here on synthetic Data streams built
byte by byte, so a wrong offset fails without needing a library on disk,
and then checked against the real carrier library when it is present.
"""
from __future__ import annotations

import asyncio
import struct
from pathlib import Path

import pytest

from eda_agent.fileio import altium_pcblib as pcblib

_CARRIER = Path(r"H:/git/multiviewer/hardware/multiviewer/carrier.PcbLib")


def _block(payload: bytes) -> bytes:
    return struct.pack("<I", len(payload)) + payload


def _u(mils: float) -> int:
    return int(round(mils * 1e4))


def _pad(designator: str, x: float, y: float, xs: float, ys: float,
         hole: float = 0.0, shape: int = 2, layer: int = 1,
         rotation: float = 0.0, plated: bool = True,
         paste: "float | None" = None, mask: "float | None" = None,
         roundrect_pct: "int | None" = None, slot: "float | None" = None,
         hole_rot: float = 0.0) -> bytes:
    main = bytearray(110)
    main[0] = layer
    struct.pack_into("<ii", main, 13, _u(x), _u(y))
    struct.pack_into("<ii", main, 21, _u(xs), _u(ys))
    struct.pack_into("<i", main, 45, _u(hole))
    main[49] = shape
    struct.pack_into("<d", main, 52, rotation)
    main[60] = 1 if plated else 0
    if paste is not None:
        struct.pack_into("<i", main, 86, _u(paste))
        main[101] = 2
    if mask is not None:
        struct.pack_into("<i", main, 90, _u(mask))
        main[102] = 2
    size = bytearray(651)
    if roundrect_pct is not None:
        size[532] = 9
        size[564] = roundrect_pct
    if slot is not None:
        size[262] = 2
        struct.pack_into("<i", size, 263, _u(slot))
        struct.pack_into("<d", size, 267, hole_rot)
    name = bytes([len(designator)]) + designator.encode("latin-1")
    blocks = [name, b"\x00", b"\x00" * 5, b"\x00", bytes(main), bytes(size)]
    return b"\x02" + b"".join(_block(b) for b in blocks)


def _track(layer, x1, y1, x2, y2, width) -> bytes:
    b = bytearray(49)
    b[0] = layer
    struct.pack_into("<iiiii", b, 13, _u(x1), _u(y1), _u(x2), _u(y2),
                     _u(width))
    return b"\x04" + _block(bytes(b))


def _fill(layer, x1, y1, x2, y2, rotation) -> bytes:
    b = bytearray(50)
    b[0] = layer
    struct.pack_into("<iiii", b, 13, _u(x1), _u(y1), _u(x2), _u(y2))
    struct.pack_into("<d", b, 29, rotation)
    return b"\x06" + _block(bytes(b))


def _text(text: str, layer, x, y, height, rotation, stroke) -> bytes:
    b = bytearray(252)
    b[0] = layer
    struct.pack_into("<iii", b, 13, _u(x), _u(y), _u(height))
    struct.pack_into("<d", b, 27, rotation)
    struct.pack_into("<i", b, 36, _u(stroke))
    s = bytes([len(text)]) + text.encode("latin-1")
    return b"\x05" + _block(bytes(b)) + _block(s)


def _arc(layer, x, y, r, start, end, width) -> bytes:
    b = bytearray(56)
    b[0] = layer
    struct.pack_into("<iii", b, 13, _u(x), _u(y), _u(r))
    struct.pack_into("<dd", b, 25, start, end)
    struct.pack_into("<i", b, 41, _u(width))
    return b"\x01" + _block(bytes(b))


def _props_record(kind: int, layer: int, props: str,
                  vertices: list) -> bytes:
    head = bytearray(18)
    head[0] = layer
    text = props.encode("latin-1") + b"\x00"
    body = bytes(head) + struct.pack("<I", len(text)) + text
    body += struct.pack("<I", len(vertices))
    for vx, vy in vertices:
        body += struct.pack("<dd", vx * 1e4, vy * 1e4)
    return bytes([kind]) + _block(body)


def _stream(*records: bytes) -> bytes:
    header = b"\x07ZZ_TEST"
    return struct.pack("<I", len(header)) + header + b"".join(records)


def test_a_pad_is_read_at_the_documented_offsets():
    fp = pcblib.parse_footprint(_stream(
        _pad("1", 0, 0, 60, 60, hole=40, shape=2, layer=74),
        _pad("3", 200.25, -12.5, 45, 25, roundrect_pct=30, paste=-2.0,
             mask=1.5, rotation=90.0),
        _pad("S", 10, 10, 80, 40, hole=20, slot=60, hole_rot=90.0,
             plated=False, layer=74, shape=1)))
    one, three, slot = fp["pads"]
    assert one == {**one, "designator": "1", "x_mils": 0.0,
                   "x_size_mils": 60.0, "hole_mils": 40.0,
                   "shape": "rectangular", "layer": "MultiLayer",
                   "plated": True, "paste_expansion_mils": None}
    assert three["x_mils"] == 200.25 and three["y_mils"] == -12.5
    assert three["shape"] == "roundrect" and three["corner_pct"] == 30
    assert three["paste_expansion_mils"] == -2.0
    assert three["mask_expansion_mils"] == 1.5
    assert three["rotation"] == 90.0 and three["layer"] == "TopLayer"
    assert slot["hole_type"] == "slot" and slot["slot_length_mils"] == 60.0
    assert slot["hole_rotation"] == 90.0 and slot["plated"] is False
    assert slot["shape"] == "round"


def test_a_text_is_two_blocks_and_does_not_desync_what_follows():
    """Mutation: read a text as ONE block. Everything after it then parses
    as garbage: here, the track would come back wrong or be lost."""
    fp = pcblib.parse_footprint(_stream(
        _text("+", 33, 49.213, -62, 40, 0.0, 6),
        _track(33, -40.125, -40.375, 140.875, -40.375, 5.906)))
    assert fp["texts"] == [{"text": "+", "layer": "TopOverlay",
                            "x_mils": 49.213, "y_mils": -62.0,
                            "height_mils": 40.0, "rotation": 0.0,
                            "stroke_width_mils": 6.0}]
    assert fp["tracks"] == [{"layer": "TopOverlay", "x1_mils": -40.125,
                             "y1_mils": -40.375, "x2_mils": 140.875,
                             "y2_mils": -40.375, "width_mils": 5.906}]
    assert "parse_error" not in fp


def test_fills_arcs_and_layers():
    fp = pcblib.parse_footprint(_stream(
        _fill(69, -10, 200, 10, 220, 45.0),
        _arc(57, 1, 2, 25, 0.0, 270.0, 5)))
    assert fp["fills"][0] == {"layer": "Mechanical13", "x1_mils": -10.0,
                              "y1_mils": 200.0, "x2_mils": 10.0,
                              "y2_mils": 220.0, "rotation": 45.0}
    arc = fp["arcs"][0]
    assert arc["layer"] == "Mechanical1" and arc["radius_mils"] == 25.0
    assert arc["end_angle"] == 270.0 and arc["width_mils"] == 5.0


def test_a_component_body_carries_its_model_placement():
    """The fields the 2026-10-04 misplacement and the Z question needed."""
    props = ("|V7_LAYER=MECHANICAL1|MODEL.NAME=x.step|MODEL.MODELTYPE=1|"
             "MODEL.2D.X=-49989.5mil|MODEL.2D.Y=-50020mil|"
             "MODEL.2D.ROTATION=0.000|MODEL.3D.ROTX=90.000|"
             "MODEL.3D.ROTY=0.000|MODEL.3D.ROTZ=45.000|MODEL.3D.DZ=3mil|"
             "STANDOFFHEIGHT=-40.3543mil|OVERALLHEIGHT=1mm|MODEL.EMBED=TRUE|")
    fp = pcblib.parse_footprint(_stream(_props_record(
        12, 57, props, [(0, 0), (10, 0), (10, 5), (0, 5)])))
    body = fp["bodies"][0]
    assert body["layer"] == "Mechanical1", (
        "one spelling across kinds: the fill/arc byte name, not V7_LAYER's")
    assert body["layer_v7"] == "MECHANICAL1"
    assert body["standoff_height_mils"] == -40.3543
    assert body["overall_height_mils"] == pytest.approx(39.3701, abs=1e-4)
    assert body["outline_bbox_mils"] == {"x1": 0.0, "y1": 0.0,
                                         "x2": 10.0, "y2": 5.0}
    model = body["model"]
    assert model["type"] == "generic" and model["name"] == "x.step"
    assert model["x_mils"] == -49989.5 and model["y_mils"] == -50020.0
    assert model["rot_x"] == 90.0 and model["rot_z"] == 45.0
    assert model["dz_mils"] == 3.0 and model["embedded"] is True


def test_a_region_reports_layer_kind_and_extent():
    fp = pcblib.parse_footprint(_stream(_props_record(
        11, 37, "|V7_LAYER=TOPSOLDER|KIND=0|",
        [(150, -20), (190, -20), (190, 20), (150, 20), (150, -20)])))
    region = fp["regions"][0]
    assert region["layer"] == "TopSolder" and region["kind"] == "0"
    assert region["layer_v7"] == "TOPSOLDER"
    assert region["vertex_count"] == 5
    assert region["bbox_mils"] == {"x1": 150.0, "y1": -20.0,
                                   "x2": 190.0, "y2": 20.0}


def test_a_truncated_stream_keeps_what_parsed_and_says_where_it_broke():
    good = _track(33, 0, 0, 10, 0, 5)
    data = _stream(good, good)[:-10]
    fp = pcblib.parse_footprint(data)
    assert len(fp["tracks"]) == 1
    assert "runs past the end" in fp["parse_error"]


def test_an_unknown_record_kind_stops_the_parse_and_says_so():
    """Mutation: guess one block for an unknown type and carry on.

    The block count is known only for the eight kinds the reader parses.
    A guess that is wrong reads every later record from the wrong offset,
    so the parse STOPS there, keeps what came before, and says where.
    """
    before = _track(33, 0, 0, 1, 1, 1)
    odd = bytes([9]) + _block(bytes(12))
    stream = _stream(before, odd, _track(33, 5, 5, 6, 6, 1))
    fp = pcblib.parse_footprint(stream)
    assert len(fp["tracks"]) == 1, "only the record BEFORE the unknown one"
    assert fp["complete"] is False
    at = len(_stream(before))
    assert f"unknown record type 9 at byte {at}" in fp["parse_error"]
    assert "not parsed" in fp["parse_error"]


def test_a_two_block_unknown_record_cannot_invent_primitives():
    """The case a guess gets wrong: an unknown record with TWO blocks,
    whose second block is the bytes of a pad record.

    A one-block guess reads the second block's length prefix as the next
    record type, misreads everything after it, and can produce primitives
    that are not in the file. Stopping produces none and says the
    footprint is incomplete.
    """
    fake_pad = _pad("X", 1, 1, 10, 10)
    odd = bytes([10]) + _block(bytes(8)) + _block(fake_pad)
    fp = pcblib.parse_footprint(_stream(odd))
    assert fp["pads"] == [] and fp["tracks"] == []
    assert fp["complete"] is False
    assert "unknown record type 10" in fp["parse_error"]


def test_a_complete_footprint_says_so():
    fp = pcblib.parse_footprint(_stream(_track(33, 0, 0, 1, 1, 1)))
    assert fp["complete"] is True and "parse_error" not in fp


def test_the_tool_does_not_present_a_partial_footprint_as_proof(
        tmp_path, monkeypatch):
    """Mutation: return ok=True for a footprint whose parse stopped."""
    lib = tmp_path / "x.PcbLib"
    lib.write_bytes(b"placeholder")
    partial = {"name": "FP", "storage": "FP", "complete": False,
               "parse_error": "unknown record type 9 at byte 40",
               "pads": [], "counts": {}}

    def fake(path, footprint_name="", offset=0, limit=25):
        out = {"library_path": str(path), "total": 2,
               "footprints": [partial], "incomplete": ["FP"]}
        if footprint_name:
            out["found"] = True
        else:
            out.update(offset=0, count=1)
        return out

    monkeypatch.setattr(pcblib, "read_pcblib", fake)
    tool = _tool()
    one = asyncio.run(tool(library_path=str(lib), footprint_name="FP"))
    assert one["ok"] is False
    assert "could not be read to the end" in one["reason"]
    page = asyncio.run(tool(library_path=str(lib)))
    assert page["ok"] is True and page["incomplete"] == ["FP"]
    assert "partial" in page["warning"]


@pytest.mark.parametrize("raw,name", [
    ("TOPSOLDER", "TopSolder"), ("BOTTOMPASTE", "BottomPaste"),
    ("TOPOVERLAY", "TopOverlay"), ("MECHANICAL13", "Mechanical13"),
    ("MECHANICAL32", "Mechanical32"), ("KEEPOUT", "KeepOut"),
    ("MULTILAYER", "MultiLayer"), ("TOP", "TopLayer"),
    ("BOTTOM", "BottomLayer"), ("MID3", "MidLayer3"),
    ("PLANE2", "InternalPlane2"), ("SOMETHINGELSE", "SOMETHINGELSE"),
])
def test_v7_layer_names_use_the_byte_spelling(raw, name):
    """Mutation: report V7_LAYER as stored. A region on TOPSOLDER and a
    pad-side fill on TopSolder then compare unequal for the same layer."""
    assert pcblib.v7_layer_name(raw) == name


def test_v7_names_match_the_byte_names_of_the_same_layer():
    for code, raw in ((37, "TOPSOLDER"), (35, "TOPPASTE"), (33, "TOPOVERLAY"),
                      (56, "KEEPOUT"), (74, "MULTILAYER"), (69,
                      "MECHANICAL13"), (1, "TOP"), (32, "BOTTOM")):
        assert pcblib.v7_layer_name(raw) == pcblib.layer_name(code)


def test_layer_names():
    assert pcblib.layer_name(1) == "TopLayer"
    assert pcblib.layer_name(32) == "BottomLayer"
    assert pcblib.layer_name(35) == "TopPaste"
    assert pcblib.layer_name(37) == "TopSolder"
    assert pcblib.layer_name(56) == "KeepOut"
    assert pcblib.layer_name(57) == "Mechanical1"
    assert pcblib.layer_name(72) == "Mechanical16"
    assert pcblib.layer_name(74) == "MultiLayer"
    assert pcblib.layer_name(200) == "Layer200"


# ------------------------------------------------------------- the real file

@pytest.mark.skipif(not _CARRIER.is_file(),
                    reason="the multiviewer carrier library is not on disk")
def test_the_carrier_library_parses_end_to_end():
    """Every footprint of a real Altium library, every record accounted for."""
    result = pcblib.read_pcblib(_CARRIER, limit=100000)
    assert result["total"] > 0 and result["count"] == result["total"]
    broken = [f["name"] for f in result["footprints"] if "parse_error" in f]
    assert not broken, broken
    assert result["incomplete"] == []
    assert all(f["complete"] for f in result["footprints"])
    unknown = {k for f in result["footprints"] for k in f["other_records"]}
    assert not unknown, f"record kinds the reader does not know: {unknown}"
    pads = sum(f["counts"]["pads"] for f in result["footprints"])
    assert pads > 0
    for f in result["footprints"]:
        for pad in f["pads"]:
            assert pad["shape"] in ("round", "rectangular", "octagonal",
                                    "roundrect"), pad
            assert pad["layer"] in ("TopLayer", "BottomLayer",
                                    "MultiLayer"), pad


# ---------------------------------------------------------------- the tool

def _tool():
    from eda_agent.tools.library import register_library_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    return captured["lib_read_pcblib_file"]


def test_the_tool_refuses_a_missing_or_foreign_file(tmp_path):
    tool = _tool()
    out = asyncio.run(tool(library_path=str(tmp_path / "nope.PcbLib")))
    assert out["ok"] is False and "no file" in out["reason"]
    junk = tmp_path / "junk.PcbLib"
    junk.write_bytes(b"not an OLE file at all")
    out = asyncio.run(tool(library_path=str(junk)))
    assert out["ok"] is False and "OLE" in out["reason"]
    out = asyncio.run(tool(library_path=""))
    assert out["ok"] is False


@pytest.mark.skipif(not _CARRIER.is_file(),
                    reason="the multiviewer carrier library is not on disk")
def test_the_tool_reads_one_footprint_or_names_the_closest():
    tool = _tool()
    listing = asyncio.run(tool(library_path=str(_CARRIER), limit=2))
    assert listing["ok"] is True and listing["count"] <= 2
    name = listing["footprints"][0]["name"]
    one = asyncio.run(tool(library_path=str(_CARRIER),
                           footprint_name=name.lower()))
    assert one["ok"] is True and one["found"] is True
    assert one["footprints"][0]["name"] == name
    miss = asyncio.run(tool(library_path=str(_CARRIER),
                            footprint_name=name + "_NOT_THERE"))
    assert miss["ok"] is False and miss["found"] is False
    assert name in miss["closest"]


def test_the_tool_is_offline_and_read_only():
    from eda_agent.tools.metadata import tool_metadata

    meta = tool_metadata("lib_read_pcblib_file")
    assert meta["maturity"] == "offline"
    assert meta["interaction"] == "readonly"
    assert meta["category"] == "library"
