"""AI 打轴原子写回命令（阶段 B）。

ApplyAiTimingCommand 把校验通过的 AlignmentResult 一次性应用到 Project：

- 覆盖全部时间戳（token 单元取对齐区间起点；结构单元按延音插值）；
- 清空句尾呼吸点时间戳（呼吸点属于旧时间轴，不由对齐器产出）；
- 为「整字补注音」的缺口字符写入 GENERATED Ruby（与时间戳同一次原子应用）；
- 一次撤销完整恢复执行前的时间戳与注音状态。

执行前校验（结果校验失败 / 工程标注漂移 / 缺口未清零时抛出异常），
任何失败都不产生部分应用。
"""

from copy import deepcopy
from typing import Dict

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
                # 句尾呼吸点：旧时间轴的释放点，一律清空
                ch.sentence_end_ts = None
                self._maybe_write_generated_ruby(
                    ch, line_idx, char_idx, line_units
                )
                ch._update_offset_timestamps()
                ch.push_to_ruby()

    def _maybe_write_generated_ruby(
        self, ch, line_idx: int, char_idx: int, line_units: list
    ) -> None:
        """为整字 GENERATED 的缺口字符写入注音（原子应用的一部分）。"""
        if ch.ruby is not None or ch.check_count <= 0:
            return
        units = [
            u for u in line_units
            if u.char_idx == char_idx and not u.is_sentence_end
        ]
        if not units:
            return
        if not all(
            u.source == PronunciationSource.GENERATED and u.reading for u in units
        ):
            return
        ch.set_ruby(
            Ruby(parts=[RubyPart(text=u.reading or "") for u in units])
        )


__all__ = ["ApplyAiTimingCommand"]
