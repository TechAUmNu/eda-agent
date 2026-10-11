# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The shared CHECK-FIRST helpers, executed against fixtures.

WHY THESE EXIST. Altium's DelphiScript engine shows an exception as a
MODAL 'press OK' dialog BEFORE the surrounding Try/Except runs (measured;
recorded beside WriteFileContent and StrToFloatDef in the sources). A Try
around a call that raises on an ordinary bad input is therefore not a
guard: the user gets a popup, and the polling loop waits on it. The cure
is to stop the exception from happening - ask first - and these are the
questions every unit asks:

* RecordCaughtException + its globals (Main.pas, at the top of the first
  unit so EVERY routine can call it): counts a catch that was not a
  genuine fault and stamps it with the command in flight, so a live run can
  see that a modal fired even when somebody clicked it away.
* AltiumMajorVersion, CfgIntOk, FocusedDocKindIs, DocumentPresent,
  OpenDocumentIfPresent, PcbPathOpenable, PcbEditorDocOpen,
  FindDMDocumentByPath (Main.pas).
* IsInt32Str, TargetDirExists, LayerIdIsSignal, IsElectricalLayerId and
  the IsFloatStr tightening (Utils.pas).

Each test EXECUTES the real Pascal through tests/pascal_eval.py and fails
on the code before these helpers (the helper is missing, or IsFloatStr
accepted '1e999', which StrToFloat then raised on).
"""
from __future__ import annotations

import importlib.util
import ntpath
import random
import re
import sys
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load, strip_comments
from tests.live_2026_10_11 import file_reader

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


def _main():
    return load(_ALTIUM / "Main.pas", minimum=40)


def _utils():
    return load(_ALTIUM / "Utils.pas", minimum=40)


# ---------------------------------------------------------------------------
# Delphi RTL models the bodies call
# ---------------------------------------------------------------------------

def _delphi_trim(s: str) -> str:
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


def _extract_file_path(s: str) -> str:
    """SysUtils.ExtractFilePath: up to and including the last '\\' or ':'."""
    k = max(s.rfind("\\"), s.rfind(":"))
    return s[:k + 1]


def _is_int_str(s: str) -> bool:
    """Utils.pas IsIntStr (indexes S[1], outside the evaluator's subset)."""
    t = _delphi_trim(s)
    if t == "":
        return False
    body = t[1:] if t[0] == "-" else t
    return body != "" and all("0" <= c <= "9" for c in body)


# ---------------------------------------------------------------------------
# 1. RecordCaughtException and its globals
# ---------------------------------------------------------------------------

def test_record_caught_exception_counts_and_names_the_command():
    log: list[str] = []
    ns = {"AppendLog": log.append, "FormatLogStamp": lambda d: "STAMP"}
    build(_main(), ["ResetCaughtExceptions", "RecordCaughtException"], ns)
    ns["CaughtExceptionCount"] = 41          # an uninitialised leftover
    ns["CurrentCommand"] = "stale"
    ns["ResetCaughtExceptions"](0)
    assert ns["CaughtExceptionCount"] == 0
    assert ns["CurrentCommand"] == "" and ns["CurrentRequestId"] == ""
    assert ns["LastCaughtStamp"] == ""

    ns["CurrentCommand"] = "pcb.normalize_vias"
    ns["CurrentRequestId"] = "abc123"
    ns["RecordCaughtException"]("PCB_NormalizeVias:PreferedWidth")
    ns["RecordCaughtException"]("PCB_NormalizeVias:PreferedWidth")
    assert ns["CaughtExceptionCount"] == 2
    assert ns["LastCaughtWhere"] == "PCB_NormalizeVias:PreferedWidth"
    assert ns["LastCaughtCommand"] == "pcb.normalize_vias"
    assert ns["LastCaughtRequestId"] == "abc123"
    assert ns["LastCaughtStamp"] == "STAMP"
    assert log[-1] == ("STAMP,0,_caught_exception,"
                       "PCB_NormalizeVias:PreferedWidth,pcb.normalize_vias,"
                       "abc123")


def test_the_counter_is_callable_from_every_routine_in_every_unit():
    """Main.pas compiles first (build.py and the .PrjScr agree), and inside
    it RecordCaughtException must sit above the first routine that has an
    Except, so no routine anywhere has to do without it. The globals live
    in Main's Var block, before any routine."""
    spec = importlib.util.spec_from_file_location(
        "_altium_build_order", _ALTIUM / "lint.py")
    lint = importlib.util.module_from_spec(spec)
    sys.modules["_altium_build_order"] = lint
    spec.loader.exec_module(lint)
    assert lint.PAS_FILES[0] == "Main.pas"
    prj = (_ALTIUM / "Altium_API.PrjScr").read_text(encoding="utf-8-sig")
    docs = re.findall(r"(?m)^DocumentPath=(\S+)", prj)
    assert docs[0] == "Main.pas", docs

    text = strip_comments((_ALTIUM / "Main.pas").read_text(encoding="utf-8-sig"))
    var_block = text[:re.search(r"(?m)^(?:Function|Procedure)\s", text).start()]
    for g in ("CurrentCommand", "CurrentRequestId", "CaughtExceptionCount",
              "LastCaughtWhere", "LastCaughtCommand", "LastCaughtRequestId",
              "LastCaughtStamp", "AltiumMajorCache", "LastCreatedLibDoc",
              "LastCreatedLibPath"):
        assert re.search(rf"(?m)^\s+{g}\s*:", var_block), g

    order = list(_main())
    rec = order.index("RecordCaughtException")
    loggers = {"AppendLog", "FormatLogStamp"}
    for name in order[:rec]:
        if name in loggers:
            continue
        assert not re.search(r"\bExcept\b", _main()[name], re.I), (
            f"{name} has an Except but sits above RecordCaughtException, "
            "so it cannot record what it catches")


# ---------------------------------------------------------------------------
# 2. AltiumMajorVersion
# ---------------------------------------------------------------------------

class _Client:
    def __init__(self, version=None, raises=False):
        self.version, self.raises, self.reads = version, raises, 0

    @property
    def GetProductVersion(self):
        self.reads += 1
        if self.raises:
            raise PascalError("no version API")
        return self.version


def _major(client):
    ns = {"Client": client, "AltiumMajorCache": "", "StrToInt": int}
    build(_main(), ["AltiumMajorVersion"], ns)
    return ns


@pytest.mark.parametrize("ver,want", [
    ("26.10.1.6", 26), ("19.0.15", 19), ("  18.1.9 ", 18), ("26", 26),
    ("", -1), ("Altium 26", -1), ("12345.1", -1), (".5", -1)])
def test_altium_major_version_parses_the_leading_number(ver, want):
    assert _major(_Client(ver))["AltiumMajorVersion"](0) == want


def test_an_unreadable_version_is_unknown_and_read_once():
    ns = _major(_Client(raises=True))
    assert ns["AltiumMajorVersion"](0) == -1
    ns2 = _major(_Client("26.10.1.6"))
    assert ns2["AltiumMajorVersion"](0) == 26
    assert ns2["AltiumMajorVersion"](0) == 26
    assert ns2["Client"].reads == 1, "the version must be read once and cached"


# ---------------------------------------------------------------------------
# 3. CfgIntOk (Main.pas cannot see IsIntStr)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("v,want", [
    ("10", True), ("600000", True), ("0", True), ("-5", True), ("+7", True),
    ("999999999", True), ("000000000000005", True),
    ("1000000000", False), ("99999999999", False), ("", False), ("-", False),
    ("+", False), ("12a", False), ("1.5", False), ('"10"', False),
    (" 10", True), ("  -3", True), ("   ", False), ("10 ", False), ("$FF", False), ("null", False)])
