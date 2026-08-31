"""「全部转为罗马字注音」一次性操作测试。

覆盖 romanize_project_to_self_ruby：保留现有注音结构转罗马音、无 ruby 单假名补
自注音、助词/促音上下文读音、幂等性、不触碰 check_count 之外的结构。
"""

from strange_uta_game.backend.domain import (
    Character,
    Project,
    Ruby,
    RubyPart,
    Sentence,
)
from strange_uta_game.backend.infrastructure.parsers.romaji import (
    romanize_project_to_self_ruby,
)


def _parts(sentence: Sentence):
    return [
        [p.text for p in ch.ruby.parts] if ch.ruby else [] for ch in sentence.characters
    ]


def _project(*sentences: Sentence) -> Project:
    proj = Project(sentences=list(sentences))
    return proj


def _kanji_with_ruby(char: str, kana_parts, singer="s1") -> Character:
    return Character(
        char=char,
        ruby=Ruby(parts=[RubyPart(text=p) for p in kana_parts]),
        check_count=len(kana_parts),
        singer_id=singer,
    )


def test_existing_kana_ruby_becomes_romaji_keeping_structure():
    # 今日 → ruby きょ/う（两段）
    sent = Sentence(singer_id="s1", characters=[
        _kanji_with_ruby("今", ["きょ"]),
        _kanji_with_ruby("日", ["う"]),
    ])
    changed = romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["kyo"], ["u"]]
    assert [ch.check_count for ch in sent.characters] == [1, 1]
    assert changed == 1


def test_bare_kana_gets_self_ruby_romaji():
    sent = Sentence.from_text("あい", "s1")
    assert _parts(sent) == [[], []]
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["a"], ["i"]]
    assert [ch.check_count for ch in sent.characters] == [1, 1]


def test_particle_wa_after_kanji():
    # 私(わたし) は → は 自注音后判定为助词 → wa
    sent = Sentence(singer_id="s1", characters=[
        _kanji_with_ruby("私", ["わ", "た", "し"]),
        Character(char="は", check_count=1, singer_id="s1"),
    ])
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["wa", "ta", "shi"], ["wa"]]


def test_word_initial_ha_not_particle():
    sent = Sentence.from_text("はじめ", "s1")
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent)[0] == ["ha"]


def test_wo_always_particle():
    sent = Sentence.from_text("を", "s1")
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["o"]]


def test_sokuon_cross_char_context():
    # 待(ま) + っ(自注音) + て(自注音) → ma / t / te（促音独立成拍）
    sent = Sentence(singer_id="s1", characters=[
        _kanji_with_ruby("待", ["ま"]),
        Character(char="っ", check_count=1, singer_id="s1"),
        Character(char="て", check_count=1, singer_id="s1"),
    ])
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["ma"], ["t"], ["te"]]


def test_idempotent_second_run_no_change():
    sent = Sentence.from_text("あい", "s1")
    proj = _project(sent)
    romanize_project_to_self_ruby(proj)
    first = _parts(sent)
    changed = romanize_project_to_self_ruby(proj)
    assert _parts(sent) == first
    assert changed == 0


def test_no_kana_no_ruby_returns_zero():
    # 纯汉字无 ruby → 不创建自注音、不变化
    sent = Sentence(singer_id="s1", characters=[
        Character(char="水", check_count=0, singer_id="s1"),
    ])
    changed = romanize_project_to_self_ruby(_project(sent))
    assert changed == 0
    assert sent.characters[0].ruby is None


def test_katakana_self_ruby():
    sent = Sentence.from_text("カナ", "s1")
    romanize_project_to_self_ruby(_project(sent))
    assert _parts(sent) == [["ka"], ["na"]]
