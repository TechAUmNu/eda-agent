# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Audit.pas and Project.pas ask first instead of catching, executed.

Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs (measured; recorded beside
WriteFileContent in Main.pas). So a Try is not a guard: the user gets a
popup and the polling loop waits on it. Each site here now checks first,
and the check's False branch does exactly what the Except branch did.

THE MODEL. Every fixture that stands for an Altium or RTL call that RAISES
appends to ``modals`` and then raises PascalError (what the translated
Except catches). The handlers run from their real source through
tests/pascal_eval.py, and every test asserts BOTH the reply and that no
modal fired. On the pre-fix source (eec334c) each test fails on one or
the other.

Two rules of the engine are modelled explicitly:

* ILayer is undeclared, so ``ILayer.IsSignalLayer`` raises on every call.
* Members resolve against the DECLARED type (lint.py
  RULE_PCB_SUBTYPE_ON_BASE, tests/test_pcb_position_per_type.py). That is
  a static property of the source, so it is applied statically: in the
  translated Python, a subtype-only member read off a local DECLARED
  ``IPCB_Primitive`` is replaced by a call that raises. A local narrowed
  first (``PadObj := Prim2; PadObj.x``) reads normally, as in Altium.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.pascal_eval import BUILTINS, PascalError, build, translate
from tests.pascal_source import load
from tests.live_2026_10_11 import file_reader

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"
# The two files under test. Point these at a pre-fix snapshot to watch the
# tests fail for the field reason.
_AUDIT_PAS = _ALTIUM / "Audit.pas"
_PROJECT_PAS = _ALTIUM / "Project.pas"


def _bodies(path: Path) -> dict[str, str]:
    return load(path, minimum=30)


# ---------------------------------------------------------------------------
# Delphi RTL and bridge-helper models shared by both worlds
# ---------------------------------------------------------------------------

def _delphi_trim(s: str) -> str:
    i, j = 0, len(s)
    while i < j and s[i] <= " ":
        i += 1
    while j > i and s[j - 1] <= " ":
        j -= 1
    return s[i:j]


def _extract_file_path(s: str) -> str:
    """SysUtils.ExtractFilePath: up to and including the last '\\' or ':'."""
    k = max(s.rfind("\\"), s.rfind(":"))
    return s[:k + 1]


def _extract_file_name(s: str) -> str:
    """SysUtils.ExtractFileName: after the last '\\' or ':'."""
    k = max(s.rfind("\\"), s.rfind(":"))
    return s[k + 1:]


def _json_str(name, value):
    return json.dumps(str(name)) + ":" + json.dumps(str(value))


def _ok(rid, data):
    try:
        return ("ok", json.loads(data))
    except ValueError:
        # The pre-fix PadsNearBoardEdge left a dangling comma behind a
        # raise; keep the reply so the modal assertion reports first.
        return ("ok-unparseable", data)


def _err(rid, code, msg):
    return ("err", code, msg)


def _extract_json_value(params, key):
    v = json.loads(params or "{}").get(key, "")
    return v if isinstance(v, str) else json.dumps(v)


def _str_to_float_def(s, d):
    try:
        return float(s)
    except (TypeError, ValueError):
        return d


class _Recorder:
    def __init__(self):
        self.modals: list[str] = []
        self.caught: list[str] = []
        self.processes: list[str] = []

    def modal(self, what: str):
        """An exception the engine would show as a modal: record, raise."""
        self.modals.append(what)
        raise PascalError(what)


def _common_ns(rec: _Recorder) -> dict:
    ns = dict(BUILTINS)
    ns.update({
        "Trim": _delphi_trim,
        "ExtractFilePath": _extract_file_path,
        "ExtractFileName": _extract_file_name,
        "JsonStr": _json_str,
        "JsonInt": lambda n, v: json.dumps(str(n)) + ":" + str(int(v)),
        "JsonFloat": lambda n, v: json.dumps(str(n)) + ":" + repr(float(v)),
        "JsonRaw": lambda n, raw: json.dumps(str(n)) + ":" + raw,
        "JsonObj": lambda body: "{" + body + "}",
        "BuildSuccessResponse": _ok,
        "BuildErrorResponse": _err,
        "ExtractJsonValue": _extract_json_value,
        "EscapeJsonString": lambda s: json.dumps(str(s))[1:-1],
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "StrToFloatDef": _str_to_float_def,
        "Round": round,
        "Abs": abs,
        "RecordCaughtException": rec.caught.append,
        "ResetParameters": lambda: None,
        "AddStringParameter": lambda k, v: None,
        "RunProcess": rec.processes.append,
    })
    return ns


