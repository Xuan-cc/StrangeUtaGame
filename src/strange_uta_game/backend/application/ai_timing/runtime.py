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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Tuple

PROGRESS_CB = Callable[[int, str], None]
CANCEL_CB = Callable[[], bool]

# 对齐 worker 的依赖集（版本由 Runtime 构建基线统一 pin，§11 阶段 H）
# 对齐 Runtime 的依赖集（版本由 Runtime 构建基线统一 pin，§11 阶段 H）。
# transformers 钉在实测与默认模型（NextFire/mms-300m，config 含
# spectrogram_scale）兼容的 5.15.0：托管 runtime 上装到更新版本时
# from_pretrained 以 Unexpected keyword argument 'spectrogram_scale'
# 拒绝加载模型（2026-08 实测），升级基线前不要解开。
PINNED_TRANSFORMERS = "transformers==5.15.0"

RUNTIME_REQUIREMENTS: List[str] = [
    "torch",
    "torchaudio",
    PINNED_TRANSFORMERS,
    "soundfile",
    "huggingface_hub",
    # standalone 人声分离（UVR/MDX 系模型；CPU 也可跑，模型自动下载）
    "audio-separator[cpu]",
    # audio-separator 0.44.x 使用旧版 librosa API（get_duration(filename=)），
    # 0.11 起已移除该参数——必须钉住
    "librosa==0.10.2.post1",
]

# Windows 上 PyPI 的 torch 默认是 CPU-only wheel，GPU 推理必须显式走
# PyTorch 官方 CUDA 索引（cu128 为当前稳定完整索引，cp313/cp314
# win_amd64 wheel 已确认存在；RTX 50 系 Blackwell 需要 cu128 及以上）。
TORCH_CUDA_TAG = "cu128"
TORCH_CUDA_INDEX_URL = f"https://download.pytorch.org/whl/{TORCH_CUDA_TAG}"

# CUDA 路由的版本钉子。两个原因都必须钉到「版本+cu128 本地标签」：
# 1. PyPI 的 torch 版本可能高于 CUDA 索引（实测 PyPI 2.13.0+cpu vs
#    cu128 索引 2.11.0+cu128），不钉版本 pip 依旧选 PyPI 的 CPU wheel；
# 2. 只钉 ``torch==2.11.0`` 时，已装的 2.11.0+cpu torchaudio 会被视为
#    已满足——CPU 变体 torchaudio 与 CUDA 变体 torch 混装不受支持。
# 升级 Runtime 基线时同步 bump 版本与 TORCH_CUDA_TAG。
TORCH_CUDA_VERSION = "2.11.0"

# CUDA 版运行环境的磁盘需求（torch cu wheel 约 3GB 下载 / 6GB 落盘，
# 加依赖与缓存余量）
CUDA_RUNTIME_DISK_GB = 8

# 打包版发行包路线（install_from_release）的 CUDA 峰值需求：底座 zip
# 0.15GB + torch cu128 wheel 3.1GB + 解压与安装双份 ≈ 9.5GB；完成后约
# 6GB（staging 缓存安装成功后自动清理）
RUNTIME_RELEASE_CUDA_PEAK_GB = 9.5

# ── 托管 Runtime Release 契约（分支 B：standalone 复用工作台底座）──
# 与 krok_helper/audio_processing/separation/integration.py 同源；工作台
# 发新 runtime release 时需同步 bump 这里（两仓耦合点，见 EMBEDDING.md）。
RUNTIME_RELEASE_REPO = "karaoke-studio/karaoke-studio-runtime"
RUNTIME_RELEASE_PYMSS_VERSION = "2.0.18"
RUNTIME_RELEASE_REVISION = "1"
RUNTIME_RELEASE_TAG = (
    f"pymss-runtime-v{RUNTIME_RELEASE_PYMSS_VERSION}"
    f"-r{RUNTIME_RELEASE_REVISION}"
)
RUNTIME_RELEASE_ASSET_PREFIX = "KaraokeStudio-PyMSS"
RUNTIME_VARIANT_CPU = "windows-cpu"
RUNTIME_VARIANT_CUDA = "windows-cu128"
# cu128 wheel 需要的最低 NVIDIA 驱动（与工作台同口径）
CUDA_12_8_MIN_WINDOWS_DRIVER = (570, 65)

