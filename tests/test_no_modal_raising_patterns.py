# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Ratchet: the constructs that raise an Altium MODAL may only get rarer.

WHY. Altium's DelphiScript engine shows an exception as a modal 'press OK'
dialog BEFORE the surrounding Try/Except runs (measured; recorded in
Main.pas beside WriteFileContent). A Try is therefore not a guard: the
only cure is not to raise, by checking first. The popup-free work
(2026.10.11.01) removed the recurring raising constructs it found; this
scan counts every one of them across scripts/altium/*.pas, with comments
and string literals stripped, against a per-pattern baseline equal to the
count left after that work.

THE RULE. A count above its baseline is a new raising construct: guard it
(check first, and make the false branch do what the Except did) instead
of raising the baseline. A count BELOW its baseline means one was fixed:
lower the baseline in the same change, so the ratchet stays tight. A
baseline entry that is not zero says why the remaining sites are allowed.

"Guarded" is judged per routine: the named check must appear in the same
routine BEFORE the construct. That is a heuristic, deliberately strict in
one direction only - it can miss a guard written another way (the
baseline then carries it, with its reason), never invent one.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"

_STRING = re.compile(r"'(?:[^']|'')*'")


def _clean(body: str) -> str:
    """A comment-free body with every string literal emptied, so a message
    that NAMES a construct is never counted as using it."""
    return _STRING.sub("''", body)


def _routines(altium: Path) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for path in sorted(altium.glob("*.pas")):
        if path.name == "Altium_MCP.pas":   # the built bundle: a duplicate
            continue
        out[path.name] = {n: _clean(b)
                          for n, b in load(path, minimum=1).items()}
    return out


def _unguarded(body: str, construct: str, guard: str | None) -> int:
    """Occurrences of ``construct`` with no ``guard`` earlier in ``body``."""
    hits = 0
    for m in re.finditer(construct, body):
        if guard is None or not re.search(guard, body[:m.start()]):
            hits += 1
    return hits


def _call_arg(body: str, open_paren: int) -> str:
    """The text from ``open_paren`` (just past a ``(``) to its match."""
    depth, i = 0, open_paren
    while i < len(body):
        c = body[i]
        if c == "(":
            depth += 1
        elif c == ")":
            if depth == 0:
                break
            depth -= 1
        i += 1
    return body[open_paren:i].strip()


def _conversion_unguarded(name, body):
    """A conversion is guarded only by a range-safe check OF ITS OWN
    ARGUMENT, made after the argument was last assigned. A check of some
    other variable earlier in the routine proves nothing about this one
    (re-review: any earlier IsFloatStr used to mark every later
    conversion guarded)."""
    hits = 0
    for m in re.finditer(
            r"\b(?:StrToInt|StrToInt64|StrToFloat)\s*\(\s*(?!\d+\s*\))", body):
        arg = _call_arg(body, m.end())
        before = body[:m.start()]
        esc = re.escape(arg)
        checks = [g.start() for g in re.finditer(
            r"\b(?:IsInt32Str|CfgIntOk|IsFloatStr)\s*\(\s*" + esc + r"\s*\)",
            before)]
        sets = [a.start() for a in re.finditer(
            r"(?<![\w.])" + esc + r"\s*:=", before)]
        if not checks or (sets and sets[-1] > checks[-1]):
            hits += 1
    return hits


# The only shapes that count as having asked before a bare
# PCBServer.GetCurrentPCBLibrary (re-review: a guard ANYWHERE earlier in the
# routine, even in a sibling If branch, used to count):
#   1. the gate on the SAME statement:  If PcbLibMayBeCurrent[At](..) Then V :=
#   2. the gate's early exit (PCB_RenumberPads):
#        If Not PcbLibMayBeCurrent[At](..) Then Begin Result := ...; Exit; End;
#   3. a SUCCESSFUL ResolveLibFootprint immediately before, with its Nil
#      exit between: it asks the gated lookup itself and answers non-Nil
#      only after GetCurrentPCBLibrary answered, so the server is loaded and
#      the same lookup cannot raise.
_PCBLIB_SAME_STATEMENT = re.compile(
    r"\bPcbLibMayBeCurrent(?:At)?\s*\([^()]*\)\s+Then\s+\w+\s*:=\s*$")
_PCBLIB_EARLY_EXIT = re.compile(
    r"\bIf\s+Not\s+PcbLibMayBeCurrent(?:At)?\s*\([^()]*\)\s+Then\s+Begin\s+"
    r"Result\s*:=\s*BuildErrorResponse\s*\([^;]*\)\s*;\s*Exit\s*;\s*End\s*;")
_PCBLIB_AFTER_RESOLVE = re.compile(
    r"(\w+)\s*:=\s*ResolveLibFootprint\s*\(\s*Params\s*,\s*ErrCode\s*,\s*Why\s*\)"
    r"\s*;\s*If\s+\1\s*=\s*Nil\s+Then\s+Begin\s+Result\s*:=\s*BuildErrorResponse"
    r"\s*\(\s*RequestId\s*,\s*ErrCode\s*,\s*Why\s*\)\s*;\s*Exit\s*;\s*End\s*;"
    r"\s*\w+\s*:=\s*$")


def _pcblib_unguarded(name, body):
    hits = 0
    for m in re.finditer(r"\bPCBServer\s*\.\s*GetCurrentPCBLibrary\b", body):
        before = body[:m.start()]
        if (_PCBLIB_SAME_STATEMENT.search(before)
                or _PCBLIB_EARLY_EXIT.search(before)
                or _PCBLIB_AFTER_RESOLVE.search(before)):
            continue
        hits += 1
    return hits


def _rule(construct, guard=None, routines=None):
    def count(name, body):
        if routines is not None and name not in routines:
            return 0
        return _unguarded(body, construct, guard)
    return count


# Each pattern: (name, why it raises, counter, {file: allowed count, why}).
# The why-strings are the test's documentation; keep them true.
PATTERNS = [
    ("conversion_without_range_check",
     "StrToInt / StrToInt64 / StrToFloat raise EConvertError on text that "
     "does not parse, is out of range or carries a control character, and "
     "the conversion error is a modal. IsIntStr is a SYNTAX test only "
     "(99999999999 passed it); the range-safe checks are IsInt32Str, "
     "CfgIntOk and IsFloatStr, and each guards only a conversion OF THE "
     "ARGUMENT IT CHECKED. Use StrToIntDef / StrToFloatDef.",
     _conversion_unguarded,
     {"Main.pas": (1, "AltiumMajorVersion converts the digits-only string "
                      "of at most four characters it builds just above"),
      "Utils.pas": (1, "StrToFloatDef converts Work, the locale-reshaped "
                       "copy of S built only after IsFloatStr(S) and the "
                       "control-character refusal passed"),
      "PCB.pas": (7, "PCB_RenumberPads re-reads indices and coordinates it "
                     "wrote into its own TStringLists with IntToStr")}),

    ("undeclared_ilayer",
     "ILayer is not part of the script binding: ILayer.IsSignalLayer raised "
     "an undeclared-identifier modal per layer (audit via antennas). Use "
     "LayerIdIsSignal / IsElectricalLayerId (Utils.pas).",
     _rule(r"\bILayer\s*\.|\.IsSignalLayer\b"),
     {}),

    ("stack_edit_without_stack_walk",
     "LayerObject_V7[id] answers an object for ANY id (mechanical, overlay, "
     "unused mid layers), so it cannot show absence; InsertLayer on a layer "
     "the stack already holds, or RemoveFromStack on one it does not, "
     "raised. Ask LayerInStackWalk (PCB.pas) first.",
     _rule(r"\.(?:InsertLayer|RemoveFromStack)\s*\(", r"\bLayerInStackWalk\s*\("),
     {}),

    ("open_document_unchecked_path",
     "Client.OpenDocument on a path that is neither open nor on disk raises "
     "Altium's file error (a modal) before the Except. Ask DocumentPresent "
     "/ PcbPathOpenable / FileExists first, or use OpenDocumentIfPresent.",
     _rule(r"\bClient\s*\.\s*OpenDocument\s*\(",
           r"\b(?:DocumentPresent|PcbPathOpenable|FileExists)\s*\("),
     {}),

    ("text_file_io_unchecked",
     "AssignFile + Rewrite / Append / Reset raise EInOutError (a modal) on a "
     "missing folder, a bad name or a locked file. Check TargetDirExists / "
     "FileExists / DirectoryExists first.",
     _rule(r"(?<![.\w])(?:AssignFile|Rewrite|Append|Reset)\s*\(",
           r"\b(?:TargetDirExists|FileExists|DirectoryExists)\s*\("),
     {"Main.pas": (1, "AppendLog's AssignFile on activity.log in the "
                      "bridge's own workspace folder (Append only when the "
                      "file exists): no non-raising open is proven in this "
                      "engine, and only an exclusive opener trips it"),
      "PCB.pas": (1, "LmTrace, the same pattern for layout_trace.log in "
                     "the workspace folder"),
      "Dispatcher.pas": (2, "StopMCPServer: dead code, never dispatched")}),

    ("schserver_without_nil_check",
     "SchServer is Nil in a session where the schematic server never "
     "loaded, and a member read on it is a modal. The application handlers "
     "run with no document established, so they test SchServer <> Nil "
     "first (the inventory flagged Application.pas).",
     _rule(r"\bSchServer\s*\.", r"\bSchServer\s*(?:<>|=)\s*Nil\b",
           routines={"App_GetActiveDocument", "App_GetPreferences",
                     "App_ExitActiveCommand"}),
     {}),

    ("pcblib_lookup_before_focus_check",
     "Naming PCBServer before any PcbDoc / PcbLib has opened in the session "
     "raises an uncatchable undeclared identifier. A focused SchLib or "
     "SchDoc satisfies the handlers' own focus checks, so every lookup asks "
     "PcbLibMayBeCurrent / PcbLibMayBeCurrentAt (Main.pas) ON THE SAME "
     "STATEMENT, exits early on it (PCB_RenumberPads), or directly follows "
     "a successful ResolveLibFootprint, which asked the gated lookup itself.",
     _pcblib_unguarded,
     {}),

    ("model_factory_unchecked_format",
     "ModelFactory_FromFilename on a format the loader cannot read can "
     "raise inside the PCB transaction. Ask Model3DExtMayLoad (a DENY-list: "
     "it refuses only what is certainly not a model) first, answer the "
     "Model = Nil reply, and count a raise in the call's own Except.",
     _rule(r"\.ModelFactory_FromFilename\s*\(", r"\bModel3DExtMayLoad\s*\("),
     {}),

    ("mkset_into_layerset_object",
     "AddFilter_IPCB_LayerSet takes an IPCB_LayerSet OBJECT; a MkSet SET is "
     "a type-cast modal per call. The set form is AddFilter_LayerSet.",
     _rule(r"AddFilter_IPCB_LayerSet\s*\(\s*MkSet\s*\("),
     {}),

    ("undeclared_variation_enum",
     "The eVariation_* names are not declared in the binding (a Case on "
     "them crashed); every variation read compares DM_VariationKind with "
     "the literal ordinal (1 = not fitted).",
     _rule(r"\beVariation_\w+"),
     {}),

    ("layer_object_net",
     "IPCB_LayerObject_V7 has no Net: LayerObj.Net faulted live as an "
     "undeclared identifier (twice per pcb_set_plane_net call).",
     _rule(r"\bLayerObj\s*\.\s*Net\b"),
     {}),

    ("lib_reader_on_unchecked_path",
     "CreateLibCompInfoReader / ReadAllComponentInfo on an absolute .SchLib "
     "path with no file behind it (the normal state of a library the bridge "
     "has just authored) raised. Ask LibFileMissing first.",
     _rule(r"\bCreateLibCompInfoReader\s*\(",
           r"\b(?:LibFileMissing|FileExists|ResolveSchLibForLoad)\s*\("),
     {"Library.pas": (3, "Lib_GetComponents (one) and Lib_DiffLibraries "
                         "(two) create their readers with no Try at all: "
                         "guarding them turns today's uncaught-error reply "
                         "into an empty result, an owner decision "
                         "(LibFileMissing is ready for both)")}),

    ("force_directories_on_empty_path",
     "ForceDirectories('') raises (a modal); every other path returns False. "
     "Test ExtractFilePath(...) <> '' (or TargetDirExists) first.",
     _rule(r"\bForceDirectories\s*\(",
           r"ExtractFilePath\s*\([^)]*\)\s*<>\s*''|\bTargetDirExists\s*\(|"
           r"\bDirectoryExists\s*\(|"
           r"(\w+)\s*:=\s*ExtractFilePath\s*\([^)]*\)\s*;\s*If\s+\1\s*<>\s*''"),
     {}),
]


def _counts(altium: Path) -> dict[str, dict[str, int]]:
    units = _routines(altium)
    out: dict[str, dict[str, int]] = {}
    for name, _why, counter, _base in PATTERNS:
        per_file: dict[str, int] = {}
        for fname, routines in units.items():
            n = sum(counter(r, b) for r, b in routines.items())
            if n:
                per_file[fname] = n
        out[name] = per_file
    return out


def _sites(altium: Path, pattern: str) -> list[str]:
    counter = next(c for n, _w, c, _b in PATTERNS if n == pattern)
    return [f"{f}:{r}" for f, routines in _routines(altium).items()
            for r, b in routines.items() for _ in range(counter(r, b))]


_COUNTS = None


def _now():
    global _COUNTS
    if _COUNTS is None:
        _COUNTS = _counts(_ALTIUM)
    return _COUNTS


@pytest.mark.parametrize("name", [p[0] for p in PATTERNS])
def test_raising_construct_count_only_falls(name):
    why = next(w for n, w, _c, _b in PATTERNS if n == name)
    base = next(b for n, _w, _c, b in PATTERNS if n == name)
    allowed = {f: n for f, (n, _r) in base.items() if n}
    got = _now()[name]
    assert got == allowed, (
        f"{name}: counts {got} != baseline {allowed}.\nWHY IT RAISES: {why}\n"
        f"Above the baseline: guard the new site (check first; the false "
        f"branch does what the Except did). Below it: lower the baseline in "
        f"this change.\nSites: {_sites(_ALTIUM, name)}")


def test_every_allowance_says_why():
    for name, why, _c, base in PATTERNS:
        assert len(why) > 60, name
        for fname, (n, reason) in base.items():
            assert n == 0 or len(reason) > 20, (name, fname)


def test_the_scanner_sees_every_unit():
    """A scanner that parses nothing passes everything: every unit must
    yield routines, and the patterns must still MATCH the constructs they
    forbid (fed the pre-fix shapes)."""
    units = _routines(_ALTIUM)
    for fname in ("Main.pas", "Utils.pas", "Application.pas", "Library.pas",
                  "PCB.pas", "PCBGeneric.pas", "Generic.pas", "Audit.pas",
                  "Project.pas", "StatusForm.pas"):
        assert len(units[fname]) >= 5, fname
    samples = {
        "conversion_without_range_check": "If IsIntStr(V) Then N := StrToInt(V);",
        "undeclared_ilayer": "If ILayer.IsSignalLayer(L) Then",
        "stack_edit_without_stack_walk": "LayerStack.RemoveFromStack(Obj);",
        "open_document_unchecked_path": "D := Client.OpenDocument('PCB', P);",
        "text_file_io_unchecked": "AssignFile(F, P); Rewrite(F);",
        "model_factory_unchecked_format": "M := B.ModelFactory_FromFilename(P, False);",
        "mkset_into_layerset_object": "I.AddFilter_IPCB_LayerSet(MkSet(L));",
        "undeclared_variation_enum": "If K = eVariation_NotFitted Then",
        "layer_object_net": "LayerObj.Net := N;",
        "lib_reader_on_unchecked_path": "R := SchServer.CreateLibCompInfoReader(P);",
        "force_directories_on_empty_path": "ForceDirectories(ExtractFilePath(P));",
        "pcblib_lookup_before_focus_check": "L := PCBServer.GetCurrentPCBLibrary;",
    }
    for name, _w, counter, _b in PATTERNS:
        if name == "schserver_without_nil_check":
            assert counter("App_GetPreferences",
                           "D := SchServer.GetCurrentSchDocument;") == 1
            assert counter("App_GetPreferences",
                           "If SchServer <> Nil Then D := SchServer.X;") == 0
            continue
        assert counter("R", samples[name]) >= 1, name
    assert PATTERNS[0][2]("R", "If IsInt32Str(V) Then N := StrToInt(V);") == 0
    assert PATTERNS[0][2]("R", "N := StrToInt('5');") == 1   # string emptied
    assert PATTERNS[0][2]("R", _clean("Msg := 'StrToInt(X)';")) == 0


if __name__ == "__main__":   # pragma: no cover - prints counts for a tree
    import sys
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else _ROOT
    for k, v in _counts(root / "scripts" / "altium").items():
        print(f"{k:40s} {v}")
