from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QWidget

from strange_uta_game.frontend.dialog_policy import install_non_modal_dialog_policy


def test_exec_dialog_does_not_become_application_modal(qapp):
    install_non_modal_dialog_policy(qapp)
    parent = QWidget()
    parent.show()
    dialog = QDialog(parent)
    observed = {}

    def inspect_and_close():
        observed["modality"] = dialog.windowModality()
        observed["is_modal"] = dialog.isModal()
        observed["active_modal"] = qapp.activeModalWidget()
        observed["parent_enabled"] = parent.isEnabled()
        dialog.accept()

    QTimer.singleShot(0, inspect_and_close)
    result = dialog.exec()

    assert result == QDialog.DialogCode.Accepted
    assert observed == {
        "modality": Qt.WindowModality.NonModal,
        "is_modal": False,
        "active_modal": None,
        "parent_enabled": True,
    }


def test_install_policy_is_idempotent(qapp):
    install_non_modal_dialog_policy(qapp)
    first_filter = qapp._sug_non_modal_dialog_filter
    install_non_modal_dialog_policy(qapp)
    assert qapp._sug_non_modal_dialog_filter is first_filter
