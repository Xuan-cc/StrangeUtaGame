"""Nicokara 导出元数据标签设置对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from strange_uta_game.frontend.font_utils import ui_font
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import LineEdit, PrimaryPushButton, PushButton, ScrollArea, SpinBox


class NicokaraTagsDialog(QDialog):
    """Nicokara 导出元数据标签设置对话框

    设置 @Title/@Artist/@Album/@TaggingBy/@SilencemSec/@Offset/@HeadOffset/@Custom 等标签。
    """

    def __init__(self, tag_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Nicokara 标签设置"))
        self.setMinimumWidth(500)

        screen = parent.screen() if parent else QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(int(screen.availableGeometry().height() * 0.85))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel(self.tr("Nicokara 标签设置"))
        title.setFont(ui_font(14))
        layout.addWidget(title)

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(ScrollArea.Shape.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(14)

        form_layout = QVBoxLayout()
        form_layout.setSpacing(8)

        def _row(label_text: str) -> LineEdit:
            row = QHBoxLayout()
            lbl = QLabel(label_text)
            lbl.setFont(ui_font(10))
            lbl.setMinimumWidth(150)
            edit = LineEdit()
            edit.setFont(ui_font(10))
            row.addWidget(lbl)
            row.addWidget(edit)
            form_layout.addLayout(row)
            return edit

        self._edit_title = _row(self.tr("@Title（歌曲名）"))
        self._edit_artist = _row(self.tr("@Artist（演唱者）"))
        self._edit_album = _row(self.tr("@Album（专辑名）"))
        self._edit_tagging_by = _row(self.tr("@TaggingBy（打轴者）"))

        # @SilencemSec — SpinBox
        silence_row = QHBoxLayout()
        silence_lbl = QLabel(self.tr("@SilencemSec（静音）"))
        silence_lbl.setFont(ui_font(10))
        silence_lbl.setMinimumWidth(150)

        self._spin_silence = SpinBox()
        self._spin_silence.setRange(0, 99999)
        self._spin_silence.setSuffix(" ms")
        self._spin_silence.setFont(ui_font(10))
        silence_row.addWidget(silence_lbl)
        silence_row.addWidget(self._spin_silence)
        form_layout.addLayout(silence_row)

        def _offset_row(key: str, edit_attr: str) -> None:
            row = QHBoxLayout()
            lbl = QLabel(key)
            lbl.setFont(ui_font(10))
            lbl.setMinimumWidth(150)
            spin = SpinBox()
            spin.setRange(-99999, 99999)
            spin.setSuffix(" ms")
            spin.setFont(ui_font(10))
            setattr(self, edit_attr, spin)
            row.addWidget(lbl)
            row.addWidget(spin)
            form_layout.addLayout(row)

        _offset_row(self.tr("@Offset（全局偏移）"), "_spin_offset")
        _offset_row(self.tr("@HeadOffset（头部偏移）"), "_spin_head_offset")

        scroll_layout.addLayout(form_layout)

        # @Custom 动态列表
        custom_lbl = QLabel(self.tr("@Custom（自定义标签）"))
        custom_lbl.setFont(ui_font(10))
        scroll_layout.addWidget(custom_lbl)

        self._custom_list: list[LineEdit] = []
        self._custom_container = QVBoxLayout()
        self._custom_container.setSpacing(4)
        scroll_layout.addLayout(self._custom_container)

        custom_btn_row = QHBoxLayout()
        btn_add_custom = PushButton(self.tr("添加自定义行"), self)
        btn_add_custom.setFont(ui_font(10))
        btn_add_custom.clicked.connect(self._on_add_custom)
        custom_btn_row.addWidget(btn_add_custom)
        custom_btn_row.addStretch()
        scroll_layout.addLayout(custom_btn_row)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, stretch=1)

        # 填充初始值
        self._edit_title.setText(tag_data.get("title", ""))
        self._edit_artist.setText(tag_data.get("artist", ""))
        self._edit_album.setText(tag_data.get("album", ""))
        self._edit_tagging_by.setText(tag_data.get("tagging_by", ""))
        self._spin_silence.setValue(tag_data.get("silence_ms", 0))
        self._spin_offset.setValue(tag_data.get("offset", 0))
        self._spin_head_offset.setValue(tag_data.get("head_offset", 0))
        for custom_val in tag_data.get("custom", []):
            self._on_add_custom(custom_val)

        # 确定/取消
        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton(self.tr("确定"), self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton(self.tr("取消"), self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _on_add_custom(self, value: str = ""):
        edit = LineEdit()
        edit.setFont(ui_font(10))
        edit.setPlaceholderText(self.tr("自定义标签内容，例：@MyTag=value"))
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
            "offset": self._spin_offset.value(),
            "head_offset": self._spin_head_offset.value(),
            "custom": [e.text().strip() for e in self._custom_list if e.text().strip()],
        }
