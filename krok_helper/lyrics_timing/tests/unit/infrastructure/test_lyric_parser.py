"""歌词解析器测试。"""

import pytest
from pathlib import Path
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    TXTParser,
    LRCParser,
    KRAParser,
    LyricParserFactory,
    ParseError,
    parse_to_sentences,
    ParsedLine,
)


class TestTXTParser:
    """测试 TXT 解析器"""

    def test_parse_simple_text(self):
        parser = TXTParser()
        content = "第一行\n第二行\n第三行"
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "第一行"
        assert result[0].timetags == []

    def test_parse_skip_empty_lines(self):
        parser = TXTParser()
        content = "第一行\n\n第三行"
        result = parser.parse(content)

        assert len(result) == 2

    def test_parse_strip_whitespace(self):
        parser = TXTParser()
        content = "  第一行  \n  第二行  "
        result = parser.parse(content)

        assert result[0].text == "第一行"
        assert result[1].text == "第二行"


class TestLRCParser:
    """测试 LRC 解析器"""

    def test_parse_simple_lrc(self):
        parser = LRCParser()
        content = "[00:10.50]第一行\n[00:15.20]第二行"
        result = parser.parse(content)

        assert len(result) == 2
        assert result[0].text == "第一行"
        assert result[0].timetags == [(0, 10500)]

        assert result[1].text == "第二行"
        assert result[1].timetags == [(0, 15200)]

    def test_parse_skip_metadata(self):
        parser = LRCParser()
        content = "[ti:Title]\n[ar:Artist]\n[00:10.00]歌词"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "歌词"

    def test_parse_milliseconds_precision(self):
        parser = LRCParser()
        content = "[00:10.123]歌词"
        result = parser.parse(content)

        assert result[0].timetags == [(0, 10123)]

    def test_parse_start_end_timestamps(self):
        """测试 [start]歌词[end] 格式 — 增强LRC常见格式"""
        parser = LRCParser()
        content = "[00:06.540]一闪一闪亮晶晶[00:09.300]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]

    def test_parse_start_end_multi_lines(self):
        """测试多行 [start]歌词[end] 格式"""
        parser = LRCParser()
        content = (
            "[00:06.540]一闪一闪亮晶晶[00:09.300]\n"
            "[00:09.300]满天都是小星星[00:12.120]\n"
            "[00:12.120]挂在天上放光明[00:15.060]"
        )
        result = parser.parse(content)

        assert len(result) == 3
        assert result[0].text == "一闪一闪亮晶晶"
        assert result[0].timetags == [(0, 6540)]
        assert result[1].text == "满天都是小星星"
        assert result[1].timetags == [(0, 9300)]
        assert result[2].text == "挂在天上放光明"
        assert result[2].timetags == [(0, 12120)]

    def test_parse_colon_separator(self):
        """测试冒号分隔的时间标签 [mm:ss:cc]"""
        parser = LRCParser()
        content = "[00:06:54]一闪一闪[00:09:30]"
        result = parser.parse(content)

        assert len(result) == 1
        assert result[0].text == "一闪一闪"
        assert result[0].timetags == [(0, 6540)]


class TestLyricParserFactory:
    """测试解析器工厂"""

    def test_get_txt_parser(self):
        parser = LyricParserFactory.get_parser("test.txt")
        assert isinstance(parser, TXTParser)

    def test_get_lrc_parser(self):
        parser = LyricParserFactory.get_parser("test.lrc")
        assert isinstance(parser, LRCParser)

    def test_get_kra_parser(self):
        parser = LyricParserFactory.get_parser("test.kra")
        assert isinstance(parser, KRAParser)

    def test_unsupported_format_raises_error(self):
        with pytest.raises(ParseError):
            LyricParserFactory.get_parser("test.mp3")


class TestParseToSentences:
    """测试转换为 Sentence"""

    def test_convert_with_timetags(self):
        parsed_lines = [
            ParsedLine(text="测试", timetags=[(0, 1000)]),
        ]

        sentences = parse_to_sentences(parsed_lines, "singer_1")

        assert len(sentences) == 1
        assert sentences[0].text == "测试"


