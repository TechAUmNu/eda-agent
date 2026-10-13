# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Generic.pas asks before it touches a member some types lack.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs (measured; recorded beside
WriteFileContent in Main.pas). Generic.pas's late-bound property getter and
setter, its sheet-entry delete and its constraint-group read all reached
members on objects that do not have them, so a Try/Except that "handled" the
failure still popped a dialog per object and stalled the polling loop.

Every test here EXECUTES the real Pascal through tests/pascal_eval.py. The
fixture objects model Altium's interfaces: touching a member a type does not
declare records a MODAL and then raises PascalError, which is what the Except
sees once somebody presses OK. Each test fails on the code before the fix
(eec334c) because a modal was recorded where the fix asks first, or because
a site that cannot be guarded yet does not count its catch.

THE GUARDS ARE ALLOWLISTS since 2026.10.12.01 (the comment block above
SchObjectHasText). They were denylists on the premise that an unchecked type
would at worst raise a COUNTED exception; live 2026-10-12 (P21) a member the
object's class lacks is the engine's halt instead, so a governed name is now
asked only of the types listed for it, and every other type answers ''
noted 'unreadable' (tests/test_live_03_findings.py holds the sweep's pairs).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_GENERIC = _ROOT / "scripts" / "altium" / "Generic.pas"

_HELPERS = ["SchObjectIsPlainShape", "SchObjectLacksVertexArray",
            "SchPropAbsentOn", "SchVertexReadName", "SchObjectLacksIsMirrored",
            "SchObjTypeTag", "SchObjectHasText", "SchObjectHasIsHidden",
            "SchObjectHasOrientation", "PinEndX", "PinEndY",
            # upstream's TextColor guard (2026.10.13 merge): MatchesFilter
            # and SetSchProperty preflight through these.
            "SchObjectHasTextColor", "UnsupportedSchProperty"]


def _generic():
    return load(_GENERIC, minimum=120)


# ---------------------------------------------------------------------------
# Fixtures: Altium objects that pop a modal on an undeclared member
# ---------------------------------------------------------------------------

_TYPES = ["eNetLabel", "ePort", "ePowerObject", "eSchComponent", "eWire",
          "eBus", "eBusEntry", "eParameter", "eParameterSet", "ePin", "eLabel",
          "eLine", "eRectangle", "eSheetSymbol", "eSheetEntry", "eNoERC",
          "eJunction", "eImage", "eTextFrame", "eNote", "eProbe",
          "eHarnessConnector", "eCrossSheetConnector", "eCompileMask"]


class Ctx:
    def __init__(self):
        self.modals: list[tuple[str, str]] = []
        self.caught: list[str] = []
        self.diag: list[tuple[str, str]] = []
        self.ids: dict[str, int] = {}

    def name_of(self, oid):
        return next(k for k, v in self.ids.items() if v == oid)


class Loc:
    def __init__(self, x, y):
        self.X, self.Y = x, y


class SchObj:
    """Members not in ``members`` are undeclared: reading or writing one
    records a modal and raises, exactly the live sequence."""

    def __init__(self, ctx, type_name, **members):
        d = {"ObjectId": ctx.ids[type_name], "Location": Loc(10, 20),
             "UniqueId": "UID", "Color": 0, "OwnerPartId": 0,
             "OwnerPartDisplayMode": 0}
        d.update(members)
        object.__setattr__(self, "_d", d)
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_t", type_name)

    def __getattr__(self, name):
        d = object.__getattribute__(self, "_d")
        if name in d:
            return d[name]
        ctx = object.__getattribute__(self, "_ctx")
        ctx.modals.append((object.__getattribute__(self, "_t"), name))
        raise PascalError(name)

    def __setattr__(self, name, value):
        d = object.__getattribute__(self, "_d")
        if name in d:
            d[name] = value
            return
        ctx = object.__getattribute__(self, "_ctx")
        ctx.modals.append((object.__getattribute__(self, "_t"), name))
        raise PascalError(name)


def _vertex_members(n):
    pts = [Loc(100 * i, 7 * i) for i in range(1, n + 1)]
    return {"GetState_VerticesCount": n,
            "GetState_Vertex": lambda i: pts[int(i) - 1],
            "LineWidth": 1}


def _members(type_name):
    """The declared members of each type, from docs/altium-delphiscript/02."""
    t = type_name
    if t in ("eWire", "eBus"):
        return _vertex_members(2)
    return {
        "eNetLabel": {"Text": "N", "FontId": 1, "Orientation": 0,
                      "Justification": 0, "IsMirrored": False},
        "ePort": {"Name": "P", "Style": 0, "IOType": 0, "Alignment": 0,
                  "Width": 100, "AreaColor": 0, "TextColor": 0, "FontId": 1},
        "ePowerObject": {"Text": "VCC", "Style": 0, "Orientation": 0},
        "eSchComponent": {"LibReference": "NE555", "SourceLibraryName": "L",
                          "DesignItemId": "D", "ComponentDescription": "Timer",
                          "CurrentPartID": 2, "PartCount": 4,
                          "IsMirrored": True, "Orientation": 0,
                          "SetState_CurrentPartID": lambda v: None,
                          "SetState_IsMirrored": lambda v: None},
        "eParameter": {"Name": "Value", "Text": "10k", "IsHidden": False},
        "ePin": {"Name": "A", "Designator": "1", "Electrical": 0,
                 "PinLength": 300, "Orientation": 0, "IsHidden": False},
        "eLabel": {"Text": "L", "FontId": 1, "Orientation": 0},
        "eRectangle": {"Corner": Loc(1, 2), "LineWidth": 1, "IsSolid": True,
                       "AreaColor": 0},
        "eSheetSymbol": {"XSize": 500, "YSize": 800, "LineWidth": 1,
                         "AreaColor": 0, "IsSolid": True},
        "eSheetEntry": {"Name": "E", "Side": 0, "DistanceFromTop": 100,
                        "IOType": 0, "Style": 0},
        "eNoERC": {"Orientation": 0},
        "eJunction": {},
        "eImage": {"Corner": Loc(1, 2)},
        "eTextFrame": {"Corner": Loc(1, 2), "Text": "T", "FontId": 1},
        "eNote": {"Corner": Loc(1, 2), "Text": "T", "FontId": 1},
        "eHarnessConnector": {"XSize": 500, "YSize": 800},
        "eCrossSheetConnector": {"Text": "N", "Style": 0, "Orientation": 0},
        "eLine": {"Corner": Loc(1, 2), "LineWidth": 1},
        "eBusEntry": {"Corner": Loc(1, 2), "LineWidth": 1},
        "eProbe": {}, "eParameterSet": {"Name": "PS"}, "eCompileMask": {},
    }[t]


def _ns(ctx, bodies):
    """A namespace with every e-constant the bodies name and the Utils / Main
    routines they call, modelled."""
    names = set()
    for b in bodies.values():
        names.update(re.findall(r"\be[A-Z]\w*", strip_comments(b)))
    for i, n in enumerate(sorted(names | set(_TYPES)), start=1):
        ctx.ids[n] = 1000 + i
    ns = dict(ctx.ids)

    def note(kind, prop):
        ctx.diag.append((kind, prop))

    ns.update({
        "RecordCaughtException": ctx.caught.append,
        "NotePropertyDiag": note,
        "CoordToMils": lambda c: int(c),
        "MilsToCoord": lambda m: int(m),
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "StrToBool": lambda v: str(v).strip().lower() in ("true", "1"),
        "PinElectricalToStr": lambda e: f"elec{e}",
        "ElectricalOrdinal": lambda v: 0,
        "GetSheetSymbolText": lambda o, p: "",
        "GetSchComponentSubText": lambda o, p: "",
        "SetSheetSymbolText": lambda o, p, v: True,
        "SetSchComponentSubText": lambda o, p, v: None,
        "RecordCastError": lambda w: None,
        # Library.pas: keeps the held component's recorded name true on a
        # LibReference write (tests/test_popup_free_crossfile.py runs it).
        "NoteLibComponentRenamed": lambda o, v: None,
    })
    return ns


def _present(bodies, names):
    """Translate what this revision has. A helper the old code lacks is
    simply absent, so the old BODY runs and fails for the field reason."""
    return [n for n in names if n in bodies]


def _prop_ns():
    ctx = Ctx()
    g = _generic()
    ns = _ns(ctx, g)
    build(g, _present(g, _HELPERS) + ["GetSchVertexProperty", "GetSchProperty",
                                      "SetSchProperty", "MatchesFilter"], ns)
    return ctx, ns


def _obj(ctx, t, **extra):
    m = _members(t)
    m.update(extra)
    return SchObj(ctx, t, **m)


# The (type, property) pairs that popped and are now asked first. Each type
# is one whose interface certainly lacks the member.
_PLAIN = ["eNetLabel", "ePort", "ePowerObject", "eWire", "eBus", "eParameter",
          "eLabel", "eRectangle", "eSheetEntry", "eNoERC", "eJunction",
          "eImage", "eTextFrame", "eNote", "eCrossSheetConnector"]
_COMPONENT_ONLY = ["LibReference", "SourceLibraryName", "DesignItemId",
                   "ComponentDescription"]
_ABSENT = (
    [(t, p) for t in _PLAIN + ["ePin"] for p in _COMPONENT_ONLY]
    + [(t, p) for t in _PLAIN + ["eSchComponent", "eSheetSymbol",
                                 "eHarnessConnector"]
       for p in ("Electrical", "PinLength")]
    + [(t, p) for t in _PLAIN + ["ePin"] for p in ("XSize", "YSize")]
)
_NO_VERTICES = [t for t in _PLAIN if t not in ("eWire", "eBus")] + [
    "eSchComponent", "ePin", "eSheetSymbol", "eHarnessConnector"]
_VERTEX_NAMES = ["VerticesCount", "Vertex.1.X", "Vertex.1.Y", "Vertex.2.X",
                 "Vertex.2.Y", "Vertices", "Vertex1.X", "VertexLast.Y"]


# ---------------------------------------------------------------------------
# GetSchProperty / GetSchVertexProperty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_name,prop", _ABSENT)
def test_getter_asks_before_a_member_the_type_lacks(type_name, prop):
    ctx, ns = _prop_ns()
    got = ns["GetSchProperty"](_obj(ctx, type_name), prop)
    assert ctx.modals == [], f"{prop} on {type_name} popped a modal"
    assert got == ""
    assert ctx.diag == [("unreadable", prop)]   # the gap is named


@pytest.mark.parametrize("type_name", _NO_VERTICES)
@pytest.mark.parametrize("prop", _VERTEX_NAMES)
def test_vertex_reads_ask_before_a_type_with_no_vertex_array(type_name, prop):
    ctx, ns = _prop_ns()
    assert ns["GetSchProperty"](_obj(ctx, type_name), prop) == ""
    assert ctx.modals == [], f"{prop} on {type_name} popped a modal"


@pytest.mark.parametrize("prop", ["CurrentPartID", "PartCount"])
@pytest.mark.parametrize("type_name", ["eNetLabel", "ePin", "eWire",
                                       "eSheetSymbol", "eProbe", "eLine"])
def test_component_part_reads_ask_for_a_component_first(prop, type_name):
    """Comp := Obj does not narrow: on any other type this was an undeclared
    identifier. ObjectId decides, so even unprobed types are safe here."""
    ctx, ns = _prop_ns()
    assert ns["GetSchProperty"](_obj(ctx, type_name), prop) == ""
    assert ctx.modals == []


def test_getter_isMirrored_on_a_mirrorless_type_does_not_ask_the_object():
    ctx, ns = _prop_ns()
    for t in ("eWire", "eBus", "eJunction", "eRectangle"):
        assert ns["GetSchProperty"](_obj(ctx, t), "IsMirrored") == ""
    assert ctx.modals == []


def test_getter_still_reads_every_member_the_type_has():
    """The guards never take away a read that works today."""
    ctx, ns = _prop_ns()
    get = ns["GetSchProperty"]
    comp = _obj(ctx, "eSchComponent")
    assert get(comp, "LibReference") == "NE555"
    assert get(comp, "ComponentDescription") == "Timer"
    assert get(comp, "CurrentPartID") == "2"
    assert get(comp, "PartCount") == "4"
    assert get(comp, "IsMirrored") == "true"
    assert get(_obj(ctx, "ePin"), "Electrical") == "elec0"
    assert get(_obj(ctx, "ePin"), "PinLength") == "300"
    assert get(_obj(ctx, "eSheetSymbol"), "XSize") == "500"
    assert get(_obj(ctx, "eHarnessConnector"), "YSize") == "800"
    assert get(_obj(ctx, "eNetLabel"), "IsMirrored") == "false"
    wire = _obj(ctx, "eWire")
    assert get(wire, "VerticesCount") == "2"
    assert get(wire, "Vertex.2.X") == "200"
    assert get(wire, "Vertices") == "100,7;200,14"
    assert get(wire, "VertexLast.Y") == "14"
    assert ctx.modals == [] and ctx.caught == []


def test_getter_never_asks_a_type_outside_the_allowlist():
    """eLine / eProbe used to keep 'today's path' because no list named
    them. The SDK interfaces lack these members, and an absent member is the
    engine's halt (P21, 2026-10-12): they are not asked any more, even when
    the object would have answered."""
    ctx, ns = _prop_ns()
    get = ns["GetSchProperty"]
    line = _obj(ctx, "eLine", **_vertex_members(3))
    assert get(line, "VerticesCount") == ""
    assert get(line, "Vertices") == ""
    probe = _obj(ctx, "eProbe", LibReference="X", XSize=5, Electrical=1)
    assert get(probe, "LibReference") == ""
    assert get(probe, "XSize") == ""
    assert get(probe, "Electrical") == ""
    assert ctx.modals == []
    assert ("unreadable", "VerticesCount") in ctx.diag


def test_getter_counts_an_ordinary_raise_on_an_allowed_pair():
    """Name on a probe is ALLOWED (declared, P15). If the getter raises an
    ordinary exception there, the Try stays the backstop and the catch is
    COUNTED with the property and the type. Name on a wire is not asked."""
    ctx, ns = _prop_ns()
    assert ns["GetSchProperty"](_obj(ctx, "eProbe"), "Name") == ""
    assert ctx.modals == [("eProbe", "Name")]
    assert ctx.caught == [f"GetSchProperty:Name:{ctx.ids['eProbe']}"]
    assert ns["GetSchProperty"](_obj(ctx, "eWire"), "Name") == ""
    assert ctx.modals == [("eProbe", "Name")]


def test_vertex_helper_counts_an_ordinary_raise_on_an_allowed_type():
    """A polyline is allowed; an ordinary raise there is counted. A bus
    entry is not asked at all."""
    ctx, ns = _prop_ns()
    assert ns["GetSchVertexProperty"](_obj(ctx, "eBusEntry"), "Vertices") == ""
    assert ctx.modals == []
    poly = SchObj(ctx, "ePolyline")
    assert ns["GetSchVertexProperty"](poly, "Vertices") == ""
    assert ctx.modals == [("ePolyline", "GetState_VerticesCount")]
    assert ctx.caught == [
        f"GetSchVertexProperty:VerticesCount:{ctx.ids['ePolyline']}"]


def test_a_bad_filter_name_no_longer_pops_once_per_object():
    """The field shape: one unsupported name in a FILTER was one modal for
    every object of the type on the sheet."""
    ctx, ns = _prop_ns()
    labels = [_obj(ctx, "eNetLabel") for _ in range(25)]
    hits = [ns["MatchesFilter"](o, "LibReference=NE555") for o in labels]
    assert hits == [False] * 25
    assert ctx.modals == []


# ---------------------------------------------------------------------------
# SetSchProperty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("type_name,prop", _ABSENT + [
    (t, "Description") for t in _PLAIN + ["ePin"]])
