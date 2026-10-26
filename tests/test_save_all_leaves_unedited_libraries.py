# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""app_save_all leaves alone a library nobody edited.

The save pass used to flag every open document modified and save it, so
a library opened only to read from or copy out of was rewritten too.
MEASURED: one save-all after copying symbols out of ten user libraries
rewrote all ten, untouched.

A library is now written when it reads modified OR a tool recorded the
edit on the bridge's record (RecordBridgeEdit, reached through
MarkDocDirty / MarkDocDirtyByPath in Main.pas and MarkLibDirty in
Library.pas). Upstream built the same idea with its own helpers
(NoteDocEdited / DocWasEdited / IsLibraryDocPath); the 2026-10-25 merge
kept this fork's record and these tests check its names. The record is
load-bearing: a library edit whose modified flag does not propagate and
that never reaches a recorder is not saved by app_save_all (or by a
detach, which arms the same skip). The last test here is the one that
matters for that reason.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "altium"
MAIN = load(SCRIPTS / "Main.pas")
LIBRARY = load(SCRIPTS / "Library.pas")

#: Writes that change a library's content.
_WRITES = re.compile(
    r"AddSchComponent\(|RemoveSchComponent\(|AddPCBObject\(|RemovePCBObject\("
    r"|DestroyPCBObject\(|AddSchObject\(|RemoveSchObject\(|RegisterComponent\("
    r"|\.Height\s*:=|LibReference\s*:=|\.Name\s*:=", re.I)
#: The handler is working on a library rather than a board or sheet.
_ON_A_LIBRARY = re.compile(
    r"ISch_Lib\b|IPCB_Library|GetCurrentPCBLibrary|eSchLib\b", re.I)
#: Records the edit, or writes the file itself.
_RECORDS = re.compile(
    r"MarkLibDirty|MarkDocDirty|RecordBridgeEdit|DoFileSave|SaveObject",
    re.I)


def _calls(body: str, name: str) -> bool:
    return re.search(r"\b" + re.escape(name) + r"\s*\(", body) is not None


def test_the_save_pass_skips_a_library_no_tool_edited() -> None:
    body = MAIN["SaveOneDocByDocRef"]
    skip = body.find("Skip := SaveSkipCleanLibs")
    save = body.find("WriteDocAndNote(")
    assert skip >= 0 and _calls(body, "BridgeEditRecorded"), (
        "SaveOneDocByDocRef no longer checks whether a library was edited "
        "before saving it, so every library opened to read is rewritten")
    assert save > skip, (
        "the edit check must come before the save, or it decides nothing")
    # A recorded edit overrides the skip, or a library whose flag reads
    # clean after a tool edited it is never written.
    override = body.find("If Skip And InRecord Then")
    assert skip < override < save, (
        "the bridge's record no longer overrides the clean-library skip")


def test_both_dirty_helpers_record_the_edit() -> None:
    assert _calls(MAIN["MarkDocDirtyByPath"], "MarkDocDirty")
    assert _calls(MAIN["MarkDocDirty"], "RecordBridgeEdit")
    assert _calls(LIBRARY["MarkLibDirty"], "RecordBridgeEdit")


def test_library_pas_never_flags_a_document_behind_the_record() -> None:
    """A bare SetModified(True) marks the document without recording the
    edit, so app_save_all skips it. Allowed only right before a save."""
    offenders = []
    for name, body in LIBRARY.items():
        if name == "MarkLibDirty":
            continue
        for m in re.finditer(r"SetModified\(True\)", body):
            if "DoFileSave(" not in body[m.end():m.end() + 200]:
                offenders.append(name)
    assert not offenders, (
        f"SetModified(True) without a save in {sorted(set(offenders))}. "
        f"Call MarkDocDirtyByPath instead, which records the edit")


def _edits_without_recording() -> list[str]:
    recorders = {n for n, b in LIBRARY.items() if _RECORDS.search(b)}
    grew = True
    while grew:
        grew = False
        for n, b in LIBRARY.items():
            if n not in recorders and any(_calls(b, r) for r in recorders):
                recorders.add(n)
                grew = True
    return sorted(n for n, b in LIBRARY.items()
                  if n.startswith("Lib_") and _ON_A_LIBRARY.search(b)
                  and _WRITES.search(b) and n not in recorders)


def test_the_scan_finds_library_editors() -> None:
    """Floor: a pattern that stops matching would pass over everything."""
    editors = [n for n, b in LIBRARY.items()
               if n.startswith("Lib_") and _ON_A_LIBRARY.search(b)
               and _WRITES.search(b)]
    assert len(editors) >= 20, (
        f"only {len(editors)} library editors found; the scan has stopped "
        f"working and the check below examines nothing")


def test_every_library_edit_is_recorded() -> None:
    missing = _edits_without_recording()
    assert not missing, (
        f"{missing} change a library without MarkLibDirty, MarkDocDirty "
        f"or RecordBridgeEdit, so app_save_all leaves the edit unsaved")


def test_the_reply_names_the_libraries_it_left() -> None:
    app = load(SCRIPTS / "Application.pas", minimum=10)
    assert "libraries_left_unedited" in app["App_SaveAll"]
