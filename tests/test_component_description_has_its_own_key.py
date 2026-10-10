# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""parameters['Description'] must mean one thing, and must not lose a real one.

Field result, 2026-10-10, bridge 2026.10.08.01 (the live checker on the
multiviewer carrier). ``proj_get_component_info`` returned
``parameters['Description']`` = the COMPONENT's own description when the
part had no Description parameter, and = the parameter's value when it
had one. The handler copied the compiled DM parameter list verbatim, and
that list carries a synthesised "Description" entry. Same key, two
meanings: a caller asking "does this part carry a Description parameter?"
(exactly what the stamp tool's ModifyCompParamIfPresent decides on the
Altium side) got yes for every part with a description.

The first fix reported the component description under its own key,
``component_description`` (``IComponent.DM_Description``), and dropped
the first Description entry whose VALUE equalled it. Review found the
inverse bug in the bridge's own normal state: ``sch_set_components_
parameters``' Description key writes the component description AND any
Description parameter the part already carries, so after a stamp the two
are equal ON PURPOSE. A part with ONE real Description parameter then lost
it from ``parameters`` and the reply said it had none.

Now the copy is chosen by STRUCTURE. Two or more Description entries: one
is the copy, drop one equal to the description. Exactly one, with
different text: it is real, keep it. Exactly one with the same text: the
placed part on its sheet decides (the walk ModifyCompParamIfPresent
makes); with the sheet not open the entry is KEPT and
``description_parameter_present`` is null instead of a guess. The fields
are built only when parameters are asked for, so a
``with_parameters=false`` call never reaches the first-use
``DM_Description`` read.

The Pascal cannot run here. The helper's decision lines are read from the
comment-free source and run over the field cases by a step-for-step mirror
that refuses to run if those lines change shape; on a build without them
the mirror falls back to what that build did, so the field cases fail
there (carrier-local e0dd714: verbatim copy; the first fix: drop by value).
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_PROJ = _ROOT / "scripts" / "altium" / "Project.pas"
_GEN = _ROOT / "scripts" / "altium" / "Generic.pas"
_SRC = _ROOT / "src" / "eda_agent"

HELPER = "ComponentParamFieldsJson"

# The decision, as it must read in the helper.
COUNT_RULE = re.compile(
    r"If UpperCase\(Comp\.DM_Parameters\(K\)\.DM_Name\) = 'DESCRIPTION' Then"
    r"\s*Begin\s*Inc\(DescCount\);\s*"
    r"SingleCopy := Comp\.DM_Parameters\(K\)\.DM_Value = CompDesc;\s*End;")
SINGLE_RULE = re.compile(r"If DescCount <> 1 Then SingleCopy := False;")
STATE_RULE = re.compile(
    r"If SingleCopy Then\s*Begin.*?"
    r"State := SchDescriptionParamState\(SheetPath, LogDes\);\s*End;", re.S)
DROP_RULE = re.compile(
    r"DropCopy := \(DescCount >= 2\) Or \(SingleCopy And \(State = 0\)\);")
PRESENT_RULE = re.compile(
    r"If DescCount = 0 Then PresentJson := 'false'\s*"
    r"Else If Not SingleCopy Then PresentJson := 'true'\s*"
    r"Else If State = 1 Then PresentJson := 'true'\s*"
    r"Else If State = 0 Then PresentJson := 'false'\s*"
    r"Else PresentJson := 'null';")
SKIP_RULE = re.compile(
    r"If DropCopy And \(Not Dropped\) And \(UpperCase\(PName\) = 'DESCRIPTION'\)"
    r"\s*And \(PValue = CompDesc\) Then\s*Begin\s*Dropped := True;\s*"
    r"Continue;\s*End;")

# The first fix's rule (drop the first equal entry, whatever the structure).
OLD_SKIP_RULE = re.compile(
    r"If \(Not SkippedCopy\) And \(UpperCase\(PName\) = 'DESCRIPTION'\)\s*"
    r"And \(PValue = CompDesc\) Then")


def _fns() -> dict[str, str]:
    return load(_PROJ)


