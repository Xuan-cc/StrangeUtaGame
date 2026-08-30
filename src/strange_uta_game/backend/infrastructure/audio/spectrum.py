"""声谱图 / BPM 检测 / 峰值层 DSP — 纯 numpy，无 Qt 依赖。

属于基础设施层（音频处理），由 frontend worker 包装后在线程中调用。
设计要点（实测数据见 docs/waveform-spectrum-upgrade-design.md）：
- 全文件 STFT ≈140ms/分钟音频，必须由调用方放后台线程
- 幅度谱量化为 uint8（固定 -128~0dB 量程），内存 ≈5.3MB/分钟
- 峰值层分块向量化 + LRU 预算，后台预热后 UI 零等待
- 块级 min/max envelope 保峰值（绝不用步长抽样）
"""

from __future__ import annotations

from typing import Callable, List, Optional

import numpy as np

# uint8 量化的 dB 量程：0 → 0dB（满幅），255 → DB_FLOOR。
DB_FLOOR = -128.0

# 金字塔最粗层的最小行数（再粗没有意义）。
_PYRAMID_MIN_ROWS = 256

# 完整金字塔（原始矩阵 + 各层 ≈ 2×矩阵）的内存预算；超过则只保留原始矩阵，
# 由渲染侧按需惰性构建单个所需层（长音频防膨胀，见 WaveformDisplay）。
PYRAMID_BUDGET_BYTES = 256 * 1024 * 1024

# 基础声谱矩阵（frames × bins uint8）的内存预算：worker 启动前预估，
# 超限自动逐级降低窗口重叠（93.75→87.5→75→50），最低档仍超则拒绝计算。
# 未设此门禁时 1 小时音频 @93.75% 重叠的基础矩阵约 1.18GiB，有 OOM 风险。
SPECTRUM_MATRIX_BUDGET_BYTES = 384 * 1024 * 1024


def frame_count(n_samples: int, fft_size: int, hop: int) -> int:
    """居中加窗（librosa ``center=True`` 口径）的 STFT 帧数：``1 + n // hop``。

    帧 k 的窗口中心对齐 ``k·hop``（覆盖 ±fft/2，首尾补零），因此任何
    样本——包括结尾不足一窗的音频——都会落在某个窗口的高权区，而不是
    恰好卡在 Hann 窗边缘被衰减到地板以下（floor 口径 + 边缘补零的
    非居中方案正是这个问题：fft=8192、50% 重叠时最多丢 ≈93ms 的尾部）。
    计算与预算估算必须共用本公式。
    """
    return 1 + max(0, n_samples) // max(1, hop)


