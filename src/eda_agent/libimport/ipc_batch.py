# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Altium IPC Footprint Wizard batch files: read the input, read the report.

Pure and offline. The batch generator itself is a dialog with no API, and
ui/ipc_batch_dialog.py drives it; this module holds everything about it
that can be decided from files alone, so all of it is testable without
Altium:

* what a batch CSV will produce, and the traps in it that were MEASURED to
  fail or corrupt a run (``parse_batch_csv``);
* what the generator's HTML report says per input file (``parse_report``);
* where that report landed, and which one belongs to a given CSV
  (``find_reports`` / ``report_for``);
* which generated names are the generator's '.' -> ',' substitution, and
  what they should be called (``comma_renames``).

MEASURED, live on AD 26.10, 2026-10-02..04 (13 reports on disk):

* xls input turns numeric cells into dates: ``Invalid quantity
  "04/01/1900 19:12" in Real field "Hmin"`` for every numeric field, even
  with General cell format. CSV is the only input that survives.
* Row 0 is the template's row 0 (its FIRST CELL is the package tag the
  generator detects the type from), row 1 the column names, data after.
* A blank PinOrder is a hard error: ``Field "PinOrder" is blank``.
* PinOrder ``1 -2 -3`` produced designators '1', '-2', '-3': the field is
  split on whitespace as well as commas. Write it quoted, ``"1,2,3"``.
* A '.' in FootprintName comes back as ','.
* The report goes to the dialog's STICKY Output Folder, not beside the
  CSV: reports for CSVs in three different folders all landed in the first
  folder's Output directory.
* The report file name carries the time to the MINUTE, so two runs in one
  minute share a name. Read each report as soon as its run ends.
