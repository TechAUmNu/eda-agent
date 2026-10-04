# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The 3D body tools: rotation on the model, everything read back.

lib_link_3d_model, lib_get_footprint_bodies and pcb_place_3d_body share
the Body3D_ helpers in Library.pas. What these guards pin is the part a
review cannot see failing, because the Pascal cannot run here:

* The IPCB_Model members (SetState, ModelType, FileName, Rotation) are
  declared by the AD26 script binding. An undeclared identifier halts
  the polling loop where no Try reaches, so each is confined to one
  small helper, and every helper runs only on an explicit request: a
  rotation or a Z offset, or model_state=true on the body list (off by
  default). A plain link and a default list then run only members that
  have run live before.
* GetState is NEVER called. Measured live 2026-10-04 it returns its OUT
  parameters as 0 beside a saved ROTX=90 / ROTZ=45, so its read-back
  was a false mismatch; the rotation read-back is reported unavailable
  and the saved file (lib_read_pcblib_file) is the proof.
* align='none' puts the model origin on the FOOTPRINT origin: the move
  adds the footprint origin, because the factory drops the model origin
  at absolute (0, 0) (measured 2026-10-04: 50,000 mil from its pads),
  and a placement check compares the body with the footprint, not with
  itself.
* offset_z is the model's Dz (MODEL.3D.DZ), not StandoffHeight, which
  was measured not to lift a STEP model.
* The rotation is written BEFORE the body derives its outline and before
  it is added. Setting body properties on an unowned body crashed
  ADVPCB.DLL once; this writes the model, and the order is what makes
  the body's box follow the turned model.
* Body.x / Body.y are never read: the AD26 SDK declares no position on
  IPCB_ComponentBody or its ancestors.
* replace=true removes nothing until the new model has loaded, and
  removes with the measured one-match-per-pass shape of
  Lib_DeleteFootprintPrimitives.

