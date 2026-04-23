"""SUG 项目文件解析器测试。"""

import pytest
from pathlib import Path
from datetime import datetime

from strange_uta_game.backend.infrastructure.persistence.sug_io import (
    SugProjectParser,
    SugParseError,
)
from strange_uta_game.backend.domain import (
    Project,
    Singer,
    Sentence,
    Character,
    Ruby,
    RubyPart,
)


class TestSugProjectParser:
    """测试 SUG 项目文件解析器"""

    def test_save_and_load_simple_project(self, tmp_path):
        """测试保存和加载简单项目"""
        # 创建项目
        project = Project()
        singer = project.get_default_singer()

        sentence = Sentence.from_text("测试歌词", singer.id)
        project.add_sentence(sentence)

        # 保存
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))

        # 验证文件存在
        assert file_path.exists()

        # 加载
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert loaded.id == project.id
        assert len(loaded.sentences) == 1
        assert loaded.sentences[0].text == "测试歌词"

    def test_save_and_load_with_timetags(self, tmp_path):
        """测试保存和加载带时间标签的项目"""
        project = Project()
        singer = project.get_default_singer()

        sentence = Sentence.from_text("赤い花", singer.id)
        sentence.characters[0].add_timestamp(1000)
        sentence.characters[1].add_timestamp(1500)
        sentence.characters[2].add_timestamp(2000)
        sentence.characters[2].set_sentence_end_ts(2500)

        project.add_sentence(sentence)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert sum(len(c.all_timestamps) for c in loaded.sentences[0].characters) == 4
        assert loaded.sentences[0].characters[0].timestamps[0] == 1000
        assert loaded.sentences[0].characters[2].sentence_end_ts == 2500

    def test_load_legacy_v2_sentence_end_without_sentence_end_ts(self, tmp_path):
        file_path = tmp_path / "legacy_v2.sug"
        file_path.write_text(
            """
{
  "version": "2.0",
  "id": "p1",
  "metadata": {},
  "audio_duration_ms": 0,
  "singers": [
    {
      "id": "s1",
      "name": "默认",
      "color": "#FF6B6B",
      "is_default": true,
      "display_priority": 0,
      "enabled": true,
      "backend_number": 1
    }
  ],
  "sentences": [
    {
      "id": "line1",
      "singer_id": "s1",
      "characters": [
        {
          "char": "花",
          "check_count": 2,
          "timestamps": [1000, 1500],
          "linked_to_next": false,
          "is_line_end": true,
          "is_sentence_end": true,
          "is_rest": false,
          "singer_id": "s1"
        }
      ]
    }
  ]
}
            """.strip(),
            encoding="utf-8",
        )

        loaded = SugProjectParser.load(str(file_path))

        char = loaded.sentences[0].characters[0]
        assert char.check_count == 1
        assert char.timestamps == [1000]
        assert char.sentence_end_ts == 1500

    def test_load_legacy_v2_sentence_end_without_release_timestamp(self, tmp_path):
        file_path = tmp_path / "legacy_v2_partial.sug"
        file_path.write_text(
            """
{
  "version": "2.0",
  "id": "p1",
  "metadata": {},
  "audio_duration_ms": 0,
  "singers": [
    {
      "id": "s1",
      "name": "默认",
      "color": "#FF6B6B",
      "is_default": true,
      "display_priority": 0,
      "enabled": true,
      "backend_number": 1
    }
  ],
  "sentences": [
    {
      "id": "line1",
      "singer_id": "s1",
      "characters": [
        {
          "char": "花",
          "check_count": 2,
          "timestamps": [1000],
          "linked_to_next": false,
          "is_line_end": true,
          "is_sentence_end": true,
          "is_rest": false,
          "singer_id": "s1"
        }
      ]
    }
  ]
}
            """.strip(),
            encoding="utf-8",
        )

        loaded = SugProjectParser.load(str(file_path))

        char = loaded.sentences[0].characters[0]
        assert char.check_count == 1
        assert char.timestamps == [1000]
        assert char.sentence_end_ts is None

    def test_save_and_load_with_rubies(self, tmp_path):
        """测试保存和加载带注音的项目"""
        project = Project()
        singer = project.get_default_singer()

        sentence = Sentence.from_text("赤い花", singer.id)
        sentence.characters[0].set_ruby(Ruby(parts=[RubyPart(text="あか")]))
        sentence.characters[2].set_ruby(Ruby(parts=[RubyPart(text="はな")]))

        project.add_sentence(sentence)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert len(loaded.sentences[0].rubies) == 2
        assert loaded.sentences[0].rubies[0].text == "あか"

    def test_save_and_load_multiple_singers(self, tmp_path):
        """测试保存和加载多演唱者项目"""
        project = Project()

        # 添加演唱者
        singer2 = Singer(name="和声", color="#4ECDC4")
        project.add_singer(singer2)

        # 添加歌词
        sentence1 = Sentence.from_text("主唱", project.get_default_singer().id)
        sentence2 = Sentence.from_text("和声", singer2.id)

        project.add_sentence(sentence1)
        project.add_sentence(sentence2)

        # 保存并加载
        file_path = tmp_path / "test.sug"
        SugProjectParser.save(project, str(file_path))
        loaded = SugProjectParser.load(str(file_path))

        # 验证
        assert len(loaded.singers) == 2
        assert len(loaded.sentences) == 2

    def test_load_nonexistent_file_raises_error(self, tmp_path):
        """测试加载不存在的文件应该报错"""
        with pytest.raises(SugParseError) as exc_info:
            SugProjectParser.load(str(tmp_path / "nonexistent.sug"))

        assert "文件不存在" in str(exc_info.value)

    def test_load_invalid_json_raises_error(self, tmp_path):
        """测试加载无效的 JSON 文件应该报错"""
        file_path = tmp_path / "invalid.sug"
        file_path.write_text("not valid json", encoding="utf-8")

        with pytest.raises(SugParseError) as exc_info:
            SugProjectParser.load(str(file_path))

        assert "JSON" in str(exc_info.value)
