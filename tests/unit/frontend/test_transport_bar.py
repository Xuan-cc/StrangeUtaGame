from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QWidget

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


def test_playback_range_is_forwarded_to_progress_slider(qapp):
    bar = TransportBar()
    bar.set_duration(10_000)

    bar.set_playback_range(2_500, 7_500)

    assert bar._range_start_ms == 2_500
    assert bar._range_end_ms == 7_500
    assert bar.slider_progress._range_start_value == 2_500
    assert bar.slider_progress._range_end_value == 7_500


def test_speed_label_commits_multiplier_and_returns_to_display_mode(qapp):
    bar = TransportBar()
    received = []
    bar.speed_changed.connect(received.append)

    bar.lbl_speed_value.begin_edit()
    assert bar.lbl_speed_value.editor.text() == "100"
    assert bar.lbl_speed_value.text() == ""
    assert bar.lbl_speed_value.suffix_label.text() == "%"
    assert not bar.lbl_speed_value.suffix_label.isHidden()
    bar.lbl_speed_value.editor.setText("75")
    bar.lbl_speed_value.editor.editingFinished.emit()

    assert bar.lbl_speed_value.is_editing() is False
    assert bar.lbl_speed_value.editor.isHidden()
    assert bar.slider_speed.value() == 75
    assert bar.lbl_speed_value.text() == "0.75x"
    assert received == [0.75]


def test_speed_label_invalid_value_restores_current_display(qapp):
    bar = TransportBar()
    bar.set_speed_value(80, emit_signal=False)

    bar.lbl_speed_value.begin_edit()
    bar.lbl_speed_value.editor.setText("not a speed")
    bar.lbl_speed_value.editor.returnPressed.emit()

    assert bar.slider_speed.value() == 80
    assert bar.lbl_speed_value.text() == "0.80x"


def test_speed_label_double_clicks_to_edit_and_commits_on_focus_loss(qapp, qtbot):
    bar = TransportBar()
    bar.show()

    qtbot.mouseDClick(bar.lbl_speed_value, Qt.MouseButton.LeftButton)
    assert bar.lbl_speed_value.is_editing() is True
    assert bar.lbl_speed_value.editor.isVisible()

    bar.lbl_speed_value.editor.setText("65")
    qapp.sendEvent(
        bar.lbl_speed_value.editor,
        QFocusEvent(QEvent.Type.FocusOut),
    )

    assert bar.lbl_speed_value.is_editing() is False
    assert bar.slider_speed.value() == 65
    assert bar.lbl_speed_value.text() == "0.65x"


def test_speed_label_enter_is_consumed_before_reaching_parent(qapp, qtbot):
    class KeyCatchingParent(QWidget):
        def __init__(self):
            super().__init__()
            self.return_presses = 0

        def keyPressEvent(self, event):  # noqa: N802
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.return_presses += 1
            super().keyPressEvent(event)

    parent = KeyCatchingParent()
    bar = TransportBar(parent)
    parent.show()
    bar.show()
    bar.lbl_speed_value.begin_edit()
    bar.lbl_speed_value.editor.setText("70")

    qtbot.keyPress(bar.lbl_speed_value.editor, Qt.Key.Key_Return)

    assert parent.return_presses == 0
    assert bar.lbl_speed_value.is_editing() is False
    assert bar.slider_speed.value() == 70
