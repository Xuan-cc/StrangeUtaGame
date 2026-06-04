"""LLM 注音（llm_ruby）单元测试。

不发起任何真实网络请求：通过替换 ``LLMRubyClient`` 或注入假 client 验证
解析、按行回退、缓存命中/未命中等行为。
"""

import pytest

from strange_uta_game.backend.infrastructure.parsers.llm_ruby import (
    LLMRubyAnalyzer,
    LLMRubyConfig,
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
    import strange_uta_game.backend.infrastructure.parsers.llm_ruby as m

    monkeypatch.setattr(m, "_llm_log_dir", lambda: tmp_path / "llm_ruby")
    m.llm_log_event("request", url="http://x", attempt=1)
    log_file = tmp_path / "llm_ruby" / "requests.log"
    assert log_file.exists() and "request" in log_file.read_text(encoding="utf-8")
    m.clear_llm_logs()
    assert not (tmp_path / "llm_ruby").exists()


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
