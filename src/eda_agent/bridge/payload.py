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

Substitution rather than escaping was forced: the Pascal parser had no
escape syntax to escape TO. The component-parameter stamp is the
exception now (:func:`encode_stamps`): a Manufacturer Part Number or a
Description is data that must arrive EXACTLY, and a ``;`` silently
turned into ``,`` there is a wrong part number on a BOM (field report
2026-10-08). That handler opts in with ``value_encoding=pct1`` and
decodes ``%XX`` in Pascal (``PctDecode`` in Main.pas) after splitting.

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
           "join_ops", "OP_SEPARATOR", "PASCAL_INT_MAX", "MAX_COORD_MILS",
           "STAMP_ENCODING", "stamp_encode", "stamp_decode",
           "stamp_key_route", "encode_stamps", "stamp_control_chars",
           "is_stamp_reply", "stamp_reply_problem", "STAMP_SKEW_NOTE"]

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


# ------------------------------------------------- the stamp value encoding

#: Sent as ``value_encoding`` by every builder of a
#: ``generic.set_sch_components_parameters`` payload, and echoed back by a
#: handler that decoded it. A reply WITHOUT the echo came from a script that
#: predates the decoder and wrote the escapes literally.
STAMP_ENCODING = "pct1"

#: The characters :func:`stamp_encode` escapes. ``;`` ends a field, ``~``
#: (any run of two) ends an operation, ``=`` splits a key from its value
#: (only the FIRST one, but escaping every one makes a KEY safe too), and
#: ``%`` is the escape itself. Nothing else needs it: ``,`` ``|`` quotes and
#: backslashes are JSON's business and survive the transport already, and
#: Latin-1 text arrives as itself (``\u00b5`` -> ``Chr(181)``).
_STAMP_ESCAPED = frozenset("%;~=")


def stamp_encode(value: object) -> str:
    """``value`` with ``% ; ~ =`` as ``%25 %3B %7E %3D``; all else as is.

    Lossless, unlike :func:`payload_safe`: ``PctDecode`` in Main.pas turns
    it back after the payload has been split, so ``A;B`` arrives as
    ``A;B`` rather than ``A,B``.
    """
    return "".join(f"%{ord(ch):02X}" if ch in _STAMP_ESCAPED else ch
                   for ch in str(value))


def _hex(ch: str) -> int:
    return int(ch, 16) if ch and ch in "0123456789abcdefABCDEF" else -1


def stamp_decode(text: str) -> str:
    """What ``PctDecode`` in Main.pas makes of ``text``, step for step.

    ``%`` and two hex digits is that character; anything else, a lone or
    trailing ``%`` included, is copied. Kept beside the encoder so the
    round trip is testable without Altium.
    """
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "%" and i + 2 < n:
            d1, d2 = _hex(text[i + 1]), _hex(text[i + 2])
            if d1 >= 0 and d2 >= 0:
                out.append(chr(d1 * 16 + d2))
                i += 3
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def stamp_key_route(key: str) -> str:
    """Where the stamp handler sends ``key``, as the reply reports it.

    ``Value`` and ``Comment`` set the component's Comment text and
    ``Description`` its own description (``ComponentDescription``), all
    properties, never parameters: a ``Description`` key used to create a
    second, visible parameter called Description at the sheet origin.
    ``Footprint`` is read-only from DelphiScript and is skipped. Any other
    key is a parameter, modified in place or created.

    ``Value`` and ``Footprint`` match exactly, as they always have;
    ``Comment`` and ``Description`` match in any case. Mirrors the
    branches of ``Gen_SetSchComponentsParameters``.

    ``Description`` also updates a parameter called Description when the
    part ALREADY carries one (imported and DbLib-style symbols often do;
    the old route updated it in place, and leaving it would give the part
    two descriptions that disagree). It is never created. The reply lists
    those under ``description_parameters_also_set``.
    """
    lower = key.lower()
    if key == "Value" or lower == "comment":
        return "property:Comment"
    if lower == "description":
        return "property:ComponentDescription"
    if key == "Footprint":
        return "skipped:read-only"
    return "parameter"


#: Control characters a stamp may carry: tab, line feed, carriage return.
#: Any other below U+0020 is refused. A NUL in particular reaches Pascal as
#: ``Chr(0)`` and the COM string conversion truncates the text there.
_STAMP_CONTROL_OK = frozenset("\t\n\r")


