"""SUG 项目文件解析器

StrangeUtaGame 项目文件格式 (.sug)
基于 JSON，包含完整的项目数据。

版本历史:
  - v1.0: 初始版本（lines + checkpoints + timetags + rubies 分离存储）
  - v2.0: 层次化模型（sentences + characters 一体化存储）
"""

import json
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime
from uuid import uuid4

from strange_uta_game.backend.domain import (
    Project,
    ProjectMetadata,
    Singer,
    Sentence,
    Character,
    Ruby,
    DomainError,
)


class SugParseError(Exception):
    """SUG 文件解析错误"""

    pass


def _split_ruby_text(ruby_text: str, char_count: int) -> List[str]:
    """将 ruby 文本拆分到多个字符（用于 v1.0 迁移）

    按字符数均匀分配。字符数 <= 目标数时一对一分配，
    多余位置为空字符串；字符数 > 目标数时均匀合并。

    Args:
        ruby_text: 注音文本
        char_count: 需要分配到的字符数量

    Returns:
        拆分后的文本列表，每个元素对应一个字符
    """
    if char_count <= 0:
        return [ruby_text] if ruby_text else []
    if char_count == 1:
        return [ruby_text]

    chars = list(ruby_text)
    if len(chars) <= char_count:
        return chars + [""] * (char_count - len(chars))

    # 字符数 > 目标数：均匀合并
    result = []
    base = len(chars) // char_count
    extra = len(chars) % char_count
    pos = 0
    for i in range(char_count):
        size = base + (1 if i < extra else 0)
        result.append("".join(chars[pos : pos + size]))
        pos += size
    return result


class SugMigrator:
    """SUG 文件版本迁移器

    处理不同版本之间的数据迁移。
    """

    CURRENT_VERSION = "2.0"

    @classmethod
    def migrate(cls, data: Dict[str, Any], from_version: str) -> Dict[str, Any]:
        """将旧版本数据迁移到最新版本

        Args:
            data: 旧版本数据
            from_version: 原版本号

        Returns:
            迁移后的数据（v2.0 格式字典）
        """
        if from_version == cls.CURRENT_VERSION:
            return data

        if from_version == "1.0":
            return cls._migrate_v1_to_v2(data)

        # 未知版本，原样返回（由解析器尝试处理）
        return data

    @classmethod
    def _migrate_v1_to_v2(cls, data: Dict[str, Any]) -> Dict[str, Any]:
        """v1.0 → v2.0 迁移

        将旧的 lines/checkpoints/timetags/rubies 分离存储格式
        转换为新的 sentences/characters 层次化存储格式。
        """
        result = {
            "version": "2.0",
            "id": data.get("id", ""),
            "metadata": data.get("metadata", {}),
            "audio_duration_ms": data.get("audio_duration_ms", 0),
            "singers": data.get("singers", []),
            "sentences": [],
        }

        for line_data in data.get("lines", []):
            sentence_data = cls._migrate_line_to_sentence(line_data)
            result["sentences"].append(sentence_data)

        return result

    @classmethod
    def _migrate_line_to_sentence(cls, line_data: Dict[str, Any]) -> Dict[str, Any]:
        """将 v1.0 的 line dict 转换为 v2.0 的 sentence dict

        转换规则:
          1. chars + checkpoints → characters（合并属性）
          2. timetags 按 char_idx 分组 → character.timestamps[]
          3. rubies 按 start_idx/end_idx 拆分到各 character
          4. check_count==0 → 前一字符 linked_to_next=True
        """
        line_singer_id = line_data.get("singer_id", "")
        text = line_data.get("text", "")
        chars = line_data.get("chars", list(text) if text else [])

        # 修复 chars/text 不一致
        if text and "".join(chars) != text:
            chars = list(text)

        # ── 1. 构建 checkpoint 映射: char_idx → checkpoint dict ──
        cp_map: Dict[int, Dict[str, Any]] = {}
        for cp in line_data.get("checkpoints", []):
            idx = int(cp.get("char_idx", 0))
            cp_map[idx] = dict(cp)  # 浅拷贝，避免修改原数据

        # ── 2. linked_to_next 迁移: check_count==0 → 前一字符 linked_to_next ──
        # 空格字符不应触发连词（空格 check_count==0 是过滤规则的结果，不代表连读）
        sorted_indices = sorted(cp_map.keys())
        for i, idx in enumerate(sorted_indices):
            cp = cp_map[idx]
            ch_char = chars[idx] if idx < len(chars) else ""
            if ch_char and ch_char.isspace():
                continue
            if int(cp.get("check_count", 1)) == 0 and i > 0:
                prev_idx = sorted_indices[i - 1]
                cp_map[prev_idx]["linked_to_next"] = True

        # ── 3. 构建 timetag 映射: char_idx → {cp_idx: timestamp_ms} ──
        tag_map: Dict[int, Dict[int, int]] = {}
        for tag in line_data.get("timetags", []):
            char_idx = int(tag.get("char_idx", 0))
            cp_idx = int(tag.get("checkpoint_idx", 0))
            ts = int(tag.get("timestamp_ms", 0))
            if char_idx not in tag_map:
                tag_map[char_idx] = {}
            tag_map[char_idx][cp_idx] = ts

        # ── 4. 构建 ruby 映射: char_idx → ruby_text ──
        ruby_map: Dict[int, str] = {}
        for ruby_data in line_data.get("rubies", []):
            start = int(ruby_data.get("start_idx", 0))
            end = int(ruby_data.get("end_idx", 1))
            ruby_text = ruby_data.get("text", "")
            if not ruby_text:
                continue
            span = end - start
            if span <= 0:
                continue
            if span == 1:
                ruby_map[start] = ruby_text
            else:
                parts = _split_ruby_text(ruby_text, span)
                for offset, part in enumerate(parts):
                    if part:
                        ruby_map[start + offset] = part

        # ── 5. 组装 characters ──
        characters: List[Dict[str, Any]] = []
        for i, ch in enumerate(chars):
            cp = cp_map.get(i, {})
            check_count = max(int(cp.get("check_count", 1)), 0)

            # 从 timetag map 构建 timestamps 列表（按 cp_idx 顺序）
            timestamps: List[int] = []
            if i in tag_map:
                tag_indices = sorted(tag_map[i].keys())
                if tag_indices:
                    max_idx = tag_indices[-1]
                    for cp_idx in range(max_idx + 1):
                        timestamps.append(tag_map[i].get(cp_idx, 0))

            char_dict: Dict[str, Any] = {
                "char": ch,
                "check_count": check_count,
                "timestamps": timestamps,
                "linked_to_next": bool(cp.get("linked_to_next", False)),
                "is_line_end": bool(cp.get("is_line_end", False)),
                "is_rest": bool(cp.get("is_rest", False)),
                "singer_id": cp.get("singer_id", "") or line_singer_id,
            }

            if i in ruby_map:
                char_dict["ruby"] = {"text": ruby_map[i]}

            characters.append(char_dict)

        return {
            "id": line_data.get("id") or str(uuid4()),
            "singer_id": line_singer_id,
            "characters": characters,
        }


