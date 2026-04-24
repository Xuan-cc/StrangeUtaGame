"""时间轴控件。

显示当前播放位置、打轴节奏点分布。
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QWidget


# ──────────────────────────────────────────────
# 时间轴
# ──────────────────────────────────────────────

class TimelineWidget(QWidget):
    """时间轴 - 显示时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._time_tags: List[int] = []
        self.setMinimumHeight(80)
        self.setMaximumHeight(120)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.update()

    def set_position(self, ms: int):
        self._current_ms = ms
        self.update()

    def set_time_tags(self, tags_ms: List[int]):
        self._time_tags = sorted(tags_ms)
        self.update()

    def paintEvent(self, a0: Optional[QPaintEvent]):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor("#F0F0F0"))

        if self._duration_ms <= 0:
            painter.setPen(QColor("#999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请加载音频文件"
            )
            return

        # 时间网格（10 秒间隔）
        painter.setPen(QPen(QColor("#DDD"), 1))
        for t in range(0, self._duration_ms + 1, 10000):
            x = int((t / self._duration_ms) * w)
            painter.drawLine(x, 0, x, h)

        # 时间标签标记
        painter.setPen(QPen(QColor("#FF6B6B"), 2))
        for tag in self._time_tags:
            x = int((tag / self._duration_ms) * w)
            painter.drawLine(x, int(h * 0.3), x, int(h * 0.7))

        # 播放位置
        if 0 <= self._current_ms <= self._duration_ms:
            x = int((self._current_ms / self._duration_ms) * w)
            painter.setPen(QPen(QColor("#4ECDC4"), 2))
            painter.drawLine(x, 0, x, h)
            painter.setBrush(QBrush(QColor("#4ECDC4")))
            painter.drawEllipse(x - 4, h // 2 - 4, 8, 8)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None:
            return
        if self._duration_ms <= 0:
            return
        ratio = max(0.0, min(1.0, a0.position().x() / self.width()))
        self.seek_requested.emit(int(ratio * self._duration_ms))


# ──────────────────────────────────────────────
# 编辑器主界面
# ──────────────────────────────────────────────
