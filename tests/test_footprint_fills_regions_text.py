# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Footprint fills, regions, sub-mil tracks, and one text per call.

The library authoring tools could not do four things a real carrier
library needed, and each gap was being worked around by hand:

* stencil apertures, mask openings and keep-outs could not live in a
  footprint at all, so they became board-level notes
  (``lib_add_footprint_fills`` / ``lib_add_footprint_regions``);
* tracks and arcs quantised to whole mils, and a decimal did not even
  round: ``StrToIntDef`` returned the DEFAULT for "12.5";
* one ``lib_add_footprint_text`` call left two texts (measured
  2026-10-03);
* only the text tool could name its footprint.

These are SOURCE guards for the Pascal, which cannot run here, plus the
Python payload builders driven directly. Each guard pins the call or
literal whose absence was the defect, so reverting a fix fails here.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from tests.pascal_source import strip_comments

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = ROOT / "scripts" / "altium" / "Library.pas"


def _functions() -> dict[str, str]:
    """``{name: comment-free body}`` for every routine in Library.pas."""
    src = strip_comments(LIBRARY_PAS.read_text(encoding="utf-8",
                                               errors="replace"))
    starts = [(m.group(1), m.start()) for m in
              re.finditer(r"^(?:Function|Procedure)\s+(\w+)", src, re.M)]
    out = {}
    for i, (name, start) in enumerate(starts):
        end = starts[i + 1][1] if i + 1 < len(starts) else len(src)
        out[name] = src[start:end]
    return out


FNS = _functions()

#: Every handler that writes footprint geometry and must choose its
#: footprint through the shared resolver.
AUTHORING = ("Lib_AddFootprintTrack", "Lib_AddFootprintTracks",
             "Lib_AddFootprintArc", "Lib_AddFootprintText",
             "Lib_AddFootprintFills", "Lib_AddFootprintRegions")


def test_the_scan_sees_library_pas():
    assert len(FNS) > 80, "the routine split found almost nothing"
    for name in AUTHORING + ("ResolveLibFootprint", "RegisterLibPrimitive",
                             "Lib_SetCurrentFootprint"):
        assert name in FNS, f"{name} is gone from Library.pas"


# ---------------------------------------------------------------------------
# Registration: every footprint primitive reaches disk.
# ---------------------------------------------------------------------------

def _both_branch(body: str) -> str:
    """The ``Mode = 'both'`` branch of RegisterLibPrimitive, the default."""
    start = body.index("If Mode = 'both' Then")
    end = body.index("Else If Mode = 'footprint'", start)
    return body[start:end]


def test_the_shared_registration_has_all_four_calls():
    """Footprint.AddPCBObject alone reads back and is discarded on save."""
    both = _both_branch(FNS["RegisterLibPrimitive"])
    assert "Footprint.AddPCBObject(Prim)" in both
    assert "PcbLib.Board.AddPCBObject(Prim)" in both
    assert both.count("PCBM_BoardRegisteration") == 2, (
        "both the footprint and the board must be told")


def test_the_shared_registration_keeps_the_inline_order():
    """Both adds, then both broadcasts: the order pads were measured with.

    Mutation: move the board add below the footprint broadcast (the
    interleaved order an earlier version of the helper ran). Every call
    was proven, the order never was, and it sat under every track, arc,
    text, fill and region add. The inline pad add is read too, so the two
    cannot drift apart again.
    """
    both = _both_branch(FNS["RegisterLibPrimitive"])
    order = [both.index("Footprint.AddPCBObject(Prim)"),
             both.index("PcbLib.Board.AddPCBObject(Prim)"),
             both.index("SendMessageToRobots(Footprint.I_ObjectAddress"),
             both.index("SendMessageToRobots(PcbLib.Board.I_ObjectAddress")]
    assert order == sorted(order), (
        "RegisterLibPrimitive must add to the footprint, add to the board, "
        "then broadcast to the footprint, then to the board")

    pad = FNS["Lib_AddFootprintPad"]
    inline = [pad.index("Footprint.AddPCBObject(Pad)"),
              pad.index("PcbLib.Board.AddPCBObject(Pad)"),
              pad.index("SendMessageToRobots(Footprint.I_ObjectAddress"),
              pad.index("SendMessageToRobots(PcbLib.Board.I_ObjectAddress")]
    assert inline == sorted(inline), (
        "the inline pad registration this helper copies changed order")


def _creates_primitive(body: str) -> bool:
    return bool(re.search(r"PCBObjectFactory\(e(Pad|Track|Arc|Text|Fill|"
                          r"Region|ComponentBody)Object", body))


