"""RhythmicaLyrics 字典 → 项目 dictionary.json 转换 CLI（薄包装）。

后端 ``strange_uta_game.backend.infrastructure.parsers.rl_dictionary`` 已支持：
* tab 行（旧 RL .txt / NetKakuteiYomi.txt 缓存）
* 成对行（KAKUTE_MOJI_INIT.hsp 字面体内体 / .ini ``[AutoCheckDefine]`` 段）
* HSP 字面体（自动剥壳）
* INI ``[AutoCheckDefine]`` 段（自动定位）
* utf-8 / cp932 编码自动识别

本脚本仅作 CLI 入口。GUI 中"导入RL字典"按钮与"网络词典导入文件"按钮均直接复用
后端解析器，无需经过此脚本。

用法
----
::

    python scripts/import_rl_builtin_dict.py <input_path> [-o out.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "src"))

from strange_uta_game.backend.infrastructure.parsers.rl_dictionary import (  # noqa: E402
    parse_rl_dictionary,
    read_rl_dictionary_file,
)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("input", type=Path)
    p.add_argument("-o", "--output", type=Path, default=Path("rl_dict.json"))
    args = p.parse_args()

    text = read_rl_dictionary_file(str(args.input))
    entries = parse_rl_dictionary(text)
    args.output.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[done] {len(entries)} 条 → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
