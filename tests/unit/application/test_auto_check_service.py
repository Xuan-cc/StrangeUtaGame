"""AutoCheckService 测试。"""

import pytest
from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Sentence
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import DummyAnalyzer


def _get_sudachi_analyzer():
    """尝试获取真实 SudachiAnalyzer；不可用则返回 None（测试 skip）。"""
    try:
        from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
            SudachiAnalyzer,
        )

        return SudachiAnalyzer()
    except Exception:
        return None


class TestAutoCheckService:
    """测试自动检查服务"""

    def test_analyze_sentence_simple(self):
        """测试分析简单日文"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        assert len(results) == 3
        assert results[0].char == "赤"
        assert results[1].char == "い"
        assert results[2].char == "花"

    def test_apply_to_sentence(self):
        """测试应用自动检查结果"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")

        service.apply_to_sentence(sentence)

        # 验证 chars 被设置
        assert sentence.chars == ["赤", "い", "花"]

        # 验证 characters 被创建
        assert len(sentence.characters) == 3

        # 验证最后一个字符标记为句尾
        assert sentence.characters[2].is_line_end

    def test_estimate_check_count(self):
        """测试估算节奏点数量"""
        service = AutoCheckService(DummyAnalyzer())

        count = service.estimate_check_count("赤い花")

        # DummyAnalyzer 直接返回字符数
        assert count == 3

    def test_keep_existing_timetags(self):
        """测试保留现有时间标签"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")
        sentence.characters[0].add_timestamp(1000)

        # 应用自动检查（保留时间标签）
        service.apply_to_sentence(sentence, keep_existing_timetags=True)

        # 验证时间标签保留
        assert len(sentence.characters[0].timestamps) == 1

    def test_clear_existing_timetags(self):
        """测试清除现有时间标签"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("赤い花", "s1")
        sentence.characters[0].add_timestamp(1000)

        # 应用自动检查（清除时间标签）
        service.apply_to_sentence(sentence, keep_existing_timetags=False)

        # 验证时间标签被清除
        assert len(sentence.characters[0].timestamps) == 0

    def test_flags_disable_hiragana(self):
        """测试关闭平假名打勾"""
        flags = {"hiragana": False, "kanji": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        assert results[1].check_count == 0

    def test_flags_check_line_end_disabled(self):
        """测试关闭行尾打勾"""
        flags = {"check_line_end": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        service.apply_to_sentence(sentence)

        assert not sentence.characters[-1].is_line_end


class TestA3RootCauseEmptyRubyGroup:
    """批 17 A.3 根因锁定（集成层红测）。

    用户原报错 verbatim：'Ruby 分组存在空组:'お#お#''
    触发：自动分析"大空" 或 "{大|おお}{空|そら}" 均抛异常。
    新 API 下 # 不再作为分组标记，断言改为验证 parts 结构合法。
    """

    def test_ookusora_raw_no_empty_group_exception(self):
        """A.3 端到端：自动分析"大空" 不应抛异常，且每个 part.text 非空"""
        analyzer = _get_sudachi_analyzer()
        if analyzer is None:
            pytest.skip("SudachiAnalyzer 不可用，跳过集成测试")

        service = AutoCheckService(analyzer)
        sentence = Sentence.from_text("大空", "s1")

        service.apply_to_sentence(sentence)

        for c in sentence.characters:
            if c.ruby:
                assert c.ruby.parts, f"parts 为空: {c.char}"
                for p in c.ruby.parts:
                    assert p.text, f"part.text 为空: char={c.char!r}"

    def test_ookusora_linked_no_empty_group_exception(self):
        """A.3 端到端：自动分析"{大|おお}{空|そら}" 不应抛异常"""
        analyzer = _get_sudachi_analyzer()
        if analyzer is None:
            pytest.skip("SudachiAnalyzer 不可用，跳过集成测试")

        from strange_uta_game.backend.infrastructure.parsers.inline_format import (
            from_inline_text,
        )

        sentence = from_inline_text("{大|おお}{空|そら}", "s1")
        service = AutoCheckService(analyzer)

        service.apply_to_sentence(sentence)

        for c in sentence.characters:
            if c.ruby:
                assert c.ruby.parts, f"parts 为空: {c.char}"
                for p in c.ruby.parts:
                    assert p.text, f"part.text 为空: char={c.char!r}"


class TestPunctuationCheckpointFlag:
    """#5：标点参与节奏点开关。

    默认关闭：()【】[]{}「」!?、， 等标点 check_count=0；
    开关启用：标点 check_count >= 1。
    """

    def test_punctuation_default_off(self):
        """默认关闭：标点 check_count=0"""
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text("ねえ、君", "s2")
        service.apply_to_sentence(sentence)
        for c in sentence.characters:
            if c.char == "、":
                assert c.check_count == 0, "默认情况下标点 check_count 必须为 0"

    def test_punctuation_enabled(self):
        """开关开启：标点 check_count >= 1"""
        service = AutoCheckService(
            DummyAnalyzer(),
            auto_check_flags={"checkpoint_on_punctuation": True},
        )
        sentence = Sentence.from_text("ねえ、君", "s1")
        service.apply_to_sentence(sentence)
        for c in sentence.characters:
            if c.char == "、":
                assert c.check_count >= 1, "开关启用时标点 check_count 必须 >= 1"

    def test_punctuation_full_set_default_off(self):
        """覆盖完整 PUNCTUATION_SET：默认全部为 0"""
        from strange_uta_game.backend.domain import PUNCTUATION_SET

        text = "あ" + "".join(PUNCTUATION_SET) + "い"
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text(text, "s1")
        service.apply_to_sentence(sentence)
        for c in sentence.characters:
            if c.char in PUNCTUATION_SET:
                assert c.check_count == 0, f"标点 {c.char!r} 默认 check_count 必须为 0"


class TestSelectedCheckpointShift:
    """选中 cp 在 check_count 缩减后顺延。"""

    def test_shift_within_char_truncates(self):
        from strange_uta_game.backend.domain import Project, Sentence

        project = Project()
        sentence = Sentence.from_text("赤い花", "s1")
        for c in sentence.characters:
            c.check_count = 3
        project.sentences.append(sentence)
        project.set_selected_checkpoint(0, 0, 2)
        # 缩减
        sentence.characters[0].check_count = 1
        project.shift_selected_checkpoint_if_lost()
        loc = project.get_selected_checkpoint()
        assert loc == (0, 0, 0), f"应截断到 cp 0，实际 {loc}"

    def test_shift_to_next_char_when_zero(self):
        from strange_uta_game.backend.domain import Project, Sentence

        project = Project()
        sentence = Sentence.from_text("、あ", "s1")
        sentence.characters[0].check_count = 1  # 标点先有 cp
        sentence.characters[1].check_count = 1
        project.sentences.append(sentence)
        project.set_selected_checkpoint(0, 0, 0)
        # 模拟开关关闭 → 标点变 0
        sentence.characters[0].check_count = 0
        project.shift_selected_checkpoint_if_lost()
        loc = project.get_selected_checkpoint()
        assert loc == (0, 1, 0), f"应顺延到下一字符 cp 0，实际 {loc}"

    def test_shift_clears_when_all_lost(self):
        from strange_uta_game.backend.domain import Project, Sentence

        project = Project()
        sentence = Sentence.from_text("、", "s1")
        sentence.characters[0].check_count = 1
        project.sentences.append(sentence)
        project.set_selected_checkpoint(0, 0, 0)
        sentence.characters[0].check_count = 0
        project.shift_selected_checkpoint_if_lost()
        assert project.get_selected_checkpoint() is None


class TestBlankLineLineStartEndGuard:
    """Q1：空行（text.strip() 为空）不应被 check_line_start/check_line_end 强制打 CP。

    即使 check_line_start=True / check_line_end=True，只要句子文本 strip 后为空，
    行首 CP 和行尾 CP 都必须被压制。这是"空行不自动 CP"语义相对于 line_start/end 的优先级。
    """

    def test_blank_line_whitespace_line_start_suppressed(self):
        """空行（全是空格）+ check_line_start=True → 不强制 max(count,1)"""
        flags = {"check_line_start": True, "space": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("   ", "s1")

        results = service.analyze_sentence(sentence)

        for r in results:
            assert r.check_count == 0, f"空行首字符不应被强制 CP，实际 {r.check_count}"

    def test_blank_line_whitespace_line_end_suppressed(self):
        """空行（全是空格）+ check_line_end=True → is_line_end/is_sentence_end 均为 False"""
        flags = {"check_line_end": True, "space": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("   ", "s1")

        service.apply_to_sentence(sentence)

        assert sentence.characters, "空格行应保留字符"
        for c in sentence.characters:
            assert not c.is_line_end, "空行不应有 is_line_end"
            assert not c.is_sentence_end, "空行不应有 is_sentence_end"

    def test_non_blank_line_line_start_still_works(self):
        """非空行 + check_line_start=True → 首字符 check_count >= 1（回归保护）"""
        flags = {"check_line_start": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("あい", "s1")

        results = service.analyze_sentence(sentence)

        assert results[0].check_count >= 1, "非空行首字符应被 check_line_start 强制 >=1"

    def test_non_blank_line_line_end_still_works(self):
        """非空行 + check_line_end=True → 末字符 is_line_end=True（回归保护）"""
        flags = {"check_line_end": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("あい", "s1")

        service.apply_to_sentence(sentence)

        assert sentence.characters[-1].is_line_end, "非空行末字符应 is_line_end=True"
        assert sentence.characters[-1].is_sentence_end, "非空行末字符应 is_sentence_end=True"

    def test_blank_line_update_checkpoints_from_rubies_line_start_suppressed(self):
        """update_checkpoints_from_rubies：空行 + check_line_start=True → 首字符 check_count=0"""
        flags = {"check_line_start": True, "space": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("   ", "s1")
        for c in sentence.characters:
            c.check_count = 0

        service.update_checkpoints_from_rubies(sentence)

        for c in sentence.characters:
            assert c.check_count == 0, f"空行 update 后不应被强制 CP，实际 {c.check_count}"

    def test_blank_line_update_checkpoints_from_rubies_line_end_suppressed(self):
        """update_checkpoints_from_rubies：空行 + check_line_end=True → is_line_end=False"""
        flags = {"check_line_end": True, "space": False}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("   ", "s1")

        service.update_checkpoints_from_rubies(sentence)

        for c in sentence.characters:
            assert not c.is_line_end, "空行 update 后不应 is_line_end"
            assert not c.is_sentence_end, "空行 update 后不应 is_sentence_end"


class TestFallbackSplitPeelKana:
    """连词回退：头尾假名剥离策略（_fallback_split_peel_kana）"""

    def test_kana_suffix_preserved(self):
        """可愛い / かわいい → 尾部い自注音保留"""
        service = AutoCheckService(DummyAnalyzer())
        parts = service._fallback_split_peel_kana("可愛い", "かわいい")
        assert len(parts) == 3
        assert parts[2] == "い", f"末尾 い 应自注音，实际 {parts[2]}"
        assert parts[0], "首汉字应有注音"

    def test_pure_kanji_no_candidates_first_char_all(self):
        """无候选回退：XYZ/ABC → 首字全吃（保留原语义）"""
        service = AutoCheckService(DummyAnalyzer())
        parts = service._fallback_split_peel_kana("XYZ", "ABC")
        assert parts == ["ABC", "", ""], f"无候选应首字全吃，实际 {parts}"

    def test_kana_prefix_and_suffix_preserved(self):
        """お可愛い / おかわいい → 头尾假名都保留自注音"""
        service = AutoCheckService(DummyAnalyzer())
        parts = service._fallback_split_peel_kana("お可愛い", "おかわいい")
        assert len(parts) == 4
        assert parts[0] == "お", f"头部 お 应自注音，实际 {parts[0]}"
        assert parts[3] == "い", f"末尾 い 应自注音，实际 {parts[3]}"

    def test_single_char_returns_full_reading(self):
        """单字：直接返回 reading"""
        service = AutoCheckService(DummyAnalyzer())
        parts = service._fallback_split_peel_kana("漢", "かん")
        assert parts == ["かん"]

    def test_empty_reading_fallback(self):
        """空 reading：全空"""
        service = AutoCheckService(DummyAnalyzer())
        parts = service._fallback_split_peel_kana("漢字", "")
        assert parts == ["", ""]


class TestLibraryBlockFallbackLinking:
    """library 块走 fallback 路径后同块汉字自动连词（端到端）"""

    def test_kawaii_library_block_links_kanji(self):
        """可愛い：同一 morpheme 的 可→愛 应 linked"""
        analyzer = _get_sudachi_analyzer()
        if analyzer is None:
            pytest.skip("SudachiAnalyzer 不可用")
        service = AutoCheckService(analyzer)
        sentence = Sentence.from_text("可愛い", "s1")
        service.apply_to_sentence(sentence)

        chars = sentence.characters
        assert len(chars) == 3
        assert chars[0].char == "可"
        assert chars[1].char == "愛"
        assert chars[2].char == "い"
        # 关键：可→愛 linked（同 morpheme 同 block）
        assert chars[0].linked_to_next, "可→愛 应 linked"
        # 愛 仍展示自己的 ruby（后字继续展示自己的 ruby）
        assert chars[1].ruby is not None and len(chars[1].ruby.parts) == 2
        # い 是假名独立块，不参与连词
        assert not chars[1].linked_to_next, "愛→い 不应 linked（い 是独立假名块）"

    def test_daibouken_dict_path_links_all(self):
        """大冒険（字典逗号分隔路径）：大→冒→険 全 linked"""
        analyzer = _get_sudachi_analyzer()
        if analyzer is None:
            pytest.skip("SudachiAnalyzer 不可用")
        service = AutoCheckService(analyzer)
        sentence = Sentence.from_text("大冒険", "s1")
        service.apply_to_sentence(sentence)

        chars = sentence.characters
        assert len(chars) == 3
        assert chars[0].linked_to_next, "大→冒 应 linked"
        assert chars[1].linked_to_next, "冒→険 应 linked"
        # 各字有自己的 mora ruby
        assert all(c.ruby is not None and len(c.ruby.parts) == 2 for c in chars)

    def test_ashita_library_block_links(self):
        """明日：morpheme 整块 → 明→日 linked"""
        analyzer = _get_sudachi_analyzer()
        if analyzer is None:
            pytest.skip("SudachiAnalyzer 不可用")
        service = AutoCheckService(analyzer)
        sentence = Sentence.from_text("明日", "s1")
        service.apply_to_sentence(sentence)

        chars = sentence.characters
        assert len(chars) == 2
        assert chars[0].linked_to_next, "明→日 应 linked"


class TestEnglishWordCheckpoints:
    """批 18 #9：英文词组节奏点规则
    - 多字母英文词：首字 cp=1，中间字母 cp=0，末字母自动标 is_sentence_end
    - 适用于自动分析节奏点路径（apply_to_sentence / analyze_sentence）
    - 同时覆盖 e2k 命中（Hello/Happy/honey/day）和 fallback（Heyyyyy）两条分支
    """

    def _apply(self, text: str):
        service = AutoCheckService(DummyAnalyzer())
        sentence = Sentence.from_text(text, "s1")
        service.apply_to_sentence(sentence)
        return sentence.characters

    def _assert_word_rule(self, chars, start: int, end: int, label: str):
        """断言 chars[start:end] 满足 首=1cp/中=0/末=is_sentence_end 规则"""
        assert chars[start].check_count == 1, (
            f"{label}: 首字 '{chars[start].char}' 应 cp=1, 实际 {chars[start].check_count}"
        )
        for i in range(start + 1, end):
            assert chars[i].check_count == 0, (
                f"{label}: 字 '{chars[i].char}' (idx={i}) 应 cp=0, 实际 {chars[i].check_count}"
            )
        assert chars[end - 1].is_sentence_end, (
            f"{label}: 末字 '{chars[end - 1].char}' 应 is_sentence_end=True"
        )

    def test_hello_world(self):
        """Hello world：两词均按规则；e2k 命中分支"""
        chars = self._apply("Hello world")
        self._assert_word_rule(chars, 0, 5, "Hello")
        self._assert_word_rule(chars, 6, 11, "world")

    def test_hello_comma_world(self):
        """Hello, world：comma 在 'o' 之后，'o' 仍应 is_sentence_end"""
        chars = self._apply("Hello, world")
        self._assert_word_rule(chars, 0, 5, "Hello")
        # comma idx=5，space idx=6，world idx=7..11
        self._assert_word_rule(chars, 7, 12, "world")

    def test_i_love_you(self):
        """I love you：'I' 单字母词跳过规则；love/you 按规则"""
        chars = self._apply("I love you")
        # love: idx 2..6 ('l','o','v','e')
        self._assert_word_rule(chars, 2, 6, "love")
        # you: idx 7..10
        self._assert_word_rule(chars, 7, 10, "you")

    def test_japanese_mixed_english(self):
        """今日はHappy honey day：日文 + 多个英文词混排"""
        text = "今日はHappy honey day"
        chars = self._apply(text)
        # 'Happy' = idx 3..8
        self._assert_word_rule(chars, 3, 8, "Happy")
        # 'honey' = idx 9..14
        self._assert_word_rule(chars, 9, 14, "honey")
        # 'day' = idx 15..18
        self._assert_word_rule(chars, 15, 18, "day")

    def test_one_apple_one_day(self):
        """One apple one day, doctor go away.：每词的末字应 is_sentence_end"""
        text = "One apple one day, doctor go away."
        chars = self._apply(text)
        self._assert_word_rule(chars, 0, 3, "One")
        self._assert_word_rule(chars, 4, 9, "apple")
        self._assert_word_rule(chars, 10, 13, "one")
        self._assert_word_rule(chars, 14, 17, "day")
        self._assert_word_rule(chars, 19, 25, "doctor")
        self._assert_word_rule(chars, 26, 28, "go")
        self._assert_word_rule(chars, 29, 33, "away")

    def test_heyyyyy_fallback(self):
        """Heyyyyy...：OOV 词走 english_fallback 分支，规则同样应用"""
        chars = self._apply("Heyyyyyyyyyyyyyyy")
        self._assert_word_rule(chars, 0, len(chars), "Heyyyyy")

