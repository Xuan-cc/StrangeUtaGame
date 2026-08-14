"""AI 打轴阶段 B：对齐请求/结果 schema、token 映射与原子写回测试。

验收门槛（计划文档 §11 阶段 B）：使用固定 emission 的测试可无模型地
完成稳定写回与撤销——全量覆盖时间戳、一次撤销完整恢复时间戳与注音、
校验失败/漂移不产生部分应用。
"""

import pytest

from strange_uta_game.backend.application import (
    AlignmentRequest,
    AlignmentResult,
    AlignmentValidationError,
    ApplyAiTimingCommand,
    ProjectDriftError,
    PronunciationResolver,
)
from strange_uta_game.backend.application.ai_timing import (
    EmissionSpan,
    build_alignment_request,
    build_alignment_tokens,
    checkpoint_timestamps,
    interpolate_structural_timestamps,
    validate_result,
)
from strange_uta_game.backend.application.ai_timing.pronunciation import (
    compute_annotation_digest,
)
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


def _sentence(chars_spec, singer_id="s1"):
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


def _resolved_project(sentences):
    """构建已填充读音的 plan 与工程（无模型路径）。"""
    project = Project()
    project.sentences = sentences
    resolver = PronunciationResolver(
        analyzer=_FixedReadingAnalyzer(), chinese_mode=False
    )
    plan = resolver.resolve_project(project, fill_missing=True)
    return project, plan


def _spans_from_tokens(request, start_ms_list):
    """按 token 顺序给出固定区间起点，构造固定 emission 结果。"""
    spans = []
    for token, start in zip(request.tokens, start_ms_list):
        spans.append(
            EmissionSpan(
                token_index=token.index,
                start_ms=start,
                end_ms=start + 200,
            )
        )
    return spans


class TestBuildAlignmentTokens:
    """读音 → Latn token 的构建与结构映射。"""

    def test_kana_readings_romanized(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        project, plan = _resolved_project([s])
        tokens = build_alignment_tokens(plan)
        assert [t.text for t in tokens] == ["a", "ka"]
        assert [t.location for t in tokens] == [(0, 0, 0), (0, 1, 0)]
        assert [t.raw_reading for t in tokens] == ["あ", "か"]

    def test_existing_part_readings_romanized(self):
        s = _sentence([("赤", 2, ["あ", "か"], False)])
        project, plan = _resolved_project([s])
        tokens = build_alignment_tokens(plan)
        assert [t.text for t in tokens] == ["a", "ka"]

    def test_pinyin_diacritics_stripped(self):
        s = _sentence([("你", 1, ["nǐ"], False), ("好", 1, ["hǎo"], False)])
        project, plan = _resolved_project([s])
        tokens = build_alignment_tokens(plan)
        assert [t.text for t in tokens] == ["ni", "hao"]

    def test_latin_and_number_readings_pass_through(self):
        s = _sentence([("L", 1, None, False), ("3", 1, ["さん"], False)])
        project, plan = _resolved_project([s])
        tokens = build_alignment_tokens(plan)
        assert [t.text for t in tokens] == ["l", "san"]

    def test_pause_punctuation_sentence_end_produce_no_tokens(self):
        s = _sentence(
            [
                ("ん", 2, ["ん", "^"], False),
                ("！", 1, None, False),
                ("い", 1, None, True),
            ]
        )
        project, plan = _resolved_project([s])
        tokens = build_alignment_tokens(plan)
        # 停顿拍与标点无 token；句尾字符的正常 checkpoint 仍是 token，
        # 仅其虚拟句尾点（cp=check_count）不产生 token
        assert [t.location for t in tokens] == [(0, 0, 0), (0, 2, 0)]

    def test_pending_units_block_token_build(self):
        s = _sentence([("赤", 3, ["あ"], False)])  # parts<check_count → 缺口
        project, plan = _resolved_project([s])
        with pytest.raises(AlignmentValidationError, match="缺少读音"):
            build_alignment_tokens(plan)

    def test_request_carries_digest_and_schema(self):
        s = _sentence([("あ", 1, None, False)])
        project, plan = _resolved_project([s])
        request = build_alignment_request(plan)
        assert request.annotation_digest == plan.annotation_digest
        assert request.schema_version == 1
        assert len(request.tokens) == 1


class TestValidateResult:
    """固定 emission 结果的完整校验。"""

    def _request(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        project, plan = _resolved_project([s])
        return build_alignment_request(plan)

    def test_valid_result_passes(self):
        request = self._request()
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 400]),
        )
        validate_result(result, request)

    def test_schema_mismatch_rejected(self):
        request = self._request()
        result = AlignmentResult(
            schema_version=99,
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 400]),
        )
        with pytest.raises(AlignmentValidationError, match="版本不匹配"):
            validate_result(result, request)

    def test_digest_mismatch_rejected(self):
        request = self._request()
        result = AlignmentResult(
            annotation_digest="different", spans=_spans_from_tokens(request, [100, 400])
        )
        with pytest.raises(AlignmentValidationError, match="摘要不一致"):
            validate_result(result, request)

    def test_incomplete_coverage_rejected(self):
        request = self._request()
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=[EmissionSpan(token_index=0, start_ms=100, end_ms=300)],
        )
        with pytest.raises(AlignmentValidationError, match="覆盖不完整"):
            validate_result(result, request)

    def test_negative_timestamp_rejected(self):
        request = self._request()
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [-1, 400]),
        )
        with pytest.raises(AlignmentValidationError, match="负时间戳"):
            validate_result(result, request)

    def test_non_monotonic_rejected(self):
        request = self._request()
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [500, 400]),
        )
        with pytest.raises(AlignmentValidationError, match="不单调"):
            validate_result(result, request)

    def test_duplicate_token_span_rejected(self):
        request = self._request()
        spans = _spans_from_tokens(request, [100, 400])
        spans[1].token_index = 0
        result = AlignmentResult(
            annotation_digest=request.annotation_digest, spans=spans
        )
        with pytest.raises(AlignmentValidationError, match="重复"):
            validate_result(result, request)


