# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A footprint delete removes while another footprint is current.

The current footprint's primitives live on the library board, which is
written back into the footprint at the next switch. Measured on AD 26.10:
removing from the current footprint reported removed 6 and all of them
returned when the editor switched away and back, and Board.RemovePCBObject
did nothing at all. Removing while another footprint was current lasted
through switches, save and reopen, with the primitive freed, for 25 cycles.
"""

from __future__ import annotations

from pathlib import Path

LIBRARY = Path(__file__).resolve().parents[1] / "scripts" / "altium" / "Library.pas"


def _body(name: str) -> str:
    text = LIBRARY.read_text(encoding="utf-8-sig")
    start = text.index(f"Function {name}(")
    return text[start:text.index("\nEnd;\n", start)]


# MERGE NOTE (2026.10.13): upstream parked the target unconditionally and
# freed each removed primitive with DestroyPCBObject. The merge kept
# carrier-local's park as OPT-IN through park_current (which the Python side
# sends by default whenever remove_from_board is off, tools/library.py
# _park_choice), parking on another footprint first (LibParkCurrentElsewhere)
# and on upstream's FootprintToHoldFocus holder only when none exists. The
# primitive is removed, never destroyed, by the delete. These tests pin the
# same measured property (remove while ANOTHER footprint is current) against
# that structure.

def test_the_target_is_moved_off_screen_before_anything_is_removed():
    body = _body("Lib_DeleteFootprintPrimitives")
    assert "ExtractJsonValue(Params, 'park_current')" in body
    read = body.index("WasCurrent := TargetWasCurrent")
    park = body.index("Parked := LibParkCurrentElsewhere(PcbLib, Target)")
    hold = body.index("Holder := FootprintToHoldFocus(PcbLib, Target, TempMade)")
    switch = body.index("Parked := LibMakeCurrentVerified(PcbLib, Holder)")
    assert body.index("TargetWasCurrent := (PcbLib.CurrentComponent = Target)") < read
    assert read < park < hold < switch
    assert switch < body.index("PCBServer.PreProcess")
    assert switch < body.index("Target.RemovePCBObject(Prim)")


def test_park_current_is_sent_by_default_from_python():
    from eda_agent.tools.library import _park_choice
    assert _park_choice(None, False) == (True, None)
    assert _park_choice(None, True) == (False, None)


def test_each_removed_primitive_is_removed_inside_its_open_iterator():
    body = _body("Lib_DeleteFootprintPrimitives")
    remove = body.index("Target.RemovePCBObject(Prim)")
    assert remove < body.index("Target.GroupIterator_Destroy(GrpIter)", remove)
    assert "PCBServer.DestroyPCBObject(Prim)" not in body


def test_the_target_is_shown_again_and_a_temporary_holder_removed():
    body = _body("Lib_DeleteFootprintPrimitives")
    tail = body[body.index("PCBServer.PostProcess"):]
    gate = tail.index("If TempMade And (Holder <> Nil) Then")
    restore = tail.index("Restored := LibMakeCurrentVerified(PcbLib, Target)")
    assert gate < restore
    cleanup = tail[restore:]
    assert cleanup.index("If Restored Then") < cleanup.index("PcbLib.RemoveComponent(Holder)")
    for call in ("PcbLib.RemoveComponent(Holder)", "PcbLib.DeRegisterComponent(Holder)",
                 "PCBServer.DestroyPCBLibComp(Holder)"):
        assert call in cleanup, call
    assert '"was_current":' in body


def test_the_holder_is_another_footprint_or_a_temporary_one():
    body = _body("FootprintToHoldFocus")
    assert "Footprint.I_ObjectAddress <> Target.I_ObjectAddress" in body
    assert "PcbLib.RegisterComponent(Result)" in body
    assert "TempMade := True" in body
