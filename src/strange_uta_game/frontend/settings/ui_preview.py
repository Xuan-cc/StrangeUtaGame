"""Lightweight live preview used by the interface settings page."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QWidget

from strange_uta_game.frontend.font_utils import DEFAULT_FONT_FAMILY
from strange_uta_game.frontend.theme import theme


class InterfacePreview(QWidget):
    """Draw a small, project-independent approximation of the karaoke view."""

    _REFERENCE_WIDTH = 1200.0

    def __init__(self, parent: QWidget | None = None, *, floating: bool = False):
        super().__init__(parent)
        self._floating = floating
        if floating:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAutoFillBackground(False)
        else:
            self.setMinimumHeight(250)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._values: dict[str, Any] = {
            "main_font": DEFAULT_FONT_FAMILY,
            "ruby_font": DEFAULT_FONT_FAMILY,
            "font_size": 18,
            "current_line_font_size": 22,
            "ruby_size": 10,
            "ruby_spacing": 4,
            "cp_size": 8,
            "cp_spacing": 4,
            "line_height_factor": 1.2,
            "alignment_margin": 168,
            "alignment": "center",
            "needs_guide_symbol": "✚",
            "needs_guide_size": 12,
            "checkpoint_marker": "▶",
        }
        theme.changed.connect(self.update)

    @property
    def preview_values(self) -> dict[str, Any]:
        """Return a copy for diagnostics and focused UI tests."""
        return dict(self._values)

    def set_preview_values(self, **values: Any) -> None:
        self._values.update(values)
        self.update()

    @staticmethod
    def _font(family: str, pixel_size: int, *, bold: bool = False) -> QFont:
        font = QFont(family or DEFAULT_FONT_FAMILY)
        font.setPixelSize(max(1, int(pixel_size)))
        if bold:
            font.setWeight(QFont.Weight.Bold)
        return font

    @staticmethod
    def _text_x(
        alignment: str,
        width: float,
        left: float,
        right: float,
        margin: float,
    ) -> float:
        if alignment == "left":
            return left + margin
        if alignment == "right":
            return right - margin - width
        return (left + right - width) / 2.0

    def _draw_main_text(
        self,
        painter: QPainter,
        text: str,
        baseline: float,
        font: QFont,
        color,
        area: QRectF,
        scaled_margin: float,
    ) -> tuple[float, QFontMetricsF]:
        metrics = QFontMetricsF(font)
        text_width = metrics.horizontalAdvance(text)
        x = self._text_x(
            self._values["alignment"],
            text_width,
            area.left() + 34,
            area.right() - 10,
            scaled_margin,
        )
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(QPointF(x, baseline), text)
        return x, metrics

    def paintEvent(self, event: QPaintEvent | None) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        area = QRectF(self.rect()).adjusted(7, 5, -7, -9)
        if self._floating:
            shadow = QColor(0, 0, 0, 55 if theme.is_dark else 38)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(shadow)
            painter.drawRoundedRect(area.translated(0, 4), 12, 12)

        background = QColor(theme.karaoke_bg)
        background.setAlpha(224 if self._floating else 255)
        border = QColor(theme.border_primary)
        border.setAlpha(220)
        painter.setPen(QPen(border, 1))
        painter.setBrush(background)
        painter.drawRoundedRect(area, 12 if self._floating else 8, 12 if self._floating else 8)

        values = self._values
        alignment = values.get("alignment", "center")
        alignment_label = {
            "left": self.tr("左对齐"),
            "center": self.tr("居中对齐"),
            "right": self.tr("右对齐"),
        }.get(alignment, self.tr("居中对齐"))

        hint_font = self._font(DEFAULT_FONT_FAMILY, 11)
        title_font = self._font(DEFAULT_FONT_FAMILY, 12, bold=True)
        painter.setFont(title_font)
        painter.setPen(theme.text_primary)
        painter.drawText(
            area.adjusted(16, 11, -16, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
            self.tr("界面实时预览"),
        )
        painter.setFont(hint_font)
        painter.setPen(theme.text_hint)
        painter.drawText(
            area.adjusted(16, 12, -16, 0),
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
            self.tr("当前效果 · {0}").format(alignment_label),
        )

        separator_y = area.top() + 35
        separator = QColor(theme.border_primary)
        separator.setAlpha(150)
        painter.setPen(QPen(separator, 1))
        painter.drawLine(
            QPointF(area.left() + 14, separator_y),
            QPointF(area.right() - 14, separator_y),
        )

        content = area.adjusted(12, 42, -12, -12)
        margin = max(0.0, float(values.get("alignment_margin", 0)))
        scaled_margin = margin * content.width() / self._REFERENCE_WIDTH

        current_size = int(values.get("current_line_font_size", 22))
        context_size = int(values.get("font_size", 18))
        ruby_size = int(values.get("ruby_size", 10))
        cp_size = int(values.get("cp_size", 8))
        ruby_spacing = max(0, int(values.get("ruby_spacing", 4)))
        cp_spacing = max(0, int(values.get("cp_spacing", 4)))
        main_font = self._font(values.get("main_font", ""), current_size, bold=True)
        context_font = self._font(values.get("main_font", ""), context_size)
        ruby_font = self._font(values.get("ruby_font", ""), ruby_size)
        cp_font = self._font(DEFAULT_FONT_FAMILY, cp_size)

        current_metrics = QFontMetricsF(main_font)
        ruby_metrics = QFontMetricsF(ruby_font)
        cp_metrics = QFontMetricsF(cp_font)
        unit_height = (
            current_metrics.height()
            + ruby_metrics.height()
            + cp_metrics.height()
            + ruby_spacing
            + cp_spacing
        )
        factor = max(0.05, float(values.get("line_height_factor", 1.2)))
        row_gap = min(content.height() * 0.37, max(32.0, unit_height * factor))
        current_baseline = content.center().y() + current_metrics.ascent() * 0.28

        rows = (
            (
                "昨日までのメロディー",
                current_baseline - row_gap,
                theme.karaoke_text_past,
                1,
            ),
            ("この歌を届けよう", current_baseline, theme.karaoke_text_current, 2),
            (
                "明日へ続くハーモニー",
                current_baseline + row_gap,
                theme.karaoke_text_future,
                3,
            ),
        )
        for text, baseline, color, number in rows:
            painter.setFont(hint_font)
            painter.setPen(
                theme.line_number_current if number == 2 else theme.line_number_normal
            )
            painter.drawText(QPointF(content.left() + 7, baseline), str(number))
            font = main_font if number == 2 else context_font
            x, metrics = self._draw_main_text(
                painter, text, baseline, font, color, content, scaled_margin
            )
            if number != 2:
                continue

            painter.setFont(ruby_font)
            painter.setPen(theme.accent_secondary)
            ruby_text = "この うた      とど"
            ruby_y = baseline - metrics.ascent() - ruby_spacing
            painter.drawText(QPointF(x, ruby_y), ruby_text)

            painter.setFont(cp_font)
            painter.setPen(theme.accent_warning)
            marker = str(values.get("checkpoint_marker", "▶") or "▶")
            marker_y = baseline + metrics.descent() + cp_spacing + cp_metrics.ascent()
            painter.drawText(QPointF(x, marker_y), marker)

            guide_font = self._font(
                DEFAULT_FONT_FAMILY,
                int(values.get("needs_guide_size", 12)),
                bold=True,
            )
            painter.setFont(guide_font)
            guide_color = theme.accent_warning
            guide_color.setAlpha(190)
            painter.setPen(guide_color)
            guide = str(values.get("needs_guide_symbol", "✚") or "✚")
            painter.drawText(QPointF(x - 4, baseline - metrics.ascent() + 3), guide)

        painter.end()
