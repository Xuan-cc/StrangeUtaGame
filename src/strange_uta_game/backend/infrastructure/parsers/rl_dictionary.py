"""RhythmicaLyrics 字典文件解析 — 纯文本 → 条目列表。

格式（与 RhythmicaLyrics 兼容）：每行 ``[原文]\\t[注音1],[注音2],...``。

- 注音项中的全角加号 ``＋`` 为连词占位符，解析时剥离；
- 仅含 ``＋`` 的项表示该字符无独立读音（与前字符连词），与其他空读音一同在尾部被去除；
- 空行 / 无 ``\\t`` / 原文或注音全空的行直接跳过。

Public API
----------
- :func:`parse_rl_dictionary` — 文本 → ``List[Dict[str, object]]``，形如
  ``[{"enabled": True, "word": "赤い", "reading": "あ,かい"}, ...]``。
"""

from __future__ import annotations

from typing import Dict, List

# 全角加号 U+FF0B
_LINK_MARKER = "\uff0b"


def parse_rl_dictionary(text: str) -> List[Dict[str, object]]:
    """解析 RL 字典文本为条目列表。

    Args:
        text: 原始文本内容。

    Returns:
        条目列表；每项包含 ``enabled`` (bool, 总为 True)、``word`` (str) 与
        ``reading`` (str，逗号分隔)。若读音全部为空则该行丢弃。
    """
    entries: List[Dict[str, object]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or "\t" not in line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        word = parts[0].strip()
        raw_readings = parts[1].strip()
        if not word or not raw_readings:
            continue

        cleaned: List[str] = []
        for piece in raw_readings.split(","):
            piece = piece.strip().replace(_LINK_MARKER, "")
            cleaned.append(piece)

        # 去除尾部多余空读音（含纯 ＋ 被剥离后的空项）
        while cleaned and not cleaned[-1]:
            cleaned.pop()

        reading = ",".join(cleaned)
        if reading:
            entries.append({"enabled": True, "word": word, "reading": reading})
    return entries
