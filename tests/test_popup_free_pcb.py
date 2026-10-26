# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""PCB.pas and PCBGeneric.pas ask first instead of catching.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs (measured; recorded beside
WriteFileContent in Main.pas). A Try around a call that raises on an
ordinary input is therefore not a guard: the user gets a popup and the
polling loop waits on it. Each site below now checks first, or - where no
question can be asked without risking a call that works today - counts the
catch with RecordCaughtException so one live run can settle it.

HOW. The handlers are EXECUTED through tests/pascal_eval.py against fixture
boards. A fixture member that the real engine would refuse records a popup
and raises PascalError (what the Except would then catch). Each test fails
on the code before this change - a popup was recorded, a catch went
uncounted, or the guard's helper did not exist - and also pins the reply,
which must be what the call produced after the dialog.

Where the defect is a DECLARED TYPE (DelphiScript resolves and narrows by
declaration, which an untyped Python run cannot see), the test is a rule
over every routine in the file, parsed from the Var sections, not a pin on
one line.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build, translate
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"


def _pcb():
    return load(_ALTIUM / "PCB.pas", minimum=40)


def _generic():
    return load(_ALTIUM / "PCBGeneric.pas", minimum=10)


def _main():
    return load(_ALTIUM / "Main.pas", minimum=40)


def _utils():
    return load(_ALTIUM / "Utils.pas", minimum=40)


# ---------------------------------------------------------------------------
# Fixture machinery
# ---------------------------------------------------------------------------

# TLayer ordinals as Altium numbers them (only order and ranges matter).
LAYERS = {"eNoLayer": 0, "eTopLayer": 1, "eBottomLayer": 32,
          "eTopOverlay": 33, "eBottomOverlay": 34, "eTopPaste": 35,
          "eBottomPaste": 36, "eTopSolder": 37, "eBottomSolder": 38,
          "eInternalPlane1": 39, "eInternalPlane16": 54,
          "eKeepOutLayer": 56, "eMechanical1": 57, "eMechanical16": 72}
for _n in range(1, 31):
    LAYERS[f"eMidLayer{_n}"] = 1 + _n
for _n in range(1, 17):
    LAYERS[f"eInternalPlane{_n}"] = 38 + _n
NAME_OF = {}
for _k, _v in LAYERS.items():
    NAME_OF.setdefault(_v, _k[1:])

OIDS = {"eArcObject": 1, "ePadObject": 2, "eViaObject": 3, "eTrackObject": 4,
        "eTextObject": 5, "eFillObject": 6, "eConnectionObject": 7,
        "eComponentObject": 8, "ePolyObject": 9, "eRegionObject": 10,
        "eComponentBodyObject": 11, "eEmbeddedBoardObject": 12,
        "eRuleObject": 13}


class Popups(list):
    """Every member the engine would have refused, in order."""


class Fx:
    """A fixture object. Names in ``undeclared`` are members the script
    engine refuses: reading or writing one records a popup and raises
    PascalError, which is what a translated Except catches."""

    def __init__(self, popups, undeclared=(), **attrs):
        object.__setattr__(self, "_popups", popups)
        object.__setattr__(self, "_undeclared", set(undeclared))
        for k, v in attrs.items():
            object.__setattr__(self, k, v)

    def __getattr__(self, name):
        if name in self._undeclared:
            self._popups.append(name)
            raise PascalError(name)
        raise AttributeError(name)

    def __setattr__(self, name, value):
        if name in self._undeclared:
            self._popups.append(name)
            raise PascalError(name)
        object.__setattr__(self, name, value)


class Undeclared:
    """An identifier the engine does not know at all (PCBServer before any
    PCB document was opened): touching any member pops."""

    def __init__(self, popups, label):
        self._popups, self._label = popups, label

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        self._popups.append(f"{self._label}.{name}")
        raise PascalError(name)


class StrList:
    """TStringList: Add / IndexOf / Count / [i] / Free."""

    def __init__(self):
        self.items = []
        self.Free = None

    def Add(self, s):
        self.items.append(s)
        return len(self.items) - 1

    def IndexOf(self, s):
        return self.items.index(s) if s in self.items else -1

    @property
    def Count(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]


class IfaceList(StrList):
    """TInterfaceList: Add / Count / Items[i]."""

    @property
    def Items(self):
        return self.items


class _Iter:
    """A board / group iterator over a fixed list: FirstPCBObject restarts,
    NextPCBObject advances, the filters are accepted and ignored."""

    def __init__(self, items):
        self.items, self.i = list(items), 0

    def AddFilter_ObjectSet(self, s):
        pass

    AddFilter_LayerSet = AddFilter_Method = AddFilter_ObjectSet

    @property
    def FirstPCBObject(self):
        self.i = 0
        return self.items[0] if self.items else None

    @property
    def NextPCBObject(self):
        self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None


class _StrListClass:
    @property
    def Create(self):
        return StrList()


def _ns(popups, **extra):
    """The namespace every handler run shares: responses, JSON helpers,
    layer names, constants. Anything a test needs to observe is passed in."""
    caught: list[str] = []
    dirty: list[str] = []
    ns = dict(LAYERS)
    ns.update(OIDS)
    ns.update({
        "BuildErrorResponse": lambda rid, code, msg: ("ERR", code, msg),
        "BuildSuccessResponse": lambda rid, data: ("OK", data),
        "ExtractJsonValue": lambda params, key: params.get(key, ""),
        "EscapeJsonString": lambda s: s,
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "FloatToJsonStr": lambda f: repr(float(f)),
        "GetLayerString": lambda l: NAME_OF.get(l, f"L{l}"),
        "BoardLayerNamesHint": lambda b: "",
        "MarkDocDirtyByPath": dirty.append,
        "RecordCaughtException": caught.append,
        "MilsToCoord": lambda m: int(m) * 10000,
        "CoordToMils": lambda c: int(c) // 10000,
        # Upstream's decimal-mil conversions (Utils.pas, 2026.10.25 merge).
        "MilsToCoordF": lambda m: round(float(m) * 10000),
        "CoordToMilsF": lambda c: c / 10000,
        "StrToFloatDef": lambda s, d: float(s) if s else d,
        "Abs": abs,
        "MkSet": lambda *a: set(a),
        "AllLayers": "AllLayers",
        "eProcessAll": "eProcessAll",
        "c_Broadcast": "c_Broadcast", "c_NoEventData": "c_NoEventData",
        "PCBM_BoardRegisteration": "Reg", "PCBM_BeginModify": "Begin",
        "PCBM_EndModify": "End",
        "TStringList": _StrListClass(),
        "CreateObject": lambda cls: IfaceList(),
        "TInterfaceList": "TInterfaceList",
        "_caught": caught, "_dirty": dirty, "_popups": popups,
    })
    ns.update(extra)
    return ns


