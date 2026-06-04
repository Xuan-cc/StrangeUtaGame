from __future__ import annotations

from krok_helper.video_download.ytdlp_service import YtDlpService


def test_sanitizes_windows_invalid_chars() -> None:
    result = YtDlpService()._sanitize_filename('a<b>c:d"e/f\\g|h?i*j')

    assert result == "a_b_c_d_e_f_g_h_i_j"
    assert not any(char in result for char in '<>:"/\\|?*')


def test_collapses_whitespace() -> None:
    assert YtDlpService()._sanitize_filename("  a   b\t\nc  ") == "a b c"


def test_strips_dots_and_spaces_at_ends() -> None:
    assert YtDlpService()._sanitize_filename("  .name.  ") == "name"


def test_truncates_to_180_chars() -> None:
    assert len(YtDlpService()._sanitize_filename("a" * 300)) <= 180


def test_preserves_cjk() -> None:
    assert YtDlpService()._sanitize_filename("中文かなカナ😀") == "中文かなカナ😀"


def test_empty_input_returns_empty() -> None:
    assert YtDlpService()._sanitize_filename("") == ""
