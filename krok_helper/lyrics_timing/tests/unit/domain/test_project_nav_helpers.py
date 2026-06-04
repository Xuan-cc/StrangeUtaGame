"""Project A3/A4 domain helpers 单元测试：

- :py:meth:`Project.find_prev_line_with_checkpoints`
- :py:meth:`Project.collect_all_timestamp_ms`
"""

from strange_uta_game.backend.domain import (
    Character,
    Project,
    Sentence,
    Singer,
)


def _make_char(c: str, *, checks: int = 0, sentence_end: bool = False,
               timestamps: list[int] | None = None) -> Character:
    ch = Character(char=c, check_count=checks, singer_id="s1",
                   is_sentence_end=sentence_end)
    if timestamps:
        # all_timestamps 由 timestamps 列表派生；直接设置底层字段以保持测试聚焦
        ch.timestamps = list(timestamps)
    return ch


def _make_project(lines: list[list[Character]]) -> Project:
    singer = Singer(name="s", color="#000000", is_default=True,
                    display_priority=0, backend_number=1)
    sentences = [
        Sentence(singer_id=singer.id, characters=chars) for chars in lines
    ]
    return Project(sentences=sentences, singers=[singer])


class TestFindPrevLineWithCheckpoints:
    def test_returns_minus_one_when_no_prior_checkpoint(self):
        project = _make_project([
            [_make_char("あ")],
            [_make_char("い")],
        ])
        assert project.find_prev_line_with_checkpoints(1) == -1

    def test_skips_empty_lines_upward(self):
        project = _make_project([
            [_make_char("赤", checks=1)],
            [_make_char("い")],  # 空 checkpoint
            [_make_char("花")],  # 空 checkpoint
            [_make_char("が", checks=2)],  # current
        ])
        assert project.find_prev_line_with_checkpoints(3) == 0

    def test_sentence_end_counts_as_checkpoint(self):
        project = _make_project([
            [_make_char("あ", sentence_end=True)],
            [_make_char("い")],
        ])
        assert project.find_prev_line_with_checkpoints(1) == 0

    def test_returns_immediate_predecessor_when_has_checkpoint(self):
        project = _make_project([
            [_make_char("a", checks=1)],
            [_make_char("b", checks=3)],
            [_make_char("c")],
        ])
        assert project.find_prev_line_with_checkpoints(2) == 1

    def test_current_idx_zero_returns_minus_one(self):
        project = _make_project([[_make_char("a", checks=1)]])
        assert project.find_prev_line_with_checkpoints(0) == -1


class TestCollectAllTimestampMs:
    def test_empty_project_returns_empty(self):
        project = _make_project([])
        assert project.collect_all_timestamp_ms() == []

    def test_no_timestamps_returns_empty(self):
        project = _make_project([[_make_char("a"), _make_char("b")]])
        assert project.collect_all_timestamp_ms() == []

    def test_preserves_order_flat(self):
        project = _make_project([
            [
                _make_char("a", timestamps=[100, 200]),
                _make_char("b", timestamps=[300]),
            ],
            [_make_char("c", timestamps=[50])],
        ])
        # 按 sentence→character→checkpoint 顺序展开，不排序
        assert project.collect_all_timestamp_ms() == [100, 200, 300, 50]
