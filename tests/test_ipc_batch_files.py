# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The IPC batch generator's files: what a CSV will make, what a report says.

Every rule here is a measurement from live runs on AD 26.10
(2026-10-02..04, thirteen reports), not a reading of documentation, and
two of them CORRECT what was believed before the reports were read side
by side:

* a blank PinOrder is NOT always an error. It failed on the SOT23
  template and generated cleanly on SOIC, SON and SOP;
* the report does NOT land beside the CSV. The Output Folder is sticky.

The report fixtures below keep the generator's real markup (including
its malformed ``text-align:left]`` styles) with the paths replaced.
"""

from __future__ import annotations

import os
import time

from eda_agent.libimport import ipc_batch as ib

HEAD = ("SOT23,Package Specifications,,,,,,,,,,,,,,,,,,,,,,,,,,,,"
        "Footprint Specifications,,,,,,,,,,,\n")
COLS = ("FootprintName,FootprintDescription,Emin,Emax,PinCount,PinOrder,"
        "Pitch\n")


def _csv(tmp_path, rows, head=HEAD, cols=COLS, name="pkg.csv"):
    path = tmp_path / name
    path.write_text(head + cols + "".join(rows), encoding="utf-8")
    return str(path)


def _problems(plan):
    return [(f["problem"], f["severity"]) for f in plan["findings"]]


def test_xls_is_refused_with_the_measured_reason(tmp_path):
    path = tmp_path / "pilot.xls"
    path.write_bytes(b"not really xls")
    plan = ib.parse_batch_csv(str(path))
    assert _problems(plan) == [("xls_input", "error")]
    assert "04/01/1900" in plan["findings"][0]["why"]


def test_names_and_the_package_tag_are_read(tmp_path):
    plan = ib.parse_batch_csv(_csv(tmp_path, [
        'SOT-23-6_P0.95,six,2.6,3,6,"1,2,3,4,5,6",0.95\n',
        "\n",                                    # a blank line is not a row
        'SOT-23-3_P0.95,three,2.6,3,3,"1,2,3",0.95\n']))
    assert plan["package_tag"] == "SOT23"
    assert plan["names"] == ["SOT-23-6_P0.95", "SOT-23-3_P0.95"]
    assert plan["data_rows"] == 2
    assert plan["findings"] == []


def test_a_blank_pin_order_fails_on_sot23_only(tmp_path):
    rows = ["X_P0.95,x,2.6,3,3,,0.95\n"]
    sot = ib.parse_batch_csv(_csv(tmp_path, rows))
    assert ("blank_pin_order", "error") in _problems(sot)
    assert sot["findings"][0]["row"] == 3        # the line a sheet shows

    soic = ib.parse_batch_csv(_csv(
        tmp_path, rows, head="SOIC,Package Specifications\n",
        name="soic.csv"))
    assert "blank_pin_order" not in [p for p, _ in _problems(soic)], (
        "SOIC, SON and SOP generated cleanly with a blank PinOrder")


def test_a_dashed_pin_order_is_caught(tmp_path):
    """'1 -2 -3' became pad designators '1', '-2', '-3'."""
    plan = ib.parse_batch_csv(_csv(tmp_path, [
        "SOT-23-5_P0.95,five,2.6,3,5,1 -2 -3 -4 -5,0.95\n"]))
    assert ("pin_order_dash", "error") in _problems(plan)


def test_duplicate_names_and_missing_tag_are_errors(tmp_path):
    plan = ib.parse_batch_csv(_csv(tmp_path, [
        'A,a,1,2,3,"1,2,3",1\n', 'A,b,1,2,3,"1,2,3",1\n'],
        head=",Package Specifications\n"))
    problems = _problems(plan)
    assert ("no_package_tag", "error") in problems
    assert ("duplicate_name", "error") in problems


REPORT = """<html><body>
<table class="front_matter">
<tr class="front_matter">
<td class="front_matter_column1">Date:</td>
<td class="front_matter_column2"></td>
<td class="front_matter_column3">03/10/2026</td>
</tr>
<tr class="front_matter">
<td class="front_matter_column1">Elapsed Time:</td>
<td class="front_matter_column2"></td>
<td class="front_matter_column3">00:00:00</td>
</tr>
</table>
<a name="X"><h2>Processed Source Files</h2></a><a name="Y"><table>
<tr>
<th class="column1">Source file path</th>
<th class="column2">Fatal errors</th>
<th class="column3">Errors</th>
<th class="column4">Warnings</th>
</tr>
<tr class="onmouseout_odd" onmouseover="className = 'onmouseover_odd'" onmouseout="className = 'onmouseout_odd'">
<td style="width:60%; text-align:left]" class="column1">C:\\work\\gen_chip.csv</td>
<td style="width:13.3%; text-align:centre]" class="column2">0</td>
<td style="width:13.3%; text-align:centre]" class="column3">0</td>
<td style="width:13.3%; text-align:centre]" class="column4">0</td>
</tr>
<tr class="onmouseout_even" onmouseover="className = 'onmouseover_even'" onmouseout="className = 'onmouseout_even'">
<td style="width:60%; text-align:left]" class="column1">C:\\work\\gen_sot23.csv</td>
<td style="width:13.3%; text-align:centre]" class="column2">0</td>
<td style="width:13.3%; text-align:centre]" class="column3">1</td>
<td style="width:13.3%; text-align:centre]" class="column4">0</td>
</tr>
</table></a><hr color="#EEEEEE"><a name="Z"><h2>Errors and Warnings for file "C:\\work\\gen_sot23.csv"</h2></a><a name="W"><table>
<tr>
<th class="column1">Error and Warning Type</th>
<th class="column2">Affected footprint</th>
<th class="column3">Description</th>
</tr>
<tr class="onmouseout_odd" onmouseover="className = 'onmouseover_odd'" onmouseout="className = 'onmouseout_odd'">
<td style="width:25%; text-align:left]" class="column1">Invalid field value</td>
<td style="width:15%; text-align:left]" class="column2">Package #   1 (SOT-23-3_P0.95)</td>
<td style="width:60%; text-align:left]" class="column3">Error: Field "PinOrder" is blank</td>
</tr>
</table></a></body></html>
"""


def test_the_report_is_read_per_file():
    parsed = ib.parse_report(REPORT)
    assert parsed["parsed"]
    assert parsed["front"]["date"] == "03/10/2026"
    assert [s["path"] for s in parsed["sources"]] == [
        r"C:\work\gen_chip.csv", r"C:\work\gen_sot23.csv"]
    chip, sot = parsed["sources"]
    assert (chip["fatal"], chip["errors"], chip["warnings"]) == (0, 0, 0)
    assert chip["messages"] == []
    assert sot["errors"] == 1
    assert sot["messages"] == [{
        "type": "Invalid field value",
        "footprint": "Package #   1 (SOT-23-3_P0.95)",
        "description": 'Error: Field "PinOrder" is blank'}]


def test_an_unrecognised_report_is_not_a_clean_one():
    parsed = ib.parse_report("<html><body>something else</body></html>")
    assert parsed["parsed"] is False and parsed["sources"] == []


def test_the_report_for_a_csv_is_found_by_its_listed_path(tmp_path):
    out = tmp_path / "Output"
    out.mkdir()
    old = out / "IPC Compliant Footprint Wizard - Batch Report old.html"
    new = out / "IPC Compliant Footprint Wizard - Batch Report new.html"
    old.write_text(REPORT.replace("gen_sot23", "elsewhere"), encoding="cp1252")
    new.write_text(REPORT, encoding="cp1252")
    long_ago = time.time() - 3600
    os.utime(old, (long_ago, long_ago))

    since = time.time() - 60
    hits = ib.find_reports([str(out), str(tmp_path / "missing")], since)
    assert [h["path"] for h in hits] == [str(new)], (
        "a report older than the run must not be considered")
    found = ib.report_for(r"C:\WORK\gen_sot23.csv", hits)
    assert found and found["source"]["errors"] == 1, (
        "Windows paths compare case-insensitively")
    assert found["other_sources"] == [r"C:\work\gen_chip.csv"]
    assert ib.report_for(r"C:\work\nope.csv", hits) is None


def test_only_new_comma_names_with_a_dotted_request_are_renamed():
    plan = ib.comma_renames(
        created={"SOT-23-6_P0,95", "R_0402", "HDR_1,2"},
        expected=["SOT-23-6_P0.95", "R_0402"],
        existing={"SOT-23-6_P0,95", "R_0402", "HDR_1,2"})
    assert plan["renames"] == [{"from": "SOT-23-6_P0,95",
                                "to": "SOT-23-6_P0.95"}]
    assert plan["ambiguous"] == [] and plan["clashes"] == []


def test_a_taken_target_or_two_candidates_is_reported_not_renamed():
    clash = ib.comma_renames({"Q_1,2"}, ["Q_1.2"], {"Q_1,2", "Q_1.2"})
    assert clash["renames"] == [] and clash["clashes"] == [
        {"from": "Q_1,2", "to": "Q_1.2"}]
    two = ib.comma_renames({"A,B,C"}, ["A.B,C", "A,B.C"], set())
    assert two["renames"] == [] and two["ambiguous"][0]["from"] == "A,B,C"
