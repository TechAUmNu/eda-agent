# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The 2026.10.11.03 live run (2026-10-12) and the base-typed sweep after it.

Each test EXECUTES the real Pascal through tests/pascal_eval.py (or calls
the real Python tool) and fails on 75e3286 for the reason named.

1. D-S3-1 (FATAL, P21): ``sch_place_cross_sheet_connector side=left``
   halted the engine on ``Conn.Side := 0`` - the cross-sheet connector has
   no ``Side`` - inside an open PreProcess. A side is now refused by the
   Python tool AND by the handler, before anything is created.
2. The sweep's FATAL-LIKELY dispatcher pairs: GetSchProperty /
   SetSchProperty (obj_query, obj_modify, filters, obj_create) touched
   ``Name``, ``FontId``, ``Style``, ... and the vertex readers on every
   type not denied. A pair the object's class lacks is the halt, not a
   counted catch, so the names are governed by per-ObjectId ALLOWLISTS
   now and an unlisted type answers '' noted ``unreadable``.
3. D-S3-2 (P11): out-of-enum label orientation / justification were stored
   and saved. Refused in Python and in both handlers, before any focus.
4. D-S3-3 (P12): install / uninstall reported success whenever the call
   did not raise. They now report what the installed list shows changed.
5. D-S3-4 (P13): a copy into a CLOSED destination PcbLib reported success
   into a background-loaded library that can never be saved. A library to
   be edited is opened as a document first, or the call is refused.
6. D-S2-1 (P7): the empty-3D-model undo took the body off the footprint
   only; it stayed listed and reached the file. Both halves now, read back.
