"""主窗口。

应用主容器，使用 PyQt-Fluent-Widgets 的 FluentWindow。
"""

from PyQt6.QtWidgets import QApplication, QWidget, QVBoxLayout, QFileDialog
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QAction, QKeySequence
from qfluentwidgets import FluentWindow, NavigationInterface, NavigationItemPosition
from qfluentwidgets import FluentIcon as FIF

import sys
from typing import Optional
from pathlib import Path

from strange_uta_game.backend.domain import Project


class MainWindow(FluentWindow):
    """主窗口

    应用主容器，使用 Fluent Design 风格。
    """

    def __init__(self):
        super().__init__()

        # 设置窗口属性
        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")
        self.resize(1400, 900)

        # 当前项目
        self._current_project: Optional[Project] = None

        # 初始化界面
        self._init_ui()
        self._init_menu()

    def _init_ui(self):
        """初始化界面"""
        # 创建启动界面
        from .startup.startup_interface import StartupInterface

        self.startup_interface = StartupInterface(self)

        # 设置启动界面为中央部件
        self.setCentralWidget(self.startup_interface)

        # 连接启动界面的信号
        self.startup_interface.project_created.connect(self._on_project_created)

    def _init_menu(self):
        """初始化菜单栏"""
        # 文件菜单
        file_menu = self.menuBar().addMenu("文件")

        self.action_new = QAction("新建项目", self)
        self.action_new.setShortcut(QKeySequence.StandardKey.New)
        self.action_new.triggered.connect(self._on_new_project)
        file_menu.addAction(self.action_new)

        self.action_open = QAction("打开项目", self)
        self.action_open.setShortcut(QKeySequence.StandardKey.Open)
        self.action_open.triggered.connect(self._on_open_project)
        file_menu.addAction(self.action_open)

        self.action_save = QAction("保存项目", self)
        self.action_save.setShortcut(QKeySequence.StandardKey.Save)
        self.action_save.triggered.connect(self._on_save_project)
        self.action_save.setEnabled(False)
        file_menu.addAction(self.action_save)

        file_menu.addSeparator()

        self.action_load_audio = QAction("加载音频", self)
        self.action_load_audio.triggered.connect(self._on_load_audio)
        self.action_load_audio.setEnabled(False)
        file_menu.addAction(self.action_load_audio)

        file_menu.addSeparator()

        self.action_exit = QAction("退出", self)
        self.action_exit.setShortcut(QKeySequence.StandardKey.Quit)
        self.action_exit.triggered.connect(self.close)
        file_menu.addAction(self.action_exit)

        # 编辑菜单
        edit_menu = self.menuBar().addMenu("编辑")

        self.action_undo = QAction("撤销", self)
        self.action_undo.setShortcut(QKeySequence.StandardKey.Undo)
        self.action_undo.triggered.connect(self._on_undo)
        self.action_undo.setEnabled(False)
        edit_menu.addAction(self.action_undo)

        self.action_redo = QAction("重做", self)
        self.action_redo.setShortcut(QKeySequence.StandardKey.Redo)
        self.action_redo.triggered.connect(self._on_redo)
        self.action_redo.setEnabled(False)
        edit_menu.addAction(self.action_redo)

        # 视图菜单
        view_menu = self.menuBar().addMenu("视图")

        self.action_back_to_start = QAction("返回启动界面", self)
        self.action_back_to_start.triggered.connect(self._on_back_to_start)
        view_menu.addAction(self.action_back_to_start)

    def _on_project_created(self, project: Project):
        """项目创建完成回调"""
        self._current_project = project

        # 切换到编辑器界面
        self._switch_to_editor(project)

    def _switch_to_editor(self, project: Project):
        """切换到编辑器界面"""
        from .editor.editor_interface import EditorInterface

        # 创建编辑器界面
        self.editor_interface = EditorInterface(self)
        self.editor_interface.set_project(project)
        self.editor_interface.project_saved.connect(self._on_project_saved)

        # 设置编辑器为中央部件
        self.setCentralWidget(self.editor_interface)

        # 启用相关菜单项
        self.action_save.setEnabled(True)
        self.action_load_audio.setEnabled(True)
        self.action_undo.setEnabled(True)
        self.action_redo.setEnabled(True)

        # 更新标题
        if project.metadata and project.metadata.title:
            self.setWindowTitle(f"StrangeUtaGame - {project.metadata.title}")

    def _on_new_project(self):
        """新建项目"""
        # 返回启动界面
        self._on_back_to_start()

    def _on_open_project(self):
        """打开项目"""
        # 触发启动界面的打开项目功能
        if hasattr(self, "startup_interface"):
            self.startup_interface._on_open_project()

    def _on_save_project(self):
        """保存项目"""
        if not self._current_project:
            return

        # 选择保存路径
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存项目",
            "",
            "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
        )

        if file_path:
            # 确保扩展名正确
            if not file_path.endswith(".sug"):
                file_path += ".sug"

            if hasattr(self, "editor_interface"):
                self.editor_interface.save_project(file_path)

    def _on_project_saved(self):
        """项目保存回调"""
        pass

    def _on_load_audio(self):
        """加载音频"""
        if not hasattr(self, "editor_interface"):
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )

        if file_path:
            self.editor_interface.load_audio(file_path)

    def _on_undo(self):
        """撤销"""
        if hasattr(self, "editor_interface"):
            self.editor_interface.undo()

    def _on_redo(self):
        """重做"""
        if hasattr(self, "editor_interface"):
            self.editor_interface.redo()

    def _on_back_to_start(self):
        """返回启动界面"""
        # 释放编辑器资源
        if hasattr(self, "editor_interface"):
            self.editor_interface.deleteLater()
            delattr(self, "editor_interface")

        # 创建新的启动界面
        from .startup.startup_interface import StartupInterface

        self.startup_interface = StartupInterface(self)
        self.startup_interface.project_created.connect(self._on_project_created)
        self.setCentralWidget(self.startup_interface)

        # 禁用相关菜单项
        self.action_save.setEnabled(False)
        self.action_load_audio.setEnabled(False)
        self.action_undo.setEnabled(False)
        self.action_redo.setEnabled(False)

        # 重置标题
        self.setWindowTitle("StrangeUtaGame - 歌词打轴工具")

        self._current_project = None


def main():
    """应用入口"""
    # 启用 DPI 缩放
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    # 创建应用
    app = QApplication(sys.argv)

    # 创建主窗口
    window = MainWindow()
    window.show()

    # 运行应用
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
