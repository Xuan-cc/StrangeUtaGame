"""用户字典送り仮名剥离 & 劣质拆分 fallback 测试。

覆盖 `auto_check_service.py` analyze_sentence 中两个关键行为：

1. 首尾假名剥离：字典条目末尾/首部若是假名且 split_parts 对应位置为空或
   等于字符本身，应从 `char_to_block` 中剥离，使其成为独立自注音字符，
   避免 linked_to_next 把送り仮名错误吸入连词块。

2. 劣质拆分 fallback：字典 reading 用 `,` 分段后，若存在「对应字符是汉字」
   且 part 为空的位置，视为字典条目错漏，改走 `_fallback_split_peel_kana`
   重算整块，避免汉字无注音。
"""

import pytest

from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Sentence
from strange_uta_game.backend.domain.models import Character
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    SudachiAnalyzer,
)


def _get_sudachi():
    """真实 SudachiAnalyzer；不可用则返回 None 触发 skip。"""
    try:
        return SudachiAnalyzer()
    except Exception:
        return None


def _serialize(chars):
    """将 characters 序列化为 `{汉字||读音}` 形式（同 ruby_editor._lines_to_text）。"""
    out = ""
    i = 0
    n = len(chars)
    while i < n:
        if chars[i].ruby:
            gs = i
            while i < n - 1 and chars[i].linked_to_next:
                i += 1
            i += 1
            tp = "".join(ch.char for ch in chars[gs:i])
            rd = ",".join(
                "|".join(p.text for p in ch.ruby.parts) if ch.ruby else ""
                for ch in chars[gs:i]
            )
            out += f"{{{tp}||{rd}}}"
        else:
            out += chars[i].char
            i += 1
    return out


def _make_sentence(text):
    return Sentence(
        singer_id="default",
        characters=[Character(char=c) for c in text],
    )


class TestDictOkuriganaPeel:
    """字典末尾送り仮名剥离场景。"""

    def setup_method(self):
        if _get_sudachi() is None:
            pytest.skip("SudachiAnalyzer 不可用")

    def test_dict_quality_fallback_on_empty_kanji_part(self):
        """字典 `可愛い → かわい,,い`：中间空 part 对应汉字是合法的「首字承载」。

        期望输出：`{可愛||か|わ|い,}い`（保留字典拆分，尾部 い 剥离为独立）
        - 可 ruby=[か,わ,い]/cp=3（承载所有 mora），愛 ruby=None/cp=0，linked=True
        - い 被尾部假名剥离 → 独立自注音 ruby=None
        """
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "可愛い", "reading": "かわい,,い"}
            ],
        )
        sent = _make_sentence("可愛い")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "{可愛||か|わ|い,}い"
        # linked 链：可→愛 承载，い 独立
        assert sent.characters[0].linked_to_next is True
        assert sent.characters[1].linked_to_next is False
        # 末尾 い 被剥离：ruby=None（裸写），cp=1（自身 mora）
        assert sent.characters[2].ruby is None
        assert sent.characters[2].check_count == 1

    def test_dict_tail_empty_against_kanji_triggers_fallback(self):
        """`食物 → たもの,`：尾 part 空对应汉字 `物` → fallback。

        期望：`{食物||た,もの}`（物 拿到 `もの`，不再无注音）
        """
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "食物", "reading": "たもの,"}
            ],
        )
        sent = _make_sentence("食物")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "{食物||た,もの}"
        # 两字都在块内
        assert sent.characters[0].linked_to_next is True
        assert sent.characters[1].ruby is not None
        assert "".join(p.text for p in sent.characters[1].ruby.parts) == "もの"

    def test_dict_middle_empty_against_kana_preserved(self):
        """`食べ物 → た,,もの`：中间 part 空对应假名 `べ` → 保持字典语义。

        这是合法的「连词首字承载」，不应触发 fallback。
        期望：`{食べ物||た,,も|の}`（べ ruby=None，由 食 的 `た` 承载首 mora）
        """
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "食べ物", "reading": "た,,もの"}
            ],
        )
        sent = _make_sentence("食べ物")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "{食べ物||た,,も|の}"

    def test_dict_leading_kana_peeled(self):
        """`お花見 → お,はな,み`：首字 お 是假名 + part==char → 剥离出 block。

        期望：`お{花見||は|な,み}`
        """
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "お花見", "reading": "お,はな,み"}
            ],
        )
        sent = _make_sentence("お花見")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "お{花見||は|な,み}"
        # お 被剥离为独立自注音（ruby=None 因 len==1 且 ==self）
        assert sent.characters[0].ruby is None
        assert sent.characters[0].linked_to_next is False

    def test_normal_dict_unaffected(self):
        """正常字典 `大冒険 → だい,ぼう,けん` 不应受新逻辑影响。"""
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "大冒険", "reading": "だい,ぼう,けん"}
            ],
        )
        sent = _make_sentence("大冒険")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "{大冒険||だ|い,ぼ|う,け|ん}"

    def test_two_char_dict_tail_empty_fallback(self):
        """2 字刷质：`可愛 → かわ,` 尾空对汉字 → fallback。"""
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "可愛", "reading": "かわ,"}
            ],
        )
        sent = _make_sentence("可愛")
        service.apply_to_sentence(sent)

        assert _serialize(sent.characters) == "{可愛||か,わ}"
        assert sent.characters[0].linked_to_next is True

    def test_adjective_okurigana_no_dict(self):
        """无字典时 `赤い`（Sudachi 原生解析）应保持 `{赤||あ|か}い`。

        回归验证：首尾剥离 + 劣质 fallback 不破坏无字典路径。
        """
        service = AutoCheckService(ruby_analyzer=_get_sudachi())
        sent = _make_sentence("赤い")
        service.apply_to_sentence(sent)

        result = _serialize(sent.characters)
        # 赤 必须有 `あか` 注音；い 独立裸写
        assert "赤" in result and "あ" in result and "か" in result
        assert sent.characters[-1].char == "い"
        # い ruby 要么为 None（裸写）要么 == "い"（自身）
        i_ch = sent.characters[-1]
        if i_ch.ruby is not None:
            assert "".join(p.text for p in i_ch.ruby.parts) == "い"


