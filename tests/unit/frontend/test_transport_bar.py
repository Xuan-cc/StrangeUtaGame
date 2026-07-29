from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from strange_uta_game.frontend.editor.timing.transport_bar import TransportBar


def test_position_update_stays_suspended_during_real_drag(qapp, monkeypatch):
    bar = TransportBar()
    bar.set_duration(10_000)
    bar._on_slider_pressed()
    monkeypatch.setattr(bar, "_left_mouse_button_is_down", lambda: True)

    bar.set_position(5_000)

    assert bar._is_dragging is True
    assert bar.slider_progress.value() == 0


def test_position_update_recovers_from_lost_slider_release(qapp, monkeypatch):
    bar = TransportBar()
    bar.set_duration(10_000)
    bar._on_slider_pressed()
    monkeypatch.setattr(bar, "_left_mouse_button_is_down", lambda: False)

    bar.set_position(5_000)

    assert bar._is_dragging is False
    assert bar.slider_progress.value() == 5_000
    assert bar.lbl_time.text() == "00:05.00 / 00:10.00"
