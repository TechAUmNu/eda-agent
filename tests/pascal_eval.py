# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run straight-line DelphiScript bodies against Python fixtures.

WHY THIS EXISTS. A guard that pins a body by substring passes for the
wrong reason whenever the defect is in the LOGIC rather than in a missing
line: ``If NameHits = 1 Then`` was pinned, and the walk around it still
returned the wrong file (review, 2026-10-11). This translates the small
subset of Pascal the path and record helpers are written in into Python,
so a test can EXECUTE the real source text on a fixture workspace and
fail on the old body for the field reason.

The subset, and nothing more: Function/Procedure headers with value
parameters, a Var section, Begin/End blocks, If/Then/Else, For/To/DownTo,
While, Try/Except and Try/Finally, Exit/Continue/Break, Inc/Dec,
assignments, calls (including method calls and property reads), the
operators And/Or/Not/Div/Mod and the relational operators. Anything else
raises, so an unsupported construct can never be skipped silently.

Semantics a caller must supply in the namespace: the Altium objects
(``Client``, ``GetWorkspace`` as the workspace VALUE, since Pascal calls a
parameterless function without parentheses), ``FileAge`` and any helper
the body calls that is not translated alongside it. The Delphi string
builtins are provided here with Delphi's 1-based semantics.
"""
from __future__ import annotations

import ntpath
import re

from tests.pascal_source import strip_comments

_TOKEN = re.compile(
    r"\s*(?:(?P<str>'(?:[^']|'')*')|(?P<num>\d+)|(?P<id>[A-Za-z_]\w*)"
    r"|(?P<op>:=|<>|<=|>=|[=<>+\-*/(),.;:\[\]]))")

_KEYWORDS = {
    "begin", "end", "if", "then", "else", "for", "to", "downto", "do",
    "while", "try", "except", "finally", "exit", "continue", "break",
    "var", "function", "procedure", "and", "or", "not", "div", "mod",
    "nil", "true", "false", "repeat", "until", "case", "of", "with",
}
_STOP = {"then", "do", "to", "downto", "else", "end", "except", "finally",
         "of", "until"}


def _tokens(src: str) -> list[tuple[str, str]]:
    out, i = [], 0
    src = src.rstrip()
    while i < len(src):
        m = _TOKEN.match(src, i)
        if not m or m.end() == i:
            if src[i:].strip() == "":
                break
            raise SyntaxError(f"cannot tokenise at: {src[i:i + 40]!r}")
        i = m.end()
        kind = m.lastgroup
        text = m.group(kind)
        if kind == "id" and text.lower() in _KEYWORDS:
            out.append(("kw", text.lower()))
        else:
            out.append((kind, text))
    return out


def _pascal_string(lit: str) -> str:
    return lit[1:-1].replace("''", "'")


class _Translator:
    def __init__(self, toks):
        self.t = toks
        self.i = 0
        self.lines: list[str] = []
        self.assigned: set[str] = set()
        self.is_function = False

    # -- token helpers ----------------------------------------------------
    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else ("eof", "")

    def take(self, kind=None, text=None):
        tok = self.peek()
        if kind and tok[0] != kind or text and tok[1] != text:
            raise SyntaxError(f"expected {kind} {text!r}, got {tok!r} at "
                              f"token {self.i}")
        self.i += 1
        return tok

    def at_kw(self, *words):
        tok = self.peek()
        return tok[0] == "kw" and tok[1] in words

    def at_op(self, op):
        tok = self.peek()
        return tok[0] == "op" and tok[1] == op

    def emit(self, depth, text):
        self.lines.append("    " * depth + text)

    # -- header -----------------------------------------------------------
    def header(self):
        kind = self.take("kw")[1]
        if kind not in ("function", "procedure"):
            raise SyntaxError("not a Function/Procedure")
        self.is_function = kind == "function"
        name = self.take("id")[1]
        params: list[str] = []
        if self.at_op("("):
            self.take("op", "(")
            group: list[str] = []
            while not self.at_op(")"):
                tok = self.take()
                if tok == ("kw", "var"):
                    raise SyntaxError("Var parameters are not supported")
                if tok[0] == "id":
                    group.append(tok[1])
                elif tok == ("op", ":"):
                    self.take("id")               # the type
                    params.extend(group)
                    group = []
                elif tok in (("op", ","), ("op", ";")):
                    continue
            self.take("op", ")")
        if self.at_op(":"):
            self.take("op", ":")
            self.take("id")
        self.take("op", ";")
        locals_: list[str] = []
        if self.at_kw("var"):
            self.take("kw", "var")
            while not self.at_kw("begin"):
                names = [self.take("id")[1]]
                while self.at_op(","):
                    self.take("op", ",")
                    names.append(self.take("id")[1])
                self.take("op", ":")
                self.take("id")
                self.take("op", ";")
                locals_.extend(names)
        return name, params, locals_

    # -- expressions ------------------------------------------------------
    def expr(self, stop_ops=(";",)):
        parts, depth = [], 0
        while True:
            kind, text = self.peek()
            if kind == "eof":
                break
            if depth == 0 and kind == "kw" and text in _STOP:
                break
            if depth == 0 and kind == "op" and (text in stop_ops
                                                or text == ":="):
                break
            if kind == "op" and text in "([":
                depth += 1
            if kind == "op" and text in ")]":
                if depth == 0:
                    break
                depth -= 1
            self.i += 1
            if kind == "str":
                parts.append(repr(_pascal_string(text)))
            elif kind == "num":
                parts.append(text)
            elif kind == "kw":
                parts.append({"and": " and ", "or": " or ", "not": " not ",
                              "div": " // ", "mod": " % ", "nil": "None",
                              "true": "True", "false": "False"}[text])
            elif kind == "op":
                parts.append({"=": " == ", "<>": " != "}.get(text, text))
            else:
                parts.append(text)
        if not parts:
            raise SyntaxError(f"empty expression at token {self.i}")
        return "".join(parts)

    # -- statements -------------------------------------------------------
    def ret(self):
        return "return Result" if self.is_function else "return None"

    def block_body(self, depth, enders):
        """Statements up to one of ``enders`` (not consumed)."""
        n0 = len(self.lines)
        while not self.at_kw(*enders):
            if self.at_op(";"):
                self.take("op", ";")
                continue
            self.statement(depth)
            if self.at_op(";"):
                self.take("op", ";")
        if len(self.lines) == n0:
            self.emit(depth, "pass")

    def statement(self, depth):
        kind, text = self.peek()
        if kind == "kw":
            if text == "begin":
                self.take()
                self.block_body(depth, ("end",))
                self.take("kw", "end")
                return
            if text == "if":
                self.take()
                cond = self.expr()
                self.take("kw", "then")
                self.emit(depth, f"if {cond}:")
                self.sub(depth + 1)
                if self.at_kw("else"):
                    self.take()
                    self.emit(depth, "else:")
                    self.sub(depth + 1)
                return
            if text == "for":
                self.take()
                var = self.take("id")[1]
                self.assigned.add(var)
                self.take("op", ":=")
                lo = self.expr()
                down = self.take("kw")[1] == "downto"
                hi = self.expr()
                self.take("kw", "do")
                rng = (f"range(({lo}), ({hi}) - 1, -1)" if down
                       else f"range(({lo}), ({hi}) + 1)")
                self.emit(depth, f"for {var} in {rng}:")
                self.sub(depth + 1)
                return
            if text == "while":
                self.take()
                cond = self.expr()
                self.take("kw", "do")
                self.emit(depth, f"while {cond}:")
                self.sub(depth + 1)
                return
            if text == "try":
                self.take()
                self.emit(depth, "try:")
                self.block_body(depth + 1, ("except", "finally"))
                which = self.take("kw")[1]
                if which == "except":
                    # Only what a fixture RAISES on purpose (an Altium call
                    # failing). A Python error in a fixture or a bad
                    # translation must fail the test, not be swallowed.
                    self.emit(depth, "except PascalError:")
                else:
                    self.emit(depth, "finally:")
                self.block_body(depth + 1, ("end",))
                self.take("kw", "end")
                return
            if text == "exit":
                self.take()
                self.emit(depth, self.ret())
                return
            if text in ("continue", "break"):
                self.take()
                self.emit(depth, text)
                return
            if text in ("end", "else", "except", "finally"):
                self.emit(depth, "pass")
                return
            raise SyntaxError(f"unsupported statement keyword {text!r}")
        if kind == "id" and text in ("Inc", "Dec") and self.peek(1) == ("op", "("):
            self.take()
            self.take("op", "(")
            var = self.take("id")[1]
            self.assigned.add(var)
            step = "1"
            if self.at_op(","):
                self.take("op", ",")
                step = self.expr(stop_ops=(")",))
            self.take("op", ")")
            sign = "+" if text == "Inc" else "-"
            self.emit(depth, f"{var} {sign}= {step}")
            return
        # Assignment or call.
        target = self.expr(stop_ops=(";",))
        if self.at_op(":="):
            self.take("op", ":=")
            value = self.expr()
            if re.fullmatch(r"\w+", target):
                self.assigned.add(target)
            self.emit(depth, f"{target} = {value}")
            return
        if re.fullmatch(r"\w+", target):
            target += "()"
        self.emit(depth, target)

    def sub(self, depth):
        """One statement as an indented suite (an empty one is ``pass``)."""
        if self.at_op(";") or self.at_kw("else", "end", "except", "finally"):
            self.emit(depth, "pass")
            return
        self.statement(depth)


def translate(body: str) -> tuple[str, str]:
    """``(name, python_source)`` for one Function/Procedure body."""
    tr = _Translator(_tokens(strip_comments(body)))
    name, params, locals_ = tr.header()
    tr.take("kw", "begin")
    tr.block_body(1, ("end",))
    tr.take("kw", "end")
    known = set(params) | set(locals_) | {"Result"}
    globals_ = sorted(tr.assigned - known)
    head = [f"def {name}({', '.join(params)}):"]
    if globals_:
        head.append("    global " + ", ".join(globals_))
    head.append("    Result = None")
    for v in locals_:
        head.append(f"    {v} = None")
    src = "\n".join(head + tr.lines + ["    return Result"])
    return name, src


# -- Delphi builtins, 1-based --------------------------------------------

def _copy(s, i, n):
    i = max(int(i), 1)
    n = max(int(n), 0)
    return s[i - 1:i - 1 + n]


def _pos(sub, s):
    if sub == "":
        return 0
    return s.find(sub) + 1


def _strtointdef(s, d):
    try:
        return int(str(s).strip())
    except ValueError:
        return d


class PascalError(Exception):
    """What a fixture raises to model an Altium call raising; the only
    exception a translated Try/Except catches."""


BUILTINS = {
    "PascalError": PascalError,
    "UpperCase": lambda s: s.upper(),
    "LowerCase": lambda s: s.lower(),
    "Trim": lambda s: s.strip(),
    "StringReplace": lambda s, a, b, flags: s.replace(a, b),
    "ExtractFileName": lambda s: ntpath.basename(s),
    "ExtractFileExt": lambda s: ntpath.splitext(s)[1],
    "Copy": _copy,
    "Pos": _pos,
    "Length": len,
    "IntToStr": lambda n: str(int(n)),
    "StrToIntDef": _strtointdef,
}


def build(bodies: dict[str, str], names, ns: dict) -> dict:
    """Translate ``names`` (in order) from ``bodies`` into ``ns``."""
    for n in BUILTINS:
        ns.setdefault(n, BUILTINS[n])
    for n in names:
        _, src = translate(bodies[n])
        try:
            exec(compile(src, f"<pascal {n}>", "exec"), ns)   # noqa: S102
        except SyntaxError as exc:                          # pragma: no cover
            raise SyntaxError(f"{n} translated badly:\n{src}") from exc
    return ns
