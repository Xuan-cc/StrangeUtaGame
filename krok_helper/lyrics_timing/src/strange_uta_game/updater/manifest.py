"""GitHub Release "latest" 抽象。

提供 :func:`fetch_latest_release`：依次尝试三个源的 API（受 ``UpdaterSettings``
排序与代理影响），把 GitHub Release JSON 收敛为 :class:`LatestRelease` 数据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import http_client
from .sources import SourceId, build_api_urls, build_api_list_urls, build_download_url
from .version import strip_tag_prefix, is_newer_version


@dataclass(frozen=True)
class ReleaseAsset:
    """Release 下的单个文件资产。"""
    name: str
    size: int
    download_url: str

    @property
    def extension(self) -> str:
        name = self.name.lower()
        for ext in (".zip", ".rar", ".7z", ".tar.gz", ".tgz", ".sha256"):
            if name.endswith(ext):
                return ext
        # 兜底
        idx = name.rfind(".")
        return name[idx:] if idx >= 0 else ""


@dataclass(frozen=True)
class LatestRelease:
    """聚合的 latest release 信息。"""
    tag: str
    version: str          # tag 去掉 SUGv 前缀后的纯版本号
    name: str             # release 标题（可能为空，回落到 tag）
    body: str             # changelog 正文（markdown）
    html_url: str         # Release 页面 URL
    prerelease: bool
    published_at: str
    assets: List[ReleaseAsset] = field(default_factory=list)

    # ── 资产挑选 ──────────────────────────────────────────────

    def pick_primary_asset(self, preferred_name: Optional[str] = None) -> Optional[ReleaseAsset]:
        """挑选用于安装的主资产。

        当 ``preferred_name`` 被指定时（变体感知场景），**精确匹配或返回 None**：
        - 找到 → 返回对应资产；
        - 找不到 → 返回 ``None``（调用方据此判定"此变体的资产尚未上传"）。
        这样可防止 noWinIME 变体在找不到自己的 zip 时误下载主版本 zip。

        当 ``preferred_name`` 未指定时，使用回退顺序：
        1. 任何 ``.zip``；
        2. 任何 ``.rar`` —— 兼容旧版发布；
        3. 任何 ``.7z``；
        4. 第一个非 ``.sha256`` 资产。
        """
        if not self.assets:
            return None
        if preferred_name:
            for a in self.assets:
                if a.name == preferred_name:
                    return a
            # preferred 明确但未找到 → 不回退，返回 None 以防变体混装
            return None
        for ext in (".zip", ".rar", ".7z"):
            for a in self.assets:
                if a.name.lower().endswith(ext):
                    return a
        for a in self.assets:
            if not a.name.lower().endswith(".sha256"):
                return a
        return None

    def pick_sha256_asset(self, primary_name: str) -> Optional[ReleaseAsset]:
        """挑选与 ``primary_name`` 配对的 ``.sha256`` 文件（可选）。"""
        target = f"{primary_name}.sha256"
        for a in self.assets:
            if a.name == target:
                return a
        return None


# ───────────────────────── 解析 ─────────────────────────


def _parse_release_json(payload: Dict[str, Any]) -> LatestRelease:
    """把 GitHub Release JSON 解析为 :class:`LatestRelease`。"""
    tag = str(payload.get("tag_name") or "")
    assets_raw = payload.get("assets") or []
    assets: List[ReleaseAsset] = []
    for a in assets_raw:
        if not isinstance(a, dict):
            continue
        name = str(a.get("name") or "")
        if not name:
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                size=int(a.get("size") or 0),
                download_url=str(a.get("browser_download_url") or ""),
            )
        )
    return LatestRelease(
        tag=tag,
        version=strip_tag_prefix(tag),
        name=str(payload.get("name") or "") or tag,
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        prerelease=bool(payload.get("prerelease") or False),
        published_at=str(payload.get("published_at") or ""),
        assets=assets,
    )


# ───────────────────────── 主入口 ─────────────────────────


def fetch_latest_release(
    source_order: List[str],
    proxies: Optional[Dict[str, str]] = None,
    include_prerelease: bool = False,
) -> Tuple[Optional[LatestRelease], List[Tuple[SourceId, str, str]]]:
    """按 ``source_order`` 顺序请求 release API，返回首个成功的结果。

    Args:
        source_order: 用户配置的源排序。
        proxies: ``requests`` 风格代理 dict（可为 ``None``）。
        include_prerelease: 当前未启用预发布通道；保留参数以便未来扩展。

    Returns:
        ``(release, attempts)``：

        * ``release`` 为成功获取的 :class:`LatestRelease`，全部失败则为 ``None``；
        * ``attempts`` 是 ``(source_id, url, error)`` 序列，供调用方记录日志。
    """
    attempts: List[Tuple[SourceId, str, str]] = []
    candidates = build_api_urls(source_order)
    for source_id, url in candidates:
        result = http_client.get_json(url, proxies=proxies)
        if not result.ok or not isinstance(result.body, dict):
            attempts.append((source_id, url, result.error or "未知错误"))
            continue
        try:
            release = _parse_release_json(result.body)  # type: ignore[arg-type]
        except Exception as e:
            attempts.append((source_id, url, f"解析失败: {e}"))
            continue
        # release.html_url 走的是 github.com，没问题；但是
        # 资产的 download_url 也来自 GitHub，可能需要替换为镜像。
        # 我们暂不在这里改写；让调用方使用 :func:`override_assets_with_source`
        # 决定是否替换。
        if not release.tag:
            attempts.append((source_id, url, "缺少 tag_name"))
            continue
        if release.prerelease and not include_prerelease:
            attempts.append((source_id, url, "命中预发布版本，已跳过"))
            continue
        attempts.append((source_id, url, ""))
        return release, attempts
    return None, attempts


def fetch_releases_since(
    current_version: str,
    source_order: List[str],
    proxies: Optional[Dict[str, str]] = None,
    include_prerelease: bool = False,
) -> Tuple[List["LatestRelease"], List[Tuple[SourceId, str, str]]]:
    """获取所有比 ``current_version`` 更新的 release（不含当前版本本身）。

    按源顺序尝试，第一个成功的源返回结果。结果按发布时间从新到旧排列。
    全部源失败时返回空列表。

    用途：跨版本更新时（如 1.0.0→1.0.3）聚合 1.0.1、1.0.2、1.0.3 的全部
    更新日志，让用户在弹窗中看到所有版本的变更内容。
    """
    attempts: List[Tuple[SourceId, str, str]] = []
    candidates = build_api_list_urls(source_order)
    for source_id, url in candidates:
        result = http_client.get_json(url, proxies=proxies)
        if not result.ok or not isinstance(result.body, list):
            attempts.append((source_id, url, result.error or "未知错误"))
            continue
        try:
            releases: List[LatestRelease] = []
            for item in result.body:
                if not isinstance(item, dict):
                    continue
                try:
                    rel = _parse_release_json(item)
                except Exception:
                    continue
                if not rel.tag:
                    continue
                if rel.prerelease and not include_prerelease:
                    continue
                if is_newer_version(rel.version, current_version):
                    releases.append(rel)
            attempts.append((source_id, url, ""))
            return releases, attempts
        except Exception as e:
            attempts.append((source_id, url, f"解析失败: {e}"))
            continue
    return [], attempts


def override_asset_urls(
    release: LatestRelease,
    source: SourceId,
    primary_asset_name: Optional[str] = None,
) -> LatestRelease:
    """把 release 的资产下载 URL 替换为指定源的 URL。

    GitHub Release JSON 里的 ``browser_download_url`` 永远是 github.com 的，但
    用户走 ``ghproxy`` / ``gh-proxy`` 等反代下载时需要把 URL 改写。
    """
    new_assets: List[ReleaseAsset] = []
    for a in release.assets:
        if primary_asset_name and a.name != primary_asset_name:
            # 非主资产保持原样
            new_assets.append(a)
            continue
        new_url = build_download_url(source, release.tag, a.name)
        new_assets.append(ReleaseAsset(name=a.name, size=a.size, download_url=new_url))
    return LatestRelease(
        tag=release.tag,
        version=release.version,
        name=release.name,
        body=release.body,
        html_url=release.html_url,
        prerelease=release.prerelease,
        published_at=release.published_at,
        assets=new_assets,
    )
