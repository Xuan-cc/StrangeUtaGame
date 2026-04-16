"""启动界面。

启动界面包含：
- 歌词输入区（粘贴/导入）
- 导入预览（自动分析结果）
- 音频选择区
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QLineEdit,
    QFileDialog,
    QMessageBox,
    QFrame,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QGroupBox,
    QGridLayout,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    TextEdit,
    LineEdit,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    ComboBox,
    SpinBox,
)

from typing import Optional, List
from pathlib import Path

from strange_uta_game.backend.application import (
    ProjectService,
    AutoCheckService,
)
from strange_uta_game.backend.domain import Project, LyricLine, Singer
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    LyricParserFactory,
    parse_to_lyric_lines,
)


class LyricInputPanel(QGroupBox):
    """歌词输入面板

    支持：
    - 直接粘贴歌词
    - 导入 TXT/LRC/KRA 文件
    - 拖拽文件
    """

    lyrics_changed = pyqtSignal(str)  # 歌词内容变更
    file_imported = pyqtSignal(str)  # 文件导入

    def __init__(self, parent=None):
        super().__init__("歌词输入", parent)
        self.setFixedWidth(400)

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 文本编辑区
        self.text_edit = TextEdit(self)
        self.text_edit.setPlaceholderText(
            "在此粘贴歌词...\n或拖拽歌词文件到此处\n支持格式: TXT, LRC, KRA"
        )
        self.text_edit.setMinimumHeight(300)
        self.text_edit.textChanged.connect(self._on_text_changed)

        # 设置拖拽支持
        self.text_edit.setAcceptDrops(True)
        self.text_edit.dragEnterEvent = self._drag_enter_event
        self.text_edit.dropEvent = self._drop_event

        layout.addWidget(self.text_edit)

        # 按钮区
        button_layout = QHBoxLayout()

        self.btn_import = PushButton("导入文件", self, icon=FIF.FOLDER)
        self.btn_import.clicked.connect(self._on_import_file)

        self.btn_paste = PushButton("从剪贴板粘贴", self, icon=FIF.PASTE)
        self.btn_paste.clicked.connect(self._on_paste)

        self.btn_clear = PushButton("清空", self, icon=FIF.DELETE)
        self.btn_clear.clicked.connect(self._on_clear)

        button_layout.addWidget(self.btn_import)
        button_layout.addWidget(self.btn_paste)
        button_layout.addWidget(self.btn_clear)
        button_layout.addStretch()

        layout.addLayout(button_layout)

    def _drag_enter_event(self, event: QDragEnterEvent):
        """拖拽进入事件"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop_event(self, event: QDropEvent):
        """拖拽放下事件"""
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if file_path.endswith((".txt", ".lrc", ".kra")):
                self.file_imported.emit(file_path)
            else:
                InfoBar.error(
                    title="不支持的格式",
                    content="请选择歌词文件 (TXT, LRC, KRA)",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

    def _on_text_changed(self):
        """文本变更"""
        text = self.text_edit.toPlainText()
        self.lyrics_changed.emit(text)

    def _on_import_file(self):
        """导入文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择歌词文件",
            "",
            "歌词文件 (*.txt *.lrc *.kra);;所有文件 (*.*)",
        )
        if file_path:
            self.file_imported.emit(file_path)

    def _on_paste(self):
        """从剪贴板粘贴"""
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        text = clipboard.text()
        if text:
            self.text_edit.setPlainText(text)

    def _on_clear(self):
        """清空文本"""
        self.text_edit.clear()

    def get_lyrics(self) -> str:
        """获取当前歌词文本"""
        return self.text_edit.toPlainText()

    def set_lyrics(self, text: str):
        """设置歌词文本"""
        self.text_edit.setPlainText(text)


class AudioSelectPanel(QGroupBox):
    """音频选择面板

    支持：
    - 选择音频文件
    - 拖拽音频文件
    - 显示音频信息
    """

    audio_selected = pyqtSignal(str)  # 音频路径

    def __init__(self, parent=None):
        super().__init__("音频选择", parent)
        self.setFixedWidth(350)

        self._audio_path: Optional[str] = None

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 文件路径输入
        self.line_path = LineEdit(self)
        self.line_path.setPlaceholderText("点击选择或拖拽音频文件...")
        self.line_path.setReadOnly(True)

        # 拖拽支持
        self.line_path.setAcceptDrops(True)
        self.line_path.dragEnterEvent = self._drag_enter_event
        self.line_path.dropEvent = self._drop_event

        # 浏览按钮
        self.btn_browse = PushButton("浏览...", self, icon=FIF.FOLDER)
        self.btn_browse.clicked.connect(self._on_browse)

        # 路径布局
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.line_path)
        path_layout.addWidget(self.btn_browse)

        layout.addLayout(path_layout)

        # 音频信息显示
        self.lbl_info = QLabel("请选择音频文件")
        self.lbl_info.setStyleSheet("color: gray;")
        layout.addWidget(self.lbl_info)

        layout.addStretch()

    def _drag_enter_event(self, event: QDragEnterEvent):
        """拖拽进入"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def _drop_event(self, event: QDropEvent):
        """拖拽放下"""
        urls = event.mimeData().urls()
        if urls:
            file_path = urls[0].toLocalFile()
            if self._is_audio_file(file_path):
                self._set_audio(file_path)
            else:
                InfoBar.error(
                    title="不支持的格式",
                    content="请选择音频文件 (MP3, WAV, FLAC)",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

    def _is_audio_file(self, file_path: str) -> bool:
        """检查是否为音频文件"""
        audio_exts = (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a")
        return file_path.lower().endswith(audio_exts)

    def _on_browse(self):
        """浏览文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )
        if file_path:
            self._set_audio(file_path)

    def _set_audio(self, file_path: str):
        """设置音频文件"""
        self._audio_path = file_path
        self.line_path.setText(file_path)

        # 尝试获取音频信息
        try:
            from strange_uta_game.backend.infrastructure.audio import SoundDeviceEngine

            engine = SoundDeviceEngine()
            if engine.load(file_path):
                info = engine.get_audio_info()
                if info:
                    duration_sec = info.duration_ms // 1000
                    minutes = duration_sec // 60
                    seconds = duration_sec % 60
                    self.lbl_info.setText(
                        f"时长: {minutes}:{seconds:02d} | "
                        f"采样率: {info.sample_rate}Hz | "
                        f"声道: {info.channels}"
                    )
                engine.release()
            else:
                self.lbl_info.setText("无法加载音频文件")
        except Exception as e:
            self.lbl_info.setText(f"音频信息获取失败: {e}")

        self.audio_selected.emit(file_path)

    def get_audio_path(self) -> Optional[str]:
        """获取当前音频路径"""
        return self._audio_path


class ImportPreview(QGroupBox):
    """导入预览面板

    显示自动分析后的歌词结构：
    - 行号
    - 文本
    - 字符数
    - 节奏点数量
    - 注音预览
    """

    def __init__(self, parent=None):
        super().__init__("导入预览", parent)

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # 说明标签
        desc_label = QLabel("自动分析结果预览")
        desc_label.setStyleSheet("color: gray;")
        layout.addWidget(desc_label)

        # 预览表格
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["行号", "文本", "字符数", "节奏点", "注音预览"]
        )
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(0, 50)
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(3, 60)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Fixed
        )
        self.table.setColumnWidth(4, 60)

        self.table.setMinimumHeight(300)
        self.table.setAlternatingRowColors(True)

        layout.addWidget(self.table)

        # 统计信息
        self.lbl_stats = QLabel("共 0 行")
        self.lbl_stats.setStyleSheet("color: gray;")
        layout.addWidget(self.lbl_stats)

        layout.addStretch()

    def set_lines(self, lines: List[LyricLine]):
        """设置歌词行列表"""
        self.table.setRowCount(len(lines))

        total_chars = 0
        total_checkpoints = 0

        for i, line in enumerate(lines):
            # 行号
            self.table.setItem(i, 0, QTableWidgetItem(str(i + 1)))

            # 文本
            self.table.setItem(i, 1, QTableWidgetItem(line.text))

            # 字符数
            char_count = len(line.chars)
            total_chars += char_count
            self.table.setItem(i, 2, QTableWidgetItem(str(char_count)))

            # 节奏点
            check_count = sum(c.check_count for c in line.checkpoints)
            total_checkpoints += check_count
            self.table.setItem(i, 3, QTableWidgetItem(str(check_count)))

            # 注音预览（前3个）
            ruby_preview = ""
            if line.rubies:
                ruby_texts = [r.text for r in line.rubies[:3]]
                ruby_preview = ", ".join(ruby_texts)
                if len(line.rubies) > 3:
                    ruby_preview += "..."
            self.table.setItem(i, 4, QTableWidgetItem(ruby_preview))

        # 更新统计
        self.lbl_stats.setText(
            f"共 {len(lines)} 行 | {total_chars} 字符 | {total_checkpoints} 节奏点"
        )

    def clear(self):
        """清空预览"""
        self.table.setRowCount(0)
        self.lbl_stats.setText("共 0 行")


class StartupInterface(QWidget):
    """启动界面主容器

    整合所有启动面板，管理项目创建流程。
    """

    project_created = pyqtSignal(object)  # Project 对象

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project_service = ProjectService()
        self._auto_check_service = AutoCheckService()

        self._current_lines: List[LyricLine] = []
        self._audio_path: Optional[str] = None

        self._init_ui()
        self._connect_signals()

    def _init_ui(self):
        """初始化界面"""
        # 主布局
        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(20)

        # 左侧：歌词输入
        self.lyric_panel = LyricInputPanel(self)
        layout.addWidget(self.lyric_panel)

        # 中间：导入预览
        self.preview_panel = ImportPreview(self)
        layout.addWidget(self.preview_panel, stretch=1)

        # 右侧：音频选择 + 创建按钮
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(15)

        # 音频选择面板
        self.audio_panel = AudioSelectPanel(self)
        right_layout.addWidget(self.audio_panel)

        # 创建项目按钮
        self.btn_create = PrimaryPushButton("创建项目", self)
        self.btn_create.setIcon(FIF.PLAY)
        self.btn_create.setMinimumHeight(50)
        self.btn_create.clicked.connect(self._on_create_project)
        right_layout.addWidget(self.btn_create)

        # 打开现有项目按钮
        self.btn_open = PushButton("打开现有项目", self)
        self.btn_open.setIcon(FIF.FOLDER)
        self.btn_open.clicked.connect(self._on_open_project)
        right_layout.addWidget(self.btn_open)

        right_layout.addStretch()

        layout.addWidget(right_widget)

    def _connect_signals(self):
        """连接信号"""
        # 歌词输入变化时重新解析
        self.lyric_panel.lyrics_changed.connect(self._on_lyrics_changed)
        self.lyric_panel.file_imported.connect(self._on_file_imported)

        # 音频选择
        self.audio_panel.audio_selected.connect(self._on_audio_selected)

    def _on_lyrics_changed(self, text: str):
        """歌词文本变化"""
        if text.strip():
            self._parse_and_preview(text)
        else:
            self._current_lines = []
            self.preview_panel.clear()

    def _on_file_imported(self, file_path: str):
        """导入文件"""
        try:
            # 解析文件
            parsed_lines = LyricParserFactory.parse_file(file_path)

            # 创建默认演唱者并转换为 LyricLine
            default_singer = Singer(name="演唱者1", color="#FF6B6B", is_default=True)
            lines = parse_to_lyric_lines(parsed_lines, default_singer.id)

            # 应用自动分析到每一行
            for line in lines:
                self._auto_check_service.apply_to_line(line)

            # 显示在输入区
            text = "\n".join(line.text for line in lines)
            self.lyric_panel.set_lyrics(text)

            # 更新预览
            self._current_lines = lines
            self.preview_panel.set_lines(lines)

            InfoBar.success(
                title="导入成功",
                content=f"成功导入 {len(lines)} 行歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

        except Exception as e:
            InfoBar.error(
                title="导入失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _parse_and_preview(self, text: str):
        """解析歌词并更新预览"""
        try:
            # 创建默认演唱者
            lines = []
            default_singer = Singer(name="演唱者1", color="#FF6B6B", is_default=True)

            # 按行分割并创建 LyricLine
            for line_text in text.split("\n"):
                line_text = line_text.strip()
                if not line_text:
                    continue

                line = LyricLine(singer_id=default_singer.id, text=line_text)
                # 应用自动分析
                self._auto_check_service.apply_to_line(line)
                lines.append(line)

            self._current_lines = lines
            self.preview_panel.set_lines(lines)

        except Exception as e:
            InfoBar.warning(
                title="解析警告",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_audio_selected(self, file_path: str):
        """音频选择"""
        self._audio_path = file_path
        InfoBar.success(
            title="音频已选择",
            content=f"{Path(file_path).name}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_create_project(self):
        """创建项目"""
        # 检查是否有歌词
        if not self._current_lines:
            InfoBar.warning(
                title="无法创建项目",
                content="请先输入或导入歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 检查是否有音频
        if not self._audio_path:
            result = QMessageBox.question(
                self,
                "确认",
                "尚未选择音频文件，是否继续创建项目？\n（您可以在编辑器中稍后添加音频）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return

        try:
            # 创建项目
            project = self._project_service.create_project()

            # 添加歌词行
            for line in self._current_lines:
                project.add_line(line)

            # 注意：音频路径不存储在项目文件中，用户每次使用需重新选择

            InfoBar.success(
                title="项目创建成功",
                content=f"共 {len(project.lines)} 行歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

            # 发射信号
            self.project_created.emit(project)

        except Exception as e:
            InfoBar.error(
                title="创建失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_open_project(self):
        """打开现有项目"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            "",
            "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
        )

        if file_path:
            try:
                project = self._project_service.open_project(file_path)

                if project is None:
                    return  # 打开失败，错误已由回调处理

                title = project.metadata.title if project.metadata else "未命名项目"

                InfoBar.success(
                    title="项目打开成功",
                    content=f"{title}",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

                # 发射信号
                self.project_created.emit(project)

            except Exception as e:
                InfoBar.error(
                    title="打开失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
