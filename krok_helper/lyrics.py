from __future__ import annotations

import base64
import binascii
from concurrent.futures import ThreadPoolExecutor, as_completed
import gzip
import hashlib
import json
import random
import re
import time
import unicodedata
import zlib
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from krok_helper.config import APP_NAME, APP_VERSION


LYRICS_PREVIEW_LINE = "line"
LYRICS_PREVIEW_VERBATIM = "verbatim"
LYRICS_LANGUAGE_ORIGINAL = "original"
LYRICS_LANGUAGE_TRANSLATION = "translation"
DEFAULT_LYRICS_PROVIDER_IDS = ("qm", "kg", "ne", "lrclib")
DEFAULT_LYRICS_SEARCH_LIMIT = 25
PROVIDER_PAGE_SIZE = 25

_TIMESTAMP_PATTERN = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{2,3}))?\]")
_TOKEN_SPLIT_PATTERN = re.compile(r"[\s\-_/\\|,.;:!?()\[\]{}'\"`~]+")
_LRC_METADATA_LINE_PATTERN = re.compile(r"^\[(?:ar|al|au|by|offset|ti|tool):.*\]$", re.IGNORECASE)
_LRC_ANY_TIMESTAMP_PATTERN = re.compile(r"[\[<](\d{1,3}):(\d{2})(?:[.:](\d{2,3}))?[\]>]")
_LRC_TIMESTAMP_TOKEN_PATTERN = re.compile(r"(?P<open>[\[<])(\d{1,3}):(\d{2})(?:[.:](\d{2,3}))?(?P<close>[\]>])")
_INTRO_CREDIT_LINE_PATTERN = re.compile(
    r"^(?:作词|作曲|编曲|制作人|作詞|作曲|編曲|詞|曲|編|词|曲|编|lyric(?:s|ist)?|composer|arranger|producer|prod|歌手|演唱|vocal|artist)\s*[:：]",
    re.IGNORECASE,
)
_LINE_DURATION_PATTERN = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_KRC_WORD_PATTERN = re.compile(r"<(?P<start>\d+),(?P<duration>\d+),\d+>(?P<content>[^<]*)")
_YRC_WORD_PATTERN = re.compile(r"\((?P<start>\d+),(?P<duration>\d+),\d+\)(?P<content>[^()]*)")

_PROVIDER_DISPLAY_NAMES = {
    "ne": "网易云音乐",
    "qm": "QQ音乐",
    "kg": "酷狗音乐",
    "lrclib": "LRCLIB",
}
_PROVIDER_PRIORITIES = {
    "qm": 40,
    "kg": 30,
    "ne": 20,
    "lrclib": 10,
}
_KUGOU_SEARCH_DOMAINS = (
    "mobilecdnbj.kugou.com",
    "msearch.kugou.com",
    "mobiles.kugou.com",
    "msearchcdn.kugou.com",
)
_KUGOU_SIGN_SALT = "LnT6xpN3khm36zse0QzvmgTZ3waWdRSA"
_KUGOU_KRC_KEY = b"@Gaw^2tGQ61-\xce\xd2ni"


class LyricsSearchError(RuntimeError):
    """Raised when lyrics searching or lyrics loading fails."""


@dataclass(slots=True)
class LyricsSearchCandidate:
    provider_id: str
    provider_name: str
    track_id: str
    title: str
    artist: str
    album: str
    duration_seconds: float | None
    provider_payload: object | None = None
    lyrics_payload: object | None = None
    line_lyrics: str = ""
    verbatim_lyrics: str = ""
    plain_lyrics: str = ""
    translation_lyrics: str = ""
    source_url: str | None = None
    source_priority: int = 0
    provider_position: int = 10_000
    query_variant_index: int = 0
    lyrics_loaded: bool = False
    load_error: str = ""
    title_match_tier: int = 0
    title_score: float = 0.0
    artist_score: float = 0.0
    album_score: float = 0.0
    lyrics_score: float = 0.0
    display_score: float = 0.0
    match_source: str = ""

    @property
    def key(self) -> str:
        return f"{self.provider_id}:{self.track_id}"

    @property
    def has_synced_lyrics(self) -> bool:
        return bool(self.line_lyrics.strip() or self.verbatim_lyrics.strip())

    @property
    def best_available_lyrics(self) -> str:
        return self.verbatim_lyrics.strip() or self.line_lyrics.strip() or self.plain_lyrics.strip()

    @property
    def has_translation(self) -> bool:
        return bool(self.translation_lyrics.strip())


@dataclass(slots=True, frozen=True)
class LyricsPreview:
    text: str
    used_synced_lyrics: bool
    used_estimated_char_timing: bool


@dataclass(slots=True, frozen=True)
class LyricsSearchBatch:
    results: list[LyricsSearchCandidate]
    overflow_results: list[LyricsSearchCandidate]
    next_provider_pages: dict[str, int]
    has_more: bool


class LyricsProvider(Protocol):
    provider_id: str
    provider_name: str
    source_priority: int

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT, page: int = 1) -> list[LyricsSearchCandidate]:
        ...

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        ...


class LrclibFallbackProvider:
    provider_id = "lrclib"
    provider_name = _PROVIDER_DISPLAY_NAMES["lrclib"]
    source_priority = _PROVIDER_PRIORITIES["lrclib"]

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT, page: int = 1) -> list[LyricsSearchCandidate]:
        params = urlencode({"q": keyword})
        request = Request(
            f"https://lrclib.net/api/search?{params}",
            headers={
                "Accept": "application/json",
                "User-Agent": f"{APP_NAME}/{APP_VERSION}",
            },
        )
        try:
            payload = _load_json_from_request(request)
        except LyricsSearchError as exc:
            raise LyricsSearchError(f"LRCLIB 请求失败: {exc}") from exc

        if not isinstance(payload, list):
            raise LyricsSearchError("LRCLIB 返回数据格式异常。")

        page_size = max(1, limit)
        start_index = max(0, (page - 1) * page_size)
        end_index = start_index + page_size
        items: list[LyricsSearchCandidate] = []
        for entry in payload[start_index:end_index]:
            if not isinstance(entry, dict):
                continue
            plain_lyrics = str(entry.get("plainLyrics") or "").strip()
            line_lyrics = _sanitize_lrc_text(str(entry.get("syncedLyrics") or "").strip())
            items.append(
                LyricsSearchCandidate(
                    provider_id=self.provider_id,
                    provider_name=self.provider_name,
                    track_id=str(entry.get("id") or ""),
                    title=str(entry.get("trackName") or entry.get("name") or "").strip(),
                    artist=str(entry.get("artistName") or "").strip(),
                    album=str(entry.get("albumName") or "").strip(),
                    duration_seconds=_coerce_float(entry.get("duration")),
                    provider_payload=entry,
                    line_lyrics=line_lyrics,
                    verbatim_lyrics="",
                    plain_lyrics=plain_lyrics or _strip_lrc_timestamps(line_lyrics),
                    source_priority=self.source_priority,
                    provider_position=start_index + len(items) + 1,
                    lyrics_loaded=bool(plain_lyrics or line_lyrics),
                )
            )
        return items

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        if candidate.lyrics_loaded:
            return candidate
        payload = candidate.provider_payload
        if not isinstance(payload, dict):
            raise LyricsSearchError("LRCLIB 候选数据无效，无法加载歌词。")
        candidate.line_lyrics = _sanitize_lrc_text(str(payload.get("syncedLyrics") or "").strip())
        candidate.verbatim_lyrics = ""
        candidate.plain_lyrics = str(payload.get("plainLyrics") or "").strip() or _strip_lrc_timestamps(candidate.line_lyrics)
        candidate.lyrics_loaded = bool(candidate.line_lyrics or candidate.plain_lyrics)
        if not candidate.lyrics_loaded:
            raise LyricsSearchError("LRCLIB 没有返回可用歌词。")
        return candidate