def test_cfg_int_ok_admits_only_what_strtoint_takes_and_fits(v, want):
    ns = build(_main(), ["CfgIntOk"], {})
    assert ns["CfgIntOk"](v) is want


# ---------------------------------------------------------------------------
# 4. IsInt32Str
# ---------------------------------------------------------------------------

def _int32():
    return build(_utils(), ["IsInt32Str"], {"IsIntStr": _is_int_str})["IsInt32Str"]


@pytest.mark.parametrize("s,want", [
    ("2147483647", True), ("2147483648", False), ("-2147483648", True),
    ("-2147483649", False), ("00000000012", True), ("0000002147483648", False),
    ("99999999999", False), ("0", True), ("-0", True), ("12", True),
    ("abc", False), ("", False), ("-", False), ("1.0", False),
    (" 42 ", True)])
def test_is_int32_str_bounds_the_value_not_just_the_syntax(s, want):
    assert _int32()(s) is want


def test_is_int32_str_agrees_with_the_int32_range():
    f = _int32()
    rnd = random.Random(7)
    for _ in range(4000):
        n = rnd.choice([rnd.randint(-2**31 - 50, -2**31 + 50),
                        rnd.randint(2**31 - 50, 2**31 + 50),
                        rnd.randint(-10**12, 10**12)])
        s = str(n)
        assert f(s) is (-2**31 <= n <= 2**31 - 1), s
        padded = ("-" if n < 0 else "") + "000" + str(abs(n))
        assert f(padded) is (-2**31 <= n <= 2**31 - 1), padded