def test_every_library_primitive_is_registered_with_both_owners():
    """A new primitive kind cannot be added with three of the four calls."""
    creators = {n: b for n, b in FNS.items() if _creates_primitive(b)}
    assert len(creators) >= 8, (
        f"only {len(creators)} primitive creators found; the scan broke")
    bad = []
    for name, body in creators.items():
        if "RegisterLibPrimitive(" in body:
            continue
        if ("Board.AddPCBObject(" in body
                and "PCBM_BoardRegisteration" in body):
            continue
        bad.append(name)
    assert not bad, (
        f"{bad} create a footprint primitive without registering it with "
        f"the library board; it will read back and vanish on save")


# ---------------------------------------------------------------------------
# Choosing the footprint.
# ---------------------------------------------------------------------------

# The pad handlers take the same resolver (the hand-off this cluster
# recorded for the pad tools), so every footprint add can name its
# footprint.
@pytest.mark.parametrize("handler", AUTHORING + (
    "Lib_SetCurrentFootprint", "Lib_AddFootprintPad",
    "Lib_AddFootprintPads"))
def test_authoring_handlers_resolve_their_footprint(handler):
    body = FNS[handler]
    assert "ResolveLibFootprint(Params" in body, (
        f"{handler} does not resolve its footprint through "
        f"ResolveLibFootprint, so component_name does nothing")
    assert "PcbLib.CurrentComponent" not in body, (
        f"{handler} reads CurrentComponent itself instead of the resolver")


def test_the_resolver_verifies_the_switch():
    """Board.AddPCBObject attaches to the CURRENT footprint.

    A switch that silently fails would split one primitive across two
    footprints, so the write must be read back, and the old silent
    SetState_CurrentComponent must not come back.
    """
    body = FNS["ResolveLibFootprint"]
    write = body.index("PcbLib.CurrentComponent := Target")
    assert "PcbLib.CurrentComponent <> Target" in body[write:], (
        "the switch is not read back after it is made")
    assert "SetState_CurrentComponent" not in body
    for key in ("'library_path'", "'component_name'", "'footprint_name'"):
        assert key in body


def test_the_resolver_verifies_the_library_it_focused():
    body = FNS["ResolveLibFootprint"]
    opened = body.index("WorkspaceManager:OpenObject")
    assert "WRONG_DOCUMENT_FOCUSED" in body[opened:], (
        "an OpenObject that silently fails would leave the previous "
        "library in front and the write would land there")


# ---------------------------------------------------------------------------
# Sub-mil geometry.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("handler", AUTHORING)
def test_geometry_is_parsed_and_converted_as_decimals(handler):
    """StrToIntDef answers "12.5" with its default; MilsToCoord is Integer."""
    body = FNS[handler]
    assert "StrToIntDef(" not in body, (
        f"{handler} parses with StrToIntDef, which cannot read a decimal")
    assert "MilsToCoord(" not in body, (
        f"{handler} converts through the integer MilsToCoord")
    assert "MilsToCoordF(" in body


# ---------------------------------------------------------------------------
# The doubled text.
# ---------------------------------------------------------------------------

def test_the_text_handler_counts_and_removes_only_what_it_created():
    body = FNS["Lib_AddFootprintText"]
    before = body.index("LibTextAddresses(Footprint")
    create = body.index("PCBObjectFactory(eTextObject")
    assert before < create, "the BEFORE snapshot must precede the create"
    assert "RemoveLibTextByAddress(" in body
    for field in ('"new_objects"', '"group_yields"', '"duplicates_found"',
                  '"duplicates_removed"'):
        assert field in body, f"the reply lost {field}"


def test_a_duplicate_is_removed_from_the_footprint_inside_the_walk():
    """GroupIterator_Destroy releases what the iterator handed out.

    Mutation: put back ``PcbLib.Board.RemovePCBObject(Prim)`` beside the
    footprint removal, or drop the BeginModify bracket. The board removal
    ran automatically on every duplicate and had never been measured; it
    is now the caller's opt-in, through LibBoardRemoveEntries.
    """
    body = FNS["RemoveLibTextByAddress"]
    begin = body.index("PCBM_BeginModify")
    fp = body.index("Footprint.RemovePCBObject(Prim)")
    destroy = body.index("GroupIterator_Destroy")
    end = body.index("PCBM_EndModify")
    assert begin < fp < destroy < end
    assert "Board.RemovePCBObject" not in body


def test_the_text_cleanup_never_removes_without_a_surviving_original():
    """An unread NewAddr made the created text look like its own duplicate.

    Mutation: delete either guard. With NewAddr '' every new text with
    the string passed the duplicate test, the created one included, and
    the cleanup would have removed it and reported success.
    """
    body = FNS["Lib_AddFootprintText"]
    remove = body.index("RemoveLibTextByAddress(")
    assert body.index("NewAddr = ''") < remove
    assert body.index("Pos('|' + NewAddr + '|', AfterAddrs) = 0") < remove
    assert '"duplicate_cleanup"' in body


