"""LLM 注音（llm_ruby）单元测试。

不发起任何真实网络请求：通过替换 ``LLMRubyClient`` 或注入假 client 验证
解析、按行回退、缓存命中/未命中等行为。
"""

import time

import pytest

from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
    LLMRubyAnalyzer,
    LLMRubyConfig,
    _annotated_to_pairs,
    _coerce_json,
    _extract_content,
    _parse_payload,
)
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    RubyAnalyzer,
    RubyResult,
)


class _SelfAnalyzer(RubyAnalyzer):
    """回退用假分析器：每个字符自注音（reading == char）。"""

    def analyze(self, text):
        return [
            RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
            for i, c in enumerate(text)
        ]

    def get_reading(self, text):
        return text


# ──────────────────────────────────────────────
# _parse_payload
# ──────────────────────────────────────────────


def test_parse_payload_normal():
    text = (
        '{"lines":[{"i":0,"tokens":'
        '[{"s":"今日","r":"きょう"},{"s":"は","r":"は"}]}]}'
    )
    mapping = _parse_payload(text, ["今日は"])
    assert mapping == {0: [("今日", "きょう"), ("は", "は")]}


def test_parse_payload_line_mismatch_dropped():
    # surface 连接 ≠ 原行 → 该行被丢弃（交由调用方回退）
    text = '{"lines":[{"i":0,"tokens":[{"s":"今","r":"いま"}]}]}'
    assert _parse_payload(text, ["今日は"]) == {}


def test_parse_payload_out_of_range_index_skipped():
    text = '{"lines":[{"i":5,"tokens":[{"s":"あ","r":"あ"}]}]}'
    assert _parse_payload(text, ["あ"]) == {}


def test_parse_payload_invalid_json_raises():
    with pytest.raises(Exception):
        _parse_payload("not json at all", ["あ"])


# ── r 数组形态 ──


def test_parse_payload_array_reading_accepted():
    """r 为字符串数组 + 长度等于 s → 原样接受。"""
    text = (
        '{"lines":[{"i":0,"tokens":'
        '[{"s":"毎日","r":["まい","にち"]}]}]}'
    )
    assert _parse_payload(text, ["毎日"]) == {0: [("毎日", ["まい", "にち"])]}


def test_parse_payload_array_with_empty_strings_accepted():
    """r 数组含空串（表示连词）也接受，下游 _build_results 会拼成字符串走整块路径。"""
    text = (
        '{"lines":[{"i":0,"tokens":'
        '[{"s":"今日","r":["きょう",""]}]}]}'
    )
    assert _parse_payload(text, ["今日"]) == {0: [("今日", ["きょう", ""])]}


def test_parse_payload_array_length_mismatch_degrades_to_string():
    """r 数组长度 ≠ s 字符数 → 退化为字符串拼接（避免错位映射）。"""
    text = (
        '{"lines":[{"i":0,"tokens":'
        '[{"s":"毎日","r":["まいにち"]}]}]}'
    )
    assert _parse_payload(text, ["毎日"]) == {0: [("毎日", "まいにち")]}


# ── annotated 形式（PR4 主路径） ──


def test_annotated_to_pairs_mixed_block_and_bare():
    """`{今日||きょう,}は{毎日||まい,にち}` → 块用 Pairs，块外裸字符逐字。"""
    pairs, raw = _annotated_to_pairs("{今日||きょう,}は{毎日||まい,にち}")
    assert raw == "今日は毎日"
    assert pairs == [
        ("今日", "きょう"),       # 尾随空读音 → 拼成字符串走整块分配
        ("は", "は"),
        ("毎日", ["まい", "にち"]),  # 全非空 → 数组形
    ]


def test_annotated_to_pairs_mora_within_char_concatenates():
    """字内 mora `|` 仅作为分隔提示，下游按字读音处理 → 拼成单串。"""
    pairs, raw = _annotated_to_pairs("{大冒険||だ|い,ぼ|う,け|ん}")
    assert raw == "大冒険"
    assert pairs == [("大冒険", ["だい", "ぼう", "けん"])]


def test_annotated_to_pairs_short_form():
    """短形：`{赤|あか}` / `{愛|あ|い}`。"""
    pairs, _ = _annotated_to_pairs("{赤|あか}")
    assert pairs == [("赤", "あか")]
    pairs, _ = _annotated_to_pairs("{愛|あ|い}")
    assert pairs == [("愛", "あい")]


def test_annotated_to_pairs_empty_block_no_ruby():
    """`{text}` 无 ruby → 每字自注音。"""
    pairs, raw = _annotated_to_pairs("{abc}")
    assert raw == "abc"
    assert pairs == [("a", "a"), ("b", "b"), ("c", "c")]


def test_annotated_to_pairs_unclosed_brace_falls_through():
    """未闭合 `{` → 当普通字符，不抛异常。"""
    pairs, raw = _annotated_to_pairs("ab{cd")
    assert raw == "ab{cd"


def test_parse_payload_annotated_form():
    """LLM 主路径：`text` 字段含 annotated 文本。"""
    payload = (
        '{"lines":[{"i":0,"text":"{今日||きょう,}は{毎日||まい,にち}"}]}'
    )
    assert _parse_payload(payload, ["今日は毎日"]) == {
        0: [
            ("今日", "きょう"),
            ("は", "は"),
            ("毎日", ["まい", "にち"]),
        ]
    }