class TestUpdateCheckpointsPreservesLinkedToNext:
    """回归：`update_checkpoints_for_project` 不得擦 linked_to_next。

    历史 bug：#10 清理逻辑在「linked=True 且下一字 cc != 0」时断开连词，
    但新规则允许「连词不强制后字 cc==0；后字继续展示自己的 ruby」，该清理
    会错误断开合法连词链 [可,愛]→[い]，导致切换页面时 `{可愛||か|わ|い,}い`
    退化为 `{可||か|わ|い}愛い`。
    """

    def setup_method(self):
        if _get_sudachi() is None:
            pytest.skip("SudachiAnalyzer 不可用")

    def test_update_checkpoints_preserves_kawaii_linked_chain(self):
        """`可愛い + かわい,,い`：analyze 后 update_checkpoints 不得断链。"""
        from strange_uta_game.backend.domain.project import Project

        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "可愛い", "reading": "かわい,,い"}
            ],
        )
        sent = _make_sentence("可愛い")
        service.apply_to_sentence(sent)

        # analyze 后状态
        assert _serialize(sent.characters) == "{可愛||か|わ|い,}い"
        assert sent.characters[0].linked_to_next is True
        assert sent.characters[1].linked_to_next is False

        # 模拟 "自动分析全部注音" 第二步：update_checkpoints_for_project
        project = Project(sentences=[sent])
        service.update_checkpoints_for_project(project)

        # linked 链必须保留
        assert sent.characters[0].linked_to_next is True, (
            "可.linked 被 update_checkpoints 错误断开"
        )
        # 序列化必须保持连词形态
        assert _serialize(sent.characters) == "{可愛||か|わ|い,}い"

    def test_update_checkpoints_preserves_kyou_linked_chain(self):
        """`今日 + きょ,う`：同样不得断 今→日 连词。"""
        from strange_uta_game.backend.domain.project import Project

        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "今日", "reading": "きょ,う"}
            ],
        )
        sent = _make_sentence("今日")
        service.apply_to_sentence(sent)

        linked_before = sent.characters[0].linked_to_next
        project = Project(sentences=[sent])
        service.update_checkpoints_for_project(project)

        assert sent.characters[0].linked_to_next is linked_before, (
            "今.linked 被 update_checkpoints 错误改动"
        )
