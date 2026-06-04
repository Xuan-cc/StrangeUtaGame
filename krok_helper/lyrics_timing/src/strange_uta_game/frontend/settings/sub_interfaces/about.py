"""关于子页面。"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtWidgets import QFileDialog, QHBoxLayout, QWidget
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar, InfoBarPosition,
    PrimaryPushButton, PushButton,
    SettingCard, SettingCardGroup,
)

from strange_uta_game.__version__ import __version__ as _app_version
from .base import SubSettingInterface


class AboutSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings_ref = None
        self._init_ui()

    def _init_ui(self):
        self.about_group = SettingCardGroup("关于", self.scrollWidget)

        about_card = SettingCard(FIF.INFO, "StrangeUtaGame - 歌词打轴软件",
            f"版本 v{_app_version}  |  由 RhythmicaLyrics 启发", self.about_group)
        self.about_group.addSettingCard(about_card)

        link_card = SettingCard(FIF.GITHUB, "GitHub",
            "https://github.com/Xuan-cc/StrangeUtaGame", self.about_group)
        self.about_group.addSettingCard(link_card)

        self._path_card = SettingCard(FIF.FOLDER, "配置文件位置", "（未加载）", self.about_group)
        btn_open = PushButton("打开目录", self._path_card)
        btn_open.setFont(QFont("Microsoft YaHei", 10))
        btn_open.clicked.connect(self._open_config_dir)
        btn_change = PushButton("更改位置", self._path_card)
        btn_change.setFont(QFont("Microsoft YaHei", 10))
        btn_change.clicked.connect(self._change_config_dir)
        self._path_card.hBoxLayout.addWidget(btn_open, 0, Qt.AlignmentFlag.AlignRight)
        self._path_card.hBoxLayout.addWidget(btn_change, 0, Qt.AlignmentFlag.AlignRight)
        self._path_card.hBoxLayout.addSpacing(16)
        self.about_group.addSettingCard(self._path_card)

        # FFmpeg 路径设置卡
        self.tools_group = SettingCardGroup("工具配置", self.scrollWidget)
        self._ffmpeg_card = SettingCard(
            FIF.MOVIE, "FFmpeg 路径",
            "用于加载视频文件时提取音频（留空则使用系统环境变量）",
            self.tools_group,
        )
        self._ffmpeg_path_label = PushButton("（使用环境变量）", self._ffmpeg_card)
        self._ffmpeg_path_label.setFont(QFont("Microsoft YaHei", 9))
        self._ffmpeg_path_label.setEnabled(False)
        self._ffmpeg_path_label.setMaximumWidth(260)
        btn_browse_ffmpeg = PushButton("浏览", self._ffmpeg_card)
        btn_browse_ffmpeg.setFont(QFont("Microsoft YaHei", 10))
        btn_browse_ffmpeg.clicked.connect(self._browse_ffmpeg)
        btn_clear_ffmpeg = PushButton("清除", self._ffmpeg_card)
        btn_clear_ffmpeg.setFont(QFont("Microsoft YaHei", 10))
        btn_clear_ffmpeg.clicked.connect(self._clear_ffmpeg_path)
        self._ffmpeg_card.hBoxLayout.addWidget(self._ffmpeg_path_label, 0, Qt.AlignmentFlag.AlignRight)
        self._ffmpeg_card.hBoxLayout.addWidget(btn_browse_ffmpeg, 0, Qt.AlignmentFlag.AlignRight)
        self._ffmpeg_card.hBoxLayout.addWidget(btn_clear_ffmpeg, 0, Qt.AlignmentFlag.AlignRight)
        if sys.platform == "win32":
            self._btn_install_ffmpeg = PrimaryPushButton("一键安装", self._ffmpeg_card)
            self._btn_install_ffmpeg.setFont(QFont("Microsoft YaHei", 10))
            self._btn_install_ffmpeg.clicked.connect(self._install_ffmpeg)
            self._ffmpeg_card.hBoxLayout.addWidget(self._btn_install_ffmpeg, 0, Qt.AlignmentFlag.AlignRight)
        self._ffmpeg_card.hBoxLayout.addSpacing(16)
        self.tools_group.addSettingCard(self._ffmpeg_card)
        self.expandLayout.addWidget(self.about_group)
        self.expandLayout.addWidget(self.tools_group)

        # 保存/重置按钮
        btn_widget = QWidget(self.scrollWidget)
        btn_widget.setMinimumHeight(60)
        btn_layout = QHBoxLayout(btn_widget)
        btn_layout.setContentsMargins(0, 10, 0, 24)
        self.btn_save = PrimaryPushButton("保存设置", btn_widget)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumHeight(36)
        self.btn_save.hide()
        self.btn_reset = PushButton("重置为默认设置", btn_widget)
        self.btn_reset.setIcon(FIF.DELETE)
        self.btn_reset.setMinimumHeight(36)
        # btn_save 保留属性供外层 signal 连接，但不在 UI 中显示
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addStretch()
        self.expandLayout.addWidget(btn_widget)

    def connect_signals(self):
        pass  # 按钮回调由外层连接

    def load_settings(self, s):
        self._settings_ref = s
        embedded = getattr(s, "_provider", None) is not None
        # embedded 模式下配置走宿主存储，没有"配置文件目录"概念：
        # 隐藏整张「配置文件位置」卡片，并避免 setContent(str(None)) 显示 "None"。
        self._path_card.setVisible(not embedded)
        if not embedded:
            self._path_card.setContent(str(s._config_path))
        self.tools_group.setVisible(not embedded)
        ffmpeg_path = s.get("tools.ffmpeg_path", "")
        self._update_ffmpeg_label(ffmpeg_path)

    def collect_settings(self, s):
        pass  # 关于页的 FFmpeg 路径在浏览/清除时即时保存，无需在此收集

    def _open_config_dir(self):
        if self._settings_ref is None or self._settings_ref._config_path is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._settings_ref._config_path.parent)))

    def _change_config_dir(self):
        if self._settings_ref is None or self._settings_ref._config_path is None:
            return
        s = self._settings_ref
        new_dir = QFileDialog.getExistingDirectory(self, "选择配置文件存储目录", str(s._config_path.parent))
        if not new_dir:
            return

        new_dir_path = Path(new_dir)
        program_dir = Path(sys.argv[0]).resolve().parent
        redirect_file = program_dir / ".config_redirect"

        if new_dir_path.resolve() == program_dir.resolve():
            try:
                if redirect_file.exists():
                    redirect_file.unlink()
            except Exception:
                pass
        else:
            try:
                redirect_file.write_text(str(new_dir_path), encoding="utf-8")
            except Exception as e:
                InfoBar.error(title="更改失败", content=f"无法写入重定向文件: {e}",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000, parent=self)
                return

        old_path = s._config_path
        new_path = new_dir_path / "config.json"
        if old_path.exists() and old_path != new_path:
            try:
                import shutil
                new_dir_path.mkdir(exist_ok=True)
                shutil.copy2(str(old_path), str(new_path))
                for fname in ("dictionary.json", "network_dictionary.json", "singers.json"):
                    op = old_path.parent / fname
                    np = new_dir_path / fname
                    if op.exists() and op != np:
                        shutil.copy2(str(op), str(np))
            except Exception as e:
                InfoBar.warning(title="配置复制失败", content=f"请手动复制配置文件: {e}",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000, parent=self)

        s._config_path = new_path
        s._dict_path = new_dir_path / "dictionary.json"
        s._network_dict_path = new_dir_path / "network_dictionary.json"
        s._singers_path = new_dir_path / "singers.json"
        self._path_card.setContent(str(new_path))
        InfoBar.success(title="配置位置已更改", content=f"配置文件将保存到: {new_path}",
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=5000, parent=self)

    def _update_ffmpeg_label(self, path: str):
        if path:
            label = Path(path).name
            self._ffmpeg_path_label.setText(label)
            self._ffmpeg_path_label.setToolTip(path)
        else:
            self._ffmpeg_path_label.setText("（使用环境变量）")
            self._ffmpeg_path_label.setToolTip("")

    def _browse_ffmpeg(self):
        if self._settings_ref is not None and getattr(self._settings_ref, "_provider", None) is not None:
            return
        current = ""
        if self._settings_ref:
            current = self._settings_ref.get("tools.ffmpeg_path", "") or ""
        init_dir = str(Path(current).parent) if current else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 FFmpeg 可执行文件", init_dir,
            "可执行文件 (ffmpeg.exe ffmpeg);;所有文件 (*.*)",
        )
        if not path:
            return
        self._save_ffmpeg_path(path)

    def _clear_ffmpeg_path(self):
        if self._settings_ref is not None and getattr(self._settings_ref, "_provider", None) is not None:
            return
        self._save_ffmpeg_path("")

    def _save_ffmpeg_path(self, path: str):
        if self._settings_ref is None:
            return
        if getattr(self._settings_ref, "_provider", None) is not None:
            return
        self._settings_ref.set("tools.ffmpeg_path", path)
        self._settings_ref.save()
        self._update_ffmpeg_label(path)
        if path:
            InfoBar.success(title="FFmpeg 路径已保存", content=path,
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=4000, parent=self)
        else:
            InfoBar.success(title="FFmpeg 路径已清除", content="将使用系统环境变量中的 ffmpeg",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=3000, parent=self)

    def _install_ffmpeg(self):
        import ctypes
        # -Command 参数用双引号包裹，内部用单引号，避免转义冲突
        ps_args = (
            "-NoExit -Command \""
            "winget install Gyan.FFmpeg "
            "--accept-package-agreements --accept-source-agreements; "
            "Write-Host ''; "
            "Write-Host '>>> 安装完成，可关闭此窗口。<<<' -ForegroundColor Green; "
            "pause\""
        )
        # ShellExecuteW verb=runas 触发 UAC 提权，返回值 >32 表示成功启动
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "powershell", ps_args, None, 1
        )
        if ret <= 32:
            InfoBar.error(
                title="无法启动安装",
                content=f"ShellExecute 返回 {ret}，请检查是否拒绝了 UAC 提权。",
                orient=Qt.Orientation.Horizontal, isClosable=True,
                position=InfoBarPosition.TOP, duration=6000, parent=self,
            )
            return
        InfoBar.info(
            title="已请求管理员权限启动 FFmpeg 安装",
            content="安装完成后，重启软件即可通过环境变量自动使用，或点击「浏览」手动指定路径。",
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=8000, parent=self,
        )
