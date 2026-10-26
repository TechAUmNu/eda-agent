# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""COM apartments for UI work run off the event-loop thread.

MEASURED 2026-10-05 on a fresh MCP server process: the IPC batch form's
options read as [] (queue_readable false) while its plain Win32 text dump
held every caption, and the run stopped at 'configure' with "no
radiobutton reads ...". COM is per thread; comtypes initialises only the
thread that imports it, and drive_one ran in an executor worker with no
CoInitialize, so every MSAA read failed silently and an empty list was
reported as the answer.

The fake below reproduces that failure mode exactly: its accessible read
returns nothing on a thread that has not called CoInitialize. Every test
runs its UI entry on a FRESH threading.Thread, as an executor would.
No real Windows UI is touched.
"""

from __future__ import annotations

import inspect
import threading

import pytest

from eda_agent.tools import ipc_batch_flow as flow
from eda_agent.ui import controls
from eda_agent.ui import ipc_batch_dialog as dlg
from eda_agent.ui import menu, uia
from eda_agent.ui import windows as win


class FakeCom:
    """comtypes' CoInitialize / CoUninitialize, counted per thread."""

    def __init__(self, init_error=None, uninit_error=None):
        self.calls = []
        self.counts = {}
        self.init_error = init_error
        self.uninit_error = uninit_error
        self._lock = threading.Lock()

    def CoInitialize(self):                      # noqa: N802 - comtypes name
        ident = threading.get_ident()
        with self._lock:
            self.calls.append(("init", ident))
        if self.init_error is not None:
            raise self.init_error
        with self._lock:
            self.counts[ident] = self.counts.get(ident, 0) + 1

    def CoUninitialize(self):                    # noqa: N802 - comtypes name
        ident = threading.get_ident()
        with self._lock:
            self.calls.append(("uninit", ident))
            self.counts[ident] = self.counts.get(ident, 0) - 1
        if self.uninit_error is not None:
            raise self.uninit_error

    def initialised(self) -> bool:
        return self.counts.get(threading.get_ident(), 0) > 0


def _on_fresh_thread(fn, *args, **kwargs):
    """Run fn on a new thread, as run_in_executor would; return result+ident."""
    box: dict = {}

    def target():
        box["ident"] = threading.get_ident()
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as exc:             # noqa: BLE001
            box["error"] = exc

    t = threading.Thread(target=target)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "the UI entry hung"
    if "error" in box:
        raise box["error"]
    return box["result"], box["ident"]


# ---------------------------------------------------------------------------
# The batch form, as measured: its options are TXPCheckBox / TXPRadioButton
# child windows, so Win32 sees their captions even when MSAA reads nothing.

_TARGET = r"H:\git\multiviewer\hardware\multiviewer\carrier.PcbLib"
_OPTIONS = [
    ("Produce STEP model", "TXPCheckBox", "checkbox"),
    ("Generate all footprints in " + _TARGET, "TXPRadioButton",
     "radiobutton"),
    ("Open generated report", "TXPCheckBox", "checkbox"),
    ("Generate report on completion", "TXPCheckBox", "checkbox"),
    ("Generate single PcbLib files per input file", "TXPRadioButton",
     "radiobutton"),
]


def _form():
    kids = [win.Control(hwnd=101, class_name="TXPBitBtn", text="Add Files"),
            win.Control(hwnd=102, class_name="TXPBitBtn", text="Start"),
            win.Control(hwnd=103, class_name="TXPBitBtn", text="&Close")]
    kids += [win.Control(hwnd=500 + i, class_name=cls, text=label)
             for i, (label, cls, _role) in enumerate(_OPTIONS)]
    return win.Window(hwnd=100, class_name="TForm_FootprintWizardBatch",
                      title="IPC Compliant Footprints Batch generator",
                      pid=1, controls=kids)


