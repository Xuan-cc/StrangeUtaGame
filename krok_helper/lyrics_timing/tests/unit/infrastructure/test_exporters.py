"""导出器测试。"""

import re
import pytest
import tempfile
import os
from pathlib import Path

from strange_uta_game.backend.domain import (
    Project,
    Sentence,
    Character,
    Ruby,
    TimeTagType,
)
from strange_uta_game.backend.infrastructure.exporters import (
    LRCExporter,
    KRAExporter,
    TXTExporter,
    Txt2AssExporter,
    ASSDirectExporter,
    NicokaraExporter,
    get_exporter_by_name,
    get_all_exporters,
    ExportError,
)
from strange_uta_game.backend.application import ExportService


class TestLRCExporter:
    """测试 LRC 导出器"""

    def test_export_simple(self):
        """测试简单导出"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = LRCExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 增强型 LRC: 行级 [mm:ss.xxx] + 逐字 <mm:ss.xxx>
            assert "[00:12.345]" in content
            assert "<00:12.345>测" in content
            assert "试歌词" in content
        finally:
            os.unlink(temp_path)

    def test_export_with_metadata(self):
        """测试带元数据的导出"""
        project = Project()
        project.metadata.title = "测试歌曲"
        project.metadata.artist = "测试艺术家"

        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = LRCExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "[ti:测试歌曲]" in content
            assert "[ar:测试艺术家]" in content
        finally:
            os.unlink(temp_path)

    def test_export_empty_project_raises_error(self):
        """测试空项目导出报错"""
        project = Project()
        # 不添加歌词行

        exporter = LRCExporter()

        with tempfile.NamedTemporaryFile(mode="w", suffix=".lrc", delete=False) as f:
            temp_path = f.name

        try:
            with pytest.raises(ExportError):
                exporter.export(project, temp_path)
        finally:
            os.unlink(temp_path)


class TestKRAExporter:
    """测试 KRA 导出器"""

    def test_export(self):
        """测试 KRA 导出"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = KRAExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".kra", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            assert os.path.exists(temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "测试歌词" in content
        finally:
            os.unlink(temp_path)


class TestTXTExporter:
    """测试 TXT 导出器"""

    def test_export(self):
        """测试 TXT 导出"""
        project = Project()
        project.metadata.title = "测试歌曲"
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = TXTExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "# 测试歌曲" in content
            assert "[001]" in content
            assert "测试歌词" in content
        finally:
            os.unlink(temp_path)


