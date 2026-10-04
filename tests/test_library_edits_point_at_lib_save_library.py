# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library edits tell the agent to save with lib_save_library.

``app_save_all`` writes EVERY open document, and every save rewrites open
SchDocs and PcbDocs with fresh parameter UniqueIDs (measured on a live
project, 2026-10-02). Steered there after a one-footprint edit, an agent
puts churn into every open sheet of the owner's version control, which is
why ``lib_save_library`` exists. These guards hold the three places an
agent reads the follow-up from:

* a library tool's docstring,
* a library recipe in ``tool_guide``,
* the ``next_step`` / ``note`` a library handler sends back.

Each may still NAME app_save_all (as the thing to avoid, or for saving
several documents on purpose), but never without lib_save_library.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_source import strip_comments

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_PAS = ROOT / "scripts" / "altium" / "Library.pas"


def _library_tools() -> dict:
    from eda_agent.tools.library import register_library_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    return captured


def test_the_scan_sees_the_library_tools():
    tools = _library_tools()
    assert len(tools) > 80 and "lib_save_library" in tools


def test_no_library_tool_docstring_sends_the_agent_to_app_save_all():
    offenders = []
    for name, fn in _library_tools().items():
        if name == "lib_save_library":
            continue                     # it names app_save_all to contrast
        doc = fn.__doc__ or ""
        if "app_save_all" in doc and "lib_save_library" not in doc:
            offenders.append(name)
    assert not offenders, (
        f"{offenders} tell the agent to run app_save_all, which rewrites "
        f"every open sheet; name lib_save_library")


def test_no_library_recipe_sends_the_agent_to_app_save_all():
    from eda_agent.tools.guidance import _RECIPES

    offenders = []
    for recipe in _RECIPES:
        if recipe.get("document_kind") != "library":
            continue
        text = " ".join([recipe.get("note", "")]
                        + list(recipe.get("use", ())))
        if "app_save_all" in text and "lib_save_library" not in text:
            offenders.append(recipe["task"])
        if "app_save_all" in recipe.get("use", ()):
            offenders.append(recipe["task"])
    assert not offenders, offenders


def test_saving_a_library_has_a_recipe_that_avoids_app_save_all():
    from eda_agent.tools.guidance import guidance_for

    out = guidance_for("save the library after editing a footprint", "",
                       "altium", {})
    first = out["recipes"][0]
    assert first["use"][0] == "lib_save_library"
    assert any(a[0] == "app_save_all" if isinstance(a, (list, tuple))
               else a.get("tool") == "app_save_all"
               for a in first.get("avoid", [])), first


def _statements_naming(text: str, word: str) -> list[str]:
    """Each decommented statement (``;`` to ``;``) containing ``word``."""
    code = strip_comments(text)
    out = []
    for m in re.finditer(re.escape(word), code):
        start = code.rfind(";", 0, m.start()) + 1
        end = code.find(";", m.end())
        out.append(code[start:end if end >= 0 else len(code)].strip())
    return out


@pytest.fixture(scope="module")
def library_pas() -> str:
    return LIBRARY_PAS.read_text(encoding="utf-8", errors="replace")


def test_the_handler_scan_finds_the_follow_ups(library_pas):
    """Not vacuous: the notes that name lib_save_library are found."""
    assert len(_statements_naming(library_pas, "lib_save_library")) >= 8


def test_no_library_handler_reply_sends_the_agent_to_app_save_all(
        library_pas):
    offenders = [s for s in _statements_naming(library_pas, "app_save_all")
                 if "lib_save_library" not in s]
    assert not offenders, (
        "a library handler's next_step or note names app_save_all without "
        "lib_save_library:\n" + "\n---\n".join(o[:200] for o in offenders))
