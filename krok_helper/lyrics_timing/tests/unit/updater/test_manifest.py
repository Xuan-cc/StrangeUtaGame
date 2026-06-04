"""``strange_uta_game.updater.manifest`` 单元测试（纯解析，不发请求）。"""

import pytest

from strange_uta_game.updater.manifest import (
    LatestRelease,
    ReleaseAsset,
    _parse_release_json,
    override_asset_urls,
)


def _fake_payload():
    return {
        "tag_name": "SUGv0.3.3",
        "name": "v0.3.3",
        "body": "## What's new\n- A\n- B",
        "html_url": "https://github.com/Xuan-cc/StrangeUtaGame/releases/tag/SUGv0.3.3",
        "prerelease": False,
        "published_at": "2026-05-16T12:00:00Z",
        "assets": [
            {
                "name": "StrangeUtaGame-v0.3.3.zip",
                "size": 12345,
                "browser_download_url": "https://github.com/x/y/releases/download/SUGv0.3.3/StrangeUtaGame-v0.3.3.zip",
            },
            {
                "name": "StrangeUtaGame-v0.3.3.zip.sha256",
                "size": 70,
                "browser_download_url": "https://github.com/x/y/releases/download/SUGv0.3.3/StrangeUtaGame-v0.3.3.zip.sha256",
            },
        ],
    }


class TestParseReleaseJson:
    def test_basic_fields(self):
        rel = _parse_release_json(_fake_payload())
        assert rel.tag == "SUGv0.3.3"
        assert rel.version == "0.3.3"
        assert rel.name == "v0.3.3"
        assert "A" in rel.body
        assert rel.prerelease is False
        assert rel.published_at.startswith("2026-")

    def test_asset_count(self):
        rel = _parse_release_json(_fake_payload())
        assert len(rel.assets) == 2

    def test_pick_primary_prefers_exact(self):
        rel = _parse_release_json(_fake_payload())
        primary = rel.pick_primary_asset("StrangeUtaGame-v0.3.3.zip")
        assert primary is not None
        assert primary.name == "StrangeUtaGame-v0.3.3.zip"

    def test_pick_primary_preferred_not_found_returns_none(self):
        # preferred_name 明确指定但不存在 → 返回 None（防止变体混装，不回退）
        rel = _parse_release_json(_fake_payload())
        primary = rel.pick_primary_asset("StrangeUtaGame-noWinIME-v0.3.3.zip")
        assert primary is None

    def test_pick_primary_no_preferred_falls_back_to_zip(self):
        # 不指定 preferred_name → 正常回退到第一个 .zip
        rel = _parse_release_json(_fake_payload())
        primary = rel.pick_primary_asset()
        assert primary is not None
        assert primary.name.endswith(".zip")

    def test_pick_sha256(self):
        rel = _parse_release_json(_fake_payload())
        sha = rel.pick_sha256_asset("StrangeUtaGame-v0.3.3.zip")
        assert sha is not None
        assert sha.name == "StrangeUtaGame-v0.3.3.zip.sha256"

    def test_empty_assets(self):
        payload = _fake_payload()
        payload["assets"] = []
        rel = _parse_release_json(payload)
        assert rel.pick_primary_asset() is None
        assert rel.pick_sha256_asset("X") is None


class TestOverrideAssetUrls:
    def test_replace_primary(self):
        rel = _parse_release_json(_fake_payload())
        overridden = override_asset_urls(rel, "ghproxy", "StrangeUtaGame-v0.3.3.zip")
        primary = overridden.pick_primary_asset("StrangeUtaGame-v0.3.3.zip")
        assert primary is not None
        assert "mirror.ghproxy.com" in primary.download_url

    def test_keep_others(self):
        rel = _parse_release_json(_fake_payload())
        overridden = override_asset_urls(rel, "ghproxy", "StrangeUtaGame-v0.3.3.zip")
        sha = overridden.pick_sha256_asset("StrangeUtaGame-v0.3.3.zip")
        # sha256 资产名字不匹配 primary_asset_name，URL 应保持原样
        assert sha is not None
        assert "mirror.ghproxy.com" not in sha.download_url

    def test_override_all_when_no_filter(self):
        rel = _parse_release_json(_fake_payload())
        overridden = override_asset_urls(rel, "fastgit", primary_asset_name=None)
        # 没有 primary_asset_name 过滤 → 所有资产都被重写
        for a in overridden.assets:
            assert a.download_url.startswith("https://download.fastgit.org/")


class TestReleaseAssetExt:
    def test_extension_detection(self):
        a = ReleaseAsset(name="foo.tar.gz", size=0, download_url="")
        assert a.extension == ".tar.gz"
        b = ReleaseAsset(name="foo.zip", size=0, download_url="")
        assert b.extension == ".zip"
        c = ReleaseAsset(name="foo.zip.sha256", size=0, download_url="")
        assert c.extension == ".sha256"
        d = ReleaseAsset(name="noext", size=0, download_url="")
        assert d.extension == ""
