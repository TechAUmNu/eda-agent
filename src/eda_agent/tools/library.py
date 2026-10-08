# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library management tools for Altium Designer MCP Server."""

import asyncio
import difflib
import re
from pathlib import Path
from typing import Any, Optional
from ..bridge import get_bridge
from ..bridge.payload import (MAX_COORD_MILS, finite_number, join_ops,
                              name_wire_problem, non_finite, payload_safe,
                              unsendable_chars, whole_number, wire_bool)
from ..libimport import extract_cse_zip, inspect_cse_zip
from .bulk_hints import BulkHintTracker
from .datasheet_hints import tag_response
from ..config import get_config
from ..atomicfile import replace_with_retry


def _encode_layer_ops(layers) -> "str | dict":
    """Layer dicts to the batch string, or a refusal dict.

    Shared by ``lib_set_mech_layers`` and ``lib_run_across``. It lived
    inside the first of those, so a sweep passing the same list through
    the second sent a JSON array where the handler expected the encoded
    string. The handler parsed no operations from it, changed nothing,
    and reported success, which across twenty two libraries read as
    twenty two successes and no work done.
    """
    if not isinstance(layers, (list, tuple)):
        return {"success": False,
                "error": (f"layers must be a list of dicts, got "
                          f"{type(layers).__name__}")}
    ops: list[str] = []
    for entry in layers:
        if not isinstance(entry, dict):
            return {"success": False,
                    "error": f"each layer must be a dict, got {entry!r}"}
        name = str(entry.get("layer") or "").strip()
        if not name:
            return {"success": False,
                    "error": "every layer entry needs a 'layer' key"}
        # layer selects, name renames and kind is a vocabulary word, so all
        # three are refused by the name rule, never sanitised. The old
        # check looked for '~~' only: name='A;enabled=false' went out as a
        # live enabled=false ahead of the real flag, and GetBatchField's
        # first match won.
        for key, value in (("layer", name), ("name", entry.get("name")),
                           ("kind", entry.get("kind"))):
            why = "" if value is None else name_wire_problem(str(value))
            if why:
                return {"success": False,
                        "error": f"{key} {str(value)!r} {why}"}
        fields = [f"layer={name}"]
        if entry.get("name") is not None:
            fields.append(f"name={entry['name']}")
        if entry.get("enabled") is not None:
            # By the one boolean rule: truthiness read "false" as true.
            enabled = wire_bool(entry["enabled"])
            if enabled is None:
                return {"success": False,
                        "error": (f"enabled must be true or false, got "
                                  f"{entry['enabled']!r}")}
            fields.append(f"enabled={'true' if enabled else 'false'}")
        if entry.get("kind") is not None:
            fields.append(f"kind={entry['kind']}")
        ops.append(";".join(str(f) for f in fields))

    if not ops:
        return {"success": False,
                "error": ("layers is empty, so nothing would change. An "
                          "empty request that reports success is how a "
                          "library gets skipped unnoticed")}
    return join_ops(ops)


# Schematic symbol grid is 100 mils. Every pin Location and every
# rectangle/line corner authored via the lib_add_* tools is rounded to
# this grid before the bridge call. Off-grid pins break wire routing
# in placed instances; off-grid rectangle corners produce blurry-looking
# bodies and prevent the Altium snap mechanism from aligning them.
# This is a hard invariant, not a hint -- every coord goes through here.
_SCHEMATIC_GRID_MILS = 100

# Footprint-policy sweep: footprints per bulk-geometry page, and the per-page
# timeout. The first page also opens the PcbLib, which on a large library is
# seconds; the default command timeout is far too short for it.
_GEOMETRY_PAGE = 250
_GEOMETRY_TIMEOUT = 120.0

# A repaired designator further than this from its pads did not land on the
# part. The biggest footprints here span a couple of thousand mils; a designator
# dropped at the board origin is ~50000 away.
_STRAY_MILS = 5000

# Apply/reload passes before giving up. Each pass fixes what the previous one's
# geometry change revealed (a resized text has a new bounding box). Convergence
# is fast -- ~90% in the first pass -- so this is a runaway guard, not a budget.
_MAX_PASSES = 6


#: Where lib_link_3d_model may put a body in X and Y. "none" keeps the
#: original behaviour (offsets are a move from wherever the body lands);
#: the other two are computed from the box read back after the add.
_FOOTPRINT_3D_ALIGN = ("none", "center", "pads")


#: Names whose value is not a finite number (bools included). The one
#: guard lives in bridge/payload.py; a copy here once treated True as 1.0
#: while its siblings refused it.
_non_finite = non_finite


def _batch_encoding() -> str:
    """The encoding the Pascal batch reader actually decodes.

    The batch handlers read with classic AssignFile/ReadLn, which hands
    Altium bytes in the SYSTEM ANSI codepage; Python's name for that is
    "mbcs" (Windows only). The previous latin-1 choice was wrong twice
    on a CP1252 machine: 0x80-0x9F characters (trademark sign, curly
    quotes, en/em dashes) crashed the tool with UnicodeEncodeError, and
    any latin-1/ANSI divergence would have reached Altium as the wrong
    character. Off Windows there is no "active ANSI codepage", so fall
    back to cp1252, the codepage of the Windows machine the workspace
    is shared with in every real deployment.
    """
    try:
        "".encode("mbcs")
        return "mbcs"
    except LookupError:
        return "cp1252"


def _first_unencodable(value: str, encoding: str) -> str:
    """The first character of ``value`` the codepage cannot represent.

    Not taken from UnicodeEncodeError.start/end: the native mbcs codec
    reports those in UTF-16 code units, so slicing the code-point
    string with them lands past an astral character and names ''.
    """
    for ch in value:
        try:
            ch.encode(encoding)
        except UnicodeEncodeError:
            return ch
    return ""


def _edit_line(act: dict) -> str:
    """One tab-separated edit row: name, layer_id, x, y, height, create.

    An empty field tells the script to leave that property alone.
    """
    def _f(key):
        v = act.get(key)
        return "" if v is None else str(v)

    return "\t".join((
        str(act["footprint"]), _f("layer_id"), _f("x"), _f("y"), _f("height"),
        "1" if act.get("create") else "0",
    ))


