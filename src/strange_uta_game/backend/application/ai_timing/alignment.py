"""AI 打轴版本化对齐请求/结果 schema 与 token span 映射（阶段 B）。

数据流：

    PronunciationPlan（阶段 A）
        ↓ build_alignment_request（读音 → Latn 域 token）
    AlignmentRequest ──(阶段 C worker 推理)──► AlignmentResult
        ↓ validate_result / checkpoint_timestamps
    ApplyAiTimingCommand（原子写回 + 一次撤销，见 commands.py）

本模块是纯数据与纯逻辑：不加载模型、不读音频、不触碰 Project 写路径，
测试用固定 emission 即可完成稳定写回与撤销验证。
"""

import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from strange_uta_game.backend.application.ai_timing.pronunciation import (
    PronunciationPlan,
)
from strange_uta_game.backend.infrastructure.parsers.romaji import (
    romanize_ruby_parts,
)

SCHEMA_VERSION = 1
"""AlignmentRequest / AlignmentResult 的协议版本。

破坏性变更（增删必填字段、语义变化）必须递增并在 worker 与宿主两侧
同时校验；兼容性新增字段不递增。
"""

UnitLocation = Tuple[int, int, int]
"""反向映射坐标 (line_idx, char_idx, checkpoint_idx)，与 PronunciationUnit.location 一致。"""


class AlignmentValidationError(ValueError):
    """对齐请求/结果未通过校验（消息为中文，可直接展示给用户）。"""


# ──────────────────────────────────────────────
# 媒体身份
# ──────────────────────────────────────────────


@dataclass(frozen=True)
class MediaIdentity:
    """源音频身份（缓存键组成部分；阶段 E 人声复用与缓存命中依据）。

    Attributes:
        source_path: 原始媒体路径（展示与人工核对用，不参与指纹）。
        content_sha256: 实际对齐音频内容摘要；空串表示未计算（测试/占位）。
        duration_ms: 音频时长（毫秒）。
    """

    source_path: str = ""
    content_sha256: str = ""
    duration_ms: int = 0


# ──────────────────────────────────────────────
# 请求
# ──────────────────────────────────────────────


@dataclass
class AlignmentToken:
    """一个模型 token：对齐器输出的最小 span 单位。

    text 为 Latn 域文本（假名已转罗马字、拼音已去声调）；raw_reading 保留
    原始读音用于人工核对与缓存诊断。location 是写回 Project 的反向映射。
    """

    index: int
    text: str
    raw_reading: str
    location: UnitLocation
    line_idx: int = 0
    """所在行索引（冗余存储，便于按行分组构建 transcript）。"""


@dataclass
class AlignmentRequest:
    """forced-alignment worker 的版本化输入。"""

    schema_version: int = SCHEMA_VERSION
    media: Optional[MediaIdentity] = None
    annotation_digest: str = ""
    tokens: List[AlignmentToken] = field(default_factory=list)
    options: Dict[str, object] = field(default_factory=dict)
    """执行选项（音频倍速、尾音修正等；阶段 C/D 定义具体键）。"""
    word_groups: List[List[int]] = field(default_factory=list)
    """拉丁词组：同一英文词被手工拆成的多个时间单位的 token 索引。

    词级端点（整词字母序列的 CTC 区间）可靠，词内音节边界不可靠——
    provider 按各成员子 token 数比例切分词区间（英文手工音节拆分的
    对齐质量修正，FA-Kara/yohane 均无此处理）。假名/汉字/拼音单位
    永不进入词组（逐字时序不受影响）。
    """


# ──────────────────────────────────────────────
# 结果
# ──────────────────────────────────────────────


@dataclass
class EmissionSpan:
    """单个 token 的对齐输出区间。"""

    token_index: int
    start_ms: int
    end_ms: int
    score: float = 1.0


@dataclass
class AlignmentResult:
    """forced-alignment worker 的版本化输出。"""

    schema_version: int = SCHEMA_VERSION
    annotation_digest: str = ""
    model_id: str = ""
    spans: List[EmissionSpan] = field(default_factory=list)

    def spans_by_token(self) -> Dict[int, EmissionSpan]:
        return {s.token_index: s for s in self.spans}


# ──────────────────────────────────────────────
# 请求构建：读音 → Latn token
# ──────────────────────────────────────────────


