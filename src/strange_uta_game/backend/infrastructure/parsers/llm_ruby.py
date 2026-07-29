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
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from strange_uta_game import app_dirs
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    KanaDistributingAnalyzer,
    RubyAnalyzer,
    RubyResult,
    is_all_katakana,
    is_english_reading,
)

# 读音可为字符串（整段读音，由本地分配器拆到字）或字符串数组（LLM 已自分到字，
# 与 surface 等长，空串表示该字与前字连词）。
Reading = Union[str, List[str]]
Pairs = List[Tuple[str, Reading]]

_ANTHROPIC_VERSION = "2023-06-01"

# 会话级请求日志：写到程序目录 .cache/llm_ruby/，**仅启动时清理**。
# 退出时保留以便事后复盘（崩溃前的请求 / 响应不会随退出消失）。
#
# 文件布局（每次 HTTP 调用三件套，按全局自增 seq 编号）：
#   .cache/llm_ruby/
#     index.log                  jsonl 概要：seq/ts/event/url/status/elapsed/error
#     001-request.json           完整请求体（pretty JSON，api_key 已抹）
#     001-response.json          完整响应体（JSON 则 pretty；非 JSON 则原样 .txt）
#     001-extracted.txt          _extract_content 后的纯文本（用户最想读的那段）
#     002-request.json           ...
#
# 不再把 body 塞进 index.log 字段（旧设计 JSON 套 JSON、转义满屏）。
_LOG_DIR_NAME = "llm_ruby"
_INDEX_FILE_NAME = "index.log"

# 全局调用序号（进程级单调递增），并发安全。
_call_lock = threading.Lock()
_call_seq = 0


def _next_call_seq() -> int:
    global _call_seq
    with _call_lock:
        _call_seq += 1
        return _call_seq


def _llm_log_dir() -> Path:
    """LLM 请求日志目录（缓存根目录下 llm_ruby 子目录，与 tsm_cache 同源）。"""
    return app_dirs.cache_dir() / _LOG_DIR_NAME


