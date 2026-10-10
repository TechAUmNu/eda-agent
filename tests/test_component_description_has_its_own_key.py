# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""parameters['Description'] must mean one thing.

Field result, 2026-10-10, bridge 2026.10.08.01 (the live checker on the
multiviewer carrier). ``proj_get_component_info`` returned
``parameters['Description']`` = the COMPONENT's own description when the
part had no Description parameter, and = the parameter's value when it
had one. The handler copied the compiled DM parameter list verbatim, and
that list carries a synthesised "Description" entry. Same key, two
meanings: a caller asking "does this part carry a Description parameter?"
(exactly what the stamp tool's ModifyCompParamIfPresent decides on the
Altium side) got yes for every part with a description.

Now the component description is reported under its own key,
``component_description`` (``IComponent.DM_Description``), and the
parameter dict drops the one entry named Description whose value equals
it, in BOTH the single and the batch handler, through one helper.

Callers checked: nothing in src/ reads ``parameters['Description']`` from
these replies (the orchestrator reads Value/MPN/Manufacturer, the
dashboard displays the dict), so the split changes no consumer.

The Pascal cannot run here. The helper's skip rule is read from the
comment-free source and run over the field cases by a step-for-step
mirror that refuses to run if the rule in the source changes shape.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ROOT = Path(__file__).resolve().parents[1]
_PROJ = _ROOT / "scripts" / "altium" / "Project.pas"
_SRC = _ROOT / "src" / "eda_agent"

SKIP_RULE = re.compile(
    r"If \(Not SkippedCopy\) And \(UpperCase\(PName\) = 'DESCRIPTION'\)\s*"
    r"And \(PValue = CompDesc\) Then\s*Begin\s*SkippedCopy := True;\s*"
    r"Continue;\s*End;")


def _fns() -> dict[str, str]:
    return load(_PROJ)


def _reply(dm_params: list[tuple[str, str]], dm_description: str) -> dict:
    """{component_description, parameters} as the handler now builds them.

    On a build without the helper this falls back to the verbatim copy
    the field saw (and no component_description), so the field cases fail
    there.
    """
    fns = _fns()
    helper = fns.get("ComponentParamsJson")
    if helper is None or not SKIP_RULE.search(helper):
        params: dict[str, str] = {}
        for name, value in dm_params:
            params[name] = value          # JSON object: last key wins
        return {"parameters": params}
    params = {}
    skipped = False
    for name, value in dm_params:
        if not skipped and name.upper() == "DESCRIPTION" \
                and value == dm_description:
            skipped = True
            continue
        params[name] = value
    return {"component_description": dm_description, "parameters": params}


DESC = "LDO 3 A, adjustable"


def test_no_description_parameter_means_no_description_key():
    """The field case: the part has only a component description."""
    out = _reply([("Description", DESC), ("Value", "TPS7A83")], DESC)
    assert "Description" not in out["parameters"], (
        "the component description masqueraded as a parameter")
    assert out.get("component_description") == DESC


def test_a_real_description_parameter_stays_a_parameter():
    out = _reply([("Description", "regulator, see datasheet")], DESC)
    assert out["parameters"]["Description"] == "regulator, see datasheet"
    assert out.get("component_description") == DESC


def test_copy_and_real_entry_together_keep_the_real_one():
    out = _reply([("Description", DESC),
                  ("Description", "regulator, see datasheet")], DESC)
    assert out["parameters"] == {"Description": "regulator, see datasheet"}
    assert out.get("component_description") == DESC


def test_other_parameters_are_untouched():
    out = _reply([("Manufacturer", "TI"), ("Value", DESC)], DESC)
    assert out["parameters"] == {"Manufacturer": "TI", "Value": DESC}, (
        "only a parameter NAMED Description may be dropped")


def test_both_handlers_use_the_helper_and_report_the_key():
    fns = _fns()
    helper = fns["ComponentParamsJson"]
    assert SKIP_RULE.search(helper)
    for name in ("Proj_GetComponentInfo", "Proj_GetComponentInfoBatch"):
        body = fns[name]
        assert "Try CompDesc := Comp.DM_Description; Except CompDesc := " \
               "''; End;" in body, name
        assert "ComponentParamsJson(Comp, CompDesc)" in body, name
        assert '"component_description":"\' + EscapeJsonString(CompDesc)' \
            in body, name
        assert "Comp.DM_Parameters(K)" not in body, (
            f"{name} still copies the DM list itself")

    text = _PROJ.read_text(encoding="utf-8")
    assert text.index("Function ComponentParamsJson(") < \
        text.index("Function Proj_GetComponentInfo(") < \
        text.index("Function Proj_GetComponentInfoBatch("), (
            "DelphiScript needs the callee declared first")


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


def test_the_tool_docstrings_name_the_key():
    src = (_SRC / "tools" / "project.py").read_text(encoding="utf-8")
    for tool in ("async def proj_get_component_info(",
                 "async def proj_get_component_info_many("):
        start = src.index(tool)
        body = src[start:src.index("@mcp.tool()", start)]
        assert "component_description" in body, tool