# ---------------------------------------------------------------------------
# The PCB world (Audit.pas)
# ---------------------------------------------------------------------------

E_NO, E_TOP, E_BOT, E_PLANE1, E_MULTI = 0, 1, 32, 39, 74
E_TRACK, E_ARC, E_PAD, E_VIA = "eTrackObject", "eArcObject", "ePadObject", "eViaObject"
MIL = 10000

# Members a PCB SUBTYPE publishes and IPCB_Primitive does not (the lint
# rule's list plus the centre position).
_SUBTYPE_ONLY = ("x", "y", "x1", "y1", "x2", "y2", "StartX", "StartY",
                 "EndX", "EndY", "HoleSize", "SizeOnLayer", "IsPadRemoved",
                 "IntersectLayer")


class _Net:
    def __init__(self, name):
        self.Name = name


class _Rect:
    def __init__(self, l, b, r, t):
        self.Left, self.Bottom, self.Right, self.Top = l, b, r, t


class _Planes(dict):
    """Via.IsConnectedToPlane[layer]: False for a plane not listed."""

    def __missing__(self, key):
        return False


class _Prim:
    _ids = 0

    def __init__(self, oid, layer, net=None, **kw):
        _Prim._ids += 1
        self.I_ObjectAddress = _Prim._ids
        self.ObjectId = oid
        self.Layer = layer
        self.InNet = net is not None
        self.Net = _Net(net) if net else None
        self.InComponent = False
        self.Component = None
        self.TearDrop = False
        self.Name = kw.pop("name", "")
        self.removed_on = kw.pop("removed_on", ())
        self.planes = _Planes(kw.pop("planes", {}))
        self.edge_dist = kw.pop("edge_dist", 10 ** 9)
        for k, v in kw.items():
            setattr(self, k, v)

    # Pads and vias.
    def IntersectLayer(self, layer):
        return True

    def IsPadRemoved(self, layer):
        return layer in self.removed_on

    def SizeOnLayer(self, layer):
        return 40 * MIL

    HoleSize = 20 * MIL

    @property
    def IsConnectedToPlane(self):
        return self.planes

    @property
    def BoundingRectangle(self):
        return _Rect(*self.bbox())

    def BoundingRectangleOnLayer(self, layer):
        return _Rect(*self.bbox())

    def bbox(self):
        if self.ObjectId == E_TRACK:
            return (min(self.x1, self.x2), min(self.y1, self.y2),
                    max(self.x1, self.x2), max(self.y1, self.y2))
        h = 20 * MIL
        return (self.x - h, self.y - h, self.x + h, self.y + h)


def track(layer, net, a, b):
    return _Prim(E_TRACK, layer, net, x1=a[0] * MIL, y1=a[1] * MIL,
                 x2=b[0] * MIL, y2=b[1] * MIL)


def pad(layer, net, at, **kw):
    return _Prim(E_PAD, layer, net, x=at[0] * MIL, y=at[1] * MIL, **kw)


def via(net, at, **kw):
    return _Prim(E_VIA, E_MULTI, net, x=at[0] * MIL, y=at[1] * MIL, **kw)


class _LayerObj:
    def __init__(self, lid):
        self.LayerID = lid


class _Stack:
    def __init__(self, ids):
        self.objs = [_LayerObj(i) for i in ids]

    @property
    def FirstLayer(self):
        return self.objs[0] if self.objs else None

    def NextLayer(self, layer):
        i = self.objs.index(layer)
        return self.objs[i + 1] if i + 1 < len(self.objs) else None


class _Iter:
    def __init__(self, board, spatial):
        self.board, self.spatial = board, spatial
        self.oset = None
        self.lset = None
        self.area = None
        self.items: list = []

    def AddFilter_ObjectSet(self, s):
        self.oset = s

    def AddFilter_LayerSet(self, s):
        self.lset = s

    def AddFilter_IPCB_LayerSet(self, s):
        self.lset = s

    def AddFilter_Area(self, l, b, r, t):
        self.area = (l, b, r, t)

    def AddFilter_Method(self, m):
        pass

    def _wanted(self, p):
        if self.oset is not None and p.ObjectId not in self.oset:
            return False
        if self.lset == "signal" and not (E_TOP <= p.Layer <= E_BOT):
            return False
        if isinstance(self.lset, set) and p.Layer not in self.lset:
            return False
        if self.area is not None:
            l, b, r, t = p.bbox()
            al, ab, ar, at = self.area
            if r < al or l > ar or t < ab or b > at:
                return False
        return True

    @property
    def FirstPCBObject(self):
        self.items = [p for p in self.board.prims if self._wanted(p)]
        return self.NextPCBObject

    @property
    def NextPCBObject(self):
        return self.items.pop(0) if self.items else None


