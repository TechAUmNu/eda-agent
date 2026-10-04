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


def test_the_round_two_helpers_are_exported():
    for name in ("join_ops", "OP_SEPARATOR", "PASCAL_INT_MAX",
                 "MAX_COORD_MILS"):
        assert name in payload.__all__


# --- whole_number has a range ---------------------------------------------
# IsIntStr accepts any run of digits and StrToIntDef returns its DEFAULT
# past the 32-bit Integer, so x=1e30 placed a No-ERC at the origin and
# reported it placed.
@pytest.mark.parametrize("value,limit,want", [
    (2 ** 31 - 1, None, 2 ** 31 - 1), (-(2 ** 31 - 1), None, -(2 ** 31 - 1)),
    (2 ** 31, None, None), (1e30, None, None),
    (214748, payload.MAX_COORD_MILS, 214748),
    (-214748, payload.MAX_COORD_MILS, -214748),
    (214749, payload.MAX_COORD_MILS, None)])
def test_whole_number_refuses_what_the_pascal_cannot_hold(value, limit, want):
    got = (whole_number(value) if limit is None
           else whole_number(value, limit))
    assert got == want


def test_the_coordinate_limit_is_the_internal_unit_in_an_integer():
    # 1/10000 mil per unit, 32-bit signed: 214748 mils is the last one.
    assert payload.MAX_COORD_MILS == 214748
    assert payload.MAX_COORD_MILS * 10000 <= payload.PASCAL_INT_MAX
    assert (payload.MAX_COORD_MILS + 1) * 10000 > payload.PASCAL_INT_MAX


def test_no_erc_points_past_the_coordinate_space_are_refused():
    from eda_agent.tools.generic import no_erc_targets_payload

    out, problems = no_erc_targets_payload([{"x": 1e30, "y": 0}])
    assert out == "" and problems
    out, problems = no_erc_targets_payload([{"x": 214749, "y": 0}])
    assert out == "" and problems
    out, problems = no_erc_targets_payload([{"x": 214748, "y": -214748}])
    assert out == "x=214748;y=-214748" and problems == []


def test_a_pin_edit_length_past_the_coordinate_space_is_refused():
    from eda_agent.tools.library import _pin_edits_payload

    out, problems = _pin_edits_payload([{"designator": "1",
                                         "length": 10 ** 9}])
    assert problems and "length" in problems[0]


# --- join_ops: no value can meet the operation separator --------------------
from tests.altium_simulator import _get_batch_field, _iter_batch_ops  # noqa: E402


def test_join_ops_keeps_a_trailing_tilde_inside_its_value():
    from eda_agent.bridge.payload import join_ops

    joined = join_ops(["layer=Top~", "designator=2;x=5"])
    ops = list(_iter_batch_ops(joined))
    assert len(ops) == 2
    assert _get_batch_field(ops[0], "layer") == "Top~"
    assert _get_batch_field(ops[1], "designator") == "2"
    # the bare joiner it replaced lost both
    bare = list(_iter_batch_ops("~~".join(["layer=Top~", "designator=2"])))
    assert _get_batch_field(bare[0], "layer") == "Top"
    assert _get_batch_field(bare[1], "designator") == ""


