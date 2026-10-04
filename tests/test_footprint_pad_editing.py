# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Pad editing inside a PcbLib footprint, and the through-hole roundrect fix.

Before ``lib_modify_footprint_pads`` / ``lib_delete_footprint_pads`` the
only way to change ONE pad of a saved footprint was to edit the .PcbLib
by hand. MEASURED on a real carrier library (2026-10-03): three shape
bytes per pad were patched in the OLE stream with the library closed,
because the only library-scoped delete takes every pad on a layer, and
deleting and re-adding pads loses per-pad overrides.

And a header's pin 1, asked for as roundrect, read back as a plain round
pad from both the saved file and ``lib_get_pad_geometry``: the add
handlers wrote the shape with ``SetState_StackShapeOnLayer(Pad.Layer,
...)``, and for a drilled pad Pad.Layer is eMultiLayer, which is not a
pad-stack entry, so Altium ignored it without raising.

The Pascal cannot run here, so the Pascal half is SOURCE guards on the
calls whose absence caused the measured failures; the Python half is
driven.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import load
from tests.test_cross_validate import get_batch_field, next_batch_op

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = ROOT / "scripts" / "altium" / "Library.pas"


@pytest.fixture(scope="module")
def pas() -> dict[str, str]:
    return load(LIBRARY_PAS, minimum=100)


def _ops(payload: str) -> list[str]:
    out, rest = [], payload
    while True:
        op, rest = next_batch_op(rest)
        if not op:
            return out
        out.append(op)


# ------------------------------------------------------------------ Pascal

def test_no_handler_writes_a_pad_shape_on_the_multilayer_entry(pas):
    """The measured bug, by its exact shape."""
    offenders = [name for name, body in pas.items()
                 if re.search(r"SetState_StackShapeOnLayer\(\s*Pad\.Layer\s*,"
                              r"\s*eRoundedRectangular", body)]
    assert not offenders, (
        f"{offenders} write roundrect on Pad.Layer, which for a drilled "
        f"pad is eMultiLayer: Altium ignores it and the pad stays round")


def test_both_add_handlers_route_roundrect_through_the_copper_writer(pas):
    for name in ("Lib_AddFootprintPad", "Lib_AddFootprintPads"):
        assert "SetPadCopperShape(" in pas[name], (
            f"{name} no longer sets roundrect on every copper layer")


def test_the_copper_writer_covers_the_layers_a_drilled_pad_has(pas):
    body = pas["SetPadCopperShape"]
    for needle in ("eTopLayer", "eBottomLayer", "eMidLayer1",
                   "LayerStack_V7"):
        assert needle in body, f"SetPadCopperShape lost {needle}"
    # And it reports what it saw rather than assuming.
    assert "'unconfirmed'" in body and "PadShapeWord(Pad.TopShape)" in body


def test_modify_brackets_every_edit_and_reads_it_back(pas):
    """Lib_SetDesignator measured that an unbracketed edit is dropped on
    save; a write that is not read back is a success nobody checked."""
    body = pas["Lib_ModifyFootprintPads"]
    begin = body.index("PCBM_BeginModify")
    end = body.index("PCBM_EndModify")
    assert begin < body.index("Pad.X :=") < end
    assert "AddChangedField(MismatchJson" in body
    assert "Target.RemovePCBObject" not in body


def test_modify_refuses_a_duplicate_designator_without_an_index(pas):
    assert "'AMBIGUOUS'" in pas["Lib_ModifyFootprintPads"]


def test_modify_reads_the_origin_only_after_a_verified_switch(pas):
    """x and y are relative to Footprint.X/Y, which is only the true frame
    for a CURRENT footprint: read off a non-current IPC footprint it put
    pin 1 at the origin (fpcheck.py, 2026-10-02), and making a footprint
    current relocates it. Mutation: delete the switch, or move the origin
    read above it."""
    body = pas["Lib_ModifyFootprintPads"]
    switch = body.index("PcbLib.CurrentComponent := Target")
    readback = body.index("PcbLib.CurrentComponent <> Target", switch)
    origin = body.index("FootprintOriginX(Target)")
    assert switch < readback < origin
    assert "'SELECT_FAILED'" in body


