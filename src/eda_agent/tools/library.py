# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library management tools for Altium Designer MCP Server."""

import asyncio
import difflib
import math
import re
from pathlib import Path
from typing import Any, Optional
from ..bridge import get_bridge
from ..bridge.payload import (MAX_COORD_MILS, finite_number, join_ops,
                              name_wire_problem, non_finite, payload_safe,
                              unsendable_chars, whole_number, wire_bool)
from ..fileio import pcblib_models
from ..libimport import extract_cse_zip, inspect_cse_zip
from ..library_db import (
    dblib_path_refusal, is_dblib_path, redact_connection_text, redact_reply,
    search_hit_as_result,
)
from .bulk_hints import BulkHintTracker
from .datasheet_hints import tag_response
from ..config import get_config
from ..units import format_length, length_in, normalise_units
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


def _step_solid_count(path: str) -> Optional[int]:
    """Solids in a STEP file, or None when it cannot be read."""
    try:
        text = Path(path).read_text(encoding="latin-1", errors="ignore")
    except OSError:
        return None
    return (text.count("MANIFOLD_SOLID_BREP(")
            + text.count("BREP_WITH_VOIDS("))


async def _remove_old_bodies(bridge, footprint: str) -> dict:
    """Take a footprint's 3D bodies off before a new model is linked.

    Linking adds a body; it never replaced one. A follow-up take rebuilt
    every footprint's model and each kept its old plain box beside the
    new one, removed afterwards by hand.

    The removal PARKS the footprint (``park_current``): a primitive removed
    while its footprint is current was MEASURED (2026-10-05,
    docs/RELEASE_VERIFICATION.md step 31 item 3b) to come back with the
    next save, so the old box would return beside the new model. The link
    that follows makes the footprint current again to add (the same
    delete-then-link route as ``_replace_then_link``).
    """
    if not footprint.strip():
        return {"skipped": "no footprint name, so the current one's bodies "
                           "were left as they are"}
    try:
        out = await bridge.send_command_async(
            "library.delete_footprint_primitives",
            {"footprint_name": footprint, "object_type": "component_body",
             "confirm": "true", "park_current": "true"})
    except Exception as exc:                    # noqa: BLE001
        return {"error": str(exc)}
    if isinstance(out, dict):
        return {"removed": out.get("removed", 0), "leftover": out.get("leftover", 0)}
    return {"removed": 0}


async def _save_library(bridge, library_path) -> tuple[bool, str]:
    """Write ONE library to disk: (saved, reason).

    These passes used application.save_all, which writes every open
    document, so fixing one library's designators rewrote sheets and
    boards in other projects (issue #94). save_document measures the save
    by the file getting newer, and file times tick every two seconds, so
    a save right after the previous one is tried once more.
    """
    params = {"file_path": library_path} if library_path else {}
    reply: dict = {}
    for attempt in range(2):
        reply = await bridge.send_command_async(
            "application.save_document", params, timeout=_GEOMETRY_TIMEOUT)
        reply = reply if isinstance(reply, dict) else {}
        if reply.get("saved"):
            return True, ""
        if attempt == 0:
            await asyncio.sleep(2.1)
    return False, str(reply.get("reason") or "the library did not get newer on disk")


async def _open_document(bridge, path: str) -> str:
    """Make sure Altium has ``path`` open as a document; '' or the problem.

    Focused when it is open already; opened (WorkspaceManager:OpenObject)
    and focused when it is not.
    """
    from ..bridge.exceptions import AltiumError

    async def focus() -> str:
        try:
            await bridge.send_command_async(
                "application.set_active_document", {"file_path": path})
            return ""
        except AltiumError as exc:
            return str(exc)

    problem = await focus()
    if problem and "not loaded" in problem.lower():
        try:
            await bridge.send_command_async("application.run_process", {
                "process_name": "WorkspaceManager:OpenObject",
                "parameters": f"ObjectKind=Document|FileName={path}"})
        except AltiumError as exc:
            return f"could not open {path}: {exc}"
        problem = await focus()
    return f"could not open {path}: {problem}" if problem else ""


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
    "splitplaneobject", "splitplane",
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


def _len(v: object, units: str = "mil") -> str:
    """A length for the wire in the call's unit.

    Mils go through :func:`_mil`, 1/10000 mil, which IS Altium's internal
    resolution, so a mil payload is exactly what it always was. Millimetres
    go through format_length (six places, never an exponent): rounding a
    millimetre to four places would be 0.1 um, coarser than Altium.
    """
    return format_length(v) if normalise_units(units) == "mm" else _mil(v)


def _units_refusal(units: str) -> dict[str, Any] | None:
    """The reply for a unit the authoring tools do not take, or None."""
    if normalise_units(units) is None:
        return {"error": f"units must be 'mil' or 'mm', not {units!r}"}
    return None


def _with_units(params: dict[str, Any], units: str) -> dict[str, Any]:
    """Name millimetres on the wire; mils, the default, are left implicit
    so a call that never mentions units sends what it always sent."""
    if normalise_units(units) == "mm":
        params["units"] = "mm"
    return params


def corner_pct_from_radius(radius: float, x_size: float, y_size: float) -> int:
    """Altium's roundrect percentage for an absolute corner radius.

    Altium has no setter for the absolute radius: it stores a percentage
    (0..100) of half the pad's shortest side, so a pad resized later keeps
    the percentage and its radius changes with it."""
    half = min(float(x_size), float(y_size)) / 2.0
    if half <= 0:
        return 0
    return max(0, min(100, round(100.0 * float(radius) / half)))


def _pad_corner_pct(p: dict[str, Any], units: str) -> int:
    """A pad dict's corner percentage; corner_radius_length wins when given."""
    if p.get("corner_radius_length") is not None:
        return corner_pct_from_radius(
            p["corner_radius_length"], p.get("x_size", length_in(units, 60)),
            p.get("y_size", length_in(units, 60)))
    return round(p.get("corner_radius", 25))


def _target_refusal(component_name: Optional[str],
                    footprint_name: Optional[str]) -> dict[str, Any] | None:
    """The reply when a footprint tool is given two different names.

    ``component_name`` and ``footprint_name`` name the same thing (the
    Pascal resolver reads component_name, then footprint_name), so two
    different values cannot both be honoured and neither is guessed.
    """
    a = (component_name or "").strip()
    b = (footprint_name or "").strip()
    if a and b and a.upper() != b.upper():
        return {"error": (f"component_name {a!r} and footprint_name {b!r} "
                          "name different footprints; pass one")}
    return None


def _add_target(params: dict[str, Any], component_name: Optional[str],
                footprint_name: Optional[str],
                library_path: Optional[str]) -> dict[str, Any]:
    """Name the footprint and library on the wire, each only when given,
    under the key the caller used, so an unnamed call keeps its old shape."""
    if component_name:
        params["component_name"] = component_name
    elif footprint_name:
        params["footprint_name"] = footprint_name
    if library_path:
        params["library_path"] = library_path
    return params


#: Every numeric field a pad dict may carry, checked before formatting.
#: hole_length is the land-pattern builder's name for a slot's length
#: (the Pascal makes a slot when it exceeds hole_size); slot_length is the
#: original field, sent with hole_type="slot".
_PAD_NUMBER_KEYS = ("x", "y", "x_size", "y_size", "hole_size",
                    "corner_radius", "rotation", "slot_length", "hole_length",
                    "mask_expansion", "paste_expansion", "hole_rotation",
                    "corner_radius_length")


def _pads_payload(pads: list[dict[str, Any]],
                  units: str = "mil",
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
        # corner_radius_length: the radius as a length, turned into
        # Altium's percentage of half the shortest side.
        corner = (_pad_corner_pct(p, units)
                  if p.get("corner_radius_length") is not None
                  else whole_number(p.get("corner_radius", 25)))
        if corner is None or not 0 <= corner <= 100:
            skipped_invalid += 1
            continue
        fields = [
            f"designator={desig}",
            # Lengths in ``units`` (_len): round() here quantised every
            # millimetre land pattern to whole mils before it left Python.
            f"x={_len(p.get('x', 0), units)}",
            f"y={_len(p.get('y', 0), units)}",
            f"x_size={_len(p.get('x_size', length_in(units, 60)), units)}",
            f"y_size={_len(p.get('y_size', length_in(units, 60)), units)}",
            f"hole_size={_len(p.get('hole_size', 0), units)}",
            f"shape={_payload_safe(p.get('shape', 'rectangular'))}",
            f"corner_radius={corner}",
            # Formatted, never sent raw: "1_000" passes float() and reads
            # as 0 in StrToFloatDef.
            f"rotation={_mil(p.get('rotation', 0))}",
            f"layer={_payload_safe(p.get('layer', 'TopLayer'))}",
        ]
        # Optional pad features, emitted only when given, so every payload
        # without them is byte-identical to what it always was. Lengths in
        # ``units``, unrounded like the required ones.
        if plated is not None:
            fields.append(f"plated={'true' if plated else 'false'}")
        if p.get('hole_type'):
            fields.append(f"hole_type={_payload_safe(p['hole_type'])}")
        for key in ('slot_length',) + _OPTIONAL_PAD_LENGTHS:
            if p.get(key) is not None:
                fields.append(f"{key}={_len(p[key], units)}")
        # A slot given without hole_rotation runs along the pad's longer
        # side (_slot_rotation); a given rotation is sent as it is.
        turn = (float(p['hole_rotation']) if p.get('hole_rotation') is not None
                else _slot_rotation(p, units))
        if turn is not None:
            fields.append(f"hole_rotation={format_length(turn)}")
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


def _unplated(plated: Any) -> bool:
    if plated is None:
        return False
    if isinstance(plated, str):
        return plated.strip().lower() in ("false", "0", "no")
    return not plated


def _slot_rotation(p: dict[str, Any], units: str) -> Optional[float]:
    """The slot's turn from the pad's X axis, or None for no slot.

    Altium runs a slot along the pad's X. A slot given without
    ``hole_rotation`` runs along the pad's longer side, so a pad taller
    than it is wide has its slot turned 90 degrees. A USB-C receptacle's
    shell pads (1.1 x 1.9 mm with a 1.4 mm slot, 1.1 x 1.6 mm with a
    1.1 mm one) came out with their slots across the pads.
    """
    try:
        hole = float(p.get("hole_size") or 0)
        slot = float(p.get("hole_length") or 0)
    except (TypeError, ValueError):
        return None
    if not (hole > 0 and slot > hole):
        return None
    if p.get("hole_rotation") is not None:
        return float(p["hole_rotation"])
    xs = float(p.get("x_size", length_in(units, 60)))
    ys = float(p.get("y_size", length_in(units, 60)))
    return 90.0 if ys > xs else 0.0


def _tracks_payload(tracks: list[dict[str, Any]],
                    units: str = "mil") -> tuple[str, int]:
    """Build the ``tracks`` batch payload, and count what was dropped.

    Sibling of :func:`_pads_payload`. Coordinates and width go through
    ``_mil``, so a 12.5 mil courtyard line arrives as 12.5 rather than the
    whole mil ``round()`` used to make of it. The Pascal reads them with
    StrToFloatDef now, which is what makes the decimal meaningful: under
    StrToIntDef a decimal did not round, it fell back to the DEFAULT.

    A track missing an endpoint, carrying a non-number, or with a width
    that is not positive is dropped and counted, never sent as zeros.

    Lengths are in ``units`` ("mil" default, or "mm") and go out
    through _len (1/10000 mil, or exact millimetres); an omitted width is 10 mil in
    the caller's unit. Shared by lib_add_footprint_tracks and the
    package footprint tool so the two cannot format a track
    differently.
    """
    op_strs: list[str] = []
    skipped_invalid = 0
    for t in tracks:
        if not isinstance(t, dict):
            skipped_invalid += 1
            continue
        coords = [_num(t.get(k)) for k in ("x1", "y1", "x2", "y2")]
        width = _num(t.get("width", length_in(units, 10)))
        if any(c is None for c in coords) or width is None or width <= 0:
            skipped_invalid += 1
            continue
        fields = [
            f"x1={_len(coords[0], units)}", f"y1={_len(coords[1], units)}",
            f"x2={_len(coords[2], units)}", f"y2={_len(coords[3], units)}",
            f"width={_len(width, units)}",
            f"layer={_payload_safe(t.get('layer') or 'TopOverlay')}",
        ]
        op_strs.append(";".join(fields))
    # join_ops: a layer ending in '~' cost the next track its x1.
    return join_ops(op_strs), skipped_invalid


def _fills_payload(
        fills: list[dict[str, Any]],
        units: str = "mil") -> tuple[str, list[dict[str, Any]]]:
    """Build the ``fills`` batch payload, and say what was dropped and why.

    Returns ``(payload, skipped)`` where each skipped entry names the
    fill's index and the reason, the shape the Pascal's own ``failures``
    use, so a caller reads both lists the same way.

    Each op goes out layer first, then the corners in ``units`` unrounded
    (:func:`_len`), then ``rotation`` only when it is not zero and
    ``keepout=true`` only when asked: upstream's wire form. The Pascal reads
    every field by name and defaults rotation to 0.

    LAYER IS REQUIRED. A track defaults to silkscreen because a stray silk
    line is harmless; a fill defaulted to anything would be copper or paste
    that nobody asked for.
    """
    op_strs: list[str] = []
    skipped: list[dict[str, Any]] = []
    for i, f in enumerate(fills or []):
        if not isinstance(f, dict):
            skipped.append({"item": i, "reason": "is not an object"})
            continue
        coords = [_num(f.get(k)) for k in ("x1", "y1", "x2", "y2")]
        if any(c is None for c in coords):
            skipped.append({"item": i,
                            "reason": "needs numeric x1, y1, x2, y2"})
            continue
        if coords[0] == coords[2] or coords[1] == coords[3]:
            skipped.append({"item": i, "reason": "has no area"})
            continue
        layer = str(f.get("layer") or "").strip()
        if not layer:
            skipped.append({"item": i, "reason": "has no layer"})
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
            f"layer={_payload_safe(layer)}",
            f"x1={_len(coords[0], units)}", f"y1={_len(coords[1], units)}",
            f"x2={_len(coords[2], units)}", f"y2={_len(coords[3], units)}",
        ]
        if rotation:
            fields.append(f"rotation={_mil(rotation)}")
        if keepout:
            fields.append("keepout=true")
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped


#: Region kinds the Pascal accepts. Anything else is refused on this side,
#: because the alternative is a copper region the caller did not ask for.
_REGION_KINDS = ("copper", "cutout", "keepout")


def _vertex(point: object) -> Optional[tuple[float, float, float]]:
    """One vertex as ``(x, y, sweep)``, from ``[x, y]``, ``[x, y, sweep]``
    or ``{"x": .., "y": .., "sweep": ..}``. A sweep of 0 (the default) is a
    straight edge to the next vertex; any other is a true arc of that many
    degrees, counterclockwise when positive. None when any of the three is
    not a finite number."""
    s: Optional[float] = 0.0
    if isinstance(point, dict):
        x, y = _num(point.get("x")), _num(point.get("y"))
        if point.get("sweep") is not None:
            s = _num(point.get("sweep"))
    elif isinstance(point, (list, tuple)) and len(point) in (2, 3):
        x, y = _num(point[0]), _num(point[1])
        if len(point) == 3:
            s = _num(point[2])
    else:
        return None
    if x is None or y is None or s is None:
        return None
    return x, y, s


def _regions_payload(
        regions: list[dict[str, Any]],
        units: str = "mil") -> tuple[str, list[dict[str, Any]]]:
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
        # "points" is the land-pattern builder's key, sent in the
        # points=x,y/x,y form the Pascal also reads; "vertices" is the
        # tool's, sent as pts=. Same checks either way.
        as_points = r.get("vertices") is None and r.get("points") is not None
        raw = r.get("points") if as_points else r.get("vertices")
        if not isinstance(raw, (list, tuple)):
            skipped.append({"item": i, "reason": "vertices must be a list of "
                                                  "[x, y] points"})
            continue
        pts = [_vertex(p) for p in raw]
        if any(p is None for p in pts):
            skipped.append({"item": i, "reason": "every vertex must be "
                                                  "[x, y] or [x, y, sweep] "
                                                  "with finite numbers"})
            continue
        if len(pts) > 1 and pts[0][:2] == pts[-1][:2]:
            pts = pts[:-1]
        if len(pts) < 3:
            skipped.append({"item": i, "reason": "at least three distinct "
                                                  "vertices are required"})
            continue
        layer = str(r.get("layer") or "").strip()
        if not layer and kind != "keepout":
            skipped.append({"item": i, "reason": "no layer: layer is "
                                                  "required (only kind=keepout "
                                                  "defaults, to KeepOutLayer)"})
            continue
        # True arcs (upstream's, restored by the 2026-10-25 merge): the
        # edge leaving a vertex with a sweep goes out in an arcs field as
        # index,cx,cy,radius,angle1,angle2 (1-based index, Altium's
        # counterclockwise angles), byte for byte what pcb_place_region
        # sends. An arc that cannot be drawn refuses the region.
        arcs: list[str] = []
        bad_arc = ""
        for k, (x, y, sweep) in enumerate(pts):
            if not sweep:
                continue
            nx, ny, _ = pts[(k + 1) % len(pts)]
            arc = _edge_arc(x, y, nx, ny, sweep)
            if isinstance(arc, str):
                bad_arc = f"point {k}: {arc}"
                break
            cx, cy, rad, a1, a2 = arc
            arcs.append(f"{k + 1},{format_length(cx)},{format_length(cy)},"
                        f"{format_length(rad)},{_angle_text(a1)},"
                        f"{_angle_text(a2)}")
        if bad_arc:
            skipped.append({"item": i, "reason": bad_arc})
            continue
        if as_points:
            # layer first and points last, kind only when not the
            # default: the wire the land-pattern tool always sent.
            fields = [f"layer={_payload_safe(layer)}"] if layer else []
            if kind != "copper":
                fields.append(f"kind={kind}")
            fields.append("points=" + "/".join(
                f"{_len(x, units)},{_len(y, units)}" for x, y, _ in pts))
        else:
            flat = ",".join(f"{_len(x, units)},{_len(y, units)}"
                            for x, y, _ in pts)
            fields = [f"pts={flat}", f"kind={kind}"]
            if layer:
                fields.append(f"layer={_payload_safe(layer)}")
        # arcs last, and only when there are any: a straight region's
        # payload is byte-identical to what it always was.
        if arcs:
            fields.append("arcs=" + "/".join(arcs))
        op_strs.append(";".join(fields))
    return join_ops(op_strs), skipped


#: Values the text tool's ``registration`` diagnostic accepts.
_TEXT_REGISTRATIONS = ("both", "footprint", "board")


def _pad_geometry_problem(reply: Any) -> str:
    """Why a library.get_pad_geometry reply is not a footprint, or ''.

    A footprint with no pads reads as an empty list and is a real
    answer; a refusal or a reply with no list at all is not.
    """
    if not isinstance(reply, dict):
        return f"the read returned {type(reply).__name__}, not a footprint"
    if reply.get("success") is False or (
            reply.get("error") and not isinstance(reply.get("pads"), list)):
        err = reply.get("error")
        if isinstance(err, dict):
            err = err.get("message") or err.get("code")
        return f"the read was refused: {err or 'no reason given'}"
    if not isinstance(reply.get("pads"), list):
        return "the read carried no pad list"
    return ""


def _unread_footprint(name: str, reason: str, code: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "reason": (f"could not read footprint {name!r}: {reason}. Nothing "
                   "was audited; check the name with lib_get_footprints"),
        "findings": [],
    }
    if code:
        out["error_code"] = code
    return out


#: Pad fields the batch handler writes only when present, all lengths in
#: the call's units: a slot length (above hole_size it makes a slotted
#: hole), and manual paste and solder mask expansions.
_OPTIONAL_PAD_LENGTHS = ("hole_length", "paste_expansion", "mask_expansion")


def _edge_arc(x1: float, y1: float, x2: float, y2: float,
              sweep: float) -> tuple[float, float, float, float, float] | str:
    """The circle of an edge drawn as an arc from (x1, y1) to (x2, y2)
    that sweeps ``sweep`` degrees, counterclockwise when positive.

    Returns ``(cx, cy, radius, angle1, angle2)`` with the angles in
    Altium's sense, counterclockwise from angle1 to angle2 and each in
    [0, 360), so a clockwise edge has its start at angle2. Returns the
    reason as a string when there is no such arc.
    """
    if not math.isfinite(sweep) or abs(sweep) >= 360:
        return f"sweep {sweep:g} must be between -360 and 360 degrees"
    dx, dy = x2 - x1, y2 - y1
    chord = math.hypot(dx, dy)
    if chord == 0:
        return "an arc needs its end point apart from its start"
    half = math.radians(abs(sweep)) / 2
    radius = chord / (2 * math.sin(half))
    # The centre sits on the chord's perpendicular bisector, on the left
    # of the travel for a counterclockwise arc under 180 degrees and on
    # the right past it; cos(half) changes sign at 180 and does both.
    offset = radius * math.cos(half) * (1 if sweep > 0 else -1)
    cx = (x1 + x2) / 2 - dy / chord * offset
    cy = (y1 + y2) / 2 + dx / chord * offset
    start = math.degrees(math.atan2(y1 - cy, x1 - cx)) % 360
    end = math.degrees(math.atan2(y2 - cy, x2 - cx)) % 360
    if sweep > 0:
        return cx, cy, radius, start, end
    return cx, cy, radius, end, start


def _angle_text(deg: float) -> str:
    """An angle for the wire: ten decimals, trailing zeros dropped, and a
    value that rounds to 360 sent as 0."""
    text = f"{round(deg, 10) % 360:.10f}".rstrip("0").rstrip(".")
    return text if text not in ("", "-0") else "0"


