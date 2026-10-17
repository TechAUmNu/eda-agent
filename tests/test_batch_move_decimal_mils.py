"""pcb.batch_move_components must not read a decimal coordinate as 0.

2026-10-17: x/y went through StrToIntDef(..., 0), so a move to 7260.6 mils
landed the part at x = 0 and was still counted as applied.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "PCB.pas"


def _body():
    s = SRC.read_text(encoding="utf-8")
    i = s.index("Function PCB_BatchMoveComponents(")
    return s[i:s.index("{ PCB_GetTraceLengths", i)]


def test_coordinates_are_not_parsed_as_integers():
    b = _body()
    assert not re.search(r"StrToIntDef\((XStr|YStr)", b)
    assert "NewX, NewY : Double;" in b


def test_a_non_number_fails_its_entry_by_name():
    b = _body()
    assert "IsFloatStr(XStr)" in b and "IsFloatStr(YStr)" in b
    assert "bad_entries" in b