def test_the_geometry_reader_dedupes_by_address():
    """The audit read one double-registered text as two."""
    body = FNS["Lib_GetLibraryGeometry"]
    assert "Pad.I_ObjectAddress" in body
    assert "Txt.I_ObjectAddress" in body


def test_a_default_delete_removes_from_the_footprint_only():
    """The library-board half is opt-in until a live run proves it.

    Mutation: put ``PcbLib.Board.RemovePCBObject(Prim)`` back in the
    loop, or make the footprint current unconditionally. Both changed a
    delete tool that had worked into the experiment. The shared rule and
    its guards live in tests/test_library_removal_rule.py.
    """
    body = FNS["Lib_DeleteFootprintPrimitives"]
    assert "Target.RemovePCBObject(Prim)" in body
    assert "Board.RemovePCBObject" not in body
    assert "PcbLib.CurrentComponent :=" not in body


# ---------------------------------------------------------------------------
# Vocabularies agree across the bridge.
# ---------------------------------------------------------------------------

def test_region_kinds_agree_with_the_pascal():
    from eda_agent.tools.library import _REGION_KINDS
    body = FNS["Lib_AddFootprintRegions"]
    accepted = set(re.findall(r"KindStr <> '([a-z_]+)'", body))
    assert accepted, "no accepted kinds parsed out of the Pascal"
    assert accepted == set(_REGION_KINDS)


def test_text_registrations_agree_with_the_pascal():
    from eda_agent.tools.library import _TEXT_REGISTRATIONS
    body = FNS["Lib_AddFootprintText"]
    accepted = set(re.findall(r"RegMode <> '([a-z_]+)'", body))
    assert accepted == set(_TEXT_REGISTRATIONS)


# ---------------------------------------------------------------------------
# Python payloads.
# ---------------------------------------------------------------------------

def _fields(op: str) -> dict[str, str]:
    # GetBatchField's rule: a field with no '=' names nothing and is
    # skipped. join_ops closes every operation but the last with one.
    return dict(f.split("=", 1) for f in op.split(";") if "=" in f)


def test_a_decimal_track_survives():
    from eda_agent.tools.library import _tracks_payload
    payload, skipped = _tracks_payload([
        {"x1": 12.5, "y1": -3.3333333, "x2": 0, "y2": 0, "width": 5.9055},
        {"x1": "a", "y1": 0, "x2": 0, "y2": 0},
        {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "width": 0},
    ])
    assert skipped == 2
    f = _fields(payload)
    assert f["x1"] == "12.5" and f["y1"] == "-3.3333"
    assert f["width"] == "5.9055" and f["layer"] == "TopOverlay"


def test_fills_need_a_layer_and_an_area():
    from eda_agent.tools.library import _fills_payload
    payload, skipped = _fills_payload([
        {"x1": -24.6063, "y1": -24.6063, "x2": 24.6063, "y2": 24.6063,
         "layer": "TopPaste"},
        {"x1": 0, "y1": 0, "x2": 1, "y2": 1},
        {"x1": 0, "y1": 0, "x2": 0, "y2": 1, "layer": "TopPaste"},
        {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "TopLayer",
         "keepout": True, "rotation": 45},
    ])
    assert [s["item"] for s in skipped] == [1, 2]
    ops = payload.split("~~")
    assert _fields(ops[0])["x2"] == "24.6063"
    assert _fields(ops[1])["keepout"] == "true"
    assert _fields(ops[1])["rotation"] == "45"


@pytest.mark.parametrize("value,keepout", [
    ("false", False), ("0", False), (False, False), (0, False),
    ("true", True), ("1", True), (True, True)])
def test_fill_keepout_parses_by_the_one_boolean_rule(value, keepout):
    """Truthiness sent keepout=true for the string 'false' and turned the
    fill into a keep-out; this is an EDIT, so that was a silent wrong
    change."""
    from eda_agent.tools.library import _fills_payload
    payload, skipped = _fills_payload([
        {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "TopLayer",
         "keepout": value}])
    assert skipped == []
    assert ("keepout" in _fields(payload)) is keepout


def test_a_fill_keepout_that_is_not_a_boolean_is_skipped_with_a_reason():
    from eda_agent.tools.library import _fills_payload
    payload, skipped = _fills_payload([
        {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "TopLayer",
         "keepout": "perhaps"}])
    assert payload == ""
    assert skipped == [{"item": 0, "reason": "keepout must be true or false"}]