# ---------------------------------------------------------------------------
# 5. IsFloatStr: nothing StrToFloat would overflow on
# ---------------------------------------------------------------------------

def _is_float():
    return build(_utils(), ["IsFloatStr"], {})["IsFloatStr"]


def _converts(s: str) -> bool:
    return abs(float(s)) != float("inf")


@pytest.mark.parametrize("s", [
    "1e999", "1E400", "1" * 400, "4e308", "1e1000000", "0.5e310",
    "9" * 309 + ".5"])
def test_a_value_strtofloat_overflows_on_is_refused(s):
    assert _is_float()(s) is False, s


@pytest.mark.parametrize("s", [
    "1.5", "-2", "+3.0e10", "1e05", "1e005", "1e307", "9.9e307", "0.5e308",
    ".05e309", "1" * 308, "0." + "0" * 10 + "1e317", "0e999", "0.0e999",
    "00001e307", " 7 ", "0." + "0" * 60 + "1"])
def test_every_value_that_converts_still_passes(s):
    assert _converts(s)
    assert _is_float()(s) is True, s


def test_nothing_accepted_overflows():
    f = _is_float()
    rnd = random.Random(11)
    alpha = "0123456789.eE"
    for _ in range(20000):
        s = "".join(rnd.choice(alpha) for _ in range(rnd.randint(1, 8)))
        if f(s):
            assert _converts(s), s


# A SIGNED exponent (2026-10-11). Python's json writes small floats as
# '1e-05'; the loop used to meet the sign after 'e' as a non-number and exit,
# so the caller's Default silently replaced the value.
@pytest.mark.parametrize("s", [
    "1e-05", "1e-5", "1E+5", "-1.5e-03", "+2.5E-307", "1e+307", "0e-999",
    "0.0e-5", "0." + "0" * 5 + "1e-300", "12.5e-2"])
def test_a_signed_exponent_is_accepted(s):
    assert _converts(s)
    assert _is_float()(s) is True, s


@pytest.mark.parametrize("s", [
    "1e-", "1e+", "1e-+5", "1e--5", "1e5-", "1-5", "-e5", "1e-5.5", "e-5",
    "1e-5e2"])
def test_a_misplaced_sign_is_still_refused(s):
    assert _is_float()(s) is False, s


@pytest.mark.parametrize("s", ["1e-308", "1e-400", "5e-324", "0.01e-306",
                               "1e-1000000"])
def test_an_underflowing_value_is_refused(s):
    """Order -308 or below (under the smallest normal Double) is refused,
    the mirror of the order-308 overflow ceiling: StrToFloat's underflow
    behaviour in this engine is unmeasured, so the Default stands."""
    assert _is_float()(s) is False, s