def test_setter_reports_failed_without_touching_a_missing_member(type_name,
                                                                  prop):
    ctx, ns = _prop_ns()
    rc = ns["SetSchProperty"](_obj(ctx, type_name), prop, "1")
    assert ctx.modals == [], f"writing {prop} on {type_name} popped a modal"
    assert rc == -1                         # what the outer Except gave
    assert ctx.diag == [("failed", prop)]
    assert ctx.caught == []


@pytest.mark.parametrize("prop", ["CurrentPartID", "PartCount"])
@pytest.mark.parametrize("type_name", ["eNetLabel", "ePin", "eWire", "eLine"])
def test_setter_part_writes_ask_for_a_component_first(prop, type_name):
    """Both writes raised and were swallowed, leaving Result 1 with no diag;
    doing nothing leaves exactly that."""
    ctx, ns = _prop_ns()
    rc = ns["SetSchProperty"](_obj(ctx, type_name), prop, "2")
    assert ctx.modals == []
    assert rc == 1 and ctx.diag == []


def test_setter_isMirrored_on_a_mirrorless_type_is_not_written_and_fails():
    """Not written, and reported 'failed' - it used to keep Result 1, a
    write claimed that never happened (review of 2026.10.12.01)."""
    ctx, ns = _prop_ns()
    for t in ("eWire", "eBus", "eJunction", "eRectangle"):
        assert ns["SetSchProperty"](_obj(ctx, t), "IsMirrored", "true") == -1
    assert ctx.modals == []
    assert ctx.diag == [("failed", "IsMirrored")] * 4


