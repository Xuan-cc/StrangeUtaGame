"""打包脚本 - 使用 PyInstaller 打包 StrangeUtaGame

注意事项：
1. sounddevice 和 soundfile 依赖 PortAudio / libsndfile，需要确保 DLL 被打包
2. PyQt6 有平台插件需要处理
3. sudachipy / sudachidict_core 需要 collect-data 才能正常加载词典
4. numpy 是音频引擎核心依赖，不可排除
5. 使用 --onedir 模式避免单文件解压问题
"""

import PyInstaller.__main__
import os
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()

# 检查依赖
print("检查依赖...")
try:
    import PyQt6
    import sounddevice
    import soundfile
    import pykakasi
    import qfluentwidgets
    import numpy
    import sudachipy
    import sudachidict_core
    import jaconv

    print("✓ 所有依赖已安装")
except ImportError as e:
    print(f"✗ 缺少依赖: {e}")
    print("请先运行: pip install -r requirements.txt")
    sys.exit(1)

# 构建 PyInstaller 参数
args = [
    "main.py",  # 主脚本
    "--name=StrangeUtaGame",  # 应用名称
    "--onedir",  # 使用目录模式（推荐，启动更快）
    "--windowed",  # Windows GUI 应用（无控制台窗口）
    "--clean",  # 清理临时文件
    "--noconfirm",  # 不确认覆盖
    # 数据文件
    "--add-data=src/strange_uta_game;strange_uta_game",  # 源代码
    "--add-data=src/strange_uta_game/resource/icon.ico;strange_uta_game/resource",  # 图标
    "--add-data=src/strange_uta_game/config/config.json;strange_uta_game/config",  # 默认配置
    "--add-data=src/strange_uta_game/config/dictionary.json;strange_uta_game/config",  # 默认字典
    "--add-data=src/strange_uta_game/config/singers.json;strange_uta_game/config",  # 默认演唱者
    "--add-data=src/strange_uta_game/config/e2k.txt;strange_uta_game/config",  # 英语注音词典 (CMU-based)
    "--add-data=src/strange_uta_game/config/cmudict-0.7b;strange_uta_game/config",  # CMU Pronouncing Dictionary (e2k 引擎数据源)
    # ── 隐藏导入（PyInstaller 可能检测不到的模块） ──
    # 音频
    "--hidden-import=sounddevice",
    "--hidden-import=soundfile",
    "--hidden-import=numpy",
    "--hidden-import=numpy.core",
    "--hidden-import=numpy.fft",
    # 日语处理
    "--hidden-import=pykakasi",
    "--hidden-import=pykakasi.kakasi",
    "--hidden-import=sudachipy",
    "--hidden-import=sudachidict_core",
    "--hidden-import=jaconv",
    # Qt / UI
    "--hidden-import=qfluentwidgets",
    "--hidden-import=PyQt6.sip",
    "--hidden-import=PyQt6.QtCore",
    "--hidden-import=PyQt6.QtGui",
    "--hidden-import=PyQt6.QtWidgets",
    # 标准库可能被跳过的模块
    "--hidden-import=encodings.idna",
    "--hidden-import=pkg_resources",
    # 排除不必要的模块（减小体积）
    "--exclude-module=matplotlib",
    "--exclude-module=scipy",
    "--exclude-module=pandas",
    "--exclude-module=tkinter",
    "--exclude-module=unittest",
    "--exclude-module=pdb",
    "--exclude-module=pydoc",
    "--exclude-module=test",
    # ── 收集所有二进制文件和数据 ──
    "--collect-all=sounddevice",
    "--collect-all=soundfile",
    "--collect-all=pykakasi",
    "--collect-all=qfluentwidgets",
    "--collect-data=sudachipy",
    "--collect-data=sudachidict_core",
    "--collect-binaries=soundfile",
    # 图标
    "--icon=src/strange_uta_game/resource/icon.ico",
]

