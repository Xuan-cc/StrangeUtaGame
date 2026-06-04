from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDialog,
    QHeaderView,
    QTableWidgetItem,
    QAbstractItemView,
    QMessageBox,
)
from PyQt6.QtCore import Qt, QEvent
from PyQt6.QtGui import QFont, QColor, QKeyEvent, QKeySequence
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    TableWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    CaptionLabel,
)
from typing import Optional, List
from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
)
from strange_uta_game.backend.domain.models import RubyPart
from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
    parse_timed_line,
)
from strange_uta_game.frontend.theme import theme
import re


# ─────────────────────────────────────────────────────────────────────────
# 行(连词组) ↔ 6 列单元格 的「扁平」互转
#
# 模型：一行 = 一段扁平 checkpoint 序列。
#   - Checkpoint 列：每字符一个计数，逗号分隔（如 "2,2,1"）；累加 = 全行 cp 总数 K
#   - 注音 列：每个 RubyPart(mora) 一段，逗号分隔，全行展平（共 K 段）；空段=无注音
#   - 时间标签 列：每个 checkpoint 一段时间戳，逗号分隔，按序对齐到 K 个 cp；
#     允许空段（缺省/未打轴）；句尾释放点附在第 K 段之后
#   - 演唱者 列：每字符一个名字，逗号分隔
# 写回：把各列重新拼成项目「带时间戳行内格式」(annotated_text)，交由
#   parse_timed_line 解析为 Character —— 绝不在前端重切 mora。
# ─────────────────────────────────────────────────────────────────────────

# 会破坏行内块串结构的元字符；字符/注音含这些时退回直接构造，避免解析串损坏。
_DANGER_CHARS = set("{}|,[]>")


def _row_cells_from_chars(
    chars: List[Character], singer_map: dict
) -> tuple[str, str, str, str, str, str]:
    """一行的 Character 列表 → 6 列单元格文本（与 _build_row_chars 互逆）。"""
    char_str = "".join(c.char for c in chars)
    cp_str = ",".join(str(c.check_count) for c in chars)

    # 注音：每 cp 一段，全行展平；整行无注音则留空
    has_ruby = any(c.ruby for c in chars)
    ruby_segs: List[str] = []
    for c in chars:
        parts = c.ruby.parts if c.ruby else []
        for j in range(c.check_count):
            ruby_segs.append(parts[j].text if j < len(parts) else "")
    ruby_str = ",".join(ruby_segs) if has_ruby else ""

    # 时间标签：每 cp 一段全局时间戳 + 句尾释放点；整行无时间戳则留空
    time_segs: List[str] = []
    any_ts = False
    for c in chars:
        for j in range(c.check_count):
            if j < len(c.global_timestamps):
                time_segs.append(_fmt_time(c.global_timestamps[j]))
                any_ts = True
            else:
                time_segs.append("")
    last = chars[-1]
    if last.is_sentence_end:
        if last.global_sentence_end_ts is not None:
            time_segs.append(_fmt_time(last.global_sentence_end_ts))
            any_ts = True
        else:
            time_segs.append("")
    time_str = ",".join(time_segs) if any_ts else ""

    end_str = "是" if last.is_sentence_end else ""
    singer_str = ",".join(
        (singer_map.get(c.singer_id, "") if c.singer_id else "") for c in chars
    )
    return char_str, ruby_str, cp_str, end_str, time_str, singer_str


def _row_to_block_str(
    glyphs: List[str],
    mora_flat: List[str],
    ts_flat_global: List[Optional[int]],
    check_counts: List[int],
    is_sentence_end: bool,
) -> str:
    """把一行扁平列数据拼成 annotated_text 的 ``{原文||...}`` 带时间戳块串。

    每字符按其 check_count 取走对应的 mora / 时间戳段，组成
    ``[ts]mora|[ts]mora`` 段；缺省时间戳用占位 ``[T]``；句尾释放点贴在末字段尾。
    """
    total = sum(check_counts)
    segs: List[str] = []
    offset = 0
    for i, _g in enumerate(glyphs):
        k = check_counts[i]
        slots: List[str] = []
        for j in range(k):
            idx = offset + j
            ts = ts_flat_global[idx] if idx < len(ts_flat_global) else None
            mora = mora_flat[idx] if idx < len(mora_flat) else ""
            tok = f"[{_fmt_time(ts)}]" if ts is not None else "[T]"
            slots.append(tok + mora)
        seg = "|".join(slots)
        if is_sentence_end and i == len(glyphs) - 1:
            rel = ts_flat_global[total] if total < len(ts_flat_global) else None
            seg += f"[>{_fmt_time(rel)}]" if rel is not None else "[>T]"
        segs.append(seg)
        offset += k
    return "{" + "".join(glyphs) + "||" + ",".join(segs) + "}"