def test_delete_resolves_everything_before_removing_anything(pas):
    body = pas["Lib_DeleteFootprintPads"]
    assert body.index("SELECTOR_UNRESOLVED") < body.index("PCBServer.PreProcess")
    assert "'CONFIRM_REQUIRED'" in body


def test_delete_removes_while_the_iterator_is_open(pas):
    """Lib_DeleteFootprintPrimitives measured that a primitive is dangling
    once its GroupIterator is destroyed; removing it then killed the
    engine inside ScriptingSystem.DLL."""
    body = pas["Lib_DeleteFootprintPads"]
    remove = body.index("Target.RemovePCBObject(Prim)")
    destroy_after = body.index("Target.GroupIterator_Destroy(GrpIter)", remove)
    create_before = body.rindex("Target.GroupIterator_Create", 0, remove)
    between = body[create_before:remove]
    assert "GroupIterator_Destroy" not in between
    assert destroy_after > remove


def test_the_new_writers_say_the_save_is_deferred(pas):
    for name in ("Lib_ModifyFootprintPads", "Lib_DeleteFootprintPads",
                 "Lib_SetFootprintDescription"):
        assert "JsonBool('written_to_disk', False)" in pas[name], name
        assert "MarkDocDirtyByPath(" in pas[name], name


# ------------------------------------------------------------------ Python

def test_a_blank_designator_is_still_dropped_by_default():
    from eda_agent.tools import library as lib

    payload, skipped = lib._pads_payload([{"designator": "", "x": 0}])
    assert payload == "" and skipped == 1


@pytest.mark.parametrize("call", [
    lambda lib: lib._pads_payload([{"designator": "", "x": 1}],
                                  allow_blank=True),
    lambda lib: lib._pads_payload([{"designator": "", "x": 1,
                                    "allow_blank_designator": True}]),
    lambda lib: lib._pads_payload([{"designator": "", "x": 1,
                                    "hole_size": 40, "plated": False}]),
])
def test_a_blank_designator_survives_when_asked_for(call):
    from eda_agent.tools import library as lib

    payload, skipped = call(lib)
    assert skipped == 0
    (op,) = _ops(payload)
    assert get_batch_field(op, "designator") == ""
    assert get_batch_field(op, "x") == "1"


def test_edit_payload_keeps_sub_mil_values_and_the_selector():
    from eda_agent.tools import library as lib

    payload, problems = lib._pad_edits_payload([
        {"designator": "1", "index": 1, "x": -12.34567, "shape": "RoundRect",
         "corner_radius": 30, "mask_expansion": "rule"},
    ])
    assert problems == []
    (op,) = _ops(payload)
    assert get_batch_field(op, "designator") == "1"
    assert get_batch_field(op, "index") == "1"
    assert get_batch_field(op, "x") == "-12.3457"
    assert get_batch_field(op, "shape") == "roundrect"
    assert get_batch_field(op, "mask_expansion") == "rule"


def test_a_hostile_designator_cannot_add_a_field():
    from eda_agent.tools import library as lib

    payload, problems = lib._pad_edits_payload([
        {"designator": "1;x=999~~designator=2", "y": 5},
    ])
    assert problems == []
    ops = _ops(payload)
    assert len(ops) == 1, "a '~~' in the designator forged a second edit"
    assert get_batch_field(ops[0], "x") == ""