class TestTimestampsMapping:
    """token 区间 → checkpoint 时间戳（含结构单元插值）。"""

    def test_token_units_take_span_start(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        project, plan = _resolved_project([s])
        request = build_alignment_request(plan)
        span_map = checkpoint_timestamps(
            AlignmentResult(
                annotation_digest=request.annotation_digest,
                spans=_spans_from_tokens(request, [100, 400]),
            ),
            request,
        )
        assert span_map == {(0, 0, 0): (100, 300), (0, 1, 0): (400, 600)}

    def test_structural_unit_uses_prev_end(self):
        """停顿符/标点单元：延音 = 前一 token 终点。"""
        s = _sentence(
            [
                ("ん", 2, ["ん", "^"], False),
                ("！", 1, None, False),
                ("か", 1, None, False),
            ]
        )
        project, plan = _resolved_project([s])
        request = build_alignment_request(plan)
        # tokens: (0,0,0)=ん首拍, (0,2,0)=か
        spans = _spans_from_tokens(request, [100, 500])
        span_map = checkpoint_timestamps(
            AlignmentResult(annotation_digest=request.annotation_digest, spans=spans),
            request,
        )
        ts = interpolate_structural_timestamps(plan, request, span_map)
        assert ts[(0, 0, 0)] == 100  # token 起点
        assert ts[(0, 0, 1)] == 300  # 停顿拍 = ん 区间终点
        assert ts[(0, 1, 0)] == 300  # 标点 = 前一 token 终点
        assert ts[(0, 2, 0)] == 500  # token 起点

    def test_structural_clamped_to_next_start(self):
        """前 token 终点晚于后 token 起点时收敛（保证行内单调）。"""
        s = _sentence(
            [("あ", 1, None, False), ("！", 1, None, False), ("か", 1, None, False)]
        )
        project, plan = _resolved_project([s])
        request = build_alignment_request(plan)
        spans = [
            EmissionSpan(token_index=0, start_ms=100, end_ms=500),
            EmissionSpan(token_index=1, start_ms=400, end_ms=600),
        ]
        span_map = checkpoint_timestamps(
            AlignmentResult(annotation_digest=request.annotation_digest, spans=spans),
            request,
        )
        ts = interpolate_structural_timestamps(plan, request, span_map)
        assert ts[(0, 1, 0)] == 400  # min(500, 400)

    def test_line_without_tokens_skipped(self):
        """无 token 的纯结构行不产生时间戳（保留原轴）。"""
        s1 = _sentence([("あ", 1, None, False)])
        s2 = _sentence([("！", 1, None, False)])
        project, plan = _resolved_project([s1, s2])
        request = build_alignment_request(plan)
        spans = _spans_from_tokens(request, [100])
        span_map = checkpoint_timestamps(
            AlignmentResult(annotation_digest=request.annotation_digest, spans=spans),
            request,
        )
        ts = interpolate_structural_timestamps(plan, request, span_map)
        assert (0, 0, 0) in ts
        assert (1, 0, 0) not in ts


class TestApplyAiTimingCommand:
    """原子写回 + 一次撤销（固定 emission，无模型）。"""

    def _setup(self, sentences):
        project = Project()
        project.sentences = sentences
        resolver = PronunciationResolver(chinese_mode=False)
        plan = resolver.collect_existing_annotations(project)
        request = build_alignment_request(plan)
        return project, plan, request

    def test_apply_overwrites_all_timestamps(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        s.characters[0].add_timestamp(9999)
        s.characters[1].add_timestamp(9999)
        project, plan, request = self._setup([s])
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 400]),
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        cmd.execute()
        assert project.sentences[0].characters[0].timestamps == [100]
        assert project.sentences[0].characters[1].timestamps == [400]

    def test_undo_restores_timestamps_and_ruby(self):
        s = _sentence([("赤", 2, ["あ", "か"], False), ("い", 1, None, True)])
        s.characters[0].add_timestamp(1000)
        s.characters[0].add_timestamp(1200)
        s.characters[1].add_timestamp(1400)
        s.characters[1].set_sentence_end_ts(2000)
        project, plan, request = self._setup([s])
        # tokens: 赤あ/赤か/い
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 300, 500]),
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        cmd.execute()
        # 句尾释放点由对齐推导：い 的 token 区间 (500,700) → 释放=700
        assert project.sentences[0].characters[1].sentence_end_ts == 700
        cmd.undo()
        restored = project.sentences[0]
        assert restored.characters[0].timestamps == [1000, 1200]
        assert restored.characters[1].timestamps == [1400]
        assert restored.characters[1].sentence_end_ts == 2000
        assert [p.text for p in restored.characters[0].ruby.parts] == ["あ", "か"]
        assert restored.characters[0].ruby.timestamps == [1000, 1200]

    def test_redo_reapplies(self):
        s = _sentence([("あ", 1, None, False)])
        project, plan, request = self._setup([s])
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100]),
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        cmd.execute()
        cmd.undo()
        assert project.sentences[0].characters[0].timestamps == []
        cmd.redo()
        assert project.sentences[0].characters[0].timestamps == [100]
        cmd.undo()
        assert project.sentences[0].characters[0].timestamps == []

    def test_generated_readings_not_written_to_project(self):
        """缺口读音只用于 transcript，不写回工程 ruby（2026-08 用户决策）。"""
        s = _sentence([("見", 1, None, False), ("て", 1, None, False)])
        resolver = PronunciationResolver(
            analyzer=_FixedReadingAnalyzer({"見": "み"}), chinese_mode=False
        )
        project = Project()
        project.sentences = [s]
        plan = resolver.resolve_project(project, fill_missing=True)
        request = build_alignment_request(plan)
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 300]),
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        assert s.characters[0].ruby is None
        cmd.execute()
        # 时间戳已应用，但 ruby 保持为空（原注音原样——这里本就没有注音）
        assert s.characters[0].timestamps == [100]
        assert s.characters[0].ruby is None
        assert s.characters[1].ruby is None
        cmd.undo()
        restored = project.sentences[0]
        assert restored.characters[0].timestamps == []
        assert restored.characters[0].ruby is None

    def test_existing_ruby_never_rewritten(self):
        s = _sentence([("赤", 1, ["あか"], False)])
        project, plan, request = self._setup([s])
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100]),
        )
        ApplyAiTimingCommand(project, plan, request, result).execute()
        assert [p.text for p in s.characters[0].ruby.parts] == ["あか"]

    def test_invalid_result_blocks_without_partial_apply(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        project, plan, request = self._setup([s])
        bad = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=[EmissionSpan(token_index=0, start_ms=100, end_ms=300)],
        )
        cmd = ApplyAiTimingCommand(project, plan, request, bad)
        with pytest.raises(AlignmentValidationError):
            cmd.execute()
        assert s.characters[0].timestamps == []  # 无部分应用
        assert s.characters[1].timestamps == []

    def test_project_drift_blocks_apply(self):
        s = _sentence([("あ", 1, None, False), ("か", 1, None, False)])
        project, plan, request = self._setup([s])
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 400]),
        )
        # 工程在执行快照之后被修改（标注变化，非时间戳）
        s.characters[1].set_ruby(Ruby(parts=[RubyPart(text="か")]))
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        with pytest.raises(ProjectDriftError):
            cmd.execute()
        assert s.characters[0].timestamps == []

    def test_multi_line_apply_and_global_order(self):
        s1 = _sentence([("あ", 1, None, False)])
        s2 = _sentence([("か", 1, None, False)])
        project, plan, request = self._setup([s1, s2])
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            spans=_spans_from_tokens(request, [100, 400]),
        )
        ApplyAiTimingCommand(project, plan, request, result).execute()
        assert s1.characters[0].timestamps == [100]
        assert s2.characters[0].timestamps == [400]


