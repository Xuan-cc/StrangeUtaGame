"""唤起独立 ``Updater.exe`` 接管更新流程。

设计要点：

1. **位置约定** —— ``Updater.exe`` 与主程序 ``StrangeUtaGame.exe`` 同目录。这是
   PyInstaller 单目录打包后最自然的位置；``build.py`` 会保证拷贝到位。
   开发环境下（直接 ``python main.py``）不应该出现 Updater.exe，因此本模块在
   未找到 Updater.exe 时返回 ``Result(launched=False, reason=...)``，由调用方
   决定如何提示。

2. **不被自身锁定** —— Updater.exe 也是 Windows 进程，正在运行时不能被替换。
   我们在调起前把 Updater.exe 复制到 ``%TEMP%/StrangeUtaGameUpdater/Updater.exe``
   再执行 temp 副本；安装完毕后由 Updater.exe 自己清理临时目录。

3. **主程序退出顺序** —— 主程序退出后 Updater.exe 才能解锁 ``StrangeUtaGame.exe``
   与 ``_internal/``。本模块在 ``launch_updater`` 中传入主程序 PID，由 Updater
   等待 PID 退出后再开始替换；调用方在调用本函数后应立刻 ``QApplication.quit``。

4. **自更新** —— 主程序在启动 Updater 之前，先尝试从远端 app part zip 中提取
   新的 Updater.exe 并替换本地版本，确保旧 updater 也能被更新。
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

log = logging.getLogger(__name__)

# 与主程序同目录下的 Updater.exe 名字。
UPDATER_EXE_NAME = "Updater.exe"
# 临时目录名（在 %TEMP% 下）。
TMP_DIR_NAME = "StrangeUtaGameUpdater"


@dataclass
class LaunchPlan:
    """启动 Updater 的输入参数（供 :func:`launch_updater` 使用）。"""
    app_dir: Path                                   # 主程序所在目录
    app_exe_name: str                               # 主程序 EXE 文件名
    target_version: str                             # 目标版本号（纯版本号，非 tag）
    target_tag: str                                 # 远端 release tag
    asset_name: str                                 # 资产文件名（zip）
    download_urls: List[Tuple[str, str]]            # [(source_id, url), ...]
    proxy_url: str = ""                             # 例 ``http://127.0.0.1:7890``
    internal_dir_name: str = "_internal"            # PyInstaller 内部目录名
    expected_sha256: str = ""                       # 可选：发布方提供的 SHA256
    launch_after_update: bool = True                # 安装完是否自动启动主程序

    # 仅供 LaunchPlan.command_args 内部使用
    extras: List[str] = field(default_factory=list)

    def command_args(self, updater_exe: Path, current_pid: int) -> List[str]:
        """生成传给 Updater.exe 的命令行参数。"""
        args: List[str] = [str(updater_exe)]
        args += ["--app-dir", str(self.app_dir)]
        args += ["--app-exe", self.app_exe_name]
        args += ["--target-version", self.target_version]
        args += ["--target-tag", self.target_tag]
        args += ["--asset-name", self.asset_name]
        args += ["--internal-name", self.internal_dir_name]
        args += ["--pid", str(current_pid)]
        if self.proxy_url:
            args += ["--proxy", self.proxy_url]
        if self.expected_sha256:
            args += ["--sha256", self.expected_sha256]
        if not self.launch_after_update:
            args += ["--no-launch"]
        # ``--url`` 允许重复，按用户配置的源排序提供
        for source_id, url in self.download_urls:
            args += ["--url", f"{source_id}|{url}"]
        args += self.extras
        return args


@dataclass
class LaunchResult:
    """:func:`launch_updater` 的返回。"""
    launched: bool
    updater_path: str = ""
    temp_copy_path: str = ""
    pid: int = 0
    reason: str = ""


# ───────────────────────── 工具 ─────────────────────────


def find_app_dir() -> Path:
    """返回主程序根目录（与 ``Updater.exe`` 同级）。

    PyInstaller 模式下，``sys.executable`` 指向 ``StrangeUtaGame.exe``，因此
    其父目录就是我们要的根。开发环境下回退到项目根（``main.py`` 所在）。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    # 开发环境兜底
    return Path(sys.argv[0]).resolve().parent