def test_parse_payload_annotated_mismatch_dropped():
    """剥离 `{...}` 后 ≠ 原行 → 整行丢弃，由调用方按行回退。"""
    payload = '{"lines":[{"i":0,"text":"{今日||きょう,}"}]}'  # raw=今日 != 今日は
    assert _parse_payload(payload, ["今日は"]) == {}


def test_parse_payload_unwrapped_kanji_dropped():
    """漏包的漢字（块外裸 漢字 → 自注音）虽字符对齐，但应判未达标而丢弃。"""
    # 「今日」正确注音，但「天気」漏包 → 块外裸字 → 天/気 自注音（无注音）
    payload = '{"lines":[{"i":0,"text":"{今日||きょう,}は天気"}]}'
    assert _parse_payload(payload, ["今日は天気"]) == {}


def test_parse_payload_empty_reading_kanji_block_dropped():
    """空读音块 `{漢字}` / `{漢字||}` 静默失音 → 应判未达标而丢弃。"""
    assert _parse_payload('{"lines":[{"i":0,"text":"{空}"}]}', ["空"]) == {}
    assert _parse_payload('{"lines":[{"i":0,"text":"{空||}"}]}', ["空"]) == {}


def test_parse_payload_unread_kanji_in_tokens_dropped():
    """tokens 路径同样把「漢字读音==自身」判为未达标。"""
    payload = '{"lines":[{"i":0,"tokens":[{"s":"空","r":"空"}]}]}'
    assert _parse_payload(payload, ["空"]) == {}


def test_parse_payload_kana_self_reading_still_accepted():
    """纯假名/记号读音==自身是正常的（は→は），不应被覆盖校验误伤。"""
    payload = '{"lines":[{"i":0,"text":"{今日||きょう,}は！"}]}'
    assert _parse_payload(payload, ["今日は！"]) == {
        0: [("今日", "きょう"), ("は", "は"), ("！", "！")]
    }


def test_parse_payload_prefers_text_over_tokens():
    """同时给 text 和 tokens → 优先 text（推荐路径）。"""
    payload = (
        '{"lines":[{"i":0,'
        '"text":"{毎日||まい,にち}",'
        '"tokens":[{"s":"毎日","r":"まいひ"}]}]}'  # 错误读音的旧字段
    )
    result = _parse_payload(payload, ["毎日"])
    assert result == {0: [("毎日", ["まい", "にち"])]}


def test_parse_payload_falls_back_to_tokens_when_no_text():
    """缺 text 时回退旧 tokens 格式（兼容历史 LLM 客户端）。"""
    payload = (
        '{"lines":[{"i":0,"tokens":[{"s":"今日","r":"きょう"},{"s":"は","r":"は"}]}]}'
    )
    assert _parse_payload(payload, ["今日は"]) == {
        0: [("今日", "きょう"), ("は", "は")]
    }


def test_parse_payload_array_non_string_element_degrades():
    """r 数组含非字符串元素 → 退化（提取所有字符串拼接，否则用 s 自身）。"""
    text = (
        '{"lines":[{"i":0,"tokens":'
        '[{"s":"毎日","r":["まい",null]}]}]}'
    )
    # null 被过滤；保留的字符串拼接 = "まい"
    assert _parse_payload(text, ["毎日"]) == {0: [("毎日", "まい")]}


def test_coerce_json_strips_code_fence():
    assert _coerce_json('```json\n{"lines":[]}\n```') == {"lines": []}


def test_extract_content_all_formats():
    openai = {"choices": [{"message": {"content": "X"}}]}
    assert _extract_content(openai, "openai") == "X"
    anthropic = {"content": [{"type": "text", "text": "Y"}]}
    assert _extract_content(anthropic, "anthropic") == "Y"
    # Responses API：output[].content[].text，跳过 reasoning 项
    responses = {
        "output": [
            {"type": "reasoning", "content": []},
            {"type": "message", "content": [
                {"type": "output_text", "text": "Z1"},
                {"type": "output_text", "text": "Z2"},
            ]},
        ]
    }
    assert _extract_content(responses, "responses") == "Z1Z2"
    # 便捷字段优先
    assert _extract_content({"output_text": "W"}, "responses") == "W"
    assert _extract_content({"_stream_text": "streamed"}, "openai") == "streamed"


def test_extract_stream_delta_ignores_reasoning_content():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        _extract_stream_delta,
    )

    assert _extract_stream_delta(
        {"choices": [{"delta": {"reasoning_content": "private thought"}}]},
        "openai",
    ) == ("", True)
    assert _extract_stream_delta(
        {"choices": [{"delta": {"content": "{"}}]}, "openai"
    ) == ("{", False)
    assert _extract_stream_delta(
        {"type": "response.reasoning_summary_text.delta", "delta": "thought"},
        "responses",
    ) == ("", True)
    assert _extract_stream_delta(
        {"type": "response.output_text.delta", "delta": "{}"}, "responses"
    ) == ("{}", False)
    assert _extract_stream_delta(
        {"delta": {"type": "thinking_delta", "thinking": "thought"}},
        "anthropic",
    ) == ("", True)
    assert _extract_stream_delta(
        {"delta": {"type": "text_delta", "text": "{}"}}, "anthropic"
    ) == ("{}", False)


