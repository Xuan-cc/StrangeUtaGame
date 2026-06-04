"""自动保存子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import SpinSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class AutoSaveSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("自动保存", self.scrollWidget)
        self.card_auto_save_enabled = SwitchSettingCard(FIF.SAVE, "启用定时自动保存",
            "定时将项目保存为临时文件，防止闪退丢失数据", parent=g)
        self.card_auto_save_interval = SpinSettingCard(FIF.HISTORY, "自动保存间隔",
            "每隔多少分钟自动保存一次（1~60分钟）",
            min_val=1, max_val=60, step=1, suffix=" 分钟", parent=g)
        g.addSettingCard(self.card_auto_save_enabled)
        g.addSettingCard(self.card_auto_save_interval)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_auto_save_enabled.checked_changed.connect(self._notify_changed)
        self.card_auto_save_interval.value_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_auto_save_enabled.setChecked(s.get("auto_save.enabled", True))
        self.card_auto_save_interval.setValue(s.get("auto_save.interval_minutes", 5))

    def collect_settings(self, s):
        s.set("auto_save.enabled", self.card_auto_save_enabled.isChecked())
        s.set("auto_save.interval_minutes", self.card_auto_save_interval.value())