class TestTxt2AssExporter:
    """测试 txt2ass 导出器"""

    def test_export(self):
        """测试 txt2ass 导出"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = Txt2AssExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "# Format: [mm:ss.xx]Lyrics" in content
            assert "[00:12.34]测试歌词" in content
        finally:
            os.unlink(temp_path)


class TestASSDirectExporter:
    """测试 ASS 直接导出器"""

    def test_export(self):
        """测试 ASS 导出"""
        project = Project()
        project.metadata.title = "测试歌曲"
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = ASSDirectExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ass", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            assert "[Script Info]" in content
            assert "Title: 测试歌曲" in content
            assert "[V4+ Styles]" in content
            assert "[Events]" in content
            assert "Dialogue:" in content
        finally:
            os.unlink(temp_path)

    def test_generate_karaoke_text_compound_ruby(self):
        """连词 ruby 导出：{合言葉||あ|い,こ|と,ば} 场景。

        "合" 拥有所有 5 个 ruby part 和 5 个时间戳；"言"、"葉" check_count=0 无时间戳。
        期望输出：合言葉|<あ 出现在首个 \\k 块，言葉不作为 tail 追加到末尾。
        """
        from strange_uta_game.backend.domain import Ruby, RubyPart

        project = Project()
        singer = project.singers[0]

        sentence = Sentence.from_text("合言葉", singer.id)
        # "合": 5 个 ruby part，5 个 checkpoint
        ch0 = sentence.characters[0]
        ch0.check_count = 5
        ch0.set_ruby(Ruby(parts=[
            RubyPart(text="あ"),
            RubyPart(text="い"),
            RubyPart(text="こ"),
            RubyPart(text="と"),
            RubyPart(text="ば"),
        ]))
        for i, ts in enumerate([1000, 1130, 1260, 1390, 1520]):
            ch0.add_timestamp(ts, checkpoint_idx=i)
        ch0.linked_to_next = True

        # "言": check_count=0，无时间戳，linked_to_next=True
        ch1 = sentence.characters[1]
        ch1.check_count = 0
        ch1.linked_to_next = True

        # "葉": check_count=0，无时间戳，连词末尾
        ch2 = sentence.characters[2]
        ch2.check_count = 0

        ch2.is_sentence_end = True
        ch2.sentence_end_ts = 1650
        ch2._update_offset_timestamps()
        project.add_sentence(sentence)

        exporter = ASSDirectExporter()
        line_start_ms = sentence.global_timing_start_ms
        line_end_ms = exporter._compute_line_end_ms(sentence)
        text = exporter._generate_karaoke_text(sentence, line_start_ms, line_end_ms)

        # "合言葉" 应整体出现在首个 \\k 块的注音前
        assert "合言葉|<あ" in text, f"连词 kanji 未整体输出: {text}"
        # "言葉" 不应出现在末尾（tail_text bug）
        assert not text.endswith("言葉{\\k0}"), f"言葉 被错误地追加到末尾: {text}"
        assert "ば言葉" not in text, f"言葉 混入续段尾部: {text}"


class TestNicokaraExporter:
    """测试 Nicokara 导出器"""

    def test_export_basic(self):
        """测试基本导出：单字符时间戳使用 [MM:SS:CC] 冒号格式"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        exporter = NicokaraExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 冒号分隔的厘秒格式 [00:12:34]
            assert "[00:12:34]" in content
            assert "测" in content
            # 不应包含旧格式的头部或 ASS 标签
            assert "# Nicokara" not in content
            assert "\\k" not in content
        finally:
            os.unlink(temp_path)

    def test_export_per_char_timestamps(self):
        """测试逐字时间戳：每个字符前有 [MM:SS:CC]"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("宝箱", singer.id)
        # 为两个字符分别打轴
        sentence.characters[0].add_timestamp(10330)
        sentence.characters[1].add_timestamp(10780)
        project.add_sentence(sentence)

        exporter = NicokaraExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 逐字格式: [00:10:33]宝[00:10:78]箱
            assert "[00:10:33]宝" in content
            assert "[00:10:78]箱" in content
        finally:
            os.unlink(temp_path)

    def test_export_line_end_timestamp(self):
        """测试行末结束时间戳"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("あ", singer.id)
        sentence.characters[0].add_timestamp(1000, checkpoint_idx=0)
        sentence.characters[0].set_sentence_end_ts(2000)
        project.add_sentence(sentence)

        exporter = NicokaraExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # [00:01:00]あ[00:02:00]  （字符 + 行末时间戳）
            assert "[00:01:00]あ[00:02:00]" in content
        finally:
            os.unlink(temp_path)

    def test_export_file_extension(self):
        """测试文件扩展名为 .lrc"""
        exporter = NicokaraExporter()
        assert exporter.file_extension == ".lrc"