class _Board:
    def __init__(self, prims, layers=(E_TOP, E_PLANE1, E_BOT), touching=()):
        self.prims = list(prims)
        self.LayerStack_V7 = _Stack(layers)
        self.BoardOutline = object()
        self.touching = {frozenset((a.I_ObjectAddress, b.I_ObjectAddress))
                         for a, b in touching}

    @property
    def BoardIterator_Create(self):
        return _Iter(self, False)

    @property
    def SpatialIterator_Create(self):
        return _Iter(self, True)

    def BoardIterator_Destroy(self, it):
        pass

    def SpatialIterator_Destroy(self, it):
        pass

    def PrimPrimDistance(self, a, b):
        if a is self.BoardOutline:
            return b.edge_dist
        key = frozenset((a.I_ObjectAddress, b.I_ObjectAddress))
        return 0 if key in self.touching else 10 ** 9


def _pcb_primitive_pos(obj, want_y, found):
    """PCBGeneric.pas PCBPrimitivePos (a Var parameter, outside the
    evaluator's subset; its own per-type tests live in
    test_pcb_position_per_type.py). Pads and vias only, here."""
    if obj.ObjectId in (E_PAD, E_VIA):
        return obj.y if want_y else obj.x
    return 0


class _ILayer:
    def __init__(self, rec):
        self.rec = rec

    def IsSignalLayer(self, layer):
        self.rec.modal("Undeclared identifier: ILayer")


def _primitive_locals(pascal_body: str) -> list[str]:
    """Locals the routine DECLARES as IPCB_Primitive."""
    var = re.search(r"(?is)\bVar\b(.*?)\bBegin\b", pascal_body).group(1)
    names: list[str] = []
    for decl in var.split(";"):
        if ":" in decl and decl.split(":")[1].strip() == "IPCB_Primitive":
            names += [n.strip() for n in decl.split(":")[0].split(",")]
    return names


def _run_audit(name: str, board: _Board, params: str = "{}"):
    rec = _Recorder()
    bodies = _bodies(_AUDIT_PAS)
    utils = load(_ALTIUM / "Utils.pas", minimum=40)
    ns = _common_ns(rec)
    ns.update({
        "eNoLayer": E_NO, "eTopLayer": E_TOP, "eBottomLayer": E_BOT,
        "eMultiLayer": E_MULTI, "eTrackObject": E_TRACK, "eArcObject": E_ARC,
        "ePadObject": E_PAD, "eViaObject": E_VIA,
        "eFillObject": "eFillObject", "eRegionObject": "eRegionObject",
        "eProcessAll": 0, "AllLayers": "all",
        "LayerSet": type("LS", (), {"SignalLayers": "signal"})(),
        "MkSet": lambda *a: set(a),
        "GetPCBBoardAnywhere": lambda d: board,
        "CoordToMils": lambda c: int(c) // MIL,
        "MilsToCoord": lambda m: int(m) * MIL,
        "GetLayerString": lambda layer: {E_TOP: "TopLayer",
                                         E_BOT: "BottomLayer"}.get(layer, "L%d" % layer),
        "PCBPrimitivePos": _pcb_primitive_pos,
        "ILayer": _ILayer(rec),
        # Audit.pas PadEdgeGapMils (upstream's outline geometry, taken by the
        # 2026.10.13 merge; its own tests are test_pads_near_a_round_edge.py):
        # here the fixture's modelled gap, in mils. It types the pad or via
        # itself, so it is not the base-typed read these tests police.
        "PadEdgeGapMils": lambda outline, obj: obj.edge_dist / MIL,
        # Utils.pas FloatToJsonStr: Delphi FloatToStr, '.' separator.
        "FloatToJsonStr": lambda v: format(float(v), ".15g"),
    })

    def base_member(obj, member):
        rec.modal(f"Undeclared identifier: {member}")

    ns["_base_member"] = base_member
    build(utils, ["LayerIdIsSignal"], ns)
    build(bodies, ["PointsCloseEnough"], ns)
    _, src = translate(bodies[name])
    base_locals = _primitive_locals(bodies[name])
    assert base_locals, (
        f"{name}: no IPCB_Primitive local found, so the declared-type rule "
        f"would be applied to nothing and every position read would pass")
    for local in base_locals:
        src = re.sub(r"\b(%s)\.(%s)\b" % (re.escape(local), "|".join(_SUBTYPE_ONLY)),
                     r'_base_member(\1, "\2")', src)
    exec(compile(src, f"<pascal {name}>", "exec"), ns)   # noqa: S102
    return ns[name](params, "rid"), rec


