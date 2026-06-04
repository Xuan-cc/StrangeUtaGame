"""LLM 注音 —— 调用用户自备的大模型为整首歌词做分词 + 平假名注音。

设计要点
--------
LLM 的产出（分词 + 平假名读音）与 WinRT/Sudachi 的 ``(surface, reading)`` 序列同构，
因此 :class:`LLMRubyAnalyzer` 复用 :class:`KanaDistributingAnalyzer` 的全部下游读音
分配逻辑（``_results_from_pairs``），无需改动 ``AutoCheckService``。

- **整首一次发送**：``annotate_lines(lines)`` 一次请求带上全部行（含行号与上下文），
  最大化跨行消歧能力。
- **两套 API 分支**：OpenAI 兼容（``/chat/completions``）与 Anthropic 原生
  （``/v1/messages``）。
- **失败自动回退**：网络/鉴权/JSON 整体失败 → 标记 ``llm_failed``，所有行回退本地
  引擎；个别行缺失或 surface 拼接 ≠ 原行 → 该行回退，其余行用 LLM 结果。
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    KanaDistributingAnalyzer,
    RubyAnalyzer,
    RubyResult,
    is_all_katakana,
    is_english_reading,
)

# 平假名读音类型别名：每行是 (surface, reading) 序列
Pairs = List[Tuple[str, str]]

_ANTHROPIC_VERSION = "2023-06-01"

# 会话级请求日志：写到程序目录 .cache/llm_ruby/requests.log，启动与退出时清理。
_LOG_DIR_NAME = "llm_ruby"
_LOG_FILE_NAME = "requests.log"


def _llm_log_dir() -> Path:
    """LLM 请求日志目录（程序目录下 .cache/llm_ruby，与 tsm_cache 同源）。"""
    program_dir = Path(sys.argv[0]).resolve().parent
    return program_dir / ".cache" / _LOG_DIR_NAME


def llm_log_event(event: str, **fields) -> None:
    """追加一条 JSON 行日志（失败静默，绝不影响主流程）。"""
    try:
        d = _llm_log_dir()
        d.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        record.update(fields)
        with open(d / _LOG_FILE_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def clear_llm_logs() -> None:
    """清除 LLM 请求日志目录（程序启动 / 退出时调用）。"""
    import shutil

    try:
        d = _llm_log_dir()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    except Exception:
        pass

_SYSTEM_PROMPT = (
    "あなたは日本語の注音（ふりがな）エンジンです。"
    "与えられた歌詞を行ごとに分かち書きし、各トークンに平仮名の読みを付けます。"
    "規則：(1) 漢字を含むトークンの読みは平仮名で。"
    "(2) 仮名・記号・英数字のトークンの読みは原文そのまま。"
    "(3) 各行のトークンの surface を連結すると元の行と完全に一致すること。"
    "(4) JSON のみを出力し、説明文を一切付けないこと。"
)

_OUTPUT_SCHEMA_HINT = (
    '出力フォーマット（JSON のみ）：\n'
    '{"lines":[{"i":0,"tokens":[{"s":"今日","r":"きょう"},{"s":"は","r":"は"}]}]}\n'
    "i は行番号、tokens はその行の分かち書き、s は原文の断片、r は平仮名の読み。"
)

# 片假名外来语 → 英文标注规则（仅在 annotate_katakana_with_english 开启时追加）
_ENGLISH_KATAKANA_RULE = (
    "(5) 英語由来の外来語のカタカナ語（例：ギター→guitar、コンピューター→computer、"
    "メロディー→melody）は、読み r に元の英単語の綴り（小文字）を入れる。"
    "英語に対応しないカタカナ（擬音語・和製語・人名など、例：ドキドキ・コタツ）は"
    "通常どおりカタカナ/平仮名の読みを返し、英語にしないこと。"
)


@dataclass
class LLMRubyConfig:
    """LLM 注音连接配置（来源 config.json[llm_ruby]）。"""

    enabled: bool = False
    provider: str = "openai"  # "openai" | "anthropic" | "custom"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    apply_user_dict: bool = True
    timeout_sec: int = 60

    @classmethod
    def from_settings(cls, settings) -> "LLMRubyConfig":
        """从 AppSettings 读取（``settings.get("llm_ruby.<k>")``）。"""
        return cls(
            enabled=bool(settings.get("llm_ruby.enabled", False)),
            provider=str(settings.get("llm_ruby.provider", "openai") or "openai"),
            base_url=str(settings.get("llm_ruby.base_url", "") or "").strip(),
            api_key=str(settings.get("llm_ruby.api_key", "") or "").strip(),
            model=str(settings.get("llm_ruby.model", "") or "").strip(),
            apply_user_dict=bool(settings.get("llm_ruby.apply_user_dict", True)),
            timeout_sec=int(settings.get("llm_ruby.timeout_sec", 60) or 60),
        )

    def is_complete(self) -> bool:
        """连接信息是否齐全（可发起请求）。"""
        return bool(self.base_url and self.api_key and self.model)

    @property
    def api_format(self) -> str:
        """规范化的接口格式：``openai`` | ``anthropic`` | ``responses``。

        兼容历史值：``custom`` 等未知值一律按 OpenAI Chat Completions 处理。
        """
        p = (self.provider or "openai").lower()
        if p == "anthropic":
            return "anthropic"
        if p == "responses":
            return "responses"
        return "openai"

    @property
    def is_anthropic(self) -> bool:
        return self.api_format == "anthropic"


def _resolve_proxies(settings) -> Optional[Dict[str, str]]:
    """复用 updater 的代理解析（与网络词典/更新器一致）。settings 可为 None。"""
    if settings is None:
        return None
    try:
        from strange_uta_game.updater.proxy import resolve_proxy

        mode = str(settings.get("updater.proxy.mode", "system") or "system")
        manual = str(settings.get("updater.proxy.manual_url", "") or "")
        _info, proxies = resolve_proxy(mode, manual)
        return proxies
    except Exception:
        return None


class LLMRubyError(Exception):
    """LLM 注音请求/解析失败。``message`` 为面向用户的可读原因。"""


class LLMRubyClient:
    """LLM 注音 HTTP 客户端（OpenAI 兼容 / Anthropic 原生）。"""

    def __init__(
        self,
        config: LLMRubyConfig,
        proxies: Optional[Dict[str, str]] = None,
        annotate_english: bool = False,
    ):
        self._cfg = config
        self._proxies = proxies
        # 是否要求 LLM 为英语外来语片假名返回英文读音
        self._annotate_english = annotate_english

    # ── 公共 API ──

    def test_connection(self) -> Tuple[bool, str]:
        """对一行示例做最小注音请求，验证连通性。

        返回 ``(ok, message)``：成功时 message 含耗时；失败时为可读错误。
        """
        if not self._cfg.is_complete():
            return (False, "连接信息不完整：请填写 Base URL、API Key 与模型")
        sample = "今日はいい天気"
        start = time.time()
        try:
            result, err = self.annotate_lines([sample])
        except LLMRubyError as e:
            return (False, str(e))
        except Exception as e:  # noqa: BLE001
            return (False, f"{type(e).__name__}: {e}")
        elapsed = time.time() - start
        # 先暴露请求/解析的真实错误（网络、鉴权、HTTP、JSON 等）
        if err is not None:
            return (False, err)
        if 0 not in result:
            return (
                False,
                "请求成功，但返回内容未能解析为该行的有效注音"
                "（surface 与原文不匹配或行缺失）",
            )
        readings = "".join(r for _, r in result[0])
        return (True, f"连接成功（{elapsed:.1f}s，模型 {self._cfg.model}）：{sample} → {readings}")

    def annotate_lines(
        self, lines: List[str]
    ) -> Tuple[Dict[int, Pairs], Optional[str]]:
        """整首一次请求，返回 ``({line_idx: pairs}, error)``。

        error 为 None 表示请求成功（个别行可能因校验失败而缺席，由调用方按行回退）；
        非 None 表示整体失败（网络/鉴权/JSON），调用方应全部回退本地引擎。
        """
        if not self._cfg.is_complete():
            return ({}, "连接信息不完整")
        llm_log_event("annotate_start", lines=len(lines))
        try:
            raw = self._request(lines)
        except LLMRubyError as e:
            llm_log_event("annotate_error", error=str(e))
            return ({}, str(e))
        except Exception as e:  # noqa: BLE001
            llm_log_event("annotate_error", error=f"{type(e).__name__}: {e}")
            return ({}, f"{type(e).__name__}: {e}")
        try:
            mapping = _parse_payload(raw, lines)
        except Exception as e:  # noqa: BLE001
            snippet = (raw or "")[:300].replace("\n", " ")
            llm_log_event("parse_error", error=str(e), raw=self._redact(raw)[:2000])
            return ({}, f"返回内容解析失败：{e}；原始返回：{snippet}")
        # 记录命中/缺失行数，便于核对按行回退
        llm_log_event(
            "annotate_done",
            total=len(lines),
            annotated=len(mapping),
            missing=sorted(set(range(len(lines))) - set(mapping.keys())),
        )
        return (mapping, None)

    # ── 请求构造 ──

    def _build_user_prompt(self, lines: List[str]) -> str:
        numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(lines))
        english_rule = ("\n" + _ENGLISH_KATAKANA_RULE) if self._annotate_english else ""
        return (
            "次の日本語の歌詞（1曲全体、文脈を保持すること）を行ごとに注音してください。\n"
            f"{_OUTPUT_SCHEMA_HINT}"
            f"{english_rule}\n"
            "歌詞：\n"
            f"{numbered}"
        )

    @staticmethod
    def _endpoint_url(base: str, fmt: str) -> str:
        """根据 base_url 与接口格式推导最终端点，避免重复/缺失路径。

        - 末尾 ``#`` 表示「按字面 URL 使用，不再追加任何路径」（one-api/LiteLLM 习惯）。
        - 已含目标端点路径时原样使用。
        - ``anthropic`` → ``/v1/messages``；``responses`` → ``/v1/responses``；
          ``openai`` → ``/chat/completions``。``.../v1`` 结尾只补最后一段。
        """
        b = (base or "").strip()
        if b.endswith("#"):
            return b[:-1]
        b = b.rstrip("/")
        if fmt == "anthropic":
            if b.endswith("/messages"):
                return b
            return b + ("/messages" if b.endswith("/v1") else "/v1/messages")
        if fmt == "responses":
            if b.endswith("/responses"):
                return b
            return b + ("/responses" if b.endswith("/v1") else "/v1/responses")
        # openai chat completions
        if b.endswith("/chat/completions"):
            return b
        return b + "/chat/completions"

    def _request(self, lines: List[str]) -> str:
        """发起请求，返回模型输出的原始文本（应为 JSON 字符串）。"""
        user_prompt = self._build_user_prompt(lines)
        fmt = self._cfg.api_format
        url = self._endpoint_url(self._cfg.base_url, fmt)

        if fmt == "anthropic":
            headers = {
                "x-api-key": self._cfg.api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
                "content-type": "application/json",
            }
            body = {
                "model": self._cfg.model,
                "max_tokens": 8192,
                "temperature": 0,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            # Anthropic 参数稳定；仅 temperature 可能被个别模型拒绝，作为可选剥离项。
            data = self._post_with_param_fallback(url, headers, body, ["temperature"])
            return _extract_content(data, "anthropic")

        if fmt == "responses":
            headers = {
                "Authorization": f"Bearer {self._cfg.api_key}",
                "content-type": "application/json",
            }
            body = {
                "model": self._cfg.model,
                "instructions": _SYSTEM_PROMPT,
                "input": user_prompt,
                "temperature": 0,
                "text": {"format": {"type": "json_object"}},
            }
            # Responses API：text（结构化输出）与 temperature 在部分模型上不被支持。
            data = self._post_with_param_fallback(url, headers, body, ["text", "temperature"])
            return _extract_content(data, "responses")

        # OpenAI Chat Completions（兼容）
        headers = {
            "Authorization": f"Bearer {self._cfg.api_key}",
            "content-type": "application/json",
        }
        body = {
            "model": self._cfg.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        # 不少兼容端点/推理模型不支持 response_format 或固定 temperature → 400。
        # 逐步剥离重试（_coerce_json 已能兜底解析非严格 JSON）。
        data = self._post_with_param_fallback(
            url, headers, body, ["response_format", "temperature"]
        )
        return _extract_content(data, "openai")

    def _post_with_param_fallback(
        self, url: str, headers: dict, body: dict, optional_keys: List[str]
    ) -> dict:
        """请求；遇 HTTP 400 时按 ``optional_keys`` 顺序累计剥离可选参数重试。"""
        attempts: List[dict] = [dict(body)]
        cur = dict(body)
        for k in optional_keys:
            if k in cur:
                cur = {kk: vv for kk, vv in cur.items() if kk != k}
                attempts.append(dict(cur))
        last_err: Optional[LLMRubyError] = None
        for attempt in attempts:
            try:
                return self._post_json(url, headers, attempt)
            except LLMRubyError as e:
                # 仅对 400（参数问题）继续剥离重试；其他错误立即抛出
                if not str(e).startswith("HTTP 400"):
                    raise
                last_err = e
        assert last_err is not None
        raise last_err

    # 瞬时错误重试：网络层异常（含 443 端口的 TLS/连接中断）与 429/5xx。
    _TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504})
    _MAX_RETRIES = 2

    def _redact(self, data) -> str:
        """把请求体/响应文本转为字符串并抹掉 api_key（防止泄漏到日志）。"""
        s = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        key = self._cfg.api_key
        if key:
            s = s.replace(key, "***")
        return s

    def _post_json(self, url: str, headers: dict, body: dict) -> dict:
        """POST 并返回解析后的 JSON 响应体；非 200 抛 LLMRubyError。

        瞬时失败（网络中断 / 429 / 5xx）按 :attr:`_MAX_RETRIES` 退避重试；
        每次请求/响应/错误写入会话级日志（api_key 已脱敏）。
        """
        import requests

        last_err: Optional[LLMRubyError] = None
        for attempt in range(self._MAX_RETRIES + 1):
            llm_log_event(
                "request", url=url, attempt=attempt + 1,
                format=self._cfg.api_format, body=self._redact(body),
            )
            start = time.time()
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=self._cfg.timeout_sec,
                    proxies=self._proxies,
                )
            except requests.exceptions.RequestException as e:
                llm_log_event(
                    "network_error", url=url, attempt=attempt + 1, error=str(e)
                )
                last_err = LLMRubyError(f"网络请求失败：{e}")
                if attempt < self._MAX_RETRIES:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise last_err from e

            elapsed_ms = int((time.time() - start) * 1000)
            text = resp.text or ""
            llm_log_event(
                "response", url=url, attempt=attempt + 1,
                status=resp.status_code, elapsed_ms=elapsed_ms,
                body=self._redact(text)[:20000],
            )

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as e:
                    raise LLMRubyError(f"响应非 JSON：{e}") from e

            # 429 / 5xx 视为瞬时，退避重试
            if resp.status_code in self._TRANSIENT_STATUS and attempt < self._MAX_RETRIES:
                last_err = LLMRubyError(f"HTTP {resp.status_code}：{text[:200]}")
                time.sleep(0.8 * (attempt + 1))
                continue

            raise LLMRubyError(f"HTTP {resp.status_code}：{text[:200]}")

        # 理论不可达：重试耗尽（网络/瞬时）后由上面 raise；兜底
        assert last_err is not None
        raise last_err


# ──────────────────────────────────────────────
# 响应解析（独立函数，便于单测）
# ──────────────────────────────────────────────


def _extract_content(data: dict, fmt: str) -> str:
    """从 API 响应体取出模型输出文本（按接口格式）。"""
    try:
        if fmt == "anthropic":
            # {"content":[{"type":"text","text":"..."}]}
            parts = data.get("content") or []
            return "".join(
                p.get("text", "") for p in parts if isinstance(p, dict)
            )
        if fmt == "responses":
            # 便捷字段优先；否则遍历 output[].content[].text（跳过 reasoning 等无 text 项）
            if isinstance(data.get("output_text"), str):
                return data["output_text"]
            texts: List[str] = []
            for item in data.get("output") or []:
                if not isinstance(item, dict):
                    continue
                for part in item.get("content") or []:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        texts.append(part["text"])
            return "".join(texts)
        # openai chat completions: {"choices":[{"message":{"content":"..."}}]}
        choices = data.get("choices") or []
        return choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMRubyError(f"响应结构异常：{e}") from e


def _coerce_json(text: str) -> dict:
    """把模型输出文本解析为 JSON 对象（容忍包裹的 ```json 代码块）。"""
    s = (text or "").strip()
    if s.startswith("```"):
        # 去除 ```json ... ``` 围栏
        s = s.strip("`")
        if s[:4].lower() == "json":
            s = s[4:]
        s = s.strip()
    # 容错：截取首个 { 到末个 }
    if not s.startswith("{"):
        lo = s.find("{")
        hi = s.rfind("}")
        if lo != -1 and hi != -1 and hi > lo:
            s = s[lo : hi + 1]
    return json.loads(s)