def test_setter_isMirrored_admitted_type_writes_and_a_raise_is_counted_failed():
    ctx, ns = _prop_ns()
    label = _obj(ctx, "eNetLabel")
    assert ns["SetSchProperty"](label, "IsMirrored", "true") == 1
    assert label.IsMirrored is True          # declared: the write lands
    assert ns["SetSchProperty"](_obj(ctx, "eParameterSet"), "IsMirrored",
                                "true") == -1
    assert ctx.modals == [("eParameterSet", "IsMirrored")]
    assert ctx.caught == [
        f"SetSchProperty:IsMirrored:{ctx.ids['eParameterSet']}"]
    assert ctx.diag == [("failed", "IsMirrored")]


def test_setter_still_writes_every_member_the_type_has():
    ctx, ns = _prop_ns()
    s = ns["SetSchProperty"]
    comp = _obj(ctx, "eSchComponent")
    assert s(comp, "LibReference", "LM358") == 1 and comp.LibReference == "LM358"
    assert s(comp, "Description", "Op amp") == 1
    assert comp.ComponentDescription == "Op amp"
    assert s(comp, "PartCount", "5") == 1 and comp.PartCount == 5
    assert s(comp, "CurrentPartID", "3") == 1 and comp.CurrentPartID == 3
    pin = _obj(ctx, "ePin")
    assert s(pin, "PinLength", "200") == 1 and pin.PinLength == 200
    sym = _obj(ctx, "eSheetSymbol")
    assert s(sym, "XSize", "900") == 1 and sym.XSize == 900
    probe = _obj(ctx, "eProbe", LibReference="X")
    assert s(probe, "LibReference", "Y") == -1 and probe.LibReference == "X"
    assert ctx.modals == [] and ctx.diag == [("failed", "LibReference")]


