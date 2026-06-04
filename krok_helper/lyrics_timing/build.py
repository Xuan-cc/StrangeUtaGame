"""打包脚本 - 使用 PyInstaller 打包 StrangeUtaGame

变体（--variant）：
  main      Windows + WinRT 日语注音（默认，已分发版本）
  noWinIME  Windows，无 WinRT，内置 sudachi-mini 注音
  mac       macOS，无 WinRT，内置 sudachi-mini 注音

注意事项：
1. sounddevice 和 soundfile 依赖 PortAudio / libsndfile，需要确保 DLL 被打包
2. PyQt6 有平台插件需要处理
3. main 变体：日语注音用 WinRT IME；noWinIME/mac 变体：使用 sudachi-mini
4. numpy 是音频引擎核心依赖，不可排除
5. 使用 --onedir 模式避免单文件解压问题
"""

import argparse
import contextlib
import re
import sys
from pathlib import Path

import PyInstaller.__main__


def _force_utf8_stdio() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

# 命令行参数
parser = argparse.ArgumentParser(description="PyInstaller 打包脚本")
parser.add_argument("--clean", action="store_true", help="传给 PyInstaller --clean，完整重建")
parser.add_argument(
    "--variant",
    choices=["main", "noWinIME", "mac"],
    default="main",
    help="构建变体：main（默认）/ noWinIME / mac",
)
_cli_args = parser.parse_args()

VARIANT = _cli_args.variant

PROJECT_ROOT = Path(__file__).parent.absolute()
VERSION_FILE = PROJECT_ROOT / "src" / "strange_uta_game" / "__version__.py"

# ── 防呆：让本地 src/ 屏蔽任何旧的 editable install ──────────────────────────
# 历史上 strange_uta_game 曾以独立仓库 E:\KaraMaker\StrangeUtaGame\ 形式存在并
# 被 `pip install -e .`，迁移到 krok_helper/lyrics_timing 之后那份 editable
# install 仍会留在 Python 环境的 sys.path 里。PyInstaller 做 import 分析时
# 命中的就是旧路径，最终 PYZ 里打进去的是旧 bytecode；--add-data 复制进
# dist/_internal/strange_uta_game/ 的新源码只是陪跑，运行时不会被加载，
# 表现就是"源码改了、dist 里也有新文件，可装好的 EXE 行为却照旧"。
#
# 把本地 src 放到 sys.path 最前并打印实际命中路径，让每次打包都能在控制台
# 立刻看出"这次打的是哪份 strange_uta_game"。
_SRC_DIR = str(PROJECT_ROOT / "src")
while _SRC_DIR in sys.path:
    sys.path.remove(_SRC_DIR)
sys.path.insert(0, _SRC_DIR)
try:
    import strange_uta_game as _sug_probe

    _sug_path = Path(_sug_probe.__file__).resolve()
    _expected = (PROJECT_ROOT / "src" / "strange_uta_game" / "__init__.py").resolve()
    if _sug_path != _expected:
        raise SystemExit(
            "✗ 打包前自检失败：import strange_uta_game 命中的不是本仓库源码。\n"
            f"  期望: {_expected}\n"
            f"  实际: {_sug_path}\n"
            "  常见原因：环境里有旧位置的 `pip install -e .`（例如\n"
            "  E:\\KaraMaker\\StrangeUtaGame\\）。先 `pip uninstall strange-uta-game`\n"
            "  或确认 sys.path 头部为当前 src/ 后重试。"
        )
    print(f"✓ 打包将使用: {_sug_path}")
    del _sug_probe
except ImportError:
    # 还没装/没找到都没关系，sys.path 已经放进去了，PyInstaller 自己也能找到
    print(f"  (尚未 import strange_uta_game，将走 sys.path 首项: {_SRC_DIR})")

# ── 变体配置 ──────────────────────────────────────────────────────────────────

