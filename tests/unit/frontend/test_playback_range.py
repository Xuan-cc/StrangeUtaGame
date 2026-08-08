from __future__ import annotations

from strange_uta_game.backend.application import CommandManager
from strange_uta_game.frontend.editor.timing.commands import PlaybackRangeCommand


def test_playback_range_command_supports_undo_and_redo():
    states: list[tuple[int | None, int | None]] = []
    manager = CommandManager()
    command = PlaybackRangeCommand(
        lambda start, end: states.append((start, end)),
        (None, None),
        (1_000, 2_000),
        "lock playback range",
    )

    manager.execute(command)
    manager.undo()
    manager.redo()

    assert states == [(1_000, 2_000), (None, None), (1_000, 2_000)]