"""
from __future__ import annotations

import asyncio
import json
import ntpath
import re
from types import SimpleNamespace

import pytest

from tests.pascal_eval import PascalError, build, translate
from tests.pascal_source import load, strip_comments
from tests.test_generic_popup_free import (
    Ctx, SchObj, _generic, _members, _ns as _gen_ns, _obj, _present, _HELPERS)

from pathlib import Path

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"
_LIBRARY = load(_ALTIUM / "Library.pas", minimum=40)
_MAIN = load(_ALTIUM / "Main.pas", minimum=40)
_UTILS = load(_ALTIUM / "Utils.pas", minimum=20)


class _Halt(AssertionError):
    """The engine's 'Undeclared identifier' halt: no translated Except
    catches an AssertionError, exactly as no Pascal Except sees the halt."""


def _esc(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _nb_rewrite(body: str) -> str:
    """pascal_eval has no Var parameters: NextBatchOp(Var Remaining) is
    rewritten into its two halves (the op, and what is left)."""
    return re.sub(r"(\w+)\s*:=\s*NextBatchOp\(Remaining\);",
                  r"\1 := _NBOP(Remaining); Remaining := _NBREST(Remaining);",
                  body)


def _nb_split(rem):
    while rem:
        i = rem.find("~~")
        if i < 0:
            return rem, ""
        op, rem = rem[:i], rem[i + 2:]
        if op:
            return op, rem
    return "", ""


_NB = {"_NBOP": lambda r: _nb_split(r)[0], "_NBREST": lambda r: _nb_split(r)[1]}


def _common(extra=None):
    ns = {
        "BuildErrorResponse": lambda rid, code, msg: ("ERR", code, msg),
        "BuildErrorResponseDetailed":
            lambda rid, code, msg, det: ("ERR", code, msg, json.loads(det)),
        "BuildSuccessResponse": lambda rid, data: ("OK", json.loads(data)),
        "EscapeJsonString": _esc,
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "FileExists": lambda p: False,
        "RecordCaughtException": lambda w: None,
    }
    ns.update(_NB)
    if extra:
        ns.update(extra)
    build(_MAIN, ["LooksAbsolutePath", "DocPathKey", "DocNameKey",
                  "CanonicalPathValue"], ns)
    return ns


# ---------------------------------------------------------------------------
# 1. D-S3-1: the cross-sheet connector's side
# ---------------------------------------------------------------------------

class _NoSide:
    """A cross-sheet connector: Location and Text exist (Text ran live,
    P21); Side does not, and touching it is the halt."""

    def __init__(self, log):
        object.__setattr__(self, "_d", {"Location": None, "Text": ""})
        object.__setattr__(self, "_log", log)

    def __getattr__(self, name):
        d = object.__getattribute__(self, "_d")
        if name in d:
            return d[name]
        raise _Halt(f"Undeclared identifier: {name}")

    def __setattr__(self, name, value):
        d = object.__getattribute__(self, "_d")
        if name not in d:
            raise _Halt(f"Undeclared identifier: {name}")
        object.__getattribute__(self, "_log").append(name)
        d[name] = value


def _cross_sheet(params):
    g = _generic()
    log: list = []
    state = {"pre": 0, "post": 0, "made": [], "registered": []}

    class Proc:
        def PreProcess(self, *a):
            state["pre"] += 1

        def PostProcess(self, *a):
            state["post"] += 1

    class Doc:
        DocumentName = r"C:\P\S.SchDoc"
        GraphicallyInvalidate = None

        def RegisterSchObjectInContainer(self, o):
            state["registered"].append(o)

    class Server:
        ProcessControl = Proc()
        GetCurrentSchDocument = Doc()

        def SchObjectFactory(self, kind, how):
            o = _NoSide(log)
            state["made"].append(o)
            return o

    ns = _common({
        "SchServer": Server(), "ExtractJsonValue": lambda p, k: params.get(k, ""),
        "MilsToCoord": lambda m: m, "Point": lambda x, y: (x, y),
        # Upstream's decimal mils (2026.10.25 merge): x / y are parsed as
        # decimals and echoed through FloatToJsonStr.
        "StrToFloatDef": lambda s, d: float(s) if str(s).strip() else d,
        "MilsToCoordF": lambda m: m,
        "FloatToJsonStr":
            lambda v: str(int(v)) if float(v) == int(v) else repr(float(v)),
        "SchRegisterObject": lambda d, o: None,
        "MarkDocDirtyByPath": lambda p: None,
        "eCrossSheetConnector": 33, "eCreate_Default": 0,
    })
    build(g, ["Gen_PlaceCrossSheetConnector"], ns)
    return ns, state, log


@pytest.mark.parametrize("side", ["left", "right", "LEFT", " left "])
def test_a_cross_sheet_side_is_refused_before_anything_is_created(side):
    ns, state, log = _cross_sheet({"x": "100", "y": "200", "net": "N",
                                   "side": side})
    reply = ns["Gen_PlaceCrossSheetConnector"]("{}", "r")
    assert reply[0] == "ERR" and reply[1] == "NOT_SCRIPTABLE"
    assert "Nothing was placed" in reply[2]
    assert state["made"] == [] and state["pre"] == 0, (
        "the connector was created or a PreProcess opened before the refusal")


def test_without_side_the_connector_is_placed_and_named_and_side_untouched():
    ns, state, log = _cross_sheet({"x": "100", "y": "200", "net": "N"})
    reply = ns["Gen_PlaceCrossSheetConnector"]("{}", "r")
    assert reply == ("OK", {"success": True, "x": 100, "y": 200, "net": "N"})
    assert log == ["Location", "Text"]
    assert state["pre"] == state["post"] == 1
    assert len(state["registered"]) == 1


class _CapturingBridge:
    def __init__(self):
        self.commands: list = []

    async def send_command_async(self, command, params=None, **kw):
        self.commands.append((command, dict(params or {})))
        return {"success": True}


def _tools():
    from eda_agent.tools.generic import register_generic_tools
    from eda_agent.tools.library import register_library_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_generic_tools(_Mcp())
    register_library_tools(_Mcp())
    return captured


def test_the_python_tool_refuses_a_side_and_sends_nothing(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    fake = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    tool = _tools()["sch_place_cross_sheet_connector"]
    reply = asyncio.run(tool(x=100, y=200, net="N", side="left"))
    assert reply["success"] is False and reply["error"] == "NOT_SCRIPTABLE"
    assert fake.commands == []
    asyncio.run(tool(x=100, y=200, net="N"))
    assert fake.commands == [("generic.place_cross_sheet_connector",
                              {"x": "100", "y": "200", "net": "N"})]


# ---------------------------------------------------------------------------
# 2. The dispatcher allowlists
# ---------------------------------------------------------------------------

def _prop_ns():
    ctx = Ctx()
    g = _generic()
    ns = _gen_ns(ctx, g)
    build(g, _present(g, _HELPERS) + ["GetSchVertexProperty", "GetSchProperty",
                                      "SetSchProperty", "MatchesFilter"], ns)
    return ctx, ns


#: The sweep's FATAL-LIKELY pairs (section 2 of BASETYPED_SWEEP): the SDK
#: interface of that type lacks the member, and nothing guarded it.
_FATAL_LIKELY = {
    "Name": "eNetLabel ePowerObject eSchComponent eWire eBus eBusEntry "
            "eLabel eLine eRectangle eSheetSymbol eNoERC eJunction eImage "
            "eTextFrame eNote eCrossSheetConnector eCompileMask",
    "FontId": "eSchComponent eWire eBus eBusEntry ePin eLine eRectangle "
              "eSheetSymbol eJunction eImage eNoERC eHarnessConnector "
              "eCompileMask",
    "LineWidth": "eNetLabel ePowerObject eSchComponent eParameter ePin "
                 "eLabel eSheetEntry eNoERC eJunction eCrossSheetConnector",
    "Style": "eNetLabel eSchComponent eWire eBus eBusEntry eParameter ePin "
             "eLabel eLine eRectangle eSheetSymbol eJunction eImage "
             "eTextFrame eNote eCompileMask",
    "IOType": "eNetLabel ePowerObject eSchComponent eWire eBus eBusEntry "
              "eParameter eParameterSet ePin eLabel eLine eRectangle "
              "eSheetSymbol eNoERC eJunction eImage eTextFrame eNote eProbe "
              "eCrossSheetConnector eCompileMask",
    "Alignment": "eNetLabel ePowerObject eSchComponent eWire eBus eBusEntry "
                 "eParameter ePin eLabel eLine eRectangle eSheetSymbol "
                 "eNoERC eJunction eImage eCrossSheetConnector eCompileMask",
    "TextColor": "eNetLabel ePowerObject eSchComponent eWire eBus eBusEntry "
                 "eParameter ePin eLabel eLine eRectangle eNoERC eJunction "
                 "eImage eCrossSheetConnector eCompileMask",
    "Justification": "eWire eBus eBusEntry ePin eLine eRectangle "
                     "eSheetSymbol eSheetEntry eNoERC eJunction eImage "
                     "eSchComponent eHarnessConnector eCompileMask",
    "Width": "eNetLabel ePowerObject eSchComponent eWire eBus eBusEntry "
             "eParameter ePin eLabel eLine eRectangle eSheetSymbol eNoERC "
             "eJunction eImage eCrossSheetConnector eCompileMask",
    "IsSolid": "eNetLabel ePort ePowerObject eSchComponent eWire eBus "
               "eBusEntry eParameter ePin eLabel eLine eSheetEntry eNoERC "
               "eJunction eCrossSheetConnector",
    "Orientation": "eWire eBus eBusEntry eLine eRectangle eSheetSymbol "
                   "eJunction eImage eTextFrame eNote eHarnessConnector "
                   "eCompileMask",
    "Text": "eCompileMask",
    "LibReference": "eBusEntry eParameterSet eLine eSheetSymbol eProbe "
                    "eHarnessConnector eCompileMask",
    "Electrical": "eBusEntry eParameterSet eLine eProbe eCompileMask",
    "XSize": "eSchComponent eBusEntry eParameterSet eLine eProbe "
             "eCompileMask",
    "IsMirrored": "ePort eBusEntry ePin eLine eSheetSymbol eSheetEntry "
                  "eNoERC eImage eTextFrame eNote eProbe eHarnessConnector "
                  "eCrossSheetConnector eCompileMask",
}
_PAIRS = [(p, t) for p, ts in _FATAL_LIKELY.items() for t in ts.split()]


def _bare(ctx, t):
    """An object of type t carrying ONLY the members the fixture says the
    type has, minus the one under test: touching it records a modal."""
    return _obj(ctx, t)


@pytest.mark.parametrize("prop,type_name", _PAIRS)
def test_the_getter_never_asks_a_type_the_sdk_says_lacks_it(prop, type_name):
    ctx, ns = _prop_ns()
    o = _bare(ctx, type_name)
    object.__getattribute__(o, "_d").pop(prop, None)
    assert ns["GetSchProperty"](o, prop) == ""
    assert ctx.modals == [], f"{prop} read on {type_name}"
    assert ("unreadable", prop) in ctx.diag


@pytest.mark.parametrize("prop,type_name",
                         [pt for pt in _PAIRS if pt[0] != "IsMirrored"])
def test_the_setter_never_writes_a_type_the_sdk_says_lacks_it(prop, type_name):
    ctx, ns = _prop_ns()
    o = _bare(ctx, type_name)
    object.__getattribute__(o, "_d").pop(prop, None)
    ns["SetSchProperty"](o, prop, "1")
    assert ctx.modals == [], f"{prop} written on {type_name}"
    # Reported, never silently claimed: 'failed' for the governed names,
    # 'unknown' for Orientation (its long-standing answer).
    assert ctx.diag and ctx.diag[-1][0] in ("failed", "unknown")


def test_obj_query_net_label_name_answers_unreadable_without_asking():
    """The sweep's first named test: properties=Name on eNetLabel."""
    ctx, ns = _prop_ns()
    assert ns["GetSchProperty"](_obj(ctx, "eNetLabel"), "Name") == ""
    assert ctx.modals == [] and ctx.diag == [("unreadable", "Name")]


