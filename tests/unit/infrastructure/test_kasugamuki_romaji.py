from strange_uta_game.backend.domain import Character, Ruby, RubyPart, Sentence
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    sentence_to_kasugamuki,
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

    # The sokuon and its following kana are one pronunciation unit.
    assert result == "{待|ま>ma}っ{て|>tte}"
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


def test_export_keeps_digraph_in_one_romaji_annotation():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(char="き", check_count=1, singer_id="s1"),
            Character(char="ゃ", check_count=1, singer_id="s1"),
        ],
    )

    assert sentence_to_kasugamuki_romaji(sentence) == "{きゃ|>kya}"


def test_sokuon_is_plain_and_keeps_its_own_timestamp():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(
                char="っ", check_count=1, timestamps=[100], singer_id="s1"
            ),
            Character(
                char="て", check_count=1, timestamps=[200], singer_id="s1"
            ),
        ],
    )

    assert sentence_to_kasugamuki_romaji(sentence) == (
        "[00:00:10]っ{て|>[00:00:20]tte}"
    )


def test_linked_word_is_wrapped_as_one_kasugamuki_block():
    sentence = Sentence(
        singer_id="s1",
        characters=[
            Character(
                char="明",
                ruby=Ruby(
                    parts=[
                        RubyPart(text="あ"),
                        RubyPart(text="し"),
                        RubyPart(text="た"),
                    ]
                ),
                check_count=3,
                singer_id="s1",
                linked_to_next=True,
            ),
            Character(char="日", check_count=0, singer_id="s1"),
        ],
    )

    assert sentence_to_kasugamuki(sentence) == "{明日|あした}"
    assert sentence_to_kasugamuki_romaji(sentence) == "{明日|あした>ashita}"
