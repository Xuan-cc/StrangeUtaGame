"""Checkpoint / linked_to_next 通用回归测试。

历史上同文件还覆盖 `_apply_dictionary` 三步后处理（送り仮名剥离、劣质拆分
fallback），该 phase 在 dictionary annotated 化重构后被 Phase 5
「用户词典直接覆盖整段 Character[]」取代，相应用例已整体删除。
"""

import pytest

from strange_uta_game.backend.application import AutoCheckService
from strange_uta_game.backend.domain import Sentence
from strange_uta_game.backend.domain.models import Character
from strange_uta_game.backend.infrastructure.parsers.ruby_analyzer import (
    WinRTAnalyzer,
)


def _get_sudachi():
    """真实注音分析器（WinRT IME 主引擎）；不可用返回 None 触发 skip。"""
    try:
        return WinRTAnalyzer()
    except Exception:
        return None


def _serialize(chars):
    """将 characters 序列化为 `{汉字||读音}` 形式（同 fulltext_interface._lines_to_text）。"""
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


class TestUpdateCheckpointsPreservesLinkedToNext:
    """回归：`update_checkpoints_for_project` 不得擦 linked_to_next。

    历史 bug：#10 清理逻辑在「linked=True 且下一字 cc != 0」时断开连词，
    但新规则允许「连词不强制后字 cc==0；后字继续展示自己的 ruby」，该清理
    会错误断开合法连词链 [可,愛]→[い]，导致切换页面时 `{可愛||か,わ|い}い`
    退化为 `{可||か}{愛||わ|い}い`。
    """

    def setup_method(self):
        if _get_sudachi() is None:
            pytest.skip("WinRT 注音引擎不可用")

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
        assert _serialize(sent.characters) == "{可愛||か,わ|い}い"
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
        assert _serialize(sent.characters) == "{可愛||か,わ|い}い"

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


class TestKanaSingleCheckpointCap:
    """单一平假名/片假名封顶 1 cp 回归。

    规则（用户 2026-04-23）：单字假名最多 1 个节奏点，可以是 0。
    典型场景：`ロミオ + e2k(Ro,me,o)` 之前被 split_into_moras 按字符误计为
    2/2/1，应封顶为 1/1/1。
    """

    def setup_method(self):
        if _get_sudachi() is None:
            pytest.skip("WinRT 注音引擎不可用")

    def test_katakana_with_e2k_english_reading_caps_to_one(self):
        """`ロミオ → Ro,me,o`（e2k 用户词典模拟）：cp 必须 1/1/1。"""
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "ロミオ", "reading": "Ro,me,o"}
            ],
        )
        sent = _make_sentence("ロミオ")
        service.apply_to_sentence(sent)

        ccs = [c.check_count for c in sent.characters]
        assert ccs == [1, 1, 1], f"期望 [1,1,1]，实际 {ccs}"

    def test_katakana_with_long_dict_reading_caps_to_one(self):
        """`カ + reading=かあ`（假设 mora=2）：片假名单字封顶 1。"""
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "カタ", "reading": "かあ,た"}
            ],
        )
        sent = _make_sentence("カタ")
        service.apply_to_sentence(sent)

        # カ 尽管 reading=かあ（2 mora），也必须封顶 1
        assert sent.characters[0].check_count <= 1
        # タ 同理
        assert sent.characters[1].check_count <= 1

    def test_kanji_unaffected_by_cap(self):
        """汉字不受封顶影响：`大冒険 → だい,ぼう,けん` 仍是 2/2/2。"""
        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "大冒険", "reading": "だい,ぼう,けん"}
            ],
        )
        sent = _make_sentence("大冒険")
        service.apply_to_sentence(sent)

        ccs = [c.check_count for c in sent.characters]
        assert ccs == [2, 2, 2], f"期望 [2,2,2]，实际 {ccs}"

    def test_update_checkpoints_also_caps_kana(self):
        """`update_checkpoints_for_project` 路径也必须封顶。"""
        from strange_uta_game.backend.domain.project import Project

        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "ロミオ", "reading": "Ro,me,o"}
            ],
        )
        sent = _make_sentence("ロミオ")
        service.apply_to_sentence(sent)

        project = Project(sentences=[sent])
        service.update_checkpoints_for_project(project)

        ccs = [c.check_count for c in sent.characters]
        assert ccs == [1, 1, 1], (
            f"update_checkpoints 后 cp 未封顶：{ccs}"
        )


class TestKanjiEmptyRubyZeroCheckpoint:
    """空 ruby 的汉字 cp 必须为 0（连词块内后字）。

    规则：汉字的 cp 严格由它自己的 ruby parts 决定；连词块内 mora 已压在
    首字上时，块内后续汉字 ruby=None，cp 必须=0，不能因默认规则留 1。

    典型场景：`今日（きょう）` → 今=[きょ,う]/cp=2、日=None/cp=0（连词块内
    后续汉字读音压在首字上）。此前 update_checkpoints_from_rubies 对
    `not char.ruby` 的汉字 continue 跳过，保留默认 cp=1，导致连词块内错误多拍。
    """

    def setup_method(self):
        if _get_sudachi() is None:
            pytest.skip("WinRT 注音引擎不可用")

    def test_linked_block_empty_ruby_kanji_cp_zero(self):
        """`今日（きょう）` update_checkpoints 后 日.cp 必须 = 0。

        きょう 共 3 拍无法均分到 2 字，整词读音压在首字「今」上、「日」ruby=None，
        连词承载；块内空 ruby 汉字的 cp 必须为 0。
        """
        from strange_uta_game.backend.domain.project import Project

        service = AutoCheckService(ruby_analyzer=_get_sudachi())
        sent = _make_sentence("今日")
        service.apply_to_sentence(sent)

        # analyze 阶段已经正确
        assert sent.characters[1].ruby is None, "日 应为空 ruby"
        assert sent.characters[1].check_count == 0, "日 初始 cp 应为 0"
        assert sent.characters[0].linked_to_next is True, "今→日 应连词"

        # update_checkpoints 不得把空 ruby 的汉字 cp 改回非 0
        project = Project(sentences=[sent])
        service.update_checkpoints_for_project(project)

        assert sent.characters[1].check_count == 0, (
            f"update_checkpoints 后 日.cp={sent.characters[1].check_count}，期望 0"
        )
        # 首字不应被影响
        assert sent.characters[0].check_count == 2, (
            f"今.cp 应为 2（きょう→きょ|う），实际 {sent.characters[0].check_count}"
        )

    def test_kanji_with_ruby_still_uses_mora_count(self):
        """有 ruby 的汉字仍按 mora 数计 cp（回归保护）。"""
        from strange_uta_game.backend.domain.project import Project

        service = AutoCheckService(
            ruby_analyzer=_get_sudachi(),
            user_dictionary=[
                {"enabled": True, "word": "大冒険", "reading": "だい,ぼう,けん"}
            ],
        )
        sent = _make_sentence("大冒険")
        service.apply_to_sentence(sent)

        project = Project(sentences=[sent])
        service.update_checkpoints_for_project(project)

        ccs = [c.check_count for c in sent.characters]
        assert ccs == [2, 2, 2], (
            f"汉字按 mora 计数错误：{ccs}"
        )
