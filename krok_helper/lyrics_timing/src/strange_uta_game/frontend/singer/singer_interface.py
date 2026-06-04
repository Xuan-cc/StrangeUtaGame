"""演唱者管理界面。

管理演唱者的添加、删除、重命名、颜色设置、顺序调整等。

主要交互：
- 多选（Ctrl/Shift）→ 批量删除 / 启用 / 禁用 / 上移 / 下移 / 置顶 / 置底
- Qt 原生拖放调整顺序
- 顶部常驻搜索框过滤（过滤期间禁用拖放与顺序按钮，避免操作隐藏项）
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QColorDialog,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QAbstractItemView,
    QComboBox,
    QRadioButton,
    QButtonGroup,
)
from PyQt6.QtCore import Qt, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap, QPainter
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    ListWidget,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    CaptionLabel,
)

from typing import Optional, List, Set

# 分组过滤哨兵：表示「只显示无分组演唱者」，与「全部」("") 区分
_FILTER_NO_GROUP = "\x00nogroup"

from strange_uta_game.backend.domain import Project, Singer
from strange_uta_game.backend.application import SingerService
from strange_uta_game.backend.domain.entities import _compute_complement_color


def _make_singer_icon(colors: List[str], w: int = 32, h: int = 18):
    """生成演唱者分色预览图标（用于列表 item icon）"""
    from PyQt6.QtGui import QIcon
    pixmap = QPixmap(w, h)
    p = QPainter(pixmap)
    n = len(colors) if colors else 1
    for i, c in enumerate(colors):
        y0 = int(i * h / n)
        y1 = int((i + 1) * h / n)
        p.fillRect(QRect(0, y0, w, y1 - y0), QColor(c))
    p.end()
    return QIcon(pixmap)


class SingerEditDialog(QDialog):
    """演唱者编辑对话框，支持单色与分色（最多5色）模式"""

    MAX_COLORS = 5

    def __init__(
        self,
        singer: Singer = None,
        existing_groups: List[str] = None,
        existing_singers: List[Singer] = None,
        parent=None,
    ):
        super().__init__(parent)
        self._singer = singer
        self._color = singer.color if singer else "#FF6B6B"
        self._color_mode = singer.color_mode if singer else "solid"
        self._split_colors: List[str] = list(singer.split_colors) if singer else []
        self._existing_groups = existing_groups or []
        # 可用于"加载颜色"的演唱者列表（排除自身）
        self._existing_singers: List[Singer] = [
            s for s in (existing_singers or [])
            if not singer or s.id != singer.id
        ]

        self.setWindowTitle("编辑演唱者" if singer else "添加演唱者")
        self.resize(340, 300)
        self._init_ui()

    # ── 初始化 ────────────────────────────────────────────────────────────

    def _init_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(8)

        # 名称 / 分组
        form = QFormLayout()
        form.setSpacing(6)

        self.line_name = LineEdit()
        if self._singer:
            self.line_name.setText(self._singer.name)
        else:
            self.line_name.setPlaceholderText("输入演唱者名称（留空自动编号）...")
        form.addRow("显示名称:", self.line_name)

        # 分组：可编辑下拉，选项来自项目已有分组
        self.combo_group = QComboBox()
        self.combo_group.setEditable(True)
        self.combo_group.addItem("")          # 空 = 无分组
        for g in self._existing_groups:
            self.combo_group.addItem(g)
        current_group = self._singer.group if self._singer else ""
        self.combo_group.setCurrentText(current_group)
        self.combo_group.lineEdit().setPlaceholderText("留空为默认分组")
        form.addRow("分组:", self.combo_group)

        # 从已有演唱者加载颜色（仅在有其他演唱者时显示）
        if self._existing_singers:
            self._btn_load_color = PushButton("从已有演唱者加载颜色…")
            self._btn_load_color.clicked.connect(self._on_load_from_singer)
            form.addRow("颜色来源:", self._btn_load_color)

        # 颜色模式
        mode_widget = QWidget()
        mode_layout = QHBoxLayout(mode_widget)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        self._rb_solid = QRadioButton("单色")
        self._rb_split = QRadioButton("分色（最多5色）")
        mode_grp = QButtonGroup(self)
        mode_grp.addButton(self._rb_solid, 0)
        mode_grp.addButton(self._rb_split, 1)
        (self._rb_split if self._color_mode == "split" else self._rb_solid).setChecked(True)
        self._rb_solid.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self._rb_solid)
        mode_layout.addWidget(self._rb_split)
        mode_layout.addStretch()
        form.addRow("颜色模式:", mode_widget)
        outer.addLayout(form)

        # ── 单色面板 ──
        self._solid_panel = QWidget()
        sp_layout = QHBoxLayout(self._solid_panel)
        sp_layout.setContentsMargins(0, 0, 0, 0)
        self._lbl_solid_preview = QLabel()
        self._lbl_solid_preview.setFixedSize(40, 28)
        self._refresh_solid_swatch()
        btn_solid_pick = PushButton("选择颜色...")
        btn_solid_pick.clicked.connect(self._on_pick_solid)
        sp_layout.addWidget(self._lbl_solid_preview)
        sp_layout.addWidget(btn_solid_pick)
        sp_layout.addStretch()
        outer.addWidget(self._solid_panel)

        # ── 分色面板 ──
        self._split_panel = QWidget()
        split_layout = QVBoxLayout(self._split_panel)
        split_layout.setContentsMargins(0, 0, 0, 0)
        split_layout.setSpacing(4)

        self._split_rows_widget = QWidget()
        self._split_rows_layout = QVBoxLayout(self._split_rows_widget)
        self._split_rows_layout.setContentsMargins(0, 0, 0, 0)
        self._split_rows_layout.setSpacing(4)
        split_layout.addWidget(self._split_rows_widget)

        self._btn_add_split = PushButton("+ 添加颜色")
        self._btn_add_split.clicked.connect(self._on_add_split_color)
        split_layout.addWidget(self._btn_add_split)

        # 分色预览条（水平条，从上到下展示各色带）
        preview_label = QLabel("预览：")
        split_layout.addWidget(preview_label)
        self._lbl_split_preview = QLabel()
        self._lbl_split_preview.setFixedHeight(36)
        self._lbl_split_preview.setMinimumWidth(200)
        split_layout.addWidget(self._lbl_split_preview)

        outer.addWidget(self._split_panel)

        # 初始化分色行
        self._rebuild_split_rows()

        # 默认演唱者
        self.chk_default = QPushButton("设为默认演唱者")
        self.chk_default.setCheckable(True)
        if self._singer and self._singer.is_default:
            self.chk_default.setChecked(True)
        outer.addWidget(self.chk_default)

        # 确定 / 取消
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        outer.addWidget(button_box)

        self._update_panel_visibility()

    # ── 模式切换 ──────────────────────────────────────────────────────────

    def _update_panel_visibility(self):
        is_split = self._rb_split.isChecked()
        self._solid_panel.setVisible(not is_split)
        self._split_panel.setVisible(is_split)
        self.adjustSize()

    def _on_mode_changed(self):
        self._color_mode = "split" if self._rb_split.isChecked() else "solid"
        if self._color_mode == "split" and not self._split_colors:
            # 切换到分色时自动补一个对比色
            from strange_uta_game.backend.domain.entities import _compute_complement_color
            self._split_colors = [_compute_complement_color(self._color)]
            self._rebuild_split_rows()
        self._update_panel_visibility()

    # ── 单色面板 ──────────────────────────────────────────────────────────

    def _refresh_solid_swatch(self):
        self._lbl_solid_preview.setStyleSheet(
            f"background-color: {self._color}; border: 1px solid gray;"
        )

    def _on_pick_solid(self):
        color = QColorDialog.getColor(QColor(self._color), self, "选择演唱者颜色")
        if color.isValid():
            self._color = color.name()
            self._refresh_solid_swatch()

    # ── 分色面板 ──────────────────────────────────────────────────────────

    def _all_split_colors(self) -> List[str]:
        return [self._color] + self._split_colors

    def _rebuild_split_rows(self):
        while self._split_rows_layout.count():
            item = self._split_rows_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        all_colors = self._all_split_colors()
        n = len(all_colors)
        for i, color in enumerate(all_colors):
            self._split_rows_layout.addWidget(self._make_split_row(i, color, n))

        self._btn_add_split.setEnabled(n < self.MAX_COLORS)
        self._refresh_split_preview()

    def _make_split_row(self, idx: int, color: str, total: int) -> QWidget:
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)

        swatch = QLabel()
        swatch.setFixedSize(40, 24)
        swatch.setStyleSheet(f"background-color: {color}; border: 1px solid gray;")

        btn_pick = PushButton(f"颜色 {idx + 1}")
        btn_pick.setFixedWidth(80)
        btn_pick.clicked.connect(lambda _checked, i=idx: self._on_pick_split_color(i))

        row_layout.addWidget(swatch)
        row_layout.addWidget(btn_pick)

        if total > 2:
            btn_del = QPushButton("✕")
            btn_del.setFixedSize(24, 24)
            btn_del.clicked.connect(lambda _checked, i=idx: self._on_remove_split_color(i))
            row_layout.addWidget(btn_del)

        row_layout.addStretch()
        return row

    def _on_pick_split_color(self, idx: int):
        all_colors = self._all_split_colors()
        current = all_colors[idx] if idx < len(all_colors) else "#FFFFFF"
        color = QColorDialog.getColor(QColor(current), self, f"选择颜色 {idx + 1}")
        if not color.isValid():
            return
        if idx == 0:
            self._color = color.name()
        else:
            self._split_colors[idx - 1] = color.name()
        self._rebuild_split_rows()

    def _on_remove_split_color(self, idx: int):
        if idx == 0:
            if self._split_colors:
                self._color = self._split_colors.pop(0)
        else:
            del self._split_colors[idx - 1]
        self._rebuild_split_rows()

    def _on_add_split_color(self):
        if len(self._all_split_colors()) >= self.MAX_COLORS:
            return
        from strange_uta_game.backend.domain.entities import _compute_complement_color
        new_color = _compute_complement_color(self._all_split_colors()[-1])
        self._split_colors.append(new_color)
        self._rebuild_split_rows()

    def _refresh_split_preview(self):
        all_colors = self._all_split_colors()
        w = max(self._lbl_split_preview.width(), 200)
        h = 36
        pixmap = QPixmap(w, h)
        p = QPainter(pixmap)
        n = len(all_colors)
        for i, hex_c in enumerate(all_colors):
            y0 = int(i * h / n)
            y1 = int((i + 1) * h / n)
            p.fillRect(QRect(0, y0, w, y1 - y0), QColor(hex_c))
        p.end()
        self._lbl_split_preview.setPixmap(pixmap)

    # ── 从已有演唱者加载颜色 ─────────────────────────────────────────────

    def _on_load_from_singer(self):
        """弹出菜单，从已有演唱者中选择颜色配置复制过来"""
        from PyQt6.QtWidgets import QMenu
        from PyQt6.QtGui import QIcon

        menu = QMenu(self)
        for s in self._existing_singers:
            icon = QIcon(_make_singer_icon(s.get_all_colors(), 32, 18))
            label = s.name
            if s.group:
                label += f"  [{s.group}]"
            action = menu.addAction(icon, label)
            action.setData(s)

        chosen = menu.exec(self._btn_load_color.mapToGlobal(
            self._btn_load_color.rect().bottomLeft()
        ))
        if not chosen:
            return

        src: Singer = chosen.data()
        self._color = src.color
        self._color_mode = src.color_mode
        self._split_colors = list(src.split_colors)

        # 同步 UI
        if self._color_mode == "split":
            self._rb_split.setChecked(True)
        else:
            self._rb_solid.setChecked(True)
        self._refresh_solid_swatch()
        self._rebuild_split_rows()
        self._update_panel_visibility()

    # ── 数据获取 ──────────────────────────────────────────────────────────

    def get_data(self) -> dict:
        return {
            "name": self.line_name.text().strip(),
            "color": self._color,
            "color_mode": self._color_mode,
            "split_colors": list(self._split_colors),
            "is_default": self.chk_default.isChecked(),
            "group": self.combo_group.currentText().strip(),
        }


class BatchGroupDialog(QDialog):
    """批量设置演唱者分组的对话框"""

    def __init__(self, existing_groups: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量设置分组")
        self.resize(320, 130)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("选择或输入分组名称（留空则清除分组）："))

        self.combo = QComboBox(self)
        self.combo.setEditable(True)
        self.combo.addItem("", "")
        for g in existing_groups:
            self.combo.addItem(g, g)
        self.combo.setCurrentIndex(0)
        layout.addWidget(self.combo)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_group(self) -> str:
        return self.combo.currentText().strip()


class TransferTargetDialog(QDialog):
    """批量删除时选择转移目标的对话框"""

    def __init__(
        self,
        candidates: List[Singer],
        default_id: Optional[str] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("选择转移目标")
        self.resize(360, 160)

        self._candidates = candidates

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel("被删除演唱者的歌词将转移到以下演唱者：")
        )

        self.combo = QComboBox(self)
        for s in candidates:
            label = s.name + ("（默认）" if s.is_default else "")
            self.combo.addItem(label, s.id)
        if default_id:
            idx = self.combo.findData(default_id)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
        layout.addWidget(self.combo)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.button(QDialogButtonBox.StandardButton.Ok).setText("确定")
        button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def get_target_id(self) -> Optional[str]:
        return self.combo.currentData()


class SingerPresetLoadDialog(QDialog):
    """从软件预设加载演唱者的多选对话框（用 CheckState 驱动选中，避免 MultiSelection UI 刷新 BUG）"""

    def __init__(self, presets: list, existing_names: set, app_settings=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从软件预设加载演唱者")
        self.resize(400, 450)

        self._presets = presets
        self._existing_names = existing_names
        self._app_settings = app_settings

        self._init_ui()
        self._populate_list()

        self.list_widget.itemChanged.connect(self._update_stats)
        self._current_group_filter: str = ""

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # 搜索过滤框
        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("过滤:"))
        self.line_filter = LineEdit()
        self.line_filter.setPlaceholderText("输入名称搜索...")
        self.line_filter.textChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.line_filter)

        filter_layout.addWidget(QLabel("分组:"))
        self.combo_group = QComboBox(self)
        self.combo_group.addItem("全部分组", "")
        # 从预设中收集所有分组
        groups = sorted(set(p.get("group", "") for p in self._presets if p.get("group", "")))
        if any(not p.get("group", "") for p in self._presets):
            self.combo_group.addItem("（无分组）", _FILTER_NO_GROUP)
        for g in groups:
            self.combo_group.addItem(g, g)
        self.combo_group.currentIndexChanged.connect(self._apply_filter)
        filter_layout.addWidget(self.combo_group)

        layout.addLayout(filter_layout)

        # 演唱者列表（NoSelection，选中状态完全由 CheckState 驱动）
        self.list_widget = ListWidget()
        self.list_widget.setIconSize(QSize(32, 18))
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.NoSelection
        )
        layout.addWidget(self.list_widget)

        # 全选/全不选按钮
        select_layout = QHBoxLayout()
        btn_select_all = PushButton("全选", self)
        btn_select_all.clicked.connect(self._on_select_all)
        select_layout.addWidget(btn_select_all)

        btn_deselect_all = PushButton("全不选", self)
        btn_deselect_all.clicked.connect(self._on_deselect_all)
        select_layout.addWidget(btn_deselect_all)

        select_layout.addStretch()

        # 统计标签
        self.lbl_stats = CaptionLabel("")
        select_layout.addWidget(self.lbl_stats)
        layout.addLayout(select_layout)

        # 按钮行
        button_layout = QHBoxLayout()
        
        # 删除选中演唱者按钮
        self.btn_delete_selected = PushButton("删除选中演唱者", self)
        self.btn_delete_selected.setIcon(FIF.DELETE)
        self.btn_delete_selected.clicked.connect(self._on_delete_selected)
        button_layout.addWidget(self.btn_delete_selected)
        
        button_layout.addStretch()
        
        # 加载选中按钮
        btn_load = PushButton("加载选中", self)
        btn_load.setIcon(FIF.DOWNLOAD)
        btn_load.clicked.connect(self._on_accept)
        button_layout.addWidget(btn_load)
        
        # 取消按钮
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(btn_cancel)
        
        layout.addLayout(button_layout)

    def _populate_list(self):
        """填充列表"""
        # 同步更新分组过滤下拉
        self.combo_group.blockSignals(True)
        saved_group = self.combo_group.currentData()
        self.combo_group.clear()
        self.combo_group.addItem("全部分组", "")
        groups = sorted(set(p.get("group", "") for p in self._presets if p.get("group", "")))
        has_no_group = any(not p.get("group", "") for p in self._presets)
        if has_no_group:
            self.combo_group.addItem("（无分组）", _FILTER_NO_GROUP)
        for g in groups:
            self.combo_group.addItem(g, g)
        idx = self.combo_group.findData(saved_group)
        self.combo_group.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_group.blockSignals(False)

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for preset in self._presets:
            name = preset.get("name", "")
            if not name:
                continue

            is_existing = name in self._existing_names
            color = preset.get("color", "#FF6B6B")
            color_mode = preset.get("color_mode", "solid")
            split_colors = preset.get("split_colors", [])
            group = preset.get("group", "")

            all_colors = ([color] + split_colors) if color_mode == "split" and split_colors else [color]

            item = QListWidgetItem()
            display_name = f"{name} [{group}]" if group else name
            item.setText(f"{display_name}  (已存在)" if is_existing else display_name)

            # 颜色图标（支持分色预览）
            item.setIcon(_make_singer_icon(all_colors, 32, 18))

            # 演唱者主色作为半透明背景
            bg_color = QColor(color)
            bg_color.setAlpha(80)
            item.setBackground(bg_color)

            if is_existing:
                item.setForeground(QColor("gray"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setCheckState(Qt.CheckState.Unchecked)
            else:
                # 打开后默认全不选，上次commit提交漏拆分，使用注释提示。
                item.setCheckState(Qt.CheckState.Unchecked)

            item.setData(Qt.ItemDataRole.UserRole, preset)
            item.setData(Qt.ItemDataRole.UserRole + 1, is_existing)

            self.list_widget.addItem(item)

        self.list_widget.blockSignals(False)
        self._update_stats()

    def _apply_filter(self):
        """过滤列表（名称 + 分组），只控制显示/隐藏，不影响勾选状态"""
        filter_text = self.line_filter.text().strip().lower()
        group_filter = self.combo_group.currentData() or ""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            preset = item.data(Qt.ItemDataRole.UserRole)
            name = preset.get("name", "").lower()
            group = preset.get("group", "")
            name_match = not filter_text or filter_text in name
            if group_filter == _FILTER_NO_GROUP:
                group_match = not group
            elif group_filter:
                group_match = group == group_filter
            else:
                group_match = True
            item.setHidden(not (name_match and group_match))
        self._update_stats()

    def _on_select_all(self):
        """全选可见且未存在的项"""
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden() and not item.data(Qt.ItemDataRole.UserRole + 1):
                item.setCheckState(Qt.CheckState.Checked)
        self.list_widget.blockSignals(False)
        self._update_stats()

    def _on_deselect_all(self):
        """取消勾选所有可见项"""
        self.list_widget.blockSignals(True)
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden() and not item.data(Qt.ItemDataRole.UserRole + 1):
                item.setCheckState(Qt.CheckState.Unchecked)
        self.list_widget.blockSignals(False)
        self._update_stats()

    # 旧名称兼容（不需要对外暴露，但防止万一有其他地方连接）
    def _on_filter_changed(self, text: str = ""):
        self._apply_filter()

    def _update_stats(self):
        """更新统计信息（总勾选数/总数）"""
        checked = 0
        total = 0
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            total += 1
            if item.checkState() == Qt.CheckState.Checked:
                checked += 1
        self.lbl_stats.setText(f"已选 {checked}/{total}")

    def _on_delete_selected(self):
        """删除选中的演唱者预设"""
        # 获取选中的预设
        selected_presets = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                preset = item.data(Qt.ItemDataRole.UserRole)
                if preset:
                    selected_presets.append(preset)
        
        if not selected_presets:
            InfoBar.warning(title="未选择", content="请至少选择一位演唱者",
                            parent=self, duration=2000)
            return
        
        # 二次确认
        names = "、".join(p.get("name", "") for p in selected_presets[:5])
        if len(selected_presets) > 5:
            names += f" 等 {len(selected_presets)} 位"
        
        msg = QMessageBox(self)
        msg.setWindowTitle("确认删除")
        msg.setText(f"确定要从预设中删除以下演唱者吗？\n\n{names}\n\n删除后将无法恢复。")
        btn_yes = msg.addButton("删除", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        
        if msg.clickedButton() is not btn_yes:
            return
        
        # 从预设中删除选中的演唱者
        if not self._app_settings:
            InfoBar.error(title="错误", content="无法访问设置",
                          parent=self, duration=3000)
            return
        
        try:
            # 获取当前预设
            current_presets = self._app_settings.load_singer_presets()
            
            # 按 (name, group) 精确匹配删除，避免误删同名不同分组的预设
            keys_to_delete = {(p.get("name", ""), p.get("group", "")) for p in selected_presets}
            updated_presets = [
                p for p in current_presets
                if (p.get("name", ""), p.get("group", "")) not in keys_to_delete
            ]
            
            # 保存更新后的预设
            self._app_settings.save_singer_presets(updated_presets)
            
            # 更新本地预设列表
            self._presets = updated_presets
            
            # 重新填充列表
            self._populate_list()
            
            InfoBar.success(title="删除成功", content=f"已删除 {len(selected_presets)} 位演唱者预设",
                            parent=self, duration=2000)
        except Exception as e:
            InfoBar.error(title="删除失败", content=f"保存预设时出错: {e}",
                          parent=self, duration=3000)

    def _on_accept(self):
        """确认选择"""
        has_checked = any(
            self.list_widget.item(i).checkState() == Qt.CheckState.Checked
            for i in range(self.list_widget.count())
        )
        if not has_checked:
            InfoBar.warning(title="未选择", content="请至少选择一位演唱者",
                            parent=self, duration=2000)
            return
        self.accept()

    def get_selected_presets(self) -> list:
        """获取勾选的预设列表"""
        result = []
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                preset = item.data(Qt.ItemDataRole.UserRole)
                if preset:
                    result.append(preset)
        return result


class SingerColorPreviewPanel(QWidget):
    """独立的演唱者颜色预览面板，不受列表背景色影响"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedWidth(176)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title = QLabel("颜色预览")
        title.setStyleSheet("font-size: 13px; font-weight: bold; color: #888;")
        layout.addWidget(title)

        self._swatch = QLabel()
        self._swatch.setMinimumSize(152, 96)
        self._swatch.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._swatch)

        self._name_label = QLabel("未选中")
        self._name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_label.setWordWrap(True)
        self._name_label.setStyleSheet("color: #888; font-size: 12px;")
        layout.addWidget(self._name_label)

        layout.addStretch()
        self._clear()

    def set_preview(self, name: str, colors: list):
        self._name_label.setText(name)
        self._name_label.setStyleSheet("color: #CCC; font-size: 12px;")

        w = max(self._swatch.width(), 152)
        h = max(self._swatch.height(), 96)
        pixmap = QPixmap(w, h)
        pixmap.fill(QColor("#2B2B2B"))

        if colors:
            p = QPainter(pixmap)
            n = len(colors)
            pen_w = 2
            for i, c in enumerate(colors):
                t = int(i * (h - pen_w * 2) / n) + pen_w
                b = int((i + 1) * (h - pen_w * 2) / n) + pen_w
                p.fillRect(QRect(pen_w, t, w - pen_w * 2, b - t), QColor(c))
            p.end()

        self._swatch.setPixmap(pixmap)

    def set_multiple(self, count: int):
        self._name_label.setText(f"已选中 {count} 位")
        self._name_label.setStyleSheet("color: #888; font-size: 12px;")
        self._clear_swatch()

    def _clear(self):
        self._name_label.setText("未选中")
        self._name_label.setStyleSheet("color: #888; font-size: 12px;")
        self._clear_swatch()

    def _clear_swatch(self):
        pixmap = QPixmap(152, 96)
        pixmap.fill(QColor("#2B2B2B"))
        self._swatch.setPixmap(pixmap)


