"""自动检查服务。

分析歌词文本，计算节奏点数量，生成注音。
"""

from typing import List, Tuple, Optional
from dataclasses import dataclass

from strange_uta_game.backend.domain import (
    Project,
    LyricLine,
    Ruby,
    CheckpointConfig,
)
from strange_uta_game.backend.infrastructure.parsers.text_splitter import (
    split_text,
    SplitConfig,
)
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    create_analyzer,
    RubyAnalyzer,
    RubyResult,
)


@dataclass
class AutoCheckResult:
    """自动检查结果"""

    line_idx: int
    char_idx: int
    char: str
    check_count: int
    ruby: Optional[str]


class AutoCheckService:
    """自动检查服务

    分析歌词文本：
    1. 拆分字符
    2. 分析注音
    3. 计算节奏点数量
    4. 生成 CheckpointConfig
    """

    def __init__(self, ruby_analyzer: RubyAnalyzer = None):
        """
        Args:
            ruby_analyzer: 注音分析器（如果为 None 则自动创建）
        """
        self._analyzer = ruby_analyzer or create_analyzer()

    def analyze_line(
        self, line: LyricLine, split_config: SplitConfig = None
    ) -> List[AutoCheckResult]:
        """分析单行歌词

        Args:
            line: 歌词行
            split_config: 拆分配置

        Returns:
            分析结果列表
        """
        if not line.text:
            return []

        split_config = split_config or SplitConfig()

        # 拆分文本
        chars, check_counts = split_text(line.text, split_config)

        # 分析注音
        ruby_results = self._analyzer.analyze(line.text)

        # 创建字符到注音的映射
        char_to_ruby = {}
        for result in ruby_results:
            for idx in range(result.start_idx, result.end_idx):
                if idx < len(chars):
                    char_to_ruby[idx] = result.reading

        # 构建结果
        results = []
        for i, (char, count) in enumerate(zip(chars, check_counts)):
            results.append(
                AutoCheckResult(
                    line_idx=0,  # 将在 apply_to_line 中设置
                    char_idx=i,
                    char=char,
                    check_count=count,
                    ruby=char_to_ruby.get(i),
                )
            )

        return results

    def apply_to_line(
        self,
        line: LyricLine,
        split_config: SplitConfig = None,
        keep_existing_timetags: bool = True,
    ) -> None:
        """分析并应用自动检查结果到歌词行

        Args:
            line: 歌词行
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
        """
        results = self.analyze_line(line, split_config)

        if not results:
            return

        # 更新 chars
        line.chars = [r.char for r in results]

        # 重建注音
        line.rubies.clear()
        current_ruby = None
        current_start = 0

        for i, result in enumerate(results):
            if result.ruby:
                if current_ruby is None:
                    current_ruby = result.ruby
                    current_start = i
                elif result.ruby != current_ruby:
                    # 注音变化，保存之前的
                    line.add_ruby(
                        Ruby(text=current_ruby, start_idx=current_start, end_idx=i)
                    )
                    current_ruby = result.ruby
                    current_start = i
            else:
                if current_ruby is not None:
                    # 保存之前的注音
                    line.add_ruby(
                        Ruby(text=current_ruby, start_idx=current_start, end_idx=i)
                    )
                    current_ruby = None

        # 处理最后一个注音
        if current_ruby is not None:
            line.add_ruby(
                Ruby(text=current_ruby, start_idx=current_start, end_idx=len(results))
            )

        # 重建 checkpoint 配置
        line.checkpoints = [
            CheckpointConfig(
                char_idx=i,
                check_count=result.check_count,
                is_line_end=(i == len(results) - 1),
            )
            for i, result in enumerate(results)
        ]

        # 如果不保留时间标签，清空
        if not keep_existing_timetags:
            line.timetags.clear()

    def analyze_project(
        self, project: Project, split_config: SplitConfig = None
    ) -> List[Tuple[int, List[AutoCheckResult]]]:
        """分析整个项目

        Args:
            project: 项目
            split_config: 拆分配置

        Returns:
            (行索引, 分析结果) 列表
        """
        results = []

        for i, line in enumerate(project.lines):
            line_results = self.analyze_line(line, split_config)
            # 更新行索引
            for r in line_results:
                r.line_idx = i
            results.append((i, line_results))

        return results

    def apply_to_project(
        self,
        project: Project,
        split_config: SplitConfig = None,
        keep_existing_timetags: bool = True,
    ) -> None:
        """分析并应用到整个项目

        Args:
            project: 项目
            split_config: 拆分配置
            keep_existing_timetags: 是否保留现有时间标签
        """
        for line in project.lines:
            self.apply_to_line(line, split_config, keep_existing_timetags)

    def estimate_check_count(self, text: str) -> int:
        """估算文本的节奏点数量

        Args:
            text: 输入文本

        Returns:
            估算的节奏点数量
        """
        if not text:
            return 0

        try:
            results = self._analyzer.analyze(text)

            count = 0
            for result in results:
                # 汉字：注音假名数量
                if self._is_kanji(result.text[0]):
                    count += len(result.reading)
                # 假名：1 个
                elif self._is_kana(result.text[0]):
                    count += 1

            return count

        except Exception:
            # 如果分析失败，返回字符数作为保守估计
            return len(text)

    @staticmethod
    def _is_kanji(char: str) -> bool:
        """检查是否是汉字"""
        code = ord(char)
        return (
            (0x4E00 <= code <= 0x9FFF)
            or (0x3400 <= code <= 0x4DBF)
            or (0xF900 <= code <= 0xFAFF)
        )

    @staticmethod
    def _is_kana(char: str) -> bool:
        """检查是否是假名"""
        code = ord(char)
        return (0x3040 <= code <= 0x309F) or (0x30A0 <= code <= 0x30FF)
