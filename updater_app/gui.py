"""Updater GUI — qfluentwidgets 窗口模式（进度环 + 日志）。

使用 qfluentwidgets 主题系统，与主程序 UI 风格统一。
由 ``main.py`` 在 qfluentwidgets 可用时自动启用；不可用时回退控制台。
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import (
    QCoreApplication,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QThread,
    QTimer,
    QTranslator,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QCursor, QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QMessageBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    ProgressRing,
    PushButton,
    SubtitleLabel,
    Theme,
    isDarkTheme,
    setTheme,
    setThemeColor,
    themeColor,
)

ACCENT_COLOR = "#FF6B6B"


def _tr(s: str) -> str:
    return QCoreApplication.translate("UpdaterGUI", s)


# ───────────────────────── 日志桥接 ─────────────────────────


class _SignalBridge(QWidget):
    """不可见 widget，仅用来承载跨线程 signal。"""

    log_signal = pyqtSignal(str)


class _SignalLogHandler(logging.Handler):
    """将 logging 记录通过 Qt signal 转发到 GUI。"""

    def __init__(self, bridge: _SignalBridge):
        super().__init__()
        self._bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._bridge.log_signal.emit(self.format(record))
        except RuntimeError:
            pass


# ───────────────────────── Worker 线程 ─────────────────────────


class _UpdaterWorker(QThread):
    """后台线程执行 ``run()`` 更新逻辑。"""

    finished = pyqtSignal(int)

    def __init__(
        self,
        args: object,
        run_func: Callable,
        log_handler: logging.Handler,
    ):
        super().__init__()
        self._args = args
        self._run_func = run_func
        self._log_handler = log_handler

    def run(self) -> None:
        try:
            rc = self._run_func(
                self._args,
                extra_log_handler=self._log_handler,
                gui_mode=True,
            )
        except Exception:
            import traceback
            err_msg = traceback.format_exc()
            try:
                record = logging.LogRecord(
                    "updater", logging.ERROR, "", 0, err_msg, (), None
                )
                self._log_handler.emit(record)
            except Exception:
                pass
            rc = 99
        self.finished.emit(rc)


# ───────────────────────── 主窗口 ─────────────────────────


class _UpdaterWindow(QWidget):
    """Updater GUI 主窗口 — 圆角面板 + 进度环 + 日志区，跟随主题配色。"""

    _WIDTH = 520
    _HEIGHT = 420
    _RADIUS = 14

    def __init__(self, title: str, icon_path: Optional[str] = None):
        super().__init__()
        self._running = True

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._WIDTH, self._HEIGHT)
        if icon_path:
            self.setWindowIcon(QIcon(icon_path))

        self._bg_color = QColor(32, 32, 36, 240)
        self._border_color = QColor(255, 255, 255, 20)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 16)
        root.setSpacing(0)

        # 标题
        self._title = SubtitleLabel(title)
        self._title.setAlignment(Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self._title)
        root.addSpacing(18)

        # 中间：进度环 + 状态文本
        mid = QHBoxLayout()
        mid.setSpacing(16)

        self._ring = ProgressRing(self, useAni=False)
        self._ring.setFixedSize(48, 48)
        self._ring.setTextVisible(True)
        self._ring.setValue(0)
        mid.addWidget(self._ring)

        self._status = BodyLabel(_tr("正在初始化…"))
        self._status.setWordWrap(True)
        self._status.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        mid.addWidget(self._status, 1)
        root.addLayout(mid)
        root.addSpacing(10)

        # 日志区
        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOn
        )
        root.addWidget(self._log, 1)
        root.addSpacing(10)

        # 底部按钮
        bottom = QHBoxLayout()
        bottom.addStretch()
        self._btn = PushButton(_tr("取消更新"), self)
        self._btn.setFixedWidth(100)
        self._btn.clicked.connect(self.close)
        bottom.addWidget(self._btn)
        root.addLayout(bottom)

        self._apply_theme()
        self._center_on_screen()
        self._drag_pos = None

        # 确保窗口在前台（子进程搬迁后可能被父进程窗口遮挡）
        self.raise_()
        self.activateWindow()

    # ── 主题 ──

    def _apply_theme(self) -> None:
        dark = isDarkTheme()
        accent = themeColor()

        if dark:
            self._bg_color = QColor(32, 32, 36, 240)
            self._border_color = QColor(255, 255, 255, 20)
        else:
            self._bg_color = QColor(245, 245, 248, 240)
            self._border_color = QColor(0, 0, 0, 15)

        self._ring.setCustomBarColor(accent, accent)
        text_color = QColor("#e0e0e0") if dark else QColor("#1a1a1a")
        self._ring._drawText = lambda painter, text: (
            painter.setFont(self._ring.font()),
            painter.setPen(text_color),
            painter.drawText(
                self._ring.rect(), Qt.AlignmentFlag.AlignCenter, text
            ),
        )

        if dark:
            self._log.setStyleSheet(
                "QTextEdit {"
                "  background: rgba(0,0,0,0.3);"
                "  border: 1px solid rgba(255,255,255,0.06);"
                "  border-radius: 6px;"
                "  color: #cccccc;"
                "  font-size: 12px;"
                "  padding: 6px;"
                "}"
            )
        else:
            self._log.setStyleSheet(
                "QTextEdit {"
                "  background: rgba(0,0,0,0.04);"
                "  border: 1px solid rgba(0,0,0,0.08);"
                "  border-radius: 6px;"
                "  color: #333333;"
                "  font-size: 12px;"
                "  padding: 6px;"
                "}"
            )

        ac = accent.name()
        if dark:
            self._btn.setStyleSheet(
                "QPushButton {"
                "  color: #e0e0e0;"
                "  background: rgba(255,255,255,0.08);"
                f"  border: 1px solid {ac};"
                "  border-radius: 6px;"
                "  padding: 6px 16px;"
                "  font-size: 13px;"
                "}"
                "QPushButton:hover { background: rgba(255,255,255,0.15); }"
                "QPushButton:pressed { background: rgba(255,255,255,0.05); }"
                "QPushButton:disabled {"
                "  color: #aaaaaa;"
                "  border-color: rgba(128,128,128,0.3);"
                "}"
            )
        else:
            self._btn.setStyleSheet(
                "QPushButton {"
                "  color: #1a1a1a;"
                "  background: rgba(0,0,0,0.05);"
                f"  border: 1px solid {ac};"
                "  border-radius: 6px;"
                "  padding: 6px 16px;"
                "  font-size: 13px;"
                "}"
                "QPushButton:hover { background: rgba(0,0,0,0.09); }"
                "QPushButton:pressed { background: rgba(0,0,0,0.03); }"
                "QPushButton:disabled {"
                "  color: #555555;"
                "  border-color: rgba(128,128,128,0.3);"
                "}"
            )

        self.update()

    # ── 槽 ──

    def append_log(self, text: str) -> None:
        self._log.append(text)
        sb = self._log.verticalScrollBar()
        if sb:
            sb.setValue(sb.maximum())
        self._parse_progress(text)

    def _parse_progress(self, text: str) -> None:
        m = re.search(r"(\d+)%", text)
        if m:
            self._ring.setValue(int(m.group(1)))
            return

        status_map = {
            "下载": _tr("正在下载…"),
            "校验": _tr("正在校验…"),
            "解压": _tr("正在解压…"),
            "备份": _tr("正在备份…"),
            "写入": _tr("正在写入文件…"),
            "增量更新": _tr("正在执行增量更新…"),
            "等待": _tr("等待主程序退出…"),
            "启动": _tr("正在启动主程序…"),
            "更新完成": _tr("更新完成"),
        }
        for kw, label in status_map.items():
            if kw in text:
                self._status.setText(label)
                break

    def on_finished(self, code: int) -> None:
        self._running = False
        self._btn.setText(_tr("关闭"))
        if code == 0:
            # 搬迁完成（code=0）——子进程接管，此窗口静默关闭
            self.close()
        else:
            self._status.setText(
                _tr("更新失败（退出码 {code}），请查看日志").format(code=code)
            )
            self._ring.setValue(0)

    # ── 绘制 ──

    def paintEvent(self, event) -> None:
        W, H, R = self._WIDTH, self._HEIGHT, self._RADIUS
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, W, H), R, R)
        p.setClipPath(clip)
        p.fillRect(0, 0, W, H, self._bg_color)
        p.setPen(self._border_color)
        p.drawRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), R, R)
        p.end()

    # ── 窗口拖拽 ──

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None

    # ── 关闭 ──

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None and event.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:
        if self._running:
            reply = QMessageBox.question(
                self,
                _tr("确认"),
                _tr("更新正在进行中，确定要关闭吗？\n强制关闭可能导致程序损坏。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        event.accept()

    def finish_fade(self) -> None:
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(250)
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self.close)
        self._fade.start()

    def _center_on_screen(self) -> None:
        # 居中到光标所在屏（多显示器下不强制跳回主屏）
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            self.move(
                geo.x() + (geo.width() - self.width()) // 2,
                geo.y() + (geo.height() - self.height()) // 2,
            )


# ───────────────────────── 翻译加载 ─────────────────────────


def _load_translator(app: QApplication, app_dir: str, locale: str) -> Optional[QTranslator]:
    """尝试从 app_dir/_internal/.../translations/ 加载 .qm 翻译文件。"""
    if not locale or locale in ("zh_CN", "auto"):
        return None

    qm_candidates = [
        Path(app_dir) / "_internal" / "strange_uta_game" / "frontend"
        / "localization" / "translations" / f"app.{locale}.qm",
    ]
    for qm in qm_candidates:
        if qm.exists():
            translator = QTranslator(app)
            if translator.load(str(qm)):
                app.installTranslator(translator)
                return translator
    return None


# ───────────────────────── 入口 ─────────────────────────


def _detect_dark_mode() -> bool:
    try:
        import winreg

        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return val == 0
    except Exception:
        return True


def _find_icon(app_dir: str) -> Optional[str]:
    candidates = [
        Path(app_dir) / "_internal" / "strange_uta_game" / "resource" / "icon.ico",
        Path(app_dir) / "icon.ico",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _hide_console() -> None:
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def run_gui(args: object, run_func: Callable) -> int:
    """创建 QApplication + 窗口 + worker，阻塞直到完成。"""
    _hide_console()

    # 与主程序保持一致的高 DPI 缩放策略（分数缩放 125%/150% 不被抹平）
    from PyQt6.QtCore import Qt
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv[:1])

    # qfluentwidgets 主题
    dark = _detect_dark_mode()
    setThemeColor(ACCENT_COLOR, lazy=True)
    setTheme(Theme.DARK if dark else Theme.LIGHT, lazy=True)

    # 加载翻译
    app_dir = str(getattr(args, "app_dir", ""))
    locale = getattr(args, "locale", "")
    _translator = _load_translator(app, app_dir, locale)  # noqa: F841 prevent GC

    version = getattr(args, "target_version", "")
    title = _tr("正在更新到 v{version}").format(version=version) if version else _tr("正在更新…")

    icon_path = _find_icon(app_dir)

    win = _UpdaterWindow(title, icon_path)
    win.show()

    LOG_FORMAT = "[%(asctime)s] %(levelname)s %(message)s"
    DATE_FORMAT = "%H:%M:%S"

    bridge = _SignalBridge(win)
    handler = _SignalLogHandler(bridge)
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))

    bridge.log_signal.connect(win.append_log, Qt.ConnectionType.QueuedConnection)

    worker = _UpdaterWorker(args, run_func, handler)
    worker.finished.connect(win.on_finished, Qt.ConnectionType.QueuedConnection)
    worker.start()

    app.exec()

    if worker.isRunning():
        worker.wait(5000)

    return 0
