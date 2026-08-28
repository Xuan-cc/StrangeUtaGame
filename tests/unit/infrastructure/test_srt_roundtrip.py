"""SUG → SRT → SUG roundtrip 一致性测试。

SRT 格式只携带行级 Start/End——逐字时间、注音、演唱者、连词在导出时
即已缺失（可理解的格式损失）。往返后应保留的契约：

1. 每行文本一致；
2. 行起始 ts 落在首字符（= 导出 Start）；
3. 行末字符的句尾释放 ts = 导出 End（下一行 Start，末行 +5s）；
4. 导入 → 再导出，SRT 文本逐字相同（idempotent）。
"""
from __future__ import annotations

import os
import tempfile

from strange_uta_game.backend.domain import Project, Sentence
from strange_uta_game.backend.infrastructure.exporters.srt_exporter import SRTExporter
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    parse_to_sentences,
)
from strange_uta_game.backend.infrastructure.parsers.srt_parser import SRTParser


def _make_project() -> Project:
    project = Project()
    project.metadata.title = "SrtRoundtrip"
    s1 = project.get_default_singer()

    sent = Sentence.from_text("いつか", s1.id)
    sent.characters[0].add_timestamp(1000)
    sent.characters[1].add_timestamp(1500)
    sent.characters[2].add_timestamp(2000)
    sent.characters[2].is_sentence_end = True
    sent.characters[2].sentence_end_ts = 2500
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # ♫ 间奏行（End = 下一行 Start，覆盖非歌词字符的往返）
    sent = Sentence.from_text("♫", s1.id)
    sent.characters[0].add_timestamp(27840)
    sent.characters[0].is_sentence_end = True
    sent.characters[0].sentence_end_ts = 42680
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    sent = Sentence.from_text("見た", s1.id)
    sent.characters[0].add_timestamp(43000)
    sent.characters[1].add_timestamp(43500)
    sent.characters[1].is_sentence_end = True
    sent.characters[1].sentence_end_ts = 44000
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    return project


def _export_srt(project: Project) -> str:
    exporter = SRTExporter()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "r.srt")
        exporter.export(project, path)
        with open(path, "r", encoding="utf-8") as f:
            return f.read()


def _import_srt(content: str) -> Project:
    project = Project()
    parsed_lines = SRTParser().parse(content)
    sentences = parse_to_sentences(parsed_lines, project.get_default_singer().id)
    for s in sentences:
        project.add_sentence(s)
    return project


class TestSRTRoundtrip:
    def test_line_texts_and_bounds_preserved(self):
        """行文本 / 行首 ts / 行末句尾释放 ts 与导出的 Start/End 一致。"""
        project = _make_project()
        srt1 = _export_srt(project)
        p2 = _import_srt(srt1)

        assert [s.text for s in p2.sentences] == ["いつか", "♫", "見た"]

        # 行 1：Start=1000，End=行 2 Start=27840
        line1 = p2.sentences[0]
        assert line1.characters[0].timestamps == [1000]
        assert line1.characters[-1].is_sentence_end is True
        assert line1.characters[-1].sentence_end_ts == 27840

        # 行 2（♫）：Start=27840，End=行 3 Start=43000
        line2 = p2.sentences[1]
        assert line2.characters[0].char == "♫"
        assert line2.characters[0].timestamps == [27840]
        assert line2.characters[-1].sentence_end_ts == 43000

        # 行 3（末行）：End = Start + 5000
        line3 = p2.sentences[2]
        assert line3.characters[0].timestamps == [43000]
        assert line3.characters[-1].sentence_end_ts == 48000

    def test_reexport_is_idempotent(self):
        """导入 → 再导出，SRT 文本逐字相同。"""
        project = _make_project()
        srt1 = _export_srt(project)
        p2 = _import_srt(srt1)
        srt2 = _export_srt(p2)

        assert srt1 == srt2, (
            "SRT 二次导出与第一次不同：\n"
            f"--- round1 ---\n{srt1}\n--- round2 ---\n{srt2}\n"
        )
