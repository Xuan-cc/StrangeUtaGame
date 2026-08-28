from __future__ import annotations

import time

import numpy as np
import pytest
from PyQt6.QtCore import Qt

from strange_uta_game.frontend.editor.timing.timeline_widget import (
    TimelineWidget,
    WaveformDisplay,
)

SR = 44100


def _tone(seconds: float = 2.0, freq: float = 440.0) -> np.ndarray:
    t = np.arange(int(SR * seconds), dtype=np.float64) / SR
    return (0.6 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


class TestDisplayMode:
    def test_default_display_height_is_common(self, qapp):
        """显示高度是波形/声谱公共属性：期望经 sizeHint 表达（默认 120 =
        旧版不可调时的波形窗口高度）。

        minimumHeight 恒为硬下限 80——空间不足时布局压缩显示区，
        而不是把顶层窗口撑出屏幕（P1：曾实测请求 713px 窗口被撑到 855px）。
        """
        display = WaveformDisplay()
        assert display.display_settings()["display_mode"] == "waveform"
        assert display.sizeHint().height() == 120
        assert display.minimumHeight() == 120  # 领取空间（Expanding 预览会吃掉 hint）
        display.set_display_mode("spectrum")
        assert display.sizeHint().height() == 120

    def test_display_height_adjustable_both_modes(self, qapp):
        display = WaveformDisplay()
        display.set_spectrum_params(display_height=300)
        assert display.sizeHint().height() == 300
        assert display.minimumHeight() == 300  # 领取空间
        display.set_display_mode("spectrum")
        assert display.sizeHint().height() == 300
        display.set_display_mode("waveform")
        assert display.sizeHint().height() == 300  # 公共属性，切换模式不重置

    def test_invalid_mode_is_ignored(self, qapp):
        display = WaveformDisplay()
        display.set_display_mode("nonsense")
        assert display.display_settings()["display_mode"] == "waveform"

    def test_spectrum_background_pipeline_and_view(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(2.0), SR, 1)
        assert display._spectrum_state == "computing"
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        display.resize(400, 200)

        view = display._compute_spectrum_view(400, 120)

        assert view is not None
        assert view.shape == (400, 120)
        assert view.dtype == np.uint8
        # 440Hz 正弦在图内应有可见能量（量化值远高于地板）
        assert int(view.max()) > 60

    def test_pyramid_is_built_in_worker(self, qapp, qtbot):
        """金字塔应在 worker 线程构建（小音频在预算内 → levels 非空）。"""
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(2.0), SR, 1)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum["levels"] is not None
        assert len(display._spectrum["levels"]) >= 1

    def test_lazy_level_fallback_when_pyramid_absent(self, qapp, qtbot, monkeypatch):
        """超预算路径：视图渲染绝不同步调用 build_level（monkeypatch 置雷）。"""
        from strange_uta_game.backend.infrastructure.audio import spectrum

        display = WaveformDisplay()
        display.set_duration(12000)
        display.set_zoom(1.0)  # 默认 50x 只显示 240ms，选层会落在第 0 层
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(12.0), SR, 1)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        spec = display._spectrum
        assert spec["levels"] is not None
        # 模拟超预算形态：levels=None + worker 后台备好的粗层金字塔
        mid, coarse = spectrum.coarse_pyramid_for_budget(spec["matrix"])
        spec["levels"] = None
        spec["coarse_mid"] = mid
        spec["coarse_levels"] = coarse
        display._spectrum_view_cache = None
        display.resize(200, 120)

        def _boom(*_a, **_k):
            raise AssertionError("UI 绘制路径不得同步构建完整层")

        monkeypatch.setattr(spectrum, "build_level", _boom)
        view = display._compute_spectrum_view(200, 100)

        assert view is not None
        assert view.shape == (200, 100)

    def test_switching_to_waveform_cancels_compute_keeps_cache(self, qapp, qtbot):
        """切回波形模式：取消在途计算（省 CPU），保留已完成缓存便于快速切回。"""
        display = WaveformDisplay()
        display.set_duration(30000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(30.0), SR, 1)
        assert display._spectrum_state == "computing"

        display.set_display_mode("waveform")
        assert display._spectrum_worker is None  # 已取消
        assert display._spectrum_state == "idle"
        assert display._spectrum is None  # 未完成的任务没有缓存

        display.set_display_mode("spectrum")  # 重新进入：重新计算
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        display.set_display_mode("waveform")
        assert display._spectrum is not None  # 已完成的缓存保留

    def test_set_spectrum_active_controls_compute(self, qapp, qtbot):
        """波形区隐藏时取消在途计算；重新显示时按需恢复。"""
        display = WaveformDisplay()
        display.set_duration(30000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(30.0), SR, 1)
        assert display._spectrum_state == "computing"

        display.set_spectrum_active(False)
        assert display._spectrum_worker is None

        display.set_spectrum_active(True)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum is not None

    def test_display_height_preference_survives_small_window(self, qapp):
        """小窗口压缩只影响实际显示（minimumHeight 恒 80），期望不被覆盖。"""
        display = WaveformDisplay()
        display.set_spectrum_params(display_height=380)
        assert display.minimumHeight() == 380  # 领取空间
        assert display.sizeHint().height() == 380  # 期望保留
        assert display._display_height == 380

    def test_tag_handle_centered_both_modes(self, qapp):
        """把手恒在显示区中央（Tag 竖线中部，方便点击）——任意模式/高度一致。"""
        display = WaveformDisplay()
        for mode in ("waveform", "spectrum"):
            display.set_display_mode(mode)
            for height in (220, 400):
                assert display._handle_center_y(height) == height / 2.0

    def test_cancel_returns_immediately_and_can_restart(self, qapp, qtbot):
        """UI 路径取消不得同步 wait（冻结界面），且取消后可立即重启计算。"""
        display = WaveformDisplay()
        display.set_duration(30000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(30.0), SR, 1)
        assert display._spectrum_state == "computing"

        started = time.perf_counter()
        display._reset_spectrum_cache()  # 内部走 _cancel_spectrum_worker
        elapsed = time.perf_counter() - started
        assert elapsed < 0.5

        display._ensure_spectrum()
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum is not None

    def test_dyn_range_floor_direction(self, qapp):
        """动态范围 R 的可见下沿应在 -R dB（u≈(128-R)·255/128），方向不能反。"""
        display = WaveformDisplay()
        lut = display._spectrum_lut()  # 默认 90dB
        floor = int(round((128 - 90) * 255.0 / 128.0))
        bg = (240, 240, 240)  # 测试环境为浅色主题
        assert tuple(lut[floor - 1][:3]) == bg  # 低于地板 → 背景
        assert tuple(lut[floor][:3]) != bg      # 地板之上进入色带
        assert tuple(lut[floor][:3]) == (0, 0, 4)  # 地板=渐变底部（拉伸）
        assert tuple(lut[255][:3]) == (252, 255, 164)

    def test_view_cache_reused_for_same_window(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(2.0), SR, 1)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        display.resize(400, 200)

        first = display._compute_spectrum_view(400, 120)
        second = display._compute_spectrum_view(400, 120)

        assert first is second

    def test_fft_change_triggers_recompute(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(2.0), SR, 1)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        old_matrix = display._spectrum["matrix"]  # 强引用防 id 地址复用误判

        display.set_spectrum_params(fft_size=1024)

        assert display._spectrum is None  # 缓存已作废，等待重算
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum["matrix"] is not old_matrix
        assert display._spectrum["fft_size"] == 1024

    def test_static_layer_paints_in_both_modes(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_audio_data(_tone(2.0), SR, 1)
        display.resize(300, 150)

        layer_waveform = display._render_static_layer(300, 150, 0.0, 2000.0, 2000.0)
        display.set_display_mode("spectrum")
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        layer_spectrum = display._render_static_layer(300, 150, 0.0, 2000.0, 2000.0)

        assert not layer_waveform.isNull()
        assert not layer_spectrum.isNull()


class TestBpmGrid:
    def test_grid_settings_roundtrip(self, qapp):
        display = WaveformDisplay()
        display.set_grid_mode("bpm")
        display.set_grid_bpm(137.5)
        settings = display.display_settings()
        assert settings["grid_mode"] == "bpm"
        assert settings["grid_bpm"] == 137.5

    def test_bpm_grid_paints_without_error(self, qapp):
        display = WaveformDisplay()
        display.set_duration(4000)
        display.set_grid_mode("bpm")
        display.set_grid_bpm(120.0)
        display.resize(300, 150)

        layer = display._render_static_layer(300, 150, 0.0, 4000.0, 4000.0)

        assert not layer.isNull()


class TestTimelineWidgetIntegration:
    def test_gear_button_and_display_settings_signal(self, qapp):
        timeline = TimelineWidget()
        assert hasattr(timeline, "btn_waveform_settings")

        received = []
        timeline.display_settings_changed.connect(lambda d: received.append(dict(d)))

        timeline._apply_display_settings({
            "display_mode": "spectrum",
            "grid_mode": "bpm",
            "grid_bpm": 128.0,
            "spectrum_fft_size": 2048,
            "spectrum_freq_scale": "log",
            "spectrum_dyn_range_db": 90,
            "display_height": 220,
        })

        assert timeline.display_settings()["display_mode"] == "spectrum"
        assert len(received) == 1
        assert received[0]["display_mode"] == "spectrum"

        # 重复施加相同设置不再发信号（避免无谓持久化）
        timeline._apply_display_settings({
            "display_mode": "spectrum",
            "grid_mode": "bpm",
            "grid_bpm": 128.0,
            "spectrum_fft_size": 2048,
            "spectrum_freq_scale": "log",
            "spectrum_dyn_range_db": 90,
            "display_height": 220,
        })
        assert len(received) == 1

    def test_settings_dialog_nonmodal_and_single_instance(self, qapp, qtbot):
        """齿轮弹窗：普通非模态窗口，重复点击复用同一实例，销毁后清引用。"""
        timeline = TimelineWidget()
        timeline._on_waveform_settings_clicked()

        dialog = timeline._advanced_dialog
        assert dialog is not None
        assert not dialog.isModal()
        assert dialog.windowModality() == Qt.WindowModality.NonModal
        assert dialog.isVisible()

        timeline._on_waveform_settings_clicked()
        assert timeline._advanced_dialog is dialog

        dialog.deleteLater()
        qtbot.waitUntil(lambda: timeline._advanced_dialog is None, timeout=5000)


class TestAdvancedDialog:
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

    def test_dialog_is_plain_nonmodal_window(self, qapp):
        from PyQt6.QtWidgets import QDialog

        dialog = self._make_dialog({"display_mode": "waveform"})
        assert isinstance(dialog, QDialog)
        assert not dialog.isModal()
        assert dialog.windowModality() == Qt.WindowModality.NonModal

    def test_dialog_loads_initial_and_collects(self, qapp):
        initial = {
            "display_mode": "spectrum",
            "grid_mode": "bpm",
            "grid_bpm": 128.0,
            "spectrum_fft_size": 1024,
            "spectrum_freq_scale": "linear",
            "spectrum_dyn_range_db": 100,
            "display_height": 260,
        }
        dialog = self._make_dialog(initial)

        assert dialog.radio_spectrum.isChecked()
        assert dialog.radio_grid_bpm.isChecked()
        collected = dialog._collect()
        assert collected["display_mode"] == "spectrum"
        assert collected["grid_mode"] == "bpm"
        assert collected["grid_bpm"] == 128.0
        assert collected["spectrum_fft_size"] == 1024
        assert collected["spectrum_freq_scale"] == "linear"
        assert collected["spectrum_dyn_range_db"] == 100
        assert collected["display_height"] == 260

    def test_detect_button_disabled_without_audio(self, qapp):
        dialog = self._make_dialog(
            {"display_mode": "waveform"}, audio_source=None
        )
        assert not dialog.btn_detect_bpm.isEnabled()

    def test_set_audio_source_reenables_detect_button(self, qapp):
        dialog = self._make_dialog({"display_mode": "waveform"}, audio_source=None)
        dialog.set_audio_source((np.zeros(100, np.float32), SR))
        assert dialog.btn_detect_bpm.isEnabled()
        dialog.set_audio_source(None)
        assert not dialog.btn_detect_bpm.isEnabled()

    def test_reopen_same_audio_can_detect_again(self, qapp):
        """P2-1：检测中关闭窗口 → 相同音源重开 → 按钮恢复、可再次检测。

        set_audio_source 对相同音源（对象身份相同）且任务已停止时会提前
        返回——取消路径必须自己恢复按钮，否则永久禁用。
        """
        source = (np.zeros(SR, np.float32), SR)
        dialog = self._make_dialog(
            {"display_mode": "waveform"}, audio_source=source
        )
        dialog._on_detect_bpm()
        assert dialog._bpm_running is True
        assert not dialog.btn_detect_bpm.isEnabled()

        dialog.show()
        dialog.hide()  # 触发 hideEvent → _cancel_bpm_worker
        assert dialog._bpm_running is False
        assert dialog.btn_detect_bpm.isEnabled()

        dialog.set_audio_source(source)  # 相同音源：提前返回路径
        assert dialog.btn_detect_bpm.isEnabled()

        dialog._on_detect_bpm()  # 可以再次检测
        assert dialog._bpm_running is True
        dialog._cancel_bpm_worker()  # 清理第二个任务

    def test_mode_switch_emits_applied_once(self, qapp):
        """P3-1：toggled 双触发（旧按钮取消+新按钮选中）只提交一次。"""
        dialog = self._make_dialog({"display_mode": "waveform"})
        received = []
        dialog.applied.connect(lambda d: received.append(d["display_mode"]))
        dialog.radio_spectrum.setChecked(True)
        assert received == ["spectrum"]
        dialog.radio_waveform.setChecked(True)
        assert received == ["spectrum", "waveform"]

    def test_grid_switch_emits_applied_once(self, qapp):
        dialog = self._make_dialog({"display_mode": "waveform"})
        received = []
        dialog.applied.connect(lambda d: received.append(d["grid_mode"]))
        dialog.radio_grid_bpm.setChecked(True)
        assert received == ["bpm"]
        dialog.radio_grid_time.setChecked(True)
        assert received == ["bpm", "time"]

    def test_bpm_progress_updates_status(self, qapp):
        dialog = self._make_dialog({"display_mode": "waveform"})
        dialog._bpm_running = True
        dialog._on_bpm_progress(0.42)
        assert "42%" in dialog.bpm_status.text()

    def test_set_height_cap_reports_actual_without_overriding(self, qapp):
        """cap 仅用于提示：期望高度（滑条值/持久化）不被临时窗口大小覆盖。"""
        dialog = self._make_dialog({"display_mode": "spectrum"})
        received = []
        dialog.applied.connect(lambda d: received.append(d))
        dialog.set_height_cap(240)
        assert dialog.height_slider.value() == 120  # 期望（默认）不变
        assert dialog.height_slider.maximum() == 400  # 期望域固定
        assert received == []  # 不因 cap 触发持久化
        dialog.height_slider.setValue(320)
        dialog.set_height_cap(200)  # 窗口变小：仅提示实际显示高度
        assert dialog.height_slider.value() == 320  # 期望不被覆盖
        assert "200" in dialog.height_caption.text()

    def test_audio_source_switch_a_to_b_no_crash(self, qapp):
        """P1：音源 tuple 含 ndarray——A 歌切 B 歌不得触发元素比较异常。"""
        samples_a = np.zeros(100, np.float32)
        samples_b = np.ones(100, np.float32)
        dialog = self._make_dialog(
            {"display_mode": "waveform"}, audio_source=(samples_a, SR)
        )
        dialog.set_audio_source((samples_b, SR))  # 曾抛 ValueError
        assert dialog._audio_source == (samples_b, SR) or dialog._audio_source[0] is samples_b
        # 同一音源重复推送：跳过（身份相同）
        dialog.set_audio_source((samples_b, SR))

    def test_stale_bpm_result_filtered_by_relay(self, qapp):
        """P2：取消 A 后启动 B，A 的迟到结果不得污染 B 的状态。"""
        from strange_uta_game.frontend.editor.timing.task_runner import TaskRelay

        dialog = self._make_dialog(
            {"display_mode": "waveform"}, audio_source=(np.zeros(10, np.float32), SR)
        )

        class _W:
            def request_cancel(self):
                pass

        worker_a, worker_b = _W(), _W()
        relay_a = TaskRelay(
            dialog,
            lambda w=worker_a: dialog.is_task_current(worker_a),
            on_finished=dialog._on_bpm_result,
        )
        dialog._bpm_running = True
        dialog._bpm_worker = worker_b  # B 正在检测

        relay_a._finished({"bpm": 999.0, "confidence": 0.9})  # A 迟到到达中继

        assert dialog.bpm_spin.value() != 999.0
        assert dialog._bpm_worker is worker_b  # B 的引用未被覆盖

    def test_bpm_range_and_commit_timing(self, qapp):
        """BPM 范围 10–600；输入期间不应用，editingFinished 一次性提交。"""
        dialog = self._make_dialog({"display_mode": "waveform", "grid_bpm": 90.0})
        assert dialog.bpm_spin.minimum() == 10.0
        assert dialog.bpm_spin.maximum() == 600.0
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        dialog.bpm_spin.setValue(180.0)  # 逐键输入：不立即应用
        assert received == []

        dialog.bpm_spin.editingFinished.emit()  # 失焦/回车确认
        assert len(received) == 1
        assert received[0]["grid_bpm"] == 180.0

    def test_bpm_auto_detect_commits_explicitly(self, qapp):
        """自动检测 setValue 后必须显式提交（editingFinished 不会自动触发）。"""
        dialog = self._make_dialog({"display_mode": "waveform"})
        dialog._bpm_running = True
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        dialog._on_bpm_result({"bpm": 128.0, "confidence": 0.8})

        assert dialog.bpm_spin.value() == 128.0
        assert received and received[-1]["grid_bpm"] == 128.0

    def test_retranslate_covers_all_static_labels(self, qapp):
        """P2：语言切换重译必须覆盖全部 GroupBox 标题与参数标签。"""
        dialog = self._make_dialog({"display_mode": "spectrum"})
        for widget in (dialog._mode_group, dialog._grid_group, dialog._params_group):
            widget.setTitle("XX")
        for label in (dialog._lbl_fft, dialog._lbl_scale,
                      dialog._lbl_dyn, dialog._lbl_height):
            label.setText("XX")

        dialog._retranslate()

        assert dialog._mode_group.title() == "显示模式"
        assert dialog._grid_group.title() == "网格与节拍"
        assert dialog._params_group.title() == "声谱参数"
        assert dialog._lbl_fft.text() == "FFT 窗口"
        assert dialog._lbl_scale.text() == "频率刻度"
        assert dialog._lbl_dyn.text() == "动态范围"
        assert dialog._lbl_height.text() == "显示高度"

    def test_audio_source_switch_cancels_and_disables(self, qapp):
        """切歌：取消在途检测、旧结果不更新 UI、按钮随新音源重置。"""
        class _StubWorker:
            cancelled = False

            def request_cancel(self):
                self.cancelled = True

        stub = _StubWorker()
        dialog = self._make_dialog(
            {"display_mode": "waveform"},
            audio_source=(np.zeros(100, np.float32), SR),
        )
        dialog._bpm_running = True
        dialog._bpm_worker = stub

        dialog.set_audio_source(None)  # 清空音频

        assert stub.cancelled  # 旧任务已取消
        assert dialog._bpm_worker is None
        assert not dialog._bpm_running
        assert not dialog.btn_detect_bpm.isEnabled()
        # 迟到的旧结果（running 已复位）不得更新 UI
        dialog.bpm_status.setText("")
        dialog._on_bpm_result({"bpm": 999.0, "confidence": 0.9})
        assert dialog.bpm_status.text() == ""
        assert dialog.bpm_spin.value() != 999.0

        dialog.set_audio_source((np.zeros(100, np.float32), SR))
        assert dialog.btn_detect_bpm.isEnabled()

    def test_params_enabled_only_in_spectrum_mode(self, qapp):
        dialog = self._make_dialog({"display_mode": "waveform"})
        assert not dialog.fft_combo.isEnabled()
        dialog.radio_spectrum.setChecked(True)
        assert dialog.fft_combo.isEnabled()


class TestSettingsDefaults:
    def test_new_timing_keys_have_defaults(self):
        from strange_uta_game.frontend.settings.app_settings import AppSettings

        timing = AppSettings.DEFAULT_SETTINGS["timing"]
        expected = {
            "waveform_visible": True,
            "waveform_display_mode": "waveform",
            "waveform_grid_mode": "time",
            "waveform_grid_bpm": 120.0,
            "spectrum_fft_size": 2048,
            "spectrum_freq_scale": "log",
            "spectrum_dyn_range_db": 90,
            "display_height": 120,
        }
        for key, value in expected.items():
            assert timing.get(key) == value, key


class TestOwnerDestructionCrashSafety:
    """P1：owner 销毁不得崩溃——Qt 原生崩溃会终止整个进程，须子进程验证。"""

    @staticmethod
    def _run_probe(mode: str) -> int:
        import subprocess
        import sys
        from pathlib import Path

        probe = Path(__file__).parent / "_spectrum_crash_probe.py"
        root = Path(__file__).parents[3]
        result = subprocess.run(
            [sys.executable, str(probe), mode],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60,
        )
        return result.returncode

    def test_destroy_display_during_spectrum_compute(self):
        assert self._run_probe("spectrum") == 0

    def test_destroy_dialog_during_bpm_detect(self):
        assert self._run_probe("dialog") == 0


class TestTimelineAudioSourcePush:
    def test_set_audio_data_pushes_source_to_open_dialog(self, qapp):
        timeline = TimelineWidget()
        timeline._on_waveform_settings_clicked()
        dialog = timeline._advanced_dialog
        assert dialog is not None
        assert not dialog.btn_detect_bpm.isEnabled()  # 尚无音频

        timeline.set_audio_data(np.ones(44100, np.float32) * 0.1, SR, 1)

        assert dialog.btn_detect_bpm.isEnabled()

        timeline.clear_audio_data()

        assert not dialog.btn_detect_bpm.isEnabled()

        dialog.deleteLater()

    def test_toggle_waveform_spectrum_action(self, qapp):
        """快捷键动作：切到声谱再切回，走 display_settings 持久化链。"""
        from types import SimpleNamespace

        from strange_uta_game.frontend.editor.timing_interface import EditorInterface

        timeline = TimelineWidget()
        received = []
        timeline.display_settings_changed.connect(
            lambda d: received.append(dict(d))
        )
        ed = SimpleNamespace(timeline=timeline)
        EditorInterface._toggle_waveform_spectrum(ed)
        assert timeline.display_settings()["display_mode"] == "spectrum"
        assert len(received) == 1
        EditorInterface._toggle_waveform_spectrum(ed)
        assert timeline.display_settings()["display_mode"] == "waveform"
        assert len(received) == 2


class TestEditorLevelSpectrumHeight:
    """P1：声谱高度真实布局——show 后按实际 geometry 断言无重叠。"""

    _SIZE_SMALL = (1028, 713)
    _SIZE_LARGE = (1400, 1100)

    def _make_editor(self, monkeypatch, qapp, size, preferred_height):
        import strange_uta_game.frontend.editor.timing_interface as timing_module

        monkeypatch.setattr(
            timing_module.EditorInterface, "_init_keysound", lambda self: None
        )
        editor = timing_module.EditorInterface()
        editor.resize(*size)
        editor.show()
        qapp.processEvents()
        # 走真实设置链（触发预览 minimumHeight 让位）
        editor.timeline._apply_display_settings({
            "display_mode": "spectrum",
            "grid_mode": "time",
            "grid_bpm": 120.0,
            "grid_line_width": 2,
            "spectrum_fft_size": 2048,
            "spectrum_freq_scale": "log",
            "spectrum_dyn_range_db": 90,
            "display_height": preferred_height,
        })
        # 布局协商经 singleShot 多轮收敛，模拟真实事件循环多泵几轮
        for _ in range(12):
            qapp.processEvents()
        # 建窗后才切声谱：窗口此前可能被波形模式的固有 min 暂时撑大
        #（Qt 窗口只涨不缩）——重新 resize 回请求尺寸，模拟用户缩小窗口
        editor.resize(*size)
        for _ in range(6):
            qapp.processEvents()
        return editor

    def test_no_overlap_and_height_honored_large_window(
        self, monkeypatch, qapp
    ):
        editor = self._make_editor(monkeypatch, qapp, self._SIZE_LARGE, 400)
        try:
            wd = editor.timeline.waveform_display
            bar = editor.timeline._bottom_bar
            # 复审 P1 断言：显示区不得侵入底栏
            assert wd.geometry().bottom() < bar.geometry().top()
            # 底栏不越出时间轴
            assert bar.geometry().bottom() <= editor.timeline.rect().bottom()
            # 时间轴整体位于预览上方
            assert editor.timeline.geometry().bottom() <= editor.preview.geometry().top()
            # 大窗口下期望高度被真实满足
            assert wd.height() >= 398
            # P1-1 断言：顶层窗口不被期望高度撑大
            assert editor.height() <= self._SIZE_LARGE[1] + 2, (
                f"窗口被撑大: {editor.height()} > {self._SIZE_LARGE[1]}"
            )
        finally:
            editor.close()
            editor.deleteLater()

    def test_no_overlap_small_window(self, monkeypatch, qapp):
        editor = self._make_editor(monkeypatch, qapp, self._SIZE_SMALL, 400)
        try:
            wd = editor.timeline.waveform_display
            bar = editor.timeline._bottom_bar
            assert wd.geometry().bottom() < bar.geometry().top()
            assert bar.geometry().bottom() <= editor.timeline.rect().bottom()
            assert editor.timeline.geometry().bottom() <= editor.preview.geometry().top()
            # 预览让位后至少保住最低可操作高度
            assert editor.preview.height() >= 155
            # P1-1 断言：预览已让位，适度扩展可接受（远好于此前的 855）
            assert editor.height() <= 805, (
                f"窗口过度撑大: {editor.height()}（预期 ≤805，曾 855）"
            )
            assert editor.preview.minimumHeight() == 160  # 预览已让位
            # 显示区被压缩（期望 400 拿不满），但领取了空间
            assert wd.height() <= self._SIZE_SMALL[1]
            assert wd.minimumHeight() == 400  # 期望值（预览 yield 后空间够）
        finally:
            editor.close()
            editor.deleteLater()

    def test_preview_yields_in_both_modes(
        self, monkeypatch, qapp
    ):
        """预览在波形和声谱两种模式下都让位；关闭波形后恢复固有 min。"""
        editor = self._make_editor(monkeypatch, qapp, self._SIZE_SMALL, 300)
        try:
            assert editor.preview.minimumHeight() == 160  # 声谱模式让位
            editor.timeline._apply_display_settings({
                "display_mode": "waveform",
                "grid_mode": "time",
                "grid_bpm": 120.0,
                "grid_line_width": 2,
                "spectrum_fft_size": 2048,
                "spectrum_overlap": 0.75,
                "spectrum_freq_scale": "log",
                "spectrum_dyn_range_db": 90,
                "display_height": 300,
            })
            qapp.processEvents()
            # 波形模式也让位（显示高度领取空间的必要条件）
            assert editor.preview.minimumHeight() == 160

            # 关闭波形开关 → 预览恢复固有 min
            editor.timeline.set_waveform_visible(False)
            qapp.processEvents()
            assert editor.preview.minimumHeight() == 400
        finally:
            editor.close()
            editor.deleteLater()


class TestBpmGridLineWidth:
    def test_width_hierarchy(self, qapp):
        display = WaveformDisplay()
        # 层级：半拍 = max(1, 基础-1)，拍线 = 基础，小节线 = 基础+1
        assert display._bpm_grid_widths() == (1, 2, 3)
        display.set_grid_line_width(5)
        assert display._bpm_grid_widths() == (4, 5, 6)

    def test_dialog_roundtrip_and_invalid_input_restores(self, qapp):
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "waveform", "grid_line_width": 3}
        )
        assert dialog.grid_width_edit.text() == "3"
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        dialog.grid_width_edit.setText("5")
        dialog._commit_grid_width()
        assert received and received[-1]["grid_line_width"] == 5

        # 非法：空串 / 越界 → 恢复上一次有效值（42 现在是合法值）
        dialog.grid_width_edit.setText("")
        dialog._commit_grid_width()
        assert dialog.grid_width_edit.text() == "5"
        dialog.grid_width_edit.setText("999")
        dialog._commit_grid_width()
        assert dialog.grid_width_edit.text() == "5"
        dialog.grid_width_edit.setText("0")
        dialog._commit_grid_width()
        assert dialog.grid_width_edit.text() == "0"  # 0 合法 = 不绘制

    def test_persistence_settings_key_defaults(self):
        from strange_uta_game.frontend.settings.app_settings import AppSettings

        timing = AppSettings.DEFAULT_SETTINGS["timing"]
        assert timing.get("waveform_grid_line_width") == 2
        assert timing.get("spectrum_overlap") == 0.75
        assert timing.get("display_height") == 120


