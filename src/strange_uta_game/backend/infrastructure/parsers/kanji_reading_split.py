"""把多字汉字块的整段假名读音按字拆开。

两条策略：
1. ``split_by_kanji_dict(word, reading)``：用 KANJIDIC2 派生的单字音读/训读
   字典做组合匹配。例 「世界」+「せかい」 → ["せ", "かい"]。无法匹配时返回
   ``None`` —— 这通常意味着遇到了 ateji（当て字，整词读音与单字读音无关）。
2. ``even_distribute_kana(reading, n_chars)``：把假名按字数均分。例
   「はじまり」(4 假名) 分给 「新時代」(3 字) → ["はじ", "ま", "り"]，
   多出的假名贴在首字。ateji 场景的兜底。

组合接口 ``compute_per_kanji_readings`` 先试字典，失败再均分，返回 (读音列表, 是否 ateji)。

被 ``lyric_parser.parse_to_sentences`` 在解析 UtaTen 标记 LRC 时调用 —— 让带
``[tool:utaten-ruby]`` 头的输入也能享受「连词 + 每字 ruby」的呈现，而不是
所有假名都堆在首字。

历史包袱：``backend/application/auto_check_service.AutoCheckService._split_by_kanji_dict``
是该字典查询的原始实现，逻辑等同；该副本独立到 infrastructure 层是为了避免
``lyric_parser`` 反向依赖 application 层。后续若清理 auto_check_service 可以
直接复用本模块。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import List, Optional, Tuple


# 连浊：清音首字母 → 浊音（处理「々」继承前字读音时常见）
_DAKUTEN_MAP = str.maketrans(
    "かきくけこさしすせそたちつてとはひふへほ",
    "がぎぐげござじずぜぞだぢづでどばびぶべぼ",
)
# 半浊音：は行 → ぱ行
_HANDAKUTEN_MAP = str.maketrans("はひふへほ", "ぱぴぷぺぽ")


def _kata_to_hira(text: str) -> str:
    out: List[str] = []
    for ch in text:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


def _is_kanji(ch: str) -> bool:
    if len(ch) != 1:
        return False
    code = ord(ch)
    return (
        (0x4E00 <= code <= 0x9FFF)
        or (0x3400 <= code <= 0x4DBF)
        or (0xF900 <= code <= 0xFAFF)
        or code == 0x3005  # 々
    )


@lru_cache(maxsize=1)
def _load_kanji_dict() -> dict:
    """惰性加载 ``config/kanji_readings.json``。失败返回空 dict（降级到均分）。"""
    try:
        # 本文件位于 backend/infrastructure/parsers/，字典在 strange_uta_game/config/。
        # 路径：上溯三级到 strange_uta_game/ 根，再进 config/。
        config_path = Path(__file__).resolve().parents[3] / "config" / "kanji_readings.json"
        if config_path.exists():
            return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _char_reading_options(ch: str, kanji_dict: dict, prev_opts: Optional[List[str]]) -> Optional[List[str]]:
    """返回单个字符的候选读音列表（已转平假名）。

    - 「々」：继承上一字的候选，附加连浊/半浊变体。
    - 普通汉字：字典中 on + kun（kun 去掉送假名 ``.`` 之后部分）。
    - 字典查不到：返回 None（整块判定失败）。
    """
    if ch == "々":
        if not prev_opts:
            return None
        opts = list(prev_opts)
        for opt in prev_opts:
            if not opt:
                continue
            head = opt[0]
            dakuten = head.translate(_DAKUTEN_MAP)
            if dakuten != head:
                variant = dakuten + opt[1:]
                if variant not in opts:
                    opts.append(variant)
            handakuten = head.translate(_HANDAKUTEN_MAP)
            if handakuten != head:
                variant = handakuten + opt[1:]
                if variant not in opts:
                    opts.append(variant)
        return opts

    entry = kanji_dict.get(ch)
    if not entry:
        return None
    on = [_kata_to_hira(r) for r in entry.get("on", [])]
    kun_general: List[str] = []
    kun_positional: List[str] = []
    for r in entry.get("kun", []):
        hira = _kata_to_hira(r).split(".")[0]
        if not hira:
            continue
        if hira.startswith("-"):
            kun_positional.append(hira.lstrip("-"))
        else:
            kun_general.append(hira)
    merged: List[str] = []
    seen = set()
    for r in on + kun_general + kun_positional:
        if r and r not in seen:
            seen.add(r)
            merged.append(r)
    return merged or None


def split_by_kanji_dict(word: str, reading: str) -> Optional[List[str]]:
    """用字典组合匹配把 ``reading`` 切分到 ``word`` 的每个字符。

    成功返回长度 == ``len(word)`` 的读音列表（每段对应一个字符）；
    任一字符字典查不到、或所有组合都拼不出 ``reading`` 时返回 ``None``。

    ``reading`` 中含片假名时先归一为平假名再匹配（与字典里 on/kun 的存储方式一致）。
    """
    if not word or not reading:
        return None
    kanji_dict = _load_kanji_dict()
    if not kanji_dict:
        return None

    target = _kata_to_hira(reading)

    char_options: List[List[str]] = []
    for i, ch in enumerate(word):
        prev = char_options[-1] if char_options else None
        opts = _char_reading_options(ch, kanji_dict, prev)
        if not opts:
            return None
        char_options.append(opts)

    n = len(word)

    def _match(idx: int, pos: int) -> Optional[List[str]]:
        if idx == n:
            return [] if pos == len(target) else None
        for opt in char_options[idx]:
            end = pos + len(opt)
            if end <= len(target) and target[pos:end] == opt:
                rest = _match(idx + 1, end)
                if rest is not None:
                    return [opt] + rest
        return None

    return _match(0, 0)


def even_distribute_kana(reading: str, n_chars: int) -> List[str]:
    """把 ``reading`` 按字数均分成 ``n_chars`` 段，多出的假名贴到首字。

    ateji（如 新時代/はじまり）兜底：4 假名 / 3 字 → ["はじ", "ま", "り"]，
    1+2+1 这种"中段独大"明显反直觉，"末段独大"会让句首拖音，所以选首段独大。
    """
    if n_chars <= 0:
        return []
    if not reading:
        return [""] * n_chars

    target = _kata_to_hira(reading)
    total = len(target)
    base, extra = divmod(total, n_chars)
    parts: List[str] = []
    pos = 0
    for i in range(n_chars):
        # 余数全部塞给首字；中间和末尾各拿 base 个假名
        length = base + extra if i == 0 else base
        parts.append(target[pos : pos + length])
        pos += length
    return parts


def compute_per_kanji_readings(word: str, reading: str) -> Tuple[List[str], bool]:
    """先试字典拆分，失败回退到均分。

    返回 ``(readings_per_char, is_ateji)``：
    - 字典命中 → ``is_ateji=False``，每段是该字真实读音；
    - 字典失败 → ``is_ateji=True``，每段是均分结果（首字独大）。

    ``word`` 必须全部是汉字（``_is_kanji`` 判定）；含其他字符时返回 ``([reading], True)``
    把整段挂回首字（调用方自行处理 fallback）。
    """
    if not word or not reading:
        return ([reading], True)

    if not all(_is_kanji(c) for c in word):
        return ([reading], True)

    dict_split = split_by_kanji_dict(word, reading)
    if dict_split is not None and len(dict_split) == len(word):
        return (dict_split, False)

    return (even_distribute_kana(reading, len(word)), True)
