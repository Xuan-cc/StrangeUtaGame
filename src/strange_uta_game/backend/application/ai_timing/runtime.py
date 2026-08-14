"""AI 打轴 Runtime 探测与安装（阶段 D）。

对齐 Runtime = 独立的 Python 环境 + PyTorch / Transformers 等重型依赖，
与 SUG 主程序安装互相隔离。worker（阶段 C）默认以当前解释器启动；
standalone 配置了 ``runtime_python`` 后改由该解释器启动。

- ``AiRuntimeManager.probe``：子进程导入探测（torch/transformers/
  soundfile 版本 + CUDA 可用性），中文状态报告；
- ``AiRuntimeManager.install``：venv + pip 安装（镜像可配），
  逐行流式回报进度、支持协作取消；pip 执行器可注入以便离线测试。

真实安装烟测（Windows CPU / NVIDIA）属于 §12.3 手动矩阵，
CI 仅覆盖 probe/编排逻辑。
"""

import json
import subprocess
import sys
import venv
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

PROGRESS_CB = Callable[[int, str], None]
CANCEL_CB = Callable[[], bool]

# 对齐 worker 的依赖集（版本由 Runtime 构建基线统一 pin，§11 阶段 H）
RUNTIME_REQUIREMENTS: List[str] = [
    "torch",
    "torchaudio",
    "transformers",
    "soundfile",
    "huggingface_hub",
    # standalone 人声分离（UVR/MDX 系模型；CPU 也可跑，模型自动下载）
    "audio-separator[cpu]",
    # audio-separator 0.44.x 使用旧版 librosa API（get_duration(filename=)），
    # 0.11 起已移除该参数——必须钉住
    "librosa==0.10.2.post1",
]

_PROBE_CODE = (
    "import json;"
    "import torch,transformers,soundfile,audio_separator;"
    "print(json.dumps({'torch': torch.__version__,"
    " 'cuda': bool(torch.cuda.is_available()),"
    " 'transformers': transformers.__version__}))"
)


class AiRuntimeError(RuntimeError):
    """Runtime 操作错误（中文消息）。"""


@dataclass
class RuntimeStatus:
    available: bool
    python_path: str = ""
    torch_version: str = ""
    transformers_version: str = ""
    cuda_available: bool = False
    message: str = ""

    @property
    def summary(self) -> str:
        if not self.available:
            return self.message or "对齐运行环境不可用"
        device = "CUDA" if self.cuda_available else "CPU"
        return (
            f"PyTorch {self.torch_version} · Transformers "
            f"{self.transformers_version} · {device}"
        )


