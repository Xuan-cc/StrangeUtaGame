"""打包脚本 - 使用 PyInstaller 打包 StrangeUtaGame

注意事项：
1. sounddevice 和 soundfile 依赖 PortAudio，需要确保 DLL 被打包
2. PyQt6 有平台插件需要处理
3. 使用 --onedir 模式避免单文件解压问题
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
    # 隐藏导入（PyInstaller 可能检测不到的模块）
    "--hidden-import=sounddevice",
    "--hidden-import=soundfile",
    "--hidden-import=pykakasi",
    "--hidden-import=pykakasi.kakasi",
    "--hidden-import=qfluentwidgets",
    "--hidden-import=PyQt6.sip",
    "--hidden-import=PyQt6.QtCore",
    "--hidden-import=PyQt6.QtGui",
    "--hidden-import=PyQt6.QtWidgets",
    # 排除不必要的模块（减小体积）
    "--exclude-module=matplotlib",
    "--exclude-module=numpy.random",
    "--exclude-module=scipy",
    "--exclude-module=pandas",
    "--exclude-module=tkinter",
    "--exclude-module=unittest",
    "--exclude-module=pdb",
    "--exclude-module=pydoc",
    "--exclude-module=test",
    # 收集所有二进制文件（DLL 等）
    "--collect-all=sounddevice",
    "--collect-all=soundfile",
    "--collect-all=pykakasi",
    "--collect-all=qfluentwidgets",
    # 图标（如果有的话）
    # '--icon=icon.ico',
]

# 平台特定配置
if sys.platform == "win32":
    # Windows 平台
    print("检测到 Windows 平台")
    args.extend(
        [
            "--add-binary=portaudio.dll;.",
        ]
    )

    # 尝试找到 PortAudio DLL
    try:
        import sounddevice

        sd_path = Path(sounddevice.__file__).parent
        portaudio_dll = sd_path / "_sounddevice_data" / "portaudio.dll"
        if portaudio_dll.exists():
            print(f"✓ 找到 PortAudio DLL: {portaudio_dll}")
        else:
            print("! 未找到独立的 portaudio.dll，将依赖 sounddevice 自动加载")
    except:
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
print("4. 如缺少 DLL，请安装 Visual C++ Redistributable")
print("   https://aka.ms/vs/17/release/vc_redist.x64.exe")
print("=" * 60)
