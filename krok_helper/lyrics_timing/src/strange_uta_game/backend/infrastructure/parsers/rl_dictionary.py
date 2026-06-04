"""RhythmicaLyrics 字典文件解析 — 纯文本 → annotated 格式条目列表。

格式（与 RhythmicaLyrics 兼容）：每行 ``[原文]\\t[注音1],[注音2],...``。

- 注音项中的全角加号 ``＋`` 为连词占位符，解析时剥离；
- 仅含 ``＋`` 的项表示该字符无独立读音（与前字符连词），与其他空读音一同在尾部被去除；
- 空行 / 无 ``\\t`` / 原文或注音全空的行直接跳过；
- 含 ASCII 字母的 ``word`` 整条丢弃（项目不再支持英文词条走用户词典）；
- 注音直接转换为项目规范的 annotated 行内格式（参见 ``annotated_text``）：

  - 段数 == 字符数且每段非空 → 直转为 ``{word||r1,r2,...,rN}``；
  - 否则跑 Sudachi 重分析 → ``{block||reading,空,...}`` 块拼接；
  - Sudachi 失败 → 整词单块兜底 ``{word||full_reading,空,...}``。

Public API
----------
- :func:`parse_rl_dictionary` — 文本 → ``List[Dict[str, object]]``，形如
  ``[{"enabled": True, "word": "赤い", "reading": "{赤||あか}い"}, ...]``。
- :func:`convert_legacy_reading` — 单条 ``(word, 老逗号 reading)`` → 新 annotated reading。
  迁移脚本与 RL 导入共用。
"""

from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Tuple

# 全角加号 U+FF0B
_LINK_MARKER = "\uff0b"

# RL 读音尾标 @<digit>（a_chk_kakute_flg 字段）
_FLAG_TAIL_RE = re.compile(r"@\d+\s*$")

# KAKUTE_MOJI_INIT.hsp 中 HSP 字面体：a_chk_kakute_moji_su_init={"..."}
_HSP_LITERAL_RE = re.compile(
    r'a_chk_kakute_moji_su_init\s*=\s*\{"(.*?)"\}', re.DOTALL
)


# ──────────────────────────────────────────────
# 字符判定 / 转换
# ──────────────────────────────────────────────


def _has_ascii_letter(s: str) -> bool:
    """``word`` 是否含 ASCII 英文字母（用于识别需丢弃的英文词条）。"""
    return any(("a" <= c <= "z") or ("A" <= c <= "Z") for c in s)


def _is_kanji(c: str) -> bool:
    """是否汉字（CJK 统合汉字基本区 + 扩展 A）。"""
    o = ord(c)
    return 0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF


def _kata_to_hira(s: str) -> str:
    """片假名转平假名。"""
    out: List[str] = []
    for c in s:
        o = ord(c)
        if 0x30A1 <= o <= 0x30F6:
            out.append(chr(o - 0x60))
        else:
            out.append(c)
    return "".join(out)


# ──────────────────────────────────────────────
# RL 单 piece 解析：连词/数字/cp 后缀
# ──────────────────────────────────────────────


# 仅数字（半角，可前导零）的 piece 整体被 RL 视为 "无 ruby + 显式 cp 数"
_DIGIT_ONLY_RE = re.compile(r"^\d+$")
# "/<digits>" 末尾后缀（cp 强制覆盖；RL src: /\d+$）
_CP_OVERRIDE_SUFFIX_RE = re.compile(r"/(\d+)$")


