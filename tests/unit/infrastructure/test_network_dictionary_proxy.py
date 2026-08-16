"""网络词典 HTTP 拉取的代理管控测试。

``fetch_source_entries`` / ``auto_update_enabled_sources`` 必须把
「设置 → 网络与代理」（``updater.proxy.resolve_proxy`` 同源）解析出的代理
真正作用到 urllib 请求上；mode=off（``{}``）时要显式禁用代理，
而不是回落系统环境变量。
"""

from __future__ import annotations

import urllib.request
from typing import Any, List, Tuple

import pytest

from strange_uta_game.backend.infrastructure import network_dictionary as nd


# ───────────────────────── urllib 拦截 ─────────────────────────


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False


class _FakeOpener:
    def __init__(self, body: bytes):
        self._body = body
        self.requests: List[Any] = []

    def open(self, req: Any, timeout: Any = None) -> _FakeResponse:
        self.requests.append(req)
        return _FakeResponse(self._body)


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> Tuple[List[List[Any]], List[_FakeOpener]]:
    """拦截 ``urllib.request.build_opener``：记录 handlers 并返回可控 opener。"""
    handler_groups: List[List[Any]] = []
    openers: List[_FakeOpener] = []

    def _fake_build_opener(*handlers: Any) -> _FakeOpener:
        handler_groups.append(list(handlers))
        opener = _FakeOpener(b"[success]\xe3\x81\x82\t\xe3\x81\x82\n")
        openers.append(opener)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", _fake_build_opener)
    return handler_groups, openers


def _proxy_handler(handler_groups: List[List[Any]]):
    for handlers in handler_groups:
        for h in handlers:
            if isinstance(h, urllib.request.ProxyHandler):
                return h
    return None


# ───────────────────────── fetch_source_entries ─────────────────────────


class TestFetchSourceEntriesProxy:
    def test_proxies_dict_applied_to_proxy_handler(self, captured):
        handler_groups, openers = captured
        proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        nd.fetch_source_entries("http://example.net/dict.php", proxies=proxies)

        assert openers and openers[0].requests, "应发起一次请求"
        handler = _proxy_handler(handler_groups)
        assert handler is not None, "proxies 非空时必须注入 ProxyHandler"
        assert handler.proxies.get("http") == "http://127.0.0.1:7890"
        assert handler.proxies.get("https") == "http://127.0.0.1:7890"

    def test_empty_proxies_disables_env_proxy(self, captured):
        # mode=off → {}：显式禁用代理（含系统环境变量），仍是"受管控"
        handler_groups, _ = captured
        nd.fetch_source_entries("http://example.net/dict.php", proxies={})

        handler = _proxy_handler(handler_groups)
        assert handler is not None, "proxies={} 时必须注入空 ProxyHandler 以禁用环境代理"
        assert handler.proxies == {}

    def test_none_proxies_keeps_default_behavior(self, captured):
        # 未管控（None）→ 不注入 ProxyHandler，走 urllib 默认（环境变量）
        handler_groups, _ = captured
        nd.fetch_source_entries("http://example.net/dict.php", proxies=None)

        assert _proxy_handler(handler_groups) is None

    def test_https_url_still_gets_ssl_handler(self, captured):
        handler_groups, _ = captured
        nd.fetch_source_entries(
            "https://example.net/dict.php",
            proxies={"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"},
        )

        assert _proxy_handler(handler_groups) is not None
        assert any(
            isinstance(h, urllib.request.HTTPSHandler)
            for handlers in handler_groups
            for h in handlers
        ), "HTTPS URL 应保留证书上下文 handler"


# ───────────────────────── resolve_app_proxies ─────────────────────────


class TestResolveAppProxies:
    def _patch_settings(self, monkeypatch: pytest.MonkeyPatch, mode: str, manual: str = "") -> None:
        from strange_uta_game.updater.settings import UpdaterSettings

        fake = UpdaterSettings(proxy_mode=mode, proxy_manual_url=manual)
        monkeypatch.setattr(
            UpdaterSettings, "load", classmethod(lambda cls, app=None: fake)
        )

    def test_manual_mode_returns_dict(self, monkeypatch):
        self._patch_settings(monkeypatch, "manual", "127.0.0.1:7890")
        assert nd.resolve_app_proxies() == {
            "http": "http://127.0.0.1:7890",
            "https": "http://127.0.0.1:7890",
        }

    def test_off_mode_returns_empty_dict(self, monkeypatch):
        # off 是"显式不用代理"，必须与 None（未管控）区分开
        self._patch_settings(monkeypatch, "off")
        assert nd.resolve_app_proxies() == {}

    def test_system_mode_reads_system_proxy(self, monkeypatch):
        from strange_uta_game.updater.proxy import ProxyInfo

        self._patch_settings(monkeypatch, "system")
        monkeypatch.setattr(
            "strange_uta_game.updater.proxy.read_system_proxy",
            lambda: ProxyInfo(url="http://127.0.0.1:7897", source="system"),
        )
        assert nd.resolve_app_proxies() == {
            "http": "http://127.0.0.1:7897",
            "https": "http://127.0.0.1:7897",
        }

    def test_settings_unreadable_returns_none(self, monkeypatch):
        from strange_uta_game.updater.settings import UpdaterSettings

        def _boom(cls, app=None):
            raise RuntimeError("settings unavailable")

        monkeypatch.setattr(UpdaterSettings, "load", classmethod(_boom))
        assert nd.resolve_app_proxies() is None


# ───────────────────────── auto_update_enabled_sources ─────────────────────────


class TestAutoUpdatePassthrough:
    def test_proxies_forwarded_to_fetch(self, monkeypatch):
        seen: dict = {}

        def _fake_fetch(url, timeout=8.0, allow_insecure_fallback=True, proxies=None):
            seen["proxies"] = proxies
            return [{"enabled": True, "word": "あ", "reading": "あ"}]

        monkeypatch.setattr(nd, "fetch_source_entries", _fake_fetch)
        doc = {
            "sources": [
                {"id": "s1", "name": "S1", "url": "http://example.net/d.php", "enabled": True}
            ]
        }
        ok_msgs, fail_msgs = nd.auto_update_enabled_sources(
            doc, proxies={"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        )

        assert seen["proxies"] == {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
        assert ok_msgs and not fail_msgs
        assert doc["sources"][0]["entries"]
        assert doc["sources"][0]["last_fetched"]