class TestSpectrumAxisGutter:
    """P2：频率轴独立 gutter——tag 与频率刻度物理分离，坐标换算扣轴。"""

    def test_axis_width_by_mode(self, qapp):
        display = WaveformDisplay()
        display.resize(500, 200)
        assert display._spectrum_axis_width() == 0
        display.set_display_mode("spectrum")
        assert display._spectrum_axis_width() == 40
        assert display._plot_width() == 460

    def test_x_to_time_accounts_for_axis(self, qapp):
        display = WaveformDisplay()
        display.resize(500, 200)
        display.set_duration(10_000)
        display.set_zoom(1.0)
        display.set_display_mode("spectrum")
        # widget 坐标 = 轴宽(40) → 时间 0（轴区不参与时间映射）
        assert display._x_to_time(40) == 0
        assert display._x_to_time(40 + 230) == 5_000
        # 显式 width 仍是绘图区坐标语义（既有测试约定）
        assert display._x_to_time(0, width=500) == 0

    def test_hit_test_shifts_by_axis(self, qapp):
        display = WaveformDisplay()
        display.resize(500, 200)
        display.set_duration(10_000)
        display.set_zoom(1.0)
        display.set_display_mode("spectrum")
        display.set_time_tags([(2_000, "あ", 0, 0, 0, False, None)])
        display._render_static_layer(500, 200, 0.0, 10_000.0, 10_000.0)
        cy = display._handle_center_y(200)
        plot_x = 2_000 / 10_000 * 460  # 绘图区坐标（plot_w = 500 - 40 轴）
        # widget 坐标命中（= plot_x + 轴宽）
        hit = display._hit_test_handle(plot_x + 40, cy)
        assert hit is not None and hit[0] == (0, 0, 0, False)


