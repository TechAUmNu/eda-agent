# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A height set by lib_set_footprint_height must be in the file after a save.

Field result, 2026-10-10, bridge 2026.10.08.01, the 3D-link pass on the
multiviewer carrier library (55 footprints). Each ``lib_set_footprint_
height`` reply said ``changed: true``. After ``lib_save_library``, 51 of
the 55 HEIGHT values were NOT in the file. The four that survived had been
the library's current footprint; re-running with every footprint made
current again before the save kept all 55.

The handler set ``Target.Height`` on whatever the library walk found,
then put the previous current footprint back, and never marked the
library document modified. The library board holds the CURRENT footprint
and the save writes what it holds, so only an edit to the footprint that
was already current could reach the file, and the reply could not tell
the two apart.

It now does the measured recipe itself: bracketed write, read back, make
the footprint current AFTER the write (verified), restore the caller's
current footprint, mark the library document modified, and reply with the
house deferred-write fields (``pending_save``, ``written_to_disk``,
``marked_modified``) plus ``made_current_after_write`` so a caller can see
when the recipe did not take.

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
    assert "If Changed Then MarkDocDirtyByPath(LibFile);" in after, (
        "nothing marked the library modified, so a save could decline it")
    assert "Marked := DocIsModified(LibFile);" in after
    assert after.index("MarkDocDirtyByPath(LibFile)") < \
        after.index("DocIsModified(LibFile)")


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


def test_the_reply_carries_the_deferred_write_fields():
    body = _body()
    for field in ("JsonBool('pending_save', Changed)",
                  "JsonBool('written_to_disk', False)",
                  "JsonBool('marked_modified', Marked)",
                  "JsonBool('made_current_after_write', MadeCurrent)",
                  "JsonBool('current_restored', Restored)",
                  "JsonFloat('read_back_height_mm', CoordToMM(GotH))",
                  "JsonBool('saved', False)"):
        assert field in body, field
    assert "lib_save_library" in body, (
        "the save note must name the per-library save, not Altium's menu")


def test_a_recipe_that_did_not_take_is_said_in_the_save_note():
    body = _body()
    assert "Else If Not MadeCurrent Then" in body
    assert "Else If Not Marked Then" in body


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
                  "made_current_after_write", "lib_save_library"):
        assert field in doc, field

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