def _reply(dm_params: list[tuple[str, str]], dm_description: str,
           sheet_state: int = -1) -> dict:
    """{component_description, description_parameter_present, parameters}
    as the handler builds them. ``sheet_state`` is what
    SchDescriptionParamState answers for the placed part: 1 carries a
    Description parameter, 0 carries none, -1 cannot tell.

    Falls back to the older builds' behaviour when the rule is not in the
    source, so the cases below fail there.
    """
    fns = _fns()
    helper = fns.get(HELPER)
    if helper is not None and all(r.search(helper) for r in (
            COUNT_RULE, SINGLE_RULE, STATE_RULE, DROP_RULE, PRESENT_RULE,
            SKIP_RULE)):
        desc_count = 0
        single = False
        for name, value in dm_params:
            if name.upper() == "DESCRIPTION":
                desc_count += 1
                single = value == dm_description
        if desc_count != 1:
            single = False
        state = sheet_state if single else -1
        drop = desc_count >= 2 or (single and state == 0)
        if desc_count == 0:
            present = False
        elif not single:
            present = True
        elif state == 1:
            present = True
        elif state == 0:
            present = False
        else:
            present = None
        params: dict[str, str] = {}
        dropped = False
        for name, value in dm_params:
            if drop and not dropped and name.upper() == "DESCRIPTION" \
                    and value == dm_description:
                dropped = True
                continue
            params[name] = value
        return {"component_description": dm_description,
                "description_parameter_present": present,
                "parameters": params}

    old = fns.get("ComponentParamsJson")
    params = {}
    if old is not None and OLD_SKIP_RULE.search(old):
        skipped = False
        for name, value in dm_params:
            if not skipped and name.upper() == "DESCRIPTION" \
                    and value == dm_description:
                skipped = True
                continue
            params[name] = value
        return {"component_description": dm_description,
                "parameters": params}
    for name, value in dm_params:
        params[name] = value          # JSON object: last key wins
    return {"parameters": params}


DESC = "LDO 3 A, adjustable"


def test_no_description_parameter_means_no_description_key():
    """The field case: the part has only a component description, and its
    sheet confirms it carries no Description parameter."""
    out = _reply([("Description", DESC), ("Value", "TPS7A83")], DESC,
                 sheet_state=0)
    assert "Description" not in out["parameters"], (
        "the component description masqueraded as a parameter")
    assert out.get("component_description") == DESC
    assert out.get("description_parameter_present") is False


def test_a_single_real_parameter_equal_to_the_description_stays():
    """The review case. After ``sch_set_components_parameters`` stamps
    Description on R2 (which carries a real Description parameter), the
    component description and the parameter hold the SAME text. The DM
    list has one Description entry, equal to the description, and the
    sheet shows R2 carries the parameter: it must stay. The first fix
    dropped it and said R2 had none."""
    out = _reply([("Description", "Res; new"), ("Value", "10k")],
                 "Res; new", sheet_state=1)
    assert out["parameters"].get("Description") == "Res; new", (
        "a REAL Description parameter was dropped because its text equals "
        "the component description")
    assert out.get("description_parameter_present") is True


def test_an_unopened_sheet_keeps_the_entry_and_says_unknown():
    """Not a guess either way: a real parameter is never dropped on one."""
    out = _reply([("Description", DESC)], DESC, sheet_state=-1)
    assert out["parameters"].get("Description") == DESC
    assert "description_parameter_present" in out
    assert out["description_parameter_present"] is None


def test_two_equal_entries_after_a_stamp_keep_one():
    """The copy and a real parameter, both stamped to the same text."""
    out = _reply([("Description", "Res; new"), ("Description", "Res; new")],
                 "Res; new", sheet_state=-1)
    assert out["parameters"] == {"Description": "Res; new"}
    assert out.get("description_parameter_present") is True


def test_a_real_description_parameter_stays_a_parameter():
    out = _reply([("Description", "regulator, see datasheet")], DESC)
    assert out["parameters"]["Description"] == "regulator, see datasheet"
    assert out.get("component_description") == DESC
    assert out.get("description_parameter_present") is True


def test_copy_and_real_entry_together_keep_the_real_one():
    out = _reply([("Description", DESC),
                  ("Description", "regulator, see datasheet")], DESC)
    assert out["parameters"] == {"Description": "regulator, see datasheet"}
    assert out.get("component_description") == DESC
    assert out.get("description_parameter_present") is True


def test_no_entry_at_all_is_a_plain_false():
    out = _reply([("Value", "10k")], "", sheet_state=-1)
    assert out["parameters"] == {"Value": "10k"}
    assert out.get("description_parameter_present") is False


def test_other_parameters_are_untouched():
    out = _reply([("Manufacturer", "TI"), ("Value", DESC)], DESC)
    assert out["parameters"] == {"Manufacturer": "TI", "Value": DESC}, (
        "only a parameter NAMED Description may be dropped")