class TestFirstOpenHeightHint:
    def test_first_open_passes_actual_height(self, qapp, qtbot):
        """P2：首次创建弹窗在 show 前就传入实际显示高度（非默认 400）。"""
        timeline = TimelineWidget()
        timeline.resize(600, 240)  # 空间受限：声谱实际高度 < 400
        qapp.processEvents()
        timeline.waveform_display.set_display_mode("spectrum")
        timeline.waveform_display.resize(560, 180)
        qapp.processEvents()

        timeline._on_waveform_settings_clicked()
        dialog = timeline._advanced_dialog

        assert dialog is not None
        assert dialog._actual_height_cap == timeline.actual_spectrum_display_height()
        dialog.deleteLater()


class TestAnalysisGranularity:
    """波形分析粒度：每 X ms 取一次能量，瞬态在粗览下不被稀释。"""

    def test_hop_for_overlap(self, qapp):
        display = WaveformDisplay()
        # overlap → hop（帧距 = fft // {2,4,8,16}）
        assert display._spectrum_hop_for(2048, 0.5) == 1024
        assert display._spectrum_hop_for(2048, 0.75) == 512
        assert display._spectrum_hop_for(2048, 0.875) == 256
        assert display._spectrum_hop_for(2048, 0.9375) == 128

    def test_peaks_include_rms_layer(self, qapp):
        """双层波形：peaks 为 (min, max, rms) 三元组，RMS 在 0~峰值之间。"""
        from strange_uta_game.backend.infrastructure.audio import spectrum

        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_zoom(1.0)
        display.resize(400, 120)
        display.set_audio_data(_tone(2.0), SR, 1)
        # 预填充缓存层（回退不扫原始样本，需有层可用）
        levels = spectrum.build_peak_levels_single_pass(
            display._waveform_samples
        )
        display._waveform_peak_levels.update(levels)

        peaks = display._compute_waveform_peaks(400)

        assert peaks and len(peaks[0]) == 3
        for lo, hi, rms in peaks:
            assert lo <= rms <= max(abs(lo), abs(hi)) + 1e-6
            assert rms >= 0.0
        # 正弦 0.6 幅度的 RMS = 0.6/√2 ≈ 0.424
        rms_max = max(rms for _, _, rms in peaks)
        assert 0.35 <= rms_max <= 0.50

    def test_grid_line_width_range_and_zero_disables(self, qapp):
        """线宽 0~100；0 = 不绘制网格（时间/BPM 共用）。"""
        display = WaveformDisplay()
        assert display.display_settings()["grid_line_width"] == 2
        display.set_grid_line_width(80)
        assert display.display_settings()["grid_line_width"] == 80
        display.set_grid_line_width(999)  # 越界钳制
        assert display.display_settings()["grid_line_width"] == 100
        display.set_grid_line_width(0)
        assert display.display_settings()["grid_line_width"] == 0
        # 0 时时间网格与 BPM 网格均不绘制（渲染不报错即可）
        display.set_duration(6000)
        display.set_grid_mode("bpm")
        layer = display._render_static_layer(300, 150, 0.0, 6000.0, 6000.0)
        assert not layer.isNull()

    def test_spectrum_overlap_triggers_recompute(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(2000)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(2.0), SR, 1)
        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum["hop"] == 512  # 默认 75% 重叠

        display.set_spectrum_params(overlap=0.9375)

        qtbot.waitUntil(lambda: display._spectrum_state == "ready", timeout=15000)
        assert display._spectrum["hop"] == 128  # 93.75% → 帧距 fft/16
        assert display._spectrum["matrix"].shape[0] >= 600

    def test_dialog_collects_overlap_and_display_height(self, qapp):
        initial = {
            "display_mode": "spectrum",
            "grid_mode": "time",
            "grid_bpm": 120.0,
            "grid_line_width": 2,
            "spectrum_fft_size": 2048,
            "spectrum_overlap": 0.875,
            "spectrum_freq_scale": "log",
            "spectrum_dyn_range_db": 90,
            "display_height": 260,
        }
        dialog = TestAdvancedDialog._make_dialog(initial)
        collected = dialog._collect()
        assert collected["spectrum_overlap"] == 0.875
        assert collected["display_height"] == 260
        dialog.overlap_combo.setCurrentIndex(3)  # 93.75%
        dialog.height_slider.setValue(300)
        collected = dialog._collect()
        assert collected["spectrum_overlap"] == 0.9375
        assert collected["display_height"] == 300

    def test_enter_not_swallowed_by_default_button(self, qapp):
        """Enter 焦点修复：弹窗按钮不抢 Enter（autoDefault 关闭）。"""
        dialog = TestAdvancedDialog._make_dialog({"display_mode": "waveform"})
        assert not dialog.btn_detect_bpm.autoDefault()
        assert not dialog.btn_close.autoDefault()

    def test_bpm_inputs_strict_focus_semantics(self, qapp):
        """严格失焦语义：输入期间不应用；editingFinished（失焦/回车）提交。"""
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "waveform", "grid_bpm": 120.0, "grid_line_width": 2}
        )
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        dialog.bpm_spin.setValue(180.0)
        dialog.grid_width_edit.setText("4")
        assert received == []  # 输入期间（无 editingFinished）不应用

        dialog.bpm_spin.editingFinished.emit()  # 失焦/回车
        assert received and received[-1]["grid_bpm"] == 180.0

        received.clear()
        dialog.grid_width_edit.setFocus()
        dialog.grid_width_edit.editingFinished.emit()
        assert received and received[-1]["grid_line_width"] == 4

    def test_actual_height_hint_updates_live(self, qapp):
        """「实际 N px」提示随显示区高度变化实时刷新。"""
        timeline = TimelineWidget()
        timeline.waveform_display.set_display_mode("spectrum")
        timeline._on_waveform_settings_clicked()
        dialog = timeline._advanced_dialog

        timeline.waveform_display.resize(400, 260)  # 高度变化
        # eventFilter 走真实事件循环；直接调槽验证链路
        dialog.set_height_cap(timeline.waveform_display.height())
        assert dialog._actual_height_cap == 260
        assert "260" in dialog.height_caption.text() or dialog.height_slider.value() <= 260
        dialog.deleteLater()

