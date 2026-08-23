"""前后台性能节流。

判定标准不是「应用是否拥有焦点」：用户可能把窗口摆在屏幕一边看歌词预览、
在另一边干别的事（窗口可见但失焦），此时 UI 刷新必须全速。真正的后台是
窗口最小化/隐藏——没人可能看到画面，全速刷新纯属浪费 CPU。

本模块在 QApplication 上安装事件过滤器，监听窗口 Show/Hide/最小化等
事件后广播 ``visibility_maybe_changed``，消费方各自重估：

- 编辑页播放头轮询（timing_interface._position_poll_timer）：
  自己的顶层窗口最小化/隐藏 → 200ms；可见（含失焦）→ 用户设置刷新率。
- 校准对话框画布动画（calibration_dialog.animation_timer）：
  对话框可见且未最小化 → 运行；否则暂停。
- Win10 系统主题轮询（theme._poll_timer）：
  应用还有任何可见顶层窗口 → 轮询；全部隐藏 → 停。

音频引擎（BASS / sounddevice / 按键音 / 校准节拍器）全部运行在独立线程，
不经过这里，后台播放完全不受影响。打轴时间戳在按键发生时直接读取音频引擎
（perf_counter 外推），也不依赖被降频的 UI 轮询，后台降频不损失精度。

嵌入式宿主注意：同进程 widget 嵌入时编辑器按 ``self.window()`` 自动判定
解析到的是宿主顶层窗口——宿主只隐藏 SUG 区域（切标签页）而宿主窗口仍可见
时，自动判定看不到这层。宿主应通过 ``set_visibility_override(visible)``
（或既有的 ``MainWindow.on_host_visibility_changed``，内部已转发）显式通知：
``False`` 优先于一切自动判定（视为隐藏）；``True``/``None`` 恢复自动判定
（不强制可见——宿主窗口最小化等自动判定仍生效）。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QEvent, QObject, pyqtSignal

# 触发重估的窗口事件。ShowToParent/HideToParent 覆盖嵌入宿主模式下子窗口
# 被宿主显隐的场景（此时 SUG 主窗口不是顶层 widget）。
_REEVALUATE_EVENTS = frozenset(
    {
        QEvent.Type.Show,
        QEvent.Type.Hide,
        QEvent.Type.ShowToParent,
        QEvent.Type.HideToParent,
        QEvent.Type.WindowStateChange,
    }
)


class BackgroundThrottle(QObject):
    """跟踪「还有没有人能看到我们的 UI」并广播给需要降频的服务。"""

    visibility_maybe_changed = pyqtSignal()

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._host_hidden = False  # 嵌入式宿主显式通知的隐藏态
        app.applicationStateChanged.connect(self._refresh_visibility)
        app.installEventFilter(self)

    @property
    def is_visible(self) -> bool:
        """应用 UI 当前是否应按可见处理。

        宿主显式通知的隐藏态优先；否则实时扫描顶层窗口，无缓存。
        """
        if self._host_hidden:
            return False
        try:
            widgets = self._app.topLevelWidgets()
        except RuntimeError:
            return True
        return any(w.isVisible() and not w.isMinimized() for w in widgets)

    def set_visibility_override(self, visible: Optional[bool]) -> None:
        """宿主显式通知可见性。

        ``False``：SUG 被宿主隐藏，优先于一切自动判定（宿主窗口仍可见时
        自动判定看不到这层）；``True``/``None``：恢复自动判定——不强制
        可见，宿主窗口最小化等自动检测仍生效。重复通知是幂等的。
        """
        host_hidden = visible is False
        if host_hidden == self._host_hidden:
            return
        self._host_hidden = host_hidden
        self.visibility_maybe_changed.emit()

    def eventFilter(self, obj, event) -> bool:
        if event.type() in _REEVALUATE_EVENTS and obj.isWidgetType():
            self._refresh_visibility()
        return super().eventFilter(obj, event)

    def _refresh_visibility(self, *args) -> None:
        # 无条件广播：各消费方按自己的窗口（而非全局可见性）重估，
        # 例如主窗口最小化但浮动演唱者窗口仍可见时两者需求不同
        self.visibility_maybe_changed.emit()


_instance: Optional[BackgroundThrottle] = None


def background_throttle() -> Optional[BackgroundThrottle]:
    """进程级单例；QApplication 尚未创建（纯后端/测试场景）时返回 None。

    返回 None 时调用方跳过接入即可——定时器保持全速，行为与
    本模块存在前完全一致，是最安全的退化路径。
    """
    global _instance
    if _instance is None:
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is None:
            return None
        _instance = BackgroundThrottle(app)
    return _instance


def set_visibility_override(visible: Optional[bool]) -> None:
    """嵌入式宿主直接可用的模块级接口；单例未就绪时安全跳过。

    语义同 :meth:`BackgroundThrottle.set_visibility_override`：
    ``False`` 强制按隐藏处理，``True``/``None`` 恢复自动判定。
    """
    throttle = background_throttle()
    if throttle is not None:
        throttle.set_visibility_override(visible)


def ui_visible() -> bool:
    """当前是否应按可见处理（含显式覆盖）；单例未就绪时保守返回 True。"""
    throttle = background_throttle()
    return throttle is None or throttle.is_visible
