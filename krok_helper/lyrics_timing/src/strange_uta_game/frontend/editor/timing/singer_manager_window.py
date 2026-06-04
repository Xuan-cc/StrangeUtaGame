"""微型演唱者管理浮动窗口。

嵌入 SingerManagerInterface，以浮动窗口形式挂在 timing_interface 旁边，
不阻塞打轴操作，同时允许随时编辑演唱者。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QCursor
from PyQt6.QtWidgets import QWidget, QVBoxLayout

from strange_uta_game.frontend.singer.singer_interface import SingerManagerInterface


class MiniSingerManager(QWidget):
    """微型演唱者管理浮动窗口。

    复用 SingerManagerInterface 的全部功能，以独立浮动窗口呈现，
    可拖拽到 timing_interface 旁边使用。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("演唱者管理")
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(420, 560)
        self._init_ui()
        self._drag_pos = None

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(0)

        self._singer_ui = SingerManagerInterface(self)
        layout.addWidget(self._singer_ui)

    def set_project(self, project):
        self._singer_ui.set_project(project)

    def set_store(self, store):
        self._singer_ui.set_store(store)

    def show_at_cursor(self):
        """在鼠标位置附近显示，偏移到不遮挡 timing_interface 的区域。"""
        cursor_pos = QCursor.pos()
        screen = self.screen()
        if screen:
            geo = screen.availableGeometry()
            x = min(cursor_pos.x() + 20, geo.right() - self.width() - 10)
            y = min(cursor_pos.y() - 20, geo.bottom() - self.height() - 10)
            self.move(x, y)
        else:
            self.move(cursor_pos + QPoint(20, -20))
        self.show()
        self.raise_()
        self.activateWindow()
