"""设置界面。

提供应用设置管理界面。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QComboBox,
    QSpinBox,
    QCheckBox,
    QLineEdit,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QDialog,
    QDialogButtonBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    ComboBox,
    SpinBox,
    LineEdit,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
)

from typing import Optional, Dict, Any
from pathlib import Path
import json


class AppSettings:
    """应用设置管理

    管理应用级别的配置。
    """

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
        "shortcuts": {
            "play_pause": "A",
            "stop": "S",
            "tag_now": "Space",
            "seek_back": "Z",
            "seek_forward": "X",
            "speed_down": "Q",
            "speed_up": "W",
        },
    }

    def __init__(self, config_path: str = None):
        """
        Args:
            config_path: 配置文件路径（默认为用户目录下的 .strange_uta_game/config.json）
        """
        if config_path is None:
            config_dir = Path.home() / ".strange_uta_game"
            config_dir.mkdir(exist_ok=True)
            config_path = config_dir / "config.json"

        self._config_path = Path(config_path)
        self._settings = self._load_settings()

    def _load_settings(self) -> Dict[str, Any]:
        """加载设置"""
        if self._config_path.exists():
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    # 合并默认设置
                    settings = self.DEFAULT_SETTINGS.copy()
                    self._deep_merge(settings, loaded)
                    return settings
            except Exception as e:
                print(f"加载设置失败: {e}")

        return self.DEFAULT_SETTINGS.copy()

    def _deep_merge(self, base: Dict, override: Dict) -> None:
        """深度合并字典"""
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    def save(self) -> None:
        """保存设置到文件"""
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"保存设置失败: {e}")

    def get(self, path: str, default=None) -> Any:
        """获取设置值

        Args:
            path: 路径，如 "audio.default_volume"
            default: 默认值

        Returns:
            设置值
        """
        keys = path.split(".")
        value = self._settings

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value

    def set(self, path: str, value: Any) -> None:
        """设置值

        Args:
            path: 路径，如 "audio.default_volume"
            value: 值
        """
        keys = path.split(".")
        target = self._settings

        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]

        target[keys[-1]] = value

    def get_all(self) -> Dict[str, Any]:
        """获取所有设置"""
        return self._settings.copy()


class SettingsDialog(QDialog):
    """设置对话框"""

    settings_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.setWindowTitle("设置")
        self.resize(600, 500)

        self._settings = AppSettings()

        self._init_ui()
        self._load_current_settings()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 音频设置组
        audio_group = QGroupBox("音频设置")
        audio_layout = QFormLayout(audio_group)

        self.spin_default_volume = SpinBox()
        self.spin_default_volume.setRange(0, 100)
        self.spin_default_volume.setSuffix("%")
        audio_layout.addRow("默认音量:", self.spin_default_volume)

        self.spin_default_speed = SpinBox()
        self.spin_default_speed.setRange(50, 200)
        self.spin_default_speed.setSingleStep(10)
        self.spin_default_speed.setSuffix("%")
        audio_layout.addRow("默认速度:", self.spin_default_speed)

        self.chk_auto_play = QCheckBox("加载音频后自动播放")
        audio_layout.addRow("", self.chk_auto_play)

        layout.addWidget(audio_group)

        # 打轴设置组
        timing_group = QGroupBox("打轴设置")
        timing_layout = QFormLayout(timing_group)

        self.spin_preview_lines = SpinBox()
        self.spin_preview_lines.setRange(3, 10)
        self.spin_preview_lines.setSuffix(" 行")
        timing_layout.addRow("预览显示行数:", self.spin_preview_lines)

        self.chk_auto_advance = QCheckBox("打轴后自动跳转到下一行")
        timing_layout.addRow("", self.chk_auto_advance)

        layout.addWidget(timing_group)

        # 界面设置组
        ui_group = QGroupBox("界面设置")
        ui_layout = QFormLayout(ui_group)

        self.combo_theme = ComboBox()
        self.combo_theme.addItems(["浅色", "深色", "自动"])
        ui_layout.addRow("主题:", self.combo_theme)

        self.spin_font_size = SpinBox()
        self.spin_font_size.setRange(12, 48)
        self.spin_font_size.setSuffix(" px")
        ui_layout.addRow("歌词字体大小:", self.spin_font_size)

        layout.addWidget(ui_group)

        # 导出设置组
        export_group = QGroupBox("导出设置")
        export_layout = QFormLayout(export_group)

        self.combo_default_format = ComboBox()
        self.combo_default_format.addItems(["LRC", "KRA", "TXT", "ASS", "Nicokara"])
        export_layout.addRow("默认导出格式:", self.combo_default_format)

        self.line_export_dir = LineEdit()
        self.line_export_dir.setPlaceholderText("点击选择默认导出目录...")
        self.line_export_dir.setReadOnly(True)

        btn_browse = PushButton(parent=self, text="浏览...")
        btn_browse.clicked.connect(self._on_browse_export_dir)

        export_dir_layout = QHBoxLayout()
        export_dir_layout.addWidget(self.line_export_dir)
        export_dir_layout.addWidget(btn_browse)
        export_layout.addRow("默认导出目录:", export_dir_layout)

        layout.addWidget(export_group)

        layout.addStretch()

        # 按钮
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _load_current_settings(self):
        """加载当前设置到界面"""
        self.spin_default_volume.setValue(
            self._settings.get("audio.default_volume", 80)
        )

        speed = self._settings.get("audio.default_speed", 1.0)
        self.spin_default_speed.setValue(int(speed * 100))

        self.chk_auto_play.setChecked(
            self._settings.get("audio.auto_play_on_load", False)
        )

        self.spin_preview_lines.setValue(
            self._settings.get("timing.show_preview_lines", 5)
        )

        self.chk_auto_advance.setChecked(
            self._settings.get("timing.auto_advance_after_tag", True)
        )

        theme = self._settings.get("ui.theme", "light")
        theme_idx = {"light": 0, "dark": 1, "auto": 2}.get(theme, 0)
        self.combo_theme.setCurrentIndex(theme_idx)

        self.spin_font_size.setValue(self._settings.get("ui.font_size", 24))

        fmt = self._settings.get("export.default_format", "LRC")
        fmt_idx = {"LRC": 0, "KRA": 1, "TXT": 2, "ASS": 3, "Nicokara": 4}.get(fmt, 0)
        self.combo_default_format.setCurrentIndex(fmt_idx)

        export_dir = self._settings.get("export.last_export_dir", "")
        if export_dir:
            self.line_export_dir.setText(export_dir)

    def _on_browse_export_dir(self):
        """浏览导出目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择默认导出目录", "")
        if dir_path:
            self.line_export_dir.setText(dir_path)

    def _on_accept(self):
        """确认保存"""
        # 保存设置
        self._settings.set("audio.default_volume", self.spin_default_volume.value())
        self._settings.set("audio.default_speed", self.spin_default_speed.value() / 100)
        self._settings.set("audio.auto_play_on_load", self.chk_auto_play.isChecked())

        self._settings.set("timing.show_preview_lines", self.spin_preview_lines.value())
        self._settings.set(
            "timing.auto_advance_after_tag", self.chk_auto_advance.isChecked()
        )

        theme_map = {0: "light", 1: "dark", 2: "auto"}
        self._settings.set(
            "ui.theme", theme_map.get(self.combo_theme.currentIndex(), "light")
        )
        self._settings.set("ui.font_size", self.spin_font_size.value())

        fmt_map = {0: "LRC", 1: "KRA", 2: "TXT", 3: "ASS", 4: "Nicokara"}
        self._settings.set(
            "export.default_format",
            fmt_map.get(self.combo_default_format.currentIndex(), "LRC"),
        )

        export_dir = self.line_export_dir.text()
        if export_dir:
            self._settings.set("export.last_export_dir", export_dir)

        self._settings.save()

        self.settings_changed.emit()
        self.accept()


