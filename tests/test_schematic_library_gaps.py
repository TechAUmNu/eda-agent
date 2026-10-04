# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Guards for the schematic-library and sheet gaps closed together.

lib_set_component_kind, lib_set_pin_properties, lib_set_current_model,
sch_place_no_ercs, sch_get_no_erc_markers, sch_reload_document and the
lib_link_footprint current-model fix. Python halves are driven; Pascal
halves are pinned by reading the source, which is the only option
without a live editor.

Mutate each before trusting it (CONTRIBUTING.md): delete the demotion
loop in Lib_LinkFootprint, swap Client.CloseDocument for
WorkspaceManager:CloseObject, add eComponentKind_Jumper to
Lib_SetComponentKind, drop 'jumper' from ComponentKindOrdinal. Each must
fail one test below.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from eda_agent.tools import generic as generic_module
from eda_agent.tools import library as library_module
from eda_agent.tools.library import (
    _COMPONENT_KINDS, _pin_edits_payload, canonical_component_kind)
from eda_agent.tools.generic import (
    annotate_no_erc_placement, no_erc_erc_cross_check,
    no_erc_targets_payload, saved_no_erc_report, summarise_erc_violations)
from tests.pascal_source import load

PASCAL = Path(__file__).resolve().parents[1] / "scripts" / "altium"


@pytest.fixture(scope="module")
def library_fns() -> dict[str, str]:
    return load(PASCAL / "Library.pas")


@pytest.fixture(scope="module")
def generic_fns() -> dict[str, str]:
    return load(PASCAL / "Generic.pas")


# --------------------------------------------------------- pin edits

def test_a_pin_edit_emits_only_the_fields_given():
    payload, problems = _pin_edits_payload([
        {"designator": "3", "name": "", "electrical_type": "input",
         "show_name": False, "length": 200.0}])
    assert problems == []
    assert payload == ("designator=3;name=;electrical_type=input;"
                       "show_name=false;length=200")


@pytest.mark.parametrize("length", [200.4, float("inf"), float("nan"), True,
                                    0, -100, "long"])
def test_a_pin_length_that_is_not_a_whole_positive_number_is_refused(length):
    """inf raised OverflowError past the except, True became 1 and 200.4
    was rounded to 200, where the Pascal (IsIntStr) would have refused it.
    """
    payload, problems = _pin_edits_payload(
        [{"designator": "3", "length": length}])
    assert payload == ""
    assert problems and "length must be a whole number" in problems[0]


@pytest.mark.parametrize("owner", [True, 1.5, float("inf"), -1])
def test_an_owner_part_id_that_is_not_a_whole_number_is_refused(owner):
    _payload, problems = _pin_edits_payload(
        [{"designator": "3", "owner_part_id": owner, "name": "X"}])
    assert problems and "owner_part_id" in problems[0]


@pytest.mark.parametrize("value,wire", [
    (False, "false"), ("false", "false"), ("0", "false"), (0, "false"),
    (True, "true"), ("true", "true"), ("1", "true"), ("YES", "true")])
def test_pin_flags_parse_by_the_one_boolean_rule(value, wire):
    """Truthiness sent show_name=true for the string 'false'."""
    payload, problems = _pin_edits_payload(
        [{"designator": "3", "show_name": value, "hidden": value}])
    assert problems == []
    assert f"show_name={wire}" in payload and f"hidden={wire}" in payload


def test_a_pin_flag_that_is_not_a_boolean_is_refused():
    _payload, problems = _pin_edits_payload(
        [{"designator": "3", "hidden": "maybe"}])
    assert problems and "hidden must be true or false" in problems[0]


@pytest.mark.parametrize("key", ["new_designator", "name"])
def test_a_trailing_tilde_cannot_run_into_the_op_separator(key):
    """NextBatchOp splits on the FIRST '~~': a value ending '~' that closes
    an op read as the value without it plus a broken '~op'. Any problem
    refuses the whole call, so the partial payload is never sent."""
    edit = {"designator": "1", key: "B~"}
    _payload, problems = _pin_edits_payload([edit, {"designator": "2",
                                                    "name": "C"}])
    assert problems and "ends with '~'" in problems[0]


def test_a_leading_tilde_overbar_name_still_passes():
    payload, problems = _pin_edits_payload(
        [{"designator": "1", "name": "~RESET"}])
    assert problems == [] and payload == "designator=1;name=~RESET"


