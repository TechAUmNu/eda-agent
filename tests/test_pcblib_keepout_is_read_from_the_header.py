# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A keep-out region in a saved .PcbLib reads back as a keep-out.

Field report, 2026-10-08, the multiviewer carrier library: the QSFP28 cage
footprint ``QSFP28_CAGE_1x1_PF-10_TE2170704`` was given four Top-layer
keep-out regions, saved, and read back through ``lib_read_pcblib_file`` as
four ordinary copper regions, ``is_keepout`` False on every one. The tool
that exists to prove what the FILE holds denied the one property the edit
was made for.

The reader looked for an ``ISKEEPOUT`` property. Altium does not write one:
the flag lives in the record HEADER, the second flag byte (offset 2), which
is 2 on a keep-out and 0 otherwise, and a keep-out also carries
``KEEPOUTRESTRICTIONS`` (31 = vias, tracks, copper, SMD and TH pads) in its
property string. A scratch reader written during that session read the
header byte and got it right; this pins the same decode on the real bytes.

``_REAL_KEEPOUT_REGION`` is one of those four records, copied byte for byte
out of the saved carrier library (403 bytes: header, property string, 15
vertices). It is the evidence, so it is embedded rather than synthesised;
the non-keep-out counterpart is the same record with only the flag byte
cleared, which is how every one of about 450 ordinary regions in Altium's
own example boards is stored (byte 2 = 0, no ``KEEPOUTRESTRICTIONS``).
"""
from __future__ import annotations

import struct

from eda_agent.fileio import altium_pcblib as pcblib

_REAL_KEEPOUT_REGION = bytes.fromhex(
    "010c02ffffffffffffffffffff00000000008900000056375f4c415945523d544f"
    "507c4e414d453d207c4b494e443d307c535542504f4c59494e4445583d2d317c55"
    "4e494f4e494e4445583d307c4152435245534f4c5554494f4e3d302e356d696c7c"
    "4953534841504542415345443d46414c53457c4341564954594845494748543d30"
    "6d696c7c4b4545504f55545245535452494354494f4e533d3331000f0000000000"
    "0080278643c1000000c012c2524100000080278643c1000000008c425441000000"
    "80eb4d41c1000000008c42544100000000e07e41c1000000400d2a544100000000"
    "c6a641c100000080be0d544100000000a2c941c100000080d1ef53410000008030"
    "e741c1000000c07fd05341000000003aff41c10000004005b05341000000808f11"
    "42c1000000c09f8e5341000000000e1e42c1000000c08f6c5341000000009e2442"
    "c100000000164a534100000080322542c1000000c07427534100000000cb1f42c1"
    "00000040ee04534100000080711442c100000080c4e2524100000000a0fd41c100"
    "0000c012c25241")


def _stream(*records: bytes) -> bytes:
    header = b"\x07ZZ_TEST"
    return struct.pack("<I", len(header)) + header + b"".join(records)


def _region_record(body: bytes) -> bytes:
    return b"\x0b" + struct.pack("<I", len(body)) + body


def test_the_fixture_is_the_record_the_field_report_was_about():
    """Guard the guard: a fixture edited into something else proves
    nothing about the carrier library."""
    assert len(_REAL_KEEPOUT_REGION) == 403
    assert _REAL_KEEPOUT_REGION[:3] == b"\x01\x0c\x02"
    assert b"KEEPOUTRESTRICTIONS=31" in _REAL_KEEPOUT_REGION
    assert b"ISKEEPOUT" not in _REAL_KEEPOUT_REGION.replace(
        b"KEEPOUTRESTRICTIONS", b""), (
        "Altium wrote no ISKEEPOUT property; that absence is the bug")


def test_a_saved_keepout_region_reads_as_a_keepout():
    fp = pcblib.parse_footprint(_stream(_region_record(_REAL_KEEPOUT_REGION)))
    assert fp["complete"] is True
    region = fp["regions"][0]
    assert region["is_keepout"] is True
    assert region["keepout_restrictions"] == 31
    assert region["keepout_restricts"] == ["via", "track", "copper",
                                           "smd_pad", "th_pad"]
    assert region["layer"] == "TopLayer" and region["layer_v7"] == "TOP"
    assert region["kind"] == "0"
    assert region["vertex_count"] == 15
    assert region["bbox_mils"] == {"x1": -255.9055, "y1": 491.7323,
                                   "x2": -226.8119, "y2": 531.1024}


def test_the_same_record_without_the_header_flag_is_not_a_keepout():
    """Mutation: decide keep-out from KEEPOUTRESTRICTIONS alone, or from
    the layer. The header byte is the flag; clearing only it must flip
    the answer."""
    plain = bytearray(_REAL_KEEPOUT_REGION)
    plain[2] = 0
    region = pcblib.parse_footprint(
        _stream(_region_record(bytes(plain))))["regions"][0]
    assert region["is_keepout"] is False
    assert region["layer"] == "TopLayer"


def test_an_ordinary_region_has_no_restrictions():
    head = bytearray(18)
    head[0] = 37
    head[1] = 0x0C
    text = b"|V7_LAYER=TOPSOLDER|KIND=0|\x00"
    body = bytes(head) + struct.pack("<I", len(text)) + text
    body += struct.pack("<I", 0)
    region = pcblib.parse_footprint(_stream(_region_record(body)))["regions"][0]
    assert region["is_keepout"] is False
    assert region["keepout_restrictions"] is None
    assert region["keepout_restricts"] == []


def test_a_keepout_fill_reads_its_header_flag_too():
    """Fills share the region's header layout (KiCad decodes both the same
    way), so a keep-out fill written with ``keepout=true`` is provable from
    the file as well."""
    b = bytearray(50)
    b[0], b[1], b[2] = 1, 0x0C, 2
    fp = pcblib.parse_footprint(_stream(b"\x06" + struct.pack("<I", 50)
                                        + bytes(b)))
    assert fp["fills"][0]["is_keepout"] is True
    b[2] = 0
    fp = pcblib.parse_footprint(_stream(b"\x06" + struct.pack("<I", 50)
                                        + bytes(b)))
    assert fp["fills"][0]["is_keepout"] is False
