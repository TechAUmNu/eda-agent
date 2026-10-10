# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 George Saliba <george.saliba@salitronic.com>
"""A shown created parameter stacks under the Comment, one row at a time.

Field result, 2026-10-10, bridge 2026.10.08.01, RELEASE_VERIFICATION 36.7
on a scratch sheet, Altium Designer 26.10.1.5. ``sch_set_components_
parameters(visible=True)`` creates the missing parameters shown, one 100
mil row per parameter already shown, away from the Comment label. Two
things were wrong on the live sheet:

(a) THE FIRST ROW LANDED ONE ROW TOO FAR. ``SetCompParamTextEx`` counts the
    visible parameters its eParameter iterator returns, and its comment
    said Designator and Comment "are sub-objects, not iterator children".
    For the Comment that is false: ``obj_query`` lists ``Name=Comment`` as
    an eParameter on every part. So the label the rows are measured FROM
    was counted as a row, and every new parameter went one row further
    out than it should. Comment and Designator are now skipped by name.

(b) ON A PART TURNED 90 THE ROWS RAN ACROSS THE COMMENT. The part was at
    Orientation 1 with its Comment label horizontal (Orientation 0). The
    new parameters took the PART's orientation, so their text ran
    vertically, and stepped along X, so it ran over the Comment text. They
    now take the COMMENT's orientation and step perpendicular to its text
    (Comment 0/2 -> step -Y, 1/3 -> step +X), so they stack like the
    Comment does.

A hidden created parameter (the default) is not touched by either change.

The Pascal cannot run here. The counting condition is lifted out of the
comment-free source and EVALUATED on the iterator contents the live sheet
had, and the placement branch is read for the orientation it uses.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.pascal_source import load

_ALTIUM = Path(__file__).resolve().parents[1] / "scripts" / "altium"


def _ex() -> str:
    return load(_ALTIUM / "Generic.pas")["SetCompParamTextEx"]


def _count_condition() -> str:
    """The boolean expression guarding ``Inc(ShownCount)``, as Pascal."""
    search = _ex()[:_ex().index("If Found <> Nil Then")]
    m = re.search(r"If\s+((?:(?!Then).)*?)\s+Then\s+Inc\(ShownCount\);",
                  search, re.S)
    assert m, "no guarded Inc(ShownCount) in the parameter search"
    return m.group(1)


def _as_python(cond: str):
    """Translate the guard into a Python predicate over (name, hidden).

    Only the vocabulary the guard may use is translated; anything else
    left over makes eval fail loudly rather than pass on a misreading.
    """
    py = cond
    py = py.replace("UpperCase(Param.Name)", "name.upper()")
    py = py.replace("Param.IsHidden", "hidden")
    py = re.sub(r"\bNot\b", "not", py)
    py = re.sub(r"\bAnd\b", "and", py)
    py = re.sub(r"\bOr\b", "or", py)
    py = py.replace("<>", "!=")
    py = re.sub(r"(?<![!<>=])=(?!=)", "==", py)
    py = py.replace("'", '"')
    py = " ".join(py.split())
    assert "Param" not in py, f"untranslated term in the guard: {py!r}"
    return lambda name, hidden: bool(
        eval(py, {"__builtins__": {}}, {"name": name, "hidden": hidden}))


def _shown_count(iterated: list[tuple[str, bool]]) -> int:
    counts = _as_python(_count_condition())
    return sum(1 for name, hidden in iterated if counts(name, hidden))


# ----------------------------------------------------------------- (a) rows

def test_the_comment_label_is_not_counted_as_a_row():
    """What the iterator returned on the live part: the visible Comment
    beside one hidden parameter. Nothing that a new row could overlap is
    shown, so the first new row is row 1, directly under the Comment. On
    2026.10.08.01 this counted 1 and the row landed at 2.
    """
    live_part = [("Comment", False), ("Manufacturer", True)]
    assert _shown_count(live_part) == 0


def test_the_skip_is_by_name_whatever_the_case():
    assert _shown_count([("COMMENT", False), ("comment", False),
                         ("designator", False)]) == 0


def test_other_shown_parameters_still_push_the_row_out():
    """The offset exists for these: two shown parameters must not overlap."""
    part = [("Comment", False), ("Value", False), ("MPN", False),
            ("Supplier", True)]
    assert _shown_count(part) == 2


def test_the_comment_is_documented_as_an_iterator_child():
    """The comment that led to the bug must not survive the fix."""
    text = (_ALTIUM / "Generic.pas").read_text(encoding="utf-8")
    head = text[:text.index("Function SetCompParamTextEx(")]
    head = head[head.rindex("{ Set (or create) a component parameter."):]
    assert "Designator and Comment are sub-objects, not iterator children" \
        not in head
    assert "36.7" in head


# ---------------------------------------------------------- (b) orientation

def _shown_block() -> str:
    ex = _ex()
    start = ex.index("If Found <> Nil Then")
    create = ex[ex.index("Else", start):]
    show = create.index("If ShowNew Then")
    return create[show:create.index("Comp.AddSchObject(NewParam)")]


def test_a_shown_row_takes_the_comment_orientation():
    """The last thing Ori is read from, before the step and the
    assignment, is the Comment label. The part's orientation may stay as
    the fallback for a part whose Comment cannot be read, but only BEFORE
    the Comment read, so the Comment wins whenever it answers."""
    shown = _shown_block()
    comment_read = "Ori := Comp.Comment.Orientation;"
    assert comment_read in shown, (
        "the shown row must turn with the Comment label, not the part")
    at = shown.index(comment_read)
    later = shown[at + len(comment_read):]
    assert "Ori := " not in later, "Ori is overwritten after the Comment read"
    if "Ori := Comp.Orientation;" in shown:
        assert shown.index("Ori := Comp.Orientation;") < at
    assert later.index("If (Ori = 1) Or (Ori = 3) Then") < \
        later.index("NewParam.Orientation := Ori;")


def test_rows_step_perpendicular_to_the_comment_text():
    """Comment 1/3 (vertical text): columns beside it, +X. Comment 0/2
    (horizontal text): rows below it, -Y. Measured from the Comment's own
    location, one 100 mil step per row."""
    shown = _shown_block()
    assert "CompLoc := Comp.Comment.Location;" in shown
    assert re.search(
        r"If \(Ori = 1\) Or \(Ori = 3\) Then\s*"
        r"CompLoc\.X := CompLoc\.X \+ MilsToCoord\(100 \* \(ShownCount \+ 1\)\)"
        r"\s*Else\s*"
        r"CompLoc\.Y := CompLoc\.Y - MilsToCoord\(100 \* \(ShownCount \+ 1\)\);",
        shown)


def test_the_field_case_now_stacks_under_the_comment():
    """Part at Orientation 1, Comment at Orientation 0, located (1000,
    2000) mils, nothing else shown. The rows must go to (1000, 1900),
    (1000, 1800): under the Comment, horizontal. The 2026.10.08.01 build
    put the first one at (1200, 2000), vertical, across the Comment text.
    """
    shown = _shown_block()
    part_ori, comment_ori = 1, 0
    ori = comment_ori if "Ori := Comp.Comment.Orientation;" in shown \
        else part_ori
    rows = []
    shown_count = _shown_count([("Comment", False)])
    for _ in range(2):
        x, y = 1000, 2000
        if ori in (1, 3):
            x += 100 * (shown_count + 1)
        else:
            y -= 100 * (shown_count + 1)
        rows.append((x, y, ori))
        shown_count += 1          # the new row is itself shown now
    assert rows == [(1000, 1900, 0), (1000, 1800, 0)]


def test_a_hidden_created_parameter_is_untouched():
    ex = _ex()
    start = ex.index("If Found <> Nil Then")
    create = ex[ex.index("Else", start):]
    hidden = create[:create.index("If ShowNew Then")]
    assert "Orientation" not in hidden and "Comment" not in hidden
    assert "NewParam.IsHidden := Not ShowNew;" in hidden
