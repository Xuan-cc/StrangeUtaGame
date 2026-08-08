"""Application-wide policy for ordinary, non-modal popup windows."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, Qt
from PyQt6.QtWidgets import QApplication, QDialog


class _NonModalDialogFilter(QObject):
    """Remove modality just before Qt shows any dialog.

    ``QDialog.exec()`` temporarily applies application modality even when the
    dialog was configured as non-modal. Clearing it on the Show event keeps
    the existing synchronous result API while leaving every other window
    usable. This also covers Qt-owned dialogs such as ``QFileDialog`` and
    ``QInputDialog``.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Show and isinstance(watched, QDialog):
            watched.setWindowModality(Qt.WindowModality.NonModal)
            watched.setModal(False)
        return False


def install_non_modal_dialog_policy(app: QApplication) -> None:
    """Make all dialogs ordinary windows without changing their result API."""
    if getattr(app, "_sug_non_modal_dialog_filter", None) is not None:
        return

    event_filter = _NonModalDialogFilter(app)
    app.installEventFilter(event_filter)
    # Keep a Python reference for the lifetime of the application.
    app._sug_non_modal_dialog_filter = event_filter  # type: ignore[attr-defined]
