"""纯转换器测试：不依赖 Sentence/Character，只测 romanize_ruby_parts。"""

from strange_uta_game.backend.infrastructure.parsers.romaji import romanize_ruby_parts


class TestBasicKana:
    def test_hepburn_basic(self):
        assert romanize_ruby_parts(["し", "ち", "つ", "ふ", "じ"]) == ["shi", "chi", "tsu", "fu", "ji"]

    def test_seion(self):
        assert romanize_ruby_parts(["あ", "か", "さ", "た", "な"]) == ["a", "ka", "sa", "ta", "na"]

    def test_dakuon(self):
        assert romanize_ruby_parts(["が", "ざ", "だ", "ば"]) == ["ga", "za", "da", "ba"]

    def test_handakuon(self):
        assert romanize_ruby_parts(["ぱ", "ぴ", "ぷ", "ぺ", "ぽ"]) == ["pa", "pi", "pu", "pe", "po"]

    def test_youon_same_part(self):
        assert romanize_ruby_parts(["きゃ", "きゅ", "きょ"]) == ["kya", "kyu", "kyo"]
        assert romanize_ruby_parts(["しゃ", "しゅ", "しょ"]) == ["sha", "shu", "sho"]
        assert romanize_ruby_parts(["ちゃ", "ちゅ", "ちょ"]) == ["cha", "chu", "cho"]


class TestSokuon:
    def test_sokuon_gemination(self):
        assert romanize_ruby_parts(["ま", "っ", "て"]) == ["ma", "t", "te"]

    def test_sokuon_before_cha(self):
        assert romanize_ruby_parts(["こ", "っ", "ち"]) == ["ko", "t", "chi"]

    def test_sokuon_isolated(self):
        assert romanize_ruby_parts(["っ"]) == ["xtsu"]


class TestLongVowel:
    def test_long_vowel_repeats_previous(self):
        assert romanize_ruby_parts(["す", "ー", "ぱ", "ー"]) == ["su", "u", "pa", "a"]

    def test_long_vowel_initial(self):
        assert romanize_ruby_parts(["ー", "あ"]) == ["-", "a"]


class TestN:
    def test_n_before_vowel(self):
        assert romanize_ruby_parts(["ん", "あ"]) == ["n'", "a"]

    def test_n_before_consonant(self):
        assert romanize_ruby_parts(["あ", "ん", "し"]) == ["a", "n", "shi"]

    def test_n_before_y(self):
        assert romanize_ruby_parts(["こ", "ん", "や"]) == ["ko", "n'", "ya"]


class TestCrossPart:
    def test_cross_part_youon_split(self):
        assert romanize_ruby_parts(["き", "ょ", "う"]) == ["ky", "o", "u"]

    def test_cross_part_youon_with_timing(self):
        assert romanize_ruby_parts(["きょ", "う"]) == ["kyo", "u"]

    def test_preserves_part_count_for_sokuon(self):
        result = romanize_ruby_parts(["ま", "っ", "て"])
        assert len(result) == 3


class TestParticles:
    def test_particle_opt_in(self):
        assert romanize_ruby_parts(["は", "へ", "を"]) == ["ha", "he", "o"]

    def test_particle_enabled(self):
        assert romanize_ruby_parts(["は", "へ", "を"], particle_indices={0, 1, 2}) == ["wa", "e", "o"]

    def test_only_wo_default_particle(self):
        assert romanize_ruby_parts(["を"]) == ["o"]


class TestEdgeCases:
    def test_ascii_passthrough(self):
        assert romanize_ruby_parts(["Ro", "me", "o"]) == ["Ro", "me", "o"]

    def test_empty_input(self):
        assert romanize_ruby_parts([]) == []

    def test_katakana(self):
        assert romanize_ruby_parts(["カ", "キ", "ク"]) == ["ka", "ki", "ku"]

    def test_mixed_katakana_youon(self):
        assert romanize_ruby_parts(["キャ", "キュ", "キョ"]) == ["kya", "kyu", "kyo"]

    def test_katakana_sokuon(self):
        assert romanize_ruby_parts(["マ", "ッ", "テ"]) == ["ma", "t", "te"]