def _vias_payload(vias: list[dict[str, Any]], size: float,
                  hole_size: float) -> tuple[str, list[str]]:
    """The ``vias`` batch payload for library.add_footprint_vias.

    Each via is ``{"x": .., "y": ..}`` with an optional ``size`` and
    ``hole_size`` (the call's defaults otherwise). A via that is not
    numeric, or whose hole is not inside its diameter, is named in the
    problems and left out.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, via in enumerate(vias or []):
        if not isinstance(via, dict):
            problems.append(f"via {i} is not an object")
            continue
        try:
            x, y = float(via["x"]), float(via["y"])
            d = float(via.get("size", size))
            h = float(via.get("hole_size", hole_size))
        except (TypeError, ValueError, KeyError):
            problems.append(f"via {i} needs numeric x and y")
            continue
        if not 0 < h < d:
            problems.append(f"via {i} needs 0 < hole_size < size")
            continue
        ops.append(f"x={format_length(x)};y={format_length(y)};"
                   f"size={format_length(d)};hole_size={format_length(h)}")
    return "~~".join(ops), problems


def _bodies_payload(bodies: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The ``bodies`` batch payload for library.add_footprint_bodies.

    Each body is ``{"points": [[x, y], ...], "overall_height": ..}`` with
    optional ``standoff_height``, ``layer``, ``identifier``, ``color``
    (an Altium TColor integer) and ``opacity`` (0 to 1). A body with
    fewer than three numeric points, or heights that do not satisfy
    0 <= standoff < overall, is named in the problems and left out.
    """
    ops: list[str] = []
    problems: list[str] = []
    for i, body in enumerate(bodies or []):
        if not isinstance(body, dict):
            problems.append(f"body {i} is not an object")
            continue
        try:
            pts = [(float(p["x"]), float(p["y"])) if isinstance(p, dict)
                   else (float(p[0]), float(p[1]))
                   for p in body.get("points") or []]
            overall = float(body["overall_height"])
            standoff = float(body.get("standoff_height") or 0)
        except (TypeError, ValueError, KeyError, IndexError):
            problems.append(f"body {i} needs numeric points and overall_height")
            continue
        if len(pts) < 3:
            problems.append(f"body {i} needs three or more points")
            continue
        if not 0 <= standoff < overall:
            problems.append(f"body {i} needs 0 <= standoff_height < overall_height")
            continue
        op = ("points=" + "/".join(f"{format_length(x)},{format_length(y)}"
                                   for x, y in pts)
              + f";overall_height={format_length(overall)}"
              + f";standoff_height={format_length(standoff)}")
        for key in ("layer", "identifier"):
            if body.get(key):
                op += f";{key}={_payload_safe(str(body[key]))}"
        if body.get("color") is not None:
            op += f";color={int(body['color'])}"
        if body.get("opacity") is not None:
            op += f";opacity={min(max(float(body['opacity']), 0.0), 1.0)}"
        ops.append(op)
    return "~~".join(ops), problems


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


#: Installed library kinds lib_search cannot read, and why, as
#: library.get_installed_libraries names them.
_UNSEARCHED_KINDS = {
    "database": "a database library (DbLib) keeps its parts as rows in a "
                "database; lib_search reads it only with include_dblibs on, "
                "and lib_dblib_search reads it directly",
    "query": "a query library (SVN DbLib) keeps its parts as rows in a "
             "database, which this search does not read",
    "integrated": "an integrated library (IntLib) is a compiled package; this "
                  "search reads only open .SchLib files",
    "design_items": "workspace components are not in a file this search reads",
}


async def _search_coverage(bridge, library_path) -> dict[str, Any]:
    """What a lib_search looked through, and what it could not.

    An empty search used to read as "no such part" whatever was installed:
    a user with a database library got 0 hits and no hint that the DbLib
    was never opened. The search walks the open .SchLib documents, which
    the open-documents list names exactly; the installed list names the
    rest. Either read failing leaves its field None rather than failing
    the search.
    """
    if library_path:
        return {"libraries_searched": [library_path], "libraries_searched_count": 1}
    out: dict[str, Any] = {}
    searched: list[str] = []
    try:
        docs = await bridge.send_command_async("application.get_open_documents")
        rows = docs if isinstance(docs, list) else (
            (docs or {}).get("result") or (docs or {}).get("documents") or [])
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            path = str(row.get("file_path") or "")
            kind = str(row.get("document_kind") or "").upper()
            if (kind == "SCHLIB" or path.upper().endswith(".SCHLIB")) and path.lower() not in seen:
                seen.add(path.lower())
                searched.append(path)
        out["libraries_searched"] = searched
        out["libraries_searched_count"] = len(searched)
    except Exception:  # noqa: BLE001 - coverage is a report, never a failure
        out["libraries_searched"] = None
    try:
        inst = await bridge.send_command_async(
            "library.get_installed_libraries", {"with_counts": "false"})
        libs = (inst.get("libraries") or []) if isinstance(inst, dict) else []
        open_paths = {p.lower() for p in searched}
        skipped = []
        for lib in libs:
            if not isinstance(lib, dict):
                continue
            path = str(lib.get("library_path") or "")
            kind = str(lib.get("library_type") or "unknown")
            if kind == "unknown" and is_dblib_path(path):
                # Installed but missing from the available list, which is
                # how the type gets lost; the file is still a DbLib.
                kind = "database"
            reason = _UNSEARCHED_KINDS.get(kind)
            if reason is None and path.upper().endswith(".SCHLIB") and path.lower() not in open_paths:
                reason = ("an installed .SchLib that is not open; open it, or pass "
                          "library_path, to search it")
            if reason:
                skipped.append({"library_path": path, "library_type": kind, "reason": reason})
        out["not_searched"] = skipped
        out["not_searched_count"] = len(skipped)
        note = _coverage_note(len(searched), 0, skipped)
        if note:
            out["coverage_note"] = note
    except Exception:  # noqa: BLE001
        out["not_searched"] = None
    return out


def _coverage_note(schlib_count: int, dblib_count: int, skipped: list) -> "str | None":
    """The sentence that stops an empty search reading as "no such part"."""
    if not skipped:
        return None
    kinds = sorted({str(s.get("library_type")) for s in skipped})
    what = f"{schlib_count} open .SchLib file(s)"
    if dblib_count:
        what += (f" and {dblib_count} database "
                 f"librar{'y' if dblib_count == 1 else 'ies'}")
    else:
        what += " only"
    n = len(skipped)
    return (f"Searched {what}. {n} installed librar{'y' if n == 1 else 'ies'} "
            f"({', '.join(kinds)}) were not searched, so an empty result is not "
            "evidence that a part is absent from them.")


#: One DbLib search reads table rows over ADO. A network database or a
#: large table takes far longer than the file reads the other tools wait
#: on, so it gets its own, longer, bound.
_DBLIB_TIMEOUT = 120.0


async def _query_one_dblib(bridge, library_path: str, query: str,
                           limit: int) -> dict[str, Any]:
    """One database library searched by library.query_dblib, redacted."""
    reply = await bridge.send_command_async(
        "library.query_dblib",
        {"library_path": library_path, "query": query, "limit": str(limit)},
        timeout=_DBLIB_TIMEOUT)
    return redact_reply(reply) if isinstance(reply, dict) else {}


def _dblib_summary(path: str, reply: dict[str, Any]) -> dict[str, Any]:
    """What one DbLib search covered, for ``dblib_searches``."""
    libs = reply.get("libraries") or []
    lib = libs[0] if libs and isinstance(libs[0], dict) else {}
    return {
        "library_path": path,
        "searched": bool(lib.get("searched")),
        "error": lib.get("error"),
        "count": reply.get("count", 0),
        "truncated": bool(reply.get("truncated")),
        "rows_scanned": reply.get("rows_scanned"),
        "scan_capped": bool(reply.get("scan_capped")),
        "tables_searched": lib.get("tables_searched") or [],
        "tables_failed": lib.get("tables_failed") or [],
    }


async def _search_dblibs(bridge, result: dict[str, Any], query: str,
                         limit: int, include_dblibs: bool) -> None:
    """Fold the installed database libraries into a lib_search reply.

    Each DbLib that _search_coverage listed under ``not_searched`` is
    searched in turn with what is left of ``limit``. Its rows join
    ``results`` marked ``source: "dblib"`` and its path moves to
    ``libraries_searched``. A DbLib that is switched off, crowded out by
    the limit, or that fails stays under ``not_searched`` with that
    reason: a DbLib never fails the search, and an unread one is never
    reported as read.
    """
    skipped = result.get("not_searched")
    if not isinstance(skipped, list):
        return
    results = result.setdefault("results", [])
    searched = result.get("libraries_searched")
    schlib_count = len(searched) if isinstance(searched, list) else 0
    truncated = bool(result.get("truncated"))
    remaining: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    dblibs_read = 0
    for entry in skipped:
        if not isinstance(entry, dict) or entry.get("library_type") != "database":
            remaining.append(entry)
            continue
        path = str(entry.get("library_path") or "")
        if not include_dblibs:
            remaining.append({**entry, "reason": (
                "a database library (DbLib), skipped because include_dblibs "
                "is off; turn it on, or use lib_dblib_search")})
            continue
        room = limit - len(results)
        if room <= 0:
            truncated = True
            remaining.append({**entry, "reason": (
                "a database library (DbLib) left unsearched because the result "
                "limit was reached first; raise limit or use lib_dblib_search")})
            continue
        try:
            reply = await _query_one_dblib(bridge, path, query, room)
        except Exception as exc:  # noqa: BLE001 - one DbLib never fails the search
            remaining.append({**entry, "reason": (
                "a database library (DbLib) whose search failed: "
                + redact_connection_text(str(exc))[:300])})
            continue
        summary = _dblib_summary(path, reply)
        summaries.append(summary)
        if not summary["searched"]:
            remaining.append({**entry, "reason": (
                "a database library (DbLib) that could not be searched ("
                + str(summary["error"] or "no reply") + "); lib_dblib_info "
                "reports its connection and tables")})
            continue
        hits = [search_hit_as_result(h) for h in (reply.get("results") or [])
                if isinstance(h, dict)]
        results.extend(hits[:room])
        if summary["truncated"] or summary["scan_capped"] or len(hits) > room:
            truncated = True
        if isinstance(searched, list):
            searched.append(path)
        dblibs_read += 1
    result["not_searched"] = remaining
    result["not_searched_count"] = len(remaining)
    if isinstance(searched, list):
        result["libraries_searched_count"] = len(searched)
    if summaries:
        result["dblib_searches"] = summaries
    note = _coverage_note(schlib_count, dblibs_read, remaining)
    if note:
        result["coverage_note"] = note
    else:
        result.pop("coverage_note", None)
    result["count"] = len(results)
    result["truncated"] = truncated or len(results) >= limit


async def _search_named_dblib(bridge, library_path: str, query: str,
                              search_type: str, limit: int) -> dict[str, Any]:
    """lib_search with library_path naming a DbLib: that DbLib alone, in
    lib_search's own reply shape."""
    out: dict[str, Any] = {"query": query, "search_type": search_type,
                           "limit": limit, "results": []}
    try:
        reply = await _query_one_dblib(bridge, library_path, query, limit)
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": redact_connection_text(str(exc))}
    summary = _dblib_summary(library_path, reply)
    out["results"] = [search_hit_as_result(h) for h in (reply.get("results") or [])
                      if isinstance(h, dict)]
    out["count"] = len(out["results"])
    out["truncated"] = summary["truncated"] or summary["scan_capped"]
    out["dblib_searches"] = [summary]
    if summary["searched"]:
        out["libraries_searched"] = [library_path]
        out["libraries_searched_count"] = 1
    else:
        out["libraries_searched"] = []
        out["libraries_searched_count"] = 0
        out["not_searched"] = [{
            "library_path": library_path, "library_type": "database",
            "reason": ("a database library (DbLib) that could not be searched ("
                       + str(summary["error"] or "no reply") + ")")}]
        out["not_searched_count"] = 1
    return out


def _standard_symbol_warnings(components: list[dict]) -> list[dict]:
    """Amplifiers whose body is a rectangle with no triangle (polygon)."""
    from eda_agent.design.symbol_gen import classify_amplifier_pins

    out = []
    for comp in components:
        body = comp.get("body") or {}
        if not body or body.get("polygons", 0) or not body.get("rectangles", 0):
            continue
        groups = classify_amplifier_pins(comp.get("pins") or [])
        if groups is None:
            continue
        out.append({
            "name": comp.get("name"),
            "description": comp.get("description", ""),
            "units": len(groups["units"]),
            "reason": "the pins form an amplifier (IN+, IN-, OUT) but the "
                      "body is a rectangle; the standard symbol is a "
                      "triangle",
        })
    return out


async def _emit_amplifier_symbol(name: str, amp: Any, prefix: str,
                                 description: str) -> dict[str, Any]:
    """Create a triangle amplifier symbol from AmplifierSymbol geometry.

    Per part: the body (triangle or block) FIRST, then the pins, then the
    +/- marks, because Altium draws in insertion order and a solid body
    added later paints over the pins (see lib_create_ic_symbol)."""
    bridge = get_bridge()
    created = await bridge.send_command_async(
        "library.create_symbol",
        {"name": name, "designator_prefix": prefix,
         "description": description, "part_count": str(amp.part_count)},
    )
    steps: list[dict[str, Any]] = []
    total_pins = 0
    for part in amp.parts:
        res: dict[str, Any] = {"part": part["part"]}
        for poly in part["polygons"]:
            verts = ",".join(str(v) for pt in poly["points"] for v in pt)
            res["triangle"] = await bridge.send_command_async(
                "library.add_symbol_polygon",
                {"vertices": verts, "fill_color": poly["fill_color"],
                 "owner_part_id": poly["owner_part_id"]})
        for r in part["rectangles"]:
            res["block"] = await bridge.send_command_async(
                "library.add_symbol_rectangle",
                {"x1": r["x1"], "y1": r["y1"], "x2": r["x2"], "y2": r["y2"],
                 "fill_color": r["fill_color"], "border_color": 0,
                 "owner_part_id": r["owner_part_id"]})
        payload, _ = _pins_payload(part["pins"])
        res["pins"] = await bridge.send_command_async(
            "library.add_pins", {"pins": payload})
        total_pins += len(part["pins"])
        if part["texts"]:
            ops = [";".join([f"text={t['text']}", f"x={t['x']}",
                             f"y={t['y']}", f"font_size={t['font_size']}",
                             f"owner_part_id={t['owner_part_id']}"])
                   for t in part["texts"]]
            res["marks"] = await bridge.send_command_async(
                "library.add_symbol_text", {"texts": "~~".join(ops)})
        steps.append(res)
    return {"symbol": name, "body": "amplifier", "parts": amp.part_count,
            "pins": total_pins, "create": created, "steps": steps}


#: Line-size code for symbol glyph lines: Altium's TSize, 0 smallest,
#: 1 small (its default), 2 medium, 3 large. Not mils.
SYMBOL_LINE_WIDTH = 1

#: Outline colour of a discrete glyph's body (resistor, fuse, crystal), as
#: Altium's BGR integer: the same blue its symbol lines default to, so a
#: resistor box does not come out black beside blue capacitor plates.
SYMBOL_LINE_COLOR = 0xFF0000


#: Every key lib_set_label_formats encodes into an op.
_LABEL_OP_KEYS = frozenset({
    "target", "font_id", "color", "is_hidden", "orientation",
    "justification", "x", "y", "text",
})


