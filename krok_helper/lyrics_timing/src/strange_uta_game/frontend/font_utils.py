"""字体相关公共工具。

集中处理「用户自选系统字体」带来的两类问题：
- 字体族在当前系统不存在时的回退（跨机器 / 跨打包变体）。
- 字宽统计（ch 单位）测量时所选字体缺少全角参考字形 ``一`` 的回退。
"""

from __future__ import annotations

from PyQt6.QtGui import QFont, QFontDatabase, QFontMetrics

DEFAULT_FONT_FAMILY = "Microsoft YaHei"


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