class TestWaveformCollapseLayout:
    """P1-2: 关闭波形后时间轴收缩到底栏高度。"""

    def test_collapse_and_restore(self, qapp):
        from PyQt6.QtWidgets import QVBoxLayout, QWidget

        container = QWidget()
        layout = QVBoxLayout(container)
        timeline = TimelineWidget()
        layout.addWidget(timeline)
        preview = QWidget()
        preview.setMinimumHeight(100)
        layout.addWidget(preview, stretch=1)
        container.resize(800, 600)
        container.show()
        for _ in range(5):
            qapp.processEvents()

        open_hint = timeline.sizeHint().height()
        assert open_hint > 100  # 波形可见时高度含显示区

        # 关闭
        timeline.set_waveform_visible(False)
        for _ in range(5):
            qapp.processEvents()
        closed_hint = timeline.sizeHint().height()
        assert closed_hint < 60, f"关闭后 sizeHint={closed_hint}，应≈底栏高度"
        assert timeline.waveform_display.isVisible() is False

        # 恢复
        timeline.set_waveform_visible(True)
        for _ in range(5):
            qapp.processEvents()
        reopened_hint = timeline.sizeHint().height()
        assert reopened_hint == open_hint  # 恢复到原高度（无累积）


class TestSignalSingleEmit:
    """P3-1: 程序化切换只发一次信号。"""

    def test_set_visible_emits_exactly_once(self, qapp):
        timeline = TimelineWidget()
        signals = []
        timeline.waveform_visibility_changed.connect(
            lambda v: signals.append(v)
        )
        timeline.set_waveform_visible(False)
        assert len(signals) == 1 and signals[0] is False

        timeline.set_waveform_visible(True)
        assert len(signals) == 2 and signals[1] is True

        # 重复设置相同值：不发信号
        timeline.set_waveform_visible(True)
        assert len(signals) == 2


