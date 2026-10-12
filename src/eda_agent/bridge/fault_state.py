# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Persisted DelphiScript-fault state for the dashboard banner (roadmap 1.2).

When the bridge detects an engine fault it records the diagnosis + recovery
steps to ``last_fault.json`` in the workspace; the web dashboard reads it to
show a recovery banner ("the loop is down: here's how to restart it"). The
bridge clears it once a command succeeds again, so the banner disappears the
moment the loop recovers.

Kept out of the bridge's hot path: the bridge only touches these files when a
fault has actually been recorded (guarded by an in-memory flag), so a healthy
session pays nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional
from eda_agent.atomicfile import replace_with_retry

FAULT_FILE = "last_fault.json"


#: The ``found_by`` that outranks the others: a script-fatal box on screen
#: settles a diagnosis that silence only suggests (recovery.SCRIPT_FATAL_SEEN).
_DECISIVE = "script_fatal box seen"


def current_pump_session(workspace_dir: Path) -> str:
    """The stamp of the newest ``_session_start`` row of ``activity.log``.

    StartMCPPump appends ``<stamp>,0,_session_start,version=...`` before it
    arms the poll timer, so the stamp names the pump session a fault was
    sighted in. '' when the log is missing or its last 64 KB hold no such
    row (then the session cannot be told, and nothing is inferred from it).
    """
    path = Path(workspace_dir) / "activity.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    for line in reversed(tail.splitlines()):
        parts = line.split(",")
        if len(parts) >= 3 and parts[2] == "_session_start":
            return parts[0].strip()
    return ""


def record_fault(workspace_dir: Path, guidance: dict, *, when: str = "") -> None:
    """Write the current fault + recovery guidance to the workspace.

    ONE RECORD PER FAULT, NOT PER SIGHTING. While a fault of the same kind
    is already recorded, ``first_seen`` (and ``when``, which is the same
    value) keep the FIRST sighting and ``last_seen`` moves. It used to be
    rewritten on every sighting, and after a script-fatal halt every later
    keep-alive that timed out re-stamped it, so ``last_fault.when`` named
    the latest failed ping rather than the moment the loop died (live
    2026-10-12, finding D-TOOL-1).

    ``found_by`` (from ``guidance``) says how the fault was found. A
    script-fatal box seen on screen is kept, guidance and all, when a later
    sighting of the same fault only found silence: the box is the evidence
    that named the halted line.

    ONE PUMP SESSION PER RECORD. ``session`` is the stamp of the newest
    ``_session_start`` row in ``activity.log`` when the fault was sighted
    (``current_pump_session``). A sighting in a DIFFERENT pump session starts
    a new record even when the kind is the same: the record is removed only
    by a successful call, so without this, a loop that died, was recovered
    (OK, Stop, StartMCPServer) and died again on its very first call - the
    P8 shape - would have kept the OLD incident's ``first_seen``,
    ``found_by`` and box guidance. When either session is unknown ('') the
    sightings are taken to be one fault, as before.
    """
    path = Path(workspace_dir) / FAULT_FILE
    found_by = str(guidance.get("found_by") or "")
    first_seen = when
    session = current_pump_session(workspace_dir)
    previous = read_fault(workspace_dir)
    if isinstance(previous, dict):
        prev_rec = previous.get("recovery")
        prev_rec = prev_rec if isinstance(prev_rec, dict) else {}
        prev_session = str(previous.get("session") or "")
        same_session = not (session and prev_session
                            and session != prev_session)
        if (same_session and prev_rec.get("fault")
                and prev_rec.get("fault") == guidance.get("fault")):
            first_seen = str(previous.get("first_seen")
                             or previous.get("when") or when)
            session = session or prev_session
            prev_found = str(previous.get("found_by")
                             or prev_rec.get("found_by") or "")
            if prev_found == _DECISIVE and found_by != _DECISIVE:
                guidance = prev_rec
                found_by = prev_found
            elif not found_by:
                found_by = prev_found
    payload = {"recovery": guidance, "when": first_seen,
               "first_seen": first_seen, "last_seen": when,
               "found_by": found_by, "session": session}
    try:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        replace_with_retry(tmp, path)
    except OSError:
        pass  # a fault record is best-effort; never mask the real error


def read_fault(workspace_dir: Path) -> Optional[dict]:
    """Return the recorded fault payload, or None if the loop is healthy."""
    path = Path(workspace_dir) / FAULT_FILE
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_fault(workspace_dir: Path) -> None:
    """Remove the fault record (a command has succeeded; loop recovered)."""
    path = Path(workspace_dir) / FAULT_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
