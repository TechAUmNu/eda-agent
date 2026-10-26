# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Two openers that came in with the 2026-10-25 upstream merge never hand an
unloadable file to Client.OpenDocument.

Live 2026-10-11 (P6b, bridge 2026.10.11.02, AD 26.10.1.5): a 0-byte file
handed to OpenDocument put up Altium's own 'File type not recognised.' box,
uncounted and before any Except, blocked the loop ~13 s and then returned a
blank document. This fork guards its opens with UnopenedFileUnloadable
(Main.pas; OpenDocumentIfPresent asks it). Upstream's SchDocByPathLoading
(Utils.pas: sheet tools given a closed member's path) and the member-opening
pass of FocusProjectForClose (Project.pas: proj_close) checked FileExists
only. Both now skip such a file; a healthy closed file still opens.
"""
from __future__ import annotations

from types import SimpleNamespace

from tests.pascal_eval import build
from tests.pascal_source import load
from tests.test_live_02b_findings import (
    SHEET_ZERO, _ALTIUM, _Client, _n1_ns, _PROJECT)

_UTILS = load(_ALTIUM / "Utils.pas", minimum=20)
GOOD = r"C:\scratch\s2\good.SchDoc"


class _SchServer:
    """Answers a sheet only once the client has it open."""

    def __init__(self, client):
        self.client = client

    def GetSchDocumentByPath(self, p):
        if str(p).upper() in self.client.open_docs:
            return SimpleNamespace(DocumentName=p)
        return None


def _sheet_ns():
    client = _Client(on_disk=[SHEET_ZERO, GOOD], unloadable=[SHEET_ZERO])
    ns = _n1_ns(client, {SHEET_ZERO: ""})
    ns["SchServer"] = _SchServer(client)
    build(_UTILS, ["SchDocByPathLoading"], ns)
    return client, ns


def test_a_sheet_path_to_an_unloadable_file_is_not_opened():
    client, ns = _sheet_ns()
    assert ns["SchDocByPathLoading"](SHEET_ZERO) is None
    assert client.opened == [], "an empty .SchDoc was handed to OpenDocument"


def test_a_healthy_closed_sheet_still_loads():
    client, ns = _sheet_ns()
    assert ns["SchDocByPathLoading"](GOOD) is not None
    assert client.opened == [("SCH", GOOD)]


class _Doc:
    def __init__(self, path, kind="SCH"):
        self.DM_FullPath = path
        self.DM_DocumentKind = kind


class _Project:
    DM_ProjectFullPath = r"C:\scratch\s2\s2.PrjPcb"

    def __init__(self, docs):
        self._docs = docs

    @property
    def DM_LogicalDocumentCount(self):
        return len(self._docs)

    def DM_LogicalDocuments(self, j):
        return self._docs[j]


def _focus_ns(members):
    client = _Client(on_disk=[SHEET_ZERO, GOOD], unloadable=[SHEET_ZERO])
    ns = _n1_ns(client, {SHEET_ZERO: ""})
    # Focus takes once a member of this project is shown.
    ns["FocusedProjectPathIs"] = lambda target: bool(client.shown)
    ns["DocFullPath"] = lambda doc: doc.DM_FullPath
    build(_PROJECT, ["FocusProjectForClose"], ns)
    return client, ns, _Project([_Doc(p) for p in members])


def test_closing_never_opens_an_unloadable_member_to_focus_its_project():
    client, ns, project = _focus_ns([SHEET_ZERO])
    assert ns["FocusProjectForClose"](project) is False
    assert client.opened == [], "an empty member was handed to OpenDocument"


def test_closing_still_opens_a_healthy_member_after_an_unloadable_one():
    client, ns, project = _focus_ns([SHEET_ZERO, GOOD])
    assert ns["FocusProjectForClose"](project) is True
    assert client.opened == [("SCH", GOOD)]