class TestVisiblePeaksBoundaryMapping:
    """居中播放时边界映射正确（通过真实路径：set_audio_data → 同步预热）。"""

    def test_head_pulse_survives_center_mode_at_start(self, qapp):
        """居中播放 position=0：开头脉冲不丢。"""
        sr = 1000
        samples = np.zeros(sr, dtype=np.float32)
        samples[0] = 1.0
        d = WaveformDisplay()
        d.set_duration(1000)
        d.set_zoom(2.0)
        d.resize(100, 100)
        d.set_audio_data(samples, sr, 1)  # 1000 < 64K → 同步建层
        d.set_center_playhead_mode(True)
        d.set_position(0)
        d.set_playing(True)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None
        max_val = max(hi for _, hi, _ in peaks)
        assert max_val >= 0.99, f"开头脉冲丢失: max={max_val}"

    def test_tail_pulse_survives_center_mode_at_end(self, qapp):
        """居中播放接近结尾：末尾脉冲不丢。"""
        sr = 1000
        samples = np.zeros(sr, dtype=np.float32)
        samples[-1] = 1.0
        d = WaveformDisplay()
        d.set_duration(1000)
        d.set_zoom(2.0)
        d.resize(100, 100)
        d.set_audio_data(samples, sr, 1)
        d.set_center_playhead_mode(True)
        d.set_position(1000)
        d.set_playing(True)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None
        max_val = max(hi for _, hi, _ in peaks)
        assert max_val >= 0.99, f"末尾脉冲丢失: max={max_val}"


class TestSliderTrackClick:
    """P2-1：滑条裸点轨道/键盘改值时立即应用。"""

    def test_track_click_applies_immediately(self, qapp):
        """模拟点击轨道（valueChanged 且非 isSliderDown）→ 立即 applied。"""
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "spectrum", "spectrum_dyn_range_db": 90, "display_height": 220}
        )
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        # 模拟点击轨道：value 变化但 isSliderDown = False
        dialog.dyn_slider.setValue(100)
        # valueChanged 触发 _on_slider_value_changed → isSliderDown False → applied
        assert len(received) == 1
        assert received[0]["spectrum_dyn_range_db"] == 100

    def test_height_slider_track_click(self, qapp):
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "spectrum", "display_height": 220}
        )
        received = []
        dialog.applied.connect(lambda d: received.append(d))
        dialog.height_slider.setValue(300)
        assert len(received) == 1
        assert received[0]["display_height"] == 300

    def test_drag_does_not_apply_until_release(self, qapp):
        """拖动中（isSliderDown=True）不应用；松手后应用一次。"""
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "spectrum", "spectrum_dyn_range_db": 90}
        )
        received = []
        dialog.applied.connect(lambda d: received.append(d))

        # 模拟拖动：设 isSliderDown=True 后改值
        dialog.dyn_slider.setSliderDown(True)
        dialog.dyn_slider.setValue(110)
        assert len(received) == 0  # 拖动中不应用

        # 松手（blockSignals 防止 setSliderDown(False) 触发额外 valueChanged）
        dialog.dyn_slider.blockSignals(True)
        dialog.dyn_slider.setSliderDown(False)
        dialog.dyn_slider.blockSignals(False)
        dialog.dyn_slider.sliderReleased.emit()
        assert len(received) == 1  # 松手恰好一次
        assert received[0]["spectrum_dyn_range_db"] == 110


class TestOverlapHintAudioChange:
    """P2-3：切歌/清音频时刷新弹窗 overlap 提示。"""

    def test_audio_change_refreshes_hint(self, qapp):
        timeline = TimelineWidget()
        timeline._on_waveform_settings_clicked()
        dialog = timeline._advanced_dialog
        assert dialog is not None

        # 加载音频 → 刷新 hint
        timeline.set_audio_data(
            np.zeros(44100, np.float32), SR, 1
        )
        # hint 应被刷新（清空或更新为新值，不残留旧值）
        #（无音频时 actual=None → hint=""）

        # 清除音频 → 刷新 hint
        timeline.clear_audio_data()
        assert dialog.overlap_hint.text() == ""

        dialog.deleteLater()


class TestRmsSwitch:
    """双层波形开关：默认开，关闭后不画内层 RMS。"""

    def test_default_on_and_toggle(self, qapp):
        display = WaveformDisplay()
        assert display._waveform_rms_enabled is True
        assert display.display_settings()["waveform_rms_enabled"] is True
        display.set_waveform_rms_enabled(False)
        assert display.display_settings()["waveform_rms_enabled"] is False
        display.set_waveform_rms_enabled(True)
        assert display.display_settings()["waveform_rms_enabled"] is True

    def test_dialog_roundtrip(self, qapp):
        dialog = TestAdvancedDialog._make_dialog(
            {"display_mode": "waveform", "waveform_rms_enabled": False}
        )
        assert not dialog.rms_switch.isChecked()
        assert dialog._collect()["waveform_rms_enabled"] is False
        dialog.rms_switch.setChecked(True)
        assert dialog._collect()["waveform_rms_enabled"] is True


