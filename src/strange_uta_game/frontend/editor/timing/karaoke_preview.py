"""卡拉OK 歌词预览控件。

- 实时走字高亮 / Ruby 注音同步 / 连词平滑渲染
- 支持鼠标拖拽划词选区 + 右键菜单
- 渲染状态与 ``EditorInterface`` 通过信号耦合
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
)
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import Action, RoundMenu

from strange_uta_game.backend.domain import Character, Project, Ruby


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
        # 播放期间使用缓存；暂停时 paintEvent 旁路缓存直接重算，
        # 以便打轴/改连词等编辑动作立即反映到 wipe 时间线
        self._sentence_cache: dict = {}
        self._cache_version: int = 0
        self._is_playing: bool = False

    def set_playing(self, playing: bool):
        """由外部同步播放状态，用于决定 paintEvent 是否旁路缓存。"""
        self._is_playing = bool(playing)

    def set_project(self, project: Project):
        self._project = project
        self._scroll_center_line = 0.0
        self._sentence_cache.clear()
        self._update_display()

    def set_current_position(self, line_idx: int, char_idx: int = 0):
        # #9: 幂等保护 —— 多路 caller（UI 点击 + timing_service 回调）可能
        # 在同一帧内相继调用本方法，若目标 line_idx 已是当前值，仅更新 char_idx
        # 并触发重绘即可，避免短暂的 scroll_center 跳变导致空白行。
        new_line = float(line_idx)
        if new_line == self._scroll_center_line and line_idx == self._current_line_idx:
            self._current_char_idx = char_idx
            self._update_display()
            return
        self._current_line_idx = line_idx
        self._current_char_idx = char_idx
        # 自动跟随：当前行始终居中（强制整数，避免其他 float 路径污染）
        self._scroll_center_line = new_line
        # 行切换时重新锚定预热中心（仅播放期间）
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self._update_display()

    def set_current_time_ms(self, time_ms: int):
        self._current_time_ms = time_ms
        # 播放期间按就近扩散顺序预热少量邻近行，降低视口内首帧卡顿
        if self._is_playing:
            self._warm_nearby_cache(budget=2)
        self.update()

    def _warm_nearby_cache(self, budget: int = 2) -> None:
        """按 L, L+1, L-1, L+2, L-2, ... 的就近扩散顺序预热 _sentence_cache。

        - 仅在播放期间调用（暂停已旁路缓存，预热无意义）
        - 每次最多预热 budget 句，避免阻塞 paint
        - 已缓存/版本匹配的行直接跳过，不重复计算
        - 行号边界：center 被 clamp 到 [0, n-1]，扩散候选再次越界检查；
          两端都越界后早退，避免无意义空转
        """
        if not self._project or not self._project.sentences:
            return
        sentences = self._project.sentences
        n = len(sentences)
        if n <= 0:
            return
        center = max(0, min(self._current_line_idx, n - 1))
        warmed = 0
        for offset in range(n):
            # 扩散序：0, +1, -1, +2, -2, +3, -3, ...
            if offset == 0:
                candidates = (center,)
            else:
                candidates = (center + offset, center - offset)
            any_valid = False
            for idx in candidates:
                if idx < 0 or idx >= n:
                    continue
                any_valid = True
                entry = self._sentence_cache.get(idx)
                is_current_line = idx == self._current_line_idx
                fk = "cur" if is_current_line else "ctx"
                if entry and entry["v"] == self._cache_version and entry["fk"] == fk:
                    continue
                main_fm = self._fm_current if is_current_line else self._fm_context
                self._get_sentence_render_data(idx, sentences[idx], main_fm, fk)
                warmed += 1
                if warmed >= budget:
                    return
            # 两端都越界（向前到 0、向后到 n-1 之外）→ 无需继续扩散
            if offset > 0 and not any_valid:
                return

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
        """返回逐句渲染数据。

        渲染模型：
          - wipe 时间线的单位 = 一整行（sentence），与打轴的连词组解耦
          - 行内所有带 render_timestamps 的字符 + 行尾（is_sentence_end +
            render_sentence_end_ts）构成"锚点"
          - 相邻锚点之间的所有字符（含无时间戳的）按字符像素宽度加权线性插值
            分配 wipe 区间——解决等字符数分配导致的宽度/节奏不匹配跳变
          - 首锚之前 / 末锚之后的字符贴首/末锚（wipe 恒 0 或 1）
          - linked_to_next 只影响视觉渲染层（连词不拆字画），不参与 wipe 计算

        缓存策略：
          - 播放期间命中 (_cache_version, font_key) 匹配的缓存
          - 暂停时 paintEvent 旁路缓存（_is_playing==False 时直接重算并跳过写入）
        """
        use_cache = self._is_playing
        if use_cache:
            entry = self._sentence_cache.get(idx)
            if entry and entry["v"] == self._cache_version and entry["fk"] == font_key:
                return entry

        chars = sentence.chars
        characters = sentence.characters
        n_chars = len(chars)

        # 字符像素宽度
        char_widths = [main_fm.horizontalAdvance(ch) for ch in chars]

        # ---------- 连词组（仅用于视觉层，与 wipe 计算无关） ----------
        char_groups: list = []
        cur_grp: Optional[list] = None
        for ci in range(n_chars):
            if cur_grp is None:
                cur_grp = [ci]
                char_groups.append(cur_grp)
            elif ci > 0 and characters[ci - 1].linked_to_next:
                cur_grp.append(ci)
            else:
                cur_grp = [ci]
                char_groups.append(cur_grp)

        linked_leader_groups: dict = {}
        linked_non_leader: set = set()
        for group in char_groups:
            if len(group) > 1:
                linked_leader_groups[group[0]] = group
                for _ci in group[1:]:
                    linked_non_leader.add(_ci)

        # ---------- wipe 时间线（一行为单位，按像素宽度插值） ----------
        # 锚点列表：[(char_idx, timestamp_ms)]，char_idx == n_chars 表示行尾锚
        anchors: list[tuple[int, int]] = []
        for ci, ch in enumerate(characters):
            if ch.render_timestamps:
                anchors.append((ci, int(ch.render_timestamps[0])))

        # 行尾锚：使用最后一个带 is_sentence_end 的字符的 render_sentence_end_ts
        # 锚定到该字符的右沿位置 (sentence_end_char_idx + 1)，与普通 cp 锚 (ci, ts)
        # （字符左沿）保持对称——普通 cp 是字符开始，sentence_end 是字符结束。
        # 若 sentence_end 不在最后一字，wipe 进度才能正确停在该字右沿，而非延伸到行末。
        end_ts: Optional[int] = None
        end_anchor_pos: int = n_chars
        for ci in range(n_chars - 1, -1, -1):
            ch = characters[ci]
            if ch.is_sentence_end and ch.render_sentence_end_ts is not None:
                end_ts = int(ch.render_sentence_end_ts)
                end_anchor_pos = ci + 1
                break
        if end_ts is not None:
            anchors.append((end_anchor_pos, end_ts))

        char_wipe_times: dict = {}
        if anchors:
            # 累积像素宽度前缀和（char 左边界），长度 n_chars+1
            # cum_w[i] = sum(char_widths[:i])；char i 的像素区间 [cum_w[i], cum_w[i+1]]
            cum_w = [0]
            for w in char_widths:
                cum_w.append(cum_w[-1] + w)

            # 相邻锚点之间按像素宽度加权插值
            # 对每个 char i，取其像素中点 mid_i = (cum_w[i] + cum_w[i+1]) / 2
            # 在覆盖该 mid 的锚区间 [a_pos, b_pos] 上按比例映射时间
            # wipe 区间 = [左边界映射时间, 右边界映射时间]
            for seg_idx in range(len(anchors) - 1):
                a_ci, a_ts = anchors[seg_idx]
                b_ci, b_ts = anchors[seg_idx + 1]
                a_pos = cum_w[a_ci] if a_ci <= n_chars else cum_w[n_chars]
                b_pos = cum_w[b_ci] if b_ci <= n_chars else cum_w[n_chars]
                span_pos = b_pos - a_pos
                span_ts = b_ts - a_ts
                if span_pos <= 0:
                    # 锚点像素位置重合（极端情况）——退化为等分
                    seg_chars = list(range(a_ci, min(b_ci, n_chars)))
                    n_seg = len(seg_chars)
                    if n_seg == 0:
                        continue
                    for k, ci in enumerate(seg_chars):
                        char_wipe_times[ci] = (
                            a_ts + int(span_ts * k / n_seg),
                            a_ts + int(span_ts * (k + 1) / n_seg),
                        )
                    continue
                for ci in range(a_ci, min(b_ci, n_chars)):
                    left_pos = cum_w[ci]
                    right_pos = cum_w[ci + 1]
                    t_start = a_ts + span_ts * (left_pos - a_pos) / span_pos
                    t_end = a_ts + span_ts * (right_pos - a_pos) / span_pos
                    char_wipe_times[ci] = (int(t_start), int(t_end))

            # 首锚之前：贴首锚（wipe 立即填满）
            first_ci, first_ts = anchors[0]
            for ci in range(0, first_ci):
                char_wipe_times[ci] = (first_ts, first_ts)

            # 末锚之后：贴末锚（wipe 恒为 0）
            last_ci, last_ts = anchors[-1]
            for ci in range(max(last_ci, 0), n_chars):
                if ci not in char_wipe_times:
                    char_wipe_times[ci] = (last_ts, last_ts)

        entry = {
            "v": self._cache_version,
            "fk": font_key,
            "char_widths": char_widths,
            "total_text_width": sum(char_widths),
            "char_wipe_times": char_wipe_times,
            "linked_leader_groups": linked_leader_groups,
            "linked_non_leader": linked_non_leader,
        }
        if use_cache:
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
            y_center = int(round(y_center_f))

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
            _char_singer_colors: dict = {}  # char_idx -> QColor (基色)
            _char_complement_colors: dict = {}  # char_idx -> QColor (选中高亮色 = 演唱者补色)
            default_singer = self._project.get_default_singer()
            for ci, char in enumerate(line.characters):
                singer_obj = self._project.get_singer(char.singer_id)
                singer_color = singer_obj.color if singer_obj and singer_obj.color else default_singer.color
                comp_color = (
                    singer_obj.complement_color
                    if singer_obj and singer_obj.complement_color
                    else default_singer.complement_color or singer_color
                )
                _char_singer_colors[ci] = QColor(singer_color)
                _char_complement_colors[ci] = QColor(comp_color)

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

                # 统一高亮/hitbox 矩形：以行逻辑中心 y_center_f 为唯一锚点、
                # 高度 clamp 到 int(line_height)。此前 _rect_top 在「行框顶」
                # 与「字体 ascent 顶」之间取 max()，在当前行字体放大（22pt）
                # 相邻行字体缩小（18pt）时两套锚点不一致，会让选中行下方出现
                # 大块空白（issue #9）。现统一以行中心垂直居中矩形，消除跳变。
                _rect_height = min(main_fm.height() + 4, int(line_height))
                _rect_top = int(round(y_center_f - _rect_height / 2))

                # 当前打轴位置高亮背景
                if is_current and char_pos == self._current_char_idx:
                    highlight_bg = QColor("#FFE0E0")
                    bg_rect = QRect(
                        int(curr_x) - 1,
                        _rect_top,
                        int(char_w) + 2,
                        _rect_height,
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
                            _rect_top,
                            int(char_w) + 2,
                            _rect_height,
                        )
                        painter.fillRect(sel_rect, sel_bg)

                # 存储字符 hitbox 用于点击检测（与高亮矩形对齐）
                char_rect = QRect(
                    int(curr_x),
                    _rect_top,
                    int(char_w),
                    _rect_height,
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
                        # 单字符 ruby（per-char 模型）- 渲染剥离 '#' 分组标记
                        _ruby_disp = ruby.text
                        ruby_text_w = fm_ruby.horizontalAdvance(_ruby_disp)
                        ruby_x = curr_x + (char_w - ruby_text_w) // 2
                        ruby_y = int(y_center - main_fm.ascent() - 4)
                        painter.setFont(font_ruby)
                        painter.setPen(base_color)
                        painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
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
                                painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
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
                                    painter.drawText(int(ruby_x), ruby_y, _ruby_disp)
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
                        # Issue #Q1：CP marker 的"已打轴"判定与 wipe 渲染保持
                        # 同源——使用 render_* 时间戳字段。否则当存在 render
                        # offset 时句尾标记会与走字进度不同步。
                        has_timed = (
                            ch_obj.render_sentence_end_ts is not None
                            if is_sentence_end_marker
                            else cp_idx < len(ch_obj.render_timestamps)
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
                        # Issue #9 第十六批架构性修复：单管道渲染，不再有"选中分支"。
                        # 选中态由 Character.selected_checkpoint_idx 承载；渲染时
                        # 选中 cp 直接用演唱者补色（持久化于 Singer.complement_color），
                        # 未选中用基色。这样所有 cp 都走同一条 setPen+drawText 路径，
                        # 从源头消除"选中后大白框"——因为不再有任何路径上的第二次
                        # drawText、setClipRect、BGMode 切换等副作用。
                        is_selected = (
                            ch_obj.selected_checkpoint_idx == cp_idx
                        )
                        if not has_timed:
                            color = QColor("#ccc")
                        elif is_selected:
                            color = _char_complement_colors.get(
                                char_pos, _char_singer_colors.get(char_pos, highlight_color)
                            )
                        else:
                            color = _char_singer_colors.get(char_pos, highlight_color)

                        mw = fm_checkpoint.horizontalAdvance(marker_char)

                        painter.setPen(color)
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
