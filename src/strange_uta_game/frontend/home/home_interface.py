"""主页界面。

提供项目创建和打开功能，替代原来的启动界面。
采用 Fluent Design 风格，集成到 MSFluentWindow 侧边栏导航中。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileDialog,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    TextEdit,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SimpleCardWidget,
)

from typing import Optional, List
from pathlib import Path

from strange_uta_game.backend.domain import Project, LyricLine, Singer
from strange_uta_game.backend.application import ProjectService, AutoCheckService
from strange_uta_game.backend.infrastructure import SugProjectParser
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    LyricParserFactory,
    LRCParser,
    parse_to_lyric_lines,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    lines_from_inline_text,
)


class HomeInterface(QWidget):
    """主页界面

    提供：
    - 创建新项目（歌词输入 + 音频选择）
    - 打开已有项目
    """

    project_created = pyqtSignal(Project, str)  # (project, audio_path)
    project_opened = pyqtSignal(Project, str)  # (project, file_path)
    project_save_requested = pyqtSignal()  # 请求保存当前项目
    _LYRIC_EXTENSIONS = {".lrc", ".txt", ".kra"}
    _PROJECT_EXTENSIONS = {".sug"}
    _AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac"}

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project_service = ProjectService()
        self._audio_path: Optional[str] = None
        self._lyric_lines: List[LyricLine] = []

        self.setAcceptDrops(True)
        self._init_ui()

    def set_project(self, project: Optional[Project]):
        """设置当前项目（启用/禁用保存按钮）"""
        self.btn_save.setEnabled(project is not None)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.set_project(self._store.project)

    def dragEnterEvent(self, a0: Optional[QDragEnterEvent]):
        if a0 is None:
            return

        mime_data = a0.mimeData()
        if mime_data is None or not mime_data.hasUrls():
            a0.ignore()
            return

        file_paths = [
            url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()
        ]
        if any(self._is_supported_drop_file(path) for path in file_paths):
            a0.acceptProposedAction()
            return

        a0.ignore()

    def dropEvent(self, a0: Optional[QDropEvent]):
        if a0 is None:
            return

        mime_data = a0.mimeData()
        if mime_data is None:
            a0.ignore()
            return

        file_paths = [
            url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()
        ]
        if not file_paths:
            a0.ignore()
            return

        project_files = [path for path in file_paths if self._is_project_file(path)]
        if project_files:
            self._open_project_file(project_files[0])
            a0.acceptProposedAction()
            return

        lyric_files = [path for path in file_paths if self._is_lyric_file(path)]
        audio_files = [path for path in file_paths if self._is_audio_file(path)]

        imported_lines = 0
        for idx, file_path in enumerate(lyric_files):
            imported_lines += self._import_lyric_file(
                file_path,
                append=self.text_lyrics.toPlainText().strip() != "" or idx > 0,
                show_feedback=False,
            )

        if audio_files:
            self._set_audio_path(audio_files[0])

        if lyric_files or audio_files:
            parts = []
            if lyric_files:
                parts.append(
                    f"已导入 {len(lyric_files)} 个歌词文件，共 {imported_lines} 行"
                )
            if audio_files:
                audio_name = Path(audio_files[0]).name
                suffix = "（已使用第一个音频文件）" if len(audio_files) > 1 else ""
                parts.append(f"音频: {audio_name}{suffix}")

            InfoBar.success(
                title="导入成功",
                content="；".join(parts),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            a0.acceptProposedAction()
            return

        a0.ignore()

    def _init_ui(self):
        """初始化界面"""
        # 主布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # 标题区
        title_layout = QVBoxLayout()
        title_layout.setSpacing(10)

        title = QLabel("StrangeUtaGame")
        title.setStyleSheet("font-size: 32px; font-weight: bold;")
        title_layout.addWidget(title)

        subtitle = QLabel("歌词打轴工具")
        subtitle.setStyleSheet("font-size: 14px; color: gray;")
        title_layout.addWidget(subtitle)

        layout.addLayout(title_layout)

        # 主内容区 - 水平布局
        content_layout = QHBoxLayout()
        content_layout.setSpacing(30)

        # 左侧：创建新项目（占2份）
        create_card = self._create_new_project_card()
        content_layout.addWidget(create_card, 2)

        # 右侧：打开项目（占1份）
        open_card = self._create_open_project_card()
        content_layout.addWidget(open_card, 1)

        layout.addLayout(content_layout, 1)

    def _create_new_project_card(self) -> QWidget:
        """创建"新建项目"卡片"""
        card = SimpleCardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.setSpacing(20)

        # 标题
        title = QLabel("新建项目")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        card_layout.addWidget(title)

        # 歌词输入区
        lyric_label = QLabel("歌词文本（支持粘贴或导入 LRC/TXT）")
        lyric_label.setStyleSheet("font-size: 13px; color: #666;")
        card_layout.addWidget(lyric_label)

        self.text_lyrics = TextEdit()
        self.text_lyrics.setPlaceholderText(
            "在此粘贴歌词文本...\n"
            "支持格式：\n"
            "- 普通文本（每行一句）\n"
            "- LRC 格式（逐行或逐字）\n"
            "- KRA 格式\n\n"
            "导入文件将自动填充此区域"
        )
        self.text_lyrics.setMinimumHeight(200)
        card_layout.addWidget(self.text_lyrics)

        # 歌词操作按钮
        lyric_btn_layout = QHBoxLayout()
        lyric_btn_layout.setSpacing(10)

        self.btn_import_lyric = PushButton("导入歌词文件", self)
        self.btn_import_lyric.setIcon(FIF.FOLDER)
        self.btn_import_lyric.clicked.connect(self._on_import_lyric)
        lyric_btn_layout.addWidget(self.btn_import_lyric)

        self.btn_clear_lyric = PushButton("清空", self)
        self.btn_clear_lyric.setIcon(FIF.DELETE)
        self.btn_clear_lyric.clicked.connect(self._on_clear_lyric)
        lyric_btn_layout.addWidget(self.btn_clear_lyric)

        lyric_btn_layout.addStretch()
        card_layout.addLayout(lyric_btn_layout)

        # 音频选择区
        audio_label = QLabel("音频文件（打轴需要，可后续添加）")
        audio_label.setStyleSheet("font-size: 13px; color: #666;")
        card_layout.addWidget(audio_label)

        audio_layout = QHBoxLayout()
        audio_layout.setSpacing(10)

        self.line_audio_path = LineEdit()
        self.line_audio_path.setPlaceholderText("点击右侧按钮选择音频文件...")
        self.line_audio_path.setReadOnly(True)
        audio_layout.addWidget(self.line_audio_path)

        self.btn_select_audio = PushButton("选择音频", self)
        self.btn_select_audio.setIcon(FIF.MUSIC)
        self.btn_select_audio.clicked.connect(self._on_select_audio)
        audio_layout.addWidget(self.btn_select_audio)

        card_layout.addLayout(audio_layout)

        # 创建按钮
        self.btn_create = PrimaryPushButton("创建项目", self)
        self.btn_create.setIcon(FIF.ADD)
        self.btn_create.setMinimumHeight(45)
        self.btn_create.clicked.connect(self._on_create_project)
        card_layout.addWidget(self.btn_create)

        return card

    def _create_open_project_card(self) -> QWidget:
        """创建"打开项目"卡片"""
        card = SimpleCardWidget()
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.setSpacing(20)

        # 标题
        title = QLabel("打开项目")
        title.setStyleSheet("font-size: 20px; font-weight: bold;")
        card_layout.addWidget(title)

        # 说明
        desc = QLabel("打开已有的 .sug 项目文件")
        desc.setStyleSheet("font-size: 13px; color: #666;")
        card_layout.addWidget(desc)

        # 打开按钮
        self.btn_open = PrimaryPushButton("打开项目", self)
        self.btn_open.setIcon(FIF.FOLDER)
        self.btn_open.setMinimumHeight(50)
        self.btn_open.clicked.connect(self._on_open_project)
        card_layout.addWidget(self.btn_open)

        # 保存按钮
        self.btn_save = PushButton("保存项目", self)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setMinimumHeight(45)
        self.btn_save.setEnabled(False)
        self.btn_save.clicked.connect(self.project_save_requested.emit)
        card_layout.addWidget(self.btn_save)

        # 弹性空间
        card_layout.addStretch()

        # 提示信息
        tip = QLabel("提示：项目文件不包含音频，请确保音频文件可访问")
        tip.setStyleSheet("font-size: 12px; color: gray;")
        tip.setWordWrap(True)
        card_layout.addWidget(tip)

        return card

    def _on_import_lyric(self):
        """导入歌词文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择歌词文件",
            "",
            "歌词文件 (*.lrc *.txt *.kra);;所有文件 (*.*)",
        )

        if file_path:
            self._import_lyric_file(
                file_path,
                append=self.text_lyrics.toPlainText().strip() != "",
                show_feedback=True,
            )

    def _on_clear_lyric(self):
        """清空歌词"""
        self.text_lyrics.clear()
        self._lyric_lines = []

    def _on_select_audio(self):
        """选择音频文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )

        if file_path:
            self._set_audio_path(file_path)

    def _on_create_project(self):
        """创建项目"""
        # 获取歌词文本
        text = self.text_lyrics.toPlainText().strip()

        if not text:
            InfoBar.warning(
                title="请输入歌词",
                content="歌词文本不能为空",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        try:
            # 创建空项目
            project = self._project_service.create_project()
            default_singer = project.get_default_singer()

            if self._lyric_lines:
                # 使用已导入的歌词行（可能包含注音、时间标签等富数据）
                for line in self._lyric_lines:
                    # 将演唱者 ID 替换为项目的默认演唱者
                    line.singer_id = default_singer.id
                    for tag in line.timetags:
                        # TimeTag 是 frozen dataclass，需要重建
                        pass  # timetag.singer_id 在 domain 中是只读的，
                        # 但 LyricLine 会在 add_line 时被项目采用
                    project.add_line(line)
            else:
                # 从文本框手动输入的纯文本
                for line_text in text.split("\n"):
                    line_text = line_text.strip()
                    if line_text:
                        lyric_line = LyricLine(
                            singer_id=default_singer.id, text=line_text
                        )
                        project.add_line(lyric_line)

            # 自动分析注音并生成节奏点（仅对无注音的行）
            try:
                from strange_uta_game.frontend.settings.settings_interface import (
                    AppSettings,
                )

                app_settings = AppSettings()
                auto_check_flags = app_settings.get_all().get("auto_check", {})
                if auto_check_flags.get("auto_on_load", True):
                    user_dict = (
                        app_settings.get_all()
                        .get("ruby_dictionary", {})
                        .get("entries", [])
                    )
                    auto_check = AutoCheckService(
                        auto_check_flags=auto_check_flags, user_dictionary=user_dict
                    )
                    auto_check.apply_to_project(project)
            except Exception:
                pass  # 注音分析失败不阻止项目创建

            # 发送信号（携带音频路径，可为空字符串）
            self.project_created.emit(project, self._audio_path or "")
            self._reset_form()

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
        """打开项目"""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "打开项目",
            "",
            "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)",
        )

        if file_path:
            self._open_project_file(file_path)

    def _is_supported_drop_file(self, file_path: str) -> bool:
        suffix = Path(file_path).suffix.lower()
        return suffix in (
            self._LYRIC_EXTENSIONS | self._PROJECT_EXTENSIONS | self._AUDIO_EXTENSIONS
        )

    def _is_lyric_file(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self._LYRIC_EXTENSIONS

    def _is_project_file(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self._PROJECT_EXTENSIONS

    def _is_audio_file(self, file_path: str) -> bool:
        return Path(file_path).suffix.lower() in self._AUDIO_EXTENSIONS

    def _import_lyric_file(
        self, file_path: str, append: bool = False, show_feedback: bool = True
    ) -> int:
        try:
            lyric_lines, text_content, parsed_with_tags = self._read_lyric_file(
                file_path
            )

            if append and self._lyric_lines:
                self._lyric_lines.extend(lyric_lines)
            else:
                self._lyric_lines = list(lyric_lines)

            self._set_lyrics_text(text_content, append=append)

            if show_feedback:
                InfoBar.success(
                    title="导入成功",
                    content=(
                        f"已导入 {len(lyric_lines)} 行歌词"
                        if parsed_with_tags
                        else f"已作为普通文本导入 {len(lyric_lines)} 行"
                    ),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

            return len(lyric_lines)
        except Exception as e:
            if show_feedback:
                InfoBar.error(
                    title="导入失败",
                    content=str(e),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
            return 0

    def _read_lyric_file(self, file_path: str) -> tuple[List[LyricLine], str, bool]:
        content = Path(file_path).read_text(encoding="utf-8")

        # 检测是否为 RhythmicaLyrics 内联格式（含 [N|MM:SS:cc] 标记）
        if self._is_inline_format(content):
            temp_singer = Singer(name="临时", is_default=True)
            lyric_lines = lines_from_inline_text(content, temp_singer.id)
            text_content = "\n".join(line.text for line in lyric_lines)
            return lyric_lines, text_content, True

        parsed_lines = LyricParserFactory.parse_file(file_path)

        # 如果是 .txt 文件但内容含 LRC 时间标签，自动用 LRC 解析器重新解析
        ext = Path(file_path).suffix.lower()
        if ext == ".txt" and self._has_lrc_timestamps(content):
            lrc_parser = LRCParser()
            parsed_lines = lrc_parser.parse(content)

        temp_singer = Singer(name="临时", is_default=True)
        lyric_lines = parse_to_lyric_lines(parsed_lines, temp_singer.id)
        text_content = "\n".join(line.text for line in lyric_lines)
        parsed_with_tags = any(parsed_line.timetags for parsed_line in parsed_lines)
        return lyric_lines, text_content, parsed_with_tags

    @staticmethod
    def _has_lrc_timestamps(content: str) -> bool:
        """检测文本是否包含标准 LRC 时间标签 [mm:ss.xx]"""
        import re

        return bool(re.search(r"\[\d{1,2}:\d{2}[.:]\d{2,3}\]", content))

    @staticmethod
    def _is_inline_format(content: str) -> bool:
        """检测文本是否为 RhythmicaLyrics 内联格式。

        内联格式特征：含有 [N|MM:SS:cc] 模式（带 checkpoint 编号的时间标签），
        或含有 {漢字|...} 注音组标记。
        """
        import re

        # 检测 [N|MM:SS:cc] 模式 — 内联格式特有
        if re.search(r"\[\d+\|\d{2}:\d{2}:\d{2}\]", content):
            return True
        # 检测 {字|...} ruby 组标记
        if re.search(r"\{.+?\|.+?\}", content):
            return True
        return False

    def _create_lyric_lines_from_entries(self, line_entries: List) -> List[LyricLine]:
        temp_singer = Singer(name="临时", is_default=True)
        lyric_lines: List[LyricLine] = []
        for line_data in line_entries:
            text = line_data.text.strip()
            if text:
                lyric_lines.append(LyricLine(singer_id=temp_singer.id, text=text))
        return lyric_lines

    def _set_lyrics_text(self, text: str, append: bool = False) -> None:
        if append and self.text_lyrics.toPlainText().strip():
            self.text_lyrics.append(text)
            return

        self.text_lyrics.setPlainText(text)

    def _set_audio_path(self, file_path: str) -> None:
        self._audio_path = file_path
        self.line_audio_path.setText(file_path)
        if hasattr(self, "_store") and self._store:
            self._store.set_audio_path(file_path)

    def _reset_form(self) -> None:
        self.text_lyrics.clear()
        self.line_audio_path.clear()
        self._audio_path = None
        self._lyric_lines = []

    def _open_project_file(self, file_path: str) -> None:
        try:
            project = SugProjectParser.load(file_path)

            # 发送信号（含文件路径，用于 ProjectStore 记录 save_path）
            self.project_opened.emit(project, file_path)

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
