"""工作台主题 adapter —— SUG ``theme`` 单例的工作台门面。

本模块是 *仅工作台* 的 helper，负责：

* 把 SUG 的 ``theme`` 单例 + ``ThemeMode`` re-export 给工作台代码（统一从这里
  import，省去深路径，也便于将来若 SUG 迁仓库时只改这一处）。
* 在 SUG ``ThemeColors`` 之上扩展 *工作台专属* 语义化 color tokens
  （``shell_bg`` / ``card_bg`` / ``accent_*`` / …）—— 故意不放进 SUG 源码，
  避免污染 SUG 文件、破坏 SUG 独立分发契约（见
  ``lyrics_timing/docs/EMBEDDING.md``）。
* 提供 :func:`build_app_qss` 生成原 ``gui_qt.KrokHelperQtApp._apply_styles``
  那一大块的 QSS（light/dark 双版本，由 ``theme.is_dark`` 决定）。
* 提供 :func:`drop_card_palette` 解决 ``AlignmentDropCard`` 的功能配色
  (blue/red) × 主题 (light/dark) 二维表展开。
* 提供 :func:`apply_settings_theme` 启动期一次性推送主题（在
  ``MainWindow`` 构造之前调，避免浅色闪烁）。

**重要时序约束**：本模块必须在 ``QApplication`` 创建之后才能被 import ——
SUG ``theme.Theme.__init__`` 在 module-import 期就实例化单例，构造里会调
``QApplication.instance()`` 装平台监听器。host ``cli.run_gui`` 已按
"QApplication() → import theme_workbench → apply_settings_theme()"顺序排好。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# SUG src 路径需先挂上 sys.path 才能 import ``strange_uta_game``；
# ``krok_helper.lyrics_timing/__init__.py`` 的副作用做这件事。
import krok_helper.lyrics_timing  # noqa: F401  (installs SUG src path)

# SUG 的 theme 单例 + ThemeMode 透传给宿主代码。
# 不抽到 shared 位置：那样会破坏 SUG ``EMBEDDING.md`` 里的"独立分发"红线。
from strange_uta_game.frontend.theme import (  # noqa: F401  (re-export)
    Theme as _SugTheme,
    ThemeMode,
    theme,
)

from krok_helper.settings import (
    AppSettings,
    UI_THEME_AUTO,
    UI_THEME_DARK,
    UI_THEME_LIGHT,
)

__all__ = [
    "ThemeMode",
    "theme",
    "apply_settings_theme",
    "build_app_qss",
    "drop_card_palette",
    "DropCardPalette",
    "WorkbenchPalette",
    "palette",
    "schedule_theme_refresh",
    "themed",
]


THEME_REFRESH_DELAY_MS = 200


# ════════════════════════════════════════════════════════════════════════
# 启动期推送
# ════════════════════════════════════════════════════════════════════════

_UI_THEME_TO_MODE = {
    UI_THEME_AUTO: ThemeMode.AUTO,
    UI_THEME_LIGHT: ThemeMode.LIGHT,
    UI_THEME_DARK: ThemeMode.DARK,
}


def apply_settings_theme(settings: AppSettings) -> None:
    """根据 ``settings.ui_theme`` 推送主题。

    在 ``MainWindow`` 构造之前调，让 SUG ``theme`` 单例先把 QApplication
    palette / qfluentwidgets Theme 全部 settle 到目标模式 —— 这样窗口
    首次绘制就是正确颜色，避免"浅色闪一帧"。
    """
    mode = _UI_THEME_TO_MODE.get(settings.ui_theme, ThemeMode.AUTO)
    theme.mode = mode


# ════════════════════════════════════════════════════════════════════════
# 工作台专属 color tokens
# ════════════════════════════════════════════════════════════════════════


class WorkbenchPalette:
    """工作台专属语义化色板（light/dark 二选一）。

    每次 :func:`palette` 调用都新建实例 —— 不缓存，因 ``theme.is_dark``
    会随主题切换变化。要节流的话由 caller 在 ``theme.changed`` 回调里
    取一次后局部用。

    与 SUG ``ThemeColors`` 关系：SUG 那套覆盖卡拉 OK 预览 / 波形 / 编辑器
    专属色；这里只覆盖 *工作台外壳层* 的 token（卡片、面板、按钮、表格、
    滚动条、输入框、日志框、品牌色）。两个 palette 共享同一份
    ``theme.is_dark``。
    """

    def __init__(self, is_dark: bool):
        self._d = is_dark

    @property
    def is_dark(self) -> bool:
        return self._d

    # ── 外壳层 ──
    @property
    def shell_bg(self) -> str:
        return "#1E1E1E" if self._d else "#F4F7FB"

    @property
    def text_primary(self) -> str:
        return "#E6E6E6" if self._d else "#1f2937"

    @property
    def text_secondary(self) -> str:
        return "#A0A0A0" if self._d else "#667085"

    @property
    def text_hint(self) -> str:
        return "#808080" if self._d else "#64748B"

    @property
    def text_disabled(self) -> str:
        return "#5A5A5A" if self._d else "#94a3b8"

    # ── 卡片 / 面板 ──
    @property
    def card_bg(self) -> str:
        return "#252526" if self._d else "#FFFFFF"

    @property
    def card_border(self) -> str:
        return "#3E3E3E" if self._d else "#E5EAF2"

    @property
    def workflow_bar_bg(self) -> str:
        return "#2D2D2D" if self._d else "#FBFCFE"

    @property
    def workflow_bar_border(self) -> str:
        return "#3E3E3E" if self._d else "#E3E8F0"

    @property
    def panel_bg(self) -> str:
        return "#252526" if self._d else "#FFFFFF"

    @property
    def panel_border(self) -> str:
        return "#3E3E3E" if self._d else "#E1E7F0"

    # ── 输入框 ──
    @property
    def input_bg(self) -> str:
        return "#2D2D2D" if self._d else "#FFFFFF"

    @property
    def input_border(self) -> str:
        return "#3E3E3E" if self._d else "#d9dee8"

    @property
    def input_border_hover(self) -> str:
        return "#525252" if self._d else "#B6C2D2"

    @property
    def input_border_focus(self) -> str:
        # 浅色用偏粉的品牌补色，深色用稍亮变体保对比度。
        return "#FF8FA0" if self._d else "#D87886"

    @property
    def input_hover_bg(self) -> str:
        return "#333333" if self._d else "#FBFCFE"

    # ── 品牌主色（红） ──
    @property
    def accent_primary(self) -> str:
        # 用作 ``setThemeColor`` 与 ImportButton 主色。深色下亮一档保对比度。
        return "#FF7A8C" if self._d else "#FF5A6F"

    @property
    def accent_search(self) -> str:
        return "#FF7A8C" if self._d else "#D85C6C"

    @property
    def accent_hover(self) -> str:
        return "#FF5A6F" if self._d else "#C94F60"

    @property
    def accent_pressed(self) -> str:
        return "#C94F60" if self._d else "#B94455"

    @property
    def accent_disabled_bg(self) -> str:
        return "#5A3A40" if self._d else "#E8B5BD"

    @property
    def accent_disabled_text(self) -> str:
        return "#999999" if self._d else "#FFFFFF"

    # ── 副按钮（CopyButton / GlobalSettingsButton） ──
    @property
    def secondary_button_bg(self) -> str:
        return "#2D2D2D" if self._d else "#FFFFFF"

    @property
    def secondary_button_border(self) -> str:
        return "#3E3E3E" if self._d else "#D7DEE9"

    @property
    def secondary_button_text(self) -> str:
        return "#E6E6E6" if self._d else "#334155"

    @property
    def secondary_button_hover_bg(self) -> str:
        return "#3E3E3E" if self._d else "#F8FAFC"

    @property
    def secondary_button_hover_border(self) -> str:
        return "#525252" if self._d else "#C6D0DE"

    @property
    def secondary_button_pressed_bg(self) -> str:
        return "#424242" if self._d else "#EEF2F7"

    # ── GlobalSettingsButton hover ──
    @property
    def global_settings_hover_bg(self) -> str:
        return "#3A2A2C" if self._d else "#FFF6F7"

    @property
    def global_settings_hover_border(self) -> str:
        return "#FF7A8C" if self._d else "#F3A8B3"

    # ── 标题文字（与 text_primary 区分用） ──
    @property
    def title_text(self) -> str:
        return "#FFFFFF" if self._d else "#1f2937"

    @property
    def subtitle_text(self) -> str:
        return "#A0A0A0" if self._d else "#6B7280"

    @property
    def panel_title(self) -> str:
        return "#FFFFFF" if self._d else "#111827"

    @property
    def description_text(self) -> str:
        return "#A0A0A0" if self._d else "#667085"

    # ── 表格（LyricsResultsTable） ──
    @property
    def table_bg(self) -> str:
        return "#252526" if self._d else "#FFFFFF"

    @property
    def table_border(self) -> str:
        return "#3E3E3E" if self._d else "#DDE5EF"

    @property
    def table_row_border(self) -> str:
        return "rgba(255, 255, 255, 0.08)" if self._d else "rgba(226, 232, 240, 0.9)"

    @property
    def table_header_bg(self) -> str:
        return "#2D2D2D" if self._d else "#F8FAFC"

    @property
    def table_header_text(self) -> str:
        return "#CCCCCC" if self._d else "#64748B"

    @property
    def table_row_hover(self) -> str:
        return "#2D2D2D" if self._d else "#F8FAFC"

    @property
    def table_select_text(self) -> str:
        return "#FFFFFF" if self._d else "#111827"

    # ── 通用 QHeaderView ──
    @property
    def header_bg(self) -> str:
        return "#2D2D2D" if self._d else "#eef2f7"

    @property
    def header_text(self) -> str:
        return "#E6E6E6" if self._d else "#111827"

    @property
    def header_separator(self) -> str:
        return "#3E3E3E" if self._d else "#d5dce6"

    # ── LyricsPreviewText ──
    @property
    def preview_bg(self) -> str:
        return "#1E1E1E" if self._d else "#F8FAFC"

    @property
    def preview_border(self) -> str:
        return "#3E3E3E" if self._d else "#DDE5EF"

    @property
    def preview_focus_bg(self) -> str:
        return "#252526" if self._d else "#FBFCFE"

    @property
    def preview_text(self) -> str:
        return "#E6E6E6" if self._d else "#1E293B"

    @property
    def preview_selection_bg(self) -> str:
        return "#5A3A40" if self._d else "#FAD7DE"

    @property
    def preview_selection_text(self) -> str:
        return "#FFFFFF" if self._d else "#111827"

    @property
    def preview_title(self) -> str:
        return "#FFFFFF" if self._d else "#0F172A"

    @property
    def preview_meta(self) -> str:
        return "#A0A0A0" if self._d else "#64748B"

    # ── 关键字输入（LyricsKeywordEdit / LyricsSourceCombo） ──
    @property
    def lyrics_keyword_bg(self) -> str:
        return "#2D2D2D" if self._d else "#FFFFFF"

    @property
    def lyrics_keyword_border(self) -> str:
        return "#3E3E3E" if self._d else "#CBD5E1"

    @property
    def lyrics_keyword_hover_border(self) -> str:
        return "#525252" if self._d else "#B6C2D2"

    @property
    def lyrics_keyword_hover_bg(self) -> str:
        return "#333333" if self._d else "#FBFCFE"

    @property
    def lyrics_keyword_text(self) -> str:
        return "#E6E6E6" if self._d else "#111827"

    # ── 日志框 ──
    @property
    def log_bg(self) -> str:
        return "#1E1E1E" if self._d else "#FFFFFF"

    @property
    def log_text(self) -> str:
        return "#CCCCCC" if self._d else "#1f2937"

    # ── 进度条 ──
    @property
    def progress_bg(self) -> str:
        return "#2D2D2D" if self._d else "#eceff5"

    @property
    def progress_chunk(self) -> str:
        return "#FF7A8C" if self._d else "#FF5A6F"

    # ── 滚动条 ──
    @property
    def scrollbar_handle(self) -> str:
        return "#424242" if self._d else "#cbd3df"

    @property
    def scrollbar_handle_hover(self) -> str:
        return "#525252" if self._d else "#aeb8c8"

    # ── LyricsStripIntroCheck ──
    @property
    def check_label(self) -> str:
        return "#CCCCCC" if self._d else "#475569"


def palette() -> WorkbenchPalette:
    """返回当前主题对应的工作台 palette（每次新建，不缓存）。"""
    return WorkbenchPalette(theme.is_dark)


# ════════════════════════════════════════════════════════════════════════
# themed(widget, factory)：把内联 setStyleSheet 转成主题感知版本
# ════════════════════════════════════════════════════════════════════════


def themed(widget, qss_factory: Callable[[], str]) -> None:
    """让 ``widget`` 跟随 :data:`theme` 变化自动重写 stylesheet。

    用法::

        from krok_helper.theme_workbench import themed, palette
        themed(my_label, lambda: f"color: {palette().text_hint}; font-size: 9pt;")

    实现要点：
    * 立即调一次 factory 应用初始样式。
    * 连接 :pydata:`theme.changed`，每次 emit 时通过 ``QTimer.singleShot(0)``
      *延迟* 到下个 event loop iter 再调 factory。**这是必须的** ——
      ``theme.changed`` 是从 SUG ``_apply_theme_change`` /
      ``_reapply_win11_appearance`` 同步发出的，那两个方法刚做完
      ``_refresh_all_widgets`` 递归 polish；同步链上 setStyleSheet 会与
      Qt 内部 polish/unpolish 队列产生 native heap 竞态（Win11 + Mica +
      qfluentwidgets lazy QSS 三件套下尤其敏感）。延迟一拍让上一轮 polish
      彻底 settle 之后再写新 QSS。
    * 内置 ``RuntimeError`` 兜底：widget 的 C++ 端已销毁（用户关掉 dialog
      但 Qt 对象生命周期错位）时，捕获异常并断开连接 —— 避免内存泄漏 +
      下次 emit 抛 native crash。
    """
    from PyQt6.QtCore import QTimer

    timer = QTimer(widget)
    timer.setSingleShot(True)
    disconnected = False

    def _disconnect() -> None:
        nonlocal disconnected
        if disconnected:
            return
        disconnected = True
        try:
            theme.changed.disconnect(_on_theme_changed)
        except (RuntimeError, TypeError):
            pass

    def _apply() -> None:
        if disconnected:
            return
        try:
            widget.setStyleSheet(qss_factory())
        except RuntimeError:
            _disconnect()

    timer.timeout.connect(_apply)

    def _on_theme_changed() -> None:
        # 同步触发改成异步 —— 避免 SUG ``_refresh_all_widgets`` 链上的 polish/
        # setStyleSheet 竞态导致 native AV。延迟一小段时间是为了把 ``_reapply_
        # win11_appearance`` （SUG ``theme.py`` 里 double-singleShot(0) 触发
        # 的二次 polish）也让过去；重复 emit 只保留最后一次刷新。
        if disconnected:
            return
        try:
            timer.start(THEME_REFRESH_DELAY_MS)
        except RuntimeError:
            _disconnect()

    _apply()
    try:
        widget.destroyed.connect(lambda _obj=None: _disconnect())
    except RuntimeError:
        _disconnect()
        return
    theme.changed.connect(_on_theme_changed)


def schedule_theme_refresh(receiver, callback: Callable[[], None], *, timer_attr: str = "_theme_refresh_timer") -> None:
    """Debounce a theme-driven refresh on a QObject receiver.

    ``theme.changed`` is emitted from inside SUG's polish chain and can be
    emitted twice for one user-visible change on Win11. Restarting one timer
    keeps rapid toggles from piling up many QSS rewrites; the running/pending
    flags ensure a final pass is kept if a new change arrives during refresh.
    """
    from PyQt6.QtCore import QTimer

    running_attr = f"{timer_attr}_running"
    pending_attr = f"{timer_attr}_pending"
    callback_attr = f"{timer_attr}_callback"
    setattr(receiver, callback_attr, callback)
    timer = getattr(receiver, timer_attr, None)

    if timer is None:
        timer = QTimer(receiver)
        timer.setSingleShot(True)

        def _run() -> None:
            setattr(receiver, running_attr, True)
            try:
                latest_callback = getattr(receiver, callback_attr, None)
                if latest_callback is not None:
                    latest_callback()
            finally:
                setattr(receiver, running_attr, False)
            if getattr(receiver, pending_attr, False):
                setattr(receiver, pending_attr, False)
                timer.start(THEME_REFRESH_DELAY_MS)

        timer.timeout.connect(_run)
        setattr(receiver, timer_attr, timer)
        setattr(receiver, running_attr, False)
        setattr(receiver, pending_attr, False)

    if getattr(receiver, running_attr, False):
        setattr(receiver, pending_attr, True)
        return

    timer.start(THEME_REFRESH_DELAY_MS)


# ════════════════════════════════════════════════════════════════════════
# 主 QSS 生成
# ════════════════════════════════════════════════════════════════════════


def build_app_qss() -> str:
    """生成 ``KrokHelperQtApp._apply_styles`` 那一整块 QSS。

    light/dark 自动按当前 ``theme.is_dark`` 切。MainWindow 在
    ``theme.changed`` 回调里重新调本函数 + ``setStyleSheet``。
    """
    p = palette()
    return f"""
        QMainWindow, QWidget {{
            background: {p.shell_bg};
            color: {p.text_primary};
            font-family: "Microsoft YaHei UI";
            font-size: 10.5pt;
        }}
        QLabel, BodyLabel, CaptionLabel {{
            background: transparent;
            font-family: "Microsoft YaHei UI";
            font-weight: 400;
        }}
        StrongBodyLabel {{
            background: transparent;
            font-family: "Microsoft YaHei UI";
            font-weight: 700;
        }}
        QWidget#AppRoot {{
            background: {p.shell_bg};
        }}
        QFrame[cardWidget="true"] {{
            background: {p.card_bg};
            border: 1px solid {p.card_border};
            border-radius: 8px;
        }}
        QFrame#WorkflowBar {{
            background: {p.workflow_bar_bg};
            border: 1px solid {p.workflow_bar_border};
            border-radius: 8px;
        }}
        QFrame#WhitePanel {{
            background: {p.panel_bg};
            border: 1px solid {p.panel_border};
            border-radius: 8px;
        }}
        Pivot {{
            background: transparent;
            border: 0;
        }}
        QWidget#LyricsPage {{
            background: {p.shell_bg};
        }}
        QFrame#LyricsSearchPanel, QFrame#LyricsResultPanel, QFrame#LyricsPreviewPanel {{
            background: {p.panel_bg};
            border: 1px solid {p.panel_border};
            border-radius: 10px;
        }}
        QFrame#TrimRow {{
            background: transparent;
            border: 0;
        }}
        QLabel#AppTitle {{
            color: {p.title_text};
            font-size: 18pt;
            font-weight: 700;
        }}
        QLabel#AppSubtitle {{
            color: {p.subtitle_text};
            font-size: 10.5pt;
        }}
        ToolButton#AlignMaterialSettingsButton {{
            background: transparent;
            border: 1px solid transparent;
            border-radius: 8px;
            padding: 2px;
        }}
        ToolButton#AlignMaterialSettingsButton:hover {{
            background: {p.secondary_button_hover_bg};
            border-color: {p.secondary_button_hover_border};
        }}
        ToolButton#GlobalSettingsButton {{
            background: {p.secondary_button_bg};
            border: 1px solid {p.secondary_button_border};
            border-radius: 8px;
            padding: 4px;
        }}
        ToolButton#GlobalSettingsButton:hover {{
            background: {p.global_settings_hover_bg};
            border-color: {p.global_settings_hover_border};
        }}
        QLabel#PageTitle {{
            color: {p.title_text};
            font-size: 20pt;
            font-weight: 700;
        }}
        QLabel#PanelTitle {{
            background: transparent;
            color: {p.panel_title};
            font-size: 12.5pt;
            font-weight: 700;
        }}
        QLabel#LyricsPageDescription {{
            color: {p.description_text};
            font-size: 10pt;
        }}
        QLabel#LyricsSecondaryText, QLabel#LyricsStatusText, QLabel#LyricsResultsSummary, QLabel#LyricsPreviewHint, QLabel#LyricsMatchSummary {{
            color: {p.text_hint};
            font-size: 9pt;
        }}
        QLabel#LyricsPreviewMeta {{
            color: {p.preview_meta};
            font-size: 9.5pt;
        }}
        QLabel#LyricsPreviewTitle {{
            color: {p.preview_title};
            font-size: 14pt;
            font-weight: 700;
        }}
        QPlainTextEdit#LogText {{
            background: {p.log_bg};
            border: 0;
            color: {p.log_text};
            font-family: "Consolas";
            font-size: 10pt;
        }}
        QPlainTextEdit#LyricsPreviewText {{
            background: {p.preview_bg};
            border: 1px solid {p.preview_border};
            border-radius: 8px;
            color: {p.preview_text};
            font-size: 11pt;
            padding: 12px 14px;
            selection-background-color: {p.preview_selection_bg};
            selection-color: {p.preview_selection_text};
        }}
        QPlainTextEdit#LyricsPreviewText:focus {{
            border: 1px solid {p.input_border_focus};
            background: {p.preview_focus_bg};
        }}
        QTableWidget#LyricsResultsTable, QTableView#LyricsResultsTable, TableWidget#LyricsResultsTable {{
            background: {p.table_bg};
            alternate-background-color: {p.table_bg};
            border: 1px solid {p.table_border};
            gridline-color: transparent;
            selection-background-color: transparent;
            selection-color: {p.table_select_text};
            outline: 0;
            border-radius: 8px;
        }}
        QTableWidget#LyricsResultsTable::item, QTableView#LyricsResultsTable::item, TableWidget#LyricsResultsTable::item {{
            padding: 12px 12px;
            border: 0;
            border-bottom: 1px solid {p.table_row_border};
        }}
        QTableWidget#LyricsResultsTable::item:hover, QTableView#LyricsResultsTable::item:hover, TableWidget#LyricsResultsTable::item:hover {{
            background: {p.table_row_hover};
        }}
        QTableWidget#LyricsResultsTable::item:selected, QTableView#LyricsResultsTable::item:selected, TableWidget#LyricsResultsTable::item:selected {{
            background: {p.preview_selection_bg};
            color: {p.preview_selection_text};
        }}
        QTableWidget#LyricsResultsTable::item:selected:hover, QTableView#LyricsResultsTable::item:selected:hover, TableWidget#LyricsResultsTable::item:selected:hover {{
            background: {p.preview_selection_bg};
        }}
        QTableWidget#LyricsResultsTable QHeaderView::section, QTableView#LyricsResultsTable QHeaderView::section, TableWidget#LyricsResultsTable QHeaderView::section {{
            background: {p.table_header_bg};
            color: {p.table_header_text};
            border: 0;
            border-bottom: 1px solid {p.table_border};
            padding: 9px 10px;
            font-weight: 700;
        }}
        QPushButton#LyricsSearchButton, PrimaryPushButton#LyricsSearchButton {{
            background: {p.accent_search};
            border: 1px solid {p.accent_search};
            border-radius: 8px;
            color: #FFFFFF;
            font-weight: 700;
            padding: 8px 18px;
        }}
        QPushButton#LyricsSearchButton:hover, PrimaryPushButton#LyricsSearchButton:hover {{
            background: {p.accent_hover};
            border-color: {p.accent_hover};
        }}
        QPushButton#LyricsSearchButton:pressed, PrimaryPushButton#LyricsSearchButton:pressed {{
            background: {p.accent_pressed};
            border-color: {p.accent_pressed};
        }}
        QPushButton#LyricsSearchButton:disabled, PrimaryPushButton#LyricsSearchButton:disabled {{
            background: {p.accent_disabled_bg};
            border-color: {p.accent_disabled_bg};
            color: {p.accent_disabled_text};
        }}
        QPushButton#LyricsCopyButton {{
            background: {p.secondary_button_bg};
            border: 1px solid {p.secondary_button_border};
            border-radius: 8px;
            color: {p.secondary_button_text};
            padding: 7px 14px;
            font-weight: 600;
        }}
        QPushButton#LyricsCopyButton:hover {{
            background: {p.secondary_button_hover_bg};
            border-color: {p.secondary_button_hover_border};
        }}
        QPushButton#LyricsCopyButton:pressed {{
            background: {p.secondary_button_pressed_bg};
        }}
        QPushButton#LyricsImportButton {{
            background: {p.accent_primary};
            border: 1px solid {p.accent_primary};
            border-radius: 8px;
            color: #FFFFFF;
            padding: 7px 14px;
            font-weight: 700;
        }}
        QPushButton#LyricsImportButton:hover {{
            background: {p.accent_hover};
            border-color: {p.accent_hover};
        }}
        QPushButton#LyricsImportButton:pressed {{
            background: {p.accent_pressed};
            border-color: {p.accent_pressed};
        }}
        QPushButton#LyricsImportButton:disabled {{
            background: {p.accent_disabled_bg};
            border-color: {p.accent_disabled_bg};
            color: {p.accent_disabled_text};
        }}
        QCheckBox#LyricsStripIntroCheck {{
            color: {p.check_label};
            spacing: 7px;
        }}
        QHeaderView::section {{
            background: {p.header_bg};
            color: {p.header_text};
            border: 0;
            border-right: 1px solid {p.header_separator};
            border-bottom: 1px solid {p.header_separator};
            padding: 6px 8px;
            font-weight: 700;
        }}
        QPushButton[compact="true"] {{
            padding: 3px 8px;
            font-size: 10pt;
        }}
        QProgressBar {{
            border: 0;
            background: {p.progress_bg};
            min-height: 10px;
            max-height: 10px;
            border-radius: 5px;
        }}
        QProgressBar::chunk {{
            background: {p.progress_chunk};
            border-radius: 5px;
        }}
        QRadioButton:disabled, QCheckBox:disabled, QLabel:disabled {{
            color: {p.text_disabled};
        }}
        QCheckBox {{
            background: transparent;
        }}
        QLineEdit, QComboBox, QDoubleSpinBox {{
            background: {p.input_bg};
            border: 1px solid {p.input_border};
            padding: 8px 10px;
            border-radius: 12px;
            color: {p.text_primary};
        }}
        QLineEdit#LyricsKeywordEdit, QComboBox#LyricsSourceCombo, QComboBox#LyricsPreviewModeCombo {{
            background: {p.lyrics_keyword_bg};
            border: 1px solid {p.lyrics_keyword_border};
            border-radius: 8px;
            padding: 8px 12px;
            min-height: 24px;
            color: {p.lyrics_keyword_text};
        }}
        QLineEdit#LyricsKeywordEdit:hover, QComboBox#LyricsSourceCombo:hover, QComboBox#LyricsPreviewModeCombo:hover {{
            border-color: {p.lyrics_keyword_hover_border};
            background: {p.lyrics_keyword_hover_bg};
        }}
        QLineEdit#LyricsKeywordEdit:focus, QComboBox#LyricsSourceCombo:focus, QComboBox#LyricsPreviewModeCombo:focus {{
            border: 1px solid {p.input_border_focus};
            background: {p.lyrics_keyword_bg};
        }}
        QScrollBar:vertical {{
            background: transparent;
            border: 0;
            width: 12px;
            margin: 4px 0 4px 0;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            border: 0;
            height: 12px;
            margin: 0 4px 0 4px;
        }}
        QScrollBar::handle:vertical {{
            background: {p.scrollbar_handle};
            border-radius: 6px;
            min-height: 48px;
            margin: 2px;
        }}
        QScrollBar::handle:horizontal {{
            background: {p.scrollbar_handle};
            border-radius: 6px;
            min-width: 48px;
            margin: 2px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {p.scrollbar_handle_hover};
        }}
        QScrollBar::handle:horizontal:hover {{
            background: {p.scrollbar_handle_hover};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
            background: transparent;
            border: 0;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
            background: transparent;
            border: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: transparent;
        }}
        """


# ════════════════════════════════════════════════════════════════════════
# AlignmentDropCard 配色：variant (blue/red) × 主题 (light/dark) 正交化
# ════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class DropCardPalette:
    """``AlignmentDropCard`` 一套配色。原代码用 ``dict[str, str]``，改成
    冻结 dataclass 让 typo 在 import 期就报错。

    属性名与原代码的 dict key 对齐（``accent`` / ``accent_border`` / …），
    迁移时只需把 ``palette["accent"]`` 改成 ``palette.accent``。
    """

    accent: str
    accent_border: str
    icon_background: str
    action_background: str
    hover_background: str
    selected_background: str
    selected_icon_background: str
    selected_action_background: str


# (variant, is_dark) → DropCardPalette
_DROP_CARD_VARIANTS: dict[str, dict[bool, DropCardPalette]] = {
    "blue": {
        False: DropCardPalette(
            accent="#4C8DFF",
            accent_border="#CFE0FF",
            icon_background="#EEF5FF",
            action_background="#F5F9FF",
            hover_background="#FAFCFF",
            selected_background="#EEF5FF",
            selected_icon_background="#CFE3FF",
            selected_action_background="#E4EEFF",
        ),
        True: DropCardPalette(
            accent="#5B9DFF",
            accent_border="#2E4A7A",
            icon_background="#1F2C40",
            action_background="#1B2638",
            hover_background="#202C40",
            selected_background="#243349",
            selected_icon_background="#2E4A7A",
            selected_action_background="#2A3C57",
        ),
    },
    "red": {
        False: DropCardPalette(
            accent="#FF5D72",
            accent_border="#FFD7DE",
            icon_background="#FFF0F3",
            action_background="#FFF7F8",
            hover_background="#FFFBFB",
            selected_background="#FFF1F4",
            selected_icon_background="#FFD6DE",
            selected_action_background="#FFE8ED",
        ),
        True: DropCardPalette(
            accent="#FF7A8C",
            accent_border="#5A3A40",
            icon_background="#3A2A2C",
            action_background="#2D2225",
            hover_background="#332629",
            selected_background="#3A2A2C",
            selected_icon_background="#5A3A40",
            selected_action_background="#4A3035",
        ),
    },
}


def drop_card_palette(variant: str) -> DropCardPalette:
    """获取 ``AlignmentDropCard`` 配色。

    Parameters
    ----------
    variant : str
        ``"blue"``（视频/对齐目标）或 ``"red"``（音频/源素材）。
        非法值 raise ``KeyError`` —— 调用方写错应该立刻发现。

    Returns
    -------
    DropCardPalette
        当前 ``theme.is_dark`` 对应的 palette。
    """
    return _DROP_CARD_VARIANTS[variant][theme.is_dark]
