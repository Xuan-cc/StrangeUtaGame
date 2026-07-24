from types import SimpleNamespace

from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.frontend.editor.timing.dialogs import (
    execute_auto_interlude_guide,
)
from strange_uta_game.frontend.editor.timing_interface import EditorInterface


def _timed_project(*, stored_duration_ms: int = 0) -> Project:
    project = Project(audio_duration_ms=stored_duration_ms)
    singer_id = project.get_default_singer().id
    last_char = Character(
        char="歌",
        singer_id=singer_id,
        timestamps=[90_000],
        is_sentence_end=True,
        is_line_end=True,
        sentence_end_ts=100_000,
    )
    project.sentences = [
        Sentence(singer_id=singer_id, characters=[last_char]),
    ]
    return project


def _generate(project: Project, *, live_duration_ms=None) -> dict:
    return execute_auto_interlude_guide(
        project,
        min_guide_time_s=5,
        format_str="{position}{time}",
        position_mappings={
            "0": {"enabled": False, "text": "前奏"},
            "1": {"enabled": False, "text": "间奏"},
            "2": {"enabled": True, "text": "后奏"},
        },
        allow_inline=False,
        new_line=True,
        front_margin_ms=150,
        back_margin_ms=150,
        audio_duration_ms=live_duration_ms,
    )


def test_outro_uses_live_engine_duration_when_project_duration_is_zero():
    project = _timed_project(stored_duration_ms=0)

    result = _generate(project, live_duration_ms=120_000)

    assert result["inserted"] == 1
    assert len(project.sentences) == 2
    assert project.sentences[1].text == "后奏20"


def test_outro_live_duration_overrides_stale_project_duration():
    project = _timed_project(stored_duration_ms=102_000)

    result = _generate(project, live_duration_ms=120_000)

    assert result["inserted"] == 1
    assert project.sentences[1].text == "后奏20"


def test_outro_falls_back_to_saved_duration_without_loaded_media():
    project = _timed_project(stored_duration_ms=120_000)

    result = _generate(project)

    assert result["inserted"] == 1
    assert project.sentences[1].text == "后奏20"


class _FakeStore:
    def __init__(self):
        self.mark_dirty_calls = 0

    def mark_dirty(self):
        self.mark_dirty_calls += 1


def test_sync_project_audio_duration_updates_and_marks_project_dirty():
    store = _FakeStore()
    editor = SimpleNamespace(
        _project=Project(audio_duration_ms=0),
        _store=store,
    )

    changed = EditorInterface._sync_project_audio_duration(editor, 123_456)

    assert changed is True
    assert editor._project.audio_duration_ms == 123_456
    assert store.mark_dirty_calls == 1


def test_sync_project_audio_duration_ignores_invalid_or_unchanged_duration():
    store = _FakeStore()
    editor = SimpleNamespace(
        _project=Project(audio_duration_ms=123_456),
        _store=store,
    )

    assert EditorInterface._sync_project_audio_duration(editor, 0) is False
    assert EditorInterface._sync_project_audio_duration(editor, 123_456) is False
    assert editor._project.audio_duration_ms == 123_456
    assert store.mark_dirty_calls == 0