def stamp_control_chars(value: object) -> str:
    """Control characters in ``value`` a stamp cannot carry, first-seen."""
    seen: list[str] = []
    for ch in str(value):
        if ord(ch) < 0x20 and ch not in _STAMP_CONTROL_OK \
                and ch not in seen:
            seen.append(ch)
    return "".join(seen)


def _stamp_text_problem(*texts: object) -> str:
    """Why these texts cannot arrive exactly through a stamp, or ""."""
    lost = "".join(unsendable_chars(t) for t in texts)
    if lost:
        return f"contains {lost!r}, which the bridge flattens to '?'"
    ctrl = "".join(stamp_control_chars(t) for t in texts)
    if ctrl:
        return (f"contains control character(s) {ctrl!r}; a NUL truncates "
                f"the text in Altium and the others are not carried "
                f"reliably")
    return ""


def encode_stamps(stamps) -> tuple[str, list[dict]]:
    """The ``stamps`` payload for ``generic.set_sch_components_parameters``.

    ``stamps`` is a list of dicts, each with a ``designator`` and
    ``{parameter: value}`` pairs. Every designator, key and value goes
    through :func:`stamp_encode`, so the payload must be sent with
    ``value_encoding`` = :data:`STAMP_ENCODING`.

    Returns ``(payload, problems)``. A field that cannot be sent exactly
    is LEFT OUT and named in ``problems`` (``{"designator", "key",
    "reason"}``) rather than flattened: text above U+00FF, which the
    bridge turns into ``?``, a control character other than tab, line
    feed and carriage return (a NUL truncates the text), and an empty
    key. A stamp with no designator
    is left out whole. ``None`` and empty values are skipped silently, as
    the handler skips empty values anyway. The caller decides whether a
    problem refuses the call.
    """
    ops: list[str] = []
    problems: list[dict] = []
    for stamp in stamps:
        designator = stamp.get("designator") if isinstance(stamp, dict) \
            else None
        if designator is None or str(designator) == "":
            problems.append({"designator": None, "key": "designator",
                             "reason": f"stamp has no designator: {stamp!r}"})
            continue
        why = _stamp_text_problem(designator)
        if why:
            problems.append({"designator": str(designator),
                             "key": "designator", "reason": why})
            continue
        fields = [f"designator={stamp_encode(designator)}"]
        for key, value in stamp.items():
            if key == "designator" or value is None:
                continue
            text = str(value)
            if text == "":
                continue
            if str(key) == "":
                problems.append({"designator": str(designator), "key": "",
                                 "reason": "empty parameter name"})
                continue
            why = _stamp_text_problem(str(key), text)
            if why:
                problems.append({"designator": str(designator),
                                 "key": str(key), "reason": why})
                continue
            fields.append(f"{stamp_encode(key)}={stamp_encode(text)}")
        if len(fields) > 1:
            ops.append(";".join(fields))
    return "~~".join(ops), problems

#: What every caller says when a stamp reply came from a handler older than
#: the encoding. One wording, so a log search finds every path that hit it.
STAMP_SKEW_NOTE = ("stamp handler predates pct1: values containing % ; ~ = "
                   "were written escaped; redeploy scripts and re-run")


def is_stamp_reply(reply: object) -> bool:
    """True when ``reply`` is what ``Gen_SetSchComponentsParameters``
    answered, rather than a refusal that never reached Altium.

    ``send_command`` and ``send_command_async`` RETURN a policy refusal
    (``{"success": False, "error": {...}}``) instead of raising it. That
    dict has no ``value_encoding`` either, so judging it as a reply would
    report a stale script, and a routing table, for writes that never
    happened. Every handler reply carries ``updated``.
    """
    return (isinstance(reply, dict) and reply.get("success") is not False
            and "updated" in reply)


def stamp_reply_problem(reply: object) -> str:
    """:data:`STAMP_SKEW_NOTE` when a stamp reply shows deploy skew, or "".

    A handler that predates ``PctDecode`` ignores ``value_encoding`` and
    writes the escapes literally: a datasheet URL's ``?a=b`` becomes
    ``?a%3Db``, ``%20`` becomes ``%2520``. Plain text is unaffected, so the
    only evidence is the missing echo. The bulk tool, the plan executor and
    the sheet emitter all send this payload; all three ask here, so none
    of them discards the one signal that the values went in wrong.

    Not a stamp reply at all (a refusal, ``None``) is not skew: "".
    """
    if not is_stamp_reply(reply):
        return ""
    if reply.get("value_encoding") == STAMP_ENCODING:
        return ""
    return STAMP_SKEW_NOTE