def test_setter_read_only_vertex_names_still_answer_unknown():
    """The vertex names are getter-only. Asking first must not turn the
    setter's 'unknown' into 'failed'."""
    ctx, ns = _prop_ns()
    for t in ("eSchComponent", "eWire"):
        assert ns["SetSchProperty"](_obj(ctx, t), "VerticesCount", "3") == 0
    assert ctx.diag == [("unknown", "VerticesCount")] * 2
    assert ctx.modals == []


def test_setter_counts_an_ordinary_raise_on_an_allowed_pair():
    ctx, ns = _prop_ns()
    assert ns["SetSchProperty"](_obj(ctx, "eProbe"), "Name", "X") == -1
    assert ctx.diag == [("failed", "Name")]
    assert ctx.caught == [f"SetSchProperty:Name:{ctx.ids['eProbe']}"]
    # A wire is not written at all: failed, no modal, nothing counted.
    assert ns["SetSchProperty"](_obj(ctx, "eWire"), "Name", "X") == -1
    assert ctx.caught == [f"SetSchProperty:Name:{ctx.ids['eProbe']}"]


# ---------------------------------------------------------------------------
# ProcessSchDocObjects: a sheet entry is removed by its OWNER only
# ---------------------------------------------------------------------------

class Iter:
    def __init__(self, items):
        self.items, self.i, self.only = list(items), 0, None

    def AddFilter_ObjectSet(self, s):
        self.only = set(s)

    def _cur(self):
        while self.i < len(self.items) and (
                self.items[self.i].ObjectId not in self.only):
            self.i += 1
        return self.items[self.i] if self.i < len(self.items) else None

    @property
    def FirstSchObject(self):
        self.i = 0
        return self._cur()

    @property
    def NextSchObject(self):
        self.i += 1
        return self._cur()


