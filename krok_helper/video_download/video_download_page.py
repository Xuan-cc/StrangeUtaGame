from __future__ import annotations

import ctypes
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QRectF, QThread, Qt, QTimer, QUrl, pyqtSignal as Signal
from PyQt6.QtGui import QColor, QDesktopServices, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    FluentIcon as FIF,
    LineEdit,
    PlainTextEdit,
    PrimaryPushButton,
    PushButton,
    TableWidget,
    ToolButton,
    ToolTip,
    ToolTipFilter,
    ToolTipPosition,
)
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu
from qfluentwidgets.components.widgets.menu import MenuAnimationType

from .bilibili_auth import BilibiliQrLoginService
from .cookie_manager import BilibiliAccountProfile, CookieManager
from .download_task import (
    DownloadOptions,
    DownloadTask,
    FormatOption,
    NAMING_RULE_CUSTOM,
    NAMING_RULE_TITLE,
    NAMING_RULE_TITLE_UPLOADER,
    SOURCE_BILIBILI,
    SOURCE_YOUTUBE,
    TASK_STATUS_CANCELLED,
    TASK_STATUS_COMPLETED,
    TASK_STATUS_DOWNLOADING,
    TASK_STATUS_FAILED,
    TASK_STATUS_WAITING,
    VideoInfo,
)
from .format_parser import format_bytes
from .video_logic import (
    classify_existing_task,
    find_existing_task_for_info,
    find_matching_format,
    select_default_format,
    video_identity_keys,
)
from .ytdlp_service import DownloadCancelledError, VideoDownloadError, YtDlpService


class _LightToolTipFilter(ToolTipFilter):
    # Use qfluent's themed ToolTip instead of Qt's native QTipLabel (renders dark
    # for buttons inside qfluent's TableWidget). Parentless on purpose: qfluent
    # parents the tip to the main window, whose `QWidget { background: #F4F7FB }`
    # then cascades onto the tip's outer frame as a gray border.
    def _createToolTip(self):
        return ToolTip(self.parent().toolTip())


DELETE_BUTTON_QSS = (
    "QPushButton { background: transparent; border: 1px solid #e5e7eb; border-radius: 8px; padding: 0; }"
    "QPushButton:hover { background: #fff1f2; border-color: #fda4af; }"
)


def _create_delete_button(tooltip: str) -> PushButton:
    button = PushButton(FIF.DELETE, "")
    button.setFixedSize(30, 30)
    button.setToolTip(tooltip)
    button.installEventFilter(_LightToolTipFilter(button, 300, ToolTipPosition.TOP))
    button.setStyleSheet(DELETE_BUTTON_QSS)
    return button


DEFAULT_CUSTOM_TEMPLATE = "{title}"
CONCURRENT_COUNT_OPTIONS = ("1", "2", "3", "4", "5")
TIMEOUT_OPTIONS = ("5", "10", "15")
RETRY_COUNT_OPTIONS = ("1", "2", "3", "4", "5")
VIDEO_DETAILS_CARD_HEIGHT = 340
TASK_SWITCH_COMBO_WIDTH = 720
TASK_SWITCH_TITLE_PIXELS = 610
PLATFORM_STATUS_LOGGED_IN = "#22c55e"
PLATFORM_STATUS_LOGGED_OUT = "#f43f5e"
PLATFORM_STATUS_PENDING = "#b45309"
SEGMENT_STYLE_NORMAL = (
    "QFrame { background: transparent; border: 0; border-radius: 7px; }"
)
SEGMENT_STYLE_SELECTED = (
    "QFrame { background: #FFF1F2; border: 1px solid #fda4af; border-radius: 7px; }"
)
SEGMENT_TITLE_COLOR_NORMAL = "#475467"
SEGMENT_TITLE_COLOR_SELECTED = "#111827"
DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1
DOWNLOAD_TABLE_FIXED_WIDTHS = {
    0: 88,
    2: 92,
    3: 108,
    4: 84,
    5: 118,
    6: 124,
}
COMBO_BOX_VIEW_QSS = """
QAbstractItemView {
    background-color: transparent;
    border: none;
    border-radius: 0px;
    padding: 4px;
    outline: none;
}

QAbstractItemView::item {
    height: 32px;
    padding: 0 12px;
    border-radius: 6px;
}

QAbstractItemView::item:selected {
    background-color: #FFF1F2;
    color: black;
}
"""


def open_in_explorer(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.Popen(["explorer", str(path)])


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "-"
    total_seconds = int(round(seconds))
    minutes, remain = divmod(total_seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remain:02d}"
    return f"{minutes}:{remain:02d}"


def format_speed(speed: float | int | None) -> str:
    if not speed or speed <= 0:
        return ""
    return f"{format_bytes(int(speed))}/s"


class PanelCard(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: int = 16,
        padding: tuple[int, int, int, int] = (16, 16, 16, 16),
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self._padding = padding
        self._spacing = spacing
        self.setObjectName("PanelCard")
        self.setStyleSheet(
            f"""
            QFrame#PanelCard {{
                background: #ffffff;
                border: 1px solid rgba(226, 232, 240, 0.95);
                border-radius: {radius}px;
            }}
            """
        )

    def create_vbox(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*self._padding)
        layout.setSpacing(self._spacing)
        return layout

    def create_hbox(self) -> QHBoxLayout:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*self._padding)
        layout.setSpacing(self._spacing)
        return layout

    def create_grid(self) -> QGridLayout:
        layout = QGridLayout(self)
        layout.setContentsMargins(*self._padding)
        layout.setHorizontalSpacing(self._spacing)
        layout.setVerticalSpacing(self._spacing)
        return layout


class ClickableFrame(QFrame):
    clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QFrame { background: transparent; border: 0; }")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class AdaptiveStackedWidget(QStackedWidget):
    def sizeHint(self):  # noqa: N802
        current = self.currentWidget()
        if current is not None:
            return current.sizeHint()
        return super().sizeHint()

    def minimumSizeHint(self):  # noqa: N802
        current = self.currentWidget()
        if current is not None:
            return current.minimumSizeHint()
        return super().minimumSizeHint()

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802
        super().setCurrentIndex(index)
        self.updateGeometry()


class ExpandablePanelCard(PanelCard):
    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        expanded: bool = True,
        radius: int = 16,
        padding: tuple[int, int, int, int] = (16, 16, 16, 16),
        spacing: int = 12,
    ) -> None:
        super().__init__(parent, radius=radius, padding=padding, spacing=spacing)
        self._expanded = expanded

        outer_layout = self.create_vbox()
        outer_layout.setSpacing(0)

        self.header = ClickableFrame(self)
        header_layout = QHBoxLayout(self.header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.title_label = QLabel(title)
        self.title_label.setProperty("panelTitle", True)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        header_layout.addWidget(self.title_label, 1)

        self.toggle_button = ToolButton(self.header)
        self.toggle_button.setFixedSize(28, 28)
        self.toggle_button.setStyleSheet("QToolButton { background: transparent; border: 0; }")
        header_layout.addWidget(self.toggle_button, 0, Qt.AlignmentFlag.AlignRight)

        self.content_widget = QWidget(self)
        self.content_widget.setStyleSheet("background: transparent; border: 0;")
        self.content_widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 12, 0, 0)
        self.content_layout.setSpacing(spacing)

        outer_layout.addWidget(self.header)
        outer_layout.addWidget(self.content_widget)

        self.header.clicked.connect(self.toggle_expanded)
        self.toggle_button.clicked.connect(self.toggle_expanded)
        self.set_expanded(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self.content_widget.setVisible(expanded)
        icon = FIF.CHEVRON_DOWN_MED if expanded else FIF.CHEVRON_RIGHT_MED
        self.toggle_button.setIcon(icon.icon())

    def toggle_expanded(self) -> None:
        self.set_expanded(not self._expanded)


class WhiteComboBoxMenu(ComboBoxMenu):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        self.view.setStyleSheet(COMBO_BOX_VIEW_QSS)
        self.view.setFrameShape(QFrame.Shape.NoFrame)
        self.hBoxLayout.setContentsMargins(0, 0, 0, 0)
        self.hBoxLayout.setSpacing(0)
        self.view.setViewportMargins(0, 0, 0, 0)
        self.setShadowEffect(blurRadius=0, offset=(0, 0), color=QColor(0, 0, 0, 0))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        try:
            preference = ctypes.c_int(DWMWCP_DONOTROUND)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                int(self.winId()),
                DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(preference),
                ctypes.sizeof(preference),
            )
        except Exception:
            pass

    def exec(self, pos, ani=True, aniType=MenuAnimationType.DROP_DOWN):
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.adjustSize(pos, aniType)

        overflow = self.view.verticalScrollBar().maximum()
        if overflow > 0:
            self.view.setFixedHeight(self.view.height() + overflow + 8)

        self.adjustSize()
        return super().exec(pos, ani, aniType)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor("#EAEAEA"), 1))
        painter.setBrush(QColor("white"))
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 8, 8)


class StyledComboBox(ComboBox):
    def _createComboMenu(self):
        return WhiteComboBoxMenu(self)


class QrPlaceholder(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(210, 210)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pixmap = QPixmap()
        self._message = "正在准备二维码…"
        self._login_url = ""

    def set_qr_image(self, image_bytes: bytes, login_url: str) -> None:
        self._login_url = login_url
        if image_bytes and self._pixmap.loadFromData(image_bytes):
            self.update()
            return
        self._pixmap = QPixmap()
        self._message = "二维码生成失败，点击在浏览器中打开"
        self.update()

    def set_message(self, message: str, login_url: str = "") -> None:
        if login_url:
            self._login_url = login_url
        self._message = message or "请点击刷新状态重新生成二维码"
        self._pixmap = QPixmap()
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._login_url
            and self.rect().contains(event.position().toPoint())
        ):
            QDesktopServices.openUrl(QUrl(self._login_url))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("#e5e7eb"), 1))
        painter.setBrush(QColor("#f8fafc"))
        painter.drawRoundedRect(rect, 18, 18)

        if not self._pixmap.isNull():
            qr_rect = rect.adjusted(16, 16, -16, -16)
            scaled = self._pixmap.scaled(
                qr_rect.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            target_x = int(qr_rect.center().x() - scaled.width() / 2)
            target_y = int(qr_rect.center().y() - scaled.height() / 2)
            painter.drawPixmap(target_x, target_y, scaled)
            return

        painter.setPen(QPen(QColor("#111827"), 5))
        box = rect.adjusted(26, 26, -26, -26)
        size = 32
        for x, y in (
            (box.left(), box.top()),
            (box.right() - size, box.top()),
            (box.left(), box.bottom() - size),
        ):
            painter.drawRect(x, y, size, size)

        painter.setPen(QColor("#94a3b8"))
        painter.setFont(QFont("Microsoft YaHei UI", 10))
        painter.drawText(rect.adjusted(20, 148, -20, -20), Qt.AlignmentFlag.AlignCenter, self._message)


class AvatarLabel(QLabel):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(72, 72)
        self._pixmap = QPixmap()
        self._fallback_text = "B"

    def set_avatar(self, image_bytes: bytes, fallback_text: str) -> None:
        self._fallback_text = (fallback_text or "B").strip()[:1].upper() or "B"
        self._pixmap = QPixmap()
        if image_bytes:
            self._pixmap.loadFromData(image_bytes)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(QColor("#dbeafe"), 1))
        painter.setBrush(QColor("#eff6ff"))
        painter.drawEllipse(rect)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter.setClipPath(self._build_clip_path(rect))
            painter.drawPixmap(
                int(rect.center().x() - scaled.width() / 2),
                int(rect.center().y() - scaled.height() / 2),
                scaled,
            )
            painter.setClipping(False)
            return

        painter.setPen(QColor("#2563eb"))
        font = QFont("Microsoft YaHei UI", 24)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._fallback_text)

    def _build_clip_path(self, rect):
        from PyQt6.QtGui import QPainterPath

        path = QPainterPath()
        path.addEllipse(QRectF(rect))
        return path


