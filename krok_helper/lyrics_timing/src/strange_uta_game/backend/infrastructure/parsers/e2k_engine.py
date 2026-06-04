# -*- coding: utf-8 -*-
"""英语单词 → 片假名 规则引擎。

改编自 Morikatron Engineer Blog 的示例代码「英語をカタカナ表記に変換してみる」：
https://tech.morikatron.ai/entry/2020/05/25/100000
原始实现：https://github.com/morikatron/snippet/tree/master/english_to_kana

基于 CMU Pronouncing Dictionary (cmudict-0.7b, BSD-2-Clause) 的音素到片假名规则转换。

与原版差异：
- 所有注释翻译为中文
- baseform 转换（剥离重音、去重、变体重命名）改为 Python 内联实现，无需 Perl
- 路径解析兼容 PyInstaller 打包环境
- 单例模式，避免多次加载
- 增加 log 文件写入时的编码处理（utf-8）

对外 API:
    EnglishToKanaEngine.instance().convert(word: str) -> Optional[str]
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Dict, List, Optional


class EnglishToKanaEngine:
    """基于 CMU 字典的英语单词片假名转换引擎。

    构造时加载 cmudict-0.7b，执行 baseform 转换后为每个单词按音素规则生成片假名。
    转换结果缓存在 self.eng_kana_dic 中供 convert() 查询。
    """

    _instance: Optional["EnglishToKanaEngine"] = None

    def __init__(self) -> None:
        # === 元音音素 → 母音槽位 ===
        # 空字符串 '' 表示需要上下文推断的曖昧（模糊）母音
        self.vowels: Dict[str, str] = {
            "AA": "",  # 曖昧母音
            "AH": "",  # 曖昧母音
            "AE": "a",
            "AO": "o",
            "AW": "a",
            "AY": "a",
            "EH": "e",
            "ER": "a",
            "EY": "e",
            "IH": "i",
            "IY": "i",
            "OW": "o",
            "OY": "o",
            "UH": "u",
            "UW": "u",
        }

        # === 子音 + 母音槽位 → 片假名 ===
        # '' 槽位用于子音独立出现（无后续母音）的场景
        self.kana_dic: Dict[str, Dict[str, str]] = {
            "B": {"a": "バ", "i": "ビ", "u": "ブ", "e": "ベ", "o": "ボ", "": "ブ"},  # be    B IY
            "CH": {"a": "チャ", "i": "チ", "u": "チュ", "e": "チェ", "o": "チョ", "": "チ"},  # cheese CH IY Z
            "D": {"a": "ダ", "i": "ディ", "u": "ドゥ", "e": "デ", "o": "ド", "": "ド"},  # dee   D IY
            "DH": {"a": "ザ", "i": "ジ", "u": "ズ", "e": "ゼ", "o": "ゾ", "": "ズ"},  # thee  DH IY
            "F": {"a": "ファ", "i": "フィ", "u": "フ", "e": "フェ", "o": "フォ", "": "フ"},  # fee   F IY
            "G": {"a": "ガ", "i": "ギ", "u": "グ", "e": "ゲ", "o": "ゴ", "": "グ"},  # green G R IY N
            "HH": {"a": "ハ", "i": "ヒ", "u": "フ", "e": "ヘ", "o": "ホ", "": "フ"},  # he    HH IY
            "JH": {"a": "ジャ", "i": "ジ", "u": "ジュ", "e": "ジェ", "o": "ジョ", "": "ジ"},  # gee   JH IY
            "K": {"a": "カ", "i": "キ", "u": "ク", "e": "ケ", "o": "コ", "": "ク"},  # key   K IY
            "L": {"a": "ラ", "i": "リ", "u": "ル", "e": "レ", "o": "ロ", "": "ル"},  # lee   L IY
            "M": {"a": "マ", "i": "ミ", "u": "ム", "e": "メ", "o": "モ", "": "ム"},  # me    M IY
            "N": {"a": "ナ", "i": "ニ", "u": "ヌ", "e": "ネ", "o": "ノ", "": "ン"},  # knee  N IY
            "NG": {"a": "ンガ", "i": "ンギ", "u": "ング", "e": "ンゲ", "o": "ンゴ", "": "ング"},  # ping P IH NG
            "P": {"a": "パ", "i": "ピ", "u": "プ", "e": "ペ", "o": "ポ", "": "プ"},  # pee   P IY
            "R": {"a": "ラ", "i": "リ", "u": "ル", "e": "レ", "o": "ロ", "": "ー"},  # read  R IY D
            "S": {"a": "サ", "i": "シ", "u": "ス", "e": "セ", "o": "ソ", "": "ス"},  # sea   S IY
            "SH": {"a": "シャ", "i": "シ", "u": "シュ", "e": "シェ", "o": "ショ", "": "シュ"},  # she SH IY
            "T": {"a": "タ", "i": "ティ", "u": "チュ", "e": "テ", "o": "ト", "": "ト"},  # tea   T IY
            "TH": {"a": "サ", "i": "シ", "u": "シュ", "e": "セ", "o": "ソ", "": "ス"},  # theta TH EY T AH
            "V": {"a": "バ", "i": "ビ", "u": "ブ", "e": "ベ", "o": "ボ", "": "ブ"},  # vee   V IY
            "W": {"a": "ワ", "i": "ウィ", "u": "ウ", "e": "ウェ", "o": "ウォ", "": "ウ"},  # we    W IY
            "Y": {"a": "ア", "i": "", "u": "ュ", "e": "エ", "o": "ョ", "": "イ"},  # yield Y IY L D
            "BOS_Y": {"a": "ヤ", "i": "イ", "u": "ユ", "e": "イエ", "o": "ヨ", "": "イ"},  # 词首 Y
            "Z": {"a": "ザ", "i": "ジ", "u": "ズ", "e": "ゼ", "o": "ゾ", "": "ズ"},  # zee   Z IY
            "ZH": {"a": "ジャ", "i": "ジ", "u": "ジュ", "e": "ジェ", "o": "ジョ", "": "ジュ"},  # seizure S IY ZH ER
            "T_S": {"a": "ツァ", "i": "ツィ", "u": "ツ", "e": "ツェ", "o": "ツォ", "": "ツ"},  # T+S 连音
        }

        # 转换结果缓存： word(小写) → カタカナ
        self.eng_kana_dic: Dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # 单例与加载
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "EnglishToKanaEngine":
        """获取单例。首次访问时会加载并构建词典。"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load()
        return cls._instance

    @staticmethod
    def _resolve_cmudict_path() -> Optional[Path]:
        """解析 cmudict-0.7b 路径（兼容 PyInstaller 打包）。"""
        base = getattr(sys, "_MEIPASS", None)
        if base:
            p = Path(base) / "strange_uta_game" / "config" / "cmudict-0.7b"
            if p.exists():
                return p
        dev_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "config"
            / "cmudict-0.7b"
        )
        if dev_path.exists():
            return dev_path
        return None

    def _load(self) -> None:
        """加载 cmudict-0.7b，做 baseform 转换并构建 eng_kana_dic。"""
        if self._loaded:
            return
        path = self._resolve_cmudict_path()
        if path is None or not path.exists():
            self._loaded = True
            return
        try:
            with open(path, "r", encoding="latin-1", errors="ignore") as f:
                raw_lines = f.read().split("\n")
        except Exception as e:
            print(f"加载 cmudict 失败: {e}")
            self._loaded = True
            return

        # 将 cmudict-0.7b 原始内容转换为 baseform 形式：
        # 1) 剥离每个音素尾部的重音数字（AH1 → AH）
        # 2) 去重 —— 同一单词若去重音后发音一致，仅保留第一条
        # 3) 同一单词若有多个不同发音，按出现顺序重命名为 WORD(2), WORD(3) ...
        baseform_entries = self._build_baseform(raw_lines)

        for word, phonemes in baseform_entries:
            # 跳过非字母起始（符号、标点）
            if not word or not (0x41 <= ord(word[0]) <= 0x5A):
                continue
            # 发音变体（WORD(2) 等）跳过，仅用首选发音
            if "(" in word:
                continue
            word_lower = word.lower()
            yomi = self._phonemes_to_kana(word_lower, phonemes)
            if yomi:
                self.eng_kana_dic[word_lower] = yomi

        self._loaded = True

    @staticmethod
    def _build_baseform(raw_lines: List[str]) -> List[tuple]:
        """将 cmudict-0.7b 原始行转换为 baseform 形式（剥离重音 + 去重 + 变体重命名）。

        等价于原仓库中 make_baseform.pl 的功能：
        - 去掉每个音素末尾的重音数字（`\\d+$`）
        - 同一单词若去重音后的音素序列完全相同，只保留首次出现的那条
        - 若仍有多个不同发音，按顺序重命名为 WORD(2), WORD(3) ...

        Returns:
            [(word, phoneme_string), ...]
        """
        stress_re = re.compile(r"\d+$")

        # 原文件使用 ';;;' 作为注释前缀
        # 每行格式： WORD  <两个空格> P1 P2 P3 ...
        parsed: Dict[str, List[str]] = {}  # base_word → [stripped_phoneme_line, ...]
        order: List[str] = []

        for line in raw_lines:
            if not line or line.startswith(";;;"):
                continue
            # 分隔符：两个空格（0.7b 标准格式）
            if "  " in line:
                word, rest = line.split("  ", 1)
            else:
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                word, rest = parts[0], parts[1]

            # 提取 base word（去掉 (2), (3) 等变体后缀）
            m = re.match(r"^(.+?)\((\d+)\)$", word)
            if m:
                base = m.group(1)
            else:
                base = word
            if not base:
                continue

            # 剥离音素尾部的重音数字
            phonemes = rest.strip().split()
            stripped = [stress_re.sub("", p) for p in phonemes]
            stripped_key = " ".join(stripped)

            if base not in parsed:
                parsed[base] = []
                order.append(base)
            existing = parsed[base]
            # 去重：相同 stripped 发音跳过
            if stripped_key not in existing:
                existing.append(stripped_key)

        # 展开为 (word, phoneme_string) 列表，多发音时重命名
        out: List[tuple] = []
        for base in order:
            variants = parsed[base]
            for i, ph in enumerate(variants):
                name = base if i == 0 else f"{base}({i + 1})"
                out.append((name, ph))
        return out

    # ------------------------------------------------------------------
    # 核心转换：音素序列 → 片假名
    # ------------------------------------------------------------------

    def _phonemes_to_kana(self, word: str, phoneme_line: str) -> str:
        """将单个单词的音素序列转换为片假名读音。"""
        sound_list = phoneme_line.split(" ")
        if not sound_list:
            return ""
        yomi = ""

        # 头尾加入 BOS/EOS 哨兵，方便判断上下文
        sound_list = ["BOS"] + sound_list + ["EOS"]

        for i in range(1, len(sound_list) - 1):
            s = sound_list[i]
            s_prev = sound_list[i - 1]
            s_next = sound_list[i + 1]

            # 词首为 Y 时切换到 BOS_Y 映射
            if s_prev == "BOS" and s == "Y":
                s = sound_list[i] = "BOS_Y"

            if s in self.kana_dic and s_next not in self.vowels:
                # === 子音 (后接子音 / 词尾) ===
                if s_next in {"Y"}:
                    # 后接 Y 时取 イ 行的首字符（例：フィ → フ）
                    yomi += self.kana_dic[s]["i"][0]
                elif s == "D" and s_next == "Z":
                    # D 音吞掉，留给下一个 Z
                    continue
                elif s == "T" and s_next == "S":
                    # 合并为 T_S
                    sound_list[i + 1] = "T_S"
                    continue
                elif s == "NG" and s_next in {"K", "G"}:
                    # NG 后接 K/G 时取首字符：ング → ン
                    yomi += self.kana_dic[s][""][0]
                elif s_prev in {"EH", "EY", "IH", "IY"} and s == "R":
                    # 前接特定母音的 R 转为长音 アー
                    yomi += "アー"
                else:
                    yomi += self.kana_dic[s][""]
            elif s in self.vowels:
                # === 母音 ===
                # 确定母音落在哪一个 aiueo 槽位
                if s in {"AA", "AH"}:
                    # 曖昧母音：用词形推断
                    v = self._find_vowel(word, i - 1, len(sound_list) - 2)
                else:
                    v = self.vowels[s]

                if s_prev in self.kana_dic:
                    # (子音 → 母音)
                    yomi += self.kana_dic[s_prev][v]
                else:
                    # (母音 → 母音)：连续母音的变化规则
                    if s_prev in {"AY", "EY", "OY"} and s not in {"AA", "AH"}:
                        yomi += {"a": "ヤ", "i": "イ", "u": "ユ", "e": "エ", "o": "ヨ"}[v]
                    elif s_prev in {"AW", "UW"}:
                        yomi += {"a": "ワ", "i": "ウィ", "u": "ウ", "e": "ウェ", "o": "ウォ"}[v]
                    elif s_prev in {"ER"}:
                        yomi += {"a": "ラ", "i": "リ", "u": "ル", "e": "レ", "o": "ロ"}[v]
                    else:
                        yomi += {"a": "ア", "i": "イ", "u": "ウ", "e": "エ", "o": "オ"}[v]

                # Y 音母音化
                if s in {"AY", "EY", "OY"}:
                    yomi += "イ"
                # 后续为子音时，部分母音附加长音
                if s_next not in self.vowels:
                    if s in {"ER", "IY", "OW", "UW"}:
                        yomi += "ー"
                    elif s in {"AW"}:
                        yomi += "ウ"

                # === 促音（ッ）规则 ===
                yomi = self._append_sokuon(yomi, s, s_prev, s_next, sound_list, i)

        return yomi

    def _append_sokuon(
        self,
        yomi: str,
        s: str,
        s_prev: str,
        s_next: str,
        sound_list: List[str],
        i: int,
    ) -> str:
        """根据母音前后文判断是否在尾部追加促音 ッ。规则改编自原脚本。"""
        # EH 且后接 T
        if s in {"EH"} and s_next in {"T"}:
            if s_prev in {"B"}:
                # 前接 B 时默认不加，但 T 为词尾的特殊情况除外
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"S"}:
                    yomi += "ッ"
            else:
                yomi += "ッ"

        # UH 且后接 K/D/T
        if (not yomi.endswith("ッ")) and s in {"UH"} and s_next in {"K", "D", "T"}:
            yomi += "ッ"

        # AE 规则簇
        if (not yomi.endswith("ッ")) and s in {"AE"}:
            if s_next in {"P"}:
                if s_prev in {"L", "HH"}:
                    yomi += "ッ"
                elif (
                    s_prev in {"N", "R"}
                    and i > 1
                    and sound_list[i - 2] in self.kana_dic
                ):
                    yomi += "ッ"
                elif s_prev in {"K", "T"} and i == len(sound_list) - 3:
                    yomi += "ッ"
            if s_next in {"D"}:
                # 优先规则：B R AE D 系列
                if s_prev in {"R"} and i > 1 and sound_list[i - 2] in {"B"}:
                    yomi += "ッ"
                elif s_prev in self.kana_dic:
                    if i == len(sound_list) - 3:
                        yomi += "ッ"
                    elif i == len(sound_list) - 4 and sound_list[i + 2] in {"Z"}:
                        yomi += "ッ"

        # AH 规则簇
        if (not yomi.endswith("ッ")) and s in {"AH"}:
            if s_prev in {"L"} and s_next in {"K"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"IY", "S"}:
                    yomi += "ッ"
            elif (
                i <= len(sound_list) - 5
                and s_next in {"TH"}
                and sound_list[i + 2] in {"IH"}
                and sound_list[i + 3] in {"NG"}
            ):
                yomi += "ッ"

        # SH-AA-K 默认加
        if (
            (not yomi.endswith("ッ"))
            and s_prev in {"SH"}
            and s in {"AA"}
            and s_next in {"K"}
        ):
            # 但 K 后接 AH/UW 时不加
            if not (
                i <= len(sound_list) - 4 and sound_list[i + 2] in {"AH", "UW"}
            ):
                yomi += "ッ"

        # L-IH-SH 且 SH 为词尾
        if (
            (not yomi.endswith("ッ"))
            and s_prev in {"L"}
            and s in {"IH"}
            and s_next in {"SH"}
            and i == len(sound_list) - 3
        ):
            yomi += "ッ"

        # {特定母音} + P (可能后接 S 或 IH-NG)
        if (not yomi.endswith("ッ")) and s in {"AA", "AH", "EH"}:
            if s_next in {"P"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"S"}:
                    yomi += "ッ"
                elif (
                    i <= len(sound_list) - 5
                    and sound_list[i + 2] in {"IH"}
                    and sound_list[i + 3] in {"NG"}
                ):
                    yomi += "ッ"

        # {特定母音} + T (可能后接 S)
        if (not yomi.endswith("ッ")) and s in {"IH", "AE", "AH", "AA"}:
            if s_next in {"T"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"S"}:
                    yomi += "ッ"

        # {特定母音} + K (可能后接 S 或 IH-NG)
        if (not yomi.endswith("ッ")) and s in {"IH", "AE"}:
            if s_next in {"K"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"S"}:
                    yomi += "ッ"
                elif (
                    i <= len(sound_list) - 5
                    and sound_list[i + 2] in {"IH"}
                    and sound_list[i + 3] in {"NG"}
                ):
                    yomi += "ッ"

        # AA + K (可能后接 T 或 IH-NG)
        if (not yomi.endswith("ッ")) and s in {"AA"}:
            if s_next in {"K"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"T"}:
                    yomi += "ッ"
                elif (
                    i <= len(sound_list) - 5
                    and sound_list[i + 2] in {"IH"}
                    and sound_list[i + 3] in {"NG"}
                ):
                    yomi += "ッ"

        # EH + D (可能后接 Z)
        if (not yomi.endswith("ッ")) and s in {"EH"}:
            if s_next in {"D"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"Z"}:
                    yomi += "ッ"

        # {AA, AH} + CH (可能后接 Z 或 IH-NG)
        if (not yomi.endswith("ッ")) and s in {"AA", "AH"}:
            if s_next in {"CH"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"Z"}:
                    yomi += "ッ"
                elif (
                    i <= len(sound_list) - 5
                    and sound_list[i + 2] in {"IH"}
                    and sound_list[i + 3] in {"NG"}
                ):
                    yomi += "ッ"

        # IH + JH (可能后接 D 或 IH-Z)
        if (not yomi.endswith("ッ")) and s in {"IH"}:
            if s_next in {"JH"}:
                if i == len(sound_list) - 3:
                    yomi += "ッ"
                elif i == len(sound_list) - 4 and sound_list[i + 2] in {"D"}:
                    yomi += "ッ"
                elif (
                    i == len(sound_list) - 5
                    and sound_list[i + 2] in {"IH"}
                    and sound_list[i + 3] in {"Z"}
                ):
                    yomi += "ッ"

        return yomi

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _find_vowel(text: str, pos: int, length: int) -> str:
        """曖昧母音（AA/AH）：从词形中找最近的字母母音，映射到 aiueo 槽位。"""
        if length <= 0:
            return "a"
        p = (pos + 0.5) / length
        text_len = len(text)
        distance_list: List[float] = []
        vowel_list: List[str] = []
        for i, ch in enumerate(text):
            if ch in {"a", "i", "u", "e", "o"}:
                vowel_list.append(ch)
                distance_list.append(abs(p - (i + 0.5) / text_len))
        if not distance_list:
            return "a"
        v = vowel_list[distance_list.index(min(distance_list))]
        # u 在曖昧母音场景下归并到 a（原脚本行为）
        if v == "u":
            v = "a"
        return v

    # ------------------------------------------------------------------
    # 公开查询接口
    # ------------------------------------------------------------------

    def convert(self, english: str) -> Optional[str]:
        """转换单个英语单词为片假名。未收录则返回 None。"""
        if not english:
            return None
        key = english.lower()
        return self.eng_kana_dic.get(key)

    def has(self) -> bool:
        """词典是否加载成功且非空。"""
        return bool(self.eng_kana_dic)
