# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A mark that did not take is reported, and a save-all still writes it.

FIELD RESULTS 2026-10-10, live on bridge 2026.10.10.01 (Altium Designer
26.10.1.5), RELEASE_VERIFICATION 37.2 and a scratch-sheet run:

* lib_set_footprint_height wrote three heights. Every reply said
  ``marked_modified: false`` while the library's tab showed '*'.
  lib_save_library then reported ``was_modified: false``. The heights did
  persist, but only because that save writes whatever the flag says.
* obj_modify on a free scratch SchDoc answered ``marked_modified: true``
  (a constant), and the active document read ``modified: false`` fourteen
  seconds later while the title bar showed '*'.

Both documents were FREE documents under a Temp path. MarkDocDirtyByPath
did ``Client.GetDocumentByPath``, exited silently on Nil and swallowed a
failing SetModified, so nothing could tell a mark that took from one that
never found its document; and obj_modify set ``Marked := True`` after it
regardless. With the flag reading clean, app_save_all's default (skip a
clean library) and only_modified (skip a clean sheet) would have dropped
those edits without a word.

Now:

1. MarkDocDirty returns the flag READ BACK and leaves what it found in
   globals that MarkOutcomeJson renders as ``mark`` on every reply that
   claims marked_modified. Its lookup (FindOpenServerDoc) survives a free
   document's bare name and a path spelled with other case or slashes,
   and it also marks and reads the FOCUSED document when that is the same
   file (the object whose tab shows '*').
2. obj_modify / obj_batch_modify report that read-back, with a
   ``save_hint`` naming app_save_all without only_modified (or
   lib_save_library) when it did not register.
3. The flag stays a witness, not the only one: every path a bridge edit
   marks goes on BridgeEditedPaths, and app_save_all writes a recorded
   document even when its flag reads clean, reporting it as
   ``written_by_bridge_record``.