def _builders():
    """(name, payload) per field-structured library builder, with a
    free-text field ending in '~' as the LAST field of the first op."""
    from eda_agent.tools import library as lib

    out = []
    out.append(("pad edits", lib._pad_edits_payload(
        [{"designator": "1", "layer": "Top~"},
         {"designator": "2", "x": 5}])[0], "designator", "2"))
    out.append(("tracks", lib._tracks_payload(
        [{"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "Top~"},
         {"x1": 7, "y1": 0, "x2": 1, "y2": 1}])[0], "x1", "7"))
    out.append(("fills", lib._fills_payload(
        [{"x1": 0, "y1": 0, "x2": 1, "y2": 1, "layer": "Top~"},
         {"x1": 7, "y1": 0, "x2": 9, "y2": 1, "layer": "Top"}])[0],
        "x1", "7"))
    out.append(("regions", lib._regions_payload(
        [{"vertices": [[0, 0], [1, 0], [1, 1]], "layer": "Top~"},
         {"vertices": [[7, 0], [8, 0], [8, 1]], "layer": "Top"}])[0],
        "pts", "7,0,8,0,8,1"))
    out.append(("pads", lib._pads_payload(
        [{"designator": "1", "layer": "Top~"},
         {"designator": "2"}])[0], "designator", "2"))
    out.append(("pins", lib._pins_payload(
        [{"designator": "1", "electrical_type": "input~"},
         {"designator": "2"}])[0], "designator", "2"))
    out.append(("pin edits", lib._pin_edits_payload(
        [{"designator": "1", "electrical_type": "input~"},
         {"designator": "3", "name": "X"}])[0], "designator", "3"))
    return out


@pytest.mark.parametrize("name,joined,key,want",
                         _builders(), ids=[b[0] for b in _builders()])
def test_a_trailing_tilde_cannot_take_the_next_operation(name, joined, key,
                                                         want):
    """A pad edit whose layer ended in '~' made the NEXT edit's key
    '~designator', read as '' = the footprint's unnamed pads; a track lost
    x1, which StrToFloatDef turned into 0."""
    ops = list(_iter_batch_ops(joined))
    assert len(ops) == 2, (name, joined)
    assert _get_batch_field(ops[1], key) == want, (name, joined)
    assert any(_get_batch_field(ops[0], k).endswith("~")
               for k in ("layer", "electrical_type")), (
        f"{name}: the value's own '~' was lost")


def test_every_library_field_builder_uses_join_ops():
    """The joiner is shared so a builder with a free-text field cannot be
    the one that forgot. A bare '~~'.join in these is the defect."""
    from eda_agent.tools import library as lib

    for fn in (lib._encode_layer_ops, lib._pads_payload,
               lib._pad_edits_payload, lib._tracks_payload,
               lib._fills_payload, lib._regions_payload, lib._pins_payload,
               lib._pin_edits_payload):
        source = inspect.getsource(fn)
        assert "join_ops(" in source, fn.__name__
        assert '"~~".join' not in source, fn.__name__


# --- pin edits check the value they SEND ---------------------------------------
def test_a_pin_edit_designator_is_checked_after_stripping():
    """"2~ " checked raw passed the trailing-'~' rule and went out as "2~",
    which renumbered pin 1 to "2" and cost the next edit its designator."""
    from eda_agent.tools.library import _pin_edits_payload

    for key in ("new_designator", "designator"):
        _, problems = _pin_edits_payload(
            [{"designator": "1", key: "2~ ", "name": "A"}
             if key == "new_designator" else
             {"designator": "2~ ", "name": "A"},
             {"designator": "3", "name": "X"}])
        assert problems and "ends with '~'" in problems[0], (key, problems)


# --- the add-pin builder: numbers by the house guards -------------------------
@pytest.mark.parametrize("bad", [
    {"x": "abc"}, {"y": float("nan")}, {"length": float("inf")},
    {"rotation": "east"}, {"x": 1e30}, {"owner_part_id": True},
    {"owner_part_id": 1.7}, {"owner_part_id": -1}, {"owner_part_id": "a"}])
def test_add_pins_drops_and_counts_a_pin_whose_number_is_not_one(bad):
    from eda_agent.tools.library import _pins_payload

    payload_, skipped = _pins_payload([dict({"designator": "1"}, **bad),
                                       {"designator": "2"}])
    assert skipped == 1, bad
    ops = list(_iter_batch_ops(payload_))
    assert [_get_batch_field(o, "designator") for o in ops] == ["2"]


def test_add_pins_reads_numeric_strings_and_keeps_its_snapping():
    from eda_agent.tools.library import _pins_payload

    payload_, skipped = _pins_payload([{"designator": "1", "x": "149",
                                        "owner_part_id": "2",
                                        "rotation": 90.0}])
    assert skipped == 0
    assert _get_batch_field(payload_, "x") == "100"
    assert _get_batch_field(payload_, "owner_part_id") == "2"
    assert _get_batch_field(payload_, "rotation") == "90"


def test_add_pins_says_why_nothing_was_sent(monkeypatch, tmp_path):
    import asyncio

    from tests.conftest import install_bridge_fake

    fake = _Capture()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    tools = _library_tools()
    out = asyncio.run(tools["lib_add_pins"](
        pins=[{"designator": "1", "owner_part_id": True}]))
    assert out["skipped_invalid"] == 1 and fake.sent == []
    assert "number or flag" in out["error"]


# --- the batch add-pad builder: corner_radius and rotation -------------------
@pytest.mark.parametrize("value,want", [
    ("50", "50"), (50, "50"), (50.0, "50"), (0, "0"), (100, "100")])
def test_batch_pad_corner_radius_is_a_whole_percentage(value, want):
    from eda_agent.tools.library import _pads_payload

    payload_, skipped = _pads_payload([{"designator": "1",
                                        "corner_radius": value}])
    assert skipped == 0
    assert _get_batch_field(payload_, "corner_radius") == want


@pytest.mark.parametrize("value", [50.5, 150, -1, True, "fifty"])
def test_batch_pad_corner_radius_outside_the_rule_drops_the_pad(value):
    """The single-pad tool and the pad edits refuse these; the batch
    rounded 50.5, sent 150, and raised a TypeError on round("50")."""
    from eda_agent.tools.library import _pads_payload

    payload_, skipped = _pads_payload([{"designator": "1",
                                        "corner_radius": value}])
    assert payload_ == "" and skipped == 1


def test_batch_pad_rotation_is_formatted_not_sent_raw():
    """Raw, "1_000" passed float() here and read as 0 in StrToFloatDef."""
    from eda_agent.tools.library import _pads_payload

    payload_, skipped = _pads_payload([{"designator": "1",
                                        "rotation": "1_000"}])
    assert skipped == 0 and _get_batch_field(payload_, "rotation") == "1000"
    payload_, skipped = _pads_payload([{"designator": "1",
                                        "rotation": "45.50"}])
    assert _get_batch_field(payload_, "rotation") == "45.5"


# --- booleans the old truthiness read wrong ----------------------------------
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


class _Capture:
    def __init__(self):
        self.sent: list = []

    async def send_command_async(self, command, params=None, **kw):
        self.sent.append((command, dict(params or {})))
        return {"success": True}


def _run_tool(monkeypatch, tmp_path, name, **kwargs):
    import asyncio

    from tests.conftest import install_bridge_fake

    fake = _Capture()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    return asyncio.run(_library_tools()[name](**kwargs)), fake.sent


def test_symbol_text_reads_the_string_false_as_false(monkeypatch, tmp_path):
    out, sent = _run_tool(monkeypatch, tmp_path, "lib_add_symbol_text",
                          texts=[{"text": "NC", "bold": "false",
                                  "italic": "true"}])
    (_, params), = sent
    op = params["texts"]
    assert _get_batch_field(op, "bold") == "false"
    assert _get_batch_field(op, "italic") == "true"


@pytest.mark.parametrize("bad", [
    {"bold": "maybe"}, {"x": "abc"}, {"rotation": float("nan")},
    {"font_size": 0}, {"font_size": 10.5}, {"owner_part_id": True}])
def test_symbol_text_refuses_what_it_cannot_read(monkeypatch, tmp_path, bad):
    out, sent = _run_tool(monkeypatch, tmp_path, "lib_add_symbol_text",
                          texts=[dict({"text": "NC"}, **bad)])
    assert out["ok"] is False and out["problems"] and sent == []


def test_mech_layer_enabled_reads_the_string_false_as_false():
    from eda_agent.tools.library import _encode_layer_ops

    joined = _encode_layer_ops([{"layer": "Mechanical 1",
                                 "enabled": "false"}])
    assert _get_batch_field(joined, "enabled") == "false"
    refused = _encode_layer_ops([{"layer": "Mechanical 1",
                                  "enabled": "maybe"}])
    assert isinstance(refused, dict) and refused["success"] is False


@pytest.mark.parametrize("entry", [
    {"layer": "Mechanical 1", "name": "A;enabled=false", "enabled": True},
    {"layer": "Mechanical 1", "kind": "assembly;name=X"},
    {"layer": "Mechanical 1;name=X"},
    {"layer": "Mechanical 1", "name": "Courtyard~"}])
def test_mech_layer_names_are_refused_not_sent_raw(entry):
    """name='A;enabled=false' went out as a live enabled=false AHEAD of the
    real flag, and GetBatchField's first match won."""
    from eda_agent.tools.library import _encode_layer_ops

    refused = _encode_layer_ops([entry])
    assert isinstance(refused, dict) and refused["success"] is False
    assert "separator" in refused["error"]


def test_label_format_is_hidden_reads_the_string_false_as_false(
        monkeypatch, tmp_path):
    out, sent = _run_tool(monkeypatch, tmp_path, "lib_set_label_formats",
                          ops=[{"target": "designator",
                                "is_hidden": "false"}])
    (_, params), = sent
    assert _get_batch_field(params["ops"], "is_hidden") == "false"


@pytest.mark.parametrize("bad", [
    {"is_hidden": "maybe"}, {"font_id": "x"}, {"color": 1.5},
    {"orientation": float("inf")}])
def test_label_formats_refuse_what_they_cannot_read(monkeypatch, tmp_path,
                                                    bad):
    out, sent = _run_tool(monkeypatch, tmp_path, "lib_set_label_formats",
                          ops=[dict({"target": "designator"}, **bad)])
    assert out["ok"] is False and sent == []
