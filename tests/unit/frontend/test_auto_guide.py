from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.frontend.editor.timing.auto_guide import (
    AutoGuideParams,
    apply_auto_guide_candidates,
    candidate_preflight,
    scan_auto_guide_candidates,
)


def _ch(text, ts=None, *, end=False, end_ts=None, todo=False, guide=False):
    return Character(
        char=text,
        timestamps=[] if ts is None else [ts],
        check_count=0 if ts is None else 1,
        is_sentence_end=end,
        sentence_end_ts=end_ts,
        needs_guide=todo,
        is_guide=guide,
    )


def _project(chars):
    project = Project()
    singer_id = project.get_default_singer().id
    for char in chars:
        char.singer_id = singer_id
    project.sentences = [Sentence(singer_id=singer_id, characters=chars)]
    return project


def test_scan_uses_continuous_state_and_sentence_end_boundary():
    project = _project(
        [
            _ch("a", 1000),
            _ch("b", 1500, end=True, end_ts=2000),
            _ch("c", 6001),
            _ch("d", 6500),
        ]
    )
    found = scan_auto_guide_candidates(project, 3000)
    assert [c.target.char for c in found] == ["a", "c"]
    assert found[0].left_ms == 0
    assert found[0].is_first is True
    assert found[1].left_ms == 2000
    assert found[1].gap_ms == 4001


def test_todo_is_forced_and_missing_timestamp_is_not_executable():
    project = _project([_ch("a", 1000), _ch("x", todo=True)])
    found = scan_auto_guide_candidates(project, 30_000)
    assert [c.target.char for c in found] == ["a", "x"]
    todo = found[1]
    assert todo.forced_by_todo is True
    assert candidate_preflight(todo, AutoGuideParams(symbol="●"))["reason"] == "missing_target"


def test_explicit_guides_do_not_trigger_or_shorten_boundary():
    project = _project(
        [
            _ch("a", 1000, end=True, end_ts=2000),
            _ch("●", 3000, guide=True),
            _ch("b", 6001),
        ]
    )
    found = scan_auto_guide_candidates(project, 3000)
    assert [c.target.char for c in found] == ["a", "b"]
    assert found[1].left_ms == 2000
    assert found[1].existing_count == 1


def test_apply_replace_marks_chars_and_clears_todo():
    old = _ch("○", 3000, guide=True)
    target = _ch("歌", 5000, todo=True)
    project = _project([old, target])
    candidate = scan_auto_guide_candidates(project, 999_999)[0]
    assert candidate.target is target

    result = apply_auto_guide_candidates(
        project,
        [(candidate, AutoGuideParams(symbol="●", count=2, duration_ms=1000))],
    )
    chars = project.sentences[0].characters
    assert result == {
        "inserted": 1,
        "inserted_chars": 2,
        "replaced": 1,
        "cleared_todos": 1,
        "skipped": 0,
    }
    assert [c.char for c in chars] == ["●", "●", "歌"]
    assert [c.timestamps for c in chars[:2]] == [[3000], [4000]]
    assert all(c.is_guide for c in chars[:2])
    assert target.needs_guide is False


def test_fixed_interval_unknown_left_is_executable_but_fill_is_not():
    candidate = scan_auto_guide_candidates(_project([_ch("歌", 5000)]), 3000)[0]
    candidate.is_first = False
    candidate.left_ms = None
    fixed = candidate_preflight(candidate, AutoGuideParams(symbol="●"))
    fill = candidate_preflight(candidate, AutoGuideParams(symbol="●", fill_gap=True))
    assert fixed["executable"] is True
    assert fixed["overrun_ms"] is None
    assert fill == {"executable": False, "reason": "missing_left"}