@pytest.mark.parametrize("type_name", ["eLine", "eBusEntry", "eParameterSet",
                                       "eProbe", "eCompileMask"])
@pytest.mark.parametrize("prop", ["VerticesCount", "Vertex.1.X", "Vertices",
                                  "VertexLast.Y"])
def test_vertex_reads_never_reach_a_two_point_or_unprobed_type(type_name, prop):
    ctx, ns = _prop_ns()
    assert ns["GetSchProperty"](_obj(ctx, type_name), prop) == ""
    assert ctx.modals == [], f"{prop} on {type_name}"


def test_a_filter_on_name_over_many_net_labels_asks_none_of_them():
    ctx, ns = _prop_ns()
    labels = [_obj(ctx, "eNetLabel") for _ in range(25)]
    assert [ns["MatchesFilter"](o, "Name=CLK") for o in labels] == [False] * 25
    assert ctx.modals == []


def test_the_allowed_pairs_still_read_and_write():
    """A pair the allowlists keep is asked exactly as before."""
    ctx, ns = _prop_ns()
    get, put = ns["GetSchProperty"], ns["SetSchProperty"]
    port = _obj(ctx, "ePort")
    assert get(port, "Name") == "P" and get(port, "Width") == "100"
    assert get(port, "Style") == "0" and get(port, "IOType") == "0"
    assert get(_obj(ctx, "eNetLabel"), "FontId") == "1"
    assert get(_obj(ctx, "ePowerObject"), "Style") == "0"
    assert get(_obj(ctx, "eSheetEntry"), "Name") == "E"
    assert get(_obj(ctx, "eRectangle"), "IsSolid") == "true"
    assert get(_obj(ctx, "eSheetSymbol"), "LineWidth") == "1"
    assert get(_obj(ctx, "eParameterSet"), "Name") == "PS"
    assert get(_obj(ctx, "eCrossSheetConnector"), "Orientation") == "0"
    wire = _obj(ctx, "eWire")
    assert get(wire, "VerticesCount") == "2"
    assert put(port, "Name", "Q") == 1 and port.Name == "Q"
    assert ctx.modals == [] and ctx.caught == []


def test_one_list_governs_name_and_fontid_never_reaches_a_sheet_entry():
    """2026.10.25 merge: ours' SchPropAbsentOn and upstream's
    SchObjectHasName both gated Name, so only their INTERSECTION was asked
    and the declared PS / PR / HC (ours) and DE (upstream) went unreadable.
    The Name branch now delegates, so the two can never disagree. FontId
    on a sheet entry stopped the polling loop (upstream, AD 25.8), so ours'
    FontId list no longer admits it."""
    ctx, ns = _prop_ns()
    absent, has = ns["SchPropAbsentOn"], ns["SchObjectHasName"]
    for t in sorted(ctx.ids):
        o = SchObj(ctx, t)
        assert absent(o, "Name") is (not has(o)), t
    for t in ("ePort", "ePin", "eSheetEntry", "eParameter", "eParameterSet",
              "eProbe", "eHarnessConnector", "eDesignator"):
        assert has(SchObj(ctx, t)), t
    for t in ("eNetLabel", "eLabel", "eNote", "eTextFrame", "eSheetName",
              "eSheetFileName", "eSchComponent"):
        assert not has(SchObj(ctx, t)), t
    assert absent(SchObj(ctx, "eSheetEntry"), "FontId") is True
    assert ctx.modals == []


class _Unregistered:
    """A constant the AD26 runtime might not register: evaluating it is the
    engine's halt, which no Except sees."""

    def __eq__(self, other):
        raise _Halt("Undeclared identifier: eCompileMask")

    __ne__ = __eq__
    __hash__ = object.__hash__


@pytest.mark.parametrize("prop", ["LineWidth", "IsSolid"])
@pytest.mark.parametrize("type_name", ["eSchComponent", "eNetLabel", "ePin"])
def test_a_line_width_or_solid_query_never_evaluates_ecompilemask(prop,
                                                                  type_name):
    """eCompileMask has never been evaluated by a live run (its users are
    the 'compilemask' type word and a handler Python cannot reach). As the
    LAST operand of the LineWidth / IsSolid allowlists it was evaluated for
    every type no earlier operand matched - a default query path."""
    ctx, ns = _prop_ns()
    ns["eCompileMask"] = _Unregistered()
    o = _obj(ctx, type_name)
    assert ns["GetSchProperty"](o, prop) == ""
    ns["SetSchProperty"](o, prop, "1")
    assert ns["MatchesFilter"](o, f"{prop}=1") is False
    assert ctx.modals == []


@pytest.mark.parametrize("type_name", ["eLine", "eNoERC", "ePin",
                                       "eSheetSymbol", "eTextFrame", "eImage"])
