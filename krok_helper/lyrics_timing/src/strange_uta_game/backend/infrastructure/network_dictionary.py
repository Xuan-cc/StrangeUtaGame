"""网络读音词典 — 数据模型 + HTTP 拉取 + 文件导入。

设计要点
--------
* 网络词典与本地词典分文件存储：本地 ``dictionary.json``、网络
  ``network_dictionary.json``。
* 网络词典是多源容器：每个源含 ``id`` / ``name`` / ``url`` / ``enabled`` /
  ``builtin`` / ``last_fetched`` / ``entries``，源粒度可启用/禁用。
* 源协议：与 RhythmicaLyrics 服务端 ``kakuteiyominet.php?req=get`` 兼容 —
  返回 ``[success]`` + 体 ``word\\treadings\\n``（LF 用 ``$0A`` 真字节）。
* 字典源优先级模型：两层。
  - 全局 ``source_order``：``["local", "<src_id_1>", "<src_id_2>", ...]``。
  - 源内 entries 顺序：自顶向下。
* lookup 行为：自顶向下首个命中（保留 ``analyze_sentence`` 现有子串匹配语义）。
* 联网 fetch 默认关闭，仅在 UI 触发"刷新"按钮时执行。

Public API
----------
* :data:`BUILTIN_SOURCES` — 不可删除的内置预设（含 RL 官方）。
* :data:`DEFAULT_NETWORK_DICTIONARY` — 网络词典文件首次创建时的默认体。
* :func:`fetch_source_entries` — HTTP 拉取单个源 → annotated entries。
* :func:`import_file_to_entries` — 本地文件 → annotated entries（复用
  ``parse_rl_dictionary`` 多格式解析）。
* :func:`flatten_effective_dictionary` — 给定本地条目 + 网络词典文档 →
  按 ``source_order`` 拼接后的全局 entries（供 ``analyze_sentence`` 消费）。
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
    parse_rl_dictionary,
    read_rl_dictionary_file,
)


# ──────────────────────────────────────────────
# 内置预设 & 默认文档
# ──────────────────────────────────────────────


BUILTIN_SOURCES: List[Dict[str, Any]] = [
    {
        "id": "rl_official",
        "name": "RhythmicaLyrics 官方",
        "url": "http://timetag.main.jp/RhythmicaLyrics/kakuteiyominet.php",
        "builtin": True,
        "enabled": True,
    }
]


# 默认 *设置* 文档（meta 部分 —— 与 ``config.json["network_dictionary"]`` 同形）
DEFAULT_NETWORK_DICTIONARY_META: Dict[str, Any] = {
    "enabled": True,
    "source_order": ["local"] + [s["id"] for s in BUILTIN_SOURCES],
    "sources": [dict(s) for s in BUILTIN_SOURCES],
}

# 旧字段 - 兼容外部调用方
DEFAULT_NETWORK_DICTIONARY: Dict[str, Any] = {
    "enabled": True,
    "source_order": list(DEFAULT_NETWORK_DICTIONARY_META["source_order"]),
    "sources": [
        {**s, "last_fetched": None, "entries": []}
        for s in BUILTIN_SOURCES
    ],
}


_LOCAL_SOURCE_ID = "local"


# ──────────────────────────────────────────────
# 网络拉取
# ──────────────────────────────────────────────


def _build_ssl_context(insecure: bool = False) -> ssl.SSLContext:
    """构造 HTTPS 验证上下文：优先使用 ``certifi`` 根证书包；失败时回退系统默认。

    Windows / PyInstaller 打包环境下 Python ssl 模块的根证书路径常出问题，
    导致 ``CERTIFICATE_VERIFY_FAILED``；显式喂 ``certifi.where()`` 可消除该症。
    """
    if insecure:
        ctx = ssl._create_unverified_context()
        return ctx
    try:
        import certifi  # type: ignore
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def fetch_source_entries(
    url: str,
    timeout: float = 8.0,
    allow_insecure_fallback: bool = True,
) -> List[Dict[str, Any]]:
    """HTTP 拉取一个 RL 兼容的网络源 → annotated entries。

    协议（来自 RhythmicaLyrics 源码 ``routin_func.hsp:6876``）：
    ``GET <url>?req=get&dummy=<unix_ms>`` → ``[success]\\n<word>\\t<readings>\\n...``
    （字节体里 ``\\n`` 是 LF $0A，UTF-8 编码）。

    HTTPS 路径默认用 ``certifi`` 根证书包验证；若验证失败且
    ``allow_insecure_fallback=True`` 则改用未验证上下文重试一次（Windows 上
    系统证书链缺失的常见兜底，仅用于公开词典数据这类无敏感性场景）。

    Args:
        url: 服务端 PHP 端点 URL。
        timeout: 连接 + 读超时（秒）。
        allow_insecure_fallback: 默认 True；遇到证书验证错误时尝试无验证重试。

    Returns:
        annotated 条目列表（同 :func:`parse_rl_dictionary` 输出格式）。

    Raises:
        urllib.error.URLError: 网络层错误（DNS 失败、连接拒绝、超时等）。
        ssl.SSLError: 在 ``allow_insecure_fallback=False`` 时把 SSL 错误透传。
        ValueError: 响应体不以 ``[success]`` 开头。
    """
    full_url = f"{url}?req=get&dummy={int(time.time() * 1000)}"
    req = urllib.request.Request(
        full_url,
        headers={"User-Agent": "StrangeUtaGame/1.0 (+rl-compat)"},
    )

    def _do_request(insecure: bool) -> bytes:
        ctx = _build_ssl_context(insecure=insecure) if url.lower().startswith("https") else None
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()

    try:
        raw = _do_request(insecure=False)
    except urllib.error.URLError as e:
        # 仅在 SSL 证书验证失败 / 系统证书链缺失时尝试无验证重试
        reason = getattr(e, "reason", e)
        is_cert_err = isinstance(reason, ssl.SSLCertVerificationError) or (
            isinstance(reason, ssl.SSLError) and "CERTIFICATE_VERIFY_FAILED" in str(reason)
        )
        if not (is_cert_err and allow_insecure_fallback):
            raise
        raw = _do_request(insecure=True)

    body = raw.decode("utf-8", errors="replace")
    if not body.startswith("[success]"):
        raise ValueError(f"网络词典响应非 [success] 开头：{body[:64]!r}")
    text = body[len("[success]"):]
    return parse_rl_dictionary(text)


# ──────────────────────────────────────────────
# 文件导入
# ──────────────────────────────────────────────


def import_file_to_entries(path: str) -> List[Dict[str, Any]]:
    """本地文件 → annotated entries（utf-8 / cp932 自动识别）。

    支持 RL 多种字典文本格式：tab 行、成对行、HSP 字面体、INI 段。
    """
    text = read_rl_dictionary_file(path)
    return parse_rl_dictionary(text)


# ──────────────────────────────────────────────
# 全局扁平化
# ──────────────────────────────────────────────


def _source_index(doc: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {s.get("id", ""): s for s in doc.get("sources", []) if s.get("id")}


def flatten_effective_dictionary(
    local_entries: List[Dict[str, Any]],
    net_doc: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """把本地 + 启用的网络源按 ``source_order`` 拼接为单一全局 entries 列表。

    规则：
    * 网络总开关 ``enabled=False`` → 仅返回 ``local_entries`` 的副本。
    * 否则按 ``source_order`` 顺序遍历每个 id：
      - ``"local"`` → 注入 ``local_entries``。
      - 其他 id → 查 ``sources`` 表，若源 ``enabled=True`` 则注入其 entries。
    * 未出现在 ``source_order`` 中的源被忽略（用户显式排除）。
    * 任何不在源表中的 id（含 ``"local"`` 重复）静默跳过。

    Args:
        local_entries: ``dictionary.json`` 内容。
        net_doc: ``network_dictionary.json`` 内容（含 ``enabled``/
            ``sources``/``source_order``）。

    Returns:
        新的拼接列表；调用方可像旧 ``load_dictionary()`` 结果一样消费。
    """
    if not isinstance(net_doc, dict):
        return list(local_entries)

    if not net_doc.get("enabled", False):
        return list(local_entries)

    order: List[str] = net_doc.get("source_order") or [_LOCAL_SOURCE_ID]
    sources = _source_index(net_doc)

    out: List[Dict[str, Any]] = []
    seen_local = False
    for src_id in order:
        if src_id == _LOCAL_SOURCE_ID:
            if seen_local:
                continue
            seen_local = True
            out.extend(local_entries)
            continue
        src = sources.get(src_id)
        if not src or not src.get("enabled", True):
            continue
        out.extend(src.get("entries", []) or [])
    # 若 source_order 中未列 "local"，本地默认置顶（防止用户误删 sentinel 后失去本地词典）
    if not seen_local:
        out = list(local_entries) + out
    return out


def ensure_builtin_sources(doc: Dict[str, Any]) -> Dict[str, Any]:
    """确保 ``doc`` 含全部 :data:`BUILTIN_SOURCES`（按 id 检测），缺失则补齐。

    用于打开旧文档时补丁式向前兼容；不修改用户已有源；对内置源以
    :data:`BUILTIN_SOURCES` 的 ``name`` / ``url`` 强制刷新，使代码内更新可生效。
    """
    if not isinstance(doc, dict):
        doc = json.loads(json.dumps(DEFAULT_NETWORK_DICTIONARY))
    sources = doc.setdefault("sources", [])
    by_id = {s.get("id"): s for s in sources if isinstance(s, dict)}
    for b in BUILTIN_SOURCES:
        if b["id"] in by_id:
            existing = by_id[b["id"]]
            existing["builtin"] = True
            existing.setdefault("enabled", True)
            existing.setdefault("entries", [])
            existing.setdefault("last_fetched", None)
            # 内置源 name 默认与 BUILTIN_SOURCES 保持一致（用户改过的不动）
            existing.setdefault("name", b["name"])
            existing.setdefault("url", b["url"])
        else:
            new = dict(b)
            new.setdefault("entries", [])
            new.setdefault("last_fetched", None)
            sources.append(new)
    order = doc.setdefault("source_order", [_LOCAL_SOURCE_ID])
    if _LOCAL_SOURCE_ID not in order:
        order.insert(0, _LOCAL_SOURCE_ID)
    for b in BUILTIN_SOURCES:
        if b["id"] not in order:
            order.append(b["id"])
    doc.setdefault("enabled", True)
    return doc


def split_meta_and_cache(doc: Dict[str, Any]) -> "Tuple[Dict[str, Any], Dict[str, Dict[str, Any]]]":
    """把统一 doc 拆为 (meta_for_config_json, cache_for_network_dictionary_json)。

    meta：与 ``config.json["network_dictionary"]`` 同形（不含 entries / last_fetched）。
    cache：``{src_id: {entries: [...], last_fetched: <ts>}}``，仅含 entries 数据，
    便于用户判断"拉取后存到哪儿"。
    """
    meta: Dict[str, Any] = {
        "enabled": bool(doc.get("enabled", True)),
        "source_order": list(doc.get("source_order") or []),
        "sources": [],
    }
    cache: Dict[str, Dict[str, Any]] = {}
    for s in doc.get("sources", []) or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        meta["sources"].append({
            "id": sid,
            "name": s.get("name", sid),
            "url": s.get("url", ""),
            "builtin": bool(s.get("builtin", False)),
            "enabled": bool(s.get("enabled", True)),
        })
        cache[sid] = {
            "entries": list(s.get("entries", []) or []),
            "last_fetched": s.get("last_fetched"),
        }
    return meta, cache


def merge_meta_and_cache(
    meta: Dict[str, Any], cache: Dict[str, Dict[str, Any]]
) -> Dict[str, Any]:
    """把 (meta, cache) 合成统一 doc（UI / lookup 消费形态）。

    meta 中每个 source 项 + cache 中对应 id 的 entries / last_fetched 拼接。
    cache 中存在 / meta 缺失的 source id 被忽略（防止僵尸条目）。
    """
    if not isinstance(meta, dict):
        meta = json.loads(json.dumps(DEFAULT_NETWORK_DICTIONARY_META))
    sources: List[Dict[str, Any]] = []
    for s in meta.get("sources", []) or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        cached = (cache or {}).get(sid, {})
        sources.append({
            "id": sid,
            "name": s.get("name", sid),
            "url": s.get("url", ""),
            "builtin": bool(s.get("builtin", False)),
            "enabled": bool(s.get("enabled", True)),
            "entries": list(cached.get("entries", []) or []),
            "last_fetched": cached.get("last_fetched"),
        })
    return {
        "enabled": bool(meta.get("enabled", True)),
        "source_order": list(meta.get("source_order") or [_LOCAL_SOURCE_ID]),
        "sources": sources,
    }


_INTERVAL_UNIT_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}


def interval_to_seconds(value: int, unit: str) -> int:
    """把 (value, unit) 转换为秒数；非法 unit 退回到 ``week``。"""
    base = _INTERVAL_UNIT_SECONDS.get(unit, _INTERVAL_UNIT_SECONDS["week"])
    try:
        v = max(1, int(value))
    except (TypeError, ValueError):
        v = 1
    return v * base


def is_auto_update_due(last_at: float, interval_value: int, interval_unit: str) -> bool:
    """根据上次自动同步时间戳判断是否到期。``last_at <= 0`` 视为从未同步 → 直接到期。"""
    if not last_at or last_at <= 0:
        return True
    return (time.time() - float(last_at)) >= interval_to_seconds(interval_value, interval_unit)


def auto_update_enabled_sources(
    doc: Dict[str, Any],
    *,
    timeout: float = 8.0,
) -> "Tuple[List[str], List[str]]":
    """遍历 ``doc`` 中所有启用且 URL 非空的源并 HTTP 拉取，原地写回 entries / last_fetched。

    Args:
        doc: 统一形态的网络词典文档（含 ``sources``）。
        timeout: 单源拉取超时。

    Returns:
        ``(ok_msgs, fail_msgs)``：成功 / 失败的人类可读消息列表，供日志或 UI 显示。
    """
    ok_msgs: List[str] = []
    fail_msgs: List[str] = []
    for src in doc.get("sources") or []:
        if not src.get("enabled"):
            continue
        url = (src.get("url") or "").strip()
        if not url:
            continue
        try:
            entries = fetch_source_entries(url, timeout=timeout)
            src["entries"] = entries
            src["last_fetched"] = int(time.time())
            ok_msgs.append(f"{src.get('name', src.get('id', '?'))}: {len(entries)} 条")
        except Exception as e:
            fail_msgs.append(f"{src.get('name', src.get('id', '?'))}: {e}")
    return ok_msgs, fail_msgs


__all__ = [
    "BUILTIN_SOURCES",
    "DEFAULT_NETWORK_DICTIONARY",
    "DEFAULT_NETWORK_DICTIONARY_META",
    "fetch_source_entries",
    "import_file_to_entries",
    "flatten_effective_dictionary",
    "ensure_builtin_sources",
    "split_meta_and_cache",
    "merge_meta_and_cache",
    "interval_to_seconds",
    "is_auto_update_due",
    "auto_update_enabled_sources",
]
