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
_ui_font_override = ""
_managed_ui_primary_families: set[str] = set()


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


def _installed_font_family(family: str | None) -> str:
    """返回系统中的规范字体族名；未安装或为空时返回空串。"""
    if not family:
        return ""
    installed = {name.casefold(): name for name in QFontDatabase.families()}
    return installed.get(family.casefold(), "")


def resolve_ui_font_override(family: str | None) -> str:
    """解析用户 UI 字体设置；``auto``、空值和缺失字体均表示自动。"""
    if not family or family.casefold() == "auto":
        return ""
    return _installed_font_family(family)


def ui_font_families(language_code: str | None = None) -> list[str]:
    """返回当前系统实际可用的 UI 字体回退链。

    用户指定字体优先；目标语言常用字体与系统通用字体继续作为字形回退，避免
    西文字体不含中日文字形时出现方框。
    """
    candidates = _ui_font_candidates(language_code or _active_ui_language)
    installed = {family.casefold(): family for family in QFontDatabase.families()}
    available: list[str] = []
    if _ui_font_override:
        available.append(_ui_font_override)
    available.extend(
        installed[name.casefold()]
        for name in candidates
        if name.casefold() in installed
        and installed[name.casefold()].casefold() not in {f.casefold() for f in available}
    )

    system_family = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont).family()
    if system_family and system_family.casefold() not in {f.casefold() for f in available}:
        available.append(system_family)
    if not available:
        available.append(DEFAULT_FONT_FAMILY)
    return available


def _apply_application_ui_font(previous_families: list[str] | None = None) -> None:
    """将当前 UI 字体链应用到 Qt 与 QFluentWidgets。

    QFluentWidgets 会给控件显式设置自己的字体，单独调用
    ``QApplication.setFont`` 无法覆盖它；因此还要同步其全局字体配置，并刷新
    已创建且仍使用旧 UI 字体链的显式字体控件。
    """
    families = ui_font_families()
    old_fluent_families: list[str] = []
    try:
        import qfluentwidgets
        from qfluentwidgets.common.style_sheet import updateStyleSheet

        old_fluent_families = qfluentwidgets.fontFamilies()
        set_fluent_font_families = qfluentwidgets.setFontFamilies
        update_fluent_style_sheet = updateStyleSheet
    except Exception:
        # 字体工具也会在不安装 qfluentwidgets 的最小环境中使用。
        set_fluent_font_families = None
        update_fluent_style_sheet = None

    # 延迟导入避免仅使用字体测量工具时强制创建/依赖 QApplication。
    from PyQt6.QtWidgets import QApplication, QToolTip, QWidget

    app = QApplication.instance()
    if app is None:
        if set_fluent_font_families is not None:
            set_fluent_font_families(families)
        return

    from strange_uta_game.frontend.fluent_tooltips import install_fluent_tooltips

    install_fluent_tooltips(app)

    managed_chains = [old_fluent_families]
    if previous_families:
        managed_chains.append(previous_families)
    _managed_ui_primary_families.update(
        chain[0].casefold() for chain in managed_chains if chain
    )

    # 必须在修改 QApplication / Fluent 全局字体前先记录控件。父控件收到
    # FontChange 时会立即改变子控件的继承字体，如果边修改边筛选，遍历顺序靠后的
    # QLabel、LineEdit 等子控件就可能被漏掉，随后又被自身样式恢复为旧字体。
    managed_widgets: list[tuple[QWidget, QFont]] = []
    for widget in app.allWidgets():
        current = QFont(widget.font())
        current_families = current.families()
        if (
            current_families
            and current_families[0].casefold() in _managed_ui_primary_families
        ):
            managed_widgets.append((widget, current))

    if set_fluent_font_families is not None:
        set_fluent_font_families(families)
        if update_fluent_style_sheet is not None:
            update_fluent_style_sheet(lazy=False)

    font = QFont(app.font())
    font.setFamilies(families)
    app.setFont(font)

    tooltip_font = QFont(QToolTip.font())
    tooltip_font.setFamilies(families)
    QToolTip.setFont(tooltip_font)

    for widget, widget_font in managed_widgets:
        widget_font.setFamilies(families)
        widget.setFont(widget_font)

    _managed_ui_primary_families.add(families[0].casefold())


def set_ui_font_override(family: str | None) -> str:
    """设置用户选择的 UI 字体并返回实际覆盖值；空串表示按语言自动选择。"""
    global _ui_font_override
    previous_families = ui_font_families()
    _ui_font_override = resolve_ui_font_override(family)
    _apply_application_ui_font(previous_families)
    return _ui_font_override


def set_ui_language(language_code: str) -> None:
    """更新 UI 字体语言，并保留用户字体覆盖设置。"""
    global _active_ui_language
    previous_families = ui_font_families()
    _active_ui_language = language_code
    _apply_application_ui_font(previous_families)


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