def _build_row_chars_direct(
    glyphs: List[str],
    check_counts: List[int],
    mora_flat: List[str],
    ts_flat_global: List[Optional[int]],
    is_sentence_end: bool,
    global_offset: int,
    singer_ids: List[str],
) -> List[Character]:
    """直接构造一行 Character（与 parse_timed_line 等价，用于含元字符的安全回退）。

    时间戳与解析器行为一致：遇到首个缺省(None)即停止（其后 cp 视为未打轴）。
    """
    total = sum(check_counts)
    chars: List[Character] = []
    offset = 0
    for i, g in enumerate(glyphs):
        k = check_counts[i]
        mora_i = mora_flat[offset : offset + k]
        ts_i = ts_flat_global[offset : offset + k]
        timestamps: List[int] = []
        for t in ts_i:
            if t is None:
                break
            timestamps.append(max(0, t - global_offset))
        is_end = is_sentence_end and i == len(glyphs) - 1
        end_ts = None
        if is_end:
            rel = ts_flat_global[total] if total < len(ts_flat_global) else None
            end_ts = max(0, rel - global_offset) if rel is not None else None
        ch = Character(
            char=g,
            check_count=k,
            timestamps=timestamps,
            singer_id=singer_ids[i],
            is_sentence_end=is_end,
            sentence_end_ts=end_ts,
        )
        parts = [RubyPart(text=m) for m in mora_i]
        if any(m for m in mora_i):
            ch.set_ruby(Ruby(parts=parts))
        ch.push_to_ruby()
        chars.append(ch)
        offset += k
    for k in range(len(chars) - 1):
        chars[k].linked_to_next = True
    return chars


def _build_row_chars(
    data: dict, global_offset: int, default_singer_id: str
) -> List[Character]:
    """把一行解析后的扁平数据构造成 Character 列表。

    常规行：拼成带时间戳块串 → parse_timed_line（复用经测试的解析引擎）；
    字符/注音含行内格式元字符时回退到直接构造，避免串损坏。
    时间戳遇到中间空洞即截断（其后时间戳丢弃）——domain 紧凑存储不支持中间空洞。
    """
    glyphs = data["glyphs"]
    mora_flat = data["mora_flat"]
    ts_flat = data["ts_flat"]
    ccs = data["check_counts"]
    is_se = data["is_sentence_end"]
    singer_ids = data["singer_ids"]

    if _row_needs_direct(glyphs, mora_flat):
        chars = _build_row_chars_direct(
            glyphs, ccs, mora_flat, ts_flat, is_se, global_offset, singer_ids
        )
    else:
        block = _row_to_block_str(glyphs, mora_flat, ts_flat, ccs, is_se)
        chars, _ = parse_timed_line(
            block, default_singer_id=default_singer_id, offset_ms=global_offset
        )
        if len(chars) != len(glyphs):
            # 解析结果与预期字符数不符（异常字符），安全回退
            chars = _build_row_chars_direct(
                glyphs, ccs, mora_flat, ts_flat, is_se, global_offset, singer_ids
            )
        else:
            for i, ch in enumerate(chars):
                ch.singer_id = singer_ids[i]

    for ch in chars:
        ch.is_line_end = False  # 真正行尾由 _fix_sentence_character_invariants 设定
        ch.push_to_ruby()
    return chars


def _row_needs_direct(glyphs: List[str], mora_flat: List[str]) -> bool:
    """字符或注音含行内格式元字符时，需走直接构造而非串解析。"""
    if any(g in _DANGER_CHARS for g in glyphs):
        return True
    return any(any(ch in _DANGER_CHARS for ch in m) for m in mora_flat)


def _fmt_time(ms: int) -> str:
    s = ms // 1000
    c = (ms % 1000) // 10
    return f"{s // 60:02d}:{s % 60:02d}.{c:02d}"


