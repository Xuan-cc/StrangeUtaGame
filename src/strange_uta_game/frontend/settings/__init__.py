"""设置界面模块。

提供应用设置管理界面组件。
"""

from .settings_interface import (
    SettingsInterface,
    AppSettings,
)
from .singer_manager import SingerManagerInterface
from .ruby_editor import RubyInterface

__all__ = [
    "SettingsInterface",
    "AppSettings",
    "SingerManagerInterface",
    "RubyInterface",
]
