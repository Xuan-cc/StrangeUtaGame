"""词典规范化脚本 —— 把"完美对齐"的旧词条拆为独立块。

策略
----
对当前 ``src/strange_uta_game/config/dictionary.json`` 每条 annotated 词条，
解析为 ``(raw_chars, ruby_map)``：

* **完美对齐**：``ruby_map`` 覆盖所有字符位置（即"段数 == 字符数"）且无空段。
  → 该条是用户精心分段的产物，可安全拆为 N 个独立块/字面字符：

  - 字符 ``c`` 段 ``r``：
    - 若 ``r == c``（严格相等）→ **自注音取消**，输出字面 ``c``。
    - 若 ``c`` 为长音符 ``ー`` → **直接剥离**注音，输出字面 ``ー``。
    - 否则 → 输出 ``{c||r}`` 独立块（含汉字 ``々`` 等所有其他情况）。

  例：
    - ``{微笑ん||ほほ,え,ん}`` → ``{微||ほほ}{笑||え}ん``
    - ``{食べ物||た,べ,もの}`` → ``{食||た}べ{物||もの}``
    - ``{真夏||ま,なつ}`` → ``{真||ま}{夏||なつ}``

* **未完美对齐**（段数 ≠ 字符数 / 含空段 / 含连词块）→ **原样保留**。
  例：``{大冒険||だいぼうけん}``、``{ハート||heart,,}``、``{木霊||こだま,}``。

异常处理
--------
* 解析失败 / ``raw_text != word`` → 原样保留。
* 单字词 → 原样保留（无拆分意义）。

用法
----
::

    python scripts/migrate_dictionary.py             # 实际写入
    python scripts/migrate_dictionary.py --dry-run   # 只生成报告，不写文件
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import re

from strange_uta_game.backend.infrastructure.parsers.annotated_text import (  # noqa: E402
    parse_annotated_line,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (  # noqa: E402
    split_into_moras,
)

DEFAULT_DICT = _SRC / "strange_uta_game" / "config" / "dictionary.json"
DEFAULT_REPORT = _ROOT / "scripts" / "migrate_dictionary_report.txt"


def _is_perfectly_aligned(
    word: str, annotated_reading: str
) -> Tuple[Optional[List[str]], Optional[List[str]]]:
    """判定旧 annotated reading 是否"完美对齐"。

    Returns:
        ``(raw_chars, segs)``：
        - 完美对齐 → 两者均为长度 N 的列表，``segs[i]`` 是字符 ``raw_chars[i]``
          的读音串（由 ruby_map[i] 的 RubyPart 拼接得到，无逗号）。
        - 不对齐 / 解析失败 / raw_text 不匹配 → ``(None, None)``。
    """
    try:
        raw_text, raw_chars, ruby_map = parse_annotated_line(annotated_reading)
    except Exception:
        return None, None
    if raw_text != word:
        return None, None
    n = len(raw_chars)
    if n == 0:
        return None, None
    # 完美对齐：每个位置都有非空 ruby 段
    segs: List[str] = []
    for i in range(n):
        parts = ruby_map.get(i)
        if not parts:
            return None, None  # 含空段（缺失或全空）
        joined = "".join(parts)
        if not joined:
            return None, None
        segs.append(joined)
    return raw_chars, segs


def _is_kana_only(r: str) -> bool:
    """reading 串是否只含假名字符（平假名、片假名、长音符）。

    只有纯假名 reading 才按 mora 拆分；含英文/数字/汉字等的读音不拆。
    """
    for ch in r:
        o = ord(ch)
        if not (0x3040 <= o <= 0x309F or  # 平假名
                0x30A0 <= o <= 0x30FF):   # 片假名（含ー）
            return False
    return bool(r)


def _mora_reading(r: str) -> str:
    """把纯假名读音串 ``r`` 按 mora 拆分后用 ``|`` 连接。

    非纯假名 reading（含英文字母、数字等）原样返回不拆。
    若只有 1 个 mora，直接返回原串。
    例：``ちゅう`` → ``ちゅ|う``；``ほほ`` → ``ほ|ほ``；``た`` → ``た``；``me`` → ``me``。
    """
    if not _is_kana_only(r):
        return r
    moras = split_into_moras(r)
    if len(moras) <= 1:
        return r
    return "|".join(moras)


def _normalize_aligned(raw_chars: List[str], segs: List[str]) -> str:
    """把完美对齐的 ``(chars, segs)`` 序列化为新 annotated 串。

    规则：
    * ``r == c`` → 字面 ``c``（自注音取消）。
    * ``c == 'ー'`` → 字面 ``c``（长音符剥离注音）。
    * 其他 → ``{c||<mora分段>}`` 独立块，reading 按 mora 拆后用 ``|`` 连接。
    """
    out: List[str] = []
    for c, r in zip(raw_chars, segs):
        if c == "ー":
            out.append(c)
        elif r == c:
            out.append(c)
        else:
            out.append(f"{{{c}||{_mora_reading(r)}}}")
    return "".join(out)


# 匹配单字独立块 {x||r}，其中 r 不含 , 和 |（单段纯读音）
_SINGLE_BLOCK_RE = re.compile(r"\{([^{}]+)\|\|([^{},|]+)\}")


def _apply_mora_to_reading(annotated: str) -> str:
    """对 annotated 串中所有单段独立块 ``{x||r}`` 补充 mora 分段。

    只处理 ``r`` 不含 ``,`` 和 ``|``（即单段、尚未 mora 化）且为纯假名的块。
    多字共用块（含 ``,``）或已有 mora 分段（含 ``|``）的块不动。
    """
    def _replace(m: "re.Match[str]") -> str:
        x, r = m.group(1), m.group(2)
        new_r = _mora_reading(r)
        return f"{{{x}||{new_r}}}"

    return _SINGLE_BLOCK_RE.sub(_replace, annotated)


def migrate(input_path: Path, *, dry_run: bool = False) -> Dict[str, Any]:
    """主流程：

    对每条词条执行两步处理（均在一次循环内）：

    1. **完美对齐拆细**：整词 annotated reading 满足"每字独立段、无空段"时，
       拆为独立 ``{c||r}`` 块 + 自注音/长音剥离。
    2. **mora 补全**：对所有单段独立块 ``{x||r}``（``r`` 纯假名、无 ``,``/``|``）
       补充 mora 分段（``r`` → ``m1|m2|...``）。

    两步对同一条词条先后执行（拆细后的产物也会被 mora 补全）。
    """
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"字典文件根必须是 list：{input_path}")

    new_entries: List[Dict[str, Any]] = []
    counts = {
        "step_aligned": 0,    # 完美对齐 → 拆细（含后续 mora 补全）
        "step_mora_only": 0,  # 未完美对齐但有 mora 补全发生
        "unchanged": 0,       # 两步均无变化
        "kept_single": 0,     # 单字词
    }
    sample_changed: List[Dict[str, str]] = []

    for entry in raw:
        if not isinstance(entry, dict):
            continue
        word = entry.get("word", "")
        old_reading = entry.get("reading", "")
        enabled = bool(entry.get("enabled", True))
        if not word or not old_reading:
            continue

        if len(word) <= 1:
            new_entries.append(
                {"enabled": enabled, "word": word, "reading": old_reading}
            )
            counts["kept_single"] += 1
            continue

        # Step 1: 完美对齐拆细
        chars, segs = _is_perfectly_aligned(word, old_reading)
        if chars is not None and segs is not None:
            reading_after_align = _normalize_aligned(chars, segs)
            aligned = True
        else:
            reading_after_align = old_reading
            aligned = False

        # Step 2: mora 补全（作用于 Step 1 产物）
        reading_after_mora = _apply_mora_to_reading(reading_after_align)

        new_reading = reading_after_mora
        new_entries.append(
            {"enabled": enabled, "word": word, "reading": new_reading}
        )

        if aligned:
            counts["step_aligned"] += 1
        elif new_reading != old_reading:
            counts["step_mora_only"] += 1
        else:
            counts["unchanged"] += 1

        if new_reading != old_reading and len(sample_changed) < 25:
            sample_changed.append(
                {"word": word, "old": old_reading, "new": new_reading}
            )

    if not dry_run:
        input_path.write_text(
            json.dumps(new_entries, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {
        "total_input": len(raw),
        "total_output": len(new_entries),
        "counts": counts,
        "sample_changed": sample_changed,
    }


def _write_report(report: Dict[str, Any], path: Path) -> None:
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("词典规范化报告（完美对齐拆细 + mora 补全）")
    lines.append("=" * 60)
    lines.append(f"输入条目数: {report['total_input']}")
    lines.append(f"输出条目数: {report['total_output']}")
    lines.append("")
    lines.append("-" * 60)
    lines.append("处理分布")
    lines.append("-" * 60)
    c = report["counts"]
    lines.append(f"  完美对齐拆细（含 mora 补全）: {c['step_aligned']}")
    lines.append(f"  仅 mora 补全（未对齐条目）:   {c['step_mora_only']}")
    lines.append(f"  无变化:                      {c['unchanged']}")
    lines.append(f"  单字词:                      {c['kept_single']}")
    lines.append("")
    if report["sample_changed"]:
        lines.append("-" * 60)
        lines.append("变化样本（前 25）")
        lines.append("-" * 60)
        for item in report["sample_changed"]:
            lines.append(f"  {item['word']}: {item['old']}  →  {item['new']}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: List[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", type=Path, default=DEFAULT_DICT)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    print(f"[migrate] 读取 {args.input}")
    report = migrate(args.input, dry_run=args.dry_run)
    _write_report(report, args.report)

    c = report["counts"]
    print(f"[migrate] 输入 {report['total_input']} → 输出 {report['total_output']}")
    print(
        f"[migrate]   aligned={c['step_aligned']} "
        f"mora_only={c['step_mora_only']} "
        f"unchanged={c['unchanged']} "
        f"single={c['kept_single']}"
    )
    print(f"[migrate] 报告 → {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
