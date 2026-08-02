from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from strange_uta_game.backend.infrastructure.parsers.text_splitter import CharType
from strange_uta_game.frontend.editor.fulltext_interface import DeleteRubyByTypeDialog


def test_space_is_a_single_combined_delete_option(qtbot):
    dialog = DeleteRubyByTypeDialog(initial_types=["full_space"])
    qtbot.addWidget(dialog)

    options = {char_type: checkbox for char_type, checkbox in dialog._checkboxes}

    assert CharType.SPACE in options
    assert CharType.FULL_SPACE not in options
    assert options[CharType.SPACE].isChecked()
    assert dialog.selected_type_names() == ["space"]