class SettingsInterface(QWidget):
    """设置界面主容器

    简化版设置界面，可以直接嵌入主窗口。
    """

    def __init__(self, parent=None):
        super().__init__(parent)

        self._settings = AppSettings()

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 标题
        title = QLabel("设置")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = QLabel("应用设置将在保存项目时自动保存")
        desc.setStyleSheet("color: gray;")
        layout.addWidget(desc)

        layout.addSpacing(20)

        # 打开设置对话框按钮
        btn_open_settings = PrimaryPushButton(parent=self, text="打开设置对话框")
        btn_open_settings.setIcon(FIF.SETTING)
        btn_open_settings.clicked.connect(self._open_settings_dialog)
        layout.addWidget(btn_open_settings)

        # 重置设置按钮
        btn_reset = PushButton(parent=self, text="重置为默认设置")
        btn_reset.setIcon(FIF.DELETE)
        btn_reset.clicked.connect(self._reset_settings)
        layout.addWidget(btn_reset)

        layout.addSpacing(20)

        # 关于部分
        about_title = QLabel("关于")
        about_title.setStyleSheet("font-size: 16px; font-weight: bold;")
        layout.addWidget(about_title)

        about_content = QLabel(
            "StrangeUtaGame - 歌词打轴软件\n"
            "版本: 1.0.0\n"
            "由 RhythmicaLyrics 启发\n\n"
            "项目地址:\n"
        )
        about_content.setStyleSheet("color: gray;")
        about_content.setWordWrap(True)
        layout.addWidget(about_content)

        # GitHub 链接
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl

        self.lbl_github = QLabel(
            '<a href="https://github.com/Xuan-cc/StrangeUtaGame" style="color: #0066cc;">'
            "https://github.com/Xuan-cc/StrangeUtaGame</a>"
        )
        self.lbl_github.setOpenExternalLinks(True)
        self.lbl_github.setStyleSheet("font-size: 12px;")
        layout.addWidget(self.lbl_github)

        layout.addStretch()

        # 配置文件路径显示
        path_label = QLabel(f"配置文件: {self._settings._config_path}")
        path_label.setStyleSheet("color: gray; font-size: 10px;")
        path_label.setWordWrap(True)
        layout.addWidget(path_label)

    def _open_settings_dialog(self):
        """打开设置对话框"""
        dialog = SettingsDialog(self)
        dialog.exec()

    def _reset_settings(self):
        """重置为默认设置"""
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(
            self,
            "确认重置",
            "确定要将所有设置重置为默认值吗？\n这将覆盖您当前的设置。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 删除配置文件
            try:
                if self._settings._config_path.exists():
                    self._settings._config_path.unlink()

                # 重新加载默认设置
                self._settings = AppSettings()

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
        """获取设置对象"""
        return self._settings
