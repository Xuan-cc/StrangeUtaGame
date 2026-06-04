"""WinRT 日语注音引擎可用性 UI 引导。

在触发日语注音前调用 :func:`ensure_winrt_japanese`：
- 引擎可用 → 直接返回 True；
- 缺少日语 IME 功能 → 弹对话框，提供「现在安装（UAC 提权）」/「手动安装」/「暂不」；
  - 现在安装：先说明将弹出 UAC，确认后在后台线程跑 Add-WindowsCapability，
    安装中显示忙碌提示；成功→True，UAC 被拒/失败→转手动引导文案。
- 缺 winrt 包（打包问题）→ 报错，无法现场修复。

后端探测/安装/文案见 ``ruby_analyzer`` 的
:func:`winrt_japanese_status` / :func:`install_winrt_japanese` / :func:`winrt_install_guidance`。
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QMessageBox,
    QPushButton,
    QWidget,
)

from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    WINRT_JA_CAPABILITY,
    install_winrt_japanese,
    winrt_install_guidance,
    winrt_japanese_status,
)

_INSTALL_CMD = f"Add-WindowsCapability -Online -Name {WINRT_JA_CAPABILITY}"


class _InstallWorker(QThread):
    """后台执行 UAC 提权安装，避免阻塞 UI 线程。"""

    finished_result = pyqtSignal(bool, str)

    def run(self) -> None:
        ok, msg = install_winrt_japanese()
        self.finished_result.emit(ok, msg)


def _show_guidance(parent: Optional[QWidget], extra: str = "") -> None:
    """展示手动安装引导，附「复制命令」按钮。"""
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Information)
    box.setWindowTitle("手动安装日语注音组件")
    box.setText((extra + "\n\n" if extra else "") + winrt_install_guidance())
    copy_btn = box.addButton("复制命令", QMessageBox.ButtonRole.ActionRole)
    box.addButton("我知道了", QMessageBox.ButtonRole.AcceptRole)
    # 阻止「复制命令」关闭对话框：点后复制到剪贴板并保持打开
    copy_btn.clicked.disconnect()
    copy_btn.clicked.connect(
        lambda: QApplication.clipboard().setText(_INSTALL_CMD)
    )
    box.exec()


def _run_install_blocking(parent: Optional[QWidget]) -> bool:
    """后台线程跑安装 + 模态忙碌提示，返回是否安装成功。"""
    busy = QMessageBox(parent)
    busy.setIcon(QMessageBox.Icon.Information)
    busy.setWindowTitle("正在安装")
    busy.setText("正在从 Windows Update 下载并安装日语注音组件，请稍候…\n"
                 "（请在弹出的 UAC 窗口点击「是」以授权安装）")
    busy.setStandardButtons(QMessageBox.StandardButton.NoButton)

    result: dict = {}
    worker = _InstallWorker()

    def _on_done(ok: bool, msg: str) -> None:
        result["ok"] = ok
        result["msg"] = msg
        busy.accept()

    worker.finished_result.connect(_on_done)
    worker.start()
    busy.exec()  # 阻塞直到 _on_done 调 accept
    worker.wait()
    return bool(result.get("ok"))


def ensure_winrt_japanese(parent: Optional[QWidget] = None) -> bool:
    """确保注音引擎可用；必要时弹引导。返回最终是否可用。

    noWinIME / mac 变体使用 sudachi-mini 作为主引擎，无需 WinRT，直接返回 True。
    """
    from strange_uta_game.__version__ import VARIANT
    if VARIANT:
        # 非 main 变体：sudachi-mini 已打包，无需 WinRT，直接放行
        return True

    available, reason = winrt_japanese_status()
    if available:
        return True

    if reason == "no_winrt_package":
        QMessageBox.critical(
            parent,
            "缺少注音组件",
            "未找到 winrt 运行库（winrt-Windows.Globalization）。\n"
            "这通常是安装包不完整导致，请重新安装本应用或联系开发者。",
        )
        return False

    # engine_unavailable / error：缺日语 IME 功能，引导安装
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle("需要安装日语注音组件")
    box.setText(
        "日语注音需要 Windows 的日语功能（含日语 IME），当前系统未安装。\n"
        "约几十 MB，从 Windows Update 联网下载，不会更改系统显示语言。\n\n"
        "是否现在安装？"
    )
    btn_install = box.addButton("现在安装", QMessageBox.ButtonRole.AcceptRole)
    btn_manual = box.addButton("手动安装", QMessageBox.ButtonRole.ActionRole)
    box.addButton("暂不", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    clicked = box.clickedButton()

    if clicked is btn_manual:
        _show_guidance(parent)
        return False
    if clicked is not btn_install:
        return False  # 暂不

    # 现在安装：先说明将弹出 UAC，征得同意
    confirm = QMessageBox.question(
        parent,
        "授权安装",
        "接下来会弹出 Windows 的「用户账户控制 (UAC)」窗口，\n"
        "请点击「是」以授权安装日语组件。\n\n是否继续？",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if confirm != QMessageBox.StandardButton.Yes:
        return False

    ok = _run_install_blocking(parent)
    if ok:
        QMessageBox.information(parent, "安装完成", "日语注音组件已安装，可以开始注音了。")
        return True

    # UAC 被拒或安装失败 → 转手动引导
    _show_guidance(
        parent,
        extra="自动安装未完成（可能未授权 UAC 或下载失败）。可按下面的方式手动安装：",
    )
    return False
