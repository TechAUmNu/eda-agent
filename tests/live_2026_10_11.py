# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Fixtures shared by the tests of the 2026-10-11 live run's findings
(the first 2026-10-11 build on AD 26.10.1.5; docs/RELEASE_VERIFICATION.md
sections 38 and 39 record the run). Each test module EXECUTES the real
Pascal through tests/pascal_eval.py and fails on ef2d79b for the field
reason; this module holds the field's odd modified flag and small helpers.
"""
from __future__ import annotations

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load
from tests.test_bridge_record_names_one_file import _ALTIUM, _key


def _pipe(text):
    return [e for e in str(text).split("|") if e]


def _keys(text):
    return [_key(e) for e in _pipe(text)]


class FieldFlag:
    """What AD 26.10.1.5 read back after SetModified(True) on a sheet: a
    value for which 'If X' AND 'If Not X' were BOTH false (live). The
    leading hypothesis is a Boolean variant holding 1: a compare with the
    Boolean literals fails (True is -1) and a NUMERIC compare with 0 sees it
    set. ``__pnot__`` is DelphiScript's 'If Not X' (tests/pascal_eval.py
    routes Not through it); ``FieldFlag(-1)`` is a normal True and
    ``FieldFlag(0)`` a normal False."""

    def __init__(self, value=1):
        self.value = value

    def __bool__(self):
        # DelphiScript's 'If X' holds only for True's -1.
        return self.value == -1

    def __pnot__(self):
        # 'If Not X' held only for a real False (live: false for the odd
        # value as well as for True).
        return self.value == 0

    def __eq__(self, other):
        if isinstance(other, bool):
            return self.value == (-1 if other else 0)
        return self.value == other

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = None


class NullFlag:
    """A Null: converting it to a Boolean raises (Delphi's default), so
    'If X' and 'If Not X' both raise, as does any compare."""

    def _raise(self, *_a):
        raise PascalError("Null")

    __bool__ = __pnot__ = __eq__ = __ne__ = _raise
    __hash__ = None


class StrictFlag:
    """A NORMAL flag that refuses every form the field never proved on it:
    'If X' and 'If Not X' answer, but a compare with an integer or a Boolean
    literal raises. A reader built only from the two proven forms never
    notices; one that compares a Boolean with 0 does."""

    def __init__(self, value):
        self.value = bool(value)

    def __bool__(self):
        return self.value

    def __pnot__(self):
        return not self.value

    def __eq__(self, _other):
        raise PascalError("unproven compare")

    __ne__ = __eq__
    __hash__ = None


class OddRaising(FieldFlag):
    """The field's odd value with every compare refused: both proven tests
    false, any compare raises. It models NO containment - on the engine a
    raise is a modal BEFORE the Except runs - so the readers must answer it
    without ever comparing it (review of d198b65): a test passes only when
    nothing is caught."""

    def __eq__(self, _other):
        raise PascalError("compare")

    __ne__ = __eq__
    __hash__ = None


def _field(doc, save_clears=True):
    """Make one fixture document behave like the field: SetModified(True)
    leaves the odd value; a save clears it unless the editor declines."""
    def set_modified(value):
        if value:
            doc.marks += 1
            doc.Modified = FieldFlag(1)
        else:
            doc.Modified = False

    def save(_kind):
        doc.saves += 1
        if save_clears:
            doc.disk.touch(doc.FileName)
            doc.Modified = False
    doc.SetModified = set_modified
    doc.DoFileSave = save
    return doc


_MAIN = load(_ALTIUM / "Main.pas", minimum=20)
_UTILS = load(_ALTIUM / "Utils.pas", minimum=20)


def _with(w, names, bodies=None):
    bodies = bodies or _MAIN
    build(bodies, [n for n in names if n in bodies], w.ns)
    return w


def _caught(ns):
    ns.setdefault("_caught", [])
    ns["RecordCaughtException"] = lambda where: ns["_caught"].append(where)
    return ns["_caught"]


# ---------------------------------------------------------------------------
# A file's bytes as FileLooksLoadable reads them (the .02b run, N1)
# ---------------------------------------------------------------------------

# The OLE compound-file magic D0 CF 11 E0 A1 B1 1A E1 as TStringList.Text
# hands it back on a cp1252 system (the ANSI decode), and on a UTF-8 code
# page (D0 CF and E0 A1 B1 decode to ONE character each). Chr(17) - the
# magic's 0x11 - is inside the first four characters either way.
OLE_HEAD_CP1252 = "\u00d0\u00cf\x11\u00e0\u00a1\u00b1\x1a\u00e1"
OLE_HEAD_UTF8 = "\u03cf\x11\u0871\x1a"


class LockedFile(Exception):
    """A content value that makes LoadFromFile raise (a locked file)."""


def file_reader(contents=None, default=OLE_HEAD_CP1252, loads=None):
    """``TStringList.Create`` / ``LoadFromFile`` / ``Text`` / ``Free`` over a
    fake disk: ``contents`` maps an UPPER-CASED path to the decoded text
    ('' = a 0-byte file; ``LockedFile`` = the read raises an ordinary
    EFOpenError), every other path reads ``default`` (a real board's head).
    ``loads`` collects every path read, so a test can see a file was never
    read. Also ``Sleep`` (the retry) and ``Chr``."""
    contents = {k.upper(): v for k, v in (contents or {}).items()}
    seen = loads if loads is not None else []

    class _SL:
        Free = None

        def __init__(self):
            self.Text = ""

        def LoadFromFile(self, path):
            seen.append(path)
            value = contents.get(str(path).upper(), default)
            if value is LockedFile:
                raise PascalError("EFOpenError: file in use")
            self.Text = value

    class _Factory:
        @property
        def Create(self):
            return _SL()

    return {"TStringList": _Factory(), "Sleep": lambda _ms: None, "Chr": chr}
