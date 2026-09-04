"""Caption label that temporarily becomes an editor on double-click."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent, QResizeEvent
from PyQt6.QtWidgets import QLineEdit
from qfluentwidgets import CaptionLabel


class InlineEditableCaptionLabel(CaptionLabel):
    """A compact value label with an in-place, commit-on-finish editor."""

    value_committed = pyqtSignal(str)

    def __init__(
        self,
        text: str = "",
        parent=None,
        *,
        edit_scale: float = 1.0,
        edit_suffix: str = "",
    ):
        # CaptionLabel's text overload calls ``self.__init__(parent)`` and is
        # therefore unsafe for subclasses with a different constructor.
        super().__init__(parent)
        self.setText(text)
        self._editing = False
        self._display_text_before_edit = text
        self._edit_scale = edit_scale
        self._edit_suffix = edit_suffix
        self.editor = QLineEdit(self)
        self.editor.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.editor.hide()
        self.editor.installEventFilter(self)
        self.editor.returnPressed.connect(self._finish_edit)
        self.editor.editingFinished.connect(self._finish_edit)
        self.suffix_label = CaptionLabel(edit_suffix, self)
        self.suffix_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.suffix_label.setAttribute(
            Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        self.suffix_label.hide()

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        # Consume Enter before QLineEdit emits returnPressed.  _finish_edit()
        # hides the editor, so allowing the same event to continue would make
        # it bubble to TimingInterface after focus has left QLineEdit, where it
        # is interpreted as "insert line break".
        if (
            obj is self.editor
            and event.type() == QEvent.Type.KeyPress
            and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
        ):
            event.accept()
            self._finish_edit()
            return True
        return super().eventFilter(obj, event)

    def mouseDoubleClickEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is None or event.button() != Qt.MouseButton.LeftButton or not self.isEnabled():
            super().mouseDoubleClickEvent(event)
            return
        self.begin_edit()
        event.accept()

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802
        super().resizeEvent(event)
        if hasattr(self, "editor"):
            self._layout_editor()

    def _layout_editor(self) -> None:
        suffix_width = 0
        if self._edit_suffix:
            suffix_width = self.suffix_label.fontMetrics().horizontalAdvance(
                self._edit_suffix
            ) + 6
        self.editor.setGeometry(
            0,
            0,
            max(1, self.width() - suffix_width),
            self.height(),
        )
        self.suffix_label.setGeometry(
            self.width() - suffix_width,
            0,
            suffix_width,
            self.height(),
        )

    def begin_edit(self) -> None:
        """Show the editor with the displayed multiplier selected."""
        if self._editing or not self.isEnabled():
            return
        self._editing = True
        text = self.text().strip()
        self._display_text_before_edit = self.text()
        if text.lower().endswith("x"):
            text = text[:-1]
        if self._edit_scale != 1.0:
            try:
                scaled_text = f"{float(text) * self._edit_scale:g}"
            except ValueError:
                scaled_text = None
            if scaled_text is not None:
                text = scaled_text
        # The editor and suffix occupy separate rectangles.  Clear the label
        # underneath so its trailing "x" cannot show through between them.
        self.setText("")
        self.editor.setText(text)
        self._layout_editor()
        self.editor.show()
        self.editor.raise_()
        if self._edit_suffix:
            self.suffix_label.show()
            self.suffix_label.raise_()
        self.editor.setFocus(Qt.FocusReason.MouseFocusReason)
        self.editor.selectAll()

    def _finish_edit(self) -> None:
        """Leave edit mode and submit once for either Enter or focus loss."""
        if not self._editing:
            return
        value = self.editor.text()
        self._editing = False
        self.editor.hide()
        self.suffix_label.hide()
        self.setText(self._display_text_before_edit)
        self.value_committed.emit(value + self._edit_suffix)

    def is_editing(self) -> bool:
        return self._editing
