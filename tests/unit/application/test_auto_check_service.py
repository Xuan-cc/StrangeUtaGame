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

    def test_flags_kanji_single_check(self):
        """测试汉字节奏点限定为1"""
        flags = {"kanji_single_check": True}
        service = AutoCheckService(DummyAnalyzer(), auto_check_flags=flags)
        sentence = Sentence.from_text("赤い花", "s1")

        results = service.analyze_sentence(sentence)

        for result in results:
            if result.char in ("赤", "花"):
                assert result.check_count <= 1

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
