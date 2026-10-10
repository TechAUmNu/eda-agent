# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_read_pcblib_file reports each footprint's HEIGHT as the file has it.

FIELD STORY, 2026-10-10 (bridge 2026.10.10.01, AD 26.10.1.5). Step 37.2
set three footprint heights with lib_set_footprint_height, saved, and
needed to prove the heights reached the file. The bridge's own read-backs
answer from Altium's memory, which is exactly what was measured wrong on
2026-10-08 (heights read back set and were gone after save + reload). The
offline reader was the one tool that answers from disk, and it did not
report the height at all: it read every pad, track and body of the
footprint and skipped the one field the step needed. The proof had to be
done by hand with olefile.

Altium keeps the height in the footprint's Parameters stream as a length
with its unit (``|HEIGHT=130.7087mil|``, read from the carrier library).
Each test here fails on eec334c, where read_pcblib has no height fields.
"""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from eda_agent.fileio import altium_pcblib as pcblib

_CARRIER = Path(r"H:/git/multiviewer/hardware/multiviewer/carrier.PcbLib")


def _params(text: str) -> bytes:
    body = text.encode("latin-1") + b"\x00"
    return struct.pack("<I", len(body)) + body


def _data() -> bytes:
    header = b"\x07ZZ_TEST"
    return struct.pack("<I", len(header)) + header


class _FakeOle:
    """The three calls read_pcblib makes on an olefile.OleFileIO."""

    def __init__(self, streams: dict):
        self._streams = streams

    def listdir(self, streams=True):
        return [list(k) for k in self._streams]

    def openstream(self, entry):
        key = tuple(entry)
        if key not in self._streams:
            raise OSError(f"no stream {key}")

        class _S:
            def __init__(self, b):
                self._b = b

            def read(self):
                return self._b
        return _S(self._streams[key])

    def close(self):
        pass


@pytest.fixture
def fake_library(monkeypatch):
    import olefile
    streams = {
        ("A", "Parameters"): _params("|PATTERN=TALL|HEIGHT=1023.622mil|"
                                     "DESCRIPTION=jack|"),
        ("A", "Data"): _data(),
        ("B", "Parameters"): _params("|PATTERN=FLAT|HEIGHT=0mil|"),
        ("B", "Data"): _data(),
        ("C", "Parameters"): _params("|PATTERN=METRIC|HEIGHT=1.2mm|"),
        ("C", "Data"): _data(),
        ("D", "Parameters"): _params("|PATTERN=NONE|DESCRIPTION=x|"),
        ("D", "Data"): _data(),
    }
    monkeypatch.setattr(olefile, "isOleFile", lambda p: True)
    monkeypatch.setattr(olefile, "OleFileIO", lambda p: _FakeOle(streams))
    return "fake.PcbLib"


def test_the_step_37_2_proof_reads_the_height_off_disk(fake_library):
    """The read 37.2 needed: one footprint, its stored height."""
    one = pcblib.read_pcblib(fake_library, footprint_name="TALL")
    fp, = one["footprints"]
    assert fp["height_raw"] == "1023.622mil"
    assert fp["height_mils"] == pytest.approx(1023.622)
    assert fp["height_mm"] == pytest.approx(26.0, abs=1e-3)


def test_every_footprint_of_a_page_carries_its_height(fake_library):
    page = pcblib.read_pcblib(fake_library, limit=10)
    got = {f["name"]: f["height_mm"] for f in page["footprints"]}
    assert got["FLAT"] == 0.0
    assert got["METRIC"] == pytest.approx(1.2)


def test_a_missing_height_is_null_not_zero(fake_library):
    """A footprint whose file holds no HEIGHT must not read as 0 mm: 0
    disables the placement-collision rule, and a proof that says 0 for
    "absent" would pass a height that never reached the file."""
    fp, = pcblib.read_pcblib(fake_library, footprint_name="NONE")["footprints"]
    assert fp["height_raw"] is None
    assert fp["height_mils"] is None and fp["height_mm"] is None


def test_the_tool_docstring_names_the_height_fields():
    text = (Path(pcblib.__file__).parents[1] / "tools" / "library.py"
            ).read_text(encoding="utf-8")
    start = text.index("async def lib_read_pcblib_file(")
    doc = text[start:text.index('"""', text.index('"""', start) + 3)]
    assert "height_mm" in doc and "height_mils" in doc
    assert "lib_set_footprint_height" in doc


@pytest.mark.skipif(not _CARRIER.is_file(),
                    reason="multiviewer carrier library not present")
def test_the_carrier_library_heights_read_as_stored():
    """The real file: the SoM mezzanine connector stores 130.7087mil."""
    one = pcblib.read_pcblib(_CARRIER,
                             footprint_name="ADM6-60-01_5-L-4-2-A-TR")
    fp, = one["footprints"]
    assert fp["height_raw"].endswith("mil")
    assert fp["height_mm"] == pytest.approx(fp["height_mils"] * 0.0254,
                                            abs=1e-3)
