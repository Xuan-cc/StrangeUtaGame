from strange_uta_game.frontend.editor.timing_interface import EditorInterface


def test_ui_refresh_fps_accepts_supported_values():
    assert EditorInterface._normalize_ui_refresh_fps(30) == 30
    assert EditorInterface._normalize_ui_refresh_fps("30") == 30
    assert EditorInterface._normalize_ui_refresh_fps(60) == 60


def test_ui_refresh_fps_falls_back_to_safe_values():
    assert EditorInterface._normalize_ui_refresh_fps(None) == 60
    assert EditorInterface._normalize_ui_refresh_fps("invalid") == 60
    assert EditorInterface._normalize_ui_refresh_fps(15) == 30
    assert EditorInterface._normalize_ui_refresh_fps(45) == 60
