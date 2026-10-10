# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A height set by lib_set_footprint_height must be in the file after a save.

Field result, 2026-10-10, bridge 2026.10.08.01, the 3D-link pass on the
multiviewer carrier library (55 footprints). Each ``lib_set_footprint_
height`` reply said ``changed: true``. After ``lib_save_library``, 51 of
the 55 HEIGHT values were NOT in the file; re-running with every
footprint made current again before the save kept all 55. That is the
measurement. Which four survived was not recorded: probably the
footprints that were current at some point after their write.

The handler set ``Target.Height`` on whatever the library walk found,
then put the previous current footprint back, never switched to the
edited footprint, and never marked the library document modified. The
working hypothesis: the library board holds the CURRENT footprint and the
save writes what it holds, so a footprint never switched to after its edit
is not written.

It now does the measured recipe itself on EVERY path: bracketed write,
then the library switches TO the footprint (verified). A footprint that
was not current is made current and the caller's restored; one that was
ALREADY current (the default, empty ``footprint_name``) is parked on
another footprint and switched back, since making the current footprint
current is a no-op and the recipe would silently never run. The height is
read back AFTER the switches, so a switch that reloaded the stored copy
could not hide behind a pre-switch read. ``pending_save`` is true only for
a change made with the switch; ``made_current_after_write``,
``was_current`` and ``parked_elsewhere_first`` say which path ran.

The sweep ``lib_update_footprint_heights_from_3d`` writes the same way
without the switch; until RELEASE_VERIFICATION 37.2 measures it, its
reply says the heights are unconfirmed on save.

The Pascal cannot run here; it is read comment-free, per function.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_LIB = _ROOT / "scripts" / "altium" / "Library.pas"


def _body() -> str:
    return load(_LIB)["Lib_SetFootprintHeight"]


def _write_at(body: str) -> int:
    assert body.count("Target.Height := NewH;") == 1, (
        "expected exactly one height write")
    return body.index("Target.Height := NewH;")


def test_the_write_is_bracketed_like_every_other_footprint_edit():
    body = _body()
    write = _write_at(body)
    begin = body.rfind("PCBM_BeginModify", 0, write)
    end = body.find("PCBM_EndModify", write)
    assert begin >= 0 and end > write, (
        "the height write must sit between PCBM_BeginModify and "
        "PCBM_EndModify on the footprint")
    assert body.rfind("PCBServer.PreProcess", 0, write) >= 0
    assert body.find("PCBServer.PostProcess", write) > write


def test_the_footprint_is_made_current_after_the_write():
    """The step that made the field values reach the file."""
    body = _body()
    write = _write_at(body)
    after = body[write:]
    assert "LibMakeCurrentVerified(CurLib, Target)" in after, (
        "the footprint must be made current after its height is set, or "
        "the save drops the value (51 of 55 lost, 2026-10-10)")
    make = after.index("LibMakeCurrentVerified(CurLib, Target)")
    restore = after.index("LibMakeCurrentVerified(CurLib, SavedCurrent)")
    assert make < restore, "make it current first, then hand the old one back"

    text = _LIB.read_text(encoding="utf-8")
    assert text.index("Function LibMakeCurrentVerified(") < \
        text.index("Function Lib_SetFootprintHeight("), (
            "DelphiScript needs the callee declared first")


def test_restoring_the_old_current_footprint_is_no_longer_the_only_switch():
    """The old shape: write, then put SavedCurrent back unverified. That
    left the edited footprint never current after its edit."""
    body = _body()
    assert "If SavedCurrent <> Nil Then CurLib.CurrentComponent := " \
           "SavedCurrent;" not in body


def test_the_library_document_is_marked_and_read_back():
    body = _body()
    write = _write_at(body)
    after = body[write:]
    assert "LibFile := CurLib.Board.FileName;" in after
    # Since 2026.10.11.01 the mark returns its own read-back (the
    # 2026-10-10 field run read marked_modified false while the tab showed
    # '*', and the old mark could not say whether it found the document).
    assert "Marked := MarkDocDirty(LibFile);" in after, (
        "nothing marked the library modified, so a save could decline it")
    assert after.index("If Changed Then") < \
        after.index("Marked := MarkDocDirty(LibFile);")
    assert "Marked := DocIsModified(LibFile);" in after, (
        "an unchanged height still reports the flag as read")


def test_changed_comes_from_reading_the_height_back():
    """`changed` used to be OldH <> NewH: what was asked, not what is
    there."""
    body = _body()
    write = _write_at(body)
    after = body[write:]
    assert "GotH := Target.Height;" in after
    assert "Changed := GotH <> OldH;" in after
    assert "JsonBool('changed', OldH <> NewH)" not in body
    assert "JsonBool('changed', Changed)" in body


