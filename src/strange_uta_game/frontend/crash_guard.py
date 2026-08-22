"""全局异常兜底：未处理的 Python 异常不再直接闪退。

PyQt6 对从槽函数 / 虚函数重写中逃逸的未处理异常，默认行为是打印 traceback
后调用 ``qFatal`` 终止进程——打包成无控制台的窗口程序后，用户看到的就是
闪退且拿不到任何报错信息。本模块安装两级钩子替代该行为：

- ``sys.excepthook``：主线程 + Qt 回调（槽 / 虚函数重写）中逃逸的异常；
- ``threading.excepthook``：``threading.Thread`` 目标函数中逃逸的异常
  （PyQt6 的 ``QThread.run`` 重写逃逸的异常同样路由到 ``sys.excepthook``）。

安装点在 ``MainWindow.__init__`` 入口（不在 main.py——嵌入宿主不经过
main.py）：standalone 与嵌入宿主（krok-helper 等）共用同一份保护，宿主
进程中逃逸的异常同样被兜住。宿主自装的 excepthook 不会被剥夺：我们处理
完后原样链式转交。

行为：traceback 追加写入 ``logs/crash.log``（超限轮转），并在主线程弹 Fluent
对话框告知用户，用户确认后程序继续运行而不是被 abort。

健壮性设计（钩子自身绝不再抛）：

- 处理中置位 ``_handling``：弹窗/落盘过程中的二次异常只尽力落盘，不再弹窗；
- 同一异常签名 10 秒内只弹一次窗（防止 paint 风暴刷屏），日志照常记录；
- worker 线程的异常经 ``QMetaObject.invokeMethod``（队列连接）转回主线程弹窗，
  不在工作线程碰任何控件；
- Fluent 对话框失败时回退原生 ``QMessageBox``，再失败则仅落盘。

边界（文档化而非掩盖）：纯 C++ 层崩溃（访问违例、Qt 内部 use-after-free）
不是 Python 异常，无法被本机制捕获——这类问题必须从源头修复，例如弹层
销毁竞争应把重活延迟到下一轮事件循环（见 ai_timing_dialog 的下拉模型修复）。
"""

from __future__ import annotations

import queue
import sys
import threading
import time
import traceback
import weakref
from datetime import datetime
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QMetaObject, QObject, Qt, QThread, pyqtSlot

__all__ = ["CrashGuard", "install_crash_guard"]

_LOG_FILENAME = "crash.log"
_MAX_LOG_BYTES = 1_000_000  # 超过 ~1MB 轮转为 crash.log.1
_DIALOG_COOLDOWN_S = 10.0


class _CrashReporter(QObject):
    """主线程驻留的弹窗转发器：任意线程 submit，主线程事件循环里统一弹窗。"""

    def __init__(self) -> None:
        super().__init__()
        self._pending: "queue.SimpleQueue[tuple[str, str]]" = queue.SimpleQueue()

    def submit(self, title: str, content: str) -> None:
        """任意线程可调：入队并请求主线程处理（队列连接，线程安全）。"""
        self._pending.put((title, content))
        try:
            QMetaObject.invokeMethod(
                self, "_drain", Qt.ConnectionType.QueuedConnection
            )
        except Exception:
            # 主循环已死 / Qt 收尾期：入队内容无人消费，但不允许向上抛
            pass

    @pyqtSlot()
    def _drain(self) -> None:
        # 一次 drained 批量合并成一个对话框：异常风暴只打扰用户一次
        reports: list[tuple[str, str]] = []
        while True:
            try:
                reports.append(self._pending.get_nowait())
            except queue.Empty:
                break
        if not reports:
            return
        if len(reports) == 1:
            title, content = reports[0]
        else:
            title, content = reports[-1]
            content = f"（本次连续捕获 {len(reports)} 个未处理错误，以下为最近一个）\n\n{content}"
        _show_error_dialog(title, content)


def _show_error_dialog(title: str, content: str) -> None:
    """尽力弹窗：Fluent 优先，失败回退原生 QMessageBox，再失败静默。"""
    try:
        from strange_uta_game.frontend.fluent_widgets import message_error

        message_error(None, title, content, ok_text="继续运行", copyable=True)
        return
    except Exception:
        pass
    try:
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.critical(None, title, content)
    except Exception:
        pass


def _exception_signature(exc_type, exc_value, exc_tb) -> str:
    """异常去重签名：类型 + 最内层抛出位置。"""
    frame = "?"
    while exc_tb is not None:
        frame = f"{exc_tb.tb_frame.f_code.co_filename}:{exc_tb.tb_lineno}"
        exc_tb = exc_tb.tb_next
    return f"{getattr(exc_type, '__module__', '')}.{getattr(exc_type, '__qualname__', exc_type)}@{frame}"


def _exception_headline(exc_value) -> str:
    return f"{type(exc_value).__name__}: {exc_value}" if exc_value is not None else "未知异常"