@pytest.fixture
def screen(monkeypatch):
    """A scripted batch form whose MSAA read needs COM on the calling thread."""
    form = _form()
    state = {"windows": {form.hwnd: form}, "pressed": [],
             "read_threads": []}
    com = FakeCom()
    roles = {500 + i: role for i, (_l, _c, role) in enumerate(_OPTIONS)}

    def describe_all(window):
        state["read_threads"].append(threading.get_ident())
        if not com.initialised():
            return []                            # the 2026-10-05 symptom
        return [{"ok": True, "role": roles[c.hwnd], "label": c.text,
                 "name": c.text, "checked": False, "enabled": True,
                 "hwnd": c.hwnd} for c in window.controls if c.hwnd in roles]

    def click(control):
        state["pressed"].append(control.text)
        if control.text == "&Close":
            state["windows"].pop(100, None)

    monkeypatch.setattr(controls, "_comtypes", com)
    monkeypatch.setattr(controls, "available", lambda: True)
    monkeypatch.setattr(controls, "describe_all", describe_all)
    monkeypatch.setattr(controls, "list_items", lambda hwnd: [])
    monkeypatch.setattr(win, "dialogs",
                        lambda pid: list(state["windows"].values()))
    monkeypatch.setattr(win, "capture",
                        lambda hwnd: state["windows"].get(hwnd))
    monkeypatch.setattr(win, "click", click)
    monkeypatch.setattr(win, "wait_for_close",
                        lambda hwnd, timeout=0: hwnd not in state["windows"])
    monkeypatch.setattr(dlg.dialog_report, "report",
                        lambda pid: {"dialogs": len(state["windows"])})
    state["com"] = com
    return state


def test_the_executor_entry_initialises_com_on_its_own_thread(screen):
    """The flow's executor target, on a fresh thread, READS the radios.

    Foreign library on purpose: the run stops at 'configure' after reading
    the destination radio, and the reason it gives ('different library')
    is only reachable if the options were actually read.
    """
    rec, ident = _on_fresh_thread(
        flow._drive_one_reporting, 1, r"C:\w\a.csv", [], dialog_timeout=1,
        library_path=r"H:\git\other\parts.PcbLib")
    com = screen["com"]
    assert ("init", ident) in com.calls, (
        "CoInitialize must run on the worker thread itself")
    assert com.calls[0] == ("init", ident)
    assert com.calls[-1] == ("uninit", ident), (
        "and CoUninitialize on that thread after the work")
    assert com.calls.count(("init", ident)) == 1, (
        "nested guards (drive_one -> snapshot -> set_destination) must not "
        "initialise again")
    assert com.counts[ident] == 0, "init and uninit are balanced"
    assert set(screen["read_threads"]) == {ident}

    assert len(rec["before"]["options"]) == len(_OPTIONS)
    assert "options_error" not in rec["before"]
    assert rec["stage"] == "configure"
    assert "different library" in rec["destination"]["reason"]
    assert "Start" not in screen["pressed"]


def test_drive_one_alone_is_guarded_too(screen):
    """Callers that skip the flow wrapper still get an apartment."""
    rec, ident = _on_fresh_thread(
        dlg.drive_one, 1, r"C:\w\a.csv", [], dialog_timeout=1,
        library_path=r"H:\git\other\parts.PcbLib")
    assert screen["com"].calls == [("init", ident), ("uninit", ident)]
    assert len(rec["before"]["options"]) == len(_OPTIONS)


def test_without_com_the_empty_read_is_loud_and_start_is_refused(
        screen, monkeypatch):
    """Negative control, and the louder failure.

    No COM on the thread (comtypes unavailable to the guard), so the fake
    reads nothing, exactly as live. The record must say UNREAD, not list
    zero options, and the destination must refuse Start with that reason
    instead of 'no radiobutton reads ...'.
    """
    monkeypatch.setattr(controls, "_comtypes", None)
    rec, _ident = _on_fresh_thread(
        flow._drive_one_reporting, 1, r"C:\w\a.csv", [], dialog_timeout=1,
        library_path=_TARGET)
    before = rec["before"]
    assert "options" not in before, (
        "an empty list reads as 'this form has no options'")
    assert "UNREAD" in before["options_error"]
    assert "COM is not initialised" in before["options_error"]
    assert before["queue_readable"] is False
    assert before["options_unread"][1] == "Generate all footprints in " + \
        _TARGET

    dest = rec["destination"]
    assert dest["ok"] is False and dest.get("unread") is True
    assert "UNREAD" in dest["reason"]
    assert "no radiobutton reads" not in dest["reason"]
    assert rec["stage"] == "configure"
    assert "Start" not in screen["pressed"]
    assert screen["pressed"] == ["&Close"]


