"""StrangeUtaGame Updater 入口（独立可执行）。

调用约定（由主程序的 ``installer.py`` 构造命令行）：

.. code::

    Updater.exe
        --app-dir <主程序所在目录>
        --app-exe <主程序 EXE 文件名>
        --target-version <X.Y.Z>
        --target-tag <SUGvX.Y.Z>
        --asset-name <StrangeUtaGame-vX.Y.Z.zip>
        --internal-name <_internal>
        --pid <主程序 PID>
        --url <source_id|url>     (允许重复)
        [--proxy http://127.0.0.1:port]
        [--sha256 <十六进制摘要>]
        [--no-launch]

执行流程：

1. 等待主程序 PID 退出（最长 30 秒）
2. 按 ``--url`` 顺序尝试下载 zip 到 ``%TEMP%/StrangeUtaGameUpdater/download``
3. （可选）校验 SHA-256
4. 解压到 ``%TEMP%/StrangeUtaGameUpdater/extracted/<topdir>``
5. 备份 ``<app_dir>/_internal`` 至 ``<app_dir>/_internal.bak`` —— 失败回滚
6. 覆盖 ``StrangeUtaGame.exe`` 与 ``_internal/`` 至 ``<app_dir>``
7. 启动新版本主程序（除非 ``--no-launch``）
8. 清理临时目录后退出

任何步骤失败均执行尽量保守的回滚，并把日志写到
``%TEMP%/StrangeUtaGameUpdater/updater.log`` 以及标准输出。
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 使用 UTF-8 —— 避免 Windows 控制台默认 cp1252/cp936 时
    在打包后的 Updater.exe 中 ``print/log`` 抛 ``UnicodeEncodeError``。"""
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

LOG_FORMAT = "[%(asctime)s] %(levelname)s %(message)s"
DATE_FORMAT = "%H:%M:%S"

TMP_DIR_NAME = "StrangeUtaGameUpdater"
CHUNK_SIZE = 128 * 1024
DEFAULT_USER_AGENT = "StrangeUtaGame-Updater/standalone"

# 等待主程序退出的总时长（秒）
WAIT_PID_TIMEOUT = 30.0
# tasklist 探测到 PID 消失后，再宽限多久让 Windows 完全释放 DLL/_internal 文件句柄。
# 即便主进程已"退出"，Win 内核清理 DLL 句柄、Defender 实时扫描等都可能让短时间内的
# 文件操作返回 Access Denied。
POST_EXIT_GRACE_SECONDS = 2.0
# 备份 / 覆盖 _internal 时遇到 PermissionError 的最大重试次数与间隔。
FILE_LOCK_RETRY_COUNT = 6
FILE_LOCK_RETRY_INTERVAL = 1.5

# 本地已安装版本/分包指纹的存放位置（相对 ``--app-dir/<internal_name>/``）。
LOCAL_MANIFEST_FILENAME = ".installed_manifest.json"
# manifest schema 兼容版本（远端 manifest.schema 必须 <= 该值才走增量；否则降级全量）。
SUPPORTED_MANIFEST_SCHEMA = 1
# Updater 自身的文件名；自更新时 rename 为 ``<name>.old``，下次启动时清理。
UPDATER_EXE_NAME = "Updater.exe"


# ───────────────────────── 数据结构 ─────────────────────────


@dataclass
class Args:
    app_dir: Path
    app_exe: str
    target_version: str
    target_tag: str
    asset_name: str
    internal_name: str
    pid: int
    urls: List[Tuple[str, str]]
    proxy_url: str
    sha256: str
    launch_after: bool


# ───────────────────────── 命令行解析 ─────────────────────────


def parse_args(argv: Optional[List[str]] = None) -> Args:
    p = argparse.ArgumentParser(
        prog="StrangeUtaGame Updater",
        description="替换 StrangeUtaGame.exe 与 _internal/ 下的文件，并重启应用。",
    )
    p.add_argument("--app-dir", required=True, type=Path)
    p.add_argument("--app-exe", required=True, type=str)
    p.add_argument("--target-version", required=True, type=str)
    p.add_argument("--target-tag", required=True, type=str)
    p.add_argument("--asset-name", required=True, type=str)
    p.add_argument("--internal-name", default="_internal", type=str)
    p.add_argument("--pid", required=True, type=int)
    p.add_argument(
        "--url",
        dest="urls",
        action="append",
        default=[],
        help='下载候选 URL，格式 "source_id|https://..."，可重复',
    )
    p.add_argument("--proxy", dest="proxy_url", default="", type=str)
    p.add_argument("--sha256", dest="sha256", default="", type=str)
    p.add_argument(
        "--no-launch",
        dest="launch_after",
        action="store_false",
        default=True,
    )
    ns = p.parse_args(argv)

    urls: List[Tuple[str, str]] = []
    for raw in ns.urls or []:
        s = str(raw)
        if "|" not in s:
            urls.append(("unknown", s))
            continue
        sid, url = s.split("|", 1)
        urls.append((sid.strip() or "unknown", url.strip()))

    return Args(
        app_dir=Path(ns.app_dir).resolve(),
        app_exe=str(ns.app_exe),
        target_version=str(ns.target_version),
        target_tag=str(ns.target_tag),
        asset_name=str(ns.asset_name),
        internal_name=str(ns.internal_name),
        pid=int(ns.pid),
        urls=urls,
        proxy_url=str(ns.proxy_url or "").strip(),
        sha256=str(ns.sha256 or "").strip().lower(),
        launch_after=bool(ns.launch_after),
    )


# ───────────────────────── 日志 ─────────────────────────