class AiRuntimeManager:
    """探测 / 安装对齐 Runtime（standalone 自管；embedded 由工作台注入）。

    Args:
        pip_runner: 可注入的 pip 执行函数
            ``(python_exe, args, on_line, cancel) -> returncode``；
            默认实现以子进程运行 pip 并流式转发输出。
    """

    def __init__(
        self,
        pip_runner: Optional[
            Callable[[str, List[str], Callable[[str], None], CANCEL_CB], int]
        ] = None,
    ):
        self._pip_runner = pip_runner or self._default_pip_runner

    # ── 探测 ──

    def probe(self, python_exe: str = "", timeout_s: float = 30.0) -> RuntimeStatus:
        """子进程探测目标解释器是否具备对齐依赖。"""
        exe = python_exe or sys.executable
        if not Path(exe).is_file() and exe != sys.executable:
            return RuntimeStatus(
                available=False,
                python_path=exe,
                message=f"Python 路径不存在：{exe}",
            )
        try:
            completed = subprocess.run(
                [exe, "-c", _PROBE_CODE],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            return RuntimeStatus(
                available=False, python_path=exe, message="探测超时"
            )
        except OSError as exc:
            return RuntimeStatus(
                available=False, python_path=exe, message=f"无法启动 Python：{exc}"
            )
        if completed.returncode != 0:
            missing = self._parse_missing_module(completed.stderr or "")
            return RuntimeStatus(
                available=False,
                python_path=exe,
                message=(
                    f"缺少对齐依赖（{missing}），请下载对齐运行环境"
                    if missing
                    else "对齐运行环境校验失败"
                ),
            )
        try:
            info = json.loads(completed.stdout.strip().splitlines()[-1])
        except (json.JSONDecodeError, IndexError, ValueError):
            return RuntimeStatus(
                available=False, python_path=exe, message="探测输出无法解析"
            )
        return RuntimeStatus(
            available=True,
            python_path=exe,
            torch_version=str(info.get("torch", "")),
            transformers_version=str(info.get("transformers", "")),
            cuda_available=bool(info.get("cuda", False)),
        )

    @staticmethod
    def _parse_missing_module(stderr: str) -> str:
        for line in stderr.splitlines():
            if "ModuleNotFoundError" in line:
                try:
                    return line.split("No module named")[-1].strip().strip("' ")
                except IndexError:
                    break
        return ""

    # ── 安装 ──

    def install(
        self,
        target_dir: Path,
        *,
        proxy: str = "",
        index_url: str = "",
        extra_index_url: str = "",
        mirror: str = "",
        requirements: Optional[List[str]] = None,
        progress: Optional[PROGRESS_CB] = None,
        cancel: Optional[CANCEL_CB] = None,
    ) -> RuntimeStatus:
        """在 target_dir 创建 venv 并安装依赖；返回探测结果。

        Args:
            index_url / extra_index_url: pip 源（如 PyTorch CPU 轮子源）。
            mirror: pip 镜像（如清华源），优先级低于 index_url。
        """
        progress = progress or (lambda p, m: None)
        cancel = cancel or (lambda: False)

        target_dir = Path(target_dir)
        progress(2, f"创建虚拟环境：{target_dir}")
        try:
            venv.create(target_dir, with_pip=True, clear=False)
        except (OSError, ValueError) as exc:
            raise AiRuntimeError(f"创建虚拟环境失败：{exc}") from exc

        python_exe = self._venv_python(target_dir)
        if not python_exe.is_file():
            raise AiRuntimeError("虚拟环境创建后未找到 python.exe")

        if cancel():
            raise AiRuntimeError("已取消")

        # pip 参数一次构建：dry-run 预估与正式安装复用同一组参数
        # （此前 dry-run 引用了尚未定义的 args，UnboundLocalError 被静默
        # 吞掉，包数量预估从未生效，pip 进度一直退化为逐行 +1 模式）
        args: List[str] = ["-m", "pip", "install", "--disable-pip-version-check"]
        if proxy:
            args += ["--proxy", proxy]
        if index_url:
            args += ["--index-url", index_url]
        if extra_index_url:
            args += ["--extra-index-url", extra_index_url]
        elif mirror:
            args += ["-i", mirror]
        args += list(requirements or RUNTIME_REQUIREMENTS)

        import os
        import time as _time

        pip_env = dict(os.environ)
        if proxy:
            pip_env["HTTP_PROXY"] = proxy
            pip_env["HTTPS_PROXY"] = proxy
        self._pip_env = pip_env

        # dry-run 预估包数（流式转发解析行，解析阶段界面不空转）；
        # 失败（离线/源不可达）静默退化为无总量模式
        total_packages = 0
        size_text = ""
        try:
            import json as _json
            import tempfile as _tf

            report_path = Path(_tf.gettempdir()) / "krok-pip-dryrun.json"
            try:
                report_path.unlink()
            except OSError:
                pass
            dry_lines = 0

            def _dry_line(line: str) -> None:
                nonlocal dry_lines
                text = line.strip()
                if text:
                    dry_lines += 1
                    progress(min(9, 3 + dry_lines // 3), f"解析依赖：{text}")

            dry_rc = self._pip_runner(
                python_exe,
                [*args, "--dry-run", "--report", str(report_path)],
                _dry_line,
                cancel,
            )
            if dry_rc == 0 and report_path.is_file():
                report = _json.loads(report_path.read_text(encoding="utf-8"))
                items = report.get("install", []) or []
                total_packages = len(items)
                total_mb = (
                    sum(
                        float(
                            (it.get("download_info") or {})
                            .get("archive_info", {})
                            .get("size")
                            or 0
                        )
                        for it in items
                    )
                    / 1024
                    / 1024
                )
                # 部分pip版本的 report 不含体积字段：仅在确有数值时展示
                if total_mb >= 1:
                    size_text = f"，总下载量约 {total_mb:.0f}MB"
        except Exception:
            total_packages = 0
            size_text = ""
        if cancel():
            raise AiRuntimeError("已取消")

        if total_packages:
            progress(10, f"共 {total_packages} 个包{size_text}，开始安装")
        else:
            progress(10, "安装对齐依赖（体积较大，可能需要数分钟）")

        started = _time.monotonic()
        fetched = 0
        fallback_lines = 0

        def _on_line(line: str) -> None:
            nonlocal fetched, fallback_lines
            text = line.strip()
            if not text:
                return
            if text.startswith("Successfully installed"):
                progress(95, text)
                return
            if total_packages:
                # 包粒度：pip 完成全部安装才输出 Successfully，按它计数没有
                # 中间进度可用——改按每个包的下载/命中缓存行推进
                lower = text.lower()
                is_fetch = lower.startswith("downloading") or lower.startswith(
                    "using cached"
                )
                if is_fetch and ".metadata" not in lower:
                    fetched += 1
                    elapsed_min = max(0.02, (_time.monotonic() - started) / 60)
                    progress(
                        min(94, 10 + int(85 * fetched / total_packages)),
                        f"获取依赖 {min(fetched, total_packages)}/"
                        f"{total_packages}"
                        f"（{fetched / elapsed_min:.1f} 包/分）：{text}",
                    )
                    return
                # 其他输出（Collecting/元数据/安装）：转发但不推进百分比，
                # 避免解析行把百分比顶满导致 ETA 失真
                progress(
                    min(94, 10 + int(85 * fetched / total_packages)), text
                )
                return
            # 无总量模式：渐进爬升（每行 +1，封顶 94），避免恒停导致 ETA 失效
            fallback_lines += 1
            progress(min(94, 10 + fallback_lines), text)

        returncode = self._pip_runner(python_exe, args, _on_line, cancel)
        if cancel():
            raise AiRuntimeError("已取消")
        if returncode != 0:
            raise AiRuntimeError(f"依赖安装失败（pip 返回码 {returncode}）")

        progress(97, "校验运行环境")
        status = self.probe(str(python_exe))
        progress(100, "运行环境就绪" if status.available else "运行环境校验未通过")
        if not status.available:
            raise AiRuntimeError(status.message)
        return status

    @staticmethod
    def _venv_python(target_dir: Path) -> Path:
        if sys.platform == "win32":
            return target_dir / "Scripts" / "python.exe"
        return target_dir / "bin" / "python"

    def _default_pip_runner(
        self,
        python_exe: str,
        args: List[str],
        on_line: Callable[[str], None],
        cancel: CANCEL_CB,
    ) -> int:
        process = subprocess.Popen(
            [python_exe, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=getattr(self, "_pip_env", None),
        )
        assert process.stdout is not None
        for line in process.stdout:
            if cancel():
                process.kill()
                return 1
            on_line(line)
        return process.wait()


__all__ = [
    "RUNTIME_REQUIREMENTS",
    "AiRuntimeError",
    "RuntimeStatus",
    "AiRuntimeManager",
]