def test_consume_event_stream_reports_reasoning_without_collecting_it():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    client = LLMRubyClient(
        LLMRubyConfig(base_url="x", api_key="k", model="m", provider="openai")
    )
    progress = []
    client.set_progress_callback(progress.append)

    class _Resp:
        @staticmethod
        def iter_lines(decode_unicode=True):
            yield 'data: {"choices":[{"delta":{"reasoning_content":"secret"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"{\\"lines\\":"}}]}'
            yield 'data: {"choices":[{"delta":{"content":"[]}"}}]}'
            yield "data: [DONE]"

    assert client._consume_event_stream(_Resp(), time.time()) == '{"lines":[]}'
    assert progress[0] == "模型正在推理…"
    assert any("tokens/s" in message for message in progress)
    assert progress[-1].startswith("LLM 返回完成：")


def test_estimate_token_count_is_lightweight_and_cjk_aware():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        _estimate_token_count,
    )

    assert _estimate_token_count("日本語") == 3.0
    assert _estimate_token_count("abcd") == 1.0
    assert _estimate_token_count("日 abcd") == 2.0


# ──────────────────────────────────────────────
# LLMRubyAnalyzer
# ──────────────────────────────────────────────


def _make_analyzer(monkeypatch, annotate_return):
    """构造 LLMRubyAnalyzer 并打桩其 client.annotate_lines。"""
    cfg = LLMRubyConfig(
        enabled=True, base_url="http://x", api_key="k", model="m"
    )
    analyzer = LLMRubyAnalyzer(cfg, lines=["今日は", "空"], fallback=_SelfAnalyzer())
    monkeypatch.setattr(
        analyzer._client, "annotate_lines", lambda lines: annotate_return
    )
    return analyzer


def test_analyzer_cache_hit_uses_llm(monkeypatch):
    mapping = {0: [("今日", "きょう"), ("は", "は")]}
    analyzer = _make_analyzer(monkeypatch, (mapping, None))
    results = analyzer.analyze("今日は")
    # 今日 → きょう 分配（こ ょ う 等），は 自注音；至少包含「今」起始块
    assert "".join(r.reading for r in results) == "きょうは"
    assert analyzer.llm_failed is False


def test_analyzer_array_reading_emits_per_char_results(monkeypatch):
    """LLM 数组形态 r=["まい","にち"] → 直接逐字 emit，绕过本地分配启发式。

    回归 LLM 注音 毎日→まいひ 的场景：现在 LLM 可直接说「毎=まい、日=にち」，
    下游不再用 kanji_dict 拼接，保留 LLM 的高置信切分。
    """
    mapping = {0: [("毎日", ["まい", "にち"])]}
    analyzer = _make_analyzer(monkeypatch, (mapping, None))
    # _make_analyzer 默认 lines=["今日は", "空"]，需要直接调 analyze("毎日") 命中需另建。
    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(cfg, lines=["毎日"], fallback=_SelfAnalyzer())
    monkeypatch.setattr(analyzer._client, "annotate_lines", lambda lines: (mapping, None))

    results = analyzer.analyze("毎日")
    # 期望 2 条 single-char RubyResult，分别承载 まい / にち；
    # 且都标 morpheme_span=(0,2)，让下游 Phase 5 享有连词组保护。
    assert len(results) == 2
    assert results[0].text == "毎" and results[0].reading == "まい"
    assert results[0].start_idx == 0 and results[0].end_idx == 1
    assert results[0].morpheme_span == (0, 2)
    assert results[1].text == "日" and results[1].reading == "にち"
    assert results[1].start_idx == 1 and results[1].end_idx == 2
    assert results[1].morpheme_span == (0, 2)


def test_analyzer_array_with_empty_falls_back_to_compound(monkeypatch):
    """数组含空串（如 ["きょう",""]）→ 退化为整块 reading，由本地分配处理。"""
    mapping = {0: [("今日", ["きょう", ""])]}
    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(cfg, lines=["今日"], fallback=_SelfAnalyzer())
    monkeypatch.setattr(analyzer._client, "annotate_lines", lambda lines: (mapping, None))

    results = analyzer.analyze("今日")
    # 拼接读音 "きょう"，由 _results_from_pairs/_distribute_morpheme_reading 处理。
    # 期望整块单 RubyResult，下游可继续 fallback peel。
    assert sum(r.end_idx - r.start_idx for r in results) == 2
    joined = "".join(r.reading for r in results)
    assert joined == "きょう"


def test_get_reading_supports_array_form(monkeypatch):
    """get_reading 需正确拼接数组形态。"""
    mapping = {0: [("毎日", ["まい", "にち"])]}
    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(cfg, lines=["毎日"], fallback=_SelfAnalyzer())
    monkeypatch.setattr(analyzer._client, "annotate_lines", lambda lines: (mapping, None))
    assert analyzer.get_reading("毎日") == "まいにち"


def test_analyzer_cache_miss_falls_back(monkeypatch):
    mapping = {0: [("今日", "きょう"), ("は", "は")]}
    analyzer = _make_analyzer(monkeypatch, (mapping, None))
    # 「空」未在 LLM 返回中（mapping 仅含行 0）→ 回退本地引擎（自注音）
    results = analyzer.analyze("空")
    assert [r.reading for r in results] == ["空"]


def test_analyzer_request_error_marks_failed_and_falls_back(monkeypatch):
    analyzer = _make_analyzer(monkeypatch, ({}, "网络请求失败：timeout"))
    results = analyzer.analyze("今日は")
    assert analyzer.llm_failed is True
    assert analyzer.last_error == "网络请求失败：timeout"
    # 全部回退自注音
    assert "".join(r.reading for r in results) == "今日は"


