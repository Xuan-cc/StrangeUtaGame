"""RL 字典解析器单元测试（RL 真语义版）。

新解析与 RL 源码 ``@RhythmicaLyrics.hsp:12636+`` 应用路径对齐：
* piece 末尾 ``＋``（U+FF0B）→ 该字符与下一字符连词；
* piece 末尾 ``/<N>`` → 强制 cp 数（注音格式不携带）；
* 整段数字 piece → 无 ruby（注音格式不携带 cp 数）；
* ruby 多 mora → 按 mora（小假名 / ``ー`` 附属前拍）拆分为 ``|`` 段；
* ruby == 字符（kata→hira 归一化）→ 字面输出（不包 ``{...||...}``）。
"""

from __future__ import annotations

from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (
    parse_rl_dictionary,
)


class TestParseRlDictionary:
    def test_basic_entry_independent_blocks(self):
        # 无 ＋ 连词标记 → 每个字符独立成块，多 mora 按 | 拆
        text = "赤い\tあ,かい\n"
        entries = parse_rl_dictionary(text)
        assert entries == [
            {"enabled": True, "word": "赤い", "reading": "{赤||あ}{い||か|い}"}
        ]

    def test_kanji_word_mora_split(self):
        # 「ほん」「とう」均 2 mora → ほ|ん / と|う
        text = "本当\tほん,とう\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{本||ほ|ん}{当||と|う}"

    def test_kana_tail_outputs_literal(self):
        # 末位假名字符 ruby == 字面 → 字面输出
        text = "本当に\tほん,とう,に\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{本||ほ|ん}{当||と|う}に"

    def test_skip_empty_and_malformed_lines(self):
        text = "\n   \n漢字 no-tab\n本当\tほん,とう\n"
        entries = parse_rl_dictionary(text)
        assert len(entries) == 1
        assert entries[0]["word"] == "本当"

    def test_trailing_link_only_piece_dropped(self):
        # 尾部仅含 ＋ 的占位 piece 被剥离，剩 3 piece 对齐 3 字
        text = "本当に\tほん,とう,に,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{本||ほ|ん}{当||と|う}に"

    def test_link_marker_makes_linked_block(self):
        # piece 末尾 ＋ → 该字符与下一字符连词 → 单个 {...||...} 块
        text = "特別\tとく＋,べつ\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{特別||と|く,べ|つ}"

    def test_excess_pieces_merged_to_last_char(self):
        # 1 字 + 多 piece → 多 piece ruby 合并到末字符并按 mora 拆
        text = "心\tここ,ろ,,,\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{心||こ|こ|ろ}"

    def test_entry_dropped_when_all_readings_empty(self):
        # 全部 ＋ → ruby 全空 → 丢弃整条
        text = "空\t＋,＋,＋\n"
        entries = parse_rl_dictionary(text)
        assert entries == []

    def test_cp_override_slash_suffix_stripped(self):
        # ruby/<N> 后缀 → cp 信息丢弃（注音格式不承载），ruby 保留
        text = "山\tやま/3\n"
        entries = parse_rl_dictionary(text)
        assert entries[0]["reading"] == "{山||や|ま}"

    def test_digit_only_piece_is_empty_ruby(self):
        # 整段数字 piece → 无 ruby，该字符字面输出
        text = "山田\t3,やま\n"
        entries = parse_rl_dictionary(text)
        # 山 数字 piece → 字面；田 ruby やま → {田||や|ま}
        assert entries[0]["reading"] == "山{田||や|ま}"

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

    def test_english_word_dropped(self):
        """word 含 ASCII 字母 → 整条丢弃。"""
        text = "hello\tハロー\n赤\tあか\n"
        entries = parse_rl_dictionary(text)
        assert [e["word"] for e in entries] == ["赤"]


class TestFrontendShimCompatibility:
    """确认前端旧导入路径 ``_parse_rl_dictionary`` 与后端实现等价。"""

    def test_frontend_shim_delegates_to_backend(self):
        from strange_uta_game.frontend.settings.app_settings import (
            _parse_rl_dictionary,
        )

        text = "赤い\tあ,かい\n本当\tほん,とう\n"
        assert _parse_rl_dictionary(text) == parse_rl_dictionary(text)
