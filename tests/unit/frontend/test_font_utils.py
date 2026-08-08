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
    from qfluentwidgets import PushButton

    monkeypatch.setattr(
        "strange_uta_game.frontend.font_utils.QFontDatabase.families",
        lambda: ["Custom UI", "Yu Gothic UI", "Segoe UI"],
    )
    try:
        set_ui_language("ja_JP")
        existing_fluent_button = PushButton("existing")
        assert set_ui_font_override("custom ui") == "Custom UI"
        assert ui_font(10).families()[:2] == ["Custom UI", "Yu Gothic UI"]
        assert qapp.font().families()[:2] == ["Custom UI", "Yu Gothic UI"]
        assert existing_fluent_button.font().families()[:2] == [
            "Custom UI",
            "Yu Gothic UI",
        ]
        assert PushButton("new").font().families()[:2] == [
            "Custom UI",
            "Yu Gothic UI",
        ]

        assert set_ui_font_override("missing font") == ""
        assert ui_font(10).families()[0] == "Yu Gothic UI"
        assert resolve_ui_font_override("auto") == ""
    finally:
        set_ui_font_override("")