def _parse_piece(piece: str) -> Tuple[str, bool, Optional[int]]:
    """解析一个 RL 读音 piece 的语义三元组。

    RL 真实语义（来自 ``@RhythmicaLyrics.hsp:12636+``）：
      * 末尾 ``＋``（U+FF0B）→ 该字符与下一字符连词（``temp_kakute_plus[i]=1``）。
        注：前缀 ``＋`` 在某些 RL 数据中也出现（与上一字符连词），剥离即可。
      * ``/<digits>`` 后缀 → 强制 cp 数（``temp_kakute_yomisu_kyosei``），剥离后
        保留 ruby 文本。
      * 整段全数字 → 该字符 cp = int(piece)，无 ruby（``jc_yomisu(cnt)=int``，
        ``temp_kakute_tan`` 视为空 ruby）。
      * 否则 → 普通 ruby 文本。RL 不在 piece 内再以分隔符切 mora —— 整个 piece
        即一个字符的连续 mora 串，cp 由下游按 mora 计数推断。

    Returns:
        ``(ruby, linked_to_next, cp_override)``：
        ``ruby`` 是 piece 的 ruby 文本（可能为空，表示无独立 ruby）；
        ``linked_to_next`` 为 True 时该字符与下一字符连词；
        ``cp_override`` 不为 None 时表示用户显式指定的 cp 数（本项目仅在
        ``ruby == ""`` 时用作 cp 信息保留；annotated 格式不携带 cp 数，由
        下游 ``_apply_user_dictionary_to_sentence`` 按 RubyPart 数派生）。
    """
    s = piece.strip()
    linked = False
    if s.endswith(_LINK_MARKER):
        linked = True
        s = s[:-1].rstrip()
    if s.startswith(_LINK_MARKER):
        # 防御性：剥离前缀 ＋（视作来自上一字符的链接残余）
        s = s[1:].lstrip()
    cp_override: Optional[int] = None
    m = _CP_OVERRIDE_SUFFIX_RE.search(s)
    if m:
        cp_override = int(m.group(1))
        s = s[: m.start()].rstrip()
    if _DIGIT_ONLY_RE.match(s):
        cp_override = int(s)
        s = ""
    return s, linked, cp_override


def _ruby_to_mora_segment(ruby: str) -> str:
    """把 ruby 文本按 mora 拆分并用 ``|`` 连接（annotated 段内 mora 分隔符）。

    复用 ``inline_format.split_into_moras`` —— 小假名 / ``ー`` 附属前一拍。
    """
    if not ruby:
        return ""
    # 延迟 import 避免模块循环
    from strange_uta_game.backend.infrastructure.parsers.inline_format import (
        split_into_moras,
    )
    moras = split_into_moras(ruby)
    return "|".join(moras) if moras else ""


# ──────────────────────────────────────────────
# 直转主分支：RL piece → annotated 块串
# ──────────────────────────────────────────────


def _direct_convert(word: str, old_reading: str) -> Optional[str]:
    """按 RL 真实语义把 ``readings`` 转为 annotated 块串。

    流程：
      1. ``readings.split(",")`` → ``pieces``；
      2. 每 piece 调 :func:`_parse_piece` 拆出 ``(ruby, linked, cp_override)``；
      3. 长度对齐 ``len(word)``：超出截断、不足补空；
      4. 按 ``linked`` 串成连词链 ``[i..j]``；
      5. 每个链生成 annotated 输出：
         * 单字（``i == j``）：
           - ruby 空 → 字面字符输出；
           - ruby 与字符相等（kata→hira 归一化后）→ 字面输出；
           - 否则 ``{char||mora|mora|...}``。
         * 连词块（``i < j``）：``{chars||seg_i,seg_{i+1},...,seg_j}``；
           segs 全空时退化为字面拼接。

    Returns:
        annotated reading 串；老 reading 全空（无任何有效 ruby）→ ``None``。
    """
    chars = list(word)
    if not chars:
        return None
    pieces = old_reading.split(",") if old_reading else [""]
    parsed = [_parse_piece(p) for p in pieces]

    n = len(chars)
    if len(parsed) > n:
        # 超出部分：若仍有 ruby，拼到最后一个字符（罕见 RL 数据切分错位）
        tail_rubies = [
            t[0] for t in parsed[n - 1 :] if t[0]
        ]
        if tail_rubies:
            merged_ruby = "".join(tail_rubies)
            linked_to_next_last = parsed[-1][1]
            parsed = parsed[: n - 1] + [(merged_ruby, linked_to_next_last, None)]
        else:
            parsed = parsed[:n]
    if len(parsed) < n:
        parsed = parsed + [("", False, None)] * (n - len(parsed))

    # 若所有 piece 的 ruby 均为空 → 整词无注音 → 视为无效
    if not any(t[0] for t in parsed):
        return None

    out: List[str] = []
    i = 0
    while i < n:
        j = i
        while j < n - 1 and parsed[j][1]:
            j += 1
        chain_chars = "".join(chars[i : j + 1])
        chain_rubies = [parsed[k][0] for k in range(i, j + 1)]
        chain_segs = [_ruby_to_mora_segment(r) for r in chain_rubies]

        if i == j:
            seg = chain_segs[0]
            char = chars[i]
            if not seg:
                out.append(char)
            elif _kata_to_hira(seg.replace("|", "")) == _kata_to_hira(char):
                out.append(char)
            else:
                out.append(f"{{{char}||{seg}}}")
        else:
            if all(s == "" for s in chain_segs):
                out.append(chain_chars)
            else:
                segs_str = ",".join(chain_segs)
                out.append(f"{{{chain_chars}||{segs_str}}}")
        i = j + 1

    return "".join(out)


