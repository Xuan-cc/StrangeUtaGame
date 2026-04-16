"""主窗口 - 简化测试版"""

from PyQt6.QtWidgets import QWidget, QVBoxLayout, QFileDialog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from qfluentwidgets import FluentWindow, NavigationInterface, NavigationItemPosition
from qfluentwidgets import FluentIcon as FIF

from typing import Optional
from pathlib import Path

from strange_uta_game.backend.domain import Project


class MainWindow(FluentWindow):
    """主窗口"""

    def __init__(self):
        print("MainWindow.__init__ start")
        super().__init__()
        print("super().__init__ done")

        # 设置窗口属性
        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        self.resize(1400, 900)
        print("Window properties set")

        # 当前项目
        self._current_project: Optional[Project] = None
        print("Project initialized")

        # 暂时不初始化 UI
        # self._init_ui()
        # self._init_menu()
        print("MainWindow.__init__ done")

    def _init_ui(self):
        """初始化界面"""
        from .startup.startup_interface import StartupInterface

        self.startup_interface = StartupInterface(self)
        self.setCentralWidget(self.startup_interface)
        self.startup_interface.project_created.connect(self._on_project_created)

    def _init_menu(self):
        """初始化菜单栏"""
        pass

    def _on_project_created(self, project: Project):
        pass

    def _on_new_project(self):
        pass

    def _on_open_project(self):
        pass

    def _on_save_project(self):
        pass

    def _on_load_audio(self):
        pass

    def _on_undo(self):
        pass

    def _on_redo(self):
        pass

    def _on_back_to_start(self):
        pass