def _strip_diacritics(text: str) -> str:
    """去除组合变音符号（拼音声调 → 无调拼音），转小写。"""
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    return unicodedata.normalize("NFC", stripped).lower()


def _contains_kana(text: str) -> bool:
    return any(
        "\u3040" <= c <= "\u309f" or "\u30a0" <= c <= "\u30ff" for c in text
    )


def build_alignment_tokens(plan: PronunciationPlan) -> List[AlignmentToken]:
    """把 plan 中带 token 的单元转换为 Latn 域 token 序列（公共入口）。"""
    return _build_tokens_and_word_groups(plan)[0]


def _build_tokens_and_word_groups(
    plan: PronunciationPlan,
) -> Tuple[List[AlignmentToken], List[List[int]]]:
    """token 序列 + 拉丁词组。

    假名读音按行序列整体罗马字化（保证促音/拗音跨 part 组合正确），
    其余读音去声调转小写后原样使用。plan 存在缺口时抛出校验错误——
    「所有正文都能生成合法 token」是执行前置条件（§8.1）。

    拉丁词组：同一行内**位置相邻**且都是 LATIN token 单位（中间无空白/
    停顿/其他脚本单元）视为同一英文词——手工按音节/字母拆分的时间单
    位由此成组，供 provider 词内比例切分。CMU 音素口径命中的词逐音节
    独立对齐（FA-Kara 做法），不进词组。
    """
    pending = plan.pending_units
    if pending:
        u = pending[0]
        raise AlignmentValidationError(
            f"第 {u.line_idx + 1} 行第 {u.char_idx + 1} 个字符「{u.char_text}」"
            f"缺少读音，无法生成对齐 token"
        )

    from strange_uta_game.backend.application.ai_timing.pronunciation import (
        ScriptKind,
    )

    tokens: List[AlignmentToken] = []
    word_groups: List[List[int]] = []
    line_numbers = sorted({u.line_idx for u in plan.units})
    for line_idx in line_numbers:
        line_units = [
            u
            for u in plan.units_for_line(line_idx)
            if not u.is_sentence_end and u.reading
        ]
        tokenizable = [u for u in line_units if u.has_model_token()]
        if not tokenizable:
            continue
        # 假名按序列整体罗马字化；非假名单元在序列中占位透传
        readings = [u.reading or "" for u in line_units]
        if any(_contains_kana(r) for r in readings):
            # for_alignment：FA-Kara 对齐拼写（促音遇ち行写作 t、孤立
            # 促音回退 h），仅用于对齐 token，不影响显示/导出拼写
            romanized = romanize_ruby_parts(readings, for_alignment=True)
        else:
            romanized = readings
        rom_by_id = {id(u): romanized[i] for i, u in enumerate(line_units)}

        # 完整非停顿点单元序列（含无读音的结构单元）：分组判据需要看见
        # 空白/停顿等分隔符——它们 reading 为 None，会被 line_units 的
        # u.reading 过滤掉，只按 line_units 迭代会把不同词误并成组
        ordered_units = [
            u for u in plan.units_for_line(line_idx) if not u.is_sentence_end
        ]
        tokenizable_ids = {id(u) for u in tokenizable}
        current_run: List[int] = []
        prev_latin_pos = -2

        def _flush_run() -> None:
            if len(current_run) >= 2:
                word_groups.append(list(current_run))
            current_run.clear()

        def _unit_token_texts(u, rom: str) -> Tuple[List[str], bool]:
            """单元 → (对齐 token 文本列表, 是否独立对齐)。

            0-多个 token：多音节英文词拆音节。独立对齐的 token 不进
            word_groups（CTC 逐音节直接定位，FA-Kara 音素口径）；
            回退口径（e2k 按拍等）仍按词组比例切分。

            中文模式下的汉字拉丁读音按拼音→表音转写；拉丁词优先走
            CMU 音素→音节（transcription 模块），未收录回退 e2k/
            pyphen（模块内部静默降级）。
            """
            from strange_uta_game.backend.application.ai_timing.pronunciation import (
                ScriptKind,
            )
            from strange_uta_game.backend.application.ai_timing.transcription import (
                english_number_reading,
                english_word_phoneme_syllables,
                english_word_syllables,
                pinyin_to_phonetic,
            )

            # 数字 → 英文读法 → 罗马字：此前数字 token 文本原样透传，
            # worker 归一化后为空串，数字实际从未对齐过（仅相邻插值）
            if (
                u.script == ScriptKind.NUMBER
                and rom
                and not _contains_kana(rom)
                and rom.strip().replace(".", "").isdigit()
            ):
                reading = english_number_reading(rom.strip())
                if reading:
                    return [reading], False
            if (
                plan.chinese_mode
                and u.script == ScriptKind.KANJI
                and rom
                and not _contains_kana(rom)
            ):
                converted = pinyin_to_phonetic(rom)
                text = converted if converted else _strip_diacritics(rom)
                return [text], False
            if u.script == ScriptKind.LATIN and rom and not _contains_kana(rom):
                stripped = _strip_diacritics(rom)
                phoneme = english_word_phoneme_syllables(stripped)
                if phoneme is not None:
                    # 音素口径：逐音节独立对齐，不按词组比例切分
                    return phoneme, True
                syllables = english_word_syllables(stripped)
                if syllables:
                    return syllables, False
            text = _strip_diacritics(rom) if not _contains_kana(rom) else rom
            return [text], False

        def _append_unit_tokens(u) -> Tuple[List[int], bool]:
            """为单元生成 1..N 个 token（多音节英文词=多 token 同 location），
            返回 (token 索引列表, 是否独立对齐)。"""
            rom = rom_by_id.get(id(u), u.reading or "")
            texts, independent = _unit_token_texts(u, rom)
            indexes = []
            for text in texts:
                idx = len(tokens)
                tokens.append(
                    AlignmentToken(
                        index=idx,
                        text=text,
                        raw_reading=u.reading or "",
                        location=u.location,
                        line_idx=line_idx,
                    )
                )
                indexes.append(idx)
            return indexes, independent

        for pos, u in enumerate(ordered_units):
            is_latin_token = (
                u.script == ScriptKind.LATIN and id(u) in tokenizable_ids
            )
            if not is_latin_token:
                _flush_run()
                prev_latin_pos = -2
                if id(u) in tokenizable_ids:
                    _append_unit_tokens(u)
                continue
            new_indexes, independent = _append_unit_tokens(u)
            if independent:
                # 音素口径音节：独立对齐，打断相邻词组（不与比例切分混用）
                _flush_run()
                prev_latin_pos = -2
                continue
            if pos == prev_latin_pos + 1 and current_run:
                current_run.extend(new_indexes)
            else:
                _flush_run()
                current_run.extend(new_indexes)
            prev_latin_pos = pos
        _flush_run()
    return tokens, word_groups


