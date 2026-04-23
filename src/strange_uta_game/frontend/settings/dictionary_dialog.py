"""用户读音词典编辑对话框。"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from qfluentwidgets import InfoBar, InfoBarPosition, PrimaryPushButton, PushButton

from .app_settings import _parse_rl_dictionary


class DictionaryEditDialog(QDialog):
    """用户读音词典编辑对话框

    三列表格：启用 | 词 | 读音
    按从上到下排列，顶部 = 最高优先级。新条目默认添加到顶部。
    支持导入 RL 字典文件和上下移动调整优先级。
    """

    def __init__(self, entries: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("读音词典")
        self.setMinimumSize(560, 480)
        self._entries = [dict(e) for e in entries]  # 深拷贝

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        title = QLabel("读音词典")
        title.setFont(QFont("Microsoft YaHei", 14))
        layout.addWidget(title)

        desc = QLabel(
            "设置固定读音的词汇。词典中的词将优先于自动注音。\n"
            "优先级从上到下递减，新添加的词条默认在顶部（最高优先级）。"
        )
        desc.setFont(QFont("Microsoft YaHei", 10))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._table = QTableWidget(0, 3, self)
        self._table.setHorizontalHeaderLabels(["启用", "词", "读音"])
        header = self._table.horizontalHeader()
        if header is not None:
            header.setStretchLastSection(True)
        self._table.setColumnWidth(0, 50)
        self._table.setColumnWidth(1, 140)
        self._table.setAlternatingRowColors(True)
        layout.addWidget(self._table)

        # 填充数据
        for entry in self._entries:
            self._append_row(
                entry.get("enabled", True),
                entry.get("word", ""),
                entry.get("reading", ""),
            )

        # 按钮行
        btn_row = QHBoxLayout()
        btn_add = PushButton("添加", self)
        btn_add.clicked.connect(self._on_add)
        btn_del = PushButton("删除选中", self)
        btn_del.clicked.connect(self._on_delete)
        btn_up = PushButton("上移", self)
        btn_up.clicked.connect(self._on_move_up)
        btn_down = PushButton("下移", self)
        btn_down.clicked.connect(self._on_move_down)
        btn_import = PushButton("导入RL字典", self)
        btn_import.clicked.connect(self._on_import_rl)
        btn_row.addWidget(btn_add)
        btn_row.addWidget(btn_del)
        btn_row.addWidget(btn_up)
        btn_row.addWidget(btn_down)
        btn_row.addWidget(btn_import)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # 确定/取消
        ok_row = QHBoxLayout()
        btn_ok = PrimaryPushButton("确定", self)
        btn_ok.clicked.connect(self.accept)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        ok_row.addStretch()
        ok_row.addWidget(btn_ok)
        ok_row.addWidget(btn_cancel)
        layout.addLayout(ok_row)

    def _append_row(self, enabled: bool, word: str = "", reading: str = ""):
        """在表格末尾追加一行。"""
        row = self._table.rowCount()
        self._table.insertRow(row)

        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._table.setItem(row, 0, chk)
        self._table.setItem(row, 1, QTableWidgetItem(word))
        self._table.setItem(row, 2, QTableWidgetItem(reading))

    def _insert_row_at(self, row: int, enabled: bool, word: str, reading: str):
        """在指定位置插入一行。"""
        self._table.insertRow(row)
        chk = QTableWidgetItem()
        chk.setCheckState(Qt.CheckState.Checked if enabled else Qt.CheckState.Unchecked)
        self._table.setItem(row, 0, chk)
        self._table.setItem(row, 1, QTableWidgetItem(word))
        self._table.setItem(row, 2, QTableWidgetItem(reading))

    def _on_add(self):
        # 新条目插入到顶部（最高优先级）
        self._insert_row_at(0, True, "", "")
        self._table.scrollToTop()
        self._table.selectRow(0)

    def _on_delete(self):
        rows = sorted(
            set(idx.row() for idx in self._table.selectedIndexes()), reverse=True
        )
        for row in rows:
            self._table.removeRow(row)

    def _on_move_up(self):
        """将选中行上移一位（提高优先级）。"""
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()))
        if not rows or rows[0] == 0:
            return
        for row in rows:
            self._swap_rows(row, row - 1)
        # 重新选中
        self._table.clearSelection()
        for row in rows:
            self._table.selectRow(row - 1)

    def _on_move_down(self):
        """将选中行下移一位（降低优先级）。"""
        rows = sorted(
            set(idx.row() for idx in self._table.selectedIndexes()), reverse=True
        )
        if not rows or rows[0] == self._table.rowCount() - 1:
            return
        for row in rows:
            self._swap_rows(row, row + 1)
        # 重新选中
        self._table.clearSelection()
        for row in rows:
            self._table.selectRow(row + 1)

    def _swap_rows(self, row_a: int, row_b: int):
        """交换两行数据。"""
        for col in range(self._table.columnCount()):
            item_a = self._table.takeItem(row_a, col)
            item_b = self._table.takeItem(row_b, col)
            if item_a and item_b:
                self._table.setItem(row_a, col, item_b)
                self._table.setItem(row_b, col, item_a)

    def _on_import_rl(self):
        """导入 RL 字典文件。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择RL字典文件", "", "文本文件 (*.txt);;所有文件 (*)"
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            InfoBar.warning(
                title="读取失败",
                content=str(e),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        new_entries = _parse_rl_dictionary(text)
        if not new_entries:
            InfoBar.warning(
                title="导入失败",
                content="未找到有效的词典条目",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 逆序导入：重复条目以新导入覆盖并置顶（最高优先级）
        # word -> row 索引，随插入/删除动态重建
        def _rebuild_index() -> dict:
            idx = {}
            for r in range(self._table.rowCount()):
                item = self._table.item(r, 1)
                if item:
                    idx[item.text().strip()] = r
            return idx

        index = _rebuild_index()
        added = 0
        updated = 0
        for entry in reversed(new_entries):
            word = entry["word"]
            reading = entry["reading"]
            if word in index:
                # 覆盖旧条目：删除后插入顶部
                self._table.removeRow(index[word])
                updated += 1
            else:
                added += 1
            self._insert_row_at(0, True, word, reading)
            index = _rebuild_index()

        self._table.scrollToTop()
        InfoBar.success(
            title="导入完成",
            content=f"新增 {added} 条，覆盖 {updated} 条（共 {len(new_entries)} 条，已全部置顶）",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=self,
        )

    def get_entries(self) -> list:
        entries = []
        for row in range(self._table.rowCount()):
            chk = self._table.item(row, 0)
            word_item = self._table.item(row, 1)
            reading_item = self._table.item(row, 2)
            word = word_item.text().strip() if word_item else ""
            reading = reading_item.text().strip() if reading_item else ""
            if not word and not reading:
                continue
            enabled = chk.checkState() == Qt.CheckState.Checked if chk else True
            entries.append({"enabled": enabled, "word": word, "reading": reading})
        return entries
