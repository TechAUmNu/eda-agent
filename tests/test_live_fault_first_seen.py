# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""last_fault keeps the FIRST sighting, and says how the fault was found.

FIELD (2026-10-12 19:35-19:37, bridge 2026.10.12.02, the P8 halt; finding
D-TOOL-1). The script-fatal box went up at 19:35:4x and the fault was
recorded then. Every later keep-alive that timed out recorded it AGAIN,
and each record overwrote ``when``, so ``last_fault.when`` drifted to the
latest failed ping instead of the moment the loop died. And the recorded
guidance was the generic dead-loop text ("probably not running"), although
the bridge had SEEN the box that settles it.

* A re-sighting of the same fault kind keeps ``first_seen`` (and ``when``)
  and moves ``last_seen``; a different kind starts a new record.
* ``found_by`` records how it was found. A dead loop found by its
  script-fatal box says so in the guidance, the status summary and
  ``loop_note``, and a later sighting that only found silence does not
  overwrite that evidence.
* Every place the bridge records a dead loop passes how it found it.

Each test fails on 7af6747.
"""
from __future__ import annotations

import time

import pytest

from eda_agent.bridge.fault_state import read_fault, record_fault
from eda_agent.bridge.recovery import DEAD_LOOP, STUCK_HANDLER, recovery_guidance


def test_a_resighting_keeps_the_first_time():
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        ws = Path(d)
        record_fault(ws, recovery_guidance(DEAD_LOOP), when="2026-10-12T19:35:47")
        record_fault(ws, recovery_guidance(DEAD_LOOP), when="2026-10-12T19:36:17")
        record_fault(ws, recovery_guidance(DEAD_LOOP), when="2026-10-12T19:36:47")
        payload = read_fault(ws)
        assert payload["when"] == "2026-10-12T19:35:47", (
            "when moved to the latest sighting")
        assert payload.get("first_seen") == "2026-10-12T19:35:47"
        assert payload.get("last_seen") == "2026-10-12T19:36:47"


def test_a_different_fault_kind_starts_a_new_record(tmp_path):
    record_fault(tmp_path, recovery_guidance(STUCK_HANDLER), when="T1")
    record_fault(tmp_path, recovery_guidance(DEAD_LOOP), when="T2")
    payload = read_fault(tmp_path)
    assert payload["recovery"]["fault"] == DEAD_LOOP
    assert payload["when"] == "T2"
    assert payload.get("first_seen") == "T2" and payload.get("last_seen") == "T2"


def test_script_fatal_guidance_says_the_box_was_seen():
    from eda_agent.bridge.recovery import SCRIPT_FATAL_SEEN, recovery_message
    g = recovery_guidance(DEAD_LOOP, found_by=SCRIPT_FATAL_SEEN)
    assert g["found_by"] == "script_fatal box seen"
    assert "script_fatal" in g["diagnosis"]
    assert "does NOT resume" in g["diagnosis"]
    assert "probably not running" not in g["diagnosis"]
    assert any("StartMCPServer" in s for s in g["steps"])
    assert "Script IDE" in recovery_message(DEAD_LOOP,
                                            found_by=SCRIPT_FATAL_SEEN)
    # Without it, the generic text and no found_by key (unchanged callers).
    plain = recovery_guidance(DEAD_LOOP)
    assert "found_by" not in plain and "probably not running" in plain["diagnosis"]


def test_silence_after_the_box_does_not_overwrite_the_evidence(tmp_path):
    from eda_agent.bridge.recovery import NO_REPLY_NO_HEARTBEAT, SCRIPT_FATAL_SEEN
    record_fault(tmp_path, recovery_guidance(DEAD_LOOP,
                                             found_by=SCRIPT_FATAL_SEEN),
                 when="T1")
    record_fault(tmp_path, recovery_guidance(DEAD_LOOP,
                                             found_by=NO_REPLY_NO_HEARTBEAT),
                 when="T2")
    payload = read_fault(tmp_path)
    assert payload["found_by"] == SCRIPT_FATAL_SEEN
    assert "script_fatal" in payload["recovery"]["diagnosis"]
    assert (payload["when"], payload["last_seen"]) == ("T1", "T2")


def _bridge(tmp_path):
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    return AltiumBridge()


def _status(bridge):
    from unittest.mock import patch
    from eda_agent.bridge.process_manager import AltiumSelection
    bridge._attached = True
    with patch.object(bridge.process_manager, "select_altium_process",
                      return_value=AltiumSelection(None, [], "none")):
        return bridge.get_altium_status()


def test_the_early_box_is_recorded_as_seen_and_kept_through_timeouts(tmp_path):
    """The field sequence: the early dialog probe finds the box (recorded),
    then later calls time out with no reply and no heartbeat. Status must
    still name the FIRST time and the box."""
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    bridge = _bridge(tmp_path)
    bridge._dialog_probe = lambda: {
        "blocked": True, "dialog_count": 1, "kinds": ["script_fatal"],
        "summary": "script_fatal: 'Error' says 'Undeclared identifier'"}
    start = time.monotonic() - 60
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_loop("r" * 32, tmp_path / "response_x.json",
                          tmp_path / "progress_x.json", tmp_path, 0.01,
                          start + 3600, 30, start, None)
    first = read_fault(tmp_path)
    assert first["found_by"] == "script_fatal box seen"
    # A later keep-alive: no reply, no heartbeat, no dialog probe answer.
    payload = dict(first)
    payload["when"] = payload["first_seen"] = "2026-10-12T19:35:47"
    import json
    (tmp_path / "last_fault.json").write_text(json.dumps(payload),
                                              encoding="utf-8")
    bridge._dialog_probe = lambda: None
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_loop("s" * 32, tmp_path / "response_y.json",
                          tmp_path / "progress_y.json", tmp_path, 0.0005,
                          time.monotonic(), 0.0005, time.monotonic(), None)
    status = _status(bridge)
    fault = status["last_fault"]
    assert fault["when"] == "2026-10-12T19:35:47"
    assert fault["first_seen"] == "2026-10-12T19:35:47"
    assert fault["last_seen"] and fault["last_seen"] != fault["first_seen"]
    assert fault["found_by"] == "script_fatal box seen"
    assert "fatal error box (script_fatal) was seen" in status["loop_note"]
    assert "2026-10-12T19:35:47" in status["loop_note"]
    assert "last seen" in status["loop_note"]


def test_a_silent_dead_loop_says_how_it_was_found(tmp_path):
    from eda_agent.bridge.exceptions import AltiumTimeoutError
    bridge = _bridge(tmp_path)
    bridge._dialog_probe = lambda: None
    with pytest.raises(AltiumTimeoutError):
        bridge._poll_loop("s" * 32, tmp_path / "response_y.json",
                          tmp_path / "progress_y.json", tmp_path, 0.0005,
                          time.monotonic(), 0.0005, time.monotonic(), None)
    fault = _status(bridge)["last_fault"]
    assert fault["found_by"] == "no reply and no heartbeat"