The Pascal cannot run here. The path-matching functions are lifted out of
the comment-free source and EVALUATED on the field's paths; the rest pins
the calls, their order and the reply fields whose absence was the failure.
Every test fails against eec334c (bridge 2026.10.10.01).
"""
from __future__ import annotations

import ntpath
import re
from pathlib import Path

import pytest

from tests.pascal_source import load, strip_comments

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent" / "tools"


def _main():
    return load(_ALTIUM / "Main.pas", minimum=20)


def _generic():
    return load(_ALTIUM / "Generic.pas", minimum=100)


def _library():
    return load(_ALTIUM / "Library.pas", minimum=100)


def _application():
    return load(_ALTIUM / "Application.pas", minimum=12)


def _utils():
    return load(_ALTIUM / "Utils.pas", minimum=20)


def _at(body: str, needle: str) -> int:
    i = body.find(needle)
    assert i != -1, f"not found: {needle!r}"
    return i


# ---------------------------------------------------------------------------
# A tiny evaluator for the straight-line Pascal the path matching is made of
# ---------------------------------------------------------------------------

def _copy(s: str, i: int, n: int) -> str:
    return s[i - 1:i - 1 + n] if i >= 1 else s[:n]


_FUNCS = {
    "UpperCase": lambda s: s.upper(),
    "Trim": lambda s: s.strip(),
    "StringReplace": lambda s, a, b, flags: s.replace(a, b),
    "ExtractFileName": lambda s: ntpath.basename(s),
    "Copy": _copy,
}


def _py_expr(pascal: str) -> str:
    """One Pascal expression as Python. String literals are copied by
    content (``'\\'`` is ONE backslash in Pascal), operators mapped."""
    out, i = [], 0
    while i < len(pascal):
        ch = pascal[i]
        if ch == "'":
            j, buf = i + 1, []
            while True:
                if pascal[j] == "'" and pascal[j + 1:j + 2] == "'":
                    buf.append("'")
                    j += 2
                elif pascal[j] == "'":
                    break
                else:
                    buf.append(pascal[j])
                    j += 1
            out.append(repr("".join(buf)))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    s = "".join(out)
    s = s.replace("<>", " != ")
    s = re.sub(r"(?<![<>!=:])=(?!=)", " == ", s)
    s = re.sub(r"\bOr\b", " or ", s)
    s = re.sub(r"\bAnd\b", " and ", s)
    s = re.sub(r"\bNot\b", " not ", s)
    return s


def _result_expr(func: str) -> str:
    m = re.search(r"Result\s*:=\s*(.+?);", _main()[func], re.S)
    assert m, f"{func} has no Result assignment"
    return m.group(1)


def _compiled():
    """DocPathKey, DocNameKey and LooksAbsolutePath, built from the source
    text. (DocPathsMatch, which matched a bare name against ANY absolute
    path of that name, is gone: review 2026-10-11 found it let the lookup
    return a namesake. The lookup itself is EXECUTED on a fixture
    workspace in tests/test_bridge_record_names_one_file.py.)"""
    ns: dict = dict(_FUNCS)
    for name, arg in (("LooksAbsolutePath", "P"), ("DocPathKey", "P"),
                      ("DocNameKey", "P")):
        expr = _py_expr(_result_expr(name))
        ns[name] = eval(f"lambda {arg}: {expr}", ns)          # noqa: S307
    return ns


_TEMP = r"C:\Users\user\AppData\Local\Temp\scratch\x"


@pytest.mark.parametrize("a, b, same", [
    # The same file spelled with other case and forward slashes.
    (_TEMP.replace("\\", "/").lower() + "/height_copy.PcbLib",
     _TEMP + r"\HEIGHT_COPY.PCBLIB", True),
    # Two different files that share a name are NOT the same file.
    (r"C:\a\X.SchDoc", r"C:\b\X.SchDoc", False),
    # A bare name is not an absolute path's key, whatever its file name.
    ("SCRATCH_A.SchDoc", _TEMP + r"\SCRATCH_A.SchDoc", False),
])
def test_one_key_per_file(a, b, same):
    ns = _compiled()
    assert (ns["DocPathKey"](a) == ns["DocPathKey"](b)) is same


def test_a_bare_name_keys_by_its_file_name():
    ns = _compiled()
    assert ns["DocNameKey"]("SCRATCH_A.SchDoc") == \
        ns["DocNameKey"](_TEMP + r"\scratch_a.schdoc")


def test_a_unc_path_is_absolute():
    ns = _compiled()
    assert ns["LooksAbsolutePath"](r"\\server\share\a.SchDoc")
    assert ns["LooksAbsolutePath"](_TEMP + r"\a.SchDoc")
    assert not ns["LooksAbsolutePath"]("a.SchDoc")


# ---------------------------------------------------------------------------
# 1. The mark says whether it took
# ---------------------------------------------------------------------------

def test_the_mark_returns_the_flag_it_reads_back():
    body = _main()["MarkDocDirty"]
    assert body.startswith("Function MarkDocDirty(FilePath : String) : Boolean;")
    set_at = _at(body, "ServerDoc.SetModified(True);")
    read_at = _at(body, "MarkLastModified := ServerDoc.Modified;")
    assert set_at < read_at, "the flag must be read AFTER it is set"
    assert "Result := MarkLastModified;" in body


def test_a_lookup_miss_is_no_longer_silent():
    """The eec334c body: GetDocumentByPath, ``If ServerDoc = Nil Then
    Exit;``, ``Try ServerDoc.SetModified(True); Except End;``."""
    body = strip_comments(_main()["MarkDocDirty"])
    assert "If ServerDoc = Nil Then Exit;" not in body
    assert "Try ServerDoc.SetModified(True); Except End;" not in body
    assert "MarkLastError := 'SetModified raised" in body
    assert "no open document matches this path" in body
    wrapper = _main()["MarkDocDirtyByPath"]
    assert "MarkDocDirty(FilePath)" in wrapper
    assert "GetDocumentByPath" not in wrapper, (
        "the wrapper must not keep a lookup of its own that can miss")


def test_the_lookup_walks_free_documents_and_falls_back_to_the_focus():
    """Shape only; the behaviour is executed in
    test_bridge_record_names_one_file.py."""
    body = strip_comments(_main()["FindOpenServerDoc"])
    assert "Workspace.DM_FreeDocumentsProject" in body
    assert "NameHits := DocNameCount(FilePath);" in body, (
        "a bare name must be counted over the LISTING before any lookup")
    assert "FindDocVia := 'ambiguous_name';" in body
    assert "DocPathsMatch" not in body, "a name match must never answer an absolute path"
    assert "View.OwnerDocument" in _main()["FocusedServerDoc"]


def test_the_focused_document_is_marked_and_read_too():
    body = strip_comments(_main()["MarkDocDirty"])
    focus = body[_at(body, "Focused := FocusedServerDoc(0);"):]
    assert "Focused.SetModified(True);" in focus
    assert "FocusedMod := Focused.Modified;" in focus
    assert "If FocusedMod Then MarkLastModified := True;" in focus


def test_the_read_consults_the_same_document_the_mark_wrote():
    body = strip_comments(_main()["DocIsModified"])
    assert "FindOpenServerDoc(FilePath)" in body
    assert "FocusedServerDoc(0)" in body
    assert "Client.GetDocumentByPath(FilePath)" not in body, (
        "a read with its own lookup can consult a different object")


def test_the_mark_outcome_is_rendered_for_the_reply():
    body = _utils()["MarkOutcomeJson"]
    for field in ('"found":', '"via":', '"modified_read":',
                  '"focused_modified_read":', '"recorded_for_save_all":',
                  '"problem":', '"file_name":'):
        assert field in body, field
    text = (_ALTIUM / "Utils.pas").read_text(encoding="utf-8")
    assert text.index("Function MarkOutcomeJson(") > \
        text.index("Function EscapeJsonString("), "callee declared first"


def test_every_reply_that_claims_marked_modified_carries_mark():
    missing = []
    for pas in sorted(_ALTIUM.glob("*.pas")):
        if pas.name == "Altium_MCP.pas":
            continue
        for name, body in load(pas, minimum=1).items():
            code = strip_comments(body)
            claims = ('"marked_modified":' in code
                      or "JsonBool('marked_modified'" in code)
            if not claims or name == "Gen_BatchModify":
                continue
            if '"mark":' not in code and "JsonRaw('mark'" not in code:
                missing.append(f"{pas.name}:{name}")
    assert not missing, (
        f"replies claim marked_modified without saying what the mark found: "
        f"{missing}")


# ---------------------------------------------------------------------------
# 2. obj_modify / obj_batch_modify report the read-back
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["ProcessActiveDoc", "ProcessDocByPath"])
def test_obj_modify_reports_the_read_back_not_a_constant(name):
    body = strip_comments(_generic()[name])
    assert "Marked := True;" not in body, (
        "marked_modified is a constant again; it said true on 2026-10-10 "
        "for a sheet that read modified:false")
    # ProcessDocByPath marks the caller's absolute path when it has one
    # (review of c8ee69e): a free sheet's DocumentName is bare.
    assert {"ProcessActiveDoc": "Marked := MarkDocDirty(DocPath);",
            "ProcessDocByPath": "Marked := MarkDocDirty(SchDocMarkPath(DocPath, SchDoc));",
            }[name] in body
    assert "MarkJson := MarkOutcomeJson(0);" in body
    assert "',\"mark\":' + MarkJson + HintJson" in body
    assert "If Not Marked Then" in body
    assert "MarkSaveHint(False, False)" in body
    assert "',\"pending_save\":' + BoolToJsonStr(IsMutating And (TotalMatched > 0))" \
        in body, ("the edit is pending a save whether or not the flag "
                  "registered, and only when the call changed something")


def test_the_save_hint_names_the_routes_that_still_write():
    hint = _utils()["MarkSaveHint"]
    assert "app_save_all WITHOUT only_modified" in hint
    assert "lib_save_library" in hint
    assert "written_by_bridge_record" in hint


def test_batch_rows_carry_each_ops_mark():
    body = strip_comments(_generic()["Gen_BatchModify"])
    assert "MarkedOp := ExtractJsonValue(OpResult, 'marked_modified');" in body
    assert "',\"marked_modified\":' + MarkedOpJson(MarkedOp)" in body
    assert "',\"ops_mark_not_read_back\":' + IntToStr(MarksUnread)" in body
    assert "MarksUnread := 0;" in body
    assert "MarkSaveHint(False, False)" in body


def test_project_scope_counts_only_marks_that_read_back():
    body = strip_comments(_generic()["IterateProjectDocs"])
    assert "If MarkDocDirty(FilePath) Then Inc(SheetsMarked)" in body
    assert "',\"marks_not_read_back\":['" in body


def test_the_height_reply_carries_the_mark():
    body = strip_comments(_library()["Lib_SetFootprintHeight"])
    assert "Marked := MarkDocDirty(LibFile);" in body
    assert "JsonRaw('mark', MarkJson)" in body
    assert "MarkSaveHint(False, True)" in body


def test_lib_save_library_says_whether_the_bridge_edited_it():
    """was_modified read false after three real height writes."""
    body = strip_comments(_library()["Lib_SaveLibrary"])
    assert "BridgeEdited := BridgeEditRecorded(LibPath)" in body
    assert "',\"bridge_edited\":' + BoolToJsonStr(BridgeEdited)" in body
    forget = _at(body, "ForgetBridgeEdit(LibPath);")
    assert _at(body, "(AgeAfter <> AgeBefore)") < forget, (
        "the record may let go only once the file got newer")


# ---------------------------------------------------------------------------
# 3. app_save_all writes what the bridge edited, whatever the flag says
# ---------------------------------------------------------------------------

def test_the_mark_records_the_path_even_when_the_lookup_misses():
    """An ABSOLUTE path goes on the record before the lookup; a bare name
    only when nothing resolved (review 2026-10-11: a bare entry matched
    every file of that name)."""
    body = strip_comments(_main()["MarkDocDirty"])
    early = _at(body, "If LooksAbsolutePath(Trim(FilePath)) Then")
    assert early < _at(body, "RecordBridgeEdit(FilePath);") < \
        _at(body, "FindOpenServerDoc(FilePath)"), (
            "an absolute path the lookup cannot find must still be on the record")
    assert "RecordBridgeEdit(DiskName);" in body


def test_the_record_overrides_a_clean_read_in_the_save_pass():
    body = strip_comments(_main()["SaveOneDocByDocRef"])
    rec = _at(body, "InRecord := BridgeEditRecorded(DocPath) "
                    "Or BridgeEditRecorded(DiskPath);")
    override = _at(body, "If Skip And InRecord Then")
    skip = _at(body, "If Skip Then")
    assert rec < override < skip, (
        "the record must be consulted before the skip decides")
    assert "ByRecord := True;" in body[override:skip]
    assert "WriteDocAndNote(ServerDoc, DiskPath, DocPath, InRecord, ByRecord)" in body
    note = strip_comments(_main()["WriteDocAndNote"])
    write = _at(note, "Try ServerDoc.DoFileSave(''); Except End;")
    moved = _at(note, "Moved := (AgeAfter <> -1) And (AgeAfter <> AgeBefore);")
    assert write < moved < _at(note, "ForgetBridgeEdit(DocPath);")
    assert "PathListWith(SaveBridgeRecordPaths, DiskPath)" in note
    assert "PathListWith(SaveBridgeRecordUnwritten, DiskPath)" in note


def test_save_all_reports_what_it_wrote_from_the_record():
    body = strip_comments(_application()["App_SaveAll"])
    reset = _at(body, "SaveBridgeRecordPaths := '';")
    assert reset < _at(body, "SaveAllDirty(0)")
    for key in ('"written_by_bridge_record":', '"bridge_record_not_written":',
                '"bridge_record_pending":'):
        assert key in body, key
    assert "PipeListToJsonArray(SaveBridgeRecordPaths)" in body
    assert "If (SaveBridgeRecordUnwritten <> '') Or (SaveBridgeRecordClosed <> '') Then" in body
    branches = body.split("Result := BuildSuccessResponse(")[1:]
    assert len(branches) == 5
    for b in branches:
        assert "+ Policy" in b.split(");", 1)[0]


def test_the_record_starts_empty_and_a_close_lets_go():
    pump = load(_ALTIUM / "StatusForm.pas", minimum=10)["StartMCPPump"]
    assert "BridgeEditedPaths := '';" in pump
    close = strip_comments(_application()["App_CloseDocument"])
    assert "ForgetBridgeEdit(FilePath);" in close
    assert _at(close, "Client.CloseDocument(ServerDoc);") < \
        _at(close, "ForgetBridgeEdit(FilePath);")


def test_free_documents_are_aged_by_the_save_check():
    """The field documents were free documents; a pass that wrote only
    them would have read 'not one got newer on disk'."""
    body = strip_comments(_main()["WorkspaceDocPaths"])
    assert "Workspace.DM_FreeDocumentsProject" in body
    assert "FindOpenServerDoc(Path)" in body


def test_helpers_are_declared_before_their_callers():
    text = (_ALTIUM / "Main.pas").read_text(encoding="utf-8")
    order = ["Function LooksAbsolutePath(", "Function DocPathKey(",
             "Function DocNameKey(", "Function ServerDocFileName(",
             "Function FocusedServerDoc(", "Function ListedDocKey(",
             "Function DocNameCount(", "Function FindOpenServerDoc(",
             "Function FileAgeOrNone(", "Function BridgeEntryKey(",
             "Function BridgeEntryStamp(", "Function BridgeRecordWithout(",
             "Function PathListWith(", "Function BridgeEditRecorded(",
             "Procedure RecordBridgeEdit(", "Procedure ForgetBridgeEdit(",
             "Function BridgeRecordKeys(", "Function MarkDocDirty(",
             "Procedure MarkDocDirtyByPath(", "Function DocIsModified(",
             "Function ListedServerDoc(", "Function WriteDocAndNote(",
             "Procedure SaveOneDocByDocRef(", "Function WorkspaceDocPaths(",
             "Procedure SaveAllDirty(", "Function SaveBridgeRecordSweep("]
    at = [text.index(n) for n in order]
    assert at == sorted(at), "DelphiScript has no forward declarations"


# ---------------------------------------------------------------------------
# The tool text a caller reads
# ---------------------------------------------------------------------------

def test_the_tool_docstrings_name_the_new_fields():
    app = (_SRC / "application.py").read_text(encoding="utf-8")
    save_all = app.split("async def app_save_all", 1)[1].split("@mcp.tool()", 1)[0]
    assert "written_by_bridge_record" in save_all
    assert "bridge_record_not_written" in save_all
    gen = (_SRC / "generic.py").read_text(encoding="utf-8")
    modify = gen.split("async def obj_modify(", 1)[1].split("@mcp.tool()", 1)[0]
    assert "save_hint" in modify and "READ BACK" in modify
    batch = gen.split("async def obj_batch_modify(", 1)[1].split("@mcp.tool()", 1)[0]
    assert "ops_mark_not_read_back" in batch
    lib = (_SRC / "library.py").read_text(encoding="utf-8")
    save_lib = lib.split("async def lib_save_library(", 1)[1].split("@mcp.tool()", 1)[0]
    assert "bridge_edited" in save_lib
