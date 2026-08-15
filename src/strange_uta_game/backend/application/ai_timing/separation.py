"""standalone 人声分离（2026-08 补齐：点击自动对齐自动分人声）。

分离在共享 AI Runtime（``ai_runtime`` venv，含 ``audio-separator``）的
**子进程**中执行：不阻塞 UI、取消可直接终止进程、崩溃隔离。模型使用
UVR-MDX-NET-Inst_HQ_3（人声/伴奏双输出），自动下载到统一 ``ai_models``
目录（与对齐模型同源管理）。产物按工作台命名约定写为
``<原文件名>_人声.wav``，可直接被同目录严格匹配（§6.1 ③）与缓存复用。
"""

from __future__ import annotations

import subprocess
from collections import deque
from pathlib import Path
from typing import Callable, Optional

ProgressFn = Callable[[str, int, str], None]
CancelFn = Callable[[], bool]

SEPARATION_MODEL = "UVR-MDX-NET-Inst_HQ_3.onnx"
VOCAL_STEM = "人声"


def _tr(s: str) -> str:
    from PyQt6.QtCore import QCoreApplication

    return QCoreApplication.translate("AiTimingSeparation", s)


def ffmpeg_missing_message(embedded: bool = False) -> str:
    """FFmpeg 缺失时的阻断消息；提示按运行模式引导到对应入口。

    embedded 下 SUG 自身的 ffmpeg 设置入口隐藏（EMBEDDING §5），
    必须引导到工作台。raise 时才调 _tr：模块级常量会在 import 期
    固化语言，切语言后不刷新（WORKFLOW §六）。
    """
    if embedded:
        return _tr(
            "人声分离需要 FFmpeg，但未找到可用的 FFmpeg。"
            "嵌入式运行的 FFmpeg 由工作台统一管理，"
            "请检查工作台设置中的 FFmpeg 配置后重试"
        )
    return _tr(
        "人声分离需要 FFmpeg，但未在系统中找到。"
        "请在「设置 → 关于/语言」中配置 FFmpeg 路径"
        "（或安装 FFmpeg 并加入系统 PATH）后重试"
    )


def resolve_ffmpeg_exe() -> str:
    """解析本机可用的 ffmpeg 完整路径；找不到返回空串。

    audio-separator 0.44.x 在 ``Separator()`` 构造时就强制探测 PATH 上的
    ffmpeg（缺失直接抛 FileNotFoundError，子进程退出码 1 且宿主只能看到
    返回码）。复用主程序的解析口径：用户在「设置 → 关于/语言」配置的
    路径优先，其次系统 PATH——配置路径不在 PATH 上时由调用方注入子进程。
    """
    try:
        from strange_uta_game.backend.infrastructure.audio.video_converter import (
            get_ffmpeg_path,
        )

        configured = get_ffmpeg_path()
        if configured and configured != "ffmpeg":
            return configured if Path(configured).is_file() else ""
        import shutil

        return shutil.which("ffmpeg") or ""
    except Exception:
        return ""


def _failure_hint(tail_text: str, *, embedded: bool = False) -> str:
    """按子进程输出尾部识别常见失败原因，给出可操作提示。

    embedded 模式下 SUG 自身的 ffmpeg/网络设置入口被隐藏（EMBEDDING
    §5），提示必须引导到工作台，否则用户按提示找不到可操作的地方。
    """
    lowered = tail_text.lower()
    if "ffmpeg" in lowered:
        if embedded:
            return _tr("FFmpeg 不可用：请检查工作台设置中的 FFmpeg 配置")
        return _tr(
            "FFmpeg 不可用：请在「设置 → 关于/语言」配置 FFmpeg 路径后重试"
        )
    if any(
        key in lowered
        for key in (
            "github",
            "connectionerror",
            "failed to download",
            "max retries",
            "timed out",
            "ssl",
        )
    ):
        if embedded:
            return _tr(
                "分离模型首次使用需从 GitHub 下载，当前下载失败："
                "请检查网络（代理跟随工作台的网络设置）后重试"
            )
        return _tr(
            "分离模型首次使用需从 GitHub 下载，当前下载失败："
            "请检查网络（可在「设置 → 网络与代理」配置代理）后重试"
        )
    return ""

