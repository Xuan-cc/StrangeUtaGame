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

import re
import time
from copy import deepcopy
from pathlib import Path
from typing import Callable, Optional, Tuple

from PyQt6.QtCore import QEvent, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QDragEnterEvent, QDropEvent, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
)
from qfluentwidgets import (
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    StateToolTip,
    setCustomStyleSheet,
)

from strange_uta_game.backend.application import (
    CheckpointPosition,
    TimingService,
)
from strange_uta_game.backend.application.auto_check_service import (
    get_kanji_linked_indices,
)
from strange_uta_game.backend.application.export_service import ExportService
from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.backend.infrastructure.audio import AudioLoadError
from strange_uta_game.backend.infrastructure.exporters import get_exporter_by_name
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    CharType,
    get_char_type,
)
from strange_uta_game.frontend.theme import theme, ThemeColors

from .line_interface import LineDetailDialog
from .timing import (
    CharEditDialog,
    CompleteTimestampDialog,
    EditorToolBar,
    FileLoader,
    InsertGuideSymbolDialog,
    KaraokePreview,
    MiniSingerManager,
    ModifyCharacterDialog,
    SentenceSnapshotCommand,
    TimelineWidget,
    TransportBar,
    _SentenceSnapshotCommand,
)

__all__ = [
    "EditorInterface",
    # re-exports for backward compatibility
    "_SentenceSnapshotCommand",
    "SentenceSnapshotCommand",
    "TransportBar",
    "EditorToolBar",
    "KaraokePreview",
    "MiniSingerManager",
    "TimelineWidget",
    "ModifyCharacterDialog",
    "InsertGuideSymbolDialog",
    "CharEditDialog",
    "CompleteTimestampDialog",
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
        self._pressed_keys: set[str] = set()  # 当前按下的打轴按键集合（支持多键独立）
        self._last_position_update_time = 0.0  # 60fps UI 节流
        self._fast_forward_ms = 5000
        self._rewind_ms = 5000
        self._key_map = {}  # key_string -> action_name, populated by _apply_settings
        self._settings_loaded = False  # 配置是否已加载成功
        # 长按/短按支持
        self._long_press_timer = QTimer(self)
        self._long_press_timer.setSingleShot(True)
        self._long_press_timer.setInterval(300)
        self._long_press_timer.timeout.connect(self._on_long_press_timeout)
        self._pending_press_key: Optional[str] = None
        self._pending_press_action_short: Optional[str] = None
        self._pending_press_action_long: Optional[str] = None
        # 当 cp 标记被点击时，沿 _on_checkpoint_clicked → move_to_checkpoint →
        # on_checkpoint_moved (signal) → _handle_checkpoint_moved →
        # _apply_checkpoint_position 链路同步执行；此标志使后者跳过
        # set_current_position，从而不污染"选中字符"光标 (_current_char_idx)。
        # 区分：selected_cp（cp 标记选中态）vs selected_char（光标/选中字符态）。
        self._suppress_cp_cursor_move = False
        self._file_loader = FileLoader(self)
        self._mini_singer_manager: Optional[MiniSingerManager] = None
        self._init_ui()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAcceptDrops(True)
        self._bind_callback_signals()

        # 位置主动拉取定时器（UI 线程 60fps，替代旧的回调线程+信号推送）
        self._position_poll_timer = QTimer(self)
        self._position_poll_timer.setInterval(16)  # ~60fps
        self._position_poll_timer.timeout.connect(self._poll_audio_position)

        # 滚动模式：auto / always / never（由按钮循环切换，持久化到 config）
        self._scroll_mode: str = "auto"
        self._update_scroll_mode_btn_style()

        # 自动滚动状态机：用户交互挂起 → 播放到达新行 + 3s 无交互后恢复
        self._auto_scroll_suspended: bool = False
        self._auto_scroll_new_line_reached: bool = False
        self._auto_scroll_cooldown_timer = QTimer(self)
        self._auto_scroll_cooldown_timer.setSingleShot(True)
        self._auto_scroll_cooldown_timer.setInterval(6000)
        self._auto_scroll_cooldown_timer.timeout.connect(
            self._on_auto_scroll_cooldown_timeout
        )
        # eventFilter 中鼠标拖拽检测
        self._auto_scroll_mouse_press_pos = None

        # 按键音播放器（低延迟，基于 BASS Sample API）
        self._keysound_player = None
        self._keysound_enabled: bool = True
        # None 表示"尚未加载过任何风格"，确保 _apply_settings 首次调用时强制加载
        self._keysound_style = None
        self._init_keysound()

    def _init_keysound(self) -> None:
        """创建播放器并预加载默认风格样本（失败时静默跳过，不影响主功能）。"""
        try:
            from ...backend.infrastructure.audio.keysound_player import KeySoundPlayer
            self._keysound_player = KeySoundPlayer()
            self._reload_keysound("default")  # 预热：先加载默认风格
        except Exception as e:
            print(f"[KeySound] 初始化失败: {e}")

    _KEYSOUND_STYLE_FILES = {
        "osu":    ("osu_press.wav",    "osu_release.wav"),
        "arcade": ("arcade_press.wav", "arcade_release.wav"),
        "sci":    ("sci_press.wav",    "sci_release.wav"),
    }

    def _reload_keysound(self, style: str) -> None:
        """按指定风格重新加载按键音样本。"""
        if self._keysound_player is None:
            return
        from pathlib import Path as _Path
        sounds_dir = _Path(__file__).resolve().parent.parent.parent / "resource" / "sounds"
        press_name, release_name = self._KEYSOUND_STYLE_FILES.get(
            style, ("press.wav", "release.wav")
        )
        try:
            self._keysound_player.load(sounds_dir / press_name, sounds_dir / release_name)
        except Exception as e:
            print(f"[KeySound] 样本加载失败: {e}")

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
        self.toolbar.save_as_clicked.connect(self._on_save_as)
        self.toolbar.new_project_clicked.connect(self._on_new_project)
        self.toolbar.load_project_clicked.connect(self._on_load_project)
        self.toolbar.load_audio_clicked.connect(self._on_load_audio)
        self.toolbar.load_lyrics_clicked.connect(self._on_load_lyrics)
        self.toolbar.modify_char_clicked.connect(self._on_modify_char)
        self.toolbar.insert_guide_clicked.connect(self._on_insert_guide)
        self.toolbar.bulk_change_clicked.connect(self._on_bulk_change)
        self.toolbar.modify_line_clicked.connect(self._on_modify_line)
        self.toolbar.analyze_rubies_clicked.connect(self._on_analyze_rubies)
        self.toolbar.analyze_rubies_by_line_clicked.connect(self._on_analyze_rubies_by_line)
        self.toolbar.analyze_rubies_selected_clicked.connect(self._on_analyze_rubies_selected)
        self.toolbar.open_fulltext_clicked.connect(self._on_open_fulltext)
        self.toolbar.delete_rubies_by_type_clicked.connect(self._on_delete_rubies_by_type)
        self.toolbar.set_singer_by_line_clicked.connect(self._on_set_singer_by_line)
        self.toolbar.apply_singer_clicked.connect(self._on_apply_singer)
        self.toolbar.singer_manager_clicked.connect(self._on_singer_manager_clicked)
        self.toolbar.complete_timestamp_clicked.connect(self._on_complete_timestamp)
        self.toolbar.separate_symbol_timestamp_clicked.connect(self._on_separate_symbol_timestamp)
        self.toolbar.adjust_raw_timestamp_clicked.connect(self._on_adjust_raw_timestamp)
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
        self.timeline.waveform_visibility_changed.connect(self._on_waveform_visibility_changed)
        layout.addWidget(self.timeline)

        # 4) 歌词预览（占主要空间）
        self.preview = KaraokePreview(self)
        self.preview.line_clicked.connect(self._on_line_clicked)
        self.preview.checkpoint_clicked.connect(self._on_checkpoint_clicked)
        self.preview.char_selected.connect(self._on_char_selected)
        self.preview.char_edit_requested.connect(self._on_char_edit_requested)
        self.preview.seek_to_char_requested.connect(self._on_seek_to_char)
        self.preview.seek_to_checkpoint_requested.connect(self._on_seek_to_checkpoint)
        self.preview.singer_change_requested.connect(self._on_singer_change_selection)
        self.preview.delete_chars_requested.connect(self._on_delete_chars_requested)
        self.preview.delete_timestamp_requested.connect(self._on_delete_timestamp_requested)
        self.preview.insert_space_before_requested.connect(
            self._on_insert_space_before_requested
        )
        self.preview.insert_space_after_requested.connect(
            self._on_insert_space_after_requested
        )
        self.preview.merge_line_up_requested.connect(self._on_merge_line_up_requested)
        self.preview.delete_line_requested.connect(self._on_delete_line_requested)
        self.preview.insert_blank_line_before_requested.connect(
            self._on_insert_blank_line_before_requested
        )
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
        self.preview.auto_scroll_line_changed.connect(
            self._on_auto_scroll_line_changed
        )
        self.preview.user_interaction_during_auto_scroll.connect(
            self._on_user_interaction_during_auto_scroll
        )
        self.preview.installEventFilter(self)
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

        self.btn_clear_tags = PushButton("清除当前行时间戳", self)
        self.btn_clear_tags.setIcon(FIF.DELETE)
        self.btn_clear_tags.clicked.connect(self._on_clear_current_line_tags)
        bottom.addWidget(self.btn_clear_tags)

        self.btn_scroll_mode = PushButton("自动滚动", self)
        self.btn_scroll_mode.setObjectName("btnScrollMode")
        self.btn_scroll_mode.setIcon(FIF.SYNC)
        self.btn_scroll_mode.setToolTip(
            "切换歌词预览滚动模式：\n"
            "自动滚动 — 操作后挂起 6 秒自动恢复\n"
            "始终滚动 — 始终跟随播放位置\n"
            "不滚动 — 完全停用自动滚动"
        )
        self.btn_scroll_mode.clicked.connect(self._on_cycle_scroll_mode)
        theme.changed.connect(self._update_scroll_mode_btn_style)
        bottom.addWidget(self.btn_scroll_mode)

        bottom.addStretch()

        # 快捷键提示（动态跟随设置）
        self.lbl_shortcut_hint = QLabel("")
        self.lbl_shortcut_hint.setStyleSheet(f"font-size: 11px; color: {theme.text_hint.name()};")
        bottom.addWidget(self.lbl_shortcut_hint)

        layout.addLayout(bottom)

        # 6) 状态栏
        # 布局：[播放状态] <stretch> [当前行/字符/时间戳] <stretch> [总体进度]
        status = QHBoxLayout()
        status.setContentsMargins(5, 2, 5, 2)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet(f"font-size: 12px; color: {theme.text_primary.name()};")
        status.addWidget(self.lbl_status)
        status.addStretch()
        # 行号/字符/时间戳信息（#5：从打轴栏移到此处，与播放状态一同显示）
        self.lbl_line_info = QLabel("当前行: -")
        self.lbl_line_info.setStyleSheet(f"font-size: 12px; color: {theme.text_primary.name()};")
        status.addWidget(self.lbl_line_info)
        status.addStretch()
        self.lbl_progress = QLabel("行: 0/0 | 进度: 0%")
        self.lbl_progress.setStyleSheet(f"font-size: 12px; color: {theme.text_primary.name()};")
        status.addWidget(self.lbl_progress)
        layout.addLayout(status)

    def set_timing_service(self, timing_service: TimingService):
        """接入 TimingService 并完成全部回调/信号接线。

        本方法被设计为可重复调用（幂等）：切换音频引擎后 MainWindow 会再次调用它，
        以把所有回调重新挂到新引擎与服务上。两处 _global_qt 信号在重连前先断开旧连接，
        避免重复连接导致回调多次触发。
        """
        self._timing_service = timing_service
        self._timing_service.set_callbacks(self)
        # 注册渲染进度回调：经 pyqtSignal 自动 marshal 到 UI 线程。
        self._timing_service.set_render_progress_callback(
            lambda spd, prog: self._render_progress_signal.emit(float(spd), float(prog))
        )
        # 注册timing_servive焦点时间戳改变回调（先断开旧连接保证幂等）
        try:
            self._timing_service._global_qt._focus_moved_signal.disconnect(self._handle_foucus_moved)
        except (TypeError, RuntimeError):
            pass
        self._timing_service._global_qt._focus_moved_signal.connect(self._handle_foucus_moved)
        # 注册当前行居中滚动信号（先断开旧连接保证幂等）
        try:
            self._timing_service._global_qt._center_current_line_signal.disconnect(self._handle_center_current_line)
        except (TypeError, RuntimeError):
            pass
        self._timing_service._global_qt._center_current_line_signal.connect(self._handle_center_current_line)
        # 注册结构性变更信号（节奏点增减后需要刷新歌词显示）
        try:
            self._timing_service._global_qt._structural_change_signal.disconnect(self.refresh_lyric_display)
        except (TypeError, RuntimeError):
            pass
        self._timing_service._global_qt._structural_change_signal.connect(self.refresh_lyric_display)
        # 传音频引擎引用给 preview，使 paintEvent 可主动拉取高精度时间
        self.preview.set_audio_engine(timing_service._audio_engine)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _get_setting_interface(self):
        """Return SUG's settings interface even when embedded in a host window."""
        widget = self
        while widget is not None:
            setting_iface = getattr(widget, "settingInterface", None)
            if setting_iface is not None:
                return setting_iface
            widget = widget.parentWidget()
        main_window = self.window()
        return getattr(main_window, "settingInterface", None)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.set_project(self._store.project)
            if self._mini_singer_manager is not None:
                self._mini_singer_manager.set_project(self._store.project)
        elif change_type in ("rubies", "lyrics", "checkpoints"):
            self.refresh_lyric_display()
        elif change_type == "timetags":
            self._update_time_tags_display()
            self._update_status()
        elif change_type == "settings":
            self._apply_settings()

    def _apply_settings(self):
        """从 AppSettings 读取设定并应用到编辑器。"""
        try:
            self._apply_settings_inner()
        except Exception as e:
            # 此方法挂在 ProjectStore.data_changed("settings") 信号槽上，
            # 任何未捕获的 Python 异常都可能在 Qt C++ 派发层变为 0xC0000409
            # STATUS_STACK_BUFFER_OVERRUN 原生闪退（参见 commit fccb832）。
            # 兜底打日志，决不让 cascade 击穿到 Qt。
            print(f"[Settings] _apply_settings 失败: {e}")

    def _apply_settings_inner(self):
        if not self._store:
            return
        # In embedded mode, self.window() is the host window. Walk parents to
        # find SUG's own MainWindow so runtime settings apply immediately.
        setting_iface = self._get_setting_interface()
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
            "tag_now_extra",
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
            "nav_prev_char",
            "nav_next_char",
            "timestamp_up",
            "timestamp_down",
            "cycle_checkpoint",
            "cycle_checkpoint_prev",
            "delete_timestamp",
            "bulk_change",
            "modify_char",
            "insert_guide",
            "modify_line",
            "analyze_rubies",
            "analyze_rubies_by_line",
            "analyze_rubies_selected",
            "open_fulltext",
            "delete_rubies_by_type",
            "set_singer_by_line",
            "apply_singer",
            "timestamps_to_sentence_end",
            "quick_export",
            "insert_space",
            "merge_line_up",
            "tag_now_editor",
            "tag_now_extra_editor",
            "clear_all_checkpoints",
            "tag_and_delete_next",
        ]
        # 默认值兜底（当设置未写入新 schema 时使用）
        defaults = {
            "tag_now": "Space",
            "tag_now_extra": "",
            "play_pause": "D",
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
            "volume_up": "",
            "volume_down": "",
            "nav_prev_line": "UP",
            "nav_next_line": "DOWN",
            "nav_prev_char": "LEFT",
            "nav_next_char": "RIGHT",
            "timestamp_up": "ALT+UP",
            "timestamp_down": "ALT+DOWN",
            "cycle_checkpoint": "ALT+RIGHT",
            "cycle_checkpoint_prev": "ALT+LEFT",
            "delete_timestamp": "Backspace",
            "bulk_change": "CTRL+H:short",
            "modify_char": "",
            "insert_guide": "",
            "modify_line": "",
            "analyze_rubies": "",
            "analyze_rubies_by_line": "",
            "analyze_rubies_selected": "",
            "open_fulltext": "CTRL+T",
            "delete_rubies_by_type": "",
            "set_singer_by_line": "",
            "apply_singer": "",
            "timestamps_to_sentence_end": "",
            "quick_export": "",
            "insert_space": "M",
            "merge_line_up": "Shift+Enter",
            "tag_now_editor": "",
            "tag_now_extra_editor": "",
            "clear_all_checkpoints": "",
            "tag_and_delete_next": "",
        }

        def _normalize_trigger(raw: str) -> str:
            """将旧格式快捷键值（无 :short/:long 后缀）标准化为新格式。"""
            if not raw:
                return raw
            parts = []
            needs_update = False
            for k in raw.split(","):
                k = k.strip()
                if k:
                    if ":" not in k:
                        parts.append(f"{k}:short")
                        needs_update = True
                    else:
                        parts.append(k)
            return ",".join(parts) if needs_update else raw

        # 标记是否有旧格式需要持久化
        self._settings_migrated = False

        def _collect_map(mode_key: str) -> tuple[dict, dict, dict]:
            """返回 (key_map_short, key_map_long, action->key_str) 三套数据。"""
            key_map_short: dict[str, str] = {}
            key_map_long: dict[str, str] = {}
            action_to_keys: dict[str, str] = {}
            for action in action_names:
                raw = settings.get(
                    f"shortcuts.{mode_key}.{action}",
                    # 兼容旧 schema（无 mode_key 的扁平 shortcuts.xxx）
                    settings.get(f"shortcuts.{action}", defaults[action]),
                )
                # 旧格式自动更正：无后缀的键名补全为 :short
                normalized = _normalize_trigger(raw)
                if normalized != raw:
                    settings.set(f"shortcuts.{mode_key}.{action}", normalized)
                    self._settings_migrated = True
                    raw = normalized
                action_to_keys[action] = raw
                for k in (raw or "").split(","):
                    k = k.strip()
                    if k:
                        parts = k.split(":")
                        key_name = parts[0].strip()
                        trigger = parts[1].strip().lower() if len(parts) > 1 else "short"
                        if key_name:
                            if trigger == "long":
                                key_map_long[key_name.upper()] = action
                            else:
                                key_map_short[key_name.upper()] = action
            return key_map_short, key_map_long, action_to_keys

        timing_short, timing_long, timing_actions = _collect_map("timing_mode")
        edit_short, edit_long, edit_actions = _collect_map("edit_mode")
        # 旧格式迁移后自动保存
        if self._settings_migrated:
            settings.save()
            self._settings_migrated = False
        self._key_map_timing_short = timing_short
        self._key_map_timing_long = timing_long
        self._key_map_edit_short = edit_short
        self._key_map_edit_long = edit_long
        # 当前活动 map（按播放状态切换；初始为编辑模式）
        self._key_map_short = edit_short
        self._key_map_long = edit_long
        # 兼容旧引用
        self._key_map = edit_short
        old_speed_pct = self.transport.get_speed_value()
        new_speed_pct = self.transport.set_speed_range(
            settings.get("audio.speed_slider_min", 0.5),
            settings.get("audio.speed_slider_max", 1.0),
            emit_signal=False,
        )
        if self._timing_service and new_speed_pct != old_speed_pct:
            self._timing_service.set_speed(new_speed_pct / 100.0)
        # 应用渲染偏移（与导出偏移联动）
        render_offset = settings.get("export.offset_ms", 0)
        self.preview.set_global_offset(render_offset)
        # 同步工具栏偏移控件
        self.toolbar.edit_offset.blockSignals(True)
        self.toolbar.edit_offset.setText(str(render_offset))
        self.toolbar.edit_offset.blockSignals(False)
        # 将偏移量写入所有字符的渲染/导出时间戳
        if self._project:
            self._project.global_offset_ms = render_offset
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    # 旧版 .sug 升级 / 第三方导入可能在 timestamps 中混入
                    # 非 int（None / 字符串），ch.set_offset 内部的算术会抛
                    # TypeError。单个脏字符不应阻断整次 settings cascade。
                    try:
                        ch.set_offset(render_offset)
                    except Exception as e:
                        print(f"[Settings] set_offset 跳过脏字符: {e}")
        # 应用歌词对齐方式
        lyrics_alignment = settings.get("ui.lyrics_alignment", "center")
        self.preview.set_alignment(lyrics_alignment)
        # 应用左/右对齐页边距
        alignment_margin = settings.get("ui.alignment_margin", 168)
        self.preview.set_alignment_margin(alignment_margin)
        # 应用字体大小设置
        base_font_size = settings.get("ui.font_size", 18)
        current_line_size = settings.get("ui.current_line_font_size", 22)
        ruby_size = settings.get("ui.ruby_size", 10)
        cp_size = settings.get("ui.cp_size", 8)
        line_height_factor = settings.get("ui.line_height_factor", 1.20)
        ruby_spacing = settings.get("ui.ruby_spacing", 4)
        main_font = settings.get("ui.main_font", "Microsoft YaHei")
        ruby_font = settings.get("ui.ruby_font", "Microsoft YaHei")
        self.preview.set_font_sizes(base_font_size, current_line_size, ruby_size, cp_size, line_height_factor, ruby_spacing, main_font=main_font, ruby_font=ruby_font)
        # 应用 checkpoint 标记字符
        checkpoint_markers = settings.get("ui.checkpoint_markers", {})
        if checkpoint_markers:
            self.preview.set_checkpoint_markers(checkpoint_markers)
        # 更新快捷键提示（#6：只保留 9 项核心）
        self._update_shortcut_hint(timing_actions, edit_actions)
        # #7：打轴按钮文字联动 shortcuts.timing_mode.tag_now
        tag_key_raw = timing_actions.get("tag_now", "Space")
        tag_first = tag_key_raw.split(",")[0].split(":")[0].strip() if tag_key_raw else "Space"
        if hasattr(self, "btn_tag"):
            self.btn_tag.setText(f"打轴 ({tag_first})")
        # #8：同步模式指示器（首次应用设置时刷新）
        self._update_mode_indicator()
        # 应用禁用单击跳转设置
        disable_click_jump = settings.get("timing.disable_click_jump", False)
        self.preview.set_disable_click_jump(disable_click_jump)
        # 应用走字预览指引开关
        preview_guide = settings.get("timing.preview_guide_enabled", False)
        self.preview.set_preview_guide_enabled(preview_guide)
        # 应用滚动模式（设置页修改后同步到按钮和 preview）
        scroll_mode = settings.get("timing.scroll_mode", "auto")
        if scroll_mode != self._scroll_mode:
            self._scroll_mode = scroll_mode
            self._sync_scroll_mode()
        # 按键音开关、音量、风格
        self._keysound_enabled = bool(settings.get("timing.keysound_enabled", True))
        if self._keysound_player is not None:
            self._keysound_player.set_enabled(self._keysound_enabled)
        keysound_volume = int(settings.get("timing.keysound_volume", 100))
        if self._keysound_player is not None:
            self._keysound_player.set_volume(keysound_volume)
        keysound_style = str(settings.get("timing.keysound_style", "default"))
        samples_invalid = (
            self._keysound_player is not None and not self._keysound_player.is_loaded()
        )
        if keysound_style != self._keysound_style or samples_invalid:
            self._keysound_style = keysound_style
            self._reload_keysound(keysound_style)
        self._settings_loaded = True

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
                first_key = key.split(",")[0].split(":")[0].strip()
                if first_key:
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
        # 写入设置（与设置页面联动）—— 必须用 settingInterface 的共享实例，
        # 否则 _store.notify("settings") 触发 _apply_settings() 时读到的还是旧值，
        # 会立刻把刚设的偏移回滚掉。
        try:
            setting_iface = self._get_setting_interface()
            if setting_iface:
                app_settings = setting_iface.get_settings()
            else:
                from strange_uta_game.frontend.settings.app_settings import AppSettings
                app_settings = AppSettings()
            app_settings.set("export.offset_ms", offset_ms)
            app_settings.save()
        except Exception:
            pass
        # 同步到Project对象
        if self._project:
            self._project.global_offset_ms = offset_ms
        # 更新所有字符的偏移时间戳（单个脏字符不能阻断整次更新）
        if self._project:
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    try:
                        ch.set_offset(offset_ms)
                    except Exception as e:
                        print(f"[Offset] set_offset 跳过脏字符: {e}")
        # 更新渲染
        try:
            self.preview.set_global_offset(offset_ms)
        except Exception as e:
            print(f"[Offset] preview.set_global_offset 失败: {e}")
        # 通知 ProjectStore，使 Settings 页面等监听者同步更新
        if hasattr(self, "_store") and self._store:
            self._store.notify("settings")

    def set_project(self, project: Project):
        self._project = project
        # 获取AppSettings实例（与_apply_settings使用同一个）
        app_settings = None
        try:
            setting_iface = self._get_setting_interface()
            if setting_iface:
                app_settings = setting_iface.get_settings()
        except Exception:
            pass
        # 从项目读取全局偏移，若为None则使用config中的值（兼容旧版.sug）
        offset = project.global_offset_ms
        if offset is None:
            offset = app_settings.get("export.offset_ms", 0) if app_settings else 0
            # 写入project，保存时旧sug自动升级
            project.global_offset_ms = offset
        else:
            # 项目有偏移量，同步到config.json
            if app_settings:
                app_settings.set("export.offset_ms", offset)
                app_settings.save()
            InfoBar.success(
                title="已应用项目全局偏移",
                content=f"从项目读取到全局偏移: {offset}ms，已同步到设置",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        # 通知 ProjectStore，使 Settings 页面等监听者与项目偏移保持同步
        if hasattr(self, "_store") and self._store:
            self._store.notify("settings")

        # 先应用偏移到所有字符，再设置到preview（预渲染缓存会使用global_timestamps）
        for sentence in project.sentences:
            for ch in sentence.characters:
                ch.set_offset(offset)
        # 更新预览和工具栏
        self.preview.set_global_offset(offset)
        self.toolbar.edit_offset.blockSignals(True)
        self.toolbar.edit_offset.setText(str(offset))
        self.toolbar.edit_offset.blockSignals(False)
        # 设置到preview（会触发预渲染，此时global_timestamps已正确）
        self.preview.set_project(project)
        self._apply_checkpoint_position(
            self._timing_service.get_current_position()
            if self._timing_service
            else CheckpointPosition()
        )
        self._update_time_tags_display()
        self._update_status()
        # 重新应用设置（字体大小、行间距、对齐方式等）
        self._apply_settings()

    def release_resources(self):
        """释放音频资源"""
        if self._timing_service:
            self._timing_service.release()
        # timing_service.release() 会调用 BASS_Free，使 keysound sample handle 失效。
        # 在此归零 handle（避免野指针），并重置风格标记，确保下次 _apply_settings 强制重新加载。
        if self._keysound_player is not None:
            self._keysound_player.invalidate()
        self._keysound_style = None

    # ==================== 拖拽加载 ====================

    def dragEnterEvent(self, a0: Optional[QDragEnterEvent]):
        if a0 is None:
            return
        mime = a0.mimeData()
        if mime is not None and mime.hasUrls():
            for url in mime.urls():
                if self._file_loader.can_accept_drop(url.toLocalFile()):
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
            self._file_loader.handle_drop(url.toLocalFile())
        a0.acceptProposedAction()

    # ==================== 工具栏操作 ====================

    # 判断文本是否为全文本编辑器的内联格式。匹配以下任意一种特征：
    # - [>...] 句尾 token（我们格式独有）
    # - {原文||...} 双竖线注音块（我们格式独有）
    # - [T] 占位符（我们格式独有）
    # - [ts]X[ 连续逐字时间戳（LRC 每行只有一个起始 token，不会出现此模式）
    _INLINE_TS_DETECT_RE = re.compile(
        r"\[>[^\]]*\]|\{[^{}]+\|\||\[T\]|\[\d+:\d{2}\.\d{2}\].\["
    )

    def _on_paste_lyrics(self):
        """从剪贴板粘贴（Ctrl+V）。

        - 内联格式（含 [>...] 句尾 token 或 {原文||} 注音块）：调用 _paste_inline_format。
        - 空项目 / 无歌词行：整批加载歌词文本。
        - 已有歌词：在当前光标处插入（富信息 or 纯文本）。
        """
        from PyQt6.QtWidgets import QApplication

        clipboard = QApplication.clipboard()
        if not clipboard:
            return

        text = clipboard.text()

        # 内联时间戳格式优先（来自全文本编辑器的复制内容）
        if text and self._INLINE_TS_DETECT_RE.search(text):
            self._paste_inline_format(text)
            return

        # 空项目 / 无歌词：整批加载
        if self._file_loader.can_load_from_clipboard():
            if not text or not text.strip():
                return
            self._file_loader.load_lyrics_from_text(text)
            return

        # 已有歌词：在光标处插入
        self._paste_chars_at_cursor(text)

    def _paste_inline_format(self, text: str) -> None:
        """将内联时间戳格式文本（来自全文本编辑器）粘贴进项目。

        按行调用 ``parse_timed_line`` 解析，保留时间戳/ruby/演唱者/连词信息。

        - 空项目：解析结果直接作为全部 Sentence 加载。
        - 已有歌词：在当前光标行处插入——首行字符插入当前行光标位置，
          后续行创建新 Sentence 插入到当前行之后；光标后原有字符追加到最后一行末尾。
        """
        if not self._project:
            return

        from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
            parse_timed_line,
        )

        # 构建 singer 映射（与全文本编辑器一致）
        name_to_id: dict = {}
        default_singer = ""
        for s in self._project.singers:
            name_to_id[s.name] = s.id
            if s.is_default:
                default_singer = s.id
        if not default_singer and self._project.singers:
            default_singer = self._project.singers[0].id

        offset = getattr(self._project, "global_offset_ms", 0) or 0

        # 按行解析
        raw_lines = [seg.strip("\r") for seg in text.split("\n")]
        if len(raw_lines) > 1 and raw_lines[-1] == "" and text.endswith("\n"):
            raw_lines.pop()

        inherited = default_singer
        parsed_lines: list[list] = []
        for ls in raw_lines:
            chars, inherited = parse_timed_line(
                ls,
                name_to_singer_id=name_to_id,
                default_singer_id=default_singer,
                inherited_singer_id=inherited,
                offset_ms=offset,
            )
            parsed_lines.append(chars)

        if not parsed_lines:
            return

        project = self._project

        # ── 空项目：直接整批加载 ──
        if not project.sentences:
            from strange_uta_game.backend.domain import Sentence

            def _load():
                sentences = []
                for chars in parsed_lines:
                    singer = (chars[0].singer_id if chars and chars[0].singer_id
                              else default_singer)
                    sentences.append(Sentence(singer_id=singer, characters=chars))
                project.sentences = sentences
                last = len(sentences) - 1
                return last, 0, 0, "lyrics"

            self._execute_structural_edit("粘贴内联格式", _load)
            return

        # ── 已有歌词：插入光标处 ──
        if self.preview._focus_line_idx >= 0 and self.preview._focus_char_idx >= 0:
            line_idx = self.preview._focus_line_idx
            insert_at = (min(self.preview._focus_char_idx,
                             self.preview._focus_char_range_end)
                         if self.preview._focus_char_range_end >= 0
                         else self.preview._focus_char_idx)
        else:
            line_idx = self._current_line_idx
            insert_at = self.preview._current_char_idx

        if line_idx < 0 or line_idx >= len(project.sentences):
            return

        from strange_uta_game.backend.domain import Sentence

        sentence = project.sentences[line_idx]
        original_len = len(sentence.characters)
        pos = max(0, min(insert_at + 1, original_len))

        def _mutate_inline():
            s = project.sentences[line_idx]
            after_chars = list(s.characters[pos:])
            s.characters = list(s.characters[:pos])

            # 首行字符插入当前行
            for ch in parsed_lines[0]:
                ch.is_line_end = False
                s.characters.append(ch)

            insert_after = line_idx
            if len(parsed_lines) == 1:
                # 单行粘贴：光标后原有字符追加回来
                for ch in after_chars:
                    ch.is_line_end = False
                    s.characters.append(ch)
            else:
                for i, seg_chars in enumerate(parsed_lines[1:]):
                    seg = list(seg_chars)
                    # 最后一段拼接光标后原有字符
                    if i == len(parsed_lines) - 2:
                        seg.extend(after_chars)
                    for ch in seg:
                        ch.is_line_end = False
                    if seg:
                        seg[-1].is_line_end = True
                    singer = (seg[0].singer_id if seg and seg[0].singer_id
                              else sentence.singer_id)
                    new_s = Sentence(singer_id=singer, characters=seg)
                    project.sentences.insert(insert_after + 1, new_s)
                    insert_after += 1

            if s.characters:
                s.characters[-1].is_line_end = True

            last_line = insert_after
            last_char = max(0, len(project.sentences[last_line].characters) - 1)
            return last_line, last_char, 0, "lyrics"

        self._execute_structural_edit("粘贴内联格式", _mutate_inline)

    def _on_copy_chars(self):
        """复制选中字符为内联时间戳格式（Ctrl+C）。

        编码为内联格式字符串写入系统剪贴板，Ctrl+V 时可经
        _INLINE_TS_DETECT_RE 识别并通过 _paste_inline_format 无损还原。
        """
        from PyQt6.QtWidgets import QApplication
        from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
            sentence_to_timed_line,
        )

        if not self._project:
            return

        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            start = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
            end = max(self.preview._focus_char_idx, self.preview._focus_char_range_end)
        else:
            line_idx = self._current_line_idx
            start = self.preview._current_char_idx
            end = start

        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if not sentence.characters:
            return

        start = max(0, min(start, len(sentence.characters) - 1))
        end = max(start, min(end, len(sentence.characters) - 1))
        chars = [deepcopy(sentence.characters[i]) for i in range(start, end + 1)]
        if not chars:
            return

        id_to_name = {s.id: s.name for s in self._project.singers}
        offset = getattr(self._project, "global_offset_ms", 0) or 0
        inline_text, _ = sentence_to_timed_line(
            chars,
            singer_id_to_name=id_to_name,
            line_singer_id=sentence.singer_id,
            default_singer_id=sentence.singer_id,
            offset_ms=offset,
        )

        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(inline_text)

        InfoBar.success(
            title="已复制",
            content=f"已复制 {len(chars)} 个字符",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=1500,
            parent=self,
        )

    def _paste_chars_at_cursor(self, clipboard_text: str) -> None:
        """在当前光标处插入字符（Ctrl+V，已有歌词时）。

        富信息粘贴：剪贴板文本与上次 Ctrl+C 一致时插入字符深拷贝（保留注音/
        节奏点/时间戳/演唱者等）。纯文本：逐字构造为新歌词字符。
        纯文本含换行时按行拆分，首段插入当前行，后续段依次新建行；
        光标后的原有字符拼接至最后一段末尾。
        纯文本粘贴后自动对受影响字符范围执行局部注音分析（不影响已有注音）。
        插入经 _execute_structural_edit 包装，纳入 undo/redo。
        """
        if not self._project:
            return

        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
        ):
            line_idx = self.preview._focus_line_idx
            if self.preview._focus_char_range_end >= 0:
                insert_at = min(
                    self.preview._focus_char_idx, self.preview._focus_char_range_end
                )
            else:
                insert_at = self.preview._focus_char_idx
        else:
            line_idx = self._current_line_idx
            insert_at = self.preview._current_char_idx

        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]

        buffer = getattr(self, "_char_clipboard", None)
        buffer_text = getattr(self, "_char_clipboard_text", None)
        if buffer and clipboard_text == buffer_text:
            new_chars = []
            for c in buffer:
                ch = deepcopy(c)
                # 插入位非行尾时清理行尾标记与 UI 选中态，避免重复行尾/选中
                ch.is_line_end = False
                ch.selected_checkpoint_idx = None
                new_chars.append(ch)
        else:
            if not clipboard_text or not clipboard_text.strip():
                return
            # 按换行拆分，保留空行作为空行（维持用户排版）；仅丢弃末尾换行符产生的终止空段
            lines = [seg.strip("\r") for seg in clipboard_text.split("\n")]
            if len(lines) > 1 and lines[-1] == "" and clipboard_text.endswith("\n"):
                lines.pop()
            if not lines:
                return

            if len(lines) == 1:
                new_chars = [
                    Character(char=c, singer_id=sentence.singer_id)
                    for c in lines[0]
                ]
                if not new_chars:
                    return

                project = self._project
                original_len = len(sentence.characters)
                pos = max(0, min(insert_at, original_len))
                affected = set(range(pos, pos + len(lines[0])))

                def _mutate():
                    s = project.sentences[line_idx]
                    for off, ch in enumerate(new_chars):
                        s.insert_character(pos + off, ch)
                    return line_idx, pos + len(new_chars) - 1, 0, "lyrics"

                self._execute_structural_edit("粘贴字符", _mutate)
                self._analyze_rubies_specs_async(
                    [(line_idx, affected)], "粘贴字符注音分析",
                    show_winrt_dialog=False,
                )
                return

            # 多行：拆行粘贴
            singer_id = sentence.singer_id
            project = self._project
            original_len = len(sentence.characters)
            pos = max(0, min(insert_at, original_len))
            has_after = pos < original_len

            def _mutate_multi():
                s = project.sentences[line_idx]
                after_chars = list(s.characters[pos:])
                s.characters = s.characters[:pos]

                # 第一段拼入当前行
                for c in lines[0]:
                    s.characters.append(Character(char=c, singer_id=singer_id))
                for ch in s.characters:
                    ch.is_line_end = False
                if s.characters:
                    s.characters[-1].is_line_end = True

                # 后续段逐行插入
                insert_after = line_idx
                for i, seg_text in enumerate(lines[1:]):
                    seg_chars = [
                        Character(char=c, singer_id=singer_id) for c in seg_text
                    ]

                    # 最后一段拼接光标后原有字符
                    if i == len(lines) - 2:
                        seg_chars.extend(after_chars)

                    for ch in seg_chars:
                        ch.is_line_end = False
                    if seg_chars:
                        seg_chars[-1].is_line_end = True

                    new_sentence = Sentence(
                        singer_id=singer_id, characters=seg_chars
                    )
                    project.sentences.insert(insert_after + 1, new_sentence)
                    insert_after += 1

                last_line = insert_after
                last_sentence = project.sentences[last_line]
                last_char = max(0, len(last_sentence.characters) - 1)
                return last_line, last_char, 0, "lyrics"

            self._execute_structural_edit("粘贴字符", _mutate_multi)
            # 收集所有受影响行/范围，合并为一次异步分析，避免多个 InfoBar
            _paste_specs: list = []
            if lines[0]:
                _paste_specs.append((line_idx, set(range(pos, pos + len(lines[0])))))
            for li in range(line_idx + 1, line_idx + len(lines) - 1):
                _paste_specs.append((li, None))
            if len(lines) > 1 and lines[-1]:
                _affected_last = (
                    set(range(0, len(lines[-1]))) if has_after else None
                )
                _paste_specs.append((line_idx + len(lines) - 1, _affected_last))
            if _paste_specs:
                self._analyze_rubies_specs_async(
                    _paste_specs, "粘贴字符注音分析",
                    show_winrt_dialog=False,
                )

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

        # 已有正式保存路径（非 .cache 临时）→ 直接保存
        if (
            store is not None
            and store.save_path
            and not store.is_temp_save_path()
        ):
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

        # 无正式保存路径 / 仍是临时项目 → 弹出另存为对话框
        suggested = store.suggested_save_path(".sug") if store else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "保存项目", suggested, "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        # 登记工作目录到 config
        if store:
            store.set_working_dir(path)

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

    def _on_new_project(self):
        """新建项目（检查当前项目是否需要保存）"""
        if self._project:
            store = getattr(self, "_store", None)
            # 检查是否有未保存的更改
            if store and store.dirty:
                msg = QMessageBox(self)
                msg.setWindowTitle("保存当前项目")
                msg.setText("当前项目有未保存的更改，是否保存？")
                btn_save = msg.addButton("保存", QMessageBox.ButtonRole.AcceptRole)
                msg.addButton("放弃", QMessageBox.ButtonRole.DestructiveRole)
                btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
                msg.setDefaultButton(btn_save)
                msg.exec()
                clicked = msg.clickedButton()
                if clicked is btn_save:
                    self._on_save()
                elif clicked is btn_cancel:
                    return

        # 创建新项目
        from strange_uta_game.backend.application import ProjectService

        project_service = ProjectService()
        project = project_service.create_project()
        if self._store:
            self._store.load_project(project)
        else:
            self.set_project(project)

    def _on_save_as(self):
        """项目另存为"""
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
        suggested = store.suggested_save_path(".sug") if store else ""
        path, _ = QFileDialog.getSaveFileName(
            self, "另存为", suggested, "StrangeUtaGame 项目 (*.sug);;所有文件 (*.*)"
        )
        if not path:
            return
        if not path.endswith(".sug"):
            path += ".sug"

        # 登记工作目录到 config
        if store:
            store.set_working_dir(path)

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

    def _on_load_project(self):
        """加载项目文件"""
        self._file_loader.prompt_load_project()

    def _on_load_audio(self):
        self._file_loader.prompt_load_audio()

    def _on_load_lyrics(self):
        """加载歌词文件到当前项目（替换现有歌词）。"""
        self._file_loader.prompt_load_lyrics()

    def _on_undo(self):
        if self._timing_service and self._timing_service.can_undo():
            self._timing_service.undo()
            cmd = self._timing_service.command_manager.get_last_undone_command()
            if isinstance(cmd, SentenceSnapshotCommand) and cmd.undo_position:
                self._sync_after_structure_change(
                    change_type="lyrics",
                    focus_line_idx=cmd.undo_position[0],
                    focus_char_idx=cmd.undo_position[1],
                    move_cp=getattr(cmd, "move_cp", True),
                )
            else:
                self._update_time_tags_display()
                self._apply_checkpoint_position(self._timing_service.get_current_position())
                self._update_status()
            self._sync_focus_from_timing_service()

    def _on_redo(self):
        if self._timing_service and self._timing_service.can_redo():
            self._timing_service.redo()
            cmd = self._timing_service.command_manager.get_last_redone_command()
            if isinstance(cmd, SentenceSnapshotCommand) and cmd.redo_position:
                self._sync_after_structure_change(
                    change_type="lyrics",
                    focus_line_idx=cmd.redo_position[0],
                    focus_char_idx=cmd.redo_position[1],
                    move_cp=getattr(cmd, "move_cp", True),
                )
            else:
                self._update_time_tags_display()
                self._apply_checkpoint_position(self._timing_service.get_current_position())
                self._update_status()
            self._sync_focus_from_timing_service()

    def _sync_focus_from_timing_service(self):
        """将 TimingService 当前位置同步到 focus 域。"""
        if self._timing_service:
            pos = self._timing_service.get_current_position()
            self.preview.set_focus_position(pos.line_idx, pos.char_idx)

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
                sel_line = self.preview._focus_line_idx
                sel_start = self.preview._focus_char_idx
                sel_end = self.preview._focus_char_range_end
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
        sel_line = self.preview._focus_line_idx
        sel_start = self.preview._focus_char_idx
        sel_end = self.preview._focus_char_range_end

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
                cursor_pos = (self._current_line_idx, self.preview._current_char_idx)
                cmd.undo_position = cursor_pos
                cmd.redo_position = cursor_pos
                command_manager.execute(cmd)

            # Reapply global offset & rebuild global checkpoints
            self._reapply_global_offset()
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

    def _on_modify_line(self):
        """打开修改选中行对话框（复用行编辑界面的 LineDetailDialog）"""
        if not self._project:
            return

        line_idx = self.preview._current_line_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return

        sentence = self._project.sentences[line_idx]
        before_sentences = deepcopy(self._project.sentences)

        dialog = LineDetailDialog(sentence, project=self._project, parent=self)
        dialog.exec()

        if dialog.was_modified():
            command_manager = None
            if self._timing_service:
                command_manager = self._timing_service.command_manager
            if command_manager is not None:
                after_sentences = deepcopy(self._project.sentences)
                cmd = SentenceSnapshotCommand(
                    self._project,
                    before_sentences,
                    after_sentences,
                    f"修改选中行（第 {line_idx + 1} 句）",
                )
                cursor_pos = (self._current_line_idx, self.preview._current_char_idx)
                cmd.undo_position = cursor_pos
                cmd.redo_position = cursor_pos
                command_manager.execute(cmd)

            self._reapply_global_offset()
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("rubies")
                self._store.notify("checkpoints")
                self._store.notify("lyrics")

    def _on_delete_rubies_by_type(self):
        """工具栏「按类型删除注音」入口。

        与全文本编辑界面的同名功能逻辑保持一致（复用 DeleteRubyByTypeDialog 与
        扩展类型集合规则），但通过 :py:meth:`_execute_structural_edit` 包装为
        SentenceSnapshotCommand，支持撤销/重做并自动同步 timing_service。

        勾选 HIRAGANA → 同时移除小假名(ぁぃ等)与促音 っ；
        勾选 KATAKANA → 同时移除小假名(ァィ等)与促音 ッ。
        """
        if not self._project:
            return
        # 复用 fulltext_interface 的对话框（CharType 复选 + 默认勾选平假名/片假名）
        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        from .fulltext_interface import DeleteRubyByTypeDialog

        app_settings = AppSettings()
        saved_types = app_settings.get("auto_check.delete_ruby_types", [])

        dlg = DeleteRubyByTypeDialog(self, initial_types=saved_types)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        selected = dlg.selected_types()

        # 保存用户选择到配置（无论是否有变化）
        app_settings.set("auto_check.delete_ruby_types", dlg.selected_type_names())
        app_settings.save()

        if not selected:
            return

        # 拆解选中项：区分普通 CharType 与片假名子类型
        from .fulltext_interface import _ruby_is_all_hiragana
        ct_selected = {x for x in selected if isinstance(x, CharType)}
        delete_kata_hira = "katakana_hiragana_ruby" in selected
        delete_kata_eng = "katakana_english_ruby" in selected

        extended = set(ct_selected)
        if CharType.HIRAGANA in ct_selected:
            extended.add(CharType.SOKUON)  # 平假名选中时同时处理促音っ

        removed_box = [0]

        def _mutate() -> Optional[tuple[int, int, Optional[int], str]]:
            assert self._project is not None
            removed = 0
            for sentence in self._project.sentences:
                kanji_linked = get_kanji_linked_indices(sentence.characters)
                for idx, ch in enumerate(sentence.characters):
                    if not ch.ruby:
                        continue
                    if idx in kanji_linked:
                        continue  # 与汉字连词，视为汉字，保留注音
                    ct = get_char_type(ch.char)

                    # 片假名（不含促音ッ，ッ/っ 由 SOKUON 路径独立处理）
                    is_kata_family = ct == CharType.KATAKANA
                    if is_kata_family:
                        if delete_kata_hira or delete_kata_eng:
                            is_hira = _ruby_is_all_hiragana(ch.ruby.text)
                            if (is_hira and delete_kata_hira) or (not is_hira and delete_kata_eng):
                                ch.set_ruby(None)
                                removed += 1
                        continue

                    if ct in extended:
                        if ct == CharType.SOKUON and ch.char == "っ" and CharType.HIRAGANA not in ct_selected:
                            continue
                        ch.set_ruby(None)
                        removed += 1
            if removed == 0:
                return None
            removed_box[0] = removed
            # 焦点保持在当前位置；ruby 变更使用 "rubies" 通道刷新（与 fulltext 一致）
            return (self._current_line_idx, self.preview._current_char_idx, None, "rubies")

        ok = self._execute_structural_edit("按类型删除注音", _mutate)
        if not ok:
            InfoBar.info(
                title="无变化",
                content="所选类型范围内没有需要删除的注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        labels = ", ".join(
            label for ct, label in DeleteRubyByTypeDialog._TYPE_LABELS if ct in selected
        )
        InfoBar.success(
            title="删除完成",
            content=f"已删除 {removed_box[0]} 个注音（类型: {labels}）",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _on_set_singer_by_line(self):
        """工具栏「按行设置演唱者」入口。

        弹出对话框显示所有行（只读），用户可多选行后批量设置演唱者。
        点击"应用"按钮后不关闭对话框，方便继续设置其他行。
        通过 _execute_structural_edit 包装，支持撤销/重做。
        """
        if not self._project:
            return
        if not self._project.singers:
            InfoBar.warning(
                title="无演唱者",
                content="项目中没有演唱者，请先添加演唱者",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        from .timing.dialogs import SetSingerByLineDialog

        dlg = SetSingerByLineDialog(
            self._project.sentences,
            [s for s in self._project.singers if s.enabled],
            self,
            focus_line_idx=self._current_line_idx,
        )
        dlg.apply_requested.connect(self._on_apply_singer_by_line)
        dlg.exec()

    def _on_apply_singer_by_line(self, result_map: dict):
        """处理按行设置演唱者的应用请求"""
        if not self._project or not result_map:
            return

        def _mutate() -> Optional[tuple[int, int, Optional[int], str]]:
            assert self._project is not None
            changed = 0
            for line_idx, singer_id in result_map.items():
                if 0 <= line_idx < len(self._project.sentences):
                    sentence = self._project.sentences[line_idx]
                    sentence.singer_id = singer_id
                    for ch in sentence.characters:
                        if ch.singer_id != singer_id:
                            ch.singer_id = singer_id
                            if ch.ruby:
                                ch.push_to_ruby()
                    changed += 1
            if changed == 0:
                return None
            return (self._current_line_idx, self.preview._current_char_idx, None, "singers")

        ok = self._execute_structural_edit("按行设置演唱者", _mutate)
        if not ok:
            InfoBar.info(
                title="无变化",
                content="所选行的演唱者未发生变化",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        InfoBar.success(
            title="设置完成",
            content=f"已为 {len(result_map)} 行设置演唱者",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _on_apply_singer(self):
        """工具栏「应用演唱者」入口。

        弹出对话框显示当前选中字符信息，用户可选择演唱者并应用到选中字符。
        通过 _execute_structural_edit 包装，支持撤销/重做。
        """
        if not self._project:
            return
        if not self._project.singers:
            InfoBar.warning(
                title="无演唱者",
                content="项目中没有演唱者，请先添加演唱者",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        line_idx = self._current_line_idx
        char_idx = self.preview._current_char_idx

        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        # 获取选中字符范围
        start_idx = char_idx
        end_idx = char_idx
        if (
            self.preview._focus_line_idx == line_idx
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            start_idx = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
            end_idx = max(self.preview._focus_char_idx, self.preview._focus_char_range_end)

        chars = sentence.characters[start_idx:end_idx + 1]
        char_text = "".join(c.char for c in chars)

        # 获取当前演唱者信息
        singer_ids = set()
        for ch in chars:
            if ch.singer_id:
                singer_ids.add(ch.singer_id)

        singer_map = {s.id: s for s in self._project.singers}
        current_singers = [singer_map[sid] for sid in singer_ids if sid in singer_map]

        from .timing.dialogs import ApplySingerDialog

        dlg = ApplySingerDialog(
            char_text,
            current_singers,
            [s for s in self._project.singers if s.enabled],
            self,
        )
        dlg.apply_requested.connect(lambda singer_id: self._on_apply_singer_to_chars(line_idx, start_idx, end_idx, singer_id))
        dlg.exec()

    def _on_apply_singer_to_chars(self, line_idx: int, start_idx: int, end_idx: int, singer_id: str):
        """处理应用演唱者到选中字符的请求"""
        if not self._project:
            return

        def _mutate() -> Optional[tuple[int, int, Optional[int], str]]:
            assert self._project is not None
            sentence = self._project.sentences[line_idx]
            changed = False
            for ci in range(start_idx, end_idx + 1):
                if 0 <= ci < len(sentence.characters):
                    ch = sentence.characters[ci]
                    if ch.singer_id != singer_id:
                        ch.singer_id = singer_id
                        if ch.ruby:
                            ch.push_to_ruby()
                        changed = True
            # 如果整个行都被选中，也更新 sentence.singer_id
            if start_idx == 0 and end_idx >= len(sentence.characters) - 1:
                if sentence.singer_id != singer_id:
                    sentence.singer_id = singer_id
                    changed = True
            if not changed:
                return None
            return (line_idx, start_idx, None, "singers")

        ok = self._execute_structural_edit("应用演唱者", _mutate)
        if not ok:
            InfoBar.info(
                title="无变化",
                content="所选字符的演唱者未发生变化",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        InfoBar.success(
            title="设置完成",
            content="已为选中字符设置演唱者",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=4000,
            parent=self,
        )

    def _on_singer_manager_clicked(self):
        """工具栏「演唱者管理」入口。

        打开一个微型浮动窗口，复用 SingerManagerInterface 的全部功能，
        允许用户在打轴的同时随时编辑演唱者。
        """
        if self._mini_singer_manager is not None and self._mini_singer_manager.isVisible():
            self._mini_singer_manager.raise_()
            self._mini_singer_manager.activateWindow()
            return

        self._mini_singer_manager = MiniSingerManager(self)
        if self._project:
            self._mini_singer_manager.set_project(self._project)
        if hasattr(self, "_store") and self._store:
            self._mini_singer_manager.set_store(self._store)
        self._mini_singer_manager.show_at_cursor()

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

        # 快照 before：InsertGuideSymbolDialog 会原地修改 project.sentences
        before_sentences = deepcopy(self._project.sentences)

        dialog = InsertGuideSymbolDialog(sentence, char_idx, self)
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
                    f"插入导唱符（第 {line_idx + 1} 句 第 {char_idx + 1} 字前）",
                )
                cursor_pos = (self._current_line_idx, self.preview._current_char_idx)
                cmd.undo_position = cursor_pos
                cmd.redo_position = cursor_pos
                command_manager.execute(cmd)

            # Reapply global offset & rebuild global checkpoints
            self._reapply_global_offset()
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("lyrics")

    def _on_complete_timestamp(self):
        """补全时间戳功能入口"""
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

        from .timing.dialogs import CompleteTimestampDialog

        dlg = CompleteTimestampDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.was_apply_clicked():
            return

        scope_types = dlg.get_scope_types()
        exclude_rules = dlg.get_exclude_rules()
        head_offset_ms = dlg.get_head_offset_ms()
        tail_offset_ms = dlg.get_tail_offset_ms()

        if not scope_types:
            InfoBar.warning(
                title="未选择适用范围",
                content="请至少选择一种字符类型",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 执行补全时间戳
        count = self._execute_complete_timestamp(scope_types, exclude_rules, head_offset_ms, tail_offset_ms)

        if count > 0:
            InfoBar.success(
                title="补全完成",
                content=f"已为 {count} 个字符补全时间戳",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
        else:
            InfoBar.info(
                title="无需补全",
                content="没有找到需要补全时间戳的字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_separate_symbol_timestamp(self):
        """分离符号时间戳功能入口"""
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

        from .timing.dialogs import SeparateSymbolTimestampDialog

        dlg = SeparateSymbolTimestampDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.was_apply_clicked():
            return

        symbol_chars = dlg.get_symbol_chars()
        pre_comp_ms = dlg.get_pre_comp_ms()
        post_comp_ms = dlg.get_post_comp_ms()

        if not symbol_chars:
            InfoBar.warning(
                title="未选择符号分组",
                content="请至少选择一个符号分组",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        post_count, pre_count = self._execute_separate_symbol_timestamp(
            symbol_chars, pre_comp_ms, post_comp_ms
        )

        total = post_count + pre_count
        if total > 0:
            InfoBar.success(
                title="分离完成",
                content=f"共处理 {total} 个符号（后补偿 {post_count} 个，前补偿 {pre_count} 个）",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
        else:
            InfoBar.info(
                title="无需处理",
                content="没有找到符合条件的符号时间戳",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

    def _on_adjust_raw_timestamp(self):
        """调整原始时间戳功能入口 — 打开非模态调整窗口，允许边测试边调整"""
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

        from .timing.dialogs import AdjustRawTimestampDialog

        if not hasattr(self, "_adjust_ts_dlg") or self._adjust_ts_dlg is None or not self._adjust_ts_dlg.isVisible():
            self._adjust_ts_dlg = AdjustRawTimestampDialog(self)
            self._adjust_ts_dlg.apply_requested.connect(self._on_apply_adjust_raw_timestamp)

        self._adjust_ts_dlg.show()
        self._adjust_ts_dlg.raise_()
        self._adjust_ts_dlg.activateWindow()

    def _on_apply_adjust_raw_timestamp(self, delta_ms: int):
        """处理调整原始时间戳对话框的应用请求"""
        if not self._project:
            return

        project = self._project

        def _mutate():
            modified = 0
            for sentence in project.sentences:
                for ch in sentence.characters:
                    if ch.check_count > 0 and ch.timestamps:
                        ch.timestamps = [max(0, ts + delta_ms) for ts in ch.timestamps]
                        modified += 1
                    if ch.sentence_end_ts is not None:
                        ch.sentence_end_ts = max(0, ch.sentence_end_ts + delta_ms)
                    ch._update_offset_timestamps()
                    ch.push_to_ruby()
            if modified == 0 and all(
                ch.sentence_end_ts is None
                for sentence in project.sentences
                for ch in sentence.characters
            ):
                return None
            return (self._current_line_idx, self.preview._current_char_idx, None, "timetags")

        ok = self._execute_structural_edit("调整原始时间戳", _mutate)
        if ok:
            if hasattr(self, "_adjust_ts_dlg") and self._adjust_ts_dlg is not None:
                self._adjust_ts_dlg.set_status(f"已成功偏移 {delta_ms:+d} ms")
            InfoBar.success(
                title="调整完成",
                content=f"所有原始时间戳已整体偏移 {delta_ms:+d} ms",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
        else:
            if hasattr(self, "_adjust_ts_dlg") and self._adjust_ts_dlg is not None:
                self._adjust_ts_dlg.set_status("无可调整的时间戳", success=False)

    def _execute_complete_timestamp(self, scope_types: set[str], exclude_rules: list[str], head_offset_ms: int = 150, tail_offset_ms: int = 150) -> int:
        """执行补全时间戳的核心逻辑

        Args:
            scope_types: 选中的字符类型集合
            exclude_rules: 选中的排除规则列表
            head_offset_ms: 行首无前方时间戳时，向后找到时间戳后扣除的毫秒数
            tail_offset_ms: 行尾无后方时间戳时，向前找到时间戳后增加的毫秒数

        Returns:
            补全的字符数量
        """
        if not self._project:
            return 0

        from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
            CharType,
            get_char_type,
        )

        # 映射 scope_types 到 CharType
        type_map = {
            "kanji": CharType.KANJI,
            "hiragana": CharType.HIRAGANA,
            "katakana": CharType.KATAKANA,
            "sokuon": CharType.SOKUON,
            "long_vowel": CharType.LONG_VOWEL,
            "alphabet": CharType.ALPHABET,
            "number": CharType.NUMBER,
            "symbol": CharType.SYMBOL,
        }

        target_types = set()
        for key in scope_types:
            if key in type_map:
                target_types.add(type_map[key])

        # 捨仮名需要特殊处理（小假名）
        include_chisai_kana = "chisai_kana" in scope_types
        _SMALL_KANA = set("ぁぃぅぇぉゃゅょゎァィゥェォャュョヮゕゖ")

        # 拨音需要特殊处理
        include_chon = "chon" in scope_types
        _CHON_CHARS = set("んン")

        exclude_linked = "linked" in exclude_rules

        def _is_target_char(ch_obj, char_idx: int, chars_list) -> bool:
            """判断字符是否为目标类型（check_count=0 且符合适用规则）"""
            char = ch_obj.char
            # 跳过 check_count > 0 的字符（已有节奏点，无需补全）
            if ch_obj.check_count > 0:
                return False
            # 跳过被连词字符（如果启用排除）
            # 连词组中的所有字符都应被排除：当前字符 linked_to_next=True 或前一个字符 linked_to_next=True
            if exclude_linked:
                if ch_obj.linked_to_next:
                    return False
                if char_idx > 0 and chars_list[char_idx - 1].linked_to_next:
                    return False

            # 捨仮名检查
            if include_chisai_kana and char in _SMALL_KANA:
                return True

            # 拨音检查
            if include_chon and char in _CHON_CHARS:
                return True

            # 普通类型检查
            try:
                char_type = get_char_type(char)
                return char_type in target_types
            except (ValueError, IndexError):
                return False

        def _find_prev_timestamp(line_idx: int, char_idx: int) -> Optional[int]:
            """向前逐字查找最近的时间戳（在同一行内）

            同时检查普通时间戳和句尾时间戳，取较大者（更接近目标字符）。
            """
            sentence = self._project.sentences[line_idx]
            for ci in range(char_idx - 1, -1, -1):
                ch = sentence.characters[ci]
                candidates = []
                if ch.timestamps:
                    candidates.append(ch.timestamps[-1])
                if ch.is_sentence_end and ch.sentence_end_ts is not None:
                    candidates.append(ch.sentence_end_ts)
                if candidates:
                    return max(candidates)
            return None

        def _find_next_timestamp(line_idx: int, char_idx: int) -> Optional[int]:
            """向后逐字查找最近的时间戳（在同一行内）

            同时检查普通时间戳和句尾时间戳，取较小者（更接近目标字符）。
            """
            sentence = self._project.sentences[line_idx]
            for ci in range(char_idx + 1, len(sentence.characters)):
                ch = sentence.characters[ci]
                candidates = []
                if ch.timestamps:
                    candidates.append(ch.timestamps[0])
                if ch.is_sentence_end and ch.sentence_end_ts is not None:
                    candidates.append(ch.sentence_end_ts)
                if candidates:
                    return min(candidates)
            return None

        total_count = 0

        def _mutate() -> Optional[tuple[int, int, Optional[int], str]]:
            nonlocal total_count
            assert self._project is not None

            for line_idx, sentence in enumerate(self._project.sentences):
                chars = sentence.characters
                total_chars = len(chars)
                i = 0
                while i < total_chars:
                    # 跳过不符合适用条件的字符
                    if not _is_target_char(chars[i], i, chars):
                        i += 1
                        continue

                    # 收集连续的待补全字符段
                    segment_start = i
                    while i < total_chars and _is_target_char(chars[i], i, chars):
                        i += 1
                    segment_end = i  # 不包含

                    segment_len = segment_end - segment_start

                    # 判断段的位置
                    is_at_start = (segment_start == 0)  # 行首
                    # 行尾：段之后若仅剩“多余空格占位符”（句尾 token 贴轴后被解析
                    # 出来的裸空格），也视为行尾。必须校验 check_count==0，避免误吞
                    # 真正带轴的空格字符。
                    is_at_end = all(
                        chars[ci].check_count == 0
                        and get_char_type(chars[ci].char) == CharType.SPACE
                        for ci in range(segment_end, total_chars)
                    )

                    # 查找前后时间戳
                    prev_ts = _find_prev_timestamp(line_idx, segment_start)
                    next_ts = _find_next_timestamp(line_idx, segment_end - 1)

                    # 根据位置和时间戳决定处理方式
                    if is_at_start and is_at_end:
                        # 整行都是待补全字符，前后都没有时间戳，跳过
                        continue
                    elif is_at_start:
                        # 行首：只有后方时间戳，逐个递减
                        if next_ts is None:
                            continue
                        # 从后往前逐个递减
                        for ci in range(segment_end - 1, segment_start - 1, -1):
                            if ci == segment_end - 1:
                                ts = max(0, next_ts - head_offset_ms)
                            else:
                                ts = max(0, chars[ci + 1].timestamps[0] - head_offset_ms)
                            chars[ci].timestamps = [ts]
                            chars[ci].check_count = 1
                            chars[ci]._update_offset_timestamps()
                            chars[ci].push_to_ruby()
                            total_count += 1
                    elif is_at_end:
                        # 行尾：只有前方时间戳
                        if prev_ts is None:
                            continue
                        last_ci = segment_end - 1
                        last_char = chars[last_ci]
                        # 判断最后一个字符是否为符号且有句尾时间戳
                        if (last_char.is_punctuation
                                and last_char.is_sentence_end
                                and last_char.sentence_end_ts is not None):
                            # 符号特殊处理：原句尾转普通，新句尾 = 原句尾 + tail_offset
                            original_end_ts = last_char.sentence_end_ts
                            last_char.timestamps = [original_end_ts]
                            last_char.check_count = 1
                            last_char.sentence_end_ts = original_end_ts + tail_offset_ms
                            last_char._update_offset_timestamps()
                            last_char.push_to_ruby()
                            total_count += 1
                            # 前面的字符均分(prev_ts, original_end_ts)
                            if segment_len > 1:
                                time_diff = original_end_ts - prev_ts
                                for idx, ci in enumerate(range(segment_start, last_ci)):
                                    ts = prev_ts + time_diff * (idx + 1) // segment_len
                                    chars[ci].timestamps = [ts]
                                    chars[ci].check_count = 1
                                    chars[ci]._update_offset_timestamps()
                                    chars[ci].push_to_ruby()
                                    total_count += 1
                        else:
                            # 非符号：均分(prev_ts, 句尾时间戳)
                            end_ts = (last_char.sentence_end_ts
                                      if last_char.is_sentence_end
                                         and last_char.sentence_end_ts is not None
                                      else prev_ts + tail_offset_ms)
                            time_diff = end_ts - prev_ts
                            for idx, ci in enumerate(range(segment_start, segment_end)):
                                ts = prev_ts + time_diff * (idx + 1) // (segment_len + 1)
                                chars[ci].timestamps = [ts]
                                chars[ci].check_count = 1
                                chars[ci]._update_offset_timestamps()
                                chars[ci].push_to_ruby()
                                total_count += 1
                    else:
                        # 行中：前后都应该有时间戳
                        if prev_ts is None or next_ts is None:
                            continue
                        if segment_len == 1:
                            avg_ts = (prev_ts + next_ts) // 2
                            chars[segment_start].timestamps = [avg_ts]
                            chars[segment_start].check_count = 1
                            chars[segment_start]._update_offset_timestamps()
                            chars[segment_start].push_to_ruby()
                            total_count += 1
                        else:
                            time_diff = next_ts - prev_ts
                            for idx, ci in enumerate(range(segment_start, segment_end)):
                                ts = prev_ts + time_diff * (idx + 1) // (segment_len + 1)
                                chars[ci].timestamps = [ts]
                                chars[ci].check_count = 1
                                chars[ci]._update_offset_timestamps()
                                chars[ci].push_to_ruby()
                                total_count += 1

            if total_count == 0:
                return None
            return (self._current_line_idx, self.preview._current_char_idx, None, "timetags")

        ok = self._execute_structural_edit("补全时间戳", _mutate)
        if not ok:
            return 0

        return total_count

    def _execute_separate_symbol_timestamp(
        self,
        symbol_chars: frozenset,
        pre_comp_ms: int,
        post_comp_ms: int,
    ) -> tuple:
        """执行分离符号时间戳的核心逻辑。

        两个独立 pass：
        - Pass 1（后补偿）：符号 cc=0 且 is_sentence_end=True 且 sentence_end_ts 不为空
          → cc 改为 1，timestamps = [old_end_ts]，sentence_end_ts 后移 post_comp_ms
        - Pass 2（前补偿）：符号 cc=1 且紧跟的第一个非符号字符 cc=0
          → 非符号字符 cc 改为 1 并获得符号时间戳，符号时间戳前移 pre_comp_ms

        Returns:
            (post_count, pre_count)
        """
        if not self._project:
            return 0, 0

        post_count = 0
        pre_count = 0

        def _mutate():
            nonlocal post_count, pre_count
            assert self._project is not None

            # 构建全局字符列表（跨行，保持顺序）
            all_chars: list = []
            for sentence in self._project.sentences:
                for ch in sentence.characters:
                    all_chars.append(ch)

            # ── Pass 1: 后补偿 ──────────────────────────────
            for ch in all_chars:
                if (
                    ch.char in symbol_chars
                    and ch.check_count == 0
                    and ch.is_sentence_end
                    and ch.sentence_end_ts is not None
                ):
                    old_end_ts = ch.sentence_end_ts
                    ch.check_count = 1
                    ch.timestamps = [old_end_ts]
                    ch.sentence_end_ts = old_end_ts + post_comp_ms
                    ch._update_offset_timestamps()
                    ch.push_to_ruby()
                    post_count += 1

            # ── Pass 2: 前补偿 ──────────────────────────────
            for i, ch in enumerate(all_chars):
                if ch.char not in symbol_chars or ch.check_count != 1 or not ch.timestamps:
                    continue

                # 找紧跟的第一个非符号字符
                next_non_sym = None
                for j in range(i + 1, len(all_chars)):
                    if all_chars[j].char not in symbol_chars:
                        next_non_sym = all_chars[j]
                        break

                if next_non_sym is None or next_non_sym.check_count != 0:
                    continue

                old_sym_ts = ch.timestamps[0]

                # 给紧跟非符号字符赋予符号的原始时间戳
                next_non_sym.check_count = 1
                next_non_sym.timestamps = [old_sym_ts]
                next_non_sym._update_offset_timestamps()
                next_non_sym.push_to_ruby()

                # 符号时间戳前移
                ch.timestamps = [max(0, old_sym_ts - pre_comp_ms)]
                ch._update_offset_timestamps()
                ch.push_to_ruby()

                pre_count += 1

            if post_count == 0 and pre_count == 0:
                return None
            return (self._current_line_idx, self.preview._current_char_idx, None, "timetags")

        ok = self._execute_structural_edit("分离符号时间戳", _mutate)
        if not ok:
            return 0, 0
        return post_count, pre_count

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

        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            changed = False

            for ci in range(start_char, end_char + 1):
                if ci < len(sentence.characters):
                    ch = sentence.characters[ci]
                    if ch.singer_id != singer_id:
                        ch.singer_id = singer_id
                        ch.push_to_ruby()
                        changed = True

            if start_char == 0 and end_char >= len(sentence.chars) - 1:
                if sentence.singer_id != singer_id:
                    sentence.singer_id = singer_id
                    changed = True

            if not changed:
                return None
            return line_idx, start_char, None, "lyrics"

        ok = self._execute_structural_edit("划选设置演唱者", _mutate)

        if ok:
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
        """异步加载音频到引擎。

        引擎 load() 现在包含整轨解码 + TSM 源 MP3 编码 + 预渲染派发等重操作，
        必须放到后台线程，否则会卡死 UI。UI 更新在 finished 回调里完成。
        """
        if not self._timing_service:
            return False

        # 防重入：正在加载时忽略新请求
        if getattr(self, "_audio_loading", False):
            return False
        self._audio_loading = True
        # 提前置位，配合 MainWindow._on_data_changed 的幂等守卫，避免
        # store.set_audio_path → emit("audio") → load_audio 的重入回环。
        self._audio_file_path = file_path

        # 状态提示
        self._audio_state_tooltip = StateToolTip("正在加载音频", "正在读取音频文件...", self)
        green = theme.status_complete.name()
        self._audio_state_tooltip.setStyleSheet(f"""
            StateToolTip {{
                background-color: {green};
                border: 1px solid {green};
                border-radius: 8px;
            }}
            StateToolTip QLabel {{
                color: white;
            }}
        """)
        self._audio_state_tooltip.move(self._audio_state_tooltip.getSuitablePos())
        self._audio_state_tooltip.show()

        # 后台线程加载
        from strange_uta_game.frontend.workers import AudioLoadWorker

        engine = self._timing_service._audio_engine
        self._audio_load_thread = QThread(self)
        self._audio_load_worker = AudioLoadWorker(engine, file_path)
        self._audio_load_worker.moveToThread(self._audio_load_thread)

        self._audio_load_thread.started.connect(self._audio_load_worker.run)
        self._audio_load_worker.progress.connect(self._on_audio_load_progress)
        self._audio_load_worker.finished.connect(lambda: self._on_audio_loaded(file_path))
        self._audio_load_worker.error.connect(self._on_audio_load_error)
        self._audio_load_worker.finished.connect(self._cleanup_audio_load_thread)
        self._audio_load_worker.error.connect(self._cleanup_audio_load_thread)

        self._audio_load_thread.start()
        return True

    def _on_audio_load_progress(self, stage: str, value: float) -> None:
        if getattr(self, "_audio_state_tooltip", None):
            self._audio_state_tooltip.setContent(stage)

    def _on_audio_loaded(self, file_path: str) -> None:
        """音频后台加载完成（UI 线程）：刷新时长/波形/默认音量速度。"""
        if getattr(self, "_audio_state_tooltip", None):
            self._audio_state_tooltip.setState(True)
            self._audio_state_tooltip.setContent("加载完成")
            self._audio_state_tooltip.close()
            self._audio_state_tooltip = None

        info = self._timing_service.get_audio_info() if self._timing_service else None
        if info:
            self.transport.set_duration(info.duration_ms)
            self.timeline.set_duration(info.duration_ms)
            self.preview.set_duration(info.duration_ms)
            self.transport.set_position(0)
            self.timeline.set_position(0)

            samples = self._timing_service.get_original_samples()
            if samples is not None:
                self.timeline.set_audio_data(samples, info.sample_rate, info.channels)

        self._audio_file_path = file_path
        self.timeline.set_audio_name(Path(file_path).name)

        # 应用设置中的默认音量和速度
        if self._timing_service:
            setting_iface = self._get_setting_interface()
            if setting_iface is not None:
                settings = setting_iface.get_settings()
                default_volume = int(settings.get("audio.default_volume", 80))
                self.transport.slider_volume.setValue(default_volume)
                speed_min = settings.get("audio.speed_slider_min", 0.5)
                speed_max = settings.get("audio.speed_slider_max", 1.0)
                self.transport.set_speed_range(
                    speed_min,
                    speed_max,
                    emit_signal=False,
                )
                default_speed = settings.get("audio.default_speed", 1.0)
                speed_pct = self.transport.set_speed_value(
                    int(default_speed * 100), emit_signal=False
                )
                self._timing_service.set_speed(speed_pct / 100.0)
                # 用实际滑块范围重新触发预渲染，过滤掉用户不会用到的速度档。
                # 已渲染/已入队的速度幂等跳过，无重复开销。
                self._timing_service.prewarm_speeds(
                    speed_min=speed_min,
                    speed_max=speed_max,
                )

        # 与 Home 页加载音频的动作对称：广播 audio 变更，使导出页等订阅者同步
        # 先设 original_media_path（可能标 dirty），再 emit "audio"，
        # 确保 _update_title() 读到正确的 dirty 状态。
        if hasattr(self, "_store") and self._store:
            self._store.set_original_media_path(file_path)
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
        self._audio_loading = False

    def _on_audio_load_error(self, error_msg: str) -> None:
        if getattr(self, "_audio_state_tooltip", None):
            self._audio_state_tooltip.close()
            self._audio_state_tooltip = None
        # 加载失败，复位以允许重试
        self._audio_file_path = None
        self._audio_loading = False
        InfoBar.error(
            title="加载失败",
            content=error_msg,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=self,
        )

    def _cleanup_audio_load_thread(self) -> None:
        thread = getattr(self, "_audio_load_thread", None)
        if thread is not None:
            thread.quit()
            thread.wait()
            self._audio_load_thread = None
        worker = getattr(self, "_audio_load_worker", None)
        if worker is not None:
            worker.deleteLater()
            self._audio_load_worker = None

    def _update_mode_indicator(self):
        """#8：根据播放状态更新左下角模式指示器与激活的 key_map。

        - 播放中 → "模式：打轴"，使用 _key_map_timing_short/long
        - 未播放 → "模式：编辑"，使用 _key_map_edit_short/long
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
            if hasattr(self, "_key_map_timing_short"):
                self._key_map_short = self._key_map_timing_short
                self._key_map_long = self._key_map_timing_long
                self._key_map = self._key_map_timing_short
        else:
            self.lbl_mode.setText("模式：编辑")
            self.lbl_mode.setStyleSheet(
                "font-size: 12px; padding: 2px 8px; border-radius: 4px;"
                "background-color: #e0e0e0; color: #444;"
            )
            if hasattr(self, "_key_map_edit_short"):
                self._key_map_short = self._key_map_edit_short
                self._key_map_long = self._key_map_edit_long
                self._key_map = self._key_map_edit_short
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
                # 播放完毕后再次点击播放：检测 EOF（PAUSED 且位置恰好在末尾）。
                # 此时 _last_reported_ms == _duration_ms，get_position_ms 也返回 duration。
                # 需要先 seek(0) 重置 _last_reported_ms 和 BASS 位置，再 play()；
                # 否则 get_display_position_ms 的单调性保护会让位置卡在 duration，
                # 导致所有控件一直显示末尾、不滚动，直到用户手动 seek 才解除。
                if not self._timing_service.is_playing():
                    dur = self._timing_service.get_duration_ms()
                    pos = self._timing_service.get_position_ms()
                    if dur > 0 and pos >= dur:
                        self._timing_service.seek(0)
                        self.transport.set_position(0)
                        self.timeline.set_position(0)
                        self.preview.set_current_time_ms(0)
                self._timing_service.play()
                self.transport.set_playing(True)
                self.preview.set_playing(True)
                self.timeline.set_playing(True)
                self.lbl_status.setText("播放中")
                self._update_mode_indicator()
                self.preview._last_auto_scroll_line_idx = -1
                # 无论鼠标点击还是键盘快捷键触发播放，都无条件恢复自动滚动
                self._auto_scroll_suspended = False
                self._auto_scroll_new_line_reached = False
                self._auto_scroll_cooldown_timer.stop()
                self.preview._auto_scroll_suspended = False
                # 启动位置主动拉取定时器
                self._position_poll_timer.start()
            except Exception as e:
                self._show_runtime_error(str(e))

    def _on_pause(self):
        if self._timing_service:
            self._timing_service.pause()
            self.transport.set_playing(False)
            self.preview.set_playing(False)
            self.timeline.set_playing(False)
            self.lbl_status.setText("已暂停")
            self._update_mode_indicator()
            # 重置自动滚动状态
            self._auto_scroll_suspended = False
            self._auto_scroll_new_line_reached = False
            self._auto_scroll_cooldown_timer.stop()
            # 停止位置拉取定时器
            self._position_poll_timer.stop()
            # 切换到编辑模式时校验所有行时间戳
            self._validate_all_timestamps()

    def _on_stop(self):
        if self._timing_service:
            self._timing_service.stop()
            self.transport.set_playing(False)
            self.preview.set_playing(False)
            self.timeline.set_playing(False)
            self.transport.set_position(0)
            self.timeline.set_position(0)
            self.lbl_status.setText("已停止")
            self._update_mode_indicator()
            # 重置自动滚动状态
            self._auto_scroll_suspended = False
            self._auto_scroll_new_line_reached = False
            self._auto_scroll_cooldown_timer.stop()
            # 停止位置拉取定时器
            self._position_poll_timer.stop()
            # 切换到编辑模式时校验所有行时间戳
            self._validate_all_timestamps()

    def _on_seek(self, ms: int):
        self._suspend_auto_scroll()
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

    def _on_waveform_visibility_changed(self, visible: bool):
        self.timeline.updateGeometry()
        self.preview.updateGeometry()
        self.updateGeometry()

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
        # 切换行前，校验上一行的时间戳
        if self._project and 0 <= self._current_line_idx < len(self._project.sentences):
            self._validate_line_timestamps(self._current_line_idx)
        self._current_line_idx = idx
        self._update_line_info()

    def _validate_line_timestamps(self, line_idx: int) -> None:
        """校验指定行的所有字符时间戳，确保不超过允许的数量。

        规则：
        - 每个字符允许的时间戳数量 = check_count + (1 if is_sentence_end else 0)
        - timestamps 列表长度不允许超过 check_count
        - 如果有冗余时间戳，截断并推送至 ruby
        """
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        for ch in sentence.characters:
            max_timestamps = ch.check_count
            if len(ch.timestamps) > max_timestamps:
                ch.timestamps = ch.timestamps[:max_timestamps]
                ch._update_offset_timestamps()
                ch.push_to_ruby()

    def _validate_all_timestamps(self) -> None:
        """校验项目中所有行的时间戳（切换到编辑模式时调用）"""
        if not self._project:
            return
        for line_idx in range(len(self._project.sentences)):
            self._validate_line_timestamps(line_idx)

    def _resolve_target_char(self) -> tuple[int, int]:
        """解析字符级操作的目标 (line_idx, char_idx)。

        双域设计：
        - focus 域 (`preview._focus_*`)：用户视觉/操作真理，由点击/拖选/纯←→/打轴驱动，
          不被 cp 自动跳跃污染。字符级操作的优先来源。
        - current 域 (`self._current_line_idx` + `preview._current_char_idx`)：
          后台 TimingService 反馈的合法 cp 位置，会被 cp 跳跃污染。打轴模式
          (TimingService.is_playing()) 下字符级操作目标 — 因为打轴时 TimingService
          自动推进，focus 是用户上次点的位置，可能不是当前正在打的字符。

        Returns:
            (line_idx, char_idx)：目标字符。无 focus 时回退 current；
            两域都无效时返回 (-1, -1)。
        """
        # focus 域优先（line + char 一起取，避免 cp 跳跃后
        # _current_line_idx 与 _focus_line_idx 不一致导致目标错位）
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            char_idx = min(
                self.preview._focus_char_idx,
                self.preview._focus_char_range_end,
            )
            return line_idx, char_idx
        # focus 无效：回退 current
        return self._current_line_idx, self.preview._current_char_idx

    def _on_checkpoint_clicked(self, line_idx: int, char_idx: int, cp_idx: int):
        """点击 checkpoint 标记：仅切换 selected_cp 与音频跳转，不移动光标。

        selected_cp（Character.selected_checkpoint_idx + preview._current_checkpoint_idx）
        与 selected_char（preview._current_char_idx + _focus_*）是两个独立状态：
        - 点击 cp 标记 → 仅 selected_cp 改变；selected_char（光标）保持
        - 点击字符文本 / 方向键 → selected_char（光标）改变
        - F4/F5/F6/Alt+←→ 等编辑/循环操作 → 作用于 selected_char

        通过临时设置 _suppress_cp_cursor_move 阻止
        _apply_checkpoint_position 调用 set_current_position。
        """
        if not self._timing_service:
            return
        self._suppress_cp_cursor_move = True
        try:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, cp_idx)
        finally:
            self._suppress_cp_cursor_move = False
        # 同步 focus 和 current 字符到 cp 对应的字符
        self.preview.set_current_position(line_idx, char_idx)
        self.preview.set_focus_position(line_idx, char_idx)
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
        # 优先向前查找最近的CP，找不到再向后找。
        # 抑制 _apply_checkpoint_position 的居中滚动：用户操作的是 focus 域，
        # 视口应留在被点击字符所在行，不跳到 cp 所在行。
        if self._timing_service:
            self._suppress_cp_cursor_move = True
            try:
                self._timing_service.move_to_checkpoint(
                    line_idx, char_idx, 0, prefer_backward=True
                )
            finally:
                self._suppress_cp_cursor_move = False
            self._current_line_idx = line_idx
            pos = self._timing_service.get_current_position()
            self.preview._current_char_idx = pos.char_idx

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

        before_sentences = deepcopy(self._project.sentences)

        dialog = CharEditDialog(sentence, char_idx, self)
        dialog.exec()
        if dialog.was_modified():
            command_manager = None
            if self._timing_service:
                command_manager = self._timing_service.command_manager
            if command_manager is not None:
                after_sentences = deepcopy(self._project.sentences)
                # 用连词范围的起始字符描述
                word_start, word_end = sentence.get_word_char_range(char_idx)
                if word_end - word_start > 1:
                    desc = f"编辑连词（第 {line_idx + 1} 句 第 {word_start + 1}-{word_end} 字）"
                else:
                    desc = f"编辑字符（第 {line_idx + 1} 句 第 {char_idx + 1} 字）"
                cmd = SentenceSnapshotCommand(
                    self._project,
                    before_sentences,
                    after_sentences,
                    desc,
                )
                cursor_pos = (self._current_line_idx, self.preview._current_char_idx)
                cmd.undo_position = cursor_pos
                cmd.redo_position = cursor_pos
                command_manager.execute(cmd)

            self._reapply_global_offset()
            if self._timing_service:
                self._timing_service.rebuild_global_checkpoints()
            self.preview._update_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
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

    def _cycle_current_checkpoint(self, direction: int = 1):
        """#2：Alt+→/Alt+← 循环切换"当前选中字符"的 checkpoint 索引。

        目标字符优先级：
        1. 若 KaraokePreview 存在有效选中范围，使用选中字符的起点
           (line = _focus_line_idx, char = min(sel_start, sel_end))。
        2. 否则回退到 TimingService.get_current_position()（播放/打轴上下文）。

        句尾字符若带 is_sentence_end，则句尾 checkpoint 也在循环序列内
        （位置为 check_count）。

        Args:
            direction: +1 表示下一个 checkpoint（Alt+→），-1 表示上一个（Alt+←）。
        """
        if not self._project or not self._timing_service:
            return
        # 优先用选中字符
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            char_idx = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
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
        step = 1 if direction >= 0 else -1
        next_idx = (base_idx + step) % total
        self._timing_service.move_to_checkpoint(line_idx, char_idx, next_idx)
        self._update_line_info()
        self.refresh_lyric_display()

    def _rebuild_checkpoints(self):
        if self._timing_service:
            if hasattr(self._timing_service, "rebuild_global_checkpoints"):
                self._timing_service.rebuild_global_checkpoints()
            else:
                self._timing_service.rebuild_global_checkpoints()

    def _reapply_global_offset(self) -> None:
        """将当前全局偏移重新应用到所有字符。

        结构编辑（修改字符、插入导唱符等）会创建新的 Character 对象，
        其 _global_offset_ms 默认为 0。此方法从 preview 读取当前偏移值
        并写入所有字符，确保 global_timestamps 与渲染/导出一致。
        """
        if not self._project:
            return
        offset = self.preview._global_offset_ms
        self._project.global_offset_ms = offset
        for sentence in self._project.sentences:
            for ch in sentence.characters:
                ch.set_offset(offset)

    def _sync_after_structure_change(
        self,
        change_type: str = "lyrics",
        focus_line_idx: Optional[int] = None,
        focus_char_idx: Optional[int] = None,
        checkpoint_idx: Optional[int] = None,
        move_cp: bool = True,
    ):
        if not self._project:
            return

        self._reapply_global_offset()
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
        self.preview.set_focus_position(line_idx, char_idx)
        self._current_line_idx = line_idx

        if move_cp and self._timing_service and sentence.characters:
            target_cp = checkpoint_idx if checkpoint_idx is not None else 0
            self._timing_service.move_to_checkpoint(line_idx, char_idx, target_cp, prefer_backward=True)

        self.refresh_lyric_display()
        self._update_time_tags_display()
        self._update_status()
        if hasattr(self, "_store") and self._store:
            self._store.notify(change_type)

    def _execute_structural_edit(
        self,
        description: str,
        mutator: Callable[[], Optional[tuple[int, int, Optional[int], str]]],
        move_cp: bool = True,
    ) -> bool:
        if not self._project:
            return False

        undo_pos = (self._current_line_idx, self.preview._current_char_idx)

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
            command.undo_position = undo_pos
            focus_line_idx, focus_char_idx, checkpoint_idx, change_type = result
            command.redo_position = (focus_line_idx, focus_char_idx)
            command.move_cp = move_cp
            command_manager.execute(command)

        focus_line_idx, focus_char_idx, checkpoint_idx, change_type = result
        self._sync_after_structure_change(
            change_type=change_type,
            focus_line_idx=focus_line_idx,
            focus_char_idx=focus_char_idx,
            checkpoint_idx=checkpoint_idx,
            move_cp=move_cp,
        )
        return True

    def _register_timestamp_undo(
        self,
        before_sentences: list,
        focus_line_idx: int,
        focus_char_idx: int,
        description: str,
    ) -> None:
        """手动注册撤销命令（不走 _sync_after_structure_change）。"""
        if not self._project:
            return
        # after_sentences 不深拷贝 —— execute() 内会自行 deepcopy，省去一次全量拷贝
        after_sentences = self._project.sentences
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
            undo_pos = (self._current_line_idx, self.preview._current_char_idx)
            command.undo_position = undo_pos
            command.redo_position = (focus_line_idx, focus_char_idx)
            command_manager.execute(command)

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
    
    def _delete_timestamp(self, line_idx: int, char_idx: int) :
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return None

        sentence = self._project.sentences[line_idx]
        if not sentence.characters:
            return None
        
        sentence.clear_one_timestamps(char_idx)

    def _insert_line_break_at_current(self):
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        project = self._project

        self._execute_structural_edit(
            "插入换行",
            lambda: (
                project.insert_line_break(line_idx, char_idx)
                or (line_idx + 1, 0, 0, "lyrics")
            ),
            move_cp=False,
        )

    def _delete_current_selection_or_char(self):
        if not self._project:
            return

        # Del 仅在编辑模式触发（keyPressEvent 路由）。focus 域为真理：
        # 用户拖选范围 → 删整段；单点 focus → 删该字符；focus 无效 → 删 current。
        if (
            self.preview._focus_line_idx >= 0
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            line_idx = self.preview._focus_line_idx
            start = min(self.preview._focus_char_idx, self.preview._focus_char_range_end)
            end = max(self.preview._focus_char_idx, self.preview._focus_char_range_end) + 1
        else:
            line_idx = self._current_line_idx
            start = self.preview._current_char_idx
            end = start + 1

        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
        )

    def _toggle_sentence_end_at_current(self):
        if not self._project:
            return
        # `.` (编辑模式) / F4 (打轴模式) 共用入口；目标字符由 `_resolve_target_char()`
        # 按模式分流：编辑模式 focus 优先，打轴模式 current。
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _convert_timestamps_to_sentence_end(self):
        """取消当前字符所有节奏点、清除时间戳并标记为句尾。"""
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        def _mutate():
            char = sentence.characters[char_idx]
            char.clear_timestamps()
            char.set_check_count(0, force=True)
            if not char.is_sentence_end:
                char.is_sentence_end = True
            return line_idx, char_idx, 0, "checkpoints"

        self._execute_structural_edit("时间戳转句尾", _mutate)

    def _tag_at_current_position_in_edit_mode(self):
        """编辑模式下打轴：读取当前进度条位置并写入当前节奏点，不启动音频。"""
        if not self._timing_service:
            return
        try:
            self._timing_service.on_edit_mode_tag()
        except Exception as e:
            self._show_runtime_error(str(e))

    def _clear_all_checkpoints(self):
        """清除当前字符的全部节奏点：cc=0，is_sentence_end=False，清空时间戳。"""
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        def _mutate():
            char = sentence.characters[char_idx]
            char.clear_timestamps()
            char.set_check_count(0, force=True)
            char.is_sentence_end = False
            return line_idx, char_idx, 0, "checkpoints"

        self._execute_structural_edit("清除所有节奏点", _mutate)

    def _change_checkpoint(self, delta: int):
        """增加或减少"当前选中字符"的节奏点数量。

        通过 `_resolve_target_char()` 解析目标：编辑/编辑模式下都 focus 域优先
        （用户点击/拖选/纯←→设置的字符，不被 cp 自动跳跃污染）；打轴模式
        """
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        def _mutate():
            if delta > 0:
                from strange_uta_game.frontend.editor.timing.dialogs import (
                    _get_ruby_split_mode,
                )
                mode = _get_ruby_split_mode()
                sentence.add_checkpoint(char_idx, ruby_split_mode=mode)
            else:
                # 减到 0 时自动退化为 Nicokara 无 mora 格式（注音文本保留）
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
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            return

        self._execute_structural_edit(
            "切换句尾",
            lambda: (
                sentence.toggle_sentence_end(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

    def _toggle_word_join(self):
        """F3 连词/取消连词。

        单选：toggle 当前字符的 linked_to_next（保持旧行为）。
        多选（划选多个字符）：沿用"快速连词/取消连词"逻辑 ——
        若选区内所有字符的 linked_to_next 均为 False，则把除最后一个被选中字符外
        的 linked_to_next 都置为 True（将选区连成一个词）；否则把选区内所有字符的
        linked_to_next 都置为 False。
        """
        if not self._project:
            return

        # 解析选择范围（与 _on_modify_char 一致：优先划选区域，回退单字符）
        sel_line = self.preview._focus_line_idx
        sel_start = self.preview._focus_char_idx
        sel_end = self.preview._focus_char_range_end
        if sel_line >= 0 and sel_start >= 0:
            use_line = sel_line
            start_idx = min(sel_start, sel_end)
            end_idx = max(sel_start, sel_end)
        else:
            use_line, char_idx = self._resolve_target_char()
            start_idx = end_idx = char_idx

        if use_line < 0 or use_line >= len(self._project.sentences):
            return
        sentence = self._project.sentences[use_line]
        if start_idx < 0 or end_idx >= len(sentence.characters):
            return

        if start_idx == end_idx:
            self._toggle_word_join_single(use_line, start_idx)
        else:
            self._toggle_word_join_range(use_line, start_idx, end_idx)

    def _toggle_word_join_single(self, line_idx: int, char_idx: int):
        """单字符连词：toggle 该字符的 linked_to_next。"""
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
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

        def _mutate():
            ch.linked_to_next = new_linked
            return (line_idx, char_idx, 0, "checkpoints")

        self._execute_structural_edit(
            "连词" if new_linked else "取消连词",
            _mutate,
            move_cp=False,
        )

        InfoBar.success(
            title="连词" if new_linked else "取消连词",
            content=f"已{'连接' if new_linked else '断开'}「{sentence.chars[char_idx]}」与「{sentence.chars[char_idx + 1]}」",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _toggle_word_join_range(self, line_idx: int, start_idx: int, end_idx: int):
        """多字符连词：选区全未连词 → 除末字外全部连词；否则全部取消连词。"""
        sentence = self._project.sentences[line_idx]
        chars = sentence.characters
        selected = chars[start_idx : end_idx + 1]
        # 选区内全部 linked_to_next 为 False → 连词；否则 → 全部取消
        link = all(not c.linked_to_next for c in selected)

        def _mutate():
            if link:
                # 除最后一个被选中字符外，全部置为连词（末字保持 False，避免连到选区外）
                for i in range(start_idx, end_idx):
                    chars[i].linked_to_next = True
            else:
                for i in range(start_idx, end_idx + 1):
                    chars[i].linked_to_next = False
            return (line_idx, start_idx, 0, "checkpoints")

        self._execute_structural_edit(
            "连词" if link else "取消连词",
            _mutate,
            move_cp=False,
        )

        InfoBar.success(
            title="连词" if link else "取消连词",
            content=(
                f"已将第 {line_idx + 1} 句 第 {start_idx + 1}-{end_idx + 1} 字连为一个词"
                if link
                else f"已断开第 {line_idx + 1} 句 第 {start_idx + 1}-{end_idx + 1} 字的连词"
            ),
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _on_nav_line(self, delta: int):
        """方向键导航：上一行 (delta=-1) 或下一行 (delta=+1)。

        编辑模式：focus 域为真理来源（与 ←→/Space/Backspace/`.` 一致）。
        起点取 focus 行（无效则 current），目标行落在第一个字符 (char_idx=0)。
        使用 :py:meth:`Project.find_prev_line_with_characters` /
        :py:meth:`Project.find_next_line_with_characters` 跳过空行（无字符的行）。
        到达项目首尾时停止。

        打轴模式：保持原 cp 跳跃语义（focus 不跟随，current 由 TimingService 推进）。
        """
        if not self._project or not self._timing_service:
            return
        sentences = self._project.sentences

        # playing = bool(self._timing_service.is_playing())
        # if playing:
        #     # 打轴模式：原行为不变（基于 current 行 + cp 跳跃）
        #     if delta < 0:
        #         cand = self._project.find_prev_line_with_checkpoints(self._current_line_idx)
        #         if cand < 0:
        #             return
        #         new_idx = cand
        #     else:
        #         new_idx = self._current_line_idx + delta
        #         if new_idx < 0 or new_idx >= len(sentences):
        #             return
        #     self._timing_service.move_to_checkpoint(new_idx, 0, 0)
        #     self._update_time_tags_display()
        #     self._update_status()
        #     return

        # 编辑模式：focus 起点 + 跳空行 + 写 focus + 驱动 current
        if self.preview._focus_line_idx >= 0:
            line_idx = self.preview._focus_line_idx
        else:
            line_idx = self._current_line_idx
        if delta < 0:
            cand = self._project.find_prev_line_with_characters(line_idx)
        else:
            cand = self._project.find_next_line_with_characters(line_idx)
        if cand < 0:
            return
        # 继承当前 char_idx，越界则 clamp 到目标行行尾
        cur_char = self.preview._focus_char_idx if self.preview._focus_char_idx >= 0 else self.preview._current_char_idx
        target_chars = sentences[cand].characters
        if target_chars:
            new_char = min(cur_char, len(target_chars) - 1)
        else:
            new_char = 0
        new_line = cand
        # 行切换前校验当前行的时间戳
        if new_line != line_idx:
            self._validate_line_timestamps(line_idx)
        # 直接写 focus 域（与 _on_nav_char 同款，不依赖 cp 回调链污染）
        self.preview._focus_line_idx = new_line
        self.preview._focus_char_idx = new_char
        self.preview._focus_char_range_end = new_char
        # 驱动 current 跟随：找最近 cp 反馈到 current。
        # 抑制 _apply_checkpoint_position 的居中滚动，以 focus 域为基准。
        self._suppress_cp_cursor_move = True
        try:
            self._timing_service.move_to_checkpoint(
                new_line, new_char, 0, prefer_backward=True
            )
        finally:
            self._suppress_cp_cursor_move = False
        self._current_line_idx = new_line
        pos = self._timing_service.get_current_position()
        self.preview._current_char_idx = pos.char_idx
        self.preview._current_line_idx = new_line
        self.preview.scroll_current_line_to_center()
        self._update_line_info()
        self._update_time_tags_display()
        self._update_status()
        self.preview.update()

    def _on_nav_char(self, delta: int):
        """方向键左右导航：上一字符 (delta=-1) 或下一字符 (delta=+1)。

        字符级操作 → 读 focus 域（用户视觉真理），不读被 cp 跳跃污染的
        current 域。同时直接更新 focus 域字段，并驱动 current 跟随
        (move_to_checkpoint 让 TimingService 找最近 cp 反馈到 current)。

        行内移动：在当前 focus 行的字符序列内 ±1。
        跨行边界：
        - delta=-1 且 focus 已在首字符 (char_idx == 0)：跳到上一行的末字符。
        - delta=+1 且 focus 已在末字符：跳到下一行的首字符 (char_idx = 0)。
        跨行使用 :py:meth:`Project.find_prev_line_with_characters` /
        :py:meth:`Project.find_next_line_with_characters` 跳过空行。
        到达项目首尾时停止（不循环）。

        Args:
            delta: -1 表示左移 (LEFT)，+1 表示右移 (RIGHT)。
        """
        if not self._project or not self._timing_service:
            return
        sentences = self._project.sentences
        # focus 域作为真理来源；focus 无效则回退 current 一次
        if self.preview._focus_line_idx >= 0 and self.preview._focus_char_idx >= 0:
            line_idx = self.preview._focus_line_idx
            char_idx = min(
                self.preview._focus_char_idx,
                self.preview._focus_char_range_end
                if self.preview._focus_char_range_end >= 0
                else self.preview._focus_char_idx,
            )
        else:
            line_idx = self._current_line_idx
            char_idx = self.preview._current_char_idx
        if line_idx < 0 or line_idx >= len(sentences):
            return
        chars = sentences[line_idx].characters
        if delta < 0:
            if char_idx > 0:
                new_line, new_char = line_idx, char_idx - 1
            else:
                cand = self._project.find_prev_line_with_characters(line_idx)
                if cand < 0:
                    return
                prev_chars = sentences[cand].characters
                new_line, new_char = cand, max(0, len(prev_chars) - 1)
        else:
            if char_idx < len(chars) - 1:
                new_line, new_char = line_idx, char_idx + 1
            else:
                cand = self._project.find_next_line_with_characters(line_idx)
                if cand < 0:
                    return
                new_line, new_char = cand, 0
        # 直接更新 focus 域（不依赖 cp 回调链）
        self.preview._focus_line_idx = new_line
        self.preview._focus_char_idx = new_char
        self.preview._focus_char_range_end = new_char
        # 驱动 current 跟随：让 TimingService 找最近 cp，
        # 反馈经 _apply_checkpoint_position 更新 current 域。
        # 抑制居中滚动，以 focus 域为基准。
        self._suppress_cp_cursor_move = True
        try:
            self._timing_service.move_to_checkpoint(
                new_line, new_char, 0, prefer_backward=True
            )
        finally:
            self._suppress_cp_cursor_move = False
        self._current_line_idx = new_line
        pos = self._timing_service.get_current_position()
        self.preview._current_char_idx = pos.char_idx
        self.preview._current_line_idx = new_line
        self.preview.scroll_current_line_to_center()
        self._update_line_info()
        self._update_time_tags_display()
        self._update_status()

    def _find_previous_timestamp(self, line_idx: int, char_idx: int) -> Optional[int]:
        """向前查找最近的时间戳（可能在上一行）

        从指定位置向前搜索，返回找到的第一个时间戳。
        """
        if not self._project:
            return None

        # 从当前行往前找
        for li in range(line_idx, -1, -1):
            sentence = self._project.sentences[li]
            # 确定本行搜索的字符范围
            end_char = char_idx if li == line_idx else len(sentence.characters) - 1

            for ci in range(end_char, -1, -1):
                char = sentence.get_character(ci)
                if not char:
                    continue
                tags = char.all_global_timestamps
                if tags:
                    return tags[-1]  # 返回该字符最后一个时间戳（最近的）
        return None

    def _find_previous_timestamp_with_position(
        self, line_idx: int, char_idx: int
    ) -> Optional[tuple[int, int, int]]:
        """向前查找最近的时间戳，同时返回该时间戳所在的字符位置

        Args:
            line_idx: 当前行索引
            char_idx: 当前字符索引

        Returns:
            找到的 (timestamp, line_idx, char_idx) 或 None
        """
        if not self._project:
            return None

        # 从当前行往前找
        for li in range(line_idx, -1, -1):
            sentence = self._project.sentences[li]
            # 确定本行搜索的字符范围
            end_char = char_idx if li == line_idx else len(sentence.characters) - 1

            for ci in range(end_char, -1, -1):
                char = sentence.get_character(ci)
                if not char:
                    continue
                tags = char.all_global_timestamps
                if tags:
                    return (tags[-1], li, ci)
        return None

    def _resolve_cp_idx_for_timestamp(
        self, line_idx: int, char_idx: int, timestamp: int
    ) -> int:
        """根据时间戳值反查所属的 checkpoint 索引。

        在字符的 all_global_timestamps 中找到与 timestamp 匹配的索引；
        找不到时回退到最后一个 cp。
        """
        if not self._project or line_idx >= len(self._project.sentences):
            return 0
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.characters):
            return 0
        char = sentence.get_character(char_idx)
        if not char:
            return 0
        tags = char.all_global_timestamps
        if not tags:
            return 0
        # 精确匹配
        for i, t in enumerate(tags):
            if t == timestamp:
                return i
        # 找不到精确匹配，回退到最后一个 cp
        return len(tags) - 1

    def _find_prev_char_with_cp(
        self, line_idx: int, char_idx: int
    ) -> Optional[Tuple[int, int, int]]:
        """向前查找最近一个有CP的字符（check_count > 0）

        Args:
            line_idx: 当前行索引
            char_idx: 当前字符索引

        Returns:
            找到的 (line_idx, char_idx, cp_idx) 或 None
        """
        if not self._project:
            return None

        # 从当前行往前找
        for li in range(line_idx, -1, -1):
            sentence = self._project.sentences[li]
            # 当前行从 char_idx - 1 开始（跳过当前字符），其他行从末尾开始
            end_char = char_idx - 1 if li == line_idx else len(sentence.characters) - 1

            for ci in range(end_char, -1, -1):
                char = sentence.get_character(ci)
                if not char:
                    continue
                if char.check_count > 0:
                    return (li, ci, 0)

        return None

    def _on_seek_to_char(self, line_idx: int, char_idx: int):
        """双击字符 → 跳转到该字符的时间戳（无时间戳则向前查找）

        对于无CP字符：
        - 有时间戳：跳转到该时间戳，CP挪到该字符
        - 无时间戳但找到前一个时间戳：跳转到前一个时间戳，CP挪到时间戳所在的字符
        - 完全没有时间戳：跳转到歌曲开头(0)，CP挪到全文键第一个CP
        不动focus域的字符选中。
        """
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        char = sentence.get_character(char_idx)
        if not char:
            return

        # 判断当前字符是否有 checkpoint
        no_checkpoint = char.check_count == 0 and not char.is_sentence_end

        tags = char.all_global_timestamps
        if tags:
            # 有时间戳：跳转到该时间戳
            self._on_seek(tags[0])
            # CP挪到当前字符
            if self._timing_service:
                self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)
        elif no_checkpoint:
            # 无CP字符且无时间戳：向前查找最近的时间戳
            result = self._find_previous_timestamp_with_position(line_idx, char_idx)
            if result is not None:
                prev_ts, ts_line_idx, ts_char_idx = result
                self._on_seek(prev_ts)
                # CP挪到时间戳所在的字符的对应 cp_idx（而非固定 0）
                if self._timing_service:
                    cp_idx = self._resolve_cp_idx_for_timestamp(
                        ts_line_idx, ts_char_idx, prev_ts
                    )
                    self._timing_service.move_to_checkpoint(
                        ts_line_idx, ts_char_idx, cp_idx
                    )
            else:
                # 完全没有时间戳：跳转到歌曲开头
                self._on_seek(0)
                # CP挪到全文键第一个CP
                if self._timing_service:
                    self._timing_service.move_to_checkpoint(0, 0, 0)
        else:
            # 有CP但无时间戳：向前查找最近的时间戳
            result = self._find_previous_timestamp_with_position(line_idx, char_idx)
            if result is not None:
                prev_ts, ts_line_idx, ts_char_idx = result
                self._on_seek(prev_ts)
                # CP挪到时间戳所在的字符的对应 cp_idx（而非固定 0）
                if self._timing_service:
                    cp_idx = self._resolve_cp_idx_for_timestamp(
                        ts_line_idx, ts_char_idx, prev_ts
                    )
                    self._timing_service.move_to_checkpoint(
                        ts_line_idx, ts_char_idx, cp_idx
                    )
            else:
                # 完全没有时间戳：跳转到歌曲开头
                self._on_seek(0)
                # CP挪到全文键第一个CP
                if self._timing_service:
                    self._timing_service.move_to_checkpoint(0, 0, 0)

        self._update_time_tags_display()
        self._update_status()

    def _on_seek_to_checkpoint(self, line_idx: int, char_idx: int, cp_idx: int):
        """双击 checkpoint → 跳转到该 checkpoint 的时间戳（无时间戳则向前查找）"""
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        char = sentence.get_character(char_idx)
        if not char:
            return

        tags = char.all_global_timestamps
        if tags:
            target_idx = min(cp_idx, len(tags) - 1)
            self._on_seek(tags[target_idx])
        else:
            # 向前查找最近的时间戳，仅跳转音频
            prev_ts = self._find_previous_timestamp(line_idx, char_idx)
            if prev_ts is not None:
                self._on_seek(prev_ts)

        # 移动打轴位置到当前双击的 checkpoint
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, cp_idx)
            self._update_time_tags_display()
            self._update_status()
        # 同步 focus 字符到 cp 对应的字符
        self.preview.set_focus_position(line_idx, char_idx)

    def _on_delete_chars_requested(self, line_idx: int, start: int, end: int):
        self._execute_structural_edit(
            "删除字符",
            lambda: self._delete_char_range(line_idx, start, end),
            move_cp=False,
        )
    
    def _on_delete_timestamp_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.chars):
            return

        jump_before_raw = getattr(self, "_jump_before_ms", 3000)
        speed = self._timing_service.get_speed() if self._timing_service else 1.0
        jump_before = int(jump_before_raw * speed)
        char = sentence.get_character(char_idx)

        before_sentences = deepcopy(self._project.sentences)

        if char and char.all_global_timestamps:
            # 当前字符有时间戳：删除当前字符时间戳，音频回退3秒，结束
            seek_ms = max(0, char.all_global_timestamps[0] - jump_before)
            self._delete_timestamp(line_idx, char_idx)
            self._register_timestamp_undo(before_sentences, line_idx, char_idx, "删除时间戳")
            if self._timing_service:
                self._timing_service.move_to_checkpoint(line_idx, char_idx, 0, prefer_backward=True)
                self._update_time_tags_display()
                self._update_status()
            self._on_seek(seek_ms)
        else:
            # 当前字符没有时间戳：找前一个有节奏点的字符
            prev_char = self._find_prev_char_with_cp(line_idx, char_idx)
            if not prev_char:
                return
            prev_line, prev_char_idx, prev_cp_idx = prev_char
            prev = self._project.sentences[prev_line].get_character(prev_char_idx)
            seek_ms = max(0, prev.all_global_timestamps[0] - jump_before) if prev and prev.all_global_timestamps else None
            self._delete_timestamp(prev_line, prev_char_idx)
            self._register_timestamp_undo(before_sentences, prev_line, prev_char_idx, "删除时间戳")
            if self._timing_service:
                self._timing_service.move_to_checkpoint(prev_line, prev_char_idx, prev_cp_idx)
                self._update_time_tags_display()
                self._update_status()
            self.preview.set_focus_position(prev_line, prev_char_idx)
            if seek_ms is not None:
                self._on_seek(seek_ms)

    def _on_insert_space_before_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            if not sentence.characters:
                # 空行：直接在行首插入
                new_char = Character(char=" ", check_count=0, singer_id=sentence.singer_id)
                sentence.insert_character(0, new_char)
                return line_idx, 0, 0, "lyrics"
            if char_idx < 0 or char_idx >= len(sentence.characters):
                return None
            ref_char = sentence.characters[char_idx]
            new_char = Character(
                char=" ",
                check_count=0,
                singer_id=ref_char.singer_id or sentence.singer_id,
            )
            sentence.insert_character(char_idx, new_char)
            return line_idx, char_idx, 0, "lyrics"

        self._execute_structural_edit("在前插入空格", _mutate, move_cp=False)

    def _on_insert_space_after_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            if not sentence.characters:
                # 空行：直接在行首插入
                new_char = Character(char=" ", check_count=0, singer_id=sentence.singer_id)
                sentence.insert_character(0, new_char)
                return line_idx, 0, 0, "lyrics"
            if char_idx < 0 or char_idx >= len(sentence.characters):
                return None
            ref_char = sentence.characters[char_idx]
            new_char = Character(
                char=" ",
                check_count=0,
                singer_id=ref_char.singer_id or sentence.singer_id,
            )
            sentence.insert_character(char_idx + 1, new_char)
            return line_idx, char_idx + 1, 0, "lyrics"

        self._execute_structural_edit("插入空格", _mutate, move_cp=False)

    def _insert_space_at_current(self):
        """在当前字符后插入空格（快捷键入口）。"""
        if not self._project:
            return
        line_idx, char_idx = self._resolve_target_char()
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            sentence = project.sentences[line_idx]
            if not sentence.characters:
                new_char = Character(char=" ", check_count=0, singer_id=sentence.singer_id)
                sentence.insert_character(0, new_char)
                return line_idx, 0, 0, "lyrics"
            if char_idx < 0 or char_idx >= len(sentence.characters):
                return None
            ref_char = sentence.characters[char_idx]
            new_char = Character(
                char=" ",
                check_count=0,
                singer_id=ref_char.singer_id or sentence.singer_id,
            )
            sentence.insert_character(char_idx + 1, new_char)
            return line_idx, char_idx + 1, 0, "lyrics"

        self._execute_structural_edit("插入空格", _mutate, move_cp=False)

    def _merge_line_up_at_current(self):
        """将当前行合并到上一行（快捷键触发）。"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx <= 0 or line_idx >= len(self._project.sentences):
            return
        self._on_merge_line_up_requested(line_idx)

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
            move_cp=False,
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

        self._execute_structural_edit("删除本行", _mutate, move_cp=False)

    def _on_insert_blank_line_before_requested(self, line_idx: int):
        if not self._project:
            return
        project = self._project

        singer_id = ""
        if 0 <= line_idx < len(project.sentences):
            sentence = project.sentences[line_idx]
            if sentence.characters:
                singer_id = sentence.characters[-1].singer_id

        self._execute_structural_edit(
            "在前插入空行",
            lambda: ((project.insert_blank_line(line_idx - 1, singer_id=singer_id), 0, None, "lyrics")),
            move_cp=False,
        )

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
            move_cp=False,
        )

    def _on_add_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project

        def _mutate():
            from strange_uta_game.frontend.editor.timing.dialogs import (
                _get_ruby_split_mode,
            )
            mode = _get_ruby_split_mode()
            project.sentences[line_idx].add_checkpoint(
                char_idx, ruby_split_mode=mode
            )
            return line_idx, char_idx, 0, "checkpoints"

        self._execute_structural_edit("增加节奏点", _mutate)

    def _on_remove_checkpoint_requested(self, line_idx: int, char_idx: int):
        if not self._project or line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        project = self._project
        sentence = project.sentences[line_idx]

        def _mutate():
            # 减到 0 时自动退化为 Nicokara 无 mora 格式（注音文本保留）
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

    def _execute_action(self, action: str, key: int):
        """执行指定的快捷键动作。"""
        if action == "play_pause":
            if self._timing_service and self._timing_service.is_playing():
                self._on_pause()
            else:
                self._on_play()
        elif action == "stop":
            self._on_stop()
        elif action == "seek_back":
            if self._timing_service and self._timing_service.is_playing():
                cur = self._timing_service.get_position_ms()
                speed = self._timing_service.get_speed()
                self._on_seek(max(0, cur - int(self._rewind_ms * speed)))
        elif action == "seek_forward":
            if self._timing_service and self._timing_service.is_playing():
                cur = self._timing_service.get_position_ms()
                dur = self._timing_service.get_duration_ms()
                speed = self._timing_service.get_speed()
                self._on_seek(min(dur, cur + int(self._fast_forward_ms * speed)))
        elif action == "speed_down":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(v - 5)
        elif action == "speed_up":
            v = self.transport.get_speed_value()
            self.transport.set_speed_value(v + 5)
        elif action == "volume_up":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(min(100, v + 5))
        elif action == "volume_down":
            v = self.transport.slider_volume.value()
            self.transport.slider_volume.setValue(max(0, v - 5))
        elif action == "nav_prev_line":
            self._on_nav_line(-1)
        elif action == "nav_next_line":
            self._on_nav_line(1)
        elif action == "nav_prev_char":
            self._on_nav_char(-1)
        elif action == "nav_next_char":
            self._on_nav_char(1)
        elif action == "timestamp_up":
            self._adjust_current_timestamp(self._timing_adjust_step_ms)
        elif action == "timestamp_down":
            self._adjust_current_timestamp(-self._timing_adjust_step_ms)
        elif action == "cycle_checkpoint":
            self._cycle_current_checkpoint(1)
        elif action == "cycle_checkpoint_prev":
            self._cycle_current_checkpoint(-1)
        elif action == "edit_ruby":
            if self._project:
                # 与「修改所选字符」等窗口统一：优先使用 focus 域（拖选/聚焦），
                # 无 focus 选择时回退到 current 域。
                sel_line = self.preview._focus_line_idx
                sel_start = self.preview._focus_char_idx
                if sel_line >= 0 and sel_start >= 0:
                    line_idx = sel_line
                    char_idx = sel_start
                else:
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
                line_idx, char_idx = self._resolve_target_char()
                if line_idx >= 0 and char_idx >= 0:
                    self.preview.toggle_sentence_end_requested.emit(line_idx, char_idx)
                else:
                    self._toggle_sentence_end_at_current()
        elif action == "delete_timestamp":
            if self._project:
                line_idx = self._current_line_idx
                char_idx = self.preview._current_char_idx
                self._on_delete_timestamp_requested(line_idx, char_idx)
        elif action == "bulk_change":
            self._on_bulk_change()
        elif action == "modify_char":
            self._on_modify_char()
        elif action == "insert_guide":
            self._on_insert_guide()
        elif action == "modify_line":
            self._on_modify_line()
        elif action == "analyze_rubies":
            self._on_analyze_rubies()
        elif action == "analyze_rubies_by_line":
            self._on_analyze_rubies_by_line()
        elif action == "analyze_rubies_selected":
            self._on_analyze_rubies_selected()
        elif action == "open_fulltext":
            self._on_open_fulltext()
        elif action == "delete_rubies_by_type":
            self._on_delete_rubies_by_type()
        elif action == "set_singer_by_line":
            self._on_set_singer_by_line()
        elif action == "apply_singer":
            self._on_apply_singer()
        elif action == "timestamps_to_sentence_end":
            self._convert_timestamps_to_sentence_end()
        elif action in ("tag_now_editor", "tag_now_extra_editor"):
            self._tag_at_current_position_in_edit_mode()
        elif action == "clear_all_checkpoints":
            self._clear_all_checkpoints()
        elif action == "quick_export":
            self._on_quick_export()
        elif action == "insert_space":
            self._insert_space_at_current()
        elif action == "merge_line_up":
            self._merge_line_up_at_current()

    def _on_quick_export(self):
        """快捷导出：使用默认导出格式弹出保存对话框并导出。"""
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

        from strange_uta_game.frontend.settings.app_settings import AppSettings

        settings = AppSettings()
        format_name = settings.get("export.default_format", "Nicokara (带注音)")

        try:
            exporter = get_exporter_by_name(format_name)
        except ValueError:
            InfoBar.error(
                title="导出失败",
                content=f"未知的导出格式: {format_name}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        ext = exporter.file_extension
        file_filter = exporter.file_filter

        store = getattr(self, "_store", None)
        audio_path = getattr(store, "audio_path", None) if store else None
        if audio_path:
            base_name = Path(audio_path).stem
        elif self._project.metadata.title:
            base_name = self._project.metadata.title
        else:
            base_name = "untitled"
        suggested_dir = ""
        if store:
            suggested_dir = store.working_dir
        if not suggested_dir:
            suggested_dir = settings.get("export.last_export_dir", "")
        suggested_path = str(Path(suggested_dir) / (base_name + ext)) if suggested_dir else base_name + ext

        file_path, _ = QFileDialog.getSaveFileName(
            self, "快捷导出", suggested_path, file_filter
        )
        if not file_path:
            return

        if not Path(file_path).suffix:
            file_path += ext

        export_service = ExportService()
        result = export_service.export(
            self._project,
            format_name,
            file_path,
            offset_ms=settings.get("export.offset_ms", 0),
            software_compensation_ms=settings.get("export.software_compensation_ms", 0),
        )
        if result.success:
            settings.set("export.last_export_dir", str(Path(file_path).parent))
            settings.save()
            InfoBar.success(
                title="导出成功",
                content=result.file_path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            InfoBar.error(
                title="导出失败",
                content=result.error_message or "未知错误",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

    def _on_long_press_timeout(self):
        """长按定时器超时，执行 long 动作。"""
        action = self._pending_press_action_long
        key_name = self._pending_press_key
        # 清除 pending 状态（标记为已处理长按）
        self._pending_press_key = None
        self._pending_press_action_short = None
        self._pending_press_action_long = None
        if action:
            self._execute_action(action, 0)

    def eventFilter(self, obj, event):
        """捕获 preview 子控件的键盘和鼠标交互，触发自动滚动挂起。"""
        if obj is self.preview:
            etype = event.type()
            if etype == QEvent.Type.KeyPress:
                self._suspend_auto_scroll()
            elif etype == QEvent.Type.MouseButtonPress:
                if event.button() == Qt.MouseButton.LeftButton:
                    self._auto_scroll_mouse_press_pos = (
                        int(event.position().x()),
                        int(event.position().y()),
                    )
                self._suspend_auto_scroll()
            elif etype == QEvent.Type.MouseMove:
                if self._auto_scroll_mouse_press_pos is not None:
                    dx = int(event.position().x()) - self._auto_scroll_mouse_press_pos[0]
                    dy = int(event.position().y()) - self._auto_scroll_mouse_press_pos[1]
                    if dx * dx + dy * dy > 100:  # 10px threshold
                        self._suspend_auto_scroll()
            elif etype == QEvent.Type.MouseButtonRelease:
                self._auto_scroll_mouse_press_pos = None
                self._suspend_auto_scroll()
        return False

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is None:
            return
        # 所有键盘操作挂起自动滚动（Play 按钮走 _on_play，不经过这里）
        self._suspend_auto_scroll()
        self._action_from_keyboard = True
        try:
            self._keyPressEvent_impl(a0)
        finally:
            self._action_from_keyboard = False

    def _keyPressEvent_impl(self, a0: QKeyEvent):
        # 记录 handler 进入时刻（time.monotonic 同一时钟源）。
        # 注意：这里测的是“handler 入口 → 读取音频位置”之间的同步处理耗时，
        # 不是事件在 Qt 队列里排队等待的时间（旧版 a0.timestamp() 那种语义已废弃，
        # 因其与 QPC 跨时钟会引入稳定的固定偏移）。UI 卡顿导致的排队等待不在此补偿范围内。
        handler_entry_s = time.monotonic()
        key = a0.key()
        modifiers = a0.modifiers()
        playing = bool(self._timing_service and self._timing_service.is_playing())

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
            elif key == Qt.Key.Key_V:
                self._on_paste_lyrics()
                a0.accept()
                return
            elif key == Qt.Key.Key_C:
                self._on_copy_chars()
                a0.accept()
                return
            # 其他 Ctrl 组合键：不直接 return，继续走 key_map 查找

        # Convert Qt key to string name for mapping lookup
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            super().keyPressEvent(a0)
            return

        key_upper = key_name.upper()
        action_short = self._key_map_short.get(key_upper)
        action_long = self._key_map_long.get(key_upper)
        # Fallback to default key map only if settings not loaded yet
        if not self._settings_loaded and action_short is None and action_long is None:
            action_short = self._default_key_action(key, modifiers)

        # tag_now / tag_now_extra 使用 press/release 语义，立即执行，不走长按检测
        if action_short in ("tag_now", "tag_now_extra") or action_long in ("tag_now", "tag_now_extra"):
            if not playing:
                self._add_checkpoint()
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name not in self._pressed_keys:
                # 按键音：普通 cp → 按下时播放 press；句尾 cp → 忽略（等 release）
                if self._keysound_player is not None:
                    if not self._timing_service.is_current_cp_sentence_end_tail():
                        self._keysound_player.play_press()
                try:
                    self._pressed_keys.add(key_name)
                    # handler 入口到此刻的同步处理耗时（非 Qt 队列等待时间）
                    queue_delay_ms = max(0, int((time.monotonic() - handler_entry_s) * 1000))
                    if queue_delay_ms > 500:
                        queue_delay_ms = 0
                    self._timing_service.on_timing_key_pressed(key_name, queue_delay_ms)
                except Exception as e:
                    self._pressed_keys.discard(key_name)
                    self._show_runtime_error(str(e))
            a0.accept()
            return

        # tag_and_delete_next：同样使用 press/release 语义，不走长按检测
        if action_short == "tag_and_delete_next" or action_long == "tag_and_delete_next":
            if not playing:
                self._add_checkpoint()
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name not in self._pressed_keys:
                if self._keysound_player is not None:
                    if not self._timing_service.is_current_cp_sentence_end_tail():
                        self._keysound_player.play_press()
                try:
                    self._pressed_keys.add(key_name)
                    queue_delay_ms = max(0, int((time.monotonic() - handler_entry_s) * 1000))
                    if queue_delay_ms > 500:
                        queue_delay_ms = 0
                    self._timing_service.on_tag_and_delete_next_pressed(key_name, queue_delay_ms)
                except Exception as e:
                    self._pressed_keys.discard(key_name)
                    self._show_runtime_error(str(e))
            a0.accept()
            return

        # 只有 short 绑定：立即执行，保留 isAutoRepeat 行为
        if action_short is not None and action_long is None:
            self._execute_action(action_short, key)
            a0.accept()
            return

        # 有 long 绑定（可能同时有 short）：启动定时器等待区分
        if action_long is not None:
            self._pending_press_key = key_upper
            self._pending_press_action_short = action_short
            self._pending_press_action_long = action_long
            self._long_press_timer.start()
            a0.accept()
            return

        # 无绑定的按键
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # 如果焦点在 QLineEdit 上（如偏移输入框），不拦截回车
            focused = QApplication.focusWidget()
            if isinstance(focused, QLineEdit):
                return
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
        # handler 进入时刻；queue_delay_ms 测的是入口→读位置的同步处理耗时，
        # 非 Qt 队列等待（详见 _keyPressEvent_impl 处说明）。
        handler_entry_s = time.monotonic()
        key = a0.key()
        modifiers = a0.modifiers()
        key_name = self._qt_key_to_name(key, modifiers)
        if not key_name:
            super().keyReleaseEvent(a0)
            return

        key_upper = key_name.upper()

        # tag_now / tag_now_extra 释放处理
        action_short = self._key_map_short.get(key_upper)
        action_long = self._key_map_long.get(key_upper)
        if action_short in ("tag_now", "tag_now_extra") or action_long in ("tag_now", "tag_now_extra"):
            if not (self._timing_service and self._timing_service.is_playing()):
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name in self._pressed_keys:
                # 按键音：句尾 cp → 抬起时播放 release；普通 cp → 忽略
                if self._keysound_player is not None:
                    if self._timing_service.is_current_cp_sentence_end_tail():
                        self._keysound_player.play_release()
                try:
                    # handler 入口到此刻的同步处理耗时（非 Qt 队列等待时间）
                    queue_delay_ms = max(0, int((time.monotonic() - handler_entry_s) * 1000))
                    if queue_delay_ms > 500:
                        queue_delay_ms = 0
                    self._timing_service.on_timing_key_released(key_name, queue_delay_ms)
                except Exception as e:
                    self._show_runtime_error(str(e))
                finally:
                    self._pressed_keys.discard(key_name)
            a0.accept()
            return

        # tag_and_delete_next 释放处理
        if action_short == "tag_and_delete_next" or action_long == "tag_and_delete_next":
            if not (self._timing_service and self._timing_service.is_playing()):
                a0.accept()
                return
            if a0.isAutoRepeat():
                a0.ignore()
                return
            if self._timing_service and key_name in self._pressed_keys:
                if self._keysound_player is not None:
                    if self._timing_service.is_current_cp_sentence_end_tail():
                        self._keysound_player.play_release()
                try:
                    queue_delay_ms = max(0, int((time.monotonic() - handler_entry_s) * 1000))
                    if queue_delay_ms > 500:
                        queue_delay_ms = 0
                    self._timing_service.on_tag_and_delete_next_released(key_name, queue_delay_ms)
                except Exception as e:
                    self._show_runtime_error(str(e))
                finally:
                    self._pressed_keys.discard(key_name)
            a0.accept()
            return

        # 长按/短按释放处理
        if self._pending_press_key == key_upper and self._long_press_timer.isActive():
            # 定时器仍在运行 = 短按（300ms 内释放）
            self._long_press_timer.stop()
            action = self._pending_press_action_short
            self._pending_press_key = None
            self._pending_press_action_short = None
            self._pending_press_action_long = None
            if action:
                self._execute_action(action, key)
            a0.accept()
            return

        # 长按已超时，pending 已被 _on_long_press_timeout 清除，忽略释放
        if a0.isAutoRepeat():
            a0.ignore()
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
            # 逗号用占位名 COMMA，必须与 _KeyCaptureButton._build_key_name 一致，
            # 否则配置里存的 "COMMA" 与按键事件解析结果匹配不上。
            Qt.Key.Key_Comma: "COMMA",
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
            "D": "play_pause",
            "S": "stop",
            "Z": "seek_back",
            "X": "seek_forward",
            "Q": "speed_down",
            "W": "speed_up",
            "F2": "edit_ruby",
            "F3": "toggle_word_join",
            "UP": "nav_prev_line",
            "DOWN": "nav_next_line",
            "LEFT": "nav_prev_char",
            "RIGHT": "nav_next_char",
            "ALT+UP": "timestamp_up",
            "ALT+DOWN": "timestamp_down",
            "ALT+LEFT": "cycle_checkpoint_prev",
            "ALT+RIGHT": "cycle_checkpoint",
            "SHIFT+ENTER": "merge_line_up",
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

    def pause_poll_for_page_animation(self, duration_ms: int = 350) -> None:
        """页面切换动画期间暂停位置拉取定时器。

        停止 timer 后，transport/timeline/preview 不会收到新的 setValue/update 调用，
        qfluentwidgets Slider 的内部动画也因此没有新目标追逐，会迅速收敛并停止重绘。
        页面动画结束后（duration_ms 毫秒）自动恢复，不影响打轴精度。
        """
        if not self._position_poll_timer.isActive():
            return
        self._position_poll_timer.stop()
        QTimer.singleShot(
            duration_ms,
            lambda: (
                self._position_poll_timer.start()
                if self._timing_service and self._timing_service.is_playing()
                else None
            ),
        )

    def _poll_audio_position(self) -> None:
        """UI 线程 QTimer 主动拉取音频位置（替代旧的回调线程+信号推送）。

        直接从音频引擎获取基于 perf_counter 外推的高精度位置，
        消除多层异步排队带来的延迟和抖动。
        """
        if not self._timing_service:
            return
        engine = self._timing_service._audio_engine
        position_ms = self._timing_service.get_position_ms()
        duration_ms = self._timing_service.get_duration_ms()

        # 页面切换动画期间（self.y() != 0）跳过 UI 重绘，避免与动画争抢导致控件抖动。
        # 位置读取和播放结束检测不受影响，不影响打轴精度。
        if self.y() == 0:
            self.transport.set_duration(duration_ms)
            self.timeline.set_duration(duration_ms)
            self.transport.set_position(position_ms)
            self.timeline.set_position(position_ms)
            self.preview.set_current_time_ms(position_ms)

        # 检测播放结束（位置到达末尾或引擎已停止）
        if not engine.is_playing():
            self.transport.set_playing(False)
            self.preview.set_playing(False)
            self.timeline.set_playing(False)
            self.lbl_status.setText("播放完毕")
            self._update_mode_indicator()
            # 重置自动滚动状态
            self._auto_scroll_suspended = False
            self._auto_scroll_new_line_reached = False
            self._auto_scroll_cooldown_timer.stop()
            # 停止位置拉取定时器
            self._position_poll_timer.stop()
            # 切换到编辑模式时校验所有行时间戳
            self._validate_all_timestamps()

    # ==================== 自动滚动状态机 ====================

    def _on_cycle_scroll_mode(self):
        """按钮点击：循环切换滚动模式 auto → always → never → auto，并持久化。"""
        modes = ["auto", "always", "never"]
        self._scroll_mode = modes[(modes.index(self._scroll_mode) + 1) % len(modes)]
        self._sync_scroll_mode()
        # 持久化到 config
        setting_iface = self._get_setting_interface()
        if setting_iface is not None:
            s = setting_iface.get_settings()
            s.set("timing.scroll_mode", self._scroll_mode)
            s.save()

    _SCROLL_MODE_LABELS = {"auto": "自动滚动", "always": "始终滚动", "never": "从不滚动"}

    def _sync_scroll_mode(self):
        """将当前 _scroll_mode 同步到按钮文字、颜色和 preview。"""
        self.btn_scroll_mode.setText(self._SCROLL_MODE_LABELS.get(self._scroll_mode, "自动滚动"))
        self._update_scroll_mode_btn_style()
        self.preview.set_scroll_mode(self._scroll_mode)
        # 切换到 always / auto 时：重置挂起状态并立刻滚动到当前播放行
        if self._scroll_mode in ("always", "auto"):
            self._auto_scroll_suspended = False
            self._auto_scroll_new_line_reached = False
            self._auto_scroll_cooldown_timer.stop()
            self.preview.resume_auto_scroll()

    def _update_scroll_mode_btn_style(self):
        """根据当前滚动模式刷新按钮颜色。

        使用 qfluentwidgets setCustomStyleSheet 将颜色 QSS 追加到按钮原有样式之后，
        不覆盖 border / padding 等布局属性，避免图标错位。
        浅色和深色两套 QSS 分别传入，qfluentwidgets 主题切换时自动选用对应版本。
        """
        tc_l = ThemeColors(is_dark=False)
        tc_d = ThemeColors(is_dark=True)
        _bgs = {
            "auto":   (tc_l.accent_secondary, tc_d.accent_secondary),  # 蓝
            "always": (tc_l.status_complete,  tc_d.status_complete),   # 绿
            "never":  (tc_l.status_none,      tc_d.status_none),       # 灰
        }
        bg_l, bg_d = _bgs.get(self._scroll_mode, _bgs["auto"])

        def make_qss(bg) -> str:
            lum = 0.299 * bg.red() + 0.587 * bg.green() + 0.114 * bg.blue()
            text = "#1a1a1a" if lum > 150 else "#ffffff"
            return (
                f"#btnScrollMode {{"
                f" background-color: {bg.name()};"
                f" color: {text};"
                f"}}"
                f" #btnScrollMode:hover {{"
                f" background-color: {bg.lighter(115).name()};"
                f"}}"
                f" #btnScrollMode:pressed {{"
                f" background-color: {bg.darker(110).name()};"
                f"}}"
            )

        setCustomStyleSheet(self.btn_scroll_mode, make_qss(bg_l), make_qss(bg_d))

    def _suspend_auto_scroll(self):
        """挂起自动滚动：重置冷却状态，通知 preview 暂停。"""
        if self._scroll_mode == "always":
            return
        self._auto_scroll_suspended = True
        self._auto_scroll_new_line_reached = False
        self._auto_scroll_cooldown_timer.stop()
        self.preview._suspend_auto_scroll()

    def _on_user_interaction_during_auto_scroll(self):
        """preview 用户交互信号的槽：同步挂起状态并停止冷却计时器。"""
        self._auto_scroll_suspended = True
        self._auto_scroll_new_line_reached = False
        self._auto_scroll_cooldown_timer.stop()

    def _on_auto_scroll_line_changed(self):
        """preview 自动滚动换行信号的槽：标记新行已到达，启动 3s 冷却。"""
        if self._auto_scroll_suspended:
            self._auto_scroll_new_line_reached = True
            if not self._auto_scroll_cooldown_timer.isActive():
                self._auto_scroll_cooldown_timer.start()

    def _on_auto_scroll_cooldown_timeout(self):
        """冷却超时：若播放已到达新行，恢复自动滚动。"""
        if self._auto_scroll_suspended and self._auto_scroll_new_line_reached:
            self._auto_scroll_suspended = False
            self._auto_scroll_new_line_reached = False
            self.preview.resume_auto_scroll()

    # ========================================================

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
    
    def _handle_foucus_moved(self, line_idx: int, char_idx: int):
        self.preview.set_focus_position(line_idx, char_idx)

    def _handle_center_current_line(self):
        self.preview.scroll_current_line_to_center()

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

        new_line_idx = max(0, min(position.line_idx, len(self._project.sentences) - 1))
        # 行切换时校验上一行的时间戳
        if new_line_idx != self._current_line_idx:
            if 0 <= self._current_line_idx < len(self._project.sentences):
                self._validate_line_timestamps(self._current_line_idx)
        self._current_line_idx = new_line_idx
        self._update_selected_checkpoint(new_line_idx, position.char_idx, position.checkpoint_idx)
        # cp 标记点击路径：跳过光标移动，保持 selected_char 不被污染。
        # 仍需要刷新 preview 显示以反映新的 selected_cp 高亮。
        if self._suppress_cp_cursor_move:
            self.preview._update_display()
        else:
            self.preview.set_current_position(new_line_idx, position.char_idx)
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
                total_chars = len(sentence.characters)
                # 使用带 global_offset 的时间戳，与实际渲染/导出预览一致
                ts_parts = []
                for ts in ch.global_timestamps:
                    m, s = divmod(ts // 1000, 60)
                    ms = ts % 1000
                    ts_parts.append(f"{m:02d}:{s:02d}.{ms:03d}")
                if ch.is_sentence_end and ch.global_sentence_end_ts is not None:
                    ets = ch.global_sentence_end_ts
                    m, s = divmod(ets // 1000, 60)
                    ms = ets % 1000
                    ts_parts.append(f"句尾{m:02d}:{s:02d}.{ms:03d}")
                if ts_parts:
                    char_info = f" | 字 {char_idx + 1}/{total_chars} | 「{ch.char}」 {', '.join(ts_parts)}"
                else:
                    char_info = f" | 字 {char_idx + 1}/{total_chars} | 「{ch.char}」 未打轴"
            self.lbl_line_info.setText(f"行 {idx + 1}/{total}: {preview}{char_info}")
        else:
            self.lbl_line_info.setText("当前行: -")

    def _update_time_tags_display(self):
        if not self._project:
            return
        # 使用渲染时间戳（带偏移），与波形显示对齐
        self.timeline.set_time_tags(self._project.collect_all_global_timestamp_ms_with_chars())

    def _update_status(self):
        if not self._project:
            self.lbl_progress.setText("行: 0/0 | 进度: 0%")
            return
        meaningful_lines = [
            s for s in self._project.sentences
            if any(c.total_timing_points > 0 for c in s.characters)
        ]
        total = len(meaningful_lines)
        timed = sum(1 for s in meaningful_lines if s.has_timetags)
        pct = int(timed / total * 100) if total > 0 else 0
        self.lbl_progress.setText(f"行: {total} | 已打轴: {timed}/{total} ({pct}%)")

    def refresh_lyric_display(self):
        self.preview._update_display()

    def _auto_analyze_rubies(self, only_noruby: bool = False, auto_detect_chinese: bool = False):
        """执行注音分析（核心逻辑，供多处复用）。

        分析在后台 QThread 中进行，不阻塞 UI。分析结果通过信号回调到主线程，
        再手动构建 SentenceSnapshotCommand 纳入 undo/redo 栈。

        Args:
            only_noruby: True=仅分析未注音字符，False=全部重新分析
            auto_detect_chinese: True=自动检测纯中文歌词并走中文模式（跳过注音）。
                仅导入歌词后的自动触发应传 True；用户手动按"注音分析"按钮明确表达了
                注音意图，应传 False，避免纯汉字日文行被误判为中文。
        """
        if not self._project:
            return
        if getattr(self, "_ruby_analyzing", False):
            return

        from strange_uta_game.backend.application import AutoCheckService, is_chinese_lyrics
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        from strange_uta_game.frontend.workers import RubyAnalyzeWorker

        app_settings = AppSettings()
        llm_active = app_settings.llm_ruby_active()
        auto_check_flags = app_settings.get_all().get("auto_check", {})
        user_dict = app_settings.load_effective_dictionary()
        annotate_katakana_with_english = app_settings.get(
            "ruby_dictionary.annotate_katakana_with_english", False
        )
        delete_types = auto_check_flags.get("delete_ruby_types", [])

        # 中文歌词检测：仅导入歌词的自动触发阶段启用；用户主动触发注音分析时
        # 视为明确需要注音，不再检测中文（避免纯汉字日文行被误判）。
        chinese_mode = (
            auto_detect_chinese
            and auto_check_flags.get("chinese_lyrics_detection", True)
            and is_chinese_lyrics("".join(s.text for s in self._project.sentences))
        )

        # LLM 注音激活时不需要本地日语 IME，跳过 WinRT 安装引导。中文模式同样跳过。
        if not chinese_mode and not llm_active:
            from strange_uta_game.frontend.winrt_japanese_guide import (
                ensure_winrt_japanese,
            )
            if not ensure_winrt_japanese(self):
                return

        # AutoCheckService（含 WinRTAnalyzer / LLMRubyAnalyzer）在主线程创建，
        # 确保 WinRT STA apartment 正确；LLM 整首一次发送需传入全部行文本。
        if chinese_mode:
            analyzer = None
            llm_apply_user_dict = True
            auto_check = AutoCheckService(
                auto_check_flags=auto_check_flags,
                user_dictionary=user_dict,
                annotate_katakana_with_english=annotate_katakana_with_english,
                chinese_mode=True,
            )
        else:
            lines = [s.text for s in self._project.sentences]
            analyzer = app_settings.build_ruby_analyzer(
                lines, annotate_katakana_with_english=annotate_katakana_with_english
            )
            llm_apply_user_dict = app_settings.llm_apply_user_dict() if llm_active else True
            auto_check = AutoCheckService(
                ruby_analyzer=analyzer,
                auto_check_flags=auto_check_flags,
                user_dictionary=user_dict,
                annotate_katakana_with_english=annotate_katakana_with_english,
            )

        # 在主线程提前快照 before 状态和光标位置（worker 运行期间不能读 self._project）
        before_sentences = deepcopy(self._project.sentences)
        undo_pos = (self._current_line_idx, self.preview._current_char_idx)
        focus_line_idx = self._current_line_idx
        focus_char_idx = self.preview._current_char_idx

        project_copy = deepcopy(self._project)

        green = theme.status_complete.name()
        state_tooltip = StateToolTip("正在分析注音", "准备中...", self)
        state_tooltip.setStyleSheet(f"""
            StateToolTip {{
                background-color: {green};
                border: 1px solid {green};
                border-radius: 8px;
            }}
            StateToolTip QLabel {{
                color: white;
            }}
        """)
        state_tooltip.move(state_tooltip.getSuitablePos())
        state_tooltip.show()
        self._ruby_analyzing = True

        worker = RubyAnalyzeWorker(
            project_copy, auto_check, only_noruby, delete_types,
            llm_apply_user_dict=llm_apply_user_dict,
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        # 保存强引用，防止 PyQt6 弱引用机制在函数返回后回收 worker/thread
        self._ruby_analyze_worker = worker
        self._ruby_analyze_thread = thread

        def _on_progress(current: int, total: int) -> None:
            state_tooltip.setContent(f"已处理 {current}/{total} 行")

        def _cleanup() -> None:
            self._ruby_analyze_worker = None
            self._ruby_analyze_thread = None
            self._ruby_analyzing = False

        def _on_finished(analyzed_project, deleted_count: int) -> None:
            state_tooltip.setState(True)
            _cleanup()

            # LLM 注音失败时已回退本地引擎，提示用户。
            if getattr(analyzer, "llm_failed", False):
                InfoBar.warning(
                    title="LLM 注音失败，已回退本地引擎",
                    content=str(getattr(analyzer, "last_error", "") or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )

            after_sentences = analyzed_project.sentences
            command_manager = (
                self._timing_service.command_manager if self._timing_service else None
            )
            if command_manager is not None:
                command = SentenceSnapshotCommand(
                    self._project,
                    before_sentences,
                    after_sentences,
                    "注音分析",
                )
                command.undo_position = undo_pos
                command.redo_position = (focus_line_idx, focus_char_idx)
                command_manager.execute(command)
            else:
                self._project.sentences = deepcopy(after_sentences)

            self._sync_after_structure_change(
                change_type="rubies",
                focus_line_idx=focus_line_idx,
                focus_char_idx=focus_char_idx,
                checkpoint_idx=None,
                move_cp=False,
            )

            if deleted_count > 0:
                InfoBar.success(
                    title="注音分析完成",
                    content=f"已重新分析注音，并自动删除了 {deleted_count} 个注音",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )
            else:
                InfoBar.success(
                    title="注音分析完成",
                    content="已重新分析注音",
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=3000,
                    parent=self,
                )

        def _on_error(err: str) -> None:
            state_tooltip.setState(True)
            _cleanup()
            InfoBar.warning(
                title="注音分析失败",
                content=err,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

        def _on_llm_waiting() -> None:
            state_tooltip.setContent("正在等待 LLM 返回…（整首歌词一次性发送，请稍候）")

        thread.started.connect(worker.run)
        worker.llm_waiting.connect(_on_llm_waiting)
        worker.progress.connect(_on_progress)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)

        thread.start()

    def _on_analyze_rubies(self):
        """工具栏「注音分析」— 弹三选项对话框"""
        if not self._project:
            return

        msg = QMessageBox(self)
        msg.setWindowTitle("自动分析全部注音")
        msg.setText("请选择分析范围：")
        msg.setInformativeText(
            "「全部重新分析」会覆盖现有注音。\n"
            "「仅分析未注音字符」会保留已有的人工/字典注音。"
        )
        btn_all = msg.addButton("全部重新分析", QMessageBox.ButtonRole.DestructiveRole)
        btn_only_noruby = msg.addButton(
            "仅分析未注音字符", QMessageBox.ButtonRole.AcceptRole
        )
        btn_cancel = msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_only_noruby)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked is btn_cancel or clicked is None:
            return
        only_noruby = clicked is btn_only_noruby
        self._auto_analyze_rubies(only_noruby=only_noruby)

    def _auto_analyze_all_rubies(self):
        """自动分析全部注音（用于歌词导入后重新注音，覆盖已有）"""
        self._auto_analyze_rubies(only_noruby=False)

    def _analyze_rubies_subset(
        self,
        line_idx: int,
        restrict_indices: Optional[set],
        label: str,
        *,
        show_winrt_dialog: bool = True,
    ) -> None:
        """对单行（restrict_indices=None）或行内选定字符执行注音分析（异步）。"""
        self._analyze_rubies_specs_async(
            [(line_idx, restrict_indices)], label,
            show_winrt_dialog=show_winrt_dialog,
        )

    def _analyze_rubies_specs_async(
        self,
        specs: list,
        label: str,
        *,
        show_winrt_dialog: bool = True,
    ) -> None:
        """对多个指定行/范围批量执行注音分析（后台 QThread，不阻塞 UI）。

        Args:
            specs: list of (line_idx, restrict_indices | None)
            label: 用于 InfoBar 标题和 undo 描述
            show_winrt_dialog: False 时 WinRT 不可用则静默跳过（粘贴触发时用）
        """
        if not self._project or not specs:
            return
        if getattr(self, "_ruby_subset_analyzing", False):
            return

        from strange_uta_game.backend.application import AutoCheckService
        from strange_uta_game.frontend.settings.settings_interface import AppSettings
        from strange_uta_game.frontend.workers import RubySubsetAnalyzeWorker

        app_settings = AppSettings()
        llm_active = app_settings.llm_ruby_active()

        # LLM 注音激活时不依赖本地日语 IME，跳过 WinRT 检查/引导。
        # 用户主动触发的按行/按选定字符分析：不做中文检测——按下"注音分析"按钮
        # 即表示需要注音，避免纯汉字日文行被误判为中文跳过。
        if not llm_active:
            from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
                winrt_japanese_status,
            )
            from strange_uta_game.frontend.winrt_japanese_guide import (
                ensure_winrt_japanese,
            )

            if show_winrt_dialog:
                if not ensure_winrt_japanese(self):
                    return
            else:
                available, _ = winrt_japanese_status()
                if not available:
                    return

        auto_check_flags = app_settings.get_all().get("auto_check", {})
        user_dict = app_settings.load_effective_dictionary()
        annotate_katakana_with_english = app_settings.get(
            "ruby_dictionary.annotate_katakana_with_english", False
        )
        # LLM 整首一次发送：传入全部行文本以保留上下文（按行命中缓存）。
        lines = [s.text for s in self._project.sentences]
        analyzer = app_settings.build_ruby_analyzer(
            lines, annotate_katakana_with_english=annotate_katakana_with_english
        )
        llm_apply_user_dict = app_settings.llm_apply_user_dict() if llm_active else True
        auto_check = AutoCheckService(
            ruby_analyzer=analyzer,
            auto_check_flags=auto_check_flags,
            user_dictionary=user_dict,
            annotate_katakana_with_english=annotate_katakana_with_english,
        )

        before_sentences = deepcopy(self._project.sentences)
        undo_pos = (self._current_line_idx, self.preview._current_char_idx)
        focus_line_idx = specs[0][0]
        focus_char_idx = self.preview._current_char_idx

        project_copy = deepcopy(self._project)
        self._ruby_subset_analyzing = True

        # LLM 注音整首一次发送、可能较慢：显示忙碌指示器（本地引擎很快，无需）。
        subset_tooltip = None
        if llm_active:
            green = theme.status_complete.name()
            subset_tooltip = StateToolTip("正在分析注音", "正在等待 LLM 返回…", self)
            subset_tooltip.setStyleSheet(f"""
                StateToolTip {{
                    background-color: {green};
                    border: 1px solid {green};
                    border-radius: 8px;
                }}
                StateToolTip QLabel {{
                    color: white;
                }}
            """)
            subset_tooltip.move(subset_tooltip.getSuitablePos())
            subset_tooltip.show()

        worker = RubySubsetAnalyzeWorker(
            project_copy, auto_check, specs, apply_user_dict=llm_apply_user_dict
        )
        thread = QThread(self)
        worker.moveToThread(thread)

        self._ruby_subset_analyze_worker = worker
        self._ruby_subset_analyze_thread = thread

        def _cleanup() -> None:
            self._ruby_subset_analyze_worker = None
            self._ruby_subset_analyze_thread = None
            self._ruby_subset_analyzing = False

        def _close_tooltip() -> None:
            if subset_tooltip is not None:
                subset_tooltip.setState(True)

        def _on_finished(analyzed_project) -> None:
            _cleanup()
            _close_tooltip()
            if getattr(analyzer, "llm_failed", False):
                InfoBar.warning(
                    title="LLM 注音失败，已回退本地引擎",
                    content=str(getattr(analyzer, "last_error", "") or ""),
                    orient=Qt.Orientation.Horizontal,
                    isClosable=True,
                    position=InfoBarPosition.TOP,
                    duration=5000,
                    parent=self,
                )
            after_sentences = analyzed_project.sentences
            command_manager = (
                self._timing_service.command_manager if self._timing_service else None
            )
            if command_manager is not None:
                command = SentenceSnapshotCommand(
                    self._project, before_sentences, after_sentences, label
                )
                command.undo_position = undo_pos
                command.redo_position = (focus_line_idx, focus_char_idx)
                command_manager.execute(command)
            else:
                self._project.sentences = deepcopy(after_sentences)

            self._sync_after_structure_change(
                change_type="rubies",
                focus_line_idx=focus_line_idx,
                focus_char_idx=focus_char_idx,
                checkpoint_idx=None,
                move_cp=False,
            )
            InfoBar.success(
                title=f"{label}完成",
                content="已分析所选范围的注音",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )

        def _on_error(err: str) -> None:
            _cleanup()
            _close_tooltip()
            InfoBar.warning(
                title=f"{label}失败",
                content=err,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )

        def _on_llm_waiting() -> None:
            if subset_tooltip is not None:
                subset_tooltip.setContent("正在等待 LLM 返回…（整首歌词一次性发送，请稍候）")

        thread.started.connect(worker.run)
        worker.llm_waiting.connect(_on_llm_waiting)
        worker.finished.connect(_on_finished)
        worker.error.connect(_on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def _on_analyze_rubies_by_line(self):
        """工具栏「按行注音分析」— 仅分析当前行。"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            InfoBar.warning(
                title="未选中行",
                content="请先在歌词中选择要分析的行",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return
        self._analyze_rubies_subset(line_idx, None, "按行注音分析")

    def _on_analyze_rubies_selected(self):
        """工具栏「注音分析所选字符」— 仅分析当前行的选中字符范围。"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        char_idx = self.preview._current_char_idx
        if line_idx < 0 or line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx < 0 or char_idx >= len(sentence.characters):
            InfoBar.warning(
                title="未选中字符",
                content="请先选择要分析的字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        start_idx = char_idx
        end_idx = char_idx
        if (
            self.preview._focus_line_idx == line_idx
            and self.preview._focus_char_idx >= 0
            and self.preview._focus_char_range_end >= 0
        ):
            start_idx = min(
                self.preview._focus_char_idx, self.preview._focus_char_range_end
            )
            end_idx = max(
                self.preview._focus_char_idx, self.preview._focus_char_range_end
            )
        self._analyze_rubies_subset(
            line_idx, set(range(start_idx, end_idx + 1)), "注音分析所选字符"
        )

    def _on_open_fulltext(self):
        """工具栏「全文本编辑」— 以对话框打开全文本注音编辑界面。"""
        if not self._project:
            InfoBar.warning(
                title="无项目",
                content="请先创建或打开项目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return
        from .fulltext_interface import FullTextEditDialog

        line_idx = max(0, self._current_line_idx)
        char_idx = max(0, self.preview._current_char_idx)
        dlg = FullTextEditDialog(
            self._store, self, current_line=line_idx, current_char=char_idx
        )
        dlg.exec()