from strange_uta_game.backend.infrastructure.windows import (
    hidden_subprocess_kwargs,
)

_PROBE_CODE = (
    "import json;"
    "import torch,transformers,soundfile,audio_separator;"
    "print(json.dumps({'torch': torch.__version__,"
    " 'cuda': bool(torch.cuda.is_available()),"
    " 'gpu': torch.cuda.get_device_name(0) if torch.cuda.is_available() else '',"
    " 'transformers': transformers.__version__}))"
)


def detect_nvidia_gpu() -> str:
    """探测 NVIDIA GPU 名称（nvidia-smi）；无独显/无驱动/失败返回空串。

    在宿主进程执行即可——探测的解释器与本机是同一块显卡；CPU 版
    torch 看不到 CUDA 设备，但 nvidia-smi 与 torch 版本无关。
    """
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            **hidden_subprocess_kwargs(),
        )
        if completed.returncode == 0:
            return (completed.stdout or "").strip().splitlines()[0].strip()
    except (OSError, subprocess.TimeoutExpired, IndexError):
        pass
    return ""


def detect_torch_build(python_exe: str) -> Optional[Tuple[str, str]]:
    """探测目标解释器里 torch 的 (基线版本, 变体标签)。

    如 ``2.7.1+cu128`` → ``("2.7.1", "cu128")``；``2.7.1`` / ``2.7.1+cpu``
    → ``("2.7.1", "cpu")``。无法导入 torch（环境未装/损坏）返回 None。

    增量安装（方案 B）用它动态配对 torchaudio：托管 runtime 升级
    torch 后无需改代码，配对版本自动跟随。
    """
    if not python_exe:
        # 空解释器（打包未装环境）绝不回落 sys.executable——那会把
        # 打包应用当 python 启动（整个应用再开一遍的幽灵进程）
        return None
    try:
        completed = subprocess.run(
            [python_exe, "-c", "import torch; print(torch.__version__)"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=90,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    lines = (completed.stdout or "").strip().splitlines()
    raw = lines[-1].strip() if lines else ""
    if not raw:
        return None
    if "+" in raw:
        version, tag = raw.split("+", 1)
    else:
        version, tag = raw, "cpu"
    version = version.strip()
    tag = tag.strip().lower()
    if not version or not tag:
        return None
    return version, tag


def nvidia_driver_supports_cu128() -> bool:
    """NVIDIA 驱动是否满足 cu128 wheel 的最低要求（570.65）。"""
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            **hidden_subprocess_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if completed.returncode != 0:
        return False
    for line in (completed.stdout or "").splitlines():
        fields = line.strip().split(".")
        if len(fields) < 2 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        if (int(fields[0]), int(fields[1])) >= CUDA_12_8_MIN_WINDOWS_DRIVER:
            return True
    return False


def release_variant_for_host() -> str:
    """按本机硬件选择 runtime release 变体（cu128 需显卡且驱动达标）。"""
    if detect_nvidia_gpu() and nvidia_driver_supports_cu128():
        return RUNTIME_VARIANT_CUDA
    return RUNTIME_VARIANT_CPU


def release_manifest_url(variant: str) -> str:
    name = (
        f"{RUNTIME_RELEASE_ASSET_PREFIX}-{variant}"
        f"-v{RUNTIME_RELEASE_PYMSS_VERSION}-r{RUNTIME_RELEASE_REVISION}.json"
    )
    return (
        f"https://github.com/{RUNTIME_RELEASE_REPO}/releases/download/"
        f"{RUNTIME_RELEASE_TAG}/{name}"
    )


def fetch_runtime_release_manifest(
    variant: str, *, proxy: str = ""
) -> dict:
    """拉取并校验 runtime release 的 JSON 清单（schema 1）。"""
    import requests

    url = release_manifest_url(variant)
    try:
        resp = requests.get(
            url,
            timeout=(10, 30),
            proxies=({"http": proxy, "https": proxy} if proxy else None),
        )
        resp.raise_for_status()
        manifest = resp.json()
    except Exception as exc:
        raise AiRuntimeError(f"获取运行环境清单失败：{exc}") from exc
    if manifest.get("schema") != 1:
        raise AiRuntimeError("运行环境清单格式不受支持")
    parts = ((manifest.get("archive") or {}).get("parts")) or []
    if not parts:
        raise AiRuntimeError("运行环境清单缺少下载分片")
    return manifest


def _safe_extract_path(base: Path, rel: str) -> Optional[Path]:
    """解压目标安全化：拒绝绝对路径与 ``..`` 逃逸。"""
    if not rel or Path(rel).is_absolute():
        return None
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base.resolve())
    except ValueError:
        return None
    return candidate


def _replace_dir_with_retry(src: Path, dst: Path) -> None:
    """替换目录，带重试：Windows 上杀软/索引器可能短暂锁定刚解压的文件。"""
    import shutil as _shutil
    import time as _time

    last: Optional[Exception] = None
    for _ in range(5):
        try:
            if dst.exists():
                _shutil.rmtree(dst)
            src.replace(dst)
            return
        except OSError as exc:
            last = exc
            _time.sleep(0.5)
    raise AiRuntimeError(f"替换运行环境目录失败：{last}")


def _download_verified(
    url: str,
    dest: Path,
    *,
    expected_size: int,
    expected_sha256: str,
    proxy: str = "",
    progress: Optional[PROGRESS_CB] = None,
    cancel: Optional[CANCEL_CB] = None,
    base_pct: int = 0,
    span_pct: int = 10,
    label: str = "",
) -> None:
    """流式下载到 ``.part`` 并做 sha256 校验（字节级进度 + 速度/ETA 文案）。

    已存在且校验通过的文件直接复用（修复重装不重下大文件）。
    """
    import hashlib

    sha = hashlib.sha256()

    def _ok(path: Path) -> bool:
        if not path.is_file() or path.stat().st_size != expected_size:
            return False
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest() == expected_sha256

    if _ok(dest):
        progress(base_pct + span_pct, f"{label}已存在且校验通过，复用")
        return

    part = dest.with_name(dest.name + ".part")
    part.parent.mkdir(parents=True, exist_ok=True)
    try:
        import requests

        resp = requests.get(
            url,
            stream=True,
            timeout=(10, 60),
            proxies=({"http": proxy, "https": proxy} if proxy else None),
        )
        resp.raise_for_status()
    except Exception as exc:
        raise AiRuntimeError(f"下载失败（{label or url}）：{exc}") from exc

    import time as _time

    done = 0
    t0 = _time.monotonic()
    try:
        with part.open("wb") as fh:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if cancel and cancel():
                    raise AiRuntimeError("已取消")
                if not chunk:
                    continue
                fh.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if expected_size and progress:
                    elapsed = max(0.001, _time.monotonic() - t0)
                    rate = done / elapsed / 1024 / 1024
                    remain_s = (
                        int((expected_size - done) / 1024 / 1024 / max(rate, 0.001))
                        if rate > 0.001
                        else 0
                    )
                    m, s = divmod(remain_s, 60)
                    progress(
                        base_pct
                        + int(span_pct * min(1.0, done / max(1, expected_size))),
                        f"{label}{done // 1024 // 1024}MB/"
                        f"{expected_size // 1024 // 1024}MB"
                        f"（{rate:.1f}MB/s，预计剩余 {m}:{s:02d}）",
                    )
    except AiRuntimeError:
        raise
    except Exception as exc:
        raise AiRuntimeError(f"下载失败（{label or url}）：{exc}") from exc

    if sha.hexdigest() != expected_sha256 or (
        expected_size and done != expected_size
    ):
        raise AiRuntimeError(f"下载校验失败（{label or url}），请重试")
    part.replace(dest)


class AiRuntimeError(RuntimeError):
    """Runtime 操作错误（中文消息）。"""


@dataclass
class RuntimeStatus:
    available: bool
    python_path: str = ""
    torch_version: str = ""
    transformers_version: str = ""
    cuda_available: bool = False
    gpu_name: str = ""
    """NVIDIA GPU 名称（CUDA 可用时来自 torch，否则来自 nvidia-smi）。"""
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
        if not python_exe and getattr(sys, "frozen", False):
            # 打包应用的 sys.executable 是应用 exe：拿它当解释器探测
            # 会把整个应用再启动一遍（实测幽灵 SUG 的来源）
            return RuntimeStatus(
                available=False,
                python_path="",
                message="尚未安装对齐运行环境，请点击「安装 / 修复」",
            )
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
                **hidden_subprocess_kwargs(),
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
            gpu_name=str(info.get("gpu", "")) or detect_nvidia_gpu(),
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

        if getattr(sys, "frozen", False):
            # frozen 宿主建不了 venv（应用 exe 没有 venv/ensurepip 机制）：
            # 走托管 runtime release 下载路线（内嵌 Python 底座，整机
            # 无需系统 Python），torch wheel 与 AI 增量随之安装。
            # runtime release 目前只有 Windows 资产：macOS 打包版给明确
            # 提示（源码运行的 macOS 走上面的 venv 路径，完全可用）
            if sys.platform != "win32":
                raise AiRuntimeError(
                    "macOS 打包版暂不支持自动安装运行环境；"
                    "源码运行的 macOS 可直接使用（安装/修复走 venv 路径）"
                )
            return self.install_from_release(
                target_dir,
                mirror=mirror,
                proxy=proxy,
                progress=progress,
                cancel=cancel,
            )

        # GPU 策略最先确定（探测在本机执行，与解释器无关）：检测到
        # NVIDIA 显卡时走 PyTorch 官方 CUDA 索引——PyPI 的 Windows
        # torch 默认是 CPU-only wheel，不显式指定索引 GPU 永远不会被
        # 用到；-U 让已装的 CPU 版原地升级到 +cu128 本地版本
        gpu_name = detect_nvidia_gpu()
        use_cuda = bool(gpu_name)
        if use_cuda:
            progress(2, f"检测到 NVIDIA GPU（{gpu_name}），安装 CUDA 版运行环境")
            import shutil as _shutil

            try:
                free_gb = (
                    _shutil.disk_usage(target_dir.anchor or target_dir.parent).free
                    / 1024
                    / 1024
                    / 1024
                )
                if free_gb < CUDA_RUNTIME_DISK_GB:
                    raise AiRuntimeError(
                        f"CUDA 版运行环境安装期间（含下载缓存）约需"
                        f"{CUDA_RUNTIME_DISK_GB}GB 磁盘，当前剩余 "
                        f"{free_gb:.1f}GB（完成后实际占用约 5GB）。"
                        "请清理磁盘后重试"
                    )
            except AiRuntimeError:
                raise
            except Exception:
                pass  # 空间查询失败不阻断，交给 pip 自身的磁盘错误
        else:
            progress(2, "未检测到 NVIDIA GPU，安装 CPU 版运行环境")

        progress(4, f"创建虚拟环境：{target_dir}")
        try:
            # 懒加载：嵌入式 Python 发行版（托管 PyMSS runtime）没有 venv
            # 模块——worker 子进程也会导入本模块，顶层 import 会让对齐
            # 直接崩掉（ModuleNotFoundError: No module named 'venv'）
            import venv
        except ImportError as exc:
            raise AiRuntimeError(
                "该解释器是嵌入式发行版，不支持创建虚拟环境；"
                "请使用共享运行环境安装（install_shared）"
            ) from exc
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
        elif use_cuda:
            args += ["--extra-index-url", TORCH_CUDA_INDEX_URL]
        elif mirror:
            args += ["-i", mirror]
        requirements = list(requirements or RUNTIME_REQUIREMENTS)
        if use_cuda:
            # 钉住 torch/torchaudio 到 CUDA 索引可用的版本（见常量注释），
            # 其余依赖不受影响
            cuda_pin = f"=={TORCH_CUDA_VERSION}+{TORCH_CUDA_TAG}"
            pinned = []
            for req in requirements:
                name = (
                    req.split("=")[0]
                    .split(">")[0]
                    .split("<")[0]
                    .split("[")[0]
                    .strip()
                )
                if name in ("torch", "torchaudio"):
                    pinned.append(f"{name}{cuda_pin}")
                else:
                    pinned.append(req)
            requirements = pinned
        if use_cuda:
            args += ["-U"]
        args += list(requirements)
        return self._pip_install_requirements(
            python_exe, args, progress=progress, cancel=cancel
        )

    def install_from_release(
        self,
        target_dir: Path,
        *,
        mirror: str = "",
        proxy: str = "",
        progress: Optional[PROGRESS_CB] = None,
        cancel: Optional[CANCEL_CB] = None,
        manifest_fetch=None,
    ) -> RuntimeStatus:
        """分支 B：从 karaoke-studio-runtime release 下载托管底座并安装。

        流程：选变体 → 拉清单 → 下载分卷底座 zip（sha256 校验）→ 解压
        校验出 ``runtime/python.exe``（内嵌 Python，整机无需系统 Python）
        → 下载 torch wheel 并用底座自带 pip 安装 → 复用 ``install_shared``
        装 AI 增量依赖。已有可用 runtime 时跳过下载直接增量。

        Args:
            manifest_fetch: 可注入的清单获取函数（测试用，默认
                ``fetch_runtime_release_manifest``）。
        """
        import zipfile

        progress = progress or (lambda p, m: None)
        cancel = cancel or (lambda: False)
        target_dir = Path(target_dir)
        runtime_dir = target_dir / "runtime"
        python_exe = runtime_dir / (
            "python.exe" if sys.platform == "win32" else "bin" / "python"
        )

        # 快路径：已有可用环境（修复重装）直接增量。判定用「torch 可导入」
        # 而非完整 probe——半装状态（torch 就位、AI 依赖缺失）也能免重下
        # 3GB wheel，直接补装依赖
        if python_exe.is_file():
            if detect_torch_build(str(python_exe)) is not None:
                progress(60, "检测到已安装的运行环境，跳过下载")

                def _scaled(p: int, m: str) -> None:
                    progress(60 + int(p * 0.39), m)

                status = self.install_shared(
                    str(python_exe),
                    mirror=mirror,
                    proxy=proxy,
                    progress=_scaled,
                    cancel=cancel,
                )
                progress(100, "运行环境就绪")
                return status

        variant = release_variant_for_host()
        gpu = detect_nvidia_gpu()
        if variant == RUNTIME_VARIANT_CUDA:
            progress(
                2, f"检测到 NVIDIA GPU（{gpu}），下载 CUDA 版托管运行环境"
            )
            # cu128 路线峰值预检：底座 + torch wheel 下载缓存 + 解压安装
            # 双份（完成后约 6GB，staging 自动清理）
            import shutil as _shutil

            try:
                free_gb = (
                    _shutil.disk_usage(
                        target_dir.anchor or target_dir.parent
                    ).free
                    / 1024
                    / 1024
                    / 1024
                )
                if free_gb < RUNTIME_RELEASE_CUDA_PEAK_GB:
                    raise AiRuntimeError(
                        f"CUDA 版运行环境安装期间（含底座与 torch wheel "
                        f"下载缓存）峰值约 {RUNTIME_RELEASE_CUDA_PEAK_GB}GB，"
                        f"当前剩余 {free_gb:.1f}GB（完成后实际占用约 6GB，"
                        "缓存会自动清理）。请清理磁盘后重试"
                    )
            except AiRuntimeError:
                raise
            except Exception:
                pass  # 空间查询失败不阻断，交给 pip 自身的磁盘错误
        else:
            progress(2, "未检测到可用 NVIDIA GPU/驱动，下载 CPU 版托管运行环境")

        fetch = manifest_fetch or fetch_runtime_release_manifest
        manifest = fetch(variant, proxy=proxy)
        if cancel():
            raise AiRuntimeError("已取消")
        parts = manifest["archive"]["parts"]
        torch_info = (manifest.get("torch") or {}).get("wheel") or {}
        total_bytes = sum(int(p.get("size") or 0) for p in parts) + int(
            torch_info.get("size") or 0
        )
        staging = target_dir / "staging"
        staging.mkdir(parents=True, exist_ok=True)

        # 分卷底座 + torch wheel 按字节摊进度（5-60）
        done_bytes = 0
        zip_parts: List[Path] = []
        for idx, part_info in enumerate(parts):
            part_dest = staging / f"runtime-{variant}.zip.{idx + 1:03d}"

            def _part_progress(p: int, m: str, _pi=part_info, _base=done_bytes):
                # 每个文件的子进度折算到全局字节刻度
                frac = _base / max(1, total_bytes) + (
                    (p / 100.0) * int(_pi.get("size") or 0) / max(1, total_bytes)
                )
                progress(5 + int(55 * min(1.0, frac)), m)

            _download_verified(
                str(part_info.get("url", "")),
                part_dest,
                expected_size=int(part_info.get("size") or 0),
                expected_sha256=str(part_info.get("sha256", "")),
                proxy=proxy,
                progress=_part_progress,
                cancel=cancel,
                label=f"底座分卷 {idx + 1}/{len(parts)} ",
            )
            zip_parts.append(part_dest)
            done_bytes += int(part_info.get("size") or 0)

        wheel_dest = None
        if torch_info:
            wheel_dest = staging / str(torch_info.get("filename") or "torch.whl")

            def _wheel_progress(p: int, m: str):
                frac = done_bytes / max(1, total_bytes) + (
                    (p / 100.0) * int(torch_info.get("size") or 0)
                    / max(1, total_bytes)
                )
                progress(5 + int(55 * min(1.0, frac)), m)

            _download_verified(
                str(torch_info.get("url", "")),
                wheel_dest,
                expected_size=int(torch_info.get("size") or 0),
                expected_sha256=str(torch_info.get("sha256", "")),
                proxy=proxy,
                progress=_wheel_progress,
                cancel=cancel,
                label="PyTorch wheel ",
            )

        # 拼接分卷 → 解压校验（60-75）
        import io as _io

        progress(60, "解压运行环境底座…")
        payload = staging / "payload"
        if payload.exists():
            import shutil as _shutil

            _shutil.rmtree(payload)
        payload.mkdir(parents=True)
        try:
            with _io.BytesIO() as joined:
                for part in zip_parts:
                    joined.write(part.read_bytes())
                joined.seek(0)
                with zipfile.ZipFile(joined) as zf:
                    zf.extractall(payload)
        except Exception as exc:
            raise AiRuntimeError(f"解压运行环境失败：{exc}") from exc

        files = manifest.get("files") or []
        for entry in files:
            rel = str(entry.get("path", ""))
            target = _safe_extract_path(payload, rel)
            if target is None or not target.is_file():
                raise AiRuntimeError(f"运行环境底座缺少文件：{rel}")
            if entry.get("size") and target.stat().st_size != int(entry["size"]):
                raise AiRuntimeError(f"运行环境底座文件大小不符：{rel}")
        # KS 契约（separation/runtime.py 的 _safe_member）：底座 zip 条目
        # 与 files[].path 均带 runtime/ 前缀——python 落在 payload/runtime/
        # 下，搬运也只取该子目录。此前按「无前缀」口径校验，打包版安装
        # 在解压后必报「缺少 python.exe」
        payload_runtime = payload / "runtime"
        payload_python = payload_runtime / python_exe.relative_to(runtime_dir)
        if not payload_python.is_file():
            raise AiRuntimeError(
                "运行环境底座缺少 python.exe（清单与实际不符）"
            )

        # 替换正式 runtime 目录（60-75 末）：只搬 runtime/ 子目录，
        # 避免 target/runtime/runtime 双重前缀
        _replace_dir_with_retry(payload_runtime, runtime_dir)

        # torch wheel 安装（75-85）：底座自带 pip，依赖走常规索引
        if wheel_dest is not None:
            progress(75, "安装 PyTorch（体积较大）…")
            pip_args = [
                str(python_exe),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                str(wheel_dest),
            ]
            if proxy:
                pip_args += ["--proxy", proxy]
            if mirror:
                pip_args += ["-i", mirror]
            returncode = self._pip_runner(
                str(python_exe),
                pip_args[1:],
                lambda line: progress(
                    80, line.strip()[:80] if line.strip() else "安装 PyTorch…"
                ),
                cancel,
            )
            if cancel():
                raise AiRuntimeError("已取消")
            if returncode != 0:
                raise AiRuntimeError(
                    f"PyTorch 安装失败（pip 返回码 {returncode}）"
                )

        # AI 增量依赖（85-99）：torch 已就位，动态配对 torchaudio
        def _scaled(p: int, m: str) -> None:
            progress(85 + int(p * 0.14), m)

        status = self.install_shared(
            str(python_exe),
            mirror=mirror,
            proxy=proxy,
            progress=_scaled,
            cancel=cancel,
        )
        # 成功后清理 staging（底座分卷 + torch wheel 共约 3.2GB）；
        # 失败路径保留，供重试时断点复用
        import shutil as _shutil

        _shutil.rmtree(staging, ignore_errors=True)
        progress(100, "运行环境就绪")
        return status

    def install_shared(
        self,
        python_exe: str,
        *,
        mirror: str = "",
        proxy: str = "",
        progress: Optional[PROGRESS_CB] = None,
        cancel: Optional[CANCEL_CB] = None,
    ) -> RuntimeStatus:
        """向宿主托管的 Runtime（工作台 PyMSS runtime）增量安装 AI 依赖。

        与 ``install``（自建 venv）不同：不创建环境、**不安装 torch**
        （托管 runtime 已带，按其变体只装配对的 torchaudio），直接向
        该解释器 pip 安装 AI 增量包。方案 B：embedded 与分离共享同一
        份 torch（含 CUDA），避免重复下载约 3GB。

        Raises:
            AiRuntimeError: 解释器不存在 / 托管环境缺 torch（提示先在
                工作台完成分离环境安装）/ pip 失败。
        """
        progress = progress or (lambda p, m: None)
        cancel = cancel or (lambda: False)
        exe = Path(python_exe)
        if not exe.is_file():
            raise AiRuntimeError(f"托管运行环境解释器不存在：{python_exe}")
        if cancel():
            raise AiRuntimeError("已取消")

        build = detect_torch_build(str(exe))
        if build is None:
            raise AiRuntimeError(
                "托管运行环境缺少 PyTorch：请先在工作台「音频分离」页"
                "完成环境安装，再使用 AI 打轴"
            )
        torch_version, torch_tag = build
        # torchaudio 必须与 torch 同版本同变体（混装不受支持）；PyPI 的
        # torchaudio 可能比官方索引新，按「版本+本地标签」精确钉住
        index_url = f"https://download.pytorch.org/whl/{torch_tag}"
        # 注：对齐转写（拼音表音/CMU 英文音节）发生在主进程构建请求时
        # （transcription.py，主程序自带 pyphen/nltk），worker 无需这些依赖
        requirements: List[str] = [
            f"torchaudio=={torch_version}+{torch_tag}",
            PINNED_TRANSFORMERS,
            "soundfile",
            "huggingface_hub",
            "audio-separator[cpu]",
            "librosa==0.10.2.post1",
        ]
        progress(
            2,
            f"复用工作台运行环境（PyTorch {torch_version}+{torch_tag}），"
            "仅安装 AI 增量依赖（不重复下载 torch，预计新增约 0.5GB）",
        )
        args: List[str] = ["-m", "pip", "install", "--disable-pip-version-check"]
        if proxy:
            args += ["--proxy", proxy]
        args += ["--extra-index-url", index_url]
        if mirror:
            args += ["-i", mirror]
        args += list(requirements)
        return self._pip_install_requirements(
            str(exe), args, progress=progress, cancel=cancel
        )

    def _pip_install_requirements(
        self,
        python_exe,
        args: List[str],
        *,
        progress: PROGRESS_CB,
        cancel: CANCEL_CB,
    ) -> RuntimeStatus:
        """pip 安装公共管线：dry-run 预估 → 流式安装 → 探测校验。"""
        import os
        import time as _time

        pip_env = dict(os.environ)
        if "--proxy" in args:
            pip_env["HTTP_PROXY"] = args[args.index("--proxy") + 1]
            pip_env["HTTPS_PROXY"] = args[args.index("--proxy") + 1]
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
                    progress(min(9, 5 + dry_lines // 3), f"解析依赖：{text}")

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
            **hidden_subprocess_kwargs(),
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
    "TORCH_CUDA_TAG",
    "TORCH_CUDA_INDEX_URL",
    "TORCH_CUDA_VERSION",
    "CUDA_RUNTIME_DISK_GB",
    "RUNTIME_RELEASE_REPO",
    "RUNTIME_RELEASE_TAG",
    "RUNTIME_VARIANT_CPU",
    "RUNTIME_VARIANT_CUDA",
    "AiRuntimeError",
    "RuntimeStatus",
    "AiRuntimeManager",
    "detect_nvidia_gpu",
    "detect_torch_build",
    "nvidia_driver_supports_cu128",
    "release_variant_for_host",
    "release_manifest_url",
    "fetch_runtime_release_manifest",
]
