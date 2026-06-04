from __future__ import annotations

from types import SimpleNamespace

import pytest

import krok_helper.lyrics_timing  # noqa: F401 - installs bundled SUG src path
from krok_helper.gui_qt import KrokHelperQtApp, WORKFLOW_LYRICS_TIMING
from krok_helper.lyrics import (
    LYRICS_LANGUAGE_TRANSLATION,
    LYRICS_PREVIEW_LINE,
    LyricsSearchCandidate,
)


class _FakeTimingPage:
    def __init__(self) -> None:
        self.imported_text = ""

    def import_lyrics_from_text(self, content: str) -> bool:
        self.imported_text = content
        return True


class _FakeFileLoader:
    def __init__(self) -> None:
        self.loaded_text = ""

    def load_lyrics_from_text(self, content: str) -> None:
        self.loaded_text = content


def test_import_current_lyrics_to_timing_uses_filtered_preview() -> None:
    candidate = LyricsSearchCandidate(
        provider_id="qm",
        provider_name="QQ音乐",
        track_id="1",
        title="Song",
        artist="Artist",
        album="Album",
        duration_seconds=None,
        line_lyrics="[00:01.00]original",
        translation_lyrics="[00:01.00]translated",
        lyrics_loaded=True,
    )
    timing_page = _FakeTimingPage()
    shown_modules: list[str] = []
    app = SimpleNamespace(
        lyrics_selected_candidate=candidate,
        lyrics_strip_intro_checkbox=SimpleNamespace(isChecked=lambda: True),
        lyrics_timing_page=timing_page,
        _current_lyrics_preview_mode=lambda: LYRICS_PREVIEW_LINE,
        _current_lyrics_language=lambda: LYRICS_LANGUAGE_TRANSLATION,
        _refresh_lyrics_import_button=lambda _preview: None,
        _show_module=shown_modules.append,
    )
    app._build_current_lyrics_preview = lambda selected: KrokHelperQtApp._build_current_lyrics_preview(app, selected)

    KrokHelperQtApp._import_current_lyrics_to_timing(app)

    assert timing_page.imported_text == "[00:01.00]translated"
    assert shown_modules == [WORKFLOW_LYRICS_TIMING]


def test_sug_public_import_api_routes_text_to_editor_loader() -> None:
    pytest.importorskip("numpy")
    from strange_uta_game.frontend.main_window import MainWindow as LyricsTimingMainWindow

    loader = _FakeFileLoader()
    editor = SimpleNamespace(_file_loader=loader)
    switched_to: list[object] = []
    window = SimpleNamespace(
        editorInterface=editor,
        switchTo=switched_to.append,
    )

    imported = LyricsTimingMainWindow.import_lyrics_from_text(window, "hello\nworld")

    assert imported is True
    assert loader.loaded_text == "hello\nworld"
    assert switched_to == [editor]
