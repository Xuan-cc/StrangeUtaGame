from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


UPDATER_EXE_NAME = "Updater.exe"
TMP_DIR_NAME = "KaraokeStudioUpdater"
DEFAULT_APP_EXE_NAME = "Karaoke Studio.exe"


@dataclass
class LaunchPlan:
    app_dir: Path
    app_exe_name: str
    target_version: str
    target_tag: str
    asset_name: str
    download_urls: list[tuple[str, str]]
    proxy_url: str = ""
    internal_dir_name: str = "_internal"
    expected_sha256: str = ""
    launch_after_update: bool = True
    extras: list[str] = field(default_factory=list)

    def command_args(self, updater_exe: Path, current_pid: int) -> list[str]:
        args = [str(updater_exe)]
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
        for source_id, url in self.download_urls:
            args += ["--url", f"{source_id}|{url}"]
        args += self.extras
        return args


@dataclass
class LaunchResult:
    launched: bool
    updater_path: str = ""
    temp_copy_path: str = ""
    pid: int = 0
    reason: str = ""


def find_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(sys.argv[0]).resolve().parent


def find_app_exe_name() -> str:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).name
    return DEFAULT_APP_EXE_NAME


def find_updater_exe(app_dir: Optional[Path] = None) -> Optional[Path]:
    root = app_dir or find_app_dir()
    for path in (
        root / UPDATER_EXE_NAME,
        root / "_internal" / "updater" / UPDATER_EXE_NAME,
    ):
        if path.exists():
            return path
    return None


def is_updater_available(app_dir: Optional[Path] = None) -> bool:
    return find_updater_exe(app_dir) is not None


def _copy_updater_to_temp(updater_exe: Path) -> Path:
    tmp_dir = Path(tempfile.gettempdir()) / TMP_DIR_NAME
    tmp_dir.mkdir(parents=True, exist_ok=True)
    dest = tmp_dir / UPDATER_EXE_NAME
    try:
        shutil.copy2(str(updater_exe), str(dest))
    except PermissionError:
        dest = tmp_dir / f"Updater-{int(time.time())}.exe"
        shutil.copy2(str(updater_exe), str(dest))
    return dest


def launch_updater(plan: LaunchPlan) -> LaunchResult:
    updater = find_updater_exe(plan.app_dir)
    if updater is None:
        return LaunchResult(
            launched=False,
            reason="Updater.exe was not found next to the application.",
        )
    try:
        temp_copy = _copy_updater_to_temp(updater)
    except Exception as exc:  # noqa: BLE001
        return LaunchResult(launched=False, reason=f"Failed to copy Updater.exe: {exc}")

    args = plan.command_args(temp_copy, os.getpid())
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(plan.app_dir),
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
        )
    except Exception as exc:  # noqa: BLE001
        return LaunchResult(launched=False, updater_path=str(updater), temp_copy_path=str(temp_copy), reason=str(exc))

    return LaunchResult(
        launched=True,
        updater_path=str(updater),
        temp_copy_path=str(temp_copy),
        pid=int(proc.pid),
    )