def test_a_refused_is_mirrored_write_reports_failed_not_written(type_name):
    """A non-component outside the IsMirrored allowlist is not written, and
    the setter says so: -1 ('failed'), never 1 ('written')."""
    ctx, ns = _prop_ns()
    o = _obj(ctx, type_name)
    assert ns["SetSchProperty"](o, "IsMirrored", "true") == -1
    assert ctx.modals == [], "IsMirrored was touched"
    assert ctx.diag == [("failed", "IsMirrored")]


def test_an_admitted_is_mirrored_write_still_writes():
    ctx, ns = _prop_ns()
    lbl = _obj(ctx, "eNetLabel")
    assert ns["SetSchProperty"](lbl, "IsMirrored", "true") == 1
    assert lbl.IsMirrored is True and ctx.diag == []


@pytest.mark.parametrize("type_name", ["eLine", "eWire", "eJunction",
                                       "eImage"])
def test_a_refused_orientation_write_is_unknown_and_not_written(type_name):
    """It used to note 'unknown' AND return 1 ('written')."""
    ctx, ns = _prop_ns()
    o = _obj(ctx, type_name)
    assert ns["SetSchProperty"](o, "Orientation", "1") == 0
    assert ctx.modals == []
    assert ctx.diag == [("unknown", "Orientation")]


def test_harness_type_reads_on_a_harness_connector_only():
    ctx, ns = _prop_ns()
    hc = _obj(ctx, "eHarnessConnector", HarnessType="USB")
    assert ns["GetSchProperty"](hc, "HarnessType") == "USB"
    assert ns["GetSchProperty"](_obj(ctx, "eNetLabel"), "HarnessType") == ""
    assert ctx.modals == []
    assert ("unreadable", "HarnessType") in ctx.diag


# ---------------------------------------------------------------------------
# 3. D-S3-2: label orientation / justification ranges
# ---------------------------------------------------------------------------

def _label_ns(params):
    calls: list = []
    ns = _common({
        "ExtractJsonValue": lambda p, k: params.get(k, ""),
        "GetWorkspace": None,
    })
    build(_MAIN, ["GetBatchField"], ns)
    bodies = dict(_LIBRARY)
    bodies["Lib_SetLabelFormats"] = _nb_rewrite(bodies["Lib_SetLabelFormats"])
    names = [n for n in ("LabelOrdinalProblem",) if n in bodies]
    build(bodies, names + ["Lib_SetLabelFormats", "Lib_SetLabelFormat"], ns)
    return ns, calls


@pytest.mark.parametrize("ops,field,value,rng", [
    ("target=designator;orientation=90", "ops[0].orientation", "90", "0..3"),
    ("target=designator;orientation=-1", "ops[0].orientation", "-1", "0..3"),
    ("target=designator;font_id=2~~target=comment;justification=12",
     "ops[1].justification", "12", "0..8"),
])
def test_the_batch_handler_refuses_an_out_of_range_ordinal_before_focus(
        ops, field, value, rng):
    ns, _ = _label_ns({"ops": ops})
    reply = ns["Lib_SetLabelFormats"]("{}", "r")
    # GetWorkspace is None: a handler that went on would answer NO_WORKSPACE.
    assert reply[0] == "ERR" and reply[1] == "INVALID_ARGUMENT", reply
    assert f"{field}={value}" in reply[2] and rng in reply[2]
    assert "Nothing was changed" in reply[2]


def test_the_batch_handler_lets_in_range_ordinals_through():
    ns, _ = _label_ns({"ops": "target=designator;orientation=3;justification=8"})
    assert ns["Lib_SetLabelFormats"]("{}", "r")[1] == "NO_WORKSPACE"


@pytest.mark.parametrize("params", [{"orientation": "4"},
                                    {"justification": "9"},
                                    {"orientation": "x"}])
def test_the_single_handler_refuses_an_out_of_range_ordinal(params):
    ns, _ = _label_ns(params)
    reply = ns["Lib_SetLabelFormat"]("{}", "r")
    assert reply[0] == "ERR" and reply[1] == "INVALID_ARGUMENT", reply


@pytest.mark.parametrize("op,needle", [
    ({"target": "designator", "orientation": 90}, "ops[0].orientation=90"),
    ({"target": "designator", "justification": 12}, "ops[0].justification=12"),
])
def test_the_python_tool_refuses_an_out_of_range_ordinal(op, needle,
                                                         monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    fake = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, fake)
    reply = asyncio.run(_tools()["lib_set_label_formats"](ops=[op]))
    assert reply["error"] == "INVALID_ARGUMENT" and needle in reply["reason"]
    assert fake.commands == []


# ---------------------------------------------------------------------------
# 4. D-S3-3: install / uninstall report what changed
# ---------------------------------------------------------------------------

class _Ilm:
    """IntegratedLibraryManager: the list holds exact spellings, and
    Install / UnInstall act only on what they recognise - neither raises
    on a relative name or a respelled path (P12)."""

    def __init__(self, paths, known_files=()):
        self.paths = list(paths)
        self.known = {k.upper() for k in known_files}

    @property
    def InstalledLibraryCount(self):
        return len(self.paths)

    def InstalledLibraryPath(self, i):
        return self.paths[int(i)]

    def InstallLibrary(self, p):
        if p.upper() in self.known:
            self.paths.append(p)

    def UnInstallLibrary(self, p):
        if p in self.paths:
            self.paths.remove(p)


def _install_ns(ilm, files=()):
    have = {f.upper() for f in files}
    ns = _common({"IntegratedLibraryManager": ilm,
                  "FileExists": lambda p: p.strip().upper() in have})
    names = [n for n in ("InstalledLibPathList", "InstalledPathsNotIn",
                         "InstalledPathWithName", "InstalledPathsJson")
             if n in _LIBRARY]
    build(_LIBRARY, names + ["InstalledLibNameKnown", "Lib_InstallLibrary",
                             "Lib_UninstallLibrary"], ns)
    return ns


_LIBS = [r"C:\L\Misc Devices.IntLib", r"C:\L\Misc Connectors.IntLib"]
_NEW = r"C:\scratch\s3_install.SchLib"


def _call(ns, fn, path):
    ns["ExtractJsonValue"] = lambda p, k: {"library_path": path}.get(k, "")
    return ns[fn]("{}", "r")


def test_a_relative_missing_name_is_not_reported_installed():
    ilm = _Ilm(_LIBS)
    reply = _call(_install_ns(ilm), "Lib_InstallLibrary", "nope_rel.SchLib")
    assert reply[0] == "OK" and reply[1]["installed"] is False
    assert "did not change" in reply[1]["reason"]
    assert ilm.paths == _LIBS


