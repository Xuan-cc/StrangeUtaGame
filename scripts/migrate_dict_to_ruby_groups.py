"""字典迁移脚本：v0.1.0 → v0.2.0 Ruby 分组格式。

分隔符语义
----------
- ',' (逗号)  = 词内【字符】边界（哪几个读音属于哪个汉字）
- '#' (井号)  = 单个字符内的 【checkpoint】 边界（多拍时）

示例：
    私 わたし        (1字3拍)  →  わ#た#し
    大冒険 だいぼうけん (3字)   →  だ#い,ぼ#う,けん
    赤い あかい       (2字)    →  あ,か#い

迁移规则
--------
1. reading 已含 '#' → 只规整空白，不改动。
2. len(word) == 1 → split_into_moras(reading) 用 '#' 连接（不引入 ','）。
3. len(word) > 1：
   a. reading 含 ',' → 保留 ',' 作字符边界，每段内部用 mora→'#' 切分。
   b. 无分隔符 → 先用 AutoCheckService._try_split_to_chars 按字拆分得到
      每字符读音，然后每字符内再 mora→'#' 切分，最后 ',' 连接。
   c. 如果按字拆分失败 → 回退：split_into_moras 仅当 mora 数 == len(word)
      时 1:1 分配（每字符一拍，无需 '#'），',' 连接。
   d. 否则归入 manual_review，保持原样。
4. 输出 --dry-run 报告 / --apply 实际写入（写前自动备份 .bak.<timestamp>）。

用法
----
    # 对默认分发字典 dry-run
    python scripts/migrate_dict_to_ruby_groups.py --dry-run

    # 对运行时字典 apply
    python scripts/migrate_dict_to_ruby_groups.py \
        --input "%APPDATA%\\StrangeUtaGame\\dictionary.json" --apply

    # 指定任意字典文件
    python scripts/migrate_dict_to_ruby_groups.py \
        --input path/to/dictionary.json --apply
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 将项目源码根加入 sys.path，允许作为独立脚本运行
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from strange_uta_game.backend.application.auto_check_service import (  # noqa: E402
    AutoCheckService,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (  # noqa: E402
    split_into_moras,
)


DEFAULT_DICT = _SRC / "strange_uta_game" / "config" / "dictionary.json"


# ──────────────────────────────────────────────
# 单条迁移
# ──────────────────────────────────────────────


def _moras_to_cp_groups(reading: str) -> str:
    """将单字符的读音按 mora 切分，用 '#' 连接作为 checkpoint 分组。

    单 mora 时返回原串（不加 '#'）。
    """
    moras = split_into_moras(reading)
    if len(moras) <= 1:
        return reading
    return "#".join(moras)


def _migrate_reading(
    word: str,
    reading: str,
    service: AutoCheckService,
) -> Tuple[str, str]:
    """返回 (new_reading, status)。

    status:
      unchanged       - 原样保留（已是新格式或单 mora）
      normalized      - 仅规整空白
      comma_cp_split  - 保留 ','，每段内部 mora→'#'
      mora_split      - 单字符词按 mora→'#'
      char_split      - 多字符词按字拆分 + 每字符内 mora→'#'，','  连接
      char_split_flat - 多字符词按字拆分得单拍，无 '#'，','  连接
      manual_review   - 无法自动判断
    """
    if not reading:
        return reading, "unchanged"

    raw = reading.strip()

    # 1. 已含 '#' → 仅规整空白
    if "#" in raw:
        # 按 ',' 分字符，每字符内按 '#' 分 cp，全部 strip 后重新拼
        char_parts: List[str] = []
        for seg in raw.split(","):
            cps = [g.strip() for g in seg.split("#") if g.strip()]
            if not cps:
                return reading, "manual_review"
            char_parts.append("#".join(cps))
        new = ",".join(char_parts)
        return new, ("normalized" if new != reading else "unchanged")

    # 2. 单字符词
    if len(word) <= 1:
        new = _moras_to_cp_groups(raw)
        if new == raw:
            return raw, ("normalized" if raw != reading else "unchanged")
        return new, "mora_split"

    # 3. 多字符词 + 已含 ',' → 每段内部 mora→'#'
    if "," in raw:
        segs = [s.strip() for s in raw.split(",")]
        if not segs or any(not s for s in segs):
            return reading, "manual_review"
        new = ",".join(_moras_to_cp_groups(s) for s in segs)
        return new, "comma_cp_split"

    # 4. 多字符词 + 无分隔 → 尝试按字拆分
    try:
        char_split = service._try_split_to_chars(word, raw)  # noqa: SLF001
    except Exception:
        char_split = None
    if char_split and len(char_split) == len(word) and all(char_split):
        new = ",".join(_moras_to_cp_groups(s) for s in char_split)
        status = "char_split" if any("#" in p for p in new.split(",")) else "char_split_flat"
        return new, status

    # 5. 回退：mora 数 == 词长 → 每字符一拍，无 '#'
    moras = split_into_moras(raw)
    if len(moras) == len(word):
        return ",".join(moras), "char_split_flat"

    # 6. 无法自动判断
    return reading, "manual_review"


# ──────────────────────────────────────────────
# 文件级迁移
# ──────────────────────────────────────────────


def migrate_file(
    input_path: Path,
    *,
    apply: bool,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(f"字典文件不存在: {input_path}")

    raw = input_path.read_text(encoding="utf-8")
    entries: List[Dict[str, Any]] = json.loads(raw)
    if not isinstance(entries, list):
        raise ValueError(f"字典格式异常，期望 list，实得 {type(entries).__name__}")

    # AutoCheckService 需要以原字典构造上下文
    service = AutoCheckService(user_dictionary=entries)

    stats: Dict[str, int] = {
        "total": len(entries),
        "unchanged": 0,
        "normalized": 0,
        "comma_cp_split": 0,
        "mora_split": 0,
        "char_split": 0,
        "char_split_flat": 0,
        "manual_review": 0,
    }
    manual_samples: List[Dict[str, Any]] = []
    migrated: List[Dict[str, Any]] = []

    for entry in entries:
        if not isinstance(entry, dict):
            migrated.append(entry)
            stats["unchanged"] += 1
            continue

        word = str(entry.get("word", ""))
        reading = str(entry.get("reading", ""))
        new_reading, status = _migrate_reading(word, reading, service)
        stats[status] = stats.get(status, 0) + 1

        if status == "manual_review" and len(manual_samples) < 20:
            manual_samples.append(
                {"word": word, "reading": reading, "reason": "cannot auto split"}
            )

        new_entry = dict(entry)
        new_entry["reading"] = new_reading
        migrated.append(new_entry)

    report = {
        "input": str(input_path),
        "stats": stats,
        "manual_review_samples": manual_samples,
        "applied": False,
        "backup": None,
        "output": None,
    }

    if apply:
        out = output_path or input_path
        # 备份
        if out.exists():
            stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = out.with_suffix(out.suffix + f".bak.{stamp}")
            shutil.copy2(out, backup)
            report["backup"] = str(backup)
        out.write_text(
            json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report["applied"] = True
        report["output"] = str(out)

    return report


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────


def _print_report(report: Dict[str, Any]) -> None:
    print("=" * 60)
    print(f"输入: {report['input']}")
    print("统计:")
    for key, val in report["stats"].items():
        print(f"  {key:<16} {val}")
    samples = report.get("manual_review_samples") or []
    if samples:
        print(f"\n需人工复核样本（前 {len(samples)} 条）:")
        for s in samples:
            print(f"  - {s['word']}  reading={s['reading']!r}")
    if report["applied"]:
        print(f"\n✅ 已写入: {report['output']}")
        print(f"   备份:   {report['backup']}")
    else:
        print("\nℹ️  dry-run，未写入。使用 --apply 真正执行迁移。")
    print("=" * 60)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=DEFAULT_DICT, help="字典 JSON 路径")
    ap.add_argument("--output", type=Path, default=None, help="输出路径（默认覆盖 --input）")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=True, help="只生成报告（默认）")
    group.add_argument("--apply", action="store_true", help="实际写入迁移结果")
    args = ap.parse_args(argv)

    report = migrate_file(args.input, apply=bool(args.apply), output_path=args.output)
    _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
