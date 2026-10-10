# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_close_document(discard_changes=True) must not deny a discard it made.

Field result, 2026-10-10, bridge 2026.10.08.01, RELEASE_VERIFICATION
section 36 on a scratch sheet. The sheet had been edited (its title bar
showed it modified). ``app_close_document(discard_changes=True)`` closed
it, the file on disk was untouched, so the edits were thrown away, and
the reply said ``discarded: false``.

Why: ``App_CloseDocument`` built the field as ``Discard And WasModified``,
and ``WasModified`` is ``IServerDocument.Modified``, the read the handler's
own header calls "a floor only: that read can miss editor edits". It
missed them, and a ``false`` derived from a floor is a claim the handler
cannot support.

The reply is now three-valued (true: read modified and discarded; null:
discard asked, the read said clean, so unknown; false: discard not
asked), and carries the raw ``modified_read``, ``discard_requested`` and
``file_unchanged_on_disk`` (FileAge before and after the close), which is
the evidence that the discard did not slip through as a save.

The Pascal cannot run here. The decision is lifted out of the comment-free
source and evaluated for the field case.
"""
from __future__ import annotations

import inspect
import re
from pathlib import Path

from tests.pascal_source import load

_APP = Path(__file__).resolve().parents[1] / "scripts" / "altium" / \
    "Application.pas"


def _body() -> str:
    return load(_APP, minimum=10)["App_CloseDocument"]


def _discarded(discard: bool, modified_read: bool):
    """What the handler's reply says for ``discarded``, from its source.

    Reads the If/Else chain that sets DiscardedJson. A build without the
    chain (the old ``BoolToJsonStr(Discard And WasModified)``) is evaluated
    as that expression, so this reproduces the field reply on it.
    """
    body = _body()
    if "BoolToJsonStr(Discard And WasModified)" in body:
        return discard and modified_read
    m = re.search(
        r"If Not Discard Then DiscardedJson := '(\w+)'\s*"
        r"Else If WasModified Then DiscardedJson := '(\w+)'\s*"
        r"Else DiscardedJson := '(\w+)';", body)
    assert m, "the discarded decision is not in a shape this test reads"
    word = m.group(1) if not discard else (
        m.group(2) if modified_read else m.group(3))
    return {"true": True, "false": False, "null": None}[word]


def test_the_field_case_no_longer_says_false():
    """discard asked, title bar modified, Modified read False: the edits
    were lost, so the reply must not say they were not."""
    assert _discarded(discard=True, modified_read=False) is None


def test_a_read_modified_discard_still_says_true():
    assert _discarded(discard=True, modified_read=True) is True


def test_no_discard_asked_says_false():
    assert _discarded(discard=False, modified_read=False) is False


def test_the_reply_carries_the_evidence():
    body = _body()
    for field in ('"discarded":\' + DiscardedJson',
                  '"discard_requested":\' + BoolToJsonStr(Discard)',
                  '"modified_read":\' + BoolToJsonStr(WasModified)',
                  '"file_unchanged_on_disk":\' + UnchangedJson'):
        assert field in body, field


def test_the_disk_check_brackets_the_close():
    body = _body()
    close = body.index("Client.CloseDocument(ServerDoc);")
    before = body.rfind("AgeBefore := FileAge(DiskPath);", 0, close)
    after = body.find("AgeAfter := FileAge(DiskPath);", close)
    assert before >= 0 and after > close
    assert "If (AgeBefore = -1) Or (AgeAfter = -1) Then UnchangedJson := " \
           "'null'" in body, "an unreadable file age must not read as true"


def test_the_discard_still_clears_the_flag_before_closing():
    body = _body()
    clear = body.index("ServerDoc.SetModified(False)")
    assert clear < body.index("Client.CloseDocument(ServerDoc);")


def test_the_tool_docstring_explains_the_three_values():
    from eda_agent.tools import application as app_module

    captured = {}

    class DummyMcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register = getattr(app_module, "register_application_tools")
    register(DummyMcp())
    doc = inspect.getdoc(captured["app_close_document"]) or ""
    for word in ("null", "modified_read", "file_unchanged_on_disk",
                 "discard_requested"):
        assert word in doc, word