def test_nothing_accepted_with_signs_overflows_or_underflows():
    f = _is_float()
    rnd = random.Random(29)
    alpha = "0123456789.eE+-"
    for _ in range(20000):
        s = "".join(rnd.choice(alpha) for _ in range(rnd.randint(1, 9)))
        if f(s):
            v = float(s)
            assert abs(v) != float("inf"), s
            assert v == 0.0 or abs(v) >= 1e-307, s

# ---------------------------------------------------------------------------
# 6. Layer classes
# ---------------------------------------------------------------------------

_LAYERS = {"eTopLayer": 1, "eMidLayer1": 2, "eMidLayer30": 31,
           "eBottomLayer": 32, "eTopOverlay": 33, "eTopSolder": 37,
           "eBottomSolder": 38, "eInternalPlane1": 39,
           "eInternalPlane16": 54, "eDrillGuide": 55, "eMechanical1": 57}


def test_layer_classes():
    ns = build(_utils(), ["LayerIdIsSignal", "IsElectricalLayerId"],
               dict(_LAYERS))
    sig, cu = ns["LayerIdIsSignal"], ns["IsElectricalLayerId"]
    for name, lid in _LAYERS.items():
        signal = 1 <= lid <= 32
        plane = 39 <= lid <= 54
        assert sig(lid) is signal, name
        assert cu(lid) is (signal or plane), name
    assert sig(0) is False and cu(0) is False          # eNoLayer


# ---------------------------------------------------------------------------
# 7. TargetDirExists
# ---------------------------------------------------------------------------

def test_target_dir_exists():
    dirs = {"C:\\proj\\", "\\\\srv\\share\\"}
    ns = build(_utils(), ["TargetDirExists"], {
        "ExtractFilePath": _extract_file_path,
        "DirectoryExists": lambda d: d in dirs})
    f = ns["TargetDirExists"]
    assert f("C:\\proj\\new.PrjPcb") is True
    assert f("\\\\srv\\share\\a.SchDoc") is True
    assert f("C:\\no_such_dir\\x.SchDoc") is False
    assert f("bare.PrjPcb") is True        # no folder to check: as today
    assert f("") is False and f("   ") is False


# ---------------------------------------------------------------------------
# 8. Documents: present, opened only when present, PCB paths
# ---------------------------------------------------------------------------

class _Docs:
    """Client's document calls. OpenDocument on a document that is neither
    open nor on disk RAISES, the modal the helpers exist to avoid."""

    def __init__(self, open_paths=(), disk=()):
        self.open = {p.upper(): object() for p in open_paths}
        self.disk = {p.upper() for p in disk}
        self.opened: list[str] = []

    def GetDocumentByPath(self, p):
        return self.open.get(p.upper())

    def OpenDocument(self, kind, p):
        self.opened.append(p)
        if p.upper() not in self.open and p.upper() not in self.disk:
            raise PascalError("file not found")
        return self.open.get(p.upper()) or ("doc", kind, p)

    def file_exists(self, p):
        return p.upper() in self.disk


def _doc_ns(client):
    ns = {"Client": client, "FileExists": client.file_exists,
          "ExtractFileExt": lambda s: ntpath.splitext(s)[1]}
    ns.update(file_reader())               # every file on disk reads as a board
    return build(_main(), ["DocumentPresent", "FileLooksLoadable",
                           "UnopenedFileUnloadable", "OpenDocumentIfPresent",
                           "PcbPathOpenable"], ns)


