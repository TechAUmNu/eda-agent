# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Mutating list-packing tools refuse values carrying their separator.

44 tools pack a list into one delimited field; for most, a value
containing the delimiter merely splits into fragments that match
nothing. These are the ones where a FRAGMENT CAN MATCH A REAL OBJECT
and the operation mutates: a net name fragment like 'VCC' exists on
most boards, a designator fragment like 'R1' on most schematics. Each
must refuse, not strip: stripping 'R|1' produces 'R1', a different
real component (the DNP tool's original fix made exactly that mistake,
revisited in test_dnp_paste_exclusion.py).

The sharpest case is obj_batch_delete, where a '~~' inside a filter
value fabricates a NEW operation the confirm_delete_all guard never
inspected, so an unfiltered delete could ride in unconfirmed.
"""
from __future__ import annotations

import asyncio

import pytest

from eda_agent.tools.generic import register_generic_tools
from eda_agent.tools.library import register_library_tools
from eda_agent.tools.pcb import register_pcb_tools


class _CapturingBridge:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict]] = []

    async def send_command_async(self, command, params=None, **kwargs):
        self.commands.append((command, params or {}))
        return {"ok": True, "success": True}


def _tools() -> dict:
    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    for register in (register_generic_tools, register_library_tools,
                     register_pcb_tools):
        register(_Mcp())
    return captured


#: (tool, poisoned kwargs). Every value carries the tool's own
#: separator inside one element.
_CASES = [
    ("obj_batch_delete", {
        "operations": [{"scope": "active_doc", "object_type": "eWire",
                        "filter": "A~~scope=project;object_type="
                                  "eJunction;filter="}]}),
    ("sch_clear_source_library", {"designators": ["R1,R2"]}),
    ("lib_clear_source_library", {"component_names": ["A,B"]}),
    ("lib_move_components", {
        "source_schlib": "a.SchLib", "dest_schlib": "b.SchLib",
        "names": ["X~~Y"]}),
    ("lib_move_footprints", {
        "source_pcblib": "a.PcbLib", "dest_pcblib": "b.PcbLib",
        "names": ["X~~Y"]}),
    ("pcb_set_rules_enabled", {"names": ["A|B"], "enabled": False}),
    ("pcb_move_components", {"moves": [{"designator": "R|1", "x": 100}]}),
    ("pcb_lock_net_routing", {"nets": ["VCC|GND"], "lock": True}),
    ("pcb_clear_source_footprint_library",
     {"designator_filter": ["R|1"]}),
    ("lib_set_pin_properties", {
        "component_name": "U1",
        "pins": [{"designator": "1~~designator=2", "name": "X"}]}),
    ("sch_place_no_ercs", {"targets": [{"designator": "R1;pin=2",
                                        "pin": "1"}]}),
    # Pad edits were the odd ones out: they SANITISED the selector and the
    # new name ('A;B' went out as 'A,B') where every sibling refuses.
    ("lib_modify_footprint_pads", {
        "footprint_name": "FP",
        "edits": [{"designator": "1~~designator=2", "y": 1}]}),
    ("lib_modify_footprint_pads", {
        "footprint_name": "FP",
        "edits": [{"designator": "1", "new_designator": "A;B"}]}),
    ("lib_delete_footprint_pads", {
        "footprint_name": "FP", "pads": [{"designator": "1;all=true"}],
        "confirm": True}),
    ("lib_rename_footprints", {"renames": [{"from": "A;B", "to": "C"}]}),
    # A TRAILING '~' runs into the '~~' after it: 'new=B~' + '~~' reads as
    # 'new=B' and an op '~old=...'. The reason names the separator too.
    ("lib_rename_footprints", {"renames": [{"from": "A", "to": "B~"}]}),
    ("lib_set_pin_properties", {
        "component_name": "U1",
        "pins": [{"designator": "1", "new_designator": "2~"},
                 {"designator": "3", "name": "X"}]}),
    # The same, behind trailing whitespace: checked raw, "2~ " passed and
    # went out stripped as "2~".
    ("lib_set_pin_properties", {
        "component_name": "U1",
        "pins": [{"designator": "1", "new_designator": "2~ "},
                 {"designator": "3", "name": "X"}]}),
]


@pytest.mark.parametrize("name,kwargs", _CASES,
                         ids=[c[0] for c in _CASES])
def test_a_separator_carrying_value_refuses_and_sends_nothing(
        name, kwargs, monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    fake = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    tools = _tools()

    result = asyncio.run(tools[name](**kwargs))

    assert isinstance(result, dict) and result.get("ok") is False, (
        f"{name} did not refuse: {result}")
    assert "separator" in result.get("reason", "") or "delimiter" in \
        result.get("reason", ""), (
        f"{name} refused without naming the separator hazard: "
        f"{result.get('reason')!r}")
    assert fake.commands == [], (
        f"{name} reached the bridge despite the refusal: {fake.commands}")


def test_clean_values_still_pass_through(monkeypatch, tmp_path):
    """The refusal must not catch ordinary calls: a clean list reaches
    the bridge with the joined field intact."""
    from tests.conftest import install_bridge_fake

    fake = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    tools = _tools()

    asyncio.run(tools["pcb_lock_net_routing"](
        nets=["VCC", "GND"], lock=True))
    assert fake.commands, "the clean call never reached the bridge"
    command, params = fake.commands[-1]
    assert command == "pcb.lock_net_routing"
    assert params["nets"] == "VCC|GND"
