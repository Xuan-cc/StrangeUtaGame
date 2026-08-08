"""界面字体随本地化语言切换的单元测试。"""

from __future__ import annotations

from PyQt6.QtGui import QFont

from strange_uta_game.frontend.font_utils import (
    resolve_ui_font_override,
    set_ui_font_override,
    set_ui_language,
    ui_font,
)
from strange_uta_game.frontend.localization import install_translators, localization


def test_ui_font_changes_with_language(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.font_utils.QFontDatabase.families",
        lambda: ["Microsoft YaHei UI", "Yu Gothic UI", "Segoe UI"],
    )

    set_ui_language("zh_CN")
    assert ui_font(10).families()[0] == "Microsoft YaHei UI"

    set_ui_language("ja_JP")
    assert ui_font(10).families()[0] == "Yu Gothic UI"

    set_ui_language("en_US")
    assert ui_font(10).families()[0] == "Segoe UI"


def test_language_manager_updates_application_font(qapp, monkeypatch):
    monkeypatch.setattr(
        "strange_uta_game.frontend.font_utils.QFontDatabase.families",
        lambda: ["Microsoft YaHei UI", "Yu Gothic UI", "Segoe UI"],
    )
    previous = QFont(qapp.font())
    previous_language = localization.current_code
    try:
        install_translators("ja_JP")
        assert qapp.font().families()[0] == "Yu Gothic UI"
        install_translators("en_US")
        assert qapp.font().families()[0] == "Segoe UI"
    finally:
        install_translators(previous_language)
        qapp.setFont(previous)


def test_user_override_precedes_language_font_and_keeps_fallbacks(qapp, monkeypatch):
    from PyQt6.QtWidgets import QLabel, QLineEdit, QTableWidget, QToolTip, QWidget
    from qfluentwidgets import (
        Action,
        FluentIcon,
        PushButton,
        RoundMenu,
        SettingCard,
        SettingCardGroup,
    )

    monkeypatch.setattr(
        "strange_uta_game.frontend.font_utils.QFontDatabase.families",
        lambda: ["Arial", "Yu Gothic UI", "Segoe UI"],
    )
    try:
        set_ui_language("ja_JP")
        existing_fluent_button = PushButton("existing")
        other_page = QWidget()
        other_page_label = QLabel("Other page label", other_page)
        other_page_input = QLineEdit(other_page)
        other_page_table = QTableWidget(0, 2, other_page)
        other_page_table.setHorizontalHeaderLabels(["First", "Second"])
        existing_menu = RoundMenu(parent=other_page)
        existing_menu.addAction(Action("Menu action"))
        settings_group = SettingCardGroup("Settings")
        settings_card = SettingCard(
            FluentIcon.SETTING,
            "Child title",
            "Child content",
            settings_group,
        )
        settings_group.addSettingCard(settings_card)
        assert set_ui_font_override("arial") == "Arial"
        assert ui_font(10).families()[:2] == ["Arial", "Yu Gothic UI"]
        assert qapp.font().families()[:2] == ["Arial", "Yu Gothic UI"]
        assert existing_fluent_button.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert "font: 14px 'Arial','Yu Gothic UI'" in settings_card.styleSheet()
        assert "font: 11px 'Arial','Yu Gothic UI'" in settings_card.styleSheet()
        assert other_page_label.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert other_page_input.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert other_page_table.horizontalHeader().font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert QToolTip.font().families()[:2] == ["Arial", "Yu Gothic UI"]
        assert existing_menu.view.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert PushButton("new").font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]
        assert RoundMenu().view.font().families()[:2] == [
            "Arial",
            "Yu Gothic UI",
        ]

        assert set_ui_font_override("missing font") == ""
        assert ui_font(10).families()[0] == "Yu Gothic UI"
        assert resolve_ui_font_override("auto") == ""
    finally:
        set_ui_font_override("")