async def _footprint_3d_body(bridge: Any, step: Any, spec: Any, model: Any,
                             fp_name: str, mode: str) -> dict[str, Any]:
    """The 3D body of a footprint built from a package drawing.

    First choice a STEP model designed from the same drawing
    (design.models3d), linked to the footprint; the fallback an extruded
    body of the package outline from its standoff to its height. Returns
    ``{kind, note, ...}`` where kind is step, extruded or none.
    """
    from ..design import models3d

    if mode == "none":
        return {"kind": "none", "note": "3D body: none, as asked"}
    why_not_step = ""
    if mode in ("auto", "step"):
        import logging
        import time as _time
        log = logging.getLogger(__name__)
        t0 = _time.monotonic()
        plan = models3d.plan_model(spec, model)
        if plan is None:
            why_not_step = (f"the {spec.family} family is not modelled yet "
                            f"(modelled: {', '.join(models3d.MODELLED_FAMILIES)})"
                            if spec.family not in models3d.MODELLED_FAMILIES
                            else "the package gives too few dimensions for its shape")
        else:
            safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                           for ch in fp_name)
            path = get_config().workspace_dir / "models" / f"{safe}.step"
            t1 = _time.monotonic()
            wrote = models3d.write_step(plan, path, name=fp_name)
            # The first model in a server once took 146 s here and none
            # outside it could be made slow; the log says which step.
            log.info("3D model %s: plan %.1f s, write %.1f s (%s)", fp_name,
                     t1 - t0, _time.monotonic() - t1, wrote.get("writer") or "none")
            if wrote["ok"]:
                linked = await step("model_3d", bridge.send_command_async(
                    "library.link_3d_model",
                    {"component_name": fp_name, "model_path": wrote["path"],
                     "offset_x": 0, "offset_y": 0, "offset_z": 0,
                     "rotation_x": 0, "rotation_y": 0, "rotation_z": 0}))
                if isinstance(linked, dict) and linked.get("placement_ok") is False:
                    # The handler added the body in memory before it found it
                    # off the footprint: an extruded fallback now would put a
                    # second body beside the misplaced one, so report instead.
                    problem = str((linked.get("placement") or {}).get("problem")
                                  or "placement check failed")
                    return {"kind": "step", "placement_ok": False,
                            "path": wrote["path"], "writer": wrote["writer"],
                            "assumed": plan.assumed, "sources": plan.sources,
                            "note": ("3D body: the STEP model was linked in "
                                     f"memory but is NOT on its footprint "
                                     f"({problem}). No extruded fallback was "
                                     "added beside it. Remove it with "
                                     "lib_delete_footprint_primitives "
                                     "(object_type componentbody, "
                                     "library_path) before saving")}
                if linked is not None:
                    note = (f"3D body: STEP model from the drawing, written with "
                            f"{wrote['writer']} to {wrote['path']} and linked")
                    if plan.assumed:
                        note += "; not in the drawing, so assumed: " + "; ".join(plan.assumed)
                    return {"kind": "step", "path": wrote["path"],
                            "writer": wrote["writer"], "assumed": plan.assumed,
                            "sources": plan.sources, "note": note}
                why_not_step = "the STEP model was written but Altium did not link it"
            else:
                why_not_step = wrote.get("reason") or "no STEP writer worked"
        if mode == "step":
            return {"kind": "none", "note": f"3D body: none, {why_not_step}"}

    b = model.body
    if b.z1 <= b.z0 or b.x1 <= b.x0 or b.y1 <= b.y0:
        return {"kind": "none", "note": "3D body: none, the package gives no "
                "body size and height" + (f" ({why_not_step})" if why_not_step else "")}
    payload = (f"points={b.x0},{b.y0}/{b.x1},{b.y0}/{b.x1},{b.y1}/{b.x0},{b.y1}"
               f";overall_height={b.z1};standoff_height={max(b.z0, 0.0)}"
               f";identifier={payload_safe(fp_name)}")
    reply = await step("model_3d", bridge.send_command_async(
        "library.add_footprint_bodies", _with_units(
            {"bodies": payload, "footprint_name": fp_name}, "mm")))
    ok = isinstance(reply, dict) and int(reply.get("added") or 0) == 1
    reason = f" because {why_not_step}" if why_not_step else ""
    if not ok:
        return {"kind": "none", "note": "3D body: none, the extruded fallback "
                f"was not added{reason}"}
    return {"kind": "extruded", "note": "3D body: an extruded box of the package "
            f"outline and height, the fallback{reason}; lib_create_3d_model "
            "replaces it with a STEP model built from the drawing"}


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
        body: str = "auto",
        invert_on_top: bool = False,
    ) -> dict[str, Any]:
        """Create a complete IC symbol (body plus every pin) in one call.

        Amplifiers get the standard triangle: with ``body="auto"`` it is
        chosen when the pin names form IN+/IN-/OUT units (+INA/-INA/OUTA
        and IN1+/IN1-/OUT1 count), and a dual or quad becomes one
        triangle per part plus a power part. Copying a standard part
        from the user's library with lib_copy_component is an
        alternative.

        Otherwise a rectangle: you group the pins (inputs, power and
        control LEFT; outputs and status RIGHT, discipline rule 18) and
        this lays them on grid with the top-left pin's wire end at the
        origin (rule 14).

        Args:
            name: Component name.
            left_pins: Pins on the LEFT side, top-to-bottom. Each a dict
                with `designator`, `name`, and optional `electrical_type`
                (input/output/power/passive/...).
            right_pins: Pins on the RIGHT side, top-to-bottom, same shape.
                For an amplifier the sides are ignored: the triangle puts
                every pin where the convention does.
            designator_prefix: e.g. "U".
            description: Component description.
            body: "auto" (triangle for amplifiers, else rectangle),
                "amplifier" or "rectangle".
            invert_on_top: triangle only; put IN- above IN+.

        Returns:
            Dict with the symbol name, pin count, ``body`` drawn, body
            extent, and the per-step bridge results.
        """
        from eda_agent.design.symbol_gen import (
            generate_amplifier_symbol, generate_ic_symbol,
            looks_like_amplifier)

        wanted = (body or "auto").strip().lower()
        if wanted not in ("auto", "amplifier", "rectangle"):
            return {"ok": False, "reason":
                    "body must be 'auto', 'amplifier' or 'rectangle'"}
        all_pins = [*left_pins, *right_pins]
        if wanted == "amplifier" or (
                wanted == "auto" and looks_like_amplifier(all_pins,
                                                          description)):
            try:
                amp = generate_amplifier_symbol(all_pins,
                                                invert_on_top=invert_on_top)
            except ValueError as e:
                if wanted == "amplifier":
                    return {"ok": False, "reason": str(e)}
                amp = None
            if amp is not None:
                return await _emit_amplifier_symbol(
                    name, amp, designator_prefix, description)

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
        pin_map: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Create a standard DISCRETE symbol (the conventional glyph) in ONE call.

        Use this, not a rectangle, for any discrete part. Draws resistor,
        capacitor, polarized_capacitor, inductor (arcs), diode, led,
        crystal, fuse, zener, schottky, tvs, tvs_bidirectional, switch,
        push_button, and the three-pin nmos, pmos, npn and pnp. Two-pin
        parts have pin 1 on the left and pin 2 on the right; a transistor
        has its gate/base on the left and the other two leaving the top
        and bottom. Glyph detail is emitted in raw mils, so it keeps its
        sub-100-mil shape while every pin stays on the 100-mil grid.

        Args:
            name: Component name (e.g. "R_0402").
            kind: one of the kinds above (aliases such as r/c/cp/l/d/xtal/
                f/z/sd/tvs_bi/sw/button/nfet/pfet/bjt_npn are accepted).
            designator_prefix: default by kind (R/C/L/D/Y/F/S/Q) if omitted.
            description: Component description.
            pin_map: transistors only, role -> pin number, e.g.
                {"G": "1", "S": "2", "D": "3"}. Defaults are the common
                SOT-23 assignment (MOSFET 1=G 2=S 3=D, BJT 1=B 2=E 3=C);
                check the datasheet.

        Returns:
            Dict with the symbol name and per-step bridge results.
        """
        from eda_agent.design.symbol_gen import (
            TRANSISTOR_DEFAULT_PINS, generate_passive_symbol,
            generate_transistor_symbol)

        try:
            if kind.strip().lower() in ("nmos", "pmos", "npn", "pnp",
                                        "nfet", "pfet", "n_mosfet",
                                        "p_mosfet", "mosfet_n", "mosfet_p",
                                        "bjt_npn", "bjt_pnp"):
                geom = generate_transistor_symbol(kind, pin_map)
            elif pin_map:
                return {"ok": False, "reason":
                        "pin_map applies to transistors only "
                        f"({sorted(TRANSISTOR_DEFAULT_PINS)})"}
            else:
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
            "zener": "D", "z": "D", "schottky": "D", "sd": "D",
            "tvs": "D", "tvs_uni": "D", "tvs_bidirectional": "D",
            "tvs_bi": "D", "bidirectional_tvs": "D",
            "switch": "S", "spst": "S", "sw": "S", "push_button": "S",
            "pushbutton": "S", "button": "S", "tactile": "S",
            "nmos": "Q", "pmos": "Q", "npn": "Q", "pnp": "Q", "nfet": "Q",
            "pfet": "Q", "n_mosfet": "Q", "p_mosfet": "Q", "mosfet_n": "Q",
            "mosfet_p": "Q", "bjt_npn": "Q", "bjt_pnp": "Q",
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
                 "fill_color": -1, "border_color": SYMBOL_LINE_COLOR})
        if geom.lines:
            # width is Altium's line-size code (0 smallest .. 3 large), not
            # mils: 10 drew every plate and lead as a fat bar, so a
            # capacitor read as a blue blob.
            line_ops = [
                ";".join([f"x1={l['x1']}", f"y1={l['y1']}",
                          f"x2={l['x2']}", f"y2={l['y2']}",
                          f"width={SYMBOL_LINE_WIDTH}"])
                for l in geom.lines
            ]
            steps["lines"] = await bridge.send_command_async(
                "library.add_symbol_lines", {"lines": "~~".join(line_ops)})
        for i, poly in enumerate(geom.polygons):
            verts = ",".join(
                str(v) for pt in poly["points"] for v in pt)
            steps[f"poly{i}"] = await bridge.send_command_async(
                "library.add_symbol_polygon", {"vertices": verts})
        for i, arc in enumerate(geom.arcs):
            steps[f"arc{i}"] = await bridge.send_command_async(
                "library.add_symbol_arc",
                {"x_center": arc["x"], "y_center": arc["y"],
                 "radius": arc["radius"], "start_angle": arc["start"],
                 "end_angle": arc["end"], "width": 1})

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

        Call it before ``obj_modify`` / ``obj_batch_modify`` with scope
        "active_doc" on a symbol's pins, rectangles or parameters: those
        act on the current component, which reading one with
        ``lib_get_component_details`` does not change. (Scope
        "lib_component:NAME" selects the symbol for you.)

        It also makes that component the HELD symbol that the nameless
        ``lib_add_*`` symbol edits act on. It is held for the current
        bridge session only: a StartMCPServer re-run (the recovery after a
        dead loop) or an Altium restart drops it, and the next nameless
        edit answers NO_COMPONENT. Call this again after a recovery, or
        pass ``component_name`` to each edit.

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
        component_name: str = "",
    ) -> dict[str, Any]:
        """Add MANY pins to the current symbol in ONE call (there is no
        single-pin variant).

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
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.

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

        params: dict[str, Any] = {"pins": payload}
        if component_name:
            params["component_name"] = component_name
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_pins",
            params,
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
        component_name: str = "",
        line_width: Optional[int] = None,
        is_solid: Optional[bool] = None,
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
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.
            line_width: Border width, 0 (smallest) to 3 (large); omit
                for Altium's default.
            is_solid: Fill (True) or outline only (False); omit and a
                fill colour makes it solid.

        Returns:
            Dictionary confirming rectangle addition, with the style it
            now holds read back: ``color``, ``area_color``,
            ``line_width``, ``is_solid``.
        """
        if line_width is not None and line_width not in (0, 1, 2, 3):
            return {"success": False, "error": "line_width is 0 to 3"}
        params: dict[str, Any] = {
            "x1": _snap(int(x1)),
            "y1": _snap(int(y1)),
            "x2": _snap(int(x2)),
            "y2": _snap(int(y2)),
            "fill_color": fill_color,
            "border_color": border_color,
        }
        if line_width is not None:
            params["line_width"] = int(line_width)
        if is_solid is not None:
            params["is_solid"] = "true" if is_solid else "false"
        if component_name:
            params["component_name"] = component_name
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_rectangle",
            params,
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_text(
        texts: list[dict[str, Any]],
        component_name: str = "",
    ) -> dict[str, Any]:
        """Add MANY body-text items to the current symbol in ONE call.

        Free text such as polarity marks, pin-group headings and "NC".

        Args:
            texts: List of text dicts, each with:
                - text     (str, required; empty is refused)
                - x, y     (int, mils)
                - rotation (int, default 0), 0/90/180/270
                - font_size (int, default 10): Altium's font size, not
                  mils and not converted
                - font_name (str, default "Arial")
                - bold, italic (bool, default False)
                - owner_part_id (int, optional). Same meaning as in
                  ``lib_add_pins``: 1..part_count picks a sub-part, 0
                  shares the text across all of them. Omit for
                  single-part symbols.
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.

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
        params: dict[str, Any] = {"texts": join_ops(op_strs)}
        if component_name:
            params["component_name"] = component_name
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_text",
            params,
        )

    @mcp.tool()
    async def lib_add_symbol_lines(
        lines: list[dict[str, Any]],
        grid: int = 25,
        component_name: str = "",
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
                default 1) and an optional ``color`` (Altium colour
                integer).
            grid: snap grid (mils) for the line endpoints. Glyph lines do
                NOT need the 100-mil pin grid (rule 15), so the default is
                a finer 25 mils for clean glyph detail; pass 100 for the
                coarse grid or 0/1 to disable snapping.
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.

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
            if line.get("color") is not None:
                fields.append(f"color={int(line['color'])}")
            op_strs.append(";".join(fields))
        if not op_strs:
            return {"error": "No lines provided", "added": 0}
        params: dict[str, Any] = {"lines": "~~".join(op_strs)}
        if component_name:
            params["component_name"] = component_name
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.add_symbol_lines",
            params,
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

        A name the library already has (ignoring case) is refused with
        NAME_EXISTS: a second footprint of the same name would be
        unreachable, every lookup by name finding the first.

        Args:
            name: Footprint name
            description: Footprint description

        Returns:
            Dictionary with created footprint information
        """
        if not name.strip():
            return {"success": False, "error": "name is required"}
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
        """Create a COMPLETE standard footprint in ONE call from the land
        dimensions you give (pads, silk outline, courtyard). For a package
        drawing with tolerances prefer lib_create_footprint_from_package,
        which sizes lands by IPC-7351B.

        Args:
            name: Footprint name (e.g. "SOIC-8_3.9x4.9").
            family: "chip" | "sip" | "dual" (SOIC/SOP/SON/SOT/DIP; SOT-23
                is dual with 3 pins) | "header" (2-row, odd/even) | "tab"
                (SOT-223, DPAK, TO-220; give tab_w, tab_h) | "quad"
                (QFP/QFN) | "bga" (give rows, cols). hole > 0 makes any
                family through-hole.
            pin_count: Total pads (2 chip, even header, signal pins for
                tab, multiple of 4 quad; ignored for bga).
            rows, cols: BGA grid.
            exposed_pad: QFN/QFP centre pad size, mils (0 = none); it is
                pin_count+1.
            tab_w, tab_h: Tab pad size, mils; it is pin_count+1.
            skip: BGA balls to leave out, e.g. ["A1"].
            pitch: Pad pitch within a row, mils.
            pad_w, pad_h: Pad size, mils. pad_w is the X size (across the
                row for chip and dual, along it for sip, header and tab);
                for quad it runs along each row.
            row_span: Centre-to-centre between rows (dual, quad), mils.
            shape: Pad shape (default "roundrect", corner_radius in %).
            silk: Draw a TopOverlay body outline.
            courtyard: Courtyard margin, mils (0 = none; Mechanical1).
            body_w, body_h: Body size for the silk (default pad extent).
            hole: Drill, mils; > 0 gives through-hole pads, pin 1 square.
            description: Footprint description.

        Returns:
            Footprint name, pad and track counts, extent, step results.
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
    async def lib_create_footprint_from_package(
        package: Any,
        name: str = "",
        density: str = "",
        description: str = "",
        assembly_layer: str = "",
        courtyard_layer: str = "",
        drafting: Optional[dict[str, Any]] = None,
        set_height: bool = True,
        model_3d: str = "auto",
    ) -> dict[str, Any]:
        """Create a COMPLETE footprint in the open PcbLib from a package drawing.

        Give the package as the datasheet draws it, not a pad list: lands
        come from IPC-7351B for the family and density. Writes pads,
        exposed-pad paste windows, silkscreen with a pin 1 dot, assembly
        outline and .Designator, courtyard and height; origin at the
        centroid, all lengths in mm. The pads are then read back and any
        difference is a failure.

        DATASHEET DISCIPLINE: transcribe each dimension as min/nom/max
        from the manufacturer's drawing fetched in this conversation, and
        name it in ``source``; nominal-only dimensions give worse lands
        and a warning.

        Args:
            package: the PackageSpec as an object (or its JSON text):
                ``family`` (chip, melf, molded, sod, sot23, sot89, sot223,
                dpak, soic, son, qfn, qfp, plcc, bga, lga, crystal2,
                crystal4, dip, header, radial, axial, to220, to92,
                connector, custom) and the dimensions it needs, each a
                number, ``[min, max]``, ``[min, nom, max]`` or
                ``{min, nom, max, cite}``. Axes: two-row parts have their
                rows LEFT and RIGHT, so ``lead_span_x`` is the tip to tip
                span across them (JEDEC E) and ``body_y`` the body along
                them (D); ``lead_length`` is the terminal along the span
                (L), ``lead_width`` across it (b). Exposed pad:
                ``ep_x``/``ep_y``. ``source``: {document, revision, page,
                ref, url}.
            name: Footprint name; default the package's, else generated
                (e.g. SOIC-8-P1.27N).
            density: L, N or M (default the package's, else N).
            description: Appended to the generated description.
            assembly_layer, courtyard_layer: Mechanical layers. Empty picks
                them by kind or name ("Assembly Top", "Courtyard Top");
                with neither, it refuses and lists the layers.
            drafting: Overrides, e.g. {"silk_width": 0.15,
                "ep_paste_coverage": 0.6, "mask_expansion": 0.05}.
            set_height: Write the height (package A max).
            model_3d: The 3D body. "auto" (default): a STEP model designed
                from the same drawing (body at its standoff and height,
                each lead or terminal at its land, exposed pad, balls),
                written with CadQuery or FreeCAD and linked to the
                footprint; when the family is not modelled or no writer is
                installed, an extruded body of the package outline and
                height instead, and ``notes`` says which and why. "step":
                the STEP model or nothing. "extruded": the extruded body.
                "none": no body. A STEP model is refused when a dimension
                it needs names no drawing.

        Returns:
            {ok, footprint, library_path, created, layers, readback, pads,
            courtyard_mm, warnings, notes, ipc, steps, model_3d}. ``ok``
            only when every step succeeded, the readback matched and a
            linked STEP body sits on its footprint (``model_3d.placement_ok``
            false fails it); a later failure leaves the footprint created
            but incomplete.
        """
        from ..bridge.exceptions import AltiumError
        from ..design.landpattern.emit import (
            LayerRoleError,
            altium_plan,
            model_from_package,
            resolve_altium_layers,
            verify_altium_readback,
        )
        from ..design.landpattern.emit import package_spec
        from ..design.landpattern.spec import SpecError

        if model_3d not in ("auto", "step", "extruded", "none"):
            return {"ok": False, "created": False,
                    "reason": "model_3d is auto, step, extruded or none"}
        try:
            model = model_from_package(package, density=density, name=name,
                                       description=description,
                                       drafting=drafting)
            spec = package_spec(package)
        except SpecError as e:
            return {"ok": False, "reason": str(e), "created": False}
        fp_name = model.name
        if any(ord(ch) < 32 for ch in fp_name) or not fp_name.strip():
            return {"ok": False, "created": False,
                    "reason": f"footprint name {fp_name!r} is empty or has a "
                              "control character"}

        bridge = get_bridge()
        steps: dict[str, Any] = {}

        async def step(label: str, call) -> Any:
            try:
                reply = await call
            except AltiumError as e:
                steps[label] = {"error": str(e), "code": e.code}
                return None
            steps[label] = reply
            return reply

        def failed(label: str, why: str, created: bool) -> dict[str, Any]:
            out: dict[str, Any] = {
                "ok": False, "footprint": fp_name, "created": created,
                "reason": f"{label}: {why}", "steps": steps,
                "warnings": model.warnings}
            if created:
                out["note"] = (f"footprint {fp_name!r} exists and is "
                               "incomplete; remove it with lib_delete_footprint "
                               "before trying again")
            return out

        # The library the writes go to: GetCurrentPCBLibrary, the same one
        # create_footprint uses. Its path keeps the reads on that library.
        mech = await step("mech_layers", bridge.send_command_async(
            "library.get_mech_layers", {}))
        if mech is None and not (assembly_layer and courtyard_layer):
            return failed("mech_layers", "could not read the library's "
                          "mechanical layers; pass assembly_layer and "
                          "courtyard_layer", False)
        lib_path = str((mech or {}).get("library_path") or "")
        try:
            layers = resolve_altium_layers(
                (mech or {}).get("layers") or [], assembly_layer=assembly_layer,
                courtyard_layer=courtyard_layer)
            plan = altium_plan(model, layers)
        except (LayerRoleError, SpecError) as e:
            return {"ok": False, "created": False, "reason": str(e),
                    "steps": steps}

        listing = await step("existing", bridge.send_command_async(
            "library.get_footprints", {"library_path": lib_path}))
        if listing is None:
            return failed("existing", "could not list the library, so a "
                          "footprint of the same name cannot be ruled out", False)
        rows = listing.get("footprints") if isinstance(listing, dict) else None
        if not isinstance(rows, list):
            return failed("existing", "the library listing carried no "
                          "footprint list", False)
        names = {str(f.get("name")).upper() for f in rows if isinstance(f, dict)}
        if fp_name.upper() in names:
            return failed("existing", f"a footprint named {fp_name!r} is "
                          "already in the library; pass another name", False)

        created = await step("create", bridge.send_command_async(
            "library.create_footprint",
            {"name": fp_name, "description": model.description}))
        if created is None:
            return failed("create", "the footprint was not created", False)

        def batch_ok(reply: Any, want: int) -> str:
            if not isinstance(reply, dict):
                return "no reply"
            if int(reply.get("added") or 0) != want or int(
                    reply.get("failed") or 0):
                return (f"added {reply.get('added')} of {want}, failed "
                        f"{reply.get('failed')}, unknown layers "
                        f"{reply.get('unknown_layers')!r}")
            if int(reply.get("property_failures") or 0):
                return (f"Altium refused {reply.get('property_failures')} "
                        "pad property writes (slot, plating or expansion)")
            return ""

        pads_payload, _ = _pads_payload(plan["pads"], "mm")
        reply = await step("pads", bridge.send_command_async(
            "library.add_footprint_pads", _with_units(
                {"pads": pads_payload, "footprint_name": fp_name}, "mm")))
        problem = "the call failed" if reply is None else batch_ok(
            reply, len(plan["pads"]))
        if problem:
            return failed("pads", problem, True)

        tracks_payload, _ = _tracks_payload(plan["tracks"], "mm")
        if tracks_payload:
            reply = await step("tracks", bridge.send_command_async(
                "library.add_footprint_tracks", _with_units(
                    {"tracks": tracks_payload, "footprint_name": fp_name},
                    "mm")))
            problem = "the call failed" if reply is None else batch_ok(
                reply, len(plan["tracks"]))
            if problem:
                return failed("tracks", problem, True)

        if plan["regions"]:
            regions_payload, refused = _regions_payload(plan["regions"], "mm")
            if refused:
                return failed("regions", "; ".join(
                    f"region {s['item']}: {s['reason']}" for s in refused),
                    True)
            reply = await step("regions", bridge.send_command_async(
                "library.add_footprint_regions", _with_units(
                    {"regions": regions_payload, "footprint_name": fp_name},
                    "mm")))
            problem = "the call failed" if reply is None else batch_ok(
                reply, len(plan["regions"]))
            if problem:
                return failed("regions", problem, True)

        for i, arc in enumerate(plan["arcs"]):
            reply = await step(f"arc_{i}", bridge.send_command_async(
                "library.add_footprint_arc", _with_units({
                    "x_center": format_length(arc["x_center"]),
                    "y_center": format_length(arc["y_center"]),
                    "radius": format_length(arc["radius"]),
                    "start_angle": arc["start_angle"],
                    "end_angle": arc["end_angle"],
                    "width": format_length(arc["width"]),
                    "layer": arc["layer"], "footprint_name": fp_name}, "mm")))
            if reply is None:
                return failed(f"arc_{i}", "the call failed", True)

        for i, text in enumerate(plan["texts"]):
            reply = await step(f"text_{i}", bridge.send_command_async(
                "library.add_footprint_text", _with_units({
                    "text": text["text"],
                    "x": format_length(text["x"]),
                    "y": format_length(text["y"]),
                    "size": format_length(text["size"]),
                    "width": format_length(text["width"]),
                    "rotation": 0, "layer": text["layer"],
                    "component_name": fp_name}, "mm")))
            if reply is None:
                return failed(f"text_{i}", "the call failed", True)

        height = float(plan["height_mm"] or 0.0)
        if set_height and height > 0:
            reply = await step("height", bridge.send_command_async(
                "library.set_footprint_height",
                {"footprint_name": fp_name,
                 "height_mm": format_length(height)}))
            if reply is None:
                return failed("height", "the call failed", True)

        readback = await step("readback", bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": fp_name, "library_path": lib_path}))
        verdict = verify_altium_readback(plan, readback)
        notes = list(plan["notes"])
        if not (set_height and height > 0):
            notes.append("height: not written (set_height false, or the "
                         "package gives no height)")
        body = await _footprint_3d_body(bridge, step, spec, model, fp_name,
                                        model_3d)
        notes.append(body["note"])
        return {
            # A STEP body linked off its footprint fails the build too.
            "ok": bool(verdict.get("ok")) and body.get("placement_ok") is not False,
            "footprint": fp_name,
            "library_path": lib_path,
            "family": model.family,
            "density": model.density,
            "description": model.description,
            "created": {"pads": len(plan["pads"]),
                        "tracks": len(plan["tracks"]),
                        "regions": len(plan["regions"]),
                        "arcs": len(plan["arcs"]),
                        "texts": len(plan["texts"]),
                        "height_mm": height if set_height else 0.0},
            "layers": layers,
            "readback": verdict,
            "pads": [{"number": p.number, "x": p.x, "y": p.y,
                      "width": p.width, "height": p.height, "shape": p.shape,
                      "rotation": p.rotation, "hole": p.hole,
                      "kind": p.kind} for p in model.pads],
            "courtyard_mm": list(model.courtyard_bbox()),
            "pin1": list(model.pin1),
            "warnings": model.warnings,
            "model_3d": body,
            "notes": notes,
            "ipc": model.ipc.get("calcs", {}),
            "steps": steps,
        }

    @mcp.tool()
    async def lib_add_footprint_pad(
        designator: str,
        x: float,
        y: float,
        x_size: Optional[float] = None,
        y_size: Optional[float] = None,
        hole_size: float = 0,
        shape: str = "rectangular",
        layer: str = "TopLayer",
        rotation: float = 0,
        corner_radius: int = 25,
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
        corner_radius_length: Optional[float] = None,
    ) -> dict[str, Any]:
        """Add a pad to a footprint: the named one, or the current one.

        Args:
            designator: Pad designator (e.g., "1", "2"); "" for an
                unnamed fiducial or mechanical pad.
            x: X coordinate in ``units``, relative to the footprint
                origin. Fractions are kept, never rounded to whole mils.
            y: Y coordinate in ``units``
            x_size: Pad X size (default 60 mil, in the call's unit)
            y_size: Pad Y size (default 60 mil, in the call's unit)
            hole_size: Drill hole size (0 for SMD). A drilled pad
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
            corner_radius_length: or the radius as a length, converted to
                that % of half the shortest side (so resizing the pad
                later changes it).
            component_name: Footprint to add the pad to. It is made the
                library's current footprint, and the switch is checked,
                as the track, arc, text, fill and region tools do.
                Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first; refused when a
                different document ends up in front.
            footprint_name: the same as component_name (either name
                works; giving both with different values is refused).
            units: "mil" (default) or "mm", for every length and
                coordinate here; mm values are kept exact.

        Returns:
            Dictionary confirming pad addition; ``shape_status`` says
            whether a roundrect read back ("confirmed") or not.
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        if x_size is None:
            x_size = length_in(units, 60)
        if y_size is None:
            y_size = length_in(units, 60)
        # The same guards as the track, arc and text siblings: a NaN raised
        # a traceback, and a rotation sent raw reached StrToFloatDef as
        # NaN/inf text and came back as its default.
        nums = {"x": _num(x), "y": _num(y), "x_size": _num(x_size),
                "y_size": _num(y_size), "hole_size": _num(hole_size),
                "rotation": _num(rotation)}
        bad = [k for k, v in nums.items() if v is None]
        if bad:
            return {"ok": False, "reason": (
                f"not a finite number: {', '.join(bad)} ({units}, and "
                f"degrees for rotation)")}
        if nums["x_size"] <= 0 or nums["y_size"] <= 0:
            return {"ok": False, "reason": (
                "x_size and y_size must be greater than zero")}
        if nums["hole_size"] < 0:
            return {"ok": False, "reason": (
                "hole_size cannot be negative (0 makes the pad SMD)")}
        if corner_radius_length is not None:
            crl = _num(corner_radius_length)
            if crl is None or crl < 0:
                return {"ok": False, "reason": (
                    "corner_radius_length must be a length of 0 or more")}
            cr = _pad_corner_pct({"corner_radius_length": crl,
                                  "x_size": nums["x_size"],
                                  "y_size": nums["y_size"]}, units)
        else:
            cr = whole_number(corner_radius)
        if cr is None or not 0 <= cr <= 100:
            return {"ok": False, "reason": (
                "corner_radius is a whole percentage, 0 to 100")}
        params: dict[str, Any] = {
            "designator": designator,
            "x": _len(nums["x"], units),
            "y": _len(nums["y"], units),
            "x_size": _len(nums["x_size"], units),
            "y_size": _len(nums["y_size"], units),
            "hole_size": _len(nums["hole_size"], units),
            "shape": shape,
            "layer": layer,
            "rotation": _mil(nums["rotation"]),
            "corner_radius": cr,
        }
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_pad", _with_units(params, units))
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_pad")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_via(
        x: float,
        y: float,
        size: float = 40,
        hole_size: float = 20,
        footprint_name: str = "",
        units: str = "mil",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Add a through-stack via to a footprint: the named one, or the current one.

        The via spans top to bottom. Its position is relative to the
        footprint origin, like a pad's. It goes into the focused PCB
        library; give ``library_path`` to refuse when that is not the one
        you mean.

        Args:
            x, y: Via centre, relative to the footprint origin.
            size: Via diameter (default 40 mil).
            hole_size: Drill diameter (default 20 mil). Must be below size.
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for every length here.
            library_path: Full path to the .PcbLib. When given, the call
                refuses unless that library is the focused one.

        Returns:
            Dictionary confirming the via was added
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = {
            "x": format_length(x),
            "y": format_length(y),
            "size": format_length(size),
            "hole_size": format_length(hole_size),
        }
        if footprint_name:
            params["footprint_name"] = footprint_name
        if library_path:
            params["library_path"] = library_path
        result = await bridge.send_command_async(
            "library.add_footprint_via", _with_units(params, units))
        return result

    @mcp.tool()
    async def lib_add_footprint_vias(
        vias: list[dict[str, Any]],
        size: float = 40,
        hole_size: float = 20,
        footprint_name: str = "",
        units: str = "mil",
        library_path: str = "",
    ) -> dict[str, Any]:
        """Add many through-stack vias to a footprint in ONE call.

        A via grid under an exposed pad is 9 to 50 vias: one call places
        them all, settles the footprint once, and replies with what was
        placed and what was not. Positions are relative to the footprint
        origin. Same rules as lib_add_footprint_via.

        Args:
            vias: list of ``{"x": .., "y": ..}``, each with an optional
                ``size`` and ``hole_size`` overriding the defaults below.
            size: Via diameter for vias that give none (default 40 mil).
            hole_size: Drill for vias that give none (default 20 mil).
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for every length here.
            library_path: Full path to the .PcbLib. When given, the call
                refuses unless that library is the focused one.

        Returns:
            Dict with added, failed, bad_size (hole not inside the
            diameter), bad_number, total; a via refused here before
            sending is listed in ``refused`` with the reason.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _vias_payload(vias, size, hole_size)
        if not payload:
            return {"error": "No valid vias", "added": 0,
                    "total": len(vias or []), "refused": problems}
        params: dict[str, Any] = {"vias": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        if library_path:
            params["library_path"] = library_path
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_vias", _with_units(params, units),
            timeout=60.0)
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_create_3d_model(
        footprint_name: str,
        solids: list[dict[str, Any]],
        source: str,
        output_dir: str = "",
        replace_existing: bool = True,
    ) -> dict[str, Any]:
        """Build a STEP model from the datasheet drawing and link it to a
        footprint in the ACTIVE .PcbLib.

        The footprint's existing 3D bodies are removed first
        (``replace_existing``, reported as ``replaced_bodies``), so a
        rebuilt model does not sit beside the old box.

        For a part no package family covers (a connector, a LED with a
        lens, a header, a switch): read each feature off the mechanical
        drawing and give it as a primitive. lib_create_footprint_from_package
        already models the standard families; an extruded body
        (lib_add_footprint_bodies) is only the fallback.

        Args:
            footprint_name: the footprint to link the model to.
            solids: primitives in mm, origin at the footprint origin, Z up
                from the board (a top-side part sits on Z=0). Each has
                ``kind`` and ``role`` (colour: body, metal, ceramic, ball,
                white, lens, gold, pin1) and:
                box ``x0 y0 z0 x1 y1 z1``; cylinder ``r`` with ``cx cy
                z0 z1`` (vertical) or ``axis: "x"``, ``cy cz x0 x1``;
                sphere ``cx cy cz r``; prism ``profile`` [[u, z], ...]
                in the XZ plane swept from ``y0`` to ``y1`` (``axis``
                "y"), or in the YZ plane swept from ``x0`` to ``x1``
                (``axis`` "x").
            source: the drawing the dimensions come from (document,
                revision, page or figure). Required: a model is not built
                from guesses.
            output_dir: folder for the .step; empty puts it in a ``3D``
                folder beside the active library.
            replace_existing: remove the footprint's 3D bodies before
                linking (default True); False adds this one beside them.

        Returns:
            ``ok``, ``path``, ``writer``, ``height_mm``, ``linked`` (the
            link reply) and ``replaced_bodies``, or ``ok: false`` with
            ``problems`` or ``reason``. ``ok`` and ``success`` are false
            when the link reply's ``placement_ok`` is false (the body was
            added in memory but does not sit on its footprint).
        """
        from ..design import models3d

        plan, problems = models3d.plan_from_solids(solids, source)
        if plan is None:
            return {"ok": False, "problems": problems}
        bridge = get_bridge()
        folder = Path(output_dir) if output_dir else None
        if folder is None:
            active = await bridge.send_command_async(
                "application.get_active_document", {}) or {}
            doc = str(active.get("file_path") or "") if isinstance(active, dict) else ""
            folder = (Path(doc).parent / "3D" if doc.lower().endswith(".pcblib")
                      else get_config().workspace_dir / "models")
        safe = "".join(ch if ch.isalnum() or ch in "-_." else "_"
                       for ch in footprint_name) or "model"
        wrote = await asyncio.to_thread(
            models3d.write_step, plan, folder / f"{safe}.step",
            name=footprint_name or safe)
        if not wrote["ok"]:
            return {"ok": False, "reason": wrote.get("reason"),
                    "tried": wrote.get("tried")}
        replaced = (await _remove_old_bodies(bridge, footprint_name)
                    if replace_existing else None)
        linked = await bridge.send_command_async(
            "library.link_3d_model",
            {"component_name": footprint_name, "model_path": wrote["path"],
             "offset_x": 0, "offset_y": 0, "offset_z": 0,
             "rotation_x": 0, "rotation_y": 0, "rotation_z": 0})
        ok = isinstance(linked, dict) and linked.get("success") is not False
        out = {"ok": ok, "path": wrote["path"], "writer": wrote["writer"],
               "height_mm": plan.height, "solids": len(plan.solids),
               "source": source, "linked": linked,
               "replaced_bodies": replaced}
        # The link handler always says success; whether the body sits on
        # its footprint is placement_ok (false = misplaced or unreadable,
        # null = unverified). Same judgement as lib_link_3d_model.
        if ok and linked.get("placement_ok") is False:
            out = _mark_failed(out, (
                "the STEP model was written and linked in memory but does "
                "not sit on its footprint: " + str((linked.get("placement")
                                                    or {}).get("problem")
                                                   or "placement check failed")
                + ". Not saved; remove it with lib_delete_footprint_primitives "
                "(object_type componentbody) before saving"))
        return out

    @mcp.tool()
    async def lib_add_footprint_bodies(
        bodies: list[dict[str, Any]],
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add extruded 3D bodies to a PcbLib footprint in ONE call.

        THE FALLBACK BODY. A part's 3D body should be its STEP model:
        the vendor's, linked with lib_link_3d_model, or one generated from
        the package description (lib_create_footprint_from_package does
        that first). Use this for a part with neither, or for a clearance
        or keep-in volume: an outline lifted from standoff_height to
        overall_height.

        Args:
            bodies: list of ``{"points": [[x, y], ...], "overall_height":
                ..}``, the outline relative to the footprint origin, with
                optional ``standoff_height`` (default 0), ``layer``
                (default TopLayer, the side the body projects from; a
                mechanical layer such as Mechanical13 is common),
                ``identifier`` (a name to find the body again by),
                ``color`` (Altium TColor integer) and ``opacity`` (0-1).
            footprint_name: footprint to write into; empty uses the
                current one.
            units: "mil" (default) or "mm", for outline and heights.

        Returns:
            Dict with added, failed, bad_points, bad_height,
            unknown_layers, total and ``bodies``, each read back
            (outline, heights, identifier, layer) with which optional
            properties were applied.
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        payload, problems = _bodies_payload(bodies)
        if not payload:
            return {"error": "No valid bodies", "added": 0,
                    "total": len(bodies or []), "refused": problems}
        params: dict[str, Any] = {"bodies": payload}
        if footprint_name:
            params["footprint_name"] = footprint_name
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_bodies", _with_units(params, units))
        if isinstance(result, dict) and problems:
            result["refused"] = problems
        return result

    @mcp.tool()
    async def lib_set_via_properties(
        x: float,
        y: float,
        library_path: str = "",
        footprint_name: str = "",
        units: str = "mil",
        hole_size: Optional[float] = None,
        size: Optional[float] = None,
        dia_mid: Optional[float] = None,
        dia_bottom: Optional[float] = None,
        stack_mode: Optional[str] = None,
        tenting_top: Optional[bool] = None,
        tenting_bottom: Optional[bool] = None,
        mask_expansion: Optional[float] = None,
        mask_bottom_expansion: Optional[float] = None,
        mask_separate: Optional[bool] = None,
        mask_manual: Optional[bool] = None,
        mask_from_hole_edge: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Modify one via inside a footprint of an open PCB library.

        The via is found by its position relative to the footprint origin
        (within 1 mil, whatever ``units``). No via there is an error, and so
        is more than one: nothing is changed. Only the fields given are
        written, and each is read back: one that did not take sets
        ``all_writes_ran`` false. A middle or bottom diameter raises the via
        to a local-stack via in the same call. The mask expansions are set
        through the pad cache.

        Tenting only persists while the via's mask expansion is manual
        (measured on Altium 26.11). A via whose mask follows the design
        rules is switched to a manual expansion at the value it already
        has before tenting is written, and ``mask_made_manual`` says so.

        Args:
            x, y: The via centre, relative to the footprint origin.
            library_path: Full path to the open .PcbLib; it must be the
                focused library, or the call refuses. Empty: the focused
                library.
            footprint_name: footprint to change; empty uses the current one.
            units: "mil" (default) or "mm".
            hole_size: Drill diameter.
            size: Top-layer diameter.
            dia_mid: Middle-layer diameter (makes a local-stack via).
            dia_bottom: Bottom-layer diameter (makes a local-stack via).
            stack_mode: "simple" (one diameter on every layer) or
                "top-middle-bottom", written before the sizes.
            tenting_top, tenting_bottom: Solder mask tenting on each side.
            mask_expansion: Top solder mask expansion (manual).
            mask_bottom_expansion: Bottom solder mask expansion (manual).
            mask_separate: Separate (True) or linked (False) mask expansions.
            mask_manual: True keeps the mask as written; False hands it
                back to the design rule (not with a mask value).
            mask_from_hole_edge: Measure the mask from the drill edge.

        Returns:
            Dictionary with ``all_writes_ran``, the via's drill and size,
            its tenting and mask expansions read back, ``mask_source``
            (manual or rule), ``mask_from_hole_edge``, ``stack_mode``,
            ``mid_unverified`` (a middle diameter that reads back 0 on
            this build, so the write could not be checked) and
            ``mask_made_manual``. A field this build cannot read comes back
            null.
        """
        if stack_mode is not None and stack_mode not in ("simple",
                                                         "top-middle-bottom"):
            return {"success": False,
                    "error": "stack_mode must be simple or top-middle-bottom"}
        if stack_mode == "simple" and (dia_mid is not None
                                       or dia_bottom is not None):
            return {"success": False,
                    "error": "a simple via has one size; dia_mid and "
                             "dia_bottom need top-middle-bottom"}
        if mask_manual is False and (mask_expansion is not None
                                     or mask_bottom_expansion is not None):
            return {"success": False,
                    "error": "mask_manual=False hands the mask to the rule; "
                             "a mask value with it would be ignored"}
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        params: dict[str, Any] = {
            "x": format_length(x),
            "y": format_length(y),
        }
        if library_path:
            params["library_path"] = library_path
        if footprint_name:
            params["footprint_name"] = footprint_name
        if stack_mode is not None:
            params["stack_mode"] = stack_mode
        for key, value in (("hole_size", hole_size), ("size", size),
                           ("dia_mid", dia_mid), ("dia_bottom", dia_bottom)):
            if value is not None:
                params[key] = format_length(value)
        for key, value in (("tenting_top", tenting_top),
                           ("tenting_bottom", tenting_bottom),
                           ("mask_separate", mask_separate),
                           ("mask_manual", mask_manual),
                           ("mask_from_hole_edge", mask_from_hole_edge)):
            if value is not None:
                params[key] = "true" if value else "false"
        for key, value in (("mask_expansion", mask_expansion),
                           ("mask_bottom_expansion", mask_bottom_expansion)):
            if value is not None:
                params[key] = format_length(value)
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_via_properties", _with_units(params, units))

    @mcp.tool()
    async def lib_count_layer_objects(library_path: str = "") -> dict[str, Any]:
        """Count what is on each layer across every footprint of a PCB library.

        Read only. Answers "is this layer empty in every footprint?" before a
        layer is disabled or a layer scheme is tidied, and names the
        footprints that use each layer, so a layer that is not empty says
        where to look. Covers tracks, arcs, text, regions, fills, 3D bodies,
        pads, vias, polygons, dimensions and coordinate markers. Mechanical
        layers past 16 are named by number (``Mechanical17``...).

        Args:
            library_path: Full path to the open .PcbLib; it must be the
                focused library, or the call refuses. Empty: the focused
                library.

        Returns:
            Dictionary with ``layers`` (``{layer, count, footprints}`` for
            each layer that holds anything), ``footprints`` and ``objects``
        """
        bridge = get_bridge()
        params = {"library_path": library_path} if library_path else {}
        return await bridge.send_command_async(
            "library.count_layer_objects", params, timeout=60.0)

    @mcp.tool()
    async def lib_get_library_origin(library_path: str) -> dict[str, Any]:
        """Report where a PCB library's origin marker sits.

        Read from the library's backing board, the same one footprint
        coordinates are written against. The library must already be open
        and focused in Altium.

        Args:
            library_path: Full path to the open .PcbLib. It must be the
                focused library, or the call refuses.

        Returns:
            Dictionary with origin_x_mil, origin_y_mil and library_path
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_library_origin", {"library_path": library_path})

    @mcp.tool()
    async def lib_add_footprint_pads(
        pads: list[dict[str, Any]],
        allow_blank_designator: bool = False,
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add MANY pads to the current footprint in ONE call. PREFER THIS
        over looping lib_add_footprint_pad. Coordinates are not snapped.

        Args:
            pads: List of pad dicts, each with (lengths in ``units``):
                - designator (str, required) -- e.g. "1", "2", "A1". A
                  blank one is dropped and counted unless the pad is
                  unplated, carries "allow_blank_designator": true, or
                  the call passes allow_blank_designator=True.
                - x, y       (float), pad centre relative to the
                  footprint origin; fractions are kept
                - x_size, y_size (float, default 60 mil)
                - hole_size  (float, default 0 = SMD)
                - shape      (str, default "rectangular"), one of
                  round / rectangular / octagonal / roundrect (modern IPC
                  rounded-rectangle). Oval = round with x_size!=y_size.
                  roundrect is set on every copper layer, so it works on
                  through-hole pads too.
                - corner_radius (int %, default 25) for shape="roundrect",
                  or corner_radius_length as a length (converted to
                  that % of half the shortest side)
                - rotation   (float deg, default 0)
                - layer      (str, default "TopLayer"). Only used for SMD
                  pads (hole_size=0); a drilled pad is forced through-hole
                  (MultiLayer). Use "BottomLayer" for bottom-side SMD.
                Optional, written only when given:
                - plated (bool): False makes an NPTH hole.
                - hole_type (str): round / square / slot; slot_length
                  and hole_rotation (deg) for a slot.
                - hole_length: a slot length; above hole_size it makes a
                  slotted hole hole_size wide, along the pad's X before
                  rotation, without hole_type. Without hole_rotation
                  such a slot runs along the pad's longer side.
                - mask_expansion, paste_expansion (float): a manual
                  per-pad override of the design rule; minus the larger
                  pad side closes the paste opening.
                The reply counts them (slots, unplated, expansions) and
                any write this Altium build refused (property_failures).
            allow_blank_designator: Keep pads whose designator is blank
                (fiducials, mechanical pads) instead of dropping them.
            component_name: Footprint to add to, for the whole batch. It
                is made the library's current footprint, and the switch is
                checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first; refused when a
                different document ends up in front.
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm" for every pad in the call: one
                land pattern, one unit, so half a footprint cannot be
                drawn in the wrong one. Millimetres are kept exact.

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
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        payload, skipped_invalid = _pads_payload(
            pads, units, allow_blank=allow_blank_designator)

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
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_pads", _with_units(params, units))
        if isinstance(result, dict) and skipped_invalid:
            result["skipped_invalid"] = skipped_invalid
        return result

    @mcp.tool()
    async def lib_add_footprint_track(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        width: Optional[float] = None,
        layer: str = "TopOverlay",
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add a track to a footprint (for silkscreen/courtyard).

        Lengths are DECIMAL, in ``units``, kept exact rather than rounded
        to whole mils: a 0.15 mm courtyard line is 5.9055 mil, not 6.

        Args:
            x1: Start X, relative to the footprint origin
            y1: Start Y
            x2: End X
            y2: End Y
            width: Track width (default 10 mil, in the call's unit)
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
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm", for every length and
                coordinate here.

        Returns:
            Dict with ``success``, ``footprint``, ``layer`` and
            ``pending_save`` (run ``lib_save_library`` to write the file).
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        if width is None:
            width = length_in(units, 10)
        coords = [_num(v) for v in (x1, y1, x2, y2)]
        w = _num(width)
        if any(c is None for c in coords) or w is None or w <= 0:
            return {"ok": False, "reason": (
                "x1, y1, x2, y2 must be numbers and width a positive number, "
                f"all in {units}")}
        params: dict[str, Any] = {
            "x1": _len(coords[0], units), "y1": _len(coords[1], units),
            "x2": _len(coords[2], units), "y2": _len(coords[3], units),
            "width": _len(w, units), "layer": layer,
        }
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_track", _with_units(params, units))
        hint = BulkHintTracker.record_and_hint("lib_add_footprint_track")
        if hint and isinstance(result, dict):
            result["_hint_bulk"] = hint
        return result

    @mcp.tool()
    async def lib_add_footprint_tracks(
        tracks: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add MANY tracks to a footprint in ONE call.

        PREFER THIS over looping `lib_add_footprint_track`: one call,
        one undo step and one save for a whole outline.

        Lengths are DECIMAL, in ``units``, kept exact, so a courtyard need
        not be rounded outward to whole mils.

        Args:
            tracks: List of track dicts, each with:
                - x1, y1, x2, y2 (decimals kept) -- endpoints, relative
                  to the footprint origin
                - width (default 10 mil, in the call's unit)
                - layer (str, default "TopOverlay" silkscreen). Any
                  Altium layer name, e.g. "BottomOverlay" or
                  "Mechanical1".."Mechanical32" for courtyard/assembly.
            component_name: Footprint to draw in, for the whole batch. It
                is made the library's current footprint, and the switch is
                checked. Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm" for every track in the call.

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
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        payload, skipped_invalid = _tracks_payload(tracks, units)
        if not payload:
            return {
                "error": "No valid tracks (each needs numeric x1,y1,x2,y2 "
                         "and a positive width)",
                "added": 0,
                "total": len(tracks),
                "skipped_invalid": skipped_invalid,
            }
        params: dict[str, Any] = {"tracks": payload}
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_tracks", _with_units(params, units))
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
        width: Optional[float] = None,
        layer: str = "TopOverlay",
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add an arc to a footprint.

        Centre, radius and width are DECIMAL, in ``units``, kept exact.

        Args:
            x_center: Center X, relative to the footprint origin
            y_center: Center Y
            radius: Arc radius
            start_angle: Start angle in degrees
            end_angle: End angle in degrees
            width: Line width (default 10 mil, in the call's unit)
            layer: Layer name. Default "TopOverlay" (silkscreen). Any
                Altium layer accepted, e.g. "Mechanical1".."Mechanical32"
                for pin-1 / assembly markers.
            component_name: Footprint to draw in. It is made the
                library's current footprint, and the switch is checked.
                Defaults to the current footprint.
            library_path: Optional .PcbLib to focus first.
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm", for every length and
                coordinate here.

        Returns:
            Dict with ``success``, ``footprint``, ``layer`` and
            ``pending_save``.
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        if width is None:
            width = length_in(units, 10)
        nums = [_num(v) for v in (x_center, y_center, radius, start_angle,
                                  end_angle, width)]
        if any(n is None for n in nums) or nums[2] <= 0 or nums[5] <= 0:
            return {"ok": False, "reason": (
                "x_center, y_center, start_angle and end_angle must be "
                f"numbers, and radius and width positive numbers ({units})")}
        params: dict[str, Any] = {
            "x_center": _len(nums[0], units),
            "y_center": _len(nums[1], units),
            "radius": _len(nums[2], units),
            "start_angle": _mil(nums[3]),
            "end_angle": _mil(nums[4]),
            "width": _len(nums[5], units),
            "layer": layer,
        }
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_arc", _with_units(params, units))
        return result

    @mcp.tool()
    async def lib_add_footprint_text(
        text: str,
        x: float = 0,
        y: float = 0,
        size: Optional[float] = None,
        width: Optional[float] = None,
        rotation: float = 0,
        layer: str = "TopOverlay",
        use_ttfont: bool = False,
        mirror: bool = False,
        library_path: Optional[str] = None,
        component_name: Optional[str] = None,
        registration: str = "both",
        remove_from_board: bool = False,
        footprint_name: str = "",
        units: str = "mil",
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
            x, y: Coordinates in DECIMAL ``units``, relative to the
                FOOTPRINT's origin, kept exact.
            size: Text height (default 50 mil, in the call's unit). 50 mil
                is a common silkscreen size; drop to 30-40 for tight
                footprints.
            width: Stroke width (default 8 mil). 8 reads cleanly at 50 mil
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
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm", for every length and
                coordinate here, kept exact rather than rounded to
                whole mils.

        Returns:
            Dict with ``success``, ``footprint``, ``text``, ``layer``,
            ``x``, ``y``, ``registration``, ``new_objects``,
            ``group_yields``, ``duplicates_found``, ``duplicates_removed``,
            ``duplicate_cleanup``, ``board_removal`` and ``pending_save``.
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        if not text:
            return {"ok": False,
                    "reason": "text is required: pass the string to place"}
        if size is None:
            size = length_in(units, 50)
        if width is None:
            width = length_in(units, 8)
        nums = [_num(v) for v in (x, y, size, width, rotation)]
        if any(n is None for n in nums) or nums[2] <= 0 or nums[3] <= 0:
            return {"ok": False, "reason": (
                "x, y and rotation must be numbers, and size and width "
                f"positive numbers ({units})")}
        mode = (registration or "both").strip().lower()
        if mode not in _TEXT_REGISTRATIONS:
            return {"ok": False, "reason": (
                f"registration must be one of {', '.join(_TEXT_REGISTRATIONS)}")}
        bridge = get_bridge()
        params: dict[str, Any] = _with_units({
            "text": text,
            "x": _len(nums[0], units), "y": _len(nums[1], units),
            "size": _len(nums[2], units), "width": _len(nums[3], units),
            "rotation": _mil(nums[4]), "layer": layer,
        }, units)
        if footprint_name and not component_name:
            params["footprint_name"] = footprint_name

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
        footprint_name: str = "",
        units: str = "mil",
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
                - x1, y1, x2, y2 (in ``units``, decimals kept) --
                  opposite corners, relative to the footprint origin,
                  either order
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
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm", for every corner, kept exact.

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
            ``skipped`` lists fills refused here before sending (item and
            reason), and ``refused`` the same refusals as readable strings.
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        payload, skipped = _fills_payload(fills, units)
        # ``refused``: the same refusals as readable strings ("fill 0 has
        # no layer"), upstream's shape; ``skipped`` keeps item and reason.
        refused = [f"fill {s['item']} {s['reason']}" for s in skipped]
        if not payload:
            return {"ok": False, "error": "No valid fills",
                    "reason": "No valid fills to send",
                    "added": 0, "total": len(fills or []),
                    "skipped": skipped, "refused": refused}
        params: dict[str, Any] = {"fills": payload}
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_fills", _with_units(params, units))
        if isinstance(result, dict) and skipped:
            result["skipped"] = skipped
            result["refused"] = refused
        return result

    @mcp.tool()
    async def lib_add_footprint_regions(
        regions: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        footprint_name: str = "",
        units: str = "mil",
    ) -> dict[str, Any]:
        """Add polygonal regions on ANY layer of a footprint, in ONE call.

        The shapes a rectangle cannot make: a plus-shaped keep-out between
        a crystal's pads, an L-shaped mask opening, a chamfered paste
        aperture, a polygon-pour cutout under an RF pad. Inside the
        footprint they travel with the part to every board.

        NOT ``pcb_place_region``, which acts on a BOARD.

        Args:
            regions: List of region dicts, each with:
                - vertices: list of [x, y] points in ``units`` (decimals
                  kept), relative to the footprint origin, at least 3. Do
                  not repeat the first point at the end; a repeat is
                  dropped. ("points" is accepted for the same list.) A
                  point given as [x, y, sweep] (or {"x", "y", "sweep"})
                  makes the edge from it to the next point a TRUE ARC of
                  ``sweep`` degrees, counterclockwise when positive, e.g.
                  a slot end or a rounded paste window; 0 is a straight
                  edge, and |sweep| must be under 360.
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
            footprint_name: the same as component_name.
            units: "mil" (default) or "mm", for every vertex, kept exact.

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
            ``arc_edges`` (arc edges read back off the regions made),
            ``failures`` (item and reason), ``unknown_layers``,
            ``kind_requested`` / ``kind_applied``, ``keepout_requested`` /
            ``keepout_applied``, ``footprint`` and ``pending_save``.
            ``skipped`` lists regions refused here before sending.
        """
        refusal = _units_refusal(units) or _target_refusal(
            component_name, footprint_name)
        if refusal:
            return refusal
        payload, skipped = _regions_payload(regions, units)
        # ``refused``: the same refusals as readable strings, the shape the
        # land-pattern tooling reads; ``skipped`` keeps item and reason.
        refused = [f"region {s['item']}: {s['reason']}" for s in skipped]
        if not payload:
            return {"ok": False,
                    "reason": "No valid regions to send",
                    "added": 0, "total": len(regions), "skipped": skipped,
                    "refused": refused}
        params: dict[str, Any] = {"regions": payload}
        _add_target(params, component_name, footprint_name, library_path)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_footprint_regions", _with_units(params, units))
        if isinstance(result, dict) and skipped:
            result["skipped"] = skipped
            result["refused"] = refused
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

        Runs Extract Sources and reports which files appeared (in a folder
        named after the IntLib, or beside it). Pass the reported paths as
        ``library_path`` to the other library tools.

        Args:
            intlib_path: Absolute path to the .IntLib file.

        Returns:
            Dict with ``intlib_path``, ``extract_dir``, ``sch_lib_path``,
            ``sch_lib_found``, ``pcb_lib_path``, ``pcb_lib_found``. A
            ``_found`` flag of False means that source did not appear (the
            IntLib lacks it, the folder was not writable, or this build
            names the command differently).
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

        Each convention is the library's most common one, so any house
        style works; ``policy`` pins one instead, e.g.
        ``{"silk_layer": "Top Overlay", "courtyard": true}``. Each finding
        gives footprint, dimension, expected and actual. Designators must
        sit on the average pad centre, within
        ``policy={"designator_center_tol": <mils>}``. One library pass.

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
            fix_layer: move designators that are not on the convention
                layer onto it.
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
        """Set every footprint's height in the active PcbLib from its
        tallest 3D body, for the height-clearance DRC (a height of 0
        makes that rule pass silently).

        Args:
            mode: ``raise`` (default) only increases a height, so a
                hand-set one survives. ``match`` also lowers it to the
                model; use it when heights are too tall. For one
                footprint, or one with no model, use
                ``lib_set_footprint_height``.

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
          - UNCONFIRMED THAT THE HEIGHTS REACH THE FILE. This sweep
            writes Height without switching to each footprint after the
            write, which is exactly how ``lib_set_footprint_height`` lost
            51 of 55 heights on save (2026-10-10) before it gained that
            switch. Until that is measured for the sweep, save, then read
            two of the ``items`` back from the file
            (``lib_read_pcblib_file``); if they are missing, set them
            with ``lib_set_footprint_height``. The reply carries
            ``heights_confirmed_on_save: false`` and a ``save_note``.

        Returns:
            Dict with ``mode``, ``inspected``, ``updated``, ``lowered``,
            ``without_model``, ``without_model_names``,
            ``heights_confirmed_on_save`` (always false), ``save_note``
            and ``items`` (``{name, old_height_mm, new_height_mm}`` per
            footprint).
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

        THE WRITE NOW REACHES THE SAVE. Measured 2026-10-10: 55 heights
        set, every reply said changed, and after ``lib_save_library`` 51
        of the 55 were not in the file; making each footprint current
        again before the save kept all 55. The handler now switches the
        library to the footprint after the write (the step that made
        them stick) on every path: a footprint that was not current is
        made current and yours is put back; one that was already current
        (``footprint_name`` empty, or naming it) is parked on another
        footprint and switched back. Then it reads the height back and
        marks the library modified.

        Args:
            height_mm: the height in MILLIMETRES, not mils. Must be
                non-negative.
            footprint_name: which footprint. Empty uses the library's
                current component.

        Returns:
            ``{name, old_height_mm, new_height_mm, read_back_height_mm,
            read_back_before_switch_mm, changed, was_current,
            parked_elsewhere_first, made_current_after_write,
            current_restored, marked_modified, mark, pending_save,
            written_to_disk, saved, save_note, note}``.
            ``marked_modified`` is the library's modified flag READ BACK
            after the mark, and ``mark`` says which open document the mark
            found (``path``, ``file_name``, ``found``, ``via``,
            ``modified_read``, ``focused_modified_read``,
            ``recorded_for_save_all`` - true when the library went on the
            bridge's record under its ABSOLUTE path, the only kind
            ``app_save_all`` writes from -, ``problem``); on 2026-10-10 the
            flag read false on every write while the tab showed '*'. When
            it does not register, ``save_note`` names lib_save_library,
            and ``app_save_all`` writes the library anyway from the
            bridge's own record (``written_by_bridge_record``). Prove the
            height reached the file with ``lib_read_pcblib_file``
            (``height_mm``).
            ``read_back_height_mm`` and ``changed`` come from the read
            AFTER the footprint switches, not before them and not from
            what was asked. ``pending_save`` is true only when the height
            changed AND the switch after the write happened, i.e. an
            in-memory change made the way that was measured to survive a
            save; a change without the switch (for instance the only
            footprint in the library, which cannot be switched away from)
            reads ``pending_save: false`` with a ``save_note`` saying the
            save is unmeasured. ``written_to_disk`` and ``saved`` are
            always false. Run ``lib_save_library`` with the library path
            and check its ``written_to_disk``.
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

        With replace=True (default) existing footprint models are removed
        first; False adds another.

        WHICH FOOTPRINT IS CURRENT is decided by ``set_current``, not left
        to chance. Measured before this parameter existed: replace=False
        left BOTH the old and the new footprint flagged current, because
        Altium does not clear the others by itself.

        Args:
            component_name: Name of the schematic component. Resolved by
                library reference and made current before linking, so the
                model lands on the symbol you name rather than on whatever
                was last created. Leave empty to target the symbol held
                from ``lib_create_symbol`` / ``lib_set_current_component``;
                with none held the reply is NO_COMPONENT (the editor's own
                selection is never read). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart.
            footprint_name: Name of the footprint to link
            footprint_library: NOT APPLIED (kept for old callers). The
                footprint is bound by name with an empty location, since
                a .PcbLib path there wedges AD26: install the library and
                keep footprint names unambiguous.
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
        """Link a 3D STEP model to a footprint in the ACTIVE .PcbLib, as a
        component body (drives the 3D view and height DRC).

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
            ``success``, ``footprint``, ``model``, ``units`` ("mils"),
            ``footprint_origin_mils`` and ``moved_to_footprint_origin``
            (align="none" only: the body's own origin was moved onto the
            footprint origin, or needed no move), and:

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
        """List the components in a library: name, alias_name, part_count,
        description, fast. ``part_count`` here can read one high; use
        lib_get_component_details when it matters.

        Args:
            library_path: Library path (default: the active library).
            with_parameters: Add each component's parameters (slow on
                large libraries; for one symbol use
                lib_get_component_details).
            with_designator: Add each default designator ("U?"); also
                loads every symbol, so slow on large libraries.

        Returns:
            ``count`` and ``components`` [{index, name, alias_name,
            part_count, description, parameters?, designator?}]. Pass
            ``index`` as component_index to the details, delete and
            rename tools when a name has characters you cannot type.
            The list is the SAVED file. When the library is open, two more
            keys compare it with the symbols in memory:
            ``unsaved_symbols`` (in the open library, not yet in the file,
            for example renamed or new) and
            ``saved_names_not_in_open_library`` (in the file, gone from the
            open library). Name a symbol as the open library has it.
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
        include_dblibs: bool = True,
    ) -> dict[str, Any]:
        """Search open SchLib documents and installed database libraries.

        Searched: every .SchLib open (in a project or standalone) and every
        installed DbLib, case-insensitive substring. NOT searched: IntLibs,
        query/SVN libraries, workspace components, closed SchLibs, so 0
        hits is not "no such part"; ``not_searched`` and ``coverage_note``
        say what was skipped. It reads files ON DISK: a symbol created and
        not yet saved is not found (use lib_get_component_details).
        DbLib rows match on every column (MPN, value) and are marked
        ``source: "dblib"``; lib_dblib_search narrows to a table.

        DATASHEET DISCIPLINE: fetch a part's datasheet before recommending
        it; do not go on symbol metadata alone.

        Args:
            query: Substring to match.
            search_type: "all" (name, alias, description; default),
                "name", "description", or "parameters" (slow).
            library_path: Restrict to one .SchLib or .DbLib.
            limit: Cap on matches, SchLib and DbLib together (default 100).
            include_dblibs: Also search installed DbLibs (default True;
                slower on large databases).

        Returns:
            ``query``, ``search_type``, ``count``, ``limit``,
            ``truncated``, ``results`` [{name, alias_name, description,
            library_path, part_count, source}; DbLib rows add table,
            key_field, symbol_ref, symbol_library, footprints,
            matched_field], ``_datasheet_guidance``,
            ``libraries_searched``, ``not_searched`` [{library_path,
            library_type, reason}], ``dblib_searches``, ``coverage_note``.
        """
        bridge = get_bridge()
        if library_path and is_dblib_path(library_path):
            result = await _search_named_dblib(
                bridge, library_path, query, search_type, limit)
            if result.get("success") is False:
                return result
            synthetic = {"components": result.get("results") or []}
            return tag_response(
                result, components=synthetic, context="lib_search"
            )
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
        if isinstance(result, dict) and "error" not in result:
            for row in result.get("results") or []:
                if isinstance(row, dict):
                    row.setdefault("source", "schlib")
            result.update(await _search_coverage(bridge, library_path))
            if not library_path:
                await _search_dblibs(bridge, result, query, limit, include_dblibs)
        if isinstance(result, dict):
            synthetic = {"components": (
                result.get("results") or result.get("components") or []
            )}
            return tag_response(
                result, components=synthetic, context="lib_search"
            )
        return result

    # =========================================================================
    # Database libraries (DbLib)
    # =========================================================================

    @mcp.tool()
    async def lib_dblib_info(
        library_path: str,
        with_fields: bool = True,
    ) -> dict[str, Any]:
        """What a database library (.DbLib) declares, and how it connects.

        Call it first on an unfamiliar DbLib: per table it names the key
        column and the symbol and footprint columns that
        ``lib_dblib_get_record`` and ``sch_place_dblib_component`` take.
        The password is never returned (``redacted``, ``has_password``).
        ``key_field_source`` says how the key was found; ``first_column``
        is a guess.

        Args:
            library_path: Full path of the .DbLib (or .SVNDbLib);
                lib_get_installed_libraries lists installed ones.
            with_fields: Connect read-only to list each table's columns
                (default True). False reads the file only.

        Returns:
            {"library_path", "connection": {"provider", "kind" (access /
            excel / sql_server / odbc / oracle / other), "data_source",
            "has_password", "redacted", "read_only"}, "left_quote",
            "right_quote", "search_path", "table_count", "tables": [{"name",
            "enabled", "schema", "key_field", "key_field_source",
            "key_field_found", "symbol_ref_field", "symbol_library_field",
            "description_field", "footprint_fields": [{"ref_field",
            "library_field"}], "fields" (null when not read), "field_count",
            "error", "settings"}], "sections", "fields_included",
            "connected" (null when no connection was attempted)}.
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        bridge = get_bridge()
        params: dict[str, Any] = {"library_path": library_path}
        if not with_fields:
            params["with_fields"] = "false"
        result = await bridge.send_command_async(
            "library.get_dblib_info", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_dblib_search(
        query: str,
        library_path: Optional[str] = None,
        table: Optional[str] = None,
        fields: Optional[list[str]] = None,
        limit: int = 50,
        max_rows: int = 20000,
    ) -> dict[str, Any]:
        """Search database libraries (.DbLib) for components, read-only.

        Case-insensitive substring match over every row of every enabled
        table (lib_search folds these in already; this narrows to a table
        or columns). The query never reaches the database as SQL: rows
        are read and matched inside Altium, bounded by ``max_rows``
        (``scan_capped`` says the bound was hit).

        Args:
            query: Text to find (case-insensitive substring).
            library_path: One .DbLib to search. Omitted, every installed
                database library is searched.
            table: One table the DbLib declares (as lib_dblib_info names
                it). Omitted, every enabled table.
            fields: Column names to match. Omitted, every column. A name
                a table lacks is reported under ``unknown_fields``.
            limit: Cap on returned rows (1 to 1000, default 50).
            max_rows: Cap on rows read in total (default 20000).

        Returns:
            "query", "count", "limit", "truncated", "rows_scanned",
            "scan_capped", "libraries" (per library: searched, an error
            code, tables searched and failed, unknown_fields), and
            "results" (library_path, table, key, key_field, symbol_ref,
            symbol_library, footprints, description, matched_field,
            matched_value). No connection string is ever returned.
        """
        if not isinstance(query, str) or not query.strip():
            return {"success": False, "error": "query is required"}
        if library_path:
            refusal = dblib_path_refusal(library_path)
            if refusal:
                return refusal
        if not 1 <= int(limit) <= 1000:
            return {"success": False, "error": "limit must be between 1 and 1000"}
        if int(max_rows) < 1:
            return {"success": False, "error": "max_rows must be at least 1"}
        names = [str(f).strip() for f in (fields or [])]
        if any(not n or "|" in n for n in names):
            return {"success": False,
                    "error": ("each entry in fields must be a non-empty column "
                              "name without '|', which separates them on the "
                              "way to Altium")}
        params: dict[str, Any] = {
            "query": query,
            "limit": str(int(limit)),
            "max_rows": str(int(max_rows)),
        }
        if library_path:
            params["library_path"] = library_path
        if table:
            params["table"] = table
        if names:
            params["fields"] = "|".join(names)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.query_dblib", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_dblib_get_record(
        library_path: str,
        table: str,
        key: str,
        key_field: Optional[str] = None,
    ) -> dict[str, Any]:
        """Every column of one row of a database library table.

        The row is found by its key, the value in the table's key column
        (``key`` in a ``lib_dblib_search`` result; the column is
        ``key_field`` in ``lib_dblib_info``). The key is passed to the
        database as a parameter, never as SQL text. ``lookup`` says how
        the row was found: ``parameter`` when the database matched it,
        ``scan`` when the parameterised query could not run and the
        table was read and compared instead.

        Args:
            library_path: Full path of the .DbLib file.
            table: A table the DbLib declares.
            key: The row's key value.
            key_field: Optional column to match instead of the DbLib's
                own key column; it must be a column of the table.

        Returns:
            {"library_path", "table", "key", "key_field",
            "key_field_source", "lookup", "found", "match_count",
            "rows_scanned", "scan_capped", "symbol_ref", "symbol_library",
            "footprints": [{"ref", "library"}], "description",
            "fields": {column: value}}. ``found`` False is an answer, not
            an error: the key is not in that table. ``match_count`` above
            1 means the key is not unique and the first row is shown.
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        if not isinstance(table, str) or not table.strip():
            return {"success": False, "error": "table is required"}
        if not isinstance(key, str) or not key.strip():
            return {"success": False, "error": "key is required"}
        params: dict[str, Any] = {
            "library_path": library_path, "table": table, "key": key,
        }
        if key_field:
            params["key_field"] = key_field
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_dblib_record", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def sch_place_dblib_component(
        library_path: str,
        table: str,
        key: str,
        x: int,
        y: int,
        rotation: int = 0,
        designator: Optional[str] = None,
        sheet_path: Optional[str] = None,
        part_id: Optional[int] = None,
    ) -> dict[str, Any]:
        """Place one database library (DbLib) part on a schematic sheet.

        Altium builds the part from the row (symbol, footprints,
        parameters and database link) and drops it at (x, y), with no
        cursor or click. The DbLib must be installed (lib_install_library)
        or in the project, or the call is refused. ``database_linked``
        says whether the placed part kept its link. For a whole sheet use
        sch_draw_template instead.

        Args:
            library_path: Full path of the .DbLib file.
            table: A table the DbLib declares.
            key: The row's key value (``key`` in a lib_dblib_search
                result).
            x, y: Location in mils.
            rotation: 0, 90, 180 or 270.
            designator: Optional designator to stamp on the part.
            sheet_path: Optional .SchDoc path; default the active sheet.
            part_id: Which part of a multi-part component to place, from
                1 (A). Left out, Altium's default part is placed.

        Returns:
            "placed", "library_path", "table", "key", "lib_reference",
            "database_table", "database_linked", "x", "y", "rotation",
            "part_id" (read back), "part_count", "part_id_applied",
            "designator".
        """
        refusal = dblib_path_refusal(library_path)
        if refusal:
            return refusal
        if not isinstance(table, str) or not table.strip():
            return {"success": False, "error": "table is required"}
        if not isinstance(key, str) or not key.strip():
            return {"success": False, "error": "key is required"}
        if int(rotation) not in (0, 90, 180, 270):
            return {"success": False, "error": "rotation must be 0, 90, 180 or 270"}
        if part_id is not None and int(part_id) < 1:
            return {"success": False, "error": "part_id must be 1 or more"}
        params: dict[str, Any] = {
            "library_path": library_path, "table": table, "key": key,
            "x": str(int(x)), "y": str(int(y)), "rotation": str(int(rotation)),
        }
        if designator:
            params["designator"] = designator
        if sheet_path:
            params["sheet_path"] = sheet_path
        if part_id is not None:
            params["part_id"] = str(int(part_id))
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.place_dblib_component", params, timeout=_DBLIB_TIMEOUT)
        return redact_reply(result)

    @mcp.tool()
    async def lib_get_component_details(
        component_name: str = "",
        library_path: Optional[str] = None,
        component_index: Optional[int] = None,
    ) -> dict[str, Any]:
        """Everything about one library symbol in one call: metadata, pins,
        parameters, models, and the style (font_id, color, is_hidden, x,
        y, orientation, justification) of its designator, comment and
        parameters. obj_get_font_spec expands a font_id. Naming
        library_path opens (focuses) that library; nothing is saved.

        DATASHEET DISCIPLINE: pins, parameters and MPN here are hints;
        fetch the datasheet and cite it before stating a pin or rating.

        Args:
            component_name: LibRef in the .SchLib.
            library_path: .SchLib path (default: the focused library).
            component_index: ``index`` from lib_get_components, for a name
                with characters you cannot reproduce. Give exactly one of
                name or index.

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
        check_standard_symbols: bool = False,
        refresh: bool = False,
    ) -> dict[str, Any]:
        """Audit label styles across every symbol of a library in one call.

        Reports each symbol's designator style; comment, parameter styles
        and pins are opt-in. With an ``expect_designator_*`` filter only
        the symbols that do NOT match come back.
        ``check_standard_symbols`` adds ``standard_symbol_warnings``:
        amplifiers drawn as a rectangle instead of a triangle (warns only).

        Args:
            library_path: .SchLib (default: the focused one).
            with_comment: Add the comment style.
            with_parameters: Add parameter styles (slower).
            with_pins: Add pins, ``body`` (rectangle and polygon counts)
                and description.
            check_standard_symbols: Flag rectangular amplifiers (implies
                with_pins).
            expect_designator_font_id: Keep only symbols whose designator
                font differs.
            expect_designator_color: Keep only symbols whose designator
                colour differs (BGR int, e.g. 8388608 for navy).
            limit: Cap on symbols returned (default 5000).
            timeout: Poll timeout in seconds.
            refresh: SAVE the library and read it back in first. Symbols
                are listed from the saved file, so ones created or changed
                since the last save are otherwise missing or stale.

        Returns:
            library_path, count, mismatch_count, limit, truncated,
            filter_applied, refreshed, ``components`` [{name, designator,
            mismatched, comment?, pins?, parameter_styles?}].
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"limit": str(limit)}
        if library_path:
            params["library_path"] = library_path
        if with_comment:
            params["with_comment"] = "true"
        if with_parameters:
            params["with_parameters"] = "true"
        if with_pins or check_standard_symbols:
            params["with_pins"] = "true"
        if expect_designator_font_id is not None:
            params["expect_designator_font_id"] = str(expect_designator_font_id)
        if expect_designator_color is not None:
            params["expect_designator_color"] = str(expect_designator_color)
        if refresh:
            params["refresh"] = "true"
        result = await bridge.send_command_async(
            "library.audit_styles", params, timeout=timeout,
        )
        result = result or {}
        if check_standard_symbols and isinstance(result, dict):
            result["standard_symbol_warnings"] = _standard_symbol_warnings(
                result.get("components") or [])
        return result

    @mcp.tool()
    async def lib_set_label_formats(
        ops: list[dict[str, Any]],
        component_name: Optional[str] = None,
        library_path: Optional[str] = None,
        only_mismatched: bool = True,
        limit: int = 5000,
        timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Set label styles on SchLib symbols, several targets in ONE call
        (no single-target variant), across one symbol or the whole library.

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
             "orientation": <int>,           # optional, 0..3
             "justification": <int>,         # optional, 0..8
             "x": <mils>, "y": <mils>,       # optional, symbol frame
             "text": <str>}                  # optional, e.g. "=Partnumber"

        ``orientation`` is Altium's quarter-turn ORDINAL (0 = 0, 1 = 90,
        2 = 180, 3 = 270 degrees), written to the label as given, NOT a
        degree count: 90 is out of range. ``justification`` is the
        0..8 text-justification ordinal (0 = bottom-left ... 8 =
        top-right). An out-of-range value is refused with
        ``INVALID_ARGUMENT`` naming the op, the field and the range, and
        nothing is sent: live (2026-10-12) Altium stored 90 and 12 as
        given and the save wrote them into the library file.

        At least one style field must be set per op. Targets are
        ``"designator"``, ``"comment"``, or ``"parameter:<Name>"``.
        Use ``obj_get_font_id`` / ``obj_get_font_spec`` to resolve the font_id
        for {name, size, bold, italic} from the active library's font
        table; that keeps the call neutral to any particular library's
        style choices.

        Args:
            ops: Non-empty list of ops; targets cannot contain ';' or
                '~~'.
            component_name: One symbol only; omit for the whole library.
            library_path: .SchLib (default: the focused one).
            only_mismatched: Skip labels already matching (default True).
            limit: Cap on components in a library walk.
            timeout: Poll timeout override.

        Returns:
            library_path, scope ("single"|"bulk"), total, limit,
            truncated, and ``ops`` [{target, modified, already_compliant,
            missing_target, failed}].
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
            unknown = sorted(set(op) - _LABEL_OP_KEYS)
            if unknown:
                # A key this does not encode would be dropped on the way to
                # Altium while the reply still said "modified". That is how
                # designator x/y moves reported success and never moved.
                return {"ok": False, "reason":
                        f"ops[{i}] has keys this tool does not set: "
                        f"{unknown}. Known keys: "
                        f"{sorted(_LABEL_OP_KEYS)}"}
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
            for key, top in (("orientation", 3), ("justification", 8)):
                if op.get(key) is not None:
                    value = whole_number(op[key])
                    if value is None:
                        return {"ok": False, "reason":
                                f"ops[{i}].{key} must be a whole number"}
                    # Live 2026-10-12 (P11): Altium does not refuse an
                    # out-of-enum ordinal, it STORES it and the save writes
                    # it. The handler repeats this check.
                    if not 0 <= value <= top:
                        return {
                            "ok": False, "success": False,
                            "error": "INVALID_ARGUMENT",
                            "reason": (
                                f"ops[{i}].{key}={value} is out of range "
                                f"0..{top}"
                                + (" (the quarter-turn ordinal: 0 = 0, "
                                   "1 = 90, 2 = 180, 3 = 270 degrees)"
                                   if key == "orientation" else
                                   " (0 = bottom-left ... 8 = top-right)")
                                + "; nothing was sent."),
                        }
                    parts.append(f"{key}={value}")
                    style_set = True
            for axis in ("x", "y"):
                if op.get(axis) is not None:
                    parts.append(f"{axis}={int(round(float(op[axis])))}")
                    style_set = True
            if op.get("text") is not None:
                text = str(op["text"])
                if not text or ";" in text or "~~" in text:
                    return {"ok": False, "reason":
                            f"ops[{i}].text must be non-empty and contain "
                            "neither ';' nor '~~'"}
                parts.append(f"text={text}")
                style_set = True
            if not style_set:
                return {"ok": False, "reason":
                        f"ops[{i}] must set at least one of font_id / "
                        "color / is_hidden / orientation / justification "
                        "/ x / y / text"}
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
        """Name, enable and set the kind of a library's mechanical layers.

        Takes the library by PATH (unlike the pcb_* layer tools, which act
        on whatever is focused) and refuses unless that library is the one
        focused. Every change is read back and failures are reported per
        layer. A Top/Bottom kind ("Courtyard Top") belongs to a layer PAIR,
        so give the partner kind to another layer in the same call. Use
        ``lib_run_across`` to apply one scheme to many libraries.

        Args:
            layers: [{layer (e.g. "Mechanical13"), name?, enabled?,
                kind?}].
            library_path: Library to edit (default: the focused one; only
                safe for a single library).
            tidy_pairs: Also remove layer pairs whose two layers are not a
                matching Top/Bottom kind pair (default off: it removes
                pairs).
            timeout: Seconds.

        Returns:
            ``library``, ``layers`` [{layer, changed, problem}],
            ``changed``, ``failed``, ``kinds_displaced``,
            ``pairs_removed``, ``pairs_tidied``, ``pairs_scanned_to``.
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
        """Run one library command against several libraries in one call
        (the loop runs inside Altium; each library is still opened).

        Works for commands that accept a ``library_path`` (most reads and
        batch edits). Footprint editing commands (``create_footprint``,
        ``add_footprint_pad``, ``link_3d_model``...) act on the open
        library and cannot be swept.

        Failure is per library: one that will not open does not stop the
        rest, and each is reported separately.

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

        Each assignment updates or creates one parameter. Three names set
        properties instead: ``"Description"``, ``"Designator"`` (the
        default, e.g. ``"U?"``) and ``"Comment"`` (e.g. ``"=Partnumber"``).
        A created parameter is hidden; ``lib_set_label_formats`` shows,
        hides or moves it.

        Args:
            assignments: List of dicts with keys:
                - component_name: Name of the component in the library
                - param_name: Parameter name (e.g., "Partnumber", "Manufacturer"),
                  or the special "Description" / "Designator" / "Comment" keys
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
        component_name: str = "",
        color: Optional[int] = None,
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
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.
            color: line colour (Altium colour integer); omit for the
                default.

        Returns:
            Dictionary confirming arc addition, with ``color`` and
            ``line_width`` read back
        """
        params: dict[str, Any] = {
            "x_center": _snap_grid(x_center, grid),
            "y_center": _snap_grid(y_center, grid),
            "radius": _snap_grid(radius, grid),
            "start_angle": start_angle,
            "end_angle": end_angle,
            "width": width,
        }
        if component_name:
            params["component_name"] = component_name
        if color is not None:
            params["color"] = int(color)
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_arc",
            params,
        )
        return result

    @mcp.tool()
    async def lib_add_symbol_polygon(
        vertices: str,
        grid: int = 25,
        component_name: str = "",
        fill_color: Optional[int] = None,
        owner_part_id: Optional[int] = None,
        color: Optional[int] = None,
        line_width: Optional[int] = None,
        is_solid: Optional[bool] = None,
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
            component_name: The symbol to edit, by library reference,
                in the active SchLib. It becomes the held (current)
                symbol for later calls without a name. Unlike
                ``lib_set_current_component`` it is found without
                reopening the library (a symbol made this session and
                no longer the last one made may need
                ``lib_set_current_component`` first), and the edit
                lands on the symbol's current part (its CurrentPartID,
                which can differ from the part the editor's spinner
                shows), or on part 1 / display mode 0 when it reports
                none. Omit to
                edit the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT. The symbol merely selected in the
                editor is never the target and never a fallback: it is
                only compared with the target, to pick how the
                primitive goes in (``shown`` in the reply). The held symbol does not survive
                a StartMCPServer re-run or an Altium restart, so after a
                recovery pass ``component_name`` or call
                ``lib_set_current_component`` again.
            fill_color: omit for a solid glyph in the line colour (a diode
                triangle); a colour such as 8454143, Altium's block yellow,
                makes it a filled BODY (an amplifier triangle); -1 draws
                an outline only.
            owner_part_id: the part of a multi-part symbol to draw on;
                omit for the current part.
            color: border colour (Altium colour integer).
            line_width: border width, 0 (smallest) to 3 (large).
            is_solid: fill (True) or outline only (False), over what
                ``fill_color`` implies.

        Returns:
            Dictionary confirming polygon addition with vertex count, and
            the style it now holds read back: ``color``, ``area_color``,
            ``line_width``, ``is_solid``
        """
        if line_width is not None and line_width not in (0, 1, 2, 3):
            return {"success": False, "error": "line_width is 0 to 3"}
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
        params: dict[str, Any] = {"vertices": vertices}
        if component_name:
            params["component_name"] = component_name
        if fill_color is not None:
            params["fill_color"] = int(fill_color)
        if owner_part_id is not None:
            params["owner_part_id"] = int(owner_part_id)
        if color is not None:
            params["color"] = int(color)
        if line_width is not None:
            params["line_width"] = int(line_width)
        if is_solid is not None:
            params["is_solid"] = "true" if is_solid else "false"
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.add_symbol_polygon",
            params,
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
        library_path: str = "",
    ) -> dict[str, Any]:
        """Get all pins of a library component.

        Name the component: without ``component_name`` this reads
        whatever the SchLib editor has selected. Pin names and types in
        a symbol can be wrong; check them against the datasheet before
        relying on them.

        Args:
            component_name: library reference of the symbol to read.
                Empty means the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT (the editor's own selection is never read).
                The held symbol does not survive a StartMCPServer re-run
                or an Altium restart.
            output_path: write the full pin array here as JSON and
                return a summary instead.
            inline_limit: above this many pins the array goes to a file
                (``pins_path``); 0 always returns inline.
            library_path: the .SchLib to read; it is brought to the front
                (opened if need be) first, so a sheet in front does not
                stop the read. Empty reads the library in front.

        Returns:
            "count", "component", and "pins", each with designator,
            name, electrical_type, x, y, orientation, hidden,
            ``owner_part_id`` and ``display_mode`` (0 normal, 1+ an
            alternate mode, not a duplicate). Spilled: "pins_path" and
            "summary" instead of "pins". Plus `_datasheet_guidance`.
        """
        bridge = get_bridge()
        if library_path:
            problem = await _open_document(bridge, library_path)
            if problem:
                return {"success": False, "error": problem}
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
                Empty means the symbol held from ``lib_create_symbol`` /
                ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT (the editor's own selection is never read).
                The held symbol does not survive a StartMCPServer re-run
                or an Altium restart.

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

        A KiCad 6+ symbol: a body rectangle round the pins and every pin
        with number, name, type and position, in mm. Naming a component
        leaves it current in the library (``current_component``), so a
        later call without a name, such as ``lib_add_pins``, acts on it.

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

        result = {
            "success": True,
            "output_path": str(out),
            "footprint": name,
            "pad_count": len(norm),
        }
        custom = [p["name"] for p in norm
                  if str(p["shape"]).strip().lower() == "custom"]
        if custom:
            result["custom_pads"] = custom
            result["warning"] = (
                "Custom-shaped pads were written as rectangles of their "
                "bounding box, not their outline: " + ", ".join(custom))
        return result

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

        Every named library is taken from its OPEN DOCUMENT: a closed one
        (source or destination) is opened as a document first, and one
        that cannot be (not an absolute path to an existing, readable
        .PcbLib) is refused - ``DEST_NOT_OPEN`` for the library that will
        be edited, ``SOURCE_NOT_OPEN`` for a source that is only read -
        with nothing changed. Nothing is loaded in the background: live
        2026-10-12 a copy into a closed library said success while the
        library was loaded only in the background, the copy never reached
        the file and lib_save_library answered NOT_LOADED. With overwrite,
        the copy counts only if the destination lists the NEW footprint
        (``COPY_FAILED`` otherwise).

        Returns:
            {"success": true, "source": "...", "new_name": "...",
             "same_library": bool, "source_opened", "dest_opened",
             "marked_modified", "mark", "pending_save": true,
             "written_to_disk": false}. Save with lib_save_library on
            dest_library.
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

        Bulk lib_copy_component: copies each match whole, then removes it
        from the source unless ``delete_from_source`` is False. One already
        in the destination is skipped unless ``overwrite``. A failed copy
        is never deleted from the source.

        Args:
            source_schlib: source .SchLib path.
            dest_schlib: destination .SchLib path.
            names: explicit list of component LibReferences to move.
            name_regex: regex matched against source component names when names
                is not given.
            delete_from_source: remove moved components from source (default True).
            overwrite: replace a same-named component already in dest.
            dry_run: report the selection without moving anything. Use it
                with name_regex, which is a ``search``, not a full match
                (``R`` selects every name containing an R).

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N}, or
            in dry-run {"dry_run", "resolved", "count", ...}.
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
    async def lib_rotate_footprint(
        library_path: str,
        footprint: str,
        angle: int,
        dry_run: bool = False,
        save: bool = True,
        show: bool = False,
    ) -> dict[str, Any]:
        """Turn a footprint in a .PcbLib about its own origin by 90, 180 or
        270 degrees, counter-clockwise (-90 is 270), every primitive with it:
        pads (position and pad rotation), tracks, arcs, fills, regions, vias,
        texts (.Designator / .Comment included) and 3D bodies, whose model
        turns with the body as it does under Altium's own Rotate Selection
        (the model's own rotation fields are never written). The library is
        opened by path; it need not be focused, and the user's selection is
        not touched.

        Every pad is read back: its centre must land where the turn puts it
        and its rotation must move by the angle. Any that did not are
        listed in ``pad_mismatches`` and counted in ``mismatches``; the
        library is then NOT saved, so it can be inspected (or the footprint
        turned back by the opposite angle). Polygons and dimensions in a
        footprint are counted in ``not_turned``. Look at one turned
        footprint in 3D before a batch (``show``), since the model's
        orientation cannot be read back.

        Args:
            library_path: the .PcbLib.
            footprint: footprint name (case-insensitive).
            angle: 90, 180, 270 or -90.
            dry_run: count what would be turned, change nothing.
            save: save the library afterwards when nothing mismatched.
            show: make the footprint the library's current one afterwards,
                to look at it (its 3D model shows only there). Changes what
                the library editor has on screen.

        Returns:
            ``kinds`` (count per primitive kind), ``turned``,
            ``not_turned``, ``mismatches``, ``pad_mismatches``,
            ``bodies_with_models`` and ``saved``.
        """
        if angle not in (90, 180, 270, -90):
            return {"success": False,
                    "error": "angle must be 90, 180, 270 or -90 (counter-clockwise)"}
        bridge = get_bridge()
        if not dry_run:
            # A library Altium has not opened as a document is loaded off
            # screen by the turn, where nothing can save it: the edit would
            # be lost. Open it first, the way the template tools open a sheet.
            opened = await _open_document(bridge, library_path)
            if opened:
                return {"success": False, "saved": False, "error": opened}
        result = await bridge.send_command_async(
            "library.rotate_footprint",
            {"library_path": library_path, "footprint": footprint,
             "angle": str(angle), "dry_run": "true" if dry_run else "false",
             "show": "true" if show else "false"},
            timeout=60.0,
        )
        if not isinstance(result, dict) or dry_run:
            return result
        out = dict(result)
        out["saved"] = False
        if not save:
            out["next_step"] = "Not saved (save=False). app_save_document on the library writes it."
        elif out.get("mismatches"):
            out["next_step"] = ("Not saved: something did not turn as expected. Inspect it, "
                                "or turn the footprint back with the opposite angle.")
        else:
            out["saved"], reason = await _save_library(bridge, library_path)
            if not out["saved"]:
                out["save_problem"] = reason
        return out

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

        Copies each footprint whole, then removes it from the source
        unless delete_from_source is False; with lib_move_components it
        splits a library. Give names or name_regex. A footprint already in
        dest is skipped unless overwrite=True.

        Args:
            source_pcblib: source .PcbLib path.
            dest_pcblib: destination .PcbLib path.
            names: explicit list of footprint names to move.
            name_regex: regex matched against source footprint names when names
                is not given.
            delete_from_source: remove moved footprints from source (default True).
            overwrite: replace a same-named footprint already in dest.
            dry_run: report the selection and move nothing. Use it with
                name_regex: the pattern matches anywhere in a name, so a
                short one selects far more, and the default deletes them
                from the source.

        Both libraries are taken from their OPEN DOCUMENTS: a closed one
        is opened as a document first (and stays open); one that cannot
        be is refused (``SOURCE_NOT_OPEN`` / ``DEST_NOT_OPEN``) before
        anything is edited. Nothing is loaded in the background, so a
        copy can never land in a library the save does not write. A
        footprint is deleted from the source only after the destination
        lists its copy - the NEW footprint when overwrite replaced one
        (``copy_not_listed`` counts the rest, which stay in the source).

        Returns:
            {"success": true, "moved": N, "skipped": N, "failed": N,
            "copy_not_listed", "deleted_from_source", "marked_modified",
            "mark", "source_marked_modified", "source_mark",
            "source_opened", "dest_opened", "pending_save",
            "written_to_disk": false}, or in dry-run
            {"dry_run", "resolved", "count", ...} with nothing sent to
            the bridge. Save both libraries with lib_save_library.
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
    async def lib_split_pin_functions(
        component_name: str = "",
    ) -> dict[str, Any]:
        """Split slash-delimited pin names into pin function lists.

        For the current library symbol, parses each pin whose name is
        `PRIMARY/ALT1/ALT2/...` into the pin's function list (the alternate-
        function popup), leaving `PRIMARY` as the visible name. Pins without a
        `/` are unchanged.

        Args:
            component_name: The symbol, by library reference, in the active
                SchLib. Omit for the symbol held from ``lib_create_symbol``
                / ``lib_set_current_component``; with none held the reply
                is NO_COMPONENT (the editor's own selection is never used).
                The held symbol does not survive a StartMCPServer re-run
                or an Altium restart.

        Returns:
            {"pins_processed": N}.
        """
        params: dict[str, Any] = {}
        if component_name:
            params["component_name"] = component_name
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.split_pin_functions", params)

    @mcp.tool()
    async def lib_get_installed_libraries(
        with_counts: bool = True,
    ) -> dict[str, Any]:
        """List the libraries installed in the Altium environment.

        Reads the environment's own list (IntLib, SchLib, PcbLib,
        database and query libraries), open or not; `lib_search` sees
        only open SchLibs. Installed means switched on; the available
        total comes beside it, so a library present but switched off
        shows as a gap between the two numbers.

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
        result = await bridge.send_command_async(
            "library.get_installed_libraries", params
        )
        # Altium's component count is not a database library's row count:
        # MEASURED on AD26, a DbLib of five rows read 0 while its connection
        # was unreadable and 1 once it worked. Reported as unknown, with
        # where the real answer is, rather than as a number.
        if isinstance(result, dict) and with_counts:
            for lib in result.get("libraries") or []:
                if isinstance(lib, dict) and lib.get("library_type") in ("database", "query"):
                    lib["component_count"] = None
                    lib["count_note"] = ("Altium's count does not reflect a database "
                                         "library's rows; lib_dblib_info and "
                                         "lib_dblib_search read the database.")
        return result

    @mcp.tool()
    async def lib_install_library(library_path: str) -> dict[str, Any]:
        """Register a library with the environment's Available Libraries.

        Installs an .IntLib / .SchLib / .PcbLib so its parts resolve by
        lib-ref across the workspace (the step after authoring a library).

        Args:
            library_path: absolute path to the library file.

        Returns:
            {"installed": bool, "library_path", "added": [paths],
            "reason"}. ``installed`` is true only when the environment's
            installed list GAINED an entry (read before and after the
            call, compared by path); otherwise false with ``reason`` (no
            file at that path, already installed, the list did not
            change, or it could not be read). Live 2026-10-12 a relative
            name with no file behind it used to answer true.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.install_library", {"library_path": library_path}
        )

    @mcp.tool()
    async def lib_get_vault_name() -> dict[str, Any]:
        """The Workspace name the managed tools would use, read off the design.

        Altium writes the Workspace name onto every managed part it
        places. This reads it from the active sheet, then from the other
        sheets that are already open; it never opens one. A Workspace has
        been seen under two names on one design, so a part addressed by
        kind 4 (VaultName) is preferred and ``other_kind`` says when only
        another kind was found.

        Returns:
            ``found``, ``vault``, ``other_kind`` and ``seen``, every
            ``kind:name`` met, separated by ``|``.
        """
        bridge = get_bridge()
        return await bridge.send_command_async("library.get_vault_name", {})

    @mcp.tool()
    async def lib_find_vault_components(name: str) -> dict[str, Any]:
        """Find the Design Item ID of a managed part by its exact name.

        Looks through the managed (Workspace) parts already placed on the
        sheets Altium has open, and matches ``name`` exactly, case
        ignored, against each part's comment, library reference and
        Design Item ID. Every match comes back, one per Design Item ID,
        with the lifecycle state the library manager reports, so two
        parts sharing a name are both shown rather than the first one
        being picked.

        It does not search the whole Workspace: that needs Altium's EDMS
        interfaces, whose methods no reference documents, and a call this
        Altium lacks would stop the bridge. A part not yet used in the
        design has to be looked up in Altium's Components panel.

        Args:
            name: The comment, library reference or Design Item ID.

        Returns:
            ``name``, ``count``, ``sheets_searched`` and ``matches``, each
            with design_item_id, comment, lib_reference, vault,
            lib_identifier_kind, revision_guid, lifecycle_state and sheet.
        """
        if not str(name or "").strip():
            return {"success": False, "error": "name is required"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.find_vault_components", {"name": str(name).strip()})

    @mcp.tool()
    async def lib_get_vault_component(
        design_item_id: str,
        vault: str = "",
        lib_identifier_kind: Optional[int] = None,
    ) -> dict[str, Any]:
        """Check one managed (Workspace) component can be placed, without placing it.

        Loads the item the way ``sch_place_vault_components`` would, reads
        its identity, and throws it away again; nothing reaches a sheet.
        The kind asked for is tried first, then the others. The library
        manager's lookups (library path, lifecycle state, revisions,
        placement parameters) are reported alongside, as they come back.
        A part number is not looked up here, so bring the Design Item ID.

        Args:
            design_item_id: The component's Design Item ID.
            vault: The Workspace name. Usually left out; see
                ``lib_get_vault_name``.
            lib_identifier_kind: Altium's TLibIdentifierKind, tried first:
                0 Any, 1 NameNoType, 2 NameWithType, 3 FullPath, 4 VaultName
                (the default). A managed part Altium placed shows the kind
                and identifier that work, through ``obj_query`` on
                ``eSchComponent``.

        Returns:
            ``placeable``, ``loader_callable``, lib_identifier_kind (the
            kind that loaded, -1 if none), ``load_probe`` (``kind:result``
            for each try), the loaded ``loaded_design_item_id``,
            ``vault_guid``, ``item_guid`` and ``revision_guid``, then
            ``library_path``, ``display_path``, ``lifecycle_state``,
            ``has_revisions``, ``revisions``, ``placement_parameters``,
            ``note`` and ``diag`` (library manager calls that raised).
        """
        bridge = get_bridge()
        params: dict[str, Any] = {"design_item_id": design_item_id}
        if vault:
            params["vault"] = vault
        if lib_identifier_kind is not None:
            params["lib_identifier_kind"] = int(lib_identifier_kind)
        return await bridge.send_command_async(
            "library.get_vault_component", params)

    @mcp.tool()
    async def lib_uninstall_library(library_path: str) -> dict[str, Any]:
        """Unregister a library from the environment's Available Libraries.

        Args:
            library_path: absolute path to the library file, spelled as
                the installed list holds it.

        Returns:
            {"uninstalled": bool, "library_path", "removed": [paths],
            "reason"}. ``uninstalled`` is true only when the installed
            list LOST an entry (read before and after, compared by
            path); otherwise false with ``reason``, which names the
            installed spelling when the same file name is installed
            under another path. Live 2026-10-12 a respelled path used to
            answer true while the library stayed installed.
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

        Also repairs a name a caller cannot type (a quote or control
        character from a broken import): address it by
        ``component_index``. A name another component has is NAME_EXISTS.
        The component is removed and re-added under the new name so the
        library's by-name index is rebuilt. Marks the library dirty for a
        deferred save: save it with lib_save_library.

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

        A SINGLE-FOOTPRINT LIBRARY CAN BE PARKED TOO (upstream measured
        the park on AD 26.10): with ``park_current`` and no other footprint
        to make current, a temporary one is created for the delete and
        removed afterwards, with the target shown again first;
        ``temporary_holder`` / ``restored_current`` say this happened.

        Args:
            footprint_name: The footprint to edit. Required: deleting
                from whichever footprint the editor happens to show is
                the mistake this avoids.
            object_type: PCB object type, e.g. "track", "arc", "text",
                "region", "fill". Give this or ``layer``.
            layer: Restrict to one layer, e.g. "TopOverlay", or a name
                the library's stack gives it, such as "Top Assembly". A
                name the stack does not have is refused, with the names
                it does have.
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
            ``target_current_during_removal``, ``leftover`` (matching
            primitives still there, counted again) and ``hint``,
            ``marked_modified`` (the
            library reads modified afterwards), ``examined``,
            ``layers``, ``pads_included``, ``was_current`` (the target was
            on screen when the call began), ``temporary_holder`` (a
            single-footprint library got a temporary footprint to park on),
            ``restored_current`` (the target was made current again so that
            temporary footprint could be removed),
            ``board_removal``
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
    async def lib_move_bodies_to_layer(
        layer: str,
        footprint_names: Optional[list[str]] = None,
        keep_layers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Put the 3D bodies of the focused PCB library on one mechanical
        layer.

        For libraries merged from sources that used different layers.
        Bodies already on the layer are counted and left; nothing is added
        or removed.

        Args:
            layer: The mechanical layer, by number ("Mechanical13") or by
                the name the library's stack gives it. Anything else is
                refused, as Altium's body dialog only offers mechanical
                layers.
            footprint_names: Only these footprints. Empty means every
                footprint in the library; names no footprint has come
                back in ``not_found``.
            keep_layers: Layers whose bodies stay where they are, by the
                same names as ``layer``. A body already on one of them is
                counted as already on the layer. Every other body moves.
                An unknown name refuses the whole call, and nothing moves.
                Empty keeps the old behaviour: every body moves.

        Does NOT save the library. A mechanical layer the library has not
        enabled is accepted, with ``layer_enabled`` false and a
        ``warning``, since Altium does not draw what is on it.

        Returns:
            Dict with ``layer``, ``layer_enabled``, ``footprints``,
            ``bodies``, ``moved``,
            ``already_on_layer``, ``footprints_without_bodies``,
            ``not_found``, and ``items`` (``{name, moved, from}`` for
            each footprint that changed).
        """
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        params: dict[str, Any] = {"layer": layer.strip()}
        names = [n.strip() for n in (footprint_names or []) if n.strip()]
        if names:
            params["footprint_names"] = "\t".join(names)
        keep = [k.strip() for k in (keep_layers or []) if k.strip()]
        if keep:
            params["keep_layers"] = ",".join(keep)
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.move_bodies_to_layer", params, timeout=60.0)

    @mcp.tool()
    async def lib_redraw_assembly(
        layer: str,
        footprint_names: Optional[list[str]] = None,
        outline_from: str = "auto",
        courtyard_layer: str = "",
        width: Optional[float] = None,
        designator: bool = True,
        text_height: float = 0,
        replace: bool = True,
        confirm: bool = False,
        units: str = "mil",
    ) -> dict[str, Any]:
        """Draw the assembly layer of the focused PCB library again from
        each footprint's own geometry: a rectangle round the part and a
        ``.Designator`` in its middle.

        With ``replace`` (the default) the tracks, arcs, texts, fills and
        regions on ``layer`` are cleared first; bodies and pads are never
        touched. Does NOT save. A layer the library has not enabled is
        accepted with ``layer_enabled`` false and a ``warning``.

        Args:
            layer: The mechanical layer for the drawing.
            footprint_names: Only these footprints. Empty means all.
            outline_from: ``body``, ``pads``, ``courtyard`` (drawn on
                ``courtyard_layer``) or ``auto`` (that order of
                preference). A footprint with none is in ``skipped``.
            courtyard_layer: The courtyard's layer; not ``layer`` when
                replacing.
            width: Line width (default 4 mil).
            designator: Place a centred ``.Designator``, upright in a
                tall box.
            text_height: 0 sizes it to the box (0.2 to 1 mm).
            replace: Clear the layer first; False draws beside it.
            confirm: Required with ``replace``.
            units: "mil" (default) or "mm" for ``width`` and
                ``text_height``.

        Returns:
            ``layer``, ``layer_enabled``, ``redrawn``, ``cleared``,
            ``skipped``, ``not_found``, ``items`` (``{name,
            outline_from, cleared, width_mm, height_mm}``).
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        if not layer.strip():
            return {"ok": False, "reason": "layer is required"}
        source = (outline_from or "auto").strip().lower()
        if source not in ("auto", "body", "pads", "courtyard"):
            return {"ok": False, "reason": (
                f"outline_from must be auto, body, pads or courtyard; "
                f"got {outline_from!r}")}
        if source == "courtyard" and not courtyard_layer.strip():
            return {"ok": False,
                    "reason": "outline_from courtyard needs courtyard_layer"}
        if text_height < 0 or (width is not None and width <= 0):
            return {"ok": False,
                    "reason": "width must be above 0 and text_height 0 or more"}
        params: dict[str, Any] = {
            "layer": layer.strip(),
            "outline_from": source,
            "width": format_length(length_in(units, 4) if width is None else width),
            "designator": "true" if designator else "false",
            "replace": "true" if replace else "false",
        }
        names = [n.strip() for n in (footprint_names or []) if n.strip()]
        if names:
            params["footprint_names"] = "\t".join(names)
        if courtyard_layer.strip():
            params["courtyard_layer"] = courtyard_layer.strip()
        if text_height:
            params["text_height"] = format_length(text_height)
        if confirm:
            params["confirm"] = "true"
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.redraw_assembly", _with_units(params, units), timeout=120.0)

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

        With an empty Location a footprint resolves by name in the editor
        but is missing from a compiled library, with no compile error.
        Targets footprint models named model_name, or all of them. Copy
        the exact source_library from a model that embeds correctly
        (``lib_get_component_details`` shows each model's location); it
        is written as given.

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
            bridge_edited, still_modified, file_age_before,
            file_age_after, library_path}``. ``was_modified`` is Altium's
            flag, which has read false after real edits (2026-10-10);
            ``bridge_edited`` says whether a bridge edit marked this file
            since it was last written, plus ``reason`` whenever ``ok`` is false
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
        """Pad geometry of one PcbLib footprint as floats in mm, relative
        to the footprint origin (no integer-mil rounding, for datasheet
        tolerance checks).

        Args:
            footprint_name: the footprint name in the library.
            library_path: optional .PcbLib path; defaults to the
                focused library.

        Returns:
            {name, description, library_path, pad_count, pads: [{
             name, x_mm, y_mm, copper_x_mm, copper_y_mm, w_mm, h_mm,
             shape, corner_pct, rotation, hole_rotation, hole_mm,
             hole_w_mm, hole_type, slot_aligned (slots only), plated,
             layer, selected, mode, paste_expansion_mm,
             paste_expansion_source, paste_expansion_state,
             mask_expansion_mm, mask_bottom_expansion_mm, mask_separate,
             mask_expansion_source, mask_expansion_state}]}.
            selected is whether the pad is selected in the editor (what
            lib_set_pad_properties selected=True acts on); mode is the
            stack mode, 0 simple, 1 top-middle-bottom, 2 full stack.
            x/y is the pad origin, copper_x/y the copper centre (differs
            on a custom pad). hole_rotation is relative to the pad.
            mask_expansion_mm is the top side when mask_separate, else
            both; mask_bottom_expansion_mm is the stored bottom value.
            *_source is 'manual' (pad override) or 'rule' (value 0; the
            rule holds it); *_state is the cache's manual/valid/invalid.
        """
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.get_pad_geometry",
            {"footprint_name": footprint_name, "library_path": library_path},
        )

    @mcp.tool()
    async def lib_set_pad_properties(
        footprint_name: str,
        pad_name: str = "",
        selected: bool = False,
        library_path: str = "",
        units: str = "mil",
        x_size: Optional[float] = None,
        y_size: Optional[float] = None,
        bottom_x_size: Optional[float] = None,
        bottom_y_size: Optional[float] = None,
        shape: Optional[str] = None,
        corner_radius_pct: Optional[float] = None,
        stack_mode: Optional[str] = None,
        rotation: Optional[float] = None,
        hole_type: Optional[str] = None,
        hole_size: Optional[float] = None,
        hole_width: Optional[float] = None,
        hole_rotation: Optional[float] = None,
        plated: Optional[bool] = None,
        paste_expansion: Optional[float] = None,
        paste_manual: Optional[bool] = None,
        mask_expansion: Optional[float] = None,
        mask_bottom_expansion: Optional[float] = None,
        mask_separate: Optional[bool] = None,
        mask_manual: Optional[bool] = None,
        mask_from_hole_edge: Optional[bool] = None,
        paste_top_enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        """Change existing pads in a PcbLib footprint: every pad named
        ``pad_name``, or with ``selected=True`` every pad selected in the
        editor, only the fields given, each read back. A written
        expansion is made manual, without which Altium ignores it;
        ``mask_manual`` / ``paste_manual`` False hands one back to the
        design rule. On a per-layer pad stack ``x_size`` / ``y_size`` are
        the top size (``note``); ``stack_mode`` and the bottom size
        change the rest.

        Args:
            footprint_name: The footprint holding the pad.
            pad_name: The pad's designator. Names need not be unique (an
                SMD pad "1" beside a thru-hole "1"): use ``selected``
                to say which.
            selected: Change the pads selected in the editor instead of
                those named ``pad_name``.
            library_path: Optional .PcbLib path; defaults to the focused
                library.
            units: "mil" (default) or "mm", for every length here.
            x_size, y_size: Copper size (the top size on a stacked pad).
            bottom_x_size, bottom_y_size: Bottom copper size; makes a
                simple pad top-middle-bottom.
            shape: "round", "rectangular", "octagonal" or "roundrect".
            corner_radius_pct: Rounded-rectangle corner, 0 to 100, as a
                percentage of half the shorter side (how Altium stores
                it); 25 when ``shape`` is roundrect and this is not given.
            stack_mode: "simple" (the top size serves every layer) or
                "top-middle-bottom", written before the sizes.
            rotation: Pad rotation in degrees.
            hole_type: "round", "square" or "slot", written before the
                hole sizes, so a slot can be made round again.
            hole_size: Hole diameter, 0 for a surface-mount pad.
            hole_width: Slot length; makes the hole a slot (so it is
                refused beside ``hole_type`` round or square).
            hole_rotation: Slot rotation relative to the pad, degrees.
            plated: Whether the hole is plated.
            paste_expansion: Paste expansion.
            paste_manual: True keeps the paste value as written; False
                hands the paste back to the design rule.
            mask_expansion: Mask expansion, both sides unless
                ``mask_bottom_expansion`` is also given.
            mask_bottom_expansion: Bottom mask expansion; unlinks sides.
            mask_separate: Link (False) or unlink (True) the mask sides
                (the bottom value takes effect once unlinked).
            mask_manual: As ``paste_manual``, for the solder mask.
            mask_from_hole_edge: Measure the mask from the drill edge,
                not the copper (mechanical holes and slots).
            paste_top_enabled: Switch the top paste opening on or off
                (not its size). Altium 26.11 keeps this only while the
                paste expansion is manual, so a rule-driven one is made
                manual at its current value first, and ``note`` says so.
                Before Altium Designer 25 the flag does not exist: the
                write is refused per pad and reads give null.

        Returns:
            ``pads_changed``, ``pads_with_a_failed_write``, ``note``,
            ``pads`` as read back (top and bottom size, shape,
            ``corner_radius_pct``, ``stack_mode``, rotation, hole, slot,
            plating, expansions and their state, ``mask_separate``,
            ``mask_from_hole_edge``, ``paste_top_enabled``,
            ``all_writes_ran``).
        """
        refusal = _units_refusal(units)
        if refusal:
            return refusal
        if not pad_name and not selected:
            return {"success": False,
                    "error": "give pad_name, or selected=True for the "
                             "pads selected in the editor"}
        if hole_type is not None and hole_type not in ("round", "square", "slot"):
            return {"success": False,
                    "error": "hole_type must be round, square or slot"}
        if hole_type in ("round", "square") and hole_width is not None:
            return {"success": False,
                    "error": "hole_width makes a slot; leave it out for a "
                             "round or square hole"}
        if stack_mode is not None and stack_mode not in ("simple",
                                                         "top-middle-bottom"):
            return {"success": False,
                    "error": "stack_mode must be simple or top-middle-bottom"}
        if corner_radius_pct is not None and not 0 <= corner_radius_pct <= 100:
            return {"success": False,
                    "error": "corner_radius_pct is 0 to 100"}
        if mask_manual is False and (mask_expansion is not None
                                     or mask_bottom_expansion is not None):
            return {"success": False,
                    "error": "mask_manual=False hands the mask to the rule; "
                             "a mask value with it would be ignored"}
        if paste_manual is False and paste_expansion is not None:
            return {"success": False,
                    "error": "paste_manual=False hands the paste to the rule; "
                             "a paste value with it would be ignored"}
        params: dict[str, Any] = {"footprint_name": footprint_name,
                                  "pad_name": pad_name,
                                  "library_path": library_path}
        if selected:
            params["selected"] = "true"
        base = len(params)
        if hole_type is not None:
            params["hole_type"] = hole_type
        for key, value in (("x_size", x_size), ("y_size", y_size),
                           ("bottom_x_size", bottom_x_size),
                           ("bottom_y_size", bottom_y_size),
                           ("hole_size", hole_size), ("hole_width", hole_width),
                           ("paste_expansion", paste_expansion),
                           ("mask_expansion", mask_expansion),
                           ("mask_bottom_expansion", mask_bottom_expansion)):
            if value is not None:
                params[key] = format_length(value)
        for key, value in (("rotation", rotation),
                           ("hole_rotation", hole_rotation)):
            if value is not None:
                params[key] = float(value)
        if shape is not None:
            params["shape"] = shape
        if corner_radius_pct is not None:
            params["corner_radius_pct"] = float(corner_radius_pct)
        if stack_mode is not None:
            params["stack_mode"] = stack_mode
        if plated is not None:
            params["plated"] = bool(plated)
        for key, value in (("mask_separate", mask_separate),
                           ("mask_manual", mask_manual),
                           ("paste_manual", paste_manual),
                           ("mask_from_hole_edge", mask_from_hole_edge)):
            if value is not None:
                params[key] = "true" if value else "false"
        if paste_top_enabled is not None:
            # The top paste opening itself, not its expansion (issue #80).
            params["paste_top_enabled"] = "true" if paste_top_enabled else "false"
        if len(params) == base:
            return {"success": False,
                    "error": "give at least one property to change"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.set_pad_properties", _with_units(params, units))

    @mcp.tool()
    async def lib_get_footprint_primitives(
        footprint_name: str,
        library_path: str = "",
        kinds: str = "",
    ) -> dict[str, Any]:
        """Every non-pad primitive of one PcbLib footprint, with where it is.

        Tracks, arcs, regions, fills, texts and vias, each with its layer
        (the name the library's stack gives it, plus ``layer_id``), in mm
        from the footprint's origin: the frame the ``lib_add_footprint_*``
        writers take. Pads are in ``lib_get_pad_geometry``. Read-only.
        Each primitive is listed once. Altium's own iterator yields one
        just added to the footprint on screen twice until that footprint
        is shown again; the footprint writers here show it again after
        each write, and this read skips a repeat either way.

        Args:
            footprint_name: The footprint name in the library.
            library_path: Optional .PcbLib path; defaults to the focused
                library.
            kinds: Optional ``|`` list of what to read, from track, arc,
                region, fill, text, via and body (``"via|body"``); empty
                reads all. Each kind is its own filtered walk.

        Every primitive carries ``selected``. Each via also carries its
        mask (top, bottom, separate, ``mask_expansion_source`` manual or
        rule, ``mask_from_hole_edge``), tenting, the four testpoint flags
        (read only), ``stack_mode`` and its top / mid / bottom diameters;
        each body its outline box (x1, y1, x2, y2).

        Returns:
            {name, library_path, height_mm, editor_origin_x_mm /
            editor_origin_y_mm (the editor's origin marker in this
            footprint's frame), kinds, bodies (a count),
            body_layers (the layer of each body), body_models [{layer,
            identifier, has_model, model_file (the file the model was
            embedded from), model_type (Altium's T3DModelType; 1 for a
            STEP model), step_solids (solids in that file when it is
            still there, else null: a STEP model is ONE body whatever its
            detail, so read this, not ``bodies``), rotation,
            overall_height_mm, standoff_mm}], tracks
            [{layer, layer_id, x1, y1, x2, y2, width}], arcs [{layer,
            layer_id, xc, yc, r, start, end, width}], regions and fills
            [{layer, layer_id, x1, y1, x2, y2, area and arc_edges
            (regions)}], texts
            [{text, layer, layer_id, x, y, size, rotation, x1, y1, x2,
            y2}], vias [{x, y, diameter, hole}]}. A text's box is
            Altium's cached one and can lag an edit made this session.
        """
        params: dict[str, Any] = {"footprint_name": footprint_name,
                                  "library_path": library_path}
        if kinds:
            params["kinds"] = kinds
        bridge = get_bridge()
        result = await bridge.send_command_async(
            "library.get_footprint_primitives", params)
        # ONE BODY IS NOT ONE BLOCK. A STEP model is a single body however
        # detailed it is; a take read "bodies": 1 on every footprint as
        # "single plain blocks" and rebuilt eleven models that already had
        # their terminations and leads. The file says how many solids.
        for model in (result.get("body_models") or []
                      if isinstance(result, dict) else []):
            if isinstance(model, dict) and model.get("model_file"):
                model["step_solids"] = await asyncio.to_thread(
                    _step_solid_count, str(model["model_file"]))
        return result

    @mcp.tool()
    async def lib_select_footprint_primitives(
        footprint_name: str,
        objects: list[dict[str, Any]],
        clear: bool = True,
    ) -> dict[str, Any]:
        """Select primitives of one PcbLib footprint in the editor, for an
        editor command that acts on the selection (obj_run_process with
        PCB:CustomPadShape to merge a pad and a region into a custom pad,
        PCB:Clear, ...). obj_select acts on a board, not a footprint.

        Several sets select together in one call; with ``clear=False`` a
        later call adds to the selection. Pad names are not unique (an
        SMD "12" and a thru-hole "12" can share a footprint), so narrow by
        layer or box and check the reply before running the command.

        Args:
            footprint_name: The footprint, made current if it is not.
            objects: One or more filter sets; an object matching ANY set is
                selected. Each set has any of ``object_type`` ("pad",
                "region", "track", ...), ``layer`` ("TopLayer",
                "MultiLayer", ...), ``name`` (a pad's designator or a
                region's Name) and ``box`` ([x1, y1, x2, y2] in mm from the
                footprint origin, the box lib_get_footprint_primitives
                gives a region, fill or text; matched within 0.001 mm).
            clear: Deselect everything else first (default True).

        Returns:
            {selected (how many matched), layers (theirs), selected_total
            (everything the editor now holds selected)}
        """
        sets: list[str] = []
        for i, spec in enumerate(objects or []):
            if not isinstance(spec, dict):
                return {"ok": False, "reason": f"set {i} is not an object"}
            fields: list[str] = []
            for key in ("object_type", "layer", "name"):
                value = str(spec.get(key) or "").strip()
                if value:
                    fields.append(f"{key}={payload_safe(value)}")
            box = spec.get("box")
            if box is not None:
                try:
                    x1, y1, x2, y2 = (float(v) for v in box)
                except (TypeError, ValueError):
                    return {"ok": False,
                            "reason": f"set {i}: box is [x1, y1, x2, y2] in mm"}
                fields.append("box=" + ",".join(
                    format_length(v) for v in (x1, y1, x2, y2)))
            if not fields:
                return {"ok": False, "reason": f"set {i} has no filter; it "
                        "would select the whole footprint"}
            sets.append(";".join(fields))
        if not sets:
            return {"ok": False, "reason": "objects needs at least one set"}
        bridge = get_bridge()
        return await bridge.send_command_async(
            "library.select_footprint_primitives",
            {"footprint_name": footprint_name, "objects": "~~".join(sets),
             "clear": "true" if clear else "false"})

    @mcp.tool()
    async def lib_extract_embedded_models(
        library_path: str,
        footprint_name: str = "",
        model_names: Optional[list] = None,
        out_dir: str = "",
        overwrite: bool = False,
    ) -> dict[str, Any]:
        """Get back the ORIGINAL 3D model files (STEP) embedded in a .PcbLib.

        Byte for byte the file Altium took in: each is written only when
        its checksum equals the one Altium stored when it embedded it.
        No Altium API saves an embedded model, so this reads the saved
        library file directly, read only (it never writes the library).
        A model added in Altium and not saved yet is not in the file.

        With ``footprint_name``, Altium is asked which model files that
        footprint's bodies use and only those are extracted; with
        ``model_names``, those; otherwise every embedded model.

        Args:
            library_path: The .PcbLib on disk.
            footprint_name: Extract only this footprint's models (asks
                Altium, so the library must be reachable over the bridge).
            model_names: Model file names, e.g. ["NE555DR.stp"].
            out_dir: Folder to write to (default: the workspace's
                models/<library name>).
            overwrite: Replace files already there.

        Returns:
            ``written`` [{name, path, bytes, sha256, checksum_verified,
            step_header, rotation_deg}], ``refused`` [{name, reason}],
            ``not_in_saved_file`` (asked for, not in the saved file).
        """
        path = Path(library_path)
        if not path.is_file():
            return {"ok": False, "reason": f"no such library file: {library_path}"}
        names = list(model_names or [])
        asked_altium = None
        if footprint_name:
            prims = await get_bridge().send_command_async(
                "library.get_footprint_primitives",
                {"footprint_name": footprint_name, "library_path": str(path)})
            if not isinstance(prims, dict) or "body_models" not in prims:
                return {"ok": False, "reason": "Altium could not list the "
                        "footprint's 3D bodies", "altium": prims}
            from_altium = [b.get("model_file", "") for b in prims["body_models"]
                           if b.get("has_model") and b.get("model_file")]
            asked_altium = from_altium
            if not from_altium:
                return {"ok": False, "reason": f"{footprint_name} has no 3D "
                        f"model, only simple bodies", "body_models": prims["body_models"]}
            # Altium's names are Windows paths whatever runs this.
            names += [re.split(r"[\\/]", n)[-1] for n in from_altium]
        target = Path(out_dir) if out_dir else (
            Path(get_bridge().config.workspace_dir) / "models" / path.stem)
        try:
            got = pcblib_models.extract_embedded_models(
                path, target, names=names or None, overwrite=overwrite)
        except ValueError as exc:
            return {"ok": False, "reason": str(exc)}
        if asked_altium is not None:
            got["footprint"] = footprint_name
            got["altium_model_files"] = asked_altium
        return got

    async def _live_footprint(footprint_name: str, library_path: str,
                              courtyard_layer: str, assembly_layer: str):
        """(model, notes) for one footprint, read from the library live:
        pads, every other primitive, and the library's layer kinds."""
        from ..bridge.exceptions import AltiumCommandError
        from ..design.landpattern.live import model_from_live

        bridge = get_bridge()
        args = {"footprint_name": footprint_name, "library_path": library_path}
        pads = await bridge.send_command_async("library.get_pad_geometry", args)
        prims = await bridge.send_command_async(
            "library.get_footprint_primitives", args)
        notes: list[str] = []
        mech = None
        want = str((prims or {}).get("library_path") or library_path)
        try:
            reply = await bridge.send_command_async(
                "library.get_mech_layers", {"library_path": want})
            got = str((reply or {}).get("library_path") or "")
            if got and want and got.lower() != want.lower():
                notes.append(f"layer kinds were read from {got}, not {want}; "
                             f"layers are matched by the names given only")
            else:
                mech = (reply or {}).get("layers") or []
        except AltiumCommandError as e:
            notes.append(f"layer kinds not read ({e.code or e}); courtyard "
                         f"and assembly come from the names given only")
        model = model_from_live(pads, prims, mech,
                                courtyard_layer=courtyard_layer,
                                assembly_layer=assembly_layer)
        if not model.pads and not model.graphics:
            notes.append(f"{footprint_name} is empty: no pads and no "
                         f"primitives")
        elif not any(g.layer == "courtyard" for g in model.graphics):
            notes.append("no courtyard found: no mechanical layer has the "
                         "Courtyard kind and courtyard_layer was not given")
        return model, notes

    @mcp.tool()
    async def lib_render_footprint(
        footprint_name: str,
        library_path: str = "",
        path: str = "",
        png: bool = True,
        courtyard_layer: str = "",
        assembly_layer: str = "",
    ) -> dict[str, Any]:
        """Draw a footprint as it is in its library, to look at.

        Read live (pads, every primitive, the library's layer kinds) and
        drawn the way ``footprint_render_package`` draws a planned one:
        copper with pad numbers and holes, paste, silkscreen, assembly
        dashed blue, courtyard magenta, anything else dashed grey. Which
        layer is the courtyard comes from Altium's layer KIND; a library
        whose layers have no kind set needs ``courtyard_layer`` /
        ``assembly_layer`` (e.g. "Mechanical15").

        Args:
            footprint_name: the footprint in the library.
            library_path: the .PcbLib; empty = the focused library.
            path: SVG to write; default <workspace>/renders/<name>_live.svg.
            png: also write a PNG beside it when a headless browser is
                installed.
            courtyard_layer / assembly_layer: layer names to use when the
                layer kinds do not say.

        Returns:
            {ok, svg_path, png_path, pads, roles (per role: count and
            extent in mm), height_mm, silk_over_copper (silkscreen lines
            that touch a pad, by pad), notes}.
        """
        from ..design.landpattern.live import role_summary, silk_over_copper
        from ..design.landpattern.svg import render_svg

        model, notes = await _live_footprint(
            footprint_name, library_path, courtyard_layer, assembly_layer)
        on_copper = silk_over_copper(model)
        if on_copper:
            notes.insert(0, f"silkscreen over copper on pad(s) "
                            f"{', '.join(sorted({h['pad'] for h in on_copper}))}")
        svg = render_svg(model, notes=[f"{len(model.pads)} pads, read live",
                                       *notes[:2]])
        if path:
            target = Path(path).with_suffix(".svg")
        else:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "_", model.name).strip("._") \
                or "footprint"
            target = get_config().workspace_dir / "renders" / f"{safe}_live.svg"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(svg, encoding="utf-8")
        except OSError as exc:
            return {"ok": False, "reason": f"could not write {target}: {exc}"}
        out: dict[str, Any] = {
            "ok": True, "svg_path": str(target), "png_path": None,
            "name": model.name, "pads": len(model.pads),
            "roles": role_summary(model), "height_mm": model.body.z1,
            "silk_over_copper": on_copper, "notes": notes,
            "legend": {"copper": "pads", "dark": "paste", "black": "silkscreen",
                       "blue dashed": "assembly", "magenta": "courtyard",
                       "grey dashed": "other layers"}}
        if png:
            from ..render import rasterize_svg, size_svg_for_raster

            sized, w_px, h_px = size_svg_for_raster(svg, target_width=1400)
            raster = target.with_suffix(".raster.svg")
            try:
                raster.write_text(sized, encoding="utf-8")
                rr = await asyncio.to_thread(
                    rasterize_svg, str(raster),
                    str(target.with_suffix(".png")), w_px, h_px)
            finally:
                try:
                    raster.unlink()
                except OSError:
                    pass
            if rr.get("ok"):
                out["png_path"] = rr["png_path"]
            else:
                out["rasterize_note"] = rr.get("reason")
        return out

    @mcp.tool()
    async def lib_compare_footprints(
        footprint_a: str,
        footprint_b: str,
        library_a: str = "",
        library_b: str = "",
        tolerance_mm: float = 0.001,
        courtyard_layer: str = "",
        assembly_layer: str = "",
    ) -> dict[str, Any]:
        """What differs between two footprints, pad by pad and layer by layer.

        Both read live, from one library or two (a vendor's footprint
        against yours, an old revision against the new). Pads are matched
        by number: position, size, shape, corner radius, rotation, hole,
        plating, paste and mask expansion are compared. Silkscreen,
        assembly, courtyard and the other layers are compared by count and
        extent. ``same`` is true only when nothing differs over the
        tolerance.

        Args:
            footprint_a / footprint_b: footprint names.
            library_a / library_b: their .PcbLib files; empty = focused
                library (library_b defaults to library_a).
            tolerance_mm: smallest difference reported.
            courtyard_layer / assembly_layer: layer names to use when the
                layer kinds do not say.
        """
        from ..design.landpattern.live import compare_live

        model_a, notes_a = await _live_footprint(
            footprint_a, library_a, courtyard_layer, assembly_layer)
        model_b, notes_b = await _live_footprint(
            footprint_b, library_b or library_a, courtyard_layer,
            assembly_layer)
        out = compare_live(model_a, model_b, tol=max(0.0, tolerance_mm))
        out.update({"a": {"footprint": footprint_a,
                          "library": model_a.source.get("library_path")},
                    "b": {"footprint": footprint_b,
                          "library": model_b.source.get("library_path")},
                    "notes": sorted(set(notes_a + notes_b))})
        return out

    @mcp.tool()
    async def lib_build_integrated_library(
        package_path: str,
        source_libraries: list[str],
        install: bool = False,
    ) -> dict[str, Any]:
        """Package libraries into a LibPkg and compile it to an IntLib.

        Through Altium, end to end: the .LibPkg is created when it does
        not exist, each source library (.SchLib, .PcbLib, ...) is added to
        it, and the package is compiled. The IntLib Altium writes to
        "Project Outputs for <package>" is then looked for, and
        ``built`` is true only when a NEW file is there, so a compile
        that produced nothing is not reported as done. ``install`` adds
        the IntLib to the installed libraries.

        Args:
            package_path: the .LibPkg to create or update (full path).
            source_libraries: full paths of the libraries to package.
            install: install the compiled IntLib.
        """
        import os
        import time as _time

        if Path(package_path).suffix.lower() != ".libpkg":
            return {"success": False, "error": "BAD_PARAM",
                    "reason": "package_path must be a .LibPkg"}
        sources = [str(s) for s in (source_libraries or []) if str(s).strip()]
        missing = [s for s in sources if not os.path.isfile(s)]
        if not sources or missing:
            return {"success": False, "error": "MISSING_SOURCES",
                    "reason": ("no source libraries given" if not sources
                               else "not found: " + ", ".join(missing))}
        bridge = get_bridge()
        steps: dict[str, Any] = {}
        created = not os.path.isfile(package_path)
        if created:
            os.makedirs(os.path.dirname(package_path) or ".", exist_ok=True)
            steps["create"] = await bridge.send_command_async(
                "project.create", {"project_path": package_path,
                                   "project_type": "IntegratedLibrary"})
        else:
            steps["open"] = await bridge.send_command_async(
                "project.open", {"project_path": package_path})
        added = []
        for src in sources:
            reply = await bridge.send_command_async(
                "project.add_document",
                {"document_path": src, "project_path": package_path})
            added.append({"library": src,
                          "ok": bool(isinstance(reply, dict)
                                     and reply.get("success", True))})
        out_dir = Path(package_path).parent / \
            f"Project Outputs for {Path(package_path).stem}"
        intlib = out_dir / f"{Path(package_path).stem}.IntLib"
        before = intlib.stat().st_mtime if intlib.is_file() else None
        started = _time.time()
        steps["compile"] = await bridge.send_command_async(
            "project.compile", {"project_path": package_path}, timeout=300.0)
        after = intlib.stat().st_mtime if intlib.is_file() else None
        built = after is not None and (before is None or after > before)
        out: dict[str, Any] = {
            "success": built, "built": built, "created": created,
            "package_path": package_path, "added": added,
            "intlib": str(intlib) if intlib.is_file() else None,
            "intlib_bytes": intlib.stat().st_size if intlib.is_file() else None,
            "compile_seconds": round(_time.time() - started, 1)}
        if not built:
            out["reason"] = (f"no new IntLib at {intlib} after the compile; "
                             f"read Altium's Messages panel for the errors")
        elif install:
            out["install"] = await bridge.send_command_async(
                "library.install_library", {"library_path": str(intlib)})
        return out

    @mcp.tool()
    async def lib_audit_footprint_vs_datasheet(
        footprint_name: str,
        spec_json: Any,
        library_path: str = "",
    ) -> dict[str, Any]:
        """Audit a footprint against the manufacturer's land pattern.

        Compares the live pads (count, position, size, shape, drill,
        numbering, thermal pad and its paste) with a spec transcribed
        from the manufacturer datasheet fetched in this conversation and
        cited in ``spec.source``. Origin and 90-degree rotation are
        aligned out; a mirrored pattern is reported, not compensated.

        The spec (JSON object or string):
          source: {datasheet_url, reference, part_number}   REQUIRED
          pads: [{name, x, y, w, h, shape?, hole?}]         explicit, mm
          dual_row: {count, pitch, span, pad_w, pad_h, shape?}
          quad: {count, pitch, span_x, span_y, pad_w, pad_h, shape?}
          thermal_pad: {name, x, y, w, h, shape?}
          thermal_paste: 'full' | 'windowed'
          position_tol / size_tol: mm, default 0.05

        dual_row numbers like SOIC and quad like QFN (pin 1 top-left,
        CCW); otherwise give ``pads``. pad_w runs ACROSS the row in
        dual_row but ALONG it in quad. span / span_x / span_y are pad
        CENTRE to pad CENTRE (C on a vendor land pattern), never the
        outer extent Z or the package's lead span.

        Whatever the spec says, pads of different names whose copper
        overlaps are an error and a gap under 0.1 mm a warning
        (check ``pad_clearance``): a footprint that faithfully matches a
        misread spec still shorts.

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

        from ..bridge.exceptions import AltiumCommandError

        bridge = get_bridge()
        try:
            footprint = await bridge.send_command_async(
                "library.get_pad_geometry",
                {"footprint_name": footprint_name, "library_path": library_path},
            )
        except AltiumCommandError as e:
            return _unread_footprint(footprint_name, str(e), e.code)
        problem = _pad_geometry_problem(footprint)
        if problem:
            # A footprint that could not be read is not one with the wrong
            # pad count. Auditing the refusal reported "pad_count: expected
            # 8, actual 0" for a name that simply was not in the library.
            out = _unread_footprint(footprint_name, problem, "")
            out["footprint"] = footprint
            return out
        report = audit_footprint_against_spec(spec, footprint)
        report["footprint"] = footprint
        report["spec_source"] = spec.source.model_dump()
        return report

    @mcp.tool()
    async def lib_audit_footprint_vs_package(
        footprint_name: str,
        package: Any,
        density: str = "N",
        library_path: str = "",
        assembly_layer: str = "",
        courtyard_layer: str = "",
    ) -> dict[str, Any]:
        """Audit an existing PcbLib footprint against its package drawing.

        Takes the PackageSpec (as lib_create_footprint_from_package does).
        Reads only. Checks pad count, numbering and pin 1 (names a
        mirror-image footprint), pitch, lands against IPC-7351B within
        vendor tolerances, holes, exposed-pad paste, solder mask, origin,
        courtyard, silkscreen, assembly outline and height. Unread items
        are in ``not_examined``. Run footprint_check_package on the
        package first.

        Args:
            footprint_name: the footprint in the library.
            package: the PackageSpec as an object or JSON text.
            density: L, N or M, the IPC pattern compared against.
            library_path: optional .PcbLib path; default the focused one.
            assembly_layer, courtyard_layer: the mechanical layers for
                those roles; empty picks them by the library's layer kind
                ("Assembly Top" / "Courtyard Top"), then by name.

        Returns:
            ``{ok, errors, warnings, findings: [{check, severity, message,
            pad?, expected?, actual?}], examined, not_examined, alignment,
            metrics, notes, package_check}``. ``ok`` is true with no
            error-severity finding.
        """
        from ..bridge.exceptions import AltiumCommandError
        from ..design.landpattern.audit import audit_footprint_vs_package
        from ..design.landpattern.check import check_package, read_package
        from ..design.landpattern.emit import model_from_package
        from ..design.landpattern.spec import SpecError

        try:
            spec = read_package(package)
            model = model_from_package(spec, density=density or "N")
        except SpecError as e:
            return {"ok": False, "reason": str(e), "findings": []}

        bridge = get_bridge()
        try:
            footprint = await bridge.send_command_async(
                "library.get_pad_geometry",
                {"footprint_name": footprint_name, "library_path": library_path},
            )
        except AltiumCommandError as e:
            return _unread_footprint(footprint_name, str(e), e.code)
        problem = _pad_geometry_problem(footprint)
        if problem:
            out = _unread_footprint(footprint_name, problem, "")
            out["footprint"] = footprint
            return out
        lib_path = str(footprint.get("library_path") or library_path or "")

        prims: Optional[dict[str, Any]] = None
        prims_reason = ""
        try:
            reply = await bridge.send_command_async(
                "library.get_footprint_primitives",
                {"footprint_name": footprint_name, "library_path": lib_path})
            if isinstance(reply, dict) and isinstance(reply.get("tracks"), list):
                prims = reply
            else:
                prims_reason = "the primitives read carried no track list"
        except AltiumCommandError as e:
            prims_reason = (f"the primitives read failed ({e.code or e}); a "
                            "deployed script older than this tool has no "
                            "get_footprint_primitives handler")

        mech: Optional[list[dict[str, Any]]] = None
        notes: list[str] = []
        try:
            reply = await bridge.send_command_async("library.get_mech_layers", {})
            mpath = str((reply or {}).get("library_path") or "")
            if mpath and lib_path and mpath.lower() != lib_path.lower():
                notes.append(f"the mechanical layer kinds come from {mpath}, "
                             f"not {lib_path}; layers were matched by name only")
            else:
                mech = list((reply or {}).get("layers") or [])
        except AltiumCommandError as e:
            notes.append(f"mechanical layer kinds not read ({e.code or e}); "
                         "layers were matched by name only")

        report = audit_footprint_vs_package(
            spec, footprint["pads"], model=model, primitives=prims,
            mech_layers=mech, courtyard_layer=courtyard_layer,
            assembly_layer=assembly_layer, primitives_reason=prims_reason)
        report["notes"] = notes + list(report.get("notes") or [])
        if prims is not None:
            report["primitives_read"] = {
                k: len(prims.get(k) or [])
                for k in ("tracks", "arcs", "regions", "fills", "texts")}
            report["primitives_read"]["height_mm"] = prims.get("height_mm")
        sanity = check_package(spec)
        report["package_check"] = {
            "ok": sanity["ok"], "errors": sanity["errors"],
            "warnings": sanity["warnings"],
            "findings": [f for f in sanity["findings"]
                         if f["severity"] == "error"]}
        if not sanity["ok"]:
            report["notes"].append(
                "the package itself fails its sanity checks "
                "(package_check); the footprint was audited against a "
                "package that cannot be right")
        report["footprint"] = footprint_name
        report["library_path"] = lib_path
        return report

    @mcp.tool()
    async def lib_easyeda_search(query: str, limit: int = 20) -> dict[str, Any]:
        """Search LCSC / EasyEDA for parts by MPN or description.

        EXPECT THIS TO FAIL, and not for lack of credentials: the
        service's search routes no longer answer (checked live), and no
        login would restore them. Search LCSC in a browser instead and
        import by id, which still works:
        lib_easyeda_import(lcsc_id="C1234", ...). When it does answer it
        gives candidates only. HTTPS with a host allowlist;
        EASYEDA_API_BASE overrides the endpoint.

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

        target="altium" returns an ORDERED PLAN of this server's library
        tools, like lib_easyeda_import; nothing is written until you run
        the steps. target="inspect" only parses. A multi-unit symbol
        becomes one Altium multi-part symbol; hidden pins stay hidden;
        derived symbols take their parent's geometry. Audit a converted
        footprint with ``lib_audit_footprint_vs_datasheet`` before
        trusting it.

        Args:
            symbol_path, footprint_path: the KiCad files; give either or
                both.
            symbol_name: one symbol of a multi-symbol library; default
                the first.
            target: "altium" (default) or "inspect".
            schlib_path, pcblib_path: destination libraries, required
                for target="altium".
            include_body_art: also draw the symbol body; False gives
                pins only.
            unit: convert only this sub-part (0 = all).
            model_3d_path: a resolved 3D model file to link (for example
                from part_fetch).

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
        """Convert an EasyEDA / LCSC part to KiCad files or an Altium plan
        (own converter, from EasyEDA's published format).

        Args:
            lcsc_id: fetch online, e.g. "C7420".
            payload_path: or read a saved component JSON (offline).
            target: "inspect" parses only (use first on an unfamiliar
                part); "kicad" writes ``.kicad_sym`` and ``.kicad_mod``
                into ``output_dir``; "altium" returns an ordered plan of
                this server's lib_* tools that recreates the part in
                ``schlib_path`` / ``pcblib_path``. Nothing is written to
                Altium; review the plan, then run the steps
                (tool_invoke).
            schlib_path, pcblib_path: required for target="altium".
            output_dir: required for target="kicad".
            symbol_name, footprint_name: override the part's own names.
            include_body_art: also draw the symbol body; False gives
                pins only.
            fetch_3d: kicad only; fetch the 3D model as a ``.wrl`` the
                footprint references (Altium needs STEP, not VRML).

        An imported footprint is a vendor drawing, not ground truth:
        audit it with ``lib_audit_footprint_vs_datasheet``. Polygon pads
        and slotted holes are reported in ``warnings``.

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

        Per component: clears SourceLibraryName and TargetFileName; keeps
        ONE model per (type, name) in place, with its parameters, pin map
        and datafile link; applies ``rename_map``; and repairs footprints
        with no datafile link. Nothing is rebuilt, so nothing is lost.
        When renaming footprints, run lib_rename_footprint on the PcbLib
        first and pass the same map here.

        Args:
            library_path: optional .SchLib path; defaults to the focused lib.
            rename_map: optional {old_model_name: new_model_name} applied to
                the kept model's name and matching datafile entity.
            dedupe_only: skip renames; only collapse duplicates + clear source.
            source_library: optional; written into every PCBLIB model's
                datafile Location so footprints embed on compile (never
                blanked). Copy it from a known-good model (see
                lib_set_model_source).
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
        ``lib_delete_footprint_primitives``, ``lib_modify_footprint_pads``,
        ``lib_link_3d_model``, ``lib_set_footprint_height`` or
        ``lib_update_footprint_heights_from_3d``, run ``lib_save_library``
        and then this:
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
            [{name, storage, height_raw, height_mils, height_mm, complete,
            counts, pads, tracks, arcs, vias, fills, texts, regions,
            bodies, other_records, parse_error?}]}``.

            ``height_mils`` / ``height_mm`` are the footprint's HEIGHT as
            the file stores it (its Parameters stream, ``height_raw``
            being the stored string, e.g. ``130.7087mil``); null when the
            file holds none. This is the proof a height set by
            ``lib_set_footprint_height`` reached disk.

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
