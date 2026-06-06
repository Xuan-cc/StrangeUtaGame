from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from krok_helper.config import APP_NAME
from krok_helper.network import (
    build_urllib_opener_for_app_settings,
    load_current_app_settings,
    proxy_cli_args_for_app_settings,
    proxy_url_for_app_settings,
    subprocess_env_for_app_settings,
)
from .download_task import SOURCE_BILIBILI, SOURCE_YOUTUBE


@dataclass(slots=True)
class BilibiliAccountProfile:
    nickname: str
    avatar_url: str = ""
    avatar_bytes: bytes = b""


class CookieManager:
    def __init__(self, cookie_path: str = "", app_settings=None) -> None:
        self._configured_path = cookie_path.strip()
        self._app_settings = app_settings

    def set_cookie_path(self, cookie_path: str) -> None:
        self._configured_path = cookie_path.strip()

    def _settings(self):
        return self._app_settings or load_current_app_settings()

    def default_cookie_path(self, platform: str = SOURCE_BILIBILI) -> Path:
        filename = "youtube_cookies.txt" if platform == SOURCE_YOUTUBE else "bilibili_cookies.txt"
        appdata = os.getenv("APPDATA")
        if os.name == "nt" and appdata:
            return Path(appdata) / APP_NAME / "video_download" / filename
        return Path.home() / ".config" / APP_NAME.lower().replace(" ", "-") / filename

    def resolved_cookie_path(self, platform: str = SOURCE_BILIBILI) -> Path:
        if platform == SOURCE_BILIBILI and self._configured_path:
            return Path(self._configured_path).expanduser()
        path = self.default_cookie_path(platform)
        if path.exists():
            return path
        legacy = self._legacy_default_cookie_path(platform)
        return legacy if legacy.exists() and self._is_builtin_default_cookie_path(path) else path

    def _legacy_default_cookie_path(self, platform: str = SOURCE_BILIBILI) -> Path:
        filename = "youtube_cookies.txt" if platform == SOURCE_YOUTUBE else "bilibili_cookies.txt"
        appdata = os.getenv("APPDATA")
        if os.name == "nt" and appdata:
            return Path(appdata) / "Karaoke Helper" / "video_download" / filename
        return Path.home() / ".config" / "karaoke-helper" / filename

    def _is_builtin_default_cookie_path(self, path: Path) -> bool:
        parts = path.parts
        if os.name == "nt":
            return len(parts) >= 3 and parts[-3] == APP_NAME and parts[-2] == "video_download"
        return len(parts) >= 2 and parts[-2] == APP_NAME.lower().replace(" ", "-")

    def build_opener(self, *handlers):
        return build_urllib_opener_for_app_settings(self._settings(), *handlers)

    def has_cookie(self, platform: str = SOURCE_BILIBILI) -> bool:
        path = self.resolved_cookie_path(platform)
        return path.is_file() and path.stat().st_size > 0

    def get_cookie_path(self, platform: str = SOURCE_BILIBILI) -> str | None:
        path = self.resolved_cookie_path(platform)
        return str(path) if path.exists() else None

    def clear(self, platform: str = SOURCE_BILIBILI) -> None:
        path = self.resolved_cookie_path(platform)
        if path.exists():
            path.unlink()

    def clear_cookie(self) -> None:
        self.clear(SOURCE_BILIBILI)

    def load_cookie_jar(self, platform: str = SOURCE_BILIBILI) -> http.cookiejar.MozillaCookieJar:
        path = self.resolved_cookie_path(platform)
        jar = http.cookiejar.MozillaCookieJar(str(path))
        if path.is_file():
            jar.load(ignore_discard=True, ignore_expires=True)
        return jar

    def save_cookie_jar(self, jar: http.cookiejar.MozillaCookieJar, platform: str = SOURCE_BILIBILI) -> Path:
        path = self.resolved_cookie_path(platform)
        path.parent.mkdir(parents=True, exist_ok=True)
        for cookie in jar:
            cookie.discard = False
        jar.filename = str(path)
        jar.save(ignore_discard=True, ignore_expires=True)
        return path

    def set_cookie(
        self,
        jar: http.cookiejar.MozillaCookieJar,
        *,
        name: str,
        value: str,
        domain: str = ".bilibili.com",
        path: str = "/",
        expires: int | None = None,
        secure: bool = True,
        http_only: bool = False,
    ) -> None:
        cookie = http.cookiejar.Cookie(
            version=0,
            name=name,
            value=value,
            port=None,
            port_specified=False,
            domain=domain,
            domain_specified=True,
            domain_initial_dot=domain.startswith("."),
            path=path,
            path_specified=True,
            secure=secure,
            expires=expires,
            discard=False,
            comment=None,
            comment_url=None,
            rest={"HttpOnly": None} if http_only else {},
            rfc2109=False,
        )
        jar.set_cookie(cookie)

    def check_login_status(self, platform: str = SOURCE_BILIBILI) -> bool:
        if platform == SOURCE_YOUTUBE:
            return self.has_cookie(SOURCE_YOUTUBE)
        if not self.has_cookie(SOURCE_BILIBILI):
            return False

        try:
            payload = self._fetch_nav_payload()
            return bool(payload.get("data", {}).get("isLogin"))
        except Exception:
            return self._has_valid_sessdata_locally()

    def get_profile(self, platform: str = SOURCE_BILIBILI) -> BilibiliAccountProfile | None:
        if platform == SOURCE_YOUTUBE:
            if not self.has_cookie(SOURCE_YOUTUBE):
                return None
            return BilibiliAccountProfile(nickname="已登录")

        try:
            payload = self._fetch_nav_payload()
        except Exception:
            return None

        data = payload.get("data") or {}
        if not data.get("isLogin"):
            return None

        avatar_url = str(data.get("face") or "")
        return BilibiliAccountProfile(
            nickname=str(data.get("uname") or "Bilibili 用户"),
            avatar_url=avatar_url,
            avatar_bytes=self._fetch_bytes(avatar_url),
        )

    def get_account_profile(self) -> BilibiliAccountProfile | None:
        return self.get_profile(SOURCE_BILIBILI)

    def import_from_browser(self, platform: str, browser: str) -> Path:
        if platform != SOURCE_YOUTUBE:
            raise ValueError("当前仅支持导入 YouTube 浏览器 Cookie。")
        browser_name = browser.strip().lower()
        if browser_name != "firefox":
            raise ValueError(
                "目前仅支持从 Firefox 导入 YouTube Cookie；Chrome / Edge 等内核浏览器无法导出可用的 YouTube Cookie。"
            )

        path = self.resolved_cookie_path(SOURCE_YOUTUBE)
        path.parent.mkdir(parents=True, exist_ok=True)

        try:
            from yt_dlp import YoutubeDL
        except ModuleNotFoundError:
            self._import_from_browser_with_cli(browser_name, path)
        else:
            self._import_from_browser_with_python(YoutubeDL, browser_name, path)

        if not self.has_cookie(SOURCE_YOUTUBE):
            raise RuntimeError("未能从浏览器导入有效 Cookie。请确认浏览器中已登录 YouTube。")
        return path

    def _import_from_browser_with_python(self, youtube_dl, browser_name: str, path: Path) -> None:
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "cookiesfrombrowser": (browser_name,),
            "cookiefile": str(path),
        }
        proxy_url = proxy_url_for_app_settings(self._settings())
        if proxy_url:
            options["proxy"] = proxy_url
        try:
            with youtube_dl(options) as ydl:
                ydl.cookiejar.save(str(path), ignore_discard=True, ignore_expires=True)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"从 {browser_name.title()} 导入 Cookie 失败：{exc}") from exc

    def _import_from_browser_with_cli(self, browser_name: str, path: Path) -> None:
        cli = shutil.which("yt-dlp")
        if not cli:
            raise RuntimeError("未找到 yt-dlp。请安装 yt-dlp 后再导入浏览器 Cookie。")
        command = [
            cli,
            "--cookies-from-browser",
            browser_name,
            "--cookies",
            str(path),
            "--skip-download",
            "--dump-single-json",
            "--no-warnings",
            "--no-update",
            "https://www.youtube.com/",
        ]
        proxy_args = proxy_cli_args_for_app_settings(self._settings())
        if proxy_args:
            command[1:1] = proxy_args
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
            timeout=60,
            env=subprocess_env_for_app_settings(self._settings()),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if completed.returncode != 0 and not path.exists():
            message = completed.stderr.strip() or completed.stdout.strip() or "yt-dlp 导入失败"
            raise RuntimeError(message)

    def _has_valid_sessdata_locally(self) -> bool:
        path = self.resolved_cookie_path(SOURCE_BILIBILI)
        if not path.is_file():
            return False

        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                domain, _flag, cookie_path, _secure, expires, name, value = parts[:7]
                domain = domain.lower()
                name = name.strip()
                value = value.strip()
                if "bilibili" not in domain:
                    continue
                if name != "SESSDATA" or not value:
                    continue
                if cookie_path not in ("/", ""):
                    continue
                if expires.isdigit() and int(expires) not in (0, 2147483647) and int(expires) < int(time.time()):
                    return False
                return True
        except Exception:
            return False
        return False

    def _fetch_nav_payload(self) -> dict:
        jar = self.load_cookie_jar()
        opener = self.build_opener(urllib.request.HTTPCookieProcessor(jar))
        request = urllib.request.Request(
            "https://api.bilibili.com/x/web-interface/nav",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            },
        )
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    def _fetch_bytes(self, url: str) -> bytes:
        if not url:
            return b""
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://www.bilibili.com/",
                },
            )
            with self.build_opener().open(request, timeout=15) as response:
                return response.read()
        except Exception:
            return b""
