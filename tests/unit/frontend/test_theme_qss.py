from strange_uta_game.frontend.theme import ThemeColors, theme


def _global_qss(is_dark: bool) -> str:
    previous_colors = theme._colors
    try:
        theme._colors = ThemeColors(is_dark)
        return theme._build_global_qss()
    finally:
        theme._colors = previous_colors


def test_dark_global_qss_styles_native_tooltips_and_headers():
    qss = _global_qss(True)

    assert "QToolTip" in qss
    assert "background-color: #2D2D2D" in qss
    assert "color: #FFFFFF" in qss
    assert "QHeaderView::section" in qss
    assert "color: #CCCCCC" in qss


def test_light_global_qss_keeps_native_tooltips_and_headers_light():
    qss = _global_qss(False)

    assert "QToolTip" in qss
    assert "background-color: #FFFFDC" in qss
    assert "color: #000000" in qss
    assert "QHeaderView::section" in qss
    assert "color: #333333" in qss
