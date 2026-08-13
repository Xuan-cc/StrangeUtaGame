"""Compact in-place editor used by the F2 ruby shortcut."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog, QHBoxLayout
from qfluentwidgets import LineEdit, PushButton

from strange_uta_game.backend.domain import Character

from . import dialogs as ruby_dialogs


class RubyEditPopup(QDialog):
    """Edit one character's ruby and its link to the following character."""

    _INPUT_MIN_WIDTH = 92
    _INPUT_MAX_WIDTH = 420
    _LINK_BUTTON_MIN_WIDTH = 52

    def __init__(
        self,
        character: Character,
        can_link_next: bool,
        parent=None,
        link_toggle_callback: Callable[[], None] | None = None,
    ):
        super().__init__(parent)
        self._character = character
        self._can_link_next = can_link_next
        self._link_toggle_callback = link_toggle_callback
        self._split_mode = ruby_dialogs._get_ruby_split_mode()
        if self._split_mode not in ("direct", "char", "mora"):
            self._split_mode = "mora"
        self._original_ruby = self._ruby_input_text(character, self._split_mode)
        self._original_linked = bool(character.linked_to_next)
        self._linked = self._original_linked and can_link_next
        self._modified = False
        self._cancelled = False
        self._finished = False
        self._anchor: QRect | None = None

        self.setWindowFlags(
            Qt.WindowType.Popup
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.NoDropShadowWindowHint
        )
        self.setObjectName("rubyEditPopup")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self.edit_ruby = LineEdit(self)
        self.edit_ruby.setText(self._original_ruby)
        self.edit_ruby.setPlaceholderText(self.tr("注音"))
        self.edit_ruby.setToolTip(self.tr("回车保存，Esc 取消"))
        self.edit_ruby.setAttribute(
            Qt.WidgetAttribute.WA_InputMethodEnabled, True
        )
        self.edit_ruby.setInputMethodHints(Qt.InputMethodHint.ImhNone)
        self.edit_ruby.setClearButtonEnabled(True)
        self.edit_ruby.setFixedWidth(self._INPUT_MIN_WIDTH)
        self.edit_ruby.returnPressed.connect(self._apply)
        self.edit_ruby.textChanged.connect(self._resize_for_text)
        layout.addWidget(self.edit_ruby)

        self.btn_link_next = PushButton(self)
        self.btn_link_next.setCheckable(True)
        self.btn_link_next.setFixedWidth(self._LINK_BUTTON_MIN_WIDTH)
        self.btn_link_next.setEnabled(can_link_next)
        self.btn_link_next.clicked.connect(self._toggle_link)
        self._sync_link_button()
        layout.addWidget(self.btn_link_next)

        self._resize_for_text(self.edit_ruby.text())

    @staticmethod
    def _ruby_input_text(character: Character, mode: str) -> str:
        if not character.ruby or not character.ruby.parts:
            return ""
        if mode == "direct":
            return ",".join(part.text for part in character.ruby.parts)
        return character.ruby.text

    def _toggle_link(self, checked: bool) -> None:
        if self._link_toggle_callback is not None:
            self._link_toggle_callback()
            self._linked = bool(self._character.linked_to_next)
            self._sync_link_button()
            return
        self._linked = bool(checked) and self._can_link_next
        self._sync_link_button()

    def _sync_link_button(self) -> None:
        self.btn_link_next.setChecked(self._linked)
        self.btn_link_next.setText(self.tr("链接"))
        self._resize_link_button()
        if not self._can_link_next:
            self.btn_link_next.setToolTip(self.tr("行末字符不能链接下一字"))
        else:
            self.btn_link_next.setToolTip(
                self.tr("点击取消与下一个字的链接")
                if self._linked
                else self.tr("点击链接下一个字")
            )

    def _resize_link_button(self) -> None:
        """Fit translated labels such as Japanese ``リンク`` without clipping."""
        text_width = self.btn_link_next.fontMetrics().horizontalAdvance(
            self.btn_link_next.text()
        )
        self.btn_link_next.setFixedWidth(
            max(self._LINK_BUTTON_MIN_WIDTH, text_width + 28)
        )
        self.setFixedSize(self.sizeHint())
        if self._anchor is not None and self.isVisible():
            self._position_above_anchor()

    def _resize_for_text(self, text: str) -> None:
        text_width = self.edit_ruby.fontMetrics().horizontalAdvance(
            text or self.edit_ruby.placeholderText()
        )
        input_width = max(
            self._INPUT_MIN_WIDTH,
            min(self._INPUT_MAX_WIDTH, text_width + 42),
        )
        self.edit_ruby.setFixedWidth(input_width)
        self.setFixedSize(self.sizeHint())
        if self._anchor is not None and self.isVisible():
            self._position_above_anchor()

    def _apply(self) -> None:
        if self._finished or self._cancelled:
            return
        ruby_text = self.edit_ruby.text().strip()
        ruby_changed = ruby_text != self._original_ruby
        split_mode = self._split_mode
        mode_changed = split_mode != "direct" and "," in ruby_text
        if mode_changed:
            split_mode = "direct"
            ruby_dialogs._set_ruby_split_mode(split_mode)
            self._split_mode = split_mode
        link_changed = (
            self._link_toggle_callback is None
            and self._linked != self._original_linked
        )
        if ruby_changed:
            self._character.set_ruby(
                ruby_dialogs.parse_ruby_text(
                    ruby_text,
                    self._character.check_count,
                    mode=split_mode,
                )
            )
        if link_changed:
            self._character.linked_to_next = self._linked
        self._modified = ruby_changed or link_changed or mode_changed
        self._finished = True
        super().accept()

    def reject(self) -> None:
        """Treat popup dismissal (including an outside click) as save."""
        if self._cancelled:
            super().reject()
        else:
            self._apply()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self._cancelled = True
            self._finished = True
            super().reject()
            return
        super().keyPressEvent(event)

    def event(self, event: QEvent) -> bool:
        # A Qt.Popup receives WindowDeactivate when the user clicks elsewhere.
        # Queue the save so Qt can finish dispatching the outside click first.
        if (
            event.type() == QEvent.Type.WindowDeactivate
            and self.isVisible()
            and not self._cancelled
            and not self._finished
        ):
            QTimer.singleShot(0, self._apply)
        return super().event(event)

    def was_modified(self) -> bool:
        return self._modified

    def show_above(self, anchor: QRect) -> int:
        """Run the popup just above a character rectangle in global coordinates."""
        self._anchor = QRect(anchor)
        self.adjustSize()
        self._position_above_anchor()
        # The native popup must exist before Windows can bind an IME context.
        # Refocus on the first event-loop turn so language switching and
        # composition work for Japanese, Chinese, and other input methods.
        QTimer.singleShot(0, self._activate_ruby_input)
        return self.exec()

    def _activate_ruby_input(self) -> None:
        if self._finished:
            return
        self.activateWindow()
        self.edit_ruby.setFocus(Qt.FocusReason.PopupFocusReason)
        self.edit_ruby.selectAll()
        input_method = QApplication.inputMethod()
        input_method.update(
            Qt.InputMethodQuery.ImEnabled | Qt.InputMethodQuery.ImHints
        )

    def _position_above_anchor(self) -> None:
        if self._anchor is None:
            return
        anchor = self._anchor
        width, height = self.width(), self.height()
        x = anchor.center().x() - width // 2
        y = anchor.top() - height - 4

        screen = QApplication.screenAt(anchor.center())
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x = max(available.left(), min(x, available.right() - width + 1))
            if y < available.top():
                y = min(anchor.bottom() + 4, available.bottom() - height + 1)
        self.move(QPoint(x, y))