# 每个变体的 PyInstaller 额外参数（在公共参数基础上叠加）
_VARIANT_CONFIGS = {
    "main": {
        # WinRT 相关
        "hidden_imports": [
            "--hidden-import=winrt.windows.globalization",
            "--hidden-import=winrt.windows.foundation",
            "--hidden-import=winrt.windows.foundation.collections",
        ],
        "collect_all": ["--collect-all=winrt"],
        # 主版本不含 sudachi：即便构建机器上恰好装了 sudachipy，也不打进包里
        "exclude_modules": [
            "--exclude-module=sudachipy",
            "--exclude-module=sudachidict_small",
            "--exclude-module=sudachidict_core",
            "--exclude-module=sudachidict_full",
        ],
        # 额外检查的依赖（import 名）
        "required_deps": ["winrt.windows.globalization"],
    },
    "noWinIME": {
        "hidden_imports": [
            "--hidden-import=sudachipy",
            "--hidden-import=sudachidict_small",
        ],
        "collect_all": [
            "--collect-all=sudachipy",
            "--collect-data=sudachidict_small",
        ],
        "exclude_modules": [
            "--exclude-module=winrt",
            "--exclude-module=winrt.windows.globalization",
            "--exclude-module=winrt.windows.foundation",
            # 只使用 sudachidict_small，排除大字典以减小包体积
            "--exclude-module=sudachidict_core",
            "--exclude-module=sudachidict_full",
        ],
        # 自定义 hook 覆盖系统 hook-sudachipy.py，阻止自动收集 core/full 字典
        "hooks_dir": str(PROJECT_ROOT / "pyinstaller_hooks"),
        "required_deps": [],
    },
    "mac": {
        "hidden_imports": [
            "--hidden-import=sudachipy",
            "--hidden-import=sudachidict_small",
        ],
        "collect_all": [
            "--collect-all=sudachipy",
            "--collect-data=sudachidict_small",
        ],
        "exclude_modules": [
            "--exclude-module=winrt",
            "--exclude-module=winrt.windows.globalization",
            "--exclude-module=winrt.windows.foundation",
            # 只使用 sudachidict_small，排除大字典以减小包体积
            "--exclude-module=sudachidict_core",
            "--exclude-module=sudachidict_full",
        ],
        # 自定义 hook 覆盖系统 hook-sudachipy.py，阻止自动收集 core/full 字典
        "hooks_dir": str(PROJECT_ROOT / "pyinstaller_hooks"),
        "required_deps": [],
    },
}

_cfg = _VARIANT_CONFIGS[VARIANT]

# ── 输出名称 ──────────────────────────────────────────────────────────────────

APP_NAME = "StrangeUtaGame" if VARIANT == "main" else f"StrangeUtaGame-{VARIANT}"

# ── 依赖检查 ──────────────────────────────────────────────────────────────────

print(f"构建变体: {VARIANT}  →  {APP_NAME}")
print("检查依赖...")

_common_deps = [
    ("PyQt6", "PyQt6"),
    ("sounddevice", "sounddevice"),
    ("soundfile", "soundfile"),
    ("pedalboard", "pedalboard"),
    ("pykakasi", "pykakasi"),
    ("qfluentwidgets", "qfluentwidgets"),
    ("numpy", "numpy"),
    ("jaconv", "jaconv"),
]

_failed = False
for _label, _mod in _common_deps:
    try:
        __import__(_mod)
    except ImportError:
        print(f"✗ 缺少依赖: {_label}")
        _failed = True

for _mod in _cfg["required_deps"]:
    try:
        __import__(_mod)
    except ImportError:
        print(f"✗ 缺少变体依赖: {_mod}")
        _failed = True

if VARIANT in ("noWinIME", "mac"):
    for _sudachi_pkg in ("sudachipy", "sudachidict_small"):
        try:
            __import__(_sudachi_pkg)
        except ImportError:
            print(f"✗ 缺少依赖: {_sudachi_pkg}（请 pip install sudachipy sudachidict_small）")
            _failed = True

if _failed:
    print("请先安装缺少的依赖后重试。")
    sys.exit(1)

import PyQt6
import sounddevice
import soundfile
import pedalboard
import numpy

print("✓ 所有依赖已安装")
print(f"  PyQt6: {PyQt6.QtCore.PYQT_VERSION_STR}")
print(f"  sounddevice: {sounddevice.__version__}")
print(f"  soundfile: {soundfile.__version__}")
print(f"  pedalboard: {pedalboard.__version__}")
print(f"  numpy: {numpy.__version__}")

