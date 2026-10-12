# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Panel replies say what this layer can see (finding D-TOOL-2).

FIELD (2026-10-12 19:34, AD 26.10.1.5, F2_open.png): with SCH Library
open and DOCKED in the left pane (its tab in the strip at the bottom of
the pane, beside Projects, Navigator and SCHLIB Filter),
``app_read_panel "SCH Library"`` answered ``no panel named 'SCH Library'``
and offered Comments, Manufacturer Part Search and Properties - the
right-edge tabs.

THE CAUSE, read from ui/panels.py: a panel is found only through its
TPopupPanelButton, the EDGE TAB a collapsed panel shows. A panel docked
open in a pane group has no such button, and the class that names a
docked pane's tabs has not been measured, so it is not guessed. What is
fixed offline:

* The miss no longer reads as "closed": ``read_panel`` (and a failed
  ``open_panel``) carry ``enumerates: edge_tabs_only`` and a note naming
  the docked case; ``app_list_panels`` says the same.
* ``app_list_panels`` read a ``holder_class`` key ``list_open`` never set,
  so it raised KeyError whenever any panel was enumerated. Found while
  reading the cause.

Both fail on 7af6747.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

from eda_agent.tools import uiauto
from eda_agent.ui import panels


def _tab(text, hwnd, x=1900):
    return SimpleNamespace(class_name="TPopupPanelButton", text=text,
                           hwnd=hwnd, rect=(x, 100 + hwnd, 20, 80))


_EDGE = [_tab("Properties", 1), _tab("Comments", 2),
         _tab("Manufacturer Part Search", 3)]


def _patch(monkeypatch):
    monkeypatch.setattr(panels, "_frame_controls", lambda pid: list(_EDGE))
    monkeypatch.setattr(panels, "available", lambda: True)
    monkeypatch.setattr(uiauto, "_altium_pid", lambda: (4242, None))


def _tools():
    captured = {}

    class _Capture:
        def tool(self, *a, **k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    uiauto.register_uiauto_tools(_Capture())
    return captured


def test_list_panels_answers_when_panels_are_enumerated(monkeypatch):
    _patch(monkeypatch)
    out = asyncio.run(_tools()["app_list_panels"]())
    assert out["ok"] is True and out["count"] == 3
    assert {p["name"] for p in out["panels"]} == {
        "Properties", "Comments", "Manufacturer Part Search"}
    assert all(p["holder_class"] == "TPopupPanelButton"
               for p in out["panels"])
    assert out["enumerates"] == "edge_tabs_only"
    assert "docked" in out["note"]


def test_a_docked_panel_miss_does_not_read_as_closed(monkeypatch):
    """The field call: SCH Library docked open, not an edge tab."""
    _patch(monkeypatch)
    out = panels.read_panel(4242, "SCH Library")
    assert out["ok"] is False
    assert out["enumerates"] == "edge_tabs_only"
    assert "docked open" in out["reason"]
    assert "SCH Library" in out["note"] and "not mean the panel is closed" \
        in out["note"]
    assert out["offered"] == ["Comments", "Manufacturer Part Search",
                              "Properties"]


def test_an_edge_tab_panel_is_still_found(monkeypatch):
    _patch(monkeypatch)
    panel = panels.find(4242, "properties")
    assert panel is not None and panel["name"] == "Properties"
    assert panel["holder_class"] == "TPopupPanelButton"
