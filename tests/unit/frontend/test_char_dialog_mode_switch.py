"""Regression tests for normal/bulk character-dialog mode switching."""

from strange_uta_game.backend.domain import Character, Project, Ruby, RubyPart, Sentence
from strange_uta_game.frontend.editor.timing.bulk_change_dialog import BulkChangeDialog
from strange_uta_game.frontend.editor.timing.dialogs import (
    CharEditDialog,
    ModifyCharacterDialog,
)


def _linked_sentence() -> Sentence:
    return Sentence(
        singer_id="singer",
        characters=[
            Character(
                char="可",
                ruby=Ruby(parts=[RubyPart(text="か")]),
                check_count=1,
                linked_to_next=True,
            ),
            Character(
                char="愛",
                ruby=Ruby(parts=[RubyPart(text="わい")]),
                check_count=1,
            ),
        ],
    )


def test_f2_classic_switch_uses_real_linked_word_without_visual_plus(qapp):
    dialog = CharEditDialog(_linked_sentence(), 0)

    dialog.btn_switch_bulk.click()
    state = dialog.get_switch_state()

    assert dialog.switch_to_bulk_requested()
    assert state["search_word"] == "可愛"
    assert "+" not in state["search_word"]
    dialog.close()


def test_modify_switch_preserves_edits_in_bulk_mode(qapp):
    sentence = _linked_sentence()
    normal = ModifyCharacterDialog(sentence, 0, 1)
    normal.edit_new_chars.setText("恋愛")
    normal._char_rows[0][1].setText("こい")
    normal._char_rows[0][2].setText("2")
    normal._char_rows[0][3].setChecked(True)
    state = normal.get_switch_state()

    bulk = BulkChangeDialog(
        Project(sentences=[sentence]),
        initial_word=state["search_word"],
        initial_state=state,
    )

    assert bulk.edit_word.text() == "可愛"
    assert bulk.edit_new_chars.text() == "恋愛"
    assert bulk._char_rows[0][1].text() == "こい"
    assert bulk._char_rows[0][2].text() == "2"
    assert bulk._char_rows[0][3].isChecked()
    normal.close()
    bulk.close()


def test_bulk_switch_preserves_edits_in_modify_mode(qapp):
    sentence = _linked_sentence()
    bulk = BulkChangeDialog(Project(sentences=[sentence]), initial_word="可愛")
    bulk.edit_new_chars.setText("恋愛")
    bulk._char_rows[0][1].setText("こい")
    bulk.btn_switch_normal.click()
    state = bulk.get_switch_state()

    normal = ModifyCharacterDialog(sentence, 0, 1, initial_state=state)

    assert bulk.switch_to_normal_requested()
    assert normal.edit_new_chars.text() == "恋愛"
    assert normal._char_rows[0][1].text() == "こい"
    bulk.close()
    normal.close()
