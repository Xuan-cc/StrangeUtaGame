"""分色标签设置助手对话框。

按演唱者逐行配置 @Emoji 标签参数，应用后将结果写入
AppSettings["nicokara_tags"]["custom"]，并记忆首行参数供下次预填。

@Emoji 格式（SHINTA NicokaraMaker3 规格）：
  @Emoji=<触发字符>,<前画像>,<后画像（可空）>,<选项...>

典型用途（无图标分色，仅用透明 1x1 占位）：
  @Emoji=【演唱者名】,透明画像1x1.png,,Zoom=1,NoDecor,MarginRight=-170
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# AppSettings 延迟导入（避免循环 / Qt 未初始化时失败），
# 同时在模块级声明以便 unittest.mock.patch 可以 patch。
try:
    from strange_uta_game.frontend.settings.settings_interface import AppSettings
except Exception:  # pragma: no cover
    AppSettings = None  # type: ignore[assignment,misc]

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    SubtitleLabel,
)

# 匹配 @Emoji 或 @EmojiN= 开头的行（大小写不敏感）
_EMOJI_TAG_RE = re.compile(r"^@Emoji\d*=", re.IGNORECASE)

DEFAULT_PARAMS = "透明画像1x1.png,,Zoom=1,NoDecor,MarginRight=-170"


def strip_emoji_tags(custom: List[str]) -> List[str]:
    """从 custom 列表中删除所有 @Emoji 行，返回新列表。"""
    return [line for line in custom if not _EMOJI_TAG_RE.match(line.strip())]


def build_emoji_tag(trigger: str, params: str) -> str:
    """构建一行 @Emoji 标签字符串。

    Args:
        trigger: 触发字符，如 「【太郎】」
        params:  逗号分隔的参数部分，如 「透明画像1x1.png,,NoDecor,MarginRight=-170」

    Returns:
        完整的标签行，如 "@Emoji=【太郎】,透明画像1x1.png,,NoDecor,MarginRight=-170"
    """
    return f"@Emoji={trigger},{params}"


def apply_emoji_tags_to_settings(
    singer_params: List[Tuple[str, str, str]],
) -> None:
    """将 @Emoji 配置写入 AppSettings["nicokara_tags"]["custom"]，
    并记忆首行参数到 AppSettings["nicokara_emoji_default"]。

    旧的 @Emoji 行会被全部删除，再写入新的行。
    其他 custom 行（非 @Emoji）保持不变。

    Args:
        singer_params: [(singer_name, trigger, params), ...]
            singer_name: 演唱者显示名（仅用于注释，不写入文件）
            trigger:     触发字符，如 "【太郎】"
            params:      @Emoji= 后第二个字段起的内容，如 "img.png,,NoDecor,MarginRight=-170"
    """
    if AppSettings is None:
        return  # pragma: no cover
    settings = AppSettings()
    tags: dict = settings.get("nicokara_tags") or {}
    old_custom: List[str] = tags.get("custom", [])

    # 清除旧 @Emoji 行，保留其余 custom
    new_custom = strip_emoji_tags(old_custom)

    # 追加新 @Emoji 行
    for _, trigger, params in singer_params:
        if trigger.strip():
            new_custom.append(build_emoji_tag(trigger.strip(), params.strip()))

    tags["custom"] = new_custom
    settings.set("nicokara_tags", tags)

    # 记忆首行参数（不含触发字符，以便跨项目复用图像/选项）
    if singer_params:
        first_params = singer_params[0][2].strip()
        settings.set("nicokara_emoji_default", first_params)

    settings.save()


class EmojiTagDialog(QDialog):
    """分色标签设置助手弹窗

    按演唱者逐行配置 @Emoji 标签。每行包含：
    - 触发字符（默认 =【演唱者名】，可修改）
    - 参数（默认为首行记忆值，否则为 DEFAULT_PARAMS）

    「确定」后调用 apply_emoji_tags_to_settings 写入配置。
    """

    def __init__(
        self,
        singers: List[Tuple[str, str]],  # [(singer_id, singer_name), ...]
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("分色标签设置助手")
        self.setMinimumWidth(620)
        self.setMinimumHeight(300)

        self._singers = singers  # 当前需要配置的演唱者列表
        # [(singer_name, trigger_edit, params_edit), ...]
        self._rows: List[Tuple[str, LineEdit, LineEdit]] = []

        self._init_ui()
        self._populate_rows()

    def _init_ui(self):
        screen = self.parent().screen() if self.parent() else QApplication.primaryScreen()
        if screen:
            self.setMaximumHeight(int(screen.availableGeometry().height() * 0.85))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 16)
        layout.setSpacing(12)

        title = SubtitleLabel("分色标签设置助手")
        layout.addWidget(title)

        desc = CaptionLabel(
            "为每位演唱者配置 @Emoji 标签。触发字符与正文中的【演唱者名】对应，\n"
            "参数部分格式：前画像,后画像（可空）,选项... （逗号分隔）"
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        # 表头
        header = QHBoxLayout()
        lbl_singer = QLabel("演唱者")
        lbl_singer.setFixedWidth(90)
        lbl_singer.setFont(QFont("Microsoft YaHei", 9))
        lbl_trigger = QLabel("触发字符")
        lbl_trigger.setFixedWidth(130)
        lbl_trigger.setFont(QFont("Microsoft YaHei", 9))
        lbl_params = QLabel("参数（前画像, 后画像, 选项...）")
        lbl_params.setFont(QFont("Microsoft YaHei", 9))
        header.addWidget(lbl_singer)
        header.addWidget(lbl_trigger)
        header.addWidget(lbl_params)
        layout.addLayout(header)

        # 滚动区容纳行列表
        self._row_widget = QWidget()
        self._row_layout = QVBoxLayout(self._row_widget)
        self._row_layout.setContentsMargins(0, 0, 0, 0)
        self._row_layout.setSpacing(6)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(self._row_widget)
        layout.addWidget(scroll, stretch=1)

        layout.addStretch()

        # 确定/取消
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_ok = PrimaryPushButton("应用并保存", self)
        btn_ok.clicked.connect(self._on_accept)
        btn_copy = PushButton("将首行参数复制到其他行", self)
        btn_copy.clicked.connect(self._copy_first_row_params)
        btn_cancel = PushButton("取消", self)
        btn_cancel.clicked.connect(self.reject)
        btn_row.addWidget(btn_ok)
        btn_row.addWidget(btn_copy)
        btn_row.addWidget(btn_cancel)
        layout.addLayout(btn_row)

    def _get_default_params(self) -> str:
        """读取上次记忆的参数，无则用内置默认值。"""
        if AppSettings is None:
            return DEFAULT_PARAMS  # pragma: no cover
        try:
            saved = AppSettings().get("nicokara_emoji_default", "")
            return saved if saved else DEFAULT_PARAMS
        except Exception:
            return DEFAULT_PARAMS

    def _populate_rows(self):
        """根据 self._singers 填充每行配置控件。"""
        default_params = self._get_default_params()

        for _singer_id, singer_name in self._singers:
            row_layout = QHBoxLayout()
            row_layout.setSpacing(8)

            name_lbl = BodyLabel(singer_name)
            name_lbl.setFixedWidth(90)
            row_layout.addWidget(name_lbl)

            trigger_edit = LineEdit()
            trigger_edit.setFixedWidth(130)
            trigger_edit.setFont(QFont("Microsoft YaHei", 10))
            trigger_edit.setText(f"【{singer_name}】")
            trigger_edit.setPlaceholderText("触发字符")
            row_layout.addWidget(trigger_edit)

            params_edit = LineEdit()
            params_edit.setFont(QFont("Microsoft YaHei", 10))
            params_edit.setText(default_params)
            params_edit.setPlaceholderText("前画像,后画像（可空）,选项...")
            row_layout.addWidget(params_edit)

            self._row_layout.addLayout(row_layout)
            self._rows.append((singer_name, trigger_edit, params_edit))

        self._row_layout.addStretch()

    def get_singer_params(self) -> List[Tuple[str, str, str]]:
        """返回 [(singer_name, trigger, params), ...] 列表。"""
        result = []
        for singer_name, trigger_edit, params_edit in self._rows:
            trigger = trigger_edit.text().strip()
            params = params_edit.text().strip()
            result.append((singer_name, trigger, params))
        return result

    def _copy_first_row_params(self):
        if not self._rows:
            return
        first_params = self._rows[0][2].text()
        for _, _, params_edit in self._rows[1:]:
            params_edit.setText(first_params)

    def _on_accept(self):
        singer_params = self.get_singer_params()
        apply_emoji_tags_to_settings(singer_params)
        self.accept()
