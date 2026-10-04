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


# ---------------------------------------------------------------------------
# The destination radio as this AD26 build captions it (measured 2026-10-04):
# 'Generate all footprints in <full path of the library>', not 'current
# PcbLib file'. The word match missed it and the run stopped unstarted.

_TARGET = r"H:\git\multiviewer\hardware\multiviewer\carrier.PcbLib"

_MEASURED_OPTIONS = [
    ("Produce STEP model", "checkbox"),
    ("Generate all footprints in " + _TARGET, "radiobutton"),
    ("Open generated PcbLib files on completion", "checkbox"),
    ("Open generated report", "checkbox"),
    ("Generate report on completion", "checkbox"),
    ("Generate single PcbLib files per footprint name", "radiobutton"),
    ("Generate single PcbLib files per input file", "radiobutton"),
]


def _described(options):
    return [{"label": label, "role": role, "hwnd": 500 + i}
            for i, (label, role) in enumerate(options)]


@pytest.mark.parametrize("target", [
    _TARGET,
    _TARGET.lower(),
    _TARGET.replace("\\", "/"),
    "  " + _TARGET + "  ",
])
def test_the_path_caption_is_accepted_when_it_names_the_target(target):
    out = dlg.destination_choice(_described(_MEASURED_OPTIONS), target)
    assert out["ok"] is True and out["match"] == "path"
    assert out["found"]["hwnd"] == 501
    assert out["named_path"] == _TARGET


def test_a_caption_naming_another_library_refuses_start():
    """Mutation: accept any 'Generate all footprints in ...' radio."""
    out = dlg.destination_choice(_described(_MEASURED_OPTIONS),
                                 r"H:\git\other\parts.PcbLib")
    assert out["ok"] is False and out["found"] is None
    assert "different library" in out["reason"]
    assert out["named_paths"] == [_TARGET]


def test_a_path_holding_the_old_words_is_still_judged_by_its_path():
    """Mutation: let the 'current' + 'pcblib' word match win first.

    A library under a folder called 'current' contains both words, and
    must still be refused when it is not the target.
    """
    options = [(r"Generate all footprints in C:\current\x.PcbLib",
                "radiobutton")]
    out = dlg.destination_choice(_described(options), _TARGET)
    assert out["ok"] is False


def test_the_old_caption_is_still_accepted():
    options = [("Generate all footprints in current PcbLib file",
                "radiobutton"),
               ("Generate single PcbLib files per input file", "radiobutton")]
    out = dlg.destination_choice(_described(options), _TARGET)
    assert out["ok"] is True and out["match"] == "current_words"
    assert out["found"]["hwnd"] == 500


def test_no_destination_radio_refuses_start():
    options = [("Generate single PcbLib files per input file", "radiobutton")]
    out = dlg.destination_choice(_described(options), _TARGET)
    assert out["ok"] is False and out["offered"]


def test_set_destination_checks_the_measured_radio(monkeypatch):
    monkeypatch.setattr(dlg.controls, "available", lambda: True)
    monkeypatch.setattr(dlg.controls, "describe_all",
                        lambda d: _described(_MEASURED_OPTIONS))
    seen = []

    def set_checked(hwnd, want):
        seen.append((hwnd, want))
        return {"ok": True, "checked": True}

    monkeypatch.setattr(dlg.controls, "set_checked", set_checked)
    out = dlg.set_destination(_form(), _TARGET)
    assert out["ok"] is True and out["match"] == "path"
    assert seen == [(501, True)]


def test_drive_one_refuses_start_for_a_foreign_destination(monkeypatch):
    """The whole stop path: the form names another library, so the run
    stops at 'configure', closes the unstarted form, and never presses
    Start."""
    form = _form()
    screen = Screen(monkeypatch, form)
    screen.on_press["&Close"] = lambda: screen.windows.pop(100)
    monkeypatch.setattr(dlg, "snapshot", lambda d: {})
    monkeypatch.setattr(dlg.controls, "available", lambda: True)
    monkeypatch.setattr(dlg.controls, "describe_all",
                        lambda d: _described(_MEASURED_OPTIONS))
    monkeypatch.setattr(dlg.controls, "set_checked",
                        lambda hwnd, want: {"ok": True})
    rec = dlg.drive_one(1, r"C:\w\a.csv", [], dialog_timeout=1,
                        library_path=r"H:\elsewhere\other.PcbLib")
    assert rec["stage"] == "configure" and "Start" not in screen.pressed
    assert "different library" in rec["reason"]
    assert rec["batch_dialog_open"] is False


def test_the_flow_hands_the_target_library_to_the_dialog_driver():
    import inspect
    from eda_agent.tools import ipc_batch_flow as flow

    source = inspect.getsource(flow.run_batch)
    assert "library_path=library_path" in source, (
        "drive_one must be told the target library, or the path caption "
        "can never be confirmed")


def test_a_relative_target_is_compared_as_an_absolute_path(monkeypatch,
                                                           tmp_path):
    """Mutation: compare without os.path.abspath. A relative library_path
    used to be refused as 'names a different library'."""
    lib = tmp_path / "lib" / "carrier.PcbLib"
    lib.parent.mkdir()
    monkeypatch.chdir(tmp_path)
    options = [("Generate all footprints in " + str(lib), "radiobutton")]
    out = dlg.destination_choice(_described(options),
                                 str(lib.relative_to(tmp_path)))
    assert out["ok"] is True and out["match"] == "path"


@pytest.mark.parametrize("elided", [
    r"H:\git\...\carrier.PcbLib",
    r"H:\git\multiviewer" + chr(0x2026) + "carrier.PcbLib",
])
def test_an_elided_caption_is_refused_as_unconfirmable(elided):
    """Mutation: refuse it as 'a different library' (misleading) or accept
    it. A shortened path cannot be confirmed either way."""
    options = [("Generate all footprints in " + elided, "radiobutton")]
    out = dlg.destination_choice(_described(options), _TARGET)
    assert out["ok"] is False and out["found"] is None
    assert "elided" in out["reason"]
    assert "different library" not in out["reason"]


def test_the_flow_and_the_dialog_share_one_path_rule():
    """Mutation: keep two _same_path helpers with different rules."""
    from eda_agent.tools import ipc_batch_flow as flow

    assert flow._same_path(_TARGET.lower(), _TARGET.replace("\\", "/"))
    assert dlg.same_library_path(_TARGET, _TARGET + ".")
    assert not flow._same_path("", _TARGET)
    assert not dlg.same_library_path(_TARGET, r"H:\other\carrier.PcbLib")