def test_set_option_reports_an_unread_form_too(screen, monkeypatch):
    monkeypatch.setattr(controls, "_comtypes", None)
    out, _ = _on_fresh_thread(dlg.set_option, _form(), dlg.REPORT_ON_WORDS,
                              True, role="checkbox")
    assert out["ok"] is False and out["unread"] is True
    assert "UNREAD" in out["reason"]


# ---------------------------------------------------------------------------
# The guard itself.

def test_the_guard_is_reentrant_and_balanced(monkeypatch):
    com = FakeCom()
    monkeypatch.setattr(controls, "_comtypes", com)
    seen: dict = {}

    def work():
        with controls.com_apartment():
            with controls.com_apartment():
                seen["inner"] = list(com.calls)
            seen["after_inner"] = list(com.calls)
        return list(com.calls)

    calls, ident = _on_fresh_thread(work)
    assert seen["inner"] == [("init", ident)]
    assert seen["after_inner"] == [("init", ident)], (
        "leaving an inner guard must not uninitialise the thread")
    assert calls == [("init", ident), ("uninit", ident)]


def test_a_thread_it_did_not_initialise_is_not_uninitialised(monkeypatch):
    """RPC_E_CHANGED_MODE: the thread is already in the MTA. Not ours."""
    err = OSError(-2147417850, "Cannot change thread mode after it is set")
    com = FakeCom(init_error=err)
    monkeypatch.setattr(controls, "_comtypes", com)
    ran: list = []

    def work():
        with controls.com_apartment():
            ran.append(True)
        return list(com.calls)

    calls, ident = _on_fresh_thread(work)
    assert ran == [True], "the body still runs"
    assert calls == [("init", ident)], "and no CoUninitialize follows"


def test_the_guard_never_raises(monkeypatch):
    com = FakeCom(uninit_error=OSError("teardown failed"))
    monkeypatch.setattr(controls, "_comtypes", com)
    out, _ = _on_fresh_thread(controls.in_com_apartment(lambda: 7))
    assert out == 7

    monkeypatch.setattr(controls, "_comtypes", None)
    out, _ = _on_fresh_thread(controls.in_com_apartment(lambda: 8))
    assert out == 8


def test_the_body_error_still_balances(monkeypatch):
    com = FakeCom()
    monkeypatch.setattr(controls, "_comtypes", com)

    @controls.in_com_apartment
    def boom():
        raise ValueError("inside")

    with pytest.raises(ValueError):
        _on_fresh_thread(boom)
    assert [name for name, _ in com.calls] == ["init", "uninit"]


def test_a_raising_body_releases_its_com_objects_before_uninit(monkeypatch):
    """A raise must not carry COM proxies past CoUninitialize.

    The traceback holds every finished frame of the body and so their
    IAccessible / UIA locals; freed later on another thread they would be
    Released against an ended apartment. The fake proxy records, at its
    own release, whether its thread's COM was still up. pytest.raises
    keeps the exception (and so the traceback) alive well past the guard,
    which is exactly the executor future holding it: without the frame
    clearing this proxy is still alive after uninit, and the test fails.
    """
    com = FakeCom()
    monkeypatch.setattr(controls, "_comtypes", com)
    released: list = []

    class Proxy:
        def __del__(self):
            released.append(com.initialised())

    def deep():
        proxy = Proxy()                          # noqa: F841 - held by frame
        raise ValueError("inside, with a live proxy in this frame")

    @controls.in_com_apartment
    def boom():
        deep()

    with pytest.raises(ValueError):
        _on_fresh_thread(boom)
    assert released == [True], (
        "the proxy must die inside the apartment, on its own thread")
    assert [name for name, _ in com.calls] == ["init", "uninit"]


