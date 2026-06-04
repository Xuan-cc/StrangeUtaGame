"""带内联时间戳的全文本格式 编解码往返测试。

覆盖：
- 有 ruby 多 mora 块 + 逐 checkpoint 起始时间戳
- 块内/块外句尾时间戳 [>...]
- 占位 [--:--.---]（应有 checkpoint 但无 ts）
- 纯假名单点、check_count==0 裸字符
- 演唱者切换 【名】 标签
- 编码→解码→再编码 幂等（无损往返）
"""

from __future__ import annotations

from strange_uta_game.backend.domain import Character, Ruby, RubyPart
from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
    parse_timed_line,
    sentence_to_timed_line,
    timed_line_columns,
)


def _ruby_char(ch, moras, timestamps, *, linked=False, end_ts=None, is_end=False, singer=""):
    c = Character(
        char=ch,
        check_count=len(moras),
        timestamps=list(timestamps),
        linked_to_next=linked,
        is_sentence_end=is_end or (end_ts is not None),
        sentence_end_ts=end_ts,
        singer_id=singer,
    )
    c.set_ruby(Ruby(parts=[RubyPart(text=m) for m in moras]))
    c.push_to_ruby()
    return c


def _plain_char(ch, *, check_count=1, timestamps=(), end_ts=None, is_end=False, singer=""):
    return Character(
        char=ch,
        check_count=check_count,
        timestamps=list(timestamps),
        is_sentence_end=is_end or (end_ts is not None),
        sentence_end_ts=end_ts,
        singer_id=singer,
    )


def _enc(chars, **kw):
    line, _ = sentence_to_timed_line(chars, **kw)
    return line


def _roundtrip(chars, **enc_kw):
    """编码→解码→再编码，返回 (line1, line2)。无损时二者相等。"""
    line1, _ = sentence_to_timed_line(chars, **enc_kw)
    dec_kw = {}
    if "singer_id_to_name" in enc_kw:
        dec_kw["name_to_singer_id"] = {v: k for k, v in enc_kw["singer_id_to_name"].items()}
    dec_kw["default_singer_id"] = enc_kw.get("default_singer_id", "")
    decoded, _ = parse_timed_line(line1, **dec_kw)
    line2, _ = sentence_to_timed_line(decoded, **enc_kw)
    return line1, line2, decoded


# ──────────────────────────────────────────────
# 编码字符串形态
# ──────────────────────────────────────────────


def test_encode_ruby_block_with_timestamps():
    chars = [
        _ruby_char("大", ["だ", "い"], [1000, 1200], linked=True),
        _ruby_char("冒", ["ぼ", "う"], [1400, 1600], linked=True),
        _ruby_char("険", ["け", "ん"], [1800, 2000]),
    ]
    line = _enc(chars)
    assert line == (
        "{大冒険||[00:01.00]だ|[00:01.20]い,"
        "[00:01.40]ぼ|[00:01.60]う,"
        "[00:01.80]け|[00:02.00]ん}"
    )


def test_encode_sentence_end_outside_block():
    chars = [
        _ruby_char("大", ["だ", "い"], [1000, 1200], linked=True),
        _ruby_char("冒", ["ぼ", "う"], [1400, 1600], linked=True),
        _ruby_char("険", ["け", "ん"], [1800, 2000]),
        _plain_char("だ", timestamps=[2200], end_ts=2500),
    ]
    line = _enc(chars)
    assert line.endswith("}[00:02.20]だ[>00:02.50]")


def test_encode_sentence_end_inside_block():
    chars = [
        _ruby_char("大", ["だ", "い"], [1000, 1200], linked=True),
        _ruby_char("険", ["け", "ん"], [1800, 2000], end_ts=2500),
    ]
    line = _enc(chars)
    assert line == (
        "{大険||[00:01.00]だ|[00:01.20]い,"
        "[00:01.80]け|[00:02.00]ん[>00:02.50]}"
    )


def test_encode_placeholders_when_no_timestamps():
    chars = [_ruby_char("大", ["だ", "い"], [], is_end=True)]
    line = _enc(chars)
    assert line == "{大||[T]だ|[T]い[>T]}"


