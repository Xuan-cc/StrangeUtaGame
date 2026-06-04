from __future__ import annotations

import ctypes
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from string import Formatter
from typing import Callable

DWMWA_WINDOW_CORNER_PREFERENCE = 33
DWMWCP_DONOTROUND = 1

os.environ["QFLUENT_WIDGETS_NO_PROMOTION"] = "1"

from PyQt6.QtCore import QEvent, QSize, QThread, QTimer, Qt, pyqtSignal as Signal
from PyQt6.QtGui import QColor, QBrush, QFont, QFontMetrics, QIcon, QKeySequence, QPainter, QPen, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QButtonGroup,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGraphicsDropShadowEffect,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QStyleOptionViewItem,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox as QCheckBox,
    ComboBox as QComboBox,
    FluentIcon as FIF,
    LineEdit as QLineEdit,
    PlainTextEdit as QPlainTextEdit,
    PrimaryPushButton,
    ProgressBar as QProgressBar,
    PushButton as QPushButton,
    RadioButton as QRadioButton,
    setTheme,
    setThemeColor,
    Slider as QSlider,
    StrongBodyLabel,
    TableWidget as QTableWidget,
    Theme,
    ToolButton,
    qconfig,
)
from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu
from qfluentwidgets.components.widgets.menu import MenuAnimationType
from qfluentwidgets.components.widgets.table_view import TableItemDelegate

from krok_helper.audio_alignment import (
    DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE,
    DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE,
    ENCODE_MODE_HARDWARE,
    ENCODE_MODE_SOFTWARE,
    LEAD_FILL_BLACK,
    LEAD_FILL_FREEZE,
    LEAD_FILL_WHITE,
    AlignmentPreviewProcess,
    AutoAlignResult,
    WaveformData,
    build_alignment_preview_command,
    export_aligned_audio,
    export_aligned_video,
    estimate_waveform_alignment,
    extract_waveform,
    format_offset,
    start_alignment_preview,
)
from krok_helper.config import (
    APP_NAME,
    APP_TITLE,
    WINDOW_HEIGHT,
    WINDOW_MIN_HEIGHT,
    WINDOW_MIN_WIDTH,
    WINDOW_WIDTH,
)
from krok_helper.errors import ExportCancelled, ProcessingError
from krok_helper.ffmpeg import _build_subprocess_kwargs, find_tool, probe_media, terminate_process
from krok_helper.lyrics import (
    DEFAULT_LYRICS_SEARCH_LIMIT,
    DEFAULT_LYRICS_PROVIDER_IDS,
    LYRICS_LANGUAGE_ORIGINAL,
    LYRICS_LANGUAGE_TRANSLATION,
    LYRICS_PREVIEW_LINE,
    LYRICS_PREVIEW_VERBATIM,
    LyricsPreview,
    LyricsSearchBatch,
    LyricsSearchCandidate,
    LyricsSearchService,
    build_lyrics_preview,
)
from krok_helper.pipeline import (
    DEFAULT_OFF_NAME_TEMPLATE,
    DEFAULT_ON_NAME_TEMPLATE,
    OUTPUT_NAME_MODE_FIXED,
    OUTPUT_NAME_MODE_TEMPLATE,
    OUTPUT_NAME_MODE_VIDEO_NAME,
    resolve_output_dir,
    resolve_output_paths,
    run_pipeline,
    validate_output_name_template,
)
from krok_helper.settings import load_app_settings, save_app_settings
from krok_helper.video_download import VideoDownloadPage
from krok_helper.windows import set_explicit_app_user_model_id


ALIGN_TARGET_VIDEO = "video"
ALIGN_TARGET_AUDIO = "audio"
VIDEO_EXTENSIONS = {".mkv", ".mp4", ".mov", ".avi"}
AUDIO_EXTENSIONS = {".flac", ".wav", ".mp3", ".m4a", ".aac", ".ape", ".alac", ".mkv"}
HIRES_AUDIO_EXTENSIONS = AUDIO_EXTENSIONS | {".mp4"}
ALIGN_AUDIO_EXTENSIONS = AUDIO_EXTENSIONS | {".mp4"}
WINDOWS_INVALID_FILENAME_CHARS = '<>:"/\\|?*'
ALIGNMENT_TEMPLATE_FORMATTER = Formatter()
FFMPEG_DIR_PLACEHOLDER = "未设置，将优先使用系统 PATH 中的 ffmpeg"
WORKFLOW_VIDEO_DOWNLOAD = "video_download"
WORKFLOW_WAVEFORM_ALIGN = "waveform_align"
WORKFLOW_LYRICS_SEARCH = "lyrics_search"
WORKFLOW_LYRICS_TIMING = "lyrics_timing"
WORKFLOW_SUBTITLE_RENDER = "subtitle_render"
WORKFLOW_HIRES_MIX = "hires_mix"
LYRICS_SOURCE_OPTIONS = [
    ("聚合", DEFAULT_LYRICS_PROVIDER_IDS),
    ("QQ音乐", ("qm",)),
    ("酷狗音乐", ("kg",)),
    ("网易云音乐", ("ne",)),
    ("LRCLIB", ("lrclib",)),
]
LYRICS_SOURCE_MAP = {label: provider_ids for label, provider_ids in LYRICS_SOURCE_OPTIONS}
LYRICS_PREVIEW_MODE_OPTIONS = [
    ("按行 LRC", LYRICS_PREVIEW_LINE),
    ("按字 LRC", LYRICS_PREVIEW_VERBATIM),
]
LYRICS_PREVIEW_MODE_MAP = {label: mode for label, mode in LYRICS_PREVIEW_MODE_OPTIONS}
LYRICS_LANGUAGE_OPTIONS = [
    ("原文", LYRICS_LANGUAGE_ORIGINAL),
    ("中文译文", LYRICS_LANGUAGE_TRANSLATION),
]
LYRICS_LANGUAGE_MAP = {label: value for label, value in LYRICS_LANGUAGE_OPTIONS}

APP_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo" / "logo.jpg"
TASKBAR_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "logo" / "logo2.png"
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
DEFAULT_UI_FONT_FAMILIES = [
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
    "Yu Gothic UI",
    "Meiryo UI",
    "Meiryo",
    "PingFang SC",
]


def open_in_explorer(path: Path) -> None:
    subprocess.Popen(["explorer", str(path)])


def load_app_icon() -> QIcon | None:
    if not APP_LOGO_PATH.exists():
        return None
    icon = QIcon(str(APP_LOGO_PATH))
    return None if icon.isNull() else icon


def load_taskbar_icon() -> QIcon | None:
    if TASKBAR_LOGO_PATH.exists():
        icon = QIcon(str(TASKBAR_LOGO_PATH))
        if not icon.isNull():
            return icon
    return load_app_icon()


def apply_safe_label_metrics(
    label: QLabel,
    font: QFont,
    *,
    top_padding: int = 3,
    bottom_padding: int = 2,
) -> None:
    margins = label.contentsMargins()
    label.setContentsMargins(margins.left(), top_padding, margins.right(), bottom_padding)
    label.setMinimumHeight(QFontMetrics(font).height() + top_padding + bottom_padding)


def apply_card_shadow(widget: QWidget, *, alpha: int = 20) -> None:
    shadow = QGraphicsDropShadowEffect(widget)
    shadow.setBlurRadius(12)
    shadow.setXOffset(0)
    shadow.setYOffset(2)
    shadow.setColor(QColor(16, 24, 40, alpha))
    widget.setGraphicsEffect(shadow)


def build_app_ui_font(*, point_size: float = 10.5, bold: bool = False) -> QFont:
    font = QFont()
    font.setFamilies(DEFAULT_UI_FONT_FAMILIES)
    font.setPointSizeF(point_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferDefault)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    if bold:
        font.setBold(True)
    return font


def build_lyrics_ui_font(*, point_size: float = 10.5, bold: bool = False) -> QFont:
    return build_app_ui_font(point_size=point_size, bold=bold)


def sync_fluent_ui_fonts() -> None:
    qconfig.set(qconfig.fontFamilies, DEFAULT_UI_FONT_FAMILIES, save=False)


def format_media_duration(seconds: float | None) -> str:
    if seconds is None or seconds <= 0:
        return "时长未知"

    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    if minutes:
        return f"{minutes}:{remainder:06.3f}"
    return f"{seconds:.3f}s"


class WhiteComboBoxMenu(ComboBoxMenu):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
        # 保留 qfluentwidgets 默认的透明顶层窗口，不要关闭 WA_TranslucentBackground
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


class StyledComboBox(QComboBox):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

    def _createComboMenu(self):
        return WhiteComboBoxMenu(self)


class CardWidget(QFrame):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        radius: int = 10,
        padding: tuple[int, int, int, int] = (14, 14, 14, 14),
        spacing: int = 12,
    ) -> None:
        super().__init__(parent)
        self.setProperty("cardWidget", True)
        self.setProperty("cardRadius", radius)
        self._default_padding = padding
        self._default_spacing = spacing

    def createVBoxLayout(self) -> QVBoxLayout:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*self._default_padding)
        layout.setSpacing(self._default_spacing)
        return layout

    def createHBoxLayout(self) -> QHBoxLayout:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(*self._default_padding)
        layout.setSpacing(self._default_spacing)
        return layout

    def createGridLayout(self) -> QGridLayout:
        layout = QGridLayout(self)
        layout.setContentsMargins(*self._default_padding)
        layout.setHorizontalSpacing(self._default_spacing)
        layout.setVerticalSpacing(self._default_spacing)
        return layout


class LyricsResultsDelegate(TableItemDelegate):
    def __init__(self, parent) -> None:
        super().__init__(parent)
        self.margin = 1
        self.setCheckedColor("#D85C6C", "#D85C6C")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:  # noqa: D401
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)

        is_selected = index.row() in self.selectedRows
        is_hovered = self.hoverRow == index.row() or bool(option.state & QStyle.StateFlag.State_MouseOver)
        if is_selected:
            painter.save()
            painter.fillRect(option.rect, QColor("#FFF6F7"))
            if index.column() == 0:
                accent_rect = option.rect.adjusted(0, 6, -(option.rect.width() - 3), -6)
                painter.fillRect(accent_rect, QColor("#D85C6C"))
            painter.restore()
            opt.state &= ~QStyle.StateFlag.State_Selected
        elif is_hovered:
            painter.save()
            painter.fillRect(option.rect, QColor("#F8FAFC"))
            painter.restore()

        super().paint(painter, opt, index)


class ControlBar(CardWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, radius=10, padding=(14, 12, 14, 12), spacing=10)

    def apply_button_metrics(self, *buttons: QWidget) -> None:
        for button in buttons:
            if hasattr(button, "setMinimumHeight"):
                button.setMinimumHeight(34)


