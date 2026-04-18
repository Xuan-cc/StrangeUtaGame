"""SUG 项目文件解析器

StrangeUtaGame 项目文件格式 (.sug)
基于 JSON，包含完整的项目数据。
"""

import json
from dataclasses import dataclass, asdict
from typing import Dict, Any, List, Optional
from pathlib import Path
from datetime import datetime

from strange_uta_game.backend.domain import (
    Project,
    ProjectMetadata,
    Singer,
    LyricLine,
    TimeTag,
    TimeTagType,
    Ruby,
    CheckpointConfig,
)


class SugParseError(Exception):
    """SUG 文件解析错误"""

    pass


class SugMigrator:
    """SUG 文件版本迁移器

    处理不同版本之间的数据迁移。
    """

    CURRENT_VERSION = "1.0"

    @classmethod
    def migrate(cls, data: Dict[str, Any], from_version: str) -> Dict[str, Any]:
        """将旧版本数据迁移到最新版本

        Args:
            data: 旧版本数据
            from_version: 原版本号

        Returns:
            迁移后的数据
        """
        if from_version == cls.CURRENT_VERSION:
            return data

        # 版本 1.0 是基础版本，无需迁移
        # 未来版本添加迁移逻辑

        return data


class SugProjectParser:
    """SUG 项目文件解析器

    负责 Project 对象的序列化和反序列化。
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
        except (ValueError, KeyError, TypeError) as e:
            raise SugParseError(f"项目数据解析失败: {e}") from e

    @staticmethod
    def _project_to_dict(project: Project) -> Dict[str, Any]:
        """将 Project 对象转换为字典"""
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
                }
                for s in project.singers
            ],
            "lines": [
                SugProjectParser._lyric_line_to_dict(line) for line in project.lines
            ],
        }

    @staticmethod
    def _lyric_line_to_dict(line: LyricLine) -> Dict[str, Any]:
        """将 LyricLine 对象转换为字典"""
        return {
            "id": line.id,
            "singer_id": line.singer_id,
            "text": line.text,
            "chars": line.chars,
            "checkpoints": [
                {
                    "char_idx": c.char_idx,
                    "check_count": c.check_count,
                    "is_line_end": c.is_line_end,
                    "is_rest": c.is_rest,
                    "linked_to_next": c.linked_to_next,
                }
                for c in line.checkpoints
            ],
            "timetags": [
                {
                    "timestamp_ms": t.timestamp_ms,
                    "singer_id": t.singer_id,
                    "char_idx": t.char_idx,
                    "checkpoint_idx": t.checkpoint_idx,
                    "tag_type": t.tag_type.name,
                }
                for t in line.timetags
            ],
            "rubies": [
                {
                    "text": r.text,
                    "start_idx": r.start_idx,
                    "end_idx": r.end_idx,
                }
                for r in line.rubies
            ],
        }

    @staticmethod
    def _dict_to_project(data: Dict[str, Any]) -> Project:
        """将字典转换为 Project 对象"""
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
                id=singer_data.get("id", str(__import__("uuid").uuid4())),
                name=singer_data.get("name", "未命名"),
                color=singer_data.get("color", "#FF6B6B"),
                is_default=singer_data.get("is_default", False),
                display_priority=int(singer_data.get("display_priority", 0)),
                enabled=singer_data.get("enabled", True),
            )
            singers.append(singer)

        # 解析歌词行
        lines = []
        for line_data in data.get("lines", []):
            line = SugProjectParser._dict_to_lyric_line(line_data)
            lines.append(line)

        # 创建项目
        project = Project(
            id=data.get("id") or str(__import__("uuid").uuid4()),
            singers=singers,
            lines=lines,
            metadata=metadata,
            audio_duration_ms=int(data.get("audio_duration_ms", 0)),
        )

        return project

    @staticmethod
    def _dict_to_lyric_line(data: Dict[str, Any]) -> LyricLine:
        """将字典转换为 LyricLine 对象"""
        # 解析时间标签
        timetags = []
        for tag_data in data.get("timetags", []):
            try:
                tag_type = TimeTagType[tag_data.get("tag_type", "CHAR_START")]
            except KeyError:
                tag_type = TimeTagType.CHAR_START

            tag = TimeTag(
                timestamp_ms=int(tag_data.get("timestamp_ms", 0)),
                singer_id=tag_data.get("singer_id", ""),
                char_idx=int(tag_data.get("char_idx", 0)),
                checkpoint_idx=int(tag_data.get("checkpoint_idx", 0)),
                tag_type=tag_type,
            )
            timetags.append(tag)

        # 解析节奏点配置
        checkpoints = []
        for cp_data in data.get("checkpoints", []):
            cp = CheckpointConfig(
                char_idx=int(cp_data.get("char_idx", 0)),
                check_count=int(cp_data.get("check_count", 1)),
                is_line_end=cp_data.get("is_line_end", False),
                is_rest=cp_data.get("is_rest", False),
                linked_to_next=cp_data.get("linked_to_next", False),
            )
            checkpoints.append(cp)

        # 旧数据迁移: check_count==0 的字符表示与前一个字符连词
        # 在前一个字符上设置 linked_to_next=True
        for i in range(1, len(checkpoints)):
            if (
                checkpoints[i].check_count == 0
                and not checkpoints[i - 1].linked_to_next
            ):
                prev = checkpoints[i - 1]
                checkpoints[i - 1] = CheckpointConfig(
                    char_idx=prev.char_idx,
                    check_count=prev.check_count,
                    is_line_end=prev.is_line_end,
                    is_rest=prev.is_rest,
                    linked_to_next=True,
                )

        # 解析注音
        rubies = []
        for ruby_data in data.get("rubies", []):
            ruby = Ruby(
                text=ruby_data.get("text", ""),
                start_idx=int(ruby_data.get("start_idx", 0)),
                end_idx=int(ruby_data.get("end_idx", 1)),
            )
            rubies.append(ruby)

        # 安全读取文本和字符列表
        text = data.get("text", "")
        chars = data.get("chars", list(text) if text else [])

        # 修复 chars/text 不一致（可能因手动编辑或旧版本导致）
        if text and "".join(chars) != text:
            chars = list(text)

        # 创建歌词行
        line = LyricLine(
            id=data.get("id", str(__import__("uuid").uuid4())),
            singer_id=data.get("singer_id", ""),
            text=text,
            chars=chars,
            timetags=timetags,
            checkpoints=checkpoints,
            rubies=rubies,
        )

        return line