def test_the_read_that_counts_comes_after_the_switches():
    """Review: the first version read the height back BEFORE switching
    footprints. If a switch reloads the footprint from its stored copy,
    that reply says changed:true with the new value while the value is
    already gone. The reported height and `changed` come from a read
    after every LibMakeCurrentVerified; the pre-switch read is reported
    beside it."""
    body = _body()
    after = body[_write_at(body):]
    last_switch = after.rindex("LibMakeCurrentVerified(")
    read = after.index("GotH := Target.Height;")
    assert read > last_switch, "the height is read back before the switches"
    assert after.index("Changed := GotH <> OldH;") > read
    assert after.index("GotBefore := Target.Height;") < \
        after.index("LibMakeCurrentVerified(")
    assert "JsonFloat('read_back_before_switch_mm', CoordToMM(GotBefore))" \
        in body


def test_an_already_current_footprint_is_switched_away_and_back():
    """Review: with footprint_name empty (the default) the target IS the
    current footprint, LibMakeCurrentVerified does nothing, and the
    measured recipe (switch to the footprint after the write) never ran.
    Now the current footprint is parked on another one first and switched
    back, and made_current_after_write is true only when both happened."""
    body = _body()
    after = body[_write_at(body):]
    assert "WasCurrent := SavedCurrent = Target;" in after
    park = after.index("Parked := LibParkCurrentElsewhere(CurLib, Target);")
    back = after.index("Restored := LibMakeCurrentVerified(CurLib, Target);")
    assert after.index("If WasCurrent Then") < park < back
    assert "MadeCurrent := Parked And Restored;" in after, (
        "a make-current that was a no-op must not count as the switch")

    text = _LIB.read_text(encoding="utf-8")
    assert text.index("Function LibParkCurrentElsewhere(") < \
        text.index("Function Lib_SetFootprintHeight("), (
            "DelphiScript needs the callee declared first")


def test_pending_save_needs_the_switch():
    """A change without the switch is the case measured lost on save, so
    it is not 'an in-memory change a save will write'."""
    body = _body()
    assert "Pending := Changed And MadeCurrent;" in body
    assert "JsonBool('pending_save', Pending)" in body
    assert "JsonBool('pending_save', Changed)" not in body


def test_the_sweep_warns_that_its_heights_are_unconfirmed():
    """lib_update_footprint_heights_from_3d writes Height on non-current
    footprints and restores the current one: the pattern diagnosed above.
    Until 37.2 measures it, the reply must not imply the heights are
    ready to save."""
    sweep = load(_LIB)["Lib_UpdateFootprintHeightsFrom3D"]
    assert "JsonBool('heights_confirmed_on_save', False)" in sweep
    assert "JsonStr('save_note', SaveNote)" in sweep
    assert "UNCONFIRMED THAT THESE HEIGHTS REACH THE FILE" in sweep
    assert "lib_read_pcblib_file" in sweep


def test_the_reply_carries_the_deferred_write_fields():
    body = _body()
    for field in ("JsonBool('pending_save', Pending)",
                  "JsonBool('written_to_disk', False)",
                  "JsonBool('marked_modified', Marked)",
                  "JsonBool('made_current_after_write', MadeCurrent)",
                  "JsonBool('current_restored', Restored)",
                  "JsonBool('was_current', WasCurrent)",
                  "JsonBool('parked_elsewhere_first', Parked)",
                  "JsonFloat('read_back_height_mm', CoordToMM(GotH))",
                  "JsonBool('saved', False)"):
        assert field in body, field
    assert "lib_save_library" in body, (
        "the save note must name the per-library save, not Altium's menu")


def test_a_recipe_that_did_not_take_is_said_in_the_save_note():
    body = _body()
    assert "Else If Not MadeCurrent Then" in body
    assert "Else If Not Marked Then" in body
    assert "Else If WasCurrent And (Not Parked) Then" in body, (
        "a lone footprint that cannot be switched away from must say so")


def test_the_tool_docstring_names_the_new_fields(monkeypatch):
    from eda_agent.tools import library as library_module

    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(DummyMcp())
    doc = inspect.getdoc(captured["lib_set_footprint_height"]) or ""
    for field in ("pending_save", "written_to_disk",
                  "made_current_after_write", "lib_save_library",
                  "read_back_before_switch_mm", "was_current",
                  "parked_elsewhere_first"):
        assert field in doc, field
    sweep_doc = inspect.getdoc(
        captured["lib_update_footprint_heights_from_3d"]) or ""
    assert "heights_confirmed_on_save" in sweep_doc
    assert "UNCONFIRMED" in sweep_doc

    sent = []

    class _Bridge:
        async def send_command_async(self, command, params=None,
                                     timeout=None):
            sent.append((command, params))
            return {"ok": True}

    monkeypatch.setattr(library_module, "get_bridge", lambda: _Bridge())
    captured.clear()
    library_module.register_library_tools(DummyMcp())
    asyncio.run(captured["lib_set_footprint_height"](
        height_mm=2.5, footprint_name="SOT-23"))
    assert sent == [("library.set_footprint_height",
                     {"height_mm": 2.5, "footprint_name": "SOT-23"})]
