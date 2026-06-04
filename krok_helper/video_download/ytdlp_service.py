from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .download_task import (
    DownloadOptions,
    DownloadTask,
    NAMING_RULE_CUSTOM,
    NAMING_RULE_TITLE,
    NAMING_RULE_TITLE_UPLOADER,
    SOURCE_BILIBILI,
    SOURCE_UNKNOWN,
    SOURCE_YOUTUBE,
    VideoInfo,
)
from .format_parser import FormatParser


WINDOWS_INVALID_FILENAME_PATTERN = re.compile(r'[\\/:*?"<>|]+')
YOUTUBE_FALLBACK_EXTRACTOR_ARGS = "youtube:player_client=tv,web_safari,mweb,android"


class VideoDownloadError(RuntimeError):
    """Raised when yt-dlp operations fail."""


class DownloadCancelledError(VideoDownloadError):
    """Raised when the user cancels the current download."""


class YtDlpService:
    def __init__(self, format_parser: FormatParser | None = None) -> None:
        self._format_parser = format_parser or FormatParser()

    def get_ytdlp_version(self) -> str:
        try:
            import yt_dlp
        except ModuleNotFoundError:
            cli = self._find_ytdlp_cli()
            completed = subprocess.run(
                [cli, "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=15,
                creationflags=self._subprocess_creationflags(),
            )
            if completed.returncode != 0:
                message = completed.stderr.strip() or completed.stdout.strip() or "无法读取 yt-dlp 版本。"
                raise VideoDownloadError(message)
            version = completed.stdout.strip() or "未知版本"
            return f"命令行版 {version}"
        return f"Python 包 {yt_dlp.version.__version__}"

    def get_latest_ytdlp_version(self) -> str:
        request = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json",
            headers={"User-Agent": "krok-helper"},
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        except Exception as exc:  # noqa: BLE001
            raise VideoDownloadError(f"无法查询 yt-dlp 最新版本：{exc}") from exc
        version = str((payload.get("info") or {}).get("version") or "").strip()
        if not version:
            raise VideoDownloadError("PyPI 未返回 yt-dlp 最新版本。")
        return version

    def update_ytdlp(self) -> str:
        try:
            import yt_dlp  # noqa: F401
        except ModuleNotFoundError:
            return self._update_ytdlp_cli()
        return self._update_ytdlp_python_package()

    def extract_info(self, url: str, cookie_file: str | None = None) -> VideoInfo:
        infos = self.extract_infos(url, cookie_file)
        if not infos:
            raise VideoDownloadError("没有可用的解析结果。")
        return infos[0]

    def extract_infos(self, url: str, cookie_file: str | None = None) -> list[VideoInfo]:
        source = self.detect_source(url)
        raw_info, extractor_args_hint = self._extract_info_with_best_backend(
            url,
            cookie_file,
            allow_playlist=source == SOURCE_BILIBILI,
        )
        entries = self._unwrap_bilibili_entries(raw_info) if source == SOURCE_BILIBILI else []
        if entries:
            parent_title = str(raw_info.get("title") or "")
            total = len(entries)
            return [
                self._build_video_info(
                    self._hydrate_playlist_entry(entry, cookie_file),
                    url,
                    extractor_args_hint,
                    parent_title=parent_title,
                    part_index=index + 1,
                    part_total=total,
                )
                for index, entry in enumerate(entries)
            ]
        return [self._build_video_info(self._unwrap_info(raw_info), url, extractor_args_hint)]

    def _hydrate_playlist_entry(
        self,
        entry: dict[str, Any],
        cookie_file: str | None,
    ) -> dict[str, Any]:
        if entry.get("formats"):
            return entry
        entry_url = self._coerce_webpage_url(entry, "")
        if not entry_url:
            return entry
        try:
            raw_entry, _hint = self._extract_info_with_best_backend(
                entry_url,
                cookie_file,
                allow_playlist=False,
            )
        except Exception:
            return entry
        return self._unwrap_info(raw_entry)

    def _build_video_info(
        self,
        info: dict[str, Any],
        fallback_url: str,
        extractor_args_hint: str,
        *,
        parent_title: str = "",
        part_index: int = 0,
        part_total: int = 0,
    ) -> VideoInfo:
        formats = self._format_parser.parse_formats(info.get("formats"))
        thumbnail_url = str(info.get("thumbnail") or "")
        webpage_url = self._coerce_webpage_url(info, fallback_url)
        title = str(info.get("title") or "未命名视频")
        if parent_title and title and title != parent_title:
            prefix = f"P{part_index} " if part_total > 1 and part_index > 0 else ""
            title = f"{parent_title} - {prefix}{title}".strip()
        return VideoInfo(
            url=webpage_url,
            source=self.detect_source(fallback_url, info.get("extractor_key")),
            title=title,
            uploader=str(info.get("uploader") or info.get("channel") or info.get("owner") or "-"),
            duration=float(info["duration"]) if info.get("duration") else None,
            thumbnail_url=thumbnail_url,
            thumbnail_bytes=self._fetch_thumbnail_bytes(thumbnail_url),
            webpage_url=webpage_url,
            width=int(info.get("width") or 0),
            height=int(info.get("height") or 0),
            filesize=self._pick_filesize(info),
            formats=formats,
            recommended_option_id=formats[0].option_id if formats else "",
            subtitles_available=bool(info.get("subtitles") or info.get("automatic_captions")),
            extractor_args_hint=extractor_args_hint,
        )

    def download(
        self,
        task: DownloadTask,
        options: DownloadOptions,
        progress_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        save_dir = Path(options.save_dir).expanduser()
        save_dir.mkdir(parents=True, exist_ok=True)
        title = task.title or (task.info.title if task.info else "未命名视频")
        uploader = task.info.uploader if task.info else ""
        resolution = task.selected_format.resolution if task.selected_format else ""
        if not resolution and task.info and task.info.height:
            resolution = f"{task.info.height}p"
        output_stem = self._build_output_stem(
            title=title,
            uploader=uploader,
            resolution=resolution,
            options=options,
        )
        outtmpl = str(save_dir / f"{output_stem}.%(ext)s")
        selected_format = task.selected_format.download_format if task.selected_format else "best"
        extractor_args_hint = task.info.extractor_args_hint if task.info else ""
        preexisting_outputs = self._snapshot_output_candidates(save_dir, output_stem)

        youtube_dl = self._import_ytdlp()
        if youtube_dl is not None:
            try:
                self._download_with_python_retry(
                    youtube_dl,
                    task,
                    options,
                    progress_callback,
                    save_dir=save_dir,
                    output_stem=output_stem,
                    outtmpl=outtmpl,
                    selected_format=selected_format,
                    extractor_args_hint=extractor_args_hint,
                )
            except DownloadCancelledError:
                self._cleanup_cancelled_outputs(save_dir, output_stem, preexisting_outputs)
                raise
            return

        try:
            self._download_with_cli_retry(
                task,
                options,
                progress_callback,
                save_dir=save_dir,
                output_stem=output_stem,
                outtmpl=outtmpl,
                selected_format=selected_format,
                extractor_args_hint=extractor_args_hint,
            )
        except DownloadCancelledError:
            self._cleanup_cancelled_outputs(save_dir, output_stem, preexisting_outputs)
            raise

    def _extract_info_with_best_backend(
        self,
        url: str,
        cookie_file: str | None = None,
        *,
        allow_playlist: bool = False,
    ) -> tuple[dict[str, Any], str]:
        youtube_dl = self._import_ytdlp()
        if youtube_dl is not None:
            return self._extract_info_with_python_retry(youtube_dl, url, cookie_file, allow_playlist=allow_playlist)
        return self._extract_info_with_cli_retry(url, cookie_file, allow_playlist=allow_playlist)

    def _extract_info_with_python_retry(
        self,
        youtube_dl,
        url: str,
        cookie_file: str | None,
        *,
        allow_playlist: bool = False,
    ) -> tuple[dict[str, Any], str]:
        try:
            return self._extract_info_with_python_api(youtube_dl, url, cookie_file, allow_playlist=allow_playlist), ""
        except VideoDownloadError as exc:
            if self._should_retry_youtube_with_fallback(url, str(exc)):
                return (
                    self._extract_info_with_python_api(
                        youtube_dl,
                        url,
                        cookie_file,
                        extractor_args_hint=YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                        allow_playlist=allow_playlist,
                    ),
                    YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                )
            raise

    def _extract_info_with_python_api(
        self,
        youtube_dl,
        url: str,
        cookie_file: str | None,
        *,
        extractor_args_hint: str = "",
        allow_playlist: bool = False,
    ) -> dict[str, Any]:
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": not allow_playlist,
            "skip_download": True,
        }
        if cookie_file and Path(cookie_file).is_file():
            ydl_opts["cookiefile"] = cookie_file
        if extractor_args_hint:
            ydl_opts["extractor_args"] = self._build_python_extractor_args(extractor_args_hint)

        try:
            with youtube_dl(ydl_opts) as ydl:
                return ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            raise VideoDownloadError(self._normalize_error_message(exc)) from exc

    def _extract_info_with_cli_retry(
        self,
        url: str,
        cookie_file: str | None,
        *,
        allow_playlist: bool = False,
    ) -> tuple[dict[str, Any], str]:
        try:
            return self._extract_info_with_cli(url, cookie_file, allow_playlist=allow_playlist), ""
        except VideoDownloadError as exc:
            if self._should_retry_youtube_with_fallback(url, str(exc)):
                return (
                    self._extract_info_with_cli(
                        url,
                        cookie_file,
                        extractor_args_hint=YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                        allow_playlist=allow_playlist,
                    ),
                    YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                )
            raise

    def _extract_info_with_cli(
        self,
        url: str,
        cookie_file: str | None = None,
        *,
        extractor_args_hint: str = "",
        allow_playlist: bool = False,
    ) -> dict[str, Any]:
        command = [
            self._find_ytdlp_cli(),
            "--dump-single-json",
            "--skip-download",
            "--yes-playlist" if allow_playlist else "--no-playlist",
            "--no-warnings",
            "--no-update",
            url,
        ]
        if extractor_args_hint:
            command[1:1] = ["--extractor-args", extractor_args_hint]
        if cookie_file and Path(cookie_file).is_file():
            command[1:1] = ["--cookies", cookie_file]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=60,
                creationflags=self._subprocess_creationflags(),
            )
        except Exception as exc:  # noqa: BLE001
            raise VideoDownloadError(self._normalize_error_message(exc)) from exc

        if completed.returncode != 0:
            message = completed.stderr.strip() or completed.stdout.strip() or "yt-dlp failed"
            raise VideoDownloadError(self._normalize_error_message(RuntimeError(message)))

        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise VideoDownloadError("yt-dlp 返回了无法解析的 JSON 结果。") from exc

    def _download_with_python_retry(
        self,
        youtube_dl,
        task: DownloadTask,
        options: DownloadOptions,
        progress_callback: Callable[[dict[str, Any]], None],
        *,
        save_dir: Path,
        output_stem: str,
        outtmpl: str,
        selected_format: str,
        extractor_args_hint: str,
    ) -> None:
        try:
            self._download_with_python_api(
                youtube_dl,
                task,
                options,
                progress_callback,
                save_dir=save_dir,
                output_stem=output_stem,
                outtmpl=outtmpl,
                selected_format=selected_format,
                extractor_args_hint=extractor_args_hint,
            )
        except VideoDownloadError as exc:
            if self._should_retry_youtube_with_fallback(task.url, str(exc), extractor_args_hint):
                self._download_with_python_api(
                    youtube_dl,
                    task,
                    options,
                    progress_callback,
                    save_dir=save_dir,
                    output_stem=output_stem,
                    outtmpl=outtmpl,
                    selected_format=selected_format,
                    extractor_args_hint=YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                )
                return
            raise

    def _download_with_python_api(
        self,
        youtube_dl,
        task: DownloadTask,
        options: DownloadOptions,
        progress_callback: Callable[[dict[str, Any]], None],
        *,
        save_dir: Path,
        output_stem: str,
        outtmpl: str,
        selected_format: str,
        extractor_args_hint: str = "",
    ) -> None:
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "outtmpl": outtmpl,
            "format": selected_format,
            "overwrites": True,
            "retries": max(0, int(options.retry_count)),
            "socket_timeout": max(1, int(options.timeout)),
            "progress_hooks": [self._build_hook(task, progress_callback)],
            "writethumbnail": bool(options.download_thumbnail),
            "writesubtitles": bool(options.download_subtitle),
            "writeautomaticsub": bool(options.download_subtitle),
        }
        if extractor_args_hint:
            ydl_opts["extractor_args"] = self._build_python_extractor_args(extractor_args_hint)
        if options.merge_video_audio:
            ydl_opts["merge_output_format"] = "mp4"
        if options.cookie_file and Path(options.cookie_file).is_file():
            ydl_opts["cookiefile"] = options.cookie_file

        before_pids = self._snapshot_child_pids()
        done_event = threading.Event()
        watcher = self._start_cancel_watcher(task, done_event, before_pids)
        try:
            with youtube_dl(ydl_opts) as ydl:
                result = ydl.extract_info(task.url, download=True)
                final_info = self._unwrap_info(result)
                task.local_file = self._resolve_output_file(save_dir, output_stem, final_info, task, options)
        except DownloadCancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            if task.cancel_requested:
                raise DownloadCancelledError("下载已取消。") from exc
            raise VideoDownloadError(self._normalize_error_message(exc)) from exc
        finally:
            done_event.set()
            watcher.join(timeout=1)

    def _download_with_cli_retry(
        self,
        task: DownloadTask,
        options: DownloadOptions,
        progress_callback: Callable[[dict[str, Any]], None],
        *,
        save_dir: Path,
        output_stem: str,
        outtmpl: str,
        selected_format: str,
        extractor_args_hint: str,
    ) -> None:
        try:
            self._download_with_cli(
                task,
                options,
                progress_callback,
                save_dir=save_dir,
                output_stem=output_stem,
                outtmpl=outtmpl,
                selected_format=selected_format,
                extractor_args_hint=extractor_args_hint,
            )
        except VideoDownloadError as exc:
            if self._should_retry_youtube_with_fallback(task.url, str(exc), extractor_args_hint):
                self._download_with_cli(
                    task,
                    options,
                    progress_callback,
                    save_dir=save_dir,
                    output_stem=output_stem,
                    outtmpl=outtmpl,
                    selected_format=selected_format,
                    extractor_args_hint=YOUTUBE_FALLBACK_EXTRACTOR_ARGS,
                )
                return
            raise

    def _download_with_cli(
        self,
        task: DownloadTask,
        options: DownloadOptions,
        progress_callback: Callable[[dict[str, Any]], None],
        *,
        save_dir: Path,
        output_stem: str,
        outtmpl: str,
        selected_format: str,
        extractor_args_hint: str = "",
    ) -> None:
        progress_marker = "__KROK_PROGRESS__"
        command = [
            self._find_ytdlp_cli(),
            "--newline",
            "--no-warnings",
            "--no-update",
            "--no-playlist",
            "--force-overwrites",
            "--output",
            outtmpl,
            "--format",
            selected_format,
            "--retries",
            str(max(0, int(options.retry_count))),
            "--socket-timeout",
            str(max(1, int(options.timeout))),
            "--progress-template",
            (
                f"download:{progress_marker}"
                "%(progress.status)s|%(progress.downloaded_bytes)s|%(progress.total_bytes)s|"
                "%(progress.total_bytes_estimate)s|%(progress.speed)s|%(progress.eta)s|"
                "%(progress.fragment_index)s|%(progress.fragment_count)s"
            ),
        ]
        if extractor_args_hint:
            command.extend(["--extractor-args", extractor_args_hint])
        if options.download_thumbnail:
            command.append("--write-thumbnail")
        if options.download_subtitle:
            command.extend(["--write-subs", "--write-auto-subs"])
        if options.merge_video_audio:
            command.extend(["--merge-output-format", "mp4"])
        if options.cookie_file and Path(options.cookie_file).is_file():
            command.extend(["--cookies", options.cookie_file])
        command.append(task.url)

        before_pids = self._snapshot_child_pids()
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="ignore",
            creationflags=self._subprocess_creationflags(),
        )
        done_event = threading.Event()
        watcher = self._start_cancel_watcher(task, done_event, before_pids, process=process)
        output_lines: list[str] = []

        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.strip()
                if not line:
                    continue
                output_lines.append(line)
                if self._is_cli_merge_line(line):
                    self._emit_merge_progress(progress_callback=progress_callback)
                if task.cancel_requested and process.poll() is None:
                    process.terminate()
                    raise DownloadCancelledError("下载已取消。")
                if progress_marker in line:
                    marker_index = line.index(progress_marker) + len(progress_marker)
                    self._emit_cli_progress(line[marker_index:], progress_callback=progress_callback)

            return_code = process.wait()
            if task.cancel_requested:
                raise DownloadCancelledError("下载已取消。")
        except DownloadCancelledError:
            self._terminate_process(process)
            raise
        except Exception as exc:  # noqa: BLE001
            self._terminate_process(process)
            if task.cancel_requested:
                raise DownloadCancelledError("下载已取消。") from exc
            raise VideoDownloadError(self._normalize_error_message(exc)) from exc
        finally:
            done_event.set()
            watcher.join(timeout=1)

        if return_code != 0:
            if task.cancel_requested:
                raise DownloadCancelledError("下载已取消。")
            resolved_file = self._resolve_output_file(save_dir, output_stem, {}, task, options)
            if resolved_file is not None and resolved_file.is_file() and resolved_file.stat().st_size > 0:
                task.local_file = resolved_file
                return
            message = self._pick_cli_error_message(output_lines, progress_marker)
            raise VideoDownloadError(self._normalize_error_message(RuntimeError(message)))

        task.local_file = self._resolve_output_file(save_dir, output_stem, {}, task, options)

    def _pick_cli_error_message(self, output_lines: list[str], progress_marker: str) -> str:
        meaningful_lines = [
            line
            for line in output_lines
            if line and progress_marker not in line and not line.startswith("[download]")
        ]
        error_line = next((line for line in reversed(meaningful_lines) if "ERROR:" in line), "")
        if error_line:
            return error_line
        warning_line = next((line for line in reversed(meaningful_lines) if "WARNING:" in line), "")
        if warning_line:
            return warning_line
        return next(reversed(meaningful_lines), "yt-dlp download failed")

    def detect_source(self, url: str, extractor_key: str | None = None) -> str:
        normalized = url.lower()
        extractor_key = (extractor_key or "").lower()
        if "bilibili" in normalized or "bili" in extractor_key:
            return SOURCE_BILIBILI
        if "youtube.com" in normalized or "youtu.be" in normalized or "youtube" in extractor_key:
            return SOURCE_YOUTUBE
        return SOURCE_UNKNOWN

    def _import_ytdlp(self):
        try:
            from yt_dlp import YoutubeDL
        except ModuleNotFoundError:
            return None
        return YoutubeDL

    def _find_ytdlp_cli(self) -> str:
        cli = shutil.which("yt-dlp")
        if cli:
            return cli
        raise VideoDownloadError("未找到 yt-dlp。请安装 `yt-dlp` 命令或 Python 包。")

    def _update_ytdlp_cli(self) -> str:
        cli = self._find_ytdlp_cli()
        try:
            return self._run_update_command([cli, "-U"])
        except VideoDownloadError as exc:
            message = str(exc)
            if self._should_fallback_to_pip_update(message):
                python_executable = self._python_executable_for_cli(cli)
                pip_output = self._update_ytdlp_python_package(python_executable)
                return (
                    "yt-dlp 命令行自更新不可用，已改用对应 Python 环境的 pip 更新 yt-dlp。\n"
                    f"{pip_output}"
                )
            raise

    def _update_ytdlp_python_package(self, python_executable: str | None = None) -> str:
        python = python_executable or sys.executable
        return self._run_update_command([python, "-m", "pip", "install", "-U", "yt-dlp"])

    def _python_executable_for_cli(self, cli: str) -> str:
        cli_path = Path(cli)
        if os.name == "nt":
            parent = cli_path.parent
            if parent.name.lower() == "scripts":
                candidate = parent.parent / "python.exe"
                if candidate.is_file():
                    return str(candidate)
        return sys.executable

    def normalize_version(self, version_text: str) -> str:
        match = re.search(r"(\d{4})\.(\d{1,2})\.(\d{1,2})(?:\.(\d+))?", version_text)
        if not match:
            return version_text.strip()
        year, month, day, suffix = match.groups()
        normalized = f"{int(year):04d}.{int(month):02d}.{int(day):02d}"
        if suffix is not None:
            normalized = f"{normalized}.{suffix}"
        return normalized

    def _should_fallback_to_pip_update(self, message: str) -> bool:
        lower = message.lower()
        return (
            "installed yt-dlp with pip" in lower
            or "wheel from pypi" in lower
            or "use that to update" in lower
        )

    def _run_update_command(self, command: list[str]) -> str:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                timeout=180,
                creationflags=self._subprocess_creationflags(),
            )
        except Exception as exc:  # noqa: BLE001
            raise VideoDownloadError(self._normalize_error_message(exc)) from exc

        output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part.strip())
        if completed.returncode != 0:
            raise VideoDownloadError(output or "yt-dlp 更新失败。")
        return output or "yt-dlp 已更新。"

    def _unwrap_info(self, raw_info: dict[str, Any]) -> dict[str, Any]:
        if isinstance(raw_info, dict) and raw_info.get("entries"):
            entries = [entry for entry in raw_info.get("entries") or [] if isinstance(entry, dict)]
            if entries:
                return entries[0]
        return raw_info

    def _unwrap_bilibili_entries(self, raw_info: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(raw_info, dict):
            return []
        entries = [entry for entry in raw_info.get("entries") or [] if isinstance(entry, dict)]
        if len(entries) <= 1:
            return []
        return entries

    def _coerce_webpage_url(self, info: dict[str, Any], fallback_url: str) -> str:
        for key in ("webpage_url", "original_url", "url"):
            value = str(info.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
        return fallback_url

    def _pick_filesize(self, info: dict[str, Any]) -> int | None:
        formats = info.get("formats") or []
        for key in ("filesize", "filesize_approx"):
            value = info.get(key)
            if isinstance(value, (int, float)) and value > 0:
                return int(value)
        for item in formats:
            for key in ("filesize", "filesize_approx"):
                value = item.get(key)
                if isinstance(value, (int, float)) and value > 0:
                    return int(value)
        return None

    def _fetch_thumbnail_bytes(self, url: str) -> bytes:
        if not url:
            return b""
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.read()
        except Exception:
            return b""

    def _build_output_stem(self, *, title: str, uploader: str, resolution: str, options: DownloadOptions) -> str:
        safe_title = self._sanitize_filename(title or "未命名视频")
        safe_uploader = self._sanitize_filename(uploader or "未知作者")

        if options.naming_rule == NAMING_RULE_TITLE_UPLOADER:
            stem = f"{safe_title} - {safe_uploader}"
        elif options.naming_rule == NAMING_RULE_CUSTOM:
            template = options.custom_template.strip() or "{title}"
            try:
                stem = template.format(title=safe_title, uploader=safe_uploader, author=safe_uploader)
            except Exception as exc:  # noqa: BLE001
                raise VideoDownloadError(f"自定义命名模板无效：{exc}") from exc
        elif options.naming_rule == NAMING_RULE_TITLE:
            stem = safe_title
        else:
            stem = safe_title

        safe_resolution = self._sanitize_filename(resolution or "")
        if safe_resolution:
            suffix = f"[{safe_resolution}]"
            if not stem.endswith(suffix):
                stem = f"{stem} {suffix}".strip()

        stem = self._sanitize_filename(stem.strip())
        return stem or "video"

    def _sanitize_filename(self, value: str) -> str:
        cleaned = WINDOWS_INVALID_FILENAME_PATTERN.sub("_", value)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
        return cleaned[:180]

    def _build_hook(
        self,
        task: DownloadTask,
        progress_callback: Callable[[dict[str, Any]], None],
    ) -> Callable[[dict[str, Any]], None]:
        def hook(status: dict[str, Any]) -> None:
            if task.cancel_requested:
                raise DownloadCancelledError("下载已取消。")

            payload = {
                "status": str(status.get("status") or ""),
                "downloaded_bytes": int(status.get("downloaded_bytes") or 0),
                "total_bytes": int(status.get("total_bytes") or 0),
                "total_bytes_estimate": int(status.get("total_bytes_estimate") or 0),
                "speed": float(status.get("speed") or 0),
                "eta": status.get("eta"),
                "fragment_index": int(status.get("fragment_index") or 0),
                "fragment_count": int(status.get("fragment_count") or 0),
                "filename": str(status.get("filename") or ""),
            }
            progress_callback(payload)

        return hook

    def _emit_cli_progress(
        self,
        payload_text: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None],
    ) -> None:
        parts = payload_text.split("|")
        status, downloaded, total, estimated, speed, eta, fragment_index, fragment_count = (parts + [""] * 8)[:8]
        payload = {
            "status": status,
            "downloaded_bytes": self._parse_int(downloaded),
            "total_bytes": self._parse_int(total),
            "total_bytes_estimate": self._parse_int(estimated),
            "speed": self._parse_float(speed),
            "eta": self._parse_int(eta),
            "fragment_index": self._parse_int(fragment_index),
            "fragment_count": self._parse_int(fragment_count),
            "filename": "",
        }
        progress_callback(payload)

    def _is_cli_merge_line(self, line: str) -> bool:
        lower = line.lower()
        return "[merger]" in lower and "merg" in lower

    def _emit_merge_progress(self, *, progress_callback: Callable[[dict[str, Any]], None]) -> None:
        progress_callback(
            {
                "status": "merging",
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "total_bytes_estimate": 0,
                "speed": 0.0,
                "eta": None,
                "fragment_index": 0,
                "fragment_count": 0,
                "filename": "",
            }
        )

    def _parse_int(self, value: str | None) -> int:
        try:
            return int(float(value or 0))
        except (TypeError, ValueError):
            return 0

    def _parse_float(self, value: str | None) -> float:
        try:
            return float(value or 0)
        except (TypeError, ValueError):
            return 0.0

    def _resolve_output_file(
        self,
        save_dir: Path,
        output_stem: str,
        info: dict[str, Any],
        task: DownloadTask,
        options: DownloadOptions,
    ) -> Path | None:
        requested_downloads = info.get("requested_downloads") or []
        for item in requested_downloads:
            filepath = item.get("filepath")
            if filepath:
                return Path(filepath)

        ext = "mp4" if options.merge_video_audio or (task.selected_format and task.selected_format.requires_merge) else ""
        if not ext:
            ext = str(info.get("ext") or (task.selected_format.ext if task.selected_format else "") or "mp4")
        return save_dir / f"{output_stem}.{ext}"

    def _snapshot_output_candidates(self, save_dir: Path, output_stem: str) -> dict[Path, tuple[int, int]]:
        snapshot: dict[Path, tuple[int, int]] = {}
        for path in self._iter_output_candidates(save_dir, output_stem):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot[path] = (stat.st_size, stat.st_mtime_ns)
        return snapshot

    def _cleanup_cancelled_outputs(
        self,
        save_dir: Path,
        output_stem: str,
        preexisting_outputs: dict[Path, tuple[int, int]],
    ) -> None:
        for path in self._iter_output_candidates(save_dir, output_stem):
            try:
                stat = path.stat()
            except OSError:
                continue
            previous = preexisting_outputs.get(path)
            current = (stat.st_size, stat.st_mtime_ns)
            if previous == current:
                continue
            try:
                path.unlink()
            except OSError:
                pass

    def _iter_output_candidates(self, save_dir: Path, output_stem: str):
        try:
            for path in save_dir.iterdir():
                if path.is_file() and self._is_output_candidate(path, output_stem):
                    yield path
        except OSError:
            return

    def _is_output_candidate(self, path: Path, output_stem: str) -> bool:
        name = path.name
        if not name.startswith(output_stem):
            return False
        suffix = name[len(output_stem) :]
        return not suffix or suffix.startswith((".", "-"))

    def _terminate_process(self, process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

    def _snapshot_child_pids(self) -> set[int]:
        try:
            import psutil

            return {child.pid for child in psutil.Process().children(recursive=True)}
        except Exception:
            return set()

    def _start_cancel_watcher(
        self,
        task: DownloadTask,
        done_event: threading.Event,
        before_pids: set[int],
        *,
        process: subprocess.Popen[str] | None = None,
    ) -> threading.Thread:
        def watch() -> None:
            while not done_event.wait(0.1):
                if not task.cancel_requested:
                    continue
                if process is not None and process.poll() is None:
                    process.terminate()
                self._terminate_new_media_children(before_pids)
                return

        thread = threading.Thread(target=watch, name="krok-download-cancel-watcher", daemon=True)
        thread.start()
        return thread

    def _terminate_new_media_children(self, before_pids: set[int]) -> None:
        try:
            import psutil
        except Exception:
            return

        targets = []
        try:
            children = psutil.Process().children(recursive=True)
        except Exception:
            return

        for child in children:
            if child.pid in before_pids:
                continue
            try:
                name = child.name().lower()
            except Exception:
                name = ""
            if "ffmpeg" in name or "yt-dlp" in name:
                targets.append(child)

        for child in targets:
            try:
                child.terminate()
            except Exception:
                pass
        try:
            _, alive = psutil.wait_procs(targets, timeout=1)
        except Exception:
            alive = targets
        for child in alive:
            try:
                child.kill()
            except Exception:
                pass

    def _subprocess_creationflags(self) -> int:
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)

    def _build_python_extractor_args(self, extractor_args_hint: str) -> dict[str, dict[str, list[str]]]:
        if extractor_args_hint == YOUTUBE_FALLBACK_EXTRACTOR_ARGS:
            return {"youtube": {"player_client": ["tv", "web_safari", "mweb", "android"]}}
        return {}

    def _should_retry_youtube_with_fallback(
        self,
        url: str,
        message: str,
        extractor_args_hint: str = "",
    ) -> bool:
        if extractor_args_hint == YOUTUBE_FALLBACK_EXTRACTOR_ARGS:
            return False
        if self.detect_source(url) != SOURCE_YOUTUBE:
            return False
        lower = message.lower()
        return (
            "not a bot" in lower
            or "cookies-from-browser" in lower
            or "downloaded file is empty" in lower
            or "empty file" in lower
            or "空文件" in message
            or "机器人校验" in message
        )

    def _normalize_error_message(self, exc: Exception) -> str:
        message = str(exc).strip() or exc.__class__.__name__
        lower = message.lower()
        if "ffmpeg" in lower and "not found" in lower:
            return "未找到 ffmpeg，无法合并音视频或处理封面。请先安装 ffmpeg 并加入 PATH。"
        if "requested format is not available" in lower:
            return "当前清晰度不可用，请重新解析后选择其他格式。"
        if "downloaded file is empty" in lower or "empty file" in lower:
            return (
                "YouTube 返回了空文件，通常是当前清晰度/播放客户端不可用、Cookie 失效或 yt-dlp 版本偏旧。"
                "已尝试兼容模式；如果仍失败，请刷新 Firefox Cookie、更新 yt-dlp，或换一个清晰度重试。"
            )
        if "not a bot" in lower:
            return "YouTube 触发了机器人校验，已尝试兼容模式；如果仍失败，请稍后重试。"
        if "sign in to confirm your age" in lower or "login required" in lower:
            return "该视频需要登录后访问，请检查 Bilibili 登录状态是否有效。"
        if "http error 403" in lower:
            return "访问被拒绝，可能需要刷新登录状态或稍后重试。"
        if "timed out" in lower:
            return "网络超时，请稍后重试。"
        if "module named yt_dlp" in lower:
            return "本机没有安装 yt_dlp Python 包，也没有可用的 yt-dlp 命令。"
        return message
