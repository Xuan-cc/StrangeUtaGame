from strange_uta_game.backend.domain import Character, Sentence
from strange_uta_game.backend.infrastructure.parsers.annotated_text import (
    sentence_to_timed_line,
)
from strange_uta_game.frontend.editor.timing.dialogs import insert_guide_before


def _sentence_with_target(target_ts):
    target = Character(
        char="歌",
        singer_id="singer_1",
        check_count=1,
        timestamps=[target_ts],
    )
    return Sentence(singer_id="singer_1", characters=[target]), target


def test_reverse_insert_marks_last_guide_with_target_start_ts():
    sentence, target = _sentence_with_target(44_010)

    result = insert_guide_before(
        sentence, 0, "●", 3, 1000, reverse=True, fill_gap=False
    )

    assert result["ok"] is True
    guides = sentence.characters[:3]
    assert [c.char for c in guides] == ["●", "●", "●"]
    assert [c.timestamps for c in guides] == [[43_010], [42_010], [41_010]]
    assert guides[-1].is_sentence_end is True
    assert guides[-1].sentence_end_ts == 44_010
    assert not any(c.is_sentence_end for c in guides[:-1])
    assert target.is_sentence_end is False


def test_non_reverse_insert_keeps_guides_unmarked():
    sentence, _ = _sentence_with_target(44_010)

    result = insert_guide_before(
        sentence, 0, "●", 3, 1000, reverse=False, fill_gap=False
    )

    assert result["ok"] is True
    guides = sentence.characters[:3]
    assert [c.timestamps for c in guides] == [[41_010], [42_010], [43_010]]
    assert not any(c.is_sentence_end for c in guides)


def test_reverse_single_guide_serializes_with_trailing_end_token():
    sentence, _ = _sentence_with_target(45_010)

    result = insert_guide_before(
        sentence, 0, "○", 1, 1000, reverse=True, fill_gap=False
    )

    assert result["ok"] is True
    line, _ = sentence_to_timed_line(sentence.characters)
    assert "[00:44.01]○[>00:45.01]" in line


def test_reverse_without_target_timestamp_skips_end_marker():
    target = Character(char="歌", singer_id="singer_1", check_count=0)
    sentence = Sentence(singer_id="singer_1", characters=[target])

    result = insert_guide_before(
        sentence, 0, "●", 2, 1000, reverse=True, fill_gap=False
    )

    assert result["ok"] is True
    guides = sentence.characters[:2]
    assert not any(c.timestamps for c in guides)
    assert not any(c.is_sentence_end for c in guides)
