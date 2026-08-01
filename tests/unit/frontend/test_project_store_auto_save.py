from __future__ import annotations

from PyQt6.QtCore import QCoreApplication

from strange_uta_game.frontend import project_store as project_store_module
from strange_uta_game.frontend.project_store import ProjectStore


def test_auto_save_is_deferred_while_predicate_is_true(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app
    store = ProjectStore()
    store._project = object()
    store._save_path = "song.sug"
    store.set_auto_save_defer_predicate(lambda: True)

    calls = []
    monkeypatch.setattr(
        store,
        "_launch_save",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    store._auto_save_timer.start()
    store._do_auto_save()

    assert calls == []
    assert store._auto_save_timer.isActive()


def test_auto_save_runs_when_defer_predicate_is_false(monkeypatch):
    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app
    store = ProjectStore()
    store._project = object()
    store._save_path = "song.sug"
    store.set_auto_save_defer_predicate(lambda: False)

    calls = []
    monkeypatch.setattr(
        store,
        "_launch_save",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    store._do_auto_save()

    assert len(calls) == 1
    assert calls[0][0][0] == "song.sug.autosave"
