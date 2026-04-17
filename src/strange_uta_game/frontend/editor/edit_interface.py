from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QDialog,
    QHeaderView,
    QDialogButtonBox,
    QTableWidgetItem,
    QComboBox,
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QColor
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
    LyricLine,
    TimeTag,
    Ruby,
    CheckpointConfig,
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


class LineDetailDialog(QDialog):
    """行详情对话框 - 允许编辑时间标签"""

    def __init__(self, line: LyricLine, project=None, parent=None):
        super().__init__(parent)
        self.line = line
        self._project = project
        self._modified = False

        title_text = self.line.text[:30] + ("..." if len(self.line.text) > 30 else "")
        self.setWindowTitle(f"行详情 - {title_text}")
        self.resize(800, 500)
        self.setFont(QFont("Microsoft YaHei", 10))

        self.vbox = QVBoxLayout(self)

        # 演唱者选择
        if self._project and self._project.singers:
            singer_layout = QHBoxLayout()
            singer_label = QLabel("演唱者:")
            singer_label.setStyleSheet("font-size: 12px; font-weight: bold;")
            singer_layout.addWidget(singer_label)
            self.singer_combo = QComboBox(self)
            current_singer_idx = 0
            for idx, singer in enumerate(self._project.singers):
                self.singer_combo.addItem(singer.name, singer.id)
                if singer.id == self.line.singer_id:
                    current_singer_idx = idx
            self.singer_combo.setCurrentIndex(current_singer_idx)
            singer_layout.addWidget(self.singer_combo)
            singer_layout.addStretch()
            self.vbox.addLayout(singer_layout)
        else:
            self.singer_combo = None

        # 提示
        hint = QLabel("双击可编辑「字符」「注音」「Checkpoint数」「时间标签」列")
        hint.setStyleSheet("color: gray; font-size: 11px;")
        self.vbox.addWidget(hint)

        # Table
        self.table = TableWidget(self)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(
            ["字符", "注音", "Checkpoint数", "句尾", "时间标签"]
        )
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.vbox.addWidget(self.table)

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
        chars = self.line.chars
        self.table.setRowCount(len(chars))

        cp_map = {cp.char_idx: cp for cp in self.line.checkpoints}

        for i, char in enumerate(chars):
            # 字符 (editable)
            item_char = QTableWidgetItem(char)
            self.table.setItem(i, 0, item_char)

            # 注音 (editable)
            ruby = self.line.get_ruby_for_char(i)
            ruby_text = ruby.text if ruby else ""
            item_ruby = QTableWidgetItem(ruby_text)
            self.table.setItem(i, 1, item_ruby)

            # Checkpoint数 (editable)
            cp = cp_map.get(i)
            cp_count = str(cp.check_count) if cp else "0"
            item_cp = QTableWidgetItem(cp_count)
            self.table.setItem(i, 2, item_cp)

            # 句尾 (read-only)
            is_end = "是" if cp and cp.is_line_end else ""
            item_end = QTableWidgetItem(is_end)
            item_end.setFlags(item_end.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 3, item_end)

            # 时间标签 (editable)
            timetags = self.line.get_timetags_for_char(i)
            tag_texts = [_fmt_time(t.timestamp_ms) for t in timetags]
            item_time = QTableWidgetItem(", ".join(tag_texts))
            # Keep editable (default flags include ItemIsEditable)
            self.table.setItem(i, 4, item_time)

        self.table.blockSignals(False)

    def _on_cell_changed(self, row: int, col: int):
        if col in (0, 1, 2, 4):
            self._modified = True

    def _on_save(self):
        """Save edited characters, rubies, and time tags back to the LyricLine."""
        errors: List[str] = []

        cp_map = {cp.char_idx: cp for cp in self.line.checkpoints}

        for i in range(self.table.rowCount()):
            # --- 字符编辑 (col 0) ---
            item_char = self.table.item(i, 0)
            if item_char:
                new_char = item_char.text().strip()
                if new_char and new_char != self.line.chars[i]:
                    self.line.chars[i] = new_char

            # --- 注音编辑 (col 1) ---
            item_ruby = self.table.item(i, 1)
            if item_ruby:
                new_ruby_text = item_ruby.text().strip()
                old_ruby = self.line.get_ruby_for_char(i)
                if new_ruby_text:
                    if old_ruby:
                        if old_ruby.text != new_ruby_text:
                            self.line.rubies.remove(old_ruby)
                            try:
                                self.line.add_ruby(
                                    Ruby(
                                        text=new_ruby_text,
                                        start_idx=old_ruby.start_idx,
                                        end_idx=old_ruby.end_idx,
                                    )
                                )
                            except Exception as e:
                                errors.append(
                                    f"字符 {i + 1} ({self.line.chars[i]}): 注音错误 {e}"
                                )
                    else:
                        try:
                            self.line.add_ruby(
                                Ruby(
                                    text=new_ruby_text,
                                    start_idx=i,
                                    end_idx=i + 1,
                                )
                            )
                        except Exception as e:
                            errors.append(
                                f"字符 {i + 1} ({self.line.chars[i]}): 注音错误 {e}"
                            )
                else:
                    if old_ruby:
                        self.line.rubies.remove(old_ruby)

            # --- Checkpoint 编辑 (col 2) ---
            item_cp = self.table.item(i, 2)
            if item_cp:
                try:
                    new_count = int(item_cp.text().strip())
                    if new_count < 0:
                        new_count = 0
                    old_cp_cfg = cp_map.get(i)
                    if old_cp_cfg and old_cp_cfg.check_count != new_count:
                        self.line.set_checkpoint_config(
                            CheckpointConfig(
                                char_idx=i,
                                check_count=new_count,
                                is_line_end=old_cp_cfg.is_line_end,
                                is_rest=old_cp_cfg.is_rest,
                            )
                        )
                except ValueError:
                    errors.append(f"字符 {i + 1}: Checkpoint数必须为整数")

            # --- 时间标签编辑 (col 4) ---
            item = self.table.item(i, 4)
            if not item:
                continue

            text = item.text().strip()

            # Remove existing timetags for this char
            existing = self.line.get_timetags_for_char(i)
            for tag in list(existing):
                self.line.timetags.remove(tag)

            if not text:
                continue

            # Parse comma-separated time values
            parts = [p.strip() for p in text.split(",") if p.strip()]
            for cp_idx, part in enumerate(parts):
                ms = _parse_time(part)
                if ms is None:
                    errors.append(
                        f"字符 {i + 1} ({self.line.chars[i]}): 无法解析 '{part}'"
                    )
                    continue
                tag = TimeTag(
                    timestamp_ms=ms,
                    singer_id=self.line.singer_id,
                    char_idx=i,
                    checkpoint_idx=cp_idx,
                )
                self.line.add_timetag(tag)

        # 同步 text 字段
        self.line.text = "".join(self.line.chars)

        # 保存演唱者选择
        if self.singer_combo is not None:
            new_singer_id = self.singer_combo.currentData()
            if new_singer_id and new_singer_id != self.line.singer_id:
                self.line.singer_id = new_singer_id

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

        self._modified = True
        # Refresh the table to show saved state
        self._populate_table()

        InfoBar.success(
            title="已保存",
            content="时间标签已更新",
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

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(20, 20, 20, 20)
        self.vbox.setSpacing(10)

        # Top Area
        self.title_label = QLabel("编辑视图", self)
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

        # Column Resizing
        header = self.table.horizontalHeader()
        if header:
            header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.vbox.addWidget(self.table)

    def set_project(self, project: Project):
        self.project = project
        self._update_table()

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

        lines = self.project.lines
        total_lines = len(lines)
        completed_lines = sum(1 for line in lines if line.is_fully_timed())
        progress = (completed_lines / total_lines * 100) if total_lines > 0 else 0

        self.stats_label.setText(
            f"共 {total_lines} 行 | 已完成 {completed_lines} 行 | 进度 {progress:.1f}%"
        )

        self.table.setRowCount(total_lines)
        for i, line in enumerate(lines):
            # 1. 行号
            item_idx = QTableWidgetItem(str(i + 1))
            item_idx.setFlags(item_idx.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 0, item_idx)

            # 2. 歌词文本
            item_text = QTableWidgetItem(line.text)
            item_text.setFlags(item_text.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 1, item_text)

            # 3. 演唱者
            singer = self.project.get_singer(line.singer_id) if line.singer_id else None
            singer_name = singer.name if singer else "未知"
            item_singer = QTableWidgetItem(singer_name)
            item_singer.setFlags(item_singer.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 2, item_singer)

            # 4. 字符数
            item_len = QTableWidgetItem(str(len(line.chars)))
            item_len.setFlags(item_len.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 3, item_len)

            # 5. 已打轴 — 按 checkpoint 统计
            total_cp = sum(cp.check_count for cp in line.checkpoints)
            timed_cp = len(line.timetags)

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
            if line.timetags:
                sorted_tags = sorted(line.timetags, key=lambda t: t.timestamp_ms)
                first_time = _fmt_time(sorted_tags[0].timestamp_ms)
                last_time = _fmt_time(sorted_tags[-1].timestamp_ms)
                time_range = f"{first_time} ~ {last_time}"
            else:
                time_range = "未打轴"

            item_range = QTableWidgetItem(time_range)
            item_range.setFlags(item_range.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(i, 6, item_range)

            # 8. 操作 (Button)
            btn = PushButton("编辑", self.table)
            btn.clicked.connect(
                lambda checked, current_line=line: self._show_detail(current_line)
            )
            self.table.setCellWidget(i, 7, btn)

    def _show_detail(self, line: LyricLine):
        dialog = LineDetailDialog(line, project=self.project, parent=self)
        dialog.exec()
        # Refresh table if dialog modified data
        if dialog.was_modified():
            self._update_table()
            if hasattr(self, "_store"):
                self._store.notify("lyrics")

    def showEvent(self, a0):
        super().showEvent(a0)
        self._update_table()