class AlignModeCard(QFrame):
    def __init__(
        self,
        radio: QRadioButton,
        *,
        title: str,
        tag_text: str,
        description: str,
        icon: FIF,
        palette: dict[str, str],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._radio = radio
        self._title = title
        self._tag_text = tag_text
        self._description = description
        self._icon = icon
        self._palette = palette
        self._hovered = False
        self.setObjectName("AlignModeCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(110)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 12, 12)
        layout.setSpacing(16)

        self.accent_bar = QFrame(self)
        self.accent_bar.setFixedWidth(4)
        self.accent_bar.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.accent_bar)

        self.icon_box = QLabel(self)
        self.icon_box.setFixedSize(64, 64)
        self.icon_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon_box, 0, Qt.AlignmentFlag.AlignVCenter)

        body_row = QHBoxLayout()
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(14)
        layout.addLayout(body_row, 1)

        self._radio.setText("")
        self._radio.setCursor(Qt.CursorShape.PointingHandCursor)
        self._radio.setFixedWidth(26)
        body_row.addWidget(self._radio, 0, Qt.AlignmentFlag.AlignVCenter)

        text_content = QWidget(self)
        text_content.setObjectName("AlignModeTextContent")
        text_content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text_content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_column = QVBoxLayout(text_content)
        text_column.setContentsMargins(0, 0, 0, 0)
        text_column.setSpacing(8)
        body_row.addWidget(text_content, 1, Qt.AlignmentFlag.AlignVCenter)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)

        self.title_label = QLabel(self._title, self)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        title_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.tag_label = QLabel(self._tag_text, self)
        self.tag_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.tag_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_row.addWidget(self.tag_label, 0, Qt.AlignmentFlag.AlignVCenter)
        text_column.addLayout(title_row)

        self.desc_label = QLabel(self._description, self)
        self.desc_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.desc_label.setWordWrap(False)
        self.desc_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.desc_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        text_column.addWidget(self.desc_label, 0, Qt.AlignmentFlag.AlignLeft)

        self._refresh_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._radio.setChecked(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def sync_ui(self) -> None:
        self._refresh_style()

    def _refresh_style(self) -> None:
        checked = self._radio.isChecked()
        accent = self._palette["accent"]
        background = self._palette["selected_background"] if checked else "#ffffff"
        if self._hovered and not checked:
            background = self._palette["hover_background"]
        border = self._palette["selected_border"] if checked else "#E5E7EB"
        if self._hovered and not checked:
            border = self._palette["hover_border"]

        self.setStyleSheet(
            f"""
            QFrame#AlignModeCard {{
                background: {background};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            QWidget#AlignModeTextContent {{
                background: transparent;
                border: 0;
            }}
            """
        )
        self.accent_bar.setStyleSheet(
            f"background: {accent if checked else 'transparent'}; border: 0; border-radius: 2px;"
        )
        self.icon_box.setStyleSheet(
            f"""
            QLabel {{
                background: {self._palette["selected_icon_background"] if checked else self._palette["icon_background"]};
                border: 1px solid {self._palette["icon_border"]};
                border-radius: 16px;
            }}
            """
        )
        self.icon_box.setPixmap(self._icon.icon(color=QColor(accent)).pixmap(QSize(24, 24)))
        self._radio.setStyleSheet(
            f"""
            QRadioButton {{
                background: transparent;
                border: 0;
                padding: 0;
                margin: 0;
                min-width: 24px;
                max-width: 24px;
            }}
            QRadioButton::indicator {{
                width: 20px;
                height: 20px;
            }}
            QRadioButton::indicator:unchecked {{
                background: #ffffff;
                border: 2px solid #98A2B3;
                border-radius: 10px;
            }}
            QRadioButton::indicator:checked {{
                background: #ffffff;
                border: 6px solid {accent};
                border-radius: 10px;
            }}
            """
        )
        self.title_label.setStyleSheet("color: #111827; font-size: 15pt; font-weight: 700; background: transparent;")
        self.tag_label.setStyleSheet(
            f"""
            QLabel {{
                background: {self._palette["tag_background"]};
                color: {accent};
                border: 1px solid {self._palette["tag_border"]};
                border-radius: 11px;
                padding: 3px 9px;
                font-size: 9pt;
                font-weight: 700;
            }}
            """
        )
        self.desc_label.setStyleSheet("color: #667085; font-size: 10.5pt; background: transparent; border: 0;")


class ExportOptionRow(QFrame):
    def __init__(
        self,
        button: QWidget,
        *,
        title: str,
        icon: FIF,
        accent: str = "#ff5a6f",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._button = button
        self._title = title
        self._icon = icon
        self._accent = accent
        self._hovered = False
        self.setObjectName("ExportOptionRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(96)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(16)

        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(42, 42)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)

        self._button.setText("")
        self._button.setVisible(False)
        self._button.setFixedSize(0, 0)
        self._button.setStyleSheet(self._button_style())

        self.indicator_frame = QFrame(self)
        self.indicator_frame.setFixedSize(24, 24)
        self.indicator_frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.indicator_frame.setStyleSheet("background: transparent; border: 0;")
        self.indicator_mark = QLabel("✓", self.indicator_frame)
        self.indicator_mark.setGeometry(0, 0, 24, 24)
        self.indicator_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.indicator_mark.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.indicator_frame, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setWordWrap(True)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._button.toggled.connect(self._refresh_style)
        self._refresh_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._button.isEnabled():
            if isinstance(self._button, QRadioButton):
                self._button.setChecked(True)
            else:
                self._button.setChecked(not self._button.isChecked())
            event.accept()
            return
        super().mousePressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_style()
        super().changeEvent(event)

    def _button_style(self) -> str:
        return "QCheckBox, QRadioButton { background: transparent; border: 0; padding: 0; margin: 0; }"

    def _refresh_style(self) -> None:
        enabled = self.isEnabled() and self._button.isEnabled()
        checked = self._button.isChecked()
        if not enabled:
            background = "transparent"
            title_color = "#98A2B3"
            icon_color = "#D0D5DD"
        elif checked:
            background = "#FFF6F7"
            title_color = "#111827"
            icon_color = self._accent
        elif self._hovered:
            background = "#FFF9FA"
            title_color = "#111827"
            icon_color = self._accent
        else:
            background = "transparent"
            title_color = "#111827"
            icon_color = self._accent

        self.setStyleSheet(
            f"""
            QFrame#ExportOptionRow {{
                background: {background};
                border: 0;
                border-radius: 12px;
            }}
            """
        )
        self.title_label.setStyleSheet(
            f"color: {title_color}; font-size: 12.5pt; background: transparent; border: 0; padding: 0; margin: 0;"
        )
        self.icon_label.setStyleSheet("background: transparent; border: 0;")
        self.icon_label.setPixmap(self._icon.icon(color=QColor(icon_color)).pixmap(QSize(28, 28)))
        if enabled:
            if checked:
                indicator_background = self._accent
                indicator_border = self._accent
                mark_color = "#ffffff"
            else:
                indicator_background = "#ffffff"
                indicator_border = "#98A2B3"
                mark_color = "transparent"
        else:
            indicator_background = "#F8FAFC"
            indicator_border = "#D0D5DD"
            mark_color = "transparent"
        self.indicator_frame.setStyleSheet(
            f"background: {indicator_background}; border: 2px solid {indicator_border}; border-radius: 6px;"
        )
        self.indicator_mark.setStyleSheet(
            f"color: {mark_color}; font-size: 13pt; font-weight: 700; background: transparent; border: 0;"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)


class ExportChoiceCard(QFrame):
    def __init__(
        self,
        radio: QRadioButton,
        *,
        title: str,
        accent: str = "#ff5a6f",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._radio = radio
        self._title = title
        self._accent = accent
        self._hovered = False
        self.setObjectName("ExportChoiceCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(92)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        self._radio.setText("")
        self._radio.setVisible(False)
        self._radio.setFixedSize(0, 0)
        self._radio.setStyleSheet("QRadioButton { background: transparent; border: 0; padding: 0; margin: 0; }")

        self.indicator_frame = QFrame(self)
        self.indicator_frame.setFixedSize(24, 24)
        self.indicator_frame.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.indicator_frame.setStyleSheet("background: transparent; border: 0;")
        self.indicator_dot = QFrame(self.indicator_frame)
        self.indicator_dot.setFixedSize(10, 10)
        self.indicator_dot.move(7, 7)
        self.indicator_dot.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.indicator_frame, 0, Qt.AlignmentFlag.AlignVCenter)

        self.title_label = QLabel(title, self)
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self._radio.toggled.connect(self._refresh_style)
        self._refresh_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self._radio.isEnabled():
            self._radio.setChecked(True)
            event.accept()
            return
        super().mousePressEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            self._refresh_style()
        super().changeEvent(event)

    def _refresh_style(self) -> None:
        enabled = self.isEnabled() and self._radio.isEnabled()
        checked = self._radio.isChecked()

        if not enabled:
            background = "#F8FAFC"
            border = "#EAECF0"
            title_color = "#98A2B3"
        elif checked:
            background = "#FFF6F7"
            border = "#F7C8CE"
            title_color = "#111827"
        elif self._hovered:
            background = "#FFFBFB"
            border = "#F1D7DB"
            title_color = "#111827"
        else:
            background = "#FFFFFF"
            border = "#E5E7EB"
            title_color = "#111827"

        self.setStyleSheet(
            f"""
            QFrame#ExportChoiceCard {{
                background: {background};
                border: 1px solid {border};
                border-radius: 14px;
            }}
            """
        )
        self.title_label.setStyleSheet(
            f"color: {title_color}; font-size: 13.5pt; font-weight: 700; background: transparent; border: 0; padding: 0; margin: 0;"
        )
        if not enabled:
            indicator_border = "#D0D5DD"
            indicator_fill = "transparent"
        elif checked:
            indicator_border = self._accent
            indicator_fill = self._accent
        else:
            indicator_border = "#98A2B3"
            indicator_fill = "transparent"
        self.indicator_frame.setStyleSheet(
            f"background: #ffffff; border: 2px solid {indicator_border}; border-radius: 12px;"
        )
        self.indicator_dot.setStyleSheet(
            f"background: {indicator_fill}; border: 0; border-radius: 5px;"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)


@dataclass(frozen=True)
class WorkflowStepItem:
    module_id: str
    number: int
    title: str
    description: str
    implemented: bool


WORKFLOW_STEPS = [
    WorkflowStepItem(WORKFLOW_VIDEO_DOWNLOAD, 1, "视频下载", "下载在线视频", False),
    WorkflowStepItem(WORKFLOW_WAVEFORM_ALIGN, 2, "波形对齐", "音频与视频对齐", True),
    WorkflowStepItem(WORKFLOW_LYRICS_SEARCH, 3, "歌词检索", "搜索并获取歌词", True),
    WorkflowStepItem(WORKFLOW_LYRICS_TIMING, 4, "歌词打轴", "逐字 / 逐句打轴", False),
    WorkflowStepItem(WORKFLOW_SUBTITLE_RENDER, 5, "字幕视频生成", "渲染字幕样式", False),
    WorkflowStepItem(WORKFLOW_HIRES_MIX, 6, "Hi-Res 混流", "音视频混流导出", True),
]


class WorkflowStepButton(QWidget):
    clicked = Signal(int)

    def __init__(self, step: WorkflowStepItem, index: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.step = step
        self.index = index
        self._active = False
        self._hovered = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setObjectName("WorkflowStepItem")

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        layout = QHBoxLayout()
        layout.setContentsMargins(18, 10, 18, 8)
        layout.setSpacing(10)

        self.number_label = QLabel(str(step.number))
        self.number_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.number_label.setFixedSize(32, 32)
        self.number_label.setObjectName("WorkflowStepNumber")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        self.title_label = QLabel(step.title)
        self.title_label.setObjectName("WorkflowStepTitle")
        self.desc_label = QLabel(step.description)
        self.desc_label.setObjectName("WorkflowStepDescription")
        self.desc_label.setWordWrap(False)
        self.bottom_line = QFrame(self)
        self.bottom_line.setObjectName("WorkflowStepUnderline")
        self.bottom_line.setFixedHeight(2)
        self.bottom_line.hide()

        text_layout.addWidget(self.title_label)
        text_layout.addWidget(self.desc_label)

        layout.addWidget(self.number_label, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addLayout(text_layout, 1)
        outer_layout.addLayout(layout)
        outer_layout.addWidget(self.bottom_line)
        self._refresh_style()

    def setActive(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        self._refresh_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.index)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _refresh_style(self) -> None:
        if self._active:
            background = "#FFF6F7"
            title_color = "#BC495A"
            desc_color = "#8F5B64"
            number_background = "#D85C6C"
            number_color = "#FFFFFF"
            number_border = "#D85C6C"
        elif self._hovered:
            background = "#F6F8FB"
            title_color = "#1F2937"
            desc_color = "#64748B"
            number_background = "#FFFFFF"
            number_color = "#64748B"
            number_border = "#CBD5E1"
        else:
            background = "transparent"
            title_color = "#1F2937"
            desc_color = "#64748B"
            number_background = "#FFFFFF"
            number_color = "#64748B"
            number_border = "#CBD5E1"

        self.setStyleSheet(
            f"""
            QWidget#WorkflowStepItem {{
                background: {background};
                border: 0;
                border-radius: 10px;
            }}
            QLabel#WorkflowStepNumber {{
                background: {number_background};
                border: 1px solid {number_border};
                border-radius: 16px;
                color: {number_color};
                font-size: 12px;
                font-weight: 700;
            }}
            QLabel#WorkflowStepTitle {{
                color: {title_color};
                font-size: 14px;
                font-weight: 700;
            }}
            QLabel#WorkflowStepDescription {{
                color: {desc_color};
                font-size: 11px;
            }}
            QFrame#WorkflowStepUnderline {{
                background: #D85C6C;
                border: 0;
                border-radius: 1px;
            }}
            """
        )
        self.bottom_line.setVisible(self._active)


class WorkflowStepper(QWidget):
    currentChanged = Signal(int)
    stepClicked = Signal(int)

    def __init__(self, steps: list[WorkflowStepItem], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._steps = steps
        self._items: list[WorkflowStepButton] = []
        self._current_index = 0
        self.setObjectName("WorkflowStepper")

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        for index, _step in enumerate(steps):
            item = self.createStepItem(index)
            self._items.append(item)
            self._layout.addWidget(item, 1)
            if index < len(steps) - 1:
                separator = QLabel("›")
                separator.setAlignment(Qt.AlignmentFlag.AlignCenter)
                separator.setStyleSheet("color: #D1D5DB; font-size: 18px; font-weight: 500;")
                separator.setFixedWidth(24)
                self._layout.addWidget(separator, 0, Qt.AlignmentFlag.AlignVCenter)

        self.updateStepStyles()

    def createStepItem(self, index: int) -> WorkflowStepButton:
        item = WorkflowStepButton(self._steps[index], index, self)
        item.clicked.connect(self._handleStepClicked)
        return item

    def currentIndex(self) -> int:
        return self._current_index

    def setCurrentIndex(self, index: int) -> None:
        if index < 0 or index >= len(self._steps):
            return
        if self._current_index == index:
            self.updateStepStyles()
            return
        self._current_index = index
        self.updateStepStyles()
        self.currentChanged.emit(index)

    def setCurrentModule(self, module_id: str) -> None:
        for index, step in enumerate(self._steps):
            if step.module_id == module_id:
                self.setCurrentIndex(index)
                return

    def moduleIdAt(self, index: int) -> str:
        return self._steps[index].module_id

    def updateStepStyles(self) -> None:
        for index, item in enumerate(self._items):
            item.setActive(index == self._current_index)

    def updateStyles(self) -> None:
        self.updateStepStyles()

    def _handleStepClicked(self, index: int) -> None:
        self.stepClicked.emit(index)


class PlaceholderPage(QWidget):
    def __init__(self, *, title: str, description: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        shell = QVBoxLayout(self)
        shell.setContentsMargins(20, 20, 20, 20)
        shell.setSpacing(18)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)

        title_label = QLabel(title)
        title_label.setStyleSheet('font-size: 22pt; font-weight: 700; color: #1f2937;')
        subtitle_label = BodyLabel(description)
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet("color: #667085; font-size: 10.5pt;")
        header.addWidget(title_label)
        header.addWidget(subtitle_label)
        shell.addLayout(header)

        card = CardWidget(radius=10, padding=(32, 36, 32, 36), spacing=10)
        card_layout = card.createVBoxLayout()

        card_title = StrongBodyLabel(title)
        card_title.setStyleSheet("font-size: 18pt; font-weight: 700; color: #1f2937;")
        card_hint = QLabel("该模块尚未开发")
        card_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_hint.setStyleSheet(
            "background: #fff1f3; color: #d61f45; border: 1px solid #ffd1d8; "
            "border-radius: 14px; padding: 18px 20px; font-size: 14pt; font-weight: 700;"
        )
        card_desc = CaptionLabel("当前版本仅保留界面位置，用于统一工作流结构。")
        card_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_desc.setStyleSheet("color: #8a94a6; font-size: 10pt;")

        card_layout.addWidget(card_title, 0, Qt.AlignmentFlag.AlignLeft)
        card_layout.addStretch(1)
        card_layout.addWidget(card_hint)
        card_layout.addWidget(card_desc)
        card_layout.addStretch(1)
        shell.addWidget(card, 1)


class DropZoneCard(CardWidget):
    pathChanged = Signal(Path)
    browseRequested = Signal()

    def __init__(
        self,
        *,
        title: str,
        hint: str,
        extensions: set[str],
        min_height: int = 220,
        icon_text: str = "",
        placeholder_icon: str = "",
        accent_bg: str = "#f6f8fb",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.extensions = {ext.lower() for ext in extensions}
        self.accent_bg = accent_bg
        self.path: Path | None = None
        self._hovered = False
        self._drag_state = "idle"
        self._default_action_text = "点击选择文件，或直接拖进这个区域"

        self.setObjectName("DropZoneCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAcceptDrops(True)
        self.setMinimumHeight(min_height)

        layout = self.createVBoxLayout()
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)

        self.icon_label = QLabel(icon_text)
        self.icon_label.setObjectName("DropZoneIcon")
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.icon_label.setVisible(bool(icon_text))

        self.title_label = QLabel(title)
        self.title_label.setObjectName("DropZoneTitle")
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        title_font = QFont("Microsoft YaHei UI", 12)
        title_font.setBold(True)
        apply_safe_label_metrics(self.title_label, title_font)
        title_row.addWidget(self.icon_label, 0, Qt.AlignmentFlag.AlignVCenter)
        title_row.addWidget(self.title_label, 1, Qt.AlignmentFlag.AlignVCenter)

        self.hint_label = QLabel(hint)
        self.hint_label.setObjectName("DropZoneHint")
        self.hint_label.setWordWrap(True)
        self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.placeholder_label = QLabel(placeholder_icon)
        self.placeholder_label.setObjectName("DropZonePlaceholder")
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.path_label = QLabel("未选择文件")
        self.path_label.setObjectName("DropZonePath")
        self.path_label.setWordWrap(True)
        self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self.action_label = QLabel(self._default_action_text)
        self.action_label.setObjectName("DropZoneAction")
        self.action_label.setWordWrap(True)
        self.action_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._status_badge = QLabel("✓", self)
        self._status_badge.setObjectName("DropZoneStatusBadge")
        self._status_badge.setFixedSize(22, 22)
        self._status_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._status_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_badge.setStyleSheet(
            """
            QLabel#DropZoneStatusBadge {
                background: #10B981;
                color: white;
                border-radius: 11px;
                font-size: 13pt;
                font-weight: 700;
                qproperty-alignment: AlignCenter;
            }
            """
        )
        self._status_badge.hide()

        layout.addLayout(title_row)
        layout.addWidget(self.hint_label)
        layout.addStretch(1)
        layout.addWidget(self.placeholder_label, 0, Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.path_label)
        layout.addWidget(self.action_label)
        self._refresh_style()

    def accepts(self, path: Path) -> bool:
        return path.is_file() and path.suffix.lower() in self.extensions

    def set_path(self, path: Path) -> None:
        self.path = path
        self.path_label.setText(str(path))
        self._drag_state = "idle"
        self._refresh_style()

    def clear_path(self) -> None:
        self.path = None
        self.path_label.setText("未选择文件")
        self._drag_state = "idle"
        self._refresh_style()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._refresh_style()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._refresh_style()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_status_badge()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.browseRequested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            self._drag_state = "reject"
            self._refresh_style()
            event.ignore()
            return
        path = Path(urls[0].toLocalFile()).expanduser()
        if self.accepts(path):
            self._drag_state = "accept"
            self._refresh_style()
            event.acceptProposedAction()
            return
        self._drag_state = "reject"
        self._refresh_style()
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._drag_state = "idle"
        self._refresh_style()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        urls = event.mimeData().urls()
        if not urls:
            self._drag_state = "idle"
            self._refresh_style()
            event.ignore()
            return
        path = Path(urls[0].toLocalFile()).expanduser()
        if not self.accepts(path):
            self._drag_state = "reject"
            self._refresh_style()
            event.ignore()
            return
        self.set_path(path)
        self.pathChanged.emit(path)
        event.acceptProposedAction()

    def _position_status_badge(self) -> None:
        self._status_badge.move(max(0, self.width() - 32), 10)
        self._status_badge.raise_()

    def _refresh_style(self) -> None:
        selected = bool(getattr(self, "_path", None) or self.path)
        border_width = "1.5"
        border_style = "dashed"
        if self._drag_state == "accept":
            background = "#dbeafe"
            border = "#2f6fed"
            accent = "#1d4ed8"
            border_width = "2"
            border_style = "solid"
            action_text = "松开鼠标即可导入这个文件"
        elif self._drag_state == "reject":
            background = "#fef2f2"
            border = "#ef4444"
            accent = "#b91c1c"
            border_width = "2"
            border_style = "solid"
            action_text = "这个文件类型不支持，请换一个文件"
        elif self._hovered:
            background = self.accent_bg
            border = "#2f6fed"
            accent = "#2f6fed"
            border_width = "2"
            border_style = "solid"
            action_text = self._default_action_text
        elif selected:
            background = "#FFFFFF"
            border = "#10B981"
            accent = "#177245"
            border_style = "solid"
            action_text = self._default_action_text
        else:
            background = self.accent_bg
            border = "#C2CAD8"
            accent = "#2f6fed"
            action_text = self._default_action_text

        self.action_label.setText(action_text)
        self.placeholder_label.setVisible(self.path is None and bool(self.placeholder_label.text()))
        self._status_badge.setVisible(selected)

        self.setStyleSheet(
            f"""
            QFrame#DropZoneCard {{
                background: {background};
                border: {border_width}px {border_style} {border};
                border-radius: 10px;
            }}
            QLabel#DropZoneIcon {{
                background: transparent;
                border: 0;
                font-size: 16pt;
            }}
            QLabel#DropZoneTitle {{
                background: transparent;
                border: 0;
                color: #1f2937;
                font-family: "Microsoft YaHei UI";
                font-size: 12pt;
                font-weight: 700;
            }}
            QLabel#DropZoneHint {{
                background: transparent;
                border: 0;
                color: #5b6677;
                font-family: "Microsoft YaHei UI";
                font-size: 10pt;
            }}
            QLabel#DropZonePlaceholder {{
                background: transparent;
                border: 0;
                color: #C2CAD8;
                font-family: "Microsoft YaHei UI";
                font-size: 48px;
            }}
            QLabel#DropZonePath {{
                background: transparent;
                border: 0;
                color: #111827;
                font-family: "Consolas";
                font-size: 10pt;
            }}
            QLabel#DropZoneAction {{
                background: transparent;
                border: 0;
                color: {accent};
                font-family: "Microsoft YaHei UI";
                font-size: 10pt;
                font-weight: 700;
            }}
            """
        )
        self._position_status_badge()


class BackgroundTask(QThread):
    log_message = Signal(str)
    task_succeeded = Signal(object)
    task_failed = Signal(str)

    def __init__(self, runner: Callable[[Callable[[str], None]], object]) -> None:
        super().__init__()
        self._runner = runner

    def run(self) -> None:  # noqa: D401
        try:
            result = self._runner(self.log_message.emit)
        except Exception as exc:  # noqa: BLE001
            self.task_failed.emit(str(exc))
            return
        self.task_succeeded.emit(result)


class WaveformView(QWidget):
    playheadChanged = Signal(float)
    offsetChanged = Signal(float)
    trimChanged = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.video_waveform: WaveformData | None = None
        self.audio_waveform: WaveformData | None = None
        self.target_track = ALIGN_TARGET_VIDEO
        self.offset_seconds = 0.0
        self.playhead_seconds = 0.0
        self.view_start_seconds = 0.0
        self.trim_end_seconds: float | None = None
        self.pixels_per_second = 120.0
        self.drag_mode = "offset"
        self._drag_kind = ""
        self._drag_start_x = 0.0
        self._drag_start_offset = 0.0
        self._drag_start_view = 0.0
        self.track_label_width = 190
        self.right_reserved_width = 58
        self._auto_fit_view = True
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(280)
        self.setFocusPolicy(Qt.FocusPolicy.WheelFocus)
        self.setMouseTracking(True)

    def clear(self) -> None:
        self.video_waveform = None
        self.audio_waveform = None
        self.offset_seconds = 0.0
        self.playhead_seconds = 0.0
        self.view_start_seconds = 0.0
        self.trim_end_seconds = None
        self._auto_fit_view = True
        self.update()

    def set_waveforms(self, *, video_waveform: WaveformData, audio_waveform: WaveformData) -> None:
        self.video_waveform = video_waveform
        self.audio_waveform = audio_waveform
        self.offset_seconds = 0.0
        self.playhead_seconds = 0.0
        self.view_start_seconds = 0.0
        self.trim_end_seconds = None
        self._auto_fit_view = True
        self.fit_to_waveforms()
        self.update()

    def set_target_track(self, target_track: str) -> None:
        self.target_track = target_track
        self.set_offset(0.0)

    def set_drag_mode(self, mode: str) -> None:
        self.drag_mode = mode if mode in {"offset", "pan"} else "offset"

    def set_offset(self, seconds: float) -> None:
        self.offset_seconds = seconds
        self.offsetChanged.emit(seconds)
        self.update()

    def nudge_offset(self, delta_seconds: float) -> None:
        self.set_offset(self.offset_seconds + delta_seconds)

    def set_playhead(self, seconds: float, *, keep_visible: bool = False) -> None:
        self.playhead_seconds = max(0.0, seconds)
        if keep_visible:
            self._ensure_visible(self.playhead_seconds)
        self.playheadChanged.emit(self.playhead_seconds)
        self.update()

    def set_trim_end(self, seconds: float) -> None:
        self.trim_end_seconds = max(0.0, seconds)
        self.trimChanged.emit(self.trim_end_seconds)
        self.update()

    def clear_trim_end(self) -> None:
        self.trim_end_seconds = None
        self.trimChanged.emit(None)
        self.update()

    def set_zoom(self, pixels_per_second: float) -> None:
        self._auto_fit_view = False
        self._zoom_to(pixels_per_second, self._playhead_anchor_x())

    def fit_to_waveforms(self) -> None:
        if not self.video_waveform or not self.audio_waveform:
            return
        _plot_left, plot_width = self._plot_bounds()
        usable_width = max(1.0, plot_width - 8.0)
        self.pixels_per_second = max(0.5, min(1200.0, usable_width / 15.0))
        self.view_start_seconds = 0.0
        self._auto_fit_view = True
        self.update()

    def reset_view(self) -> None:
        self.fit_to_waveforms()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._auto_fit_view and self.video_waveform and self.audio_waveform:
            self.fit_to_waveforms()

    def jump_to_end(self) -> None:
        if not self.video_waveform or not self.audio_waveform:
            return
        visible_seconds = self._visible_seconds()
        video_end = self.video_waveform.duration + (self.offset_seconds if self.target_track == ALIGN_TARGET_VIDEO else 0.0)
        audio_end = self.audio_waveform.duration + (self.offset_seconds if self.target_track == ALIGN_TARGET_AUDIO else 0.0)
        timeline_end = max(video_end, audio_end)
        self.view_start_seconds = max(0.0, timeline_end - visible_seconds * (2 / 3))
        self.update()

    def source_starts(self) -> tuple[float, float]:
        video_offset = self.offset_seconds if self.target_track == ALIGN_TARGET_VIDEO else 0.0
        audio_offset = self.offset_seconds if self.target_track == ALIGN_TARGET_AUDIO else 0.0
        timeline_start = max(self.view_start_seconds, video_offset, audio_offset, 0.0)
        return max(0.0, timeline_start - video_offset), max(0.0, timeline_start - audio_offset)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if not self.video_waveform or not self.audio_waveform:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else (1 / 1.15)
        self._auto_fit_view = False
        self._zoom_to(self.pixels_per_second * factor, self._playhead_anchor_x())
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if not self.video_waveform or not self.audio_waveform:
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start_x = event.position().x()
            self._drag_start_offset = self.offset_seconds
            self._drag_start_view = self.view_start_seconds
            if event.position().y() <= 24 and event.position().x() >= self.track_label_width:
                self._drag_kind = "playhead"
                self._set_playhead_from_x(event.position().x())
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self._drag_kind = "playhead"
                self._set_playhead_from_x(event.position().x())
            elif self.drag_mode == "pan":
                self._drag_kind = "pan"
            else:
                self._drag_kind = "offset"

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._drag_kind == "playhead":
            self._set_playhead_from_x(event.position().x())
            return
        if self._drag_kind == "pan":
            delta_seconds = (event.position().x() - self._drag_start_x) / self.pixels_per_second
            self.view_start_seconds = max(0.0, self._drag_start_view - delta_seconds)
            self.update()
            return
        if self._drag_kind == "offset":
            delta_seconds = (event.position().x() - self._drag_start_x) / self.pixels_per_second
            self.set_offset(self._drag_start_offset + delta_seconds)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_kind = ""

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        if self._auto_fit_view and self.video_waveform and self.audio_waveform:
            _plot_left, plot_width = self._plot_bounds()
            fitted = max(0.5, min(1200.0, max(1.0, plot_width - 8.0) / 15.0))
            if abs(fitted - self.pixels_per_second) > 0.01:
                self.pixels_per_second = fitted
                self.view_start_seconds = 0.0

        if not self.video_waveform or not self.audio_waveform:
            return

        outer_rect = self.rect().adjusted(0, 0, -self.right_reserved_width, -1)
        label_width = self.track_label_width

        ruler_rect = outer_rect.adjusted(label_width, 0, 0, -(outer_rect.height() - 24))
        painter.setPen(QColor("#ffffff"))
        painter.drawLine(ruler_rect.left() + 1, ruler_rect.top(), ruler_rect.right() - 1, ruler_rect.top())
        painter.setPen(QColor("#cfd7e2"))
        painter.drawLine(ruler_rect.left(), ruler_rect.top() + 1, ruler_rect.left(), ruler_rect.bottom())
        painter.drawLine(ruler_rect.right(), ruler_rect.top() + 1, ruler_rect.right(), ruler_rect.bottom())
        painter.drawLine(ruler_rect.left(), ruler_rect.bottom(), ruler_rect.right(), ruler_rect.bottom())

        content_rect = outer_rect.adjusted(0, 24, 0, 0)
        track_gap = 0
        track_height = max(68, int((content_rect.height() - track_gap) / 2))
        video_label_rect = content_rect.adjusted(0, 0, -(content_rect.width() - label_width), -(content_rect.height() - track_height))
        video_rect = content_rect.adjusted(label_width, 0, 0, -(content_rect.height() - track_height))
        audio_label_rect = content_rect.adjusted(0, track_height + track_gap, -(content_rect.width() - label_width), 0)
        audio_rect = content_rect.adjusted(label_width, track_height + track_gap, 0, 0)

        self._draw_ruler(painter, ruler_rect)
        video_offset = self.offset_seconds if self.target_track == ALIGN_TARGET_VIDEO else 0.0
        audio_offset = self.offset_seconds if self.target_track == ALIGN_TARGET_AUDIO else 0.0
        self._draw_label_block(
            painter,
            video_label_rect,
            "字幕视频音轨",
            format_offset(video_offset) if self.target_track == ALIGN_TARGET_VIDEO else "",
            QColor("#F04452"),
        )
        self._draw_label_block(
            painter,
            audio_label_rect,
            "原唱音源",
            format_offset(audio_offset) if self.target_track == ALIGN_TARGET_AUDIO else "",
            QColor("#2F6BFF"),
        )

        self._draw_track(
            painter,
            video_rect,
            self.video_waveform,
            QColor("#F04452"),
            self.offset_seconds if self.target_track == ALIGN_TARGET_VIDEO else 0.0,
        )
        self._draw_track(
            painter,
            audio_rect,
            self.audio_waveform,
            QColor("#2F6BFF"),
            self.offset_seconds if self.target_track == ALIGN_TARGET_AUDIO else 0.0,
        )

        playhead_x = self._time_to_x(self.playhead_seconds, video_rect.left())
        painter.setPen(QPen(QColor("#F04452"), 2))
        painter.drawLine(int(playhead_x), ruler_rect.top(), int(playhead_x), audio_rect.bottom())

        if self.trim_end_seconds is not None:
            trim_x = self._time_to_x(self.trim_end_seconds, video_rect.left())
            painter.setPen(QPen(QColor("#eab308"), 2, Qt.PenStyle.DashLine))
            painter.drawLine(int(trim_x), video_rect.top(), int(trim_x), video_rect.bottom())

    def _draw_track(
        self,
        painter: QPainter,
        rect,
        waveform: WaveformData,
        color: QColor,
        track_offset: float,
    ) -> None:
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QColor("#d5dce6"))
        painter.drawRect(rect)

        center_y = rect.center().y()
        painter.setPen(QPen(QColor("#e5e7eb"), 1))
        painter.drawLine(rect.left() + 1, center_y, rect.right() - 1, center_y)

        painter.setPen(QPen(color, 1))
        usable_height = max(12.0, rect.height() * 0.35)
        for x in range(rect.left() + 1, rect.right(), 2):
            absolute_time = self.view_start_seconds + ((x - rect.left()) / self.pixels_per_second)
            source_time = absolute_time - track_offset
            if source_time < 0 or source_time >= waveform.duration:
                continue
            index = int(source_time * waveform.peaks_per_second)
            if index < 0 or index >= len(waveform.peaks):
                continue
            amplitude = waveform.peaks[index]
            top = center_y - int(amplitude * usable_height)
            bottom = center_y + int(amplitude * usable_height)
            painter.drawLine(x, top, x, bottom)

        track_end_seconds = waveform.duration + track_offset
        end_x = self._time_to_x(track_end_seconds, rect.left())
        if rect.left() < end_x < rect.right():
            end_x_int = int(end_x)
            painter.fillRect(end_x_int + 1, rect.top() + 1, rect.right() - end_x_int - 1, rect.height() - 2, QColor("#f8fafc"))
            painter.setPen(QPen(QColor("#94a3b8"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(end_x_int, rect.top() + 1, end_x_int, rect.bottom() - 1)
            painter.setPen(QColor("#94a3b8"))
            painter.setFont(QFont("Microsoft YaHei UI", 8))
            painter.drawText(end_x_int + 4, rect.top() + 12, "结束")

    def _draw_label_block(self, painter: QPainter, rect, title: str, offset_text: str, title_color: QColor) -> None:
        painter.fillRect(rect, QColor("#ffffff"))
        text_rect = rect.adjusted(10, 8, -10, -8)
        title_font = QFont("Microsoft YaHei UI", 11)
        title_font.setBold(True)
        title_metrics = QFontMetrics(title_font)
        offset_font = QFont("Microsoft YaHei UI", 11)
        offset_font.setBold(True)
        offset_metrics = QFontMetrics(offset_font)
        line_gap = 4 if offset_text else 0
        content_height = title_metrics.height() + line_gap + (offset_metrics.height() if offset_text else 0)
        start_y = text_rect.top() + max(0, (text_rect.height() - content_height) // 2)
        painter.setFont(title_font)
        painter.setPen(title_color)
        title_display = title_metrics.elidedText(title, Qt.TextElideMode.ElideRight, text_rect.width())
        painter.drawText(
            text_rect.left(),
            start_y,
            text_rect.width(),
            title_metrics.height(),
            int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
            title_display,
        )
        if offset_text:
            painter.setFont(offset_font)
            offset_display = offset_metrics.elidedText(offset_text, Qt.TextElideMode.ElideRight, text_rect.width())
            painter.drawText(
                text_rect.left(),
                start_y + title_metrics.height() + line_gap,
                text_rect.width(),
                offset_metrics.height(),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                offset_display,
            )

    def _draw_ruler(self, painter: QPainter, rect) -> None:
        visible_seconds = self._visible_seconds()
        min_label_spacing_px = 72.0
        step = 1.0
        for candidate in (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0):
            if candidate * self.pixels_per_second >= min_label_spacing_px:
                step = candidate
                break
            step = candidate
        painter.setPen(QColor("#94a3b8"))
        start_tick = int(self.view_start_seconds // step)
        end_tick = int((self.view_start_seconds + visible_seconds) // step) + 1
        for tick in range(start_tick, end_tick):
            tick_seconds = tick * step
            x = self._time_to_x(tick_seconds, rect.left())
            if x < rect.left() or x > rect.right():
                continue
            painter.drawLine(int(x), rect.bottom() - 6, int(x), rect.bottom())
            label = f"{tick_seconds:.1f}s" if step < 10 else f"{tick_seconds:.0f}s"
            painter.drawText(int(x) + 2, rect.top() + 14, label)

    def _plot_bounds(self) -> tuple[float, float]:
        plot_left = float(self.track_label_width)
        plot_width = max(1.0, float(self.width() - self.track_label_width - self.right_reserved_width - 1))
        return plot_left, plot_width

    def _zoom_to(self, pixels_per_second: float, anchor_x: float) -> None:
        plot_left, plot_width = self._plot_bounds()
        anchor_x = min(plot_left + plot_width, max(plot_left, anchor_x))
        old_pixels_per_second = max(1.0, self.pixels_per_second)
        anchor_seconds = self.view_start_seconds + (anchor_x - plot_left) / old_pixels_per_second
        self.pixels_per_second = max(0.5, min(1200.0, pixels_per_second))
        self.view_start_seconds = max(0.0, anchor_seconds - (anchor_x - plot_left) / self.pixels_per_second)
        self.update()

    def _playhead_anchor_x(self) -> float:
        plot_left, plot_width = self._plot_bounds()
        return min(plot_left + plot_width, max(plot_left, self._time_to_x(self.playhead_seconds, plot_left)))

    def _visible_seconds(self) -> float:
        _plot_left, plot_width = self._plot_bounds()
        return max(1.0, plot_width / self.pixels_per_second)

    def _ensure_visible(self, seconds: float) -> None:
        visible_seconds = self._visible_seconds()
        if seconds < self.view_start_seconds:
            self.view_start_seconds = max(0.0, seconds - visible_seconds * 0.1)
        elif seconds > self.view_start_seconds + visible_seconds:
            self.view_start_seconds = max(0.0, seconds - visible_seconds * 0.9)

    def _time_to_x(self, seconds: float, left_edge: int) -> float:
        return left_edge + (seconds - self.view_start_seconds) * self.pixels_per_second

    def _set_playhead_from_x(self, x_pos: float) -> None:
        rect_left, rect_width = self._plot_bounds()
        clamped_x = min(rect_left + rect_width, max(rect_left, x_pos))
        time_pos = self.view_start_seconds + (clamped_x - rect_left) / self.pixels_per_second
        self.set_playhead(time_pos)


class KrokHelperQtApp(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = load_app_settings()
        self.hires_task: BackgroundTask | None = None
        self.lyrics_search_task: BackgroundTask | None = None
        self.lyrics_fetch_task: BackgroundTask | None = None
        self.align_analysis_task: BackgroundTask | None = None
        self.align_auto_task: BackgroundTask | None = None
        self.align_export_task: BackgroundTask | None = None
        self.lyrics_search_service = LyricsSearchService()
        self.lyrics_search_results: list[LyricsSearchCandidate] = []
        self.lyrics_pending_results: list[LyricsSearchCandidate] = []
        self.lyrics_selected_candidate: LyricsSearchCandidate | None = None
        self.lyrics_search_keyword = ""
        self.lyrics_search_provider_ids: tuple[str, ...] = DEFAULT_LYRICS_PROVIDER_IDS
        self.lyrics_next_provider_pages: dict[str, int] = {}
        self.lyrics_has_more_results = False
        self._lyrics_loading_more = False
        self._lyrics_loading_key = ""
        self.align_preview_process = None
        self.align_preview_started_at = 0.0
        self.align_preview_start_seconds = 0.0
        self._hires_cancel_requested = False
        self._hires_process: subprocess.Popen | None = None
        self._hires_expected_outputs: list[Path] = []
        self._hires_completed_outputs: list[Path] = []
        self._hires_preexisting_outputs: set[Path] = set()
        self._align_export_cancel_requested = False
        self._align_export_process: subprocess.Popen | None = None
        self._align_export_expected_outputs: list[Path] = []
        self._align_export_completed_outputs: list[Path] = []
        self.active_module = WORKFLOW_VIDEO_DOWNLOAD
        self._loading_settings_into_ui = True

        self.output_name_mode_value = OUTPUT_NAME_MODE_FIXED
        self.on_name_template_value = DEFAULT_ON_NAME_TEMPLATE
        self.off_name_template_value = DEFAULT_OFF_NAME_TEMPLATE
        self.align_video_name_template_value = DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE
        self.align_audio_name_template_value = DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE
        self.ffmpeg_dir_text = ""
        self._align_lead_fill_selection = LEAD_FILL_BLACK
        self._align_encode_selection = (
            self.settings.align_encode_mode
            if self.settings.align_encode_mode in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}
            else ENCODE_MODE_SOFTWARE
        )
        self._media_duration_cache: dict[Path, str] = {}
        self._suppress_preview_seek_restart = False
        self._restoring_from_maximized = False
        self._startup_geometry_applied = False
        self.align_control_panel: QFrame | None = None
        self.align_open_output_button: QPushButton | None = None
        self.align_clear_button: QPushButton | None = None
        self.align_jump_to_end_button: QPushButton | None = None
        self.align_reset_view_button: QPushButton | None = None

        setTheme(Theme.LIGHT, lazy=True)
        setThemeColor("#ff5a6f", lazy=True)
        self.setWindowTitle(APP_TITLE)
        app_icon = load_app_icon()
        if app_icon is not None:
            self.setWindowIcon(app_icon)
        self.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
        self.setMinimumSize(WINDOW_MIN_WIDTH, WINDOW_MIN_HEIGHT)

        self._apply_styles()
        self._build_ui()
        self._load_settings_into_ui()
        self._bind_shortcuts()

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(300)
        self.preview_timer.timeout.connect(self._poll_alignment_preview)

    def _track_background_task(self, attr_name: str, task: BackgroundTask) -> BackgroundTask:
        task.setObjectName(attr_name)
        setattr(self, attr_name, task)
        task.finished.connect(lambda attr_name=attr_name, task=task: self._cleanup_background_task(attr_name, task))
        task.finished.connect(task.deleteLater)
        return task

    def _cleanup_background_task(self, attr_name: str, task: BackgroundTask) -> None:
        if getattr(self, attr_name, None) is task:
            setattr(self, attr_name, None)

    def _running_background_tasks(self) -> list[BackgroundTask]:
        task_attrs = (
            "hires_task",
            "lyrics_search_task",
            "lyrics_fetch_task",
            "align_analysis_task",
            "align_auto_task",
            "align_export_task",
        )
        tasks: list[BackgroundTask] = []
        for attr_name in task_attrs:
            task = getattr(self, attr_name, None)
            if task is not None and task.isRunning():
                tasks.append(task)
        return tasks

    def showEvent(self, event) -> None:  # noqa: N802
        if not self._startup_geometry_applied:
            self._startup_geometry_applied = True
            QTimer.singleShot(0, self._apply_startup_window_geometry)
        super().showEvent(event)

    def changeEvent(self, event) -> None:  # noqa: N802
        if event.type() == QEvent.Type.WindowStateChange:
            old_state = event.oldState() if hasattr(event, "oldState") else Qt.WindowState.WindowNoState
            current_state = self.windowState()
            was_large = bool(
                old_state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen)
            )
            is_large = bool(
                current_state & (Qt.WindowState.WindowMaximized | Qt.WindowState.WindowFullScreen)
            )
            if was_large and not is_large and not self._restoring_from_maximized:
                self._restoring_from_maximized = True
                QTimer.singleShot(0, self._restore_windowed_geometry_centered)
        super().changeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_lyrics_layout_direction()

    def _refresh_lyrics_layout_direction(self) -> None:
        layout = getattr(self, "lyrics_content_layout", None)
        if layout is None:
            return
        narrow = self.width() < 1220
        target_direction = QBoxLayout.Direction.TopToBottom if narrow else QBoxLayout.Direction.LeftToRight
        if layout.direction() != target_direction:
            layout.setDirection(target_direction)
        if narrow:
            layout.setStretch(0, 1)
            layout.setStretch(1, 1)
        else:
            layout.setStretch(0, 7)
            layout.setStretch(1, 6)

    def _apply_startup_window_geometry(self) -> None:
        self._restore_windowed_geometry_centered()

    def _restore_windowed_geometry_centered(self) -> None:
        try:
            screen = self.screen() or QApplication.primaryScreen()
            if screen is None:
                return
            available = screen.availableGeometry()
            safe_rect = available.adjusted(48, 72, -48, -88)
            if safe_rect.width() <= 0 or safe_rect.height() <= 0:
                safe_rect = available.adjusted(24, 24, -24, -48)

            min_width = min(WINDOW_MIN_WIDTH, safe_rect.width())
            min_height = min(WINDOW_MIN_HEIGHT, safe_rect.height())
            if min_width > 0 and min_height > 0:
                self.setMinimumSize(min_width, min_height)

            target_width = min(
                WINDOW_WIDTH,
                safe_rect.width(),
            )
            target_height = min(
                WINDOW_HEIGHT,
                safe_rect.height(),
            )
            target_width = max(min_width, target_width)
            target_height = max(min_height, target_height)
            left = safe_rect.x() + max(0, (safe_rect.width() - target_width) // 2)
            top = safe_rect.y() + max(0, (safe_rect.height() - target_height) // 2)
            self.setGeometry(left, top, target_width, target_height)
        finally:
            self._restoring_from_maximized = False

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #F4F7FB;
                color: #1f2937;
                font-family: "Microsoft YaHei UI";
                font-size: 10.5pt;
            }
            QLabel, BodyLabel, CaptionLabel {
                background: transparent;
                font-family: "Microsoft YaHei UI";
                font-weight: 400;
            }
            StrongBodyLabel {
                background: transparent;
                font-family: "Microsoft YaHei UI";
                font-weight: 700;
            }
            QWidget#AppRoot {
                background: #F4F7FB;
            }
            QFrame[cardWidget="true"] {
                background: #FFFFFF;
                border: 1px solid #E5EAF2;
                border-radius: 8px;
            }
            QFrame#WorkflowBar {
                background: #FBFCFE;
                border: 1px solid #E3E8F0;
                border-radius: 8px;
            }
            QWidget#LyricsPage {
                background: #F4F7FB;
            }
            QFrame#LyricsSearchPanel, QFrame#LyricsResultPanel, QFrame#LyricsPreviewPanel {
                background: #FFFFFF;
                border: 1px solid #E1E7F0;
                border-radius: 10px;
            }
            QFrame#TrimRow {
                background: transparent;
                border: 0;
            }
            QLabel#AppTitle {
                color: #1f2937;
                font-size: 18pt;
                font-weight: 700;
            }
            QLabel#AppSubtitle {
                color: #6B7280;
                font-size: 10.5pt;
            }
            ToolButton#AlignMaterialSettingsButton {
                background: transparent;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 2px;
            }
            ToolButton#AlignMaterialSettingsButton:hover {
                background: #F3F4F6;
                border-color: #E5E7EB;
            }
            QLabel#PageTitle {
                color: #1f2937;
                font-size: 20pt;
                font-weight: 700;
            }
            QLabel#PanelTitle {
                background: transparent;
                color: #111827;
                font-size: 12.5pt;
                font-weight: 700;
            }
            QLabel#LyricsPageDescription {
                color: #667085;
                font-size: 10pt;
            }
            QLabel#LyricsSecondaryText, QLabel#LyricsStatusText, QLabel#LyricsResultsSummary, QLabel#LyricsPreviewHint, QLabel#LyricsMatchSummary {
                color: #64748B;
                font-size: 9pt;
            }
            QLabel#LyricsPreviewMeta {
                color: #64748B;
                font-size: 9.5pt;
            }
            QLabel#LyricsPreviewTitle {
                color: #0F172A;
                font-size: 14pt;
                font-weight: 700;
            }
            QPlainTextEdit#LogText {
                background: #ffffff;
                border: 0;
                color: #1f2937;
                font-family: "Consolas";
                font-size: 10pt;
            }
            QPlainTextEdit#LyricsPreviewText {
                background: #F8FAFC;
                border: 1px solid #DDE5EF;
                border-radius: 8px;
                color: #1E293B;
                font-size: 11pt;
                padding: 12px 14px;
                selection-background-color: #FAD7DE;
                selection-color: #111827;
            }
            QPlainTextEdit#LyricsPreviewText:focus {
                border: 1px solid #D87886;
                background: #FBFCFE;
            }
            QTableWidget#LyricsResultsTable {
                background: #FFFFFF;
                alternate-background-color: #ffffff;
                border: 1px solid #DDE5EF;
                gridline-color: transparent;
                selection-background-color: transparent;
                selection-color: #111827;
                outline: 0;
                border-radius: 8px;
            }
            QTableWidget#LyricsResultsTable::item {
                padding: 12px 12px;
                border: 0;
                border-bottom: 1px solid rgba(226, 232, 240, 0.9);
            }
            QTableWidget#LyricsResultsTable::item:hover {
                background: #F8FAFC;
            }
            QTableWidget#LyricsResultsTable::item:selected {
                background: transparent;
                color: #111827;
            }
            QTableWidget#LyricsResultsTable::item:selected:hover {
                background: transparent;
            }
            QTableWidget#LyricsResultsTable QHeaderView::section {
                background: #F8FAFC;
                color: #64748B;
                border: 0;
                border-bottom: 1px solid #DDE5EF;
                padding: 9px 10px;
                font-weight: 700;
            }
            QPushButton#LyricsSearchButton, PrimaryPushButton#LyricsSearchButton {
                background: #D85C6C;
                border: 1px solid #D85C6C;
                border-radius: 8px;
                color: #FFFFFF;
                font-weight: 700;
                padding: 8px 18px;
            }
            QPushButton#LyricsSearchButton:hover, PrimaryPushButton#LyricsSearchButton:hover {
                background: #C94F60;
                border-color: #C94F60;
            }
            QPushButton#LyricsSearchButton:pressed, PrimaryPushButton#LyricsSearchButton:pressed {
                background: #B94455;
                border-color: #B94455;
            }
            QPushButton#LyricsSearchButton:disabled, PrimaryPushButton#LyricsSearchButton:disabled {
                background: #E8B5BD;
                border-color: #E8B5BD;
                color: #FFFFFF;
            }
            QPushButton#LyricsCopyButton {
                background: #FFFFFF;
                border: 1px solid #D7DEE9;
                border-radius: 8px;
                color: #334155;
                padding: 7px 14px;
                font-weight: 600;
            }
            QPushButton#LyricsCopyButton:hover {
                background: #F8FAFC;
                border-color: #C6D0DE;
            }
            QPushButton#LyricsCopyButton:pressed {
                background: #EEF2F7;
            }
            QCheckBox#LyricsStripIntroCheck {
                color: #475569;
                spacing: 7px;
            }
            QHeaderView::section {
                background: #eef2f7;
                color: #111827;
                border: 0;
                border-right: 1px solid #d5dce6;
                border-bottom: 1px solid #d5dce6;
                padding: 6px 8px;
                font-weight: 700;
            }
            QPushButton[compact="true"] {
                padding: 3px 8px;
                font-size: 10pt;
            }
            QProgressBar {
                border: 0;
                background: #eceff5;
                min-height: 10px;
                max-height: 10px;
                border-radius: 5px;
            }
            QProgressBar::chunk {
                background: #ff5a6f;
                border-radius: 5px;
            }
            QRadioButton:disabled, QCheckBox:disabled, QLabel:disabled {
                color: #94a3b8;
            }
            QCheckBox {
                background: transparent;
            }
            QLineEdit, QComboBox, QDoubleSpinBox {
                background: #ffffff;
                border: 1px solid #d9dee8;
                padding: 8px 10px;
                border-radius: 12px;
            }
            QLineEdit#LyricsKeywordEdit, QComboBox#LyricsSourceCombo, QComboBox#LyricsPreviewModeCombo {
                background: #FFFFFF;
                border: 1px solid #CBD5E1;
                border-radius: 8px;
                padding: 8px 12px;
                min-height: 24px;
                color: #111827;
            }
            QLineEdit#LyricsKeywordEdit:hover, QComboBox#LyricsSourceCombo:hover, QComboBox#LyricsPreviewModeCombo:hover {
                border-color: #B6C2D2;
                background: #FBFCFE;
            }
            QLineEdit#LyricsKeywordEdit:focus, QComboBox#LyricsSourceCombo:focus, QComboBox#LyricsPreviewModeCombo:focus {
                border: 1px solid #D87886;
                background: #FFFFFF;
            }
            QScrollBar:vertical {
                background: transparent;
                border: 0;
                width: 12px;
                margin: 4px 0 4px 0;
            }
            QScrollBar:horizontal {
                background: transparent;
                border: 0;
                height: 12px;
                margin: 0 4px 0 4px;
            }
            QScrollBar::handle:vertical {
                background: #cbd3df;
                border-radius: 6px;
                min-height: 48px;
                margin: 2px;
            }
            QScrollBar::handle:horizontal {
                background: #cbd3df;
                border-radius: 6px;
                min-width: 48px;
                margin: 2px;
            }
            QScrollBar::handle:vertical:hover {
                background: #aeb8c8;
            }
            QScrollBar::handle:horizontal:hover {
                background: #aeb8c8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0;
                background: transparent;
                border: 0;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0;
                background: transparent;
                border: 0;
            }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
                background: transparent;
            }
            QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
                background: transparent;
            }
            """
        )

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("AppRoot")
        shell = QVBoxLayout(central)
        shell.setContentsMargins(24, 20, 24, 16)
        shell.setSpacing(12)

        self.workflow_stepper = WorkflowStepper(WORKFLOW_STEPS, self)
        self.workflow_stepper.stepClicked.connect(self._handle_workflow_step_clicked)

        workflow_bar = CardWidget(radius=10, padding=(12, 8, 12, 8), spacing=0)
        workflow_bar.setObjectName("WorkflowBar")
        workflow_bar.setFixedHeight(80)
        workflow_bar_layout = workflow_bar.createHBoxLayout()
        workflow_bar_layout.setContentsMargins(10, 8, 10, 8)
        workflow_bar_layout.setSpacing(10)
        workflow_bar_layout.addWidget(self.workflow_stepper, 1)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("PageStack")
        self.video_download_page = VideoDownloadPage(self.settings, self._save_all_settings, self)
        self.align_page = self._build_alignment_page()
        self.lyrics_page = self._build_lyrics_page()
        self.lyrics_timing_page = PlaceholderPage(
            title="歌词打轴",
            description="根据歌词内容与音频节奏进行逐句或逐字时间轴制作。",
        )
        self.subtitle_render_page = PlaceholderPage(
            title="字幕视频生成",
            description="将已完成时间轴和样式设置渲染为字幕视频输出。",
        )
        self.hires_page = self._build_hires_page()
        self.module_pages = {
            WORKFLOW_VIDEO_DOWNLOAD: self.video_download_page,
            WORKFLOW_WAVEFORM_ALIGN: self.align_page,
            WORKFLOW_LYRICS_SEARCH: self.lyrics_page,
            WORKFLOW_LYRICS_TIMING: self.lyrics_timing_page,
            WORKFLOW_SUBTITLE_RENDER: self.subtitle_render_page,
            WORKFLOW_HIRES_MIX: self.hires_page,
        }
        self.page_stack.addWidget(self.video_download_page)
        self.page_stack.addWidget(self.align_page)
        self.page_stack.addWidget(self.lyrics_page)
        self.page_stack.addWidget(self.lyrics_timing_page)
        self.page_stack.addWidget(self.subtitle_render_page)
        self.page_stack.addWidget(self.hires_page)

        shell.addWidget(workflow_bar)
        shell.addWidget(self.page_stack, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("准备就绪")
        self._show_module(WORKFLOW_VIDEO_DOWNLOAD)

    def _show_module(self, module_id: str) -> None:
        if module_id not in self.module_pages:
            return
        previous_module = self.active_module
        if (
            previous_module == WORKFLOW_WAVEFORM_ALIGN
            and module_id != WORKFLOW_WAVEFORM_ALIGN
            and getattr(self, "align_preview_process", None) is not None
            and self.align_preview_process.is_running()
        ):
            self._stop_alignment_preview()
        self.active_module = module_id
        self.page_stack.setCurrentWidget(self.module_pages[module_id])
        self.workflow_stepper.setCurrentModule(module_id)
        current_step = next((step for step in WORKFLOW_STEPS if step.module_id == module_id), None)
        if current_step is not None:
            self.statusBar().showMessage(f"当前模块：{current_step.number}. {current_step.title}")

    def _handle_workflow_step_clicked(self, index: int) -> None:
        self._show_module(self.workflow_stepper.moduleIdAt(index))

    def _save_all_settings(self) -> Path:
        self.settings.output_name_mode = self.output_name_mode_value
        self.settings.on_name_template = self.on_name_template_value
        self.settings.off_name_template = self.off_name_template_value
        self.settings.align_video_name_template = self.align_video_name_template_value
        self.settings.align_audio_name_template = self.align_audio_name_template_value
        self.settings.ffmpeg_dir = self.ffmpeg_dir_text
        if not self._loading_settings_into_ui:
            self._update_alignment_preferences_from_ui()
        return save_app_settings(self.settings)

    def _bind_shortcuts(self) -> None:
        self.shortcut_space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.shortcut_space.activated.connect(self._handle_align_space_shortcut)
        self.shortcut_export = QShortcut(QKeySequence("Ctrl+S"), self)
        self.shortcut_export.activated.connect(self._handle_align_export_shortcut)
        self.shortcut_auto = QShortcut(QKeySequence("Ctrl+D"), self)
        self.shortcut_auto.activated.connect(self._handle_align_auto_shortcut)
        self.shortcut_drag_mode = QShortcut(QKeySequence("Alt+V"), self)
        self.shortcut_drag_mode.activated.connect(self._handle_align_drag_mode_shortcut)

    def _focused_widget_is_text_input(self) -> bool:
        widget = QApplication.focusWidget()
        return isinstance(widget, (QLineEdit, QPlainTextEdit, QComboBox, QDoubleSpinBox))

    def _handle_align_space_shortcut(self) -> None:
        if self.active_module != WORKFLOW_WAVEFORM_ALIGN or self._focused_widget_is_text_input():
            return
        if self.align_preview_process is not None and self.align_preview_process.is_running():
            self._stop_alignment_preview()
            return
        if self.waveform_view.video_waveform is not None and self.waveform_view.audio_waveform is not None:
            self._start_alignment_preview()
        else:
            self._start_alignment_analysis()

    def _handle_align_export_shortcut(self) -> None:
        if self.active_module != WORKFLOW_WAVEFORM_ALIGN or self._focused_widget_is_text_input():
            return
        self._start_aligned_export()

    def _handle_align_auto_shortcut(self) -> None:
        if self.active_module != WORKFLOW_WAVEFORM_ALIGN or self._focused_widget_is_text_input():
            return
        self._auto_align_waveforms()

    def _handle_align_drag_mode_shortcut(self) -> None:
        if self.active_module != WORKFLOW_WAVEFORM_ALIGN or self._focused_widget_is_text_input():
            return
        if self.align_drag_pan_radio.isChecked():
            self.align_drag_offset_radio.setChecked(True)
        else:
            self.align_drag_pan_radio.setChecked(True)
        if hasattr(self, "align_drag_mode_button"):
            is_pan = self.align_drag_pan_radio.isChecked()
            self.align_drag_mode_button.setToolTip(
                "当前：平移视图，Alt+V 切换为移动偏移" if is_pan else "当前：移动偏移，Alt+V 切换为平移视图"
            )

    def _build_lyrics_page(self) -> QWidget:
        page = QWidget()
        page.setObjectName("LyricsPage")
        shell = QVBoxLayout(page)
        shell.setContentsMargins(18, 18, 18, 18)
        shell.setSpacing(14)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel("歌词检索")
        title.setObjectName("PageTitle")
        desc = QLabel(
            "输入歌名、歌手、专辑或歌词片段后搜索歌曲；结果会优先保留各来源原始搜索顺位，再用歌名、歌手、专辑等匹配度修正。"
        )
        desc.setObjectName("LyricsPageDescription")
        desc.setWordWrap(True)
        header.addWidget(title)
        header.addWidget(desc)
        shell.addLayout(header)

        search_panel = CardWidget(radius=10, padding=(18, 18, 18, 16), spacing=10)
        search_panel.setObjectName("LyricsSearchPanel")
        search_layout = search_panel.createGridLayout()
        search_layout.setHorizontalSpacing(10)
        search_layout.setVerticalSpacing(8)

        self.lyrics_source_combo = StyledComboBox()
        self.lyrics_source_combo.setObjectName("LyricsSourceCombo")
        self.lyrics_source_combo.addItems([label for label, _provider_ids in LYRICS_SOURCE_OPTIONS])
        self.lyrics_source_combo.setFont(build_lyrics_ui_font(point_size=10.5))
        self.lyrics_source_combo.setFixedWidth(156)
        self.lyrics_source_combo.setFixedHeight(42)
        self._install_single_click_combo_behavior(self.lyrics_source_combo)
        self.lyrics_source_combo.currentIndexChanged.connect(self._persist_lyrics_preferences)

        self.lyrics_keyword_edit = QLineEdit()
        self.lyrics_keyword_edit.setObjectName("LyricsKeywordEdit")
        self.lyrics_keyword_edit.setPlaceholderText("例如：Recollect / Reweave / Redo / Realize")
        self.lyrics_keyword_edit.setMinimumHeight(42)
        self.lyrics_keyword_edit.returnPressed.connect(self._start_lyrics_search)
        self.lyrics_search_button = PrimaryPushButton("搜索歌曲")
        self.lyrics_search_button.setObjectName("LyricsSearchButton")
        self.lyrics_search_button.setFixedSize(128, 42)
        self.lyrics_search_button.clicked.connect(self._start_lyrics_search)
        self.lyrics_status_label = QLabel("当前支持聚合搜索，也可以手动切换到 QQ音乐、酷狗音乐、网易云音乐或 LRCLIB 单源搜索。")
        self.lyrics_status_label.setObjectName("LyricsStatusText")
        self.lyrics_status_label.setWordWrap(True)
        self.lyrics_status_label.setFont(build_lyrics_ui_font(point_size=9.5))
        search_layout.addWidget(self.lyrics_source_combo, 0, 0)
        search_layout.addWidget(self.lyrics_keyword_edit, 0, 1)
        search_layout.addWidget(self.lyrics_search_button, 0, 2)
        search_layout.addWidget(self.lyrics_status_label, 1, 0, 1, 3)
        search_layout.setColumnStretch(1, 1)
        shell.addWidget(search_panel)

        content = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(14)
        self.lyrics_content_layout = content

        result_panel = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=12)
        result_panel.setObjectName("LyricsResultPanel")
        result_layout = result_panel.createVBoxLayout()
        result_title = QLabel("匹配结果")
        result_title.setObjectName("PanelTitle")
        self.lyrics_results_summary_label = QLabel("还没有搜索结果。")
        self.lyrics_results_summary_label.setObjectName("LyricsResultsSummary")
        self.lyrics_results_summary_label.setFont(build_lyrics_ui_font(point_size=9.5))
        self.lyrics_results_table = QTableWidget()
        self.lyrics_results_table.setRowCount(0)
        self.lyrics_results_table.setColumnCount(5)
        self.lyrics_results_table.setObjectName("LyricsResultsTable")
        self.lyrics_results_table.setHorizontalHeaderLabels(["歌曲", "艺术家", "专辑", "时长", "来源"])
        self.lyrics_results_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.lyrics_results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.lyrics_results_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.lyrics_results_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lyrics_results_table.setAlternatingRowColors(False)
        self.lyrics_results_table.setShowGrid(False)
        self.lyrics_results_table.setMouseTracking(True)
        self.lyrics_results_table.viewport().setMouseTracking(True)
        self.lyrics_results_table.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
        self.lyrics_results_table.setWordWrap(False)
        self.lyrics_results_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.lyrics_results_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.lyrics_results_table.setFont(build_lyrics_ui_font(point_size=10.5))
        self.lyrics_results_table.verticalHeader().setVisible(False)
        self.lyrics_results_table.verticalHeader().setDefaultSectionSize(50)
        self.lyrics_results_table.horizontalHeader().setStretchLastSection(False)
        self.lyrics_results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.lyrics_results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.lyrics_results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.lyrics_results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.lyrics_results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.lyrics_results_table.setItemDelegate(LyricsResultsDelegate(self.lyrics_results_table))
        self.lyrics_results_table.installEventFilter(self)
        self.lyrics_results_table.currentCellChanged.connect(self._handle_lyrics_result_selected)
        self.lyrics_results_table.verticalScrollBar().valueChanged.connect(self._maybe_load_more_lyrics_results)
        result_layout.addWidget(result_title)
        result_layout.addWidget(self.lyrics_results_summary_label)
        result_layout.addWidget(self.lyrics_results_table, 1)
        QTimer.singleShot(0, self._resize_lyrics_results_columns)
        content.addWidget(result_panel, 7)

        preview_panel = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=12)
        preview_panel.setObjectName("LyricsPreviewPanel")
        preview_layout = preview_panel.createVBoxLayout()
        preview_header = QHBoxLayout()
        preview_header.setContentsMargins(0, 0, 0, 0)
        preview_header.setSpacing(8)
        preview_title = QLabel("歌词预览")
        preview_title.setObjectName("PanelTitle")
        preview_header.addWidget(preview_title)
        preview_header.addStretch(1)
        self.copy_lyrics_button = QPushButton("复制歌词")
        self.copy_lyrics_button.setObjectName("LyricsCopyButton")
        self.copy_lyrics_button.clicked.connect(self._copy_current_lyrics_preview)
        self.copy_lyrics_button.setFixedHeight(36)
        preview_header.addWidget(self.copy_lyrics_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lyrics_strip_intro_checkbox = QCheckBox("省略歌曲介绍")
        self.lyrics_strip_intro_checkbox.setObjectName("LyricsStripIntroCheck")
        self.lyrics_strip_intro_checkbox.setMinimumHeight(36)
        self.lyrics_strip_intro_checkbox.setChecked(True)
        self.lyrics_strip_intro_checkbox.toggled.connect(lambda _: self._refresh_lyrics_preview())
        self.lyrics_strip_intro_checkbox.toggled.connect(self._persist_lyrics_preferences)
        preview_header.addSpacing(12)
        preview_header.addWidget(self.lyrics_strip_intro_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lyrics_language_combo = StyledComboBox()
        self.lyrics_language_combo.setObjectName("LyricsLanguageCombo")
        self.lyrics_language_combo.addItems([label for label, _value in LYRICS_LANGUAGE_OPTIONS])
        self.lyrics_language_combo.setFixedWidth(112)
        self.lyrics_language_combo.setFixedHeight(36)
        self.lyrics_language_combo.setToolTip("切换原文 / 中文译文（无译文时禁用）")
        self.lyrics_language_combo.currentIndexChanged.connect(lambda _: self._refresh_lyrics_preview())
        self.lyrics_language_combo.currentIndexChanged.connect(self._persist_lyrics_preferences)
        self._install_single_click_combo_behavior(self.lyrics_language_combo)
        preview_header.addWidget(self.lyrics_language_combo, 0, Qt.AlignmentFlag.AlignVCenter)
        self.lyrics_preview_mode_combo = StyledComboBox()
        self.lyrics_preview_mode_combo.setObjectName("LyricsPreviewModeCombo")
        self.lyrics_preview_mode_combo.addItems([label for label, _mode in LYRICS_PREVIEW_MODE_OPTIONS])
        self.lyrics_preview_mode_combo.setFixedWidth(112)
        self.lyrics_preview_mode_combo.setFixedHeight(36)
        self.lyrics_preview_mode_combo.currentIndexChanged.connect(lambda _: self._refresh_lyrics_preview())
        self.lyrics_preview_mode_combo.currentIndexChanged.connect(self._persist_lyrics_preferences)
        self._install_single_click_combo_behavior(self.lyrics_preview_mode_combo)
        preview_header.addWidget(self.lyrics_preview_mode_combo, 0, Qt.AlignmentFlag.AlignVCenter)

        self.lyrics_preview_title_label = QLabel("未选择歌曲")
        self.lyrics_preview_title_label.setObjectName("LyricsPreviewTitle")
        self.lyrics_preview_title_label.setWordWrap(True)
        self.lyrics_preview_title_label.setFont(build_lyrics_ui_font(point_size=14, bold=True))
        self.lyrics_preview_meta_label = QLabel("来源: -")
        self.lyrics_preview_meta_label.setObjectName("LyricsPreviewMeta")
        self.lyrics_preview_meta_label.setWordWrap(True)
        self.lyrics_preview_meta_label.setFont(build_lyrics_ui_font(point_size=10.5))
        self.lyrics_match_summary_label = QLabel("匹配字段: -")
        self.lyrics_match_summary_label.setObjectName("LyricsMatchSummary")
        self.lyrics_match_summary_label.setWordWrap(True)
        self.lyrics_match_summary_label.setFont(build_lyrics_ui_font(point_size=9.5))
        self.lyrics_preview_hint_label = QLabel("搜索后选择一首歌，即可查看逐行或按字的 LRC 预览。")
        self.lyrics_preview_hint_label.setObjectName("LyricsPreviewHint")
        self.lyrics_preview_hint_label.setWordWrap(True)
        self.lyrics_preview_hint_label.setFont(build_lyrics_ui_font(point_size=9.5))
        self.lyrics_preview_edit = QPlainTextEdit()
        self.lyrics_preview_edit.setReadOnly(True)
        self.lyrics_preview_edit.setObjectName("LyricsPreviewText")
        self.lyrics_preview_edit.setFont(build_lyrics_ui_font(point_size=11))
        self.lyrics_preview_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.lyrics_preview_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.lyrics_preview_edit.setPlaceholderText("歌词会显示在这里。")
        self.lyrics_preview_edit.setTabStopDistance(QFontMetrics(self.lyrics_preview_edit.font()).horizontalAdvance(" ") * 4)

        preview_layout.addLayout(preview_header)
        preview_layout.addWidget(self.lyrics_preview_title_label)
        preview_layout.addWidget(self.lyrics_preview_meta_label)
        preview_layout.addWidget(self.lyrics_match_summary_label)
        preview_layout.addWidget(self.lyrics_preview_hint_label)
        preview_layout.addWidget(self.lyrics_preview_edit, 1)
        content.addWidget(preview_panel, 6)

        shell.addLayout(content, 1)
        self._refresh_lyrics_layout_direction()
        self._clear_lyrics_results()
        return page

    def _start_lyrics_search(self, *, load_more: bool = False) -> None:
        if self.lyrics_search_task is not None and self.lyrics_search_task.isRunning():
            return
        if load_more and not self.lyrics_has_more_results:
            return
        keyword = self.lyrics_search_keyword if load_more else self.lyrics_keyword_edit.text().strip()
        if not keyword:
            QMessageBox.information(self, APP_TITLE, "请输入搜索关键词。")
            return

        self.lyrics_search_button.setEnabled(False)
        provider_ids = self.lyrics_search_provider_ids if load_more else self._current_lyrics_source_ids()
        if load_more:
            self._lyrics_loading_more = True
            selected_key = self.lyrics_selected_candidate.key if self.lyrics_selected_candidate is not None else ""
            self._render_lyrics_results_table(selected_key=selected_key)
            self.lyrics_status_label.setText(f"已加载 {len(self.lyrics_search_results)} 条结果，正在加载更多…")
        else:
            self._lyrics_loading_more = False
            self.lyrics_status_label.setText("正在搜索歌词候选歌曲…")
            self.lyrics_search_keyword = keyword
            self.lyrics_search_provider_ids = provider_ids
            self.lyrics_next_provider_pages = {}
            self.lyrics_has_more_results = False
            self.lyrics_pending_results = []
            self._clear_lyrics_results()

        def runner(logger: Callable[[str], None]) -> tuple[bool, LyricsSearchBatch]:
            _ = logger
            return (
                load_more,
                self.lyrics_search_service.search_batch(
                    keyword,
                    provider_ids=provider_ids,
                    limit=DEFAULT_LYRICS_SEARCH_LIMIT,
                    provider_pages=self.lyrics_next_provider_pages if load_more else None,
                ),
            )

        task = self._track_background_task("lyrics_search_task", BackgroundTask(runner))
        task.task_succeeded.connect(self._finish_lyrics_search_success)
        task.task_failed.connect(self._finish_lyrics_search_failure)
        task.start()

    def _finish_lyrics_search_success(self, results: object) -> None:
        self.lyrics_search_button.setEnabled(True)
        load_more = False
        payload = results
        if isinstance(results, tuple) and len(results) == 2 and isinstance(results[0], bool):
            load_more = results[0]
            payload = results[1]

        batch = payload if isinstance(payload, LyricsSearchBatch) else None
        batch_results = list(batch.results) if batch is not None else (list(results) if isinstance(results, list) else [])
        if batch is not None:
            self.lyrics_pending_results.extend(batch.overflow_results)
        self.lyrics_next_provider_pages = dict(batch.next_provider_pages) if batch is not None else {}
        self.lyrics_has_more_results = bool(batch.has_more or self.lyrics_pending_results) if batch is not None else False

        if load_more:
            existing_keys = {candidate.key for candidate in self.lyrics_search_results}
            for candidate in batch_results:
                if candidate.key not in existing_keys:
                    self.lyrics_search_results.append(candidate)
                    existing_keys.add(candidate.key)
        else:
            self.lyrics_search_results = batch_results

        if not self.lyrics_search_results:
            self.lyrics_status_label.setText("没有找到匹配的歌词结果。")
            self._clear_lyrics_results()
            return

        selected_key = self.lyrics_selected_candidate.key if self.lyrics_selected_candidate is not None else ""
        self._render_lyrics_results_table(selected_key=selected_key if load_more else "")
        selected_source = self.lyrics_source_combo.currentText()
        if selected_source == "聚合":
            self.lyrics_status_label.setText(
                f"已加载 {len(self.lyrics_search_results)} 条候选结果，来源优先级：QQ > 酷狗 > 网易云 > LRCLIB。"
            )
        else:
            self.lyrics_status_label.setText(f"已加载 {len(self.lyrics_search_results)} 条候选结果，当前来源：{selected_source}。")
        self.lyrics_results_summary_label.setText(
            "结果优先保留各来源原始搜索顺位，再按歌曲、艺术家、专辑匹配度修正；同一首歌会保留不同来源。"
            + (" 向下滚动可继续加载更多结果。" if self.lyrics_has_more_results else "")
        )
        self._lyrics_loading_more = False
        self._render_lyrics_results_table(selected_key=selected_key if load_more else "")

    def _finish_lyrics_search_failure(self, message: str) -> None:
        self.lyrics_search_button.setEnabled(True)
        self._lyrics_loading_more = False
        if not self.lyrics_search_results:
            self._clear_lyrics_results()
        else:
            selected_key = self.lyrics_selected_candidate.key if self.lyrics_selected_candidate is not None else ""
            self._render_lyrics_results_table(selected_key=selected_key)
        self.lyrics_status_label.setText("歌词搜索失败。")
        QMessageBox.critical(self, APP_TITLE, message or "歌词搜索失败。")

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.Wheel and self._should_route_alignment_wheel(watched, event):
            self.waveform_view.wheelEvent(event)
            if event.isAccepted():
                self._sync_alignment_zoom_slider()
                return True
            return False
        if (
            hasattr(self, "lyrics_results_table")
            and watched is self.lyrics_results_table
            and event.type() in {QEvent.Type.Resize, QEvent.Type.Show}
        ):
            QTimer.singleShot(0, self._resize_lyrics_results_columns)
        return super().eventFilter(watched, event)

    def _should_route_alignment_wheel(self, watched, event) -> bool:
        if not hasattr(self, "waveform_view"):
            return False
        waveform_view = self.waveform_view
        if not waveform_view.isVisible() or not waveform_view.isEnabled():
            return False
        if waveform_view.video_waveform is None or waveform_view.audio_waveform is None:
            return False
        watched_widgets = (
            waveform_view,
            getattr(self, "align_waveform_stage", None),
            getattr(self, "align_scroll_area", None),
            getattr(self, "align_scroll_viewport", None),
        )
        if not any(watched is widget for widget in watched_widgets if widget is not None):
            return False
        if hasattr(event, "globalPosition"):
            global_pos = event.globalPosition().toPoint()
        else:
            local_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            global_pos = watched.mapToGlobal(local_pos)
        return waveform_view.rect().contains(waveform_view.mapFromGlobal(global_pos))

    def _sync_alignment_zoom_slider(self) -> None:
        if hasattr(self, "align_zoom_slider"):
            self.align_zoom_slider.blockSignals(True)
            self.align_zoom_slider.setValue(int(round(self.waveform_view.pixels_per_second)))
            self.align_zoom_slider.blockSignals(False)

    def _resize_lyrics_results_columns(self) -> None:
        viewport_width = self.lyrics_results_table.viewport().width()
        if viewport_width <= 0:
            return

        duration_width = 92
        source_width = 96
        remaining = max(120, viewport_width - duration_width - source_width)
        song_width = int(remaining * 0.36)
        artist_width = int(remaining * 0.27)
        album_width = max(0, remaining - song_width - artist_width)

        self.lyrics_results_table.setColumnWidth(0, song_width)
        self.lyrics_results_table.setColumnWidth(1, artist_width)
        self.lyrics_results_table.setColumnWidth(2, album_width)
        self.lyrics_results_table.setColumnWidth(3, duration_width)
        self.lyrics_results_table.setColumnWidth(4, source_width)

    def _render_lyrics_results_table(self, *, selected_key: str = "") -> None:
        row_count = len(self.lyrics_search_results) + (1 if self._lyrics_loading_more and self.lyrics_search_results else 0)
        self.lyrics_results_table.clearSpans()
        self.lyrics_results_table.setRowCount(row_count)
        self._resize_lyrics_results_columns()
        selected_row = -1
        for row, candidate in enumerate(self.lyrics_search_results):
            duration_text = format_media_duration(candidate.duration_seconds) if candidate.duration_seconds else "-"
            items = [
                QTableWidgetItem(candidate.title or "-"),
                QTableWidgetItem(candidate.artist or "-"),
                QTableWidgetItem(candidate.album or "-"),
                QTableWidgetItem(duration_text),
                QTableWidgetItem(candidate.provider_name),
            ]
            for column, item in enumerate(items):
                item.setData(Qt.ItemDataRole.UserRole, row)
                item.setFont(build_lyrics_ui_font(point_size=10.5, bold=(column == 0)))
                if column in (1, 2):
                    item.setForeground(QBrush(QColor("#64748B")))
                if column == 3:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    item.setForeground(QBrush(QColor("#475569")))
                elif column == 4:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setFont(build_lyrics_ui_font(point_size=9.5, bold=True))
                    item.setForeground(QBrush(QColor("#B94D5D")))
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.lyrics_results_table.setItem(row, column, item)
            if selected_key and candidate.key == selected_key:
                selected_row = row

        if self._lyrics_loading_more and self.lyrics_search_results:
            loading_row = len(self.lyrics_search_results)
            loading_item = QTableWidgetItem("加载中...")
            loading_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            loading_item.setFont(build_lyrics_ui_font(point_size=9.5))
            loading_item.setForeground(QBrush(QColor("#64748B")))
            loading_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.lyrics_results_table.setSpan(loading_row, 0, 1, self.lyrics_results_table.columnCount())
            self.lyrics_results_table.setItem(loading_row, 0, loading_item)

        if selected_row < 0 and self.lyrics_search_results:
            selected_row = 0
        if selected_row >= 0:
            self.lyrics_results_table.selectRow(selected_row)
            self._handle_lyrics_result_selected(selected_row, 0, -1, -1)

    def _maybe_load_more_lyrics_results(self) -> None:
        if not self.lyrics_has_more_results or self._lyrics_loading_more:
            return
        if self.lyrics_search_task is not None and self.lyrics_search_task.isRunning():
            return
        scrollbar = self.lyrics_results_table.verticalScrollBar()
        if scrollbar.maximum() <= 0:
            return
        if scrollbar.value() < scrollbar.maximum() - 12:
            return
        if not self.lyrics_search_keyword:
            return
        if self.lyrics_pending_results:
            self._append_pending_lyrics_results()
            return
        self._start_lyrics_search(load_more=True)

    def _append_pending_lyrics_results(self) -> None:
        if not self.lyrics_pending_results:
            self.lyrics_has_more_results = bool(self.lyrics_next_provider_pages)
            return
        selected_key = self.lyrics_selected_candidate.key if self.lyrics_selected_candidate is not None else ""
        chunk = self.lyrics_pending_results[:DEFAULT_LYRICS_SEARCH_LIMIT]
        self.lyrics_pending_results = self.lyrics_pending_results[DEFAULT_LYRICS_SEARCH_LIMIT:]
        existing_keys = {candidate.key for candidate in self.lyrics_search_results}
        for candidate in chunk:
            if candidate.key not in existing_keys:
                self.lyrics_search_results.append(candidate)
                existing_keys.add(candidate.key)
        self.lyrics_has_more_results = bool(self.lyrics_pending_results or self.lyrics_next_provider_pages)
        self._render_lyrics_results_table(selected_key=selected_key)
        selected_source = self.lyrics_source_combo.currentText()
        if selected_source == "聚合":
            self.lyrics_status_label.setText(
                f"已加载 {len(self.lyrics_search_results)} 条候选结果，来源优先级：QQ > 酷狗 > 网易云 > LRCLIB。"
            )
        else:
            self.lyrics_status_label.setText(f"已加载 {len(self.lyrics_search_results)} 条候选结果，当前来源：{selected_source}。")
        self.lyrics_results_summary_label.setText(
            "结果优先保留各来源原始搜索顺位，再按歌曲、艺术家、专辑匹配度修正；同一首歌会保留不同来源。"
            + (" 向下滚动可继续加载更多结果。" if self.lyrics_has_more_results else "")
        )

    def _handle_lyrics_result_selected(
        self,
        current_row: int,
        current_column: int,
        previous_row: int,
        previous_column: int,
    ) -> None:
        _ = current_column, previous_row, previous_column
        if current_row < 0 or current_row >= len(self.lyrics_search_results):
            self.lyrics_selected_candidate = None
            self._refresh_lyrics_preview()
            return
        self.lyrics_selected_candidate = self.lyrics_search_results[current_row]
        self._ensure_selected_lyrics_loaded()
        self._refresh_lyrics_preview()

    def _refresh_lyrics_preview(self) -> None:
        candidate = self.lyrics_selected_candidate
        self._update_lyrics_language_combo_state(candidate)
        if candidate is None:
            self.lyrics_preview_title_label.setText("未选择歌曲")
            self.lyrics_preview_meta_label.setText("来源: -")
            self.lyrics_match_summary_label.setText("匹配字段: -")
            self.lyrics_preview_hint_label.setText("搜索后选择一首歌，即可查看逐行或按字的 LRC 预览。")
            self.lyrics_preview_edit.clear()
            return

        if candidate.load_error:
            self.lyrics_preview_title_label.setText(f"{candidate.title or '未命名'}")
            self.lyrics_preview_meta_label.setText(
                f"歌手: {candidate.artist or '-'}    专辑: {candidate.album or '-'}    来源: {candidate.provider_name}"
            )
            self.lyrics_match_summary_label.setText("歌词加载失败")
            self.lyrics_preview_hint_label.setText(candidate.load_error)
            self.lyrics_preview_edit.setPlainText(candidate.load_error)
            return

        if not candidate.lyrics_loaded:
            self.lyrics_preview_title_label.setText(f"{candidate.title or '未命名'}")
            self.lyrics_preview_meta_label.setText(
                f"歌手: {candidate.artist or '-'}    专辑: {candidate.album or '-'}    来源: {candidate.provider_name}"
            )
            self.lyrics_match_summary_label.setText(
                "匹配字段: "
                f"{candidate.match_source}；歌名 {candidate.title_score:.0f} / "
                f"歌手 {candidate.artist_score:.0f} / 专辑 {candidate.album_score:.0f}"
            )
            self.lyrics_preview_hint_label.setText(f"正在从 {candidate.provider_name} 加载歌词…")
            self.lyrics_preview_edit.setPlainText("正在加载歌词…")
            return

        preview_mode = self._current_lyrics_preview_mode()
        language = self._current_lyrics_language()
        preview = build_lyrics_preview(
            candidate,
            preview_mode,
            strip_intro_lines=self.lyrics_strip_intro_checkbox.isChecked(),
            language=language,
        )
        self.lyrics_preview_title_label.setText(f"{candidate.title or '未命名'}")
        self.lyrics_preview_meta_label.setText(
            f"歌手: {candidate.artist or '-'}    专辑: {candidate.album or '-'}    来源: {candidate.provider_name}"
        )
        self.lyrics_match_summary_label.setText(
            "匹配字段: "
            f"{candidate.match_source}；歌名 {candidate.title_score:.0f} / "
            f"歌手 {candidate.artist_score:.0f} / 专辑 {candidate.album_score:.0f} / "
            f"歌词 {candidate.lyrics_score:.0f}"
        )
        self.lyrics_preview_hint_label.setText(self._build_lyrics_preview_hint(candidate, preview))
        self.lyrics_preview_edit.setPlainText(preview.text or "当前结果没有可显示的歌词。")

    def _build_lyrics_preview_hint(self, candidate: LyricsSearchCandidate, preview: LyricsPreview) -> str:
        if preview.used_synced_lyrics and preview.used_estimated_char_timing:
            return (
                f"{candidate.provider_name} 提供了逐行同步歌词；当前“按字 LRC”是基于相邻行时间做的轻量估算，"
                "方便先预览卡拉 OK 节奏。"
            )
        if preview.used_synced_lyrics:
            return f"{candidate.provider_name} 提供了同步歌词，当前优先显示这个来源的字幕。"
        return f"{candidate.provider_name} 当前只有纯文本歌词，暂时无法提供真实时间轴。"

    def _copy_current_lyrics_preview(self) -> None:
        clipboard = QApplication.clipboard()
        if clipboard is None:
            return
        clipboard.setText(self.lyrics_preview_edit.toPlainText())
        QToolTip.showText(
            self.copy_lyrics_button.mapToGlobal(self.copy_lyrics_button.rect().center()),
            "歌词已复制到剪切板",
            self.copy_lyrics_button,
            self.copy_lyrics_button.rect(),
            1600,
        )

    def _ensure_selected_lyrics_loaded(self) -> None:
        candidate = self.lyrics_selected_candidate
        if candidate is None or candidate.lyrics_loaded:
            return
        if self.lyrics_fetch_task is not None and self.lyrics_fetch_task.isRunning():
            return

        self._lyrics_loading_key = candidate.key

        def runner(logger: Callable[[str], None]) -> LyricsSearchCandidate:
            _ = logger
            return self.lyrics_search_service.fetch_lyrics(candidate)

        task = self._track_background_task("lyrics_fetch_task", BackgroundTask(runner))
        task.task_succeeded.connect(self._finish_lyrics_fetch_success)
        task.task_failed.connect(self._finish_lyrics_fetch_failure)
        task.start()

    def _finish_lyrics_fetch_success(self, result: object) -> None:
        self._lyrics_loading_key = ""
        loaded_candidate = result if isinstance(result, LyricsSearchCandidate) else None
        if loaded_candidate is not None:
            for index, candidate in enumerate(self.lyrics_search_results):
                if candidate.key == loaded_candidate.key:
                    self.lyrics_search_results[index] = loaded_candidate
                    if self.lyrics_selected_candidate is not None and self.lyrics_selected_candidate.key == loaded_candidate.key:
                        self.lyrics_selected_candidate = loaded_candidate
                    break
        self._refresh_lyrics_preview()
        if self.lyrics_selected_candidate is not None and not self.lyrics_selected_candidate.lyrics_loaded:
            QTimer.singleShot(0, self._ensure_selected_lyrics_loaded)

    def _finish_lyrics_fetch_failure(self, message: str) -> None:
        failed_key = self._lyrics_loading_key
        self._lyrics_loading_key = ""
        for candidate in self.lyrics_search_results:
            if candidate.key == failed_key:
                candidate.load_error = message or f"{candidate.provider_name} 歌词加载失败。"
                if self.lyrics_selected_candidate is not None and self.lyrics_selected_candidate.key == failed_key:
                    self.lyrics_selected_candidate = candidate
                break
        self._refresh_lyrics_preview()
        if self.lyrics_selected_candidate is not None and not self.lyrics_selected_candidate.lyrics_loaded and not self.lyrics_selected_candidate.load_error:
            QTimer.singleShot(0, self._ensure_selected_lyrics_loaded)

    def _clear_lyrics_results(self) -> None:
        self.lyrics_search_results = []
        self.lyrics_pending_results = []
        self.lyrics_selected_candidate = None
        self.lyrics_next_provider_pages = {}
        self.lyrics_has_more_results = False
        self._lyrics_loading_more = False
        self._lyrics_loading_key = ""
        self.lyrics_results_table.clearContents()
        self.lyrics_results_table.setRowCount(0)
        self.lyrics_results_summary_label.setText("还没有搜索结果。")
        self._refresh_lyrics_preview()

    def _build_hires_page(self) -> QWidget:
        page = QWidget()
        shell = QVBoxLayout(page)
        shell.setContentsMargins(20, 20, 20, 20)
        shell.setSpacing(16)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        title = QLabel("卡拉 OK 字幕视频一键 Hi-Res 生成")
        title.setObjectName("PageTitle")
        desc = QLabel("把字幕视频拖进下方卡片，再按需放入原唱音频和 / 或伴奏音频。至少提供一条音频就可以开始生成。")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #475467; font-size: 10.5pt;")
        header.addWidget(title)
        header.addWidget(desc)
        shell.addLayout(header)

        settings_card = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=10)
        apply_card_shadow(settings_card)
        settings_layout = settings_card.createGridLayout()
        settings_layout.setHorizontalSpacing(14)
        settings_layout.setVerticalSpacing(10)
        output_label = QLabel("输出目录")
        output_label.setStyleSheet('font-size: 11pt; font-weight: 400; color: #475467;')
        self.output_dir_label = QLabel("跟随字幕视频所在目录")
        self.output_dir_label.setWordWrap(True)
        self.output_dir_label.setStyleSheet('font-size: 11pt; color: #1f2937; font-weight: 500;')
        ffmpeg_title = QLabel("FFmpeg 目录 ⓘ")
        ffmpeg_title.setToolTip('FFmpeg 目录、输出命名等偏好设置可在"设置"窗口中调整并保存到本地。')
        ffmpeg_title.setStyleSheet('font-size: 11pt; font-weight: 400; color: #475467;')
        self.hires_ffmpeg_label = QLabel(FFMPEG_DIR_PLACEHOLDER)
        self.hires_ffmpeg_label.setWordWrap(True)
        settings_button = QPushButton("⚙ 设置")
        settings_button.setStyleSheet(
            """
            QPushButton {
                background: transparent;
                border: 1px solid #D5DCE6;
                border-radius: 6px;
                padding: 6px 14px;
                color: #475467;
                font-size: 10.5pt;
            }
            QPushButton:hover {
                background: #F2F4F8;
            }
            """
        )
        settings_button.clicked.connect(lambda: self._open_settings_window("hires"))
        settings_layout.addWidget(output_label, 0, 0)
        settings_layout.addWidget(self.output_dir_label, 0, 1)
        settings_layout.addWidget(ffmpeg_title, 1, 0)
        settings_layout.addWidget(self.hires_ffmpeg_label, 1, 1)
        settings_layout.addWidget(settings_button, 1, 2)
        settings_layout.setColumnStretch(1, 1)
        shell.addWidget(settings_card)

        card_row = QHBoxLayout()
        card_row.setContentsMargins(0, 0, 0, 0)
        card_row.setSpacing(12)
        self.video_zone = DropZoneCard(
            title="字幕视频",
            hint="支持 mkv / mp4 / mov / avi\n这里会决定输出文件名和输出目录。",
            extensions=VIDEO_EXTENSIONS,
            min_height=190,
            icon_text="🎬",
            placeholder_icon="🎞",
            accent_bg="#EEF4FF",
        )
        self.video_zone.browseRequested.connect(self._choose_video)
        self.video_zone.pathChanged.connect(self.set_video_path)

        self.on_vocal_zone = DropZoneCard(
            title="原唱音频",
            hint="支持 flac / wav / mp3 / m4a / aac / ape / alac / mkv / mp4\n可单独生成原唱 Hi-Res 视频，也可和伴奏一起生成。",
            extensions=HIRES_AUDIO_EXTENSIONS,
            min_height=190,
            icon_text="🎤",
            placeholder_icon="🎙",
            accent_bg="#F3EEFF",
        )
        self.on_vocal_zone.browseRequested.connect(self._choose_on_audio)
        self.on_vocal_zone.pathChanged.connect(self.set_on_vocal_path)

        self.off_vocal_zone = DropZoneCard(
            title="伴奏音频",
            hint="支持 flac / wav / mp3 / m4a / aac / ape / alac / mkv / mp4\n可单独生成伴奏 Hi-Res 视频，也可和原唱一起生成。",
            extensions=HIRES_AUDIO_EXTENSIONS,
            min_height=190,
            icon_text="🎵",
            placeholder_icon="♪",
            accent_bg="#EAF7F4",
        )
        self.off_vocal_zone.browseRequested.connect(self._choose_off_audio)
        self.off_vocal_zone.pathChanged.connect(self.set_off_vocal_path)
        for drop_zone in (self.video_zone, self.on_vocal_zone, self.off_vocal_zone):
            apply_card_shadow(drop_zone)

        card_row.addWidget(self.video_zone, 1)
        card_row.addWidget(self.on_vocal_zone, 1)
        card_row.addWidget(self.off_vocal_zone, 1)
        shell.addLayout(card_row)

        log_panel = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=12)
        apply_card_shadow(log_panel)
        log_layout = log_panel.createGridLayout()
        log_layout.setVerticalSpacing(12)
        log_title = QLabel("处理日志")
        log_title.setObjectName("PanelTitle")
        log_button_style = """
            QPushButton {
                background: transparent;
                border: 0;
                border-radius: 6px;
                color: #475467;
                font-size: 12pt;
            }
            QPushButton:hover {
                background: #F2F4F8;
            }
        """
        copy_log_btn = QPushButton("📋")
        copy_log_btn.setFixedSize(28, 28)
        copy_log_btn.setToolTip("复制全部日志")
        copy_log_btn.setStyleSheet(log_button_style)
        copy_log_btn.clicked.connect(self._copy_hires_log)
        clear_log_btn = QPushButton("🗑")
        clear_log_btn.setFixedSize(28, 28)
        clear_log_btn.setToolTip("清空日志")
        clear_log_btn.setStyleSheet(log_button_style)
        self.hires_log = QPlainTextEdit()
        self.hires_log.setObjectName("LogText")
        self.hires_log.setReadOnly(True)
        clear_log_btn.clicked.connect(self.hires_log.clear)
        self.hires_log.setPlaceholderText("运行后将在此显示 FFmpeg 输出与处理进度...")
        self.hires_log.setStyleSheet(
            """
            QPlainTextEdit#LogText {
                background: #FAFBFC;
                border: 1px solid #E4E7EC;
                border-radius: 8px;
                color: #1f2937;
                font-family: "Consolas", "JetBrains Mono", monospace;
                font-size: 10pt;
                padding: 10px;
            }
            """
        )
        log_layout.addWidget(log_title, 0, 0)
        log_layout.addWidget(copy_log_btn, 0, 1)
        log_layout.addWidget(clear_log_btn, 0, 2)
        log_layout.addWidget(self.hires_log, 1, 0, 1, 3)
        log_layout.setColumnStretch(0, 1)
        log_layout.setRowStretch(1, 1)
        shell.addWidget(log_panel, 1)

        controls_bar = ControlBar()
        controls = controls_bar.createHBoxLayout()
        self.hires_start_button = PrimaryPushButton("▶  开始生成")
        self.hires_start_button.clicked.connect(self._start_hires)
        self.hires_cancel_button = QPushButton("■  取消生成")
        self.hires_cancel_button.setEnabled(False)
        self.hires_cancel_button.clicked.connect(self._stop_hires)
        clear_button = QPushButton("✕  清空已选文件")
        clear_button.clicked.connect(self._clear_hires_inputs)
        open_output_button = QPushButton("📁  打开输出目录")
        open_output_button.clicked.connect(self._open_hires_output_dir)
        self.hires_progress = QProgressBar()
        self.hires_progress.setRange(0, 1)
        self.hires_progress.setValue(0)
        self.hires_progress.setFixedWidth(220)
        self.hires_progress.setFixedHeight(10)
        self.hires_progress.setTextVisible(True)
        self.hires_progress.setStyleSheet(
            """
            QProgressBar {
                border: 0;
                border-radius: 5px;
                background: #E5E7EB;
                text-align: center;
                color: transparent;
            }
            QProgressBar::chunk {
                background: #2f6fed;
                border-radius: 5px;
            }
            """
        )
        self.hires_status_label = QLabel("准备就绪")
        self._set_hires_status_color("#475467")
        controls.addWidget(self.hires_start_button)
        controls.addWidget(self.hires_cancel_button)
        controls.addWidget(clear_button)
        controls.addWidget(open_output_button)
        controls.addStretch(1)
        controls.addWidget(self.hires_progress)
        controls.addSpacing(12)
        controls.addWidget(self.hires_status_label)
        controls_bar.apply_button_metrics(self.hires_start_button, self.hires_cancel_button, clear_button, open_output_button)
        shell.addWidget(controls_bar)
        return page

    def _build_alignment_page(self) -> QWidget:
        from PyQt6.QtCore import QSize

        class AlignmentInfoLabel(BodyLabel):
            def __init__(self, owner: "KrokHelperQtApp", text: str = "", parent: QWidget | None = None) -> None:
                super().__init__(parent)
                self._owner = owner
                QLabel.setText(self, text)

            def setText(self, text: str) -> None:  # noqa: N802
                super().setText(text)
                if hasattr(self._owner, "align_video_export_duration_label"):
                    self._owner._refresh_alignment_export_panels()

        class AlignmentDropCard(CardWidget):
            pathChanged = Signal(Path)
            browseRequested = Signal()
            removeRequested = Signal()

            def __init__(
                self,
                *,
                owner: "KrokHelperQtApp",
                title: str,
                media_label: str,
                hint: str,
                extensions: set[str],
                icon: FIF,
                theme: str,
                parent: QWidget | None = None,
            ) -> None:
                super().__init__(parent, radius=18, padding=(16, 16, 16, 16), spacing=12)
                self._owner = owner
                self.extensions = {ext.lower() for ext in extensions}
                self.path: Path | None = None
                self._hovered = False
                self._drag_state = "idle"
                self._theme = theme
                self._display_mode = "empty"
                self._balanced_height: int | None = None
                self._missing_text = ""
                self._media_label = media_label
                self._icon = icon
                self._default_action_text = "点击选择文件，或直接拖拽进入区域"
                self._empty_detail_text = f"{media_label}: 时长未知"
                self._theme_palette = self._build_theme_palette(theme)

                self.setObjectName("AlignmentDropCard")
                self.setCursor(Qt.CursorShape.PointingHandCursor)
                self.setAcceptDrops(True)
                self.setMinimumWidth(0)
                self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                self.setMinimumHeight(158)

                layout = self.createVBoxLayout()
                layout.setContentsMargins(16, 16, 16, 16)
                layout.setSpacing(12)
                self._main_layout = layout

                header = QHBoxLayout()
                header.setContentsMargins(0, 0, 0, 0)
                header.setSpacing(14)

                self.icon_button = ToolButton(self)
                self.icon_button.setIcon(icon.icon())
                self.icon_button.setIconSize(QSize(34, 34))
                self.icon_button.setFixedSize(68, 68)
                self.icon_button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self.icon_button.setStyleSheet("ToolButton { background: transparent; border: 0; padding: 0; }")

                text_layout = QVBoxLayout()
                text_layout.setContentsMargins(0, 0, 0, 0)
                text_layout.setSpacing(4)

                self.title_label = StrongBodyLabel(title)
                self.title_label.setMinimumWidth(0)
                self.title_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

                self.hint_label = BodyLabel(hint)
                self.hint_label.setMinimumWidth(0)
                self.hint_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                self.hint_label.setWordWrap(True)
                self.hint_label.setStyleSheet("color: #667085;")
                self.hint_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

                text_layout.addWidget(self.title_label)
                text_layout.addWidget(self.hint_label)
                header.addWidget(self.icon_button, 0, Qt.AlignmentFlag.AlignTop)
                header.addLayout(text_layout, 1)
                self.header_actions = QHBoxLayout()
                self.header_actions.setContentsMargins(0, 0, 0, 0)
                self.header_actions.setSpacing(8)
                header.addLayout(self.header_actions, 0)
                layout.addLayout(header)

                file_info_row = QHBoxLayout()
                file_info_row.setContentsMargins(0, 0, 0, 0)
                file_info_row.setSpacing(10)

                self.file_name_label = BodyLabel("未选择文件")
                self.file_name_label.setMinimumWidth(0)
                self.file_name_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
                self.file_name_label.setStyleSheet("color: #111827; font-weight: 400;")
                self.file_name_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                file_info_row.addWidget(self.file_name_label, 1)
                self.file_state_badge = QLabel("已选择")
                self.file_state_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
                self.file_state_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self.file_state_badge.hide()
                file_info_row.addWidget(self.file_state_badge, 0, Qt.AlignmentFlag.AlignVCenter)

                self.ready_duration_label = BodyLabel("")
                self.ready_duration_label.setMinimumWidth(0)
                self.ready_duration_label.setStyleSheet("color: #667085;")
                self.ready_duration_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self.ready_duration_label.hide()
                file_info_row.addWidget(self.ready_duration_label, 0, Qt.AlignmentFlag.AlignRight)

                self.detail_label = AlignmentInfoLabel(owner, self._empty_detail_text, self)
                self.detail_label.setMinimumWidth(0)
                self.detail_label.setStyleSheet("color: #667085;")
                self.detail_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.detail_label.setWordWrap(False)
                self.detail_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                file_info_row.addWidget(self.detail_label, 0, Qt.AlignmentFlag.AlignRight)
                layout.addLayout(file_info_row)

                self.action_frame = QFrame(self)
                self.action_frame.setObjectName("AlignmentDropAction")
                action_layout = QHBoxLayout(self.action_frame)
                action_layout.setContentsMargins(16, 12, 16, 12)
                action_layout.setSpacing(10)

                self.action_icon = ToolButton(self.action_frame)
                self.action_icon.setIcon(FIF.UP.icon())
                self.action_icon.setIconSize(QSize(20, 20))
                self.action_icon.setFixedSize(28, 28)
                self.action_icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
                self.action_icon.setStyleSheet("ToolButton { background: transparent; border: 0; padding: 0; }")

                self.action_label = BodyLabel(self._default_action_text)
                self.action_label.setCursor(Qt.CursorShape.PointingHandCursor)
                self.action_label.setMinimumHeight(42)
                self.action_label.setStyleSheet("font-weight: 400;")
                self.action_label.mousePressEvent = lambda _event: self.browseRequested.emit()

                action_layout.addStretch(1)
                action_layout.addWidget(self.action_icon)
                action_layout.addWidget(self.action_label)
                action_layout.addStretch(1)
                layout.addWidget(self.action_frame)

                self.replace_button = QPushButton("更换")
                self.replace_button.clicked.connect(self.browseRequested.emit)
                self.remove_button = QPushButton("移除")
                self.remove_button.clicked.connect(self.removeRequested.emit)
                for button in (self.replace_button, self.remove_button):
                    button.setMinimumHeight(34)
                    button.setMinimumWidth(76)
                    button.hide()
                    self.header_actions.addWidget(button)
                self.file_name_label.setText("未选择文件")
                self.detail_label.setText(self._empty_detail_text)
                self._refresh_style()

            def _build_theme_palette(self, theme: str) -> dict[str, str]:
                if theme == "blue":
                    return {
                        "accent": "#4C8DFF",
                        "accent_border": "#CFE0FF",
                        "icon_background": "#EEF5FF",
                        "action_background": "#F5F9FF",
                        "hover_background": "#FAFCFF",
                        "selected_background": "#EEF5FF",
                        "selected_icon_background": "#CFE3FF",
                        "selected_action_background": "#E4EEFF",
                    }
                return {
                    "accent": "#FF5D72",
                    "accent_border": "#FFD7DE",
                    "icon_background": "#FFF0F3",
                    "action_background": "#FFF7F8",
                    "hover_background": "#FFFBFB",
                    "selected_background": "#FFF1F4",
                    "selected_icon_background": "#FFD6DE",
                    "selected_action_background": "#FFE8ED",
                }

            def accepts(self, path: Path) -> bool:
                return path.is_file() and path.suffix.lower() in self.extensions

            def set_path(self, path: Path) -> None:
                self.path = path
                self.file_name_label.setText(path.name)
                self._drag_state = "idle"
                self._refresh_style()

            def clear_path(self) -> None:
                self.path = None
                self.file_name_label.setText("未选择文件")
                self.detail_label.setText(self._empty_detail_text)
                self._drag_state = "idle"
                self._refresh_style()

            def set_display_mode(self, mode: str, *, missing_text: str = "") -> None:
                self._display_mode = mode if mode in {"empty", "ready", "chip"} else "empty"
                self._missing_text = missing_text
                self._refresh_style()

            def set_balanced_height(self, height: int | None) -> None:
                self._balanced_height = height
                self._refresh_style()

            def enterEvent(self, event) -> None:  # noqa: N802
                self._hovered = True
                self._refresh_style()
                super().enterEvent(event)

            def leaveEvent(self, event) -> None:  # noqa: N802
                self._hovered = False
                self._refresh_style()
                super().leaveEvent(event)

            def mousePressEvent(self, event) -> None:  # noqa: N802
                if event.button() == Qt.MouseButton.LeftButton:
                    self.browseRequested.emit()
                    event.accept()
                    return
                super().mousePressEvent(event)

            def dragEnterEvent(self, event) -> None:  # noqa: N802
                urls = event.mimeData().urls()
                if not urls:
                    self._drag_state = "reject"
                    self._refresh_style()
                    event.ignore()
                    return
                path = Path(urls[0].toLocalFile()).expanduser()
                if self.accepts(path):
                    self._drag_state = "accept"
                    self._refresh_style()
                    event.acceptProposedAction()
                    return
                self._drag_state = "reject"
                self._refresh_style()
                event.ignore()

            def dragLeaveEvent(self, event) -> None:  # noqa: N802
                self._drag_state = "idle"
                self._refresh_style()
                super().dragLeaveEvent(event)

            def dropEvent(self, event) -> None:  # noqa: N802
                urls = event.mimeData().urls()
                if not urls:
                    self._drag_state = "idle"
                    self._refresh_style()
                    event.ignore()
                    return
                path = Path(urls[0].toLocalFile()).expanduser()
                if not self.accepts(path):
                    self._drag_state = "reject"
                    self._refresh_style()
                    event.ignore()
                    return
                self.set_path(path)
                self.pathChanged.emit(path)
                event.acceptProposedAction()

            def _refresh_style(self) -> None:
                self._refresh_style_modern()
                return

            def _refresh_style_modern(self) -> None:
                palette = self._theme_palette
                is_selected = self.path is not None
                is_chip = self._display_mode == "chip" and is_selected
                is_ready = self._display_mode == "ready" and is_selected
                if not is_selected:
                    self.file_name_label.setText("未选择文件")
                if self._drag_state == "accept":
                    background = "#ffffff"
                    border = palette["accent"]
                    accent = palette["accent"]
                    border_width = 2
                    action_background = palette["action_background"]
                    action_border = palette["accent_border"]
                    action_text = "松开鼠标即可导入这个文件"
                elif self._drag_state == "reject":
                    background = "#fff1f2"
                    border = "#ff4d5e"
                    accent = "#ff4d5e"
                    border_width = 2
                    action_background = "#fff5f6"
                    action_border = "#ffc7d0"
                    action_text = "文件类型不支持，请重新选择"
                elif is_selected:
                    background = palette["selected_background"]
                    border = palette["accent"]
                    accent = palette["accent"]
                    border_width = 2
                    action_background = palette["selected_action_background"]
                    action_border = palette["accent_border"]
                    action_text = "点击更换文件，或拖入新文件覆盖"
                elif self._hovered:
                    background = palette["hover_background"]
                    border = palette["accent_border"]
                    accent = palette["accent"]
                    border_width = 1
                    action_background = palette["action_background"]
                    action_border = palette["accent_border"]
                    action_text = self._default_action_text
                else:
                    background = "#ffffff"
                    border = "#E9EDF3"
                    accent = palette["accent"]
                    border_width = 1
                    action_background = palette["action_background"]
                    action_border = "#E7EEF8" if self._theme == "blue" else "#F2E8EB"
                    action_text = self._default_action_text

                if is_chip:
                    background = palette["selected_background"]
                    border = palette["accent_border"]
                    border_width = 1
                    action_text = "点击更换"
                elif is_ready:
                    background = palette["selected_background"]
                    border = palette["accent"]
                    border_width = 1
                    action_text = "点击更换文件，或拖入新文件覆盖"

                compact = is_chip or is_ready
                if is_chip:
                    self._main_layout.setContentsMargins(14, 8, 14, 8)
                    self._main_layout.setSpacing(0)
                else:
                    self._main_layout.setContentsMargins(16, 16, 16, 16)
                    self._main_layout.setSpacing(8 if is_ready else 12)
                self.setMinimumHeight(54 if is_chip else 158)
                self.setMaximumHeight(64 if is_chip else 16777215)
                if self._balanced_height is not None:
                    self.setMinimumHeight(self._balanced_height)
                    self.setMaximumHeight(self._balanced_height)
                self.icon_button.setFixedSize(28 if is_chip else (34 if is_ready else 68), 28 if is_chip else (34 if is_ready else 68))
                self.icon_button.setIconSize(QSize(16 if is_chip else (18 if is_ready else 34), 16 if is_chip else (18 if is_ready else 34)))
                self.hint_label.setVisible(not is_selected)
                self.file_name_label.setVisible(not is_chip)
                self.detail_label.setVisible(False)
                clean_duration = self.detail_label.text().split(": ", 1)[-1]
                self.ready_duration_label.setText(clean_duration if is_ready and clean_duration != self._empty_detail_text else "")
                self.ready_duration_label.setVisible(is_ready)
                self.file_state_badge.setVisible(is_ready)
                self.file_state_badge.setText("✓ 已就绪")
                self.action_frame.setVisible(not is_chip)
                if is_ready:
                    self.action_frame.hide()
                self.replace_button.setVisible(is_ready or is_chip)
                self.remove_button.setVisible(False)
                self.detail_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
                self.action_label.setText(action_text)
                self.action_icon.setIcon(
                    (FIF.UPDATE if is_selected and self._drag_state == "idle" else FIF.UP).icon()
                )
                self.title_label.setText(
                    f"{self.path.name} · {self.detail_label.text().split(': ', 1)[-1]}"
                    if is_chip and self.path is not None
                    else self._media_label
                )
                if not is_chip:
                    self.title_label.setText(self._media_label)
                self.title_label.setStyleSheet(
                    f"color: {palette['accent']}; font-size: {'11.5pt' if is_chip else '16pt'}; background: transparent; border: 0;"
                )
                self.title_label.setFont(build_app_ui_font(point_size=11.5 if is_chip else 16, bold=True))
                self.hint_label.setStyleSheet("color: #667085; font-size: 11pt; background: transparent; border: 0;")
                self.file_name_label.setStyleSheet(
                    f"color: {'#182230' if is_selected else '#344054'}; font-size: {'10.5pt' if is_chip else '12pt'}; font-weight: 400; background: transparent; border: 0;"
                )
                self.file_state_badge.setStyleSheet(
                    f"""
                    QLabel {{
                        background: transparent;
                        color: #16803D;
                        border: 0;
                        border-radius: 10px;
                        padding: 3px 10px;
                        font-size: 9.5pt;
                    }}
                    """
                )
                self.file_state_badge.setFont(build_app_ui_font(point_size=9.5, bold=True))
                self.ready_duration_label.setStyleSheet(
                    'color: #667085; font-family: "Microsoft YaHei UI"; font-size: 11pt; font-weight: 400; background: transparent; border: 0;'
                )
                self.detail_label.setStyleSheet(
                    'color: #98A2B3; font-family: "Microsoft YaHei UI"; font-size: 11pt; font-weight: 400; background: transparent; border: 0;'
                )
                self.icon_button.setStyleSheet(
                    f"""
                    ToolButton {{
                        background: {'transparent' if is_chip else (palette["selected_icon_background"] if is_selected else palette["icon_background"])};
                        border: 1px solid {'transparent' if is_chip else (palette["accent_border"] if is_selected else 'transparent')};
                        border-radius: {17 if is_chip else 24}px;
                        padding: 0;
                        color: {accent};
                    }}
                    """
                )
                self.action_icon.setStyleSheet(
                    f"""
                    ToolButton {{
                        background: transparent;
                        border: 0;
                        padding: 0;
                        color: {accent};
                    }}
                    """
                )
                self.action_label.setStyleSheet(f"color: {accent}; font-size: 12pt; font-weight: 400;")
                for button in (self.replace_button, self.remove_button):
                    button.setStyleSheet(
                        f"""
                        QPushButton {{
                            background: #ffffff;
                            color: #1F2937;
                            border: 1px solid #D0D5DD;
                            border-radius: 8px;
                            padding: 6px 14px;
                        }}
                        QPushButton:hover {{
                            border-color: {palette["accent"]};
                            color: {palette["accent"]};
                        }}
                        """
                    )
                if self._display_mode == "empty" and self._missing_text:
                    self.action_label.setText(f"{self._default_action_text}\n{self._missing_text}")
                self.setStyleSheet(
                    f"""
                    QFrame#AlignmentDropCard {{
                        background: {background};
                        border: {border_width}px solid {border};
                        border-radius: 18px;
                    }}
                    QFrame#AlignmentDropAction {{
                        background: {action_background};
                        border: 1px solid {action_border};
                        border-radius: 14px;
                    }}
                    QFrame#AlignmentDropCard QLabel {{
                        background: transparent;
                        border: 0;
                    }}
                    """
                )
                return

        class AlignmentExportProxyButton(PrimaryPushButton):
            def __init__(self, owner: "KrokHelperQtApp") -> None:
                super().__init__(owner)
                self._owner = owner
                self.hide()

            def setEnabled(self, enabled: bool) -> None:
                super().setEnabled(enabled)
                self._owner._sync_alignment_export_buttons()

        scroll = QScrollArea()
        self.align_scroll_area = scroll
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.installEventFilter(self)

        page = QWidget()
        shell = QVBoxLayout(page)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(14)

        self.waveform_view = WaveformView()
        self.waveform_view.playheadChanged.connect(self._handle_playhead_changed)
        self.waveform_view.offsetChanged.connect(self._handle_waveform_offset_changed)
        self.waveform_view.trimChanged.connect(self._refresh_align_trim_status)
        self._last_fill_mode = None
        self._pending_offset_finalized_seconds = 0.0
        self._offset_finalize_timer = QTimer(self)
        self._offset_finalize_timer.setSingleShot(True)
        self._offset_finalize_timer.setInterval(50)
        self._offset_finalize_timer.timeout.connect(
            lambda: self._on_offset_finalized(self._pending_offset_finalized_seconds)
        )
        self.waveform_view.offsetChanged.connect(
            lambda seconds: (
                setattr(self, "_pending_offset_finalized_seconds", float(seconds)),
                self._offset_finalize_timer.start(),
            )
        )
        self._align_volume_refresh_timer = QTimer(self)
        self._align_volume_refresh_timer.setSingleShot(True)
        self._align_volume_refresh_timer.setInterval(120)
        self._align_volume_refresh_timer.timeout.connect(self._apply_alignment_preview_volume)
        self.waveform_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.waveform_view.setMinimumHeight(300)
        self.wave_view = self.waveform_view

        self.align_export_button = AlignmentExportProxyButton(self)
        self.align_export_button.clicked.connect(self._start_aligned_export)
        self.align_export_button.setEnabled(False)

        self._align_nudge_step = 0.01

        self.align_video_zone = AlignmentDropCard(
            owner=self,
            media_label="字幕视频",
            title="选择字幕视频",
            hint="支持 mkv / mp4 / mov / avi",
            extensions=VIDEO_EXTENSIONS,
            icon=FIF.VIDEO,
            theme="red",
        )
        self.align_video_zone.browseRequested.connect(self._choose_align_video)
        self.align_video_zone.pathChanged.connect(self.set_align_video_path)
        self.align_video_info_label = self.align_video_zone.detail_label
        self.align_video_zone.title_label.setText("字幕视频")
        self.align_video_zone.hint_label.setText("支持 mkv / mp4 / mov / avi")

        self.align_audio_zone = AlignmentDropCard(
            owner=self,
            media_label="原唱音源",
            title="选择原唱音频",
            hint="支持 flac / wav / mp3 / m4a / aac / ape / alac / mkv / mp4",
            extensions=ALIGN_AUDIO_EXTENSIONS,
            icon=FIF.MUSIC,
            theme="blue",
        )
        self.align_audio_zone.browseRequested.connect(self._choose_align_audio)
        self.align_audio_zone.pathChanged.connect(self.set_align_audio_path)
        self.align_audio_info_label = self.align_audio_zone.detail_label
        self.align_audio_zone.title_label.setText("原唱音频")
        self.align_audio_zone.hint_label.setText("支持 flac / wav / mp3 / m4a / aac / ape / alac / mkv / mp4")

        def clear_align_video_only() -> None:
            self.align_video_zone.clear_path()
            self._invalidate_alignment_waveforms()

        def clear_align_audio_only() -> None:
            self.align_audio_zone.clear_path()
            self._invalidate_alignment_waveforms()

        self.align_video_zone.removeRequested.connect(clear_align_video_only)
        self.align_audio_zone.removeRequested.connect(clear_align_audio_only)

        clear_button = QPushButton("清空")
        clear_button.clicked.connect(self._clear_alignment_inputs)
        clear_button.setMinimumHeight(36)
        clear_button.setMinimumWidth(84)
        clear_button_policy = clear_button.sizePolicy()
        clear_button_policy.setRetainSizeWhenHidden(True)
        clear_button.setSizePolicy(clear_button_policy)

        self.align_stop_export_button = QPushButton("停止导出")
        self.align_stop_export_button.setIcon(FIF.CLOSE.icon())
        self.align_stop_export_button.clicked.connect(self._stop_alignment_export)
        self.align_stop_export_button.setEnabled(False)
        self.align_stop_export_button.setMinimumHeight(36)

        open_output_button = QPushButton("打开输出目录")
        open_output_button.clicked.connect(self._open_align_output_dir)
        open_output_button.setMinimumHeight(36)

        self.align_open_output_button = open_output_button
        self.align_clear_button = clear_button

        self.align_material_card = CardWidget(radius=10, padding=(16, 14, 16, 14), spacing=12)
        material_layout = self.align_material_card.createVBoxLayout()
        material_header = QHBoxLayout()
        material_header.setContentsMargins(0, 0, 0, 0)
        material_header.setSpacing(10)
        material_title = QLabel("素材输入")
        material_title.setObjectName("PanelTitle")
        self.align_material_status_label = QLabel("① 先导入素材")
        self.align_material_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.align_material_status_label.setStyleSheet(
            "background: #FFF1F2; color: #F04452; border: 1px solid #FFD1D8; "
            "border-radius: 7px; padding: 4px 10px;"
        )
        self.align_material_status_label.setFont(build_app_ui_font(point_size=10.5, bold=True))
        self.align_material_settings_button = ToolButton(FIF.SETTING)
        self.align_material_settings_button.setObjectName("AlignMaterialSettingsButton")
        self.align_material_settings_button.setToolTip("波形对齐设置")
        self.align_material_settings_button.setFixedSize(30, 30)
        self.align_material_settings_button.setIconSize(QSize(16, 16))
        self.align_material_settings_button.clicked.connect(lambda: self._open_settings_window("align"))

        material_header.addWidget(material_title)
        material_header.addWidget(self.align_material_status_label)
        material_header.addStretch(1)
        material_header.addWidget(self.align_clear_button)
        material_header.addSpacing(2)
        material_header.addWidget(self.align_material_settings_button, 0, Qt.AlignmentFlag.AlignVCenter)
        material_layout.addLayout(material_header)

        self.align_material_body = QWidget()
        self.align_material_body.setStyleSheet("background: transparent; border: 0;")
        material_body_layout = QHBoxLayout(self.align_material_body)
        material_body_layout.setContentsMargins(0, 0, 0, 0)
        material_body_layout.setSpacing(14)
        material_body_layout.addWidget(self.align_video_zone, 1)
        material_body_layout.addWidget(self.align_audio_zone, 1)
        material_layout.addWidget(self.align_material_body)
        shell.addWidget(self.align_material_card)

        shell.addWidget(self._build_waveform_toolbar())

        main_row = QWidget()
        main_row.setMinimumWidth(0)
        main_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.align_control_panel = main_row
        main_layout = QHBoxLayout(main_row)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        self.align_waveform_card = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=8)
        self.align_waveform_card.setMinimumWidth(0)
        self.align_waveform_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        waveform_grid = QGridLayout(self.align_waveform_card)
        waveform_grid.setContentsMargins(16, 16, 16, 16)
        waveform_grid.setVerticalSpacing(8)
        waveform_grid.setHorizontalSpacing(0)
        waveform_grid.setRowStretch(0, 0)
        waveform_grid.setRowStretch(1, 1)
        waveform_grid.setRowStretch(2, 0)
        waveform_header = QLabel("波形工作区")
        waveform_header.setObjectName("PanelTitle")
        waveform_grid.addWidget(waveform_header, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        waveform_stage = QFrame(self.align_waveform_card)
        waveform_stage.setObjectName("AlignWaveformStage")
        waveform_stage.setMinimumWidth(0)
        waveform_stage.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        waveform_stage.setStyleSheet("QFrame#AlignWaveformStage { background: transparent; border: 0; }")
        self.align_waveform_stage = waveform_stage
        waveform_stage.installEventFilter(self)
        self.waveform_view.installEventFilter(self)
        self.align_scroll_viewport = scroll.viewport()
        self.align_scroll_viewport.installEventFilter(self)
        stage_grid = QGridLayout(waveform_stage)
        stage_grid.setContentsMargins(0, 0, 0, 0)
        stage_grid.setSpacing(0)
        stage_grid.addWidget(self.waveform_view, 0, 0)
        self.align_waveform_placeholder = QLabel(
            "导入字幕视频与原唱音源后，点击「生成波形」即可在此查看对齐视图"
        )
        self.align_waveform_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.align_waveform_placeholder.setWordWrap(True)
        self.align_waveform_placeholder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.align_waveform_placeholder.setStyleSheet("color: #667085; font-size: 12pt;")
        stage_grid.addWidget(self.align_waveform_placeholder, 0, 0, alignment=Qt.AlignmentFlag.AlignCenter)
        self.align_drag_mode_button = ToolButton(waveform_stage)
        self.align_drag_mode_button.setIcon(FIF.MOVE.icon())
        self.align_drag_mode_button.setToolTip("切换拖动模式 (Alt+V)")
        self.align_drag_mode_button.clicked.connect(self._handle_align_drag_mode_shortcut)
        self.align_drag_mode_button.setFixedSize(34, 34)
        stage_grid.addWidget(
            self.align_drag_mode_button,
            0,
            0,
            alignment=Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight,
        )
        self.align_nudge_panel = QFrame(self.align_waveform_card)
        self.align_nudge_panel.setObjectName("AlignNudgePanel")
        nudge_layout = QHBoxLayout(self.align_nudge_panel)
        nudge_layout.setContentsMargins(8, 8, 8, 8)
        nudge_layout.setSpacing(8)
        for text, delta in (("-0.1", -0.1), ("-0.01", -0.01), ("归零", None), ("+0.01", 0.01), ("+0.1", 0.1)):
            button = QPushButton(text)
            button.setMinimumHeight(30)
            if delta is None:
                button.clicked.connect(lambda _checked=False: self.waveform_view.set_offset(0.0))
            else:
                button.clicked.connect(lambda _checked=False, value=delta: self.waveform_view.nudge_offset(value))
            nudge_layout.addWidget(button)
        waveform_grid.addWidget(waveform_stage, 1, 0)
        waveform_grid.addWidget(self.align_nudge_panel, 2, 0, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        main_layout.addWidget(self.align_waveform_card, 1)

        right_sidebar = self._build_adjustment_panels()
        right_sidebar.setFixedWidth(380)
        main_layout.addWidget(right_sidebar, 0)
        main_layout.setStretch(0, 1)
        main_layout.setStretch(1, 0)
        shell.addWidget(main_row)

        self.align_log_panel = CardWidget(radius=10, padding=(14, 12, 14, 12), spacing=8)
        log_layout = self.align_log_panel.createVBoxLayout()
        log_header = QHBoxLayout()
        log_header.setContentsMargins(0, 0, 0, 0)
        log_header.setSpacing(10)
        self.align_log_toggle_button = QPushButton("▸  对齐日志")
        self.align_log_toggle_button.setFlat(True)
        self.align_log_toggle_button.setStyleSheet(
            "text-align: left; font-weight: 700; color: #111827; border: 0; background: transparent;"
        )
        clear_log_button = QPushButton("清空日志")
        clear_log_button.setIcon(FIF.DELETE.icon())
        clear_log_button.hide()
        self.align_clear_log_button = clear_log_button
        log_header.addWidget(self.align_log_toggle_button, 1)
        log_header.addWidget(clear_log_button)
        log_layout.addLayout(log_header)
        self.align_log_container = QWidget()
        self.align_log_container.setStyleSheet("background: #FFFFFF; border: 0;")
        log_body_layout = QVBoxLayout(self.align_log_container)
        log_body_layout.setContentsMargins(0, 0, 0, 0)
        log_body_layout.setSpacing(0)
        self.align_log = QPlainTextEdit()
        self.align_log.setObjectName("LogText")
        self.align_log.setReadOnly(True)
        self.align_log.setMinimumHeight(120)
        self.log_text = self.align_log
        clear_log_button.clicked.connect(self.align_log.clear)
        log_body_layout.addWidget(self.align_log)
        log_layout.addWidget(self.align_log_container)
        self.align_log_container.hide()

        def toggle_log() -> None:
            expanded = not self.align_log_container.isVisible()
            self.align_log_container.setVisible(expanded)
            self.align_clear_log_button.setVisible(expanded)
            self.align_log_toggle_button.setText(("▾" if expanded else "▸") + "  对齐日志")

        self.align_log_toggle_button.clicked.connect(toggle_log)
        shell.addWidget(self.align_log_panel)
        shell.addStretch(1)

        self._refresh_alignment_material_inputs()
        self._refresh_align_target_ui()
        self._on_alignment_target_changed()
        self._refresh_alignment_preview_controls()
        scroll.setWidget(page)
        return scroll

    def _build_waveform_toolbar(self) -> QWidget:
        from PyQt6.QtCore import QSize

        toolbar_card = CardWidget(radius=10, padding=(14, 12, 14, 12), spacing=10)
        toolbar_card.setMinimumWidth(0)
        toolbar_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = toolbar_card.createHBoxLayout()
        layout.setSpacing(6)

        self.align_analyze_button = QPushButton("生成波形")
        self.align_analyze_button.setIcon(FIF.MUSIC.icon())
        self.align_analyze_button.clicked.connect(self._start_alignment_analysis)
        self.align_analyze_button.setToolTip("生成波形 (空格)")
        self.align_auto_button = PrimaryPushButton("自动对齐")
        self.align_auto_button.setIcon(FIF.SYNC.icon())
        self.align_auto_button.clicked.connect(self._auto_align_waveforms)
        self.align_auto_button.setToolTip("自动对齐 (Ctrl+D)")
        self.btn_auto_align = self.align_auto_button
        self.align_preview_button = QPushButton("播放")
        self.align_preview_button.setIcon(FIF.PLAY.icon())
        self.align_preview_button.clicked.connect(self._toggle_alignment_preview)
        self.align_preview_button.setToolTip("播放 (空格)")

        toolbar_button_specs = (
            (self.align_analyze_button, 108),
            (self.align_auto_button, 118),
            (self.align_preview_button, 86),
        )
        for button, minimum_width in toolbar_button_specs:
            button.setMinimumHeight(36)
            button.setMaximumHeight(36)
            button.setMinimumWidth(minimum_width)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            button.setIconSize(QSize(16, 16))
            layout.addWidget(button)

        self.align_drag_offset_radio = QRadioButton("移动字幕视频")
        self.align_drag_pan_radio = QRadioButton("平移视图")
        self.align_drag_offset_radio.setChecked(True)
        self.align_drag_group = QButtonGroup(self)
        self.align_drag_group.setExclusive(True)
        self.align_drag_group.addButton(self.align_drag_offset_radio)
        self.align_drag_group.addButton(self.align_drag_pan_radio)
        self.align_drag_offset_radio.toggled.connect(
            lambda checked: self.waveform_view.set_drag_mode("offset" if checked else "pan")
        )
        self.rb_drag_move = self.align_drag_offset_radio
        self.rb_drag_pan = self.align_drag_pan_radio
        self.align_drag_offset_radio.hide()
        self.align_drag_pan_radio.hide()

        volume_button = ToolButton(toolbar_card)
        volume_button.setIcon(FIF.VOLUME.icon())
        volume_button.setIconSize(QSize(18, 18))
        volume_button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        volume_button.setStyleSheet("ToolButton { background: transparent; border: 0; }")
        layout.addWidget(volume_button)

        self.align_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.align_volume_slider.setRange(0, 100)
        self.align_volume_slider.setValue(50)
        self.align_volume_slider.setMinimumWidth(36)
        self.align_volume_slider.setMaximumWidth(100)
        self.align_volume_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.align_volume_slider.valueChanged.connect(self._queue_alignment_preview_volume_refresh)
        layout.addWidget(self.align_volume_slider)

        self.align_reset_view_button = QPushButton("回到开头")
        self.align_reset_view_button.setIcon(FIF.SKIP_BACK.icon())
        self.align_reset_view_button.setMinimumHeight(36)
        self.align_reset_view_button.setMaximumHeight(36)
        self.align_reset_view_button.setMinimumWidth(104)
        self.align_reset_view_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.align_reset_view_button.setIconSize(QSize(16, 16))
        self.align_reset_view_button.clicked.connect(self._reset_alignment_waveform_view)
        layout.addWidget(self.align_reset_view_button)

        self.align_jump_to_end_button = QPushButton("跳到末尾")
        self.align_jump_to_end_button.setIcon(FIF.SKIP_FORWARD.icon())
        self.align_jump_to_end_button.setMinimumHeight(36)
        self.align_jump_to_end_button.setMaximumHeight(36)
        self.align_jump_to_end_button.setMinimumWidth(104)
        self.align_jump_to_end_button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        self.align_jump_to_end_button.setIconSize(QSize(16, 16))
        self.align_jump_to_end_button.clicked.connect(self.waveform_view.jump_to_end)
        layout.addWidget(self.align_jump_to_end_button)

        zoom_out = ToolButton(toolbar_card)
        zoom_out.setIcon(FIF.ZOOM_OUT.icon())
        zoom_out.setIconSize(QSize(18, 18))
        zoom_out.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        zoom_out.setStyleSheet("ToolButton { background: transparent; border: 0; }")
        layout.addWidget(zoom_out)

        self.align_zoom_slider = QSlider(Qt.Orientation.Horizontal)
        self.align_zoom_slider.setRange(1, 800)
        self.align_zoom_slider.setValue(120)
        self.align_zoom_slider.valueChanged.connect(lambda value: self.waveform_view.set_zoom(float(value)))
        self.align_zoom_slider.setMinimumWidth(36)
        self.align_zoom_slider.setMaximumWidth(110)
        self.align_zoom_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.align_zoom_slider)

        zoom_in = ToolButton(toolbar_card)
        zoom_in.setIcon(FIF.ZOOM_IN.icon())
        zoom_in.setIconSize(QSize(18, 18))
        zoom_in.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        zoom_in.setStyleSheet("ToolButton { background: transparent; border: 0; }")
        layout.addWidget(zoom_in)

        self.align_progress = QProgressBar()
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(0)
        self.align_progress.setMinimumWidth(40)
        self.align_progress.setMaximumWidth(120)
        self.align_progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.align_progress.setTextVisible(False)
        layout.addWidget(self.align_progress)

        self.align_status_label = BodyLabel("准备生成波形")
        self.align_status_label.setMinimumWidth(210)
        self.align_status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.align_status_label.setStyleSheet("color: #475467; font-weight: 400;")
        layout.addWidget(self.align_status_label, 1)
        return toolbar_card

    def _build_adjustment_panels(self) -> QWidget:
        wrapper = QWidget()
        wrapper.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        wrapper.setStyleSheet("background: transparent; border: 0;")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        self.align_control_card = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=12)
        self.align_control_card.setObjectName("AlignControlCard")
        self.align_control_card.setStyleSheet(
            """
            QFrame#AlignControlCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
            QFrame#AlignControlCard QLabel {
                background: transparent;
                border: 0;
            }
            QFrame#AlignControlCard QCheckBox {
                background: transparent;
            }
            """
        )
        self.subtitle_adjust_card = self.align_control_card
        self.SubtitleAdjust = self.align_control_card
        self.original_adjust_card = self.align_control_card
        self.OriginalAdjust = self.align_control_card
        control_layout = self.align_control_card.createVBoxLayout()
        control_layout.setSpacing(12)
        control_layout.addWidget(StrongBodyLabel("对齐控制"))

        segment = QWidget()
        segment.setObjectName("AlignTargetSegment")
        segment.setMinimumHeight(36)
        segment.setStyleSheet(
            """
            QWidget#AlignTargetSegment {
                background: transparent;
                border: 1px solid #D0D5DD;
                border-radius: 8px;
            }
            """
        )
        segment_layout = QHBoxLayout(segment)
        segment_layout.setContentsMargins(0, 0, 0, 0)
        segment_layout.setSpacing(0)
        self.align_target_video_radio = QRadioButton("对齐字幕视频")
        self.align_target_audio_radio = QRadioButton("对齐原唱音频")
        self.align_target_video_radio.setChecked(True)
        self.align_target_group = QButtonGroup(self)
        self.align_target_group.setExclusive(True)
        self.align_target_group.addButton(self.align_target_video_radio)
        self.align_target_group.addButton(self.align_target_audio_radio)
        self.align_target_video_radio.toggled.connect(self._on_alignment_target_changed)
        self.align_target_audio_radio.toggled.connect(self._on_alignment_target_changed)
        self.rb_adjust_subtitle = self.align_target_video_radio
        self.rb_adjust_original = self.align_target_audio_radio
        self.align_target_video_radio.hide()
        self.align_target_audio_radio.hide()
        self.align_target_video_button = QPushButton("对齐字幕视频")
        self.align_target_audio_button = QPushButton("对齐原唱音频")
        self.align_target_video_button.setCheckable(True)
        self.align_target_audio_button.setCheckable(True)
        self.align_target_video_button.clicked.connect(lambda _checked=False: self.align_target_video_radio.setChecked(True))
        self.align_target_audio_button.clicked.connect(lambda _checked=False: self.align_target_audio_radio.setChecked(True))
        segment_layout.addWidget(self.align_target_video_button, 1)
        segment_layout.addWidget(self.align_target_audio_button, 1)
        control_layout.addWidget(segment)

        self.align_control_placeholder = QFrame()
        self.align_control_placeholder.setObjectName("AlignControlPlaceholder")
        self.align_control_placeholder.setStyleSheet(
            "QFrame#AlignControlPlaceholder { background: transparent; border: 0; }"
            "ToolButton { background: transparent; border: 0; }"
        )
        placeholder_layout = QHBoxLayout(self.align_control_placeholder)
        placeholder_layout.setContentsMargins(18, 20, 18, 20)
        placeholder_layout.setSpacing(12)
        placeholder_icon = ToolButton(self.align_control_placeholder)
        placeholder_icon.setIcon(FIF.SETTING.icon())
        placeholder_icon.setEnabled(False)
        placeholder_layout.addWidget(placeholder_icon, 0, Qt.AlignmentFlag.AlignVCenter)
        placeholder_label = BodyLabel("请先导入素材并生成波形")
        placeholder_label.setStyleSheet("color: #9CA3AF;")
        placeholder_layout.addWidget(placeholder_label, 1, Qt.AlignmentFlag.AlignVCenter)
        control_layout.addWidget(self.align_control_placeholder)

        self.align_video_options_widget = QWidget()
        self.align_video_options_widget.setStyleSheet("background: transparent; border: 0;")
        video_layout = QVBoxLayout(self.align_video_options_widget)
        video_layout.setContentsMargins(0, 0, 0, 0)
        video_layout.setSpacing(12)

        self.align_lead_trim_radio = QRadioButton("裁剪")
        self.align_lead_fill_black_radio = QRadioButton("补黑")
        self.align_lead_fill_white_radio = QRadioButton("补白")
        self.align_lead_fill_freeze_radio = QRadioButton("首帧定格")
        self.align_lead_fill_black_radio.setChecked(True)
        self.align_lead_fill_group = QButtonGroup(self)
        self.align_lead_fill_group.setExclusive(True)
        for radio in (
            self.align_lead_trim_radio,
            self.align_lead_fill_black_radio,
            self.align_lead_fill_white_radio,
            self.align_lead_fill_freeze_radio,
        ):
            self.align_lead_fill_group.addButton(radio)

        self.align_lead_row_widget = QWidget()
        self.align_lead_row_widget.setStyleSheet("background: transparent; border: 0;")
        lead_row = QHBoxLayout(self.align_lead_row_widget)
        lead_row.setContentsMargins(0, 0, 0, 0)
        lead_row.setSpacing(10)
        lead_row.addWidget(BodyLabel("片头："))
        self.align_head_btn_crop = QPushButton("裁剪")
        self.align_head_btn_black = QPushButton("补黑")
        self.align_head_btn_white = QPushButton("补白")
        self.align_head_btn_freeze = QPushButton("首帧定格")

        def select_head_mode(key: str, radio: QRadioButton) -> None:
            if key == "black":
                self._last_fill_mode = LEAD_FILL_BLACK
            elif key == "white":
                self._last_fill_mode = LEAD_FILL_WHITE
            elif key == "freeze":
                self._last_fill_mode = LEAD_FILL_FREEZE
            radio.setChecked(True)
            self._update_head_mode_buttons(key)
            self._refresh_alignment_export_panels()

        for button, key, radio in (
            (self.align_head_btn_crop, "crop", self.align_lead_trim_radio),
            (self.align_head_btn_black, "black", self.align_lead_fill_black_radio),
            (self.align_head_btn_white, "white", self.align_lead_fill_white_radio),
            (self.align_head_btn_freeze, "freeze", self.align_lead_fill_freeze_radio),
        ):
            button.clicked.connect(lambda _checked=False, k=key, r=radio: select_head_mode(k, r))
            lead_row.addWidget(button, 1)
        video_layout.addWidget(self.align_lead_row_widget)

        self.align_head_trim_row_widget = QWidget()
        self.align_head_trim_row_widget.setStyleSheet("background: transparent; border: 0;")
        self.align_lead_trim_seconds_spin = QDoubleSpinBox()
        self.align_lead_trim_seconds_spin.setDecimals(3)
        self.align_lead_trim_seconds_spin.setRange(0.0, 99999.0)
        self.align_lead_trim_seconds_spin.setEnabled(False)
        self.spin_head_trim = self.align_lead_trim_seconds_spin
        self.align_head_trim_row_widget.hide()

        self.align_trim_none_radio = QRadioButton("不处理")
        self.align_trim_to_audio_radio = QRadioButton("裁到音频末尾")
        self.align_trim_none_radio.setChecked(True)
        self.rb_tail_none = self.align_trim_none_radio
        self.rb_tail_trim = self.align_trim_to_audio_radio
        self.align_trim_mode_group = QButtonGroup(self)
        self.align_trim_mode_group.setExclusive(True)
        self.align_trim_mode_group.addButton(self.align_trim_none_radio)
        self.align_trim_mode_group.addButton(self.align_trim_to_audio_radio)
        tail_row = QHBoxLayout()
        tail_row.setContentsMargins(0, 0, 0, 0)
        tail_row.setSpacing(14)
        tail_row.addWidget(BodyLabel("片尾："))
        tail_row.addWidget(self.align_trim_none_radio)
        tail_row.addWidget(self.align_trim_to_audio_radio)
        tail_row.addStretch(1)
        video_layout.addLayout(tail_row)
        self.align_trim_label = BodyLabel("未设置")
        self.align_trim_label.setStyleSheet("color: #667085;")
        self.align_trim_mark_button = QPushButton("设置尾裁点")
        self.align_trim_clear_button = QPushButton("清除尾裁点")
        self.align_trim_mark_button.setMinimumHeight(32)
        self.align_trim_clear_button.setMinimumHeight(32)
        self.align_trim_mark_button.clicked.connect(
            lambda: self.waveform_view.set_trim_end(self.waveform_view.playhead_seconds)
        )
        self.align_trim_clear_button.clicked.connect(self.waveform_view.clear_trim_end)
        tail_action_row = QHBoxLayout()
        tail_action_row.setContentsMargins(0, 0, 0, 0)
        tail_action_row.setSpacing(8)
        tail_action_row.addWidget(self.align_trim_mark_button)
        tail_action_row.addWidget(self.align_trim_clear_button)
        tail_action_row.addWidget(self.align_trim_label, 1)
        video_layout.addLayout(tail_action_row)
        self.chk_auto_trim = self.align_trim_to_audio_radio
        self.align_trim_none_radio.toggled.connect(
            lambda _checked: self._refresh_align_trim_status(self.waveform_view.trim_end_seconds)
        )
        self.align_trim_none_radio.toggled.connect(lambda _checked: self._refresh_alignment_export_panels())
        self.align_trim_to_audio_radio.toggled.connect(
            lambda _checked: self._refresh_align_trim_status(self.waveform_view.trim_end_seconds)
        )
        self.align_trim_to_audio_radio.toggled.connect(lambda _checked: self._refresh_alignment_export_panels())

        encode_row = QHBoxLayout()
        encode_row.setContentsMargins(0, 0, 0, 0)
        encode_row.setSpacing(14)
        encode_row.addWidget(BodyLabel("编码："))
        self.align_encode_software_radio = QRadioButton("软编(CPU)")
        self.align_encode_hardware_radio = QRadioButton("硬编(GPU)")
        self.align_encode_software_radio.setChecked(True)
        self.rb_codec_cpu = self.align_encode_software_radio
        self.rb_codec_gpu = self.align_encode_hardware_radio
        self.align_encode_group = QButtonGroup(self)
        self.align_encode_group.setExclusive(True)
        self.align_encode_group.addButton(self.align_encode_software_radio)
        self.align_encode_group.addButton(self.align_encode_hardware_radio)
        self.align_encode_software_radio.toggled.connect(
            lambda checked: self._handle_alignment_encode_mode_toggled(ENCODE_MODE_SOFTWARE, checked)
        )
        self.align_encode_hardware_radio.toggled.connect(
            lambda checked: self._handle_alignment_encode_mode_toggled(ENCODE_MODE_HARDWARE, checked)
        )
        self.align_encode_row_widget = QWidget()
        self.align_encode_row_widget.setStyleSheet("background: transparent; border: 0;")
        encode_inner = QHBoxLayout(self.align_encode_row_widget)
        encode_inner.setContentsMargins(0, 0, 0, 0)
        encode_inner.setSpacing(14)
        encode_inner.addWidget(self.align_encode_software_radio)
        encode_inner.addWidget(self.align_encode_hardware_radio)
        encode_inner.addStretch(1)
        encode_row.addWidget(self.align_encode_row_widget, 1)
        video_layout.addLayout(encode_row)

        option_row = QHBoxLayout()
        option_row.setContentsMargins(0, 0, 0, 0)
        option_row.setSpacing(14)
        self.align_force_1080p60_check = QCheckBox("重编码1080p60")
        self.align_use_video_audio_check = QCheckBox("保留源音轨")
        self.chk_reencode = self.align_force_1080p60_check
        self.chk_keep_audio = self.align_use_video_audio_check
        self.align_force_1080p60_card = self.align_force_1080p60_check
        self.align_use_video_audio_card = self.align_use_video_audio_check
        self.align_encode_software_card = self.align_encode_software_radio
        self.align_encode_hardware_card = self.align_encode_hardware_radio
        self.align_force_1080p60_check.toggled.connect(self._persist_alignment_preferences)
        self.align_use_video_audio_check.toggled.connect(self._persist_alignment_preferences)
        option_row.addWidget(self.align_force_1080p60_check)
        option_row.addWidget(self.align_use_video_audio_check)
        option_row.addStretch(1)
        video_layout.addLayout(option_row)
        control_layout.addWidget(self.align_video_options_widget)

        self.align_audio_offset_widget = QFrame()
        self.align_audio_offset_widget.setObjectName("AlignAudioOffsetPanel")
        self.align_audio_offset_widget.setStyleSheet(
            "QFrame#AlignAudioOffsetPanel { background: transparent; border: 1px solid #E5E7EB; border-radius: 8px; }"
        )
        audio_offset_layout = QVBoxLayout(self.align_audio_offset_widget)
        audio_offset_layout.setContentsMargins(20, 26, 20, 26)
        audio_offset_layout.setSpacing(12)
        audio_offset_title = BodyLabel("原唱音源偏移")
        audio_offset_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        audio_offset_title.setStyleSheet("color: #1F2937; font-size: 13pt; background: transparent; border: 0;")
        self.align_offset_label = QLabel("+0.000s")
        self.label_offset = self.align_offset_label
        self.align_offset_label.setMinimumWidth(0)
        self.align_offset_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.align_offset_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.align_offset_label.setStyleSheet(
            'color: #2F6BFF; font-family: "Microsoft YaHei UI"; font-size: 24pt; background: transparent; border: 0;'
        )
        self.align_offset_label.setFont(build_app_ui_font(point_size=24, bold=True))
        audio_offset_layout.addStretch(1)
        audio_offset_layout.addWidget(audio_offset_title)
        audio_offset_layout.addWidget(self.align_offset_label)
        audio_offset_layout.addStretch(1)
        control_layout.addWidget(self.align_audio_offset_widget)
        self.align_audio_offset_widget.hide()

        control_body_height = max(
            self.align_control_placeholder.sizeHint().height(),
            self.align_video_options_widget.sizeHint().height(),
            self.align_audio_offset_widget.sizeHint().height(),
        )
        for body_widget in (
            self.align_control_placeholder,
            self.align_video_options_widget,
            self.align_audio_offset_widget,
        ):
            body_widget.setMinimumHeight(control_body_height)
            body_widget.setMaximumHeight(control_body_height)

        layout.addWidget(self.align_control_card, 0)

        self.align_export_card = CardWidget(radius=10, padding=(16, 16, 16, 16), spacing=12)
        self.align_export_card.setObjectName("AlignExportCard")
        self.align_export_card.setStyleSheet(
            """
            QFrame#AlignExportCard {
                background: #FFFFFF;
                border: 1px solid #E5E7EB;
                border-radius: 10px;
            }
            QFrame#AlignExportCard QLabel {
                background: transparent;
                border: 0;
            }
            """
        )
        export_layout = self.align_export_card.createVBoxLayout()
        export_layout.setSpacing(12)
        export_layout.addWidget(StrongBodyLabel("导出"))
        self.align_export_duration_label = QLabel("预计时长 —:—（时长未知）")
        self.align_export_duration_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.align_export_duration_label.setStyleSheet(
            'color: #1F2937; font-family: "Microsoft YaHei UI"; font-size: 18pt; font-weight: 500; background: transparent; border: 0;'
        )
        self.align_export_origin_label = BodyLabel("(原始 时长未知)")
        self.align_export_origin_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.align_export_origin_label.setStyleSheet("color: #667085; background: transparent; border: 0;")
        export_layout.addWidget(self.align_export_duration_label)
        export_layout.addWidget(self.align_export_origin_label)
        self.align_mode_export_button = PrimaryPushButton("导出对齐视频  Ctrl+S")
        self.align_mode_export_button.setFont(build_app_ui_font(point_size=11, bold=True))
        self.align_mode_export_button.setMinimumHeight(46)
        self.align_mode_export_button.clicked.connect(
            lambda: self._trigger_alignment_export(
                ALIGN_TARGET_VIDEO if self._is_align_video_target() else ALIGN_TARGET_AUDIO
            )
        )
        self.ExportVideoBtn = self.align_mode_export_button
        self.btn_export_video = self.align_mode_export_button
        self.ExportWAVBtn = self.align_mode_export_button
        self.btn_export_wav = self.align_mode_export_button
        export_layout.addWidget(self.align_mode_export_button)
        export_actions = QHBoxLayout()
        export_actions.setContentsMargins(0, 0, 0, 0)
        export_actions.setSpacing(10)
        export_actions.addWidget(self.align_stop_export_button)
        export_actions.addWidget(self.align_open_output_button)
        export_layout.addLayout(export_actions)
        self.align_video_export_duration_label = QLabel("时长未知")
        self.align_video_export_origin_label = BodyLabel("(原始时长: 时长未知)")
        self.align_audio_export_duration_label = QLabel("时长未知")
        self.align_audio_export_origin_label = BodyLabel("(原始时长: 时长未知)")
        self.label_export_video_duration = self.align_video_export_duration_label
        self.label_export_video_src_duration = self.align_video_export_origin_label
        self.label_export_wav_duration = self.align_audio_export_duration_label
        self.label_export_wav_src_duration = self.align_audio_export_origin_label
        layout.addWidget(self.align_export_card, 0)
        layout.addStretch(1)

        self.waveform_view.offsetChanged.connect(self._refresh_original_adjustment_panel)
        self.waveform_view.offsetChanged.connect(self._refresh_alignment_export_panels)
        self.waveform_view.trimChanged.connect(lambda _value: self._refresh_alignment_export_panels())
        self._update_head_mode_buttons("black")
        self._refresh_original_adjustment_panel(self.waveform_view.offset_seconds)
        self._refresh_alignment_export_panels()
        return wrapper

    def _update_head_mode_buttons(self, selected_key: str | None) -> None:
        button_map = {
            "crop": getattr(self, "align_head_btn_crop", None),
            "black": getattr(self, "align_head_btn_black", None),
            "white": getattr(self, "align_head_btn_white", None),
            "freeze": getattr(self, "align_head_btn_freeze", None),
        }
        selected_style = (
            "QPushButton {"
            " background: #ff4d5e;"
            " color: white;"
            " border: none;"
            " border-radius: 6px;"
            " padding: 6px 12px;"
            "}"
        )
        unselected_style = (
            "QPushButton {"
            " background: #ffffff;"
            " color: #374151;"
            " border: 1px solid #e5e7eb;"
            " border-radius: 6px;"
            " padding: 6px 12px;"
            "}"
            "QPushButton:hover {"
            " background: #fff1f2;"
            " border: 1px solid #ffb3bc;"
            " color: #ff4d5e;"
            "}"
        )
        disabled_style = (
            "QPushButton {"
            " background: #ffffff;"
            " color: #9ca3af;"
            " border: 1px solid #e5e7eb;"
            " border-radius: 6px;"
            " padding: 6px 12px;"
            "}"
        )
        for key, button in button_map.items():
            if button is None:
                continue
            if not button.isEnabled():
                button.setStyleSheet(disabled_style)
                button.setFont(build_app_ui_font(point_size=10.5, bold=False))
            else:
                is_selected = bool(selected_key and key == selected_key)
                button.setStyleSheet(selected_style if is_selected else unselected_style)
                button.setFont(build_app_ui_font(point_size=10.5, bold=is_selected))

    def _on_offset_finalized(self, seconds: float) -> None:
        if not hasattr(self, "align_target_video_radio") or not self.align_target_video_radio.isChecked():
            return
        if not hasattr(self, "align_lead_trim_radio"):
            return

        if seconds < 0:
            if self.align_lead_fill_white_radio.isChecked():
                self._last_fill_mode = LEAD_FILL_WHITE
            elif self.align_lead_fill_freeze_radio.isChecked():
                self._last_fill_mode = LEAD_FILL_FREEZE
            elif self.align_lead_fill_black_radio.isChecked():
                self._last_fill_mode = LEAD_FILL_BLACK

            self.align_lead_trim_radio.setEnabled(True)
            self.align_lead_fill_black_radio.setEnabled(False)
            self.align_lead_fill_white_radio.setEnabled(False)
            self.align_lead_fill_freeze_radio.setEnabled(False)
            if hasattr(self, "align_head_btn_crop"):
                self.align_head_btn_crop.setEnabled(True)
            if hasattr(self, "align_head_btn_black"):
                self.align_head_btn_black.setEnabled(False)
            if hasattr(self, "align_head_btn_white"):
                self.align_head_btn_white.setEnabled(False)
            if hasattr(self, "align_head_btn_freeze"):
                self.align_head_btn_freeze.setEnabled(False)
            self.align_lead_trim_radio.setChecked(True)
            self._update_head_mode_buttons("crop")
            return

        self.align_lead_trim_radio.setEnabled(False)
        self.align_lead_fill_black_radio.setEnabled(True)
        self.align_lead_fill_white_radio.setEnabled(True)
        self.align_lead_fill_freeze_radio.setEnabled(True)
        if hasattr(self, "align_head_btn_crop"):
            self.align_head_btn_crop.setEnabled(False)
        if hasattr(self, "align_head_btn_black"):
            self.align_head_btn_black.setEnabled(True)
        if hasattr(self, "align_head_btn_white"):
            self.align_head_btn_white.setEnabled(True)
        if hasattr(self, "align_head_btn_freeze"):
            self.align_head_btn_freeze.setEnabled(True)

        if self._last_fill_mode == LEAD_FILL_WHITE:
            self.align_lead_fill_white_radio.setChecked(True)
            self._update_head_mode_buttons("white")
        elif self._last_fill_mode == LEAD_FILL_FREEZE:
            self.align_lead_fill_freeze_radio.setChecked(True)
            self._update_head_mode_buttons("freeze")
        else:
            self.align_lead_fill_black_radio.setChecked(True)
            self._update_head_mode_buttons("black")

    def _set_alignment_nudge_step(self, seconds: float) -> None:
        self._align_nudge_step = seconds
        if not hasattr(self, "align_step_small_button") or not hasattr(self, "align_step_large_button"):
            return
        button_map = {
            self.align_step_small_button: seconds == 0.01,
            self.align_step_large_button: seconds == 0.1,
        }
        for button, checked in button_map.items():
            button.setChecked(checked)
            button.setFont(build_app_ui_font(point_size=10.5, bold=checked))
            button.setStyleSheet(
                (
                    "background: #fff1f2; border: 1px solid #ff4d5e; color: #ff2947;"
                    if checked
                    else "background: #ffffff; border: 1px solid #e4e7ec; color: #1f2937;"
                )
            )

    def _trigger_alignment_export(self, target: str) -> None:
        if target == ALIGN_TARGET_VIDEO:
            self.align_target_video_radio.setChecked(True)
        else:
            self.align_target_audio_radio.setChecked(True)
        if self.align_export_button.isEnabled():
            self.align_export_button.click()

    def _sync_alignment_export_buttons(self) -> None:
        base_enabled = bool(getattr(self, "align_export_button", None) and self.align_export_button.isEnabled())
        is_video_target = bool(getattr(self, "align_target_video_radio", None) and self.align_target_video_radio.isChecked())
        if hasattr(self, "align_mode_export_button"):
            self.align_mode_export_button.setEnabled(base_enabled)
            self.align_mode_export_button.setText(
                "导出对齐视频  Ctrl+S" if is_video_target else "导出对齐 WAV  Ctrl+S"
            )
        video_enabled = base_enabled and is_video_target
        wav_enabled = base_enabled and not is_video_target
        if hasattr(self, "ExportVideoBtn") and self.ExportVideoBtn is not getattr(self, "align_mode_export_button", None):
            self.ExportVideoBtn.setEnabled(video_enabled)
        if hasattr(self, "btn_export_video") and self.btn_export_video is not getattr(self, "align_mode_export_button", None):
            self.btn_export_video.setEnabled(video_enabled)
        if hasattr(self, "ExportWAVBtn") and self.ExportWAVBtn is not getattr(self, "align_mode_export_button", None):
            self.ExportWAVBtn.setEnabled(wav_enabled)
        if hasattr(self, "btn_export_wav") and self.btn_export_wav is not getattr(self, "align_mode_export_button", None):
            self.btn_export_wav.setEnabled(wav_enabled)

    def _reset_alignment_waveform_view(self) -> None:
        self.waveform_view.reset_view()
        self._sync_alignment_zoom_slider()

    def _on_alignment_target_changed(self, *_args) -> None:
        target_track = ALIGN_TARGET_VIDEO if self.align_target_video_radio.isChecked() else ALIGN_TARGET_AUDIO
        if hasattr(self, "preview_timer"):
            self._stop_alignment_preview(log_message=False)
        self.waveform_view.set_target_track(target_track)
        self._refresh_align_target_ui()
        is_subtitle_target = self.rb_adjust_subtitle.isChecked()
        if self.subtitle_adjust_card is not self.original_adjust_card:
            self._set_panel_enabled(self.subtitle_adjust_card, is_subtitle_target)
            self._set_panel_enabled(self.original_adjust_card, not is_subtitle_target)
        has_waveforms = self.waveform_view.video_waveform is not None and self.waveform_view.audio_waveform is not None
        if hasattr(self, "align_control_placeholder"):
            self.align_control_placeholder.setVisible(not has_waveforms)
        if hasattr(self, "align_video_options_widget"):
            self.align_video_options_widget.setVisible(has_waveforms and is_subtitle_target)
        if hasattr(self, "align_audio_offset_widget"):
            self.align_audio_offset_widget.setVisible(has_waveforms and not is_subtitle_target)
        if hasattr(self, "subtitle_accent_bar"):
            self.subtitle_accent_bar.setVisible(is_subtitle_target)
        if hasattr(self, "original_accent_bar"):
            self.original_accent_bar.setVisible(not is_subtitle_target)
        if hasattr(self, "subtitle_adjust_badge"):
            self.subtitle_adjust_badge.setEnabled(False)
        if hasattr(self, "original_adjust_badge"):
            self.original_adjust_badge.setEnabled(False)
        if not is_subtitle_target and hasattr(self, "align_lead_trim_radio"):
            self.align_lead_trim_radio.setChecked(False)
            if hasattr(self, "align_head_trim_row_widget"):
                self.align_head_trim_row_widget.setVisible(False)
            self.align_lead_trim_seconds_spin.setEnabled(False)
        self._apply_alignment_mode_styles()
        self._sync_alignment_export_buttons()
        self._refresh_alignment_export_panels()
        self._persist_alignment_preferences()

    def _set_panel_enabled(self, panel: QWidget, enabled: bool):
        for w in panel.findChildren(QWidget):
            w.setEnabled(enabled)

    def _alignment_accent_color(self) -> str:
        return "#F04452" if self._is_align_video_target() else "#2F6BFF"

    def _apply_alignment_mode_styles(self) -> None:
        if not hasattr(self, "align_target_video_radio"):
            return
        accent = self._alignment_accent_color()
        neutral_border = "#D0D5DD"
        segment_button_style = f"""
        QPushButton {{
            background: transparent;
            color: #1F2937;
            border: 0;
            border-radius: 7px;
            padding: 8px 12px;
        }}
        QPushButton:checked {{
            background: {accent};
            color: #FFFFFF;
            border: 0;
        }}
        QPushButton:disabled {{
            background: transparent;
            color: #9CA3AF;
            border: 0;
        }}
        """
        if hasattr(self, "align_target_video_button"):
            self.align_target_video_button.setChecked(self.align_target_video_radio.isChecked())
            self.align_target_audio_button.setChecked(self.align_target_audio_radio.isChecked())
            self.align_target_video_button.setStyleSheet(segment_button_style)
            self.align_target_audio_button.setStyleSheet(segment_button_style)
            self.align_target_video_button.setFont(
                build_app_ui_font(point_size=10.5, bold=self.align_target_video_button.isChecked())
            )
            self.align_target_audio_button.setFont(
                build_app_ui_font(point_size=10.5, bold=self.align_target_audio_button.isChecked())
            )
        button_style = f"""
        QPushButton {{
            background: {accent};
            color: #FFFFFF;
            border: 0;
            border-radius: 8px;
            padding: 7px 14px;
            font-size: 11pt;
        }}
        QPushButton:disabled {{
            background: #E5E7EB;
            color: #9CA3AF;
        }}
        """
        if hasattr(self, "align_mode_export_button"):
            self.align_mode_export_button.setStyleSheet(button_style)
            self.align_mode_export_button.setFont(build_app_ui_font(point_size=11, bold=True))
        if hasattr(self, "align_offset_label"):
            self.align_offset_label.setText(format_offset(self.waveform_view.offset_seconds))
            self.align_offset_label.setStyleSheet(
                f'color: {accent}; font-family: "Microsoft YaHei UI"; font-size: 24pt; background: transparent; border: 0;'
            )
            self.align_offset_label.setFont(build_app_ui_font(point_size=24, bold=True))
        if hasattr(self, "align_nudge_panel"):
            self.align_nudge_panel.setStyleSheet(
                f"""
                QFrame#AlignNudgePanel {{
                    background: #FFFFFF;
                    border: 1px solid rgba(229, 231, 235, 0.75);
                    border-radius: 10px;
                }}
                QPushButton {{
                    background: #FFFFFF;
                    border: 1px solid #E5E7EB;
                    border-radius: 7px;
                    padding: 5px 12px;
                }}
                QPushButton:hover {{
                    border-color: {accent};
                    color: {accent};
                }}
                """
            )

    def _refresh_original_adjustment_panel(self, seconds: float) -> None:
        if hasattr(self, "align_offset_label"):
            self.align_offset_label.setText(format_offset(seconds))

    def _refresh_alignment_export_panels(self) -> None:
        video_waveform = self.waveform_view.video_waveform
        audio_waveform = self.waveform_view.audio_waveform

        if video_waveform is None:
            video_duration_text = "时长未知"
            video_origin_text = "时长未知"
        else:
            video_origin_text = format_media_duration(video_waveform.duration)
            video_duration_seconds = max(0.0, video_waveform.duration + self.waveform_view.offset_seconds)
            trim_duration = self._compute_video_trim_duration()
            if trim_duration is not None:
                video_duration_seconds = trim_duration
            video_duration_text = format_media_duration(video_duration_seconds)

        if audio_waveform is None:
            audio_duration_text = "时长未知"
            audio_origin_text = "时长未知"
        else:
            audio_origin_text = format_media_duration(audio_waveform.duration)
            audio_duration_seconds = max(0.0, audio_waveform.duration + self.waveform_view.offset_seconds)
            audio_duration_text = format_media_duration(audio_duration_seconds)

        self.align_video_export_duration_label.setText(video_duration_text)
        self.align_video_export_origin_label.setText(f"(原始时长: {video_origin_text})")
        self.align_audio_export_duration_label.setText(audio_duration_text)
        self.align_audio_export_origin_label.setText(f"(原始时长: {audio_origin_text})")
        if hasattr(self, "align_export_duration_label"):
            is_video_target = self._is_align_video_target()
            duration_text = video_duration_text if is_video_target else audio_duration_text
            origin_text = video_origin_text if is_video_target else audio_origin_text
            if duration_text == "时长未知":
                self.align_export_duration_label.setText("预计时长 —:—（时长未知）")
                self.align_export_duration_label.setStyleSheet(
                    'color: #667085; font-family: "Microsoft YaHei UI"; font-size: 16pt; font-weight: 500; background: transparent; border: 0;'
                )
                self.align_export_origin_label.setText("")
            else:
                self.align_export_duration_label.setText(f"预计时长 {duration_text}")
                self.align_export_duration_label.setStyleSheet(
                    'color: #1F2937; font-family: "Microsoft YaHei UI"; font-size: 18pt; font-weight: 500; background: transparent; border: 0;'
                )
                self.align_export_origin_label.setText(f"(原始 {origin_text})")
            self._sync_alignment_export_buttons()
            self._apply_alignment_mode_styles()

    def _load_settings_into_ui(self) -> None:
        self._loading_settings_into_ui = True
        self.set_ffmpeg_dir(Path(self.settings.ffmpeg_dir) if self.settings.ffmpeg_dir.strip() else Path())
        self.set_output_name_mode(self.settings.output_name_mode)
        self.set_output_name_templates(self.settings.on_name_template, self.settings.off_name_template)
        self.align_video_name_template_value = self.settings.align_video_name_template or DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE
        self.align_audio_name_template_value = self.settings.align_audio_name_template or DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE
        if self.settings.align_target == ALIGN_TARGET_AUDIO:
            self.align_target_audio_radio.setChecked(True)
        else:
            self.align_target_video_radio.setChecked(True)
        self._align_encode_selection = (
            self.settings.align_encode_mode
            if self.settings.align_encode_mode in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}
            else ENCODE_MODE_SOFTWARE
        )
        if self._align_encode_selection == ENCODE_MODE_HARDWARE:
            self.align_encode_hardware_radio.setChecked(True)
        else:
            self.align_encode_software_radio.setChecked(True)
        self.align_force_1080p60_check.setChecked(bool(self.settings.align_force_1080p60))
        self.align_use_video_audio_check.setChecked(bool(self.settings.align_export_use_video_audio))
        self._restore_lyrics_preferences()
        self._loading_settings_into_ui = False

    def _restore_lyrics_preferences(self) -> None:
        saved_source_ids = tuple(str(item) for item in (self.settings.lyrics_source_ids or DEFAULT_LYRICS_PROVIDER_IDS) if str(item))
        if not saved_source_ids:
            saved_source_ids = DEFAULT_LYRICS_PROVIDER_IDS
        for index, (label, provider_ids) in enumerate(LYRICS_SOURCE_OPTIONS):
            if provider_ids == saved_source_ids:
                self.lyrics_source_combo.setCurrentIndex(index)
                break

        saved_preview_mode = str(self.settings.lyrics_preview_mode or LYRICS_PREVIEW_LINE)
        for index, (label, mode) in enumerate(LYRICS_PREVIEW_MODE_OPTIONS):
            if mode == saved_preview_mode:
                self.lyrics_preview_mode_combo.setCurrentIndex(index)
                break
        saved_language = str(self.settings.lyrics_language or LYRICS_LANGUAGE_ORIGINAL)
        for index, (label, value) in enumerate(LYRICS_LANGUAGE_OPTIONS):
            if value == saved_language:
                self.lyrics_language_combo.setCurrentIndex(index)
                break
        self.lyrics_strip_intro_checkbox.setChecked(bool(self.settings.lyrics_strip_intro_lines))

    def _install_single_click_combo_behavior(self, combo: QComboBox) -> None:
        popup_view = getattr(combo, "view", None)
        if not callable(popup_view):
            return
        view = popup_view()
        if view is None:
            return
        view.pressed.connect(lambda index, combo=combo: self._handle_combo_popup_pressed(combo, index.row()))

    def _handle_combo_popup_pressed(self, combo: QComboBox, row: int) -> None:
        if row < 0 or row >= combo.count():
            return
        combo.setCurrentIndex(row)
        hide_popup = getattr(combo, "hidePopup", None)
        if callable(hide_popup):
            hide_popup()

    def _current_lyrics_source_ids(self) -> tuple[str, ...]:
        return LYRICS_SOURCE_MAP.get(self.lyrics_source_combo.currentText(), DEFAULT_LYRICS_PROVIDER_IDS)

    def _current_lyrics_preview_mode(self) -> str:
        return LYRICS_PREVIEW_MODE_MAP.get(self.lyrics_preview_mode_combo.currentText(), LYRICS_PREVIEW_LINE)

    def _current_lyrics_language(self) -> str:
        return LYRICS_LANGUAGE_MAP.get(self.lyrics_language_combo.currentText(), LYRICS_LANGUAGE_ORIGINAL)

    def _update_lyrics_language_combo_state(self, candidate: LyricsSearchCandidate | None) -> None:
        combo = getattr(self, "lyrics_language_combo", None)
        if combo is None:
            return
        has_translation = bool(candidate is not None and candidate.has_translation)
        translation_index = next(
            (i for i, (_label, value) in enumerate(LYRICS_LANGUAGE_OPTIONS) if value == LYRICS_LANGUAGE_TRANSLATION),
            -1,
        )
        if translation_index >= 0:
            set_item_enabled = getattr(combo, "setItemEnabled", None)
            if callable(set_item_enabled):
                set_item_enabled(translation_index, has_translation)
            else:
                # Fallback for plain QComboBox / future swap.
                model = combo.model() if hasattr(combo, "model") else None
                item = model.item(translation_index) if model is not None and hasattr(model, "item") else None
                if item is not None:
                    flags = item.flags()
                    if has_translation:
                        item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    else:
                        item.setFlags(flags & ~(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
        # If user has selected translation but the currently loaded song doesn't
        # have one, silently fall back to original *without* persisting, so the
        # saved preference is preserved for the next song that does have a
        # translation.
        if (
            candidate is not None
            and candidate.lyrics_loaded
            and not has_translation
            and self._current_lyrics_language() == LYRICS_LANGUAGE_TRANSLATION
        ):
            previous = self._loading_settings_into_ui
            self._loading_settings_into_ui = True
            try:
                for index, (_label, value) in enumerate(LYRICS_LANGUAGE_OPTIONS):
                    if value == LYRICS_LANGUAGE_ORIGINAL:
                        combo.setCurrentIndex(index)
                        break
            finally:
                self._loading_settings_into_ui = previous

    def _persist_lyrics_preferences(self, *_args) -> None:
        if self._loading_settings_into_ui:
            return
        source_ids = self._current_lyrics_source_ids()
        preview_mode = self._current_lyrics_preview_mode()
        language = self._current_lyrics_language()
        self.settings.lyrics_source_ids = tuple(source_ids)
        self.settings.lyrics_preview_mode = preview_mode
        self.settings.lyrics_language = language
        self.settings.lyrics_strip_intro_lines = self.lyrics_strip_intro_checkbox.isChecked()
        save_app_settings(self.settings)

    def _persist_alignment_preferences(self, *_args) -> None:
        if self._loading_settings_into_ui:
            return
        self._update_alignment_preferences_from_ui()
        save_app_settings(self.settings)

    def _handle_alignment_encode_mode_toggled(self, encode_mode: str, checked: bool) -> None:
        if not checked:
            return
        self._align_encode_selection = (
            encode_mode if encode_mode in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE} else ENCODE_MODE_SOFTWARE
        )
        self._persist_alignment_preferences()

    def _current_alignment_encode_mode(self) -> str:
        return (
            self._align_encode_selection
            if self._align_encode_selection in {ENCODE_MODE_SOFTWARE, ENCODE_MODE_HARDWARE}
            else ENCODE_MODE_SOFTWARE
        )

    def _update_alignment_preferences_from_ui(self) -> None:
        if hasattr(self, "align_target_video_radio"):
            self.settings.align_target = (
                ALIGN_TARGET_VIDEO if self.align_target_video_radio.isChecked() else ALIGN_TARGET_AUDIO
            )
        self.settings.align_encode_mode = self._current_alignment_encode_mode()
        if hasattr(self, "align_force_1080p60_check"):
            self.settings.align_force_1080p60 = self.align_force_1080p60_check.isChecked()
        if hasattr(self, "align_use_video_audio_check"):
            self.settings.align_export_use_video_audio = self.align_use_video_audio_check.isChecked()

    def _sync_ffmpeg_labels(self) -> None:
        self.hires_ffmpeg_label.setText(self.ffmpeg_dir_text or FFMPEG_DIR_PLACEHOLDER)
        self._refresh_media_info_labels()

    def set_video_path(self, path: Path) -> None:
        self.video_zone.set_path(path)
        self.output_dir_label.setText(str(resolve_output_dir(path)))

    def set_on_vocal_path(self, path: Path) -> None:
        self.on_vocal_zone.set_path(path)

    def set_off_vocal_path(self, path: Path) -> None:
        self.off_vocal_zone.set_path(path)

    def set_align_video_path(self, path: Path) -> None:
        self.align_video_zone.set_path(path)
        self.align_video_info_label.setText(self._build_media_info(path, "字幕视频"))
        self._invalidate_alignment_waveforms()
        self._refresh_alignment_material_inputs()

    def set_align_audio_path(self, path: Path) -> None:
        self.align_audio_zone.set_path(path)
        self.align_audio_info_label.setText(self._build_media_info(path, "原唱音源"))
        self._invalidate_alignment_waveforms()
        self._refresh_alignment_material_inputs()

    def set_ffmpeg_dir(self, path: Path) -> None:
        self.ffmpeg_dir_text = str(path) if str(path).strip() else ""
        self._sync_ffmpeg_labels()

    def set_output_name_mode(self, mode: str) -> None:
        if mode == OUTPUT_NAME_MODE_VIDEO_NAME:
            mode = OUTPUT_NAME_MODE_TEMPLATE
            self.set_output_name_templates(DEFAULT_ON_NAME_TEMPLATE, DEFAULT_OFF_NAME_TEMPLATE)
        if mode not in {OUTPUT_NAME_MODE_FIXED, OUTPUT_NAME_MODE_TEMPLATE}:
            raise ProcessingError(f"不支持的输出命名模式: {mode}")
        self.output_name_mode_value = mode

    def set_output_name_templates(self, on_template: str, off_template: str) -> None:
        self.on_name_template_value = on_template
        self.off_name_template_value = off_template

    def _choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择字幕视频", "", "视频文件 (*.mkv *.mp4 *.mov *.avi);;所有文件 (*.*)")
        if path:
            self.set_video_path(Path(path))

    def _choose_on_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择原唱音频",
            "",
            "音频文件 (*.flac *.wav *.mp3 *.m4a *.aac *.ape *.alac *.mkv *.mp4);;所有文件 (*.*)",
        )
        if path:
            self.set_on_vocal_path(Path(path))

    def _choose_off_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择伴奏音频",
            "",
            "音频文件 (*.flac *.wav *.mp3 *.m4a *.aac *.ape *.alac *.mkv *.mp4);;所有文件 (*.*)",
        )
        if path:
            self.set_off_vocal_path(Path(path))

    def _choose_align_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择用于对齐的字幕视频", "", "视频文件 (*.mkv *.mp4 *.mov *.avi);;所有文件 (*.*)")
        if path:
            self.set_align_video_path(Path(path))

    def _choose_align_audio(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择需要对齐的原唱音源", "", "音频或 MP4 文件 (*.flac *.wav *.mp3 *.m4a *.aac *.ape *.alac *.mkv *.mp4);;所有文件 (*.*)")
        if path:
            self.set_align_audio_path(Path(path))

    def _refresh_media_info_labels(self) -> None:
        self.align_video_info_label.setText(self._build_media_info(self.align_video_zone.path, "字幕视频"))
        self.align_audio_info_label.setText(self._build_media_info(self.align_audio_zone.path, "原唱音源"))
        if hasattr(self, "align_material_status_label"):
            self._refresh_alignment_material_inputs()

    def _build_media_info(self, path: Path | None, label: str) -> str:
        if path is None:
            return f"{label}: 时长未知"
        cache_key = path.expanduser()
        cached_duration = self._media_duration_cache.get(cache_key)
        if cached_duration is not None:
            return f"{label}: {cached_duration}"
        try:
            ffprobe_path = find_tool("ffprobe.exe", self._resolve_ffmpeg_dir())
            info = probe_media(ffprobe_path, path)
        except Exception:  # noqa: BLE001
            return f"{label}: 时长未知"
        duration_text = format_media_duration(info.duration)
        self._media_duration_cache[cache_key] = duration_text
        return f"{label}: {duration_text}"

    def _refresh_alignment_material_inputs(self) -> None:
        if not hasattr(self, "align_material_status_label"):
            return
        has_video = self.align_video_zone.path is not None
        has_audio = self.align_audio_zone.path is not None
        count = int(has_video) + int(has_audio)
        self.align_video_zone.set_balanced_height(None)
        self.align_audio_zone.set_balanced_height(None)
        if count == 0:
            status_text = "① 先导入素材"
            status_style = "background: #FFF1F2; color: #F04452; border: 1px solid #FFD1D8;"
            self.align_video_zone.set_display_mode("empty")
            self.align_audio_zone.set_display_mode("empty")
            self._align_empty_material_card_height = max(
                self.align_video_zone.sizeHint().height(),
                self.align_audio_zone.sizeHint().height(),
            )
        elif count == 1:
            missing = "原唱音频" if has_video else "字幕视频"
            status_text = f"● 已导入 1/2 · 还差{missing}"
            status_style = "background: transparent; color: #1F2937; border: 0;"
            self.align_video_zone.set_display_mode("ready" if has_video else "empty", missing_text="还需导入字幕视频")
            self.align_audio_zone.set_display_mode("ready" if has_audio else "empty", missing_text="还需导入原唱音频")
        else:
            status_text = "已导入 2/2"
            status_style = "background: transparent; color: #667085; border: 0;"
            self.align_video_zone.set_display_mode("chip")
            self.align_audio_zone.set_display_mode("chip")
        if count == 1:
            balanced_height = getattr(
                self,
                "_align_empty_material_card_height",
                max(self.align_video_zone.sizeHint().height(), self.align_audio_zone.sizeHint().height()),
            )
            self.align_video_zone.set_balanced_height(balanced_height)
            self.align_audio_zone.set_balanced_height(balanced_height)
        self.align_material_status_label.setText(status_text)
        self.align_material_status_label.setStyleSheet(
            f"{status_style} border-radius: 7px; padding: 2px 10px;"
        )
        self.align_material_status_label.setFont(build_app_ui_font(point_size=10.5, bold=True))
        if self.align_clear_button is not None:
            self.align_clear_button.setVisible(count >= 1)
        if hasattr(self, "align_waveform_placeholder"):
            if count == 1:
                self.align_waveform_placeholder.setText(
                    f"再导入{'原唱音频' if has_video else '字幕视频'}后，点击「生成波形」即可在此查看对齐视图"
                )
            else:
                self.align_waveform_placeholder.setText(
                    "导入字幕视频与原唱音源后，点击「生成波形」即可在此查看对齐视图"
                )

    def _open_settings_window(self, context: str) -> None:
        dialog = QDialog(self)
        title = "波形对齐设置" if context == "align" else "Hi-Res 生成设置"
        dialog.setWindowTitle(f"{APP_TITLE} - {title}")
        dialog.resize(860, 540)

        outer = QVBoxLayout(dialog)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        shell = QVBoxLayout(content)
        shell.setContentsMargins(20, 20, 20, 20)
        shell.setSpacing(18)

        heading = QLabel(title)
        heading.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 18pt; font-weight: 700;')
        shell.addWidget(heading)

        ffmpeg_panel = QFrame()
        ffmpeg_panel.setObjectName("WhitePanel")
        ffmpeg_layout = QGridLayout(ffmpeg_panel)
        ffmpeg_layout.setContentsMargins(14, 14, 14, 14)
        ffmpeg_title = QLabel("FFmpeg 目录")
        ffmpeg_title.setObjectName("PanelTitle")
        ffmpeg_display = QLineEdit(dialog)
        ffmpeg_display.setText(self.ffmpeg_dir_text)
        ffmpeg_display.setPlaceholderText(FFMPEG_DIR_PLACEHOLDER)
        choose_button = QPushButton("选择目录")
        choose_button.clicked.connect(
            lambda: self._choose_ffmpeg_for_dialog(dialog, ffmpeg_display)
        )
        system_button = QPushButton("使用系统 PATH")
        system_button.clicked.connect(lambda: ffmpeg_display.setText(""))
        ffmpeg_hint_1 = QLabel("推荐直接选择 ffmpeg 的 bin 目录，例如 D:\\tools\\ffmpeg\\bin。")
        ffmpeg_hint_1.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
        ffmpeg_hint_2 = QLabel("也可以选择 ffmpeg 根目录，程序会尝试其中的 bin\\ffmpeg.exe 和 bin\\ffprobe.exe。")
        ffmpeg_hint_2.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
        ffmpeg_layout.addWidget(ffmpeg_title, 0, 0)
        ffmpeg_layout.addWidget(ffmpeg_display, 0, 1)
        ffmpeg_layout.addWidget(choose_button, 0, 2)
        ffmpeg_layout.addWidget(system_button, 0, 3)
        ffmpeg_layout.addWidget(ffmpeg_hint_1, 1, 1, 1, 3)
        ffmpeg_layout.addWidget(ffmpeg_hint_2, 2, 1, 1, 3)
        ffmpeg_layout.setColumnStretch(1, 1)
        shell.addWidget(ffmpeg_panel)

        status_label = QLabel("")
        status_label.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #177245;')

        if context == "align":
            naming_panel = QFrame()
            naming_panel.setObjectName("WhitePanel")
            naming_layout = QGridLayout(naming_panel)
            naming_layout.setContentsMargins(14, 14, 14, 14)
            naming_title = QLabel("对齐导出命名")
            naming_title.setObjectName("PanelTitle")
            video_template_edit = QLineEdit(dialog)
            video_template_edit.setText(self.align_video_name_template_value)
            audio_template_edit = QLineEdit(dialog)
            audio_template_edit.setText(self.align_audio_name_template_value)
            naming_help_1 = QLabel("默认: 对齐后视频 {video_name}_aligned.mp4；对齐后音频 {audio_name}_aligned.wav。")
            naming_help_1.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
            naming_help_2 = QLabel("视频模板支持 {video_name}；音频模板支持 {audio_name} 和 {video_name}。不用写扩展名。")
            naming_help_2.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
            naming_layout.addWidget(naming_title, 0, 0)
            naming_layout.addWidget(QLabel("对齐后视频模板"), 1, 0)
            naming_layout.addWidget(video_template_edit, 1, 1)
            naming_layout.addWidget(QLabel("对齐后音频模板"), 2, 0)
            naming_layout.addWidget(audio_template_edit, 2, 1)
            naming_layout.addWidget(naming_help_1, 3, 1)
            naming_layout.addWidget(naming_help_2, 4, 1)
            naming_layout.setColumnStretch(1, 1)
            shell.addWidget(naming_panel)
        else:
            naming_panel = QFrame()
            naming_panel.setObjectName("WhitePanel")
            naming_layout = QGridLayout(naming_panel)
            naming_layout.setContentsMargins(14, 14, 14, 14)
            naming_title = QLabel("输出命名")
            naming_title.setObjectName("PanelTitle")
            mode_group = QButtonGroup(dialog)
            fixed_radio = QRadioButton("默认命名: on_vocal.mkv / off_vocal.mkv")
            template_radio = QRadioButton("自定义模板: 使用你自己的命名样式")
            mode_group.addButton(fixed_radio)
            mode_group.addButton(template_radio)
            if self.output_name_mode_value == OUTPUT_NAME_MODE_TEMPLATE:
                template_radio.setChecked(True)
            else:
                fixed_radio.setChecked(True)
            on_template_edit = QLineEdit(dialog)
            on_template_edit.setText(self.on_name_template_value)
            off_template_edit = QLineEdit(dialog)
            off_template_edit.setText(self.off_name_template_value)

            def sync_template_enabled() -> None:
                enabled = template_radio.isChecked()
                on_template_edit.setEnabled(enabled)
                off_template_edit.setEnabled(enabled)

            fixed_radio.toggled.connect(lambda _checked: sync_template_enabled())
            template_radio.toggled.connect(lambda _checked: sync_template_enabled())
            sync_template_enabled()

            naming_help_1 = QLabel("支持占位符 {video_name}。不用写 .mkv。示例: {video_name}_karaoke_on")
            naming_help_1.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
            naming_help_2 = QLabel("默认: 原唱 on_vocal.mkv；伴奏 off_vocal.mkv。")
            naming_help_2.setStyleSheet('font-family: "Microsoft YaHei UI"; font-size: 9pt; color: #6b7280;')
            naming_layout.addWidget(naming_title, 0, 0)
            naming_layout.addWidget(fixed_radio, 1, 1)
            naming_layout.addWidget(template_radio, 2, 1)
            naming_layout.addWidget(QLabel("原唱模板"), 3, 0)
            naming_layout.addWidget(on_template_edit, 3, 1)
            naming_layout.addWidget(QLabel("伴奏模板"), 4, 0)
            naming_layout.addWidget(off_template_edit, 4, 1)
            naming_layout.addWidget(naming_help_1, 5, 1)
            naming_layout.addWidget(naming_help_2, 6, 1)
            naming_layout.setColumnStretch(1, 1)
            shell.addWidget(naming_panel)

        shell.addWidget(status_label)

        controls = QHBoxLayout()
        controls.addStretch(1)
        save_button = QPushButton("保存设置")
        close_button = QPushButton("关闭")
        close_button.clicked.connect(dialog.close)

        def save_settings_from_dialog() -> None:
            try:
                mode = self.output_name_mode_value
                on_template = self.on_name_template_value
                off_template = self.off_name_template_value
                align_video_template = self.align_video_name_template_value
                align_audio_template = self.align_audio_name_template_value
                if context == "align":
                    align_video_template = video_template_edit.text().strip() or DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE
                    align_audio_template = audio_template_edit.text().strip() or DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE
                else:
                    mode = OUTPUT_NAME_MODE_TEMPLATE if template_radio.isChecked() else OUTPUT_NAME_MODE_FIXED
                    on_template = on_template_edit.text().strip() or DEFAULT_ON_NAME_TEMPLATE
                    off_template = off_template_edit.text().strip() or DEFAULT_OFF_NAME_TEMPLATE

                saved_path = self._save_settings_payload(
                    output_name_mode=mode,
                    on_template=on_template,
                    off_template=off_template,
                    align_video_template=align_video_template,
                    align_audio_template=align_audio_template,
                    ffmpeg_dir_text=ffmpeg_display.text().strip(),
                )
            except ProcessingError as exc:
                QMessageBox.critical(dialog, APP_TITLE, str(exc))
                return

            status_label.setText("设置已保存到本地。")
            QMessageBox.information(dialog, APP_TITLE, f"设置已保存：\n{saved_path}")

        save_button.clicked.connect(save_settings_from_dialog)
        controls.addWidget(save_button)
        controls.addWidget(close_button)
        shell.addLayout(controls)
        dialog.exec()

    def _choose_ffmpeg_for_dialog(self, parent: QWidget, target: QLineEdit) -> None:
        path = QFileDialog.getExistingDirectory(parent, "选择 ffmpeg 所在目录")
        if path:
            target.setText(path)

    def _save_settings_payload(
        self,
        *,
        output_name_mode: str,
        on_template: str,
        off_template: str,
        align_video_template: str,
        align_audio_template: str,
        ffmpeg_dir_text: str,
    ) -> Path:
        ffmpeg_dir = Path(ffmpeg_dir_text).expanduser() if ffmpeg_dir_text.strip() else None
        if ffmpeg_dir is not None and not ffmpeg_dir.is_dir():
            raise ProcessingError("所选 ffmpeg 目录无效，请重新选择。")

        if output_name_mode not in {OUTPUT_NAME_MODE_FIXED, OUTPUT_NAME_MODE_TEMPLATE}:
            raise ProcessingError("输出命名模式无效，请重新选择。")
        if output_name_mode == OUTPUT_NAME_MODE_TEMPLATE:
            on_template = validate_output_name_template(on_template, "原唱")
            off_template = validate_output_name_template(off_template, "伴奏")

        align_video_template = self._validate_alignment_name_template(
            align_video_template,
            "对齐后视频",
            allowed_fields={"video_name"},
            extensions=(".mp4", ".mkv"),
        )
        align_audio_template = self._validate_alignment_name_template(
            align_audio_template,
            "对齐后音频",
            allowed_fields={"audio_name", "video_name"},
            extensions=(".wav",),
        )

        self.output_name_mode_value = output_name_mode
        self.on_name_template_value = on_template
        self.off_name_template_value = off_template
        self.align_video_name_template_value = align_video_template
        self.align_audio_name_template_value = align_audio_template
        self.ffmpeg_dir_text = str(ffmpeg_dir) if ffmpeg_dir else ""
        self._sync_ffmpeg_labels()
        self.settings.output_name_mode = self.output_name_mode_value
        self.settings.on_name_template = self.on_name_template_value
        self.settings.off_name_template = self.off_name_template_value
        self.settings.align_video_name_template = self.align_video_name_template_value
        self.settings.align_audio_name_template = self.align_audio_name_template_value
        self.settings.ffmpeg_dir = self.ffmpeg_dir_text
        self._update_alignment_preferences_from_ui()
        return save_app_settings(self.settings)

    def _resolve_output_name_mode(self) -> str:
        if self.output_name_mode_value not in {OUTPUT_NAME_MODE_FIXED, OUTPUT_NAME_MODE_TEMPLATE}:
            raise ProcessingError("输出命名模式无效，请重新选择。")
        return self.output_name_mode_value

    def _resolve_output_name_templates(self, *, require_valid: bool) -> tuple[str, str]:
        on_template = self.on_name_template_value or DEFAULT_ON_NAME_TEMPLATE
        off_template = self.off_name_template_value or DEFAULT_OFF_NAME_TEMPLATE
        if require_valid:
            on_template = validate_output_name_template(on_template, "原唱")
            off_template = validate_output_name_template(off_template, "伴奏")
        return on_template, off_template

    def _resolve_alignment_name_templates(self, *, require_valid: bool) -> tuple[str, str]:
        video_template = self.align_video_name_template_value or DEFAULT_ALIGNED_VIDEO_NAME_TEMPLATE
        audio_template = self.align_audio_name_template_value or DEFAULT_ALIGNED_AUDIO_NAME_TEMPLATE
        if require_valid:
            video_template = self._validate_alignment_name_template(
                video_template,
                "对齐后视频",
                allowed_fields={"video_name"},
                extensions=(".mp4", ".mkv"),
            )
            audio_template = self._validate_alignment_name_template(
                audio_template,
                "对齐后音频",
                allowed_fields={"audio_name", "video_name"},
                extensions=(".wav",),
            )
        return video_template, audio_template

    def _validate_alignment_name_template(
        self,
        template: str,
        label: str,
        *,
        allowed_fields: set[str],
        extensions: tuple[str, ...],
    ) -> str:
        normalized = template.strip()
        for extension in extensions:
            if normalized.lower().endswith(extension):
                normalized = normalized[: -len(extension)].rstrip()
                break
        if not normalized:
            raise ProcessingError(f"{label}模板不能为空。")
        if "/" in normalized or "\\" in normalized:
            raise ProcessingError(f"{label}模板不能包含路径分隔符。")
        for _, field_name, _, _ in ALIGNMENT_TEMPLATE_FORMATTER.parse(normalized):
            if field_name and field_name not in allowed_fields:
                supported = "、".join(f"{{{name}}}" for name in sorted(allowed_fields))
                raise ProcessingError(f"{label}模板包含不支持的占位符 {field_name}。当前支持 {supported}。")
        return normalized

    def _resolve_ffmpeg_dir(self) -> Path | None:
        if not self.ffmpeg_dir_text.strip():
            return None
        path = Path(self.ffmpeg_dir_text).expanduser()
        if not path.is_dir():
            raise ProcessingError("所选 ffmpeg 目录无效，请重新选择。")
        return path

    def _validate_hires_inputs(
        self,
    ) -> tuple[Path, Path | None, Path | None, Path, Path | None, str, str | None, str | None]:
        video_path = self.video_zone.path
        on_vocal_path = self.on_vocal_zone.path
        off_vocal_path = self.off_vocal_zone.path
        ffmpeg_dir = self._resolve_ffmpeg_dir()
        output_name_mode = self._resolve_output_name_mode()

        missing: list[str] = []
        if video_path is None or not video_path.is_file():
            missing.append("字幕视频")
        if on_vocal_path is not None and not on_vocal_path.is_file():
            missing.append("原唱音频")
        if off_vocal_path is not None and not off_vocal_path.is_file():
            missing.append("伴奏音频")
        if missing:
            raise ProcessingError(f"请先选择有效的文件: {', '.join(missing)}")
        assert video_path is not None

        if on_vocal_path is None and off_vocal_path is None:
            raise ProcessingError("请至少选择原唱音频或伴奏音频中的一个。")
        if (
            on_vocal_path is not None
            and off_vocal_path is not None
            and on_vocal_path.resolve() == off_vocal_path.resolve()
        ):
            raise ProcessingError("原唱音频和伴奏音频不能是同一个文件。")

        output_dir = resolve_output_dir(video_path)
        if output_name_mode == OUTPUT_NAME_MODE_TEMPLATE:
            on_template, off_template = self._resolve_output_name_templates(require_valid=True)
        else:
            on_template, off_template = None, None

        return (
            video_path,
            on_vocal_path,
            off_vocal_path,
            output_dir,
            ffmpeg_dir,
            output_name_mode,
            on_template,
            off_template,
        )

    def _set_hires_status_color(self, color: str) -> None:
        self.hires_status_label.setStyleSheet(
            f'font-family: "Microsoft YaHei UI"; font-size: 10pt; font-weight: 400; color: {color};'
        )

    def _copy_hires_log(self) -> None:
        QApplication.clipboard().setText(self.hires_log.toPlainText())

    def _is_hires_running(self) -> bool:
        return self.hires_task is not None and self.hires_task.isRunning()

    def _register_hires_process(self, process: subprocess.Popen | None) -> None:
        self._hires_process = process

    def _cleanup_incomplete_hires_outputs(self) -> None:
        completed = set(self._hires_completed_outputs)
        for path in self._hires_expected_outputs:
            if path in completed or path in self._hires_preexisting_outputs or not path.exists():
                continue
            try:
                path.unlink()
                self._append_hires_log(f"已清理未完成的输出文件: {path}")
            except OSError as exc:
                self._append_hires_log(f"清理未完成的输出文件失败: {path} ({exc})")

    def _reset_hires_cancel_state(self) -> None:
        self._hires_cancel_requested = False
        self._hires_process = None
        self._hires_expected_outputs = []
        self._hires_completed_outputs = []
        self._hires_preexisting_outputs = set()

    def _stop_hires(self) -> None:
        if not self._is_hires_running():
            return
        if not self._hires_cancel_requested:
            self._hires_cancel_requested = True
            self.hires_cancel_button.setEnabled(False)
            self.hires_status_label.setText("正在取消…")
            self._set_hires_status_color("#475467")
            self._append_hires_log("正在取消生成…")
        process = self._hires_process
        if process is not None:
            terminate_process(process)

    def _start_hires(self) -> None:
        if self._is_hires_running():
            QMessageBox.information(self, APP_TITLE, "当前任务还在处理中，请稍等。")
            return

        try:
            args = self._validate_hires_inputs()
        except ProcessingError as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))
            return

        self.hires_log.clear()
        (
            video_path,
            on_vocal_path,
            off_vocal_path,
            output_dir,
            _ffmpeg_dir,
            output_name_mode,
            on_template,
            off_template,
        ) = args
        on_output, off_output = resolve_output_paths(
            video_path,
            output_dir,
            output_name_mode,
            on_name_template=on_template,
            off_name_template=off_template,
            include_on=on_vocal_path is not None,
            include_off=off_vocal_path is not None,
        )
        self._hires_cancel_requested = False
        self._hires_process = None
        self._hires_expected_outputs = [path for path in (on_output, off_output) if path is not None]
        self._hires_completed_outputs = []
        self._hires_preexisting_outputs = {path for path in self._hires_expected_outputs if path.exists()}
        self.hires_start_button.setEnabled(False)
        self.hires_cancel_button.setEnabled(True)
        self.hires_progress.setRange(0, 0)
        self.hires_status_label.setText("处理中…")
        self._set_hires_status_color("#2f6fed")

        def runner(logger: Callable[[str], None]) -> list[Path]:
            (
                video_path,
                on_vocal_path,
                off_vocal_path,
                output_dir,
                ffmpeg_dir,
                output_name_mode,
                on_template,
                off_template,
            ) = args
            outputs = run_pipeline(
                video_path=video_path,
                on_vocal_path=on_vocal_path,
                off_vocal_path=off_vocal_path,
                output_dir=output_dir,
                ffmpeg_dir=ffmpeg_dir,
                output_name_mode=output_name_mode,
                on_name_template=on_template,
                off_name_template=off_template,
                logger=logger,
                should_cancel=lambda: self._hires_cancel_requested,
                on_process_started=self._register_hires_process,
            )
            self._hires_completed_outputs.extend(outputs)
            return outputs

        task = self._track_background_task("hires_task", BackgroundTask(runner))
        task.log_message.connect(self._append_hires_log)
        task.task_succeeded.connect(self._finish_hires_success)
        task.task_failed.connect(self._finish_hires_failure)
        task.start()

    def _append_hires_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.hires_log.appendPlainText(f"[{timestamp}] {message}")

    def _append_align_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.align_log.appendPlainText(f"[{timestamp}] {message}")

    def _finish_hires_success(self, outputs: object) -> None:
        was_cancelled = self._hires_cancel_requested
        self._hires_process = None
        self.hires_progress.setRange(0, 1)
        self.hires_progress.setValue(0 if was_cancelled else 1)
        self.hires_start_button.setEnabled(True)
        self.hires_cancel_button.setEnabled(False)
        if was_cancelled:
            self._cleanup_incomplete_hires_outputs()
            self.hires_status_label.setText("生成已取消")
            self._set_hires_status_color("#475467")
            self._append_hires_log("生成已取消，临时文件和未完成输出已清理。")
            self._reset_hires_cancel_state()
            return
        self.hires_status_label.setText("完成")
        self._set_hires_status_color("#10B981")
        self._reset_hires_cancel_state()
        lines = "\n".join(str(path) for path in outputs) if isinstance(outputs, list) else str(outputs)
        QMessageBox.information(self, APP_TITLE, f"输出完成:\n{lines}")

    def _finish_hires_failure(self, message: str) -> None:
        was_cancelled = self._hires_cancel_requested
        self._hires_process = None
        self.hires_progress.setRange(0, 1)
        self.hires_progress.setValue(0)
        self.hires_start_button.setEnabled(True)
        self.hires_cancel_button.setEnabled(False)
        if was_cancelled:
            self._cleanup_incomplete_hires_outputs()
            self.hires_status_label.setText("生成已取消")
            self._set_hires_status_color("#475467")
            self._append_hires_log("生成已取消，临时文件和未完成输出已清理。")
            self._reset_hires_cancel_state()
            return
        self.hires_status_label.setText("失败")
        self._set_hires_status_color("#EF4444")
        self._reset_hires_cancel_state()
        self._append_hires_log(f"处理失败: {message}")
        QMessageBox.critical(self, APP_TITLE, message)

    def _clear_hires_inputs(self) -> None:
        if self.hires_task is not None and self.hires_task.isRunning():
            QMessageBox.information(self, APP_TITLE, "当前生成任务还在处理中，请稍等。")
            return
        self.video_zone.clear_path()
        self.on_vocal_zone.clear_path()
        self.off_vocal_zone.clear_path()
        self.output_dir_label.setText("跟随字幕视频所在目录")
        self.hires_status_label.setText("已清空已选文件")
        self._set_hires_status_color("#475467")

    def _open_hires_output_dir(self) -> None:
        video_path = self.video_zone.path
        if video_path is None:
            QMessageBox.information(self, APP_TITLE, "请先选择字幕视频。")
            return
        output_dir = resolve_output_dir(video_path)
        output_dir.mkdir(parents=True, exist_ok=True)
        open_in_explorer(output_dir)

    def _validate_alignment_inputs(self) -> tuple[Path, Path, Path | None]:
        video_path = self.align_video_zone.path
        audio_path = self.align_audio_zone.path
        ffmpeg_dir = self._resolve_ffmpeg_dir()
        if video_path is None or not video_path.is_file():
            raise ProcessingError("请先选择有效的字幕视频。")
        if audio_path is None or not audio_path.is_file():
            raise ProcessingError("请先选择有效的原唱音源。")
        return video_path, audio_path, ffmpeg_dir

    def _has_complete_alignment_inputs(self) -> bool:
        video_path = self.align_video_zone.path
        audio_path = self.align_audio_zone.path
        return (
            video_path is not None
            and audio_path is not None
            and video_path.is_file()
            and audio_path.is_file()
        )

    def _invalidate_alignment_waveforms(self) -> None:
        self._stop_alignment_preview(log_message=False)
        self.waveform_view.clear()
        self.align_status_label.setText("准备生成波形")
        if hasattr(self, "align_waveform_placeholder"):
            self.align_waveform_placeholder.show()
        if hasattr(self, "align_nudge_panel"):
            self.align_nudge_panel.hide()
        self._refresh_alignment_material_inputs()
        self._refresh_align_target_ui()
        self._refresh_alignment_preview_controls()

    def _start_alignment_analysis(self) -> None:
        if self.align_analysis_task is not None and self.align_analysis_task.isRunning():
            QMessageBox.information(self, APP_TITLE, "当前波形任务还在处理中，请稍等。")
            return
        try:
            video_path, audio_path, ffmpeg_dir = self._validate_alignment_inputs()
        except ProcessingError as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))
            return

        self.align_log.clear()
        self.align_analyze_button.setEnabled(False)
        self.align_export_button.setEnabled(False)
        self.align_auto_button.setEnabled(False)
        self.align_preview_button.setEnabled(False)
        self.align_progress.setRange(0, 0)
        self.align_status_label.setText("生成波形中…")

        def runner(logger: Callable[[str], None]) -> tuple[WaveformData, WaveformData]:
            video_waveform = extract_waveform(video_path, ffmpeg_dir, logger, label="字幕视频音轨")
            audio_waveform = extract_waveform(audio_path, ffmpeg_dir, logger, label="原唱音源")
            return video_waveform, audio_waveform

        task = self._track_background_task("align_analysis_task", BackgroundTask(runner))
        task.log_message.connect(self._append_align_log)
        task.task_succeeded.connect(self._finish_alignment_analysis_success)
        task.task_failed.connect(self._finish_alignment_analysis_failure)
        task.start()

    def _finish_alignment_analysis_success(self, payload: object) -> None:
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(1)
        self.align_analyze_button.setEnabled(True)
        if not isinstance(payload, tuple) or len(payload) != 2:
            self._finish_alignment_analysis_failure("波形结果无效。")
            return
        video_waveform, audio_waveform = payload
        self.waveform_view.set_waveforms(video_waveform=video_waveform, audio_waveform=audio_waveform)
        self.align_video_info_label.setText(f"字幕视频: {format_media_duration(video_waveform.duration)}")
        self.align_audio_info_label.setText(f"原唱音源: {format_media_duration(audio_waveform.duration)}")
        self.align_status_label.setText("波形已生成")
        self._sync_alignment_zoom_slider()
        self._refresh_alignment_material_inputs()
        self._refresh_align_target_ui()
        self._refresh_alignment_preview_controls()

    def _finish_alignment_analysis_failure(self, message: str) -> None:
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(0)
        self.align_analyze_button.setEnabled(True)
        self.align_status_label.setText("波形生成失败")
        self._append_align_log(f"波形生成失败: {message}")
        self._refresh_alignment_preview_controls()
        QMessageBox.critical(self, APP_TITLE, message)

    def _auto_align_waveforms(self) -> None:
        if self.align_auto_task is not None and self.align_auto_task.isRunning():
            QMessageBox.information(self, APP_TITLE, "当前自动对齐任务还在处理中，请稍等。")
            return
        if self.waveform_view.video_waveform is None or self.waveform_view.audio_waveform is None:
            QMessageBox.critical(self, APP_TITLE, "请先生成波形。")
            return

        video_start_seconds, audio_start_seconds = self.waveform_view.source_starts()
        target_track = ALIGN_TARGET_VIDEO if self._is_align_video_target() else ALIGN_TARGET_AUDIO
        self._stop_alignment_preview(log_message=False)
        self.align_analyze_button.setEnabled(False)
        self.align_auto_button.setEnabled(False)
        self.align_export_button.setEnabled(False)
        self.align_preview_button.setEnabled(False)
        self.align_progress.setRange(0, 0)
        self.align_status_label.setText("自动对齐中…")
        self._append_align_log(
            f"自动对齐分析从当前视图左边界开始: 视频 {video_start_seconds:.3f}s，音频 {audio_start_seconds:.3f}s"
        )

        def runner(_logger: Callable[[str], None]) -> AutoAlignResult:
            return estimate_waveform_alignment(
                self.waveform_view.video_waveform,
                self.waveform_view.audio_waveform,
                target_track=target_track,
                video_start_seconds=video_start_seconds,
                audio_start_seconds=audio_start_seconds,
            )

        task = self._track_background_task("align_auto_task", BackgroundTask(runner))
        task.task_succeeded.connect(self._finish_auto_align_success)
        task.task_failed.connect(self._finish_auto_align_failure)
        task.start()

    def _finish_auto_align_success(self, payload: object) -> None:
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(1)
        self.align_analyze_button.setEnabled(True)
        if not isinstance(payload, AutoAlignResult):
            self._finish_auto_align_failure("自动对齐结果无效。")
            return
        self.waveform_view.set_offset(payload.target_offset_seconds)
        self.waveform_view.set_playhead(max(0.0, payload.media_offset_seconds), keep_visible=True)
        confidence_percent = int(round(payload.confidence * 100))
        self.align_status_label.setText(f"自动对齐完成，置信度 {confidence_percent}%")
        target_label = "字幕视频" if self._is_align_video_target() else "原唱音源"
        self._append_align_log(
            f"自动对齐完成: 移动{target_label} {format_offset(payload.target_offset_seconds)}，"
            f"媒体相对偏移 {format_offset(payload.media_offset_seconds)}，置信度 {confidence_percent}%"
        )
        self._append_align_log(
            f"自动对齐评分: score={payload.score:.3f}, second={payload.second_score:.3f}, "
            f"overlap={payload.overlap_seconds:.2f}s, search=±{payload.search_seconds:.0f}s"
        )
        if payload.confidence < 0.55:
            self._append_align_log("自动对齐置信度偏低，建议先试听预览再确认。")
        self._refresh_alignment_preview_controls()

    def _finish_auto_align_failure(self, message: str) -> None:
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(0)
        self.align_analyze_button.setEnabled(True)
        self.align_status_label.setText("自动对齐失败")
        self._refresh_alignment_preview_controls()
        QMessageBox.critical(self, APP_TITLE, message)

    def _handle_align_target_changed(self) -> None:
        target_track = ALIGN_TARGET_VIDEO if self.align_target_video_radio.isChecked() else ALIGN_TARGET_AUDIO
        self._stop_alignment_preview(log_message=False)
        self.waveform_view.set_target_track(target_track)
        self._refresh_align_target_ui()

    def _refresh_align_target_ui(self) -> None:
        is_video_target = self._is_align_video_target()
        has_waveforms = self.waveform_view.video_waveform is not None and self.waveform_view.audio_waveform is not None
        if hasattr(self, "align_target_video_card"):
            self.align_target_video_card.sync_ui()
        if hasattr(self, "align_target_audio_card"):
            self.align_target_audio_card.sync_ui()
        self._handle_waveform_offset_changed(self.waveform_view.offset_seconds)
        self.align_drag_offset_radio.setText("移动字幕视频" if is_video_target else "移动原唱音源")
        self.align_export_button.setText("导出对齐视频" if is_video_target else "导出对齐音频")
        self.align_force_1080p60_check.setEnabled(has_waveforms and is_video_target)
        self.align_force_1080p60_card.setEnabled(has_waveforms and is_video_target)
        self.align_use_video_audio_check.setEnabled(has_waveforms and is_video_target)
        self.align_use_video_audio_card.setEnabled(has_waveforms and is_video_target)
        self._sync_align_tail_trim_controls()
        if has_waveforms and is_video_target:
            self.align_lead_row_widget.setEnabled(True)
            self.align_encode_row_widget.setEnabled(True)
            self.align_encode_software_card.setEnabled(True)
            self.align_encode_hardware_card.setEnabled(True)
            if self._align_lead_fill_selection == LEAD_FILL_WHITE:
                self.align_lead_fill_white_radio.setChecked(True)
            elif self._align_lead_fill_selection == LEAD_FILL_FREEZE:
                self.align_lead_fill_freeze_radio.setChecked(True)
            else:
                self.align_lead_fill_black_radio.setChecked(True)
            if self._align_encode_selection == ENCODE_MODE_HARDWARE:
                self.align_encode_hardware_radio.setChecked(True)
            else:
                self.align_encode_software_radio.setChecked(True)
            self._on_offset_finalized(self.waveform_view.offset_seconds)
        else:
            if self.align_lead_fill_white_radio.isChecked():
                self._align_lead_fill_selection = LEAD_FILL_WHITE
            elif self.align_lead_fill_freeze_radio.isChecked():
                self._align_lead_fill_selection = LEAD_FILL_FREEZE
            else:
                self._align_lead_fill_selection = LEAD_FILL_BLACK
            self.align_lead_fill_group.setExclusive(False)
            self.align_lead_trim_radio.setChecked(False)
            self.align_lead_fill_black_radio.setChecked(False)
            self.align_lead_fill_white_radio.setChecked(False)
            self.align_lead_fill_freeze_radio.setChecked(False)
            self.align_lead_fill_group.setExclusive(True)

            self.align_encode_group.setExclusive(False)
            self.align_encode_software_radio.setChecked(False)
            self.align_encode_hardware_radio.setChecked(False)
            self.align_encode_group.setExclusive(True)

            self.align_lead_row_widget.setEnabled(False)
            self.align_encode_row_widget.setEnabled(False)
            self.align_encode_software_card.setEnabled(False)
            self.align_encode_hardware_card.setEnabled(False)
            self.align_head_btn_crop.setEnabled(False)
            self.align_head_btn_black.setEnabled(False)
            self.align_head_btn_white.setEnabled(False)
            self.align_head_btn_freeze.setEnabled(False)
            self._update_head_mode_buttons(None)
        if self.align_control_panel is not None:
            self.align_control_panel.setEnabled(has_waveforms)
        self.waveform_view.setEnabled(has_waveforms)
        if hasattr(self, "align_control_placeholder"):
            self.align_control_placeholder.setVisible(not has_waveforms)
        if hasattr(self, "align_video_options_widget"):
            self.align_video_options_widget.setVisible(has_waveforms and is_video_target)
        if hasattr(self, "align_audio_offset_widget"):
            self.align_audio_offset_widget.setVisible(has_waveforms and not is_video_target)
        if hasattr(self, "align_waveform_placeholder"):
            self.align_waveform_placeholder.setVisible(not has_waveforms)
        if hasattr(self, "align_nudge_panel"):
            self.align_nudge_panel.setVisible(has_waveforms)
        if hasattr(self, "align_drag_mode_button"):
            self.align_drag_mode_button.setEnabled(has_waveforms)
        if has_waveforms:
            self.align_status_label.setText(self.align_status_label.text())
        self._refresh_align_trim_status(self.waveform_view.trim_end_seconds)
        self._apply_alignment_mode_styles()

    def _handle_waveform_offset_changed(self, seconds: float) -> None:
        self.align_offset_label.setText(format_offset(seconds))
        self._refresh_align_trim_status(self.waveform_view.trim_end_seconds)
        self._refresh_alignment_export_panels()
        self._apply_alignment_mode_styles()

    def _handle_playhead_changed(self, seconds: float) -> None:
        if (
            not self._suppress_preview_seek_restart
            and self.align_preview_process is not None
            and self.align_preview_process.is_running()
        ):
            self._restart_alignment_preview_from_playhead()

    def _restart_alignment_preview_from_playhead(self) -> None:
        self._start_alignment_preview()

    def _toggle_alignment_preview(self) -> None:
        if self.align_preview_process is not None and self.align_preview_process.is_running():
            self._stop_alignment_preview()
        else:
            self._start_alignment_preview()

    def _refresh_align_trim_status(self, trim_seconds: object) -> None:
        if not self._is_align_video_target():
            self.align_trim_label.setText("仅在导出字幕视频时生效")
            return

        manual_trim = trim_seconds if isinstance(trim_seconds, float) else self.waveform_view.trim_end_seconds
        parts: list[str] = []
        if manual_trim is not None:
            parts.append(f"手动尾裁到 {manual_trim:.3f}s")
        if self.align_trim_to_audio_radio.isChecked():
            auto_trim = self._compute_video_trim_duration()
            if auto_trim is not None and self.waveform_view.audio_waveform is not None:
                parts.append(f"自动最多保留到音频末尾 {self.waveform_view.audio_waveform.duration:.3f}s")
            else:
                parts.append("自动尾裁已开启")
        self.align_trim_label.setText("；".join(parts) if parts else "未设置")
        self._sync_align_tail_trim_controls()

    def _sync_align_tail_trim_controls(self) -> None:
        if not hasattr(self, "align_trim_mark_button"):
            return
        has_waveforms = self.waveform_view.video_waveform is not None and self.waveform_view.audio_waveform is not None
        is_video_target = self._is_align_video_target()
        base_enabled = has_waveforms and is_video_target
        has_manual_trim = self.waveform_view.trim_end_seconds is not None
        auto_trim_enabled = self.align_trim_to_audio_radio.isChecked()
        self.align_trim_none_radio.setEnabled(base_enabled)
        self.align_trim_to_audio_radio.setEnabled(base_enabled and not has_manual_trim)
        self.align_trim_mark_button.setEnabled(base_enabled and not auto_trim_enabled)
        self.align_trim_clear_button.setEnabled(base_enabled and not auto_trim_enabled and has_manual_trim)

    def _compute_video_trim_duration(self) -> float | None:
        if not self._is_align_video_target():
            return None
        if self.waveform_view.video_waveform is None:
            return None
        base_duration = max(0.0, self.waveform_view.video_waveform.duration + self.waveform_view.offset_seconds)
        if base_duration <= 0:
            return None
        candidates = [base_duration]
        if self.waveform_view.trim_end_seconds is not None:
            candidates.append(self.waveform_view.trim_end_seconds)
        if self.align_trim_to_audio_radio.isChecked() and self.waveform_view.audio_waveform is not None:
            candidates.append(self.waveform_view.audio_waveform.duration)
        trim_duration = min(candidates)
        if trim_duration < base_duration - 0.001:
            return max(0.001, trim_duration)
        return None

    def _refresh_alignment_preview_controls(self) -> None:
        has_inputs = self._has_complete_alignment_inputs()
        has_any_inputs = self.align_video_zone.path is not None or self.align_audio_zone.path is not None
        has_waveforms = self.waveform_view.video_waveform is not None and self.waveform_view.audio_waveform is not None
        is_playing = self.align_preview_process is not None and self.align_preview_process.is_running()
        is_exporting = self._is_align_export_running()
        is_busy = (
            (self.align_analysis_task is not None and self.align_analysis_task.isRunning())
            or (self.align_auto_task is not None and self.align_auto_task.isRunning())
            or is_exporting
        )
        self.align_analyze_button.setEnabled(has_inputs and not is_playing and not is_busy)
        self.align_auto_button.setEnabled(has_waveforms and not is_playing and not is_busy)
        self.align_preview_button.setEnabled(is_playing or (has_waveforms and not is_busy))
        if is_playing:
            self.align_preview_button.setText("停止")
            self.align_preview_button.setIcon(FIF.PAUSE.icon())
            self.align_preview_button.setToolTip("停止 (空格)")
        else:
            self.align_preview_button.setText("播放")
            self.align_preview_button.setIcon(FIF.PLAY.icon())
            self.align_preview_button.setToolTip("播放 (空格)")
        self.align_export_button.setEnabled(has_waveforms and not is_playing and not is_busy)
        self.align_stop_export_button.setEnabled(is_exporting)
        if self.align_open_output_button is not None:
            self.align_open_output_button.setEnabled(has_waveforms)
        if self.align_clear_button is not None:
            self.align_clear_button.setEnabled(has_any_inputs and not is_busy)
        if self.align_jump_to_end_button is not None:
            self.align_jump_to_end_button.setEnabled(has_waveforms)
        if self.align_reset_view_button is not None:
            self.align_reset_view_button.setEnabled(has_waveforms)
        self.align_zoom_slider.setEnabled(has_waveforms)
        if hasattr(self, "align_volume_slider"):
            self.align_volume_slider.setEnabled(has_waveforms)
        if not has_inputs and not is_busy and not is_playing:
            if self.align_video_zone.path is None and self.align_audio_zone.path is not None:
                self.align_status_label.setText("还需导入字幕视频后即可生成波形")
            elif self.align_video_zone.path is not None and self.align_audio_zone.path is None:
                self.align_status_label.setText("还需导入原唱音频后即可生成波形")
            elif self.waveform_view.video_waveform is None and self.waveform_view.audio_waveform is None:
                self.align_status_label.setText("准备生成波形")
        self._sync_alignment_export_buttons()

    def _queue_alignment_preview_volume_refresh(self, _value: int) -> None:
        if hasattr(self, "_align_volume_refresh_timer"):
            self._align_volume_refresh_timer.start()

    def _apply_alignment_preview_volume(self) -> None:
        if self.align_preview_process is not None and self.align_preview_process.is_running():
            self._restart_alignment_preview_from_playhead()

    def _is_align_export_running(self) -> bool:
        return self.align_export_task is not None and self.align_export_task.isRunning()

    def _register_align_export_process(self, process: subprocess.Popen | None) -> None:
        self._align_export_process = process

    def _cleanup_incomplete_align_exports(self) -> None:
        completed = set(self._align_export_completed_outputs)
        for path in self._align_export_expected_outputs:
            if path in completed or not path.exists():
                continue
            try:
                path.unlink()
                self._append_align_log(f"已清理未完成的输出文件: {path}")
            except OSError as exc:
                self._append_align_log(f"清理未完成的输出文件失败: {path} ({exc})")

    def _reset_align_export_state(self) -> None:
        self._align_export_cancel_requested = False
        self._align_export_process = None
        self._align_export_expected_outputs = []
        self._align_export_completed_outputs = []

    def _stop_alignment_export(self) -> None:
        if not self._is_align_export_running():
            return
        if not self._align_export_cancel_requested:
            self._align_export_cancel_requested = True
            self.align_status_label.setText("正在停止导出…")
            self._append_align_log("正在停止导出…")
        process = self._align_export_process
        if process is not None:
            terminate_process(process)

    def _is_align_video_target(self) -> bool:
        return self.align_target_video_radio.isChecked()

    def _start_alignment_preview(self) -> None:
        if self.waveform_view.video_waveform is None or self.waveform_view.audio_waveform is None:
            QMessageBox.critical(self, APP_TITLE, "请先生成波形并完成对齐。")
            return
        try:
            video_path, audio_path, ffmpeg_dir = self._validate_alignment_inputs()
        except ProcessingError as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))
            return

        self._stop_alignment_preview(log_message=False)
        target_track = ALIGN_TARGET_VIDEO if self._is_align_video_target() else ALIGN_TARGET_AUDIO
        preview_start_seconds = self.waveform_view.playhead_seconds
        volume_percent = self.align_volume_slider.value() if hasattr(self, "align_volume_slider") else 50
        try:
            ffmpeg_path = find_tool("ffmpeg.exe", ffmpeg_dir)
            ffplay_path = find_tool("ffplay.exe", ffmpeg_dir)
            ffmpeg_command = build_alignment_preview_command(
                ffmpeg_path=ffmpeg_path,
                video_path=video_path,
                audio_path=audio_path,
                offset_seconds=self.waveform_view.offset_seconds,
                target_track=target_track,
                preview_start_seconds=preview_start_seconds,
            )
            ffplay_command = [
                ffplay_path,
                "-hide_banner",
                "-loglevel",
                "error",
                "-nodisp",
                "-autoexit",
                "-volume",
                str(max(0, min(100, int(volume_percent)))),
                "-i",
                "pipe:0",
            ]
            ffmpeg_process = subprocess.Popen(
                ffmpeg_command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **_build_subprocess_kwargs(),
            )
            assert ffmpeg_process.stdout is not None
            try:
                ffplay_process = subprocess.Popen(
                    ffplay_command,
                    stdin=ffmpeg_process.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_build_subprocess_kwargs(),
                )
            except Exception:
                ffmpeg_process.terminate()
                raise
            finally:
                ffmpeg_process.stdout.close()
            self._append_align_log(f"预览音量: {int(volume_percent)}%")
            self.align_preview_process = AlignmentPreviewProcess(
                ffmpeg_process=ffmpeg_process,
                ffplay_process=ffplay_process,
            )
        except Exception as exc:  # noqa: BLE001
            self.align_preview_process = None
            self._append_align_log(f"播放预览失败: {exc}")
            QMessageBox.critical(self, APP_TITLE, f"播放预览失败:\n{exc}")
            self._refresh_alignment_preview_controls()
            return

        self.align_preview_started_at = time.monotonic()
        self.align_preview_start_seconds = preview_start_seconds
        self.align_status_label.setText("正在播放预览")
        self.preview_timer.start()
        self._refresh_alignment_preview_controls()

    def _stop_alignment_preview(self, *, log_message: bool = True) -> None:
        process = self.align_preview_process
        if process is not None:
            process.stop()
            self.align_preview_process = None
            if log_message:
                self._append_align_log("播放预览已停止")
        self.preview_timer.stop()
        self.align_preview_started_at = 0.0
        self.align_preview_start_seconds = 0.0
        self._refresh_alignment_preview_controls()

    def _poll_alignment_preview(self) -> None:
        process = self.align_preview_process
        if process is None:
            self.preview_timer.stop()
            self._refresh_alignment_preview_controls()
            return
        if process.is_running():
            elapsed = time.monotonic() - self.align_preview_started_at
            self._suppress_preview_seek_restart = True
            try:
                self.waveform_view.set_playhead(self.align_preview_start_seconds + elapsed, keep_visible=True)
            finally:
                self._suppress_preview_seek_restart = False
            return
        self.preview_timer.stop()
        self.align_preview_process = None
        self.align_preview_started_at = 0.0
        self.align_preview_start_seconds = 0.0
        self.align_status_label.setText("预览播放结束")
        self._append_align_log("播放预览结束")
        self._refresh_alignment_preview_controls()

    def _render_alignment_output_path(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        is_video_target: bool,
    ) -> Path:
        video_template, audio_template = self._resolve_alignment_name_templates(require_valid=True)
        template = video_template if is_video_target else audio_template
        extension = ".mp4" if is_video_target else ".wav"
        try:
            stem = template.format(video_name=video_path.stem, audio_name=audio_path.stem).strip()
        except Exception as exc:  # noqa: BLE001
            label = "对齐后视频" if is_video_target else "对齐后音频"
            raise ProcessingError(f"{label}模板无法生成文件名: {exc}") from exc

        stem = stem.rstrip(". ")
        if not stem:
            raise ProcessingError("导出文件名不能为空。")
        invalid_chars = sorted({char for char in stem if char in WINDOWS_INVALID_FILENAME_CHARS})
        if invalid_chars:
            raise ProcessingError(f"文件名包含非法字符: {' '.join(invalid_chars)}")
        source_path = video_path if is_video_target else audio_path
        return source_path.with_name(f"{stem}{extension}")

    def _start_aligned_export(self) -> None:
        if self.align_export_task is not None and self.align_export_task.isRunning():
            QMessageBox.information(self, APP_TITLE, "当前导出任务还在处理中，请稍等。")
            return
        if self.waveform_view.video_waveform is None or self.waveform_view.audio_waveform is None:
            QMessageBox.critical(self, APP_TITLE, "请先生成波形并完成对齐。")
            return
        try:
            video_path, audio_path, ffmpeg_dir = self._validate_alignment_inputs()
            is_video_target = self._is_align_video_target()
            initial_path = self._render_alignment_output_path(
                video_path=video_path,
                audio_path=audio_path,
                is_video_target=is_video_target,
            )
            output_kind = "对齐视频" if is_video_target else "对齐音频"
            if is_video_target:
                output_path_text, _ = QFileDialog.getSaveFileName(
                    self,
                    "导出对齐视频",
                    str(initial_path),
                    "MP4 视频 (*.mp4);;Matroska 视频 (*.mkv);;所有文件 (*.*)",
                )
            else:
                output_path_text, _ = QFileDialog.getSaveFileName(
                    self,
                    "导出对齐音频",
                    str(initial_path),
                    "WAV 音频 (*.wav);;所有文件 (*.*)",
                )
        except ProcessingError as exc:
            QMessageBox.critical(self, APP_TITLE, str(exc))
            return

        if not output_path_text:
            return
        output_path = Path(output_path_text).expanduser()
        offset_seconds = self.waveform_view.offset_seconds
        encode_mode = self._current_alignment_encode_mode()
        if self.align_lead_fill_white_radio.isChecked():
            lead_fill_color = LEAD_FILL_WHITE
        elif self.align_lead_fill_freeze_radio.isChecked():
            lead_fill_color = LEAD_FILL_FREEZE
        else:
            lead_fill_color = LEAD_FILL_BLACK
        force_1080p60 = self.align_force_1080p60_check.isChecked()
        use_source_video_audio = self.align_use_video_audio_check.isChecked() if is_video_target else False
        video_trim_duration = self._compute_video_trim_duration() if is_video_target else None
        self._align_export_cancel_requested = False
        self._align_export_process = None
        self._align_export_expected_outputs = [output_path]
        self._align_export_completed_outputs = []

        self._stop_alignment_preview(log_message=False)
        self.align_analyze_button.setEnabled(False)
        self.align_auto_button.setEnabled(False)
        self.align_export_button.setEnabled(False)
        self.align_preview_button.setEnabled(False)
        self.align_progress.setRange(0, 0)
        self.align_status_label.setText("导出中…")

        def runner(logger: Callable[[str], None]) -> list[Path]:
            outputs: list[Path] = []
            if is_video_target:
                outputs.append(
                    export_aligned_video(
                        video_path=video_path,
                        audio_path=audio_path,
                        output_path=output_path,
                        offset_seconds=offset_seconds,
                        ffmpeg_dir=ffmpeg_dir,
                        logger=logger,
                        should_cancel=lambda: self._align_export_cancel_requested,
                        on_process_started=self._register_align_export_process,
                        encode_mode=encode_mode,
                        lead_fill_color=lead_fill_color,
                        force_1080p60=force_1080p60,
                        output_duration_seconds=video_trim_duration,
                        use_source_video_audio=use_source_video_audio,
                    )
                )
                self._align_export_completed_outputs.append(outputs[-1])
            else:
                outputs.append(
                    export_aligned_audio(
                        audio_path=audio_path,
                        output_path=output_path,
                        offset_seconds=offset_seconds,
                        ffmpeg_dir=ffmpeg_dir,
                        logger=logger,
                        should_cancel=lambda: self._align_export_cancel_requested,
                        on_process_started=self._register_align_export_process,
                    )
                )
                self._align_export_completed_outputs.append(outputs[-1])
            return outputs

        task = self._track_background_task("align_export_task", BackgroundTask(runner))
        task.log_message.connect(self._append_align_log)
        task.task_succeeded.connect(lambda outputs: self._finish_aligned_export(True, "", outputs, output_kind))
        task.task_failed.connect(lambda message: self._finish_aligned_export(False, message, None, output_kind))
        task.start()
        self._refresh_alignment_preview_controls()

    def _finish_aligned_export(
        self,
        success: bool,
        message: str,
        output_paths: object,
        output_kind: str,
    ) -> None:
        was_cancelled = self._align_export_cancel_requested
        self._align_export_process = None
        self.align_progress.setRange(0, 1)
        self.align_progress.setValue(1 if success and not was_cancelled else 0)
        self.align_analyze_button.setEnabled(True)
        if was_cancelled:
            self._cleanup_incomplete_align_exports()
            self.align_status_label.setText("导出已停止")
            self._refresh_alignment_preview_controls()
            self._reset_align_export_state()
            self._append_align_log("导出任务已终止，未完成的输出文件已清理。")
            return
        self.align_status_label.setText("导出完成" if success else "导出失败")
        self._refresh_alignment_preview_controls()
        self._reset_align_export_state()
        if success and isinstance(output_paths, list):
            QMessageBox.information(self, APP_TITLE, f"{output_kind}已导出:\n" + "\n".join(str(path) for path in output_paths))
            return
        self._append_align_log(f"导出失败: {message}")
        QMessageBox.critical(self, APP_TITLE, message)

    def _clear_alignment_inputs(self) -> None:
        if (
            (self.align_analysis_task is not None and self.align_analysis_task.isRunning())
            or (self.align_auto_task is not None and self.align_auto_task.isRunning())
            or (self.align_export_task is not None and self.align_export_task.isRunning())
        ):
            QMessageBox.information(self, APP_TITLE, "当前对齐任务还在处理中，请稍等。")
            return
        self._stop_alignment_preview(log_message=False)
        self.align_video_zone.clear_path()
        self.align_audio_zone.clear_path()
        self.align_log.clear()
        self.waveform_view.clear()
        self._refresh_media_info_labels()
        self._refresh_align_target_ui()
        self._refresh_alignment_preview_controls()
        self.align_status_label.setText("准备生成波形")

    def _open_align_output_dir(self) -> None:
        source_path = self.align_audio_zone.path or self.align_video_zone.path
        if source_path is None:
            QMessageBox.information(self, APP_TITLE, "请先选择文件。")
            return
        output_dir = source_path.parent
        output_dir.mkdir(parents=True, exist_ok=True)
        open_in_explorer(output_dir)

    def closeEvent(self, event) -> None:  # noqa: N802
        if self._running_background_tasks():
            QMessageBox.information(self, APP_TITLE, "当前后台任务仍在运行，请等待完成后再关闭窗口。")
            event.ignore()
            return
        self._stop_alignment_preview(log_message=False)
        try:
            self._save_all_settings()
        except Exception:
            pass
        super().closeEvent(event)


def launch_qt_app() -> int:
    set_explicit_app_user_model_id("KaraokeHelper.Desktop")
    sync_fluent_ui_fonts()
    app = QApplication.instance() or QApplication([])
    app.setFont(build_app_ui_font())
    app_icon = load_taskbar_icon()
    if app_icon is not None:
        app.setWindowIcon(app_icon)
    window = KrokHelperQtApp()
    window.show()
    return app.exec()
