"""更新提示弹窗。

* :class:`UpdateAvailableDialog` —— 检测到新版本时展示版本号、发布说明与"立即更新 /
  稍后 / 跳过此版本"按钮。
* :class:`UpdateCheckErrorDialog` —— 手动检查时如失败，给用户一个明确反馈。

依赖 qfluentwidgets 的 ``MessageBoxBase`` 风格，与项目其他对话框保持一致。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QDesktopServices, QFont
from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    HyperlinkButton,
    MessageBoxBase,
    PushButton,
    SubtitleLabel,
    TextEdit,
    TitleLabel,
)

from ..manifest import LatestRelease


class UpdateAvailableDialog(MessageBoxBase):
    """有新版本可用时弹出。

    返回值约定（通过 :attr:`user_choice`）：

    * ``"update"``  —— 用户点击立即更新（外层应启动 Updater.exe）
    * ``"later"``   —— 用户选择稍后
    * ``"skip"``    —— 用户选择跳过此版本（外层应将版本号写入 ``skipped_version``）

    布局参考 March7thAssistant 的更新弹窗：标题 + 副标题（版本号 + 发布时间）+
    可滚动 changelog + 操作按钮。
    """

    def __init__(
        self,
        release: LatestRelease,
        local_version: str,
        primary_source_label: str = "",
        all_releases: Optional[List[LatestRelease]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.user_choice: str = "later"
        self._release = release
        self._build_ui(release, local_version, primary_source_label, all_releases or [])

    def _build_ui(
        self,
        release: LatestRelease,
        local_version: str,
        source_label: str,
        all_releases: List[LatestRelease],
    ):
        # 主体内容容器（MessageBoxBase 的 viewLayout 是 QVBoxLayout）
        title = TitleLabel("发现新版本", self)
        self.viewLayout.addWidget(title)

        sub = SubtitleLabel(f"v{release.version}", self)
        self.viewLayout.addWidget(sub)

        meta_line = BodyLabel(
            f"当前版本 v{local_version}　|　"
            f"发布于 {release.published_at[:10] or '未知日期'}",
            self,
        )
        meta_line.setStyleSheet("color: #888;")
        self.viewLayout.addWidget(meta_line)

        if source_label:
            src_line = BodyLabel(f"下载源：{source_label}", self)
            src_line.setStyleSheet("color: #888;")
            self.viewLayout.addWidget(src_line)

        # changelog —— 使用 Qt 原生 ``setMarkdown`` 渲染 GitHub Release body 的 Markdown。
        # 该 API 在 Qt 5.14+ 可用，PyQt6 全面支持；覆盖 #/##、列表、链接、行内代码、
        # 代码块等绝大多数 GFM 语法，零额外依赖。
        # 跨版本更新时（all_releases 包含多个版本）拼合所有中间版本的 changelog，
        # 每段以版本号和发布日期作为二级标题，让用户看到完整的变更历史。
        changelog_label = BodyLabel("更新内容：", self)
        self.viewLayout.addWidget(changelog_label)

        body_view = TextEdit(self)
        body_view.setReadOnly(True)
        body_view.setMinimumHeight(260)
        body_view.setMinimumWidth(560)
        body_view.setFont(QFont("Microsoft YaHei", 10))

        if len(all_releases) > 1:
            sections: List[str] = []
            for rel in all_releases:
                date = rel.published_at[:10] if rel.published_at else "未知日期"
                header = f"## v{rel.version}（{date}）"
                body = rel.body.strip()
                sections.append(f"{header}\n\n{body}" if body else header)
            body_text = "\n\n---\n\n".join(sections)
        else:
            body_text = release.body.strip()

        if body_text:
            try:
                body_view.setMarkdown(body_text)
            except Exception:
                body_view.setPlainText(body_text)
        else:
            body_view.setPlainText("（发布说明为空）")
        self.viewLayout.addWidget(body_view)

        # 链接到 release 页面
        if release.html_url:
            link = HyperlinkButton(release.html_url, "在浏览器中查看完整发布说明", self)
            link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.html_url)))
            self.viewLayout.addWidget(link)

        # 替换 MessageBoxBase 默认的 yes/cancel 按钮
        self.yesButton.setText("立即更新")
        self.cancelButton.setText("稍后再说")
        # 增加"跳过此版本"按钮
        self.skip_btn = PushButton("跳过此版本", self.buttonGroup)
        self.skip_btn.clicked.connect(self._on_skip_clicked)
        # 把跳过按钮加到 buttonLayout 最左侧，作为"次要"操作
        self.buttonLayout.insertWidget(0, self.skip_btn)

        # 拦截 yes/cancel，记录用户选择
        self.yesButton.clicked.connect(self._on_update_clicked)
        self.cancelButton.clicked.connect(self._on_later_clicked)

        self.setMinimumWidth(560)

    # ── 槽 ──

    def _on_update_clicked(self) -> None:
        self.user_choice = "update"

    def _on_later_clicked(self) -> None:
        self.user_choice = "later"

    def _on_skip_clicked(self) -> None:
        self.user_choice = "skip"
        # 视作"接受"关闭弹窗
        self.accept()


class UpdateCheckErrorDialog(MessageBoxBase):
    """检查更新失败时（仅在用户主动触发的入口）展示错误详情。"""

    def __init__(
        self,
        message: str,
        attempts: Optional[List[Tuple[str, str, str]]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._build_ui(message, attempts or [])

    def _build_ui(self, message: str, attempts: List[Tuple[str, str, str]]):
        title = TitleLabel("检查更新失败", self)
        self.viewLayout.addWidget(title)

        msg_label = BodyLabel(message, self)
        msg_label.setWordWrap(True)
        self.viewLayout.addWidget(msg_label)

        if attempts:
            sub = BodyLabel("源尝试记录：", self)
            self.viewLayout.addWidget(sub)
            detail = TextEdit(self)
            detail.setReadOnly(True)
            detail.setMinimumHeight(140)
            detail.setMinimumWidth(420)
            lines = []
            for source_id, url, err in attempts:
                tag = "OK" if not err else "FAIL"
                lines.append(f"[{tag}] {source_id} - {url}")
                if err:
                    lines.append(f"    {err}")
            detail.setPlainText("\n".join(lines))
            self.viewLayout.addWidget(detail)

        self.yesButton.setText("我知道了")
        self.cancelButton.hide()
        self.setMinimumWidth(480)