"""

from __future__ import annotations

import csv
import html
import os
import re
from pathlib import Path
from typing import Iterable, Optional

#: Matches every batch report the generator writes. The name is
#: 'IPC Compliant Footprint Wizard - Batch Report 2026-Oct-04, 07.01 am.html'
#: (the DLL's format string is ' - Batch Report %s.html').
REPORT_GLOB = "*Batch Report*.html"

#: Templates whose PinOrder was MEASURED to be required. Blank PinOrder
#: generated cleanly on SOIC, SON and SOP (reports of 2026-10-02 23:40 and
#: 2026-10-04 07:01) and failed on SOT23 (2026-10-03 01:14), so this is a
#: per-template fact, not a rule; add a tag only on a measured failure.
PIN_ORDER_REQUIRED = frozenset({"SOT23"})

XLS_REASON = (
    "the batch generator's xls reader turns every numeric cell into a "
    "date, so every dimension fails as 'Invalid quantity \"04/01/1900 "
    "19:12\" in Real field ...' (measured 2026-10-02, General cell format "
    "included). Write the same rows as CSV")


def _norm(path: object) -> str:
    return os.path.normcase(os.path.normpath(str(path or "")))


def _finding(out: dict, problem: str, severity: str, why: str,
             row: Optional[int] = None, column: str = "",
             value: str = "") -> None:
    item = {"csv": out["csv"], "problem": problem, "severity": severity,
            "why": why}
    if row is not None:
        item["row"] = row
    if column:
        item["column"] = column
    if value:
        item["value"] = value
    out["findings"].append(item)


def parse_batch_csv(path: str) -> dict:
    """What one batch CSV will produce, and every measured trap in it.

    ``row`` in a finding is the 1-based line number a spreadsheet shows.
    Severity ``error`` is a trap that fails the package or produces wrong
    output; ``warning`` is something the caller should look at.
    """
    p = Path(path)
    out: dict = {"csv": str(p), "package_tag": "", "columns": [],
                 "data_rows": 0, "names": [], "unnamed_rows": [],
                 "findings": []}
    suffix = p.suffix.lower()
    if suffix in (".xls", ".xlsx"):
        _finding(out, "xls_input", "error", XLS_REASON)
        return out
    if suffix != ".csv":
        _finding(out, "not_csv", "error",
                 f"{p.name} is not a .csv file; the batch generator reads "
                 f"CSV (and xls, which mangles numbers)")
        return out
    if not p.is_file():
        _finding(out, "missing", "error", f"{p} does not exist")
        return out
    try:
        with open(p, newline="", encoding="utf-8-sig",
                  errors="replace") as f:
            rows = list(csv.reader(f))
    except (OSError, csv.Error) as exc:
        _finding(out, "unreadable", "error", str(exc))
        return out

    if len(rows) < 2:
        _finding(out, "no_header", "error",
                 "a batch CSV needs the template's row 0 (package tag) and "
                 "row 1 (column names) before any data")
        return out

    tag = (rows[0][0] if rows[0] else "").strip()
    out["package_tag"] = tag
    if not tag:
        _finding(out, "no_package_tag", "error",
                 "row 0's first cell must be the template's package tag "
                 "(e.g. SOIC, SOT23, Chip); the generator detects the "
                 "package type from it", row=1)

    columns = [c.strip() for c in rows[1]]
    out["columns"] = columns
    index = {c.lower(): i for i, c in enumerate(columns) if c}
    name_i = index.get("footprintname")
    pin_i = index.get("pinorder")
    if name_i is None:
        _finding(out, "no_footprint_name_column", "warning",
                 "no FootprintName column, so the generator picks the "
                 "names and none can be predicted; '.'->',' repairs are "
                 "skipped for this file")

    def cell(row: list, i: Optional[int]) -> str:
        if i is None or i >= len(row):
            return ""
        return row[i].strip()

    first_line: dict = {}
    for lineno, row in enumerate(rows[2:], start=3):
        if not any((c or "").strip() for c in row):
            continue
        out["data_rows"] += 1
        name = cell(row, name_i)
        if name:
            if name in first_line:
                _finding(out, "duplicate_name", "error",
                         f"FootprintName repeats line {first_line[name]}; "
                         f"the second package would collide with the first",
                         row=lineno, column="FootprintName", value=name)
            else:
                first_line[name] = lineno
                out["names"].append(name)
        else:
            out["unnamed_rows"].append(lineno)
        if pin_i is not None:
            pins = cell(row, pin_i)
            if not pins and tag.upper() in PIN_ORDER_REQUIRED:
                _finding(out, "blank_pin_order", "error",
                         f"measured on the {tag} template: the generator "
                         f"rejects the package with 'Field \"PinOrder\" is "
                         f"blank'. (A blank PinOrder was accepted by the "
                         f"SOIC, SON and SOP templates.)",
                         row=lineno, column="PinOrder")
            dashed = [t for t in re.split(r"[,\s]+", pins)
                      if t and "-" in t]
            if dashed:
                _finding(out, "pin_order_dash", "error",
                         "measured: PinOrder '1 -2 -3' produced pad "
                         "designators '1', '-2', '-3'. The field splits on "
                         "commas and whitespace and '-' is not a range; "
                         "write \"1,2,3\"",
                         row=lineno, column="PinOrder", value=pins)

    if out["data_rows"] == 0:
        _finding(out, "no_data_rows", "error",
                 "no data rows after the two header rows")
    return out


_ROW = re.compile(r'<tr class="onmouseout_[a-z]+".*?</tr>', re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_FRONT = re.compile(
    r'class="front_matter_column1">([^<]*)</td>\s*<td[^>]*>\s*</td>\s*'
    r'<td[^>]*>([^<]*)</td>', re.I)
_SECTION = re.compile(r'<h2>Errors and Warnings for file "([^"]*)"</h2>',
                      re.I)


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _count(cell: str) -> Optional[int]:
    try:
        return int(cell)
    except ValueError:
        return None


def parse_report(text: str) -> dict:
    """The batch report's front matter and per-file results.

    ``sources`` has one entry per processed file with ``fatal``,
    ``errors``, ``warnings`` (the summary table) and ``messages`` (the
    per-file detail table: type, footprint, description). ``parsed`` is
    False when the summary table was not found, which means the format
    changed, not that the run was clean.
    """
    out: dict = {"front": {}, "sources": [], "parsed": False}
    for key, value in _FRONT.findall(text):
        out["front"][key.strip().rstrip(":").strip().lower()] = value.strip()

    start = text.find("<h2>Processed Source Files")
    if start < 0:
        return out
    end = text.find("</table>", start)
    for row in _ROW.findall(text[start:end if end > 0 else len(text)]):
        cells = [_text(c) for c in _CELL.findall(row)]
        if len(cells) < 4:
            continue
        out["sources"].append({
            "path": cells[0], "fatal": _count(cells[1]),
            "errors": _count(cells[2]), "warnings": _count(cells[3]),
            "messages": []})

    by_path = {_norm(s["path"]): s for s in out["sources"]}
    sections = list(_SECTION.finditer(text))
    for i, match in enumerate(sections):
        stop = sections[i + 1].start() if i + 1 < len(sections) else len(text)
        segment = text[match.end():stop]
        table_end = segment.find("</table>")
        if table_end >= 0:
            segment = segment[:table_end]
        path = html.unescape(match.group(1))
        target = by_path.get(_norm(path))
        if target is None:
            target = {"path": path, "fatal": None, "errors": None,
                      "warnings": None, "messages": []}
            out["sources"].append(target)
            by_path[_norm(path)] = target
        for row in _ROW.findall(segment):
            cells = [_text(c) for c in _CELL.findall(row)]
            if len(cells) >= 3:
                target["messages"].append({"type": cells[0],
                                           "footprint": cells[1],
                                           "description": cells[2]})
    out["parsed"] = bool(out["sources"])
    return out


def read_report(path: str) -> dict:
    """``parse_report`` on a file. cp1252, because the title carries a
    registered sign written as a single byte."""
    text = Path(path).read_text(encoding="cp1252", errors="replace")
    parsed = parse_report(text)
    parsed["path"] = str(path)
    return parsed


def find_reports(dirs: Iterable[str], since: float,
                 slack: float = 2.0) -> list:
    """Batch reports written at or after ``since``, newest first."""
    seen: set = set()
    found: list = []
    for folder in dirs:
        if not folder:
            continue
        try:
            entries = list(Path(folder).glob(REPORT_GLOB))
        except OSError:
            continue
        for entry in entries:
            key = _norm(entry)
            if key in seen:
                continue
            seen.add(key)
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime >= since - slack:
                found.append({"path": str(entry), "mtime": mtime})
    found.sort(key=lambda r: r["mtime"], reverse=True)
    return found


def report_for(csv_path: str, candidates: Iterable[dict]) -> Optional[dict]:
    """The newest candidate report that lists ``csv_path`` as processed."""
    want = _norm(csv_path)
    for candidate in candidates:
        try:
            parsed = read_report(candidate["path"])
        except OSError:
            continue
        for source in parsed["sources"]:
            if _norm(source["path"]) == want:
                return {"path": candidate["path"], "source": source,
                        "front": parsed["front"],
                        "other_sources": [s["path"] for s in parsed["sources"]
                                          if s is not source]}
    return None


def comma_renames(created: Iterable[str], expected: Iterable[str],
                  existing: Iterable[str]) -> dict:
    """Map the generator's ',' names back to the '.' names that were asked for.

    Only names in ``created`` (new since the run began) are considered, so
    a footprint that legitimately carries a comma, or one that was already
    in the library, is never touched. ``existing`` is the library as it is
    now: a target name already present is a clash, reported and not done.
    """
    by_comma: dict = {}
    for name in expected:
        if "." in name:
            by_comma.setdefault(name.replace(".", ","), set()).add(name)
    have = set(existing)
    renames, ambiguous, clashes = [], [], []
    for name in sorted(set(created)):
        targets = by_comma.get(name)
        if not targets:
            continue
        if len(targets) > 1:
            ambiguous.append({"from": name, "candidates": sorted(targets)})
            continue
        target = next(iter(targets))
        if target in have:
            clashes.append({"from": name, "to": target})
            continue
        renames.append({"from": name, "to": target})
    return {"renames": renames, "ambiguous": ambiguous, "clashes": clashes}