class TestNicokaraWithRubyExporter:
    """测试带注音的 Nicokara 导出器"""

    def test_export_with_ruby(self):
        """测试 @Ruby 注音标签生成"""
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        sentence = Sentence.from_text("赤い", singer.id)
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="あか")]))
        sentence.characters[0].add_timestamp(5000)
        sentence.characters[1].add_timestamp(6000)
        project.add_sentence(sentence)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # @Offset 仅在 project.offset_ms ≠ 0 时输出（避免 round-trip 污染）；
            # 当前 Project 没有 offset_ms 字段，因此不应出现 @Offset 行。
            assert "@Offset" not in content
            # 应包含 @Ruby 标签（朴素分段：kanji + reading + pos1 + pos2）
            assert "@Ruby1=赤,あか" in content
            # 歌词部分仍为逐字时间戳
            assert "[00:05:00]赤" in content
        finally:
            os.unlink(temp_path)

    def test_export_ruby_relative_timestamps(self):
        """测试 @Ruby 读音中的相对时间戳"""
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        sentence = Sentence.from_text("赤い", singer.id)
        # 设置「赤」的 check_count 为 2（对应读音 あか）
        sentence.characters[0].check_count = 2
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="あ"), RubyPart(text="か")]))

        # checkpoint_idx=0 → あ, checkpoint_idx=1 → か
        sentence.characters[0].add_timestamp(5000, checkpoint_idx=0)
        sentence.characters[0].add_timestamp(5150, checkpoint_idx=1)
        sentence.characters[1].add_timestamp(6000)
        project.add_sentence(sentence)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # @Ruby 读音应包含相对时间戳
            # あ (offset 0) + [00:00:15] (150ms) + か
            assert "あ[00:00:15]か" in content
        finally:
            os.unlink(temp_path)


    def test_export_ruby_multi_reading_disambiguation(self):
        """linked_to_next 切段策略：同 ruby tag 内的多字必须语义构成连词，
        即 `Character.linked_to_next == True` 才能合并；否则必须切为独立 entry。

        本测试构造两个相邻但**未设连词**的单字 ruby（「言」「葉」），
        以及第二句独立的「言」ruby（不同读音）。预期输出 3 个独立 @RubyN：
          - @Ruby1: 言, こ[ts]と  （单字 + 字内相对时间戳）
          - @Ruby2: 葉, ば
          - @Ruby3: 言, ゆ
        三段各带独立位置范围。
        """
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        # 第一句: 言 with reading こ[0]と[163]，葉 with reading ば
        # 两个 ruby 独立、未 linked → 必须切为两个 entry
        s1 = Sentence.from_text("言葉は", singer.id)
        s1.characters[0].check_count = 2
        s1.characters[0].set_ruby(
            Ruby(parts=[RubyPart(text="こ"), RubyPart(text="と")])
        )
        s1.characters[0].add_timestamp(1000, checkpoint_idx=0)
        s1.characters[0].add_timestamp(1163, checkpoint_idx=1)
        # 注意：linked_to_next 默认 False，符合「未设为连词」的语义
        s1.characters[1].set_ruby(Ruby(parts=[RubyPart(text="ば")]))
        s1.characters[1].add_timestamp(1300)
        s1.characters[2].add_timestamp(1500)
        project.add_sentence(s1)

        # 第二句: 言 with reading ゆ (不同的读音)
        s2 = Sentence.from_text("言う", singer.id)
        s2.characters[0].set_ruby(Ruby(parts=[RubyPart(text="ゆ")]))
        s2.characters[0].add_timestamp(5000)
        s2.characters[1].add_timestamp(5200)
        project.add_sentence(s2)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 第一段：「言」单独 entry，reading 含字内相对 ts
            assert re.search(
                r"@Ruby\d+=言,こ\[\d{2}:\d{2}:\d{2}\]と,"
                r"\[00:01:00\],\[00:01:30\]",
                content,
            ), f"未匹配「言」段 (单字+相对 ts):\n{content}"
            # 第二段：「葉」单独 entry
            assert re.search(
                r"@Ruby\d+=葉,ば,\[00:01:30\],\[00:01:50\]",
                content,
            ), f"未匹配「葉」段:\n{content}"
            # 第三段：第二句「言」独立 entry，自身位置范围
            assert re.search(
                r"@Ruby\d+=言,ゆ,\[00:05:00\],\[00:05:20\]",
                content,
            ), f"未匹配第二句「言」段:\n{content}"
            # 严格不允许把「言葉」合并（未 linked）
            assert "@Ruby1=言葉" not in content
            assert "@Ruby2=言葉" not in content
            assert "@Ruby3=言葉" not in content
        finally:
            os.unlink(temp_path)

    def test_export_ruby_linked_merges_into_single_entry(self):
        """linked_to_next=True 正向用例：相邻 ruby 字显式连词，
        必须合并为单一 @RubyN entry（与 disambiguation 测试构成正/反对照）。

        构造「言葉」两字均有 ruby、且 `言.linked_to_next == True`，
        预期输出：
          @Ruby1=言葉,こと[..]ば,[pos1],[pos2]
        其中 pos1 取「言」首 ts，pos2 取「は」(下一字) ts。
        """
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        # 「言葉は」：言+葉 同为 ruby 且 linked → 合并 entry
        sentence = Sentence.from_text("言葉は", singer.id)
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="こと")]))
        sentence.characters[0].add_timestamp(1000)
        sentence.characters[0].linked_to_next = True  # 显式连词
        sentence.characters[1].set_ruby(Ruby(parts=[RubyPart(text="ば")]))
        sentence.characters[1].add_timestamp(1300)
        sentence.characters[2].add_timestamp(1500)
        project.add_sentence(sentence)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 合并段：亲文字「言葉」，ruby「ことば」（含字间相对 ts），
            # pos1=言首 [00:01:00]，pos2=は开始 [00:01:50]
            assert re.search(
                r"@Ruby\d+=言葉,こと\[\d{2}:\d{2}:\d{2}\]ば,"
                r"\[00:01:00\],\[00:01:50\]",
                content,
            ), f"未匹配 linked 合并 entry:\n{content}"
            # 严格不允许被切成两段
            assert not re.search(r"@Ruby\d+=言,こと", content), (
                f"linked 字被错误切段:\n{content}"
            )
            assert not re.search(r"@Ruby\d+=葉,ば", content), (
                f"linked 字被错误切段:\n{content}"
            )
        finally:
            os.unlink(temp_path)

    def test_export_ruby_linked_allows_internal_singing_pause(self):
        """`is_sentence_end` 表示「演唱停顿」而非语义句末，**不参与**
        ruby 切段判断。即使连词内部某字 is_sentence_end=True，
        只要 linked_to_next=True，整个连词仍合并为单一 @RubyN entry。

        构造「言葉」连词，「言」同时 linked_to_next=True 且
        is_sentence_end=True（演唱时此处有呼吸停顿），预期：
          - 仍合并为单 entry @Ruby1=言葉,...
          - pos2 取「葉」段尾的下一字 ts（不是被「言」的 sentence_end_ts 切断）
        """
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        sentence = Sentence.from_text("言葉は", singer.id)
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="こと")]))
        sentence.characters[0].add_timestamp(1000)
        sentence.characters[0].linked_to_next = True
        # 演唱停顿但仍为连词：is_sentence_end 不应破坏切段
        sentence.characters[0].is_sentence_end = True
        sentence.characters[0].sentence_end_ts = 1200
        sentence.characters[1].set_ruby(Ruby(parts=[RubyPart(text="ば")]))
        sentence.characters[1].add_timestamp(1300)
        sentence.characters[2].add_timestamp(1500)
        project.add_sentence(sentence)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 合并段：「言葉」仍为单 entry，pos2=「は」起始 ts [00:01:50]
            # 关键反断言：不允许在「言」处被切断 → 不允许出现单独的「言」段
            assert re.search(
                r"@Ruby\d+=言葉,こと\[\d{2}:\d{2}:\d{2}\]ば,"
                r"\[00:01:00\],\[00:01:50\]",
                content,
            ), f"linked + is_sentence_end 被错误切段:\n{content}"
            assert not re.search(r"@Ruby\d+=言,こと", content), (
                f"is_sentence_end 错误地切断了 linked 连词:\n{content}"
            )
        finally:
            os.unlink(temp_path)

    def test_export_ruby_linked_downstream_no_ruby(self):
        """linked_to_next=True 但下游字无 ruby 的场景（如「明日」：
        「明」有 ruby「あした」、linked_to_next=True；
        「日」无 ruby、linked_to_next=False）。

        预期：
          - @Ruby1=明日,あした,[pos1],[pos2]  —— 「日」贡献 kanji 但无 reading
          - **不**出现 @Ruby=明（亲文字只含「明」是错的）
        """
        from strange_uta_game.backend.domain import Ruby, RubyPart
        from strange_uta_game.backend.infrastructure.exporters import (
            NicokaraWithRubyExporter,
        )

        project = Project()
        singer = project.singers[0]

        # 「明日は」：明(ruby=あした, linked=True) + 日(ruby=None) + は(无ruby)
        sentence = Sentence.from_text("明日は", singer.id)
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="あした")]))
        sentence.characters[0].add_timestamp(1000)
        sentence.characters[0].linked_to_next = True
        # 「日」无 ruby，check_count=0，linked=False
        sentence.characters[1].add_timestamp(1300)
        sentence.characters[2].add_timestamp(1500)
        project.add_sentence(sentence)

        exporter = NicokaraWithRubyExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 亲文字必须是「明日」（含下游无 ruby 字）
            # pos2=[00:01:50] 取段尾字「日」的下一字「は」的 ts=1500ms
            assert re.search(
                r"@Ruby\d+=明日,あした,\[00:01:00\],\[00:01:50\]",
                content,
            ), f"明日 linked 未正确合并:\n{content}"
            # 严格不允许只含「明」
            assert not re.search(r"@Ruby\d+=明,", content), (
                f"「明日」被错误截断为「明」:\n{content}"
            )
        finally:
            os.unlink(temp_path)