def find_app_exe_name() -> str:
    """主程序 EXE 文件名。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return "StrangeUtaGame.exe"


def find_updater_exe(app_dir: Optional[Path] = None) -> Optional[Path]:
    """定位与主程序同目录的 ``Updater.exe``；找不到返回 ``None``。"""
    app_dir = app_dir or find_app_dir()
    p = app_dir / UPDATER_EXE_NAME
    if p.exists():
        return p
    # 兼容：放在 _internal/updater/ 下的版本
    p2 = app_dir / "_internal" / "updater" / UPDATER_EXE_NAME
    if p2.exists():
        return p2
    return None


def _copy_updater_to_temp(updater_exe: Path) -> Path:
    """把 Updater.exe 复制到临时目录，避免自身被锁。"""
    tmp_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / UPDATER_EXE_NAME
    # 已存在的副本可能正被另一次更新流程占用 —— 用唯一时间戳后缀兜底
    try:
        shutil.copy2(str(updater_exe), str(dest))
    except PermissionError:
        import time
        dest = tmp_dir / f"Updater-{int(time.time())}.exe"
        shutil.copy2(str(updater_exe), str(dest))
    return dest


# ───────────────────────── 主入口 ─────────────────────────


def _fetch_remote_manifest(
    plan: LaunchPlan,
    proxies_dict: Optional[dict],
) -> Optional[dict]:
    """从各下载源尝试拉取 ``manifest-vX.Y.Z.json``，返回解析后的 dict 或 None。

    manifest 是一个小 JSON 文件，包含各 part 的 sha256 内容哈希，用于在下载
    app.zip 之前先判断是否需要更新，避免不必要的大文件下载。

    manifest 文件名从 ``plan.asset_name`` 派生：
    ``StrangeUtaGame-noWinIME-v1.0.3.zip`` → ``manifest-noWinIME-v1.0.3.json``
    """
    import requests

    manifest_filename = plan.asset_name.replace("StrangeUtaGame", "manifest", 1).replace(".zip", ".json")

    for _source_id, url in plan.download_urls:
        prefix = url.rsplit("/", 1)[0]
        manifest_url = f"{prefix}/{manifest_filename}"
        try:
            resp = requests.get(
                manifest_url,
                headers={"User-Agent": "StrangeUtaGame-MainApp/self-update"},
                proxies=proxies_dict,
                timeout=(5, 15),
                allow_redirects=True,
            )
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data.get("parts"), dict):
                    log.info("[self-update] manifest 获取成功（version=%s）", data.get("version"))
                    return data
        except Exception as e:
            log.debug("[self-update] manifest 拉取失败（%s）: %s", _source_id, e)
    return None


def _read_local_manifest(plan: LaunchPlan) -> Optional[dict]:
    """读取本地 ``_internal/.installed_manifest.json``，不存在或解析失败返回 None。"""
    p = plan.app_dir / plan.internal_dir_name / ".installed_manifest.json"
    if not p.exists():
        return None
    try:
        import json
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _verify_zip_sha256(zip_path: str, url: str, proxies_dict: Optional[dict]) -> bool:
    """下载 ``{url}.sha256`` 并校验 zip 文件的 raw sha256。

    校验失败或无法拉取 sha256 文件时返回 ``True``（降级为跳过校验），不阻断流程。
    """
    import requests
    import hashlib
    import re

    sha_url = url + ".sha256"
    log.info("[self-update] 校验 app.zip sha256: %s", sha_url)
    try:
        resp = requests.get(
            sha_url,
            headers={"User-Agent": "StrangeUtaGame-MainApp/self-update"},
            proxies=proxies_dict,
            timeout=(5, 15),
            allow_redirects=True,
        )
        if resp.status_code != 200:
            log.warning("[self-update] 获取 sha256 文件失败 HTTP %s，跳过校验", resp.status_code)
            return True
        m = re.search(r"\b([0-9a-fA-F]{64})\b", resp.text)
        if not m:
            log.warning("[self-update] sha256 文件内容无法解析，跳过校验")
            return True
        expected = m.group(1).lower()
    except Exception as e:
        log.warning("[self-update] 获取 sha256 文件异常（跳过校验）: %s", e)
        return True

    h = hashlib.sha256()
    try:
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        log.warning("[self-update] 读取下载文件失败（跳过校验）: %s", e)
        return True

    actual = h.hexdigest().lower()
    if actual != expected:
        log.error("[self-update] app.zip sha256 不匹配（期望 %s, 实际 %s）", expected, actual)
        return False
    log.info("[self-update] app.zip sha256 校验通过")
    return True


def _update_updater_from_remote(
    plan: LaunchPlan,
    proxies: Optional[dict] = None,
    progress_cb=None,  # Optional[Callable[[str], None]]
) -> bool:
    """尝试从远端 app part zip 提取并替换本地 Updater.exe。

    解决鸡生蛋问题：已分发的旧 Updater.exe 没有自更新逻辑，无法更新自身。
    主程序在启动 updater 之前调用本函数，先拉取 app part zip，提取新的
    Updater.exe 并替换本地版本。这样即使旧 updater 没有自更新代码，
    也能通过主程序间接触发更新。

    流程：
    1. 先拉取 manifest JSON（小文件），对比 ``parts.app.sha256`` 与本地
       ``.installed_manifest.json`` 中记录的值；一致则 Updater.exe 未变，
       直接返回，**不发起任何 zip 下载**。
    2. sha256 不一致（或本地无清单）时，才下载 app.zip，并验证其 sha256。
    3. 从 zip 中提取新 Updater.exe 并替换。

    返回 ``True`` 表示成功更新了 Updater.exe（或已是最新），``False`` 表示
    失败但不影响后续流程（降级使用旧 updater）。

    ``progress_cb`` 若提供，会在下载过程中以人类可读字符串回调（用于 UI 更新显示）。
    """
    import requests

    app_dir = plan.app_dir
    local_updater = app_dir / UPDATER_EXE_NAME
    proxies_dict = {"http": plan.proxy_url, "https": plan.proxy_url} if plan.proxy_url else None

    # ── Step 1: 先用 manifest sha256 判断 Updater.exe 是否需要更新 ─────────────
    # 拉取远端 manifest（小 JSON），读取本地清单，对比 app part sha256。
    # 一致 → Updater.exe 未变化 → 直接返回，完全不下载 app.zip。
    remote_manifest = _fetch_remote_manifest(plan, proxies_dict)
    local_manifest = _read_local_manifest(plan)

    remote_app_sha = ""
    local_app_sha = ""
    if remote_manifest:
        remote_app_sha = (remote_manifest.get("parts", {}).get("app") or {}).get("sha256", "")
    if local_manifest:
        local_app_sha = (local_manifest.get("parts", {}).get("app") or {}).get("sha256", "")

    if remote_app_sha and local_app_sha:
        if remote_app_sha == local_app_sha:
            log.info(
                "[self-update] app sha256 一致（%s…），Updater.exe 无需更新，跳过下载",
                remote_app_sha[:12],
            )
            return True
        log.info(
            "[self-update] app sha256 不同（本地 %s…, 远端 %s…），需更新 Updater.exe",
            local_app_sha[:12],
            remote_app_sha[:12],
        )
    else:
        if not remote_app_sha:
            log.info("[self-update] 无法获取远端 manifest sha256，尝试下载 app.zip 兜底")
        else:
            log.info("[self-update] 本地无安装清单（首次升级），下载 app.zip 更新 Updater.exe")

    # ── Step 2: 下载 app.zip ─────────────────────────────────────────────────
    # 直接落到 Updater 的 parts 工作目录（%TEMP%/StrangeUtaGameUpdater/parts/），
    # 与 Updater 走增量更新时的落盘路径完全一致。
    # 成功下载并校验后不删除该文件——Updater 启动后发现文件已存在，会发 Range
    # 请求确认完整性（服务器返回 HTTP 416），然后直接走本地内容哈希校验，
    # 完全跳过重新下载，彻底消除对同一文件的二次下载浪费。
    # app part zip 名从全量 asset name 派生：
    # "StrangeUtaGame-noWinIME-v1.0.3.zip" → "StrangeUtaGame-noWinIME-v1.0.3-app.zip"
    app_zip_name = plan.asset_name.replace(
        f"-v{plan.target_version}.zip", f"-v{plan.target_version}-app.zip"
    )
    shared_parts_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME / "parts"
    try:
        shared_parts_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    canonical_zip = shared_parts_dir / app_zip_name

    candidates: List[Tuple[str, str]] = []
    for source_id, url in plan.download_urls:
        prefix = url.rsplit("/", 1)[0]
        candidates.append((source_id, f"{prefix}/{app_zip_name}"))

    _RETRY_COUNT = 3
    _RETRY_INTERVAL = 2.0
    _last_source_id: Optional[str] = None

    for source_id, url in candidates:
        log.info("[self-update] 尝试下载 app part: %s", url)
        try:
            import time as _time

            # 切换到新源时删除上一个源的残留文件，防止跨源数据混合导致 zip 损坏
            if _last_source_id is not None and canonical_zip.exists():
                log.info("[self-update] 切换源 [%s] → [%s]，删除残留部分文件",
                         _last_source_id, source_id)
                try:
                    canonical_zip.unlink()
                except OSError as _e:
                    log.warning("[self-update] 删除残留文件失败: %s", _e)
            _last_source_id = source_id

            last_err = ""
            downloaded = False
            for attempt in range(1, _RETRY_COUNT + 1):
                try:
                    with requests.get(
                        url,
                        headers={"User-Agent": "StrangeUtaGame-MainApp/self-update"},
                        proxies=proxies_dict,
                        timeout=(10, 60),
                        allow_redirects=True,
                        stream=True,
                    ) as resp:
                        if resp.status_code != 200:
                            last_err = f"HTTP {resp.status_code}"
                            log.warning("[self-update] %s from %s (attempt %d/%d)",
                                        last_err, source_id, attempt, _RETRY_COUNT)
                            if attempt < _RETRY_COUNT:
                                _time.sleep(_RETRY_INTERVAL)
                            continue
                        total = int(resp.headers.get("Content-Length") or 0)
                        done = 0
                        last_pct = -1
                        with open(canonical_zip, "wb") as f:
                            for chunk in resp.iter_content(chunk_size=64 * 1024):
                                if chunk:
                                    f.write(chunk)
                                    done += len(chunk)
                                    if total > 0 and progress_cb is not None:
                                        pct = int(done * 100 / total)
                                        if pct >= last_pct + 10:
                                            last_pct = pct
                                            progress_cb(
                                                f"正在获取最新更新器… "
                                                f"{pct}%  "
                                                f"({done / 1024 / 1024:.1f} / "
                                                f"{total / 1024 / 1024:.1f} MB)"
                                            )
                    downloaded = True
                    break
                except requests.RequestException as e:
                    last_err = f"网络错误: {e}"
                    log.warning("[self-update] %s from %s (attempt %d/%d)",
                                last_err, source_id, attempt, _RETRY_COUNT)
                    if attempt < _RETRY_COUNT:
                        _time.sleep(_RETRY_INTERVAL)

            if not downloaded:
                log.warning("[self-update] 源 %s 全部重试失败: %s", source_id, last_err)
                # 删除可能损坏的部分文件
                try:
                    canonical_zip.unlink()
                except OSError:
                    pass
                continue

            # ── Step 3: 校验 app.zip sha256 ─────────────────────────────────
            if not _verify_zip_sha256(str(canonical_zip), url, proxies_dict):
                log.warning("[self-update] app.zip sha256 校验失败，放弃此源")
                try:
                    canonical_zip.unlink()
                except OSError:
                    pass
                continue

            # ── Step 4: 提取 Updater.exe ─────────────────────────────────────
            with zipfile.ZipFile(str(canonical_zip)) as zf:
                updater_entry = None
                for name in zf.namelist():
                    if name.endswith(UPDATER_EXE_NAME) and not name.endswith("/"):
                        updater_entry = name
                        break
                if updater_entry is None:
                    log.warning("[self-update] app part zip 中未找到 %s，放弃此源", UPDATER_EXE_NAME)
                    try:
                        canonical_zip.unlink()
                    except OSError:
                        pass
                    continue

                new_bytes = zf.read(updater_entry)

            # 双重保险：字节级对比（应对 manifest 不可用时的兜底路径）
            if local_updater.exists() and local_updater.read_bytes() == new_bytes:
                log.info(
                    "[self-update] Updater.exe 字节一致，无需替换；"
                    "app.zip 已保留在 %s 供 Updater 增量复用", canonical_zip,
                )
                return True

            # 写入新 Updater.exe（带重试：Windows 下句柄释放可能有短暂延迟）
            import time as _time
            for _attempt in range(3):
                try:
                    local_updater.write_bytes(new_bytes)
                    break
                except PermissionError:
                    if _attempt < 2:
                        _time.sleep(1.0)
                    else:
                        raise
            log.info(
                "[self-update] 已更新 Updater.exe（%d bytes）；"
                "app.zip 保留在 %s，Updater 增量更新时将直接复用，无需重复下载",
                len(new_bytes), canonical_zip,
            )
            return True

        except Exception as e:
            log.warning("[self-update] 从 %s 下载/提取失败: %s", source_id, e)
            try:
                canonical_zip.unlink()
            except OSError:
                pass

    log.warning("[self-update] 所有源均失败，将使用旧版 Updater.exe")
    return False


def launch_updater(plan: LaunchPlan, progress_cb=None) -> LaunchResult:
    """根据 ``plan`` 启动独立 Updater.exe；调用后调用方应立刻退出 Qt 应用。

    返回 :class:`LaunchResult`；``launched=False`` 时由调用方提示用户。

    ``progress_cb`` 若提供（``Callable[[str], None]``），会在自更新 Updater 的
    下载阶段以人类可读的进度字符串回调，调用方可据此刷新 UI 提示。
    """
    updater = find_updater_exe(plan.app_dir)
    if updater is None:
        return LaunchResult(
            launched=False,
            reason=(
                "未找到 Updater.exe。请重新下载完整安装包，或确保 "
                "Updater.exe 与主程序位于同一目录。"
            ),
        )

    # 先尝试从远端更新 Updater.exe 自身（解决旧 updater 无自更新逻辑的问题）
    try:
        _update_updater_from_remote(plan, progress_cb=progress_cb)
        # 重新定位（可能已被更新）
        updater = find_updater_exe(plan.app_dir) or updater
    except Exception as e:
        log.warning("自更新 Updater.exe 失败（忽略，继续使用旧版）: %s", e)

    try:
        temp_copy = _copy_updater_to_temp(updater)
    except OSError as e:
        return LaunchResult(
            launched=False,
            updater_path=str(updater),
            reason=f"无法复制 Updater.exe 到临时目录: {e}",
        )

    args = plan.command_args(temp_copy, os.getpid())

    # Windows 下，必须给 Updater 一个**可见的新控制台**（不能用 DETACHED_PROCESS）：
    #
    # * ``DETACHED_PROCESS (0x08)``  会让进程完全无控制台，print/log 全部消失 —— 用户
    #   什么都看不到，万一报错也无从排查。
    # * ``CREATE_NEW_CONSOLE (0x10)`` 为新进程开一个独立 cmd 窗口（与主程序解耦），
    #   用户能实时看到下载进度与错误信息。这才符合"控制台 UI Updater"的设计意图。
    # * ``CREATE_NEW_PROCESS_GROUP (0x200)`` 让新进程独立于父进程的进程组，
    #   主程序退出时不会连带把 Updater 杀掉。
    flags = 0
    if sys.platform == "win32":
        flags = 0x00000010 | 0x00000200  # CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP

    try:
        proc = subprocess.Popen(  # noqa: S603 — 受信任的本地 EXE
            args,
            close_fds=True,
            cwd=str(plan.app_dir),
            creationflags=flags,
            # 不接管 Updater 的 stdio —— 让它的新控制台自己管，否则即便有窗口也看不到内容。
            stdin=None,
            stdout=None,
            stderr=None,
        )
    except OSError as e:
        return LaunchResult(
            launched=False,
            updater_path=str(updater),
            temp_copy_path=str(temp_copy),
            reason=f"启动 Updater 失败: {e}",
        )

    return LaunchResult(
        launched=True,
        updater_path=str(updater),
        temp_copy_path=str(temp_copy),
        pid=proc.pid,
    )


def is_updater_available() -> bool:
    """便利方法：用于 UI 决定"立即更新"按钮是否可用。"""
    return find_updater_exe() is not None