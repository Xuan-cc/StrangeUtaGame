"""日志界面。

简单的日志显示界面，用于显示应用运行日志。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
)
from PyQt6.QtCore import Qt
from qfluentwidgets import (
    TextEdit,
    PushButton,
    FluentIcon as FIF,
    SubtitleLabel,
)


class LogInterface(QWidget):
    """日志界面"""

    def __init__(self, parent=None):
        super().__init__(parent)

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = SubtitleLabel("日志")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        layout.addWidget(title)

        # 日志显示区
        self.text_log = TextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setPlaceholderText("应用运行日志将显示在这里...")
        layout.addWidget(self.text_log)

        # 按钮区
        button_layout = QHBoxLayout()

        self.btn_clear = PushButton("清空", self)
        self.btn_clear.setIcon(FIF.DELETE)
        self.btn_clear.clicked.connect(self._on_clear)
        button_layout.addWidget(self.btn_clear)

        button_layout.addStretch()

        layout.addLayout(button_layout)

    def _on_clear(self):
        """清空日志"""
        self.text_log.clear()

    def append_log(self, message: str):
        """添加日志"""
        from datetime import datetime

        timestamp = datetime.now().strftime("%H:%M:%S")
        self.text_log.append(f"[{timestamp}] {message}")
