"""导出器模块。

提供各种格式的歌词导出功能。
"""

from .base import IExporter, BaseExporter, ExportError
from .lrc_exporter import LRCExporter, KRAExporter
from .txt_exporter import TXTExporter
from .txt2ass_exporter import Txt2AssExporter, ASSDirectExporter
from .nicokara_exporter import NicokaraExporter, NicokaraWithRubyExporter
from .inline_exporter import InlineExporter

# 所有可用的导出器
ALL_EXPORTERS = [
    LRCExporter,
    KRAExporter,
    TXTExporter,
    Txt2AssExporter,
    ASSDirectExporter,
    NicokaraExporter,
    NicokaraWithRubyExporter,
    InlineExporter,
]


def get_exporter_by_name(name: str) -> IExporter:
    """根据名称获取导出器实例

    Args:
        name: 导出器名称 ('LRC', 'KRA', 'TXT', 等)

    Returns:
        导出器实例

    Raises:
        ValueError: 找不到对应名称的导出器
    """
    for exporter_class in ALL_EXPORTERS:
        exporter = exporter_class()
        if exporter.name == name:
            return exporter

    raise ValueError(f"未知的导出器: {name}")


def get_exporter_by_extension(ext: str) -> IExporter:
    """根据扩展名获取导出器实例

    Args:
        ext: 文件扩展名 (如 '.lrc', '.txt')

    Returns:
        导出器实例

    Raises:
        ValueError: 找不到对应扩展名的导出器
    """
    ext = ext.lower()

    for exporter_class in ALL_EXPORTERS:
        exporter = exporter_class()
        if exporter.file_extension.lower() == ext:
            return exporter

    raise ValueError(f"不支持的扩展名: {ext}")


def get_all_exporters():
    """获取所有导出器实例"""
    return [exporter_class() for exporter_class in ALL_EXPORTERS]


__all__ = [
    "IExporter",
    "BaseExporter",
    "ExportError",
    "LRCExporter",
    "KRAExporter",
    "TXTExporter",
    "Txt2AssExporter",
    "ASSDirectExporter",
    "NicokaraExporter",
    "NicokaraWithRubyExporter",
    "InlineExporter",
    "ALL_EXPORTERS",
    "get_exporter_by_name",
    "get_exporter_by_extension",
    "get_all_exporters",
]
