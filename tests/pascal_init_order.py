# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Walk a DelphiScript entry routine IN EXECUTION ORDER and say, for every
unit-level global, whether it is assigned unconditionally before anything
reads it.

WHY IN ORDER, AND WHY UNCONDITIONALLY (review of 2026.10.12.03, D-F2-1).
The first guard counted a global as initialised when ANY ``Name :=``
appeared anywhere StartMCPPump could reach. That accepts the exact D-F2-1
shape: ``If PerfNames = Nil Then PerfNames := TStringList.Create`` is an
assignment in the reach, but on a fresh pump the global holds Empty, Empty
is NOT Nil, the branch is skipped, and the global stays Empty. It also
accepted an assignment that sits AFTER the first read (EnsureStatusBuffers
reads the perf lists before ShowStatusForm's ResetPerfStats creates them),
and one inside a Try body behind statements that may raise.

So this walks the statements of the entry routine, inlining each routine
it calls at the point of the call, and tracks per global:

* a READ while the global is still unassigned is a finding (an ``= Nil``
  test is a read: on Empty it is the test that misfires);
* an assignment counts only when it is UNCONDITIONAL: not inside an
  If/Case/While/For/Repeat/With body, not inside a Try body or an Except
  handler (an earlier raise skips it), and not after a conditional Exit in
  the same routine (``If X Then Exit;`` makes the rest of that routine
  conditional). A Finally body runs either way and counts;
* a global still unassigned when the walk ends is a finding.

The parser is deliberately small: statements, blocks, the control
keywords and calls. It reads the comment-stripped source with string
literals blanked, and treats a routine's own parameters, locals and
``Result`` as not-the-global.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from tests.pascal_source import functions, strip_comments

_TOK = re.compile(r":=|<>|<=|>=|[A-Za-z_]\w*|\d+(?:\.\d+)?|''|\S")

_KW = {
    "begin", "end", "if", "then", "else", "for", "to", "downto", "do",
    "while", "try", "except", "finally", "exit", "continue", "break",
    "var", "const", "function", "procedure", "and", "or", "not", "div",
    "mod", "nil", "true", "false", "repeat", "until", "case", "of", "with",
    "raise", "in", "is", "as", "on", "xor", "shl", "shr",
}
_SIMPLE_STOP = {"end", "else", "until", "except", "finally"}


def strip_strings(code: str) -> str:
    return re.sub(r"'(?:[^']|'')*'", "''", code)


def unit_code(text: str) -> str:
    """Comment-free, string-blanked source of one unit."""
    return strip_strings(strip_comments(text))


_STOP = re.compile(r"(?mi)^\s*(Function|Procedure|Const|Begin|Var|Type)\b")


def unit_globals_of(code: str) -> list[tuple[str, str]]:
    """``[(name, type)]`` for the unit-level Var blocks of one unit's code.

    A Var block at column 0 that runs into a ``Begin`` is the local block
    of a routine with nested routines (PCB.pas, Audit.pas), not a unit
    block: a unit block runs into the next routine, Const, Type or EOF.
    """
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"(?mi)^Var\b", code):
        rest = code[m.end():]
        stop = _STOP.search(rest)
        if stop and stop.group(1).lower() == "begin":
            continue
        block = rest[:stop.start()] if stop else rest
        for decl in re.finditer(r"([\w\s,]+?)\s*:\s*(\w+)\s*;", block):
            for name in decl.group(1).split(","):
                out.append((name.strip(), decl.group(2)))
    return out


@dataclass
class _Routine:
    name: str
    locals: set[str]
    toks: list[str]


def _parse_routine(name: str, text: str) -> _Routine:
    """Header parameters and the local Var block become locals; the token
    list is the statement part from the first top-level ``Begin``."""
    m = re.match(r"\s*(?:Function|Procedure)\s+\w+\s*(\((.*?)\))?", text,
                 re.S | re.I)
    locs = {"result", name.lower()}
    if m and m.group(2):
        for grp in m.group(2).split(";"):
            names = grp.split(":")[0]
            names = re.sub(r"(?i)^\s*(var|const|out)\s+", "", names)
            for n in names.split(","):
                if n.strip():
                    locs.add(n.strip().lower())
    b = re.search(r"(?i)\bBegin\b", text)
    hdr_end = text.find(";", m.end()) + 1 if m else 0
    head = text[hdr_end:b.start()] if b else text[hdr_end:]
    vm = re.search(r"(?i)\bVar\b(.*)", head, re.S)
    if vm:
        for decl in re.finditer(r"([\w\s,]+?)\s*:\s*\w+", vm.group(1)):
            for n in decl.group(1).split(","):
                if n.strip():
                    locs.add(n.strip().lower())
    body = text[b.start():] if b else ""
    return _Routine(name, locs, _TOK.findall(body))