def test_analyzer_prewarm_once(monkeypatch):
    calls = {"n": 0}

    def _annotate(lines):
        calls["n"] += 1
        return ({0: [("今日", "きょう"), ("は", "は")]}, None)

    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(cfg, lines=["今日は"], fallback=_SelfAnalyzer())
    monkeypatch.setattr(analyzer._client, "annotate_lines", _annotate)
    analyzer.analyze("今日は")
    analyzer.analyze("今日は")
    analyzer.get_reading("今日は")
    assert calls["n"] == 1  # 仅首次触发批量请求


def test_test_connection_surfaces_real_error(monkeypatch):
    """连通性测试应暴露 annotate_lines 的真实错误，而非通用 JSON 提示。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(base_url="http://x", api_key="k", model="m")
    client = LLMRubyClient(cfg)
    monkeypatch.setattr(
        client, "annotate_lines",
        lambda lines: ({}, "网络请求失败：Max retries exceeded"),
    )
    ok, msg = client.test_connection()
    assert ok is False
    assert msg == "网络请求失败：Max retries exceeded"


def test_endpoint_url_join():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    f = LLMRubyClient._endpoint_url
    # OpenAI Chat Completions
    assert f("https://api.openai.com/v1", "openai") == "https://api.openai.com/v1/chat/completions"
    assert f("https://api.openai.com/v1/", "openai") == "https://api.openai.com/v1/chat/completions"
    assert f("https://x/v1/chat/completions", "openai") == "https://x/v1/chat/completions"
    # 字面 URL（# 结尾）
    assert f("https://x/custom/path#", "openai") == "https://x/custom/path"
    # Anthropic
    assert f("https://api.anthropic.com", "anthropic") == "https://api.anthropic.com/v1/messages"
    assert f("https://api.anthropic.com/v1", "anthropic") == "https://api.anthropic.com/v1/messages"
    assert f("https://x/v1/messages", "anthropic") == "https://x/v1/messages"
    # OpenAI Responses
    assert f("https://opencode.ai/zen/v1", "responses") == "https://opencode.ai/zen/v1/responses"
    assert f("https://api.openai.com", "responses") == "https://api.openai.com/v1/responses"
    assert f("https://x/v1/responses", "responses") == "https://x/v1/responses"


def test_post_param_fallback_strips_on_400(monkeypatch):
    """response_format 触发 400 时应剥离重试并成功。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        LLMRubyClient,
        LLMRubyError,
    )

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)
    seen_bodies = []

    def _fake_post(url, headers, body):
        seen_bodies.append(body)
        if "response_format" in body:
            raise LLMRubyError("HTTP 400：response_format not supported")
        return {"ok": True}

    monkeypatch.setattr(client, "_post_json", _fake_post)
    result = client._post_with_param_fallback(
        "http://x/v1/chat/completions", {},
        {"model": "m", "temperature": 0, "response_format": {"type": "json_object"},
         "messages": []},
        ["response_format", "temperature"],
    )
    assert result == {"ok": True}
    # 第一次带 response_format（400），第二次剥离后成功
    assert "response_format" in seen_bodies[0]
    assert "response_format" not in seen_bodies[1]


def test_post_param_fallback_non_400_raises(monkeypatch):
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        LLMRubyClient,
        LLMRubyError,
    )

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)

    def _fake_post(url, headers, body):
        raise LLMRubyError("HTTP 401：unauthorized")

    monkeypatch.setattr(client, "_post_json", _fake_post)
    with pytest.raises(LLMRubyError, match="401"):
        client._post_with_param_fallback(
            "http://x", {}, {"model": "m", "messages": []}, ["response_format"]
        )


def test_annotate_lines_responses_format_end_to_end(monkeypatch):
    """provider=responses 时，_request 用 Responses 结构发送并解析 output[]。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(
        provider="responses", base_url="https://opencode.ai/zen/v1",
        api_key="k", model="m",
    )
    client = LLMRubyClient(cfg)
    captured = {}

    payload = '{"lines":[{"i":0,"tokens":[{"s":"空","r":"そら"}]}]}'

    def _fake_post(url, headers, body):
        captured["url"] = url
        captured["body"] = body
        return {"output": [{"type": "message", "content": [
            {"type": "output_text", "text": payload}]}]}

    monkeypatch.setattr(client, "_post_json", _fake_post)
    mapping, err = client.annotate_lines(["空"])
    assert err is None
    assert mapping == {0: [("空", "そら")]}
    assert captured["url"] == "https://opencode.ai/zen/v1/responses"
    assert "input" in captured["body"] and "instructions" in captured["body"]


def test_annotate_lines_retries_failed_lines(monkeypatch):
    """首轮漏注音的行（漢字未注音）应在第二轮被单独重发并补齐。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)
    rounds = {"n": 0}

    def _fake_request(lines):
        rounds["n"] += 1
        if rounds["n"] == 1:
            # 整首：行0 OK，行1「空」漏注音（块外裸字 → 自注音）
            return (
                '{"lines":['
                '{"i":0,"text":"{今日||きょう,}"},'
                '{"i":1,"text":"空"}]}'
            )
        # 第二轮只收到 pending=["空"]，这次正确注音
        assert lines == ["空"]
        return '{"lines":[{"i":0,"text":"{空||そら}"}]}'

    monkeypatch.setattr(client, "_request", _fake_request)
    mapping, err = client.annotate_lines(["今日", "空"])
    assert err is None
    assert rounds["n"] == 2  # 触发了第二轮重试
    assert mapping[0] == [("今日", "きょう")]
    assert mapping[1] == [("空", "そら")]


