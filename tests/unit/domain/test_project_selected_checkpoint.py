"""Project checkpoint 全局选中单选不变量测试（Issue #9 第十六批）。

覆盖：
- I1: 全局唯一选中
- I2: select_default_checkpoint 选中 (0,0,0)
- I3: 增删对称 — 每次 set 必先清旧
- Character.selected_checkpoint_idx 默认 None
- 无效定位返回 False
"""

from strange_uta_game.backend.domain.entities import Sentence, Singer
from strange_uta_game.backend.domain.models import Character
from strange_uta_game.backend.domain.project import Project


def _make_project_with_lines(n_lines: int = 2, chars_per_line: int = 3) -> Project:
    singer = Singer(name="默认", color="#FF6B6B", is_default=True, backend_number=1)
    sentences = []
    for _ in range(n_lines):
        chars = [Character(char="あ", singer_id=singer.id) for _ in range(chars_per_line)]
        sentences.append(Sentence(singer_id=singer.id, characters=chars))
    return Project(singers=[singer], sentences=sentences)


class TestSelectedCheckpointInvariants:
    def test_character_default_selected_is_none(self):
        ch = Character(char="a", singer_id="s1")
        assert ch.selected_checkpoint_idx is None

    def test_set_single_selection(self):
        p = _make_project_with_lines()
        assert p.set_selected_checkpoint(0, 1, 0) is True
        assert p.sentences[0].characters[1].selected_checkpoint_idx == 0

    def test_global_single_select_invariant(self):
        """I1: 设新前必清旧——全局最多一个选中。"""
        p = _make_project_with_lines(n_lines=3, chars_per_line=2)
        p.set_selected_checkpoint(0, 0, 0)
        p.set_selected_checkpoint(2, 1, 0)

        selected_count = sum(
            1
            for s in p.sentences
            for c in s.characters
            if c.selected_checkpoint_idx is not None
        )
        assert selected_count == 1
        assert p.sentences[2].characters[1].selected_checkpoint_idx == 0
        assert p.sentences[0].characters[0].selected_checkpoint_idx is None

    def test_invalid_line_returns_false(self):
        p = _make_project_with_lines()
        assert p.set_selected_checkpoint(99, 0, 0) is False
        # 无效调用不应影响已有选中态
        p.set_selected_checkpoint(0, 0, 0)
        assert p.set_selected_checkpoint(-1, 0, 0) is False
        assert p.sentences[0].characters[0].selected_checkpoint_idx == 0

    def test_invalid_char_returns_false(self):
        p = _make_project_with_lines()
        assert p.set_selected_checkpoint(0, 99, 0) is False

    def test_get_selected_checkpoint(self):
        p = _make_project_with_lines()
        assert p.get_selected_checkpoint() is None

        p.set_selected_checkpoint(1, 2, 0)
        assert p.get_selected_checkpoint() == (1, 2, 0)

    def test_clear_selected_checkpoint(self):
        p = _make_project_with_lines()
        p.set_selected_checkpoint(0, 0, 0)
        p.clear_selected_checkpoint()
        assert p.get_selected_checkpoint() is None

    def test_select_default_checkpoint(self):
        """I2: 项目打开后选中 (0,0,0)。"""
        p = _make_project_with_lines()
        assert p.select_default_checkpoint() is True
        assert p.get_selected_checkpoint() == (0, 0, 0)

    def test_select_default_empty_project(self):
        singer = Singer(name="默认", color="#FF6B6B", is_default=True, backend_number=1)
        p = Project(singers=[singer], sentences=[])
        assert p.select_default_checkpoint() is False
        assert p.get_selected_checkpoint() is None

    def test_select_default_empty_line(self):
        singer = Singer(name="默认", color="#FF6B6B", is_default=True, backend_number=1)
        empty_sentence = Sentence(singer_id=singer.id, characters=[])
        p = Project(singers=[singer], sentences=[empty_sentence])
        assert p.select_default_checkpoint() is False
