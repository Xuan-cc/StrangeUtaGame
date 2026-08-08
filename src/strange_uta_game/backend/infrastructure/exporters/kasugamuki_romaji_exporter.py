"""Kirakara 注音格式导出器。

Kirakara 沿用春日向注音语法，并可选择是否附加罗马音层。它还支持
按演唱者过滤，以及在演唱者切换处插入 ``【@演唱者名】`` 标签。
"""

from copy import deepcopy
from typing import Dict, List, Optional, Set, Tuple

from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    sentences_to_kasugamuki,
    sentences_to_kasugamuki_romaji,
)
from .base import BaseExporter, ExportError


class KirakaraExporter(BaseExporter):
    """导出 Kirakara ``.krl`` 文件。"""

    @property
    def name(self) -> str:
        return "Kirakara"

    @property
    def description(self) -> str:
        return "Kirakara 注音格式（可选罗马音及演唱者标签）"

    @property
    def file_extension(self) -> str:
        return ".krl"

    @property
    def file_filter(self) -> str:
        return "Kirakara 注音文件 (*.krl)"

    @staticmethod
    def _default_singer_id(project: Project) -> Optional[str]:
        for singer in project.singers:
            if singer.is_default:
                return singer.id
        return project.singers[0].id if project.singers else None

    @staticmethod
    def _normalize_singer_id(
        singer_id: Optional[str],
        default_singer_id: Optional[str],
        known_singer_ids: Set[str],
    ) -> Optional[str]:
        if not singer_id or singer_id in ("?", "未知"):
            return default_singer_id
        if singer_id not in known_singer_ids:
            return default_singer_id
        return singer_id

    def _effective_singer_id(
        self,
        sentence: Sentence,
        char: Character,
        default_singer_id: Optional[str],
        known_singer_ids: Set[str],
    ) -> Optional[str]:
        return self._normalize_singer_id(
            char.singer_id or sentence.singer_id,
            default_singer_id,
            known_singer_ids,
        )

    def _singer_block_has_content(
        self,
        sentence: Sentence,
        start_idx: int,
        singer_id: Optional[str],
        singer_ids: Optional[Set[str]],
        default_singer_id: Optional[str],
        known_singer_ids: Set[str],
    ) -> bool:
        for char in sentence.characters[start_idx:]:
            effective = self._effective_singer_id(
                sentence, char, default_singer_id, known_singer_ids
            )
            if effective != singer_id:
                break
            if singer_ids is not None and effective not in singer_ids:
                continue
            if char.char.strip():
                return True
        return False

    def _prepare_sentences(
        self,
        project: Project,
        singer_ids: Optional[Set[str]],
        insert_singer_tags: bool,
        insert_singer_each_line: bool,
        singer_map: Optional[Dict[str, str]],
    ) -> List[Sentence]:
        """返回仅用于导出的副本，不修改打开中的项目。"""
        result: List[Sentence] = []
        previous_singer_id: Optional[str] = None
        default_singer_id = self._default_singer_id(project)
        known_singer_ids = {singer.id for singer in project.singers}

        for source in project.sentences:
            is_blank_line = not source.text.strip()
            effective_previous = None if insert_singer_each_line else previous_singer_id
            output_chars: List[Character] = []
            # (复制后的字符, 原始索引)，用来只保留仍然相邻的连词关系。
            copied_originals: List[Tuple[Character, int]] = []

            for index, source_char in enumerate(source.characters):
                effective_singer = self._effective_singer_id(
                    source, source_char, default_singer_id, known_singer_ids
                )
                if singer_ids is not None and effective_singer not in singer_ids:
                    continue

                needs_tag = bool(
                    insert_singer_tags
                    and singer_map
                    and (
                        effective_singer != effective_previous
                        or source_char.force_singer_tag
                    )
                )
                if needs_tag and not source_char.force_singer_tag:
                    needs_tag = self._singer_block_has_content(
                        source,
                        index,
                        effective_singer,
                        singer_ids,
                        default_singer_id,
                        known_singer_ids,
                    )

                if needs_tag:
                    singer_name = singer_map.get(effective_singer, "") if effective_singer else ""
                    if singer_name:
                        # 标签自身没有时间戳、注音和连词关系。
                        output_chars.append(
                            Character(
                                char=f"【@{singer_name}】",
                                check_count=0,
                                singer_id=effective_singer or source.singer_id,
                            )
                        )

                copied = deepcopy(source_char)
                copied.linked_to_next = False
                # 仅当两个原字符仍相邻且中间没有插入标签时恢复连词。
                if copied_originals:
                    previous_copy, previous_index = copied_originals[-1]
                    if (
                        not needs_tag
                        and previous_index + 1 == index
                        and source.characters[previous_index].linked_to_next
                    ):
                        previous_copy.linked_to_next = True
                output_chars.append(copied)
                copied_originals.append((copied, index))
                effective_previous = effective_singer
                previous_singer_id = effective_singer

            if output_chars:
                exported = deepcopy(source)
                exported.characters = output_chars
                result.append(exported)
            elif is_blank_line:
                # 与 Nicokara 一致：用户保留的空行不受演唱者过滤影响。
                result.append(deepcopy(source))

        return result

    def export(
        self,
        project: Project,
        file_path: str,
        singer_ids: Optional[Set[str]] = None,
        insert_singer_tags: bool = False,
        insert_singer_each_line: bool = False,
        singer_map: Optional[Dict[str, str]] = None,
        export_romaji: bool = True,
    ) -> None:
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)
        try:
            sentences = self._prepare_sentences(
                project,
                singer_ids,
                insert_singer_tags,
                insert_singer_each_line,
                singer_map,
            )
            if export_romaji:
                content = sentences_to_kasugamuki_romaji(sentences)
            else:
                content = sentences_to_kasugamuki(sentences)
            with open(file_path, "w", encoding="utf-8") as file:
                file.write(content)
        except Exception as exc:
            raise ExportError(f"导出 Kirakara 格式失败: {exc}") from exc


# 保留旧类名，避免第三方代码的 import 在格式升级后立即失效。
KasugamukiRomajiExporter = KirakaraExporter
