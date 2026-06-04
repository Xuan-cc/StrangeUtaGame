"""Checkpoint 字符设定弹窗 — 自定义节奏点标记符号。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)
from qfluentwidgets import PushButton


# 默认值（与 AppSettings.DEFAULT_SETTINGS 保持一致）
DEFAULT_MARKERS = {
    "cp_first_timed": "▶",
    "cp_first_empty": "▷",
    "cp_multi_timed": "▮",
    "cp_multi_empty": "▯",
    "cp_sentence_end_timed": "⬟",
    "cp_sentence_end_empty": "⬠",
}

# 三行两列的布局定义：(配置key, 行标签, 列0=已打轴, 列1=未打轴)
_LAYOUT = [
    ("cp_first",        "首节奏点",   "cp_first_timed",          "cp_first_empty"),
    ("cp_multi",        "后续节奏点", "cp_multi_timed",          "cp_multi_empty"),
    ("cp_sentence_end", "句尾标记",   "cp_sentence_end_timed",   "cp_sentence_end_empty"),
]


class CheckpointMarkerDialog(QDialog):
    """自定义 checkpoint 标记字符的配置弹窗。"""

    def __init__(self, current: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Checkpoint 字符设定")
        self.setMinimumWidth(320)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        markers = dict(DEFAULT_MARKERS)
        if current:
            markers.update(current)

        self._edits: dict[str, QLineEdit] = {}

        root = QVBoxLayout(self)

        # 表头
        header = QHBoxLayout()
        header.addStretch()
        header.addWidget(QLabel("已打轴"))
        header.addWidget(QLabel("未打轴"))
        root.addLayout(header)

        # 三行
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for row, (_, label, timed_key, empty_key) in enumerate(_LAYOUT):
            grid.addWidget(QLabel(label), row, 0)
            for col, key in enumerate((timed_key, empty_key), start=1):
                edit = QLineEdit(markers.get(key, DEFAULT_MARKERS[key]))
                edit.setMaxLength(4)
                edit.setFixedWidth(48)
                edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(edit, row, col)
                self._edits[key] = edit
        root.addLayout(grid)

        # 按钮行
        btn_row = QHBoxLayout()
        btn_reset = PushButton("恢复默认")
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btn_ok = PushButton("确定")
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消")
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    def get_markers(self) -> dict[str, str]:
        """返回用户配置的 6 个标记字符。"""
        return {key: edit.text() or DEFAULT_MARKERS[key] for key, edit in self._edits.items()}

    def _reset(self):
        for key, edit in self._edits.items():
            edit.setText(DEFAULT_MARKERS[key])