def test_objects_cached_in_an_apartment_die_with_it(monkeypatch):
    """uia's automation object is per apartment, not per process."""
    com = FakeCom()
    monkeypatch.setattr(controls, "_comtypes", com)
    made: list = []

    class FakeCC:
        @staticmethod
        def CreateObject(cls, interface=None):    # noqa: N802
            made.append(object())
            return made[-1]

    class FakeUIA:
        CUIAutomation = object()
        IUIAutomation = object()

    monkeypatch.setattr(uia, "_AVAILABLE", True)
    monkeypatch.setattr(uia, "_cc", FakeCC)
    monkeypatch.setattr(uia, "_UIA", FakeUIA, raising=False)

    def work():
        with controls.com_apartment():
            first = uia._uia()
            again = uia._uia()
        dropped = getattr(uia._LOCAL, "automation", None)
        with controls.com_apartment():
            second = uia._uia()
        return first, again, dropped, second

    (first, again, dropped, second), _ = _on_fresh_thread(work)
    assert first is again, "reused within one apartment"
    assert dropped is None, "dropped when the apartment that made it ended"
    assert second is not first, "a later apartment makes its own"


# ---------------------------------------------------------------------------
# One guard, used everywhere a worker thread does UI work.

def test_menu_uses_the_shared_guard():
    source = inspect.getsource(menu)
    assert "comtypes.CoInitialize" not in source
    assert source.count("controls.com_apartment()") == 2


@pytest.mark.parametrize("fn", [
    flow._drive_one_reporting, flow._screen_report,
    dlg.drive_one, dlg.snapshot, dlg.set_option, dlg.set_destination,
    dlg.add_file, dlg.start_and_wait, dlg.close_form,
])
def test_executor_entries_carry_the_guard(fn):
    assert hasattr(fn, "__wrapped__"), (
        f"{fn.__name__} runs in an executor and must enter com_apartment")


def test_every_uiauto_executor_target_is_guarded():
    """A run_in_executor target in uiauto either is menu.click_path /
    menu.list_path (guarded inside) or carries in_com_apartment."""
    from eda_agent.tools import uiauto

    source = inspect.getsource(uiauto)
    assert source.count("@controls.in_com_apartment") == 3   # press, run x2
    assert "controls.in_com_apartment(lambda: dialog_driver.drive(" in source


# ---------------------------------------------------------------------------
# When an empty read is a failure.

def test_unread_reason_needs_an_empty_read_and_visible_options():
    form = _form()
    assert controls.unread_reason(form, [{"role": "button"}]) is None
    reason = controls.unread_reason(form, [])
    assert reason and "5 checkbox/radio" in reason and "UNREAD" in reason

    empty = win.Window(hwnd=1, class_name="X", title="t", pid=1, controls=[])
    assert controls.unread_reason(empty, []) is None


def test_stock_button_styles_count_as_options():
    radio = win.Control(hwnd=1, class_name="Button", text="Per file",
                        style=0x50010009)            # BS_AUTORADIOBUTTON
    push = win.Control(hwnd=2, class_name="Button", text="OK",
                       style=0x50010000)             # BS_PUSHBUTTON
    hidden = win.Control(hwnd=3, class_name="TXPCheckBox", text="Hidden",
                         visible=False)
    w = win.Window(hwnd=9, class_name="X", title="t", pid=1,
                   controls=[radio, push, hidden])
    assert controls.option_like_controls(w) == [radio]


def test_a_radio_group_box_is_not_counted_as_an_option():
    """TRadioGroup / TXPRadioGroup is the captioned box, not a radio."""
    radio = win.Control(hwnd=1, class_name="TXPRadioButton", text="Per file")
    box = win.Control(hwnd=2, class_name="TXPRadioGroup", text="Output")
    vcl = win.Control(hwnd=3, class_name="TRadioGroup", text="Mode")
    w = win.Window(hwnd=9, class_name="X", title="t", pid=1,
                   controls=[box, radio, vcl])
    assert controls.option_like_controls(w) == [radio]
    reason = controls.unread_reason(w, [])
    assert "1 checkbox/radio" in reason and "'Output'" not in reason


def test_the_bridge_timeout_probe_enters_the_guard_too():
    """_dialog_probe runs on the bridge's _executor thread and calls the
    same dialog_report.report that _screen_report guards."""
    from eda_agent.bridge.altium_bridge import AltiumBridge

    source = inspect.getsource(AltiumBridge._dialog_probe)
    assert "with controls.com_apartment():" in source
    assert source.index("com_apartment") < source.index(
        "dialog_report.report(")
