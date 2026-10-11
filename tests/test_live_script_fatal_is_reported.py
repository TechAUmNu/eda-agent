# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The 2026-10-11 dead loop, as the Python side sees it.

FIELD (bridge 2026.10.11.02, AD 26.10.1.5). ``lib_add_symbol_lines`` hit
the script engine's "Undeclared identifier: CurrentPartID" box: a
TMessageForm titled "Error" with only OK, the same class, caption and
button as an ordinary exception box. app_list_open_dialogs called it a
plain ``error``, the bridge's timeout text said "Answer it", and after OK
the polling loop stayed DEAD (no Except runs on that halt). app_get_status
kept answering ``attached: true`` for twenty minutes while the workspace
held ``last_fault.json`` = ``dead_loop``.

* app_list_open_dialogs classifies that box as ``script_fatal``, with a
  note that OK does not resume the bridge and the recovery steps.
* The bridge's timeout suffix says the same instead of "Answer it".
* app_get_status reports the recorded fault (``last_fault``) and a
  ``loop_note``, so ``attached`` is not read as a live loop.
* The symbol-edit tools carry ``component_name`` to the bridge when given
  and send byte-identical payloads when not.

Each test fails on 3aca481.
"""
from __future__ import annotations

import json

import pytest

from eda_agent.ui import dialog_report


class _Win:
    def __init__(self, title, message, buttons=("OK",), ocr=()):
        self.title = title
        self.class_name = "TMessageForm"
        self.hwnd = id(self)
        self._message = list(message)
        self._buttons = buttons
        self._ocr = list(ocr)

    def message_text(self):
        return list(self._message)

    def buttons(self):
        class _B:
            def __init__(self, t):
                self.text, self.class_name, self.enabled = t, "TButton", True
        return [_B(t) for t in self._buttons]

    def has_unreadable_content(self):
        return False


# ---------------------------------------------------------------------------
# app_list_open_dialogs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Undeclared identifier: CurrentPartID",
    "Undeclared identifier: CurrentPartlD",      # the field OCR
    "Undeclared identifier: SchIterator_Create",
])
def test_the_engines_undeclared_identifier_box_is_script_fatal(text):
    assert dialog_report.classify("Error", [text]) == "script_fatal"


def test_an_ordinary_error_box_is_still_an_error():
    assert dialog_report.classify("Error", ["Cannot open file"]) == "error"
    assert dialog_report.classify(
        "Warning", ["File type not recognised."]) == "warning"


def test_a_script_fatal_box_says_ok_does_not_resume_the_bridge(monkeypatch):
    dialog_report.forget_ocr()
    monkeypatch.setattr(dialog_report.ocr, "read_window_text",
                        lambda hwnd: ["Undeclared identifier: CurrentPartlD",
                                      "OK"])
    d = dialog_report.describe_dialog(_Win("Error", []))
    assert d["kind"] == "script_fatal"
    assert d["needs_a_human"] is True
    assert d["ok_resumes_the_bridge"] is False
    note = d["note"]
    assert "HALTED" in note and "Stop" in note and "StartMCPServer" in note

    summary = dialog_report._summarise([d], blocked=True)
    assert "script_fatal" in summary
    assert "will NOT resume" in summary


# ---------------------------------------------------------------------------
# The bridge's timeout text
# ---------------------------------------------------------------------------

def test_the_timeout_suffix_does_not_tell_a_script_fatal_box_to_be_answered():
    from eda_agent.bridge.altium_bridge import AltiumBridge
    probe = {"dialog_count": 1, "kinds": ["script_fatal"],
             "summary": "script_fatal: 'Error' says 'Undeclared identifier'"}
    text = AltiumBridge._dialog_suffix(probe)
    assert "will NOT resume" in text
    assert "StartMCPServer" in text
    assert "Answer it" not in text
    plain = AltiumBridge._dialog_suffix(
        {"dialog_count": 1, "kinds": ["error"], "summary": "error: x"})
    assert "Answer it" in plain


# ---------------------------------------------------------------------------
# app_get_status
# ---------------------------------------------------------------------------

def _status(tmp_path):
    from unittest.mock import patch
    from eda_agent.bridge.altium_bridge import AltiumBridge
    from eda_agent.bridge.process_manager import AltiumSelection
    from eda_agent.config import configure
    configure(workspace_dir=tmp_path)
    bridge = AltiumBridge()
    bridge._attached = True
    with patch.object(bridge.process_manager, "select_altium_process",
                      return_value=AltiumSelection(None, [], "none")):
        return bridge.get_altium_status()


def test_status_reports_a_recorded_dead_loop(tmp_path):
    from eda_agent.bridge.fault_state import record_fault
    from eda_agent.bridge.recovery import DEAD_LOOP, recovery_guidance
    record_fault(tmp_path, recovery_guidance(DEAD_LOOP), when="20:20:36")
    status = _status(tmp_path)
    fault = status["last_fault"]
    assert fault["fault"] == "dead_loop"
    assert fault["when"] == "20:20:36"
    assert any("StartMCPServer" in s for s in fault["steps"])
    assert "dead_loop" in status["loop_note"]
    assert "attached" in status["loop_note"]


def test_status_with_no_fault_recorded_says_none(tmp_path):
    status = _status(tmp_path)
    assert status["last_fault"] is None
    assert "loop_note" not in status


def test_status_survives_an_unreadable_fault_file(tmp_path):
    (tmp_path / "last_fault.json").write_text("{not json", encoding="utf-8")
    assert _status(tmp_path)["last_fault"] is None
    (tmp_path / "last_fault.json").write_text(json.dumps({"x": 1}),
                                              encoding="utf-8")
    assert _status(tmp_path)["last_fault"]["fault"] == "unknown"


# ---------------------------------------------------------------------------
# component_name reaches the bridge, and only when given
# ---------------------------------------------------------------------------

class _Sent:
    command = None
    params = None


def _tools(monkeypatch, module_name, register):
    import importlib
    module = importlib.import_module(module_name)
    sent = _Sent()

    class FakeBridge:
        async def send_command_async(self, command, params=None,
                                     timeout=None):
            sent.command, sent.params = command, params
            return {"ok": True}

    monkeypatch.setattr(f"{module_name}.get_bridge", lambda: FakeBridge())
    captured = {}

    class DummyMcp:
        def tool(self):
            def decorator(fn):
                captured[fn.__name__] = fn
                return fn
            return decorator

    getattr(module, register)(DummyMcp())
    return captured, sent


_LIB_CALLS = [
    ("lib_add_pins", {"pins": [{"designator": "1", "name": "A"}]}),
    ("lib_add_symbol_rectangle", {"x1": 0, "y1": 0, "x2": 100, "y2": 100}),
    ("lib_add_symbol_text", {"texts": [{"text": "HI"}]}),
    ("lib_add_symbol_lines", {"lines": [{"x1": 0, "x2": 100}]}),
    ("lib_add_symbol_arc", {"x_center": 0, "y_center": 0, "radius": 50}),
    ("lib_add_symbol_polygon", {"vertices": "0,0,100,0,100,100"}),
    ("lib_split_pin_functions", {}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", _LIB_CALLS)
async def test_library_edit_tools_carry_component_name(monkeypatch, tool,
                                                       args):
    tools, sent = _tools(monkeypatch, "eda_agent.tools.library",
                         "register_library_tools")
    await tools[tool](**args, component_name="U_X")
    assert sent.params.get("component_name") == "U_X"
    await tools[tool](**args)
    assert "component_name" not in sent.params, (
        "a call without component_name must send what it always sent")


@pytest.mark.asyncio
async def test_obj_create_and_batch_create_carry_component_name(monkeypatch):
    tools, sent = _tools(monkeypatch, "eda_agent.tools.generic",
                         "register_generic_tools")
    await tools["obj_create"](object_type="eLine", properties="X=1",
                              container="component", component_name="U_X")
    assert sent.params["component_name"] == "U_X"
    await tools["obj_create"](object_type="eLine", properties="X=1")
    assert "component_name" not in sent.params
    await tools["obj_batch_create"](
        operations=[{"object_type": "eLine", "properties": "X=1",
                     "container": "component"}], component_name="U_X")
    assert sent.params["component_name"] == "U_X"