_SCRIPT = r"""
import sys
inp, out_dir, model_dir = sys.argv[1], sys.argv[2], sys.argv[3]
# audio-separator 的进度走 logging：重定向到 stdout，宿主读行循环才能
# 收到模型下载/加载阶段的输出（否则该阶段取消无响应、无进度）
import logging
logging.basicConfig(stream=sys.stdout, level=logging.INFO, format="%(message)s")
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
        runtime_python: AI Runtime 的 python.exe 路径，或返回路径的
            零参 callable（惰性读取：安装/修复完成后路径才写入设置，
            同一次弹窗会话内 prober 必须能立即反映新值）。空 = 当前
            解释器，仅在主环境恰好装有 audio-separator 时可用。
        model_root: 统一模型根（分离模型与对齐模型同源）。
        proxy: 传给分离子进程的网络代理 URL；模型首次使用需从
            GitHub 下载，代理设置必须随之注入子进程环境。
        embedded: 嵌入式运行（工作台宿主）。FFmpeg 解析来源不变
            （宿主注入的 tools.ffmpeg_path），但失败提示引导到
            工作台设置而非 SUG 自身的隐藏入口。
    """

    def __init__(
        self,
        runtime_python,
        model_root: Path,
        *,
        proxy: str = "",
        embedded: bool = False,
    ):
        self._python = runtime_python
        self._model_root = Path(model_root) if model_root else None
        self._proxy = str(proxy or "")
        self._embedded = bool(embedded)

    def _python_exe(self) -> str:
        value = self._python() if callable(self._python) else self._python
        return str(value or "")

    def identity(self) -> dict:
        return {"model": SEPARATION_MODEL, "stem": VOCAL_STEM, "params": {}}

    def available(self) -> bool:
        python = self._python_exe()
        if not python or not Path(python).is_file():
            return False
        try:
            # 必须探测真实入口：顶层包可导入不代表 separator 模块可用
            # （裸 audio-separator 缺 onnxruntime/audioread 时顶层仍成功）
            from strange_uta_game.backend.infrastructure.windows import (
                hidden_subprocess_kwargs,
            )

            completed = subprocess.run(
                [
                    python,
                    "-c",
                    "from audio_separator.separator import Separator",
                ],
                capture_output=True,
                timeout=30,
                **hidden_subprocess_kwargs(),
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
        python = self._python_exe()
        if not python or not Path(python).is_file() or not self.available():
            raise RuntimeError(
                _tr("分离环境未安装：请先在弹窗中安装对齐环境（含分离能力）")
            )
        out_dir = source.parent
        # audio-separator 构造即探测 ffmpeg（缺失 → 子进程退出码 1，
        # 宿主只能看到返回码）：启动前解析并把其目录注入子进程 PATH，
        # 未安装时给出可操作的中文错误而不是裸返回码
        ffmpeg = resolve_ffmpeg_exe()
        if not ffmpeg:
            raise RuntimeError(
                ffmpeg_missing_message(embedded=self._embedded)
            )
        cmd = [
            python,
            "-c",
            _SCRIPT,
            str(source),
            str(out_dir),
            str(self._model_root),
        ]
        import os

        env = dict(os.environ)
        # 中文 Windows 子进程默认按 GBK 写管道，宿主按 UTF-8 读会乱码
        env["PYTHONIOENCODING"] = "utf-8"
        # 用户配置的 ffmpeg 通常不在 PATH 上（如独立目录的 ffmpeg.exe）；
        # 即使在，前置注入也无害
        ffmpeg_dir = str(Path(ffmpeg).parent)
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
        if self._proxy:
            # 分离模型首次使用需从 GitHub 下载：主程序解析出的代理
            # （系统/手动）必须传给子进程，requests 会读这些环境变量
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                env[key] = self._proxy
        from strange_uta_game.backend.infrastructure.windows import (
            hidden_subprocess_kwargs,
        )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **hidden_subprocess_kwargs(),
        )
        result_path: Optional[Path] = None
        assert proc.stdout is not None
        import re as _re

        # 子进程的失败输出（traceback 混入 stdout）此前被逐行读取后丢弃，
        # 报错只剩返回码，外部用户反馈完全不可诊断——保留有界尾部，
        # 失败时并入异常消息
        tail: deque = deque(maxlen=24)

        _ansi_re = _re.compile("\x1b\\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b-\x1f]")
        # tqdm 进度行（audio_separator 分块处理）形如：
        #   " 50%|█████| 1/2 [00:02<00:02,  2.21s/it]"（\r 分隔、可能同行多段）
        # —— 解析出 k/N 与 tqdm 自带的剩余时间，喂给进度回调（速度+ETA）
        _tqdm_re = _re.compile(r"(\d+)/(\d+) \[[^\]<]*<(\d+):(\d{2})")
        for raw_line in proc.stdout:
            m = None
            for m2 in _tqdm_re.finditer(raw_line):
                m = m2  # 取最后一段（\r 覆盖写时同行会有多个状态）
            if m is not None:
                done, total = int(m.group(1)), int(m.group(2))
                rem_s = int(m.group(3)) * 60 + int(m.group(4))
                if total > 0 and done <= total:
                    pct = 60 + int(35 * done / total)
                    rate = (rem_s / (total - done)) if done < total else 0.0
                    progress(
                        "separation",
                        min(95, pct),
                        f"分离处理 {done}/{total} 块，"
                        f"预计剩余 {int(rem_s // 60)}:{rem_s % 60:02d}"
                        + (f"（{rate:.1f}s/块）" if rate else ""),
                    )
                continue
            line = _ansi_re.sub("", raw_line)
            line = line.replace("\r", " ").strip()
            if not line:
                continue
            if len(line) > 160:
                line = line[:159] + "…"
            tail.append(line)
            if cancel():
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:
                    pass
                raise RuntimeError(_tr("已取消人声分离"))
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
        detail_lines = [t for t in tail if t][-3:]
        detail = "；".join(detail_lines)
        # 提示基于全部保留输出判断（如 FFmpeg 缺失行出现在 traceback 之前，
        # 会被 detail 的 [-3:] 截掉），展示只取末尾几行
        hint = _failure_hint(" ".join(tail), embedded=self._embedded)
        if detail:
            raise RuntimeError(
                _tr("人声分离失败（返回码 {code}）。").format(code=returncode)
                + hint
                + _tr("子进程输出：{output}").format(output=detail)
            )
        raise RuntimeError(
            _tr(
                "人声分离失败（返回码 {code}）。"
                "请确认分离环境已完整安装后重试"
            ).format(code=returncode)
        )