# -- Audit_FindViaAntennas ---------------------------------------------------

def test_via_antennas_are_found_without_ilayer():
    """ILayer.IsSignalLayer raised on the first via, every call; the Except
    left Connected at 0 for every via, so NO antenna was ever reported."""
    a = via("N1", (100, 100))                                   # top only
    b = via("N2", (300, 100))                                   # top + bottom
    c = via("N3", (500, 100), planes={E_PLANE1: True})          # top + plane
    d = via("N4", (700, 100), planes={E_PLANE1: True})          # plane only
    ta = track(E_TOP, "N1", (0, 100), (100, 100))
    tb1 = track(E_TOP, "N2", (200, 100), (300, 100))
    tb2 = track(E_BOT, "N2", (300, 100), (300, 300))
    tc = track(E_TOP, "N3", (400, 100), (500, 100))
    board = _Board([a, b, c, d, ta, tb1, tb2, tc],
                   touching=[(ta, a), (tb1, b), (tb2, b), (tc, c)])
    reply, rec = _run_audit("Audit_FindViaAntennas", board)
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply[0] == "ok"
    data = reply[1]
    assert data["checked"] == 4
    assert data["violations"] == 2
    got = sorted((i["net"], i["at"], i["connected_layers"]) for i in data["items"])
    assert got == [("N1", "(100,100)", 1), ("N4", "(700,100)", 1)], (
        "the plane-only via must still be judged through IsConnectedToPlane "
        "(the plane layer is not a signal layer)")


# -- Audit_FindPadsCenterNotConnected ----------------------------------------

def test_through_hole_pads_are_checked_without_ilayer():
    """Same identifier on the through-hole branch: a modal per pad per
    layer, after which the pad was never checked."""
    off = pad(E_MULTI, "N1", (100, 100), name="1")
    on = pad(E_MULTI, "N2", (300, 100), name="2")
    t_off = track(E_TOP, "N1", (0, 100), (95, 100))     # touches, ends off-centre
    t_on = track(E_BOT, "N2", (300, 100), (300, 300))   # ends at the centre
    board = _Board([off, on, t_off, t_on],
                   touching=[(t_off, off), (t_on, on)])
    reply, rec = _run_audit("Audit_FindPadsCenterNotConnected", board)
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    data = reply[1]
    assert data["checked"] == 2
    assert data["violations"] == 1
    assert [(i["pin"], i["net"], i["layer"], i["at"]) for i in data["items"]] == [
        ("1", "N1", "TopLayer", "(100,100)")]


# -- Audit_FindRemovedPadShapes ----------------------------------------------

def test_removed_pad_shapes_report_the_real_position():
    """Obj.x / Obj.y on the declared IPCB_Primitive: a modal for every pad
    and via, and the Except then reported every item at (0,0)."""
    p = pad(E_MULTI, "N1", (120, 340), removed_on=(E_BOT,))
    v = via("N2", (500, 600))
    v.SizeOnLayer = lambda layer: 20 * MIL if layer == E_TOP else 40 * MIL
    board = _Board([p, v])
    reply, rec = _run_audit("Audit_FindRemovedPadShapes", board)
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    data = reply[1]
    assert data["checked"] == 2
    assert sorted((i["kind"], i["layer"], i["at"]) for i in data["items"]) == [
        ("pad", "BottomLayer", "(120,340)"), ("via", "TopLayer", "(500,600)")]


# -- Audit_FindPadsNearBoardEdge ---------------------------------------------

