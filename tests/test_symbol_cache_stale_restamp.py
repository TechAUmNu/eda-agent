# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A put() into a cache stamped for an older library must not re-certify the old entries."""
import json
import os
import time

from eda_agent.design.symbols import SymbolCache, SymbolModel


def _model(lib, ref):
    return SymbolModel.from_dict({
        "lib_path": str(lib), "lib_ref": ref,
        "pins": [{"designator": "1", "name": "A", "x": 0, "y": 0,
                  "orientation": 0, "length": 100,
                  "electrical_type": "passive", "hidden": False}],
        "body_bbox": {"x_min": 0, "y_min": -100, "x_max": 100, "y_max": 0},
        "designator_prefix": "U", "description": "",
    })


def test_restamp_drops_entries_from_an_older_library(tmp_path):
    lib = tmp_path / "lib.SchLib"
    lib.write_bytes(b"v1")
    cache = SymbolCache(tmp_path / "cache")
    cache.put(_model(lib, "OLD_A"))
    cache.put(_model(lib, "OLD_B"))
    assert cache.get(str(lib), "OLD_A") is not None

    # The library is saved: new mtime.
    t = time.time() + 5
    lib.write_bytes(b"v2")
    os.utime(lib, (t, t))
    assert cache.get(str(lib), "OLD_A") is None          # stale -> miss

    cache.put(_model(lib, "NEW_C"))                       # one fresh read
    assert cache.get(str(lib), "NEW_C") is not None
    # The bug: OLD_A/OLD_B came back as fresh after the re-stamp.
    assert cache.get(str(lib), "OLD_A") is None
    assert cache.get(str(lib), "OLD_B") is None
