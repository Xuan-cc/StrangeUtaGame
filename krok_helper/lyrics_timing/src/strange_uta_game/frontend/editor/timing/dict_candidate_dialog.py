"""候补字典查询对话框 + 字典条目 → 字符行填充工具。

被「批量变更 / 修改所选字符 / 编辑字符(F2)」三个对话框复用：在执行/取消之间
插入"查询候补字典"按钮，点击弹出本对话框，列出**完全匹配** word 的所有词典条目
（本地 + 启用网络源，经 ``load_effective_dictionary``）。用户双击条目或点"应用"
后，调用方按选中条目的 annotated reading 解析填充字符行（含 RubyPart / check_count /
连词），再执行原对话框的应用逻辑并关闭两窗口。

公共 API
--------
* :func:`query_dict_candidates` — word → 完全匹配的条目列表（保序、去重 reading）。
* :func:`apply_entry_to_dialog_rows` — 把 ``(word, reading)`` 解析并填充到一个具备
  ``edit_new_chars`` + ``_char_rows`` 结构的对话框；返回是否成功。
* :class:`DictCandidateDialog` — 候补列表对话框。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import PrimaryPushButton, PushButton


def query_dict_candidates(word: str) -> List[Dict[str, Any]]:
    """返回 word 完全匹配的词典条目（本地 + 启用网络源）。

    Args:
        word: 待查询原文（完全相等匹配 entry["word"]）。

    Returns:
        ``[{"word": str, "reading": str}, ...]``，按 effective dict 顺序（优先级），
        相同 reading 去重。查询失败 / 无匹配 → 空列表。
    """
    word = (word or "").strip()
    if not word:
        return []
    try:
        from strange_uta_game.frontend.settings.app_settings import AppSettings

        entries = AppSettings().load_effective_dictionary()
    except Exception:
        return []
    out: List[Dict[str, Any]] = []
    seen_readings: set = set()
    for e in entries or []:
        if (e.get("word") or "").strip() != word:
            continue
        reading = (e.get("reading") or "").strip()
        if reading in seen_readings:
            continue
        seen_readings.add(reading)
        out.append({"word": word, "reading": reading})
    return out


def apply_entry_to_dialog_rows(dialog: Any, word: str, reading: str) -> bool:
    """把词典条目 ``(word, reading)`` 解析后填充到对话框的字符行。

    要求 ``dialog`` 具备：
      * ``edit_new_chars`` (QLineEdit)：设置其文本会触发行重建到 len(word) 行；
      * ``_char_rows``：``[(label, edit_ruby, edit_check, chk_linked), ...]``。

    解析按"完全遵循词典格式"：
      * RubyPart：annotated 段内 ``|`` 分隔 → 行注音框用半角逗号连接（行约定）；
      * check_count：= 该字符 RubyPart 数（无 part → 1）；
      * 连词：同 annotated block 内相邻字符 linked=True。

    Returns:
        成功填充 True；reading 解析失败 / raw≠word → False（不改动对话框）。
    """
    from strange_uta_game.backend.application.auto_check_service import (
        _parse_dict_reading,
    )

    parsed = _parse_dict_reading(reading, word)
    if parsed is None:
        return False
    per_char_parts, char_block_id = parsed
    n = len(word)
    if len(per_char_parts) != n:
        return False

    # 强制"直接应用"分段模式：使行注音框里的逗号分段被原样采纳，
    # 不被"按字符/按 mora 均分"重新切分，确保完全遵循词典格式。
    radio_direct = getattr(dialog, "_radio_direct", None)
    if radio_direct is not None:
        try:
            radio_direct.setChecked(True)
        except Exception:
            pass

    # 设置新字符文本 → 触发对话框重建行（保留旧值，但随后逐行覆盖）
    dialog.edit_new_chars.setText(word)

    rows = getattr(dialog, "_char_rows", [])
    if len(rows) != n:
        # 行数与 word 不符（重建异常）→ 放弃，避免错位
        return False

    for i in range(n):
        _lbl, edit_ruby, edit_check, chk_linked = rows[i]
        parts = per_char_parts[i]
        edit_ruby.setText(",".join(parts))
        edit_check.setText(str(max(1, len(parts))) if parts else "1")
        # 连词：与下一字符同 block 且 block 非字面(-1)
        linked = (
            i + 1 < n
            and char_block_id[i] != -1
            and char_block_id[i] == char_block_id[i + 1]
        )
        chk_linked.setChecked(linked)
    return True


class DictCandidateDialog(QDialog):
    """候补字典查询对话框：列出 word 的完全匹配条目，供选择应用。"""

    def __init__(self, word: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("查询候补字典")
        self.setMinimumSize(480, 360)
        self._word = (word or "").strip()
        self._candidates = query_dict_candidates(self._word)
        self._selected_entry: Optional[Dict[str, Any]] = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel(f"候补字典 — 「{self._word}」")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel(
            "列出词典中与所选原文**完全匹配**的条目（本地 + 启用网络源，按优先级）。\n"
            "双击条目或选中后点「应用」，将按该条目的格式（RubyPart / 节奏点 / 连词）填充并执行。"
        )
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._table = QTableWidget(0, 2, self)
        self._table.setHorizontalHeaderLabels(["词", "注音(annotated)"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self._table.setColumnWidth(0, 120)
        self._table.setAlternatingRowColors(True)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.cellDoubleClicked.connect(self._on_double_clicked)
        layout.addWidget(self._table, 1)

        for entry in self._candidates:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(entry["word"]))
            self._table.setItem(row, 1, QTableWidgetItem(entry["reading"]))
        if self._candidates:
            self._table.selectRow(0)
        else:
            empty = QLabel("（词典中没有完全匹配的条目）")
            empty.setFont(QFont("Microsoft YaHei", 10))
            layout.addWidget(empty)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_apply = PrimaryPushButton("应用", self)
        self.btn_apply.clicked.connect(self._on_apply)
        self.btn_apply.setEnabled(bool(self._candidates))
        btn_row.addWidget(self.btn_apply)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _row_to_entry(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self._candidates):
            return self._candidates[row]
        return None

    def _on_double_clicked(self, row: int, _col: int) -> None:
        entry = self._row_to_entry(row)
        if entry is not None:
            self._selected_entry = entry
            self.accept()

    def _on_apply(self) -> None:
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows:
            return
        entry = self._row_to_entry(rows[0])
        if entry is not None:
            self._selected_entry = entry
            self.accept()

    def get_selected_entry(self) -> Optional[Dict[str, Any]]:
        return self._selected_entry
