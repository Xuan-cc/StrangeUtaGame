from pathlib import Path

from strange_uta_game.backend.application.project_import_service import (
    ProjectImportService,
)
from strange_uta_game.backend.infrastructure.parsers.kasugamuki_format import (
    is_kasugamuki_content,
    sentences_from_kasugamuki,
    sentences_to_kasugamuki,
    strip_krl_config,
)
from strange_uta_game.frontend.editor.timing.lyric_loader import (
    detect_lyric_format,
    parse_lyric_content,
)

SINGER_ID = "singer-1"


def test_strip_foreign_config_block_with_nested_data_and_braces_in_string():
    content = '''config {
        "style": {"font": "Noto {Sans}"},
        "groups": [{"name": "title"}]
    }

{秒|[00:11:70]びょ[00:11:81]う>[00:11:70]byo[00:11:81]u}[00:12:00]
'''

    stripped = strip_krl_config(content)

    assert stripped.startswith("{秒|")
    assert "font" not in stripped
    assert is_kasugamuki_content(content)
    assert detect_lyric_format(content) == "krl"


def test_parse_krl_pronunciation_romaji_timing_and_line_release():
    content = (
        "config {\"unused\": true}\n\n"
        "“{ア|>[00:10:64]a}{知|[00:11:03]し>[00:11:03]shi}”[00:11:65]\n"
        "{秒|[00:11:70]びょ[00:11:81]う>[00:11:70]byo[00:11:81]u}"
        "{で|>[00:12:01]de}[00:12:69]\n"
    )

    sentences = sentences_from_kasugamuki(content, SINGER_ID)

    assert [sentence.text for sentence in sentences] == ["“ア知”", "秒で"]
    assert sentences[0].characters[1].ruby is None
    assert sentences[0].characters[1].timestamps == [10640]
    assert sentences[0].characters[2].ruby.text == "し"
    assert sentences[0].characters[2].timestamps == [11030]
    assert sentences[0].characters[-1].is_sentence_end
    assert sentences[0].characters[-1].sentence_end_ts == 11650

    seconds = sentences[1].characters[0]
    assert [part.text for part in seconds.ruby.parts] == ["びょ", "う"]
    assert seconds.timestamps == [11700, 11810]
    assert sentences[1].characters[-1].sentence_end_ts == 12690


def test_parse_lyric_content_routes_krl_before_generic_inline_parser():
    content = (
        'config {"characterProfiles": {}}\n'
        "{利|[00:12:74]り>[00:12:74]ri}[00:12:90]"
    )

    sentences, is_nicokara, singers, metadata = parse_lyric_content(
        content, SINGER_ID
    )

    assert not is_nicokara
    assert singers == []
    assert metadata == {"format": "krl"}
    assert sentences[0].text == "利"
    assert sentences[0].characters[0].ruby.text == "り"
    assert sentences[0].characters[0].timestamps == [12740]
    assert sentences[0].characters[0].sentence_end_ts == 12900


def test_project_import_service_accepts_krl_extension(monkeypatch):
    content = (
        'config {"unused": {"nested": true}}\n'
        "{秒|[00:11:70]びょ[00:11:81]う>[00:11:70]byo[00:11:81]u}[00:12:00]"
    )
    monkeypatch.setattr(Path, "read_text", lambda self, encoding: content)

    sentences, metadata = ProjectImportService.load_lyrics_and_meta_from_file(
        "lyrics.krl", SINGER_ID
    )

    assert metadata == {"format": "krl"}
    assert sentences[0].text == "秒"
    assert sentences[0].characters[0].timestamps == [11700, 11810]


def test_linked_group_primary_subtitle_and_ruby_round_trip():
    source = (
        "前{明日|[00:01:00]あ[00:01:20]し[00:01:40]た>"
        "[00:01:00]a[00:01:20]shi[00:01:40]ta}後[00:02:00]"
    )

    sentences = sentences_from_kasugamuki(source, SINGER_ID)
    sentence = sentences[0]

    assert sentence.text == "前明日後"
    assert [character.char for character in sentence.characters] == [
        "前", "明", "日", "後",
    ]
    assert [character.linked_to_next for character in sentence.characters] == [
        False, True, False, False,
    ]
    assert sentence.characters[1].ruby.text == "あした"
    assert sentence.characters[2].ruby is None
    exported = sentences_to_kasugamuki(sentences)
    assert exported == (
        "前{明日|[00:01:00]あ[00:01:20]し[00:01:40]た}後[00:02:00]"
    )

    reparsed = sentences_from_kasugamuki(exported, SINGER_ID)[0]
    assert [character.char for character in reparsed.characters] == [
        "前", "明", "日", "後",
    ]
    assert [character.linked_to_next for character in reparsed.characters] == [
        False, True, False, False,
    ]
    assert reparsed.characters[1].ruby.text == "あした"


def test_primary_round_trip_preserves_empty_and_space_only_subtitle_lines():
    source = (
        "{空|[00:01:00]そら}\n"
        "\n"
        " \n"
        "{白|[00:02:00]しろ}[00:02:50]"
    )

    sentences = sentences_from_kasugamuki(source, SINGER_ID)

    assert [sentence.text for sentence in sentences] == ["空", "", " ", "白"]
    assert sentences_to_kasugamuki(sentences) == source


def test_discarded_romaji_only_group_does_not_invent_linked_word():
    sentences = sentences_from_kasugamuki(
        "{きゃ|>[00:01:00]kya}", SINGER_ID
    )

    assert [character.char for character in sentences[0].characters] == ["き", "ゃ"]
    assert not any(
        character.linked_to_next for character in sentences[0].characters
    )


def test_linked_group_line_key_up_is_restored_on_group_tail():
    source = "{明日|[00:01:00]あ[00:01:20]し[00:01:40]た}[00:02:00]"

    sentences = sentences_from_kasugamuki(source, SINGER_ID)
    characters = sentences[0].characters

    assert [character.char for character in characters] == ["明", "日"]
    assert characters[0].linked_to_next
    assert not characters[0].is_sentence_end
    assert characters[1].is_sentence_end
    assert characters[1].sentence_end_ts == 2000
    assert sentences_to_kasugamuki(sentences) == source
