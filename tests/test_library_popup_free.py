# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""Library.pas: check first, so the modal never fires.

WHY. Altium's DelphiScript engine shows an exception as a MODAL 'press OK'
dialog BEFORE the surrounding Try/Except runs (measured; recorded beside
WriteFileContent in Main.pas). A Try around a call that raises on an
ordinary bad input is not a guard, so each site below now ASKS first and
does, on the 'no' branch, exactly what its Except branch did:

* The held library component (LastCreatedLibComponent) outlives the
  command that set it. Once its library is closed or the component is
  removed, its first member read is the modal. It is now dropped on
  evidence (library closed, or reopened as a different document while
  document identity is stable), forgotten before every RemoveSchComponent
  that can remove it, renamed with it, and its NAME is never read back off
  it (TargetLibComponentName).
* Component readers are never created on an absolute library path with no
  file behind it (LibFileMissing) - the normal state of a library the
  bridge has just authored.
* install / uninstall / component counts, PcbLib loads, 3D model formats
  and mechanical kinds on builds that predate them ask first too.
* Where no guard can be conservative (the label write,
  GetPCBLibraryByPath) the catch is COUNTED with RecordCaughtException for
  the live probe. MarkLibDirty, SetOwnerPart and TargetLibComponentName
  were counted too until 2026-10-11, when the live run showed their read
  off CurrentSchComponent is the engine's 'Undeclared identifier' HALT,
  which no Except sees: they no longer read that reference at all
  (tests/test_live_editor_component_is_never_read.py).

