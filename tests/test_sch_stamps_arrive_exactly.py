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


def _description_branch(gen: str) -> str:
    """The whole Description branch, from its test to the next one.

    Delimited by the two branch heads rather than a lazy ``Begin(.*?)End``:
    that stopped at the ``End`` of the first ``Try ... Except End;`` and
    checked one line of the branch, so anything after it went unseen.
    """
    head = "Else If KeyU = 'DESCRIPTION' Then"
    assert head in gen, "no Description branch: it would fall through to " \
                        "a parameter called Description"
    start = gen.index(head)
    end = gen.index("Else If Key = 'Footprint'", start)
    assert end > start
    return gen[start:end]


def test_the_handler_routes_description_to_the_component():
    gen = load(_ALTIUM / "Generic.pas")["Gen_SetSchComponentsParameters"]
    branch = _description_branch(gen)
    assert "Comp.ComponentDescription := Val" in branch
    assert "SetCompParamText" not in branch, (
        "SetCompParamText(Ex) CREATES a missing parameter: the stray "
        "Description parameter this branch exists to prevent")
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

def _found_and_create_branches() -> tuple[str, str]:
    """(modify branch, create branch) of SetCompParamTextEx.

    Split at the first ``Else`` AFTER ``If Found <> Nil Then``, not the
    first in the function: an ``Else`` earlier on would leave the modify
    slice empty and its "untouched" checks passing on nothing.
    """
    ex = load(_ALTIUM / "Generic.pas")["SetCompParamTextEx"]
    start = ex.index("If Found <> Nil Then")
    els = ex.index("Else", start)
    assert els > start
    found = ex[start:els]
    assert "Found.Text := ParamValue" in found, "the modify slice is empty"
    return found, ex[els:]


def _create_branch() -> str:
    return _found_and_create_branches()[1]


def test_a_created_parameter_is_hidden_and_sits_on_its_component():
    body = _create_branch()
    add = body.index("Comp.AddSchObject(NewParam)")
    for needle in ("CompLoc := Comp.Location;", "NewParam.Location := CompLoc;",
                   "NewParam.IsHidden := Not ShowNew;"):
        assert needle in body, needle
        assert body.index(needle) < add, (
            f"{needle} must be set before the parameter is added")


def test_an_existing_parameter_keeps_its_visibility_and_position():
    found, _ = _found_and_create_branches()
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


# ------------------------------------------------- review follow-ups, 2026-10-08
#
# A review of the change above found that it fixed the three field reports
# and opened smaller gaps next to each: a part's real Description parameter
# left stale, a deploy skew that two of the three senders could not see, a
# refusal dressed up as a stale script, shown parameters piled on the
# symbol origin, and values that still did not arrive and said nothing.


def test_an_existing_description_parameter_is_updated_never_created():
    """Imported and DbLib-style symbols often carry a REAL parameter called
    Description. The old route (SetCompParamText) updated it in place; the
    first version of the property route wrote only ComponentDescription,
    so such a part kept two descriptions that disagree and the reply
    (properties_set 1, parameters_modified 0) hid it. The branch now also
    updates that parameter if present, through a helper that cannot
    create one."""
    fns = load(_ALTIUM / "Generic.pas")
    helper = fns["ModifyCompParamIfPresent"]
    assert "UpperCase(Param.Name) = UpperCase(ParamName)" in helper, (
        "the search must be case-insensitive, like SetCompParamTextEx's")
    assert "Found.Text := ParamValue" in helper
    for creates in ("SchObjectFactory", "AddSchObject", "SchRegisterObject",
                    "SetCompParamText"):
        assert creates not in helper, (
            f"{creates}: a modify-only helper must never create a parameter")

    gen = fns["Gen_SetSchComponentsParameters"]
    branch = _description_branch(gen)
    prop = branch.index("Comp.ComponentDescription := Val")
    also = branch.index("If ModifyCompParamIfPresent(Comp, Key, Val) Then")
    assert prop < also
    assert "Inc(ParamsModified)" in branch[also:]
    assert "DescParamJson" in branch[also:]
    assert '"description_parameters_also_set"' in gen

    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    assert text.index("Function ModifyCompParamIfPresent(") < \
        text.index("Function Gen_SetSchComponentsParameters("), (
            "DelphiScript needs the callee declared first")


