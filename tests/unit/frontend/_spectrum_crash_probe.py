# -*- coding: utf-8 -*-
"""P1 线程生命周期崩溃探针 — 必须由测试以独立子进程运行。

用法: python _spectrum_crash_probe.py <spectrum|dialog>
计算/检测进行中销毁 UI owner，进程必须正常退出（returncode 0）。
Qt 原生崩溃会直接终止本进程而非 pytest，所以不能同进程测试。
"""
import sys

sys.path.insert(0, "src")

import numpy as np  # noqa: E402
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

mode = sys.argv[1] if len(sys.argv) > 1 else "spectrum"
app = QApplication(sys.argv)

# 2 分钟噪声：STFT ≈0.3s / BPM ≈0.4s，销毁时任务必然在途
samples = (np.random.default_rng(1).standard_normal(44100 * 120) * 0.1).astype(
    np.float32
)

if mode == "spectrum":
    from strange_uta_game.frontend.editor.timing.timeline_widget import WaveformDisplay

    display = WaveformDisplay()
    display.set_duration(120_000)
    display.set_display_mode("spectrum")
    display.set_audio_data(samples, 44100, 1)
    assert display._spectrum_state == "computing"
    display.deleteLater()  # 计算中销毁 owner
    del display
else:
    from strange_uta_game.frontend.editor.timing.waveform_advanced_dialog import (
        WaveformAdvancedDialog,
    )

    parent = QWidget()
    dialog = WaveformAdvancedDialog(
        {"display_mode": "waveform"}, (samples, 44100), parent=parent
    )
    dialog._on_detect_bpm()
    assert dialog._bpm_running
    dialog.deleteLater()  # 检测中销毁 owner
    parent.deleteLater()
    del dialog, parent

QTimer.singleShot(5000, app.quit)  # 留时间给取消/自回收链
app.exec()
print("survived", mode)
sys.exit(0)
