"""导出设定子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import BrowseSettingCard, ComboSettingCard, SpinSettingCard, TextSettingCard
from .base import SubSettingInterface

_FMT_TO_IDX = {
    "LRC (增强型)": 0, "LRC (逐行)": 1, "LRC (逐字)": 2,
    "KRA": 3, "TXT": 4, "SRT": 5, "txt2ass": 6, "ASS": 7,
    "Nicokara": 8, "Nicokara (带注音)": 9, "RL 编辑模式": 10,
    "LRC": 0,  # 旧配置兼容
}
_IDX_TO_FMT = {
    0: "LRC (增强型)", 1: "LRC (逐行)", 2: "LRC (逐字)",
    3: "KRA", 4: "TXT", 5: "SRT", 6: "txt2ass", 7: "ASS",
    8: "Nicokara", 9: "Nicokara (带注音)", 10: "RL 编辑模式",
}


class ExportSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("导出设定", self.scrollWidget)
        self.card_default_format = ComboSettingCard(FIF.SHARE, "默认导出格式",
            "导出歌词时的默认文件格式",
            items=["LRC (增强型)", "LRC (逐行)", "LRC (逐字)", "KRA", "TXT",
                   "SRT", "txt2ass", "ASS", "Nicokara", "Nicokara (带注音)", "RL 编辑模式"],
            parent=g)
        self.card_export_dir = BrowseSettingCard(FIF.FOLDER, "默认导出目录",
            "导出文件的默认保存位置", parent=g)
        self.card_software_compensation = SpinSettingCard(FIF.HISTORY, "软件导出补偿",
            "导出时给时间戳加上此补偿值（除.sug外的所有格式），负值=提前，正值=延后",
            min_val=-5000, max_val=5000, step=10, suffix=" ms", parent=g)
        self.card_nicokara_pause_char = TextSettingCard(FIF.EDIT, "Nicokara停顿符",
            "导出Nicokara（带注音）格式时，删除rubyTag中的此字符",
            placeholder="输入停顿符", max_length=5, parent=g)
        for c in [self.card_default_format, self.card_export_dir,
                  self.card_software_compensation, self.card_nicokara_pause_char]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_default_format.index_changed.connect(self._notify_changed)
        self.card_export_dir.path_changed.connect(self._notify_changed)
        # 软件导出补偿只在导出/导入时被消费，不影响任何运行时状态。
        # 走静默保存通道，避免触发整条 settings cascade
        # （_apply_settings 会遍历所有字符、可能触发 BASS 重载）。
        self.card_software_compensation.value_changed.connect(
            lambda v: self._silent_save("export.software_compensation_ms", v)
        )
        self.card_nicokara_pause_char.value_changed.connect(self._notify_changed)

    def load_settings(self, s):
        self.card_default_format.setCurrentIndex(
            _FMT_TO_IDX.get(s.get("export.default_format", "Nicokara (带注音)"), 9))
        export_dir = s.get("export.last_export_dir", "")
        if export_dir:
            self.card_export_dir.setText(export_dir)
        self.card_software_compensation.setValue(s.get("export.software_compensation_ms", 0))
        self.card_nicokara_pause_char.setValue(s.get("export.nicokara_pause_char", "^"))

    def collect_settings(self, s):
        s.set("export.default_format",
              _IDX_TO_FMT.get(self.card_default_format.currentIndex(), "Nicokara (带注音)"))
        export_dir = self.card_export_dir.text()
        if export_dir:
            s.set("export.last_export_dir", export_dir)
        s.set("export.software_compensation_ms", self.card_software_compensation.value())
        s.set("export.nicokara_pause_char", self.card_nicokara_pause_char.value())
