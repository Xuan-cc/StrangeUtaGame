"""快捷键子页面。

_on_shortcut_changed 与原始 SettingsInterface 逻辑完全一致：
- 冲突检测后，通过 _notify_changed（即 _change_callback）通知外层保存。
- _SHORTCUT_ACTIONS / _SHORTCUT_MODES 保留为类属性，供测试直接引用。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from qfluentwidgets import FluentIcon as FIF, InfoBar, InfoBarPosition, SettingCardGroup

from ..cards import ShortcutSettingCard
from .base import SubSettingInterface


class ShortcutSubInterface(SubSettingInterface):

    _SHORTCUT_ACTIONS: list = [
        # (key, icon, title, content, default_timing, default_edit, scope,
        #  timing_content, edit_content, readonly)
        ("play_pause", FIF.PLAY, "播放/暂停", "切换播放和暂停", "D:short", "D:short", "both", None, None, False),
        ("stop", FIF.PAUSE, "停止", "停止播放", "S:short", "S:short", "both", None, None, False),
        ("speed_down", FIF.SPEED_OFF, "减速", "降低播放速度", "Q:short", "Q:short", "both", None, None, False),
        ("speed_up", FIF.SPEED_HIGH, "加速", "提高播放速度", "W:short", "W:short", "both", None, None, False),
        ("volume_up", FIF.VOLUME, "音量增大", "增大播放音量", "", "", "both", None, None, False),
        ("volume_down", FIF.MUTE, "音量减小", "减小播放音量", "", "", "both", None, None, False),
        ("nav_prev_line", FIF.UP, "上一行", "移动到上一歌词行", "UP:short", "UP:short", "both", None, None, False),
        ("nav_next_line", FIF.DOWN, "下一行", "移动到下一歌词行", "DOWN:short", "DOWN:short", "both", None, None, False),
        ("nav_prev_char", FIF.LEFT_ARROW, "上一字符", "在当前行内移动到上一个字符；若已在首字符则跳到上一行末字符", "LEFT:short", "LEFT:short", "both", None, None, False),
        ("nav_next_char", FIF.RIGHT_ARROW, "下一字符", "在当前行内移动到下一个字符；若已在末字符则跳到下一行首字符", "RIGHT:short", "RIGHT:short", "both", None, None, False),
        ("cycle_checkpoint_prev", FIF.SYNC, "切换字内节奏点（反向）", "在当前字符的多个节奏点之间反向循环切换（Alt+←）", "ALT+LEFT:short", "ALT+LEFT:short", "both", None, None, False),
        ("timestamp_up", FIF.UP, "时间戳+步长", "增加选中节奏点时间戳", "ALT+UP:short", "ALT+UP:short", "both", None, None, False),
        ("timestamp_down", FIF.DOWN, "时间戳-步长", "减少选中节奏点时间戳", "ALT+DOWN:short", "ALT+DOWN:short", "both", None, None, False),
        ("cycle_checkpoint", FIF.SYNC, "切换字内节奏点", "在当前字符的多个节奏点之间循环切换（Alt+→）", "ALT+RIGHT:short", "ALT+RIGHT:short", "both", None, None, False),
        ("edit_ruby", FIF.EDIT, "注音编辑", "编辑当前字符注音", "F2:short", "F2:short", "both", None, None, False),
        ("toggle_word_join", FIF.LINK, "连词", "连词/取消连词；划选多个字符时：全未连词则整段连为一个词，否则整段取消连词", "F3:short", "F3:short", "both", None, None, False),
        ("tag_now", FIF.PLAY, "打轴键", "打轴操作的按键【仅打轴模式】", "Space:short", "", "timing_only", None, None, False),
        ("tag_now_extra", FIF.PLAY, "打轴键 Extra", "打轴操作的备用按键【仅打轴模式】", "", "", "timing_only", None, None, False),
        ("tag_and_delete_next", FIF.PLAY, "打轴并删除下一节奏点", "记录当前节奏点时间戳，同时删除下一个节奏点本身（减少 check_count），光标跳至原第三个节奏点【仅打轴模式】", "", "", "timing_only", None, None, False),
        ("tag_now_editor", FIF.PLAY, "打轴键（编辑模式）", "编辑模式下打轴：记录当前进度条时间戳至当前节奏点【仅编辑模式】", "", "", "edit_only", None, None, False),
        ("tag_now_extra_editor", FIF.PLAY, "打轴键 Extra（编辑模式）", "编辑模式下打轴（备用键）：记录当前进度条时间戳至当前节奏点【仅编辑模式】", "", "", "edit_only", None, None, False),
        ("seek_back", FIF.LEFT_ARROW, "后退", "后退跳转【仅打轴模式】", "Z:short", "", "timing_only", None, None, False),
        ("seek_forward", FIF.CHEVRON_RIGHT, "前进", "前进跳转【仅打轴模式】", "X:short", "", "timing_only", None, None, False),
        ("delete_timestamp", FIF.DELETE, "删除当前时间戳并回滚", "删除跳转【仅打轴模式】", "Backspace:short", "", "timing_only", None, None, False),
        ("add_checkpoint", FIF.PIN, "增加节奏点", "增加当前字符的节奏点数量", "F5:short", "Space:short", "split", "增加当前字符的节奏点数量（默认 [）", "增加当前字符的节奏点数量（默认 Space）", False),
        ("remove_checkpoint", FIF.REMOVE, "删除节奏点", "减少当前字符的节奏点数量", "F6:short", "Backspace:short", "split", "减少当前字符的节奏点数量（默认 ]", "减少当前字符的节奏点数量（默认 Backspace）", False),
        ("toggle_line_end", FIF.TAG, "切换句尾", "切换当前字符的句尾标记", "F4:short", ".:short", "split", "切换当前字符的句尾标记（默认 句号）", "切换当前字符的句尾标记（默认 句号）", False),
        ("bulk_change", FIF.EDIT, "批量变更", "打开批量变更对话框", "CTRL+H:short", "CTRL+H:short", "both", None, None, False),
        ("modify_char", FIF.EDIT, "修改所选字符", "打开修改所选字符对话框", "", "", "both", None, None, False),
        ("insert_guide", FIF.ADD, "插入导唱符", "打开插入导唱符对话框", "", "", "both", None, None, False),
        ("modify_line", FIF.EDIT, "修改选中行", "打开修改选中行对话框", "", "", "both", None, None, False),
        ("analyze_rubies", FIF.SYNC, "注音分析", "自动分析全部注音", "", "", "both", None, None, False),
        ("analyze_rubies_by_line", FIF.SYNC, "按行注音分析", "仅分析当前行的注音", "", "", "both", None, None, False),
        ("analyze_rubies_selected", FIF.SYNC, "注音分析所选字符", "仅分析当前行选中字符的注音", "", "", "both", None, None, False),
        ("open_fulltext", FIF.EDIT, "全文本编辑", "打开全文本编辑界面", "CTRL+T:short", "CTRL+T:short", "both", None, None, False),
        ("delete_rubies_by_type", FIF.DELETE, "按类型删除注音", "按类型删除注音对话框", "", "", "both", None, None, False),
        ("set_singer_by_line", FIF.PEOPLE, "按行设置演唱者", "按行批量设置演唱者", "", "", "both", None, None, False),
        ("apply_singer", FIF.PEOPLE, "应用演唱者", "为选中字符设置演唱者", "", "", "both", None, None, False),
        ("timestamps_to_sentence_end", FIF.TAG, "时间戳转句尾", "取消所有节奏点、清除时间戳并标记为句尾", "", "", "both", None, None, False),
        ("clear_all_checkpoints", FIF.DELETE, "清除所有节奏点", "删除当前字符全部节奏点并取消句尾标记（cc=0，is_sentence_end=False）", "", "", "both", None, None, False),
        ("quick_export", FIF.SHARE, "快捷导出", "使用默认导出格式快速导出到文件", "", "", "both", None, None, False),
        ("insert_space", FIF.ADD, "插入空格", "在当前字符后插入空格", "M:short", "M:short", "both", None, None, False),
        ("undo", FIF.CANCEL, "撤销", "撤销操作", "CTRL+Z:short", "CTRL+Z:short", "both", None, None, True),
        ("redo", FIF.SYNC, "重做", "重做操作", "CTRL+Y:short", "CTRL+Y:short", "both", None, None, True),
        ("save", FIF.SAVE, "保存", "保存项目", "CTRL+S:short", "CTRL+S:short", "both", None, None, True),
        ("copy_chars", FIF.COPY, "复制字符", "复制选中字符的完整信息", "CTRL+C:short", "CTRL+C:short", "both", None, None, True),
        ("paste_lyrics", FIF.PASTE, "粘贴", "无歌词时粘贴整批歌词文本；已有歌词时在光标处插入（复制的字符或纯文本）", "CTRL+V:short", "CTRL+V:short", "both", None, None, True),
        ("insert_line_break", FIF.RETURN, "插入换行", "在光标处插入换行", "Enter:short", "Enter:short", "both", None, None, True),
        ("merge_line_up", FIF.RETURN, "合并上一行", "将当前行合并到上一行末尾", "Shift+Enter:short", "Shift+Enter:short", "both", None, None, True),
        ("delete_char", FIF.DELETE, "删除字符", "删除选中内容或当前字符", "Delete:short", "Delete:short", "both", None, None, True),
    ]

    _SHORTCUT_MODES: list = [
        ("timing_mode", "打轴模式（音乐播放时）"),
        ("edit_mode", "编辑模式（音乐暂停时）"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._shortcut_cards: dict[str, dict[str, ShortcutSettingCard]] = {
            mode_key: {} for mode_key, _ in self._SHORTCUT_MODES
        }
        self._loading = False
        self._init_ui()

        # 主题变化时刷新快捷键标签前缀颜色（深/浅模式颜色不同）
        from strange_uta_game.frontend.theme import theme
        theme.changed.connect(self._refresh_tag_colors)

    @staticmethod
    def _tag_colors() -> dict:
        """返回当前主题下快捷键标签前缀颜色。

        浅色模式：深蓝/深绿/深紫（对浅背景对比度高）
        深色模式：亮蓝/亮绿/亮紫（对深背景对比度高）
        """
        from strange_uta_game.frontend.theme import theme
        if theme.is_dark:
            return {"timing": "#60CDFF", "edit": "#6BCB77", "both": "#CE93D8"}
        return {"timing": "#0078D4", "edit": "#107C10", "both": "#5C2D91"}

    @staticmethod
    def _make_tag_html(scope: str, title: str, colors: dict) -> str:
        """生成带颜色标签前缀的 HTML 标题字符串。"""
        _map = {
            "both":         (colors["both"],   "[通用]"),
            "timing_only":  (colors["timing"], "[打轴]"),
            "edit_only":    (colors["edit"],   "[编辑]"),
            "split_timing": (colors["timing"], "[打轴]"),
            "split_edit":   (colors["edit"],   "[编辑]"),
        }
        if scope in _map:
            color, tag = _map[scope]
            return f'<span style="color:{color};font-weight:bold;">{tag}</span> {title}'
        return title

    def _refresh_tag_colors(self):
        """主题变化时重新为所有快捷键卡片标题应用正确的颜色标签。"""
        colors = self._tag_colors()
        for row in self._SHORTCUT_ACTIONS:
            key, _, title, _, _, _, scope, _, _, _ = row
            if scope == "both":
                card = self._shortcut_cards["timing_mode"].get(key)
                if card:
                    card.setTitle(self._make_tag_html("both", title, colors))
            elif scope == "timing_only":
                card = self._shortcut_cards["timing_mode"].get(key)
                if card:
                    card.setTitle(self._make_tag_html("timing_only", title, colors))
            elif scope == "edit_only":
                card = self._shortcut_cards["edit_mode"].get(key)
                if card:
                    card.setTitle(self._make_tag_html("edit_only", title, colors))
            elif scope == "split":
                card_t = self._shortcut_cards["timing_mode"].get(key)
                if card_t:
                    card_t.setTitle(self._make_tag_html("split_timing", title, colors))
                card_e = self._shortcut_cards["edit_mode"].get(key)
                if card_e:
                    card_e.setTitle(self._make_tag_html("split_edit", title, colors))

    def _init_ui(self):
        colors = self._tag_colors()

        group = SettingCardGroup("快捷键", self.scrollWidget)

        def _wrap(t, s):
            return self._make_tag_html(s, t, colors)

        for row in self._SHORTCUT_ACTIONS:
            key, icon, title, content, dt, de, scope, tc, ec, ro = row
            if scope == "both":
                card = ShortcutSettingCard(icon, "", content, dt, parent=group)
                card.setTitle(_wrap(title, "both"))
                if ro: card.setReadOnly(True)
                self._shortcut_cards["timing_mode"][key] = card
                self._shortcut_cards["edit_mode"][key] = card
                group.addSettingCard(card)
                if not ro:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "timing_only":
                card = ShortcutSettingCard(icon, "", content, dt, parent=group)
                card.setTitle(_wrap(title, "timing_only"))
                if ro: card.setReadOnly(True)
                self._shortcut_cards["timing_mode"][key] = card
                group.addSettingCard(card)
                if not ro:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "edit_only":
                card = ShortcutSettingCard(icon, "", content, de, parent=group)
                card.setTitle(_wrap(title, "edit_only"))
                if ro: card.setReadOnly(True)
                self._shortcut_cards["edit_mode"][key] = card
                group.addSettingCard(card)
                if not ro:
                    card.value_changed.connect(lambda v, c=card: self._on_shortcut_changed(c, v))
            elif scope == "split":
                ct = tc or content
                ce = ec or content
                card_t = ShortcutSettingCard(icon, "", ct, dt, parent=group)
                card_t.setTitle(_wrap(title, "split_timing"))
                if ro: card_t.setReadOnly(True)
                self._shortcut_cards["timing_mode"][key] = card_t
                group.addSettingCard(card_t)
                if not ro:
                    card_t.value_changed.connect(lambda v, c=card_t: self._on_shortcut_changed(c, v))
                card_e = ShortcutSettingCard(icon, "", ce, de, parent=group)
                card_e.setTitle(_wrap(title, "split_edit"))
                if ro: card_e.setReadOnly(True)
                self._shortcut_cards["edit_mode"][key] = card_e
                group.addSettingCard(card_e)
                if not ro:
                    card_e.value_changed.connect(lambda v, c=card_e: self._on_shortcut_changed(c, v))

        self.expandLayout.addWidget(group)

    # connect_signals 不需要额外操作，信号已在 _init_ui 中连接
    def connect_signals(self):
        pass

    def _on_shortcut_changed(self, changed_card: ShortcutSettingCard, new_value: str):
        """冲突检测，无冲突则通知外层保存。"""
        if self._loading:
            return

        new_pairs: list[tuple[str, str]] = []
        for k in new_value.split(","):
            k = k.strip()
            if not k:
                continue
            if ":" in k:
                kp, tp = k.rsplit(":", 1)
                new_pairs.append((kp.strip().upper(), tp.strip().lower()))
            else:
                new_pairs.append((k.upper(), "short"))

        action_titles = {a[0]: a[2] for a in self._SHORTCUT_ACTIONS}

        for mode_key, mode_label in self._SHORTCUT_MODES:
            mode_actions = self._shortcut_cards[mode_key]
            if not any(card is changed_card for card in mode_actions.values()):
                continue
            for action_key, card in mode_actions.items():
                if card is changed_card:
                    continue
                other_pairs = card.all_keys_with_trigger()
                if action_key == "tag_now":
                    expanded = []
                    for k, _ in other_pairs:
                        expanded.extend([(k, "short"), (k, "long")])
                    other_pairs = expanded
                for nk, nt in new_pairs:
                    if not nk:
                        continue
                    for ok, ot in other_pairs:
                        if nk == ok and nt == ot:
                            for btn in [changed_card.btn_key1, changed_card.btn_key2]:
                                if btn.get_key().strip().upper() == nk and btn.get_trigger_type() == nt:
                                    btn.restore_original_key()
                            trigger_label = "长按" if nt == "long" else "短按"
                            InfoBar.warning(
                                title="快捷键冲突",
                                content=f"[{mode_label}]「{action_titles[action_key]}」已占用{trigger_label}按键 {nk}",
                                orient=Qt.Orientation.Horizontal,
                                isClosable=True,
                                position=InfoBarPosition.TOP,
                                duration=4000,
                                parent=self,
                            )
                            return

        self._notify_changed()

    def load_settings(self, s):
        self._loading = True
        try:
            for mode_key, _ in self._SHORTCUT_MODES:
                for row in self._SHORTCUT_ACTIONS:
                    action_key = row[0]
                    default_timing = row[4]
                    default_edit = row[5]
                    default_key = default_timing if mode_key == "timing_mode" else default_edit
                    card = self._shortcut_cards[mode_key].get(action_key)
                    if card is None:
                        continue
                    value = s.get(f"shortcuts.{mode_key}.{action_key}", default_key)
                    card.setValue(value)
        finally:
            self._loading = False

    def collect_settings(self, s):
        for mode_key, _ in self._SHORTCUT_MODES:
            for row in self._SHORTCUT_ACTIONS:
                action_key = row[0]
                readonly = row[9] if len(row) > 9 else False
                if readonly:
                    continue
                card = self._shortcut_cards[mode_key].get(action_key)
                if card is None:
                    continue
                s.set(f"shortcuts.{mode_key}.{action_key}", card.value())