def test_encode_bare_char_check_count_zero():
    chars = [_plain_char(" ", check_count=0)]
    assert _enc(chars) == " "


def test_encode_plain_kana_no_ts_uses_placeholder():
    chars = [_plain_char("あ", check_count=1, timestamps=[])]
    assert _enc(chars) == "[T]あ"


# ──────────────────────────────────────────────
# 解码 + 无损往返
# ──────────────────────────────────────────────


def test_roundtrip_full_timed():
    chars = [
        _ruby_char("大", ["だ", "い"], [1000, 1200], linked=True),
        _ruby_char("冒", ["ぼ", "う"], [1400, 1600], linked=True),
        _ruby_char("険", ["け", "ん"], [1800, 2000]),
        _plain_char("だ", timestamps=[2200], end_ts=2500),
    ]
    line1, line2, decoded = _roundtrip(chars)
    assert line1 == line2
    # 字段还原
    assert [c.char for c in decoded] == ["大", "冒", "険", "だ"]
    assert decoded[0].timestamps == [1000, 1200]
    assert [p.text for p in decoded[0].ruby.parts] == ["だ", "い"]
    assert decoded[0].linked_to_next and decoded[1].linked_to_next
    assert not decoded[2].linked_to_next
    assert decoded[3].is_sentence_end and decoded[3].sentence_end_ts == 2500
    assert decoded[3].timestamps == [2200] and decoded[3].ruby is None
    assert decoded[-1].is_line_end


def test_roundtrip_placeholders():
    chars = [_ruby_char("漢", ["か", "ん", "じ"], [5000], is_end=True)]
    line1, line2, decoded = _roundtrip(chars)
    assert line1 == line2
    assert decoded[0].check_count == 3
    assert decoded[0].timestamps == [5000]  # 仅首 checkpoint 有 ts，其余占位
    assert [p.text for p in decoded[0].ruby.parts] == ["か", "ん", "じ"]
    assert decoded[0].is_sentence_end and decoded[0].sentence_end_ts is None


def test_roundtrip_with_singer_tags():
    smap = {"id-a": "太郎", "id-b": "花子"}
    chars = [
        _plain_char("あ", timestamps=[100], singer="id-a"),
        _plain_char("い", timestamps=[200], singer="id-b"),
        _plain_char("う", timestamps=[300], singer="id-b"),
    ]
    line1, line2, decoded = _roundtrip(
        chars, singer_id_to_name=smap, default_singer_id="id-a"
    )
    assert "【太郎】" in line1 or line1.startswith("[")  # 首字=默认可不打标签
    assert "【花子】" in line1
    assert line1 == line2
    assert decoded[0].singer_id == "id-a"
    assert decoded[1].singer_id == "id-b"
    assert decoded[2].singer_id == "id-b"


def test_decode_collision_text_is_independent():
    """两行文本相同，但各自携带不同时间戳 → 独立解码互不影响。"""
    a = parse_timed_line("[00:01.00]か[00:01.10]き[00:01.20]く")[0]
    b = parse_timed_line("[00:02.00]か[00:02.10]き[00:02.20]く")[0]
    assert [c.timestamps for c in a] == [[1000], [1100], [1200]]
    assert [c.timestamps for c in b] == [[2000], [2100], [2200]]