def _server(popups, messages=None, **attrs):
    msgs = messages if messages is not None else []
    return Fx(popups, PreProcess=None, PostProcess=None,
              SendMessageToRobots=lambda *a: msgs.append(a[2]), **attrs)


class Stack:
    """IPCB_LayerStack_V7 over a list of layer ids: the FirstLayer /
    NextLayer walk lists exactly those, while LayerObject_V7[] answers an
    object for ANY id - the property the old guards relied on wrongly."""

    def __init__(self, popups, ids, insertable=(), removable=()):
        self.popups = popups
        self.ids = list(ids)
        self.insertable = set(insertable)
        self.removable = set(removable)
        self.objs = {}

    def obj(self, lid):
        if lid not in self.objs:
            self.objs[lid] = Fx(self.popups, LayerID=lid, Name=NAME_OF[lid])
        return self.objs[lid]

    @property
    def FirstLayer(self):
        return self.obj(self.ids[0]) if self.ids else None

    def NextLayer(self, o):
        i = self.ids.index(o.LayerID) + 1
        return self.obj(self.ids[i]) if i < len(self.ids) else None

    @property
    def LayerObject_V7(self):
        return {lid: self.obj(lid) for lid in NAME_OF}

    def InsertLayer(self, lid):
        if lid in self.ids or lid not in self.insertable:
            self.popups.append(f"InsertLayer({NAME_OF[lid]})")
            raise PascalError("InsertLayer")
        self.ids.insert(-1, lid)

    def RemoveFromStack(self, o):
        lid = o.LayerID
        if lid not in self.ids or lid not in self.removable:
            self.popups.append(f"RemoveFromStack({NAME_OF[lid]})")
            raise PascalError("RemoveFromStack")
        self.ids.remove(lid)


def _run(names, ns, sources=None):
    bodies = dict(_utils())
    bodies.update(_main())
    bodies.update(_generic())
    bodies.update(_pcb())
    if sources:
        bodies.update(sources)
    return build(bodies, names, ns)


# ---------------------------------------------------------------------------
# PCBGeneric.pas
# ---------------------------------------------------------------------------

def _primitive_pos():
    """PCBPrimitivePos with its Var Found parameter returned alongside the
    result (the evaluator has no by-reference parameters)."""
    body = _generic()["PCBPrimitivePos"].replace(
        "Var Found : Boolean", "Found : Boolean")
    _, src = translate(body)
    return src.replace("return Result", "return Result, Found")


def test_a_component_body_position_is_refused_without_a_popup():
    """IPCB_ComponentBody declares no x/y (03-pcb-interfaces.md 3.11), so
    the Body.x branch popped a modal on every X/Y query or modify of a body
    and THEN reported Found=False. It must report Found=False without the
    read, and every type that has a position must still give it."""
    popups = Popups()
    ns = _ns(popups, PascalError=PascalError)
    exec(compile(_primitive_pos(), "<PCBPrimitivePos>", "exec"), ns)  # noqa: S102
    body = Fx(popups, ["x", "y"], ObjectId=OIDS["eComponentBodyObject"])
    assert ns["PCBPrimitivePos"](body, False, False) == (0, False)
    assert ns["PCBPrimitivePos"](body, True, False) == (0, False)
    assert popups == [], f"a body's position popped {popups}"

    pad = Fx(popups, ObjectId=OIDS["ePadObject"], x=11, y=22)
    txt = Fx(popups, ObjectId=OIDS["eTextObject"], XLocation=5, YLocation=6)
    assert ns["PCBPrimitivePos"](pad, True, False) == (22, True)
    assert ns["PCBPrimitivePos"](txt, False, False) == (5, True)
    assert popups == []


def test_no_routine_reads_a_position_off_a_component_body():
    """The rule, over the whole file: a local declared IPCB_ComponentBody
    is never asked for x or y (StandoffHeight and the other declared
    members stay)."""
    offenders = []
    for name, body in _generic().items():
        for var in _locals_of_type(body, "IPCB_ComponentBody"):
            if re.search(rf"(?i)\b{var}\.(x|y)\b", body):
                offenders.append(name)
    assert offenders == [], offenders


def _get_property_ns(popups):
    notes: list[tuple[str, str]] = []
    ns = _ns(popups, NotePropertyDiag=lambda k, p: notes.append((k, p)),
             GetLayerString=lambda l: NAME_OF.get(l, ""))
    ns["_notes"] = notes
    return ns


def test_get_property_counts_the_catch_of_an_unproven_connection_member():
    """IsRedundant and Mode on a ratsnest line have never run from a script
    and no question can be asked first, so the catch is COUNTED for the live
    probe. The reply is unchanged: ''."""
    popups = Popups()
    ns = _get_property_ns(popups)
    _run(["PCBConnRedundant", "PCBConnMode", "GetPCBProperty"], ns)
    conn = Fx(popups, ["IsRedundant", "Mode"],
              ObjectId=OIDS["eConnectionObject"])
    assert ns["GetPCBProperty"](conn, "IsRedundant") == ""
    assert ns["GetPCBProperty"](conn, "Mode") == ""
    assert ns["_caught"] == ["GetPCBProperty:IsRedundant",
                             "GetPCBProperty:Mode"]


