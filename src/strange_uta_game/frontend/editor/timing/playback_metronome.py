"""播放同步节拍器 — 按 BPM 网格的偏移与 BPM 在播放期间触发节拍音。

拍点与波形 BPM 网格同源：``拍时刻 = offset + b·(60000/BPM)``（b 为整数，
可为负），每小节第一拍（拍号分子 N，``b % N == 0``，即网格小节线）一记
高音重音。

调度模型（复刻校准弹窗已验证的「粗睡 + 自旋精等」）：

- 引擎 ``get_position_ms()`` 已做输出延迟补偿，即"当前可听到的媒体时刻"；
  节拍音触发后自身还要滞后约一个输出延迟才出声，因此以该值作提前量
  触发，与引擎位置补偿共用同一延迟模型。
- 位置恒为原始时间轴（变速不伸缩），等待墙钟时间按 ``get_speed()`` 折算，
  变速播放时拍点仍落在正确的媒体时刻。
- 每轮粗睡醒来重查位置/参数/播放状态：seek、播放中改 BPM/偏移、暂停
  都在下一个等待周期（≤20ms）内被吸收；最后 ~12ms 自旋等待保证触发
  时刻精度。
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable, Optional

# 临近自旋窗口（媒体 ms）：距下一拍不足该值时进入精等
_NEAR_MS = 12.0
# 粗睡分档（秒）：远（>0.5s）/中（>50ms）/近（8ms 片），保证状态变化
# 最迟 ~20ms 内被吸收，同时低 BPM 长间隙不空转查询
_SLEEP_FAR = 0.25
_SLEEP_MID = 0.02
_SLEEP_NEAR = 0.008

_BPM_MIN, _BPM_MAX = 10.0, 600.0
_OFFSET_MIN_MS, _OFFSET_MAX_MS = -600000, 600000
# 拍号分子（每小节拍数）范围：覆盖 2/4~8/4 常用拍号，留余量到 16
_BEATS_PER_BAR_MIN, _BEATS_PER_BAR_MAX = 1, 16


def next_beat_after(position_ms: float, bpm: float, offset_ms: float) -> tuple:
    """position_ms 之后第一拍的 ``(拍索引, 拍时刻)``。

    恰好停在拍点上时该拍归入"已过"、返回下一拍——在拍点处暂停再恢复
    不会立刻补响一记。拍索引可为负（对应网格的负数拍）。
    """
    beat_ms = 60000.0 / bpm
    b = math.floor((position_ms - offset_ms) / beat_ms) + 1
    return b, offset_ms + b * beat_ms


def is_accent_beat(beat_index: int, beats_per_bar: int = 4) -> bool:
    """重音判定：每小节第一拍（拍号分子为 N 时 N 拍一记，负数拍同余）。

    与 BPM 网格小节线同口径；默认 4/4（历史行为）。
    """
    return beat_index % max(1, int(beats_per_bar)) == 0


class PlaybackMetronome:
    """随播放调度的节拍器（daemon 线程）。

    经 provider 注入与音频引擎的交互，UI 侧仅 ``configure/start/stop/resync``：
    ``position_ms`` 返回延迟补偿后的可听位置；``speed`` 为当前播放速度。
    """

    def __init__(
        self,
        player,
        position_ms: Callable[[], float],
        is_playing: Callable[[], bool],
        speed: Callable[[], float],
        output_latency_ms: Callable[[], float],
    ) -> None:
        self._player = player
        self._position_ms = position_ms
        self._is_playing = is_playing
        self._speed = speed
        self._output_latency_ms = output_latency_ms

        self._lock = threading.Lock()
        self._running = False
        # 参数/会话代际：configure/start/resync/stop 各自递增，线程发现变化
        # 即作废在途等待、按当前位置重新对齐（不补响）
        self._generation = 0
        self._bpm = 120.0
        self._offset_ms = 0.0
        self._beats_per_bar = 4
        self._thread: Optional[threading.Thread] = None

    # ── 对外（UI 线程调用） ──

    def configure(self, bpm: float, offset_ms: int, beats_per_bar: int = 4) -> None:
        """更新拍点参数（BPM/偏移/拍号分子）；播放中变更时调度线程重新对齐。"""
        bpm = float(min(max(bpm, _BPM_MIN), _BPM_MAX))
        offset_ms = float(min(max(int(offset_ms), _OFFSET_MIN_MS), _OFFSET_MAX_MS))
        beats_per_bar = int(
            min(max(int(beats_per_bar), _BEATS_PER_BAR_MIN), _BEATS_PER_BAR_MAX)
        )
        with self._lock:
            if (
                self._bpm == bpm
                and self._offset_ms == offset_ms
                and self._beats_per_bar == beats_per_bar
            ):
                return
            self._bpm = bpm
            self._offset_ms = offset_ms
            self._beats_per_bar = beats_per_bar
            self._generation += 1

    def start(self) -> None:
        """开始随播放调度（幂等；已在跑时仅作废在途等待重新对齐）。"""
        with self._lock:
            thread_alive = (
                self._running
                and self._thread is not None
                and self._thread.is_alive()
            )
            if thread_alive:
                self._generation += 1
                return
            # _running 为真但线程已死（未预见的异常路径）：直接重新拉起
            self._running = True
            self._generation += 1
            thread = threading.Thread(
                target=self._loop, name="PlaybackMetronome", daemon=True
            )
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        """停止调度。只置标志不 join：线程最迟约一个等待周期（~20ms）自退，
        不阻塞 UI 线程；触发前会复查播放状态，不会多响。"""
        with self._lock:
            self._running = False
            self._generation += 1
            self._thread = None

    def resync(self) -> None:
        """seek 等位置跳变后重新对齐（不重启线程、不补响）。"""
        with self._lock:
            self._generation += 1

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._running

    # ── 调度线程 ──

    def _query_position(self) -> Optional[float]:
        try:
            return float(self._position_ms())
        except Exception:
            return None  # 引擎瞬时不可用（加载/切换中）：稍后重试，不退出

    def _loop(self) -> None:
        me = threading.current_thread()
        next_beat_ms: Optional[float] = None  # None = 需按当前位置重新对齐
        beat_index = 0
        gen_seen = -1
        try:
            while True:
                with self._lock:
                    if not self._running or self._thread is not me:
                        return
                    gen = self._generation
                    bpm = self._bpm
                    offset = self._offset_ms
                    beats_per_bar = self._beats_per_bar
                if gen != gen_seen:
                    gen_seen = gen
                    next_beat_ms = None

                try:
                    playing = bool(self._is_playing())
                except Exception:
                    return
                if not playing:
                    return  # 播放完毕/暂停（UI stop() 之外的兜底）
                pos = self._query_position()
                if pos is None:
                    time.sleep(_SLEEP_MID)
                    continue

                beat_ms = 60000.0 / bpm
                if next_beat_ms is None:
                    beat_index, next_beat_ms = next_beat_after(pos, bpm, offset)
                elif pos - next_beat_ms > beat_ms:
                    # 落后超过一拍（等待期间位置前跳）：只重对齐不补响
                    beat_index, next_beat_ms = next_beat_after(pos, bpm, offset)
                elif next_beat_ms - pos > beat_ms * 2.0:
                    # 领先超过两拍 = 位置向后跳变未经 resync（换音频把位置
                    # 重置回 0 等）——同样重对齐，否则会陷入"粗睡等待一个
                    # 永远不来的远拍"死循环（正常等待的下一拍至多领先一拍）
                    beat_index, next_beat_ms = next_beat_after(pos, bpm, offset)

                try:
                    speed = max(0.1, float(self._speed()))
                    lead_ms = max(0.0, float(self._output_latency_ms()))
                except Exception:
                    speed, lead_ms = 1.0, 0.0

                delta_ms = next_beat_ms - pos - lead_ms
                if delta_ms > _NEAR_MS:
                    # 粗睡：按剩余墙钟时间分档，留 2ms 余量转入临近阶段
                    remaining_s = delta_ms / 1000.0 / speed
                    if remaining_s > 0.5:
                        chunk = _SLEEP_FAR
                    elif remaining_s > 0.05:
                        chunk = _SLEEP_MID
                    else:
                        chunk = _SLEEP_NEAR
                    time.sleep(max(0.001, min(remaining_s - 0.002, chunk)))
                    continue

                # 临近：重查位置取得新锚点，折算精确墙钟目标后自旋等待
                now = time.perf_counter()
                pos2 = self._query_position()
                if pos2 is None:
                    continue
                target = now + max(0.0, next_beat_ms - pos2 - lead_ms) / 1000.0 / speed
                # 锚点后位置突变（如 seek 后退）→ 回到粗睡重新判定
                if target - now > (_NEAR_MS * 3) / 1000.0:
                    continue
                while time.perf_counter() < target:
                    pass

                # 触发前复查：stop()（置标志）与暂停（is_playing 转 False）
                # 都可能在自旋窗口内发生，不复查会多响一记
                with self._lock:
                    if not self._running or self._thread is not me:
                        return
                try:
                    if not bool(self._is_playing()):
                        return
                except Exception:
                    return
                if is_accent_beat(beat_index, beats_per_bar):
                    self._player.play_accent()
                else:
                    self._player.play_beat()
                beat_index += 1
                next_beat_ms = offset + beat_index * beat_ms
        finally:
            with self._lock:
                if self._thread is me:
                    self._running = False
                    self._thread = None
