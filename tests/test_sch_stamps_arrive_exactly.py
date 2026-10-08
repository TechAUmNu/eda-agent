# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A parameter stamp writes what the caller asked for, where they asked.

Three field reports from one session, 2026-10-08, the multiviewer carrier
project, bridge 2026.10.07.03, all on ``sch_set_components_parameters``:

1. A Manufacturer Part Number or Description containing ``;`` arrived on
   the part with ``,`` instead. The batch grammar splits fields on ``;``
   and had no escape, so the Python side SUBSTITUTED every ``;`` with
   ``,`` (``payload_safe``). Safe for the grammar, silently wrong for the
   data: a BOM line whose MPN is not the MPN that was sent. The stamp
   payload is now ENCODED (``% ; ~ =`` as ``%XX``) and ``PctDecode`` in
   Main.pas turns it back after the split.

2. Passing ``Description`` created a NEW visible parameter called
   Description instead of setting the component's own description
   (``ISch_Component.ComponentDescription``, the one the BOM and the
   Properties panel read). It is now routed to the property, ``Comment``
   likewise to the Comment text, and the reply says which keys went to
   properties and which to parameters.

3. Every parameter the tool CREATED came out visible at (0,0) of the
   sheet: an MPN drawn in the corner, far from its part. New parameters
   are now hidden and placed on the owning component unless
   ``visible=True``; existing ones keep their visibility and position.

The Pascal cannot run here, so the handler side is pinned two ways: the
payload is parsed by a step-for-step mirror of ``NextBatchOp``, the
handler's field loop and ``PctDecode`` and must reproduce the caller's
dicts EXACTLY; and the Pascal source is read (comment-free, per function)
for the branches the mirror assumes.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from eda_agent.bridge import payload as wire
from eda_agent.tools import generic as generic_mod
from eda_agent.tools.registry import ToolRegistry
from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"
_SRC = _ROOT / "src" / "eda_agent"

#: Spelled out rather than read from wire.STAMP_ENCODING, so a build
#: without the encoding fails on the VALUE it delivers, not on an import.
_PCT1 = "pct1"

OHM = chr(0x03A9)
MICRO = chr(0x00B5)
DEGREE = chr(0x00B0)


# ------------------------------------------------------------- the harness

class _RecordingBridge:
    def __init__(self, reply=None):
        self.calls = []
        self._reply = reply

    async def send_command_async(self, command, params=None, **kwargs):
        # Through JSON and back, as the request file carries it.
        self.calls.append((command, json.loads(json.dumps(params))))
        if self._reply is not None:
            return dict(self._reply)
        return {"updated": 1, "failed": 0, "total": 1,
                "value_encoding": _PCT1}


def _tool(monkeypatch, bridge):
    monkeypatch.setattr(generic_mod, "get_bridge", lambda: bridge)
    registry = ToolRegistry()
    generic_mod.register_generic_tools(registry)
    fns = {t.name: t.fn for t in asyncio.run(registry.list_tools())}
    return lambda **kw: asyncio.run(fns["sch_set_components_parameters"](**kw))


def _handler_reads(params: dict) -> list[dict]:
    """What Gen_SetSchComponentsParameters makes of ``params``.

    Mirrors NextBatchOp (split on the first ``~~``, skip empties), the
    designator lookup (GetBatchField, then PctDecode when encoded) and the
    handler's own field loop (split on ``;``, key at the FIRST ``=``,
    decode AFTER the split, skip empty values).
    """
    encoded = params.get("value_encoding") == _PCT1
    dec = wire.stamp_decode if encoded else (lambda s: s)
    out = []
    remaining = params["stamps"]
    while remaining:
        sep = remaining.find("~~")
        op, remaining = ((remaining, "") if sep < 0
                         else (remaining[:sep], remaining[sep + 2:]))
        if not op:
            continue
        stamp: dict = {}
        for field in op.split(";"):
            if "=" not in field:
                continue
            key, val = field.split("=", 1)
            key, val = dec(key), dec(val)
            if key and val:
                stamp[key] = val
        out.append(stamp)
    return out


HOSTILE = [
    "GRM188R71H104KA93D; alt GRM188R71H104KA93J",
    "Cap, 100nF, 50V; X7R",
    "R=10k|TOL=1%",
    'say "hi" \\ there',
    "already %3B escaped",
    "100%",
    "trailing %",
    "%%",
    "~{RESET}",
    "two~~tildes",
    "ends with ~",
    "10" + MICRO + "F 85" + DEGREE + "C Ren" + chr(0xE9),
    "semi;colon=equals~tilde%percent",
]


# ------------------------------------------------- 1. values arrive exactly

