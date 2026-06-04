"""QThread 异步检查工作器。

把"读取 release / 比较版本"这一段网络 IO 放到子线程，避免阻塞 UI。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from ..__version__ import __version__
from . import http_client
from .manifest import LatestRelease, fetch_latest_release, fetch_releases_since, override_asset_urls
from .proxy import resolve_proxy
from .settings import UpdaterSettings
from .sources import SourceId
from .version import is_newer_version

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """检查更新的结果。"""
    ok: bool
    has_update: bool = False
    release: Optional[LatestRelease] = None
    primary_url: str = ""            # 选定的资产下载 URL（已根据用户源排序写好）
    primary_source: str = ""         # 命中的源 id
    primary_asset_name: str = ""
    download_candidates: List[Tuple[SourceId, str]] = None  # type: ignore[assignment]
    error: str = ""
    # ``True`` 表示由防抖逻辑跳过；调用方可以静默不弹任何东西。
    skipped_due_to_cooldown: bool = False
    # 检测过程的源尝试记录（成功源 error 为空）
    attempts: List[Tuple[SourceId, str, str]] = None  # type: ignore[assignment]
    # 从当前版本到最新版本之间所有中间版本的 release（含最新版，从新到旧）
    # 用于跨版本更新时在弹窗中展示全部版本的更新日志
    all_releases: List[LatestRelease] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.download_candidates is None:
            self.download_candidates = []
        if self.attempts is None:
            self.attempts = []
        if self.all_releases is None:
            self.all_releases = []


class _CheckRunnable(QObject):
    """实际跑在子线程中的对象。"""
    finished = pyqtSignal(object)  # CheckResult

    def __init__(
        self,
        settings: UpdaterSettings,
        manual: bool = False,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._manual = bool(manual)

    def run(self) -> None:
        try:
            result = self._do_check()
        except Exception as e:  # pragma: no cover — 防御性兜底
            log.exception("更新检查异常")
            result = CheckResult(ok=False, error=f"检查异常: {e}")
        self.finished.emit(result)

    def _do_check(self) -> CheckResult:
        if not self._settings.enabled:
            return CheckResult(ok=False, error="更新功能已禁用")

        # 1) 代理
        _info, proxies = resolve_proxy(self._settings.proxy_mode, self._settings.proxy_manual_url)

        # 2) 拉取 release
        release, attempts = fetch_latest_release(
            self._settings.source_order, proxies=proxies
        )
        if release is None:
            log.warning("所有更新源 API 均失败: %s", attempts)
            return CheckResult(
                ok=False,
                error="无法访问任何更新源（请检查网络/代理）",
                attempts=attempts,
            )

        # 拉取成功 → 更新 last_check_at（即使 has_update=False 也写，避免每次启动都打 API）
        try:
            self._settings.last_check_at = int(time.time())
            self._settings.save()
        except Exception:
            log.exception("写入 last_check_at 失败")

        # 3) 比较版本，并确认本变体的资产已上传
        version_newer = is_newer_version(release.version, __version__)

        # 4) 构造下载候选 URL（让用户在弹窗里看到 OK，下载阶段还可以接力）
        from .sources import build_release_urls
        from ..__version__ import ASSET_NAME_TEMPLATE
        preferred_asset_name = ASSET_NAME_TEMPLATE.format(version=release.version)
        found_asset = release.pick_primary_asset(preferred_name=preferred_asset_name)
        # 只有版本更新 AND 本变体的 zip 已上传到这个 release，才报有更新。
        # 防止 noWinIME 等变体在主版本 zip 上传、但自己的 zip 尚未上传时误报有更新。
        has_update = version_newer and (found_asset is not None)
        asset_name = found_asset.name if found_asset else preferred_asset_name

        candidates: List[Tuple[SourceId, str]] = build_release_urls(
            self._settings.source_order, release.tag, asset_name
        )
        primary_source_id = ""
        primary_url = ""
        if candidates:
            primary_source_id, primary_url = candidates[0]
            # 同步改写 release.assets 中匹配名字的 URL
            release = override_asset_urls(release, primary_source_id, asset_name)  # type: ignore[arg-type]

        # 5) 有更新时获取全部中间版本的 changelog（跨版本更新日志聚合）
        all_releases: List[LatestRelease] = []
        if has_update:
            try:
                all_releases, _ = fetch_releases_since(
                    __version__, self._settings.source_order, proxies=proxies
                )
            except Exception:
                log.debug("获取全量 releases 列表失败（仅展示最新版 changelog）", exc_info=True)
            if not all_releases:
                all_releases = [release]

        return CheckResult(
            ok=True,
            has_update=has_update,
            release=release,
            primary_url=primary_url,
            primary_source=primary_source_id,
            primary_asset_name=asset_name,
            download_candidates=candidates,
            attempts=attempts,
            all_releases=all_releases,
        )



class UpdateChecker(QObject):
    """QObject 包装：把工作器放到独立 QThread 上跑。

    使用方式：

        checker = UpdateChecker(settings, manual=False)  # 启动期
        checker.finished.connect(on_check_done)
        checker.start()

    ``manual=True`` 时跳过 8h 防抖（用户在设置里手动点击"立即检查更新"时使用）。
    """

    finished = pyqtSignal(object)  # CheckResult

    def __init__(
        self,
        settings: UpdaterSettings,
        manual: bool = False,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._settings = settings
        self._manual = bool(manual)
        self._thread: Optional[QThread] = None
        self._worker: Optional[_CheckRunnable] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return  # 已在跑
        self._thread = QThread()
        self._worker = _CheckRunnable(self._settings, manual=self._manual)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_worker_finished)
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.finished.connect(self._cleanup_thread)

        self._thread.start()

    def _on_worker_finished(self, result: object) -> None:
        # 把信号转发给外部连接者
        self.finished.emit(result)

    def _cleanup_thread(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
