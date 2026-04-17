"""设置界面 — RhythmicaLyrics 风格层级设定系统。

使用 qfluentwidgets 的 SettingCardGroup + ExpandLayout 实现分组卡片布局。
所有设置通过 AppSettings 统一管理，保存到 ~/.strange_uta_game/config.json。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QDialog,
    QTableWidget,
    QTableWidgetItem,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
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
            "tag_offset_ms": 0,
            "speed_correction": 100,
            "fast_forward_ms": 5000,
            "rewind_ms": 5000,
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
            "clear_tags": "ESCAPE",
        },
    }

    def __init__(self, config_path: Optional[str] = None):
        if config_path is None:
            config_dir = Path.home() / ".strange_uta_game"
            config_dir.mkdir(exist_ok=True)
            self._config_path = config_dir / "config.json"
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


class ShortcutSettingCard(SettingCard):
    """快捷键设定卡片"""

    value_changed = pyqtSignal(str)

    def __init__(
        self, icon, title: str, content: str, default_key: str = "", parent=None
    ):
        super().__init__(icon, title, content, parent)
        self.line = LineEdit(self)
        self.line.setText(default_key)
        self.line.setFixedWidth(100)
        self.line.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.line.editingFinished.connect(
            lambda: self.value_changed.emit(self.line.text())
        )
        self.hBoxLayout.addWidget(self.line, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def setValue(self, value: str):
        self.line.setText(value)

    def value(self) -> str:
        return self.line.text().strip()


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
        self._table.horizontalHeader().setStretchLastSection(True)
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

        def _row(label_text: str) -> tuple:
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


# ──────────────────────────────────────────────
# 设置界面
# ──────────────────────────────────────────────


class SettingsInterface(ScrollArea):
    """设置界面 — RhythmicaLyrics 风格分组卡片布局

    分组结构：
    1. 演奏控制 — 快进/快退量、默认音量/速度
    2. 打轴设定 — 偏移量、速度补正、预览行数
    3. 自动打勾 — 各字符类型的开关
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

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self._init_ui()
        self._load_current_settings()

        # ScrollArea 配置
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("settingInterface")

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store

    def _init_ui(self):
        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(60, 20, 60, 20)

        self._init_playback_group()
        self._init_timing_group()
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

        self.playback_group.addSettingCard(self.card_volume)
        self.playback_group.addSettingCard(self.card_speed)
        self.playback_group.addSettingCard(self.card_fast_forward)
        self.playback_group.addSettingCard(self.card_rewind)
        self.playback_group.addSettingCard(self.card_auto_play)
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

        self.timing_group.addSettingCard(self.card_offset)
        self.timing_group.addSettingCard(self.card_speed_correction)
        self.timing_group.addSettingCard(self.card_preview_lines)
        self.expandLayout.addWidget(self.timing_group)

    # ── 自動打勾 ──

    def _init_auto_check_group(self):
        self.auto_check_group = SettingCardGroup(
            "自動打勾 — 打勾規則", self.scrollWidget
        )

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
        self.check_behavior_group = SettingCardGroup("打勾方式", self.scrollWidget)

        self.card_auto_on_load = SwitchSettingCard(
            FIF.ACCEPT,
            "读取时自动打勾",
            "导入文本后自动执行打勾分析",
            parent=self.check_behavior_group,
        )
        self.card_check_n = SwitchSettingCard(
            FIF.ACCEPT,
            "「ん」打勾",
            "对ん字符设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_check_sokuon = SwitchSettingCard(
            FIF.ACCEPT,
            "促音打勾",
            "对っ/ッ促音设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_check_parentheses = SwitchSettingCard(
            FIF.ACCEPT,
            "括号内文字打勾",
            "对括号()内的文字设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_check_empty_lines = SwitchSettingCard(
            FIF.ACCEPT,
            "空行打勾",
            "对空行设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_check_line_start = SwitchSettingCard(
            FIF.ACCEPT,
            "行首打勾",
            "在每行开头增加一个节奏点",
            parent=self.check_behavior_group,
        )
        self.card_check_line_end = SwitchSettingCard(
            FIF.ACCEPT,
            "行尾打勾",
            "在每行末尾增加一个节奏点（用于记录行结束时间）",
            parent=self.check_behavior_group,
        )
        self.card_kanji_single = SwitchSettingCard(
            FIF.ACCEPT,
            "汉字节奏点限定为1",
            "每个汉字最多只设置1个节奏点",
            parent=self.check_behavior_group,
        )
        self.card_space_after_jp = SwitchSettingCard(
            FIF.ACCEPT,
            "日语后空格打勾",
            "日语字符后的空格设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_space_after_alpha = SwitchSettingCard(
            FIF.ACCEPT,
            "字母后空格打勾",
            "英文字母后的空格设置节奏点",
            parent=self.check_behavior_group,
        )
        self.card_space_after_symbol = SwitchSettingCard(
            FIF.ACCEPT,
            "符号数字后空格打勾",
            "符号或数字后的空格设置节奏点",
            parent=self.check_behavior_group,
        )

        self.check_behavior_group.addSettingCard(self.card_auto_on_load)
        self.check_behavior_group.addSettingCard(self.card_check_n)
        self.check_behavior_group.addSettingCard(self.card_check_sokuon)
        self.check_behavior_group.addSettingCard(self.card_check_parentheses)
        self.check_behavior_group.addSettingCard(self.card_check_empty_lines)
        self.check_behavior_group.addSettingCard(self.card_check_line_start)
        self.check_behavior_group.addSettingCard(self.card_check_line_end)
        self.check_behavior_group.addSettingCard(self.card_kanji_single)
        self.check_behavior_group.addSettingCard(self.card_space_after_jp)
        self.check_behavior_group.addSettingCard(self.card_space_after_alpha)
        self.check_behavior_group.addSettingCard(self.card_space_after_symbol)
        self.expandLayout.addWidget(self.check_behavior_group)

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
            "ESCAPE",
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
        self.expandLayout.addWidget(self.shortcut_group)

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
        path_card = SettingCard(
            FIF.FOLDER,
            "配置文件位置",
            str(self._settings._config_path),
            self.about_group,
        )
        self.about_group.addSettingCard(path_card)

        self.expandLayout.addWidget(self.about_group)

    # ==================== 数据绑定 ====================

    def _load_current_settings(self):
        """从 AppSettings 加载所有设置到 UI 控件"""
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

        # 打轴设定
        self.card_offset.setValue(self._settings.get("timing.tag_offset_ms", 0))
        self.card_speed_correction.setValue(
            self._settings.get("timing.speed_correction", 100)
        )
        self.card_preview_lines.setValue(
            self._settings.get("timing.show_preview_lines", 5)
        )

        # 自動打勾
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
            self._settings.get("shortcuts.clear_tags", "ESCAPE")
        )

    def _collect_settings(self):
        """从 UI 控件收集所有设置并写入 AppSettings"""
        # 演奏控制
        self._settings.set("audio.default_volume", self.card_volume.value())
        self._settings.set("audio.default_speed", self.card_speed.value())
        self._settings.set("audio.auto_play_on_load", self.card_auto_play.isChecked())
        self._settings.set("timing.fast_forward_ms", self.card_fast_forward.value())
        self._settings.set("timing.rewind_ms", self.card_rewind.value())

        # 打轴设定
        self._settings.set("timing.tag_offset_ms", self.card_offset.value())
        self._settings.set(
            "timing.speed_correction", self.card_speed_correction.value()
        )
        self._settings.set("timing.show_preview_lines", self.card_preview_lines.value())

        # 自動打勾
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

    # ==================== 操作 ====================

    def _on_save(self):
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