class NeteaseLyricsProvider:
    provider_id = "ne"
    provider_name = _PROVIDER_DISPLAY_NAMES["ne"]
    source_priority = _PROVIDER_PRIORITIES["ne"]

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT, page: int = 1) -> list[LyricsSearchCandidate]:
        page_size = PROVIDER_PAGE_SIZE
        items: list[LyricsSearchCandidate] = []
        request_page = max(1, page)
        for current_page in range(request_page, request_page + _page_count(limit, page_size)):
            offset = (current_page - 1) * page_size
            url = "https://music.163.com/api/cloudsearch/pc?" + urlencode(
                {
                    "s": keyword,
                    "type": "1",
                    "offset": str(offset),
                    "limit": str(page_size),
                }
            )
            request = Request(
                url,
                headers={
                    "Referer": "https://music.163.com/",
                    "User-Agent": "Mozilla/5.0",
                },
            )
            try:
                payload = _load_json_from_request(request)
            except LyricsSearchError as exc:
                raise LyricsSearchError(f"{self.provider_name} 搜索失败: {exc}") from exc

            songs = payload.get("result", {}).get("songs", []) if isinstance(payload, dict) else []
            if not isinstance(songs, list) or not songs:
                break
            for song in songs:
                if not isinstance(song, dict):
                    continue
                artists = song.get("ar") or song.get("artists") or []
                artist_names = [str(artist.get("name") or "").strip() for artist in artists if isinstance(artist, dict)]
                album = song.get("al") or song.get("album") or {}
                items.append(
                    LyricsSearchCandidate(
                        provider_id=self.provider_id,
                        provider_name=self.provider_name,
                        track_id=str(song.get("id") or ""),
                        title=str(song.get("name") or "").strip(),
                        artist=" / ".join([name for name in artist_names if name]),
                        album=str(album.get("name") or "").strip() if isinstance(album, dict) else "",
                        duration_seconds=_coerce_float(song.get("dt")) / 1000.0 if song.get("dt") else None,
                        provider_payload={
                            "id": str(song.get("id") or ""),
                        },
                        source_priority=self.source_priority,
                        provider_position=offset + len(items) + 1,
                    )
                )
                if len(items) >= limit:
                    return items[:limit]
            if len(songs) < page_size:
                break
        return items[:limit]

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        if candidate.lyrics_loaded:
            return candidate
        track_id = candidate.track_id.strip()
        if not track_id:
            raise LyricsSearchError(f"{self.provider_name} 缺少歌曲 ID，无法加载歌词。")

        url = "https://music.163.com/api/song/lyric?" + urlencode(
            {
                "id": track_id,
                "lv": "-1",
                "kv": "-1",
                "tv": "-1",
                "rv": "-1",
                "yv": "1",
            }
        )
        request = Request(
            url,
            headers={
                "Referer": "https://music.163.com/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            payload = _load_json_from_request(request)
        except LyricsSearchError as exc:
            raise LyricsSearchError(f"{self.provider_name} 加载歌词失败: {exc}") from exc

        line_lyrics = _extract_nested_lyric(payload, "lrc")
        verbatim_lyrics = ""
        yrc_lyrics = _extract_nested_lyric(payload, "yrc") or _extract_nested_lyric(payload, "klyric")
        if yrc_lyrics:
            converted_line, converted_verbatim, converted_plain = _convert_yrc_text(yrc_lyrics)
            line_lyrics = converted_line or line_lyrics
            verbatim_lyrics = converted_verbatim
            candidate.plain_lyrics = converted_plain
        plain_lyrics = candidate.plain_lyrics or _strip_lrc_timestamps(line_lyrics)
        translation_lyrics = _extract_nested_lyric(payload, "tlyric")
        if not line_lyrics and not plain_lyrics:
            raise LyricsSearchError(f"{self.provider_name} 没有返回可用歌词。")

        candidate.lyrics_payload = payload
        candidate.line_lyrics = line_lyrics
        candidate.verbatim_lyrics = verbatim_lyrics
        candidate.plain_lyrics = plain_lyrics
        candidate.translation_lyrics = translation_lyrics
        candidate.lyrics_loaded = True
        return candidate


class QqMusicLyricsProvider:
    provider_id = "qm"
    provider_name = _PROVIDER_DISPLAY_NAMES["qm"]
    source_priority = _PROVIDER_PRIORITIES["qm"]

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT, page: int = 1) -> list[LyricsSearchCandidate]:
        page_size = PROVIDER_PAGE_SIZE
        items: list[LyricsSearchCandidate] = []
        request_page = max(1, page)
        for current_page in range(request_page, request_page + _page_count(limit, page_size)):
            payload = {
                "comm": {
                    "ct": 11,
                    "cv": "1003006",
                    "v": "1003006",
                    "os_ver": "15",
                    "phonetype": "24122RKC7C",
                    "rom": "Redmi/miro/miro:15/AE3A.240806.005/OS2.0.105.0.VOMCNXM:user/release-keys",
                    "tmeAppID": "qqmusiclight",
                    "nettype": "NETWORK_WIFI",
                    "udid": "0",
                },
                "request": {
                    "method": "DoSearchForQQMusicLite",
                    "module": "music.search.SearchCgiService",
                    "param": {
                        "search_id": str(random.randint(10**16, 10**17 - 1)),
                        "remoteplace": "search.android.keyboard",
                        "query": keyword,
                        "search_type": 0,
                        "num_per_page": page_size,
                        "page_num": current_page,
                        "highlight": 0,
                        "nqc_flag": 0,
                        "page_id": 1,
                        "grp": 1,
                    },
                },
            }
            request = Request(
                "https://u.y.qq.com/cgi-bin/musicu.fcg",
                data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
                headers={
                    "Accept-Encoding": "gzip",
                    "Content-Type": "application/json",
                    "Cookie": "tmeLoginType=-1;",
                    "User-Agent": "okhttp/3.14.9",
                },
                method="POST",
            )
            try:
                response = _load_json_from_request(request)
            except LyricsSearchError as exc:
                raise LyricsSearchError(f"{self.provider_name} 搜索失败: {exc}") from exc

            songs = response.get("request", {}).get("data", {}).get("body", {}).get("item_song", [])
            if not isinstance(songs, list) or not songs:
                break
            for song in songs:
                if not isinstance(song, dict):
                    continue
                singers = song.get("singer") or []
                singer_names = [str(singer.get("name") or "").strip() for singer in singers if isinstance(singer, dict)]
                album = song.get("album") or {}
                items.append(
                    LyricsSearchCandidate(
                        provider_id=self.provider_id,
                        provider_name=self.provider_name,
                        track_id=str(song.get("id") or ""),
                        title=str(song.get("title") or "").strip(),
                        artist=" / ".join([name for name in singer_names if name]),
                        album=str(album.get("name") or "").strip() if isinstance(album, dict) else "",
                        duration_seconds=_coerce_float(song.get("interval")),
                        provider_payload={
                            "id": str(song.get("id") or ""),
                            "mid": str(song.get("mid") or ""),
                        },
                        source_priority=self.source_priority,
                        provider_position=((current_page - 1) * page_size) + len(items) + 1,
                    )
                )
                if len(items) >= limit:
                    return items[:limit]
            if len(songs) < page_size:
                break
        return items[:limit]

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        if candidate.lyrics_loaded:
            return candidate
        payload = candidate.provider_payload if isinstance(candidate.provider_payload, dict) else {}
        song_mid = str(payload.get("mid") or "").strip()
        song_id = _coerce_int(payload.get("id") or candidate.track_id)
        if not song_mid:
            raise LyricsSearchError(f"{self.provider_name} 缺少 songmid，无法加载歌词。")

        url = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?" + urlencode(
            {
                "songmid": song_mid,
                "g_tk": "5381",
                "loginUin": "0",
                "hostUin": "0",
                "format": "json",
                "inCharset": "utf8",
                "outCharset": "utf-8",
                "notice": "0",
                "platform": "yqq.json",
                "needNewCode": "0",
                "nobase64": "1",
            }
        )
        request = Request(
            url,
            headers={
                "Referer": "https://y.qq.com/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            response = _load_json_from_request(request)
        except LyricsSearchError as exc:
            raise LyricsSearchError(f"{self.provider_name} 加载歌词失败: {exc}") from exc

        line_lyrics = _coerce_qq_lyric(response.get("lyric"))
        plain_lyrics = _strip_lrc_timestamps(line_lyrics)
        translation_lyrics = _strip_qq_translation_placeholders(_coerce_qq_lyric(response.get("trans")))
        modern_payload: dict = {}
        if not translation_lyrics:
            try:
                modern_payload = self._fetch_play_lyric_info(song_mid, song_id)
            except LyricsSearchError:
                modern_payload = {}
            if modern_payload:
                translation_lyrics = _strip_qq_translation_placeholders(
                    _coerce_qq_lyric(
                        modern_payload.get("trans")
                        or modern_payload.get("transLyric")
                        or modern_payload.get("translate")
                        or modern_payload.get("translation")
                        or modern_payload.get("lt_lyric")
                    )
                )
                if not line_lyrics:
                    line_lyrics = _coerce_qq_lyric(modern_payload.get("lyric"))
                    plain_lyrics = _strip_lrc_timestamps(line_lyrics)
        if not line_lyrics and not plain_lyrics:
            raise LyricsSearchError(f"{self.provider_name} 没有返回可用歌词。")

        if modern_payload:
            response = dict(response)
            response["modern"] = modern_payload
        candidate.lyrics_payload = response
        candidate.line_lyrics = line_lyrics
        candidate.verbatim_lyrics = ""
        candidate.plain_lyrics = plain_lyrics
        candidate.translation_lyrics = translation_lyrics
        candidate.lyrics_loaded = True
        return candidate

    def _fetch_play_lyric_info(self, song_mid: str, song_id: int | None = None) -> dict:
        payload = {
            "comm": {
                "ct": 11,
                "cv": "1003006",
                "v": "1003006",
                "tmeAppID": "qqmusic",
            },
            "req": {
                "module": "music.musichallSong.PlayLyricInfo",
                "method": "GetPlayLyricInfo",
                "param": {
                    "songMID": song_mid,
                    "songID": song_id or 0,
                    "qrc": 0,
                    "trans": 1,
                    "roma": 0,
                },
            },
        }
        request = Request(
            "https://u.y.qq.com/cgi-bin/musicu.fcg",
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Referer": "https://y.qq.com/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        response = _load_json_from_request(request)
        if not isinstance(response, dict):
            return {}
        data = response.get("req", {})
        if not isinstance(data, dict):
            return {}
        lyric_data = data.get("data", {})
        return lyric_data if isinstance(lyric_data, dict) else {}


class KugouLyricsProvider:
    provider_id = "kg"
    provider_name = _PROVIDER_DISPLAY_NAMES["kg"]
    source_priority = _PROVIDER_PRIORITIES["kg"]

    def search(self, keyword: str, *, limit: int = DEFAULT_LYRICS_SEARCH_LIMIT, page: int = 1) -> list[LyricsSearchCandidate]:
        page_size = PROVIDER_PAGE_SIZE
        items: list[LyricsSearchCandidate] = []
        request_page = max(1, page)
        for current_page in range(request_page, request_page + _page_count(limit, page_size)):
            url = "http://mobilecdnbj.kugou.com/api/v3/search/song?" + urlencode(
                {
                    "showtype": "14",
                    "highlight": "",
                    "pagesize": str(page_size),
                    "tag_aggr": "1",
                    "plat": "0",
                    "sver": "5",
                    "keyword": keyword,
                    "correct": "1",
                    "api_ver": "1",
                    "version": "9108",
                    "page": str(current_page),
                }
            )
            request = Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                payload = _load_json_from_request(request)
            except LyricsSearchError as exc:
                raise LyricsSearchError(f"{self.provider_name} 搜索失败: {exc}") from exc

            songs = payload.get("data", {}).get("info", []) if isinstance(payload, dict) else []
            if not isinstance(songs, list) or not songs:
                break
            for song in songs:
                if not isinstance(song, dict):
                    continue
                items.append(
                    LyricsSearchCandidate(
                        provider_id=self.provider_id,
                        provider_name=self.provider_name,
                        track_id=str(song.get("album_audio_id") or song.get("audio_id") or song.get("hash") or ""),
                        title=str(song.get("songname") or "").strip(),
                        artist=str(song.get("singername") or "").replace("、", " / ").strip(),
                        album=str(song.get("album_name") or "").strip(),
                        duration_seconds=_coerce_float(song.get("duration")),
                        provider_payload={
                            "hash": str(song.get("hash") or ""),
                            "album_audio_id": str(song.get("album_audio_id") or ""),
                            "duration_ms": int(_coerce_float(song.get("duration")) * 1000) if song.get("duration") else 0,
                        },
                        source_priority=self.source_priority,
                        provider_position=((current_page - 1) * page_size) + len(items) + 1,
                    )
                )
                if len(items) >= limit:
                    return items[:limit]
            if len(songs) < page_size:
                break
        return items[:limit]

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        if candidate.lyrics_loaded:
            return candidate
        payload = candidate.provider_payload if isinstance(candidate.provider_payload, dict) else {}
        song_hash = str(payload.get("hash") or "").strip()
        album_audio_id = str(payload.get("album_audio_id") or candidate.track_id or "").strip()
        duration_ms = int(payload.get("duration_ms") or round((candidate.duration_seconds or 0) * 1000))
        if not song_hash or not album_audio_id or duration_ms <= 0:
            raise LyricsSearchError(f"{self.provider_name} 缺少必要参数，无法加载歌词。")

        search_params = {
            "album_audio_id": album_audio_id,
            "duration": str(duration_ms),
            "hash": song_hash,
            "keyword": f"{candidate.artist or ''} - {candidate.title or ''}".strip(" -"),
            "lrctxt": "1",
            "man": "no",
        }
        try:
            search_response = self._request_lyric_api("https://lyrics.kugou.com/v1/search", search_params)
        except LyricsSearchError as exc:
            raise LyricsSearchError(f"{self.provider_name} 加载歌词失败: {exc}") from exc

        lyric_candidates = search_response.get("candidates", []) if isinstance(search_response, dict) else []
        if not isinstance(lyric_candidates, list) or not lyric_candidates:
            raise LyricsSearchError(f"{self.provider_name} 没有找到歌词。")

        lyric_info = lyric_candidates[0]
        if not isinstance(lyric_info, dict):
            raise LyricsSearchError(f"{self.provider_name} 返回了无效歌词数据。")

        download_params = {
            "accesskey": str(lyric_info.get("accesskey") or ""),
            "charset": "utf8",
            "client": "mobi",
            "fmt": "krc",
            "id": str(lyric_info.get("id") or ""),
            "ver": "1",
        }
        try:
            download_response = self._request_lyric_api("http://lyrics.kugou.com/download", download_params)
        except LyricsSearchError as exc:
            raise LyricsSearchError(f"{self.provider_name} 下载歌词失败: {exc}") from exc

        content = str(download_response.get("content") or "")
        if not content:
            raise LyricsSearchError(f"{self.provider_name} 没有返回歌词内容。")

        content_type = int(download_response.get("contenttype") or 0)
        if content_type == 2:
            plain_text = base64.b64decode(content).decode("utf-8", "replace").strip()
            candidate.line_lyrics = _sanitize_lrc_text(plain_text) if "[" in plain_text and "]" in plain_text else ""
            candidate.verbatim_lyrics = ""
            candidate.plain_lyrics = plain_text if not candidate.line_lyrics else _strip_lrc_timestamps(candidate.line_lyrics)
        else:
            try:
                raw_krc = _decrypt_kugou_krc(base64.b64decode(content))
            except (ValueError, zlib.error, binascii.Error) as exc:
                raise LyricsSearchError(f"{self.provider_name} 解密歌词失败: {exc}") from exc
            line_lyrics, verbatim_lyrics, plain_lyrics, translation_lyrics = _convert_krc_text(raw_krc)
            candidate.line_lyrics = line_lyrics
            candidate.verbatim_lyrics = verbatim_lyrics
            candidate.plain_lyrics = plain_lyrics
            candidate.translation_lyrics = translation_lyrics

        candidate.lyrics_payload = {"lyric_info": lyric_info, "download": download_response}
        candidate.lyrics_loaded = bool(candidate.line_lyrics or candidate.verbatim_lyrics or candidate.plain_lyrics)
        if not candidate.lyrics_loaded:
            raise LyricsSearchError(f"{self.provider_name} 没有返回可显示的歌词。")
        return candidate

    def _request_lyric_api(self, url: str, params: dict[str, str]) -> dict:
        base_params = {
            "appid": "3116",
            "clientver": "11070",
            **params,
        }
        signature = _build_kugou_signature(base_params)
        request_url = url + "?" + urlencode({**base_params, "signature": signature})
        mid = hashlib.md5(str(int(time.time() * 1000)).encode("utf-8")).hexdigest()
        request = Request(
            request_url,
            headers={
                "Accept-Encoding": "gzip, deflate",
                "Connection": "Keep-Alive",
                "KG-CLIENTTIMEMS": str(int(time.time() * 1000)),
                "KG-RC": "1",
                "KG-Rec": "1",
                "User-Agent": "Android14-1070-11070-201-0-Lyric-wifi",
                "mid": mid,
            },
        )
        payload = _load_json_from_request(request)
        error_code = payload.get("error_code", payload.get("errcode", 0)) if isinstance(payload, dict) else 0
        if error_code not in (0, 200):
            raise LyricsSearchError(str(payload.get("error_msg") or payload.get("errmsg") or "接口返回错误"))
        return payload


class LyricsSearchService:
    def __init__(self, providers: list[LyricsProvider] | None = None) -> None:
        self.providers = providers or build_default_providers()
        self.provider_map = {provider.provider_id: provider for provider in self.providers}

    def search(
        self,
        keyword: str,
        *,
        provider_ids: tuple[str, ...] = DEFAULT_LYRICS_PROVIDER_IDS,
        limit: int = DEFAULT_LYRICS_SEARCH_LIMIT,
    ) -> list[LyricsSearchCandidate]:
        return self.search_batch(keyword, provider_ids=provider_ids, limit=limit).results

    def search_batch(
        self,
        keyword: str,
        *,
        provider_ids: tuple[str, ...] = DEFAULT_LYRICS_PROVIDER_IDS,
        limit: int = DEFAULT_LYRICS_SEARCH_LIMIT,
        provider_pages: dict[str, int] | None = None,
    ) -> LyricsSearchBatch:
        normalized_keyword = " ".join(keyword.split())
        if not normalized_keyword:
            return LyricsSearchBatch(results=[], overflow_results=[], next_provider_pages={}, has_more=False)

        allowed_providers = [provider for provider in self.providers if provider.provider_id in provider_ids]
        if not allowed_providers:
            raise LyricsSearchError("没有可用的歌词来源。")

        ranked: dict[str, LyricsSearchCandidate] = {}
        errors: list[str] = []
        next_provider_pages: dict[str, int] = {}
        request_pages = {provider.provider_id: max(1, (provider_pages or {}).get(provider.provider_id, 1)) for provider in allowed_providers}

        def fetch_provider_page(provider: LyricsProvider) -> tuple[str, int, list[LyricsSearchCandidate]]:
            page = request_pages[provider.provider_id]
            return provider.provider_id, page, provider.search(normalized_keyword, limit=PROVIDER_PAGE_SIZE, page=page)

        with ThreadPoolExecutor(max_workers=max(1, len(allowed_providers))) as executor:
            future_map = {executor.submit(fetch_provider_page, provider): provider for provider in allowed_providers}
            for future in as_completed(future_map):
                provider = future_map[future]
                try:
                    provider_id, page, results = future.result()
                except LyricsSearchError as exc:
                    errors.append(str(exc))
                    continue
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{provider.provider_name} 搜索失败: {exc}")
                    continue

                if len(results) >= PROVIDER_PAGE_SIZE:
                    next_provider_pages[provider_id] = page + 1
                for candidate in results:
                    candidate.query_variant_index = 0
                    _rank_candidate(candidate, normalized_keyword)
                    existing = ranked.get(candidate.key)
                    if existing is None or _sort_key(candidate) > _sort_key(existing):
                        ranked[candidate.key] = candidate

        if not ranked and errors:
            raise LyricsSearchError("\n".join(list(dict.fromkeys(errors))))

        ordered = sorted(ranked.values(), key=_sort_key, reverse=True)
        visible_results = ordered[:limit]
        overflow_results = ordered[limit:]
        return LyricsSearchBatch(
            results=visible_results,
            overflow_results=overflow_results,
            next_provider_pages=next_provider_pages,
            has_more=bool(next_provider_pages or overflow_results),
        )

    def fetch_lyrics(self, candidate: LyricsSearchCandidate) -> LyricsSearchCandidate:
        provider = self.provider_map.get(candidate.provider_id)
        if provider is None:
            raise LyricsSearchError(f"未找到歌词来源适配器: {candidate.provider_name}")
        return provider.fetch_lyrics(candidate)


def build_default_providers() -> list[LyricsProvider]:
    return [
        QqMusicLyricsProvider(),
        KugouLyricsProvider(),
        NeteaseLyricsProvider(),
        LrclibFallbackProvider(),
    ]


def build_lyrics_preview(
    candidate: LyricsSearchCandidate,
    preview_mode: str,
    *,
    strip_intro_lines: bool = True,
    language: str = LYRICS_LANGUAGE_ORIGINAL,
) -> LyricsPreview:
    if strip_intro_lines:
        line_lyrics = _strip_leading_intro_lines(
            candidate.line_lyrics,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
        )
        verbatim_lyrics = _strip_leading_intro_lines(
            candidate.verbatim_lyrics,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
        )
        plain_lyrics = _strip_leading_intro_lines(
            candidate.plain_lyrics,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
        )
        translation_lyrics = _strip_leading_intro_lines(
            candidate.translation_lyrics,
            title=candidate.title,
            artist=candidate.artist,
            album=candidate.album,
        )
    else:
        line_lyrics = candidate.line_lyrics
        verbatim_lyrics = candidate.verbatim_lyrics
        plain_lyrics = candidate.plain_lyrics
        translation_lyrics = candidate.translation_lyrics

    if language == LYRICS_LANGUAGE_TRANSLATION and translation_lyrics.strip():
        return LyricsPreview(
            text=translation_lyrics.strip(),
            used_synced_lyrics=True,
            used_estimated_char_timing=False,
        )

    if preview_mode == LYRICS_PREVIEW_VERBATIM:
        if verbatim_lyrics.strip():
            return LyricsPreview(
                text=verbatim_lyrics.strip(),
                used_synced_lyrics=True,
                used_estimated_char_timing=False,
            )
        if line_lyrics.strip():
            return LyricsPreview(
                text=_build_estimated_verbatim_lrc(parse_lrc_lines(line_lyrics)),
                used_synced_lyrics=True,
                used_estimated_char_timing=True,
            )
        return LyricsPreview(
            text=plain_lyrics.strip(),
            used_synced_lyrics=False,
            used_estimated_char_timing=False,
        )

    if line_lyrics.strip():
        return LyricsPreview(
            text=line_lyrics.strip(),
            used_synced_lyrics=True,
            used_estimated_char_timing=False,
        )

    return LyricsPreview(
        text=plain_lyrics.strip(),
        used_synced_lyrics=False,
        used_estimated_char_timing=False,
    )


@dataclass(slots=True, frozen=True)
class ParsedLrcLine:
    start_ms: int
    text: str


def parse_lrc_lines(text: str) -> list[ParsedLrcLine]:
    lines: list[ParsedLrcLine] = []
    for raw_line in text.splitlines():
        matches = list(_TIMESTAMP_PATTERN.finditer(raw_line))
        if not matches:
            continue
        lyric_text = _TIMESTAMP_PATTERN.sub("", raw_line).strip()
        for match in matches:
            lines.append(ParsedLrcLine(start_ms=_parse_timestamp_match(match), text=lyric_text))
    return sorted(lines, key=lambda line: line.start_ms)


def format_lrc_timestamp(milliseconds: int) -> str:
    total_cs = max(0, int(milliseconds) + 5) // 10
    minutes = total_cs // 6_000
    seconds = (total_cs % 6_000) // 100
    hundredths = total_cs % 100
    return f"{minutes:02}:{seconds:02}.{hundredths:02}"


def _build_estimated_verbatim_lrc(lines: list[ParsedLrcLine]) -> str:
    rendered_lines: list[str] = []
    for index, line in enumerate(lines):
        if not line.text:
            continue
        next_start_ms = lines[index + 1].start_ms if index + 1 < len(lines) else None
        line_end_ms = _estimate_line_end_ms(line.start_ms, next_start_ms, line.text)
        rendered_lines.append(_render_verbatim_line(line.start_ms, line_end_ms, line.text))
    return "\n".join(rendered_lines)


def _render_verbatim_line(start_ms: int, end_ms: int, text: str) -> str:
    visible_count = sum(1 for char in text if not char.isspace())
    if visible_count <= 0:
        return f"[{format_lrc_timestamp(start_ms)}]{text}"

    span_ms = max(end_ms - start_ms, visible_count * 40)
    step_ms = max(span_ms // visible_count, 40)
    current_index = 0
    parts: list[str] = []
    for char in text:
        if char.isspace():
            parts.append(char)
            continue
        char_start = start_ms + min(current_index * step_ms, span_ms)
        parts.append(f"[{format_lrc_timestamp(char_start)}]{char}")
        current_index += 1
    return "".join(parts)


def _estimate_line_end_ms(start_ms: int, next_start_ms: int | None, text: str) -> int:
    if next_start_ms is not None and next_start_ms > start_ms:
        return next_start_ms
    visible_chars = max(1, sum(1 for char in text if not char.isspace()))
    estimated_span = min(max(visible_chars * 140, 1_200), 7_000)
    return start_ms + estimated_span


def _build_query_variants(keyword: str) -> list[str]:
    variants: list[str] = []

    def add(candidate: str) -> None:
        cleaned = " ".join(candidate.split())
        if cleaned and cleaned not in variants:
            variants.append(cleaned)

    add(keyword)
    for separator in (" - ", " / ", "、", "|"):
        if separator in keyword:
            parts = [part.strip() for part in keyword.split(separator) if part.strip()]
            if len(parts) >= 2:
                add(" ".join(parts))
                for part in parts:
                    add(part)
            break
    return variants


def _rank_candidate(candidate: LyricsSearchCandidate, keyword: str) -> None:
    candidate.title_match_tier = _title_match_tier(keyword, candidate.title)
    candidate.title_score = _score_text(keyword, candidate.title)
    candidate.artist_score = _score_text(keyword, candidate.artist)
    candidate.album_score = _score_text(keyword, candidate.album)
    candidate.lyrics_score = _score_text(keyword, candidate.plain_lyrics or candidate.line_lyrics)
    candidate.display_score = round(
        candidate.title_score * 0.55
        + candidate.artist_score * 0.25
        + candidate.album_score * 0.12
        + candidate.lyrics_score * 0.08
        + (candidate.source_priority / 20.0),
        1,
    )
    candidate.match_source = _best_match_source(candidate)


def _best_match_source(candidate: LyricsSearchCandidate) -> str:
    field_scores = {
        "歌名": candidate.title_score,
        "歌手": candidate.artist_score,
        "专辑": candidate.album_score,
        "歌词片段": candidate.lyrics_score,
    }
    return max(field_scores.items(), key=lambda item: item[1])[0]


def _sort_key(candidate: LyricsSearchCandidate) -> tuple[int, int, int, int, float, float, float, float, float]:
    return (
        candidate.title_match_tier,
        -candidate.query_variant_index,
        -candidate.provider_position,
        candidate.source_priority,
        candidate.title_score,
        candidate.artist_score,
        candidate.album_score,
        candidate.lyrics_score,
        candidate.display_score,
    )


def _title_match_tier(keyword: str, title: str) -> int:
    normalized_keyword = _normalize_text(keyword)
    normalized_title = _normalize_text(title)
    if not normalized_keyword or not normalized_title:
        return 0
    if normalized_keyword == normalized_title:
        return 4

    compact_keyword = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", normalized_keyword)
    compact_title = re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", normalized_title)
    if compact_keyword and compact_keyword == compact_title:
        return 4
    if normalized_title.startswith(normalized_keyword) or compact_title.startswith(compact_keyword):
        return 3
    if normalized_keyword in normalized_title or (compact_keyword and compact_keyword in compact_title):
        return 2
    return 1 if _score_text(keyword, title) >= 80 else 0


def _score_text(keyword: str, text: str) -> float:
    normalized_keyword = _normalize_text(keyword)
    normalized_text = _normalize_text(text)
    if not normalized_keyword or not normalized_text:
        return 0.0
    if normalized_keyword == normalized_text:
        return 100.0

    score = SequenceMatcher(None, normalized_keyword, normalized_text).ratio() * 100
    if normalized_keyword in normalized_text:
        score = max(score, 92.0)

    keyword_tokens = _tokenize(normalized_keyword)
    text_tokens = _tokenize(normalized_text)
    if keyword_tokens and text_tokens:
        overlap = sum(1 for token in keyword_tokens if token in text_tokens)
        coverage = overlap / len(keyword_tokens)
        if coverage:
            score = max(score, coverage * 95.0)
        prefix_hits = sum(1 for token in keyword_tokens if any(text_token.startswith(token) for text_token in text_tokens))
        if prefix_hits:
            score = max(score, prefix_hits / len(keyword_tokens) * 90.0)

    return round(min(score, 100.0), 2)


def _tokenize(text: str) -> list[str]:
    return [token for token in _TOKEN_SPLIT_PATTERN.split(text) if token]


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = normalized.casefold()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def _parse_timestamp_match(match: re.Match[str]) -> int:
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3) or "00"
    milliseconds = int(fraction.ljust(3, "0")[:3])
    return minutes * 60_000 + seconds * 1_000 + milliseconds


def _coerce_float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _sanitize_lrc_text(text: str) -> str:
    if not text.strip():
        return ""
    raw_lines = [_normalize_lyric_spacing(line).strip() for line in text.splitlines()]
    has_timestamped_lines = any(_LRC_ANY_TIMESTAMP_PATTERN.search(line) for line in raw_lines)
    kept_lines: list[str] = []
    for raw_line in raw_lines:
        if not raw_line or _LRC_METADATA_LINE_PATTERN.match(raw_line):
            continue
        if has_timestamped_lines and not _LRC_ANY_TIMESTAMP_PATTERN.search(raw_line):
            continue
        normalized_line = _normalize_lrc_timestamp_tokens(raw_line)
        normalized_line = _remove_spaces_after_timestamps(normalized_line)
        lyric_body = _LRC_TIMESTAMP_TOKEN_PATTERN.sub("", normalized_line).strip()
        if not lyric_body:
            continue
        kept_lines.append(normalized_line)
    return "\n".join(kept_lines).strip()


def _strip_lrc_timestamps(text: str) -> str:
    if not text.strip():
        return ""
    body_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _normalize_lyric_spacing(raw_line).strip()
        if not line or _LRC_METADATA_LINE_PATTERN.match(line):
            continue
        body_lines.append(_LRC_TIMESTAMP_TOKEN_PATTERN.sub("", line).strip())
    return "\n".join([line for line in body_lines if line]).strip()


def _normalize_lrc_timestamp_tokens(text: str) -> str:
    def repl(match: re.Match[str]) -> str:
        open_token = match.group("open")
        close_token = match.group("close")
        minutes = int(match.group(2))
        seconds = int(match.group(3))
        fraction = match.group(4) or "00"
        timestamp_ms = minutes * 60_000 + seconds * 1_000 + int(fraction.ljust(3, "0")[:3])
        return f"{open_token}{format_lrc_timestamp(timestamp_ms)}{close_token}"

    return _LRC_TIMESTAMP_TOKEN_PATTERN.sub(repl, text)


def _remove_spaces_after_timestamps(text: str) -> str:
    return re.sub(r"(?<=[\]>])[ \t]+", "", text)


def _normalize_lyric_spacing(text: str) -> str:
    return str(text or "").replace("\u3000", " ")


def _strip_leading_intro_lines(text: str, *, title: str = "", artist: str = "", album: str = "") -> str:
    if not text.strip():
        return ""

    raw_lines = [line.strip() for line in text.splitlines() if line.strip()]
    drop_count = 0
    max_probe_lines = min(5, len(raw_lines))
    for index in range(max_probe_lines):
        content = _LRC_TIMESTAMP_TOKEN_PATTERN.sub("", raw_lines[index]).strip()
        if _looks_like_intro_credit_line_v2(content, title=title, artist=artist, album=album):
            drop_count += 1
            continue
        break
    return "\n".join(raw_lines[drop_count:]).strip()


def _looks_like_intro_credit_line(text: str, *, title: str = "", artist: str = "", album: str = "") -> bool:
    content = text.strip()
    if not content:
        return False
    if _INTRO_CREDIT_LINE_PATTERN.match(content):
        return True
    separator_index = min((index for index in (content.find(":"), content.find("：")) if index >= 0), default=-1)
    if 0 < separator_index <= 6 and len(content) <= 40:
        return True

    compact_content = _compact_compare_text(content)
    compact_title = _compact_compare_text(title)
    compact_artist = _compact_compare_text(artist)
    compact_album = _compact_compare_text(album)

    if compact_title and compact_artist and compact_title in compact_content and compact_artist in compact_content:
        remainder = compact_content.replace(compact_title, "", 1).replace(compact_artist, "", 1)
        if len(remainder) <= 4:
            return True
    if compact_title and compact_album and compact_title in compact_content and compact_album in compact_content:
        remainder = compact_content.replace(compact_title, "", 1).replace(compact_album, "", 1)
        if len(remainder) <= 4:
            return True
    normalized_content = _normalize_text(content)
    normalized_title = _normalize_text(title)
    if normalized_title and normalized_content.startswith(normalized_title) and len(content) <= 80:
        tail = normalized_content[len(normalized_title):].strip()
        if tail.startswith(("-", "–", "—", "/", "／")) or any(separator in content for separator in (" - ", " / ", "/", "／")):
            return True
    return False


def _compact_compare_text(text: str) -> str:
    return re.sub(r"[^0-9a-z\u3040-\u30ff\u3400-\u9fff]+", "", _normalize_text(text))


def _looks_like_intro_credit_line_v2(text: str, *, title: str = "", artist: str = "", album: str = "") -> bool:
    content = text.strip()
    if not content:
        return False
    if _INTRO_CREDIT_LINE_PATTERN.match(content):
        return True

    separator_index = min((index for index in (content.find(":"), content.find("：")) if index >= 0), default=-1)
    if 0 < separator_index <= 8 and len(content) <= 48:
        return True

    compact_content = _compact_compare_text(content)
    compact_title = _compact_compare_text(title)
    compact_artist = _compact_compare_text(artist)
    compact_album = _compact_compare_text(album)
    compact_title_base = _compact_compare_text(_strip_parenthetical_segments(title))
    compact_artist_base = _compact_compare_text(_strip_parenthetical_segments(artist))
    compact_album_base = _compact_compare_text(_strip_parenthetical_segments(album))

    if compact_title and compact_artist and compact_title in compact_content and compact_artist in compact_content:
        remainder = compact_content.replace(compact_title, "", 1).replace(compact_artist, "", 1)
        if len(remainder) <= 8:
            return True
    if compact_title and compact_album and compact_title in compact_content and compact_album in compact_content:
        remainder = compact_content.replace(compact_title, "", 1).replace(compact_album, "", 1)
        if len(remainder) <= 8:
            return True
    if compact_title_base and compact_artist_base and compact_title_base in compact_content and compact_artist_base in compact_content:
        remainder = compact_content.replace(compact_title_base, "", 1).replace(compact_artist_base, "", 1)
        if len(remainder) <= 14:
            return True
    if compact_title_base and compact_album_base and compact_title_base in compact_content and compact_album_base in compact_content:
        remainder = compact_content.replace(compact_title_base, "", 1).replace(compact_album_base, "", 1)
        if len(remainder) <= 14:
            return True

    normalized_content = _normalize_text(content)
    normalized_title = _normalize_text(title)
    normalized_title_base = _normalize_text(_strip_parenthetical_segments(title))
    if normalized_title and normalized_content.startswith(normalized_title) and len(content) <= 96:
        tail = normalized_content[len(normalized_title):].strip()
        if tail.startswith(("-", "–", "—", "/", "／")) or any(separator in content for separator in (" - ", " / ", "/", "／")):
            return True
    if normalized_title_base and normalized_content.startswith(normalized_title_base) and len(content) <= 96:
        tail = normalized_content[len(normalized_title_base):].strip()
        if tail.startswith(("-", "–", "—", "/", "／")) or any(separator in content for separator in (" - ", " / ", "/", "／")):
            return True
    return False


def _strip_parenthetical_segments(text: str) -> str:
    return re.sub(r"[\(\[\{（【「『].*?[\)\]\}）】」』]", "", text or "").strip()


def _page_count(limit: int, page_size: int) -> int:
    return max(1, (max(limit, 1) + page_size - 1) // page_size)


def _load_json_from_request(request: Request) -> dict | list:
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read()
            content_encoding = response.headers.get("Content-Encoding", "")
    except HTTPError as exc:
        raise LyricsSearchError(f"HTTP {exc.code}") from exc
    except URLError as exc:
        raise LyricsSearchError(f"网络错误: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LyricsSearchError("请求超时") from exc

    if "gzip" in content_encoding.lower() or payload[:2] == b"\x1f\x8b":
        payload = gzip.decompress(payload)
    try:
        return json.loads(payload.decode("utf-8", "replace"))
    except json.JSONDecodeError as exc:
        raise LyricsSearchError("返回了无法解析的数据") from exc


def _extract_nested_lyric(payload: object, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    value = payload.get(key)
    if not isinstance(value, dict):
        return ""
    lyric = str(value.get("lyric") or "").strip()
    if not lyric:
        return ""
    return _sanitize_lrc_text(lyric)


def _coerce_qq_lyric(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "[" in text and "]" in text:
        return _sanitize_lrc_text(text)
    try:
        decoded = base64.b64decode(text).decode("utf-8", "replace").strip()
    except (binascii.Error, ValueError):
        return _sanitize_lrc_text(text)
    return _sanitize_lrc_text(decoded)


def _strip_qq_translation_placeholders(text: str) -> str:
    if not text.strip():
        return ""
    kept_lines: list[str] = []
    for line in text.splitlines():
        body = _LRC_TIMESTAMP_TOKEN_PATTERN.sub("", line).strip()
        if body == "//":
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines).strip()


def _build_kugou_signature(params: dict[str, str]) -> str:
    joined = "".join(
        f"{key}={json.dumps(value, ensure_ascii=False) if isinstance(value, dict) else value}"
        for key, value in sorted(params.items())
    )
    raw = f"{_KUGOU_SIGN_SALT}{joined}{_KUGOU_SIGN_SALT}".encode("utf-8")
    return hashlib.md5(raw).hexdigest()


def _decrypt_kugou_krc(payload: bytes) -> str:
    encrypted = payload[4:] if payload.startswith(b"krc1") else payload[4:]
    decrypted = bytearray()
    for index, item in enumerate(encrypted):
        decrypted.append(item ^ _KUGOU_KRC_KEY[index % len(_KUGOU_KRC_KEY)])
    return zlib.decompress(bytes(decrypted)).decode("utf-8", "replace").lstrip("\ufeff")


def _convert_krc_text(text: str) -> tuple[str, str, str, str]:
    line_parts: list[str] = []
    verbatim_parts: list[str] = []
    line_starts: list[int] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _LINE_DURATION_PATTERN.match(line)
        if not match:
            continue
        line_start = int(match.group(1))
        line_starts.append(line_start)
        line_content = match.group(3)
        words = list(_KRC_WORD_PATTERN.finditer(line_content))
        if not words:
            clean_text = line_content.strip()
            if clean_text:
                line_parts.append(f"[{format_lrc_timestamp(line_start)}]{clean_text}")
            continue

        text_parts: list[str] = []
        verbatim_line: list[str] = []
        for word_match in words:
            word_text = word_match.group("content")
            if not word_text:
                continue
            word_start = line_start + int(word_match.group("start"))
            text_parts.append(word_text)
            verbatim_line.append(f"[{format_lrc_timestamp(word_start)}]{word_text}")
        merged_text = "".join(text_parts).strip()
        if merged_text:
            line_parts.append(f"[{format_lrc_timestamp(line_start)}]{merged_text}")
            verbatim_parts.append("".join(verbatim_line))

    line_lyrics = _sanitize_lrc_text("\n".join(line_parts))
    verbatim_lyrics = _sanitize_lrc_text("\n".join([part for part in verbatim_parts if part]))
    plain_lyrics = _strip_lrc_timestamps(line_lyrics) or _strip_lrc_timestamps(verbatim_lyrics)
    translation_lyrics = _extract_kugou_language_translation(text, line_starts)
    return line_lyrics, verbatim_lyrics, plain_lyrics, translation_lyrics


def _extract_kugou_language_translation(text: str, line_starts: list[int]) -> str:
    translation_parts: list[str] = []
    for raw_match in re.finditer(r"^\[language:([^\]]+)\]", text, flags=re.MULTILINE):
        encoded = raw_match.group(1).strip()
        if not encoded:
            continue
        try:
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.b64decode(encoded + padding).decode("utf-8", "replace"))
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        for language_item in payload.get("content", []):
            if not isinstance(language_item, dict):
                continue
            try:
                item_type = int(language_item.get("type") or -1)
            except (TypeError, ValueError):
                continue
            if item_type != 1:
                continue
            lyric_rows = language_item.get("lyricContent")
            if not isinstance(lyric_rows, list):
                continue
            for index, row in enumerate(lyric_rows):
                if index >= len(line_starts):
                    break
                translated_text = _flatten_kugou_language_row(row)
                if not translated_text or _is_kugou_translation_credit(translated_text):
                    continue
                translation_parts.append(f"[{format_lrc_timestamp(line_starts[index])}]{translated_text}")
    return _sanitize_lrc_text("\n".join(translation_parts))


def _flatten_kugou_language_row(row: object) -> str:
    if isinstance(row, list):
        text = "".join(str(part or "") for part in row)
    else:
        text = str(row or "")
    return re.sub(r"\s+", " ", text).strip()


def _is_kugou_translation_credit(text: str) -> bool:
    normalized = _normalize_text(text)
    return any(
        marker in normalized
        for marker in (
            "以下歌词翻译由",
            "歌词翻译由",
            "文曲大模型提供",
        )
    )


def _convert_yrc_text(text: str) -> tuple[str, str, str]:
    line_parts: list[str] = []
    verbatim_parts: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        match = _LINE_DURATION_PATTERN.match(line)
        if not match:
            continue
        line_start = int(match.group(1))
        line_content = match.group(3)
        words = list(_YRC_WORD_PATTERN.finditer(line_content))
        if not words:
            clean_text = re.sub(r"\(\d+,\d+,\d+\)", "", line_content).strip()
            if clean_text:
                line_parts.append(f"[{format_lrc_timestamp(line_start)}]{clean_text}")
            continue

        text_parts: list[str] = []
        verbatim_line: list[str] = []
        for word_match in words:
            word_text = word_match.group("content")
            if not word_text:
                continue
            word_start = int(word_match.group("start"))
            text_parts.append(word_text)
            verbatim_line.append(f"[{format_lrc_timestamp(word_start)}]{word_text}")
        merged_text = "".join(text_parts).strip()
        if merged_text:
            line_parts.append(f"[{format_lrc_timestamp(line_start)}]{merged_text}")
            verbatim_parts.append("".join(verbatim_line))

    line_lyrics = _sanitize_lrc_text("\n".join(line_parts))
    verbatim_lyrics = _sanitize_lrc_text("\n".join([part for part in verbatim_parts if part]))
    plain_lyrics = _strip_lrc_timestamps(line_lyrics) or _strip_lrc_timestamps(verbatim_lyrics)
    return line_lyrics, verbatim_lyrics, plain_lyrics
