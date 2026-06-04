"""StrangeUtaGame 应用程序入口。

启动歌词打轴软件的主入口点。
"""

import sys
from pathlib import Path

# 添加 src 到路径
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# 设置 Windows 任务栏图标（AppUserModelID）必须在 QApplication 创建之前调用
if sys.platform == "win32":
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "xuancc.strangeutagame.app.1"
        )
    except Exception:
        pass

# 必须先创建 QApplication，再导入任何 QWidget
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon

# 启用 DPI 缩放
QApplication.setHighDpiScaleFactorRoundingPolicy(
    Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
)

# 创建应用实例
app = QApplication(sys.argv)

# 确定图标路径（后续多次使用）
_icon_path = (
    Path(__file__).parent / "src" / "strange_uta_game" / "resource" / "icon.ico"
)
if not _icon_path.exists():
    # PyInstaller 打包后的路径
    _base = getattr(sys, "_MEIPASS", Path(__file__).parent)
    _icon_path = Path(_base) / "strange_uta_game" / "resource" / "icon.ico"

# 初始化主题管理器（必须在创建主窗口之前）
from strange_uta_game.frontend.theme import theme
from strange_uta_game.frontend.settings.app_settings import AppSettings

# 从配置文件读取主题设置并应用
settings = AppSettings()
theme_value = settings.get("ui.theme", "auto")
from strange_uta_game.frontend.theme import ThemeMode
mode_map = {
    "light": ThemeMode.LIGHT,
    "dark": ThemeMode.DARK,
    "auto": ThemeMode.AUTO,
}
theme.mode = mode_map.get(theme_value, ThemeMode.AUTO)

# 在主题初始化完成后设置应用图标，避免 setTheme 内部重置图标
if _icon_path.exists():
    app.setWindowIcon(QIcon(str(_icon_path)))

# 清理上次会话残留的 LLM 请求日志（每次启动从干净状态开始）
try:
    from strange_uta_game.backend.infrastructure.parsers.llm_ruby import clear_llm_logs
    clear_llm_logs()
except Exception:
    pass

# 现在可以安全导入其他模块
from strange_uta_game.frontend.main_window import MainWindow


def _force_taskbar_icon(window, icon_path: Path) -> None:
    """在窗口显示后强制刷新 Windows 任务栏图标。

    Qt 的 setWindowIcon 在 python.exe 宿主进程下有时无法正确更新任务栏，
    需要直接通过 Win32 API 向 HWND 发送 WM_SETICON 并通知 Shell 刷新。
    """
    if sys.platform != "win32" or not icon_path.exists():
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32

        # 加载图标（大图标 32x32，小图标 16x16）
        LR_LOADFROMFILE = 0x0010
        IMAGE_ICON = 1
        hicon_big = user32.LoadImageW(
            None, str(icon_path), IMAGE_ICON, 32, 32, LR_LOADFROMFILE
        )
        hicon_small = user32.LoadImageW(
            None, str(icon_path), IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )

        hwnd = int(window.winId())
        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1
        if hicon_big:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)

    except Exception:
        pass


def main():
    """应用入口"""
    # Windows 日文 locale (cp932) 下 stdout 无法输出某些 Unicode 字符（如 U+29F8 ⧸、
    # U+301C 〜），强制切到 UTF-8 与其他入口 (build.py / updater_app) 保持一致。
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    # 从命令行参数中提取 .sug 文件路径（双击关联打开时传入）
    initial_project = None
    for arg in sys.argv[1:]:
        if arg.lower().endswith(".sug") and Path(arg).is_file():
            initial_project = str(Path(arg).resolve())
            break

    # 创建主窗口
    window = MainWindow()
    window.show()

    from PyQt6.QtCore import QTimer

    # 在事件循环启动后强制补设图标：
    # QTimer.singleShot(0, _preload) 会在第一个 tick 运行并可能重置图标，
    # 用 100ms 延迟确保在 _preload 之后再补设一次。
    QTimer.singleShot(100, lambda: _force_taskbar_icon(window, _icon_path))

    # 如果有命令行传入的项目文件，延迟加载（等事件循环启动后执行）
    if initial_project:
        QTimer.singleShot(200, lambda: window.open_initial_project(initial_project))

    # 运行应用
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