def test_a_hostile_fill_layer_cannot_add_a_field():
    from eda_agent.tools.library import _fills_payload
    payload, _ = _fills_payload([{"x1": 0, "y1": 0, "x2": 1, "y2": 1,
                                  "layer": "TopPaste;x1=999~~x1=5"}])
    assert len(payload.split("~~")) == 1
    assert _fields(payload)["x1"] == "0"


def test_regions_close_themselves_and_refuse_unknown_kinds():
    from eda_agent.tools.library import _regions_payload
    square = [[0, 0], [20, 0], [20, 20], [0, 20]]
    payload, skipped = _regions_payload([
        {"kind": "keepout", "vertices": square + [[0, 0]]},
        {"kind": "copper", "layer": "TopSolder",
         "vertices": [{"x": 0, "y": 0}, {"x": 1.5, "y": 0},
                      {"x": 0, "y": 1.5}]},
        {"kind": "board_cutout", "layer": "TopLayer", "vertices": square},
        {"layer": "TopLayer", "vertices": [[0, 0], [1, 1]]},
        {"vertices": square},
    ])
    assert [s["item"] for s in skipped] == [2, 3, 4]
    ops = payload.split("~~")
    keepout = _fields(ops[0])
    assert keepout["pts"] == "0,0,20,0,20,20,0,20", "closing repeat kept"
    assert "layer" not in keepout, "the Pascal defaults it to KeepOutLayer"
    assert _fields(ops[1])["pts"] == "0,0,1.5,0,0,1.5"


class _Recorder:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.calls.append((command, dict(params or {})))
        return {"success": True}

    def send_command(self, command, params=None, **kwargs):
        self.calls.append((command, dict(params or {})))
        return {"success": True}


def _tools():
    from eda_agent.tools.library import register_library_tools

    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    return captured


@pytest.fixture
def sent(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    rec = _Recorder()
    install_bridge_fake(monkeypatch, tmp_path, rec)
    return rec


@pytest.mark.parametrize("tool,args,command", [
    ("lib_add_footprint_track",
     {"x1": 0, "y1": 0, "x2": 1.5, "y2": 0}, "library.add_footprint_track"),
    ("lib_add_footprint_tracks",
     {"tracks": [{"x1": 0, "y1": 0, "x2": 1, "y2": 0}]},
     "library.add_footprint_tracks"),
    ("lib_add_footprint_arc",
     {"x_center": 0, "y_center": 0, "radius": 12.5},
     "library.add_footprint_arc"),
    ("lib_add_footprint_text", {"text": "REF"}, "library.add_footprint_text"),
    ("lib_add_footprint_fills",
     {"fills": [{"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "TopPaste"}]},
     "library.add_footprint_fills"),
    ("lib_add_footprint_regions",
     {"regions": [{"layer": "TopLayer",
                   "vertices": [[0, 0], [1, 0], [0, 1]]}]},
     "library.add_footprint_regions"),
    ("lib_add_footprint_pad",
     {"designator": "1", "x": 0, "y": 0}, "library.add_footprint_pad"),
    ("lib_add_footprint_pads",
     {"pads": [{"designator": "1", "x": 0, "y": 0}]},
     "library.add_footprint_pads"),
])
def test_every_footprint_tool_can_name_its_footprint(sent, tool, args,
                                                    command):
    fn = _tools()[tool]
    asyncio.run(fn(**args, component_name="FP_A",
                   library_path="C:/x/a.PcbLib"))
    cmd, params = sent.calls[-1]
    assert cmd == command
    assert params["component_name"] == "FP_A"
    assert params["library_path"] == "C:/x/a.PcbLib"


def test_the_singular_tools_send_decimals(sent):
    tools = _tools()
    asyncio.run(tools["lib_add_footprint_arc"](
        x_center=0.25, y_center=0, radius=12.5, width=5.9055))
    _cmd, params = sent.calls[-1]
    assert params["radius"] == "12.5" and params["x_center"] == "0.25"
    assert params["width"] == "5.9055"
    asyncio.run(tools["lib_add_footprint_text"](text="A", x=1.75, y=-0.5))
    _cmd, params = sent.calls[-1]
    assert params["x"] == "1.75" and params["y"] == "-0.5"
    assert "registration" not in params, "the default must stay off the wire"


def test_an_unknown_text_registration_is_refused_before_sending(sent):
    out = asyncio.run(_tools()["lib_add_footprint_text"](
        text="A", registration="sideways"))
    assert out["ok"] is False
    assert not sent.calls


def test_set_current_footprint_requires_a_name(sent):
    out = asyncio.run(_tools()["lib_set_current_footprint"](
        component_name="  "))
    assert out["ok"] is False
    assert not sent.calls
