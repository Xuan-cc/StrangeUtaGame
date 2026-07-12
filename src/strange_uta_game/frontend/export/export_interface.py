"""导出界面。

提供多格式导出功能，支持 LRC/KRA/TXT/ASS/Nicokara。
Nicokara 格式支持演唱者过滤和演唱者标签插入。
"""

from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QFileDialog,
    QListWidget,
    QListWidgetItem,
    QDialog,
    QTextEdit,
)
from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from qfluentwidgets import (
    PushButton,
    PrimaryPushButton,
    LineEdit,
    InfoBar,
    InfoBarPosition,
    FluentIcon as FIF,
    ScrollArea,
    SimpleCardWidget,
    CheckBox,
    setCustomStyleSheet,
    TitleLabel,
    SubtitleLabel,
    BodyLabel,
    CaptionLabel,
)

from typing import Optional, Set, Dict, cast
from pathlib import Path
import re

from strange_uta_game.backend.domain import Project
from strange_uta_game.backend.application.export_service import ExportService
from strange_uta_game.frontend.settings.settings_interface import (
    AppSettings,
    NicokaraTagsDialog,
)
from strange_uta_game.frontend.theme import theme as _theme
from strange_uta_game.frontend.fluent_widgets import FluentGroupBox, message_question
from strange_uta_game.frontend.window_sizing import fit_to_screen


class RubyMismatchDialog(QDialog):
    """注音分段不匹配对话框 — 预览按字符/mora 均分结果并支持直接应用后导出。"""

    def __init__(self, detail: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("注音分段不匹配"))
        fit_to_screen(self, 640, 500)
        self._action: str = "cancel"

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        desc = QLabel(self.tr(
            "以下字符的注音分段数量与节奏点数量不匹配。\n"
            "可选择自动均分方案修复后继续导出，或忽略继续导出。"
        ))
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._preview = QTextEdit()
        self._preview.setReadOnly(True)
        self._build_preview_content(detail)
        layout.addWidget(self._preview, 1)

        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        self.btn_char = PrimaryPushButton(self.tr("按字符均分并导出"), self)
        self.btn_mora = PrimaryPushButton(self.tr("按mora均分并导出"), self)
        self.btn_ignore = PushButton(self.tr("忽略并继续导出"), self)
        self.btn_cancel = PushButton(self.tr("取消"), self)

        self.btn_char.clicked.connect(lambda: self._set_action("char"))
        self.btn_mora.clicked.connect(lambda: self._set_action("mora"))
        self.btn_ignore.clicked.connect(lambda: self._set_action("ignore"))
        self.btn_cancel.clicked.connect(self.reject)

        btn_layout.addWidget(self.btn_char)
        btn_layout.addWidget(self.btn_mora)
        btn_layout.addWidget(self.btn_ignore)
        btn_layout.addWidget(self.btn_cancel)
        layout.addLayout(btn_layout)

    def _build_preview_content(self, detail: dict) -> None:
        lines: list[str] = []
        lines.append("=" * 50)
        lines.append(self.tr("【不匹配列表】"))
        lines.append("=" * 50)
        for line in detail.get("mismatch_lines", []):
            lines.append(f"  {line}")
        lines.append("")
        lines.append("=" * 50)
        lines.append(self.tr("【按字符均分预览】"))
        lines.append("=" * 50)
        for line in detail.get("char_preview_lines", []):
            lines.append(f"  {line}")
        lines.append("")
        lines.append("=" * 50)
        lines.append(self.tr("【按mora均分预览】"))
        lines.append("=" * 50)
        for line in detail.get("mora_preview_lines", []):
            lines.append(f"  {line}")
        self._preview.setPlainText("\n".join(lines))

    def _set_action(self, action: str) -> None:
        self._action = action
        self.accept()

    def get_action(self) -> str:
        return self._action