# ── VARIANT patch 上下文管理器 ────────────────────────────────────────────────


@contextlib.contextmanager
def _patch_version_variant(variant: str):
    """临时将 __version__.py 中的 VARIANT 值改为 variant，构建后还原。"""
    original = VERSION_FILE.read_text(encoding="utf-8")
    patched = re.sub(
        r'^(VARIANT\s*=\s*)"[^"]*"',
        rf'\1"{variant}"',
        original,
        flags=re.MULTILINE,
    )
    if patched == original and variant != "":
        print(f"! 警告：未能在 __version__.py 中找到 VARIANT 行，将原样构建")
    try:
        VERSION_FILE.write_text(patched, encoding="utf-8")
        yield
    finally:
        VERSION_FILE.write_text(original, encoding="utf-8")


# ── PyInstaller 参数（公共部分） ──────────────────────────────────────────────

_src_sep = ";" if sys.platform == "win32" else ":"

args = [
    "main.py",
    f"--name={APP_NAME}",
    "--onedir",
    "--windowed",
    "--noconfirm",
    # 数据文件
    f"--add-data=src/strange_uta_game{_src_sep}strange_uta_game",
    f"--add-data=src/strange_uta_game/resource/icon.ico{_src_sep}strange_uta_game/resource",
    f"--add-data=src/strange_uta_game/config/config.json{_src_sep}strange_uta_game/config",
    f"--add-data=src/strange_uta_game/config/dictionary.json{_src_sep}strange_uta_game/config",
    f"--add-data=src/strange_uta_game/config/singers.json{_src_sep}strange_uta_game/config",
    f"--add-data=src/strange_uta_game/config/e2k.txt{_src_sep}strange_uta_game/config",
    f"--add-data=src/strange_uta_game/config/cmudict-0.7b{_src_sep}strange_uta_game/config",
    # 公共隐藏导入
    "--hidden-import=sounddevice",
    "--hidden-import=soundfile",
    "--hidden-import=pedalboard",
    "--hidden-import=pedalboard.io",
    "--hidden-import=pedalboard.io.AudioFile",
    "--hidden-import=pedalboard.io.StreamResampler",
    "--hidden-import=pedalboard.time_stretch",
    "--hidden-import=numpy",
    "--hidden-import=numpy.core",
    "--hidden-import=numpy.fft",
    "--hidden-import=numpy.lib",
    "--hidden-import=pykakasi",
    "--hidden-import=pykakasi.kakasi",
    "--hidden-import=jaconv",
    "--hidden-import=qfluentwidgets",
    "--hidden-import=PyQt6.sip",
    "--hidden-import=PyQt6.QtCore",
    "--hidden-import=PyQt6.QtGui",
    "--hidden-import=PyQt6.QtWidgets",
    "--hidden-import=encodings.idna",
    "--hidden-import=pkg_resources",
    "--hidden-import=colorsys",
    "--collect-submodules=strange_uta_game.updater",
    "--collect-all=sounddevice",
    "--collect-all=soundfile",
    "--collect-all=pedalboard",
    "--collect-all=pykakasi",
    "--collect-all=qfluentwidgets",
    "--collect-binaries=soundfile",
    "--icon=src/strange_uta_game/resource/icon.ico",
]

# 追加变体专属参数
args.extend(_cfg["hidden_imports"])
args.extend(_cfg["collect_all"])
args.extend(_cfg["exclude_modules"])
if _cfg.get("hooks_dir"):
    args.append(f"--additional-hooks-dir={_cfg['hooks_dir']}")

if _cli_args.clean:
    args.append("--clean")
    print("启用 PyInstaller --clean（完整重建）")

# ── 平台特定配置 ──────────────────────────────────────────────────────────────

if sys.platform == "win32":
    print("检测到 Windows 平台")
    try:
        import sounddevice as _sd
        sd_path = Path(_sd.__file__).parent
        portaudio_dll = (
            sd_path / "_sounddevice_data" / "portaudio-binaries" / "libportaudio64bit.dll"
        )
        if not portaudio_dll.exists():
            portaudio_dll = sd_path / "_sounddevice_data" / "portaudio.dll"
        if portaudio_dll.exists():
            args.append(f"--add-binary={portaudio_dll};.")
            print(f"✓ 找到 PortAudio DLL: {portaudio_dll}")
        else:
            print("! 未找到独立的 portaudio.dll，依赖 --collect-all=sounddevice 自动加载")
    except Exception:
        pass

