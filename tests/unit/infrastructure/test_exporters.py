"""导出器测试。"""

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

            assert "[00:12.34]测试歌词" in content
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

    def test_export_paragraph_separation(self):
        """测试段落间距 >5 秒时插入空行"""
        project = Project()
        singer = project.singers[0]

        sentence1 = Sentence.from_text("第一行", singer.id)
        sentence1.characters[0].add_timestamp(1000)
        project.add_sentence(sentence1)

        sentence2 = Sentence.from_text("第二行", singer.id)
        sentence2.characters[0].add_timestamp(10000)
        project.add_sentence(sentence2)

        exporter = NicokaraExporter()

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".lrc", delete=False, encoding="utf-8"
        ) as f:
            temp_path = f.name

        try:
            exporter.export(project, temp_path)

            with open(temp_path, "r", encoding="utf-8") as f:
                content = f.read()

            # 两行之间应有空行（间隔 9 秒 > 5 秒阈值）
            assert "\n\n" in content
        finally:
            os.unlink(temp_path)


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

            # 应包含 @Offset
            assert "@Offset=+0" in content
            # 应包含 @Ruby 标签（单次出现无需位置标记）
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

    def test_batch_export(self):
        """测试批量导出"""
        project = Project()
        singer = project.singers[0]
        sentence = Sentence.from_text("测试歌词", singer.id)
        sentence.characters[0].add_timestamp(12345)
        project.add_sentence(sentence)

        service = ExportService()

        with tempfile.TemporaryDirectory() as temp_dir:
            results = service.batch_export(
                project, ["LRC", "KRA"], temp_dir, "test_export"
            )

            assert len(results) == 2
            assert all(r.success for r in results)

            # 检查文件是否创建
            assert os.path.exists(os.path.join(temp_dir, "test_export.lrc"))
            assert os.path.exists(os.path.join(temp_dir, "test_export.kra"))
