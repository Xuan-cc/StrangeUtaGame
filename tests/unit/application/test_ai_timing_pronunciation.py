"""AI 打轴阶段 A：PronunciationResolver / PronunciationPlan 领域契约测试。

验收门槛（计划文档 §11 阶段 A）：
- 项目已有 RubyPart 优先于所有自动分析；
- 已有整段 Ruby 优先，仅缺失字符自动补充；
- 日中英同一行混排；
- 数字、标点、空格和装饰符；
- tokenizer 不支持字符（OTHER 脚本缺口）在计划中保持缺口（执行前阻断依据）；
- token 到 Character/checkpoint 反向映射稳定；
- 任何自动分析都不会覆盖已有项目标注。
"""

import pytest

from strange_uta_game.backend.application import (
    ProjectDriftError,
    PronunciationResolver,
    PronunciationSource,
    ScriptKind,
)
from strange_uta_game.backend.application.ai_timing import compute_annotation_digest
from strange_uta_game.backend.domain import (
    Character,
    Project,
    Ruby,
    RubyPart,
    Sentence,
)
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    RubyAnalyzer,
    RubyResult,
)


class _FixedReadingAnalyzer(RubyAnalyzer):
    """按固定映射给单字注音的测试分析器（其余字符自注音）。"""

    def __init__(self, mapping=None):
        self._mapping = mapping or {}

    def analyze(self, text):
        return [
            RubyResult(
                text=ch,
                reading=self._mapping.get(ch, ch),
                start_idx=i,
                end_idx=i + 1,
            )
            for i, ch in enumerate(text)
        ]

    def get_reading(self, text):
        return "".join(self._mapping.get(ch, ch) for ch in text)


class _BlockAnalyzer(RubyAnalyzer):
    """把整行作为单一分析块返回固定读音的多字块分析器。

    用于验证多字块读音按 checkpoint 分发（split_ruby_for_checkpoints 路径）。
    """

    def __init__(self, reading):
        self._reading = reading

    def analyze(self, text):
        return [
            RubyResult(text=text, reading=self._reading, start_idx=0, end_idx=len(text))
        ]

    def get_reading(self, text):
        return self._reading


def _project_with_sentence(sentence: Sentence) -> Project:
    project = Project()
    project.sentences = [sentence]
    return project


def _sentence(chars_spec, singer_id="s1"):
    """按 [(char, check_count, ruby_parts or None, is_sentence_end)] 构建句子。"""
    characters = []
    for char, cc, parts, sent_end in chars_spec:
        ruby = Ruby(parts=[RubyPart(text=p) for p in parts]) if parts else None
        characters.append(
            Character(
                char=char,
                check_count=cc,
                ruby=ruby,
                is_sentence_end=sent_end,
                singer_id=singer_id,
            )
        )
    return Sentence(singer_id=singer_id, characters=characters)


