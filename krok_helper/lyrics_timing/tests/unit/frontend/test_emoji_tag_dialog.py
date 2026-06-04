"""分色标签设置助手核心逻辑单测。

仅测试纯函数（无 Qt 依赖）：
- strip_emoji_tags
- build_emoji_tag
- apply_emoji_tags_to_settings（通过 mock AppSettings）
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from strange_uta_game.frontend.export.emoji_tag_dialog import (
    DEFAULT_PARAMS,
    apply_emoji_tags_to_settings,
    build_emoji_tag,
    strip_emoji_tags,
)


class TestStripEmojiTags:
    """strip_emoji_tags 正确清除 @Emoji 行"""

    def test_removes_at_emoji(self):
        custom = [
            "@Title=Test",
            "@Emoji=【太郎】,img.png,,NoDecor",
            "@Artist=Someone",
        ]
        result = strip_emoji_tags(custom)
        assert result == ["@Title=Test", "@Artist=Someone"]

    def test_removes_numbered_emoji(self):
        """@Emoji1=, @Emoji2= 等也应被清除"""
        custom = [
            "@Emoji1=【甲】,a.png",
            "@Emoji2=【乙】,b.png",
            "@TaggingBy=X",
        ]
        result = strip_emoji_tags(custom)
        assert result == ["@TaggingBy=X"]

    def test_case_insensitive(self):
        custom = ["@emoji=trigger,img.png", "@EMOJI2=t,img.png", "@Title=X"]
        result = strip_emoji_tags(custom)
        assert result == ["@Title=X"]

    def test_empty_list(self):
        assert strip_emoji_tags([]) == []

    def test_no_emoji_lines(self):
        custom = ["@Title=X", "@Artist=Y"]
        assert strip_emoji_tags(custom) == ["@Title=X", "@Artist=Y"]

    def test_preserves_order(self):
        custom = ["@A=1", "@Emoji=t,img.png", "@B=2", "@C=3"]
        result = strip_emoji_tags(custom)
        assert result == ["@A=1", "@B=2", "@C=3"]


class TestBuildEmojiTag:
    """build_emoji_tag 生成正确格式"""

    def test_basic(self):
        tag = build_emoji_tag("【太郎】", "img.png,,NoDecor,MarginRight=-170")
        assert tag == "@Emoji=【太郎】,img.png,,NoDecor,MarginRight=-170"

    def test_single_field(self):
        tag = build_emoji_tag("【花子】", "透明画像1x1.png")
        assert tag == "@Emoji=【花子】,透明画像1x1.png"

    def test_default_params(self):
        tag = build_emoji_tag("【X】", DEFAULT_PARAMS)
        assert tag.startswith("@Emoji=【X】,透明画像1x1.png")


class TestApplyEmojiTagsToSettings:
    """apply_emoji_tags_to_settings 正确更新 AppSettings"""

    def _make_settings(self, initial_custom=None):
        """构造一个 mock AppSettings，初始含给定 custom 列表。"""
        mock_settings = MagicMock()
        initial_tags = {"title": "Song", "custom": initial_custom or []}
        # get("nicokara_tags") → initial_tags; get("nicokara_emoji_default") → ""
        def _get(key, default=None):
            if key == "nicokara_tags":
                return dict(initial_tags)
            if key == "nicokara_emoji_default":
                return ""
            return default
        mock_settings.get.side_effect = _get
        mock_settings.set = MagicMock()
        mock_settings.save = MagicMock()
        return mock_settings

    def test_writes_new_emoji_lines(self):
        mock_settings = self._make_settings()
        singer_params = [
            ("太郎", "【太郎】", "img.png,,NoDecor"),
            ("花子", "【花子】", "img2.png,,NoDecor"),
        ]
        with patch(
            "strange_uta_game.frontend.export.emoji_tag_dialog.AppSettings",
            return_value=mock_settings,
        ):
            apply_emoji_tags_to_settings(singer_params)

        # 验证 set("nicokara_tags", ...) 被调用
        calls = {call[0][0]: call[0][1] for call in mock_settings.set.call_args_list}
        written_tags = calls["nicokara_tags"]
        custom = written_tags["custom"]
        assert "@Emoji=【太郎】,img.png,,NoDecor" in custom
        assert "@Emoji=【花子】,img2.png,,NoDecor" in custom
        assert written_tags["title"] == "Song"  # 其他字段不变

    def test_removes_old_emoji_before_writing(self):
        old_custom = [
            "@Emoji=【旧】,old.png",
            "@TaggingBy=Someone",
        ]
        mock_settings = self._make_settings(initial_custom=old_custom)
        singer_params = [("太郎", "【太郎】", "new.png")]

        with patch(
            "strange_uta_game.frontend.export.emoji_tag_dialog.AppSettings",
            return_value=mock_settings,
        ):
            apply_emoji_tags_to_settings(singer_params)

        calls = {call[0][0]: call[0][1] for call in mock_settings.set.call_args_list}
        custom = calls["nicokara_tags"]["custom"]
        # 旧 @Emoji 行被删
        assert all("旧" not in line for line in custom)
        # 新行存在
        assert "@Emoji=【太郎】,new.png" in custom
        # 非 Emoji 行保留
        assert "@TaggingBy=Someone" in custom

    def test_remembers_first_params(self):
        mock_settings = self._make_settings()
        singer_params = [
            ("太郎", "【太郎】", "remembered.png,,NoDecor"),
            ("花子", "【花子】", "other.png"),
        ]
        with patch(
            "strange_uta_game.frontend.export.emoji_tag_dialog.AppSettings",
            return_value=mock_settings,
        ):
            apply_emoji_tags_to_settings(singer_params)

        calls = {call[0][0]: call[0][1] for call in mock_settings.set.call_args_list}
        # 首行参数被记忆
        assert calls["nicokara_emoji_default"] == "remembered.png,,NoDecor"

    def test_empty_trigger_skipped(self):
        """触发字符为空的行不写入"""
        mock_settings = self._make_settings()
        singer_params = [("太郎", "", "img.png"), ("花子", "【花子】", "img2.png")]

        with patch(
            "strange_uta_game.frontend.export.emoji_tag_dialog.AppSettings",
            return_value=mock_settings,
        ):
            apply_emoji_tags_to_settings(singer_params)

        calls = {call[0][0]: call[0][1] for call in mock_settings.set.call_args_list}
        custom = calls["nicokara_tags"]["custom"]
        assert len([l for l in custom if l.startswith("@Emoji=")]) == 1
        assert "@Emoji=【花子】,img2.png" in custom

    def test_saves_settings(self):
        mock_settings = self._make_settings()
        with patch(
            "strange_uta_game.frontend.export.emoji_tag_dialog.AppSettings",
            return_value=mock_settings,
        ):
            apply_emoji_tags_to_settings([("太郎", "【太郎】", "img.png")])
        mock_settings.save.assert_called_once()
