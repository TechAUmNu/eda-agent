# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Python-side refusals in the library tools share ONE shape.

``{"ok": False, "reason": ..., <details>}``. Before, lib_rename_footprints
answered ``{"error": ...}`` when every rename was refused,
lib_auto_link_3d_models answered ``{"success": False, "error": ...}`` for
a missing folder but ``{"ok": False, "reason": ...}`` for a bad align, and
lib_save_library ``{"ok": False, "error": ...}``, so a caller could not
test for a refusal one way.
"""

from __future__ import annotations

import asyncio

import pytest


class _Bridge:
    def __init__(self, replies=None, raise_for=None):
        self.calls = []
        self.replies = replies or {}
        self.raise_for = raise_for or {}

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command in self.raise_for:
            raise self.raise_for[command]
        return self.replies.get(command, {"success": True})


def _tools(monkeypatch, bridge):
    from eda_agent.tools import library as lib

    monkeypatch.setattr(lib, "get_bridge", lambda: bridge)
    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    lib.register_library_tools(_Mcp())
    return captured


def _is_refusal(out: dict) -> bool:
    return (isinstance(out, dict) and out.get("ok") is False
            and isinstance(out.get("reason"), str) and out["reason"]
            and "error" not in out and "success" not in out)


def test_every_rename_refused_is_the_house_refusal(monkeypatch):
    bridge = _Bridge()
    out = asyncio.run(_tools(monkeypatch, bridge)["lib_rename_footprints"](
        renames=[{"from": "A", "to": "A"}]))
    assert _is_refusal(out), out
    assert "same name" in out["reason"]
    assert out["refused"] and out["renamed"] == 0, "details are kept"
    assert bridge.calls == []


def test_a_save_the_bridge_refuses_is_the_house_refusal(monkeypatch,
                                                        tmp_path):
    from eda_agent.bridge.exceptions import AltiumCommandError

    lib_path = tmp_path / "a.PcbLib"
    lib_path.write_text("x")
    bridge = _Bridge(raise_for={"library.save_library": AltiumCommandError(
        "Not open in Altium", code="NOT_LOADED")})
    out = asyncio.run(_tools(monkeypatch, bridge)["lib_save_library"](
        library_path=str(lib_path)))
    assert _is_refusal(out), out
    assert out["code"] == "NOT_LOADED" and out["written_to_disk"] is False


@pytest.mark.parametrize("reply,fragment", [
    ({"still_modified": False, "file_age_changed": False},
     "did not change on disk"),
    ({"still_modified": True, "file_age_changed": True},
     "still reads modified"),
])
def test_a_save_that_did_not_land_says_why(monkeypatch, tmp_path, reply,
                                           fragment):
    lib_path = tmp_path / "a.PcbLib"
    lib_path.write_text("x")
    bridge = _Bridge(replies={"library.save_library": reply})
    out = asyncio.run(_tools(monkeypatch, bridge)["lib_save_library"](
        library_path=str(lib_path)))
    assert out["ok"] is False and fragment in out["reason"], out


def test_a_save_that_landed_carries_no_reason(monkeypatch, tmp_path):
    lib_path = tmp_path / "a.PcbLib"
    lib_path.write_text("x")
    bridge = _Bridge(replies={"library.save_library": {
        "still_modified": False, "file_age_changed": True}})
    out = asyncio.run(_tools(monkeypatch, bridge)["lib_save_library"](
        library_path=str(lib_path)))
    assert out["ok"] is True and "reason" not in out
