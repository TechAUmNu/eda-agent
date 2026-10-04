# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""The 3D body tools: rotation on the model, everything read back.

lib_link_3d_model, lib_get_footprint_bodies and pcb_place_3d_body share
the Body3D_ helpers in Library.pas. What these guards pin is the part a
review cannot see failing, because the Pascal cannot run here:

* The IPCB_Model members (SetState, GetState, ModelType, FileName,
  Rotation) are declared by the AD26 script binding and exercised by no
  earlier code. An undeclared identifier halts the polling loop where no
  Try reaches, so each is confined to one small helper, and every helper
  runs only on an explicit request: a rotation, or model_state=true on
  the body list (off by default). A plain link and a default list then
  run only members that have run live before.
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
    "Body3D_ApplyRotation": ("SetState",),
    "Body3D_ModelStateJson": ("GetState",),
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
        "IPCB_Model.SetState/GetState are unproven on a live session and "
        "must stay inside their one-purpose helpers, which run only when "
        "a rotation was asked for; a fault in one stops the polling loop, "
        "so no default path may reach them: " + "; ".join(offenders))


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
    for helper in ("Body3D_ModelTypeId(", "Body3D_ModelInfoJson(",
                   "Body3D_ModelStateJson("):
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
    """Mutation: move the Body3D_ApplyRotation call below the AddPCBObject."""
    body = handlers[name]
    rot = body.find("Body3D_ApplyRotation(")
    derive = body.find("SetState_FromModel")
    add = body.find("AddPCBObject(")
    assert rot > 0 and derive > 0 and add > 0, (
        f"{name}: the rotation, the derive or the add is gone")
    assert rot < derive < add, (
        f"{name}: the rotation must be written on the model before "
        f"SetState_FromModel and before the body is added, so the body's "
        f"outline follows the turned model")


@pytest.mark.parametrize("name", ["Lib_Link3DModel", "PCB_Place3DBody"])
def test_the_unproven_calls_run_only_when_a_rotation_was_asked_for(
        handlers, name):
    """Mutation: drop the `If WantRotation Then` in front of either call.

    That guard is the whole safety argument: without it every link, with
    or without a rotation, would depend on an identifier no live session
    has confirmed.
    """
    body = handlers[name]
    assert re.search(r"If\s+WantRotation\s+Then\s+DidRotation\s*:=\s*"
                     r"Body3D_ApplyRotation\(", body), (
        f"{name}: Body3D_ApplyRotation is reached without a requested "
        f"rotation")
    assert re.search(r"If\s+WantRotation\s+Then\s+Begin\s+StateJson\s*:=\s*"
                     r"Body3D_ModelStateJson\(", body), (
        f"{name}: the GetState read-back runs without a requested rotation")
    assert body.count("Body3D_ApplyRotation(") == 1
    assert body.count("Body3D_ModelStateJson(") == 1


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