def test_a_real_install_reports_the_entry_it_added():
    ilm = _Ilm(_LIBS, known_files=[_NEW])
    reply = _call(_install_ns(ilm, files=[_NEW]), "Lib_InstallLibrary", _NEW)
    assert reply[1]["installed"] is True and reply[1]["added"] == [_NEW]


def test_an_already_installed_library_is_not_reported_installed_again():
    ilm = _Ilm(_LIBS + [_NEW], known_files=[_NEW])
    reply = _call(_install_ns(ilm, files=[_NEW]), "Lib_InstallLibrary",
                  _NEW.lower())
    assert reply[1]["installed"] is False
    assert "already installed" in reply[1]["reason"]


def test_a_respelled_uninstall_is_not_reported_uninstalled():
    ilm = _Ilm(_LIBS + [_NEW])
    spelled = _NEW.replace("\\", "/").lower()
    reply = _call(_install_ns(ilm), "Lib_UninstallLibrary", spelled)
    assert reply[1]["uninstalled"] is False
    assert _NEW in ilm.paths
    assert "did not change" in reply[1]["reason"]
    assert _NEW.replace("\\", "\\") in reply[1]["reason"]


def test_a_real_uninstall_reports_the_entry_it_removed():
    ilm = _Ilm(_LIBS + [_NEW])
    reply = _call(_install_ns(ilm), "Lib_UninstallLibrary", _NEW)
    assert reply[1]["uninstalled"] is True and reply[1]["removed"] == [_NEW]
    assert ilm.paths == _LIBS


def test_an_unreadable_list_never_reports_success():
    class Blind(_Ilm):
        @property
        def InstalledLibraryCount(self):
            raise PascalError("unreadable")

    ilm = Blind(_LIBS, known_files=[_NEW])
    reply = _call(_install_ns(ilm, files=[_NEW]), "Lib_InstallLibrary", _NEW)
    assert reply[1]["installed"] is False
    assert "could not be read" in reply[1]["reason"]


# ---------------------------------------------------------------------------
# 5. D-S3-4: copy / move into a closed PcbLib
# ---------------------------------------------------------------------------

class _Fp:
    def __init__(self, name):
        self.Name = name

    def CopyTo(self, other, how):
        other.copied_from = self.Name


class _Lib:
    """A PCB library. ``drops`` are names whose RegisterComponent does not
    land; ``stuck`` makes RemoveComponent a silent no-op (a removal that
    does not take)."""

    def __init__(self, path, names=(), drops=(), stuck=False):
        self.path = path
        self.comps = [_Fp(n) for n in names]
        self.drops = set(drops)
        self.stuck = stuck
        self.Board = SimpleNamespace(FileName=path,
                                     ViewManager_FullUpdate=None)
        self.RefreshView = None

    @property
    def ComponentCount(self):
        return len(self.comps)

    def GetComponent(self, j):
        return self.comps[int(j)]

    def GetComponentByName(self, n):
        return next((c for c in self.comps if c.Name == n), None)

    @property
    def CreateNewComponent(self):
        return _Fp("")

    def RegisterComponent(self, fp):
        if fp.Name not in self.drops:
            self.comps.append(fp)

    def RemoveComponent(self, fp):
        if not self.stuck and fp in self.comps:
            self.comps.remove(fp)

    def DeRegisterComponent(self, fp):
        pass

    def names(self):
        return [c.Name for c in self.comps]


def _key(p):
    return p.strip().replace("/", "\\").upper()


class _World:
    """Documents (by DocPathKey, each with its canonical FileName), the DM
    focus, OpenObject (opens an openable file as a document and focuses
    it; focuses an open one), and the PCB server: GetCurrentPCBLibrary is
    the FOCUSED document's library. ``stale`` maps a path to a BACKGROUND
    instance that GetPCBLibraryByPath keeps returning even after the path
    is open as a document - the review's two-call sequence. Background
    loads are recorded, and must never happen."""

    def __init__(self, libs, docs=(), openable=(), files=(), stale=None,
                 focus_sticks=True, focus=None):
        self.libs = {_key(k): v for k, v in libs.items()}
        self.docs = {_key(d): d for d in docs}
        self.openable = {_key(o): o for o in openable}
        self.files = {_key(f) for f in files} | set(self.openable)
        self.stale = {_key(k): v for k, v in (stale or {}).items()}
        self.focus_sticks = focus_sticks
        self.focus = focus
        self.loaded: list = []
        self.by_path: list = []
        self.opened: list = []
        self.marked: list = []
        self.pre = 0
        self.params: dict = {}
        world = self

        class Client:
            @staticmethod
            def GetDocumentByPath(p):
                c = world.docs.get(_key(p))
                return SimpleNamespace(FileName=c) if c else None

        class Server:
            @staticmethod
            def GetPCBLibraryByPath(p):
                world.by_path.append(p)
                if _key(p) in world.stale:
                    return world.stale[_key(p)]
                return world.libs.get(_key(p)) if _key(p) in world.docs else None

            @staticmethod
            def LoadPCBLibraryByPath(p):
                world.loaded.append(p)
                return world.stale.get(_key(p)) or world.libs.get(_key(p))

            @property
            def PreProcess(self):
                world.pre += 1

            @property
            def PostProcess(self):
                world.pre -= 1

            @property
            def GetCurrentPCBLibrary(self):
                if world.focus is None:
                    return None
                return world.libs.get(_key(world.focus))

        class Workspace:
            @property
            def DM_FocusedDocument(self):
                if world.focus is None:
                    return None
                return SimpleNamespace(DM_FullPath=world.focus)

        self.client, self.server, self.workspace = Client, Server(), Workspace()

    def find_doc(self, p):
        c = self.docs.get(_key(p))
        return SimpleNamespace(FileName=c) if c else None

    def run_process(self, name):
        assert name == "WorkspaceManager:OpenObject"
        p = self.params.get("FileName", "")
        self.opened.append(p)
        k = _key(p)
        if k not in self.docs and k in self.openable:
            self.docs[k] = self.openable[k]
        if k in self.docs and self.focus_sticks:
            self.focus = self.docs[k]

    def ns(self, params):
        world = self

        def mark(p):
            world.marked.append(p)
            return _key(p) in world.docs

        ns = _common({
            "Client": self.client, "PCBServer": self.server,
            "GetWorkspace": self.workspace,
            "FindOpenServerDoc": self.find_doc,
            "ExtractJsonValue": lambda pr, k: params.get(k, ""),
            "FileExists": lambda p: _key(p) in world.files,
            "FileLooksLoadable": lambda p: _key(p) in world.files,
            "ResetParameters": lambda: world.params.clear(),
            "AddStringParameter": lambda k, v: world.params.__setitem__(k, v),
            "RunProcess": self.run_process,
            "MarkDocDirty": mark, "MarkDocDirtyByPath": mark,
            "MarkOutcomeJson": lambda d: "{}",
            "PcbLibMayBeCurrent": lambda d: world.focus is not None,
            "PcbLibMayBeCurrentAt": lambda p: True,
            "eFullCopy": 1,
        })
        build(_MAIN, ["ServerDocFileName"], ns)
        bodies = dict(_LIBRARY)
        bodies["Lib_MoveFootprints"] = _nb_rewrite(bodies["Lib_MoveFootprints"])
        helpers = [n for n in ("PcbLibLoadable", "PcbLibIsWorkspaceDoc",
                               "PcbLibOpenAsDocument", "PcbLibDocPath",
                               "PcbLibMayOpen", "PcbLibEnsureDocPath",
                               "PcbLibDocLibrary", "PcbLibNotOpenMessage",
                               "PcbLibNoDocLibraryMessage") if n in bodies]
        # upstream (2026.10.13 merge) moves a copy onto its own footprint's
        # origin; these fixtures model where documents open, not geometry,
        # so the origins are reported as already agreeing (-1).
        ns.setdefault("AlignCopiedFootprint", lambda src, dest: -1)
        build(bodies, helpers + ["Lib_CopyFootprint", "Lib_MoveFootprints"], ns)
        return ns


