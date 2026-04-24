"""编辑器界面（打轴主界面）。

本模块仅包含 ``EditorInterface`` 主类。控件与对话框已拆分到 ``timing/`` 子包：

- ``timing.commands``        : ``_SentenceSnapshotCommand``
- ``timing.transport_bar``   : ``TransportBar``
- ``timing.toolbar``         : ``EditorToolBar``
- ``timing.karaoke_preview`` : ``KaraokePreview``
- ``timing.timeline_widget`` : ``TimelineWidget``
- ``timing.dialogs``         : ``ModifyCharacterDialog`` / ``InsertGuideSymbolDialog`` / ``CharEditDialog``

为保留历史 import 路径（``from ...editor.timing_interface import _SentenceSnapshotCommand`` 等），
本模块对子包内符号进行 re-export。
"""

from __future__ import annotations

import time
from copy import deepcopy
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
)

from strange_uta_game.backend.application import (
    CheckpointPosition,
    TimingService,
)
from strange_uta_game.backend.domain import Character, Project
from strange_uta_game.backend.domain.models import RubyMoraDegradeError
from strange_uta_game.backend.infrastructure.audio import AudioLoadError

from .timing import (
    _SentenceSnapshotCommand,
    SentenceSnapshotCommand,
    CharEditDialog,
    EditorToolBar,
    InsertGuideSymbolDialog,
    KaraokePreview,
    ModifyCharacterDialog,
    TimelineWidget,
    TransportBar,
)

__all__ = [
    "EditorInterface",
    # re-exports for backward compatibility
    "_SentenceSnapshotCommand",
    "SentenceSnapshotCommand",
    "TransportBar",
    "EditorToolBar",
    "KaraokePreview",
    "TimelineWidget",
    "ModifyCharacterDialog",
    "InsertGuideSymbolDialog",
    "CharEditDialog",
]


# ──────────────────────────────────────────────
# 编辑器主界面
# ──────────────────────────────────────────────

