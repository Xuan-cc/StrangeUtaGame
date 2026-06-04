"""设置界面 "网络与代理" 分组。

通过 :func:`attach_proxy_group` 注入到现有 ``SettingsInterface``，保持
settings_interface.py 的改动最小。

布局：
* 代理模式（关闭 / 系统代理 / 自动检测 / 手动指定）
* 手动地址（仅在 manual 模式可编辑）
* 当前生效代理 + 自动检测按钮
* 测试连通性按钮（用 ``https://api.github.com`` 探测）
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QWidget
from qfluentwidgets import (
    BodyLabel,
    ComboBox,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    LineEdit,
    PushButton,
    SettingCard,
    SettingCardGroup,
)

from .. import http_client
from ..proxy import (
    COMMON_PROXY_PORTS,
    detect_proxy_auto,
    read_system_proxy,
    resolve_proxy,
)
from ..settings import UpdaterSettings, ensure_persisted

if TYPE_CHECKING:
    from ...frontend.settings.settings_interface import SettingsInterface


# Mode 与下拉框 index 的双向映射
_MODE_TO_INDEX = {"off": 0, "system": 1, "auto": 2, "manual": 3}
_INDEX_TO_MODE = {v: k for k, v in _MODE_TO_INDEX.items()}
_MODE_LABELS = ["关闭代理", "使用系统代理", "自动检测代理", "手动指定地址"]


class _ProxyModeCard(SettingCard):
    """代理模式选择卡片（继承官方 SettingCard 风格）。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.GLOBE,
            "代理模式",
            "访问 GitHub 时是否经过代理；自动检测会探测常用本地代理端口",
            parent,
        )
        self.combo = ComboBox(self)
        self.combo.addItems(_MODE_LABELS)
        self.combo.setFixedWidth(180)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


