"""自动保存子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import BrowseSettingCard, SpinSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class AutoSaveSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        tr = self.tr
        g = SettingCardGroup(tr("自动保存"), self.scrollWidget)
        self._tr_register(g, title_source="自动保存")
        self.card_auto_save_enabled = SwitchSettingCard(FIF.SAVE, tr("启用定时自动保存"),
            tr("定时将项目副本保存到文件旁，切换/关闭项目或另存为时自动清理"), parent=g)
        self._tr_register(self.card_auto_save_enabled,
            title_source="启用定时自动保存",
            content_source="定时将项目副本保存到文件旁，切换/关闭项目或另存为时自动清理")
        self.card_auto_save_interval = SpinSettingCard(FIF.HISTORY, tr("自动保存间隔"),
            tr("每隔多少分钟自动保存一次（1~60分钟）"),
            min_val=1, max_val=60, step=1, suffix=tr(" 分钟"), parent=g)
        self._tr_register(self.card_auto_save_interval,
            title_source="自动保存间隔",
            content_source="每隔多少分钟自动保存一次（1~60分钟）",
            suffix_source=" 分钟")
        self.card_backup_count = SpinSettingCard(FIF.HISTORY, tr("自动备份项目个数"),
            tr("保存或退出时在备份目录保留的项目备份份数（0 表示不备份）"),
            min_val=0, max_val=99999, step=1, suffix=tr(" 个"), parent=g)
        self._tr_register(self.card_backup_count,
            title_source="自动备份项目个数",
            content_source="保存或退出时在备份目录保留的项目备份份数（0 表示不备份）",
            suffix_source=" 个")
        self.card_crash_recovery = SwitchSettingCard(FIF.UPDATE, tr("闪退恢复"),
            tr("编辑后 2 秒内自动保存恢复文件，闪退后可恢复未保存的数据"), parent=g)
        self._tr_register(self.card_crash_recovery,
            title_source="闪退恢复",
            content_source="编辑后 2 秒内自动保存恢复文件，闪退后可恢复未保存的数据")
        self.card_backup_dir = BrowseSettingCard(FIF.FOLDER, tr("备份位置"),
            tr("项目备份与临时文件的存放目录（留空使用默认位置）"),
            clearable=True, parent=g)
        self._tr_register(self.card_backup_dir,
            title_source="备份位置",
            content_source="项目备份与临时文件的存放目录（留空使用默认位置）")
        self.card_default_save_dir = BrowseSettingCard(FIF.SAVE, tr("SUG默认保存目录"),
            tr("设置后，保存未命名项目时将始终优先使用此目录。\n留空则不启用，自动使用已保存项目 / 最近加载的文件所在目录。"),
            clearable=True, parent=g)
        self._tr_register(self.card_default_save_dir,
            title_source="SUG默认保存目录",
            content_source="设置后，保存未命名项目时将始终优先使用此目录。\n留空则不启用，自动使用已保存项目 / 最近加载的文件所在目录。")
        g.addSettingCard(self.card_auto_save_enabled)
        g.addSettingCard(self.card_auto_save_interval)
        g.addSettingCard(self.card_crash_recovery)
        g.addSettingCard(self.card_backup_count)
        g.addSettingCard(self.card_backup_dir)
        g.addSettingCard(self.card_default_save_dir)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_auto_save_enabled.checked_changed.connect(self._notify_changed)
        self.card_auto_save_interval.value_changed.connect(self._notify_changed)
        self.card_crash_recovery.checked_changed.connect(self._notify_changed)
        self.card_backup_count.value_changed.connect(self._notify_changed)
        self.card_backup_dir.path_changed.connect(self._notify_changed)
        self.card_default_save_dir.path_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_auto_save_enabled.setChecked(s.get("auto_save.enabled", True))
        self.card_auto_save_interval.setValue(s.get("auto_save.interval_minutes", 5))
        self.card_crash_recovery.setChecked(s.get("auto_save.crash_recovery_enabled", True))
        self.card_backup_count.setValue(s.get("auto_save.backup_count", 10))
        self.card_backup_dir.setText(s.get("auto_save.backup_dir", "") or "")
        self.card_default_save_dir.setText(s.get("auto_save.default_save_dir", "") or "")

    def collect_settings(self, s):
        s.set("auto_save.enabled", self.card_auto_save_enabled.isChecked())
        s.set("auto_save.interval_minutes", self.card_auto_save_interval.value())
        s.set("auto_save.crash_recovery_enabled", self.card_crash_recovery.isChecked())
        s.set("auto_save.backup_count", self.card_backup_count.value())
        s.set("auto_save.backup_dir", self.card_backup_dir.text().strip())
        s.set("auto_save.default_save_dir", self.card_default_save_dir.text().strip())
