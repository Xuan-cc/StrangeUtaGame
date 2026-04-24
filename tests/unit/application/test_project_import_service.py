"""ProjectImportService 单元测试。

覆盖：
- 内联格式（``[n|HH:MM:SS]``）
- LRC 格式（``.lrc`` 通过工厂）
- 带 LRC 时间标签的 ``.txt`` 文件（应强制 LRC 解析）
- 不存在文件 / 空文件
- 所有 Sentence 均归属到传入的 default_singer_id
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strange_uta_game.backend.application import (
    ProjectImportError,
    ProjectImportService,
)


SINGER_ID = "singer-default"


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return str(p)


class TestLoadLyricsFromFile:
    def test_lrc_file(self, tmp_path: Path):
        content = "[00:00.00]赤い花\n[00:05.00]白い雲\n"
        path = _write(tmp_path, "song.lrc", content)

        sentences = ProjectImportService.load_lyrics_from_file(path, SINGER_ID)

        assert len(sentences) == 2
        assert sentences[0].text == "赤い花"
        assert sentences[1].text == "白い雲"
        assert all(s.singer_id == SINGER_ID for s in sentences)

    def test_txt_with_lrc_tags_forces_lrc_parser(self, tmp_path: Path):
        # 即使扩展名是 .txt，含 [MM:SS.xx] 标签也应走 LRC
        content = "[00:01.00]あ\n[00:02.00]い\n"
        path = _write(tmp_path, "song.txt", content)

        sentences = ProjectImportService.load_lyrics_from_file(path, SINGER_ID)

        assert len(sentences) == 2
        assert sentences[0].text == "あ"
        assert sentences[1].text == "い"

    def test_inline_format_detected(self, tmp_path: Path):
        # 内联格式：[<cp_idx>|HH:MM:SS]字符
        # 取自 sentences_from_inline_text 支持的最小形态
        content = "[0|00:00:00]あ[1|00:00:05]い\n"
        path = _write(tmp_path, "inline.txt", content)

        sentences = ProjectImportService.load_lyrics_from_file(path, SINGER_ID)

        assert len(sentences) >= 1
        # 所有 sentence 归属默认演唱者
        assert all(s.singer_id == SINGER_ID for s in sentences)

    def test_missing_file_raises_project_import_error(self, tmp_path: Path):
        missing = str(tmp_path / "nope.lrc")
        with pytest.raises(ProjectImportError):
            ProjectImportService.load_lyrics_from_file(missing, SINGER_ID)

    def test_empty_file_returns_empty_list(self, tmp_path: Path):
        path = _write(tmp_path, "empty.lrc", "")
        sentences = ProjectImportService.load_lyrics_from_file(path, SINGER_ID)
        assert sentences == []
