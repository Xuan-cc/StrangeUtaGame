from __future__ import annotations

import os
import socket
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any


COMMON_PROXY_PORTS: tuple[int, ...] = (
    7890,
    7891,
    7897,
    17897,
    10809,
    10808,
    1080,
    1081,
    2080,
    8118,
    8888,
    8889,
    20171,
    20172,
    33210,
    7070,
    6152,
    1087,
)

PROXY_ENV_KEYS: tuple[str, ...] = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)


@dataclass(frozen=True)
class ProxyInfo:
    url: str
    source: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.url)


def _parse_proxy_server(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if "=" in value:
        parts = [part.strip() for part in value.split(";") if part.strip()]
        mapping: dict[str, str] = {}
        for part in parts:
            if "=" not in part:
                continue
            key, item = part.split("=", 1)
            mapping[key.strip().lower()] = item.strip()
        value = mapping.get("https") or mapping.get("http") or ""
    if value and "://" not in value:
        value = f"http://{value}"
    return value


def read_system_proxy() -> ProxyInfo | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            try:
                enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except FileNotFoundError:
                return None
            if not int(enabled):
                return None
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                return None
    except Exception:
        return None

    url = _parse_proxy_server(str(server))
    return ProxyInfo(url=url, source="system") if url else None


def _is_port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect((host, port))
        return True
    except OSError:
        return False


def scan_local_proxy_ports(
    ports: tuple[int, ...] = COMMON_PROXY_PORTS,
    timeout: float = 0.15,
) -> list[int]:
    return [port for port in ports if _is_port_open("127.0.0.1", port, timeout=timeout)]


def detect_proxy_auto(extra_ports: tuple[int, ...] | None = None) -> ProxyInfo | None:
    system_proxy = read_system_proxy()
    if system_proxy and system_proxy.is_valid:
        return system_proxy

    ports = COMMON_PROXY_PORTS
    if extra_ports:
        ports = tuple(dict.fromkeys((*COMMON_PROXY_PORTS, *extra_ports)))
    found = scan_local_proxy_ports(ports)
    if not found:
        return None
    return ProxyInfo(url=f"http://127.0.0.1:{found[0]}", source="scan")


def parse_manual_proxy(value: str) -> ProxyInfo | None:
    url = (value or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = f"http://{url}"
    host_part = url.rsplit("@", 1)[-1].split("://", 1)[-1]
    if ":" not in host_part:
        return None
    return ProxyInfo(url=url, source="manual")


def resolve_proxy(mode: str, manual_value: str = "") -> tuple[ProxyInfo | None, dict[str, str] | None]:
    if mode == "system":
        info = read_system_proxy()
    elif mode == "manual":
        info = parse_manual_proxy(manual_value)
    elif mode == "auto":
        info = detect_proxy_auto()
    else:
        info = None

    if not info or not info.is_valid:
        return None, None
    return info, {"http": info.url, "https": info.url}


def _updater_settings_from_app_settings(app_settings: Any):
    from krok_helper.updater.settings import UpdaterSettings

    return UpdaterSettings.load(app_settings)


def resolve_app_proxy(app_settings: Any) -> tuple[ProxyInfo | None, dict[str, str] | None]:
    settings = _updater_settings_from_app_settings(app_settings)
    return resolve_proxy(settings.proxy_mode, settings.proxy_manual_url)


def proxy_url_for_app_settings(app_settings: Any) -> str:
    info, _proxies = resolve_app_proxy(app_settings)
    return info.url if info and info.is_valid else ""


def requests_session_for_proxy(mode: str, manual_value: str = ""):
    import requests

    session = requests.Session()
    info, proxies = resolve_proxy(mode, manual_value)
    if mode == "off" or proxies:
        session.trust_env = False
    return session, proxies


def requests_session_for_app_settings(app_settings: Any):
    settings = _updater_settings_from_app_settings(app_settings)
    return requests_session_for_proxy(settings.proxy_mode, settings.proxy_manual_url)


def urllib_proxy_handler_for_app_settings(app_settings: Any) -> urllib.request.ProxyHandler | None:
    settings = _updater_settings_from_app_settings(app_settings)
    info, _proxies = resolve_proxy(settings.proxy_mode, settings.proxy_manual_url)
    if info and info.is_valid:
        return urllib.request.ProxyHandler({"http": info.url, "https": info.url})
    if settings.proxy_mode == "off":
        return urllib.request.ProxyHandler({})
    return None


def build_urllib_opener_for_app_settings(app_settings: Any, *handlers):
    proxy_handler = urllib_proxy_handler_for_app_settings(app_settings)
    if proxy_handler is not None:
        return urllib.request.build_opener(proxy_handler, *handlers)
    if handlers:
        return urllib.request.build_opener(*handlers)
    return urllib.request.build_opener()


def load_current_app_settings():
    from krok_helper.settings import load_app_settings

    return load_app_settings()


def build_urllib_opener_for_current_settings(*handlers):
    return build_urllib_opener_for_app_settings(load_current_app_settings(), *handlers)


def subprocess_env_for_app_settings(app_settings: Any) -> dict[str, str]:
    env = os.environ.copy()
    settings = _updater_settings_from_app_settings(app_settings)
    url = proxy_url_for_app_settings(app_settings)
    if url:
        env["HTTP_PROXY"] = url
        env["HTTPS_PROXY"] = url
        env["ALL_PROXY"] = url
        env["http_proxy"] = url
        env["https_proxy"] = url
        env["all_proxy"] = url
    elif settings.proxy_mode == "off":
        for key in PROXY_ENV_KEYS:
            env.pop(key, None)
    return env


def subprocess_env_for_current_settings() -> dict[str, str]:
    return subprocess_env_for_app_settings(load_current_app_settings())


def subprocess_kwargs_for_app_settings(app_settings: Any) -> dict[str, Any]:
    return {"env": subprocess_env_for_app_settings(app_settings)}


def proxy_cli_args_for_app_settings(app_settings: Any) -> list[str]:
    url = proxy_url_for_app_settings(app_settings)
    return ["--proxy", url] if url else []