Each guard names the mutation that makes it fail. All of them were run.
"""
from __future__ import annotations

import asyncio
import math
import re
from pathlib import Path

import pytest

from tests.pascal_source import load

_PAS = Path(__file__).resolve().parents[1] / "scripts" / "altium"


@pytest.fixture(scope="module")
def library() -> dict[str, str]:
    return load(_PAS / "Library.pas", minimum=100)


@pytest.fixture(scope="module")
def pcb() -> dict[str, str]:
    return load(_PAS / "PCB.pas", minimum=120)


@pytest.fixture(scope="module")
def handlers(library, pcb) -> dict[str, str]:
    for name in ("Lib_Link3DModel", "Lib_GetFootprintBodies"):
        assert name in library, f"{name} is gone from Library.pas"
    assert "PCB_Place3DBody" in pcb, "PCB_Place3DBody is gone from PCB.pas"
    return {
        "Lib_Link3DModel": library["Lib_Link3DModel"],
        "Lib_GetFootprintBodies": library["Lib_GetFootprintBodies"],
        "PCB_Place3DBody": pcb["PCB_Place3DBody"],
    }


#: Helper -> the unproven IPCB_Model members it alone may touch.
_MODEL_HELPERS = {
    "Body3D_ApplyModelState": ("SetState",),
    "Body3D_ModelTypeId": ("ModelType",),
    "Body3D_ModelInfoJson": ("FileName", "Rotation"),
}


def test_every_model_helper_exists(library):
    """The scan below is vacuous if the helpers it exempts are gone."""
    missing = [h for h in _MODEL_HELPERS if h not in library]
    assert not missing, f"helpers missing from Library.pas: {missing}"


def test_set_and_get_state_are_called_only_inside_their_helpers(
        library, pcb):
    """Mutation: write Model.SetState(...) directly in Lib_Link3DModel.

    SetState_Cache / SetState_FromModel are different identifiers and do
    not match: the pattern needs the parenthesis straight after the name.
    """
    pattern = re.compile(r"\.\s*(SetState|GetState)\s*\(")
    offenders = []
    for module, funcs in (("Library.pas", library), ("PCB.pas", pcb)):
        for name, body in funcs.items():
            for hit in pattern.finditer(body):
                member = hit.group(1)
                if member in _MODEL_HELPERS.get(name, ()):
                    continue
                offenders.append(f"{module}:{name} calls .{member}(")
    assert not offenders, (
        "IPCB_Model.SetState must stay inside Body3D_ApplyModelState, which "
        "runs only when a rotation or a Z offset was asked for, and "
        "GetState is called nowhere (it returns zeros, measured "
        "2026-10-04): " + "; ".join(offenders))


def test_get_state_is_called_nowhere(library, pcb):
    """Mutation: bring back Model.GetState(RX, RY, RZ, DzC).

    MEASURED 2026-10-04: it returned 0, 0, 0 beside a saved file holding
    MODEL.3D.ROTX=90 / ROTZ=45, and the link reported
    rotation_readback_matches=false on a rotation that had landed.
    """
    for module, funcs in (("Library.pas", library), ("PCB.pas", pcb)):
        for name, body in funcs.items():
            assert not re.search(r"\.\s*GetState\s*\(", body), (
                f"{module}:{name} calls GetState, whose OUT parameters do "
                f"not come back in DelphiScript")
    readback = library["Body3D_RotationReadbackJson"]
    assert "JsonBool('available', False)" in readback
    assert "lib_read_pcblib_file" in readback
    assert not re.search(r"\bModel\b", readback.split("Begin", 1)[1]), (
        "the unavailable read-back must touch no IPCB_Model member")


def test_the_body_list_reads_models_only_when_asked(handlers):
    """Mutation: parse model_state as `<> 'false'` again (default ON).

    That made the first ordinary lib_get_footprint_bodies call, and every
    body of every footprint in a sweep, run four IPCB_Model members no
    live session has confirmed, where a fault cannot be caught.
    """
    body = handlers["Lib_GetFootprintBodies"]
    assert re.search(r"WantModel\s*:=\s*\(LowerCase\(Trim\(ExtractJsonValue"
                     r"\(Params,\s*'model_state'\)\)\)\s*=\s*'true'\)", body), (
        "model_state must be an explicit opt-in: only 'true' turns it on")
    assert "<> 'false'" not in body
    for helper in ("Body3D_ModelTypeId(", "Body3D_ModelInfoJson("):
        for hit in re.finditer(re.escape(helper), body):
            guard = body.rfind("If WantModel Then", 0, hit.start())
            assert guard >= 0 and hit.start() - guard < 400, (
                f"{helper} is reached without `If WantModel Then` in front")


def test_model_reads_go_through_helpers(handlers):
    """Mutation: read Model.ModelType inline in Lib_GetFootprintBodies."""
    pattern = re.compile(r"\bModel\s*\.\s*(ModelType|FileName|Rotation|"
                         r"SetState|GetState)\b")
    offenders = [f"{name}: Model.{m.group(1)}"
                 for name, body in handlers.items()
                 for m in pattern.finditer(body)]
    assert not offenders, (
        "a handler touches an unproven IPCB_Model member directly instead "
        "of through its Body3D_ helper: " + "; ".join(offenders))


@pytest.mark.parametrize("name", ["Lib_Link3DModel", "PCB_Place3DBody"])
def test_rotation_is_applied_before_the_body_derives_its_outline(
        handlers, name):
    """Mutation: move the Body3D_ApplyModelState call below the AddPCBObject."""
    body = handlers[name]
    rot = body.find("Body3D_ApplyModelState(")
    derive = body.find("SetState_FromModel")
    add = body.find("AddPCBObject(")
    assert rot > 0 and derive > 0 and add > 0, (
        f"{name}: the rotation, the derive or the add is gone")
    assert rot < derive < add, (
        f"{name}: the rotation must be written on the model before "
        f"SetState_FromModel and before the body is added, so the body's "
        f"outline follows the turned model")


def test_the_model_state_is_written_only_when_asked_for(handlers):
    """Mutation: drop the `If WantModelState Then` / `If WantRotation Then`.

    A link with neither a rotation nor a Z offset must run the sequence
    that has always run: factory, load, SetState_FromModel, assign, add.
    """
    link = handlers["Lib_Link3DModel"]
    assert re.search(r"WantModelState\s*:=\s*WantRotation\s+Or\s+"
                     r"\(OffZ\s*<>\s*0\)", link)
    assert re.search(r"If\s+WantModelState\s+Then\s+DidModelState\s*:=\s*"
                     r"Body3D_ApplyModelState\(Model,\s*RotX,\s*RotY,\s*RotZ,"
                     r"\s*MilsToCoordF\(OffZ\)\)", link), (
        "lib_link_3d_model must write offset_z as the model's Dz, and only "
        "when a rotation or a Z offset was asked for")
    board = handlers["PCB_Place3DBody"]
    assert re.search(r"If\s+WantRotation\s+Then\s+DidRotation\s*:=\s*"
                     r"Body3D_ApplyModelState\(Model,\s*RotX,\s*RotY,\s*RotZ,"
                     r"\s*0\)", board)
    for body in (link, board):
        assert body.count("Body3D_ApplyModelState(") == 1
        assert "Body3D_RotationReadbackJson(0)" in body, (
            "the reply must say the rotation read-back is unavailable")


def test_offset_z_no_longer_writes_the_standoff(handlers):
    """Mutation: put back Body.StandoffHeight := MilsToCoordF(OffZ).

    MEASURED 2026-10-04: that stored STANDOFFHEIGHT=3mil beside
    MODEL.3D.DZ=0 and an OVERALLHEIGHT equal to the turned model's own
    top, i.e. the model did not move. Altium's own libraries place a STEP
    with MODEL.3D.DZ.
    """
    assert "StandoffHeight :=" not in handlers["Lib_Link3DModel"]


def test_align_none_puts_the_model_origin_on_the_footprint_origin(handlers):
    """Mutation: compute the 'none' move as just the offsets again.

    The factory drops the model origin at ABSOLUTE (0, 0) of the library
    board; with the footprint at (50000, 50000) mils the old move left the
    model 50,000 mil from its pads (measured 2026-10-04, MODEL.2D.X =
    -49989.5mil in the saved file).
    """
    body = handlers["Lib_Link3DModel"]
    at = body.index("If AlignStr = 'none' Then\n")
    block = body[at:at + 300]
    assert "DX := OrgX + MilsToCoordF(OffX);" in block
    assert "DY := OrgY + MilsToCoordF(OffY);" in block


def test_the_placement_is_checked_against_the_footprint(handlers):
    """Mutation: drop the placement check, or compare the body with itself.

    bbox_expected (pre-move box plus the move) passed a body 50,000 mil
    from its footprint. The check that replaces it measures the gap
    between the body's box and the PADS' box, and fails above
    PlaceFarMils or when the box cannot be read.
    """
    body = handlers["Lib_Link3DModel"]
    check = body.index("RX1 := PX1;")
    gap = body.index("PlaceOk := (Gap <= MilsToCoordF(PlaceFarMils));", check)
    readback = body.index("Body3D_ReadRect(Body, NX1, NY1, NX2, NY2, HaveNewRect)")
    assert readback < check, (
        "the check must measure the box read back AFTER the move")
    assert "RX1 := OrgX;" in body[check:gap], (
        "with no pads the reference is the footprint origin")
    assert "NX2" in body[check:gap] and "RX2" in body[check:gap], (
        "the gap must compare the body's box with the reference box")
    assert "JsonBool('placement_ok', PlaceOk)" in body
    assert "If HaveNewRect Then" in body[check:gap]


def test_place_far_mils_is_a_declared_constant():
    main = (_PAS / "Main.pas").read_text(encoding="utf-8-sig")
    assert re.search(r"^\s*PlaceFarMils\s*=\s*1000;", main, re.M)


def test_no_3d_code_reads_a_position_off_the_body(library, handlers):
    """Mutation: compute the move from CoordToMils(Body.x) again.

    IPCB_ComponentBody declares no x or y in the AD26 SDK, nor do its
    ancestors IPCB_Region and IPCB_Primitive. PCBGeneric.pas still reads
    one for obj_query; that is outside this scope and recorded in the
    design notes.
    """
    scope = dict(handlers)
    scope.update({n: b for n, b in library.items() if n.startswith("Body3D_")})
    assert len(scope) >= 12, f"only {len(scope)} 3D routines found"
    pattern = re.compile(r"\bBody\s*\.\s*(x|y)\b", re.IGNORECASE)
    offenders = [f"{n}: Body.{m.group(1)}" for n, b in scope.items()
                 for m in pattern.finditer(b)]
    assert not offenders, (
        "a 3D routine reads a position off the body, which the SDK does "
        "not declare: " + "; ".join(offenders))


def test_a_linked_body_is_registered_with_footprint_and_library_board(
        handlers):
    """Mutation: delete the PcbLib.Board.AddPCBObject(Body) line.

    Without the board half the body lives in the working copy only and
    the save throws it away, which is how this tool once read as doing
    nothing at all.
    """
    body = handlers["Lib_Link3DModel"]
    assert "Footprint.AddPCBObject(Body)" in body
    assert "PcbLib.Board.AddPCBObject(Body)" in body
    broadcasts = re.findall(
        r"PCBM_BoardRegisteration\s*,\s*Body\.I_ObjectAddress", body)
    assert len(broadcasts) == 2, (
        f"expected the registration broadcast to the footprint and the "
        f"library board, found {len(broadcasts)}")


def test_replace_strips_nothing_until_the_new_model_has_loaded(handlers):
    """Mutation: call Body3D_RemoveAll before ModelFactory_FromFilename."""
    body = handlers["Lib_Link3DModel"]
    load_at = body.find("ModelFactory_FromFilename(")
    nil_check = body.find("Model = Nil", load_at)
    remove_at = body.find("Body3D_RemoveAll(")
    assert 0 < load_at < nil_check < remove_at, (
        "the footprint's bodies must only be removed after the new model "
        "loaded, or an unreadable STEP leaves it with no body at all")


def test_the_remover_keeps_the_measured_one_match_per_pass_shape(library):
    """Mutation: walk on with NextPCBObject, or collect into a list.

    Lib_DeleteFootprintPrimitives measured both: a collected primitive
    faulted the engine, and removing after the iterator was destroyed
    left it dangling and took the scripting system down.
    """
    body = library["Body3D_RemoveAll"]
    assert "NextPCBObject" not in body
    assert "TInterfaceList" not in body
    remove = body.find("RemovePCBObject(")
    destroy = body.find("GroupIterator_Destroy(")
    assert 0 < remove < destroy, (
        "the match must be removed while its iterator is still open")


@pytest.mark.parametrize("name", ["Lib_Link3DModel", "PCB_Place3DBody"])
def test_no_exit_inside_the_edit_transaction(handlers, name):
    """Mutation: Exit from inside the PreProcess bracket on a load failure.

    An open transaction refuses every later PCB save until a restart
    (measured 2026-08-25). The failure is recorded and reported after
    PostProcess instead.
    """
    body = handlers[name]
    start = body.find("PCBServer.PreProcess")
    end = body.find("PCBServer.PostProcess")
    assert 0 < start < end
    assert not re.search(r"\bExit\b", body[start:end]), (
        f"{name} can leave between PreProcess and PostProcess")


def test_the_body_list_is_read_only(library):
    """Mutation: drop get_footprint_bodies from LibActionIsReadOnly.

    The list makes the footprint current to measure in the right frame;
    it must put that back, and the dispatcher must put the focused
    document back.
    """
    assert "'get_footprint_bodies'" in library["LibActionIsReadOnly"]
    body = library["Lib_GetFootprintBodies"]
    for verb in ("AddPCBObject", "RemovePCBObject", "PreProcess",
                 "MoveByXY", "StandoffHeight :="):
        assert verb not in body, f"the read-only list calls {verb}"
    assert "PcbLib.CurrentComponent := SavedCurrent" in body


@pytest.mark.parametrize("name,target", [("Lib_Link3DModel", "Footprint"),
                                         ("Lib_GetFootprintBodies", "Target")])
def test_the_footprint_switch_is_read_back(handlers, name, target):
    """Mutation: put back Try PcbLib.SetState_CurrentComponent(...) Except End.

    The link registers the body with the library board too, and the board
    attaches it to the CURRENT footprint, so a switch that silently failed
    would put one model on two footprints (the hazard ResolveLibFootprint
    closes for the other footprint writes). The list reports the switch as
    frame.made_current.
    """
    body = handlers[name]
    assert "SetState_CurrentComponent(" not in body, (
        f"{name} switches footprints without reading the switch back")
    write = body.index(f"PcbLib.CurrentComponent := {target}")
    assert "PcbLib.CurrentComponent" in body[write + 10:], (
        f"{name} does not read CurrentComponent back after the switch")


# --------------------------------------------------------- Python side

class _CapturingBridge:
    def __init__(self, replies=None, raise_for=None) -> None:
        self.commands: list[tuple[str, dict]] = []
        self.replies = replies or {}
        self.raise_for = raise_for or {}

    async def send_command_async(self, command, params=None, **kwargs):
        self.commands.append((command, dict(params or {})))
        if command in self.raise_for:
            raise self.raise_for[command]
        return self.replies.get(command, {"success": True})


def _tools() -> dict:
    from eda_agent.tools.library import register_library_tools
    from eda_agent.tools.pcb import register_pcb_tools

    captured: dict = {}

    class _Mcp:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    register_library_tools(_Mcp())
    register_pcb_tools(_Mcp())
    return captured


@pytest.fixture
def fake(monkeypatch, tmp_path):
    from tests.conftest import install_bridge_fake

    bridge = _CapturingBridge()
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    return bridge


def test_link_sends_rotation_replace_and_align(fake):
    tools = _tools()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="SOIC8", model_path=r"C:\m\soic8.step",
        rotation_x=90, offset_z=2.5, replace=True, align="center"))
    command, params = fake.commands[-1]
    assert command == "library.link_3d_model"
    assert params["rotation_x"] == 90.0 and params["rotation_z"] == 0.0
    assert params["offset_z"] == 2.5
    assert params["replace"] == "true"
    assert params["align"] == "center"
    assert "library_path" not in params


def test_link_names_the_library_only_when_given(fake):
    tools = _tools()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="X", model_path="m.step",
        library_path=r"C:\lib\carrier.PcbLib"))
    _command, params = fake.commands[-1]
    assert params["library_path"] == r"C:\lib\carrier.PcbLib"
    assert params["replace"] == "false" and params["align"] == "none"


@pytest.mark.parametrize("kwargs", [
    {"align": "middle"},
    {"rotation_x": math.nan},
    {"offset_y": math.inf},
])
def test_link_refuses_before_reaching_the_bridge(fake, kwargs):
    tools = _tools()
    result = asyncio.run(tools["lib_link_3d_model"](
        component_name="X", model_path="m.step", **kwargs))
    assert result.get("ok") is False, result
    assert fake.commands == []


def test_body_list_sends_model_state_as_a_word(fake):
    tools = _tools()
    asyncio.run(tools["lib_get_footprint_bodies"](
        footprint_name="SOIC8", model_state=False))
    command, params = fake.commands[-1]
    assert command == "library.get_footprint_bodies"
    assert params["model_state"] == "false"
    assert params["footprint_name"] == "SOIC8"


def test_body_list_reads_no_model_by_default(fake):
    """Mutation: default model_state back to True on the Python side."""
    tools = _tools()
    asyncio.run(tools["lib_get_footprint_bodies"](footprint_name="SOIC8"))
    _command, params = fake.commands[-1]
    assert params["model_state"] == "false"
    asyncio.run(tools["lib_get_footprint_bodies"](
        footprint_name="SOIC8", model_state=True))
    _command, params = fake.commands[-1]
    assert params["model_state"] == "true"


def test_board_body_sends_rotation_and_refuses_a_bad_align(fake):
    tools = _tools()
    asyncio.run(tools["pcb_place_3d_body"](
        model_path="m.step", x=1000.5, rotation_z=45))
    command, params = fake.commands[-1]
    assert command == "pcb.place_3d_body"
    assert params["x"] == "1000.5"
    assert params["rotation_z"] == "45.0"
    assert params["align"] == "center"

    before = len(fake.commands)
    result = asyncio.run(tools["pcb_place_3d_body"](
        model_path="m.step", align="origin"))
    assert result.get("ok") is False
    assert len(fake.commands) == before


def test_auto_link_reports_a_refused_link_as_failed(monkeypatch, tmp_path):
    """Before, every attempted link was listed as linked whatever came back."""
    from eda_agent.bridge.exceptions import AltiumCommandError
    from tests.conftest import install_bridge_fake

    models = tmp_path / "models"
    models.mkdir()
    (models / "SOIC8.step").write_text("x")
    bridge = _CapturingBridge(
        replies={"library.get_footprints":
                 {"footprints": [{"name": "SOIC8"}, {"name": "QFN16"}]}},
        raise_for={"library.link_3d_model":
                   AltiumCommandError("MODEL_LOAD_FAILED", code="MODEL_LOAD_FAILED")})
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    tools = _tools()
    result = asyncio.run(tools["lib_auto_link_3d_models"](
        model_dir=str(models), replace=True))
    assert result["linked"] == []
    assert [f["footprint"] for f in result["failed"]] == ["SOIC8"]
    assert result["unmatched_footprints"] == ["QFN16"]
    assert result["success"] is False
    link = [p for c, p in bridge.commands if c == "library.link_3d_model"]
    assert link and link[0]["replace"] == "true"


def test_replace_keeps_the_placeholder_layer(handlers):
    """Mutation: read the layer after Body3D_RemoveAll, when nothing is left.

    The replaced body's layer is what a swapped-in STEP inherits, so it
    has to be read while that body still exists, and written only after
    the new body has an owner (a setter on an unowned body crashed
    ADVPCB.DLL).
    """
    body = handlers["Lib_Link3DModel"]
    first = body.find("Body3D_FirstBodyLayer(")
    remove = body.find("Body3D_RemoveAll(")
    assert 0 < first < remove, (
        "the placeholder's layer must be read before it is removed")
    add = body.find("PcbLib.Board.AddPCBObject(Body)")
    write = body.find("Body.Layer := WantLayer")
    assert 0 < add < write, "the layer must be written after the add"


def test_link_sends_a_layer_only_when_given(fake):
    tools = _tools()
    asyncio.run(tools["lib_link_3d_model"](
        component_name="X", model_path="m.step"))
    assert "layer" not in fake.commands[-1][1]
    asyncio.run(tools["lib_link_3d_model"](
        component_name="X", model_path="m.step", layer="Mechanical 13"))
    assert fake.commands[-1][1]["layer"] == "Mechanical 13"


def test_auto_link_names_the_library_on_every_link(monkeypatch, tmp_path):
    """get_footprints is a READ, and the dispatcher restores the previous
    focus after a read, so a link without library_path resolved against
    whatever document had focus: NO_PCBLIB on a sheet, or bodies (and, with
    replace, deletions) in a different PcbLib with a same-named footprint.
    """
    from tests.conftest import install_bridge_fake

    models = tmp_path / "models"
    models.mkdir()
    for name in ("SOIC8", "QFN16"):
        (models / f"{name}.step").write_text("x")
    bridge = _CapturingBridge(replies={"library.get_footprints": {
        "footprints": [{"name": "SOIC8"}, {"name": "QFN16"}]}})
    install_bridge_fake(monkeypatch, tmp_path, bridge)
    lib = r"C:\libs\carrier.PcbLib"
    asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(models), library_path=lib, replace=True,
        reload_first=False))
    links = [p for c, p in bridge.commands if c == "library.link_3d_model"]
    assert len(links) == 2
    assert all(p.get("library_path") == lib for p in links), links

    bridge.commands.clear()
    asyncio.run(_tools()["lib_auto_link_3d_models"](model_dir=str(models)))
    links = [p for c, p in bridge.commands if c == "library.link_3d_model"]
    assert links and all("library_path" not in p for p in links), (
        "without a library_path the focused library is used, as documented")


def test_auto_link_refuses_a_missing_folder_in_the_house_envelope(fake,
                                                                  tmp_path):
    result = asyncio.run(_tools()["lib_auto_link_3d_models"](
        model_dir=str(tmp_path / "nope")))
    assert result["ok"] is False and "not a directory" in result["reason"]
    assert "error" not in result and "success" not in result
    assert fake.commands == []
