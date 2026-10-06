# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Every DelphiScript unit closes each Begin/Try/Case/Record exactly once.

A STRAY ``End;`` SHIPPED PAST EVERY OTHER CHECK. On 2026-10-06 the
save-all policy change (bridge 2026.10.06.01, never deployed) left one
extra ``End;`` straight after App_SaveAll's own. It closed nothing, so
Application.pas could not compile, and DelphiScript compiles the whole
Altium_API.PrjScr project, so the bridge would not have loaded at all.
lint.py, build.py and the full offline suite were green with it in place:
nothing counted Begin against End, and FPC was not run. Review found it by
a block-depth scan; this is that scan, as a lint rule and a test.

The negative controls rebuild the exact defect from the real source, so the
rule is proved against the shape that shipped, not only a toy.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts" / "altium"
LINT_PATH = SCRIPTS / "lint.py"

# The build output is a concatenation of the others and may be stale.
EXCLUDED = {"Altium_MCP.pas", "scratch.pas"}


def _load_lint_module():
    spec = importlib.util.spec_from_file_location("_altium_lint_balance", LINT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_altium_lint_balance"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lint():
    return _load_lint_module()


def _live_pas() -> list[Path]:
    return [p for p in sorted(SCRIPTS.glob("*.pas")) if p.name not in EXCLUDED]


def _rules(findings) -> list[str]:
    return [f.rule for f in findings]


def test_there_are_units_to_scan():
    names = {p.name for p in _live_pas()}
    for required in ("Main.pas", "Application.pas", "Generic.pas", "Project.pas"):
        assert required in names


@pytest.mark.parametrize("pas", _live_pas(), ids=lambda p: p.name)
def test_every_unit_balances(lint, pas):
    text = pas.read_text(encoding="utf-8")
    findings = lint._scan_block_balance(pas.name, text)
    assert not findings, "\n".join(f.format() for f in findings)


def test_the_rule_runs_inside_lint_file(lint, tmp_path, monkeypatch):
    """build.py aborts on lint errors, so the rule must be IN lint_file,
    not only reachable from this test."""
    # lint_file names findings relative to scripts/altium, which fails
    # across drives (tmp on C:, a checkout on H:); the name is not under test.
    monkeypatch.setattr(lint.os.path, "relpath",
                        lambda p, start=None: Path(p).name)
    bad = tmp_path / "Bad.pas"
    bad.write_text("Procedure P;\nBegin\nEnd;\nEnd;\n", encoding="utf-8")
    found = [f for f in lint.lint_file(str(bad))
             if f.rule == "block-end-closes-nothing"]
    assert found and found[0].severity == "error" and found[0].line == 4


def test_the_shipped_defect_is_caught(lint):
    """NEGATIVE CONTROL: re-create the 2026.10.06.01 stray End after
    App_SaveAll's closing End in the real Application.pas."""
    text = (SCRIPTS / "Application.pas").read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    marker = "SaveAllDirty raised an exception');" + nl + "    End;" + nl + "End;" + nl
    assert text.count(marker) == 1, "App_SaveAll's tail moved; update the control"
    broken = text.replace(marker, marker + "End;" + nl)
    stray_line = broken[: broken.index(marker) + len(marker)].count("\n") + 1

    findings = lint._scan_block_balance("Application.pas", broken)
    assert _rules(findings) == ["block-end-closes-nothing"]
    assert findings[0].line == stray_line


def test_a_missing_end_is_caught(lint):
    text = (SCRIPTS / "Main.pas").read_text(encoding="utf-8")
    nl = "\r\n" if "\r\n" in text else "\n"
    # Drop the last top-level "End;" in the unit.
    idx = text.rfind(nl + "End;")
    assert idx != -1
    broken = text[:idx] + text[idx + len(nl + "End;"):]
    assert _rules(lint._scan_block_balance("Main.pas", broken)) == [
        "block-unclosed-at-eof"]


@pytest.mark.parametrize("snippet", [
    "{ End; }",
    "{ an End that\n  spans lines End; }",
    "(* End; *)",
    "// End;",
    "S := 'End;';",
    "S := 'it''s the End';",
])
def test_end_in_comments_and_strings_is_not_counted(lint, snippet):
    src = "Procedure P;\nVar S : String;\nBegin\n" + snippet + "\nEnd;\n"
    assert lint._scan_block_balance("X.pas", src) == []


def test_try_case_and_record_each_need_their_end(lint):
    good = (
        "Type R = Record A : Integer; End;\n"
        "Procedure P(I : Integer);\nBegin\n"
        "    Try I := 1; Except I := 0; End;\n"
        "    Case I Of 1: I := 2; End;\n"
        "End;\n")
    assert lint._scan_block_balance("X.pas", good) == []
    for opener in ("Try I := 1; Except I := 0; End;",
                   "Case I Of 1: I := 2; End;"):
        broken = good.replace(opener, opener[:-len(" End;")], 1)
        assert _rules(lint._scan_block_balance("X.pas", broken)) == [
            "block-unclosed-at-eof"], opener
