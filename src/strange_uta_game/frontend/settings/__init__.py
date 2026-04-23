"""设置界面模块。

提供应用设置管理与演唱者管理界面。
"""

from .settings_interface import (
    SettingsInterface,
    AppSettings,
)
from .singer_interface import SingerManagerInterface

__all__ = [
    "SettingsInterface",
    "AppSettings",
    "SingerManagerInterface",
]