def test_annotate_lines_stops_when_retry_no_progress(monkeypatch):
    """重试轮零新增 → 停止重试，缺失行留给调用方回退（不死循环）。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)
    rounds = {"n": 0}

    def _fake_request(lines):
        rounds["n"] += 1
        # 每轮都把「空」漏注音 → 永远不达标
        return '{"lines":[{"i":0,"text":"空"}]}'

    monkeypatch.setattr(client, "_request", _fake_request)
    mapping, err = client.annotate_lines(["空"])
    assert err is None
    assert mapping == {}  # 始终缺失 → 调用方回退
    assert rounds["n"] == 2  # 首轮 + 1 次重试后因零新增停止（_MAX_ANNOTATE_ROUNDS=2）


def test_annotate_lines_first_round_error_returns_error(monkeypatch):
    """首轮整体失败（网络等）→ 直接返回 error，不进重试。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        LLMRubyClient,
        LLMRubyError,
    )

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)

    def _fake_request(lines):
        raise LLMRubyError("网络请求失败：timeout")

    monkeypatch.setattr(client, "_request", _fake_request)
    mapping, err = client.annotate_lines(["空"])
    assert mapping == {}
    assert err == "网络请求失败：timeout"


def test_annotate_lines_reports_progress(monkeypatch):
    """进度回调应收到「请求 / 已返回，正在解析」等阶段文本。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(base_url="http://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)
    msgs = []
    client.set_progress_callback(msgs.append)
    monkeypatch.setattr(
        client, "_request",
        lambda lines: '{"lines":[{"i":0,"text":"{空||そら}"}]}',
    )
    client.annotate_lines(["空"])
    assert any("请求" in m for m in msgs)
    assert any("解析" in m for m in msgs)


def test_post_json_retries_transient_then_succeeds(monkeypatch):
    """网络层异常（443 类）应退避重试，重试成功后返回。"""
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient
    import requests

    monkeypatch.setattr(m.time, "sleep", lambda *_: None)  # 跳过退避
    cfg = LLMRubyConfig(base_url="https://x/v1", api_key="k", model="m")
    client = LLMRubyClient(cfg)

    calls = {"n": 0}

    class _Resp:
        status_code = 200
        text = '{"ok":1}'

        def json(self):
            return {"ok": 1}

    def _post(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.exceptions.ConnectionError(
                "HTTPSConnectionPool(host='x', port=443): Max retries exceeded"
            )
        return _Resp()

    monkeypatch.setattr(requests, "post", _post)
    assert client._post_json("https://x/v1/chat/completions", {}, {"model": "m"}) == {"ok": 1}
    assert calls["n"] == 2  # 第一次失败、第二次成功


def test_redact_hides_api_key():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    client = LLMRubyClient(LLMRubyConfig(api_key="sk-secret", base_url="x", model="m"))
    assert "sk-secret" not in client._redact({"k": "sk-secret"})
    assert "***" in client._redact("token=sk-secret end")


def test_log_write_and_clear(tmp_path, monkeypatch):
    """index.log 写入小字段、clear 后整目录消失。"""
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m

    monkeypatch.setattr(m, "_llm_log_dir", lambda: tmp_path / "llm_ruby")
    m.llm_log_event("request", url="http://x", attempt=1, seq=1)
    log_file = tmp_path / "llm_ruby" / "index.log"
    assert log_file.exists() and "request" in log_file.read_text(encoding="utf-8")
    m.clear_llm_logs()
    assert not (tmp_path / "llm_ruby").exists()


def test_dump_call_writes_individual_files(tmp_path, monkeypatch):
    """_dump_call 把请求/响应分别写入独立文件，无 JSON 套 JSON 转义。"""
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m

    monkeypatch.setattr(m, "_llm_log_dir", lambda: tmp_path / "llm_ruby")
    # dict → pretty JSON .json 文件
    m._dump_call(1, "request", {"model": "x", "messages": [{"role": "user", "content": "今日は"}]})
    req = tmp_path / "llm_ruby" / "0001-request.json"
    assert req.exists()
    text = req.read_text(encoding="utf-8")
    # 日语原文应可读、没有 \\u 转义
    assert "今日は" in text
    assert "\\u" not in text
    # pretty 应有换行缩进
    assert "\n" in text and "  " in text

    # 字符串响应若可识别为 JSON 也美化
    m._dump_call(1, "response", '{"choices":[{"message":{"content":"{今日||きょう,}"}}]}')
    resp = tmp_path / "llm_ruby" / "0001-response.json"
    assert resp.exists()
    # 美化后 content 内 annotated 文本可读
    assert "{今日||きょう,}" in resp.read_text(encoding="utf-8")

    # 非 JSON 字符串以 .txt 落盘
    m._dump_call(2, "extracted", "{今日||きょう,}は{毎日||まい,にち}")
    ext = tmp_path / "llm_ruby" / "0002-extracted.txt"
    assert ext.exists()
    assert ext.read_text(encoding="utf-8") == "{今日||きょう,}は{毎日||まい,にち}"


def test_dump_call_redactor_strips_api_key(tmp_path, monkeypatch):
    """redactor 回调把 api_key 抹掉再落盘。"""
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m

    monkeypatch.setattr(m, "_llm_log_dir", lambda: tmp_path / "llm_ruby")
    redactor = lambda s: s.replace("sk-secret", "***")
    m._dump_call(3, "request", {"api_key": "sk-secret", "model": "x"}, redactor=redactor)
    text = (tmp_path / "llm_ruby" / "0003-request.json").read_text(encoding="utf-8")
    assert "sk-secret" not in text
    assert "***" in text


def test_call_seq_monotonic_and_reset_on_clear(tmp_path, monkeypatch):
    """_next_call_seq 单调自增、clear_llm_logs 后归零。"""
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m

    monkeypatch.setattr(m, "_llm_log_dir", lambda: tmp_path / "llm_ruby")
    m.clear_llm_logs()
    a, b, c = m._next_call_seq(), m._next_call_seq(), m._next_call_seq()
    assert a < b < c
    m.clear_llm_logs()
    d = m._next_call_seq()
    assert d == 1  # 归零后第一次从 1 开始


def test_katakana_english_helpers():
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
        is_all_katakana,
        is_english_reading,
    )
    assert is_all_katakana("ギター")
    assert is_all_katakana("コンピューター")
    assert not is_all_katakana("ギターは")  # 含平假名
    assert not is_all_katakana("")
    assert is_english_reading("guitar")
    assert is_english_reading("ice cream")
    assert not is_english_reading("ぎたー")
    assert not is_english_reading("")


def test_analyzer_emits_katakana_english_block_when_enabled(monkeypatch):
    """开关开启：片假名外来语 + 英文读音 → 单块保留英文读音。"""
    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(
        cfg, lines=["ギター"], fallback=_SelfAnalyzer(),
        annotate_katakana_with_english=True,
    )
    monkeypatch.setattr(
        analyzer._client, "annotate_lines",
        lambda lines: ({0: [("ギター", "guitar")]}, None),
    )
    results = analyzer.analyze("ギター")
    assert len(results) == 1
    assert results[0].text == "ギター"
    assert results[0].reading == "guitar"
    assert (results[0].start_idx, results[0].end_idx) == (0, 3)


def test_analyzer_ignores_english_when_disabled(monkeypatch):
    """开关关闭：即便返回英文读音，片假名也按假名逐字处理（英文被丢弃）。"""
    cfg = LLMRubyConfig(enabled=True, base_url="http://x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(
        cfg, lines=["ギター"], fallback=_SelfAnalyzer(),
        annotate_katakana_with_english=False,
    )
    monkeypatch.setattr(
        analyzer._client, "annotate_lines",
        lambda lines: ({0: [("ギター", "guitar")]}, None),
    )
    results = analyzer.analyze("ギター")
    # 逐字片假名→平假名，reading 中无英文
    assert "".join(r.reading for r in results) == "ぎたー"


def test_prompt_includes_english_rule_only_when_enabled():
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import LLMRubyClient

    cfg = LLMRubyConfig(base_url="x", api_key="k", model="m")
    on = LLMRubyClient(cfg, annotate_english=True)._build_user_prompt(["ギター"])
    off = LLMRubyClient(cfg, annotate_english=False)._build_user_prompt(["ギター"])
    assert "guitar" in on  # 规则示例
    assert "guitar" not in off


def test_prompt_contains_split_vs_jukujikun_rule():
    """系统提示词应包含「拆字 vs 不拆」判定规则及关键正反例（辞典 ‐/＝ 锚点）。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import _SYSTEM_PROMPT

    # 锚点：明确引用 小学館デジタル大辞泉 + ‐ / ＝ 分隔
    assert "小学館" in _SYSTEM_PROMPT
    assert "‐" in _SYSTEM_PROMPT  # U+2010
    assert "＝" in _SYSTEM_PROMPT  # U+FF1D
    assert "熟字訓" in _SYSTEM_PROMPT
    # 拆字正例（多字块 + , 分隔，新写法）
    assert "{物語||もの,がたり}" in _SYSTEM_PROMPT
    assert "{毎日||まい,にち}" in _SYSTEM_PROMPT
    assert "{大冒険||だい,ぼう,けん}" in _SYSTEM_PROMPT
    # 不拆反例（多字块 + 末尾,）
    assert "{今日||きょう,}" in _SYSTEM_PROMPT
    assert "{昨日||きのう,}" in _SYSTEM_PROMPT
    assert "{大人||おとな,}" in _SYSTEM_PROMPT
    assert "{風邪||かぜ,}" in _SYSTEM_PROMPT
    # 一日的双重读（ついたち 熟字訓 vs いちにち 可拆）须有提示
    assert "ついたち" in _SYSTEM_PROMPT
    # 块边界 = morpheme 边界 的明示
    assert "morpheme 境界" in _SYSTEM_PROMPT


