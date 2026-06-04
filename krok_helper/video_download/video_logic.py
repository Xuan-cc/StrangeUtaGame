from __future__ import annotations

from typing import Literal

from .download_task import (
    DownloadTask,
    FormatOption,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DOWNLOADING,
    VideoInfo,
)


ExistingTaskDecision = Literal["update", "keep"]


def video_identity_keys(*urls: str) -> set[str]:
    keys: set[str] = set()
    for url in urls:
        normalized = (url or "").strip().rstrip("/")
        if normalized:
            keys.add(normalized)
    return keys


def find_existing_task_for_info(tasks: list[DownloadTask], info: VideoInfo) -> DownloadTask | None:
    incoming_keys = video_identity_keys(info.url, info.webpage_url)
    if not incoming_keys:
        return None
    for task in tasks:
        task_webpage_url = task.info.webpage_url if task.info else ""
        task_keys = video_identity_keys(task.url, task_webpage_url)
        if incoming_keys & task_keys:
            return task
    return None


def classify_existing_task(task: DownloadTask) -> ExistingTaskDecision:
    if task.status in (TASK_STATUS_COMPLETED, TASK_STATUS_DOWNLOADING):
        return "keep"
    return "update"


def select_default_format(formats: list[FormatOption]) -> FormatOption | None:
    for option in formats:
        if option.is_recommended:
            return option
    return formats[0] if formats else None


def find_matching_format(formats: list[FormatOption], option_id: str) -> FormatOption | None:
    if not option_id:
        return None
    for option in formats:
        if option.option_id == option_id:
            return option
    return None
