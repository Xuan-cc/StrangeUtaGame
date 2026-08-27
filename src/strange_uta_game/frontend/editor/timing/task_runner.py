"""自回收后台任务运行器 — 线程生命周期与 UI owner 解耦。

解决问题：QThread 以 UI 控件为 parent 时，控件销毁会连带析构仍在运行的
线程，触发 ``QThread: Destroyed while thread is still running`` 或原生
fast-fail 崩溃（实测 -1073740791）。

设计（纯信号驱动，正常 UI 路径无 ``wait()``）：

- 线程不 parent 到任何 UI 控件，由本模块的进程级注册表持 Python 强引用，
  ``thread.finished`` 后注销并 ``deleteLater``——控件销毁与否都不影响回收。
- ``worker.finished/error → thread.quit``：worker 一返回（含取消路径）事件
  循环即退出；``thread.finished → thread.deleteLater`` 保证线程停止后才销毁。
- ``worker.finished/error → worker.deleteLater`` 是 worker 线程内的直连
  延迟删除，可能在主线程消费排队信号**之前**就把 worker 析构掉——因此 UI
  槽绝不使用 ``sender()``（会拿到悬垂指针）。身份过滤改由 :class:`TaskRelay`
  完成：每个任务一个中继 QObject，parent 为 UI owner（owner 销毁 → 中继
  销毁 → 排队信号被 Qt 丢弃）；中继槽先以 ``is_current()`` 闭包判定任务
  是否仍为当前任务，过期结果直接丢弃。
- ``owner.destroyed → worker.request_cancel``：owner 销毁时请求取消（lambda
  只引用 worker，不访问 UI，安全）。
- 应用退出（``aboutToQuit``）：专门的收尾流程允许 ``wait``——取消全部任务并
  等待线程结束，避免解释器关闭时的原生崩溃。
"""

from __future__ import annotations

from typing import Callable, List, Optional, Tuple

from PyQt6.QtCore import QCoreApplication, QObject, QThread

# (thread, worker) 强引用；thread.finished 后自动注销。
_TASKS: List[Tuple[QThread, QObject]] = []
_shutdown_hooked = False


class TaskRelay(QObject):
    """worker 信号 → UI 槽 的主线程中继，自带任务身份过滤。

    parent 必须是 UI owner：owner 销毁时本对象随之销毁，worker 发往本对象
    的排队信号被 Qt 丢弃，不会触碰已销毁的 UI。每个任务一个实例；
    ``is_current`` 是捕获了 worker 与 owner 状态的闭包（仅在 owner 存活
    时才会被调用——中继是 owner 的子对象）。
    """

    def __init__(
        self,
        owner: QObject,
        is_current: Callable[[], bool],
        on_progress: Optional[Callable] = None,
        on_finished: Optional[Callable] = None,
        on_error: Optional[Callable] = None,
    ):
        super().__init__(owner)
        self._is_current = is_current
        self._on_progress = on_progress
        self._on_finished = on_finished
        self._on_error = on_error

    def _progress(self, value) -> None:
        if self._is_current() and self._on_progress is not None:
            self._on_progress(value)

    def _finished(self, result) -> None:
        if self._is_current() and self._on_finished is not None:
            self._on_finished(result)

    def _error(self, message) -> None:
        if self._is_current() and self._on_error is not None:
            self._on_error(message)


def _hook_app_shutdown() -> None:
    global _shutdown_hooked
    if _shutdown_hooked:
        return
    app = QCoreApplication.instance()
    if app is None:
        return
    _shutdown_hooked = True
    app.aboutToQuit.connect(shutdown)


def _unregister(thread: QThread) -> None:
    for i, (t, _w) in enumerate(_TASKS):
        if t is thread:
            del _TASKS[i]
            return


def start_task(
    owner: QObject,
    worker: QObject,
    *,
    on_progress: Optional[Callable] = None,
    on_finished: Optional[Callable] = None,
    on_error: Optional[Callable] = None,
    is_current: Optional[Callable[[object], bool]] = None,
) -> TaskRelay:
    """启动自回收后台任务并经中继连接 UI 槽。

    ``on_progress/on_finished/on_error`` 必须是 ``owner`` 的方法引用：中继
    以 owner 为 parent，owner 销毁时整条 UI 链自动拆除。``is_current(worker)``
    用于过滤过期任务的结果（如换音频后的旧任务），不传则不过滤——
    通用一次性任务（预热等）不应误绑 owner 上同名的领域方法。
    """
    _hook_app_shutdown()
    thread = QThread()  # 不 parent 到 owner：控件销毁不得析构运行中的线程
    worker.moveToThread(thread)
    thread.started.connect(worker.run)

    # TaskRelay 无参调用 is_current —— 包装为捕获 worker 的零参闭包
    if is_current is not None:
        is_current = (lambda w=worker, f=is_current: f(w))
    relay = TaskRelay(
        owner,
        is_current or (lambda: True),
        on_progress,
        on_finished,
        on_error,
    )
    worker.progress.connect(relay._progress)
    worker.finished.connect(relay._finished)
    worker.error.connect(relay._error)

    # 自回收链（不依赖任何 UI 对象存活）：
    # worker 返回（含取消）→ 退线程事件循环 → 线程停止后销毁两者。
    worker.finished.connect(thread.quit)
    worker.error.connect(thread.quit)
    worker.finished.connect(worker.deleteLater)
    worker.error.connect(worker.deleteLater)
    thread.finished.connect(thread.deleteLater)
    thread.finished.connect(lambda t=thread: _unregister(t))
    # owner 销毁 → 请求取消（lambda 只引用 worker，安全）
    owner.destroyed.connect(lambda _, w=worker: w.request_cancel())

    _TASKS.append((thread, worker))
    thread.start()
    return relay


def running_tasks() -> int:
    return len(_TASKS)


def shutdown(timeout_ms: int = 3000) -> None:
    """应用退出收尾：取消全部任务并等待线程结束（仅退出路径允许 wait）。"""
    for _thread, worker in list(_TASKS):
        worker.request_cancel()
    for thread, _worker in list(_TASKS):
        thread.quit()
        thread.wait(timeout_ms)
    _TASKS.clear()