class ExportInterface(QWidget):
    """导出界面"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: Optional[Project] = None
        self._export_service = ExportService()
        # 输出目录是否由用户通过「浏览」主动选定。一经选定即保留，自动预填不再
        # 覆盖它（切换音频、改设置均不影响），直到用户再次「浏览」更换、或切换项目。
        # 放在 __init__ 而非 _init_ui，避免切语言重建时被清零。
        self._output_user_set = False
        self._init_ui()

    def changeEvent(self, event):
        """切语言时整张导出页拆掉重建。保留输出路径/文件名/格式选中等用户状态。"""
        if event.type() == QEvent.Type.LanguageChange:
            from strange_uta_game.frontend.localization import detach_layout_for_rebuild
            saved = {
                "output": self.line_output.text() if hasattr(self, "line_output") else "",
                "fname": self.line_filename.text() if hasattr(self, "line_filename") else "",
                "fmt_row": self.format_list.currentRow() if hasattr(self, "format_list") else -1,
            }
            detach_layout_for_rebuild(self)
            self._init_ui()  # _init_ui 内部已会调 _populate_formats，无需再调
            # 还原用户输入
            if hasattr(self, "line_output"):
                self.line_output.setText(saved["output"])
            if hasattr(self, "line_filename"):
                self.line_filename.setText(saved["fname"])
            if hasattr(self, "format_list") and saved["fmt_row"] >= 0:
                try:
                    self.format_list.setCurrentRow(saved["fmt_row"])
                except Exception:
                    pass
            # 重新填充演唱者列表（_init_ui 不负责，仅在 set_store 时被调）
            if hasattr(self, "_store") and self._store is not None:
                try:
                    self._refresh_singer_checkboxes()
                except Exception:
                    pass
        super().changeEvent(event)

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)
        layout.setSpacing(20)

        # 标题（保存为实例变量，防止 Python GC 清出 WeakKeyDictionary 导致主题失效）
        self.title_label = TitleLabel(self.tr("导出"))
        layout.addWidget(self.title_label)

        desc = CaptionLabel(self.tr("将项目导出为多种歌词格式"))
        layout.addWidget(desc)

        # 格式选择
        content = QHBoxLayout()
        content.setSpacing(20)

        # 左侧：格式列表
        left_card = SimpleCardWidget()
        left_layout = QVBoxLayout(left_card)
        left_layout.setContentsMargins(20, 20, 20, 20)
        left_layout.setSpacing(10)

        left_label = SubtitleLabel(self.tr("选择导出格式"))
        left_layout.addWidget(left_label)

        self.format_list = QListWidget()
        self.format_list.setMinimumHeight(200)
        left_layout.addWidget(self.format_list)

        content.addWidget(left_card, 1)

        # 右侧：导出配置
        right_card = SimpleCardWidget()
        right_layout = QVBoxLayout(right_card)
        right_layout.setContentsMargins(20, 20, 20, 20)
        right_layout.setSpacing(15)

        right_label = SubtitleLabel(self.tr("导出设置"))
        right_layout.addWidget(right_label)

        # 输出路径
        path_label = CaptionLabel(self.tr("输出路径"))
        right_layout.addWidget(path_label)

        path_row = QHBoxLayout()
        self.line_output = LineEdit()
        self.line_output.setPlaceholderText(self.tr("选择导出目录..."))
        self.line_output.setReadOnly(True)
        path_row.addWidget(self.line_output)

        btn_browse = PushButton(self.tr("浏览..."), self)
        btn_browse.setIcon(FIF.FOLDER)
        btn_browse.clicked.connect(self._on_browse)
        path_row.addWidget(btn_browse)
        right_layout.addLayout(path_row)

        # 文件名
        fname_label = CaptionLabel(self.tr("文件名（不含扩展名）"))
        right_layout.addWidget(fname_label)

        self.line_filename = LineEdit()
        self.line_filename.setPlaceholderText("untitled")
        right_layout.addWidget(self.line_filename)

        # Nicokara 标签设置按钮（仅 Nicokara 格式显示）
        self.btn_tags = PushButton(self.tr("Nicokara 标签设置..."), self)
        self.btn_tags.setIcon(FIF.TAG)
        self.btn_tags.clicked.connect(self._on_nicokara_tags)
        self.btn_tags.hide()
        right_layout.addWidget(self.btn_tags)

        # 演唱者选择区域（仅 Nicokara 格式显示）
        self._singer_group = FluentGroupBox(self.tr("演唱者过滤"))
        singer_group_layout = self._singer_group.contentLayout
        singer_group_layout.setSpacing(6)

        singer_hint = CaptionLabel(self.tr("勾选要导出的演唱者（不勾选则导出全部）"))
        singer_group_layout.addWidget(singer_hint)

        self._singer_checkboxes: list[CheckBox] = []
        self._singer_checkbox_widget = QWidget()
        self._singer_checkbox_container = QVBoxLayout(self._singer_checkbox_widget)
        self._singer_checkbox_container.setContentsMargins(0, 0, 0, 0)
        self._singer_checkbox_container.setSpacing(6)

        self._singer_scroll_area = ScrollArea()
        self._singer_scroll_area.setWidgetResizable(True)
        self._singer_scroll_area.setMaximumHeight(120)
        self._singer_scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self._singer_scroll_area.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self._singer_scroll_area.setWidget(self._singer_checkbox_widget)
        # 断开 autoFillBackground 对系统 QPalette 的依赖
        self._singer_scroll_area.viewport().setAutoFillBackground(False)
        self._singer_checkbox_widget.setAutoFillBackground(False)
        singer_group_layout.addWidget(self._singer_scroll_area)

        self._chk_insert_singer_tags = CheckBox(self.tr("插入【演唱者名】标签"))
        self._chk_insert_singer_tags.setToolTip(
            self.tr("导出时，当演唱者发生变化，在字符前自动插入演唱者名称标签")
        )
        self._chk_insert_singer_tags.hide()

        self._chk_insert_singer_each_line = CheckBox(self.tr("->每行行首都插入演唱者"))
        self._chk_insert_singer_each_line.setToolTip(
            self.tr("每一行开头都插入演唱者名称标签（需先启用「插入【演唱者名】标签」）")
        )
        self._chk_insert_singer_each_line.setEnabled(False)
        self._chk_insert_singer_each_line.hide()
        self._chk_insert_singer_tags.stateChanged.connect(
            lambda state: self._chk_insert_singer_each_line.setEnabled(bool(state))
        )

        # 分色标签设置助手按钮（仅 Nicokara 格式显示，紧接「插入演唱者标签」之后）
        self._btn_emoji_config = PushButton(self.tr("分色标签设置助手..."), self)
        self._btn_emoji_config.setToolTip(
            self.tr("为每位演唱者配置 @Emoji 分色标签，配置后自动写入 Nicokara 标签的自定义字段")
        )
        self._btn_emoji_config.clicked.connect(self._on_emoji_config)
        self._btn_emoji_config.hide()

        self._singer_group.hide()
        right_layout.addWidget(self._singer_group)
        right_layout.addWidget(self._chk_insert_singer_tags)
        right_layout.addWidget(self._chk_insert_singer_each_line)
        right_layout.addWidget(self._btn_emoji_config)

        right_layout.addStretch()

        # 导出按钮
        self.btn_export = PrimaryPushButton(self.tr("导出"), self)
        self.btn_export.setIcon(FIF.SHARE)
        self.btn_export.setMinimumHeight(45)
        self.btn_export.clicked.connect(self._on_export)
        right_layout.addWidget(self.btn_export)

        content.addWidget(right_card, 1)

        layout.addLayout(content, 1)

        # 所有控件创建完毕后再填充格式列表（_populate_formats 会访问 btn_tags 等控件）
        self._populate_formats()

        # 主题变化时刷新 QListWidget 和标题标签样式（二者不在 qfluentwidgets 管理中）
        _theme.changed.connect(self._update_theme_style)
        self._update_theme_style()

    def _update_theme_style(self) -> None:
        """主题变化时刷新不受 qfluentwidgets 管理的控件样式。

        - title_label (TitleLabel)：局部变量创建后可能被 GC 移出
          styleSheetManager 的 WeakKeyDictionary，需显式更新颜色。
        - format_list (QListWidget)：纯 Qt 控件，依赖 QPalette 渲染，
          需要显式 QSS 覆盖。
        """
        text = _theme.text_primary.name()
        self.title_label.setStyleSheet(f"color: {text};")

        bg     = _theme.bg_primary.name()
        border = _theme.border_primary.name()
        hover  = _theme.bg_hover.name()
        sel    = _theme.bg_selected.name()
        # 选中行始终用白字（bg_selected 是深蓝色，深浅模式下均与白字对比度最佳）
        self.format_list.setStyleSheet(f"""
            QListWidget {{
                background-color: {bg};
                color: {text};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px;
                outline: none;
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background-color: {sel};
                color: #ffffff;
            }}
            QListWidget::item:hover:!selected {{
                background-color: {hover};
            }}
        """)

    @staticmethod
    def _strip_extension_hint(name: str) -> str:
        """去除格式名末尾形如 '(.ext)' 的后缀提示，便于名称比对。

        例如 'LRC (增强型) (.lrc)' → 'LRC (增强型)'
        """
        return re.sub(r"\s*\(\.[^)]+\)$", "", name).strip()

    def _tr_format_name(self, name: str) -> str:
        """显式枚举各 format key → 让 .ts 抽取器把源串纳入 ExportInterface
        上下文（变量参数的 self.tr(var) 抓不到）。"""
        if name == "LRC (增强型)":         return self.tr("LRC (增强型)")
        if name == "LRC (逐行)":           return self.tr("LRC (逐行)")
        if name == "LRC (逐字)":           return self.tr("LRC (逐字)")
        if name == "Nicokara (带注音)":    return self.tr("Nicokara (带注音)")
        if name == "RL 编辑模式":           return self.tr("RL 编辑模式")
        if name == "春日向注音":            return self.tr("春日向注音")
        if name == "春日向注音（带罗马音）": return self.tr("春日向注音（带罗马音）")
        # KRA / TXT / SRT / txt2ass / ASS / Nicokara 都是英文，无需翻译
        return name

    def _populate_formats(self):
        """填充格式列表"""
        # 必须先 clear——changeEvent 重建后会再次调，不清会双份
        self.format_list.clear()
        formats = self._export_service.get_available_formats()
        for fmt in formats:
            # name 是 config.json 里的 key（如 "LRC (增强型)"），不能改写；
            # 列表里只翻译显示文本，UserRole 仍存原 name 供保存/查询用。
            display = f"{self._tr_format_name(fmt['name'])} ({fmt['extension']})"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, fmt["name"])
            self.format_list.addItem(item)
        if self.format_list.count() > 0:
            default_format = self._strip_extension_hint(
                AppSettings().get("export.default_format", "")
            )
            default_row = 0
            if default_format:
                for i in range(self.format_list.count()):
                    item = self.format_list.item(i)
                    if item and self._strip_extension_hint(
                        item.data(Qt.ItemDataRole.UserRole)
                    ) == default_format:
                        default_row = i
                        break
            self.format_list.setCurrentRow(default_row)
        self.format_list.currentItemChanged.connect(self._on_format_selected)
        # 信号在 setCurrentRow 之后才连接，需手动触发一次以初始化格式专属控件
        self._on_format_selected(self.format_list.currentItem(), None)

    def _on_format_selected(self, current, _previous):
        """根据所选格式显示/隐藏 Nicokara 专用控件"""
        if current:
            name = current.data(Qt.ItemDataRole.UserRole)
            is_nicokara = "nicokara" in name.lower()
            self.btn_tags.setVisible(is_nicokara)
            self._singer_group.setVisible(is_nicokara)
            self._chk_insert_singer_tags.setVisible(is_nicokara)
            self._chk_insert_singer_each_line.setVisible(is_nicokara)
            self._btn_emoji_config.setVisible(is_nicokara)
            if is_nicokara:
                self._refresh_singer_checkboxes()

    def set_project(self, project: Project):
        self._project = project

    def _get_export_offset(self) -> int:
        """从设置中获取导出时间偏移（毫秒）。"""
        settings = AppSettings()
        return settings.get("export.offset_ms", 0)

    def _get_software_compensation(self) -> int:
        """从设置中获取软件导出补偿（毫秒）。"""
        settings = AppSettings()
        return settings.get("export.software_compensation_ms", 0)

    def set_store(self, store):
        """接入 ProjectStore 统一数据中心。"""
        self._store = store
        store.data_changed.connect(self._on_data_changed)

    def _on_data_changed(self, change_type: str):
        """响应 ProjectStore 的数据变更。"""
        try:
            if change_type == "project":
                self._project = self._store.project
                self._sync_default_filename()
                # 切换项目才清除用户上次的浏览选择
                self._sync_default_output_dir(reset_user_choice=True)
                self._refresh_singer_checkboxes()
            elif change_type == "audio":
                # 音频变更即刻反映到默认文件名（无需等待"创建项目"）
                self._sync_default_filename()
                self._sync_default_output_dir()
            elif change_type == "singers":
                if self._store and self._store.project:
                    self._project = self._store.project
                self._refresh_singer_checkboxes()
            elif change_type == "settings":
                self._sync_default_format()
                self._sync_default_output_dir()
        except Exception as e:
            print(f"[ExportInterface] _on_data_changed({change_type}) 失败: {e}")

    def _sync_default_format(self):
        """将 format_list 的选中项与配置中的 default_format 同步。"""
        default_format = self._strip_extension_hint(
            AppSettings().get("export.default_format", "")
        )
        if not default_format:
            return
        for i in range(self.format_list.count()):
            item = self.format_list.item(i)
            if item and self._strip_extension_hint(
                item.data(Qt.ItemDataRole.UserRole)
            ) == default_format:
                self.format_list.setCurrentRow(i)
                # 若目标行与当前行相同，setCurrentRow 不会 emit currentItemChanged，
                # 需手动触发以确保格式专属控件（Nicokara 区块等）正确刷新
                self._on_format_selected(item, None)
                return

    def _sync_default_output_dir(self, reset_user_choice: bool = False):
        """根据 store 的导出目录自动预填导出路径（仅影响预填值）。

        预填取值优先级见 ``ProjectStore.export_dir``（已保存项目 > 默认导出
        目录 > 上次加载目录）。用户通过「浏览」主动选过路径后，该选择会被
        保留，自动预填不再覆盖它，直到用户再次「浏览」更换、或切换项目。

        Args:
            reset_user_choice: 为 True 时清除用户上次的浏览选择（仅切换项目时
                传入；切换音频、改设置都不重置）。
        """
        if not self._store:
            return
        if reset_user_choice:
            self._output_user_set = False
        # 用户通过「浏览」指定的导出路径应当保留，不被自动预填覆盖
        if self._output_user_set:
            return
        export_dir = self._store.export_dir
        if export_dir:
            self.line_output.setText(export_dir)

    def _sync_default_filename(self):
        """根据当前 store 的音频 / 项目元数据刷新默认导出文件名。"""
        audio_path = getattr(self._store, "audio_path", None) if self._store else None
        if audio_path:
            default_name = Path(audio_path).stem
        elif self._project and self._project.metadata.title:
            default_name = self._project.metadata.title
        else:
            default_name = ""
        self.line_filename.setText(default_name)

    def _refresh_singer_checkboxes(self):
        """刷新演唱者 checkbox 列表"""
        # 清除现有 checkbox
        while self._singer_checkbox_container.count():
            item = self._singer_checkbox_container.takeAt(0)
            if item is not None:
                widget = item.widget()
                if widget is not None:
                    cast(QWidget, widget).deleteLater()
        self._singer_checkboxes.clear()

        if not self._project:
            return

        used_singer_ids = set()
        known_singer_ids = {s.id for s in self._project.singers}
        # 查找默认演唱者 ID（用于归一化未知演唱者）
        default_singer_id = None
        for s in self._project.singers:
            if s.is_default:
                default_singer_id = s.id
                break
        if default_singer_id is None and self._project.singers:
            default_singer_id = self._project.singers[0].id

        for sentence in getattr(self._project, "sentences", []) or []:
            # 行级别演唱者
            sentence_singer = getattr(sentence, "singer_id", None)
            if sentence_singer:
                if sentence_singer in known_singer_ids:
                    used_singer_ids.add(sentence_singer)
                elif default_singer_id:
                    # 未知演唱者视为默认演唱者
                    used_singer_ids.add(default_singer_id)
            elif default_singer_id:
                used_singer_ids.add(default_singer_id)
            # per-char 级别演唱者
            for character in getattr(sentence, "characters", []) or []:
                singer_id = getattr(character, "singer_id", None)
                if singer_id:
                    if singer_id in known_singer_ids:
                        used_singer_ids.add(singer_id)
                    elif singer_id in ("?", "未知") and default_singer_id:
                        used_singer_ids.add(default_singer_id)

        for singer in self._project.singers:
            if singer.id not in used_singer_ids:
                continue
            if not singer.enabled:
                continue
            chk = CheckBox(f"{singer.name}")
            chk.setProperty("singer_id", singer.id)
            # 用 setCustomStyleSheet 追加（而非 setStyleSheet 替换）演唱者专属
            # 文字色，保留 qfluentwidgets CheckBox 自管理的勾选框 QSS，使其在
            # 主题切换时仍被正常接管、不退化。
            _singer_qss = f"CheckBox {{ color: {singer.color}; font-weight: bold; }}"
            setCustomStyleSheet(chk, _singer_qss, _singer_qss)
            self._singer_checkbox_container.addWidget(chk)
            self._singer_checkboxes.append(chk)

        self._singer_checkbox_container.addStretch(1)

    def _get_selected_singer_ids(self) -> Optional[Set[str]]:
        """获取勾选的演唱者 ID 集合，如果没有勾选任何则返回 None（表示全部）"""
        selected = set()
        for chk in self._singer_checkboxes:
            if chk.isChecked():
                selected.add(chk.property("singer_id"))
        return selected if selected else None

    def _get_singer_map(self) -> Dict[str, str]:
        """获取 singer_id → 显示名 的映射"""
        if not self._project:
            return {}
        return {s.id: s.name for s in self._project.singers}

    def _on_browse(self):
        # 优先用导出专用目录（默认导出目录 > 项目/音频/歌词），回退到 last_export_dir
        default_dir = ""
        if self._store:
            default_dir = self._store.export_dir
        if not default_dir:
            default_dir = AppSettings().get("export.last_export_dir", "")
        path = QFileDialog.getExistingDirectory(self, self.tr("选择导出目录"), default_dir)
        if path:
            self.line_output.setText(path)
            self._output_user_set = True

    def _on_nicokara_tags(self):
        """打开 Nicokara 标签设置对话框"""
        settings = AppSettings()
        tag_data = settings.get("nicokara_tags") or {}
        dialog = NicokaraTagsDialog(tag_data, self)
        if dialog.exec() == NicokaraTagsDialog.DialogCode.Accepted:
            new_tags = dialog.get_tag_data()
            settings.set("nicokara_tags", new_tags)
            settings.save()
            if self._store:
                self._store.mark_dirty()

    def _on_emoji_config(self):
        """打开分色标签设置助手对话框。

        演唱者列表以当前过滤器勾选结果为准（无勾选则使用全部演唱者）。
        已有 @Emoji 标签优先读取，无匹配项回退到默认参数。
        配置确认后自动写入 nicokara_tags.custom 并记忆首行参数。
        """
        from strange_uta_game.frontend.export.emoji_tag_dialog import (
            EmojiTagDialog,
            split_params,
        )

        if not self._project:
            InfoBar.warning(
                title=self.tr("无项目"),
                content=self.tr("请先创建或打开项目"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 以过滤器勾选结果为准；无勾选则取全部演唱者
        selected_ids = self._get_selected_singer_ids()
        singer_list: list[tuple[str, str]] = []
        for singer in self._project.singers:
            if not singer.enabled:
                continue
            if selected_ids is None or singer.id in selected_ids:
                singer_list.append((singer.id, singer.name))

        if not singer_list:
            InfoBar.warning(
                title=self.tr("无演唱者"),
                content=self.tr("项目中没有可用的演唱者"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        # 解析已有的 @Emoji 标签，按触发字符建立映射。
        # 只匹配 @Emoji= (主标签)，排除 @EmojiN= (叠加层变体)。
        settings = AppSettings()
        tag_data = settings.get("nicokara_tags") or {}
        custom = tag_data.get("custom", [])
        existing_tags: dict[str, tuple[str, str, str]] = {}
        for line in custom:
            line = line.strip()
            if line.lower().startswith("@emoji="):
                rest = line[len("@Emoji="):]
                comma_idx = rest.find(",")
                if comma_idx > 0:
                    trigger = rest[:comma_idx]
                    params = rest[comma_idx + 1:]
                    existing_tags[trigger] = split_params(params)

        dialog = EmojiTagDialog(singer_list, self, existing_tags=existing_tags)
        if dialog.exec() == EmojiTagDialog.DialogCode.Accepted:  # apply_emoji_tags_to_settings 在 _on_accept 内部调用
            if self._store:
                self._store.mark_dirty()

    def _on_export(self):
        if not self._project:
            InfoBar.warning(
                title=self.tr("无项目"),
                content=self.tr("请先创建或打开项目"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        selected = self.format_list.currentItem()
        if not selected:
            InfoBar.warning(
                title=self.tr("未选择格式"),
                content=self.tr("请选择导出格式"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=self,
            )
            return

        output_dir = self.line_output.text()
        if not output_dir:
            # 弹出文件选择
            default_dir = ""
            if self._store:
                default_dir = self._store.export_dir
            if not default_dir:
                default_dir = AppSettings().get("export.last_export_dir", "")
            output_dir = QFileDialog.getExistingDirectory(self, self.tr("选择导出目录"), default_dir)
            if not output_dir:
                return
            self.line_output.setText(output_dir)

        # 导出前提示：仍有未补的"导唱待办"标记时让用户确认
        needs_guide_marks: list[tuple[int, int]] = [
            (line_idx, char_idx)
            for line_idx, s in enumerate(self._project.sentences)
            for char_idx, c in enumerate(s.characters)
            if c.needs_guide
        ]
        if needs_guide_marks:
            preview_lines = [
                self.tr("第 {line} 行 第 {char} 字").format(line=l + 1, char=c + 1)
                for l, c in needs_guide_marks[:10]
            ]
            extra = (
                self.tr("\n...另 {n} 处").format(n=len(needs_guide_marks) - 10)
                if len(needs_guide_marks) > 10
                else ""
            )
            if not message_question(
                self,
                self.tr("仍有导唱待办未处理"),
                self.tr("还剩 {n} 个标记点未添加导唱符。").format(n=len(needs_guide_marks))
                + "\n\n"
                + "\n".join(preview_lines)
                + extra,
                yes_text=self.tr("继续导出"),
                no_text=self.tr("取消"),
            ):
                return

        # 导出前验证
        warnings = self._export_service.validate_before_export(self._project)
        if warnings:
            InfoBar.warning(
                title=self.tr("导出提醒"),
                content="\n".join(warnings[:3]),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )

        # 校验 rubyPart 数量与 checkCount 是否匹配
        ruby_mismatches = self._export_service.validate_ruby_parts(self._project)
        if ruby_mismatches:
            detail = self._export_service.get_ruby_mismatch_detail(self._project)
            dialog = RubyMismatchDialog(detail, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            action = dialog.get_action()
            if action == "char":
                self._export_service.apply_ruby_parts_split(self._project, "char")
                if self._store:
                    self._store.mark_dirty()
                    self._store.notify("rubies")
            elif action == "mora":
                self._export_service.apply_ruby_parts_split(self._project, "mora")
                if self._store:
                    self._store.mark_dirty()
                    self._store.notify("rubies")
            elif action == "ignore":
                pass  # 忽略不匹配，继续导出
            elif action == "cancel":
                return

        name = selected.data(Qt.ItemDataRole.UserRole)
        # 获取扩展名
        formats = self._export_service.get_available_formats()
        ext = ""
        for fmt in formats:
            if fmt["name"] == name:
                ext = fmt["extension"]
                break

        base_name = (
            self.line_filename.text().strip()
            or self._project.metadata.title
            or "untitled"
        )
        filename = base_name + ext
        filepath = str(Path(output_dir) / filename)

        # 检查文件是否已存在
        if Path(filepath).exists():
            if not message_question(
                self,
                self.tr("文件已存在"),
                self.tr("文件已存在：\n{filename}").format(filename=filename)
                + "\n\n"
                + self.tr("是否覆盖该文件？"),
                yes_text=self.tr("覆盖"),
                no_text=self.tr("取消"),
            ):
                return

        result = self._export_service.export(
            self._project,
            name,
            filepath,
            offset_ms=self._get_export_offset(),
            singer_ids=self._get_selected_singer_ids(),
            insert_singer_tags=self._chk_insert_singer_tags.isChecked(),
            insert_singer_each_line=self._chk_insert_singer_each_line.isChecked(),
            singer_map=self._get_singer_map(),
            software_compensation_ms=self._get_software_compensation(),
        )
        if result.success:
            # 将本次使用的格式持久化为默认导出格式
            settings = AppSettings()
            settings.set("export.default_format", name)
            settings.save()

            InfoBar.success(
                title=self.tr("导出成功"),
                content=result.file_path,
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
        else:
            InfoBar.error(
                title=self.tr("导出失败"),
                content=result.error_message or self.tr("未知错误"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=self,
            )
