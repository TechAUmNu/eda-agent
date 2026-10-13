# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A member read off a BASE-typed reference must be one somebody reviewed.

THE SHAPE THAT KILLED THE LOOP (live 2026-10-12 02:51, probe P21, bridge
2026.10.11.03). ``Gen_PlaceCrossSheetConnector`` held the factory's object
in a local declared ``ISch_GraphicalObject`` and wrote ``Conn.Side``: the
cross-sheet connector's class publishes no ``Side``, the script engine
halted on the line ("Undeclared identifier: Side"), no ``Except`` ran,
nothing was counted, the polling loop died and the PreProcess opened two
lines earlier stayed open.

WHAT DECIDES IT. The engine resolves a member against the object it
actually HOLDS at runtime; the type a local is declared with is decoration.
P21 itself proves it: ``Conn.Text`` - not a base member either - ran on
the line before ``Side``, through the same local. So re-declaring a local
at a derived interface fixes nothing, and the guard that works is an
ObjectId test (or a member proven on that exact runtime type) BEFORE the
access. Base members are not guaranteed either (a schematic polygon lacks
``Location``), but those are the members every type here was built to
carry, so the scan below concentrates on the ones that are not.

THE GUARD. Every ``<name>.<member>`` where ``<name>`` is declared at one of
the base interfaces and ``<member>`` is NOT in that interface's known
member list (``BASE_MEMBERS``) must be a REVIEWED site in ``REVIEWED`` -
keyed (unit, routine, name, member) and carrying why it is safe
(ObjectId-gated, allowlist-gated, or proven live on that runtime type) or
which FATAL-IF-WRONG release probe owns it. A NEW instance fails the suite
until somebody either gates it or records why it is safe, and a reviewed
site that disappears fails too, so the list cannot rot.

On 75e3286 this fails: ``Generic.pas Gen_PlaceCrossSheetConnector Conn
.Side`` is not, and must never be, a reviewed site.

WHAT THIS GUARD DOES NOT COVER (review of 2026.10.12.01). It scans
references DECLARED at a base interface, and nothing else:

* a DERIVED-typed local holding the wrong runtime object (26.9.1.9:
  ``Body.Rotation`` on an ``IPCB_ComponentBody`` local), or an absent
  member written through any derived-typed local (``ISch_Port``,
  ``ISch_HarnessConnector``, a typed copy ``Comp := Obj``) passes it;