SRC = r"C:\scratch\s3_src.PcbLib"
DEST = r"C:\scratch\s3_dest.PcbLib"


def test_a_copy_into_a_closed_destination_opens_it_as_a_document_first():
    src, dest = _Lib(SRC, ["P13_COPY"]), _Lib(DEST, ["OTHER"])
    w = _World({SRC: src, DEST: dest}, docs=[SRC], openable=[DEST])
    ns = w.ns({"source_name": "P13_COPY", "source_library": SRC,
               "dest_library": DEST})
    reply = ns["Lib_CopyFootprint"]("{}", "r")
    assert reply[0] == "OK", reply
    assert DEST in w.opened and _key(DEST) in w.docs
    assert w.loaded == [], "a library was loaded in the background"
    assert dest.names() == ["OTHER", "P13_COPY"]
    assert reply[1]["pending_save"] is True
    assert reply[1]["written_to_disk"] is False
    assert reply[1]["marked_modified"] is True and "mark" in reply[1]
    assert reply[1]["dest_opened"] is True


def test_a_destination_that_cannot_be_opened_is_refused_with_nothing_changed():
    src, dest = _Lib(SRC, ["P13_COPY"]), _Lib(DEST, ["OTHER"])
    w = _World({SRC: src, DEST: dest}, docs=[SRC])      # DEST not openable
    ns = w.ns({"source_name": "P13_COPY", "source_library": SRC,
               "dest_library": DEST})
    reply = ns["Lib_CopyFootprint"]("{}", "r")
    assert reply[0] == "ERR" and reply[1] == "DEST_NOT_OPEN", reply
    assert "Nothing was changed" in reply[2]
    assert "it was not opened" in reply[2], (
        "the refusal must say no open ran (the file is not there)")
    assert dest.names() == ["OTHER"]
    assert w.loaded == [] and w.pre == 0


def test_a_relative_closed_destination_is_never_handed_to_openobject():
    src = _Lib(SRC, ["P13_COPY"])
    w = _World({SRC: src}, docs=[SRC])
    ns = w.ns({"source_name": "P13_COPY", "source_library": SRC,
               "dest_library": "rel_dest.PcbLib"})
    reply = ns["Lib_CopyFootprint"]("{}", "r")
    assert reply[1] == "DEST_NOT_OPEN"
    assert "rel_dest.PcbLib" not in w.opened


def test_a_move_out_of_a_closed_source_opens_it_or_deletes_nothing():
    src, dest = _Lib(SRC, ["A", "B"]), _Lib(DEST, [])
    w = _World({SRC: src, DEST: dest}, docs=[DEST], files=[SRC])  # SRC won't open
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A~~B"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[0] == "ERR" and reply[1] == "SOURCE_NOT_OPEN", reply
    assert "did not make it one" in reply[2], (
        "an open WAS tried here, and the refusal must say so")
    assert src.names() == ["A", "B"] and dest.comps == []
    assert w.loaded == [] and w.pre == 0


def test_a_move_into_a_closed_destination_that_opens_moves_and_marks_both():
    src, dest = _Lib(SRC, ["A", "B"]), _Lib(DEST, [])
    w = _World({SRC: src, DEST: dest}, docs=[SRC], openable=[DEST])
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A~~B"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[0] == "OK", reply
    assert reply[1]["moved"] == 2 and reply[1]["deleted_from_source"] == 2
    assert src.comps == [] and dest.names() == ["A", "B"]
    assert w.loaded == []
    assert reply[1]["pending_save"] is True and reply[1]["dest_opened"] is True
    assert set(w.marked) == {DEST, SRC}


def test_a_move_never_deletes_a_footprint_whose_copy_is_not_listed():
    src, dest = _Lib(SRC, ["A", "B"]), _Lib(DEST, [], drops=["B"])
    w = _World({SRC: src, DEST: dest}, docs=[SRC, DEST])
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A~~B"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[1]["moved"] == 1 and reply[1]["copy_not_listed"] == 1
    assert src.names() == ["B"]