# 平台特定配置
if sys.platform == "win32":
    # Windows 平台
    print("检测到 Windows 平台")

    # 尝试找到 PortAudio DLL 并添加
    try:
        import sounddevice as _sd

        sd_path = Path(_sd.__file__).parent
        portaudio_dll = (
            sd_path
            / "_sounddevice_data"
            / "portaudio-binaries"
            / "libportaudio64bit.dll"
        )
        if not portaudio_dll.exists():
            portaudio_dll = sd_path / "_sounddevice_data" / "portaudio.dll"
        if portaudio_dll.exists():
            args.append(f"--add-binary={portaudio_dll};.")
            print(f"✓ 找到 PortAudio DLL: {portaudio_dll}")
        else:
            print(
                "! 未找到独立的 portaudio.dll，将依赖 --collect-all=sounddevice 自动加载"
            )
    except Exception:
        pass

elif sys.platform == "darwin":
    # macOS
    print("检测到 macOS 平台")
    args.extend(
        [
            "--osx-bundle-identifier=com.xuancc.strangeutagame",
        ]
    )

else:
    # Linux
    print("检测到 Linux 平台")

print("\n开始打包...")
print(f"输出目录: {PROJECT_ROOT / 'dist'}")

# 运行 PyInstaller
PyInstaller.__main__.run(args)

print("\n✓ 打包完成!")
print(f"可执行文件位于: {PROJECT_ROOT / 'dist' / 'StrangeUtaGame'}")

# 打包后的说明
print("\n" + "=" * 60)
print("打包后注意事项：")
print("=" * 60)
print("1. 测试音频功能是否正常（播放/暂停/变速）")
print("2. 检查项目保存和打开功能")
print("3. 验证导出功能（LRC/KRA/ASS 等）")
print("4. 测试日语注音功能（依赖 sudachipy + pykakasi）")
print("5. 如缺少 DLL，请安装 Visual C++ Redistributable")
print("   https://aka.ms/vs/17/release/vc_redist.x64.exe")
print("=" * 60)

# ── 完整的命令行打包命令（供参考） ──
#
# pip install pyinstaller
#
# pyinstaller --noconfirm --onedir --windowed --clean \
#   --name "StrangeUtaGame" \
#   --icon=src/strange_uta_game/resource/icon.ico \
#   --add-data "src/strange_uta_game;strange_uta_game" \
#   --add-data "src/strange_uta_game/resource/icon.ico;strange_uta_game/resource" \
#   --add-data "src/strange_uta_game/config/config.json;strange_uta_game/config" \
#   --add-data "src/strange_uta_game/config/dictionary.json;strange_uta_game/config" \
#   --add-data "src/strange_uta_game/config/singers.json;strange_uta_game/config" \
#   --hidden-import=sounddevice \
#   --hidden-import=soundfile \
#   --hidden-import=numpy \
#   --hidden-import=numpy.core \
#   --hidden-import=numpy.fft \
#   --hidden-import=pykakasi \
#   --hidden-import=pykakasi.kakasi \
#   --hidden-import=sudachipy \
#   --hidden-import=sudachidict_core \
#   --hidden-import=jaconv \
#   --hidden-import=qfluentwidgets \
#   --hidden-import=PyQt6.sip \
#   --hidden-import=PyQt6.QtCore \
#   --hidden-import=PyQt6.QtGui \
#   --hidden-import=PyQt6.QtWidgets \
#   --hidden-import=encodings.idna \
#   --hidden-import=pkg_resources \
#   --exclude-module=matplotlib \
#   --exclude-module=scipy \
#   --exclude-module=pandas \
#   --exclude-module=tkinter \
#   --exclude-module=unittest \
#   --exclude-module=pdb \
#   --exclude-module=pydoc \
#   --exclude-module=test \
#   --collect-all=sounddevice \
#   --collect-all=soundfile \
#   --collect-all=pykakasi \
#   --collect-all=qfluentwidgets \
#   --collect-data=sudachipy \
#   --collect-data=sudachidict_core \
#   --collect-binaries=soundfile \
#   main.py
