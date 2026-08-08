"""Regression tests for the live interface-settings preview."""

from __future__ import annotations

from PyQt6.QtCore import Qt


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
    assert values["checkpoint_marker"] == "◆"
    assert values["needs_guide_symbol"] == "!"

    page.card_current_line_font_size.setValue(32)
    page.card_lyrics_alignment.setCurrentIndex(0)
    assert page.preview.preview_values["current_line_font_size"] == 32
    assert page.preview.preview_values["alignment"] == "left"

    page.resize(1100, 750)
    page.show()
    qapp.processEvents()
    assert page.preview.parentWidget() is page.viewport()
    assert page.preview.testAttribute(
        Qt.WidgetAttribute.WA_TransparentForMouseEvents
    )
    assert page.preview.width() >= 420
    assert page.preview.height() >= 280

    geometry_before_scroll = page.preview.geometry()
    page.verticalScrollBar().setValue(page.verticalScrollBar().maximum())
    qapp.processEvents()
    assert page.preview.geometry() == geometry_before_scroll
    assert not page.preview.grab().isNull()
    page.close()


def test_preview_alignment_positions_are_ordered():
    from strange_uta_game.frontend.settings.ui_preview import InterfacePreview

    left = InterfacePreview._text_x("left", 100, 40, 500, 20)
    center = InterfacePreview._text_x("center", 100, 40, 500, 20)
    right = InterfacePreview._text_x("right", 100, 40, 500, 20)

    assert left < center < right
