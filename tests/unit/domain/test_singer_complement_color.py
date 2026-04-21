"""Singer.complement_color 单元测试（Issue #9 第十六批架构性修复）。

覆盖：
- HSV h+180 补色自动计算
- change_color 同步更新补色
- 灰度色退化处理
- 从 .sug 加载时向后兼容（旧文件无字段自动补算）
"""

import pytest

from strange_uta_game.backend.domain.entities import (
    Singer,
    _compute_complement_color,
)


class TestComputeComplementColor:
    """测试 _compute_complement_color 纯函数。"""

    def test_red_to_cyan(self):
        # #FF0000 (h=0) → #00FFFF (h=180)
        assert _compute_complement_color("#FF0000") == "#00FFFF"

    def test_green_to_magenta(self):
        # #00FF00 (h=120) → #FF00FF (h=300)
        assert _compute_complement_color("#00FF00") == "#FF00FF"

    def test_blue_to_yellow(self):
        # #0000FF (h=240) → #FFFF00 (h=60)
        assert _compute_complement_color("#0000FF") == "#FFFF00"

    def test_grayscale_returns_same(self):
        # 纯灰无有意义色相
        assert _compute_complement_color("#808080") == "#808080"

    def test_invalid_input_returns_same(self):
        assert _compute_complement_color("") == ""
        assert _compute_complement_color("invalid") == "invalid"
        assert _compute_complement_color("#FFF") == "#FFF"

    def test_preserves_saturation_and_value(self):
        # #FF6B6B 的补色应保持 S/V
        import colorsys

        result = _compute_complement_color("#FF6B6B")
        # 解析两色的 HSV
        def to_hsv(hex_c):
            r, g, b = (
                int(hex_c[1:3], 16) / 255,
                int(hex_c[3:5], 16) / 255,
                int(hex_c[5:7], 16) / 255,
            )
            return colorsys.rgb_to_hsv(r, g, b)

        h1, s1, v1 = to_hsv("#FF6B6B")
        h2, s2, v2 = to_hsv(result)
        assert abs(s1 - s2) < 0.01
        assert abs(v1 - v2) < 0.01
        # h 差 ~0.5
        assert abs(((h2 - h1) % 1.0) - 0.5) < 0.01


class TestSingerComplementColor:
    """测试 Singer dataclass 的补色字段。"""

    def test_default_auto_compute(self):
        s = Singer(name="测试", color="#FF6B6B")
        assert s.complement_color == _compute_complement_color("#FF6B6B")
        assert s.complement_color.startswith("#")
        assert len(s.complement_color) == 7

    def test_change_color_updates_complement(self):
        s = Singer(name="测试", color="#FF0000")
        assert s.complement_color == "#00FFFF"

        s.change_color("#0000FF")
        assert s.color == "#0000FF"
        assert s.complement_color == "#FFFF00"

    def test_explicit_complement_preserved(self):
        # 从 .sug 加载场景：显式传入
        s = Singer(
            name="测试", color="#FF0000", complement_color="#ABCDEF"
        )
        assert s.complement_color == "#ABCDEF"

    def test_missing_complement_recomputed_backward_compat(self):
        # 旧 .sug 文件：complement_color 为空字符串 → 自动补算
        s = Singer(name="测试", color="#FF0000", complement_color="")
        assert s.complement_color == "#00FFFF"

    def test_invalid_complement_recomputed(self):
        # 损坏的 complement_color → 自动补算
        s = Singer(name="测试", color="#FF0000", complement_color="xyz")
        assert s.complement_color == "#00FFFF"
