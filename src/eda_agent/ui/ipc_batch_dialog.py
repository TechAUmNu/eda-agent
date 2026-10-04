# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Drive Altium's IPC Compliant Footprints Batch generator dialog.

WIN32 ONLY, NO BRIDGE, and that is not a style choice. The dialog is
opened by a RunProcess inside a bridge handler, and that handler does not
return until the dialog closes, so for as long as anything here runs the
scripting engine answers nothing. The bridge work that brackets a run
(focus the library before, save, rename and reload after) lives in
tools/ipc_batch_flow.py. A guard test holds this module to no bridge call.

WHAT IS KNOWN ABOUT THE DIALOG, AND HOW.

MEASURED live (2026-10-02..04): the buttons 'Add Files' and 'Start'; Add
Files opens a common file dialog titled 'Open Package Data Files'; typing
the full path and Enter adds the file; on completion a report HTML is
written and opened as a document, which takes focus.

READ FROM THE EXTENSION'S OWN BINARY, not yet seen on screen (the
FootprintWizard.dll string table and form resources): form
Form_FootprintWizardBatch with Button_AddFiles, Button_RemoveFiles,
Button_StartStop whose caption toggles Start / Stop, and Button_Close; a
radio 'Generate all footprints in current PcbLib file' beside several
'Generate single PcbLib files per ...' alternatives (which explains the
stray ZZ_IPC_PILOT.PcbLib found in an Output folder); checkboxes
'Generate report on completion', 'Open generated report' and 'Open
generated PcbLib files on completion'; a directory edit 'Output Folder'.
Secondary forms: Form_AddFileMessages ('Add File Messages'),
Form_FootprintExists ('"<name>" already exists in the library'), and a
confirm 'One or more PcbLib files to be generated already exist, delete
the files prior to generating footprints?'.

Everything in the second group is used defensively: it is matched by
words rather than exact captions, read back after every change, and a
miss STOPS the run before Start with what was actually on screen, so one
live call corrects the constant instead of a run going wrong.

MEASURED 2026-10-04 (AD26, form class TForm_FootprintWizardBatch, title
'IPC(R) Footprints Batch generator'): the destination radio is NOT
captioned 'Generate all footprints in current PcbLib file' on this
build. It names the library: 'Generate all footprints in
H:\\git\\multiviewer\\hardware\\multiviewer\\carrier.PcbLib' (checked and
enabled), beside 'Generate single PcbLib files per footprint name' and
'... per input file'. The word match ('current' + 'pcblib') missed it
and the run stopped at 'configure', unstarted, which was the safe
outcome. set_destination now accepts that caption when, and only when,
the path it names IS the target library (normalised, case-insensitive),
which is a stronger check than the old words; the old caption is still
accepted; a caption naming any other library refuses Start.

