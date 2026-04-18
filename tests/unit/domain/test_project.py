import pytest
from datetime import datetime
from strange_uta_game.backend.domain import (
    Project,
    ProjectMetadata,
    Singer,
    Sentence,
    ValidationError,
    DomainError,
)


class TestProject:
    def test_creation_with_defaults(self):
        project = Project()
        assert project.id is not None
        assert len(project.sentences) == 0
        assert len(project.singers) == 1
        assert project.audio_duration_ms == 0
        assert isinstance(project.metadata, ProjectMetadata)

        default_singer = project.singers[0]
        assert default_singer.is_default is True
        assert default_singer.name == "未命名"
        assert default_singer.backend_number == 1

    def test_add_singer(self):
        project = Project()
        new_singer = Singer(name="和声", color="#4ECDC4")
        project.add_singer(new_singer)
        assert len(project.singers) == 2

    def test_remove_singer_with_cascade(self):
        project = Project()
        singer = Singer(name="和声")
        project.add_singer(singer)

        s = Sentence.from_text("测试", singer.id)
        project.add_sentence(s)
        assert len(project.sentences) == 1

        project.remove_singer(singer.id)
        assert len(project.sentences) == 0

    def test_remove_singer_with_transfer(self):
        project = Project()
        default_singer = project.get_default_singer()
        singer = Singer(name="和声")
        project.add_singer(singer)

        s = Sentence.from_text("测试", singer.id)
        project.add_sentence(s)

        project.remove_singer(singer.id, transfer_to=default_singer.id)
        assert len(project.sentences) == 1
        assert project.sentences[0].singer_id == default_singer.id

    def test_add_sentence(self):
        project = Project()
        singer = project.get_default_singer()
        s = Sentence.from_text("测试", singer.id)
        project.add_sentence(s)
        assert len(project.sentences) == 1
        assert project.sentences[0].text == "测试"

    def test_move_sentence(self):
        project = Project()
        singer = project.get_default_singer()
        s1 = Sentence.from_text("1", singer.id)
        s2 = Sentence.from_text("2", singer.id)
        project.add_sentence(s1)
        project.add_sentence(s2)

        project.move_sentence(s2.id, 0)
        assert project.sentences[0].text == "2"
        assert project.sentences[1].text == "1"

    def test_get_all_timestamps(self):
        project = Project()
        singer = project.get_default_singer()
        s = Sentence.from_text("AB", singer.id)
        # A: count=1, B: count=2
        s.characters[0].add_timestamp(1000)
        s.characters[1].add_timestamp(2000)
        project.add_sentence(s)

        all_ts = project.get_all_timestamps()
        assert len(all_ts) == 2
        # (sentence_id, s_idx, c_idx, cp_idx, ts)
        assert all_ts[0][4] == 1000
        assert all_ts[1][4] == 2000

    def test_get_timing_statistics(self):
        project = Project()
        singer = project.get_default_singer()
        s = Sentence.from_text("AB", singer.id)
        # A(1) + B(2) = 3 total checkpoints
        s.characters[0].add_timestamp(1000)
        project.add_sentence(s)

        stats = project.get_timing_statistics()
        assert stats["total_lines"] == 1
        assert stats["total_chars"] == 2
        assert stats["total_timetags"] == 1
        assert stats["total_checkpoints"] == 3
        assert stats["timing_progress"] == "1/3"

    def test_validate(self):
        project = Project()
        assert project.is_valid()

        # Invalid singer_id
        s = Sentence.from_text("test", "nonexistent")
        # Bypass add_sentence validation
        project.sentences.append(s)
        assert not project.is_valid()
        errors = project.validate()
        assert any("singer_id" in e for e in errors)

    def test_compat_aliases(self):
        project = Project()
        singer = project.get_default_singer()
        s = Sentence.from_text("test", singer.id)
        project.add_line(s)
        assert len(project.lines) == 1
        assert project.get_line(s.id) == s

        project.remove_line(s.id)
        assert len(project.lines) == 0