def _parse_payload(text: str, lines: List[str]) -> Dict[int, Pairs]:
    """把模型输出解析为 ``{line_idx: pairs}``。

    校验：每行 tokens 的 surface 连接需与原行完全一致，否则丢弃该行（调用方回退）。
    """
    obj = _coerce_json(text)
    raw_lines = obj.get("lines")
    if not isinstance(raw_lines, list):
        raise ValueError("缺少 lines 数组")

    mapping: Dict[int, Pairs] = {}
    for entry in raw_lines:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("i")
        if not isinstance(idx, int) or not (0 <= idx < len(lines)):
            continue
        tokens = entry.get("tokens")
        if not isinstance(tokens, list):
            continue
        pairs: Pairs = []
        for tok in tokens:
            if not isinstance(tok, dict):
                continue
            s = tok.get("s", "")
            r = tok.get("r", "")
            if not isinstance(s, str) or not isinstance(r, str) or not s:
                continue
            pairs.append((s, r or s))
        # 校验：surface 连接 == 原行
        if "".join(s for s, _ in pairs) != lines[idx]:
            continue  # 该行丢弃，由调用方回退
        mapping[idx] = pairs
    return mapping


# ──────────────────────────────────────────────
# 分析器
# ──────────────────────────────────────────────


class LLMRubyAnalyzer(KanaDistributingAnalyzer):
    """以 LLM 整首注音为准的分析器；缓存未命中/失败时回退到本地引擎。

    与其他分析器一致：``analyze(text)`` 逐句调用。首次调用时惰性触发整首批量
    请求（HTTP 因此发生在调用方的后台 worker 线程内）。
    """

    def __init__(
        self,
        config: LLMRubyConfig,
        lines: List[str],
        fallback: RubyAnalyzer,
        proxies: Optional[Dict[str, str]] = None,
        annotate_katakana_with_english: bool = False,
    ):
        self._cfg = config
        self._lines = list(lines)
        self._fallback = fallback
        self._annotate_katakana_with_english = annotate_katakana_with_english
        self._client = LLMRubyClient(
            config, proxies=proxies, annotate_english=annotate_katakana_with_english
        )
        self._cache: Dict[str, Pairs] = {}
        self._prewarmed = False
        self.llm_failed = False
        self.last_error: Optional[str] = None

        # KanaDistributingAnalyzer 的读音分配需要 pykakasi 参考（同其他子类）
        self._pykakasi_conv = None
        try:
            import pykakasi

            kks = pykakasi.kakasi()
            kks.setMode("J", "H")
            self._pykakasi_conv = kks.getConverter()
        except Exception:
            pass

    def prewarm(self) -> None:
        """显式触发整首批量请求（供调用方在进度提示「等待 LLM」时主动调用）。"""
        self._ensure_prewarmed()

    def _ensure_prewarmed(self) -> None:
        if self._prewarmed:
            return
        self._prewarmed = True
        mapping, err = self._client.annotate_lines(self._lines)
        if err is not None:
            self.llm_failed = True
            self.last_error = err
            return
        # 按文本缓存（重复行映射到同一结果）
        for idx, pairs in mapping.items():
            if 0 <= idx < len(self._lines):
                self._cache[self._lines[idx]] = pairs

    def analyze(self, text: str) -> List[RubyResult]:
        if not text:
            return []
        self._ensure_prewarmed()
        pairs = self._cache.get(text)
        if pairs is not None:
            return self._build_results(pairs)
        # 未命中（失败 / 该行被校验丢弃 / 不在原始行集合）→ 回退本地引擎
        return self._fallback.analyze(text)

    def _build_results(self, pairs: Pairs) -> List[RubyResult]:
        """把 LLM 的 (surface, reading) 序列转为 RubyResult。

        片假名外来语 + 英文读音（且开关开启）→ 整词作为单块 RubyResult（保留英文读音，
        下游 ``AutoCheckService`` 识别为 ``katakana_english`` 来源，首字承载英文、整词连词）；
        其余 pair 复用基类 :meth:`_results_from_pairs` 的逐字/逐块假名分配。
        """
        results: List[RubyResult] = []
        pos = 0
        for surface, reading in pairs:
            start = pos
            end = pos + len(surface)
            if (
                self._annotate_katakana_with_english
                and is_all_katakana(surface)
                and is_english_reading(reading)
            ):
                # 片假名外来语：整词单块，保留英文读音（不经过假名分配，避免被丢弃）
                results.append(
                    RubyResult(
                        text=surface, reading=reading.strip(),
                        start_idx=start, end_idx=end,
                    )
                )
            else:
                # 复用基类逐 pair 分配，再平移索引到本行内的绝对位置
                for r in self._results_from_pairs([(surface, reading)]):
                    results.append(
                        RubyResult(
                            text=r.text, reading=r.reading,
                            start_idx=r.start_idx + start,
                            end_idx=r.end_idx + start,
                        )
                    )
            pos = end
        return results

    def get_reading(self, text: str) -> str:
        if not text:
            return ""
        self._ensure_prewarmed()
        pairs = self._cache.get(text)
        if pairs is not None:
            return "".join(r for _, r in pairs)
        return self._fallback.get_reading(text)
