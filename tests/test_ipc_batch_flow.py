# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""lib_ipc_batch_generate's sequence, against a fake bridge and fake form.

The order is the contract, because each step depends on the one before:

* nothing reaches Altium when preflight finds a measured trap, or when a
  CSV's names are already in the library
* the library is refocused and READ BACK before every run, because the
  previous run's report took focus
* while the batch form is up the bridge is not called at all
* a reload never follows a save that did not reach disk, since closing a
  dirty library raises a prompt that blocks the bridge
* only names this call created are renamed, and the result is read back
  from the reopened file
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from eda_agent.tools import ipc_batch_flow as flow

SOT = ("SOT23,Package Specifications\n"
       "FootprintName,FootprintDescription,PinCount,PinOrder\n")


class FakeBridge:
    def __init__(self, workspace, library, names):
        self.config = type("C", (), {"workspace_dir": workspace})()
        self.library = library
        self.names = set(names)
        self.focused = ""
        self.calls = []
        self.save_writes = True
        self.detached = []

    async def send_command_async(self, command, params=None, timeout=None):
        params = params or {}
        self.calls.append(command)
        if command == "library.get_footprints":
            return {"footprints": [{"name": n} for n in sorted(self.names)]}
        if command == "application.set_active_document":
            self.focused = params["file_path"]
            return {"success": True}
        if command == "application.get_active_document":
            return {"file_path": self.focused}
        if command == "application.close_document":
            return {"closed": True}
        if command == "library.save_library":
            if self.save_writes:
                Path(self.library).write_text(str(len(self.calls)))
            return {"still_modified": not self.save_writes,
                    "file_age_changed": self.save_writes}
        if command == "library.reload_library":
            return {"reloaded": True}
        if command == "library.rename_footprints":
            results = []
            for op in params["renames"].split("~~"):
                fields = dict(f.split("=", 1) for f in op.split(";"))
                self.names.discard(fields["old"])
                self.names.add(fields["new"])
                results.append({"old": fields["old"], "new": fields["new"],
                                "status": "renamed"})
            return {"results": results}
        raise AssertionError(f"unexpected command {command}")

    def send_command_detached(self, command, params=None):
        self.calls.append("detached:" + command)
        self.detached.append(params)
        return "rid"

    def collect_detached(self, request_id, timeout):
        self.calls.append("collect")
        return object()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    library = tmp_path / "carrier.PcbLib"
    library.write_text("lib")
    csv_path = tmp_path / "sot.csv"
    csv_path.write_text(SOT + 'SOT-23-6_P0.95,six,6,"1,2,3,4,5,6"\n')
    bridge = FakeBridge(tmp_path, str(library), {"R_0402", "HDR_1,2"})

    runs = []

    def drive_one(pid, csv, dirs, **kw):
        runs.append(csv)
        assert bridge.calls[-1] == "detached:generic.run_process", (
            "the form is driven straight after the process is fired")
        bridge.calls.append("ui")
        bridge.names.add("SOT-23-6_P0,95")       # what the generator writes
        return {"ok": True, "batch_dialog_open": False,
                "report": {"path": str(tmp_path / "r.html"), "front": {},
                           "other_sources": [],
                           "source": {"fatal": 0, "errors": 0,
                                      "warnings": 0, "messages": []}}}

    monkeypatch.setattr(flow.ipc_batch_dialog, "drive_one", drive_one)
    return bridge, str(library), str(csv_path), runs


def _run(bridge, csvs, library, **kw):
    return asyncio.run(flow.run_batch(bridge, 1, csvs, library, **kw))


def test_a_measured_trap_stops_before_altium_is_touched(setup, tmp_path):
    bridge, library, _csv, runs = setup
    bad = tmp_path / "bad.csv"
    bad.write_text(SOT + "SOT-23-3_P0.95,three,3,\n")    # blank on SOT23
    out = _run(bridge, [str(bad)], library)
    assert out["stopped"]["stage"] == "preflight"
    assert bridge.calls == [] and runs == []


