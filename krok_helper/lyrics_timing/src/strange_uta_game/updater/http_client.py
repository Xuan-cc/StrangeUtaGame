"""带代理与多源接力的 HTTP 客户端。

封装 ``requests``：

* 透传 ``proxies`` 字典；
* 提供 ``get_json`` / ``get_text`` / ``download`` 三个常用入口；
* :class:`SourceTrialRunner` 把"多个候选 URL 按顺序尝试"的逻辑收敛成一个
  迭代器，调用方只关心成功与最终错误。

注意：``ghproxy`` / ``gh-proxy`` 等反代有时对部分端点不可用，需要靠
``SourceTrialRunner`` 在失败时降级到下一个 URL。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import requests

# 默认超时（连接 / 读取）。
DEFAULT_TIMEOUT: Tuple[float, float] = (10.0, 30.0)
# 下载流的 chunk 大小。
CHUNK_SIZE = 64 * 1024
# 下载失败重试次数。
DOWNLOAD_RETRY_COUNT = 3
# 下载重试间隔（秒）。
DOWNLOAD_RETRY_INTERVAL = 2.0

UA = (
    "StrangeUtaGame-Updater/{ver} (+https://github.com/Xuan-cc/StrangeUtaGame)"
)


def _headers() -> Dict[str, str]:
    # 这里读 __version__ 是为了在请求头里带版本，方便服务端日志。
    from ..__version__ import __version__

    return {
        "User-Agent": UA.format(ver=__version__),
        "Accept": "application/octet-stream, application/json;q=0.9, */*;q=0.5",
    }


@dataclass
class HttpResult:
    """单次 HTTP 操作结果。"""
    ok: bool
    status: int = 0
    error: str = ""
    # 仅 ``get_json`` / ``get_text`` 使用
    body: Optional[object] = None
    # ``download`` 使用：实际保存到的本地路径
    file_path: str = ""


# ───────────────────────── 基础调用 ─────────────────────────


def get_json(
    url: str,
    *,
    proxies: Optional[Dict[str, str]] = None,
    timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
) -> HttpResult:
    """GET 一段 JSON 文本并解析。"""
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return HttpResult(ok=False, error=f"网络错误: {e}")
    if resp.status_code != 200:
        return HttpResult(ok=False, status=resp.status_code, error=f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        return HttpResult(ok=False, status=resp.status_code, error=f"JSON 解析失败: {e}")
    return HttpResult(ok=True, status=resp.status_code, body=data)


def get_text(
    url: str,
    *,
    proxies: Optional[Dict[str, str]] = None,
    timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
) -> HttpResult:
    """GET 文本（用于 ``.sha256`` 等小文件校验）。"""
    try:
        resp = requests.get(
            url,
            headers=_headers(),
            proxies=proxies,
            timeout=timeout,
            allow_redirects=True,
        )
    except requests.RequestException as e:
        return HttpResult(ok=False, error=f"网络错误: {e}")
    if resp.status_code != 200:
        return HttpResult(ok=False, status=resp.status_code, error=f"HTTP {resp.status_code}")
    return HttpResult(ok=True, status=resp.status_code, body=resp.text)


def download(
    url: str,
    dest_path: str,
    *,
    proxies: Optional[Dict[str, str]] = None,
    timeout: Tuple[float, float] = DEFAULT_TIMEOUT,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> HttpResult:
    """流式下载到 ``dest_path``，支持断点续传和重试。

    Args:
        progress_cb: ``(bytes_done, bytes_total)``；total 未知时为 ``0``。
        cancel_check: 每次写入前调用；返回 ``True`` 立即放弃下载。

    成功时返回 ``HttpResult.file_path`` 指向已写入的文件。
    """
    import os

    last_error = ""
    for attempt in range(1, DOWNLOAD_RETRY_COUNT + 1):
        try:
            # 检查本地是否已有部分下载的文件
            existing_size = 0
            if os.path.exists(dest_path):
                existing_size = os.path.getsize(dest_path)

            # 构建请求头，支持断点续传
            headers = _headers()
            if existing_size > 0:
                headers["Range"] = f"bytes={existing_size}-"

            with requests.get(
                url,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                allow_redirects=True,
                stream=True,
            ) as resp:
                # 处理服务器不支持 Range 请求的情况
                if existing_size > 0 and resp.status_code == 200:
                    # 服务器不支持 Range 请求，重新下载
                    existing_size = 0
                elif resp.status_code == 206:
                    # 服务器支持 Range 请求，继续下载
                    pass
                elif existing_size > 0 and resp.status_code == 416:
                    # HTTP 416 Range Not Satisfiable：请求的起始偏移超出文件大小，
                    # 说明本地文件已等于或超过服务端文件大小，视为下载完成。
                    return HttpResult(ok=True, status=416, file_path=dest_path)
                elif resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    if attempt < DOWNLOAD_RETRY_COUNT:
                        time.sleep(DOWNLOAD_RETRY_INTERVAL)
                        continue
                    return HttpResult(
                        ok=False,
                        status=resp.status_code,
                        error=last_error,
                    )

                total = int(resp.headers.get("Content-Length") or 0)
                # 如果是续传，total 是剩余大小，需要加上已下载的
                if existing_size > 0 and resp.status_code == 206:
                    total += existing_size
                done = existing_size

                # 确保目标目录存在
                os.makedirs(os.path.dirname(os.path.abspath(dest_path)), exist_ok=True)
                # 以追加模式打开文件（如果是续传）或写入模式（如果是新下载）
                mode = "ab" if existing_size > 0 and resp.status_code == 206 else "wb"
                with open(dest_path, mode) as f:
                    for chunk in resp.iter_content(CHUNK_SIZE):
                        if cancel_check and cancel_check():
                            return HttpResult(ok=False, error="用户取消")
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb:
                            try:
                                progress_cb(done, total)
                            except Exception:
                                pass
            return HttpResult(ok=True, status=200, file_path=dest_path)
        except requests.RequestException as e:
            last_error = f"网络错误: {e}"
        except OSError as e:
            last_error = f"写文件失败: {e}"

        if attempt < DOWNLOAD_RETRY_COUNT:
            time.sleep(DOWNLOAD_RETRY_INTERVAL)

    return HttpResult(ok=False, error=last_error)


# ───────────────────────── 多源接力 ─────────────────────────


@dataclass
class SourceAttempt:
    """单一源的尝试结果，供 UI / 日志展示。"""
    source_id: str
    url: str
    ok: bool
    error: str = ""


class SourceTrialRunner:
    """按候选 URL 顺序尝试，直到第一个成功；汇总所有失败记录。"""

    def __init__(self, candidates: Iterable[Tuple[str, str]]):
        # 每项 ``(source_id, url)``
        self._candidates: List[Tuple[str, str]] = list(candidates)
        self.attempts: List[SourceAttempt] = []

    def run(self, op: Callable[[str], HttpResult]) -> Optional[HttpResult]:
        """对每个候选 URL 调用 ``op(url)``，返回第一个成功的 :class:`HttpResult`。

        所有候选均失败返回 ``None``，可读 :attr:`attempts` 获取每个源失败原因。
        """
        for source_id, url in self._candidates:
            result = op(url)
            self.attempts.append(
                SourceAttempt(source_id=source_id, url=url, ok=result.ok, error=result.error)
            )
            if result.ok:
                return result
        return None

    def summary(self) -> str:
        """生成日志友好的总结字符串。"""
        if not self.attempts:
            return "no source attempted"
        parts: List[str] = []
        for a in self.attempts:
            parts.append(
                f"[{a.source_id}] {'OK' if a.ok else 'FAIL'}"
                + (f" {a.error}" if a.error else "")
            )
        return " | ".join(parts)
