"""``strange_uta_game.updater.sources`` 单元测试。"""

import pytest

from strange_uta_game.__version__ import REPO_NAME, REPO_OWNER
from strange_uta_game.updater.sources import (
    DEFAULT_ORDER,
    GH_PROXY_PREFIXES,
    SOURCE_IDS,
    SOURCE_LABELS,
    build_api_urls,
    build_api_list_urls,
    build_download_url,
    build_release_urls,
    normalize_order,
)


class TestNormalizeOrder:
    def test_empty_returns_default(self):
        assert normalize_order([]) == list(DEFAULT_ORDER)

    def test_keeps_user_order(self):
        # 用户把 gh-proxy 提前，其余按默认顺序补齐
        assert normalize_order(["gh-proxy", "github"]) == [
            "gh-proxy",
            "github",
        ]

    def test_drops_unknown(self):
        # 未知 id（含已下架的 ghproxy / ghproxy-net 旧源）被丢弃，
        # 缺失项按默认顺序补齐 —— 老用户 config.json 的迁移路径
        assert normalize_order(
            ["bad", "github", "x", "ghproxy", "ghproxy-net"]
        ) == list(DEFAULT_ORDER)

    def test_deduplicates(self):
        assert normalize_order(["github", "github", "gh-proxy"]) == list(DEFAULT_ORDER)


class TestBuildDownloadUrl:
    def test_github_direct(self):
        url = build_download_url("github", "SUGv0.3.2", "StrangeUtaGame-v0.3.2.zip")
        assert url == (
            f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
            f"/releases/download/SUGv0.3.2/StrangeUtaGame-v0.3.2.zip"
        )

    def test_gh_proxy_uses_first_node(self):
        # 单 URL 构造走首节点；多节点接力用 build_release_urls
        url = build_download_url("gh-proxy", "SUGv0.3.2", "F.zip")
        assert url.startswith(f"{GH_PROXY_PREFIXES[0]}/https://github.com/")
        assert url.endswith("/SUGv0.3.2/F.zip")

    def test_unknown_source_raises(self):
        with pytest.raises(ValueError):
            build_download_url("rofl", "SUGv0.3.2", "F.zip")  # type: ignore[arg-type]


class TestBuildReleaseUrls:
    def test_default_order_expands_nodes(self):
        urls = build_release_urls(list(DEFAULT_ORDER), "SUGv1", "X.zip")
        # github 1 条 + gh-proxy 每节点 1 条
        assert [sid for sid, _ in urls] == ["github"] + ["gh-proxy"] * len(
            GH_PROXY_PREFIXES
        )

    def test_user_order(self):
        urls = build_release_urls(["gh-proxy", "github"], "SUGv1", "X.zip")
        assert [sid for sid, _ in urls] == ["gh-proxy"] * len(
            GH_PROXY_PREFIXES
        ) + ["github"]

    def test_gh_proxy_node_rotation_order(self):
        urls = build_release_urls(["gh-proxy"], "T", "F.zip")
        # normalize_order 会把缺失的 github 补到末尾，只看 gh-proxy 部分
        gh_urls = [url for sid, url in urls if sid == "gh-proxy"]
        assert gh_urls == [
            f"{prefix}/https://github.com/{REPO_OWNER}/{REPO_NAME}"
            f"/releases/download/T/F.zip"
            for prefix in GH_PROXY_PREFIXES
        ]

    def test_url_content(self):
        urls = build_release_urls(list(SOURCE_IDS), "T", "F.zip")
        for sid, url in urls:
            assert "T" in url and "F.zip" in url


class TestBuildApiUrls:
    def test_all_sources(self):
        api = build_api_urls(list(SOURCE_IDS))
        assert len(api) == 1 + len(GH_PROXY_PREFIXES)
        # GitHub 官方
        assert api[0][1].startswith("https://api.github.com/repos/")
        # gh-proxy 每个节点都包装 api.github.com
        for _sid, url in api[1:]:
            assert "/https://api.github.com/repos/" in url
        # 已下架的旧源域名不再出现
        for _sid, url in api:
            assert "ghfast.top" not in url
            assert "ghproxy.net" not in url

    def test_list_urls_same_shape(self):
        api = build_api_list_urls(["gh-proxy"], per_page=5)
        # normalize_order 补齐 github 后 gh-proxy 部分仍按节点展开
        gh_entries = [(sid, url) for sid, url in api if sid == "gh-proxy"]
        assert [sid for sid, _ in gh_entries] == ["gh-proxy"] * len(
            GH_PROXY_PREFIXES
        )
        for _sid, url in gh_entries:
            assert "/https://api.github.com/repos/" in url
            assert "per_page=5" in url


class TestSourceLabels:
    def test_all_have_labels(self):
        for sid in SOURCE_IDS:
            assert sid in SOURCE_LABELS
            assert SOURCE_LABELS[sid]  # 非空