def test_decode_malformed_bracket_becomes_literal():
    """非法 [xxxx] 按普通字符逐字解析，首字正常消耗 pending_starts。"""
    # 前面无 pending_starts → [99x] 和 [oops] 的各字均 check_count=0
    chars, _ = parse_timed_line("[99x]あ[oops]い")
    assert [c.char for c in chars] == list("[99x]あ[oops]い")
    assert all(c.check_count == 0 for c in chars)

    # 合法起始 token + 后接非法句尾 token → あ 正常带 ts，非法 [>bad] 变 6 个普通字符
    chars2, _ = parse_timed_line("[00:01.00]う[>bad]")
    assert chars2[0].char == "う" and chars2[0].timestamps == [1000]
    assert not chars2[0].is_sentence_end   # [>bad] 未被识别为句尾
    assert [c.char for c in chars2[1:]] == list("[>bad]")
    assert all(c.check_count == 0 for c in chars2[1:])

    # 合法 ts + 非法块 → pending_starts 由非法块首字消耗，不丢失
    # 场景：[00:09.61][约30秒][>00:39.30]
    chars3, _ = parse_timed_line("[00:09.61][XXX][>00:39.30]")
    # '[' 消耗 pending_starts(9610)；'X','X','X',']' check_count=0；
    # ']' 末尾的 [>00:39.30] 作为句尾附到最后一个字符
    assert chars3[0].char == "[" and chars3[0].timestamps == [9610]
    assert all(c.check_count == 0 for c in chars3[1:-1])
    assert chars3[-1].is_sentence_end and chars3[-1].sentence_end_ts == 39300

    # 块内非法起始 token → 仍按结构化块处理，首 checkpoint 占位（block 内不改行为）
    chars4, _ = parse_timed_line("{漢||[bad]か|[00:02.00]ん}")
    assert chars4[0].check_count == 2
    assert chars4[0].timestamps == []  # 首 cp 占位 → 截断
    assert [p.text for p in chars4[0].ruby.parts] == ["か", "ん"]

    # 占位符 [T] / [>T] 仍然合法
    chars5, _ = parse_timed_line("[T]あ[>T]")
    assert chars5[0].char == "あ"
    assert chars5[0].check_count == 1 and chars5[0].timestamps == []
    assert chars5[0].is_sentence_end and chars5[0].sentence_end_ts is None


def test_centisecond_rounding():
    """毫秒非 10 整除时编码到厘秒会截断（厘秒级精度）。"""
    chars = [_plain_char("あ", timestamps=[1234])]
    line = _enc(chars)
    assert line == "[00:01.23]あ"  # 1234ms → 厘秒 23
    decoded, _ = parse_timed_line(line)
    assert decoded[0].timestamps == [1230]


def test_default_singer_label_roundtrip():
    """切换回默认演唱者用 【默认演唱者】 标签；解码映射回 default_singer_id。"""
    smap = {"id-a": "太郎", "id-b": "花子"}
    chars = [
        _plain_char("ね", timestamps=[100], singer="id-b"),
        _plain_char("こ", timestamps=[200], singer="id-a"),  # id-a = default
    ]
    line, _ = sentence_to_timed_line(
        chars, singer_id_to_name=smap, default_singer_id="id-a"
    )
    assert "【花子】" in line and "【默认演唱者】" in line
    decoded, _ = parse_timed_line(
        line, name_to_singer_id={v: k for k, v in smap.items()}, default_singer_id="id-a"
    )
    assert decoded[0].singer_id == "id-b"
    assert decoded[1].singer_id == "id-a"


def test_timed_line_columns_point_to_glyphs():
    """timed_line_columns 返回的列号精确指向各字符的可见字形。"""
    chars = [
        _ruby_char("大", ["だ", "い"], [1000, 1200], linked=True),
        _ruby_char("険", ["け", "ん"], [1800, 2000]),
        _plain_char("だ", timestamps=[2200], end_ts=2500),
    ]
    line = _enc(chars)
    cols = timed_line_columns(chars)
    assert line[cols[0]] == "大"
    assert line[cols[1]] == "険"
    assert line[cols[2]] == "だ"
    assert cols[0] == 1  # '{' 之后


def test_offset_applied_on_encode_and_compensated_on_decode():
    """编码加全局偏移（与打轴显示一致），解码减回原始值。"""
    chars = [_plain_char("あ", timestamps=[1000], end_ts=2000)]
    line, _ = sentence_to_timed_line(chars, offset_ms=300)
    assert "[00:01.30]" in line  # 1000+300=1300ms → 厘秒 30
    assert "[>00:02.30]" in line  # 2000+300=2300ms
    decoded, _ = parse_timed_line(line, offset_ms=300)
    assert decoded[0].timestamps == [1000]
    assert decoded[0].sentence_end_ts == 2000


def test_empty_line_roundtrip():
    line, last = sentence_to_timed_line([], inherited_singer_id="x")
    assert line == ""
    chars, last2 = parse_timed_line("")
    assert chars == []
