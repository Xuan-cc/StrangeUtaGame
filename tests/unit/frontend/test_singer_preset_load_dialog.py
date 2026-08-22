from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest

from strange_uta_game.frontend.singer.singer_interface import (
    SingerPresetLoadDialog,
)


def _show_dialog(dialog, qapp):
    dialog.show()
    qapp.processEvents()
    QTest.qWait(10)


def test_clicking_preset_row_toggles_check_state(qapp):
    dialog = SingerPresetLoadDialog([{"name": "Singer A"}], set())
    _show_dialog(dialog, qapp)

    item = dialog.list_widget.item(0)
    row_point = dialog.list_widget.visualItemRect(item).center()

    QTest.mouseClick(dialog.list_widget.viewport(), Qt.MouseButton.LeftButton, pos=row_point)
    assert item.checkState() == Qt.CheckState.Checked

    QTest.mouseClick(dialog.list_widget.viewport(), Qt.MouseButton.LeftButton, pos=row_point)
    assert item.checkState() == Qt.CheckState.Unchecked

    dialog.close()


def test_clicking_checkbox_toggles_only_once(qapp):
    dialog = SingerPresetLoadDialog([{"name": "Singer A"}], set())
    _show_dialog(dialog, qapp)

    item = dialog.list_widget.item(0)
    checkbox_point = dialog.list_widget._check_indicator_rect(item).center()

    QTest.mouseClick(
        dialog.list_widget.viewport(),
        Qt.MouseButton.LeftButton,
        pos=checkbox_point,
    )
    assert item.checkState() == Qt.CheckState.Checked

    dialog.close()


def test_existing_preset_row_remains_disabled(qapp):
    dialog = SingerPresetLoadDialog([{"name": "Singer A"}], {"Singer A"})
    _show_dialog(dialog, qapp)

    item = dialog.list_widget.item(0)
    row_point = dialog.list_widget.visualItemRect(item).center()
    QTest.mouseClick(dialog.list_widget.viewport(), Qt.MouseButton.LeftButton, pos=row_point)

    assert item.checkState() == Qt.CheckState.Unchecked
    dialog.close()
