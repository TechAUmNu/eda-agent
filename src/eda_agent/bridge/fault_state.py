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
    """
    path = Path(workspace_dir) / FAULT_FILE
    found_by = str(guidance.get("found_by") or "")
    first_seen = when
    previous = read_fault(workspace_dir)
    if isinstance(previous, dict):
        prev_rec = previous.get("recovery")
        prev_rec = prev_rec if isinstance(prev_rec, dict) else {}
        if prev_rec.get("fault") and prev_rec.get("fault") == guidance.get("fault"):
            first_seen = str(previous.get("first_seen")
                             or previous.get("when") or when)
            prev_found = str(previous.get("found_by")
                             or prev_rec.get("found_by") or "")
            if prev_found == _DECISIVE and found_by != _DECISIVE:
                guidance = prev_rec
                found_by = prev_found
            elif not found_by:
                found_by = prev_found
    payload = {"recovery": guidance, "when": first_seen,
               "first_seen": first_seen, "last_seen": when,
               "found_by": found_by}
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