@pytest.mark.parametrize("value", HOSTILE)
def test_a_value_arrives_exactly_as_sent(monkeypatch, value):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    run(stamps=[{"designator": "U7", "Manufacturer Part Number": value,
                 "Value": "TPS7A8300"}])
    (command, params), = bridge.calls
    assert command == "generic.set_sch_components_parameters"
    assert _handler_reads(params) == [{
        "designator": "U7", "Manufacturer Part Number": value,
        "Value": "TPS7A8300"}], (
        f"{value!r} did not survive the wire; the old sanitiser turned "
        f"';' into ',' and collapsed '~~'")
    assert params["value_encoding"] == _PCT1


def test_hostile_designators_and_keys_cannot_reshape_the_payload(monkeypatch):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    stamps = [{"designator": "R1;Value=999", "a=b;c": "x~~designator=R99"},
              {"designator": "R2~", "Note": "~"}]
    run(stamps=stamps)
    (_, params), = bridge.calls
    assert _handler_reads(params) == [
        {"designator": "R1;Value=999", "a=b;c": "x~~designator=R99"},
        {"designator": "R2~", "Note": "~"}]


@pytest.mark.parametrize("text", HOSTILE + ["plain", ""])
def test_the_codec_round_trips_and_leaves_no_separator(text):
    enc = wire.stamp_encode(text)
    assert ";" not in enc and "~" not in enc and "=" not in enc
    assert wire.stamp_decode(enc) == text


def test_plain_text_is_sent_unchanged():
    """An older handler ignores the marker; text with nothing to escape
    must still arrive right through it."""
    for text in ("RC0603FR-0710KL", "Yageo", "10k", "Cap 100nF 50V X7R",
                 "10" + MICRO + "F"):
        assert wire.stamp_encode(text) == text


def test_decode_copies_what_is_not_an_escape():
    """PctDecode copies a '%' not followed by two hex digits."""
    for text in ("%", "%4", "%G1", "50%off", "%%41"):
        assert wire.stamp_decode(text) == text.replace("%41", "A")


def test_text_the_bridge_would_flatten_is_refused_not_written(monkeypatch):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    out = run(stamps=[{"designator": "R1", "Value": "10" + OHM}])
    assert out["ok"] is False and bridge.calls == []
    assert out["problems"][0]["key"] == "Value"
    assert OHM in out["problems"][0]["reason"]


def test_the_pascal_decodes_after_splitting():
    """The mirror above is only worth something if the handler does this.

    Decoding BEFORE the split would turn a decoded ';' back into a field
    separator, which is the original bug wearing a disguise.
    """
    main = load(_ALTIUM / "Main.pas")
    dec = main["PctDecode"]
    assert "HexDigitValue" in dec and "Chr(D1 * 16 + D2)" in dec
    text = (_ALTIUM / "Main.pas").read_text(encoding="utf-8")
    assert text.index("Function HexDigitValue(") < \
        text.index("Function PctDecode("), (
            "DelphiScript needs the callee declared first")

    gen = load(_ALTIUM / "Generic.pas")["Gen_SetSchComponentsParameters"]
    assert "ExtractJsonValue(Params, 'value_encoding')" in gen
    assert wire.STAMP_ENCODING == _PCT1
    assert f"= '{_PCT1}'" in gen, (
        "the Pascal marker and STAMP_ENCODING disagree")
    assert re.search(r"FieldStr := GetBatchField\(Op, 'designator'\);\s*"
                     r"If Encoded Then FieldStr := PctDecode\(FieldStr\);",
                     gen), "the designator selector is not decoded"
    split = gen.index("EqPos := Pos('=', FieldStr);")
    decode = gen.index("Key := PctDecode(Key);")
    assert split < decode and "Val := PctDecode(Val);" in gen
    assert "value_encoding\":\"' + EscapeJsonString(EncodingStr)" in gen, (
        "the handler must echo the encoding so Python can detect skew")


def test_every_stamp_payload_builder_encodes(monkeypatch):
    """Three modules build this payload. One of them substituting would
    bring the ',' back on that path only.

    Any source file that sends the command must name STAMP_ENCODING and
    call encode_stamps, and none may keep the ``replace(';', ',')`` the
    executor used.
    """
    senders = [p for p in _SRC.rglob("*.py")
               if '"generic.set_sch_components_parameters"'
               in p.read_text(encoding="utf-8")]
    assert len(senders) >= 3, senders
    for path in senders:
        src = path.read_text(encoding="utf-8")
        assert "STAMP_ENCODING" in src and "encode_stamps(" in src, path
        assert "replace(';', ',')" not in src, path


# ----------------------------------- 2. Description is the component's own

def test_description_and_comment_are_properties_not_parameters(monkeypatch):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    out = run(stamps=[{"designator": "U1", "Description": "LDO; 3 A",
                       "comment": "TPS7A83", "MPN": "TPS7A8300RGWR",
                       "Footprint": "QFN-20"}])
    assert out["key_routing"] == {
        "Description": "property:ComponentDescription",
        "comment": "property:Comment",
        "MPN": "parameter",
        "Footprint": "skipped:read-only"}


