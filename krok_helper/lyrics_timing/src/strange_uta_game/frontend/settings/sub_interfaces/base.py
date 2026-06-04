"""设置子页面基类。

每个 SubSettingInterface 负责：
  1. 创建自己的 UI 控件（_init_ui）
  2. 将 UI 控件的变更信号连接到自己的 _on_any_changed（_connect_signals）
  3. 从 AppSettings 读取初始值写入 UI（load_settings）
  4. 从 UI 读取当前值写回 AppSettings（collect_settings）

SettingsInterface（外层）负责：
  - 在 preload 阶段依次调用各子页面的 load_settings
  - 在需要保存时调用各子页面的 collect_settings，然后 settings.save() + notify
  - 把"有值变化"事件从子页面冒泡到 SettingsInterface._schedule_auto_save
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import ExpandLayout, ScrollArea


class SubSettingInterface(ScrollArea):
    """设置子页面基类。

    子类实现：
        _init_ui(self)          — 创建控件并加入 expandLayout
        connect_signals(self)   — 把各控件的变更信号连接到 _notify_changed
        load_settings(self, s)  — 从 AppSettings s 读取值填写 UI
        collect_settings(self, s) — 从 UI 读取值写入 AppSettings s
    """

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._change_callback: Optional[Callable] = None
        self._silent_save_callback: Optional[Callable[[str, Any], None]] = None

        self.scrollWidget = QWidget()
        self.expandLayout = ExpandLayout(self.scrollWidget)

        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setObjectName("subSettingInterface")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 断开 autoFillBackground 对系统 QPalette 的依赖
        self.viewport().setAutoFillBackground(False)
        self.scrollWidget.setAutoFillBackground(False)

        self.expandLayout.setSpacing(28)
        self.expandLayout.setContentsMargins(20, 20, 20, 20)

    def set_change_callback(self, cb: Callable) -> None:
        """由 SettingsInterface 注入，有设置变更时调用。"""
        self._change_callback = cb

    def set_silent_save_callback(self, cb: Callable[[str, Any], None]) -> None:
        """由 SettingsInterface 注入：把单个 key 静默写入 AppSettings 并落盘，
        不触发 settings_changed / store.notify("settings") cascade。

        用于"只在导出/导入时才被消费、改完不影响任何运行时状态"的设置项
        （如 ``export.software_compensation_ms``），避免每次微调都跑一遍
        timing_interface._apply_settings 全量重应用。
        """
        self._silent_save_callback = cb

    def _notify_changed(self, *_args) -> None:
        """控件变更时通知外层触发自动保存。"""
        if self._change_callback is not None:
            self._change_callback()

    def _silent_save(self, path: str, value: Any) -> None:
        """直接写 ``path → value`` 到 AppSettings 并落盘，不触发 cascade。"""
        if self._silent_save_callback is not None:
            self._silent_save_callback(path, value)

    # ── 子类必须实现的接口 ──────────────────────────────────────────

    def connect_signals(self) -> None:
        """把各控件的变更信号连到 self._notify_changed。子类实现。"""

    def load_settings(self, settings) -> None:
        """从 AppSettings 读取值填写 UI 控件。子类实现。"""

    def collect_settings(self, settings) -> None:
        """从 UI 控件读取值写入 AppSettings。子类实现。"""
