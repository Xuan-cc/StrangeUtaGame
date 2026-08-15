"""AI 打轴原子写回命令（阶段 B）。

ApplyAiTimingCommand 把校验通过的 AlignmentResult 一次性应用到 Project：

- 覆盖全部时间戳（token 单元取对齐区间起点；结构单元按延音插值）；
- 清空句尾呼吸点时间戳（呼吸点属于旧时间轴，不由对齐器产出）；
- 缺口字符的 GENERATED 读音只用于对齐 transcript，不写回工程
  （2026-08 用户决策：AI 打轴只改时间轴，原 .sug 注音保持原样）；
- 一次撤销完整恢复执行前的时间戳与注音状态。

执行前校验（结果校验失败 / 工程标注漂移 / 缺口未清零时抛出异常），
任何失败都不产生部分应用。
"""

from copy import deepcopy
from typing import Dict, Optional

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentResult,
    UnitLocation,
    checkpoint_timestamps,
    interpolate_structural_timestamps,
    validate_result,
)
from strange_uta_game.backend.application.ai_timing.pronunciation import (
    PronunciationPlan,
    PronunciationSource,
    compute_annotation_digest,
)
from strange_uta_game.backend.application.ai_timing.resolver import ProjectDriftError
from strange_uta_game.backend.application.commands.base import Command
from strange_uta_game.backend.domain import Project, Ruby, RubyPart


class ApplyAiTimingCommand(Command):
    """把 AlignmentResult 原子应用到 Project，支持一次撤销/重做。

    execute() 在写回前做完整校验（结果 schema、标注摘要漂移、缺口清零），
    任一失败抛 AlignmentValidationError / ProjectDriftError 且不改动工程。
    """

    def __init__(
        self,
        project: Project,
        plan: PronunciationPlan,
        request: AlignmentRequest,
        result: AlignmentResult,
        description: str = "AI 打轴",
    ):
        self._project = project
        self._plan = plan
        self._request = request
        self._result = result
        self._description = description
        self._before_sentences = None
        self._after_sentences = None

    # ── Command 协议 ──

    def execute(self) -> None:
        self._validate_or_raise()
        if self._after_sentences is not None:
            # 重做路径：恢复 execute 后的快照
            self._project.sentences = deepcopy(self._after_sentences)
            self._project._update_timestamp()
            return
        self._before_sentences = deepcopy(self._project.sentences)
        self._apply()
        self._after_sentences = deepcopy(self._project.sentences)

    def undo(self) -> None:
        if self._before_sentences is None:
            return
        self._project.sentences = deepcopy(self._before_sentences)
        self._project._update_timestamp()

    def redo(self) -> None:
        self.execute()

    @property
    def description(self) -> str:
        return self._description

    # ── 校验 ──

    def _validate_or_raise(self) -> None:
        validate_result(self._result, self._request)
        if self._plan.pending_units:
            u = self._plan.pending_units[0]
            raise ProjectDriftError(
                f"第 {u.line_idx + 1} 行「{u.char_text}」仍缺少读音，"
                "不能应用 AI 打轴结果"
            )
        current_digest = compute_annotation_digest(self._project)
        if current_digest != self._plan.annotation_digest:
            raise ProjectDriftError(
                "工程标注在 AI 打轴执行后发生了变化，结果已失效"
            )

    # ── 写回 ──

    def _apply(self) -> None:
        span_map = checkpoint_timestamps(self._result, self._request)
        ts_map: Dict[UnitLocation, int] = interpolate_structural_timestamps(
            self._plan, self._request, span_map
        )

        for line_idx, sentence in enumerate(self._project.sentences):
            line_units = [
                u for u in self._plan.units_for_line(line_idx)
                if not u.is_sentence_end
            ]
            if not any(u.location in ts_map for u in line_units):
                # 行内无任何 token（纯结构行）：保留原轴
                continue
            for char_idx, ch in enumerate(sentence.characters):
                locations = [
                    (line_idx, char_idx, cp) for cp in range(ch.check_count)
                ]
                if any(loc in ts_map for loc in locations):
                    ch.timestamps = [
                        ts_map.get(loc, 0) for loc in locations
                    ]
                # 句尾释放点（尾音）：由对齐结果推导而非清空——
                # token 字符取其最后一个 token 的区间终点（尾音结束），
                # 无 token 字符回退到自身末个 checkpoint 的插值时间
                if ch.is_sentence_end:
                    derived = self._derive_sentence_end(
                        ch, line_idx, char_idx, line_units, span_map, ts_map
                    )
                    if derived is not None:
                        ch.sentence_end_ts = derived
                # 2026-08 用户决策：自动补出的读音只用于对齐 transcript，
                # 不写回工程 ruby（原 .sug 注音保持原样，只改时间轴）
                ch._update_offset_timestamps()
                ch.push_to_ruby()

    @staticmethod
    def _derive_sentence_end(
        ch,
        line_idx: int,
        char_idx: int,
        line_units: list,
        span_map,
        ts_map,
    ) -> Optional[int]:
        """推导句尾释放点时间：末 token 区间终点 > 末 checkpoint 插值 >
        行内末个 token 终点 > 保留原值（绝不返回 None 造成尾音丢失）。"""
        # 末个 token 的区间终点（尾音自然结束位置）
        best = None
        for u in line_units:
            if u.char_idx != char_idx or u.is_sentence_end:
                continue
            span = span_map.get(u.location)
            if span is not None:
                best = span[1] if best is None else max(best, span[1])
        if best is not None:
            return best
        # 无 token（结构字符）：末个 checkpoint 的插值时间
        if ch.timestamps:
            return max(ch.timestamps)
        # 句尾字符自身无节奏点（如「け!」的呼吸占位 0cp）：取行内最后
        # 一个 token 的区间终点——占位字符无法标注不该让尾音停留在
        # 陈旧原值上（2026-08 实测：否则新轴下释放点丢失）
        for u in reversed(line_units):
            span = span_map.get(u.location)
            if span is not None:
                return span[1]
        # 无任何可推导信号（check_count=0 且无 token）：返回 None，
        # 调用方保留原释放点，绝不置空
        return None

