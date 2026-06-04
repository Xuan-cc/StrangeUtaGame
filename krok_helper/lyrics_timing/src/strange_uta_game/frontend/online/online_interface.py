"""在线查询界面。

暂定不开发，仅保留占位标签页。
"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from qfluentwidgets import SubtitleLabel


class OnlineQueryInterface(QWidget):
    """在线查询 - 占位界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        label = SubtitleLabel("在线查询 - 暂未开发")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
