"""Draggable live preview used by the interface settings page."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPoint, QRectF, Qt
from PyQt6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent, QPen, QResizeEvent
from PyQt6.QtWidgets import QWidget

from strange_uta_game.backend.domain.entities import Sentence
from strange_uta_game.backend.domain.models import Ruby, RubyPart
from strange_uta_game.backend.domain.project import Project
from strange_uta_game.frontend.editor.timing.karaoke_preview import KaraokePreview
from strange_uta_game.frontend.font_utils import DEFAULT_FONT_FAMILY, ui_font
from strange_uta_game.frontend.theme import theme


def _sample_project() -> Project:
    """Build stable sample data that exercises the real karaoke renderer."""
    project = Project()
    singer_id = project.get_default_singer().id
    lines = (
        "昨日までのメロディー",
        "この歌を届けよう",
        "明日へ続くハーモニー",
    )
    project.sentences = [Sentence.from_text(text, singer_id) for text in lines]

    current = project.sentences[1]
    ruby_by_index = {0: "こ", 1: "の", 2: "うた", 5: "とど"}
    for index, reading in ruby_by_index.items():
        current.characters[index].set_ruby(Ruby(parts=[RubyPart(reading)]))

    # 同时显示已打轴/未打轴 checkpoint 与导唱待办标记。
    timestamp = 1000
    for char in current.characters[:4]:
        char.add_timestamp(timestamp)
        timestamp += 400
    current.characters[4].needs_guide = True
    return project


class InterfacePreview(QWidget):
    """A draggable shell containing the application's real karaoke preview."""

    HEADER_HEIGHT = 42
    CONTENT_MARGIN = 8

    def __init__(self, parent: QWidget | None = None, *, floating: bool = False):
        super().__init__(parent)
        self._floating = floating
        self._drag_offset: QPoint | None = None
        self._user_positioned = False
        self._values: dict[str, Any] = {}
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setAutoFillBackground(False)

        self.canvas = KaraokePreview(self)
        self.canvas.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.canvas.set_project(_sample_project())
        self.canvas.set_hide_hitbox_highlights(True)
        self.canvas.set_current_position(1, 0)
        self.canvas.set_focus_position(1, 0)
        theme.changed.connect(self.update)

    @property
    def preview_values(self) -> dict[str, Any]:
        """Return a copy for diagnostics and focused UI tests."""
        return dict(self._values)

    @property
    def user_positioned(self) -> bool:
        return self._user_positioned

    def set_preview_values(self, **values: Any) -> None:
        """Apply settings through the same public API used by the editor."""
        self._values.update(values)
        self.canvas.set_font_sizes(
            int(values.get("font_size", 18)),
            int(values.get("current_line_font_size", 22)),
            int(values.get("ruby_size", 10)),
            int(values.get("cp_size", 8)),
            float(values.get("line_height_factor", 1.2)),
            int(values.get("ruby_spacing", 4)),
            main_font=values.get("main_font", DEFAULT_FONT_FAMILY),
            ruby_font=values.get("ruby_font", DEFAULT_FONT_FAMILY),
            cp_spacing=int(values.get("cp_spacing", 4)),
        )
        self.canvas.set_alignment(values.get("alignment", "center"))
        self.canvas.set_alignment_margin(int(values.get("alignment_margin", 168)))
        markers = values.get("checkpoint_markers") or {}
        if markers:
            self.canvas.set_checkpoint_markers(markers)
        self.canvas.set_needs_guide_style(
            values.get("needs_guide_symbol", "✚"),
            int(values.get("needs_guide_size", 12)),
        )
        self.canvas.request_repaint()
        self.update()

    def clamp_to_parent(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        max_x = max(0, parent.width() - self.width())
        max_y = max(0, parent.height() - self.height())
        self.move(
            max(0, min(self.x(), max_x)),
            max(0, min(self.y(), max_y)),
        )

    def resizeEvent(self, event: QResizeEvent | None) -> None:  # noqa: N802
        margin = self.CONTENT_MARGIN
        self.canvas.setGeometry(
            margin,
            self.HEADER_HEIGHT,
            max(1, self.width() - margin * 2),
            max(1, self.height() - self.HEADER_HEIGHT - margin),
        )
        # 高度变化会影响真实预览的可见行数，用现值重走同一设置 API。
        if self._values:
            self.set_preview_values(**self._values)
        super().resizeEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if (
            event is not None
            and event.button() == Qt.MouseButton.LeftButton
            and event.position().y() <= self.HEADER_HEIGHT
        ):
            self._drag_offset = event.position().toPoint()
            self.raise_()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and self._drag_offset is not None:
            parent = self.parentWidget()
            if parent is not None:
                top_left = parent.mapFromGlobal(event.globalPosition().toPoint())
                self.move(top_left - self._drag_offset)
                self.clamp_to_parent()
                self._user_positioned = True
            event.accept()
            return
        if event is not None and event.position().y() <= self.HEADER_HEIGHT:
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # noqa: N802
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            if event.position().y() <= self.HEADER_HEIGHT:
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.unsetCursor()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        area = QRectF(self.rect()).adjusted(3, 3, -3, -3)

        shadow = QColor(0, 0, 0, 55 if theme.is_dark else 38)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(area.translated(0, 2), 12, 12)

        chrome = QColor(theme.bg_secondary)
        chrome.setAlpha(218 if self._floating else 255)
        border = QColor(theme.border_primary)
        border.setAlpha(225)
        painter.setPen(QPen(border, 1))
        painter.setBrush(chrome)
        painter.drawRoundedRect(area, 12, 12)

        painter.setFont(ui_font(11))
        painter.setPen(theme.text_primary)
        painter.drawText(
            QRectF(
                area.left() + 14,
                area.top(),
                area.width() - 28,
                self.HEADER_HEIGHT,
            ),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            self.tr("界面实时预览（拖动此处移动）"),
        )
        painter.end()
