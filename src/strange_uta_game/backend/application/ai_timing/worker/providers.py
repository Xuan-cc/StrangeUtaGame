"""forced-alignment provider 实现（阶段 C）。

Provider 抽象对应计划文档 §5.2 的 ForcedAlignmentProvider 接口；
推理实现吸收 yohane（Wav2Vec2 CTC + torchaudio forced_align）与
torchaudio MMS_FA bundle 的公开路径，不引入其 CLI 或私有 API。

重型依赖（torch / torchaudio / transformers / soundfile）全部在 load()
内部延迟导入：宿主与测试环境未安装 Runtime 时模块本身可正常导入，
错误在执行时转换为中文提示。
"""

import gc
import re
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Sequence, Tuple

from strange_uta_game.backend.application.ai_timing.alignment import (
    AlignmentRequest,
    AlignmentResult,
    EmissionSpan,
)

ProgressFn = Callable[[int, str], None]
CancelFn = Callable[[], bool]

DEFAULT_WAV2VEC2_MODEL = "NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn"


def normalize_latn_text(text: str) -> str:
    """把 token 文本归一化为对齐器可接受的 Latn 转写（同 yohane 口径）。

    小写、弯撇号转直撇号，其余非 [a-z' ] 字符替换为空格并折叠。
    """
    text = text.lower().replace("’", "'")
    text = re.sub("([^a-z' ])", " ", text)
    text = re.sub(" +", " ", text)
    return text.strip()


class AlignmentProviderError(RuntimeError):
    """provider 执行错误（中文消息）。"""


class AlignmentCancelledError(AlignmentProviderError):
    """协作取消信号（worker 转换为 cancelled 消息）。"""


