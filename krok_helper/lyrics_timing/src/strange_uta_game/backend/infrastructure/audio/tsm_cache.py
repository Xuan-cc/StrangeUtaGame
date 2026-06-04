"""磁盘缓存 TSM 渲染缓存。

设计：
- 切换播放速度（≠ 1.0x）时，后台 worker 用 Pedalboard 的 time_stretch 渲染，
  结果保存到磁盘缓存文件，不占用大量内存。
- 播放时从磁盘缓存读取到内存。
- 缓存文件位于程序所在目录下的 .cache 文件夹，更换歌曲或退出时自动清理。
- 1.0x 特殊路径：直接返回原始 PCM 引用，零渲染开销。
- 缓存文件采用 MP3 格式压缩，节省磁盘空间。

分块并行渲染架构：
- 最多同时渲染 2 个不同速度（MAX_SPEEDS）
- 每个速度内部，音频分成多个块（30秒/块），由多个 worker 并行处理
- 块之间有 10% 渲染重叠保证质量，拼接时提取 core 区域直接硬切

缓存文件命名：{歌曲名}_{speed}x.mp3
"""

from __future__ import annotations

import ctypes
import heapq
import os
import sys
import threading
import time
from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor, Future
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set

import numpy as np
import soundfile as sf
from pedalboard import time_stretch
from pedalboard.io import AudioFile


ProgressCallback = Callable[[float, float], None]  # (speed, 0.0~1.0)
DoneCallback = Callable[[float], None]              # (speed,)
LoadProgressCallback = Callable[[str, float], None]  # (stage, 0.0~1.0)


# ---- Windows 线程优先级 ----
_THREAD_PRIORITY_BELOW_NORMAL = -1


def _set_worker_thread_priority() -> None:
    """将当前线程（TSMWorker）降到 BELOW_NORMAL，让音频线程优先获得 CPU。
    仅 Windows 生效，其他平台静默忽略。
    """
    if sys.platform != "win32":
        return
    try:
        handle = ctypes.windll.kernel32.GetCurrentThread()
        ctypes.windll.kernel32.SetThreadPriority(handle, _THREAD_PRIORITY_BELOW_NORMAL)
    except Exception:
        pass

_SPEED_QUANT = 2  # round(speed, 2)，0.01 精度
_CACHE_DIR_NAME = ".cache"
_MP3_QUALITY = 128  # MP3 比特率 (kbps)

# 分块渲染参数
_MAX_SPEEDS = 2         # 最多同时渲染的速度数
_CHUNK_SECONDS = 30     # 每块秒数
_OVERLAP_RATIO = 0.1    # 渲染重叠比例 10%，保证 TSM 渲染质量
_CPU_USAGE_RATIO = 0.7  # CPU 使用比例上限


def _get_max_workers() -> int:
    """根据 CPU 核心数计算全局最大 worker 数，不超过 70%。"""
    import os
    cpu_count = os.cpu_count() or 4
    return max(1, int(cpu_count * _CPU_USAGE_RATIO))


def _quantize(speed: float) -> float:
    return round(float(speed), _SPEED_QUANT)


def _get_cache_dir() -> Path:
    """获取缓存目录（程序所在目录下的 .cache 文件夹）"""
    program_dir = Path(sys.argv[0]).resolve().parent
    cache_dir = program_dir / _CACHE_DIR_NAME
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_cache_path(song_name: str, speed: float) -> Path:
    """获取缓存文件路径"""
    cache_dir = _get_cache_dir()
    q = _quantize(speed)
    filename = f"{song_name}_{q}x.mp3"
    return cache_dir / filename


def _get_source_mp3_path(song_name: str) -> Path:
    """获取源 MP3 文件路径"""
    cache_dir = _get_cache_dir()
    return cache_dir / f"{song_name}_source.mp3"


