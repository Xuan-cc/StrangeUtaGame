"""更新源 URL 模板。

提供下载源：

* ``github``    —— 官方 GitHub Release 直链
* ``gh-proxy``  —— gh-proxy 反代族（单源多节点，见 :data:`GH_PROXY_PREFIXES`）

gh-proxy 是同一服务的多个 CDN 入口（官网 https://gh-proxy.com 首页 REGIONS
节点表）。API 检测与资产下载都按 :data:`GH_PROXY_PREFIXES` 顺序在节点间接力，
单个节点故障自动换下一个，源 id 始终是 ``gh-proxy``。

已移除的源（2026-08 实测）：

* ``ghproxy``（ghfast.top）—— 已禁止包装 ``api.github.com``（HTTP 403
  "Invalid input."），仅剩文件下载能力，作检测源必败；
* ``ghproxy-net``（ghproxy.net）—— 证书过期，API 与下载均不可用。

URL 构造统一通过 :func:`build_release_urls`，避免散落字符串拼接。
"""

from __future__ import annotations

from typing import Dict, List, Literal, Tuple

from ..__version__ import REPO_NAME, REPO_OWNER

SourceId = Literal["github", "gh-proxy"]
SOURCE_IDS: Tuple[SourceId, ...] = ("github", "gh-proxy")

# gh-proxy 反代族节点前缀（按尝试顺序）。
# gh-proxy.com 为历史主域（仍可用）；其余为官网分发的正式节点
# （Cloudflare ×3 / Fastly / AxisNow），全部实测支持 API 包装与下载直链。
GH_PROXY_PREFIXES: Tuple[str, ...] = (
    "https://gh-proxy.com",
    "https://gh-proxy.org",
    "https://cdn.gh-proxy.org",
    "https://v4.gh-proxy.org",
    "https://v6.gh-proxy.org",
    "https://axisnow.gh-proxy.org",
)

# 人类可读的标签，供 UI 显示。
SOURCE_LABELS: Dict[SourceId, str] = {
    "github": "GitHub Release（官方）",
    "gh-proxy": "GitHub Proxy（gh-proxy 多节点）",
}

# 默认顺序（用户可在 UI 中拖动调整）。
DEFAULT_ORDER: List[SourceId] = list(SOURCE_IDS)


def normalize_order(order: List[str]) -> List[SourceId]:
    """规范化用户配置的源顺序：

    * 仅保留合法 id；
    * 去重；
    * 缺失的源按 ``DEFAULT_ORDER`` 顺序补到末尾。
    """
    seen: List[SourceId] = []
    for sid in order:
        if sid in SOURCE_IDS and sid not in seen:
            seen.append(sid)  # type: ignore[arg-type]
    for sid in DEFAULT_ORDER:
        if sid not in seen:
            seen.append(sid)
    return seen


def _release_download_path(tag: str, asset_name: str) -> str:
    """构造 ``/<owner>/<repo>/releases/download/<tag>/<file>`` 公共片段。"""
    return f"{REPO_OWNER}/{REPO_NAME}/releases/download/{tag}/{asset_name}"


def build_download_url(source: SourceId, tag: str, asset_name: str) -> str:
    """根据源 id 构造一个具体的下载 URL。

    ``gh-proxy`` 返回首节点（:data:`GH_PROXY_PREFIXES[0]`）的 URL；多节点
    接力列表请用 :func:`build_release_urls`。
    """
    path = _release_download_path(tag, asset_name)
    if source == "github":
        return f"https://github.com/{path}"
    if source == "gh-proxy":
        return f"{GH_PROXY_PREFIXES[0]}/https://github.com/{path}"
    raise ValueError(f"未知的更新源 id: {source!r}")


def build_release_urls(order: List[str], tag: str, asset_name: str) -> List[Tuple[SourceId, str]]:
    """按用户排序构造下载 URL 列表，元素为 ``(source_id, url)``。

    ``gh-proxy`` 展开为每个节点一条候选（保持节点顺序），同一源 id 出现
    多次；调用方按列表顺序接力即可。
    """
    path = _release_download_path(tag, asset_name)
    out: List[Tuple[SourceId, str]] = []
    for sid in normalize_order(order):
        if sid == "github":
            out.append((sid, f"https://github.com/{path}"))
        elif sid == "gh-proxy":
            for prefix in GH_PROXY_PREFIXES:
                out.append((sid, f"{prefix}/https://github.com/{path}"))
    return out


def build_api_urls(order: List[str]) -> List[Tuple[SourceId, str]]:
    """构造"获取 latest release"的 API URL 列表（用于检测版本）。

    GitHub 官方 API: ``https://api.github.com/repos/<owner>/<repo>/releases/latest``
    gh-proxy 节点包装 ``https://<prefix>/https://api.github.com/...``，
    每个节点一条候选。
    """
    api_path = f"repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    out: List[Tuple[SourceId, str]] = []
    for sid in normalize_order(order):
        if sid == "github":
            out.append((sid, f"https://api.github.com/{api_path}"))
        elif sid == "gh-proxy":
            for prefix in GH_PROXY_PREFIXES:
                out.append((sid, f"{prefix}/https://api.github.com/{api_path}"))
    return out


def build_api_list_urls(order: List[str], per_page: int = 30) -> List[Tuple[SourceId, str]]:
    """构造"获取 releases 列表"的 API URL 列表（用于跨版本更新日志聚合）。

    GitHub 官方 API: ``https://api.github.com/repos/<owner>/<repo>/releases?per_page=N``
    返回最多 ``per_page`` 条 release，按发布时间从新到旧排列。
    gh-proxy 节点包装方式同 :func:`build_api_urls`。
    """
    api_path = f"repos/{REPO_OWNER}/{REPO_NAME}/releases?per_page={per_page}"
    out: List[Tuple[SourceId, str]] = []
    for sid in normalize_order(order):
        if sid == "github":
            out.append((sid, f"https://api.github.com/{api_path}"))
        elif sid == "gh-proxy":
            for prefix in GH_PROXY_PREFIXES:
                out.append((sid, f"{prefix}/https://api.github.com/{api_path}"))
    return out