class TestPeakLevelTailAndCancel:
    """P1/P2：峰值层尾部样本保留 + 取消链接通 + 分块不转整曲 float64。"""

    def test_tail_sample_preserved(self):
        from strange_uta_game.backend.infrastructure.audio import spectrum as sc

        n = 2097153  # 奇数个样本（复审用的精确值）
        samples = np.zeros(n, dtype=np.float32)
        samples[-1] = 1.0  # 最后一个样本是峰值
        levels = sc.build_peak_levels_single_pass(samples)
        # 最细层的最后一个 bin 必须包含那个峰值
        finest = min(levels.keys())
        mins, maxs, rmss = levels[finest]
        assert maxs[-1] >= 0.99  # 尾部峰值未丢失

    def test_cancel_stops_immediately(self):
        from strange_uta_game.backend.infrastructure.audio import spectrum as sc

        samples = np.zeros(44100 * 5, dtype=np.float32)  # 5s
        result = sc.build_peak_levels_single_pass(
            samples, cancel_check=lambda: True
        )
        assert result is None

    def test_peak_level_worker_runs(self, qapp):
        from strange_uta_game.frontend.workers import PeakLevelWorker

        import numpy as np

        samples = np.zeros(44100, dtype=np.float32)
        w = PeakLevelWorker(samples)
        results = []
        w.finished.connect(lambda r: results.append(r))
        w.run()  # 同步执行（测试）
        assert results and isinstance(results[0], dict)

    def test_actual_overlap_visible_when_degraded(self, qapp):
        """P2-3：预算降级时 display_settings 携带 actual_spectrum_overlap。"""
        from strange_uta_game.backend.infrastructure.audio import spectrum as sc

        display = WaveformDisplay()
        display.set_duration(60000)
        display.set_display_mode("spectrum")
        # 模拟短样本但收紧预算使 93.75% 被降档
        original = sc.SPECTRUM_MATRIX_BUDGET_BYTES
        sc.SPECTRUM_MATRIX_BUDGET_BYTES = 6 * 1024 * 1024  # 6MB → 75% 可用
        try:
            display._waveform_samples = np.zeros(44100 * 60, dtype=np.float32)
            display._sample_rate = SR
            display._spectrum_fft_size = 2048
            display._spectrum_overlap = 0.9375
            display._reset_spectrum_cache()
            display._ensure_spectrum()
            settings = display.display_settings()
            actual = settings.get("actual_spectrum_overlap")
            assert actual is not None and actual < 0.9375
        finally:
            sc.SPECTRUM_MATRIX_BUDGET_BYTES = original


class TestShortAudioRealPath:
    """P2-1: 短音频通过真实路径（set_audio_data → 预热 → compute）不空白。"""

    def test_1_sample_audio_shows_peaks(self, qapp):
        d = WaveformDisplay()
        d.set_duration(1)  # 1 sample ≈ 0.02ms，int() 会得 0；至少 1ms
        d.set_zoom(1.0)
        d.resize(100, 100)
        d.set_audio_data(np.ones(1, dtype=np.float32), SR, 1)
        # set_audio_data 内部调 _preheat_peak_levels（<64K 同步建层）
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None and len(peaks) > 0

    def test_1000_sample_audio_shows_peaks(self, qapp):
        d = WaveformDisplay()
        d.set_duration(int(1000 / SR * 1000))
        d.set_zoom(1.0)
        d.resize(100, 100)
        d.set_audio_data(np.ones(1000, dtype=np.float32), SR, 1)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None and len(peaks) > 0
        assert any(hi > 0.5 for _, hi, _ in peaks)  # 全 1.0 音频应有高值

    def test_4095_sample_audio_shows_peaks(self, qapp):
        d = WaveformDisplay()
        d.set_duration(int(4095 / SR * 1000))
        d.set_zoom(1.0)
        d.resize(100, 100)
        d.set_audio_data(np.ones(4095, dtype=np.float32), SR, 1)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None and len(peaks) > 0

    def test_4096_sample_audio_shows_peaks(self, qapp):
        d = WaveformDisplay()
        d.set_duration(int(4096 / SR * 1000))
        d.set_zoom(1.0)
        d.resize(100, 100)
        d.set_audio_data(np.ones(4096, dtype=np.float32), SR, 1)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None and len(peaks) > 0

    def test_short_audio_transient_preserved(self, qapp):
        """短音频中的瞬态通过真实路径保留。"""
        d = WaveformDisplay()
        n = 8000  # < 64K → 同步建层
        samples = np.zeros(n, dtype=np.float32)
        samples[3] = 1.0  # 瞬态在偏移 3
        d.set_duration(int(n / SR * 1000))
        d.set_zoom(1.0)
        d.resize(100, 100)
        d.set_audio_data(samples, SR, 1)
        peaks = d._compute_waveform_peaks(100)
        assert peaks is not None
        assert max(hi for _, hi, _ in peaks) >= 0.99

    def test_no_cache_returns_none_not_scan(self, qapp):
        """无缓存时不扫描原始音频（长音频返回 None 显示占位）。"""
        d = WaveformDisplay()
        n = SR * 60  # 60s → 后台预热路径
        samples = np.zeros(n, dtype=np.float32)
        d.set_duration(60_000)
        d.set_zoom(1.0)
        d.resize(100, 100)
        # 模拟预热未完成：不调 set_audio_data（不触发预热），只设底层
        d._waveform_samples = samples
        d._samples = samples
        d._sample_rate = SR
        d._duration_ms = 60_000
        d._waveform_peak_levels.clear()  # 确保无缓存
        peaks = d._compute_waveform_peaks(100)
        assert peaks is None  # 占位，不扫描


class TestSpectrumActiveGate:
    """P2：inactive 状态门禁——后台/隐藏时任何路径都不得重启计算。"""

    def test_inactive_blocks_restart_on_audio_change(self, qapp):
        display = WaveformDisplay()
        display.set_duration(30000)
        display.set_display_mode("spectrum")

        display.set_spectrum_active(False)  # 隐藏/后台
        assert display._spectrum_active is False

        display.set_audio_data(_tone(10.0), SR, 1)  # 曾在此重启 worker

        assert display._spectrum_worker is None  # 门禁生效
        assert display._spectrum is None

        # 恢复可见：按需启动
        display.set_spectrum_active(True)
        assert display._spectrum_worker is not None


class TestSpectrumMatrixBudget:
    """P1：基础矩阵预算——超限自动降重叠，最低档仍超则拒绝。"""

    def test_pick_overlap_within_budget_falls_back(self):
        from strange_uta_game.backend.infrastructure.audio import spectrum as sc

        # 短音频：任何重叠都在预算内 → 返回首选
        assert sc.pick_overlap_within_budget(SR * 60, SR, 2048, 0.9375) == 0.9375
        # 极端长音频 + 小预算：降档到更低重叠
        tiny = 1024 * 1024  # 1MB 预算
        result = sc.pick_overlap_within_budget(SR * 3600, SR, 2048, 0.9375, tiny)
        assert result is None or result <= 0.75  # 高重叠被拒/降档

    def test_pick_overlap_budget_math(self):
        """纯函数验证：矩阵估算与降档链（不分配大数组）。"""
        from strange_uta_game.backend.infrastructure.audio import spectrum as sc

        # 10 分钟 @44.1k fft=2048 hop=128 → frames≈20668, bins=1025 ≈ 20MB
        est = sc.estimate_matrix_bytes(SR * 600, SR, 2048, 128)
        assert 200 * 1024 * 1024 < est < 205 * 1024 * 1024
        # 预算 20MB → 93.75% 被拒，降到 75%（hop=512 → ≈5MB）
        ov = sc.pick_overlap_within_budget(SR * 3600, SR, 2048, 0.9375, 200 * 1024 * 1024)
        assert ov == 0.5  # 一小时 93.75%(1212MB)/87.5%(606MB)/75%(303MB) 均超 200MB → 50%(152MB)
        # 正常预算 → 返回首选
        ov = sc.pick_overlap_within_budget(SR * 600, SR, 2048, 0.9375, 384 * 1024 * 1024)
        assert ov == 0.9375

class TestPeakLevelBudget:
    """P1：峰值层缓存 LRU 预算 + 后台预热。"""

    def test_lru_evicts_over_budget(self, qapp):
        display = WaveformDisplay()
        display._waveform_peak_levels.clear()
        # 每层 3×32B=96B；预算 200B → 4 层 384B 超限，淘汰到 ≤2 层
        chunk = np.zeros(8, dtype=np.float32)  # 32B
        display._PEAK_LEVEL_BUDGET_BYTES = 200
        for i in range(4):
            display._waveform_peak_levels[i] = (chunk, chunk, chunk)
        display._trim_peak_level_cache()
        assert len(display._waveform_peak_levels) <= 2  # 至少保留 1

    def test_preheat_populates_cache(self, qapp, qtbot):
        display = WaveformDisplay()
        display.set_duration(10000)
        display.set_zoom(1.0)
        display.set_display_mode("spectrum")
        display.set_audio_data(_tone(10.0), SR, 1)
        qtbot.waitUntil(
            lambda: len(display._waveform_peak_levels) > 0, timeout=15000
        )
        # 预热层包含三层（min/max/rms）
        level = next(iter(display._waveform_peak_levels.values()))
        assert len(level) == 3