def test_a_missing_document_is_never_handed_to_open_document():
    c = _Docs(open_paths=[r"C:\b\Unsaved.PcbDoc"], disk=[r"C:\b\Board.PcbDoc"])
    ns = _doc_ns(c)
    assert ns["OpenDocumentIfPresent"]("PCB", r"C:\b\Gone.PcbDoc") is None
    assert ns["OpenDocumentIfPresent"]("PCB", "") is None
    assert c.opened == [], "OpenDocument was called on a missing document"
    assert ns["OpenDocumentIfPresent"]("PCB", r"C:\b\Board.PcbDoc") is not None
    assert ns["OpenDocumentIfPresent"]("PCB", r"C:\b\Unsaved.PcbDoc") is not None
    assert ns["DocumentPresent"](r"C:\b\Unsaved.PcbDoc") is True   # open, no file
    assert ns["DocumentPresent"](r"C:\b\Gone.PcbDoc") is False


def test_pcb_path_openable():
    c = _Docs(open_paths=[r"C:\b\Unsaved.PcbDoc"],
              disk=[r"C:\b\Board.PcbDoc", r"C:\b\Sheet.SchDoc", r"C:\b\Old.pcb"])
    f = _doc_ns(c)["PcbPathOpenable"]
    assert f(r"C:\b\Board.PcbDoc") is True
    assert f(r"C:\b\board.PCBDOC") is True
    assert f(r"C:\b\Unsaved.PcbDoc") is True
    assert f(r"C:\b\Old.pcb") is True
    assert f(r"C:\b\Sheet.SchDoc") is False        # not a board
    assert f(r"C:\nope.PcbDoc") is False           # not present
    assert f("") is False


# ---------------------------------------------------------------------------
# 9. Workspace fixtures: FocusedDocKindIs, PcbEditorDocOpen, FindDMDocument
# ---------------------------------------------------------------------------

class _Doc:
    def __init__(self, kind, full):
        self.DM_DocumentKind = kind
        self._full = full

    @property
    def DM_FullPath(self):
        if isinstance(self._full, Exception):
            raise self._full
        return self._full


class _Proj:
    def __init__(self, docs):
        self.docs = docs

    @property
    def DM_LogicalDocumentCount(self):
        return len(self.docs)

    def DM_LogicalDocuments(self, i):
        return self.docs[i]


class _Ws:
    def __init__(self, projects=(), free=(), focused=None, free_raises=False):
        self.projects = list(projects)
        self._free = _Proj(list(free))
        self.DM_FocusedDocument = focused
        self.free_raises = free_raises

    @property
    def DM_ProjectCount(self):
        return len(self.projects)

    def DM_Projects(self, i):
        return self.projects[i]

    @property
    def DM_FreeDocumentsProject(self):
        if self.free_raises:
            raise PascalError("no free documents project")
        return self._free


def test_focused_doc_kind_is():
    def f(ws, kind):
        return build(_main(), ["FocusedDocKindIs"],
                     {"GetWorkspace": ws})["FocusedDocKindIs"](kind)
    lib = _Doc("PCBLIB", r"C:\l\a.PcbLib")
    assert f(_Ws(focused=lib), "PCBLIB") is True
    assert f(_Ws(focused=lib), "pcblib") is True
    assert f(_Ws(focused=lib), "PCB") is False
    assert f(_Ws(focused=None), "PCBLIB") is False
    assert f(None, "PCBLIB") is False


def _editor_open(ws, open_paths=()):
    c = _Docs(open_paths=open_paths)
    ns = build(_main(), ["PcbEditorDocOpen"], {"GetWorkspace": ws, "Client": c})
    return ns["PcbEditorDocOpen"](0)


def test_pcb_editor_doc_open():
    board = _Doc("PCB", r"C:\p\Board.PcbDoc")
    sheet = _Doc("SCH", r"C:\p\Top.SchDoc")
    assert _editor_open(None) is False
    assert _editor_open(_Ws([_Proj([sheet])])) is False
    # A listed member that is CLOSED does not count ...
    assert _editor_open(_Ws([_Proj([sheet, board])])) is False
    # ... an open one does,
    assert _editor_open(_Ws([_Proj([sheet, board])]),
                        open_paths=[r"C:\p\Board.PcbDoc"]) is True
    # and so does any free PCB / PCB library (bare listing, open by definition).
    assert _editor_open(_Ws([_Proj([sheet])],
                            free=[_Doc("PCBLIB", "Lib.PcbLib")])) is True
    assert _editor_open(_Ws(free=[_Doc("SCHLIB", "Lib.SchLib")])) is False
    assert _editor_open(_Ws([_Proj([board])], free_raises=True)) is False
    # A member whose path will not read is skipped, not fatal.
    assert _editor_open(_Ws([_Proj([_Doc("PCB", PascalError("x")), board])]),
                        open_paths=[r"C:\p\Board.PcbDoc"]) is True


