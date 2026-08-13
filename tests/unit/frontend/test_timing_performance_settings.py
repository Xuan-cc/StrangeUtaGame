from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay


def test_ui_refresh_fps_accepts_supported_values():
    assert EditorInterface._normalize_ui_refresh_fps(30) == 30
    assert EditorInterface._normalize_ui_refresh_fps("30") == 30
    assert EditorInterface._normalize_ui_refresh_fps(60) == 60


def test_ui_refresh_fps_falls_back_to_safe_values():
    assert EditorInterface._normalize_ui_refresh_fps(None) == 60
    assert EditorInterface._normalize_ui_refresh_fps("invalid") == 60
    assert EditorInterface._normalize_ui_refresh_fps(15) == 30
    assert EditorInterface._normalize_ui_refresh_fps(45) == 60


def test_waveform_static_layer_is_reused_for_playhead_updates(qapp, monkeypatch):
    display = WaveformDisplay()
    display.resize(640, 120)
    display.set_duration(120_000)
    display.show()
    qapp.processEvents()
    calls = 0
    original = display._render_static_layer

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(display, "_render_static_layer", counted)
    display._invalidate_static_layer()
    display.grab()
    display.set_position(1_000)
    display.grab()

    assert calls == 1


def test_waveform_static_layer_rebuilds_after_scroll(qapp, monkeypatch):
    display = WaveformDisplay()
    display.resize(640, 120)
    display.set_duration(120_000)
    display.grab()
    old_layer = display._static_layer

    display.set_scroll_position(0.25)
    display.grab()

    assert display._static_layer is not old_layer