def _parse_time(text: str) -> Optional[int]:
    """Parse time string 'MM:SS.cc' to milliseconds. Returns None on failure."""
    text = text.strip()
    if not text:
        return None
    m = re.match(r"^(\d+):(\d{1,2})\.(\d{1,2})$", text)
    if not m:
        return None
    minutes = int(m.group(1))
    seconds = int(m.group(2))
    centis = int(m.group(3))
    if seconds >= 60:
        return None
    return (minutes * 60 + seconds) * 1000 + centis * 10


def _clone_ruby(ruby: Optional[Ruby]) -> Optional[Ruby]:
    if ruby is None:
        return None
    return Ruby(
        parts=[RubyPart(text=p.text, offset_ms=p.offset_ms) for p in ruby.parts],
        timestamps=list(ruby.timestamps),
        singer_id=ruby.singer_id,
    )


def _clone_character(character: Character) -> Character:
    cloned = Character(
        char=character.char,
        ruby=_clone_ruby(character.ruby),
        check_count=character.check_count,
        timestamps=list(character.timestamps),
        sentence_end_ts=character.sentence_end_ts,
        linked_to_next=character.linked_to_next,
        is_line_end=character.is_line_end,
        is_sentence_end=character.is_sentence_end,
        is_rest=character.is_rest,
        singer_id=character.singer_id,
    )
    cloned.push_to_ruby()
    return cloned


def _clone_sentence(sentence: Sentence) -> Sentence:
    return Sentence(
        singer_id=sentence.singer_id,
        characters=[_clone_character(character) for character in sentence.characters],
    )


def _fix_sentence_character_invariants(sentence: Sentence):
    if not sentence.characters:
        return

    last_index = len(sentence.characters) - 1
    for index, character in enumerate(sentence.characters):
        if index == last_index:
            continue
        # 非末尾字符不能是行尾
        if character.is_line_end:
            character.is_line_end = False

    last_character = sentence.characters[-1]
    # 末尾字符始终是行尾
    if not last_character.is_line_end:
        last_character.is_line_end = True
    # 末尾字符不能向后连词
    last_character.linked_to_next = False
    last_character.push_to_ruby()


