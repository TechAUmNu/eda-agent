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
    """The field's odd value, if the engine also refuses the numeric and
    literal compares: both proven tests false, every compare raises."""

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
