"""Regression tests for choosing the F2 ruby editor style."""

from types import SimpleNamespace

from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.frontend.editor.timing_interface import EditorInterface
from strange_uta_game.frontend.settings.app_settings import AppSettings


def test_f2_ruby_editor_defaults_to_compact():
    assert AppSettings.DEFAULT_SETTINGS["ui"]["f2_ruby_editor_mode"] == "compact"


def test_f2_classic_mode_routes_to_legacy_editor():
    sentence = Sentence(
        singer_id="singer",
        characters=[Character(char="字", check_count=1)],
    )
    calls = []
    editor = SimpleNamespace(
        _project=Project(sentences=[sentence]),
        _f2_ruby_editor_mode="classic",
        _show_classic_char_editor=lambda line_idx, char_idx, target: calls.append(
            (line_idx, char_idx, target)
        ),
    )

    EditorInterface._on_char_edit_requested(editor, 0, 0)

    assert calls == [(0, 0, sentence)]


def test_f2_editor_setting_is_below_ruby_shortcut(qapp):
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QWidget
    from strange_uta_game.frontend.settings.sub_interfaces.shortcut import (
        ShortcutSubInterface,
    )

    page = ShortcutSubInterface()
    shortcut_card = page._shortcut_cards["timing_mode"]["edit_ruby"]
    group = shortcut_card.parentWidget()

    assert page.card_f2_ruby_editor.parentWidget() is group
    cards = group.findChildren(
        QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly
    )
    assert cards.index(page.card_f2_ruby_editor) == cards.index(shortcut_card) + 1
    page.close()


def test_rapid_f2_editor_switches_notify_once_after_popup_stack(qapp):
    from strange_uta_game.frontend.settings.sub_interfaces.shortcut import (
        ShortcutSubInterface,
    )

    page = ShortcutSubInterface()
    notifications = []
    page.set_change_callback(lambda: notifications.append(1))

    page.card_f2_ruby_editor.setCurrentIndex(1)
    page.card_f2_ruby_editor.setCurrentIndex(0)

    assert page._f2_editor_apply_pending is True
    assert notifications == []
    for _ in range(10):
        qapp.processEvents()
    assert page._f2_editor_apply_pending is False
    assert notifications == [1]
    page.close()