class CrashGuard:
    """安装并持有全局异常钩子；一个进程只应 install 一次。"""

    def __init__(self, app: QObject, log_dir: Path) -> None:
        self._app = weakref.ref(app)
        self.log_dir = Path(log_dir)
        self._handling = False
        self._recent_dialog_keys: dict[str, float] = {}
        self._reporter = _CrashReporter()
        self._reporter.moveToThread(app.thread())
        self._orig_sys_hook = sys.excepthook
        self._orig_threading_hook = threading.excepthook

    # ---- 安装 ----

    def install(self) -> None:
        sys.excepthook = self._sys_hook
        threading.excepthook = self._threading_hook

    def uninstall(self) -> None:
        sys.excepthook = self._orig_sys_hook
        threading.excepthook = self._orig_threading_hook

    # ---- 钩子入口 ----

    def _sys_hook(self, exc_type, exc_value, exc_tb) -> None:
        if issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
            self._orig_sys_hook(exc_type, exc_value, exc_tb)
            return
        self.handle_exception(exc_type, exc_value, exc_tb, threading.current_thread().name)
        self._chain_sys_hook(exc_type, exc_value, exc_tb)

    def _chain_sys_hook(self, exc_type, exc_value, exc_tb) -> None:
        """嵌入宿主若自装了 ``sys.excepthook``，我们处理完后原样转交。

        宿主进程（krok-helper 等）可能有自己的错误上报钩子——兜底不能剥夺
        宿主的处理权。默认打印钩子（``sys.__excepthook__``）跳过：crash.log
        已更完整，且 pythonw 无控制台时 stderr 为 None，默认钩子不可靠。
        """
        if self._orig_sys_hook is sys.__excepthook__:
            return
        try:
            self._orig_sys_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    def _threading_hook(self, args) -> None:
        if args.exc_type is not None and issubclass(
            args.exc_type, (SystemExit, KeyboardInterrupt)
        ):
            self._orig_threading_hook(args)
            return
        name = args.thread.name if args.thread is not None else "?"
        self.handle_exception(args.exc_type, args.exc_value, args.exc_traceback, f"线程 {name}")
        if self._orig_threading_hook is not threading.__excepthook__:
            try:
                self._orig_threading_hook(args)
            except Exception:
                pass

    # ---- 核心处理 ----

    def handle_exception(self, exc_type, exc_value, exc_tb, origin: str) -> None:
        """记录 + （受控地）弹窗。绝不向上抛异常。"""
        if self._handling:
            # 弹窗/落盘过程中的二次异常：只尽力留痕
            self._write_log(self._format_report(exc_type, exc_value, exc_tb, origin, note="（处理期间的二次异常，仅记录）"))
            return
        self._handling = True
        try:
            text = self._format_report(exc_type, exc_value, exc_tb, origin)
            self._write_log(text)
            self._maybe_show_dialog(exc_type, exc_value, exc_tb)
        except BaseException:
            # 兜底的兜底：钩子里任何失败都不允许再向外冒
            pass
        finally:
            self._handling = False

    def _format_report(self, exc_type, exc_value, exc_tb, origin: str, note: str = "") -> str:
        try:
            tb_text = "".join(
                traceback.format_exception(exc_type, exc_value, exc_tb)
            )
        except Exception:
            tb_text = f"{_exception_headline(exc_value)}\n（traceback 格式化失败）\n"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return f"[{ts}] [{origin}] {note}未处理异常：\n{tb_text}\n"

    def _write_log(self, text: str) -> None:
        try:
            path = self.log_dir / _LOG_FILENAME
            if path.exists() and path.stat().st_size > _MAX_LOG_BYTES:
                rotated = path.with_name(_LOG_FILENAME + ".1")
                try:
                    rotated.unlink(missing_ok=True)
                    path.rename(rotated)
                except OSError:
                    # 轮转失败（文件被占用等）：截断重写，保证可继续追加
                    path.write_text("", encoding="utf-8")
            with path.open("a", encoding="utf-8", errors="replace") as f:
                f.write(text)
        except Exception:
            pass

    def _maybe_show_dialog(self, exc_type, exc_value, exc_tb) -> None:
        app = self._app()
        if app is None or app.thread() is None:
            return
        key = _exception_signature(exc_type, exc_value, exc_tb)
        now = time.monotonic()
        stale = [k for k, t in self._recent_dialog_keys.items() if now - t > _DIALOG_COOLDOWN_S]
        for k in stale:
            self._recent_dialog_keys.pop(k, None)
        if key in self._recent_dialog_keys:
            return  # 同一错误短时间重复：日志已记，不重复打扰
        self._recent_dialog_keys[key] = now

        log_path = self.log_dir / _LOG_FILENAME
        title = "程序遇到未处理的错误"
        content = (
            f"{_exception_headline(exc_value)}\n\n"
            f"详细堆栈已记录到：{log_path}\n"
            "点击「继续运行」尝试恢复；若反复出现请携带该日志反馈。"
        )

        in_app_thread = QThread.currentThread() is app.thread()
        if in_app_thread and self._main_loop_live():
            # 事件循环运行中：弹窗延迟到下一轮（不在当前（可能是弹层销毁
            # 中的）调用栈里再进嵌套事件循环），与下拉模型修复同一思路
            self._reporter.submit(title, content)
        elif in_app_thread:
            # 尚未进入事件循环（启动期）：直接弹，否则进程即将退出没人能看到
            _show_error_dialog(title, content)
        else:
            # worker 线程：转回主线程
            self._reporter.submit(title, content)

    @staticmethod
    def _main_loop_live() -> bool:
        try:
            from PyQt6.QtCore import QAbstractEventDispatcher

            return QAbstractEventDispatcher.instance() is not None
        except Exception:
            return False


_guard: Optional[CrashGuard] = None


def install_crash_guard(app: QObject, log_dir: Optional[Path] = None) -> CrashGuard:
    """安装全局异常兜底（幂等）。应在 QApplication 创建后、业务窗口创建前调用。"""
    global _guard
    if _guard is not None:
        return _guard
    from strange_uta_game.app_dirs import logs_dir

    _guard = CrashGuard(app, log_dir if log_dir is not None else logs_dir())
    _guard.install()
    return _guard