def test_teardrop_is_asked_only_of_the_objects_teardrops_are_made_of():
    """Upstream's TearDrop read (2026.10.25 merge) asked Obj.TearDrop of any
    primitive. The name is resolved on the object at run time (IsKeepoutFor*
    on a via raised 'Undeclared identifier', AD 25.8), so it is asked only of
    tracks and arcs (probe P43) and regions (the geometry map's
    Region.TearDrop); anything else answers '' without the read."""
    popups = Popups()
    ns = _get_property_ns(popups)
    _run(["GetPCBProperty"], ns)
    for oid in ("ePadObject", "eViaObject", "eTextObject", "eFillObject",
                "eComponentBodyObject"):
        obj = Fx(popups, ["TearDrop"], ObjectId=OIDS[oid])
        assert ns["GetPCBProperty"](obj, "TearDrop") == "", oid
    assert popups == [], f"TearDrop was asked of {popups}"
    for oid in ("eTrackObject", "eArcObject", "eRegionObject"):
        obj = Fx(popups, ObjectId=OIDS[oid], TearDrop=True)
        assert ns["GetPCBProperty"](obj, "TearDrop") == "true", oid
    assert popups == [] and ns["_caught"] == []


def test_set_property_counts_the_catch_of_the_unproven_region_kind_write():
    popups = Popups()
    ns = _get_property_ns(popups)
    ns.update({f"eRegionKind_{k}": k for k in
               ("BoardCutout", "Cutout", "Copper", "NamedRegion", "Cavity")})
    # upstream's decimal-length refusal (2026.10.13 merge): SetPCBProperty
    # asks IsLengthProperty / IsFloatStr before writing a length.
    _run(["IsFloatStr", "IsLengthProperty", "SetPCBProperty"], ns)
    rgn = Fx(popups, ["Kind"], ObjectId=OIDS["eRegionObject"])
    assert ns["SetPCBProperty"](rgn, "Kind", "board_cutout") == -1
    assert ns["_caught"] == ["SetPCBProperty:Kind"]
    assert ns["_notes"] == [("failed", "Kind")]


# ---------------------------------------------------------------------------
# Declared-type rules (parsed from each routine's Var section)
# ---------------------------------------------------------------------------

def _var_types(body: str) -> dict[str, str]:
    m = re.search(r"(?is)\)\s*(?::\s*\w+\s*)?;\s*Var\b(.*?)\bBegin\b", body)
    types: dict[str, str] = {}
    if not m:
        return types
    for decl in m.group(1).split(";"):
        if ":" in decl:
            names, typ = decl.rsplit(":", 1)
            for n in names.split(","):
                if n.strip():
                    types[n.strip().lower()] = typ.strip()
    return types


def _locals_of_type(body: str, typ: str) -> list[str]:
    return [v for v, t in _var_types(body).items() if t.lower() == typ.lower()]


def test_var_parser_reads_declarations():
    """The rules below are only as good as this parser, so check it on a
    known routine before trusting a clean result from it."""
    types = _var_types(_pcb()["PCB_SetTrackWidth"])
    assert types["prim"] == "IPCB_Primitive"
    assert types["track"] == "IPCB_Track"
    assert types["matches"] == "TInterfaceList"


def _interface_list_traffic():
    """(routine, direction, local, declared type) for every value put into
    or taken out of a TInterfaceList in PCB.pas / PCBGeneric.pas."""
    seen = []
    for name, body in {**_pcb(), **_generic()}.items():
        types = _var_types(body)
        lists = {v for v, t in types.items() if t.lower() == "tinterfacelist"}
        for m in re.finditer(r"(\w+)\s*:=\s*(\w+)\.Items\s*\[", body):
            if m.group(2).lower() in lists:
                seen.append((name, "out", m.group(1),
                             types.get(m.group(1).lower(), "?")))
        for m in re.finditer(r"(\w+)\.Add\s*\(\s*(\w+)\s*\)", body):
            if m.group(1).lower() in lists:
                seen.append((name, "in", m.group(2),
                             types.get(m.group(2).lower(), "?")))
    return seen


def test_interface_lists_hold_the_base_primitive_in_and_out():
    """A TInterfaceList item assigned straight to a DERIVED interface skips
    QueryInterface and faults in oleaut32 on the first call through it
    (measured; PCB_SetTrackWidth carries the note). PCB_DistributeComponents
    did that with IPCB_Component on every call matching two parts, and
    PCB_BindPadNets with IPCB_Net for the second pin of every net. Every
    value now goes in and comes out as IPCB_Primitive and is narrowed after."""
    traffic = _interface_list_traffic()
    assert len(traffic) >= 10, f"the scan found too little: {traffic}"
    bad = [t for t in traffic if t[3].lower() != "ipcb_primitive"]
    assert bad == [], bad
    routines = {t[0] for t in traffic}
    assert {"PCB_DistributeComponents", "PCB_BindPadNets"} <= routines


_RULE_SUBTYPE = re.compile(r"IPCB_\w*(?:Constraint|Rule)$", re.I)


def test_rule_subtype_locals_are_narrowed_only_at_the_iterator_or_factory():
    """DelphiScript narrows a rule interface only when the typed local is
    assigned straight from the iterator (LmRulesSection) or the factory. The
    old PCB_GetRoomRules copied an IPCB_Rule local into its confinement
    local, which did not narrow, so BoundingRect popped once per room rule
    and every room came back with a zero box."""
    checked, bad = 0, []
    for name, body in _pcb().items():
        for var, typ in _var_types(body).items():
            if not _RULE_SUBTYPE.match(typ) or typ.lower() == "ipcb_rule":
                continue
            for m in re.finditer(rf"(?i)\b{var}\s*:=\s*([^;]+);", body):
                rhs = m.group(1).strip()
                checked += 1
                if not (re.fullmatch(r"\w+\.(?:First|Next)PCBObject", rhs)
                        or "PCBRuleFactory" in rhs):
                    bad.append((name, var, rhs))
    assert checked >= 15, "the scan found too few rule assignments to mean anything"
    assert bad == [], bad


