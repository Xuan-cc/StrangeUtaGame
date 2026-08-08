from strange_uta_game.backend.domain import Character, Project, Sentence
from strange_uta_game.frontend.editor.timing.commands import CharacterSnapshotCommand


def test_character_snapshot_registration_preserves_live_identity():
    character = Character(char="字", check_count=1)
    project = Project(sentences=[Sentence(characters=[character], singer_id="s1")])
    before = Character(char="字", check_count=1)
    character.linked_to_next = True
    command = CharacterSnapshotCommand(
        project, 0, 0, before, character, "链接"
    )

    command.execute()

    assert project.sentences[0].characters[0] is character
    assert character.linked_to_next is True


def test_character_snapshot_undo_and_redo_only_target_one_character():
    first = Character(char="今", check_count=1)
    second = Character(char="日", check_count=1)
    project = Project(
        sentences=[Sentence(characters=[first, second], singer_id="s1")]
    )
    before = Character(char="今", check_count=1)
    first.linked_to_next = True
    command = CharacterSnapshotCommand(
        project, 0, 0, before, first, "链接"
    )
    command.execute()

    command.undo()
    assert project.sentences[0].characters[0].linked_to_next is False
    assert project.sentences[0].characters[1] is second

    command.redo()
    assert project.sentences[0].characters[0].linked_to_next is True
    assert project.sentences[0].characters[1] is second