elif sys.platform == "darwin":
    print("检测到 macOS 平台")
    args.extend(["--osx-bundle-identifier=com.xuancc.strangeutagame"])

else:
    print("检测到 Linux 平台")

# ── 构建 ──────────────────────────────────────────────────────────────────────

print(f"\n开始打包 {APP_NAME}...")
print(f"输出目录: {PROJECT_ROOT / 'dist' / APP_NAME}")

with _patch_version_variant("" if VARIANT == "main" else VARIANT):
    PyInstaller.__main__.run(args)

print(f"\n✓ 打包完成: {APP_NAME}")

# ── ARM64 Windows PortAudio DLL 修复 ─────────────────────────────────────────

if sys.platform == "win32":
    _internal = PROJECT_ROOT / "dist" / APP_NAME / "_internal"
    _pa_64bit = _internal / "libportaudio64bit.dll"
    _pa_arm64 = _internal / "libportaudioarm64.dll"
    if _pa_64bit.exists() and not _pa_arm64.exists():
        try:
            import shutil as _shutil
            _shutil.copy2(str(_pa_64bit), str(_pa_arm64))
            print(f"✓ 已生成 ARM64 PortAudio DLL: {_pa_arm64}")
        except Exception as _e:
            print(f"! 生成 ARM64 PortAudio DLL 失败: {_e}")

# ── 复制 Updater.exe（仅 Windows） ───────────────────────────────────────────

if sys.platform == "win32":
    _updater_src = PROJECT_ROOT / "updater_app" / "dist" / "Updater.exe"
    _updater_dst_dir = PROJECT_ROOT / "dist" / APP_NAME
    _updater_dst = _updater_dst_dir / "Updater.exe"
    if _updater_dst_dir.exists():
        if _updater_src.exists():
            try:
                import shutil as _shutil
                _shutil.copy2(str(_updater_src), str(_updater_dst))
                print(f"✓ 已复制 Updater.exe → {_updater_dst}")
            except Exception as _e:
                print(f"✗ 复制 Updater.exe 失败: {_e}")
        else:
            print(
                "✗ 未找到 updater_app/dist/Updater.exe。\n"
                "  自动更新功能不可用。请先运行:\n"
                "    python updater_app/build_updater.py\n"
                "  再重新打包主程序。"
            )
    else:
        print("! dist/{APP_NAME}/ 目录不存在，跳过 Updater.exe 复制")

# ── 验证 updater 子包 ─────────────────────────────────────────────────────────

_updater_pkg = (
    PROJECT_ROOT / "dist" / APP_NAME / "_internal"
    / "strange_uta_game" / "updater"
)
if _updater_pkg.is_dir():
    _n = len(list(_updater_pkg.iterdir()))
    print(f"✓ strange_uta_game.updater 子包已收集（{_n} 文件）")
else:
    print(
        "✗ strange_uta_game.updater 子包未被打包!\n"
        "  请确认 build.py 中包含 --collect-submodules=strange_uta_game.updater."
    )

# ── 打包后说明 ────────────────────────────────────────────────────────────────

print("\n" + "=" * 60)
print(f"打包后注意事项（{APP_NAME}）：")
print("=" * 60)
print("1. 测试音频功能是否正常（播放/暂停/变速）")
print("2. 检查项目保存和打开功能")
print("3. 验证导出功能（LRC/KRA/ASS 等）")
if VARIANT == "main":
    print("4. 测试日语注音（WinRT IME；缺日语功能时应弹出安装引导）")
else:
    print("4. 测试日语注音（sudachi-mini；注音应直接可用，无需安装额外组件）")
if sys.platform == "win32":
    print("5. 如缺少 DLL，请安装 Visual C++ Redistributable")
    print("   https://aka.ms/vs/17/release/vc_redist.x64.exe")
print("=" * 60)
