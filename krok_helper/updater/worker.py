from __future__ import annotations

import logging
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from krok_helper.config import APP_VERSION
from krok_helper.network import requests_session_for_proxy
from krok_helper.updater.settings import UpdaterSettings
from krok_helper.updater.sources import SourceId, build_api_urls, build_release_urls

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    size: int
    download_url: str


@dataclass(frozen=True)
class LatestRelease:
    tag: str
    version: str
    name: str
    body: str
    html_url: str
    prerelease: bool
    published_at: str
    assets: list[ReleaseAsset] = field(default_factory=list)

    def pick_primary_asset(self, preferred_name: str) -> ReleaseAsset | None:
        for asset in self.assets:
            if asset.name == preferred_name:
                return asset
        platform_key = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else ""
        if platform_key:
            for asset in self.assets:
                if platform_key in asset.name.lower() and asset.name.lower().endswith(".zip"):
                    return asset
        for asset in self.assets:
            if asset.name.lower().endswith(".zip"):
                return asset
        return None


@dataclass
class CheckResult:
    ok: bool
    has_update: bool = False
    release: LatestRelease | None = None
    primary_url: str = ""
    primary_source: str = ""
    primary_asset_name: str = ""
    download_candidates: list[tuple[SourceId, str]] = field(default_factory=list)
    attempts: list[tuple[SourceId, str, str]] = field(default_factory=list)
    error: str = ""
    skipped_due_to_cooldown: bool = False


def _strip_tag_prefix(tag: str) -> str:
    value = tag.strip()
    if value.lower().startswith("v"):
        return value[1:]
    return value


def _version_key(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in _strip_tag_prefix(value).replace("-", ".").split("."):
        digits = ""
        for char in chunk:
            if char.isdigit():
                digits += char
            else:
                break
        parts.append(int(digits or 0))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer_version(remote: str, local: str = APP_VERSION) -> bool:
    return _version_key(remote) > _version_key(local)


def current_asset_name() -> str:
    if sys.platform == "darwin":
        return "KaraokeStudio-macos.zip"
    return "KaraokeStudio-windows.zip"


def _parse_release(payload: dict[str, Any]) -> LatestRelease:
    assets: list[ReleaseAsset] = []
    for raw_asset in payload.get("assets") or []:
        if not isinstance(raw_asset, dict):
            continue
        name = str(raw_asset.get("name") or "")
        if not name:
            continue
        assets.append(
            ReleaseAsset(
                name=name,
                size=int(raw_asset.get("size") or 0),
                download_url=str(raw_asset.get("browser_download_url") or ""),
            )
        )
    tag = str(payload.get("tag_name") or "")
    return LatestRelease(
        tag=tag,
        version=_strip_tag_prefix(tag),
        name=str(payload.get("name") or "") or tag,
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or ""),
        prerelease=bool(payload.get("prerelease") or False),
        published_at=str(payload.get("published_at") or ""),
        assets=assets,
    )


def _proxies_for(settings: UpdaterSettings) -> dict[str, str] | None:
    _session, proxies = requests_session_for_proxy(settings.proxy_mode, settings.proxy_manual_url)
    return proxies


def fetch_latest_release(
    settings: UpdaterSettings,
) -> tuple[LatestRelease | None, list[tuple[SourceId, str, str]]]:
    attempts: list[tuple[SourceId, str, str]] = []
    session, proxies = requests_session_for_proxy(settings.proxy_mode, settings.proxy_manual_url)
    for source, url in build_api_urls(settings.source_order):
        try:
            response = session.get(
                url,
                headers={"User-Agent": "KaraokeStudio-Updater/1.0", "Accept": "application/json"},
                proxies=proxies,
                timeout=(5, 20),
                allow_redirects=True,
            )
            if response.status_code != 200:
                attempts.append((source, url, f"HTTP {response.status_code}"))
                continue
            payload = response.json()
            if not isinstance(payload, dict):
                attempts.append((source, url, "响应不是 JSON 对象"))
                continue
            release = _parse_release(payload)
            if not release.tag:
                attempts.append((source, url, "缺少 tag_name"))
                continue
            if release.prerelease:
                attempts.append((source, url, "命中预发布版本，已跳过"))
                continue
            attempts.append((source, url, ""))
            return release, attempts
        except Exception as exc:  # noqa: BLE001
            attempts.append((source, url, str(exc)))
    return None, attempts


class _CheckRunnable(QObject):
    finished = pyqtSignal(object)

    def __init__(self, settings: UpdaterSettings, manual: bool = False):
        super().__init__()
        self._settings = settings
        self._manual = manual

    def run(self) -> None:
        try:
            result = self._do_check()
        except Exception as exc:  # noqa: BLE001
            log.exception("更新检查异常")
            result = CheckResult(ok=False, error=f"检查异常: {exc}")
        self.finished.emit(result)

    def _do_check(self) -> CheckResult:
        if not self._settings.enabled:
            return CheckResult(ok=False, error="更新功能已禁用")
        if not self._manual and not self._settings.check_on_startup:
            return CheckResult(ok=False, skipped_due_to_cooldown=True)
        if not self._manual and self._settings.is_within_check_cooldown():
            return CheckResult(ok=True, skipped_due_to_cooldown=True)

        release, attempts = fetch_latest_release(self._settings)
        if release is None:
            return CheckResult(ok=False, error="无法访问任何更新源（请检查网络/代理）", attempts=attempts)

        self._settings.last_check_at = int(time.time())
        self._settings.last_seen_version = release.version
        preferred_asset = current_asset_name()
        asset = release.pick_primary_asset(preferred_asset)
        candidates: list[tuple[SourceId, str]] = []
        primary_source = ""
        primary_url = ""
        asset_name = asset.name if asset is not None else preferred_asset
        if asset is not None:
            candidates = build_release_urls(self._settings.source_order, release.tag, asset_name)
            if candidates:
                primary_source, primary_url = candidates[0]

        has_update = is_newer_version(release.version, APP_VERSION) and asset is not None
        if has_update and not self._manual and self._settings.skipped_version == release.version:
            has_update = False
        return CheckResult(
            ok=True,
            has_update=has_update,
            release=release,
            primary_url=primary_url,
            primary_source=primary_source,
            primary_asset_name=asset_name,
            download_candidates=candidates,
            attempts=attempts,
        )


class UpdateChecker(QObject):
    finished = pyqtSignal(object)

    def __init__(self, settings: UpdaterSettings, manual: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self._settings = settings
        self._manual = manual
        self._thread: QThread | None = None
        self._worker: _CheckRunnable | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = QThread()
        self._worker = _CheckRunnable(self._settings, self._manual)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _on_finished(self, result: object) -> None:
        self.finished.emit(result)

    def _cleanup(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
