# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guided recovery for DelphiScript engine faults (roadmap 1.2).

When the Altium script engine faults, the bridge can already tell *which*
way it failed (stuck handler vs dead polling loop vs corrupt response) from
the progress-heartbeat file. This module turns that diagnosis into concrete,
consistent recovery steps so an LLM client can relay exactly what the user
must do, instead of every call site inventing its own prose, and the
dashboard can render the same banner.

Pure data; no Altium, no bridge state. One source of truth for the recovery
copy, consumed by the bridge exceptions and the web dashboard.
"""

from __future__ import annotations

from typing import Optional

# Fault kinds the bridge can distinguish.
STUCK_HANDLER = "stuck_handler"      # heartbeat ticking, handler never returns
DEAD_LOOP = "dead_loop"              # no heartbeat, no response
CORRUPT_RESPONSE = "corrupt_response"  # response file present but unparseable

# HOW a fault was found, recorded beside it (``found_by``). The kinds above
# say what the bridge concluded; this says what it saw. A dead loop found
# because a script-engine fatal box was ON SCREEN is a different thing to
# act on than one inferred from silence: the box names the line that
# halted, and pressing its OK does not resume anything.
SCRIPT_FATAL_SEEN = "script_fatal box seen"
NO_REPLY_NO_HEARTBEAT = "no reply and no heartbeat"

_STOP_STEP = (
    "In Altium's Script IDE toolbar press the red Stop button (Run > Stop, "
    "or Ctrl+F3; use Ctrl+Pause/Break if the script is truly hung)."
)
_RELAUNCH_STEP = (
    "Relaunch the polling loop: File > Run Script... > Altium_API > "
    "Dispatcher.pas > StartMCPServer > Run."
)

# An exception inside a handler is shown by Altium's script engine as a
# MODAL "press OK" dialog BEFORE the handler's own Try/Except runs, and
# the engine is blocked until someone presses it. From outside that looks
# exactly like a handler stuck in a loop (the heartbeat file stays), so
# the stuck-handler guidance names the dialog first: pressing OK lets the
# handler finish and the call returns INTERNAL_ERROR, and stopping the
# script instead would throw that away.
_DIALOG_STEP = (
    "Look for an Altium error dialog first: an exception inside the "
    "handler is shown as a modal 'press OK' dialog that blocks the script "
    "engine. If one is open, note its text and press OK; the handler then "
    "finishes and the call returns INTERNAL_ERROR, so do not stop the "
    "script."
)

_GUIDANCE = {
    STUCK_HANDLER: {
        "diagnosis": (
            "Altium is alive (it answered keep-alives) but the command's "
            "handler never returned: an Altium error dialog may be open and "
            "blocking it, or it is stuck in a loop."
        ),
        "steps": [_DIALOG_STEP, _STOP_STEP, _RELAUNCH_STEP],
    },
    DEAD_LOOP: {
        "diagnosis": (
            "No response and no progress heartbeat: the polling loop is "
            "probably not running (never started, or halted on an earlier "
            "engine fault)."
        ),
        "steps": [
            "If an Altium error dialog is open, note its text and dismiss it.",
            _STOP_STEP,
            _RELAUNCH_STEP,
        ],
    },
    # DEAD_LOOP as it is found when a script-fatal box is on screen (live
    # 2026-10-12: "Undeclared identifier: CurrentPartID"). The generic
    # dead-loop text says "probably not running", which is a guess the box
    # has already settled.
    (DEAD_LOOP, SCRIPT_FATAL_SEEN): {
        "diagnosis": (
            "A script-engine fatal error box (script_fatal) was seen on "
            "screen: the engine HALTED on the line the Script IDE shows, and "
            "the polling loop is dead. Pressing OK does NOT resume it."
        ),
        "steps": [
            "Note the box's text and the unit and line the Script IDE "
            "opened, then press OK.",
            _STOP_STEP,
            _RELAUNCH_STEP,
            "After the relaunch no library symbol is held: pass "
            "component_name to symbol edits, or call "
            "lib_set_current_component again.",
        ],
    },
    CORRUPT_RESPONSE: {
        "diagnosis": (
            "The response file was present but unparseable: Altium likely "
            "crashed mid-write."
        ),
        "steps": [
            "Retry the call once (the corrupt file was removed automatically).",
            "If it recurs: " + _STOP_STEP,
            _RELAUNCH_STEP,
        ],
    },
}

# Where a human can read the full story.
_DOCS_HINT = "See README 'Known limitations' > 'Altium DelphiScript engine can crash'."


def _in_flight_phrase(in_flight: Optional[dict]) -> str:
    """'application.save_all (request 1a2b3c4d)', or '' with nothing known."""
    if not in_flight:
        return ""
    command = str(in_flight.get("command") or "").strip()
    if not command:
        return ""
    request_id = str(in_flight.get("request_id") or "").strip()
    if request_id:
        return f"{command} (request {request_id[:8]})"
    return command


def recovery_guidance(fault: str, in_flight: Optional[dict] = None,
                      found_by: str = "") -> dict:
    """Structured recovery steps for a fault kind.

    Returns ``{fault, diagnosis, steps:[...], docs}``. An unknown fault gets
    a generic stop-and-relaunch fallback so callers never get an empty dict.

    ``in_flight`` is the call Altium is working on, as the progress
    heartbeat names it (``{"command", "request_id", ...}``; see
    ``read_in_flight``). When given, a stuck-handler diagnosis names that
    command and the result carries it as ``in_flight``.

    ``found_by`` is how the fault was found (``SCRIPT_FATAL_SEEN``,
    ``NO_REPLY_NO_HEARTBEAT``). When given the result carries it, and a
    dead loop found by its script-fatal box gets the guidance for that.
    """
    entry = _GUIDANCE.get((fault, found_by)) or _GUIDANCE.get(fault)
    if entry is None:
        entry = {
            "diagnosis": "The Altium script engine is not responding as expected.",
            "steps": [_STOP_STEP, _RELAUNCH_STEP],
        }
    diagnosis = entry["diagnosis"]
    named = _in_flight_phrase(in_flight)
    if named and fault == STUCK_HANDLER:
        diagnosis = (
            f"Altium is alive (it answered keep-alives) but the handler for "
            f"{named} never returned: an Altium error dialog may be open and "
            f"blocking it (pressing OK lets it finish with INTERNAL_ERROR), "
            f"or it is stuck in a loop."
        )
    out = {
        "fault": fault,
        "diagnosis": diagnosis,
        "steps": list(entry["steps"]),
        "docs": _DOCS_HINT,
    }
    if named:
        out["in_flight"] = dict(in_flight)
    if found_by:
        out["found_by"] = found_by
    return out


def recovery_message(fault: str, in_flight: Optional[dict] = None,
                     found_by: str = "") -> str:
    """One-line, numbered recovery steps for embedding in an error message.

    The MCP layer surfaces ``str(exception)``, not the structured details, so
    the actionable steps must live in the message text too. Same source as
    ``recovery_guidance``, no drift.
    """
    g = recovery_guidance(fault, in_flight, found_by)
    numbered = " ".join(f"{i}) {s}" for i, s in enumerate(g["steps"], 1))
    named = _in_flight_phrase(in_flight)
    if named and fault == STUCK_HANDLER:
        return f"In flight: {named}. Recovery: {numbered}"
    return f"Recovery: {numbered}"