def build_alignment_request(
    plan: PronunciationPlan,
    media: Optional[MediaIdentity] = None,
    options: Optional[Dict[str, object]] = None,
) -> AlignmentRequest:
    """从 PronunciationPlan 构建版本化对齐请求（含拉丁词组）。"""
    tokens, word_groups = _build_tokens_and_word_groups(plan)
    return AlignmentRequest(
        media=media,
        annotation_digest=plan.annotation_digest,
        tokens=tokens,
        options=dict(options or {}),
        word_groups=word_groups,
    )


# ──────────────────────────────────────────────
# 结果校验与时间戳映射
# ──────────────────────────────────────────────


def validate_result(result: AlignmentResult, request: AlignmentRequest) -> None:
    """完整校验结果，任一条件不满足抛 AlignmentValidationError（中文消息）。

    校验项（§8 阶段 B）：schema 版本、标注摘要一致、span 覆盖全部 token、
    token 索引合法且唯一、区间非负且 start ≤ end、按 token 顺序单调不减。
    """
    if result.schema_version != request.schema_version:
        raise AlignmentValidationError(
            f"对齐结果协议版本不匹配（请求 {request.schema_version}，"
            f"结果 {result.schema_version}）"
        )
    if result.annotation_digest != request.annotation_digest:
        raise AlignmentValidationError("对齐结果与请求的工程标注摘要不一致")
    if len(result.spans) != len(request.tokens):
        raise AlignmentValidationError(
            f"对齐结果覆盖不完整：期望 {len(request.tokens)} 个 token 的区间，"
            f"实际 {len(result.spans)} 个"
        )
    seen = set()
    for span in result.spans:
        if span.token_index in seen:
            raise AlignmentValidationError(
                f"token {span.token_index} 的对齐区间重复"
            )
        seen.add(span.token_index)
        if not 0 <= span.token_index < len(request.tokens):
            raise AlignmentValidationError(
                f"token 索引 {span.token_index} 超出范围"
            )
        if span.start_ms < 0 or span.end_ms < 0:
            raise AlignmentValidationError(
                f"token {span.token_index} 出现负时间戳"
            )
        if span.start_ms > span.end_ms:
            raise AlignmentValidationError(
                f"token {span.token_index} 的区间起点晚于终点"
            )
    prev_end = -1
    for token in request.tokens:
        span = next(s for s in result.spans if s.token_index == token.index)
        if span.start_ms < prev_end:
            raise AlignmentValidationError(
                f"token {token.index} 的起始时间早于前一 token，时间轴不单调"
            )
        prev_end = span.end_ms


