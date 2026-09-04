"""ASS export → import → export roundtrip 回归测试。

覆盖 5 类典型场景，保证：
1. 第一次导出和第二次导出文本逐字相同（idempotent）。
2. 原始 Project 的关键字段（每字 ts、停顿点 ts、ruby、连词、per-char singer）
   被重导入后完整保留。

不覆盖 line_end_ts 兜底场景；不覆盖前导无 ts 字符的边角情况。
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Tuple

import pytest

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Singer,
    Ruby,
    RubyPart,
)
from strange_uta_game.backend.infrastructure.exporters.txt2ass_exporter import (
    ASSDirectExporter,
)
from strange_uta_game.backend.infrastructure.parsers.ass_parser import ASSParser
from strange_uta_game.backend.infrastructure.parsers.lyric_parser import (
    parse_to_sentences,
)


# ────────────────────────────────────────────────────────────────
# helpers
# ────────────────────────────────────────────────────────────────


def _signature(project: Project) -> List[Dict[str, Any]]:
    """Project 的稳定签名，仅含 ASS 应该 round-trip 保留的字段。"""
    out: List[Dict[str, Any]] = []
    for sent in project.sentences:
        out.append({
            "text": sent.text,
            "chars": [
                {
                    "ch": c.char,
                    "ts": list(c.global_timestamps),
                    "end_ts": c.global_sentence_end_ts,
                    "is_end": c.is_sentence_end,
                    "ruby": [p.text for p in (c.ruby.parts if c.ruby else [])],
                    "linked": c.linked_to_next,
                    # singer 用 name 而非 id 比较（id 是 UUID 重导入会变）
                    "singer_name": _singer_name(project, c.singer_id or sent.singer_id),
                }
                for c in sent.characters
            ],
        })
    return out


def _singer_name(project: Project, singer_id: str) -> str:
    for s in project.singers:
        if s.id == singer_id:
            return s.name
    return ""


def _roundtrip(project: Project) -> Tuple[str, str, Project]:
    """export → parse → re-build project → export 一次，返回 (ass1, ass2, reimported)。"""
    exporter = ASSDirectExporter()
    with tempfile.TemporaryDirectory() as td:
        ass1_path = os.path.join(td, "r1.ass")
        ass2_path = os.path.join(td, "r2.ass")

        exporter.export(project, ass1_path)
        with open(ass1_path, "r", encoding="utf-8") as f:
            ass1 = f.read()

        # 重导入
        parser = ASSParser()
        parsed_lines = parser.parse(ass1)

        # 收集 per-char singer 显示名
        all_names: set = set()
        for pl in parsed_lines:
            for name in pl.char_singer_map.values():
                if name:
                    all_names.add(name)

        # 名字 → id：先匹配原 project 的 singer，缺失则新建（roundtrip 测试里
        # 新建几乎不发生，因为我们在原 project 里就有这些 singer）
        name_to_id: Dict[str, str] = {}
        rebuilt_singers: List[Singer] = list(project.singers)
        for name in sorted(all_names):
            hit = next((s for s in rebuilt_singers if s.name == name), None)
            if hit:
                name_to_id[name] = hit.id
            else:
                new_singer = Singer(name=name, color="#4ECDC4", is_default=False)
                rebuilt_singers.append(new_singer)
                name_to_id[name] = new_singer.id

        sentences = parse_to_sentences(
            parsed_lines, project.singers[0].id, singer_name_to_id=name_to_id
        )

        p2 = Project()
        p2.metadata.title = parser.parse_metadata().get("title", "")
        # 替换默认 singer 集合为「原 + 新建」
        p2.singers = rebuilt_singers
        for s in sentences:
            p2.add_sentence(s)

        exporter.export(p2, ass2_path)
        with open(ass2_path, "r", encoding="utf-8") as f:
            ass2 = f.read()

        return ass1, ass2, p2


# ────────────────────────────────────────────────────────────────
# fixtures
# ────────────────────────────────────────────────────────────────


def _make_project_basic() -> Project:
    """普通逐字 + 单字单 part ruby + 单字多 part ruby + 多字 span ruby + 行内换人"""
    project = Project()
    project.metadata.title = "RoundtripCase"

    s1 = project.singers[0]
    s2 = Singer(name="B", color="#4ECDC4", is_default=False)
    project.add_singer(s2)

    # Sentence 1: 普通逐字 + 句末释放
    sent = Sentence.from_text("いつか", s1.id)
    sent.characters[0].add_timestamp(1000)
    sent.characters[1].add_timestamp(1500)
    sent.characters[2].add_timestamp(2000)
    sent.characters[2].is_sentence_end = True
    sent.characters[2].sentence_end_ts = 2500
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # Sentence 2: 单字单 part ruby
    sent = Sentence.from_text("見た", s1.id)
    sent.characters[0].add_timestamp(3000)
    sent.characters[0].set_ruby(Ruby(parts=[RubyPart(text="み")]))
    sent.characters[0].check_count = 1
    sent.characters[0].push_to_ruby()
    sent.characters[1].add_timestamp(3500)
    sent.characters[1].is_sentence_end = True
    sent.characters[1].sentence_end_ts = 4000
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # Sentence 3: 单字多 part ruby（届 → と + ど）
    sent = Sentence.from_text("届く", s1.id)
    ch0 = sent.characters[0]
    ch0.check_count = 2
    ch0.set_ruby(Ruby(parts=[RubyPart(text="と"), RubyPart(text="ど")]))
    ch0.add_timestamp(5000, checkpoint_idx=0)
    ch0.add_timestamp(5200, checkpoint_idx=1)
    sent.characters[1].add_timestamp(5500)
    sent.characters[1].is_sentence_end = True
    sent.characters[1].sentence_end_ts = 6000
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # Sentence 4: 多字 span ruby（大冒険 → だいぼうけん）
    sent = Sentence.from_text("大冒険", s1.id)
    sent.characters[0].add_timestamp(7000)
    sent.characters[0].set_ruby(Ruby(parts=[RubyPart(text="だいぼうけん")]))
    sent.characters[0].check_count = 1
    sent.characters[0].linked_to_next = True
    sent.characters[1].linked_to_next = True
    sent.characters[2].is_sentence_end = True
    sent.characters[2].sentence_end_ts = 8000
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # Sentence 5: 行内换人 + 标点（ねぇ、君 ねぇ=s1, 、=s1, 君=s2）
    sent = Sentence.from_text("ねぇ君", s1.id)  # 去掉标点简化（标点本就无 ts）
    sent.characters[0].add_timestamp(9000)
    sent.characters[1].add_timestamp(9300)
    sent.characters[2].add_timestamp(9800)
    sent.characters[2].singer_id = s2.id
    sent.characters[2].is_sentence_end = True
    sent.characters[2].sentence_end_ts = 10500
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    # Sentence 6: ♫ 间奏行（单字符符号，带 ts + 停顿点）
    sent = Sentence.from_text("♫", s1.id)
    sent.characters[0].add_timestamp(11000)
    sent.characters[0].is_sentence_end = True
    sent.characters[0].sentence_end_ts = 12000
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    return project


def _make_project_midline_pause() -> Project:
    """句中断句轴点（is_sentence_end 演唱停顿）场景。

    - s1「て　不意に」：轴点在锚字「て」上（34430→35010），轴点后是
      全角空格（无 ts）。导出契约：{\k58}て{\k24}　{\k20}不|<ふ。
    - s2「奇跡？縁」：轴点在无 ts 标点「？」上（26200），前一音是
      「跡」的续段 き@26090。导出契约：{\k6}#|き{\k5}？{\k34}縁|<え
      （均分 + 空文本间隙段）。
    """
    from strange_uta_game.backend.domain import Ruby, RubyPart

    project = Project()
    project.metadata.title = "MidlinePauseCase"
    s1 = project.singers[0]

    sent = Sentence.from_text("て　不意に", s1.id)
    c_te = sent.characters[0]
    c_te.add_timestamp(34430)
    c_te.is_sentence_end = True
    c_te.set_sentence_end_ts(35010)
    sent.characters[2].check_count = 1
    sent.characters[2].set_ruby(Ruby(parts=[RubyPart(text="ふ")]))
    sent.characters[2].add_timestamp(35250)
    sent.characters[3].check_count = 1
    sent.characters[3].set_ruby(Ruby(parts=[RubyPart(text="い")]))
    sent.characters[3].add_timestamp(35450)
    sent.characters[4].add_timestamp(35860)
    sent.characters[4].set_sentence_end_ts(36220)
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    sent = Sentence.from_text("奇跡？縁", s1.id)
    sent.characters[0].check_count = 1
    sent.characters[0].set_ruby(Ruby(parts=[RubyPart(text="き")]))
    sent.characters[0].add_timestamp(25680)
    ch_seki = sent.characters[1]
    ch_seki.check_count = 2
    ch_seki.set_ruby(Ruby(parts=[RubyPart(text="せ"), RubyPart(text="き")]))
    ch_seki.add_timestamp(25910, checkpoint_idx=0)
    ch_seki.add_timestamp(26090, checkpoint_idx=1)
    c_q = sent.characters[2]
    c_q.is_sentence_end = True
    c_q.set_sentence_end_ts(26200)
    ch_en = sent.characters[3]
    ch_en.check_count = 3
    ch_en.set_ruby(
        Ruby(parts=[RubyPart(text="え"), RubyPart(text="に"), RubyPart(text="し")])
    )
    ch_en.add_timestamp(26540, checkpoint_idx=0)
    ch_en.add_timestamp(26710, checkpoint_idx=1)
    ch_en.add_timestamp(26870, checkpoint_idx=2)
    ch_en.set_sentence_end_ts(27500)
    for ch in sent.characters:
        ch.set_offset(0)
    project.add_sentence(sent)

    return project


# ────────────────────────────────────────────────────────────────
# tests
# ────────────────────────────────────────────────────────────────


class TestASSRoundtrip:
    def test_ass_text_is_idempotent_on_second_pass(self):
        """export → import → export 后两份 ASS 文本逐字相同。"""
        project = _make_project_basic()
        ass1, ass2, _ = _roundtrip(project)
        assert ass1 == ass2, (
            "ASS 文本第二次导出与第一次不同：\n"
            f"--- round1 ---\n{ass1}\n"
            f"--- round2 ---\n{ass2}\n"
        )

    def test_project_signature_preserved(self):
        """每字 ts、停顿点、ruby、连词、per-char singer 都被保留。"""
        project = _make_project_basic()
        sig_before = _signature(project)
        _, _, p2 = _roundtrip(project)
        sig_after = _signature(p2)

        # 同句数
        assert len(sig_before) == len(sig_after), (
            f"句数变了: {len(sig_before)} → {len(sig_after)}"
        )

        for i, (a, b) in enumerate(zip(sig_before, sig_after)):
            assert a == b, (
                f"sentence #{i} text={a['text']!r} 签名不一致\n"
                f"  orig: {a}\n"
                f"  rein: {b}\n"
            )

    def test_pre_roll_compensation_keeps_first_ts(self):
        """首字 ts 不漂移：导出 Dialogue Start = 首字 ts - PRE_ROLL，
        重导入靠 SUG 哨兵补偿回首字 ts。"""
        project = _make_project_basic()
        _, _, p2 = _roundtrip(project)

        orig_first = project.sentences[0].characters[0].global_timestamps[0]
        new_first = p2.sentences[0].characters[0].global_timestamps[0]
        assert orig_first == new_first, (
            f"首字 ts 漂移了: 原 {orig_first}, 重导入 {new_first}"
        )

    def test_multi_char_span_ruby_not_split(self):
        """大冒険 的「だいぼうけん」整段读音不被均分到 3 个字。"""
        project = _make_project_basic()
        _, _, p2 = _roundtrip(project)

        sent4 = p2.sentences[3]  # 大冒険
        ch0 = sent4.characters[0]
        assert ch0.ruby is not None and ch0.ruby.parts, "大字的 ruby 丢了"
        assert ch0.ruby.parts[0].text == "だいぼうけん", (
            f"多字 span ruby 被均分: ch0.ruby = {[p.text for p in ch0.ruby.parts]}"
        )
        # 后两字不应当被分到读音
        assert sent4.characters[1].ruby is None, "冒字不该有 ruby"
        assert sent4.characters[2].ruby is None, "険字不该有 ruby"

    def test_linked_group_end_ts_on_tail(self):
        """连词组的 sentence_end_ts 绑在链尾「険」，不是锚字「大」。"""
        project = _make_project_basic()
        _, _, p2 = _roundtrip(project)

        sent4 = p2.sentences[3]  # 大冒険
        # 大不应当 is_sentence_end
        assert sent4.characters[0].is_sentence_end is False, (
            "锚字「大」不应当持有停顿点"
        )
        # 険 应当是 is_sentence_end + end_ts=8000
        tail = sent4.characters[2]
        assert tail.is_sentence_end is True, "链尾「険」应当是停顿点"
        assert tail.global_sentence_end_ts == 8000, (
            f"链尾「険」end_ts 错: {tail.global_sentence_end_ts}"
        )

    def test_per_char_singer_preserved(self):
        """ねぇ君 行内的 君→B 切换被 round-trip 保留。"""
        project = _make_project_basic()
        s2_name = "B"
        _, _, p2 = _roundtrip(project)

        sent5 = p2.sentences[4]  # ねぇ君
        # 君的 singer 应当是 B
        kimi_singer_id = sent5.characters[2].singer_id
        kimi_singer = next((s for s in p2.singers if s.id == kimi_singer_id), None)
        assert kimi_singer is not None, "君字找不到 singer"
        assert kimi_singer.name == s2_name, (
            f"君字的 singer 错: {kimi_singer.name}, 期望 {s2_name}"
        )

    def test_title_preserved(self):
        """[Script Info] Title 被 round-trip 保留。"""
        project = _make_project_basic()
        _, _, p2 = _roundtrip(project)
        assert p2.metadata.title == "RoundtripCase", (
            f"Title round-trip 丢失: {p2.metadata.title!r}"
        )

    def test_musical_note_interlude_roundtrip(self):
        """♫ 间奏行：ts + 停顿点经 ASS 往返完整保留。"""
        project = _make_project_basic()
        _, _, p2 = _roundtrip(project)

        sent6 = p2.sentences[5]  # ♫
        ch = sent6.characters[0]
        assert ch.char == "♫"
        assert ch.global_timestamps == [11000]
        assert ch.is_sentence_end is True
        assert ch.global_sentence_end_ts == 12000

    def test_non_sug_ass_no_compensation(self):
        """第三方 ASS（无 SUG 哨兵）按 Dialogue Start 原始值解析，不补偿。"""
        third_party = (
            "[Script Info]\n"
            "Title: Foreign\n"
            "ScriptType: v4.00+\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            "Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
            "0,0,0,0,100,100,0,0,1,2,0,5,10,10,10,1\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:03.00,Default,,0,0,0,,"
            "{\\k50}い{\\k50}つ{\\k50}か\n"
        )
        parser = ASSParser()
        parsed_lines = parser.parse(third_party)
        assert len(parsed_lines) == 1
        # 首字 ts 应当 = Dialogue Start = 1000ms，不补偿
        first_ts = parsed_lines[0].timetags[0][1]
        assert first_ts == 1000, (
            f"第三方 ASS 不应当被补偿: 首字 ts = {first_ts}, 期望 1000"
        )

    def test_third_party_plain_ass_dialogue_end_as_line_end(self):
        """第三方无 \\k 的普通 Dialogue：Dialogue End → line_end_ts 绑行末字符。"""
        third_party = (
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
            "Dialogue: 0,0:00:02.26,0:00:04.78,Default,,0,0,0,,As I'm here\n"
        )
        parser = ASSParser()
        parsed_lines = parser.parse(third_party)

        assert parser.has_karaoke_tags is False
        assert parser.has_ruby_syntax is False
        pl = parsed_lines[0]
        assert pl.timetags == [(0, 2260)]
        assert pl.line_end_ts == 4780
        assert pl.line_end_bind_last is True

        sentences = parse_to_sentences(parsed_lines, "s1")
        tail = sentences[0].characters[-1]
        assert tail.char == "e"
        assert tail.is_sentence_end is True
        assert tail.sentence_end_ts == 4780

    def test_third_party_karaoke_ass_end_takes_max_of_chain_and_dialogue_end(self):
        r"""第三方 \k 链：line_end = max(\k 链末, Dialogue End)，绑行末字符。

        `{\k50}い{\k50}つ` 链末 = 1000 + 1000 = 2000 > Dialogue End 1.50 → 2000
        （厘秒舍入导致链末略超 End 时以链末为准）。
        """
        third_party = (
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
            "Dialogue: 0,0:00:01.00,0:00:01.50,Default,,0,0,0,,"
            "{\\k50}い{\\k50}つ\n"
            "Dialogue: 0,0:00:02.00,0:00:04.00,Default,,0,0,0,,"
            "{\\k50}か\n"
        )
        parser = ASSParser()
        parsed_lines = parser.parse(third_party)

        assert parser.has_karaoke_tags is True
        assert parsed_lines[0].line_end_ts == 2000  # 链末 2000 > End 1500
        assert parsed_lines[0].line_end_bind_last is True
        # Dialogue End 超出链末（尾部 padding）→ 取 End
        assert parsed_lines[1].line_end_ts == 4000

        sentences = parse_to_sentences(parsed_lines, "s1")
        tail0 = sentences[0].characters[-1]
        assert tail0.is_sentence_end is True
        assert tail0.sentence_end_ts == 2000
        tail1 = sentences[1].characters[-1]
        assert tail1.sentence_end_ts == 4000

    def test_sug_sentinel_karaoke_keeps_chain_binding(self):
        r"""SUG 哨兵文件：行尾释放仍按 \k 链绑链尾（bind_last=False），
        Dialogue End 中的 post-roll 不进入 line_end_ts。"""
        sug_ass = (
            "[Script Info]\n"
            "; Generator: StrangeUtaGame\n"
            "; SUG-PreRollMs: 100\n"
            "; SUG-PostRollMs: 500\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
            "Effect, Text\n"
            "Dialogue: 0,0:00:00.90,0:00:03.50,Default,,0,0,0,,"
            "{\\k50}い{\\k50}つ{\\k0}\n"
        )
        parser = ASSParser()
        parsed_lines = parser.parse(sug_ass)

        # pre-roll 补偿：首字 ts = 900 + 100 = 1000
        assert parsed_lines[0].timetags == [(0, 1000), (1, 1500)]
        # post-roll 不进入：链末 = 1000 + 500 + 500 = 2000（Dialogue End 3500 被忽略）
        assert parsed_lines[0].line_end_ts == 2000
        assert parsed_lines[0].line_end_bind_last is False

    def test_midline_pause_export_contract(self):
        """句中断句轴点导出契约（用户反馈 bug 的回归）：
        - 轴点不被前一个音吞掉：{\k58}て{\k24}　{\k20}不|<ふ
        - 轴点前标点均分 + 空文本间隙段：{\k6}#|き{\k5}？{\k34}
        """
        project = _make_project_midline_pause()
        exporter = ASSDirectExporter()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "r.ass")
            exporter.export(project, path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()

        assert "{\\k58}て{\\k24}　{\\k20}" in content, (
            f"轴点间隙未切给空格:\n{content}"
        )
        assert "{\\k6}#|き{\\k5}？{\\k34}" in content, (
            f"轴点前标点未均分/未输出间隙段:\n{content}"
        )
        # 旧的吞轴点行为不应再出现
        assert "{\\k82}て" not in content
        assert "{\\k45}#|き？" not in content

    def test_midline_pause_roundtrip_idempotent(self):
        """中断句点 export→import→export 幂等（空文本间隙段经
        gap_pause_map 还原为 sentence_end_ts，二次导出不漂移）。
        """
        project = _make_project_midline_pause()
        ass1, ass2, p2 = _roundtrip(project)
        assert ass1 == ass2, (
            "含中断句点的 ASS 二次导出漂移：\n"
            f"--- round1 ---\n{ass1}\n"
            f"--- round2 ---\n{ass2}\n"
        )

    def test_midline_pause_restored_on_reimport(self):
        """空文本间隙段在重导入时绑回轴点：
        - 「？」获得自身块起点 ts 26150 和 sentence_end_ts 26200；
        - 「て　不意に」里空格承接间隙（ts 35010），行内 \k 边界保持。
        """
        project = _make_project_midline_pause()
        _, _, p2 = _roundtrip(project)

        sent2 = p2.sentences[1]  # 奇跡？縁
        c_q = sent2.characters[2]
        assert c_q.global_timestamps == [26150], (
            f"「？」重导入 ts 错: {c_q.global_timestamps}"
        )
        assert c_q.is_sentence_end is True, "「？」的轴点标记丢失"
        assert c_q.global_sentence_end_ts == 26200, (
            f"「？」轴点释放 ts 错: {c_q.global_sentence_end_ts}"
        )

        sent1 = p2.sentences[0]  # て　不意に
        c_space = sent1.characters[1]
        assert c_space.global_timestamps == [35010], (
            f"空格应承接轴点间隙 (ts 35010): {c_space.global_timestamps}"
        )
