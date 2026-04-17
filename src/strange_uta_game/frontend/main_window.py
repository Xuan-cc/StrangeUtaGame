"""主窗口。

应用主容器，使用 PyQt-Fluent-Widgets 的 MSFluentWindow。
参考 March7thAssistant 的 UI 架构。
"""

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QIcon, QAction, QKeySequence
from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox

from qfluentwidgets import (
    NavigationItemPosition,
    MSFluentWindow,
    setThemeColor,
    NavigationBarPushButton,
    setTheme,
    Theme,
    FluentIcon as FIF,
)
from qfluentwidgets import InfoBar, InfoBarPosition

from typing import Optional

from strange_uta_game.backend.application import CommandManager, TimingService
from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.infrastructure.audio import SoundDeviceEngine
from strange_uta_game.frontend.project_store import ProjectStore


class MainWindow(MSFluentWindow):
    """主窗口 - MSFluentWindow 侧边栏导航架构"""

    def __init__(self):
        super().__init__()

        self._audio_engine = SoundDeviceEngine()
        self._command_manager = CommandManager()
        self._timing_service = TimingService(self._audio_engine, self._command_manager)
        self._store = ProjectStore(self)

        self._init_window()
        self._init_interfaces()
        self._init_navigation()

        # 中央响应：store 的 project 变更 → 同步 timing_service 等
        self._store.data_changed.connect(self._on_data_changed)

    def _init_window(self):
        """初始化窗口属性"""
        setThemeColor("#FF6B6B", lazy=True)
        setTheme(Theme.AUTO, lazy=True)

        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        self.setMinimumSize(1200, 800)
        self.resize(1400, 900)

        # 居中
        screen = QApplication.primaryScreen()
        if screen is not None:
            geometry = screen.availableGeometry()
            self.move(
                (geometry.width() - self.width()) // 2,
                (geometry.height() - self.height()) // 2,
            )

    def _init_interfaces(self):
        """初始化所有子界面"""
        from .home.home_interface import HomeInterface
        from .editor.editor_interface import EditorInterface
        from .export.export_interface import ExportInterface
        from .settings.singer_manager import SingerManagerInterface
        from .settings.ruby_editor import RubyInterface
        from .settings.settings_interface import SettingsInterface
        from .editor.edit_interface import EditInterface

        self.homeInterface = HomeInterface(self)
        self.homeInterface.setObjectName("homeInterface")
        self.homeInterface.project_created.connect(self._on_project_created)
        self.homeInterface.project_opened.connect(self._on_project_opened)
        self.homeInterface.project_save_requested.connect(self._on_save_project)

        self.editorInterface = EditorInterface(self)
        self.editorInterface.setObjectName("editorInterface")
        self.editorInterface.set_timing_service(self._timing_service)

        self.exportInterface = ExportInterface(self)
        self.exportInterface.setObjectName("exportInterface")

        self.singerInterface = SingerManagerInterface(self)
        self.singerInterface.setObjectName("singerInterface")

        self.rubyInterface = RubyInterface(self)
        self.rubyInterface.setObjectName("rubyInterface")

        self.settingInterface = SettingsInterface(self)
        self.settingInterface.setObjectName("settingInterface")

        self.editViewInterface = EditInterface(self)
        self.editViewInterface.setObjectName("editViewInterface")

        # 将 store 传递给所有子界面
        self.homeInterface.set_store(self._store)
        self.editorInterface.set_store(self._store)
        self.editViewInterface.set_store(self._store)
        self.exportInterface.set_store(self._store)
        self.singerInterface.set_store(self._store)
        self.rubyInterface.set_store(self._store)
        self.settingInterface.set_store(self._store)

    def _init_navigation(self):
        """初始化侧边栏导航"""
        self.addSubInterface(self.homeInterface, FIF.HOME, "主页")
        self.addSubInterface(self.editorInterface, FIF.PLAY, "打轴")
        self.addSubInterface(self.editViewInterface, FIF.EDIT, "编辑")
        self.addSubInterface(self.exportInterface, FIF.SHARE, "导出")
        self.addSubInterface(self.singerInterface, FIF.PEOPLE, "演唱者")
        self.addSubInterface(self.rubyInterface, FIF.FONT, "注音编辑")

        # 底部
        self.addSubInterface(
            self.settingInterface,
            FIF.SETTING,
            "设置",
            position=NavigationItemPosition.BOTTOM,
        )

        # 默认主页
        self.switchTo(self.homeInterface)

    # ==================== 项目操作 ====================

    def _on_project_created(self, project: Project, audio_path: str = ""):
        """项目创建完成"""
        self._store.load_project(project, audio_path=audio_path if audio_path else None)

        # 自动加载主页选择的音频
        if audio_path:
            self.editorInterface.load_audio(audio_path)

        self.switchTo(self.editorInterface)

        InfoBar.success(
            title="项目创建成功",
            content=f"共 {len(project.lines)} 行歌词",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_project_opened(self, project: Project, file_path: str = ""):
        """项目打开完成"""
        self._store.load_project(project, save_path=file_path if file_path else None)
        self.switchTo(self.editorInterface)

        InfoBar.success(
            title="项目打开成功",
            content=f"共 {len(project.lines)} 行歌词",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _on_data_changed(self, change_type: str):
        """响应 store 的数据变更 — 同步非 UI 组件。"""
        if change_type == "project":
            project = self._store.project
            self._command_manager.clear()
            if project:
                self._timing_service.set_project(project)
            if project and project.metadata and project.metadata.title:
                self.setWindowTitle(f"StrangeUtaGame - {project.metadata.title}")
            else:
                self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        elif change_type == "settings":
            # 同步打轴偏移到 TimingService
            settings = self.settingInterface.get_settings()
            offset_ms = settings.get("timing.tag_offset_ms", 0)
            self._timing_service.set_timing_offset(offset_ms)

    # ==================== 窗口事件 ====================

    def _on_save_project(self):
        """从任意页面触发保存"""
        self.editorInterface._on_save()

    def closeEvent(self, e):
        """关闭窗口时检查未保存变更并退出"""
        if self._store.dirty:
            reply = QMessageBox.question(
                self,
                "未保存的更改",
                "项目有未保存的更改，是否在退出前保存？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if reply == QMessageBox.StandardButton.Save:
                self._on_save_project()
            elif reply == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return

        # 释放编辑器资源
        if hasattr(self, "editorInterface"):
            self.editorInterface.release_resources()
        QApplication.quit()
        e.accept()