class TestPeakPreheatCancellation:
    """P2：换歌（含换到短音频）/清除音频时，旧峰值预热任务必须取消。"""

    @staticmethod
    def _recording_worker_cls(record):
        from strange_uta_game.frontend.workers import PeakLevelWorker

        class RecordingPeakLevelWorker(PeakLevelWorker):
            """run() 不发 finished：worker 停留在“在途”，取消状态可稳定断言。"""

            def __init__(self, samples):
                super().__init__(samples)
                self.cancel_requested = False
                record.append(self)

            def request_cancel(self):
                self.cancel_requested = True
                super().request_cancel()

            def run(self):
                pass

        return RecordingPeakLevelWorker

    @staticmethod
    def _stop_workers(record, qapp):
        """让 stub 线程退出（不发结果的清理路径，避免跨测试泄漏线程）。"""
        for w in record:
            try:
                w.finished.emit(None)
            except RuntimeError:
                pass  # C++ 对象已被 deleteLater 回收
        for _ in range(10):
            qapp.processEvents()

    def test_long_to_long_cancels_old(self, qapp, monkeypatch):
        import strange_uta_game.frontend.workers as workers_mod

        record = []
        monkeypatch.setattr(
            workers_mod, "PeakLevelWorker", self._recording_worker_cls(record)
        )
        display = WaveformDisplay()
        display.set_duration(10000)
        try:
            display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
            display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
            assert len(record) == 2
            assert record[0].cancel_requested is True
            assert record[1].cancel_requested is False
            assert display._preheat_cancel is not None  # 新任务句柄在位
        finally:
            self._stop_workers(record, qapp)

    def test_long_to_short_cancels_old(self, qapp, monkeypatch):
        import strange_uta_game.frontend.workers as workers_mod

        record = []
        monkeypatch.setattr(
            workers_mod, "PeakLevelWorker", self._recording_worker_cls(record)
        )
        display = WaveformDisplay()
        display.set_duration(10000)
        try:
            display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
            # 短音频走同步建层，但旧后台预热仍须取消
            display.set_audio_data(np.zeros(1000, dtype=np.float32), SR, 1)
            assert len(record) == 1
            assert record[0].cancel_requested is True
            assert display._preheat_cancel is None  # 短路径不启动后台任务
            assert len(display._waveform_peak_levels) > 0  # 短音频已同步建层
        finally:
            self._stop_workers(record, qapp)

    def test_long_to_clear_cancels_old(self, qapp, monkeypatch):
        import strange_uta_game.frontend.workers as workers_mod

        record = []
        monkeypatch.setattr(
            workers_mod, "PeakLevelWorker", self._recording_worker_cls(record)
        )
        display = WaveformDisplay()
        display.set_duration(10000)
        try:
            display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
            display.clear_audio_data()
            assert len(record) == 1
            assert record[0].cancel_requested is True
            assert display._preheat_cancel is None
        finally:
            self._stop_workers(record, qapp)

    def test_short_to_short_leaves_no_handle(self, qapp):
        display = WaveformDisplay()
        display.set_duration(1000)
        display.set_audio_data(np.zeros(1000, dtype=np.float32), SR, 1)
        display.set_audio_data(np.zeros(2000, dtype=np.float32), SR, 1)
        assert display._preheat_cancel is None

    def test_late_old_worker_result_rejected(self, qapp, monkeypatch):
        """取消是请求式的：旧 worker 可能算完才送达，结果不得写入新缓存。"""
        import strange_uta_game.frontend.workers as workers_mod

        record = []
        monkeypatch.setattr(
            workers_mod, "PeakLevelWorker", self._recording_worker_cls(record)
        )
        display = WaveformDisplay()
        display.set_duration(10000)
        try:
            display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
            old_worker = record[0]
            display.set_audio_data(np.zeros(1000, dtype=np.float32), SR, 1)
            fake = np.zeros(4, dtype=np.float32)
            old_worker.finished.emit({0xDEAD: (fake, fake, fake)})
            for _ in range(10):
                qapp.processEvents()
            assert 0xDEAD not in display._waveform_peak_levels
        finally:
            self._stop_workers(record, qapp)

    def test_handle_released_after_normal_completion(self, qapp, qtbot):
        """真实路径：worker 正常完成后取消句柄释放为 None。"""
        display = WaveformDisplay()
        display.set_duration(10000)
        display.set_audio_data(np.zeros(SR * 5, dtype=np.float32), SR, 1)
        assert display._preheat_cancel is not None
        qtbot.waitUntil(lambda: display._preheat_cancel is None, timeout=15000)


class TestPreMixedMono:
    """P1-1：立体声降混在引擎加载线程完成，UI 线程零降混。

    10 分钟立体声同步降混实测 ≈208ms + 106MB 瞬时分配，原先发生在
    set_audio_data（UI 线程回调）里，加载完成瞬间明显卡界面。
    """

    def test_compute_mono_samples_stereo_mean(self):
        from strange_uta_game.backend.infrastructure.audio.base import (
            compute_mono_samples,
        )

        data = np.array([[1.0, -1.0], [0.5, 0.25]], dtype=np.float32)
        mono = compute_mono_samples(data)
        assert mono.dtype == np.float32
        np.testing.assert_allclose(mono, [0.0, 0.375], atol=1e-7)

    def test_compute_mono_samples_shapes(self):
        from strange_uta_game.backend.infrastructure.audio.base import (
            compute_mono_samples,
        )

        col = compute_mono_samples(
            np.array([[1.0], [2.0], [3.0]], dtype=np.float32)
        )
        np.testing.assert_allclose(col, [1.0, 2.0, 3.0])
        assert col.flags["C_CONTIGUOUS"]  # 列切片转连续（as_strided 消费）
        flat = compute_mono_samples(np.array([1.0, 2.0], dtype=np.float32))
        np.testing.assert_allclose(flat, [1.0, 2.0])
        assert compute_mono_samples(None) is None

    def test_display_uses_premixed_mono_reference(self, qapp):
        """预混数据直接引用（is 判定）：UI 线程不再做任何降混/复制。"""
        display = WaveformDisplay()
        stereo = np.zeros((100, 2), dtype=np.float32)
        stereo[:, 0] = 1.0
        mono = stereo.mean(axis=1, dtype=np.float32)
        display.set_audio_data(stereo, SR, 2, mono=mono)
        assert display._waveform_samples is mono

    def test_display_fallback_mixes_when_mono_missing(self, qapp):
        """未预混的旧路径/测试：同步降混兜底，结果一致。"""
        display = WaveformDisplay()
        stereo = np.zeros((100, 2), dtype=np.float32)
        stereo[:, 0] = 1.0
        display.set_audio_data(stereo, SR, 2)
        np.testing.assert_allclose(display._waveform_samples, 0.5, atol=1e-7)

    def test_timeline_passes_mono_through(self, qapp):
        timeline = TimelineWidget()
        stereo = np.zeros((100, 2), dtype=np.float32)
        mono = np.zeros(100, dtype=np.float32)
        timeline.set_audio_data(stereo, SR, 2, mono=mono)
        assert timeline.waveform_display._waveform_samples is mono


class TestAxisCoordinateInteraction:
    """P2：频率轴后拖动/平移/滚轮锚点按绘图区宽度换算。"""

    def test_wheel_anchor_accounts_for_axis(self, qapp):
        display = WaveformDisplay()
        display.resize(500, 200)
        display.set_duration(10_000)
        display.set_zoom(1.0)
        display.set_display_mode("spectrum")
        axis = display._spectrum_axis_width()
        plot_w = display._plot_width()
        # 控件坐标 270（绘图区中央）：plot_x=230, ratio=0.5
        x = axis + plot_w // 2
        display._scroll_position = 0.0
        t = display._x_to_time(x)
        assert t == 5_000  # 中央 → 5s（整窗 10s）

