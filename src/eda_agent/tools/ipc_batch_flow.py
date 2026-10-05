# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Run Altium's IPC footprint batch generator over CSVs, end to end.

What ``lib_ipc_batch_generate`` does, kept out of library.py so the steps
can be tested against a fake bridge and fake dialog driver.

THE MANUAL SEQUENCE THIS REPLACES was about ten calls per CSV, recorded
live 2026-10-02..04: focus the PcbLib; obj_run_process
PCB:BatchPCBLibCreate, which "fails" with a modal-dialog error precisely
because the dialog came up; press Add Files; wait for 'Open Package Data
Files'; type the path and Enter (setting the File name control wrote the
LABEL); press Start; refocus the library, because the report opened as a
document and took focus; rename every name the generator wrote with ','
for '.'; reload before any edit, because a fresh footprint ignores
primitive deletes until then. Each step was a chance to act on the wrong
document, and the renames were done from memory of which names had dots.

THREE PHASES PER CSV, and the split is load-bearing. The batch form is
opened by a RunProcess inside a bridge handler, and that handler does not
return until the form closes, so while it is up the bridge answers
nothing:

* BRIDGE, before: refocus the library (the previous report took focus),
  read back that it is focused, then fire the process WITHOUT waiting
  (``send_command_detached``).
* WIN32 ONLY, during: ``ui.ipc_batch_dialog.drive_one``. No bridge call
  can be made here, and none is.
* BRIDGE, after: collect the fired request's reply, which is the proof
  RunProcess returned and the engine is free; close the report document.

Then, ONCE for the whole batch: save, reload, rename the comma names back
(only names this call created), save, reload, and read the names back
from the reopened file, which is the only read that proves the result is
on disk rather than in the editor.
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
from pathlib import Path
from typing import Optional

from ..atomicfile import replace_with_retry
from ..bridge.altium_bridge import PROTOCOL_VERSION
from ..libimport import ipc_batch as files
from ..ui import controls, dialog_report, ipc_batch_dialog

#: The extension's batch command (FootprintWizard.rcs: Command=
#: 'PCB:BatchPCBLibCreate', Caption 'IPC Compliant Footprints Batch
#: generator...', under the PcbLib editor's Tools menu).
BATCH_PROCESS = "PCB:BatchPCBLibCreate"

#: Remembers where the last report was found, because the form's Output
#: Folder is sticky and is NOT beside the CSV (see libimport.ipc_batch).
_STATE_FILE = "ipc_batch_state.json"


def _same_path(a: object, b: object) -> bool:
    """One path rule for the whole batch flow: the dialog module's."""
    return ipc_batch_dialog.same_library_path(a, b)


def _failure(command: str, exc: BaseException) -> dict:
    return {"command": command, "error": str(exc),
            "code": getattr(exc, "code", type(exc).__name__),
            "details": getattr(exc, "details", None)}


@controls.in_com_apartment
def _screen_report(pid: int) -> dict:
    """``dialog_report.report``, which never raises here.

    Called on the way OUT of a stop, so an exception from it (an OCR
    failure, a window that vanished mid-read) would replace the stop
    record with a bare traceback, which is the one thing a stop exists
    to prevent.

    Runs in an executor, so in com_apartment like every such entry: the
    report reads Win32 and OCR today, and a reader added later that
    reaches MSAA or UIA must not come back empty on a worker thread.
    """
    try:
        return dialog_report.report(pid)
    except Exception as exc:                     # noqa: BLE001
        return {"error": f"the screen could not be read: {exc}"}


def _form_still_open(pid: int) -> bool:
    """Whether the batch form is still up. True when that cannot be read:
    a bridge call made behind an open form only sits until it times out,
    so "unknown" is answered the safe way."""
    try:
        return ipc_batch_dialog.find_batch_dialog(pid) is not None
    except Exception:                            # noqa: BLE001
        return True


