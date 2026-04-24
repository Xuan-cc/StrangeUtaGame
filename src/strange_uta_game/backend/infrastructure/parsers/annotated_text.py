"""带注音标注的行级文本格式 解析/序列化。

用于全文本编辑界面（``frontend/editor/fulltext_interface``）在 ``Sentence.characters``
与单行 / 多行字符串之间互转。

格式约定
--------
``{大冒険||だ|い,ぼ|う,け|ん}``

- ``{ ... }``：一个注音块，内部由 ``||`` 把原文与读音分开。
- ``||``：分隔 "原文字符串" 与 "读音区"。
- ``,``：分隔多个字符，各自对应一组读音。
- ``|``：分隔同一字符内多个 ``RubyPart``（mora）。

兼容格式
--------
- ``{漢|か|ん|じ}``：单字多段 mora（``||`` 缺省）。
- ``{赤|あか}``：单字单段 reading。
- ``{text}``：无读音，等价于纯文本（右括号闭合）。

不支持的旧格式（已在 0.x 弃用）
- ``漢字{かんじ}``：后置格式。

Public API
----------
- :func:`parse_annotated_line` — 单行文本 → (raw_text, raw_chars, ruby_map)。
- :func:`sentence_to_annotated_line` — ``Sequence[Character]`` → 单行带注音字符串，
  连词组（``linked_to_next`` 链）合并为一个 ``{...||...}`` 块。
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    # 仅用于类型注解，避免运行时 import 产生循环。
    from strange_uta_game.backend.domain.models import Character


def parse_annotated_line(
    line_text: str,
) -> Tuple[str, List[str], Dict[int, List[str]]]:
    """解析带注音标注的文本行为 (原文, 字符列表, ruby_map)。

    Args:
        line_text: 单行文本（不含换行符）。

    Returns:
        ``(raw_text, raw_chars, ruby_map)``：

        - ``raw_text`` — 剥离标注后的纯原文；
        - ``raw_chars`` — 与 ``raw_text`` 字符一一对应的列表；
        - ``ruby_map`` — ``char_idx → [RubyPart.text, ...]``，无读音的字符不出现在键中。

    Note:
        未配对的 ``{`` 按普通字符处理，保持与 0.2 行为一致。
    """
    raw_chars: List[str] = []
    ruby_map: Dict[int, List[str]] = {}
    i = 0
    n = len(line_text)

    while i < n:
        if line_text[i] == "{":
            close = line_text.find("}", i)
            if close == -1:
                # 无配对右括号，当普通字符处理
                raw_chars.append(line_text[i])
                i += 1
                continue

            content = line_text[i + 1 : close]

            if "||" in content:
                # 主格式：text||mora|mora,mora|mora
                text_part, readings_part = content.split("||", 1)
                per_char_readings = readings_part.split(",")
                start_idx = len(raw_chars)
                for ch in text_part:
                    raw_chars.append(ch)

                for j, reading_group in enumerate(per_char_readings):
                    # reading_group 内部用 "|" 分 mora；空串代表无 ruby
                    parts = [p for p in reading_group.split("|") if p != ""]
                    if parts and (start_idx + j) < len(raw_chars):
                        ruby_map[start_idx + j] = parts
            elif "|" in content:
                # 兼容简短：{text|mora|mora|mora}（单字多段）或 {text|reading}（单字单段）
                text_part, _, readings_part = content.partition("|")
                parts = [p for p in readings_part.split("|") if p != ""]

                start_idx = len(raw_chars)
                for ch in text_part:
                    raw_chars.append(ch)

                if len(text_part) == 1 and parts:
                    ruby_map[start_idx] = parts
                elif len(text_part) > 1 and parts:
                    # 歧义：多字只给一个 reading，兜底当作首字全吃
                    ruby_map[start_idx] = parts
            else:
                # {text} 无 ruby → 纯文本
                for ch in content:
                    raw_chars.append(ch)

            i = close + 1
        else:
            raw_chars.append(line_text[i])
            i += 1

    raw_text = "".join(raw_chars)
    return raw_text, raw_chars, ruby_map


def sentence_to_annotated_line(characters: "Sequence[Character]") -> str:
    """把一个 Sentence 的 ``characters`` 序列化为单行带注音文本。

    序列化规则：

    - 连续的 ``linked_to_next`` 链合并为一个 ``{原文||mora|...,mora|...}`` 块，
      同一字的多 ``RubyPart`` 用 ``|`` 相连，不同字之间用 ``,``。
    - 非连词且带 ``ruby`` 的字符输出为 ``{字||mora|mora|...}``（单字块）。
    - 无 ``ruby`` 的字符按 ``character.char`` 原样输出。

    Args:
        characters: ``Sentence.characters`` 序列。

    Returns:
        单行字符串（不含换行符）；空输入返回空串。
    """
    buf: List[str] = []
    i = 0
    n = len(characters)
    while i < n:
        ch = characters[i]
        if ch.ruby:
            # 收集连词组（linked_to_next 链）
            group_start = i
            while i < n - 1 and characters[i].linked_to_next:
                i += 1
            i += 1  # 包含链中最后一个字符
            group = characters[group_start:i]
            text_part = "".join(c.char for c in group)
            readings = ",".join(
                "|".join(p.text for p in c.ruby.parts) if c.ruby else ""
                for c in group
            )
            buf.append(f"{{{text_part}||{readings}}}")
        else:
            buf.append(ch.char)
            i += 1
    return "".join(buf)
