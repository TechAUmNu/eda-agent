# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A rebuilt 3D model replaces the footprint's old body.

A follow-up take rebuilt the model of every footprint; each kept its old
plain box beside the new model, and the agent removed them by hand.

The 2026-10-25 merge kept carrier-local's lib_link_3d_model (replace=False
by default; replace=True deletes with the footprint parked, then links,
after a save and reopen), so upstream's three tests of a replace-by-default
lib_link_3d_model (replace_existing, replaced_bodies) went. Its replace
route is pinned by test_3d_bodies_contract.py and
test_library_removal_rule.py. lib_create_3d_model keeps upstream's
remove-first default, now with the footprint parked.
"""

from __future__ import annotations

import asyncio

from eda_agent.tools import library


class _Bridge:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params=None, timeout=None):
        self.calls.append((command, dict(params or {})))
        if command == "library.delete_footprint_primitives":
            return {"removed": 1, "leftover": 0}
        return {"success": True}


def _tools(bridge, monkeypatch):
    monkeypatch.setattr(library, "get_bridge", lambda: bridge)
    tools = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn
            return deco

    library.register_library_tools(_Mcp())
    return tools


def test_a_plain_link_adds_beside_and_removes_nothing(monkeypatch, tmp_path):
    """carrier-local's default, kept by the merge: replace is opt-in,
    because replace=True saves and reopens the library first."""
    step = tmp_path / "lens.step"
    step.write_text("x", encoding="ascii")
    bridge = _Bridge()
    asyncio.run(_tools(bridge, monkeypatch)["lib_link_3d_model"](
        "LED_2020", str(step)))
    assert [c for c, _ in bridge.calls] == ["library.link_3d_model"]
    assert bridge.calls[0][1]["replace"] == "false"


def test_the_old_body_is_removed_with_the_footprint_parked():
    """A primitive removed while its footprint is current came back with
    the next save (MEASURED 2026-10-05), so the removal before a rebuilt
    model is linked parks the footprint, as the replace route does."""
    bridge = _Bridge()
    out = asyncio.run(library._remove_old_bodies(bridge, "LED_2020"))
    [(command, delete)] = bridge.calls
    assert command == "library.delete_footprint_primitives"
    assert delete["footprint_name"] == "LED_2020"
    assert delete["object_type"] == "component_body" and delete["confirm"] == "true"
    assert delete["park_current"] == "true"
    assert out == {"removed": 1, "leftover": 0}


def test_the_current_footprint_is_not_guessed_at():
    bridge = _Bridge()
    out = asyncio.run(library._remove_old_bodies(bridge, ""))
    assert bridge.calls == [] and "skipped" in out


def test_a_model_reports_its_solids_not_just_one_body(monkeypatch, tmp_path):
    """A take read "bodies": 1 as "single plain blocks" and rebuilt eleven
    models that already had terminations and leads."""
    step = tmp_path / "chip.step"
    step.write_text("#1=MANIFOLD_SOLID_BREP('',#2);\n#3=MANIFOLD_SOLID_BREP('',#4);\n"
                    "#5=MANIFOLD_SOLID_BREP('',#6);\n", encoding="latin-1")

    class _B:
        async def send_command_async(self, command, params=None, timeout=None):
            return {"bodies": 1, "body_models": [
                {"model_file": str(step)}, {"model_file": str(tmp_path / "gone.step")}]}

    out = asyncio.run(_tools(_B(), monkeypatch)["lib_get_footprint_primitives"]("C0402"))
    assert [m["step_solids"] for m in out["body_models"]] == [3, None]