@controls.in_com_apartment
def _drive_one_reporting(pid: int, csv_path: str, dirs: list,
                         **kwargs) -> dict:
    """``drive_one``, with any exception turned into a stop record.

    drive_one runs in an executor while RunProcess is blocked behind the
    form it drives, so an exception escaping it (a disabled-button race in
    windows.click, an accessibility read failing, OCR in a dialog report)
    used to end the whole call as a traceback with the form still up, the
    bridge still blocked and nothing saying so.

    In an executor is also why this enters com_apartment: on a worker
    without COM the form's options read as an empty list (MEASURED
    2026-10-05, see controls.py). drive_one carries the same guard; nested,
    the inner one is a no-op.
    """
    try:
        return ipc_batch_dialog.drive_one(pid, csv_path, dirs, **kwargs)
    except Exception as exc:                     # noqa: BLE001
        return {"csv": csv_path, "ok": False, "stage": "dialog",
                "reason": (f"the dialog driver raised "
                           f"{type(exc).__name__}: {exc}"),
                "error": _failure("ipc_batch_dialog.drive_one", exc),
                "batch_dialog_open": _form_still_open(pid)}


def _reply_record(reply) -> dict:
    """What the RunProcess handler answered, kept rather than discarded.

    Before, only ``reply is not None`` was looked at, so a handler error
    (unknown process, a refusal) surfaced as a run that stopped at 'open'
    with nothing saying why no form appeared.
    """
    rec: dict = {"success": getattr(reply, "success", None),
                 "error": getattr(reply, "error", None),
                 "data": getattr(reply, "data", None)}
    version = getattr(reply, "protocol_version", 0)
    if version and version != PROTOCOL_VERSION:
        rec["protocol_mismatch"] = {"client": PROTOCOL_VERSION,
                                    "server": version}
    return rec


def _reply_problem(rec: dict) -> str:
    """The handler's own words when it did not succeed, else ""."""
    parts = []
    if rec.get("success") is False:
        err = rec.get("error") if isinstance(rec.get("error"), dict) else {}
        code = err.get("code") or "an error"
        message = err.get("message") or ""
        parts.append(f"RunProcess answered {code}"
                     + (f": {message}" if message else ""))
    if rec.get("protocol_mismatch"):
        mm = rec["protocol_mismatch"]
        parts.append(f"the Altium script speaks protocol {mm['server']}, "
                     f"this server {mm['client']}; reload the script")
    return "; ".join(parts)


def _load_state(workspace_dir: Path) -> dict:
    try:
        return json.loads((workspace_dir / _STATE_FILE).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(workspace_dir: Path, state: dict) -> None:
    path = workspace_dir / _STATE_FILE
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(state, indent=1), encoding="utf-8")
        replace_with_retry(tmp, path)
    except OSError:
        pass


async def _footprint_names(bridge, library_path: str):
    """(set of names, None) or (None, failure)."""
    try:
        reply = await bridge.send_command_async(
            "library.get_footprints", {"library_path": library_path})
    except Exception as exc:                     # noqa: BLE001
        return None, _failure("library.get_footprints", exc)
    names = {str(fp.get("name") or "") for fp in
             (reply or {}).get("footprints", []) if isinstance(fp, dict)}
    names.discard("")
    return names, None


async def _focus_library(bridge, library_path: str) -> dict:
    """Make the library the focused document, and READ BACK that it is.

    The batch generator writes into whatever PcbLib is current, and the
    report from the previous run took focus, so this runs before every
    run rather than once.
    """
    out: dict = {"library_path": library_path}
    for attempt in (1, 2):
        try:
            await bridge.send_command_async(
                "application.set_active_document",
                {"file_path": library_path})
            break
        except Exception as exc:                 # noqa: BLE001
            out["set_error"] = _failure(
                "application.set_active_document", exc)
            if attempt == 2:
                out["ok"] = False
                return out
            # NOT_LOADED: get_footprints opens a library that is not open.
            await _footprint_names(bridge, library_path)
    try:
        active = await bridge.send_command_async(
            "application.get_active_document")
    except Exception as exc:                     # noqa: BLE001
        out.update(ok=False, read_error=_failure(
            "application.get_active_document", exc))
        return out
    focused = (active or {}).get("file_path", "")
    out["active_document"] = focused
    out["ok"] = _same_path(focused, library_path)
    return out


async def _close_report(bridge, report_path: str) -> dict:
    try:
        reply = await bridge.send_command_async(
            "application.close_document", {"file_path": report_path},
            timeout=30.0)
    except Exception as exc:                     # noqa: BLE001
        return {"closed": False, **_failure("application.close_document",
                                            exc)}
    return {"closed": bool((reply or {}).get("closed")), "reply": reply}