class EditorInterface(QWidget):
    """编辑器界面主容器"""

    project_saved = pyqtSignal()
    _position_changed_signal = pyqtSignal(int, int, object)
    _checkpoint_moved_signal = pyqtSignal(object)
    _timetag_added_signal = pyqtSignal()
    _timing_error_signal = pyqtSignal(str, str)
    # 渲染进度：(speed, progress)。内部从音频 worker 线程触发，经此信号
    # 自动 marshal 到 UI 线程（Qt 跨线程默认 queued connection）。
    _render_progress_signal = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._timing_service: Optional[TimingService] = None
        self._audio_file_path: Optional[str] = None
        self._current_line_idx = 0
        self._space_pressed = False
        self._last_position_update_time = 0.0  # 60fps UI 节流
        self._fast_forward_ms = 5000
        self._rewind_ms = 5000
        self._key_map = {}  # key_string -> action_name, populated by _apply_settings
        self._init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self._bind_callback_signals()

    def _bind_callback_signals(self):
        self._position_changed_signal.connect(self._handle_position_changed)
        self._checkpoint_moved_signal.connect(self._handle_checkpoint_moved)
        self._timetag_added_signal.connect(self._handle_timetag_added)
        self._timing_error_signal.connect(self._handle_timing_error)
        self._render_progress_signal.connect(self._handle_render_progress)

    def _handle_render_progress(self, speed: float, progress: float) -> None:
        """UI 线程：把进度转交给 TransportBar 显示。"""
        self.transport.set_render_progress(speed, progress)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 5)
        layout.setSpacing(8)

        # 1) 工具栏
        self.toolbar = EditorToolBar(self)
        self.toolbar.save_clicked.connect(self._on_save)
        self.toolbar.load_audio_clicked.connect(self._on_load_audio)
        self.toolbar.load_lyrics_clicked.connect(self._on_load_lyrics)
        self.toolbar.modify_char_clicked.connect(self._on_modify_char)
        self.toolbar.insert_guide_clicked.connect(self._on_insert_guide)
        self.toolbar.bulk_change_clicked.connect(self._on_bulk_change)
        self.toolbar.offset_changed.connect(self._on_offset_changed)
        layout.addWidget(self.toolbar)

        # 2) 播放控制栏
        self.transport = TransportBar(self)
        self.transport.play_clicked.connect(self._on_play)
        self.transport.pause_clicked.connect(self._on_pause)
        self.transport.stop_clicked.connect(self._on_stop)
        self.transport.seek_requested.connect(self._on_seek)
        self.transport.speed_changed.connect(self._on_speed_changed)
        self.transport.volume_changed.connect(self._on_volume_changed)
        layout.addWidget(self.transport)

        # 3) 时间轴
        self.timeline = TimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_seek)
        layout.addWidget(self.timeline)

        # 4) 歌词预览（占主要空间）
        self.preview = KaraokePreview(self)
        self.preview.line_clicked.connect(self._on_line_clicked)
        self.preview.checkpoint_clicked.connect(self._on_checkpoint_clicked)
        self.preview.char_selected.connect(self._on_char_selected)
        self.preview.char_edit_requested.connect(self._on_char_edit_requested)
        self.preview.seek_to_char_requested.connect(self._on_seek_to_char)
        self.preview.singer_change_requested.connect(self._on_singer_change_selection)
        self.preview.delete_chars_requested.connect(self._on_delete_chars_requested)
        self.preview.insert_space_after_requested.connect(
            self._on_insert_space_after_requested
        )
        self.preview.merge_line_up_requested.connect(self._on_merge_line_up_requested)
        self.preview.delete_line_requested.connect(self._on_delete_line_requested)
        self.preview.insert_blank_line_requested.connect(
            self._on_insert_blank_line_requested
        )
        self.preview.add_checkpoint_requested.connect(
            self._on_add_checkpoint_requested
        )
        self.preview.remove_checkpoint_requested.connect(
            self._on_remove_checkpoint_requested
        )
        self.preview.toggle_sentence_end_requested.connect(
            self._on_toggle_sentence_end_requested
        )
        layout.addWidget(self.preview, stretch=1)

        # 5) 底部打轴操作栏
        # 布局：[模式指示器] [打轴按钮] [清除按钮] <stretch> [快捷键提示]
        bottom = QHBoxLayout()
        bottom.setSpacing(10)

        # 左下角模式指示器（#8：区分音乐播放/暂停模式）
        self.lbl_mode = QLabel("模式：编辑")
        self.lbl_mode.setStyleSheet(
            "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
            "background-color: #e0e0e0; color: #444;"
        )
        bottom.addWidget(self.lbl_mode)

        self.btn_tag = PrimaryPushButton("打轴 (Space)", self)
        self.btn_tag.setIcon(FIF.PIN)
        self.btn_tag.setMinimumHeight(36)
        self.btn_tag.setMinimumWidth(160)
        self.btn_tag.clicked.connect(self._on_tag_now)
        bottom.addWidget(self.btn_tag)

        self.btn_clear_tags = PushButton("清除当前行标签", self)
        self.btn_clear_tags.setIcon(FIF.DELETE)
        self.btn_clear_tags.clicked.connect(self._on_clear_current_line_tags)
        bottom.addWidget(self.btn_clear_tags)

        bottom.addStretch()

        # 快捷键提示（动态跟随设置）
        self.lbl_shortcut_hint = QLabel("")
        self.lbl_shortcut_hint.setStyleSheet("font-size: 11px; color: gray;")
        bottom.addWidget(self.lbl_shortcut_hint)

        layout.addLayout(bottom)

        # 6) 状态栏
        # 布局：[播放状态] <stretch> [当前行/字符/时间戳] <stretch> [总体进度]
        status = QHBoxLayout()
        status.setContentsMargins(5, 2, 5, 2)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("font-size: 11px; color: gray;")
        status.addWidget(self.lbl_status)
        status.addStretch()
        # 行号/字符/时间戳信息（#5：从打轴栏移到此处，与播放状态一同显示）
        self.lbl_line_info = QLabel("当前行: -")
        self.lbl_line_info.setStyleSheet("font-size: 11px; color: gray;")
        status.addWidget(self.lbl_line_info)
        status.addStretch()
        self.lbl_progress = QLabel("行: 0/0 | 进度: 0%")
        self.lbl_progress.setStyleSheet("font-size: 11px; color: gray;")
        status.addWidget(self.lbl_progress)
        layout.addLayout(status)

    def set_timing_service(self, timing_service: TimingService):
        self._timing_service = timing_service
        self._timing_service.set_callbacks(self)
        # 注册渲染进度回调：经 pyqtSignal 自动 marshal 到 UI 线程。
        self._timing_service.set_render_progress_callback(
            lambda spd, prog: self._render_progress_signal.emit(float(spd), float(prog))
        )

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.set_project(self._store.project)
        elif change_type in ("rubies", "lyrics", "checkpoints"):
            self.refresh_lyric_display()
        elif change_type == "timetags":
            self._update_time_tags_display()
            self._update_status()
        elif change_type == "settings":
            self._apply_settings()

    def _apply_settings(self):
        """从 AppSettings 读取设定并应用到编辑器。"""
        if not self._store:
            return
        # 通过 MainWindow 的 settingInterface 获取 AppSettings
        main_window = self.window()
        setting_iface = getattr(main_window, "settingInterface", None)
        if setting_iface is None:
            return
        settings = setting_iface.get_settings()
        self._fast_forward_ms = settings.get("timing.fast_forward_ms", 5000)
        self._rewind_ms = settings.get("timing.rewind_ms", 5000)
        self._jump_before_ms = settings.get("timing.jump_before_ms", 3000)
        # #4：读取时间戳微调步长（默认 10ms）
        self._timing_adjust_step_ms = int(
            settings.get("timing.timing_adjust_step_ms", 10)
        )
        # #8/#11/#13：读取双模式快捷键映射（打轴模式=播放中、编辑模式=未播放）
        # 动作集合（所有动作在两种模式下都存在，读设置时各自取值，互不干扰）
        action_names = [
            "tag_now",
            "play_pause",
            "stop",
            "seek_back",
            "seek_forward",
            "speed_down",
            "speed_up",
            "edit_ruby",
            "add_checkpoint",
            "remove_checkpoint",
            "toggle_line_end",
            "toggle_word_join",
            "volume_up",
            "volume_down",
            "nav_prev_line",
            "nav_next_line",
            "timestamp_up",
            "timestamp_down",
            "cycle_checkpoint",
        ]
        # 默认值兜底（当设置未写入新 schema 时使用）
        defaults = {
            "tag_now": "Space",
            "play_pause": "A",
            "stop": "S",
            "seek_back": "Z",
            "seek_forward": "X",
            "speed_down": "Q",
            "speed_up": "W",
            "edit_ruby": "F2",
            "add_checkpoint": "F4",
            "remove_checkpoint": "F5",
            "toggle_line_end": "F6",
            "toggle_word_join": "F3",
            "volume_up": "UP",
            "volume_down": "DOWN",
            "nav_prev_line": "LEFT",
            "nav_next_line": "RIGHT",
            "timestamp_up": "ALT+UP",
            "timestamp_down": "ALT+DOWN",
            "cycle_checkpoint": "ALT+RIGHT",
        }

        def _collect_map(mode_key: str) -> tuple[dict, dict]:
            """返回 (key_map, action->key_str) 两套数据，后者用于提示显示。"""
            key_map: dict[str, str] = {}
            action_to_keys: dict[str, str] = {}
            for action in action_names:
                raw = settings.get(
                    f"shortcuts.{mode_key}.{action}",
                    # 兼容旧 schema（无 mode_key 的扁平 shortcuts.xxx）
                    settings.get(f"shortcuts.{action}", defaults[action]),
                )
                action_to_keys[action] = raw
                for k in (raw or "").split(","):
                    k = k.strip()
                    if k:
                        key_map[k.upper()] = action
            return key_map, action_to_keys

        self._key_map_timing, timing_actions = _collect_map("timing_mode")
        self._key_map_edit, edit_actions = _collect_map("edit_mode")
        for key_name in ("SPACE", "Z", "X"):
            self._key_map_edit.pop(key_name, None)
        # 兼容旧字段名：当前活动 map（按播放状态切换；初始为编辑模式）
        self._key_map = self._key_map_edit
        # 应用默认速度
        default_speed = settings.get("audio.default_speed", 1.0)
        speed_pct = int(default_speed * 100)
        self.transport.edit_speed.blockSignals(True)
        self.transport.edit_speed.setText(f"{max(50, min(200, speed_pct))}%")
        self.transport.edit_speed.blockSignals(False)
        # 应用渲染偏移（与导出偏移联动）
        render_offset = settings.get("export.offset_ms", -100)
        self.preview.set_render_offset(render_offset)
        # 同步工具栏偏移控件
        self.toolbar.edit_offset.blockSignals(True)
        self.toolbar.edit_offset.setText(str(render_offset))
        self.toolbar.edit_offset.blockSignals(False)
        # 将偏移量写入所有字符的渲染/导出时间戳
        if self._project:
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    ch.set_offsets(render_offset, render_offset)
        # 更新快捷键提示（#6：只保留 9 项核心）
        self._update_shortcut_hint(timing_actions, edit_actions)
        # #7：打轴按钮文字联动 shortcuts.timing_mode.tag_now
        tag_key_raw = timing_actions.get("tag_now", "Space")
        tag_first = tag_key_raw.split(",")[0].strip() if tag_key_raw else "Space"
        if hasattr(self, "btn_tag"):
            self.btn_tag.setText(f"打轴 ({tag_first})")
        # #8：同步模式指示器（首次应用设置时刷新）
        self._update_mode_indicator()

    def _update_shortcut_hint(
        self, timing_actions: dict, edit_actions: Optional[dict] = None
    ):
        """根据当前设置的快捷键映射，动态更新底部提示。

        #6：只显示 9 项核心动作（播放/停止/前进/后退/加速/减速/加节奏点/减节奏点/句尾），
        按当前模式（播放中=打轴模式，否则=编辑模式）取快捷键文本。
        """
        action_labels = [
            ("play_pause", "播放"),
            ("stop", "停止"),
            ("seek_back", "后退"),
            ("seek_forward", "前进"),
            ("speed_down", "减速"),
            ("speed_up", "加速"),
            ("add_checkpoint", "加节奏点"),
            ("remove_checkpoint", "减节奏点"),
            ("toggle_line_end", "句尾"),
        ]
        playing = bool(self._timing_service and self._timing_service.is_playing())
        active = timing_actions if playing else (edit_actions or timing_actions)
        parts = []
        for action, label in action_labels:
            key = active.get(action, "")
            if key:
                first_key = key.split(",")[0].strip()
                parts.append(f"{first_key}{label}")
        parts.append("Alt+→ 切换字内节奏点")
        if hasattr(self, "lbl_shortcut_hint"):
            self.lbl_shortcut_hint.setText(" ".join(parts))
        # 缓存以便模式切换时再次调用（无需重读设置）
        self._shortcut_actions_timing = timing_actions
        self._shortcut_actions_edit = edit_actions or timing_actions

    # ==================== 项目 ====================

    def _on_offset_changed(self, offset_ms: int):
        """工具栏偏移控件变更 — 更新设置、字符偏移时间戳和渲染缓存"""
        # 写入设置（与设置页面联动）
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )

            app_settings = AppSettings()
            app_settings.set("export.offset_ms", offset_ms)
            app_settings.save()
        except Exception:
            pass
        # 更新所有字符的偏移时间戳
        if self._project:
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    ch.set_offsets(offset_ms, offset_ms)
        # 更新渲染
        self.preview.set_render_offset(offset_ms)

    def set_project(self, project: Project):
        self._project = project
        self.preview.set_project(project)
        # 应用当前渲染/导出偏移到新加载项目的所有字符
        offset = self.preview._render_offset_ms
        for sentence in project.sentences:
            for ch in sentence.characters:
                ch.set_offsets(offset, offset)
        self._apply_checkpoint_position(
            self._timing_service.get_current_position()
            if self._timing_service
            else CheckpointPosition()
        )
        self._update_time_tags_display()
        self._update_status()

    def release_resources(self):
        """释放音频资源"""
        if self._timing_service:
            self._timing_service.release()

    # ==================== 拖拽加载 ====================

    _AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"}
    _LYRIC_EXTENSIONS = {".lrc", ".txt", ".kra"}
    _PROJECT_EXTENSIONS = {".sug"}

    def dragEnterEvent(self, a0: Optional[QDragEnterEvent]):
        if a0 is None:
            return
        mime = a0.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                file_path = url.toLocalFile()
                ext = Path(file_path).suffix.lower()
                if (
                    ext
                    in self._AUDIO_EXTENSIONS
                    | self._LYRIC_EXTENSIONS
                    | self._PROJECT_EXTENSIONS
                ):
                    a0.acceptProposedAction()
                    return
        a0.ignore()

    def dropEvent(self, a0: Optional[QDropEvent]):
        if a0 is None:
            return
        mime = a0.mimeData()
        if mime is None or not mime.hasUrls():
            a0.ignore()
            return
        for url in mime.urls():
            file_path = url.toLocalFile()
            ext = Path(file_path).suffix.lower()
            if ext in self._AUDIO_EXTENSIONS:
                self.load_audio(file_path)
            elif ext in self._LYRIC_EXTENSIONS:
                self._load_lyrics_from_path(file_path)
            elif ext in self._PROJECT_EXTENSIONS:
                # 项目文件交给主窗口处理
                main_window = self.window()
                open_fn = getattr(main_window, "_open_project_file", None)
                if open_fn is not None:
                    open_fn(file_path)
        a0.acceptProposedAction()

    def _load_lyrics_from_path(self, path: str):
        """从文件路径加载歌词（拖拽或按钮均可调用）。"""
        if not self._project:
            InfoBar.warning(
                title="无法加载",
                content="请先创建或打开一个项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        try:
            from strange_uta_game.backend.application import (
                ProjectImportService,
                ProjectImportError,
            )

            default_singer = self._project.get_default_singer()
            try:
                sentences = ProjectImportService.load_lyrics_from_file(
                    path, default_singer.id
                )
            except ProjectImportError as e:
                raise RuntimeError(str(e)) from e

            # 替换项目歌词
            self._project.sentences.clear()
            for s in sentences:
                self._project.sentences.append(s)

            # 重建引擎状态
            if self._timing_service:
                self._timing_service.set_project(self._project)
            if self._store:
                self._store.notify("lyrics")

            self.refresh_lyric_display()
            InfoBar.success(
                title="歌词已加载",
                content=f"已加载 {len(sentences)} 行歌词",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        except Exception as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    # ==================== 工具栏操作 ====================

    def _on_save(self):
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        store = getattr(self, "_store", None)

        # 已有保存路径 → 直接保存
        if store and store.save_path:
            if store.save():
                InfoBar.success(
                    title="保存成功",
                    content=store.save_path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=2000,
                    parent=self,
                )
                self.project_saved.emit()
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + (store.save_path or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            return

        # 无保存路径 → 弹出另存为对话框
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目", "", "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        try:
            if store:
                success = store.save(path)
            else:
                from strange_uta_game.backend.infrastructure.persistence.sug_io import (
                    SugProjectParser,
                )

                SugProjectParser.save(self._project, path)
                success = True

            if success:
                InfoBar.success(
                    title="保存成功",
                    content=path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
                self.project_saved.emit()
            else:
                InfoBar.error(
                    title="保存失败",
                    content="无法保存到 " + path,
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
        except Exception as e:
            InfoBar.error(
                title="保存失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_load_audio(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择音频文件",
            "",
            "音频文件 (*.mp3 *.wav *.flac *.aac *.ogg *.m4a);;所有文件 (*.*)",
        )
        if path:
            self.load_audio(path)

    def _on_load_lyrics(self):
        """加载歌词文件到当前项目（替换现有歌词）。"""
        if not self._project:
            InfoBar.warning(
                title="无法加载",
                content="请先创建或打开一个项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择歌词文件",
            "",
            "歌词文件 (*.lrc *.txt *.kra);;所有文件 (*.*)",
        )
        if path:
            self._load_lyrics_from_path(path)

    def _on_undo(self):
        if self._timing_service and self._timing_service.can_undo():
            self._timing_service.undo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_redo(self):
        if self._timing_service and self._timing_service.can_redo():
            self._timing_service.redo()
            self._update_time_tags_display()
            self._apply_checkpoint_position(self._timing_service.get_current_position())
            self._update_status()

    def _on_bulk_change(self):
        """Ctrl+H — 打开批量変更对话框，自动填充当前焦点字符的连词或划选区域"""
        from strange_uta_game.frontend.editor.timing import BulkChangeDialog

        initial_word = ""
        initial_reading = ""
        if self._project:
            line_idx = self.preview._current_line_idx
            char_idx = self.preview._current_char_idx
            if 0 <= line_idx < len(self._project.sentences):
                sentence = self._project.sentences[line_idx]
                text = sentence.text
                chars = sentence.characters

                # 优先使用划选区域（多字符选择）
                sel_line = self.preview._sel_line_idx
                sel_start = self.preview._sel_start_char
                sel_end = self.preview._sel_end_char
                if sel_line >= 0 and sel_start >= 0 and sel_line == line_idx:
                    lo = min(sel_start, sel_end)
                    hi = max(sel_start, sel_end)
                    if lo < len(chars) and hi < len(chars) and hi >= lo:
                        initial_word = text[lo : hi + 1]
                        readings: list[str] = []
                        for ci in range(lo, hi + 1):
                            r = chars[ci].ruby
                            readings.append(r.text if r else "")
                        if any(readings):
                            initial_reading = ",".join(readings)
                elif 0 <= char_idx < len(chars):
                    # 回退到连词逻辑（由领域方法 Sentence.get_word_char_range 计算）
                    start, end = sentence.get_word_char_range(char_idx)
                    initial_word = text[start:end]
                    readings = []
                    for ci in range(start, end):
                        r = chars[ci].ruby
                        readings.append(r.text if r else "")
                    if any(readings):
                        initial_reading = ",".join(readings)

        dialog = BulkChangeDialog(
            self._project,
            self,
            initial_word=initial_word,
            initial_reading=initial_reading,
        )
        dialog.exec()

    def _on_modify_char(self):
        """打开修改所选字符对话框"""
        if not self._project:
            return

        # Determine selection range
        line_idx = self.preview._current_line_idx
        sel_line = self.preview._sel_line_idx
        sel_start = self.preview._sel_start_char
        sel_end = self.preview._sel_end_char

        if sel_line >= 0 and sel_start >= 0:
            # Use drag selection
            use_line = sel_line
            start_idx = min(sel_start, sel_end)
            end_idx = max(sel_start, sel_end)
        else:
            # Use single char selection
            use_line = line_idx
            char_idx = self.preview._current_char_idx
            start_idx = char_idx
            end_idx = char_idx

        if use_line < 0 or use_line >= len(self._project.sentences):
            return
        sentence = self._project.sentences[use_line]
        if start_idx < 0 or end_idx >= len(sentence.characters):
            return

        # 快照 before：ModifyCharacterDialog 会原地修改 project.sentences
        before_sentences = deepcopy(self._project.sentences)

        dialog = ModifyCharacterDialog(sentence, start_idx, end_idx, self)
        dialog.exec()

        if dialog.was_modified():
            # 将本次修改登记为一次 SentenceSnapshotCommand（支持撤销/重做）
            command_manager = None
            if self._timing_service:
                command_manager = self._timing_service.command_manager
            if command_manager is not None:
                after_sentences = deepcopy(self._project.sentences)
                cmd = SentenceSnapshotCommand(
                    self._project,
                    before_sentences,
                    after_sentences,
                    f"修改字符（第 {use_line + 1} 句 第 {start_idx + 1}-{end_idx + 1} 字）",
                )
                # 我们已经原地修改完成，不希望 execute() 再跑一次：
                # 用直接入栈方式——调用 execute 会重置为 after_sentences（幂等，安全）
                command_manager.execute(cmd)

            # Rebuild global checkpoints
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("rubies")
                self._store.notify("checkpoints")
                self._store.notify("lyrics")

            # 弹窗汇总连词失败项
            failures = dialog.get_linked_failures()
            if failures:
                lines = []
                for abs_idx, ch, reason in failures[:20]:
                    lines.append(
                        f"  第 {use_line + 1} 句 第 {abs_idx + 1} 字「{ch}」：{reason}"
                    )
                more = ""
                if len(failures) > 20:
                    more = f"\n...（还有 {len(failures) - 20} 项未显示）"
                QMessageBox.information(
                    self,
                    "部分连词设置未应用",
                    "以下位置为末字/句尾/行尾，不能设置连词，已自动跳过：\n\n"
                    + "\n".join(lines)
                    + more,
                )

    def _on_insert_guide(self):
        """打开插入导唱符对话框"""
        if not self._project:
            return

        line_idx = self.preview._current_line_idx
        char_idx = self.preview._current_char_idx

        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        dialog = InsertGuideSymbolDialog(sentence, char_idx, self)
        dialog.exec()

        if dialog.was_modified():
            # Rebuild global checkpoints
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("lyrics")

    # ==================== 音频 ====================

    def _on_singer_change_selection(
        self, line_idx: int, start_char: int, end_char: int, singer_id: str
    ):
        """划词选中后，修改选中范围内所有字符的 per-char singer_id"""
        if (
            not self._project
            or line_idx < 0
            or line_idx >= len(self._project.sentences)
        ):
            return

        sentence = self._project.sentences[line_idx]

        # 更新选中范围内每个字符的 singer_id
        for ci in range(start_char, end_char + 1):
            if ci < len(sentence.characters):
                sentence.characters[ci].singer_id = singer_id
                sentence.characters[ci].push_to_ruby()

        # 如果选中了整行，也更新 sentence.singer_id
        if start_char == 0 and end_char >= len(sentence.chars) - 1:
            sentence.singer_id = singer_id

        if hasattr(self, "_store") and self._store:
            self._store.notify("lyrics")
        self.preview.update()

        InfoBar.success(
            title="演唱者已更新",
            content=f"已将第 {line_idx + 1} 行第 {start_char + 1}~{end_char + 1} 字的演唱者更改",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def load_audio(self, file_path: str) -> bool:
        if not self._timing_service:
            return False

        try:
            self._timing_service.load_audio(file_path)
            info = self._timing_service.get_audio_info()
            if info:
                self.transport.set_duration(info.duration_ms)
                self.timeline.set_duration(info.duration_ms)
                self.transport.set_position(0)
                self.timeline.set_position(0)

            self._audio_file_path = file_path
            self.toolbar.lbl_audio.setText(Path(file_path).name)

            # 与 Home 页加载音频的动作对称：广播 audio 变更，使导出页等订阅者同步
            if hasattr(self, "_store") and self._store:
                self._store.set_audio_path(file_path)

            InfoBar.success(
                title="音频已加载",
                content=Path(file_path).name,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return True
        except AudioLoadError as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False
        except Exception as e:
            InfoBar.error(
                title="加载失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
            return False

    def _update_mode_indicator(self):
        """#8：根据播放状态更新左下角模式指示器与激活的 key_map。

        - 播放中 → "模式：打轴"，使用 _key_map_timing
        - 未播放 → "模式：编辑"，使用 _key_map_edit
        同步刷新底部快捷键提示（因为两模式文本可能不同）。
        """
        if not hasattr(self, "lbl_mode"):
            return
        playing = bool(self._timing_service and self._timing_service.is_playing())
        if playing:
            self.lbl_mode.setText("模式：打轴")
            self.lbl_mode.setStyleSheet(
                "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
                "background-color: #ffd54f; color: #333; font-weight: bold;"
            )
            if hasattr(self, "_key_map_timing"):
                self._key_map = self._key_map_timing
        else:
            self.lbl_mode.setText("模式：编辑")
            self.lbl_mode.setStyleSheet(
                "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
                "background-color: #e0e0e0; color: #444;"
            )
            if hasattr(self, "_key_map_edit"):
                self._key_map = self._key_map_edit
        # 刷新快捷键提示（按新模式取文本）
        if hasattr(self, "_shortcut_actions_timing"):
            self._update_shortcut_hint(
                self._shortcut_actions_timing,
                getattr(self, "_shortcut_actions_edit", None),
            )

    # ==================== 播放控制 ====================

    def _on_play(self):
        if self._timing_service:
            try:
                self._timing_service.play()
                self.transport.set_playing(self._timing_service.is_playing())
                self.lbl_status.setText("播放中")
                self._update_mode_indicator()
            except Exception as e:
                self._show_runtime_error(str(e))

    def _on_pause(self):
        if self._timing_service:
            self._timing_service.pause()
            self.transport.set_playing(False)
            self.lbl_status.setText("已暂停")
            self._update_mode_indicator()

    def _on_stop(self):
        if self._timing_service:
            self._timing_service.stop()
            self.transport.set_playing(False)
            self.transport.set_position(0)
            self.timeline.set_position(0)
            self.lbl_status.setText("已停止")
            self._update_mode_indicator()

    def _on_seek(self, ms: int):
        if self._timing_service:
            self._timing_service.seek(ms)
            self.transport.set_position(ms)
            self.timeline.set_position(ms)

    def _on_speed_changed(self, speed: float):
        if self._timing_service:
            self._timing_service.set_speed(speed)

    def _on_volume_changed(self, vol: int):
        if self._timing_service:
            self._timing_service.set_volume(vol)

    # ==================== 打轴 ====================

    def _on_tag_now(self):
        if not self._timing_service:
            return

        try:
            self._timing_service.on_timing_key_pressed("SPACE")
            self._timing_service.on_timing_key_released("SPACE")
        except Exception as e:
            self._show_runtime_error(str(e))

    def _on_clear_current_line_tags(self):
        if not self._timing_service:
            return

        self._timing_service.clear_timetags_for_current_line()
        self._update_time_tags_display()
        self._update_status()

    def _on_line_clicked(self, idx: int):
        self._current_line_idx = idx
        self._update_line_info()

    def _on_checkpoint_clicked(self, line_idx: int, char_idx: int, cp_idx: int):
        """点击 checkpoint 标记跳转到对应打轴位置"""
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, cp_idx)
            self._update_time_tags_display()
            self._update_status()

    def _on_char_selected(self, line_idx: int, char_idx: int):
        """点击字符选中 — 移动到该字符的第一个 checkpoint。

        若字符无 checkpoint（check_count=0 且非句尾），保持视觉焦点在
        该字符上，方便用户通过 F4 添加节奏点；内部打轴位置仍移到最近的
        下一个有效 checkpoint，确保按空格时能正确赋时间戳。
        """
        # #9: 单一 set_current_position 入口，避免 timing_service 回调在
        # 同帧内反复覆盖 _scroll_center_line 造成空白行抖动。仅当字符无
        # checkpoint 时由本地直接居中；否则交给 _apply_checkpoint_position
        # 统一处理。
        self._current_line_idx = line_idx

        # 判断当前字符是否有 checkpoint
        no_checkpoint = True
        if self._project and line_idx < len(self._project.sentences):
            sentence = self._project.sentences[line_idx]
            if char_idx < len(sentence.characters):
                ch = sentence.characters[char_idx]
                no_checkpoint = ch.check_count == 0 and not ch.is_sentence_end

        if no_checkpoint:
            # 无 checkpoint：直接把视觉焦点定到被点击字符
            self.preview.set_current_position(line_idx, char_idx)
        else:
            # 有 checkpoint：由 timing_service 回调经 _apply_checkpoint_position
            # 统一调用 set_current_position，避免双写 _scroll_center_line
            if self._timing_service:
                self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            else:
                self.preview.set_current_position(line_idx, char_idx)
            self._update_line_info()
            self._update_time_tags_display()
            self._update_status()
            return

        # 无 checkpoint 分支也触发 timing_service 移动（便于随后空格赋时间戳）
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)

        self._update_line_info()
        self._update_time_tags_display()
        self._update_status()

    def _on_char_edit_requested(self, line_idx: int, char_idx: int):
        """F2 键弹出注音编辑对话框"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return
        dialog = CharEditDialog(sentence, char_idx, self)
        dialog.exec()
        if dialog.was_modified():
            self.preview._update_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store"):
                self._store.notify("rubies")
                self._store.notify("checkpoints")
                self._store.notify("lyrics")

    def _add_checkpoint(self):
        """F4 增加当前字符节奏点 (+1)。"""
        self._change_checkpoint(delta=1)

    def _remove_checkpoint(self):
        """F5 删除当前字符节奏点 (-1)，最小为 0。"""
        self._change_checkpoint(delta=-1)

    def _adjust_current_timestamp(self, delta_ms: int):
        """Alt+↑/↓ 微调当前选中 checkpoint 的时间戳。

        批 18 #8：委托给 TimingService.adjust_current_timestamp 统一处理，
        由服务层保证 _update_offset_timestamps + push_to_ruby 双同步。
        """
        if not self._project or not self._timing_service:
            return
        if not self._timing_service.adjust_current_timestamp(delta_ms):
            return
        self._update_time_tags_display()
        self.refresh_lyric_display()
        self._update_line_info()
        if hasattr(self, "_store") and self._store:
            self._store.notify("timetags")

    def _cycle_current_checkpoint(self):
        """#2：Tab 键循环切换"当前选中字符"的 checkpoint 索引。

        目标字符优先级：
        1. 若 KaraokePreview 存在有效选中范围，使用选中字符的起点
           (line = _sel_line_idx, char = min(sel_start, sel_end))。
        2. 否则回退到 TimingService.get_current_position()（播放/打轴上下文）。

        句尾字符若带 is_sentence_end，则句尾 checkpoint 也在循环序列内
        （位置为 check_count）。
        """
        if not self._project or not self._timing_service:
            return
        # 优先用选中字符
        if (
            self.preview._sel_line_idx >= 0
            and self.preview._sel_start_char >= 0
            and self.preview._sel_end_char >= 0
        ):
            line_idx = self.preview._sel_line_idx
            char_idx = min(self.preview._sel_start_char, self.preview._sel_end_char)
            # 以 TimingService 当前 checkpoint_idx 为起点（若行/字匹配），
            # 否则从 0 起。
            pos = self._timing_service.get_current_position()
            base_idx = (
                pos.checkpoint_idx
                if (pos.line_idx == line_idx and pos.char_idx == char_idx)
                else 0
            )
        else:
            pos = self._timing_service.get_current_position()
            line_idx = pos.line_idx
            char_idx = pos.char_idx
            base_idx = pos.checkpoint_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.characters):
            return
        ch = sentence.characters[char_idx]
        total = ch.check_count + (1 if ch.is_sentence_end else 0)
        if total <= 0:
            return
        next_idx = (base_idx + 1) % total
        self._timing_service.move_to_checkpoint(line_idx, char_idx, next_idx)
        self._update_line_info()
        self.refresh_lyric_display()

    def _rebuild_checkpoints(self):
        if self._timing_service:
            if hasattr(self._timing_service, "rebuild_global_checkpoints"):
                self._timing_service.rebuild_global_checkpoints()
            else:
                self._timing_service.rebuild_global_checkpoints()

    def _sync_after_structure_change(
        self,
        change_type: str = "lyrics",
        focus_line_idx: Optional[int] = None,
        focus_char_idx: Optional[int] = None,
        checkpoint_idx: Optional[int] = None,
    ):
        if not self._project:
            return

        self._rebuild_checkpoints()

        total_lines = len(self._project.sentences)
        if total_lines == 0:
            self._current_line_idx = 0
            self.preview._current_line_idx = 0
            self.preview._current_char_idx = 0
            self.preview._current_checkpoint_idx = None
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            return

        line_idx = focus_line_idx if focus_line_idx is not None else self._current_line_idx
        line_idx = max(0, min(line_idx, total_lines - 1))
        sentence = self._project.sentences[line_idx]

        if sentence.characters:
            char_idx = focus_char_idx if focus_char_idx is not None else self.preview._current_char_idx
            char_idx = max(0, min(char_idx, len(sentence.characters) - 1))
        else:
            char_idx = 0

        self._update_selected_checkpoint(line_idx, char_idx, checkpoint_idx)
        self.preview.set_current_position(line_idx, char_idx)
        self._current_line_idx = line_idx

        if self._timing_service and sentence.characters:
            target_cp = checkpoint_idx if checkpoint_idx is not None else 0
            self._timing_service.move_to_checkpoint(line_idx, char_idx, target_cp)

        self.refresh_lyric_display()
        self._update_time_tags_display()
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify(change_type)

    def _execute_structural_edit(
        self,
        description: str,
        mutator: Callable[[], Optional[tuple[int, int, Optional[int], str]]],
    ) -> bool:
        if not self._project:
            return False

        before_sentences = deepcopy(self._project.sentences)
        result = mutator()
        if result is None:
            return False

        after_sentences = deepcopy(self._project.sentences)
        command_manager = None
        if self._timing_service:
            command_manager = self._timing_service.command_manager
        if command_manager is not None:
            command = SentenceSnapshotCommand(
                self._project,
                before_sentences,
                after_sentences,
                description,
            )
            command_manager.execute(command)

        focus_line_idx, focus_char_idx, checkpoint_idx, change_type = result
        self._sync_after_structure_change(
            change_type=change_type,
            focus_line_idx=focus_line_idx,
            focus_char_idx=focus_char_idx,
            checkpoint_idx=checkpoint_idx,
        )
        return True

    def _delete_char_range(
        self, line_idx: int, start_idx: int, end_idx: int
    ) -> Optional[tuple[int, int, Optional[int], str]]:
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return None

        sentence = self._project.sentences[line_idx]
        if not sentence.characters:
            return None

        start = max(0, min(start_idx, len(sentence.characters) - 1))
        end = max(start + 1, min(end_idx, len(sentence.characters)))
        delete_count = end - start
        for _ in range(delete_count):
            became_empty = sentence.delete_character(start)
            if became_empty:
                break

        if not sentence.characters:
            self._project.delete_line(line_idx)
            if not self._project.sentences:
                return 0, 0, None, "lyrics"
            new_line_idx = max(0, min(line_idx, len(self._project.sentences) - 1))
            new_sentence = self._project.sentences[new_line_idx]
            new_char_idx = 0 if not new_sentence.characters else min(start, len(new_sentence.characters) - 1)
            return new_line_idx, new_char_idx, 0, "lyrics"

        new_char_idx = min(start, len(sentence.characters) - 1)
        return line_idx, new_char_idx, 0, "lyrics"

    def _insert_line_break_at_current(self):
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        project = self._project

        self._execute_structural_edit(
            "插入换行",
            lambda: (
                project.insert_line_break(line_idx, char_idx)
                or (line_idx + 1, 0, 0, "lyrics")
            ),
        )

    def _delete_current_selection_or_char(self):
        if not self._project:
            return

        line_idx = self._current_line_idx
        start = self.preview._current_char_idx
        end = start + 1
        if (
            self.preview._sel_line_idx == line_idx
            and self.preview._sel_start_char >= 0
            and self.preview._sel_end_char >= 0
        ):
            start = min(self.preview._sel_start_char, self.preview._sel_end_char)
            end = max(self.preview._sel_start_char, self.preview._sel_end_char) + 1

        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
        )

    def _toggle_sentence_end_at_current(self):
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _change_checkpoint(self, delta: int):
        """增加或减少"当前选中字符"的节奏点数量。

        目标字符优先级：
        1. 若 KaraokePreview 在当前行存在有效选中范围 (_sel_line_idx == 当前行)，
           作用于选中范围的起始字符 (min(sel_start, sel_end))。
        2. 否则回退到 preview._current_char_idx（光标所在字符）。

        这样 F5/F6/Space/Backspace 快捷键始终作用于用户选中的字符，
        而非"当前选中的 checkpoint 所属字符"。
        """
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        # 优先使用用户显式选中字符；若选中范围落在当前行则取其起点。
        if (
            self.preview._sel_line_idx == line_idx
            and self.preview._sel_start_char >= 0
            and self.preview._sel_end_char >= 0
        ):
            char_idx = min(self.preview._sel_start_char, self.preview._sel_end_char)
        else:
            char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        def _mutate():
            if delta > 0:
                sentence.add_checkpoint(char_idx)
            else:
                try:
                    sentence.remove_checkpoint(char_idx)
                except RubyMoraDegradeError:
                    # 减到 0 会从有 mora 退化为 Nicokara 无 mora 格式（注音文本保留）
                    ch_text = sentence.characters[char_idx].char
                    reply = QMessageBox.question(
                        self,
                        "退化为无 mora 格式",
                        f"字符 '{ch_text}' 的节奏点将减至 0，注音将从有 mora 退化为 Nicokara 无 mora 格式。\n"
                        f"注音文本会完整保留。是否继续？",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        # 用户取消：返回当前状态，不变更
                        cp_idx = self.preview._current_checkpoint_idx
                        return line_idx, char_idx, cp_idx if cp_idx is not None else 0, "checkpoints"
                    sentence.remove_checkpoint(char_idx, force=True)
            cp_idx = self.preview._current_checkpoint_idx
            if cp_idx is not None and delta < 0:
                cp_idx = min(cp_idx, sentence.characters[char_idx].check_count)
            return line_idx, char_idx, cp_idx if cp_idx is not None else 0, "checkpoints"

        self._execute_structural_edit("调整节奏点", _mutate)

    def _toggle_line_end(self):
        """F6 切换当前字符的句尾标记 (is_line_end)。

        句尾标记独立于普通 checkpoint 数量。
        """
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _toggle_word_join(self):
        """F3 连词/取消连词 — toggle 当前字符的 linked_to_next 标记"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        # 不能在最后一个字符上连词
        if char_idx >= len(sentence.characters) - 1:
            InfoBar.warning(
                title="无法连词",
                content="已是最后一个字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
            return

        ch = sentence.characters[char_idx]
        new_linked = not ch.linked_to_next
        ch.linked_to_next = new_linked

        if self._timing_service:
            self._timing_service.rebuild_global_checkpoints()
        self.refresh_lyric_display()
        self.preview.repaint()  # 强制同步重绘，确保连词视觉立即更新
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify("checkpoints")

        InfoBar.success(
            title="连词" if new_linked else "取消连词",
            content=f"已{'连接' if new_linked else '断开'}「{sentence.chars[char_idx]}」与「{sentence.chars[char_idx + 1]}」",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_nav_line(self, delta: int):
        """方向键导航：上一行 (delta=-1) 或下一行 (delta=+1)。

        空行无 checkpoint，TimingService.move_to_checkpoint 向下搜索时天然跳过；
        但向上时不会跨空行。此处对 delta=-1 调用领域方法
        :py:meth:`Project.find_prev_line_with_checkpoints` 补上跳空行逻辑，首行处停止。
        """
        if not self._project:
            return
        sentences = self._project.sentences
        if delta < 0:
            cand = self._project.find_prev_line_with_checkpoints(self._current_line_idx)
            if cand < 0:
                return
            new_idx = cand
        else:
            new_idx = self._current_line_idx + delta
            if new_idx < 0 or new_idx >= len(sentences):
                return
        if self._timing_service:
            self._timing_service.move_to_checkpoint(new_idx, 0, 0)
            self._update_time_tags_display()
            self._update_status()

    def _on_seek_to_char(self, line_idx: int, char_idx: int):
        """双击字符 → 跳转到该字符的 checkpoint 前指定毫秒数"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        jump_before = getattr(self, "_jump_before_ms", 3000)
        tags = sentence.get_timetags_for_char(char_idx)
        if tags:
            target_ms = max(0, tags[0] - jump_before)
            self._on_seek(target_ms)

        # 同时移动打轴位置到该字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
            self._update_time_tags_display()
            self._update_status()

    def _on_delete_chars_requested(self, line_idx: int, start: int, end: int):
        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
        )

    def _on_insert_space_after_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            if char_idx < 0 or char_idx >= len(sentence.characters):
                return None
            ref_char = sentence.characters[char_idx]
            new_char = Character(
                char=" ",
                check_count=1,
                singer_id=ref_char.singer_id or sentence.singer_id,
            )
            sentence.insert_character(char_idx + 1, new_char)
            return line_idx, char_idx + 1, 0, "lyrics"

        self._execute_structural_edit("插入空格", _mutate)

    def _on_merge_line_up_requested(self, line_idx: int):
        if not self._project:
            return
        project = self._project
        self._execute_structural_edit(
            "合并上一行",
            lambda: (
                (
                    line_idx - 1,
                    max(0, len(project.sentences[line_idx - 1].characters) - 1),
                    0,
                    "lyrics",
                )
                if project.merge_line_into_previous(line_idx)
                else None
            ),
        )

    def _on_delete_line_requested(self, line_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            project.delete_line(line_idx)
            if not project.sentences:
                return 0, 0, None, "lyrics"
            new_line_idx = max(0, min(line_idx, len(project.sentences) - 1))
            return new_line_idx, 0, 0, "lyrics"

        self._execute_structural_edit("删除本行", _mutate)

    def _on_insert_blank_line_requested(self, line_idx: int):
        if not self._project:
            return
        project = self._project

        singer_id = ""
        if 0 <= line_idx < len(project.sentences):
            sentence = project.sentences[line_idx]
            if sentence.characters:
                singer_id = sentence.characters[-1].singer_id

        self._execute_structural_edit(
            "插入空行",
            lambda: ((project.insert_blank_line(line_idx, singer_id=singer_id), 0, None, "lyrics")),
        )

    def _on_add_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        self._execute_structural_edit(
            "增加节奏点",
            lambda: (
                project.sentences[line_idx].add_checkpoint(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _on_remove_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project
        sentence = project.sentences[line_idx]

        def _mutate():
            try:
                sentence.remove_checkpoint(char_idx)
            except RubyMoraDegradeError:
                ch_text = sentence.characters[char_idx].char
                reply = QMessageBox.question(
                    self,
                    "退化为无 mora 格式",
                    f"字符 '{ch_text}' 的节奏点将减至 0，注音将从有 mora 退化为 Nicokara 无 mora 格式。\n"
                    f"注音文本会完整保留。是否继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return line_idx, char_idx, 0, "checkpoints"
                sentence.remove_checkpoint(char_idx, force=True)
            return line_idx, char_idx, 0, "checkpoints"

        self._execute_structural_edit("减少节奏点", _mutate)

    def _on_toggle_sentence_end_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                project.sentences[line_idx].toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    # ==================== 键盘 ====================

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        modifiers = a0.modifiers()
        playing = bool(self._timing_service and self._timing_service.is_playing())

        if playing and key == Qt.Key.Key_F4:
            self._toggle_sentence_end_at_current()
            a0.accept()
            return
        if playing and key == Qt.Key.Key_F5:
            self._add_checkpoint()
            a0.accept()
            return
        if playing and key == Qt.Key.Key_F6:
            self._remove_checkpoint()
            a0.accept()
            return

        # Ctrl 快捷键（系统级，优先处理）
        if modifiers & Qt.KeyboardModifier.ControlModifier:
            if key == Qt.Key.Key_Z:
                self._on_undo()
                a0.accept()
                return
            elif key == Qt.Key.Key_Y:
                self._on_redo()
                a0.accept()
                return
            elif key == Qt.Key.Key_S:
                self._on_save()
                a0.accept()
                return
            elif key == Qt.Key.Key_H:
                self._on_bulk_change()
                a0.accept()
                return
            # 其他 Ctrl 组合键：不直接 return，继续走 key_map 查找

        # Convert Qt key to string name for mapping lookup
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            super().keyPressEvent(a0)
            return

        action = self._key_map.get(key_name.upper())
        # Fallback to default key map if settings not loaded yet
        if action is None:
            action = self._default_key_action(key, modifiers)

        if action == "tag_now":
            if not playing:
                self._add_checkpoint()
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and not self._space_pressed:
                try:
                    self._space_pressed = True
                    self._timing_service.on_timing_key_pressed("SPACE")
                except Exception as e:
                    self._space_pressed = False
                    self._show_runtime_error(str(e))
            a0.accept()
            return
        elif action == "play_pause":
            if self._timing_service and self._timing_service.is_playing():
                self._on_pause()
            else:
                self._on_play()
        elif action == "stop":
            self._on_stop()
        elif action == "seek_back":
            if not playing:
                a0.accept()
                return
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                self._on_seek(max(0, cur - self._rewind_ms))
        elif action == "seek_forward":
            if not playing:
                a0.accept()
                return
            if self._timing_service:
                cur = self._timing_service.get_position_ms()
                dur = self._timing_service.get_duration_ms()
                self._on_seek(min(dur, cur + self._fast_forward_ms))
        elif action == "speed_down":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(max(50, v - 10))
        elif action == "speed_up":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(min(200, v + 10))
        elif action == "volume_up":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(min(100, v + 5))
        elif action == "volume_down":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(max(0, v - 5))
        elif action == "nav_prev_line":
            self._on_nav_line(-1)
            a0.accept()
            return
        elif action == "nav_next_line":
            self._on_nav_line(1)
            a0.accept()
            return
        elif action == "timestamp_up":
            # #3/#4：以 checkpoint 为单位 + 步长可配置
            self._adjust_current_timestamp(self._timing_adjust_step_ms)
            a0.accept()
            return
        elif action == "timestamp_down":
            self._adjust_current_timestamp(-self._timing_adjust_step_ms)
            a0.accept()
            return
        elif action == "cycle_checkpoint":
            # #2：Tab 循环切换当前字符的 checkpoint
            self._cycle_current_checkpoint()
            a0.accept()
            return
        elif action == "edit_ruby":
            if self._project:
                line_idx = self._current_line_idx
                char_idx = self.preview._current_char_idx
                self._on_char_edit_requested(line_idx, char_idx)
        elif action == "add_checkpoint":
            if self._project:
                self._add_checkpoint()
        elif action == "remove_checkpoint":
            if self._project:
                self._remove_checkpoint()
        elif action == "toggle_word_join":
            if self._project:
                self._toggle_word_join()
        elif action == "toggle_line_end":
            if self._project:
                self._toggle_line_end()
        elif key == Qt.Key.Key_Backspace and not playing:
            self._remove_checkpoint()
            a0.accept()
            return
        elif a0.text() in (".", "。"):
            self._toggle_sentence_end_at_current()
            a0.accept()
            return
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._insert_line_break_at_current()
            a0.accept()
            return
        elif key == Qt.Key.Key_Delete:
            self._delete_current_selection_or_char()
            a0.accept()
            return
        else:
            super().keyPressEvent(a0)

    def keyReleaseEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        key = a0.key()
        modifiers = a0.modifiers()
        key_name = self._qt_key_to_name(key, modifiers)
        action = self._key_map.get(key_name.upper()) if key_name else None
        if action is None:
            action = self._default_key_action(key, modifiers)
        if action == "tag_now":
            if not (self._timing_service and self._timing_service.is_playing()):
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and self._space_pressed:
                try:
                    self._timing_service.on_timing_key_released("SPACE")
                except Exception as e:
                    self._show_runtime_error(str(e))
            self._space_pressed = False
            a0.accept()
            return
        super().keyReleaseEvent(a0)

    def _qt_key_to_name(
        self, key, modifiers=Qt.KeyboardModifier.NoModifier
    ) -> Optional[str]:
        """Convert Qt key enum to string name for shortcut mapping.

        支持组合键，如 CTRL+F4、ALT+A、SHIFT+Z 等。
        """
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
            # 标点键（#11 修复：支持字面量键名，与 _KeyCaptureButton 保持一致）
            Qt.Key.Key_Comma: ",",
            Qt.Key.Key_Period: ".",
            Qt.Key.Key_Slash: "/",
            Qt.Key.Key_Semicolon: ";",
            Qt.Key.Key_Apostrophe: "'",
            Qt.Key.Key_BracketLeft: "[",
            Qt.Key.Key_BracketRight: "]",
            Qt.Key.Key_Backslash: "\\",
            Qt.Key.Key_Minus: "-",
            Qt.Key.Key_Equal: "=",
            Qt.Key.Key_QuoteLeft: "`",
        }
        if key in _key_names:
            parts.append(_key_names[key])
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
            parts.append(chr(key))
        elif Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            parts.append(chr(key))
        else:
            return None
        return "+".join(parts) if parts else None

    def _default_key_action(
        self, key, modifiers=Qt.KeyboardModifier.NoModifier
    ) -> Optional[str]:
        """Fallback key mapping when settings not loaded."""
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            return None
        defaults = {
            "SPACE": "tag_now",
            "A": "play_pause",
            "S": "stop",
            "Z": "seek_back",
            "X": "seek_forward",
            "Q": "speed_down",
            "W": "speed_up",
            "F2": "edit_ruby",
            "F3": "toggle_word_join",
            "F4": "add_checkpoint",
            "F5": "remove_checkpoint",
            "F6": "toggle_line_end",
            "UP": "volume_up",
            "DOWN": "volume_down",
            "LEFT": "nav_prev_line",
            "RIGHT": "nav_next_line",
            "ALT+UP": "timestamp_up",
            "ALT+DOWN": "timestamp_down",
        }
        return defaults.get(key_name.upper())

    # ==================== TimingService 回调 ====================

    def on_timetag_added(
        self,
        singer_id: str,
        line_idx: int,
        char_idx: int,
        checkpoint_idx: int,
        timestamp_ms: int,
    ) -> None:
        _ = singer_id, line_idx, char_idx, checkpoint_idx, timestamp_ms
        self._timetag_added_signal.emit()

    def on_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ) -> None:
        self._position_changed_signal.emit(position_ms, duration_ms, singer_positions)

    def on_singer_changed(self, new_singer_id: str, prev_singer_id: str) -> None:
        _ = new_singer_id, prev_singer_id

    def on_checkpoint_moved(self, position: CheckpointPosition) -> None:
        self._checkpoint_moved_signal.emit(position)

    def on_timing_error(self, error_type: str, message: str) -> None:
        self._timing_error_signal.emit(error_type, message)

    def _handle_position_changed(
        self, position_ms: int, duration_ms: int, singer_positions
    ):
        # 60fps UI 节流：跳过间隔 < 16ms 的更新
        now = time.monotonic()
        if now - self._last_position_update_time < 0.016:
            return
        self._last_position_update_time = now

        _ = singer_positions
        self.transport.set_duration(duration_ms)
        self.timeline.set_duration(duration_ms)
        self.transport.set_position(position_ms)
        self.timeline.set_position(position_ms)
        self.preview.set_current_time_ms(position_ms)
        if self._timing_service:
            playing = self._timing_service.is_playing()
            self.transport.set_playing(playing)
            self.preview.set_playing(playing)

    def _handle_checkpoint_moved(self, position: CheckpointPosition):
        self._apply_checkpoint_position(position)
        self._update_status()

    def _handle_timetag_added(self):
        self._update_time_tags_display()
        self._update_status()

    def _handle_timing_error(self, error_type: str, message: str):
        InfoBar.warning(
            title=error_type,
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    # ==================== 辅助 ====================

    def _update_selected_checkpoint(
        self,
        line_idx: int,
        char_idx: int,
        cp_idx: Optional[int],
    ) -> None:
        """统一入口：更新 cp 选中态（UI 状态 + domain 选中状态）。

        Issue #9 第十六批架构性修复：
        - UI 侧 preview._current_checkpoint_idx 仍维持（用于渲染判断兼容旧路径）
        - Domain 侧 Project.set_selected_checkpoint 维持全局单选不变量 I1
        - 渲染时 paintEvent 直接读 char.selected_checkpoint_idx → singer.complement_color
          单管道上色，不再需要"选中分支 + HSV 运行时补色 + 额外 drawText"

        调用点覆盖所有 cp 切换事件（除 F5/F6 增减 cp 外，按用户约定不触发）：
        - _apply_checkpoint_position（TimingService 主通路）
        - _sync_after_structure_change（结构编辑后）
        - _on_char_selected 无 cp 分支的直接 set_current_position
        """
        self.preview._current_checkpoint_idx = cp_idx
        if self._project is None or cp_idx is None:
            # cp_idx=None 时不清 project 选中态：保持旧选中直到下次有效切换。
            # 这是因为某些路径（空项目、无 cp 字符）传 None 只代表"当前字符没 cp"，
            # 不代表"用户想取消选中"。
            return
        self._project.set_selected_checkpoint(line_idx, char_idx, cp_idx)

    def _apply_checkpoint_position(self, position: CheckpointPosition):
        if not self._project or not self._project.sentences:
            self._current_line_idx = 0
            self.preview._current_checkpoint_idx = None
            self._update_line_info()
            return

        line_idx = max(0, min(position.line_idx, len(self._project.sentences) - 1))
        self._current_line_idx = line_idx
        self._update_selected_checkpoint(line_idx, position.char_idx, position.checkpoint_idx)
        self.preview.set_current_position(line_idx, position.char_idx)
        self._update_line_info()

    def _show_runtime_error(self, message: str):
        InfoBar.error(
            title="操作失败",
            content=message,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def _update_line_info(self):
        if self._project and self._project.sentences:
            total = len(self._project.sentences)
            idx = min(self._current_line_idx, total - 1)
            text = self._project.sentences[idx].text
            preview = text[:30] + "..." if len(text) > 30 else text
            # 显示选中字符的时间戳信息
            char_info = ""
            char_idx = self.preview._current_char_idx
            sentence = self._project.sentences[idx]
            if 0 <= char_idx < len(sentence.characters):
                ch = sentence.characters[char_idx]
                ts_parts = []
                for ts in ch.timestamps:
                    m, s = divmod(ts // 1000, 60)
                    ms = ts % 1000
                    ts_parts.append(f"{m:02d}:{s:02d}.{ms:03d}")
                if ch.is_sentence_end and ch.sentence_end_ts is not None:
                    ets = ch.sentence_end_ts
                    m, s = divmod(ets // 1000, 60)
                    ms = ets % 1000
                    ts_parts.append(f"句尾{m:02d}:{s:02d}.{ms:03d}")
                if ts_parts:
                    char_info = f" | 「{ch.char}」 {', '.join(ts_parts)}"
                else:
                    char_info = f" | 「{ch.char}」 未打轴"
            self.lbl_line_info.setText(f"行 {idx + 1}/{total}: {preview}{char_info}")
        else:
            self.lbl_line_info.setText("当前行: -")

    def _update_time_tags_display(self):
        if not self._project:
            return
        self.timeline.set_time_tags(self._project.collect_all_timestamp_ms())

    def _update_status(self):
        if not self._project:
            self.lbl_progress.setText("行: 0/0 | 进度: 0%")
            return
        total = len(self._project.sentences)
        timed = sum(1 for s in self._project.sentences if s.has_timetags)
        pct = int(timed / total * 100) if total > 0 else 0
        self.lbl_progress.setText(f"行: {total} | 已打轴: {timed}/{total} ({pct}%)")

    def refresh_lyric_display(self):
        self.preview._update_display()