class SugProjectParser:
    """SUG 项目文件解析器

    负责 Project 对象的序列化和反序列化。
    支持 v2.0 格式读写，以及 v1.0 格式向上兼容读取。
    """

    @staticmethod
    def save(project: Project, file_path: str) -> None:
        """保存项目到 SUG 文件

        Args:
            project: 项目对象
            file_path: 保存路径

        Raises:
            SugParseError: 保存失败
        """
        try:
            data = SugProjectParser._project_to_dict(project)

            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            raise SugParseError(f"保存项目失败: {e}")

    @staticmethod
    def load(file_path: str) -> Project:
        """从 SUG 文件加载项目

        支持 v1.0 和 v2.0 格式。v1.0 文件自动迁移为 v2.0 模型。

        Args:
            file_path: 文件路径

        Returns:
            项目对象

        Raises:
            SugParseError: 加载失败或文件损坏
        """
        path = Path(file_path)

        if not path.exists():
            raise SugParseError(f"文件不存在: {file_path}")

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

        except json.JSONDecodeError as e:
            raise SugParseError(f"JSON 解析错误: {e}")
        except Exception as e:
            raise SugParseError(f"读取文件失败: {e}")

        # 版本检查和迁移
        version = data.get("version", "1.0")
        if version != SugMigrator.CURRENT_VERSION:
            data = SugMigrator.migrate(data, version)

        try:
            return SugProjectParser._dict_to_project(data)
        except (ValueError, KeyError, TypeError, DomainError) as e:
            raise SugParseError(f"项目数据解析失败: {e}") from e

    # ==================== 序列化 (Project → Dict) ====================

    @staticmethod
    def _project_to_dict(project: Project) -> Dict[str, Any]:
        """将 Project 对象转换为 v2.0 字典"""
        return {
            "version": SugMigrator.CURRENT_VERSION,
            "id": project.id,
            "metadata": {
                "title": project.metadata.title,
                "artist": project.metadata.artist,
                "album": project.metadata.album,
                "language": project.metadata.language,
                "created_at": project.metadata.created_at.isoformat(),
                "updated_at": project.metadata.updated_at.isoformat(),
            },
            "audio_duration_ms": project.audio_duration_ms,
            "singers": [
                {
                    "id": s.id,
                    "name": s.name,
                    "color": s.color,
                    "is_default": s.is_default,
                    "display_priority": s.display_priority,
                    "enabled": s.enabled,
                    "backend_number": s.backend_number,
                }
                for s in project.singers
            ],
            "sentences": [
                SugProjectParser._sentence_to_dict(s) for s in project.sentences
            ],
        }

    @staticmethod
    def _sentence_to_dict(sentence: Sentence) -> Dict[str, Any]:
        """将 Sentence 对象转换为字典"""
        characters = []
        for char in sentence.characters:
            char_dict: Dict[str, Any] = {
                "char": char.char,
                "check_count": char.check_count,
                "timestamps": list(char.timestamps),
                "linked_to_next": char.linked_to_next,
                "is_line_end": char.is_line_end,
                "is_rest": char.is_rest,
                "singer_id": char.singer_id,
            }
            if char.ruby:
                char_dict["ruby"] = {"text": char.ruby.text}
            characters.append(char_dict)

        return {
            "id": sentence.id,
            "singer_id": sentence.singer_id,
            "characters": characters,
        }

    # ==================== 反序列化 (Dict → Project) ====================

    @staticmethod
    def _dict_to_project(data: Dict[str, Any]) -> Project:
        """将 v2.0 字典转换为 Project 对象"""
        # 解析元数据（安全 datetime 解析）
        metadata_data = data.get("metadata", {})

        def _safe_datetime(value: Optional[str]) -> datetime:
            if value:
                try:
                    return datetime.fromisoformat(value)
                except (ValueError, TypeError):
                    pass
            return datetime.now()

        metadata = ProjectMetadata(
            title=metadata_data.get("title", ""),
            artist=metadata_data.get("artist", ""),
            album=metadata_data.get("album", ""),
            language=metadata_data.get("language", "ja"),
            created_at=_safe_datetime(metadata_data.get("created_at")),
            updated_at=_safe_datetime(metadata_data.get("updated_at")),
        )

        # 解析演唱者
        singers = []
        for singer_data in data.get("singers", []):
            singer = Singer(
                id=singer_data.get("id") or str(uuid4()),
                name=singer_data.get("name", "未命名"),
                color=singer_data.get("color", "#FF6B6B"),
                is_default=singer_data.get("is_default", False),
                display_priority=int(singer_data.get("display_priority", 0)),
                enabled=singer_data.get("enabled", True),
                backend_number=int(singer_data.get("backend_number", 0)),
            )
            singers.append(singer)

        # 解析句子
        sentences = []
        for sentence_data in data.get("sentences", []):
            sentence = SugProjectParser._dict_to_sentence(sentence_data)
            sentences.append(sentence)

        # 创建项目
        project = Project(
            id=data.get("id") or str(uuid4()),
            singers=singers,
            sentences=sentences,
            metadata=metadata,
            audio_duration_ms=int(data.get("audio_duration_ms", 0)),
        )

        return project

    @staticmethod
    def _dict_to_sentence(data: Dict[str, Any]) -> Sentence:
        """将字典转换为 Sentence 对象"""
        singer_id = data.get("singer_id", "")

        characters = []
        for char_data in data.get("characters", []):
            # 解析 Ruby
            ruby = None
            ruby_data = char_data.get("ruby")
            if ruby_data and ruby_data.get("text"):
                ruby = Ruby(text=ruby_data["text"])

            char = Character(
                char=char_data.get("char", "?"),
                ruby=ruby,
                check_count=int(char_data.get("check_count", 1)),
                timestamps=[int(ts) for ts in char_data.get("timestamps", [])],
                linked_to_next=bool(char_data.get("linked_to_next", False)),
                is_line_end=bool(char_data.get("is_line_end", False)),
                is_rest=bool(char_data.get("is_rest", False)),
                singer_id=char_data.get("singer_id", "") or singer_id,
            )
            # 推送时间戳和演唱者到 Ruby
            char.push_to_ruby()
            characters.append(char)

        # 确保 singer_id 有效（回退到第一个字符的 singer_id）
        effective_singer_id = singer_id
        if not effective_singer_id and characters:
            effective_singer_id = characters[0].singer_id

        sentence = Sentence(
            id=data.get("id") or str(uuid4()),
            singer_id=effective_singer_id,
            characters=characters,
        )

        return sentence
