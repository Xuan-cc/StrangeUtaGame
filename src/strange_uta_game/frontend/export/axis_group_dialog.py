"""轴分组对话框 — 分色分轴编辑（「进入下一步」与导出页轴分组共用）。

把演唱者（分色）分配到 N 个轴分组，供宿主把同一 SUG 拆成多个轴文件、
或 standalone 导出按组拆分输出文件：

- 分组以竖排卡片横向排列：点「添加分组」（行尾虚线幽灵卡片）在最右
  追加新卡片，删除分组后整行向左收缩，始终至少保留 1 组；
- 每组独立勾选演唱者（与导出页过滤器的口径一致），同一演唱者可同时
  勾入多组（其文本同时进入多个轴）；**组内不勾选任何演唱者 = 该轴包含
  全部演唱者**（沿用过滤器「不勾选则导出全部」的口径）；
- 每组必须有组名且不重复（standalone 按组导出时组名用作文件名后缀
  ``_组名``，为空/重复会撞文件名，确认时校验拦截）；
- 必须且只能有一个「主分组」：主分组的导出文件携带完整标签信息
  （@Title 等元数据 + 非歌手 custom 标签），其余组只带本组实际演唱者
  的 @Emoji 触发词标签。

确认时读取各卡片勾选状态生成 ``AxisGroup`` 列表（空勾选的组保留，语义
为全部演唱者）。组名为空/重复时阻止确认。
"""

from __future__ import annotations

from typing import List, Optional, Set, Tuple

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    CaptionLabel,
    CheckBox,
    FluentIcon as FIF,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    RadioButton,
    ScrollArea,
    SimpleCardWidget,
    StrongBodyLabel,
    TransparentToolButton,
    setCustomStyleSheet,
)

from strange_uta_game.backend.domain import AxisGroup
from strange_uta_game.frontend.fluent_widgets import message_warning
from strange_uta_game.frontend.theme import theme
from strange_uta_game.frontend.window_sizing import fit_min_size

# 候选演唱者快照：(singer_id, 显示名, 主色 #RRGGBB)
SingerSnapshot = Tuple[str, str, str]

_CARD_MIN_WIDTH = 176
_CARD_MAX_WIDTH = 208

# 「添加分组」幽灵卡片：透明底 + 虚线框，浅/深两套交给 setCustomStyleSheet
# 随主题自动接管（追加式，保留 qfluentwidgets 自管理的按钮 QSS 其余部分）
_ADD_LIGHT_QSS = """
PushButton {
    border: 1px dashed rgba(0, 0, 0, 0.20);
    border-radius: 8px;
    background: transparent;
    color: rgba(0, 0, 0, 0.50);
    font-weight: normal;
}
PushButton:hover {
    border: 1px solid rgba(0, 0, 0, 0.32);
    background: rgba(0, 0, 0, 0.04);
    color: rgba(0, 0, 0, 0.70);
}
PushButton:pressed {
    background: rgba(0, 0, 0, 0.08);
}
"""

_ADD_DARK_QSS = """
PushButton {
    border: 1px dashed rgba(255, 255, 255, 0.18);
    border-radius: 8px;
    background: transparent;
    color: rgba(255, 255, 255, 0.55);
    font-weight: normal;
}
PushButton:hover {
    border: 1px solid rgba(255, 255, 255, 0.32);
    background: rgba(255, 255, 255, 0.06);
    color: rgba(255, 255, 255, 0.75);
}
PushButton:pressed {
    background: rgba(255, 255, 255, 0.10);
}
"""


def _card_inner_bg() -> QColor:
    """卡片内部的近似底色：演唱者文字色对比度计算的基准。

    SimpleCardWidget 实际是半透明白叠加窗底，这里取主题的三级背景色
    （各主题下输入框等内嵌控件的底色）作为等效不透明近似值即可。
    """
    return theme.bg_tertiary