class TestExporterUtils:
    """测试导出器工具函数"""

    def test_get_exporter_by_name(self):
        """测试根据名称获取导出器"""
        exporter = get_exporter_by_name("LRC (增强型)")
        assert isinstance(exporter, LRCExporter)

        exporter = get_exporter_by_name("KRA")
        assert isinstance(exporter, KRAExporter)

    def test_get_exporter_by_name_legacy(self):
        """测试旧名称 'LRC' 向后兼容"""
        exporter = get_exporter_by_name("LRC")
        assert isinstance(exporter, LRCExporter)

    def test_get_exporter_by_name_invalid(self):
        """测试获取不存在的导出器"""
        with pytest.raises(ValueError):
            get_exporter_by_name("INVALID")

    def test_get_all_exporters(self):
        """测试获取所有导出器"""
        exporters = get_all_exporters()
        assert len(exporters) >= 11

        names = [e.name for e in exporters]
        assert "LRC (增强型)" in names
        assert "LRC (逐行)" in names
        assert "LRC (逐字)" in names
        assert "KRA" in names
        assert "TXT" in names
        assert "SRT" in names


class TestExportService:
    """测试导出服务"""

    def test_get_available_formats(self):
        """测试获取可用格式"""
        service = ExportService()
        formats = service.get_available_formats()

        assert len(formats) >= 7

        lrc_format = next((f for f in formats if f["name"] == "LRC (增强型)"), None)
        assert lrc_format is not None
        assert lrc_format["extension"] == ".lrc"

    def test_export(self):
        """测试导出功能"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        service = ExportService()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            os.unlink(temp_path)  # 删除临时文件，让服务创建

            result = service.export(project, "LRC (增强型)", temp_path)

            assert result.success is True
            assert result.file_path == temp_path
            assert result.format_name == "LRC (增强型)"

            assert os.path.exists(temp_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    def test_validate_before_export(self):
        """测试导出前验证"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        # 不添加时间标签
        project.add_sentence(sentence)

        service = ExportService()
        errors = service.validate_before_export(project)

        # 应该提示没有完成打轴
        assert len(errors) > 0
        assert any("没有时间标签" in e for e in errors)
