# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D2 of the 2026-10-11 live run: app_save_all listed a file it had just
written ALSO in skipped_unmodified / libraries_skipped. The walk reached it
twice (a free document through DM_Projects and DM_FreeDocumentsProject, or
two slash spellings of one path) and deduped the skip list by UpperCase,
not DocPathKey. Each test fails on ef2d79b.
"""
from __future__ import annotations

from tests.live_2026_10_11 import _caught, _field, _keys
from tests.test_bridge_record_names_one_file import (
    LIB, SHEET, Listed, World, _key)


# ---------------------------------------------------------------------------
# D2. One visit per file per save pass
# ---------------------------------------------------------------------------

LIB_FWD = LIB.replace("\\", "/")


def test_a_free_document_the_walk_reaches_twice_is_written_once_and_never_skipped():
    """DM_Projects holding the Free Documents project too: the walk reached
    the library twice, wrote it on the first visit and filed it under
    skipped_unmodified (libraries_skipped) on the second."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.free.docs = [Listed(LIB_FWD, "PCBLIB")]
    w.ns["GetWorkspace"].projects = [w.project, w.free]
    w.lib.Modified = True
    w.save_all(only_modified=True)
    assert w.lib.saves == 1
    assert _key(LIB) not in _keys(w.SaveSkippedPaths), (
        "the library just written is listed as skipped: " + w.SaveSkippedPaths)


def test_a_bridge_edited_field_document_reached_twice_is_listed_once():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.ns.setdefault("MarkLastFlagRaw", "")
    w.ns.setdefault("MarkLastFlagTests", "")
    w.free.docs = [Listed(LIB_FWD, "PCBLIB")]
    w.ns["GetWorkspace"].projects = [w.project, w.free]
    _field(w.lib)
    w.MarkDocDirty(LIB)
    w.save_all(only_modified=True)
    assert w.lib.saves == 1
    assert _key(LIB) not in _keys(w.SaveSkippedPaths)
    assert w.record_keys() == set()


def test_a_clean_library_under_two_spellings_is_skipped_once():
    """carrier.PcbLib was listed under both slash directions."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.free.docs = [Listed(LIB_FWD, "PCBLIB")]
    w.project.docs = [Listed(SHEET), Listed(LIB, "PCBLIB")]
    w.save_all(only_modified=True)
    assert w.lib.saves == 0
    assert _keys(w.SaveSkippedPaths).count(_key(LIB)) == 1, w.SaveSkippedPaths
    assert w.SaveSkipped == 2          # the sheet and the library, once each