class SingerManagerInterface(QWidget):
    """演唱者管理界面"""

    singers_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self._project: Optional[Project] = None
        self._singer_service: Optional[SingerService] = None
        # 防止 reorder 引发的 model 信号回环触发自身
        self._suppress_reorder_signal = False
        # 当前搜索关键词（用于禁用顺序按钮）
        self._filter_text: str = ""
        # 当前分组过滤
        self._group_filter: str = ""
        # 持久选中状态：跨搜索/刷新保留，以 singer.id 为键
        self._selected_ids: Set[str] = set()

        self._init_ui()

    def _init_ui(self):
        """初始化界面"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)

        # 标题
        title = QLabel("演唱者管理")
        title.setStyleSheet("font-size: 24px; font-weight: bold;")
        layout.addWidget(title)

        # 说明
        desc = CaptionLabel(
            "管理演唱者：双击编辑；Ctrl/Shift 多选可批量操作；拖动可调整顺序。"
        )
        layout.addWidget(desc)

        # 搜索框 + 分组过滤（常驻）
        search_row = QHBoxLayout()
        self.line_search = LineEdit()
        self.line_search.setPlaceholderText("搜索演唱者名称...")
        self.line_search.setClearButtonEnabled(True)
        self.line_search.textChanged.connect(self._on_search_changed)
        search_row.addWidget(self.line_search)

        search_row.addWidget(QLabel("分组:"))
        self.combo_group_filter = QComboBox(self)
        self.combo_group_filter.addItem("全部", "")
        self.combo_group_filter.setMinimumWidth(90)
        self.combo_group_filter.currentIndexChanged.connect(self._on_group_filter_changed)
        search_row.addWidget(self.combo_group_filter)

        layout.addLayout(search_row)

        # 演唱者列表 + 独立颜色预览面板
        list_row = QHBoxLayout()

        self.list_singers = ListWidget()
        self.list_singers.setMinimumHeight(260)
        self.list_singers.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        self.list_singers.setDragDropMode(
            QAbstractItemView.DragDropMode.InternalMove
        )
        self.list_singers.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_singers.itemDoubleClicked.connect(self._on_edit_singer)
        self.list_singers.itemSelectionChanged.connect(self._on_selection_changed)
        # 拖放完成后，rowsMoved 由内部模型发出
        self.list_singers.model().rowsMoved.connect(self._on_rows_moved)
        list_row.addWidget(self.list_singers, stretch=1)

        self._color_preview_panel = SingerColorPreviewPanel(self)
        list_row.addWidget(self._color_preview_panel)

        layout.addLayout(list_row, stretch=1)

        # ── 第一排按钮：常规操作 ──
        row1 = QHBoxLayout()

        self.btn_add = PrimaryPushButton("添加", self)
        self.btn_add.setIcon(FIF.ADD)
        self.btn_add.clicked.connect(self._on_add_singer)
        row1.addWidget(self.btn_add)

        self.btn_edit = PushButton("编辑", self)
        self.btn_edit.setIcon(FIF.EDIT)
        self.btn_edit.clicked.connect(self._on_edit_singer)
        self.btn_edit.setEnabled(False)
        row1.addWidget(self.btn_edit)

        self.btn_delete = PushButton("删除", self)
        self.btn_delete.setIcon(FIF.DELETE)
        self.btn_delete.clicked.connect(self._on_delete_singers)
        self.btn_delete.setEnabled(False)
        row1.addWidget(self.btn_delete)

        # 分隔
        row1.addSpacing(10)

        self.btn_set_group = PushButton("设置分组", self)
        self.btn_set_group.setIcon(FIF.TAG)
        self.btn_set_group.clicked.connect(self._on_set_group)
        self.btn_set_group.setEnabled(False)
        row1.addWidget(self.btn_set_group)

        row1.addSpacing(10)

        self.btn_enable = PushButton("启用", self)
        self.btn_enable.setIcon(FIF.ACCEPT)
        self.btn_enable.clicked.connect(lambda: self._on_set_enabled(True))
        self.btn_enable.setEnabled(False)
        row1.addWidget(self.btn_enable)

        self.btn_disable = PushButton("禁用", self)
        self.btn_disable.setIcon(FIF.CLOSE)
        self.btn_disable.clicked.connect(lambda: self._on_set_enabled(False))
        self.btn_disable.setEnabled(False)
        row1.addWidget(self.btn_disable)

        row1.addStretch()
        layout.addLayout(row1)

        # ── 第二排按钮：顺序调整 ──
        row2 = QHBoxLayout()

        self.btn_top = PushButton("置顶", self)
        self.btn_top.setIcon(FIF.UP)
        self.btn_top.clicked.connect(lambda: self._on_move("top"))
        self.btn_top.setEnabled(False)
        row2.addWidget(self.btn_top)

        self.btn_up = PushButton("上移", self)
        self.btn_up.setIcon(FIF.UP)
        self.btn_up.clicked.connect(lambda: self._on_move("up"))
        self.btn_up.setEnabled(False)
        row2.addWidget(self.btn_up)

        self.btn_down = PushButton("下移", self)
        self.btn_down.setIcon(FIF.DOWN)
        self.btn_down.clicked.connect(lambda: self._on_move("down"))
        self.btn_down.setEnabled(False)
        row2.addWidget(self.btn_down)

        self.btn_bottom = PushButton("置底", self)
        self.btn_bottom.setIcon(FIF.DOWN)
        self.btn_bottom.clicked.connect(lambda: self._on_move("bottom"))
        self.btn_bottom.setEnabled(False)
        row2.addWidget(self.btn_bottom)

        row2.addSpacing(20)

        # 预设按钮挪到这一排尾部
        self.btn_save_preset = PushButton("保存为软件预设", self)
        self.btn_save_preset.setIcon(FIF.SAVE)
        self.btn_save_preset.setToolTip(
            "将当前演唱者列表保存到软件设置，每次启动自动加载"
        )
        self.btn_save_preset.clicked.connect(self._on_save_preset)
        row2.addWidget(self.btn_save_preset)

        self.btn_load_preset = PushButton("从软件预设加载", self)
        self.btn_load_preset.setIcon(FIF.DOWNLOAD)
        self.btn_load_preset.setToolTip("从软件设置中加载已保存的演唱者预设到当前项目")
        self.btn_load_preset.clicked.connect(self._on_load_preset)
        row2.addWidget(self.btn_load_preset)

        row2.addStretch()
        layout.addLayout(row2)

        # 统计信息
        self.lbl_stats = CaptionLabel("共 0 位演唱者")
        layout.addWidget(self.lbl_stats)

    # ==================== 数据接入 ====================

    def set_project(self, project: Project):
        """设置项目"""
        self._project = project
        self._singer_service = SingerService(project)
        self._selected_ids.clear()
        self._refresh_list()

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        if change_type == "project":
            project = self._store.project
            if project:
                self._project = project
                self._singer_service = SingerService(project)
            else:
                self._project = None
                self._singer_service = None
            self._selected_ids.clear()
            self._refresh_list()
        elif change_type == "singers":
            self._refresh_list()

    # ==================== 列表刷新 ====================

    def _refresh_list(self):
        """刷新演唱者列表（保持当前选中、滚动位置不变）"""
        # 抑制 rowsMoved 信号（清空/填充时不触发 reorder 回调）
        self._suppress_reorder_signal = True
        try:
            # 动态更新分组过滤下拉（保留当前选中分组）
            if self._project:
                self.combo_group_filter.blockSignals(True)
                all_groups = sorted(set(s.group for s in self._project.singers if s.group))
                has_no_group = any(not s.group for s in self._project.singers)
                current_group = self._group_filter
                self.combo_group_filter.clear()
                self.combo_group_filter.addItem("全部", "")
                if has_no_group:
                    self.combo_group_filter.addItem("（无分组）", _FILTER_NO_GROUP)
                for g in all_groups:
                    self.combo_group_filter.addItem(g, g)
                idx = self.combo_group_filter.findData(current_group)
                self.combo_group_filter.setCurrentIndex(idx if idx >= 0 else 0)
                if idx < 0:
                    self._group_filter = ""
                self.combo_group_filter.blockSignals(False)

            self.list_singers.clear()

            if not self._project:
                self.lbl_stats.setText("未加载项目")
                self._update_button_state()
                return

            filter_lower = self._filter_text.strip().lower()

            for singer in self._project.singers:
                # 名称过滤
                if filter_lower and filter_lower not in singer.name.lower():
                    continue
                # 分组过滤
                if self._group_filter == _FILTER_NO_GROUP:
                    if singer.group:
                        continue
                elif self._group_filter and singer.group != self._group_filter:
                    continue

                item = QListWidgetItem()

                # 显示格式: [后台编号] 名称 (分组) [默认] (已禁用)
                display_text = singer.name
                if singer.group:
                    display_text += f" ({singer.group})"
                if singer.is_default:
                    display_text += " [默认]"
                if not singer.enabled:
                    display_text += " (已禁用)"

                item.setText(display_text)

                # 存储演唱者 ID
                item.setData(Qt.ItemDataRole.UserRole, singer.id)

                # 颜色背景：选中时用补色，未选中时用演唱者本色
                is_selected = singer.id in self._selected_ids
                if is_selected:
                    bg_hex = _compute_complement_color(singer.color)
                else:
                    bg_hex = singer.color
                color = QColor(bg_hex)
                item.setBackground(color)
                luminance = (
                    0.299 * color.red()
                    + 0.587 * color.green()
                    + 0.114 * color.blue()
                ) / 255
                item.setForeground(
                    QColor("black") if luminance > 0.5 else QColor("white")
                )
                # 拖放控制：搜索过滤时禁止拖动（避免操作隐藏项造成混乱）
                flags = item.flags()
                if filter_lower:
                    flags = flags & ~Qt.ItemFlag.ItemIsDragEnabled
                else:
                    flags = flags | Qt.ItemFlag.ItemIsDragEnabled
                # 列表本身禁止子项作为放置目标（拖到项之间而非项上）
                flags = flags & ~Qt.ItemFlag.ItemIsDropEnabled
                item.setFlags(flags)

                # 还原选中（使用持久的 _selected_ids，跨搜索/刷新保留）
                if is_selected:
                    item.setSelected(True)

                self.list_singers.addItem(item)

            # 更新统计
            total = len(self._project.singers)
            enabled = sum(1 for s in self._project.singers if s.enabled)
            stats_text = f"共 {total} 位演唱者（{enabled} 位启用）"
            selected_count = len(self.list_singers.selectedItems())
            if selected_count > 0:
                stats_text += f" — 已选中 {selected_count} 位"
            visible = self.list_singers.count()
            if filter_lower or self._group_filter:
                stats_text += f"  [过滤中：{visible} 项可见]"
            self.lbl_stats.setText(stats_text)
        finally:
            self._suppress_reorder_signal = False

        self._update_button_state()

    # ==================== 选中与按钮状态 ====================

    def _get_selected_singer_ids(self) -> List[str]:
        """获取当前选中的演唱者 ID 列表。

        以后端 project.singers 的排列顺序为基准，返回 _selected_ids 中存在的 ID。
        这样即使在搜索状态下，不可见的选中项也会被包含，且顺序稳定。
        """
        if not self._project:
            return []
        return [s.id for s in self._project.singers if s.id in self._selected_ids]

    def _on_selection_changed(self):
        """选中变化：同步到持久选中集合，刷新按钮状态和统计。

        只对当前可见项做差量更新：
        - 可见且选中 → 加入 _selected_ids
        - 可见但未选中 → 从 _selected_ids 移除
        - 不可见（被搜索过滤掉）→ _selected_ids 保持原值不动
        """
        for i in range(self.list_singers.count()):
            item = self.list_singers.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            if not sid:
                continue
            if item.isSelected():
                self._selected_ids.add(sid)
            else:
                self._selected_ids.discard(sid)

        # 更新 item 背景色（选中态变化时需要重绘补色）
        self._suppress_reorder_signal = True
        try:
            for i in range(self.list_singers.count()):
                item = self.list_singers.item(i)
                sid = item.data(Qt.ItemDataRole.UserRole)
                if not sid:
                    continue
                singer = self._project.get_singer(sid) if self._project else None
                if not singer:
                    continue
                is_sel = sid in self._selected_ids
                bg_hex = _compute_complement_color(singer.color) if is_sel else singer.color
                color = QColor(bg_hex)
                item.setBackground(color)
                lum = (0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()) / 255
                item.setForeground(QColor("black") if lum > 0.5 else QColor("white"))
        finally:
            self._suppress_reorder_signal = False

        self._update_button_state()
        self._update_color_preview()
        # 仅更新统计文字尾部"已选中 K 位"，避免完全重建列表
        if self._project:
            total = len(self._project.singers)
            enabled = sum(1 for s in self._project.singers if s.enabled)
            stats_text = f"共 {total} 位演唱者（{enabled} 位启用）"
            selected_count = len(self._selected_ids)
            if selected_count > 0:
                stats_text += f" — 已选中 {selected_count} 位"
            if self._filter_text.strip() or self._group_filter:
                stats_text += f"  [过滤中：{self.list_singers.count()} 项可见]"
            self.lbl_stats.setText(stats_text)

    def _update_button_state(self):
        """根据当前选中数量、过滤状态、项目状态更新按钮可用性"""
        has_project = self._project is not None
        selected_ids = self._get_selected_singer_ids()
        n_selected = len(selected_ids)
        is_filtering = bool(self._filter_text.strip())

        # 编辑：仅单选可用
        self.btn_edit.setEnabled(has_project and n_selected == 1)

        # 删除：至少 1 项，且不会清空所有演唱者
        total = len(self._project.singers) if self._project else 0
        self.btn_delete.setEnabled(
            has_project and n_selected >= 1 and (total - n_selected) >= 1
        )

        # 设置分组
        self.btn_set_group.setEnabled(has_project and n_selected >= 1)

        # 启用/禁用
        self.btn_enable.setEnabled(has_project and n_selected >= 1)
        self.btn_disable.setEnabled(has_project and n_selected >= 1)

        # 顺序：过滤时禁用（看不到完整列表，操作会困惑）
        order_ok = has_project and n_selected >= 1 and not is_filtering and not self._group_filter
        self.btn_top.setEnabled(order_ok)
        self.btn_up.setEnabled(order_ok)
        self.btn_down.setEnabled(order_ok)
        self.btn_bottom.setEnabled(order_ok)

    def _update_color_preview(self):
        """更新右侧独立颜色预览面板"""
        if not self._project:
            self._color_preview_panel._clear()
            return

        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            self._color_preview_panel._clear()
            return

        if len(selected_ids) > 1:
            self._color_preview_panel.set_multiple(len(selected_ids))
            return

        singer = self._project.get_singer(selected_ids[0])
        if not singer:
            self._color_preview_panel._clear()
            return

        colors = singer.get_all_colors()
        self._color_preview_panel.set_preview(singer.name, colors)

    # ==================== 搜索 / 分组过滤 ====================

    def _on_search_changed(self, text: str):
        self._filter_text = text or ""
        self._refresh_list()

    def _on_group_filter_changed(self):
        self._group_filter = self.combo_group_filter.currentData() or ""
        self._refresh_list()

    # ==================== 拖放重排 ====================

    def _on_rows_moved(self, *args, **kwargs):
        """Qt 内部拖放完成后触发：读取当前列表顺序并提交后端"""
        if self._suppress_reorder_signal:
            return
        if not self._project or not self._singer_service:
            return
        # 搜索过滤期间不允许拖放（项的 DragEnabled 已被禁，但稳妥起见再判一次）
        if self._filter_text.strip():
            return

        ordered_ids: List[str] = []
        for i in range(self.list_singers.count()):
            item = self.list_singers.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            if sid:
                ordered_ids.append(sid)

        if len(ordered_ids) != len(self._project.singers):
            # 不一致：回滚刷新
            self._refresh_list()
            return

        ok = self._singer_service.reorder_singers(ordered_ids)
        if not ok:
            self._refresh_list()
            return

        self._notify_singers_changed()

    # ==================== 添加 / 编辑 ====================

    def _get_existing_groups(self) -> List[str]:
        """获取当前项目中已存在的分组名称列表"""
        if not self._project:
            return []
        return sorted(set(s.group for s in self._project.singers if s.group))

    def _on_add_singer(self):
        if not self._project:
            self._warn("未加载项目", "请先打开或创建一个项目")
            return

        dialog = SingerEditDialog(
            existing_groups=self._get_existing_groups(),
            existing_singers=list(self._project.singers),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        try:
            singer_name = data["name"] if data["name"] else None
            singer = self._singer_service.add_singer(
                name=singer_name,
                color=data["color"],
                color_mode=data.get("color_mode", "solid"),
                split_colors=data.get("split_colors", []),
                group=data.get("group", ""),
            )
            if data["is_default"]:
                self._singer_service.set_default_singer(singer.id)

            self._notify_singers_changed()
            self._info("添加成功", f"已添加演唱者: {singer.name}")
        except Exception as e:
            self._error("添加失败", str(e))

    def _on_edit_singer(self):
        selected_ids = self._get_selected_singer_ids()
        if len(selected_ids) != 1:
            return
        singer = self._project.get_singer(selected_ids[0])
        if not singer:
            return

        dialog = SingerEditDialog(
            singer,
            existing_groups=self._get_existing_groups(),
            existing_singers=list(self._project.singers),
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        data = dialog.get_data()
        try:
            if data["name"] and data["name"] != singer.name:
                self._singer_service.rename_singer(singer.id, data["name"])
            color_changed = (
                data["color"] != singer.color
                or data["color_mode"] != singer.color_mode
                or data["split_colors"] != list(singer.split_colors)
            )
            if color_changed:
                self._singer_service.change_singer_color(
                    singer.id,
                    data["color"],
                    color_mode=data["color_mode"],
                    split_colors=data["split_colors"],
                )
            if data["is_default"] and not singer.is_default:
                self._singer_service.set_default_singer(singer.id)
            new_group = data.get("group", "")
            if new_group != singer.group:
                self._singer_service.change_singer_group(singer.id, new_group)

            self._notify_singers_changed()
            self._info("修改成功", f"已更新演唱者: {singer.name}")
        except Exception as e:
            self._error("修改失败", str(e))

    # ==================== 批量删除 ====================

    def _on_delete_singers(self):
        if not self._project:
            return

        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return

        total = len(self._project.singers)
        if total - len(selected_ids) < 1:
            self._warn("无法删除", "必须至少保留一个演唱者")
            return

        selected_singers = [self._project.get_singer(sid) for sid in selected_ids]
        selected_singers = [s for s in selected_singers if s is not None]

        # 候选转移目标：不在被删除集合中的演唱者
        candidates = [
            s for s in self._project.singers if s.id not in set(selected_ids)
        ]
        if not candidates:
            self._warn("无法删除", "没有可用的转移目标")
            return

        # 弹窗选择转移目标
        default_singer = self._project.get_default_singer()
        default_target_id = (
            default_singer.id
            if default_singer and default_singer.id not in set(selected_ids)
            else candidates[0].id
        )
        dlg = TransferTargetDialog(candidates, default_target_id, self)

        # 简要确认信息
        names = "、".join(s.name for s in selected_singers[:5])
        if len(selected_singers) > 5:
            names += f" 等 {len(selected_singers)} 位"
        msg = QMessageBox(self)
        msg.setWindowTitle("确认批量删除")
        msg.setText(
            f"确定要删除 {len(selected_singers)} 位演唱者吗？\n\n{names}\n\n"
            "这些演唱者的歌词将转移到你下一步选择的演唱者。"
        )
        btn_yes = msg.addButton("继续", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        if msg.clickedButton() is not btn_yes:
            return

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        transfer_to = dlg.get_target_id()
        if not transfer_to:
            return

        ok = self._singer_service.batch_remove_singers(selected_ids, transfer_to)
        if not ok:
            self._error("删除失败", "请检查转移目标是否有效")
            return

        self._notify_singers_changed()
        self._info("删除成功", f"已删除 {len(selected_singers)} 位演唱者")

    # ==================== 批量启用/禁用 ====================

    def _on_set_enabled(self, enabled: bool):
        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return
        ok = self._singer_service.batch_set_enabled(selected_ids, enabled)
        if not ok:
            self._error(
                "操作失败", "部分演唱者状态未能更新" if not enabled else "部分演唱者未能启用"
            )
            return
        self._notify_singers_changed()
        self._info(
            "完成",
            f"已{'启用' if enabled else '禁用'} {len(selected_ids)} 位演唱者",
        )

    # ==================== 批量设置分组 ====================

    def _on_set_group(self):
        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return

        dialog = BatchGroupDialog(self._get_existing_groups(), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_group = dialog.get_group()
        ok = all(
            self._singer_service.change_singer_group(sid, new_group)
            for sid in selected_ids
        )
        if not ok:
            self._error("操作失败", "部分演唱者分组未能更新")
            return

        self._notify_singers_changed()
        label = f"「{new_group}」" if new_group else "（无分组）"
        self._info("完成", f"已将 {len(selected_ids)} 位演唱者设为分组 {label}")

    # ==================== 顺序调整（按钮） ====================

    def _on_move(self, direction: str):
        selected_ids = self._get_selected_singer_ids()
        if not selected_ids:
            return
        # 顺序操作期间用 backend 提供的"保持相对间隔"语义
        ok = self._singer_service.move_singers(selected_ids, direction)
        if not ok:
            return
        self._notify_singers_changed()

    # ==================== 演唱者预设 ====================

    def _on_save_preset(self):
        """将当前项目的演唱者保存到软件全局设置
        
        比较现有预设：同名则覆盖，不同名则新增。
        预设中有但本次项目未使用的保留。新更新的预设放在顶部。
        """
        if not self._project or not self._project.singers:
            self._warn("无法保存", "当前没有演唱者可保存")
            return

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        existing_presets = app_settings.load_singer_presets()

        current_singers = self._project.singers

        # 合并规则：
        # 旧预设被「替换」的条件（即不保留）：
        #   同名 且（旧分组为空 或 旧分组 == 当前演唱者分组）
        # 旧预设被「保留」：同名但旧分组非空且与任何当前同名演唱者分组均不同
        kept_presets = []
        for ep in existing_presets:
            ep_name = ep.get("name", "")
            ep_group = ep.get("group", "")
            covered = any(
                s.name == ep_name and (ep_group == "" or ep_group == s.group)
                for s in current_singers
            )
            if not covered:
                kept_presets.append(ep)

        # 当前项目演唱者转预设（放在顶部）
        new_presets = [
            {
                "name": s.name,
                "color": s.color,
                "color_mode": s.color_mode,
                "split_colors": s.split_colors,
                "is_default": s.is_default,
                "backend_number": s.backend_number,
                "group": s.group,
            }
            for s in current_singers
        ]

        # 合并：新预设在前，保留的旧预设在后
        merged = new_presets + kept_presets
        app_settings.save_singer_presets(merged)

        self._info(
            "保存成功", f"已保存 {len(new_presets)} 位演唱者预设到软件设置"
        )

    def _on_load_preset(self):
        """从软件全局设置加载演唱者预设到当前项目（弹窗多选）"""
        if not self._project or not self._singer_service:
            self._warn("未加载项目", "请先打开或创建一个项目")
            return

        from strange_uta_game.frontend.settings.settings_interface import AppSettings

        app_settings = AppSettings()
        presets = app_settings.load_singer_presets()

        if not presets:
            self._warn("无预设", "软件中没有保存的演唱者预设，请先保存")
            return

        existing_names = {s.name for s in self._project.singers}

        # 弹出多选对话框
        dialog = SingerPresetLoadDialog(presets, existing_names, app_settings, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_presets = dialog.get_selected_presets()
        if not selected_presets:
            return

        # 若项目中仅有一个未命名默认演唱者（软件初始占位符），导入后将其替换
        unnamed_default_id = None
        if (
            len(self._project.singers) == 1
            and self._project.singers[0].name == "未命名"
            and self._project.singers[0].is_default
        ):
            unnamed_default_id = self._project.singers[0].id

        added = 0
        for preset in selected_presets:
            name = preset.get("name", "")
            if not name or name in existing_names:
                continue
            try:
                singer = self._singer_service.add_singer(
                    name=name,
                    color=preset.get("color", "#FF6B6B"),
                    color_mode=preset.get("color_mode", "solid"),
                    split_colors=preset.get("split_colors", []),
                    group=preset.get("group", ""),
                )
                if added == 0 and unnamed_default_id:
                    # 第一个导入的演唱者：设为默认，再删除初始占位符（先加后删保证至少有一个）。
                    # 必须把占位符名下的句子转移给新演唱者，否则原默认演唱者的歌词会被级联删除。
                    self._singer_service.set_default_singer(singer.id)
                    self._singer_service.remove_singer(
                        unnamed_default_id, transfer_to=singer.id
                    )
                    unnamed_default_id = None
                elif preset.get("is_default", False):
                    self._singer_service.set_default_singer(singer.id)
                added += 1
                existing_names.add(name)
            except Exception:
                pass

        self._notify_singers_changed()

        if added > 0:
            self._info("加载成功", f"已从预设加载 {added} 位新演唱者")

    # ==================== 工具方法 ====================

    def _notify_singers_changed(self):
        """统一通知：刷新本地列表 + 通知 ProjectStore"""
        self._refresh_list()
        if hasattr(self, "_store"):
            self._store.notify("singers")
        self.singers_changed.emit()

    def _info(self, title: str, content: str, duration: int = 2000):
        InfoBar.success(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )

    def _warn(self, title: str, content: str, duration: int = 3000):
        InfoBar.warning(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )

    def _error(self, title: str, content: str, duration: int = 5000):
        InfoBar.error(
            title=title,
            content=content,
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=duration,
            parent=self,
        )