class ForcedAlignmentProvider(ABC):
    """对齐器 provider 抽象（§5.2）。"""

    provider_id: str = ""

    @abstractmethod
    def validate_model(self, model_spec: Dict[str, Any]) -> None:
        """校验模型描述可用（缺文件/缺依赖时抛中文错误）。"""

    @abstractmethod
    def load(
        self,
        model_spec: Dict[str, Any],
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> None:
        """加载模型到内存（任务内只加载一次）。"""

    @abstractmethod
    def align(
        self,
        request: AlignmentRequest,
        audio_path: str,
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> AlignmentResult:
        """对音频执行 forced alignment，返回 AlignmentResult。"""

    def unload(self) -> None:
        """释放模型与设备资源（默认清引用 + 回收）。"""
        gc.collect()


def _cancelled_check(cancel: CancelFn) -> None:
    if cancel():
        raise AlignmentCancelledError("已取消")


class FakeProvider(ForcedAlignmentProvider):
    """确定性假 provider：进程生命周期 / 协议 / 取消测试用。

    均分 token 区间（duration_ms / token 数），支持 options 控制：
    - ``fake_duration_ms``：音频总时长（默认 1000）；
    - ``fake_delay_ms``：每个 token 之间的延迟（模拟长任务，取消测试用）；
    - ``fake_crash``：load 阶段抛出异常（崩溃隔离测试用）。
    """

    provider_id = "fake"

    def validate_model(self, model_spec: Dict[str, Any]) -> None:
        if model_spec.get("fake_crash"):
            # 崩溃测试走 load 阶段
            return

    def load(
        self,
        model_spec: Dict[str, Any],
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> None:
        progress(10, "加载测试模型")
        if model_spec.get("fake_crash"):
            raise AlignmentProviderError("测试崩溃：模拟模型加载失败")
        if model_spec.get("fake_crash_process"):
            import os

            os._exit(3)
        progress(50, "测试模型就绪")

    def align(
        self,
        request: AlignmentRequest,
        audio_path: str,
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> AlignmentResult:
        options = request.options or {}
        duration = int(options.get("fake_duration_ms", 1000))
        delay = float(options.get("fake_delay_ms", 0))
        tokens = request.tokens
        if not tokens:
            raise AlignmentProviderError("没有可对齐的 token")
        step = duration / len(tokens)
        spans: List[EmissionSpan] = []
        import time

        for i, token in enumerate(tokens):
            _cancelled_check(cancel)
            if delay:
                time.sleep(delay / 1000.0)
            spans.append(
                EmissionSpan(
                    token_index=token.index,
                    start_ms=int(round(i * step)),
                    end_ms=int(round((i + 1) * step)),
                    score=1.0,
                )
            )
            progress(
                50 + int(50 * (i + 1) / len(tokens)),
                f"对齐进度 {i + 1}/{len(tokens)}",
            )
        return AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id="fake",
            spans=spans,
        )


class _TorchProviderBase(ForcedAlignmentProvider):
    """torch 系 provider 共用逻辑：音频加载、CTC 对齐、帧→毫秒换算。"""

    def __init__(self) -> None:
        self._model: Any = None
        self._device: Any = None
        self._model_id: str = ""

    def _import_torch(self):
        try:
            import torch
        except ImportError as exc:
            raise AlignmentProviderError(
                "对齐运行环境未安装（缺少 PyTorch）。请先在 AI 打轴弹窗中下载对齐运行环境"
            ) from exc
        return torch

    def _load_audio_mono(self, audio_path: str) -> Tuple[Any, int]:
        """加载音频为 (torch tensor [1, samples], sample_rate)。"""
        torch = self._import_torch()
        try:
            import soundfile as sf
        except ImportError as exc:
            raise AlignmentProviderError(
                "对齐运行环境缺少 soundfile，无法读取音频"
            ) from exc
        try:
            import numpy as np

            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
        except Exception as exc:
            raise AlignmentProviderError(f"读取音频失败：{exc}") from exc
        waveform = torch.from_numpy(data.T)  # [channels, samples]
        if waveform.size(0) > 1:
            waveform = waveform.mean(dim=0, keepdim=True)
        return waveform, sample_rate

    def _ctc_align_groups(
        self, emission: Any, token_groups: Sequence[Sequence[int]], blank: int
    ) -> List[Tuple[int, int]]:
        """公开 API 路径的 CTC forced alignment。

        对展平的子 token 序列做 forced_align + merge_tokens，按分组
        聚合出每组的 (首子 token 起始帧, 末子 token 结束帧)。
        """
        from torchaudio.functional import forced_align, merge_tokens

        flat = [t for group in token_groups for t in group]
        if not flat:
            raise AlignmentProviderError("没有可对齐的 token")
        alignments, scores = forced_align(
            emission, flat, input_lengths=None, target_lengths=None
        )
        spans = merge_tokens(alignments, scores, blank=blank)
        grouped: List[Tuple[int, int]] = []
        offset = 0
        for group in token_groups:
            if not group:
                grouped.append((-1, -1))
                continue
            first = spans[offset]
            last = spans[offset + len(group) - 1]
            grouped.append((first.start, last.end))
            offset += len(group)
        return grouped

    def _frames_to_spans(
        self,
        request: AlignmentRequest,
        groups: List[Tuple[int, int]],
        num_frames: int,
        num_samples: int,
        sample_rate: int,
        tail_snap: bool,
    ) -> List[EmissionSpan]:
        """帧区间 → 毫秒 EmissionSpan；空组用相邻 token 插值补齐。"""
        ratio = (num_samples / num_frames) / sample_rate * 1000.0  # ms / frame
        spans: List[EmissionSpan] = []
        valid = [(i, g) for i, g in enumerate(groups) if g[0] >= 0]
        for i, token in enumerate(request.tokens):
            match = next(((gi, g) for gi, g in valid if gi == i), None)
            if match is None:
                # 归一化后无子 token 的读音：用前一个有效 token 终点 /
                # 后一个有效 token 起点插值
                prev_end = None
                next_start = None
                for gi, g in valid:
                    if gi < i:
                        prev_end = int(round(g[1] * ratio))
                    elif next_start is None:
                        next_start = int(round(g[0] * ratio))
                start = prev_end if prev_end is not None else (next_start or 0)
                end = next_start if next_start is not None else start
                spans.append(
                    EmissionSpan(token_index=token.index, start_ms=start, end_ms=end)
                )
                continue
            _, g = match
            spans.append(
                EmissionSpan(
                    token_index=token.index,
                    start_ms=int(round(g[0] * ratio)),
                    end_ms=int(round(g[1] * ratio)),
                )
            )
        if tail_snap:
            # 尾音修正：token 终点吸附到同需求序列的下一 token 起点，
            # 弥补 CTC 对长音/尾音的截断（FA-Kara 思路）；最后一个保持原值
            for i in range(len(spans) - 1):
                nxt = spans[i + 1]
                if spans[i].end_ms < nxt.start_ms:
                    spans[i] = EmissionSpan(
                        token_index=spans[i].token_index,
                        start_ms=spans[i].start_ms,
                        end_ms=nxt.start_ms,
                        score=spans[i].score,
                    )
        return spans

    def unload(self) -> None:
        self._model = None
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass


class Wav2Vec2LatnProvider(_TorchProviderBase):
    """微调 Wav2Vec2 CTC 模型（默认 NextFire/mms-300m-ForcedAligner-karaoke-ja-Latn）。

    输入为 Latn（罗马字/无调拼音）转写；每个 checkpoint 读音编码为一组
    子 token，CTC 对齐后聚合为该 checkpoint 的区间。
    """

    provider_id = "wav2vec2"

    def validate_model(self, model_spec: Dict[str, Any]) -> None:
        model_id = str(model_spec.get("model_id") or DEFAULT_WAV2VEC2_MODEL)
        if not model_id:
            raise AlignmentProviderError("未指定对齐模型")

    def load(
        self,
        model_spec: Dict[str, Any],
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> None:
        torch = self._import_torch()
        try:
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        except ImportError as exc:
            raise AlignmentProviderError(
                "对齐运行环境未安装 Transformers，请先下载对齐运行环境"
            ) from exc
        self._model_id = str(model_spec.get("model_id") or DEFAULT_WAV2VEC2_MODEL)
        device = str(model_spec.get("device") or "auto")
        self._device = torch.device(
            device if device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        progress(20, f"加载对齐模型 {self._model_id}")
        _cancelled_check(cancel)
        try:
            self._processor = Wav2Vec2Processor.from_pretrained(self._model_id)
            self._model = Wav2Vec2ForCTC.from_pretrained(self._model_id)
        except Exception as exc:
            raise AlignmentProviderError(f"对齐模型加载失败：{exc}") from exc
        self._model.to(self._device)
        progress(50, "对齐模型就绪")

    def align(
        self,
        request: AlignmentRequest,
        audio_path: str,
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> AlignmentResult:
        if self._model is None:
            raise AlignmentProviderError("对齐模型尚未加载")
        torch = self._import_torch()
        from torchaudio.functional import resample

        progress(55, "读取音频")
        waveform, sample_rate = self._load_audio_mono(audio_path)
        _cancelled_check(cancel)

        target_sr = int(self._processor.feature_extractor.sampling_rate)
        if sample_rate != target_sr:
            waveform = resample(waveform, sample_rate, target_sr)
            sample_rate = target_sr

        texts = [normalize_latn_text(t.text) for t in request.tokens]
        tokenizer = self._processor.tokenizer
        groups = [
            tokenizer.encode(text, add_special_tokens=False) if text else []
            for text in texts
        ]
        progress(65, "模型推理中")
        _cancelled_check(cancel)
        inputs = self._processor(
            audio=waveform.numpy(),
            sampling_rate=sample_rate,
            return_tensors="pt",
        )
        with torch.inference_mode():
            outputs = self._model(**inputs.to(self._device))
            emission = torch.nn.functional.log_softmax(outputs.logits, dim=-1)

        progress(85, "计算对齐区间")
        blank = self._model.config.pad_token_id
        grouped = self._ctc_align_groups(emission[0], groups, blank=blank)
        spans = self._frames_to_spans(
            request,
            grouped,
            num_frames=emission.size(1),
            num_samples=waveform.size(1),
            sample_rate=sample_rate,
            tail_snap=bool((request.options or {}).get("tail_snap", True)),
        )
        progress(100, "对齐完成")
        return AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id=self._model_id,
            spans=spans,
        )


class MmsFaProvider(_TorchProviderBase):
    """torchaudio MMS_FA 基础模型（备选，许可证限制较少）。"""

    provider_id = "mms_fa"

    def validate_model(self, model_spec: Dict[str, Any]) -> None:
        if not str(model_spec.get("model_id") or "MMS_FA"):
            raise AlignmentProviderError("未指定对齐模型")

    def load(
        self,
        model_spec: Dict[str, Any],
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> None:
        torch = self._import_torch()
        from torchaudio.pipelines import MMS_FA

        self._model_id = "MMS_FA"
        device = str(model_spec.get("device") or "auto")
        self._device = torch.device(
            device if device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        progress(20, "加载 MMS_FA 对齐模型")
        _cancelled_check(cancel)
        try:
            self._bundle = MMS_FA
            self._tokenizer = self._bundle.get_tokenizer()
            self._aligner = self._bundle.get_aligner()
            self._model = self._bundle.get_model()
        except Exception as exc:
            raise AlignmentProviderError(f"MMS_FA 模型加载失败：{exc}") from exc
        self._model.to(self._device)
        progress(50, "MMS_FA 模型就绪")

    def align(
        self,
        request: AlignmentRequest,
        audio_path: str,
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> AlignmentResult:
        if self._model is None:
            raise AlignmentProviderError("对齐模型尚未加载")
        torch = self._import_torch()
        from torchaudio.functional import resample

        progress(55, "读取音频")
        waveform, sample_rate = self._load_audio_mono(audio_path)
        _cancelled_check(cancel)

        target_sr = int(self._bundle.sample_rate)
        if sample_rate != target_sr:
            waveform = resample(waveform, sample_rate, target_sr)
            sample_rate = target_sr
        waveform = waveform.mean(0, keepdim=True)

        texts = [normalize_latn_text(t.text) for t in request.tokens]
        # MMS_FA tokenizer 按 uroman 转写整词序列；空文本产生空组，
        # 空组不参与 bundle aligner（其内部按词插入 <star>）
        encoded = self._tokenizer(texts)
        groups = [
            list(ids) if text else [] for text, ids in zip(texts, encoded)
        ]

        progress(65, "模型推理中")
        _cancelled_check(cancel)
        with torch.inference_mode():
            emission, _ = self._model(waveform.to(self._device))

        progress(85, "计算对齐区间")
        non_empty = [g for g in groups if g]
        token_spans = self._aligner(emission[0], non_empty)
        grouped: List[Tuple[int, int]] = []
        span_iter = iter(token_spans)
        for g in groups:
            if not g:
                grouped.append((-1, -1))
                continue
            spans = next(span_iter)
            grouped.append((spans[0].start, spans[-1].end))
        spans = self._frames_to_spans(
            request,
            grouped,
            num_frames=emission.size(1),
            num_samples=waveform.size(1),
            sample_rate=sample_rate,
            tail_snap=bool((request.options or {}).get("tail_snap", True)),
        )
        progress(100, "对齐完成")
        return AlignmentResult(
            annotation_digest=request.annotation_digest,
            model_id=self._model_id,
            spans=spans,
        )


_REGISTRY: Dict[str, type] = {
    FakeProvider.provider_id: FakeProvider,
    Wav2Vec2LatnProvider.provider_id: Wav2Vec2LatnProvider,
    MmsFaProvider.provider_id: MmsFaProvider,
}


def create_provider(model_spec: Dict[str, Any]) -> ForcedAlignmentProvider:
    """按 model_spec["provider"] 创建 provider；环境变量
    ``SUG_AITIMING_FAKE_PROVIDER=1`` 强制使用 fake（测试/离线诊断）。"""
    import os

    provider_id = str(model_spec.get("provider") or "wav2vec2")
    if os.environ.get("SUG_AITIMING_FAKE_PROVIDER") == "1":
        provider_id = "fake"
    cls = _REGISTRY.get(provider_id)
    if cls is None:
        raise AlignmentProviderError(
            f"未知的对齐 provider：{provider_id}（可用：{'、'.join(_REGISTRY)}）"
        )
    return cls()


__all__ = [
    "ForcedAlignmentProvider",
    "AlignmentProviderError",
    "AlignmentCancelledError",
    "FakeProvider",
    "Wav2Vec2LatnProvider",
    "MmsFaProvider",
    "create_provider",
    "normalize_latn_text",
    "DEFAULT_WAV2VEC2_MODEL",
]
