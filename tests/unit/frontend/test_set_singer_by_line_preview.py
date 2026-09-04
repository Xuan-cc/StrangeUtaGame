from types import SimpleNamespace

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QSignalSpy, QTest

from strange_uta_game.backend.domain import Character, Sentence, Singer
from strange_uta_game.frontend.editor.timing.dialogs import SetSingerByLineDialog
from strange_uta_game.frontend.editor.timing_interface import EditorInterface


def _sentence(text: str, singer_id: str, timestamp: int | None = None) -> Sentence:
    characters = [Character(char=char, singer_id=singer_id) for char in text]
    if characters and timestamp is not None:
        characters[0].check_count = 1
        characters[0].timestamps = [timestamp]
        characters[0]._update_offset_timestamps()
    return Sentence(singer_id=singer_id, characters=characters)


def test_clicking_lyric_content_requests_line_preview(qapp):
    singer = Singer(id="singer", name="Singer")
    dialog = SetSingerByLineDialog([_sentence("歌词", singer.id)], [singer])
    dialog.show()
    qapp.processEvents()
    spy = QSignalSpy(dialog.preview_line_requested)

    item = dialog.table.item(0, 2)
    QTest.mouseClick(
        dialog.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=dialog.table.visualItemRect(item).center(),
    )

    assert len(spy) == 1
    assert spy[0] == [0]
    assert dialog._get_checkbox(0).isChecked()
    dialog.close()


def test_clicking_non_lyric_column_does_not_request_preview(qapp):
    singer = Singer(id="singer", name="Singer")
    dialog = SetSingerByLineDialog([_sentence("歌词", singer.id)], [singer])
    dialog.show()
    qapp.processEvents()
    spy = QSignalSpy(dialog.preview_line_requested)

    item = dialog.table.item(0, 1)
    QTest.mouseClick(
        dialog.table.viewport(),
        Qt.MouseButton.LeftButton,
        pos=dialog.table.visualItemRect(item).center(),
    )

    assert len(spy) == 0
    dialog.close()


def test_preview_singer_line_seeks_to_line_start_and_starts_playback():
    sentence = _sentence("歌词", "singer", 2345)
    calls = []
    timing_service = SimpleNamespace(is_playing=lambda: False)
    preview = SimpleNamespace(
        set_current_position=lambda line, char: calls.append(("position", line, char)),
        set_focus_position=lambda line, char: calls.append(("focus", line, char)),
    )
    editor = SimpleNamespace(
        _project=SimpleNamespace(sentences=[sentence]),
        _timing_service=timing_service,
        _current_line_idx=-1,
        preview=preview,
        _update_line_info=lambda: calls.append(("info",)),
        _on_seek=lambda ms: calls.append(("seek", ms)),
        _on_play=lambda: calls.append(("play",)),
    )

    EditorInterface._on_preview_singer_line(editor, 0)

    assert editor._current_line_idx == 0
    assert calls == [
        ("position", 0, 0),
        ("focus", 0, 0),
        ("info",),
        ("seek", 2345),
        ("play",),
    ]


def test_preview_singer_line_without_timestamp_does_nothing():
    calls = []
    editor = SimpleNamespace(
        _project=SimpleNamespace(sentences=[_sentence("歌词", "singer")]),
        _timing_service=SimpleNamespace(is_playing=lambda: False),
        _current_line_idx=7,
        _on_seek=lambda ms: calls.append(ms),
    )

    EditorInterface._on_preview_singer_line(editor, 0)

    assert editor._current_line_idx == 7
    assert calls == []