class Sym:
    def __init__(self, ctx, name, entries):
        self.ctx, self.name, self.entries = ctx, name, entries
        self.ObjectId = ctx.ids["eSheetSymbol"]

    @property
    def SchIterator_Create(self):
        return Iter(self.entries)

    def SchIterator_Destroy(self, it):
        pass

    def RemoveSchObject(self, obj):
        if obj in self.entries:
            self.entries.remove(obj)
            return
        self.ctx.modals.append(("eSheetSymbol", f"RemoveSchObject@{self.name}"))
        raise PascalError("wrong parent")


class Doc:
    def __init__(self, ctx, top, syms):
        self.ctx, self.top, self.syms = ctx, top, syms
        self.DocumentName = "C:\\P\\Top.SchDoc"
        self.GraphicallyInvalidate = None

    @property
    def SchIterator_Create(self):
        allv = list(self.top)
        for s in self.syms:
            allv.append(s)
            allv.extend(s.entries)
        return Iter(allv)

    def SchIterator_Destroy(self, it):
        pass

    def RemoveSchObject(self, obj):
        if obj in self.top:
            self.top.remove(obj)
        # A sheet entry: silently ignored, as the source comment records.


class _Proc:
    def PreProcess(self, *a):
        pass

    def PostProcess(self, *a):
        pass


