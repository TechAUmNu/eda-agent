# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D1 and D5 of the 2026-10-11 live run (first 2026-10-11 build, AD 26.10.1.5).

D1  After SetModified(True), IServerDocument.Modified read as neither true
    nor false: 'If X' AND 'If Not X' were both false, so every mark said
    marked_modified:false with '*' on the tab, and the negated branches
    (save_hint, mark.problem, the bridge-edit close message) never fired.
    Every read now goes through ModifiedFlagState / ModifiedFlagOf
    (Main.pas), built ONLY from 'If X' and 'If Not X' - the two forms that
    ran live on the value - so the odd value (neither held) reads modified
    and a normal flag never meets an unproven compare (review of de09e23).
    The mark carries flag_raw / flag_tests as the diagnostic. FieldFlag
    models the value; its __pnot__ is the field's 'If Not X'.
D5  The MODIFIED close refusal did not name the bridge edit.

Each test fails on ef2d79b.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.live_2026_10_11 import (
    FieldFlag, NullFlag, OddRaising, StrictFlag, _MAIN, _UTILS, _caught,
    _field, _with)
from tests.pascal_eval import PascalError, build
from tests.pascal_source import load
from tests.test_bridge_record_names_one_file import World, _ALTIUM


# ---------------------------------------------------------------------------
# D1. The flag is read as a real Boolean
# ---------------------------------------------------------------------------

_READERS = ["ModifiedFlagState", "ModifiedFlagOf", "ModifiedFlagRaw",
            "ModifiedFlagTests"]


def _flag_ns():
    ns = {}
    _caught(ns)
    build(_MAIN, [n for n in _READERS if n in _MAIN], ns)
    return ns


class _Doc:
    def __init__(self, value=False, raises=False):
        self.value = value
        self.raises = raises

    @property
    def Modified(self):
        if self.raises:
            raise PascalError("Modified")
        return self.value


@pytest.mark.parametrize("value, expected", [
    (True, True), (False, False), (FieldFlag(1), True), (FieldFlag(-1), True),
    (FieldFlag(0), False), (StrictFlag(True), True), (StrictFlag(False), False),
    (OddRaising(1), True)])
def test_modified_flag_of_returns_a_real_boolean(value, expected):
    ns = _flag_ns()
    got = ns["ModifiedFlagOf"](_Doc(value))
    assert got is expected
    assert ns["_caught"] == []


def test_modified_flag_of_nil_is_false_and_a_raising_read_is_doubt():
    ns = _flag_ns()
    assert ns["ModifiedFlagOf"](None) is False
    assert ns["ModifiedFlagOf"](_Doc(raises=True)) is True
    assert ns["ModifiedFlagOf"](_Doc(NullFlag())) is True
    assert ns["_caught"] == ["ModifiedFlagState:Modified"] * 2


@pytest.mark.parametrize("value", [True, False])
def test_a_normal_flag_never_meets_an_unproven_form(value):
    """Review of de09e23: `Raw <> 0` (and the diagnostics' `= 1`, `= True`)
    compared a Boolean with an integer or a literal - forms that never ran in
    this codebase - on EVERY read, outside any Try. A normal flag that
    refuses those compares must pass through all four readers untouched."""
    ns = _flag_ns()
    doc = _Doc(StrictFlag(value))
    assert ns["ModifiedFlagOf"](doc) is value
    assert ns["ModifiedFlagState"](doc) == (1 if value else 0)
    assert ns["ModifiedFlagRaw"](doc) == ("true" if value else "false")
    assert ns["ModifiedFlagTests"](doc) == (
        "if=1,not=0" if value else "if=0,not=1")
    assert ns["_caught"] == []


def test_an_odd_value_whose_compares_raise_is_caught_and_counted():
    """If the engine refuses the numeric compares on the odd value too, the
    raise stays inside the diagnostics' own Try, keeps the proven half of
    the answer, and is counted under its own site."""
    ns = _flag_ns()
    doc = _Doc(OddRaising(1))
    assert ns["ModifiedFlagState"](doc) == 2
    assert ns["ModifiedFlagRaw"](doc) == "odd"
    assert ns["ModifiedFlagTests"](doc) == "if=0,not=0,raised"
    assert ns["_caught"] == ["ModifiedFlagRaw:compare",
                             "ModifiedFlagTests:compare"]


def test_the_diagnostic_names_the_value_by_comparisons():
    ns = _flag_ns()
    assert ns["ModifiedFlagRaw"](_Doc(FieldFlag(1))) == "1"
    assert ns["ModifiedFlagRaw"](_Doc(FieldFlag(-1))) == "true"   # a normal True
    assert ns["ModifiedFlagRaw"](_Doc(False)) == "false"
    assert ns["ModifiedFlagRaw"](_Doc(raises=True)) == "unreadable"
    assert ns["ModifiedFlagRaw"](None) == ""
    assert ns["ModifiedFlagTests"](_Doc(FieldFlag(1))) == (
        "if=0,not=0,ne_zero=1,eq_true=0,eq_false=0")
    assert ns["ModifiedFlagTests"](_Doc(FieldFlag(-1))) == "if=1,not=0"
    assert ns["ModifiedFlagTests"](_Doc(raises=True)) == "unreadable"


