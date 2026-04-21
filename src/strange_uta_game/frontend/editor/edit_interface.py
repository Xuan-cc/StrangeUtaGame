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
)
from typing import Optional, List
from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
)
import re


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
        text=ruby.text, timestamps=list(ruby.timestamps), singer_id=ruby.singer_id
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
    # 默认末尾也是句尾（如果之前不是句尾，添加句尾并+1 checkpoint）
    if not last_character.is_sentence_end:
        last_character.is_sentence_end = True
    if last_character.check_count < 1:
        last_character.check_count = 1
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
            "连词合并为一行，注音/Checkpoint/演唱者用逗号分隔对应各字符\n"
            "双击可编辑「字符」「注音」「Checkpoint数」「句尾」「时间标签」「演唱者」列\n"
            "句尾列填写「是」标记为句尾（独立记录释放时间），留空取消"
        )
        hint.setStyleSheet("color: gray; font-size: 11px;")
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
            # 字符 (editable)
            group_text = "".join(characters[ci].char for ci in group)
            item_char = QTableWidgetItem(group_text)
            self.table.setItem(row, 0, item_char)

            # 注音 (editable) — 连词用逗号分隔
            rubies_text: list[str] = []
            for ci in group:
                r = characters[ci].ruby
                rubies_text.append(r.text if r else "")
            if len(group) > 1:
                ruby_display = ",".join(rubies_text)
            else:
                ruby_display = rubies_text[0]
            item_ruby = QTableWidgetItem(ruby_display)
            self.table.setItem(row, 1, item_ruby)

            # Checkpoint数 (editable) — 连词用逗号分隔
            cp_vals: list[str] = []
            for ci in group:
                cp_vals.append(str(characters[ci].check_count))
            cp_display = ",".join(cp_vals) if len(group) > 1 else cp_vals[0]
            item_cp = QTableWidgetItem(cp_display)
            self.table.setItem(row, 2, item_cp)

            # 句尾 (editable) — 组内最后字符
            last_ci = group[-1]
            is_end = "是" if characters[last_ci].is_sentence_end else ""
            item_end = QTableWidgetItem(is_end)
            self.table.setItem(row, 3, item_end)

            # 时间标签 (editable) — 连词每字符用 | 分隔
            tag_parts: list[str] = []
            for ci in group:
                timetags = self.sentence.get_timetags_for_char(ci)
                tag_texts = [_fmt_time(t) for t in timetags]
                tag_parts.append(", ".join(tag_texts) if tag_texts else "")
            time_display = " | ".join(tag_parts) if len(group) > 1 else tag_parts[0]
            item_time = QTableWidgetItem(time_display)
            self.table.setItem(row, 4, item_time)

            # 演唱者 (editable) — per-char singer，连词逗号分隔
            singer_parts: list[str] = []
            for ci in group:
                sid = characters[ci].singer_id
                singer_parts.append(singer_map.get(sid, "") if sid else "")
            if len(group) > 1:
                singer_display = ",".join(singer_parts)
            else:
                singer_display = singer_parts[0]
            item_singer = QTableWidgetItem(singer_display)
            self.table.setItem(row, 5, item_singer)

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
            check_count=1,
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
        """Save edited data back to the Sentence (supports grouped linked chars)."""
        errors: List[str] = []
        characters = self.sentence.characters

        # Singer name → id 映射
        name_to_id: dict[str, str] = {}
        if self._project:
            for s in self._project.singers:
                name_to_id[s.name] = s.id

        for row_idx, group in enumerate(self._row_groups):
            g_len = len(group)

            # --- 字符编辑 (col 0) ---
            item_char = self.table.item(row_idx, 0)
            if item_char:
                new_chars = list(item_char.text().strip())
                if len(new_chars) == g_len:
                    for k, ci in enumerate(group):
                        if new_chars[k] != characters[ci].char:
                            characters[ci].char = new_chars[k]

            # --- 注音编辑 (col 1) ---
            item_ruby = self.table.item(row_idx, 1)
            if item_ruby:
                raw = item_ruby.text().strip()
                if g_len > 1 and "," in raw:
                    # 连词组：逗号分隔 per-char ruby
                    parts = raw.split(",")
                    for k, ci in enumerate(group):
                        new_r_text = parts[k].strip() if k < len(parts) else ""
                        if new_r_text:
                            try:
                                characters[ci].set_ruby(Ruby(text=new_r_text))
                            except Exception as e:
                                errors.append(f"字符 {ci + 1}: 注音错误 {e}")
                        else:
                            characters[ci].set_ruby(None)
                else:
                    # 单字符或无逗号的整体 ruby
                    if raw:
                        if g_len == 1:
                            try:
                                characters[group[0]].set_ruby(Ruby(text=raw))
                            except Exception as e:
                                errors.append(f"字符 {group[0] + 1}: 注音错误 {e}")
                        else:
                            # 多字符无逗号：整体 ruby 分配到第一个字符
                            try:
                                characters[group[0]].set_ruby(Ruby(text=raw))
                            except Exception as e:
                                errors.append(f"字符 {group[0] + 1}: 注音错误 {e}")
                            for ci in group[1:]:
                                characters[ci].set_ruby(None)
                    else:
                        # 清除组内所有 ruby
                        for ci in group:
                            characters[ci].set_ruby(None)

            # --- Checkpoint 编辑 (col 2) ---
            item_cp = self.table.item(row_idx, 2)
            if item_cp:
                raw_cp = item_cp.text().strip()
                if g_len > 1 and "," in raw_cp:
                    parts = raw_cp.split(",")
                    for k, ci in enumerate(group):
                        try:
                            new_count = int(parts[k].strip()) if k < len(parts) else 0
                            if new_count < 0:
                                new_count = 0
                            characters[ci].check_count = new_count
                        except ValueError:
                            errors.append(f"字符 {ci + 1}: Checkpoint数必须为整数")
                else:
                    try:
                        new_count = int(raw_cp)
                        if new_count < 0:
                            new_count = 0
                        characters[group[0]].check_count = new_count
                    except ValueError:
                        errors.append(f"行 {row_idx + 1}: Checkpoint数必须为整数")

            # --- 句尾编辑 (col 3) ---
            item_end = self.table.item(row_idx, 3)
            if item_end:
                raw_end = item_end.text().strip()
                last_ci = group[-1]
                new_is_sentence_end = raw_end == "是"
                old_is_sentence_end = characters[last_ci].is_sentence_end
                if new_is_sentence_end != old_is_sentence_end:
                    if new_is_sentence_end:
                        if characters[last_ci].check_count <= 0:
                            errors.append(
                                f"字符 {last_ci + 1}: 句尾至少需要 1 个普通节奏点"
                            )
                            continue
                        characters[last_ci].is_sentence_end = True
                    else:
                        characters[last_ci].clear_sentence_end_ts()
                        characters[last_ci].is_sentence_end = False

            # --- 时间标签编辑 (col 4) ---
            item_time = self.table.item(row_idx, 4)
            if item_time:
                raw_time = item_time.text().strip()
                if g_len > 1:
                    # 连词组：用 | 分隔各字符的时间标签
                    char_time_parts = raw_time.split("|") if raw_time else []
                    for k, ci in enumerate(group):
                        characters[ci].clear_timestamps()
                        part = (
                            char_time_parts[k].strip()
                            if k < len(char_time_parts)
                            else ""
                        )
                        if not part:
                            continue
                        segments = [p.strip() for p in part.split(",") if p.strip()]
                        normal_segments = segments[: characters[ci].check_count]
                        for cp_idx, seg in enumerate(normal_segments):
                            ms = _parse_time(seg)
                            if ms is None:
                                errors.append(f"字符 {ci + 1}: 无法解析 '{seg}'")
                                continue
                            characters[ci].add_timestamp(ms, checkpoint_idx=cp_idx)
                        if (
                            characters[ci].is_sentence_end
                            and len(segments) > characters[ci].check_count
                        ):
                            ms = _parse_time(segments[characters[ci].check_count])
                            if ms is None:
                                errors.append(
                                    f"字符 {ci + 1}: 无法解析 '{segments[characters[ci].check_count]}'"
                                )
                            else:
                                characters[ci].set_sentence_end_ts(ms)
                else:
                    ci = group[0]
                    characters[ci].clear_timestamps()
                    if raw_time:
                        segments = [p.strip() for p in raw_time.split(",") if p.strip()]
                        normal_segments = segments[: characters[ci].check_count]
                        for cp_idx, seg in enumerate(normal_segments):
                            ms = _parse_time(seg)
                            if ms is None:
                                errors.append(f"字符 {ci + 1}: 无法解析 '{seg}'")
                                continue
                            characters[ci].add_timestamp(ms, checkpoint_idx=cp_idx)
                        if (
                            characters[ci].is_sentence_end
                            and len(segments) > characters[ci].check_count
                        ):
                            ms = _parse_time(segments[characters[ci].check_count])
                            if ms is None:
                                errors.append(
                                    f"字符 {ci + 1}: 无法解析 '{segments[characters[ci].check_count]}'"
                                )
                            else:
                                characters[ci].set_sentence_end_ts(ms)

            # --- 演唱者编辑 (col 5) ---
            item_singer = self.table.item(row_idx, 5)
            if item_singer:
                raw_singer = item_singer.text().strip()
                if g_len > 1 and "," in raw_singer:
                    parts = raw_singer.split(",")
                    for k, ci in enumerate(group):
                        sname = parts[k].strip() if k < len(parts) else ""
                        sid = name_to_id.get(sname, "") if sname else ""
                        characters[ci].singer_id = sid
                else:
                    sname = raw_singer
                    sid = name_to_id.get(sname, "") if sname else ""
                    for ci in group:
                        characters[ci].singer_id = sid

        if errors:
            InfoBar.warning(
                title="部分解析失败",
                content="\n".join(errors[:5]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

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

        self.desc_label = QLabel("查看和编辑所有歌词行的打轴数据", self)
        self.desc_label.setFont(QFont("Microsoft YaHei", 12))
        self.desc_label.setStyleSheet("color: gray;")
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
        total_lines = len(sentences)
        completed_lines = sum(1 for s in sentences if s.is_fully_timed())
        progress = (completed_lines / total_lines * 100) if total_lines > 0 else 0

        self.stats_label.setText(
            f"共 {total_lines} 行 | 已完成 {completed_lines} 行 | 进度 {progress:.1f}%"
        )

        self.table.setRowCount(total_lines)
        for i, sentence in enumerate(sentences):
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
                item_timed.setForeground(QColor("#2ecc71"))
            elif timed_cp > 0:
                item_timed.setForeground(QColor("#f39c12"))
            else:
                item_timed.setForeground(QColor("#999"))
            self.table.setItem(i, 4, item_timed)

            # 6. 总Checkpoint
            item_total_cp = QTableWidgetItem(str(total_cp))
            item_total_cp.setFlags(item_total_cp.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 5, item_total_cp)

            # 7. 时间范围
            if sentence.has_timetags:
                start_ms = sentence.timing_start_ms
                end_ms = sentence.timing_end_ms
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
            result = QMessageBox.question(
                self,
                "确认删除",
                f"确定要删除选中的 {len(selected_sentences)} 行吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
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