def test_prompt_no_longer_uses_single_block_compound_notation():
    """PR8 后旧的「连续单字块表示复合词」写法不应再出现在 prompt 里。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        _SYSTEM_PROMPT, _OUTPUT_SCHEMA_HINT,
    )

    # PR6 引入的旧写法（已被 PR8 取代）：不能再出现
    for bad in ("{物||もの}{語||がたり}", "{笑||え}{顔||かお}", "{毎||まい}{日||にち}",
                "{大||だい}{冒||ぼう}{険||けん}", "{日||に}{本||ほん}"):
        assert bad not in _SYSTEM_PROMPT, f"prompt 仍含旧写法 {bad!r}"
        assert bad not in _OUTPUT_SCHEMA_HINT, f"schema hint 仍含旧写法 {bad!r}"


def test_prompt_schema_hint_examples_consistent():
    """schema hint 用 PR8 新写法。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import _OUTPUT_SCHEMA_HINT

    assert "{毎日||まい,にち}" in _OUTPUT_SCHEMA_HINT
    assert "{物語||もの,がたり}" in _OUTPUT_SCHEMA_HINT
    assert "{今日||きょう,}" in _OUTPUT_SCHEMA_HINT
    # 隣接复合词示例
    assert "{物語||もの,がたり}{映画||えい,が}" in _OUTPUT_SCHEMA_HINT


