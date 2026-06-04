from __future__ import annotations

from krok_helper.video_download.video_logic import find_matching_format, select_default_format


def test_select_default_picks_recommended(make_format_option) -> None:
    regular = make_format_option("regular")
    recommended = make_format_option("recommended", is_recommended=True)

    assert select_default_format([regular, recommended]) is recommended


def test_select_default_falls_back_to_first(make_format_option) -> None:
    first = make_format_option("first")
    second = make_format_option("second")

    assert select_default_format([first, second]) is first


def test_select_default_empty_returns_none() -> None:
    assert select_default_format([]) is None


def test_find_matching_returns_option_when_id_present(make_format_option) -> None:
    option = make_format_option("fmt-1")

    assert find_matching_format([option], "fmt-1") is option


def test_find_matching_returns_none_when_id_missing(make_format_option) -> None:
    option = make_format_option("fmt-1")

    assert find_matching_format([option], "missing") is None
