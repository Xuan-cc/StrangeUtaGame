"""一键构建当前平台的所有发布变体（含更新器）。

Windows：构建 main + noWinIME 两个变体
macOS：  构建 mac 变体
Linux：  构建 main 变体（兜底）

每个变体的构建流程：
  1. python updater_app/build_updater.py   构建 Updater.exe（Windows 专用）
  2. python build.py --variant <variant>   构建主程序

产物位置：
  dist/StrangeUtaGame/          main
  dist/StrangeUtaGame-noWinIME/ noWinIME
  dist/StrangeUtaGame-mac/      mac（在 macOS 上构建）

用法：
  python build_all.py [--clean] [--variants main noWinIME]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.absolute()

# 各平台默认构建的变体列表
_PLATFORM_VARIANTS: dict[str, list[str]] = {
    "win32": ["main", "noWinIME"],
    "darwin": ["mac"],
    "linux": ["main"],
}


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


def run_step(cmd: list[str], step_name: str) -> None:
    print(f"\n{'=' * 60}")
    print(f">>> {step_name}")
    print(f"    {' '.join(str(c) for c in cmd)}")
    print("=" * 60)
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        print(f"\n✗ 步骤失败（退出码 {result.returncode}）: {step_name}")
        sys.exit(result.returncode)
    print(f"✓ 完成: {step_name}")


def build_variant(variant: str, clean: bool) -> None:
    """构建单个变体（含其更新器）。"""
    # 1. 构建 Updater.exe（仅 Windows）
    if sys.platform == "win32":
        updater_cmd = [sys.executable, "updater_app/build_updater.py"]
        if clean:
            updater_cmd.append("--clean")
        run_step(updater_cmd, f"构建 Updater.exe（供 {variant} 变体使用）")

    # 2. 构建主程序
    main_cmd = [sys.executable, "build.py", "--variant", variant]
    if clean:
        main_cmd.append("--clean")
    run_step(main_cmd, f"构建主程序 variant={variant}")


def main() -> int:
    ap = argparse.ArgumentParser(description="一键构建所有发布变体")
    ap.add_argument("--clean", action="store_true", help="传给 PyInstaller --clean，完整重建")
    ap.add_argument(
        "--variants",
        nargs="+",
        choices=["main", "noWinIME", "mac"],
        default=None,
        help="指定要构建的变体（默认按平台自动选择）",
    )
    cli = ap.parse_args()

    platform_key = sys.platform if sys.platform in _PLATFORM_VARIANTS else "linux"
    variants: list[str] = cli.variants or _PLATFORM_VARIANTS[platform_key]

    print(f"平台: {sys.platform}")
    print(f"将构建以下变体: {variants}")

    for variant in variants:
        print(f"\n{'#' * 60}")
        print(f"# 变体: {variant}")
        print(f"{'#' * 60}")
        build_variant(variant, cli.clean)

    print(f"\n{'=' * 60}")
    print(f"✓ 全部变体构建完成: {variants}")
    print("产物位置:")
    for variant in variants:
        suffix = f"-{variant}" if variant != "main" else ""
        print(f"  dist/StrangeUtaGame{suffix}/")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
