"""Regression tests for the live interface-settings preview."""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QFontDatabase
from PyQt6.QtTest import QTest
from qfluentwidgets import FluentIcon

from strange_uta_game.frontend.settings.cards import FontSettingCard


class _SettingsStub:
    def __init__(self, values):
        self.values = values
        self.save_count = 0
        self._provider = None

    def get(self, path, default=None):
        return self.values.get(path, default)

    def set(self, path, value):
        self.values[path] = value

    def save(self):
        self.save_count += 1


def test_ui_settings_preview_loads_and_tracks_controls(qapp, monkeypatch):
    from strange_uta_game.frontend.settings.sub_interfaces.ui_settings import (
        UISubInterface,
    )

    page = UISubInterface()
    page.connect_signals()
    installed_families = [f for f in QFontDatabase.families() if f]
    if not installed_families:
        # 套件中段个别测试会破坏会话级 QApplication/字体库状态，使
        # QFontDatabase.families() 变空；此时字体卡无法有效测试，跳过
        # 而非误报失败（单独运行本测试不受影响）。
        pytest.skip("QFontDatabase 无已安装字体（会话字体库被污染）")
    interface_family = QFontDatabase.systemFont(
        QFontDatabase.SystemFont.GeneralFont
    ).family()
    if interface_family not in installed_families:
        # systemFont 偶尔返回未安装的通用别名（如 "Sans Serif"），
        # resolve_ui_font_override 会按设计折叠为空串；退回任一已安装字体。
        interface_family = installed_families[0]
    settings = _SettingsStub(
        {
            "ui.interface_font": interface_family,
            "ui.main_font": "Arial",
            "ui.ruby_font": "Arial",
            "ui.font_size": 16,
            "ui.current_line_font_size": 28,
            "ui.ruby_size": 11,
            "ui.ruby_spacing": 7,
            "ui.cp_size": 9,
            "ui.cp_spacing": 5,
            "ui.line_height_factor": 1.4,
            "ui.alignment_margin": 120,
            "ui.lyrics_alignment": "right",
            "ui.needs_guide_symbol": "!",
            "ui.needs_guide_size": 15,
            "ui.checkpoint_markers": {"cp_first_timed": "◆"},
        }
    )
    page.load_settings(settings)

    values = page.preview.preview_values
    assert page.card_interface_font.value() == interface_family
    applied_fonts: list[str] = []
    monkeypatch.setattr(
        "strange_uta_game.frontend.settings.sub_interfaces.ui_settings.set_ui_font_override",
        lambda family: applied_fonts.append(family) or family,
    )
    page._on_interface_font_changed(interface_family)
    assert applied_fonts == [interface_family]
    assert settings.save_count == 1
    assert settings.values["ui.interface_font"] == interface_family
    page.collect_settings(settings)
    assert settings.values["ui.interface_font"] == interface_family
    assert values["font_size"] == 16
    assert values["current_line_font_size"] == 28
    assert values["alignment"] == "right"
    assert values["checkpoint_markers"]["cp_first_timed"] == "◆"
    assert values["needs_guide_symbol"] == "!"

    page.card_current_line_font_size.setValue(32)
    page.card_lyrics_alignment.setCurrentIndex(0)
    assert page.preview.preview_values["current_line_font_size"] == 32
    assert page.preview.preview_values["alignment"] == "left"
    assert page.preview.canvas._alignment == "left"
    assert page.preview.canvas._font_current.pointSize() == 32

    page.resize(1100, 750)
    page.show()
    qapp.processEvents()
    assert page.preview.parentWidget() is page.viewport()
    assert page.preview.canvas.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    assert page.preview.width() >= 420
    assert page.preview.height() >= 380
    from strange_uta_game.frontend.editor.timing.karaoke_preview import KaraokePreview

    assert isinstance(page.preview.canvas, KaraokePreview)

    geometry_before_scroll = page.preview.geometry()
    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
    qapp.processEvents()
    assert page.preview.geometry() == geometry_before_scroll

    size_before_resize = page.preview.size()
    QTest.mousePress(
        page.preview,
        Qt.MouseButton.LeftButton,
        pos=QPoint(page.preview.width() - 2, page.preview.height() - 2),
    )
    QTest.mouseMove(
        page.preview,
        QPoint(page.preview.width() - 70, page.preview.height() - 60),
    )
    QTest.mouseRelease(
        page.preview,
        Qt.MouseButton.LeftButton,
        pos=QPoint(page.preview.width() - 2, page.preview.height() - 2),
    )
    qapp.processEvents()
    assert page.preview.user_resized
    assert page.preview.size() != size_before_resize
    assert page.preview.width() >= page.preview.MINIMUM_WIDTH
    assert page.preview.height() >= page.preview.MINIMUM_HEIGHT

    position_before_drag = page.preview.pos()
    QTest.mousePress(
        page.preview,
        Qt.MouseButton.LeftButton,
        pos=QPoint(80, 20),
    )
    QTest.mouseMove(page.preview, QPoint(20, 100))
    QTest.mouseRelease(
        page.preview,
        Qt.MouseButton.LeftButton,
        pos=QPoint(20, 100),
    )
    qapp.processEvents()
    assert page.preview.user_positioned
    assert page.preview.pos() != position_before_drag
    assert page.preview.x() >= 0
    assert page.preview.y() >= 0
    assert page.preview.geometry().right() <= page.viewport().rect().right()
    assert page.preview.geometry().bottom() <= page.viewport().rect().bottom()

    assert not page.preview.grab().isNull()
    page.close()


def test_interface_font_card_can_return_to_auto(qapp):
    card = FontSettingCard(FluentIcon.FONT, "UI font", "", allow_auto=True)
    card.setValue("Example Font")
    changed: list[str] = []
    card.value_changed.connect(changed.append)

    card._on_auto()

    assert card.value() == ""
    assert changed == [""]
    assert card.btn.text() == card.tr("按界面语言自动选择")
    card.close()


def test_preview_uses_real_renderer_and_ruby_sample(qapp):
    from strange_uta_game.frontend.editor.timing.karaoke_preview import KaraokePreview
    from strange_uta_game.frontend.settings.ui_preview import InterfacePreview

    preview = InterfacePreview()
    assert isinstance(preview.canvas, KaraokePreview)
    assert len(preview.canvas._project.sentences) == 3
    assert preview.canvas._visible_lines >= 3
    assert preview.canvas._project.sentences[1].characters[2].ruby.text == "うた"
    preview.close()