class MiniAvatarLabel(QLabel):
    def __init__(self, text: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(30, 30)
        self._text = text
        self._color = color
        self._pixmap = QPixmap()

    def set_avatar(self, image_bytes: bytes, fallback_text: str, color: str = "#38bdf8") -> None:
        self._text = (fallback_text or "B").strip()[:1].upper() or "B"
        self._color = color
        self._pixmap = QPixmap()
        if image_bytes:
            self._pixmap.loadFromData(image_bytes)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(rect)

        if not self._pixmap.isNull():
            scaled = self._pixmap.scaled(
                rect.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
            from PyQt6.QtGui import QPainterPath

            path = QPainterPath()
            path.addEllipse(QRectF(rect))
            painter.setClipPath(path)
            painter.drawPixmap(
                int(rect.center().x() - scaled.width() / 2),
                int(rect.center().y() - scaled.height() / 2),
                scaled,
            )
            painter.setClipping(False)
            return

        painter.setPen(QColor("white"))
        font = QFont("Microsoft YaHei UI", 10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._text)


@dataclass(slots=True)
class ParsedBatch:
    infos: list[VideoInfo]
    errors: list[str]
    groups: list["ParsedVideoGroup"] | None = None


@dataclass(slots=True)
class ParsedVideoGroup:
    source_url: str
    infos: list[VideoInfo]


class ParseLinksWorker(QThread):
    batchFinished = Signal(object)

    def __init__(
        self,
        urls: list[str],
        cookie_files_by_source: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._urls = urls
        self._cookie_files_by_source = cookie_files_by_source
        self._service = YtDlpService()

    def run(self) -> None:  # noqa: D401
        infos: list[VideoInfo] = []
        groups: list[ParsedVideoGroup] = []
        errors: list[str] = []
        for url in self._urls:
            try:
                source = self._service.detect_source(url)
                cookie_file = self._cookie_files_by_source.get(source, "")
                parsed_infos = self._service.extract_infos(url, cookie_file)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{url}：{exc}")
                continue
            infos.extend(parsed_infos)
            groups.append(ParsedVideoGroup(source_url=url, infos=parsed_infos))
        self.batchFinished.emit(ParsedBatch(infos=infos, errors=errors, groups=groups))


class DownloadWorker(QThread):
    progressChanged = Signal(str, object)
    taskSucceeded = Signal(str)
    taskFailed = Signal(str, str)
    taskCancelled = Signal(str)

    def __init__(self, task: DownloadTask, options: DownloadOptions, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task = task
        self._options = options
        self._service = YtDlpService()

    def run(self) -> None:  # noqa: D401
        if self._task.cancel_requested:
            self.taskCancelled.emit(self._task.task_id)
            return
        try:
            self._service.download(
                self._task,
                self._options,
                lambda payload: self.progressChanged.emit(self._task.task_id, payload),
            )
        except DownloadCancelledError:
            self.taskCancelled.emit(self._task.task_id)
            return
        except Exception as exc:  # noqa: BLE001
            self.taskFailed.emit(self._task.task_id, str(exc))
            return
        self.taskSucceeded.emit(self._task.task_id)


class CookieImportWorker(QThread):
    importSucceeded = Signal(str)
    importFailed = Signal(str)

    def __init__(self, cookie_manager: CookieManager, platform: str, browser: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cookie_manager = cookie_manager
        self._platform = platform
        self._browser = browser

    def run(self) -> None:  # noqa: D401
        try:
            path = self._cookie_manager.import_from_browser(self._platform, self._browser)
        except Exception as exc:  # noqa: BLE001
            self.importFailed.emit(str(exc))
            return
        self.importSucceeded.emit(str(path))


class YtDlpUpdateWorker(QThread):
    updateSucceeded = Signal(str, str)
    updateFailed = Signal(str)

    def run(self) -> None:  # noqa: D401
        service = YtDlpService()
        try:
            before_version = service.get_ytdlp_version()
            update_output = service.update_ytdlp()
            after_version = service.get_ytdlp_version()
            latest_version = service.get_latest_ytdlp_version()
        except Exception as exc:  # noqa: BLE001
            self.updateFailed.emit(str(exc))
            return
        if service.normalize_version(after_version) != service.normalize_version(latest_version):
            self.updateFailed.emit(
                "yt-dlp 更新命令已执行，但当前实际使用的版本仍不是最新版："
                f"{after_version}，最新版 {latest_version}。\n"
                "请检查 PATH 中的 yt-dlp 是否来自另一个 Python 环境。"
            )
            return
        version_text = after_version if before_version == after_version else f"{before_version} → {after_version}"
        self.updateSucceeded.emit(version_text, update_output)


class BilibiliQrLoginWorker(QThread):
    qrReady = Signal(bytes, str)
    loginStatusChanged = Signal(object)
    loginSucceeded = Signal(str)
    loginFailed = Signal(str)

    def __init__(self, cookie_manager: CookieManager, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._cookie_manager = cookie_manager
        self._stop_requested = False

    def stop(self) -> None:
        self._stop_requested = True

    def run(self) -> None:  # noqa: D401
        service = BilibiliQrLoginService(self._cookie_manager)
        try:
            ticket = service.request_qr_ticket()
        except Exception as exc:  # noqa: BLE001
            self.loginFailed.emit(str(exc))
            return

        self.qrReady.emit(ticket.image_bytes, ticket.login_url)
        self.loginStatusChanged.emit({"code": 86101, "message": "请使用哔哩哔哩 App 扫码"})

        while not self._stop_requested:
            try:
                status = service.poll_login(ticket.qrcode_key)
            except Exception as exc:  # noqa: BLE001
                self.loginFailed.emit(str(exc))
                return

            self.loginStatusChanged.emit({"code": status.code, "message": status.message, "success": status.success})
            if status.success:
                self.loginSucceeded.emit(self._cookie_manager.get_cookie_path() or "")
                return
            if status.code == 86038:
                return
            self.msleep(1800)


class VideoDownloadPage(QWidget):
    settingsChanged = Signal()

    def __init__(self, settings, save_settings: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self._save_settings = save_settings
        self.cookie_manager = CookieManager(getattr(settings, "video_download_cookie_path", ""))
        self._parse_worker: ParseLinksWorker | None = None
        self._cookie_import_worker: CookieImportWorker | None = None
        self._ytdlp_update_worker: YtDlpUpdateWorker | None = None
        self._qr_login_worker: BilibiliQrLoginWorker | None = None
        self._running_workers: dict[str, DownloadWorker] = {}
        self._tasks: list[DownloadTask] = []
        self._task_index: dict[str, DownloadTask] = {}
        self._current_task_id = ""
        self._format_options: list[FormatOption] = []
        self._format_table_updating = False
        self._per_video_controls_updating = False
        self._selection_syncing = False
        self._bilibili_profile: BilibiliAccountProfile | None = None
        self._youtube_profile: BilibiliAccountProfile | None = None
        self._recent_bilibili_login_deadline = 0.0

        self._build_ui()
        self._load_settings()
        self._refresh_cookie_status()
        self._refresh_youtube_cookie_status()
        self._ensure_qr_login()
        self._refresh_preview()
        self._refresh_download_table()

    def _build_ui(self) -> None:
        self.setStyleSheet(
            """
            QLabel[panelTitle="true"] {
                background: transparent;
                border: 0;
                color: #111827;
                font-size: 13pt;
                font-weight: 700;
            }
            QLabel[sectionTitle="true"] {
                background: transparent;
                border: 0;
                color: #111827;
                font-size: 11pt;
                font-weight: 700;
            }
            QLabel[hint="true"] {
                background: transparent;
                border: 0;
                color: #6b7280;
                font-size: 10pt;
            }
            QLabel, CaptionLabel, BodyLabel {
                background: transparent;
                border: 0;
                font-family: "Microsoft YaHei UI";
                font-weight: 400;
            }
            TableWidget {
                background: #ffffff;
                border: 1px solid rgba(203, 213, 225, 0.9);
                border-radius: 16px;
                gridline-color: transparent;
            }
            TableWidget::item {
                padding: 8px 10px;
                border-bottom: 1px solid rgba(226, 232, 240, 0.85);
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #111827;
                border: 0;
                border-right: 1px solid rgba(226, 232, 240, 0.8);
                border-bottom: 1px solid rgba(226, 232, 240, 0.9);
                padding: 8px;
                font-weight: 700;
            }
            """
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        left_panel = self._build_left_panel()
        center_panel = self._build_center_panel()

        left_panel.setFixedWidth(320)

        root.addWidget(left_panel, 0)
        root.addWidget(center_panel, 1)

    def _build_left_panel(self) -> QWidget:
        panel = PanelCard(self, padding=(12, 12, 12, 12), spacing=12)
        panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = panel.create_vbox()

        layout.addWidget(self._build_account_card(panel), 0)
        layout.addWidget(self._build_account_status_card(panel), 0)
        layout.addStretch(1)

        return panel

    def _build_center_panel(self) -> QWidget:
        panel = QWidget(self)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        input_card = PanelCard(panel)
        input_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        input_layout = input_card.create_vbox()
        input_layout.addWidget(self._create_panel_title("视频链接输入"))

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(12)
        self.link_input = PlainTextEdit()
        self.link_input.setPlaceholderText("粘贴 YouTube 或 Bilibili 视频链接，每行一个链接")
        self.link_input.setFixedHeight(76)
        input_row.addWidget(self.link_input, 1)

        input_buttons = QVBoxLayout()
        input_buttons.setContentsMargins(0, 0, 0, 0)
        input_buttons.setSpacing(10)
        self.parse_button = PrimaryPushButton(FIF.LINK, "解析")
        self.clear_input_button = PushButton(FIF.DELETE, "清空")
        self.parse_button.clicked.connect(self._start_parse)
        self.clear_input_button.clicked.connect(self.link_input.clear)
        input_buttons.addWidget(self.parse_button)
        input_buttons.addWidget(self.clear_input_button)
        input_buttons.addStretch(1)
        input_row.addLayout(input_buttons)
        input_layout.addLayout(input_row)

        self.parse_status_label = CaptionLabel("准备解析视频链接。")
        self.parse_status_label.setWordWrap(True)
        input_layout.addWidget(self.parse_status_label)

        info_card = PanelCard(panel)
        info_card.setFixedHeight(VIDEO_DETAILS_CARD_HEIGHT)
        info_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        info_layout = info_card.create_vbox()
        info_title_row = QHBoxLayout()
        info_title_row.setContentsMargins(0, 0, 0, 0)
        info_title_row.setSpacing(8)
        info_title_row.addWidget(self._create_panel_title("视频信息与下载设置"), 1)
        self.delete_current_task_button = _create_delete_button("从列表删除当前视频")
        self.delete_current_task_button.clicked.connect(self._delete_current_task)
        info_title_row.addWidget(self.delete_current_task_button, 0)
        info_layout.addLayout(info_title_row)

        self.video_details_stack = QStackedWidget(info_card)
        self.video_details_stack.setStyleSheet("background: transparent; border: 0;")
        self.video_details_stack.addWidget(self._build_video_empty_state(info_card))
        self.video_details_stack.addWidget(self._build_video_details_state(info_card))
        info_layout.addWidget(self.video_details_stack, 1)

        download_card = PanelCard(panel)
        download_card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        download_layout = download_card.create_vbox()
        download_title_row = QHBoxLayout()
        download_title_row.setContentsMargins(0, 0, 0, 0)
        download_title_row.setSpacing(8)
        download_title_row.addWidget(self._create_panel_title("下载列表"), 1)
        self.download_settings_button = ToolButton(FIF.SETTING)
        self.download_settings_button.setFixedSize(30, 30)
        self.download_settings_button.setToolTip("下载设置")
        self.download_settings_button.setStyleSheet(
            "QToolButton { background: transparent; border: 1px solid #e5e7eb; border-radius: 8px; }"
            "QToolButton:hover { background: #f8fafc; }"
        )
        self.download_settings_button.clicked.connect(self._open_download_settings_dialog)
        download_title_row.addWidget(self.download_settings_button, 0)
        download_layout.addLayout(download_title_row)

        self.download_table = TableWidget()
        self.download_table.setColumnCount(7)
        self.download_table.setHorizontalHeaderLabels(["状态", "标题", "来源", "分辨率", "进度", "大小", "操作"])
        self.download_table.verticalHeader().hide()
        self.download_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.download_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.download_table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.download_table.setWordWrap(False)
        self.download_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.download_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        download_header = self.download_table.horizontalHeader()
        download_header.setStretchLastSection(False)
        download_header.setMinimumSectionSize(44)
        download_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        download_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        download_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        download_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        download_header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        download_header.setSectionResizeMode(5, QHeaderView.ResizeMode.Fixed)
        download_header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        for column, width in DOWNLOAD_TABLE_FIXED_WIDTHS.items():
            self.download_table.setColumnWidth(column, width)
        self.download_table.itemSelectionChanged.connect(self._handle_task_selection_changed)
        download_layout.addWidget(self.download_table, 1)
        self.download_hint_label = CaptionLabel("暂无下载任务。")
        download_layout.addWidget(self.download_hint_label)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 4, 0, 0)
        action_row.setSpacing(10)
        self.start_all_button = PrimaryPushButton(FIF.DOWNLOAD, "全部开始")
        self.pause_all_button = PushButton(FIF.PAUSE, "全部暂停")
        self.cancel_all_button = PushButton(FIF.CANCEL, "全部取消")
        self.open_folder_button = PushButton(FIF.FOLDER, "打开文件夹")
        self.clear_list_button = PushButton(FIF.DELETE, "清空列表")
        self.pause_all_button.setEnabled(False)
        self.pause_all_button.setToolTip("预留接口，后续接入任务暂停。")
        self.start_all_button.clicked.connect(self._start_all_downloads)
        self.cancel_all_button.clicked.connect(self._cancel_all_downloads)
        self.open_folder_button.clicked.connect(self._open_save_folder)
        self.clear_list_button.clicked.connect(self._clear_task_list)
        action_row.addWidget(self.start_all_button)
        action_row.addWidget(self.pause_all_button)
        action_row.addWidget(self.cancel_all_button)
        action_row.addWidget(self.open_folder_button)
        action_row.addWidget(self.clear_list_button)
        action_row.addStretch(1)
        download_layout.addLayout(action_row)

        layout.addWidget(input_card)
        layout.addWidget(info_card)
        layout.addWidget(download_card, 1)
        return panel

    def _build_video_empty_state(self, parent: QWidget) -> QWidget:
        empty = QWidget(parent)
        empty.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(empty)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addStretch(1)

        icon = QLabel("↓")
        icon.setFixedSize(96, 96)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet(
            "background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 48px; color: #cbd5e1; font-size: 34pt;"
        )
        title = BodyLabel("粘贴链接并点击解析")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("color: #334155; font-size: 13pt; font-weight: 700;")
        hint = CaptionLabel("解析后将在这里显示视频信息与每个视频独立的下载设置")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: #64748b;")
        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addStretch(1)
        return empty

    def _build_video_details_state(self, parent: QWidget) -> QWidget:
        details = QWidget(parent)
        details.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(details)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(0, 0, 0, 0)
        switch_row.setSpacing(8)
        switch_row.addStretch(1)
        self.prev_task_button = ToolButton(FIF.LEFT_ARROW)
        self.prev_task_button.setFixedSize(30, 30)
        self.prev_task_button.clicked.connect(lambda: self._move_task_selection(-1))
        self.task_switch_combo = StyledComboBox()
        self.task_switch_combo.setFixedWidth(TASK_SWITCH_COMBO_WIDTH)
        self.task_switch_combo.setFixedHeight(32)
        self.task_switch_combo.currentIndexChanged.connect(self._handle_task_switch_combo_changed)
        self._install_single_click_combo_behavior(self.task_switch_combo)
        self.task_total_label = CaptionLabel("/ 0")
        self.task_total_label.setStyleSheet("color: #475467;")
        self.next_task_button = ToolButton(FIF.RIGHT_ARROW)
        self.next_task_button.setFixedSize(30, 30)
        self.next_task_button.clicked.connect(lambda: self._move_task_selection(1))
        switch_row.addWidget(self.prev_task_button, 0)
        switch_row.addWidget(self.task_switch_combo, 0)
        switch_row.addWidget(self.task_total_label, 0, Qt.AlignmentFlag.AlignVCenter)
        switch_row.addWidget(self.next_task_button, 0)
        switch_row.addStretch(1)
        layout.addLayout(switch_row)

        info_row = QHBoxLayout()
        info_row.setContentsMargins(0, 0, 0, 0)
        info_row.setSpacing(18)
        info_row.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.thumbnail_label = QLabel("暂无视频信息")
        self.thumbnail_label.setFixedSize(250, 142)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setStyleSheet(
            "background: #f8fafc; border: 1px solid #e5e7eb; border-radius: 8px; color: #94a3b8; font-size: 11pt;"
        )
        info_row.addWidget(self.thumbnail_label, 0)

        meta_widget = QWidget()
        meta_widget.setStyleSheet("background: transparent; border: 0;")
        meta_layout = QGridLayout(meta_widget)
        meta_layout.setContentsMargins(0, 4, 0, 0)
        meta_layout.setHorizontalSpacing(12)
        meta_layout.setVerticalSpacing(8)
        meta_layout.setColumnStretch(0, 0)
        meta_layout.setColumnStretch(1, 1)
        self.info_value_labels: dict[str, QLabel] = {}
        for row, (key, title) in enumerate(
            (
                ("title", "标题"),
                ("uploader", "作者"),
                ("duration", "时长"),
                ("resolution", "分辨率"),
                ("filesize", "大小"),
            )
        ):
            label = QLabel(f"{title}：")
            label.setStyleSheet("color: #475467;")
            value = QLabel("-")
            value.setWordWrap(False)
            value.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            value.setStyleSheet("color: #111827; font-weight: 400;")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            meta_layout.addWidget(label, row, 0)
            meta_layout.addWidget(value, row, 1)
            self.info_value_labels[key] = value
        info_row.addWidget(meta_widget, 1, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(info_row)
        layout.addSpacing(12)

        settings_grid = QGridLayout()
        settings_grid.setContentsMargins(0, 0, 0, 0)
        settings_grid.setHorizontalSpacing(10)
        settings_grid.setVerticalSpacing(6)
        settings_grid.setColumnStretch(1, 1)
        settings_grid.setColumnStretch(2, 1)

        settings_grid.addWidget(CaptionLabel("清晰度 / 格式"), 0, 0, Qt.AlignmentFlag.AlignVCenter)
        self.format_combo = StyledComboBox()
        self.format_combo.setMinimumHeight(34)
        self.format_combo.currentIndexChanged.connect(self._handle_format_combo_changed)
        self._install_single_click_combo_behavior(self.format_combo)
        settings_grid.addWidget(self.format_combo, 0, 1, 1, 4)

        settings_grid.addWidget(CaptionLabel("文件命名"), 1, 0, Qt.AlignmentFlag.AlignVCenter)
        self.naming_rule_combo = StyledComboBox()
        self.naming_rule_combo.setMinimumHeight(34)
        self.naming_rule_combo.setFixedWidth(220)
        self.naming_rule_combo.addItems([NAMING_RULE_TITLE, NAMING_RULE_TITLE_UPLOADER, NAMING_RULE_CUSTOM])
        self._install_single_click_combo_behavior(self.naming_rule_combo)
        self.naming_rule_combo.currentTextChanged.connect(self._handle_per_video_settings_changed)
        settings_grid.addWidget(self.naming_rule_combo, 1, 1)

        self.custom_template_container = QWidget(details)
        self.custom_template_container.setFixedHeight(34)
        self.custom_template_container.setStyleSheet("background: transparent; border: 0;")
        custom_template_layout = QHBoxLayout(self.custom_template_container)
        custom_template_layout.setContentsMargins(0, 0, 0, 0)
        custom_template_layout.setSpacing(0)
        self.custom_template_edit = LineEdit()
        self.custom_template_edit.setPlaceholderText("{title} - {uploader}")
        self.custom_template_edit.textChanged.connect(self._handle_per_video_settings_changed)
        custom_template_layout.addWidget(self.custom_template_edit)
        settings_grid.addWidget(self.custom_template_container, 1, 2)

        self.per_video_merge_checkbox = CheckBox("自动合并音视频（推荐）")
        self.per_video_thumbnail_checkbox = CheckBox("下载封面")
        self.per_video_merge_checkbox.stateChanged.connect(self._handle_per_video_settings_changed)
        self.per_video_thumbnail_checkbox.stateChanged.connect(self._handle_per_video_settings_changed)
        settings_grid.addWidget(self.per_video_merge_checkbox, 1, 3)
        settings_grid.addWidget(self.per_video_thumbnail_checkbox, 1, 4)
        layout.addLayout(settings_grid)

        self.format_summary_widget = QWidget(details)
        self.format_summary_widget.hide()
        self.format_value_labels = {
            "format": QLabel("-"),
            "resolution": QLabel("-"),
            "video_codec": QLabel("-"),
            "audio_codec": QLabel("-"),
            "filesize": QLabel("-"),
        }
        self.format_table = TableWidget()
        self.format_table.hide()
        self.format_table.setColumnCount(6)
        self.format_table.setHorizontalHeaderLabels(["选择", "格式", "分辨率", "视频编码", "音频编码", "大小"])
        self.format_table.verticalHeader().hide()
        self.format_table.setEditTriggers(TableWidget.EditTrigger.NoEditTriggers)
        self.format_table.setSelectionBehavior(TableWidget.SelectionBehavior.SelectRows)
        self.format_table.setSelectionMode(TableWidget.SelectionMode.SingleSelection)
        self.format_table.itemChanged.connect(self._handle_format_item_changed)
        self.format_table.cellClicked.connect(self._handle_format_cell_clicked)
        self.format_hint_label = CaptionLabel("请先解析视频链接。")
        self.format_hint_label.hide()
        return details

    def _build_account_card(self, parent: QWidget) -> QWidget:
        account_card = PanelCard(parent, padding=(16, 16, 16, 14))
        account_layout = account_card.create_vbox()
        account_layout.addWidget(self._create_panel_title("账号"))

        self.account_segment_row = QWidget(account_card)
        self.account_segment_row.setObjectName("AccountSegmentRow")
        self.account_segment_row.setStyleSheet(
            """
            QWidget#AccountSegmentRow {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            """
        )
        segment_layout = QHBoxLayout(self.account_segment_row)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(0)
        self.bilibili_segment = self._create_account_segment("Bilibili")
        self.youtube_segment = self._create_account_segment("YouTube")
        self.bilibili_segment.clicked.connect(lambda: self._switch_account_platform(SOURCE_BILIBILI))
        self.youtube_segment.clicked.connect(lambda: self._switch_account_platform(SOURCE_YOUTUBE))
        segment_layout.addWidget(self.bilibili_segment, 1)
        segment_layout.addWidget(self.youtube_segment, 1)
        account_layout.addWidget(self.account_segment_row)

        self.account_stack = AdaptiveStackedWidget(account_card)
        self.account_stack.setStyleSheet("background: transparent; border: 0;")
        self.account_stack.addWidget(self._build_bilibili_account_panel(account_card))
        self.account_stack.addWidget(self._build_youtube_account_panel(account_card))
        account_layout.addWidget(self.account_stack)
        self._switch_account_platform(SOURCE_BILIBILI)

        return account_card

    def _create_account_segment(self, title: str) -> ClickableFrame:
        segment = ClickableFrame()
        segment.setFixedHeight(34)
        segment.setStyleSheet(SEGMENT_STYLE_NORMAL)
        layout = QHBoxLayout(segment)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)
        layout.addStretch(1)
        title_label = BodyLabel(title)
        title_label.setStyleSheet(
            f"background: transparent; border: 0; color: {SEGMENT_TITLE_COLOR_NORMAL}; font-weight: 400;"
        )
        dot = QFrame(segment)
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {PLATFORM_STATUS_LOGGED_OUT}; border-radius: 5px;")
        layout.addWidget(title_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)
        segment.status_dot = dot  # type: ignore[attr-defined]
        segment.title_label = title_label  # type: ignore[attr-defined]
        return segment

    def _build_bilibili_account_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        panel.setStyleSheet("background: transparent; border: 0;")
        cookie_layout = QVBoxLayout(panel)
        cookie_layout.setContentsMargins(0, 8, 0, 0)
        cookie_layout.setSpacing(10)

        self.qr_wrapper = QWidget(panel)
        self.qr_wrapper.setStyleSheet("background: transparent; border: 0;")
        qr_layout = QVBoxLayout(self.qr_wrapper)
        qr_layout.setContentsMargins(0, 0, 0, 0)
        qr_layout.setSpacing(10)
        self.qr_placeholder = QrPlaceholder()
        qr_layout.addWidget(self.qr_placeholder, 0, Qt.AlignmentFlag.AlignHCenter)
        cookie_layout.addWidget(self.qr_wrapper)

        self.account_profile_widget = QWidget(panel)
        self.account_profile_widget.setObjectName("AccountProfileWidget")
        self.account_profile_widget.setStyleSheet(
            """
            QWidget#AccountProfileWidget {
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 16px;
            }
            QWidget#AccountProfileWidget QLabel,
            QWidget#AccountProfileWidget BodyLabel,
            QWidget#AccountProfileWidget CaptionLabel {
                background: transparent;
                border: 0;
            }
            """
        )
        account_layout = QVBoxLayout(self.account_profile_widget)
        account_layout.setContentsMargins(16, 16, 16, 16)
        account_layout.setSpacing(8)
        self.account_avatar_label = AvatarLabel()
        self.account_name_label = BodyLabel("Bilibili 用户")
        self.account_name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_name_label.setStyleSheet("color: #111827; font-size: 12pt; font-weight: 400;")
        self.account_hint_label = CaptionLabel("当前已登录 Bilibili 账号")
        self.account_hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.account_hint_label.setStyleSheet("color: #64748b;")
        account_layout.addWidget(self.account_avatar_label, 0, Qt.AlignmentFlag.AlignHCenter)
        account_layout.addWidget(self.account_name_label, 0, Qt.AlignmentFlag.AlignHCenter)
        account_layout.addWidget(self.account_hint_label, 0, Qt.AlignmentFlag.AlignHCenter)
        self.account_profile_widget.hide()
        cookie_layout.addWidget(self.account_profile_widget)

        status_row = QWidget(panel)
        status_row.setStyleSheet("background: transparent; border: 0;")
        status_row_layout = QHBoxLayout(status_row)
        status_row_layout.setContentsMargins(0, 0, 0, 0)
        status_row_layout.setSpacing(8)
        status_row_layout.addStretch(1)
        self.cookie_status_dot = QFrame(status_row)
        self.cookie_status_dot.setFixedSize(10, 10)
        self.cookie_status_dot.setStyleSheet(f"background: {PLATFORM_STATUS_LOGGED_OUT}; border-radius: 5px;")
        status_row_layout.addWidget(self.cookie_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cookie_status_text_label = BodyLabel("未登录")
        self.cookie_status_text_label.setStyleSheet(f"color: {PLATFORM_STATUS_LOGGED_OUT}; font-weight: 400;")
        status_row_layout.addWidget(self.cookie_status_text_label, 0, Qt.AlignmentFlag.AlignVCenter)
        status_row_layout.addStretch(1)
        cookie_layout.addWidget(status_row)

        cookie_button_row = QHBoxLayout()
        cookie_button_row.setContentsMargins(0, 0, 0, 0)
        cookie_button_row.setSpacing(8)
        self.refresh_cookie_button = PushButton(FIF.SYNC, "刷新状态")
        self.logout_cookie_button = PushButton("退出登录")
        self.refresh_cookie_button.clicked.connect(self._handle_refresh_cookie_clicked)
        self.logout_cookie_button.clicked.connect(self._handle_logout_cookie_clicked)
        cookie_button_row.addStretch(1)
        cookie_button_row.addWidget(self.refresh_cookie_button, 0)
        cookie_button_row.addWidget(self.logout_cookie_button, 0)
        cookie_button_row.addStretch(1)
        cookie_layout.addLayout(cookie_button_row)
        return panel

    def _build_youtube_account_panel(self, parent: QWidget) -> QWidget:
        panel = QWidget(parent)
        panel.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        title = BodyLabel("通过 Firefox 浏览器导入")
        title.setStyleSheet("color: #111827; font-weight: 700;")
        layout.addWidget(title, 0, Qt.AlignmentFlag.AlignLeft)

        steps = QWidget(panel)
        steps.setStyleSheet("background: transparent; border: 0;")
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(0, 0, 0, 0)
        steps_layout.setSpacing(5)
        for text in ("1. 在 Firefox 中登录 youtube.com", "2. 点击下方按钮导入 Cookie"):
            step_label = CaptionLabel(text)
            step_label.setStyleSheet("color: #64748b;")
            steps_layout.addWidget(step_label)
        layout.addWidget(steps)

        inline_status_row = QWidget(panel)
        inline_status_row.setStyleSheet("background: transparent; border: 0;")
        inline_status_layout = QHBoxLayout(inline_status_row)
        inline_status_layout.setContentsMargins(0, 0, 0, 0)
        inline_status_layout.setSpacing(8)
        inline_status_layout.addStretch(1)
        self.youtube_status_dot = QFrame(inline_status_row)
        self.youtube_status_dot.setFixedSize(10, 10)
        self.youtube_status_dot.setStyleSheet(f"background: {PLATFORM_STATUS_LOGGED_OUT}; border-radius: 5px;")
        self.youtube_status_text_label = BodyLabel("未登录")
        self.youtube_status_text_label.setStyleSheet(f"color: {PLATFORM_STATUS_LOGGED_OUT}; font-weight: 400;")
        inline_status_layout.addWidget(self.youtube_status_dot, 0, Qt.AlignmentFlag.AlignVCenter)
        inline_status_layout.addWidget(self.youtube_status_text_label, 0, Qt.AlignmentFlag.AlignVCenter)
        inline_status_layout.addStretch(1)
        layout.addWidget(inline_status_row)

        self.import_youtube_cookie_button = PushButton(FIF.DOWNLOAD, "从 Firefox 导入 Cookie")
        self.import_youtube_cookie_button.setToolTip(
            "Chrome / Edge 等 Chromium 内核浏览器无法导出可用的 YouTube Cookie，目前仅支持 Firefox。"
        )
        self.import_youtube_cookie_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.import_youtube_cookie_button.clicked.connect(self._handle_import_youtube_cookie_clicked)
        layout.addWidget(self.import_youtube_cookie_button)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.refresh_youtube_cookie_button = PushButton(FIF.SYNC, "刷新状态")
        self.logout_youtube_cookie_button = PushButton("退出登录")
        self.refresh_youtube_cookie_button.clicked.connect(self._refresh_youtube_cookie_status)
        self.logout_youtube_cookie_button.clicked.connect(self._handle_logout_youtube_cookie_clicked)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_youtube_cookie_button, 0)
        button_row.addWidget(self.logout_youtube_cookie_button, 0)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return panel

    def _build_account_status_card(self, parent: QWidget) -> QWidget:
        card = PanelCard(parent, padding=(16, 16, 16, 16), spacing=10)
        layout = card.create_vbox()
        layout.addWidget(self._create_panel_title("账号状态"))

        status_box = QFrame(card)
        status_box.setObjectName("AccountStatusBox")
        status_box.setStyleSheet(
            """
            QFrame#AccountStatusBox {
                background: #ffffff;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
            }
            QFrame[accountRow="true"] {
                background: transparent;
                border: 0;
            }
            """
        )
        status_layout = QVBoxLayout(status_box)
        status_layout.setContentsMargins(0, 0, 0, 0)
        status_layout.setSpacing(0)
        self.bilibili_status_row = self._create_account_status_row("Bilibili", SOURCE_BILIBILI)
        divider = QFrame(status_box)
        divider.setFixedHeight(1)
        divider.setStyleSheet("background: #e5e7eb; border: 0;")
        self.youtube_status_row = self._create_account_status_row("YouTube", SOURCE_YOUTUBE)
        status_layout.addWidget(self.bilibili_status_row)
        status_layout.addWidget(divider)
        status_layout.addWidget(self.youtube_status_row)
        layout.addWidget(status_box)
        return card

    def _create_account_status_row(self, title: str, platform: str) -> ClickableFrame:
        row = ClickableFrame()
        row.setProperty("accountRow", True)
        row.setFixedHeight(46)
        row.clicked.connect(lambda platform=platform: self._switch_account_platform(platform))
        layout = QHBoxLayout(row)
        layout.setContentsMargins(10, 0, 10, 0)
        layout.setSpacing(8)

        icon = MiniAvatarLabel("B" if platform == SOURCE_BILIBILI else "▶", "#38bdf8" if platform == SOURCE_BILIBILI else "#ef4444")
        name_label = BodyLabel(title)
        name_label.setStyleSheet("color: #111827;")
        dot = QFrame(row)
        dot.setFixedSize(10, 10)
        dot.setStyleSheet(f"background: {PLATFORM_STATUS_LOGGED_OUT}; border-radius: 5px;")
        status_label = BodyLabel("未登录")
        status_label.setStyleSheet(f"color: {PLATFORM_STATUS_LOGGED_OUT}; font-weight: 400;")

        layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(name_label, 1, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(status_label, 0, Qt.AlignmentFlag.AlignVCenter)

        row.platform_icon = icon  # type: ignore[attr-defined]
        row.status_dot = dot  # type: ignore[attr-defined]
        row.status_label = status_label  # type: ignore[attr-defined]
        return row

    def _install_single_click_combo_behavior(self, combo: ComboBox) -> None:
        popup_view = getattr(combo, "view", None)
        if not callable(popup_view):
            return
        view = popup_view()
        if view is None:
            return
        view.pressed.connect(lambda index, combo=combo: self._handle_combo_popup_pressed(combo, index.row()))

    def _handle_combo_popup_pressed(self, combo: ComboBox, row: int) -> None:
        if row < 0 or row >= combo.count():
            return
        combo.setCurrentIndex(row)
        hide_popup = getattr(combo, "hidePopup", None)
        if callable(hide_popup):
            hide_popup()

    def _create_panel_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("panelTitle", True)
        return label

    def _create_section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setProperty("sectionTitle", True)
        return label

    def _set_combo_text_or_default(self, combo: ComboBox, value: str, fallback: str) -> None:
        target = value if value in [combo.itemText(index) for index in range(combo.count())] else fallback
        combo.setCurrentText(target)

    def _combo_int_value(self, combo: ComboBox, fallback: int) -> int:
        try:
            return int(combo.currentText() or fallback)
        except (TypeError, ValueError):
            return fallback

    def _settings_int_value(self, name: str, fallback: int) -> int:
        try:
            return int(getattr(self.settings, name, fallback) or fallback)
        except (TypeError, ValueError):
            return fallback

    def _load_settings(self) -> None:
        self._set_source(getattr(self.settings, "video_download_source", SOURCE_YOUTUBE))

    def _set_source(self, source: str) -> None:
        self.settings.video_download_source = source
        self._persist_settings(save=False)

    def _switch_account_platform(self, platform: str) -> None:
        if not hasattr(self, "account_stack"):
            return
        index = 1 if platform == SOURCE_YOUTUBE else 0
        self.account_stack.setCurrentIndex(index)
        for segment, selected in (
            (self.bilibili_segment, platform == SOURCE_BILIBILI),
            (self.youtube_segment, platform == SOURCE_YOUTUBE),
        ):
            segment.setStyleSheet(SEGMENT_STYLE_SELECTED if selected else SEGMENT_STYLE_NORMAL)
            color = SEGMENT_TITLE_COLOR_SELECTED if selected else SEGMENT_TITLE_COLOR_NORMAL
            weight = 600 if selected else 400
            title_label = getattr(segment, "title_label", None)
            if title_label is not None:
                title_label.setStyleSheet(
                    f"background: transparent; border: 0; color: {color}; font-weight: {weight};"
                )

    def _set_platform_dot(self, widget: QWidget, color: str) -> None:
        widget.setStyleSheet(f"background: {color}; border-radius: 5px;")

    def _refresh_account_status_rows(self) -> None:
        if hasattr(self, "bilibili_segment"):
            bili_logged_in = self._bilibili_profile is not None
            bili_color = PLATFORM_STATUS_LOGGED_IN if bili_logged_in else PLATFORM_STATUS_LOGGED_OUT
            self._set_platform_dot(self.bilibili_segment.status_dot, bili_color)  # type: ignore[attr-defined]
            self._set_platform_dot(self.bilibili_status_row.status_dot, bili_color)  # type: ignore[attr-defined]
            self.bilibili_status_row.status_label.setText(  # type: ignore[attr-defined]
                "已登录" if self._bilibili_profile else "未登录"
            )
            self.bilibili_status_row.status_label.setStyleSheet(  # type: ignore[attr-defined]
                f"color: {bili_color}; font-weight: 400;"
            )
            self.bilibili_status_row.platform_icon.set_avatar(  # type: ignore[attr-defined]
                self._bilibili_profile.avatar_bytes if self._bilibili_profile else b"",
                self._bilibili_profile.nickname if self._bilibili_profile else "B",
                "#38bdf8",
            )

        if hasattr(self, "youtube_segment"):
            yt_logged_in = self._youtube_profile is not None
            yt_color = PLATFORM_STATUS_LOGGED_IN if yt_logged_in else PLATFORM_STATUS_LOGGED_OUT
            self._set_platform_dot(self.youtube_segment.status_dot, yt_color)  # type: ignore[attr-defined]
            self._set_platform_dot(self.youtube_status_row.status_dot, yt_color)  # type: ignore[attr-defined]
            self.youtube_status_row.status_label.setText(  # type: ignore[attr-defined]
                "已登录" if self._youtube_profile else "未登录"
            )
            self.youtube_status_row.status_label.setStyleSheet(  # type: ignore[attr-defined]
                f"color: {yt_color}; font-weight: 400;"
            )

    def _set_cookie_status_display(self, text: str, color: str) -> None:
        self.cookie_status_text_label.setText(text)
        self.cookie_status_text_label.setStyleSheet(f"color: {color}; font-weight: 400;")
        self.cookie_status_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
        self._refresh_account_status_rows()

    def _apply_account_profile(self, profile: BilibiliAccountProfile | None) -> None:
        if profile is None:
            self._bilibili_profile = None
            self.account_profile_widget.hide()
            self.qr_wrapper.show()
            self.logout_cookie_button.setEnabled(False)
            self._refresh_account_status_rows()
            return

        self._bilibili_profile = profile
        self.account_avatar_label.set_avatar(profile.avatar_bytes, profile.nickname)
        self.account_name_label.setText(profile.nickname or "Bilibili 用户")
        self.account_profile_widget.show()
        self.qr_wrapper.hide()
        self.logout_cookie_button.setEnabled(True)
        self._refresh_account_status_rows()

    def _clear_account_profile(self) -> None:
        self._bilibili_profile = None
        self.account_avatar_label.set_avatar(b"", "B")
        self.account_name_label.setText("Bilibili 用户")
        self.account_profile_widget.hide()
        self.qr_wrapper.show()
        self.logout_cookie_button.setEnabled(False)
        self._refresh_account_status_rows()

    def _handle_refresh_cookie_clicked(self) -> None:
        self._refresh_cookie_status()
        if not self.cookie_manager.check_login_status():
            self._ensure_qr_login(force_restart=True)

    def _handle_logout_cookie_clicked(self) -> None:
        if self._qr_login_worker is not None and self._qr_login_worker.isRunning():
            self._qr_login_worker.stop()
            self._qr_login_worker.wait(2000)
        self.cookie_manager.clear_cookie()
        self._recent_bilibili_login_deadline = 0.0
        self._clear_account_profile()
        self._set_cookie_status_display("未登录", PLATFORM_STATUS_LOGGED_OUT)
        self.qr_placeholder.set_message("已退出登录，正在生成新的二维码…")
        self.parse_status_label.setText("已退出 Bilibili 登录，并清空本地 Cookie。")
        self._ensure_qr_login(force_restart=True)

    def _refresh_youtube_cookie_status(self) -> None:
        self._youtube_profile = self.cookie_manager.get_profile(SOURCE_YOUTUBE)
        logged_in = self._youtube_profile is not None
        color = PLATFORM_STATUS_LOGGED_IN if logged_in else PLATFORM_STATUS_LOGGED_OUT
        text = self._youtube_profile.nickname if self._youtube_profile else "未登录"
        if hasattr(self, "youtube_status_text_label"):
            self.youtube_status_text_label.setText(text)
            self.youtube_status_text_label.setStyleSheet(f"color: {color}; font-weight: 400;")
            self.youtube_status_dot.setStyleSheet(f"background: {color}; border-radius: 5px;")
            self.logout_youtube_cookie_button.setEnabled(logged_in)
        self._refresh_account_status_rows()

    def _handle_import_youtube_cookie_clicked(self) -> None:
        if self._cookie_import_worker is not None and self._cookie_import_worker.isRunning():
            self.parse_status_label.setText("正在导入 YouTube Cookie，请稍候。")
            return
        browser = "Firefox"
        self.import_youtube_cookie_button.setEnabled(False)
        self.youtube_status_text_label.setText("正在导入…")
        self.youtube_status_text_label.setStyleSheet(f"color: {PLATFORM_STATUS_PENDING}; font-weight: 400;")
        self.youtube_status_dot.setStyleSheet(f"background: {PLATFORM_STATUS_PENDING}; border-radius: 5px;")
        self._cookie_import_worker = CookieImportWorker(self.cookie_manager, SOURCE_YOUTUBE, browser, self)
        self._cookie_import_worker.importSucceeded.connect(self._handle_youtube_cookie_import_succeeded)
        self._cookie_import_worker.importFailed.connect(self._handle_youtube_cookie_import_failed)
        self._cookie_import_worker.finished.connect(self._handle_cookie_import_worker_finished)
        self._cookie_import_worker.start()

    def _handle_youtube_cookie_import_succeeded(self, cookie_path: str) -> None:
        self.parse_status_label.setText(f"YouTube Cookie 已导入到 {cookie_path}。")
        self._refresh_youtube_cookie_status()

    def _handle_youtube_cookie_import_failed(self, message: str) -> None:
        self.parse_status_label.setText(f"YouTube Cookie 导入失败：{message}")
        self._refresh_youtube_cookie_status()

    def _handle_cookie_import_worker_finished(self) -> None:
        self.import_youtube_cookie_button.setEnabled(True)
        self._cookie_import_worker = None

    def _handle_logout_youtube_cookie_clicked(self) -> None:
        self.cookie_manager.clear(SOURCE_YOUTUBE)
        self.parse_status_label.setText("已退出 YouTube 登录，并清空本地 Cookie。")
        self._refresh_youtube_cookie_status()

    def _ensure_qr_login(self, force_restart: bool = False) -> None:
        profile = self.cookie_manager.get_account_profile()
        if profile is not None:
            self._apply_account_profile(profile)
            self.qr_placeholder.set_message("当前已登录，如需重新扫码可点击刷新状态")
            return

        self._clear_account_profile()
        if self._qr_login_worker is not None and self._qr_login_worker.isRunning():
            if not force_restart:
                return
            self._qr_login_worker.stop()
            self._qr_login_worker.wait(2000)

        self.qr_placeholder.set_message("正在生成二维码…")
        self._qr_login_worker = BilibiliQrLoginWorker(self.cookie_manager, self)
        self._qr_login_worker.qrReady.connect(self._handle_qr_ready)
        self._qr_login_worker.loginStatusChanged.connect(self._handle_qr_status_changed)
        self._qr_login_worker.loginSucceeded.connect(self._handle_qr_login_succeeded)
        self._qr_login_worker.loginFailed.connect(self._handle_qr_login_failed)
        self._qr_login_worker.finished.connect(self._handle_qr_worker_finished)
        self._qr_login_worker.start()

    def _handle_qr_ready(self, image_bytes: bytes, login_url: str) -> None:
        self.qr_placeholder.set_qr_image(image_bytes, login_url)

    def _handle_qr_status_changed(self, status_payload: object) -> None:
        if isinstance(status_payload, dict):
            raw_code = status_payload.get("code")
            try:
                status_code = int(raw_code) if raw_code not in (None, "") else -1
            except (TypeError, ValueError):
                status_code = -1
            message = str(status_payload.get("message") or "")
        else:
            status_code = -1
            message = str(status_payload or "")

        if self._bilibili_profile is not None and status_code in (86090, 86101):
            return
        if status_code == 86090 or "确认" in message:
            self._set_cookie_status_display("等待确认", PLATFORM_STATUS_PENDING)
            self.qr_placeholder.set_message("已扫码，请在手机上确认登录")
            return
        if status_code == 86038 or "过期" in message:
            self._set_cookie_status_display("二维码已过期", PLATFORM_STATUS_PENDING)
            self.qr_placeholder.set_message("二维码已过期，点击刷新状态重新生成")
            return
        if status_code == 0 or "成功" in message:
            self._set_cookie_status_display("已登录", PLATFORM_STATUS_LOGGED_IN)
            self.qr_placeholder.set_message("登录成功，正在同步账号信息…")
            return
        if status_code == 86101:
            self._set_cookie_status_display("待扫码", PLATFORM_STATUS_LOGGED_OUT)
            return
        if message:
            self._set_cookie_status_display(f"状态码 {status_code}", PLATFORM_STATUS_PENDING)
            self.qr_placeholder.set_message(message)
            return
        self._set_cookie_status_display("未登录", PLATFORM_STATUS_LOGGED_OUT)

    def _handle_qr_login_succeeded(self, cookie_path: str) -> None:
        self._recent_bilibili_login_deadline = time.monotonic() + 10.0
        self._set_cookie_status_display("已登录", PLATFORM_STATUS_LOGGED_IN)
        self.qr_placeholder.set_message("登录成功，正在同步账号信息…")
        self.parse_status_label.setText(f"扫码登录成功，Cookie 已保存到 {cookie_path or self.cookie_manager.resolved_cookie_path()}。")
        self._refresh_cookie_status_with_retry(remaining=6)

    def _handle_qr_login_failed(self, message: str) -> None:
        self._clear_account_profile()
        self.qr_placeholder.set_message("二维码加载失败，点击二维码可在浏览器中打开登录页")
        self.parse_status_label.setText(f"Bilibili 扫码登录失败：{message}")

    def _handle_qr_worker_finished(self) -> None:
        self._qr_login_worker = None
        if (
            self._bilibili_profile is not None
            or self.cookie_manager.has_cookie()
            or time.monotonic() < self._recent_bilibili_login_deadline
        ):
            self._refresh_cookie_status()

    def _refresh_cookie_status(self) -> None:
        profile = self.cookie_manager.get_account_profile()
        if profile is not None:
            self._apply_account_profile(profile)
            self._set_cookie_status_display("已登录", PLATFORM_STATUS_LOGGED_IN)
            self._recent_bilibili_login_deadline = 0.0
            return

        if self.cookie_manager.has_cookie() and time.monotonic() < self._recent_bilibili_login_deadline:
            self._clear_account_profile()
            self.qr_placeholder.set_message("登录成功，正在同步账号信息…")
            self._set_cookie_status_display("已登录", PLATFORM_STATUS_LOGGED_IN)
            return

        self._clear_account_profile()
        if self.cookie_manager.has_cookie():
            self._set_cookie_status_display("Cookie 无效", PLATFORM_STATUS_PENDING)
            return
        self._set_cookie_status_display("未登录", PLATFORM_STATUS_LOGGED_OUT)

    def _refresh_cookie_status_with_retry(self, remaining: int) -> None:
        self._refresh_cookie_status()
        if remaining <= 0:
            return
        if self.cookie_manager.get_account_profile() is not None:
            return
        QTimer.singleShot(1000, lambda: self._refresh_cookie_status_with_retry(remaining - 1))

    def _choose_save_dir_for(self, target: LineEdit) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择保存路径", target.text().strip() or str(Path.home()))
        if not directory:
            return
        target.setText(directory)

    def _current_ytdlp_version_text(self) -> str:
        try:
            return YtDlpService().get_ytdlp_version()
        except Exception as exc:  # noqa: BLE001
            return f"未检测到：{exc}"

    def _safe_set_widget_enabled(self, widget: QWidget, enabled: bool) -> None:
        try:
            widget.setEnabled(enabled)
        except RuntimeError:
            pass

    def _safe_set_label_text(self, label: QLabel, text: str) -> None:
        try:
            label.setText(text)
        except RuntimeError:
            pass

    def _start_ytdlp_update(self, version_label: QLabel, update_button: PushButton) -> None:
        if self._ytdlp_update_worker is not None and self._ytdlp_update_worker.isRunning():
            self.parse_status_label.setText("yt-dlp 正在更新，请稍候。")
            return

        update_button.setEnabled(False)
        version_label.setText("正在更新 yt-dlp…")
        self.parse_status_label.setText("正在更新 yt-dlp，请稍候。")
        worker = YtDlpUpdateWorker(self)
        self._ytdlp_update_worker = worker
        worker.updateSucceeded.connect(
            lambda version, output: self._handle_ytdlp_update_succeeded(version_label, version, output)
        )
        worker.updateFailed.connect(lambda message: self._handle_ytdlp_update_failed(version_label, message))
        worker.finished.connect(lambda: self._safe_set_widget_enabled(update_button, True))
        worker.finished.connect(self._handle_ytdlp_update_worker_finished)
        worker.start()

    def _handle_ytdlp_update_succeeded(self, version_label: QLabel, version_text: str, output: str) -> None:
        self._safe_set_label_text(version_label, version_text)
        self.parse_status_label.setText("yt-dlp 更新完成。")
        if output:
            self.parse_status_label.setToolTip(output)

    def _handle_ytdlp_update_failed(self, version_label: QLabel, message: str) -> None:
        self._safe_set_label_text(version_label, self._current_ytdlp_version_text())
        self.parse_status_label.setText(f"yt-dlp 更新失败：{message}")

    def _handle_ytdlp_update_worker_finished(self) -> None:
        self._ytdlp_update_worker = None

    def _sync_naming_rule_edit_visibility(self, naming_rule_combo: ComboBox, custom_template_edit: LineEdit) -> None:
        custom_template_edit.setVisible(naming_rule_combo.currentText() == NAMING_RULE_CUSTOM)

    def _open_download_settings_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("下载设置")
        dialog.resize(560, 430)

        shell = QVBoxLayout(dialog)
        shell.setContentsMargins(16, 16, 16, 16)
        shell.setSpacing(12)

        form_card = PanelCard(dialog, padding=(16, 16, 16, 16), spacing=12)
        form_layout = form_card.create_vbox()
        form_layout.addWidget(CaptionLabel("保存路径"))

        save_path_row = QHBoxLayout()
        save_path_row.setContentsMargins(0, 0, 0, 0)
        save_path_row.setSpacing(8)
        save_dir_edit = LineEdit()
        save_dir_edit.setText(getattr(self.settings, "video_download_save_dir", "") or str(Path.home() / "Downloads"))
        browse_button = ToolButton(FIF.FOLDER)
        browse_button.setFixedSize(38, 38)
        browse_button.clicked.connect(lambda: self._choose_save_dir_for(save_dir_edit))
        save_path_row.addWidget(save_dir_edit, 1)
        save_path_row.addWidget(browse_button, 0)
        form_layout.addLayout(save_path_row)

        form_layout.addWidget(self._create_section_title("并发下载"))
        concurrent_combo = StyledComboBox()
        concurrent_combo.setMinimumHeight(40)
        concurrent_combo.addItems(CONCURRENT_COUNT_OPTIONS)
        self._set_combo_text_or_default(
            concurrent_combo,
            str(self._settings_int_value("video_download_concurrent_count", 3)),
            "3",
        )
        self._install_single_click_combo_behavior(concurrent_combo)
        form_layout.addWidget(concurrent_combo)

        form_layout.addWidget(self._create_section_title("网络设置"))
        form_layout.addWidget(CaptionLabel("超时时间（秒）"))
        timeout_combo = StyledComboBox()
        timeout_combo.setMinimumHeight(40)
        timeout_combo.addItems(TIMEOUT_OPTIONS)
        self._set_combo_text_or_default(timeout_combo, str(self._settings_int_value("video_download_timeout", 5)), "5")
        self._install_single_click_combo_behavior(timeout_combo)
        form_layout.addWidget(timeout_combo)

        form_layout.addWidget(CaptionLabel("重试次数"))
        retry_combo = StyledComboBox()
        retry_combo.setMinimumHeight(40)
        retry_combo.addItems(RETRY_COUNT_OPTIONS)
        self._set_combo_text_or_default(retry_combo, str(self._settings_int_value("video_download_retry_count", 3)), "3")
        self._install_single_click_combo_behavior(retry_combo)
        form_layout.addWidget(retry_combo)

        form_layout.addWidget(self._create_section_title("yt-dlp"))
        ytdlp_row = QHBoxLayout()
        ytdlp_row.setContentsMargins(0, 0, 0, 0)
        ytdlp_row.setSpacing(8)
        ytdlp_version_label = CaptionLabel(self._current_ytdlp_version_text())
        ytdlp_version_label.setStyleSheet("color: #64748b;")
        ytdlp_update_button = PushButton(FIF.UPDATE, "更新 yt-dlp")
        ytdlp_update_button.clicked.connect(lambda: self._start_ytdlp_update(ytdlp_version_label, ytdlp_update_button))
        ytdlp_row.addWidget(ytdlp_version_label, 1, Qt.AlignmentFlag.AlignVCenter)
        ytdlp_row.addWidget(ytdlp_update_button, 0)
        form_layout.addLayout(ytdlp_row)

        shell.addWidget(form_card, 1)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)
        button_row.addStretch(1)
        cancel_button = PushButton("取消")
        save_button = PrimaryPushButton("保存")
        cancel_button.clicked.connect(dialog.reject)

        def save_settings_from_dialog() -> None:
            self.settings.video_download_save_dir = save_dir_edit.text().strip() or str(Path.home() / "Downloads")
            self.settings.video_download_concurrent_count = self._combo_int_value(concurrent_combo, 3)
            self.settings.video_download_timeout = self._combo_int_value(timeout_combo, 5)
            self.settings.video_download_retry_count = self._combo_int_value(retry_combo, 3)
            self._persist_settings()
            dialog.accept()

        save_button.clicked.connect(save_settings_from_dialog)
        button_row.addWidget(cancel_button, 0)
        button_row.addWidget(save_button, 0)
        shell.addLayout(button_row)

        dialog.exec()

    def _persist_settings(self, *args, save: bool = True) -> None:
        del args
        if not hasattr(self.settings, "video_download_merge_video_audio"):
            self.settings.video_download_merge_video_audio = True
        if not hasattr(self.settings, "video_download_download_thumbnail"):
            self.settings.video_download_download_thumbnail = False
        self.settings.video_download_download_subtitle = False
        if not getattr(self.settings, "video_download_save_dir", ""):
            self.settings.video_download_save_dir = str(Path.home() / "Downloads")
        if not getattr(self.settings, "video_download_naming_rule", ""):
            self.settings.video_download_naming_rule = NAMING_RULE_TITLE
        if not getattr(self.settings, "video_download_custom_template", ""):
            self.settings.video_download_custom_template = DEFAULT_CUSTOM_TEMPLATE
        self.settings.video_download_concurrent_count = self._settings_int_value("video_download_concurrent_count", 3)
        self.settings.video_download_timeout = self._settings_int_value("video_download_timeout", 5)
        self.settings.video_download_retry_count = self._settings_int_value("video_download_retry_count", 3)
        self.settings.video_download_cookie_path = ""
        if save:
            self._save_settings()
            self.settingsChanged.emit()

    def _start_parse(self) -> None:
        if self._parse_worker is not None and self._parse_worker.isRunning():
            self.parse_status_label.setText("正在解析，请稍候。")
            return

        urls = [line.strip() for line in self.link_input.toPlainText().splitlines() if line.strip()]
        if not urls:
            self.parse_status_label.setText("请输入至少一个视频链接。")
            return

        cookie_files_by_source = {
            SOURCE_BILIBILI: self.cookie_manager.get_cookie_path(SOURCE_BILIBILI)
            or str(self.cookie_manager.resolved_cookie_path(SOURCE_BILIBILI)),
            SOURCE_YOUTUBE: self.cookie_manager.get_cookie_path(SOURCE_YOUTUBE)
            or str(self.cookie_manager.resolved_cookie_path(SOURCE_YOUTUBE)),
        }
        self.parse_button.setEnabled(False)
        self.parse_status_label.setText(f"正在解析 {len(urls)} 个链接…")
        self._parse_worker = ParseLinksWorker(urls, cookie_files_by_source, self)
        self._parse_worker.batchFinished.connect(self._handle_parse_finished)
        self._parse_worker.finished.connect(self._handle_parse_worker_finished)
        self._parse_worker.start()

    def _handle_parse_finished(self, batch: ParsedBatch) -> None:
        parsed_count = 0
        first_new_task_id = ""
        existing_count = 0
        first_existing_task_id = ""
        skipped_count = 0
        selected_infos = self._select_infos_from_parsed_batch(batch)
        for info in selected_infos:
            existing = self._find_existing_task_for_info(info)
            if existing is not None:
                if classify_existing_task(existing) == "update":
                    previous_option_id = existing.selected_format.option_id if existing.selected_format else ""
                    existing.url = info.url
                    existing.info = info
                    existing.title = info.title
                    existing.source = info.source
                    existing.available_formats = list(info.formats)
                    existing.selected_format = self._find_matching_format(existing.available_formats, previous_option_id)
                    if existing.selected_format is None:
                        existing.selected_format = self._select_default_format(info.formats)
                    existing.filesize = self._preferred_task_filesize(existing)
                existing_count += 1
                if not first_existing_task_id:
                    first_existing_task_id = existing.task_id
                continue

            task = self._create_download_task(info)
            self._tasks.append(task)
            self._task_index[task.task_id] = task
            parsed_count += 1
            if not first_new_task_id:
                first_new_task_id = task.task_id

        if first_new_task_id:
            self._current_task_id = first_new_task_id
        elif first_existing_task_id:
            self._current_task_id = first_existing_task_id
        elif selected_infos and not self._current_task_id:
            self._current_task_id = self._tasks[0].task_id

        if selected_infos:
            first_source = selected_infos[0].source
            if first_source in (SOURCE_YOUTUBE, SOURCE_BILIBILI):
                self._set_source(first_source)

        parts: list[str] = []
        if parsed_count:
            parts.append(f"成功解析 {parsed_count} 个链接")
        if existing_count:
            parts.append(f"{existing_count} 个视频已存在，未重复添加")
        skipped_count = len(batch.infos) - len(selected_infos)
        if skipped_count:
            parts.append(f"{skipped_count} 个分 P 未添加")
        if batch.errors:
            parts.append(f"{len(batch.errors)} 个链接失败")
        self.parse_status_label.setText("，".join(parts) if parts else "没有可用的解析结果。")
        if batch.errors:
            self.parse_status_label.setToolTip("\n".join(batch.errors))
        else:
            self.parse_status_label.setToolTip("")
        self._refresh_preview()
        self._refresh_download_table()

    def _select_infos_from_parsed_batch(self, batch: ParsedBatch) -> list[VideoInfo]:
        groups = batch.groups or [ParsedVideoGroup(source_url="", infos=[info]) for info in batch.infos]
        selected: list[VideoInfo] = []
        for group in groups:
            if len(group.infos) > 1 and group.infos[0].source == SOURCE_BILIBILI:
                chosen = self._choose_bilibili_parts(group.infos)
                selected.extend(chosen)
                continue
            selected.extend(group.infos)
        return selected

    def _choose_bilibili_parts(self, infos: list[VideoInfo]) -> list[VideoInfo]:
        dialog = QDialog(self)
        dialog.setWindowTitle("选择要下载的分 P")
        dialog.resize(620, 420)

        shell = QVBoxLayout(dialog)
        shell.setContentsMargins(16, 16, 16, 16)
        shell.setSpacing(12)
        shell.addWidget(self._create_section_title("选择要添加到下载列表的分 P"))

        hint = CaptionLabel("检测到这是一个 Bilibili 多分 P 视频。勾选需要下载的分 P 后再添加到下载列表。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b;")
        shell.addWidget(hint)

        scroll_area = QScrollArea(dialog)
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: transparent; border: 1px solid #e5e7eb; border-radius: 8px; }")
        content = QWidget(scroll_area)
        content.setStyleSheet("background: transparent; border: 0;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(10, 10, 10, 10)
        content_layout.setSpacing(8)

        checkboxes: list[CheckBox] = []
        for index, info in enumerate(infos, start=1):
            checkbox = CheckBox(f"P{index}. {info.title or '未命名视频'}")
            checkbox.setChecked(index == 1)
            checkbox.setToolTip(info.webpage_url or info.url)
            content_layout.addWidget(checkbox)
            checkboxes.append(checkbox)
        content_layout.addStretch(1)
        scroll_area.setWidget(content)
        shell.addWidget(scroll_area, 1)

        tool_row = QHBoxLayout()
        tool_row.setContentsMargins(0, 0, 0, 0)
        select_all_button = PushButton("全选")
        clear_button = PushButton("全不选")
        select_all_button.clicked.connect(lambda: [checkbox.setChecked(True) for checkbox in checkboxes])
        clear_button.clicked.connect(lambda: [checkbox.setChecked(False) for checkbox in checkboxes])
        tool_row.addWidget(select_all_button)
        tool_row.addWidget(clear_button)
        tool_row.addStretch(1)
        shell.addLayout(tool_row)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.addStretch(1)
        cancel_button = PushButton("取消")
        add_button = PrimaryPushButton("添加选中分 P")
        cancel_button.clicked.connect(dialog.reject)
        add_button.clicked.connect(dialog.accept)
        button_row.addWidget(cancel_button)
        button_row.addWidget(add_button)
        shell.addLayout(button_row)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return []
        return [info for info, checkbox in zip(infos, checkboxes, strict=False) if checkbox.isChecked()]

    def _handle_parse_worker_finished(self) -> None:
        self.parse_button.setEnabled(True)
        self._parse_worker = None

    def _select_default_format(self, formats: list[FormatOption]) -> FormatOption | None:
        return select_default_format(formats)

    def _find_matching_format(self, formats: list[FormatOption], option_id: str) -> FormatOption | None:
        return find_matching_format(formats, option_id)

    def _find_existing_task_for_info(self, info: VideoInfo) -> DownloadTask | None:
        return find_existing_task_for_info(self._tasks, info)

    def _video_identity_keys(self, *urls: str) -> set[str]:
        return video_identity_keys(*urls)

    def _preferred_task_filesize(self, task: DownloadTask) -> int | None:
        if task.selected_format and task.selected_format.filesize:
            return task.selected_format.filesize
        if task.info and task.info.filesize:
            return task.info.filesize
        return task.filesize

    def _create_download_task(self, info: VideoInfo, selected_option_id: str = "") -> DownloadTask:
        available_formats = list(info.formats)
        selected_format = self._find_matching_format(available_formats, selected_option_id)
        if selected_format is None:
            selected_format = self._select_default_format(available_formats)
        naming_rule = getattr(self.settings, "video_download_naming_rule", NAMING_RULE_TITLE) or NAMING_RULE_TITLE
        if naming_rule not in (NAMING_RULE_TITLE, NAMING_RULE_TITLE_UPLOADER, NAMING_RULE_CUSTOM):
            naming_rule = NAMING_RULE_TITLE
        task = DownloadTask(
            task_id=uuid.uuid4().hex,
            url=info.url,
            title=info.title,
            source=info.source,
            selected_format=selected_format,
            filesize=info.filesize,
            info=info,
            available_formats=available_formats,
            naming_rule=naming_rule,
            custom_template=(
                getattr(self.settings, "video_download_custom_template", DEFAULT_CUSTOM_TEMPLATE)
                or DEFAULT_CUSTOM_TEMPLATE
            ),
            merge_video_audio=bool(getattr(self.settings, "video_download_merge_video_audio", True)),
            download_thumbnail=False,
        )
        task.filesize = self._preferred_task_filesize(task)
        return task

    def _duplicate_completed_task_for_format(self, task: DownloadTask, option: FormatOption) -> DownloadTask | None:
        if task.info is None:
            return None
        duplicated = self._create_download_task(task.info, selected_option_id=option.option_id)
        duplicated.source = task.source
        duplicated.title = task.title
        duplicated.settings_confirmed = True
        self._tasks.append(duplicated)
        self._task_index[duplicated.task_id] = duplicated
        self._current_task_id = duplicated.task_id
        return duplicated

    def _current_task_row(self) -> int:
        if not self._current_task_id:
            return -1
        for row, task in enumerate(self._tasks):
            if task.task_id == self._current_task_id:
                return row
        return -1

    def _task_status_color(self, task: DownloadTask) -> str:
        if task.status == TASK_STATUS_FAILED or task.selected_format is None:
            return PLATFORM_STATUS_LOGGED_OUT
        if task.status in (TASK_STATUS_DOWNLOADING, TASK_STATUS_COMPLETED) or task.settings_confirmed:
            return PLATFORM_STATUS_LOGGED_IN
        return "#94a3b8"

    def _status_dot_icon(self, color: str) -> QIcon:
        pixmap = QPixmap(12, 12)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawEllipse(1, 1, 10, 10)
        painter.end()
        return QIcon(pixmap)

    def _refresh_task_switcher(self) -> None:
        if not hasattr(self, "task_switch_combo"):
            return
        self._selection_syncing = True
        self.task_switch_combo.clear()
        for row, task in enumerate(self._tasks):
            title = task.title or "未命名视频"
            display_text = self._task_switcher_text(row, title)
            self.task_switch_combo.addItem(
                display_text,
                self._status_dot_icon(self._task_status_color(task)),
            )
        current_row = self._current_task_row()
        if current_row >= 0:
            self.task_switch_combo.setCurrentIndex(current_row)
        self.task_total_label.setText(f"/ {len(self._tasks) if self._tasks else 0}")
        self.prev_task_button.setEnabled(current_row > 0)
        self.next_task_button.setEnabled(0 <= current_row < len(self._tasks) - 1)
        self._selection_syncing = False

    def _task_switcher_text(self, row: int, title: str) -> str:
        prefix = f"第 {row + 1} 个："
        elided_title = self.task_switch_combo.fontMetrics().elidedText(
            title,
            Qt.TextElideMode.ElideRight,
            TASK_SWITCH_TITLE_PIXELS,
        )
        return f"{prefix}{elided_title}"

    def _move_task_selection(self, delta: int) -> None:
        current_row = self._current_task_row()
        target_row = current_row + delta
        if target_row < 0 or target_row >= len(self._tasks):
            return
        self.download_table.selectRow(target_row)

    def _handle_task_switch_combo_changed(self, index: int) -> None:
        if self._selection_syncing or index < 0 or index >= len(self._tasks):
            return
        self.download_table.selectRow(index)

    def _sync_per_video_controls(self, task: DownloadTask | None) -> None:
        if not hasattr(self, "naming_rule_combo"):
            return
        self._per_video_controls_updating = True
        if task is None:
            self._set_combo_text_or_default(self.naming_rule_combo, NAMING_RULE_TITLE, NAMING_RULE_TITLE)
            self.custom_template_edit.setText(DEFAULT_CUSTOM_TEMPLATE)
            self.per_video_merge_checkbox.setChecked(True)
            self.per_video_thumbnail_checkbox.setChecked(False)
        else:
            self._set_combo_text_or_default(self.naming_rule_combo, task.naming_rule or NAMING_RULE_TITLE, NAMING_RULE_TITLE)
            self.custom_template_edit.setText(task.custom_template or DEFAULT_CUSTOM_TEMPLATE)
            self.per_video_merge_checkbox.setChecked(bool(task.merge_video_audio))
            self.per_video_thumbnail_checkbox.setChecked(bool(task.download_thumbnail))
        self._sync_custom_template_visibility()
        self._per_video_controls_updating = False

    def _sync_custom_template_visibility(self) -> None:
        is_custom = self.naming_rule_combo.currentText() == NAMING_RULE_CUSTOM
        self.custom_template_edit.setVisible(is_custom)

    def _handle_per_video_settings_changed(self, *args) -> None:
        del args
        if self._per_video_controls_updating:
            return
        task = self._current_task()
        if task is None:
            return
        task.naming_rule = self.naming_rule_combo.currentText() or NAMING_RULE_TITLE
        task.custom_template = self.custom_template_edit.text().strip() or DEFAULT_CUSTOM_TEMPLATE
        task.merge_video_audio = self.per_video_merge_checkbox.isChecked()
        task.download_thumbnail = self.per_video_thumbnail_checkbox.isChecked()
        task.settings_confirmed = True
        self._sync_custom_template_visibility()
        self._refresh_task_switcher()

    def _refresh_preview(self) -> None:
        task = self._current_task()
        if task is None or task.info is None:
            self.video_details_stack.setCurrentIndex(0)
            self.delete_current_task_button.setEnabled(False)
            self.thumbnail_label.setText("暂无视频信息")
            self.thumbnail_label.setPixmap(QPixmap())
            for label in self.info_value_labels.values():
                label.setText("-")
            self._format_options = []
            self._refresh_format_table()
            self._sync_per_video_controls(None)
            self._refresh_task_switcher()
            return

        self.video_details_stack.setCurrentIndex(1)
        self.delete_current_task_button.setEnabled(True)
        info = task.info
        if info.thumbnail_bytes:
            pixmap = QPixmap()
            pixmap.loadFromData(info.thumbnail_bytes)
            self.thumbnail_label.setPixmap(
                pixmap.scaled(
                    self.thumbnail_label.size(),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            self.thumbnail_label.setText("")
        else:
            self.thumbnail_label.setPixmap(QPixmap())
            self.thumbnail_label.setText("暂无封面")

        selected_format = task.selected_format
        resolution = selected_format.resolution if selected_format else (f"{info.height}p" if info.height else "-")
        size_text = format_bytes(self._preferred_task_filesize(task))
        self._set_info_value_text("title", info.title or "-")
        self._set_info_value_text("uploader", info.uploader or "-")
        self.info_value_labels["duration"].setText(format_duration(info.duration))
        self.info_value_labels["resolution"].setText(resolution)
        self.info_value_labels["filesize"].setText(size_text)

        self._format_options = list(task.available_formats)
        self._refresh_format_table()
        self._sync_per_video_controls(task)
        self._refresh_task_switcher()

    def _set_info_value_text(self, key: str, text: str) -> None:
        label = self.info_value_labels[key]
        label.setToolTip(text)
        label.setText(label.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, max(80, label.width())))

    def _refresh_format_table(self) -> None:
        self._format_table_updating = True
        self.format_table.setRowCount(0)
        task = self._current_task()
        if task is None or not self._format_options:
            self.format_hint_label.setText("请先解析视频链接。")
            self._format_table_updating = False
            return

        self.format_table.setRowCount(len(self._format_options))
        for row, option in enumerate(self._format_options):
            choose_item = QTableWidgetItem("推荐" if option.is_recommended else "")
            choose_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
            )
            choose_item.setCheckState(
                Qt.CheckState.Checked
                if task.selected_format and task.selected_format.option_id == option.option_id
                else Qt.CheckState.Unchecked
            )
            self.format_table.setItem(row, 0, choose_item)
            self.format_table.setItem(row, 1, QTableWidgetItem(option.format_label))
            self.format_table.setItem(row, 2, QTableWidgetItem(option.resolution))
            self.format_table.setItem(row, 3, QTableWidgetItem(option.video_codec))
            self.format_table.setItem(row, 4, QTableWidgetItem(option.audio_codec))
            self.format_table.setItem(row, 5, QTableWidgetItem(format_bytes(option.filesize)))
        self.format_hint_label.setText("解析完成后可在这里切换当前任务的下载格式。")
        self._format_table_updating = False

    def _handle_format_cell_clicked(self, row: int, column: int) -> None:
        if column != 0:
            return
        item = self.format_table.item(row, 0)
        if item is None:
            return
        item.setCheckState(Qt.CheckState.Checked)

    def _handle_format_item_changed(self, item: QTableWidgetItem) -> None:
        if self._format_table_updating or item.column() != 0 or item.checkState() != Qt.CheckState.Checked:
            return

        task = self._current_task()
        if task is None:
            return

        self._format_table_updating = True
        for row in range(self.format_table.rowCount()):
            row_item = self.format_table.item(row, 0)
            if row_item is None or row_item is item:
                continue
            row_item.setCheckState(Qt.CheckState.Unchecked)
        self._format_table_updating = False

        option = self._format_options[item.row()]
        task.selected_format = option
        task.filesize = option.filesize or (task.info.filesize if task.info else None)
        self._refresh_preview()
        self._refresh_download_table()

    def _refresh_format_table(self) -> None:
        self._format_table_updating = True
        self.format_table.setRowCount(0)
        self.format_combo.clear()

        task = self._current_task()
        if task is None or not self._format_options:
            self._set_format_summary(None)
            self.format_hint_label.setText("请先解析视频链接。")
            self._format_table_updating = False
            return

        self.format_table.setRowCount(len(self._format_options))
        selected_index = 0
        for row, option in enumerate(self._format_options):
            choose_item = QTableWidgetItem("推荐" if option.is_recommended else "")
            choose_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsUserCheckable
            )
            is_selected = bool(task.selected_format and task.selected_format.option_id == option.option_id)
            choose_item.setCheckState(Qt.CheckState.Checked if is_selected else Qt.CheckState.Unchecked)
            self.format_table.setItem(row, 0, choose_item)
            self.format_table.setItem(row, 1, QTableWidgetItem(option.format_label))
            self.format_table.setItem(row, 2, QTableWidgetItem(option.resolution))
            self.format_table.setItem(row, 3, QTableWidgetItem(option.video_codec))
            self.format_table.setItem(row, 4, QTableWidgetItem(option.audio_codec))
            self.format_table.setItem(row, 5, QTableWidgetItem(format_bytes(option.filesize)))
            self.format_combo.addItem(self._format_option_text(option))
            if is_selected:
                selected_index = row

        self.format_combo.setCurrentIndex(selected_index)
        self._set_format_summary(self._format_options[selected_index])
        self.format_hint_label.setText("解析完成后可在这里切换当前任务的下载格式。")
        self._format_table_updating = False

    def _handle_format_cell_clicked(self, row: int, column: int) -> None:
        if column != 0 or row < 0 or row >= len(self._format_options):
            return
        self.format_combo.setCurrentIndex(row)

    def _handle_format_item_changed(self, item: QTableWidgetItem) -> None:
        if self._format_table_updating or item.column() != 0 or item.checkState() != Qt.CheckState.Checked:
            return
        self.format_combo.setCurrentIndex(item.row())

    def _handle_format_combo_changed(self, index: int) -> None:
        if self._format_table_updating or index < 0 or index >= len(self._format_options):
            return

        task = self._current_task()
        if task is None:
            return

        option = self._format_options[index]
        if task.selected_format and task.selected_format.option_id == option.option_id:
            return
        if task.status == TASK_STATUS_COMPLETED:
            duplicated = self._duplicate_completed_task_for_format(task, option)
            if duplicated is not None:
                self.parse_status_label.setText(f"已基于 {task.title} 新建一个下载任务，可继续选择清晰度后开始下载。")
                self._refresh_preview()
                self._refresh_download_table()
            return

        self._format_table_updating = True
        for row in range(len(self._format_options)):
            row_item = self.format_table.item(row, 0)
            if row_item is None:
                continue
            row_item.setCheckState(Qt.CheckState.Checked if row == index else Qt.CheckState.Unchecked)
        self._format_table_updating = False

        task.selected_format = option
        task.filesize = self._preferred_task_filesize(task)
        task.settings_confirmed = True
        self._set_format_summary(option)
        self._refresh_preview()
        self._refresh_download_table()

    def _format_option_text(self, option: FormatOption) -> str:
        codecs = " / ".join(part for part in (option.video_codec, option.audio_codec) if part and part != "-")
        parts = [option.format_label or "默认格式", option.resolution or "-"]
        if codecs:
            parts.append(codecs)
        size_text = format_bytes(option.filesize)
        if size_text != "-":
            parts.append(size_text)
        text = " | ".join(parts)
        return f"推荐 | {text}" if option.is_recommended else text

    def _set_format_summary(self, option: FormatOption | None) -> None:
        if option is None:
            for label in self.format_value_labels.values():
                label.setText("-")
            return

        format_text = option.format_label or "-"
        if option.is_recommended:
            format_text = f"推荐 | {format_text}"
        self.format_value_labels["format"].setText(format_text)
        self.format_value_labels["resolution"].setText(option.resolution or "-")
        self.format_value_labels["video_codec"].setText(option.video_codec or "-")
        self.format_value_labels["audio_codec"].setText(option.audio_codec or "-")
        self.format_value_labels["filesize"].setText(format_bytes(option.filesize))

    def _refresh_download_table(self) -> None:
        self.download_table.setRowCount(len(self._tasks))
        self.download_hint_label.setText("暂无下载任务。" if not self._tasks else f"共 {len(self._tasks)} 个任务。")

        for row, task in enumerate(self._tasks):
            resolution = task.selected_format.resolution if task.selected_format else "-"
            if task.status == TASK_STATUS_COMPLETED:
                progress_text = "100%"
            elif task.progress_merge_active:
                progress_text = "合并中"
            else:
                progress_text = f"{task.progress:.0f}%"

            status_item = QTableWidgetItem(task.status)
            status_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            if task.error_message:
                status_item.setToolTip(task.error_message)

            title_text = task.title or "-"
            title_item = QTableWidgetItem(title_text)
            title_item.setTextAlignment(int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
            title_item.setToolTip(title_text)

            source_item = QTableWidgetItem(task.source)
            source_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))

            resolution_item = QTableWidgetItem(resolution)
            resolution_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            resolution_item.setToolTip(resolution)

            progress_item = QTableWidgetItem(progress_text)
            progress_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))
            progress_tooltip = " / ".join(part for part in (task.progress_phase_name, task.speed_text) if part)
            if progress_tooltip:
                progress_item.setToolTip(progress_tooltip)

            size_item = QTableWidgetItem(format_bytes(task.filesize))
            size_item.setTextAlignment(int(Qt.AlignmentFlag.AlignCenter))

            self.download_table.setItem(row, 0, status_item)
            self.download_table.setItem(row, 1, title_item)
            self.download_table.setItem(row, 2, source_item)
            self.download_table.setItem(row, 3, resolution_item)
            self.download_table.setItem(row, 4, progress_item)
            self.download_table.setItem(row, 5, size_item)
            self.download_table.setCellWidget(row, 6, self._build_task_action_button(task))
            self.download_table.setRowHeight(row, 44)

        if self._current_task_id:
            for row, task in enumerate(self._tasks):
                if task.task_id == self._current_task_id:
                    self._selection_syncing = True
                    self.download_table.selectRow(row)
                    self._selection_syncing = False
                    break
        self._refresh_download_actions()
        self._refresh_task_switcher()

    def _refresh_download_actions(self) -> None:
        has_tasks = bool(self._tasks)
        self.start_all_button.setEnabled(has_tasks)
        self.pause_all_button.setEnabled(False)
        self.cancel_all_button.setEnabled(has_tasks)
        self.open_folder_button.setEnabled(has_tasks)
        self.clear_list_button.setEnabled(has_tasks)

    def _build_task_action_button(self, task: DownloadTask) -> QWidget:
        container = QWidget()
        container.setStyleSheet("background: transparent; border: 0;")
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(6)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        button = PushButton("取消" if task.status in (TASK_STATUS_WAITING, TASK_STATUS_DOWNLOADING) else "重试")
        button.setProperty("compact", True)
        button.setFixedSize(64, 30)
        if task.status == TASK_STATUS_COMPLETED:
            button.setText("打开")
            button.clicked.connect(lambda: self._open_task_file(task.task_id))
        elif task.status in (TASK_STATUS_WAITING, TASK_STATUS_DOWNLOADING):
            button.clicked.connect(lambda: self._cancel_task(task.task_id))
        else:
            button.clicked.connect(lambda: self._retry_task(task.task_id))
        layout.addWidget(button, 0, Qt.AlignmentFlag.AlignCenter)

        delete_button = _create_delete_button("从列表删除此视频")
        delete_button.clicked.connect(lambda: self._delete_task(task.task_id))
        layout.addWidget(delete_button, 0, Qt.AlignmentFlag.AlignCenter)
        return container

    def _reset_task_progress_tracking(self, task: DownloadTask) -> None:
        task.progress = 0.0
        task.speed_text = ""
        task.downloaded_bytes = 0
        task.filesize = self._preferred_task_filesize(task)
        requires_merge = bool(task.selected_format and task.selected_format.requires_merge)
        download_phase_count = 2 if requires_merge else 1
        task.progress_total_phases = download_phase_count + (1 if requires_merge else 0)
        task.progress_phase_index = 0
        task.progress_phase_bytes = 0
        task.progress_phase_name = "正在下载视频…" if requires_merge else ""
        task.progress_phase_source_name = ""
        task.progress_phase_totals = [0] * download_phase_count
        task.progress_phase_downloaded = [0] * download_phase_count
        task.progress_merge_active = False
        task.speed_samples.clear()

    def _update_task_download_phase(self, task: DownloadTask, payload: dict) -> None:
        self._ensure_progress_phase_storage(task)
        status = str(payload.get("status") or "")
        if status == "merging":
            self._enter_merge_phase(task)
            return

        filename = str(payload.get("filename") or "")
        downloaded_bytes = int(payload.get("downloaded_bytes") or 0)
        total_bytes = int(payload.get("total_bytes") or payload.get("total_bytes_estimate") or 0)
        fragment_index = int(payload.get("fragment_index") or 0)
        fragment_count = int(payload.get("fragment_count") or 0)
        download_phase_count = len(task.progress_phase_downloaded)
        phase_index = min(task.progress_phase_index, download_phase_count - 1)

        if self._should_advance_download_phase(task, filename, downloaded_bytes):
            self._lock_current_download_phase(task)
            task.progress_phase_index = min(task.progress_phase_index + 1, download_phase_count - 1)
            task.progress_phase_bytes = 0
            task.progress_phase_source_name = filename
            self._set_download_phase_label(task)
            phase_index = task.progress_phase_index
        elif filename and not task.progress_phase_source_name:
            task.progress_phase_source_name = filename

        if total_bytes > 0:
            task.progress_phase_totals[phase_index] = max(task.progress_phase_totals[phase_index], total_bytes)
        elif fragment_count > 0:
            task.progress_phase_totals[phase_index] = max(task.progress_phase_totals[phase_index], fragment_count)

        if downloaded_bytes > 0:
            task.progress_phase_downloaded[phase_index] = max(
                task.progress_phase_downloaded[phase_index],
                downloaded_bytes,
            )
        elif fragment_count > 0 and fragment_index > 0:
            task.progress_phase_downloaded[phase_index] = max(
                task.progress_phase_downloaded[phase_index],
                fragment_index,
            )

        if status == "finished":
            self._lock_current_download_phase(task)

        task.progress_phase_bytes = task.progress_phase_downloaded[phase_index]

    def _ensure_progress_phase_storage(self, task: DownloadTask) -> None:
        requires_merge = task.progress_total_phases > 1
        expected_download_phases = 2 if requires_merge else 1
        if len(task.progress_phase_totals) != expected_download_phases:
            task.progress_phase_totals = [0] * expected_download_phases
        if len(task.progress_phase_downloaded) != expected_download_phases:
            task.progress_phase_downloaded = [0] * expected_download_phases

    def _should_advance_download_phase(self, task: DownloadTask, filename: str, downloaded_bytes: int) -> bool:
        download_phase_count = len(task.progress_phase_downloaded)
        if task.progress_phase_index >= download_phase_count - 1:
            return False
        if filename and task.progress_phase_source_name and filename != task.progress_phase_source_name:
            return True

        previous_downloaded = task.progress_phase_downloaded[task.progress_phase_index]
        previous_total = task.progress_phase_totals[task.progress_phase_index]
        if previous_downloaded <= 0 or downloaded_bytes <= 0:
            return False
        if downloaded_bytes >= max(1, int(previous_downloaded * 0.25)):
            return False
        if previous_total > 0:
            return previous_downloaded >= int(previous_total * 0.8)
        return previous_downloaded > 10 * 1024 * 1024

    def _lock_current_download_phase(self, task: DownloadTask) -> None:
        if not task.progress_phase_downloaded:
            return
        phase_index = min(task.progress_phase_index, len(task.progress_phase_downloaded) - 1)
        phase_total = max(task.progress_phase_totals[phase_index], task.progress_phase_downloaded[phase_index])
        task.progress_phase_totals[phase_index] = phase_total
        task.progress_phase_downloaded[phase_index] = phase_total

    def _set_download_phase_label(self, task: DownloadTask) -> None:
        if task.progress_total_phases <= 1:
            task.progress_phase_name = ""
        elif task.progress_phase_index == 0:
            task.progress_phase_name = "正在下载视频…"
        else:
            task.progress_phase_name = "正在下载音频…"

    def _enter_merge_phase(self, task: DownloadTask) -> None:
        for index in range(len(task.progress_phase_downloaded)):
            phase_total = max(task.progress_phase_totals[index], task.progress_phase_downloaded[index])
            task.progress_phase_totals[index] = phase_total
            task.progress_phase_downloaded[index] = phase_total
        task.progress_merge_active = True
        task.progress_phase_index = max(0, task.progress_total_phases - 1)
        task.progress_phase_bytes = 0
        task.progress_phase_name = "正在合并…"

    def _compose_task_progress(self, task: DownloadTask) -> float:
        self._ensure_progress_phase_storage(task)
        download_total = self._estimated_download_total(task)
        if download_total <= 0:
            return max(0.0, min(99.0, task.progress))
        merge_weight = self._merge_progress_weight(task, download_total)
        cumulative_downloaded = self._cumulative_downloaded_bytes(task)
        if task.progress_merge_active:
            cumulative_downloaded = download_total
        overall_progress = cumulative_downloaded * 100 / (download_total + merge_weight)
        return max(0.0, min(99.0, overall_progress))

    def _estimated_download_total(self, task: DownloadTask) -> int:
        totals = task.progress_phase_totals
        downloaded = task.progress_phase_downloaded
        known_total = sum(total for total in totals if total > 0)
        unknown_indexes = [index for index, total in enumerate(totals) if total <= 0]
        if not unknown_indexes:
            return known_total

        selected_total = task.filesize or (task.selected_format.filesize if task.selected_format else 0) or 0
        remaining_from_selected = max(0, selected_total - known_total)
        if remaining_from_selected > 0:
            return known_total + remaining_from_selected

        first_basis = totals[0] if totals and totals[0] > 0 else (downloaded[0] if downloaded else 0)
        future_estimate = max(1, int(first_basis * 0.3)) if first_basis > 0 else 0
        return known_total + future_estimate * len(unknown_indexes)

    def _merge_progress_weight(self, task: DownloadTask, download_total: int) -> int:
        if task.progress_total_phases <= len(task.progress_phase_totals):
            return 0
        return max(1, int(download_total * 0.05))

    def _cumulative_downloaded_bytes(self, task: DownloadTask) -> int:
        if not task.progress_phase_downloaded:
            return task.downloaded_bytes
        current_index = min(task.progress_phase_index, len(task.progress_phase_downloaded) - 1)
        completed = sum(task.progress_phase_totals[:current_index])
        return completed + task.progress_phase_downloaded[current_index]

    def _update_task_speed_text(self, task: DownloadTask, payload: dict) -> None:
        if task.progress_merge_active:
            task.speed_text = "正在合并…"
            return
        now = time.monotonic()
        cumulative_bytes = self._cumulative_downloaded_bytes(task)
        task.speed_samples.append((now, cumulative_bytes))
        while task.speed_samples and now - task.speed_samples[0][0] > 8:
            task.speed_samples.popleft()
        if len(task.speed_samples) >= 2:
            first_time, first_bytes = task.speed_samples[0]
            elapsed = now - first_time
            if elapsed > 0 and cumulative_bytes >= first_bytes:
                task.speed_text = format_speed((cumulative_bytes - first_bytes) / elapsed)
                return
        task.speed_text = format_speed(payload.get("speed"))

    def _handle_task_selection_changed(self) -> None:
        if self._selection_syncing:
            return
        rows = sorted({index.row() for index in self.download_table.selectedIndexes()})
        if not rows:
            return
        row = rows[0]
        if row >= len(self._tasks):
            return
        self._current_task_id = self._tasks[row].task_id
        self._refresh_preview()

    def _build_download_options(self, task: DownloadTask | None = None) -> DownloadOptions:
        self._persist_settings()
        source = task.source if task is not None else SOURCE_BILIBILI
        cookie_file = self.cookie_manager.get_cookie_path(source) or str(self.cookie_manager.resolved_cookie_path(source))
        naming_rule = (task.naming_rule if task is not None else None) or NAMING_RULE_TITLE
        if naming_rule not in (NAMING_RULE_TITLE, NAMING_RULE_TITLE_UPLOADER, NAMING_RULE_CUSTOM):
            naming_rule = NAMING_RULE_TITLE
        return DownloadOptions(
            save_dir=getattr(self.settings, "video_download_save_dir", "") or str(Path.home() / "Downloads"),
            naming_rule=naming_rule,
            custom_template=(task.custom_template if task is not None else None) or DEFAULT_CUSTOM_TEMPLATE,
            merge_video_audio=bool(task.merge_video_audio if task is not None else True),
            download_thumbnail=bool(task.download_thumbnail if task is not None else False),
            download_subtitle=False,
            concurrent_count=self._settings_int_value("video_download_concurrent_count", 3),
            timeout=self._settings_int_value("video_download_timeout", 5),
            retry_count=self._settings_int_value("video_download_retry_count", 3),
            cookie_file=cookie_file,
        )

    def _start_all_downloads(self) -> None:
        if not self._tasks:
            self.parse_status_label.setText("请先解析视频链接。")
            return

        if not (getattr(self.settings, "video_download_save_dir", "") or "").strip():
            self.parse_status_label.setText("请先选择保存路径。")
            return

        for task in self._tasks:
            if task.status in (TASK_STATUS_FAILED, TASK_STATUS_CANCELLED):
                task.status = TASK_STATUS_WAITING
                self._reset_task_progress_tracking(task)
                task.error_message = ""

        self._start_pending_downloads()
        self._refresh_download_table()

    def _start_pending_downloads(self) -> None:
        concurrent_count = self._settings_int_value("video_download_concurrent_count", 3)
        while len(self._running_workers) < concurrent_count:
            task = next((item for item in self._tasks if item.status == TASK_STATUS_WAITING), None)
            if task is None:
                break
            if task.selected_format is None and task.available_formats:
                task.selected_format = task.available_formats[0]
            if task.selected_format is None:
                task.status = TASK_STATUS_FAILED
                task.error_message = "没有可用格式，请重新解析。"
                continue

            task.cancel_requested = False
            task.status = TASK_STATUS_DOWNLOADING
            self._reset_task_progress_tracking(task)
            options = self._build_download_options(task)
            worker = DownloadWorker(task, options, self)
            worker.progressChanged.connect(self._handle_download_progress)
            worker.taskSucceeded.connect(self._handle_download_success)
            worker.taskFailed.connect(self._handle_download_failed)
            worker.taskCancelled.connect(self._handle_download_cancelled)
            worker.finished.connect(lambda task_id=task.task_id: self._cleanup_worker(task_id))
            self._running_workers[task.task_id] = worker
            worker.start()

        self._refresh_download_table()

    def _handle_download_progress(self, task_id: str, payload: dict) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        self._update_task_download_phase(task, payload)
        task.downloaded_bytes = self._cumulative_downloaded_bytes(task)
        preferred_filesize = self._preferred_task_filesize(task)
        learned_total = self._estimated_download_total(task)
        if preferred_filesize is None and learned_total > 0:
            task.filesize = learned_total
        task.progress = max(task.progress, self._compose_task_progress(task))
        self._update_task_speed_text(task, payload)
        self._refresh_download_table()

    def _handle_download_success(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        task.status = TASK_STATUS_COMPLETED
        task.progress = 100.0
        task.speed_text = ""
        task.speed_samples.clear()
        if task.local_file and task.local_file.exists():
            try:
                task.filesize = task.local_file.stat().st_size
            except OSError:
                task.filesize = self._preferred_task_filesize(task)
        self.parse_status_label.setText(f"下载完成：{task.title}")
        self._refresh_download_table()
        self._start_pending_downloads()

    def _handle_download_failed(self, task_id: str, message: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        task.status = TASK_STATUS_FAILED
        task.speed_text = ""
        task.speed_samples.clear()
        task.error_message = message
        self.parse_status_label.setText(f"下载失败：{task.title}，{message}")
        self._refresh_download_table()
        self._start_pending_downloads()

    def _handle_download_cancelled(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        task.status = TASK_STATUS_CANCELLED
        task.speed_text = ""
        task.speed_samples.clear()
        task.progress = 0.0
        self.parse_status_label.setText(f"已取消：{task.title}")
        self._refresh_download_table()
        self._start_pending_downloads()

    def _cleanup_worker(self, task_id: str) -> None:
        worker = self._running_workers.pop(task_id, None)
        if worker is not None:
            worker.deleteLater()

    def _cancel_task(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        task.cancel_requested = True
        if task.status in (TASK_STATUS_WAITING, TASK_STATUS_DOWNLOADING):
            task.status = TASK_STATUS_CANCELLED
            task.speed_text = ""
            task.speed_samples.clear()
            self.parse_status_label.setText(f"已取消：{task.title}")
            self._refresh_download_table()

    def _delete_current_task(self) -> None:
        task = self._current_task()
        if task is None:
            return
        self._delete_task(task.task_id)

    def _delete_task(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        if task.status == TASK_STATUS_DOWNLOADING:
            result = QMessageBox.question(
                self,
                "删除下载任务",
                "该视频正在下载，删除会取消下载并清理临时文件。确定要删除吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if result != QMessageBox.StandardButton.Yes:
                return
            task.cancel_requested = True

        row = next((index for index, item in enumerate(self._tasks) if item.task_id == task_id), -1)
        if row < 0:
            return

        was_current = self._current_task_id == task_id
        self._tasks.pop(row)
        self._task_index.pop(task_id, None)
        if was_current:
            if self._tasks:
                next_row = min(row, len(self._tasks) - 1)
                self._current_task_id = self._tasks[next_row].task_id
            else:
                self._current_task_id = ""
        self.parse_status_label.setText(f"已从下载列表删除：{task.title}")
        self._refresh_preview()
        self._refresh_download_table()

    def _retry_task(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        task.cancel_requested = False
        task.status = TASK_STATUS_WAITING
        self._reset_task_progress_tracking(task)
        task.error_message = ""
        self._start_pending_downloads()

    def _cancel_all_downloads(self) -> None:
        for task in self._tasks:
            task.cancel_requested = True
            if task.status == TASK_STATUS_WAITING:
                task.status = TASK_STATUS_CANCELLED
        self._refresh_download_table()
        self.parse_status_label.setText("已发送取消请求。")

    def _open_save_folder(self) -> None:
        open_in_explorer(Path(getattr(self.settings, "video_download_save_dir", "") or str(Path.home() / "Downloads")))

    def _open_task_file(self, task_id: str) -> None:
        task = self._task_index.get(task_id)
        if task is None:
            return
        if task.local_file and task.local_file.exists():
            open_in_explorer(task.local_file.parent)
            return
        self._open_save_folder()

    def _clear_task_list(self) -> None:
        if self._running_workers:
            QMessageBox.information(self, "视频下载", "当前还有下载任务在进行中，请先取消。")
            return
        self._tasks.clear()
        self._task_index.clear()
        self._current_task_id = ""
        self._refresh_preview()
        self._refresh_download_table()
        self.parse_status_label.setText("已清空下载列表。")

    def _current_task(self) -> DownloadTask | None:
        return self._task_index.get(self._current_task_id)