def _find(ws, path):
    ns = {"GetWorkspace": ws}
    build(_main(), ["LooksAbsolutePath", "DocPathKey", "FindDMDocumentByPath"], ns)
    return ns["FindDMDocumentByPath"](path)


def test_find_dm_document_by_path_never_answers_with_a_namesake():
    member = _Doc("SCH", r"H:\proj\Top.SchDoc")
    other = _Doc("SCH", r"H:\other\Top.SchDoc")
    free_bare = _Doc("SCH", "Top.SchDoc")
    free_abs = _Doc("SCH", r"C:\tmp\Scratch.SchDoc")
    ws = _Ws([_Proj([other]), _Proj([member])], free=[free_bare, free_abs])
    assert _find(ws, r"H:\proj\Top.SchDoc") is member
    assert _find(ws, "h:/proj/top.schdoc") is member       # one key per file
    assert _find(ws, r"C:\tmp\Scratch.SchDoc") is free_abs  # free documents too
    assert _find(ws, r"C:\tmp\Top.SchDoc") is None          # bare listing: no guess
    assert _find(ws, "Top.SchDoc") is None                  # bare request: no guess
    assert _find(ws, "") is None
    assert _find(None, r"H:\proj\Top.SchDoc") is None
    assert _find(_Ws([_Proj([member])], free_raises=True),
                 r"H:\nope\X.SchDoc") is None


# ---------------------------------------------------------------------------
# 10. Structural promises
# ---------------------------------------------------------------------------

_DM_ONLY = ["FocusedDocKindIs", "DocumentPresent", "OpenDocumentIfPresent",
            "FileLooksLoadable", "UnopenedFileUnloadable", "PcbPathOpenable", "PcbEditorDocOpen", "FindDMDocumentByPath",
            "AltiumMajorVersion"]


@pytest.mark.parametrize("name", _DM_ONLY)
def test_the_pre_checks_never_reference_an_editor_server(name):
    """Merely REFERENCING PCBServer when the PCB editor server is not
    loaded is the uncatchable modal these checks exist to avoid."""
    body = _main()[name]
    assert not re.search(r"\b(PCBServer|SchServer)\b", body), name


_MAIN_HELPERS = ["ResetCaughtExceptions", "RecordCaughtException",
                 "AltiumMajorVersion", "CfgIntOk"] + _DM_ONLY[:-1]


def test_main_helpers_call_nothing_from_a_later_unit():
    """Main.pas compiles FIRST. A Main.pas helper calling a routine defined
    only in Utils.pas or later is an undeclared identifier at run time -
    the very modal this work removes - and the per-unit call-before-
    definition lint cannot see it."""
    main = _main()
    later: set[str] = set()
    for unit in ("Utils.pas", "Application.pas", "Project.pas", "Library.pas",
                 "PCBGeneric.pas", "PCB.pas", "Generic.pas", "Audit.pas",
                 "StatusForm.pas", "Dispatcher.pas"):
        later |= set(load(_ALTIUM / unit, minimum=1))
    later -= set(main)
    later = {n for n in later if n.lower() not in {
        "strtointdef", "strtofloatdef", "strtobool"}}    # RTL builtins too
    for name in _MAIN_HELPERS:
        body = main[name]
        for callee in later:
            assert not re.search(rf"(?<![.\w]){callee}\s*\(", body), (
                f"{name} calls {callee}, which Main.pas cannot see")