def test_pads_near_the_edge_are_reported_with_their_position():
    """Obj.x on the declared base raised while the entry was being built:
    a modal, Violations counted, and no item (a dangling comma instead)."""
    near = pad(E_TOP, "N1", (15, 400), edge_dist=10 * MIL)
    far = pad(E_TOP, "N2", (500, 400), edge_dist=300 * MIL)
    nvia = via("N3", (20, 20), edge_dist=5 * MIL)
    board = _Board([near, far, nvia])
    reply, rec = _run_audit("Audit_FindPadsNearBoardEdge", board,
                            '{"clearance_mils": "25"}')
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    data = reply[1]
    assert data["checked"] == 3
    assert data["violations"] == 2
    assert sorted((i["kind"], i["distance_mils"], i["at"]) for i in data["items"]) == [
        ("pad", 10, "(15,400)"), ("via", 5, "(20,20)")]


# -- Audit_FindBadConnections ------------------------------------------------

def test_pad_landed_endpoints_are_not_reported_as_dangling():
    """Prim2.x on the declared base raised for every endpoint near a pad;
    the Except left Found False, so every pad-terminated track was a
    false dangling endpoint."""
    p1 = pad(E_TOP, "N1", (100, 100))
    p2 = pad(E_MULTI, "N1", (300, 100))
    v = via("N2", (600, 100))
    t1 = track(E_TOP, "N1", (100, 100), (300, 100))     # pad to pad: clean
    t2 = track(E_TOP, "N2", (450, 100), (600, 100))     # free end at 450
    board = _Board([p1, p2, v, t1, t2])
    reply, rec = _run_audit("Audit_FindBadConnections", board,
                            '{"tolerance_mils": "1"}')
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    data = reply[1]
    assert data["checked"] == 2
    assert data["violations"] == 1, data["items"]
    assert [(i["net"], i["at"]) for i in data["items"]] == [("N2", "(450,100)")]


# -- Audit_VariantNotFitted --------------------------------------------------

class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_variant_not_fitted_compares_the_literal_kind():
    """eVariation_NotFitted appears nowhere else in the bridge and is not
    proven declared; an undeclared identifier there is a modal OUTSIDE any
    Try. Every other variation read uses the literal 1 (not fitted)."""
    comps = [_Obj(DM_UniqueId=u, DM_PhysicalDesignator=d, DM_Comment="c")
             for u, d in (("U1", "R1"), ("U2", "R2"), ("U3", "R3"))]
    kinds = {"U1": 1, "U2": 0, "U3": 2}
    variant = _Obj(DM_Description="Lite",
                   DM_FindComponentVariationByUniqueId=lambda u: _Obj(
                       DM_VariationKind=kinds[u]))
    flat = _Obj(DM_ComponentCount=3, DM_Components=lambda i: comps[i])
    project = _Obj(DM_CurrentProjectVariant=variant, DM_DocumentFlattened=flat)
    rec = _Recorder()
    ns = _common_ns(rec)
    ns["GetWorkspace"] = _Obj(DM_FocusedProject=project)
    bodies = _bodies(_AUDIT_PAS)
    try:
        build(bodies, ["Audit_VariantNotFitted"], ns)
        reply = ns["Audit_VariantNotFitted"]("{}", "rid")
    except NameError as exc:
        pytest.fail(f"the handler still names an undeclared identifier: {exc}")
    data = reply[1]
    assert (data["checked"], data["violations"]) == (3, 1)
    assert [i["designator"] for i in data["items"]] == ["R1"]


# ---------------------------------------------------------------------------
# The file / workspace world (Project.pas)
# ---------------------------------------------------------------------------

_BAD_NAME_CHARS = '<>"|?*'


