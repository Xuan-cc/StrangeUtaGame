"""字体选择弹窗 — 带过滤器的 Fluent 风格字体选择器。

系统字体可能很多，下拉菜单不便查找；改用遮罩弹窗 + 搜索过滤 + 列表，
支持双击直接选用。每个列表项以该字体自身渲染，便于预览。

显示与搜索均支持字体的本地化名称（如日文「HG教科書体」、中文「微软雅黑」），
不再只能通过英文/罗马名查找。条目（含逐族的平滑缩放/书写系统查询与本地化
名解析）由 :mod:`...frontend.font_cache` 进程级缓存并提供启动预热，字体库
庞大的机器上重复打开不再重新枚举全部字体。仅列出可平滑缩放的字体，排除
Terminal/Fixedsys 等位图字体（DirectWrite 无法加载会刷
``CreateFontFaceFromHDC() failed`` 报错）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QListWidgetItem
from qfluentwidgets import BodyLabel, ListWidget, MessageBoxBase, SearchLineEdit, SubtitleLabel

from strange_uta_game.frontend.font_cache import font_picker_entries

_FAMILY_ROLE = Qt.ItemDataRole.UserRole
_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1


class FontPickerDialog(MessageBoxBase):
    """以遮罩对话框形式选择系统字体。

    用法::

        dlg = FontPickerDialog(current_family, parent=window)
        if dlg.exec():
            family = dlg.selected_family()  # 返回 Qt 可用的族名
    """

    def __init__(self, current: str = "", title: str = "", parent=None):
        super().__init__(parent)
        self._selected = current or ""
        self._entries = font_picker_entries()  # [(family, display, search)]，进程级缓存

        # title 默认走 tr("选择字体")——参数为空时取本地化默认；调用方传入自定义
        # 标题（如带前缀）时使用原值。
        self.titleLabel = SubtitleLabel(title or self.tr("选择字体"), self)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText(self.tr("输入字体名称过滤（支持中/日/韩文名）…"))
        self.searchEdit.setClearButtonEnabled(True)
        self.listWidget = ListWidget(self)
        self.listWidget.setMinimumHeight(380)
        self.hintLabel = BodyLabel("", self)
        self.hintLabel.setWordWrap(True)

        self.viewLayout.addWidget(self.titleLabel)
        self.viewLayout.addWidget(self.searchEdit)
        self.viewLayout.addWidget(self.listWidget)
        self.viewLayout.addWidget(self.hintLabel)
        self.widget.setMinimumWidth(460)

        self.yesButton.setText(self.tr("选用"))
        self.cancelButton.setText(self.tr("取消"))

        self._populate(self._entries)
        self._select_family(self._selected)

        self.searchEdit.textChanged.connect(self._on_filter)
        self.listWidget.currentItemChanged.connect(self._on_current_changed)
        self.listWidget.itemDoubleClicked.connect(self._on_double_clicked)

    # ── 列表填充/选择 ──

    def _populate(self, entries: list[tuple[str, str, str]]):
        self.listWidget.clear()
        for family, display, search in entries:
            item = QListWidgetItem(display)
            item.setFont(QFont(family, 12))  # 以该字体自身渲染做预览
            item.setData(_FAMILY_ROLE, family)
            item.setData(_SEARCH_ROLE, search)
            self.listWidget.addItem(item)

    def _select_family(self, family: str):
        if not family:
            return
        for i in range(self.listWidget.count()):
            item = self.listWidget.item(i)
            if item.data(_FAMILY_ROLE) == family:
                self.listWidget.setCurrentItem(item)
                self.listWidget.scrollToItem(item)
                return

    def _on_filter(self, text: str):
        text = (text or "").strip().lower()
        if not text:
            filtered = self._entries
        else:
            filtered = [e for e in self._entries if text in e[2]]
        self._populate(filtered)
        self._select_family(self._selected)

    def _on_current_changed(self, current, _previous=None):
        if current is not None:
            fam = current.data(_FAMILY_ROLE)
            if fam:
                self._selected = fam

    def _on_double_clicked(self, item: QListWidgetItem):
        if item is not None:
            fam = item.data(_FAMILY_ROLE)
            if fam:
                self._selected = fam
        self.accept()

    # ── 对外 ──

    def selected_family(self) -> str:
        return self._selected