def test_room_rules_report_the_box_and_names_from_the_narrowed_rule():
    popups = Popups()
    room = Fx(popups, RuleKind="conf", Name="Room1", Enabled=True,
              Scope1Expression="InComponentClass('A')", Comment="c",
              BoundingRect=Fx(popups, Left=10000, Bottom=20000, Right=30000,
                              Top=40000), Kind="in")
    other = Fx(popups, RuleKind="clear")
    board = Fx(popups, BoardIterator_Create=_Iter([other, room]),
               BoardIterator_Destroy=lambda i: None)
    # FloatToStr's spelling (a whole mil reads "1", not "1.0"), so the box
    # asserted is the reply byte for byte since the decimal-mil merge.
    ns = _ns(popups, eRule_ConfinementConstraint="conf", eConfineIn="in",
             GetPCBBoardAnywhere=lambda d: board,
             FloatToJsonStr=lambda f: "%.15g" % float(f))
    _run(["PCB_GetRoomRules"], ns)
    kind, data = ns["PCB_GetRoomRules"]({}, "r1")
    assert kind == "OK" and popups == []
    assert ('"name":"Room1"' in data and '"kind":"ConfineIn"' in data
            and '"x1":1,"y1":2,"x2":3,"y2":4' in data and '"count":1' in data)


def test_the_embedded_board_is_declared_as_its_subtype():
    """XLocation, DocumentPath, RowCount ... are IPCB_EmbeddedBoard members;
    on a local declared IPCB_Primitive each write was an undeclared
    identifier. Every local the embedded-board factory fills is declared as
    the subtype, the way PCB_Panelize already does."""
    found = 0
    for name, body in _pcb().items():
        types = _var_types(body)
        for m in re.finditer(r"(\w+)\s*:=\s*PCBServer\.PCBObjectFactory\s*\(\s*"
                             r"eEmbeddedBoardObject", body):
            found += 1
            assert types.get(m.group(1).lower(), "?") == "IPCB_EmbeddedBoard", \
                (name, m.group(1), types.get(m.group(1).lower()))
    assert found >= 2


def test_an_embedded_board_write_that_still_fails_is_counted():
    popups = Popups()
    msgs: list = []
    emb = Fx(popups, ["XLocation", "YLocation"], I_ObjectAddress="E")
    board = Fx(popups, AddPCBObject=lambda o: None, I_ObjectAddress="B",
               FileName="b.PcbDoc")
    ns = _ns(popups, GetPCBBoardAnywhere=lambda d: board,
             ResolveLayerId=lambda b, s: LAYERS["eTopLayer"],
             StrToIntDef=lambda s, d: int(s) if s else d,
             eNoDimension=0, eCreate_Default=0,
             PCBServer=_server(popups, msgs,
                               PCBObjectFactory=lambda *a: emb))
    _run(["PCB_PlaceEmbeddedBoard"], ns)
    kind, data = ns["PCB_PlaceEmbeddedBoard"](
        {"child_path": "C:\\c.PcbDoc", "rows": "2"}, "r")
    assert kind == "OK" and '"rows":2' in data
    assert emb.RowCount == 2 and emb.DocumentPath == "C:\\c.PcbDoc"
    assert ns["_caught"] == ["PCB_PlaceEmbeddedBoard:XLocation",
                             "PCB_PlaceEmbeddedBoard:YLocation"]


# ---------------------------------------------------------------------------
# PCB_RenumberPads: the DM layer is asked before PCBServer is named
# ---------------------------------------------------------------------------

class _Docs:
    """A DM project: its logical documents, by kind."""

    def __init__(self, kinds):
        self.docs = [type("D", (), {"DM_DocumentKind": k,
                                    "DM_FullPath": k + ".doc"})()
                     for k in kinds]
        self.DM_LogicalDocumentCount = len(self.docs)

    def DM_LogicalDocuments(self, i):
        return self.docs[i]


def _workspace(kind, open_kinds=()):
    """DM focus ``kind`` (None = nothing focused) with ``open_kinds`` as free
    documents, which are open by definition."""
    doc = None if kind is None else type("D", (), {"DM_DocumentKind": kind})()
    free = _Docs(open_kinds)
    return type("W", (), {"DM_FocusedDocument": doc, "DM_ProjectCount": 0,
                          "DM_FreeDocumentsProject": free})()


_GATE = ["PcbEditorDocOpen", "PcbLibMayBeCurrent"]


def _gate_bodies():
    return load(_ALTIUM / "Main.pas", minimum=40)


def _run_renumber(ns):
    build(_gate_bodies(), _GATE, ns)
    build(load(_ALTIUM / "Library.pas", minimum=40),
          ["CurrentFootprintNarrowed"], ns)
    _run(["PCB_RenumberPads"], ns)


@pytest.mark.parametrize("kind,open_kinds", [
    (None, ()), ("SCH", ("PCBLIB",)), ("PCB", ("PCB", "PCBLIB")),
    ("SCHLIB", ("PCBLIB",)), (None, ("SCH",))])