def test_a_mistyped_key_is_refused_with_the_right_one():
    _payload, problems = _pin_edits_payload(
        [{"designator": "1", "nmae": "EN"}])
    assert problems and "'nmae'" in problems[0] and "'name'" in problems[0]


def test_an_edit_that_changes_nothing_is_refused():
    _payload, problems = _pin_edits_payload([{"designator": "1"}])
    assert problems and "nothing to change" in problems[0]


@pytest.mark.parametrize("key", ["designator", "new_designator", "name"])
def test_a_separator_in_a_selecting_field_is_refused(key):
    edit = {"designator": "1", "name": "X"}
    edit[key] = "A;x=2"
    _payload, problems = _pin_edits_payload([edit])
    assert any("separator" in p for p in problems)


# ------------------------------------------------------ component kind

def test_every_python_kind_is_accepted_by_the_pascal(library_fns):
    body = library_fns["ComponentKindOrdinal"]
    accepted = set(re.findall(r"N = '([a-z_]+)'", body))
    assert len(accepted) >= 7, "parsed almost nothing out of the Pascal"
    assert set(_COMPONENT_KINDS.values()) <= accepted


def test_the_pascal_names_round_trip_to_python(library_fns):
    # Whole literals only: the fallback is 'ordinal_' + IntToStr(...),
    # which a bare quoted-word pattern would read as a name.
    emitted = set(re.findall(r"Result := '([a-z_]+)'\s*(?:;|$)",
                             library_fns["ComponentKindName"], re.M))
    emitted.discard("unreadable")
    assert emitted == set(_COMPONENT_KINDS.values())


def test_properties_panel_words_fold_to_canonical_kinds():
    assert canonical_component_kind("Net Tie (In BOM)") == "net_tie_bom"
    assert canonical_component_kind("Net Tie") == "net_tie_no_bom"
    assert canonical_component_kind("Standard (No BOM)") == "standard_no_bom"
    assert canonical_component_kind("widget") is None


def test_only_proven_kind_constants_are_spelled(library_fns):
    """The five unproven kinds go by ordinal; a named one would be an
    identifier nothing in this codebase has executed."""
    body = library_fns["Lib_SetComponentKind"]
    names = set(re.findall(r"\beComponentKind_\w+", body))
    assert names == {"eComponentKind_NetTie_BOM",
                     "eComponentKind_NetTie_NoBOM"}


# -------------------------------------------------------- current model

def test_link_footprint_demotes_before_it_adds(library_fns):
    body = library_fns["Lib_LinkFootprint"]
    assert "IsCurrent := False" in body
    assert body.index("IsCurrent := False") < body.index(
        "AddSchImplementation"), (
        "the demotion must run before the add, so the new model is never "
        "compared against the old ones")
    assert "ExtractJsonValue(Params, 'set_current')" in body


# --------------------------------------------------------------- reload

def test_reload_closes_by_object_after_a_proven_save(generic_fns):
    body = generic_fns["Gen_ReloadSchDocument"]
    assert "Client.CloseDocument" in body
    assert "WorkspaceManager:CloseObject" not in body, (
        "CloseObject acts on the FOCUSED document")
    assert body.index("SAVE_FAILED") < body.index("Client.CloseDocument")
    assert body.index("'MODIFIED'") < body.index("Client.CloseDocument")


# --------------------------------------------------------------- No-ERC

@pytest.mark.parametrize("target", [
    {"x": 12.7, "y": 0}, {"x": True, "y": 0}, {"x": float("inf"), "y": 0},
    {"x": 1, "y": float("nan")}, {"x": "a", "y": 0}, {"x": 1}])
def test_no_erc_points_must_be_whole_finite_mils(target):
    """int() truncated 12.7 to 12, turned True into 1, and raised
    OverflowError on an infinity, past the except."""
    payload, problems = no_erc_targets_payload([target])
    assert payload == ""
    assert problems and "whole-number" in problems[0]


def test_no_erc_pin_ending_in_tilde_is_refused():
    _p, problems = no_erc_targets_payload(
        [{"designator": "U1", "pin": "4~"}, {"designator": "U1", "pin": "5"}])
    assert problems and "ends with '~'" in problems[0]


