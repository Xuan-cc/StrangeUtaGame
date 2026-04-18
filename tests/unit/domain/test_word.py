from strange_uta_game.backend.domain import Word, Character, Ruby


class TestWord:
    def test_word_text(self):
        ch1 = Character(char="赤")
        ch2 = Character(char="い")
        word = Word(characters=[ch1, ch2])
        assert word.text == "赤い"
        assert word.char_count == 2

    def test_word_ruby_properties(self):
        ch1 = Character(char="赤", ruby=Ruby(text="あか"))
        ch2 = Character(char="い")
        word = Word(characters=[ch1, ch2])

        assert word.ruby_parts == ["あか"]
        assert word.ruby_text == "あか"
        assert word.ruby_csv == "あか"
        assert word.has_ruby is True

    def test_word_multi_ruby(self):
        ch1 = Character(char="昨", ruby=Ruby(text="きの"))
        ch2 = Character(char="日", ruby=Ruby(text="う"))
        word = Word(characters=[ch1, ch2])

        assert word.ruby_parts == ["きの", "う"]
        assert word.ruby_text == "きのう"
        assert word.ruby_csv == "きの,う"
        assert word.has_ruby is True

    def test_word_no_ruby(self):
        word = Word(characters=[Character(char="a")])
        assert not word.has_ruby
        assert word.ruby_parts == []
        assert word.ruby_text == ""
        assert word.ruby_csv == ""
