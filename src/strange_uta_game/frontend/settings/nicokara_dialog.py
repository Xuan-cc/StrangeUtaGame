"""Nicokara 导出元数据标签设置对话框。"""

from __future__ import annotations

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, SpinBox


class NicokaraTagsDialog(QDialog):
    """Nicokara 导出元数据标签设置对话框

    设置 @Title/@Artist/@Album/@TaggingBy/@SilencemSec/@Custom 等标签。
    """

    def __init__(self, tag_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Nicokara 标签设置")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("Nicokara 标签设置")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        form_layout = QVBoxLayout()
        form_layout.setSpacing(8)

        def _row(label_text: str) -> LineEdit:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFont(QFont("Microsoft YaHei", 10))
            lbl.setFixedWidth(120)
            edit = LineEdit()
            edit.setFont(QFont("Microsoft YaHei", 10))
            row.addWidget(lbl)
            row.addWidget(edit)
            form_layout.addLayout(row)
            return edit

        self._edit_title = _row("@Title（歌曲名）")
        self._edit_artist = _row("@Artist（演唱者）")
        self._edit_album = _row("@Album（专辑名）")
        self._edit_tagging_by = _row("@TaggingBy（打轴者）")

        # @SilencemSec — SpinBox
        silence_row = QHBoxLayout()
        silence_lbl = QLabel("@SilencemSec（静音）")
        silence_lbl.setFont(QFont("Microsoft YaHei", 10))
        silence_lbl.setFixedWidth(120)

        self._spin_silence = SpinBox()
        self._spin_silence.setRange(0, 99999)
        self._spin_silence.setSuffix(" ms")
        self._spin_silence.setFont(QFont("Microsoft YaHei", 10))
        silence_row.addWidget(silence_lbl)
        silence_row.addWidget(self._spin_silence)
        form_layout.addLayout(silence_row)

        layout.addLayout(form_layout)

        # @Custom 动态列表
        custom_lbl = QLabel("@Custom（自定义标签）")
        custom_lbl.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(custom_lbl)

        self._custom_list: list[LineEdit] = []
        self._custom_container = QVBoxLayout()
        self._custom_container.setSpacing(4)
        layout.addLayout(self._custom_container)

        custom_btn_row = QHBoxLayout()
        btn_add_custom = PushButton("添加自定义行", self)
        btn_add_custom.setFont(QFont("Microsoft YaHei", 10))
        btn_add_custom.clicked.connect(self._on_add_custom)
        custom_btn_row.addWidget(btn_add_custom)
        custom_btn_row.addStretch()
        layout.addLayout(custom_btn_row)

        # 填充初始值
        self._edit_title.setText(tag_data.get("title", ""))
        self._edit_artist.setText(tag_data.get("artist", ""))
        self._edit_album.setText(tag_data.get("album", ""))
        self._edit_tagging_by.setText(tag_data.get("tagging_by", ""))
        self._spin_silence.setValue(tag_data.get("silence_ms", 0))
        for custom_val in tag_data.get("custom", []):
            self._on_add_custom(custom_val)

        layout.addStretch()

        # 确定/取消
        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _on_add_custom(self, value: str = ""):
        edit = LineEdit()
        edit.setFont(QFont("Microsoft YaHei", 10))
        edit.setPlaceholderText("自定义标签内容，例：@MyTag=value")
        if value:
            edit.setText(value)
        self._custom_list.append(edit)
        self._custom_container.addWidget(edit)

    def get_tag_data(self) -> dict:
        return {
            "title": self._edit_title.text().strip(),
            "artist": self._edit_artist.text().strip(),
            "album": self._edit_album.text().strip(),
            "tagging_by": self._edit_tagging_by.text().strip(),
            "silence_ms": self._spin_silence.value(),
            "custom": [e.text().strip() for e in self._custom_list if e.text().strip()],
        }
