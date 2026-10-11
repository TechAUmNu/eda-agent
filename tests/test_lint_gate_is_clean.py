# SPDX-License-Identifier: Apache-2.0
"""The deploy gate's lint must pass on the committed sources.

build.py refuses to produce Altium_MCP.pas while scripts/altium/lint.py
reports an error, so a lint error is a deploy blocker. Before this test the
suite never ran the whole gate: on 2026-10-11 the popup-free repair turned
LoadMCPConfig's six config reads into ``If CfgIntOk(V) Then Try ...; ...;
Except End;`` (a known-bad DelphiScript form, see the if-then-try rule).
The full suite passed with 7416 tests, and the deploy stopped at build.py's
lint gate. This test runs the same gate, so that failure now shows up in
the suite instead of at deploy time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
LINT_PATH = REPO_ROOT / "scripts" / "altium" / "lint.py"


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("_altium_lint_gate", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_build_gate_lint_reports_no_errors():
    lint = _load_lint_module()
    # The same file set build.py lints (run_lint's default is PAS_FILES).
    errors = lint.run_lint(verbose=True)
    assert errors == 0, (
        f"scripts/altium/lint.py reports {errors} error(s); build.py would "
        "refuse to deploy. Run: python scripts/altium/lint.py")


def test_the_gate_catches_the_if_then_try_form_that_slipped_through():
    lint = _load_lint_module()
    bad = ["    If CfgIntOk(V) Then Try N := StrToInt(V); If N > 0 Then N := 1; "
           "Except End;"]
    good = ["    If CfgIntOk(V) Then Begin Try N := StrToInt(V); If N > 0 Then "
            "N := 1; Except End; End;"]
    rules_bad = {f.rule for f in lint._scan_if_then_try("Bad.pas", bad)}
    rules_good = {f.rule for f in lint._scan_if_then_try("Good.pas", good)}
    assert "if-then-try-multi-stmt" in rules_bad
    assert not rules_good
