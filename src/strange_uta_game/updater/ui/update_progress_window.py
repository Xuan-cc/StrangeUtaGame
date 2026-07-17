"""更新准备进度窗口。

当主程序从远端更新 Updater.exe 并准备启动更新时，弹出此窗口展示
实时进度，并允许用户手动取消。视觉风格参考启动闪屏窗口，配色跟随主题。
"""

from __future__ import annotations

import re

from PyQt6.QtCore import Qt, QRectF, QCoreApplication, pyqtSignal
from PyQt6.QtGui import QPainter, QColor, QPainterPath, QCursor, QKeyEvent, QCloseEvent
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QApplication,
)
from qfluentwidgets import ProgressRing, PushButton


def _tr(s: str) -> str:
    return QCoreApplication.translate("UpdateProgressWindow", s)


class UpdateProgressWindow(QWidget):
    """更新准备进度窗口 — 圆角面板 + 进度环 + 取消按钮，跟随主题配色。"""

    cancelled = pyqtSignal()

    _WIDTH = 400
    _HEIGHT = 210
    _RADIUS = 16

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._WIDTH, self._HEIGHT)

        # 是否由用户主动取消（而非正常完成）。用于 _on_done 回调判断是否需要跳过后续处理。
        self._cancelled_by_user = False

        # paintEvent 用到的颜色，先给默认值，由 _apply_theme 覆盖
        self._bg_color = QColor(32, 32, 36, 240)
        self._border_color = QColor(255, 255, 255, 20)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 18)
        root.setSpacing(0)

        # 标题
        self._title_label = QLabel(_tr("正在准备更新"))
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._title_label)
        root.addSpacing(18)

        # 中间：进度环 + 状态文本
        mid = QHBoxLayout()
        mid.setSpacing(16)

        self._ring = ProgressRing(self, useAni=False)
        self._ring.setFixedSize(48, 48)
        self._ring.setTextVisible(True)
        self._ring.setValue(0)
        mid.addWidget(self._ring)

        self._status = QLabel(_tr("正在获取最新更新器，请稍候…"))
        self._status.setWordWrap(True)
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        mid.addWidget(self._status, 1)
        root.addLayout(mid)

        root.addStretch()

        # 底部：取消按钮（右对齐）
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._btn_cancel = PushButton(_tr("取消更新"), self)
        self._btn_cancel.setFixedWidth(100)
        self._btn_cancel.clicked.connect(self._on_cancel)
        bottom.addWidget(self._btn_cancel)
        root.addLayout(bottom)

        # 应用当前主题并监听后续切换
        self._apply_theme()
        try:
            from strange_uta_game.frontend.theme import theme
            theme.changed.connect(self._apply_theme)
        except Exception:
            pass

        self._center_on_screen()

    # ----------------------------------------------------------------

    def _apply_theme(self) -> None:
        """根据当前主题刷新所有配色。"""
        try:
            from strange_uta_game.frontend.theme import theme
            c = theme.colors
        except Exception:
            return

        dark = c.is_dark

        # ── paintEvent 用色 ──
        bg = QColor(c.bg_secondary)
        bg.setAlpha(240)
        self._bg_color = bg
        border = QColor(c.border_primary)
        border.setAlpha(40)
        self._border_color = border

        # ── 文本 ──
        self._title_label.setStyleSheet(
            f"color: {c.text_primary.name()};"
            "font-size: 16px; font-weight: 600;"
        )
        self._status.setStyleSheet(
            f"color: {c.text_secondary.name()}; font-size: 13px;"
        )

        # ── 进度环：环形用主题色，百分比文字跟随文本色 ──
        from qfluentwidgets import themeColor
        accent = themeColor()
        self._ring.setCustomBarColor(accent, accent)
        text_pen_color = QColor(c.text_primary)
        self._ring._drawText = lambda painter, text: (
            painter.setFont(self._ring.font()),
            painter.setPen(text_pen_color),
            painter.drawText(
                self._ring.rect(), Qt.AlignmentFlag.AlignCenter, text
            ),
        )

        # ── 取消按钮（边框跟随主题色）──
        ac = accent.name()
        if dark:
            self._btn_cancel.setStyleSheet(
                "QPushButton {"
                f"  color: {c.text_primary.name()};"
                "  background: rgba(255,255,255,0.08);"
                f"  border: 1px solid {ac};"
                "  border-radius: 6px;"
                "  padding: 6px 12px;"
                "  font-size: 13px;"
                "}"
                "QPushButton:hover { background: rgba(255,255,255,0.15); }"
                "QPushButton:pressed { background: rgba(255,255,255,0.05); }"
            )
        else:
            self._btn_cancel.setStyleSheet(
                "QPushButton {"
                f"  color: {c.text_primary.name()};"
                "  background: rgba(0,0,0,0.05);"
                f"  border: 1px solid {ac};"
                "  border-radius: 6px;"
                "  padding: 6px 12px;"
                "  font-size: 13px;"
                "}"
                "QPushButton:hover { background: rgba(0,0,0,0.09); }"
                "QPushButton:pressed { background: rgba(0,0,0,0.03); }"
            )

        self.update()

    # ----------------------------------------------------------------

    def _center_on_screen(self) -> None:
        # 居中到光标所在屏（多显示器下不强制跳回主屏）
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )

    def update_from_text(self, text: str) -> None:
        """根据进度文本更新状态和进度环。

        文本中包含 ``XX%`` 时自动提取并设置进度环的值。
        """
        self._status.setText(text)
        m = re.search(r"(\d+)%", text)
        if m:
            self._ring.setValue(int(m.group(1)))
        QApplication.processEvents()

    def set_progress(self, value: int, text: str = "") -> None:
        """直接设置进度值和文本。"""
        self._ring.setValue(value)
        if text:
            self._status.setText(text)
        QApplication.processEvents()

    def _on_cancel(self) -> None:
        self._btn_cancel.setEnabled(False)
        self._btn_cancel.setText(_tr("正在取消…"))
        self._cancelled_by_user = True
        self.cancelled.emit()
        self.finish()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self._on_cancel()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent | None) -> None:
        if not self._cancelled_by_user:
            self._on_cancel()
        if event is not None:
            event.accept()

    # ---- 绘制 ----

    def paintEvent(self, event):
        W, H, R = self._WIDTH, self._HEIGHT, self._RADIUS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, W, H), R, R)
        p.setClipPath(clip)
        p.fillRect(0, 0, W, H, self._bg_color)
        p.setPen(self._border_color)
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), R, R)
        p.end()

    def finish(self) -> None:
        """淡出并关闭窗口。"""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(250)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.finished.connect(self.close)
        self._fade_anim.start()
