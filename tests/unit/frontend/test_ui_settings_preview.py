"""Regression tests for the live interface-settings preview."""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest


class _SettingsStub:
    def __init__(self, values):
        self.values = values

    def get(self, path, default=None):
        return self.values.get(path, default)


def test_ui_settings_preview_loads_and_tracks_controls(qapp):
    from strange_uta_game.frontend.settings.sub_interfaces.ui_settings import (
        UISubInterface,
    )

    page = UISubInterface()
    page.connect_signals()
    page.load_settings(
        _SettingsStub(
            {
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
    )

    values = page.preview.preview_values
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


def test_preview_uses_real_renderer_and_ruby_sample(qapp):
    from strange_uta_game.frontend.editor.timing.karaoke_preview import KaraokePreview
    from strange_uta_game.frontend.settings.ui_preview import InterfacePreview

    preview = InterfacePreview()
    assert isinstance(preview.canvas, KaraokePreview)
    assert len(preview.canvas._project.sentences) == 3
    assert preview.canvas._visible_lines >= 3
    assert preview.canvas._project.sentences[1].characters[2].ruby.text == "うた"
    preview.close()
