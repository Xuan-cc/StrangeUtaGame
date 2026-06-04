from __future__ import annotations

from krok_helper.video_download.download_task import (
    SOURCE_BILIBILI,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DOWNLOADING,
    TASK_STATUS_FAILED,
    TASK_STATUS_WAITING,
)
from krok_helper.video_download.video_download_page import ParsedBatch, ParsedVideoGroup
from krok_helper.video_download.video_logic import classify_existing_task


def test_existing_completed_kept_as_is(make_download_task) -> None:
    task = make_download_task(status=TASK_STATUS_COMPLETED)

    assert classify_existing_task(task) == "keep"


def test_existing_downloading_kept_as_is(make_download_task) -> None:
    task = make_download_task(status=TASK_STATUS_DOWNLOADING)

    assert classify_existing_task(task) == "keep"


def test_existing_waiting_updates(make_download_task) -> None:
    for status in (TASK_STATUS_WAITING, TASK_STATUS_FAILED, TASK_STATUS_CANCELLED):
        task = make_download_task(status=status)

        assert classify_existing_task(task) == "update"


def test_ParsedBatch_dataclass_default_groups_none() -> None:
    batch = ParsedBatch(infos=[], errors=[])

    assert batch.groups is None


def test_ParsedVideoGroup_holds_source_url_and_infos(make_video_info) -> None:
    info = make_video_info(source=SOURCE_BILIBILI)
    group = ParsedVideoGroup(source_url="https://www.bilibili.com/video/BV1abc", infos=[info])

    assert group.source_url == "https://www.bilibili.com/video/BV1abc"
    assert group.infos == [info]
