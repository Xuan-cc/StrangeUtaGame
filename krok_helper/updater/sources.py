from __future__ import annotations

from typing import Literal

REPO_OWNER = "karaoke-studio"
REPO_NAME = "karaoke-studio"

SourceId = Literal["github", "ghproxy", "gh-proxy", "ghproxy-net"]
SOURCE_IDS: tuple[SourceId, ...] = ("github", "ghproxy", "gh-proxy", "ghproxy-net")
SOURCE_LABELS: dict[SourceId, str] = {
    "github": "GitHub Release（官方）",
    "ghproxy": "GitHub Proxy（mirror.ghproxy.com）",
    "gh-proxy": "GitHub Proxy（gh-proxy.com）",
    "ghproxy-net": "GitHub Proxy（ghproxy.net）",
}
DEFAULT_ORDER: list[SourceId] = list(SOURCE_IDS)


def normalize_order(order: list[str] | tuple[str, ...]) -> list[SourceId]:
    seen: list[SourceId] = []
    for value in order:
        if value in SOURCE_IDS and value not in seen:
            seen.append(value)  # type: ignore[arg-type]
    for value in DEFAULT_ORDER:
        if value not in seen:
            seen.append(value)
    return seen


def build_download_url(source: SourceId, tag: str, asset_name: str) -> str:
    path = f"{REPO_OWNER}/{REPO_NAME}/releases/download/{tag}/{asset_name}"
    if source == "github":
        return f"https://github.com/{path}"
    if source == "ghproxy":
        return f"https://mirror.ghproxy.com/https://github.com/{path}"
    if source == "gh-proxy":
        return f"https://gh-proxy.com/https://github.com/{path}"
    if source == "ghproxy-net":
        return f"https://ghproxy.net/https://github.com/{path}"
    raise ValueError(f"未知的更新源 id: {source!r}")


def build_release_urls(order: list[str] | tuple[str, ...], tag: str, asset_name: str) -> list[tuple[SourceId, str]]:
    return [(source, build_download_url(source, tag, asset_name)) for source in normalize_order(order)]


def build_api_urls(order: list[str] | tuple[str, ...]) -> list[tuple[SourceId, str]]:
    api_path = f"repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"
    urls: list[tuple[SourceId, str]] = []
    for source in normalize_order(order):
        if source == "github":
            urls.append((source, f"https://api.github.com/{api_path}"))
        elif source == "ghproxy":
            urls.append((source, f"https://mirror.ghproxy.com/https://api.github.com/{api_path}"))
        elif source == "gh-proxy":
            urls.append((source, f"https://gh-proxy.com/https://api.github.com/{api_path}"))
        elif source == "ghproxy-net":
            urls.append((source, f"https://ghproxy.net/https://api.github.com/{api_path}"))
    return urls
