# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D3 and D4 of the 2026-10-11 live run.

D3  A ONE-operation obj_batch_modify was parsed by the legacy positional
    parser (no '~~' in the payload) and answered unknown_object_type with
    object_type 'object_type=eSchComponent'.
D4  ops_mark_not_read_back counted an op that matched nothing.

Gen_BatchModify is EXECUTED whole: the evaluator has no Var parameters, so
its Var-parameter callees share their variables with it by name. Each test
fails on ef2d79b.
"""
from __future__ import annotations

import asyncio
import json
import re

from eda_agent.tools import generic as generic_mod
from eda_agent.tools.registry import ToolRegistry
from tests.live_2026_10_11 import _MAIN, _UTILS
from tests.pascal_eval import build
from tests.pascal_source import load
from tests.test_bridge_record_names_one_file import _ALTIUM


# ---------------------------------------------------------------------------
# D3 / D4. obj_batch_modify framing and the per-op mark count
# ---------------------------------------------------------------------------

_GENERIC = load(_ALTIUM / "Generic.pas", minimum=40)


def _rewrite_var_params(body, names):
    """Var parameters made globals of the namespace: the evaluator has no
    Var parameters, so caller and callee share the variable by name."""
    for n in names:
        body = re.sub(r"\bVar\s+" + n + r"\s*:\s*String", "X" + n + " : String", body)
    return body


def _drop_locals(body, names):
    head, rest = body.split("Begin", 1)
    for n in names:
        head = re.sub(r"\b" + n + r"\s*,\s*", "", head)
        head = re.sub(r",\s*" + n + r"\b", "", head)
        head = re.sub(r"^\s*" + n + r"\s*:\s*String;\s*$", "", head, flags=re.M)
    head = re.sub(r"^\s*:\s*String;\s*$", "", head, flags=re.M)
    return head + "Begin" + rest


def _batch_ns(results):
    shared = ["Remaining", "Scope", "ObjTypeStr", "FilterStr", "SetStr",
              "ScopeType", "ScopePath"]
    bodies = {
        "Gen_BatchModify": _drop_locals(_GENERIC["Gen_BatchModify"], shared),
        "SplitLegacyModifyOp": _rewrite_var_params(
            _GENERIC["SplitLegacyModifyOp"],
            ["Scope", "ObjTypeStr", "FilterStr", "SetStr"]),
        "ParseScope": _rewrite_var_params(_GENERIC["ParseScope"],
                                          ["ScopeType", "ScopePath"]),
        "NextBatchOp": _rewrite_var_params(_MAIN["NextBatchOp"], ["Remaining"]),
        "GetBatchField": _MAIN["GetBatchField"],
        # Upstream (2026.10.25 merge): set is read with GetBatchTail, so a
        # value may hold ';' (Python writes set last).
        "GetBatchTail": _MAIN["GetBatchTail"],
        "MarkedOpJson": _UTILS["MarkedOpJson"],
    }
    calls = []

    def process(scope_path, obj, filt, _a, set_str, mode, rid, _z):
        calls.append((scope_path, obj, filt, set_str))
        return results.get(filt, '{"success":true,"matched":0}')

    def extract(text, key):
        def find(o):
            if isinstance(o, dict):
                if key in o:
                    return o[key]
                for v in o.values():
                    hit = find(v)
                    if hit is not None:
                        return hit
            return None
        v = find(json.loads(text))
        if v is None:
            return ""
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    ns = dict(Remaining="", Scope="", ObjTypeStr="", FilterStr="", SetStr="",
              ScopeType="", ScopePath="")
    ns.update(
        ExtractJsonValue=extract,
        ResetPropertyDiag=lambda _d: None,
        ObjectTypeFromString=lambda s: 1 if s == "eSchComponent" else -1,
        ObjectTypeFromStringPCB=lambda s: -1,
        ApplyLibComponentScope=lambda t, p: True,
        ProcessDocByPath=process,
        ProcessActiveDoc=lambda *a: process("", *a),
        IterateProjectDocs=lambda obj, filt, a, s, mode, rid, sp, z: process(
            sp, obj, filt, a, s, mode, rid, z),
        ProcessActivePCBDoc=lambda *a: process("", *a),
        MarkSaveHint=lambda m, lib: "save hint",
        EscapeJsonString=lambda s: s.replace("\\", "\\\\").replace('"', '\\"'),
        RenderPropertyDiagJson=lambda _d: "[]",
        BuildSuccessResponse=lambda rid, data: '{"success":true,"data":' + data + '}',
        BuildErrorResponse=lambda rid, code, msg: json.dumps(
            {"success": False, "code": code, "message": msg}))
    build(bodies, list(bodies), ns)
    return ns, calls


SHEET_OP = (r"scope=doc:C:\scratch\s38_X.SchDoc;object_type=eSchComponent;"
            r"filter=Designator.Text=D101;set=Location.X=5400")
NOMATCH_OP = (r"scope=doc:C:\scratch\s38_X.SchDoc;object_type=eSchComponent;"
              r"filter=Designator.Text=NOPE;set=Location.X=1")
_HIT = '{"success":true,"matched":1,"marked_modified":true}'
_UNREAD = '{"success":true,"matched":1,"marked_modified":false}'
_MISS = '{"success":true,"matched":0,"marked_modified":false}'


def _batch(payload, results):
    ns, calls = _batch_ns(results)
    reply = json.loads(ns["Gen_BatchModify"](json.dumps({"operations": payload}), "r"))
    return reply["data"], calls


def test_a_one_op_batch_without_a_separator_is_parsed_as_tilde_framing():
    """The payload a client before .02 sends for ONE op: no '~~'."""
    data, calls = _batch(SHEET_OP, {"Designator.Text=D101": _HIT})
    assert data["operations_processed"] == 1, data
    row, = data["results"]
    assert row["object_type"] == "eSchComponent"
    assert row["note"] == ""
    assert calls == [(r"C:\scratch\s38_X.SchDoc", 1, "Designator.Text=D101",
                      "Location.X=5400")]


def test_a_trailing_separator_is_one_op_not_two():
    data, calls = _batch(SHEET_OP + "~~", {"Designator.Text=D101": _HIT})
    assert data["operations_processed"] == 1
    assert data["operations_skipped"] == 0
    assert len(calls) == 1


def test_the_legacy_positional_form_still_parses():
    data, calls = _batch(r"doc:C:\x.SchDoc;eSchComponent;Designator.Text=D101;Location.X=1",
                         {"Designator.Text=D101": _HIT})
    assert data["operations_processed"] == 1
    assert calls[0][2] == "Designator.Text=D101"


def test_a_zero_match_op_is_not_a_mark_that_failed_to_read_back():
    data, _ = _batch(SHEET_OP + "~~" + NOMATCH_OP,
                     {"Designator.Text=D101": _UNREAD, "Designator.Text=NOPE": _MISS})
    assert data["ops_mark_not_read_back"] == 1, data
    hit, miss = data["results"]
    assert hit["marked_modified"] is False
    assert miss["matched"] == 0 and miss["marked_modified"] is None


def _tools():
    registry = ToolRegistry()
    generic_mod.register_generic_tools(registry)
    return {t.name: t.fn for t in asyncio.run(registry.list_tools())}


class _Recorder:
    def __init__(self):
        self.calls = []

    async def send_command_async(self, command, params, *a, **k):
        self.calls.append((command, params))
        return {"success": True}


def test_the_client_frames_a_single_op_too(monkeypatch):
    """So a bridge before .02 (which needs a '~~' to pick the tilde parser)
    also takes a one-op batch."""
    bridge = _Recorder()
    monkeypatch.setattr(generic_mod, "get_bridge", lambda: bridge)
    asyncio.run(_tools()["obj_batch_modify"](operations=[
        {"scope": "active_doc", "object_type": "eSchComponent",
         "filter": "Designator.Text=D101", "set": "Location.X=5400"}]))
    (command, params), = bridge.calls
    assert command == "generic.batch_modify"
    assert params["operations"].endswith("~~")
    assert params["operations"].count("~~") == 1


def test_next_batch_op_skips_the_empty_op_after_a_trailing_separator():
    ns = {"Remaining": "a=1~~"}
    build({"NextBatchOp": _rewrite_var_params(_MAIN["NextBatchOp"], ["Remaining"])},
          ["NextBatchOp"], ns)
    assert ns["NextBatchOp"]("") == "a=1"
    assert ns["NextBatchOp"]("") == ""
    assert ns["Remaining"] == ""