@pytest.mark.parametrize("key,route", [
    ("Description", "property:ComponentDescription"),
    ("DESCRIPTION", "property:ComponentDescription"),
    ("description", "property:ComponentDescription"),
    ("Comment", "property:Comment"),
    ("Value", "property:Comment"),
    ("value", "parameter"),
    ("Footprint", "skipped:read-only"),
    ("Manufacturer", "parameter"),
])
def test_key_routes(key, route):
    """``Value`` and ``Footprint`` stay exact, as they always matched."""
    assert wire.stamp_key_route(key) == route


def test_the_handler_routes_description_to_the_component():
    gen = load(_ALTIUM / "Generic.pas")["Gen_SetSchComponentsParameters"]
    branch = re.search(r"Else If KeyU = 'DESCRIPTION' Then\s*Begin(.*?)End",
                       gen, re.S)
    assert branch, "no Description branch: it would fall through to a " \
                   "parameter called Description"
    assert "Comp.ComponentDescription := Val" in branch.group(1)
    assert "SetCompParamText" not in branch.group(1)
    assert re.search(r"If \(Key = 'Value'\) Or \(KeyU = 'COMMENT'\) Then\s*"
                     r"Begin\s*SchBeginModify\(Comp\.Comment\);", gen)
    assert gen.index("KeyU = 'DESCRIPTION'") < \
        gen.index("SetCompParamTextEx(Comp, Key, Val, ShowNew)"), (
            "the property branches must be tested before the parameter one")


def test_the_reply_counts_properties_and_parameters_separately():
    gen = load(_ALTIUM / "Generic.pas")["Gen_SetSchComponentsParameters"]
    for field in ("properties_set", "parameters_modified",
                  "parameters_created", "skipped_read_only", "created",
                  "new_parameters_visible"):
        assert f'"{field}"' in gen, field


# ------------------------------------- 3. new parameters: hidden, on the part

def _create_branch() -> str:
    ex = load(_ALTIUM / "Generic.pas")["SetCompParamTextEx"]
    return ex[ex.index("Else"):]


def test_a_created_parameter_is_hidden_and_sits_on_its_component():
    body = _create_branch()
    add = body.index("Comp.AddSchObject(NewParam)")
    for needle in ("CompLoc := Comp.Location;", "NewParam.Location := CompLoc;",
                   "NewParam.IsHidden := Not ShowNew;"):
        assert needle in body, needle
        assert body.index(needle) < add, (
            f"{needle} must be set before the parameter is added")


def test_an_existing_parameter_keeps_its_visibility_and_position():
    ex = load(_ALTIUM / "Generic.pas")["SetCompParamTextEx"]
    found = ex[ex.index("If Found <> Nil Then"):ex.index("Else")]
    assert "Found.Text := ParamValue" in found
    for untouched in ("IsHidden", "Location", "ShowName"):
        assert untouched not in found, (
            f"changing a value must not touch {untouched}")


def test_every_other_creator_gets_the_hidden_default():
    """SetCompParamText (SPICE stamping, the singular tool) delegates."""
    fns = load(_ALTIUM / "Generic.pas")
    assert "SetCompParamTextEx(Comp, ParamName, ParamValue, False)" in \
        fns["SetCompParamText"]
    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    assert text.index("Function SetCompParamTextEx(") < \
        text.index("Function SetCompParamText(Comp") < \
        text.index("Function Gen_SetSchComponentsParameters(")


def test_created_parameters_are_hidden_unless_asked(monkeypatch):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    run(stamps=[{"designator": "R1", "MPN": "x"}])
    run(stamps=[{"designator": "R1", "MPN": "x"}], visible=True)
    assert [p["new_param_visible"] for _, p in bridge.calls] == \
        ["false", "true"]
    gen = load(_ALTIUM / "Generic.pas")["Gen_SetSchComponentsParameters"]
    assert "LowerCase(ExtractJsonValue(Params, 'new_param_visible')) = " \
           "'true'" in gen


def test_a_non_boolean_visible_is_refused(monkeypatch):
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    out = run(stamps=[{"designator": "R1", "MPN": "x"}], visible="maybe")
    assert out["ok"] is False and bridge.calls == []


# --------------------------------------------------------------- skew guard

def test_an_older_script_is_named_when_it_ignored_the_encoding(monkeypatch):
    """A handler that predates PctDecode writes '%3B' into the part. The
    reply has no value_encoding echo, and the tool says so."""
    old_reply = {"updated": 1, "failed": 0, "total": 1}
    bridge = _RecordingBridge(reply=old_reply)
    run = _tool(monkeypatch, bridge)
    out = run(stamps=[{"designator": "R1", "MPN": "A;B"}])
    assert "ESCAPED" in out["warning"] and "Redeploy" in out["warning"]

    bridge = _RecordingBridge()
    out = _tool(monkeypatch, bridge)(stamps=[{"designator": "R1",
                                              "MPN": "A;B"}])
    assert "warning" not in out
