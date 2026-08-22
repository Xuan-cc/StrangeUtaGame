from __future__ import annotations

import sys
import threading

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from strange_uta_game.frontend.crash_guard import CrashGuard


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _process_events(qapp, rounds: int = 8) -> None:
    for _ in range(rounds):
        qapp.processEvents()


@pytest.fixture
def guard(qapp, tmp_path, monkeypatch):
    """构建挂到临时日志目录的 CrashGuard，钩子随测试自动还原。

    弹窗函数替换为记录器——测试关心的是「异常被吃掉、落了盘、请求了弹窗」，
    不真的弹 Fluent 对话框。主线程路径固定为直接弹窗（关掉事件循环探测），
    使断言不依赖队列派发时机；跨线程编组用例单独走真实队列。
    """
    g = CrashGuard(qapp, log_dir=tmp_path)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "strange_uta_game.frontend.crash_guard._show_error_dialog",
        lambda title, content: shown.append((title, content)),
    )
    monkeypatch.setattr(CrashGuard, "_main_loop_live", staticmethod(lambda: False))
    monkeypatch.setattr(sys, "excepthook", g._sys_hook)
    monkeypatch.setattr(threading, "excepthook", g._threading_hook)
    g.shown = shown  # type: ignore[attr-defined]
    return g


def test_qt_callback_exception_is_survived_logged_and_dialogued(qapp, guard, tmp_path):
    """Qt 回调（QTimer 触发的槽）里逃逸的异常：进程存活、落盘、请求弹窗。

    若 PyQt 走了默认的 qFatal 路径，本测试进程会直接 abort——能跑到断言
    即证明自定义 excepthook 生效。
    """
    # pytest-qt 会自装 sys.excepthook 捕获事件循环异常；链式转交会（正确地）
    # 把异常再报给 pytest-qt 使本用例报错。本用例聚焦「兜底接线」，把 _orig
    # 置为默认钩子跳过转交；链式语义由 test_host_custom_hook_is_chained 覆盖。
    guard._orig_sys_hook = sys.__excepthook__
    QTimer.singleShot(0, lambda: (_ for _ in ()).throw(RuntimeError("slot-boom")))
    _process_events(qapp)

    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "RuntimeError: slot-boom" in log
    assert guard.shown, "应请求弹窗（直接或经 reporter 队列转主线程）"
    title, content = guard.shown[0]
    assert "未处理的错误" in title
    assert "slot-boom" in content
    assert "crash.log" in content


def test_worker_thread_exception_marshals_dialog_to_main_thread(qapp, guard, tmp_path):
    """threading.Thread 里逃逸的异常：落盘并经队列转回主线程弹窗。"""

    def boom():
        raise ValueError("worker-boom")

    t = threading.Thread(target=boom, name="w1")
    t.start()
    t.join()
    _process_events(qapp)

    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "ValueError: worker-boom" in log
    assert "线程 w1" in log
    assert guard.shown


def test_repeated_identical_exception_dialogs_once_but_logs_all(qapp, guard, tmp_path):
    """同一异常签名冷却期内只弹一次窗，日志逐条记录（防 paint 风暴刷屏）。"""
    try:
        raise RuntimeError("dup-boom")
    except RuntimeError:
        info1 = sys.exc_info()
    info2 = (info1[0], info1[1], info1[2])

    guard.handle_exception(*info1, origin="测试")
    guard.handle_exception(*info2, origin="测试")

    assert len(guard.shown) == 1
    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    # 两份完整报告（每份 traceback 内源码行+异常行各出现一次）
    assert log.count("未处理异常") == 2
    assert log.count("RuntimeError: dup-boom") == 2


def test_exception_inside_dialog_does_not_recurse(qapp, guard, tmp_path):
    """弹窗/处理过程中的二次异常只落盘，不得递归弹窗或向外抛。"""
    guard.shown.clear()

    def exploding_dialog(title, content):
        raise RuntimeError("dialog-boom")

    import strange_uta_game.frontend.crash_guard as cg

    orig = cg._show_error_dialog
    cg._show_error_dialog = exploding_dialog
    try:
        # 主线程 + 无事件循环（dispatcher 存在与否两路都不允许向外抛）
        guard.handle_exception(RuntimeError, RuntimeError("outer-boom"), None, "测试")
        # 触发处理中二次异常：_handling=True 期间再来一个
        guard._handling = True
        guard.handle_exception(RuntimeError, RuntimeError("inner-boom"), None, "测试")
        guard._handling = False
    finally:
        cg._show_error_dialog = orig

    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "outer-boom" in log
    assert "inner-boom" in log


def test_system_exit_passes_through_to_original_hook(qapp, guard):
    called = []
    guard._orig_sys_hook = lambda *a: called.append(a)
    guard._sys_hook(SystemExit, SystemExit(3), None)
    assert called


def test_reporter_batches_storm_into_one_dialog(qapp, guard):
    """reporter 队列积压多条时合并为一个对话框（附带计数）。"""
    guard._reporter.submit("t", "c1")
    guard._reporter.submit("t", "c2")
    guard._reporter.submit("t", "c3")
    _process_events(qapp)

    assert len(guard.shown) == 1
    title, content = guard.shown[0]
    assert "3 个" in content
    assert "c3" in content


def test_host_custom_hook_is_chained_not_replaced(qapp, guard, tmp_path):
    """嵌入宿主自装的 sys.excepthook 在我们处理后仍收到同一异常（不被剥夺）。"""
    chained = []
    guard._orig_sys_hook = lambda *a: chained.append(a[1])
    try:
        raise RuntimeError("chain-boom")
    except RuntimeError:
        info = sys.exc_info()
    guard._sys_hook(*info)

    assert chained and str(chained[0]) == "chain-boom"
    assert guard.shown  # 我们的兜底照常发生
    log = (tmp_path / "crash.log").read_text(encoding="utf-8")
    assert "chain-boom" in log


def test_default_print_hook_is_not_chained(qapp, guard, capsys):
    """原钩子是默认打印钩子时跳过转交：避免重复输出与 pythonw 下 stderr=None。"""
    guard._orig_sys_hook = sys.__excepthook__
    try:
        raise RuntimeError("quiet-boom")
    except RuntimeError:
        info = sys.exc_info()
    guard._sys_hook(*info)

    captured = capsys.readouterr()
    assert "quiet-boom" not in captured.out + captured.err
    assert guard.shown
