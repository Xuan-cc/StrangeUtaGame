from strange_uta_game.backend.domain import Character, Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    sentence_to_kasugamuki_romaji,
)


def test_export_uses_editor_sentence_level_romaji_context_without_mutating_source():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(
                char="待",
                ruby=Ruby(parts=[RubyPart(text="ま")]),
                check_count=1,
                singer_id="s1",
            ),
            Character(char="っ", check_count=1, singer_id="s1"),
            Character(char="て", check_count=1, singer_id="s1"),
        ],
    )

    result = sentence_to_kasugamuki_romaji(sentence)

    # The sokuon sees the following bare kana, exactly as it does after using
    # 注音管理 -> 转罗马音: ま / t / te (not ま / xtsu / te).
    assert result == "{待|ま>ma}{っ|>t}{て|>te}"
    assert sentence.characters[0].ruby.parts[0].text == "ま"
    assert sentence.characters[1].ruby is None
    assert sentence.characters[2].ruby is None


def test_export_uses_editor_particle_detection_for_bare_kana():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(
                char="私",
                ruby=Ruby(parts=[RubyPart(text="わ"), RubyPart(text="た"), RubyPart(text="し")]),
                check_count=3,
                singer_id="s1",
            ),
            Character(char="は", check_count=1, singer_id="s1"),
        ],
    )

    assert sentence_to_kasugamuki_romaji(sentence) == (
        "{私|わたし>watashi}{は|>wa}"
    )