class TestBpmGridOffset:
    """BPM 网格偏移（毫秒）：拍线相位对齐——节拍通常不从 0ms 开始。"""

    @staticmethod
    def _line_centers(display, w=1000, h=60):
        """把 BPM 网格画进透明位图，取中线（y=h//2）上有线素的竖线中心。

        只扫中线行：小节号文本画在顶部（y≈12），不会混入。
        """
        from PyQt6.QtGui import QPainter, QPixmap

        pm = QPixmap(w, h)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        try:
            display._draw_bpm_grid(painter, w, h, 0.0, 1000.0)
        finally:
            painter.end()
        img = pm.toImage()
        spans = []
        for x in range(w):
            if img.pixelColor(x, h // 2).alpha() > 0:
                if spans and x - spans[-1][1] <= 1:
                    spans[-1][1] = x
                else:
                    spans.append([x, x])
        return [(a + b) / 2 for a, b in spans]

    @staticmethod
    def _grid_display(**kw):
        display = WaveformDisplay()
        display.set_duration(1000)
        display.set_zoom(1.0)
        display.set_grid_mode("bpm")
        display.set_grid_bpm(kw.get("bpm", 120.0))  # 拍长 500ms
        display.set_grid_offset(kw.get("offset", 0))
        display.set_grid_line_width(2)
        return display

    def test_setter_clamps_and_reports(self, qapp):
        display = WaveformDisplay()
        assert display.display_settings()["grid_offset_ms"] == 0
        display.set_grid_offset(250)
        assert display.display_settings()["grid_offset_ms"] == 250
        display.set_grid_offset(700000)  # 越界钳到 ±600000
        assert display.display_settings()["grid_offset_ms"] == 600000
        display.set_grid_offset(-700000)
        assert display.display_settings()["grid_offset_ms"] == -600000
        display.set_grid_offset("nonsense")  # 非法输入忽略
        assert display.display_settings()["grid_offset_ms"] == -600000

    def test_offset_shifts_grid_lines(self, qapp):
        """120BPM（拍长 500ms）× 1s 视窗：偏移 250ms 后拍线整体右移。"""
        base = self._line_centers(self._grid_display(offset=0))
        # 无偏移：小节线 x=0（b=0）、半拍 250、拍线 500、半拍 750
        #（pen 宽 2/3 有 ±0.5px 的中心取整，容差 1px）
        assert base == [pytest.approx(v, abs=1) for v in (0, 250, 500, 750)]

        shifted = self._line_centers(self._grid_display(offset=250))
        # 偏移 250：小节线 250（b=0）、半拍 500、拍线 750（b=1 半拍 1000 出界）
        assert shifted == [pytest.approx(v, abs=1) for v in (250, 500, 750)]

    def test_negative_offset_draws_pre_roll_lines(self, qapp):
        """负偏移 -500ms：b=1 拍线落到 x=0（网格前移），其后依次左移一拍。"""
        centers = self._line_centers(self._grid_display(offset=-500))
        # 拍（b=1）x=0、半拍 250、拍（b=2）500、半拍 750；b=0 拍与半拍出界
        assert centers == [pytest.approx(v, abs=1) for v in (0, 250, 500, 750)]

    def test_timeline_apply_display_settings_carries_offset(self, qapp):
        timeline = TimelineWidget()
        timeline._apply_display_settings({"grid_offset_ms": 321})
        assert timeline.waveform_display.display_settings()["grid_offset_ms"] == 321

    def test_dialog_offset_roundtrip_and_invalid_restore(self, qapp):
        dialog = TestAdvancedDialog._make_dialog(
            {"grid_mode": "bpm", "grid_offset_ms": 250}
        )
        assert dialog._collect()["grid_offset_ms"] == 250
        # 失焦提交语义：非法文本恢复上次有效值并照常提交
        dialog.grid_offset_edit.setText("abc")
        dialog._commit_grid_offset()
        assert dialog._collect()["grid_offset_ms"] == 250
        dialog.grid_offset_edit.setText("-40")
        dialog._commit_grid_offset()
        assert dialog._collect()["grid_offset_ms"] == -40
        # 越界（>600000）恢复上次有效值
        dialog.grid_offset_edit.setText("999999")
        dialog._commit_grid_offset()
        assert dialog._collect()["grid_offset_ms"] == -40


class TestBpmGridPerformance:
    def test_high_bpm_long_audio_grid_under_30ms(self, qapp):
        """P2：600BPM × 2 小时全览，单次 BPM 网格构建 ≤30ms（曾实测 173ms）。"""
        import time as _time

        from PyQt6.QtGui import QPainter, QPixmap

        display = WaveformDisplay()
        display.set_duration(2 * 60 * 60 * 1000)
        display.set_zoom(1.0)
        display.set_grid_mode("bpm")
        display.set_grid_bpm(600.0)
        pixmap = QPixmap(1920, 220)
        painter = QPainter(pixmap)
        try:
            t0 = _time.perf_counter()
            display._draw_bpm_grid(painter, 1920, 220, 0.0, 7_200_000.0)
            elapsed_ms = (_time.perf_counter() - t0) * 1000
        finally:
            painter.end()
        assert elapsed_ms < 30, f"BPM 网格构建耗时 {elapsed_ms:.1f}ms"


class TestAppVisibility:
    def test_app_hidden_pauses_spectrum_and_resumes(self, qapp, qtbot):
        """P3：应用切后台暂停在途声谱计算，回前台按需恢复。"""
        timeline = TimelineWidget()
        wd = timeline.waveform_display
        wd.set_duration(30000)
        wd.set_display_mode("spectrum")
        wd.set_audio_data(_tone(30.0), SR, 1)
        assert wd._spectrum_state == "computing"

        timeline.set_app_visible(False)
        assert wd._spectrum_worker is None  # 已取消

        timeline.set_app_visible(True)
        qtbot.waitUntil(lambda: wd._spectrum_state == "ready", timeout=15000)
        assert wd._spectrum is not None


class TestSpectrumThemeRender:
    """P3：浅色/深色主题完整渲染的像素级可读性检查。"""

    @staticmethod
    def _render(display, w=480, h=200):
        display.resize(w, h)
        return display._render_static_layer(w, h, 0.0, 6000.0, 6000.0)

    @staticmethod
    def _pixels(pixmap):
        """返回 (物理像素 RGB, dpr)。物理像素 = 逻辑坐标 × dpr（offscreen 常为 1.5）。"""
        image = pixmap.toImage()
        ptr = image.constBits()
        ptr.setsize(image.sizeInBytes())
        arr = np.frombuffer(
            ptr, dtype=np.uint8, count=image.sizeInBytes()
        ).reshape(image.height(), image.width(), 4)
        dpr = max(1.0, float(image.devicePixelRatio() or 1.0))
        return arr[:, :, [2, 1, 0]].astype(int), dpr  # ARGB32 → RGB

    def test_tag_halo_and_labels_readable_both_themes(self, qapp, qtbot, monkeypatch):
        from strange_uta_game.frontend import theme as theme_module
        from strange_uta_game.frontend.theme import ThemeColors

        original_colors = theme_module.theme._colors
        try:
            for is_dark in (False, True):
                theme_module.theme._colors = ThemeColors(is_dark)
                display = WaveformDisplay()
                display.set_duration(6000)
                display.set_zoom(1.0)
                display.set_display_mode("spectrum")
                rng = np.random.default_rng(3)
                n = SR * 6
                tone = 0.4 * np.sin(
                    2 * np.pi * 880 * np.arange(n) / SR
                ).astype(np.float32)
                tone += 0.02 * rng.standard_normal(n).astype(np.float32)
                display.set_audio_data(tone, SR, 1)
                deadline = time.time() + 20
                while display._spectrum_state != "ready" and time.time() < deadline:
                    qapp.processEvents()
                    time.sleep(0.01)
                display.set_time_tags([
                    (1000, "あ", 0, 0, 0, False, "a"),
                    (3000, "い", 0, 1, 0, False, None),
                ])
                pixmap = self._render(display)
                rgb, dpr = self._pixels(pixmap)
                bg = theme_module.theme.waveform_bg
                bg_rgb = np.array([bg.red(), bg.green(), bg.blue()])
                # 逻辑坐标 → 物理像素
                def px(v):
                    return int(round(v * dpr))

                axis = display._spectrum_axis_width()
                plot_w = 480 - axis
                # tag 竖线（声谱模式从轴区右沿延伸）：x = 轴 + ts/6000*plot_w
                line_x = px(axis + 1000 / 6000 * (plot_w - 1))
                # 竖线中心区域应有语义色/描边（非背景、非纯热图列）
                column = rgb[px(120), line_x - px(3):line_x + px(3)]
                differs = np.abs(column - bg_rgb).max(axis=1) > 40
                assert differs.any(), (
                    f"dark={is_dark}: tag 竖线在内容区不可辨"
                )
                # halo：竖线两侧背景色描边像素（顶带）
                halo_zone = rgb[0:px(40), line_x - px(5):line_x - px(1)]
                halo_close = (
                    np.abs(halo_zone - bg_rgb).max(axis=2) < 60
                )
                assert halo_close.any(), f"dark={is_dark}: 竖线缺少背景色 halo"
                # 频率刻度在独立轴区：轴内有文字、且 tag 竖线不进入轴区
                left = rgb[px(30):px(180), 0:px(axis) - px(2)]
                assert (np.abs(left - bg_rgb).max(axis=2) > 100).any(), (
                    f"dark={is_dark}: 频率刻度标签缺失"
                )
                axis_strip = rgb[:, 0:px(axis) - px(6)]
                assert not (np.abs(axis_strip - bg_rgb).max(axis=2) > 150).any(), (
                    f"dark={is_dark}: 绘图内容越入频率轴区"
                )
                display.deleteLater()
        finally:
            theme_module.theme._colors = original_colors
