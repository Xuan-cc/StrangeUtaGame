"""全文本编辑 ↔ 打轴 往返序列化测试。

覆盖：
- `_parse_annotated_line` 正确解析新格式 `{字块||mora|mora,mora|mora}` 为 List[RubyPart.text]
- `_rebuild_characters` 对未改动句子保留原 Ruby（含多段 RubyPart）和 check_count
- `_rebuild_characters` 对用户显式改动 ruby 文本的字符才覆盖
- 新插入字符默认空 ruby（切换不触发自动注音）
"""

from strange_uta_game.backend.domain import (
    Sentence,
    Character,
    Ruby,
    RubyPart,
)
from strange_uta_game.frontend.editor.fulltext_interface import (
    _parse_annotated_line,
    _rebuild_characters,
)


def _make_char(ch: str, parts=None, linked: bool = False) -> Character:
    c = Character(
        char=ch,
        check_count=len(parts) if parts else 1,
        linked_to_next=linked,
        singer_id="default",
    )
    if parts:
        c.set_ruby(Ruby(parts=[RubyPart(text=p) for p in parts]))
    return c


def _make_sent(characters) -> Sentence:
    return Sentence(characters=characters, singer_id="default")


# ──────────────────────────────────────────────
# _parse_annotated_line
# ──────────────────────────────────────────────


def test_parse_multi_kanji_multi_mora():
    raw, chars, rm = _parse_annotated_line("{大冒険||だ|い,ぼ|う,け|ん}")
    assert raw == "大冒険"
    assert chars == ["大", "冒", "険"]
    assert rm == {0: ["だ", "い"], 1: ["ぼ", "う"], 2: ["け", "ん"]}


def test_parse_single_kanji_multi_mora():
    raw, chars, rm = _parse_annotated_line("{漢||か|ん|じ}")
    assert raw == "漢"
    assert rm == {0: ["か", "ん", "じ"]}


def test_parse_plain_text():
    raw, chars, rm = _parse_annotated_line("hello world")
    assert raw == "hello world"
    assert rm == {}


def test_parse_mixed():
    raw, chars, rm = _parse_annotated_line("今日は{晴||は|れ}です")
    assert raw == "今日は晴です"
    # "晴" 在 index 3（今=0 日=1 は=2 晴=3）
    assert rm == {3: ["は", "れ"]}


def test_parse_empty_reading_skipped():
    """``{大冒||,ぼう}`` — 首字 ruby 为空跳过，仅第二字有 ruby。"""
    raw, chars, rm = _parse_annotated_line("{大冒||,ぼう}")
    assert raw == "大冒"
    assert rm == {1: ["ぼう"]}


# ──────────────────────────────────────────────
# _rebuild_characters: 未改动场景保留 RubyPart 切分
# ──────────────────────────────────────────────


def test_rebuild_no_change_preserves_ruby_parts():
    """未改动文本时保留原多段 RubyPart 切分和 check_count。"""
    old_sent = _make_sent([
        _make_char("大", ["だ", "い"], linked=True),
        _make_char("冒", ["ぼ", "う"], linked=True),
        _make_char("険", ["け", "ん"]),
    ])
    new_chars = ["大", "冒", "険"]
    ruby_map = {0: ["だ", "い"], 1: ["ぼ", "う"], 2: ["け", "ん"]}

    result = _rebuild_characters(old_sent, new_chars, ruby_map)

    assert len(result) == 3
    assert [p.text for p in result[0].ruby.parts] == ["だ", "い"]
    assert [p.text for p in result[1].ruby.parts] == ["ぼ", "う"]
    assert [p.text for p in result[2].ruby.parts] == ["け", "ん"]
    assert result[0].check_count == 2
    assert result[1].check_count == 2
    assert result[2].check_count == 2


def test_rebuild_explicit_ruby_change_overrides():
    """用户显式把 ruby 文本改了（拼接后不同），应覆盖原切分。"""
    old_sent = _make_sent([
        _make_char("大", ["だ", "い"], linked=True),
        _make_char("冒", ["ぼ", "う"]),
    ])
    new_chars = ["大", "冒"]
    # 用户把 "大" 的 ruby 改成 "おお"
    ruby_map = {0: ["おお"], 1: ["ぼ", "う"]}

    result = _rebuild_characters(old_sent, new_chars, ruby_map)

    assert [p.text for p in result[0].ruby.parts] == ["おお"]
    assert result[0].check_count == 1
    # "冒" ruby 未变，仍保留原切分
    assert [p.text for p in result[1].ruby.parts] == ["ぼ", "う"]
    assert result[1].check_count == 2


# ──────────────────────────────────────────────
# _rebuild_characters: 新插入字符不自动注音
# ──────────────────────────────────────────────


def test_rebuild_inserted_char_has_empty_ruby():
    """新插入字符默认 ruby=None（切换不触发自动注音）。"""
    old_sent = _make_sent([_make_char("あ"), _make_char("い")])
    new_chars = ["あ", "新", "い"]
    ruby_map: dict = {}

    result = _rebuild_characters(old_sent, new_chars, ruby_map)

    assert len(result) == 3
    assert result[0].char == "あ" and result[0].ruby is None
    assert result[1].char == "新" and result[1].ruby is None
    assert result[1].check_count == 1
    assert result[2].char == "い" and result[2].ruby is None


def test_rebuild_inserted_char_with_explicit_ruby():
    """新插入字符用户显式标注 ruby 时才应用。"""
    old_sent = _make_sent([_make_char("あ")])
    new_chars = ["あ", "漢"]
    ruby_map = {1: ["か", "ん"]}

    result = _rebuild_characters(old_sent, new_chars, ruby_map)

    assert [p.text for p in result[1].ruby.parts] == ["か", "ん"]
    assert result[1].check_count == 2


def test_rebuild_preserves_timestamps_on_match():
    """匹配字符应保留 timestamps 不丢失。"""
    old_ch = _make_char("漢", ["か", "ん"])
    old_ch.timestamps = [1000, 1500]
    old_sent = _make_sent([old_ch])
    new_chars = ["漢"]
    ruby_map = {0: ["か", "ん"]}

    result = _rebuild_characters(old_sent, new_chars, ruby_map)

    assert result[0].timestamps == [1000, 1500]
    assert [p.text for p in result[0].ruby.parts] == ["か", "ん"]