class _FS:
    """A Windows-shaped file system with only drive C:."""

    def __init__(self, rec, dirs=(), files=None):
        self.rec = rec
        self.dirs = {d.rstrip("\\").upper() for d in dirs} | {"C:"}
        self.files = {k.upper(): v for k, v in (files or {}).items()}
        self.assigned = None
        self.copy_fault = False

    def _dir_ok(self, d):
        d = d.rstrip("\\")
        return d == "" or d.upper() in self.dirs

    def DirectoryExists(self, d):
        return d.rstrip("\\").upper() in self.dirs

    def FileExists(self, p):
        return p.upper() in self.files

    def ForceDirectories(self, d):
        if d == "":
            self.rec.modal("EInOutError: Unable to create directory")
        d = d.rstrip("\\")
        if not d.upper().startswith("C:") or any(c in d for c in _BAD_NAME_CHARS):
            return False
        parts = d.split("\\")
        for k in range(1, len(parts) + 1):
            self.dirs.add("\\".join(parts[:k]).upper())
        return True

    def _creatable(self, p):
        name = _extract_file_name(p)
        return (p != "" and name != "" and self._dir_ok(_extract_file_path(p))
                and not any(c in _BAD_NAME_CHARS or c < " " for c in name))

    def AssignFile(self, f, p):
        self.assigned = p

    def Rewrite(self, f):
        p = self.assigned
        if not self._creatable(p):
            self.rec.modal("EInOutError: Rewrite " + p)
        self.files[p.upper()] = ""

    def Write(self, f, s):
        self.files[self.assigned.upper()] += s

    def CopyFile(self, src, dst, fail_if_exists):
        if self.copy_fault or not self._dir_ok(_extract_file_path(dst)):
            self.rec.modal("CopyFile " + dst)
        self.files[dst.upper()] = self.files[src.upper()]
        return True


class _ServerDoc:
    def __init__(self, client, path):
        self.client, self.path = client, path

    @property
    def Focus(self):
        self.client.focused.append(self.path)


class _Client:
    def __init__(self, rec, fs, open_docs=(), unloadable=()):
        self.rec, self.fs = rec, fs
        self.open = {p.upper() for p in open_docs}
        self.unloadable = {p.upper() for p in unloadable}
        self.opened: list[str] = []
        self.focused: list[str] = []

    def IsDocumentOpen(self, p):
        return p.upper() in self.open

    def GetDocumentByPath(self, p):
        return _ServerDoc(self, p) if p.upper() in self.open else None

    # upstream's outjob route (2026.10.25 merge) shows the document after
    # opening it; the focus itself is recorded by _ServerDoc.Focus.
    def ShowDocument(self, doc):
        return None

    def OpenDocument(self, kind, p):
        self.opened.append(p)
        if p.upper() not in self.open and not self.fs.FileExists(p):
            # The hazard: a path that is not there. Whether the real call
            # raises or answers Nil is unrecorded; the raise is modelled,
            # and the reply must not depend on which it is.
            self.rec.modal("OpenDocument " + p)
        if p.upper() in self.unloadable:
            return None
        self.open.add(p.upper())
        return _ServerDoc(self, p)


def _project_world(rec, fs, client, workspace=None):
    ns = _common_ns(rec)
    ns.update({
        "DirectoryExists": fs.DirectoryExists,
        "FileExists": fs.FileExists,
        "ForceDirectories": fs.ForceDirectories,
        "AssignFile": fs.AssignFile,
        "Rewrite": fs.Rewrite,
        "Write": fs.Write,
        "CloseFile": lambda f: None,
        "CopyFile": fs.CopyFile,
        "Client": client,
        "GetWorkspace": workspace,
        "FocusedProjectPathIs": lambda p: False,
        "CRLF": "\r\n",
    })
    # Every file on this disk reads as loadable: the unloadable-file guard
    # (N1 of the .02b run) is tested in its own module.
    ns.update(file_reader())
    main = load(_ALTIUM / "Main.pas", minimum=40)
    utils = load(_ALTIUM / "Utils.pas", minimum=40)
    # SaveProjectFile / IsScriptProject: upstream's project save
    # (2026.10.25 merge), which Proj_ImportDocument persists through.
    build(main, ["DocumentPresent", "FileLooksLoadable",
                 "UnopenedFileUnloadable", "OpenDocumentIfPresent",
                 "IsScriptProject", "SaveProjectFile"], ns)
    build(utils, ["TargetDirExists"], ns)
    return ns


def _project_handler(ns, name):
    bodies = _bodies(_PROJECT_PAS)
    body = bodies[name]
    if name == "Proj_Create":
        # The one construct outside the evaluator's subset: the #13#10
        # character literal, swapped for a name bound to the same string.
        body = body.replace("#13#10", "CRLF")
        if "ProjStubNameWritable" in body:
            build(bodies, ["ProjStubNameWritable"], ns)
    _, src = translate(body)
    exec(compile(src, f"<pascal {name}>", "exec"), ns)   # noqa: S102
    return ns[name]


# -- Proj_Create ---------------------------------------------------------------

_STUB = ("[Design]\r\nVersion=1.0\r\nHierarchyMode=0\r\nOpenOutputs=1\r\n"
         "ArchiveProject=0\r\n")