def test_a_copy_only_move_reads_a_closed_source_through_its_document():
    """Review of the first fix: a background load is what leaves a stale
    instance behind for a LATER call, so a source that is only read is
    opened as a document too - nothing is loaded in the background."""
    src, dest = _Lib(SRC, ["A"]), _Lib(DEST, [])
    w = _World({SRC: src, DEST: dest}, docs=[DEST], openable=[SRC])
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A",
               "delete_from_source": "false"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[0] == "OK" and reply[1]["moved"] == 1, reply
    assert w.loaded == [], "the source was loaded in the background"
    assert reply[1]["source_opened"] is True and _key(SRC) in w.docs
    assert src.names() == ["A"] and dest.names() == ["A"]


def test_a_stale_background_copy_never_receives_the_copy_or_costs_the_source():
    """The review's sequence: an earlier call left a BACKGROUND instance of
    X that GetPCBLibraryByPath keeps returning after X is opened as a
    document. A move Y -> X (delete_from_source default) must put the copy
    in X's DOCUMENT library - the one the save writes - and never in the
    stale instance."""
    x_doc, x_stale = _Lib(DEST, []), _Lib(DEST, [])
    y = _Lib(SRC, ["FP"])
    w = _World({SRC: y, DEST: x_doc}, docs=[SRC], openable=[DEST],
               stale={DEST: x_stale})
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "FP"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[0] == "OK", reply
    assert x_stale.comps == [], "the copy landed in the background instance"
    assert x_doc.names() == ["FP"], "the document library lacks the copy"
    assert y.comps == [] and reply[1]["deleted_from_source"] == 1
    assert w.by_path == [] and w.loaded == []


def test_a_copy_into_an_open_library_spelled_with_slashes_copies_as_before():
    """FindOpenServerDoc's DocPathKey finds an open destination however it
    is spelled; nothing is opened under the caller's spelling."""
    src, dest = _Lib(SRC, ["P"]), _Lib(DEST, [])
    w = _World({SRC: src, DEST: dest}, docs=[SRC, DEST], focus=SRC)
    slashed = DEST.replace("\\", "/").lower()
    ns = w.ns({"source_name": "P", "source_library": SRC,
               "dest_library": slashed})
    reply = ns["Lib_CopyFootprint"]("{}", "r")
    assert reply[0] == "OK", reply
    assert dest.names() == ["P"]
    assert slashed not in w.opened, "OpenObject ran on the caller's spelling"
    assert all(o in (SRC, DEST) for o in w.opened), w.opened
    assert reply[1]["dest_opened"] is False
    assert reply[1]["dest_library"] == DEST


def test_a_focus_that_does_not_land_is_refused_before_any_edit():
    src, dest = _Lib(SRC, ["A"]), _Lib(DEST, [])
    w = _World({SRC: src, DEST: dest}, docs=[SRC, DEST], focus=SRC,
               focus_sticks=False)
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[0] == "ERR" and reply[1] == "DEST_NOT_OPEN", reply
    assert "could not be taken from the document" in reply[2]
    assert src.names() == ["A"] and dest.comps == [] and w.pre == 0


@pytest.mark.parametrize("handler", ["Lib_MoveFootprints", "Lib_CopyFootprint"])
def test_an_overwrite_whose_old_footprint_survives_never_passes_for_the_copy(
        handler):
    """overwrite=true: the old same-named footprint's removal does not take
    and the new copy does not land. GetComponentByName still answers the
    OLD one; that must not count as the copy, and the source keeps it."""
    src = _Lib(SRC, ["A"])
    dest = _Lib(DEST, ["A"], drops=["A"], stuck=True)
    old = dest.comps[0]
    w = _World({SRC: src, DEST: dest}, docs=[SRC, DEST])
    if handler == "Lib_MoveFootprints":
        ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A",
                   "overwrite": "true"})
        reply = ns[handler]("{}", "r")
        assert reply[1]["moved"] == 0 and reply[1]["copy_not_listed"] == 1
        assert reply[1]["deleted_from_source"] == 0
    else:
        ns = w.ns({"source_name": "A", "source_library": SRC,
                   "dest_library": DEST, "overwrite": "true"})
        reply = ns[handler]("{}", "r")
        assert reply[0] == "ERR" and reply[1] == "COPY_FAILED", reply
    assert src.names() == ["A"], "the source lost its footprint"
    assert dest.comps == [old]


def test_an_overwrite_that_takes_moves_the_new_copy():
    src, dest = _Lib(SRC, ["A"]), _Lib(DEST, ["A"])
    old = dest.comps[0]
    w = _World({SRC: src, DEST: dest}, docs=[SRC, DEST])
    ns = w.ns({"source_library": SRC, "dest_library": DEST, "names": "A",
               "overwrite": "true"})
    reply = ns["Lib_MoveFootprints"]("{}", "r")
    assert reply[1]["moved"] == 1 and reply[1]["copy_not_listed"] == 0
    assert dest.names() == ["A"] and dest.comps[0] is not old
    assert src.comps == []


@pytest.mark.parametrize("fname", ["Lib_MoveFootprints", "Lib_CopyFootprint"])
def test_neither_handler_loads_or_resolves_a_library_by_path(fname):
    """Source text: no background load and no by-path lookup are left for a
    stale instance to come back through."""
    body = strip_comments(_LIBRARY[fname])
    assert "LoadPCBLibraryByPath" not in body
    assert "GetPCBLibraryByPath" not in body


# ---------------------------------------------------------------------------
# 6. D-S2-1: the empty 3D body comes off the footprint AND the board
# ---------------------------------------------------------------------------

class _Body:
    def __init__(self, addr=4242):
        self.OverallHeight = 0
        self.I_ObjectAddress = addr
        self.ObjectId = 12


class _GrpIter:
    def __init__(self, items):
        self.items, self.i = list(items), 0

    def AddFilter_ObjectSet(self, _s):
        pass

    @property
    def FirstPCBObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextPCBObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


class _Footprint:
    """``listed`` holds the body once per registration (a session-added
    primitive is listed twice until a reload); ``stuck`` makes every
    removal raise, as a footprint that will not let go; ``silent`` makes
    RemovePCBObject return without removing - the .03 run's case
    (RemoveLibBodyByAddress answered True and the body stayed listed)."""

    def __init__(self, prims, stuck=False, silent=False):
        self.prims = list(prims)
        self.stuck = stuck
        self.silent = silent
        self.I_ObjectAddress = 9
        self.removes = 0

    @property
    def GroupIterator_Create(self):
        return _GrpIter(self.prims)

    def GroupIterator_Destroy(self, _it):
        pass

    def RemovePCBObject(self, prim):
        self.removes += 1
        if self.stuck:
            raise PascalError("refused")
        if not self.silent:
            self.prims.remove(prim)