@dataclass
class Finding:
    name: str
    unit: str
    typ: str
    why: str
    where: str

    def __str__(self):
        return f"{self.unit}:{self.name} : {self.typ} ({self.why}; {self.where})"


@dataclass
class _Frame:
    routine: _Routine
    exited: bool = False


@dataclass
class InitWalk:
    """Walk ``entry`` over ``routines`` (name -> source text); ``watched``
    maps each global to (unit, type). ``cut`` ends the entry routine's text
    at that marker (the statement that arms the poll timer). ``gate`` names
    an entry-routine flag whose ``If <gate> Then Exit`` does not make the
    rest conditional (StartMCPPump's re-entry test)."""

    routines: dict[str, str]
    watched: dict[str, tuple[str, str]]
    exempt: dict[str, str] = field(default_factory=dict)
    state: dict[str, str] = field(default_factory=dict)  # name -> 'set'
    cond_seen: dict[str, str] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)
    order: list[str] = field(default_factory=list)  # routines walked

    def __post_init__(self):
        self._parsed: dict[str, _Routine] = {}
        self._lower = {n.lower(): n for n in self.routines}
        self._wlower = {n.lower(): n for n in self.watched}
        self._memo: set[tuple[str, bool]] = set()
        self._stack: list[_Frame] = []

    # -- entry ---------------------------------------------------------------
    def run(self, entry: str, cut: str = "", gate: str = "") -> list[Finding]:
        text = self.routines[entry]
        if cut:
            at = text.find(cut)
            assert at > 0, f"{entry} no longer contains {cut!r}"
            text = text[:at]
        self._gate = gate.lower()
        self._walk(_parse_routine(entry, text), False, root=True)
        for low, name in self._wlower.items():
            if name in self.exempt or self.state.get(name) == "set":
                continue
            if any(f.name == name for f in self.findings):
                continue
            unit, typ = self.watched[name]
            why = ("assigned only CONDITIONALLY (" + self.cond_seen[name] + ")"
                   if name in self.cond_seen else "never assigned")
            self.findings.append(Finding(name, unit, typ, why,
                                         "before the walk ended"))
        return self.findings

    # -- routines ------------------------------------------------------------
    def _routine(self, name: str) -> _Routine:
        if name not in self._parsed:
            self._parsed[name] = _parse_routine(name, self.routines[name])
        return self._parsed[name]

    def _call(self, low: str, cond: bool):
        name = self._lower[low]
        if any(f.routine.name == name for f in self._stack):
            return
        if (name, cond) in self._memo:
            return
        if (name, False) in self._memo:
            return  # an unconditional walk already saw everything
        self._memo.add((name, cond))
        self._walk(self._routine(name), cond)

    def _walk(self, r: _Routine, cond: bool, root: bool = False):
        self.order.append(r.name)
        frame = _Frame(r)
        self._stack.append(frame)
        toks = r.toks
        i = 0
        # The statement part is one Begin..End block.
        if i < len(toks) and toks[i].lower() == "begin":
            i = self._seq(toks, i + 1, cond, {"end"}, root)
        self._stack.pop()

    def _eff(self, cond: bool) -> bool:
        return cond or self._stack[-1].exited

    # -- statements ----------------------------------------------------------
    def _seq(self, t, i, cond, stops, root=False):
        while i < len(t):
            w = t[i].lower()
            if w in stops:
                return i
            if t[i] == ";":
                i += 1
                continue
            i = self._stmt(t, i, cond, root)
        return i

    def _expect(self, t, i, word):
        if i < len(t) and t[i].lower() == word:
            return i + 1
        return i

    def _stmt(self, t, i, cond, root=False):
        w = t[i].lower()
        if w == "begin":
            i = self._seq(t, i + 1, cond, {"end"}, root)
            return self._expect(t, i, "end")
        if w == "try":
            i = self._seq(t, i + 1, True, {"except", "finally"})
            if i < len(t) and t[i].lower() == "except":
                i = self._seq(t, i + 1, True, {"end"})
            elif i < len(t) and t[i].lower() == "finally":
                i = self._seq(t, i + 1, cond, {"end"})
            return self._expect(t, i, "end")
        if w == "if":
            j = self._until(t, i + 1, {"then"})
            cond_toks = t[i + 1:j]
            self._expr(cond_toks, cond)
            i = j + 1
            if i < len(t) and t[i].lower() in ("exit", "raise"):
                gate = (root and len(cond_toks) == 1
                        and cond_toks[0].lower() == self._gate)
                if not gate:
                    self._stack[-1].exited = True
                i += 1
            else:
                i = self._stmt(t, i, True)
            if i < len(t) and t[i].lower() == "else":
                i = self._stmt(t, i + 1, True)
            return i
        if w == "case":
            j = self._until(t, i + 1, {"of"})
            self._expr(t[i + 1:j], cond)
            i = j + 1
            while i < len(t):
                ww = t[i].lower()
                if ww == "end":
                    return i + 1
                if t[i] == ";":
                    i += 1
                    continue
                if ww == "else":
                    i = self._seq(t, i + 1, True, {"end"})
                    continue
                while i < len(t) and t[i] != ":":
                    i += 1
                i = self._stmt(t, i + 1, True)
            return i
        if w in ("while", "with", "for"):
            j = self._until(t, i + 1, {"do"})
            self._expr(t[i + 1:j], cond)
            return self._stmt(t, j + 1, True)
        if w == "repeat":
            i = self._seq(t, i + 1, True, {"until"})
            j = self._simple_end(t, i + 1)
            self._expr(t[i + 1:j], True)
            return j
        if w in ("exit", "raise"):
            self._stack[-1].exited = True
            return self._simple_end(t, i + 1)
        if w in ("break", "continue"):
            return i + 1
        j = self._simple_end(t, i)
        self._simple(t[i:j], cond)
        return j

    def _until(self, t, i, words):
        depth = 0
        while i < len(t):
            if t[i] in "([":
                depth += 1
            elif t[i] in ")]":
                depth -= 1
            elif depth == 0 and t[i].lower() in words:
                return i
            i += 1
        return i

    def _simple_end(self, t, i):
        depth = 0
        while i < len(t):
            if t[i] in "([":
                depth += 1
            elif t[i] in ")]":
                depth -= 1
            elif depth == 0 and (t[i] == ";" or t[i].lower() in _SIMPLE_STOP):
                return i
            i += 1
        return i

    # -- expressions and simple statements -----------------------------------
    def _is_global(self, low: str) -> bool:
        return (low in self._wlower
                and low not in self._stack[-1].routine.locals)

    def _is_routine(self, low: str) -> bool:
        return (low in self._lower
                and low not in self._stack[-1].routine.locals)

    def _simple(self, s, cond):
        if not s:
            return
        if (len(s) >= 2 and s[1] == ":=" and re.match(r"[A-Za-z_]", s[0])):
            self._expr(s[2:], cond)
            low = s[0].lower()
            if self._is_global(low):
                self._assign(self._wlower[low], cond)
            return
        self._expr(s, cond)

    def _expr(self, s, cond):
        pending: list[tuple[int, str]] = []  # (paren depth to close at, low)
        depth = 0
        for k, tok in enumerate(s):
            if tok in "([":
                depth += 1
                continue
            if tok in ")]":
                depth -= 1
                while pending and pending[-1][0] > depth:
                    self._call(pending.pop()[1], self._eff(cond))
                continue
            if not re.match(r"[A-Za-z_]", tok):
                continue
            low = tok.lower()
            if low in _KW or (k > 0 and s[k - 1] == "."):
                continue
            if self._is_routine(low):
                if k + 1 < len(s) and s[k + 1] == "(":
                    pending.append((depth + 1, low))
                else:
                    self._call(low, self._eff(cond))
                continue
            if self._is_global(low):
                self._read(self._wlower[low], s, k)
        while pending:
            self._call(pending.pop()[1], self._eff(cond))

    def _where(self) -> str:
        return " -> ".join(f.routine.name for f in self._stack)

    def _read(self, name: str, s, k):
        if self.state.get(name) == "set" or name in self.exempt:
            return
        if any(f.name == name for f in self.findings):
            return
        unit, typ = self.watched[name]
        nxt = " ".join(s[k + 1:k + 3]).lower()
        prv = " ".join(s[max(0, k - 2):k]).lower()
        if re.match(r"(=|<>) nil", nxt) or re.search(r"nil (=|<>)$", prv):
            how = "compared with Nil while still unassigned"
        elif k + 1 < len(s) and s[k + 1] == ".":
            how = "member read while still unassigned"
        else:
            how = "read while still unassigned"
        self.findings.append(Finding(name, unit, typ, how, self._where()))

    def _assign(self, name: str, cond: bool):
        if self._eff(cond):
            self.cond_seen.setdefault(name, self._where())
            return
        self.state[name] = "set"


def routine_texts(sources: dict[str, str], units: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for unit in units:
        for name, body in functions(unit_code(sources[unit])).items():
            out[name] = body
    return out