class _SchServer:
    def __init__(self, doc=None, factory=None):
        self.ProcessControl = _Proc()
        self.GetCurrentSchDocument = doc
        self._factory = factory

    def SchObjectFactory(self, kind, how):
        return self._factory


def _delete_ns():
    ctx = Ctx()
    g = dict(_generic())
    # pascal_eval has no Var parameters; the counter is not what is checked.
    g["ProcessSchDocObjects"] = re.sub(
        r"\bVar\s+TotalMatched", "TotalMatched", g["ProcessSchDocObjects"],
        count=1)
    ns = _ns(ctx, g)
    ns.update({"MkSet": lambda *a: set(a), "SchServer": _SchServer(),
               "DeleteParametersAnyOwner": lambda *a: None,
               "EscapeJsonString": lambda s: s})
    build(g, _present(g, _HELPERS + [
        "GetSchVertexProperty", "GetSchProperty", "MatchesFilter",
        "SheetEntryUnderMatching", "SheetEntryOwnerMatching",
        "ProcessSchDocObjects"]), ns)
    return ctx, ns


def test_sheet_entry_delete_asks_only_its_owner():
    """Owner third of three symbols: before, S1 and S2 each raised (a modal
    per non-owner symbol, per deleted entry)."""
    ctx, ns = _delete_ns()
    a1, a2 = _obj(ctx, "eSheetEntry", Name="A"), _obj(ctx, "eSheetEntry", Name="A")
    b = _obj(ctx, "eSheetEntry", Name="B")
    s1, s2, s3 = Sym(ctx, "S1", [b]), Sym(ctx, "S2", []), Sym(ctx, "S3", [a1, a2])
    doc = Doc(ctx, [], [s1, s2, s3])
    ns["ProcessSchDocObjects"](doc, ctx.ids["eSheetEntry"], "Name=A", "", "",
                               "delete", "C:\\P\\Top.SchDoc", 0, 0)
    assert ctx.modals == [], ctx.modals
    assert s3.entries == [] and s1.entries == [b]
    assert ctx.caught == []


def test_other_types_still_delete_at_document_level():
    ctx, ns = _delete_ns()
    n1, n2 = _obj(ctx, "eNetLabel", Text="X"), _obj(ctx, "eNetLabel", Text="Y")
    doc = Doc(ctx, [n1, n2], [Sym(ctx, "S1", [])])
    ns["ProcessSchDocObjects"](doc, ctx.ids["eNetLabel"], "Text=X", "", "",
                               "delete", "C:\\P\\Top.SchDoc", 0, 0)
    assert doc.top == [n2] and ctx.modals == []


def test_sheet_entry_helpers_name_the_owner():
    ctx, ns = _delete_ns()
    a = _obj(ctx, "eSheetEntry", Name="A")
    s1, s2 = Sym(ctx, "S1", [_obj(ctx, "eSheetEntry", Name="B")]), Sym(ctx, "S2", [a])
    doc = Doc(ctx, [], [s1, s2])
    assert ns["SheetEntryOwnerMatching"](doc, "Name=A") is s2
    assert ns["SheetEntryUnderMatching"](s2, "Name=A") is a
    assert ns["SheetEntryOwnerMatching"](doc, "Name=Z") is None
    assert ns["SheetEntryUnderMatching"](None, "Name=A") is None


# ---------------------------------------------------------------------------
# Gen_GetConstraintGroups: the DM document, not the editor's
# ---------------------------------------------------------------------------

class _Group:
    DM_ConstraintCount = 1
    DM_TargetKindString = "Net"
    DM_TargetId = "CLK"

    def DM_Constraints(self, j):
        c = type("C", (), {})()
        c.DM_Kind, c.DM_Data = "PIN", "AB7"
        return c


class _DMDoc:
    DM_ConstraintGroupCount = 1

    def DM_ConstraintGroups(self, i):
        return _Group()


def _cg_ns(dm_doc):
    ctx = Ctx()
    g = _generic()
    ns = _ns(ctx, g)
    asked = []
    sch = SchObj(ctx, "eSheetSymbol")       # any ISch object: no DM_ members
    object.__getattribute__(sch, "_d")["DocumentName"] = "C:\\P\\Top.SchDoc"
    object.__setattr__(sch, "_t", "ISch_Document")
    ns.update({
        "SchServer": _SchServer(doc=sch),
        "ResolveLoadedDocPath": lambda p: p,
        "FindDMDocumentByPath": lambda p: asked.append(p) or dm_doc,
        "BuildSuccessResponse": lambda rid, body: body,
        "BuildErrorResponse": lambda rid, code, msg: code,
        "EscapeJsonString": lambda s: s,
    })
    build(g, ["Gen_GetConstraintGroups"], ns)
    return ctx, ns, asked