def write_designator_edits(workspace_dir, actions) -> tuple:
    """Write the edit file the bulk script reads. Returns (path, written,
    rejected).

    A footprint whose name contains a tab or newline cannot be expressed in
    this format; it is rejected rather than silently corrupting the row that
    follows it. Written atomically (temp + replace) so the script can never
    observe a half-written file.
    """
    import os
    import tempfile
    from pathlib import Path

    written, rejected = [], []
    for act in actions:
        name = str(act.get("footprint", ""))
        if not name or "\t" in name or "\n" in name or "\r" in name:
            rejected.append({"footprint": name,
                             "reason": "name contains a tab or newline"})
            continue
        written.append(act)

    path = Path(workspace_dir) / "designator_edits.tsv"
    body = "".join(_edit_line(a) + "\n" for a in written)
    fd, tmp = tempfile.mkstemp(dir=str(workspace_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
        replace_with_retry(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path, written, rejected


def _same_plan(a, b) -> bool:
    """Two plans that ask for exactly the same edits. If a pass applied and the
    next plan is identical, nothing landed and repeating will not help."""
    def key(acts):
        return sorted((x["footprint"], x.get("x"), x.get("y"),
                       x.get("layer_id"), x.get("height"), x.get("create"))
                      for x in acts)
    return bool(a) and key(a) == key(b)


async def _reload_library(bridge, library_path) -> bool:
    """Close and reopen the PcbLib so Altium rebuilds its caches from disk.

    ``IPCB_Text.BoundingRectangle`` is populated at load and is NOT refreshed by
    assigning XLocation or Size. Without a reload, everything read after a write
    reports the text's OLD extent, and a centring pass computed from that moves
    the text by a wrong delta. Save before calling this.
    """
    if not library_path:
        return False
    try:
        res = await bridge.send_command_async(
            "library.reload_library", {"library_path": library_path},
            timeout=_GEOMETRY_TIMEOUT)
        return bool(isinstance(res, dict) and res.get("reloaded"))
    except Exception:
        return False


async def _save_library_verified(bridge, library_path: str) -> dict:
    """Save ONE library, and say whether the file actually changed on disk.

    Two independent witnesses, because a save Altium declines does not
    raise: the handler's own FileAge before and after (two-second
    resolution, so a second save within the same two seconds can read as
    unchanged) and this process's nanosecond mtime of the same file.
    Either one moving is a write. Neither moving is a refusal, and the
    caller must not go on to reload, because closing a dirty library raises
    Altium's Unsaved Changes prompt and blocks the bridge.
    """
    if not str(library_path or "").strip():
        # Path("") is the working directory, whose mtime would stand in
        # for the library's; and there is no document to save.
        return {"ok": False, "written_to_disk": False, "library_path": "",
                "reason": "no library path to save (the PcbLib could not be "
                          "resolved)"}

    def mtime():
        try:
            return Path(library_path).stat().st_mtime_ns
        except OSError:
            return None

    before = mtime()
    try:
        reply = await bridge.send_command_async(
            "library.save_library", {"library_path": library_path},
            timeout=_GEOMETRY_TIMEOUT)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "reason": f"the save was refused: {exc}",
                "library_path": library_path, "written_to_disk": False,
                "code": getattr(exc, "code", type(exc).__name__),
                "details": getattr(exc, "details", None)}
    after = mtime()
    out = dict(reply) if isinstance(reply, dict) else {"reply": reply}
    out["mtime_changed"] = (None if before is None and after is None
                            else before != after)
    out["written_to_disk"] = (bool(out["mtime_changed"])
                              or bool(out.get("file_age_changed")))
    out["ok"] = out["written_to_disk"] and not out.get("still_modified")
    if not out["ok"]:
        out["reason"] = (
            "the document still reads modified after the save"
            if out["written_to_disk"] else
            "the file did not change on disk: Altium declines a save while "
            "a command is active, without raising. Run "
            "app_exit_active_command and save again")
    return out


#: The persistence proof every library delete/modify/3D tool points at.
_FILE_PROOF = (
    "lib_save_library, then lib_read_pcblib_file(library_path, "
    "footprint_name): it reads the SAVED .PcbLib with no Altium involved, "
    "so it shows what reached disk rather than what the editor holds")


async def _reload_first(bridge, library_path: str, what: str,
                        skip_reason: str = "") -> tuple[dict, Optional[dict]]:
    """Save and reopen the library BEFORE a removal, so it removes disk-loaded
    primitives. Returns ``(record, refusal)``; ``refusal`` is not None when
    the removal must not be sent.

    MEASURED 2026-10-04 (docs/RELEASE_VERIFICATION.md step 31, and the
    block above Lib_DeleteFootprintPads in Library.pas): a pad created in
    the same session and removed from its footprint was written back by
    the next save (twice, the second time with remove_from_board=True),
    while the same removal after lib_reload_library persisted. No proven
    Altium member removes the session-created object's library-board
    registration, so the tool makes every doomed primitive a disk-loaded
    one: save the library (every pending edit in it, not only this
    footprint's) and reopen it, then remove.

    A review (2026-10-04) noted the same evidence fits a second theory:
    the save rebuilds the CURRENT footprint from the library board's copy.
    The delete that persisted was on a disk-loaded footprint that was
    probably NOT current, and a reopen does not choose which footprint is
    current. So the callers also ask the handler to park the current
    footprint elsewhere (``park_current``), and then save again and read
    the FILE (``verify_on_disk``), which settles whether the removal stuck
    whichever theory holds. MEASURED 2026-10-05 (step 31 item 3b): the
    second theory holds. A disk-loaded pad removed while its footprint
    was CURRENT came back with the save, so the deletes now park by
    default even when this does not run (_park_choice).

    Refuses rather than deletes when the save does not reach disk (a
    reload of a dirty library raises Altium's Unsaved Changes prompt and
    blocks the bridge) or the reopen fails. Without a path (the caller
    resolves the focused .PcbLib first, _resolve_library_path) there is no
    file to save and reopen, so it is SKIPPED, with ``skip_reason``, and
    the caller puts that at the top of its reply.
    """
    path = str(library_path or "").strip()
    if not path:
        return ({"requested": True, "ran": False, "skipped": (
            (skip_reason or "no library_path was given")
            + ", so there was no file to save and reopen. A primitive "
            "created in this session may then come back after the save; "
            "pass library_path to have it settled first")}, None)
    save = await _save_library_verified(bridge, path)
    rec: dict = {"requested": True, "ran": True, "saved": bool(save.get("ok")),
                 "save": save, "reloaded": False}
    if not save.get("ok"):
        return rec, {"ok": False, "reload_first": rec, "reason": (
            f"reload_first: the library could not be saved first, so it was "
            f"not reopened and nothing was {what}: {save.get('reason')}. Fix "
            f"that, or pass reload_first=False to remove from the library as "
            f"it stands in memory")}
    rec["reloaded"] = await _reload_library(bridge, path)
    if not rec["reloaded"]:
        return rec, {"ok": False, "reload_first": rec, "reason": (
            f"reload_first: the library was saved but did not reopen, so "
            f"nothing was {what}. Open it again (proj_open / app_list_documents"
            f") and retry")}
    return rec, None


def _with_reload_record(result, rec: Optional[dict]):
    """Attach the reload_first record to a handler reply.

    A SKIPPED reload_first is also put at the top of the reply
    (``warning``, and in front of ``note``): a removal without it is the
    one a save has been measured to write back, and a skip buried in a
    nested record is one nobody reads.
    """
    if rec is None:
        return result
    out = dict(result) if isinstance(result, dict) else {"reply": result}
    out["reload_first"] = rec
    if not rec.get("ran") and rec.get("skipped"):
        warning = "reload_first was SKIPPED: " + str(rec["skipped"])
        out["warning"] = warning
        note = out.get("note")
        out["note"] = warning + (f" {note}" if note else "")
    return out


def _add_warning(result, warning: str) -> dict:
    """Put ``warning`` at the TOP of a reply, after any warning already
    there, and in front of ``note`` (as _with_reload_record does)."""
    out = dict(result) if isinstance(result, dict) else {"reply": result}
    prior = out.get("warning")
    out["warning"] = f"{prior} {warning}" if prior else warning
    note = out.get("note")
    if not (isinstance(note, str) and warning in note):
        out["note"] = warning + (f" {note}" if note else "")
    return out


#: The warning a replace through the HANDLER's own replace carries.
_HANDLER_REPLACE_WARNING = (
    "replace ran through the link handler's own replace, which removes the "
    "old 3D bodies while the footprint is CURRENT; measured 2026-10-05 "
    "(docs/RELEASE_VERIFICATION.md step 31, item 3b) a primitive removed "
    "from the current footprint is written back by the next save, so the "
    "old bodies may be saved beside the new one. Name the footprint "
    "(component_name) so the tool removes them with the footprint parked, "
    "and check with lib_save_library + lib_read_pcblib_file.")


def _mark_failed(result, reason: str) -> dict:
    """``ok`` AND ``success`` false, with the reason.

    Both, because a bridge reply carries ``success: true`` and a caller
    that checks ``success`` would otherwise accept what ``ok`` refused
    (exactly how the misplaced 3D body of 2026-10-04 passed).
    """
    out = dict(result) if isinstance(result, dict) else {"reply": result}
    out["ok"] = False
    out["success"] = False
    out["reason"] = reason
    return out


#: Every object_type word Utils.pas ObjectTypeFromStringPCB accepts, after
#: NormalizeTypeName (lower case; spaces, '_' and '-' dropped; a leading
#: 'e' dropped). Checked HERE so a typo is refused before reload_first
#: saves and reopens the library for a delete the handler would refuse.
#: tests/test_session_primitive_persistence.py parses the Pascal function
#: and fails when the two drift.
_PCB_OBJECT_TYPE_WORDS = frozenset({
    "trackobject", "track", "padobject", "pad", "viaobject", "via",
    "componentobject", "component", "arcobject", "arc", "fillobject",
    "fill", "textobject", "text", "polyobject", "poly", "polygon",
    "regionobject", "region", "ruleobject", "rule", "dimensionobject",
    "dimension", "connectionobject", "connection", "ratsnest",
    "componentbodyobject", "componentbody", "body", "3dbody",
})


def _normalize_type_name(value: str) -> str:
    """Utils.pas NormalizeTypeName, in Python."""
    word = str(value or "").lower()
    for ch in (" ", "_", "-"):
        word = word.replace(ch, "")
    if len(word) > 1 and word.startswith("e"):
        word = word[1:]
    return word


async def _resolve_library_path(bridge, library_path: str) -> tuple[str, str]:
    """The .PcbLib a reload_first applies to: ``(path, source)``.

    ``library_path`` when given (source ``"argument"``). Otherwise the
    FOCUSED document's path, read with application.get_active_document,
    when that document is a .PcbLib (source ``"focused document"``): the
    library a path-less call acts on anyway, so the common path-less
    delete gets the save-and-reopen too instead of keeping the measured
    failure. ``("", why)`` when neither resolves; the caller then skips
    reload_first and says so at the top of its reply.
    """
    path = str(library_path or "").strip()
    if path:
        return path, "argument"
    try:
        active = await bridge.send_command_async(
            "application.get_active_document")
    except Exception as exc:                     # noqa: BLE001
        return "", (f"no library_path was given and the focused document "
                    f"could not be read ({exc})")
    focused = ""
    if isinstance(active, dict):
        focused = str(active.get("file_path") or "").strip()
    if focused.lower().endswith(".pcblib"):
        return focused, "focused document"
    if focused:
        return "", (f"no library_path was given and the focused document "
                    f"is not a .PcbLib ({focused})")
    return "", "no library_path was given and no focused .PcbLib was found"


def _saved_footprint(path: str,
                     footprint: str) -> tuple[Optional[dict], Optional[str]]:
    """One footprint as the SAVED file holds it, or ``(None, why)``.

    Pure Python (fileio.altium_pcblib). An incomplete parse is refused: a
    partial record list cannot prove what the file holds.
    """
    from ..fileio.altium_pcblib import read_pcblib
    try:
        result = read_pcblib(path, footprint)
    except Exception as exc:                     # noqa: BLE001
        return None, f"the saved file could not be read: {exc}"
    if not result.get("found"):
        return None, f"footprint {footprint!r} is not in the saved file"
    entry = (result.get("footprints") or [None])[0]
    if not entry or not entry.get("complete"):
        return None, ("the saved footprint could not be read to the end: "
                      + str((entry or {}).get("parse_error")))
    return entry, None


def _record_total(entry: dict) -> int:
    """Every primitive record a saved footprint holds."""
    kinds = ("pads", "tracks", "arcs", "vias", "fills", "texts", "regions",
             "bodies")
    return sum(len(entry.get(k) or []) for k in kinds) + sum(
        (entry.get("other_records") or {}).values())


async def _save_and_read(bridge, path: str,
                         footprint: str) -> tuple[dict, Optional[dict]]:
    """Save the library, then read the footprint back FROM THE FILE.

    Returns ``(record, entry)``; ``entry`` is None when either step
    failed, and ``record`` says which (``saved`` False: the save did not
    reach disk; ``checked`` False with ``saved`` True: the file could
    not be read).
    """
    save = await _save_library_verified(bridge, path)
    rec: dict = {"checked": False, "saved": bool(save.get("ok")),
                 "save": save, "library_path": path}
    if not save.get("ok"):
        rec["reason"] = ("the verifying save did not reach disk, so the "
                         "change is in memory only: "
                         + str(save.get("reason")))
        return rec, None
    entry, why = _saved_footprint(path, footprint)
    if entry is None:
        rec["reason"] = why
        return rec, None
    rec["checked"] = True
    return rec, entry


def _resolve_pad_selectors(names: list, pads: list) -> tuple[dict, list]:
    """Lib_DeleteFootprintPads's selector resolution, over a name list.

    Same rules as the handler: a designator must match, a designator on
    several pads needs ``index`` or ``all``, and an index must exist.
    Returns ``({designator: pads picked}, problems)``. Picks are by
    position and de-duplicated across selectors, as the handler's Doomed
    list is by address.
    """
    picked: set = set()
    problems: list = []
    for sel in pads:
        desig = str(sel.get("designator")).strip()
        take_all = False
        if sel.get("all") is not None:
            take_all = bool(wire_bool(sel["all"]))
        idx = None
        if sel.get("index") is not None:
            idx = _pad_index(sel["index"])
        where = [i for i, n in enumerate(names) if n == desig]
        matches = len(where)
        if matches == 0:
            problems.append(f'{desig}: no pad named "{desig}"')
        elif idx is None and not take_all and matches > 1:
            problems.append(f'{desig}: {matches} pads are named "{desig}"; '
                            f"pass index (0 to {matches - 1}) or all=true")
        elif idx is not None and idx >= matches:
            problems.append(f"{desig}: index {idx} asked for, but only "
                            f'{matches} pad(s) are named "{desig}"')
        elif idx is not None:
            picked.add(where[idx])
        else:
            picked.update(where)
    counts: dict = {}
    for i in picked:
        counts[names[i]] = counts.get(names[i], 0) + 1
    return counts, problems


async def _precheck_footprint(bridge, path: str,
                              footprint: str) -> tuple[dict, Optional[str]]:
    """Is ``footprint`` in the library? Read-only, BEFORE reload_first.

    Returns ``(record, refusal_reason)``. Refuses only on a definite
    answer: a footprint list that was read and does not hold the name
    (compared case-insensitively, as the delete handlers compare). A read
    that fails, or a reply with no list, is inconclusive and lets the
    call go on; the handler still refuses a missing footprint, only after
    the save.
    """
    rec: dict = {"ran": True}
    try:
        reply = await bridge.send_command_async(
            "library.get_footprints", {"library_path": path})
    except Exception as exc:                     # noqa: BLE001
        rec.update(ran=False, error=str(exc))
        return rec, None
    listed = reply.get("footprints") if isinstance(reply, dict) else None
    if not isinstance(listed, list):
        rec.update(ran=False,
                   error="the footprint list was not in the reply")
        return rec, None
    names = [str(f.get("name", "")) for f in listed if isinstance(f, dict)]
    exact = [n for n in names if n.lower() == footprint.lower()]
    if not exact:
        close = difflib.get_close_matches(footprint, names, n=5, cutoff=0.5)
        return rec, (f"footprint {footprint!r} is not in {path}"
                     + (f" (closest: {', '.join(close)})" if close else "")
                     + ". Nothing was saved, reopened or removed")
    rec["name"] = exact[0]
    return rec, None


def _pads_persisted(before: dict, after: dict, removed: dict) -> dict:
    """Compare a footprint's pads in the saved file before and after.

    ``removed`` is ``{designator: pads the delete removed}``. A designator
    is ``absent`` when the file holds exactly ``before - removed`` pads
    named so afterwards, ``still_present`` when it holds more (the
    measured failure: the save wrote the pad back). The pad total must
    drop by exactly the number removed too, so a pad the save invented
    elsewhere is not missed.
    """
    def count(entry):
        out: dict = {}
        for pad in entry.get("pads") or []:
            name = str(pad.get("designator", ""))
            out[name] = out.get(name, 0) + 1
        return out

    b, a = count(before), count(after)
    rows, absent, still = [], [], []
    for desig, n in sorted(removed.items()):
        expected = b.get(desig, 0) - n
        rows.append({"designator": desig, "before": b.get(desig, 0),
                     "removed": n, "after": a.get(desig, 0),
                     "expected_after": expected})
        (still if a.get(desig, 0) > expected else absent).append(desig)
    total_before = len(before.get("pads") or [])
    total_after = len(after.get("pads") or [])
    expected_total = total_before - sum(removed.values())
    return {"absent": absent, "still_present": still,
            "by_designator": rows, "pads_before": total_before,
            "pads_after": total_after, "expected_pads_after": expected_total,
            "ok": not still and total_after == expected_total}


def _primitives_persisted(before: dict, after: dict, removed: int) -> dict:
    """The saved footprint's record total must drop by exactly ``removed``."""
    kinds = ("pads", "tracks", "arcs", "vias", "fills", "texts", "regions",
             "bodies")
    total_before = _record_total(before)
    total_after = _record_total(after)
    expected = total_before - removed
    return {"records_before": total_before, "records_after": total_after,
            "expected_records_after": expected,
            "still_present": max(0, total_after - expected),
            "by_kind": {k: {"before": len(before.get(k) or []),
                            "after": len(after.get(k) or [])} for k in kinds},
            "ok": total_after == expected}


def _close(a, b, tol: float = 0.01) -> bool:
    try:
        return a is not None and b is not None and abs(float(a) - float(b)) \
            <= tol
    except (TypeError, ValueError):
        return False


def _body_matches(model: dict, expected: dict) -> bool:
    """A saved body's model against lib_link_3d_model's expected_in_file.

    MODEL.2D.X/Y only when the reply gave them (align="none"); rotation
    and Dz always. A null rotation in the file reads as 0, which is what
    Altium omits.
    """
    if expected.get("model_2d_x_mils") is not None:
        if not (_close(model.get("x_mils"), expected["model_2d_x_mils"])
                and _close(model.get("y_mils"),
                           expected.get("model_2d_y_mils"))):
            return False
    for axis in ("x", "y", "z"):
        if not _close(model.get(f"rot_{axis}") or 0.0,
                      expected.get(f"model_3d_rot{axis}") or 0.0):
            return False
    return _close(model.get("dz_mils") or 0.0,
                  expected.get("model_3d_dz_mils") or 0.0)


#: Canonical layer names, as _layer_key spells them. The saved file names a
#: body's layer canonically (``Mechanical13``, fileio.altium_pcblib), while
#: a link's ``layer`` and the in-memory reply may use the library's OWN name
#: for that layer, which nothing here can map without the layer stack.
_CANONICAL_LAYER = re.compile(
    r"mechanical\d+|toplayer|bottomlayer|midlayer\d+|internalplane\d+|"
    r"(top|bottom)(overlay|paste|solder)|keepout|multilayer|drillguide|"
    r"drilldrawing")


def _layer_key(name) -> str:
    """A layer name folded for comparison: case, spaces, '_' and '-' off,
    and the short forms the bridge accepts spelled out (``Mech13`` ->
    ``mechanical13``, ``Plane2`` -> ``internalplane2``, ``Top`` ->
    ``toplayer``)."""
    k = re.sub(r"[\s_\-]", "", str(name or "")).lower()
    m = re.fullmatch(r"mech(?:anical)?0*(\d+)", k)
    if m:
        return f"mechanical{int(m.group(1))}"
    m = re.fullmatch(r"(?:internal)?plane0*(\d+)", k)
    if m:
        return f"internalplane{int(m.group(1))}"
    if k in ("top", "bottom"):
        return k + "layer"
    if k == "keepoutlayer":
        return "keepout"
    return k


def _layer_agrees(wanted, got) -> Optional[bool]:
    """Is a body on ``wanted``? True, False, or None when it cannot be told.

    None when either name is missing, or when they differ and one is not a
    canonical name: a library may call Mechanical 13 "3D Body", the saved
    file then says Mechanical13 and the reply "3D Body", and without the
    layer stack's map a difference there proves nothing.
    """
    kw, kg = _layer_key(wanted), _layer_key(got)
    if not kw or not kg:
        return None
    if kw == kg:
        return True
    if _CANONICAL_LAYER.fullmatch(kw) and _CANONICAL_LAYER.fullmatch(kg):
        return False
    return None


def _link_persisted(before: dict, after: dict, removed: int,
                    expected: Optional[dict],
                    layer: Optional[str] = None) -> dict:
    """Bodies in the saved file after a replace: count, placement, layer.

    The count must be ``before - removed + 1``, and one saved body must
    carry the values the link reply's ``expected_in_file`` names (the
    model origin relative to the footprint origin for align="none", the
    rotation and the Dz). Its stored heights are listed beside it so the
    Dz-lift question (does Altium build Dz into OVERALLHEIGHT /
    STANDOFFHEIGHT) can be read off the same reply.

    With ``layer`` (the layer the link asked for, or inherited), a matching
    body must also be on it (``layer_ok``, _layer_agrees): a body saved on
    Mechanical 1 instead of Mechanical 13 is ``ok`` false. ``layer_ok`` is
    None, and does not fail the check, when the names cannot be compared
    (a library-specific layer name against the file's canonical one).
    """
    bodies_before = len(before.get("bodies") or [])
    bodies = after.get("bodies") or []
    expected_count = bodies_before - removed + 1
    out: dict = {"bodies_before": bodies_before, "removed": removed,
                 "bodies_after": len(bodies),
                 "expected_bodies_after": expected_count,
                 "expected_in_file": expected}
    matching = []
    if isinstance(expected, dict):
        matching = [b for b in bodies
                    if _body_matches(b.get("model") or {}, expected)]
    out["matching_bodies"] = [
        {"model": b.get("model"), "layer": b.get("layer"),
         "standoff_height_mils": b.get("standoff_height_mils"),
         "overall_height_mils": b.get("overall_height_mils")}
        for b in matching]
    if not matching:
        out["saved_bodies"] = [
            {"model": b.get("model"), "layer": b.get("layer"),
             "standoff_height_mils": b.get("standoff_height_mils"),
             "overall_height_mils": b.get("overall_height_mils")}
            for b in bodies]
    out["ok"] = len(bodies) == expected_count and bool(matching)
    if layer:
        verdicts = [_layer_agrees(layer, b.get("layer")) for b in matching]
        layer_ok: Optional[bool] = None
        if any(v is True for v in verdicts):
            layer_ok = True
        elif verdicts and all(v is False for v in verdicts):
            layer_ok = False
        out["layer_wanted"] = layer
        out["layer_ok"] = layer_ok
        if layer_ok is None and matching:
            out["layer_unverified"] = (
                f"{layer!r} could not be compared with the saved layer "
                f"name(s) {sorted({str(b.get('layer')) for b in matching})}"
                f": one is the library's own name for a layer, and the "
                f"saved file names layers canonically")
        if layer_ok is False:
            out["ok"] = False
    return out


async def _replace_then_link(bridge, params: dict, path: str,
                             footprint: str, remove_from_board: bool,
                             saved_first: bool = True):
    """A replace=True link as DELETE, then a plain LINK.

    Used for EVERY replace that names its footprint, with or without
    reload_first. The handler's own replace removes the old bodies while
    the footprint is CURRENT (it must be, to add the new one), and
    MEASURED 2026-10-05 (docs/RELEASE_VERIFICATION.md step 31, item 3b) a
    primitive removed while its footprint is current is written back by
    the next save: the placeholder would end up in the file beside the new
    STEP. So the removal goes through lib_delete_footprint_primitives
    (object_type componentbody) with park_current, which removes from a
    footprint that is NOT current, and the link then runs with
    replace=False and makes it current to add.

    The replaced body's layer is read first (lib_get_footprint_bodies,
    read-only) and passed as ``layer`` when the caller gave none, so the
    new body inherits it exactly as the handler's replace did; it is
    reported as ``replaced.inherited_layer``.

    ORDER COST: the old bodies go before the new model has loaded (callers
    check the model file exists first). If the link then fails, the reply
    says how to restore: with ``saved_first`` (reload_first saved just
    before) lib_reload_library restores them; without it lib_reload_library
    goes back to the LAST save and discards every other unsaved edit too.

    Returns the link reply with ``replaced`` describing the delete, or a
    failure dict (``ok`` false). Bridge-gone errors are re-raised.
    """
    from ..bridge.exceptions import AltiumNotRunningError, AltiumTimeoutError

    replaced: dict = {"requested": True, "route": "delete_then_link",
                      "saved_first": bool(saved_first)}
    link_params = dict(params)
    link_params["replace"] = "false"
    link_params.pop("remove_from_board", None)
    bodies = None
    try:
        reply = await bridge.send_command_async(
            "library.get_footprint_bodies",
            {"footprint_name": footprint, "library_path": path,
             "model_state": "false"})
        if isinstance(reply, dict) and isinstance(reply.get("bodies"), list):
            bodies = reply["bodies"]
    except (AltiumNotRunningError, AltiumTimeoutError):
        raise
    except Exception as exc:                     # noqa: BLE001
        replaced["bodies_read_error"] = str(exc)
    layer_source = None
    if bodies is not None:
        replaced["bodies_before"] = len(bodies)
        if "layer" not in link_params:
            first = next((str(b.get("layer") or "") for b in bodies
                          if isinstance(b, dict) and b.get("layer")), "")
            if first:
                link_params["layer"] = first
                layer_source = "replaced body"
                replaced["inherited_layer"] = first
    removed = 0
    if bodies is None or bodies:
        delete_params = {"footprint_name": footprint,
                         "object_type": "componentbody", "confirm": "true",
                         "library_path": path}
        if remove_from_board:
            delete_params["remove_from_board"] = "true"
        else:
            delete_params["park_current"] = "true"
        dele = await bridge.send_command_async(
            "library.delete_footprint_primitives", delete_params)
        dele = dele if isinstance(dele, dict) else {}
        removed = int(dele.get("removed") or 0)
        replaced.update({
            "removed_bodies": removed,
            "remove_calls": dele.get("remove_calls"),
            "remaining_before_add": dele.get("still_listed"),
            "board_still_listed": dele.get("board_still_listed"),
            "target_was_current": dele.get("target_was_current"),
            "parked_elsewhere": dele.get("parked_elsewhere"),
            "target_current_during_removal": dele.get(
                "target_current_during_removal"),
            "board_removal": dele.get("board_removal"),
        })
    else:
        replaced.update({"removed_bodies": 0, "remaining_before_add": 0,
                         "board_removal": {"requested": False}})
    try:
        result = await bridge.send_command_async(
            "library.link_3d_model", link_params)
    except (AltiumNotRunningError, AltiumTimeoutError):
        raise
    except Exception as exc:                     # noqa: BLE001
        if not removed:
            raise
        gone = (f"the {removed} old 3D "
                f"bod{'y was' if removed == 1 else 'ies were'} removed in "
                f"memory, then the link failed: {exc}. Do NOT save now: the "
                f"save would write {footprint!r} with no 3D body. ")
        if saved_first:
            how = ("The library was saved just before (reload_first), so "
                   "lib_reload_library restores the old bodies (and drops "
                   "every other unsaved edit made since that save); or fix "
                   "the model and link it again")
        else:
            how = ("Nothing was saved first (reload_first did not run), so "
                   "lib_reload_library goes back to the LAST SAVE and "
                   "discards every other unsaved edit in this library too; "
                   "the cheaper repair is usually to fix the model and link "
                   "it again")
        return {"ok": False, "success": False, "replaced": replaced,
                "code": getattr(exc, "code", type(exc).__name__),
                "reason": gone + how}
    out = dict(result) if isinstance(result, dict) else {"reply": result}
    out["replaced"] = replaced
    if layer_source and isinstance(out.get("applied"), dict):
        out["applied"] = dict(out["applied"], layer_source=layer_source)
    return out


def _batch_recovery(where: str, saved_first: bool, linked: int) -> str:
    """How to settle a failed footprint of lib_auto_link_3d_models.

    The two routes cost different things, and the reply has to say both:
    lib_reload_library throws away EVERY successful link of the batch (and
    any other unsaved edit), while a save writes the failed footprint as it
    stands in memory.
    """
    reload_cost = (
        f"lib_reload_library restores {where} from the "
        + ("save reload_first made before the batch" if saved_first
           else "LAST save (reload_first did not run)")
        + (f", but also throws away the {linked} successful link(s) of "
           f"this batch" if linked else "")
        + " and every other unsaved edit")
    return (
        f"Settle {where} BEFORE any save, which would write it as it stands "
        f"in memory. Usually cheapest: fix its model and re-run that one "
        f"footprint with lib_link_3d_model(replace=True), whose "
        f"reload_first first saves the batch as it stands; if its old "
        f"bodies matter and the re-run might fail again, {reload_cost}.")


def _park_choice(park_current: Optional[bool],
                 remove_from_board: bool) -> tuple[bool, Optional[str]]:
    """Whether a library delete asks the handler to park the footprint.

    ``(park, refusal)``. Default (None): park whenever ``remove_from_board``
    is off, with or without reload_first. MEASURED 2026-10-05
    (docs/RELEASE_VERIFICATION.md step 31, item 3b): a DISK-LOADED pad,
    removed while its footprint was CURRENT, was written back by the next
    save; the default route works because after the reopen the target was
    not current. Being current is ENOUGH to bring a removed primitive back
    (whether being added this session also is, on a footprint that is NOT
    current, is unmeasured; the likely mechanism, the save writing the
    library board's copy of the current footprint, is inferred), so a
    no-reload delete parks too. The handler parks
    only a footprint that IS current (Library.pas LibParkCurrentElsewhere)
    and never with remove_from_board, whose board half needs it current,
    so True with remove_from_board is refused rather than ignored.
    """
    if park_current is None:
        return (not remove_from_board), None
    if park_current and remove_from_board:
        return False, (
            "park_current=True and remove_from_board=True contradict: the "
            "board half needs the footprint CURRENT, and parking makes "
            "another footprint current. Nothing was sent")
    return bool(park_current), None


def _rename_ops(renames) -> tuple[list[str], list[dict]]:
    """Wire ops for ``library.rename_footprints``, and the renames refused.

    A name is REFUSED, never sanitised. ``payload_safe`` turns ';' into ','
    and sends the result, which is right for free text and wrong for a
    name: the footprint would be renamed to something nobody asked for.
    The rule is ``name_wire_problem``'s, shared with every builder that
    sends a name: separators, a trailing '~' (it runs into the '~~'
    after ``new=``), and characters the bridge flattens.
    """
    ops: list[str] = []
    refused: list[dict] = []
    for item in renames or []:
        if not isinstance(item, dict):
            refused.append({"item": repr(item)[:80], "reason": (
                "each rename is {'from': <current name>, 'to': <new name>}")})
            continue
        old = str(item.get("from") or "")
        new = str(item.get("to") or "")
        problems = []
        for key, value in (("from", old), ("to", new)):
            if not value.strip():
                problems.append(f"'{key}' is empty")
                continue
            why = name_wire_problem(value)
            if why:
                problems.append(f"'{key}' {why}")
        if not problems and old == new:
            problems.append("'from' and 'to' are the same name")
        if problems:
            refused.append({"from": old, "to": new,
                            "reason": "; ".join(problems)})
        else:
            ops.append(f"old={old};new={new}")
    return ops, refused


async def _apply_edits(bridge, actions, resolved_path, failed):
    """Write one batch of designator edits. Returns (applied, created)."""
    edits_path, written, rejected = write_designator_edits(
        bridge.config.workspace_dir, actions)
    failed.extend(rejected)
    if not written:
        return 0, 0

    params = {"edits_path": str(edits_path)}
    if resolved_path:
        params["library_path"] = resolved_path
    try:
        res = await bridge.send_command_async(
            "library.set_designators", params, timeout=_GEOMETRY_TIMEOUT)
        res = res if isinstance(res, dict) else {}
    except Exception as e:
        failed.append({"footprint": "*", "error": str(e)})
        return 0, 0

    if res.get("failed"):
        failed.append({"footprint": "*",
                       "error": f"{res['failed']} edits failed"})
    if res.get("refused"):
        # The script refused to create a designator on a footprint that already
        # had one. Never silent: that guard firing means reader and writer
        # disagree about what exists.
        failed.append({"footprint": "*",
                       "error": f"{res['refused']} creates refused "
                                f"(footprint already had a .Designator)"})
    for name in res.get("missing", []):
        failed.append({"footprint": name, "error": "not found in library"})
    for name in res.get("immovable", []):
        # The script assigned XLocation, read it back, and it had not changed.
        # Silently counting these as applied hid failed repairs behind a
        # success total for an entire session.
        failed.append({"footprint": name,
                       "error": "designator did not move (XLocation refused "
                                "the assignment)"})
    return res.get("applied", 0), res.get("created", 0)


async def _read_library_geometry(bridge, library_path):
    """Page the whole library's policy geometry. Returns (footprints, path)."""
    base: dict[str, Any] = {}
    if library_path:
        base["library_path"] = library_path
    footprints: list[dict[str, Any]] = []
    resolved: Optional[str] = None
    offset, total = 0, None
    while total is None or offset < total:
        page = await bridge.send_command_async(
            "library.get_library_geometry",
            dict(base, offset=offset, limit=_GEOMETRY_PAGE),
            timeout=_GEOMETRY_TIMEOUT,
        )
        if not isinstance(page, dict):
            break
        resolved = resolved or page.get("library_path")
        total = page.get("total") or 0
        batch = page.get("footprints") or []
        footprints.extend(batch)
        if not batch:  # never spin if the script reports a short page
            break
        offset += len(batch)
    return footprints, (resolved or library_path)


def _snap(value: int) -> int:
    """Round a mil coordinate to the schematic 100-mil grid.

    Banker's-style: nearest-50 rounds away from zero. The placement
    pipelines all snap downward (// 100 * 100), but the symbol author
    tools accept user-supplied coords that may be off by a few mils
    (e.g., a hand-typed 503) -- rounding is more forgiving than
    truncating in that case.
    """
    if value >= 0:
        return ((value + _SCHEMATIC_GRID_MILS // 2)
                // _SCHEMATIC_GRID_MILS) * _SCHEMATIC_GRID_MILS
    return -(((-value + _SCHEMATIC_GRID_MILS // 2)
              // _SCHEMATIC_GRID_MILS) * _SCHEMATIC_GRID_MILS)


def _snap_grid(value: int, grid: int) -> int:
    """Round a mil coordinate to an arbitrary grid (<=1 disables snapping).

    For glyph primitives (lines / arcs / polygons), which -- unlike pins
    and rectangle corners (discipline rule 15) -- do NOT need the 100-mil
    grid, so finer detail (cap plates, diode triangles, resistor zigzags)
    can be drawn.
    """
    value = int(value)
    if grid <= 1:
        return value
    if value >= 0:
        return ((value + grid // 2) // grid) * grid
    return -(((-value + grid // 2) // grid) * grid)



#: Only two sequences actually delimit the batch payload, confirmed
#: against the Pascal parser (Main.pas GetBatchField):
#:   Pos(';', ...)  -> fields split on the FIRST ";"
#:   Pos('=', ...)  -> key/value split on the FIRST "=", so a value may
#:                     contain further "=" safely
#: and ops are separated by "~~". A single "~" is NOT a delimiter, and
#: must survive: it is standard overbar notation in pin names (~RESET).
_MULTI_TILDE = re.compile(r"~{2,}")


def _payload_safe(value: object) -> str:
    """Delegates to the shared sanitiser; see bridge/payload.py.

    Kept as a module-local name because a lot of call sites use it,
    but the RULES live in one place now: generic.py had a near-copy
    that collapsed tildes differently, so identical input produced
    different output depending on which tool sent it.
    """
    return payload_safe(value)


def _mil(v: object) -> str:
    """Pad dimension in mils at Altium's 1/10000-mil resolution;
    integers stay integers so existing payloads are unchanged."""
    r = round(float(v), 4)
    return str(int(r)) if r == int(r) else f"{r:.4f}".rstrip("0").rstrip(".")


#: Every numeric field a pad dict may carry, checked before formatting.
_PAD_NUMBER_KEYS = ("x", "y", "x_size", "y_size", "hole_size",
                    "corner_radius", "rotation", "slot_length",
                    "mask_expansion", "paste_expansion", "hole_rotation")


def _pads_payload(pads: list[dict[str, Any]],
                  allow_blank: bool = False) -> tuple[str, int]:
    """Build the ``pads`` batch payload, and count what was dropped.

    Sibling of :func:`_pins_payload`, extracted for the same reason: the
    exact string Pascal parses can be produced without a bridge, so the
    cross-validation suite runs it through the real ``GetBatchField``
    compiled by FPC. Land patterns are where a payload mistake becomes a
    board that cannot be assembled, so this is the payload most worth
    checking against the real parser rather than a reading of it.

    Returns ``(payload, skipped_invalid)``. A pad with a blank
    designator is dropped and counted, so the loss is reported instead
    of silently absorbed, UNLESS the blank is asked for: ``allow_blank``
    for the whole call, ``"allow_blank_designator": True`` on the pad,
    or ``"plated": False`` (an NPTH hole, conventionally unnamed). The
    Pascal writes whatever Name it is given; the drop was only ever a
    guard against a forgotten designator, and a fiducial or mechanical
    pad is unnamed on purpose.

    A pad whose number field is not a finite number, or whose plated /
    allow_blank_designator is not a boolean, is dropped and counted the
    same way: sending it would raise here or write a value nobody asked
    for. So is a corner_radius that is not a whole percentage 0 to 100,
    the rule lib_add_footprint_pad and the pad edits already apply.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for p in pads:
        # Free-text fields are neutralised for the same reason as pins: a
        # designator or layer carrying ";", "=" or "~~" reshapes the
        # payload and lands the pad somewhere else.
        desig = _payload_safe(str(p.get("designator", "")).strip())
        # Booleans by the one rule (wire_bool): truthiness read the string
        # "false" as true and made an NPTH hole plated. A flag that is not
        # a boolean drops the pad, counted, rather than guessing.
        plated = (None if p.get("plated") is None
                  else wire_bool(p.get("plated")))
        blank_flag = (None if p.get("allow_blank_designator") is None
                      else wire_bool(p.get("allow_blank_designator")))
        if ((p.get("plated") is not None and plated is None)
                or (p.get("allow_blank_designator") is not None
                    and blank_flag is None)):
            skipped_invalid += 1
            continue
        blank_ok = allow_blank or blank_flag is True or plated is False
        if not desig and not blank_ok:
            skipped_invalid += 1
            continue
        # Every number by the one guard: _mil on NaN raised a traceback,
        # and a bool or an infinity is never a pad dimension.
        if any(p.get(k) is not None and _num(p.get(k)) is None
               for k in _PAD_NUMBER_KEYS):
            skipped_invalid += 1
            continue
        # A whole percentage, as the single-pad tool and the pad edits
        # insist: round("50") raised, 50.5 was rounded and 150 went out.
        corner = whole_number(p.get("corner_radius", 25))
        if corner is None or not 0 <= corner <= 100:
            skipped_invalid += 1
            continue
        fields = [
            f"designator={desig}",
            f"x={_mil(p.get('x', 0))}",
            f"y={_mil(p.get('y', 0))}",
            f"x_size={_mil(p.get('x_size', 60))}",
            f"y_size={_mil(p.get('y_size', 60))}",
            f"hole_size={_mil(p.get('hole_size', 0))}",
            f"shape={_payload_safe(p.get('shape', 'rectangular'))}",
            f"corner_radius={corner}",
            # Formatted, never sent raw: "1_000" passes float() and reads
            # as 0 in StrToFloatDef.
            f"rotation={_mil(p.get('rotation', 0))}",
            f"layer={_payload_safe(p.get('layer', 'TopLayer'))}",
        ]
        # optional pad features (patch 2): emitted only when given
        if plated is not None:
            fields.append(f"plated={'true' if plated else 'false'}")
        if p.get('hole_type'):
            fields.append(f"hole_type={_payload_safe(p['hole_type'])}")
        for key in ('slot_length', 'mask_expansion', 'paste_expansion'):
            if p.get(key) is not None:
                fields.append(f"{key}={_mil(p[key])}")
        if p.get('hole_rotation') is not None:
            fields.append(f"hole_rotation={float(p['hole_rotation'])}")
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped_invalid


#: Per-edit fields lib_modify_footprint_pads accepts, by how they travel.
#: Mils and degrees go through _mil, so sub-mil values keep Altium's
#: 1/10000-mil resolution exactly as lib_add_footprint_pads sends them.
_PAD_EDIT_MILS = ("x", "y", "x_size", "y_size", "hole_size", "slot_length")
_PAD_EDIT_DEGREES = ("rotation", "hole_rotation")
_PAD_EDIT_EXPANSIONS = ("mask_expansion", "paste_expansion")
_PAD_EDIT_KEYS = frozenset(
    ("designator", "index", "shape", "corner_radius", "plated", "hole_type",
     "layer", "new_designator", "allow_blank_designator")
    + _PAD_EDIT_MILS + _PAD_EDIT_DEGREES + _PAD_EDIT_EXPANSIONS)

#: Shape words folded onto the four pad-stack shapes. Mirrors
#: CanonicalPadShape in Library.pas, which refuses anything else: a
#: modify that fell back to "round" the way the add path does would
#: reshape a pad nobody asked to reshape. "roundrectangle" is what
#: lib_get_pad_geometry reports, so a read can be fed straight back.
_PAD_SHAPE_WORDS = {
    "round": "round", "rounded": "round", "circle": "round",
    "oval": "round",
    "rectangular": "rectangular", "rect": "rectangular",
    "rectangle": "rectangular",
    "octagonal": "octagonal", "oct": "octagonal", "octagon": "octagonal",
    "roundrect": "roundrect", "rounded_rectangular": "roundrect",
    "roundrectangle": "roundrect", "rounded_rectangle": "roundrect",
}
_PAD_HOLE_TYPES = ("round", "square", "slot")


def _pad_index(value: object) -> "int | None":
    """A 0-based pad index, or None when the value is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _pad_edits_payload(edits: object,
                       allow_blank: bool = False) -> tuple[str, list[str]]:
    """Build the ``edits`` batch for lib_modify_footprint_pads.

    Returns ``(payload, problems)``. ANY problem refuses the whole call
    before anything is sent. The alternative, dropping the bad field and
    sending the rest, is the failure this codebase keeps paying for: the
    handler reports success for a pad that was only half changed.

    Same grammar and sanitiser as :func:`_pads_payload`, so the FPC
    cross-validation of ``GetBatchField`` covers this string too.
    """
    if not isinstance(edits, (list, tuple)) or not edits:
        return "", ["edits must be a non-empty list of dicts"]
    problems: list[str] = []
    ops: list[str] = []
    seen: set[tuple[str, int]] = set()
    for n, e in enumerate(edits):
        where = f"edit {n}"
        if not isinstance(e, dict):
            problems.append(f"{where}: must be a dict, got {type(e).__name__}")
            continue
        unknown = sorted(str(k) for k in set(e) - _PAD_EDIT_KEYS)
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown}; accepted: "
                            f"{sorted(_PAD_EDIT_KEYS)}")
            continue
        if e.get("designator") is None:
            problems.append(f"{where}: designator is required (\"\" selects "
                            f"the unnamed pads)")
            continue
        # A designator SELECTS the pad, so a separator is refused, never
        # substituted: 'A;B' sanitised to 'A,B' edits a pad nobody named.
        desig = str(e["designator"]).strip()
        why = name_wire_problem(desig)
        if why:
            problems.append(f"{where}: designator {desig!r} {why}; refused")
            continue
        fields = [f"designator={desig}"]
        idx = None
        if e.get("index") is not None:
            idx = _pad_index(e["index"])
            if idx is None:
                problems.append(f"{where}: index must be a whole number, 0 or "
                                f"more")
                continue
            fields.append(f"index={idx}")
        target = (desig, -1 if idx is None else idx)
        if target in seen:
            problems.append(f"{where}: pad {desig!r} (index {idx}) is already "
                            f"edited earlier in this call; merge the edits")
            continue
        seen.add(target)

        before = len(problems)
        for key in _PAD_EDIT_MILS + _PAD_EDIT_DEGREES:
            if e.get(key) is None:
                continue
            v = _num(e[key])
            if v is None:
                problems.append(f"{where}: {key} must be a number")
            elif key in ("x_size", "y_size", "slot_length") and v <= 0:
                problems.append(f"{where}: {key} must be greater than zero")
            elif key == "hole_size" and v < 0:
                problems.append(f"{where}: hole_size cannot be negative (0 "
                                f"makes the pad SMD)")
            elif key in _PAD_EDIT_DEGREES and not -360.0 <= v <= 360.0:
                problems.append(f"{where}: {key} must be within -360..360")
            else:
                fields.append(f"{key}={_mil(v)}")
        for key in _PAD_EDIT_EXPANSIONS:
            if e.get(key) is None:
                continue
            if isinstance(e[key], str) and e[key].strip().lower() == "rule":
                fields.append(f"{key}=rule")
                continue
            v = _num(e[key])
            if v is None:
                problems.append(f"{where}: {key} must be a number in mils or "
                                f"\"rule\"")
            else:
                fields.append(f"{key}={_mil(v)}")
        if e.get("shape") is not None:
            canon = _PAD_SHAPE_WORDS.get(str(e["shape"]).strip().lower())
            if canon is None:
                problems.append(f"{where}: unknown shape {e['shape']!r}; use "
                                f"round, rectangular, octagonal or roundrect")
            else:
                fields.append(f"shape={canon}")
        if e.get("corner_radius") is not None:
            cr = _num(e["corner_radius"])
            if cr is None or not cr.is_integer() or not 0 <= cr <= 100:
                problems.append(f"{where}: corner_radius is a whole "
                                f"percentage, 0 to 100")
            else:
                fields.append(f"corner_radius={int(cr)}")
        if e.get("plated") is not None:
            plated = wire_bool(e["plated"])
            if plated is None:
                problems.append(f"{where}: plated must be true or false")
            else:
                fields.append(f"plated={'true' if plated else 'false'}")
        if e.get("hole_type") is not None:
            ht = str(e["hole_type"]).strip().lower()
            if ht not in _PAD_HOLE_TYPES:
                problems.append(f"{where}: hole_type must be round, square or "
                                f"slot")
            else:
                if (ht == "slot" and e.get("slot_length") is None):
                    problems.append(f"{where}: hole_type=slot needs "
                                    f"slot_length (mils)")
                fields.append(f"hole_type={ht}")
        if e.get("layer") is not None and str(e["layer"]).strip():
            fields.append(f"layer={_payload_safe(str(e['layer']).strip())}")
        blank_ok = allow_blank
        if e.get("allow_blank_designator") is not None:
            flag = wire_bool(e["allow_blank_designator"])
            if flag is None:
                problems.append(f"{where}: allow_blank_designator must be "
                                f"true or false")
            blank_ok = blank_ok or bool(flag)
        if "new_designator" in e and e["new_designator"] is not None:
            new = str(e["new_designator"]).strip()
            why = name_wire_problem(new)
            if why:
                problems.append(f"{where}: new_designator {new!r} {why}; "
                                f"refused")
            elif new:
                fields.append(f"new_designator={new}")
            elif blank_ok:
                fields.append("clear_designator=true")
            else:
                problems.append(f"{where}: new_designator is blank; pass "
                                f"allow_blank_designator=true to leave the "
                                f"pad unnamed (fiducial, mechanical pad)")
        if len(problems) > before:
            continue
        if len(fields) == (2 if idx is not None else 1):
            problems.append(f"{where}: changes nothing; give at least one "
                            f"field to set")
            continue
        ops.append(";".join(fields))
    # join_ops, not "~~": a layer ending in '~' made the NEXT edit's key
    # '~designator', which reads as '' and selects the unnamed pads.
    return join_ops(ops), problems


def _pad_selectors_payload(pads: object) -> tuple[str, list[str]]:
    """Build the ``pads`` batch for lib_delete_footprint_pads.

    Returns ``(payload, problems)``; any problem refuses the call, for
    the same reason the Pascal refuses a selector it cannot resolve.
    """
    if not isinstance(pads, (list, tuple)) or not pads:
        return "", ["pads must be a non-empty list of dicts"]
    problems: list[str] = []
    ops: list[str] = []
    for n, p in enumerate(pads):
        where = f"pad {n}"
        if not isinstance(p, dict):
            problems.append(f"{where}: must be a dict, got {type(p).__name__}")
            continue
        unknown = sorted(str(k) for k in set(p) - {"designator", "index",
                                                     "all"})
        if unknown:
            problems.append(f"{where}: unknown key(s) {unknown}; accepted: "
                            f"designator, index, all")
            continue
        if p.get("designator") is None:
            problems.append(f"{where}: designator is required (\"\" selects "
                            f"the unnamed pads)")
            continue
        desig = str(p["designator"]).strip()
        why = name_wire_problem(desig)
        if why:
            problems.append(f"{where}: designator {desig!r} {why}; refused")
            continue
        fields = [f"designator={desig}"]
        take_all = False
        if p.get("all") is not None:
            flag = wire_bool(p["all"])
            if flag is None:
                problems.append(f"{where}: all must be true or false")
                continue
            take_all = flag
        if p.get("index") is not None:
            idx = _pad_index(p["index"])
            if idx is None:
                problems.append(f"{where}: index must be a whole number, 0 or "
                                f"more")
                continue
            if take_all:
                problems.append(f"{where}: index and all are exclusive")
                continue
            fields.append(f"index={idx}")
        if take_all:
            fields.append("all=true")
        ops.append(";".join(fields))
    return "~~".join(ops), problems


def _write_description_edits(descriptions: dict) -> tuple[str, str]:
    """Write the name<TAB>description file the bulk handler reads.

    Returns ``(path, error)``; exactly one is non-empty. Same encoding
    and newline contract as lib_batch_rename's file, for the same
    reasons: the Pascal reads it in the system ANSI codepage, and a CR
    left on a line would end up inside a description.
    """
    encoding = _batch_encoding()
    lines: list[str] = []
    for name, text in descriptions.items():
        name_s, text_s = str(name), "" if text is None else str(text)
        if not name_s.strip():
            return "", "a footprint name is blank"
        for label, value in (("name", name_s), ("description", text_s)):
            if any(c in value for c in "\t\r\n"):
                return "", (f"{name_s!r}: the {label} contains a tab or line "
                            f"break, which would corrupt the batch file")
            bad = _first_unencodable(value, encoding)
            if bad:
                return "", (f"{name_s!r}: the {label} contains {bad!r}, which "
                            f"the Altium-side reader's codepage ({encoding}) "
                            f"cannot represent")
        lines.append(f"{name_s}\t{text_s}\n")
    config = get_config()
    config.ensure_workspace()
    batch_path = config.workspace_dir / "footprint_descriptions.tsv"
    with open(batch_path, "w", encoding=encoding, newline="") as f:
        f.write("".join(lines))
    return str(batch_path), ""


#: A finite float, or None for anything that is not one (bools included).
#: Every footprint geometry field goes through here before ``_mil``; see
#: bridge/payload.py finite_number for why each refusal matters.
_num = finite_number


def _tracks_payload(tracks: list[dict[str, Any]]) -> tuple[str, int]:
    """Build the ``tracks`` batch payload, and count what was dropped.

    Sibling of :func:`_pads_payload`. Coordinates and width go through
    ``_mil``, so a 12.5 mil courtyard line arrives as 12.5 rather than the
    whole mil ``round()`` used to make of it. The Pascal reads them with
    StrToFloatDef now, which is what makes the decimal meaningful: under
    StrToIntDef a decimal did not round, it fell back to the DEFAULT.

    A track missing an endpoint, carrying a non-number, or with a width
    that is not positive is dropped and counted, never sent as zeros.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for t in tracks:
        if not isinstance(t, dict):
            skipped_invalid += 1
            continue
        coords = [_num(t.get(k)) for k in ("x1", "y1", "x2", "y2")]
        width = _num(t.get("width", 10))
        if any(c is None for c in coords) or width is None or width <= 0:
            skipped_invalid += 1
            continue
        fields = [
            f"x1={_mil(coords[0])}", f"y1={_mil(coords[1])}",
            f"x2={_mil(coords[2])}", f"y2={_mil(coords[3])}",
            f"width={_mil(width)}",
            f"layer={_payload_safe(t.get('layer') or 'TopOverlay')}",
        ]
        op_strs.append(";".join(fields))
    # join_ops: a layer ending in '~' cost the next track its x1.
    return join_ops(op_strs), skipped_invalid


def _fills_payload(
        fills: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Build the ``fills`` batch payload, and say what was dropped and why.

    Returns ``(payload, skipped)`` where each skipped entry names the
    fill's index and the reason, the shape the Pascal's own ``failures``
    use, so a caller reads both lists the same way.

    LAYER IS REQUIRED. A track defaults to silkscreen because a stray silk
    line is harmless; a fill defaulted to anything would be copper or paste
    that nobody asked for.
    """
    op_strs: list[str] = []
    skipped: list[dict[str, Any]] = []
    for i, f in enumerate(fills):
        if not isinstance(f, dict):
            skipped.append({"item": i, "reason": "not an object"})
            continue
        coords = [_num(f.get(k)) for k in ("x1", "y1", "x2", "y2")]
        if any(c is None for c in coords):
            skipped.append({"item": i, "reason": "x1, y1, x2 and y2 must "
                                                  "all be numbers (mils)"})
            continue
        if coords[0] == coords[2] or coords[1] == coords[3]:
            skipped.append({"item": i, "reason": "zero area"})
            continue
        layer = str(f.get("layer") or "").strip()
        if not layer:
            skipped.append({"item": i, "reason": "layer is required"})
            continue
        rotation = _num(f.get("rotation", 0))
        if rotation is None:
            skipped.append({"item": i, "reason": "rotation is not a number"})
            continue
        # By the one boolean rule: truthiness read the string "false" as
        # true and turned the fill into a keep-out.
        keepout = (False if f.get("keepout") is None
                   else wire_bool(f.get("keepout")))
        if keepout is None:
            skipped.append({"item": i, "reason": "keepout must be true or "
                                                  "false"})
            continue
        fields = [
            f"x1={_mil(coords[0])}", f"y1={_mil(coords[1])}",
            f"x2={_mil(coords[2])}", f"y2={_mil(coords[3])}",
            f"rotation={_mil(rotation)}",
            f"layer={_payload_safe(layer)}",
        ]
        if keepout:
            fields.append("keepout=true")
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped


#: Region kinds the Pascal accepts. Anything else is refused on this side,
#: because the alternative is a copper region the caller did not ask for.
_REGION_KINDS = ("copper", "cutout", "keepout")


def _vertex(point: object) -> Optional[tuple[float, float]]:
    """One vertex as ``(x, y)``, from ``[x, y]`` or ``{"x": .., "y": ..}``."""
    if isinstance(point, dict):
        x, y = _num(point.get("x")), _num(point.get("y"))
    elif isinstance(point, (list, tuple)) and len(point) == 2:
        x, y = _num(point[0]), _num(point[1])
    else:
        return None
    if x is None or y is None:
        return None
    return x, y


def _regions_payload(
        regions: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Build the ``regions`` batch payload, and say what was dropped and why.

    Vertices travel in ONE field, ``pts=x1,y1,x2,y2,...``. Commas are data
    to the batch grammar (only ``;`` and ``~~`` separate), and every value
    here is a number this function formatted, so nothing a caller types can
    reach the field.

    A closing vertex that repeats the first is dropped: Altium closes a
    region's contour itself, and the repeat would be a zero-length edge.
    """
    op_strs: list[str] = []
    skipped: list[dict[str, Any]] = []
    for i, r in enumerate(regions):
        if not isinstance(r, dict):
            skipped.append({"item": i, "reason": "not an object"})
            continue
        kind = str(r.get("kind") or "copper").strip().lower()
        if kind not in _REGION_KINDS:
            skipped.append({"item": i, "reason": (
                f"unknown kind {kind!r}; use one of {', '.join(_REGION_KINDS)}")})
            continue
        raw = r.get("vertices")
        if not isinstance(raw, (list, tuple)):
            skipped.append({"item": i, "reason": "vertices must be a list of "
                                                  "[x, y] points (mils)"})
            continue
        pts = [_vertex(p) for p in raw]
        if any(p is None for p in pts):
            skipped.append({"item": i, "reason": "every vertex must be "
                                                  "[x, y] with numbers"})
            continue
        if len(pts) > 1 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) < 3:
            skipped.append({"item": i, "reason": "at least 3 distinct "
                                                  "vertices are required"})
            continue
        layer = str(r.get("layer") or "").strip()
        if not layer and kind != "keepout":
            skipped.append({"item": i, "reason": "layer is required (only "
                                                  "kind=keepout defaults, to "
                                                  "KeepOutLayer)"})
            continue
        flat = ",".join(f"{_mil(x)},{_mil(y)}" for x, y in pts)
        fields = [f"pts={flat}", f"kind={kind}"]
        if layer:
            fields.append(f"layer={_payload_safe(layer)}")
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped


#: Values the text tool's ``registration`` diagnostic accepts.
_TEXT_REGISTRATIONS = ("both", "footprint", "board")


def _pins_payload(pins: list[dict[str, Any]]) -> tuple[str, int]:
    """Build the ``pins`` batch payload, and count what was dropped.

    Extracted so the exact string Pascal will parse can be produced
    without a bridge. The grammar is unforgiving -- operations split on
    "~~", fields on ";", key and value on the FIRST "=" -- and the
    cross-validation suite now runs this output through the real
    ``GetBatchField`` compiled by FPC, which is the only way to know the
    sanitiser and the parser agree rather than merely look like they do.

    Returns ``(payload, skipped_invalid)``. lib_add_footprint_pads drops
    blank designators the same way, so the count is reported, never
    silently absorbed. A pin carrying a number or flag that is not one is
    dropped and counted too: x, y, length and rotation must be finite
    numbers (x, y and length within the coordinate space, MAX_COORD_MILS),
    and owner_part_id a whole number, 0 or more.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for p in pins:
        desig = _payload_safe(str(p.get("designator", "")).strip())
        name = _payload_safe(str(p.get("name", "")).strip())
        if not desig:
            skipped_invalid += 1
            continue
        # Flags by the one boolean rule (wire_bool): truthiness read the
        # string "false" as true and hid the pin. A flag that is not a
        # boolean drops the pin, counted, rather than guessing.
        flags = {k: wire_bool(p[k]) for k in
                 ("hidden", "show_name", "show_designator")
                 if p.get(k) is not None}
        if any(v is None for v in flags.values()):
            skipped_invalid += 1
            continue
        # Numbers by the house guards, never bare round()/int(): text, NaN
        # or an infinity raised a traceback here, and owner_part_id True
        # or 1.7 quietly became sub-part 1. Positions are still rounded and
        # snapped to the pin grid, which is the documented forgiveness.
        nums = {k: _num(p.get(k, default))
                for k, default in (("x", 0), ("y", 0), ("length", 200),
                                   ("rotation", 0))}
        if (any(v is None for v in nums.values())
                or any(abs(nums[k]) > MAX_COORD_MILS
                       for k in ("x", "y", "length"))):
            skipped_invalid += 1
            continue
        owner = None
        if p.get("owner_part_id") is not None:
            owner = whole_number(p["owner_part_id"])
            if owner is None or owner < 0:
                skipped_invalid += 1
                continue
        fields = [
            f"designator={desig}",
            f"name={name}",
            f"x={_snap(round(nums['x']))}",
            f"y={_snap(round(nums['y']))}",
            f"length={_snap(round(nums['length']))}",
            f"rotation={round(nums['rotation'])}",
            f"electrical_type="
            f"{_payload_safe(p.get('electrical_type', 'passive'))}",
            f"hidden={'true' if flags.get('hidden') else 'false'}",
        ]
        if owner is not None:
            fields.append(f"owner_part_id={owner}")
        # IEEE edge decorations. Omitted entirely when unset, because a
        # fresh pin already carries eNoSymbol and the Pascal only writes
        # the property when the field is present, which keeps every
        # existing caller's payload byte-identical.
        for key in ("symbol_outer_edge", "symbol_inner_edge"):
            value = p.get(key)
            if value not in (None, ""):
                fields.append(f"{key}={_payload_safe(value)}")
        # Label visibility is tri-state on the wire: absent leaves
        # Altium's default alone, so False has to survive as the string
        # "false" rather than being folded into "not set".
        for key in ("show_name", "show_designator"):
            if key in flags:
                fields.append(f"{key}={'true' if flags[key] else 'false'}")
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped_invalid


#: Every key lib_set_pin_properties reads from one pin edit. A key outside
#: this set is refused by name with the nearest valid one, the rule
#: sch_place_components applies to placement dicts: a dropped key is an
#: edit that silently did not happen.
PIN_EDIT_KEYS = frozenset({
    "designator", "owner_part_id", "name", "new_designator",
    "electrical_type", "show_name", "show_designator", "hidden", "length",
})

#: The keys that CHANGE a pin. An edit naming none of them is a no-op and
#: is refused rather than sent.
_PIN_EDIT_CHANGES = PIN_EDIT_KEYS - {"designator", "owner_part_id"}


def _pin_edits_payload(pins: Any) -> tuple[str, list[str]]:
    """Build the ``pins`` payload for lib_set_pin_properties.

    Returns ``(payload, problems)``; any problem refuses the whole call,
    and nothing is sent. Same grammar as ``_pins_payload`` (ops on "~~",
    fields on ";", key from value on the FIRST "="), with two differences
    that both come from this being an EDIT:

    * only the fields given are emitted, because an absent field must
      leave the property alone. ``name=""`` is emitted as ``name=`` and
      clears the name; the Pascal tells it apart from no name field.
    * a separator inside ``designator``, ``new_designator`` or ``name`` is
      refused, not substituted, and so is a trailing '~'
      (``name_wire_problem``). The designator SELECTS the pin, and a
      fragment such as ``1`` matches a real pin on most symbols, so the
      edit would land on the wrong one. The value checked is the value
      SENT: both designators are stripped, so ``"2~ "`` is checked as
      ``"2~"``.

    ``length`` and ``owner_part_id`` must be whole numbers (the Pascal
    reads them as integers and refuses anything else, so rounding here
    would only hide the refusal; length also within MAX_COORD_MILS), and
    the three flags parse by ``wire_bool``: the string "false" is false.

    Electrical words are NOT checked here: the Pascal asks the one shared
    chain (StrToPinElectrical) and refuses an unknown word per op, so the
    vocabulary is stated once.
    """
    problems: list[str] = []
    ops: list[str] = []
    if not isinstance(pins, list) or not pins:
        return "", ["pins must be a non-empty list of pin edits"]
    for i, p in enumerate(pins):
        if not isinstance(p, dict):
            problems.append(f"pins[{i}] is not an object")
            continue
        for key in sorted(set(p) - PIN_EDIT_KEYS):
            near = difflib.get_close_matches(key, sorted(PIN_EDIT_KEYS), n=1)
            hint = f" (did you mean {near[0]!r}?)" if near else ""
            problems.append(f"pins[{i}]: unknown key {key!r}{hint}")
        desig = str(p.get("designator", "")).strip()
        if not desig:
            problems.append(
                f"pins[{i}]: designator is required (the pin's CURRENT number)")
            continue
        if not any(p.get(k) is not None for k in _PIN_EDIT_CHANGES):
            problems.append(f"pins[{i}] ({desig}): names nothing to change")
            continue
        for key in ("designator", "new_designator", "name"):
            value = p.get(key)
            # Checked exactly as it will be sent. The designators go out
            # stripped, so "2~ " checked raw passed and went out as "2~",
            # which ran into the separator and took the next edit's
            # designator with it.
            if value is not None and key != "name":
                value = str(value).strip()
            why = "" if value is None else name_wire_problem(str(value))
            if why:
                problems.append(
                    f"pins[{i}]: {key} {value!r} {why}, so the call is "
                    f"refused")
        # Whole numbers by the house guard, never int()/round(): length=inf
        # raised OverflowError past this check, True became part 1, and
        # 12.7 was rounded to 13 where the Pascal (IsIntStr) would have
        # refused it.
        owner = length = None
        if p.get("owner_part_id") is not None:
            owner = whole_number(p["owner_part_id"])
            if owner is None or owner < 0:
                problems.append(f"pins[{i}] ({desig}): owner_part_id must be "
                                f"a whole number, 0 or more")
                continue
        if p.get("length") is not None:
            length = whole_number(p["length"], MAX_COORD_MILS)
            if length is None or length <= 0:
                problems.append(f"pins[{i}] ({desig}): length must be a "
                                f"whole number of mils, greater than zero "
                                f"and at most {MAX_COORD_MILS}")
                continue
        flags: dict[str, bool] = {}
        for key in ("show_name", "show_designator", "hidden"):
            if p.get(key) is None:
                continue
            flag = wire_bool(p[key])
            if flag is None:
                problems.append(f"pins[{i}] ({desig}): {key} must be true or "
                                f"false")
            else:
                flags[key] = flag
        fields = [f"designator={desig}"]
        if owner is not None:
            fields.append(f"owner_part_id={owner}")
        if p.get("name") is not None:
            fields.append(f"name={p['name']}")
        if p.get("new_designator") is not None:
            new_desig = str(p["new_designator"]).strip()
            if not new_desig:
                problems.append(f"pins[{i}] ({desig}): new_designator is empty")
            fields.append(f"new_designator={new_desig}")
        if p.get("electrical_type") is not None:
            fields.append(
                f"electrical_type={_payload_safe(p['electrical_type'])}")
        for key, flag in flags.items():
            fields.append(f"{key}={'true' if flag else 'false'}")
        if length is not None:
            fields.append(f"length={length}")
        ops.append(";".join(fields))
    # join_ops: an electrical_type ending in '~' took the next edit's
    # designator with it.
    return join_ops(ops), problems


#: Altium's Component Type, in the words its Properties panel uses, to the
#: canonical word the Pascal accepts. Folded first: lower case, every run
#: of non-alphanumerics to one underscore, so "Net Tie (In BOM)" arrives as
#: net_tie_in_bom. "Net Tie" with no qualifier is Altium's no-BOM kind,
#: the same default sch_set_net_tie uses.
_COMPONENT_KINDS = {
    "standard": "standard",
    "mechanical": "mechanical",
    "graphical": "graphical",
    "net_tie_bom": "net_tie_bom",
    "net_tie_in_bom": "net_tie_bom",
    "net_tie": "net_tie_no_bom",
    "net_tie_no_bom": "net_tie_no_bom",
    "standard_no_bom": "standard_no_bom",
    "jumper": "jumper",
}


def canonical_component_kind(kind: object) -> Optional[str]:
    """The canonical Component Type word for ``kind``, or None."""
    key = re.sub(r"[^a-z0-9]+", "_", str(kind).strip().lower()).strip("_")
    return _COMPONENT_KINDS.get(key)


def _safe_filename(name: str, fallback: str = "part") -> str:
    """Thin alias for the shared importer helper.

    Kept as a name so existing call sites and their tests do not move;
    the implementation lives in libimport._names so the providers and the
    EasyEDA importer cannot drift apart on which characters are illegal.
    """
    from ..libimport._names import safe_filename

    return safe_filename(name, fallback)


def _spill_pin_list(result, pins, comp, output_path):
    """Write the full pin array to JSON and summarise what is left.

    A pin list that overflows the conversation is not merely awkward: the
    client decides what survives, so the answer becomes environment
    dependent. Writing the whole thing and returning a summary makes the
    outcome the same everywhere, and the file is the more useful artefact
    for the job people actually do with it, which is a field-by-field
    comparison against the datasheet pin table.

    The summary carries the two distributions worth eyeballing: pins per
    part, and electrical type. Both are the checks that catch a symbol
    built from a mis-transcribed table.
    """
    import json
    from collections import Counter
    from pathlib import Path

    per_part = Counter()
    per_type = Counter()
    for pin in pins:
        if not isinstance(pin, dict):
            continue
        per_part[str(pin.get("owner_part_id", ""))] += 1
        per_type[str(pin.get("electrical_type", ""))] += 1

    if output_path:
        target = Path(output_path)
    else:
        safe = "".join(c if c.isalnum() or c in "-_." else "_"
                       for c in (comp or "symbol"))
        target = Path(get_bridge().config.workspace_dir) / f"pins_{safe}.json"

    written = ""
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(pins, indent=1), encoding="utf-8")
        written = str(target)
    except OSError as exc:
        # Never lose the data because the file could not be written.
        # Returning it inline is worse than a file and better than
        # nothing, and the reason has to travel with it.
        out = dict(result)
        out["spill_error"] = f"could not write {target}: {exc}"
        return out

    out = {k: v for k, v in result.items() if k != "pins"}
    out["pins_path"] = written
    out["summary"] = {
        "pins": len(pins),
        "per_part": dict(sorted(per_part.items())),
        "per_electrical_type": dict(sorted(per_type.items())),
    }
    out["note"] = (
        f"{len(pins)} pins written to {written}. Read or diff that file "
        f"directly; it is not summarised further here."
    )
    return out


def register_library_tools(mcp):
    """Register library tools with the MCP server."""

    # =========================================================================
    # Symbol Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_symbol(
        name: str,
        designator_prefix: str = "U",
        description: str = "",
        part_count: int = 1,
    ) -> dict[str, Any]:
        """Create a new schematic symbol in the active library.

        Args:
            name: Component name
            designator_prefix: Default designator prefix (e.g., "U", "R", "C")
            description: Component description
            part_count: For multi-part components (quad op-amp, dual gate,
                etc), the number of sub-parts. Pins added via lib_add_pins
                with ``owner_part_id`` set to 1..part_count are assigned
                to that sub-part; ``owner_part_id=0`` shares the pin
                across all sub-parts (the usual power-pin pattern on a
                quad op-amp).

        Returns:
            Dictionary with created symbol information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_symbol",
            {
                "name": name,
                "designator_prefix": designator_prefix,
                "description": description,
                "part_count": str(max(1, int(part_count))),
            },
        )
        return result

    @mcp.tool()
    async def lib_create_ic_symbol(
        name: str,
        left_pins: list[dict[str, Any]],
        right_pins: list[dict[str, Any]],
        designator_prefix: str = "U",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a COMPLETE, discipline-compliant IC symbol in ONE call.

        You decide the functional grouping (inputs/power/control on the
        LEFT, outputs/status on the RIGHT -- discipline rule 18); this
        lays them out cleanly and emits the whole symbol: the body
        rectangle (Altium-yellow fill) plus every pin, grid-aligned with
        the top-left pin's wire-end at the origin (rule 14), in one
        create + one bulk add_pins + one rectangle.

        Args:
            name: Component name.
            left_pins: Pins on the LEFT side, top-to-bottom. Each a dict
                with `designator`, `name`, and optional `electrical_type`
                (input/output/power/passive/...).
            right_pins: Pins on the RIGHT side, top-to-bottom, same shape.
            designator_prefix: e.g. "U".
            description: Component description.

        Returns:
            Dict with the symbol name, pin count, body extent, and the
            per-step bridge results.
        """
        from eda_agent.design.symbol_gen import generate_ic_symbol

        # The generator is a pure function and raises on bad geometry;
        # at the tool boundary that becomes a refusal, not a stack trace.
        try:
            geom = generate_ic_symbol(left_pins, right_pins)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_symbol",
            {
                "name": name,
                "designator_prefix": designator_prefix,
                "description": description,
                "part_count": "1",
            },
        )

        # Built by the shared payload helper rather than inline. The
        # inline copy interpolated designator, name and electrical_type
        # straight into the string, so any of them carrying ";" or "~~"
        # reshaped the payload -- a pin name lifted from a datasheet
        # table is enough to do it by accident.
        # BODY FIRST, THEN PINS, AND THE ORDER IS NOT COSMETIC.
        # Altium exposes no z-order on schematic primitives: drawing order
        # IS insertion order, and there is no send-to-back to undo it. This
        # rectangle is solid (fill_color sets IsSolid), so adding it after
        # the pins paints it straight over the pin names, and the only
        # remedy is rebuilding the whole symbol. Reported from the field
        # 2026-09-21 after exactly that rebuild.
        body = geom.body
        rect_res = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            {
                "x1": body["x1"], "y1": body["y1"],
                "x2": body["x2"], "y2": body["y2"],
                "fill_color": 8454143,
                "border_color": 0,
            },
        )

        pins_payload, _ = _pins_payload(geom.pins)
        pins_res = await bridge.send_command_async(
            "library.add_pins", {"pins": pins_payload})

        return {
            "symbol": name,
            "pins": len(geom.pins),
            "left": len(left_pins),
            "right": len(right_pins),
            "width_mils": geom.width_mils,
            "height_mils": geom.height_mils,
            "create": created,
            "pins_result": pins_res,
            "rectangle_result": rect_res,
        }

    @mcp.tool()
    async def lib_create_passive_symbol(
        name: str,
        kind: str,
        designator_prefix: str = "",
        description: str = "",
    ) -> dict[str, Any]:
        """Create a standard 2-pin PASSIVE symbol in ONE call.

        Draws the recognizable glyph for a resistor / capacitor /
        polarized_capacitor / inductor / diode / led / crystal / fuse,
        with pin 1 on the left and pin 2 on the right. Composes
        `lib_create_symbol` + `lib_add_pins` + the
        glyph primitives (emitted as raw mils, so the LED's emission
        arrows and the crystal's resonator keep their sub-100-mil detail).

        Args:
            name: Component name (e.g. "R_0402").
            kind: "resistor" | "capacitor" | "polarized_capacitor" |
                "inductor" | "diode" | "led" | "crystal" | "fuse"
                (aliases r/c/cap_pol/electrolytic/cp/l/d/xtal/f accepted).
            designator_prefix: default by kind (R/C/L/D/Y/F) if omitted.
            description: Component description.

        Returns:
            Dict with the symbol name and per-step bridge results.
        """
        from eda_agent.design.symbol_gen import generate_passive_symbol

        try:
            geom = generate_passive_symbol(kind)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}
        prefix = designator_prefix or {
            "resistor": "R", "r": "R", "res": "R",
            "capacitor": "C", "c": "C", "cap": "C",
            "polarized_capacitor": "C", "cap_pol": "C",
            "electrolytic": "C", "cp": "C",
            "inductor": "L", "l": "L", "ind": "L",
            "diode": "D", "d": "D",
            "led": "D", "diode_led": "D",
            "crystal": "Y", "xtal": "Y", "y": "Y",
            "fuse": "F", "f": "F",
        }.get(kind.strip().lower(), "U")

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_symbol",
            {"name": name, "designator_prefix": prefix,
             "description": description, "part_count": "1"},
        )

        # Third copy of this format, now the last: all three built the
        # payload inline and interpolated designator, name and
        # electrical_type raw, so a value carrying ";" or "~~" reshaped
        # it. One builder means one place to get the grammar right.
        pins_payload, _ = _pins_payload(geom.pins)
        pins_res = await bridge.send_command_async(
            "library.add_pins", {"pins": pins_payload})

        steps: dict[str, Any] = {}
        for i, r in enumerate(geom.rectangles):
            steps[f"rect{i}"] = await bridge.send_command_async(
                "library.add_symbol_rectangle",
                {"x1": r["x1"], "y1": r["y1"], "x2": r["x2"], "y2": r["y2"],
                 "fill_color": -1, "border_color": 0})
        if geom.lines:
            line_ops = [
                ";".join([f"x1={l['x1']}", f"y1={l['y1']}",
                          f"x2={l['x2']}", f"y2={l['y2']}", "width=10"])
                for l in geom.lines
            ]
            steps["lines"] = await bridge.send_command_async(
                "library.add_symbol_lines", {"lines": "~~".join(line_ops)})
        for i, poly in enumerate(geom.polygons):
            verts = ",".join(
                str(v) for pt in poly["points"] for v in pt)
            steps[f"poly{i}"] = await bridge.send_command_async(
                "library.add_symbol_polygon", {"vertices": verts})

        return {
            "symbol": name, "kind": kind, "designator_prefix": prefix,
            "pins": len(geom.pins), "create": created,
            "pins_result": pins_res, "glyph": steps,
        }

    @mcp.tool()
    async def lib_set_current_component(
        component_name: str,
    ) -> dict[str, Any]:
        """Make a named component the editor's current selection in the
        active SchLib.

        Required before bulk-editing a specific component's pins,
        rectangle, or parameters via ``obj_modify`` / ``obj_batch_modify``
        on a SchLib. The asymmetry it fixes: ``lib_get_component_details``
        is a read-only fetch and does NOT update the editor's current
        component, so subsequent ``obj_modify`` on the SchLib's
        ePin / eRectangle / eParameter iterators silently hits whatever
        component was last UI-selected -- usually NOT the one you just
        read.

        Use this between switching components:
            lib_set_current_component("MyIC")
            modify_objects("ePin", scope="active_doc",
                           filter="Location.X=200", set="Orientation=2")
            lib_set_current_component("MyOtherPart")
            modify_objects("ePin", scope="active_doc",
                           filter="Location.X=200", set="Orientation=2")

        Args:
            component_name: Component name (LibRef) in the active SchLib.

        Returns:
            Dict with ``success`` + ``name``, or an error if no SchLib
            is active or the component name isn't found in it.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_current_component",
            {"name": component_name},
        )

    @mcp.tool()
    async def lib_add_pins(
        pins: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MANY pins to the current symbol in ONE call.

        PREFER THIS over adding pins one at a time (there is no singular
        variant). A 48-pin IC symbol built one pin at a time would be 48
        LLM turns; with this tool it's one turn + one
        PreProcess/PostProcess + one save.

        Args:
            pins: List of pin dicts, each with:
                - designator (str, required)
                - name       (str, required)
                - x, y       (int, mils), pin endpoint
                - length     (int, mils, default 200)
                - rotation   (int, default 0), 0/90/180/270
                - electrical_type (str, default "passive"), one of
                  input/output/bidirectional/passive/open_collector/
                  open_emitter/power/hiz/io
                - hidden     (bool, default False)
                - owner_part_id (int, optional). For multi-part
                  components (e.g. quad op-amp). 1..part_count picks a
                  specific sub-part; 0 shares the pin across all parts
                  (typical for V+ / V- power pins on a quad). Omit for
                  single-part symbols.
                - symbol_outer_edge (str, optional). IEEE decoration on
                  the pin's outer edge. "dot" draws the inversion bubble
                  of an active-low pin. Omit for none.
                - symbol_inner_edge (str, optional). Same, inner edge.
                  "clock" draws the clock wedge. The two are
                  independent, so an inverted clock sets both.
                  Either field also accepts any other TIeeeSymbol name
                  ("schmitt", "open_collector", "active_low_input", ...)
                  or a bare ordinal.
                - show_name / show_designator (bool, optional). Whether
                  the pin's name and number are DRAWN. Different from
                  `hidden`, which hides the pin itself: a resistor shows
                  both pins and neither label. Omit to leave Altium's
                  default.

        Example, one stage of a dual op-amp (sub-part 1) with shared
        power pins (sub-part 0):
            lib_add_pins(pins=[
                {"designator": "1", "name": "OUT1",  "x": 0,   "y": 0,
                 "rotation": 180, "electrical_type": "output",
                 "owner_part_id": 1},
                {"designator": "2", "name": "IN1-",  "x": 0,   "y": 100,
                 "rotation": 180, "electrical_type": "input",
                 "owner_part_id": 1},
                {"designator": "3", "name": "IN1+",  "x": 0,   "y": 200,
                 "rotation": 180, "electrical_type": "input",
                 "owner_part_id": 1},
                {"designator": "8", "name": "V+",    "x": 0,   "y": 400,
                 "rotation": 180, "electrical_type": "power",
                 "owner_part_id": 0},
            ])

        Returns:
            Dict with added, failed, total counts.
        """
        payload, skipped_invalid = _pins_payload(pins)

        if not payload:
            return {
                "error": ("No valid pins (every entry was missing a "
                          "designator, or carried a number or flag that is "
                          "not one)"),
                "added": 0,
                "total": len(pins),
                "skipped_invalid": skipped_invalid,
            }

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_pins",
            {"pins": payload},
        )
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_symbol_rectangle(
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        fill_color: int = -1,
        border_color: int = 0,
    ) -> dict[str, Any]:
        """Add a rectangle to the current symbol body.

        ADD THE BODY BEFORE THE PINS. Altium exposes no z-order on
        schematic primitives, so drawing order is insertion order and
        there is no send-to-back to correct it afterwards. A rectangle
        given a `fill_color` is SOLID, and one added after the pins paints
        over the pin names; the only fix is rebuilding the symbol. Build
        order is body, then pins, then anything drawn on top.

        Args:
            x1: First corner X in mils
            y1: First corner Y in mils
            x2: Opposite corner X in mils
            y2: Opposite corner Y in mils
            fill_color: Fill color index (-1 = no fill)
            border_color: Border color index

        Returns:
            Dictionary confirming rectangle addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            {
                "x1": _snap(int(x1)),
                "y1": _snap(int(y1)),
                "x2": _snap(int(x2)),
                "y2": _snap(int(y2)),
                "fill_color": fill_color,
                "border_color": border_color,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_text(
        texts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Add MANY body-text items to the current symbol in ONE call.

        PREFER THIS over adding text one at a time (there is no singular
        variant). Free text on a symbol is an Altium ISch_Label: the
        polarity marks, pin-group headings and "NC" annotations that
        carry meaning the geometry alone does not.

        Args:
            texts: List of text dicts, each with:
                - text     (str, required). Empty strings are refused,
                  not placed: an empty label is invisible and
                  unselectable, and only turns up later as a stray
                  object.
                - x, y     (int, mils)
                - rotation (int, default 0), 0/90/180/270
                - font_size (int, default 10). Altium's own font size,
                  the number its font manager takes. NOT mils, and not
                  converted from mils: the relationship between the two
                  is not documented anywhere this project can verify, so
                  a conversion here would silently resize every item.
                - font_name (str, default "Arial")
                - bold, italic (bool, default False)
                - owner_part_id (int, optional). Same meaning as in
                  ``lib_add_pins``: 1..part_count picks a sub-part, 0
                  shares the text across all of them. Omit for
                  single-part symbols.

        Returns:
            Dict with ``added``, ``failed``, ``total`` counts. A text whose
            number or flag is not one refuses the whole call, naming it
            (``{ok: false, reason, problems}``), and nothing is placed.
        """
        op_strs: list[str] = []
        problems: list[str] = []
        for i, item in enumerate(texts):
            if not isinstance(item, dict):
                problems.append(f"texts[{i}] is not an object")
                continue
            content = _payload_safe(str(item.get("text", "")).strip())
            if not content:
                continue
            # Numbers and flags by the house guards. round()/int() raised a
            # traceback on text, NaN or an infinity, and owner_part_id True
            # became sub-part 1; truthiness drew "false" as bold.
            nums = {k: _num(item.get(k, default))
                    for k, default in (("x", 0), ("y", 0), ("rotation", 0))}
            bad = [k for k, v in nums.items() if v is None
                   or (k != "rotation" and abs(v) > MAX_COORD_MILS)]
            font_size = whole_number(item.get("font_size", 10))
            if font_size is None or font_size <= 0:
                bad.append("font_size")
            owner = None
            if item.get("owner_part_id") is not None:
                owner = whole_number(item["owner_part_id"])
                if owner is None or owner < 0:
                    bad.append("owner_part_id")
            flags = {}
            for key in ("bold", "italic"):
                flag = (False if item.get(key) is None
                        else wire_bool(item[key]))
                if flag is None:
                    bad.append(key)
                flags[key] = flag
            if bad:
                problems.append(
                    f"texts[{i}] ({content!r}): {', '.join(bad)} not "
                    f"understood (x, y, rotation are finite numbers; "
                    f"font_size a whole number above 0; owner_part_id a "
                    f"whole number, 0 or more; bold and italic true or "
                    f"false)")
                continue
            fields = [
                f"text={content}",
                f"x={_snap(round(nums['x']))}",
                f"y={_snap(round(nums['y']))}",
                f"rotation={round(nums['rotation'])}",
                f"font_size={font_size}",
                f"font_name={_payload_safe(item.get('font_name', 'Arial'))}",
                f"bold={'true' if flags['bold'] else 'false'}",
                f"italic={'true' if flags['italic'] else 'false'}",
            ]
            if owner is not None:
                fields.append(f"owner_part_id={owner}")
            op_strs.append(";".join(fields))
        if problems:
            return {"ok": False, "added": 0, "problems": problems,
                    "reason": ("nothing was placed: "
                               + "; ".join(problems[:5]))}
        if not op_strs:
            return {"error": "No texts provided", "added": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_text",
            {"texts": join_ops(op_strs)},
        )

    @mcp.tool()
    async def lib_add_symbol_lines(
        lines: list[dict[str, Any]],
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add MANY lines to the current symbol body in ONE call.

        PREFER THIS over adding lines one at a time (there is no singular
        variant). A 12-line LED diode glyph drawn line-by-line would be 12
        LLM turns + 12 IPC round-trips + 12 redraw passes; this tool does
        it in one turn
        with a single PreProcess/PostProcess pair and one editor
        redraw at the end.

        Args:
            lines: list of dicts, each with keys ``x1``, ``y1``,
                ``x2``, ``y2`` (mils, int), ``width`` (int 0-3,
                default 1).
            grid: snap grid (mils) for the line endpoints. Glyph lines do
                NOT need the 100-mil pin grid (rule 15), so the default is
                a finer 25 mils for clean glyph detail; pass 100 for the
                coarse grid or 0/1 to disable snapping.

        Returns:
            Dict with ``added``, ``failed``, ``total`` counts.
        """
        op_strs: list[str] = []
        for line in lines:
            fields = [
                f"x1={_snap_grid(line.get('x1', 0), grid)}",
                f"y1={_snap_grid(line.get('y1', 0), grid)}",
                f"x2={_snap_grid(line.get('x2', 0), grid)}",
                f"y2={_snap_grid(line.get('y2', 0), grid)}",
                f"width={int(line.get('width', 1))}",
            ]
            op_strs.append(";".join(fields))
        if not op_strs:
            return {"error": "No lines provided", "added": 0}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_lines",
            {"lines": "~~".join(op_strs)},
        )

    # =========================================================================
    # Footprint Creation
    # =========================================================================

    @mcp.tool()
    async def lib_create_footprint(
        name: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a new PCB footprint in the active library.

        Args:
            name: Footprint name
            description: Footprint description

        Returns:
            Dictionary with created footprint information
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.create_footprint",
            {"name": name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_create_standard_footprint(
        name: str,
        family: str,
        pin_count: int,
        pitch: float = 50.0,
        pad_w: float = 24.0,
        pad_h: float = 30.0,
        row_span: float = 0.0,
        shape: str = "roundrect",
        corner_radius: int = 25,
        silk: bool = True,
        courtyard: float = 10.0,
        body_w: float = 0.0,
        body_h: float = 0.0,
        hole: float = 0.0,
        rows: int = 0,
        cols: int = 0,
        exposed_pad: float = 0.0,
        skip: list[str] = None,
        tab_w: float = 0.0,
        tab_h: float = 0.0,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a COMPLETE standard footprint in ONE call.

        Composes parametric geometry with the bulk authoring tools: makes
        the footprint, then emits every pad + the silkscreen outline +
        the courtyard rectangle in two batched round-trips. Supply the
        package's recommended land-pattern dimensions from the datasheet
        (this carries no part library -- the numbers you pass ARE the
        footprint).

        Args:
            name: Footprint name (e.g. "SOIC-8_3.9x4.9").
            family: "chip" (2-pin passive) | "sip" (single-row header) |
                "dual" (SOIC/SOP/SON/SOT/DIP) | "header" (2-row pin/box
                header, IDC, SWD/JTAG -- column-major odd/even numbering) |
                "tab" (power/tab package: SOT-223, DPAK/D2PAK, TO-220 --
                signal pins one side + a large tab pad opposite, pass
                tab_w & tab_h) | "quad" (QFP/QFN) | "bga" (ball grid --
                pass rows & cols). SOT-23 is "dual" with pin_count=3.
                Through-hole (DIP/header/TO-220) = any family with hole > 0.
            pin_count: total pads (2 for chip; even for header; n signal
                pins for tab; divisible by 4 for quad; ignored for bga).
            rows, cols: BGA ball grid dimensions (bga family only).
            exposed_pad: centre thermal/exposed pad size (mils) for QFN/QFP
                (quad family); 0 = none. Gets designator pin_count+1.
            tab_w, tab_h: tab pad size (mils) for the "tab" family; the tab
                gets designator pin_count+1 (tie it to a signal pin in the
                schematic).
            skip: BGA ball designators to omit (depopulation), e.g.
                ["A1","E5"] (bga family only).
            pitch: pad centre-to-centre within a row (mils).
            pad_w, pad_h: pad size (mils).
            row_span: centre-to-centre between the two rows / opposite
                sides (mils). Required for dual and quad.
            shape: pad shape (default "roundrect"); corner_radius is its %.
            silk: draw a silkscreen body outline (TopOverlay).
            courtyard: clearance (mils) past the extent for the courtyard
                rectangle (Mechanical1); 0 disables it.
            body_w, body_h: package body for the silk outline; default to
                the pad extent.
            hole: drill size (mils). >0 makes through-hole pads (DIP /
                header / axial): pin 1 rectangular, the rest round.

        Returns:
            Dict with the footprint name, pad/track counts, extent, and the
            per-step bridge results.
        """
        from eda_agent.design.footprint_gen import generate_footprint

        try:
            geom = generate_footprint(
                family, pin_count, pitch=pitch, pad_w=pad_w, pad_h=pad_h,
                row_span=row_span, shape=shape, silk=silk,
                courtyard=courtyard, body_w=body_w, body_h=body_h,
                hole=hole, rows=rows, cols=cols, exposed_pad=exposed_pad,
                skip=skip, tab_w=tab_w, tab_h=tab_h)
        except ValueError as e:
            return {"ok": False, "reason": str(e)}

        bridge = get_bridge()
        created = await bridge.send_command_async(
            "library.create_footprint",
            {"name": name, "description": description},
        )

        # Shared helper, same reasoning as the pins above: designator
        # and shape were interpolated raw here.
        pads_payload, _ = _pads_payload([
            {**pad, "shape": pad.get("shape", shape),
             "corner_radius": corner_radius, "rotation": 0,
             "layer": "TopLayer"}
            for pad in geom.pads
        ])
        pads_res = await bridge.send_command_async(
            "library.add_footprint_pads", {"pads": pads_payload})

        tracks = geom.all_tracks()
        tracks_res: dict[str, Any] = {}
        if tracks:
            # The shared builder: generated outlines keep their sub-mil
            # geometry instead of being rounded to whole mils here.
            tracks_payload, _ = _tracks_payload(tracks)
            tracks_res = await bridge.send_command_async(
                "library.add_footprint_tracks", {"tracks": tracks_payload})

        return {
            "footprint": name,
            "family": family,
            "pin_count": pin_count,
            "pads": len(geom.pads),
            "tracks": len(tracks),
            "width_mils": geom.width_mils,
            "height_mils": geom.height_mils,
            "create": created,
            "pads_result": pads_res,
            "tracks_result": tracks_res,
        }

    @mcp.tool()
    async def lib_add_footprint_pad(
        designator: str,
        x: float,
        y: float,
        x_size: float = 60,
        y_size: float = 60,
        hole_size: float = 0,
        shape: str = "rectangular",
        layer: str = "TopLayer",
        rotation: float = 0,
        corner_radius: int = 25,
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a pad to the current footprint.

        Args:
            designator: Pad designator (e.g., "1", "2"); "" for an
                unnamed fiducial or mechanical pad.
            x: X coordinate in mils, relative to the footprint origin.
                Sub-mil values are kept (1/10000 mil).
            y: Y coordinate in mils
            x_size: Pad X size in mils
            y_size: Pad Y size in mils
            hole_size: Drill hole size in mils (0 for SMD). A drilled pad
                is forced through-hole (MultiLayer); a hole-less pad is
                SMD on `layer`.
            shape: Pad shape -- "round", "rectangular", "octagonal", or
                "roundrect" (rounded-rectangle, the modern IPC SMD
                default). An oval/obround is "round" with x_size != y_size.
            layer: Layer for an SMD pad ("TopLayer"/"BottomLayer"); ignored
                for through-hole pads (always MultiLayer).
            rotation: Pad rotation in degrees
            corner_radius: Corner radius percentage for shape="roundrect"
                (Altium stores RR radius as a %; 25 is typical). Applied
                on every copper layer, through-hole pads included.
            component_name: Footprint to add the pad to. It is made the
                library's current footprint, and the switch is checked,
                as the track, arc, text, fill and region tools do.
                Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first; refused when a
                different document ends up in front.

        Returns:
            Dictionary confirming pad addition; ``shape_status`` says
            whether a roundrect read back ("confirmed") or not.
        """
        # The same guards as the track, arc and text siblings: _mil on NaN
        # raised a traceback, and a rotation sent raw reached StrToFloatDef
        # as NaN/inf text and came back as its default.
        nums = {"x": _num(x), "y": _num(y), "x_size": _num(x_size),
                "y_size": _num(y_size), "hole_size": _num(hole_size),
                "rotation": _num(rotation)}
        bad = [k for k, v in nums.items() if v is None]
        if bad:
            return {"ok": False, "reason": (
                f"not a finite number: {', '.join(bad)} (mils, and degrees "
                f"for rotation)")}
        if nums["x_size"] <= 0 or nums["y_size"] <= 0:
            return {"ok": False, "reason": (
                "x_size and y_size must be greater than zero")}
        if nums["hole_size"] < 0:
            return {"ok": False, "reason": (
                "hole_size cannot be negative (0 makes the pad SMD)")}
        cr = whole_number(corner_radius)
        if cr is None or not 0 <= cr <= 100:
            return {"ok": False, "reason": (
                "corner_radius is a whole percentage, 0 to 100")}
        params: dict[str, Any] = {
            "designator": designator,
            "x": _mil(nums["x"]),
            "y": _mil(nums["y"]),
            "x_size": _mil(nums["x_size"]),
            "y_size": _mil(nums["y_size"]),
            "hole_size": _mil(nums["hole_size"]),
            "shape": shape,
            "layer": layer,
            "rotation": _mil(nums["rotation"]),
            "corner_radius": cr,
        }
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_pad", params)
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_pad")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_pads(
        pads: list[dict[str, Any]],
        allow_blank_designator: bool = False,
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add MANY pads to the current footprint in ONE call.

        PREFER THIS over looping `lib_add_footprint_pad`. A 64-pad QFP
        built one pad at a time is 64 LLM turns and 64
        PreProcess/PostProcess+save cycles; with this tool it's one turn,
        one PreProcess/PostProcess, one save. Pad coords are NOT snapped
        to a grid (footprints use fine pitches like 50 mils / 0.5 mm).

        Args:
            pads: List of pad dicts, each with:
                - designator (str, required) -- e.g. "1", "2", "A1". A
                  blank one is dropped and counted unless the pad is
                  unplated, carries "allow_blank_designator": true, or
                  the call passes allow_blank_designator=True.
                - x, y       (float, mils), pad centre relative to the
                  footprint origin; sub-mil values are kept
                - x_size, y_size (float, mils, default 60)
                - hole_size  (float, mils, default 0 = SMD)
                - shape      (str, default "rectangular"), one of
                  round / rectangular / octagonal / roundrect (modern IPC
                  rounded-rectangle). Oval = round with x_size!=y_size.
                  roundrect is set on every copper layer, so it works on
                  through-hole pads too.
                - corner_radius (int %, default 25) for shape="roundrect"
                - rotation   (float deg, default 0)
                - layer      (str, default "TopLayer"). Only used for SMD
                  pads (hole_size=0); a drilled pad is forced through-hole
                  (MultiLayer). Use "BottomLayer" for bottom-side SMD.
                - plated (bool): False makes an NPTH hole.
                - hole_type (str): round / square / slot; slot_length
                  (mils) and hole_rotation (deg) for a slot.
                - mask_expansion, paste_expansion (float, mils): a manual
                  per-pad override of the design rule.
            allow_blank_designator: Keep pads whose designator is blank
                (fiducials, mechanical pads) instead of dropping them.
            component_name: Footprint to add to, for the whole batch. It
                is made the library's current footprint, and the switch is
                checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first; refused when a
                different document ends up in front.

        Example, a 4-pad 0402 + corner pad in one call:
            lib_add_footprint_pads(pads=[
                {"designator": "1", "x": -25, "y": 0, "x_size": 30,
                 "y_size": 40},
                {"designator": "2", "x":  25, "y": 0, "x_size": 30,
                 "y_size": 40},
            ])

        Returns:
            Dict with added, failed, total counts, and
            roundrect_requested / roundrect_unconfirmed: a roundrect that
            did not read back is counted, never assumed.
        """
        payload, skipped_invalid = _pads_payload(pads, allow_blank_designator)

        if not payload:
            return {
                "error": ("No valid pads (every entry was missing a "
                          "designator, or carried a number or flag that is "
                          "not one; pass allow_blank_designator=True for "
                          "fiducials or mechanical pads)"),
                "added": 0,
                "total": len(pads),
                "skipped_invalid": skipped_invalid,
            }

        params: dict[str, Any] = {"pads": payload}
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_pads", params)
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_footprint_track(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: float = 10,
        layer: str = "TopOverlay",
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add a track to a footprint (for silkscreen/courtyard).

        Lengths are DECIMAL mils, kept to 1/10000 mil like the pad tools:
        a 0.15 mm courtyard line is 5.9055, not 6.

        Args:
            x1: Start X in mils, relative to the footprint origin
            y1: Start Y in mils
            x2: End X in mils
            y2: End Y in mils
            width: Track width in mils
            layer: Layer name. Default "TopOverlay" (silkscreen). Any
                Altium layer is accepted, e.g. "BottomOverlay" or
                "Mechanical1".."Mechanical32" for courtyard / assembly
                outlines.
            component_name: Footprint to draw in. It is made the
                library's current footprint, and the switch is checked,
                so later calls without a name keep using it. Defaults to
                the current footprint.
            library_path: Optional .PcbLib to focus first. Defaults to the
                active document.

        Returns:
            Dict with ``success``, ``footprint``, ``layer`` and
            ``pending_save`` (run ``lib_save_library`` to write the file).
        """
        coords = [_num(v) for v in (x1, y1, x2, y2)]
        w = _num(width)
        if any(c is None for c in coords) or w is None or w <= 0:
            return {"ok": False, "reason": (
                "x1, y1, x2, y2 must be numbers and width a positive number, "
                "all in mils")}
        params: dict[str, Any] = {
            "x1": _mil(coords[0]), "y1": _mil(coords[1]),
            "x2": _mil(coords[2]), "y2": _mil(coords[3]),
            "width": _mil(w), "layer": layer,
        }
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_track", params)
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_track")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_tracks(
        tracks: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add MANY tracks to a footprint in ONE call.

        PREFER THIS over looping `lib_add_footprint_track`. A silkscreen
        outline + assembly outline is 8+ segments; built one at a time
        that's 8+ LLM turns and 8+ PreProcess/PostProcess+save cycles.
        Batched it's one turn, one PreProcess/PostProcess, one save.

        Lengths are DECIMAL mils, kept to 1/10000 mil like the pad tools,
        so a courtyard need not be rounded outward to whole mils.

        Args:
            tracks: List of track dicts, each with:
                - x1, y1, x2, y2 (mils, decimals kept) -- endpoints,
                  relative to the footprint origin
                - width (mils, default 10)
                - layer (str, default "TopOverlay" silkscreen). Any
                  Altium layer name, e.g. "BottomOverlay" or
                  "Mechanical1".."Mechanical32" for courtyard/assembly.
            component_name: Footprint to draw in, for the whole batch. It
                is made the library's current footprint, and the switch is
                checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.

        Example, a 100x60 mil silkscreen rectangle in one call:
            lib_add_footprint_tracks(tracks=[
                {"x1": -50, "y1": -30, "x2":  50, "y2": -30},
                {"x1":  50, "y1": -30, "x2":  50, "y2":  30},
                {"x1":  50, "y1":  30, "x2": -50, "y2":  30},
                {"x1": -50, "y1":  30, "x2": -50, "y2": -30},
            ])

        Returns:
            Dict with added, failed, total counts, ``footprint`` and
            ``pending_save``; ``skipped_invalid`` counts tracks dropped
            here for a missing or non-numeric field.
        """
        payload, skipped_invalid = _tracks_payload(tracks)
        if not payload:
            return {
                "error": "No valid tracks (each needs numeric x1,y1,x2,y2 "
                         "and a positive width)",
                "added": 0,
                "total": len(tracks),
                "skipped_invalid": skipped_invalid,
            }
        params: dict[str, Any] = {"tracks": payload}
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_tracks", params)
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_footprint_arc(
        x_center: float,
        y_center: float,
        radius: float,
        start_angle: float = 0,
        end_angle: float = 360,
        width: float = 10,
        layer: str = "TopOverlay",
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add an arc to a footprint.

        Centre, radius and width are DECIMAL mils, kept to 1/10000 mil.

        Args:
            x_center: Center X in mils, relative to the footprint origin
            y_center: Center Y in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            width: Line width in mils
            layer: Layer name. Default "TopOverlay" (silkscreen). Any
                Altium layer accepted, e.g. "Mechanical1".."Mechanical32"
                for pin-1 / assembly markers.
            component_name: Footprint to draw in. It is made the
                library's current footprint, and the switch is checked.
                Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.

        Returns:
            Dict with ``success``, ``footprint``, ``layer`` and
            ``pending_save``.
        """
        nums = [_num(v) for v in (x_center, y_center, radius, start_angle,
                                  end_angle, width)]
        if any(n is None for n in nums) or nums[2] <= 0 or nums[5] <= 0:
            return {"ok": False, "reason": (
                "x_center, y_center, start_angle and end_angle must be "
                "numbers, and radius and width positive numbers (mils)")}
        params: dict[str, Any] = {
            "x_center": _mil(nums[0]),
            "y_center": _mil(nums[1]),
            "radius": _mil(nums[2]),
            "start_angle": _mil(nums[3]),
            "end_angle": _mil(nums[4]),
            "width": _mil(nums[5]),
            "layer": layer,
        }
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_arc", params)
        return result

    @mcp.tool()
    async def lib_add_footprint_text(
        text: str,
        x: float = 0,
        y: float = 0,
        size: float = 50,
        width: float = 8,
        rotation: float = 0,
        layer: str = "TopOverlay",
        use_ttfont: bool = False,
        mirror: bool = False,
        library_path: Optional[str] = None,
        component_name: Optional[str] = None,
        registration: str = "both",
        remove_from_board: bool = False,
    ) -> dict[str, Any]:
        """Add a text primitive to a PcbLib footprint.

        Trap baked into this handler: in a PcbLib, adding a primitive
        only to the footprint does NOT register it with the placement
        editor -- the text shows up only after a save+reload. This
        tool adds the text to BOTH the footprint and the underlying
        board, then broadcasts ``PCBM_BoardRegisteration`` to both, which
        is the pattern that registers the primitive reliably.

        ONE TEXT PER CALL, CHECKED. Text was measured coming back twice
        from one call. The reply now counts what the call created:
        ``new_objects`` is how many distinct text objects appeared (1 is
        right), ``group_yields`` how often the footprint lists them (2 is
        the known double registration of a primitive added this session,
        harmless and gone after a reload), and a second NEW object with
        the same string is removed on the spot and counted in
        ``duplicates_removed``. ``lib_audit_footprint_policies`` no longer
        reads the double registration as two texts either.

        The cleanup removes nothing unless the text this call created was
        read by address and is listed by the footprint, so it can never
        take the new text with the duplicate; ``duplicate_cleanup`` says
        ``none``, ``removed`` or why it was skipped. A duplicate is taken
        off the footprint only, unless ``remove_from_board``.

        KNOWN UNFIXED (2026-10-04): that duplicate is created in this
        session and removed from the CURRENT footprint, the exact case
        lib_delete_footprint_pads measured coming back after the save
        (the deletes now save, reopen and park first; this cleanup does
        not, because saving the library inside an add would be a far
        bigger side effect). So a ``duplicates_removed`` above 0 may not
        survive the save. Check with lib_save_library, then
        lib_read_pcblib_file(library_path, footprint_name). A survivor
        can go with lib_delete_footprint_primitives (object_type "text",
        layer, library_path), whose reload_first makes the removal stick,
        but that removes EVERY text on the layer: re-add the one wanted
        after it. Follow-up recorded in docs/RELEASE_VERIFICATION.md
        step 31.

        Args:
            text: The string to place. Required.
            x, y: Coordinates in DECIMAL mils, relative to the FOOTPRINT's
                origin, kept to 1/10000 mil.
            size: Text height in mils. 50 is a common silkscreen size;
                drop to 30-40 for tight footprints.
            width: Stroke width in mils. 8 reads cleanly at 50 mil
                size; scale with ``size``.
            rotation: Rotation in degrees (0, 90, 180, 270 typical).
            layer: Layer name (``TopOverlay``, ``BottomOverlay``,
                ``TopSolder``, ``BottomSolder``, ``Mechanical1`` ...
                ``Mechanical32``).
            use_ttfont: ``True`` for TrueType; default ``False`` is the
                vector stroke font that fab houses prefer.
            mirror: Mirror the text. Set this for anything on a BOTTOM
                layer, or it reads backwards on the finished board;
                ``audit_find_mirrored_pcb_text`` reports both halves of
                the mistake (bottom text unmirrored, top text mirrored).
            library_path: Optional .PcbLib to focus before adding.
                Defaults to the active document.
            component_name: Footprint to add to. It is made the library's
                current footprint and the switch is checked, so a name
                that cannot be selected is refused instead of the text
                landing elsewhere. Defaults to the current footprint.
            registration: Diagnostic, leave at "both". "footprint" or
                "board" runs only that half of the registration, so one
                live session can show which persists exactly once after
                save and reload.
            remove_from_board: also take a removed duplicate off the
                library board. Unmeasured and off by default; see
                ``lib_delete_footprint_primitives``.

        Returns:
            Dict with ``success``, ``footprint``, ``text``, ``layer``,
            ``x``, ``y``, ``registration``, ``new_objects``,
            ``group_yields``, ``duplicates_found``, ``duplicates_removed``,
            ``duplicate_cleanup``, ``board_removal`` and ``pending_save``.
        """
        if not text:
            return {"ok": False,
                    "reason": "text is required: pass the string to place"}
        nums = [_num(v) for v in (x, y, size, width, rotation)]
        if any(n is None for n in nums) or nums[2] <= 0 or nums[3] <= 0:
            return {"ok": False, "reason": (
                "x, y and rotation must be numbers, and size and width "
                "positive numbers (mils)")}
        mode = (registration or "both").strip().lower()
        if mode not in _TEXT_REGISTRATIONS:
            return {"ok": False, "reason": (
                f"registration must be one of {', '.join(_TEXT_REGISTRATIONS)}")}
        bridge = get_bridge()
        params: dict[str, Any] = {
            "text": text,
            "x": _mil(nums[0]), "y": _mil(nums[1]),
            "size": _mil(nums[2]), "width": _mil(nums[3]),
            "rotation": _mil(nums[4]), "layer": layer,
        }
        if use_ttfont:
            params["use_ttfont"] = "true"
        if mirror:
            params["mirror"] = "true"
        if library_path:
            params["library_path"] = library_path
        if component_name:
            params["component_name"] = component_name
        if mode != "both":
            params["registration"] = mode
        if remove_from_board:
            params["remove_from_board"] = "true"
        result = await bridge.send_command_async(
            "library.add_footprint_text", params,
        )
        return result

    @mcp.tool()
    async def lib_add_footprint_fills(
        fills: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add rectangular fills on ANY layer of a footprint, in ONE call.

        FOR THE THINGS THAT USED TO BE BOARD-LEVEL NOTES: stencil apertures
        on TopPaste (suppress the pad's own paste with its
        ``paste_expansion`` in ``lib_add_footprint_pads``), solder-mask
        openings on TopSolder, keep-outs, mechanical marks. Inside the
        footprint they travel with the part to every board.

        NOT ``pcb_place_fill``, which acts on a BOARD and, aimed at a
        library, can land on whichever board an open project holds.

        Args:
            fills: List of fill dicts, each with:
                - x1, y1, x2, y2 (mils, decimals kept) -- opposite
                  corners, relative to the footprint origin, either order
                - layer (str, REQUIRED): e.g. "TopPaste", "BottomPaste",
                  "TopSolder", "BottomSolder", "TopLayer", "KeepOutLayer",
                  "Mechanical1".."Mechanical32"
                - rotation (degrees about the fill centre, default 0)
                - keepout (bool, default False): make a fill on a copper
                  layer a keep-out for that layer only. A fill on
                  "KeepOutLayer" is a keep-out already. Not yet verified
                  live: read ``keepout_applied`` in the reply.
            component_name: Footprint to add to, for the whole batch. It
                is made the library's current footprint, and the switch
                is checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.

        Example, a 3x3 grid of 49.213 mil paste apertures on a thermal pad:
            lib_add_footprint_fills(component_name="QFN-64_9x9", fills=[
                {"x1": x - 24.6063, "y1": y - 24.6063,
                 "x2": x + 24.6063, "y2": y + 24.6063, "layer": "TopPaste"}
                for x in (-70.866, 0, 70.866) for y in (-70.866, 0, 70.866)
            ])

        Returns:
            Dict with ``added``, ``failed``, ``total``, ``failures``
            (item and reason), ``unknown_layers``, ``keepout_requested``,
            ``keepout_applied``, ``footprint`` and ``pending_save``.
            ``skipped`` lists fills refused here before sending.
        """
        payload, skipped = _fills_payload(fills)
        if not payload:
            return {"ok": False,
                    "reason": "No valid fills to send",
                    "added": 0, "total": len(fills), "skipped": skipped}
        params: dict[str, Any] = {"fills": payload}
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_fills", params)
        if isinstance(result, dict) and skipped:
            result["skipped"] = skipped
        return result

    @mcp.tool()
    async def lib_add_footprint_regions(
        regions: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Add polygonal regions on ANY layer of a footprint, in ONE call.

        The shapes a rectangle cannot make: a plus-shaped keep-out between
        a crystal's pads, an L-shaped mask opening, a chamfered paste
        aperture, a polygon-pour cutout under an RF pad. Inside the
        footprint they travel with the part to every board.

        NOT ``pcb_place_region``, which acts on a BOARD.

        Args:
            regions: List of region dicts, each with:
                - vertices: list of [x, y] points in mils (decimals kept),
                  relative to the footprint origin, at least 3. Do not
                  repeat the first point at the end; a repeat is dropped.
                - layer (str): e.g. "TopLayer", "TopPaste", "TopSolder",
                  "KeepOutLayer", "Mechanical13". Required, except for
                  kind "keepout", which defaults to "KeepOutLayer".
                - kind (str, default "copper"):
                  "copper"  a solid region on the layer (on a paste or
                            mask layer that is the aperture or opening);
                  "cutout"  a polygon-pour cutout on a copper layer;
                  "keepout" a keep-out. On "KeepOutLayer" it blocks all
                            layers; on a named copper layer it blocks that
                            layer only, which is NOT yet verified live:
                            read ``keepout_applied``.
            component_name: Footprint to add to, for the whole batch. It
                is made the library's current footprint, and the switch
                is checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.

        Example, a plus-shaped top-layer keep-out between four pads:
            lib_add_footprint_regions(component_name="OSC_2016", regions=[
                {"kind": "keepout", "layer": "TopLayer", "vertices": [
                    [-3.543, -40.354], [3.543, -40.354],
                    [3.543, 40.354], [-3.543, 40.354]]},
                {"kind": "keepout", "layer": "TopLayer", "vertices": [
                    [-33.071, -16.732], [33.071, -16.732],
                    [33.071, 16.732], [-33.071, 16.732]]},
            ])

        Returns:
            Dict with ``added``, ``failed``, ``total``, ``vertices``,
            ``failures`` (item and reason), ``unknown_layers``,
            ``kind_requested`` / ``kind_applied``, ``keepout_requested`` /
            ``keepout_applied``, ``footprint`` and ``pending_save``.
            ``skipped`` lists regions refused here before sending.
        """
        payload, skipped = _regions_payload(regions)
        if not payload:
            return {"ok": False,
                    "reason": "No valid regions to send",
                    "added": 0, "total": len(regions), "skipped": skipped}
        params: dict[str, Any] = {"regions": payload}
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_regions", params)
        if isinstance(result, dict) and skipped:
            result["skipped"] = skipped
        return result

    @mcp.tool()
    async def lib_set_current_footprint(
        component_name: str,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Make a named footprint the current one in a PCB library.

        The PcbLib twin of ``lib_set_current_component``, which only knows
        SchLibs. Every footprint add tool writes into the current
        footprint unless it is given ``component_name``, and until this
        the only way to choose one was to stamp a text on it.

        The switch is read back, and a name that cannot be selected is
        refused rather than left pointing at the previous footprint.

        Args:
            component_name: The footprint to make current.
            library_path: Optional .PcbLib to focus first. Defaults to the
                active document.

        Returns:
            Dict with ``success``, ``footprint``, and ``origin_x_mils`` /
            ``origin_y_mils``: the footprint's origin AFTER the switch,
            which can move it, in the board frame the library uses.
        """
        if not component_name or not component_name.strip():
            return {"ok": False,
                    "reason": "component_name is required: the footprint "
                              "to make current"}
        params: dict[str, Any] = {"component_name": component_name.strip()}
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_current_footprint", params)

    @mcp.tool()
    async def lib_extract_intlib(
        intlib_path: str,
    ) -> dict[str, Any]:
        """Extract .SchLib + .PcbLib sources from an .IntLib.

        Opens the integrated library in Altium, runs the editor's
        ``Extract Sources`` command, then probes Altium's conventional
        output locations (a sibling folder named after the IntLib base
        name, falling back to the IntLib's own directory) to report
        which source files actually appeared on disk.

        After this returns with ``sch_lib_found`` and / or
        ``pcb_lib_found`` true, the produced files can be opened with
        the rest of the library toolset -- ``lib_get_components``,
        ``lib_get_footprints``, ``lib_copy_component``, etc. -- by
        passing the reported ``sch_lib_path`` / ``pcb_lib_path`` as
        the ``library_path`` argument.

        Args:
            intlib_path: Absolute path to the .IntLib file.

        Returns:
            Dict with ``intlib_path``, ``extract_dir``, ``sch_lib_path``,
            ``sch_lib_found``, ``pcb_lib_path``, ``pcb_lib_found``. A
            ``_found`` flag of False means Altium did not produce that
            source -- either the extract command name needs adjusting
            for the local Altium build, the IntLib does not contain that
            kind of source, or write permissions prevented the dump.
        """
        if not intlib_path:
            return {"ok": False, "reason":
                    "intlib_path is required: absolute path to the .IntLib"}
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.extract_intlib",
            {"intlib_path": intlib_path},
            timeout=60.0,
        )
        return result or {}

    @mcp.tool()
    async def lib_get_footprints(
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Enumerate every footprint in a PcbLib.

        PcbLib counterpart to ``lib_get_components`` for SchLibs. Walks
        the PCB library with ``IPCB_Library.LibraryIterator_Create``
        and returns one entry per footprint with its name and
        description.

        Args:
            library_path: Optional .PcbLib path to focus first.
                Defaults to the focused document.

        Returns:
            Dict with ``library_path``, ``count`` and ``footprints``
            (a list of ``{name, description}``).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.get_footprints", params,
        )
        return result

    @mcp.tool()
    async def lib_audit_footprint_policies(
        library_path: Optional[str] = None,
        policy: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Sweep a PcbLib and flag footprints that break the library's own
        conventions: inconsistent layer usage, pad rules, pin-1 markings,
        courtyard, silkscreen, 3D models, designator style.

        For each policy dimension it learns the library's DOMINANT convention
        across all footprints and reports every footprint that deviates (so it
        works on any house style without hard-coded rules). Pass ``policy`` to
        pin a dimension to a required value instead, e.g.
        ``{"silk_layer": "Top Overlay", "courtyard": true}``.

        Each finding names the footprint, the dimension, and the expected vs
        actual value: enough to drive a fix, not just a report.

        Designators are checked for presence, layer, height, and for sitting on
        the footprint's AVERAGE PAD CENTRE rather than the library origin (which
        is arbitrary). Tune the centring window with
        ``policy={"designator_center_tol": <mils>}``.

        Geometry is read with the bulk ``library.get_library_geometry``
        command, which walks the library once and pages the result. A library
        of ~1000 footprints is one pass and a handful of round trips rather
        than one round trip (and one full library re-scan) per footprint.

        Args:
            library_path: Optional .PcbLib to focus first; defaults to the
                focused library.
            policy: Optional explicit conventions to enforce; omitted
                dimensions are inferred from the library.

        Returns ``{library_path, footprint_count, conventions, findings,
        summary}``.
        """
        from ..design.footprint_policy import (
            audit_footprint_library,
            plan_footprint_fixes,
        )

        bridge = get_bridge()
        footprints, resolved_path = await _read_library_geometry(
            bridge, library_path)
        report = audit_footprint_library(footprints, policy=policy)
        report["fixes"] = plan_footprint_fixes(report)
        report["library_path"] = resolved_path
        return report

    @mcp.tool()
    async def lib_convert_designators_to_stroke(
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Convert every TrueType ``.Designator`` in a PcbLib to a stroke font.

        A TrueType PCB text will not persist a position change made through
        ``XLocation``: the write reads back changed, then Altium recomputes the
        position from the TrueType layout on reload and it reverts. That is why a
        handful of designators per library refuse to centre. ``Bold``/``Italic``
        are TrueType-only attributes (a stroke text cannot be bold), so a bold or
        italic designator is the reliable TrueType tell.

        This clears ``UseTTFonts``, ``Bold`` and ``Italic`` on each such
        designator, reads back to confirm, saves, and reloads. Afterwards those
        designators are stroke and ``lib_fix_designators`` can centre them.

        The save is ``lib_save_library``'s (this one file, checked on disk),
        never ``app_save_all``. If it does not land, the library is not
        reloaded and ``errors`` says why.

        Returns ``{library_path, designators, converted, names, saved,
        save}``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        res = await bridge.send_command_async(
            "library.convert_designators_to_stroke", params,
            timeout=_GEOMETRY_TIMEOUT)
        res = res if isinstance(res, dict) else {}
        saved = False
        if res.get("converted"):
            resolved = res.get("library_path") or library_path
            # ONE library, and only when the file proves it changed.
            # app_save_all rewrote every open SchDoc and PcbDoc with fresh
            # UniqueIDs, and "saved" meant only that nothing raised: a save
            # Altium declined left the library dirty, and the reload below
            # then closed it into the Unsaved Changes prompt, blocking the
            # bridge. No reload unless the save landed.
            save = await _save_library_verified(bridge, resolved or "")
            res["save"] = save
            saved = bool(save.get("ok"))
            if saved:
                await _reload_library(bridge, resolved)
            else:
                res.setdefault("errors", []).append(
                    f"save failed, so the library was NOT reloaded (closing "
                    f"it dirty raises Altium's Unsaved Changes prompt): "
                    f"{save.get('reason')}")
        res["saved"] = saved
        return res

    @mcp.tool()
    async def lib_reload_library(library_path: str) -> dict[str, Any]:
        """Close and reopen a PcbLib so Altium rebuilds its caches from disk.

        ``IPCB_Text.BoundingRectangle`` is populated when the document loads and
        is NOT refreshed when a text moves or is resized, not even by
        ``GraphicallyInvalidate``. Anything that reads a text's extent after
        writing to it therefore gets the OLD box. Reload between a write and the
        next read.

        SAVE FIRST: this does not save, and closing a dirty document loses the
        edits or raises a prompt. ``lib_fix_designators`` already does this.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.reload_library", {"library_path": library_path},
            timeout=_GEOMETRY_TIMEOUT)

    @mcp.tool()
    async def lib_probe_designator(
        footprint_name: str,
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Diagnostic: dump one footprint's raw designator geometry.

        Read-only. Returns the footprint origin and bounding rectangle, the pad
        extents, and the ``.Designator`` anchor, bounding rectangle, size and
        width: all in native TCoord. Use it to establish what
        ``IPCB_Text.BoundingRectangle`` actually measures before trusting it to
        centre anything.

        Returns ``{footprint, fp_x, fp_y, fp_rect, pad_count, pad_rect,
        desig_anchor, desig_rect, desig_size, desig_width, desig_text}``.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"footprint_name": footprint_name}
        if library_path:
            params["library_path"] = library_path
        return await bridge.send_command_async(
            "library.probe_designator", params, timeout=_GEOMETRY_TIMEOUT)

    @mcp.tool()
    async def lib_fix_designators(
        library_path: Optional[str] = None,
        dry_run: bool = True,
        fix_layer: bool = True,
        fix_center: bool = True,
        fix_height: bool = False,
        add_missing: bool = False,
        policy: Optional[dict] = None,
    ) -> dict[str, Any]:
        """Bring every footprint's ``.Designator`` onto the library's OWN
        convention: the layer, height and centring the majority already use.

        Nothing is hard-coded. The target layer, its raw ordinal, the norm
        height and the centring tolerance are all inferred from this library
        (see ``lib_audit_footprint_policies``); ``policy`` overrides any of
        them. "Centred" means on the AVERAGE PAD CENTRE, not the library
        origin.

        DEFAULTS TO A DRY RUN. With ``dry_run=True`` (the default) it reports
        exactly which footprints it would touch, on which layer, and at which
        coordinates, and writes nothing. Pass ``dry_run=False`` to apply.

        Applying RUNS TO CONVERGENCE: apply, save, reload the PcbLib, re-plan,
        repeat until nothing is left. The save is ``lib_save_library``'s
        (this one file, checked on disk), never ``app_save_all``; a save
        that does not land stops the loop before the reload. The reload is mandatory, not cosmetic:
        Altium caches each text's bounding rectangle at load and never refreshes
        it when the text moves or is resized, so a second pass without a reload
        would centre against stale geometry and shove designators off the part.
        Resizing a text also changes its box, so height fixes and centring
        settle over successive passes rather than one.

        PARTIAL SUCCESS IS EXPECTED AND REPORTED, NOT HIDDEN. A small number of
        designators (a handful per library) do not persist a position change
        through Altium's save for reasons not yet diagnosed: the write reads
        back changed but the value reverts on reload. Rather than loop forever or
        claim success, the tool stops when a pass makes no progress and returns
        ``converged: false`` with the offending footprints in ``unrepairable``
        for a human to centre by hand. Everything else is applied. ``applied``
        counts only writes confirmed by read-back, never attempts.

        Args:
            library_path: .PcbLib to fix; defaults to the focused library.
            dry_run: report the plan without writing (default True).
            fix_layer: move designators off the convention layer onto it.
            fix_center: re-centre designators beyond the library's tolerance.
            fix_height: also normalise text height to the library norm.
            add_missing: create a ``.Designator`` on footprints that lack one.
            policy: explicit conventions, e.g.
                ``{"designator_center_tol": 50, "designator_height": 20}``.

        Returns ``{library_path, dry_run, conventions, planned, applied,
        created, passes, converged, failed, skipped, actions}``.
        """
        from ..design.footprint_policy import plan_designator_repairs

        bridge = get_bridge()
        footprints, resolved_path = await _read_library_geometry(
            bridge, library_path)
        if not footprints:
            return {"success": False,
                    "error": "no footprints (open the PcbLib)"}

        plan = plan_designator_repairs(
            footprints, fix_layer=fix_layer, fix_center=fix_center,
            fix_height=fix_height, add_missing=add_missing, policy=policy)
        actions = plan["actions"]

        result: dict[str, Any] = {
            "library_path": resolved_path,
            "dry_run": dry_run,
            "conventions": plan["conventions"],
            "planned": len(actions),
            "skipped": plan["skipped"],
            "actions": actions,
        }
        if dry_run or not actions:
            result["applied"] = 0
            result["failed"] = []
            return result

        # Baseline BEFORE any write: the loop rebinds `footprints` each pass,
        # and blaming a write for duplicates that were already there makes the
        # guard cry wolf and stop being believed.
        from ..design.footprint_policy import _designator_count
        before_dupes = {fp.get("name") for fp in footprints
                        if _designator_count(fp) > 1}

        # Apply, save, RELOAD, re-plan -- until nothing is left to do.
        #
        # One pass is never enough. Altium caches each text's BoundingRectangle
        # at load and does not refresh it when XLocation or Size changes, so
        # after a write every rectangle in memory is stale. Centring is computed
        # from that rectangle, so a second pass in the same session would move
        # designators by a wrong delta. Reloading the document drops the caches;
        # the next read gets true geometry. Resizing a text also changes its
        # box, so height fixes and centring genuinely need separate passes.
        failed: list[dict] = []
        applied, created, saved, passes = 0, 0, False, 0
        touched: set = set()

        while actions and passes < _MAX_PASSES:
            passes += 1
            touched.update(a["footprint"] for a in actions)
            a, c = await _apply_edits(bridge, actions, resolved_path, failed)
            applied += a
            created += c
            if not a:
                break

            # ONE library, proven on disk (see lib_convert_designators_to_
            # stroke): app_save_all churned every open sheet, and a save
            # Altium declined did not raise, so the reload that follows
            # closed a dirty library into the Unsaved Changes prompt.
            save = await _save_library_verified(bridge, resolved_path or "")
            if not save.get("ok"):
                failed.append({
                    "footprint": "*",
                    "error": (f"save failed, so the library was NOT "
                              f"reloaded and no further pass ran: "
                              f"{save.get('reason')}")})
                break
            saved = True

            if not await _reload_library(bridge, resolved_path):
                failed.append({
                    "footprint": "*",
                    "error": "library reload failed; stopping after one pass "
                             "(further passes would read a stale bounding-box "
                             "cache and misplace designators)"})
                break

            footprints, _ = await _read_library_geometry(bridge, resolved_path)
            next_actions = plan_designator_repairs(
                footprints, fix_layer=fix_layer, fix_center=fix_center,
                fix_height=fix_height, add_missing=add_missing,
                policy=policy)["actions"]

            # A pass that changes nothing will never change anything. Repeating
            # it burns passes and, worse, reports a growing `applied` count for
            # edits that are not landing.
            if _same_plan(actions, next_actions):
                stuck = [a["footprint"] for a in next_actions]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(stuck)} designators would not move; "
                             f"stopping (no progress): {', '.join(stuck[:10])}"})
                actions = next_actions
                break
            actions = next_actions

        result["passes"] = passes
        result["converged"] = not actions
        if actions:
            result["remaining"] = len(actions)
            result["unrepairable"] = [a["footprint"] for a in actions][:25]

        # Read the library BACK and confirm no footprint gained a second
        # .Designator. A create that lands on the wrong component is invisible
        # to the writer, so the only honest check is to re-measure.
        if applied:
            from ..design.footprint_policy import _designator_offsets

            after, _ = await _read_library_geometry(bridge, resolved_path)
            dupes = [fp.get("name") for fp in after
                     if _designator_count(fp) > 1
                     and fp.get("name") not in before_dupes]
            result["verified_no_duplicates"] = not dupes
            result["preexisting_duplicates"] = sorted(before_dupes)
            if dupes:
                result["duplicates_created"] = dupes[:25]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(dupes)} footprints now have duplicate "
                             f".Designator strings: RESTORE FROM BACKUP"})

            # A designator left at the board origin instead of on its part sits
            # tens of thousands of mils away. Nothing legitimate does that.
            stray = [fp.get("name") for fp, off, _, _ in
                     _designator_offsets(after)
                     if fp.get("name") in touched and off > _STRAY_MILS]
            result["verified_no_stray_designators"] = not stray
            if stray:
                result["strays_created"] = stray[:25]
                failed.append({
                    "footprint": "*",
                    "error": f"{len(stray)} designators landed >{_STRAY_MILS} "
                             f"mils from their pads: RESTORE FROM BACKUP"})

        result["applied"] = applied
        result["created"] = created
        result["failed"] = failed
        result["saved"] = saved
        return result

    # =========================================================================
    # Component Linking
    # =========================================================================

    @mcp.tool()
    async def lib_update_footprint_heights_from_3d(
        mode: str = "raise",
    ) -> dict[str, Any]:
        """Sweep the active PCB Library: for every footprint, find the
        tallest 3D body and write its ``OverallHeight`` to
        ``Footprint.Height``.

        Footprint.Height is what Altium's placement-collision DRC
        uses to enforce height-clearance rules (don't place a tall
        electrolytic under a low overhang; don't sandwich an LCD
        connector under another PCB in a multi-board stack-up).
        Libraries shipped from vendors without explicit heights default
        to 0 which makes the DRC silently no-op -- a real production
        risk caught only at first-article assembly.

        Args:
            mode: ``raise`` (default) only ever increases a height, so a
                hand-set "I know this part is 5mm despite the model
                being 3mm" survives. ``match`` also LOWERS one to the
                model.

        Reach for ``match`` when heights are too TALL, which raising
        cannot fix and which is the more damaging fault: a footprint
        claiming 50mm when the part is 3mm fails placement-collision
        DRC against everything near it and blocks placements that are
        fine, where a too-low height merely fails to catch a real
        collision. To correct one footprint, or one with no model at
        all, use ``lib_set_footprint_height``.

        Safety:
          - NEITHER MODE WRITES ZERO. A footprint with no 3D body
            yields no measurement, and writing the 0 that implies would
            silently disable the very DRC rule this arms. Those come
            back as ``without_model`` with their names.
          - Does NOT save the library; review the ``items[]`` diff and
            save in Altium.

        Returns:
            Dict with ``mode``, ``inspected``, ``updated``, ``lowered``,
            ``without_model``, ``without_model_names``, and ``items``
            (``{name, old_height_mm, new_height_mm}`` per footprint).
        """
        mode = (mode or "raise").strip().lower()
        if mode not in ("raise", "match"):
            return {
                "ok": False,
                "reason": (
                    f"mode must be 'raise' (only increase, the default) or "
                    f"'match' (also lower to the model). Got {mode!r}."),
            }
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.update_footprint_heights_from_3d", {"mode": mode},
            timeout=60.0,
        )

    @mcp.tool()
    async def lib_set_footprint_height(
        height_mm: float,
        footprint_name: str = "",
    ) -> dict[str, Any]:
        """Set one footprint's Height directly, up or down.

        The sweep can only derive a height from a 3D body, which leaves
        two cases it cannot serve: a part with no model, and a part
        whose model is wrong. There was no setter at all before this, so
        a footprint carrying an absurd height could be read and not
        corrected.

        WHY TOO TALL IS WORSE THAN TOO SHORT. Footprint.Height drives
        placement-collision DRC. A footprint claiming 50mm when the part
        is 3mm fails against everything near it and blocks placements
        that are fine. A too-low height only fails to catch a real
        collision. The first floods the report and gets the rule
        switched off; the second is quiet.

        ZERO IS ACCEPTED AND IS NOT NEUTRAL. It disables the rule for
        that footprint rather than relaxing it, so nothing is ever
        flagged against it however tall the real part is. The reply says
        so when zero is written.

        Args:
            height_mm: the height in MILLIMETRES, not mils. Must be
                non-negative.
            footprint_name: which footprint. Empty uses the library's
                current component.

        Returns:
            ``{name, old_height_mm, new_height_mm, changed, saved,
            save_note, note}``. ``saved`` is always false: the library
            is modified in memory and left for you to review and save.
        """
        try:
            height = float(height_mm)
        except (TypeError, ValueError):
            return {"ok": False,
                    "reason": f"height_mm must be a number, got {height_mm!r}"}
        if height < 0 or height != height:      # NaN fails both comparisons
            return {"ok": False,
                    "reason": (f"height_mm must be a non-negative number of "
                               f"millimetres, got {height_mm!r}")}

        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_footprint_height",
            {"height_mm": height, "footprint_name": footprint_name},
        )

    @mcp.tool()
    async def lib_link_footprint(
        component_name: str,
        footprint_name: str,
        footprint_library: str = "",
        replace: bool = True,
        set_current: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Link a footprint to a schematic component.

        With replace=True (the default) any existing footprint (PCBLIB) model
        on the component is removed first, so re-linking replaces rather than
        appends. Pass replace=False to keep prior footprint models and add
        another (the old append behaviour, which bloats a component with
        duplicate models).

        WHICH FOOTPRINT IS CURRENT is decided by ``set_current``, not left
        to chance. Measured before this parameter existed: replace=False
        left BOTH the old and the new footprint flagged current, because
        Altium does not clear the others by itself.

        Args:
            component_name: Name of the schematic component. Resolved by
                library reference and made current before linking, so the
                model lands on the symbol you name rather than on whatever
                was last created. Leave empty to target the current
                component.
            footprint_name: Name of the footprint to link
            footprint_library: NOT APPLIED, accepted only so existing
                callers keep working. The footprint is bound by NAME via
                the implementation's datafile link, with a deliberately
                EMPTY location: passing a .PcbLib path there wedges
                AD26 (see Lib_LinkFootprint in Library.pas). Install the
                library and keep footprint names unambiguous instead.
            replace: remove existing footprint models before adding (default True)
            set_current: None (default) makes the new footprint current
                only when no other footprint model is, so an alternate
                added with replace=False stays an alternate. True makes it
                current and demotes the others. False adds it non-current.
                To switch later, use lib_set_current_model.

        Returns:
            Dictionary with footprint, replaced, is_current (read back),
            demoted (how many other models lost the flag),
            current_footprint_count and the component's models list. A
            write whose flags did not land as asked is an error
            (CURRENT_NOT_APPLIED) carrying the same read-back in details.
        """
        bridge = get_bridge()
        if set_current is None:
            current = "auto"
        else:
            current = "true" if set_current else "false"
        result = await bridge.send_command_async(
            "library.link_footprint",
            {
                "component_name": component_name,
                "footprint_name": footprint_name,
                # footprint_library is deliberately NOT sent: nothing on
                # the Pascal side reads it, and the only place it could
                # go is the datafile link location, which wedges AD26.
                "replace": "true" if replace else "false",
                "set_current": current,
            },
        )
        return result

    @mcp.tool()
    async def lib_link_3d_model(
        component_name: str,
        model_path: str,
        offset_x: float = 0,
        offset_y: float = 0,
        offset_z: float = 0,
        rotation_x: float = 0,
        rotation_y: float = 0,
        rotation_z: float = 0,
        replace: bool = False,
        align: str = "none",
        library_path: str = "",
        layer: str = "",
        remove_from_board: bool = False,
        reload_first: bool = True,
        verify_on_disk: bool = True,
    ) -> dict[str, Any]:
        """Link a 3D STEP model to a PcbLib footprint.

        Loads the STEP geometry into the footprint as an IPCB_ComponentBody
        (the real 3D body that drives the 3D view and the height/placement
        DRC). ``component_name`` selects the footprint by name (empty uses
        the library's current footprint). The library is ``library_path``
        when given, otherwise the active document, which must then be the
        .PcbLib.

        TO SWAP AN IPC GENERATOR PLACEHOLDER FOR A MANUFACTURER STEP, pass
        ``replace=True``: every 3D body already on the footprint (the
        generator's extruded box and pin cylinders, or an earlier STEP) is
        removed first. The removal runs only after the new model has
        loaded, so an unreadable file never leaves the footprint bare.

        Args:
            component_name: Name of the footprint in the PcbLib. It is
                made the library's current footprint and the switch is
                read back (``SELECT_FAILED`` otherwise), because the body
                is also registered with the library board, which attaches
                it to the CURRENT footprint.
            model_path: Path to the 3D model file (.step, .stp); must exist.
            offset_x: X offset in mils. What it is measured from depends
                on ``align``.
            offset_y: Y offset in mils, with offset_x.
            offset_z: Z offset in mils: the model's ``MODEL.3D.DZ``,
                written on the model (``Model.SetState``'s Dz) with the
                rotation, before the body derives its heights. That is
                the field Altium's own libraries place a STEP with. It
                used to set StandoffHeight, which was measured
                (2026-10-04) to store a standoff without lifting the
                model. The common adjustment: lifting a connector body
                so it sits on its pads rather than through them.
            rotation_x: rotation about X in degrees. Written on the MODEL
                (``Model.SetState``) before the body derives its outline,
                because the model is where a 3D body's rotation lives:
                assigning ``Body.Rotation`` raised an uncatchable
                "Undeclared identifier" on AD26 and took the polling loop
                down with it. A STEP authored Y-up usually needs
                rotation_x=90 to stand on the board.
            rotation_y: rotation about Y in degrees, the same route.
            rotation_z: rotation about Z in degrees, the same route.
            replace: remove the footprint's existing 3D bodies first.
                With ``replace`` the library is saved and reopened first
                by default (``reload_first``), so every body it replaces
                is loaded from disk. Whenever ``component_name`` is given
                (with or without reload_first) the old bodies are removed
                by ``lib_delete_footprint_primitives`` (componentbody)
                with the footprint PARKED (another footprint made
                current), and the new one is linked without replace: the
                handler's own replace removes while the footprint is
                CURRENT, and a primitive removed from the current
                footprint was MEASURED (2026-10-05, step 31 item 3b) to
                come back with the next save. With ``component_name``
                empty there is no footprint to park, so the handler's own
                replace runs and the reply carries a top-level
                ``warning``. The replaced body's layer is read first and
                inherited. Cost: the old bodies go before the new model
                loads (a ``model_path`` that is not a file is refused
                first); if the link then fails the reply says how to
                restore: lib_reload_library goes back to the save
                reload_first just made, or without reload_first to the
                LAST save (discarding every other unsaved edit). Parking
                without a reload is not yet measured live.
            remove_from_board: with ``replace``, also take the replaced
                bodies off the library's backing board. Off by default,
                and measured on the pad delete NOT to stop a
                session-created primitive coming back: see
                ``lib_delete_footprint_primitives``, which shares the
                rule. ``replaced.board_removal`` reports what it found.
            reload_first: with ``replace``, ON by default: save the
                library and reopen it before the link, so every body it
                replaces is loaded from disk. Uses ``library_path``, or
                the focused .PcbLib when it is empty; skipped (and said
                so in ``warning`` and ``reload_first.skipped``) when
                neither resolves or ``component_name`` is empty, because
                a reopen changes which footprint is current. A
                ``model_path`` that is not a file, or a ``component_name``
                the library does not hold (lib_get_footprints, read-only),
                is refused BEFORE the save (and, without reload_first,
                before the removal); a name matching in another case is
                sent on in the library's own spelling. A relative
                ``model_path`` that is a file here is sent as its
                absolute path, because Altium would resolve it against
                its own working directory.
                The save writes every pending edit in that library; a
                save that does not reach disk refuses the link. Ignored
                without ``replace``, which removes nothing.
            verify_on_disk: when reload_first ran, ON by default: after
                the link, save the library AGAIN and read the footprint
                back from the SAVED FILE (lib_read_pcblib_file's reader).
                ``persisted`` reports the body count (must be the count
                before, minus the removed, plus one) and the saved bodies
                carrying ``expected_in_file`` (placement, rotation, Dz),
                with their stored heights; a mismatch is ``ok`` false.
                With a layer (``layer``, or the one inherited from the
                replaced body) the matching saved body must be on it
                (``layer_ok``; null when a library-specific layer name
                cannot be compared with the file's canonical one).
                Skipped when the body fails the placement check, or reads
                back on another layer in memory, so a misplaced body is
                not written to disk.
            align: where the body goes in X and Y.
                ``"none"`` (default) puts the STEP's OWN ORIGIN on the
                FOOTPRINT origin, plus (offset_x, offset_y): what Altium's
                library editor does when it places a STEP body, and what
                a KiCad or maker model authored about its footprint
                origin needs. It used to move the body by the offsets
                from where Altium dropped it, which is ABSOLUTE (0, 0)
                on the library board (measured 2026-10-04): on a
                footprint whose origin was (50000, 50000) mils the model
                landed 50,000 mil from its pads.
                ``"center"`` puts the centre of the body's bounding box on
                the footprint origin, plus the offsets.
                ``"pads"`` puts it on the centre of the pads, plus the
                offsets. Those two move from the box read back after the
                body is added, so they work whatever origin the STEP was
                authored about; ``"center"`` suits a centred footprint,
                which an IPC generated one is.
            library_path: optional .PcbLib to focus first. Checked after
                the open, and refused when a different document ends up in
                front, so the body never lands in a library you did not
                name.
            layer: the layer to put the body on, by name as the library
                spells it (e.g. a mechanical layer). Empty leaves Altium's
                default, EXCEPT with ``replace=True``, where the new body
                takes the layer of the body it replaces, so a STEP swapped
                in for an IPC placeholder keeps the placeholder's layer and
                its flip pairing. An unknown name is refused before
                anything is touched. lib_get_footprint_bodies shows each
                body's layer.

        MEASURED LIVE 2026-10-04: the rotation written with
        IPCB_Model.SetState lands and persists (the saved record read
        MODEL.3D.ROTX=90, ROTZ=45). It CANNOT BE READ BACK in memory:
        GetState returned zeros beside that file, because its OUT
        parameters do not come back through DelphiScript, so it is no
        longer called and ``rotation_readback`` says "unavailable"
        instead of reporting a false mismatch. SetState runs only when a
        rotation or offset_z is non-zero, so a plain link runs the
        sequence that has always worked.

        PROOF OF THE PLACEMENT: ``lib_save_library``, then
        ``lib_read_pcblib_file(library_path, footprint_name)``. The body
        there carries ``model.x_mils``/``y_mils`` (MODEL.2D.X/Y, relative
        to the footprint origin: equal to offset_x/offset_y for
        align="none"), ``rot_x/rot_y/rot_z`` and ``dz_mils`` (=
        offset_z), exactly as stored. ``expected_in_file`` in this reply
        lists the values to compare.

        Returns:
            ``success``, ``footprint``, ``model``, ``units`` ("mils"), and:

            ``ok`` AND ``success`` are false (with ``reason``) when
            ``placement_ok`` is false: the body was added in memory but
            does not sit on its footprint. Undo it with
            ``lib_delete_footprint_primitives`` (object_type
            "componentbody", with library_path, so its reload_first
            saves and reopens first and the removal sticks; note that
            save writes the misplaced body to disk before the delete
            removes it), or link again with ``replace=True`` and
            corrected offsets. Also false when ``persisted`` shows the
            saved file does not match, and when the body reads back in
            memory on a different layer from the one asked for (or
            inherited); a library-specific name that cannot be compared
            with a canonical one is not a mismatch.

            ``applied``: what was actually written. ``rotation``
            (SetState returned without raising, a rotation was asked
            for); ``rotation_z`` (kept for older callers); ``z_offset``
            and ``z_offset_mils`` (offset_z written as the model's DZ);
            ``standoff_height`` (always false now: offset_z no longer
            writes it); ``offset_xy`` (a move happened); ``align``;
            ``aligned`` (false when the box or the pads could not be
            read, so nothing was aligned); ``layer`` (a layer was
            written) and ``layer_source`` (``argument``, ``replaced
            body`` or ``default``).

            ``rotation_requested``; ``model_state`` and
            ``rotation_readback_matches`` are null (kept for older
            callers); ``rotation_readback`` is ``{available: false,
            reason, verify_with}``. ``expected_in_file``: what the saved
            ComponentBody record should hold, relative to the footprint
            origin (``model_2d_x_mils``/``model_2d_y_mils`` for
            align="none", null otherwise; ``model_3d_rotx/roty/rotz``;
            ``model_3d_dz_mils``).

            ``placement``: ``{ok, basis, reference ("pads_bbox" or
            "footprint_origin"), gap_mils, limit_mils, problem}``, the
            gap between the body's box and the pads' box in the
            footprint frame (0 = overlapping). Above 1000 mil, or a box
            that cannot be read, is ``placement_ok`` false. ``basis`` is
            ``bbox_readback``, or ``bbox_expected`` when the read box was
            STALE (did not change across the move): then a far expected
            box is still false (the move as computed is wrong), and a
            near one is ``ok``/``placement_ok`` NULL, unverified, not
            failed; the saved file settles it (``persisted``, or
            lib_read_pcblib_file).

            ``replaced``: ``requested``, ``removed_bodies``,
            ``remove_calls``, ``remaining_before_add`` (0 when the
            replace worked), ``board_still_listed`` (replaced bodies the
            LIBRARY BOARD still lists, read-only; null when the
            footprint was not current, so the board held none of its
            primitives) and ``board_removal`` (``{requested}``, or with
            ``remove_from_board`` what the board half found). Through
            the delete route (every replace naming its footprint) also
            ``route`` ("delete_then_link"), ``saved_first`` (reload_first
            ran), ``bodies_before``, ``inherited_layer`` (when the new
            body took the replaced body's layer), ``target_was_current``,
            ``parked_elsewhere`` and ``target_current_during_removal``.
            ``reload_first`` when ``replace`` ran it. ``warning`` when
            the handler's own replace ran (``component_name`` empty).

            ``persisted`` (verify_on_disk): ``{checked, saved, save,
            bodies_before, removed, bodies_after,
            expected_bodies_after, expected_in_file, matching_bodies
            [{model, layer, standoff_height_mils,
            overall_height_mils}], saved_bodies?, layer_wanted?,
            layer_ok?, layer_unverified?, ok}``, read from the SAVED
            file.

            ``frame``: ``origin_x_mils`` / ``origin_y_mils`` (the
            footprint origin in library-board coordinates) and
            ``pads_bbox``, the pads' box in the same frame as the body.

            ``body``: ``layer``, ``standoff_height_mils``,
            ``overall_height_mils`` and ``bbox`` (``x1, y1, x2, y2,
            width, height``, mils from the footprint origin), all read
            back from the placed body.

            ``bbox_expected``: the box the move should have produced
            (the pre-move box plus the move). A consistency check against
            a stale read ONLY, not a placement check: a body 50,000 mil
            from its footprint passed it (2026-10-04). ``placement`` is
            the placement check.
            ``bbox_readback_stale``: true when the read box did not change
            across a move, i.e. a cached BoundingRectangle; trust
            ``bbox_expected`` then, or save and lib_reload_library.
            ``move_mils``: the move applied, ``{dx, dy}``.

            Nothing is saved: lib_save_library(library_path) writes the
            library. lib_get_footprint_bodies lists every body on the
            footprint afterwards.
        """
        numbers = {
            "offset_x": offset_x, "offset_y": offset_y, "offset_z": offset_z,
            "rotation_x": rotation_x, "rotation_y": rotation_y,
            "rotation_z": rotation_z,
        }
        bad = _non_finite(numbers)
        if bad:
            return {"ok": False, "reason": (
                f"not a finite number: {', '.join(bad)}. The handler reads "
                f"each with a parser that turns what it cannot read into 0, "
                f"so the value would arrive as no offset or no rotation "
                f"and be reported as one you did not ask for.")}
        align = (align or "none").strip().lower()
        if align not in _FOOTPRINT_3D_ALIGN:
            return {"ok": False, "reason": (
                f"align must be one of {', '.join(_FOOTPRINT_3D_ALIGN)}; "
                f"got {align!r}.")}
        if remove_from_board and not replace:
            return {"ok": False, "reason": (
                "remove_from_board takes the REPLACED bodies off the "
                "library board, so it needs replace=True; without it "
                "nothing is removed.")}

        model_file = Path(str(model_path))
        if not model_file.is_absolute() and model_file.is_file():
            # Checked HERE, so sent as checked: Altium resolves a relative
            # path against its own working directory, not this server's.
            model_path = str(model_file.resolve())
        params: dict[str, Any] = {
            "component_name": component_name,
            "model_path": model_path,
            "offset_x": float(offset_x),
            "offset_y": float(offset_y),
            "offset_z": float(offset_z),
            "rotation_x": float(rotation_x),
            "rotation_y": float(rotation_y),
            "rotation_z": float(rotation_z),
            "replace": "true" if replace else "false",
            "align": align,
        }
        if library_path:
            params["library_path"] = library_path
        if layer:
            params["layer"] = layer
        if remove_from_board:
            params["remove_from_board"] = "true"
        bridge = get_bridge()
        rec = None
        path = ""
        baseline = None
        baseline_why = None
        fp = str(component_name or "").strip()
        # A named footprint is replaced by delete (parked) then link, with
        # or without reload_first; see _replace_then_link.
        route = bool(replace and fp)
        if replace:
            if not fp:
                path, source = "", (
                    "component_name is empty, so the target is whatever "
                    "footprint is current, and a reopen changes which one "
                    "that is")
            else:
                path, source = await _resolve_library_path(bridge,
                                                           library_path)
            if route and not Path(str(model_path)).is_file():
                # Refused BEFORE the save and before the removal: the
                # handler would refuse an unreadable model only after
                # reload_first had written and reopened the whole library,
                # and after the delete route had removed the old bodies.
                return {"ok": False, "reason": (
                    f"model_path {model_path!r} is not a file. Nothing was "
                    f"saved, reopened, removed or linked")}
            if path:
                params["library_path"] = path
                # READ-ONLY, BEFORE the save, as the deletes do: a footprint
                # that is not in the library must not cost a save and
                # reopen. The library's own spelling then goes to BOTH the
                # delete and the link: the delete matches the name
                # case-insensitively and the link handler exactly, so a
                # case-different name would remove the old bodies and then
                # fail to add the new one.
                precheck, why = await _precheck_footprint(bridge, path, fp)
                if why:
                    return {"ok": False, "reason": why, "precheck": precheck}
                if precheck.get("name"):
                    fp = precheck["name"]
                    params["component_name"] = fp
            if reload_first:
                rec, refusal = await _reload_first(bridge, path,
                                                   "replaced or linked",
                                                   skip_reason=source)
                if refusal is not None:
                    return refusal
                if rec.get("ran") and verify_on_disk:
                    # The saved file right after reload_first's save is the
                    # state BEFORE the replace: the baseline for the count.
                    baseline, baseline_why = _saved_footprint(path, fp)
        if route:
            result = await _replace_then_link(
                bridge, params, path, fp, remove_from_board,
                saved_first=bool(rec is not None and rec.get("ran")))
        else:
            result = await bridge.send_command_async("library.link_3d_model",
                                                     params)
        result = _with_reload_record(result, rec)
        if replace and not route:
            result = _add_warning(result, _HANDLER_REPLACE_WARNING)
        if not isinstance(result, dict) or result.get("ok") is False:
            return result
        if result.get("placement_ok") is False:
            return _mark_failed(result, (
                "the 3D body was added in memory but does not sit on its "
                "footprint: " + str((result.get("placement") or {}).get(
                    "problem") or "placement check failed")))
        want_layer = layer or (result.get("replaced") or {}).get(
            "inherited_layer") or ""
        got_layer = (result.get("body") or {}).get("layer")
        if want_layer and _layer_agrees(want_layer, got_layer) is False:
            return _mark_failed(result, (
                f"the 3D body was added in memory on {got_layer!r}, not on "
                f"{want_layer!r}. Not saved; remove it with "
                f"lib_delete_footprint_primitives (componentbody) or link "
                f"again with replace=True"))
        if rec is not None and rec.get("ran") and verify_on_disk:
            if baseline is None:
                result["persisted"] = {"checked": False, "reason": (
                    f"no baseline from the saved file ({baseline_why}), so "
                    f"nothing could be compared; read it with "
                    f"lib_read_pcblib_file after lib_save_library")}
            else:
                check, entry = await _save_and_read(bridge, path, fp)
                if entry is not None:
                    removed = int((result.get("replaced") or {}).get(
                        "removed_bodies") or 0)
                    check.update(_link_persisted(
                        baseline, entry, removed,
                        result.get("expected_in_file"),
                        layer=want_layer or None))
                result["persisted"] = check
                if not check.get("saved"):
                    return _mark_failed(result, check.get("reason") or
                                        "the verifying save failed")
                if check.get("checked") and not check.get("ok"):
                    return _mark_failed(result, (
                        "the saved file does not hold what the link "
                        "reported: see persisted (expected "
                        f"{check.get('expected_bodies_after')} bodies, "
                        f"found {check.get('bodies_after')}; "
                        f"{len(check.get('matching_bodies') or [])} carry "
                        "the expected placement, rotation and Dz"
                        + (f"; layer_ok {check.get('layer_ok')} for "
                           f"{check.get('layer_wanted')!r}"
                           if "layer_ok" in check else "") + ")"))
        return result

    @mcp.tool()
    async def lib_get_footprint_bodies(
        footprint_name: str = "",
        library_path: str = "",
        model_state: bool = False,
        offset: int = 0,
        limit: int = 250,
    ) -> dict[str, Any]:
        """List the 3D bodies on a PcbLib footprint, or count them per footprint.

        Read-only. The question it answers is the one lib_link_3d_model
        could not: what is actually on this footprint, and where. Until
        now the only body information was a COUNT in the policy dump, so
        telling an IPC generator's extruded placeholder from a linked
        STEP, or seeing where a model landed, meant opening the 3D view.

        With ``footprint_name``: every body on that footprint, each with
        its ``layer``, ``standoff_height_mils``, ``overall_height_mils``
        and ``bbox`` (``x1, y1, x2, y2, width, height`` in mils from the
        footprint origin), plus the pads' box in the same frame
        (``frame.pads_bbox``) so a model sitting off its land is visible
        in the numbers. With ``model_state=True`` each body also carries
        ``model``: ``type`` (``extruded``, ``generic`` = an imported STEP
        or other model file, ``cylinder``, ``sphere``), ``type_id``,
        ``file``, ``planar_rotation``, ``rotation`` (always null) and
        ``rotation_readback`` (``{available: false, reason,
        verify_with}``). The 3D rotation cannot be read in memory:
        IPCB_Model.GetState returned zeros beside a saved ROTX=90 /
        ROTZ=45 (measured 2026-10-04). ``lib_read_pcblib_file`` reads it
        from the saved file.

        With ``footprint_name`` empty: the whole library, one line per
        footprint with ``bodies`` and, with ``model_state=True``, how many
        of each type. Paged by ``offset`` / ``limit``; ``total`` is the
        library size. With ``model_state=True`` it finds every footprint
        still carrying a placeholder: an ``extruded`` count above 0 and
        ``generic`` at 0.

        The single-footprint form makes the footprint current so its frame
        is the one lib_link_3d_model measures in, then puts the previous
        current footprint back. The focused document is restored too.

        Args:
            footprint_name: footprint to list; empty sweeps the library.
            library_path: optional .PcbLib; defaults to the focused one.
                Refused when a different document ends up in front.
            model_state: OFF by default. True also reads the model
                behind each body (type, file, planar rotation), through
                IPCB_Model members (ModelType, FileName, Rotation). They
                ran live once without a fault (2026-10-04); they stay
                opt-in until docs/RELEASE_VERIFICATION.md step 5 has
                passed on the release build, because an unresolved name
                is not catchable and stops the polling loop. Without it
                the reply still carries layer, heights, bbox and the
                pads' box, all read with proven members.
            offset: first footprint of the sweep page.
            limit: footprints per sweep page.

        Returns:
            One footprint: ``{library_path, footprint, units, frame:
            {origin_x_mils, origin_y_mils, made_current, pads_bbox},
            body_count,
            model_state, bodies: [{index, layer, standoff_height_mils,
            overall_height_mils, bbox, model?}]}``.
            Sweep: ``{library_path, total, offset, count, model_state,
            footprints: [{name, bodies, extruded?, generic?, cylinder?,
            sphere?, unknown?}]}``.

            A bbox read after an edit in the same session can be stale
            (BoundingRectangle is cached on texts, and nothing yet says a
            body's is not). Save and lib_reload_library before trusting
            one that disagrees with lib_link_3d_model's
            ``bbox_expected``.
        """
        try:
            offset = max(0, int(offset))
            limit = int(limit)
        except (TypeError, ValueError):
            return {"ok": False,
                    "reason": "offset and limit must be whole numbers"}
        if limit <= 0:
            limit = 250
        params: dict[str, Any] = {
            "footprint_name": footprint_name,
            "library_path": library_path,
            "model_state": "true" if model_state else "false",
            "offset": offset,
            "limit": limit,
        }
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_footprint_bodies", params, timeout=_GEOMETRY_TIMEOUT)

    @mcp.tool()
    async def lib_auto_link_3d_models(
        model_dir: str = "",
        library_path: Optional[str] = None,
        replace: bool = False,
        align: str = "none",
        remove_from_board: bool = False,
        reload_first: bool = True,
        layer: str = "",
        model_map: Optional[dict[str, str]] = None,
        verify_on_disk: bool = True,
    ) -> dict[str, Any]:
        """Batch-link STEP models to footprints, by file name or by a map.

        Enumerates every footprint in the PcbLib and links each one that
        has a model. A model comes from ``model_map`` (footprint name ->
        model file) when the map names the footprint, otherwise from
        ``model_dir``: a .step/.stp file there whose stem matches the
        footprint name (case-insensitive). Builds on lib_link_3d_model,
        with no offsets or rotations: adjust a model that needs it with a
        single lib_link_3d_model call afterwards.

        Args:
            model_dir: Folder containing .step/.stp model files matched by
                stem. May be empty when ``model_map`` is given; when both
                are given, a relative path in the map is resolved against
                it. Every path is made ABSOLUTE here (a relative one
                against this server's working directory, where it was
                checked) before it is sent, because Altium would resolve a
                relative path against its own.
            library_path: Optional .PcbLib to focus first; uses the focused
                document otherwise.
            replace: remove each matched footprint's existing 3D bodies
                first, which swaps a library's IPC generator placeholders
                for real models in one call. See lib_link_3d_model. The
                old bodies are always removed by the delete with the
                footprint PARKED, then the model linked without replace
                (with or without reload_first): the handler's own replace
                removes from the CURRENT footprint, measured 2026-10-05 to
                come back with the next save. Without reload_first nothing
                was saved first, so a failed link's restore is the LAST
                save (the reply says so), and parking without a reload is
                not yet measured live. Also the switch for the
                save-and-verify below: a footprint with no body to replace
                is linked the same way, so ``replace=True`` is how a batch
                onto bare footprints gets its ``persisted`` proof.
            align: passed to every link: ``"none"``, ``"center"`` or
                ``"pads"``. See lib_link_3d_model.
            remove_from_board: with ``replace``, passed to every link.
                Off by default; see lib_link_3d_model.
            reload_first: with ``replace``, ON by default: save the
                library and reopen it ONCE before the first link, so the
                bodies being replaced are loaded from disk (a removed
                primitive of the CURRENT footprint is written back by the
                next save, measured 2026-10-05; see
                lib_delete_footprint_pads), and then replace each through
                the delete-then-link route (old bodies removed with the
                footprint parked). Runs only when at least one footprint
                has a model. Uses ``library_path`` or the focused
                .PcbLib; skipped (``reload_first.skipped``) when neither
                resolves.
            layer: the layer for EVERY linked body, by name as the library
                spells it (the house convention is a mechanical layer such
                as "Mechanical 13"). Passed to every link and it WINS over
                the replaced body's layer, exactly as on
                lib_link_3d_model. Empty: a replaced footprint keeps its
                placeholder's layer, a bare footprint gets Altium's
                default (Mechanical 1). An unknown name is refused by the
                first link, and the batch STOPS there (``stopped``): with
                replace every later link would remove its old bodies and
                then be refused the same way. A body that reads back in
                memory on ANOTHER canonical layer than the one asked for
                (or inherited) is ``failed`` (``LAYER_MISMATCH``,
                ``body_added_in_memory``), and the verifying save checks
                the saved body's layer too (``persisted.layer_ok``).
            model_map: optional ``{footprint name: model file}`` for
                models whose file stem differs from the footprint name or
                that live in per-footprint folders (e.g.
                ``<dir>/<fp>/oriented/<fp>.step``). Footprint names match
                case-insensitively and are sent in the library's spelling.
                EVERY file is checked before anything is saved or linked:
                one that is not a file, or not a .step/.stp, refuses the
                whole call (``missing_models``, each with its
                ``problem``). A name the library does not hold is
                reported in ``unknown_footprints`` (and ``success`` is
                false) while the rest are linked.
            verify_on_disk: when reload_first ran, ON by default: after
                the last link, save the library ONCE and read every linked
                footprint back from the SAVED FILE, as lib_link_3d_model
                does for one: each ``linked`` entry gets ``persisted``
                (body count before - removed + 1, and a saved body
                carrying ``expected_in_file``). A footprint whose saved
                state does not match moves to ``failed``
                (``NOT_PERSISTED``). The verifying save is SKIPPED when
                any link failed, because a failed entry can be a misplaced
                body (the save would write it) or old bodies removed
                before a refused link (lib_reload_library restores them,
                but not after a save, and it also throws away every
                successful link of the batch). Each linked footprint
                whose saved file was NOT read back is counted in
                ``summary.unverified``, and when that is above 0 the
                reply carries ``warning``: ``success`` means "linked in
                memory", not "on disk", for those.

        Returns:
            {"success", "summary": {footprints, matched, linked, failed,
             unmatched_footprints, unused_models, unknown_footprints,
             persisted_ok, persisted_checked, unverified}, "linked":
             [{footprint,
             model, model_source ("map" or "dir"), applied, replaced,
             bbox, body, placement, expected_in_file, persisted?}],
             "failed": [{footprint, model, error, code}],
             "unmatched_footprints": [...], "unused_models": [...],
             "unknown_footprints": [...], "stopped"?, "verify"?,
             "warning"?, "reload_first"?}. A link that Altium refused, or
            whose body
            fails the placement check (``placement_ok`` false: not on its
            footprint), is listed under ``failed`` rather than
            ``linked``. A placement failure carries
            ``body_added_in_memory: true`` and ``undo``: the body IS on
            the footprint in memory and the next save writes it.
            ``verify`` is the batch's verifying save (``{ran, saved?,
            save?, reason?}``). Persistence proof by hand:
            lib_save_library, then lib_read_pcblib_file.
        """
        from ..bridge.exceptions import AltiumNotRunningError, AltiumTimeoutError

        model_dir = str(model_dir or "").strip()
        if not model_dir and not model_map:
            return {"ok": False, "reason": (
                "give model_dir (models matched by file stem) or model_map "
                "({footprint name: model file})")}
        # ABSOLUTE from here on: checked against this server's working
        # directory, so sent as checked (Altium resolves a relative path
        # against its own).
        d = Path(model_dir).resolve() if model_dir else None
        if d is not None and not d.is_dir():
            return {"ok": False, "reason": f"not a directory: {model_dir}"}
        align = (align or "none").strip().lower()
        if align not in _FOOTPRINT_3D_ALIGN:
            return {"ok": False, "reason": (
                f"align must be one of {', '.join(_FOOTPRINT_3D_ALIGN)}; "
                f"got {align!r}.")}
        if remove_from_board and not replace:
            return {"ok": False, "reason": (
                "remove_from_board needs replace=True: it takes the "
                "replaced bodies off the library board.")}
        layer = str(layer or "").strip()

        # model_map, checked IN FULL before the library is touched: a file
        # that is not there must not cost a save and reopen, nor leave the
        # batch half-linked.
        mapped: dict[str, tuple[str, str]] = {}
        if model_map is not None:
            if not isinstance(model_map, dict):
                return {"ok": False, "reason": (
                    "model_map must be a {footprint name: model file} dict")}
            missing: list[dict[str, str]] = []
            for raw_name, raw_path in model_map.items():
                name = str(raw_name or "").strip()
                given = str(raw_path or "").strip()
                if not name:
                    return {"ok": False, "reason": (
                        "model_map has an empty footprint name. Nothing was "
                        "saved, reopened or linked")}
                if name.lower() in mapped:
                    return {"ok": False, "reason": (
                        f"model_map names footprint {name!r} twice (names "
                        f"match case-insensitively). Nothing was saved, "
                        f"reopened or linked")}
                p = Path(given)
                if given and not p.is_absolute() and d is not None:
                    p = d / p
                if given:
                    p = p.resolve()
                problem = ""
                if not given or not p.is_file():
                    problem = "not a file"
                elif p.suffix.lower() not in (".step", ".stp"):
                    problem = "not a .step/.stp file"
                if problem:
                    missing.append({"footprint": name, "model": given,
                                    "resolved": str(p) if given else "",
                                    "problem": problem})
                    continue
                mapped[name.lower()] = (name, str(p))
            if missing:
                return {"ok": False, "missing_models": missing, "reason": (
                    f"{len(missing)} model_map file(s) cannot be linked: "
                    + ", ".join(f"{m['footprint']} -> {m['model']!r} "
                                f"({m['problem']})"
                                for m in missing[:5])
                    + (" ..." if len(missing) > 5 else "")
                    + ". Nothing was saved, reopened or linked")}

        models: dict[str, str] = {}
        if d is not None:
            for f in d.iterdir():
                if f.is_file() and f.suffix.lower() in (".step", ".stp"):
                    models[f.stem.lower()] = str(f)

        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        fp_data = await bridge.send_command_async("library.get_footprints", params)
        footprints = fp_data.get("footprints", []) if isinstance(fp_data, dict) else []
        names = [str(fp.get("name", "")).strip() for fp in footprints
                 if isinstance(fp, dict)]
        held = {n.lower() for n in names}
        unknown = [orig for key, (orig, _p) in mapped.items()
                   if key not in held]

        def model_for(key: str) -> tuple[Optional[str], str]:
            if key in mapped:
                return mapped[key][1], "map"
            if key in models:
                return models[key], "dir"
            return None, ""

        # reload_first only when there is something to replace: a folder
        # that matches no footprint must not cost a save of the library.
        rec = None
        reloaded = False
        path = ""
        matches_any = any(model_for(n.lower())[0] is not None for n in names)
        if replace and reload_first and matches_any:
            path, source = await _resolve_library_path(bridge,
                                                       library_path or "")
            rec, refusal = await _reload_first(bridge, path,
                                               "replaced or linked",
                                               skip_reason=source)
            if refusal is not None:
                return refusal
            reloaded = bool(rec.get("ran"))
            if reloaded and not library_path:
                # Pin every link to the library that was saved and reopened.
                library_path = path
        elif replace and matches_any and not library_path:
            # No reload, but the replace still DELETES: pin the deletes and
            # links to one library rather than whatever has focus after
            # each read.
            path, _source = await _resolve_library_path(bridge, "")
            if path:
                library_path = path

        verify = reloaded and verify_on_disk
        baselines: dict[str, tuple[Optional[dict], Optional[str]]] = {}

        linked: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        # Decided up front, so a batch that stops early still reports every
        # footprint without a model and every model no footprint takes.
        unmatched = [n for n in names if model_for(n.lower())[0] is None]
        matched = [n for n in names if model_for(n.lower())[0] is not None]
        used = {n.lower() for n in matched if model_for(n.lower())[1] == "dir"}
        stopped: Optional[dict[str, Any]] = None
        for fname in matched:
            key = fname.lower()
            model, source_kind = model_for(key)
            if verify:
                # The saved file right after reload_first's save is the
                # state BEFORE this footprint's replace: its baseline.
                baselines[fname] = _saved_footprint(path, fname)
            link_params: dict[str, Any] = {
                "component_name": fname,
                "model_path": model,
                "offset_x": 0, "offset_y": 0, "offset_z": 0,
                "rotation_x": 0, "rotation_y": 0, "rotation_z": 0,
                "replace": "true" if replace else "false",
                "align": align,
            }
            if layer:
                # Explicit layer wins: _replace_then_link inherits the
                # replaced body's layer only when "layer" is absent.
                link_params["layer"] = layer
            if remove_from_board:
                link_params["remove_from_board"] = "true"
            # EVERY link names the library. get_footprints above is a read,
            # and the dispatcher puts the previous focus back after a read,
            # so without this each link resolved against whatever document
            # had focus: NO_PCBLIB on a sheet, or bodies (and, with
            # replace, deletions) in a DIFFERENT PcbLib holding a footprint
            # of the same name. Lib_Link3DModel focuses library_path and
            # refuses when another document ends up in front.
            if library_path:
                link_params["library_path"] = library_path
            base = {"footprint": fname, "model": model,
                    "model_source": source_kind}
            try:
                if replace:
                    # Parked delete, then link, with or without the reload:
                    # the handler's own replace removes from the CURRENT
                    # footprint, measured to come back with the save.
                    res = await _replace_then_link(
                        bridge, link_params, library_path or "", fname,
                        remove_from_board, saved_first=reloaded)
                else:
                    res = await bridge.send_command_async(
                        "library.link_3d_model", link_params,
                    )
            except (AltiumNotRunningError, AltiumTimeoutError) as e:
                # The bridge itself is gone: every later link would fail
                # the same way, so stop and say where it stopped.
                failed.append(dict(base, error=str(e),
                                   code=getattr(e, "code", "")))
                stopped = {"at": fname, "reason": (
                    "the bridge stopped answering; later footprints were "
                    "not attempted")}
                break
            except Exception as e:
                code = getattr(e, "code", "")
                failed.append(dict(base, error=str(e), code=code))
                if code == "UNKNOWN_LAYER":
                    stopped = {"at": fname, "reason": (
                        f"layer {layer!r} is not in this library: every "
                        f"later link would be refused the same way")}
                    break
                continue
            res = res if isinstance(res, dict) else {}
            if res.get("ok") is False and res.get("placement_ok") is not False:
                # The delete route removed the old bodies and the link
                # then failed: said so in the reason, with the restore.
                code = res.get("code", "LINK_FAILED")
                failed.append(dict(base, code=code,
                                   error=str(res.get("reason")),
                                   replaced=res.get("replaced")))
                if code == "UNKNOWN_LAYER":
                    stopped = {"at": fname, "reason": (
                        f"layer {layer!r} is not in this library: with "
                        f"replace every later link would remove its old "
                        f"bodies and then be refused the same way. "
                        + _batch_recovery(fname, reloaded, len(linked)))}
                    break
                continue
            if res.get("placement_ok") is False:
                failed.append(dict(
                    base,
                    code="PLACEMENT_FAR_FROM_FOOTPRINT",
                    error=str((res.get("placement") or {}).get(
                        "problem") or "placement check failed"),
                    placement=res.get("placement"),
                    # Listed as failed, but the body IS on the footprint in
                    # memory and the next save writes it.
                    body_added_in_memory=True,
                    undo=(
                        "the body is in memory and the next save writes "
                        "it: remove it with lib_delete_footprint_primitives"
                        f"(footprint_name={fname!r}, object_type="
                        "'componentbody', library_path=..., confirm=True), "
                        "whose reload_first saves and reopens first so the "
                        "removal sticks (that save writes the misplaced body "
                        "to disk first; the delete then removes it), or "
                        "link again with replace=True and corrected "
                        "offsets"),
                ))
                continue
            body = res.get("body") or {}
            want_layer = layer or (res.get("replaced") or {}).get(
                "inherited_layer") or ""
            if want_layer and _layer_agrees(want_layer,
                                            body.get("layer")) is False:
                failed.append(dict(
                    base, code="LAYER_MISMATCH",
                    error=(f"the body reads back on {body.get('layer')!r}, "
                           f"not on {want_layer!r}"),
                    body={k: body.get(k) for k in (
                        "layer", "standoff_height_mils",
                        "overall_height_mils")},
                    replaced=res.get("replaced"),
                    body_added_in_memory=True,
                    undo=(
                        "the body is in memory and the next save writes "
                        "it: link again with replace=True (and the right "
                        "layer), or remove it with "
                        "lib_delete_footprint_primitives(footprint_name="
                        f"{fname!r}, object_type='componentbody', "
                        "library_path=..., confirm=True)"),
                ))
                continue
            linked.append(dict(
                base,
                layer_wanted=want_layer or None,
                applied=res.get("applied"),
                replaced=res.get("replaced"),
                bbox=body.get("bbox"),
                body={k: body.get(k) for k in (
                    "layer", "standoff_height_mils", "overall_height_mils")},
                placement=res.get("placement"),
                expected_in_file=res.get("expected_in_file"),
            ))

        verify_rec: Optional[dict[str, Any]] = None
        if verify and linked:
            if failed:
                verify_rec = {"ran": False, "reason": (
                    f"{len(failed)} link(s) failed, so the verifying save "
                    f"was skipped: it would write a failed footprint as it "
                    f"stands in memory, bare (old bodies removed, new link "
                    f"refused) or with its misplaced body. "
                    + _batch_recovery(", ".join(
                        repr(f["footprint"]) for f in failed[:5]),
                        True, len(linked)))}
                for row in linked:
                    row["persisted"] = {"checked": False,
                                        "reason": verify_rec["reason"]}
            else:
                save = await _save_library_verified(bridge, path)
                verify_rec = {"ran": True, "saved": bool(save.get("ok")),
                              "save": save, "library_path": path}
                still_linked: list[dict[str, Any]] = []
                for row in linked:
                    fname = row["footprint"]
                    if not save.get("ok"):
                        row["persisted"] = {
                            "checked": False, "saved": False, "reason": (
                                "the verifying save did not reach disk, so "
                                "the link is in memory only: "
                                + str(save.get("reason")))}
                        still_linked.append(row)
                        continue
                    baseline, why = baselines.get(fname, (None, "no baseline"))
                    if baseline is None:
                        row["persisted"] = {"checked": False, "saved": True,
                                            "reason": (
                            f"no baseline from the saved file ({why}); read "
                            f"it with lib_read_pcblib_file")}
                        still_linked.append(row)
                        continue
                    entry, why = _saved_footprint(path, fname)
                    if entry is None:
                        row["persisted"] = {"checked": False, "saved": True,
                                            "reason": why}
                        still_linked.append(row)
                        continue
                    removed = int((row.get("replaced") or {}).get(
                        "removed_bodies") or 0)
                    check = {"checked": True, "saved": True}
                    check.update(_link_persisted(
                        baseline, entry, removed,
                        row.get("expected_in_file"),
                        layer=row.get("layer_wanted")))
                    row["persisted"] = check
                    if check.get("ok"):
                        still_linked.append(row)
                    else:
                        failed.append(dict(
                            row, code="NOT_PERSISTED", error=(
                                "the saved file does not hold what the link "
                                f"reported (expected "
                                f"{check.get('expected_bodies_after')} bodies"
                                f", found {check.get('bodies_after')}; "
                                f"{len(check.get('matching_bodies') or [])} "
                                f"carry the expected placement, rotation and "
                                f"Dz"
                                + (f"; layer_ok {check.get('layer_ok')} for "
                                   f"{check.get('layer_wanted')!r}"
                                   if "layer_ok" in check else "") + ")")))
                linked = still_linked
                if not save.get("ok"):
                    verify_rec["reason"] = str(save.get("reason"))

        unused = [models[k] for k in models if k not in used]
        persisted_rows = [r.get("persisted") for r in linked
                          if isinstance(r.get("persisted"), dict)]
        unverified = [r["footprint"] for r in linked
                      if not (r.get("persisted") or {}).get("checked")]
        out: dict[str, Any] = {
            "success": not failed and not unknown and not (
                verify_rec and verify_rec.get("ran")
                and not verify_rec.get("saved")),
            "summary": {
                "footprints": len(names),
                "matched": len(matched),
                "linked": len(linked),
                "failed": len(failed),
                "unmatched_footprints": len(unmatched),
                "unused_models": len(unused),
                "unknown_footprints": len(unknown),
                "persisted_checked": sum(1 for p in persisted_rows
                                         if p.get("checked")),
                "persisted_ok": sum(1 for p in persisted_rows
                                    if p.get("checked") and p.get("ok")),
                "unverified": len(unverified),
            },
            "linked": linked,
            "failed": failed,
            "unmatched_footprints": unmatched,
            "unused_models": unused,
            "unknown_footprints": unknown,
        }
        if layer:
            out["layer"] = layer
        if unverified:
            out["unverified"] = unverified
            if verify:
                why = ("the saved file was not read back for them (see each "
                       "row's persisted.reason)")
            else:
                why = ("nothing verified them on disk: the save-and-verify "
                       "runs only when replace=True ran reload_first, with "
                       "verify_on_disk")
            out["warning"] = (
                f"{len(unverified)} linked footprint(s) are linked IN MEMORY "
                f"ONLY as far as this reply can prove: {why}. lib_save_library"
                f" then lib_read_pcblib_file settles it.")
        if stopped is not None:
            out["stopped"] = stopped
        if verify_rec is not None:
            out["verify"] = verify_rec
        if rec is not None:
            out["reload_first"] = rec
        return out

    # =========================================================================
    # Library Search and Information
    # =========================================================================

    @mcp.tool()
    async def lib_get_components(
        library_path: Optional[str] = None,
        with_parameters: bool = False,
        with_designator: bool = False,
    ) -> dict[str, Any]:
        """Get all components in a library.

        Default fast path returns only the metadata that the
        ``ILibCompInfoReader`` exposes directly: name, alias_name,
        part_count, description. That path scales linearly with file IO
        and finishes in well under a second on typical libraries.

        Setting ``with_parameters=True`` adds each component's full
        parameter dict (Manufacturer, Value, Footprint, etc.) to the
        result. That branch calls ``GetState_SchComponentByLibRef`` once
        per symbol and iterates parameters, which is O(N) in the live
        SchLib document and is what makes the call slow on libraries
        with many hundreds of components. Use it when you need the
        parameters; for a single symbol's parameters, prefer
        ``lib_get_component_details``.

        Setting ``with_designator=True`` adds each component's DEFAULT
        designator (``Component.Designator.Text``, e.g. ``"U?"`` / ``"R?"``
        / ``"IC?"``). The CompInfoReader fast path does NOT expose the
        designator, so this also loads each live symbol (same cost driver
        as with_parameters) but skips parameter iteration, keeping the
        payload small. Intended for library-wide designator-consistency
        audits.

        Args:
            library_path: Path to library (uses active library if not specified)
            with_parameters: If True, include each component's parameter
                dict (slow on large libraries). Default False.
            with_designator: If True, include each component's default
                designator string (slow on large libraries; smaller
                payload than with_parameters). Default False.

        ``part_count`` HERE IS NOT TRUSTWORTHY. It comes from the
        CompInfoReader, and MEASURED on AD26 it reported 2 for a symbol
        created single-part whose every pin carries OwnerPartId 1, while
        ``lib_get_component_details`` reported 1 for an identically
        created symbol. The two readers disagree and only the
        discrepancy is established, not a conversion between them, so
        no correction is applied here rather than guess one. Use
        ``lib_get_component_details`` when the part count matters.

        Returns:
            Dictionary with ``count`` and ``components`` list. Each
            component carries index, name, alias_name, part_count,
            description, (only when with_parameters is True) parameters,
            and (only when with_designator is True) designator.

            ``index`` is a stable addressing key (position in library
            order). Pass it as ``component_index`` to lib_get_component_details
            / lib_delete_component / lib_rename_component to reach a
            component whose ``name`` contains bytes you cannot reproduce
            (an embedded double-quote, or a control char from a broken
            import), the case where matching by name fails.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if library_path:
            params["library_path"] = library_path
        if with_parameters:
            params["with_parameters"] = "true"
        if with_designator:
            params["with_designator"] = "true"
        result = await bridge.send_command_async("library.get_components", params)
        if isinstance(result, dict):
            return tag_response(
                result, components=result, context="lib_get_components"
            )
        return result or {}

    @mcp.tool()
    async def lib_search(
        query: str,
        search_type: str = "all",
        library_path: Optional[str] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """Search open SchLib documents for components.

        Case-insensitive substring match. Walks every .SchLib that is
        a member of any open project, plus every standalone .SchLib in
        the workspace's free-documents area. Each library is read via
        ``CreateLibCompInfoReader`` so the search is fast even with
        many libraries open: it only loads symbols when ``search_type``
        is ``"parameters"``.

        IT READS THE FILE ON DISK, NOT THE EDITOR. A component created
        in this session and not yet saved WILL NOT BE FOUND, and the
        reply is an ordinary empty result with nothing to say why.
        MEASURED: a symbol that ``lib_get_component_details`` returned
        in full was absent here until a save. Save first, or use
        ``lib_get_component_details`` / ``lib_get_pin_list``, which read
        the live document.

        DATASHEET DISCIPLINE: Matches carry `_datasheet_guidance`.
        Before recommending any matched part as a replacement or
        answer, fetch its datasheet (WebSearch + WebFetch). Do not
        recommend based on symbol metadata alone.

        Args:
            query: Substring to match against component name / alias /
                description (case-insensitive).
            search_type: ``"all"`` (default, matches name / alias /
                description), ``"name"``, ``"description"``, or
                ``"parameters"`` (slow, also walks each candidate's
                parameter dict via the live symbol).
            library_path: Optional path to a single .SchLib to restrict
                the search to. When omitted, searches every open
                library.
            limit: Cap on returned matches (default 100).

        Returns:
            Dict with ``query``, ``search_type``, ``count``, ``limit``,
            ``truncated`` (True when count == limit), and ``results``,
            a list of {name, alias_name, description, library_path,
            part_count} per match, plus `_datasheet_guidance` +
            `_datasheet_parts`.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "query": query,
            "search_type": search_type,
            "limit": str(limit),
        }
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async("library.search", params)
        if isinstance(result, list):
            result = {"results": result}
        if isinstance(result, dict):
            synthetic = {"components": (
                result.get("results") or result.get("components") or []
            )}
            return tag_response(
                result, components=synthetic, context="lib_search"
            )
        return result

    @mcp.tool()
    async def lib_get_component_details(
        component_name: str = "",
        library_path: Optional[str] = None,
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Get full inspection of one library component in a single call.

        Returns metadata, every pin, every parameter (as a flat dict),
        AND visual-style records for the designator, comment, pins,
        and each parameter (font_id, color, is_hidden, x, y,
        orientation, justification). The integer ``font_id`` can be
        expanded to {name, size, bold, italic} via ``obj_get_font_spec``
        when style detail is needed; the round-trip default keeps it
        compact.

        If ``library_path`` is provided and isn't already focused, the
        library is opened (focus changes), so the next ``lib_*`` call
        operates on it without an explicit open. Saves are deferred,
        opening doesn't write anything to disk.

        DATASHEET DISCIPLINE: This response is the highest-density
        device-fact surface in the library API (pins, parameters,
        Manufacturer/MPN). Treat every value as a hint to find the
        manufacturer datasheet, not as ground truth. The response
        carries `_datasheet_guidance` and `_datasheet_parts`, fetch
        the PDF and cite a section/page before stating any pin or
        rating.

        Args:
            component_name: Component LibRef as it appears in the .SchLib.
            library_path: Optional .SchLib full path. When omitted the
                currently focused library is used.
            component_index: Alternative to component_name. The integer
                `index` from lib_get_components. Use this to reach a
                component whose LibReference carries bytes you cannot
                reproduce (an embedded double-quote, or a control char
                left by a broken import) -- the name never has to round
                trip through the caller. Provide exactly one of
                component_name / component_index.

        Returns:
            Dict with:
              - name, library_path, description, alias_name,
                part_count, pin_count
              - component_kind (standard, mechanical, graphical,
                net_tie_bom, net_tie_no_bom, standard_no_bom, jumper) and
                component_kind_ordinal, the raw value Altium holds.
              - designator: {text, font_id, color, is_hidden, x, y,
                orientation, justification} - the on-canvas designator
                label (NOT just the prefix string).
              - comment: {text, font_id, color, is_hidden, x, y,
                orientation, justification} - the on-canvas comment /
                value label.
              - pins: list of {designator, name, electrical_type, x, y,
                orientation, hidden, label_hidden}. Pin font / color
                are not exposed by the Altium SDK on ISch_Pin and
                therefore not surfaced here.
              - parameters: flat dict of name -> value (cheap lookups).
              - parameter_styles: list of {name, value, style:{font_id,
                color, is_hidden, x, y, orientation, justification}}
                in the same order parameters appear on the symbol.
              - models: list of the component's implementations (footprint
                / SPICE / 3D links), each {model_name, model_type,
                is_current, datafile_links:[{entity_name, file_kind,
                location}]}. datafile_links is the model's source (the
                library it resolves from); empty when the footprint binds
                by name only. Empty list when the part carries no models.
              - `_datasheet_guidance` + `_datasheet_parts`.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.get_component_details", params,
        )
        if isinstance(result, dict):
            mfr = ""
            mpn = ""
            params = result.get("parameters") or {}
            if isinstance(params, dict):
                mfr = str(
                    params.get("Manufacturer")
                    or params.get("manufacturer")
                    or ""
                ).strip()
                mpn = str(
                    params.get("ManufacturerPartNumber")
                    or params.get("Manufacturer Part Number")
                    or params.get("Partnumber")
                    or params.get("PartNumber")
                    or params.get("Comment")
                    or ""
                ).strip()
            if not mpn:
                mpn = str(result.get("name") or component_name or "").strip()
            explicit = (
                [{"manufacturer": mfr, "part_number": mpn, "designators": ""}]
                if mpn
                else []
            )
            return tag_response(
                result,
                explicit_parts=explicit,
                context="lib_get_component_details",
            )
        return result

    @mcp.tool()
    async def lib_audit_styles(
        library_path: Optional[str] = None,
        with_comment: bool = False,
        with_parameters: bool = False,
        with_pins: bool = False,
        expect_designator_font_id: Optional[int] = None,
        expect_designator_color: Optional[int] = None,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Bulk visual-style audit across every component in a library.

        Walks the focused .SchLib (or one specified by ``library_path``)
        component-by-component and emits each component's designator
        style record. Comment / parameter_styles / pins are opt-in via
        the ``with_*`` flags so the default response stays compact:
        designator alone is ~120 bytes per component, so a 2000-symbol
        library is ~240 KB without filters.

        Filter mode: pass ``expect_designator_font_id`` and/or
        ``expect_designator_color`` and the response only contains
        components whose designator does NOT match the expected style.
        That makes the audit case (find every symbol that doesn't use
        Times New Roman 10pt navy) a single round-trip with bounded
        output.

        ``timeout`` overrides the bridge default. A 2000-symbol audit
        with no opt-in flags finishes well under the 10s default; pass
        a larger value if you flip on ``with_parameters`` and the lib
        has heavy parameter dicts.

        Args:
            library_path: .SchLib path. Defaults to focused doc.
            with_comment: Include comment style record per component.
            with_parameters: Include parameter_styles array per component.
            with_pins: Include pins array per component.
            expect_designator_font_id: Filter; trim components where
                designator.font_id equals this value.
            expect_designator_color: Filter; trim components where
                designator.color equals this BGR int (e.g. 8388608 for
                navy / 0x000080 in BGR-packed form).
            limit: Cap on emitted entries. Default 5000.
            timeout: Per-call bridge poll timeout override (seconds).

        Returns:
            Dict with library_path, count (emitted), mismatch_count
            (subset that failed the filter), limit, truncated,
            filter_applied, and components: list of
            {name, designator:{...}, mismatched, comment?:{...},
             pins?:[...], parameter_styles?:[...]}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if library_path:
            params["library_path"] = library_path
        if with_comment:
            params["with_comment"] = "true"
        if with_parameters:
            params["with_parameters"] = "true"
        if with_pins:
            params["with_pins"] = "true"
        if expect_designator_font_id is not None:
            params["expect_designator_font_id"] = str(expect_designator_font_id)
        if expect_designator_color is not None:
            params["expect_designator_color"] = str(expect_designator_color)
        result = await bridge.send_command_async(
            "library.audit_styles", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_set_label_formats(
        ops: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        only_mismatched: bool = True,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Multi-target label-style writer for SchLib symbols.

        Applies SEVERAL label target/style ops to SchLib symbols in
        one IPC round-trip (there is no singular variant). The library
        is opened once and walked once; every op is applied to each
        component in turn. Five sequential per-target writes would
        collapse into one trip plus one library walk -- which is
        the dominant cost on large libraries (each individual call
        runs an IPC, a workspace lookup, a doc-focus check and a
        CompInfoReader walk).

        Each ``op`` is a dict describing one label target -- no field
        has a built-in default, only the ones you supply get written:

            {"target": "designator",         # required
             "font_id": <int>,               # optional
             "color":   <int>,               # optional, BGR-packed
             "is_hidden": False,             # optional
             "orientation": <int>,           # optional, 0/90/180/270
             "justification": <int>}         # optional

        At least one style field must be set per op. Targets are
        ``"designator"``, ``"comment"``, or ``"parameter:<Name>"``.
        Use ``obj_get_font_id`` / ``obj_get_font_spec`` to resolve the font_id
        for {name, size, bold, italic} from the active library's font
        table; that keeps the call neutral to any particular library's
        style choices.

        Args:
            ops: List of per-target style ops. Must be non-empty.
                Targets may not contain the wire separators ``;``
                or ``~~``.
            component_name: When set, applies only to that one
                component. Omit for bulk-walk across the library.
            library_path: .SchLib path. Defaults to focused doc.
            only_mismatched: When True (default) skip labels
                already matching the target style. Applies
                globally to every op.
            limit: Cap on processed components in bulk mode.
            timeout: Per-call bridge poll timeout override.

        Returns:
            Dict with library_path, scope ("single"|"bulk"),
            total, limit, truncated, and an ``ops`` array each
            with target, modified, already_compliant,
            missing_target, failed.
        """
        if not ops:
            return {"ok": False,
                    "reason": "ops must be a non-empty list of style ops"}
        encoded_ops: list[str] = []
        for i, op in enumerate(ops):
            if not isinstance(op, dict):
                return {"ok": False,
                        "reason": f"ops[{i}] must be a dict of style keys"}
            target = op.get("target", "designator")
            if (not isinstance(target, str) or ";" in target
                    or "~~" in target):
                return {"ok": False, "reason":
                        f"ops[{i}].target must be a string without "
                        "';' or '~~'"}
            parts = [f"target={target}"]
            style_set = False
            # Whole numbers and booleans by the house guards: int() raised
            # a traceback on junk and truncated 1.7, and truthiness read
            # is_hidden "false" as true, hiding every label it touched.
            for key in ("font_id", "color"):
                if op.get(key) is not None:
                    value = whole_number(op[key])
                    if value is None:
                        return {"ok": False, "reason":
                                f"ops[{i}].{key} must be a whole number"}
                    parts.append(f"{key}={value}")
                    style_set = True
            if op.get("is_hidden") is not None:
                hidden = wire_bool(op["is_hidden"])
                if hidden is None:
                    return {"ok": False, "reason":
                            f"ops[{i}].is_hidden must be true or false"}
                parts.append(f"is_hidden={'true' if hidden else 'false'}")
                style_set = True
            for key in ("orientation", "justification"):
                if op.get(key) is not None:
                    value = whole_number(op[key])
                    if value is None:
                        return {"ok": False, "reason":
                                f"ops[{i}].{key} must be a whole number"}
                    parts.append(f"{key}={value}")
                    style_set = True
            if not style_set:
                return {"ok": False, "reason":
                        f"ops[{i}] must set at least one of font_id / "
                        "color / is_hidden / orientation / justification"}
            encoded_ops.append(";".join(parts))
        bridge = get_bridge()
        params: dict[str, Any] = {
            "ops": join_ops(encoded_ops),
            "limit": str(limit),
        }
        if component_name:
            params["component_name"] = component_name
        if library_path:
            params["library_path"] = library_path
        if not only_mismatched:
            params["only_mismatched"] = "false"
        result = await bridge.send_command_async(
            "library.set_label_formats", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_set_mech_layers(
        layers: list[dict[str, Any]],
        library_path: Optional[str] = None,
        tidy_pairs: bool = False,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Name, enable and kind the mechanical layers of one library.

        WHY THIS EXISTS RATHER THAN THE pcb_* LAYER TOOLS. Those act on
        whichever board is current, and pointing them at a particular
        library depends on the focus actually moving. When it does not
        they operate on the previously focused library while reporting
        success: a sweep over twenty one libraries returned twenty one
        identical answers because every call had re-read the same file.

        This takes the library by PATH and refuses unless the document
        that ended up focused is the one asked for. Acting on the wrong
        library is worse than not acting, because it looks like it
        worked.

        Every change is READ BACK. A layer whose name, enable or kind did
        not take is reported against that layer rather than folded into
        an overall pass.

        PAIRED KINDS. Any kind ending in Top or Bottom is held by the
        layer PAIR rather than by either layer, under a shorter name with
        the side dropped, so "Courtyard Top" is stored as the pair kind
        "Courtyard". Assigning one therefore needs BOTH sides in the same
        call: give the partner kind to another layer in ``layers`` and
        the two are joined and the pair given the kind. Single kinds such
        as "Fab Notes" need no partner.

        Pair with ``lib_run_across`` to apply the same layer scheme to
        many libraries in a single call.

        Args:
            layers: One dict per layer. Keys: ``layer`` (required, e.g.
                "Mechanical13"), and any of ``name``, ``enabled``
                (bool), ``kind`` (e.g. "Courtyard Top"). A layer with
                none of the three is reported as nothing asked for.
            library_path: The library to edit. Defaults to the focused
                document, which is only safe for a single library.
            tidy_pairs: Remove layer pairs no kind justifies. Altium
                never drops a pair when a kind moves to other layers, so
                a library reworked more than once accumulates pairs the
                Layer Stack Manager still shows. A pair survives the
                tidy only when its two layers carry kinds that are each
                other's opposite side. Off by default, because it
                REMOVES pairs and a caller renaming one layer should not
                have the stack rearranged underneath.
            timeout: Seconds.

        Returns:
            Dict with ``library``, ``layers`` (each: layer, changed,
            problem), ``changed``, ``failed``, ``kinds_displaced``,
            ``pairs_removed``, ``pairs_tidied`` and ``pairs_scanned_to``.
        """
        encoded = _encode_layer_ops(layers)
        if isinstance(encoded, dict):
            return encoded

        params: dict[str, Any] = {"layers": encoded}
        if library_path:
            params["library_path"] = library_path
        if tidy_pairs:
            params["tidy_pairs"] = "true"

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_mech_layers", params, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_run_across(
        action: str,
        libraries: list[str],
        params: Optional[dict[str, Any]] = None,
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """Run one library command against several libraries in one call.

        Sweeping libraries one at a time costs a round trip and a turn of
        orchestration per library. This sends the whole sweep as a single
        request and loops inside Altium.

        WHAT THIS DOES NOT SAVE. Opening and saving each library, which is
        the larger cost and is unavoidable. The saving is the round trips
        and the waiting between them, which is what makes a twenty
        library sweep tedious rather than slow.

        WHICH ACTIONS WORK. Those that accept a ``library_path``, which is
        most of the read and batch-edit commands. The footprint EDITING
        commands (``create_footprint``, ``add_footprint_pad``,
        ``link_3d_model`` and the rest) act on whichever library is open
        and cannot be swept, because focusing the document is the work.

        FAILURE IS PER LIBRARY. One library that will not open does not
        abandon the rest, and the reply reports each library separately
        rather than as a single flag. "17 of 20 worked" collapses into
        either a false clean or a false failure the moment it becomes one
        boolean, and the caller cannot tell which library to fix.

        Args:
            action: Library command without its namespace, e.g.
                "get_components", "batch_rename", "audit_styles".
            libraries: Full paths. Passed to Altium separated by "|",
                which cannot occur in a Windows path.
            params: Parameters shared by every library. Any
                ``library_path`` here is overridden per library.
            timeout: Seconds for the whole sweep, not per library.

        Returns:
            Dict with ``results`` (each: library, success, data, error),
            ``succeeded``, ``failed`` and ``libraries``.
        """
        paths = [str(p).strip() for p in (libraries or []) if str(p).strip()]
        if not paths:
            return {"success": False,
                    "error": ("libraries is empty, so nothing would run. A "
                              "sweep over no libraries would report a clean "
                              "pass having done nothing")}
        if not str(action or "").strip():
            return {"success": False, "error": "action is required"}
        bad = [p for p in paths if "|" in p]
        if bad:
            return {"success": False,
                    "error": (f"a library path contains the '|' separator, "
                              f"so the list cannot be split safely: {bad}")}

        merged: dict[str, Any] = dict(params or {})
        merged.pop("library_path", None)

        # A `layers` LIST has to be encoded the same way
        # lib_set_mech_layers encodes it. Passed through raw it arrives
        # as a JSON array, the handler parses no operations from it, and
        # the sweep reports a success per library having changed
        # nothing. Anything already a string is left alone, so a caller
        # who encoded it themselves is not re-encoded.
        if isinstance(merged.get("layers"), (list, tuple)):
            encoded = _encode_layer_ops(merged["layers"])
            if isinstance(encoded, dict):
                return encoded
            merged["layers"] = encoded

        # Every value crossing the bridge is a JSON string field. A
        # nested object or list in any other parameter would arrive the
        # same way `layers` did, so it is refused rather than silently
        # flattened into something the handler cannot read.
        for key, value in merged.items():
            if isinstance(value, (list, tuple, dict)):
                return {"success": False,
                        "error": (f"params[{key!r}] is a "
                                  f"{type(value).__name__}, which the "
                                  f"handler receives as raw JSON and cannot "
                                  f"parse. Pass it in the encoded form that "
                                  f"action expects.")}

        merged["action"] = str(action).strip()
        merged["libraries"] = "|".join(paths)

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.run_across", merged, timeout=timeout,
        )
        return result or {}

    @mcp.tool()
    async def lib_batch_set_params(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch set parameters on library components.

        Each assignment sets one parameter on one component.
        If the parameter exists it is updated; if not it is created.

        Two ``param_name`` values are SPECIAL-CASED to component
        properties rather than parameters:
          - ``"Description"`` sets ``Component.ComponentDescription``.
          - ``"Designator"`` sets the component's DEFAULT designator
            (``Component.Designator.Text``, e.g. ``"U?"`` / ``"R?"``).
            Use this to normalize library designator defaults in bulk
            (pairs with ``lib_get_components(with_designator=True)``).

        Args:
            assignments: List of dicts with keys:
                - component_name: Name of the component in the library
                - param_name: Parameter name (e.g., "Partnumber", "Manufacturer"),
                  or the special "Description" / "Designator" property keys
                - param_value: Value to set
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of updated, created, and failed assignments
        """
        if not assignments:
            return {"ok": False, "reason":
                    "assignments must be a non-empty list of "
                    "component_name/param_name/param_value dicts"}
        # Validate keys and values BEFORE touching the workspace, so a
        # bad call leaves no half-written batch file behind.
        required_keys = {"component_name", "param_name", "param_value"}
        encoding = _batch_encoding()
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                return {"ok": False, "reason":
                        f"assignment {i} is missing required keys: "
                        f"{', '.join(sorted(missing))}"}
            for key in required_keys:
                if "|" in str(a[key]):
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' value contains the "
                            "pipe character '|' which would corrupt the "
                            "batch file"}
                try:
                    str(a[key]).encode(encoding)
                except UnicodeEncodeError:
                    bad = _first_unencodable(str(a[key]), encoding)
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' contains "
                            f"{bad!r}, which the Altium-side batch "
                            f"reader's codepage ({encoding}) cannot "
                            "represent; use a plain-text equivalent"}

        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_params.txt"

        # Windows would translate the newline into CR LF here. The
        # Pascal reader splits on the newline and leaves the carriage
        # return attached to the LAST field on the line, so a rename
        # wrote a LibReference ending in CR and a parameter got a CR
        # in its value. The Pascal side trims as well; both, because
        # either alone leaves the other half of the contract unstated.
        with open(batch_path, "w", encoding=encoding, newline="") as f:
            for a in assignments:
                f.write(f"{a['component_name']}|{a['param_name']}|{a['param_value']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_set_params", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_batch_rename(
        assignments: list[dict[str, str]],
        library_path: Optional[str] = None,
    ) -> dict[str, Any]:
        """Batch rename components in a schematic library.

        Each assignment renames one component from old_name to new_name.

        Args:
            assignments: List of dicts with keys:
                - old_name: Current name of the component in the library
                - new_name: New name for the component
            library_path: Path to library (uses active library if not specified)

        Returns:
            Dictionary with counts of renamed and failed assignments
        """
        if not assignments:
            return {"ok": False, "reason":
                    "assignments must be a non-empty list of "
                    "old_name/new_name dicts"}
        # Validate keys and values BEFORE touching the workspace, so a
        # bad call leaves no half-written batch file behind.
        required_keys = {"old_name", "new_name"}
        encoding = _batch_encoding()
        for i, a in enumerate(assignments):
            missing = required_keys - set(a.keys())
            if missing:
                return {"ok": False, "reason":
                        f"assignment {i} is missing required keys: "
                        f"{', '.join(sorted(missing))}"}
            for key in required_keys:
                if "|" in str(a[key]):
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' value contains the "
                            "pipe character '|' which would corrupt the "
                            "batch file"}
                try:
                    str(a[key]).encode(encoding)
                except UnicodeEncodeError:
                    bad = _first_unencodable(str(a[key]), encoding)
                    return {"ok": False, "reason":
                            f"assignment {i}: '{key}' contains "
                            f"{bad!r}, which the Altium-side batch "
                            f"reader's codepage ({encoding}) cannot "
                            "represent; use a plain-text equivalent"}

        config = get_config()
        config.ensure_workspace()
        batch_path = config.workspace_dir / "batch_rename.txt"

        # Windows would translate the newline into CR LF here. The
        # Pascal reader splits on the newline and leaves the carriage
        # return attached to the LAST field on the line, so a rename
        # wrote a LibReference ending in CR and a parameter got a CR
        # in its value. The Pascal side trims as well; both, because
        # either alone leaves the other half of the contract unstated.
        with open(batch_path, "w", encoding=encoding, newline="") as f:
            for a in assignments:
                f.write(f"{a['old_name']}|{a['new_name']}\n")

        bridge = get_bridge()
        params = {"batch_file": str(batch_path)}
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.batch_rename", params, timeout=120.0
        )
        return result

    @mcp.tool()
    async def lib_diff_libraries(
        library_a: str,
        library_b: str,
    ) -> dict[str, Any]:
        """Compare two schematic libraries and report differences.

        Returns which components are only in library A, only in B, or shared.

        Args:
            library_a: Full path to the first SchLib file
            library_b: Full path to the second SchLib file

        Returns:
            Dictionary with only_in_a, only_in_b, common arrays,
            and count_a, count_b, only_a, only_b, shared counts
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.diff_libraries",
            {"library_a": library_a, "library_b": library_b},
            timeout=60.0,
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_arc(
        x_center: int,
        y_center: int,
        radius: int,
        start_angle: float = 0,
        end_angle: float = 360,
        width: int = 1,
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add an arc to the current library symbol.

        Args:
            x_center: Center X coordinate in mils
            y_center: Center Y coordinate in mils
            radius: Arc radius in mils
            start_angle: Start angle in degrees (0 = right, 90 = up)
            end_angle: End angle in degrees
            width: Line width (0=zero, 1=small, 2=medium, 3=large)
            grid: snap grid (mils); arcs are glyph detail and don't need
                the 100-mil pin grid (rule 15), so the default is a finer
                25 (e.g. inductor coils). Pass 100 for coarse, 0/1 for none.

        Returns:
            Dictionary confirming arc addition
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_arc",
            {
                "x_center": _snap_grid(x_center, grid),
                "y_center": _snap_grid(y_center, grid),
                "radius": _snap_grid(radius, grid),
                "start_angle": start_angle,
                "end_angle": end_angle,
                "width": width,
            },
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_polygon(
        vertices: str,
        grid: int = 25,
    ) -> dict[str, Any]:
        """Add a polygon (filled shape) to the current library symbol.

        Args:
            vertices: Comma-separated x,y coordinate pairs in mils.
                Example: "0,0,100,0,100,100,0,100" creates a square with
                vertices at (0,0), (100,0), (100,100), (0,100).
                Minimum 3 vertices (6 values) required.
            grid: snap grid (mils) for the vertices. A filled polygon is
                glyph detail (e.g. a diode triangle) and doesn't need the
                100-mil pin grid (rule 15), so the default is a finer 25;
                pass 100 for coarse or 0/1 to disable snapping.

        Returns:
            Dictionary confirming polygon addition with vertex count
        """
        # Snap each (x, y) vertex pair to the chosen glyph grid (default 25;
        # finer than the 100-mil pin grid per rule 15) so triangles/chevrons
        # keep their shape instead of collapsing.
        try:
            coords = [int(v.strip()) for v in vertices.split(",") if v.strip()]
            if len(coords) >= 6 and len(coords) % 2 == 0:
                snapped = [_snap_grid(c, grid) for c in coords]
                vertices = ",".join(str(c) for c in snapped)
        except (ValueError, AttributeError):
            # Pass through; bridge will reject malformed vertices itself.
            pass
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_polygon",
            {"vertices": vertices},
        )
        return result

    @mcp.tool()
    async def lib_set_component_description(
        component_name: str,
        description: str,
    ) -> dict[str, Any]:
        """Set the description field on a library component.

        Args:
            component_name: Name of the component in the active library
            description: New description text

        Returns:
            Dictionary confirming the description was set
        """
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_component_description",
            {"component_name": component_name, "description": description},
        )
        return result

    @mcp.tool()
    async def lib_set_component_kind(
        component_name: str,
        kind: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Set a LIBRARY symbol's Component Type (net tie, graphical, jumper).

        The library twin of sch_set_net_tie, which changes one PLACED part
        on the active sheet. Setting the type on the symbol means every
        new placement gets it; existing placements follow on the next
        Update From Libraries.

        kind takes Altium's own words, case and punctuation free:
        standard, mechanical, graphical, net_tie_bom ("Net Tie (In BOM)"),
        net_tie_no_bom ("Net Tie", the no-BOM kind), standard_no_bom
        ("Standard (No BOM)"), jumper.

        The two net-tie kinds are written by the constants sch_set_net_tie
        already uses. The other five are written by their position in
        Altium's enum, checked at run time against the two named ones, and
        the value is read back: KIND_NOT_APPLIED means Altium holds
        something else, ENUM_LAYOUT_MISMATCH means this build numbers the
        enum differently and nothing was written. standard_no_bom and
        jumper rest on that ordering alone until verified once in the
        Properties panel (see RELEASE_VERIFICATION.md).

        Args:
            component_name: the component's LibReference.
            kind: the Component Type, as above.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"component", "kind_requested", "kind_before", "kind_after",
             "ordinal_after"}. The edit is in memory;
            lib_save_library(library_path) writes it.
            lib_get_component_details reports component_kind too.
        """
        canonical = canonical_component_kind(kind)
        if not str(component_name).strip():
            return {"ok": False, "reason": "component_name is required"}
        if canonical is None:
            return {"ok": False,
                    "reason": (f"unknown component kind {kind!r}; use one of "
                               + ", ".join(sorted(set(_COMPONENT_KINDS.values()))))}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_component_kind",
            {
                "component_name": str(component_name).strip(),
                "kind": canonical,
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_get_pin_list(
        component_name: str = "",
        output_path: str = "",
        inline_limit: int = 150,
    ) -> dict[str, Any]:
        """Get all pins of a library component.

        NAME THE COMPONENT. Without ``component_name`` this reads
        whatever the SchLib editor currently has selected, so the answer
        depends on editor state a caller cannot see, and any tool that
        moves the selection between calls changes what this returns.
        Passing the name also avoids disturbing the selection, which is
        what made exporting one symbol affect the next call.

        DATASHEET DISCIPLINE: Pin name + electrical_type from the
        symbol can be wrong, especially on libraries that have been
        edited by hand or imported from third-party sources. Before
        relying on a pin's function for any decision, fetch the
        manufacturer datasheet and verify against its pin-description
        table. The response carries `_datasheet_guidance` +
        `_datasheet_parts`.

        BIG SYMBOLS SPILL TO A FILE ON PURPOSE. A 699-pin module returns
        more than a conversation can hold, and which half survives is
        then decided by whichever client happens to be reading. Above
        `inline_limit` pins the full array is written as JSON and the
        reply carries `pins_path` and a summary instead. That is the
        better artefact anyway: the file can be diffed against the
        datasheet's pin table by script, field by field, without any of
        it passing through the conversation.

        Args:
            component_name: library reference of the symbol to read.
                Empty falls back to the editor's current component.
            output_path: write the full pin array here as JSON and
                return the summary. Forces the file path regardless of
                size.
            inline_limit: pin count above which the array is written to
                a file instead of returned inline. 0 always returns
                inline, which is what you want only for a small symbol.

        Returns:
            Dictionary with "count", "component" name, and "pins" array.
            Each pin has: designator, name, electrical_type, x, y,
            orientation, hidden. Plus `_datasheet_guidance` +
            `_datasheet_parts`.

            When spilled: "pins_path" plus "summary" giving pins per
            part and the electrical-type distribution, and no "pins".
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if str(component_name).strip():
            params["component_name"] = component_name
        result = await bridge.send_command_async(
            "library.get_pin_list", params
        )
        if isinstance(result, dict):
            comp = str(result.get("component") or "").strip()
            pins = result.get("pins")
            if isinstance(pins, list) and (
                output_path or (inline_limit and len(pins) > inline_limit)
            ):
                result = _spill_pin_list(result, pins, comp, output_path)
            explicit = (
                [{"manufacturer": "", "part_number": comp, "designators": ""}]
                if comp
                else []
            )
            return tag_response(
                result, explicit_parts=explicit, context="lib_get_pin_list"
            )
        return result

    @mcp.tool()
    async def lib_set_pin_owner_part(
        pin_designators: str,
        owner_part_id: int,
        component_name: str = "",
    ) -> dict[str, Any]:
        """Reassign pins of a multi-part symbol to a sub-part, or to Part Zero.

        ``owner_part_id=0`` is Altium's Part Zero: the pin belongs to
        the package as a whole instead of to one sub-part. That is the
        documented placement for a multi-part component's supply pins.
        A sub-part-owned supply pin is redrawn at every instance origin,
        and where the gate pitch is only twice the pin length those
        copies land on each other and the netlist merges the two rails.

        Changes the library only. Placed instances pick it up on the
        next Update From Libraries, which produces an ECO.

        Args:
            pin_designators: comma-separated pin numbers, e.g. "3,12".
            owner_part_id: 0 for Part Zero, or 1..part_count.
            component_name: library reference of the symbol to edit.
                Empty falls back to the editor's current component.

        Returns:
            Dictionary with "component", "owner_part_id", the
            "pins_changed" list and its "count".
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "pin_designators": pin_designators,
            "owner_part_id": int(owner_part_id),
        }
        if str(component_name).strip():
            params["component_name"] = str(component_name).strip()
        return await bridge.send_command_async(
            "library.set_pin_owner_part", params
        )

    @mcp.tool()
    async def lib_set_pin_properties(
        component_name: str,
        pins: list[dict[str, Any]],
        library_path: str = "",
    ) -> dict[str, Any]:
        """Edit pins of a LIBRARY symbol in place: name, number, type, flags.

        Use this rather than deleting and recreating a symbol to fix a pin:
        recreating loses the symbol's parameters, models and graphics. Each
        edit names the pin by its CURRENT designator and lists only what
        changes; anything not listed is left as it is.

        Pins on every sub-part are reachable without an @N scope: the edit
        walks the component's own pins, the walk lib_get_pin_list reads.
        ``owner_part_id`` narrows an edit to one part when a number is
        reused across parts.

        Renumbering is refused, per edit, when the new number already
        belongs to another pin (NEW_DESIGNATOR_EXISTS) or when two pins
        share the old number on the same part and display mode
        (AMBIGUOUS_DESIGNATOR). Edits apply in order, so swap two numbers
        through a temporary one: 1->X, 2->1, X->2.

        Args:
            component_name: the symbol's LibReference.
            pins: list of edits. Keys:
                - designator (str, required): the pin's current number.
                - owner_part_id (int, optional): only pins of this part
                  (0 = Part Zero, the shared pins).
                - name (str): new pin name; "" clears it.
                - new_designator (str): new pin number.
                - electrical_type (str): input, output, bidirectional (io),
                  passive, power, open_collector, open_emitter, hiz. An
                  unknown word is refused for that edit, never turned
                  passive.
                - show_name, show_designator (bool): label visibility.
                - hidden (bool): hide the whole pin.
                - length (whole number of mils, > 0; 12.7 is refused,
                  not rounded).
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"component", "total", "applied", "failed", "pins_changed",
             "ops": [{"designator", "matched", "applied", "verified",
             "reason", "pins": [read-back of every changed pin]}]}. No pin
            changed at all is an error (NO_PIN_CHANGED) with the same
            ops list in details. The edit is in memory until
            lib_save_library(library_path) writes it.
        """
        if not str(component_name).strip():
            return {"ok": False, "reason": "component_name is required"}
        payload, problems = _pin_edits_payload(pins)
        if problems:
            return {"ok": False,
                    "reason": ("refused, nothing was sent: "
                               + "; ".join(problems[:5])),
                    "problems": problems}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_pin_properties",
            {
                "component_name": str(component_name).strip(),
                "pins": payload,
                "library_path": library_path,
            },
            timeout=60.0,
        )

    @mcp.tool()
    async def lib_export_kicad_symbol(
        component_name: str = "",
        output_path: str = "",
        reference: str = "U",
    ) -> dict[str, Any]:
        """Export a SchLib symbol to a KiCad .kicad_sym file.

        Reads the symbol's pins from the active schematic library and writes a
        KiCad 6+ S-expression symbol: a body rectangle sized to the pin roots
        plus one pin per Altium pin (designator -> number, name, electrical
        type, position, orientation). Coordinates convert mils -> mm. This
        covers the symbol only; footprint export is separate.

        Naming a component SELECTS it in the library, and that outlives
        the call: exporting is a read, but it leaves the library on the
        component it exported. Tools called afterwards without a
        component name act on whatever is current, so a following
        ``lib_add_pins`` would add pins here. The reply reports it in
        ``current_component`` when a name was given.

        Args:
            component_name: LibRef to export. If empty, exports whatever
                component is currently selected in the SchLib.
            output_path: Destination .kicad_sym file. Defaults to
                workspace/<name>.kicad_sym.
            reference: Reference designator prefix for the symbol (default
                "U").

        Returns:
            {"output_path", "symbol", "pin_count"} or an error.
        """
        from pathlib import Path

        bridge = get_bridge()
        if component_name:
            await bridge.send_command_async(
                "library.set_current_component", {"name": component_name}
            )
        pin_data = await bridge.send_command_async("library.get_pin_list", {})
        if not isinstance(pin_data, dict):
            return {"success": False, "error": "could not read pin list"}
        name = component_name or str(pin_data.get("component") or "").strip()
        if not name:
            return {"success": False, "error": "no component selected and none named"}
        pins = pin_data.get("pins", []) or []

        from eda_agent.units import MM_PER_MIL as MM  # mils -> mm

        def esc(s: str) -> str:
            return str(s).replace("\\", "\\\\").replace('"', '\\"')

        # Altium electrical type -> KiCad pin electrical type.
        etype_map = {
            "input": "input", "output": "output", "io": "bidirectional",
            "bidirectional": "bidirectional", "opencollector": "open_collector",
            "open_collector": "open_collector", "openemitter": "open_emitter",
            "open_emitter": "open_emitter", "power": "power_in",
            "power_in": "power_in", "power_out": "power_out",
            "hiz": "tri_state", "tristate": "tri_state", "tri_state": "tri_state",
            "passive": "passive",
        }
        # Altium orientation 0/1/2/3 (right/up/left/down) -> KiCad pin angle.
        angle_map = {0: 0, 1: 90, 2: 180, 3: 270}
        # Unit direction of each orientation, to find each pin's body root.
        dir_map = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
        PIN_LEN_MILS = 100

        roots_x: list[float] = []
        roots_y: list[float] = []
        pin_lines: list[str] = []
        for p in pins:
            try:
                px = float(p.get("x", 0))
                py = float(p.get("y", 0))
            except (TypeError, ValueError):
                continue
            orient = int(p.get("orientation", 0)) % 4
            dx, dy = dir_map[orient]
            roots_x.append((px - dx * PIN_LEN_MILS) * MM)
            roots_y.append((py - dy * PIN_LEN_MILS) * MM)
            ktype = etype_map.get(
                str(p.get("electrical_type", "")).strip().lower().replace(" ", ""),
                "unspecified",
            )
            number = esc(p.get("designator", ""))
            pname = esc(p.get("name", "") or "~")
            pin_lines.append(
                f'        (pin {ktype} line (at {px * MM:.4f} {py * MM:.4f} '
                f'{angle_map[orient]}) (length {PIN_LEN_MILS * MM:.4f})\n'
                f'          (name "{pname}" (effects (font (size 1.27 1.27))))\n'
                f'          (number "{number}" (effects (font (size 1.27 1.27)))))'
            )

        if roots_x and roots_y:
            minx, maxx = min(roots_x), max(roots_x)
            miny, maxy = min(roots_y), max(roots_y)
            if maxx - minx < 2.54:
                maxx = minx + 2.54
            if maxy - miny < 2.54:
                maxy = miny + 2.54
        else:
            minx, miny, maxx, maxy = -2.54, -2.54, 2.54, 2.54

        nm = esc(name)
        body = (
            f'(kicad_symbol_lib\n'
            f'  (version 20211014)\n'
            f'  (generator eda_agent)\n'
            f'  (symbol "{nm}"\n'
            f'    (in_bom yes)\n'
            f'    (on_board yes)\n'
            f'    (property "Reference" "{esc(reference)}" (at 0 {maxy + 2.54:.4f} 0)\n'
            f'      (effects (font (size 1.27 1.27))))\n'
            f'    (property "Value" "{nm}" (at 0 {miny - 2.54:.4f} 0)\n'
            f'      (effects (font (size 1.27 1.27))))\n'
            f'    (property "Footprint" "" (at 0 0 0)\n'
            f'      (effects (font (size 1.27 1.27)) hide))\n'
            f'    (property "Datasheet" "" (at 0 0 0)\n'
            f'      (effects (font (size 1.27 1.27)) hide))\n'
            f'    (symbol "{nm}_0_1"\n'
            f'      (rectangle (start {minx:.4f} {maxy:.4f}) (end {maxx:.4f} {miny:.4f})\n'
            f'        (stroke (width 0.254) (type default))\n'
            f'        (fill (type background)))\n'
            f'    )\n'
            f'    (symbol "{nm}_1_1"\n'
            + "\n".join(pin_lines) + ("\n" if pin_lines else "")
            + f'    )\n'
            f'  )\n'
            f')\n'
        )

        if output_path:
            out = Path(output_path)
        else:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            out = get_config().workspace_dir / f"{safe}.kicad_sym"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

        result = {
            "success": True,
            "output_path": str(out),
            "symbol": name,
            "pin_count": len(pin_lines),
        }
        # Naming a component SELECTS it, and the selection outlives this
        # call. Library tools that take no component name act on whatever
        # is current, so a later lib_add_pins would land here rather than
        # on whatever the caller was editing. Reported instead of
        # restored: the caller asked about this component, so leaving it
        # selected is reasonable, but leaving it silent is not.
        if component_name:
            result["current_component"] = name
            result["note"] = (
                f"the library is now on '{name}'; tools that take no "
                "component name will act on it")
        return result

    @mcp.tool()
    async def lib_export_kicad_footprint(
        footprint_name: str = "",
        output_path: str = "",
    ) -> dict[str, Any]:
        """Export a PcbLib footprint to a KiCad ``.kicad_mod`` file.

        Reads the footprint's pads from the active (or named) PCB library and
        writes a KiCad 6+ S-expression footprint: one pad each
        (designator -> number, position, size, shape, drill, layer set).
        Through-hole pads (hole > 0) get a drill and the all-copper layer set;
        SMD pads get the side's Cu/Paste/Mask set. Altium mils convert to mm
        and the y axis is flipped (Altium y-up -> KiCad y-down). This is the
        footprint counterpart to ``lib_export_kicad_symbol``.

        Args:
            footprint_name: Footprint to export. If empty, exports the
                library's current footprint.
            output_path: Destination .kicad_mod file. Defaults to
                workspace/<name>.kicad_mod.

        Returns:
            {"output_path", "footprint", "pad_count"} or an error.
        """
        from pathlib import Path

        from ..export.kicad_footprint import format_kicad_footprint

        bridge = get_bridge()
        args: dict[str, Any] = {}
        if footprint_name:
            args["footprint_name"] = footprint_name
        data = await bridge.send_command_async(
            "library.get_footprint_pads", args
        )
        if not isinstance(data, dict) or "pads" not in data:
            return {"success": False,
                    "error": "could not read footprint pads (open the PcbLib)"}
        name = footprint_name or str(data.get("name") or "").strip()
        if not name:
            return {"success": False,
                    "error": "no footprint selected and none named"}
        pads = data.get("pads", []) or []
        # Pascal returns x/y/size_x/size_y/hole in mils; map to the writer's
        # mil-suffixed keys.
        norm = [
            {
                "name": p.get("name", ""),
                "x_mils": p.get("x", 0),
                "y_mils": p.get("y", 0),
                "size_x_mils": p.get("size_x", 0),
                "size_y_mils": p.get("size_y", 0),
                "shape": p.get("shape", "round"),
                "layer": p.get("layer", "top"),
                "hole_mils": p.get("hole", 0),
                "rotation": p.get("rotation", 0),
            }
            for p in pads
        ]
        body = format_kicad_footprint(name, norm)

        if output_path:
            out = Path(output_path)
        else:
            safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
            out = get_config().workspace_dir / f"{safe}.kicad_mod"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")

        return {
            "success": True,
            "output_path": str(out),
            "footprint": name,
            "pad_count": len(norm),
        }

    @mcp.tool()
    async def lib_copy_component(
        source_name: str,
        new_name: Optional[str] = None,
        source_library: Optional[str] = None,
        dest_library: Optional[str] = None,
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy a component WITHIN or BETWEEN schematic libraries.

        Replicates the source component (all pins, graphics, parameters)
        and adds it to the destination library. When ``dest_library`` is
        omitted or equals ``source_library`` this behaves as the original
        same-library duplicate. When ``dest_library`` differs, the
        component is copied across libraries -- the source library is
        focused for the replicate, then the destination is focused and
        the clone is added there. The destination ends focused with the
        new component selected. Save is deferred (call
        ``lib_save_library`` on the destination to flush).

        Args:
            source_name: lib_ref of the component to copy.
            new_name: lib_ref for the clone. Defaults to ``source_name``
                (natural choice for cross-library copies that should
                keep their identity).
            source_library: .SchLib path to read from. Defaults to the
                currently focused document.
            dest_library: .SchLib path to write to. Omit (or pass the
                same path as ``source_library``) for a same-library
                duplicate.
            overwrite: When True, a component already named ``new_name``
                in the destination is removed first. Default False
                returns ``NAME_EXISTS`` and changes nothing.

        Returns:
            Dictionary with ``success``, ``source``, ``new_name``,
            ``source_library``, ``dest_library``, ``same_library`` and
            ``overwrote`` flags.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"source_name": source_name}
        if new_name:
            params["new_name"] = new_name
        if source_library:
            params["source_library"] = source_library
        if dest_library:
            params["dest_library"] = dest_library
        if overwrite:
            params["overwrite"] = "true"
        result = await bridge.send_command_async(
            "library.copy_component", params,
        )
        return result

    @mcp.tool()
    async def lib_copy_footprint(
        source_name: str,
        new_name: str = "",
        source_library: str = "",
        dest_library: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Copy one footprint (all pads/primitives) into a PcbLib, optionally renaming.

        Footprint analog of lib_copy_component: full-copies the footprint with
        no delete. source_library defaults to the focused PcbLib; dest_library
        defaults to the source. A same-library copy requires a different
        new_name. Errors if new_name already exists in dest unless overwrite.

        Args:
            source_name: the footprint to copy.
            new_name: name for the copy (default source_name).
            source_library: source .PcbLib path (default the focused lib).
            dest_library: destination .PcbLib path (default source).
            overwrite: replace a same-named footprint already in dest.

        Returns:
            {"success": true, "source": "...", "new_name": "...",
             "same_library": bool}.
        """
        params: dict[str, Any] = {"source_name": source_name}
        if new_name:
            params["new_name"] = new_name
        if source_library:
            params["source_library"] = source_library
        if dest_library:
            params["dest_library"] = dest_library
        if overwrite:
            params["overwrite"] = "true"
        bridge = get_bridge()
        return await bridge.send_command_async("library.copy_footprint", params)

    @mcp.tool()
    async def lib_move_components(
        source_schlib: str,
        dest_schlib: str,
        names: Optional[list[str]] = None,
        name_regex: str = "",
        delete_from_source: bool = True,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move matching components (symbol + params + models) between SchLibs.

        Bulk analog of lib_copy_component: copies every matching component from
        source_schlib to dest_schlib (Replicate carries the whole symbol, its
        parameters and its models) in one pass, then removes them from source
        unless delete_from_source is False. This is the piece that makes a full
        library split a single call.

        Provide either names (explicit LibReferences) or name_regex (matched
        against the source's component names). With names it is one IPC call;
        with name_regex the source component list is fetched first to resolve
        the matches, then the move runs. A component already present in dest is
        skipped unless overwrite=True.

        Args:
            source_schlib: source .SchLib path.
            dest_schlib: destination .SchLib path.
            names: explicit list of component LibReferences to move.
            name_regex: regex matched against source component names when names
                is not given.
            delete_from_source: remove moved components from source (default True).
            overwrite: replace a same-named component already in dest.
            dry_run: resolve the selection and report it WITHOUT moving
                anything. Worth doing whenever name_regex is used: the
                pattern is applied with ``search``, not a full match, so
                ``R`` selects every name containing an R, and the default
                is to delete them from the source afterwards.

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...} with nothing
            sent to the bridge.

        A failed copy does not delete: the handler counts it in ``failed``
        and moves on, so the source keeps it. What is NOT recoverable is
        ``overwrite=True`` replacing a different component of the same
        name in the destination, which is why dry_run reports both flags.
        """
        resolved: list[str] = list(names or [])
        if not resolved and name_regex:
            import re

            listing = await get_bridge().send_command_async(
                "library.get_components", {"library_path": source_schlib}
            )
            comps = (listing or {}).get("components") or []
            pat = re.compile(name_regex)
            for c in comps:
                nm = c.get("name") or c.get("lib_ref") or ""
                if nm and pat.search(nm):
                    resolved.append(nm)
        if not resolved:
            return {
                "error": "no components to move: provide names[] or a "
                "name_regex that matches at least one source component"
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "source_schlib": source_schlib,
                "dest_schlib": dest_schlib,
                "resolved": resolved,
                "count": len(resolved),
                "delete_from_source": delete_from_source,
                "overwrite": overwrite,
                "note": "nothing was moved; re-run with dry_run=False",
            }
        # Names ride a '~~'-separated field, and the default is to
        # DELETE the moved parts from the source: a name carrying the
        # separator splits into fragments that can match real parts.
        # Refuse rather than strip.
        bad = [n for n in resolved if "~~" in n]
        if bad:
            return {"ok": False, "reason":
                    f"component names {bad} contain '~~', which is the "
                    "wire-format separator; fragments could match other "
                    "parts, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_components",
            {
                "source_library": source_schlib,
                "dest_library": dest_schlib,
                "names": "~~".join(resolved),
                "delete_from_source": "true" if delete_from_source else "false",
                "overwrite": "true" if overwrite else "false",
            },
        )

    @mcp.tool()
    async def lib_move_footprints(
        source_pcblib: str,
        dest_pcblib: str,
        names: Optional[list[str]] = None,
        name_regex: str = "",
        delete_from_source: bool = True,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Move matching footprints between PcbLibs (the PcbLib analog of move).

        Copies every matching footprint (all pads and primitives, via a full
        copy) from source_pcblib to dest_pcblib, then removes them from source
        unless delete_from_source is False. The footprint counterpart of
        lib_move_components; together they split a full library.

        Provide either names (explicit footprint names) or name_regex (matched
        against the source's footprint names). With names it is one IPC call;
        with name_regex the source footprint list is fetched first. A footprint
        already present in dest is skipped unless overwrite=True.

        Args:
            source_pcblib: source .PcbLib path.
            dest_pcblib: destination .PcbLib path.
            names: explicit list of footprint names to move.
            name_regex: regex matched against source footprint names when names
                is not given.
            delete_from_source: remove moved footprints from source (default True).
            overwrite: replace a same-named footprint already in dest.
            dry_run: resolve the selection and report it WITHOUT moving
                anything. Worth doing whenever name_regex is used: the
                pattern is applied with ``search``, not a full match, so a
                short pattern selects far more than intended, and the
                default is to delete them from the source afterwards.

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...} with nothing
            sent to the bridge.
        """
        resolved: list[str] = list(names or [])
        if not resolved and name_regex:
            import re

            listing = await get_bridge().send_command_async(
                "library.get_footprints", {"library_path": source_pcblib}
            )
            fps = (listing or {}).get("footprints") or []
            pat = re.compile(name_regex)
            for f in fps:
                nm = f.get("name") or ""
                if nm and pat.search(nm):
                    resolved.append(nm)
        if not resolved:
            return {
                "error": "no footprints to move: provide names[] or a "
                "name_regex that matches at least one source footprint"
            }
        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "source_pcblib": source_pcblib,
                "dest_pcblib": dest_pcblib,
                "resolved": resolved,
                "count": len(resolved),
                "delete_from_source": delete_from_source,
                "overwrite": overwrite,
                "note": "nothing was moved; re-run with dry_run=False",
            }
        # Same wire format and same default-delete as
        # lib_move_components; same refusal.
        bad = [n for n in resolved if "~~" in n]
        if bad:
            return {"ok": False, "reason":
                    f"footprint names {bad} contain '~~', which is the "
                    "wire-format separator; fragments could match other "
                    "footprints, so the call is refused"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_footprints",
            {
                "source_library": source_pcblib,
                "dest_library": dest_pcblib,
                "names": "~~".join(resolved),
                "delete_from_source": "true" if delete_from_source else "false",
                "overwrite": "true" if overwrite else "false",
            },
        )

    @mcp.tool()
    async def lib_split_pin_functions() -> dict[str, Any]:
        """Split slash-delimited pin names into pin function lists.

        For the current library symbol, parses each pin whose name is
        `PRIMARY/ALT1/ALT2/...` into the pin's function list (the alternate-
        function popup), leaving `PRIMARY` as the visible name. Pins without a
        `/` are unchanged.

        Returns:
            {"pins_processed": N}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("library.split_pin_functions", {})

    @mcp.tool()
    async def lib_get_installed_libraries(
        with_counts: bool = True,
    ) -> dict[str, Any]:
        """List the libraries installed in the Altium environment.

        The answer to "what libraries does this installation have?", which
        no other tool gives: `lib_search` walks only the SchLibs already
        open in the workspace, and `design_snapshot_inventory` has to be
        handed explicit .SchLib paths. This reads the environment's own
        list, so it covers .IntLib, .SchLib, .PcbLib, database and query
        libraries whether or not anything is open.

        INSTALLED IS NOT AVAILABLE. Installed libraries are the ones
        switched on for this environment; available ones are every library
        it knows about. This returns the installed list and reports the
        available total beside it, so a library that is present but not
        switched on shows up as a gap between the two numbers rather than
        as an absence.

        Args:
            with_counts: True (default) also reports how many components
                each library holds. That opens every library to count
                them, so pass False for a fast listing; the count then
                comes back as -1, meaning not asked rather than empty.

        Returns:
            {"libraries": [{"library_path", "file_name", "library_type",
            "library_type_ordinal", "component_count"}], "installed_count",
            "available_count", "counts_included"}. ``library_type`` is one
            of integrated / source / datafile / database / none / query /
            design_items, or unknown; it reads "unknown" with an ordinal of
            -1 when a library is installed but missing from the available
            list, which is a real state and not an error.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {}
        if not with_counts:
            params["with_counts"] = "false"
        return await bridge.send_command_async(
            "library.get_installed_libraries", params
        )

    @mcp.tool()
    async def lib_install_library(library_path: str) -> dict[str, Any]:
        """Register a library with the environment's Available Libraries.

        Installs an .IntLib / .SchLib / .PcbLib so its parts resolve by
        lib-ref across the workspace (the step after authoring a library).

        Args:
            library_path: absolute path to the library file.

        Returns:
            {"installed": bool, "library_path": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.install_library", {"library_path": library_path}
        )

    @mcp.tool()
    async def lib_uninstall_library(library_path: str) -> dict[str, Any]:
        """Unregister a library from the environment's Available Libraries.

        Args:
            library_path: absolute path to the library file.

        Returns:
            {"uninstalled": bool, "library_path": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.uninstall_library", {"library_path": library_path}
        )

    @mcp.tool()
    async def lib_delete_component(
        component_name: str = "",
        library_path: str = "",
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Delete one symbol from a schematic library (.SchLib).

        Removes the component whose LibReference is ``component_name`` and
        marks the library dirty for deferred save (flushed by
        lib_save_library).
        Deletes a single named part; if the name is not found the call
        errors (COMPONENT_NOT_FOUND) rather than silently doing nothing.
        There is no wildcard mass-delete.

        Args:
            component_name: the component's LibReference (its library name).
            library_path: optional absolute .SchLib path to target; defaults
                to the currently focused library document.
            component_index: alternative to component_name; the integer
                `index` from lib_get_components. Use it to delete a part
                whose name carries unreproducible bytes (embedded quote or
                control char). Provide exactly one of the two.

        Returns:
            {"success": true, "library_path": "...", "deleted": "..."}.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {"library_path": library_path}
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        return await bridge.send_command_async(
            "library.delete_component", params,
        )

    @mcp.tool()
    async def lib_rename_component(
        new_name: str,
        component_name: str = "",
        library_path: str = "",
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Rename one symbol's LibReference in a schematic library (.SchLib).

        This is the repair path for a component whose current name carries
        bytes a caller cannot reproduce (an embedded double-quote, or a
        control char left by a broken import). Address it by
        ``component_index`` from lib_get_components, give it a clean
        ``new_name``, and every name-based tool can reach it again.

        The component is removed and re-added under the new name so the
        library's internal by-name index is rebuilt. Errors with
        NAME_EXISTS if a different component already uses ``new_name``.
        Marks the library dirty for deferred save.

        Args:
            new_name: the clean LibReference to write.
            component_name: current LibReference of the target. Provide this
                or component_index.
            library_path: optional absolute .SchLib path; defaults to the
                focused library.
            component_index: the integer `index` from lib_get_components,
                the name-free way to pick the target. Provide this or
                component_name.

        Returns:
            {"success": true, "library_path": "...", "old_name": "...",
             "new_name": "..."}.
        """
        if not component_name and component_index is None:
            return {"ok": False, "reason":
                    "provide component_name or component_index to pick "
                    "the part"}
        bridge = get_bridge()
        params: dict[str, Any] = {
            "new_name": new_name,
            "library_path": library_path,
        }
        if component_name:
            params["component_name"] = component_name
        if component_index is not None:
            params["component_index"] = component_index
        return await bridge.send_command_async(
            "library.rename_component", params,
        )

    @mcp.tool()
    async def lib_delete_footprint_primitives(
        footprint_name: str,
        object_type: str = "",
        layer: str = "",
        library_path: str = "",
        include_pads: bool = False,
        confirm: bool = False,
        remove_from_board: bool = False,
        reload_first: bool = True,
        verify_on_disk: bool = True,
        park_current: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Delete primitives inside ONE footprint of a PCB library.

        THE ONLY LIBRARY-SCOPED PRIMITIVE DELETE. ``obj_delete`` and
        ``pcb_delete_object`` both resolve a BOARD, and when no board is
        focused that lookup opens the first PcbDoc any open project
        holds. Aimed at a library they do not fail; they remove
        primitives from a board you never named, and the reply does not
        say which one. This is the tool for editing a footprint.

        SCOPED THREE WAYS, all of which must agree before anything is
        removed: the library by path, the footprint by name, and the
        object type or layer. The library is verified AFTER it is
        brought to the front, so a delete aimed at one that never
        focused is refused rather than landing in whichever did.

        PADS ARE EXCLUDED unless ``include_pads``. Deleting a pad changes
        the part's connectivity rather than its drawing, and clearing
        graphics off a layer should not quietly cost you the pinout.

        REMOVED FROM THE FOOTPRINT, the removal this tool has always
        made. A primitive CREATED IN THIS SESSION does not stay removed:
        measured 2026-10-04, a pad added and then deleted came back with
        the next save (twice, the second time with
        ``remove_from_board=True``), while the same delete after
        ``lib_reload_library`` persisted. So by default (``reload_first``)
        this SAVES THE LIBRARY AND REOPENS IT FIRST, which makes every
        primitive it removes one loaded from disk. That is a TOOLING fix,
        not an Altium one: no proven script member removes a
        session-created primitive's library-board registration. After the
        reopen the footprint is PARKED (another footprint made current)
        when it came back current, so the removal also runs on a footprint
        that is not current, and with ``verify_on_disk`` the library is
        saved again and the footprint read back from the FILE in the same
        call (``persisted``), which settles whether the removal stuck.

        BEING CURRENT IS ENOUGH TO BRING A REMOVED PRIMITIVE BACK
        (measured 2026-10-05, docs/RELEASE_VERIFICATION.md step 31 item
        3b): a DISK-LOADED pad, removed with reload_first=False while its
        footprint was the current one, was still in the file after
        lib_save_library (inferred mechanism: the save writes the library
        board's copy of the current footprint). The default route worked
        because after the reopen the target was not current. Whether a
        session-created primitive also comes back from a footprint that is
        NOT current, without a reload, is unmeasured.
        So ``park_current`` now defaults ON whether or not reload_first
        runs: a current footprint is parked before the removal. Parking
        WITHOUT a reload has not yet been measured live; without
        reload_first nothing is verified on disk either, so prove it with
        lib_save_library + lib_read_pcblib_file.

        THE SIDE EFFECT, stated: with reload_first the call saves the
        library (every pending edit in it) before removing, and again
        after with verify_on_disk. An unknown ``object_type`` and a
        footprint that is not in the library are refused BEFORE the save.

        Args:
            footprint_name: The footprint to edit. Required: deleting
                from whichever footprint the editor happens to show is
                the mistake this avoids.
            object_type: PCB object type, e.g. "track", "arc", "string",
                "region", "fill". Give this or ``layer``.
            layer: Restrict to one layer, e.g. "TopOverlay". The usual
                case: clear a silkscreen without touching anything else.
            library_path: The .PcbLib. Defaults to the focused document.
            include_pads: Allow pads to be removed. Off by default.
            confirm: Required. Read the footprint first with
                ``lib_probe_footprint`` and pass True once the filters
                are what you mean.
            remove_from_board: ALSO take each removed primitive off the
                library's backing board, the second place every add
                registers it. Off by default. MEASURED 2026-10-04 on the
                pad delete: the board half found a session-created pad,
                "removed" it and still listed it, and the save wrote it
                back; it does not fix that case (``reload_first`` does).
                With it the footprint is made current first
                (``SELECT_FAILED`` if that does not take, nothing
                removed), the footprint removal runs as usual, and only
                then is the board swept, by address, for objects the
                footprint no longer lists.
            reload_first: ON by default. Save the library and reopen it
                before removing anything, then park the footprint. Uses
                ``library_path``, or the focused .PcbLib when it is empty;
                skipped when neither resolves, and then said so at the TOP
                of the reply (``warning``, ``note``). The save writes
                EVERY pending edit in that library, not only this
                footprint's. A save that does not reach disk refuses the
                delete, because reopening a dirty library raises Altium's
                Unsaved Changes prompt. False removes from the library as
                it stands in memory, the old behaviour.
            verify_on_disk: ON by default, used when reload_first ran:
                save again after the delete and compare the saved
                footprint's record total with the file before it
                (``persisted``); a removed primitive the save wrote back
                is ``ok``/``success`` false.
            park_current: default (None) = ON unless
                ``remove_from_board``: when the footprint is the current
                one, another footprint is made current (verified) before
                the removal, so the removal runs on a footprint the
                library board does not hold. Sent with or without
                reload_first. False removes from a current footprint, the
                combination measured to come back. True with
                ``remove_from_board`` is refused (its board half needs the
                footprint current). Parking without reload_first is not
                yet measured live (measured on pads only, so far). SIDE
                EFFECT: the handler parks BEFORE it validates ``layer``
                (INVALID_LAYER), so a delete it then refuses can still
                leave another footprint current; that is not put back
                (the park moves below the validation at the next script
                version).

        PROOF IT PERSISTED: ``persisted`` in the reply, or by hand
        ``lib_save_library``, then
        ``lib_read_pcblib_file(library_path, footprint_name)``, which
        reads the saved file with no Altium involved.

        Returns:
            Dict with ``library``, ``footprint``, ``removed`` (distinct
            primitives), ``remove_calls``, ``double_listed``,
            ``still_listed`` (removed objects the FOOTPRINT still lists;
            must be 0), ``board_still_listed`` (removed objects the
            LIBRARY BOARD still lists; above 0 is one a save can write
            back; NULL when the footprint was not current during the
            removal, when the board holds none of its primitives),
            ``target_was_current``, ``parked_elsewhere``,
            ``target_current_during_removal``, ``marked_modified`` (the
            library reads modified afterwards), ``examined``,
            ``layers``, ``pads_included``, ``board_removal``
            (``{requested}``, or with ``remove_from_board``:
            ``made_current``, ``objects``, ``listed_before``,
            ``removed``, ``remove_calls``, ``still_listed``,
            ``skipped_still_in_footprint``, ``not_run``), ``note``,
            ``reload_first`` (``{requested, ran, saved, reloaded, save}``
            or ``{requested, ran: false, skipped}``, the skip also in
            ``warning``) and ``persisted`` (``{checked, saved, save,
            records_before, records_after, expected_records_after,
            still_present, by_kind, ok}``, read from the saved file).
        """
        if not footprint_name.strip():
            return {"ok": False, "reason": "footprint_name is required"}
        if not object_type.strip() and not layer.strip():
            return {"ok": False, "reason": (
                "give object_type or layer. Emptying a whole footprint is "
                "not something to reach by leaving both filters off")}

        if (object_type.strip() and _normalize_type_name(object_type)
                not in _PCB_OBJECT_TYPE_WORDS):
            # The handler refuses it too, but only after reload_first has
            # saved and reopened the library for nothing.
            return {"ok": False, "reason": (
                f"unknown object_type {object_type!r}. Accepted (any case, "
                f"spaces/_/- ignored, a leading 'e' optional): "
                f"{', '.join(sorted(_PCB_OBJECT_TYPE_WORDS))}")}

        params: dict[str, Any] = {"footprint_name": footprint_name}
        if object_type.strip():
            params["object_type"] = object_type.strip()
        if layer.strip():
            params["layer"] = layer.strip()
        if library_path.strip():
            params["library_path"] = library_path.strip()
        if include_pads:
            params["include_pads"] = "true"
        if confirm:
            params["confirm"] = "true"
        if remove_from_board:
            params["remove_from_board"] = "true"
        park, why = _park_choice(park_current, remove_from_board)
        if why:
            return {"ok": False, "reason": why}
        if park:
            params["park_current"] = "true"

        bridge = get_bridge()
        rec = None
        baseline = None
        baseline_why = None
        path = ""
        fp = footprint_name.strip()
        if reload_first and confirm:
            # Only for a delete that will run: without confirm the handler
            # refuses, and saving and reopening a library for that would
            # be a side effect with nothing to show for it.
            path, source = await _resolve_library_path(bridge, library_path)
            if path:
                params["library_path"] = path
                pre, why = await _precheck_footprint(bridge, path, fp)
                if why:
                    return {"ok": False, "reason": why, "precheck": pre}
                fp = pre.get("name") or fp
            rec, refusal = await _reload_first(bridge, path, "deleted",
                                               skip_reason=source)
            if refusal is not None:
                return refusal
            if rec.get("ran") and verify_on_disk:
                baseline, baseline_why = _saved_footprint(path, fp)
        result = await bridge.send_command_async(
            "library.delete_footprint_primitives", params)
        result = _with_reload_record(result, rec)
        if not (rec and rec.get("ran") and verify_on_disk
                and isinstance(result, dict)):
            return result
        if baseline is None:
            result["persisted"] = {"checked": False, "reason": (
                f"no baseline from the saved file ({baseline_why}); after "
                f"lib_save_library, read it with lib_read_pcblib_file")}
            return result
        check, entry = await _save_and_read(bridge, path, fp)
        if entry is not None:
            check.update(_primitives_persisted(
                baseline, entry, int(result.get("removed") or 0)))
        result["persisted"] = check
        if not check.get("saved"):
            return _mark_failed(result, check.get("reason")
                                or "the verifying save failed")
        if check.get("checked") and not check.get("ok"):
            return _mark_failed(result, (
                f"the delete did not persist: the saved footprint holds "
                f"{check.get('records_after')} records, expected "
                f"{check.get('expected_records_after')} "
                f"({check.get('still_present')} removed primitive(s) were "
                f"written back by the save; see persisted.by_kind)"))
        return result

    @mcp.tool()
    async def lib_modify_footprint_pads(
        footprint_name: str,
        edits: list[dict[str, Any]],
        library_path: str = "",
        allow_blank_designator: bool = False,
    ) -> dict[str, Any]:
        """Change existing pads of ONE PcbLib footprint, in place, in one call.

        THE TOOL FOR EDITING A PAD. Before it the only way to reshape,
        resize, move, re-drill or rename one pad of a saved footprint
        was to edit the .PcbLib file by hand, because
        ``lib_delete_footprint_primitives`` takes every pad on a layer
        or none, and deleting and re-adding pads loses their per-pad
        overrides (a manual paste expansion, a mask expansion).

        Read the footprint first with ``lib_get_pad_geometry``: its pad
        order is what ``index`` counts in. Coordinates are mils relative
        to the footprint origin, the same convention as
        ``lib_add_footprint_pads``; sub-mil values are kept (1/10000 mil).
        The footprint is made the library's current one first, and the
        switch is read back (``SELECT_FAILED`` otherwise), because its
        origin is only a trustworthy frame for a current footprint.

        A designator carried by MORE THAN ONE pad (coincident USB-C
        contacts, a pin with two lands) is refused unless ``index``
        picks one, so a duplicate is never edited by guess. Each edit is
        validated completely before anything is written, and every
        field written is READ BACK: ``mismatched`` names any field that
        did not take, and ``pad`` is the pad's state afterwards.

        Args:
            footprint_name: The footprint to edit. Required.
            edits: One dict per pad:
                - designator (str, REQUIRED): selects the pad. "" selects
                  the unnamed pads (fiducials, NPTH holes).
                - index (int): 0-based among pads with that designator,
                  in ``lib_get_pad_geometry`` order. Required when the
                  designator is not unique.
                Then any of (omitted = left alone):
                - x, y (float, mils): new pad centre.
                - x_size, y_size (float, mils, > 0): copper size. In a
                  simple pad stack this is every layer.
                - rotation (float, degrees).
                - shape (str): round | rectangular | octagonal |
                  roundrect. Set on EVERY copper layer, through-hole
                  pads included.
                - corner_radius (int %, 0-100): for roundrect. Alone, it
                  re-radiuses a pad that is already roundrect; with no
                  corner_radius a roundrect keeps its current radius.
                - hole_size (float, mils): 0 makes the pad SMD (on
                  ``layer``, default TopLayer); > 0 makes it through-hole,
                  exactly as the add tools decide.
                - hole_type (str): round | square | slot. slot needs
                  slot_length.
                - slot_length (float, mils), hole_rotation (float, deg).
                - plated (bool): False for an NPTH hole.
                - mask_expansion, paste_expansion (float mils, or "rule"
                  to hand the value back to the design rule).
                - layer (str): SMD pads only, e.g. "BottomLayer".
                - new_designator (str): rename. "" leaves the pad unnamed,
                  and needs allow_blank_designator.
            library_path: The .PcbLib. Defaults to the focused document.
            allow_blank_designator: Allow new_designator "" on every edit
                (a fiducial or mechanical pad). A single edit can carry
                its own "allow_blank_designator": true instead.

        Returns:
            {library, footprint, applied, failed, total, results: [{
             designator, index, matches, ok, changed, mismatched,
             warnings, shape_status?, error?, message?, pad: {...}}],
             written_to_disk: false, pending_save}. ``pad`` carries
            x_mil/y_mil, sizes, shape, corner_pct, hole fields, plated,
            layer, mode, and for a through-hole pad its ``bottom`` and
            ``mid`` copper. The library is MARKED dirty, not written:
            ``lib_save_library(library_path)`` flushes it, and
            ``lib_read_pcblib_file(library_path, footprint_name)`` then
            reads the pads back from the SAVED file (no Altium involved),
            which is the proof the edit persisted.
        """
        if not str(footprint_name or "").strip():
            return {"ok": False, "reason": "footprint_name is required"}
        payload, problems = _pad_edits_payload(edits, allow_blank_designator)
        if problems:
            return {"ok": False,
                    "reason": "nothing was sent: " + problems[0],
                    "problems": problems}
        params: dict[str, Any] = {
            "footprint_name": str(footprint_name).strip(),
            "edits": payload,
        }
        if str(library_path or "").strip():
            params["library_path"] = str(library_path).strip()
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.modify_footprint_pads", params, timeout=120.0)

    @mcp.tool()
    async def lib_delete_footprint_pads(
        footprint_name: str,
        pads: list[dict[str, Any]],
        library_path: str = "",
        confirm: bool = False,
        remove_from_board: bool = False,
        reload_first: bool = True,
        verify_on_disk: bool = True,
        park_current: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Delete individual pads from ONE PcbLib footprint.

        The single-pad delete. ``lib_delete_footprint_primitives`` with
        include_pads removes every pad on a layer; this removes exactly
        the pads named. Select each by designator, plus ``index`` (the
        n-th pad with that designator in ``lib_get_pad_geometry`` order)
        when the designator is not unique, or ``all`` for every pad
        carrying it.

        ALL OR NOTHING: if any selector does not resolve to exactly the
        pads it names, nothing is removed and the reply says which.

        A PAD CREATED IN THIS SESSION DID NOT STAY DELETED (measured
        2026-10-04): removed from the footprint, it was written back by
        the next save, and again after a delete with
        ``remove_from_board=True``; after ``lib_reload_library`` the same
        delete persisted. So by default (``reload_first``) this SAVES THE
        LIBRARY AND REOPENS IT FIRST, which makes every pad it removes one
        loaded from disk. A tooling fix, stated plainly: no proven script
        member removes a session-created pad's library-board registration.
        After the reopen the footprint is PARKED (another footprint made
        current) when it came back current, so the removal also runs on a
        footprint that is not current, the other condition of the delete
        that persisted; a review noted the evidence fits "the save rebuilds
        the current footprint from the board" as well as "session-created".
        The selectors are resolved after the reopen, in the reopened
        library's pad order.

        MEASURED 2026-10-05 (docs/RELEASE_VERIFICATION.md step 31, item
        3b): the second theory is SUFFICIENT. A DISK-LOADED pad, removed with
        reload_first=False while its footprint was CURRENT (made so with
        lib_set_current_footprint), was still in the file after
        lib_save_library (target_current_during_removal true,
        board_still_listed 1). Being current is ENOUGH to make a removed
        primitive come back (inferred mechanism: the save writes the
        library board's copy of the current footprint); whether being
        added this session also is, on a footprint that is not current, is
        unmeasured. The default route (item 3) passed because after the
        reopen the target was not current. So ``park_current`` now defaults ON with or without
        reload_first. Parking without a reload is not yet measured live.

        THE SIDE EFFECT, stated: with reload_first the call SAVES the
        library (every pending edit in it) before removing anything, and
        with verify_on_disk saves it AGAIN after. Read-only checks run
        first so a delete the handler would refuse costs nothing: the
        footprint must be in the library and every selector must resolve
        against lib_get_pad_geometry (``precheck``).

        PROOF IT PERSISTED, in the same reply (verify_on_disk, on whenever
        reload_first ran): the library is saved again and the footprint
        read back FROM THE FILE; ``persisted`` compares the saved pads
        with the file as it stood before the delete, and ``ok``/``success``
        are false when a removed pad is still there. By hand:
        ``lib_save_library``, then
        ``lib_read_pcblib_file(library_path, footprint_name)``.

        Args:
            footprint_name: The footprint to edit. Required.
            pads: One dict per selector:
                - designator (str, REQUIRED). "" selects unnamed pads.
                - index (int): 0-based among pads with that designator.
                - all (bool): every pad with that designator.
            library_path: The .PcbLib. Defaults to the focused document.
            confirm: Required. Read the pads with
                ``lib_get_pad_geometry`` first, then pass True.
            remove_from_board: also take each removed pad off the
                library's backing board. Off by default; measured
                2026-10-04 NOT to stop a session-created pad coming back
                (see ``lib_delete_footprint_primitives``, which shares the
                rule).
            reload_first: ON by default. Save the library and reopen it
                before removing, then park the footprint (see above). Uses
                ``library_path``, or the focused .PcbLib when it is empty;
                skipped when neither resolves, and then said so at the TOP
                of the reply (``warning``, ``note``) as well as in
                ``reload_first.skipped``. The save writes EVERY pending
                edit in that library. A save that does not reach disk
                refuses the delete. False removes from the library as it
                stands in memory, the old behaviour.
            verify_on_disk: ON by default, used when reload_first ran:
                save again after the delete and compare the SAVED pads
                with the file before it (``persisted``). False leaves the
                delete in memory, unsaved.
            park_current: default (None) = ON unless
                ``remove_from_board``: a footprint that is current is
                parked (another made current, verified) before its pads
                are removed. Sent with or without reload_first; parking
                WITHOUT a reload is not yet measured live, so prove a
                no-reload delete with lib_save_library +
                lib_read_pcblib_file. False removes from a current
                footprint, measured to come back. True with
                ``remove_from_board`` is refused. SIDE EFFECT: the handler
                parks BEFORE it resolves the pad selectors, so a delete it
                then refuses (an unresolvable selector; checked here first
                only when reload_first runs) can still leave another
                footprint current; that is not put back (the park moves
                below the validation at the next script version).

        Returns:
            {library, footprint, removed, selectors, results, pads_before,
             pads_after, still_listed, board_still_listed,
             target_was_current, parked_elsewhere,
             target_current_during_removal, double_listed, board_removal,
             marked_modified, written_to_disk: false, pending_save,
             reload_first, precheck?, persisted?, warning?}.
            still_listed (the FOOTPRINT iterator) must be 0;
            board_still_listed (the LIBRARY BOARD iterator) above 0 is a
            pad a save can write back, and it is NULL when the footprint
            was not current during the removal (the board then holds none
            of its pads, so a count would be 0 by construction and prove
            nothing). ``persisted``: ``{checked, saved, save, absent,
            still_present, by_designator [{designator, before, removed,
            after, expected_after}], pads_before, pads_after,
            expected_pads_after, ok}``, read from the saved file. Without
            verify_on_disk the library is MARKED dirty (``marked_modified``
            reads it back), not written: ``lib_save_library(library_path)``
            flushes it.
        """
        if not str(footprint_name or "").strip():
            return {"ok": False, "reason": "footprint_name is required"}
        payload, problems = _pad_selectors_payload(pads)
        if problems:
            return {"ok": False,
                    "reason": "nothing was sent: " + problems[0],
                    "problems": problems}
        if not confirm:
            return {"ok": False, "reason": (
                "confirm=True is required: deleting a pad changes the "
                "footprint's pinout. Read the pads with "
                "lib_get_pad_geometry first.")}
        params: dict[str, Any] = {
            "footprint_name": str(footprint_name).strip(),
            "pads": payload,
            "confirm": "true",
        }
        if str(library_path or "").strip():
            params["library_path"] = str(library_path).strip()
        if remove_from_board:
            params["remove_from_board"] = "true"
        park, why = _park_choice(park_current, remove_from_board)
        if why:
            return {"ok": False, "reason": why}
        if park:
            params["park_current"] = "true"
        bridge = get_bridge()
        rec = None
        baseline = None
        baseline_why = None
        precheck: Optional[dict] = None
        path = ""
        fp = str(footprint_name).strip()
        if reload_first:
            path, source = await _resolve_library_path(bridge, library_path)
            if path:
                params["library_path"] = path
                # READ-ONLY checks BEFORE the save: a delete the handler
                # would refuse must not cost a save and reopen of the whole
                # library. Same footprint lookup and selector rules as
                # Lib_DeleteFootprintPads, against lib_get_pad_geometry
                # (which de-duplicates a session-added pad's double
                # listing, as the handler's snapshot does).
                precheck, why = await _precheck_footprint(bridge, path, fp)
                if why:
                    return {"ok": False, "reason": why, "precheck": precheck}
                fp = precheck.get("name") or fp
                try:
                    geo = await bridge.send_command_async(
                        "library.get_pad_geometry",
                        {"footprint_name": fp, "library_path": path})
                except Exception as exc:         # noqa: BLE001
                    geo = None
                    precheck["pads_error"] = str(exc)
                listed = geo.get("pads") if isinstance(geo, dict) else None
                if isinstance(listed, list):
                    names = [str(p.get("name", "")) for p in listed
                             if isinstance(p, dict)]
                    picked, problems = _resolve_pad_selectors(names, pads)
                    precheck["selected"] = picked
                    if problems:
                        return {"ok": False, "precheck": precheck,
                                "problems": problems, "reason": (
                                    "nothing was saved, reopened or removed: "
                                    + problems[0] + " (checked against "
                                    "lib_get_pad_geometry before the save)")}
            rec, refusal = await _reload_first(bridge, path, "deleted",
                                               skip_reason=source)
            if refusal is not None:
                return refusal
            if rec.get("ran") and verify_on_disk:
                baseline, baseline_why = _saved_footprint(path, fp)
        result = await bridge.send_command_async(
            "library.delete_footprint_pads", params, timeout=120.0)
        result = _with_reload_record(result, rec)
        if precheck is not None and isinstance(result, dict):
            result["precheck"] = precheck
        if not (rec and rec.get("ran") and verify_on_disk
                and isinstance(result, dict)):
            return result
        if baseline is None:
            result["persisted"] = {"checked": False, "reason": (
                f"no baseline from the saved file ({baseline_why}); after "
                f"lib_save_library, read it with lib_read_pcblib_file")}
            return result
        removed: dict = {}
        for row in result.get("results") or []:
            if isinstance(row, dict):
                desig = str(row.get("designator", ""))
                removed[desig] = removed.get(desig, 0) + int(
                    row.get("selected") or 0)
        if not removed and precheck:
            removed = dict(precheck.get("selected") or {})
        check, entry = await _save_and_read(bridge, path, fp)
        if entry is not None:
            check.update(_pads_persisted(baseline, entry, removed))
        result["persisted"] = check
        if not check.get("saved"):
            return _mark_failed(result, check.get("reason")
                                or "the verifying save failed")
        if check.get("checked") and not check.get("ok"):
            still = check.get("still_present") or []
            return _mark_failed(result, (
                "the delete did not persist: after the verifying save the "
                "file still holds "
                + (f"pad(s) {', '.join(repr(d) for d in still)}" if still
                   else f"{check.get('pads_after')} pads, expected "
                        f"{check.get('expected_pads_after')}")
                + " (see persisted.by_designator)"))
        return result

    @mcp.tool()
    async def lib_set_footprint_description(
        footprint_name: str = "",
        description: Optional[str] = None,
        descriptions: Optional[dict[str, str]] = None,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Set the Description of one PcbLib footprint, or of many in one call.

        The PcbLib twin of ``lib_set_component_description``, which
        writes a SchLib symbol. Read the current text with
        ``lib_get_footprints``. Every write is read back, so text the
        bridge flattened (characters above U+00FF arrive as "?")
        shows up as a mismatch instead of a success.

        Args:
            footprint_name: One footprint to set (with ``description``).
            description: The new text for ``footprint_name``. "" clears
                it.
            descriptions: Many at once, {footprint name: description},
                instead of the pair above. One round trip for a whole
                library, e.g. after a generator wrote "0,50mm" for
                "0.50mm" in every description.
            library_path: The .PcbLib. Defaults to the focused document.

        Returns:
            {library, updated, unchanged, mismatched, missing_count,
             missing, mismatches, results: [{footprint, old, read_back,
             changed}], written_to_disk: false, pending_save}. The
            library is MARKED dirty, not written:
            ``lib_save_library(library_path)`` flushes it.
        """
        name = str(footprint_name or "").strip()
        params: dict[str, Any] = {}
        if descriptions:
            if name or description is not None:
                return {"ok": False, "reason": (
                    "give footprint_name + description, or descriptions, "
                    "not both")}
            if not isinstance(descriptions, dict):
                return {"ok": False, "reason": (
                    "descriptions must be a {footprint name: description} "
                    "dict")}
            batch_path, error = _write_description_edits(descriptions)
            if error:
                return {"ok": False, "reason": "nothing was sent: " + error}
            params["edits_path"] = batch_path
        else:
            if not name:
                return {"ok": False, "reason": (
                    "give footprint_name + description, or descriptions")}
            if description is None:
                return {"ok": False, "reason": (
                    "description is required with footprint_name; pass "
                    "\"\" to clear it")}
            params["footprint_name"] = name
            params["description"] = str(description)
        if str(library_path or "").strip():
            params["library_path"] = str(library_path).strip()

        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.set_footprint_description", params, timeout=120.0)
        if name and isinstance(result, dict):
            lost = unsendable_chars(str(description))
            if lost:
                result["warning"] = (
                    f"{lost!r} cannot cross the bridge and arrives as '?'")
        return result

    @mcp.tool()
    async def lib_delete_footprint(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Delete one footprint from a PCB library (.PcbLib).

        Finds the footprint by name, removes and deregisters it, and MARKS
        the .PcbLib dirty. It does NOT write to disk: this bridge defers
        saves, and `lib_save_library` (this library only) is what flushes
        them. Reported: this said it saved, the footprint was still
        in the file and the timestamp unchanged until an explicit save.

        Deletes a single named footprint; if the name is not found the call
        errors (FOOTPRINT_NOT_FOUND). No wildcard mass-delete.

        Args:
            footprint_name: the footprint's name in the library.
            library_path: optional absolute .PcbLib path to target; defaults
                to the currently focused library document.

        Returns:
            {"success": true, "library_path": "...", "deleted": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.delete_footprint",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_set_model_name(
        component_name: str,
        new_model_name: str,
        model_name: str = "",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Set a model's model_name (footprint reference) on a SchLib symbol.

        Rewrites the ModelName of one of the component's implementations.
        If model_name is given, the model whose current ModelName matches is
        targeted; otherwise the current (or first) model is used.

        Args:
            component_name: the component's LibReference.
            new_model_name: the new model_name to write.
            model_name: optional current model_name to target a specific one.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "component": "...", "new_model_name": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_model_name",
            {
                "component_name": component_name,
                "new_model_name": new_model_name,
                "model_name": model_name,
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_set_model_source(
        component_name: str,
        source_library: str,
        model_name: str = "",
        use_component_library: Optional[bool] = None,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Write the datafile-link Location (source library) on a footprint model.

        A footprint whose datafile link has an empty Location resolves in the
        SchLib editor by name but cannot embed into a self-contained compiled
        library, so the footprint goes missing in the used/compiled output
        even though the package's own compile reports no error. This writes
        Location so the model is embeddable. Location is set in place (never
        through AddDataFileLink with a path, which wedges AD26). Targets PCBLIB
        models matching model_name, or all PCBLIB models when it is empty.

        IMPORTANT: get the exact source_library string from a known-good model
        that already embeds correctly. Read it via lib_get_component_details,
        whose models array now shows each model's location and
        use_component_library. This tool writes exactly what you pass; it does
        not infer the format.

        Args:
            component_name: the component's LibReference.
            source_library: the source-library reference written into Location.
            model_name: optional footprint model name to target one model.
            use_component_library: optional; sets the embed-vs-search flag.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "component": "...", "source_library": "...",
             "models_updated": N}.
        """
        params: dict[str, Any] = {
            "component_name": component_name,
            "source_library": source_library,
            "model_name": model_name,
            "library_path": library_path,
        }
        if use_component_library is not None:
            params["use_component_library"] = (
                "true" if use_component_library else "false"
            )
        bridge = get_bridge()
        return await bridge.send_command_async("library.set_model_source", params)

    @mcp.tool()
    async def lib_remove_model(
        component_name: str,
        model_name: str,
        keep_one: bool = False,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Remove models (implementations) from a SchLib component by name.

        Removes every implementation whose ModelName equals model_name. With
        keep_one=True it keeps the first match and removes the rest, which
        deletes duplicate model links while preserving one. Errors if the
        component is not found; returns removed=0 if no model matches.

        Args:
            component_name: the component's LibReference.
            model_name: the ModelName of the model(s) to remove.
            keep_one: keep the first match and remove the rest (dedup).
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"success": true, "model_name": "...", "removed": N,
             "kept_one": bool}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.remove_model",
            {
                "component_name": component_name,
                "model_name": model_name,
                "keep_one": "true" if keep_one else "false",
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_set_current_model(
        component_name: str,
        model_name: str,
        model_type: str = "PCBLIB",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Choose which of a library symbol's models is the current one.

        A placed part uses its symbol's CURRENT footprint. The flag lives
        on each model separately and Altium does not keep it exclusive, so
        a symbol can carry two current footprints or none, and nothing
        complains until a board picks the wrong land pattern. This sets
        the first model of ``model_type`` named ``model_name`` current and
        clears every other model of that type, then reads all of them back.
        Models of other types (SIM, PCB3DLib) are left alone.

        Changes the library only (in memory; lib_save_library writes
        it).
        Placed instances follow on the next Update From Libraries.

        Args:
            component_name: the component's LibReference.
            model_name: the model to make current, e.g. the footprint name.
            model_type: PCBLIB (default) for footprints; SIM for simulation.
            library_path: optional .SchLib path; defaults to the focused lib.

        Returns:
            {"component", "model_name", "model_type", "changed",
             "duplicates", "current_count", "models"}. ``duplicates`` > 0
            means several models share this name; lib_remove_model with
            keep_one=True removes the extras. MODEL_NOT_FOUND lists the
            models the symbol does have.
        """
        if not str(component_name).strip() or not str(model_name).strip():
            return {"ok": False,
                    "reason": "component_name and model_name are required"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_current_model",
            {
                "component_name": str(component_name).strip(),
                "model_name": str(model_name).strip(),
                "model_type": str(model_type).strip() or "PCBLIB",
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_rename_footprint(
        footprint_name: str,
        new_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Rename a footprint in a PCB library (.PcbLib).

        Renames the footprint whose name is footprint_name to new_name.
        Errors if footprint_name is not found or new_name already exists in
        the library.

        MARKS the .PcbLib dirty; it does not write. Call
        `lib_save_library` to flush, and check written_to_disk in its reply.

        Args:
            footprint_name: the current footprint name.
            new_name: the new footprint name.
            library_path: optional .PcbLib path; defaults to the focused lib.

        Returns:
            {"success": true, "footprint": "...", "new_name": "..."}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.rename_footprint",
            {
                "footprint_name": footprint_name,
                "new_name": new_name,
                "library_path": library_path,
            },
        )

    @mcp.tool()
    async def lib_save_library(library_path: str) -> dict[str, Any]:
        """Write ONE open library to disk, and prove the file changed.

        ``app_save_all`` writes every open sheet, and every save
        rewrites open SchDocs and PcbDocs with fresh parameter UniqueIDs
        (measured on a live project, 2026-10-02), so saving one library
        that way puts churn into every open sheet. Since 2026-10-06 it
        also, by default, leaves alone a library that reads unmodified,
        and some library edits do not raise that flag. This writes only
        the document at ``library_path``, whatever the flag says.

        MEASURED, NOT TRUSTED. Altium declines a save while a command is
        active and does not raise, so ``written_to_disk`` comes from the
        file: this process's mtime of it before and after, and the
        handler's FileAge before and after. ``ok`` is false when neither
        moved or the document still reads modified. If that happens, run
        ``app_exit_active_command`` and try again.

        A .PcbLib or a .SchLib. It must be open in Altium: a library that
        is not open holds no unsaved edits.

        Args:
            library_path: full path of the open .PcbLib or .SchLib.

        Returns:
            ``{ok, written_to_disk, mtime_changed, was_modified,
            still_modified, file_age_before, file_age_after,
            library_path}``, plus ``reason`` whenever ``ok`` is false
            (the house refusal shape; ``code`` and ``details`` when the
            save command itself was refused).
        """
        return await _save_library_verified(get_bridge(), library_path)

    @mcp.tool()
    async def lib_rename_footprints(
        renames: list[dict[str, str]],
        library_path: str = "",
    ) -> dict[str, Any]:
        """Rename MANY footprints in one PcbLib in ONE call.

        The batch sibling of ``lib_rename_footprint``: one PreProcess /
        PostProcess around all of them, and a status per rename instead of
        an error on the first bad one.

        Each result's ``status`` is ``renamed`` (written and read back) or
        why not: ``not_found``, ``name_exists`` (the new name is taken),
        ``same_name``, ``missing_field``, or ``unchanged`` (the write did
        not read back).

        Names are REFUSED, never sanitised, when the wire cannot carry
        them exactly: a ';' or '~~' (the batch separators) or a character
        above U+00FF (flattened to '?'). They come back under ``refused``
        and are not sent.

        MARKS the .PcbLib dirty; it does not write. ``lib_save_library``
        writes just this library and says whether it reached disk.

        Args:
            renames: ``[{"from": "SOT-23-6_P0,95", "to": "SOT-23-6_P0.95"}]``.
            library_path: optional .PcbLib path; defaults to the focused lib.

        Returns:
            ``{renamed, failed, total, results: [{old, new, status,
            read_back}], refused, pending_save, written_to_disk}``.
        """
        ops, refused = _rename_ops(renames)
        if not ops:
            first = (refused[0].get("reason") if refused
                     else "renames is empty")
            return {"ok": False,
                    "reason": f"no rename could be sent: {first}",
                    "renamed": 0, "refused": refused}
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.rename_footprints",
            {"library_path": library_path, "renames": "~~".join(ops)},
            timeout=_GEOMETRY_TIMEOUT)
        if isinstance(result, dict):
            result = dict(result)
            result["refused"] = refused
        return result

    @mcp.tool()
    async def lib_ipc_batch_generate(
        csv_paths: list[str],
        library_path: str,
        report_dir: str = "",
        allow_existing: bool = False,
        strict_preflight: bool = True,
        finalize_on_error: bool = False,
        close_reports: bool = True,
        open_report: Optional[bool] = None,
        may_steal_focus: bool = True,
        preflight_only: bool = False,
        dialog_timeout: float = 30.0,
        run_timeout: float = 180.0,
    ) -> dict[str, Any]:
        """Generate footprints with Altium's IPC Footprint Wizard batch mode.

        One call per batch instead of about ten per CSV. For each CSV in
        turn: refocus the library, open Tools > IPC Compliant Footprints
        Batch generator, confirm it writes into the CURRENT PcbLib, add
        the file, press Start, wait for the run and its report, close the
        form and the report document. Then once: save, reload, rename the
        generator's ',' names back to the '.' names the CSV asked for,
        save, reload, and read the names back from the reopened file.

        THE CSV is the template's own layout: row 0 is the template's row
        0 (its first cell, e.g. SOIC or SOT23, is how the generator
        detects the package), row 1 the column names, data after,
        dimensions in mm.

        PREFLIGHT, BEFORE ANYTHING IS SENT, refuses the traps measured on
        live runs: xls input (every number becomes a date), a blank
        PinOrder on SOT23 (a hard error there, though SOIC, SON and SOP
        accept one), a PinOrder like '1 -2 -3' (it became pad designators
        '1', '-2', '-3'), duplicate FootprintNames, and names already in
        the library (each stops the run on an 'already exists' prompt).
        ``preflight_only`` runs just this and lists the names a run would
        create.

        ONLY NAMES THIS CALL CREATED ARE RENAMED: a ',' name is renamed
        only when it is new AND its '.' form is a FootprintName in one of
        the CSVs, so a footprint that already had a comma is never
        touched.

        IT STOPS ON THE FIRST PROBLEM AND SAYS WHERE. ``stopped.stage``
        names the step, and any dialog on screen is reported, untouched:
        no button whose meaning is unknown is ever pressed. A stop while
        the batch form is up leaves the bridge blocked behind it
        (``stopped.bridge_blocked``); ``app_list_open_dialogs`` and
        ``app_press_dialog_button`` still work then. Footprints made
        before a stop stay UNSAVED in the editor, listed under ``unsaved``,
        unless ``finalize_on_error`` is set.

        THIS DRIVES A DIALOG. Its presses go to window handles and need no
        focus; only the fallback for entering the file name types into
        the file dialog, which brings Altium to the front
        (``may_steal_focus``). Leave Altium alone while it runs.

        Args:
            csv_paths: full paths of the batch CSVs, run one at a time.
            library_path: full path of the target .PcbLib, open in Altium
                and saved on disk.
            report_dir: a folder to look for the generator's report in.
                Usually unnecessary: the form's Output Folder is read off
                the form, and the folder of the last report found is
                remembered. That folder is sticky and is NOT the CSV's.
            allow_existing: run even when a CSV's names are already in the
                library; the generator's own 'already exists' prompt then
                stops the run and is left for a human.
            strict_preflight: refuse when preflight finds an error.
            finalize_on_error: after a stop, still save, reload and rename
                what was made.
            close_reports: close each report document after reading it.
            open_report: set the form's 'Open generated report' option;
                None leaves it alone. Altium remembers the setting.
            may_steal_focus: allow the keyboard fallback for the file name.
            preflight_only: check the CSVs and the library, run nothing.
            dialog_timeout: seconds to wait for each dialog to appear.
            run_timeout: seconds to allow one CSV's run.

        Returns:
            ``{ok, generated, renamed, report, runs, preflight, verify,
            stopped, ...}``. ``generated``: final names of the footprints
            this call added. ``renamed``: ``[{from, to, status}]``.
            ``report``: per CSV path, ``fatal``, ``errors``, ``warnings``
            and the generator's ``messages``. ``runs``: per CSV, what the
            form showed (options, Output Folder, buttons), how the file
            name went in, how the run ended, whether the bridge came back,
            and ``run_process_reply`` (what the RunProcess handler itself
            answered). An exception inside the dialog driver is a stop at
            stage ``dialog`` with ``error``, never a traceback; a complaint
            from the handler on a run that otherwise worked is listed
            under ``warnings``. A CSV listed twice is refused by preflight.
        """
        from . import uiauto as _uiauto
        from .ipc_batch_flow import _screen_report, run_batch
        from ..ui import ipc_batch_dialog, windows

        pid = 0
        if not preflight_only:
            pid, problem = _uiauto._altium_pid()
            if problem:
                return problem
            if not ipc_batch_dialog.available():
                return {"ok": False, "reason": (
                    "driving the batch generator needs pywin32 on Windows")}
            if windows.dialogs(pid) or windows.is_blocked(pid):
                reason = ("a dialog is already open in Altium. Close it "
                          "first: the run cannot tell its own dialogs from "
                          "one that was already there")
                # In an executor, as run_batch does it: the report can run
                # Windows OCR (about 2 s a dialog), which would stall the
                # MCP event loop if called inline. Through _screen_report,
                # as every stop does, so an OCR failure here is an entry in
                # the refusal, not a traceback in place of it.
                dialogs = await asyncio.get_running_loop().run_in_executor(
                    None, _screen_report, pid)
                return {"ok": False, "reason": reason,
                        "stopped": {"stage": "start", "reason": reason,
                                    "dialogs": dialogs}}
        return await run_batch(
            get_bridge(), pid, [str(p) for p in csv_paths], library_path,
            report_dir=report_dir, allow_existing=allow_existing,
            strict_preflight=strict_preflight,
            finalize_on_error=finalize_on_error,
            close_reports=close_reports, open_report=open_report,
            may_steal_focus=may_steal_focus, preflight_only=preflight_only,
            dialog_timeout=dialog_timeout, run_timeout=run_timeout)

    @mcp.tool()
    async def lib_probe_footprint(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Read-only dump of a PcbLib footprint's name-bearing fields.

        Use this to locate where an old name persists after a rename. On a
        PcbLib footprint, Name and Pattern are the SAME property (renaming Name
        writes both), and the footprint's only metadata is Name, Description,
        Height. So a leftover old name lives in a child primitive: this returns
        every text primitive's object_id and text, plus name/description/
        height_mils/primitive_count. Nothing is written.

        Args:
            footprint_name: the footprint name.
            library_path: optional .PcbLib path; defaults to the focused lib.

        Returns:
            {"success": true, "footprint": "...", "description": "...",
             "height_mils": N, "primitive_count": N,
             "texts": [{"object_id": N, "text": "..."}]}.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.probe_footprint",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_get_pad_geometry(
        footprint_name: str,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Full-precision pad geometry of one PcbLib footprint, in mm.

        The measurement half of the datasheet land-pattern audit. Unlike
        the integer-mil dump behind lib_audit_footprint_policies, every
        dimension here is a float in millimetres (a 0.65 mm pitch loses
        10 um to integer-mil rounding, which a tolerance comparison
        against the datasheet cannot afford). Coordinates are relative
        to the footprint's own origin.

        Args:
            footprint_name: the footprint name in the library.
            library_path: optional .PcbLib path; defaults to the
                focused library.

        Returns:
            {name, description, library_path, pad_count, pads: [{
             name, x_mm, y_mm, w_mm, h_mm, shape, corner_pct, rotation,
             hole_mm, hole_w_mm, hole_type, plated, layer,
             paste_expansion_mm, paste_expansion_source,
             mask_expansion_mm, mask_expansion_source}]}.
            *_expansion_source is 'manual' when the pad overrides the
            design rule, 'rule' otherwise (value 0 in that case: the
            rule's value lives in the design rules, not on the pad).
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_audit_footprint_vs_datasheet(
        footprint_name: str,
        spec_json: Any,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Audit a footprint against the manufacturer's land pattern.

        Deterministic comparison of the REAL pad geometry (read live
        from the .PcbLib) against a land-pattern spec transcribed from
        the manufacturer datasheet: pad count, every pad's position /
        size / shape / drill, the numbering sequence, pitch (implied by
        positions), the thermal pad, and its paste policy. Alignment is
        automatic (centroid + best of the four 90-degree rotations), so
        library origin and rotation conventions do not produce false
        errors; a MIRRORED pattern is deliberately not compensated and
        reports as position/sequence errors.

        DATASHEET DISCIPLINE: the spec must be transcribed from the
        manufacturer datasheet fetched in this conversation, and
        ``spec.source`` must cite it (url + figure/page + part number).
        Symbol metadata, distributor drawings, or memory of a package
        are not acceptable sources. There are no built-in package
        tables here, and that is intentional.

        The spec (JSON object or string):
          source: {datasheet_url, reference, part_number}   REQUIRED
          pads: [{name, x, y, w, h, shape?, hole?}]         explicit, mm
          dual_row: {count, pitch, span, pad_w, pad_h, shape?}
          quad: {count, pitch, span_x, span_y, pad_w, pad_h, shape?}
          thermal_pad: {name, x, y, w, h, shape?}
          thermal_paste: 'full' | 'windowed'
          position_tol / size_tol: mm, default 0.05

        dual_row expands with the SOIC/SOP convention (pin 1 top-left,
        CCW); quad with the QFP/QFN convention (pin 1 top of left side,
        CCW). For any other numbering give explicit ``pads``.

        Returns:
            {ok, rotation_applied_deg, findings: [{check, severity,
             message, pad?, expected?, actual?}]} plus the footprint
            geometry under 'footprint' for reference. ok is true only
            with zero error-severity findings.
        """
        import json as _json

        from eda_agent.design.footprint_audit import (
            LandPatternSpec,
            audit_footprint_against_spec,
        )

        if isinstance(spec_json, str):
            try:
                spec_json = _json.loads(spec_json)
            except _json.JSONDecodeError as e:
                return {"ok": False, "reason":
                        f"spec_json is not valid JSON ({e}); pass the "
                        "land-pattern spec object or its JSON text"}
        try:
            spec = LandPatternSpec.model_validate(spec_json)
        except Exception as e:                         # pydantic ValidationError
            return {"ok": False, "reason":
                    f"spec_json does not match the land-pattern spec "
                    f"schema: {e}"}

        bridge = get_bridge()
        footprint = await bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": footprint_name, "library_path": library_path},
        )
        report = audit_footprint_against_spec(spec, footprint)
        report["footprint"] = footprint
        report["spec_source"] = spec.source.model_dump()
        return report

    @mcp.tool()
    async def lib_easyeda_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Search LCSC / EasyEDA for parts by MPN or description.

        EXPECT THIS TO FAIL, and NOT because of credentials. Checked
        against the live service: the search route answers 404/403 with
        an HTML error page and no auth challenge, component fetch on the
        SAME host still works with no credentials, LCSC's own search API
        returns an error body, and the LCSC results page is rendered
        client-side (fetching it yields zero part numbers). There is no
        endpoint left to log in to, so no cookie, token or account will
        restore this.

        Search is therefore a BROWSER task, which also keeps it per-user:
        each person searches LCSC in their own signed-in browser, and no
        shared credential is ever stored by this server. Take the part
        number from there and import by id, which is unaffected:
        lib_easyeda_import(lcsc_id="C1234", ...).

        Online. Returns candidates only, no geometry: pick an lcsc_id
        and pass it to lib_easyeda_import. HTTPS-only with a host
        allowlist; override endpoints with EASYEDA_API_BASE.

        Args:
            query: MPN, keyword, or description fragment.
            limit: max candidates (default 20).

        Returns:
            {"ok": true, "count": N, "results": [{lcsc_id, mpn,
             manufacturer, package, description}]}.
        """
        from eda_agent.libimport.easyeda.fetch import (
            EasyEdaFetchError, search_components,
        )
        try:
            rows = await asyncio.to_thread(search_components, query, limit)
        except EasyEdaFetchError as exc:
            return {"ok": False, "reason": str(exc)}
        return {"ok": True, "count": len(rows), "results": rows}

    @mcp.tool()
    async def lib_kicad_import(
        symbol_path: str = "",
        footprint_path: str = "",
        symbol_name: str = "",
        target: str = "altium",
        schlib_path: str = "",
        pcblib_path: str = "",
        include_body_art: bool = True,
        unit: int = 0,
        model_3d_path: str = "",
    ) -> dict[str, Any]:
        """Convert a KiCad .kicad_sym / .kicad_mod into an Altium plan.

        Closes the gap that made KiCad-format part sources useless on
        the Altium side: this server could export Altium to KiCad but
        had no path back, so a hit from a KiCad registry or from the
        local KiCad libraries (see ``part_search``) was a dead end.

        Parses into the SAME neutral model the EasyEDA importer uses, so
        the Altium plan is produced by one shared emitter rather than a
        second copy that would drift from it.

        Give ``symbol_path``, ``footprint_path``, or both. Use
        ``symbol_name`` to pick one symbol out of a multi-symbol library
        (the first is used otherwise).

        MULTI-PART COMPONENTS: a quad gate or dual op-amp converts in
        ONE call to a real Altium multi-part symbol (``part_count`` plus
        per-pin ``owner_part_id``), not to N symbols to merge by hand.
        Set ``unit`` to a positive number to convert only that sub-part
        instead. Units are never merged into a flat symbol: they share
        coordinates by design, so a merged symbol has every unit's pins
        stacked on the same points and still looks converted.

        HIDDEN PINS: kept, and kept hidden. A hidden pin is
        electrically real (this is how symbols carry supply rails and
        no-connects), so dropping it would lose a pin while showing it
        would clutter the symbol.

        DERIVED SYMBOLS: over half of KiCad's standard entries carry no
        geometry of their own and inherit it from a parent. That link is
        followed, and ``warnings`` says where the geometry came from.

        target="altium": returns an ORDERED PLAN of this server's own
        library tools, exactly like lib_easyeda_import. Nothing is
        written to Altium; review the plan, then execute the steps.

        target="inspect": parse only, return a summary and warnings.
        Use this first on an unfamiliar library.

        DATASHEET DISCIPLINE: a converted footprint is somebody else's
        drawing, not a verified land pattern. Audit it against the
        manufacturer document with ``lib_audit_footprint_vs_datasheet``
        before trusting it.

        Returns:
            ``{"ok": ..., "component": {...}, "warnings": [...]}`` plus
            "steps"/"summary" for target="altium".
        """
        from ..libimport.easyeda.altium import build_altium_plan
        from ..libimport.kicad.reader import read_kicad_files

        if not symbol_path and not footprint_path:
            return {"ok": False,
                    "reason": "give symbol_path, footprint_path, or both"}

        for label, path in (("symbol_path", symbol_path),
                            ("footprint_path", footprint_path)):
            if path and not Path(path).is_file():
                return {"ok": False, "reason": f"{label} not found: {path}"}

        try:
            comp = read_kicad_files(
                symbol_path or None, footprint_path or None,
                symbol_name or None,
                unit=(int(unit) if int(unit or 0) > 0 else None))
        except (TypeError, ValueError, OSError) as exc:
            return {"ok": False, "reason": f"cannot read KiCad files: {exc}"}

        # A 3D body is only linked from a path the caller resolved, since
        # lib_link_3d_model loads the file: a guess would either fail at
        # execution or attach the wrong shape. part_fetch returns one in
        # model_3d_path when the installed KiCad ships it.
        if model_3d_path and comp.footprint is not None:
            if not Path(model_3d_path).is_file():
                return {"ok": False,
                        "reason": f"model_3d_path not found: "
                                  f"{model_3d_path}"}
            comp.footprint.model_3d_path = model_3d_path

        base: dict[str, Any] = {
            "ok": True,
            "component": comp.to_dict(),
            "warnings": list(comp.warnings),
        }

        mode = (target or "altium").strip().lower()
        if mode == "inspect":
            return base

        if mode != "altium":
            return {"ok": False,
                    "reason": f"unknown target {target!r}; use altium or "
                              f"inspect"}

        if not schlib_path or not pcblib_path:
            return {"ok": False,
                    "reason": "schlib_path and pcblib_path are required "
                              "for target=altium"}

        plan = build_altium_plan(
            comp, schlib_path, pcblib_path,
            include_body_art=include_body_art)
        if not plan["steps"]:
            return {"ok": False,
                    "reason": "the KiCad files carried no usable geometry",
                    "component": base["component"],
                    "warnings": plan["warnings"]}
        base["steps"] = plan["steps"]
        base["summary"] = plan["summary"]
        base["warnings"] = plan["warnings"]
        return base

    @mcp.tool()
    async def lib_easyeda_import(
        lcsc_id: str = "",
        payload_path: str = "",
        target: str = "altium",
        schlib_path: str = "",
        pcblib_path: str = "",
        output_dir: str = "",
        symbol_name: str = "",
        footprint_name: str = "",
        include_body_art: bool = True,
        fetch_3d: bool = True,
    ) -> dict[str, Any]:
        """Convert an EasyEDA / LCSC part to KiCad files or an Altium plan.

        Independent implementation from EasyEDA's published format
        spec, so symbol and footprint geometry, pads, drills, silkscreen
        and metadata all convert without any third-party converter.

        Source: ``lcsc_id`` fetches online (e.g. "C7420"), or
        ``payload_path`` reads a saved component JSON, which keeps the
        whole conversion offline and reproducible.

        target="kicad": writes ``<name>.kicad_sym`` and
        ``<name>.kicad_mod`` into ``output_dir`` and returns their paths.
        With ``fetch_3d`` (default on) the part's 3D model is fetched and
        converted to ``<name>.wrl``, which the footprint then references.
        Altium needs STEP and cannot use VRML, so this is KiCad only.

        target="altium": returns an ORDERED PLAN of this server's own
        library tools (lib_create_symbol, lib_add_pins,
        lib_create_footprint, lib_add_footprint_pads, lib_link_footprint,
        ...) that recreates the part in ``schlib_path`` / ``pcblib_path``.
        Nothing is written to Altium: review the plan, then execute the
        steps (tool_invoke drives them directly). Altium's binary library
        formats are never synthesized, the bridge authoring API is used
        instead.

        target="inspect": parse only, return the component summary and
        warnings. Use this first on an unfamiliar part.

        DATASHEET DISCIPLINE: an imported footprint is a vendor drawing,
        not ground truth. Audit it with
        ``lib_audit_footprint_vs_datasheet`` against the manufacturer
        land pattern before trusting it. Polygon pads and slotted holes
        have no faithful equivalent and are reported in ``warnings``.

        Returns:
            {"ok": ..., "component": {...}, "warnings": [...]} plus
            "files" (kicad) or "steps"/"summary" (altium).
        """
        import json as _json

        from eda_agent.libimport.easyeda import (
            build_altium_plan, footprint_to_kicad_mod, parse_component,
            symbol_to_kicad_sym,
        )
        from eda_agent.libimport.easyeda.fetch import (
            EasyEdaFetchError, fetch_component_json,
        )

        if not lcsc_id and not payload_path:
            return {"ok": False,
                    "reason": "give lcsc_id (online) or payload_path (offline)"}
        try:
            if payload_path:
                payload = _json.loads(
                    Path(payload_path).read_text(encoding="utf-8"))
            else:
                payload = await asyncio.to_thread(
                    fetch_component_json, lcsc_id)
        except EasyEdaFetchError as exc:
            return {"ok": False, "reason": str(exc)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "reason": f"cannot read payload: {exc}"}

        comp = parse_component(payload)
        base = {"ok": True, "component": comp.to_dict(),
                "warnings": comp.warnings}

        mode = (target or "altium").strip().lower()
        if mode == "inspect":
            return base

        if mode == "kicad":
            if not output_dir:
                return {"ok": False, "reason": "output_dir is required "
                                               "for target=kicad"}
            out = Path(output_dir)
            try:
                out.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                return {"ok": False, "reason": f"cannot create "
                                               f"{output_dir}: {exc}"}
            stem = _safe_filename(
                symbol_name or comp.mpn or comp.lcsc_id or "easyeda_part",
                "easyeda_part")
            files: dict[str, str] = {}
            if comp.symbol is not None:
                p = out / f"{stem}.kicad_sym"
                p.write_text(symbol_to_kicad_sym(comp), encoding="utf-8")
                files["symbol"] = str(p)
            if comp.footprint is not None:
                model_ref = None
                if fetch_3d and comp.footprint.model_3d_uuid:
                    # EasyEDA serves an OBJ-family payload with the
                    # material library inlined; model3d converts it to
                    # the VRML KiCad renders natively.
                    try:
                        from eda_agent.libimport.easyeda.fetch import (
                            fetch_3d_model,
                        )
                        from eda_agent.libimport.easyeda.model3d import (
                            obj_to_wrl, parse_easyeda_obj,
                        )
                        raw3d = fetch_3d_model(
                            comp.footprint.model_3d_uuid)
                        mdl = parse_easyeda_obj(
                            raw3d.decode("utf-8", "replace"))
                        wrl_name = _safe_filename(
                            comp.footprint.model_3d_name or stem, stem
                        ) + ".wrl"
                        wp = out / wrl_name
                        wp.write_text(obj_to_wrl(
                            mdl, name=comp.footprint.name), encoding="utf-8")
                        files["model_3d"] = str(wp)
                        model_ref = f"${{KIPRJMOD}}/{wrl_name}"
                        comp.warnings.extend(mdl.warnings)
                    except (EasyEdaFetchError, ValueError, OSError) as exc:
                        # A missing 3D model must not lose the symbol and
                        # footprint that already converted cleanly.
                        comp.warnings.append(
                            f"3D model could not be converted: {exc}")
                fp_stem = _safe_filename(
                    footprint_name or comp.footprint.name, stem)
                p = out / f"{fp_stem}.kicad_mod"
                p.write_text(footprint_to_kicad_mod(
                    comp, model_path=model_ref), encoding="utf-8")
                files["footprint"] = str(p)
            if not files:
                return {"ok": False, "reason": "payload had no geometry",
                        "warnings": comp.warnings}
            base["files"] = files
            return base

        if mode == "altium":
            if not schlib_path or not pcblib_path:
                return {"ok": False,
                        "reason": "schlib_path and pcblib_path are required "
                                  "for target=altium"}
            plan = build_altium_plan(
                comp, schlib_path, pcblib_path,
                symbol_name=symbol_name or None,
                footprint_name=footprint_name or None,
                include_body_art=include_body_art,
            )
            if not plan["steps"]:
                # An empty plan with ok=True reads as success, and the
                # caller then executes nothing and believes the part was
                # imported. The kicad branch already refuses this; keep
                # both targets honest about a payload with no geometry.
                return {"ok": False,
                        "reason": "payload had no symbol or footprint "
                                  "geometry, so there is nothing to build",
                        "component": base.get("component"),
                        "warnings": plan["warnings"]}
            base["steps"] = plan["steps"]
            base["summary"] = plan["summary"]
            # build_altium_plan already appends a warning for any text
            # the bridge will flatten, so both importers get it and
            # neither can drift.
            base["warnings"] = plan["warnings"]
            return base

        return {"ok": False,
                "reason": f"unknown target {target!r}; "
                          f"use kicad, altium, or inspect"}

    @mcp.tool()
    async def lib_clear_source_library(
        library_path: str = "",
        component_names: Optional[list[str]] = None,
        clear_target_file_name: bool = True,
        sync_design_item_id: bool = True,
    ) -> dict[str, Any]:
        """Unpin a SchLib's symbols from their source-library provenance.

        Library-side sibling of ``sch_clear_source_library``. Symbols
        copied in from another library (a vendor pack, a stock library
        like the built-in miscellaneous-devices set) carry three
        provenance fields pointing at their ORIGIN: SourceLibraryName,
        TargetFileName, and DesignItemId. Left stale, every placement
        from your library re-links against a library that no longer
        exists on the machine and lands in the <Not Found> state.

        Per matching component: clears SourceLibraryName, resets
        TargetFileName to '*', and syncs DesignItemId to the
        LibReference (each independently switchable). This is the
        minimal fast path of what lib_normalize_implementations does
        inside its full model sweep, when you only need the provenance
        cleaned, use this. Deferred save: flush with lib_save_library.

        Args:
            library_path: absolute .SchLib path; empty = focused library.
            component_names: restrict to these LibReferences; None/empty
                = every component in the library.
            clear_target_file_name: reset TargetFileName to '*'.
            sync_design_item_id: set DesignItemId = LibReference where
                they differ.

        Returns:
            {"library_path": ..., "total": inspected,
             "cleared_source_library": n, "cleared_target_file_name": n,
             "synced_design_item_id": n}.
        """
        bridge = get_bridge()
        params: dict[str, Any] = {
            "library_path": library_path,
            "clear_target_file_name":
                "true" if clear_target_file_name else "false",
            "sync_design_item_id":
                "true" if sync_design_item_id else "false",
        }
        if component_names:
            # Comma-separated field; a LibReference containing a comma
            # arrives as two names and a fragment can match a real
            # component whose provenance this tool then rewrites.
            # Refuse rather than strip.
            bad = [n for n in component_names if "," in str(n)]
            if bad:
                return {"ok": False, "reason":
                        f"component names {bad} contain a comma, which "
                        "is the wire-format separator; fragments could "
                        "match other components, so the call is refused"}
            params["component_names"] = ",".join(component_names)
        return await bridge.send_command_async(
            "library.clear_source_library", params,
        )

    @mcp.tool()
    async def lib_normalize_implementations(
        library_path: str = "",
        rename_map: Optional[dict[str, str]] = None,
        dedupe_only: bool = False,
        source_library: str = "",
        use_component_library: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Clean up every component's models in a SchLib for a self-contained package.

        Whole-library sweep. Per component it: clears the component-level
        provenance (SourceLibraryName, TargetFileName); collapses duplicate
        models, keeping ONE per (model_type, model_name) IN PLACE so the kept
        model's parameters, MapAsString pin-map, and datafile link are all
        preserved; re-flags the survivor current if any duplicate was;
        optionally renames a model's name and its datafile entity via
        rename_map; and repairs any footprint (PCBLIB) left with no datafile
        link. It does NOT destroy and rebuild implementations, so nothing on a
        model is lost.

        With source_library set, it also writes that value into the Location
        of every PCBLIB model's datafile link, turning a name-only library
        (footprints resolve but do not embed) into an embeddable one in the
        same pass. Existing Locations are only overwritten when source_library
        is given; they are never blanked. Get the exact string from a
        known-good model first (see lib_set_model_source).

        Note: it preserves what is present; it cannot resurrect data a prior
        broken run already dropped. If a library was damaged by an earlier
        version, restore the backup and run this on it.

        If you are also renaming footprint entities, run lib_rename_footprint
        on the PcbLib first, then pass the same {old: new} as rename_map so
        symbol and footprint stay matched.

        Args:
            library_path: optional .SchLib path; defaults to the focused lib.
            rename_map: optional {old_model_name: new_model_name} applied to
                the kept model's name and matching datafile entity.
            dedupe_only: skip renames; only collapse duplicates + clear source.
            source_library: optional; when set, written into every PCBLIB
                model's datafile Location so footprints embed on compile.
            use_component_library: optional; sets the embed-vs-search flag on
                the models whose Location is written.

        Returns:
            {"success": true, "components_touched": N, "duplicates_removed": N,
             "sources_cleared": N, "links_repaired": N, "sources_set": N}.
        """
        pairs = ";".join(f"{k}={v}" for k, v in (rename_map or {}).items())
        params: dict[str, Any] = {
            "library_path": library_path,
            "rename_map": pairs,
            "dedupe_only": "true" if dedupe_only else "false",
            "source_library": source_library,
        }
        if use_component_library is not None:
            params["use_component_library"] = (
                "true" if use_component_library else "false"
            )
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.normalize_implementations", params
        )

    @mcp.tool()
    async def lib_read_pcblib_file(
        library_path: str,
        footprint_name: str = "",
        offset: int = 0,
        limit: int = 25,
    ) -> dict[str, Any]:
        """Read a SAVED .PcbLib straight from disk: the persistence proof.

        PURE PYTHON, NO ALTIUM. Every bridge read-back answers from
        Altium's memory, and memory and file have disagreed: on
        2026-10-04 a pad the delete reported gone was written back by the
        next save, and a 3D body whose self-check passed was stored
        50,000 mil from its footprint. Only the file showed either. So
        after ``lib_delete_footprint_pads``,
        ``lib_delete_footprint_primitives``, ``lib_modify_footprint_pads``
        or ``lib_link_3d_model``, run ``lib_save_library`` and then this:
        what it lists is what reached disk. No reload is needed, and it
        works with Altium closed.

        COORDINATES ARE MILS RELATIVE TO THE FOOTPRINT ORIGIN, exactly as
        the file stores them (1/10000 mil resolution), so they compare
        directly with the x/y the lib_ tools take.

        Args:
            library_path: full path of the .PcbLib on disk. Altium may
                have it open; the file is only read.
            footprint_name: one footprint (case-insensitive exact name).
                Empty lists every footprint, paged.
            offset: first footprint of a page (name order), when
                footprint_name is empty.
            limit: footprints per page, when footprint_name is empty.

        Returns:
            ``{ok, library_path, total, units: "mils", frame, found?,
            closest?, offset?, count?, incomplete, warning?, footprints:
            [{name, storage, complete, counts, pads, tracks, arcs, vias,
            fills, texts, regions, bodies, other_records, parse_error?}]}``.

            ``complete`` is False when a footprint's records could not all
            be read: a record type whose layout is not known (the parse
            STOPS there rather than guess its length, because a wrong guess
            would misread every later record), or a truncated stream.
            ``incomplete`` lists those names; with ``footprint_name`` an
            incomplete footprint makes ``ok`` False, because a partial
            list proves nothing about what the file holds.

            pads: ``designator, x_mils, y_mils, x_size_mils, y_size_mils,
            shape (round | rectangular | octagonal | roundrect),
            corner_pct, rotation, hole_mils, hole_type (round | square |
            slot), slot_length_mils, hole_rotation, plated, layer,
            paste_expansion_mils, mask_expansion_mils`` (the expansions
            are null when the pad follows the design rule).
            tracks: ``layer, x1_mils, y1_mils, x2_mils, y2_mils,
            width_mils``. arcs: ``layer, x_mils, y_mils, radius_mils,
            start_angle, end_angle, width_mils``. fills: ``layer,
            x1_mils .. y2_mils, rotation, is_keepout``. texts: ``text,
            layer, x_mils, y_mils, height_mils, rotation,
            stroke_width_mils``.
            regions: ``layer, layer_v7, kind, is_keepout,
            keepout_restrictions (the KEEPOUTRESTRICTIONS bitmask,
            31 = all; null on a region that is not a keep-out; 31 on a
            keep-out whose record stores none, which is how Altium reads
            it), keepout_restrictions_defaulted (true in that last case),
            keepout_restricts (its bits as via | track | copper | smd_pad
            | th_pad), vertex_count, bbox_mils``. ``is_keepout`` is the record header's keep-out
            flag, which is where Altium stores it.
            Every ``layer`` uses one spelling (``TopLayer``,
            ``TopSolder``, ``Mechanical13``, ``MultiLayer``...);
            ``layer_v7`` is the region's or body's name as stored
            (``TOPSOLDER``, ``MECHANICAL13``).
            bodies: ``layer, layer_v7, standoff_height_mils, overall_height_mils,
            outline_bbox_mils, model: {name, type (extruded | generic |
            cylinder | sphere), type_id, x_mils, y_mils (MODEL.2D.X/Y:
            the model origin relative to the footprint origin),
            planar_rotation, rot_x, rot_y, rot_z (MODEL.3D.ROTX/Y/Z),
            dz_mils (MODEL.3D.DZ, the Z offset), embedded}``.
            ``ok`` False with ``reason`` when the file is missing, not a
            PcbLib, or the footprint is not in it (``closest`` names).
        """
        from ..fileio.altium_pcblib import read_pcblib

        path = str(library_path or "").strip()
        if not path:
            return {"ok": False, "reason": "library_path is required"}
        if not Path(path).is_file():
            return {"ok": False, "library_path": path,
                    "reason": f"no file at {path}"}
        try:
            offset = max(0, int(offset))
            limit = int(limit)
        except (TypeError, ValueError):
            return {"ok": False,
                    "reason": "offset and limit must be whole numbers"}
        if limit <= 0:
            limit = 25
        try:
            result = read_pcblib(path, str(footprint_name or "").strip(),
                                 offset=offset, limit=limit)
        except ValueError as exc:
            return {"ok": False, "library_path": path, "reason": str(exc)}
        except OSError as exc:
            return {"ok": False, "library_path": path, "reason": (
                f"the file could not be read: {exc}")}
        except Exception as exc:                 # noqa: BLE001
            # A damaged compound file can fail anywhere in olefile; that is
            # an answer about the file, not a traceback in place of one.
            return {"ok": False, "library_path": path, "reason": (
                f"the file could not be parsed as a PcbLib: "
                f"{type(exc).__name__}: {exc}")}
        if str(footprint_name or "").strip() and not result.get("found"):
            result["ok"] = False
            result["reason"] = (
                f"footprint {footprint_name!r} is not in the saved file. If "
                f"it was created this session, it reaches the file only when "
                f"lib_save_library writes it")
            return result
        incomplete = result.get("incomplete") or []
        if incomplete and str(footprint_name or "").strip():
            # One footprint asked for, and its records were not all read:
            # a partial list is not a proof of what the file holds.
            result["ok"] = False
            result["reason"] = (
                f"footprint {incomplete[0]!r} could not be read to the end: "
                f"{(result['footprints'][0] or {}).get('parse_error')}. What "
                f"is listed is only the records before that point, so it "
                f"proves nothing about the rest")
            return result
        result["ok"] = True
        if incomplete:
            result["warning"] = (
                f"{len(incomplete)} footprint(s) on this page could not be "
                f"read to the end (see each one's parse_error); their lists "
                f"are partial: {', '.join(incomplete[:10])}")
        return result

    @mcp.tool()
    async def lib_inspect_cse_zip(zip_path: str) -> dict[str, Any]:
        """Identify the library members of a Component Search Engine zip.

        Stage 5 of the autonomous flow (library readiness,
        reference/autonomy-roadmap.md): a SamacSys / Component Search
        Engine download carries the symbol, footprint and 3D model for an
        MPN in one zip. This tool reads the archive WITHOUT extracting
        anything: which member is the .SchLib, which the .PcbLib, which
        the STEP model, the best-effort MPN, and any members attempting
        path traversal (``suspicious``). Inspect first, then stage the
        files with ``lib_extract_cse_zip``. Pure Python, no Altium.

        Args:
            zip_path: Absolute path to the downloaded zip.

        Returns:
            ``{"ok": True, "mpn", "schlib", "pcblib", "step", "extras",
            "suspicious"}`` (member names; None when absent) or
            ``{"ok": False, "reason": "..."}`` when the file is missing,
            not a zip, or contains no .SchLib/.PcbLib.
        """
        return inspect_cse_zip(zip_path)

    @mcp.tool()
    async def lib_extract_cse_zip(
        zip_path: str,
        dest_dir: str = "",
    ) -> dict[str, Any]:
        """Extract a Component Search Engine zip and build its install plan.

        Stage 5 of the autonomous flow (library readiness,
        reference/autonomy-roadmap.md): stages the recognized members
        (.SchLib / .PcbLib / STEP) flattened into ``dest_dir`` and returns
        an ordered ``install_plan`` whose steps are exact
        ``lib_install_library`` / ``lib_link_footprint`` /
        ``lib_link_3d_model`` parameter dicts -- dispatch them in order to
        make the part placeable. Any archive member with an absolute path,
        drive letter, or ``..`` segment rejects the WHOLE archive before
        anything is written. Extraction is pure Python; only the install
        plan touches Altium when dispatched.

        Args:
            zip_path: Absolute path to the downloaded zip.
            dest_dir: Where to stage the files. Default:
                ``<workspace>/cse_imports/<zip stem>``.

        Returns:
            ``{"ok": True, "mpn", "files": [abs paths], "extracted":
            {"schlib"/"pcblib"/"step": abs path or None}, "install_plan":
            [{"tool", "params"}]}`` or ``{"ok": False, "reason": "..."}``.
        """
        dest = (
            Path(dest_dir)
            if dest_dir
            else get_config().workspace_dir / "cse_imports"
            / Path(zip_path).stem
        )
        return extract_cse_zip(zip_path, dest)