class _AxisGroupCard(SimpleCardWidget):
    """单张轴分组卡片：可编辑组名 + 删除按钮 + 主分组单选 + 演唱者勾选列表。"""

    remove_requested = pyqtSignal(object)  # 参数为卡片自身
    primary_toggled = pyqtSignal(object)  # 参数为卡片自身

    def __init__(
        self,
        singers: List[SingerSnapshot],
        name: str,
        checked_ids: Set[str],
        is_primary: bool = False,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._singers = singers

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(4)
        self.edit_name = LineEdit()
        self.edit_name.setText(name)
        self.edit_name.setPlaceholderText(self.tr("输入分组名"))
        self.btn_remove = TransparentToolButton(FIF.DELETE)
        self.btn_remove.setToolTip(self.tr("删除该分组"))
        self.btn_remove.clicked.connect(
            lambda: self.remove_requested.emit(self)
        )
        header.addWidget(self.edit_name, 1)
        header.addWidget(self.btn_remove)
        layout.addLayout(header)

        # 主分组单选（跨卡片互斥由对话框统一收口——QRadioButton 的
        # autoExclusive 只在同一父控件内生效，卡片各自独立父控件）
        self.radio_primary = RadioButton(self.tr("主分组"))
        self.radio_primary.setToolTip(
            self.tr("主分组的导出文件携带完整标签信息（标题/演唱者等）；其余组只带本组的 @Emoji 分色标签")
        )
        self.radio_primary.setChecked(is_primary)
        self.radio_primary.toggled.connect(
            lambda checked: checked and self.primary_toggled.emit(self)
        )
        layout.addWidget(self.radio_primary)

        layout.addSpacing(2)
        layout.addWidget(StrongBodyLabel(self.tr("演唱者")))

        self.checkboxes: List[CheckBox] = []
        for singer_id, singer_name, color in singers:
            chk = CheckBox(singer_name)
            chk.setProperty("singer_id", singer_id)
            chk.setProperty("singer_color", color)
            chk.setChecked(singer_id in checked_ids)
            layout.addWidget(chk)
            self.checkboxes.append(chk)
        self._apply_singer_colors()

        layout.addStretch(1)

        self.setMinimumWidth(_CARD_MIN_WIDTH)
        self.setMaximumWidth(_CARD_MAX_WIDTH)
        # 卡片高度贴合内容：配合行布局 AlignTop，消除滚动视口高于内容时
        # 卡片被纵向拉出的底部空洞
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )

        # 深浅主题切换时卡片底色变化，演唱者文字色需重新校正对比度
        theme.changed.connect(self._apply_singer_colors)

    def _apply_singer_colors(self) -> None:
        """重涂演唱者专属文字色（与导出页过滤器同款的追加式覆盖）。

        演唱者主色直接做文字色时，浅色模式下亮色（如黄色）几乎不可读；
        用 ``theme.ensure_contrast`` 对卡片底色校正明度——保留色相、保证
        对比度，勾选框本身的 QSS 仍由 qfluentwidgets 自管理。
        """
        bg = _card_inner_bg()
        for chk in self.checkboxes:
            raw = QColor(chk.property("singer_color"))
            color = theme.ensure_contrast(raw, bg).name()
            qss = f"CheckBox {{ color: {color}; font-weight: bold; }}"
            setCustomStyleSheet(chk, qss, qss)

    def checked_singer_ids(self) -> Set[str]:
        """本组当前勾选的演唱者 ID 集合。"""
        return {
            chk.property("singer_id")
            for chk in self.checkboxes
            if chk.isChecked()
        }

    def group_name(self) -> str:
        """组名（去空白；为空返回空串，由对话框统一校验/回退）。"""
        return self.edit_name.text().strip()


