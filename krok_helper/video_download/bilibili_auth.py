from __future__ import annotations

import email.utils
import http.cookies
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .cookie_manager import CookieManager


BILIBILI_QR_GENERATE_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
BILIBILI_QR_POLL_URL = "https://passport.bilibili.com/x/passport-login/web/qrcode/poll"
QR_IMAGE_API = "https://api.qrserver.com/v1/create-qr-code/?size=240x240&data={data}"
IMPORTANT_COOKIE_NAMES = ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid")


@dataclass(slots=True)
class QrLoginTicket:
    login_url: str
    qrcode_key: str
    image_bytes: bytes


@dataclass(slots=True)
class QrLoginStatus:
    code: int
    message: str
    success: bool = False


class BilibiliQrLoginService:
    def __init__(self, cookie_manager: CookieManager) -> None:
        self._cookie_manager = cookie_manager
        self._cookie_jar = cookie_manager.load_cookie_jar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookie_jar))

    def request_qr_ticket(self) -> QrLoginTicket:
        payload = self._request_json(BILIBILI_QR_GENERATE_URL)
        data = payload.get("data") or {}
        login_url = str(data.get("url") or "")
        qrcode_key = str(data.get("qrcode_key") or "")
        if not login_url or not qrcode_key:
            raise RuntimeError("Bilibili 未返回有效的二维码登录信息。")
        return QrLoginTicket(
            login_url=login_url,
            qrcode_key=qrcode_key,
            image_bytes=self._fetch_qr_image_bytes(login_url),
        )

    def poll_login(self, qrcode_key: str) -> QrLoginStatus:
        poll_url = f"{BILIBILI_QR_POLL_URL}?qrcode_key={urllib.parse.quote(qrcode_key)}"
        payload, response_headers = self._request_json_with_headers(poll_url)
        data = payload.get("data") or {}
        code = self._coerce_status_code(data.get("code"), payload.get("code"))
        message = self._coerce_status_message(data.get("message"), payload.get("message"))
        if code == 0:
            redirect_url = str(data.get("url") or "")
            self._persist_login_cookies(response_headers, redirect_url)
            if redirect_url:
                self._request_text(redirect_url)
            self._cookie_manager.save_cookie_jar(self._cookie_jar)
            return QrLoginStatus(code=code, message="登录成功", success=True)
        return QrLoginStatus(code=code, message=message or self._fallback_message(code), success=False)

    def _request_json(self, url: str) -> dict:
        with self._opener.open(self._make_request(url), timeout=20) as response:
            return json.load(response)

    def _request_json_with_headers(self, url: str) -> tuple[dict, object]:
        with self._opener.open(self._make_request(url), timeout=20) as response:
            return json.load(response), response.headers

    def _request_text(self, url: str) -> str:
        with self._opener.open(self._make_request(url), timeout=20) as response:
            return response.read().decode("utf-8", errors="ignore")

    def _make_request(self, url: str) -> urllib.request.Request:
        return urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://www.bilibili.com/",
            },
        )

    def _fetch_qr_image_bytes(self, login_url: str) -> bytes:
        qr_url = QR_IMAGE_API.format(data=urllib.parse.quote(login_url, safe=""))
        try:
            with urllib.request.urlopen(self._make_request(qr_url), timeout=20) as response:
                return response.read()
        except Exception:
            return b""

    def _persist_login_cookies(self, response_headers, redirect_url: str) -> None:
        self._merge_cookies_from_set_cookie(response_headers)
        self._merge_cookies_from_redirect_url(redirect_url)

    def _merge_cookies_from_set_cookie(self, response_headers) -> None:
        if hasattr(response_headers, "get_all"):
            raw_headers = response_headers.get_all("Set-Cookie") or []
        else:
            raw_headers = []

        parser = http.cookies.SimpleCookie()
        for raw in raw_headers:
            try:
                parser.load(raw)
            except Exception:
                continue

        for name in IMPORTANT_COOKIE_NAMES:
            morsel = parser.get(name)
            if morsel is None or not morsel.value:
                continue
            self._cookie_manager.set_cookie(
                self._cookie_jar,
                name=name,
                value=morsel.value,
                domain=morsel["domain"] or ".bilibili.com",
                path=morsel["path"] or "/",
                expires=self._parse_cookie_expires(morsel["expires"]),
                secure=bool(morsel["secure"]),
                http_only=bool(morsel["httponly"]),
            )

    def _merge_cookies_from_redirect_url(self, redirect_url: str) -> None:
        if not redirect_url:
            return
        parsed = urllib.parse.urlparse(redirect_url)
        query = urllib.parse.parse_qs(parsed.query, keep_blank_values=False)
        expires = None
        expires_values = query.get("Expires")
        if expires_values and expires_values[0].isdigit():
            expires = int(expires_values[0])

        for name in IMPORTANT_COOKIE_NAMES:
            values = query.get(name)
            if not values or not values[0]:
                continue
            self._cookie_manager.set_cookie(
                self._cookie_jar,
                name=name,
                value=values[0],
                expires=expires,
                secure=True,
                http_only=(name == "SESSDATA"),
            )

    def _parse_cookie_expires(self, value: str) -> int | None:
        if not value:
            return None
        try:
            parsed = email.utils.parsedate_tz(value)
            if parsed is None:
                return None
            return int(email.utils.mktime_tz(parsed))
        except Exception:
            return None

    def _coerce_status_code(self, *candidates: object) -> int:
        for candidate in candidates:
            if candidate is None or candidate == "":
                continue
            try:
                return int(candidate)
            except (TypeError, ValueError):
                continue
        return -1

    def _coerce_status_message(self, *candidates: object) -> str:
        for candidate in candidates:
            if candidate is None:
                continue
            message = str(candidate).strip()
            if message:
                return message
        return ""

    def _fallback_message(self, code: int) -> str:
        if code == 86101:
            return "未扫码"
        if code == 86090:
            return "已扫码，请在手机上确认"
        if code == 86038:
            return "二维码已过期"
        return "登录状态未知"
