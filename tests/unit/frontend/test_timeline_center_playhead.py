from __future__ import annotations

import numpy as np

from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay


def _display(qapp) -> WaveformDisplay:
    display = WaveformDisplay()
    display.set_duration(10_000)
    display.set_zoom(10.0)  # 1 秒可见窗
    return display


def test_center_mode_keeps_playhead_at_half_width_during_playback(qapp):
    display = _display(qapp)
    display.set_center_playhead_mode(True)
    display.set_position(100)
    display.set_playing(True)

    for position_ms in (100, 5_000, 9_900):
        display.set_position(position_ms)
        visible_start = display._visible_start_ms()
        x = display._ts_to_x(
            position_ms, visible_start, display._visible_duration_ms(), 1_000
        )
        assert x == 500


def test_center_mode_adds_blank_waveform_padding_before_audio(qapp):
    display = _display(qapp)
    display.set_audio_data(np.ones(1_000, dtype=np.float32), 100, 1)
    display.set_center_playhead_mode(True)
    display.set_position(100)
    display.set_playing(True)

    peaks = display._compute_waveform_peaks(10)

    assert peaks is not None
    assert peaks[:4] == [(0.0, 0.0)] * 4
    assert peaks[4:] == [(1.0, 1.0)] * 6


def test_center_mode_click_coordinates_still_seek_on_the_moving_timeline(qapp):
    display = _display(qapp)
    display.set_center_playhead_mode(True)
    display.set_position(5_000)
    display.set_playing(True)

    assert display._x_to_time(500, width=1_000) == 5_000
    assert display._x_to_time(250, width=1_000) == 4_750
    assert display._x_to_time(750, width=1_000) == 5_250


def test_waveform_peak_level_is_reused_while_scrolling(qapp):
    display = _display(qapp)
    display.set_audio_data(np.linspace(-1, 1, 1_000, dtype=np.float32), 100, 1)
    display.set_center_playhead_mode(True)
    display.set_playing(True)

    display.set_position(2_000)
    display._compute_waveform_peaks(100)
    cached_levels = dict(display._waveform_peak_levels)
    display.set_position(3_000)
    display._compute_waveform_peaks(100)

    assert display._waveform_peak_levels.keys() == cached_levels.keys()
    for bin_size, level in cached_levels.items():
        assert display._waveform_peak_levels[bin_size][0] is level[0]
        assert display._waveform_peak_levels[bin_size][1] is level[1]


def test_disabled_center_mode_preserves_regular_scroll_behavior(qapp):
    display = _display(qapp)
    display.set_scroll_position(0.25)
    display.set_position(3_000)
    display.set_playing(True)

    assert display._visible_start_ms() == 2_500
