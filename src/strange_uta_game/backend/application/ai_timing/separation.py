"""standalone 人声分离（2026-08 补齐：点击自动对齐自动分人声）。

分离在共享 AI Runtime（``ai_runtime`` venv，含 ``audio-separator``）的
**子进程**中执行：不阻塞 UI、取消可直接终止进程、崩溃隔离。模型使用
UVR-MDX-NET-Inst_HQ_3（人声/伴奏双输出），自动下载到统一 ``ai_models``
目录（与对齐模型同源管理）。产物按工作台命名约定写为
``<原文件名>_人声.wav``，可直接被同目录严格匹配（§6.1 ③）与缓存复用。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable, Optional

ProgressFn = Callable[[str, int, str], None]
CancelFn = Callable[[], bool]

SEPARATION_MODEL = "UVR-MDX-NET-Inst_HQ_3.onnx"
VOCAL_STEM = "人声"

_SCRIPT = r"""
import sys
inp, out_dir, model_dir = sys.argv[1], sys.argv[2], sys.argv[3]
print("stage:load:加载分离模型", flush=True)
from audio_separator.separator import Separator
sep = Separator(
    model_file_dir=model_dir,
    output_dir=out_dir,
    output_format="WAV",
)
sep.load_model(model_filename="UVR-MDX-NET-Inst_HQ_3.onnx")
print("stage:separate:分离处理中", flush=True)
outputs = sep.separate(inp)
stem_files = [f for f in outputs if "Vocals" in f or "vocals" in f]
if not stem_files:
    # 轨名兜底：排除伴奏轨后取剩余（UVR 系输出通常仅人声/伴奏两条）
    stem_files = [f for f in outputs if "nstrumental" not in f]
if not stem_files:
    raise SystemExit("未找到人声输出轨: %r" % (outputs,))
import os, shutil
# audio-separator 输出名为 <stem>_Vocals_.wav → 归一为 <stem>_人声.wav
src = stem_files[0]
dst = os.path.join(out_dir, os.path.splitext(os.path.basename(inp))[0] + "_人声.wav")
shutil.move(os.path.join(out_dir, src), dst)
print("done:" + dst, flush=True)
"""


class StandaloneVocalSeparator:
    """用共享 Runtime 子进程执行一次人声分离。

    Args:
        runtime_python: AI Runtime 的 python.exe（空 = 当前解释器，
            仅在主环境恰好装有 audio-separator 时可用）。
        model_root: 统一模型根（分离模型与对齐模型同源）。
    """

    def __init__(self, runtime_python: str, model_root: Path):
        self._python = runtime_python
        self._model_root = Path(model_root) if model_root else None

    def identity(self) -> dict:
        return {"model": SEPARATION_MODEL, "stem": VOCAL_STEM, "params": {}}

    def available(self) -> bool:
        if not self._python or not Path(self._python).is_file():
            return False
        try:
            completed = subprocess.run(
                [self._python, "-c", "import audio_separator"],
                capture_output=True,
                timeout=30,
            )
            return completed.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def separate(
        self,
        source_path: Path,
        progress: ProgressFn,
        cancel: CancelFn,
    ) -> Path:
        source = Path(source_path)
        if not self.available():
            raise RuntimeError(
                "分离环境未安装：请先在弹窗中安装对齐环境（含分离能力）"
            )
        out_dir = source.parent
        cmd = [
            self._python,
            "-c",
            _SCRIPT,
            str(source),
            str(out_dir),
            str(self._model_root),
        ]
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        result_path: Optional[Path] = None
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if cancel():
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise RuntimeError("已取消人声分离")
            if line.startswith("stage:load"):
                progress("separation", 10, line.split(":", 2)[2])
            elif line.startswith("stage:separate"):
                progress("separation", 60, line.split(":", 2)[2])
            elif line.startswith("done:"):
                result_path = Path(line.split(":", 1)[1])
                progress("separation", 100, "分离完成")
        returncode = proc.wait()
        if result_path is not None and result_path.is_file():
            return result_path
        raise RuntimeError(
            f"人声分离失败（返回码 {returncode}）。"
            "请确认分离环境已完整安装后重试"
        )


__all__ = ["StandaloneVocalSeparator", "SEPARATION_MODEL", "VOCAL_STEM"]
