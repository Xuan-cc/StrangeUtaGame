"""编辑器界面。

打轴主界面，包含：
- 播放控制栏（播放/暂停/停止/进度/速度/音量）
- 工具栏（保存/加载音频/撤销/重做）
- 卡拉OK 歌词预览区
- 时间轴
- 状态栏
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QCheckBox,
    QFileDialog,
    QFrame,
    QDialog,
    QFormLayout,
    QLineEdit,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QRect
from PyQt6.QtGui import (
    QFont,
    QColor,
    QPainter,
    QPen,
    QBrush,
    QPaintEvent,
    QMouseEvent,
    QKeyEvent,
    QFontMetrics,
    QDragEnterEvent,
    QDropEvent,
)

from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    Slider,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    SimpleCardWidget,
    ToolButton,
    PrimaryToolButton,
    RoundMenu,
    Action,
)

from typing import Optional, List, Callable
from pathlib import Path
from copy import deepcopy
import time

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
)
from strange_uta_game.backend.application import (
    CheckpointPosition,
    TimingService,
)
from strange_uta_game.backend.application.commands.base import Command
from strange_uta_game.backend.infrastructure.audio import AudioLoadError


class _SentenceSnapshotCommand(Command):
    """基于句子列表快照的结构编辑撤销命令。"""

    def __init__(
        self,
        project: Project,
        before_sentences: List[Sentence],
        after_sentences: List[Sentence],
        description: str,
    ):
        self._project = project
        self._before_sentences = before_sentences
        self._after_sentences = after_sentences
        self._description = description

    def execute(self) -> None:
        self._project.sentences = deepcopy(self._after_sentences)
        self._project._update_timestamp()

    def undo(self) -> None:
        self._project.sentences = deepcopy(self._before_sentences)
        self._project._update_timestamp()

    @property
    def description(self) -> str:
        return self._description


# ──────────────────────────────────────────────
# 播放控制栏
# ──────────────────────────────────────────────
class TransportBar(QFrame):
    """播放控制栏 - 紧凑水平布局"""

    play_clicked = pyqtSignal()
    pause_clicked = pyqtSignal()
    stop_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)
    speed_changed = pyqtSignal(float)
    volume_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._is_playing = False
        self.setFixedHeight(56)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        # 停止
        self.btn_stop = ToolButton(FIF.CANCEL, self)
        self.btn_stop.setFixedSize(40, 40)
        self.btn_stop.clicked.connect(self.stop_clicked.emit)
        layout.addWidget(self.btn_stop)

        # 播放/暂停
        self.btn_play = PrimaryToolButton(FIF.PLAY, self)
        self.btn_play.setFixedSize(40, 40)
        self.btn_play.clicked.connect(self._on_play_clicked)
        layout.addWidget(self.btn_play)

        # 时间
        self.lbl_time = QLabel("00:00.00 / 00:00.00")
        self.lbl_time.setStyleSheet("font-family: monospace; font-size: 12px;")
        self.lbl_time.setMinimumWidth(140)
        layout.addWidget(self.lbl_time)

        # 进度条
        self.slider_progress = Slider(Qt.Orientation.Horizontal, self)
        self.slider_progress.setRange(0, 10000)
        self.slider_progress.setValue(0)
        self.slider_progress.sliderReleased.connect(self._on_seek)
        layout.addWidget(self.slider_progress, stretch=1)

        # 速度（百分比显示，输入框，内部转换为倍率）
        lbl_speed = QLabel("速度")
        lbl_speed.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(lbl_speed)
        self.edit_speed = QLineEdit(self)
        self.edit_speed.setText("100%")
        self.edit_speed.setFixedWidth(60)
        self.edit_speed.setFixedHeight(32)
        self.edit_speed.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_speed.setStyleSheet("font-size: 12px;")
        self.edit_speed.editingFinished.connect(self._on_speed_editing_finished)
        layout.addWidget(self.edit_speed)

        # 音量
        lbl_vol = QLabel("音量")
        lbl_vol.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(lbl_vol)
        self.slider_volume = Slider(Qt.Orientation.Horizontal, self)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(100)
        self.slider_volume.setFixedWidth(100)
        self.slider_volume.valueChanged.connect(self.volume_changed.emit)
        layout.addWidget(self.slider_volume)

    def _on_play_clicked(self):
        if self._is_playing:
            self.pause_clicked.emit()
        else:
            self.play_clicked.emit()

    def _on_seek(self):
        if self._duration_ms > 0:
            ratio = self.slider_progress.value() / 10000
            self.seek_requested.emit(int(ratio * self._duration_ms))

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self._update_label()

    def set_position(self, ms: int):
        self._current_ms = ms
        if self._duration_ms > 0:
            self.slider_progress.setValue(int((ms / self._duration_ms) * 10000))
        self._update_label()

    def set_playing(self, playing: bool):
        self._is_playing = playing
        self.btn_play.setIcon(FIF.PAUSE if playing else FIF.PLAY)

    def _update_label(self):
        def fmt(ms):
            s = ms // 1000
            c = (ms % 1000) // 10
            return f"{s // 60:02d}:{s % 60:02d}.{c:02d}"

        self.lbl_time.setText(f"{fmt(self._current_ms)} / {fmt(self._duration_ms)}")

    def _on_speed_editing_finished(self):
        """速度输入框编辑完成 — 解析并发射信号"""
        text = self.edit_speed.text().strip().replace("%", "")
        try:
            val = int(text)
            val = max(50, min(200, val))
        except ValueError:
            val = 100
        self.edit_speed.setText(f"{val}%")
        self.speed_changed.emit(val / 100.0)

    def set_speed_value(self, pct: int):
        """设置速度值（百分比整数，如 100）"""
        pct = max(50, min(200, pct))
        self.edit_speed.setText(f"{pct}%")
        self.speed_changed.emit(pct / 100.0)

    def get_speed_value(self) -> int:
        """获取当前速度值（百分比整数，如 100）"""
        text = self.edit_speed.text().strip().replace("%", "")
        try:
            return max(50, min(200, int(text)))
        except ValueError:
            return 100


# ──────────────────────────────────────────────
# 工具栏
# ──────────────────────────────────────────────
class EditorToolBar(QFrame):
    """编辑器工具栏 - 保存/加载音频/加载歌词/批量变更/修改字符/插入导唱符/偏移调整"""

    save_clicked = pyqtSignal()
    load_audio_clicked = pyqtSignal()
    load_lyrics_clicked = pyqtSignal()
    bulk_change_clicked = pyqtSignal()
    modify_char_clicked = pyqtSignal()
    insert_guide_clicked = pyqtSignal()
    offset_changed = pyqtSignal(int)  # 偏移量变化（毫秒）

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(40)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(6)

        self.btn_save = PushButton("保存项目", self)
        self.btn_save.setIcon(FIF.SAVE)
        self.btn_save.setFixedHeight(32)
        self.btn_save.clicked.connect(self.save_clicked.emit)
        layout.addWidget(self.btn_save)

        self.btn_load_audio = PushButton("加载音频", self)
        self.btn_load_audio.setIcon(FIF.MUSIC)
        self.btn_load_audio.setFixedHeight(32)
        self.btn_load_audio.clicked.connect(self.load_audio_clicked.emit)
        layout.addWidget(self.btn_load_audio)

        self.btn_load_lyrics = PushButton("加载歌词", self)
        self.btn_load_lyrics.setIcon(FIF.DOCUMENT)
        self.btn_load_lyrics.setFixedHeight(32)
        self.btn_load_lyrics.clicked.connect(self.load_lyrics_clicked.emit)
        layout.addWidget(self.btn_load_lyrics)

        layout.addSpacing(10)

        self.btn_modify_char = PushButton("修改所选字符", self)
        self.btn_modify_char.setIcon(FIF.EDIT)
        self.btn_modify_char.setFixedHeight(32)
        self.btn_modify_char.clicked.connect(self.modify_char_clicked.emit)
        layout.addWidget(self.btn_modify_char)

        self.btn_insert_guide = PushButton("插入导唱符", self)
        self.btn_insert_guide.setIcon(FIF.ADD)
        self.btn_insert_guide.setFixedHeight(32)
        self.btn_insert_guide.clicked.connect(self.insert_guide_clicked.emit)
        layout.addWidget(self.btn_insert_guide)

        layout.addSpacing(10)

        self.btn_bulk_change = PushButton("批量变更 (Ctrl+H)", self)
        self.btn_bulk_change.setIcon(FIF.EDIT)
        self.btn_bulk_change.setFixedHeight(32)
        self.btn_bulk_change.clicked.connect(self.bulk_change_clicked.emit)
        layout.addWidget(self.btn_bulk_change)

        layout.addSpacing(10)

        # 整体时间戳偏移调整
        lbl_offset = QLabel("Karaoke渲染以及导出偏移:")
        lbl_offset.setStyleSheet("font-size: 11px;")
        layout.addWidget(lbl_offset)
        self.edit_offset = QLineEdit(self)
        self.edit_offset.setText("-100")
        self.edit_offset.setFixedWidth(80)
        self.edit_offset.setFixedHeight(32)
        self.edit_offset.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.edit_offset.setStyleSheet("font-size: 12px;")
        self.edit_offset.editingFinished.connect(self._on_offset_editing_finished)
        layout.addWidget(self.edit_offset)

        layout.addStretch()

        # 状态标签
        self.lbl_audio = QLabel("未加载音频")
        self.lbl_audio.setStyleSheet("font-size: 11px; color: gray;")
        layout.addWidget(self.lbl_audio)

    def _on_offset_editing_finished(self):
        """偏移输入框编辑完成 — 解析并发射信号"""
        text = self.edit_offset.text().strip()
        try:
            val = int(text)
            val = max(-2000, min(2000, val))
        except ValueError:
            val = 0
        self.edit_offset.setText(str(val))
        self.offset_changed.emit(val)


# ──────────────────────────────────────────────
# 卡拉OK 歌词预览
# ──────────────────────────────────────────────
class KaraokePreview(QWidget):
    """多行歌词预览，带逐字高亮、注音显示和滚动支持。

    滚动模型：_scroll_center_line 表示视口中央对应的行索引（浮点数）。
    - 自动跟随：打轴推进时自动居中当前行
    - 手动滚动：鼠标滚轮浏览，点击某行后重新居中
    - 首行居中：_scroll_center_line=0 时首行在正中央，上方留空
    """

    line_clicked = pyqtSignal(int)
    checkpoint_clicked = pyqtSignal(int, int, int)  # line_idx, char_idx, checkpoint_idx
    char_edit_requested = pyqtSignal(int, int)  # line_idx, char_idx (F2 key)
    seek_to_char_requested = pyqtSignal(int, int)  # line_idx, char_idx (double-click)
    char_selected = pyqtSignal(int, int)  # line_idx, char_idx
    singer_change_requested = pyqtSignal(
        int, int, int, str
    )  # line_idx, start_char, end_char, singer_id
    delete_chars_requested = pyqtSignal(int, int, int)
    insert_space_after_requested = pyqtSignal(int, int)
    merge_line_up_requested = pyqtSignal(int)
    delete_line_requested = pyqtSignal(int)
    insert_blank_line_requested = pyqtSignal(int)
    add_checkpoint_requested = pyqtSignal(int, int)
    remove_checkpoint_requested = pyqtSignal(int, int)
    toggle_sentence_end_requested = pyqtSignal(int, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._current_line_idx = 0
        self._current_char_idx = 0
        self._current_checkpoint_idx: Optional[int] = None
        self._current_time_ms = 0
        self._render_offset_ms = 0
        self._visible_lines = 7  # 视口内可见行数（决定行高）
        self._scroll_center_line: float = 0.0  # 视口中央对应的行索引
        self._checkpoint_hitboxes: list = []  # [(QRect, line_idx, char_idx, cp_idx)]
        self._char_hitboxes: list = []  # [(QRect, line_idx, char_idx)]
        self.setMinimumHeight(400)
        self.setMouseTracking(True)

        # 划词选中状态
        self._sel_line_idx: int = -1
        self._sel_start_char: int = -1
        self._sel_end_char: int = -1
        self._sel_dragging: bool = False

        # 缓存字体和 QFontMetrics，避免每帧重建
        self._font_current = QFont("Microsoft YaHei", 22, QFont.Weight.Bold)
        self._font_context = QFont("Microsoft YaHei", 18)
        self._font_ruby = QFont("Microsoft YaHei", 10)
        self._font_checkpoint = QFont("Microsoft YaHei", 10)
        self._font_line_number = QFont("Microsoft YaHei", 10)
        self._fm_current = QFontMetrics(self._font_current)
        self._fm_context = QFontMetrics(self._font_context)
        self._fm_ruby = QFontMetrics(self._font_ruby)
        self._fm_checkpoint = QFontMetrics(self._font_checkpoint)
        self._fm_line_number = QFontMetrics(self._font_line_number)
        self._line_number_margin = 45  # 行号左侧区域宽度

        # 逐句渲染数据缓存（避免每帧重复计算）
        self._sentence_cache: dict = {}
        self._cache_version: int = 0

    def set_project(self, project: Project):
        self._project = project
        self._scroll_center_line = 0.0
        self._sentence_cache.clear()
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        # 自动跟随：当前行始终居中
        self._scroll_center_line = float(line_idx)
        self._update_display()

    def set_current_time_ms(self, time_ms: int):
        self._current_time_ms = time_ms
        self.update()

    def set_render_offset(self, offset_ms: int):
        """设置渲染偏移量（毫秒），与导出偏移联动，更新所有字符的渲染时间戳"""
        self._render_offset_ms = offset_ms
        # 偏移变更时，渲染时间戳已在字符上更新，清除缓存使 wipe 区间重新计算
        self._sentence_cache.clear()
        self._cache_version += 1
        self.update()

    def _update_display(self):
        self._cache_version += 1
        self.update()

    # ---- 滚动 ----

    def wheelEvent(self, a0):
        """鼠标滚轮滚动浏览歌词"""
        if not a0 or not self._project or not self._project.sentences:
            return
        delta = a0.angleDelta().y()
        # 每个滚轮 notch（120 单位）滚动 1 行
        self._scroll_center_line -= delta / 120.0
        total = len(self._project.sentences)
        self._scroll_center_line = max(
            0.0, min(float(total - 1), self._scroll_center_line)
        )
        self.update()

    # ---- 点击 ----

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if not a0 or not self._project or not self._project.sentences:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        # 右键点击 → 打开上下文菜单
        if a0.button() == Qt.MouseButton.RightButton:
            self._show_context_menu(a0.globalPosition().toPoint(), click_x, click_y)
            return

        # 优先检查 checkpoint 标记的点击
        for marker_rect, line_idx, char_idx, cp_idx in self._checkpoint_hitboxes:
            if marker_rect.contains(click_x, click_y):
                self.checkpoint_clicked.emit(line_idx, char_idx, cp_idx)
                return

        # 检查字符文本点击 → 开始划词选择
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self._sel_line_idx = line_idx
                self._sel_start_char = char_idx
                self._sel_end_char = char_idx
                self._sel_dragging = True
                self.char_selected.emit(line_idx, char_idx)
                self.update()
                return

        # 回退到行级别点击：根据 y 坐标反算行索引
        # 清除选中状态
        self._sel_line_idx = -1
        self._sel_start_char = -1
        self._sel_end_char = -1

        h = self.height()
        line_height = h / self._visible_lines
        center_y = h / 2.0
        # 点击位置对应的行索引（浮点）
        clicked_line = self._scroll_center_line + (click_y - center_y) / line_height
        target_idx = int(round(clicked_line))
        total = len(self._project.sentences)
        if 0 <= target_idx < total:
            self.line_clicked.emit(target_idx)
        self.update()

    def mouseMoveEvent(self, a0: Optional[QMouseEvent]):
        """鼠标拖拽 → 扩展划词选择范围"""
        if not a0 or not self._sel_dragging:
            return

        move_x = int(a0.position().x())
        move_y = int(a0.position().y())

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(move_x, move_y) and line_idx == self._sel_line_idx:
                self._sel_end_char = char_idx
                self.update()
                return

    def mouseReleaseEvent(self, a0: Optional[QMouseEvent]):
        """鼠标释放 → 结束划词"""
        if a0 and a0.button() == Qt.MouseButton.LeftButton:
            self._sel_dragging = False

    def _show_context_menu(self, global_pos, click_x: int, click_y: int):
        """显示字符上下文菜单。"""
        if not self._project or not self._project.sentences:
            return

        target_line_idx = self._current_line_idx
        target_char_idx = self._current_char_idx
        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                target_line_idx = line_idx
                target_char_idx = char_idx
                self._current_line_idx = line_idx
                self._current_char_idx = char_idx
                break

        if target_line_idx < 0 or target_line_idx >= len(self._project.sentences):
            return

        sentence = self._project.sentences[target_line_idx]
        if target_char_idx < 0:
            target_char_idx = 0
        if sentence.characters and target_char_idx >= len(sentence.characters):
            target_char_idx = len(sentence.characters) - 1

        in_selection = False
        if (
            self._sel_line_idx == target_line_idx
            and self._sel_start_char >= 0
            and self._sel_end_char >= 0
        ):
            sel_start = min(self._sel_start_char, self._sel_end_char)
            sel_end = max(self._sel_start_char, self._sel_end_char)
            in_selection = sel_start <= target_char_idx <= sel_end
        else:
            sel_start = target_char_idx
            sel_end = target_char_idx

        delete_start = sel_start if in_selection else target_char_idx
        delete_end = sel_end + 1 if in_selection else target_char_idx + 1

        menu = RoundMenu(parent=self)

        delete_action = Action("删除字符", menu)
        delete_action.triggered.connect(
            lambda checked=False: self.delete_chars_requested.emit(
                target_line_idx, delete_start, delete_end
            )
        )
        menu.addAction(delete_action)

        insert_space_action = Action("在此插入空格", menu)
        insert_space_action.triggered.connect(
            lambda checked=False: self.insert_space_after_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(insert_space_action)
        menu.addSeparator()

        merge_up_action = Action("合并上一行", menu)
        merge_up_action.setEnabled(target_line_idx > 0)
        merge_up_action.triggered.connect(
            lambda checked=False: self.merge_line_up_requested.emit(target_line_idx)
        )
        menu.addAction(merge_up_action)

        delete_line_action = Action("删除本行", menu)
        delete_line_action.triggered.connect(
            lambda checked=False: self.delete_line_requested.emit(target_line_idx)
        )
        menu.addAction(delete_line_action)

        insert_blank_line_action = Action("在此插入空行", menu)
        insert_blank_line_action.triggered.connect(
            lambda checked=False: self.insert_blank_line_requested.emit(target_line_idx)
        )
        menu.addAction(insert_blank_line_action)
        menu.addSeparator()

        add_checkpoint_action = Action("增加节奏点", menu)
        add_checkpoint_action.triggered.connect(
            lambda checked=False: self.add_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(add_checkpoint_action)

        remove_checkpoint_action = Action("减少节奏点", menu)
        remove_checkpoint_action.triggered.connect(
            lambda checked=False: self.remove_checkpoint_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(remove_checkpoint_action)

        toggle_sentence_end_action = Action("设置/取消句尾", menu)
        toggle_sentence_end_action.triggered.connect(
            lambda checked=False: self.toggle_sentence_end_requested.emit(
                target_line_idx, target_char_idx
            )
        )
        menu.addAction(toggle_sentence_end_action)
        menu.addSeparator()

        singer_start = delete_start if in_selection else target_char_idx
        singer_end = delete_end - 1 if in_selection else target_char_idx
        singer_menu = RoundMenu("设置演唱者", self)
        default_singer = self._project.get_default_singer()
        default_action = Action("默认演唱者", singer_menu)
        default_action.triggered.connect(
            lambda checked=False: self.singer_change_requested.emit(
                target_line_idx, singer_start, singer_end, default_singer.id
            )
        )
        singer_menu.addAction(default_action)
        singer_menu.addSeparator()

        for singer in self._project.singers:
            action = Action(singer.name, singer_menu)
            action.triggered.connect(
                lambda checked=False, sid=singer.id: self.singer_change_requested.emit(
                    target_line_idx, singer_start, singer_end, sid
                )
            )
            singer_menu.addAction(action)

        menu.addMenu(singer_menu)
        menu.exec(global_pos)

    def _get_sentence_render_data(
        self, idx: int, sentence, main_fm, font_key: str
    ) -> dict:
        """返回缓存的逐句渲染数据，过期时重新计算。"""
        entry = self._sentence_cache.get(idx)
        if entry and entry["v"] == self._cache_version and entry["fk"] == font_key:
            return entry

        chars = sentence.chars
        characters = sentence.characters
        n_chars = len(chars)

        # 字符宽度
        char_widths = [main_fm.horizontalAdvance(ch) for ch in chars]

        # 字符组（linked_to_next 分组）
        char_groups: list = []
        cur_grp = None
        for ci in range(n_chars):
            if cur_grp is None:
                cur_grp = [ci]
                char_groups.append(cur_grp)
            elif ci > 0 and characters[ci - 1].linked_to_next:
                cur_grp.append(ci)
            else:
                cur_grp = [ci]
                char_groups.append(cur_grp)

        # 字符起始时间（使用渲染时间戳，已含偏移）
        char_start_times: dict = {}
        for ci, ch in enumerate(characters):
            if ch.render_timestamps:
                char_start_times[ci] = ch.render_timestamps[0]

        # 字符 wipe 时间区间
        char_wipe_times: dict = {}
        for group in char_groups:
            leader = group[0]
            group_start = char_start_times.get(leader)
            if group_start is None:
                for ci in group:
                    if ci in char_start_times:
                        group_start = char_start_times[ci]
                        break
            if group_start is None:
                for pci in range(leader - 1, -1, -1):
                    if pci in char_wipe_times:
                        group_start = char_wipe_times[pci][1]
                        break
            if group_start is None:
                continue
            leader_ch = characters[leader]
            if leader_ch.is_sentence_end:
                if leader_ch.render_sentence_end_ts is not None:
                    group_end = leader_ch.render_sentence_end_ts
                else:
                    group_end = group_start + 300
            else:
                group_end = None
                last_in_group = group[-1]
                for nci in range(last_in_group + 1, n_chars):
                    if nci in char_start_times:
                        group_end = char_start_times[nci]
                        break
                if group_end is None:
                    group_end = group_start + 300

            n = len(group)
            dur = group_end - group_start
            for i, ci in enumerate(group):
                char_wipe_times[ci] = (
                    group_start + dur * i / n,
                    group_start + dur * (i + 1) / n,
                )

        # 连词组信息
        linked_leader_groups: dict = {}
        linked_non_leader: set = set()
        for group in char_groups:
            if len(group) > 1:
                linked_leader_groups[group[0]] = group
                for _ci in group[1:]:
                    linked_non_leader.add(_ci)

        entry = {
            "v": self._cache_version,
            "fk": font_key,
            "char_widths": char_widths,
            "total_text_width": sum(char_widths),
            "char_wipe_times": char_wipe_times,
            "linked_leader_groups": linked_leader_groups,
            "linked_non_leader": linked_non_leader,
        }
        self._sentence_cache[idx] = entry
        return entry

    def mouseDoubleClickEvent(self, a0: Optional[QMouseEvent]):
        """双击字符 → 跳转到该字符 checkpoint 前 3 秒"""
        if not a0 or not self._project or not self._project.sentences:
            return

        click_x = int(a0.position().x())
        click_y = int(a0.position().y())

        for char_rect, line_idx, char_idx in self._char_hitboxes:
            if char_rect.contains(click_x, click_y):
                self.seek_to_char_requested.emit(line_idx, char_idx)
                return

    # ---- 绘制 ----

    def paintEvent(self, a0: Optional[QPaintEvent]):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 填充背景
        painter.fillRect(self.rect(), QColor("#FFFFFF"))

        # 清空 hitbox 缓存
        self._checkpoint_hitboxes = []
        self._char_hitboxes = []

        # 渲染时间：偏移已在 render_timestamps 中预计算，直接使用当前播放时间
        current_time = self._current_time_ms

        if not self._project or not self._project.sentences:
            painter.setPen(QColor("#999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请创建或打开项目"
            )
            painter.end()
            return

        w, h = self.width(), self.height()
        total = len(self._project.sentences)
        line_height = h / self._visible_lines
        center_y = h / 2.0

        font_current = self._font_current
        font_context = self._font_context
        font_ruby = self._font_ruby
        font_checkpoint = self._font_checkpoint

        fm_current = self._fm_current
        fm_context = self._fm_context
        fm_ruby = self._fm_ruby
        fm_checkpoint = self._fm_checkpoint

        default_highlight = QColor("#FF6B6B")

        # 计算可见行范围（留 1 行余量避免边缘裁切）
        half_visible = self._visible_lines / 2.0 + 1
        first_visible = max(0, int(self._scroll_center_line - half_visible))
        last_visible = min(total - 1, int(self._scroll_center_line + half_visible))

        for idx in range(first_visible, last_visible + 1):
            # 行中心 y 坐标
            y_center_f = center_y + (idx - self._scroll_center_line) * line_height
            y_center = int(y_center_f)

            # 跳过完全不可见的行
            if y_center_f < -line_height or y_center_f > h + line_height:
                continue

            line = self._project.sentences[idx]
            is_current = idx == self._current_line_idx

            # 绘制行号（左侧固定区域）
            painter.setFont(self._font_line_number)
            line_num_color = QColor("#FF6B6B") if is_current else QColor("#aaa")
            painter.setPen(line_num_color)
            line_num_text = str(idx + 1)
            line_num_w = self._fm_line_number.horizontalAdvance(line_num_text)
            painter.drawText(
                int(self._line_number_margin - line_num_w - 5),
                int(y_center),
                line_num_text,
            )

            # 根据演唱者获取行级别默认高亮颜色
            singer = (
                self._project.get_singer(line.singer_id) if line.singer_id else None
            )
            highlight_color = (
                QColor(singer.color) if singer and singer.color else default_highlight
            )

            # 预计算每个字符的 per-char singer 颜色（从 Character.singer_id 读取）
            _char_singer_colors: dict = {}  # char_idx -> QColor
            default_singer = self._project.get_default_singer()
            for ci, char in enumerate(line.characters):
                singer_obj = self._project.get_singer(char.singer_id)
                singer_color = singer_obj.color if singer_obj and singer_obj.color else default_singer.color
                _char_singer_colors[ci] = QColor(singer_color)

            if is_current:
                main_font = font_current
                main_fm = fm_current
                base_color = QColor("black")
            elif idx < self._current_line_idx:
                main_font = font_context
                main_fm = fm_context
                base_color = QColor("#aaa")
            else:
                main_font = font_context
                main_fm = fm_context
                base_color = QColor("#666")

            # 使用缓存的渲染数据（字符宽度/分组/wipe时间/连词信息）
            _rd = self._get_sentence_render_data(
                idx, line, main_fm, "cur" if is_current else "ctx"
            )
            char_widths = _rd["char_widths"]
            total_text_width = _rd["total_text_width"]
            char_wipe_times = _rd["char_wipe_times"]
            _linked_leader_groups = _rd["linked_leader_groups"]
            _linked_non_leader = _rd["linked_non_leader"]

            start_x = self._line_number_margin + (w - self._line_number_margin - total_text_width) // 2
            curr_x = start_x

            for char_pos, ch in enumerate(line.chars):
                char_w = char_widths[char_pos]

                # 当前打轴位置高亮背景
                if is_current and char_pos == self._current_char_idx:
                    highlight_bg = QColor("#FFE0E0")
                    bg_rect = QRect(
                        int(curr_x) - 1,
                        int(y_center - main_fm.ascent()) - 2,
                        int(char_w) + 2,
                        main_fm.height() + 4,
                    )
                    painter.fillRect(bg_rect, highlight_bg)

                # 划词选中高亮背景
                if idx == self._sel_line_idx and self._sel_start_char >= 0:
                    sel_lo = min(self._sel_start_char, self._sel_end_char)
                    sel_hi = max(self._sel_start_char, self._sel_end_char)
                    if sel_lo <= char_pos <= sel_hi:
                        sel_bg = QColor("#BDE0FE")
                        sel_rect = QRect(
                            int(curr_x) - 1,
                            int(y_center - main_fm.ascent()) - 2,
                            int(char_w) + 2,
                            main_fm.height() + 4,
                        )
                        painter.fillRect(sel_rect, sel_bg)

                # 存储字符 hitbox 用于点击检测
                char_rect = QRect(
                    int(curr_x),
                    int(y_center - main_fm.ascent()),
                    int(char_w),
                    main_fm.height(),
                )
                self._char_hitboxes.append((char_rect, idx, char_pos))

                # Ruby — 连词组合并绘制 / 单字独立绘制
                if char_pos in _linked_non_leader:
                    pass  # Ruby 由组 leader 统一绘制
                elif char_pos in _linked_leader_groups:
                    # 连词组 leader：收集组内所有 ruby 合并绘制
                    _grp = _linked_leader_groups[char_pos]
                    _grp_rubies: list = []
                    for _gci in _grp:
                        _r = line.characters[_gci].ruby
                        if _r:
                            _grp_rubies.append(_r)
                    if _grp_rubies:
                        _merged = "".join(r.text for r in _grp_rubies)
                        _grp_w = sum(char_widths[g] for g in _grp)
                        ruby_text_w = fm_ruby.horizontalAdvance(_merged)
                        ruby_x = curr_x + (_grp_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - 4)
                        painter.setFont(font_ruby)
                        painter.setPen(base_color)
                        painter.drawText(int(ruby_x), ruby_y, _merged)
                        # Wipe
                        _fw = char_wipe_times.get(_grp[0])
                        _lw = char_wipe_times.get(_grp[-1])
                        _rs = _fw[0] if _fw else None
                        _re = _lw[1] if _lw else None
                        _rh = _char_singer_colors.get(_grp[0], highlight_color)
                        if _rs is not None and _re is not None:
                            if current_time >= _re:
                                painter.setPen(_rh)
                                painter.drawText(int(ruby_x), ruby_y, _merged)
                            elif current_time >= _rs:
                                _rd = _re - _rs
                                _rr = (
                                    min(1.0, (current_time - _rs) / _rd)
                                    if _rd > 0
                                    else 1.0
                                )
                                if _rr > 0:
                                    painter.save()
                                    _rww = int(ruby_text_w * _rr)
                                    painter.setClipRect(
                                        QRect(
                                            int(ruby_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            _rww,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    painter.setPen(_rh)
                                    painter.drawText(int(ruby_x), ruby_y, _merged)
                                    painter.restore()
                        # 连词框
                        painter.save()
                        _fc = QColor(base_color)
                        _fc.setAlpha(120)
                        _fp = QPen(_fc, 1.0)
                        _fp.setStyle(Qt.PenStyle.SolidLine)
                        painter.setPen(_fp)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.drawRoundedRect(
                            int(ruby_x) - 2,
                            ruby_y - fm_ruby.ascent() - 1,
                            int(ruby_text_w) + 4,
                            fm_ruby.height() + 2,
                            2,
                            2,
                        )
                        painter.restore()
                else:
                    ruby = line.characters[char_pos].ruby
                    if ruby:
                        # 单字符 ruby（per-char 模型）
                        ruby_text_w = fm_ruby.horizontalAdvance(ruby.text)
                        ruby_x = curr_x + (char_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - 4)
                        painter.setFont(font_ruby)
                        painter.setPen(base_color)
                        painter.drawText(int(ruby_x), ruby_y, ruby.text)
                        # Wipe
                        ruby_wipe_st = char_wipe_times.get(char_pos)
                        ruby_st = ruby_wipe_st[0] if ruby_wipe_st else None
                        ruby_highlight = _char_singer_colors.get(
                            char_pos, highlight_color
                        )
                        if ruby_st is not None:
                            ruby_wipe_et = char_wipe_times.get(char_pos)
                            ruby_et = ruby_wipe_et[1] if ruby_wipe_et else ruby_st + 300
                            if current_time >= ruby_et:
                                painter.setPen(ruby_highlight)
                                painter.drawText(int(ruby_x), ruby_y, ruby.text)
                            elif current_time >= ruby_st:
                                r_dur = ruby_et - ruby_st
                                r_ratio = (
                                    min(1.0, (current_time - ruby_st) / r_dur)
                                    if r_dur > 0
                                    else 1.0
                                )
                                if r_ratio > 0:
                                    painter.save()
                                    r_wipe_w = int(ruby_text_w * r_ratio)
                                    painter.setClipRect(
                                        QRect(
                                            int(ruby_x),
                                            ruby_y - fm_ruby.ascent() - 2,
                                            r_wipe_w,
                                            fm_ruby.height() + 4,
                                        )
                                    )
                                    painter.setPen(ruby_highlight)
                                    painter.drawText(int(ruby_x), ruby_y, ruby.text)
                                    painter.restore()

                # 主文字 — 基于 checkpoint 的逐字 wipe
                painter.setFont(main_font)
                # 使用 per-char singer 颜色（如果该字符有不同的演唱者）
                char_highlight = _char_singer_colors.get(char_pos, highlight_color)

                if char_pos in char_wipe_times:
                    char_time, next_time = char_wipe_times[char_pos]

                    if current_time >= next_time:
                        # 已唱完 → 全高亮
                        painter.setPen(char_highlight)
                        painter.drawText(int(curr_x), int(y_center), ch)
                    elif current_time >= char_time:
                        # 正在唱 → wipe 渐变
                        painter.setPen(base_color)
                        painter.drawText(int(curr_x), int(y_center), ch)

                        duration = next_time - char_time
                        if duration > 0:
                            wipe_ratio = min(
                                1.0,
                                (current_time - char_time) / duration,
                            )
                        else:
                            wipe_ratio = 1.0

                        if wipe_ratio > 0:
                            painter.save()
                            wipe_w = int(char_w * wipe_ratio)
                            clip_rect = QRect(
                                int(curr_x),
                                int(y_center - main_fm.ascent() - 5),
                                wipe_w,
                                main_fm.height() + 10,
                            )
                            painter.setClipRect(clip_rect)
                            painter.setPen(char_highlight)
                            painter.drawText(int(curr_x), int(y_center), ch)
                            painter.restore()
                    else:
                        # 未唱 → 基色
                        painter.setPen(base_color)
                        painter.drawText(int(curr_x), int(y_center), ch)
                else:
                    # 不在任何字符组内 → 基色
                    painter.setPen(base_color)
                    painter.drawText(int(curr_x), int(y_center), ch)

                # 当前打轴位置指示线
                if is_current and char_pos == self._current_char_idx:
                    painter.setPen(highlight_color)
                    painter.drawLine(
                        int(curr_x),
                        int(y_center + main_fm.descent() + 2),
                        int(curr_x + char_w),
                        int(y_center + main_fm.descent() + 2),
                    )

                # Checkpoint 标记（逐 checkpoint 绘制）
                ch_obj = line.characters[char_pos]
                if ch_obj.total_timing_points > 0:
                    painter.setFont(font_checkpoint)

                    markers = []
                    for cp_idx in range(ch_obj.total_timing_points):
                        is_sentence_end_marker = (
                            ch_obj.is_sentence_end and cp_idx == ch_obj.check_count
                        )
                        has_timed = (
                            ch_obj.sentence_end_ts is not None
                            if is_sentence_end_marker
                            else cp_idx < len(ch_obj.timestamps)
                        )

                        if is_sentence_end_marker:
                            marker_char = "。"
                        elif cp_idx == 0:
                            marker_char = "▶" if has_timed else "▷"
                        else:
                            marker_char = "▮" if has_timed else "▯"

                        markers.append((marker_char, has_timed))

                    # 居中排列所有 marker
                    total_markers_w = sum(
                        fm_checkpoint.horizontalAdvance(m[0]) for m in markers
                    )
                    mx = curr_x + (char_w - total_markers_w) // 2
                    marker_y = int(y_center + main_fm.descent() + 14)

                    for cp_idx, (marker_char, has_timed) in enumerate(markers):
                        char_color = _char_singer_colors.get(char_pos, highlight_color)
                        color = char_color if has_timed else QColor("#ccc")
                        if (
                            is_current
                            and char_pos == self._current_char_idx
                            and cp_idx == self._current_checkpoint_idx
                        ):
                            h, s, v, a = char_color.getHsv()
                            if h >= 0:
                                color = QColor.fromHsv((h + 180) % 360, s, v, a)
                        painter.setPen(color)

                        mw = fm_checkpoint.horizontalAdvance(marker_char)
                        painter.drawText(int(mx), marker_y, marker_char)

                        # 存储 hitbox 用于点击检测
                        marker_rect = QRect(
                            int(mx),
                            marker_y - fm_checkpoint.ascent(),
                            int(mw),
                            fm_checkpoint.height(),
                        )
                        self._checkpoint_hitboxes.append(
                            (marker_rect, idx, char_pos, cp_idx)
                        )

                        mx += mw

                curr_x += char_w


class ModifyCharacterDialog(QDialog):
    """修改所选字符对话框 — 允许替换选中字符的文本、注音和节奏点"""

    def __init__(self, sentence, start_idx, end_idx, parent=None):
        """
        Args:
            sentence: Sentence object
            start_idx: inclusive start char index
            end_idx: inclusive end char index
            parent: parent widget
        """
        super().__init__(parent)
        self._sentence = sentence
        self._start_idx = start_idx
        self._end_idx = end_idx
        self._modified = False

        self.setWindowTitle("修改所选字符")
        self.resize(420, 300)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Field 1: Current selected chars (readonly)
        chars = sentence.characters[start_idx : end_idx + 1]
        current_text = "".join(c.char for c in chars)
        lbl_current = QLabel(current_text)
        lbl_current.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("当前选中字符:", lbl_current)

        # Field 2: New chars
        self.edit_new_chars = QLineEdit(current_text)
        self.edit_new_chars.setPlaceholderText("输入新字符")
        form.addRow("新字符:", self.edit_new_chars)

        # Field 3: New ruby (comma-separated for multi-char)
        rubies = []
        for c in chars:
            rubies.append(c.ruby.text if c.ruby else "")
        initial_ruby = ",".join(rubies) if any(rubies) else ""
        self.edit_new_ruby = QLineEdit(initial_ruby)
        self.edit_new_ruby.setPlaceholderText("注音（多字符用逗号分隔）")
        form.addRow("新注音:", self.edit_new_ruby)

        # Field 4: New check counts (comma-separated for multi-char)
        check_counts = [str(c.check_count) for c in chars]
        self.edit_new_checks = QLineEdit(",".join(check_counts))
        self.edit_new_checks.setPlaceholderText("节奏点数量（多字符用逗号分隔）")
        form.addRow("新节奏点:", self.edit_new_checks)

        layout.addLayout(form)

        # Checkbox: register to dictionary
        self.chk_register = QCheckBox("将此词注册到读音词典")
        layout.addWidget(self.chk_register)

        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_exec = PrimaryPushButton("执行", self)
        btn_exec.clicked.connect(self._on_execute)
        btn_layout.addWidget(btn_exec)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _on_execute(self):
        from strange_uta_game.backend.domain.models import Character, Ruby

        new_text = self.edit_new_chars.text().strip()
        if not new_text:
            return

        ruby_text = self.edit_new_ruby.text().strip()
        checks_text = self.edit_new_checks.text().strip()

        # Parse rubies
        if ruby_text:
            ruby_parts = [r.strip() for r in ruby_text.split(",")]
        else:
            ruby_parts = []

        # Parse check counts
        if checks_text:
            try:
                check_parts = [int(c.strip()) for c in checks_text.split(",")]
            except ValueError:
                check_parts = [1] * len(new_text)
        else:
            check_parts = [1] * len(new_text)

        # Pad lists to match new_text length
        while len(ruby_parts) < len(new_text):
            ruby_parts.append("")
        while len(check_parts) < len(new_text):
            check_parts.append(1)

        # Preserve is_sentence_end from old last char
        old_chars = self._sentence.characters[self._start_idx : self._end_idx + 1]
        old_last_is_sentence_end = old_chars[-1].is_sentence_end if old_chars else False
        old_last_is_line_end = old_chars[-1].is_line_end if old_chars else False

        # Inherit singer_id from first old char
        singer_id = old_chars[0].singer_id if old_chars else ""

        # Build new Character objects
        new_chars = []
        for i, ch_str in enumerate(new_text):
            ruby_obj = Ruby(text=ruby_parts[i]) if ruby_parts[i] else None
            new_ch = Character(
                char=ch_str,
                ruby=ruby_obj,
                check_count=max(0, check_parts[i]),
                singer_id=singer_id,
                linked_to_next=False,
                is_line_end=False,
                is_sentence_end=False,
            )
            new_chars.append(new_ch)

        # Transfer sentence_end / line_end to new last char
        if old_last_is_sentence_end:
            new_chars[-1].is_sentence_end = True
        if old_last_is_line_end:
            new_chars[-1].is_line_end = True

        # Replace in sentence.characters list
        self._sentence.characters[self._start_idx : self._end_idx + 1] = new_chars

        # Register to dictionary if checked
        if self.chk_register.isChecked():
            self._register_to_dictionary(new_text, ruby_parts)

        self._modified = True
        self.accept()

    def _register_to_dictionary(self, word: str, ruby_parts: list):
        """Register word to user dictionary (dedup + top-insert)."""
        try:
            from strange_uta_game.frontend.settings.settings_interface import (
                AppSettings,
            )

            reading = ",".join(r for r in ruby_parts if r)
            AppSettings().register_dictionary_word(word, reading)
        except Exception:
            pass

    def was_modified(self) -> bool:
        return self._modified


class InsertGuideSymbolDialog(QDialog):
    """插入导唱符对话框 — 在选中字符前插入导唱用字符"""

    def __init__(self, sentence, char_idx, parent=None):
        """
        Args:
            sentence: Sentence object
            char_idx: current selected char index (guide symbols insert BEFORE this)
            parent: parent widget
        """
        super().__init__(parent)
        self._sentence = sentence
        self._char_idx = char_idx
        self._modified = False

        self.setWindowTitle("插入导唱符")
        self.resize(400, 280)
        self.setFont(QFont("Microsoft YaHei", 10))

        layout = QVBoxLayout(self)

        form = QFormLayout()

        # Field 1: Current selected char (readonly)
        ch = sentence.characters[char_idx]
        lbl_current = QLabel(ch.char)
        lbl_current.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("当前选中字符:", lbl_current)

        # Field 2: Guide symbol text
        self.edit_symbol = QLineEdit("")
        self.edit_symbol.setPlaceholderText("请填写要插入的导唱符")
        form.addRow("导唱符:", self.edit_symbol)

        # Field 3: Count
        self.edit_count = QLineEdit("1")
        self.edit_count.setPlaceholderText("个数")
        form.addRow("个数:", self.edit_count)

        # Field 4: Duration per symbol
        self.edit_duration = QLineEdit("1000")
        self.edit_duration.setPlaceholderText("每个导唱符持续时间（毫秒）")
        form.addRow("持续时间 (ms):", self.edit_duration)

        layout.addLayout(form)
        layout.addStretch()

        # Buttons
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_exec = PrimaryPushButton("执行", self)
        btn_exec.clicked.connect(self._on_execute)
        btn_layout.addWidget(btn_exec)
        btn_close = PushButton("关闭", self)
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def _on_execute(self):
        from strange_uta_game.backend.domain.models import Character

        symbol = self.edit_symbol.text().strip()
        if not symbol:
            return

        try:
            count = max(1, int(self.edit_count.text().strip()))
        except ValueError:
            count = 1

        try:
            duration_ms = max(100, int(self.edit_duration.text().strip()))
        except ValueError:
            duration_ms = 1000

        # Get reference char's timestamp and singer
        ref_char = self._sentence.characters[self._char_idx]
        singer_id = ref_char.singer_id

        # Get reference timestamp (first timestamp of selected char)
        ref_ts = ref_char.timestamps[0] if ref_char.timestamps else None

        # Build guide characters
        # Each guide symbol has linked_to_next=True (they chain), except last
        # Actually: if symbol is multi-char, each char of the symbol is linked.
        # If count > 1, each "symbol group" is also linked.
        # Result: all guide chars are linked_to_next=True (chained as one word)
        guide_chars = []
        for i in range(count):
            for j, ch_str in enumerate(symbol):
                is_last_of_symbol = j == len(symbol) - 1
                is_last_symbol = i == count - 1
                is_last_char = is_last_of_symbol and is_last_symbol
                new_ch = Character(
                    char=ch_str,
                    ruby=None,
                    check_count=1 if is_last_of_symbol else 0,
                    singer_id=singer_id,
                    linked_to_next=not is_last_char,
                )
                # Set timestamp if reference exists
                if ref_ts is not None and is_last_of_symbol:
                    # For i-th symbol (0-indexed), timestamp = ref_ts - duration_ms * (count - i)
                    ts = ref_ts - duration_ms * (count - i)
                    if ts >= 0:
                        new_ch.add_timestamp(ts)
                guide_chars.append(new_ch)

        # Insert guide chars BEFORE the selected char
        for idx, gc in enumerate(guide_chars):
            self._sentence.characters.insert(self._char_idx + idx, gc)

        self._modified = True
        self.accept()

    def was_modified(self) -> bool:
        return self._modified


class CharEditDialog(QDialog):
    """注音编辑对话框 — 支持连词（Ruby 合并/拆分）

    连词功能：用 + 号将相邻字符合并显示。
    在 per-char Ruby 模型中，每个字符独立拥有自己的 Ruby 对象。
    连词由 Character.linked_to_next 标记控制。

    UI 布局：
    - 当前字符显示（只读）
    - 注音文本编辑
    - 确定/取消按钮
    """

    def __init__(self, sentence: "Sentence", char_idx: int, parent=None):
        super().__init__(parent)
        self._sentence = sentence
        self._char_idx = char_idx
        self._modified = False

        self.setWindowTitle("编辑注音")
        self.resize(360, 220)
        self.setFont(QFont("Microsoft YaHei", 10))

        form = QFormLayout(self)

        # 当前字符（只读）— 显示连词组内所有字符
        ch = sentence.characters[char_idx]
        # 查找连词组范围
        word_start, word_end = sentence.get_word_char_range(char_idx)
        if word_end - word_start > 1:
            display = " + ".join(
                sentence.characters[i].char for i in range(word_start, word_end)
            )
        else:
            display = ch.char

        lbl_char = QLabel(display)
        lbl_char.setStyleSheet("font-size: 16px; font-weight: bold;")
        form.addRow("字符:", lbl_char)

        # 注音编辑 — 如果是连词组，显示逗号分隔的各字符 ruby
        if word_end - word_start > 1:
            parts = []
            for i in range(word_start, word_end):
                r = sentence.characters[i].ruby
                parts.append(r.text if r else "")
            initial_ruby = ",".join(parts) if any(parts) else ""
        else:
            initial_ruby = ch.ruby.text if ch.ruby else ""

        self.edit_ruby = QLineEdit(initial_ruby)
        self.edit_ruby.setPlaceholderText("输入注音（留空则删除注音）")
        form.addRow("注音:", self.edit_ruby)

        # 连词范围提示
        if word_end - word_start > 1:
            range_text = " + ".join(
                sentence.characters[i].char for i in range(word_start, word_end)
            )
            lbl_range = QLabel(f"当前连词范围: {range_text}（逗号分隔各字符注音）")
            lbl_range.setStyleSheet("font-size: 11px; color: gray;")
            form.addRow("", lbl_range)

        self._word_start = word_start
        self._word_end = word_end

        # 按钮
        btn_layout = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_ok)
        btn_layout.addWidget(btn_cancel)
        form.addRow(btn_layout)

    def _on_accept(self):
        new_ruby_text = self.edit_ruby.text().strip()
        word_len = self._word_end - self._word_start

        if not new_ruby_text:
            # 清空连词组内所有字符的 ruby
            for i in range(self._word_start, self._word_end):
                if self._sentence.characters[i].ruby:
                    self._sentence.characters[i].set_ruby(None)
                    self._modified = True
            self.accept()
            return

        if word_len > 1 and "," in new_ruby_text:
            # 连词组：按逗号分隔赋给各字符
            parts = [p.strip() for p in new_ruby_text.split(",")]
            # 如果 parts 数量不足，用空字符串补齐
            while len(parts) < word_len:
                parts.append("")
            for i, part in enumerate(parts[:word_len]):
                ci = self._word_start + i
                if part:
                    self._sentence.characters[ci].set_ruby(Ruby(text=part))
                else:
                    self._sentence.characters[ci].set_ruby(None)
                self._modified = True
        elif word_len > 1:
            # 连词组但无逗号：按 mora 均分
            from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                split_ruby_for_checkpoints,
            )

            split_parts = split_ruby_for_checkpoints(new_ruby_text, word_len)
            for i, part in enumerate(split_parts):
                ci = self._word_start + i
                if part:
                    self._sentence.characters[ci].set_ruby(Ruby(text=part))
                else:
                    self._sentence.characters[ci].set_ruby(None)
                self._modified = True
        else:
            # 单字符
            self._sentence.characters[self._char_idx].set_ruby(Ruby(text=new_ruby_text))
            self._modified = True

        self.accept()

    def was_modified(self) -> bool:
        return self._modified


# ──────────────────────────────────────────────
# 时间轴
# ──────────────────────────────────────────────
class TimelineWidget(QWidget):
    """时间轴 - 显示时间网格 + 时间标签 + 播放位置"""

    seek_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._duration_ms = 0
        self._current_ms = 0
        self._time_tags: List[int] = []
        self.setMinimumHeight(80)
        self.setMaximumHeight(120)

    def set_duration(self, ms: int):
        self._duration_ms = ms
        self.update()

    def set_position(self, ms: int):
        self._current_ms = ms
        self.update()

    def set_time_tags(self, tags_ms: List[int]):
        self._time_tags = sorted(tags_ms)
        self.update()

    def paintEvent(self, a0: Optional[QPaintEvent]):
        _ = a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        painter.fillRect(self.rect(), QColor("#F0F0F0"))

        if self._duration_ms <= 0:
            painter.setPen(QColor("#999"))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "请加载音频文件"
            )
            return

        # 时间网格（10 秒间隔）
        painter.setPen(QPen(QColor("#DDD"), 1))
        for t in range(0, self._duration_ms + 1, 10000):
            x = int((t / self._duration_ms) * w)
            painter.drawLine(x, 0, x, h)

        # 时间标签标记
        painter.setPen(QPen(QColor("#FF6B6B"), 2))
        for tag in self._time_tags:
            x = int((tag / self._duration_ms) * w)
            painter.drawLine(x, int(h * 0.3), x, int(h * 0.7))

        # 播放位置
        if 0 <= self._current_ms <= self._duration_ms:
            x = int((self._current_ms / self._duration_ms) * w)
            painter.setPen(QPen(QColor("#4ECDC4"), 2))
            painter.drawLine(x, 0, x, h)
            painter.setBrush(QBrush(QColor("#4ECDC4")))
            painter.drawEllipse(x - 4, h // 2 - 4, 8, 8)

    def mousePressEvent(self, a0: Optional[QMouseEvent]):
        if a0 is None:
            return
        if self._duration_ms <= 0:
            return
        ratio = max(0.0, min(1.0, a0.position().x() / self.width()))
        self.seek_requested.emit(int(ratio * self._duration_ms))


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
    _line_end_started_signal = pyqtSignal(int, int, int)
    _line_end_finished_signal = pyqtSignal(int, int, int, int)

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
        self._line_end_started_signal.connect(self._handle_line_end_recording_started)
        self._line_end_finished_signal.connect(self._handle_line_end_recording_finished)

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
            from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
                LyricParserFactory,
                LRCParser,
                parse_to_sentences,
            )
            from strange_uta_game.backend.infrastructure.parsers.inline_format import (
                sentences_from_inline_text,
            )
            import re

            content = Path(path).read_text(encoding="utf-8")
            default_singer = self._project.get_default_singer()

            # 检测内联格式
            if bool(re.search(r"\[\d+\|\d{2}:\d{2}:\d{2}\]", content)):
                sentences = sentences_from_inline_text(content, default_singer.id)
            else:
                parsed_lines = LyricParserFactory.parse_file(path)
                ext = Path(path).suffix.lower()
                if ext == ".txt" and bool(
                    re.search(r"\[\d{1,2}:\d{2}[.:]\d{2,3}\]", content)
                ):
                    lrc_parser = LRCParser()
                    parsed_lines = lrc_parser.parse(content)
                sentences = parse_to_sentences(parsed_lines, default_singer.id)

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
                from strange_uta_game.backend.infrastructure.persistence.sug_parser import (
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
        from strange_uta_game.frontend.editor.bulk_change_dialog import BulkChangeDialog

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
                    # 回退到连词逻辑
                    start = char_idx
                    while (
                        start > 0
                        and start - 1 < len(chars)
                        and chars[start - 1].linked_to_next
                    ):
                        start -= 1
                    end = char_idx
                    while (
                        end < len(text)
                        and end < len(chars)
                        and chars[end].linked_to_next
                    ):
                        end += 1
                    end += 1  # exclusive
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

        dialog = ModifyCharacterDialog(sentence, start_idx, end_idx, self)
        dialog.exec()

        if dialog.was_modified():
            # Rebuild global checkpoints
            if self._timing_service:
                self._timing_service._rebuild_global_checkpoints()
            self.refresh_lyric_display()
            self._update_time_tags_display()
            self._update_status()
            if hasattr(self, "_store") and self._store:
                self._store.notify("lyrics")

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
                self._timing_service._rebuild_global_checkpoints()
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
        self._current_line_idx = line_idx
        self.preview.set_current_position(line_idx, char_idx)

        # 判断当前字符是否有 checkpoint
        no_checkpoint = True
        if self._project and line_idx < len(self._project.sentences):
            sentence = self._project.sentences[line_idx]
            if char_idx < len(sentence.characters):
                ch = sentence.characters[char_idx]
                no_checkpoint = ch.check_count == 0 and not ch.is_sentence_end

        # 单击即移动 checkpoint 目标到选中字符
        if self._timing_service:
            self._timing_service.move_to_checkpoint(line_idx, char_idx, 0)

        # 无 checkpoint 时恢复视觉焦点到被点击的字符
        if no_checkpoint:
            self.preview.set_current_position(line_idx, char_idx)

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
                self._store.notify("lyrics")

    def _add_checkpoint(self):
        """F4 增加当前字符节奏点 (+1)。"""
        self._change_checkpoint(delta=1)

    def _remove_checkpoint(self):
        """F5 删除当前字符节奏点 (-1)，最小为 0。"""
        self._change_checkpoint(delta=-1)

    def _adjust_current_timestamp(self, delta_ms: int):
        """Alt+↑/↓ 微调当前选中 checkpoint 的时间戳。

        #3：调整对象从"当前字符"改为"当前选中的 checkpoint"（以
        TimingService.get_current_position().checkpoint_idx 为准）。
        #4：步长由 timing.timing_adjust_step_ms 设置项决定（调用方传入）。
        """
        if not self._project or not self._timing_service:
            return
        pos = self._timing_service.get_current_position()
        line_idx = pos.line_idx
        char_idx = pos.char_idx
        cp_idx = pos.checkpoint_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.characters):
            return
        ch = sentence.characters[char_idx]
        # 判断是句尾 checkpoint 还是普通 checkpoint
        if ch.is_sentence_end and cp_idx == ch.check_count:
            if ch.sentence_end_ts is None:
                return
            ch.set_sentence_end_ts(max(0, ch.sentence_end_ts + delta_ms))
        else:
            if cp_idx >= len(ch.timestamps):
                return
            new_ts = max(0, ch.timestamps[cp_idx] + delta_ms)
            ch.timestamps[cp_idx] = new_ts
            ch.push_to_ruby()
        self._update_time_tags_display()
        self.refresh_lyric_display()
        self._update_line_info()
        if hasattr(self, "_store") and self._store:
            self._store.notify("timetags")

    def _cycle_current_checkpoint(self):
        """#2：Tab 键循环切换当前字符的 checkpoint 索引。

        以 TimingService.get_current_position() 为起点，将 checkpoint_idx
        推进到下一个（到尾后回到 0）。句尾字符若带 is_sentence_end，则
        句尾 checkpoint 也在循环序列内（位置为 check_count）。
        """
        if not self._project or not self._timing_service:
            return
        pos = self._timing_service.get_current_position()
        line_idx = pos.line_idx
        char_idx = pos.char_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        if char_idx >= len(sentence.characters):
            return
        ch = sentence.characters[char_idx]
        total = ch.check_count + (1 if ch.is_sentence_end else 0)
        if total <= 0:
            return
        next_idx = (pos.checkpoint_idx + 1) % total
        self._timing_service.move_to_checkpoint(line_idx, char_idx, next_idx)
        self._update_line_info()
        self.refresh_lyric_display()

    def _rebuild_checkpoints(self):
        if self._timing_service:
            if hasattr(self._timing_service, "rebuild_global_checkpoints"):
                self._timing_service.rebuild_global_checkpoints()
            else:
                self._timing_service._rebuild_global_checkpoints()

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

        self.preview._current_checkpoint_idx = checkpoint_idx
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
            command_manager = getattr(self._timing_service, "_command_manager", None)
        if command_manager is not None:
            command = _SentenceSnapshotCommand(
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
        """增加或减少当前字符的节奏点数量。"""
        if not self._project:
            return
        line_idx = self._current_line_idx
        if line_idx >= len(self._project.sentences):
            return
        sentence = self._project.sentences[line_idx]
        char_idx = self.preview._current_char_idx
        if char_idx >= len(sentence.characters):
            return

        def _mutate():
            if delta > 0:
                sentence.add_checkpoint(char_idx)
            else:
                sentence.remove_checkpoint(char_idx)
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
            self._timing_service._rebuild_global_checkpoints()
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
        """方向键导航：上一行 (delta=-1) 或下一行 (delta=+1)"""
        if not self._project:
            return
        new_idx = self._current_line_idx + delta
        if new_idx < 0 or new_idx >= len(self._project.sentences):
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

        self._execute_structural_edit(
            "插入空行",
            lambda: ((project.insert_blank_line(line_idx), 0, None, "lyrics")),
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

        self._execute_structural_edit(
            "减少节奏点",
            lambda: (
                project.sentences[line_idx].remove_checkpoint(char_idx)
                or (line_idx, char_idx, 0, "checkpoints")
            ),
        )

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

    def on_line_end_recording_started(
        self, line_idx: int, char_idx: int, start_time_ms: int
    ) -> None:
        self._line_end_started_signal.emit(line_idx, char_idx, start_time_ms)

    def on_line_end_recording_finished(
        self, line_idx: int, char_idx: int, start_time_ms: int, end_time_ms: int
    ) -> None:
        self._line_end_finished_signal.emit(
            line_idx, char_idx, start_time_ms, end_time_ms
        )

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
            self.transport.set_playing(self._timing_service.is_playing())

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

    def _handle_line_end_recording_started(
        self, line_idx: int, char_idx: int, start_time_ms: int
    ):
        _ = line_idx, char_idx, start_time_ms
        self.lbl_status.setText("句尾长按录制中")

    def _handle_line_end_recording_finished(
        self, line_idx: int, char_idx: int, start_time_ms: int, end_time_ms: int
    ):
        _ = line_idx, char_idx, start_time_ms, end_time_ms
        self.lbl_status.setText("句尾长按录制完成")

    # ==================== 辅助 ====================

    def _apply_checkpoint_position(self, position: CheckpointPosition):
        if not self._project or not self._project.sentences:
            self._current_line_idx = 0
            self.preview._current_checkpoint_idx = None
            self._update_line_info()
            return

        line_idx = max(0, min(position.line_idx, len(self._project.sentences) - 1))
        self._current_line_idx = line_idx
        self.preview._current_checkpoint_idx = position.checkpoint_idx
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
        tags_ms = []
        for sentence in self._project.sentences:
            for ch in sentence.characters:
                for ts in ch.all_timestamps:
                    tags_ms.append(ts)
        self.timeline.set_time_tags(tags_ms)

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
