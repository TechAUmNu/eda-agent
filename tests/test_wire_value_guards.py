# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""One guard per kind of value bound for the batch wire, kept in one place.

bridge/payload.py exists because two sanitisers drifted ("Kept in one
place because it was not"). The same happened to the number guard: four
copies across library.py and pcb.py, one of which accepted ``True`` as 1.0
while its siblings refused it, and three rules for booleans inside batch
dicts (truthiness, which reads the string "false" as true; bool-only; and
none). These pin the shared helpers and that the copies are gone.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from eda_agent.bridge import payload
from eda_agent.bridge.payload import (finite_number, name_wire_problem,
                                      non_finite, whole_number, wire_bool)

SRC = Path(__file__).resolve().parents[1] / "src" / "eda_agent"


@pytest.mark.parametrize("value,want", [
    (1, 1.0), ("2.5", 2.5), (0, 0.0),
    (True, None), (False, None), (float("nan"), None), (float("inf"), None),
    ("1e999", None), (10 ** 400, None), ("x", None), (None, None),
    ([1], None)])
def test_finite_number(value, want):
    assert finite_number(value) == want


@pytest.mark.parametrize("value,want", [
    (200, 200), (200.0, 200), ("200", 200), (-5, -5),
    (12.7, None), (True, None), (float("inf"), None), ("a", None)])
def test_whole_number(value, want):
    assert whole_number(value) == want


def test_non_finite_names_bools_too():
    assert non_finite({"a": 1, "b": True, "c": float("nan"), "d": "2"}) == \
        ["b", "c"]


@pytest.mark.parametrize("value,want", [
    (True, True), (False, False), (1, True), (0, False),
    ("true", True), ("FALSE", False), (" yes ", True), ("no", False),
    ("1", True), ("0", False), ("on", True), ("off", False),
    (2, None), (1.0, None), ("maybe", None), ("", None), (None, None)])
def test_wire_bool(value, want):
    assert wire_bool(value) is want


@pytest.mark.parametrize("value", ["A;B", "A~~B", "B~", "Ω"])
def test_a_name_the_wire_cannot_carry_is_named(value):
    assert name_wire_problem(value)


@pytest.mark.parametrize("value", ["R1", "~RESET", "A~B", "EN=1", "", "µ"])
def test_a_name_the_wire_carries_passes(value):
    assert name_wire_problem(value) == ""


def test_the_copies_are_gone():
    from eda_agent.tools import library, pcb

    assert library._num is finite_number
    assert library._non_finite is non_finite
    assert pcb._non_finite is non_finite
    assert not hasattr(library, "_finite"), (
        "_finite duplicated _num exactly; use one helper")
    assert "def _non_finite" not in inspect.getsource(pcb)


def _unused_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                name = (alias.asname or alias.name).split(".")[0]
                if name != "annotations":
                    imported.setdefault(name, node.lineno)
    used = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    return sorted(n for n in imported if n not in used)


@pytest.mark.parametrize("rel", [
    "tools/ipc_batch_flow.py", "ui/ipc_batch_dialog.py",
    "bridge/payload.py"])
def test_the_new_modules_import_only_what_they_use(rel):
    assert _unused_imports(SRC / rel) == []


def test_the_helpers_are_exported():
    for name in ("name_wire_problem", "finite_number", "non_finite",
                 "whole_number", "wire_bool"):
        assert name in payload.__all__
