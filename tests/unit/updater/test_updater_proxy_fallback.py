"""``updater_app`` 无 ``--proxy`` 时的代理回退解析测试。

主程序正常会透传 ``--proxy``；手动启动或旧版主程序未透传时，
Updater 必须按主程序 config.json 的 ``updater.proxy`` 设置自行解析，
保证更新器的所有下载同样受应用代理管理。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _ensure_path():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    yield


@pytest.fixture(autouse=True)
def _no_sug_config_env(monkeypatch: pytest.MonkeyPatch):
    # conftest 会给整个测试进程设置 SUG_CONFIG_DIR 隔离主程序配置；
    # 这里测的是 Updater 自己的 config 目录解析，必须从干净环境开始
    monkeypatch.delenv("SUG_CONFIG_DIR", raising=False)


def _get_module():
    import importlib
    return importlib.import_module("updater_app.main")


def _write_config(app_dir: Path, mode: str, manual: str = "") -> None:
    app_dir.mkdir(parents=True, exist_ok=True)
    (app_dir / "config.json").write_text(
        json.dumps({"updater": {"proxy": {"mode": mode, "manual_url": manual}}}),
        encoding="utf-8",
    )


class TestResolveFallbackProxy:
    def test_manual_mode(self, tmp_path):
        mod = _get_module()
        _write_config(tmp_path, "manual", "127.0.0.1:7890")
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:7890"

    def test_manual_url_keeps_scheme(self, tmp_path):
        mod = _get_module()
        _write_config(tmp_path, "manual", "socks5://127.0.0.1:1080")
        assert mod.resolve_fallback_proxy(tmp_path) == "socks5://127.0.0.1:1080"

    def test_manual_invalid_falls_back_empty(self, tmp_path):
        mod = _get_module()
        _write_config(tmp_path, "manual", "no-port-here")
        assert mod.resolve_fallback_proxy(tmp_path) == ""

    def test_off_mode_empty(self, tmp_path):
        mod = _get_module()
        _write_config(tmp_path, "off")
        assert mod.resolve_fallback_proxy(tmp_path) == ""

    def test_missing_config_empty(self, tmp_path):
        mod = _get_module()
        assert mod.resolve_fallback_proxy(tmp_path) == ""

    def test_malformed_config_empty(self, tmp_path):
        mod = _get_module()
        tmp_path.mkdir(parents=True, exist_ok=True)
        (tmp_path / "config.json").write_text("{not json", encoding="utf-8")
        assert mod.resolve_fallback_proxy(tmp_path) == ""

    def test_env_config_dir_takes_priority(self, tmp_path, monkeypatch):
        mod = _get_module()
        elsewhere = tmp_path / "elsewhere"
        _write_config(tmp_path, "off")
        _write_config(elsewhere, "manual", "127.0.0.1:7890")
        monkeypatch.setenv("SUG_CONFIG_DIR", str(elsewhere))
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:7890"

    def test_config_redirect_marker(self, tmp_path):
        mod = _get_module()
        redirected = tmp_path / "redirected"
        _write_config(redirected, "manual", "127.0.0.1:7891")
        _write_config(tmp_path, "off")
        (tmp_path / ".config_redirect").write_text(str(redirected), encoding="utf-8")
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:7891"

    def test_system_mode_uses_registry_reader(self, tmp_path, monkeypatch):
        mod = _get_module()
        _write_config(tmp_path, "system")
        monkeypatch.setattr(mod, "_read_system_proxy", lambda: "http://127.0.0.1:8888")
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:8888"

    def test_auto_mode_registry_first(self, tmp_path, monkeypatch):
        mod = _get_module()
        _write_config(tmp_path, "auto")
        scanned: list = []
        monkeypatch.setattr(mod, "_read_system_proxy", lambda: "http://127.0.0.1:8888")
        monkeypatch.setattr(
            mod, "_scan_local_proxy", lambda timeout=0.15: scanned.append(1) or ""
        )
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:8888"
        assert not scanned, "系统代理可用时不应再扫描端口"

    def test_auto_mode_falls_back_to_scan(self, tmp_path, monkeypatch):
        mod = _get_module()
        _write_config(tmp_path, "auto")
        monkeypatch.setattr(mod, "_read_system_proxy", lambda: "")
        monkeypatch.setattr(
            mod, "_scan_local_proxy", lambda timeout=0.15: "http://127.0.0.1:7897"
        )
        assert mod.resolve_fallback_proxy(tmp_path) == "http://127.0.0.1:7897"


class TestReadSystemProxyParsing:
    """注册表 ProxyServer 字符串的标准化（不真读注册表，只测解析逻辑）。"""

    def test_plain_host_port_gets_scheme(self, monkeypatch):
        winreg = pytest.importorskip("winreg")
        mod = _get_module()
        monkeypatch.setattr(mod.sys, "platform", "win32")

        class _Key:
            def __init__(self, values):
                self._values = values

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def _fake_openkey(root, path):
            return _Key(
                {
                    "ProxyEnable": (1, winreg.REG_DWORD),
                    "ProxyServer": ("127.0.0.1:7890", winreg.REG_SZ),
                }
            )

        def _fake_query(key, name):
            return key._values[name]

        monkeypatch.setattr(winreg, "OpenKey", _fake_openkey)
        monkeypatch.setattr(winreg, "QueryValueEx", _fake_query)
        assert mod._read_system_proxy() == "http://127.0.0.1:7890"