def test_constraint_groups_never_ask_the_editor_document():
    ctx, ns, _ = _cg_ns(None)
    assert ns["Gen_GetConstraintGroups"]("{}", "r") == '{"groups":[],"count":0}'
    assert ctx.modals == [], "DM_ConstraintGroupCount asked of ISch_Document"


def test_constraint_groups_read_the_dm_twin_at_the_sheet_path():
    ctx, ns, asked = _cg_ns(_DMDoc())
    reply = ns["Gen_GetConstraintGroups"]("{}", "r")
    assert asked == ["C:\\P\\Top.SchDoc"]
    assert reply == ('{"groups":[{"target_kind":"Net","target_id":"CLK",'
                     '"constraint_count":1,"constraints":[{"kind":"PIN",'
                     '"data":"AB7"}]}],"count":1}')
    assert ctx.modals == []


# ---------------------------------------------------------------------------
# needs_probe sites that cannot be guarded conservatively: counted
# ---------------------------------------------------------------------------

def _common_ns(ctx, g):
    ns = _ns(ctx, g)
    ns.update({"MkSet": lambda *a: set(a),
               "BuildSuccessResponse": lambda rid, body: body,
               "BuildErrorResponse": lambda rid, code, msg: code,
               "EscapeJsonString": lambda s: s,
               "GetPCBBoardAnywhere": lambda d: None,
               "GetWorkspace": None,
               "SchDeselectAllObjects": lambda d: None,
               "SchRegisterObject": lambda d, o: None,
               "MarkDocDirtyByPath": lambda p: None,
               "Point": lambda x, y: Loc(x, y)})
    return ns


def test_clear_highlights_counts_a_selection_catch_and_keeps_the_count():
    ctx = Ctx()
    g = _generic()
    ns = _common_ns(ctx, g)
    wire = _obj(ctx, "eWire", Selection=True)
    pin = _obj(ctx, "ePin")                    # the disputed sub-object
    label = _obj(ctx, "eNetLabel", Selection=False)
    doc = Doc(ctx, [wire, pin, label], [])
    ns["SchServer"] = _SchServer(doc=doc)
    build(g, _present(g, _HELPERS) + ["Gen_ClearHighlights"], ns)
    reply = ns["Gen_ClearHighlights"]("r")
    assert reply == '{"success":true,"context":"schematic","cleared":1}'
    assert wire.Selection is False
    assert ctx.caught == [f"Gen_ClearHighlights:Selection:{ctx.ids['ePin']}"]


def test_highlight_net_counts_a_selection_catch_and_keeps_the_count():
    ctx = Ctx()
    g = _generic()
    ns = _common_ns(ctx, g)
    ns["ExtractJsonValue"] = lambda p, k: {"net_name": "CLK"}.get(k, "")
    label = _obj(ctx, "eNetLabel", Selection=False, Text="CLK")
    entry = _obj(ctx, "eSheetEntry", Name="clk")
    doc = Doc(ctx, [label, entry], [])
    ns["SchServer"] = _SchServer(doc=doc)
    build(g, _present(g, _HELPERS) + ["Gen_HighlightNet"], ns)
    reply = ns["Gen_HighlightNet"]("{}", "r")
    assert reply.endswith('"highlighted":2}')
    assert label.Selection is True
    assert ctx.caught == [f"Gen_HighlightNet:Selection:{ctx.ids['eSheetEntry']}"]


class _Proj:
    def __init__(self, ctx, path=None):
        self.ctx, self.path = ctx, path
        self.DM_LogicalDocumentCount = 0

    @property
    def DM_ProjectFullPath(self):
        if self.path is None:
            self.ctx.modals.append(("IProject", "DM_ProjectFullPath"))
            raise PascalError("free documents")
        return self.path


def test_write_project_file_counts_an_unreadable_focused_path():
    ctx = Ctx()
    g = _generic()
    ns = _common_ns(ctx, g)
    ws = type("W", (), {})()
    ws.DM_FocusedProject = _Proj(ctx, None)
    client = type("Cl", (), {"GetDocumentByPath": staticmethod(lambda p: None)})
    ns.update({"Client": client, "Gen_ProjectFileWanted": lambda *a: False})
    build(g, ["Gen_WriteProjectFile"], ns)
    out = ns["Gen_WriteProjectFile"](ws, None, "C:\\P\\a.PrjPcb", "C:\\P\\s.SchDoc",
                                     1, False)
    assert out == "not_focused"
    assert ctx.caught == ["Gen_WriteProjectFile:DM_ProjectFullPath"]