Every test EXECUTES the real Pascal (whole routines, or the exact source
text of a handler's statements) through tests/pascal_eval.py, and fails on
the code before this change.
"""
from __future__ import annotations

import ntpath
import re
from pathlib import Path

import pytest

from tests.pascal_eval import PascalError, build
from tests.pascal_source import load, strip_comments

_ROOT = Path(__file__).resolve().parents[1]
_ALTIUM = _ROOT / "scripts" / "altium"
_LIBRARY = _ALTIUM / "Library.pas"

ESCHLIB = 7
ESHEET = 3


def _lib():
    return load(_LIBRARY, minimum=40)


def _main():
    return load(_ALTIUM / "Main.pas", minimum=40)


# ---------------------------------------------------------------------------
# Fixtures: the Altium objects the bodies touch
# ---------------------------------------------------------------------------

class Doc:
    """An IServerDocument: identity is all the bodies may use."""


class Client:
    def __init__(self, docs=None, unstable=False):
        self.docs = {k.upper(): v for k, v in (docs or {}).items()}
        self.unstable = unstable

    def GetDocumentByPath(self, p):
        d = self.docs.get(p.upper())
        if d is not None and self.unstable:
            return Doc()            # a fresh wrapper on every call
        return d


class Comp:
    def __init__(self, name):
        self.LibReference = name
        self.CurrentPartID = 1
        self.DisplayMode = 0


class Dead:
    """A component whose library has gone. ANY member read is the modal;
    it is an AssertionError, not a PascalError, so no translated Except
    can swallow it and the test fails loudly."""

    def __getattribute__(self, name):
        raise AssertionError(f"member read on a dead component: {name}")


class Raising:
    """A component whose member reads raise as an ORDINARY, catchable
    exception. NOT a model of CurrentSchComponent: live 2026-10-11 its
    member read was the engine's uncatchable halt, which ``Dead`` models."""

    def __getattr__(self, name):
        raise PascalError(f"Undeclared identifier: {name}")

    def __setattr__(self, name, value):
        raise PascalError(f"Undeclared identifier: {name}")


class SchLib:
    def __init__(self, name, comps=(), current=None, object_id=ESCHLIB):
        self.DocumentName = name
        self.ObjectId = object_id
        self.CurrentSchComponent = current
        self.index = {c.LibReference: c for c in comps}

    def GetState_SchComponentByLibRef(self, n):
        return self.index.get(n)


class Sheet:
    """A SchDoc: it has no CurrentSchComponent member at all."""

    ObjectId = ESHEET
    DocumentName = r"C:\P\S.SchDoc"

    @property
    def CurrentSchComponent(self):
        raise AssertionError("CurrentSchComponent read off a sheet")


def _ns(client=None, name_counts=None, resolved=None, files=(), extra=None,
        lib_helpers=True):
    caught: list[str] = []
    name_counts = name_counts or {}
    resolved = resolved or {}
    files = {f.upper() for f in files}
    ns = {
        "Client": client or Client(),
        "eSchLib": ESCHLIB,
        "RecordCaughtException": caught.append,
        "DocNameCount": lambda n: name_counts.get(n, 0),
        "ResolveLoadedDocPath": lambda n: resolved.get(n, ""),
        "FileExists": lambda p: p.upper() in files,
        "ScanLibForComponent": lambda lib, n: None,
        "LastCreatedLibComponent": None,
        "LastCreatedLibComponentName": "",
        "LastCreatedLibDoc": None,
        "LastCreatedLibPath": "",
        "_caught": caught,
    }
    ns.update(extra or {})
    build(_main(), ["LooksAbsolutePath", "DocPathKey", "DocNameKey"], ns)
    if not lib_helpers:
        return ns
    build(_lib(), ["RememberedLibKey", "RememberLibComponent",
                   "ForgetRememberedLibComponent", "SameLibAsRemembered",
                   "LastCreatedLibIsLive", "ForgetLibComponentIf",
                   "GetTargetLibComponent", "TargetLibComponentName",
                   "FindLibComponentInMemory"], ns)
    return ns


A = r"C:\Libs\A.SchLib"
B = r"C:\Libs\B.SchLib"


def _held(ns, lib, comp, name):
    ns["RememberLibComponent"](lib, comp, name)
    assert ns["LastCreatedLibComponent"] is comp


# ---------------------------------------------------------------------------
# 1. The held component: recorded, judged, dropped
# ---------------------------------------------------------------------------

def test_held_component_is_dropped_once_its_library_closed():
    """Closing a library frees every component in it; the next member read
    on the held one was the modal. GetTargetLibComponent now drops it and
    answers Nil, the answer it gives when nothing is held (it no longer
    falls back to the editor's current component: 2026-10-11)."""
    docs = {A: Doc()}
    client = Client(docs)
    ns = _ns(client)
    lib = SchLib(A, current=Dead())
    held = Comp("MADE")
    _held(ns, lib, held, "MADE")
    assert ns["LastCreatedLibPath"] == A
    assert ns["GetTargetLibComponent"](lib) is held

    client.docs.clear()                       # the library was closed
    ns["LastCreatedLibComponent"] = Dead()    # and its components freed
    assert ns["GetTargetLibComponent"](lib) is None
    assert ns["LastCreatedLibComponent"] is None
    assert ns["LastCreatedLibComponentName"] == ""
    assert ns["LastCreatedLibPath"] == "" and ns["LastCreatedLibDoc"] is None


def test_reopen_is_judged_by_identity_only_when_identity_is_stable():
    """A library closed and reopened outside the bridge is a different
    document object at the same path. That is evidence only when two
    lookups in a row agree; a build that wraps every call afresh keeps
    today's answer (the held component is used)."""
    client = Client({A: Doc()})
    ns = _ns(client)
    lib = SchLib(A)
    held = Comp("X")
    _held(ns, lib, held, "X")
    client.docs[A.upper()] = Doc()            # reopened
    assert ns["LastCreatedLibIsLive"](0) is False

    client = Client({A: Doc()}, unstable=True)
    ns = _ns(client)
    _held(ns, lib, held, "X")
    assert ns["LastCreatedLibIsLive"](0) is True
    assert ns["GetTargetLibComponent"](lib) is held


def test_a_bare_library_name_is_never_judged_dead():
    """A free document reports a bare DocumentName. Unless exactly one
    listed document carries that name, nothing locates it, so nothing is
    concluded: the held component stays (today's behaviour)."""
    ns = _ns(Client({}), name_counts={"A.SchLib": 2},
             resolved={"A.SchLib": A})
    lib = SchLib("A.SchLib")
    held = Comp("X")
    _held(ns, lib, held, "X")
    assert ns["LastCreatedLibPath"] == "A.SchLib", "a namesake was guessed"
    assert ns["LastCreatedLibIsLive"](0) is True

    ns = _ns(Client({}), name_counts={"A.SchLib": 1},
             resolved={"A.SchLib": A})
    _held(ns, lib, held, "X")
    assert ns["LastCreatedLibPath"] == A
    assert ns["LastCreatedLibIsLive"](0) is False, "closed, and locatable"


def test_current_component_is_never_read_off_a_sheet():
    """Lib_SplitPinFunctions accepted any focused document; with a sheet
    focused and nothing held, CurrentSchComponent was read off a SchDoc,
    which has no such member. Nil now: the NO_COMPONENT reply."""
    ns = _ns()
    assert ns["GetTargetLibComponent"](Sheet()) is None
    assert ns["TargetLibComponentName"](Sheet()) == ""


def test_target_name_is_the_recorded_one_never_read_back():
    """Lib_GetPinList / Lib_SetPinOwnerPart read LibReference off the held
    reference: exactly the read Main.pas measured holding a caller on the
    modal for two minutes. The recorded name is what FindLibComponentInMemory
    matches the held component by."""
    client = Client({A: Doc()})
    ns = _ns(client)
    lib = SchLib(A, current=Comp("SHOWN"))
    held = Comp("MADE")
    _held(ns, lib, held, "MADE")
    ns["LastCreatedLibComponent"] = held
    held.__class__ = Dead                      # any read would fail the test
    assert ns["TargetLibComponentName"](lib) == "MADE"

    # Nothing held: '' WITHOUT asking the editor's component anything
    # (3aca481 read its LibReference: the engine halt, live 2026-10-11).
    ns = _ns(client)
    assert ns["TargetLibComponentName"](SchLib(A, current=Dead())) == ""
    assert ns["_caught"] == []


def test_in_memory_lookup_answers_the_held_component_only_live_and_here():
    """Step 3 handed the held component back on a name match alone: after
    its library closed (the modal on the next read), and for ANOTHER
    library (copying X from A to B found A's X and answered NAME_EXISTS)."""
    client = Client({A: Doc(), B: Doc()})
    ns = _ns(client)
    lib_a, lib_b = SchLib(A), SchLib(B)
    held = Comp("X")
    _held(ns, lib_a, held, "X")
    find = ns["FindLibComponentInMemory"]
    assert find(lib_a, "X") is held
    assert find(lib_b, "X") is None, "another library's component"
    assert ns["LastCreatedLibComponent"] is held, "B's miss must not drop it"
    assert find(SchLib(""), "X") is held, "unknown library: today's answer"

    client.docs.pop(A.upper())
    ns["LastCreatedLibComponent"] = Dead()
    assert find(lib_a, "X") is None
    assert ns["LastCreatedLibComponent"] is None


@pytest.mark.parametrize("comp_is_held,name,lib,dropped", [
    (True, "", A, True),            # the same reference
    (False, "X", A, True),          # same recorded name, same library
    (False, "X", B, False),         # a namesake in another library
    (False, "Y", A, False),         # another component
])
def test_forget_before_remove(comp_is_held, name, lib, dropped):
    ns = _ns(Client({A: Doc(), B: Doc()}))
    held = Comp("X")
    _held(ns, SchLib(A), held, "X")
    comp = held if comp_is_held else Comp("other")
    ns["ForgetLibComponentIf"](SchLib(lib), comp, name)
    assert (ns["LastCreatedLibComponent"] is None) is dropped


class _Freed:
    """A held reference whose library has gone: not even compared."""

    def __eq__(self, other):
        if other is None:           # '= Nil' is a test of the variable
            return False
        raise AssertionError("compared against a freed component")

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = object.__hash__


def test_forget_never_compares_a_held_component_it_need_not():
    """A component held from another library is left alone, and one whose
    library has closed is dropped, both WITHOUT comparing the reference: a
    pointer compare against a freed object is the one read this change
    must not add."""
    client = Client({A: Doc(), B: Doc()})
    ns = _ns(client)
    _held(ns, SchLib(A), Comp("X"), "X")
    ns["LastCreatedLibComponent"] = _Freed()
    ns["ForgetLibComponentIf"](SchLib(B), Comp("Y"), "Y")
    assert isinstance(ns["LastCreatedLibComponent"], _Freed)
    client.docs.pop(A.upper())
    ns["ForgetLibComponentIf"](SchLib(A), Comp("Y"), "Y")
    assert ns["LastCreatedLibComponent"] is None


def _no_direct_global_writes():
    # The two renames (Lib_BatchRename, and NoteLibComponentRenamed for
    # obj_modify LibReference) update only the recorded NAME of a component
    # that stays held; every set and every clear goes through the pair.
    allowed = {"RememberLibComponent", "ForgetRememberedLibComponent",
               "Lib_BatchRename", "NoteLibComponentRenamed"}
    bad = []
    for fname, body in _lib().items():
        if fname in allowed:
            continue
        if re.search(r"\bLastCreatedLibComponent(Name)?\s*:=", body):
            bad.append(fname)
    return bad


def test_the_held_component_is_set_and_cleared_in_one_place():
    """Every set records WHICH library (path + document) the component is
    in, or liveness cannot be asked; every clear clears all four fields.
    The four set sites and RefreshSchLibFromDisk used to assign the two
    globals directly."""
    assert _no_direct_global_writes() == []


_REMOVE = re.compile(r"(\w+)\.RemoveSchComponent\((\w+)\)")


def test_every_remove_that_can_remove_the_held_component_forgets_it_first():
    """A removed component is freed, and FindLibComponentInMemory would hand
    the held one back by name: the first member read on it was the modal
    (lib_delete_component X, then lib_get_component_details X). The
    remove-and-re-add of a RENAME keeps the same component alive."""
    renames = {"Lib_BatchRename", "Lib_RenameComponent"}
    seen = 0
    for fname, body in _lib().items():
        for m in _REMOVE.finditer(body):
            lib, comp = m.group(1), m.group(2)
            if fname in renames:
                assert re.search(rf"{lib}\.AddSchComponent\({comp}\)",
                                 body[m.end():]), (fname, comp)
                continue
            seen += 1
            before = body[:m.start()]
            last = before.rfind("ForgetLibComponentIf(")
            assert last >= 0, f"{fname}: {m.group(0)} forgets nothing first"
            call = re.match(r"ForgetLibComponentIf\((\w+), (\w+), ", before[last:])
            assert call and call.groups() == (lib, comp), (
                f"{fname}: the forget before {m.group(0)} names "
                f"{call.groups() if call else '?'}")
            between = before[last:]
            assert not _REMOVE.search(between), fname
    assert seen >= 4, "the delete, copy-overwrite and both move removes"


def test_batch_rename_carries_the_held_component_to_its_new_name():
    body = strip_comments(_lib()["Lib_BatchRename"])
    m = re.search(r"If \(LastCreatedLibComponent <> Nil\).*?"
                  r"LastCreatedLibComponentName := NewName;", body, re.S)
    assert m, "Lib_BatchRename never renames the held component"
    snip = ("Procedure Snip(SchLib : Variant; Component : Variant; "
            "OldName : String; NewName : String);\nBegin\n"
            + m.group(0) + "\nEnd;")
    ns = _ns(Client({A: Doc(), B: Doc()}))
    held = Comp("OLD")
    _held(ns, SchLib(A), held, "OLD")
    build({"Snip": snip}, ["Snip"], ns)
    ns["Snip"](SchLib(B), Comp("OLD"), "OLD", "NEW")
    assert ns["LastCreatedLibComponentName"] == "OLD", "a namesake elsewhere"
    ns["Snip"](SchLib(A), held, "OLD", "NEW")
    assert ns["LastCreatedLibComponentName"] == "NEW"


@pytest.mark.parametrize("handler", ["Lib_GetPinList", "Lib_SetPinOwnerPart"])
def test_pin_handlers_never_read_the_name_off_a_component(handler):
    body = strip_comments(_lib()[handler])
    head = body[:body.index("LookupLibComponent(SchLib, WantName)")]
    assert ".LibReference" not in head, (
        f"{handler} reads LibReference before resolving the name")
    assert re.search(r"If WantName = '' Then\s+WantName := "
                     r"TargetLibComponentName\(SchLib\);", head), handler


def _split_ns(lib, held=None, held_lib=None, name=""):
    client = Client({A: Doc(), B: Doc()})
    ns = _ns(client)
    ns["ExtractJsonValue"] = lambda p, k: p.get(k, "")
    if held is not None:
        _held(ns, held_lib or lib, held, held.LibReference)
    body = strip_comments(_lib()["Lib_SplitPinFunctions"])
    m = re.search(r"Component := Nil;.*?If SchLib\.ObjectId = eSchLib Then.*?"
                  r"(?=\n\s+If Component = Nil Then)", body, re.S)
    assert m, "Lib_SplitPinFunctions does not resolve through the library"
    snip = ("Procedure Snip(SchLib : Variant; Params : Variant);\nVar\n"
            "    WantName : String;\nBegin\n" + m.group(0)
            + "\n    SnipOut := Component;\nEnd;")
    build({"Snip": snip}, ["Snip"], ns)
    ns["Snip"](lib, {"component_name": name} if name else {})
    return ns["SnipOut"]


def test_split_pin_functions_resolves_through_the_library():
    """SchIterator_Create on CurrentSchComponent raised 'Undeclared
    identifier' (measured). The target is resolved BY NAME through the
    library, whose answer carries its methods: the held component's
    recorded name, or component_name. The editor's own selection is never
    used (2026-10-11): nothing held and no name is NO_COMPONENT."""
    narrowed = Comp("SHOWN")
    lib = SchLib(A, comps=[narrowed], current=Dead())
    assert _split_ns(lib) is None, "the editor's selection is not a target"
    assert _split_ns(lib, name="SHOWN") is narrowed, "component_name"
    held = Comp("MADE")
    assert _split_ns(SchLib(A), held=held) is held, "the held one, live"
    assert _split_ns(Sheet()) is None, "a sheet with nothing held"
    assert _split_ns(Sheet(), held=held, held_lib=SchLib(A)) is held


# ---------------------------------------------------------------------------
# 2. Counted, not guarded (needs probe)
# ---------------------------------------------------------------------------
# MarkLibDirty's component invalidate and SetOwnerPart's part read used to
# live here as COUNTED catches. Live 2026-10-11 the read off
# CurrentSchComponent was the engine's halt, which no Except sees, so a
# count could never have fired. Both now read only narrowed references
# (tests/test_live_editor_component_is_never_read.py).


class Label:
    def __init__(self):
        self.FontId, self.Color, self.IsHidden = 1, 0, False
        self.Justification = 0
        self._o = 0

    @property
    def Orientation(self):
        return self._o

    @Orientation.setter
    def Orientation(self, v):
        if not 0 <= v <= 3:
            raise PascalError("value out of TRotationBy90")
        self._o = v


def test_apply_label_format_counts_a_raising_write():
    ns = _ns(extra={"SchBeginModify": lambda o: None,
                    "SchEndModify": lambda o: None}, lib_helpers=False)
    build(_lib(), ["ApplyLabelFormat"], ns)
    f = ns["ApplyLabelFormat"]
    args = dict(HasFontId=False, NewFontId=0, HasColor=False, NewColor=0,
                HasIsHidden=False, NewIsHidden=False, HasJustification=False,
                NewJustification=0, OnlyMismatched=False)
    assert f(Label(), HasOrientation=True, NewOrientation=2, **args) == 1
    assert ns["_caught"] == []
    assert f(Label(), HasOrientation=True, NewOrientation=90, **args) == -1
    assert ns["_caught"] == ["ApplyLabelFormat:write"]


# ---------------------------------------------------------------------------
# 3. Component readers on a library with no file
# ---------------------------------------------------------------------------

def _safe_sch_lib_path(p):
    if ntpath.splitext(p)[1].upper() == ".INTLIB":
        return ""
    return p


@pytest.mark.parametrize("path,on_disk,missing", [
    (A, False, True),             # authored, never saved
    (A, True, False),
    ("A.SchLib", False, False),   # relative: today's route
    (r"C:\Libs\X.IntLib", False, False),   # no source found: today's route
    ("", False, False),
])
def test_lib_file_missing(path, on_disk, missing):
    ns = _ns(files=[path] if on_disk else [],
             extra={"SafeSchLibPath": _safe_sch_lib_path})
    build(_lib(), ["LibFileMissing"], ns)
    assert ns["LibFileMissing"](path) is missing


class _SchServer:
    def __init__(self):
        self.created = []

    def CreateLibCompInfoReader(self, p):
        self.created.append(p)
        return None                 # stops the snippet at READER_FAILED


def _reader_guard(fname):
    """The statements of ``fname`` from its LibFileMissing test through the
    CreateLibCompInfoReader call, open Begins closed: the exact source text
    that decides whether a reader is made."""
    body = strip_comments(_lib()[fname])
    create = body.find("CreateLibCompInfoReader(")
    guard = body.rfind("LibFileMissing(", 0, create)
    assert guard >= 0, f"{fname} creates a reader without asking for the file"
    start = body.rfind("If ", 0, guard)
    end = body.find(chr(10), create)
    text = body[start:end]
    arg = re.search(r"LibFileMissing\((\w+)\)", text).group(1)
    assert f"SafeSchLibPath({arg})" in text, (fname, "a different path")
    depth = len(re.findall(r"\bBegin\b", text)) - len(re.findall(r"\bEnd\b", text))
    text += "\nEnd;" * depth
    return arg, text


@pytest.mark.parametrize("fname", [
    "OtherLibComponentName", "SearchOneLibrary", "ResolveLibComponent",
    "Lib_GetComponentDetails", "Lib_AuditStyles", "Lib_SetLabelFormat",
    "Lib_SetLabelFormats"])
def test_no_reader_is_made_for_a_library_with_no_file(fname):
    """The bridge authors libraries unsaved, so an open .SchLib often has no
    file yet, and the reader raised on it (a modal) where an empty reader
    answers 'no components'. Executed: missing -> no reader; on disk -> the
    reader is made exactly as before."""
    arg, text = _reader_guard(fname)
    snip = (f"Procedure Snip({arg} : String; Idx : Integer);\nVar\n"
            "    CompNum : Integer;\nBegin\n" + text + "\nEnd;")
    for on_disk, want in ((False, 0), (True, 1)):
        server = _SchServer()
        ns = _ns(files=[A] if on_disk else [],
                 extra={"SafeSchLibPath": _safe_sch_lib_path,
                        "SchServer": server,
                        "BuildErrorResponse": lambda *a: "ERR"})
        build(_lib(), ["LibFileMissing"], ns)
        build({"Snip": snip}, ["Snip"], ns)
        ns["Snip"](A, 3)
        assert len(server.created) == want, (fname, on_disk)


def test_resolve_by_index_on_a_missing_file_gives_the_empty_reader_reply():
    _, text = _reader_guard("ResolveLibComponent")
    snip = ("Procedure Snip(LibPath : String; Idx : Integer);\nVar\n"
            "    CompNum : Integer;\nBegin\n" + text + "\nEnd;")
    ns = _ns(extra={"SafeSchLibPath": _safe_sch_lib_path,
                    "SchServer": _SchServer()})
    build(_lib(), ["LibFileMissing"], ns)
    build({"Snip": snip}, ["Snip"], ns)
    ns["Snip"](A, 3)
    assert ns["ErrCode"] == "INDEX_OUT_OF_RANGE"
    assert ns["ErrMsg"] == "component_index 3 out of range (0..-1)"


# ---------------------------------------------------------------------------
# 4. Installed libraries
# ---------------------------------------------------------------------------

class _Ilm:
    def __init__(self, installed=(), raise_on=()):
        self.installed = list(installed)
        self.calls = []

    def InstallLibrary(self, p):
        self.calls.append(("install", p))
        raise PascalError("no such file")

    def UnInstallLibrary(self, p):
        self.calls.append(("uninstall", p))
        if p not in self.installed:
            raise PascalError("not installed")
        self.installed.remove(p)

    @property
    def InstalledLibraryCount(self):
        return len(self.installed)

    def InstalledLibraryPath(self, i):
        return self.installed[i]

    @property
    def AvailableLibraryCount(self):
        return 0

    def GetComponentCount(self, p):
        self.calls.append(("count", p))
        return 12


def _ilm_ns(ilm, files=()):
    ns = _ns(files=files, extra={
        "IntegratedLibraryManager": ilm,
        "ExtractJsonValue": lambda params, key: params.get(key, ""),
        "BuildErrorResponse": lambda r, c, m: ("ERR", c),
        "BuildSuccessResponse": lambda r, data: data,
        "BoolToJsonStr": lambda b: "true" if b else "false",
        "EscapeJsonString": lambda s: s,
        "InstalledLibTypeOrdinal": lambda p: 0,
        "LibTypeName": lambda o: "Integrated",
    })
    build(_main(), ["DocPathKey"], ns)
    helpers = [n for n in ("InstalledLibPathList", "InstalledPathsNotIn",
                           "InstalledPathWithName", "InstalledPathsJson")
               if n in _lib()]
    build(_lib(), helpers + ["InstalledLibNameKnown", "Lib_InstallLibrary",
                             "Lib_UninstallLibrary",
                             "Lib_GetInstalledLibraries"], ns)
    return ns


I = r"C:\Libs\Gone.IntLib"


def test_install_of_a_missing_file_is_answered_without_asking():
    ilm = _Ilm()
    ns = _ilm_ns(ilm)
    out = ns["Lib_InstallLibrary"]({"library_path": I}, "r1")
    assert '"installed":false' in out and ilm.calls == []
    out = ns["Lib_InstallLibrary"]({"library_path": "Rel.IntLib"}, "r1")
    assert ilm.calls == [("install", "Rel.IntLib")], "relative: asked as before"
    assert ns["_caught"] == ["Lib_InstallLibrary:InstallLibrary"]


def test_uninstall_of_a_library_not_installed_is_answered_without_asking():
    other = r"C:\Libs\Other.IntLib"
    ilm = _Ilm(installed=[other])
    ns = _ilm_ns(ilm)
    out = ns["Lib_UninstallLibrary"]({"library_path": I}, "r1")
    assert '"uninstalled":false' in out and ilm.calls == []
    # A different spelling of an installed file is still asked about.
    out = ns["Lib_UninstallLibrary"]({"library_path": r"c:/libs/other.intlib"}, "r1")
    assert ilm.calls == [("uninstall", r"c:/libs/other.intlib")]
    out = ns["Lib_UninstallLibrary"]({"library_path": other}, "r1")
    assert '"uninstalled":true' in out


def test_component_count_is_not_asked_of_an_installed_file_that_has_gone():
    here = r"C:\Libs\Here.IntLib"
    ilm = _Ilm(installed=[I, here, "Server.DbLib"])
    ns = _ilm_ns(ilm, files=[here])
    out = ns["Lib_GetInstalledLibraries"]({}, "r1")
    assert [c for c in ilm.calls if c[0] == "count"] == [
        ("count", here), ("count", "Server.DbLib")]
    assert '"library_path":"' + I + '"' in out
    assert out.count('"component_count":-1') == 1


# ---------------------------------------------------------------------------
# 5. PcbLib load, 3D model format, mechanical kinds
# ---------------------------------------------------------------------------

class _PcbServer:
    def __init__(self, get_raises):
        self.get_raises, self.loaded = get_raises, []

    def GetPCBLibraryByPath(self, p):
        if self.get_raises:
            raise PascalError("not loaded")
        return None

    def LoadPCBLibraryByPath(self, p):
        self.loaded.append(p)
        if not p.upper().endswith(".PCBLIB"):
            raise PascalError("not a PCB library")
        return "LIB"


_LOAD = re.compile(r"Try (\w+) := PCBServer\.GetPCBLibraryByPath\((\w+)\);"
                   r".*?LoadPCBLibraryByPath\(\2\); Except End;", re.S)


@pytest.mark.parametrize("fname", ["Lib_MoveFootprints", "Lib_CopyFootprint"])
def test_pcb_library_load_is_only_attempted_on_a_loadable_file(fname):
    body = strip_comments(_lib()[fname])
    blocks = list(_LOAD.finditer(body))
    # ONE background load since 2026.10.12.01: the SOURCE, and only when it
    # is read, never edited. A library that will be edited is opened as a
    # document or refused (P13, tests/test_live_03_findings.py).
    assert len(blocks) == 1 == body.count("LoadPCBLibraryByPath("), fname
    for m in blocks:
        var, path = m.group(1), m.group(2)
        snip = (f"Procedure Snip({path} : String);\nVar\n    {var} : Variant;\n"
                f"Begin\n    {var} := Nil;\n" + m.group(0)
                + f"\n    SnipOut := {var};\nEnd;")
        here = r"C:\Libs\F.PcbLib"
        for p, get_raises, want_loads in (
                (r"C:\Libs\Gone.PcbLib", False, []),
                (r"C:\Libs\A.SchLib", False, []),
                (here, False, [here])):
            server = _PcbServer(get_raises)
            ns = _ns(files=[here, r"C:\Libs\A.SchLib"],
                     extra={"PCBServer": server, "DeleteFromSource": False,
                            "SameLib": False})
            build(_lib(), ["PcbLibLoadable"], ns)
            build({"Snip": snip}, ["Snip"], ns)
            ns["Snip"](p)
            assert server.loaded == want_loads, (fname, var, p)
        server = _PcbServer(True)
        ns = _ns(files=[here], extra={"PCBServer": server,
                                      "DeleteFromSource": False,
                                      "SameLib": False})
        build(_lib(), ["PcbLibLoadable"], ns)
        build({"Snip": snip}, ["Snip"], ns)
        ns["Snip"](here)
        assert ns["_caught"] == [f"{fname}:GetPCBLibraryByPath"]
        assert ns["SnipOut"] == "LIB"


def test_an_unloadable_model_format_gets_the_model_nil_reply_before_preprocess():
    body = strip_comments(_lib()["Lib_Link3DModel"])
    m = re.search(r"If Not Model3DExtMayLoad\(ModelPath\) Then\s+Begin.*?End;",
                  body, re.S)
    assert m, "Lib_Link3DModel hands any file to ModelFactory_FromFilename"
    assert m.start() < body.index("PCBServer.PreProcess;"), (
        "the check must answer before the transaction opens")
    nil_text = re.search(r"FailCode := 'MODEL_LOAD_FAILED';\s+Failure := (.*?);",
                         body, re.S).group(1)
    template = ("'Could not load a 3D model from ' + ModelPath + "
                "'. Nothing was removed or added.'")
    assert " ".join(nil_text.split()) == template, (
        "the Model = Nil reply changed; keep the two texts identical")
    replies = []
    snip = ("Procedure Snip(ModelPath : String; RequestId : String);\nBegin\n"
            + m.group(0) + "\nEnd;")
    ns = _ns(extra={"BuildErrorResponse": lambda r, c, msg: replies.append(
        (c, msg)) or "ERR"})
    build(_lib(), ["Model3DExtMayLoad"], ns)
    build({"Snip": snip}, ["Snip"], ns)
    for good in ("m.step", "M.STP", "m.x_t", "m.X_B", "part.SLDPRT",
                 "m.wrl", "m.igs", "m.stl"):
        ns["Snip"](good, "r")
        assert replies == [], good
    path = r"C:\Models\body.txt"
    ns["Snip"](path, "r")
    assert replies == [("MODEL_LOAD_FAILED", "Could not load a 3D model from "
                        + path + ". Nothing was removed or added.")]


def _mech_ns(version, **extra):
    ns = _ns(extra=dict({"AltiumMajorVersion": lambda d: version}, **extra))
    build(_lib(), ["MechKindsUnsupported"], ns)
    return ns


@pytest.mark.parametrize("version,unsupported", [
    (17, True), (18, True), (19, False), (26, False), (-1, False)])
def test_mech_kinds_unsupported_only_on_a_known_old_build(version, unsupported):
    assert _mech_ns(version)["MechKindsUnsupported"](0) is unsupported


class _Board:
    def __init__(self):
        self.reads = 0

    @property
    def MasterLayerStack(self):
        self.reads += 1
        raise PascalError("Undeclared identifier: MasterLayerStack")


class _LayerObj:
    def __init__(self):
        self.writes = []

    @property
    def Kind(self):
        return 0

    @Kind.setter
    def Kind(self, v):
        self.writes.append(v)
        raise PascalError("Undeclared identifier: Kind")


@pytest.mark.parametrize("version,asked", [(18, False), (26, True), (-1, True)])
def test_mech_layer_ops_do_not_ask_an_old_build_for_kinds(version, asked):
    body = strip_comments(_lib()["ApplyMechLayerOps"])
    stack = re.search(r"MasterStack := Nil;\s+If Not MechKindsUnsupported\(0\) "
                      r"Then\s+Try MasterStack := Board\.MasterLayerStack;.*?End;",
                      body, re.S)
    assert stack, "Board.MasterLayerStack is read on every build"
    fallback = re.search(r"If \(KindBack <> KindId\) And \(MechObj = Nil\)\s+"
                         r"And \(Not MechKindsUnsupported\(0\)\) Then\s+Begin.*?End;"
                         r"\s+KindBack := ReadMechKind\(LayerObj\);\s+End;",
                         body, re.S)
    assert fallback, "the legacy Kind write runs on every build"
    snip = ("Procedure Snip(Board : Variant; LayerObj : Variant; KindId : Integer);\n"
            "Var\n    MasterStack : Variant;\n    MechObj : Variant;\n"
            "    KindBack : Integer;\nBegin\n" + stack.group(0)
            + "\n    MechObj := Nil;\n    KindBack := -1;\n" + fallback.group(0)
            + "\n    SnipOut := KindBack;\nEnd;")
    board, layer = _Board(), _LayerObj()
    ns = _mech_ns(version, ReadMechKind=lambda o: -1)
    build({"Snip": snip}, ["Snip"], ns)
    ns["Snip"](board, layer, 5)
    assert (board.reads == 1) is asked
    assert (layer.writes == [5]) is asked
    assert ns["SnipOut"] == -1, "'kind did not take' either way"


def test_pair_kind_is_not_asked_of_an_old_build():
    """JoinAndKindPair assigns through SetState_LayerPairKind(i) := k, outside
    the evaluator's subset, so this one is structural: both pair-kind members
    sit under the version gate, and KindBack -1 (the Except outcome) reaches
    the result otherwise."""
    body = strip_comments(_lib()["JoinAndKindPair"])
    m = re.search(r"KindBack := -1;\s+If Not MechKindsUnsupported\(0\) Then\s+"
                  r"Begin\s+Try MechPairs\.SetState_LayerPairKind\(PairIdx\) := "
                  r"PairKind; Except End;\s+Try KindBack := MechPairs\."
                  r"LayerPairKind\(PairIdx\); Except KindBack := -1; End;\s+End;",
                  body)
    assert m, "the pair kind is asked of every build"
    rest = body[m.end():]
    assert "LayerPairKind" not in rest
    assert body.count("LayerPairKind") == 2