class TestCollectExistingAnnotations:
    """只读收集：既有标注的优先级与结构映射。"""

    def test_ruby_part_per_checkpoint(self):
        s = _sentence([("赤", 2, ["あ", "か"], False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert [(u.reading, u.source) for u in plan.units] == [
            ("あ", PronunciationSource.EXISTING_PART),
            ("か", PronunciationSource.EXISTING_PART),
        ]
        assert plan.is_complete

    def test_ruby_parts_longer_than_check_count_merge_tail(self):
        """parts 多于 checkpoint 时，尾段并入最后一个 checkpoint（不丢失读音）。"""
        s = _sentence([("赤", 2, ["あ", "か", "い"], False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        readings = [u.reading for u in plan.units]
        assert readings == ["あ", "かい"]
        assert plan.is_complete

    def test_ruby_parts_shorter_than_check_count_stay_pending(self):
        """parts 少于 checkpoint 时不虚构、不重切：缺口保留，is_complete=False。"""
        s = _sentence([("赤", 3, ["あ", "か"], False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert [u.reading for u in plan.units] == ["あ", "か", None]
        assert not plan.is_complete
        assert len(plan.pending_units) == 1

    def test_self_readable_kana_without_ruby(self):
        s = _sentence([("あ", 1, None, False), ("ッ", 1, None, False), ("ー", 1, None, False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        for u in plan.units:
            assert u.source == PronunciationSource.EXISTING_CHARACTER
            assert u.reading == u.char_text
            assert u.has_model_token()

    def test_kanji_without_ruby_is_gap(self):
        s = _sentence([("赤", 1, None, False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        (u,) = plan.units
        assert u.reading is None
        assert u.source is None
        assert u.is_pending

    def test_punctuation_and_space_units_are_structural(self):
        """标点/空格单元不要求读音（expects_token=False），保留结构映射。"""
        s = _sentence([("！", 1, None, False), (" ", 1, None, False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert len(plan.units) == 2
        assert all(not u.expects_token for u in plan.units)
        assert not any(u.is_pending for u in plan.units)
        assert plan.is_complete

    def test_check_count_zero_and_no_sentence_end_yields_no_units(self):
        s = _sentence([(" ", 0, None, False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert plan.units == []

    def test_sentence_end_virtual_unit(self):
        """句尾呼吸点成为虚拟单元：无读音、无 token，checkpoint_idx=check_count。"""
        s = _sentence([("い", 1, None, True)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert len(plan.units) == 2
        virtual = plan.units[1]
        assert virtual.is_sentence_end
        assert virtual.checkpoint_idx == 1
        assert not virtual.has_model_token()
        assert not virtual.is_pending

    def test_pause_placeholder_part_has_no_token(self):
        s = _sentence([("ん", 2, ["ん", "^"], False)])
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert [u.has_model_token() for u in plan.units] == [True, False]
        # 用户自定义停顿符同样可通过 pause_chars 识别
        s2 = _sentence([("ん", 2, ["ん", "＊"], False)])
        plan2 = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s2)
        )
        assert [u.has_model_token(pause_chars={"＊"}) for u in plan2.units] == [
            True,
            False,
        ]
        assert plan.is_complete and plan2.is_complete

    def test_reverse_mapping_covers_all_timing_points(self):
        """单元坐标与工程全部打轴点（all_timestamps 域）一一对应。"""
        s1 = _sentence([("赤", 2, ["あ", "か"], False), ("い", 1, None, True)])
        s2 = _sentence([("空", 1, ["そら"], False), ("！", 1, None, False)])
        project = Project()
        project.sentences = [s1, s2]
        plan = PronunciationResolver().collect_existing_annotations(project)

        expected = []
        for line_idx, sentence in enumerate(project.sentences):
            for char_idx, ch in enumerate(sentence.characters):
                for cp_idx in range(ch.check_count):
                    expected.append((line_idx, char_idx, cp_idx))
                if ch.is_sentence_end:
                    expected.append((line_idx, char_idx, ch.check_count))
        actual = [u.location for u in plan.units]
        assert actual == expected
        for loc in expected:
            assert plan.unit_at(*loc) is not None

    def test_script_kinds(self):
        s = _sentence(
            [
                ("あ", 1, None, False),
                ("赤", 1, None, False),
                ("A", 1, None, False),
                ("3", 1, None, False),
                ("，", 1, None, False),
                (" ", 1, None, False),
            ]
        )
        plan = PronunciationResolver().collect_existing_annotations(
            _project_with_sentence(s)
        )
        assert [u.script for u in plan.units] == [
            ScriptKind.KANA,
            ScriptKind.KANJI,
            ScriptKind.LATIN,
            ScriptKind.NUMBER,
            ScriptKind.PUNCTUATION,
            ScriptKind.SPACE,
        ]


class TestFillMissingAnnotations:
    """缺口补足：只补空白，绝不覆盖已有标注。"""

    def test_fill_kanji_gap_via_analyzer(self):
        s = _sentence([("赤", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"赤": "あか"}), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        (u,) = plan.units
        assert u.reading == "あか"
        assert u.source == PronunciationSource.GENERATED
        assert plan.filled_count == 1
        assert plan.is_complete
        # 工程对象未被写回
        assert s.characters[0].ruby is None

    def test_existing_ruby_never_overwritten_even_if_analyzer_differs(self):
        """分析器给出不同读音时，既有标注原样保留（绝对优先级）。"""
        s = _sentence([("赤", 1, ["あか"], False), ("青", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"赤": "せき", "青": "あお"}),
            chinese_mode=False,
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        by_char = {u.char_text: u for u in plan.units}
        assert by_char["赤"].reading == "あか"
        assert by_char["赤"].source == PronunciationSource.EXISTING_PART
        assert by_char["青"].reading == "あお"
        assert by_char["青"].source == PronunciationSource.GENERATED

    def test_fill_multi_checkpoint_char_groups_by_mora(self):
        """多 checkpoint 缺口字符按 mora 分组到各节奏点。"""
        s = _sentence([("空", 2, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"空": "そら"}), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert [(u.reading, u.source) for u in plan.units] == [
            ("そ", PronunciationSource.GENERATED),
            ("ら", PronunciationSource.GENERATED),
        ]
        assert plan.filled_count == 2

    def test_fill_multichar_block_distributes_reading(self):
        """多字分析块按 split_ruby_for_checkpoints 逐字分发（均分语义）。"""
        s = _sentence([("世", 1, None, False), ("界", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_BlockAnalyzer("せかい"), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert [u.reading for u in plan.units] == ["せか", "い"]

    def test_kanji_self_reading_means_failure_and_stays_pending(self):
        """汉字拿到自身作读音 = 分析失败 → 保持缺口（执行前阻断依据）。"""
        s = _sentence([("赤", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer(), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        (u,) = plan.units
        assert u.reading is None
        assert not plan.is_complete
        assert plan.filled_count == 0

    def test_partial_ruby_char_is_not_generated(self):
        """已有部分 Ruby（parts<check_count）的字符不属于自动补注音范围。"""
        s = _sentence([("赤", 3, ["あ"], False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"赤": "あかい"}), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert [u.reading for u in plan.units] == ["あ", None, None]
        assert not plan.is_complete
        assert s.characters[0].ruby is not None  # 工程 Ruby 未被改动

    def test_mixed_japanese_line_kanji_gap_filled(self):
        """日中英同一行混排：假名自读、拉丁自读、汉字走日语分析。"""
        s = _sentence(
            [
                ("見", 1, None, False),
                ("て", 1, None, False),
                ("L", 1, None, False),
                ("3", 1, None, False),
                ("！", 1, None, False),
            ]
        )
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"見": "み", "て": "て", "3": "さん"})
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        readings = {u.char_text: u for u in plan.units}
        assert readings["見"].reading == "み"
        assert readings["て"].reading == "て"  # 假名自读
        assert readings["て"].source == PronunciationSource.EXISTING_CHARACTER
        assert readings["L"].reading == "L"  # 拉丁自读音（分析器透传）
        assert readings["L"].source == PronunciationSource.GENERATED
        assert readings["3"].reading == "さん"
        assert readings["！"].reading is None  # 标点结构单元
        assert plan.is_complete

    def test_chinese_mode_auto_detected_per_project(self):
        """全工程无假名 → 自动中文模式（与 SUG is_chinese_lyrics 口径一致）。"""
        s = _sentence([("你", 1, None, False), ("好", 1, None, False)])
        zh = _FixedReadingAnalyzer({"你": "nǐ", "好": "hǎo"})
        ja = _FixedReadingAnalyzer({"你": "jūn", "好": "kōu"})
        resolver = PronunciationResolver(analyzer=ja, chinese_analyzer=zh)
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert [u.reading for u in plan.units] == ["nǐ", "hǎo"]

    def test_chinese_mode_false_forces_japanese_for_pure_kanji(self):
        """显式非中文模式：纯汉字行（如日文全汉字行）走日语分析器。"""
        s = _sentence([("世", 1, None, False), ("界", 1, None, False)])
        ja = _BlockAnalyzer("せかい")
        zh = _FixedReadingAnalyzer({"世": "shì", "界": "jiè"})
        resolver = PronunciationResolver(
            analyzer=ja, chinese_analyzer=zh, chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert [u.reading for u in plan.units] == ["せか", "い"]

    def test_chinese_mode_detection_uses_whole_project_text(self):
        """中文检测按整工程文本：任一行含假名即非中文，纯汉字行也走日语。"""
        # 注：の 被 SUG 判定集刻意排除（中文歌装饰字），此处用る验证
        s1 = _sentence([("君", 1, None, False), ("る", 1, None, False)])
        s2 = _sentence([("世", 1, None, False), ("界", 1, None, False)])
        project = Project()
        project.sentences = [s1, s2]
        ja = _FixedReadingAnalyzer({"君": "きみ", "世": "せ", "界": "かい"})
        zh = _FixedReadingAnalyzer({"世": "shì", "界": "jiè"})
        resolver = PronunciationResolver(analyzer=ja, chinese_analyzer=zh)
        plan = resolver.resolve_project(project, fill_missing=True)
        readings = {(u.line_idx, u.char_text): u.reading for u in plan.units}
        assert readings[(0, "君")] == "きみ"
        assert readings[(0, "る")] == "る"
        assert readings[(1, "世")] == "せ"
        assert readings[(1, "界")] == "かい"

    def test_analyzer_exception_records_error_and_keeps_pending(self):
        class _BrokenAnalyzer(RubyAnalyzer):
            def analyze(self, text):
                raise RuntimeError("engine down")

            def get_reading(self, text):
                return ""

        s = _sentence([("赤", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_BrokenAnalyzer(), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        assert not plan.is_complete
        assert len(plan.generation_errors) == 1
        assert "自动注音失败" in plan.generation_errors[0]
        assert "第 1 行" in plan.generation_errors[0]

    def test_unsupported_script_is_structural_not_blocking(self):
        """OTHER 脚本（零宽字符等混入物）是结构单元：不产 token、不构成缺口。

        与计划文档 §4.3「标点、空格和装饰字符可以不产生模型 token，但必须
        保留结构映射」一致；「tokenizer 不支持字符」的执行前阻断由阶段 B
        基于真实模型词表判定，阶段 A 只保证结构映射完整。
        """
        s = _sentence([("\u200b", 1, None, False), ("赤", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"赤": "あか"}), chinese_mode=False
        )
        plan = resolver.resolve_project(_project_with_sentence(s), fill_missing=True)
        zwsp, kanji = plan.units
        assert zwsp.script == ScriptKind.OTHER
        assert not zwsp.expects_token
        assert not zwsp.is_pending
        assert not zwsp.has_model_token()
        assert kanji.reading == "あか"


class TestAnnotationDigest:
    """摘要漂移检测：collect 与 fill/应用之间工程发生变化必须被发现。"""

    def test_digest_changes_on_annotation_edit(self):
        s = _sentence([("赤", 1, ["あか"], False)])
        project = _project_with_sentence(s)
        resolver = PronunciationResolver()
        plan = resolver.collect_existing_annotations(project)
        before = plan.annotation_digest

        s.characters[0].set_ruby(Ruby(parts=[RubyPart(text="せき")]))
        assert compute_annotation_digest(project) != before

    def test_digest_changes_on_checkpoint_edit(self):
        s = _sentence([("赤", 1, None, False)])
        project = _project_with_sentence(s)
        digest = compute_annotation_digest(project)
        s.characters[0].set_check_count(2)
        assert compute_annotation_digest(project) != digest

    def test_digest_ignores_timestamps(self):
        """时间戳不属于标注摘要：AI 打轴会覆盖时间戳，其变化不应使快照失效。"""
        s = _sentence([("赤", 1, ["あか"], False)])
        project = _project_with_sentence(s)
        digest = compute_annotation_digest(project)
        s.characters[0].add_timestamp(1234)
        s.characters[0].sentence_end_ts = None
        assert compute_annotation_digest(project) == digest

    def test_fill_raises_on_drifted_project(self):
        s = _sentence([("赤", 1, None, False), ("青", 1, None, False)])
        project = _project_with_sentence(s)
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"赤": "あか"})
        )
        plan = resolver.collect_existing_annotations(project)
        # collect 之后、fill 之前工程被修改
        project.sentences[0].characters[1].set_ruby(
            Ruby(parts=[RubyPart(text="あお")])
        )
        with pytest.raises(ProjectDriftError):
            resolver.fill_missing_annotations(plan, project)


class TestProjectWriteSafety:
    """自动分析全程只读工程对象（应用前不写回任何标注/结构）。"""

    def test_resolve_does_not_mutate_project(self):
        s = _sentence(
            [
                ("赤", 2, ["あ", "か"], False),
                ("見", 1, None, False),
                (" ", 0, None, False),
                ("L", 1, None, False),
            ]
        )
        project = _project_with_sentence(s)
        before = compute_annotation_digest(project)
        ruby_snapshot = [
            ([p.text for p in c.ruby.parts] if c.ruby else None)
            for c in s.characters
        ]
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"見": "み"}), chinese_mode=False
        )
        resolver.resolve_project(project, fill_missing=True)
        after = compute_annotation_digest(project)
        assert before == after
        ruby_after = [
            ([p.text for p in c.ruby.parts] if c.ruby else None)
            for c in s.characters
        ]
        assert ruby_after == ruby_snapshot

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    DummyAnalyzer,
)


class TestLatinLinkedWords:
    """2026-08 修复：拉丁连词（SUG 英文词约定）整词注音。"""

    def _line(self):
        from strange_uta_game.backend.domain import Character, Sentence
        return Sentence(singer_id="s1", characters=[
            Character(char="T", check_count=1, ruby=None, linked_to_next=True, singer_id="s1"),
            Character(char="a", check_count=0, ruby=None, linked_to_next=True, singer_id="s1"),
            Character(char="k", check_count=0, ruby=None, linked_to_next=True, singer_id="s1"),
            Character(char="e", check_count=0, ruby=None, linked_to_next=False, singer_id="s1"),
            Character(char=" ", check_count=0, ruby=None, singer_id="s1"),
            Character(char="m", check_count=1, ruby=None, linked_to_next=True, singer_id="s1"),
            Character(char="e", check_count=0, ruby=None, linked_to_next=False, singer_id="s1"),
        ])

    def test_whole_word_reading_and_token(self):
        from strange_uta_game.backend.application.ai_timing.alignment import (
            build_alignment_tokens,
        )
        from strange_uta_game.backend.domain import Project
        project = Project()
        project.sentences = [self._line()]
        resolver = PronunciationResolver(analyzer=DummyAnalyzer(), chinese_mode=False)
        plan = resolver.resolve_project(project, fill_missing=True)
        units = [u for u in plan.units if not u.is_sentence_end]
        assert [(u.char_text, u.reading) for u in units] == [
            ("T", "Take"), ("m", "me")
        ]
        tokens = build_alignment_tokens(plan)
        assert [t.text for t in tokens] == ["take", "me"]

    def test_generated_ruby_carries_whole_word(self):
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentResult, EmissionSpan, build_alignment_request,
        )
        from strange_uta_game.backend.application.ai_timing.commands import (
            ApplyAiTimingCommand,
        )
        from strange_uta_game.backend.domain import Project
        project = Project()
        project.sentences = [self._line()]
        resolver = PronunciationResolver(analyzer=DummyAnalyzer(), chinese_mode=False)
        plan = resolver.resolve_project(project, fill_missing=True)
        request = build_alignment_request(plan)
        result = AlignmentResult(
            annotation_digest=request.annotation_digest, model_id="fake",
            spans=[EmissionSpan(t.index, i * 100, i * 100 + 50)
                   for i, t in enumerate(request.tokens)])
        ApplyAiTimingCommand(project, plan, request, result).execute()
        ch_t = project.sentences[0].characters[0]
        assert [p.text for p in ch_t.ruby.parts] == ["Take"]  # SUG 首字承载整词
        ch_m = project.sentences[0].characters[5]
        assert [p.text for p in ch_m.ruby.parts] == ["me"]

    def test_kana_linked_words_not_merged(self):
        """假名/汉字连词不受整词化影响（走形态素逐字注音）。"""
        from strange_uta_game.backend.domain import Character, Project, Sentence
        project = Project()
        project.sentences = [Sentence(singer_id="s1", characters=[
            Character(char="ま", check_count=1, ruby=None, linked_to_next=True, singer_id="s1"),
            Character(char="い", check_count=0, ruby=None, linked_to_next=False, singer_id="s1"),
        ])]
        resolver = PronunciationResolver(analyzer=DummyAnalyzer(), chinese_mode=False)
        plan = resolver.resolve_project(project, fill_missing=True)
        units = [u for u in plan.units if not u.is_sentence_end]
        # 假名自读逐字（"ま"），未被合并成整词"まい"
        assert len(units) == 1 and units[0].reading == "ま"
        assert units[0].source is not None

    def test_transcript_digest_invalidates_cache(self):
        """transcript 变化 → 缓存键变化（旧 span 不会错配新 token）。"""
        from strange_uta_game.backend.application.ai_timing.vocals import (
            alignment_cache_metadata,
        )
        a = alignment_cache_metadata(
            media_sha256="m", alignment_model="x", annotation_digest="d",
            options={"tail_snap": True, "transcript_digest": "aaa"})
        b = alignment_cache_metadata(
            media_sha256="m", alignment_model="x", annotation_digest="d",
            options={"tail_snap": True, "transcript_digest": "bbb"})
        from strange_uta_game.backend.application.ai_timing.vocals import cache_key
        assert cache_key(a) != cache_key(b)
