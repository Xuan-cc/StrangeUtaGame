from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay
from strange_uta_game.frontend.editor.timing import timeline_widget as timeline_module
from strange_uta_game.backend.domain import Character, Project, Sentence


class _DummySignal:
    def connect(self, callback):
        pass


class _TimelineThemeProxy:
    changed = _DummySignal()

    def __getattr__(self, name):
        from strange_uta_game.frontend.theme import theme as live_theme
        return getattr(live_theme, name)


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
    monkeypatch.setattr(timeline_module, "theme", _TimelineThemeProxy())
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
    monkeypatch.setattr(timeline_module, "theme", _TimelineThemeProxy())
    display = WaveformDisplay()
    display.resize(640, 120)
    display.set_duration(120_000)
    display.grab()
    old_layer = display._static_layer

    display.set_scroll_position(0.25)
    display.grab()

    assert display._static_layer is not old_layer


def test_cached_waveform_matches_forced_full_render(qapp, monkeypatch):
    monkeypatch.setattr(timeline_module, "theme", _TimelineThemeProxy())
    display = WaveformDisplay()
    display.resize(640, 120)
    display.set_duration(120_000)
    display.set_time_tags([
        (1_000, "春", 0, 0, 0, False, "はる"),
        (2_000, "風", 0, 1, 0, False, "かぜ"),
    ])
    display.show()
    qapp.processEvents()
    display.set_position(1_500)
    qapp.processEvents()
    cached = display.grab().toImage()

    display._invalidate_static_layer()
    display.update()
    qapp.processEvents()
    rebuilt = display.grab().toImage()

    assert cached == rebuilt


def test_waveform_static_layer_uses_physical_pixels_on_high_dpi(qapp, monkeypatch):
    monkeypatch.setattr(timeline_module, "theme", _TimelineThemeProxy())
    display = WaveformDisplay()
    monkeypatch.setattr(display, "devicePixelRatioF", lambda: 1.5)

    layer = display._render_static_layer(640, 120, 0.0, 1_000.0, 1_000.0)

    assert layer.devicePixelRatio() == 1.5
    assert layer.width() == 960
    assert layer.height() == 180
    assert layer.deviceIndependentSize().width() == 640
    assert layer.deviceIndependentSize().height() == 120


class _Label:
    def __init__(self):
        self.text = ""

    def setText(self, text):
        self.text = text


def _status_editor(project):
    editor = type("StatusEditor", (), {})()
    editor._project = project
    editor._status_line_cache = {}
    editor._status_cache_project_id = None
    editor._status_meaningful_total = 0
    editor._status_timed_total = 0
    editor._status_needs_guide_total = 0
    editor.lbl_progress = _Label()
    editor.lbl_needs_guide = _Label()
    editor.tr = lambda text: text
    editor._status_for_sentence = EditorInterface._status_for_sentence
    editor._rebuild_status_cache = lambda: EditorInterface._rebuild_status_cache(editor)
    editor._status_cache_is_valid = lambda: EditorInterface._status_cache_is_valid(editor)
    editor._render_cached_status = lambda: EditorInterface._render_cached_status(editor)
    return editor


def test_status_update_recomputes_only_changed_line(monkeypatch):
    first = Sentence(singer_id="s", characters=[Character(char="a", check_count=1)])
    second = Sentence(singer_id="s", characters=[Character(char="b", check_count=1)])
    project = Project(sentences=[first, second])
    editor = _status_editor(project)
    EditorInterface._rebuild_status_cache(editor)
    calls = []
    original = EditorInterface._status_for_sentence

    def counted(sentence):
        calls.append(sentence)
        return original(sentence)

    editor._status_for_sentence = counted
    first.characters[0].add_timestamp(100, 0)
    EditorInterface._update_status_line(editor, 0)

    assert calls == [first]
    assert editor._status_timed_total == 1
    assert "1/2" in editor.lbl_progress.text
