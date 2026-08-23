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

# 尾音静音判据（2026-08 用户决策口径）：相对分离人声整轨平均功率的
# 比例阈值——帧功率低于 ratio × 整轨平均功率视为静音，且需连续
# min_frames 帧才认定「进入静音」（吸收换气与持续音的瞬时低谷）。
# 用整轨平均功率作基准可以自适应不同素材的响度，不依赖绝对电平。
# 0.2 首版实测偏高（弱尾音被误判为静音截断），2026-08-15 调至 0.1。
TAIL_SILENCE_POWER_RATIO = 0.1
TAIL_SILENCE_MIN_FRAMES = 4


def apply_word_group_resplit(
    grouped: List[Tuple[int, int]],
    groups: List[List[int]],
    word_groups: List[List[int]],
) -> List[Tuple[int, int]]:
    """拉丁词组词内边界按子 token 数比例切分（原地，返回 grouped）。

    手工按音节/字母拆分的英文单位：整词字母序列的 CTC 端点（词首/词尾）
    可靠，词内边界不可靠（英文字母≠音素，Viterbi 常把切点落在元音中间
    或拖到下一词）。词内改为按各成员子 token 数加权均分，边界单调稳定。
    """
    for indexes in word_groups:
        if len(indexes) < 2:
            continue
        if any(not (0 <= i < len(grouped)) for i in indexes):
            continue
        span_start = grouped[indexes[0]][0]
        span_end = grouped[indexes[-1]][1]
        if span_end <= span_start:
            continue
        weights = [
            len(groups[i]) if 0 <= i < len(groups) and groups[i] else 1
            for i in indexes
        ]
        total = sum(weights)
        boundaries = [span_start]
        acc = 0
        for w in weights[:-1]:
            acc += w
            boundaries.append(
                span_start + int(round((span_end - span_start) * acc / total))
            )
        boundaries.append(span_end)
        for pos, i in enumerate(indexes):
            grouped[i] = (boundaries[pos], boundaries[pos + 1])
    return grouped


def _silence_boundary(
    energies: Any, mean_power: float, from_frame: int, to_frame: int
) -> int:
    """从 from_frame 向后找第一段持续静音的起始帧；未找到返回 to_frame。"""
    threshold = TAIL_SILENCE_POWER_RATIO * mean_power
    run = 0
    for f in range(max(0, from_frame), to_frame):
        if float(energies[f]) < threshold:
            run += 1
            if run >= TAIL_SILENCE_MIN_FRAMES:
                return f - TAIL_SILENCE_MIN_FRAMES + 1
        else:
            run = 0
    return to_frame


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


def _find_encoder_layers(model: Any) -> List[Any]:
    """定位 transformer encoder 层列表（HF / torchaudio 各版本结构兼容）。

    - HF ``Wav2Vec2ForCTC``: ``wav2vec2.encoder.layers``
    - torchaudio 新版（MMS_FA 包装器）: ``model.encoder.transformer.layers``
    - torchaudio 旧版: ``encoder.layers``

    按 ``named_modules`` 扫描名字以 ``layers`` 结尾、路径含 encoder/
    transformer 的层容器，取最长者（transformer 块数最多、主导耗时）；
    找不到（结构变化/新版库）时返回空列表，调用方退化为无层进度。
    """
    best: List[Any] = []
    walker = getattr(model, "named_modules", None)
    if callable(walker):
        try:
            for name, mod in walker():
                if not name or not name.endswith("layers"):
                    continue
                if "encoder" not in name and "transformer" not in name:
                    continue
                try:
                    layers = list(mod)
                except TypeError:
                    continue
                if layers and len(layers) > len(best):
                    best = layers
        except Exception:
            pass
    if best:
        return best
    # 兜底：旧式显式属性路径（named_modules 缺失/异常的普通对象也可用）
    seen = set()
    for cand in (getattr(model, "wav2vec2", None), model):
        if cand is None or id(cand) in seen:
            continue
        seen.add(id(cand))
        encoder = getattr(cand, "encoder", None)
        layers = getattr(encoder, "layers", None)
        if layers is None or not hasattr(layers, "__len__"):
            continue
        try:
            return list(layers)
        except TypeError:
            continue
    return []