def llm_log_event(event: str, **fields) -> None:
    """向 index.log 追加一条 JSON 行（仅放小字段：seq/url/status/elapsed_ms/error）。

    完整请求/响应内容请用 :func:`_dump_call`，不要塞进这里 —— 否则又会变成
    JSON 套 JSON、转义满屏。失败静默，绝不影响主流程。
    """
    try:
        d = _llm_log_dir()
        d.mkdir(parents=True, exist_ok=True)
        record = {"ts": datetime.now().isoformat(timespec="seconds"), "event": event}
        record.update(fields)
        with open(d / _INDEX_FILE_NAME, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _dump_call(
    seq: int, kind: str, content: Any,
    redactor: Optional[Callable[[str], str]] = None,
) -> None:
    """把单次调用的请求 / 响应 / 抽取文本完整写到独立文件。

    Args:
        seq: 全局调用序号（来自 :func:`_next_call_seq`）。
        kind: ``request`` / ``response`` / ``extracted`` / ``network_error`` /
            ``parse_error``。决定文件名后缀和默认扩展。
        content: 任意值。dict/list → pretty JSON；str → 原样写 .txt（或
            尝试当 JSON 解析后 pretty）；其他 → str() 后写 .txt。
        redactor: 可选回调，对序列化后的字符串再脱敏（如抹掉 api_key）。

    失败静默。文件按 ``{seq:04d}-{kind}.{ext}`` 命名。
    """
    try:
        d = _llm_log_dir()
        d.mkdir(parents=True, exist_ok=True)
        if isinstance(content, (dict, list)):
            text = json.dumps(content, ensure_ascii=False, indent=2)
            ext = "json"
        elif isinstance(content, str):
            # 尝试识别 JSON 文本并美化（响应体常是 JSON 字符串）；失败原样。
            stripped = content.lstrip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    parsed = json.loads(content)
                    text = json.dumps(parsed, ensure_ascii=False, indent=2)
                    ext = "json"
                except Exception:
                    text = content
                    ext = "txt"
            else:
                text = content
                ext = "txt"
        else:
            text = str(content)
            ext = "txt"
        if redactor is not None:
            text = redactor(text)
        path = d / f"{seq:04d}-{kind}.{ext}"
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def clear_llm_logs() -> None:
    """清除 LLM 请求日志目录（程序启动 / 退出时调用）。"""
    import shutil

    global _call_seq
    try:
        d = _llm_log_dir()
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
        with _call_lock:
            _call_seq = 0
    except Exception:
        pass

_SYSTEM_PROMPT = (
    "あなたは日本語の注音（ふりがな）エンジンです。"
    "与えられた歌詞を行ごとに、漢字を含むまとまりに ``{原文||読み}`` の形式で"
    "ふりがなを付け、注音済みの行テキストを返します。"
    "規則："
    "(1) 漢字を含む語/形態素を ``{原文||読み}`` で囲む。読みは平仮名のみ。"
    "(2) 仮名・記号・英数字（ふりがな不要なもの）は ``{}`` で囲まず原文そのまま。"
    "(3) ``{}`` を取り除いた行テキストは元の行と完全に一致すること（文字を増減しない）。"
    "(4) JSON のみを出力し、説明文を一切付けないこと。"
    "(5) 入力は日本語の歌詞である。文脈に合った標準的・一般的な複合語読みを優先し、"
    "一字一字を独立した訓読みで切ってはいけない。例：「毎日」→ まいにち（×まいひ）、"
    "「日々」→ ひび（×にちひ）、「一日」→ ついたち または いちにち（文脈で選択、×にちひ）。"
    "(6) **複合語の拆字判定：小学館デジタル大辞泉の表記を基準とする**。"
    "weblio で複合語を引くと、小学館デジタル大辞泉の見出しでは漢字に対応する"
    "読みの間に ``‐``（U+2010 ハイフン）または ``＝``（U+FF1D 全角イコール）を"
    "挿入して形態素境界を示している（例：「もの‐がたり【物語】」「まい‐にち【毎日】」）。"
    "(a) ``‐`` / ``＝`` が読みに含まれる複合語 → **拆字可**。"
    "一つの多字ブロック内で読みを ``,`` で字ごとに区切る："
    "「物語」もの‐がたり → ``{物語||もの,がたり}``、"
    "「毎日」まい‐にち → ``{毎日||まい,にち}``、"
    "「笑顔」え‐がお → ``{笑顔||え,がお}``、"
    "「大冒険」だい‐ぼう‐けん → ``{大冒険||だい,ぼう,けん}``、"
    "「日本」に‐ほん → ``{日本||に,ほん}``。"
    "(b) ``‐`` / ``＝`` がなく一塊の読みとして辞書に載っているもの → **不拆**（熟字訓・当て字）。"
    "多字ブロックの末尾に ``,`` ＋空読みで「整段を前字に集約」を示す："
    "「今日」きょう → ``{今日||きょう,}``、"
    "「昨日」きのう → ``{昨日||きのう,}``、"
    "「大人」おとな → ``{大人||おとな,}``、"
    "「一日」ついたち → ``{一日||ついたち,}``（いちにち の場合は ``{一日||いち,にち}`` 拆字可）、"
    "「風邪」かぜ → ``{風邪||かぜ,}``、"
    "「七夕」たなばた → ``{七夕||たなばた,}``。"
    "(c) **ブロック境界 ``{...}{...}`` は必ず morpheme 境界**。"
    "複数の独立した複合語が隣接する場合はそれぞれ独立した ``{...}`` ブロックにする："
    "「今日は毎日」→ ``{今日||きょう,}は{毎日||まい,にち}``、"
    "「物語映画」→ ``{物語||もの,がたり}{映画||えい,が}``。"
    "(d) 単字ブロック ``{字||読み}`` は「単独の字としての注音」を意味し、"
    "他のブロックと自動でひとまとめにならない。複合語は必ず多字ブロック ``{XX||a,b}`` で書くこと。"
    "迷ったら多字ブロック＋末尾 ``,`` 一括（不拆）にする方が安全。"
)

_OUTPUT_SCHEMA_HINT = (
    '出力フォーマット（JSON のみ）：\n'
    '{"lines":[{"i":0,'
    '"text":"{今日||きょう,}は{毎日||まい,にち}の{物語||もの,がたり}"}]}\n'
    "i は行番号、text はその行の注音済み inline annotated 形式。\n"
    "annotated 文法（プロジェクト共通の内部形式）：\n"
    "- ``{原文||読み区}`` —— 一つの morpheme を一ブロックで表す。\n"
    "- 読み区は ``,`` で字ごとの読みを区切る（原文の文字数と一致）。\n"
    "- 末尾 ``,`` ＋空読み = ブロック全体が前字に集約（熟字訓・当て字）。\n"
    "- ブロック境界 ``{...}{...}`` は厳密に morpheme 境界。隣接する複数の複合語は"
    "それぞれ独立ブロックに書く（自動結合しない）。\n"
    "- ブロック外は ``{}`` を付けず原文のまま（仮名/記号/英数字）。\n"
    "\n"
    "拆字可（辞書に ``‐`` / ``＝`` あり）— 多字ブロック内 ``,`` で区切る：\n"
    "  「物語」  → ``{物語||もの,がたり}``\n"
    "  「笑顔」  → ``{笑顔||え,がお}``\n"
    "  「毎日」  → ``{毎日||まい,にち}``\n"
    "  「大冒険」→ ``{大冒険||だい,ぼう,けん}``\n"
    "  「日本」  → ``{日本||に,ほん}``\n"
    "\n"
    "不拆（辞書で ``‐`` / ``＝`` なし、一塊の読み）— 多字ブロック＋末尾 ``,``：\n"
    "  「今日」  → ``{今日||きょう,}``\n"
    "  「昨日」  → ``{昨日||きのう,}``\n"
    "  「大人」  → ``{大人||おとな,}``\n"
    "  「一日（ついたち）」→ ``{一日||ついたち,}``\n"
    "  「風邪」  → ``{風邪||かぜ,}``\n"
    "  「七夕」  → ``{七夕||たなばた,}``\n"
    "\n"
    "複数の複合語が隣接：\n"
    "  「今日は毎日」→ ``{今日||きょう,}は{毎日||まい,にち}``\n"
    "  「物語映画」→ ``{物語||もの,がたり}{映画||えい,が}``\n"
    "\n"
    "連続する漢字でも複合語が確信できなければ字単位に分けず多字ブロック＋末尾 ``,`` で書く。"
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
    # 流式请求连续多久没有收到任何数据才判定为断线。
    timeout_sec: int = 180
    # 即使服务端持续发送 thinking/keep-alive，也不能无限占用 worker。
    total_timeout_sec: int = 600

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
            timeout_sec=int(settings.get("llm_ruby.timeout_sec", 180) or 180),
            total_timeout_sec=int(
                settings.get("llm_ruby.total_timeout_sec", 600) or 600
            ),
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
        # 记录本次 _request 链路中最近一次（成功）HTTP 调用的全局 seq，
        # 供 _request 把 extracted 文本对齐到同一调用编号下落盘。
        self._last_call_seq: Optional[int] = None
        # 进度回调：把「请求 / 已返回 / 解析 / 重试 / 第几轮」等内部阶段文本
        # 上报给 UI（由 worker 接到信号刷 StateToolTip）。失败静默，绝不影响主流程。
        self._progress_cb: Optional[Callable[[str], None]] = None

    def set_progress_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        """注册进度回调（线程安全无关：仅赋值；回调内部自行做线程转发）。"""
        self._progress_cb = cb

    def _report(self, msg: str) -> None:
        cb = self._progress_cb
        if cb is None:
            return
        try:
            cb(msg)
        except Exception:
            pass

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
        readings = "".join(
            "".join(r) if isinstance(r, list) else r for _, r in result[0]
        )
        return (True, f"连接成功（{elapsed:.1f}s，模型 {self._cfg.model}）：{sample} → {readings}")

    # 整首一次性发送是既定策略；未通过校验（字符不符 / 漏注音漢字）的行，
    # 收集后再整批发给 LLM 重试一轮，多轮仍缺才由调用方回退本地引擎。
    # 总轮数 = 1 次首发 + (N-1) 次重试。
    _MAX_ANNOTATE_ROUNDS = 2

    def annotate_lines(
        self, lines: List[str]
    ) -> Tuple[Dict[int, Pairs], Optional[str]]:
        """整首一次请求 + 缺失行重试，返回 ``({line_idx: pairs}, error)``。

        - error 为 None：请求成功（个别行多轮重试后仍缺席的，由调用方按行回退）。
        - error 非 None：**首轮**整体失败（网络/鉴权/JSON），调用方应全部回退本地引擎。

        校验在 :func:`_parse_payload` 内完成（字符完整 + 漢字读音覆盖）。未达标的
        行不进 mapping → 成为下一轮的 pending 行，整批重发给 LLM 再试。
        """
        if not self._cfg.is_complete():
            return ({}, "连接信息不完整")

        n = len(lines)
        mapping: Dict[int, Pairs] = {}
        pending = list(range(n))

        for round_idx in range(self._MAX_ANNOTATE_ROUNDS):
            sub_lines = [lines[i] for i in pending]
            is_retry = round_idx > 0
            if is_retry:
                self._report(f"第 {round_idx + 1} 轮：为 {len(sub_lines)} 行缺失注音重试…")
            else:
                self._report(f"正在请求 LLM 注音（{len(sub_lines)} 行）…")
            llm_log_event(
                "annotate_start", round=round_idx + 1, lines=len(sub_lines),
                retry=is_retry,
            )

            try:
                raw = self._request(sub_lines)
            except LLMRubyError as e:
                if not is_retry:
                    llm_log_event("annotate_error", error=str(e))
                    return ({}, str(e))
                # 重试轮失败：保留已得结果，停止重试（缺失行交调用方回退）。
                llm_log_event("annotate_retry_error", round=round_idx + 1, error=str(e))
                break
            except Exception as e:  # noqa: BLE001
                if not is_retry:
                    llm_log_event("annotate_error", error=f"{type(e).__name__}: {e}")
                    return ({}, f"{type(e).__name__}: {e}")
                llm_log_event(
                    "annotate_retry_error", round=round_idx + 1,
                    error=f"{type(e).__name__}: {e}",
                )
                break

            self._report("已返回，正在解析…")
            try:
                sub_mapping = _parse_payload(raw, sub_lines)
            except Exception as e:  # noqa: BLE001
                snippet = (raw or "")[:300].replace("\n", " ")
                err_seq = _next_call_seq()
                _dump_call(err_seq, "parse_error", raw or "", redactor=self._redact)
                llm_log_event("parse_error", seq=err_seq, round=round_idx + 1, error=str(e))
                if not is_retry:
                    return ({}, f"返回内容解析失败：{e}；原始返回：{snippet}")
                break

            # 子索引（sub_lines 内 0-based）→ 原始行索引
            newly = 0
            for sub_idx, pairs in sub_mapping.items():
                orig = pending[sub_idx]
                if orig not in mapping:
                    mapping[orig] = pairs
                    newly += 1
            pending = [i for i in range(n) if i not in mapping]
            llm_log_event(
                "annotate_round_done", round=round_idx + 1,
                newly=newly, remaining=len(pending),
            )
            if not pending:
                break
            # 重试轮零新增 → 再发也是徒劳（同样的行同样的模型），停止以免空烧 token。
            # 首轮零新增仍要给一次重试（用户要求「被回退的再给 LLM 试一下」）。
            if is_retry and newly == 0:
                break

        llm_log_event(
            "annotate_done", total=n, annotated=len(mapping), missing=pending,
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
                "stream": True,
                "system": _SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_prompt}],
            }
            # Anthropic 参数稳定；仅 temperature 可能被个别模型拒绝，作为可选剥离项。
            data = self._post_with_param_fallback(
                url, headers, body, ["temperature", "stream"]
            )
            extracted = _extract_content(data, "anthropic")
        elif fmt == "responses":
            headers = {
                "Authorization": f"Bearer {self._cfg.api_key}",
                "content-type": "application/json",
            }
            body = {
                "model": self._cfg.model,
                "instructions": _SYSTEM_PROMPT,
                "input": user_prompt,
                "temperature": 0,
                "stream": True,
                "text": {"format": {"type": "json_object"}},
            }
            # Responses API：text（结构化输出）与 temperature 在部分模型上不被支持。
            data = self._post_with_param_fallback(
                url, headers, body, ["text", "temperature", "stream"]
            )
            extracted = _extract_content(data, "responses")
        else:
            # OpenAI Chat Completions（兼容）
            headers = {
                "Authorization": f"Bearer {self._cfg.api_key}",
                "content-type": "application/json",
            }
            body = {
                "model": self._cfg.model,
                "temperature": 0,
                "stream": True,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            }
            # 不少兼容端点/推理模型不支持 response_format 或固定 temperature → 400。
            # 逐步剥离重试（_coerce_json 已能兜底解析非严格 JSON）。
            data = self._post_with_param_fallback(
                url, headers, body, ["response_format", "temperature", "stream"]
            )
            extracted = _extract_content(data, "openai")

        # 把抽取出的模型纯文本单独落盘 —— 这是用户最常想读的那段（annotated 行）。
        # _post_with_param_fallback 内部已落盘过本次成功响应（在重试链中），
        # 这里再写一个 extracted 文件对齐到最近的 seq。
        extracted_seq = self._last_call_seq
        if extracted_seq is not None:
            _dump_call(extracted_seq, "extracted", extracted or "", redactor=self._redact)
        return extracted

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
        每次请求/响应/错误**完整**落盘到 ``.cache/llm_ruby/NNNN-*.json``，
        index.log 只记 seq/url/status/elapsed 等小字段（不再 JSON 套 JSON）。
        """
        import requests

        last_err: Optional[LLMRubyError] = None
        for attempt in range(self._MAX_RETRIES + 1):
            seq = _next_call_seq()
            # 请求体完整落盘（pretty JSON，api_key 抹掉）
            _dump_call(seq, "request", body, redactor=self._redact)
            llm_log_event(
                "request", seq=seq, url=url, attempt=attempt + 1,
                format=self._cfg.api_format,
            )
            start = time.time()
            try:
                resp = requests.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=(15, self._cfg.timeout_sec),
                    proxies=self._proxies,
                    stream=bool(body.get("stream")),
                )
            except requests.exceptions.RequestException as e:
                _dump_call(seq, "network_error", str(e))
                llm_log_event(
                    "network_error", seq=seq, url=url, attempt=attempt + 1, error=str(e)
                )
                last_err = LLMRubyError(f"网络请求失败：{e}")
                if attempt < self._MAX_RETRIES:
                    self._report(
                        f"网络异常，重试中（{attempt + 2}/{self._MAX_RETRIES + 1}）…"
                    )
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise last_err from e

            elapsed_ms = int((time.time() - start) * 1000)
            content_type = str(resp.headers.get("content-type", "")).lower() if hasattr(
                resp, "headers"
            ) else ""
            is_stream = bool(body.get("stream")) and "text/event-stream" in content_type
            if resp.status_code == 200 and is_stream:
                try:
                    try:
                        text = self._consume_event_stream(resp, start)
                    except requests.exceptions.RequestException as e:
                        _dump_call(seq, "network_error", str(e))
                        llm_log_event(
                            "network_error", seq=seq, url=url,
                            attempt=attempt + 1, error=str(e), streamed=True,
                        )
                        last_err = LLMRubyError(f"网络请求失败：{e}")
                        if attempt < self._MAX_RETRIES:
                            self._report(
                                "流式响应中断，重试中"
                                f"（{attempt + 2}/{self._MAX_RETRIES + 1}）…"
                            )
                            time.sleep(0.8 * (attempt + 1))
                            continue
                        raise last_err from e
                finally:
                    resp.close()
                data = {"_stream_text": text}
                _dump_call(seq, "response", data, redactor=self._redact)
                llm_log_event(
                    "response", seq=seq, url=url, attempt=attempt + 1,
                    status=resp.status_code,
                    elapsed_ms=int((time.time() - start) * 1000),
                    streamed=True,
                )
                self._last_call_seq = seq
                return data

            text = resp.text or ""
            # 响应体完整落盘（识别 JSON 则 pretty；非 JSON 原样 .txt；不再截断）
            _dump_call(seq, "response", text, redactor=self._redact)
            llm_log_event(
                "response", seq=seq, url=url, attempt=attempt + 1,
                status=resp.status_code, elapsed_ms=elapsed_ms,
            )

            if resp.status_code == 200:
                # 记下本次成功 seq，供 _request 把 extracted 文本对齐到同 seq 落盘。
                self._last_call_seq = seq
                try:
                    return resp.json()
                except ValueError as e:
                    raise LLMRubyError(f"响应非 JSON：{e}") from e

            # 429 / 5xx 视为瞬时，退避重试
            if resp.status_code in self._TRANSIENT_STATUS and attempt < self._MAX_RETRIES:
                last_err = LLMRubyError(f"HTTP {resp.status_code}：{text[:200]}")
                self._report(
                    f"服务端 {resp.status_code}，重试中"
                    f"（{attempt + 2}/{self._MAX_RETRIES + 1}）…"
                )
                time.sleep(0.8 * (attempt + 1))
                continue

            raise LLMRubyError(f"HTTP {resp.status_code}：{text[:200]}")

        # 理论不可达：重试耗尽（网络/瞬时）后由上面 raise；兜底
        assert last_err is not None
        raise last_err

    def _consume_event_stream(self, resp, started_at: float) -> str:
        """消费 OpenAI/Responses/Anthropic SSE，只累计最终答案文本。

        reasoning/thinking 增量仅作为请求仍存活的信号，不保存也不交给解析器。
        ``requests`` 的 read timeout 负责无数据超时；这里另设总时限。
        """
        chunks: List[str] = []
        reasoning_reported = False
        for raw_line in resp.iter_lines(decode_unicode=True):
            if time.time() - started_at > self._cfg.total_timeout_sec:
                raise LLMRubyError(
                    f"LLM 请求超过总时限 {self._cfg.total_timeout_sec} 秒"
                )
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace") if isinstance(
                raw_line, bytes
            ) else str(raw_line)
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                continue

            text_delta, is_reasoning = _extract_stream_delta(
                event, self._cfg.api_format
            )
            if text_delta:
                chunks.append(text_delta)
            elif is_reasoning and not reasoning_reported:
                self._report("模型正在推理…")
                reasoning_reported = True
        return "".join(chunks)


# ──────────────────────────────────────────────
# 响应解析（独立函数，便于单测）
# ──────────────────────────────────────────────


def _extract_content(data: dict, fmt: str) -> str:
    """从 API 响应体取出模型输出文本（按接口格式）。"""
    try:
        if isinstance(data.get("_stream_text"), str):
            return data["_stream_text"]
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


def _extract_stream_delta(data: dict, fmt: str) -> Tuple[str, bool]:
    """返回 ``(最终文本增量, 是否为推理活动)``，不暴露推理内容。"""
    if fmt == "anthropic":
        delta = data.get("delta") or {}
        delta_type = str(delta.get("type", ""))
        if delta_type == "text_delta":
            return (str(delta.get("text", "")), False)
        return ("", delta_type == "thinking_delta")

    if fmt == "responses":
        event_type = str(data.get("type", ""))
        if event_type == "response.output_text.delta":
            return (str(data.get("delta", "")), False)
        return ("", "reasoning" in event_type)

    choices = data.get("choices") or []
    if not choices:
        return ("", False)
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if isinstance(content, str) and content:
        return (content, False)
    return (
        "",
        bool(delta.get("reasoning_content") or delta.get("reasoning")),
    )


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


def _annotated_to_pairs(text: str) -> Tuple[Pairs, str]:
    """把 inline annotated 形式（``{原文||读音区}は{...}``）解析为 ``(pairs, raw_text)``。

    ``raw_text`` 是剥离所有 ``{...}`` 标注后的纯原文，供调用方校验是否等于行文本。

    annotated 文法（与 ``annotated_text.parse_annotated_line`` 兼容子集）：
    - ``{原文||读音1,读音2,...}`` —— 多字块；读音以 ``,`` 分字，``|`` 分 mora。
    - ``{原文|读音}`` —— 单字简短形（缺省 ``||``）。
    - ``{原文}`` —— 无 ruby 块（等价于纯文本）。
    - 块外字符按原样输出。

    **块边界 ``{...}`` 严格等于 morpheme 边界**：
    - 想表达「这两个字是同一个复合词、可逐字注音」→ 用一个多字块 ``{毎日||まい,にち}``；
    - 想表达「这是两个独立的字/词紧贴」→ 用两个独立块 ``{毎||まい}{日||にち}``；
    - 不可拆复合词（熟字訓・当て字）→ 多字块 + 末尾 ``,`` ``{今日||きょう,}``。

    历史 PR7 尝试把连续单字块按邻接合并为同一 morpheme，但「多个复合词紧贴」时
    （如 ``{物||もの}{語||がたり}{映||えい}{画||が}`` 其实是 物語+映画 两个 morpheme）
    按邻接合并就会错。现在严格遵守「块边界 = morpheme 边界」，由 LLM 用 ``{毎日||まい,にち}``
    多字块形态明确告知。

    Pairs 编码：
    - 多字块、所有字读音非空 → ``(原文, [r1, r2, ...])`` 数组形（下游加 morpheme_span）；
    - 多字块、含尾随空读音 → ``(原文, "拼接读音")`` 字符串形（首字承载、其余连词）；
    - 单字块 → ``(原文, 读音)`` 字符串形（独立 morpheme，无 Phase 5 保护）；
    - 块外字符 → 每字 ``(c, c)`` 自注音。

    解析失败（未闭合 ``{``、读音段数不符等）会原样吃掉异常 token 并尽量恢复；
    最终 ``raw_text`` 与原行不符时调用方应丢弃该行。
    """
    pairs: Pairs = []
    raw_chars: List[str] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] == "{":
            close = text.find("}", i)
            if close == -1:
                # 未闭合 → 当普通字符
                raw_chars.append(text[i])
                pairs.append((text[i], text[i]))
                i += 1
                continue
            content = text[i + 1 : close]
            if "||" in content:
                text_part, readings_part = content.split("||", 1)
                if not text_part:
                    # 空原文 → 跳过
                    i = close + 1
                    continue
                per_char_raw = readings_part.split(",")
                # 字内 mora 分隔 "|" → 拼成单串（mora 边界下游处理）
                per_char_clean = [
                    "".join(seg for seg in r.split("|") if seg) for r in per_char_raw
                ]
                raw_chars.extend(text_part)
                # 单字块统一字符串形：array/string 在下游等价但 string 更直观。
                if len(text_part) == 1:
                    pairs.append(
                        (text_part, per_char_clean[0] if per_char_clean else text_part)
                    )
                elif len(per_char_clean) == len(text_part) and all(per_char_clean):
                    # 多字干净逐字 → 数组形（保留 morpheme_span 给 Phase 5 保护）
                    pairs.append((text_part, list(per_char_clean)))
                else:
                    # 含尾随空 / 段数不符 → 拼成字符串走整块分配
                    pairs.append((text_part, "".join(per_char_clean) or text_part))
            elif "|" in content:
                # 兼容短形：{原文|读音}
                text_part, _, reading_part = content.partition("|")
                if not text_part:
                    i = close + 1
                    continue
                reading = "".join(seg for seg in reading_part.split("|") if seg)
                raw_chars.extend(text_part)
                pairs.append((text_part, reading or text_part))
            else:
                # {原文} 无读音 → 每字自注音
                for c in content:
                    raw_chars.append(c)
                    pairs.append((c, c))
            i = close + 1
        else:
            raw_chars.append(text[i])
            pairs.append((text[i], text[i]))
            i += 1
    return pairs, "".join(raw_chars)


def _is_kanji_char(ch: str) -> bool:
    """是否为漢字（与 ruby_analyzer.RubyAnalyzer._is_kanji 同一码区，含々）。"""
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or code == 0x3005  # 々
    )


def _has_unread_kanji(pairs: Pairs) -> bool:
    """是否存在「含漢字却没拿到真实读音」的 pair（漏包 / 空读音块 / 自注音）。

    LLM 偶尔会漏把某个漢字包进 ``{...}``，或给空读音块 ``{漢字}``。这类字经
    :func:`_annotated_to_pairs` 会落成 ``(漢字, 漢字)`` 或 ``(漢字, "")`` —— 剥标后
    字符不增减，``raw_text == line`` 仍成立，于是旧逻辑静默接受、该字最终无注音。

    本函数把「漢字 surface 的拼接读音等于 surface 本身或为空」判定为未注音，
    供 :func:`_parse_payload` 在按行校验时连同字符完整性一起把关：未达标的行不
    进 mapping，交由 :meth:`LLMRubyClient.annotate_lines` 的重试轮再给 LLM 一次，
    多轮仍缺才回退本地引擎。
    """
    for surface, reading in pairs:
        if not any(_is_kanji_char(c) for c in surface):
            continue
        joined = "".join(reading) if isinstance(reading, list) else reading
        if not joined or joined == surface:
            return True
    return False


def _legacy_tokens_to_pairs(tokens: list, line: str) -> Optional[Pairs]:
    """旧 ``tokens: [{s, r}]`` 格式 → Pairs。返回 None 表示 surface 拼接 ≠ 原行。"""
    pairs: Pairs = []
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        s = tok.get("s", "")
        r = tok.get("r", "")
        if not isinstance(s, str) or not s:
            continue
        reading: Reading
        if isinstance(r, list):
            if len(r) == len(s) and all(isinstance(x, str) for x in r):
                reading = list(r)
            else:
                joined = "".join(x for x in r if isinstance(x, str))
                reading = joined or s
        elif isinstance(r, str):
            reading = r or s
        else:
            continue
        pairs.append((s, reading))
    if "".join(s for s, _ in pairs) != line:
        return None
    return pairs


def _parse_payload(text: str, lines: List[str]) -> Dict[int, Pairs]:
    """把模型输出解析为 ``{line_idx: pairs}``。

    支持两种 line 内容字段（优先 ``text``，缺失时回退 ``tokens``）：

    1. ``text`` —— 项目 inline annotated 形式（推荐）。例：
       ``{今日||きょう,}は{毎日||まい,にち}``。
       校验：剥离 ``{...}`` 标注后等于原行；否则该行丢弃，由调用方回退本地引擎。

    2. ``tokens`` —— 旧分词形式（兼容历史 LLM 客户端）。
       ``[{"s":"今日","r":"きょう"},...]``，r 可为字符串或与 s 等长的字符串数组。
       校验：所有 s 拼接等于原行；否则丢弃。
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
        line = lines[idx]
        # 优先 annotated text 形式
        text_field = entry.get("text")
        if isinstance(text_field, str) and text_field:
            pairs, raw_text = _annotated_to_pairs(text_field)
            # 校验两件事：① 剥标后字符与原行一致；② 没有漏注音的漢字。
            # 任一不满足 → 不进 mapping，该行成为重试轮的 pending 行。
            if raw_text == line and not _has_unread_kanji(pairs):
                mapping[idx] = pairs
            continue
        # 回退旧 tokens 形式
        tokens = entry.get("tokens")
        if isinstance(tokens, list):
            pairs_opt = _legacy_tokens_to_pairs(tokens, line)
            if pairs_opt is not None and not _has_unread_kanji(pairs_opt):
                mapping[idx] = pairs_opt
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

    def set_progress_callback(self, cb: Optional[Callable[[str], None]]) -> None:
        """注册进度回调，转发到 HTTP 客户端（请求/解析/重试/轮次等阶段文本）。"""
        self._client.set_progress_callback(cb)

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

        三条路径：
        1. 片假名外来语 + 英文读音（且开关开启）→ 整词作为单块 RubyResult
           （保留英文读音，下游 ``AutoCheckService`` 识别为 ``katakana_english`` 来源，
           首字承载英文、整词连词）。
        2. reading 是数组且每元素非空 → LLM 已自分到字，直接逐字 emit RubyResult
           （绕过本地 ``_distribute_morpheme_reading`` 与 ``_split_by_kanji_dict`` 启发式），
           同时为多字 surface 标 ``morpheme_span`` 让 Phase 5 享有连词组保护。
        3. 其余（reading 为字符串、或数组含空元素表示连词）→ 拼成字符串复用
           基类 :meth:`_results_from_pairs` 的逐字/逐块假名分配。
        """
        results: List[RubyResult] = []
        pos = 0
        for surface, reading in pairs:
            start = pos
            end = pos + len(surface)
            mspan: Optional[Tuple[int, int]] = (
                (start, end) if end - start > 1 else None
            )

            # 路径 2：LLM 直接给出每字读音（无空元素表示连词）
            if (
                isinstance(reading, list)
                and len(reading) == len(surface)
                and all(r for r in reading)
            ):
                for i, ch in enumerate(surface):
                    results.append(
                        RubyResult(
                            text=ch, reading=reading[i],
                            start_idx=start + i, end_idx=start + i + 1,
                            morpheme_span=mspan,
                        )
                    )
                pos = end
                continue

            # 路径 2 退化：数组中含空元素 → 拼成字符串走路径 3 的整块分配
            # （空元素自然不贡献字符，等价于「前字承载、后字连词」语义）。
            reading_str: str = (
                "".join(reading) if isinstance(reading, list) else reading
            )

            if (
                self._annotate_katakana_with_english
                and is_all_katakana(surface)
                and is_english_reading(reading_str)
            ):
                # 路径 1：片假名外来语英文标注
                results.append(
                    RubyResult(
                        text=surface, reading=reading_str.strip(),
                        start_idx=start, end_idx=end,
                        morpheme_span=mspan,
                    )
                )
            else:
                # 路径 3：复用基类逐 pair 分配，再平移索引到本行内的绝对位置
                for r in self._results_from_pairs([(surface, reading_str)]):
                    results.append(
                        RubyResult(
                            text=r.text, reading=r.reading,
                            start_idx=r.start_idx + start,
                            end_idx=r.end_idx + start,
                            morpheme_span=(
                                (r.morpheme_span[0] + start, r.morpheme_span[1] + start)
                                if r.morpheme_span is not None else mspan
                            ),
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
            return "".join(
                "".join(r) if isinstance(r, list) else r for _, r in pairs
            )
        return self._fallback.get_reading(text)
