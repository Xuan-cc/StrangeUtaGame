"""AutoCheck 子页面。"""

from __future__ import annotations

from qfluentwidgets import FluentIcon as FIF, SettingCardGroup

from ..cards import MultiBoolSettingCard, MultiCheckSettingCard, SwitchSettingCard
from .base import SubSettingInterface


class AutoCheckSubInterface(SubSettingInterface):
    _ROMAJI_EXCLUSIVE_DELETE_TYPES = {"hiragana", "katakana_hiragana_ruby", "katakana_english_ruby", "kanji"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loading_values = False
        self._delete_types_before_romanize: list[str] = []
        self._skip_restore_on_romanize_off = False
        self._init_ui()

    def _init_ui(self):
        tr = self.tr
        # 第二位标签是显示给用户的；左侧 key 是 config.json 里持久化的，**不能** tr()。
        # Group 标题是 "Auto Check"（产品名），不走 tr。
        g = SettingCardGroup("Auto Check", self.scrollWidget)
        self.card_checkpoint_chars = self._tr_register(
            MultiBoolSettingCard(
                FIF.MUSIC, tr("生成节奏点的字符类型"), tr("选择哪些字符类型自动生成节奏点"),
                items=[
                    ("hiragana", tr("ひらがな（平假名）")), ("katakana", tr("カタカナ（片假名）")),
                    ("kanji", tr("漢字（汉字）")), ("alphabet", tr("アルファベット（英文字母）")),
                    ("digit", tr("数字")), ("symbol", tr("記号（符号 + - * 等）")),
                    ("space", tr("空格")),
                    ("space_after_japanese", tr("  ↳日语后空格check")),
                    ("space_after_alphabet", tr("  ↳字母后空格check")),
                    ("space_after_symbol", tr("  ↳符号/数字后空格check")),
                ], parent=g),
            title_source="生成节奏点的字符类型",
            content_source="选择哪些字符类型自动生成节奏点")
        self.card_check_rules = self._tr_register(
            MultiBoolSettingCard(
                FIF.SETTING, tr("额外check规则"), tr("选择启用哪些自动节奏点规则"),
                items=[
                    ("check_n", tr("「ん/ン」check")), ("check_sokuon", tr("促音check")),
                    ("check_long_vowel", tr("长音符号check")), ("small_kana", tr("小写假名check")),
                    ("check_parentheses", tr("括号内文字check")),
                    ("check_empty_lines", tr("空行check")), ("check_line_start", tr("行首check")),
                    ("check_line_end", tr("行尾check")),
                    ("check_space_as_line_end", tr("空格视为句尾")),
                    ("check_english_word_end", tr("英文单词结尾句尾")),
                    ("english_syllable_check", tr("按音节Check英文单词")),
                ], parent=g),
            title_source="额外check规则",
            content_source="选择启用哪些自动节奏点规则")
        self.card_auto_on_load = self._tr_register(
            SwitchSettingCard(FIF.ACCEPT, tr("读取时自动check"),
                tr("导入文本后自动执行check分析"), parent=g),
            title_source="读取时自动check",
            content_source="导入文本后自动执行check分析")
        self.card_chinese_lyrics_detection = self._tr_register(
            SwitchSettingCard(FIF.LANGUAGE, tr("中文歌词检测"),
                tr("加载歌词时，若未检测到日文假名则自动切换为中文模式（汉字每字一个节奏点，跳过日文注音）"),
                parent=g),
            title_source="中文歌词检测",
            content_source="加载歌词时，若未检测到日文假名则自动切换为中文模式（汉字每字一个节奏点，跳过日文注音）")
        self.card_romanize_ruby = self._tr_register(
            SwitchSettingCard(FIF.LANGUAGE, tr("罗马音注音"),
                tr("需重新执行自动注音以生效"),
                parent=g),
            title_source="罗马音注音",
            content_source="需重新执行自动注音以生效")
        self.card_delete_ruby_types = self._tr_register(
            MultiCheckSettingCard(
                FIF.DELETE, tr("自动删除注音"), tr("自动注音完成后，自动删除指定类型的注音"),
                options=[
                    ("hiragana", tr("ひらがな（平假名）")),
                    ("katakana_hiragana_ruby", tr("カタカナ（片假名・注音为平假名）")),
                    ("katakana_english_ruby", tr("カタカナ（片假名・注音含有英文）")),
                    ("kanji", tr("漢字（汉字）")), ("alphabet", tr("アルファベット（英文字母）")),
                    ("number", tr("数字")), ("symbol", tr("記号（符号 + - * 等）")),
                    ("long_vowel", tr("長音符号（ー、～等）")), ("sokuon", tr("促音（っ/ッ）")),
                    ("other", tr("その他")), ("space", tr("空格")),
                ], parent=g),
            title_source="自动删除注音",
            content_source="自动注音完成后，自动删除指定类型的注音")
        for c in [self.card_checkpoint_chars, self.card_check_rules,
                  self.card_auto_on_load, self.card_chinese_lyrics_detection,
                  self.card_romanize_ruby, self.card_delete_ruby_types]:
            g.addSettingCard(c)
        self.expandLayout.addWidget(g)

    def connect_signals(self):
        self.card_checkpoint_chars.selection_changed.connect(self._notify_changed)
        self.card_check_rules.selection_changed.connect(self._notify_changed)
        self.card_auto_on_load.checked_changed.connect(self._notify_changed)
        self.card_chinese_lyrics_detection.checked_changed.connect(self._notify_changed)
        self.card_romanize_ruby.checked_changed.connect(self._on_romanize_ruby_changed)
        self.card_delete_ruby_types.selection_changed.connect(self._on_delete_ruby_types_changed)

    def _delete_types_without_romaji_exclusive(self, values: list[str]) -> list[str]:
        return [v for v in values if v not in self._ROMAJI_EXCLUSIVE_DELETE_TYPES]

    def _restore_delete_types_after_romaji(self, current: list[str]) -> list[str]:
        restored = list(current)
        for value in self._delete_types_before_romanize:
            if value in self._ROMAJI_EXCLUSIVE_DELETE_TYPES and value not in restored:
                restored.append(value)
        return restored

    def _on_romanize_ruby_changed(self, checked: bool):
        if self._loading_values:
            return
        if checked:
            selected = self.card_delete_ruby_types.selectedValues()
            self._delete_types_before_romanize = list(selected)
            filtered = self._delete_types_without_romaji_exclusive(selected)
            if filtered != selected:
                self.card_delete_ruby_types.setSelectedValues(filtered)
        elif self._skip_restore_on_romanize_off:
            self._skip_restore_on_romanize_off = False
            self._delete_types_before_romanize = list(self.card_delete_ruby_types.selectedValues())
        else:
            selected = self.card_delete_ruby_types.selectedValues()
            restored = self._restore_delete_types_after_romaji(selected)
            if restored != selected:
                self.card_delete_ruby_types.setSelectedValues(restored)
        self._notify_changed()

    def _on_delete_ruby_types_changed(self, selected: list[str]):
        if self._loading_values:
            return
        if (self.card_romanize_ruby.isChecked()
                and any(v in self._ROMAJI_EXCLUSIVE_DELETE_TYPES for v in selected)):
            self._skip_restore_on_romanize_off = True
            self.card_romanize_ruby.setChecked(False)
        if not self.card_romanize_ruby.isChecked():
            self._delete_types_before_romanize = list(self.card_delete_ruby_types.selectedValues())
        self._notify_changed()

    def load_settings(self, s):
        self._loading_values = True
        try:
            self.card_checkpoint_chars.setValues({
                "hiragana": s.get("auto_check.hiragana", True),
                "katakana": s.get("auto_check.katakana", True),
                "kanji": s.get("auto_check.kanji", True),
                "alphabet": s.get("auto_check.alphabet", False),
                "digit": s.get("auto_check.digit", False),
                "symbol": s.get("auto_check.symbol", False),
                "space": s.get("auto_check.space", False),
                "space_after_japanese": s.get("auto_check.space_after_japanese", True),
                "space_after_alphabet": s.get("auto_check.space_after_alphabet", True),
                "space_after_symbol": s.get("auto_check.space_after_symbol", True),
            })
            self.card_check_rules.setValues({
                "check_n": s.get("auto_check.check_n", True),
                "check_sokuon": s.get("auto_check.check_sokuon", True),
                "check_long_vowel": s.get("auto_check.check_long_vowel", False),
                "small_kana": s.get("auto_check.small_kana", False),
                "check_parentheses": s.get("auto_check.check_parentheses", True),
                "check_empty_lines": s.get("auto_check.check_empty_lines", False),
                "check_line_start": s.get("auto_check.check_line_start", False),
                "check_line_end": s.get("auto_check.check_line_end", True),
                "check_space_as_line_end": s.get("auto_check.check_space_as_line_end", True),
                "check_english_word_end": s.get("auto_check.check_english_word_end", True),
                "english_syllable_check": s.get("auto_check.english_syllable_check", True),
            })
            self.card_auto_on_load.setChecked(s.get("auto_check.auto_on_load", True))
            self.card_chinese_lyrics_detection.setChecked(s.get("auto_check.chinese_lyrics_detection", True))
            romanize_ruby = s.get("auto_check.romanize_ruby", False)
            saved_delete_types = s.get("auto_check.delete_ruby_types", [])
            if "katakana" in saved_delete_types:
                saved_delete_types.remove("katakana")
                if "katakana_hiragana_ruby" not in saved_delete_types:
                    saved_delete_types.append("katakana_hiragana_ruby")
                if "katakana_english_ruby" not in saved_delete_types:
                    saved_delete_types.append("katakana_english_ruby")
                s.set("auto_check.delete_ruby_types", saved_delete_types)
                s.save()
            if romanize_ruby:
                self._delete_types_before_romanize = list(saved_delete_types)
                filtered = self._delete_types_without_romaji_exclusive(saved_delete_types)
                if filtered != saved_delete_types:
                    saved_delete_types = filtered
                    s.set("auto_check.delete_ruby_types", saved_delete_types)
                    s.save()
            else:
                self._delete_types_before_romanize = list(saved_delete_types)
            self.card_romanize_ruby.setChecked(romanize_ruby)
            self.card_delete_ruby_types.setSelectedValues(saved_delete_types)
        finally:
            self._loading_values = False

    def collect_settings(self, s):
        for key, val in self.card_checkpoint_chars.values().items():
            s.set(f"auto_check.{key}", val)
        for key, val in self.card_check_rules.values().items():
            s.set(f"auto_check.{key}", val)
        s.set("auto_check.auto_on_load", self.card_auto_on_load.isChecked())
        s.set("auto_check.chinese_lyrics_detection", self.card_chinese_lyrics_detection.isChecked())
        delete_types = self.card_delete_ruby_types.selectedValues()
        romanize_ruby = self.card_romanize_ruby.isChecked()
        if romanize_ruby:
            filtered = self._delete_types_without_romaji_exclusive(delete_types)
            if filtered != delete_types:
                delete_types = filtered
                self.card_delete_ruby_types.setSelectedValues(delete_types)
        else:
            self._delete_types_before_romanize = list(delete_types)
        s.set("auto_check.romanize_ruby", romanize_ruby)
        s.set("auto_check.delete_ruby_types", delete_types)