def _create(path, dirs=(), files=None):
    rec = _Recorder()
    fs = _FS(rec, dirs=dirs, files=files)
    ns = _project_world(rec, fs, _Client(rec, fs))
    reply = _project_handler(ns, "Proj_Create")(
        json.dumps({"project_path": path}), "rid")
    return reply, rec, fs


@pytest.mark.parametrize("path,dirs", [
    ("C:\\work\\a.PrjPcb", ("C:\\work",)),        # folder already there
    ("C:\\work\\new\\a.PrjPcb", ("C:\\work",)),   # folder made on the way
])
def test_create_writes_the_same_stub_as_before(path, dirs):
    reply, rec, fs = _create(path, dirs)
    assert rec.modals == [] and rec.caught == []
    assert reply[0] == "ok" and reply[1]["project_path"] == path
    assert fs.files[path.upper()] == _STUB
    assert "WorkspaceManager:OpenObject" in rec.processes


def test_create_with_a_bare_name_writes_without_a_modal():
    """ForceDirectories('') raised EInOutError (a modal) before the bare
    name was written to the current folder anyway. The write and its reply
    are unchanged; the modal is gone."""
    reply, rec, fs = _create("a.PrjPcb")
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply[0] == "ok"
    assert fs.files["A.PRJPCB"] == _STUB


@pytest.mark.parametrize("path", [
    "",                                 # ForceDirectories('') then Rewrite('')
    "Q:\\nodrive\\a.PrjPcb",            # folder cannot be made: Rewrite raised
    "C:\\work\\a?b.PrjPcb",             # a name Windows refuses
    "C:\\work\\",                       # no name at all
])
def test_create_refuses_unwritable_paths_with_the_same_reply(path):
    reply, rec, fs = _create(path, dirs=("C:\\work",))
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply == ("err", "CREATE_FAILED",
                     "Could not write project stub: " + path)
    assert rec.processes == [], "nothing may be opened after a failed write"


def test_create_still_counts_a_genuine_rewrite_fault():
    """What is left behind the guard (an existing stub held or read-only)
    still raises; the live run sees it counted."""
    rec = _Recorder()
    fs = _FS(rec, dirs=("C:\\work",))
    ns = _project_world(rec, fs, _Client(rec, fs))

    def held(f):
        rec.modal("EInOutError: I/O error 32")

    ns["Rewrite"] = held
    reply = _project_handler(ns, "Proj_Create")(
        json.dumps({"project_path": "C:\\work\\a.PrjPcb"}), "rid")
    assert reply[0] == "err" and reply[1] == "CREATE_FAILED"
    assert rec.caught == ["Proj_Create:Rewrite"]


# -- Proj_RunOutJob ------------------------------------------------------------

class _Ini:
    def __init__(self, data):
        self.data = data

    def ReadString(self, section, key, default):
        return self.data.get(section, {}).get(key, default)

    @property
    def Free(self):
        return None


def _outjob(path, fs_files, open_docs=()):
    rec = _Recorder()
    fs = _FS(rec, dirs=("C:\\work",), files=fs_files)
    client = _Client(rec, fs, open_docs=open_docs)
    ns = _project_world(rec, fs, client)
    ini = {"OutputGroup1": {"OutputMedium1": "PDF",
                            "OutputMedium1_Type": "Publish"}}
    ns["TIniFile"] = _Obj(Create=lambda p: _Ini(ini if fs.FileExists(p) else {}))
    reply = _project_handler(ns, "Proj_RunOutJob")(
        json.dumps({"outjob_path": path, "container_name": "PDF"}), "rid")
    return reply, rec, client


def test_run_outjob_on_a_missing_file_says_container_not_found_quietly():
    reply, rec, client = _outjob("C:\\work\\typo.OutJob", {})
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply == ("err", "CONTAINER_NOT_FOUND", "Container not found: PDF")
    assert client.opened == [], "a path that is not there is never opened"


def test_run_outjob_still_opens_and_focuses_a_file_on_disk():
    reply, rec, client = _outjob("C:\\work\\a.OutJob",
                                 {"C:\\work\\a.OutJob": "x"})
    assert rec.modals == []
    assert client.opened == ["C:\\work\\a.OutJob"]
    assert client.focused == ["C:\\work\\a.OutJob"]
    assert reply[0] == "ok" and "WorkspaceManager:Print" in rec.processes


