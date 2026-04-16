"""设置界面模块。

提供应用设置管理界面组件。
"""

from .settings_interface import (
    SettingsInterface,
    SettingsDialog,
    AppSettings,
)
from .singer_manager import SingerManagerInterface
from .ruby_editor import RubyInterface

__all__ = [
    "SettingsInterface",
    "SettingsDialog",
    "AppSettings",
    "SingerManagerInterface",
    "RubyInterface",
]
