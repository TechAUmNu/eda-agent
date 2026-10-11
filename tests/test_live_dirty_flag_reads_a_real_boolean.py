# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D1 and D5 of the 2026-10-11 live run (first 2026-10-11 build, AD 26.10.1.5).

D1  After SetModified(True), IServerDocument.Modified read as neither true
    nor false: 'If X' AND 'If Not X' were both false, so every mark said
    marked_modified:false with '*' on the tab, and the negated branches
    (save_hint, mark.problem, the bridge-edit close message) never fired.
    Every read now goes through ModifiedFlagOf (Main.pas), which compares
    with 0 and returns a real Boolean; the mark carries flag_raw /
    flag_tests as the diagnostic. FieldFlag models the value: DelphiScript's
    'If X' is False for it and only a numeric compare reads it as set.
    Python's `not` cannot reproduce the field's 'If Not X' = False, so the
    tests assert on what the fixed code RETURNS, never on a negated branch.
D5  The MODIFIED close refusal did not name the bridge edit.

Each test fails on ef2d79b.
"""
from __future__ import annotations

import json
import re

import pytest

from tests.live_2026_10_11 import (
    FieldFlag, _MAIN, _UTILS, _caught, _field, _with)
from tests.pascal_eval import PascalError, build
from tests.pascal_source import load
from tests.test_bridge_record_names_one_file import World, _ALTIUM


# ---------------------------------------------------------------------------
# D1. The flag is read as a real Boolean
# ---------------------------------------------------------------------------

def _flag_ns():
    ns = {}
    _caught(ns)
    build(_MAIN, ["ModifiedFlagOf", "ModifiedFlagRaw", "ModifiedFlagTests"], ns)
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
    (True, True), (False, False), (FieldFlag(1), True), (FieldFlag(0), False),
    (-1, True), (1, True), (0, False), (None, True)])
def test_modified_flag_of_returns_a_real_boolean(value, expected):
    ns = _flag_ns()
    got = ns["ModifiedFlagOf"](_Doc(value))
    assert got is expected


def test_modified_flag_of_nil_is_false_and_a_raising_read_is_doubt():
    ns = _flag_ns()
    assert ns["ModifiedFlagOf"](None) is False
    assert ns["ModifiedFlagOf"](_Doc(raises=True)) is True
    assert ns["_caught"] == ["ModifiedFlagOf:Modified"]


def test_the_diagnostic_names_the_value_by_comparisons():
    ns = _flag_ns()
    assert ns["ModifiedFlagRaw"](_Doc(FieldFlag(1))) == "1"
    assert ns["ModifiedFlagRaw"](_Doc(FieldFlag(-1))) == "-1"   # a normal True
    assert ns["ModifiedFlagRaw"](_Doc(False)) == "0"
    assert ns["ModifiedFlagRaw"](_Doc(raises=True)) == "unreadable"
    assert ns["ModifiedFlagRaw"](None) == ""
    tests = ns["ModifiedFlagTests"](_Doc(FieldFlag(1)))
    assert tests.startswith("if=0,") and tests.endswith(
        ",eq_true=0,eq_false=0,ne_zero=1"), tests
    assert ns["ModifiedFlagTests"](_Doc(FieldFlag(-1))) == (
        "if=1,not=0,eq_true=1,eq_false=0,ne_zero=1")


def test_no_raw_modified_read_is_left_outside_the_helper():
    """Every read of IServerDocument.Modified in every unit goes through
    ModifiedFlagOf (or the two diagnostic readers beside it)."""
    allowed = {"ModifiedFlagOf", "ModifiedFlagRaw", "ModifiedFlagTests"}
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
    assert w.MarkLastFlagTests.endswith("ne_zero=1")


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