def estimate_matrix_bytes(
    n_samples: int, sample_rate: int, fft_size: int, hop: int
) -> int:
    """预估基础声谱矩阵字节数（frames × bins × 1B uint8）。"""
    return frame_count(n_samples, fft_size, hop) * (fft_size // 2 + 1)


def pick_overlap_within_budget(
    n_samples: int,
    sample_rate: int,
    fft_size: int,
    preferred_overlap: float,
    budget_bytes: Optional[int] = None,
) -> Optional[float]:
    """按基础矩阵预算选择可用的最大重叠；都不行返回 None（应拒绝计算）。

    budget_bytes 运行时读取模块常量（默认参数在 def 时绑定，测试无法
    monkeypatch）。
    """
    if budget_bytes is None:
        budget_bytes = SPECTRUM_MATRIX_BUDGET_BYTES
    # 降级阶梯：从首选重叠往下逐档（含 96.875%/98.4375% 新档位），
    # 超出预算即降；全部超限返回 None（调用方拒绝计算）
    ladder = (0.984375, 0.96875, 0.9375, 0.875, 0.75, 0.5)
    divisors = {0.5: 2, 0.75: 4, 0.875: 8, 0.9375: 16, 0.96875: 32, 0.984375: 64}
    candidates = [preferred_overlap] + [o for o in ladder if o < preferred_overlap]
    seen = set()
    for overlap in candidates:
        if overlap in seen:
            continue
        seen.add(overlap)
        hop = max(1, fft_size // divisors.get(overlap, 4))
        if estimate_matrix_bytes(n_samples, sample_rate, fft_size, hop) <= budget_bytes:
            return overlap
    return None

# 声谱图默认频率轴参数。
LOG_SCALE_MIN_HZ = 30.0  # 对数刻度下限（低于此频率基本无音乐内容）

# 声谱强度色带（0→1）。inferno 是项目原有方案，必须保持默认与端点不变；
# 其余方案只改变渲染期 LUT，不参与 STFT，也不会使声谱矩阵失效。
_COLORMAP_STOPS = {
    "inferno": (
        (0.00, (0, 0, 4)),
        (0.13, (31, 12, 72)),
        (0.25, (85, 15, 109)),
        (0.38, (136, 34, 106)),
        (0.50, (186, 54, 85)),
        (0.63, (227, 89, 51)),
        (0.75, (249, 140, 10)),
        (0.88, (249, 201, 50)),
        (1.00, (252, 255, 164)),
    ),
    "magma": (
        (0.00, (0, 0, 4)), (0.13, (28, 16, 68)),
        (0.25, (79, 18, 123)), (0.38, (129, 37, 129)),
        (0.50, (181, 54, 122)), (0.63, (229, 80, 100)),
        (0.75, (251, 135, 97)), (0.88, (254, 194, 135)),
        (1.00, (252, 253, 191)),
    ),
    "viridis": (
        (0.00, (68, 1, 84)), (0.10, (72, 36, 117)),
        (0.20, (65, 68, 135)), (0.30, (53, 95, 141)),
        (0.40, (42, 120, 142)), (0.50, (33, 145, 140)),
        (0.60, (34, 168, 132)), (0.70, (68, 191, 112)),
        (0.80, (122, 209, 81)), (0.90, (189, 223, 38)),
        (1.00, (253, 231, 37)),
    ),
    "cividis": (
        (0.00, (0, 32, 77)), (0.10, (0, 42, 102)),
        (0.20, (37, 66, 107)), (0.30, (74, 85, 107)),
        (0.40, (107, 105, 112)), (0.50, (137, 124, 117)),
        (0.60, (165, 145, 116)), (0.70, (193, 166, 111)),
        (0.80, (221, 190, 99)), (0.90, (247, 216, 78)),
        (1.00, (255, 233, 69)),
    ),
    "blue_on_black": (
        (0.00, (0, 0, 0)), (0.18, (0, 7, 48)),
        (0.38, (0, 35, 122)), (0.58, (0, 112, 196)),
        (0.76, (0, 210, 236)), (0.90, (157, 245, 255)),
        (1.00, (255, 255, 255)),
    ),
    "grayscale": (
        (0.00, (0, 0, 0)), (0.25, (42, 42, 42)),
        (0.50, (105, 105, 105)), (0.75, (181, 181, 181)),
        (1.00, (255, 255, 255)),
    ),
    "turbo": (
        (0.00, (48, 18, 59)), (0.10, (67, 62, 133)),
        (0.20, (57, 117, 194)), (0.30, (27, 165, 204)),
        (0.40, (42, 202, 158)), (0.50, (122, 229, 86)),
        (0.60, (202, 224, 52)), (0.70, (250, 186, 56)),
        (0.80, (246, 120, 38)), (0.90, (210, 60, 30)),
        (1.00, (122, 4, 3)),
    ),
}

SPECTRUM_COLORMAPS = tuple(_COLORMAP_STOPS)

ProgressCb = Optional[Callable[[float], None]]
CancelCheck = Optional[Callable[[], bool]]


def _frame_block(samples: np.ndarray, start: int, length: int) -> np.ndarray:
    """原始坐标 ``[start, start+length)`` 的采样；越界部分零填充（局部缓冲）。

    与对整首输入 ``np.pad`` 后切片语义等价，但只在首尾越界的 chunk 上
    分配小块缓冲——中段 chunk 直接返回原数组的切片视图。np.pad 会整曲
    复制：10 分钟 mono ≈106MB、1 小时 ≈635MB，且发生在任何取消检查
    之前，已取消的任务也会先完成整次复制（P2-1）。
    """
    n = len(samples)
    if start >= 0 and start + length <= n:
        return samples[start : start + length]
    buf = np.zeros(length, dtype=samples.dtype)
    lo = max(start, 0)
    hi = min(start + length, n)
    if hi > lo:
        buf[lo - start : hi - start] = samples[lo:hi]
    return buf


def _iter_frame_chunks(
    samples: np.ndarray,
    fft: int,
    hop: int,
    chunk_frames: int,
    cancel_check: CancelCheck = None,
):
    """逐块产出 (i0, blk)；blk 是 (m, fft) 的帧窗口副本（未加窗）。

    居中加窗：帧 k 的窗口中心对齐原始信号的 ``k·hop``（覆盖
    ``[k·hop - fft/2, k·hop + fft/2)``）。首尾 chunk 的越界部分在局部
    缓冲中补零，尾部内容落在最后若干帧的高权区。每个 chunk 构建**前**
    先查取消——已取消的任务不做任何分配。
    """
    n_frames = frame_count(len(samples), fft, hop)
    front = fft // 2
    i0 = 0
    while i0 < n_frames:
        if cancel_check is not None and cancel_check():
            return
        m = min(chunk_frames, n_frames - i0)
        seg = _frame_block(samples, i0 * hop - front, (m - 1) * hop + fft)
        blk = np.lib.stride_tricks.as_strided(
            seg, shape=(m, fft), strides=(hop * seg.itemsize, seg.itemsize)
        ).copy()
        yield i0, blk
        i0 += m


def compute_spectrogram(
    samples: np.ndarray,
    sample_rate: int,
    fft_size: int,
    hop: Optional[int] = None,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
) -> Optional[dict]:
    """计算整段音频的 STFT 幅度谱并量化为 uint8 dB 矩阵。

    返回 dict：matrix=(n_frames, n_bins) uint8、fft_size、hop、sample_rate。
    cancel_check 返回 True 时中止并返回 None。
    """
    if sample_rate <= 0 or fft_size <= 0 or len(samples) < 1:
        return None
    # 预取消快速退出：切歌/隐藏/改参数的失效任务不得先分配整段结果矩阵
    # （预算上限 384MB，分配之后才发现取消就太晚了）。分配之后由
    # _iter_frame_chunks 的首块前检查兜底，缩小取消信号与分配间的竞态窗。
    if cancel_check is not None and cancel_check():
        return None
    hop = max(1, fft_size // 4 if hop is None else hop)
    # 居中加窗（帧 k 中心 = k·hop，首尾补零）：尾部内容不再丢弃，
    # 且内容显示在真实时间——旧的非居中口径会提前 fft/2 显示。
    n_frames = frame_count(len(samples), fft_size, hop)
    n_bins = fft_size // 2 + 1
    out = np.empty((n_frames, n_bins), dtype=np.uint8)
    window = np.hanning(fft_size).astype(np.float32)
    # 单边谱幅度归一化：加窗正弦的峰 bin 幅值 ≈ a·Σwindow/2，
    # 故 2·mag/Σwindow ≅ 信号幅度 a（满幅正弦 ≈ 0dB）。
    # 二倍补偿只适用于内部 bin——DC 与偶数 FFT 的 Nyquist 频点没有
    # 共轭 counterpart，统一乘 2 会多算 6.02dB。
    norm = float(window.sum())
    bin_scale = np.full(n_bins, 2.0, dtype=np.float32)
    bin_scale[0] = 1.0
    if fft_size % 2 == 0:
        bin_scale[-1] = 1.0
    scale = 255.0 / -DB_FLOOR
    # 每块 ≈30 秒音频（≈70ms 计算），保证取消响应及时。
    chunk_frames = max(1, int(30 * sample_rate / hop))

    for i0, blk in _iter_frame_chunks(
        samples, fft_size, hop, chunk_frames, cancel_check
    ):
        if cancel_check is not None and cancel_check():
            return None
        blk *= window
        mag = np.abs(np.fft.rfft(blk, axis=1))
        db = 20.0 * np.log10(mag * bin_scale / norm + 1e-9)
        out[i0 : i0 + blk.shape[0]] = (
            (db - DB_FLOOR) * scale
        ).clip(0, 255).astype(np.uint8)
        if progress_cb is not None:
            progress_cb(min(1.0, (i0 + blk.shape[0]) / n_frames))

    # 生成器在取消时会提前停止（不产出剩余 chunk）：半成品不能当结果
    if cancel_check is not None and cancel_check():
        return None
    return {
        "matrix": out,
        "fft_size": fft_size,
        "hop": hop,
        "sample_rate": sample_rate,
    }


def build_pyramid(matrix: np.ndarray) -> List[np.ndarray]:
    """帧数 2^k 分组取 max 的金字塔；levels[0] == matrix。

    层 l 的行 k 恒覆盖原始帧 [k*2^l, (k+1)*2^l)，与奇数尾部补行无关
    （尾部单独成组时其索引仍满足该对齐，见方案书）。
    """
    levels = [matrix]
    cur = matrix
    while cur.shape[0] > _PYRAMID_MIN_ROWS:
        n = cur.shape[0]
        m = n // 2
        nxt = np.maximum(cur[0 : 2 * m : 2], cur[1 : 2 * m : 2])
        if n % 2:
            nxt = np.concatenate([nxt, cur[-1:]], axis=0)
        levels.append(nxt)
        cur = nxt
    return levels


def coarse_pyramid_for_budget(
    matrix: np.ndarray, budget_bytes: int = PYRAMID_BUDGET_BYTES
) -> tuple:
    """超预算长音频的后台降级方案：构建一个受预算限制的中间层及其上层金字塔。

    返回 ``(mid, levels)``：``levels[0]`` 是第 ``mid`` 层，其后逐级减半到
    最粗。渲染侧约定：

    - 需要的层 ``level >= mid`` → 直接用 ``levels[level - mid]``（已后台备好）；
    - ``level < mid``（深缩放，可见帧数少）→ 用原始矩阵的**可见切片**归约，
      扫描量与缩放深度成正比、远小于全矩阵。

    因此 UI 绘制路径永远不需要同步构建完整层（paint/resize/scroll/zoom
    都不扫描整份矩阵）。
    """
    target = max(16 * 1024 * 1024, budget_bytes // 8)
    mid = 0
    rows = matrix.shape[0]
    while (matrix.nbytes >> mid) > target and (rows >> (mid + 1)) > 0:
        mid += 1
    mid_layer = build_level(matrix, mid)
    levels = [mid_layer]
    cur = mid_layer
    while cur.shape[0] > _PYRAMID_MIN_ROWS:
        n = cur.shape[0]
        m = n // 2
        nxt = np.maximum(cur[0 : 2 * m : 2], cur[1 : 2 * m : 2])
        if n % 2:
            nxt = np.vstack([nxt, cur[-1:]])
        levels.append(nxt)
        cur = nxt
    return mid, levels


def build_peak_levels_single_pass(
    samples: np.ndarray,
    cancel_check=None,
    progress_cb=None,
) -> Optional[dict]:
    """构建最细目标层的 min/max/sum_sq/count，再逐级向粗归约。

    - **向量化分块**：对齐部分 reshape 后一次 min/max/sum，只有块首/块尾
      两个不完整 bin 单独处理——绝不逐 bin 进入 Python 循环
      （逐 bin 循环实测 10s 音频 1.7s，向量化后 <10ms）。
    - **保留尾部**：最细层和粗层都保留不满 bin 的尾组（ceil），RMS 合并
      携带实际 count 保证权重正确。
    - **可取消**：每个样本块处理完后检查。
    """
    n = len(samples)
    bin_sizes = []
    b = 1
    while (n // b) > 256:
        bin_sizes.append(b)
        b *= 2
    if not bin_sizes:
        bin_sizes = [1]

    per_cap = 96 * 1024 * 1024 // 4
    finest = None
    for bs in bin_sizes:
        rows = (n + bs - 1) // bs
        if rows * 4 * 4 <= per_cap:
            finest = bs
            break
    if finest is None:
        return {}

    rows = (n + finest - 1) // finest
    mins = np.full(rows, np.inf, dtype=np.float32)
    maxs = np.full(rows, -np.inf, dtype=np.float32)
    sum_sq = np.zeros(rows, dtype=np.float64)
    counts = np.zeros(rows, dtype=np.int32)

    chunk = max(finest * 1024, 1 << 18)  # 至少覆盖 1024 个完整 bin
    for start_i in range(0, n, chunk):
        if cancel_check is not None and cancel_check():
            return None
        end_i = min(start_i + chunk, n)
        block = samples[start_i:end_i]
        block_sq = block.astype(np.float64) ** 2

        # 块内覆盖的 bin 范围
        b0 = start_i // finest

        # 对齐部分：从块内第一个完整 bin 起到块尾，reshape 归约
        aligned_start = b0 if start_i % finest == 0 else b0 + 1
        aligned_bin_end = (end_i // finest) if end_i % finest == 0 else end_i // finest
        # 实际对齐区：[aligned_start * finest, aligned_bin_end * finest)
        lo_aligned = max(start_i, aligned_start * finest)
        hi_aligned = min(end_i, aligned_bin_end * finest)
        if hi_aligned - lo_aligned >= finest:
            sub = block[lo_aligned - start_i : hi_aligned - start_i]
            sub_sq = block_sq[lo_aligned - start_i : hi_aligned - start_i]
            sub_rows = (hi_aligned - lo_aligned) // finest
            reshaped = sub[: sub_rows * finest].reshape(sub_rows, finest)
            reshaped_sq = sub_sq[: sub_rows * finest].reshape(sub_rows, finest)
            a0 = lo_aligned // finest
            np.minimum(mins[a0 : a0 + sub_rows], reshaped.min(axis=1), out=mins[a0 : a0 + sub_rows])
            np.maximum(maxs[a0 : a0 + sub_rows], reshaped.max(axis=1), out=maxs[a0 : a0 + sub_rows])
            sum_sq[a0 : a0 + sub_rows] += reshaped_sq.sum(axis=1)
            counts[a0 : a0 + sub_rows] += finest

        # 块首不完整 bin（仅在非对齐起点时）
        if start_i % finest != 0:
            seg_end = min(end_i, (b0 + 1) * finest)
            seg = block[: seg_end - start_i]
            seg_sq = block_sq[: seg_end - start_i]
            np.minimum(mins[b0], seg.min(), out=mins[b0 : b0 + 1])
            np.maximum(maxs[b0], seg.max(), out=maxs[b0 : b0 + 1])
            sum_sq[b0] += seg_sq.sum()
            counts[b0] += len(seg)

        # 块尾不完整 bin（非对齐终点；最后一块的 end_i == n 时也可能有尾组）
        if end_i % finest != 0:
            tail_start = max(start_i, aligned_bin_end * finest)
            if tail_start < end_i:
                seg = block[tail_start - start_i :]
                seg_sq = block_sq[tail_start - start_i :]
                bi = tail_start // finest
                np.minimum(mins[bi], seg.min(), out=mins[bi : bi + 1])
                np.maximum(maxs[bi], seg.max(), out=maxs[bi : bi + 1])
                sum_sq[bi] += seg_sq.sum()
                counts[bi] += len(seg)

        if progress_cb is not None:
            progress_cb(min(0.5, end_i / n))

    if cancel_check is not None and cancel_check():
        return None

    # 最细层 RMS
    valid = counts > 0
    finest_rmss = np.zeros(rows, dtype=np.float32)
    finest_rmss[valid] = np.sqrt(sum_sq[valid] / counts[valid])
    result = {finest: (mins, maxs, finest_rmss)}

    # 逐级向粗归约（全向量化，保留尾组）
    cur_bin = finest
    cur_mins, cur_maxs = mins, maxs
    cur_sum_sq, cur_counts = sum_sq, counts
    for bs in sorted(bs2 for bs2 in bin_sizes if bs2 > finest):
        factor = bs // cur_bin
        if factor < 2:
            continue
        src_rows = len(cur_mins)
        new_rows = (src_rows + factor - 1) // factor
        new_mins = np.full(new_rows, np.inf, dtype=np.float32)
        new_maxs = np.full(new_rows, -np.inf, dtype=np.float32)
        new_sum_sq = np.zeros(new_rows, dtype=np.float64)
        new_counts = np.zeros(new_rows, dtype=np.int32)
        full_groups = src_rows // factor
        if full_groups:
            nm = cur_mins[: full_groups * factor].reshape(full_groups, factor)
            nx = cur_maxs[: full_groups * factor].reshape(full_groups, factor)
            ns = cur_sum_sq[: full_groups * factor].reshape(full_groups, factor)
            nc = cur_counts[: full_groups * factor].reshape(full_groups, factor)
            new_mins[:full_groups] = np.min(nm, axis=1)
            new_maxs[:full_groups] = np.max(nx, axis=1)
            new_sum_sq[:full_groups] = ns.sum(axis=1)
            new_counts[:full_groups] = nc.sum(axis=1)
        tail_start = full_groups * factor
        if tail_start < src_rows:
            new_mins[full_groups] = cur_mins[tail_start:].min()
            new_maxs[full_groups] = cur_maxs[tail_start:].max()
            new_sum_sq[full_groups] = cur_sum_sq[tail_start:].sum()
            new_counts[full_groups] = cur_counts[tail_start:].sum()
        nv = new_counts > 0
        new_rmss = np.zeros(new_rows, dtype=np.float32)
        new_rmss[nv] = np.sqrt(new_sum_sq[nv] / new_counts[nv])
        result[bs] = (new_mins, new_maxs, new_rmss)
        cur_bin = bs
        cur_mins, cur_maxs = new_mins, new_maxs
        cur_sum_sq, cur_counts = new_sum_sq, new_counts
        if cancel_check is not None and cancel_check():
            return None
        if progress_cb is not None:
            progress_cb(min(1.0, 0.5 + 0.5 * (bs / bin_sizes[-1])))

    return result


def reduce_peaks_by_edges(
    samples: np.ndarray, edges: np.ndarray
) -> tuple:
    """按像素段边界归约 min/max/RMS（深放大直读路径）。

    edges 为长度 P+1 的单调不减整数数组：像素 i 归约 samples[edges[i]:edges[i+1])，
    空段（clamp 或 rounding 产生的重合边界）该像素返回 0/0/0。内部用
    unique 折叠重合边界后一次 reduceat，绝不逐像素进入 Python 循环。
    返回 (mins, maxs, rmss)，float32，长度 P。
    """
    edges = np.asarray(edges, dtype=np.int64)
    p = len(edges) - 1
    mins = np.zeros(p, dtype=np.float32)
    maxs = np.zeros(p, dtype=np.float32)
    rmss = np.zeros(p, dtype=np.float32)
    if p <= 0 or len(samples) == 0:
        return mins, maxs, rmss

    clipped = np.clip(edges, 0, len(samples))
    # 折叠重合边界：uniq[j]→uniq[j+1] 是互不重叠的归约段，非空段一起
    # reduceat；再经 lookup 把结果摊回原像素（left==right 的像素保持 0）
    uniq, seg_of_edge = np.unique(clipped, return_inverse=True)
    gap = np.diff(uniq)
    nonempty = gap > 0
    if not nonempty.any():
        return mins, maxs, rmss
    starts = uniq[:-1][nonempty]
    lens = gap[nonempty]

    # 关键：先切出可见跨度再归约。reduceat 的末段会延伸到数组末尾、
    # np.square 作用于整个入参数组——不切片会把 O(可见采样) 退化成
    # 每帧全曲扫描（3 分钟歌实测 ~280ms/帧，播放必卡）。
    s0 = int(starts[0])
    s1 = int(starts[-1] + lens[-1])
    seg = samples[s0:s1]
    inner = starts - s0
    seg_min = np.minimum.reduceat(seg, inner)
    seg_max = np.maximum.reduceat(seg, inner)
    seg_sumsq = np.add.reduceat(np.square(seg.astype(np.float32)), inner)

    lookup = -np.ones(len(uniq), dtype=np.int64)
    lookup[np.nonzero(nonempty)[0]] = np.arange(len(starts))
    pix_left = seg_of_edge[:-1]
    pix_right = seg_of_edge[1:]
    seg_id = lookup[pix_left]
    ok = (pix_right > pix_left) & (seg_id >= 0)
    mins[ok] = seg_min[seg_id[ok]]
    maxs[ok] = seg_max[seg_id[ok]]
    rmss[ok] = np.sqrt(seg_sumsq[seg_id[ok]] / lens[seg_id[ok]])
    return mins, maxs, rmss


def oversample_windowed_sinc(
    samples: np.ndarray, positions: np.ndarray, half_width: int = 16
) -> np.ndarray:
    """任意分数采样位置的窗化 sinc 插值（SV WaveformOversampler 口径）。

    Blackman 窗 sinc，半宽 half_width 个输入采样；信号边界外按 0 参与
    求和且不归一化——边缘幅度自然衰减，与"界外信号真实为 0"一致。
    positions 的单位是采样坐标（1.0 = 一个采样间隔）。向量化实现：
    width×2·half_width 的 tap 矩阵一次乘加。
    """
    samples = np.asarray(samples, dtype=np.float32)
    positions = np.asarray(positions, dtype=np.float64)
    n = len(samples)
    if n == 0 or len(positions) == 0:
        return np.zeros(len(positions), dtype=np.float32)

    base = np.floor(positions).astype(np.int64)
    frac = positions - base
    offs = np.arange(-half_width + 1, half_width + 1, dtype=np.int64)
    idx = base[:, None] + offs[None, :]
    inside = (idx >= 0) & (idx < n)
    t = frac[:, None] - offs[None, :]
    x = t / half_width
    # Blackman 窗：0.42 + 0.5cos(πx) + 0.08cos(2πx)，|x|≥1 权重为 0
    w = np.sinc(t) * (
        0.42 + 0.5 * np.cos(np.pi * x) + 0.08 * np.cos(2.0 * np.pi * x)
    )
    w = np.where((np.abs(x) < 1.0) & inside, w, 0.0)
    vals = samples[np.clip(idx, 0, n - 1)]
    return (vals * w).sum(axis=1).astype(np.float32)


def pyramid_depth(rows: int) -> int:
    """build_pyramid 对给定行数会构建的额外层数（不含第 0 层）。"""
    depth = 0
    while rows > _PYRAMID_MIN_ROWS:
        rows = (rows + 1) // 2
        depth += 1
    return depth


def pick_level(levels: List[np.ndarray], frame_start: int, frame_end: int, cols: int) -> int:
    """选「窗口内组数 ≥ 2×列宽」的最粗层；都不满足时用第 0 层。"""
    for lvl in range(len(levels) - 1, 0, -1):
        g0 = frame_start >> lvl
        g1 = (frame_end + (1 << lvl) - 1) >> lvl
        if g1 - g0 >= 2 * cols:
            return lvl
    return 0


def reduce_columns(sub: np.ndarray, cols: int) -> np.ndarray:
    """把若干金字塔组归约为 cols 列（每列取覆盖组数的 max）。sub 形状 (rows, bins)。"""
    edges = np.linspace(0, sub.shape[0], cols + 1)
    idx = np.clip(np.floor(edges[:-1]).astype(np.int64), 0, sub.shape[0] - 1)
    return np.maximum.reduceat(sub, idx, axis=0)


def reduce_rows(cols_matrix: np.ndarray, bin_edges: np.ndarray) -> np.ndarray:
    """按频率 bin 边界归约为 H 行（行 0 = 最低频段）。bin_edges 长度 H+1，单调不减。"""
    idx = np.clip(
        np.floor(bin_edges[:-1]).astype(np.int64), 0, cols_matrix.shape[1] - 1
    )
    return np.maximum.reduceat(cols_matrix, idx, axis=1)


def build_level(matrix: np.ndarray, level: int) -> np.ndarray:
    """直接构建第 level 层（帧数按 2^level 分组取 max）。

    与 :func:`build_pyramid` 的层对齐一致（层 k 行 j 覆盖帧
    [j·2^k, (j+1)·2^k)），供超预算长音频按需惰性构建单层。
    """
    if level <= 0:
        return matrix
    group = 1 << level
    rows = matrix.shape[0]
    full = (rows // group) * group
    out = matrix[:full].reshape(-1, group, matrix.shape[1]).max(axis=1)
    if rows % group:
        out = np.vstack([out, matrix[full:].max(axis=0, keepdims=True)])
    return out


def resolve_freq_range(
    nyquist: float,
    scale: str,
    f_min_hz: float = 0.0,
    f_max_hz: float = 0.0,
) -> tuple:
    """钳制后的显示频率区间 ``(f_lo, f_hi)``；热图行映射与频率轴刻度共用。

    0 = 自动（log 下限 30Hz / linear 下限 0，上限 Nyquist）；钳制后为空
    （f_min ≥ f_max）回退全范围。
    """
    if scale == "log":
        f_lo, f_hi = LOG_SCALE_MIN_HZ, max(nyquist, LOG_SCALE_MIN_HZ * 2.0)
    else:
        f_lo, f_hi = 0.0, nyquist
    if f_min_hz > 0:
        f_lo = max(f_lo, float(f_min_hz))
    if f_max_hz > 0:
        f_hi = min(f_hi, float(f_max_hz))
    if f_hi <= f_lo:
        if scale == "log":
            return LOG_SCALE_MIN_HZ, max(nyquist, LOG_SCALE_MIN_HZ * 2.0)
        return 0.0, nyquist
    return f_lo, f_hi


def frequency_bin_edges(
    n_bins: int,
    sample_rate: int,
    fft_size: int,
    rows: int,
    scale: str,
    f_min_hz: float = 0.0,
    f_max_hz: float = 0.0,
) -> np.ndarray:
    """频率轴 → bin 边界数组（长度 rows+1，单位：bin 序号，浮点）。

    scale="log"：[LOG_SCALE_MIN_HZ, nyquist] 对数均分；"linear"：[0, nyquist]
    线性均分。f_min_hz / f_max_hz 为显示期频率钳制（Hz，0 = 自动/不限，
    见 :func:`resolve_freq_range`）——纯渲染期参数，不触发矩阵重算，
    默认 0/0 与无钳制时逐位一致。
    """
    f_lo, f_hi = resolve_freq_range(sample_rate / 2.0, scale, f_min_hz, f_max_hz)
    bins_per_hz = fft_size / float(sample_rate)
    if scale == "log":
        freqs = np.geomspace(f_lo, f_hi, rows + 1)
    else:
        freqs = np.linspace(f_lo, f_hi, rows + 1)
    return np.clip(freqs * bins_per_hz, 0, n_bins - 1)


def build_colormap_lut(floor_u: int, colormap: str = "inferno") -> np.ndarray:
    """构造 256×4 RGBA 查找表。

    矩阵编码为 -128dB→0、0dB→255，故 `floor_u` 必须按
    ``(128 - range_db) * 255 / 128`` 换算（range dB 动态范围的下沿电平）。
    [floor_u, 255] 的可见段重新归一化拉伸到完整所选渐变（动态范围
    越小对比越强）；u < floor_u 填**色带底部色**（拉伸段 t=0 的深色）而非
    背景——低动态范围时低于地板的区域是连续深色实底，不会露出控件背景
    形成"破洞"观感（Sonic Visualiser / Audacity 同款行为）。
    未知色带名回落到项目原有的 inferno，兼容旧配置和外部调用方。
    """
    stops = _COLORMAP_STOPS.get(str(colormap), _COLORMAP_STOPS["inferno"])
    stops_t = np.array([s[0] for s in stops], dtype=np.float64)
    stops_rgb = np.array([s[1] for s in stops], dtype=np.float64)
    floor_u = int(max(0, min(255, floor_u)))
    lut = np.empty((256, 4), dtype=np.uint8)
    lut[:, 3] = 255
    if floor_u >= 255:
        lut[:, :3] = np.round(
            np.interp(0.0, stops_t, stops_rgb)
        ).astype(np.uint8)
        return lut
    t_band = (np.arange(floor_u, 256) - floor_u) / float(255 - floor_u)
    band = np.stack(
        [np.interp(t_band, stops_t, stops_rgb[:, c]) for c in range(3)],
        axis=1,
    )
    lut[floor_u:, :3] = np.round(band).astype(np.uint8)
    # 低于地板 → 色带底部色（= band[0]，拉伸段的 t=0）
    lut[:floor_u, :3] = band[0].astype(np.uint8)
    return lut


def _mel_filterbank(n_bins: int, sample_rate: int, n_mels: int = 128) -> np.ndarray:
    """Slaney 风格三角 mel 滤波器组（librosa 默认 htk=False/norm='slaney' 的复刻）。"""
    nyq = sample_rate / 2.0

    def hz_to_mel(f):
        f = np.asarray(f, dtype=np.float64)
        return np.where(
            f < 1000.0,
            3.0 * f / 2000.0,
            15.0 + 27.0 * np.log(np.maximum(f, 1000.0) / 1000.0) / np.log(6.4),
        )

    def mel_to_hz(m):
        m = np.asarray(m, dtype=np.float64)
        return np.where(
            m < 15.0, 2000.0 * m / 3.0, 1000.0 * (6.4 ** ((m - 15.0) / 27.0))
        )

    mel_edges = np.linspace(hz_to_mel(0.0), hz_to_mel(nyq), n_mels + 2)
    hz_edges = mel_to_hz(mel_edges)
    # 滤波器边沿对应的（浮点）FFT bin 序号。
    bin_pos = hz_edges * (2 * n_bins - 2) / sample_rate
    axis = np.arange(n_bins, dtype=np.float64)
    fb = np.zeros((n_mels, n_bins), dtype=np.float32)
    for m in range(n_mels):
        lo, cen, hi = bin_pos[m], bin_pos[m + 1], bin_pos[m + 2]
        left = np.clip((axis - lo) / max(cen - lo, 1e-9), 0.0, 1.0)
        right = np.clip((hi - axis) / max(hi - cen, 1e-9), 0.0, 1.0)
        w = np.minimum(left, right)
        # Slaney 面积归一化：除以该三角在 mel 刻度上的宽度。
        width = float(mel_edges[m + 2] - mel_edges[m])
        if width > 0:
            w *= 2.0 / width
        fb[m] = w.astype(np.float32)
    return fb


def detect_bpm(
    samples: np.ndarray,
    sample_rate: int,
    progress_cb: ProgressCb = None,
    cancel_check: CancelCheck = None,
) -> dict:
    """估计 BPM — librosa 管线的纯 numpy 复刻（Ellis 2007 三段式的前两段）。

    1. onset 强度包络（librosa.onset.onset_strength）：mel 功率谱
       （n_mels=128, n_fft=2048, hop=512）→ dB（top_db=80 相对地板）→
       一阶差分 → 半波整流 → 跨 mel 频带取均值。
    2. tempo 评分：onset 包络的 Fourier tempogram（包络 DFT 在 BPM 频率处的
       幅值，频率连续）× Ellis 梳状谐波支持（周期 2/3/4 倍）× librosa 默认
       对数正态先验（center=120 BPM、std=1 个八度），log1p(1e6·x)+logprior
       后取 argmax。不用整数滞后自相关——150 BPM @hop=512 周期 34.45 帧，
       整数 lag 只能对齐一半节拍，会错检成 75 BPM（实测踩坑；librosa 默认
       tempogram 也有此量化）。

    返回 {"bpm": float | None, "confidence": 0.0~1.0}。识别失败 bpm=None。
    纯人声/自由节奏可能误判，调用方应让用户确认结果。
    """
    n_fft, hop, n_mels = 2048, 512, 128
    n = len(samples)
    # 至少覆盖几个节拍周期才有检测意义。
    if sample_rate <= 0 or n < n_fft * 8:
        return {"bpm": None, "confidence": 0.0}
    # 预取消快速退出（同 compute_spectrogram：不先分配整段 mel_power）
    if cancel_check is not None and cancel_check():
        return {"bpm": None, "confidence": 0.0}

    # 与声谱同口径的居中帧网格（首尾局部补零，见 _iter_frame_chunks）
    n_frames = frame_count(n, n_fft, hop)
    n_bins = n_fft // 2 + 1
    fb = _mel_filterbank(n_bins, sample_rate, n_mels)
    mel_power = np.empty((n_frames, n_mels), dtype=np.float32)
    window = np.hanning(n_fft).astype(np.float32)
    chunk_frames = max(1, int(30 * sample_rate / hop))

    for i0, blk in _iter_frame_chunks(
        samples, n_fft, hop, chunk_frames, cancel_check
    ):
        if cancel_check is not None and cancel_check():
            return {"bpm": None, "confidence": 0.0}
        blk *= window
        power = np.abs(np.fft.rfft(blk, axis=1))
        power *= power
        mel_power[i0 : i0 + blk.shape[0]] = power @ fb.T
        if progress_cb is not None:
            progress_cb(min(1.0, (i0 + blk.shape[0]) / n_frames))

    # 生成器在取消时提前停止：未填满的 mel_power 不能继续分析
    if cancel_check is not None and cancel_check():
        return {"bpm": None, "confidence": 0.0}

    # dB（参考常数在差分中抵消），相对全局峰值 80dB 地板（librosa top_db=80）。
    db = 10.0 * np.log10(mel_power + 1e-10)
    db -= db.max()
    np.maximum(db, -80.0, out=db)
    # 一阶差分 → 半波整流 → 跨 mel 频带均值 = onset 强度包络。
    onset_env = np.maximum(db[1:] - db[:-1], 0.0).mean(axis=1)

    # Fourier tempogram：包络 DFT 幅值在候选 BPM 频率处线性插值取样。
    fps = sample_rate / float(hop)
    nfft = 1 << max(11, int(np.ceil(np.log2(4 * len(onset_env)))))
    spec_mag = np.abs(np.fft.rfft(onset_env, nfft))

    def mag_at(f_hz: float) -> float:
        pos = f_hz / fps * nfft
        i0 = int(pos)
        if i0 + 1 >= len(spec_mag):
            return 0.0
        frac = pos - i0
        return float(spec_mag[i0] * (1.0 - frac) + spec_mag[i0 + 1] * frac)

    bpms = np.arange(60.0, 200.0 + 1e-9, 0.25)
    freqs = bpms / 60.0
    # Ellis 梳状谐波支持：候选周期 2/3/4 倍处的能量加权和。
    score = np.zeros(bpms.shape, dtype=np.float64)
    for k, wgt in enumerate((1.0, 0.5, 1.0 / 3.0, 0.25), start=1):
        score += wgt * np.array([mag_at(f * k) for f in freqs])
    # librosa.feature.tempo 默认先验：center=120 BPM、std=1 个八度。
    logprior = -0.5 * ((np.log2(bpms) - np.log2(120.0)) / 1.0) ** 2
    total = np.log1p(1e6 * score) + logprior

    best = int(np.argmax(total))
    band_mags = np.array([mag_at(f) for f in freqs])
    peak_mag = float(band_mags.max())
    if peak_mag <= 0.0:
        return {"bpm": None, "confidence": 0.0}
    confidence = float(max(0.0, min(1.0, band_mags[best] / peak_mag)))
    return {"bpm": round(float(bpms[best]), 1), "confidence": confidence}


# 首音检测：逐窗 RMS 的窗口长度（128 采样 ≈ 3ms @44.1kHz，无重叠）——
# 粒度即偏移的定位精度，远小于一拍的时长，无需再细化到采样级
_FIRST_SOUND_WINDOW = 128
# 绝对静音下限（RMS ≈ 0.002 即 -54dBFS）：兜底纯数字静音底噪
_FIRST_SOUND_ABS_FLOOR = 0.002
# 相对阈值比例：响亮参考（窗口 RMS 95 分位）的 3%——低到不至于漏掉
# 开头的人声弱起音（呼吸声级），高到能压过现场录音的底噪起振
_FIRST_SOUND_REL_RATIO = 0.03


def first_sound_ms(samples: np.ndarray, sample_rate: int) -> Optional[float]:
    """定位首个非静音位置（毫秒）——「BPM 网格偏移」一键对齐首音用。

    逐窗 RMS 超过 ``max(绝对下限, 响亮参考的 3%)`` 即视为有声：
    - 相对阈值压制现场录音/模拟底噪的起振误触发，绝对下限兜底纯数字
      静音；比例取 3%，兼顾开头人声弱起音（呼吸声级）不被漏检；
    - 参考取 95 分位而非峰值，单个爆音不会把阈值抬高到漏掉开头弱音。

    返回首个超阈窗口起点的毫秒数；全曲静音（无窗口超阈）或音频过短
    （不足一窗）返回 None。纯 numpy 向量化，整曲毫秒级完成，无需后台
    线程（与 detect_bpm 的重量级管线不同）。
    """
    n = len(samples)
    if sample_rate <= 0 or n < _FIRST_SOUND_WINDOW:
        return None
    data = np.asarray(samples, dtype=np.float32)
    usable = (n // _FIRST_SOUND_WINDOW) * _FIRST_SOUND_WINDOW
    frames = data[:usable].reshape(-1, _FIRST_SOUND_WINDOW)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    loud_ref = float(np.percentile(rms, 95))
    threshold = max(_FIRST_SOUND_ABS_FLOOR, _FIRST_SOUND_REL_RATIO * loud_ref)
    audible = rms > threshold
    if not audible.any():
        return None
    return int(np.argmax(audible)) * _FIRST_SOUND_WINDOW / float(sample_rate) * 1000.0