@pytest.mark.parametrize("edit,fragment", [
    ({"x": 1}, "designator is required"),
    ({"designator": "1"}, "changes nothing"),
    ({"designator": "1", "shape": "blob"}, "unknown shape"),
    ({"designator": "1", "x": True}, "x must be a number"),
    ({"designator": "1", "x_size": 0}, "greater than zero"),
    ({"designator": "1", "corner_radius": 120}, "0 to 100"),
    ({"designator": "1", "hole_type": "slot"}, "needs slot_length"),
    ({"designator": "1", "new_designator": ""}, "allow_blank_designator"),
    ({"designator": "1", "colour": "red"}, "unknown key"),
    ({"designator": "1", "index": -1, "x": 0}, "index must be"),
])
def test_a_bad_edit_refuses_the_whole_call(edit, fragment):
    from eda_agent.tools import library as lib

    payload, problems = lib._pad_edits_payload([{"designator": "2", "y": 1},
                                                edit])
    assert problems and fragment in problems[0], problems


def test_clearing_a_designator_is_explicit_on_the_wire():
    from eda_agent.tools import library as lib

    payload, problems = lib._pad_edits_payload(
        [{"designator": "FID1", "new_designator": ""}], allow_blank=True)
    assert problems == []
    (op,) = _ops(payload)
    assert get_batch_field(op, "clear_designator") == "true"
    assert get_batch_field(op, "new_designator") == ""


def test_the_same_pad_twice_is_refused():
    from eda_agent.tools import library as lib

    _payload, problems = lib._pad_edits_payload([
        {"designator": "1", "x": 0}, {"designator": "1", "y": 0}])
    assert problems and "merge" in problems[0]


def test_delete_selectors_refuse_index_with_all():
    from eda_agent.tools import library as lib

    _p, problems = lib._pad_selectors_payload(
        [{"designator": "1", "index": 0, "all": True}])
    assert problems and "exclusive" in problems[0]
    payload, problems = lib._pad_selectors_payload(
        [{"designator": "", "all": True}, {"designator": "3", "index": 2}])
    assert problems == []
    first, second = _ops(payload)
    assert get_batch_field(first, "all") == "true"
    assert get_batch_field(second, "index") == "2"


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        return {"success": True}


def _tools(monkeypatch, bridge):
    from eda_agent.tools import library as lib

    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    lib.register_library_tools(_Mcp())
    return captured


def test_delete_needs_confirm_before_anything_is_sent(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_delete_footprint_pads"]
    out = asyncio.run(tool(footprint_name="FP", pads=[{"designator": "1"}]))
    assert out["ok"] is False and bridge.calls == []
    asyncio.run(tool(footprint_name="FP", pads=[{"designator": "1"}],
                     confirm=True))
    assert bridge.calls[0][0] == "library.delete_footprint_pads"
    assert bridge.calls[0][1]["confirm"] == "true"


def test_a_refused_edit_sends_nothing(monkeypatch):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_modify_footprint_pads"]
    out = asyncio.run(tool(footprint_name="FP",
                           edits=[{"designator": "1", "shape": "blob"}]))
    assert out["ok"] is False and bridge.calls == []


def test_bulk_descriptions_go_through_a_batch_file(monkeypatch, tmp_path):
    from eda_agent.tools import library as lib

    class _Cfg:
        workspace_dir = tmp_path

        def ensure_workspace(self):
            return None

    monkeypatch.setattr(lib, "get_config", lambda: _Cfg())
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_set_footprint_description"]
    asyncio.run(tool(descriptions={"R_0402": "Resistor 0.50mm",
                                   "C_0603": ""}))
    command, params = bridge.calls[0]
    assert command == "library.set_footprint_description"
    raw = Path(params["edits_path"]).read_bytes()
    assert b"\r" not in raw, "a CR would end up inside a description"
    assert raw.split(b"\n")[0] == b"R_0402\tResistor 0.50mm"


def test_a_description_with_a_tab_is_refused(monkeypatch, tmp_path):
    bridge = _Bridge()
    tool = _tools(monkeypatch, bridge)["lib_set_footprint_description"]
    out = asyncio.run(tool(descriptions={"R": "a\tb"}))
    assert out["ok"] is False and bridge.calls == []