def test_renumber_pads_without_a_current_pcblib_never_names_pcbserver(
        kind, open_kinds):
    """Refused, without naming PCBServer, only on EVIDENCE the library
    lookup answers Nil: another kind focused, or no PCB document open."""
    popups = Popups()
    ns = _ns(popups, GetWorkspace=_workspace(kind, open_kinds),
             PCBServer=Undeclared(popups, "PCBServer"),
             StrToIntDef=lambda s, d: int(s) if s else d)
    _run_renumber(ns)
    reply = ns["PCB_RenumberPads"]({}, "r")
    assert popups == [], f"PCBServer was named with no PcbLib current: {popups}"
    assert reply == ("ERR", "NO_PCBLIB",
                     "No PCB library is active. Open the .PcbLib and select a "
                     "footprint.")


@pytest.mark.parametrize("kind", ["PCBLIB", None])
def test_renumber_pads_still_asks_the_library_when_one_may_be_current(kind):
    """A Nil DM focus right after a programmatic open is not evidence: with a
    PcbLib open the library is still asked, as before."""
    popups = Popups()
    lib = Fx(popups, CurrentComponent=None)
    ns = _ns(popups, GetWorkspace=_workspace(kind, ("PCBLIB",)),
             PCBServer=Fx(popups, GetCurrentPCBLibrary=lib),
             StrToIntDef=lambda s, d: int(s) if s else d)
    _run_renumber(ns)
    assert ns["PCB_RenumberPads"]({}, "r") == (
        "ERR", "NO_FOOTPRINT", "No footprint is selected")
    assert popups == []


# ---------------------------------------------------------------------------
# PCB_AddLayer / PCB_RemoveLayer
# ---------------------------------------------------------------------------

def _layer_ns(popups, stack, msgs):
    board = Fx(popups, LayerStack_V7=stack, I_ObjectAddress="B",
               FileName="b.PcbDoc")
    by_name = {v: k for k, v in NAME_OF.items()}
    return _ns(popups, GetPCBBoardAnywhere=lambda d: board,
               ResolveLayerId=lambda b, s: by_name.get(s, 0),
               PCBServer=_server(popups, msgs))


_STACK = ["TopLayer", "MidLayer1", "InternalPlane1", "BottomLayer"]


def _ids(names):
    by_name = {v: k for k, v in NAME_OF.items()}
    return [by_name[n] for n in names]


@pytest.mark.parametrize("layer", ["TopOverlay", "KeepOutLayer", "Mechanical1",
                                   "TopLayer", "MidLayer1", "InternalPlane1"])
def test_add_layer_never_hands_insertlayer_a_layer_it_must_refuse(layer):
    popups = Popups()
    msgs: list = []
    stack = Stack(popups, _ids(_STACK),
                  insertable=_ids([f"MidLayer{n}" for n in range(1, 31)]
                                  + [f"InternalPlane{n}" for n in range(1, 17)]))
    ns = _layer_ns(popups, stack, msgs)
    _run(["LayerInStackWalk", "PCB_AddLayer"], ns)
    reply = ns["PCB_AddLayer"]({"layer": layer}, "r")
    assert popups == [], popups
    assert reply == ("OK", '{"success":true,"layer":"' + layer + '"}')
    assert stack.ids == _ids(_STACK)


@pytest.mark.parametrize("layer", ["MidLayer2", "InternalPlane2"])
def test_add_layer_still_inserts_a_new_inner_layer(layer):
    popups = Popups()
    stack = Stack(popups, _ids(_STACK), insertable=_ids([layer]))
    ns = _layer_ns(popups, stack, [])
    _run(["LayerInStackWalk", "PCB_AddLayer"], ns)
    assert ns["PCB_AddLayer"]({"layer": layer}, "r")[0] == "OK"
    assert _ids([layer])[0] in stack.ids and popups == []


@pytest.mark.parametrize("layer", ["TopLayer", "BottomLayer", "Mechanical1",
                                   "TopOverlay", "MidLayer5", "InternalPlane3"])
def test_remove_layer_never_hands_removefromstack_a_layer_it_must_refuse(layer):
    popups = Popups()
    stack = Stack(popups, _ids(_STACK), removable=_ids(["MidLayer1",
                                                        "InternalPlane1"]))
    ns = _layer_ns(popups, stack, [])
    _run(["IsElectricalLayerId", "LayerIdIsSignal", "LayerInStackWalk",
          "PCB_RemoveLayer"], ns)
    reply = ns["PCB_RemoveLayer"]({"layer": layer}, "r")
    assert popups == [], popups
    assert reply == ("OK", '{"success":true,"layer":"' + layer + '"}')
    assert stack.ids == _ids(_STACK)


@pytest.mark.parametrize("layer", ["MidLayer1", "InternalPlane1"])
def test_remove_layer_still_removes_an_inner_layer_in_the_stack(layer):
    popups = Popups()
    stack = Stack(popups, _ids(_STACK), removable=_ids([layer]))
    ns = _layer_ns(popups, stack, [])
    _run(["IsElectricalLayerId", "LayerIdIsSignal", "LayerInStackWalk",
          "PCB_RemoveLayer"], ns)
    assert ns["PCB_RemoveLayer"]({"layer": layer}, "r")[0] == "OK"
    assert _ids([layer])[0] not in stack.ids and popups == []


# ---------------------------------------------------------------------------
# PCB_ModifyLayer: no conservative guard - every catch is counted
# ---------------------------------------------------------------------------

