"""设置界面 — RhythmicaLyrics 风格层级设定系统。

使用 qfluentwidgets 的 SettingCardGroup + ExpandLayout 实现分组卡片布局。
所有设置通过 AppSettings 统一管理，默认保存到程序所在目录的 config.json。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QGroupBox,
    QFileDialog,
    QDialog,
    QTableWidget,
    QTableWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QKeyEvent, QPainter, QColor, QPen, QBrush, QPaintEvent
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    ComboBox,
    SpinBox,
    DoubleSpinBox,
    LineEdit,
    SwitchButton,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SettingCardGroup,
    SettingCard,
    ExpandLayout,
    ScrollArea,
)

from typing import Optional, Dict, Any
from pathlib import Path
import json
import sys
import threading

import numpy as np
import sounddevice as sd
import time as _time


class AppSettings:
    """应用设置管理"""

    DEFAULT_SETTINGS = {
        "audio": {
            "default_volume": 80,
            "default_speed": 1.0,
            "auto_play_on_load": False,
        },
        "timing": {
            "default_check_count": 1,
            "auto_advance_after_tag": True,
            "show_preview_lines": 5,
            "tag_offset_ms": -130,
            "speed_correction": 100,
            "fast_forward_ms": 5000,
            "rewind_ms": 5000,
            "jump_before_ms": 3000,
        },
        "auto_check": {
            "hiragana": True,
            "katakana": True,
            "kanji": True,
            "alphabet": False,
            "digit": False,
            "symbol": False,
            "space": False,
            "auto_on_load": True,
            "check_n": False,
            "check_sokuon": False,
            "check_parentheses": True,
            "check_empty_lines": False,
            "check_line_start": False,
            "check_line_end": True,
            "kanji_single_check": False,
            "space_after_japanese": True,
            "space_after_alphabet": True,
            "space_after_symbol": True,
            "small_kana": False,
            "check_space_as_line_end": True,
        },
        "ui": {
            "theme": "light",
            "language": "zh_CN",
            "font_size": 24,
        },
        "export": {
            "default_format": "LRC",
            "auto_add_extension": True,
            "last_export_dir": "",
            "offset_ms": -100,
        },
        "ruby_dictionary": {
            "enabled": True,
            "entries": [],
        },
        "nicokara_tags": {
            "title": "",
            "artist": "",
            "album": "",
            "tagging_by": "",
            "silence_ms": 0,
            "custom": [],
        },
        "auto_save": {
            "enabled": True,
            "interval_minutes": 5,
        },
        "singer_presets": [],
        "shortcuts": {
            "play_pause": "A",
            "stop": "S",
            "tag_now": "Space",
            "seek_back": "Z",
            "seek_forward": "X",
            "speed_down": "Q",
            "speed_up": "W",
            "edit_ruby": "F2",
            "toggle_checkpoint": "F4",
            "volume_up": "UP",
            "volume_down": "DOWN",
            "nav_prev_line": "LEFT",
            "nav_next_line": "RIGHT",
            "clear_tags": "BACKSPACE",
        },
    }

    @staticmethod
    def get_config_dir() -> Path:
        """获取配置文件目录（默认为程序所在目录）。

        支持通过程序目录下的 .config_redirect 文件重定向到自定义位置。
        """
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"
        if redirect_file.exists():
            try:
                custom_dir = Path(redirect_file.read_text(encoding="utf-8").strip())
                if custom_dir.is_dir():
                    return custom_dir
            except Exception:
                pass
        return program_dir

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_dir = self.get_config_dir()
            try:
                config_dir.mkdir(exist_ok=True)
            except OSError:
                # 程序目录不可写时回退到用户目录
                config_dir = Path.home() / ".strange_uta_game"
                config_dir.mkdir(exist_ok=True)
            self._config_path = config_dir / "config.json"
            # 迁移：若新位置无配置，检查旧位置
            if not self._config_path.exists():
                old_config = Path.home() / ".strange_uta_game" / "config.json"
                if old_config.exists():
                    try:
                        import shutil

                        shutil.copy2(str(old_config), str(self._config_path))
                    except Exception:
                        pass
        else:
            self._config_path = Path(config_path)
        self._settings = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    settings = self._deep_copy_defaults()
                    self._deep_merge(settings, loaded)
                    return settings
            except Exception as e:
                print(f"加载设置失败: {e}")

        return self._deep_copy_defaults()

    def _deep_copy_defaults(self) -> Dict[str, Any]:
        """递归深拷贝默认设置"""
        return json.loads(json.dumps(self.DEFAULT_SETTINGS))

    def _deep_merge(self, base: Dict, override: Dict) -> None:
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存设置失败: {e}")

    def reload(self) -> None:
        """从磁盘重新加载配置文件。"""
        self._settings = self._load_settings()

    def get(self, path: str, default=None) -> Any:
        keys = path.split(".")
        value = self._settings
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value

    def set(self, path: str, value: Any) -> None:
        keys = path.split(".")
        target = self._settings
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]
        target[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        return self._settings.copy()


# ──────────────────────────────────────────────
# 自定义设定卡片
# ──────────────────────────────────────────────


class SpinSettingCard(SettingCard):
    """数值设定卡片 — 整数 SpinBox"""

    value_changed = pyqtSignal(int)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        min_val: int = 0,
        max_val: int = 100,
        step: int = 1,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.spin = SpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(180)
        self.spin.valueChanged.connect(self.value_changed.emit)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setValue(self, value: int):
        self.spin.setValue(value)

    def value(self) -> int:
        return self.spin.value()


class DoubleSpinSettingCard(SettingCard):
    """数值设定卡片 — 浮点 DoubleSpinBox"""

    value_changed = pyqtSignal(float)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        min_val: float = 0.0,
        max_val: float = 100.0,
        step: float = 0.1,
        decimals: int = 1,
        suffix: str = "",
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.spin = DoubleSpinBox(self)
        self.spin.setRange(min_val, max_val)
        self.spin.setSingleStep(step)
        self.spin.setDecimals(decimals)
        if suffix:
            self.spin.setSuffix(suffix)
        self.spin.setFixedWidth(180)
        self.spin.valueChanged.connect(self.value_changed.emit)
        self.hBoxLayout.addWidget(self.spin, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setValue(self, value: float):
        self.spin.setValue(value)

    def value(self) -> float:
        return self.spin.value()


class SwitchSettingCard(SettingCard):
    """开关设定卡片"""

    checked_changed = pyqtSignal(bool)

    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.switch = SwitchButton(self)
        self.switch.checkedChanged.connect(self.checked_changed.emit)
        self.hBoxLayout.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setChecked(self, checked: bool):
        self.switch.setChecked(checked)

    def isChecked(self) -> bool:
        return self.switch.isChecked()


class ComboSettingCard(SettingCard):
    """下拉选择设定卡片"""

    index_changed = pyqtSignal(int)

    def __init__(
        self,
        icon,
        title: str,
        content: str,
        items: list,
        parent=None,
    ):
        super().__init__(icon, title, content, parent)
        self.combo = ComboBox(self)
        self.combo.addItems(items)
        self.combo.setFixedWidth(140)
        self.combo.currentIndexChanged.connect(self.index_changed.emit)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setCurrentIndex(self, idx: int):
        self.combo.setCurrentIndex(idx)

    def currentIndex(self) -> int:
        return self.combo.currentIndex()


class BrowseSettingCard(SettingCard):
    """目录浏览设定卡片"""

    path_changed = pyqtSignal(str)

    def __init__(self, icon, title: str, content: str, parent=None):
        super().__init__(icon, title, content, parent)
        self.line = LineEdit(self)
        self.line.setPlaceholderText("点击选择...")
        self.line.setReadOnly(True)
        self.line.setFixedWidth(200)
        self.btn = PushButton("浏览", self)
        self.btn.setFixedWidth(60)
        self.btn.clicked.connect(self._on_browse)
        self.hBoxLayout.addWidget(self.line, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.btn, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_browse(self):
        dir_path = QFileDialog.getExistingDirectory(self, "选择目录", "")
        if dir_path:
            self.line.setText(dir_path)
            self.path_changed.emit(dir_path)

    def setText(self, text: str):
        self.line.setText(text)

    def text(self) -> str:
        return self.line.text()


class _KeyCaptureButton(PushButton):
    """按键捕获按钮 — 点击后进入监听模式，捕获下一次按键组合。"""

    key_captured = pyqtSignal(str)  # 捕获到的按键名称

    _init_key: str = ""

    def _postInit(self):
        super()._postInit()
        self._captured_key = ""
        self._listening = False
        self.setFixedWidth(120)
        self.setFont(QFont("Microsoft YaHei", 9))
        self.clicked.connect(self._start_listening)

    def _start_listening(self):
        self._listening = True
        self.setText("按下按键...")
        self.setStyleSheet("border: 2px solid #0078D4; border-radius: 4px;")
        self.setFocus()

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is None or not self._listening:
            super().keyPressEvent(a0)
            return
        key = a0.key()
        # 忽略单独的修饰键
        if key in (
            Qt.Key.Key_Control,
            Qt.Key.Key_Shift,
            Qt.Key.Key_Alt,
            Qt.Key.Key_Meta,
        ):
            a0.accept()
            return
        # ESC 取消捕获，不设置按键
        if key == Qt.Key.Key_Escape:
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
            self.clearFocus()
            a0.accept()
            return
        modifiers = a0.modifiers()
        key_name = _KeyCaptureButton._build_key_name(key, modifiers)
        if key_name:
            self._captured_key = key_name
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
            self.key_captured.emit(key_name)
            self.clearFocus()
        a0.accept()

    def focusOutEvent(self, a0):
        if self._listening:
            self._listening = False
            self._update_display()
            self.setStyleSheet("")
        super().focusOutEvent(a0)

    def _update_display(self):
        self.setText(self._captured_key if self._captured_key else "未设置")

    def set_key(self, key_name: str):
        self._captured_key = key_name
        self._update_display()

    def get_key(self) -> str:
        return self._captured_key

    def clear_key(self):
        self._captured_key = ""
        self._update_display()

    @staticmethod
    def _build_key_name(key, modifiers) -> Optional[str]:
        """将 Qt key + modifiers 转换为规范化字符串，如 'CTRL+F4'、'SPACE'。"""
        parts = []
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            parts.append("CTRL")
        if modifiers & Qt.KeyboardModifier.AltModifier:
            parts.append("ALT")
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            parts.append("SHIFT")

        _key_names = {
            Qt.Key.Key_Space: "SPACE",
            Qt.Key.Key_Escape: "ESCAPE",
            Qt.Key.Key_F1: "F1",
            Qt.Key.Key_F2: "F2",
            Qt.Key.Key_F3: "F3",
            Qt.Key.Key_F4: "F4",
            Qt.Key.Key_F5: "F5",
            Qt.Key.Key_F6: "F6",
            Qt.Key.Key_F7: "F7",
            Qt.Key.Key_F8: "F8",
            Qt.Key.Key_F9: "F9",
            Qt.Key.Key_F10: "F10",
            Qt.Key.Key_F11: "F11",
            Qt.Key.Key_F12: "F12",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
            Qt.Key.Key_Return: "ENTER",
            Qt.Key.Key_Enter: "ENTER",
            Qt.Key.Key_Tab: "TAB",
            Qt.Key.Key_Backspace: "BACKSPACE",
            Qt.Key.Key_Delete: "DELETE",
            Qt.Key.Key_Home: "HOME",
            Qt.Key.Key_End: "END",
            Qt.Key.Key_PageUp: "PAGEUP",
            Qt.Key.Key_PageDown: "PAGEDOWN",
            Qt.Key.Key_Insert: "INSERT",
        }
        if key in _key_names:
            parts.append(_key_names[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(key))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(key))
        else:
            return None
        return "+".join(parts)


class ShortcutSettingCard(SettingCard):
    """快捷键设定卡片 — 支持键盘监听捕获和双快捷键绑定。"""

    value_changed = pyqtSignal(str)

    def __init__(
        self, icon, title: str, content: str, default_key: str = "", parent=None
    ):
        super().__init__(icon, title, content, parent)
        # 解析默认值（可能是 "Space,A" 这样的双键位格式）
        keys = (
            [k.strip() for k in default_key.split(",") if k.strip()]
            if default_key
            else []
        )
        key1 = keys[0] if len(keys) >= 1 else default_key
        key2 = keys[1] if len(keys) >= 2 else ""

        self.btn_key1 = _KeyCaptureButton("点击设置", self)
        self.btn_key1.set_key(key1)
        self.btn_key2 = _KeyCaptureButton("点击设置", self)
        self.btn_key2.set_key(key2)

        lbl_or = QLabel("或", self)
        lbl_or.setFont(QFont("Microsoft YaHei", 9))

        self.btn_key1.key_captured.connect(self._on_key_changed)
        self.btn_key2.key_captured.connect(self._on_key_changed)

        self.hBoxLayout.addWidget(self.btn_key1, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(lbl_or, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.btn_key2, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def _on_key_changed(self, _key_name: str):
        self.value_changed.emit(self.value())

    def setValue(self, value: str):
        """设置快捷键值，支持 'Space' 或 'Space,A' 格式。"""
        keys = [k.strip() for k in value.split(",") if k.strip()] if value else []
        self.btn_key1.set_key(keys[0] if len(keys) >= 1 else "")
        self.btn_key2.set_key(keys[1] if len(keys) >= 2 else "")

    def value(self) -> str:
        """返回快捷键值，格式为 'Space' 或 'Space,A'。"""
        k1 = self.btn_key1.get_key().strip()
        k2 = self.btn_key2.get_key().strip()
        if k1 and k2:
            return f"{k1},{k2}"
        return k1 or k2

    def all_keys(self) -> list[str]:
        """返回所有已设置的快捷键列表。"""
        keys = []
        k1 = self.btn_key1.get_key().strip()
        k2 = self.btn_key2.get_key().strip()
        if k1:
            keys.append(k1.upper())
        if k2:
            keys.append(k2.upper())
        return keys

    def clear_key_by_name(self, key_name: str):
        """清除指定的快捷键（用于冲突解决）。"""
        if self.btn_key1.get_key().strip().upper() == key_name.upper():
            self.btn_key1.clear_key()
        if self.btn_key2.get_key().strip().upper() == key_name.upper():
            self.btn_key2.clear_key()


# ──────────────────────────────────────────────
# 读音词典对话框
# ──────────────────────────────────────────────


class DictionaryEditDialog(QDialog):
    """用户读音词典编辑对话框

    三列表格：启用 | 词 | 读音
    """

    def __init__(self, entries: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("读音词典")
        self.setMinimumSize(520, 420)
        self._entries = [dict(e) for e in entries]  # 深拷贝

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("读音词典")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel(
            "设置固定读音的词汇。词典中的词将优先于自动注音（最长匹配优先）。"
        )
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["启用", "词", "读音"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 140)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # 填充数据
        for entry in self._entries:
            self._add_row(
                entry.get("enabled", True),
                entry.get("word", ""),
                entry.get("reading", ""),
            )

        # 按钮行
        btn_row = QHBoxLayout()
        btn_add = PushButton("添加", self)
        btn_add.clicked.connect(self._on_add)
        btn_del = PushButton("删除选中", self)
        btn_del.clicked.connect(self._on_delete)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addStretch()
        layout.addLayout(btn_row)

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

    def _add_row(self, enabled: bool, word: str = "", reading: str = ""):
        row = self._table.rowCount()
        self._table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._table.setItem(row, 0, chk)
        self._table.setItem(row, 1, QTableWidgetItem(word))
        self._table.setItem(row, 2, QTableWidgetItem(reading))

    def _on_add(self):
        self._add_row(True, "", "")
        self._table.scrollToBottom()

    def _on_delete(self):
        rows = sorted(
            set(idx.row() for idx in self._table.selectedIndexes()), reverse=True
        )
        for row in rows:
            self._table.removeRow(row)

    def get_entries(self) -> list:
        entries = []
        for row in range(self._table.rowCount()):
            chk = self._table.item(row, 0)
            word_item = self._table.item(row, 1)
            reading_item = self._table.item(row, 2)
            word = word_item.text().strip() if word_item else ""
            reading = reading_item.text().strip() if reading_item else ""
            if not word and not reading:
                continue
            enabled = chk.checkState() == Qt.CheckState.Checked if chk else True
            entries.append({"enabled": enabled, "word": word, "reading": reading})
        return entries


# ──────────────────────────────────────────────
# Nicokara タグオプション对话框
# ──────────────────────────────────────────────


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
        from qfluentwidgets import SpinBox

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


class CalibrationCanvas(QWidget):
    """Offset 校准动画画布。"""

    def __init__(self, dialog: "CalibrationDialog", parent=None):
        super().__init__(parent)
        self._dialog = dialog
        self.setMinimumHeight(260)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, a0: QPaintEvent | None):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(18, 18, 22))

        width = max(1, self.width())
        height = max(1, self.height())
        center_x = width / 2
        center_y = height / 2

        painter.setPen(QPen(QColor(235, 70, 70), 3))
        painter.drawLine(int(center_x), 24, int(center_x), height - 24)

        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(2):
            phase = self._dialog.block_phase(index)
            x = ((phase + 0.5) % 1.0) * width
            proximity = 1.0 - min(abs(x - center_x) / max(center_x, 1.0), 1.0)
            scale = 0.55 + proximity * 0.85
            block_width = 18 + 22 * scale
            block_height = 44 + 44 * scale
            alpha = int(110 + 145 * proximity)

            painter.setBrush(QBrush(QColor(255, 255, 255, alpha)))
            rect_x = int(round(x - block_width / 2))
            rect_y = int(round(center_y - block_height / 2))
            rect_w = int(round(block_width))
            rect_h = int(round(block_height))
            painter.drawRoundedRect(
                rect_x,
                rect_y,
                rect_w,
                rect_h,
                10,
                10,
            )


class CalibrationDialog(QDialog):
    """Offset 校准弹窗。"""

    def __init__(self, parent: "SettingsInterface"):
        super().__init__(parent)
        self._settings_interface = parent
        self._sample_rate = 44100
        self._bpm = 120
        self._beat_interval = 60.0 / self._bpm
        self._start_time = _time.monotonic()
        self._next_beat_time = self._start_time
        self._schedule_version = 0
        self._running = False
        self._state_lock = threading.Lock()
        self._metronome_thread: Optional[threading.Thread] = None
        self._beat_times: list[float] = []
        self._tap_offsets_ms: list[float] = []
        self._latest_offset_ms: Optional[float] = None
        self._click_audio = self._generate_click()

        self.setWindowTitle("Offset 校准")
        self.setModal(True)
        self.resize(880, 420)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(16)

        top_row = QHBoxLayout()
        top_row.setSpacing(12)

        left_layout = QVBoxLayout()
        left_layout.setSpacing(4)
        self.lbl_latest = QLabel("最近偏移: -- ms", self)
        self.lbl_latest.setFont(QFont("Microsoft YaHei", 10))
        self.lbl_average = QLabel("平均偏移: -- ms", self)
        self.lbl_average.setFont(QFont("Microsoft YaHei", 10))
        left_layout.addWidget(self.lbl_latest)
        left_layout.addWidget(self.lbl_average)
        top_row.addLayout(left_layout)
        top_row.addStretch(1)

        right_layout = QHBoxLayout()
        right_layout.setSpacing(8)
        self.lbl_bpm = QLabel("节拍 BPM", self)
        self.lbl_bpm.setFont(QFont("Microsoft YaHei", 10))
        self.spin_bpm = SpinBox(self)
        self.spin_bpm.setRange(60, 240)
        self.spin_bpm.setValue(self._bpm)
        self.spin_bpm.setSuffix(" BPM")
        self.spin_bpm.setFixedWidth(130)
        self.spin_bpm.setFont(QFont("Microsoft YaHei", 10))
        self.btn_reset = PushButton("重置", self)
        self.btn_reset.setFont(QFont("Microsoft YaHei", 10))
        self.btn_apply = PushButton("应用", self)
        self.btn_apply.setFont(QFont("Microsoft YaHei", 10))

        right_layout.addWidget(self.lbl_bpm)
        right_layout.addWidget(self.spin_bpm)
        right_layout.addWidget(self.btn_reset)
        right_layout.addWidget(self.btn_apply)
        top_row.addLayout(right_layout)
        root.addLayout(top_row)

        self.canvas = CalibrationCanvas(self, self)
        root.addWidget(self.canvas)

        self.lbl_hint = QLabel(
            "按空格键跟拍，可持续任意次数，关闭窗口前都会保持运行", self
        )
        self.lbl_hint.setFont(QFont("Microsoft YaHei", 9))
        root.addWidget(self.lbl_hint)

        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(16)
        self.animation_timer.timeout.connect(self.canvas.update)

        self.spin_bpm.valueChanged.connect(self._on_bpm_changed)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_apply.clicked.connect(self._on_apply)

        self.spin_bpm.installEventFilter(self)
        self.btn_reset.installEventFilter(self)
        self.btn_apply.installEventFilter(self)
        self.canvas.installEventFilter(self)

    def showEvent(self, a0):
        super().showEvent(a0)
        self._start_metronome()
        self.animation_timer.start()
        QTimer.singleShot(0, self.canvas.setFocus)

    def closeEvent(self, a0):
        self.animation_timer.stop()
        self._stop_metronome()
        super().closeEvent(a0)

    def eventFilter(self, a0, a1):
        if (
            a1 is not None
            and a1.type() == a1.Type.KeyPress
            and isinstance(a1, QKeyEvent)
            and a1.key() == Qt.Key.Key_Space
            and not a1.isAutoRepeat()
        ):
            self._handle_tap()
            a1.accept()
            return True
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, a0: QKeyEvent | None):
        if a0 is not None and a0.key() == Qt.Key.Key_Space and not a0.isAutoRepeat():
            self._handle_tap()
            a0.accept()
            return
        super().keyPressEvent(a0)

    def block_phase(self, index: int) -> float:
        num_blocks = 2
        with self._state_lock:
            start_time = self._start_time
            beat_interval = self._beat_interval
        cycle_duration = beat_interval * num_blocks
        return (
            (_time.monotonic() - start_time) / cycle_duration + index / num_blocks
        ) % 1.0

    def _generate_click(self, sr=44100, duration_ms=30, freq=1000):
        n = int(sr * duration_ms / 1000)
        t = np.arange(n) / sr
        click = 0.5 * np.sin(2 * np.pi * freq * t)
        fade = np.linspace(1.0, 0.0, n)
        click *= fade
        return click.astype(np.float32)

    def _start_metronome(self):
        with self._state_lock:
            if self._running:
                return
            now = _time.monotonic()
            self._start_time = now
            self._next_beat_time = now
            self._beat_times.clear()
            self._schedule_version += 1
            self._running = True

        self._metronome_thread = threading.Thread(
            target=self._play_metronome_loop, daemon=True
        )
        self._metronome_thread.start()

    def _stop_metronome(self):
        with self._state_lock:
            self._running = False
            self._schedule_version += 1
        if self._metronome_thread and self._metronome_thread.is_alive():
            self._metronome_thread.join(timeout=1.0)
        self._metronome_thread = None

    def _play_metronome_loop(self):
        stream = None
        try:
            stream = sd.OutputStream(
                samplerate=self._sample_rate,
                channels=1,
                dtype="float32",
                latency="low",
            )
            stream.start()
        except Exception:
            stream = None

        try:
            while True:
                with self._state_lock:
                    if not self._running:
                        return
                    next_beat_time = self._next_beat_time
                    version = self._schedule_version

                now = _time.monotonic()
                wait_time = next_beat_time - now

                if wait_time > 0.003:
                    _time.sleep(min(wait_time - 0.002, 0.008))
                    continue

                # 精确自旋等待，确保判定时间与视觉中心一致
                while _time.monotonic() < next_beat_time:
                    pass

                beat_time = next_beat_time
                if stream is not None:
                    try:
                        stream.write(self._click_audio)
                    except Exception:
                        pass

                with self._state_lock:
                    if not self._running:
                        return
                    self._beat_times.append(beat_time)
                    if len(self._beat_times) > 256:
                        self._beat_times = self._beat_times[-256:]
                    if (
                        version == self._schedule_version
                        and abs(self._next_beat_time - beat_time) < 0.02
                    ):
                        self._next_beat_time = beat_time + self._beat_interval
        finally:
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass

    def _on_bpm_changed(self, value: int):
        now = _time.monotonic()
        new_interval = 60.0 / max(60, min(240, value))
        with self._state_lock:
            old_interval = self._beat_interval
            current_beats = (now - self._start_time) / old_interval
            phase = current_beats - int(current_beats)
            self._bpm = value
            self._beat_interval = new_interval
            self._start_time = now - phase * new_interval
            remaining_phase = 1.0 if phase < 0.000001 else 1.0 - phase
            self._next_beat_time = now + remaining_phase * new_interval
            self._schedule_version += 1
        self.canvas.setFocus()

    def _handle_tap(self):
        tap_time = _time.monotonic()
        offset_ms = self._calculate_tap_offset_ms(tap_time)
        if offset_ms is None:
            return
        self._latest_offset_ms = offset_ms
        self._tap_offsets_ms.append(offset_ms)
        self._update_offset_labels()
        self.canvas.setFocus()

    def _calculate_tap_offset_ms(self, tap_time: float) -> Optional[float]:
        """计算 tap 偏移量（毫秒）。

        基于视觉中心穿越时间计算：perfect_time = start_time + n * beat_interval
        正值 = 按早了（实际 tap 在完美时间之前），负值 = 按晚了。
        """
        with self._state_lock:
            start_time = self._start_time
            beat_interval = self._beat_interval
            if not self._running:
                return None

        elapsed = tap_time - start_time
        if elapsed < 0:
            return None

        # 找到最近的视觉中心穿越时间点
        n = round(elapsed / beat_interval)
        perfect_time = start_time + n * beat_interval
        return (perfect_time - tap_time) * 1000.0

    def _filtered_average_offset_ms(self) -> Optional[float]:
        if not self._tap_offsets_ms:
            return None

        values = sorted(self._tap_offsets_ms)
        filtered = values
        if len(values) >= 4:
            q1 = values[len(values) // 4]
            q3 = values[len(values) * 3 // 4]
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            filtered = [value for value in values if lower <= value <= upper]
            if not filtered:
                filtered = values

        trim_count = len(filtered) // 10
        trimmed = filtered
        if trim_count > 0 and len(filtered) - trim_count * 2 > 0:
            trimmed = filtered[trim_count : len(filtered) - trim_count]

        return sum(trimmed) / len(trimmed)

    def _format_offset_text(self, value: Optional[float]) -> str:
        if value is None:
            return "-- ms"
        return f"{round(value):+d} ms"

    def _update_offset_labels(self):
        average = self._filtered_average_offset_ms()
        self.lbl_latest.setText(
            f"最近偏移: {self._format_offset_text(self._latest_offset_ms)}"
        )
        self.lbl_average.setText(f"平均偏移: {self._format_offset_text(average)}")

    def _on_reset(self):
        self._tap_offsets_ms.clear()
        self._latest_offset_ms = None
        self._update_offset_labels()
        self.canvas.setFocus()

    def _on_apply(self):
        average = self._filtered_average_offset_ms()
        applied_offset = round(average) if average is not None else 0
        self._settings_interface.card_offset.setValue(applied_offset)
        InfoBar.success(
            title="校准完成",
            content=f"已应用 Offset：{applied_offset:+d} ms",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self._settings_interface,
        )
        self._stop_metronome()
        self.accept()


# ──────────────────────────────────────────────
# 设置界面
# ──────────────────────────────────────────────


class SettingsInterface(ScrollArea):
    """设置界面 — RhythmicaLyrics 风格分组卡片布局

    分组结构：
    1. 演奏控制 — 快进/快退量、默认音量/速度
    2. 打轴设定 — 偏移量、速度补正、预览行数
    3. Auto Check — 各字符类型的开关
    4. 界面设定 — 主题、字体大小
    5. 导出设定 — 默认格式、导出目录
    6. 快捷键
    7. 关于
    """

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._store = None
        self._settings = AppSettings()
        self._calibration_dialog = None

        # 自动保存防抖定时器
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(500)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self._loading_settings = False  # 防止加载时触发自动保存

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self._init_ui()
        self._load_current_settings()
        self._connect_auto_save_signals()

        # ScrollArea 配置
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("settingInterface")

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store

    def _connect_auto_save_signals(self):
        """将所有设置卡片的变更信号连接到自动保存。"""
        # 遍历所有属性，按类型连接信号
        for attr_name in dir(self):
            card = getattr(self, attr_name, None)
            if isinstance(card, SpinSettingCard):
                card.value_changed.connect(self._schedule_auto_save)
            elif isinstance(card, DoubleSpinSettingCard):
                card.value_changed.connect(self._schedule_auto_save)
            elif isinstance(card, SwitchSettingCard):
                card.checked_changed.connect(self._schedule_auto_save)
            elif isinstance(card, ComboSettingCard):
                card.index_changed.connect(self._schedule_auto_save)
            elif isinstance(card, BrowseSettingCard):
                card.path_changed.connect(self._schedule_auto_save)
            elif isinstance(card, ShortcutSettingCard):
                card.value_changed.connect(self._schedule_auto_save)

    def _schedule_auto_save(self, *_args):
        """防抖调度自动保存（500ms 内无新操作则保存）。"""
        if self._loading_settings:
            return
        self._auto_save_timer.start()

    def _do_auto_save(self):
        """执行自动保存：收集设置 → 保存到磁盘 → 通知变更。"""
        # 先检测快捷键冲突并自动解决
        self._resolve_shortcut_conflicts()
        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")

    def _init_ui(self):
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 20, 60, 20)

        self._init_playback_group()
        self._init_timing_group()
        self._init_calibration_group()
        self._init_auto_save_group()
        self._init_auto_check_group()
        self._init_check_behavior_group()
        self._init_dictionary_group()
        self._init_ui_group()
        self._init_export_group()
        self._init_shortcut_group()
        self._init_buttons()
        self._init_about_group()

    # ── 演奏控制 ──

    def _init_playback_group(self):
        self.playback_group = SettingCardGroup("演奏控制", self.scrollWidget)

        self.card_volume = SpinSettingCard(
            FIF.VOLUME,
            "默认音量",
            "音频加载后的初始音量",
            min_val=0,
            max_val=100,
            suffix=" %",
            parent=self.playback_group,
        )
        self.card_speed = DoubleSpinSettingCard(
            FIF.SPEED_HIGH,
            "默认速度",
            "音频加载后的初始播放速度",
            min_val=0.5,
            max_val=2.0,
            step=0.1,
            suffix=" x",
            parent=self.playback_group,
        )
        self.card_fast_forward = SpinSettingCard(
            FIF.CHEVRON_RIGHT,
            "快进量",
            "按下快进键跳过的时间",
            min_val=1000,
            max_val=30000,
            step=1000,
            suffix=" ms",
            parent=self.playback_group,
        )
        self.card_rewind = SpinSettingCard(
            FIF.LEFT_ARROW,
            "快退量",
            "按下快退键后退的时间",
            min_val=1000,
            max_val=30000,
            step=1000,
            suffix=" ms",
            parent=self.playback_group,
        )
        self.card_auto_play = SwitchSettingCard(
            FIF.PLAY,
            "自动播放",
            "加载音频文件后自动开始播放",
            parent=self.playback_group,
        )
        self.card_jump_before = SpinSettingCard(
            FIF.HISTORY,
            "双击跳转提前量",
            "双击字符跳转到该字符时间戳前的毫秒数",
            min_val=0,
            max_val=30000,
            step=500,
            suffix=" ms",
            parent=self.playback_group,
        )

        self.playback_group.addSettingCard(self.card_volume)
        self.playback_group.addSettingCard(self.card_speed)
        self.playback_group.addSettingCard(self.card_fast_forward)
        self.playback_group.addSettingCard(self.card_rewind)
        self.playback_group.addSettingCard(self.card_auto_play)
        self.playback_group.addSettingCard(self.card_jump_before)
        self.expandLayout.addWidget(self.playback_group)

    # ── 打轴设定 ──

    def _init_timing_group(self):
        self.timing_group = SettingCardGroup("打轴设定", self.scrollWidget)

        self.card_offset = SpinSettingCard(
            FIF.DATE_TIME,
            "打轴偏移",
            "补偿反应延迟（负值=提前，正值=延后）",
            min_val=-1000,
            max_val=1000,
            step=10,
            suffix=" ms",
            parent=self.timing_group,
        )
        self.card_speed_correction = SpinSettingCard(
            FIF.SPEED_MEDIUM,
            "速度补正",
            "打轴时间戳的速度修正系数",
            min_val=50,
            max_val=200,
            step=5,
            suffix=" %",
            parent=self.timing_group,
        )
        self.card_preview_lines = SpinSettingCard(
            FIF.VIEW,
            "预览行数",
            "歌词预览区域显示的行数",
            min_val=3,
            max_val=15,
            step=1,
            suffix=" 行",
            parent=self.timing_group,
        )
        self.card_export_offset = SpinSettingCard(
            FIF.HISTORY,
            "Karaoke渲染偏移及导出偏移",
            "导出时及Karaoke预览渲染的时间偏移（毫秒）",
            min_val=-5000,
            max_val=5000,
            step=10,
            suffix=" ms",
            parent=self.timing_group,
        )

        self.timing_group.addSettingCard(self.card_offset)
        self.timing_group.addSettingCard(self.card_speed_correction)
        self.timing_group.addSettingCard(self.card_preview_lines)
        self.timing_group.addSettingCard(self.card_export_offset)
        self.expandLayout.addWidget(self.timing_group)

    def _init_calibration_group(self):
        """Offset 校准。"""
        self.calibration_group = SettingCardGroup("Offset 校准", self.scrollWidget)

        cal_card = SettingCard(
            FIF.SPEED_HIGH,
            "节拍器校准",
            "打开校准弹窗，跟随节拍器按空格键测量 Offset",
            self.calibration_group,
        )

        self.btn_cal_open = PushButton("开始校准", cal_card)
        self.btn_cal_open.setFont(QFont("Microsoft YaHei", 10))
        self.btn_cal_open.clicked.connect(self._open_calibration_dialog)

        cal_card.hBoxLayout.addWidget(self.btn_cal_open, 0, Qt.AlignmentFlag.AlignRight)
        cal_card.hBoxLayout.addSpacing(16)

        self.calibration_group.addSettingCard(cal_card)
        self.expandLayout.addWidget(self.calibration_group)

    def _open_calibration_dialog(self):
        self._calibration_dialog = CalibrationDialog(self)
        self._calibration_dialog.exec()
        # 安全网：无论对话框如何关闭，确保节拍器已停止
        if self._calibration_dialog is not None:
            self._calibration_dialog._stop_metronome()
        self._calibration_dialog = None

    # ── 自动保存 ──

    def _init_auto_save_group(self):
        self.auto_save_group = SettingCardGroup("自动保存", self.scrollWidget)

        self.card_auto_save_enabled = SwitchSettingCard(
            FIF.SAVE,
            "启用定时自动保存",
            "定时将项目保存为临时文件，防止闪退丢失数据",
            parent=self.auto_save_group,
        )
        self.card_auto_save_interval = SpinSettingCard(
            FIF.HISTORY,
            "自动保存间隔",
            "每隔多少分钟自动保存一次（1~60分钟）",
            min_val=1,
            max_val=60,
            step=1,
            suffix=" 分钟",
            parent=self.auto_save_group,
        )

        self.auto_save_group.addSettingCard(self.card_auto_save_enabled)
        self.auto_save_group.addSettingCard(self.card_auto_save_interval)
        self.expandLayout.addWidget(self.auto_save_group)

    def keyPressEvent(self, a0: QKeyEvent | None):
        """设置界面按键事件。"""
        super().keyPressEvent(a0)

    def hideEvent(self, a0):
        """设置界面隐藏时关闭校准弹窗并释放资源。"""
        if self._calibration_dialog is not None:
            self._calibration_dialog.close()
        super().hideEvent(a0)

    # ── Auto Check ──

    def _init_auto_check_group(self):
        self.auto_check_group = SettingCardGroup("Auto Check", self.scrollWidget)

        self.card_check_hiragana = SwitchSettingCard(
            FIF.ACCEPT,
            "平假名",
            "自动为平假名字符生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_katakana = SwitchSettingCard(
            FIF.ACCEPT,
            "片假名",
            "自动为片假名字符生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_kanji = SwitchSettingCard(
            FIF.ACCEPT,
            "汉字",
            "自动为汉字字符生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_alphabet = SwitchSettingCard(
            FIF.ACCEPT,
            "アルファベット",
            "自动为英文字母生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_digit = SwitchSettingCard(
            FIF.ACCEPT,
            "数字",
            "自动为数字字符生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_symbol = SwitchSettingCard(
            FIF.REMOVE,
            "記号",
            "自动为符号字符生成节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_space = SwitchSettingCard(
            FIF.REMOVE,
            "空格",
            "自动为空格字符生成节奏点",
            parent=self.auto_check_group,
        )

        self.auto_check_group.addSettingCard(self.card_check_hiragana)
        self.auto_check_group.addSettingCard(self.card_check_katakana)
        self.auto_check_group.addSettingCard(self.card_check_kanji)
        self.auto_check_group.addSettingCard(self.card_check_alphabet)
        self.auto_check_group.addSettingCard(self.card_check_digit)
        self.auto_check_group.addSettingCard(self.card_check_symbol)
        self.auto_check_group.addSettingCard(self.card_check_space)
        self.expandLayout.addWidget(self.auto_check_group)

    def _init_check_behavior_group(self):
        self.card_auto_on_load = SwitchSettingCard(
            FIF.ACCEPT,
            "读取时自动check",
            "导入文本后自动执行check分析",
            parent=self.auto_check_group,
        )
        self.card_check_n = SwitchSettingCard(
            FIF.ACCEPT,
            "「ん」check",
            "对ん字符设置节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_sokuon = SwitchSettingCard(
            FIF.ACCEPT,
            "促音check",
            "对っ/ッ促音设置节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_parentheses = SwitchSettingCard(
            FIF.ACCEPT,
            "括号内文字check",
            "对括号()内的文字设置节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_empty_lines = SwitchSettingCard(
            FIF.ACCEPT,
            "空行check",
            "对空行设置节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_line_start = SwitchSettingCard(
            FIF.ACCEPT,
            "行首check",
            "在每行开头增加一个节奏点",
            parent=self.auto_check_group,
        )
        self.card_check_line_end = SwitchSettingCard(
            FIF.ACCEPT,
            "行尾check",
            "在每行末尾增加一个节奏点（用于记录行结束时间）",
            parent=self.auto_check_group,
        )
        self.card_kanji_single = SwitchSettingCard(
            FIF.ACCEPT,
            "汉字节奏点限定为1",
            "每个汉字最多只设置1个节奏点",
            parent=self.auto_check_group,
        )
        self.card_space_after_jp = SwitchSettingCard(
            FIF.ACCEPT,
            "日语后空格check",
            "日语字符后的空格设置check点",
            parent=self.auto_check_group,
        )
        self.card_space_after_alpha = SwitchSettingCard(
            FIF.ACCEPT,
            "字母后空格check",
            "英文字母后的空格设置check点",
            parent=self.auto_check_group,
        )
        self.card_space_after_symbol = SwitchSettingCard(
            FIF.ACCEPT,
            "符号数字后空格check",
            "符号或数字后的空格设置check点",
            parent=self.auto_check_group,
        )
        self.card_small_kana = SwitchSettingCard(
            FIF.ACCEPT,
            "小写假名check",
            "对小写假名（ぁ、ぃ、ゃ、ゅ、ょ等）设置节奏点",
            parent=self.auto_check_group,
        )
        self.card_space_as_line_end = SwitchSettingCard(
            FIF.ACCEPT,
            "空格视为句尾",
            "字符后跟空格时视为句尾，额外增加一个节奏点",
            parent=self.auto_check_group,
        )

        self.auto_check_group.addSettingCard(self.card_auto_on_load)
        self.auto_check_group.addSettingCard(self.card_check_n)
        self.auto_check_group.addSettingCard(self.card_check_sokuon)
        self.auto_check_group.addSettingCard(self.card_small_kana)
        self.auto_check_group.addSettingCard(self.card_check_parentheses)
        self.auto_check_group.addSettingCard(self.card_check_empty_lines)
        self.auto_check_group.addSettingCard(self.card_check_line_start)
        self.auto_check_group.addSettingCard(self.card_check_line_end)
        self.auto_check_group.addSettingCard(self.card_kanji_single)
        self.auto_check_group.addSettingCard(self.card_space_after_jp)
        self.auto_check_group.addSettingCard(self.card_space_after_alpha)
        self.auto_check_group.addSettingCard(self.card_space_after_symbol)
        self.auto_check_group.addSettingCard(self.card_space_as_line_end)

    # ── 读音词典 ──

    def _init_dictionary_group(self):
        self.dictionary_group = SettingCardGroup("读音词典", self.scrollWidget)

        dict_card = SettingCard(
            FIF.DICTIONARY,
            "自定义读音",
            "固定特定词汇的注音读法（最长匹配优先）",
            self.dictionary_group,
        )
        self.btn_open_dict = PushButton("编辑词典", dict_card)
        self.btn_open_dict.setFont(QFont("Microsoft YaHei", 10))
        self.btn_open_dict.clicked.connect(self._on_open_dictionary)
        dict_card.hBoxLayout.addWidget(
            self.btn_open_dict, 0, Qt.AlignmentFlag.AlignRight
        )
        dict_card.hBoxLayout.addSpacing(16)
        self.dict_card = dict_card

        self.dictionary_group.addSettingCard(self.dict_card)
        self.expandLayout.addWidget(self.dictionary_group)

    def _on_open_dictionary(self):
        entries = self._settings.get("ruby_dictionary.entries", [])
        dialog = DictionaryEditDialog(entries, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            new_entries = dialog.get_entries()
            self._settings.set("ruby_dictionary.entries", new_entries)
            self._settings.save()

    # ── 界面设定 ──

    def _init_ui_group(self):
        self.ui_group = SettingCardGroup("界面设定", self.scrollWidget)

        self.card_theme = ComboSettingCard(
            FIF.BRUSH,
            "主题",
            "切换应用主题颜色方案",
            items=["浅色", "深色", "自动"],
            parent=self.ui_group,
        )
        self.card_font_size = SpinSettingCard(
            FIF.FONT_SIZE,
            "歌词字体大小",
            "歌词预览区域的字体像素大小",
            min_val=12,
            max_val=48,
            step=2,
            suffix=" px",
            parent=self.ui_group,
        )

        self.ui_group.addSettingCard(self.card_theme)
        self.ui_group.addSettingCard(self.card_font_size)
        self.expandLayout.addWidget(self.ui_group)

    # ── 导出设定 ──

    def _init_export_group(self):
        self.export_group = SettingCardGroup("导出设定", self.scrollWidget)

        self.card_default_format = ComboSettingCard(
            FIF.SHARE,
            "默认导出格式",
            "导出歌词时的默认文件格式",
            items=["LRC", "KRA", "TXT", "ASS", "Nicokara"],
            parent=self.export_group,
        )
        self.card_export_dir = BrowseSettingCard(
            FIF.FOLDER,
            "默认导出目录",
            "导出文件的默认保存位置",
            parent=self.export_group,
        )

        self.export_group.addSettingCard(self.card_default_format)
        self.export_group.addSettingCard(self.card_export_dir)
        self.expandLayout.addWidget(self.export_group)

    # ── 快捷键 ──

    def _init_shortcut_group(self):
        self.shortcut_group = SettingCardGroup("快捷键", self.scrollWidget)

        self.card_sc_tag = ShortcutSettingCard(
            FIF.PLAY, "打轴键", "打轴操作的按键", "Space", parent=self.shortcut_group
        )
        self.card_sc_play_pause = ShortcutSettingCard(
            FIF.PLAY, "播放/暂停", "切换播放和暂停", "A", parent=self.shortcut_group
        )
        self.card_sc_stop = ShortcutSettingCard(
            FIF.PAUSE, "停止", "停止播放", "S", parent=self.shortcut_group
        )
        self.card_sc_seek_back = ShortcutSettingCard(
            FIF.LEFT_ARROW, "后退", "后退跳转", "Z", parent=self.shortcut_group
        )
        self.card_sc_seek_forward = ShortcutSettingCard(
            FIF.CHEVRON_RIGHT, "前进", "前进跳转", "X", parent=self.shortcut_group
        )
        self.card_sc_speed_down = ShortcutSettingCard(
            FIF.SPEED_OFF, "减速", "降低播放速度", "Q", parent=self.shortcut_group
        )
        self.card_sc_speed_up = ShortcutSettingCard(
            FIF.SPEED_HIGH, "加速", "提高播放速度", "W", parent=self.shortcut_group
        )
        self.card_sc_edit_ruby = ShortcutSettingCard(
            FIF.EDIT, "注音编辑", "编辑当前字符注音", "F2", parent=self.shortcut_group
        )
        self.card_sc_toggle_cp = ShortcutSettingCard(
            FIF.PIN,
            "增加节奏点",
            "增加当前字符的节奏点数量（Alt+该键减少）",
            "F4",
            parent=self.shortcut_group,
        )
        self.card_sc_volume_up = ShortcutSettingCard(
            FIF.VOLUME, "音量增大", "增大播放音量", "UP", parent=self.shortcut_group
        )
        self.card_sc_volume_down = ShortcutSettingCard(
            FIF.MUTE, "音量减小", "减小播放音量", "DOWN", parent=self.shortcut_group
        )
        self.card_sc_nav_prev = ShortcutSettingCard(
            FIF.LEFT_ARROW,
            "上一行",
            "移动到上一歌词行",
            "LEFT",
            parent=self.shortcut_group,
        )
        self.card_sc_nav_next = ShortcutSettingCard(
            FIF.RIGHT_ARROW,
            "下一行",
            "移动到下一歌词行",
            "RIGHT",
            parent=self.shortcut_group,
        )
        self.card_sc_clear = ShortcutSettingCard(
            FIF.DELETE,
            "清除时间标签",
            "清除当前行时间标签",
            "BACKSPACE",
            parent=self.shortcut_group,
        )
        self.card_sc_toggle_line_end = ShortcutSettingCard(
            FIF.TAG,
            "切换句尾",
            "切换当前字符的句尾标记",
            "F5",
            parent=self.shortcut_group,
        )

        self.shortcut_group.addSettingCard(self.card_sc_tag)
        self.shortcut_group.addSettingCard(self.card_sc_play_pause)
        self.shortcut_group.addSettingCard(self.card_sc_stop)
        self.shortcut_group.addSettingCard(self.card_sc_seek_back)
        self.shortcut_group.addSettingCard(self.card_sc_seek_forward)
        self.shortcut_group.addSettingCard(self.card_sc_speed_down)
        self.shortcut_group.addSettingCard(self.card_sc_speed_up)
        self.shortcut_group.addSettingCard(self.card_sc_edit_ruby)
        self.shortcut_group.addSettingCard(self.card_sc_toggle_cp)
        self.shortcut_group.addSettingCard(self.card_sc_volume_up)
        self.shortcut_group.addSettingCard(self.card_sc_volume_down)
        self.shortcut_group.addSettingCard(self.card_sc_nav_prev)
        self.shortcut_group.addSettingCard(self.card_sc_nav_next)
        self.shortcut_group.addSettingCard(self.card_sc_clear)
        self.shortcut_group.addSettingCard(self.card_sc_toggle_line_end)
        self.expandLayout.addWidget(self.shortcut_group)

    def _get_all_shortcut_cards(self) -> list[tuple[str, "ShortcutSettingCard"]]:
        """返回 (功能名称, 卡片) 列表。"""
        return [
            ("打轴键", self.card_sc_tag),
            ("播放/暂停", self.card_sc_play_pause),
            ("停止", self.card_sc_stop),
            ("后退", self.card_sc_seek_back),
            ("前进", self.card_sc_seek_forward),
            ("减速", self.card_sc_speed_down),
            ("加速", self.card_sc_speed_up),
            ("注音编辑", self.card_sc_edit_ruby),
            ("增加节奏点", self.card_sc_toggle_cp),
            ("音量增大", self.card_sc_volume_up),
            ("音量减小", self.card_sc_volume_down),
            ("上一行", self.card_sc_nav_prev),
            ("下一行", self.card_sc_nav_next),
            ("清除时间标签", self.card_sc_clear),
            ("切换句尾", self.card_sc_toggle_line_end),
        ]

    def _resolve_shortcut_conflicts(self) -> list[str]:
        """检测并解决快捷键冲突。返回冲突描述列表。"""
        cards = self._get_all_shortcut_cards()
        # 构建 key → (功能名, 卡片) 映射，按卡片顺序后者覆盖前者
        key_owners: dict[str, tuple[str, ShortcutSettingCard]] = {}
        conflicts: list[str] = []
        for name, card in cards:
            for key in card.all_keys():
                if not key:
                    continue
                if key in key_owners:
                    old_name, old_card = key_owners[key]
                    # 清除被冲突方的该按键
                    old_card.clear_key_by_name(key)
                    conflicts.append(
                        f"「{name}」占用了按键 {key}，「{old_name}」的该按键已被清除"
                    )
                key_owners[key] = (name, card)
        return conflicts

    # ── 操作按钮 ──

    def _init_buttons(self):
        btn_widget = QWidget(self.scrollWidget)
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 0, 0, 0)

        self.btn_save = PrimaryPushButton("保存设置", btn_widget)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumHeight(36)
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)

        self.btn_reset = PushButton("重置为默认设置", btn_widget)
        self.btn_reset.setIcon(FIF.DELETE)
        self.btn_reset.setMinimumHeight(36)
        self.btn_reset.clicked.connect(self._reset_settings)
        btn_layout.addWidget(self.btn_reset)

        btn_layout.addStretch()
        self.expandLayout.addWidget(btn_widget)

    # ── 关于 ──

    def _init_about_group(self):
        self.about_group = SettingCardGroup("关于", self.scrollWidget)

        about_card = SettingCard(
            FIF.INFO,
            "StrangeUtaGame - 歌词打轴软件",
            "版本 1.0.0 | 由 RhythmicaLyrics 启发",
            self.about_group,
        )
        self.about_group.addSettingCard(about_card)

        link_card = SettingCard(
            FIF.GITHUB,
            "GitHub",
            "https://github.com/Xuan-cc/StrangeUtaGame",
            self.about_group,
        )
        self.about_group.addSettingCard(link_card)

        # 配置文件路径
        self._path_card = SettingCard(
            FIF.FOLDER,
            "配置文件位置",
            str(self._settings._config_path),
            self.about_group,
        )
        btn_open_config = PushButton("打开目录", self._path_card)
        btn_open_config.setFont(QFont("Microsoft YaHei", 10))
        btn_open_config.clicked.connect(self._open_config_dir)
        self._path_card.hBoxLayout.addWidget(
            btn_open_config, 0, Qt.AlignmentFlag.AlignRight
        )
        btn_change_config = PushButton("更改位置", self._path_card)
        btn_change_config.setFont(QFont("Microsoft YaHei", 10))
        btn_change_config.clicked.connect(self._change_config_dir)
        self._path_card.hBoxLayout.addWidget(
            btn_change_config, 0, Qt.AlignmentFlag.AlignRight
        )
        self._path_card.hBoxLayout.addSpacing(16)

        self.about_group.addSettingCard(self._path_card)

        self.expandLayout.addWidget(self.about_group)

    def _open_config_dir(self):
        """打开配置文件所在目录。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices

        config_dir = self._settings._config_path.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(config_dir)))

    def _change_config_dir(self):
        """更改配置文件存储位置。"""
        new_dir = QFileDialog.getExistingDirectory(
            self, "选择配置文件存储目录", str(self._settings._config_path.parent)
        )
        if not new_dir:
            return

        new_dir_path = Path(new_dir)
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"

        if new_dir_path.resolve() == program_dir.resolve():
            # 回到默认位置 — 删除重定向文件
            try:
                if redirect_file.exists():
                    redirect_file.unlink()
            except Exception:
                pass
        else:
            try:
                redirect_file.write_text(str(new_dir_path), encoding="utf-8")
            except Exception as e:
                InfoBar.error(
                    title="更改失败",
                    content=f"无法写入重定向文件: {e}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
                return

        old_path = self._settings._config_path
        new_path = new_dir_path / "config.json"

        # 复制配置到新位置
        if old_path.exists() and old_path != new_path:
            try:
                import shutil

                new_dir_path.mkdir(exist_ok=True)
                shutil.copy2(str(old_path), str(new_path))
            except Exception as e:
                InfoBar.warning(
                    title="配置复制失败",
                    content=f"请手动复制配置文件: {e}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

        self._settings._config_path = new_path
        self._path_card.setContent(str(new_path))
        InfoBar.success(
            title="配置位置已更改",
            content=f"配置文件将保存到: {new_path}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )

    # ==================== 数据绑定 ====================

    def _load_current_settings(self):
        """从 AppSettings 加载所有设置到 UI 控件"""
        self._loading_settings = True
        try:
            self._load_current_settings_inner()
        finally:
            self._loading_settings = False

    def _load_current_settings_inner(self):
        """实际加载逻辑（被 _loading_settings 守护）"""
        # 演奏控制
        self.card_volume.setValue(self._settings.get("audio.default_volume", 80))
        self.card_speed.setValue(self._settings.get("audio.default_speed", 1.0))
        self.card_fast_forward.setValue(
            self._settings.get("timing.fast_forward_ms", 5000)
        )
        self.card_rewind.setValue(self._settings.get("timing.rewind_ms", 5000))
        self.card_auto_play.setChecked(
            self._settings.get("audio.auto_play_on_load", False)
        )
        self.card_jump_before.setValue(
            self._settings.get("timing.jump_before_ms", 3000)
        )

        # 打轴设定
        self.card_offset.setValue(self._settings.get("timing.tag_offset_ms", 0))
        self.card_speed_correction.setValue(
            self._settings.get("timing.speed_correction", 100)
        )
        self.card_preview_lines.setValue(
            self._settings.get("timing.show_preview_lines", 5)
        )

        # Auto Check
        self.card_check_hiragana.setChecked(
            self._settings.get("auto_check.hiragana", True)
        )
        self.card_check_katakana.setChecked(
            self._settings.get("auto_check.katakana", True)
        )
        self.card_check_kanji.setChecked(self._settings.get("auto_check.kanji", True))
        self.card_check_alphabet.setChecked(
            self._settings.get("auto_check.alphabet", False)
        )
        self.card_check_digit.setChecked(self._settings.get("auto_check.digit", False))
        self.card_check_symbol.setChecked(
            self._settings.get("auto_check.symbol", False)
        )
        self.card_check_space.setChecked(self._settings.get("auto_check.space", False))
        self.card_auto_on_load.setChecked(
            self._settings.get("auto_check.auto_on_load", True)
        )
        self.card_check_n.setChecked(self._settings.get("auto_check.check_n", False))
        self.card_check_sokuon.setChecked(
            self._settings.get("auto_check.check_sokuon", False)
        )
        self.card_check_parentheses.setChecked(
            self._settings.get("auto_check.check_parentheses", True)
        )
        self.card_check_empty_lines.setChecked(
            self._settings.get("auto_check.check_empty_lines", False)
        )
        self.card_check_line_start.setChecked(
            self._settings.get("auto_check.check_line_start", False)
        )
        self.card_check_line_end.setChecked(
            self._settings.get("auto_check.check_line_end", True)
        )
        self.card_kanji_single.setChecked(
            self._settings.get("auto_check.kanji_single_check", False)
        )
        self.card_space_after_jp.setChecked(
            self._settings.get("auto_check.space_after_japanese", True)
        )
        self.card_space_after_alpha.setChecked(
            self._settings.get("auto_check.space_after_alphabet", True)
        )
        self.card_space_after_symbol.setChecked(
            self._settings.get("auto_check.space_after_symbol", True)
        )
        self.card_small_kana.setChecked(
            self._settings.get("auto_check.small_kana", False)
        )
        self.card_space_as_line_end.setChecked(
            self._settings.get("auto_check.check_space_as_line_end", True)
        )

        # 界面设定
        theme = self._settings.get("ui.theme", "light")
        theme_idx = {"light": 0, "dark": 1, "auto": 2}.get(theme, 0)
        self.card_theme.setCurrentIndex(theme_idx)
        self.card_font_size.setValue(self._settings.get("ui.font_size", 24))

        # 导出设定
        fmt = self._settings.get("export.default_format", "LRC")
        fmt_idx = {"LRC": 0, "KRA": 1, "TXT": 2, "ASS": 3, "Nicokara": 4}.get(fmt, 0)
        self.card_default_format.setCurrentIndex(fmt_idx)
        export_dir = self._settings.get("export.last_export_dir", "")
        if export_dir:
            self.card_export_dir.setText(export_dir)
        self.card_export_offset.setValue(self._settings.get("export.offset_ms", 0))

        # 自动保存
        self.card_auto_save_enabled.setChecked(
            self._settings.get("auto_save.enabled", True)
        )
        self.card_auto_save_interval.setValue(
            self._settings.get("auto_save.interval_minutes", 5)
        )

        # 快捷键
        self.card_sc_tag.setValue(self._settings.get("shortcuts.tag_now", "Space"))
        self.card_sc_play_pause.setValue(
            self._settings.get("shortcuts.play_pause", "A")
        )
        self.card_sc_stop.setValue(self._settings.get("shortcuts.stop", "S"))
        self.card_sc_seek_back.setValue(self._settings.get("shortcuts.seek_back", "Z"))
        self.card_sc_seek_forward.setValue(
            self._settings.get("shortcuts.seek_forward", "X")
        )
        self.card_sc_speed_down.setValue(
            self._settings.get("shortcuts.speed_down", "Q")
        )
        self.card_sc_speed_up.setValue(self._settings.get("shortcuts.speed_up", "W"))
        self.card_sc_edit_ruby.setValue(self._settings.get("shortcuts.edit_ruby", "F2"))
        self.card_sc_toggle_cp.setValue(
            self._settings.get("shortcuts.toggle_checkpoint", "F4")
        )
        self.card_sc_volume_up.setValue(self._settings.get("shortcuts.volume_up", "UP"))
        self.card_sc_volume_down.setValue(
            self._settings.get("shortcuts.volume_down", "DOWN")
        )
        self.card_sc_nav_prev.setValue(
            self._settings.get("shortcuts.nav_prev_line", "LEFT")
        )
        self.card_sc_nav_next.setValue(
            self._settings.get("shortcuts.nav_next_line", "RIGHT")
        )
        self.card_sc_clear.setValue(
            self._settings.get("shortcuts.clear_tags", "BACKSPACE")
        )
        self.card_sc_toggle_line_end.setValue(
            self._settings.get("shortcuts.toggle_line_end", "F5")
        )

    def _collect_settings(self):
        """从 UI 控件收集所有设置并写入 AppSettings"""
        # 演奏控制
        self._settings.set("audio.default_volume", self.card_volume.value())
        self._settings.set("audio.default_speed", self.card_speed.value())
        self._settings.set("audio.auto_play_on_load", self.card_auto_play.isChecked())
        self._settings.set("timing.fast_forward_ms", self.card_fast_forward.value())
        self._settings.set("timing.rewind_ms", self.card_rewind.value())
        self._settings.set("timing.jump_before_ms", self.card_jump_before.value())

        # 打轴设定
        self._settings.set("timing.tag_offset_ms", self.card_offset.value())
        self._settings.set(
            "timing.speed_correction", self.card_speed_correction.value()
        )
        self._settings.set("timing.show_preview_lines", self.card_preview_lines.value())

        # Auto Check
        self._settings.set("auto_check.hiragana", self.card_check_hiragana.isChecked())
        self._settings.set("auto_check.katakana", self.card_check_katakana.isChecked())
        self._settings.set("auto_check.kanji", self.card_check_kanji.isChecked())
        self._settings.set("auto_check.alphabet", self.card_check_alphabet.isChecked())
        self._settings.set("auto_check.digit", self.card_check_digit.isChecked())
        self._settings.set("auto_check.symbol", self.card_check_symbol.isChecked())
        self._settings.set("auto_check.space", self.card_check_space.isChecked())
        self._settings.set(
            "auto_check.auto_on_load", self.card_auto_on_load.isChecked()
        )
        self._settings.set("auto_check.check_n", self.card_check_n.isChecked())
        self._settings.set(
            "auto_check.check_sokuon", self.card_check_sokuon.isChecked()
        )
        self._settings.set(
            "auto_check.check_parentheses", self.card_check_parentheses.isChecked()
        )
        self._settings.set(
            "auto_check.check_empty_lines", self.card_check_empty_lines.isChecked()
        )
        self._settings.set(
            "auto_check.check_line_start", self.card_check_line_start.isChecked()
        )
        self._settings.set(
            "auto_check.check_line_end", self.card_check_line_end.isChecked()
        )
        self._settings.set(
            "auto_check.kanji_single_check", self.card_kanji_single.isChecked()
        )
        self._settings.set(
            "auto_check.space_after_japanese", self.card_space_after_jp.isChecked()
        )
        self._settings.set(
            "auto_check.space_after_alphabet",
            self.card_space_after_alpha.isChecked(),
        )
        self._settings.set(
            "auto_check.space_after_symbol", self.card_space_after_symbol.isChecked()
        )
        self._settings.set("auto_check.small_kana", self.card_small_kana.isChecked())
        self._settings.set(
            "auto_check.check_space_as_line_end",
            self.card_space_as_line_end.isChecked(),
        )

        # 界面设定
        theme_map = {0: "light", 1: "dark", 2: "auto"}
        self._settings.set(
            "ui.theme", theme_map.get(self.card_theme.currentIndex(), "light")
        )
        self._settings.set("ui.font_size", self.card_font_size.value())

        # 导出设定
        fmt_map = {0: "LRC", 1: "KRA", 2: "TXT", 3: "ASS", 4: "Nicokara"}
        self._settings.set(
            "export.default_format",
            fmt_map.get(self.card_default_format.currentIndex(), "LRC"),
        )
        export_dir = self.card_export_dir.text()
        if export_dir:
            self._settings.set("export.last_export_dir", export_dir)
        self._settings.set("export.offset_ms", self.card_export_offset.value())

        # 自动保存
        self._settings.set("auto_save.enabled", self.card_auto_save_enabled.isChecked())
        self._settings.set(
            "auto_save.interval_minutes", self.card_auto_save_interval.value()
        )

        # 快捷键
        self._settings.set("shortcuts.tag_now", self.card_sc_tag.value())
        self._settings.set("shortcuts.play_pause", self.card_sc_play_pause.value())
        self._settings.set("shortcuts.stop", self.card_sc_stop.value())
        self._settings.set("shortcuts.seek_back", self.card_sc_seek_back.value())
        self._settings.set("shortcuts.seek_forward", self.card_sc_seek_forward.value())
        self._settings.set("shortcuts.speed_down", self.card_sc_speed_down.value())
        self._settings.set("shortcuts.speed_up", self.card_sc_speed_up.value())
        self._settings.set("shortcuts.edit_ruby", self.card_sc_edit_ruby.value())
        self._settings.set(
            "shortcuts.toggle_checkpoint", self.card_sc_toggle_cp.value()
        )
        self._settings.set("shortcuts.volume_up", self.card_sc_volume_up.value())
        self._settings.set("shortcuts.volume_down", self.card_sc_volume_down.value())
        self._settings.set("shortcuts.nav_prev_line", self.card_sc_nav_prev.value())
        self._settings.set("shortcuts.nav_next_line", self.card_sc_nav_next.value())
        self._settings.set("shortcuts.clear_tags", self.card_sc_clear.value())
        self._settings.set(
            "shortcuts.toggle_line_end", self.card_sc_toggle_line_end.value()
        )

    # ==================== 操作 ====================

    def _on_save(self):
        # 先检测快捷键冲突并自动解决
        conflicts = self._resolve_shortcut_conflicts()
        if conflicts:
            for msg in conflicts:
                InfoBar.warning(
                    title="快捷键冲突",
                    content=msg,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")

        InfoBar.success(
            title="设置已保存",
            content="所有设置已保存到配置文件",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _reset_settings(self):
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "确认重置",
            "确定要将所有设置重置为默认值吗？\n这将覆盖您当前的设置。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                if self._settings._config_path.exists():
                    self._settings._config_path.unlink()
                self._settings = AppSettings()
                self._load_current_settings()

                InfoBar.success(
                    title="设置已重置",
                    content="所有设置已恢复为默认值",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            except Exception as e:
                InfoBar.error(
                    title="重置失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

    def get_settings(self) -> AppSettings:
        return self._settings

    def reload_from_disk(self):
        """从磁盘重新加载配置并刷新 UI 控件。"""
        self._settings.reload()
        self._load_current_settings()
