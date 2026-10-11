# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""L1 of the 2026-10-11 live run: pcb_set_layer_visibility Mechanical21 echoed
"layer":"Unknown" because GetLayerString named Mechanical1-16 only, though
ResolveLayerId's contract says a handler echoes the RESOLVED layer. Each
test fails on ef2d79b.
"""
from __future__ import annotations

import re

import pytest

from tests.live_2026_10_11 import _UTILS, _caught
from tests.pascal_eval import PascalError, build


# ---------------------------------------------------------------------------
# L1. Mechanical layers above 16 are named
# ---------------------------------------------------------------------------

_BASE = 16908288


def _layer_ns(version=26, layer_utils_raises=False):
    names = sorted(set(re.findall(r"\be[A-Z]\w*", _UTILS["GetLayerString"]
                                  + _UTILS["GetLayerFromString"])))
    ns = {n: i + 1 for i, n in enumerate(names)}
    ns["eNoLayer"] = 0
    calls = []

    class LU:
        @staticmethod
        def MechanicalLayer(n):
            calls.append(n)
            if layer_utils_raises:
                raise PascalError("LayerUtils")
            return _BASE + n
    ns.update(LayerUtils=LU, AltiumMajorVersion=lambda _d: version,
              MechScanLimit=64,
              # A Case statement the evaluator does not take; only reached
              # for Mechanical1..16, which these tests name directly.
              GetLayerFromString=lambda s: ns.get("e" + s, ns["eTopLayer"]))
    _caught(ns)
    build(_UTILS, [n for n in ("MechLayerFromNumber", "MechLayerNameAbove16",
                               "GetLayerString") if n in _UTILS], ns)
    return ns, calls


def test_mechanical21_is_named_not_unknown():
    ns, _ = _layer_ns()
    assert ns["GetLayerString"](_BASE + 21) == "Mechanical21"
    assert ns["GetLayerString"](_BASE + 64) == "Mechanical64"
    assert ns["GetLayerString"](ns["eMechanical3"]) == "Mechanical3"


def test_a_layer_that_does_not_verify_stays_unknown():
    ns, calls = _layer_ns()
    assert ns["GetLayerString"](_BASE + 65) == "Unknown"
    assert ns["GetLayerString"](999) == "Unknown"
    assert ns["GetLayerString"](0) == "Unknown"
    assert len(calls) <= 4


@pytest.mark.parametrize("version", [-1, 18])
def test_the_lookup_is_gated_to_ad19_and_later(version):
    ns, calls = _layer_ns(version=version)
    assert ns["GetLayerString"](_BASE + 21) == "Unknown"
    assert calls == []


def test_a_raising_layer_utils_is_counted_and_stays_unknown():
    ns, _ = _layer_ns(layer_utils_raises=True)
    assert ns["GetLayerString"](_BASE + 21) == "Unknown"
    assert ns["_caught"] == ["MechLayerFromNumber:LayerUtils.MechanicalLayer"]
