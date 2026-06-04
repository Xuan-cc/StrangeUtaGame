from __future__ import annotations

from krok_helper.video_download.video_logic import find_existing_task_for_info, video_identity_keys


def test_identity_keys_rstrip_trailing_slash() -> None:
    url1 = "https://www.bilibili.com/video/BV1abc/"
    url2 = "https://www.bilibili.com/video/BV1abc"

    assert video_identity_keys(url1) == video_identity_keys(url2)


def test_identity_keys_handle_empty_and_whitespace() -> None:
    assert video_identity_keys("", "   ", "\t\n") == set()


def test_identity_keys_distinct_videos() -> None:
    assert not (
        video_identity_keys("https://www.bilibili.com/video/BV1abc")
        & video_identity_keys("https://www.bilibili.com/video/BV1xyz")
    )


def test_find_existing_matches_by_webpage_url(make_download_task, make_video_info) -> None:
    canonical = "https://www.bilibili.com/video/BV1abc"
    task = make_download_task(url=f"{canonical}?spm_id_from=333.999", webpage_url=canonical)
    info = make_video_info(url=canonical, webpage_url=canonical)

    assert find_existing_task_for_info([task], info) is task


def test_find_existing_matches_when_only_url_overlaps(make_download_task, make_video_info) -> None:
    url = "https://www.bilibili.com/video/BV1abc"
    task = make_download_task(url=url, webpage_url="")
    info = make_video_info(url=url, webpage_url="")

    assert find_existing_task_for_info([task], info) is task


def test_find_existing_returns_none_when_no_overlap(make_download_task, make_video_info) -> None:
    task = make_download_task(url="https://www.bilibili.com/video/BV1abc")
    info = make_video_info(url="https://www.bilibili.com/video/BV1xyz")

    assert find_existing_task_for_info([task], info) is None
