"""界面字体随本地化语言切换的单元测试。"""

from __future__ import annotations

from PyQt6.QtGui import QFont

from strange_uta_game.frontend.font_utils import set_ui_language, ui_font
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
