from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from krok_helper.settings import AppSettings, save_app_settings
from krok_helper.updater.sources import DEFAULT_ORDER, SourceId, normalize_order

DEFAULT_MIN_CHECK_INTERVAL_HOURS = 8

DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "check_on_startup": True,
    "min_check_interval_hours": DEFAULT_MIN_CHECK_INTERVAL_HOURS,
    "source_order": list(DEFAULT_ORDER),
    "proxy": {
        "mode": "system",
        "manual_url": "",
    },
    "skipped_version": "",
    "last_seen_version": "",
    "last_check_at": 0,
}


@dataclass
class UpdaterSettings:
    enabled: bool = True
    check_on_startup: bool = True
    min_check_interval_hours: int = DEFAULT_MIN_CHECK_INTERVAL_HOURS
    source_order: list[SourceId] = field(default_factory=lambda: list(DEFAULT_ORDER))
    proxy_mode: str = "system"
    proxy_manual_url: str = ""
    skipped_version: str = ""
    last_seen_version: str = ""
    last_check_at: int = 0

    @classmethod
    def load(cls, app_settings: AppSettings) -> "UpdaterSettings":
        raw = app_settings.updater if isinstance(app_settings.updater, dict) else {}
        merged: dict[str, Any] = {}
        for key, value in DEFAULTS.items():
            if isinstance(value, dict):
                current = raw.get(key, {})
                merged[key] = {**value, **(current if isinstance(current, dict) else {})}
            else:
                merged[key] = raw.get(key, value)
        proxy = merged.get("proxy") if isinstance(merged.get("proxy"), dict) else {}
        return cls(
            enabled=bool(merged.get("enabled", True)),
            check_on_startup=bool(merged.get("check_on_startup", True)),
            min_check_interval_hours=max(0, int(merged.get("min_check_interval_hours", DEFAULT_MIN_CHECK_INTERVAL_HOURS) or 0)),
            source_order=normalize_order(merged.get("source_order") or []),
            proxy_mode=str(proxy.get("mode", "system")),
            proxy_manual_url=str(proxy.get("manual_url", "")),
            skipped_version=str(merged.get("skipped_version", "")),
            last_seen_version=str(merged.get("last_seen_version", "")),
            last_check_at=int(merged.get("last_check_at", 0) or 0),
        )

    def save(self, app_settings: AppSettings) -> None:
        app_settings.updater = self.to_payload()
        save_app_settings(app_settings)

    def to_payload(self) -> dict[str, Any]:
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

    def is_within_check_cooldown(self, now: float | None = None) -> bool:
        if self.min_check_interval_hours <= 0 or self.last_check_at <= 0:
            return False
        now = time.time() if now is None else now
        return now - self.last_check_at < self.min_check_interval_hours * 3600


def ensure_updater_settings(app_settings: AppSettings) -> UpdaterSettings:
    raw = app_settings.updater
    settings = UpdaterSettings.load(app_settings)
    if (
        not isinstance(raw, dict)
        or "enabled" not in raw
        or "source_order" not in raw
        or "min_check_interval_hours" not in raw
        or "last_check_at" not in raw
    ):
        settings.save(app_settings)
    return settings