class AxisGroupDialog(QDialog):
    """轴分组编辑弹窗（嵌入式「进入下一步」前置步骤）。

    Args:
        singers: 候选演唱者快照列表 ``(singer_id, name, color)``，
            通常为导出页过滤器同一口径（项目中实际使用且启用的演唱者）。
        initial_groups: 初始分组。传入项目已保存的 ``axis_groups`` 时
            恢复编辑；传 None 时由调用方自行构造（如首组 = 当前过滤器）。
        parent: 父窗口。
    """

    def __init__(
        self,
        singers: List[SingerSnapshot],
        initial_groups: Optional[List[AxisGroup]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(self.tr("轴分组"))
        fit_min_size(self, 760, 520)

        self._singers = singers
        self._cards: List[_AxisGroupCard] = []

        self._init_ui()
        self._apply_theme()
        theme.changed.connect(self._apply_theme)

        primary_assigned = False
        for group in initial_groups or []:
            self._append_card(
                group.name, set(group.singer_ids), bool(group.is_primary)
            )
            primary_assigned = primary_assigned or bool(group.is_primary)
        if not self._cards:
            self._append_card("", set())
            primary_assigned = False
        if self._cards and not primary_assigned:
            self._cards[0].radio_primary.setChecked(True)
        self._sync_remove_buttons()

    # ==================== UI ====================

    def _apply_theme(self) -> None:
        """弹窗底色取主题主背景色，与主界面内容区保持同一色板。"""
        self.setStyleSheet(f"QDialog {{ background: {theme.bg_primary.name()}; }}")

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(12)

        # 标题由窗口标题栏承载，不再重复
        desc = CaptionLabel(self.tr(
            "每组对应一个轴文件：按组导出时文件名追加「_组名」，"
            "主分组的文件携带完整标签信息。\n"
            "同一演唱者可同时进入多组；不勾选任何演唱者的组 = 包含全部演唱者。"
        ))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 卡片行：横向滚动、顶对齐；新增卡片追加在最右，删除后向左收缩
        self._row_widget = QWidget()
        self._row_widget.setObjectName("axisGroupRow")
        self._row_widget.setStyleSheet(
            "#axisGroupRow { background: transparent; }"
        )
        self._row_layout = QHBoxLayout(self._row_widget)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(12)
        self._row_layout.setSizeConstraint(
            QLayout.SizeConstraint.SetMinAndMaxSize
        )
        self._row_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 「添加分组」：行尾虚线幽灵卡片，与分组卡片同宽、顶对齐
        self.btn_add = PushButton(self.tr("添加分组"))
        self.btn_add.setIcon(FIF.ADD)
        self.btn_add.setFixedSize(_CARD_MIN_WIDTH, 52)
        self.btn_add.setCursor(Qt.CursorShape.PointingHandCursor)
        setCustomStyleSheet(self.btn_add, _ADD_LIGHT_QSS, _ADD_DARK_QSS)
        self.btn_add.clicked.connect(self._on_add_group)
        self._row_layout.addWidget(self.btn_add)
        self._row_layout.addStretch(1)

        scroll = ScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setWidget(self._row_widget)
        scroll.setStyleSheet("QScrollArea { border: none; background: transparent; }")
        layout.addWidget(scroll, 1)

        # 底部：确认 / 取消
        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch()
        btn_ok = PrimaryPushButton(self.tr("确认"), self)
        btn_ok.clicked.connect(self._on_accept)
        btn_cancel = PushButton(self.tr("取消"), self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _append_card(
        self, name: str, checked_ids: Set[str], is_primary: bool = False
    ) -> None:
        """在行尾（「添加分组」幽灵卡片之前）追加一张分组卡片。"""
        default_name = name or self._next_default_name()
        card = _AxisGroupCard(
            self._singers, default_name, checked_ids, is_primary
        )
        card.remove_requested.connect(self._on_remove_card)
        card.primary_toggled.connect(self._on_card_primary_toggled)
        # 行结构恒为 [卡片×N, 添加按钮, stretch]；新卡片插在按钮前 = 最右卡片位
        self._row_layout.insertWidget(len(self._cards), card)
        self._cards.append(card)

    def _on_card_primary_toggled(self, card: "_AxisGroupCard") -> None:
        """主分组跨卡片互斥：选中一张时取消其余所有。"""
        for other in self._cards:
            if other is not card:
                other.radio_primary.setChecked(False)

    def _ensure_primary(self) -> None:
        """删除主分组后把主分组身份移交首张卡片。"""
        if self._cards and not any(
            c.radio_primary.isChecked() for c in self._cards
        ):
            self._cards[0].radio_primary.setChecked(True)

    def _on_add_group(self) -> None:
        """「添加分组」：在最右追加一张空卡片，并聚焦其组名输入框。"""
        self._append_card("", set())
        self._sync_remove_buttons()
        self._cards[-1].edit_name.setFocus()
        self._cards[-1].edit_name.selectAll()

    def _on_remove_card(self, card: "_AxisGroupCard") -> None:
        """删除一张卡片；整行向左收缩，但至少保留 1 组。"""
        if len(self._cards) <= 1:
            return
        self._cards.remove(card)
        self._row_layout.removeWidget(card)
        card.deleteLater()
        self._sync_remove_buttons()
        self._ensure_primary()

    def _sync_remove_buttons(self) -> None:
        """只剩 1 组时禁用所有删除按钮（最低保留 1 组）。"""
        single = len(self._cards) <= 1
        for card in self._cards:
            card.btn_remove.setEnabled(not single)
            card.btn_remove.setVisible(not single)

    def _next_default_name(self) -> str:
        """生成下一个不与现有组名冲突的默认名「轴N」。"""
        used = {c.group_name() for c in self._cards}
        n = 1
        while f"轴{n}" in used:
            n += 1
        return f"轴{n}"

    # ==================== 结果读取 ====================

    def get_axis_groups(self) -> List[AxisGroup]:
        """读取各卡片为 ``AxisGroup`` 列表。

        空勾选的组保留（singer_ids 为空 = 全部演唱者，沿用过滤器口径）；
        组名原样返回（可能为空）——空名/重名由 :meth:`_on_accept` 校验
        拦截，不在读取层悄悄补名。
        """
        groups: List[AxisGroup] = []
        for card in self._cards:
            singer_ids = card.checked_singer_ids()
            groups.append(
                AxisGroup(
                    name=card.group_name(),
                    singer_ids=[
                        sid for sid, _, _ in self._singers if sid in singer_ids
                    ],
                    is_primary=card.radio_primary.isChecked(),
                )
            )
        return groups

    # ==================== 确认 ====================

    def _on_accept(self) -> None:
        groups = self.get_axis_groups()
        names = [g.name for g in groups]
        if any(not n for n in names):
            message_warning(
                self,
                self.tr("分组名不能为空"),
                self.tr("每个分组都需要填写组名（按组导出时用作文件名后缀）。"),
            )
            return
        duplicated = {n for n in names if names.count(n) > 1}
        if duplicated:
            message_warning(
                self,
                self.tr("分组名不能重复"),
                self.tr("存在重复的分组名：{names}").format(
                    names="、".join(sorted(duplicated))
                ),
            )
            return
        self.accept()