def clear_cache() -> None:
    """清空所有缓存文件"""
    cache_dir = _get_cache_dir()
    for f in cache_dir.glob("*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass
    print(f"[TSM缓存] 已清空全部缓存文件: {cache_dir}")


def clear_cache_for_song(song_name: str) -> None:
    """清空指定歌曲的所有缓存文件（包括源 MP3）"""
    cache_dir = _get_cache_dir()
    for f in cache_dir.glob(f"{song_name}_*.mp3"):
        try:
            f.unlink()
        except Exception:
            pass
    source = cache_dir / f"{song_name}_source.mp3"
    if source.exists():
        try:
            source.unlink()
        except Exception:
            pass
    print(f"[TSM缓存] 已清空歌曲缓存: {song_name}")


@dataclass
class ChunkInfo:
    """分块信息"""
    index: int
    src_start: int       # 源 PCM 中的起始采样（含 overlap）
    src_end: int         # 源 PCM 中的结束采样（含 overlap）
    core_start: int      # 核心区域起始（不含 overlap）
    core_end: int        # 核心区域结束（不含 overlap）


def _split_chunks(total_samples: int, sample_rate: int) -> List[ChunkInfo]:
    """将音频分成多个块，每块向两侧扩展 overlap 用于 TSM 渲染质量。

    例如 60 秒音频，30 秒/块，渲染 overlap 3秒：
    块0: core=[0, 30s),      src=[0, 33s)      右侧扩展3秒
    块1: core=[30s, 60s),    src=[27s, 60s)    左侧扩展3秒

    重叠区域：27~33秒（6秒），渲染时两个块都覆盖该区域保证 TSM 质量。
    拼接时只提取 core 区域直接硬切，不做淡化。

    Returns:
        ChunkInfo 列表
    """
    chunk_samples = _CHUNK_SECONDS * sample_rate
    overlap_samples = int(chunk_samples * _OVERLAP_RATIO)

    chunks = []
    core_start = 0
    idx = 0
    while core_start < total_samples:
        core_end = min(core_start + chunk_samples, total_samples)
        # 左侧：非第一块向左扩展 overlap
        src_start = max(0, core_start - overlap_samples) if idx > 0 else core_start
        # 右侧：非最后一块向右扩展 overlap
        src_end = min(total_samples, core_end + overlap_samples) if core_end < total_samples else core_end

        chunks.append(ChunkInfo(
            index=idx,
            src_start=src_start,
            src_end=src_end,
            core_start=core_start,
            core_end=core_end,
        ))

        core_start = core_end
        idx += 1

    return chunks


@dataclass
class SpeedTask:
    """单个速度的渲染任务"""
    speed: float
    priority: int
    progress_cb: Optional[ProgressCallback]
    done_cb: Optional[DoneCallback]
    version: int
    chunks: List[ChunkInfo] = field(default_factory=list)
    results: Dict[int, np.ndarray] = field(default_factory=dict)  # {chunk_index: pcm}
    pending_chunks: Set[int] = field(default_factory=set)  # 待处理的块 index
    futures: List[Future] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
    completed: threading.Event = field(default_factory=threading.Event)
    cancelled: bool = False


# 全局线程池（所有速度任务共享，总并发数不超过 CPU 70%）
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()

# 专用 finalizer 线程池：单线程，负责 merge + MP3 编码 + 磁盘写入。
# 与 TSMWorker 池隔离，确保这些重操作不占用渲染 worker 槽，
# 且 Worker 的 done_callback 只做检查和投递，立即返回。
_finalizer_executor: Optional[ThreadPoolExecutor] = None
_finalizer_lock = threading.Lock()


def _get_executor() -> ThreadPoolExecutor:
    """获取全局渲染线程池（懒初始化）。"""
    global _executor
    with _executor_lock:
        if _executor is None:
            max_workers = _get_max_workers()
            _executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="TSMWorker")
            print(f"[TSM渲染池] 已初始化，最大 worker 数: {max_workers}")
        return _executor


def _get_finalizer_executor() -> ThreadPoolExecutor:
    """获取专用 finalizer 线程池（单线程，懒初始化）。"""
    global _finalizer_executor
    with _finalizer_lock:
        if _finalizer_executor is None:
            _finalizer_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="TSMFinalizer")
        return _finalizer_executor