def test_run_outjob_focuses_an_already_open_document():
    reply, rec, client = _outjob("C:\\work\\a.OutJob",
                                 {"C:\\work\\a.OutJob": "x"},
                                 open_docs=("C:\\work\\a.OutJob",))
    assert rec.modals == [] and client.opened == []
    assert client.focused == ["C:\\work\\a.OutJob"]
    assert reply[0] == "ok"


# -- Proj_LoadProjectSheets ----------------------------------------------------

def test_load_sheets_counts_a_missing_member_as_failed_without_a_modal():
    """A .PrjPcb member that was moved or deleted was 'checked' by trying to
    open it. Counts are unchanged; the open of a missing file is gone."""
    rec = _Recorder()
    sheets = ["C:\\p\\open.SchDoc", "C:\\p\\disk.SchDoc",
              "C:\\p\\gone.SchDoc", "C:\\p\\broken.SchDoc"]
    fs = _FS(rec, dirs=("C:\\p",), files={sheets[1]: "", sheets[3]: ""})
    client = _Client(rec, fs, open_docs=(sheets[0],), unloadable=(sheets[3],))
    docs = [_Obj(DM_DocumentKind="SCH", DM_FullPath=s) for s in sheets]
    docs.append(_Obj(DM_DocumentKind="PCB", DM_FullPath="C:\\p\\b.PcbDoc"))
    project = _Obj(DM_LogicalDocumentCount=len(docs),
                   DM_LogicalDocuments=lambda i: docs[i])
    ns = _project_world(rec, fs, client,
                        workspace=_Obj(DM_FocusedProject=project))
    reply = _project_handler(ns, "Proj_LoadProjectSheets")("{}", "rid")
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply == ("ok", {"total_sheets": 4, "loaded": 1,
                            "already_loaded": 1, "failed": 2})
    assert "C:\\p\\gone.SchDoc" not in client.opened


# -- Proj_ImportDocument -------------------------------------------------------

def _import(project_path, dirs, copy_fault=False):
    rec = _Recorder()
    src = "C:\\lib\\x.SchDoc"
    fs = _FS(rec, dirs=("C:\\lib",) + tuple(dirs), files={src: "DATA"})
    fs.copy_fault = copy_fault
    added: list[str] = []
    project = _Obj(DM_ProjectFullPath=project_path,
                   DM_AddSourceDocument=added.append)
    ns = _project_world(rec, fs, _Client(rec, fs),
                        workspace=_Obj(DM_FocusedProject=project))
    reply = _project_handler(ns, "Proj_ImportDocument")(
        json.dumps({"source_path": src}), "rid")
    return reply, rec, fs, added


def test_import_into_a_missing_project_folder_is_copy_failed_quietly():
    reply, rec, fs, added = _import("C:\\gone\\p.PrjPcb", ())
    assert rec.modals == [], f"a modal fired: {rec.modals}"
    assert reply == ("err", "COPY_FAILED",
                     "Failed to copy file to project directory")
    assert added == []


def test_import_into_an_existing_folder_still_copies_and_adds():
    reply, rec, fs, added = _import("C:\\proj\\p.PrjPcb", ("C:\\proj",))
    assert rec.modals == [] and rec.caught == []
    assert fs.files["C:\\PROJ\\X.SCHDOC"] == "DATA"
    assert added == ["C:\\proj\\x.SchDoc"]
    assert reply[0] == "ok" and reply[1]["dest_path"] == "C:\\proj\\x.SchDoc"


def test_import_for_a_project_with_no_folder_behaves_as_before():
    """A bare project path has no folder to check: the copy goes ahead into
    the current folder exactly as it did."""
    reply, rec, fs, added = _import("p.PrjPcb", ())
    assert rec.modals == []
    assert added == ["x.SchDoc"] and reply[0] == "ok"


def test_import_copy_fault_is_counted_for_the_live_probe():
    """CopyFile has no proven non-raising check (nothing else in the bridge
    uses it), so the Try stays and the catch is counted."""
    reply, rec, fs, added = _import("C:\\proj\\p.PrjPcb", ("C:\\proj",),
                                    copy_fault=True)
    assert reply == ("err", "COPY_FAILED",
                     "Failed to copy file to project directory")
    assert rec.caught == ["Proj_ImportDocument:CopyFile"]
    assert added == []