def resolve_device_pref(
    preference: str, cuda_available: bool, mps_available: bool
) -> str:
    """设备选择：auto 优先 CUDA → MPS → CPU；显式设备不可用时回退 CPU
    的判定由调用方给提示。纯函数便于单测（macOS 支持引入 MPS）。"""
    if preference == "auto":
        if cuda_available:
            return "cuda"
        if mps_available:
            return "mps"
        return "cpu"
    return preference


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

    def _select_device(self, torch, preference: str, progress) -> Any:
        """按偏好选设备：auto = CUDA → MPS（Apple 芯片）→ CPU；
        显式 CUDA/MPS 不可用时回退 CPU 并给出提示而非崩溃。"""
        cuda = bool(torch.cuda.is_available())
        mps = False
        try:
            mps = bool(torch.backends.mps.is_available())
        except Exception:
            mps = False
        device = resolve_device_pref(preference, cuda, mps)
        if device in ("cuda", "mps") and not (cuda if device == "cuda" else mps):
            progress(18, f"未检测到可用的 {device.upper()}，回退 CPU 推理")
            device = "cpu"
        try:
            from strange_uta_game.backend.application.ai_timing.ailog import (
                ailog,
            )

            gpu_desc = (
                torch.cuda.get_device_name(0) if cuda and device == "cuda" else ""
            )
            ailog(
                "worker",
                f"设备选择：pref={preference} cuda={cuda} mps={mps} → {device}"
                + (f"（{gpu_desc}）" if gpu_desc else ""),
            )
        except Exception:
            pass
        return torch.device(device)

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
        # torchaudio 的 forced_align 在 CUDA emission 下要求 targets
        # 也在 CUDA（compute.cu:256 "targets must be a CUDA tensor"），
        # 设备组合的兼容矩阵难以逐一验证；CTC 对齐耗时相对 transformer
        # forward 可忽略，统一回 CPU 做，保证与 CPU 路径逐位一致
        if hasattr(emission, "cpu"):
            emission = emission.cpu()
        from torchaudio.functional import forced_align, merge_tokens

        torch = self._import_torch()
        flat = [t for group in token_groups for t in group]
        if not flat:
            raise AlignmentProviderError("没有可对齐的 token")
        # 新版 torchaudio 的 forced_align API：targets 必须是 [B, N] Tensor，
        # 且要求 input_lengths/target_lengths（yohane 基于旧版 list/None 签名）
        targets = torch.tensor([flat], dtype=torch.int64)
        alignments, scores = forced_align(
            emission,
            targets,
            input_lengths=torch.tensor([emission.size(1)]),
            target_lengths=torch.tensor([len(flat)]),
            blank=blank,
        )
        # align 返回 [B, T]；merge_tokens 接受一维
        spans = merge_tokens(
            alignments[0] if alignments.dim() > 1 else alignments,
            scores[0] if scores.dim() > 1 else scores,
            blank=blank,
        )
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

    def _frame_energies(self, waveform: Any, num_frames: int) -> Any:
        """按 emission 帧计算平均功率（numpy 数组）；不可用时返回 None。

        返回 None 时尾音退回纯「吸附下一起点」行为（与旧版一致）。
        """
        try:
            import numpy as np

            wav = waveform.detach().cpu().numpy().reshape(-1)
        except Exception:
            return None
        n = int(wav.shape[0])
        if num_frames <= 0 or n <= num_frames:
            return None
        bounds = np.minimum(
            (np.arange(num_frames + 1) * (n / num_frames)).astype(np.int64), n
        )
        counts = np.diff(bounds)
        sums = np.add.reduceat(wav * wav, bounds[:-1])
        return sums / np.maximum(counts, 1)

    def _frames_to_spans(
        self,
        request: AlignmentRequest,
        groups: List[Tuple[int, int]],
        num_frames: int,
        num_samples: int,
        sample_rate: int,
        tail_snap: bool,
        waveform: Any = None,
    ) -> List[EmissionSpan]:
        """帧区间 → 毫秒 EmissionSpan；空组用相邻 token 插值补齐。

        尾音修正（tail_snap）：先吸附到下一 token 起点（弥补 CTC 对
        长音/尾音的截断，FA-Kara 思路），再按整轨平均功率的比例判据
        裁到静音边界——否则行尾尾音会一路延伸跨过整段间奏静音；
        末个 token 也借此把被 CTC 截断的真实尾音延伸到静音边界。
        """
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
            energies = (
                self._frame_energies(waveform, num_frames)
                if waveform is not None
                else None
            )
            if energies is not None:
                import numpy as np

                # 静音判据基线取能量上四分位（≈典型有声帧电平），而非
                # 全轨均值：人声轨大部分帧是句间静音/分离残留，全轨均值
                # 被拉到极低，0.1×均值的阈值形同虚设——残留 0.04~0.1×
                # 有声电平的帧永远高于阈值，静音永远“找不到”，句尾/
                # 停顿符前的字几乎总是一路延续到下一字起点
                mean_power = float(np.percentile(energies, 75))
            else:
                mean_power = 0.0
            audio_end_ms = int(round(num_frames * ratio))
            for i, cur in enumerate(spans):
                cand = (
                    spans[i + 1].start_ms if i + 1 < len(spans) else audio_end_ms
                )
                if cur.end_ms >= cand:
                    continue
                if energies is not None:
                    raw_end_f = min(num_frames - 1, int(cur.end_ms / ratio))
                    cand_f = min(num_frames, int(cand / ratio))
                    boundary_f = _silence_boundary(
                        energies, mean_power, raw_end_f, cand_f
                    )
                    new_end = int(round(boundary_f * ratio))
                    # 不短于 CTC 原始终点，不超过下一 token 起点/音频末尾
                    new_end = max(cur.end_ms, min(new_end, cand))
                    if new_end <= cur.start_ms:
                        continue
                else:
                    new_end = cand
                spans[i] = EmissionSpan(
                    token_index=cur.token_index,
                    start_ms=cur.start_ms,
                    end_ms=new_end,
                    score=cur.score,
                )
        return spans

    def _forward_with_layer_progress(
        self,
        run_forward: Callable[[], Any],
        progress: ProgressFn,
        lo: int = 66,
        hi: int = 84,
    ) -> Any:
        """执行一次模型 forward，按 encoder 层上报真实推理进度。

        通过 ``register_forward_hook`` 观察每层完成时刻——不改变任何
        计算与输出（与 FA-Kara/yohane 的一次整段 forward 完全一致），
        但把最耗时的推理阶段拆成 N 层的细粒度进度，ETA 有真实数据
        可算（各层耗时近似均匀，进度与时间近似线性）。

        结构上找不到层列表时静默退化为无层进度（只保留阶段消息）。
        """
        layers = _find_encoder_layers(self._model)
        n = len(layers)
        if not n:
            return run_forward()
        handles: List[Any] = []
        counter = {"done": 0}

        def _make_hook():
            def _hook(_module, _inputs, _output):
                counter["done"] += 1
                done = counter["done"]
                progress(
                    lo + int((hi - lo) * done / n),
                    f"模型推理中（编码器层 {done}/{n}）",
                )

            return _hook

        try:
            for layer in layers:
                handles.append(layer.register_forward_hook(_make_hook()))
            return run_forward()
        finally:
            for handle in handles:
                handle.remove()

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
        progress(12, "加载 PyTorch…（首次可能需要 1-3 分钟）")
        torch = self._import_torch()
        progress(16, "加载 Transformers…（首次可能需要 1-3 分钟）")
        try:
            from transformers import Wav2Vec2ForCTC, Wav2Vec2Processor
        except ImportError as exc:
            raise AlignmentProviderError(
                "对齐运行环境未安装 Transformers，请先下载对齐运行环境"
            ) from exc
        self._model_id = str(model_spec.get("model_id") or DEFAULT_WAV2VEC2_MODEL)
        device = str(model_spec.get("device") or "auto")
        self._device = self._select_device(torch, device, progress)
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

        def _run_forward():
            outputs = self._model(**inputs.to(self._device))
            return torch.nn.functional.log_softmax(outputs.logits, dim=-1)

        with torch.inference_mode():
            emission = self._forward_with_layer_progress(_run_forward, progress)

        progress(85, "计算对齐区间")
        blank = self._model.config.pad_token_id
        grouped = self._ctc_align_groups(emission, groups, blank=blank)
        apply_word_group_resplit(grouped, groups, request.word_groups)
        spans = self._frames_to_spans(
            request,
            grouped,
            num_frames=emission.size(1),
            num_samples=waveform.size(1),
            sample_rate=sample_rate,
            tail_snap=bool((request.options or {}).get("tail_snap", True)),
            waveform=waveform,
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
        self._device = self._select_device(torch, device, progress)
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

        def _run_forward():
            out, _ = self._model(waveform.to(self._device))
            return out

        with torch.inference_mode():
            emission = self._forward_with_layer_progress(_run_forward, progress)

        progress(85, "计算对齐区间")
        non_empty = [g for g in groups if g]
        # bundle aligner 内部同样要求 emission/targets 同设备：统一 CPU
        token_spans = self._aligner(emission[0].cpu() if hasattr(emission, "cpu") else emission[0], non_empty)
        grouped: List[Tuple[int, int]] = []
        span_iter = iter(token_spans)
        for g in groups:
            if not g:
                grouped.append((-1, -1))
                continue
            spans = next(span_iter)
            grouped.append((spans[0].start, spans[-1].end))
        apply_word_group_resplit(grouped, groups, request.word_groups)
        spans = self._frames_to_spans(
            request,
            grouped,
            num_frames=emission.size(1),
            num_samples=waveform.size(1),
            sample_rate=sample_rate,
            tail_snap=bool((request.options or {}).get("tail_snap", True)),
            waveform=waveform,
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