def test_a_name_already_in_the_library_is_refused(setup):
    bridge, library, csv_path, runs = setup
    bridge.names.add("SOT-23-6_P0,95")          # a comma form counts too
    out = _run(bridge, [csv_path], library)
    assert out["stopped"]["stage"] == "preflight"
    assert out["already_present"] == ["SOT-23-6_P0,95"]
    assert runs == [] and bridge.detached == []


def test_the_whole_sequence_in_order(setup):
    bridge, library, csv_path, runs = setup
    out = _run(bridge, [csv_path], library)
    assert out["ok"], out.get("stopped")
    assert out["generated"] == ["SOT-23-6_P0.95"]
    assert out["renamed"] == [{"from": "SOT-23-6_P0,95",
                               "to": "SOT-23-6_P0.95", "status": "renamed"}]
    assert "HDR_1,2" in bridge.names, "a name this call did not make"
    assert bridge.detached == [{"process": "PCB:BatchPCBLibCreate",
                                "params": ""}]
    assert bridge.calls == [
        "library.get_footprints",                  # before
        "application.set_active_document",         # refocus
        "application.get_active_document",         # and read it back
        "detached:generic.run_process", "ui", "collect",
        "application.close_document",              # the report
        "library.get_footprints",                  # what was made
        "library.save_library",
        "application.set_active_document", "application.get_active_document",
        "library.reload_library",
        "library.rename_footprints",
        "library.save_library",
        "application.set_active_document", "application.get_active_document",
        "library.reload_library",
        "library.get_footprints",                  # read back from disk
    ]


def test_preflight_only_runs_nothing(setup):
    bridge, library, csv_path, runs = setup
    out = _run(bridge, [csv_path], library, preflight_only=True)
    assert out["ok"] and runs == []
    assert out["preflight"]["expected_names"] == ["SOT-23-6_P0.95"]
    assert bridge.calls == ["library.get_footprints"]


def test_a_report_with_errors_stops_and_leaves_the_work_unsaved(
        setup, monkeypatch):
    bridge, library, csv_path, runs = setup

    def failing(pid, csv, dirs, **kw):
        bridge.names.add("SOT-23-6_P0,95")
        return {"ok": True, "batch_dialog_open": False, "report": {
            "path": "r.html", "front": {}, "other_sources": [],
            "source": {"fatal": 0, "errors": 2, "warnings": 0,
                       "messages": [{"description": "Error: x"}]}}}

    monkeypatch.setattr(flow.ipc_batch_dialog, "drive_one", failing)
    out = _run(bridge, [csv_path, csv_path], library)
    assert out["stopped"]["stage"] == "report"
    assert out["unsaved"] == ["SOT-23-6_P0,95"]
    assert bridge.calls.count("detached:generic.run_process") == 1, (
        "the second CSV must not run after the first reported errors")
    assert "library.save_library" not in bridge.calls


def test_no_reload_after_a_save_that_did_not_land(setup):
    bridge, library, csv_path, runs = setup
    bridge.save_writes = False
    out = _run(bridge, [csv_path], library)
    assert out["stopped"]["stage"] == "save_generated"
    assert "library.reload_library" not in bridge.calls


def test_a_form_left_open_ends_the_call_without_the_bridge(
        setup, monkeypatch):
    bridge, library, csv_path, runs = setup
    monkeypatch.setattr(flow.ipc_batch_dialog, "drive_one",
                        lambda *a, **k: {"ok": False, "stage": "run",
                                         "batch_dialog_open": True,
                                         "reason": "a dialog appeared"})
    monkeypatch.setattr(flow.dialog_report, "report", lambda pid: {})
    out = _run(bridge, [csv_path], library)
    assert out["stopped"]["bridge_blocked"] is True
    assert bridge.calls[-1] == "detached:generic.run_process", (
        "nothing may be sent while the form blocks the bridge")