def setup_logger(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("sug.updater")
    logger.setLevel(logging.INFO)
    # 控制台
    ch = logging.StreamHandler(stream=sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(ch)
    # 文件
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(str(log_path), mode="w", encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
        logger.addHandler(fh)
    except OSError:
        pass
    return logger


# ───────────────────────── 流程步骤 ─────────────────────────


def _cleanup_old_files(app_dir: Path, log: logging.Logger) -> None:
    """清理上次成功更新后遗留的 ``*.old`` 备份文件/目录。

    Windows 允许 rename 运行中的 exe，但不允许 delete/overwrite。
    自更新流程把旧 Updater.exe rename 为 .old，更新完成后也会留下主程序的
    .old 备份（更新失败时保留以便手动恢复）。这里在下次启动时做统一清理。

    安全策略：只有当对应的"无 .old 后缀"版本已存在时才删除备份，
    避免在上次更新成功但备份未清理干净的极端情况下把唯一副本删掉。
    """
    for p in app_dir.glob("*.old"):
        orig = p.with_suffix("")   # e.g. "StrangeUtaGame.exe.old" → "StrangeUtaGame.exe"
        if not orig.exists():
            log.info("保留备份（对应原始文件不存在，可能上次回滚未完成）: %s", p.name)
            continue
        try:
            if p.is_dir():
                shutil.rmtree(str(p), ignore_errors=True)
            else:
                p.unlink()
            log.info("已清理旧备份: %s", p.name)
        except OSError as e:
            log.warning("清理旧备份 %s 失败（可忽略）: %s", p.name, e)


def wait_for_pid_exit(pid: int, log: logging.Logger, timeout: float = WAIT_PID_TIMEOUT) -> bool:
    """等待指定 PID 退出，并在其后宽限 :data:`POST_EXIT_GRACE_SECONDS` 秒。"""
    log.info("等待主程序退出 (PID=%d)...", pid)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _is_pid_alive(pid):
            log.info("主程序进程已结束")
            # 关键：tasklist 报告 PID 消失，并不等于 Windows 已经释放 DLL/_internal 的
            # 文件句柄。给 OS 一点宽限时间，否则后续 rename _internal 会拿到 ERROR_ACCESS_DENIED。
            log.info("等待文件句柄释放（%.1fs）...", POST_EXIT_GRACE_SECONDS)
            time.sleep(POST_EXIT_GRACE_SECONDS)
            return True
        time.sleep(0.4)
    log.warning("等待主程序退出超时 (%.0fs)，将强制继续", timeout)
    return False


def _retry_on_permission_error(
    op_desc: str,
    func,  # type: ignore[no-untyped-def]
    log: logging.Logger,
    max_retries: int = FILE_LOCK_RETRY_COUNT,
    interval: float = FILE_LOCK_RETRY_INTERVAL,
):  # type: ignore[no-untyped-def]
    """在遇到 PermissionError / WinError 5 时重试给定操作。

    Windows 的文件锁释放是异步的：主进程"退出"后，DLL 句柄可能仍被内核挂着
    一两秒；杀毒软件也会临时锁住新文件。多次重试通常能在几秒内成功。
    """
    last_exc: BaseException = OSError("no attempt made")
    for attempt in range(1, max_retries + 1):
        try:
            return func()
        except PermissionError as e:
            last_exc = e
        except OSError as e:
            # WinError 5 (拒绝访问) / 32 (文件被占用) 同样视为可重试
            if getattr(e, "winerror", None) in (5, 32):
                last_exc = e
            else:
                raise
        log.warning(
            "%s 第 %d/%d 次失败：%s；%.1fs 后重试…",
            op_desc, attempt, max_retries, last_exc, interval,
        )
        time.sleep(interval)
    raise last_exc


def _is_pid_alive(pid: int) -> bool:
    """检测 PID 是否仍存活（Windows 用 tasklist 简单实现）。"""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            out = subprocess.check_output(  # noqa: S603
                ["tasklist", "/FI", f"PID eq {pid}"],
                stderr=subprocess.DEVNULL,
                creationflags=0x08000000,  # CREATE_NO_WINDOW
                timeout=5,
            )
            return str(pid).encode() in out
        except Exception:
            return False
    # POSIX 兜底
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# 下载失败重试次数
DOWNLOAD_RETRY_COUNT = 3
# 下载重试间隔（秒）
DOWNLOAD_RETRY_INTERVAL = 2.0


def download_one(
    url: str,
    dest: Path,
    proxies: Optional[dict],
    log: logging.Logger,
) -> Tuple[bool, str]:
    """下载一个 URL；支持断点续传和重试；返回 ``(ok, error_message)``。"""
    last_error = ""
    for attempt in range(1, DOWNLOAD_RETRY_COUNT + 1):
        try:
            # 检查本地是否已有部分下载的文件
            existing_size = 0
            if dest.exists():
                existing_size = dest.stat().st_size
                if attempt == 1:
                    log.info("  发现已下载部分: %.1f MB，尝试续传", existing_size / 1024 / 1024)

            # 构建请求头，支持断点续传
            headers = {"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"}
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            with requests.get(
                url,
                headers=headers,
                stream=True,
                proxies=proxies,
                timeout=(10, 60),
                allow_redirects=True,
            ) as resp:
                # 处理服务器不支持 Range 请求的情况
                if existing_size > 0 and resp.status_code == 200:
                    # 服务器不支持 Range 请求，重新下载
                    log.info("  服务器不支持断点续传，重新下载")
                    existing_size = 0
                elif resp.status_code == 206:
                    # 服务器支持 Range 请求，继续下载
                    if attempt == 1:
                        log.info("  服务器支持断点续传，从 %.1f MB 处继续", existing_size / 1024 / 1024)
                elif existing_size > 0 and resp.status_code == 416:
                    # HTTP 416 Range Not Satisfiable：请求的起始偏移超出文件大小，
                    # 说明本地文件已等于或超过服务端文件大小，视为下载完成。
                    log.info("  收到 HTTP 416，本地文件已完整（%.1f MB），跳过下载",
                             existing_size / 1024 / 1024)
                    return True, ""
                elif resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < DOWNLOAD_RETRY_COUNT:
                        log.warning("  下载失败: %s，%.1fs 后重试 (%d/%d)",
                                   last_error, DOWNLOAD_RETRY_INTERVAL, attempt, DOWNLOAD_RETRY_COUNT)
                        time.sleep(DOWNLOAD_RETRY_INTERVAL)
                        continue
                    return False, last_error

                total = int(resp.headers.get("Content-Length") or 0)
                # 如果是续传，total 是剩余大小，需要加上已下载的
                if existing_size > 0 and resp.status_code == 206:
                    total += existing_size
                done = existing_size
                last_pct = -1
                dest.parent.mkdir(parents=True, exist_ok=True)

                # 以追加模式打开文件（如果是续传）或写入模式（如果是新下载）
                mode = "ab" if existing_size > 0 and resp.status_code == 206 else "wb"
                with open(dest, mode) as f:
                    for chunk in resp.iter_content(CHUNK_SIZE):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if total > 0:
                            pct = int(done * 100 / total)
                            if pct >= last_pct + 5:
                                log.info("  下载中: %3d%%  (%.1f / %.1f MB)",
                                         pct, done / 1024 / 1024, total / 1024 / 1024)
                                last_pct = pct
            return True, ""
        except requests.RequestException as e:
            last_error = f"网络异常: {e}"
        except OSError as e:
            last_error = f"写文件失败: {e}"

        if attempt < DOWNLOAD_RETRY_COUNT:
            log.warning("  下载失败: %s，%.1fs 后重试 (%d/%d)",
                       last_error, DOWNLOAD_RETRY_INTERVAL, attempt, DOWNLOAD_RETRY_COUNT)
            time.sleep(DOWNLOAD_RETRY_INTERVAL)
        else:
            log.error("  下载失败，已重试 %d 次: %s", DOWNLOAD_RETRY_COUNT, last_error)

    return False, last_error


def try_download_from_sources(
    args: Args,
    download_path: Path,
    log: logging.Logger,
) -> tuple[bool, str]:
    """逐个尝试 ``args.urls`` 下载 zip；返回 ``(成功?, 命中的 URL)``。"""
    proxies = {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None
    if proxies:
        log.info("使用代理: %s", args.proxy_url)
    last_source_id = None
    for source_id, url in args.urls:
        # 切换到新源时，删除上一个源留下的部分文件，防止跨源数据拼接导致 zip 损坏
        if last_source_id is not None and download_path.exists():
            log.info("切换源 [%s] → [%s]，删除残留部分文件以防跨源数据混合", last_source_id, source_id)
            try:
                download_path.unlink()
            except OSError as e:
                log.warning("删除部分文件失败: %s", e)
        last_source_id = source_id
        log.info("[%s] 尝试下载: %s", source_id, url)
        ok, err = download_one(url, download_path, proxies, log)
        if ok:
            log.info("[%s] 下载成功 (%.1f MB)",
                     source_id, download_path.stat().st_size / 1024 / 1024)
            return True, url
        log.warning("[%s] 失败: %s", source_id, err)
    return False, ""


def try_fetch_manifest(
    args: Args,
    log: logging.Logger,
) -> Optional[Dict[str, Any]]:
    """按 ``args.urls`` 顺序尝试拉取 ``manifest-vX.Y.Z.json``。

    URL 推导规则：把 asset_name 中的 ``StrangeUtaGame`` 前缀换成 ``manifest``，
    ``.zip`` 换成 ``.json``，从而支持多变体（noWinIME / mac 等）。
    例：``StrangeUtaGame-noWinIME-v1.0.3.zip`` → ``manifest-noWinIME-v1.0.3.json``。

    GitHub Release 把同 tag 下所有 assets 放同目录，镜像源透传同样的路径，所以
    这个规则对三个源都成立。

    任何失败（HTTP 错 / JSON 错 / schema 不兼容）返回 ``None`` —— 上游会优雅
    降级到全量更新流程。
    """
    if not args.urls:
        return None
    proxies = {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None

    manifest_filename = args.asset_name.replace("StrangeUtaGame", "manifest", 1).replace(".zip", ".json")

    for source_id, zip_url in args.urls:
        prefix = zip_url.rsplit("/", 1)[0]
        manifest_url = f"{prefix}/{manifest_filename}"
        log.info("[%s] 尝试拉取 manifest: %s", source_id, manifest_url)
        try:
            resp = requests.get(
                manifest_url,
                headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/json"},
                proxies=proxies,
                timeout=(5, 15),
                allow_redirects=True,
            )
        except requests.RequestException as e:
            log.warning("[%s] manifest 拉取异常: %s", source_id, e)
            continue
        if resp.status_code != 200:
            log.warning("[%s] manifest HTTP %d（该源可能未上传 manifest）",
                        source_id, resp.status_code)
            continue
        try:
            data = resp.json()
        except ValueError as e:
            log.warning("[%s] manifest 不是合法 JSON: %s", source_id, e)
            continue
        schema = int(data.get("schema", 0))
        if schema > SUPPORTED_MANIFEST_SCHEMA:
            log.warning(
                "[%s] manifest schema=%d 超出当前 Updater 支持版本 %d，回退全量",
                source_id, schema, SUPPORTED_MANIFEST_SCHEMA,
            )
            return None
        if not isinstance(data.get("parts"), dict) or not data["parts"]:
            log.warning("[%s] manifest 缺少 parts 字段", source_id)
            continue
        log.info("[%s] manifest 解析成功（version=%s, parts=%s）",
                 source_id, data.get("version"), ",".join(data["parts"].keys()))
        # 记录命中的 source，供后续下载 part 时优先选择
        data["_source_id"] = source_id
        data["_url_prefix"] = prefix
        return data
    return None


def try_fetch_sha256(success_url: str, proxies: Optional[dict], log: logging.Logger) -> str:
    """主动尝试拉取与 zip 同源的 ``<url>.sha256`` 文件并解析摘要。

    发布流程会在 zip 同目录上传 ``StrangeUtaGame-vX.Y.Z.zip.sha256`` 资产（格式
    ``<64位hex>  文件名\\n``，coreutils ``sha256sum`` 兼容）。本函数：

    * 用 ``<成功的 zip URL> + ".sha256"`` 拼接 sha256 URL —— 因为 GitHub Release
      所有资产都在同一目录下，镜像源（ghproxy / fastgit）也透传相同路径；
    * 取首个连续 64 位十六进制子串作为摘要，对换行 / 行尾空格 / 大小写宽容；
    * 任何失败都返回 ``""``，由上游降级为"跳过校验"。
    """
    if not success_url:
        return ""
    sha_url = success_url + ".sha256"
    log.info("尝试拉取 SHA-256 校验: %s", sha_url)
    try:
        resp = requests.get(
            sha_url,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "*/*"},
            proxies=proxies,
            timeout=(5, 15),
            allow_redirects=True,
        )
    except requests.RequestException as e:
        log.warning("SHA-256 拉取失败（将跳过校验）: %s", e)
        return ""
    if resp.status_code != 200:
        log.warning("SHA-256 文件 HTTP %s（将跳过校验）", resp.status_code)
        return ""
    import re as _re
    m = _re.search(r"\b([0-9a-fA-F]{64})\b", resp.text)
    if not m:
        log.warning("SHA-256 文件内容无法解析（将跳过校验）")
        return ""
    digest = m.group(1).lower()
    log.info("拿到 SHA-256: %s", digest)
    return digest


def verify_sha256(file_path: Path, expected_hex: str, log: logging.Logger) -> bool:
    if not expected_hex:
        log.info("未提供 SHA-256，跳过校验")
        return True
    log.info("校验 SHA-256 中...")
    h = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(64 * 1024), b""):
                h.update(chunk)
    except OSError as e:
        log.error("读取下载文件失败: %s", e)
        return False
    actual = h.hexdigest().lower()
    if actual != expected_hex.lower():
        log.error("SHA-256 不匹配（期望 %s，实际 %s）", expected_hex, actual)
        return False
    log.info("SHA-256 校验通过")
    return True


def _content_hash_of_zip(zip_path: Path) -> str:
    """计算 zip 内所有文件的内容哈希（确定性，不受打包元数据影响）。"""
    entries = []
    with zipfile.ZipFile(str(zip_path), "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            content = zf.read(info.filename)
            content_hash = hashlib.sha256(content).hexdigest()
            entries.append((info.filename, content_hash))
    entries.sort(key=lambda e: e[0])
    combined = "\n".join(f"{name}:{h}" for name, h in entries)
    return hashlib.sha256(combined.encode("ascii")).hexdigest().lower()


def verify_content_hash(zip_path: Path, expected_hex: str, log: logging.Logger) -> bool:
    """校验 zip 文件的内容哈希（对 zip 内文件的路径+内容计算 sha256）。"""
    if not expected_hex:
        log.info("未提供内容哈希，跳过校验")
        return True
    log.info("校验内容哈希中...")
    actual = _content_hash_of_zip(zip_path)
    if actual != expected_hex.lower():
        log.error("内容哈希不匹配（期望 %s，实际 %s）", expected_hex, actual)
        return False
    log.info("内容哈希校验通过")
    return True


def extract_archive(
    archive: Path,
    extract_dir: Path,
    log: logging.Logger,
) -> Optional[Path]:
    """解压 zip；返回解压根目录（如果 zip 内有单一顶层目录则返回它，否则就是 ``extract_dir``）。"""
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    log.info("解压: %s → %s", archive.name, extract_dir)

    if archive.suffix.lower() != ".zip":
        log.error("当前 Updater 仅支持 .zip 格式（收到 %s）。"
                  "若仓库发布的是 .rar，请改为发布 .zip。", archive.suffix)
        return None

    try:
        with zipfile.ZipFile(str(archive)) as zf:
            zf.extractall(str(extract_dir))
    except (zipfile.BadZipFile, OSError) as e:
        log.error("解压失败: %s", e)
        return None

    # 单一顶层目录探测
    entries = [p for p in extract_dir.iterdir() if not p.name.startswith(".")]
    if len(entries) == 1 and entries[0].is_dir():
        log.info("检测到单一顶层目录: %s", entries[0].name)
        return entries[0]
    return extract_dir


def apply_update(
    app_dir: Path,
    app_exe: str,
    internal_name: str,
    new_root: Path,
    log: logging.Logger,
) -> Tuple[bool, str]:
    """把 ``new_root`` 中的内容应用到 ``app_dir``。"""
    new_exe = new_root / app_exe
    new_internal = new_root / internal_name

    # 容错：有些发布把所有文件平铺在 new_root，没有 _internal 子目录 —— 这种情况说明源包不完整
    if not new_exe.exists():
        return False, f"更新包中找不到 {app_exe}"
    if not new_internal.exists() or not new_internal.is_dir():
        return False, f"更新包中找不到 {internal_name}/"

    # 备份 _internal —— 用重试包裹，应对 Windows 异步释放 DLL 句柄的常见延迟
    backup_internal = app_dir / f"{internal_name}.old"
    cur_internal = app_dir / internal_name
    if backup_internal.exists():
        log.info("清理旧备份: %s", backup_internal)
        shutil.rmtree(backup_internal, ignore_errors=True)
    if cur_internal.exists():
        log.info("备份 %s → %s", cur_internal.name, backup_internal.name)
        try:
            _retry_on_permission_error(
                f"备份 {internal_name}",
                lambda: os.rename(str(cur_internal), str(backup_internal)),
                log,
            )
        except OSError as e:
            return False, (
                f"备份 {internal_name} 失败: {e}（主程序可能仍未完全释放文件句柄）"
            )

    # 备份 EXE（保存为 .old，更新成功后删除，失败时可用于恢复）
    cur_exe = app_dir / app_exe
    backup_exe = app_dir / f"{app_exe}.old"
    if backup_exe.exists():
        try:
            backup_exe.unlink()
        except OSError:
            pass
    exe_was_present = cur_exe.exists()
    if exe_was_present:
        log.info("备份 %s → %s", cur_exe.name, backup_exe.name)
        try:
            _retry_on_permission_error(
                "备份 EXE",
                lambda: os.rename(str(cur_exe), str(backup_exe)),
                log,
            )
        except OSError as e:
            # 回滚 _internal
            try:
                if backup_internal.exists() and not cur_internal.exists():
                    os.rename(str(backup_internal), str(cur_internal))
            except OSError:
                pass
            return False, f"备份 EXE 失败: {e}（主程序可能未完全退出）"

    # 自更新 Updater.exe：rename 为 .old（Windows 允许 rename 运行中的 exe），
    # 复制新的；下次启动时 _cleanup_old_files 会删除 .old。
    new_updater = new_root / UPDATER_EXE_NAME
    cur_updater = app_dir / UPDATER_EXE_NAME
    if new_updater.exists():
        if cur_updater.exists():
            log.info("自更新: rename %s → %s.old", UPDATER_EXE_NAME, UPDATER_EXE_NAME)
            try:
                os.rename(str(cur_updater), str(cur_updater.with_suffix(".exe.old")))
            except OSError as e:
                log.warning("rename %s 失败（可能未在运行，忽略）: %s", UPDATER_EXE_NAME, e)
        try:
            shutil.copy2(str(new_updater), str(cur_updater))
            log.info("已写入新 %s", UPDATER_EXE_NAME)
        except OSError as e:
            log.warning("写入新 %s 失败（不影响主程序更新）: %s", UPDATER_EXE_NAME, e)

    # 写入新内容 —— 同样带重试
    log.info("写入新 %s/", internal_name)
    try:
        _retry_on_permission_error(
            f"写入 {internal_name}",
            lambda: shutil.copytree(str(new_internal), str(cur_internal)),
            log,
        )
        log.info("写入新 %s", app_exe)
        _retry_on_permission_error(
            f"写入 {app_exe}",
            lambda: shutil.copy2(str(new_exe), str(cur_exe)),
            log,
        )
    except (OSError, shutil.Error) as e:
        log.error("写入新文件失败，尝试回滚: %s", e)
        # 回滚 _internal
        rollback_ok = True
        try:
            if cur_internal.exists():
                shutil.rmtree(str(cur_internal), ignore_errors=True)
            if backup_internal.exists():
                os.rename(str(backup_internal), str(cur_internal))
                log.info("已恢复 %s 备份", internal_name)
        except OSError as re:
            log.error("回滚 %s 失败: %s（备份保留在 %s）", internal_name, re, backup_internal)
            rollback_ok = False
        # 回滚 EXE
        try:
            if cur_exe.exists():
                cur_exe.unlink()
            if backup_exe.exists():
                os.rename(str(backup_exe), str(cur_exe))
                log.info("已恢复 %s 备份", app_exe)
        except OSError as re:
            log.error("回滚 %s 失败: %s（备份保留在 %s）", app_exe, re, backup_exe)
            rollback_ok = False
        if not rollback_ok:
            return False, (
                f"写入失败: {e}\n"
                f"回滚也遇到问题，旧版本备份文件：{backup_exe} / {backup_internal}\n"
                f"请手动将 .old 文件恢复为原文件名。"
            )
        return False, f"写入失败: {e}（旧版本已恢复）"

    # 删除备份（用户数据保留）
    try:
        if backup_internal.exists():
            shutil.rmtree(str(backup_internal), ignore_errors=True)
        if backup_exe.exists():
            backup_exe.unlink()
    except OSError as e:
        log.warning("清理备份时出错（不影响功能）: %s", e)

    return True, ""


def launch_main_app(app_dir: Path, app_exe: str, log: logging.Logger) -> bool:
    exe_path = app_dir / app_exe
    if not exe_path.exists():
        log.error("找不到主程序 EXE: %s", exe_path)
        return False
    log.info("启动新版本: %s", exe_path)
    try:
        # 启动主程序：主程序是 GUI 应用（PyInstaller --windowed），不需要新建控制台。
        # 同时与 Updater 解耦，避免我们关闭 Updater 控制台时把它一并杀掉。
        flags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            flags = 0x00000008 | 0x00000200
        subprocess.Popen(  # noqa: S603
            [str(exe_path)],
            cwd=str(app_dir),
            close_fds=True,
            creationflags=flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except OSError as e:
        log.error("启动主程序失败: %s", e)
        return False


# ───────────────────────── 本地清单读写 ─────────────────────────


def _local_manifest_path(args: Args) -> Path:
    return args.app_dir / args.internal_name / LOCAL_MANIFEST_FILENAME


def read_local_manifest(args: Args, log: logging.Logger) -> Optional[Dict[str, Any]]:
    p = _local_manifest_path(args)
    if not p.exists():
        log.info("本地 .installed_manifest.json 不存在（视为首次升级，走全量）")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("本地 manifest 读取失败：%s", e)
        return None


def write_local_manifest(args: Args, remote_manifest: Dict[str, Any], log: logging.Logger) -> None:
    """在更新成功后，把"当前已安装"的 part sha256 + targets 写到本地清单。

    ``targets`` 字段供下次增量更新时做"孤儿清理"：把上次存在但新版本不再包含的
    条目从磁盘删除，确保 runtime 缩小时用户磁盘占用也真正减少。
    """
    p = _local_manifest_path(args)
    payload = {
        "version": remote_manifest.get("version"),
        "schema": remote_manifest.get("schema", 1),
        "parts": {
            pid: {
                "sha256": pinfo.get("sha256", ""),
                "asset": pinfo.get("asset", ""),
                # 保存本次安装的 targets，供下次增量更新做孤儿清理
                "targets": list(pinfo.get("targets") or []),
            }
            for pid, pinfo in remote_manifest.get("parts", {}).items()
        },
        "installed_at": int(time.time()),
    }
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        log.info("已写入本地 manifest: %s", p)
    except OSError as e:
        log.warning("写本地 manifest 失败（不影响本次更新成功）: %s", e)


# ───────────────────────── 增量更新流程 ─────────────────────────


def _diff_parts(remote: Dict[str, Any], local: Optional[Dict[str, Any]]) -> List[str]:
    """返回需要更新的 part id 列表（按 manifest 的迭代顺序）。"""
    needed: List[str] = []
    local_parts = (local or {}).get("parts", {}) if isinstance(local, dict) else {}
    for pid, pinfo in remote.get("parts", {}).items():
        remote_sha = pinfo.get("sha256", "")
        local_sha = (local_parts.get(pid, {}) or {}).get("sha256", "")
        if remote_sha and remote_sha != local_sha:
            needed.append(pid)
    return needed


def _download_part(
    args: Args,
    manifest: Dict[str, Any],
    part_id: str,
    work_dir: Path,
    log: logging.Logger,
) -> Optional[Path]:
    """下载某个 part 的 zip 并校验内容哈希。

    URL 推导：用拉取 manifest 成功的源 URL 前缀拼 part.asset 文件名。同一 release
    下所有 asset 都在同一目录，URL 一定能拼出来。失败返回 ``None``。
    """
    proxies = {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None
    part_info = manifest["parts"][part_id]
    asset_name = part_info["asset"]
    expected_hash = (part_info.get("sha256") or "").lower()
    expected_size = int(part_info.get("size") or 0)

    # 优先用 manifest 命中源；失败再轮转所有源
    primary_prefix = manifest.get("_url_prefix", "")
    candidates: List[Tuple[str, str]] = []
    seen_urls: set = set()
    if primary_prefix:
        primary_url = f"{primary_prefix}/{asset_name}"
        candidates.append(("primary", primary_url))
        seen_urls.add(primary_url)
    for source_id, zip_url in args.urls:
        prefix = zip_url.rsplit("/", 1)[0]
        url = f"{prefix}/{asset_name}"
        if url not in seen_urls:
            seen_urls.add(url)
            candidates.append((source_id, url))

    dest = work_dir / "parts" / asset_name
    dest.parent.mkdir(parents=True, exist_ok=True)

    # 先校验本地已有文件：主程序自更新时可能已把同名 part zip 下载到此处。
    # 若内容哈希一致，直接复用，完全不发 HTTP 请求。
    if dest.exists() and expected_hash:
        if verify_content_hash(dest, expected_hash, log):
            log.info("part %s 本地已存在且内容哈希一致（%.1f MB），跳过下载",
                     part_id, dest.stat().st_size / 1024 / 1024)
            return dest
        log.info("part %s 本地文件哈希不匹配，重新下载", part_id)
        try:
            dest.unlink()
        except OSError:
            pass

    log.info("下载 part %s（预期 %.1f MB）", part_id, expected_size / 1024 / 1024 if expected_size else 0)
    last_src_id = None
    for src_id, url in candidates:
        # 切换到新源时，删除上一个源留下的部分文件，防止跨源数据拼接导致 zip 损坏
        if last_src_id is not None and dest.exists():
            log.info("  切换源 [%s] → [%s]，删除残留部分文件以防跨源数据混合", last_src_id, src_id)
            try:
                dest.unlink()
            except OSError as e:
                log.warning("  删除部分文件失败: %s", e)
        last_src_id = src_id
        log.info("  [%s] %s", src_id, url)
        ok, err = download_one(url, dest, proxies, log)
        if ok:
            break
        log.warning("  [%s] 失败: %s", src_id, err)
    else:
        log.error("所有源均下载失败：part=%s", part_id)
        return None

    if expected_hash and not verify_content_hash(dest, expected_hash, log):
        log.error("part %s 的内容哈希校验失败", part_id)
        return None
    return dest


def _apply_part(
    part_zip: Path,
    targets: List[str],
    app_dir: Path,
    work_dir: Path,
    part_id: str,
    log: logging.Logger,
) -> Tuple[bool, str]:
    """精确替换 part 管辖的 targets。

    流程：
    1. 把 part zip 解压到独立临时目录 ``work_dir/extract-<part>/``；
    2. 对每个 target（相对路径）：
        a) 如果 ``app_dir/<target>`` 已存在 → 重命名为 ``<target>.bak``；
        b) 把解压出的 ``<target>`` 复制到 ``app_dir/<target>``；
    3. 任何一步失败 → 全部回滚 .bak；
    4. 成功 → 清理 .bak 与解压目录。
    """
    extract_dir = work_dir / f"extract-{part_id}"
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)
    extract_dir.mkdir(parents=True, exist_ok=True)

    log.info("[%s] 解压 part 包...", part_id)
    try:
        with zipfile.ZipFile(str(part_zip)) as zf:
            zf.extractall(str(extract_dir))
    except (zipfile.BadZipFile, OSError) as e:
        return False, f"解压 part {part_id} 失败: {e}"

    backups: List[Tuple[Path, Path, bool]] = []  # (orig_path, backup_path, is_dir)

    def _rollback() -> None:
        log.warning("[%s] 回滚 …", part_id)
        for orig, bak, is_dir in reversed(backups):
            try:
                if orig.exists():
                    if is_dir:
                        shutil.rmtree(orig, ignore_errors=True)
                    else:
                        orig.unlink()
                if bak.exists():
                    os.rename(str(bak), str(orig))
            except OSError as e:
                log.error("[%s] 回滚 %s 失败: %s", part_id, orig.name, e)

    # 1) 备份 + 写入
    try:
        for rel in targets:
            new_src = extract_dir / rel
            if not new_src.exists():
                # zip 内没有这个 target —— 视为远端故意删除（极少见），跳过
                log.warning("[%s] part 包内缺少 %s，跳过", part_id, rel)
                continue

            orig = app_dir / rel
            is_dir = new_src.is_dir()

            # 自更新 Updater.exe：rename 为 .old 而非 .bak，下次启动时清理
            if rel == UPDATER_EXE_NAME:
                if orig.exists():
                    old_path = orig.with_suffix(".exe.old")
                    log.info("[%s] 自更新: rename %s → %s.old", part_id, UPDATER_EXE_NAME, UPDATER_EXE_NAME)
                    try:
                        os.rename(str(orig), str(old_path))
                    except OSError as e:
                        log.warning("[%s] rename %s 失败（忽略）: %s", part_id, UPDATER_EXE_NAME, e)
                try:
                    shutil.copy2(str(new_src), str(orig))
                    log.info("[%s] 已写入新 %s", part_id, UPDATER_EXE_NAME)
                except OSError as e:
                    log.warning("[%s] 写入新 %s 失败（不影响主程序更新）: %s", part_id, UPDATER_EXE_NAME, e)
                continue

            bak = app_dir / (rel + ".bak")
            # 清理可能残留的 .bak
            if bak.exists():
                try:
                    if bak.is_dir():
                        shutil.rmtree(bak, ignore_errors=True)
                    else:
                        bak.unlink()
                except OSError:
                    pass

            if orig.exists():
                _retry_on_permission_error(
                    f"备份 {rel}",
                    lambda o=orig, b=bak: os.rename(str(o), str(b)),
                    log,
                )
                backups.append((orig, bak, is_dir))

            # 写入新内容
            orig.parent.mkdir(parents=True, exist_ok=True)
            if is_dir:
                _retry_on_permission_error(
                    f"写入 {rel}/",
                    lambda s=new_src, o=orig: shutil.copytree(str(s), str(o)),
                    log,
                )
            else:
                _retry_on_permission_error(
                    f"写入 {rel}",
                    lambda s=new_src, o=orig: shutil.copy2(str(s), str(o)),
                    log,
                )
    except (OSError, shutil.Error) as e:
        log.error("[%s] 应用更新失败: %s", part_id, e)
        _rollback()
        return False, f"应用 part {part_id} 失败: {e}"

    # 2) 成功 → 清理备份
    for _orig, bak, is_dir in backups:
        try:
            if bak.exists():
                if is_dir:
                    shutil.rmtree(bak, ignore_errors=True)
                else:
                    bak.unlink()
        except OSError as e:
            log.warning("[%s] 清理备份 %s 失败（不影响功能）: %s", part_id, bak.name, e)

    # 3) 清理解压目录
    shutil.rmtree(extract_dir, ignore_errors=True)
    return True, ""


def run_incremental(
    args: Args,
    manifest: Dict[str, Any],
    work_dir: Path,
    log: logging.Logger,
) -> int:
    """基于 manifest 的增量更新主流程。失败时返回非 0，调用方会 fallback 到全量。"""
    log.info("=" * 60)
    log.info("尝试增量更新（manifest schema=%d）", manifest.get("schema", 1))
    log.info("=" * 60)

    local = read_local_manifest(args, log)
    needed = _diff_parts(manifest, local)
    if not needed:
        # 远端版本理应比本地新；走到这里只能说明 manifest sha 与本地完全相同 ——
        # 说明软件已经是最新版了，但我们能跑到这里说明远端版本号已经被判定 > 本地。
        # 安全起见仍然记录本地 manifest（覆盖式更新版本号），但不重启主程序也意义不大。
        log.info("所有 part 的 sha256 都已匹配，本地已是最新（异常路径，记录后退出）")
        write_local_manifest(args, manifest, log)
        return 0

    log.info("需要更新的 part: %s", ", ".join(needed))
    total_size = sum(int(manifest["parts"][p].get("size") or 0) for p in needed)
    log.info("增量下载量约：%.1f MB（全量包约 %.1f MB）",
             total_size / 1024 / 1024,
             int(manifest.get("full", {}).get("size") or 0) / 1024 / 1024)

    # 1) 全部下载完再开始应用（确保每个 part 都已落盘 + 校验通过，再动主程序文件）
    part_zips: List[Tuple[str, Path]] = []
    for pid in needed:
        zp = _download_part(args, manifest, pid, work_dir, log)
        if zp is None:
            return 31  # 增量下载失败 → caller fallback 全量
        part_zips.append((pid, zp))

    # 2) 依次应用每个 part（每个 part 内部已带备份/回滚）
    applied: List[str] = []
    for pid, zp in part_zips:
        targets = manifest["parts"][pid].get("targets") or []
        if not isinstance(targets, list) or not targets:
            log.error("[%s] manifest 缺少 targets，无法增量应用", pid)
            return 32
        ok, err = _apply_part(zp, targets, args.app_dir, work_dir, pid, log)
        if not ok:
            log.error("[%s] 应用失败：%s", pid, err)
            # 已应用的 part 没法回滚（备份已删），用户应用应能启动 —— 不致命，但
            # 后续走 fallback 全量也能修复
            return 33
        log.info("[%s] 应用成功", pid)
        applied.append(pid)

        # ── 孤儿清理：删除上次存在但新版本不再包含的 targets ────────────────────
        # 场景：runtime 缩小（某个库被删除），新 manifest.targets 里没有它，
        # _apply_part 不会主动删除旧文件。这里补做清理，让用户磁盘真正减负。
        #
        # 安全策略：
        #   - 只删除本地 manifest 里明确记录的 targets（不猜测任何其他路径）
        #   - 不删除新 targets 里仍然存在的条目（即便旧版也有）
        #   - 目录用 rmtree，文件用 unlink；任一失败都只 warning，不影响更新结果
        local_targets: List[str] = []
        if local and isinstance(local.get("parts"), dict):
            local_targets = list((local["parts"].get(pid) or {}).get("targets") or [])
        new_targets_set = set(targets)
        orphans = [t for t in local_targets if t not in new_targets_set]
        if orphans:
            log.info("[%s] 检测到 %d 个孤儿条目（旧版有、新版无），开始清理", pid, len(orphans))
            freed_bytes = 0
            for rel in orphans:
                victim = args.app_dir / rel
                if not victim.exists():
                    continue
                try:
                    size = sum(
                        f.stat().st_size for f in victim.rglob("*") if f.is_file()
                    ) if victim.is_dir() else victim.stat().st_size
                    if victim.is_dir():
                        shutil.rmtree(str(victim), ignore_errors=False)
                    else:
                        victim.unlink()
                    freed_bytes += size
                    log.info("[%s]   已删除: %s（%.1f MB）", pid, rel, size / 1024 / 1024)
                except OSError as e:
                    log.warning("[%s]   删除 %s 失败（可忽略）: %s", pid, rel, e)
            if freed_bytes > 0:
                log.info("[%s] 孤儿清理完成，释放约 %.1f MB", pid, freed_bytes / 1024 / 1024)

    # 3) 写本地 manifest（仅在所有 part 成功后）
    write_local_manifest(args, manifest, log)
    log.info("增量更新完成 ✓（已应用 part: %s）", ", ".join(applied))

    # 4) 清理临时 part 包
    for _, zp in part_zips:
        try:
            zp.unlink()
        except OSError:
            pass
    return 0


# ───────────────────────── 主流程 ─────────────────────────


def run(args: Args) -> int:
    work_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME
    work_dir.mkdir(parents=True, exist_ok=True)

    log = setup_logger(work_dir / "updater.log")
    log.info("=" * 60)
    log.info("StrangeUtaGame Updater 启动")
    log.info("目标版本: v%s  (tag: %s)", args.target_version, args.target_tag)
    log.info("主程序目录: %s", args.app_dir)
    log.info("主程序 EXE: %s", args.app_exe)
    log.info("内部目录名: %s", args.internal_name)
    log.info("下载候选: %d 个源", len(args.urls))
    log.info("=" * 60)

    # 0. 清理上次自更新遗留的 .old 文件
    _cleanup_old_files(args.app_dir, log)

    # 1. 等待主程序退出（含文件锁释放宽限）
    wait_for_pid_exit(args.pid, log)

    if not args.urls:
        log.error("未提供任何下载 URL")
        return _exit_with_pause(2)

    # ───── 2a. 先比对哈希，决定走增量还是全量 ─────
    manifest = try_fetch_manifest(args, log)
    if manifest is not None:
        local_manifest = read_local_manifest(args, log)
        needed = _diff_parts(manifest, local_manifest)

        if not needed:
            # 所有 part sha256 与本地一致 → 已是最新，无需下载任何东西
            log.info("所有 part sha256 均一致，本地已是最新版本，跳过更新")
            write_local_manifest(args, manifest, log)
            if args.launch_after:
                launch_main_app(args.app_dir, args.app_exe, log)
            log.info("更新完成 ✓（已是最新）")
            if sys.platform == "win32":
                try:
                    print()
                    print("已是最新版本。窗口将在 3 秒后关闭。")
                    time.sleep(3)
                except Exception:
                    pass
            return 0

        log.info("需要更新的 part: %s，走增量更新", ", ".join(needed))
        rc = run_incremental(args, manifest, work_dir, log)
        if rc == 0:
            if args.launch_after:
                launch_main_app(args.app_dir, args.app_exe, log)
            log.info("更新完成 ✓（增量路径）")
            if sys.platform == "win32":
                try:
                    print()
                    print("更新完成。窗口将在 3 秒后关闭。")
                    time.sleep(3)
                except Exception:
                    pass
            return 0
        log.warning("增量更新失败（rc=%d），回退到全量更新流程 ...", rc)
    else:
        log.info("未发现 manifest，使用全量更新流程")

    # ───── 2b. 全量更新 fallback ─────
    proxies = {"http": args.proxy_url, "https": args.proxy_url} if args.proxy_url else None

    # 步骤1：下载前先取预期 SHA256（小文件，挨个源尝试）
    if not args.sha256:
        for _src_id, _url in args.urls:
            sha = try_fetch_sha256(_url, proxies, log)
            if sha:
                args.sha256 = sha
                log.info("[%s] 预先获取 SHA256: %s", _src_id, sha)
                break
        if not args.sha256:
            log.info("无法预先获取 SHA256，将在下载后尝试")

    # 步骤2：下载（支持断点续传；切换源时 try_download_from_sources 自动删除残留）
    download_path = work_dir / "download" / args.asset_name
    ok, success_url = try_download_from_sources(args, download_path, log)
    if not ok:
        log.error("所有源均下载失败")
        return _exit_with_pause(3)

    # 步骤3：校验文件完整性（步骤1未取到时从成功源补取）
    if not args.sha256:
        args.sha256 = try_fetch_sha256(success_url, proxies, log)
    if not verify_sha256(download_path, args.sha256, log):
        log.error("校验失败")
        return _exit_with_pause(4)

    extract_dir = work_dir / "extracted"
    new_root = extract_archive(download_path, extract_dir, log)
    if new_root is None:
        return _exit_with_pause(5)

    ok, err = apply_update(
        args.app_dir, args.app_exe, args.internal_name, new_root, log
    )
    if not ok:
        log.error("应用更新失败: %s", err)
        return _exit_with_pause(6)
    log.info("文件替换完成")

    # 全量路径下，如果有可用的 manifest，把它写到本地 —— 下次升级就能走增量
    if manifest is not None:
        write_local_manifest(args, manifest, log)

    if args.launch_after:
        launch_main_app(args.app_dir, args.app_exe, log)

    try:
        shutil.rmtree(str(extract_dir), ignore_errors=True)
        if download_path.exists():
            download_path.unlink()
    except OSError:
        pass

    log.info("更新完成 ✓（全量路径）")
    if sys.platform == "win32":
        try:
            print()
            print("更新完成。窗口将在 3 秒后关闭。")
            time.sleep(3)
        except Exception:
            pass
    return 0


def _exit_with_pause(code: int) -> int:
    """失败退出前在控制台停留，等待用户确认。"""
    if sys.platform == "win32":
        try:
            print()
            print(f"更新失败 (退出码 {code})。日志位于 "
                  f"%TEMP%/{TMP_DIR_NAME}/updater.log")
            print("按回车键退出 ...")
            try:
                input()
            except EOFError:
                time.sleep(5)
        except Exception:
            pass
    return code


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = parse_args(argv)
    except SystemExit as e:
        return int(e.code) if isinstance(e.code, int) else 2
    return run(args)


def _fatal_pause(exc: BaseException) -> int:
    """顶层未处理异常的兜底：把堆栈打到控制台并 ``pause``，让用户能看到。"""
    import traceback
    try:
        print()
        print("=" * 60)
        print("FATAL: Updater 顶层未处理异常")
        print("=" * 60)
        traceback.print_exception(exc)
        print()
        print(f"日志（如有）位于：%TEMP%/{TMP_DIR_NAME}/updater.log")
        print("按回车键退出 ...")
        try:
            input()
        except EOFError:
            time.sleep(10)
    except Exception:
        # 连 print 都失败说明 stdout 都没了 —— 静默退出
        pass
    return 99


if __name__ == "__main__":
    # 顶层全局 catch：即便 ``main()`` 漏抛了什么也至少让用户看见错误
    # （PyInstaller bootloader 阶段的 import 错误 catch 不住，那由控制台
    # 一闪而过显示；运行时所有错误本兜底都能接住）。
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as _exc:  # noqa: BLE001 — 这里是最末端兜底
        sys.exit(_fatal_pause(_exc))
