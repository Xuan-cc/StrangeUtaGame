"""Singer 实体测试。"""

import pytest
from strange_uta_game.backend.domain import Singer, ValidationError


class TestSinger:
    """Singer 实体测试类"""

    def test_creation_with_defaults(self):
        """测试使用默认值创建 Singer"""
        singer = Singer()

        assert singer.name == "未命名"
        assert singer.color == "#FF6B6B"
        assert singer.is_default is False
        assert singer.display_priority == 0
        assert singer.enabled is True
        assert singer.id is not None  # 自动生成 UUID

    def test_creation_with_custom_values(self):
        """测试使用自定义值创建 Singer"""
        singer = Singer(
            name="初音ミク", color="#39C5BB", is_default=True, display_priority=1
        )

        assert singer.name == "初音ミク"
        assert singer.color == "#39C5BB"
        assert singer.is_default is True
        assert singer.display_priority == 1

    def test_rename(self):
        """测试重命名"""
        singer = Singer(name="未命名")
        singer.rename("初音ミク")

        assert singer.name == "初音ミク"

    def test_rename_empty_raises_error(self):
        """测试重命名为空应该抛出 ValidationError"""
        singer = Singer(name="未命名")

        with pytest.raises(ValidationError):
            singer.rename("")

    def test_change_color(self):
        """测试修改颜色"""
        singer = Singer(color="#FF6B6B")
        singer.change_color("#39C5BB")

        assert singer.color == "#39C5BB"

    def test_change_color_invalid_format(self):
        """测试修改颜色为无效格式应该抛出 ValidationError"""
        singer = Singer(color="#FF6B6B")

        # 不以 # 开头
        with pytest.raises(ValidationError):
            singer.change_color("FF6B6B")

        # 长度不正确
        with pytest.raises(ValidationError):
            singer.change_color("#FF6B")

    def test_set_enabled(self):
        """测试设置启用状态"""
        singer = Singer(enabled=True)
        singer.set_enabled(False)

        assert singer.enabled is False

    def test_mutable_attributes(self):
        """测试 Singer 属性可变性（实体特性）"""
        singer = Singer(name="未命名", color="#FF6B6B")

        # 实体是可变的
        singer.name = "新名称"
        singer.color = "#000000"

        assert singer.name == "新名称"
        assert singer.color == "#000000"

    def test_id_uniqueness(self):
        """测试每个 Singer 有唯一的 ID"""
        singer1 = Singer()
        singer2 = Singer()

        assert singer1.id != singer2.id

    def test_invalid_empty_name_on_creation(self):
        """测试创建时名称为空应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Singer(name="")

    def test_invalid_empty_id_on_creation(self):
        """测试创建时 ID 为空应该抛出 ValidationError"""
        with pytest.raises(ValidationError):
            Singer(id="")