def test_the_sheet_check_is_the_stamp_tools_own_walk():
    """It must decide exactly as ModifyCompParamIfPresent does, or the
    reply and the stamp disagree about whether a part carries one."""
    fns = _fns()
    walk = fns["SchDescriptionParamState"]
    stamp = load(_GEN)["ModifyCompParamIfPresent"]
    for piece in ("SchIterator_Create", "MkSet(eParameter)"):
        assert piece in walk and piece in stamp, piece
    assert "If UpperCase(Param.Name) = 'DESCRIPTION' Then HasReal := True;" \
        in walk
    # An unopened sheet (GetSchDocumentByPath answers Nil) is unknown.
    assert re.search(r"If SchDoc = Nil Then Exit;", walk)
    assert walk.index("Result := -1;") < walk.index("If SchDoc = Nil Then Exit;")
    assert re.search(r"Else If Failed Or \(Not FoundPart\) Then Result := -1",
                     walk)

    text = _PROJ.read_text(encoding="utf-8")
    assert text.index("Function SchDescriptionParamState(") < \
        text.index(f"Function {HELPER}(") < \
        text.index("Function Proj_GetComponentInfo("), (
            "DelphiScript needs the callee declared first")


def test_both_handlers_use_the_helper_only_when_asked():
    """DM_Description is a first use on IComponent (37.6): it must sit in
    the helper, reached only with with_parameters, never in a handler."""
    fns = _fns()
    assert "Try CompDesc := Comp.DM_Description; Except CompDesc := " \
           "''; End;" in fns[HELPER]
    for name in ("Proj_GetComponentInfo", "Proj_GetComponentInfoBatch"):
        body = fns[name]
        assert re.search(
            rf"If WithParameters Then\s*ParamList := {HELPER}\(Comp, "
            r"Doc\.DM_FullPath\);", body), name
        assert "DM_Description" not in body, (
            f"{name} reads DM_Description outside the with_parameters gate")
        assert "Comp.DM_Parameters(K)" not in body, (
            f"{name} still copies the DM list itself")
    assert '\',"component_description":"\' + EscapeJsonString(CompDesc)' \
        in fns[HELPER]
    assert "',\"description_parameter_present\":' + PresentJson" \
        in fns[HELPER]

    text = _PROJ.read_text(encoding="utf-8")
    assert text.index(f"Function {HELPER}(") < \
        text.index("Function Proj_GetComponentInfo(") < \
        text.index("Function Proj_GetComponentInfoBatch(")


def test_no_caller_reads_the_old_meaning():
    """The split must not strand a consumer of parameters['Description']."""
    hits = []
    for path in _SRC.rglob("*.py"):
        # Prose quoting the old key in ``double backticks`` is not a reader.
        src = re.sub(r"``[^`]*``", "", path.read_text(encoding="utf-8"))
        if re.search(r"""parameters["']?\]?\s*(?:\.get\(|\[)\s*["']"""
                     r"""Description["']""", src) or \
                re.search(r"""params(?:_dict)?\.get\(\s*["']Description["']""",
                          src):
            hits.append(path.name)
    assert hits == [], hits


def test_the_dashboard_drawer_still_shows_the_description(tmp_path,
                                                          monkeypatch):
    """The component drawer's core field reads ``c.description`` and the
    parameter table shows ``c.parameters``. Parts without a Description
    parameter used to show their description through the synthesised
    entry; with it gone the drawer must take ``component_description``."""
    from eda_agent.web import dashboard

    reply = {"components": [{
        "designator": "U7", "comment": "TPS7A83",
        "component_description": DESC,
        "description_parameter_present": False,
        "parameters": {"Value": "TPS7A83"}, "pins": []}]}
    monkeypatch.setattr(dashboard, "_bridge_call",
                        lambda *a, **k: reply)
    monkeypatch.setattr(dashboard, "_cache_peek", lambda *a, **k: None)
    app = dashboard.create_app(workspace_dir=tmp_path)
    app.testing = True
    got = app.test_client().get("/api/component/U7").get_json()
    assert got["ok"] is True
    assert got["data"]["description"] == DESC


def test_the_tool_docstrings_name_the_key():
    src = (_SRC / "tools" / "project.py").read_text(encoding="utf-8")
    for tool in ("async def proj_get_component_info(",
                 "async def proj_get_component_info_many("):
        start = src.index(tool)
        body = src[start:src.index("@mcp.tool()", start)]
        assert "component_description" in body, tool
        assert "description_parameter_present" in body, tool