def preflight(csv_paths: list, library_path: str) -> dict:
    """Everything decidable without Altium. ``ok`` False refuses the run."""
    findings: list = []
    plans: list = []
    if not csv_paths:
        findings.append({"problem": "no_csv", "severity": "error",
                         "why": "csv_paths is empty"})
    lib = str(library_path or "")
    if not lib.lower().endswith(".pcblib"):
        findings.append({"problem": "not_a_pcblib", "severity": "error",
                         "why": f"{lib!r} is not a .PcbLib"})
    elif not os.path.isabs(lib) or not os.path.isfile(lib):
        findings.append({"problem": "library_missing", "severity": "error",
                         "why": (f"{lib!r} must be the full path of a "
                                 f"PcbLib already saved on disk: the run "
                                 f"ends with a reload from that file")})
    owner: dict = {}
    seen_paths: dict = {}
    for raw in csv_paths or []:
        path = str(raw)
        if not os.path.isabs(path):
            findings.append({"csv": path, "problem": "relative_path",
                             "severity": "error",
                             "why": ("give the full path: it is typed into "
                                     "Altium's file dialog, which resolves "
                                     "a relative name against its own "
                                     "folder, not this server's")})
            continue
        # The same file twice: the duplicate-name check below compares
        # names across DIFFERENT files, and the already-present check only
        # sees the library as it was before the call, so the second run
        # would regenerate the first run's names and stop mid-run on the
        # generator's 'already exists' prompt, with the bridge blocked.
        key = os.path.normcase(os.path.abspath(path))
        if key in seen_paths:
            findings.append({"csv": path, "problem": "duplicate_csv",
                             "severity": "error",
                             "why": (f"listed more than once (also as "
                                     f"{seen_paths[key]!r}); a second run "
                                     f"would stop on the generator's "
                                     f"'already exists' prompt")})
            continue
        seen_paths[key] = path
        plan = files.parse_batch_csv(path)
        plans.append(plan)
        findings.extend(plan["findings"])
        for name in plan["names"]:
            if name in owner and owner[name] != path:
                findings.append({"csv": path, "problem": "duplicate_across",
                                 "severity": "error", "value": name,
                                 "why": f"also produced by {owner[name]}"})
            owner.setdefault(name, path)
    expected = list(owner)
    return {"ok": not any(f["severity"] == "error" for f in findings),
            "findings": findings, "csvs": plans, "expected_names": expected}


