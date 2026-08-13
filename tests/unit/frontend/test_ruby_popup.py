from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QDialog

from strange_uta_game.backend.domain import Character, Ruby, RubyPart
from strange_uta_game.frontend.editor.timing.ruby_popup import RubyEditPopup


def _popup(monkeypatch, character=None, **kwargs):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "direct",
    )
    return RubyEditPopup(character or _character(), **kwargs)


def _character(*, linked: bool = False) -> Character:
    return Character(
        char="今",
        ruby=Ruby(parts=[RubyPart(text="きょ"), RubyPart(text="う")]),
        check_count=2,
        linked_to_next=linked,
    )


def test_apply_updates_ruby_and_link_state(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "direct",
    )
    monkeypatch.setattr(
        "strange_uta_game.backend.infrastructure.parsers.inline_format.get_ruby_pause_char",
        lambda: " ",
    )
    character = _character()
    popup = RubyEditPopup(character, can_link_next=True)

    popup.edit_ruby.setText("い,ま")
    popup._toggle_link(True)
    popup._apply()

    assert popup.result() == QDialog.DialogCode.Accepted
    assert popup.was_modified()
    assert [part.text for part in character.ruby.parts] == ["い", "ま"]
    assert character.linked_to_next is True


def test_unchanged_submit_preserves_existing_ruby_object(qapp, monkeypatch):
    character = _character(linked=True)
    original_ruby = character.ruby
    popup = _popup(monkeypatch, character, can_link_next=True)

    popup._apply()

    assert not popup.was_modified()
    assert character.ruby is original_ruby
    assert character.linked_to_next is True


def test_last_character_cannot_link_next(qapp, monkeypatch):
    character = _character(linked=True)
    popup = _popup(monkeypatch, character, can_link_next=False)

    popup._toggle_link(True)
    popup._apply()

    assert popup.btn_link_next.isEnabled() is False
    assert character.linked_to_next is False
    assert popup.was_modified()


def test_outside_dismissal_saves(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "direct",
    )
    monkeypatch.setattr(
        "strange_uta_game.backend.infrastructure.parsers.inline_format.get_ruby_pause_char",
        lambda: " ",
    )
    character = _character()
    popup = RubyEditPopup(character, can_link_next=True)
    popup.edit_ruby.setText("い,ま")
    popup._toggle_link(True)

    popup.reject()  # Qt.Popup uses rejection when an outside click dismisses it.

    assert popup.result() == QDialog.DialogCode.Accepted
    assert [part.text for part in character.ruby.parts] == ["い", "ま"]
    assert character.linked_to_next is True


def test_escape_cancels_without_changes(qapp, monkeypatch):
    character = _character()
    original_ruby = character.ruby
    popup = _popup(monkeypatch, character, can_link_next=True)
    popup.edit_ruby.setText("いま")
    popup._toggle_link(True)

    popup.keyPressEvent(
        QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
    )

    assert popup.result() == QDialog.DialogCode.Rejected
    assert popup.was_modified() is False
    assert character.ruby is original_ruby
    assert character.linked_to_next is False


def test_link_button_uses_compact_label(qapp, monkeypatch):
    popup = _popup(monkeypatch, can_link_next=True)

    assert popup.btn_link_next.text() == "链接"
    popup._toggle_link(True)
    assert popup.btn_link_next.text() == "链接"
    assert popup.btn_link_next.isChecked() is True


def test_input_expands_only_when_text_needs_more_space(qapp, monkeypatch):
    popup = _popup(monkeypatch, can_link_next=True)
    popup.edit_ruby.setText("あ")
    compact_width = popup.width()

    popup.edit_ruby.setText("とてもながいふりがなの入力テスト")

    assert popup.width() > compact_width
    assert popup.edit_ruby.width() <= popup._INPUT_MAX_WIDTH


def test_link_button_delegates_each_toggle_and_tracks_character(qapp, monkeypatch):
    character = _character()
    calls = []

    def toggle_like_f3():
        character.linked_to_next = not character.linked_to_next
        calls.append(character.linked_to_next)

    popup = _popup(
        monkeypatch,
        character,
        can_link_next=True,
        link_toggle_callback=toggle_like_f3,
    )

    popup._toggle_link(True)
    assert calls == [True]
    assert popup.btn_link_next.isChecked() is True

    popup._toggle_link(False)
    assert calls == [True, False]
    assert popup.btn_link_next.isChecked() is False


def test_ruby_input_accepts_unrestricted_ime_text(qapp, monkeypatch):
    popup = _popup(monkeypatch, can_link_next=True)

    assert popup.edit_ruby.testAttribute(
        Qt.WidgetAttribute.WA_InputMethodEnabled
    )
    assert popup.edit_ruby.inputMethodHints() == Qt.InputMethodHint.ImhNone

    popup.edit_ruby.setText("かな・拼音・한글・ruby")
    assert popup.edit_ruby.text() == "かな・拼音・한글・ruby"


def test_link_button_expands_for_wider_translation(qapp, monkeypatch):
    popup = _popup(monkeypatch, can_link_next=True)
    compact_width = popup.width()

    popup.btn_link_next.setText("リンクする")
    popup._resize_link_button()

    expected = popup.btn_link_next.fontMetrics().horizontalAdvance("リンクする") + 28
    assert popup.btn_link_next.width() >= expected
    assert popup.width() > compact_width


def test_direct_mode_displays_existing_parts_with_commas(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "direct",
    )

    popup = RubyEditPopup(_character(), can_link_next=True)

    assert popup.edit_ruby.text() == "きょ,う"


def test_mora_mode_displays_existing_ruby_as_plain_text(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "mora",
    )

    popup = RubyEditPopup(_character(), can_link_next=True)

    assert popup.edit_ruby.text() == "きょう"


def test_char_mode_applies_using_current_mode(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "char",
    )
    monkeypatch.setattr(
        "strange_uta_game.backend.infrastructure.parsers.inline_format.get_ruby_pause_char",
        lambda: " ",
    )
    character = _character()
    popup = RubyEditPopup(character, can_link_next=True)
    popup.edit_ruby.setText("いま")

    popup._apply()

    assert [part.text for part in character.ruby.parts] == ["い", "ま"]


def test_comma_in_automatic_mode_forces_direct_and_saves_mode(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._get_ruby_split_mode",
        lambda: "mora",
    )
    saved_modes = []
    monkeypatch.setattr(
        "strange_uta_game.frontend.editor.timing.dialogs._set_ruby_split_mode",
        saved_modes.append,
    )
    monkeypatch.setattr(
        "strange_uta_game.backend.infrastructure.parsers.inline_format.get_ruby_pause_char",
        lambda: " ",
    )
    character = _character()
    popup = RubyEditPopup(character, can_link_next=True)
    popup.edit_ruby.setText("い,ま")

    popup._apply()

    assert saved_modes == ["direct"]
    assert popup._split_mode == "direct"
    assert [part.text for part in character.ruby.parts] == ["い", "ま"]
