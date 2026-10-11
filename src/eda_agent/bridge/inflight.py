# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""What Altium is working on, read from files rather than over the bridge.

Altium's DelphiScript engine shows an exception inside a handler as a
MODAL "press OK" dialog before the handler's own Try/Except runs, and the
engine is blocked until someone presses it. While that dialog is up the
bridge cannot answer anything, so these two readers go to the workspace
files the Pascal side wrote BEFORE the handler ran:

* ``progress_<id>.json``: the dispatcher's heartbeat, which since
  the 2026-10-11 script names the command and the request id;
* ``activity.log``: RecordCaughtException's ``_caught_exception`` rows,
  one per modal that a Try/Except then caught.

Both readers never raise: they are used by status and timeout paths that
must answer precisely when something else has gone wrong.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional


def read_in_flight(workspace_dir) -> list[dict]:
    """The calls Altium is working on, newest first.

    Each entry is ``{"command", "request_id", "age_s"}``. ``age_s`` comes
    from the file's mtime, because the ``started_ms`` inside is Altium's
    GetTickCount and is not comparable to this process's clock. A file
    from an older script, or one caught mid-write, still counts, with an
    empty command and the request id taken from its name.
    """
    out: list[dict] = []
    try:
        paths = list(Path(workspace_dir).glob("progress_*.json"))
    except OSError:
        return out
    now = time.time()
    found = []
    for path in paths:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        data: dict = {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
        request_id = str(data.get("request_id") or "")
        if not request_id:
            request_id = path.stem[len("progress_"):]
        found.append((mtime, {
            "command": str(data.get("command") or ""),
            "request_id": request_id,
            "age_s": round(max(0.0, now - mtime), 1),
        }))
    found.sort(key=lambda pair: pair[0], reverse=True)
    out.extend(entry for _, entry in found)
    return out


def read_last_caught(workspace_dir) -> Optional[dict]:
    """The last ``_caught_exception`` row of ``activity.log``, or None.

    RecordCaughtException (Main.pas) appends
    ``<stamp>,0,_caught_exception,<where>,<command>,<request_id>`` each
    time a Try/Except that is not a fault-only backstop catches something,
    i.e. each time an exception modal fired. Only the last 64 KB of the
    log is read.

    THIS PUMP SESSION ONLY: the backward scan stops at the newest
    ``_session_start`` row, so a catch from an earlier session is never
    reported beside ``app_ping``'s ``caught_exceptions`` = 0 for this one.
    """
    path = Path(workspace_dir) / "activity.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 65536))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(tail.splitlines()):
        parts = line.split(",")
        if len(parts) >= 3 and parts[2] == "_session_start":
            return None
        if len(parts) >= 4 and parts[2] == "_caught_exception":
            return {
                "at": parts[0],
                "where": parts[3],
                "command": parts[4] if len(parts) > 4 else "",
                "request_id": parts[5] if len(parts) > 5 else "",
            }
    return None
