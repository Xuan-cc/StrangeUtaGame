"""本地化（i18n）子系统。

设计要点：

- **两个 translator 并存**：
    1. :class:`qfluentwidgets.FluentTranslator` —— 翻译 qfluentwidgets 内置控件
       的字符串（右键菜单 Cut/Copy/Paste、对话框 OK/Cancel 等）。它硬编码加载
       ``:/qfluentwidgets/i18n/qfluentwidgets.<locale>.qm``，无法被替换。
    2. :class:`AppTranslator` —— 加载本 app 自己的 ``app.<locale>.qm``。
       本目录下的 ``translations/`` 用于存放对应 ``.ts`` / ``.qm``。
- 二者通过 ``QApplication.installTranslator`` 并行安装，互不干扰。
- 当前只注册 ``zh_CN`` 一种语言；EN/JA 待翻译完成后再加入
  :data:`AVAILABLE_LANGUAGES`。
- 源文本即简体中文。``zh_CN`` 不需要 ``.qm`` 文件——找不到时 ``tr()`` 自动
  回落到源字符串，正是我们要的效果。
"""

from __future__ import annotations

from .manager import (
    AUTO_LANGUAGE_CODE,
    AVAILABLE_LANGUAGES,
    DEFAULT_LANGUAGE,
    LocalizationManager,
    Language,
    PSEUDO_LANGUAGE_CODE,
    install_translators,
    language_by_code,
    localization,
    resolve_auto_language,
)
from .retranslate import detach_layout_for_rebuild

__all__ = [
    "AUTO_LANGUAGE_CODE",
    "AVAILABLE_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "LocalizationManager",
    "Language",
    "PSEUDO_LANGUAGE_CODE",
    "detach_layout_for_rebuild",
    "install_translators",
    "language_by_code",
    "localization",
    "resolve_auto_language",
]