def test_modify_layer_counts_each_copper_and_dielectric_catch():
    popups = Popups()
    layer = Fx(popups, ["CopperThickness", "Dielectric"], Name="Mechanical 1")
    stack = Fx(popups)
    board = Fx(popups, LayerStack_V7=stack, I_ObjectAddress="B",
               FileName="b.PcbDoc")
    ns = _ns(popups, GetPCBBoardAnywhere=lambda d: board,
             ResolveStackLayerObject=lambda s, n: layer,
             StrToIntDef=lambda s, d: int(s) if s else d,
             ePrePreg="prepreg", eNoDielectric=0, eCore=1,
             eSurfaceMaterial=3, PCBServer=_server(popups))
    _run(["DielectricTypeToken", "PCB_ModifyLayer"], ns)
    kind, data = ns["PCB_ModifyLayer"](
        {"layer": "Mechanical1", "copper_thickness_mils": "2",
         "dielectric_type": "core"}, "r")
    # The reply is what the call gave after the dialogs.
    assert kind == "OK"
    assert data == ('{"success":false,"layer":"Mechanical 1",'
                    '"applied":{"copper_thickness_mils":-1.0,'
                    '"dielectric_type":""},'
                    '"rejected":["copper_thickness_mils","dielectric_type"]}')
    assert ns["_caught"] == ["PCB_ModifyLayer:CopperThickness",
                             "PCB_ModifyLayer:DielectricType",
                             "PCB_ModifyLayer:CopperThickness",
                             "DielectricTypeToken:Dielectric"]
    assert ns["_dirty"] == []


# ---------------------------------------------------------------------------
# PCB_SetPlaneNet: IPCB_LayerObject_V7 has no Net
# ---------------------------------------------------------------------------

def test_set_plane_net_never_touches_the_layer_objects_net():
    popups = Popups()
    plane = LAYERS["eInternalPlane1"]
    layer = Fx(popups, ["Net"], Name="GND plane")
    stack = Fx(popups, LayerObject_V7={plane: layer})

    class _PlaneNets:
        """IPCB_Board.InternalPlaneNetName[layer], upstream's route, kept by
        the 2026.10.13 merge: here a build whose write does not take, so
        the read-back stays empty and the call reports applied false."""

        def __getitem__(self, layer_id):
            return ""

        def __setitem__(self, layer_id, value):
            pass

    board = Fx(popups, LayerStack_V7=stack, I_ObjectAddress="B",
               FileName="b.PcbDoc", InternalPlaneNetName=_PlaneNets())
    ns = _ns(popups, GetPCBBoardAnywhere=lambda d: board,
             ResolveLayerId=lambda b, s: plane,
             FindNetByName=lambda b, n: Fx(popups, Name=n),
             PCBServer=_server(popups))
    _run(["PCB_SetPlaneNet"], ns)
    kind, data = ns["PCB_SetPlaneNet"]({"layer": "InternalPlane1",
                                        "net": "GND"}, "r")
    assert popups == [], f"LayerObj.Net was touched: {popups}"
    assert kind == "OK"
    assert data.startswith('{"success":false,"layer":"InternalPlane1",'
                           '"layer_name":"GND plane","net":"GND",'
                           '"net_readback":"","applied":false,')
    assert 'The plane still reads back as "". The net was NOT assigned.' in data
    assert ns["_dirty"] == []


# ---------------------------------------------------------------------------
# PCB_FilletCorners: a MkSet set is not an IPCB_LayerSet object
# ---------------------------------------------------------------------------

def test_no_mkset_set_is_handed_to_the_layerset_object_overload():
    """AddFilter_IPCB_LayerSet takes an IPCB_LayerSet OBJECT; a MkSet SET
    raised a type cast there (a modal per track endpoint in
    PCB_FilletCorners, whose corner search then found nothing). The set form
    is AddFilter_LayerSet, and the fillet search still filters by layer."""
    for fname in ("PCB.pas", "PCBGeneric.pas"):
        text = strip_comments((_ALTIUM / fname).read_text(encoding="utf-8"))
        hits = re.findall(r"AddFilter_IPCB_LayerSet\s*\(\s*MkSet\s*\(", text)
        assert hits == [], (fname, len(hits))
    fillet = _pcb()["PCB_FilletCorners"]
    assert re.search(r"SpatIter\.AddFilter_LayerSet\s*\(\s*MkSet\s*\(\s*"
                     r"Track\.Layer\s*\)\s*\)", fillet)


# ---------------------------------------------------------------------------
# PCB_SetMechLayerKind: MechPairIndexFor asks before each RemovePair
# ---------------------------------------------------------------------------

class MechPairs:
    """IPCB_MechanicalLayerPairs. AddPair on an existing pair raises (the
    unmeasured case the first AddPair is counted for); RemovePair on a pair
    that is not there raises (the modal the guard removes)."""

    def __init__(self, popups, pairs, symmetric):
        self.popups, self.pairs, self.symmetric = popups, list(pairs), symmetric

    def _has(self, a, b):
        return (a, b) in self.pairs or (self.symmetric and (b, a) in self.pairs)

    def PairDefined(self, a, b):
        return self._has(a, b)

    def AddPair(self, a, b):
        if (a, b) in self.pairs or (b, a) in self.pairs:
            self.popups.append("AddPair(existing)")
            raise PascalError("AddPair")
        self.pairs.append((a, b))
        return len(self.pairs) - 1

    def RemovePair(self, a, b):
        if (a, b) in self.pairs:
            self.pairs.remove((a, b))
        elif self.symmetric and (b, a) in self.pairs:
            self.pairs.remove((b, a))
        else:
            self.popups.append(f"RemovePair({a},{b})")
            raise PascalError("RemovePair")


_T, _B = LAYERS["eMechanical1"] + 12, LAYERS["eMechanical1"] + 13


@pytest.mark.parametrize("symmetric", [False, True])
@pytest.mark.parametrize("existing", [[(_T, _B)], [(_B, _T)]])
def test_rebuilding_a_pair_never_removes_one_that_is_not_there(symmetric,
                                                               existing):
    popups = Popups()
    pairs = MechPairs(popups, existing, symmetric)
    ns = _ns(popups)
    _run(["MechPairIndexFor"], ns)
    idx = ns["MechPairIndexFor"](pairs, _T, _B)
    assert idx >= 0 and pairs.pairs == [(_T, _B)]
    assert [p for p in popups if p.startswith("RemovePair")] == [], popups
    # The first AddPair is counted, not guarded (see its header).
    assert ns["_caught"] == ["PCB_SetMechLayerKind:AddPair"]