def test_no_raw_modified_read_is_left_outside_the_helper():
    """Every read of IServerDocument.Modified in every unit goes through
    ModifiedFlagOf (or the two diagnostic readers beside it)."""
    allowed = set(_READERS)
    raw = re.compile(r"(?<!Set)(?<!\w)\w+\.Modified\b(?!\s*\()")
    found = []
    for path in sorted(_ALTIUM.glob("*.pas")):
        for name, body in load(path, minimum=1).items():
            if name in allowed:
                continue
            for m in raw.finditer(body):
                found.append(f"{path.name}:{name}: {m.group(0)}")
    assert not found, "raw Modified reads (they read neither true nor false " \
        "on AD 26.10.1.5):\n" + "\n".join(found)


def test_the_mark_reads_true_on_the_field_value_and_reports_the_probe():
    w = World(bare="nil", focus="scratch")
    _caught(w.ns)
    w.ns.setdefault("MarkLastFlagRaw", "")
    w.ns.setdefault("MarkLastFlagTests", "")
    _field(w.scratch)
    assert w.MarkDocDirty(w.scratch.FileName) is True
    assert w.MarkLastModified is True
    assert w.MarkLastError == ""
    assert w.MarkLastFlagRaw == "1"
    assert w.MarkLastFlagTests == "if=0,not=0,ne_zero=1,eq_true=0,eq_false=0"


def test_a_mark_whose_flag_cannot_be_read_does_not_claim_it():
    """Review of de09e23: ModifiedFlagOf answers True for an unreadable flag
    (doubt writes - right for a save), so the mark said marked_modified:true
    with no problem for a flag nobody read. A read-back uses the state."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.scratch.Modified = NullFlag()
    w.scratch.SetModified = lambda _v: None
    assert w.MarkDocDirty(w.scratch.FileName) is False
    assert w.MarkLastModified is False
    assert w.MarkLastFound is True
    assert w.MarkLastError == "the modified flag could not be read"
    assert w.MarkLastFlagRaw == "unreadable"


def test_a_raising_diagnostic_never_aborts_the_mark():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    _field(w.scratch)

    def boom(_doc):
        raise PascalError("diagnostic")
    w.ns["ModifiedFlagRaw"] = boom
    assert w.MarkDocDirty(w.scratch.FileName) is True
    assert w.MarkLastModified is True
    assert w.MarkLastError == ""


def test_the_mark_json_carries_the_probe():
    body = _UTILS["MarkOutcomeJson"]
    assert "',\"flag_raw\":\"' + EscapeJsonString(MarkLastFlagRaw)" in body
    assert "',\"flag_tests\":\"' + EscapeJsonString(MarkLastFlagTests)" in body
    status = load(_ALTIUM / "StatusForm.pas", minimum=10)
    assert any("MarkLastFlagRaw := '';" in b for b in status.values())


def test_doc_is_modified_reads_the_field_value_as_modified():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    _field(w.scratch).SetModified(True)
    assert w.DocIsModified(w.scratch.FileName) is True


def test_the_declined_save_detector_counts_the_field_value():
    """still_dirty: CountDirtyDocuments is how app_save_all tells a save the
    editor declined. With the field value it counted nothing."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    _with(w, ["CountDirtyInProject", "CountDirtyDocuments"])
    _field(w.sheet, save_clears=False).SetModified(True)
    assert w.CountDirtyDocuments(0) == 1


# ---------------------------------------------------------------------------
# D5. The close refusal names the bridge edit
# ---------------------------------------------------------------------------

def _close_world():
    w = World(bare="nil", focus=None)
    _caught(w.ns)

    def extract(params, key):
        return str(json.loads(params).get(key, ""))
    w.ns.update(
        ExtractJsonValue=extract,
        BuildErrorResponse=lambda rid, code, msg: json.dumps(
            {"success": False, "code": code, "message": msg}),
        BuildSuccessResponse=lambda rid, data: (
            '{"success":true,"data":' + data + '}'),
        EscapeJsonString=lambda s: s.replace("\\", "\\\\"))
    build(_UTILS, ["BoolToJsonStr"], w.ns)
    build(load(_ALTIUM / "Application.pas", minimum=10), ["App_CloseDocument"], w.ns)
    return w


def _close(w, path, discard=False):
    params = json.dumps({"file_path": path, "discard": "true" if discard else ""})
    return json.loads(w.App_CloseDocument(params, "r1"))


@pytest.mark.parametrize("field", [False, True])
def test_a_bridge_edit_is_named_when_the_flag_reads_modified(field):
    """Once the flag reads true (the D1 fix), 'BridgeEdited And Not
    FlagRead' never chose the bridge message: the generic one came back."""
    w = _close_world()
    if field:
        _field(w.scratch)
    else:
        w.scratch.flag_sticks = True
    w.MarkDocDirty(w.scratch.FileName)
    reply = _close(w, w.scratch.FileName)
    assert reply["code"] == "MODIFIED"
    assert "This bridge edited the document" in reply["message"], reply["message"]
    assert w.scratch in w.client.open


def test_the_discard_reply_renders_the_field_flag_as_true():
    w = _close_world()
    _field(w.scratch)
    w.MarkDocDirty(w.scratch.FileName)
    reply = _close(w, w.scratch.FileName, discard=True)
    assert reply["data"]["modified_read"] is True
    assert reply["data"]["bridge_edited"] is True
    assert reply["data"]["discarded"] is True


def test_a_document_nobody_edited_keeps_the_generic_refusal():
    w = _close_world()
    w.scratch.Modified = True
    reply = _close(w, w.scratch.FileName)
    assert reply["code"] == "MODIFIED"
    assert reply["message"].startswith("The document has unsaved changes")
