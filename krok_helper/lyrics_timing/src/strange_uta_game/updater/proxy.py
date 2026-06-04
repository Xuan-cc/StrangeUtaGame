"""代理检测与解析。

支持：

1. **读取 Windows 系统代理**：从注册表
   ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Internet Settings``
   读取 ``ProxyEnable`` / ``ProxyServer``。
2. **扫描常用本地代理端口**：尝试 TCP connect ``127.0.0.1:port``。
3. **解析代理字符串**：兼容
   * ``http://127.0.0.1:7890``
   * ``socks5://user:pass@host:port``
   * ``127.0.0.1:7890``（无 scheme，按 http 处理）
   * ``http=127.0.0.1:7890;https=127.0.0.1:7890`` （Windows 系统代理常见格式）
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# 常用本地代理端口（按主流软件出现频次粗排）。
# 已包含用户提到的 7897 (Clash Verge) 与 17897 (用户本机)。
COMMON_PROXY_PORTS: Tuple[int, ...] = (
    7890,   # Clash for Windows / Clash.Meta 默认 HTTP
    7891,   # Clash 默认 SOCKS
    7897,   # Clash Verge HTTP
    17897,  # 用户自定义常见端口
    10809,  # V2RayN HTTP
    10808,  # V2RayN SOCKS
    1080,   # SOCKS5 通用
    1081,   # SOCKS5 备用
    2080,   # Mihomo / 个别工具
    8118,   # Privoxy
    8888,   # Fiddler / Charles HTTP
    8889,   # Charles 备用
    20171,  # SteamPP (Watt Toolkit)
    20172,
    33210,  # 一些机场客户端默认
    7070,   # Surge
    6152,   # Surge 备用
    1087,   # Shadowsocks-NG (macOS) — 跨平台无害
)


@dataclass(frozen=True)
class ProxyInfo:
    """代理探测/解析结果。"""
    # 标准化后的 URL，例 ``http://127.0.0.1:7890``；无代理时为空串。
    url: str
    # 来源标签（调试/日志用）：``"system"`` / ``"scan"`` / ``"manual"`` / ``""``。
    source: str = ""

    @property
    def is_valid(self) -> bool:
        return bool(self.url)


# ───────────────────────── 系统代理读取 ─────────────────────────


def _parse_proxy_server(raw: str) -> str:
    """把 Windows ``ProxyServer`` 字段标准化为单一 URL。

    Windows 可能存的两种格式：

    * 单一字符串 ``"127.0.0.1:7890"`` 或 ``"http://127.0.0.1:7890"``
      → 直接套上 http:// 即可
    * 按协议分号分隔 ``"http=127.0.0.1:7890;https=127.0.0.1:7890;socks=...:1080"``
      → 优先取 ``https=``，没有再取 ``http=``，再没有取第一段
    """
    s = (raw or "").strip()
    if not s:
        return ""
    if "=" in s:
        parts = [p.strip() for p in s.split(";") if p.strip()]
        kv: Dict[str, str] = {}
        for p in parts:
            if "=" in p:
                k, v = p.split("=", 1)
                kv[k.strip().lower()] = v.strip()
        chosen = kv.get("https") or kv.get("http")
        if not chosen:
            return ""
        s = chosen
    # 加 scheme
    if "://" not in s:
        s = "http://" + s
    return s


def read_system_proxy() -> Optional[ProxyInfo]:
    """读取 Windows 系统代理。无系统代理或非 Windows 时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            try:
                enable, _ = winreg.QueryValueEx(key, "ProxyEnable")
            except FileNotFoundError:
                return None
            if not int(enable):
                return None
            try:
                server, _ = winreg.QueryValueEx(key, "ProxyServer")
            except FileNotFoundError:
                return None
        url = _parse_proxy_server(str(server))
        if not url:
            return None
        return ProxyInfo(url=url, source="system")
    except Exception:
        return None


# ───────────────────────── 端口扫描 ─────────────────────────


def _is_port_open(host: str, port: int, timeout: float = 0.15) -> bool:
    """检测 TCP ``host:port`` 是否可连接（极短超时，避免阻塞 UI）。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((host, port))
        return True
    except (OSError, socket.timeout):
        return False


def scan_local_proxy_ports(
    ports: Tuple[int, ...] = COMMON_PROXY_PORTS,
    timeout: float = 0.15,
) -> List[int]:
    """扫描本机常用代理端口，返回所有可达端口列表（按 ``ports`` 顺序）。"""
    return [p for p in ports if _is_port_open("127.0.0.1", p, timeout=timeout)]


def detect_proxy_auto(extra_ports: Optional[Tuple[int, ...]] = None) -> Optional[ProxyInfo]:
    """自动探测代理：先读系统代理，再扫描常用端口。

    Args:
        extra_ports: 额外端口（追加到默认列表末尾）。

    Returns:
        探测到的 ``ProxyInfo``；都失败返回 ``None``。
    """
    sys_proxy = read_system_proxy()
    if sys_proxy and sys_proxy.is_valid:
        return sys_proxy

    ports = COMMON_PROXY_PORTS
    if extra_ports:
        merged: List[int] = list(ports) + [p for p in extra_ports if p not in ports]
        ports = tuple(merged)
    found = scan_local_proxy_ports(ports)
    if not found:
        return None
    return ProxyInfo(url=f"http://127.0.0.1:{found[0]}", source="scan")


# ───────────────────────── 手动配置解析 ─────────────────────────


def parse_manual_proxy(value: str) -> Optional[ProxyInfo]:
    """把用户填入的代理字符串解析成 ``ProxyInfo``。

    返回 ``None`` 表示无效输入（应回退为"不使用代理"）。
    """
    s = (value or "").strip()
    if not s:
        return None
    if "://" not in s:
        s = "http://" + s
    # 不强行校验完整 URL；最低限度要求带 host:port
    if "@" in s:
        host_part = s.rsplit("@", 1)[1]
    else:
        host_part = s.split("://", 1)[1] if "://" in s else s
    if ":" not in host_part:
        return None
    return ProxyInfo(url=s, source="manual")


# ───────────────────────── 综合入口 ─────────────────────────


def resolve_proxy(
    mode: str,
    manual_value: str = "",
) -> Tuple[Optional[ProxyInfo], Optional[Dict[str, str]]]:
    """根据用户的代理模式返回最终代理。

    Args:
        mode: ``"off"`` / ``"system"`` / ``"manual"`` / ``"auto"``。
            * ``off``     —— 强制不使用代理
            * ``system``  —— 仅使用 Windows 系统代理（无则不用代理）
            * ``manual``  —— 仅使用 ``manual_value``
            * ``auto``    —— 系统代理优先，否则扫描常用端口
        manual_value: 当 ``mode == "manual"`` 时的代理字符串。

    Returns:
        ``(info, requests_proxies)``：

        * ``info`` 用于 UI 展示；不需要时为 ``None``。
        * ``requests_proxies`` 为 ``requests.get(proxies=...)`` 直接可用的 dict；
          不需要代理时为 ``None``。
    """
    info: Optional[ProxyInfo]
    if mode == "off":
        info = None
    elif mode == "system":
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