def test_annotated_parser_does_not_merge_consecutive_single_blocks():
    """PR8 后：连续单字块各自独立，不再合并。

    多个复合词紧贴时（如 物語+映画 写成连续 4 个单字块）按邻接合并是错的；
    现严格遵守「{...}{...} 块边界 = morpheme 边界」。
    """
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import _annotated_to_pairs

    # 4 个连续单字块 → 4 个独立 pair（不再合并）
    pairs, raw = _annotated_to_pairs("{物||もの}{語||がたり}{映||えい}{画||が}")
    assert raw == "物語映画"
    assert pairs == [
        ("物", "もの"),
        ("語", "がたり"),
        ("映", "えい"),
        ("画", "が"),
    ]

    # 复合词用多字块写：morpheme 整段保护
    pairs, raw = _annotated_to_pairs("{物語||もの,がたり}{映画||えい,が}")
    assert raw == "物語映画"
    assert pairs == [
        ("物語", ["もの", "がたり"]),
        ("映画", ["えい", "が"]),
    ]


def test_annotated_parser_multi_block_for_compounds():
    """复合词用多字块表达：可拆用 ',' 分字，不可拆用末尾 ','。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import _annotated_to_pairs

    # 可拆复合词（辞典 もの‐がたり）
    pairs, _ = _annotated_to_pairs("{物語||もの,がたり}")
    assert pairs == [("物語", ["もの", "がたり"])]

    # 不可拆复合词（辞典 きょう 一塊）
    pairs, _ = _annotated_to_pairs("{今日||きょう,}")
    assert pairs == [("今日", "きょう")]

    # 三字可拆
    pairs, _ = _annotated_to_pairs("{大冒険||だい,ぼう,けん}")
    assert pairs == [("大冒険", ["だい", "ぼう", "けん"])]


def test_annotated_parser_isolated_single_block_keeps_string_form():
    """孤立单字块保留字符串形（独立 morpheme，无 Phase 5 保护，用户词典可生效）。"""
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import _annotated_to_pairs

    pairs, _ = _annotated_to_pairs("ある{日||ひ}")
    assert pairs == [("あ", "あ"), ("る", "る"), ("日", "ひ")]


# ── LLM 输出 → 项目内部 → 序列化回 annotated 的语义等价（核心回归） ──
#
# LLM 按辞典锚点输出 {毎日||まい,にち}（可拆），项目内部必须表现为「两个独立
# 字符不连词」、序列化回项目 annotated 形式必须是 {毎||まい}{日||にち}（而不是
# {毎日||まい,にち} —— 那是 linked compound 写法）。同时 morpheme 保护机制
# 通过独立字段 morpheme_span 工作，不依赖 linked_to_next/连词渲染。


def _llm_to_internal(line_text: str, llm_annotated: str, user_dict=None):
    """跑一次 LLM → AutoCheckService → Character[] 全链路，返回结果句子。"""
    from strange_uta_game.backend.application import AutoCheckService
    from strange_uta_game.backend.domain import Sentence
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
        LLMRubyAnalyzer,
        LLMRubyConfig,
        _parse_payload,
    )

    cfg = LLMRubyConfig(enabled=True, base_url="x", api_key="k", model="m")
    analyzer = LLMRubyAnalyzer(cfg, lines=[line_text], fallback=_SelfAnalyzer())
    payload = '{"lines":[{"i":0,"text":' + repr(llm_annotated).replace("'", '"') + "}]}"
    # 简化：直接打桩 mapping
    mapping = _parse_payload(payload, [line_text])
    analyzer._client.annotate_lines = lambda _l: (mapping, None)

    svc = AutoCheckService(
        ruby_analyzer=analyzer, auto_check_flags={},
        user_dictionary=user_dict or [],
    )
    s = Sentence.from_text(line_text, "s0")
    svc.apply_to_sentence(s, apply_user_dict=bool(user_dict), skip_romanize=True)
    return s


def test_llm_splittable_compound_yields_independent_chars():
    """LLM {毎日||まい,にち}（可拆） → 内部两个独立字符（毎/日 都不连词）。"""
    s = _llm_to_internal("毎日", "{毎日||まい,にち}")
    assert len(s.characters) == 2
    毎, 日 = s.characters
    assert 毎.char == "毎" and "".join(p.text for p in 毎.ruby.parts) == "まい"
    assert 日.char == "日" and "".join(p.text for p in 日.ruby.parts) == "にち"
    # 关键：可拆复合词内部不连词，与 {毎||まい}{日||にち} 等价
    assert not 毎.linked_to_next, "毎 不应连词（LLM 给了 {毎日||まい,にち} = 可拆）"
    assert not 日.linked_to_next


def test_llm_jukujikun_compound_yields_linked_chars():
    """LLM {今日||きょう,}（熟字訓） → 内部 linked compound（今 连词到 日）。"""
    s = _llm_to_internal("今日", "{今日||きょう,}")
    assert len(s.characters) == 2
    今, 日 = s.characters
    assert "".join(p.text for p in 今.ruby.parts) == "きょう"
    assert 日.ruby is None or not any(p.text for p in 日.ruby.parts)
    # 关键：熟字訓 linked
    assert 今.linked_to_next, "今日 是熟字訓，必须 linked"


def test_llm_splittable_round_trip_to_project_annotated():
    """端到端 round-trip：LLM {毎日||まい,にち} → 序列化回项目 annotated 必须
    是 {毎||まい}{日||にち}（两个独立块），不是 LLM 输入的 {毎日||まい,にち}。

    这是「LLM 协议」和「项目语义」的关键转换点：LLM 用辞典锚点的多字 ,
    写法表达"可拆"，项目用独立单字块表达"两个独立字符"，转换由 _build_results
    +AutoCheckService 完成。
    """
    from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
        sentence_to_annotated_line,
    )

    s = _llm_to_internal("毎日", "{毎日||まい,にち}")
    roundtrip = sentence_to_annotated_line(s.characters)
    # 应输出独立单字块形式（mora 边界 | 可能存在，但块结构必须是两个独立 {}）
    assert "{毎" in roundtrip and "{日" in roundtrip
    assert "{毎日" not in roundtrip, (
        f"可拆复合词不应回写为 linked compound 形式，actual: {roundtrip}"
    )


def test_llm_jukujikun_round_trip_preserves_linked_form():
    """熟字訓 round-trip 保持 {今日||...,} linked compound 形式。"""
    from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
        sentence_to_annotated_line,
    )

    s = _llm_to_internal("今日", "{今日||きょう,}")
    roundtrip = sentence_to_annotated_line(s.characters)
    # 必须保持一个多字块形式
    assert roundtrip.startswith("{今日"), (
        f"熟字訓应回写为 linked compound 形式，actual: {roundtrip}"
    )


def test_morpheme_protection_decoupled_from_linking():
    """morpheme 保护机制独立于连词渲染：可拆复合词虽然内部不连词，
    用户词典 日→ひ 仍不能穿透 LLM 已确定的 毎+日=まい+にち 上下文。

    本地注音和 LLM 注音共用同一套 morpheme_span 保护（PR1），与 linked_to_next
    无关。
    """
    user_dict = [{"enabled": True, "word": "日", "reading": "{日||ひ}"}]

    # 可拆复合词：内部不连词，但 morpheme 保护应阻止 日→ひ 覆盖 日
    s = _llm_to_internal("毎日", "{毎日||まい,にち}", user_dict=user_dict)
    日 = s.characters[1]
    日_reading = "".join(p.text for p in 日.ruby.parts)
    assert 日_reading == "にち", (
        f"毎日 内的 日 应保留 LLM 给的 にち（morpheme 保护），"
        f"被错误覆盖为 {日_reading!r}"
    )
    # 验证内部确实不连词（保护机制不依赖 linked_to_next）
    assert not s.characters[0].linked_to_next

    # 隔离单字：morpheme 保护不应阻止用户词典对孤立 日 生效
    s2 = _llm_to_internal("ある日", "ある{日||ひ}", user_dict=user_dict)
    孤立日 = s2.characters[2]
    孤立日_reading = "".join(p.text for p in 孤立日.ruby.parts)
    assert 孤立日_reading == "ひ"  # LLM 和词典恰好一致；关键是没出错


def test_autocheck_renders_katakana_english_block():
    """端到端：AutoCheckService 把 ギター/guitar 渲染为首字带英文、整词连词。"""
    from strange_uta_game.backend.application import AutoCheckService
    from strange_uta_game.backend.domain import Sentence
    from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import RubyResult

    class _KataEngAnalyzer:
        def analyze(self, text):
            if text == "ギター":
                return [RubyResult(text="ギター", reading="guitar", start_idx=0, end_idx=3)]
            return [RubyResult(text=c, reading=c, start_idx=i, end_idx=i + 1)
                    for i, c in enumerate(text)]

        def get_reading(self, text):
            return text

    svc = AutoCheckService(
        _KataEngAnalyzer(),
        auto_check_flags={"katakana": True},
        annotate_katakana_with_english=True,
    )
    s = Sentence.from_text("ギター", "s1")
    svc.apply_to_sentence(s)
    chars = s.characters
    assert len(chars) == 3
    # 首字承载英文 ruby，整词连词，后两字无 ruby
    assert chars[0].ruby is not None
    assert chars[0].ruby.parts[0].text == "guitar"
    assert chars[0].check_count == 1
    assert chars[0].linked_to_next and chars[1].linked_to_next
    assert chars[1].ruby is None and chars[2].ruby is None
    assert chars[1].check_count == 0 and chars[2].check_count == 0


def test_config_is_complete():
    assert LLMRubyConfig(base_url="a", api_key="b", model="c").is_complete()
    assert not LLMRubyConfig(base_url="", api_key="b", model="c").is_complete()
    assert not LLMRubyConfig(base_url="a", api_key="", model="c").is_complete()
