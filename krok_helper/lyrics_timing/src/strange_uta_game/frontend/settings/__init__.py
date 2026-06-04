"""设置界面模块。

提供应用设置管理界面。
"""

from .app_settings import AppSettings


def __getattr__(name: str):
    if name == "SettingsInterface":
        from .settings_interface import SettingsInterface

        return SettingsInterface
    raise AttributeError(name)

__all__ = [
    "SettingsInterface",
    "AppSettings",
]