class _ProxyManualCard(SettingCard):
    """手动代理地址输入卡片。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.LINK,
            "手动代理地址",
            "例如 http://127.0.0.1:7890 ；仅在选择「手动指定地址」时生效",
            parent,
        )
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText("http://127.0.0.1:port")
        self.edit.setFixedWidth(260)
        self.hBoxLayout.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)


class _ProxyStatusCard(SettingCard):
    """当前生效代理 + 检测/测试按钮。

    内容文本由 :func:`_update_status` 维护，可能呈现三种风格：

    * **生效**：``http://127.0.0.1:7890　（系统代理）``
    * **未生效（但有建议）**：``Windows 系统代理未启用；可点击右侧"自动检测"扫描本机端口``
    * **完全关闭**：``已关闭代理 —— 直接访问网络``
    """

    def __init__(self, parent=None):
        super().__init__(
            FIF.WIFI,
            "当前生效代理",
            "（尚未检测）",
            parent,
        )
        self.btn_detect = PushButton("自动检测", self)
        self.btn_detect.setFont(QFont("Microsoft YaHei", 10))
        self.btn_test = PushButton("测试连通性", self)
        self.btn_test.setFont(QFont("Microsoft YaHei", 10))
        self.hBoxLayout.addWidget(self.btn_detect, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addWidget(self.btn_test, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def set_state(self, content: str, is_active: bool) -> None:
        """根据 ``is_active`` 改变图标 + 文本，让用户一眼看出当前是否生效。"""
        self.setContent(content)
        # 用 qfluent 的 ICON 切换：生效 → ACCEPT；未生效 → INFO
        try:
            self.iconLabel.setIcon(FIF.ACCEPT if is_active else FIF.INFO)  # type: ignore[attr-defined]
        except Exception:
            pass


def attach_proxy_group(settings_interface: "SettingsInterface") -> None:
    """把"网络与代理"分组追加到设置界面（位于"关于"之前）。

    通过 ``settings_interface.expandLayout.addWidget`` 添加；不修改其它已有控件。
    """
    parent = settings_interface
    group = SettingCardGroup("网络与代理（更新源）", parent.scrollWidget)
    mode_card = _ProxyModeCard(group)
    manual_card = _ProxyManualCard(group)
    status_card = _ProxyStatusCard(group)

    group.addSettingCard(mode_card)
    group.addSettingCard(manual_card)
    group.addSettingCard(status_card)
    parent.expandLayout.addWidget(group)

    # 用对象属性挂在 SettingsInterface 上，便于调试 / 测试
    parent.proxy_group = group  # type: ignore[attr-defined]
    parent.card_proxy_mode = mode_card  # type: ignore[attr-defined]
    parent.card_proxy_manual = manual_card  # type: ignore[attr-defined]
    parent.card_proxy_status = status_card  # type: ignore[attr-defined]

    # ── 初值 ──
    # ``ensure_persisted`` 会在用户 config.json 还没有 ``updater`` 节点时
    # 主动落盘一次默认值，确保设置可见、可被手动编辑。
    s = ensure_persisted(parent.get_settings())
    mode_card.combo.setCurrentIndex(_MODE_TO_INDEX.get(s.proxy_mode, 1))
    manual_card.edit.setText(s.proxy_manual_url)
    _update_status(status_card, s.proxy_mode, s.proxy_manual_url, initial=True)
    _refresh_manual_enabled(manual_card, s.proxy_mode)

    # ── 槽 ──

    def _save_and_refresh():
        mode = _INDEX_TO_MODE.get(mode_card.combo.currentIndex(), "system")
        manual = manual_card.edit.text().strip()
        cur = UpdaterSettings.load(parent.get_settings())
        cur.proxy_mode = mode
        cur.proxy_manual_url = manual
        cur.save(parent.get_settings())
        _refresh_manual_enabled(manual_card, mode)
        _update_status(status_card, mode, manual)

    mode_card.combo.currentIndexChanged.connect(lambda _i: _save_and_refresh())
    manual_card.edit.editingFinished.connect(_save_and_refresh)

    def _on_detect():
        info = detect_proxy_auto()
        if info and info.is_valid:
            # 写入 manual 并切到 manual 模式
            manual_card.edit.setText(info.url)
            mode_card.combo.setCurrentIndex(_MODE_TO_INDEX["manual"])
            _save_and_refresh()
            InfoBar.success(
                title="检测成功",
                content=f"已使用代理 {info.url}（来源：{info.source}）",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=parent.window(),
            )
        else:
            ports_hint = ", ".join(str(p) for p in COMMON_PROXY_PORTS[:6])
            InfoBar.warning(
                title="未检测到代理",
                content=f"未发现系统代理，也未在常用端口（{ports_hint} 等）发现监听",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4500,
                parent=parent.window(),
            )

    status_card.btn_detect.clicked.connect(_on_detect)
    status_card.btn_test.clicked.connect(
        lambda: _test_connectivity(parent, status_card)
    )


# ───────────────────────── 内部辅助 ─────────────────────────


def _refresh_manual_enabled(card: _ProxyManualCard, mode: str) -> None:
    card.edit.setEnabled(mode == "manual")


def _update_status(
    card: _ProxyStatusCard,
    mode: str,
    manual_url: str,
    initial: bool = False,
) -> None:
    """根据当前模式 + 手动地址刷新状态卡片的内容与图标。"""
    info, _ = resolve_proxy(mode, manual_url)

    # 真正生效（含 mode=off：明确"已关闭"也算"用户已做出选择"）
    if mode == "off":
        card.set_state("已关闭代理 —— 应用将直接访问网络。", is_active=True)
        return

    if info and info.is_valid:
        # 同时提示来源（system / scan / manual）便于排查
        src_label = {"system": "系统代理", "scan": "自动检测", "manual": "手动指定"}.get(
            info.source, info.source or "未知"
        )
        card.set_state(
            f"{info.url}　已生效（{_mode_label(mode)} · 来源：{src_label}）",
            is_active=True,
        )
        return

    # 未生效 —— 给出具体建议
    if mode == "system":
        # 主动尝试扫描一次本地端口，帮用户判断是不是该切到 auto 模式
        scan_hint = _build_scan_hint()
        if scan_hint:
            tip = (
                f"Windows 系统代理未启用，但 {scan_hint}。"
                "建议切换为「自动检测代理」或「手动指定地址」。"
            )
        else:
            tip = (
                "Windows 系统代理未启用，本机也未发现常用代理端口监听。"
                "若你的代理软件正在运行，请改用「手动指定地址」。"
            )
        card.set_state(tip, is_active=False)
        return

    if mode == "auto":
        card.set_state(
            "自动检测未发现可用代理。如确有代理在运行，请改用「手动指定地址」。",
            is_active=False,
        )
        return

    if mode == "manual":
        if not manual_url.strip():
            card.set_state(
                "尚未填写手动代理地址，例如 http://127.0.0.1:7897",
                is_active=False,
            )
        else:
            card.set_state(
                f"手动地址 {manual_url!r} 无效，请检查协议与端口。",
                is_active=False,
            )
        return

    card.set_state("未启用代理", is_active=False)


def _mode_label(mode: str) -> str:
    return _MODE_LABELS[_MODE_TO_INDEX.get(mode, 0)]


def _build_scan_hint() -> str:
    """启用 `system` 模式失败时，扫描一次本地代理端口给出"备选源"提示。"""
    from ..proxy import scan_local_proxy_ports
    found = scan_local_proxy_ports(timeout=0.10)
    if not found:
        return ""
    head = ", ".join(str(p) for p in found[:3])
    return f"在本机端口 {head} 上检测到代理监听"


def _test_connectivity(parent: QWidget, card: _ProxyStatusCard) -> None:
    """用当前代理设置请求 GitHub API 测试连通性。"""
    s = UpdaterSettings.load()
    _info, proxies = resolve_proxy(s.proxy_mode, s.proxy_manual_url)
    card.btn_test.setEnabled(False)
    card.btn_test.setText("测试中...")
    try:
        result = http_client.get_json(
            "https://api.github.com/zen",
            proxies=proxies,
            timeout=(5.0, 10.0),
        )
    finally:
        card.btn_test.setEnabled(True)
        card.btn_test.setText("测试连通性")

    if result.ok or result.status == 200:
        InfoBar.success(
            title="连通成功",
            content="GitHub API 可达",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=3000,
            parent=parent.window(),
        )
    else:
        InfoBar.error(
            title="连通失败",
            content=result.error or f"HTTP {result.status}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=parent.window(),
        )