class LineDetailDialog(QDialog):
    """行详情对话框 - 允许编辑时间标签，连词合并显示，per-char 演唱者"""

    def __init__(self, sentence: Sentence, project=None, parent=None):
        super().__init__(parent)
        self.sentence = sentence
        self._project = project
        self._modified = False
        self._row_groups: List[List[int]] = []  # 每行对应的 char 索引列表
        self._char_clipboard: List[Character] = []

        title_text = self.sentence.text[:30] + (
            "..." if len(self.sentence.text) > 30 else ""
        )
        self.setWindowTitle(f"行详情 - {title_text}")
        self.resize(900, 500)
        self.setFont(QFont("Microsoft YaHei", 10))

        self.vbox = QVBoxLayout(self)

        # 提示
        hint = QLabel(
            "连词合并为一行；除「字符」外各列均用逗号「,」分隔\n"
            "双击可编辑「字符」「注音」「Checkpoint数」「句尾」「时间标签」「演唱者」列\n"
            "Checkpoint数：每字符一项（如 2,2,1），累加为本行节奏点总数 K\n"
            "注音：每个 mora 一段、全行展平，段数须等于 K；留空表示整行无注音，不再自动重切\n"
            "时间标签：每个节奏点一段、按序对齐到 K 个节奏点，允许空段(,,)留空；"
            "句尾释放点写在最后；总数不得超过 K(+句尾1)\n"
            "句尾列填写「是」标记为句尾（独立记录释放时间），留空取消；演唱者每字符一项"
        )
        self.vbox.addWidget(hint)

        # Table
        char_toolbar = QHBoxLayout()
        self.btn_add_char = PushButton("添加字符", self)
        self.btn_add_char.clicked.connect(self._add_character)
        char_toolbar.addWidget(self.btn_add_char)
        self.btn_delete_char = PushButton("删除字符", self)
        self.btn_delete_char.clicked.connect(self._delete_characters)
        char_toolbar.addWidget(self.btn_delete_char)
        self.btn_copy_char = PushButton("复制字符", self)
        self.btn_copy_char.clicked.connect(self._copy_characters)
        char_toolbar.addWidget(self.btn_copy_char)
        self.btn_insert_char = PushButton("插入字符", self)
        self.btn_insert_char.clicked.connect(self._insert_characters)
        char_toolbar.addWidget(self.btn_insert_char)
        char_toolbar.addStretch()
        self.vbox.addLayout(char_toolbar)

        self.table = TableWidget(self)
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(
            ["字符", "注音", "Checkpoint数", "句尾", "时间标签", "演唱者"]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vbox.addWidget(self.table)
        self.table.installEventFilter(self)

        self._populate_table()

        # 保存/关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        self.btn_save = PrimaryPushButton("保存修改", self)
        self.btn_save.clicked.connect(self._on_save)
        btn_layout.addWidget(self.btn_save)
        self.btn_close = PushButton("关闭", self)
        self.btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_close)
        self.vbox.addLayout(btn_layout)

        # Connect cell change
        self.table.cellChanged.connect(self._on_cell_changed)

    def _populate_table(self):
        self.table.blockSignals(True)
        characters = self.sentence.characters

        # 构建连词组
        groups: List[List[int]] = []
        cur_grp: list[int] | None = None
        for i in range(len(characters)):
            if cur_grp is None:
                cur_grp = [i]
                groups.append(cur_grp)
            else:
                if characters[i - 1].linked_to_next:
                    cur_grp.append(i)
                else:
                    cur_grp = [i]
                    groups.append(cur_grp)
        self._row_groups = groups

        # Singer name lookup
        singer_map: dict[str, str] = {}
        if self._project:
            for s in self._project.singers:
                singer_map[s.id] = s.name

        self.table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            chars = [characters[ci] for ci in group]
            cells = _row_cells_from_chars(chars, singer_map)
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))

        self.table.blockSignals(False)

    def _on_cell_changed(self, row: int, col: int):
        if col in (0, 1, 2, 3, 4, 5):
            self._modified = True

    def _selected_detail_rows(self) -> List[int]:
        selection_model = self.table.selectionModel()
        if not selection_model:
            return []
        return sorted(index.row() for index in selection_model.selectedRows())

    def _selected_character_indices(self) -> List[int]:
        char_indices: List[int] = []
        for row in self._selected_detail_rows():
            if 0 <= row < len(self._row_groups):
                char_indices.extend(self._row_groups[row])
        return char_indices

    def _clone_selected_characters(self) -> List[Character]:
        return [
            _clone_character(self.sentence.characters[index])
            for index in self._selected_character_indices()
        ]

    def _notify_no_detail_selection(self, content: str):
        InfoBar.warning(
            title="未选择字符",
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _apply_character_change(self):
        _fix_sentence_character_invariants(self.sentence)
        self._populate_table()
        self._modified = True

    def _add_character(self):
        insert_index = len(self.sentence.characters)
        selected_rows = self._selected_detail_rows()
        if selected_rows:
            insert_index = self._row_groups[selected_rows[-1]][-1] + 1

        new_character = Character(
            char="　",
            check_count=0,
            singer_id=self.sentence.singer_id,
        )
        self.sentence.characters.insert(insert_index, new_character)
        self._apply_character_change()

    def _delete_characters(self):
        selected_indices = self._selected_character_indices()
        if not selected_indices:
            self._notify_no_detail_selection("请先选择要删除的字符")
            return
        if len(selected_indices) >= len(self.sentence.characters):
            InfoBar.warning(
                title="无法删除",
                content="每行至少需要保留 1 个字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2500,
                parent=self,
            )
            return

        selected_set = set(selected_indices)
        self.sentence.characters = [
            character
            for index, character in enumerate(self.sentence.characters)
            if index not in selected_set
        ]
        self._apply_character_change()

    def _copy_characters(self):
        cloned_characters = self._clone_selected_characters()
        if not cloned_characters:
            self._notify_no_detail_selection("请先选择要复制的字符")
            return
        self._char_clipboard = cloned_characters

    def _insert_characters(self):
        if not self._char_clipboard:
            InfoBar.warning(
                title="剪贴板为空",
                content="请先复制字符",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=2000,
                parent=self,
            )
            return

        insert_index = len(self.sentence.characters)
        selected_rows = self._selected_detail_rows()
        if selected_rows:
            insert_index = self._row_groups[selected_rows[-1]][-1] + 1

        clones = [_clone_character(character) for character in self._char_clipboard]
        self.sentence.characters[insert_index:insert_index] = clones
        self._apply_character_change()

    def _handle_shortcut(self, event: QKeyEvent) -> bool:
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_characters()
            return True
        if event.matches(QKeySequence.StandardKey.Paste):
            self._insert_characters()
            return True
        if event.key() == Qt.Key.Key_Delete:
            self._delete_characters()
            return True
        return False

    def eventFilter(self, a0, a1):
        if a0 is self.table and a1 is not None and a1.type() == QEvent.Type.KeyPress:
            key_event = a1 if isinstance(a1, QKeyEvent) else None
            if key_event and self._handle_shortcut(key_event):
                return True
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is not None and self._handle_shortcut(a0):
            a0.accept()
            return
        super().keyPressEvent(a0)

    def _on_save(self):
        """Save edited data back to the Sentence.

        两阶段：先把每行 6 列解析+校验为扁平数据，全部合法才整体重建该行的
        Character；任一行校验失败则整次保存中止、不写入任何数据，保证数据合法。
        写回经 annotated_text 带时间戳行内格式 + parse_timed_line 解析，不重切 mora。
        """
        characters = self.sentence.characters

        # 获取全局偏移量（用于将用户输入的全局时间戳转换回原始时间戳）
        global_offset = 0
        if characters:
            global_offset = characters[0]._global_offset_ms
        elif self._project and self._project.global_offset_ms is not None:
            global_offset = self._project.global_offset_ms

        # Singer name → id 映射
        name_to_id: dict[str, str] = {}
        if self._project:
            for s in self._project.singers:
                name_to_id[s.name] = s.id

        # Phase 1: 逐行解析 + 校验
        errors: List[str] = []
        parsed_rows: List[Optional[dict]] = []
        for row_idx in range(len(self._row_groups)):
            data, row_errors = self._parse_row(row_idx, name_to_id)
            parsed_rows.append(data)
            errors.extend(row_errors)

        if errors:
            InfoBar.warning(
                title="数据校验失败，未保存",
                content="\n".join(errors[:8]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=6000,
                parent=self,
            )
            return

        # Phase 2: 整体重建 characters（逐行经解析引擎构造后顺序拼接）
        new_characters: List[Character] = []
        for data in parsed_rows:
            if data is None:
                continue
            new_characters.extend(
                _build_row_chars(data, global_offset, self.sentence.singer_id)
            )

        for ch in new_characters:
            ch.set_offset(global_offset)
        self.sentence.characters = new_characters
        _fix_sentence_character_invariants(self.sentence)
        self._modified = True
        # Refresh the table to show saved state
        self._populate_table()

        InfoBar.success(
            title="已保存",
            content="数据已更新",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _parse_row(
        self, row_idx: int, name_to_id: dict
    ) -> tuple[Optional[dict], List[str]]:
        """把一行 6 列解析为扁平数据并校验。

        返回 (data | None, errors)。data 字段：
          glyphs / check_counts / mora_flat / ts_flat(全局,允许None) /
          is_sentence_end / singer_ids。任一校验失败返回 (None, errors)。
        """
        errors: List[str] = []

        def cell(col: int) -> str:
            item = self.table.item(row_idx, col)
            return item.text() if item else ""

        # --- 字符 (col 0) ---
        glyphs = list(cell(0))
        if not glyphs:
            errors.append(f"行 {row_idx + 1}: 字符不能为空")
            return None, errors
        n = len(glyphs)

        # --- Checkpoint (col 2) —— 每字符一项，累加得全行 cp 总数 K ---
        cp_text = cell(2).strip()
        cp_tokens = cp_text.split(",") if cp_text != "" else []
        if len(cp_tokens) != n:
            errors.append(
                f"行 {row_idx + 1}: 节奏点列需 {n} 项（与字符数一致），当前 "
                f"{len(cp_tokens)} 项"
            )
            return None, errors
        check_counts: List[int] = []
        for token in cp_tokens:
            token = token.strip()
            try:
                check_counts.append(max(0, int(token)))
            except ValueError:
                errors.append(f"行 {row_idx + 1}: 节奏点 '{token}' 不是整数")
        if errors:
            return None, errors
        total_cp = sum(check_counts)

        # --- 句尾 (col 3) —— 末字 ---
        is_sentence_end = cell(3).strip() == "是"

        # --- 注音 (col 1) —— 全行展平，每段一个 mora；段数须 == K ---
        ruby_text = cell(1)
        if ruby_text.strip() == "":
            mora_flat = [""] * total_cp
        else:
            mora_flat = [m.strip() for m in ruby_text.split(",")]
            if len(mora_flat) != total_cp:
                errors.append(
                    f"行 {row_idx + 1}: 注音段数 {len(mora_flat)} 与节奏点总数 "
                    f"{total_cp} 不一致"
                )
                return None, errors

        # --- 时间标签 (col 4) —— 全行展平，按序对齐 cp，允许空段(缺省) ---
        time_text = cell(4).strip()
        ts_flat: List[Optional[int]] = []
        if time_text != "":
            segs = time_text.split(",")
            max_allowed = total_cp + (1 if is_sentence_end else 0)
            if len(segs) > max_allowed:
                errors.append(
                    f"行 {row_idx + 1}: 时间戳个数 {len(segs)} 超过上限 "
                    f"{max_allowed}（节奏点 {total_cp}"
                    f"{'+句尾1' if is_sentence_end else ''}）"
                )
                return None, errors
            for seg in segs:
                seg = seg.strip()
                if seg == "":
                    ts_flat.append(None)
                else:
                    ms = _parse_time(seg)
                    if ms is None:
                        errors.append(f"行 {row_idx + 1}: 无法解析时间 '{seg}'")
                    else:
                        ts_flat.append(ms)
            if errors:
                return None, errors

        # --- 演唱者 (col 5) —— 每字符一项 ---
        singer_text = cell(5)
        singer_tokens = singer_text.split(",") if singer_text.strip() != "" else []
        singer_ids: List[str] = []
        for i in range(n):
            sname = singer_tokens[i].strip() if i < len(singer_tokens) else ""
            singer_ids.append(name_to_id.get(sname, "") if sname else "")

        return {
            "glyphs": glyphs,
            "check_counts": check_counts,
            "mora_flat": mora_flat,
            "ts_flat": ts_flat,
            "is_sentence_end": is_sentence_end,
            "singer_ids": singer_ids,
        }, errors

    def was_modified(self) -> bool:
        return self._modified


class EditInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("editInterface")
        self.project: Optional[Project] = None
        self._row_clipboard: List[Sentence] = []

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(20, 20, 20, 20)
        self.vbox.setSpacing(10)

        # Top Area
        self.title_label = QLabel("行编辑视图", self)
        self.title_label.setFont(QFont("Microsoft YaHei", 24, QFont.Weight.Bold))
        self.vbox.addWidget(self.title_label)

        self.desc_label = CaptionLabel("查看和编辑所有歌词行的打轴数据", self)
        self.vbox.addWidget(self.desc_label)

        # Stats
        self.stats_label = QLabel("共 0 行 | 已完成 0 行 | 进度 0%", self)
        self.stats_label.setFont(QFont("Microsoft YaHei", 10))
        self.vbox.addWidget(self.stats_label)

        # Toolbar
        toolbar = QHBoxLayout()
        self.btn_refresh = PushButton("刷新", self)
        self.btn_refresh.setIcon(FIF.SYNC)
        self.btn_refresh.clicked.connect(self._update_table)
        toolbar.addWidget(self.btn_refresh)
        self.btn_add_row = PushButton("添加行", self)
        self.btn_add_row.clicked.connect(self._add_row)
        toolbar.addWidget(self.btn_add_row)
        self.btn_delete_row = PushButton("删除行", self)
        self.btn_delete_row.clicked.connect(self._delete_rows)
        toolbar.addWidget(self.btn_delete_row)
        self.btn_copy_row = PushButton("复制行", self)
        self.btn_copy_row.clicked.connect(self._copy_rows)
        toolbar.addWidget(self.btn_copy_row)
        self.btn_insert_row = PushButton("插入行", self)
        self.btn_insert_row.clicked.connect(self._insert_rows)
        toolbar.addWidget(self.btn_insert_row)
        toolbar.addStretch()
        self.vbox.addLayout(toolbar)

        # Table Layout
        self.table = TableWidget(self)
        self.table.setColumnCount(8)
        self.table.setHorizontalHeaderLabels(
            [
                "行号",
                "歌词文本",
                "演唱者",
                "字符数",
                "已打轴",
                "总Checkpoint",
                "时间范围",
                "操作",
            ]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # 隐藏Qt默认行号表头，避免与自定义行号列重复
        v_header = self.table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)

        # Column Resizing
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vbox.addWidget(self.table)
        self.table.installEventFilter(self)

    def set_project(self, project: Project):
        self.project = project
        self._update_table()

    def scroll_to_line(self, line_idx: int):
        """滚动表格并选中指定行（用于从打轴界面自动跳转）"""
        if not self.project or not (0 <= line_idx < len(self.project.sentences)):
            return
        self.table.selectRow(line_idx)
        item = self.table.item(line_idx, 0)
        if item is not None:
            self.table.scrollToItem(
                item, QAbstractItemView.ScrollHint.PositionAtCenter
            )

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)
        if store.project:
            self.set_project(store.project)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            self.project = self._store.project
            self._update_table()
        elif change_type in ("rubies", "singers", "lyrics", "timetags", "checkpoints"):
            self._update_table()

    def _update_table(self):
        if not self.project:
            self.table.setRowCount(0)
            self.stats_label.setText("共 0 行 | 已完成 0 行 | 进度 0%")
            return

        sentences = self.project.sentences
        meaningful_lines = [
            s for s in sentences
            if any(c.total_timing_points > 0 for c in s.characters)
        ]
        total_lines = len(meaningful_lines)
        completed_lines = sum(1 for s in meaningful_lines if s.is_fully_timed())
        progress = (completed_lines / total_lines * 100) if total_lines > 0 else 0

        self.stats_label.setText(
            f"共 {total_lines} 行 | 已完成 {completed_lines} 行 | 进度 {progress:.1f}%"
        )

        self.table.setRowCount(total_lines)
        for i, sentence in enumerate(meaningful_lines):
            # 1. 行号
            item_idx = QTableWidgetItem(str(i + 1))
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 0, item_idx)

            # 2. 歌词文本 — 连词显示为词语
            display_parts: list[str] = []
            for word in sentence.words:
                word_text = word.text
                if word.char_count > 1:
                    display_parts.append(f"[{word_text}]")
                else:
                    display_parts.append(word_text)
            item_text = QTableWidgetItem("".join(display_parts))
            item_text.setFlags(item_text.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 1, item_text)

            # 3. 演唱者 — per-char singer 汇总
            singer_ids_seen: list[str] = []
            for ch in sentence.characters:
                sid = ch.singer_id if ch.singer_id else sentence.singer_id
                if sid not in singer_ids_seen:
                    singer_ids_seen.append(sid)
            if len(singer_ids_seen) <= 1:
                s = (
                    self.project.get_singer(singer_ids_seen[0])
                    if singer_ids_seen
                    else None
                )
                singer_display = s.name if s else "未知"
            else:
                names = []
                for sid in singer_ids_seen:
                    s = self.project.get_singer(sid)
                    names.append(s.name if s else "?")
                singer_display = "/".join(names)
            item_singer = QTableWidgetItem(singer_display)
            item_singer.setFlags(item_singer.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 2, item_singer)

            # 4. 字符数
            item_len = QTableWidgetItem(str(len(sentence.characters)))
            item_len.setFlags(item_len.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 3, item_len)

            # 5. 已打轴 — 按 checkpoint 统计
            timed_cp, total_cp = sentence.get_timing_progress()

            item_timed = QTableWidgetItem(f"{timed_cp}/{total_cp}")
            item_timed.setFlags(item_timed.flags() & ~Qt.ItemFlag.ItemIsEditable)
            # 颜色标记：完成绿色，未完成红色
            if timed_cp >= total_cp:
                item_timed.setForeground(theme.status_complete)
            elif timed_cp > 0:
                item_timed.setForeground(theme.status_partial)
            else:
                item_timed.setForeground(theme.status_none)
            self.table.setItem(i, 4, item_timed)

            # 6. 总Checkpoint
            item_total_cp = QTableWidgetItem(str(total_cp))
            item_total_cp.setFlags(item_total_cp.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 5, item_total_cp)

            # 7. 时间范围（使用全局偏移后的时间戳，与渲染/导出一致）
            if sentence.has_timetags:
                start_ms = sentence.global_timing_start_ms
                end_ms = sentence.global_timing_end_ms
                if start_ms is not None and end_ms is not None:
                    first_time = _fmt_time(start_ms)
                    last_time = _fmt_time(end_ms)
                    time_range = f"{first_time} ~ {last_time}"
                else:
                    time_range = "未打轴"
            else:
                time_range = "未打轴"

            item_range = QTableWidgetItem(time_range)
            item_range.setFlags(item_range.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 6, item_range)

            # 8. 操作 (Button)
            btn = PushButton("编辑", self.table)
            btn.clicked.connect(
                lambda checked, current_sentence=sentence: self._show_detail(
                    current_sentence
                )
            )
            self.table.setCellWidget(i, 7, btn)

    def _selected_row_indices(self) -> List[int]:
        selection_model = self.table.selectionModel()
        if not selection_model:
            return []
        return sorted(index.row() for index in selection_model.selectedRows())

    def _selected_sentences(self) -> List[Sentence]:
        if not self.project:
            return []
        return [
            self.project.sentences[row]
            for row in self._selected_row_indices()
            if 0 <= row < len(self.project.sentences)
        ]

    def _default_singer_id(self) -> str:
        if self.project and self.project.singers:
            return self.project.singers[0].id
        return "default"

    def _notify_row_warning(self, title: str, content: str):
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=2000,
            parent=self,
        )

    def _after_row_operation(self):
        self._update_table()
        if hasattr(self, "_store"):
            self._store.notify("lyrics")

    def _add_row(self):
        if not self.project:
            return

        selected_rows = self._selected_row_indices()
        after_sentence_id = None
        if selected_rows:
            after_sentence_id = self.project.sentences[selected_rows[-1]].id

        new_sentence = Sentence.from_text(" ", self._default_singer_id())
        self.project.add_sentence(new_sentence, after_sentence_id=after_sentence_id)
        self._after_row_operation()

    def _delete_rows(self):
        if not self.project:
            return

        selected_sentences = self._selected_sentences()
        if not selected_sentences:
            self._notify_row_warning("未选择行", "请先选择要删除的行")
            return

        if len(selected_sentences) > 1:
            msg = QMessageBox(self)
            msg.setWindowTitle("确认删除")
            msg.setText(f"确定要删除选中的 {len(selected_sentences)} 行吗？")
            btn_yes = msg.addButton("是", QMessageBox.ButtonRole.AcceptRole)
            msg.addButton("否", QMessageBox.ButtonRole.RejectRole)
            msg.setDefaultButton(btn_yes)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked is not btn_yes:
                return

        for sentence in selected_sentences:
            self.project.remove_sentence(sentence.id)
        self._after_row_operation()

    def _copy_rows(self):
        selected_sentences = self._selected_sentences()
        if not selected_sentences:
            self._notify_row_warning("未选择行", "请先选择要复制的行")
            return

        self._row_clipboard = [
            _clone_sentence(sentence) for sentence in selected_sentences
        ]
        self._after_row_operation()

    def _insert_rows(self):
        if not self.project:
            return
        if not self._row_clipboard:
            self._notify_row_warning("剪贴板为空", "请先复制行")
            return

        selected_rows = self._selected_row_indices()
        after_sentence_id = None
        if selected_rows:
            after_sentence_id = self.project.sentences[selected_rows[-1]].id

        for sentence in self._row_clipboard:
            new_sentence = _clone_sentence(sentence)
            self.project.add_sentence(new_sentence, after_sentence_id=after_sentence_id)
            after_sentence_id = new_sentence.id
        self._after_row_operation()

    def _handle_shortcut(self, event: QKeyEvent) -> bool:
        if event.matches(QKeySequence.StandardKey.Copy):
            self._copy_rows()
            return True
        if event.matches(QKeySequence.StandardKey.Paste):
            self._insert_rows()
            return True
        if event.key() == Qt.Key.Key_Delete:
            self._delete_rows()
            return True
        return False

    def eventFilter(self, a0, a1):
        if a0 is self.table and a1 is not None and a1.type() == QEvent.Type.KeyPress:
            key_event = a1 if isinstance(a1, QKeyEvent) else None
            if key_event and self._handle_shortcut(key_event):
                return True
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, a0: Optional[QKeyEvent]):
        if a0 is not None and self._handle_shortcut(a0):
            a0.accept()
            return
        super().keyPressEvent(a0)

    def _show_detail(self, sentence: Sentence):
        dialog = LineDetailDialog(sentence, project=self.project, parent=self)
        dialog.exec()
        # Refresh table if dialog modified data
        if dialog.was_modified():
            self._update_table()
            if hasattr(self, "_store"):
                self._store.notify("lyrics")

    def showEvent(self, a0):
        super().showEvent(a0)
        self._update_table()
