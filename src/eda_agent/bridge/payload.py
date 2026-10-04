# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One sanitiser for the batch-payload wire format.

Lives beside the bridge because the grammar is the IPC
contract, not a detail of any one tool: tools/, design/ and
anything else that hands a payload to Pascal need the same
rules, and design/ importing from tools/ to get them would be
the wrong dependency.

Every bulk tool sends the same grammar: operations split on ``~~``,
fields on ``;``, key from value on the FIRST ``=``. That is parsed by
``NextBatchOp`` / ``GetBatchField`` in Main.pas, and the Python side
must never emit a value that reshapes it.

Kept in one place because it was not. ``library.py`` had this function
and ``generic.py`` had a near-copy that replaced ``~~`` with two spaces
instead of collapsing tilde runs, so the same input produced different
output depending on which tool sent it.

The rules are narrow on purpose:

* ``;`` becomes ``,`` -- it ends a field, so an unescaped one both
  truncates the value and turns the remainder into a live ``key=value``.
* runs of two or more ``~`` collapse to one -- ``~~`` ends an operation
  and would fabricate an extra pin or pad.
* ``=`` is left ALONE. Only the first ``=`` splits, so ``EN=1`` and
  ``OC=OUT`` parse correctly and escaping them would corrupt real names.
* a lone ``~`` is left ALONE. It is KiCad's overbar marker (``~{RESET}``)
  and mangling it renames every active-low pin on the part. That is why
  field-structured operations are joined with :func:`join_ops` (``;~~``),
  not a bare ``~~``: a value ending in its lone ``~`` would otherwise run
  into the separator.

Substitution rather than escaping is forced: the deployed Pascal parser
has no escape syntax to escape TO.

