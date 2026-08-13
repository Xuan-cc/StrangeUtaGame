"""离开打轴页面时自动暂停的回归测试。"""

from types import SimpleNamespace

from strange_uta_game.frontend.main_window import MainWindow
from strange_uta_game.frontend.settings.app_settings import AppSettings


class _Editor:
    def __init__(self):
        self.pause_count = 0
        self.sync_count = 0

    def _on_pause(self):
        self.pause_count += 1

    def sync_playback_mode(self):
        self.sync_count += 1


class _TimingService:
    def __init__(self, playing: bool):
        self.playing = playing

    def is_playing(self) -> bool:
        return self.playing


def _window(*, enabled: bool = True, playing: bool = True):
    editor = _Editor()
    window = SimpleNamespace(
        editorInterface=editor,
        _current_interface=editor,
        _timing_service=_TimingService(playing),
        _pause_on_leave_timing_enabled=lambda: enabled,
    )
    return window, editor


def test_pause_on_leave_is_disabled_by_default():
    assert AppSettings.DEFAULT_SETTINGS["audio"]["pause_on_leave_timing"] is False


def test_leaving_timing_pauses_playing_audio():
    window, editor = _window()

    MainWindow._pause_playback_when_leaving(window, object())

    assert editor.pause_count == 1


def test_disabled_setting_does_not_pause():
    window, editor = _window(enabled=False)

    MainWindow._pause_playback_when_leaving(window, object())

    assert editor.pause_count == 0


def test_same_page_or_already_paused_does_not_pause():
    window, editor = _window()
    MainWindow._pause_playback_when_leaving(window, editor)

    window._timing_service.playing = False
    MainWindow._pause_playback_when_leaving(window, object())

    assert editor.pause_count == 0


def test_host_hide_pauses_and_syncs_embedded_timing_page():
    window, editor = _window()

    MainWindow.on_host_visibility_changed(window, False)

    assert editor.pause_count == 1
    assert editor.sync_count == 1


def test_host_visibility_syncs_stale_mode_when_audio_is_already_paused():
    window, editor = _window(playing=False)

    MainWindow.on_host_visibility_changed(window, False)
    MainWindow.on_host_visibility_changed(window, True)

    assert editor.pause_count == 0
    assert editor.sync_count == 2


def test_host_visibility_notifications_are_idempotent():
    window, editor = _window()

    def pause():
        editor.pause_count += 1
        window._timing_service.playing = False

    editor._on_pause = pause
    MainWindow.on_host_visibility_changed(window, False)
    MainWindow.on_host_visibility_changed(window, False)

    assert editor.pause_count == 1
    assert editor.sync_count == 2


def test_host_hide_honors_pause_setting_but_still_syncs_mode():
    window, editor = _window(enabled=False)

    MainWindow.on_host_visibility_changed(window, False)

    assert editor.pause_count == 0
    assert editor.sync_count == 1


def test_host_hide_does_not_pause_when_sug_is_on_another_internal_page():
    window, editor = _window()
    window._current_interface = object()

    MainWindow.on_host_visibility_changed(window, False)

    assert editor.pause_count == 0
    assert editor.sync_count == 1
