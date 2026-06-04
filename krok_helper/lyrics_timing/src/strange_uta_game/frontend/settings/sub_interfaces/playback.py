"""演奏控制子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import ComboSettingCard, DoubleSpinSettingCard, SpinSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class PlaybackSubInterface(SubSettingInterface):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        g = SettingCardGroup("演奏控制", self.scrollWidget)
        self.card_volume = SpinSettingCard(FIF.VOLUME, "默认音量", "音频加载后的初始音量",
            min_val=0, max_val=100, suffix=" %", parent=g)
        self.card_speed = DoubleSpinSettingCard(FIF.SPEED_HIGH, "默认速度", "音频加载后的初始播放速度",
            min_val=0.2, max_val=2.0, step=0.05, decimals=2, suffix=" x", parent=g)
        self.card_speed_min = DoubleSpinSettingCard(FIF.SPEED_HIGH, "速度滑块最小值", "播放栏速度滑块的下限",
            min_val=0.2, max_val=2.0, step=0.05, decimals=2, suffix=" x", parent=g)
        self.card_speed_max = DoubleSpinSettingCard(FIF.SPEED_HIGH, "速度滑块最大值", "播放栏速度滑块的上限",
            min_val=0.2, max_val=2.0, step=0.05, decimals=2, suffix=" x", parent=g)
        self.card_fast_forward = SpinSettingCard(FIF.CHEVRON_RIGHT, "快进量", "按下快进键跳过的时间",
            min_val=1000, max_val=30000, step=1000, suffix=" ms", parent=g)
        self.card_rewind = SpinSettingCard(FIF.LEFT_ARROW, "快退量", "按下快退键后退的时间",
            min_val=1000, max_val=30000, step=1000, suffix=" ms", parent=g)
        self.card_auto_play = SwitchSettingCard(FIF.PLAY, "自动播放", "加载音频文件后自动开始播放", parent=g)
        self.card_hq_speed = SwitchSettingCard(
            FIF.SPEED_HIGH, "高质量倍速",
            "开启后无爆音，但倍速切换可能有延迟，且需占用 .cache 进行离线预渲染；"
            "关闭后占用较小、切换即时，但可能会有爆音。默认开启。",
            parent=g,
        )
        self.card_jump_before = SpinSettingCard(FIF.HISTORY, "删除节奏点跳转提前量",
            "删除节奏点时跳转到该时间戳前的毫秒数",
            min_val=0, max_val=30000, step=500, suffix=" ms", parent=g)
        self.card_scroll_mode = ComboSettingCard(
            FIF.SYNC, "歌词预览滚动模式",
            "打轴时歌词预览是否跟随播放位置自动滚动",
            items=["自动滚动（操作后挂起 6 秒）", "始终滚动", "从不滚动"],
            parent=g,
        )
        for c in [self.card_volume, self.card_speed, self.card_speed_min,
                  self.card_speed_max, self.card_fast_forward,
                  self.card_rewind, self.card_auto_play, self.card_hq_speed,
                  self.card_jump_before, self.card_scroll_mode]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_volume.value_changed.connect(self._notify_changed)
        self.card_speed.value_changed.connect(self._notify_changed)
        self.card_speed_min.value_changed.connect(self._notify_changed)
        self.card_speed_max.value_changed.connect(self._notify_changed)
        self.card_fast_forward.value_changed.connect(self._notify_changed)
        self.card_rewind.value_changed.connect(self._notify_changed)
        self.card_auto_play.checked_changed.connect(self._notify_changed)
        self.card_hq_speed.checked_changed.connect(self._notify_changed)
        self.card_jump_before.value_changed.connect(self._notify_changed)
        self.card_scroll_mode.index_changed.connect(self._notify_changed)

    _SCROLL_MODE_TO_INDEX = {"auto": 0, "always": 1, "never": 2}
    _INDEX_TO_SCROLL_MODE = {0: "auto", 1: "always", 2: "never"}

    def load_settings(self, s):
        self.card_volume.setValue(s.get("audio.default_volume", 80))
        self.card_speed.setValue(s.get("audio.default_speed", 1.0))
        self.card_speed_min.setValue(s.get("audio.speed_slider_min", 0.2))
        self.card_speed_max.setValue(s.get("audio.speed_slider_max", 1.0))
        self.card_fast_forward.setValue(s.get("timing.fast_forward_ms", 5000))
        self.card_rewind.setValue(s.get("timing.rewind_ms", 5000))
        self.card_auto_play.setChecked(s.get("audio.auto_play_on_load", False))
        self.card_hq_speed.setChecked(s.get("audio.hq_speed_change", True))
        self.card_jump_before.setValue(s.get("timing.jump_before_ms", 3000))
        scroll_mode = s.get("timing.scroll_mode", "auto")
        self.card_scroll_mode.setCurrentIndex(self._SCROLL_MODE_TO_INDEX.get(scroll_mode, 0))

    def collect_settings(self, s):
        s.set("audio.default_volume", self.card_volume.value())
        s.set("audio.default_speed", self.card_speed.value())
        min_speed, max_speed = self._normalized_speed_range()
        s.set("audio.speed_slider_min", min_speed)
        s.set("audio.speed_slider_max", max_speed)
        s.set("audio.auto_play_on_load", self.card_auto_play.isChecked())
        s.set("audio.hq_speed_change", self.card_hq_speed.isChecked())
        s.set("timing.fast_forward_ms", self.card_fast_forward.value())
        s.set("timing.rewind_ms", self.card_rewind.value())
        s.set("timing.jump_before_ms", self.card_jump_before.value())
        scroll_mode = self._INDEX_TO_SCROLL_MODE.get(self.card_scroll_mode.currentIndex(), "auto")
        s.set("timing.scroll_mode", scroll_mode)

    def _normalized_speed_range(self) -> tuple[float, float]:
        min_speed = max(0.2, min(2.0, self.card_speed_min.value()))
        max_speed = max(0.2, min(2.0, self.card_speed_max.value()))
        if min_speed > max_speed:
            min_speed, max_speed = max_speed, min_speed
        return round(min_speed / 0.05) * 0.05, round(max_speed / 0.05) * 0.05
