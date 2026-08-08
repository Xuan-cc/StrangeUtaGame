from __future__ import annotations

from pathlib import Path

from strange_uta_game.frontend.editor.timing.file_loader import FileLoader
from strange_uta_game.frontend.editor.timing.toolbar import EditorToolBar


class _Settings:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.save_count = 0

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value

    def save(self):
        self.save_count += 1


class _SettingInterface:
    def __init__(self, settings):
        self._settings = settings

    def get_settings(self):
        return self._settings


class _Toolbar:
    def __init__(self):
        self.recent_paths = None

    def set_recent_projects(self, paths):
        self.recent_paths = list(paths)


class _Editor:
    def __init__(self, settings):
        self._project = None
        self._store = None
        self._timing_service = None
        self.toolbar = _Toolbar()
        self._setting_interface = _SettingInterface(settings)

    def _get_setting_interface(self):
        return self._setting_interface

    @staticmethod
    def tr(text):
        return text


def test_recent_projects_filters_missing_duplicates_and_wrong_types(tmp_path):
    existing = tmp_path / "song.sug"
    existing.write_text("{}", encoding="utf-8")
    wrong_type = tmp_path / "lyrics.txt"
    wrong_type.write_text("lyrics", encoding="utf-8")
    missing = tmp_path / "missing.sug"
    settings = _Settings({
        "recent_projects": [
            str(existing),
            str(existing),
            str(wrong_type),
            str(missing),
            None,
        ]
    })

    loader = FileLoader(_Editor(settings))

    assert loader.recent_projects() == [str(existing.absolute())]
    assert settings.values["recent_projects"] == [str(existing.absolute())]
    assert settings.save_count == 1


def test_recent_projects_repairs_non_list_setting():
    settings = _Settings({"recent_projects": "not-a-list"})
    loader = FileLoader(_Editor(settings))

    assert loader.recent_projects() == []
    assert settings.values["recent_projects"] == []
    assert settings.save_count == 1


def test_record_recent_project_moves_it_to_front_and_limits_list(tmp_path):
    files = []
    for index in range(FileLoader._MAX_RECENT_PROJECTS + 1):
        path = tmp_path / f"{index}.sug"
        path.write_text("{}", encoding="utf-8")
        files.append(path)
    settings = _Settings({"recent_projects": [str(path) for path in files[:-1]]})
    editor = _Editor(settings)
    loader = FileLoader(editor)

    loader._record_recent_project(str(files[-1]))

    recent = settings.values["recent_projects"]
    assert recent[0] == str(files[-1].absolute())
    assert len(recent) == FileLoader._MAX_RECENT_PROJECTS
    assert editor.toolbar.recent_paths == recent


def test_clear_recent_projects_updates_settings_and_toolbar(tmp_path):
    project = Path(tmp_path) / "song.sug"
    project.write_text("{}", encoding="utf-8")
    settings = _Settings({"recent_projects": [str(project)]})
    editor = _Editor(settings)
    loader = FileLoader(editor)

    loader.clear_recent_projects()

    assert settings.values["recent_projects"] == []
    assert editor.toolbar.recent_paths == []


def test_recent_projects_update_only_recent_submenu(qtbot):
    toolbar = EditorToolBar()
    qtbot.addWidget(toolbar)
    original_layout = toolbar.layout()
    original_offset_editor = toolbar.edit_offset
    original_file_menu = toolbar.btn_load.menu()
    original_recent_menu = toolbar._recent_menu

    toolbar.set_recent_projects([r"C:\projects\song.sug"])

    assert toolbar.layout() is original_layout
    assert toolbar.edit_offset is original_offset_editor
    assert toolbar.btn_load.menu() is original_file_menu
    assert toolbar._recent_menu is original_recent_menu
    assert toolbar._recent_project_paths == [r"C:\projects\song.sug"]


def test_repeated_recent_project_updates_reuse_the_same_menus(qtbot):
    toolbar = EditorToolBar()
    qtbot.addWidget(toolbar)
    file_menu = toolbar.btn_load.menu()
    recent_menu = toolbar._recent_menu

    for index in range(20):
        toolbar.set_recent_projects([fr"C:\projects\song-{index}.sug"])

    assert toolbar.btn_load.menu() is file_menu
    assert toolbar._recent_menu is recent_menu
    assert recent_menu.actions()[0].text().startswith("song-19.sug")