def test_a_new_pair_is_simply_added():
    popups = Popups()
    pairs = MechPairs(popups, [], False)
    ns = _ns(popups)
    _run(["MechPairIndexFor"], ns)
    assert ns["MechPairIndexFor"](pairs, _T, _B) == 0
    assert popups == [] and ns["_caught"] == []
    assert ns["MechPairIndexFor"](None, _T, _B) == -1


def test_set_mech_layer_kind_goes_through_the_pair_helper():
    body = _pcb()["PCB_SetMechLayerKind"]
    assert "MechPairIndexFor(MechPairs, TopL, BotL)" in body
    assert "RemovePair" not in body and "AddPair" not in body


# ---------------------------------------------------------------------------
# PCB_NormalizeVias: no rule member past Name / Descriptor, kind by literal
# ---------------------------------------------------------------------------

class _ViaBoardIter(_Iter):
    """The board iterator, filtered: vias for eViaObject, rules for
    eRuleObject (PCB_NormalizeVias walks both)."""

    def __init__(self, vias, rules, walks):
        super().__init__([])
        self.by = {OIDS["eViaObject"]: vias, OIDS["eRuleObject"]: rules}
        self.walks = walks

    def AddFilter_ObjectSet(self, s):
        for k in s:
            self.walks.append(k)
            self.items = list(self.by.get(k, []))


class _ViaBoard(Fx):
    @property
    def BoardIterator_Create(self):
        return _ViaBoardIter(self.vias, self.rules, self.walks)


#: The via-style members that do not exist on a base IPCB_Rule binding (and
#: the typed via-style read that has never run here).
_VIA_MEMBERS = ["PreferedWidth", "PreferedHoleWidth", "RuleKind"]

_VIA_DESC = ("Routing Via (MinHoleWidth=10mil) (MaxHoleWidth=10mil) "
             "(PreferredHoleWidth=10mil) (MinWidth=20mil) (MaxWidth=20mil) "
             "(PreferedWidth=20mil) (All)")


def _vias_ns(popups, vias, rule_of, msgs, rules=(), kinds=None):
    walks: list = []
    kinds = kinds if kinds is not None else []

    def dominant(v, k):
        kinds.append(k)
        return rule_of(v)

    board = _ViaBoard(popups, vias=vias, rules=list(rules), walks=walks,
                      BoardIterator_Destroy=lambda i: None,
                      FindDominantRuleForObject=dominant,
                      FileName="b.PcbDoc")
    ns = _ns(popups, GetPCBBoardAnywhere=lambda d: board,
             PCBServer=_server(popups, msgs), Round=round,
             CreateObject=lambda cls: _IList(), TInterfaceList=None)
    ns["_walks"] = walks
    return ns


class _IList(list):
    def Add(self, o):
        self.append(o)

    @property
    def Count(self):
        return len(self)

    @property
    def Items(self):
        return self


def _vias_build(ns):
    # The merged PCB_NormalizeVias (2026.10.13) also calls upstream's
    # filter / histogram helpers: tests/test_live_editor_component_is_never_
    # read.py normalize_vias_helpers builds or models them.
    from tests.test_live_editor_component_is_never_read import (
        normalize_vias_helpers)
    normalize_vias_helpers(ns)
    _run(["ViaRuleValue", "MilsToCoordF", "MMToCoord", "PCB_NormalizeVias"],
         ns)


#: The counts these tests have always pinned. The merged reply also carries
#: upstream's matched / unchanged / refusal counts and size histograms.
_NV_KEYS = ("checked", "changed", "free", "no_rule_values", "via_rule_values")


def _nv_counts(ns):
    import json
    kind, data = ns["PCB_NormalizeVias"]({}, "r")
    reply = json.loads(data)
    return kind, {k: reply[k] for k in _NV_KEYS}


def _via(popups, n, free=True):
    return Fx(popups, InComponent=not free, InPolygon=False,
              InDimension=False, I_ObjectAddress=f"V{n}", Size=1, HoleSize=1)


def test_normalize_vias_reads_no_constraint_member_off_any_rule():
    """3aca481 read PreferedWidth off the dominant base IPCB_Rule inside a
    'counted' Try; 6ff9e36 moved it to an IPCB_RoutingViaStyleRule local
    (a type name, two members and eRule_RoutingViaStyle that had never run
    here, for every rule on every board). Now the values are parsed from
    Rule.Descriptor (a proven base member), the rule kind is the literal 11,
    and no rule is asked anything but Name and Descriptor."""
    popups = Popups()
    msgs: list = []
    kinds: list = []
    vias = [_via(popups, n) for n in range(3)]
    dominant = Fx(popups, _VIA_MEMBERS, Name="RoutingVias")
    listed = Fx(popups, _VIA_MEMBERS, Name="RoutingVias", Descriptor=_VIA_DESC)
    clear = Fx(popups, _VIA_MEMBERS, Name="Clearance",
               Descriptor="Clearance Constraint (Gap=10mil) (All),(All)")
    ns = _vias_ns(popups, vias, lambda v: dominant, msgs,
                  rules=[clear, listed], kinds=kinds)
    _vias_build(ns)
    assert _nv_counts(ns) == ("OK", {'checked': 3, 'changed': 3, 'free': 3, 'no_rule_values': 0, 'via_rule_values': 1})
    assert popups == [], popups
    assert ns["_caught"] == []
    assert kinds == [11, 11, 11], "the rule kind must be the literal 11"
    assert msgs == ["Begin", "End"] * 3
    assert all(v.Size == 200000 and v.HoleSize == 100000 for v in vias)