class TSMRenderCache:
    """磁盘缓存 TSM 渲染缓存，支持分块并行渲染和优先级队列。

    架构：加载时将原始音频保存为源 MP3，后续所有操作（播放、TSM 渲染）
    都从这份 MP3 读取，减少内存占用。
    """

    # MP3 支持的采样率
    _MP3_TARGET_SR = 44100

    def __init__(self) -> None:
        self._source_mp3_path: Optional[Path] = None
        self._sample_rate: int = 0
        self._channels: int = 0
        self._song_name: str = ""

        # 速度级别任务队列：(priority, speed, progress_cb, done_cb)
        self._speed_queue: list = []
        self._queue_lock = threading.Lock()

        # 正在渲染的速度任务：{speed: SpeedTask}
        self._active_tasks: Dict[float, SpeedTask] = {}
        self._active_lock = threading.Lock()

        # 版本控制
        self._render_version: int = 0
        self._lock = threading.Lock()

        # 调度线程
        self._scheduler_thread: Optional[threading.Thread] = None
        self._scheduler_stop = threading.Event()

        # 内存级别的 PCM 缓存，避免重复读盘解码
        self._memory_cache: OrderedDict[float, np.ndarray] = OrderedDict()
        self._mem_cache_lock = threading.Lock()
        self._MAX_MEM_CACHE = 5

    # ---------- 加载 ----------

    def set_source(
        self,
        song_name: str,
        original_pcm: np.ndarray,
        sample_rate: int,
        progress_cb: Optional[LoadProgressCallback] = None,
    ) -> None:
        """切换原始音频。将原始 PCM 保存为源 MP3，清空旧缓存。"""
        self._cancel_all_and_wait()
        with self._lock:
            if progress_cb:
                progress_cb("清理旧缓存...", 0.0)
            clear_cache()
            with self._mem_cache_lock:
                self._memory_cache.clear()

            self._song_name = song_name
            channels = int(original_pcm.shape[1]) if original_pcm.ndim > 1 else 1

            if progress_cb:
                progress_cb("转换为 MP3...", 0.1)
            source_path = _get_source_mp3_path(song_name)
            actual_sr = self._save_source_as_mp3(
                original_pcm, sample_rate, channels, source_path, progress_cb
            )
            self._source_mp3_path = source_path
            self._sample_rate = actual_sr
            self._channels = channels

            if progress_cb:
                progress_cb("完成", 1.0)
            print(f"[TSM缓存] 源音频已保存为 MP3: {source_path} ({actual_sr}Hz, {channels}ch)")

    def _save_source_as_mp3(
        self,
        pcm: np.ndarray,
        sample_rate: int,
        channels: int,
        path: Path,
        progress_cb: Optional[LoadProgressCallback] = None,
    ) -> int:
        """将 PCM 保存为 MP3，如果采样率不支持则降采样。"""
        mp3_rates = [32000, 44100, 48000]
        target_sr = sample_rate
        if sample_rate not in mp3_rates:
            target_sr = min(mp3_rates, key=lambda r: abs(r - sample_rate))
            print(f"[TSM缓存] 采样率不兼容，重采样 {sample_rate}Hz → {target_sr}Hz")

        data = pcm
        if target_sr != sample_rate:
            if progress_cb:
                progress_cb("降采样中...", 0.3)
            from pedalboard.io import StreamResampler
            resampler = StreamResampler(sample_rate, target_sr, channels)
            resampled = resampler.process(pcm.T)
            tail = resampler.process(None)
            resampled_full = np.concatenate([resampled, tail], axis=1)
            data = resampled_full.T.astype(np.float32)

        if progress_cb:
            progress_cb("编码 MP3...", 0.6)
        mp3_bytes = AudioFile.encode(
            data.T,
            samplerate=target_sr,
            format="mp3",
            num_channels=channels,
            quality=_MP3_QUALITY,
        )
        if progress_cb:
            progress_cb("保存文件...", 0.9)
        with open(path, "wb") as f:
            f.write(mp3_bytes)

        return target_sr

    def clear(self) -> None:
        self._cancel_all_and_wait()
        with self._lock:
            if self._song_name:
                clear_cache_for_song(self._song_name)
            self._source_mp3_path = None
        with self._mem_cache_lock:
            self._memory_cache.clear()

    # ---------- 查询 ----------

    def get(self, speed: float) -> Optional[np.ndarray]:
        """从缓存读取。优先查内存缓存，未命中再查磁盘。"""
        if self._source_mp3_path is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._load_source_pcm()

        # 先查内存缓存
        with self._mem_cache_lock:
            if q in self._memory_cache:
                self._memory_cache.move_to_end(q)
                return self._memory_cache[q]

        # 内存没有，查磁盘缓存
        cache_path = _get_cache_path(self._song_name, q)
        if cache_path.exists():
            try:
                data = self._load_from_mp3(cache_path)
                # 存入内存缓存
                with self._mem_cache_lock:
                    self._memory_cache[q] = data
                    if len(self._memory_cache) > self._MAX_MEM_CACHE:
                        self._memory_cache.popitem(last=False)
                return data
            except Exception as e:
                print(f"[TSM缓存] 读取磁盘缓存失败: {e}")
        return None

    def _load_source_pcm(self) -> Optional[np.ndarray]:
        """从源 MP3 加载 PCM 数据（带内存缓存）。"""
        # 1.0x 对应的量化键
        q = 1.0
        with self._mem_cache_lock:
            if q in self._memory_cache:
                self._memory_cache.move_to_end(q)
                return self._memory_cache[q]

        if self._source_mp3_path is None or not self._source_mp3_path.exists():
            return None
        data = self._load_from_mp3(self._source_mp3_path)
        with self._mem_cache_lock:
            self._memory_cache[q] = data
            if len(self._memory_cache) > self._MAX_MEM_CACHE:
                self._memory_cache.popitem(last=False)
        return data

    def _load_from_mp3(self, path: Path) -> np.ndarray:
        """从 MP3 文件加载 PCM 数据。返回 (samples, channels) float32。"""
        with AudioFile(str(path)) as f:
            audio = f.read(f.frames)
        return audio.T.astype(np.float32)

    # ---------- 渲染 ----------

    def ensure(
        self,
        speed: float,
        priority: int = 99,
        progress_cb: Optional[ProgressCallback] = None,
        done_cb: Optional[DoneCallback] = None,
        preempt: bool = False,
    ) -> Optional[np.ndarray]:
        """确保 ``speed`` 对应的 PCM 就绪。

        ``preempt=True``（UI 主动申请的当前速度）：打断正在渲染的其它速度任务、
        让出 worker 槽，使本次以最高优先级立即开跑；被打断的任务按原优先级
        重新入队，稍后继续。
        """
        if self._source_mp3_path is None:
            return None
        q = _quantize(speed)
        if abs(q - 1.0) < 1e-9:
            return self._load_source_pcm()

        # 检查磁盘缓存
        cached = self.get(q)
        if cached is not None:
            print(f"[TSM缓存] 缓存命中，速度 {q}x，无需渲染")
            return cached

        # 检查是否已在活跃渲染中
        with self._active_lock:
            if q in self._active_tasks:
                active_task = self._active_tasks[q]
                # 如果新请求带有 done_cb（用户主动调速），需要把回调注入到
                # 正在跑的任务里，否则预热任务完成后不会触发换源/进度上报。
                # 用 task.lock 保护，避免与 _finalize_task / 闭包回调竞争。
                if done_cb is not None or progress_cb is not None:
                    with active_task.lock:
                        if done_cb is not None:
                            active_task.done_cb = done_cb
                        if progress_cb is not None:
                            active_task.progress_cb = progress_cb
                    print(
                        f"[TSM调度] 速度 {q}x 正在渲染中，注入用户回调"
                        f"（换源回调={'已注入' if done_cb is not None else '无'}）"
                    )
                else:
                    print(f"[TSM调度] 速度 {q}x 已在渲染中，跳过重复派发")
                return None

        with self._queue_lock:
            existing = next(
                ((pr, sp, pc, dc) for (pr, sp, pc, dc) in self._speed_queue
                 if abs(sp - q) < 1e-9),
                None,
            )
            if existing is not None:
                # 已在队列。非抢占、或新优先级不更高 → 跳过；否则提升优先级
                # （并采用本次 UI 提供的回调），让它插到队首。
                if not preempt and priority >= existing[0]:
                    print(f"[TSM调度] 速度 {q}x 已在队列中，跳过重复派发")
                    return None
                self._speed_queue = [
                    item for item in self._speed_queue if abs(item[1] - q) >= 1e-9
                ]
                heapq.heapify(self._speed_queue)
                print(f"[TSM调度] 速度 {q}x 已在队列中，提升优先级至 {priority}，替换回调")

        # 抢占：让出当前正在渲染的其它速度，使本次请求立即获得 worker 槽。
        if preempt:
            self._preempt_active(keep_speed=q)

        with self._queue_lock:
            heapq.heappush(self._speed_queue, (priority, q, progress_cb, done_cb))
            print(
                f"[TSM调度] 速度 {q}x 入队，优先级 {priority}"
                f"{'（抢占模式）' if preempt else '（预热模式）'}"
            )

        self._ensure_scheduler_running()
        return None

    def _preempt_active(self, keep_speed: float) -> None:
        """打断当前正在渲染的（除 ``keep_speed`` 外）速度任务。

        标记取消并取消其尚未开始的块 future（已运行的块靠 cancelled 标志早退），
        从活跃表移除以释放速度槽，并按原优先级重新入队以便稍后续渲。
        """
        requeue = []
        with self._active_lock:
            for spd, task in list(self._active_tasks.items()):
                if abs(spd - keep_speed) < 1e-9:
                    continue
                task.cancelled = True
                for f in task.futures:
                    f.cancel()
                # 清空已渲染的 chunk 数据，防止被抢占后残留的脏数据
                # 在下次重新入队渲染时污染新 SpeedTask（闭包回调已绑定旧
                # task，但旧 task.results 里的数据已无意义，及时释放内存）。
                with task.lock:
                    task.results.clear()
                # 被抢占的任务降级为预热任务：清除 done_cb 和 progress_cb。
                # 理由：当前用户目标速度已经变成 keep_speed，这个任务的速度
                # 对用户而言不再是"期望换源"的目标。若将来用户再次调到该
                # 速度，set_speed() 会通过 ensure(preempt=True) 重新注入正确
                # 的 done_cb；若用户从未再调到该速度，则它仅作后台预热存在。
                # 保留原始优先级（非 -1）用于后续调度排序。
                requeue.append((task.priority, spd, None, None))
                del self._active_tasks[spd]

        if not requeue:
            return
        with self._queue_lock:
            for item in requeue:
                if not any(abs(qs - item[1]) < 1e-9 for _, qs, _, _ in self._speed_queue):
                    heapq.heappush(self._speed_queue, item)
                    print(f"[TSM调度] 速度 {item[1]}x 被抢占，降级为预热任务，重新入队（优先级 {item[0]}）")

    # ---------- 调度 ----------

    def _ensure_scheduler_running(self) -> None:
        """确保调度线程在运行。"""
        with self._lock:
            if self._scheduler_thread is not None and self._scheduler_thread.is_alive():
                return
            self._scheduler_stop.clear()
            self._scheduler_thread = threading.Thread(
                target=self._scheduler_loop, daemon=True, name="TSMScheduler"
            )
            self._scheduler_thread.start()

    def _scheduler_loop(self) -> None:
        """调度线程：从速度队列取出任务，将块提交到全局线程池。"""
        while not self._scheduler_stop.is_set():
            task = None
            with self._queue_lock:
                if self._speed_queue:
                    task = heapq.heappop(self._speed_queue)

            if task is None:
                with self._active_lock:
                    if not self._active_tasks:
                        break
                time.sleep(0.1)
                continue

            priority, speed, progress_cb, done_cb = task

            with self._lock:
                current_version = self._render_version

            # 等待有空闲速度槽位
            while not self._scheduler_stop.is_set():
                with self._active_lock:
                    if len(self._active_tasks) < _MAX_SPEEDS:
                        break
                time.sleep(0.05)

            if self._scheduler_stop.is_set():
                break

            # 读取源 PCM 并分块
            source_pcm = self._load_source_pcm()
            if source_pcm is None:
                continue

            chunks = _split_chunks(len(source_pcm), self._sample_rate)
            print(f"[TSM渲染] 速度 {speed}x 开始渲染，共 {len(chunks)} 块，提交至线程池")

            # 创建速度任务
            speed_task = SpeedTask(
                speed=speed,
                priority=priority,
                progress_cb=progress_cb,
                done_cb=done_cb,
                version=current_version,
                chunks=chunks,
                pending_chunks=set(range(len(chunks))),
            )

            with self._active_lock:
                self._active_tasks[speed] = speed_task

            # 将所有块提交到全局线程池
            # 使用闭包回调，直接绑定 speed_task 引用，避免通过 chunk_index
            # 反查活跃任务表时发生跨任务数据污染（尤其是抢占重入场景）。
            executor = _get_executor()
            chunk_done_cb = self._make_chunk_done_callback(speed_task)
            for chunk in chunks:
                future = executor.submit(
                    self._render_chunk,
                    speed_task, source_pcm, chunk, current_version,
                )
                future.add_done_callback(chunk_done_cb)
                speed_task.futures.append(future)

    def _render_chunk(
        self,
        task: SpeedTask,
        source_pcm: np.ndarray,
        chunk: ChunkInfo,
        render_version: int,
    ) -> Optional[tuple]:
        """渲染单个块（在线程池中执行）。返回 (chunk_index, rendered_pcm) 或 None。"""
        # 降低 worker 线程优先级，避免与 AudioProducer 抢 CPU。
        _set_worker_thread_priority()

        if task.cancelled or self._render_version != render_version:
            return None

        try:
            chunk_pcm = source_pcm[chunk.src_start:chunk.src_end]
            chunk_pcm = np.ascontiguousarray(chunk_pcm, dtype=np.float32)

            rendered = time_stretch(
                chunk_pcm,
                float(self._sample_rate),
                stretch_factor=task.speed,
            )

            if task.cancelled or self._render_version != render_version:
                return None

            return (chunk.index, rendered.astype(np.float32))

        except Exception as e:
            print(f"[TSM渲染] 速度 {task.speed}x 第 {chunk.index} 块渲染出错: {e}")
            return None

    def _make_chunk_done_callback(self, task: SpeedTask) -> Callable[["Future"], None]:
        """为指定 SpeedTask 创建块完成回调，通过闭包直接持有 task 引用，
        避免通过 chunk_index 反查活跃任务表而引入跨任务数据污染。
        """
        def _on_chunk_done(future: Future) -> None:
            """块完成回调（在 TSMWorker 线程中执行）。

            此回调必须极轻量：只做结果存储、进度上报、完成检测和 finalizer 投递，
            绝不执行 merge / MP3编码 / 磁盘IO 等重操作（那些移到 TSMFinalizer 线程）。
            """
            try:
                result = future.result()
            except CancelledError:
                return
            if result is None:
                return

            # 任务已被取消（抢占等），丢弃结果，不写入 task.results
            if task.cancelled:
                return

            chunk_index, rendered_pcm = result

            all_done = False
            with task.lock:
                # 二次确认：取消标志可能在获取锁之前刚被设置
                if task.cancelled:
                    return
                task.results[chunk_index] = rendered_pcm
                task.pending_chunks.discard(chunk_index)
                progress = len(task.results) / len(task.chunks)
                # 检查是否所有块都成功完成
                if (len(task.results) == len(task.chunks)
                        and not task.completed.is_set()
                        and all(v is not None for v in task.results.values())):
                    task.completed.set()
                    all_done = True

            # 报告渲染进度（Worker 线程，轻量）
            # 用 task.lock 读取 progress_cb，防止与 ensure() 的回调升级竞争。
            with task.lock:
                progress_cb = task.progress_cb
            if progress_cb is not None:
                try:
                    progress_cb(task.speed, progress * 0.9)
                except Exception:
                    pass

            # 所有块完成 → 投递到专用 finalizer 线程执行 merge+保存，立即返回
            if all_done:
                _get_finalizer_executor().submit(self._finalize_task, task)

        return _on_chunk_done

    def _finalize_task(self, task: SpeedTask) -> None:
        """在 TSMFinalizer 线程中执行：merge + MP3编码 + 磁盘写入。

        此函数在专用单线程 finalizer 中运行，与 TSMWorker 池完全隔离，
        不会占用渲染 worker 槽，也不会在 Worker 的 done_callback 里阻塞。
        """
        try:
            print(f"[TSM渲染] 速度 {task.speed}x 全部块完成，开始合并...")
            # 用 task.lock 读取回调，防止与 ensure() 的回调升级竞争。
            # 在 merge 前快照一次，后续复用快照值（merge 期间回调不可能再变）。
            with task.lock:
                progress_cb = task.progress_cb
                done_cb = task.done_cb

            if progress_cb:
                try:
                    progress_cb(task.speed, 0.95)
                except Exception:
                    pass

            final_pcm = self._merge_chunks(task)

            # 保存到磁盘
            cache_path = _get_cache_path(self._song_name, task.speed)
            self._save_as_mp3(final_pcm, cache_path)
            print(f"[TSM渲染] 速度 {task.speed}x 渲染完成，已写入缓存: {cache_path.name}")

            # 释放 chunk results 占用的内存（merge 后不再需要）
            with task.lock:
                task.results.clear()

            if progress_cb:
                try:
                    progress_cb(task.speed, 1.0)
                except Exception:
                    pass

            if done_cb:
                try:
                    done_cb(task.speed)
                except Exception as e:
                    print(f"[TSM渲染] 速度 {task.speed}x 换源回调执行出错: {e}")

        except Exception as e:
            print(f"[TSM渲染] 速度 {task.speed}x 合并出错: {e}")
        finally:
            with self._active_lock:
                self._active_tasks.pop(task.speed, None)

    def _merge_chunks(self, task: SpeedTask) -> np.ndarray:
        """拼接所有块（无交叉淡化，直接硬切）。

        渲染时每个块向两侧扩展 10% overlap 保证 TSM 质量，
        此处仅提取 core 区域直接拼接。

        使用 np.empty + 逐段 copyto 代替 np.concatenate：
        - np.concatenate 会先构建 view 列表再一次性分配，内存峰值 ≈ 输入之和 + 输出
        - np.empty 预分配输出缓冲区后逐段 copyto，内存峰值只有输出大小一份
        """
        sorted_chunks = sorted(task.chunks, key=lambda c: c.index)

        # 预先计算输出总帧数，一次性分配目标缓冲区
        total_core_samples = sum(c.core_end - c.core_start for c in sorted_chunks)
        total_rendered = int(total_core_samples / task.speed)
        if total_rendered <= 0:
            return np.zeros((0, self._channels), dtype=np.float32)

        result = np.empty((total_rendered, self._channels), dtype=np.float32)
        write_pos = 0

        for chunk in sorted_chunks:
            rendered = task.results[chunk.index]
            if rendered is None:
                raise ValueError(f"Chunk {chunk.index} is None")

            # 从渲染结果中提取 core 区域（去掉两侧 overlap）
            src_len = chunk.src_end - chunk.src_start
            core_start_ratio = (chunk.core_start - chunk.src_start) / src_len
            core_end_ratio = (chunk.core_end - chunk.src_start) / src_len

            core_start = int(len(rendered) * core_start_ratio)
            core_end = int(len(rendered) * core_end_ratio)
            segment = rendered[core_start:core_end]  # view，不分配新内存

            seg_len = min(len(segment), total_rendered - write_pos)
            if seg_len <= 0:
                break
            np.copyto(result[write_pos : write_pos + seg_len], segment[:seg_len])
            write_pos += seg_len

        # 若实际写入比预计少（浮点对齐），截断
        return result[:write_pos]

    def _save_as_mp3(self, pcm: np.ndarray, path: Path) -> None:
        """将 PCM 数据保存为 MP3 文件。"""
        mp3_bytes = AudioFile.encode(
            pcm.T,
            samplerate=self._sample_rate,
            format="mp3",
            num_channels=self._channels,
            quality=_MP3_QUALITY,
        )
        with open(path, "wb") as f:
            f.write(mp3_bytes)

    def _cancel_all_and_wait(self) -> None:
        """取消所有渲染任务并等待完成。"""
        with self._lock:
            self._render_version += 1

        # 取消所有活跃任务
        with self._active_lock:
            for task in self._active_tasks.values():
                task.cancelled = True
            tasks = list(self._active_tasks.values())

        # 等待所有 future 完成
        for task in tasks:
            for future in task.futures:
                future.cancel()

        with self._active_lock:
            self._active_tasks.clear()

        with self._queue_lock:
            self._speed_queue.clear()

        self._scheduler_stop.set()
        if self._scheduler_thread and self._scheduler_thread.is_alive():
            self._scheduler_thread.join(timeout=2.0)
        self._scheduler_thread = None
