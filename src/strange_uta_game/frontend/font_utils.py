"""字体相关公共工具。

集中处理「用户自选系统字体」带来的两类问题：
- 字体族在当前系统不存在时的回退（跨机器 / 跨打包变体）。
- 字宽统计（ch 单位）测量时所选字体缺少全角参考字形 ``一`` 的回退。

界面字体会随 UI 语言选择对应平台的常用字体；歌词预览字体仍由用户设置控制。
"""

from __future__ import annotations

import sys

from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics


def _platform_default_font_family() -> str:
    """各平台的默认中文 UI 字体族。

    - Windows：``Microsoft YaHei``（微软雅黑）。
    - macOS：``PingFang SC``（苹方-简，10.11+ 系统内置的默认中文 UI 字体，
      地位对应微软雅黑）。
    - 其余（Linux 等）：``Noto Sans CJK SC``，缺失时由下游 ``resolve_font_family``
      / Qt 自身再行回退。
    """
    if sys.platform == "darwin":
        return "PingFang SC"
    if sys.platform.startswith("win"):
        return "Microsoft YaHei"
    return "Noto Sans CJK SC"


#: 当前平台的默认中文 UI 字体族（字体回退与硬编码控件字体的单一真源）。
DEFAULT_FONT_FAMILY = _platform_default_font_family()


_active_ui_language = "zh_CN"


def _ui_font_candidates(language_code: str) -> tuple[str, ...]:
    """返回某 UI 语言在当前平台的常用无衬线字体（按优先级排列）。"""
    language = (language_code or "").lower()
    is_japanese = language.startswith("ja")
    is_chinese = language.startswith("zh")

    if sys.platform.startswith("win"):
        if is_japanese:
            return ("Yu Gothic UI", "Meiryo UI", "Meiryo", "Microsoft YaHei UI")
        if is_chinese:
            return ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI")
        return ("Segoe UI", "Arial")

    if sys.platform == "darwin":
        if is_japanese:
            return ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "YuGothic")
        if is_chinese:
            return ("PingFang SC", "Heiti SC")
        return ("SF Pro Text", "Helvetica Neue", "Arial")

    if is_japanese:
        return ("Noto Sans CJK JP", "Noto Sans JP", "IPAGothic")
    if is_chinese:
        return ("Noto Sans CJK SC", "Noto Sans SC", "WenQuanYi Micro Hei")
    return ("Noto Sans", "DejaVu Sans", "Liberation Sans")


def ui_font_families(language_code: str | None = None) -> list[str]:
    """返回当前系统实际可用的 UI 字体回退链。"""
    candidates = _ui_font_candidates(language_code or _active_ui_language)
    installed = {family.casefold(): family for family in QFontDatabase.families()}
    available = [installed[name.casefold()] for name in candidates if name.casefold() in installed]

    system_family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if system_family and system_family.casefold() not in {f.casefold() for f in available}:
        available.append(system_family)
    if not available:
        available.append(DEFAULT_FONT_FAMILY)
    return available


def set_ui_language(language_code: str) -> None:
    """更新 UI 字体语言，并将对应字体应用到 QApplication。"""
    global _active_ui_language
    _active_ui_language = language_code

    # 延迟导入避免仅使用字体测量工具时强制创建/依赖 QApplication。
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    font = QFont(app.font())
    font.setFamilies(ui_font_families(language_code))
    app.setFont(font)


def ui_font(point_size: int, weight: QFont.Weight = QFont.Weight.Normal) -> QFont:
    """以当前界面语言的系统常用字体构造 ``QFont``。

    字体链由 :func:`set_ui_language` 随本地化设置更新；最后附带系统通用字体，
    确保目标语言字体缺失时仍能正常显示。
    """
    font = QFont()
    font.setFamilies(ui_font_families())
    font.setPointSize(point_size)
    font.setWeight(weight)
    return font


def resolve_font_family(family: str | None) -> str:
    """返回系统中可用的字体族名；不存在则回退到 :data:`DEFAULT_FONT_FAMILY`。"""
    if family and family in QFontDatabase.families():
        return family
    return DEFAULT_FONT_FAMILY


def make_ch_width_metrics(family: str | None, point_size: int = 16) -> tuple[QFontMetrics, str]:
    """构造用于字宽（ch）统计的 ``QFontMetrics``，并返回实际使用的字体族名。

    ch 是以全角字 ``一`` 半宽为 1 的归一化比值，故 ``point_size`` 不影响结果，
    仅需任意正值。若所选字体缺少 ``一`` 字形（如纯西文字体），测量会失真，
    此时回退到 :data:`DEFAULT_FONT_FAMILY` 测量（显示字体不受影响）。

    Returns:
        ``(metrics, effective_family)``：用于测量的度量对象，及实际测量所用字体族。
    """
    fam = resolve_font_family(family)
    fm = QFontMetrics(QFont(fam, point_size))
    if fm.horizontalAdvance("一") <= 0 and fam != DEFAULT_FONT_FAMILY:
        fam = DEFAULT_FONT_FAMILY
        fm = QFontMetrics(QFont(fam, point_size))
    return fm, fam