# ──────────────────────────────────────────────
# 共用入口：老 reading → 新 annotated reading
# ──────────────────────────────────────────────


def convert_legacy_reading(word: str, old_reading: str) -> Optional[str]:
    """把单条 ``(word, RL piece-逗号 reading)`` 转换为新 annotated reading。

    遵循 RL 真实语义（``@RhythmicaLyrics.hsp:12636+`` ``getNetKakuteiYomiText`` 应用路径）：
      * piece 末尾 ``＋`` → 连词；
      * piece 末尾 ``/<N>`` → 强制 cp 数（注音格式不承载，自动按 mora 派生）；
      * 整段数字 piece → 无 ruby，cp = 该数（同上）；
      * 多 mora ruby → 按 mora（小假名 / ``ー`` 附属前拍）拆分到 ``|``；
      * ruby == 字符（kata→hira 归一化）→ 字面输出（不包 ``{...||...}``）。

    Args:
        word: 词条字面。
        old_reading: RL 风格逗号分段读音。

    Returns:
        新 reading；``word`` 含 ASCII 字母 / 读音全空时返回 ``None``。
    """
    if not word or not old_reading:
        return None
    if _has_ascii_letter(word):
        return None
    return _direct_convert(word, old_reading)


# ──────────────────────────────────────────────
# 主入口：RL 文本 → 条目列表
# ──────────────────────────────────────────────


def _strip_flag(reading: str) -> str:
    """剥离尾部 ``@<digit>`` 标志位（RL 的 a_chk_kakute_flg 字段）。"""
    return _FLAG_TAIL_RE.sub("", reading).rstrip()


def _iter_tab_pairs(text: str) -> Iterator[Tuple[str, str]]:
    """tab 行格式 ``word\\treadings`` → (word, readings) 迭代。"""
    for raw in text.splitlines():
        line = raw.strip()
        if not line or "\t" not in line:
            continue
        word, _, raw_readings = line.partition("\t")
        word = word.strip()
        raw_readings = _strip_flag(raw_readings.strip())
        if word and raw_readings:
            yield word, raw_readings


def _iter_pairlines(text: str) -> Iterator[Tuple[str, str]]:
    """RL 成对行（KAKUTE_MOJI_INIT.hsp / .ini AutoCheckDefine）→ (word, readings)。

    奇数（非空）行 = word；紧邻下一非空行 = readings（可能含 ``@flag``）。
    """
    pending: Optional[str] = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if pending is None:
            pending = line
        else:
            yield pending, _strip_flag(line)
            pending = None


