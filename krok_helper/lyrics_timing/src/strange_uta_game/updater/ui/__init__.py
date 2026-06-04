"""updater 模块的 PyQt6 / qfluentwidgets 用户界面层。

仅在 GUI 进程使用；独立 ``Updater.exe`` 不依赖本子包。
"""

from __future__ import annotations

from .proxy_card import attach_proxy_group
from .update_card import attach_update_group, refresh_about_version
from .update_dialog import UpdateAvailableDialog, UpdateCheckErrorDialog

__all__ = [
    "UpdateAvailableDialog",
    "UpdateCheckErrorDialog",
    "attach_proxy_group",
    "attach_update_group",
    "refresh_about_version",
]