Substitution is right only for FREE TEXT. A name that selects or renames
an object is refused instead (:func:`name_wire_problem`), and numbers and
booleans bound for the wire have one guard each (:func:`finite_number`,
:func:`wire_bool`) for the same reason this module exists: copies drift.
"""

from __future__ import annotations

import math
import re
from typing import Optional

__all__ = ["payload_safe", "unsendable_chars", "name_wire_problem",
           "finite_number", "non_finite", "whole_number", "wire_bool",
           "join_ops", "OP_SEPARATOR", "PASCAL_INT_MAX", "MAX_COORD_MILS"]

#: Two or more consecutive tildes. A single one is data.
#:
#: ``~+`` would behave identically -- collapsing a run of one to
#: one is a no-op -- so the two spellings are interchangeable here
#: and a mutation between them proves nothing. What must NOT change
#: is that a lone tilde survives at all: stripping it renames every
#: active-low pin, since ``~{RESET}`` is KiCad's overbar syntax.
_MULTI_TILDE = re.compile(r"~{2,}")

#: What goes BETWEEN two operations of a field-structured payload.
#:
#: Not a bare ``~~``. :func:`payload_safe` leaves a lone ``~`` alone (it is
#: data), so a free-text value may END in one, and when that value is the
#: last field of an operation it runs into the separator: ``layer=Top~`` +
#: ``~~`` + ``designator=2`` is ``layer=Top~~~designator=2``. NextBatchOp
#: splits on the FIRST ``~~``, so the operation reads ``layer=Top`` and the
#: next one starts ``~designator=2``: its designator reads as '', which in a
#: pad edit selects the footprint's UNNAMED pads, and in a track loses x1,
#: which StrToFloatDef turns into 0.
#:
#: The ``;`` in front closes the last field first, so the ``~~`` can never
#: touch a value. It costs one empty field per operation, which
#: GetBatchField and BatchFieldPresent both skip (a field with no ``=``
#: matches no key), and ``payload_safe`` guarantees no value carries a
#: ``;`` of its own. ONLY for payloads whose operations are ``key=value``
#: fields read by GetBatchField: a ``~~``-joined list of bare names would
#: get the ``;`` inside every name.
OP_SEPARATOR = ";~~"


def join_ops(ops) -> str:
    """Join ``key=value;...`` operations so no value meets the separator.

    See :data:`OP_SEPARATOR`. The one joiner for field-structured batch
    payloads, so a builder with a free-text field cannot be the one that
    forgot.
    """
    return OP_SEPARATOR.join(ops)


def payload_safe(value: object) -> str:
    """Neutralise batch-payload delimiters inside a free-text field.

    Verified against the real tool: a pin named ``A;x=99`` injected an
    ``x=99`` field ahead of the true coordinate, placing the pin
    somewhere else, and ``B~~designator=99`` split its operation in two
    and fabricated an extra pin. Both are silent, because the payload
    stays syntactically valid either way.
    """
    text = str(value).replace(";", ",")
    return _MULTI_TILDE.sub("~", text)


def unsendable_chars(value: object) -> str:
    """Characters in ``value`` the bridge cannot carry, first-seen order.

    Altium's DelphiScript strings are single-byte. ``UnescapeJsonString``
    in Main.pas emits ``Chr(Code)`` for a codepoint up to 255 and a
    literal ``?`` for anything above, so text crossing the wire is
    silently flattened rather than rejected: ``10`` + the ohm sign
    arrives as ``10?``.

    The boundary is exactly U+00FF, not "non-ASCII". The micro sign, the
    degree sign and accented Latin all survive; the ohm sign and any CJK
    text do not. Returning the offending characters rather than a
    boolean lets a caller name them, which matters because the damage is
    invisible in the result: the call succeeds and the field is simply
    wrong.

    Returns "" when the text survives intact.
    """
    seen: set[str] = set()
    out: list[str] = []
    for ch in str(value):
        if ord(ch) > 255 and ch not in seen:
            seen.add(ch)
            out.append(ch)
    return "".join(out)


def name_wire_problem(value: object) -> str:
    """Why ``value`` cannot travel as a NAME in a batch payload, or "".

    A name SELECTS or RENAMES an object, so it is refused rather than
    passed through :func:`payload_safe`: sanitising ``A;B`` to ``A,B``
    selects or creates an object nobody asked for. Refused:

    * ``;`` -- ends the field, and the remainder becomes a live field.
    * ``~~`` -- ends the operation.
    * a TRAILING ``~``. ``NextBatchOp`` splits on the FIRST ``~~``, so a
      value ending in ``~`` that closes an operation runs into the
      separator: ``new=B~`` + ``~~`` + ``old=C`` reads as ``new=B`` and an
      operation ``~old=C``. Refused wherever it sits, so the rule does not
      depend on field order.
    * a character above U+00FF, which the bridge flattens to ``?``.

    A LEADING ``~`` is allowed: it always follows ``key=``, so it can never
    meet a separator, and it is the overbar marker in pin names (~RESET).

    The reason always names the separators, so a caller can tell this
    refusal from a missing field.
    """
    text = str(value)
    if ";" in text or "~~" in text:
        return ("contains ';' or '~~', the wire-format separators; a "
                "fragment could select or name a different object")
    if text.endswith("~"):
        return ("ends with '~', which runs into the '~~' operation "
                "separator and splits the batch payload")
    bad = unsendable_chars(text)
    if bad:
        return f"contains {bad!r}, which the bridge flattens to '?'"
    return ""


def finite_number(value: object) -> Optional[float]:
    """A finite float, or None for anything that is not one.

    THE number guard for anything bound for the wire. ``float()`` on a
    caller's junk raises, which would hand an MCP client a stack trace;
    NaN and the infinities pass a ``float`` annotation, json writes them
    as NaN / Infinity (not JSON), and a value that did reach the Pascal
    unparsed comes out of StrToFloatDef as its DEFAULT, so the call would
    report success for a value nobody sent. A bool is refused too:
    ``True`` is a number to Python and never a coordinate.
    """
    if isinstance(value, bool):
        return None
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


#: The largest magnitude a DelphiScript Integer holds (32-bit signed).
#: StrToIntDef returns its DEFAULT for anything beyond it, and IsIntStr
#: (digits only) has already said yes, so ``x=1e30`` became x=0 and the
#: marker was placed at the origin and reported as placed.
PASCAL_INT_MAX = 2 ** 31 - 1

#: The largest coordinate, in mils, that survives MilsToCoord: Altium's
#: internal unit is 1/10000 mil, held in that same 32-bit Integer, so
#: about 214,748 mils (5.45 m) is the edge of the coordinate space. A
#: value past it does not fail, it wraps.
MAX_COORD_MILS = PASCAL_INT_MAX // 10000


def whole_number(value: object,
                 limit: int = PASCAL_INT_MAX) -> Optional[int]:
    """An integer from a JSON-ish value, or None when it is not a whole one.

    For fields the Pascal reads with StrToIntDef (schematic mils, part
    ids): 12.7 is REFUSED, not rounded to 13 behind the caller's back, and
    a non-integral value that did reach StrToIntDef would become its
    default. ``200.0`` and ``"200"`` are whole numbers; bools are not.

    So is anything past ``limit`` in magnitude: by default the Pascal
    Integer (:data:`PASCAL_INT_MAX`), whose StrToIntDef would turn the
    value into its default; pass :data:`MAX_COORD_MILS` for a length or
    position in mils, which MilsToCoord would overflow first.
    """
    out = finite_number(value)
    if out is None or not out.is_integer() or abs(out) > limit:
        return None
    return int(out)


def non_finite(values: dict) -> list:
    """Names in ``values`` whose value :func:`finite_number` refuses."""
    return [name for name, value in values.items()
            if finite_number(value) is None]


#: Words accepted for a boolean inside a batch dict. Anything else is
#: refused, never guessed: these are EDIT fields, and an inverted flag is
#: a silent wrong change.
_TRUE_WORDS = frozenset({"true", "1", "yes", "on"})
_FALSE_WORDS = frozenset({"false", "0", "no", "off"})


def wire_bool(value: object) -> Optional[bool]:
    """A boolean from a JSON-ish value, or None when it is not one.

    ONE rule for every boolean inside a batch dict. Truthiness is wrong
    for these: the string ``"false"`` is truthy, so ``keepout: "false"``
    used to make the fill a keep-out. ``True``/``False``, the integers
    1/0 and the words true/false, 1/0, yes/no, on/off (any case) are
    understood; anything else, including other numbers, is None and the
    caller refuses it.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        word = value.strip().lower()
        if word in _TRUE_WORDS:
            return True
        if word in _FALSE_WORDS:
            return False
    return None
