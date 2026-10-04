# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Driving the IPC batch form: what it presses, and when it refuses to.

The form is driven from what is on screen, with fakes standing in for
Win32 here. What is guarded is the judgement, not the clicks:

* it never presses Start unless the destination is the CURRENT PcbLib,
  because the other choices write separate PcbLib files elsewhere
* it never decides a run is over without evidence, since every measured
  run finished in under a second and Stop may never be seen
* it never presses anything on a dialog it did not open, and leaves an
  'already exists' prompt or a delete confirmation for a human
* it never touches the bridge, which is blocked for the whole run
"""

from __future__ import annotations

import inspect

import pytest

from eda_agent.ui import ipc_batch_dialog as dlg
from eda_agent.ui import windows as win


def _button(hwnd, text, enabled=True):
    return win.Control(hwnd=hwnd, class_name="TXPBitBtn", text=text,
                       enabled=enabled)


def _form(hwnd=100, start_enabled=True, caption="Start"):
    return win.Window(hwnd=hwnd, class_name="TForm_FootprintWizardBatch",
                      title="IPC Compliant Footprints Batch generator",
                      pid=1, controls=[
                          _button(101, "Add Files"),
                          _button(102, caption, start_enabled),
                          _button(103, "&Close")])


class Screen:
    """A scripted Altium: which windows exist, and what was pressed."""

    def __init__(self, monkeypatch, form):
        self.windows = {form.hwnd: form}
        self.pressed = []
        self.on_press = {}
        monkeypatch.setattr(win, "dialogs", lambda pid: list(
            self.windows.values()))
        monkeypatch.setattr(win, "capture", lambda hwnd: self.windows.get(
            hwnd))
        monkeypatch.setattr(win, "click", self.click)
        monkeypatch.setattr(win, "is_blocked", lambda pid: True)
        monkeypatch.setattr(win, "wait_for_close", lambda hwnd, timeout=0:
                            hwnd not in self.windows)
        monkeypatch.setattr(dlg.dialog_report, "describe_dialog",
                            lambda w, use_ocr=True: {"title": w.title,
                                                     "kind": "unknown"})
        monkeypatch.setattr(dlg.dialog_report, "report",
                            lambda pid: {"dialogs": len(self.windows)})

    def click(self, control):
        self.pressed.append(control.text)
        action = self.on_press.get(control.text)
        if action:
            action()


def test_the_form_is_known_by_its_buttons_as_well_as_its_title():
    untitled = win.Window(hwnd=1, class_name="TForm", title="Something",
                          pid=1, controls=[_button(2, "Add Files"),
                                           _button(3, "Start")])
    assert dlg.is_batch_dialog(_form())
    assert dlg.is_batch_dialog(untitled)
    assert not dlg.is_batch_dialog(win.Window(
        hwnd=4, class_name="TForm", title="Preferences", pid=1,
        controls=[_button(5, "OK")]))


def test_a_disabled_start_is_refused_not_clicked(monkeypatch):
    form = _form(start_enabled=False)
    screen = Screen(monkeypatch, form)
    out = dlg.start_and_wait(1, form, [], r"C:\w\a.csv", timeout=1)
    assert out["ok"] is False and out["stage"] == "start"
    assert screen.pressed == []


def test_a_dialog_raised_mid_run_is_left_alone(monkeypatch):
    form = _form()
    screen = Screen(monkeypatch, form)
    exists = win.Window(hwnd=200, class_name="TForm_FootprintExists",
                        title="Footprint exists", pid=1,
                        controls=[_button(201, "Overwrite")])
    screen.on_press["Start"] = lambda: screen.windows.update({200: exists})
    monkeypatch.setattr(dlg.files, "find_reports", lambda dirs, since: [])
    out = dlg.start_and_wait(1, form, [], r"C:\w\a.csv", timeout=2,
                             poll=0.01)
    assert out["ok"] is False and out["stage"] == "run"
    assert out["dialog"]["title"] == "Footprint exists"
    assert screen.pressed == ["Start"], (
        "nothing may be pressed on a dialog this did not open")


def test_the_run_ends_on_its_report_and_the_form_is_closed(monkeypatch):
    form = _form()
    screen = Screen(monkeypatch, form)
    screen.on_press["&Close"] = lambda: screen.windows.pop(100)
    report = {"path": "r.html", "source": {"errors": 0}, "front": {},
              "other_sources": []}
    monkeypatch.setattr(dlg.files, "find_reports",
                        lambda dirs, since: [{"path": "r.html"}])
    monkeypatch.setattr(dlg.files, "report_for", lambda csv, hits: report)
    out = dlg.start_and_wait(1, form, ["D"], r"C:\w\a.csv", timeout=2,
                             poll=0.01)
    assert out["ok"] and out["finished_by"] == "start_enabled_after_run"
    assert out["report"] is report and out["closed_by_us"]
    assert screen.pressed == ["Start", "&Close"]
    assert out["main_window_blocked"] is True


def test_without_evidence_the_wait_says_so(monkeypatch):
    form = _form()
    screen = Screen(monkeypatch, form)
    screen.on_press["&Close"] = lambda: screen.windows.pop(100)
    monkeypatch.setattr(dlg.files, "find_reports", lambda dirs, since: [])
    out = dlg.start_and_wait(1, form, [], r"C:\w\a.csv", timeout=2,
                             quiet_after=0.05, poll=0.01)
    assert out["ok"] and out["finished_by"] == "start_enabled_no_evidence"
    assert out["report"] is None


def test_start_is_never_pressed_without_the_current_library(monkeypatch):
    form = _form()
    screen = Screen(monkeypatch, form)
    screen.on_press["&Close"] = lambda: screen.windows.pop(100)
    monkeypatch.setattr(dlg, "snapshot", lambda d: {})
    monkeypatch.setattr(dlg, "set_option", lambda d, words, want, role: {
        "ok": False, "offered": ["Generate single PcbLib files per input "
                                 "file"]})
    rec = dlg.drive_one(1, r"C:\w\a.csv", [], dialog_timeout=1)
    assert rec["ok"] is False and rec["stage"] == "configure"
    assert "Start" not in screen.pressed
    assert screen.pressed == ["&Close"], (
        "an unstarted form is closed so the bridge comes back")
    assert rec["batch_dialog_open"] is False


def test_the_module_never_reaches_for_the_bridge():
    source = inspect.getsource(dlg)
    assert "send_command" not in source and "get_bridge" not in source, (
        "the bridge is blocked behind this dialog for the whole run")


def test_an_unreadable_option_is_a_miss_not_an_exception(monkeypatch):
    """snapshot() guarded describe_all; set_option called it again
    unguarded straight after, while the form blocked the bridge."""
    monkeypatch.setattr(dlg.controls, "available", lambda: True)

    def broken(dialog):
        raise OSError("accessibility server busy")

    monkeypatch.setattr(dlg.controls, "describe_all", broken)
    out = dlg.set_option(_form(), dlg.CURRENT_LIBRARY_WORDS, True,
                         role="radiobutton")
    assert out["ok"] is False and "could not be read" in out["reason"]


def test_a_set_that_raises_is_a_miss_not_an_exception(monkeypatch):
    monkeypatch.setattr(dlg.controls, "available", lambda: True)
    monkeypatch.setattr(dlg.controls, "describe_all", lambda d: [
        {"role": "radiobutton", "hwnd": 5,
         "label": "Generate all footprints in current PcbLib file"}])

    def broken(hwnd, want):
        raise RuntimeError("control destroyed")

    monkeypatch.setattr(dlg.controls, "set_checked", broken)
    out = dlg.set_option(_form(), dlg.CURRENT_LIBRARY_WORDS, True,
                         role="radiobutton")
    assert out["ok"] is False and "raised" in out["reason"]


def test_an_unreadable_destination_still_refuses_start(monkeypatch):
    """The miss feeds the existing configure stop, which closes the
    unstarted form so the bridge comes back."""
    form = _form()
    screen = Screen(monkeypatch, form)
    screen.on_press["&Close"] = lambda: screen.windows.pop(100)
    monkeypatch.setattr(dlg, "snapshot", lambda d: {})
    monkeypatch.setattr(dlg.controls, "available", lambda: True)

    def broken(dialog):
        raise OSError("accessibility server busy")

    monkeypatch.setattr(dlg.controls, "describe_all", broken)
    rec = dlg.drive_one(1, r"C:\w\a.csv", [], dialog_timeout=1)
    assert rec["stage"] == "configure" and "Start" not in screen.pressed
    assert rec["batch_dialog_open"] is False
