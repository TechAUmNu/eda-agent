# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""B1 and O1 of the 2026-10-11 live run.

B1  pcb_place_components with an EXISTING .PcbLib as board_path reached
    Client.OpenDocument('PCB', <the library>). Altium answered with its OWN
    modal 'Warning - File type not recognised.' inside the call: not an
    exception, so no Except and no counter saw it, and the bridge blocked
    until the owner pressed OK. A .PcbLib board_path is now refused
    BOARD_PATH_IS_LIBRARY before anything opens, in every handler.
O1  Proj_RunOutJob handed a caller-supplied outjob_path of any file type to
    OpenDocument('OUTPUTJOB', ...), the same kind-mismatch box.

The fixtures fail the test outright where Altium shows its own box, rather
than raising something a translated Except could swallow. Each test fails
on ef2d79b.
"""
from __future__ import annotations

import json

import pytest

from tests.live_2026_10_11 import _MAIN, _caught
from tests.pascal_eval import build
from tests.pascal_source import load, strip_comments
from tests.test_bridge_record_names_one_file import _ALTIUM


# ---------------------------------------------------------------------------
# B1. A .PcbLib board_path never reaches OpenDocument('PCB', ...)
# ---------------------------------------------------------------------------

class _ModalClient:
    """OpenDocument('PCB', <a .PcbLib>) is Altium's own message box, not an
    exception: the fixture fails the test outright rather than raising
    something a translated Except could swallow."""

    def __init__(self, on_disk):
        self.on_disk = {p.upper() for p in on_disk}
        self.opened = []

    def GetDocumentByPath(self, _p):
        return None

    def OpenDocument(self, kind, path):
        self.opened.append((kind, path))
        if kind == "PCB" and path.upper().endswith(".PCBLIB"):
            raise AssertionError("Altium modal 'Warning - File type not recognised.'")
        return object()

    def ShowDocument(self, _d):
        pass


def _resolve_ns(on_disk):
    client = _ModalClient(on_disk)
    ns = {"Client": client, "FileExists": lambda p: p.upper() in client.on_disk,
          "GetPCBBoardAnywhere": lambda _d: "FALLBACK_BOARD",
          "PCBServer": type("S", (), {"GetCurrentPCBBoard": "NAMED_BOARD"})()}
    _caught(ns)
    build(_MAIN, [n for n in ("LooksAbsolutePath", "DocumentPresent",
                              "BoardPathLibraryRefusal", "ResolvePCBBoard")
                  if n in _MAIN], ns)
    return ns, client


@pytest.mark.parametrize("lib", [r"C:\scratch\s2_p1b.PcbLib",
                                 r"C:\scratch\UPPER.PCBLIB  "])
def test_an_existing_pcblib_board_path_is_never_opened_as_a_board(lib):
    ns, client = _resolve_ns([lib.strip()])
    board = ns["ResolvePCBBoard"](lib)
    assert client.opened == []
    assert board is None, "a library path must not fall back to another board"


def test_a_board_path_still_opens_a_board():
    ns, client = _resolve_ns([r"C:\scratch\b.PcbDoc"])
    assert ns["ResolvePCBBoard"](r"C:\scratch\b.PcbDoc") == "NAMED_BOARD"
    assert client.opened == [("PCB", r"C:\scratch\b.PcbDoc")]
    assert ns["ResolvePCBBoard"](r"C:\nope.PcbDoc") == "FALLBACK_BOARD"


def test_the_refusal_names_the_library_and_the_lib_tools():
    ns, _ = _resolve_ns([])
    msg = ns["BoardPathLibraryRefusal"](r"C:\scratch\s2_p1b.PcbLib")
    assert r"C:\scratch\s2_p1b.PcbLib" in msg and "lib_" in msg
    assert ns["BoardPathLibraryRefusal"](r"C:\scratch\b.PcbDoc") == ""
    assert ns["BoardPathLibraryRefusal"]("") == ""


@pytest.mark.parametrize("handler", ["PCB_PlaceComponent", "PCB_PlaceComponents",
                                     "PCB_FocusBoard"])
def test_every_board_path_handler_refuses_a_library_before_resolving(handler):
    body = strip_comments(load(_ALTIUM / "PCB.pas", minimum=40)[handler])
    refuse = body.find("If BoardPathLibraryRefusal(BoardPathStr) <> '' Then")
    resolve = body.find("ResolvePCBBoard(BoardPathStr)")
    assert refuse != -1, f"{handler} never refuses a .PcbLib board_path"
    assert refuse < resolve
    assert "'BOARD_PATH_IS_LIBRARY'" in body[refuse:resolve]


# ---------------------------------------------------------------------------
# O1. outjob_path must be an .OutJob before it is opened
# ---------------------------------------------------------------------------

def _outjob_ns():
    opened = []

    class C:
        def IsDocumentOpen(self, _p):
            return False

        def GetDocumentByPath(self, _p):
            return None

    def open_if_present(kind, path):
        opened.append((kind, path))
        if not path.upper().endswith(".OUTJOB"):
            raise AssertionError("Altium modal: wrong file type for OUTPUTJOB")
        return None

    ns = {"Client": C(), "OpenDocumentIfPresent": open_if_present,
          "ExtractJsonValue": lambda p, k: json.loads(p).get(k, ""),
          "BuildErrorResponse": lambda rid, code, msg: json.dumps(
              {"success": False, "code": code, "message": msg}),
          "GetWorkspace": None}
    body = load(_ALTIUM / "Project.pas", minimum=40)["Proj_RunOutJob"]
    # Only the part before the INI read runs here: everything from the
    # INI parse on needs Altium's TIniFile and is not what is under test.
    head = body.split("IniFile := TIniFile.Create(OutJobPath);", 1)[0]
    build({"Proj_RunOutJob": head + "End;\n"}, ["Proj_RunOutJob"], ns)
    return ns, opened


def test_a_non_outjob_path_is_refused_before_it_is_opened():
    ns, opened = _outjob_ns()
    reply = json.loads(ns["Proj_RunOutJob"](json.dumps(
        {"outjob_path": r"C:\scratch\s2_p1b.PcbLib", "container_name": "Gerber"}), "r"))
    assert opened == []
    assert reply["code"] == "NO_OUTJOB"


def test_an_outjob_path_is_still_opened():
    ns, opened = _outjob_ns()
    ns["Proj_RunOutJob"](json.dumps(
        {"outjob_path": r"C:\scratch\a.OutJob", "container_name": "Gerber"}), "r")
    assert opened == [("OUTPUTJOB", r"C:\scratch\a.OutJob")]
