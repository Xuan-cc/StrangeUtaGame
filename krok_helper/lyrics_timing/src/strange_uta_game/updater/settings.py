"""Updater 自身的配置项 —— 复用 :class:`AppSettings`，置于 ``config.json`` 的
``updater`` 命名空间下。

不直接持久化为独立文件，遵循"用户配置统一在 config.json"原则。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..frontend.settings.app_settings import AppSettings
from .sources import DEFAULT_ORDER, SourceId, normalize_order

SETTINGS_NAMESPACE = "updater"

# 默认启动期检查间隔（小时）。手动检查不受此限制。
DEFAULT_MIN_CHECK_INTERVAL_HOURS = 8

# ``config.json`` 中 ``updater`` 节点的默认值（仅作 fallback；用户每次写入会自动 merge）。
DEFAULTS: Dict[str, Any] = {
    "enabled": True,
    "check_on_startup": True,
    # 启动期检查的最小间隔（小时）—— 防抖，避免每次启动都打扰用户与 GitHub API
    "min_check_interval_hours": DEFAULT_MIN_CHECK_INTERVAL_HOURS,
    "source_order": list(DEFAULT_ORDER),
    "proxy": {
        # off / system / manual / auto
        "mode": "system",
        "manual_url": "",
    },
    # 用户点击"跳过此版本"后写入的版本号；版本超过时清零。
    "skipped_version": "",
    # 最近一次发现的远端版本号；UI 用于在主程序里展示"有新版本"红点之类。
    "last_seen_version": "",
    # 最近一次实际成功完成 release 拉取的 Unix epoch 秒数（int）。
    "last_check_at": 0,
}


@dataclass
class UpdaterSettings:
    """更新器配置的轻量包装。

    通过 :meth:`load` 从 :class:`AppSettings` 取值，通过 :meth:`save` 写回。
    """

    enabled: bool = True
    check_on_startup: bool = True
    min_check_interval_hours: int = DEFAULT_MIN_CHECK_INTERVAL_HOURS
    source_order: List[SourceId] = field(default_factory=lambda: list(DEFAULT_ORDER))
    proxy_mode: str = "system"
    proxy_manual_url: str = ""
    skipped_version: str = ""
    last_seen_version: str = ""
    last_check_at: int = 0

    # ── 与 AppSettings 桥接 ──

    @classmethod
    def load(cls, app: Optional[AppSettings] = None) -> "UpdaterSettings":
        app = app or AppSettings()
        # 全节点 fallback
        raw = app.get(SETTINGS_NAMESPACE, None)
        if not isinstance(raw, dict):
            raw = DEFAULTS
        else:
            # merge：缺失字段补默认值
            merged: Dict[str, Any] = {}
            for k, v in DEFAULTS.items():
                if isinstance(v, dict):
                    sub = raw.get(k, {})
                    if not isinstance(sub, dict):
                        sub = {}
                    merged[k] = {**v, **sub}
                else:
                    merged[k] = raw.get(k, v)
            raw = merged
        proxy = raw.get("proxy") or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            check_on_startup=bool(raw.get("check_on_startup", True)),
            min_check_interval_hours=int(
                raw.get("min_check_interval_hours", DEFAULT_MIN_CHECK_INTERVAL_HOURS)
            ),
            source_order=normalize_order(raw.get("source_order") or list(DEFAULT_ORDER)),
            proxy_mode=str(proxy.get("mode", "system")),
            proxy_manual_url=str(proxy.get("manual_url", "")),
            skipped_version=str(raw.get("skipped_version", "")),
            last_seen_version=str(raw.get("last_seen_version", "")),
            last_check_at=int(raw.get("last_check_at", 0) or 0),
        )

    def save(self, app: Optional[AppSettings] = None) -> None:
        """把当前字段写回 ``config.json``。

        采用替换式写入（``set(namespace, dict)``）—— ``AppSettings.set`` 会按路径覆盖，
        ``save`` 用 indent=2 持久化。
        """
        app = app or AppSettings()
        app.set(SETTINGS_NAMESPACE, self._serialize())
        app.save()

    def _serialize(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "check_on_startup": self.check_on_startup,
            "min_check_interval_hours": int(self.min_check_interval_hours),
            "source_order": list(self.source_order),
            "proxy": {
                "mode": self.proxy_mode,
                "manual_url": self.proxy_manual_url,
            },
            "skipped_version": self.skipped_version,
            "last_seen_version": self.last_seen_version,
            "last_check_at": int(self.last_check_at),
        }

    # ── 便利访问 ──

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "check_on_startup": self.check_on_startup,
            "min_check_interval_hours": int(self.min_check_interval_hours),
            "source_order": list(self.source_order),
            "proxy_mode": self.proxy_mode,
            "proxy_manual_url": self.proxy_manual_url,
            "skipped_version": self.skipped_version,
            "last_seen_version": self.last_seen_version,
            "last_check_at": int(self.last_check_at),
        }

    # ── 防抖工具 ──

    def is_within_check_cooldown(self, now: Optional[float] = None) -> bool:
        """启动期检查的防抖判断：``True`` 表示距上次检查不足 ``min_check_interval_hours`` 小时。"""
        if self.min_check_interval_hours <= 0 or self.last_check_at <= 0:
            return False
        now = time.time() if now is None else now
        return (now - self.last_check_at) < self.min_check_interval_hours * 3600


def ensure_persisted(app: Optional[AppSettings] = None) -> UpdaterSettings:
    """读出当前 updater 配置；若用户 config.json 中尚无 ``updater`` 节点则补写一次。

    通过比较 ``app.get(NAMESPACE)`` 与 :data:`DEFAULTS` 字段集判定"已经写过"，避免反复写盘。
    返回最终生效的 :class:`UpdaterSettings`。
    """
    app = app or AppSettings()
    raw = app.get(SETTINGS_NAMESPACE, None)
    s = UpdaterSettings.load(app)
    # 如果原 config 没有完整 ``updater`` 节点或缺关键字段 → 落盘一次
    needs_write = (
        not isinstance(raw, dict)
        or "enabled" not in raw
        or "source_order" not in raw
        or "min_check_interval_hours" not in raw
        or "last_check_at" not in raw
    )
    if needs_write:
        s.save(app)
    return s
