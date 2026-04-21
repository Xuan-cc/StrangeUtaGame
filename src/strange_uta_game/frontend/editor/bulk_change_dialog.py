"""批量変更对话框 (Ctrl+H)。

搜索项目中的指定词汇，批量修改其注音读法和节奏点数量，
可选择将该词注册到用户读音词典中。
"""

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
)
from PyQt6.QtGui import QFont
from qfluentwidgets import (
    LineEdit,
    PushButton,
    PrimaryPushButton,
    InfoBar,
    InfoBarPosition,
)
from typing import Optional
from strange_uta_game.backend.domain import Project, Ruby
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    split_ruby_for_checkpoints,
    align_ruby_to_checkpoints,
)


class BulkChangeDialog(QDialog):
    """批量変更对话框

    功能：
    - 搜索词：在项目中查找匹配的词汇
    - 修改注音：替换匹配词的 Ruby 读音
    - 修改节奏点数量（所有匹配字符 +N）
    - 可选注册到用户读音词典
    """

    def __init__(
        self,
        project: Optional[Project],
        parent=None,
        initial_word: str = "",
        initial_reading: str = "",
    ):
        super().__init__(parent)
        self._project = project
        self.setWindowTitle("批量变更 (Ctrl+H)")
        self.setMinimumWidth(440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        title = QLabel("批量变更")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel("搜索项目中的词汇，批量修改其注音或节奏点数量。")
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 搜索词
        row1 = QHBoxLayout()
        lbl1 = QLabel("搜索词：")
        lbl1.setFont(QFont("Microsoft YaHei", 10))
        lbl1.setFixedWidth(100)
        self.edit_word = LineEdit()
        self.edit_word.setPlaceholderText("输入要替换的词汇...")
        self.edit_word.setFont(QFont("Microsoft YaHei", 10))
        if initial_word:
            self.edit_word.setText(initial_word)
        row1.addWidget(lbl1)
        row1.addWidget(self.edit_word)
        layout.addLayout(row1)

        # 新注音
        row2 = QHBoxLayout()
        lbl2 = QLabel("新注音：")
        lbl2.setFont(QFont("Microsoft YaHei", 10))
        lbl2.setFixedWidth(100)
        self.edit_reading = LineEdit()
        self.edit_reading.setPlaceholderText(
            "留空将删除注音（假名，逗号分隔多段；同字符内多节奏点可用 # 分组，如 わ#た#し）"
        )
        self.edit_reading.setFont(QFont("Microsoft YaHei", 10))
        if initial_reading:
            self.edit_reading.setText(initial_reading)
        row2.addWidget(lbl2)
        row2.addWidget(self.edit_reading)
        layout.addLayout(row2)

        # 不修改注音复选框
        self.chk_skip_ruby = QCheckBox("不修改注音（勾选后忽略上方注音栏）")
        self.chk_skip_ruby.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self.chk_skip_ruby)

        # 节奏点数量设置
        row3 = QHBoxLayout()
        lbl3 = QLabel("设置节奏点：")
        lbl3.setFont(QFont("Microsoft YaHei", 10))
        lbl3.setFixedWidth(100)
        self.edit_delta = LineEdit()
        self.edit_delta.setText("-1")
        self.edit_delta.setPlaceholderText("整数或逗号分隔，如 -1、0、1,2,1")
        self.edit_delta.setFont(QFont("Microsoft YaHei", 10))
        self.edit_delta.setFixedWidth(160)
        hint = QLabel("（-1=不修改，0=设为0，逗号分隔对应各字符）")
        hint.setFont(QFont("Microsoft YaHei", 9))
        row3.addWidget(lbl3)
        row3.addWidget(self.edit_delta)
        row3.addWidget(hint)
        row3.addStretch()
        layout.addLayout(row3)

        # 注册到词典
        self.chk_register = QCheckBox("将此词注册到读音词典")
        self.chk_register.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self.chk_register)

        # 预览（匹配数量）
        self.lbl_preview = QLabel("")
        self.lbl_preview.setFont(QFont("Microsoft YaHei", 10))
        layout.addWidget(self.lbl_preview)
        self.edit_word.textChanged.connect(self._update_preview)

        layout.addStretch()

        # 按钮行
        btn_row = QHBoxLayout()
        btn_apply = PrimaryPushButton("执行", self)
        btn_apply.setFont(QFont("Microsoft YaHei", 10))
        btn_apply.clicked.connect(self._on_apply)
        btn_cancel = PushButton("关闭", self)
        btn_cancel.setFont(QFont("Microsoft YaHei", 10))
        btn_cancel.clicked.connect(self.reject)
        btn_row.addStretch()
        btn_row.addWidget(btn_apply)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _update_preview(self, word: str):
        if not self._project or not word.strip():
            self.lbl_preview.setText("")
            return
        count = self._count_matches(word.strip())
        self.lbl_preview.setText(f"找到 {count} 处匹配")

    def _count_matches(self, word: str) -> int:
        if not self._project:
            return 0
        count = 0
        for sentence in self._project.sentences:
            text = sentence.text
            pos = 0
            while pos <= len(text) - len(word):
                if text[pos : pos + len(word)] == word:
                    count += 1
                    pos += len(word)
                else:
                    pos += 1
        return count

    def _on_apply(self):
        word = self.edit_word.text().strip()
        if not word:
            return
        if not self._project:
            return

        reading = self.edit_reading.text().strip()
        skip_ruby = self.chk_skip_ruby.isChecked()
        checkpoint_str = self.edit_delta.text().strip() or "-1"
        # 支持逗号分隔的per-char节奏点值（如 "1,2,1"）
        checkpoint_vals = []
        try:
            for v in checkpoint_str.split(","):
                checkpoint_vals.append(int(v.strip()))
        except ValueError:
            checkpoint_vals = [-1]
        word_len = len(word)
        changed = 0

        for sentence in self._project.sentences:
            text = sentence.text
            pos = 0
            while pos <= len(text) - word_len:
                if text[pos : pos + word_len] == word:
                    # 修改注音
                    if not skip_ruby:
                        if reading:
                            if "," in reading and word_len > 1:
                                # 逗号分隔 → per-char Ruby
                                parts = reading.split(",")
                                for k in range(word_len):
                                    ci = pos + k
                                    if ci < len(sentence.characters):
                                        part = (
                                            parts[k].strip() if k < len(parts) else ""
                                        )
                                        if part:
                                            tgt = sentence.characters[ci]
                                            aligned = align_ruby_to_checkpoints(
                                                part,
                                                tgt.check_count,
                                                tgt.is_sentence_end,
                                            )
                                            if aligned:
                                                tgt.set_ruby(Ruby(text=aligned))
                                            else:
                                                tgt.set_ruby(None)
                                        else:
                                            sentence.characters[ci].set_ruby(None)
                            else:
                                # 整词 Ruby：按字符数拆分
                                split_parts = split_ruby_for_checkpoints(
                                    reading, word_len
                                )
                                for k in range(word_len):
                                    ci = pos + k
                                    if ci < len(sentence.characters):
                                        if k < len(split_parts) and split_parts[k]:
                                            tgt = sentence.characters[ci]
                                            aligned = align_ruby_to_checkpoints(
                                                split_parts[k],
                                                tgt.check_count,
                                                tgt.is_sentence_end,
                                            )
                                            if aligned:
                                                tgt.set_ruby(Ruby(text=aligned))
                                            else:
                                                tgt.set_ruby(None)
                                        else:
                                            sentence.characters[ci].set_ruby(None)
                        else:
                            # 留空 = 删除注音
                            for k in range(word_len):
                                ci = pos + k
                                if ci < len(sentence.characters):
                                    sentence.characters[ci].set_ruby(None)

                    # 设置节奏点（-1=不修改，0=设为0，>0=设为指定值）
                    for ci_offset in range(word_len):
                        ci = pos + ci_offset
                        # 取对应位置的值，超出则循环最后一个值
                        val_idx = min(ci_offset, len(checkpoint_vals) - 1)
                        cp_val = checkpoint_vals[val_idx]
                        if cp_val < 0:
                            continue  # -1 表示不修改
                        if ci < len(sentence.characters):
                            sentence.characters[ci].check_count = cp_val

                    changed += 1
                    pos += word_len
                else:
                    pos += 1

        # 注册到词典
        if self.chk_register.isChecked() and reading:
            try:
                from strange_uta_game.frontend.settings.settings_interface import (
                    AppSettings,
                )

                app_settings = AppSettings()
                entries = app_settings.load_dictionary()
                # 避免重复
                entries = [e for e in entries if e.get("word") != word]
                # 新条目插入到顶部（最高优先级）
                entries.insert(0, {"enabled": True, "word": word, "reading": reading})
                app_settings.save_dictionary(entries)
            except Exception:
                pass

        self.lbl_preview.setText(f"已修改 {changed} 处")

        # 通知父窗口刷新
        parent = self.parent()
        store = getattr(parent, "_store", None)
        if store is not None:
            store.notify("rubies")
            store.notify("checkpoints")
        refresh = getattr(parent, "refresh_lyric_display", None)
        if refresh is not None:
            refresh()
