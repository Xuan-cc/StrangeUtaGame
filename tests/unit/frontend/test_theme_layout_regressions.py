"""深色主题与嵌入式窄窗口布局的回归测试。"""

from types import SimpleNamespace

from PyQt6.QtCore import QObject, Qt, pyqtSignal


def test_export_settings_scroll_without_hiding_actions(qapp):
    """设置内容滚动，导出按钮留在固定操作区，歌手列表不再嵌套滚动。"""
    from strange_uta_game.frontend.export.export_interface import ExportInterface

    page = ExportInterface(embedded=True)

    assert page._settings_scroll.widget() is page._settings_widget
    assert page._settings_scroll.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert page._settings_scroll.isAncestorOf(page._singer_group)
    assert not page._settings_scroll.isAncestorOf(page.btn_export)
    assert not page._settings_scroll.isAncestorOf(page.btn_export_to_next)
    assert not hasattr(page, "_singer_scroll_area")
    assert page._settings_layout.itemAt(
        page._settings_layout.count() - 1
    ).spacerItem() is not None

    # 普通格式在高窗口中也必须贴顶紧凑排列，不能把多余高度摊进控件间距。
    page.format_list.setCurrentRow(0)
    page.resize(1200, 900)
    page.show()
    qapp.processEvents()

    assert page.line_output.geometry().top() < 100
    filename_gap = (
        page.line_filename.geometry().top() - page.line_output.geometry().bottom()
    )
    assert 0 <= filename_gap < 100
    assert page._settings_scroll.verticalScrollBar().maximum() == 0
    assert "background: transparent" in page._settings_scroll.styleSheet()
    assert "background: transparent" in page._settings_scroll.viewport().styleSheet()

    singers = [
        SimpleNamespace(
            id=f"singer-{index}",
            name=f"演唱者 {index}",
            color="#ff6699",
            enabled=True,
            is_default=index == 0,
        )
        for index in range(12)
    ]
    page.set_project(
        SimpleNamespace(
            singers=singers,
            sentences=[
                SimpleNamespace(singer_id=singer.id, characters=[])
                for singer in singers
            ],
        )
    )

    nicokara_row = next(
        row
        for row in range(page.format_list.count())
        if "nicokara"
        in page.format_list.item(row)
        .data(Qt.ItemDataRole.UserRole)
        .lower()
    )
    page.format_list.setCurrentRow(nicokara_row)
    page.resize(900, 360)
    qapp.processEvents()

    assert len(page._singer_checkboxes) == len(singers)
    assert page._singer_checkbox_container.count() == len(singers)
    assert page._settings_scroll.verticalScrollBar().maximum() > 0
    assert page.btn_export.isVisible()
    assert page.btn_export_to_next.isVisible()

    page.close()


class _ThemeStub(QObject):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.is_dark = False

    def __getattr__(self, name):
        from strange_uta_game.frontend.theme import ThemeColors

        return getattr(ThemeColors(self.is_dark), name)


def test_timing_footer_restyles_as_one_unit(monkeypatch, qapp):
    """切换主题时刷新全部底栏标签，同时保留当前播放/打轴状态。"""
    import strange_uta_game.frontend.editor.timing_interface as timing_module

    theme_stub = _ThemeStub()
    monkeypatch.setattr(timing_module, "theme", theme_stub)
    monkeypatch.setattr(
        timing_module.EditorInterface,
        "_init_keysound",
        lambda self: None,
    )

    editor = timing_module.EditorInterface()
    assert "#e0e0e0" in editor.lbl_mode.styleSheet()

    theme_stub.is_dark = True
    theme_stub.changed.emit()

    assert "#3e3e3e" in editor.lbl_mode.styleSheet()
    assert "#e6e6e6" in editor.lbl_mode.styleSheet()
    assert "#cccccc" in editor.lbl_status.styleSheet()
    assert "#cccccc" in editor.lbl_line_info.styleSheet()
    assert "#cccccc" in editor.lbl_progress.styleSheet()
    assert "#ff6b6b" in editor.lbl_needs_guide.styleSheet()
    assert "#666666" in editor.lbl_shortcut_hint.styleSheet()

    editor._timing_service = SimpleNamespace(is_playing=lambda: True)
    editor._update_mode_indicator()
    theme_stub.changed.emit()

    assert editor.lbl_mode.text() == "模式：打轴"
    assert "#ffd54f" in editor.lbl_mode.styleSheet()
    assert "#333333" in editor.lbl_mode.styleSheet()

    editor.close()
