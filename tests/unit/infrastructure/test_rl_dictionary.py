"""RL 字典解析器单元测试。"""

from __future__ import annotations

from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
    parse_rl_dictionary,
)


class TestParseRlDictionary:
    def test_basic_entry(self):
        text = "赤い\tあ,かい\n"
        entries = parse_rl_dictionary(text)
        assert entries == [
            {"enabled": True, "word": "赤い", "reading": "あ,かい"}
        ]

    def test_skip_empty_and_malformed_lines(self):
        text = "\n   \n漢字 no-tab\n本当\tほん,とう\n"
        entries = parse_rl_dictionary(text)
        assert len(entries) == 1
        assert entries[0]["word"] == "本当"

    def test_link_marker_stripped(self):
        # ＋ (U+FF0B) 剥离，仅含 ＋ 的读音项在尾部被去除
        text = "本当に\tほん,とう,に,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "ほん,とう,に"

    def test_link_marker_inside_reading_stripped_but_kept(self):
        text = "特別\tとく＋,べつ\n"
        entries = parse_rl_dictionary(text)
        # 剥离 ＋ 后还有 "とく"，保留
        assert entries[0]["reading"] == "とく,べつ"

    def test_trailing_empty_readings_removed(self):
        text = "心\tここ,ろ,,,\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "ここ,ろ"

    def test_entry_dropped_when_all_readings_empty(self):
        # 读音全为空或仅 ＋ → 丢弃整条
        text = "空\t＋,＋,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries == []

    def test_multiple_entries_preserve_order(self):
        text = "一\tいち\n二\tに\n三\tさん\n"
        entries = parse_rl_dictionary(text)
        assert [e["word"] for e in entries] == ["一", "二", "三"]

    def test_enabled_flag_always_true(self):
        text = "赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["enabled"] is True

    def test_line_with_empty_word_skipped(self):
        text = "\tあ,か\n赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert len(entries) == 1
        assert entries[0]["word"] == "赤"


class TestFrontendShimCompatibility:
    """确认前端旧导入路径 ``_parse_rl_dictionary`` 与后端实现等价。"""

    def test_frontend_shim_delegates_to_backend(self):
        from strange_uta_game.frontend.settings.app_settings import (
            _parse_rl_dictionary,
        )

        text = "赤い\tあ,かい\n本当\tほん,とう\n"
        assert _parse_rl_dictionary(text) == parse_rl_dictionary(text)