def _extract_hsp_literal(text: str) -> Optional[str]:
    """若 ``text`` 含 ``a_chk_kakute_moji_su_init={"..."}`` 字面体，剥壳返回成对行体。"""
    m = _HSP_LITERAL_RE.search(text)
    if not m:
        return None
    body = m.group(1)
    return body.replace("\\n", "\n").replace('\\"', '"')


def _extract_ini_section(text: str, section: str = "[AutoCheckDefine]") -> Optional[str]:
    """从 INI 文本提取指定段的成对行体；未找到段头返回 ``None``。"""
    in_section = False
    found = False
    out: List[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section:
                break
            if stripped == section:
                in_section = True
                found = True
            continue
        if in_section:
            out.append(raw)
    return "\n".join(out) if found else None


def _detect_pairs(text: str) -> List[Tuple[str, str]]:
    """自动识别 RL 文本格式 → (word, readings) 列表。

    识别顺序：
      1. HSP 字面体（KAKUTE_MOJI_INIT.hsp）→ 成对行
      2. INI ``[AutoCheckDefine]`` 段 → 成对行
      3. 含 tab → tab 行
      4. 否则 → 成对行（兜底）
    """
    body = _extract_hsp_literal(text)
    if body is not None:
        return list(_iter_pairlines(body))
    body = _extract_ini_section(text)
    if body is not None:
        return list(_iter_pairlines(body))
    # 启发：任意行含 \t 即视作 tab 行体（与 NetKakuteiYomi.txt / 老 RL .txt 兼容）
    if any("\t" in line for line in text.splitlines()):
        return list(_iter_tab_pairs(text))
    return list(_iter_pairlines(text))


def parse_rl_dictionary(text: str) -> List[Dict[str, object]]:
    """解析 RL 字典文本为新 annotated 格式条目列表。

    自动识别多种 RL 文本格式：
      * tab 行：``word\\treadings``（旧 RL 导出 / NetKakuteiYomi.txt 缓存）
      * 成对行：奇数行 word、偶数行 ``r1,r2,...@flag``（KAKUTE_MOJI_INIT.hsp 字面体内体 / .ini ``[AutoCheckDefine]`` 段）
      * HSP 字面体：``a_chk_kakute_moji_su_init={"..."}`` 自动剥壳
      * INI 段：自动定位 ``[AutoCheckDefine]`` 段

    Args:
        text: 原始文本内容（已解码为 str）。

    Returns:
        条目列表；每项包含 ``enabled`` (bool, 总为 True)、``word`` (str) 与
        ``reading`` (str，annotated 行内格式)。
        被丢弃的条目（含 ASCII 字母 / 读音全空 / Sudachi 解析无注音）不出现。
    """
    entries: List[Dict[str, object]] = []
    for word, raw_readings in _detect_pairs(text):
        # 仅修剪末尾的纯空 piece（含解析后会变空的 ``＋`` 占位），其余 piece 完整保留
        # 让 _direct_convert 处理 ＋ / /N / 纯数字 等 RL 真语义。
        pieces = [p for p in raw_readings.split(",")]
        # 去尾部：strip+剥离 ＋ 后为空 → 视为占位
        while pieces:
            tail = pieces[-1].strip()
            if tail == "" or tail == _LINK_MARKER:
                pieces.pop()
            else:
                break
        old_reading = ",".join(pieces)
        if not old_reading.strip():
            continue

        new_reading = convert_legacy_reading(word, old_reading)
        if not new_reading:
            continue
        entries.append({"enabled": True, "word": word, "reading": new_reading})
    return entries


def read_rl_dictionary_file(path: str) -> str:
    """读取 RL 字典文件并自动选择编码（utf-8 优先，cp932 兜底）。

    Args:
        path: 文件路径（``.txt`` / ``.hsp`` / ``.ini`` 均可）。

    Returns:
        解码后的字符串文本，可直接喂入 :func:`parse_rl_dictionary`。
    """
    with open(path, "rb") as f:
        data = f.read()
    for enc in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")