NOTHING HERE PRESSES AN UNKNOWN BUTTON. The presses are Add Files, the
file dialog's OK, Start, and Close (before Start, or after the run has
finished). Any other dialog that appears stops the run and is reported,
untouched, because the two this extension is known to raise mid-run are
'already exists' and a delete confirmation, and neither is a question
automation should answer.
"""

from __future__ import annotations

import os
import time
from typing import Iterable, Optional

from ..libimport import ipc_batch as files
from . import controls, dialog_report
from . import windows

try:                                            # pragma: no cover - platform
    import win32con
    import win32gui
    _AVAILABLE = True
except ImportError:                             # pragma: no cover - platform
    _AVAILABLE = False

#: Title words of the batch form. The DLL carries ' Footprints Batch
#: generator' after a non-ASCII registered sign; the menu caption is
#: 'IPC Compliant Footprints Batch generator...'.
BATCH_TITLE_WORDS = ("batch generator",)

ADD_FILES = "Add Files"
START = "Start"
STOP = "Stop"
CLOSE = "Close"
CANCEL = "Cancel"
FILE_DIALOG_TITLE = "Open Package Data Files"

#: Option captions as WORDS, all of which must appear in the label.
CURRENT_LIBRARY_WORDS = ("current", "pcblib")      # the destination radio
#: The destination radio as this AD26 build captions it: the prefix, then
#: the full path of the library the run will write into.
DESTINATION_PREFIX = "generate all footprints in "
REPORT_ON_WORDS = ("report", "completion")         # Generate report on completion
OPEN_REPORT_WORDS = ("open", "report")             # Open generated report

#: Common file dialog control ids: the File name combo (cmb13) and, on the
#: pre-Vista layout, the File name edit (edt1); IDOK is the Open button.
_FILE_NAME_COMBO_ID = 0x47C
_FILE_NAME_EDIT_ID = 0x480
_IDOK = 1


def available() -> bool:
    return _AVAILABLE and windows.available()


def is_batch_dialog(window) -> bool:
    """The batch form, by title or by the buttons only it offers."""
    title = (window.title or "").lower()
    if any(word in title for word in BATCH_TITLE_WORDS):
        return True
    return (window.find_button(ADD_FILES) is not None
            and (window.find_button(START) is not None
                 or window.find_button(STOP) is not None))


def find_batch_dialog(pid: int):
    for dialog in windows.dialogs(pid):
        if is_batch_dialog(dialog):
            return dialog
    return None


def wait_for_batch_dialog(pid: int, timeout: float):
    found: dict = {}

    def appeared() -> bool:
        dialog = find_batch_dialog(pid)
        if dialog is not None:
            found["dialog"] = dialog
            return True
        return False

    windows.wait_until(appeared, timeout)
    return found.get("dialog")


def _looks_like_folder(text: str) -> bool:
    text = (text or "").strip()
    if len(text) < 3:
        return False
    if not (text[1:3] in (":\\", ":/") or text.startswith("\\\\")):
        return False
    return os.path.isdir(text)


def _looks_like_input_file(text: str) -> bool:
    low = (text or "").strip().lower()
    return low.endswith((".csv", ".xls", ".xlsx")) or "\\" in low


def _option(described: list, words: Iterable[str], role: str):
    for info in described:
        if info.get("role") != role:
            continue
        text = f"{info.get('label') or ''} {info.get('name') or ''}".lower()
        if all(word in text for word in words):
            return info
    return None


def snapshot(dialog) -> dict:
    """What the batch form shows, read without changing anything."""
    texts = windows.window_text_dump(dialog, limit=120)
    out: dict = {
        "title": dialog.title, "class": dialog.class_name,
        "buttons": [{"caption": b.text, "enabled": b.enabled}
                    for b in dialog.buttons()],
        "texts": texts,
        "output_folder_candidates": [t for t in texts
                                     if _looks_like_folder(t)],
    }
    if not controls.available():
        out["options_note"] = "accessibility unavailable; options unread"
        return out
    try:
        described = controls.describe_all(dialog)
    except Exception as exc:                     # noqa: BLE001
        out["options_error"] = str(exc)
        return out
    out["options"] = [
        {"label": c.get("label") or c.get("name"), "role": c.get("role"),
         "checked": c.get("checked"), "enabled": c.get("enabled")}
        for c in described if c.get("role") in ("checkbox", "radiobutton")]
    lists = [c for c in described
             if c.get("role") in ("list", "outline", "table")]
    rows: list = []
    for info in lists:
        try:
            rows.extend(controls.list_items(info["hwnd"]))
        except Exception:                        # noqa: BLE001
            pass
    out["queue_readable"] = bool(lists)
    out["queued_rows"] = rows
    return out


def set_option(dialog, words: Iterable[str], want: bool, role: str) -> dict:
    """Set one checkbox or radio found by caption words, and read it back."""
    words = tuple(words)
    if not controls.available():
        return {"ok": False, "words": list(words),
                "reason": "reading dialog options needs comtypes"}
    # Both calls guarded, as snapshot() guards describe_all: they are known
    # to raise, and this runs while the form blocks the bridge, so an
    # escape here would end the run with no stage and the form left up.
    # A failure is a miss, and a miss on the destination refuses Start.
    try:
        described = controls.describe_all(dialog)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "words": list(words), "role": role,
                "reason": f"the dialog's options could not be read: {exc}"}
    found = _option(described, words, role)
    if found is None:
        return {"ok": False, "words": list(words), "role": role,
                "reason": (f"no {role} whose caption contains "
                           f"{' + '.join(words)}"),
                "offered": [c.get("label") or c.get("name")
                            for c in described
                            if c.get("role") in ("checkbox", "radiobutton")]}
    label = found.get("label") or found.get("name")
    try:
        result = controls.set_checked(found["hwnd"], bool(want))
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "words": list(words), "role": role,
                "label": label,
                "reason": f"setting {label!r} raised: {exc}"}
    result["label"] = label
    return result


def _same_path(a: str, b: str) -> bool:
    """Two library paths name the same file: normalised, case-insensitive.

    ``os.path.normcase`` lowercases only on Windows, so the case fold is
    explicit: Altium's paths are Windows paths whatever runs the tests.
    """
    def norm(p: str) -> str:
        p = (p or "").strip().strip('"').rstrip(".").strip()
        return os.path.normpath(p.replace("/", "\\")).replace(
            "/", "\\").lower()
    return bool(a and b) and norm(a) == norm(b)


def destination_choice(described: list, library_path: str) -> dict:
    """Pick the destination radio from the options on screen, or refuse.

    Accepted, in this order:

    * ``Generate all footprints in <path>`` whose ``<path>`` is
      ``library_path`` (``match: "path"``): the caption this AD26 build
      shows, and the stronger check, because it names the file the run
      will write into;
    * ``Generate all footprints in current PcbLib file`` (``match:
      "current_words"``), the caption the extension's string table
      carries.

    REFUSED (``ok`` False, ``found`` None) when a ``Generate all
    footprints in <path>`` radio names a DIFFERENT library: starting would
    write somewhere other than the library this call was given. Also
    refused when nothing matches. Pure: no window is touched here.
    """
    radios = [c for c in described if c.get("role") == "radiobutton"]
    offered = [c.get("label") or c.get("name") for c in described
               if c.get("role") in ("checkbox", "radiobutton")]
    wrong: list = []
    legacy: list = []
    for info in radios:
        label = (info.get("label") or info.get("name") or "").strip()
        named = _destination_path(label)
        if named is None:
            # Not a path caption: the old caption, by its words, or
            # another option ('Generate single PcbLib files per ...').
            if all(word in label.lower() for word in CURRENT_LIBRARY_WORDS):
                legacy.append((info, label))
            continue
        if _same_path(named, library_path):
            return {"ok": True, "found": info, "match": "path",
                    "label": label, "named_path": named}
        wrong.append(named)
    if wrong:
        return {"ok": False, "found": None, "offered": offered,
                "named_paths": wrong, "reason": (
                    f"the destination radio names {wrong[0]!r}, not the "
                    f"target library {library_path!r}; Start would write "
                    f"into a different library")}
    if legacy:
        info, label = legacy[0]
        return {"ok": True, "found": info, "match": "current_words",
                "label": label}
    return {"ok": False, "found": None, "offered": offered, "reason": (
        "no radiobutton reads 'Generate all footprints in current PcbLib "
        "file' or 'Generate all footprints in <the target library>'")}


def _destination_path(label: str) -> Optional[str]:
    """The library a 'Generate all footprints in <path>' caption names.

    None for any other caption, including the old 'Generate all
    footprints in current PcbLib file', which names no path.
    """
    low = (label or "").strip().lower()
    if not low.startswith(DESTINATION_PREFIX):
        return None
    named = label.strip()[len(DESTINATION_PREFIX):].strip()
    if not named or named.lower().startswith("current pcblib"):
        return None
    return named


def set_destination(dialog, library_path: str) -> dict:
    """Select the destination radio (see destination_choice) and read it back.

    Same guards as set_option: every accessibility call is caught, and a
    failure is a miss, which refuses Start.
    """
    if not controls.available():
        return {"ok": False, "reason": "reading dialog options needs comtypes"}
    try:
        described = controls.describe_all(dialog)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "reason": (
            f"the dialog's options could not be read: {exc}")}
    choice = destination_choice(described, library_path)
    if not choice.get("ok"):
        return {k: v for k, v in choice.items() if k != "found"}
    found = choice["found"]
    label = choice.get("label")
    try:
        result = controls.set_checked(found["hwnd"], True)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "label": label, "match": choice.get("match"),
                "reason": f"setting {label!r} raised: {exc}"}
    result["label"] = label
    result["match"] = choice.get("match")
    if choice.get("named_path"):
        result["named_path"] = choice["named_path"]
    return result


def _press(window, caption: str) -> dict:
    button = window.find_button(caption) if window is not None else None
    if button is None:
        return {"ok": False, "reason": f"no {caption!r} button",
                "offered": [b.text for b in window.buttons()] if window else []}
    if not button.enabled:
        return {"ok": False, "reason": f"{caption!r} is disabled"}
    windows.click(button)
    return {"ok": True, "pressed": button.text}


def close_form(dialog) -> dict:
    """Close the batch form: before Start, or after a run has finished.

    Safe in exactly those two states. Before Start nothing has been
    generated; after the run nothing is in progress. Closing is what lets
    the RunProcess that opened the form return, so the bridge comes back
    instead of waiting on a form nobody will finish. Never called while a
    run is in progress or while another dialog sits on top of the form.
    """
    current = windows.capture(dialog.hwnd)
    if current is None:
        return {"ok": True, "closed": True, "note": "already gone"}
    pressed = _press(current, CLOSE)
    if not pressed["ok"]:
        pressed = _press(current, CANCEL)
    closed = windows.wait_for_close(dialog.hwnd, timeout=5.0)
    return {"ok": closed, "closed": closed, "press": pressed}


def _file_name_edit(file_dialog) -> Optional[int]:
    """The File name edit of a common file dialog, found by control id."""
    combo = edit = None
    for control in file_dialog.controls:
        try:
            cid = win32gui.GetDlgCtrlID(control.hwnd)
        except Exception:                        # noqa: BLE001
            continue
        if (cid == _FILE_NAME_COMBO_ID and combo is None
                and control.class_name in ("ComboBoxEx32", "ComboBox")):
            combo = control.hwnd
        elif (cid == _FILE_NAME_EDIT_ID and edit is None
              and control.class_name == "Edit"):
            edit = control.hwnd
    if combo is not None:
        try:
            inner = combo
            if win32gui.GetClassName(combo) == "ComboBoxEx32":
                inner = win32gui.FindWindowEx(combo, 0, "ComboBox", None) or combo
            found = win32gui.FindWindowEx(inner, 0, "Edit", None)
            if found:
                return int(found)
        except Exception:                        # noqa: BLE001
            pass
    return edit


def _file_name_by_handle(file_dialog, path: str) -> dict:
    """Route A: WM_SETTEXT into the File name edit, then post IDOK.

    Addressed to handles, so it needs no foreground and moves nothing.
    IDOK is POSTED to the dialog rather than sent to the button: BM_CLICK
    is documented to fail on an inactive dialog, and a SENT press would
    block this thread on any message box the dialog raised while handling
    it.
    """
    edit = _file_name_edit(file_dialog)
    if not edit:
        return {"ok": False, "route": "handle",
                "reason": "no File name edit (control 0x47C / 0x480) found"}
    try:
        win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, path)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "route": "handle",
                "reason": f"WM_SETTEXT failed: {exc}"}
    got = windows.read_text(edit)
    if os.path.normcase(got.strip()) != os.path.normcase(path):
        return {"ok": False, "route": "handle", "readback": got,
                "reason": "the File name edit did not take the path"}
    try:
        win32gui.PostMessage(file_dialog.hwnd, win32con.WM_COMMAND,
                             _IDOK, 0)
    except Exception as exc:                     # noqa: BLE001
        return {"ok": False, "route": "handle", "readback": got,
                "reason": f"posting IDOK failed: {exc}"}
    return {"ok": True, "route": "handle", "readback": got}


def add_file(pid: int, dialog, csv_path: str, timeout: float,
             may_steal_focus: bool) -> dict:
    """Press Add Files and hand the file dialog one full path."""
    current = windows.capture(dialog.hwnd)
    pressed = _press(current, ADD_FILES)
    if not pressed["ok"]:
        return {"ok": False, "stage": "add_files", **pressed}

    seen = windows.wait_for_dialog(pid, FILE_DIALOG_TITLE, timeout)
    if not seen.get("ok"):
        return {"ok": False, "stage": "file_dialog",
                "reason": seen.get("reason"),
                "dialogs": dialog_report.report(pid)}
    file_dialog = windows.capture(seen["hwnd"])
    if file_dialog is None:
        return {"ok": False, "stage": "file_dialog",
                "reason": "the file dialog vanished before it was read"}

    route = _file_name_by_handle(file_dialog, csv_path)
    closed = route["ok"] and windows.wait_for_close(file_dialog.hwnd, 5.0)
    if not closed:
        if not may_steal_focus:
            _press(windows.capture(file_dialog.hwnd), CANCEL)
            return {"ok": False, "stage": "file_name", "handle_route": route,
                    "reason": ("the handle route did not add the file and "
                               "may_steal_focus is False, so the keyboard "
                               "route was not tried; the file dialog was "
                               "cancelled")}
        # Route B, MEASURED live: type the full path and press Enter. If
        # route A put text in the field, empty it first: typing goes in at
        # the caret and would append to it. The key table has no letters,
        # so select-all is not available to do the same.
        edit = _file_name_edit(file_dialog)
        if edit and route.get("readback"):
            try:
                win32gui.SendMessage(edit, win32con.WM_SETTEXT, 0, "")
            except Exception:                    # noqa: BLE001
                pass
        try:
            windows.type_text(csv_path, target=file_dialog.hwnd)
            typed = windows.read_text(edit) if edit else None
            windows.send_keys("enter", target=file_dialog.hwnd)
        except (windows.ForegroundLost, windows.AutomationDisabled) as exc:
            return {"ok": False, "stage": "file_name", "handle_route": route,
                    "reason": f"keyboard route refused: {exc}"}
        route = {"ok": True, "route": "keyboard", "typed_readback": typed,
                 "handle_route": route}
        closed = windows.wait_for_close(file_dialog.hwnd, timeout=10.0)
    if not closed:
        still = windows.capture(file_dialog.hwnd)
        return {"ok": False, "stage": "file_name", "route": route,
                "reason": "the file dialog is still open after the path",
                "file_dialog": (dialog_report.describe_dialog(still)
                                if still else None)}
    return {"ok": True, "pressed": pressed["pressed"], "route": route}


def new_dialogs(pid: int, known: set) -> list:
    return [d for d in windows.dialogs(pid) if d.hwnd not in known]


def watch_for_dialog(pid: int, known: set, seconds: float) -> list:
    """Anything that pops up within ``seconds``, described."""
    deadline = time.monotonic() + seconds
    while True:
        extra = new_dialogs(pid, known)
        if extra:
            return [dialog_report.describe_dialog(d) for d in extra]
        if time.monotonic() >= deadline:
            return []
        time.sleep(0.1)


def start_and_wait(pid: int, dialog, report_dirs: list, csv_path: str,
                   timeout: float, quiet_after: float = 8.0,
                   poll: float = 0.25) -> dict:
    """Press Start, wait until the run is over, close the form.

    "Over" needs EVIDENCE, because every measured run took under a second
    (Elapsed Time 00:00:00 in all 13 reports) and the caption may never
    be seen reading Stop. Evidence is the report for THIS csv appearing,
    or Stop having been seen and Start back. With neither, Start enabled
    for ``quiet_after`` seconds ends the wait and says the evidence was
    weak; the library read afterwards is then the only proof.
    """
    current = windows.capture(dialog.hwnd)
    started_at = time.time()
    pressed = _press(current, START)
    if not pressed["ok"]:
        return {"ok": False, "stage": "start", **pressed}
    began = time.monotonic()
    out: dict = {"ok": False, "started_at": started_at, "seen_stop": False,
                 "report": None, "finished_by": None, "closed_by_us": False}
    try:
        out["main_window_blocked"] = windows.is_blocked(pid)
    except Exception:                            # noqa: BLE001
        out["main_window_blocked"] = None

    deadline = began + timeout
    while time.monotonic() < deadline:
        for extra in new_dialogs(pid, {dialog.hwnd}):
            described = dialog_report.describe_dialog(extra)
            if described.get("kind") == "progress":
                continue
            out.update(stage="run", dialog=described, reason=(
                "a dialog appeared during the run and was left for a human: "
                "the ones this generator raises mid-run are 'already exists' "
                "and a delete confirmation"))
            return out
        if out["report"] is None:
            out["report"] = files.report_for(
                csv_path, files.find_reports(report_dirs, since=started_at))
        current = windows.capture(dialog.hwnd)
        if current is None:
            out["finished_by"] = "dialog_closed"
            break
        if current.find_button(STOP) is not None:
            out["seen_stop"] = True
        start = current.find_button(START)
        if start is not None and start.enabled:
            if out["seen_stop"] or out["report"] is not None:
                out["finished_by"] = "start_enabled_after_run"
                break
            if time.monotonic() - began >= quiet_after:
                out["finished_by"] = "start_enabled_no_evidence"
                break
        time.sleep(poll)
    else:
        out.update(stage="run", reason=(
            f"the run did not finish within {timeout:g}s"),
            dialogs=dialog_report.report(pid))
        return out

    if out["finished_by"] != "dialog_closed":
        closing = close_form(dialog)       # the run is over
        out["close"] = closing
        out["closed_by_us"] = closing.get("closed", False)
        if not closing.get("closed"):
            out.update(stage="close", reason=(
                "the run finished but the batch form would not close, so "
                "the bridge is still blocked behind it"),
                dialogs=dialog_report.report(pid))
            return out

    settle = time.monotonic() + 5.0
    while out["report"] is None and time.monotonic() < settle:
        out["report"] = files.report_for(
            csv_path, files.find_reports(report_dirs, since=started_at))
        if out["report"] is None:
            time.sleep(0.25)
    out["elapsed_s"] = round(time.monotonic() - began, 2)
    out["ok"] = True
    return out


def drive_one(pid: int, csv_path: str, report_dirs: list, *,
              dialog_timeout: float = 30.0, run_timeout: float = 180.0,
              open_report: Optional[bool] = None,
              may_steal_focus: bool = True,
              library_path: str = "") -> dict:
    """Everything between the dialog opening and it closing, for one CSV.

    Returns ``ok`` and, on a stop, ``stage``, ``reason`` and what was on
    screen. ``batch_dialog_open`` says whether the form is still up, which
    is what decides whether the bridge can be asked anything next.
    """
    rec: dict = {"csv": csv_path, "ok": False, "batch_dialog_open": False}
    dialog = wait_for_batch_dialog(pid, dialog_timeout)
    if dialog is None:
        rec.update(stage="open", reason=(
            f"no batch generator dialog appeared within {dialog_timeout:g}s"),
            dialogs=dialog_report.report(pid))
        return rec
    rec["batch_dialog_open"] = True
    rec["dialog_title"] = dialog.title
    rec["before"] = snapshot(dialog)

    def stop_unstarted(stage: str, reason: str, **more) -> dict:
        rec.update(stage=stage, reason=reason, **more)
        closing = close_form(dialog)
        rec["closed_unstarted"] = closing
        rec["batch_dialog_open"] = not closing.get("closed", False)
        return rec

    destination = set_destination(dialog, library_path)
    rec["destination"] = destination
    if not destination.get("ok"):
        return stop_unstarted("configure", (
            "could not confirm that the run writes into the target library "
            f"({destination.get('reason')}). Refusing to Start: the other "
            "choices write separate PcbLib files into the Output Folder, "
            "or into a different library"))
    rec["report_option"] = set_option(dialog, REPORT_ON_WORDS, True,
                                      role="checkbox")
    if open_report is not None:
        rec["open_report_option"] = set_option(
            dialog, OPEN_REPORT_WORDS, bool(open_report), role="checkbox")

    added = add_file(pid, dialog, csv_path, dialog_timeout, may_steal_focus)
    rec["add_file"] = added
    if not added.get("ok"):
        if added.get("stage") == "file_name" and windows.capture(
                dialog.hwnd) is not None and not new_dialogs(
                    pid, {dialog.hwnd}):
            return stop_unstarted("add_file", added.get("reason") or "")
        rec.update(stage="add_file", reason=added.get("reason"))
        return rec

    popped = watch_for_dialog(pid, {dialog.hwnd}, 1.5)
    if popped:
        rec.update(stage="add_file", dialogs_after_add=popped, reason=(
            "a dialog appeared after the file was added (the generator "
            "has an 'Add File Messages' form); left for a human"))
        return rec

    fresh = windows.capture(dialog.hwnd)
    queued = snapshot(fresh) if fresh is not None else {}
    rec["queued"] = {k: queued.get(k) for k in
                     ("buttons", "queue_readable", "queued_rows")}
    # Only rows that look like FILES count: a grid can expose a header or
    # a status line as a row, and treating that as a queued file would
    # stop a good run.
    base = os.path.basename(csv_path).lower()
    files_listed = [r for r in (queued.get("queued_rows") or [])
                    if _looks_like_input_file(r)]
    rec["queued"]["file_rows"] = files_listed
    if files_listed:
        foreign = [r for r in files_listed if base not in r.lower()]
        mine = [r for r in files_listed if base in r.lower()]
        if foreign or not mine:
            return stop_unstarted("queue", (
                "the batch list does not hold exactly this file, so Start "
                "would generate something else as well or instead"),
                foreign_rows=foreign)

    dirs = list(report_dirs) + (rec["before"].get("output_folder_candidates")
                                or [])
    rec["report_dirs"] = dirs
    run = start_and_wait(pid, fresh or dialog, dirs, csv_path, run_timeout)
    rec["run"] = run
    rec["batch_dialog_open"] = windows.capture(dialog.hwnd) is not None
    if not run.get("ok"):
        rec.update(stage=run.get("stage", "run"), reason=run.get("reason"))
        return rec
    rec["report"] = run.get("report")
    rec["ok"] = True
    return rec
