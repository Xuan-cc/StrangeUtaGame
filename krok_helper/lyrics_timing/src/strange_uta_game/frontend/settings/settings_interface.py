"""设置界面 — Pivot 选项卡 + 子页面布局。

架构说明
--------
- SettingsInterface 本身是一个 ScrollArea（沿用原来类型），
  但内部用 vBoxLayout 组织：标题 → Pivot → QStackedWidget。
- 每个选项卡对应一个 SubSettingInterface 子页面，子页面自己负责：
    load_settings(s)     从 AppSettings 读取值填写 UI
    collect_settings(s)  从 UI 读取值写入 AppSettings
    connect_signals()    把控件变更信号连到 _notify_changed
- SettingsInterface 作为协调层：
    _preload()           在主事件循环空闲时初始化子页面（QTimer.singleShot(0, ...)）
    _schedule_auto_save  防抖，500ms 后触发 _do_auto_save
    _do_auto_save        遍历所有子页面 collect_settings → save → notify
- reload_from_disk() / get_settings() 等外部接口保持兼容。

re-export（向后兼容原有 import 路径）
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SettingCard,
    SettingCardGroup,
)

from strange_uta_game.__version__ import __version__ as _app_version

from .app_settings import AppSettings, _parse_rl_dictionary
from .calibration_dialog import CalibrationCanvas, CalibrationDialog
from .checkpoint_marker_dialog import CheckpointMarkerDialog
from .cards import (
    BrowseSettingCard,
    ComboSettingCard,
    DoubleSpinSettingCard,
    MultiBoolSettingCard,
    MultiCheckSettingCard,
    ShortcutSettingCard,
    SpinSettingCard,
    SwitchSettingCard,
    TextSettingCard,
)
from .dictionary_dialog import DictionaryEditDialog
from .nicokara_dialog import NicokaraTagsDialog
from .pivot import SettingPivot
from .sub_interfaces import (
    AboutSubInterface,
    AutoCheckSubInterface,
    AutoSaveSubInterface,
    DictionarySubInterface,
    ExportSubInterface,
    NetworkSubInterface,
    PlaybackSubInterface,
    ShortcutSubInterface,
    TimingSubInterface,
    UISubInterface,
)

__all__ = [
    "SettingsInterface",
    # re-exports for backward compatibility
    "AppSettings",
    "DictionaryEditDialog",
    "NicokaraTagsDialog",
    "CalibrationDialog",
    "CalibrationCanvas",
    "SpinSettingCard",
    "DoubleSpinSettingCard",
    "SwitchSettingCard",
    "ComboSettingCard",
    "BrowseSettingCard",
    "ShortcutSettingCard",
    "TextSettingCard",
    "_parse_rl_dictionary",
    # InfoBar re-export（测试中使用）
    "InfoBar",
]


class SettingsInterface(ScrollArea):
    """设置界面（Pivot 选项卡 + 子页面）。"""

    settings_changed = pyqtSignal()

    # 选项卡配置：(routeKey, 显示文本)
    TAB_CONFIG: list[tuple[str, str]] = [
        ("playback",   "演奏控制"),
        ("timing",     "打轴"),
        ("auto_save",  "自动保存"),
        ("auto_check", "AutoCheck"),
        ("dictionary", "读音词典"),
        ("ui",         "界面"),
        ("export",     "导出"),
        ("shortcut",   "快捷键"),
        ("network",    "网络"),
        ("about",      "关于"),
    ]

    # ── 类属性兼容：测试代码通过 SettingsInterface._SHORTCUT_* 访问 ──
    _SHORTCUT_ACTIONS = ShortcutSubInterface._SHORTCUT_ACTIONS
    _SHORTCUT_MODES   = ShortcutSubInterface._SHORTCUT_MODES

    def __init__(self, parent=None, settings_provider=None):
        super().__init__(parent)
        self._store = None
        self._settings_provider = settings_provider
        self._settings = AppSettings(provider=settings_provider)
        self._embedded = settings_provider is not None
        self._tab_config = [
            item for item in self.TAB_CONFIG
            if not (self._embedded and item[0] == "network")
        ]
        self._initialized = False       # True = _preload 已调度（但可能还未完成）
        self._fully_initialized = False  # True = 所有子页面全部创建并连接完毕

        # 防抖自动保存定时器
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(500)
        self._auto_save_timer.timeout.connect(self._do_auto_save)
        self._loading_settings = False

        # 主容器
        self.scrollWidget = QWidget()
        self.vBoxLayout = QVBoxLayout(self.scrollWidget)

        # 标题
        self.settingLabel = QLabel("设置", self)
        self.settingLabel.setObjectName("settingLabel")
        self._update_setting_label_style()  # 应用初始颜色（含主题颜色）

        # 选项卡 + 堆叠区
        self.pivot = SettingPivot(self)
        self.stackedWidget = QStackedWidget(self)

        self._init_widget()
        self._init_layout()

        # 主题变化时更新标题标签颜色（setStyleSheet 会在 Qt 内部创建独立 Palette
        # 副本，阻止后续 QApplication.setPalette 的 WindowText 更新传入）
        from strange_uta_game.frontend.theme import theme
        theme.changed.connect(self._update_setting_label_style)

        # 在主事件循环空闲时预载子页面（不阻塞 MainWindow 初始化）。
        # 注意：不能用 singleShot(0)，那会在 MSFluentWindow.showEvent 处理期间
        # 触发大量子控件创建，导致窗口重新布局重置任务栏图标。
        # 用 500ms 延迟确保窗口完全显示稳定后再初始化。
        QTimer.singleShot(500, self._preload)

    # ── 初始化 ────────────────────────────────────────────────────────

    def _update_setting_label_style(self):
        """更新标题 QLabel 的样式（含主题文字颜色）。

        setStyleSheet 会让 Qt 为该 widget 生成独立 Palette 副本，导致后续
        QApplication.setPalette 的 WindowText 变化不再传入。
        因此必须在 theme.changed 时显式写入 color，确保深/浅色正确切换。
        """
        from strange_uta_game.frontend.theme import theme
        color = theme.text_primary.name()
        self.settingLabel.setStyleSheet(
            f"QLabel#settingLabel{{"
            f"font-size:28px;font-weight:bold;padding:10px 0;color:{color};"
            f"}}"
        )

    def _init_widget(self):
        self.setWidget(self.scrollWidget)
        self.setWidgetResizable(True)
        self.setViewportMargins(0, 0, 0, 0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setObjectName("settingInterface")
        self.scrollWidget.setObjectName("scrollWidget")
        # 断开 autoFillBackground 对系统 QPalette 的依赖，避免 OS 主题改变时
        # viewport / scrollWidget 的背景色跟随系统走（深色污染）。
        self.viewport().setAutoFillBackground(False)
        self.scrollWidget.setAutoFillBackground(False)

    def _init_layout(self):
        self.vBoxLayout.setContentsMargins(30, 20, 30, 0)
        self.vBoxLayout.setSpacing(10)
        self.vBoxLayout.addWidget(self.settingLabel)

        self.pivot.setFixedHeight(40)
        self.vBoxLayout.addWidget(self.pivot)

        for key, text in self._tab_config:
            self.pivot.addItem(
                routeKey=key,
                text=text,
                onClick=lambda checked, k=key: self._on_tab_changed(k),
            )

        self.vBoxLayout.addWidget(self.stackedWidget)

        self.pivot.setCurrentItem(self._tab_config[0][0])
        self.stackedWidget.setCurrentIndex(0)

    def _preload(self):
        """分批创建子页面，每批之间让事件循环有机会处理消息，避免阻塞 UI。"""
        if self._initialized:
            return
        self._initialized = True

        # 第一批：创建子页面实例（分两次，每次让出事件循环）
        self.playbackInterface  = PlaybackSubInterface(self)
        self.timingInterface    = TimingSubInterface(self)
        self.autoSaveInterface  = AutoSaveSubInterface(self)
        self.autoCheckInterface = AutoCheckSubInterface(self)
        self.dictionaryInterface= DictionarySubInterface(self)

        QTimer.singleShot(0, self._preload_second_batch)

    def _preload_second_batch(self):
        """第二批：创建剩余子页面。"""
        self.uiInterface        = UISubInterface(self)
        self.exportInterface    = ExportSubInterface(self)
        self.shortcutInterface  = ShortcutSubInterface(self)
        self.networkInterface   = None if self._embedded else NetworkSubInterface(self)
        self.aboutInterface     = AboutSubInterface(self)

        QTimer.singleShot(0, self._preload_finalize)

    def _preload_finalize(self):
        """第三批：连接信号、加载设置、注入 updater UI。"""
        # 把所有子页面按顺序加入 stackedWidget
        interfaces = [
            self.playbackInterface, self.timingInterface, self.autoSaveInterface,
            self.autoCheckInterface, self.dictionaryInterface, self.uiInterface,
            self.exportInterface, self.shortcutInterface,
            self.aboutInterface,
        ]
        if self.networkInterface is not None:
            interfaces.insert(-1, self.networkInterface)
        for iface in interfaces:
            self.stackedWidget.addWidget(iface)

        # 传入 store（若已由外层设置）
        if self._store is not None:
            self.uiInterface.set_store(self._store)

        # 连接保存/重置按钮
        self.aboutInterface.btn_save.clicked.connect(self._on_save)
        self.aboutInterface.btn_reset.clicked.connect(self._reset_settings)

        # 让每个子页面把控件变更信号连到各自的 _notify_changed，
        # 再通过 set_change_callback 冒泡到 _schedule_auto_save。
        # 同时注入 _silent_save 通道：用于那些只在导出/导入时才被消费、
        # 改完不影响任何运行时状态的设置项，绕开整条 settings cascade。
        for iface in [
            self.playbackInterface, self.timingInterface, self.autoSaveInterface,
            self.autoCheckInterface, self.dictionaryInterface, self.uiInterface,
            self.exportInterface, self.shortcutInterface,
        ]:
            iface.set_change_callback(self._schedule_auto_save)
            iface.set_silent_save_callback(self._silent_save_setting)
            iface.connect_signals()

        # 初始加载设置
        self._load_current_settings()

        # 注入 updater UI（只在初始化阶段执行一次）
        if self.networkInterface is not None:
            self.networkInterface.attach_updater_ui(self._settings)

        self._fully_initialized = True

    # ── 选项卡切换 ────────────────────────────────────────────────────

    def _on_tab_changed(self, routeKey: str):
        tab_map = {k: i for i, (k, _) in enumerate(self._tab_config)}
        self.stackedWidget.setCurrentIndex(tab_map.get(routeKey, 0))

    # ── 外部接口 ──────────────────────────────────────────────────────

    def set_store(self, store):
        """由 MainWindow 注入 ProjectStore。"""
        self._store = store
        if self._fully_initialized:
            self.uiInterface.set_store(store)

    def get_settings(self) -> AppSettings:
        return self._settings

    def reload_from_disk(self):
        """从磁盘重新加载配置并刷新 UI（由 MainWindow.switchTo 调用）。"""
        self._settings.reload()
        if self._fully_initialized:
            self._load_current_settings()

    # ── 自动保存 ──────────────────────────────────────────────────────

    def _schedule_auto_save(self, *_args):
        if self._loading_settings:
            return
        self._auto_save_timer.start()

    def _silent_save_setting(self, path: str, value) -> None:
        """直接持久化单个 key，不触发 settings_changed / notify("settings")。

        子页面对"只在导出/导入时才被消费"的设置项调用本方法，避免每次
        微调都跑一遍 timing_interface._apply_settings 全量重应用
        （后者会遍历项目所有字符、可能触发 BASS 重载，是已知的 cascade
        噪声源；参见 commit fccb832 关于 cascade 内异常变原生闪退的记录）。
        """
        if self._loading_settings:
            return
        try:
            self._settings.set(path, value)
            self._settings.save()
        except Exception as e:
            print(f"[Settings] 静默保存 {path} 失败: {e}")

    def _do_auto_save(self):
        if not self._fully_initialized:
            return
        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")
        self._apply_theme_setting()

    def _apply_theme_setting(self):
        from strange_uta_game.frontend.theme import theme, ThemeMode
        theme_value = self._settings.get("ui.theme", "auto")
        theme.mode = {"light": ThemeMode.LIGHT, "dark": ThemeMode.DARK}.get(
            theme_value, ThemeMode.AUTO)

    # ── 数据绑定 ──────────────────────────────────────────────────────

    def _load_current_settings(self):
        self._loading_settings = True
        try:
            s = self._settings
            for iface in [
                self.playbackInterface, self.timingInterface, self.autoSaveInterface,
                self.autoCheckInterface, self.dictionaryInterface, self.uiInterface,
                self.exportInterface, self.shortcutInterface, self.aboutInterface,
            ]:
                iface.load_settings(s)
            if self.networkInterface is not None:
                self.networkInterface.load_settings(s)
            self._apply_theme_setting()
        finally:
            self._loading_settings = False

    def _collect_settings(self):
        s = self._settings
        for iface in [
            self.playbackInterface, self.timingInterface, self.autoSaveInterface,
            self.autoCheckInterface, self.dictionaryInterface, self.uiInterface,
            self.exportInterface, self.shortcutInterface,
        ]:
            iface.collect_settings(s)

    # ── 事件 ──────────────────────────────────────────────────────────

    def showEvent(self, a0):
        """切换到设置页面时刷新数据（完全初始化后才刷新）。"""
        if self._fully_initialized:
            self._load_current_settings()
        super().showEvent(a0)

    def hideEvent(self, a0):
        """离开设置页面时：关闭校准弹窗、flush 未完成的自动保存。"""
        if self._fully_initialized:
            self.timingInterface.close_calibration()
            if self._auto_save_timer.isActive():
                self._auto_save_timer.stop()
                self._do_auto_save()
        super().hideEvent(a0)

    def keyPressEvent(self, a0: QKeyEvent | None):
        super().keyPressEvent(a0)

    # ── 操作 ──────────────────────────────────────────────────────────

    def _on_save(self):
        self._collect_settings()
        self._settings.save()
        self.settings_changed.emit()
        if self._store is not None:
            self._store.notify("settings")
        InfoBar.success(
            title="设置已保存", content="所有设置已保存到配置文件",
            orient=Qt.Orientation.Horizontal, isClosable=True,
            position=InfoBarPosition.TOP, duration=3000, parent=self,
        )

    def _reset_settings(self):
        from PyQt6.QtWidgets import QMessageBox
        msg = QMessageBox(self)
        msg.setWindowTitle("确认重置")
        msg.setText("确定要将所有设置重置为默认值吗？\n这将覆盖您当前的设置（用户词典和演唱者预设不受影响）。")
        btn_yes = msg.addButton("是", QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("否", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_yes)
        msg.exec()
        if msg.clickedButton() is btn_yes:
            try:
                if self._settings_provider is not None:
                    self._settings = AppSettings(provider=self._settings_provider)
                    self._settings._settings = self._settings._load_packaged_defaults()
                    self._settings.save()
                else:
                    if self._settings._config_path.exists():
                        self._settings._config_path.unlink()
                    self._settings = AppSettings()
                self._load_current_settings()
                InfoBar.success(title="设置已重置", content="所有设置已恢复为默认值",
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=3000, parent=self)
            except Exception as e:
                InfoBar.error(title="重置失败", content=str(e),
                    orient=Qt.Orientation.Horizontal, isClosable=True,
                    position=InfoBarPosition.TOP, duration=5000, parent=self)
