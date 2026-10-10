# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Headless .PcbLib reader: what a SAVED footprint library holds on disk.

WHY IT EXISTS. Every library edit the bridge makes is checked by reading it
back through Altium, and Altium answers from MEMORY. Measured 2026-10-04 on
the multiviewer carrier library: a pad deleted through the bridge read back
as gone (still_listed 0) while the next save wrote it to the file again, and
a 3D body whose position the bridge's own self-check passed was stored
50,000 mil from its footprint. Only the file said so, and reading the file
took a hand-written olefile script. This module is that script made a
library: the persistence proof for lib_delete_footprint_pads,
lib_delete_footprint_primitives, lib_modify_footprint_pads and
lib_link_3d_model, with no Altium involved.

THE FORMAT, as far as it is read here. A .PcbLib is an OLE compound file
with one storage per footprint. Each storage holds a ``Parameters`` stream
(4-byte length, then ``|KEY=VALUE|...``; ``PATTERN`` is the footprint name)
and a ``Data`` stream: a 4-byte header length and header, then records, each
one TYPE byte followed by length-prefixed blocks (uint32 length, then the
bytes). Block counts: pad (type 2) six, text (type 5) TWO, every other kind
one. A parser that treats a text as one block desyncs on everything after
it. Coordinates are int32 in 1/10000 mil, RELATIVE TO THE FOOTPRINT ORIGIN.

  pad, block 0     pascal string: the designator
  pad, block 4     layer @0; x, y @13; x_size, y_size @21; hole @45;
                   shape @49 (1 round, 2 rect, 3 octagonal); rotation
                   double @52; plated @60; paste expansion @86 used when
                   byte @101 == 2 (manual); mask expansion @90 when @102 == 2
  pad, block 5     (when >= 596 bytes) hole type @262 (0 round, 1 square,
                   2 slot); slot length @263; hole rotation double @267;
                   alternate shape @532 (9 = roundrect); corner % @564
  track            layer @0; x1, y1, x2, y2 @13; width @29
  arc              layer @0; centre @13; radius @21; start, end angle
                   doubles @25, @33; width @41
  via              x, y @13; diameter @21; hole @25
  fill             layer @0; x1, y1, x2, y2 @13; rotation double @29
  text, block 0    layer @0; x, y @13; height @21; rotation double @27;
                   stroke width @36
  text, block 1    pascal string: the text
  region, body     layer @0; uint32 property-string length @18, then the
                   latin-1 ``|KEY=value|`` string; then uint32 vertex count
                   and that many (x, y) doubles
  region, fill     keep-out flag = byte @2 (the record's second flag byte)
                   == 2; a keep-out region also carries
                   ``KEEPOUTRESTRICTIONS`` in its property string

THE KEEP-OUT FLAG IS IN THE HEADER, NOT THE PROPERTY STRING. This reader
used to look for an ``ISKEEPOUT`` property, which Altium does not write, so
every keep-out region read back as ``is_keepout`` False. Measured
2026-10-08: the carrier library's QSFP28 cage footprint holds four Top-layer
keep-out regions (header ``01 0c 02 ...``, ``KEEPOUTRESTRICTIONS=31``),
and every region in the Altium 26 example boards that is NOT a keep-out
(about 450 of them, copper and cutout alike) has that byte 0 and no
``KEEPOUTRESTRICTIONS``. KiCad's Altium importer decodes the same byte the
same way (``is_keepout = flags2 == 2``).

UNKNOWN RECORD TYPES STOP THE PARSE. The block count is known only for the
eight kinds above (types 1-6, 11, 12). Guessing "one block" for any other
type would read every later record from the wrong offset if the guess were
wrong, which can silently drop or invent pads in the one tool meant to
prove what the file holds. So the first unknown type ends that footprint's
parse: ``complete`` is False and ``parse_error`` names the type and byte
offset, and the records before it are kept.

The pad layout follows the Altium pad record as decoded by KiCad's importer
and was checked against the carrier library (hardware/tools/fpcheck.py in
the multiviewer repository, which this generalises). The track, fill, text,
region and body offsets were checked against the same file on 2026-10-04.

Layer bytes: 1 TopLayer, 2-31 MidLayer1-30, 32 BottomLayer, 33/34 Top/Bottom
Overlay, 35/36 Top/Bottom Paste, 37/38 Top/Bottom Solder, 39-54
InternalPlane1-16, 55 DrillGuide, 56 KeepOut, 57-72 Mechanical1-16, 73
DrillDrawing, 74 MultiLayer. Mechanical layers above 16 do not fit a byte;
regions and bodies carry their layer by NAME (``V7_LAYER``, e.g.
``TOPSOLDER``, ``MECHANICAL13``), which is used when present and mapped to
the same spelling the byte names use (``TopSolder``, ``Mechanical13``), so
a layer compares equal across kinds; the stored name is kept as
``layer_v7``. An unmapped byte is reported as ``Layer<n>``, not guessed.
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any, Optional

#: Pad top shape byte.
_SHAPE = {1: "round", 2: "rectangular", 3: "octagonal"}
_HOLE = {0: "round", 1: "square", 2: "slot"}
_MODEL_TYPE = {0: "extruded", 1: "generic", 2: "cylinder", 3: "sphere"}
_RECORD_NAME = {1: "arc", 2: "pad", 3: "via", 4: "track", 5: "text",
                6: "fill", 11: "region", 12: "component_body"}
#: Blocks per record, for the kinds whose layout is known. Any other type
#: stops the parse (see the module docstring).
_BLOCKS = {1: 1, 2: 6, 3: 1, 4: 1, 5: 2, 6: 1, 11: 1, 12: 1}
_UNITS = 1e4                    # file integers are 1/10000 mil


def layer_name(code: int) -> str:
    """Name of a legacy layer byte (see the module docstring)."""
    fixed = {1: "TopLayer", 32: "BottomLayer", 33: "TopOverlay",
             34: "BottomOverlay", 35: "TopPaste", 36: "BottomPaste",
             37: "TopSolder", 38: "BottomSolder", 55: "DrillGuide",
             56: "KeepOut", 73: "DrillDrawing", 74: "MultiLayer"}
    if code in fixed:
        return fixed[code]
    if 2 <= code <= 31:
        return f"MidLayer{code - 1}"
    if 39 <= code <= 54:
        return f"InternalPlane{code - 38}"
    if 57 <= code <= 72:
        return f"Mechanical{code - 56}"
    return f"Layer{code}"


#: V7_LAYER names that do not follow the TOP/BOTTOM + suffix pattern.
_V7_FIXED = {"KEEPOUT": "KeepOut", "MULTILAYER": "MultiLayer",
             "DRILLGUIDE": "DrillGuide", "DRILLDRAWING": "DrillDrawing",
             "TOP": "TopLayer", "BOTTOM": "BottomLayer",
             "TOPLAYER": "TopLayer", "BOTTOMLAYER": "BottomLayer"}
_V7_SUFFIX = {"OVERLAY": "Overlay", "PASTE": "Paste", "SOLDER": "Solder",
              "LAYER": "Layer"}


def v7_layer_name(raw: Optional[str]) -> Optional[str]:
    """A region's or body's ``V7_LAYER`` in the byte names' spelling.

    ``TOPSOLDER`` -> ``TopSolder``, ``MECHANICAL13`` -> ``Mechanical13``,
    ``KEEPOUT`` -> ``KeepOut``, ``MULTILAYER`` -> ``MultiLayer``,
    ``MID1`` -> ``MidLayer1``, ``PLANE2`` -> ``InternalPlane2``. A name
    that fits none of those is returned as stored rather than guessed.
    """
    if raw is None:
        return None
    name = raw.strip().upper()
    if not name:
        return None
    if name in _V7_FIXED:
        return _V7_FIXED[name]
    for prefix, word in (("MECHANICAL", "Mechanical"),
                         ("INTERNALPLANE", "InternalPlane"),
                         ("PLANE", "InternalPlane"),
                         ("MIDLAYER", "MidLayer"), ("MID", "MidLayer")):
        tail = name[len(prefix):]
        if name.startswith(prefix) and tail.isdigit():
            return f"{word}{int(tail)}"
    for side, word in (("TOP", "Top"), ("BOTTOM", "Bottom")):
        tail = name[len(side):]
        if name.startswith(side) and tail in _V7_SUFFIX:
            return word + _V7_SUFFIX[tail]
    return raw.strip()


def _mil(raw: int) -> float:
    return round(raw / _UNITS, 4)


def _i32(buf: bytes, off: int) -> Optional[int]:
    if off + 4 > len(buf):
        return None
    return struct.unpack_from("<i", buf, off)[0]


def _f64(buf: bytes, off: int) -> Optional[float]:
    if off + 8 > len(buf):
        return None
    return round(struct.unpack_from("<d", buf, off)[0], 6)


def _pascal(buf: bytes) -> str:
    if not buf:
        return ""
    n = buf[0]
    return buf[1:1 + n].decode("latin-1")


def _props(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in text.split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            out[key.strip().upper()] = value
    return out


def _length_mils(value: Optional[str]) -> Optional[float]:
    """'-49989.5mil' / '1.2mm' / '3' -> mils, or None when unreadable."""
    if value is None:
        return None
    text = value.strip().lower()
    scale = 1.0
    if text.endswith("mil"):
        text = text[:-3]
    elif text.endswith("mm"):
        text, scale = text[:-2], 1000.0 / 25.4
    try:
        return round(float(text) * scale, 4)
    except ValueError:
        return None


def _float(value: Optional[str]) -> Optional[float]:
    try:
        return None if value is None else float(value.strip())
    except ValueError:
        return None


def records(data: bytes) -> tuple[list[tuple[int, list[bytes]]], Optional[str]]:
    """Split one footprint's Data stream into ``(type, blocks)`` records.

    Returns the records and, when the stream ends mid-record, a length
    runs past the end, or a record type whose block count is not known
    appears, an error string (the records before it are kept, nothing
    after it is read).
    """
    out: list[tuple[int, list[bytes]]] = []
    if len(data) < 4:
        return out, "Data stream shorter than its header length"
    header_len = struct.unpack_from("<I", data, 0)[0]
    i = 4 + header_len
    if i > len(data):
        return out, "header length runs past the end of the Data stream"
    while i < len(data):
        kind = data[i]
        count = _BLOCKS.get(kind)
        if count is None:
            return out, (f"unknown record type {kind} at byte {i}; the records "
                         f"after it were not parsed (its block count is not "
                         f"known, and a guess would read everything after it "
                         f"from the wrong offset)")
        i += 1
        blocks: list[bytes] = []
        for _ in range(count):
            if i + 4 > len(data):
                return out, (f"record type {kind} at byte {i - 1} ends "
                             f"before its block length")
            n = struct.unpack_from("<I", data, i)[0]
            i += 4
            if i + n > len(data):
                return out, (f"record type {kind}: a block of {n} bytes "
                             f"runs past the end of the stream")
            blocks.append(data[i:i + n])
            i += n
        out.append((kind, blocks))
    return out, None


def parse_pad(blocks: list[bytes]) -> dict[str, Any]:
    name = _pascal(blocks[0]) if blocks else ""
    m = blocks[4] if len(blocks) > 4 else b""
    s = blocks[5] if len(blocks) > 5 else b""
    pad: dict[str, Any] = {"designator": name}
    if len(m) < 61:
        pad["error"] = f"pad main block is {len(m)} bytes, expected >= 61"
        return pad
    x, y = _i32(m, 13), _i32(m, 17)
    xs, ys = _i32(m, 21), _i32(m, 25)
    hole = _i32(m, 45)
    pad.update({
        "x_mils": _mil(x), "y_mils": _mil(y),
        "x_size_mils": _mil(xs), "y_size_mils": _mil(ys),
        "shape": _SHAPE.get(m[49], f"?{m[49]}"),
        "corner_pct": None,
        "rotation": _f64(m, 52),
        "hole_mils": _mil(hole),
        "plated": bool(m[60]),
        "layer": layer_name(m[0]),
        "hole_type": "round",
        "slot_length_mils": None,
        "hole_rotation": None,
        "paste_expansion_mils": None,
        "mask_expansion_mils": None,
    })
    if len(m) >= 103:
        if m[101] == 2:
            pad["paste_expansion_mils"] = _mil(_i32(m, 86))
        if m[102] == 2:
            pad["mask_expansion_mils"] = _mil(_i32(m, 90))
    if len(s) >= 596:
        if s[532] == 9:
            pad["shape"] = "roundrect"
            pad["corner_pct"] = s[564]
        pad["hole_type"] = _HOLE.get(s[262], f"?{s[262]}")
        if s[262] == 2:
            pad["slot_length_mils"] = _mil(_i32(s, 263))
            pad["hole_rotation"] = _f64(s, 267)
    return pad


def parse_track(b: bytes) -> dict[str, Any]:
    return {"layer": layer_name(b[0]) if b else None,
            "x1_mils": _mil(_i32(b, 13) or 0), "y1_mils": _mil(_i32(b, 17) or 0),
            "x2_mils": _mil(_i32(b, 21) or 0), "y2_mils": _mil(_i32(b, 25) or 0),
            "width_mils": _mil(_i32(b, 29) or 0)}


def parse_arc(b: bytes) -> dict[str, Any]:
    return {"layer": layer_name(b[0]) if b else None,
            "x_mils": _mil(_i32(b, 13) or 0), "y_mils": _mil(_i32(b, 17) or 0),
            "radius_mils": _mil(_i32(b, 21) or 0),
            "start_angle": _f64(b, 25), "end_angle": _f64(b, 33),
            "width_mils": _mil(_i32(b, 41) or 0)}


def parse_via(b: bytes) -> dict[str, Any]:
    return {"x_mils": _mil(_i32(b, 13) or 0), "y_mils": _mil(_i32(b, 17) or 0),
            "diameter_mils": _mil(_i32(b, 21) or 0),
            "hole_mils": _mil(_i32(b, 25) or 0)}


#: The value of the second flag byte (offset 2) of a region or fill record
#: that marks it a keep-out. See the module docstring.
_KEEPOUT_FLAG = 2

#: Bits of ``KEEPOUTRESTRICTIONS``, in the order KiCad's Altium importer
#: decodes them. 31 (all five) is what Altium writes by default.
_KEEPOUT_BITS = ((0x01, "via"), (0x02, "track"), (0x04, "copper"),
                 (0x08, "smd_pad"), (0x10, "th_pad"))

#: What a keep-out restricts when its record carries no
#: ``KEEPOUTRESTRICTIONS`` at all (files older than the property): all five.
#: Altium treats the missing value that way and KiCad's importer reads it
#: with this default, so reporting "restricts nothing" would be wrong.
_KEEPOUT_DEFAULT = 0x1F


def _header_keepout(b: bytes) -> bool:
    """The keep-out flag of a region or fill record header."""
    return len(b) > 2 and b[2] == _KEEPOUT_FLAG


def _restrictions(props: dict[str, str]) -> Optional[int]:
    raw = props.get("KEEPOUTRESTRICTIONS")
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def keepout_restriction_names(value: Optional[int]) -> list[str]:
    """``KEEPOUTRESTRICTIONS`` as names (``31`` -> all five objects)."""
    if value is None:
        return []
    return [name for bit, name in _KEEPOUT_BITS if value & bit]


def parse_fill(b: bytes) -> dict[str, Any]:
    return {"layer": layer_name(b[0]) if b else None,
            "x1_mils": _mil(_i32(b, 13) or 0), "y1_mils": _mil(_i32(b, 17) or 0),
            "x2_mils": _mil(_i32(b, 21) or 0), "y2_mils": _mil(_i32(b, 25) or 0),
            "rotation": _f64(b, 29),
            "is_keepout": _header_keepout(b)}


def parse_text(blocks: list[bytes]) -> dict[str, Any]:
    b = blocks[0] if blocks else b""
    return {"text": _pascal(blocks[1]) if len(blocks) > 1 else "",
            "layer": layer_name(b[0]) if b else None,
            "x_mils": _mil(_i32(b, 13) or 0), "y_mils": _mil(_i32(b, 17) or 0),
            "height_mils": _mil(_i32(b, 21) or 0),
            "rotation": _f64(b, 27),
            "stroke_width_mils": _mil(_i32(b, 36) or 0)}


def _property_block(b: bytes) -> tuple[dict[str, str], list[tuple[float, float]]]:
    """The ``|KEY=value|`` string and the vertices of a region or body."""
    n = _i32(b, 18)
    if n is None or n < 0 or 22 + n > len(b):
        return {}, []
    text = b[22:22 + n].split(b"\x00", 1)[0].decode("latin-1")
    vertices: list[tuple[float, float]] = []
    at = 22 + n
    count = _i32(b, at)
    if count is not None and 0 <= count and at + 4 + 16 * count <= len(b):
        for k in range(count):
            vx, vy = struct.unpack_from("<dd", b, at + 4 + 16 * k)
            vertices.append((vx / _UNITS, vy / _UNITS))
    return _props(text), vertices


def _bbox(vertices: list[tuple[float, float]]) -> Optional[dict[str, float]]:
    if not vertices:
        return None
    xs = [v[0] for v in vertices]
    ys = [v[1] for v in vertices]
    return {"x1": round(min(xs), 4), "y1": round(min(ys), 4),
            "x2": round(max(xs), 4), "y2": round(max(ys), 4)}


def _prop_layer(props: dict[str, str], b: bytes) -> Optional[str]:
    named = v7_layer_name(props.get("V7_LAYER"))
    if named:
        return named
    return layer_name(b[0]) if b else None


def parse_region(b: bytes) -> dict[str, Any]:
    props, vertices = _property_block(b)
    restrictions = _restrictions(props)
    # ISKEEPOUT is honoured if a file ever carries it, but Altium writes the
    # flag in the header (byte 2); reading only the property is the bug
    # that reported every keep-out region as a plain one.
    keepout = (_header_keepout(b)
               or props.get("ISKEEPOUT", "").upper() == "TRUE")
    # A keep-out with no stored mask restricts everything, not nothing:
    # report the effective value and say it was defaulted.
    defaulted = keepout and restrictions is None
    if defaulted:
        restrictions = _KEEPOUT_DEFAULT
    return {"layer": _prop_layer(props, b),
            "layer_v7": props.get("V7_LAYER"),
            "kind": props.get("KIND"),
            "is_keepout": keepout,
            "keepout_restrictions": restrictions,
            "keepout_restrictions_defaulted": defaulted,
            "keepout_restricts": keepout_restriction_names(restrictions),
            "vertex_count": len(vertices),
            "bbox_mils": _bbox(vertices)}


def parse_body(b: bytes) -> dict[str, Any]:
    props, vertices = _property_block(b)
    type_id = None
    try:
        type_id = int(props.get("MODEL.MODELTYPE", ""))
    except ValueError:
        pass
    embed = props.get("MODEL.EMBED")
    return {
        "layer": _prop_layer(props, b),
        "layer_v7": props.get("V7_LAYER"),
        "standoff_height_mils": _length_mils(props.get("STANDOFFHEIGHT")),
        "overall_height_mils": _length_mils(props.get("OVERALLHEIGHT")),
        "outline_bbox_mils": _bbox(vertices),
        "model": {
            "name": props.get("MODEL.NAME", ""),
            "type": _MODEL_TYPE.get(type_id, "unknown") if type_id is not None
            else None,
            "type_id": type_id,
            "x_mils": _length_mils(props.get("MODEL.2D.X")),
            "y_mils": _length_mils(props.get("MODEL.2D.Y")),
            "planar_rotation": _float(props.get("MODEL.2D.ROTATION")),
            "rot_x": _float(props.get("MODEL.3D.ROTX")),
            "rot_y": _float(props.get("MODEL.3D.ROTY")),
            "rot_z": _float(props.get("MODEL.3D.ROTZ")),
            "dz_mils": _length_mils(props.get("MODEL.3D.DZ")),
            "embedded": None if embed is None else embed.upper() == "TRUE",
        },
    }


def parse_footprint(data: bytes) -> dict[str, Any]:
    """Every primitive of one footprint's Data stream, by kind."""
    recs, error = records(data)
    out: dict[str, Any] = {"pads": [], "tracks": [], "arcs": [], "vias": [],
                           "fills": [], "texts": [], "regions": [],
                           "bodies": [], "other_records": {}}
    for kind, blocks in recs:
        first = blocks[0] if blocks else b""
        if kind == 2:
            out["pads"].append(parse_pad(blocks))
        elif kind == 4:
            out["tracks"].append(parse_track(first))
        elif kind == 1:
            out["arcs"].append(parse_arc(first))
        elif kind == 3:
            out["vias"].append(parse_via(first))
        elif kind == 6:
            out["fills"].append(parse_fill(first))
        elif kind == 5:
            out["texts"].append(parse_text(blocks))
        elif kind == 11:
            out["regions"].append(parse_region(first))
        elif kind == 12:
            out["bodies"].append(parse_body(first))
        else:                    # unreachable: records() stops on these
            key = str(kind)
            out["other_records"][key] = out["other_records"].get(key, 0) + 1
    out["complete"] = error is None
    if error:
        out["parse_error"] = error
    return out


def footprint_height(params_text: str) -> dict[str, Any]:
    """The footprint's HEIGHT from its Parameters string, mils and mm.

    Altium stores it as a length with its unit, ``|HEIGHT=130.7087mil|``
    (read from the carrier library 2026-10-10). ``height_raw`` is the
    stored string and the numeric fields are null when there is none or
    it cannot be read, so a missing height is never reported as 0.

    WHY IT IS HERE: lib_set_footprint_height and the 3D height sweep
    answer from Altium's memory, and heights were measured LOST on save
    (2026-10-08) before the switch-after-write recipe. This is the only
    read that proves a height reached the file.
    """
    raw = _props(params_text).get("HEIGHT")
    mils = _length_mils(raw)
    return {"height_raw": raw,
            "height_mils": mils,
            "height_mm": None if mils is None else round(mils * 0.0254, 4)}


def _parameters_text(ole, storage: str) -> str:
    """The latin-1 ``|KEY=VALUE|`` text of one storage's Parameters."""
    raw = ole.openstream([storage, "Parameters"]).read()[4:]
    return raw.split(b"\x00", 1)[0].decode("latin-1")


def footprint_storages(ole) -> dict[str, str]:
    """``{footprint name: storage name}`` from each storage's Parameters."""
    names: dict[str, str] = {}
    for entry in ole.listdir(streams=True):
        if len(entry) == 2 and entry[1] == "Parameters":
            raw = ole.openstream(entry).read()[4:]
            text = raw.split(b"\x00", 1)[0].decode("latin-1")
            pattern = _props(text).get("PATTERN") or entry[0]
            names[pattern] = entry[0]
    return names


def read_pcblib(path: str | Path, footprint_name: str = "", offset: int = 0,
                limit: int = 25) -> dict[str, Any]:
    """Read a saved .PcbLib. Raises ValueError on a file that is not one.

    With ``footprint_name``: that footprint only (case-insensitive exact
    match), or ``found`` False with the closest names. Without it: every
    footprint, paged by ``offset`` / ``limit`` in name order, ``total``
    being the library size.
    """
    import olefile

    path = str(path)
    if not olefile.isOleFile(path):
        raise ValueError(f"not an OLE compound file (not a .PcbLib?): {path}")
    ole = olefile.OleFileIO(path)
    try:
        storages = footprint_storages(ole)
        names = sorted(storages, key=str.lower)
        result: dict[str, Any] = {"library_path": path, "total": len(names),
                                  "units": "mils",
                                  "frame": "relative to each footprint's "
                                           "origin, as stored"}
        if footprint_name:
            wanted = footprint_name.strip().lower()
            chosen = [n for n in names if n.lower() == wanted]
            if not chosen:
                import difflib
                result["found"] = False
                result["closest"] = difflib.get_close_matches(
                    footprint_name, names, n=5, cutoff=0.5)
                result["footprints"] = []
                return result
            result["found"] = True
        else:
            offset = max(0, int(offset))
            limit = max(1, int(limit))
            chosen = names[offset:offset + limit]
            result["offset"] = offset
            result["count"] = len(chosen)
        footprints = []
        for name in chosen:
            storage = storages[name]
            try:
                height = footprint_height(_parameters_text(ole, storage))
            except OSError:
                height = footprint_height("")
            try:
                data = ole.openstream([storage, "Data"]).read()
            except OSError as exc:
                footprints.append({"name": name, "storage": storage,
                                   **height, "complete": False,
                                   "parse_error": f"no Data stream: {exc}"})
                continue
            entry = {"name": name, "storage": storage, **height}
            entry.update(parse_footprint(data))
            entry["counts"] = {k: len(entry[k]) for k in
                               ("pads", "tracks", "arcs", "vias", "fills",
                                "texts", "regions", "bodies")}
            footprints.append(entry)
        result["footprints"] = footprints
        result["incomplete"] = [f["name"] for f in footprints
                                if not f.get("complete")]
        return result
    finally:
        ole.close()
