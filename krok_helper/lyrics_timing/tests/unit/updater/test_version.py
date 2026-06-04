"""``strange_uta_game.updater.version`` 单元测试。"""

import pytest

from strange_uta_game.updater.version import (
    is_newer_version,
    parse_version,
    split_version_list,
    strip_tag_prefix,
)


class TestParseVersion:
    def test_simple(self):
        assert parse_version("0.3.2") == (0, 3, 2, "")
        assert parse_version("1.0.0") == (1, 0, 0, "")
        assert parse_version("10.20.30") == (10, 20, 30, "")

    def test_with_v_prefix(self):
        assert parse_version("v0.3.2") == (0, 3, 2, "")
        assert parse_version("V1.0.0") == (1, 0, 0, "")

    def test_with_suffix(self):
        assert parse_version("0.3.2-beta1") == (0, 3, 2, "beta1")
        assert parse_version("v1.0.0-rc.1") == (1, 0, 0, "rc.1")

    def test_two_segments(self):
        # patch 缺失视为 0
        assert parse_version("1.2") == (1, 2, 0, "")

    def test_invalid(self):
        assert parse_version("") == (0, 0, 0, "")
        assert parse_version("not-a-version") == (0, 0, 0, "")
        assert parse_version("1") == (0, 0, 0, "")


class TestIsNewerVersion:
    @pytest.mark.parametrize("a,b,expected", [
        ("1.0.0", "0.9.9", True),
        ("0.9.9", "1.0.0", False),
        ("0.3.3", "0.3.2", True),
        ("0.3.2", "0.3.2", False),
        ("0.3.1", "0.3.2", False),
        # 跨段（patch / minor / major）
        ("0.4.0", "0.3.9", True),
        ("1.0.0", "0.99.99", True),
    ])
    def test_basic_ordering(self, a, b, expected):
        assert is_newer_version(a, b) is expected

    def test_release_over_prerelease(self):
        # 三段相同时，正式版 > 预发布
        assert is_newer_version("0.3.2", "0.3.2-beta1") is True
        assert is_newer_version("0.3.2-beta1", "0.3.2") is False

    def test_prerelease_string_compare(self):
        # 两边都有 suffix 时按字符串比
        assert is_newer_version("0.3.2-beta2", "0.3.2-beta1") is True
        assert is_newer_version("0.3.2-beta1", "0.3.2-beta2") is False

    def test_invalid_versions(self):
        # 无效版本被当作 (0,0,0,"") 处理 — 互相不"更新"
        assert is_newer_version("not-version", "0.0.0") is False
        assert is_newer_version("0.0.0", "not-version") is False
        # 但合法版本 vs 无效，仍然合法更新
        assert is_newer_version("0.1.0", "not-version") is True


class TestStripTagPrefix:
    def test_strip_sugv(self):
        assert strip_tag_prefix("SUGv0.3.2") == "0.3.2"

    def test_strip_v(self):
        assert strip_tag_prefix("v0.3.2") == "0.3.2"

    def test_no_prefix(self):
        assert strip_tag_prefix("0.3.2") == "0.3.2"

    def test_empty(self):
        assert strip_tag_prefix("") == ""
        assert strip_tag_prefix(None) == ""  # type: ignore[arg-type]


class TestSplitVersionList:
    def test_normal(self):
        assert split_version_list("0.3.2, 0.3.3, 0.4.0") == ["0.3.2", "0.3.3", "0.4.0"]

    def test_empty(self):
        assert split_version_list("") == []
        assert split_version_list(",, ,") == []
