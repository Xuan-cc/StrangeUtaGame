"""txt2ass 格式导出器。

txt2ass 是一种用于生成 ASS 字幕的歌词格式。
可以配合 txt2ass 工具生成 ASS 字幕文件。

格式：
[time]text

示例：
[00:12.34]歌词文本
"""

from typing import List
from .base import BaseExporter, ExportError
from strange_uta_game.backend.domain import Project, Sentence


class Txt2AssExporter(BaseExporter):
    """txt2ass 格式导出器

    导出 txt2ass 格式，用于生成 ASS 字幕。
    格式简单，每行包含时间戳和歌词文本。
    """

    @property
    def name(self) -> str:
        return "txt2ass"

    @property
    def description(self) -> str:
        return "用于生成 ASS 字幕的格式"

    @property
    def file_extension(self) -> str:
        return ".txt"

    @property
    def file_filter(self) -> str:
        return "txt2ass 文件 (*.txt)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 txt2ass 格式"""
        self._validate_project(project)
        # txt2ass 通常使用 .txt 扩展名，但我们不强制修改

        lines = []

        # 标题信息（注释）
        if project.metadata:
            if project.metadata.title:
                lines.append(f"# Title: {project.metadata.title}")
            if project.metadata.artist:
                lines.append(f"# Artist: {project.metadata.artist}")

        lines.append("# Format: [mm:ss.xx]Lyrics")
        lines.append("")

        # 导出行
        for sentence in project.sentences:
            line_text = self._export_sentence(sentence)
            if line_text:
                lines.append(line_text)

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _export_sentence(self, sentence: Sentence) -> str:
        """导出一行歌词"""
        if not sentence.has_timetags:
            # 没有时间标签，使用占位符
            return f"[00:00.00]{sentence.text}"

        # 使用最早的时间标签
        start_ms = sentence.timing_start_ms
        time_str = self._format_timestamp(start_ms, "lrc")

        return f"{time_str}{sentence.text}"


class ASSDirectExporter(BaseExporter):
    """ASS 字幕直接导出器

    直接生成 ASS 字幕格式，无需外部工具。
    """

    @property
    def name(self) -> str:
        return "ASS"

    @property
    def description(self) -> str:
        return "ASS 字幕格式（Advanced SubStation Alpha）"

    @property
    def file_extension(self) -> str:
        return ".ass"

    @property
    def file_filter(self) -> str:
        return "ASS 字幕文件 (*.ass)"

    def export(self, project: Project, file_path: str) -> None:
        """导出为 ASS 格式"""
        self._validate_project(project)
        file_path = self._ensure_extension(file_path)

        lines = []

        # ASS 文件头
        lines.extend(self._generate_header(project))
        lines.append("")

        # Styles
        lines.extend(self._generate_styles())
        lines.append("")

        # Events
        lines.extend(self._generate_events(project))

        # 写入文件
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        except Exception as e:
            raise ExportError(f"写入文件失败: {e}")

    def _generate_header(self, project: Project) -> List[str]:
        """生成文件头"""
        title = project.metadata.title if project.metadata else "Untitled"

        return [
            "[Script Info]",
            f"Title: {title}",
            "ScriptType: v4.00+",
            "Collisions: Normal",
            "PlayDepth: 0",
            "Timer: 100.0000",
            "WrapStyle: 0",
            "ScaledBorderAndShadow: yes",
            "YCbCr Matrix: TV.601",
        ]

    def _generate_styles(self) -> List[str]:
        """生成样式定义"""
        return [
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1",
            "Style: Karaoke,Arial,24,&H00FF6B6B,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,10,10,30,1",
        ]

    def _generate_events(self, project: Project) -> List[str]:
        """生成事件（字幕行）"""
        lines = [
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        sentences = project.sentences
        for i, sentence in enumerate(sentences):
            if not sentence.has_timetags:
                continue

            # 获取时间范围
            start_time = sentence.timing_start_ms

            # 结束时间：下一行的开始时间，或当前行 + 5 秒
            if i + 1 < len(sentences):
                next_sentence = sentences[i + 1]
                if next_sentence.has_timetags:
                    end_time = next_sentence.timing_start_ms
                else:
                    end_time = start_time + 5000
            else:
                end_time = start_time + 5000

            start_str = self._format_timestamp(start_time, "ass")
            end_str = self._format_timestamp(end_time, "ass")

            # 生成卡拉OK效果文本
            text = self._generate_karaoke_text(sentence)

            event_line = f"Dialogue: 0,{start_str},{end_str},Karaoke,,0,0,0,,{text}"
            lines.append(event_line)

        return lines

    def _generate_karaoke_text(self, sentence: Sentence) -> str:
        """生成带卡拉OK效果的文本"""
        if not sentence.has_timetags or not sentence.characters:
            return sentence.text

        # 收集所有 (timestamp_ms, char) 并按时间排序
        all_tags: List[tuple[int, str]] = []
        for ch in sentence.characters:
            for ts in ch.timestamps:
                all_tags.append((ts, ch.char))

        if not all_tags:
            return sentence.text

        all_tags.sort(key=lambda t: t[0])

        result = []

        for i, (ts, char) in enumerate(all_tags):
            # 计算持续时间
            if i + 1 < len(all_tags):
                duration_ms = all_tags[i + 1][0] - ts
            else:
                duration_ms = 500  # 默认 500ms

            duration_cs = max(1, duration_ms // 10)  # ASS 使用 centiseconds

            # ASS 卡拉OK标签: {\k<duration>}<char>
            result.append(f"{{\\k{duration_cs}}}{char}")

        return "".join(result)
