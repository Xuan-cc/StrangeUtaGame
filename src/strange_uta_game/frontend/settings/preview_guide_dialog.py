"""打轴预览指引设置弹窗 — 自定义上一个/正在/下一个打的字群的不透明度和开关。"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
)
from qfluentwidgets import PushButton, SpinBox, SwitchButton

DEFAULT_GUIDE: dict[str, float | bool] = {
    "prev_alpha": 80,
    "curr_alpha": 50,
    "next_alpha": 20,
    "prev_enabled": True,
    "curr_enabled": True,
    "next_enabled": True,
}

_ROWS = [
    ("prev",  "上一个打的字"),
    ("curr",  "正在打的字"),
    ("next",  "下一个要打的字"),
]


class PreviewGuideDialog(QDialog):
    """打轴预览指引的不透明度与开关配置弹窗。"""

    def __init__(self, current: Optional[dict[str, float | bool]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("预览指引方式"))
        self.setMinimumWidth(480)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        cfg = dict(DEFAULT_GUIDE)
        if current:
            cfg.update(current)

        self._spins: dict[str, SpinBox] = {}
        self._switches: dict[str, SwitchButton] = {}

        root = QVBoxLayout(self)

        root.addWidget(
            QLabel(
                self.tr(
                    "设置播放打轴时当前行光标前后字群的不透明度与是否显示：\n"
                    "上一个打的字（高不透明度提示已完成）/ 正在打的字（当前进度）/\n"
                    "下一个要打的字（低不透明度预告）。"
                )
            )
        )

        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(10)

        header_key = QLabel(self.tr("键值名"))
        header_alpha = QLabel(self.tr("不透明度"))
        header_enable = QLabel(self.tr("启用/关闭"))
        grid.addWidget(header_key, 0, 0)
        grid.addWidget(header_alpha, 0, 1)
        grid.addWidget(header_enable, 0, 2)

        for row, (key_prefix, label_src) in enumerate(_ROWS, start=1):
            grid.addWidget(QLabel(self.tr(label_src)), row, 0)

            alpha_key = f"{key_prefix}_alpha"
            spin = SpinBox(self)
            spin.setRange(0, 100)
            spin.setSuffix(" %")
            spin.setValue(int(cfg.get(alpha_key, DEFAULT_GUIDE[alpha_key])))
            self._spins[alpha_key] = spin
            grid.addWidget(spin, row, 1)

            en_key = f"{key_prefix}_enabled"
            sw = SwitchButton(self)
            sw.setOnText(self.tr("开"))
            sw.setOffText(self.tr("关"))
            sw.setChecked(bool(cfg.get(en_key, DEFAULT_GUIDE[en_key])))
            self._switches[en_key] = sw
            grid.addWidget(sw, row, 2)

        # 显式 tr 调用的字面量引用，确保 extract_ts.py 能静态提取
        self.tr("上一个打的字")
        self.tr("正在打的字")
        self.tr("下一个要打的字")

        root.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_reset = PushButton(self.tr("恢复默认"))
        btn_reset.clicked.connect(self._reset)
        btn_row.addWidget(btn_reset)
        btn_row.addStretch()
        btn_ok = PushButton(self.tr("确定"))
        btn_ok.setDefault(True)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton(self.tr("取消"))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        root.addLayout(btn_row)

    def get_guide_config(self) -> dict[str, float | bool]:
        """返回用户配置的 6 个值。"""
        result: dict[str, float | bool] = {}
        for key, spin in self._spins.items():
            result[key] = float(spin.value())
        for key, sw in self._switches.items():
            result[key] = sw.isChecked()
        return result

    def _reset(self):
        for key, spin in self._spins.items():
            spin.setValue(int(DEFAULT_GUIDE[key]))
        for key, sw in self._switches.items():
            sw.setChecked(bool(DEFAULT_GUIDE[key]))