def host_first_separation(
    host, standalone: StandaloneVocalSeparator
) -> tuple:
    """embedded 分离编排：宿主优先，宿主未配置时回落 AI Runtime 内置分离。

    AI 安装器（自建 venv 与方案 B 共享解释器两条路径）本来就携带
    audio-separator——只为 AI 打轴的用户不必强配工作台第 2 步的分离
    环境；宿主可用时仍优先复用（会话人声零分离、跟随工作台设置）。

    Returns:
        (executor, identity, prober, follows_host)：
        executor 兼容 AiTimingService 的 ProgressFn/CancelFn 签名；
        follows_host 为构造时刻宿主是否可用（弹窗状态行展示口径）。
    """
    def _host_available() -> bool:
        try:
            return bool(host.separation_status().get("available"))
        except Exception:
            return False

    def _executor(source_path: Path, progress: ProgressFn, cancel: CancelFn) -> Path:
        if _host_available():
            return host.separate_vocal(source_path, progress, cancel)
        progress(
            "vocal",
            12,
            _tr("工作台分离环境未配置，使用 AI 运行环境内置分离"),
        )
        return standalone.separate(source_path, progress, cancel)

    def _identity() -> dict:
        if _host_available():
            return host.effective_identity()
        return standalone.identity()

    def _available() -> bool:
        return _host_available() or standalone.available()

    return _executor, _identity, _available, _host_available()


__all__ = [
    "StandaloneVocalSeparator",
    "host_first_separation",
    "SEPARATION_MODEL",
    "VOCAL_STEM",
    "ffmpeg_missing_message",
    "resolve_ffmpeg_exe",
]
