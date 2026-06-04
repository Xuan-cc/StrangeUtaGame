"""字体选择弹窗 — 带过滤器的 Fluent 风格字体选择器。

系统字体可能很多，下拉菜单不便查找；改用遮罩弹窗 + 搜索过滤 + 列表，
支持双击直接选用。每个列表项以该字体自身渲染，便于预览。

显示与搜索均支持字体的本地化名称（如日文「HG教科書体」、中文「微软雅黑」），
不再只能通过英文/罗马名查找；解析来自 :mod:`...frontend.font_names`。
仅列出可平滑缩放的字体，排除 Terminal/Fixedsys 等位图字体
（DirectWrite 无法加载会刷 ``CreateFontFaceFromHDC() failed`` 报错）。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QListWidgetItem
from qfluentwidgets import BodyLabel, ListWidget, MessageBoxBase, SearchLineEdit, SubtitleLabel

# 写法系统 → 优先本地化语言ID（用于为字体挑选「自己语言」的名字显示）
_WS = QFontDatabase.WritingSystem
_WS_LANG_PREF = [
    (_WS.Japanese, (0x0411,)),
    (_WS.Korean, (0x0412,)),
    (_WS.SimplifiedChinese, (0x0804, 0x1004)),
    (_WS.TraditionalChinese, (0x0404, 0x0C04)),
]

_FAMILY_ROLE = Qt.ItemDataRole.UserRole
_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1


def _has_cjk(text: str) -> bool:
    return any(ord(c) > 0x7F for c in text)


def _alias_map() -> dict:
    try:
        from strange_uta_game.frontend.font_names import localized_alias_map

        return localized_alias_map()
    except Exception:
        return {}


def preferred_native(family: str, natives: dict[int, str] | None = None) -> str:
    """按字体支持的书写系统挑选其「母语」名；否则取任一非 ASCII 名，无则空串。"""
    if natives is None:
        natives = _alias_map().get(family, {})
    if not natives:
        return ""
    try:
        ws = set(QFontDatabase.writingSystems(family))
    except Exception:
        ws = set()
    for system, langs in _WS_LANG_PREF:
        if system in ws:
            for lid in langs:
                if lid in natives:
                    return natives[lid]
    for name in natives.values():
        if _has_cjk(name):
            return name
    return ""


def font_display_label(family: str) -> str:
    """字体的友好显示名：有本地化名时为「本地名 (英文族名)」，否则为英文族名。"""
    if not family:
        return ""
    native = preferred_native(family)
    return f"{native}  ({family})" if native and native != family else family


class FontPickerDialog(MessageBoxBase):
    """以遮罩对话框形式选择系统字体。

    用法::

        dlg = FontPickerDialog(current_family, parent=window)
        if dlg.exec():
            family = dlg.selected_family()  # 返回 Qt 可用的族名
    """

    def __init__(self, current: str = "", title: str = "选择字体", parent=None):
        super().__init__(parent)
        self._selected = current or ""
        self._entries = self._build_entries()  # [(family, display, search)]

        self.titleLabel = SubtitleLabel(title, self)
        self.searchEdit = SearchLineEdit(self)
        self.searchEdit.setPlaceholderText("输入字体名称过滤（支持中/日/韩文名）…")
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

        self.yesButton.setText("选用")
        self.cancelButton.setText("取消")

        self._populate(self._entries)
        self._select_family(self._selected)

        self.searchEdit.textChanged.connect(self._on_filter)
        self.listWidget.currentItemChanged.connect(self._on_current_changed)
        self.listWidget.itemDoubleClicked.connect(self._on_double_clicked)

    # ── 构建条目 ──

    def _build_entries(self) -> list[tuple[str, str, str]]:
        """返回 [(qt_family, 显示名, 搜索文本)]，仅含可平滑缩放字体，附本地化名。"""
        alias_map = _alias_map()
        entries: list[tuple[str, str, str]] = []
        for fam in QFontDatabase.families():
            if not QFontDatabase.isSmoothlyScalable(fam):
                continue  # 排除位图字体（Terminal/Fixedsys/System…）
            natives = alias_map.get(fam, {})
            native = preferred_native(fam, natives)
            display = f"{native}  ({fam})" if native and native != fam else fam
            # 搜索文本含 Qt 族名 + 所有本地化名
            search = " ".join([fam, *natives.values()]).lower()
            entries.append((fam, display, search))
        return entries

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
