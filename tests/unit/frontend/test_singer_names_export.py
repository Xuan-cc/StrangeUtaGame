from types import SimpleNamespace

from strange_uta_game.frontend.singer.singer_interface import (
    build_singer_names_field,
)


def test_build_singer_names_field_uses_all_singers_and_ignores_groups():
    project = SimpleNamespace(
        singers=[
            SimpleNamespace(name="演唱者A", group="组合一"),
            SimpleNamespace(name="演唱者B", group="组合二"),
            SimpleNamespace(name='演唱者"C', group=""),
        ]
    )

    assert (
        build_singer_names_field(project)
        == '["演唱者A","演唱者B","演唱者\\"C"]'
    )


def test_build_singer_names_field_without_project_is_empty_array():
    assert build_singer_names_field(None) == "[]"