def test_write_project_file_readable_path_is_unchanged():
    ctx = Ctx()
    g = _generic()
    ns = _common_ns(ctx, g)
    ws = type("W", (), {})()
    ws.DM_FocusedProject = _Proj(ctx, "C:\\P\\a.PrjPcb")
    calls = iter([False, True])
    client = type("Cl", (), {"GetDocumentByPath": staticmethod(lambda p: None)})
    ns.update({"Client": client,
               "Gen_ProjectFileWanted": lambda *a: next(calls),
               "ResetParameters": lambda: None,
               "AddStringParameter": lambda k, v: None,
               "RunProcess": lambda p: None})
    build(g, ["Gen_WriteProjectFile"], ns)
    out = ns["Gen_WriteProjectFile"](ws, None, "C:\\P\\a.PrjPcb", "C:\\P\\s.SchDoc",
                                     1, False)
    assert out == "save_object" and ctx.caught == []


def _placement_ns(ctx, g, make):
    """``make(ctx)`` builds the factory's object once the ids exist."""
    ns = _common_ns(ctx, g)
    doc = Doc(ctx, [], [])
    doc.RegisterSchObjectInContainer = lambda o: None
    made = make(ctx)
    ns["SchServer"] = _SchServer(doc=doc, factory=made)
    return ns, made


def test_harness_type_write_is_counted_when_it_pops():
    ctx = Ctx()
    g = _generic()
    ns, made = _placement_ns(ctx, g, lambda c: _obj(c, "eHarnessConnector"))
    ns["ExtractJsonValue"] = lambda p, k: {"harness_type": "USB"}.get(k, "")
    build(g, ["Gen_PlaceHarnessConnector"], ns)
    reply = ns["Gen_PlaceHarnessConnector"]("{}", "r")
    assert reply.endswith('"harness_type":"USB"}')
    assert ctx.caught == ["Gen_PlaceHarnessConnector:HarnessType"]


def test_harness_type_write_lands_when_the_member_exists():
    ctx = Ctx()
    g = _generic()
    ns, made = _placement_ns(
        ctx, g, lambda c: _obj(c, "eHarnessConnector", HarnessType=""))
    ns["ExtractJsonValue"] = lambda p, k: {"harness_type": "USB"}.get(k, "")
    build(g, ["Gen_PlaceHarnessConnector"], ns)
    ns["Gen_PlaceHarnessConnector"]("{}", "r")
    assert made.HarnessType == "USB" and ctx.caught == []


# The cross-sheet connector's Side write is GONE (P21 halted on it,
# 2026-10-12); tests/test_live_03_findings.py pins the refusal.


# Gen_AddSheet / Gen_DeleteSheet are long handlers whose path read is one
# line; the Except of THAT Try must begin with its counter and then do
# exactly today's fallback, nothing else.

def _excepts_of(body, try_stmt):
    text = strip_comments(body)
    pat = re.compile(r"\bTry\s+" + re.escape(try_stmt) + r"\s*Except\s+(.*?)\bEnd\s*;",
                     re.S)
    return [[s.strip() for s in m.group(1).split(";") if s.strip()]
            for m in pat.finditer(text)]


@pytest.mark.parametrize("fn,stmt,tag,fallback", [
    ("Gen_AddSheet", "ProjFile := Project.DM_ProjectFullPath;",
     "Gen_AddSheet:DM_ProjectFullPath", "ProjFile := ''"),
    ("Gen_DeleteSheet", "ProjFile := Project.DM_ProjectFullPath;",
     "Gen_DeleteSheet:DM_ProjectFullPath", "ProjFile := ''"),
    ("Gen_DeleteSheet",
     "WasFocusedProject := UpperCase(FocusedProj.DM_ProjectFullPath) = "
     "UpperCase(ProjFile);",
     "Gen_DeleteSheet:FocusedProjectFullPath", "WasFocusedProject := False"),
])
def test_project_path_reads_count_their_catch(fn, stmt, tag, fallback):
    found = _excepts_of(_generic()[fn], stmt)
    assert len(found) == 1, f"{fn}: the Try around {stmt!r} moved or doubled"
    assert found[0] == [f"RecordCaughtException('{tag}')", fallback]
