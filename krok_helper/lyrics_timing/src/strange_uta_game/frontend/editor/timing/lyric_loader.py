"""歌词文件解析工具。

提供歌词格式检测和解析功能，支持 LRC/SRT/ASS/Nicokara/内联格式等。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from strange_uta_game.backend.domain import Sentence, Singer
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    LRCParser,
    NicokaraParser,
    nicokara_result_to_sentences,
    parse_to_sentences,
)
from strange_uta_game.backend.infrastructure.parsers.inline_format import (
    sentences_from_inline_text,
)


# 内联格式检测（包括 inline 和纯 RLF 文本格式）
_INLINE_PATTERN = re.compile(r"\[.*?\|.*?\]|{[^}]+\|[^}]+}")
# LRC 时间标签检测
_LRC_PATTERN = re.compile(r"\[\d{2}:\d{2}\.\d{2,3}\]")
# ASS 格式检测
_ASS_PATTERN = re.compile(r"^\[Script Info\]|^Dialogue:\s*\d+", re.MULTILINE)
# SRT 格式检测
_SRT_PATTERN = re.compile(
    r"\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3}"
)


def _is_json_content(content: str) -> bool:
    """检测内容是否为 JSON 格式（可能是 SUG 项目文件）。"""
    stripped = content.strip()
    if not stripped:
        return False
    # 快速检查：JSON 必须以 { 或 [ 开头
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return False
    # 尝试解析 JSON
    try:
        data = json.loads(stripped)
        # 检查是否包含 SUG 项目文件的特征字段
        if isinstance(data, dict):
            sug_keys = {"version", "id", "metadata", "singers", "sentences"}
            if sug_keys.intersection(data.keys()):
                return True
        return False
    except (json.JSONDecodeError, ValueError):
        return False


def _sync_nicokara_metadata_to_settings(metadata: dict) -> None:
    """把 Nicokara 解析出的 @ 元数据写回 AppSettings.nicokara_tags（覆盖式）。

    SHINTA 2025 规格 K：未知 @ 标签需要保留并在导出时原样回写，
    实现跨工具 round-trip 兼容。

    Args:
        metadata: NicokaraParseResult.metadata，{key: value} 扁平字典（key 不含 @）。

    映射：
        Title         → tags["title"]
        Artist        → tags["artist"]
        Album         → tags["album"]
        TaggingBy     → tags["tagging_by"]
        SilencemSec   → tags["silence_ms"] (int)
        Offset        → 跳过（由 Project.offset_ms 承载）
        其余 *        → tags["custom"]，每项形如 "@Key=Value"

    覆盖式：旧的 nicokara_tags 全部被替换，不做合并——
    用户「每次写入项目都换」的语义。
    """
    if not metadata:
        return
    try:
        from strange_uta_game.frontend.settings.settings_interface import (
            AppSettings,
        )
    except Exception:
        return

    known_map = {
        "Title": "title",
        "Artist": "artist",
        "Album": "album",
        "TaggingBy": "tagging_by",
    }
    tags: dict = {}
    custom: list = []
    for key, value in metadata.items():
        if key in known_map:
            tags[known_map[key]] = value
        elif key == "SilencemSec":
            try:
                tags["silence_ms"] = int(value)
            except (TypeError, ValueError):
                custom.append(f"@{key}={value}")
        elif key == "Offset":
            # @Offset 由 Project.offset_ms 单独承载，跳过避免双重写入
            continue
        else:
            custom.append(f"@{key}={value}")
    if custom:
        tags["custom"] = custom

    try:
        AppSettings().set("nicokara_tags", tags)
    except Exception:
        # 写入失败不阻断导入；exporter 端 fallback 仍可用旧值
        pass


def detect_lyric_format(content: str) -> str:
    """检测歌词内容的格式。

    Returns:
        格式名称: "sug", "inline", "nicokara", "ass", "srt", "lrc", "text"
    """
    # SUG/JSON 格式检测（最高优先级，避免误解析）
    if _is_json_content(content):
        return "sug"
    # 内联格式检测（包括 inline 和纯 RLF 文本格式）
    if _INLINE_PATTERN.search(content):
        return "inline"
    if NicokaraParser.is_nicokara_format(content):
        return "nicokara"
    if _ASS_PATTERN.search(content):
        return "ass"
    if _SRT_PATTERN.search(content):
        return "srt"
    if _LRC_PATTERN.search(content):
        return "lrc"
    return "text"


def parse_lyric_content(
    content: str,
    default_singer_id: str,
    project_singers: Optional[List[Singer]] = None,
    software_compensation_ms: int = 0,
) -> Tuple[List[Sentence], bool, List[Singer]]:
    """解析歌词内容，返回解析后的句子列表。

    Args:
        content: 歌词文本内容
        default_singer_id: 默认演唱者 ID
        project_singers: 当前项目的演唱者列表（用于 Nicokara 格式的演唱者匹配）
        software_compensation_ms: 软件导出补偿（毫秒），导入时减去此值

    Returns:
        (sentences, is_nicokara, new_singers):
        - sentences: 解析后的句子列表
        - is_nicokara: 是否为 Nicokara 格式
        - new_singers: Nicokara 格式中需要新增的演唱者列表

    Raises:
        ValueError: 当内容是 SUG 项目文件格式时（由调用方处理为项目加载）
    """

    def _apply_compensation(sentences: List[Sentence]) -> List[Sentence]:
        """应用导入补偿（减去软件导出补偿）"""
        if software_compensation_ms == 0:
            return sentences
        for sentence in sentences:
            for ch in sentence.characters:
                if ch.timestamps:
                    ch.timestamps = [
                        max(0, ts - software_compensation_ms)
                        for ts in ch.timestamps
                    ]
                if ch.sentence_end_ts is not None:
                    ch.sentence_end_ts = max(
                        0, ch.sentence_end_ts - software_compensation_ms
                    )
        return sentences

    fmt = detect_lyric_format(content)
    is_nicokara = False
    new_singers: List[Singer] = []

    # SUG 项目文件格式：抛出异常，由调用方处理为项目加载
    if fmt == "sug":
        raise ValueError("__SUG_PROJECT__")

    # 内联格式（包括 inline 和纯 RLF 文本格式）
    if fmt == "inline":
        sentences = sentences_from_inline_text(content, default_singer_id)
        return _apply_compensation(sentences), False, []

    # Nicokara 格式
    if fmt == "nicokara":
        is_nicokara = True
        parser = NicokaraParser()
        result = parser.parse(content)

        # 为 singer_key 建立映射
        singer_key_to_id: dict = {}
        singer_colors = [
            "#FF6B6B", "#4ECDC4", "#45B7D1", "#FFA07A", "#98D8C8",
            "#C9B1FF", "#F7DC6F", "#82E0AA", "#F1948A", "#85C1E9",
        ]

        # 收集所有 singer_key
        all_singer_keys: set = set()
        for singer_key in result.singer_definitions:
            all_singer_keys.add(singer_key)
        for line in result.lines:
            if line.line_singer_key:
                all_singer_keys.add(line.line_singer_key)
            for _, sk in line.char_singer_map.items():
                all_singer_keys.add(sk)

        # 匹配已有演唱者或创建新的
        for idx, singer_key in enumerate(sorted(all_singer_keys)):
            display_name = (
                result.singer_definitions.get(singer_key, singer_key) or singer_key
            )
            # 先查找已有演唱者
            existing_id = None
            if project_singers:
                for s in project_singers:
                    if s.name == display_name:
                        existing_id = s.id
                        break
            if existing_id:
                singer_key_to_id[singer_key] = existing_id
            else:
                color = singer_colors[idx % len(singer_colors)]
                new_singer = Singer(name=display_name, color=color, is_default=False)
                singer_key_to_id[singer_key] = new_singer.id
                new_singers.append(new_singer)

        # 使用 nicokara_result_to_sentences 保留原有注音和时间戳
        sentences = nicokara_result_to_sentences(
            result, singer_key_to_id, default_singer_id
        )

        # SHINTA 2025 规格透明性 (差异 K)：把解析到的 @ 元数据写回 AppSettings.nicokara_tags，
        # 覆盖式（每次导入一个 Nicokara 文件即代表用户切换到新项目）。
        # 已知键 (@Title/@Artist/@Album/@TaggingBy/@SilencemSec) 落到对应字段；
        # 其余未知 @ 标签原样收集到 tags["custom"]，导出器 round-trip 时按行回写。
        _sync_nicokara_metadata_to_settings(result.metadata)

        return _apply_compensation(sentences), is_nicokara, new_singers

    # ASS 格式
    if fmt == "ass":
        from strange_uta_game.backend.infrastructure.parsers.ass_parser import (
            ASSParser,
        )

        parser = ASSParser()
        parsed_lines = parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return _apply_compensation(sentences), False, []

    # SRT 格式
    if fmt == "srt":
        from strange_uta_game.backend.infrastructure.parsers.srt_parser import (
            SRTParser,
        )

        parser = SRTParser()
        parsed_lines = parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return _apply_compensation(sentences), False, []

    # LRC 格式
    if fmt == "lrc":
        lrc_parser = LRCParser()
        parsed_lines = lrc_parser.parse(content)
        sentences = parse_to_sentences(parsed_lines, default_singer_id)
        return _apply_compensation(sentences), False, []

    # 纯文本：按行分割，保留空行作为空 Sentence（维持用户排版）。
    # 仅丢弃文件末尾换行符产生的终止空段，避免无谓追加空行。
    from strange_uta_game.backend.domain import Character

    raw_lines = content.split("\n")
    if len(raw_lines) > 1 and raw_lines[-1] == "" and content.endswith("\n"):
        raw_lines.pop()

    sentences = []
    for raw_line in raw_lines:
        line_text = raw_line.strip()
        if not line_text:
            sentences.append(Sentence(singer_id=default_singer_id, characters=[]))
            continue
        sentence = Sentence(
            singer_id=default_singer_id,
            characters=[Character(char=c) for c in line_text],
        )
        sentences.append(sentence)

    return _apply_compensation(sentences), False, []


def read_lyric_file(path: str) -> Optional[str]:
    """读取歌词文件内容。

    Returns:
        文件内容，读取失败返回 None
    """
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception:
        return None
