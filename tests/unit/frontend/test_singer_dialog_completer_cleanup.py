from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from strange_uta_game.frontend.singer.singer_interface import (
    BatchGroupDialog,
    SingerEditDialog,
)


def _exercise_pending_completer_timer(dialog, combo, qapp):
    combo.setText("ro")
    combo.textEdited.emit("ro")

    dialog.done(QDialog.DialogCode.Rejected)
    QTest.qWait(80)
    qapp.processEvents()

    assert combo.completer() is None
    menu = getattr(combo, "_completerMenu", None)
    assert menu is None or not menu.isVisible()


def test_singer_edit_dialog_cancels_pending_completer_on_close(qapp):
    dialog = SingerEditDialog(existing_groups=["rock"])
    _exercise_pending_completer_timer(dialog, dialog.combo_group, qapp)


def test_batch_group_dialog_cancels_pending_completer_on_close(qapp):
    dialog = BatchGroupDialog(["rock"])
    _exercise_pending_completer_timer(dialog, dialog.combo, qapp)
