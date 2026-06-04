"""打包 ``Updater.exe`` —— 独立的小型可执行文件。

输出位置：``<repo>/updater_app/dist/Updater/Updater.exe`` 与
``<repo>/updater_app/dist/Updater.exe`` 等 PyInstaller 默认产物。
在 ``build.py`` 中会进一步把 ``Updater.exe`` 复制到主程序 ``dist/StrangeUtaGame/``。

使用：

.. code:: bat

    python updater_app\\build_updater.py

设计权衡：

* ``--onefile`` —— Updater 是一次性流程，体积比启动速度更重要；onefile 让最终
  发布产物里只多出一个 ``Updater.exe``，不需要额外子目录。
* ``--console`` —— 用户能在 cmd 窗口看到进度（与 March7thAssistant 一致）。
* 不引入 PyQt6 等重依赖；只走标准库 + ``requests``，约 12~16 MB。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _force_utf8_stdio() -> None:
    """强制 stdout/stderr 用 UTF-8。

    Windows 终端默认编码可能是 cp1252（GitHub Actions runner）或 cp936（中文系统），
    在那些环境下 ``print("中文")`` 会触发 ``UnicodeEncodeError``。Python 3.7+ 的
    ``TextIOWrapper.reconfigure`` 可以无侵入地改写已存在的 stdout/stderr 流。
    """
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_force_utf8_stdio()

PROJECT_ROOT = Path(__file__).resolve().parent
REPO_ROOT = PROJECT_ROOT.parent


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 Updater.exe")
    ap.add_argument(
        "--clean",
        action="store_true",
        default=False,
        help="传给 PyInstaller --clean，完整重建（改了 import 或打包配置时使用）",
    )
    cli = ap.parse_args()

    try:
        import PyInstaller.__main__  # noqa: F401
    except ImportError:
        print("缺少 pyinstaller。请先 `pip install pyinstaller`。", file=sys.stderr)
        return 1

    args = [
        str(PROJECT_ROOT / "main.py"),
        "--name=Updater",
        "--onefile",
        "--console",          # Updater 走控制台 UI，让用户能看到进度
        "--noconfirm",
        "--distpath", str(PROJECT_ROOT / "dist"),
        "--workpath", str(PROJECT_ROOT / "build"),
        "--specpath", str(PROJECT_ROOT),
        # 仅依赖标准库 + requests
        "--hidden-import=requests",
        "--hidden-import=urllib3",
        "--hidden-import=charset_normalizer",
        "--hidden-import=idna",
        "--hidden-import=certifi",
        # ── 易被 PyInstaller `--exclude-module` 副作用漏掉的标准库 ───────────
        # `colorsys` 等是非常小的纯 Python 模块，但 PyInstaller 在分析
        # excluded-package 的子依赖时偶尔会过激；为安全起见全部显式声明。
        "--hidden-import=colorsys",
        "--hidden-import=encodings",
        "--hidden-import=encodings.idna",
        "--hidden-import=encodings.utf_8",
        "--hidden-import=encodings.utf_8_sig",
        "--hidden-import=encodings.cp1252",
        "--hidden-import=encodings.cp437",
        "--hidden-import=encodings.cp65001",
        "--hidden-import=encodings.gbk",
        "--hidden-import=encodings.mbcs",
        "--hidden-import=hashlib",
        "--hidden-import=zipfile",
        "--hidden-import=tempfile",
        "--hidden-import=ssl",
        "--hidden-import=_ssl",
        # ── 排除主程序的重型依赖，缩小体积 ──
        # 注意：不要 exclude PyQt6 子模块以外的间接小标准库（colorsys 等）
        "--exclude-module=PyQt6",
        "--exclude-module=qfluentwidgets",
        "--exclude-module=numpy",
        "--exclude-module=sounddevice",
        "--exclude-module=soundfile",
        "--exclude-module=pedalboard",
        "--exclude-module=av",
        "--exclude-module=pykakasi",
        "--exclude-module=sudachipy",
        "--exclude-module=sudachidict_core",
        "--exclude-module=jaconv",
        "--exclude-module=matplotlib",
        "--exclude-module=scipy",
        "--exclude-module=tkinter",
    ]

    # 图标（沿用主程序图标，找不到就跳过）
    icon = REPO_ROOT / "src" / "strange_uta_game" / "resource" / "icon.ico"
    if icon.exists():
        args.append(f"--icon={icon}")

    if cli.clean:
        args.append("--clean")

    import PyInstaller.__main__ as pi_main
    print("开始打包 Updater.exe ...")
    pi_main.run(args)
    print()
    exe = PROJECT_ROOT / "dist" / "Updater.exe"
    if exe.exists():
        print(f"✓ 打包完成: {exe}")
        print(f"  体积: {exe.stat().st_size / 1024 / 1024:.1f} MB")
    else:
        print("! 未在 dist/Updater.exe 找到产物，请检查 PyInstaller 输出。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
