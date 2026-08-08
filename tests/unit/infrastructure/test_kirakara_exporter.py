from strange_uta_game.backend.domain import Project, Sentence, Singer
from strange_uta_game.backend.infrastructure.exporters import (
    KirakaraExporter,
    get_exporter_by_name,
)


def _timed_sentence(text: str, singer_id: str, start: int = 1000) -> Sentence:
    sentence = Sentence.from_text(text, singer_id)
    for index, char in enumerate(sentence.characters):
        char.add_timestamp(start + index * 1000)
    return sentence


def test_old_format_name_resolves_to_kirakara():
    exporter = get_exporter_by_name("春日向注音（带罗马音）")
    assert isinstance(exporter, KirakaraExporter)
    assert exporter.name == "Kirakara"
    assert exporter.file_extension == ".krl"


def test_romaji_option_switches_between_single_and_double_ruby(tmp_path):
    project = Project()
    singer = project.singers[0]
    project.add_sentence(_timed_sentence("か", singer.id))
    exporter = KirakaraExporter()

    plain_path = tmp_path / "plain.krl"
    romaji_path = tmp_path / "romaji.krl"
    exporter.export(project, str(plain_path), export_romaji=False)
    exporter.export(project, str(romaji_path), export_romaji=True)

    assert plain_path.read_text(encoding="utf-8") == "[00:01:00]か"
    assert romaji_path.read_text(encoding="utf-8") == "{か|>[00:01:00]ka}"


def test_singer_filter_and_at_singer_tags(tmp_path):
    project = Project()
    singer_a = project.singers[0]
    singer_a.name = "A"
    singer_b = Singer(name="B", color="#00FF00")
    project.add_singer(singer_b)
    sentence = _timed_sentence("XY", singer_a.id)
    sentence.characters[1].singer_id = singer_b.id
    project.add_sentence(sentence)

    output = tmp_path / "filtered.krl"
    KirakaraExporter().export(
        project,
        str(output),
        singer_ids={singer_b.id},
        insert_singer_tags=True,
        singer_map={singer_a.id: "A", singer_b.id: "B"},
        export_romaji=True,
    )

    content = output.read_text(encoding="utf-8")
    assert "X" not in content
    assert content == "【@B】[00:02:00]Y"


def test_each_line_option_repeats_singer_tag(tmp_path):
    project = Project()
    singer = project.singers[0]
    singer.name = "A"
    project.add_sentence(_timed_sentence("X", singer.id, 1000))
    project.add_sentence(_timed_sentence("Y", singer.id, 2000))
    singer_map = {singer.id: "A"}

    once = tmp_path / "once.krl"
    each_line = tmp_path / "each-line.krl"
    exporter = KirakaraExporter()
    exporter.export(
        project,
        str(once),
        insert_singer_tags=True,
        singer_map=singer_map,
        export_romaji=False,
    )
    exporter.export(
        project,
        str(each_line),
        insert_singer_tags=True,
        insert_singer_each_line=True,
        singer_map=singer_map,
        export_romaji=False,
    )

    assert once.read_text(encoding="utf-8").count("【@A】") == 1
    assert each_line.read_text(encoding="utf-8").count("【@A】") == 2