def test_a_shown_new_parameter_is_offset_and_turned_with_the_part():
    """With visible=true every created parameter used to land on exactly
    Comp.Location at orientation 0: two shown parameters drew on top of
    each other and on the body, and stayed horizontal on a part turned
    90/270. Hidden ones (the default) are unaffected. A shown one now goes
    one 100 mil row per parameter already shown, away from the Comment,
    and takes the part's orientation."""
    ex = load(_ALTIUM / "Generic.pas")["SetCompParamTextEx"]
    search = ex[:ex.index("If Found <> Nil Then")]
    assert "If Not Param.IsHidden Then Inc(ShownCount);" in search, (
        "the offset must count the parameters already shown on the part")

    create = _create_branch()
    show = create.index("If ShowNew Then")
    add = create.index("Comp.AddSchObject(NewParam)")
    shown = create[show:add]
    for needle in ("NewParam.ShowName := False;",
                   "Ori := Comp.Orientation;",
                   "CompLoc := Comp.Comment.Location;",
                   "MilsToCoord(100 * (ShownCount + 1))",
                   "NewParam.Location := CompLoc;",
                   "NewParam.Orientation := Ori;"):
        assert needle in shown, needle
    assert re.search(r"If \(Ori = 1\) Or \(Ori = 3\) Then\s*"
                     r"CompLoc\.X := CompLoc\.X \+", shown), (
        "a part turned 90/270 runs its text vertically: step along X")
    hidden = create[:show]
    assert "Orientation" not in hidden and "Comment.Location" not in hidden, (
        "a hidden created parameter keeps the plain on-the-part default")


def test_a_refusal_is_not_reported_as_a_stale_script(monkeypatch):
    """send_command_async RETURNS a policy refusal instead of raising it.
    That dict has no value_encoding either, so the tool used to attach a
    key_routing and the 'running Altium script predates' warning to a call
    that wrote nothing."""
    refused = {"success": False,
               "error": {"code": "REFUSED_BY_POLICY", "message": "no"}}
    bridge = _RecordingBridge(reply=refused)
    out = _tool(monkeypatch, bridge)(stamps=[{"designator": "R1",
                                              "MPN": "A;B"}])
    assert out == refused
    assert "warning" not in out and "key_routing" not in out


@pytest.mark.parametrize("reply,problem", [
    ({"updated": 1, "failed": 0, "total": 1, "value_encoding": _PCT1}, False),
    ({"updated": 1, "failed": 0, "total": 1}, True),
    ({"updated": 0, "failed": 1, "total": 1, "value_encoding": ""}, True),
    ({"success": False, "error": {"code": "REFUSED_BY_POLICY"}}, False),
    ({"success": True}, False),
    (None, False),
    ("text", False),
])
def test_the_shared_skew_check(reply, problem):
    assert bool(wire.stamp_reply_problem(reply)) is problem
    if problem:
        assert wire.stamp_reply_problem(reply) == wire.STAMP_SKEW_NOTE
        assert "redeploy" in wire.STAMP_SKEW_NOTE


def test_every_stamp_sender_checks_the_reply():
    """Under deploy skew a handler without PctDecode writes the escapes
    literally: a datasheet URL's '?a=b' becomes '?a%3Db', '%20' becomes
    '%2520'. Only the bulk tool looked at the echo; the plan executor and
    the sheet emitter discarded the reply, and pipeline.py stamps datasheet
    URLs through the emitter. Every sender now keeps it and asks the one
    shared check."""
    senders = [p for p in _SRC.rglob("*.py")
               if '"generic.set_sch_components_parameters"'
               in p.read_text(encoding="utf-8")]
    assert len(senders) >= 3, senders
    for path in senders:
        src = path.read_text(encoding="utf-8")
        assert "stamp_reply_problem(" in src, path


def test_the_emitter_notes_a_stale_handler():
    from eda_agent.design.emitter import EmitResult, _emit_parameter_stamps

    class _Inst:
        refdes = "U1"

    class _Bridge:
        def __init__(self, reply):
            self.reply = reply

        def send_command(self, command, params=None, **kw):
            if command == "generic.set_sch_components_parameters":
                return dict(self.reply)
            return {"success": True}

    stamps = {"U1": {"Datasheet": "https://x.example/ds?a=b%20c"}}
    stale = EmitResult()
    _emit_parameter_stamps([_Inst()], stamps, Path("s.SchDoc"),
                           _Bridge({"updated": 1, "failed": 0, "total": 1}),
                           stale)
    assert wire.STAMP_SKEW_NOTE in stale.notes

    fresh = EmitResult()
    _emit_parameter_stamps([_Inst()], stamps, Path("s.SchDoc"),
                           _Bridge({"updated": 1, "failed": 0, "total": 1,
                                    "value_encoding": _PCT1}), fresh)
    assert not any("pct1" in n for n in fresh.notes), fresh.notes