class TestApplyRubyEntries:
    """测试 @Ruby 注音应用（含位置范围消歧）"""

    def _make_sentence(self, text: str, timestamps: list) -> "Sentence":
        """创建带时间戳的句子"""
        from strange_uta_game.backend.domain import Sentence

        sentence = Sentence.from_text(text, "singer_1")
        for i, ts in enumerate(timestamps):
            if ts is not None and i < len(sentence.characters):
                sentence.characters[i].add_timestamp(ts)
        return sentence

    def test_multi_char_ruby_is_one_connective_word(self):
        """多字 @Ruby 块=单一连词：块内全部相邻字 linked_to_next=True，
        即使块内后字在 body 有独立 ts（如 アドベンチャー 的 ア/ド/ベ/チ 各有 ts）。

        这是 round-trip 关键：导出器按 linked_to_next 切段，若按「后字有 ts
        就断链」会把一个连词拆成 ア/ド/ベン/チャー 四条 @RubyN。
        同时块内**无独立 body ts 的 follower（ン/ャ/ー）必须 cc=0、ruby=None**，
        读音并入前一个 timed unit（ベ='ven'、チ='ture'）。
        """
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        # ア ド ベ ン チ ャ ー —— ア/ド/ベ/チ 有 body ts，ン/ャ/ー 无
        s = self._make_sentence(
            "アドベンチャー", [10000, 10200, 10490, None, 10770, None, None]
        )
        _apply_ruby_entries(
            s,
            [NicokaraRubyEntry(
                kanji="アドベンチャー",
                reading="a[00:00:20]d[00:00:49]ven[00:00:77]ture",
            )],
        )
        chars = s.characters
        # 块内 0..5 全部 link，块尾 ー(6) 不 link
        assert [c.linked_to_next for c in chars] == [
            True, True, True, True, True, True, False
        ]
        # timed unit 承载读音
        assert "".join(p.text for p in chars[0].ruby.parts) == "a"
        assert "".join(p.text for p in chars[1].ruby.parts) == "d"
        assert "".join(p.text for p in chars[2].ruby.parts) == "ven"
        assert "".join(p.text for p in chars[4].ruby.parts) == "ture"
        # follower：cc=0、无 ruby
        for fi in (3, 5, 6):
            assert chars[fi].check_count == 0, f"follower {fi} 应 cc=0"
            assert chars[fi].ruby is None, f"follower {fi} 应无 ruby"

    def test_multi_kanji_ruby_links_even_with_per_char_ts(self):
        """两汉字各有独立 body ts 时仍是连词（世界=せ/か/い）。

        旧规则会因「界 有独立 ts」断链，导致导出成两条 @Ruby（世 + 界）。
        """
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        s = self._make_sentence("世界", [10000, 10410])
        _apply_ruby_entries(
            s, [NicokaraRubyEntry(kanji="世界", reading="せ[00:00:21]か[00:00:41]い")]
        )
        assert s.characters[0].linked_to_next is True
        assert s.characters[1].linked_to_next is False
        # 两字都是 timed unit，各自承载读音
        assert s.characters[0].ruby is not None
        assert s.characters[1].ruby is not None

    def test_same_kanji_different_readings_across_sentences(self):
        """同一词组在不同句子有不同读音时，应按位置范围正确匹配"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        # 句子1: 言葉は (言→こと, ts=1000)
        s1 = self._make_sentence("言葉は", [1000, 1300, 1500])
        s1.characters[0].check_count = 2
        s1.characters[0].add_timestamp(1000, checkpoint_idx=0)
        s1.characters[0].add_timestamp(1163, checkpoint_idx=1)

        # 句子2: 言う (言→い, ts=5000)
        s2 = self._make_sentence("言う", [5000, 5200])

        # @Ruby 条目: 言有两个不同读音，需要位置范围
        entries = [
            NicokaraRubyEntry(
                kanji="言", reading="こ[00:00:16]と", positions=["", "[00:05:00]"]
            ),
            NicokaraRubyEntry(
                kanji="言", reading="い", positions=["[00:05:00]"]
            ),
        ]

        _apply_ruby_entries(s1, entries)
        _apply_ruby_entries(s2, entries)

        # 句子1 的 言 应该是 こと
        assert s1.characters[0].ruby is not None
        ruby_text_1 = "".join(p.text for p in s1.characters[0].ruby.parts)
        assert ruby_text_1 == "こと"

        # 句子2 的 言 应该是 い
        assert s2.characters[0].ruby is not None
        ruby_text_2 = "".join(p.text for p in s2.characters[0].ruby.parts)
        assert ruby_text_2 == "い"

    def test_no_position_later_entry_overrides_earlier(self):
        """SHINTA 2025 规格：同 kanji 多个 @RubyN 条目均无 position 时，
        后到的覆盖先到的（N 大者覆盖 N 小者），所有出现统一取最后一个 reading。

        这取代了 RhythmicaLyrics 历史的「第 N 条 → 第 N 次出现」顺序分配行为。
        """
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        s = self._make_sentence("嫌い嫌い", [1000, 2000, 3000, 4000])

        entries = [
            NicokaraRubyEntry(kanji="嫌", reading="きら"),
            NicokaraRubyEntry(kanji="嫌", reading="いや"),
        ]

        _apply_ruby_entries(s, entries)

        # 两个「嫌」均被第二个 entry 覆盖为 いや（规格合规）
        ruby1 = "".join(p.text for p in s.characters[0].ruby.parts)
        assert ruby1 == "いや"
        ruby2 = "".join(p.text for p in s.characters[2].ruby.parts)
        assert ruby2 == "いや"

    # ---- SHINTA 2025 规格附加测试 ----------------------------------------

    def test_position_range_inclusive_upper_bound_G(self):
        """差异表 G：适用区间右端点是闭合的 (`char_ms <= pos_end_ms` 通过)。

        char ts 恰好等于 pos_end 时，旧逻辑（>=）会判断失败、跳过这次出现；
        新逻辑应当落到 ruby。
        """
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        # 「嫌」位于 ts=3000，区间 [1000, 3000] 闭合上端必须命中
        s = self._make_sentence("嫌い", [3000, 3300])
        entries = [
            NicokaraRubyEntry(
                kanji="嫌",
                reading="いや",
                positions=["[00:01:00]", "[00:03:00]"],
            ),
        ]
        _apply_ruby_entries(s, entries)

        assert s.characters[0].ruby is not None, "上端闭合区间应命中"
        ruby_text = "".join(p.text for p in s.characters[0].ruby.parts)
        assert ruby_text == "いや"

    def test_position_range_inclusive_lower_bound_G(self):
        """差异表 G：适用区间左端点闭合 (`pos_start_ms <= char_ms` 通过)。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        s = self._make_sentence("嫌い", [1000, 1300])
        entries = [
            NicokaraRubyEntry(
                kanji="嫌",
                reading="いや",
                positions=["[00:01:00]", "[00:03:00]"],
            ),
        ]
        _apply_ruby_entries(s, entries)

        assert s.characters[0].ruby is not None
        ruby_text = "".join(p.text for p in s.characters[0].ruby.parts)
        assert ruby_text == "いや"

    def test_empty_reading_clears_existing_ruby_I(self):
        """差异表 I：`@RubyN=漢字,,...`（reading 为空）应清除区间内的 ruby。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        s = self._make_sentence("嫌い", [1000, 1300])

        # 第一遍：给「嫌」附 ruby
        _apply_ruby_entries(
            s, [NicokaraRubyEntry(kanji="嫌", reading="いや")]
        )
        assert s.characters[0].ruby is not None

        # 第二遍：reading="" 清除
        _apply_ruby_entries(
            s, [NicokaraRubyEntry(kanji="嫌", reading="")]
        )
        assert s.characters[0].ruby is None
        assert s.characters[0].linked_to_next is False

    def test_empty_reading_resets_linked_to_next_I(self):
        """差异表 I：清除多字 ruby 时同步重置 linked_to_next，
        防止历史连字残留导致后续渲染异常。

        构造：「葉」无独立 ts（只有「言」「は」有 ts），第一次应用 ruby
        会让「言」linked_to_next=True；reading="" 清除后必须复位。
        """
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            _apply_ruby_entries,
            NicokaraRubyEntry,
        )

        # 「葉」对应 ts=None，让首字 linked_to_next 第一遍能设为 True
        s = self._make_sentence("言葉は", [1000, None, 1600])
        _apply_ruby_entries(
            s, [NicokaraRubyEntry(kanji="言葉", reading="ことば")]
        )
        # 前置断言：首字应 link 到下一字
        assert s.characters[0].linked_to_next is True

        # 清除 → 必须把 linked_to_next 也复位
        _apply_ruby_entries(
            s, [NicokaraRubyEntry(kanji="言葉", reading="")]
        )
        assert s.characters[0].ruby is None
        assert s.characters[1].ruby is None
        assert s.characters[0].linked_to_next is False


class TestNicokaraBodyModel:
    """Nicokara body 逐字事实的解析（cc / 句尾 / 行尾 / 前导空格），不依赖 @Ruby。"""

    def _sents(self, content: str):
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
            nicokara_result_to_sentences,
        )

        result = NicokaraParser().parse(content)
        return nicokara_result_to_sentences(result, {}, "singer_1")

    def test_pure_kana_follower_cc_zero_line_end_not_sentence_end(self):
        """连读 follower（无独立 ts 的纯假名，如 ちゃ 的 ゃ）：cc=0；
        行末无释放 ts 时，末字是行尾(is_line_end) 但不是句尾(is_sentence_end)。"""
        content = "[00:00:21]く[00:00:40]ちゃ\n"
        s = self._sents(content)[0]
        assert s.text == "くちゃ"
        ch_chi = s.characters[1]  # ち：有起始 ts
        ch_ya = s.characters[2]   # ゃ：follower
        assert ch_chi.check_count == 1 and ch_chi.timestamps == [400]
        assert ch_ya.check_count == 0
        assert ch_ya.timestamps == []
        assert ch_ya.is_line_end is True      # 行尾（结构事实）
        assert ch_ya.is_sentence_end is False  # 非句尾（文件无释放 ts）
        assert ch_ya.sentence_end_ts is None

    def test_english_word_followers_cc_zero(self):
        """英文词中间/结尾字母（how 的 o/w、many 的 a/n/y）：follower cc=0。"""
        content = "[00:00:75]how [00:00:98]many?\n"
        s = self._sents(content)[0]
        ccs = [c.check_count for c in s.characters]
        # h o w ' ' m a n y ?
        assert ccs == [1, 0, 0, 0, 1, 0, 0, 0, 0]
        assert s.characters[-1].is_line_end is True
        assert s.characters[-1].is_sentence_end is False

    def test_leading_space_keeps_timestamp_alignment(self):
        """行首空格：保留为 cc=0 的字符，且其后字符的时间戳不被整行左移一格。"""
        content = " [00:00:50]い[00:00:73]つ\n"
        s = self._sents(content)[0]
        assert s.text == " いつ"
        assert s.characters[0].char == " "
        assert s.characters[0].timestamps == []      # 空格不抢时间戳
        assert s.characters[0].check_count == 0
        assert s.characters[1].char == "い"
        assert s.characters[1].timestamps == [500]  # い 拿到自己的 ts，未左移
        assert s.characters[2].char == "つ"
        assert s.characters[2].timestamps == [730]

    def test_trailing_blank_lines_before_tags_not_body(self):
        """正文与尾部 @ 标签之间、以及文件末尾的空行不应被解析为正文空行。"""
        content = (
            "[00:00:21]あ[00:00:40]\n"
            "\n"
            "\n"
            "@Emoji=【sv1】,x.png\n"
            "@Ruby1=愛,あい\n"
        )
        sents = self._sents(content)
        assert len(sents) == 1
        assert sents[0].text == "あ"

    def test_inter_verse_blank_line_preserved(self):
        """verse 之间的空行（后面还有正文）仍保留为空 Sentence。"""
        content = (
            "[00:00:21]あ[00:00:40]\n"
            "\n"
            "[00:01:21]い[00:01:40]\n"
            "\n"
            "@Ruby1=愛,あい\n"
        )
        sents = self._sents(content)
        assert [s.text for s in sents] == ["あ", "", "い"]

    def test_line_end_release_sets_sentence_end(self):
        """行末有释放 ts 时，末字应为句尾并携带释放时间戳。"""
        content = "[00:00:21]あ[00:00:40]い[00:00:60]\n"
        s = self._sents(content)[0]
        last = s.characters[-1]
        assert last.is_line_end is True
        assert last.is_sentence_end is True
        assert last.sentence_end_ts == 600


class TestNicokaraParserSpecCompliance:
    """SHINTA 2025 规格诊断 warning 测试（差异表 A / H）。"""

    def test_loose_minute_segment_emits_warning_A(self, caplog):
        """差异表 A：分钟段非 2 位（如 `[0:01:00]`）应 emit warning。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
        )
        import logging

        # 单字符分钟段 `[0:` 触发宽松违规
        content = "[0:01:00]【sv1】テスト\n"
        with caplog.at_level(
            logging.WARNING,
            logger="strange_uta_game.backend.infrastructure.parsers.lyric_parser",
        ):
            NicokaraParser().parse(content)

        msgs = [r.message for r in caplog.records if "非规格 ts" in r.message]
        assert msgs, f"应包含非规格 ts 警告, 实际记录: {caplog.records}"

    def test_strict_minute_segment_no_warning_A(self, caplog):
        """差异表 A：合规 `[MM:SS:CC]` 不应触发 ts 警告。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
        )
        import logging

        content = "[00:01:00]【sv1】テスト\n"
        with caplog.at_level(
            logging.WARNING,
            logger="strange_uta_game.backend.infrastructure.parsers.lyric_parser",
        ):
            NicokaraParser().parse(content)

        ts_warnings = [
            r.message for r in caplog.records if "非规格 ts" in r.message
        ]
        assert ts_warnings == []

    def test_ruby_index_gap_emits_warning_H(self, caplog):
        """差异表 H：@RubyN 编号跳号（1,3）应 emit warning。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
        )
        import logging

        content = (
            "[00:01:00]【sv1】嫌い\n"
            "@Ruby1=嫌,いや\n"
            "@Ruby3=い,い\n"  # 跳号：缺 Ruby2
        )
        with caplog.at_level(
            logging.WARNING,
            logger="strange_uta_game.backend.infrastructure.parsers.lyric_parser",
        ):
            NicokaraParser().parse(content)

        msgs = [r.message for r in caplog.records if "@RubyN 编号违规" in r.message]
        assert msgs, f"应包含 @RubyN 编号违规警告, 实际: {caplog.records}"

    def test_ruby_index_duplicate_emits_warning_H(self, caplog):
        """差异表 H：@RubyN 编号重复（1,1）应 emit warning。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
        )
        import logging

        content = (
            "[00:01:00]【sv1】嫌い\n"
            "@Ruby1=嫌,いや\n"
            "@Ruby1=い,い\n"
        )
        with caplog.at_level(
            logging.WARNING,
            logger="strange_uta_game.backend.infrastructure.parsers.lyric_parser",
        ):
            NicokaraParser().parse(content)

        msgs = [r.message for r in caplog.records if "@RubyN 编号违规" in r.message]
        assert msgs

    def test_ruby_index_sequential_no_warning_H(self, caplog):
        """差异表 H：合规 @Ruby1, @Ruby2 不应触发编号警告。"""
        from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
            NicokaraParser,
        )
        import logging

        content = (
            "[00:01:00]【sv1】嫌い\n"
            "@Ruby1=嫌,いや\n"
            "@Ruby2=い,い\n"
        )
        with caplog.at_level(
            logging.WARNING,
            logger="strange_uta_game.backend.infrastructure.parsers.lyric_parser",
        ):
            NicokaraParser().parse(content)

        ruby_warnings = [
            r.message for r in caplog.records if "@RubyN 编号违规" in r.message
        ]
        assert ruby_warnings == []


class TestNicokaraTagsRoundTrip:
    """差异表 K：未知 @ 标签写入 AppSettings.nicokara_tags（覆盖式）。"""

    def test_sync_known_and_custom_tags_to_settings_K(self, tmp_path, monkeypatch):
        """已知 key 映射到 known map，其他 @ 标签 push 到 tags["custom"]。"""
        from strange_uta_game.frontend.editor.timing.lyric_loader import (
            _sync_nicokara_metadata_to_settings,
        )
        from strange_uta_game.frontend.settings.app_settings import AppSettings

        # AppSettings 是 singleton：用 monkeypatch 替换 __new__ 行为绕过
        # 这里直接构造独立实例 + monkeypatch __init__ 调用链
        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{}", encoding="utf-8")

        # 让 lyric_loader 内部 AppSettings() 调用走我们的临时 config
        # AppSettings 单例机制无 reset 接口；改为直接调用 settings.set 并验证
        settings = AppSettings(config_path=str(cfg_path))
        settings.set("nicokara_tags", {})  # 清空旧值

        # monkeypatch lyric_loader 内的 AppSettings 名称指向 lambda 返回本实例
        import strange_uta_game.frontend.editor.timing.lyric_loader as ll

        class _StubSettings:
            @staticmethod
            def __call__():
                return settings

        # 用 module-level patch：替换 AppSettings 解析路径
        import strange_uta_game.frontend.settings.settings_interface as si

        monkeypatch.setattr(si, "AppSettings", lambda: settings)

        metadata = {
            "Title": "テスト曲",
            "Artist": "テスト歌手",
            "Album": "テストアルバム",
            "TaggingBy": "tester",
            "SilencemSec": "500",
            "Offset": "1000",  # 应被跳过
            "FooBar": "baz",  # 未知 → custom
            "Hello": "World",  # 未知 → custom
        }
        _sync_nicokara_metadata_to_settings(metadata)

        tags = settings.get("nicokara_tags") or {}
        assert tags.get("title") == "テスト曲"
        assert tags.get("artist") == "テスト歌手"
        assert tags.get("album") == "テストアルバム"
        assert tags.get("tagging_by") == "tester"
        assert tags.get("silence_ms") == 500

        # @Offset 跳过：不在 tags 也不在 custom
        custom = tags.get("custom") or []
        assert all("Offset" not in c for c in custom), custom

        # 未知键完整保留 @Key=Value
        assert "@FooBar=baz" in custom
        assert "@Hello=World" in custom

    def test_sync_overwrites_previous_tags_K(self, tmp_path, monkeypatch):
        """覆盖式：第二次同步完全替换前次写入，无合并。"""
        from strange_uta_game.frontend.editor.timing.lyric_loader import (
            _sync_nicokara_metadata_to_settings,
        )
        from strange_uta_game.frontend.settings.app_settings import AppSettings
        import strange_uta_game.frontend.settings.settings_interface as si

        cfg_path = tmp_path / "config.json"
        cfg_path.write_text("{}", encoding="utf-8")
        settings = AppSettings(config_path=str(cfg_path))
        monkeypatch.setattr(si, "AppSettings", lambda: settings)

        # 第一次：写入 OldTag
        _sync_nicokara_metadata_to_settings(
            {"Title": "Old", "OldTag": "v1"}
        )
        tags1 = settings.get("nicokara_tags") or {}
        assert tags1.get("title") == "Old"
        assert "@OldTag=v1" in (tags1.get("custom") or [])

        # 第二次：完全不同的元数据
        _sync_nicokara_metadata_to_settings(
            {"Title": "New", "NewTag": "v2"}
        )
        tags2 = settings.get("nicokara_tags") or {}
        assert tags2.get("title") == "New"
        custom2 = tags2.get("custom") or []
        # OldTag 必须消失（覆盖语义），NewTag 必须存在
        assert all("OldTag" not in c for c in custom2), custom2
        assert "@NewTag=v2" in custom2

    def test_sync_empty_metadata_noop_K(self, tmp_path, monkeypatch):
        """空 metadata 不应崩溃也不应写入。"""
        from strange_uta_game.frontend.editor.timing.lyric_loader import (
            _sync_nicokara_metadata_to_settings,
        )

        # 不应抛出
        _sync_nicokara_metadata_to_settings({})
        _sync_nicokara_metadata_to_settings(None)  # type: ignore[arg-type]
