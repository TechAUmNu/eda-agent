# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""D2 of the 2026-10-11 live run: app_save_all listed a file it had just
written ALSO in skipped_unmodified / libraries_skipped. The walk reached it
twice (a free document through DM_Projects and DM_FreeDocumentsProject, or
two slash spellings of one path) and deduped the skip list by UpperCase,
not DocPathKey. Each test fails on ef2d79b. The review of 0c4f20e added the
last three: the exit must not outlive the walk, and a document with no
absolute path is listed, never matched by an empty key (each fails on
0c4f20e).
"""
from __future__ import annotations

import pytest

from tests.live_2026_10_11 import _caught, _field, _keys
from tests.test_bridge_record_names_one_file import (
    LIB, SHEET, Listed, ServerDoc, World, _key)


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


# ---------------------------------------------------------------------------
# Review of 0c4f20e: the one-visit exit belongs to the walk, not to the list
# ---------------------------------------------------------------------------

def test_a_proj_save_after_save_all_writes_an_edit_made_since():
    """proj_save, proj_close save=true and the other direct
    SaveProjectMembers callers never reset SaveAttemptedKeys, so the early
    exit skipped every file the LAST app_save_all had written: the new edit
    stayed unsaved while proj_save answered success."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.sheet.Modified = True
    w.save_all(only_modified=True)
    assert w.sheet.saves == 1
    w.sheet.Modified = True                    # edited again after the pass
    w.ns["SaveProjectMembers"](w.project)      # what proj_save runs
    assert w.sheet.saves == 2
    assert w.sheet.Modified is False


def test_the_next_save_all_still_dedupes_within_its_own_pass():
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    w.free.docs = [Listed(LIB_FWD, "PCBLIB")]
    w.ns["GetWorkspace"].projects = [w.project, w.free]
    w.lib.Modified = True
    w.save_all(only_modified=True)
    w.lib.Modified = True
    w.save_all(only_modified=True)
    assert w.lib.saves == 2                    # once per pass, never twice in one
    assert w.SaveWalkPass is False


def _walk_one(w, listed, server_doc):
    w.ns["ListedServerDoc"] = lambda _doc: server_doc
    w.ns["SaveAttemptedKeys"] = ""
    w.ns["SaveUnresolvedPaths"] = ""
    w.ns["SaveWalkPass"] = True
    w.ns["SaveOneDocByDocRef"](listed)
    w.ns["SaveWalkPass"] = False


@pytest.mark.parametrize("name", ["", "Sheet9.SchDoc"])
def test_a_document_with_no_absolute_path_is_listed_not_matched_by_accident(name):
    """An empty key matched an empty attempted list (Pos('||','||') = 1), so
    an unnamed document was dropped from every list; a bare one was keyed by
    its name. Neither names a file to write: it is listed as not reached."""
    w = World(bare="nil", focus=None)
    _caught(w.ns)
    doc = ServerDoc(w.disk, name)
    doc.Modified = True
    _walk_one(w, Listed(name), doc)
    assert doc.saves == 0
    assert w.SaveUnresolvedPaths == (name or "(unnamed document)")
