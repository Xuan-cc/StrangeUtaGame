"""更新源优先级编辑弹窗。

提供 :class:`SourceOrderDialog`：

* 一个可拖动重排的 ``QListWidget``（也支持上移/下移按钮）。
* 顶部用一行 BodyLabel 提示规则；底部默认的"确定 / 取消"按钮。
* 用户点确定后，:attr:`order` 暴露最终排序，调用方负责持久化。
"""

from __future__ import annotations

from typing import List, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    FluentIcon as FIF,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
    TitleLabel,
)

from ..sources import SOURCE_IDS, SOURCE_LABELS, SourceId, normalize_order


class SourceOrderDialog(MessageBoxBase):
    """让用户拖拽 / 上下移动编辑更新源优先级。

    使用方式：

    .. code-block:: python

        dlg = SourceOrderDialog(current=["github", "ghproxy", "gh-proxy"], parent=self)
        if dlg.exec():
            new_order = dlg.order  # ["gh-proxy", "github", "ghproxy"] 之类
    """

    def __init__(self, current: List[str], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._initial: List[SourceId] = normalize_order(current)
        self.order: List[SourceId] = list(self._initial)
        self._build_ui()

    # ── 暴露给外部的最终选择 ──

    def accept(self) -> None:  # type: ignore[override]
        # 在关闭前从 list widget 同步出最终顺序
        self.order = self._read_order()
        super().accept()

    # ── UI ──

    def _build_ui(self) -> None:
        title = TitleLabel("调整更新源优先级", self)
        self.viewLayout.addWidget(title)

        hint = BodyLabel(
            "按顺序尝试，前一项失败时自动降级到下一项。\n"
            "你可以拖动条目，或选中条目后用右侧 ↑/↓ 按钮调整。",
            self,
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #888;")
        self.viewLayout.addWidget(hint)

        body = QWidget(self)
        body_layout = QHBoxLayout(body)
        body_layout.setContentsMargins(0, 8, 0, 0)
        body_layout.setSpacing(10)

        # 列表
        self.list_widget = QListWidget(body)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setFont(QFont("Microsoft YaHei", 10))
        self.list_widget.setMinimumWidth(360)
        self.list_widget.setMinimumHeight(180)
        self.list_widget.setAlternatingRowColors(True)
        for sid in self._initial:
            item = QListWidgetItem(SOURCE_LABELS.get(sid, sid))
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.list_widget.addItem(item)
        body_layout.addWidget(self.list_widget, 1)

        # 右侧按钮列
        btn_col = QWidget(body)
        btn_col_layout = QVBoxLayout(btn_col)
        btn_col_layout.setContentsMargins(0, 0, 0, 0)
        btn_col_layout.setSpacing(6)
        self.btn_up = PushButton("↑ 上移", btn_col)
        self.btn_up.setFont(QFont("Microsoft YaHei", 10))
        self.btn_up.clicked.connect(self._on_up)
        btn_col_layout.addWidget(self.btn_up)
        self.btn_down = PushButton("↓ 下移", btn_col)
        self.btn_down.setFont(QFont("Microsoft YaHei", 10))
        self.btn_down.clicked.connect(self._on_down)
        btn_col_layout.addWidget(self.btn_down)
        self.btn_reset = PushButton("恢复默认", btn_col)
        self.btn_reset.setFont(QFont("Microsoft YaHei", 10))
        self.btn_reset.clicked.connect(self._on_reset)
        btn_col_layout.addWidget(self.btn_reset)
        btn_col_layout.addStretch(1)
        body_layout.addWidget(btn_col, 0)

        self.viewLayout.addWidget(body)

        # 底部 yes/cancel
        self.yesButton.setText("确定")
        self.cancelButton.setText("取消")

        self.setMinimumWidth(520)

    # ── 槽 ──

    def _selected_row(self) -> int:
        items = self.list_widget.selectedItems()
        if not items:
            return -1
        return self.list_widget.row(items[0])

    def _on_up(self) -> None:
        row = self._selected_row()
        if row <= 0:
            return
        item = self.list_widget.takeItem(row)
        if item is None:
            return
        self.list_widget.insertItem(row - 1, item)
        self.list_widget.setCurrentRow(row - 1)

    def _on_down(self) -> None:
        row = self._selected_row()
        if row < 0 or row >= self.list_widget.count() - 1:
            return
        item = self.list_widget.takeItem(row)
        if item is None:
            return
        self.list_widget.insertItem(row + 1, item)
        self.list_widget.setCurrentRow(row + 1)

    def _on_reset(self) -> None:
        self.list_widget.clear()
        for sid in SOURCE_IDS:
            item = QListWidgetItem(SOURCE_LABELS.get(sid, sid))
            item.setData(Qt.ItemDataRole.UserRole, sid)
            self.list_widget.addItem(item)
        self.list_widget.setCurrentRow(0)

    # ── 读出最终顺序 ──

    def _read_order(self) -> List[SourceId]:
        out: List[SourceId] = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item is None:
                continue
            sid = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(sid, str) and sid in SOURCE_IDS:
                out.append(sid)  # type: ignore[arg-type]
        return normalize_order(out)
