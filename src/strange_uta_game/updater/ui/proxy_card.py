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

from PyQt6.QtCore import Qt, QCoreApplication, QTimer
from strange_uta_game.frontend.font_utils import ui_font


def _tr(s: str) -> str:
    """模块级 tr 别名，统一 "UpdaterUI" context。"""
    return QCoreApplication.translate("UpdaterUI", s)
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
# 源串保留中文供 tr 扫描；显示时统一 _tr() 翻译
_MODE_LABELS = ["关闭代理", "使用系统代理", "自动检测代理", "手动指定地址"]


def _mode_labels_translated() -> list[str]:
    # 显式逐项 _tr 让 extractor 在 UpdaterUI 上下文里登记源串
    # （列表推导 _tr(变量) 形式静态抽取拿不到字面参数）
    return [
        _tr("关闭代理"),
        _tr("使用系统代理"),
        _tr("自动检测代理"),
        _tr("手动指定地址"),
    ]


class _ProxyModeCard(SettingCard):
    """代理模式选择卡片（继承官方 SettingCard 风格）。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.GLOBE,
            _tr("代理模式"),
            _tr("访问 GitHub 时是否经过代理；自动检测会探测常用本地代理端口"),
            parent,
        )
        self.combo = ComboBox(self)
        self.combo.addItems(_mode_labels_translated())
        # 翻译后选项长度可能变化（"自动检测代理" → "Auto-detect proxy"）
        self.combo.setMinimumWidth(180)
        self.hBoxLayout.addWidget(self.combo, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def changeEvent(self, event):
        from PyQt6.QtCore import QEvent as _QEvent
        if event.type() == _QEvent.Type.LanguageChange:
            self.titleLabel.setText(_tr("代理模式"))
            self.contentLabel.setText(_tr("访问 GitHub 时是否经过代理；自动检测会探测常用本地代理端口"))
            idx = self.combo.currentIndex()
            self.combo.blockSignals(True)
            self.combo.clear()
            self.combo.addItems(_mode_labels_translated())
            if 0 <= idx < self.combo.count():
                self.combo.setCurrentIndex(idx)
            self.combo.blockSignals(False)
        super().changeEvent(event)


class _ProxyManualCard(SettingCard):
    """手动代理地址输入卡片。"""

    def __init__(self, parent=None):
        super().__init__(
            FIF.LINK,
            _tr("手动代理地址"),
            _tr("例如 http://127.0.0.1:7890 ；仅在选择「手动指定地址」时生效"),
            parent,
        )
        self.edit = LineEdit(self)
        self.edit.setPlaceholderText("http://127.0.0.1:port")
        self.edit.setMinimumWidth(260)
        self.hBoxLayout.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignRight)
        self.hBoxLayout.addSpacing(16)

    def changeEvent(self, event):
        from PyQt6.QtCore import QEvent as _QEvent
        if event.type() == _QEvent.Type.LanguageChange:
            self.titleLabel.setText(_tr("手动代理地址"))
            self.contentLabel.setText(_tr("例如 http://127.0.0.1:7890 ；仅在选择「手动指定地址」时生效"))
        super().changeEvent(event)


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
            _tr("当前生效代理"),
            _tr("（尚未检测）"),
            parent,
        )
        self.btn_detect = PushButton(_tr("自动检测"), self)
        self.btn_detect.setFont(ui_font(10))
        self.btn_test = PushButton(_tr("测试连通性"), self)
        self.btn_test.setFont(ui_font(10))
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

    def changeEvent(self, event):
        from PyQt6.QtCore import QEvent as _QEvent
        if event.type() == _QEvent.Type.LanguageChange:
            self.titleLabel.setText(_tr("当前生效代理"))
            self.btn_detect.setText(_tr("自动检测"))
            self.btn_test.setText(_tr("测试连通性"))
            # 父对象 SettingsInterface 会在下一次 _update_status 重新计算
            # content；这里只刷新按钮即可。
        super().changeEvent(event)


def attach_proxy_group(settings_interface: "SettingsInterface") -> None:
    """把"网络与代理"分组追加到设置界面（位于"关于/语言"之前）。

    通过 ``settings_interface.expandLayout.addWidget`` 添加；不修改其它已有控件。
    """
    parent = settings_interface
    group = SettingCardGroup(_tr("网络与代理（更新源）"), parent.scrollWidget)
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

    save_apply_pending = False

    def _save_and_refresh():
        # 重活延迟到下一轮事件循环（防抖合并连续变更）：
        # qfluentwidgets 的 currentIndexChanged 在弹层（RoundMenu）自身的
        # 激活/关闭调用栈内同步发出，此时同步 load/save 设置文件、system
        # 模式下还会扫本地代理端口（阻塞 IO），会与弹层销毁竞争、快速
        # 点击卡 UI（同 ai_timing_dialog 模型下拉的修复）。
        nonlocal save_apply_pending
        if save_apply_pending:
            return
        save_apply_pending = True
        QTimer.singleShot(0, _apply_save_and_refresh)

    def _apply_save_and_refresh():
        nonlocal save_apply_pending
        save_apply_pending = False
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
                title=_tr("检测成功"),
                content=_tr("已使用代理 {url}（来源：{source}）").format(
                    url=info.url, source=info.source
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3500,
                parent=parent.window(),
            )
        else:
            ports_hint = ", ".join(str(p) for p in COMMON_PROXY_PORTS[:6])
            InfoBar.warning(
                title=_tr("未检测到代理"),
                content=_tr("未发现系统代理，也未在常用端口（{ports} 等）发现监听").format(
                    ports=ports_hint
                ),
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
        card.set_state(_tr("已关闭代理 —— 应用将直接访问网络。"), is_active=True)
        return

    if info and info.is_valid:
        # 同时提示来源（system / scan / manual）便于排查
        _src_map = {"system": _tr("系统代理"), "scan": _tr("自动检测"), "manual": _tr("手动指定")}
        src_label = _src_map.get(info.source, info.source or _tr("未知"))
        card.set_state(
            _tr("{url}　已生效（{mode} · 来源：{source}）").format(
                url=info.url, mode=_mode_label(mode), source=src_label,
            ),
            is_active=True,
        )
        return

    # 未生效 —— 给出具体建议
    if mode == "system":
        # 主动尝试扫描一次本地端口，帮用户判断是不是该切到 auto 模式
        scan_hint = _build_scan_hint()
        if scan_hint:
            tip = _tr(
                "Windows 系统代理未启用，但 {hint}。"
                "建议切换为「自动检测代理」或「手动指定地址」。"
            ).format(hint=scan_hint)
        else:
            tip = _tr(
                "Windows 系统代理未启用，本机也未发现常用代理端口监听。"
                "若你的代理软件正在运行，请改用「手动指定地址」。"
            )
        card.set_state(tip, is_active=False)
        return

    if mode == "auto":
        card.set_state(
            _tr("自动检测未发现可用代理。如确有代理在运行，请改用「手动指定地址」。"),
            is_active=False,
        )
        return

    if mode == "manual":
        if not manual_url.strip():
            card.set_state(
                _tr("尚未填写手动代理地址，例如 http://127.0.0.1:7897"),
                is_active=False,
            )
        else:
            card.set_state(
                _tr("手动地址 {url!r} 无效，请检查协议与端口。").format(url=manual_url),
                is_active=False,
            )
        return

    card.set_state(_tr("未启用代理"), is_active=False)


def _mode_label(mode: str) -> str:
    return _tr(_MODE_LABELS[_MODE_TO_INDEX.get(mode, 0)])


def _build_scan_hint() -> str:
    """启用 `system` 模式失败时，扫描一次本地代理端口给出"备选源"提示。"""
    from ..proxy import scan_local_proxy_ports
    found = scan_local_proxy_ports(timeout=0.10)
    if not found:
        return ""
    head = ", ".join(str(p) for p in found[:3])
    return _tr("在本机端口 {ports} 上检测到代理监听").format(ports=head)


def _test_connectivity(parent: QWidget, card: _ProxyStatusCard) -> None:
    """用当前代理设置探测 GitHub 可达性。

    注意：**不能**用 ``api.github.com`` 来判定连通——它对未认证请求有
    "60 次/小时、按出口 IP"的限流，机场共享节点很容易被打满而返回 403。
    那种 403 恰恰证明请求已经到达 GitHub（即代理是通的），却会被误判为
    "连通失败"。因此这里改用 github.com 网页端（不受该限流约束）作为主判据，
    并单独探一次 API 配额仅用于附加提示。
    """
    from ...__version__ import REPO_NAME, REPO_OWNER

    s = UpdaterSettings.load()
    _info, proxies = resolve_proxy(s.proxy_mode, s.proxy_manual_url)
    card.btn_test.setEnabled(False)
    card.btn_test.setText(_tr("测试中..."))
    try:
        # 主判据：github.com 网页端 releases/latest 会 302 跳转，可达即视为连通。
        reach = http_client.get_redirect_location(
            f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/latest",
            proxies=proxies,
            timeout=(5.0, 10.0),
        )
        # 附加：探一次官方 API 是否被限流（仅用于提示，不决定成败）。
        api = http_client.get_json(
            "https://api.github.com/zen",
            proxies=proxies,
            timeout=(5.0, 10.0),
        )
    finally:
        card.btn_test.setEnabled(True)
        card.btn_test.setText(_tr("测试连通性"))

    if reach.ok:
        # 代理可达 GitHub。再看官方 API 是否被限流给出对应提示。
        if api.status == 403:
            InfoBar.warning(
                title=_tr("连通成功（API 限流中）"),
                content=_tr(
                    "代理可正常访问 GitHub，但官方 API 对你的出口 IP 限流（403）。"
                    "检查更新会自动改用网页端探测，通常不受影响。"
                ),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=5000,
                parent=parent.window(),
            )
        else:
            InfoBar.success(
                title=_tr("连通成功"),
                content=_tr("GitHub 可达"),
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=3000,
                parent=parent.window(),
            )
    else:
        InfoBar.error(
            title=_tr("连通失败"),
            content=reach.error or f"HTTP {reach.status}",
            orient=Qt.Orientation.Horizontal,
            isClosable=True,
            position=InfoBarPosition.TOP,
            duration=5000,
            parent=parent.window(),
        )
