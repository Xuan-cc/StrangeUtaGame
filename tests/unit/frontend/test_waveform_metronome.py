"""节拍器（波形高级窗口「网格与节拍」）单测。

覆盖五层：

1. 拍点数学 ``next_beat_after`` / 重音判定（与 BPM 网格绘制同口径）；
2. 齿轮弹窗的开关/音量收集、回填、信号与联动禁用；
3. ``WaveformDisplay``/``TimelineWidget`` 的设置透传与持久化信号；
4. ``PlaybackMetronome`` 调度线程（线性走时假引擎 + 假播放器）与节拍音资源；
5. 首音检测（``spectrum.first_sound_ms`` 与「对齐首音」按钮）。
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from strange_uta_game.frontend.editor.timing.playback_metronome import (
    PlaybackMetronome,
    is_accent_beat,
    next_beat_after,
)
from strange_uta_game.frontend.editor.timing.timeline_widget import (
    TimelineWidget,
    WaveformDisplay,
)

# ── 拍点数学 ─────────────────────────────────────────────────────────────────


class TestNextBeatAfter:
    def test_basic_no_offset(self):
        # 120BPM → 拍长 500ms；位置 260ms 的下一拍是拍 1（500ms）
        b, t = next_beat_after(260.0, 120.0, 0.0)
        assert b == 1
        assert t == pytest.approx(500.0)

    def test_positive_offset_phase(self):
        # 偏移 320ms：拍 0 在 320ms，位置 100ms 的下一拍是拍 0
        b, t = next_beat_after(100.0, 120.0, 320.0)
        assert b == 0
        assert t == pytest.approx(320.0)

    def test_negative_offset_phase(self):
        # 偏移 -320ms：拍刻 -320/180/680…，位置 100ms 的下一拍是拍 1（180ms）
        b, t = next_beat_after(100.0, 120.0, -320.0)
        assert b == 1
        assert t == pytest.approx(180.0)

    def test_exactly_on_beat_returns_next(self):
        """恰在拍点上：该拍归"已过"——在拍点处暂停再恢复不会立刻补响一记。"""
        b, t = next_beat_after(500.0, 120.0, 0.0)
        assert b == 2
        assert t == pytest.approx(1000.0)

    def test_position_before_first_beat(self):
        # 位置 0、偏移 500：第一拍（b=0）尚未到，下一拍即它
        b, t = next_beat_after(0.0, 120.0, 500.0)
        assert b == 0
        assert t == pytest.approx(500.0)

    def test_extreme_bpm(self):
        b, t = next_beat_after(250.0, 600.0, 0.0)  # 拍长 100ms
        assert b == 3
        assert t == pytest.approx(300.0)
        b, t = next_beat_after(1000.0, 10.0, 0.0)  # 拍长 6s
        assert b == 1
        assert t == pytest.approx(6000.0)

    def test_fractional_bpm_offset_matches_grid_formula(self):
        """拍时刻公式与波形 BPM 网格一致：offset + b·(60000/BPM)。"""
        bpm, offset = 113.0, 15.0
        beat = 60000.0 / bpm
        b, t = next_beat_after(1000.0, bpm, offset)
        assert b == int((1000.0 - offset) // beat) + 1
        assert t == pytest.approx(offset + b * beat)


class TestAccentBeat:
    def test_every_fourth_beat(self):
        assert is_accent_beat(0)
        assert is_accent_beat(4)
        assert is_accent_beat(8)
        assert not is_accent_beat(1)
        assert not is_accent_beat(3)
        assert not is_accent_beat(5)
        assert not is_accent_beat(7)

    def test_negative_beats_follow_grid_bar_lines(self):
        """网格对负 4 的倍数同样画小节线（不标注小节号），重音同口径。"""
        assert is_accent_beat(-4)
        assert is_accent_beat(-8)
        assert not is_accent_beat(-3)
        assert not is_accent_beat(-1)

    def test_time_signature_cycle(self):
        """3/4：每 3 拍一记重音（0,3,6…）；默认参数回退 4/4。"""
        assert is_accent_beat(0, 3)
        assert is_accent_beat(3, 3)
        assert is_accent_beat(6, 3)
        assert is_accent_beat(-3, 3)
        assert not is_accent_beat(1, 3)
        assert not is_accent_beat(2, 3)
        assert not is_accent_beat(4, 3)
        assert not is_accent_beat(5, 3)
        # 2/4 / 5/4
        assert is_accent_beat(2, 2)
        assert not is_accent_beat(3, 2)
        assert is_accent_beat(5, 5)
        assert not is_accent_beat(4, 5)

    def test_configure_accepts_and_clamps_beats_per_bar(self):
        player = _FakePlayer()
        met = PlaybackMetronome(
            player,
            position_ms=lambda: 0.0,
            is_playing=lambda: False,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(120.0, 0, 3)
        assert met._beats_per_bar == 3
        met.configure(120.0, 0, 99)
        assert met._beats_per_bar == 16  # 钳制上限
        met.configure(120.0, 0, 0)
        assert met._beats_per_bar == 1  # 钳制下限


# ── 齿轮弹窗 ─────────────────────────────────────────────────────────────────


class TestAdvancedDialogMetronome:
    @staticmethod
    def _make_dialog(initial, audio_source=None):
        from PyQt6.QtWidgets import QWidget

        from strange_uta_game.frontend.editor.timing.waveform_advanced_dialog import (
            WaveformAdvancedDialog,
        )

        parent = QWidget()  # 持引用避免 GC 连带删除对话框子件
        dialog = WaveformAdvancedDialog(initial, audio_source, parent=parent)
        dialog._test_parent_ref = parent
        return dialog

    def test_defaults_when_keys_absent(self, qapp):
        dialog = self._make_dialog({})
        collected = dialog._collect()
        assert collected["metronome_enabled"] is False
        assert collected["metronome_volume"] == 100

    def test_initial_roundtrip(self, qapp):
        dialog = self._make_dialog(
            {
                "metronome_enabled": True,
                "metronome_volume": 40,
                "grid_bpm": 113.0,
                "grid_offset_ms": 15,
            }
        )
        assert dialog.metronome_switch.isChecked()
        assert dialog.met_volume_slider.value() == 40
        collected = dialog._collect()
        assert collected["metronome_enabled"] is True
        assert collected["metronome_volume"] == 40
        assert collected["grid_bpm"] == 113.0
        assert collected["grid_offset_ms"] == 15

    def test_toggle_emits_applied(self, qapp):
        dialog = self._make_dialog({})
        emitted = []
        dialog.applied.connect(lambda d: emitted.append(dict(d)))
        dialog.metronome_switch.setChecked(True)
        assert len(emitted) == 1
        assert emitted[0]["metronome_enabled"] is True
        dialog.metronome_switch.setChecked(False)
        assert len(emitted) == 2
        assert emitted[1]["metronome_enabled"] is False

    def test_volume_slider_disabled_when_metronome_off(self, qapp):
        dialog = self._make_dialog({"metronome_enabled": False})
        assert not dialog.met_volume_slider.isEnabled()
        dialog.metronome_switch.setChecked(True)
        assert dialog.met_volume_slider.isEnabled()
        dialog.metronome_switch.setChecked(False)
        assert not dialog.met_volume_slider.isEnabled()

    def test_volume_clamped_to_range(self, qapp):
        dialog = self._make_dialog({"metronome_volume": 999})
        assert dialog.met_volume_slider.value() == 100
        assert dialog._collect()["metronome_volume"] == 100

    def test_volume_slider_release_applies(self, qapp):
        dialog = self._make_dialog({"metronome_enabled": True})
        emitted = []
        dialog.applied.connect(lambda d: emitted.append(dict(d)))
        dialog.met_volume_slider.setValue(70)
        assert dialog._collect()["metronome_volume"] == 70
        # 非拖动变更（程序 setValue/点轨道/键盘）立即应用，与其他滑条一致
        assert any(d.get("metronome_volume") == 70 for d in emitted)

    def test_time_signature_roundtrip_and_default(self, qapp):
        dialog = self._make_dialog({"beats_per_bar": 3})
        assert dialog.beats_per_bar_combo.currentText() == "3/4"
        assert dialog._collect()["beats_per_bar"] == 3

        # 键缺席 → 默认 4/4
        dialog2 = self._make_dialog({})
        assert dialog2._collect()["beats_per_bar"] == 4

        # 非法/超范围值回退 4/4
        dialog3 = self._make_dialog({"beats_per_bar": 99})
        assert dialog3._collect()["beats_per_bar"] == 4

    def test_time_signature_change_emits_applied(self, qapp):
        dialog = self._make_dialog({"beats_per_bar": 4})
        emitted = []
        dialog.applied.connect(lambda d: emitted.append(dict(d)))
        dialog.beats_per_bar_combo.setCurrentIndex(0)  # 2/4
        assert len(emitted) == 1
        assert emitted[0]["beats_per_bar"] == 2


# ── 设置透传（WaveformDisplay / TimelineWidget） ─────────────────────────────


class TestDisplayMetronomeState:
    def test_display_state_roundtrip(self, qapp):
        display = WaveformDisplay()
        s = display.display_settings()
        assert s["metronome_enabled"] is False
        assert s["metronome_volume"] == 100
        display.set_metronome_enabled(True)
        display.set_metronome_volume(55)
        s = display.display_settings()
        assert s["metronome_enabled"] is True
        assert s["metronome_volume"] == 55

    def test_volume_clamped(self, qapp):
        display = WaveformDisplay()
        display.set_metronome_volume(150)
        assert display.display_settings()["metronome_volume"] == 100

    def test_beats_per_bar_roundtrip_and_clamp(self, qapp):
        display = WaveformDisplay()
        assert display.display_settings()["beats_per_bar"] == 4
        display.set_beats_per_bar(3)
        assert display.display_settings()["beats_per_bar"] == 3
        display.set_beats_per_bar(99)
        assert display.display_settings()["beats_per_bar"] == 16
        display.set_beats_per_bar(0)
        assert display.display_settings()["beats_per_bar"] == 1

    def test_beats_per_bar_grid_paints_without_error(self, qapp):
        """3/4（非 2 幂拍号）下 BPM 网格（含小节线/小节号）绘制冒烟。"""
        display = WaveformDisplay()
        display.set_duration(4000)
        display.set_grid_mode("bpm")
        display.set_grid_bpm(120.0)
        display.set_beats_per_bar(3)
        display.resize(300, 150)
        layer = display._render_static_layer(300, 150, 0.0, 4000.0, 4000.0)
        assert not layer.isNull()

    def test_bar_lines_follow_beats_per_bar(self, qapp):
        """小节线间隔按拍号分子循环：3/4 的加重线落在第 0/3/6 拍，
        4/4 落在第 0/4 拍；小节号同步。直接以记录型 painter 验证绘制层。"""

        class _RecordingPainter:
            def __init__(self):
                self.lines = []  # (x, pen_width)
                self.texts = []  # (x, text)
                self._pen_width = 0

            def setPen(self, pen):
                # 小节号文字传的是 QColor（无 width），画线传 QPen
                width = getattr(pen, "width", None)
                self._pen_width = int(width()) if callable(width) else 0

            def drawLine(self, x1, _y1, _x2, _y2):
                self.lines.append((int(x1), self._pen_width))

            def drawText(self, x, _y, text):
                self.texts.append((int(x), text))

        def bar_lines_of(beats_per_bar: int):
            display = WaveformDisplay()
            display.set_grid_mode("bpm")
            display.set_grid_bpm(120.0)  # 拍长 500ms
            display.set_grid_line_width(2)
            display.set_beats_per_bar(beats_per_bar)
            painter = _RecordingPainter()
            # 视窗 0~3000ms（6 拍），beat_x(b) = b·500
            display._draw_bpm_grid(painter, 3000, 100, 0.0, 3000.0)
            bar_width = display._bpm_grid_widths()[2]  # 小节线宽 = 基准+1
            bar_xs = sorted(x for x, width in painter.lines if width == bar_width)
            texts = [text for _, text in sorted(painter.texts)]
            return bar_xs, texts

        # 3/4：小节线在第 0、3 拍（x=0/1500），两小节
        bar_xs, texts = bar_lines_of(3)
        assert bar_xs == [0, 1500]
        assert texts == ["1", "2"]

        # 4/4：小节线在第 0、4 拍（x=0/2000）
        bar_xs, texts = bar_lines_of(4)
        assert bar_xs == [0, 2000]
        assert texts == ["1", "2"]

        # 2/4：第 0、2、4 拍（x=0/1000/2000），三小节
        bar_xs, texts = bar_lines_of(2)
        assert bar_xs == [0, 1000, 2000]
        assert texts == ["1", "2", "3"]

    def test_timeline_apply_emits_and_holds_state(self, qapp):
        timeline = TimelineWidget()
        received = []
        timeline.display_settings_changed.connect(lambda d: received.append(dict(d)))

        timeline._apply_display_settings(
            {"metronome_enabled": True, "metronome_volume": 60, "beats_per_bar": 3}
        )
        wd = timeline.waveform_display.display_settings()
        assert wd["metronome_enabled"] is True
        assert wd["metronome_volume"] == 60
        assert wd["beats_per_bar"] == 3
        assert len(received) == 1
        assert received[0]["metronome_enabled"] is True
        assert received[0]["metronome_volume"] == 60
        assert received[0]["beats_per_bar"] == 3

        # 缺席键保持现值（兼容不含节拍器键的旧调用方）
        timeline._apply_display_settings({})
        wd = timeline.waveform_display.display_settings()
        assert wd["metronome_enabled"] is True
        assert wd["metronome_volume"] == 60
        assert wd["beats_per_bar"] == 3

        # 值未变不再发信号（避免无谓持久化）
        timeline._apply_display_settings(
            {"metronome_enabled": True, "metronome_volume": 60, "beats_per_bar": 3}
        )
        assert len(received) == 1


# ── 调度线程（假引擎 + 假播放器） ────────────────────────────────────────────


class _FakePlayer:
    def __init__(self):
        self.events = []  # (kind, fire_wall_time)

    def play_beat(self):
        self.events.append(("beat", time.perf_counter()))

    def play_accent(self):
        self.events.append(("accent", time.perf_counter()))


def _wait_until(predicate, timeout_s=5.0):
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class TestPlaybackMetronomeThread:
    def test_clicks_align_to_beats_with_accent_every_four(self):
        """线性走时假引擎（600BPM、拍长 100ms）：触发时刻对齐 100ms 整数倍，
        每 4 拍一记重音，连续触发间隔约一个拍长。"""
        player = _FakePlayer()
        start = time.perf_counter()

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0,
            is_playing=lambda: time.perf_counter() - start < 0.62,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(600.0, 0)
        met.start()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

        events = player.events
        assert len(events) >= 4  # 0.62s 窗口内至少 4-5 拍
        kinds = [kind for kind, _ in events]
        # 起播时刻线程启动有若干 ms 延迟，首个拍索引不固定；窗口内必然跨过
        # 至少一个 4 的倍数拍 → 恰好一记重音
        assert "accent" in kinds
        assert kinds.count("accent") == 1

        for kind, fired_at in events:
            rel_ms = (fired_at - start) * 1000.0
            # 触发时刻贴近某个拍点（100ms 整数倍）；CI 调度抖动留 30ms 容差
            assert abs(rel_ms - round(rel_ms / 100.0) * 100.0) <= 30.0, (
                f"{kind} fired at {rel_ms:.1f}ms"
            )
        gaps = [
            (events[i + 1][1] - events[i][1]) * 1000.0
            for i in range(len(events) - 1)
        ]
        assert all(70.0 <= g <= 130.0 for g in gaps), gaps

    def test_three_beats_per_bar_cycle(self):
        """拍号 3/4：触发序列以 3 为周期（重音-普通-普通循环），相位无关——
        不依赖线程启动延迟决定的首拍索引。"""
        player = _FakePlayer()
        start = time.perf_counter()

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0,
            is_playing=lambda: time.perf_counter() - start < 0.95,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(600.0, 0, 3)
        met.start()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

        events = player.events
        assert len(events) >= 5  # 0.95s / 100ms 拍长
        kinds = [kind for kind, _ in events]
        assert "accent" in kinds
        # 周期性：任意相隔 3 拍的触发音色相同（3/4 重音循环）
        for i in range(3, len(kinds)):
            assert kinds[i] == kinds[i - 3], kinds

    def test_stop_halts_scheduling(self):
        player = _FakePlayer()
        start = time.perf_counter()
        playing = {"on": True}

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0,
            is_playing=lambda: playing["on"],
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(600.0, 0)
        met.start()
        assert _wait_until(lambda: len(player.events) >= 2, timeout_s=5.0)

        met.stop()
        # stop 只置标志不 join；给线程一个等待周期自退（含可能已在途的一拍）
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)
        time.sleep(0.15)
        count_after_stop = len(player.events)
        time.sleep(0.2)
        assert len(player.events) == count_after_stop

    def test_start_recovers_from_zombie_running_flag(self):
        """_running 为真但线程已死（未预见路径）时，start() 必须重新拉起线程。

        换项目/切歌等过渡态若让线程异常终止而标志未复位，旧的幂等分支
        只会递增 generation、不建线程——表现为节拍器永久静默。
        """
        player = _FakePlayer()
        start = time.perf_counter()

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0,
            is_playing=lambda: time.perf_counter() - start < 1.0,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(600.0, 0)
        # 直接伪造僵尸态：标志为真、无线程（绕过 finally 复位）
        met._running = True
        met._thread = None

        met.start()
        assert _wait_until(lambda: len(player.events) >= 2, timeout_s=5.0)
        met.stop()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

    def test_backward_position_jump_realigns(self):
        """位置向后跳变（换音频把位置重置回 0）且无 resync 时，调度器必须
        按新时间轴重新对齐，而不是继续等旧的远拍（拍点错位/长期无声）。"""
        player = _FakePlayer()
        start = time.perf_counter()
        rewind_at_s = 0.7

        def position_ms():
            elapsed = time.perf_counter() - start
            if elapsed < rewind_at_s:
                return elapsed * 1000.0
            return (elapsed - rewind_at_s) * 1000.0  # 跳回 0 重新走时

        met = PlaybackMetronome(
            player,
            position_ms=position_ms,
            is_playing=lambda: time.perf_counter() - start < 1.5,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(300.0, 0)  # 200ms 一拍
        met.start()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

        assert len(player.events) >= 3
        # 跳变之后的触发必须落在"新时间轴"的拍点（rewind 后 200ms 的倍数）
        for kind, fired_at in player.events:
            rel_after_rewind_ms = (fired_at - start) * 1000.0 - rewind_at_s * 1000.0
            if rel_after_rewind_ms < 0:
                continue  # 跳变前正常触发的拍
            assert abs(
                rel_after_rewind_ms - round(rel_after_rewind_ms / 200.0) * 200.0
            ) <= 30.0, (
                f"{kind} fired at rewind+{rel_after_rewind_ms:.1f}ms (not on new grid)"
            )
        # 跳变后确有新拍触发（不是停在远拍上无声）
        after = [
            fired_at
            for _, fired_at in player.events
            if (fired_at - start) >= rewind_at_s
        ]
        assert len(after) >= 2

    def test_speed_spaces_out_wall_time(self):
        """0.5× 变速：拍点仍在正确的媒体时刻（500ms 拍长 → 墙钟 1s 间隔）。"""
        player = _FakePlayer()
        start = time.perf_counter()

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0 * 0.5,
            is_playing=lambda: time.perf_counter() - start < 0.85,
            speed=lambda: 0.5,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(120.0, 0)
        met.start()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

        # 0.85s 墙钟 → 媒体 425ms → 只触发拍 0（媒体 0ms 后第一拍在 500ms？
        # 起播位置略大于 0，首拍为 b=0 之后的下一拍；0.85s 窗口内至多 1 拍）
        assert len(player.events) <= 1

    def test_not_playing_starts_nothing(self):
        player = _FakePlayer()
        met = PlaybackMetronome(
            player,
            position_ms=lambda: 0.0,
            is_playing=lambda: False,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.start()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)
        assert player.events == []

    def test_restart_after_self_stop(self):
        """线程因播放结束自退后，start() 能再次拉起（_running 被可靠复位）。"""
        player = _FakePlayer()
        playing = {"on": True}
        start = time.perf_counter()

        met = PlaybackMetronome(
            player,
            position_ms=lambda: (time.perf_counter() - start) * 1000.0,
            is_playing=lambda: playing["on"],
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        met.configure(600.0, 0)
        met.start()
        playing["on"] = False
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)

        playing["on"] = True
        met.start()
        assert met.is_running
        assert _wait_until(lambda: len(player.events) >= 1, timeout_s=5.0)
        met.stop()
        assert _wait_until(lambda: not met.is_running, timeout_s=5.0)


# ── 节拍音资源与播放器 ───────────────────────────────────────────────────────


class TestMetronomeSounds:
    def test_sound_files_exist_and_readable(self):
        import soundfile as sf

        from strange_uta_game.backend.infrastructure.audio.metronome_player import (
            metronome_sound_paths,
        )

        beat_path, accent_path = metronome_sound_paths()
        for path, min_duration_ms in ((beat_path, 30), (accent_path, 30)):
            assert path.is_file(), f"missing metronome sound: {path}"
            data, sr = sf.read(str(path), dtype="float32")
            assert sr == 44100
            assert len(data) / sr * 1000 >= min_duration_ms
            assert float(abs(data).max()) > 0.1  # 非静音

    def test_sounddevice_fallback_loads(self):
        """mac 回退实现：load 后 is_loaded，接口齐全（无需真实输出设备）。"""
        from strange_uta_game.backend.infrastructure.audio.metronome_player import (
            SoundDeviceMetronomePlayer,
            metronome_sound_paths,
        )

        player = SoundDeviceMetronomePlayer()
        assert not player.is_loaded()
        player.load(*metronome_sound_paths())
        assert player.is_loaded()
        player.set_volume(80)
        assert player.get_volume() == 80
        player.invalidate()  # 无操作，不应抛
        player.free()
        assert not player.is_loaded()

    def test_bass_player_loads_when_available(self):
        from strange_uta_game.backend.infrastructure.audio import bass_available

        if not bass_available:
            pytest.skip("BASS 仅 Windows 可用")
        from strange_uta_game.backend.infrastructure.audio.metronome_player import (
            MetronomePlayer,
            metronome_sound_paths,
        )

        player = MetronomePlayer()
        player.load(*metronome_sound_paths())
        if not player.is_loaded():
            pytest.skip("CI 无音频输出设备，BASS_Init 不可用")
        player.set_volume(90)
        assert player.get_volume() == 90
        player.play_beat()  # 冒烟：不抛即通过
        player.free()
        assert not player.is_loaded()


# ── 首音检测（「对齐首音」按钮 + spectrum.first_sound_ms） ──────────────────


class TestFirstSound:
    SR = 44100

    @staticmethod
    def _tone(seconds: float, amp: float = 0.3, freq: float = 440.0):
        t = np.arange(int(44100 * seconds)) / 44100
        return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)

    def test_leading_silence_detected(self):
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        samples = np.concatenate([np.zeros(44100, np.float32), self._tone(1.0)])
        ms = first_sound_ms(samples, 44100)
        assert ms is not None
        assert 990 <= ms <= 1005  # 窗口粒度 ≈2.9ms

    def test_starts_loud_returns_near_zero(self):
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        assert first_sound_ms(self._tone(1.0), 44100) <= 10

    def test_all_silence_returns_none(self):
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        assert first_sound_ms(np.zeros(44100 * 2, np.float32), 44100) is None

    def test_faint_noise_below_floor_returns_none(self):
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        rng = np.random.default_rng(7)
        noise = (rng.standard_normal(44100) * 0.0005).astype(np.float32)
        assert first_sound_ms(noise, 44100) is None

    def test_too_short_returns_none(self):
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        assert first_sound_ms(np.zeros(64, np.float32), 44100) is None

    def test_quiet_vocal_onset_not_missed(self):
        """开头人声弱起音（低电平）不因后段变响而被相对阈值漏检。"""
        from strange_uta_game.backend.infrastructure.audio.spectrum import (
            first_sound_ms,
        )

        samples = np.concatenate(
            [
                np.zeros(22050, np.float32),        # 0.5s 静音
                self._tone(0.2, amp=0.02),          # 弱起音
                self._tone(1.5, amp=0.5),           # 后段变响
            ]
        )
        ms = first_sound_ms(samples, 44100)
        assert ms is not None
        assert 490 <= ms <= 510


class TestAlignFirstSoundDialog:
    @staticmethod
    def _make_dialog(initial, audio_source=None):
        from PyQt6.QtWidgets import QWidget

        from strange_uta_game.frontend.editor.timing.waveform_advanced_dialog import (
            WaveformAdvancedDialog,
        )

        parent = QWidget()
        dialog = WaveformAdvancedDialog(initial, audio_source, parent=parent)
        dialog._test_parent_ref = parent
        return dialog

    @staticmethod
    def _audio_with_leading_silence(silence_s: float = 1.0):
        sr = 44100
        t = np.arange(sr) / sr
        tone = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        return np.concatenate([np.zeros(int(sr * silence_s), np.float32), tone]), sr

    def test_button_follows_audio_source_availability(self, qapp):
        dialog = self._make_dialog({})
        assert not dialog.btn_align_first.isEnabled()
        dialog.set_audio_source(self._audio_with_leading_silence())
        assert dialog.btn_align_first.isEnabled()
        dialog.set_audio_source(None)
        assert not dialog.btn_align_first.isEnabled()

    def test_click_sets_offset_and_applies(self, qapp):
        dialog = self._make_dialog({"grid_offset_ms": 0},
                                   audio_source=self._audio_with_leading_silence())
        emitted = []
        dialog.applied.connect(lambda d: emitted.append(dict(d)))
        dialog._on_align_first_sound()

        value = int(dialog.grid_offset_edit.text())
        assert 990 <= value <= 1005
        assert dialog._collect()["grid_offset_ms"] == value
        assert len(emitted) == 1
        assert emitted[0]["grid_offset_ms"] == value
        assert "已对齐首音" in dialog.bpm_status.text()

    def test_all_silence_keeps_offset_unchanged(self, qapp):
        dialog = self._make_dialog(
            {"grid_offset_ms": 123},
            audio_source=(np.zeros(44100 * 2, np.float32), 44100),
        )
        dialog._on_align_first_sound()
        assert dialog.grid_offset_edit.text() == "123"
        assert "未检测到" in dialog.bpm_status.text()


# ── 样本失效自愈（换项目/切歌等过渡路径） ────────────────────────────────────


class _HealFakePlayer:
    """记录 load 调用的假播放器：invalidate 后 is_loaded 为假。"""

    def __init__(self):
        self._loaded = False
        self.load_calls = []
        self.volume_pct = None

    def is_loaded(self):
        return self._loaded

    def load(self, beat_path, accent_path):
        self.load_calls.append((beat_path, accent_path))
        self._loaded = True

    def set_volume(self, volume_pct):
        self.volume_pct = volume_pct

    def play_beat(self):
        pass

    def play_accent(self):
        pass

    def invalidate(self):
        self._loaded = False


class TestMetronomeSampleSelfHeal:
    """BASS 会话重建（换项目切歌/设备恢复）后样本 handle 失效——若该路径
    未经过 _on_audio_loaded 的重载，configure/播放动作必须能自愈重载，
    否则节拍器永久静默（与按键音 _apply_settings 的 samples_invalid 兜底同款）。"""

    @staticmethod
    def _make_fake_editor(player, settings_values=None):
        from types import SimpleNamespace

        from strange_uta_game.frontend.editor.timing_interface import EditorInterface

        values = dict(settings_values or {})

        class _Settings:
            def get(self, path, default=None):
                return values.get(path, default)

        met = PlaybackMetronome(
            player,
            position_ms=lambda: 0.0,
            is_playing=lambda: False,
            speed=lambda: 1.0,
            output_latency_ms=lambda: 0.0,
        )
        fake = SimpleNamespace(
            _metronome=met,
            _metronome_player=player,
            _timing_service=None,
            _get_setting_interface=lambda: SimpleNamespace(
                get_settings=lambda: _Settings()
            ),
            _metronome_is_playing=lambda: False,
        )
        fake._reload_metronome_after_audio = lambda: (
            EditorInterface._reload_metronome_after_audio(fake)
        )
        fake._ensure_metronome_samples = lambda: (
            EditorInterface._ensure_metronome_samples(fake)
        )
        return fake

    def test_ensure_samples_reloads_only_when_invalid(self):
        from strange_uta_game.backend.infrastructure.audio.metronome_player import (
            metronome_sound_paths,
        )
        from strange_uta_game.frontend.editor.timing_interface import EditorInterface

        player = _HealFakePlayer()
        fake = self._make_fake_editor(player)

        player.load(*metronome_sound_paths())  # 预热（计入 load_calls）
        player.load_calls.clear()
        assert player.is_loaded()
        EditorInterface._ensure_metronome_samples(fake)
        assert len(player.load_calls) == 0  # 已加载：不重载

        player.invalidate()  # 模拟 BASS 会话重建后 handle 归零
        assert not player.is_loaded()
        EditorInterface._ensure_metronome_samples(fake)
        assert len(player.load_calls) == 1  # 失效：自愈重载
        assert player.load_calls[0] == metronome_sound_paths()
        assert player.is_loaded()

        EditorInterface._ensure_metronome_samples(fake)
        assert len(player.load_calls) == 1  # 幂等：不重复加载

    def test_configure_from_settings_heals_invalidated_samples(self):
        from strange_uta_game.frontend.editor.timing_interface import EditorInterface

        player = _HealFakePlayer()
        player._loaded = True  # 假设曾加载
        fake = self._make_fake_editor(
            player,
            {
                "timing.waveform_metronome_enabled": True,
                "timing.waveform_grid_bpm": 128.0,
                "timing.waveform_grid_offset_ms": 25,
                "timing.waveform_metronome_volume": 65,
            },
        )
        player.invalidate()  # 换项目过渡态：样本失效，未经过 _on_audio_loaded

        EditorInterface._configure_metronome_from_settings(fake)
        assert len(player.load_calls) == 1  # 自愈
        assert fake._metronome_enabled is True
        assert player.volume_pct == 65
        assert fake._metronome._bpm == 128.0
        assert fake._metronome._offset_ms == 25.0
