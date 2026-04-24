"""带注音标注文本 parse/serialize 单元测试。

覆盖 :mod:`strange_uta_game.backend.infrastructure.parsers.annotated_text` 两个 Public API：

- ``parse_annotated_line`` — 单行文本 → (raw_text, raw_chars, ruby_map)
- ``sentence_to_annotated_line`` — ``Sequence[Character]`` → 带注音单行文本

同时验证 parse ↔ serialize 的往返（round-trip）在典型输入下保持等价。
"""

from __future__ import annotations

from strange_uta_game.backend.domain import Character, Ruby, RubyPart
from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
    parse_annotated_line,
    sentence_to_annotated_line,
)


# ──────────────────────────────────────────────
# parse_annotated_line
# ──────────────────────────────────────────────


class TestParseAnnotatedLine:
    def test_plain_text_no_annotation(self):
        raw, chars, rmap = parse_annotated_line("hello 世界")
        assert raw == "hello 世界"
        assert chars == list("hello 世界")
        assert rmap == {}

    def test_main_format_multi_char(self):
        raw, chars, rmap = parse_annotated_line("{大冒険||だ|い,ぼ|う,け|ん}")
        assert raw == "大冒険"
        assert chars == ["大", "冒", "険"]
        assert rmap == {
            0: ["だ", "い"],
            1: ["ぼ", "う"],
            2: ["け", "ん"],
        }

    def test_short_format_single_char_multi_mora(self):
        # {漢|か|ん|じ} — 单字三段 mora
        raw, chars, rmap = parse_annotated_line("{漢|か|ん|じ}")
        assert raw == "漢"
        assert rmap == {0: ["か", "ん", "じ"]}

    def test_short_format_single_char_single_reading(self):
        raw, chars, rmap = parse_annotated_line("{赤|あか}")
        assert raw == "赤"
        assert rmap == {0: ["あか"]}

    def test_text_only_block_no_ruby(self):
        # {text} 无 `|`/`||` → 纯文本，无 ruby
        raw, chars, rmap = parse_annotated_line("{foo}bar")
        assert raw == "foobar"
        assert rmap == {}

    def test_unpaired_brace_treated_as_literal(self):
        raw, chars, rmap = parse_annotated_line("a{bc")
        assert raw == "a{bc"
        assert rmap == {}

    def test_mixed_with_surrounding_text(self):
        raw, chars, rmap = parse_annotated_line("僕は{赤|あか}い")
        assert raw == "僕は赤い"
        # "赤" 是第 3 个字符（index 2）
        assert rmap == {2: ["あか"]}

    def test_empty_reading_group_skipped(self):
        # "大|" 这种 reading 全空 → 不进 ruby_map
        raw, chars, rmap = parse_annotated_line("{大||}")
        assert raw == "大"
        assert rmap == {}

    def test_main_format_some_chars_unread(self):
        # 中间字符 reading 为空串
        raw, chars, rmap = parse_annotated_line("{ABC||a,,c}")
        assert raw == "ABC"
        assert rmap == {0: ["a"], 2: ["c"]}


# ──────────────────────────────────────────────
# sentence_to_annotated_line
# ──────────────────────────────────────────────


def _ch(char: str, ruby_parts=None, linked=False) -> Character:
    """Character 构造辅助：ruby_parts 为字符串列表则自动包成 Ruby。"""
    ruby = None
    if ruby_parts:
        ruby = Ruby(parts=[RubyPart(text=t) for t in ruby_parts])
    return Character(
        char=char,
        ruby=ruby,
        check_count=len(ruby_parts) if ruby_parts else 1,
        linked_to_next=linked,
    )


class TestSentenceToAnnotatedLine:
    def test_empty_sequence(self):
        assert sentence_to_annotated_line([]) == ""

    def test_plain_chars_no_ruby(self):
        chars = [_ch("あ"), _ch("い"), _ch("う")]
        assert sentence_to_annotated_line(chars) == "あいう"

    def test_single_char_with_ruby(self):
        chars = [_ch("赤", ruby_parts=["あ", "か"])]
        assert sentence_to_annotated_line(chars) == "{赤||あ|か}"

    def test_linked_group_merged_into_one_block(self):
        # 大(だ,い) — 冒(ぼ,う) — 険(け,ん)，前两字 linked_to_next=True
        chars = [
            _ch("大", ruby_parts=["だ", "い"], linked=True),
            _ch("冒", ruby_parts=["ぼ", "う"], linked=True),
            _ch("険", ruby_parts=["け", "ん"], linked=False),
        ]
        assert sentence_to_annotated_line(chars) == "{大冒険||だ|い,ぼ|う,け|ん}"

    def test_mixed_plain_and_ruby(self):
        chars = [
            _ch("僕"),
            _ch("は"),
            _ch("赤", ruby_parts=["あか"]),
            _ch("い"),
        ]
        assert sentence_to_annotated_line(chars) == "僕は{赤||あか}い"

    def test_linked_group_in_middle(self):
        chars = [
            _ch("A"),
            _ch("大", ruby_parts=["だ", "い"], linked=True),
            _ch("冒", ruby_parts=["ぼ", "う"], linked=False),
            _ch("B"),
        ]
        assert sentence_to_annotated_line(chars) == "A{大冒||だ|い,ぼ|う}B"


# ──────────────────────────────────────────────
# Round-trip parse ↔ serialize
# ──────────────────────────────────────────────


class TestRoundTrip:
    def test_linked_group_roundtrip(self):
        chars = [
            _ch("大", ruby_parts=["だ", "い"], linked=True),
            _ch("冒", ruby_parts=["ぼ", "う"], linked=True),
            _ch("険", ruby_parts=["け", "ん"], linked=False),
        ]
        serialized = sentence_to_annotated_line(chars)
        raw, raw_chars, rmap = parse_annotated_line(serialized)
        assert raw == "大冒険"
        assert raw_chars == ["大", "冒", "険"]
        assert rmap == {0: ["だ", "い"], 1: ["ぼ", "う"], 2: ["け", "ん"]}

    def test_mixed_roundtrip(self):
        chars = [
            _ch("僕"),
            _ch("は"),
            _ch("赤", ruby_parts=["あか"]),
            _ch("い"),
        ]
        serialized = sentence_to_annotated_line(chars)
        raw, _, rmap = parse_annotated_line(serialized)
        assert raw == "僕は赤い"
        assert rmap == {2: ["あか"]}


# ──────────────────────────────────────────────
# Frontend shim 兼容
# ──────────────────────────────────────────────


class TestFrontendShim:
    def test_frontend_parse_shim_delegates(self):
        from strange_uta_game.frontend.editor.fulltext_interface import (
            _parse_annotated_line,
        )

        line = "{大冒険||だ|い,ぼ|う,け|ん}abc"
        assert _parse_annotated_line(line) == parse_annotated_line(line)
