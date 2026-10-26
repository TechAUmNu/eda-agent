# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A request whose handler is expected to block behind its own modal.

``send_command_async`` cannot carry one: its probe sees the disabled main
window three seconds in and raises, which made obj_run_process report a
failure on every batch-generator launch that worked. The detached pair
publishes without waiting and collects the reply later, and these tests
hold the parts of that a caller depends on.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from eda_agent.bridge import AltiumBridge
from eda_agent.bridge.exceptions import AltiumCommandError


@pytest.fixture
def bridge():
    from eda_agent.config import configure
    with tempfile.TemporaryDirectory() as tmp:
        configure(workspace_dir=Path(tmp))
        b = AltiumBridge()
        b.ensure_workspace()
        with patch.object(b, "is_altium_running", return_value=True), \
                patch.object(b, "_ensure_keepalive"):
            yield b
        b.detach()


def test_it_publishes_and_returns_at_once(bridge):
    started = time.monotonic()
    rid = bridge.send_command_detached(
        "generic.run_process", {"process": "PCB:BatchPCBLibCreate",
                                "params": ""})
    assert time.monotonic() - started < 1.0
    request = json.loads((bridge.config.workspace_dir
                          / f"request_{rid}.json").read_text("utf-8-sig"))
    assert request["command"] == "generic.run_process"
    assert request["params"]["process"] == "PCB:BatchPCBLibCreate"


def test_no_reply_in_time_is_none_not_an_error(bridge):
    assert bridge.collect_detached("nobody", timeout=0.2) is None


def test_a_late_reply_is_collected_and_removed(bridge):
    path = bridge.config.workspace_dir / "response_late.json"

    def answer():
        time.sleep(0.3)
        path.write_text(json.dumps({"id": "late", "success": True,
                                    "data": {"dispatched": True}}),
                        encoding="utf-8")

    threading.Thread(target=answer).start()
    reply = bridge.collect_detached("late", timeout=5.0)
    assert reply is not None and reply.success
    assert reply.data == {"dispatched": True}
    assert not path.exists()


def test_read_only_mode_refuses_it_too(bridge, monkeypatch):
    monkeypatch.setenv("EDA_AGENT_READONLY", "1")
    with pytest.raises(AltiumCommandError):
        bridge.send_command_detached("generic.run_process", {})
    assert not list(bridge.config.workspace_dir.glob("request_*.json"))