def _snip():
    body = strip_comments(_LIBRARY["Lib_Link3DModel"])
    start = body.index("Body3D_ReadRect(Body, X1, Y1, X2, Y2, HaveRect);")
    start = body.index(";", start) + 1
    end = body.index("If AlignStr = 'none' Then", start)
    return ("Procedure Snip(Body : Integer; Footprint : Integer; ModelPath : String;"
            " Replace : Boolean; RemovedDistinct : Integer; HaveRect : Boolean;"
            " PcbLib : Integer);\nBegin\n" + body[start:end] + "\nEnd;")


def _link_empty(fp_listings=1, board_holds=True, board_takes=True,
                current=True, stuck=False, silent=False):
    body = _Body()
    fp = _Footprint([body] * fp_listings, stuck=stuck, silent=silent)
    board = {"held": [body.I_ObjectAddress] if board_holds else [],
             "remove_calls": []}
    lib = SimpleNamespace(CurrentComponent=fp if current else object(),
                          Board=board)

    def remove_entries(b, entries, skip, made_current, removed):
        b["remove_calls"].append((entries, skip, made_current))
        addr = entries.split(":")[1].rstrip("|")
        if f"|{addr}|" in skip:
            return '{"requested":true,"skipped":1}'
        if board_takes and int(addr) in b["held"]:
            b["held"].remove(int(addr))
        return '{"requested":true}'

    def still(b, entries):
        if ":" not in entries:              # no entry text: count what is held
            return len(b["held"])
        addr = int(entries.split(":")[1].rstrip("|"))
        return 1 if addr in b["held"] else 0

    ns = {"PCBServer": SimpleNamespace(SendMessageToRobots=lambda *a: None),
          "c_Broadcast": 0, "PCBM_BeginModify": 0, "PCBM_EndModify": 0,
          "c_NoEventData": 0, "eComponentBodyObject": 12,
          "MkSet": lambda *a: a, "Failure": "", "FailCode": "",
          "LibBoardStillListsCount": still,
          "LibBoardRemoveEntries": remove_entries,
          "EscapeJsonString": _esc,
          "BoolToJsonStr": lambda b: "true" if b else "false",
          "RecordCaughtException": lambda w: None}
    build(_UTILS, ["JsonStr", "JsonInt", "JsonBool", "JsonRaw", "JsonObj"], ns)
    build(_LIBRARY, ["LibAddRemovalEntry", "Body3D_BodyAddresses"], ns)
    build({"R": _LIBRARY["RemoveLibBodyByAddress"].replace(
        "Var Entries : String", "Entries : String")}, ["R"], ns)
    src = translate(_snip())[1]
    exec(compile(src, "<pascal Snip>", "exec"), ns)  # noqa: S102
    ns["Snip"](body, fp, r"C:\scratch\stub.igs", False, 0, False, lib)
    return ns, fp, body, board


def test_the_empty_body_comes_off_the_footprint_and_the_library_board():
    ns, fp, body, board = _link_empty(fp_listings=2, board_holds=True)
    assert ns["FailCode"] == "MODEL_LOAD_FAILED"
    assert body not in fp.prims, "the footprint still lists the empty body"
    assert board["held"] == [], "the library board still holds the empty body"
    assert len(board["remove_calls"]) == 1
    det = json.loads(ns["EmptyDetails"])
    assert det["footprint_still_lists"] is False
    assert det["board_still_lists"] == 0 and det["removed_from_both"] is True
    assert "footprint_still_lists=false" in ns["Failure"]


def test_one_body_is_never_removed_more_than_twice():
    """Body3D_RemoveAll's documented limit: a third sighting of one address
    means RemovePCBObject is silently refusing it. The .03 run was that
    case (the removal answered True, the body stayed listed); the first
    fix repeated RemoveLibBodyByAddress up to 4 rounds = 8 removals."""
    ns, fp, body, board = _link_empty(silent=True)
    assert fp.removes == 2, f"{fp.removes} RemovePCBObject calls on one body"
    det = json.loads(ns["EmptyDetails"])
    assert det["footprint_still_lists"] is True
    assert det["footprint_remove_rounds"] == 1
    assert det["board_removal"] == {"requested": False}
    assert board["remove_calls"] == []
    assert det["removed_from_both"] is False
    assert "Do NOT save this library" in ns["Failure"]
    assert "footprint_still_lists=true" in ns["Failure"]


def test_a_footprint_that_lists_it_three_times_is_reported_not_spun_on():
    ns, fp, body, board = _link_empty(fp_listings=3)
    assert fp.removes == 2 and fp.prims == [body]
    det = json.loads(ns["EmptyDetails"])
    assert det["footprint_still_lists"] is True
    assert board["remove_calls"] == [], "rule 4: the footprint still lists it"


def test_a_board_removal_that_does_not_take_is_reported_not_claimed():
    ns, fp, body, board = _link_empty(board_takes=False)
    det = json.loads(ns["EmptyDetails"])
    assert det["board_still_lists"] == 1 and det["removed_from_both"] is False
    msg = ns["Failure"]
    assert "could NOT be taken off" in msg and "board_still_lists=1" in msg
    assert "Do NOT save this library" in msg
    assert "taken off the footprint and off the library board" not in msg


def test_a_footprint_that_will_not_let_go_is_bounded_and_skips_the_board():
    ns, fp, body, board = _link_empty(stuck=True)
    det = json.loads(ns["EmptyDetails"])
    assert det["footprint_still_lists"] is True
    assert det["footprint_remove_rounds"] == 1 and fp.removes == 1
    assert board["remove_calls"] == [], (
        "the board half ran on a body the footprint still lists (rule 4)")
    assert "Do NOT save this library" in ns["Failure"]


def test_the_board_is_not_touched_when_the_footprint_is_not_current():
    ns, fp, body, board = _link_empty(current=False)
    det = json.loads(ns["EmptyDetails"])
    assert det["board_still_lists"] is None and det["footprint_current"] is False
    assert board["remove_calls"] == []
    assert "board_still_lists=null" in ns["Failure"]
    assert "Do NOT save this library" in ns["Failure"]
