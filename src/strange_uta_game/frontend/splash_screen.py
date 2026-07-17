"""启动闪屏窗口（仅 standalone 模式）。

以 mascot 图作为整个圆角正方形背景，底部叠加半透明渐变遮罩，
在遮罩上显示应用名、进度环、进度条等初始化信息。
"""

from PyQt6.QtCore import Qt, QRectF
from PyQt6.QtGui import (
    QPainter, QColor, QPixmap, QPainterPath, QLinearGradient,
)
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QApplication

from qfluentwidgets import ProgressRing

from strange_uta_game.frontend.window_sizing import center_on_screen, current_screen


class SplashWindow(QWidget):
    """启动闪屏 — mascot 背景 + 半透明信息浮层。"""

    _SIDE = 400
    _RADIUS = 24

    def __init__(self, icon_path: str = ""):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(self._SIDE, self._SIDE)

        # ---- 背景图（按 devicePixelRatio 缩放以适配 HiDPI）----
        self._bg = QPixmap()
        if icon_path:
            px = QPixmap(icon_path)
            if not px.isNull():
                # 闪屏在 show 前构建，用光标所在屏的 dpr（多显示器下更准确）
                screen = current_screen(self)
                dpr = screen.devicePixelRatio() if screen else 2.0
                target = int(self._SIDE * dpr)
                self._bg = px.scaled(
                    target, target,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._bg.setDevicePixelRatio(dpr)

        # ---- 布局：stretch 把所有控件压到底部遮罩区域 ----
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 0, 32, 20)
        layout.setSpacing(0)

        layout.addStretch()

        # 应用名称 + 副标题 + 版本号（同一行）
        ver_text = ""
        try:
            from strange_uta_game.__version__ import __version__
            ver_text = f"v{__version__}"
        except Exception:
            pass
        title_parts = ["StrangeUtaGame", self.tr("歌词打轴工具")]
        if ver_text:
            title_parts.append(ver_text)
        title = QLabel("  ·  ".join(title_parts))
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet(
            "color: white; font-size: 14px; font-weight: 600;"
        )
        layout.addWidget(title)

        layout.addSpacing(14)

        # 圆圈进度（环形用主题色，百分比文字强制白色）
        self._ring = ProgressRing(self, useAni=False)
        self._ring.setFixedSize(48, 48)
        self._ring.setTextVisible(True)
        self._ring.setValue(0)
        self._ring._drawText = lambda painter, text: (
            painter.setFont(self._ring.font()),
            painter.setPen(Qt.GlobalColor.white),
            painter.drawText(self._ring.rect(), Qt.AlignmentFlag.AlignCenter, text),
        )
        layout.addWidget(self._ring, 0, Qt.AlignmentFlag.AlignCenter)

        layout.addSpacing(6)

        # 状态文本
        self._status = QLabel(self.tr("正在启动..."))
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status.setStyleSheet(
            "color: rgba(255,255,255,0.75); font-size: 12px;"
        )
        self._status.setFixedHeight(18)
        layout.addWidget(self._status)

        self._center_on_screen()

    # ------------------------------------------------------------------

    def _center_on_screen(self) -> None:
        center_on_screen(self)

    def set_progress(self, value: int, text: str = "") -> None:
        """更新进度值和状态文本，并立即重绘。"""
        self._ring.setValue(value)
        if text:
            self._status.setText(text)
        QApplication.processEvents()

    # ------------------------------------------------------------------
    # 绘制：圆角裁剪 → mascot 背景 → 底部半透明渐变遮罩
    # ------------------------------------------------------------------

    def paintEvent(self, event):
        S = self._SIDE
        R = self._RADIUS

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 裁剪为圆角矩形
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(0, 0, S, S), R, R)
        p.setClipPath(clip)

        # 绘制 mascot 背景（devicePixelRatio 已设置，直接绘制即可填满）
        if not self._bg.isNull():
            p.drawPixmap(0, 0, self._bg)
        else:
            p.fillRect(0, 0, S, S, QColor(50, 50, 50))

        # 底部渐变遮罩（仅覆盖下四分之一）
        overlay_y = int(S * 0.65)
        grad = QLinearGradient(0, overlay_y, 0, S)
        grad.setColorAt(0.0, QColor(0, 0, 0, 0))
        grad.setColorAt(0.4, QColor(0, 0, 0, 150))
        grad.setColorAt(1.0, QColor(0, 0, 0, 210))
        p.fillRect(0, overlay_y, S, S - overlay_y, grad)

        p.end()

    # ------------------------------------------------------------------

    def finish(self) -> None:
        """淡出并关闭闪屏。"""
        from PyQt6.QtCore import QPropertyAnimation, QEasingCurve

        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._fade_anim.setDuration(300)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_anim.finished.connect(self.close)
        self._fade_anim.start()