def test_no_erc_targets_prefer_pins_and_refuse_separators():
    payload, problems = no_erc_targets_payload(
        [{"designator": "U7", "pin": "23"}, {"x": 100, "y": 200}])
    assert problems == []
    assert payload == "designator=U7;pin=23~~x=100;y=200"
    _p, problems = no_erc_targets_payload([{"designator": "U7~~x", "pin": "1"}])
    assert problems and "separator" in problems[0]


def test_a_marker_on_a_root_is_called_out():
    out = annotate_no_erc_placement(
        {"on_pin_end": [], "on_pin_root": [{"pin": "1"}],
         "phantom_pin_end": []})
    assert "ROOT" in out["warning"]
    clean = annotate_no_erc_placement({"on_pin_end": [{"pin": "1"}]})
    assert "warning" not in clean


def test_saved_report_reads_the_record_verbatim(monkeypatch):
    import eda_agent.fileio.altium_sch as reader
    monkeypatch.setattr(reader, "read_schdoc_records", lambda p: [
        {"RECORD": "22", "Location.X": "600", "Location.Y": "510",
         "IsActive": "T", "SuppressAll": "T", "Symbol": "Thin Cross"},
        {"RECORD": "22", "Location.X": "601", "Location.Y": "510",
         "IsActive": "F", "SuppressAll": "T", "Symbol": "Thin Cross"}])
    rep = saved_no_erc_report("x.SchDoc", [{"x": 6000, "y": 5100}])
    assert rep["saved_count"] == 2
    assert len(rep["field_variants"]) == 2, "the odd one must stand out"
    assert rep["saved_not_in_memory"] == [[6010, 5100]]
    assert rep["in_memory_not_saved"] == []


def test_saved_report_normalises_both_sides_the_same_way(monkeypatch):
    """Saved positions were rounded and live ones truncated, so a marker at
    a fractional mil showed up as unsaved AND lost; a None x raised."""
    import eda_agent.fileio.altium_sch as reader
    monkeypatch.setattr(reader, "read_schdoc_records", lambda p: [
        {"RECORD": "22", "Location.X": "600", "Location.X_Frac": "67000",
         "Location.Y": "510"}])
    rep = saved_no_erc_report("x.SchDoc",
                              [{"x": 6006.7, "y": 5100},
                               {"x": None, "y": 1}])
    assert rep["in_memory_not_saved"] == []
    assert rep["saved_not_in_memory"] == []
    assert rep["unreadable_live_markers"] == 1


def test_cross_check_matches_whole_tokens_only():
    markers = [{"on_pin_end": [{"designator": "U1", "pin": "1"}]}]
    erc = {"violation_count": 2, "violations": [
        {"index": 0, "description": "Floating input pins",
         "related_objects": [{"cross_probe": "U1-1"}]},
        {"index": 1, "description": "Net with only one pin",
         "related_objects": [{"cross_probe": "U11-12"}]}]}
    out = no_erc_erc_cross_check(markers, erc)
    assert [s["violation_index"] for s in out["still_reported"]] == [0]


def test_erc_summary_flags_a_truncated_read():
    out = summarise_erc_violations({"violation_count": 3, "violations": [
        {"description": "Floating input pins: U1-1"},
        {"description": "Floating input pins: U2-4"}]})
    assert out["listed"] == 2 and out["truncated"] is True
    assert out["by_description_prefix"] == {"Floating input pins": 2}


# --------------------------------------------- refusals reach nothing

class _Capture:
    def __init__(self):
        self.commands = []

    async def send_command_async(self, command, params=None, **kw):
        self.commands.append((command, params or {}))
        return {"ok": True, "success": True}


def _tools():
    captured = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    library_module.register_library_tools(_Mcp())
    generic_module.register_generic_tools(_Mcp())
    return captured


@pytest.mark.parametrize("name,kwargs", [
    ("lib_set_pin_properties", {"component_name": "U", "pins": []}),
    ("lib_set_component_kind", {"component_name": "U", "kind": "widget"}),
    ("sch_place_no_erc", {}),
    ("sch_place_no_ercs", {"targets": [{"pin": "1"}]}),
])
def test_a_refusal_sends_nothing(name, kwargs, monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake
    fake = _Capture()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    result = asyncio.run(_tools()[name](**kwargs))
    assert result.get("ok") is False
    assert fake.commands == []
