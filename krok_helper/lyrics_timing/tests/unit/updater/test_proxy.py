"""``strange_uta_game.updater.proxy`` 单元测试。"""

import pytest

from strange_uta_game.updater.proxy import (
    ProxyInfo,
    _parse_proxy_server,
    parse_manual_proxy,
    resolve_proxy,
)


class TestParseProxyServer:
    """Windows ``ProxyServer`` 注册表项的两种存储格式都要支持。"""

    def test_simple_host_port(self):
        assert _parse_proxy_server("127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_full_url(self):
        assert _parse_proxy_server("http://127.0.0.1:7890") == "http://127.0.0.1:7890"

    def test_per_protocol_format_prefers_https(self):
        # http=...:80;https=...:443 → 取 https
        v = _parse_proxy_server("http=10.0.0.1:80;https=10.0.0.1:443")
        assert v == "http://10.0.0.1:443"

    def test_per_protocol_only_http(self):
        v = _parse_proxy_server("http=10.0.0.1:80")
        assert v == "http://10.0.0.1:80"

    def test_per_protocol_only_socks(self):
        # 没有 http/https — 应该返回空（不取 socks）
        v = _parse_proxy_server("socks=10.0.0.1:1080")
        assert v == ""

    def test_empty(self):
        assert _parse_proxy_server("") == ""
        assert _parse_proxy_server("   ") == ""


class TestParseManualProxy:
    def test_valid_with_scheme(self):
        info = parse_manual_proxy("http://127.0.0.1:7890")
        assert info is not None
        assert info.url == "http://127.0.0.1:7890"

    def test_valid_no_scheme(self):
        info = parse_manual_proxy("127.0.0.1:7890")
        assert info is not None
        assert info.url == "http://127.0.0.1:7890"

    def test_socks5_kept(self):
        info = parse_manual_proxy("socks5://127.0.0.1:1080")
        assert info is not None
        assert info.url == "socks5://127.0.0.1:1080"

    def test_invalid_missing_port(self):
        # 缺少端口 → 不可用
        assert parse_manual_proxy("127.0.0.1") is None

    def test_empty(self):
        assert parse_manual_proxy("") is None
        assert parse_manual_proxy("   ") is None


class TestResolveProxy:
    def test_off_returns_none(self):
        info, p = resolve_proxy("off")
        assert info is None
        assert p is None

    def test_manual_valid(self):
        info, p = resolve_proxy("manual", "127.0.0.1:7890")
        assert info is not None
        assert p == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}

    def test_manual_invalid_falls_back_to_none(self):
        info, p = resolve_proxy("manual", "invalid")
        assert info is None
        assert p is None

    def test_unknown_mode(self):
        info, p = resolve_proxy("???", "")  # type: ignore[arg-type]
        assert info is None
        assert p is None


class TestProxyInfo:
    def test_is_valid(self):
        assert ProxyInfo(url="http://a:1").is_valid is True
        assert ProxyInfo(url="").is_valid is False