* a BASE member on an unusual type passes it too: ``BASE_MEMBERS``
  whitelists, e.g., ``IPCB_Primitive`` layer / net / incomponent /
  iskeepout, which the sweep marks UNKNOWN on rule, connection and
  dimension objects (#10), and ``ISch_GraphicalObject`` location, which
  was measured to halt on a schematic polygon.

Those are covered only by the FATAL-IF-WRONG release probes (P15, P28,
P41-P49) and, for the one shape that already killed a session, by the
targeted ``test_the_cross_sheet_handler_writes_only_location_and_text``.
A green run here is NOT evidence that no member is absent at runtime.

``REVIEWED_COUNTS`` pins how many accesses each reviewed key makes (1
unless listed), so a second, ungated ``Obj.Name`` added inside an
already-reviewed routine fails as a new site would.
"""
from __future__ import annotations

import bisect
import re
from pathlib import Path

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"

#: Members each base interface is KNOWN to carry (lower case): what the
#: SDK declares on it and the bridge already calls on every object type it
#: meets. Grow it only with a member that is genuinely on the base.
BASE_MEMBERS: dict[str, set[str]] = {
    "ISch_GraphicalObject": set(
        "objectid location color areacolor selection ownerpartid "
        "ownerpartdisplaymode boundingrectangle graphicallyinvalidate "
        "getstate_location movetoxy movebyxy rotateby90 mirror "
        "i_objectaddress container ownerdocument schiterator_create "
        "schiterator_destroy addschobject removeschobject enabledraw dimmed "
        "disabled compilationmasked graphicallylocked setstate_location "
        "uniqueid".split()),
    "ISch_BasicContainer": set(
        "objectid container ownerdocument schiterator_create "
        "schiterator_destroy addschobject removeschobject "
        "i_objectaddress".split()),
    "ISch_Document": set(
        "objectid documentname graphicallyinvalidate "
        "registerschobjectincontainer removeschobject addschobject "
        "schiterator_create schiterator_destroy setstate_unit sheetsizex "
        "sheetsizey sheetstyle snapgridsize titleblockon unitsystem "
        "visiblegridsize workspaceorientation customx customy".split()),
    "IPCB_Primitive": set(
        "objectid layer net component selected moveable innet incomponent "
        "inpolygon polygon indimension iskeepout descriptor detail "
        "boundingrectangle i_objectaddress graphicallyinvalidate "
        "beginmodify endmodify movebyxy rotatearoundxy replicate".split()),
    "IPCB_Rule": set(
        "comment descriptor drcenabled enabled name priority rulekind "
        "scope1expression scope2expression i_objectaddress".split()),
    "IServerDocument": set("dofilesave filename focus modified "
                           "setmodified".split()),
    "IServerDocumentView": {"ownerdocument"},
}

#: Reviewed non-base accesses: (unit, routine, name, member) -> why it is
#: safe, or the release probe that owns it. Member names in lower case.
_OBJID = "ObjectId-gated before the access"
_ALLOW = "governed by the per-ObjectId allowlists (SchPropAbsentOn family)"
_PROVEN = "same member on the same runtime type ran live"
REVIEWED: dict[tuple[str, str, str, str], str] = {}


def _rev(unit, routine, name, members, why):
    for m in members.split():
        REVIEWED[(unit, routine, name, m.lower())] = why


_rev("Application.pas", "App_CreateDocument", "ServerDoc", "SetFileName",
     _PROVEN + " (AD26, d54996f)")
_rev("Generic.pas", "Gen_AddSheet", "ServerDoc", "SetFileName",
     _PROVEN + " (AD26, d54996f)")
# Upstream's simulation tools (2026.10.13 merge). Every reference is typed
# IServerDocument, the interface that declares these members.
_rev("Generic.pas", "Gen_RunSimulation", "ServerDoc", "SetFileName",
     _PROVEN + " (AD26, d54996f, the App_CreateDocument pattern; release "
     "section 1 table row '46, results saved')")
_rev("Generic.pas", "Gen_SimCfgDocument", "ServerDoc",
     "SupportsReload DoFileLoad UpdateModifiedDate",
     "UNKNOWN, normal use (run nowhere else): FATAL-IF-WRONG probe P53 "
     "(release section 46 item 3)")
_rev("Audit.pas", "Audit_FindBadConnections", "Prim1", "TearDrop",
     "UNKNOWN, normal use: FATAL-IF-WRONG probe P43")
_rev("Audit.pas", "Audit_FindBadConnections", "Prim2", "TearDrop",
     "UNKNOWN, normal use: FATAL-IF-WRONG probe P43")
_rev("Generic.pas", "GetSchVertexProperty", "Obj",
     "GetState_VerticesCount GetState_Vertex",
     _ALLOW + " (SchObjectLacksVertexArray: wires, buses, polylines)")
_rev("Generic.pas", "GetSchProperty", "Obj",
     "Name LibReference SourceLibraryName DesignItemId ComponentDescription "
     "FontId LineWidth Style IOType Alignment Electrical "
     "Justification Width PinLength XSize YSize IsSolid", _ALLOW)
_rev("Generic.pas", "GetSchProperty", "Obj", "Text",
     _ALLOW + " (SchObjectHasText)")
_rev("Generic.pas", "GetSchProperty", "Obj", "Orientation",
     _ALLOW + " (SchObjectHasOrientation; ConnectionX/Y: ePin only)")
_rev("Generic.pas", "GetSchProperty", "Obj", "IsHidden",
     _ALLOW + " (SchObjectHasIsHidden)")
_rev("Generic.pas", "GetSchProperty", "Obj", "IsMirrored",
     _ALLOW + " (SchObjectLacksIsMirrored)")
_rev("Generic.pas", "GetSchProperty", "Obj",
     "GetState_VerticesCount GetState_Vertex",
     _ALLOW + " (SchObjectLacksVertexArray)")
_rev("Generic.pas", "GetSchProperty", "Obj", "Designator",
     _OBJID + " (ePin)")
_rev("Generic.pas", "GetSchProperty", "Obj", "Side DistanceFromTop",
     _OBJID + " (eSheetEntry)")
_rev("Generic.pas", "GetSchProperty", "Obj", "HarnessType",
     _OBJID + " (eHarnessConnector); the write resolved live (P20)")
_rev("Generic.pas", "SetSchProperty", "Obj",
     "Name LibReference SourceLibraryName DesignItemId ComponentDescription "
     "FontId LineWidth Style IOType Alignment Electrical "
     "Justification Width PinLength XSize YSize IsSolid", _ALLOW)
_rev("Generic.pas", "SetSchProperty", "Obj", "Text",
     _ALLOW + " (SchObjectHasText)")
_rev("Generic.pas", "SetSchProperty", "Obj", "Orientation",
     _ALLOW + " (SchObjectHasOrientation)")
_rev("Generic.pas", "SetSchProperty", "Obj", "IsHidden",
     _ALLOW + " (SchObjectHasIsHidden)")
_rev("Generic.pas", "SetSchProperty", "Obj", "IsMirrored",
     _ALLOW + " (SchObjectLacksIsMirrored; components via SetState_)")
_rev("Generic.pas", "Gen_HighlightNet", "Obj", "Name Text",
     _OBJID + " (Name on port/sheet entry, Text via SchObjectHasText)")
_rev("Generic.pas", "Gen_MirrorSchComponent", "Prim",
     "GetState_VerticesCount GetState_Vertex SetState_Vertex Corner "
     "Orientation", _OBJID + " (built from the 2026-09-19 live failures)")
_rev("Generic.pas", "FindPlacedComponentByLibRef", "Obj", "LibReference",
     "iterator filtered to eSchComponent; " + _PROVEN)
_rev("Generic.pas", "Gen_GetDirectives", "ParamSet", "Name Location",
     "UNKNOWN, normal use: FATAL-IF-WRONG probe P44")
_rev("Generic.pas", "Gen_GetDirectives", "Param", "Name Text",
     "eParameter children; " + _PROVEN)
_rev("Generic.pas", "InferNetLabelStyle", "Obj", "FontId",
     "iterator filtered to eNetLabel; " + _PROVEN)
_rev("Generic.pas", "Gen_PlaceImage", "Img", "Corner FileName",
     "UNKNOWN, normal use: FATAL-IF-WRONG probe P47")
_rev("Generic.pas", "Gen_PlaceCrossSheetConnector", "Conn", "Text",
     _PROVEN + " (P21, 2026-10-12, net given, ran before the Side halt)")
_rev("Generic.pas", "Gen_ExplainPin", "Hit",
     "PinLength Orientation GetState_VerticesCount GetState_Vertex Text "
     "Name Designator", _OBJID)
_rev("PCB.pas", "PCB_ReplicateLayout", "Prim", "ReplicateWithChildren",
     "UNKNOWN, normal use, inside PCBServer.PreProcess: FATAL-IF-WRONG "
     "probe P41")
_rev("PCB.pas", "PCB_AddTestpointsForNetClass", "Prim",
     "IsTestpoint_Top IsTestpoint_Bottom IsAssyTestpoint_Top "
     "IsAssyTestpoint_Bottom",
     "UNKNOWN on vias, normal use, inside PreProcess: FATAL-IF-WRONG "
     "probe P45")

#: Accesses per reviewed key when more than one (every other key: 1).
REVIEWED_COUNTS: dict[tuple[str, str, str, str], int] = {
    ("Generic.pas", "Gen_ExplainPin", "Hit", "getstate_vertex"): 2,
    ("Generic.pas", "Gen_ExplainPin", "Hit", "text"): 2,
    ("Generic.pas", "Gen_GetDirectives", "ParamSet", "location"): 2,
    ("Generic.pas", "Gen_MirrorSchComponent", "Prim", "corner"): 3,
    ("Generic.pas", "Gen_MirrorSchComponent", "Prim", "orientation"): 3,
    ("Generic.pas", "GetSchProperty", "Obj", "getstate_vertex"): 4,
    ("Generic.pas", "GetSchProperty", "Obj", "orientation"): 3,
    ("Generic.pas", "GetSchProperty", "Obj", "pinlength"): 3,
    ("Generic.pas", "GetSchVertexProperty", "Obj", "getstate_vertex"): 2,
    ("Generic.pas", "GetSchVertexProperty", "Obj",
     "getstate_verticescount"): 2,
}

_BASE_LOWER = {t.lower(): t for t in BASE_MEMBERS}
_BASE_LOWER["idocument"] = "IDocument"


def _blank(text: str) -> str:
    """Comments and string literals blanked, line structure kept."""
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c == "{":
            j = text.find("}", i)
            j = n - 1 if j < 0 else j
            out.append(re.sub(r"[^\n]", " ", text[i:j + 1]))
            i = j + 1
        elif text.startswith("(*", i):
            j = text.find("*)", i)
            j = n - 2 if j < 0 else j
            out.append(re.sub(r"[^\n]", " ", text[i:j + 2]))
            i = j + 2
        elif text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
        elif c == "'":
            j = i + 1
            while j < n:
                if text[j] == "'":
                    if j + 1 < n and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''" + " " * (j - i - 1))
            i = j + 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


_HDR = re.compile(r"^\s*(function|procedure)\s+(\w+)\s*(\((.*?)\))?\s*"
                  r"(:\s*(\w+))?\s*;", re.I | re.S | re.M)


def scan(altium_dir: Path = _ALTIUM) -> list[tuple[str, str, str, str, str, int]]:
    """Every ``<base-typed name>.<member>`` whose member is not on the base:
    (unit, routine, name, member, declared type, line)."""
    hits = []
    for path in sorted(altium_dir.glob("*.pas")):
        # The build.py bundle concatenates every unit: scanning it would
        # report each source site a second time under Altium_MCP.pas.
        if path.name == "Altium_MCP.pas":
            continue
        raw = path.read_text(encoding="latin-1")
        s = _blank(raw)
        starts = [0] + [m.end() for m in re.finditer("\n", s)]
        heads = list(_HDR.finditer(s))
        for k, h in enumerate(heads):
            end = heads[k + 1].start() if k + 1 < len(heads) else len(s)
            routine, params = h.group(2), h.group(4) or ""
            chunk = s[h.end():end]
            decls: dict[str, tuple[str, str]] = {}
            for grp in params.split(";"):
                if ":" not in grp:
                    continue
                names, typ = grp.rsplit(":", 1)
                typ = typ.strip().split("=")[0].strip()
                for nm in re.sub(r"\b(?:var|const)\b", "", names,
                                 flags=re.I).split(","):
                    if nm.strip():
                        decls[nm.strip().lower()] = (nm.strip(), typ)
            if h.group(6):
                decls["result"] = ("Result", h.group(6))
            bm = re.search(r"\bbegin\b", chunk, re.I)
            if not bm:
                continue
            pre, body = chunk[:bm.start()], chunk[bm.start():]
            for vm in re.finditer(r"\bvar\b(.*?)(?=\bconst\b|\btype\b|$)",
                                  pre, re.I | re.S):
                for d in vm.group(1).split(";"):
                    if ":" not in d:
                        continue
                    names, typ = d.split(":", 1)
                    for nm in names.split(","):
                        nm = nm.strip()
                        if re.fullmatch(r"\w+", nm):
                            decls[nm.lower()] = (nm, typ.strip())
            body_at = h.end() + bm.start()
            for nm, typ in decls.values():
                base = _BASE_LOWER.get(typ.lower())
                if base is None:
                    continue
                for am in re.finditer(r"(?<![\w.])" + re.escape(nm)
                                      + r"\s*\.\s*(\w+)", body, re.I):
                    member = am.group(1)
                    if base == "IDocument":
                        if member.lower().startswith("dm_"):
                            continue
                    elif member.lower() in BASE_MEMBERS[base]:
                        continue
                    line = bisect.bisect_right(starts, body_at + am.start())
                    hits.append((path.name, routine, nm, member, base, line))
    return hits


def _key(hit):
    return (hit[0], hit[1], hit[2], hit[3].lower())


def test_every_non_base_member_on_a_base_typed_reference_is_reviewed():
    hits = scan()
    assert len(hits) > 50, (
        f"only {len(hits)} accesses found: the scanner has stopped seeing "
        f"the code, and a pass here would be vacuous")
    new = sorted({f"{h[0]}:{h[5]} {h[1]}: {h[2]}.{h[3]} ({h[4]})"
                  for h in hits if _key(h) not in REVIEWED})
    assert not new, (
        "member(s) read or written through a BASE-typed reference that "
        "nobody has reviewed (this guard sees base-typed references only: "
        "derived-typed locals and base members on unusual types are "
        "covered by the FATAL-IF-WRONG probes P15/P28/P41-P49, not here). "
        "On an object whose class lacks the member "
        "this is the script engine's 'Undeclared identifier' HALT (no "
        "Except runs, the polling loop dies - P21, 2026-10-12). Gate each "
        "on ObjectId (or use a member proven on that runtime type), or "
        "refuse the argument that reaches it, then record the site in "
        "REVIEWED with why it is safe or the FATAL-IF-WRONG probe that "
        "owns it. Re-declaring the local does NOT help:\n  "
        + "\n  ".join(new))


def test_no_reviewed_site_is_stale():
    present = {_key(h) for h in scan()}
    stale = sorted(f"{k[0]} {k[1]}: {k[2]}.{k[3]}"
                   for k in REVIEWED if k not in present)
    assert not stale, (
        "REVIEWED names accesses the source no longer makes; drop them so "
        "the list stays a list of real sites:\n  " + "\n  ".join(stale))


def test_the_cross_sheet_side_write_is_never_a_reviewed_site():
    """The P21 shape itself: no allowlist entry may ever cover it."""
    assert not any(k[1] == "Gen_PlaceCrossSheetConnector" and k[3] == "side"
                   for k in REVIEWED)


def test_the_scanner_sees_the_p21_shape():
    """A synthetic copy of the halted handler: the scan must flag Side and
    let the reviewed Text through, whatever Conn is declared as at the
    base."""
    import tempfile

    src = ("Function Gen_PlaceCrossSheetConnector(Params : String) : String;\n"
           "Var\n    Conn : ISch_GraphicalObject;\nBegin\n"
           "    Conn := SchServer.SchObjectFactory(eCrossSheetConnector, 0);\n"
           "    Try Conn.Text := 'N'; Except End;\n"
           "    Conn.Side := 0;\nEnd;\n")
    with tempfile.TemporaryDirectory() as d:
        Path(d, "Generic.pas").write_text(src, encoding="latin-1")
        hits = scan(Path(d))
    got = {(h[2], h[3]) for h in hits}
    assert got == {("Conn", "Text"), ("Conn", "Side")}
    unreviewed = [h for h in hits if _key(h) not in REVIEWED]
    assert [(h[2], h[3]) for h in unreviewed] == [("Conn", "Side")]


def test_each_reviewed_key_makes_exactly_its_recorded_number_of_accesses():
    """A reviewed key is a SITE, not a licence for the routine: a new,
    ungated access with the same (unit, routine, name, member) must fail
    as any new site does."""
    from collections import Counter

    got = Counter(_key(h) for h in scan())
    wrong = sorted(f"{k[0]} {k[1]}: {k[2]}.{k[3]} - {got.get(k, 0)} "
                   f"access(es), recorded {REVIEWED_COUNTS.get(k, 1)}"
                   for k in REVIEWED
                   if got.get(k, 0) != REVIEWED_COUNTS.get(k, 1))
    assert not wrong, (
        "a reviewed routine gained or lost an access: review the new site "
        "(gate it, or record why it is safe) and update REVIEWED_COUNTS:\n  "
        + "\n  ".join(wrong))
    assert set(REVIEWED_COUNTS) <= set(REVIEWED)


def test_a_second_access_under_a_reviewed_key_is_caught():
    """The count check sees a duplicated, ungated Conn.Text."""
    import tempfile
    from collections import Counter

    src = ("Function Gen_PlaceCrossSheetConnector(Params : String) : String;\n"
           "Var\n    Conn : ISch_GraphicalObject;\nBegin\n"
           "    Try Conn.Text := 'N'; Except End;\n"
           "    Conn.Text := 'M';\nEnd;\n")
    with tempfile.TemporaryDirectory() as d:
        Path(d, "Generic.pas").write_text(src, encoding="latin-1")
        got = Counter(_key(h) for h in scan(Path(d)))
    k = ("Generic.pas", "Gen_PlaceCrossSheetConnector", "Conn", "text")
    assert got[k] == 2 != REVIEWED_COUNTS.get(k, 1)


def test_the_cross_sheet_handler_writes_only_location_and_text():
    """P21's shape, WHATEVER Conn is declared as: the base-typed scan above
    would miss ``Conn : ISch_CrossSheetConnector; Conn.Side := 0``. No
    Side, Orientation or CrossSheetStyle may be touched on the connector
    (none is proven on that runtime type, and Side halted the engine)."""
    src = (_ALTIUM / "Generic.pas").read_text(encoding="latin-1")
    s = _blank(src)
    m = re.search(r"^Function Gen_PlaceCrossSheetConnector\b.*?^End;", s,
                  re.S | re.M | re.I)
    assert m, "Gen_PlaceCrossSheetConnector not found"
    body = m.group(0)
    assert not re.search(r"\.\s*(Side|Orientation|CrossSheetStyle)\b", body,
                         re.I), (
        "the cross-sheet connector touches Side / Orientation / "
        "CrossSheetStyle again")
    members = {mm.group(1).lower() for mm in
               re.finditer(r"(?<![\w.])Conn\s*\.\s*(\w+)", body, re.I)}
    assert members == {"location", "text"}, members