def checkpoint_timestamps(
    result: AlignmentResult, request: AlignmentRequest
) -> Dict[UnitLocation, Tuple[int, int]]:
    """token 区间 → checkpoint (start_ms, end_ms) 映射（写回的唯一定位来源）。

    同一 location 可能对应多个 token（多音节英文词按音节拆分）：
    合并为（首个 token 起点, 末个 token 终点）。
    """
    spans = result.spans_by_token()
    mapping: Dict[UnitLocation, Tuple[int, int]] = {}
    for token in request.tokens:
        span = spans[token.index]
        existing = mapping.get(token.location)
        if existing is None:
            mapping[token.location] = (span.start_ms, span.end_ms)
        else:
            mapping[token.location] = (
                min(existing[0], span.start_ms),
                max(existing[1], span.end_ms),
            )
    return mapping


def interpolate_structural_timestamps(
    plan: PronunciationPlan,
    request: AlignmentRequest,
    span_map: Dict[UnitLocation, Tuple[int, int]],
) -> Dict[UnitLocation, int]:
    """为全部 checkpoint 产出确定性的时间戳（毫秒）。

    规则：
    - token 单元：取区间 start（checkpoint = 该读音的起唱时刻）；
    - 无 token 单元（标点/停顿符/结构字符）：延音语义，取位置上最近的
      前一 token 区间终点，晚于后一 token 起点时收敛到该起点（保证行内
      单调）；无前 token 时取后一 token 起点；行内无任何 token 时整行
      不产生时间戳（调用方跳过该行，保留原轴）。
    """
    tokens_by_loc = {t.location: t for t in request.tokens}
    out: Dict[UnitLocation, int] = {}

    for line_idx in sorted({u.line_idx for u in plan.units}):
        line_tokens = [t for t in request.tokens if t.line_idx == line_idx]
        if not line_tokens:
            continue
        line_units = [
            u for u in plan.units_for_line(line_idx) if not u.is_sentence_end
        ]
        for token in line_tokens:
            out[token.location] = span_map[token.location][0]

        for i, unit in enumerate(line_units):
            if unit.location in out:
                continue
            prev_end: Optional[int] = None
            for j in range(i - 1, -1, -1):
                loc = line_units[j].location
                if loc in tokens_by_loc:
                    prev_end = span_map[loc][1]
                    break
            next_start: Optional[int] = None
            for j in range(i + 1, len(line_units)):
                loc = line_units[j].location
                if loc in tokens_by_loc:
                    next_start = span_map[loc][0]
                    break
            if prev_end is not None:
                candidate = prev_end
            elif next_start is not None:
                candidate = next_start
            else:
                candidate = span_map[line_tokens[0].location][0]
            if next_start is not None and candidate > next_start:
                candidate = next_start
            out[unit.location] = max(0, candidate)
    return out


__all__ = [
    "SCHEMA_VERSION",
    "UnitLocation",
    "AlignmentValidationError",
    "MediaIdentity",
    "AlignmentToken",
    "AlignmentRequest",
    "EmissionSpan",
    "AlignmentResult",
    "build_alignment_tokens",
    "build_alignment_request",
    "validate_result",
    "checkpoint_timestamps",
    "interpolate_structural_timestamps",
]