class TestUnalignableChars:
    """emoji / 空格 / 特殊字符：不参与对齐、不阻断执行、原文原样保留。"""

    def test_structural_chars_skip_alignment_and_survive_apply(self):
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentResult,
            EmissionSpan,
            build_alignment_request,
            checkpoint_timestamps,
            interpolate_structural_timestamps,
        )
        from strange_uta_game.backend.application.ai_timing.commands import (
            ApplyAiTimingCommand,
        )
        from strange_uta_game.backend.application.ai_timing.resolver import (
            PronunciationResolver,
        )
        from strange_uta_game.backend.domain import (
            Character,
            Project,
            Sentence,
        )
        from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
            DummyAnalyzer,
        )

        raw = "あ🎵 か☆"
        project = Project()
        project.sentences = [
            Sentence(
                singer_id="s1",
                characters=[
                    Character(char=c, check_count=1, ruby=None, singer_id="s1")
                    for c in raw
                ],
            )
        ]
        resolver = PronunciationResolver(analyzer=DummyAnalyzer(), chinese_mode=False)
        plan = resolver.resolve_project(project, fill_missing=True)
        # emoji/空格/☆ 均为结构单元：不产生缺口、不生成 token
        assert plan.is_complete
        request = build_alignment_request(plan)
        token_texts = [t.raw_reading for t in request.tokens]
        assert token_texts == ["あ", "か"]  # 仅假名成为 token
        # 伪造两个 token 区间并走完整应用
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id="fake",
            spans=[
                EmissionSpan(0, 1000, 1500),
                EmissionSpan(1, 2000, 2500),
            ],
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        cmd.execute()
        chars = project.sentences[0].characters
        # 全部 checkpoint 均获得时间戳（结构单元为插值），原文一字不改
        assert all(len(c.timestamps) == 1 for c in chars)
        assert "".join(c.char for c in chars) == raw
        ts = [c.timestamps[0] for c in chars]
        assert ts[0] == 1000 and ts[3] == 2000  # あ / か 各自 token 起点
        # 中间结构单元（🎵 空格）= 前一 token 终点收敛至下一 token 起点
        assert ts[1] == ts[2] == 1500
        # 末尾结构单元（☆）= 前一 token 终点（延音）
        assert ts[4] == 2500

    def test_applied_result_respects_project_structure(self):
        """应用后的结构不变量：timestamps 长度==check_count、ruby 同步、
        句尾释放点清空、既有标注结构不变。"""
        from strange_uta_game.backend.application.ai_timing.alignment import (
            AlignmentResult,
            EmissionSpan,
            build_alignment_request,
        )
        from strange_uta_game.backend.application.ai_timing.commands import (
            ApplyAiTimingCommand,
        )
        from strange_uta_game.backend.application.ai_timing.resolver import (
            PronunciationResolver,
        )
        from strange_uta_game.backend.domain import (
            Character,
            Project,
            Ruby,
            RubyPart,
            Sentence,
        )

        project = Project()
        project.sentences = [
            Sentence(
                singer_id="s1",
                characters=[
                    Character(
                        char="赤",
                        check_count=2,
                        ruby=Ruby(parts=[RubyPart(text="あ"), RubyPart(text="か")]),
                        is_sentence_end=True,
                        singer_id="s1",
                    ),
                    Character(char="い", check_count=1, ruby=None, singer_id="s1"),
                ],
            )
        ]
        resolver = PronunciationResolver()
        plan = resolver.collect_existing_annotations(project)
        request = build_alignment_request(plan)
        n = len(request.tokens)
        result = AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id="fake",
            spans=[
                EmissionSpan(i, 100 * i, 100 * i + 50) for i in range(n)
            ],
        )
        cmd = ApplyAiTimingCommand(project, plan, request, result)
        cmd.execute()
        ch0, ch1 = project.sentences[0].characters
        assert len(ch0.timestamps) == ch0.check_count == 2
        assert len(ch1.timestamps) == ch1.check_count == 1
        # 赤 是句尾字符：其末 token か 的区间终点（100,150）→ 释放点 150
        assert ch0.sentence_end_ts == 150
        assert ch0.ruby is not None and ch0.ruby.timestamps == ch0.all_timestamps
        assert [p.text for p in ch0.ruby.parts] == ["あ", "か"]  # 既有标注原样
        assert ch0.is_sentence_end is True  # 结构标志不变
        cmd.undo()
        ch0, ch1 = project.sentences[0].characters
        assert ch0.timestamps == [] and ch0.ruby is not None