def test_normalize_vias_leaves_a_via_whose_rule_gave_no_values():
    popups = Popups()
    msgs: list = []
    vias = [_via(popups, n) for n in range(3)]
    thin = Fx(popups, _VIA_MEMBERS, Name="Thin")
    other = Fx(popups, _VIA_MEMBERS, Name="Unlisted")
    listed = Fx(popups, _VIA_MEMBERS, Name="Thin", Descriptor=_VIA_DESC)
    ns = _vias_ns(popups, vias,
                  lambda v: other if v.I_ObjectAddress == "V1" else thin,
                  msgs, rules=[listed])
    _vias_build(ns)
    assert _nv_counts(ns) == ("OK", {'checked': 3, 'changed': 2, 'free': 3, 'no_rule_values': 1, 'via_rule_values': 1})
    assert [(v.Size, v.HoleSize) for v in vias] == [
        (200000, 100000), (1, 1), (200000, 100000)]
    assert msgs == ["Begin", "End", "Begin", "End"]
    assert popups == []


def test_normalize_vias_with_a_template_descriptor_changes_nothing_and_says_so():
    """pcb_get_rule_properties' own docstring records a via descriptor in
    the TEMPLATE form, which carries no numbers. Then nothing is cached,
    no via changes, and the reply says why (via_rule_values 0)."""
    popups = Popups()
    msgs: list = []
    vias = [_via(popups, n) for n in range(2)]
    dominant = Fx(popups, _VIA_MEMBERS, Name="RoutingVias")
    listed = Fx(popups, _VIA_MEMBERS, Name="RoutingVias",
                Descriptor="Routing Via (Templates Used To Check Via: "
                           "v30h10m0mx0) (All)")
    ns = _vias_ns(popups, vias, lambda v: dominant, msgs, rules=[listed])
    _vias_build(ns)
    assert _nv_counts(ns) == ("OK", {'checked': 2, 'changed': 0, 'free': 2, 'no_rule_values': 2, 'via_rule_values': 0})
    assert [(v.Size, v.HoleSize) for v in vias] == [(1, 1), (1, 1)]
    assert msgs == [] and popups == []


def test_normalize_vias_reads_no_rule_on_a_board_without_free_vias():
    """6ff9e36 walked every rule (RuleKind, then the via-style members) on
    every board before looking at a single via. A board whose vias are all
    inside components now never creates the rule iterator at all."""
    popups = Popups()
    msgs: list = []
    vias = [_via(popups, n, free=False) for n in range(2)]
    poison = Fx(popups, _VIA_MEMBERS + ["Name", "Descriptor"])
    ns = _vias_ns(popups, vias, lambda v: poison, msgs, rules=[poison])
    _vias_build(ns)
    assert _nv_counts(ns) == ("OK", {'checked': 2, 'changed': 0, 'free': 0, 'no_rule_values': 0, 'via_rule_values': 0})
    assert OIDS["eRuleObject"] not in ns["_walks"]
    assert popups == [] and msgs == []


@pytest.mark.parametrize("desc,key,want", [
    ("(PreferedWidth=20mil)", "PreferedWidth", 200000),
    ("x (preferedwidth = 20 mil)", "PreferedWidth", 200000),   # spaces
    ("(PreferedWidthX=20mil)", "PreferedWidth", 0),            # not the key
    ("(PreferedHoleWidth=0.254mm)", "PreferedHoleWidth", 100000),
    ("(PreferedHoleWidth=10mil)", "PreferedWidth", 0),          # never inside
    ("(PreferedWidth=20)", "PreferedWidth", 0),                 # no unit
    ("(PreferedWidth=abc)", "PreferedWidth", 0),
    ("(PreferedWidth=-5mil)", "PreferedWidth", 0),
    ("", "PreferedWidth", 0),
    ("ROUTING VIA (PREFEREDWIDTH=1.5MIL)", "PreferedWidth", 15000),
])
def test_via_rule_value_parses_only_a_keyed_number_with_a_unit(desc, key,
                                                             want):
    popups = Popups()

    def float_def(s, d):
        try:
            return float(s)
        except ValueError:
            return d

    ns = _ns(popups, Round=round, StrToFloatDef=float_def)
    _run(["ViaRuleValue", "MilsToCoordF", "MMToCoord"], ns)
    assert ns["ViaRuleValue"](desc, key) == want


def test_normalize_vias_names_no_unproven_identifier():
    """The class, not the site: the handler and its parser name none of
    the identifiers that had never run in this repo."""
    for fname in ("PCB_NormalizeVias", "ViaRuleValue"):
        code = strip_comments(_pcb()[fname])
        for ident in ("IPCB_RoutingViaStyleRule", "eRule_RoutingViaStyle",
                      ".PreferedWidth", ".PreferedHoleWidth", ".RuleKind"):
            assert ident not in code, f"{fname} names {ident}"


# ---------------------------------------------------------------------------
# PCB_MakePasteGrid: a zero grid size divided by zero outside any Try
# ---------------------------------------------------------------------------

def test_paste_grid_with_zero_grid_size_does_not_divide_by_zero():
    popups = Popups()
    pad = Fx(popups, Name="0", Layer=LAYERS["eTopLayer"], Rotation=0,
             TopXSize=1000000, TopYSize=1000000, X=0, Y=0)

    comp = Fx(popups, Name=Fx(popups, Text="U1"),
              GroupIterator_Create=_Iter([pad]),
              GroupIterator_Destroy=lambda i: None)
    board = Fx(popups, BoardIterator_Create=_Iter([comp]),
               BoardIterator_Destroy=lambda i: None)
    ns = _ns(popups, GetPCBBoardAnywhere=lambda d: board, Trunc=int,
             StrToIntDef=lambda s, d: int(s) if s else d,
             PCBServer=_server(popups))
    _run(["PCB_MakePasteGrid"], ns)
    reply = ns["PCB_MakePasteGrid"](
        {"designator": "U1", "pad_name": "0", "min_grid_size_mils": "0"}, "r")
    # Word for word what the dispatcher answered after the division dialog.
    assert reply == ("ERR", "INTERNAL_ERROR",
                     "Unhandled exception processing: pcb.make_paste_grid")
    assert popups == []