def test_the_executor_notes_a_stale_handler(tmp_path):
    from eda_agent.design.executor import execute_plan
    from tests.design.test_executor import _FakeBridge, _two_part_plan

    class _Stale(_FakeBridge):
        def __init__(self, echo):
            super().__init__()
            self.echo = echo

        def send_command(self, command, params=None, timeout=None):
            out = super().send_command(command, params, timeout)
            if command == "generic.set_sch_components_parameters":
                out = {"updated": 1, "failed": 0, "total": 1}
                if self.echo:
                    out["value_encoding"] = _PCT1
            return out

    stale = _Stale(echo=False)
    result = execute_plan(_two_part_plan(), str(tmp_path / "x.PrjPcb"),
                          bridge=stale)
    assert any(c == "generic.set_sch_components_parameters"
               for c, _ in stale.calls), "the plan stamped nothing"
    assert any(wire.STAMP_SKEW_NOTE in n for n in result.notes), result.notes

    fresh = _Stale(echo=True)
    result = execute_plan(_two_part_plan(), str(tmp_path / "y.PrjPcb"),
                          bridge=fresh)
    assert not any(wire.STAMP_SKEW_NOTE in n for n in result.notes)


def test_an_empty_value_is_reported_not_silently_dropped(monkeypatch):
    """The encoder and the handler both skip an empty value, so a caller
    trying to CLEAR an MPN got a clean reply with nothing changed. The
    tool cannot clear a parameter; it now says which keys it skipped."""
    bridge = _RecordingBridge()
    run = _tool(monkeypatch, bridge)
    out = run(stamps=[{"designator": "R1", "MPN": "", "Value": "10k",
                       "Note": None}])
    assert out["skipped_empty"] == [{"designator": "R1", "key": "MPN"},
                                    {"designator": "R1", "key": "Note"}]
    assert "MPN" not in out["key_routing"]
    (_, params), = bridge.calls
    assert _handler_reads(params) == [{"designator": "R1", "Value": "10k"}]

    bridge = _RecordingBridge()
    out = _tool(monkeypatch, bridge)(stamps=[{"designator": "R1", "MPN": ""}])
    assert out["ok"] is False and bridge.calls == []
    assert "cannot clear" in out["reason"]
    assert out["skipped_empty"] == [{"designator": "R1", "key": "MPN"}]


@pytest.mark.parametrize("bad", ["A\x00B", "x\x01", "\x1b[0m"])
def test_a_control_character_is_refused(monkeypatch, bad):
    """A NUL passed unsendable_chars, became Chr(0) in Pascal, and the COM
    string conversion truncated the text there: 'A\\x00B' arrives as 'A'
    with an ok reply."""
    bridge = _RecordingBridge()
    out = _tool(monkeypatch, bridge)(stamps=[{"designator": "R1",
                                              "Note": bad}])
    assert out["ok"] is False and bridge.calls == []
    assert out["problems"][0]["key"] == "Note"
    assert "control" in out["problems"][0]["reason"]


def test_tab_and_newlines_still_travel(monkeypatch):
    bridge = _RecordingBridge()
    _tool(monkeypatch, bridge)(stamps=[{"designator": "R1",
                                        "Note": "a\tb\r\nc"}])
    (_, params), = bridge.calls
    assert _handler_reads(params) == [{"designator": "R1",
                                       "Note": "a\tb\r\nc"}]


def test_the_supplier_pn_audit_points_at_a_tool_that_can_hide():
    """audit_find_visible_supplier_pn told agents to hide its findings with
    sch_set_components_parameters, which never changes the visibility of an
    existing parameter (and never could)."""
    src = (_SRC / "tools" / "audit.py").read_text(encoding="utf-8")
    start = src.index("async def audit_find_visible_supplier_pn(")
    body = src[start:src.index("@mcp.tool()", start)]
    assert "to hide them in bulk" not in body
    assert "obj_batch_modify" in body and "IsHidden=true" in body