async def run_batch(bridge, pid: int, csv_paths: list, library_path: str, *,
                    report_dir: str = "", allow_existing: bool = False,
                    strict_preflight: bool = True,
                    finalize_on_error: bool = False,
                    close_reports: bool = True,
                    open_report: Optional[bool] = None,
                    may_steal_focus: bool = True,
                    preflight_only: bool = False,
                    dialog_timeout: float = 30.0,
                    run_timeout: float = 180.0,
                    engine_timeout: float = 30.0) -> dict:
    """The whole sequence. See the tool docstring for the contract."""
    from .library import (_reload_library, _rename_ops,
                          _save_library_verified)

    loop = asyncio.get_running_loop()
    out: dict = {"ok": False, "library_path": library_path,
                 "generated": [], "renamed": [], "report": {}, "runs": [],
                 "stopped": None}

    pre = preflight(csv_paths, library_path)
    out["preflight"] = pre
    if not pre["ok"] and strict_preflight:
        out["stopped"] = {"stage": "preflight", "reason": (
            "the CSVs carry measured traps (see preflight.findings); "
            "nothing was sent to Altium. Fix them, or pass "
            "strict_preflight=False to run anyway")}
        return out

    before, failed = await _footprint_names(bridge, library_path)
    if failed:
        out["stopped"] = {"stage": "read_library", **failed}
        return out
    expected = pre["expected_names"]
    comma_forms = {n.replace(".", ",") for n in expected if "." in n}
    present = sorted((set(expected) | comma_forms) & before)
    out["already_present"] = present
    if present and not allow_existing:
        out["stopped"] = {"stage": "preflight", "reason": (
            "these names are already in the library, and the generator "
            "stops on an 'already exists' prompt for each. Rename or delete "
            "them first, or pass allow_existing=True to let the prompt "
            "stop the run instead"), "names": present}
        return out
    if preflight_only:
        out["ok"] = True
        out["note"] = "preflight only: nothing was generated"
        return out

    workspace = Path(bridge.config.workspace_dir)
    state = _load_state(workspace)
    error_seen = False

    for csv_path in csv_paths:
        rec: dict = {"csv": csv_path}
        out["runs"].append(rec)

        focus = await _focus_library(bridge, library_path)
        rec["focus"] = focus
        if not focus.get("ok"):
            out["stopped"] = {"stage": "focus", "csv": csv_path, "reason": (
                "the library could not be confirmed as the focused "
                "document, so the generator would write somewhere else"),
                "focus": focus}
            error_seen = True
            break

        try:
            request_id = bridge.send_command_detached(
                "generic.run_process", {"process": BATCH_PROCESS,
                                        "params": ""})
        except Exception as exc:                 # noqa: BLE001
            out["stopped"] = {"stage": "launch", "csv": csv_path,
                              **_failure("generic.run_process", exc)}
            error_seen = True
            break

        dirs = [d for d in (report_dir, state.get("report_dir"),
                            os.path.join(os.path.dirname(csv_path),
                                         "Output")) if d]
        ui = await loop.run_in_executor(None, functools.partial(
            _drive_one_reporting, pid, csv_path, dirs,
            dialog_timeout=dialog_timeout, run_timeout=run_timeout,
            open_report=open_report, may_steal_focus=may_steal_focus,
            library_path=library_path))
        rec["dialog"] = ui

        if ui.get("batch_dialog_open"):
            # The form is still up, so RunProcess has not returned and the
            # bridge cannot be asked anything: return NOW with the screen
            # state. Every bridge call from here would only sit behind it.
            out["stopped"] = {"stage": ui.get("stage", "dialog"),
                              "csv": csv_path, "reason": ui.get("reason"),
                              "bridge_blocked": True,
                              "dialogs": await loop.run_in_executor(
                                  None, _screen_report, pid)}
            if ui.get("error"):
                out["stopped"]["error"] = ui["error"]
            out["next_step"] = (
                "answer or close the dialog in Altium (app_list_open_dialogs "
                "and app_press_dialog_button work while the bridge is "
                "blocked), then lib_get_footprints to see what was made")
            return out

        reply = await loop.run_in_executor(
            None, bridge.collect_detached, request_id, engine_timeout)
        rec["engine_free"] = reply is not None
        if reply is None:
            out["stopped"] = {"stage": "engine", "csv": csv_path,
                              "bridge_blocked": True, "reason": (
                f"the batch form is gone but the RunProcess that opened it "
                f"has not returned within {engine_timeout:g}s"),
                "dialogs": await loop.run_in_executor(
                    None, _screen_report, pid)}
            return out
        rec["run_process_reply"] = _reply_record(reply)
        handler_problem = _reply_problem(rec["run_process_reply"])

        if not ui.get("ok"):
            reason = ui.get("reason") or ""
            if handler_problem:
                reason = (f"{reason}; {handler_problem}" if reason
                          else handler_problem)
            out["stopped"] = {"stage": ui.get("stage", "dialog"),
                              "csv": csv_path, "reason": reason,
                              "run_process_reply": rec["run_process_reply"]}
            if ui.get("error"):
                out["stopped"]["error"] = ui["error"]
            error_seen = True
            break
        if handler_problem:
            # The form ran and its report decides the outcome below, but
            # the handler's complaint is not thrown away.
            out.setdefault("warnings", []).append(
                {"csv": csv_path, "warning": handler_problem})

        found = ui.get("report")
        if found is None:
            # Last resort, now the bridge answers: the report opens as a
            # document and takes focus, so the active document may BE it.
            try:
                active = await bridge.send_command_async(
                    "application.get_active_document")
                path = (active or {}).get("file_path", "")
            except Exception:                    # noqa: BLE001
                path = ""
            rec["active_document_after_run"] = path
            if path.lower().endswith(".html"):
                found = files.report_for(csv_path, [{"path": path}])
        if found is None:
            out["stopped"] = {"stage": "report", "csv": csv_path, "reason": (
                "the run ended but no batch report naming this CSV was "
                "found, so its errors cannot be read. Searched: "
                + ", ".join(ui.get("report_dirs") or dirs)
                + ". Pass report_dir with the form's Output Folder")}
            error_seen = True
            break

        source = found["source"]
        out["report"][csv_path] = {
            "report_path": found["path"], "fatal": source.get("fatal"),
            "errors": source.get("errors"),
            "warnings": source.get("warnings"),
            "messages": source.get("messages", []),
            "front": found.get("front", {}),
            "also_processed": found.get("other_sources", [])}
        state["report_dir"] = os.path.dirname(found["path"])
        _save_state(workspace, state)

        if close_reports:
            rec["report_closed"] = await _close_report(bridge, found["path"])

        if found.get("other_sources"):
            out["stopped"] = {"stage": "report", "csv": csv_path, "reason": (
                "the report lists other input files as processed too, so "
                "the run generated more than this CSV"),
                "also_processed": found["other_sources"]}
            error_seen = True
            break
        if (source.get("fatal") or 0) or (source.get("errors") or 0):
            out["stopped"] = {"stage": "report", "csv": csv_path, "reason": (
                f"the generator reported {source.get('fatal')} fatal and "
                f"{source.get('errors')} errors for this CSV"),
                "messages": source.get("messages", [])}
            error_seen = True
            break

    after, failed = await _footprint_names(bridge, library_path)
    if failed:
        out["stopped"] = out["stopped"] or {"stage": "read_library",
                                            **failed}
        return out
    created = sorted(after - before)
    out["created_in_editor"] = created

    if error_seen and not finalize_on_error:
        out["unsaved"] = created
        out["next_step"] = (
            "the library holds these footprints in the editor only. Keep "
            "them with lib_save_library then lib_reload_library; drop them "
            "with app_close_document(discard_changes=True) and reopen the "
            "library")
        return out
    if not created:
        out["ok"] = not error_seen
        out["note"] = "nothing new appeared in the library; nothing to save"
        return out

    async def save_and_reload(tag: str) -> bool:
        saved = await _save_library_verified(bridge, library_path)
        out[f"saved_{tag}"] = saved
        if not saved.get("ok"):
            out["stopped"] = out["stopped"] or {
                "stage": f"save_{tag}", "reason": (
                    "the save did not reach disk, so the reload that would "
                    "follow was NOT done: closing a dirty library raises "
                    "Altium's Unsaved Changes prompt"), "save": saved}
            return False
        focus = await _focus_library(bridge, library_path)
        reloaded = focus.get("ok") and await _reload_library(
            bridge, library_path)
        out[f"reloaded_{tag}"] = bool(reloaded)
        if not reloaded:
            out["stopped"] = out["stopped"] or {
                "stage": f"reload_{tag}", "focus": focus,
                "reason": "the library did not reopen from disk"}
        return bool(reloaded)

    if not await save_and_reload("generated"):
        return out

    plan = files.comma_renames(created, expected, after)
    out["rename_plan"] = plan
    final_names = dict.fromkeys(created)
    if plan["renames"]:
        ops, refused = _rename_ops(plan["renames"])
        out["rename_refused"] = refused
        if ops:
            try:
                reply = await bridge.send_command_async(
                    "library.rename_footprints",
                    {"library_path": library_path,
                     "renames": "~~".join(ops)}, timeout=120.0)
            except Exception as exc:             # noqa: BLE001
                reply = {"results": []}
                out["rename_error"] = _failure("library.rename_footprints",
                                               exc)
            for item in (reply or {}).get("results", []):
                out["renamed"].append({"from": item.get("old"),
                                       "to": item.get("new"),
                                       "status": item.get("status")})
                if item.get("status") == "renamed":
                    final_names.pop(item.get("old"), None)
                    final_names[item.get("new")] = None
            if any(r["status"] == "renamed" for r in out["renamed"]):
                if not await save_and_reload("renamed"):
                    return out

    names, failed = await _footprint_names(bridge, library_path)
    if failed:
        out["stopped"] = out["stopped"] or {"stage": "verify", **failed}
        return out
    out["generated"] = sorted(n for n in final_names if n in names)
    out["verify"] = {
        "missing_expected": sorted(n for n in expected if n not in names),
        "lost": sorted(n for n in final_names if n not in names),
        "comma_names_left": sorted(r["from"] for r in plan["renames"]
                                   if r["from"] in names),
    }
    clean = not any(out["verify"].values()) and not plan["ambiguous"] \
        and not plan["clashes"] and all(
            r["status"] == "renamed" for r in out["renamed"])
    out["ok"] = clean and not error_seen
    return out
