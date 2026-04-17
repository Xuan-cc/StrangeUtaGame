"""演唱者管理服务。

管理演唱者的配置和分配。
"""

from typing import Optional, List, Callable
from dataclasses import dataclass

from strange_uta_game.backend.domain import Project, Singer


@dataclass
class SingerCallbacks:
    """演唱者服务回调"""

    on_singer_added: Optional[Callable[[Singer], None]] = None
    on_singer_removed: Optional[Callable[[str], None]] = None
    on_singer_updated: Optional[Callable[[Singer], None]] = None


class SingerService:
    """演唱者管理服务"""

    def __init__(self, project: Project, callbacks: SingerCallbacks = None):
        """
        Args:
            project: 项目
            callbacks: 回调函数
        """
        self._project = project
        self._callbacks = callbacks or SingerCallbacks()

    def add_singer(self, name: str = None, color: str = None) -> Singer:
        """添加演唱者

        Args:
            name: 演唱者名称（如果为 None 则自动生成 "未命名N"）
            color: 颜色（如果为 None 则自动分配）

        Returns:
            新创建的演唱者
        """
        # 自动分配颜色
        if color is None:
            color = self._assign_color()

        # 计算下一个后台编号（从1开始递增）
        next_number = self._get_next_backend_number()

        # 如果没有提供名称，自动生成 "未命名N"
        if name is None:
            name = f"未命名{next_number}"

        singer = Singer(name=name, color=color, backend_number=next_number)
        self._project.add_singer(singer)

        if self._callbacks.on_singer_added:
            self._callbacks.on_singer_added(singer)

        return singer

    def _get_next_backend_number(self) -> int:
        """获取下一个可用的后台编号

        Returns:
            下一个编号（从1开始递增）
        """
        if not self._project.singers:
            return 1

        # 找出当前最大的 backend_number
        max_number = max(
            (s.backend_number for s in self._project.singers if s.backend_number > 0),
            default=0,
        )
        return max_number + 1

    def remove_singer(self, singer_id: str, transfer_to: str = None) -> bool:
        """删除演唱者

        Args:
            singer_id: 演唱者ID
            transfer_to: 转移歌词到的演唱者ID

        Returns:
            是否成功
        """
        try:
            self._project.remove_singer(singer_id, transfer_to)

            if self._callbacks.on_singer_removed:
                self._callbacks.on_singer_removed(singer_id)

            return True

        except Exception:
            return False

    def rename_singer(self, singer_id: str, new_name: str) -> bool:
        """重命名演唱者

        Args:
            singer_id: 演唱者ID
            new_name: 新名称

        Returns:
            是否成功
        """
        singer = self._project.get_singer(singer_id)
        if not singer:
            return False

        singer.rename(new_name)

        if self._callbacks.on_singer_updated:
            self._callbacks.on_singer_updated(singer)

        return True

    def change_singer_color(self, singer_id: str, new_color: str) -> bool:
        """修改演唱者颜色

        Args:
            singer_id: 演唱者ID
            new_color: 新颜色

        Returns:
            是否成功
        """
        singer = self._project.get_singer(singer_id)
        if not singer:
            return False

        singer.change_color(new_color)

        if self._callbacks.on_singer_updated:
            self._callbacks.on_singer_updated(singer)

        return True

    def set_singer_enabled(self, singer_id: str, enabled: bool) -> bool:
        """设置演唱者启用状态

        Args:
            singer_id: 演唱者ID
            enabled: 是否启用

        Returns:
            是否成功
        """
        singer = self._project.get_singer(singer_id)
        if not singer:
            return False

        singer.set_enabled(enabled)

        if self._callbacks.on_singer_updated:
            self._callbacks.on_singer_updated(singer)

        return True

    def get_singer(self, singer_id: str) -> Optional[Singer]:
        """获取演唱者

        Args:
            singer_id: 演唱者ID

        Returns:
            演唱者对象，如果不存在则返回 None
        """
        return self._project.get_singer(singer_id)

    def set_default_singer(self, singer_id: str) -> bool:
        """设置默认演唱者。

        将指定演唱者设为默认，同时取消其他演唱者的默认状态。

        Args:
            singer_id: 演唱者ID

        Returns:
            是否成功
        """
        target = self._project.get_singer(singer_id)
        if not target:
            return False

        for s in self._project.singers:
            s.is_default = s.id == singer_id

        if self._callbacks.on_singer_updated:
            self._callbacks.on_singer_updated(target)

        return True

    def get_default_singer(self) -> Singer:
        """获取默认演唱者"""
        return self._project.get_default_singer()

    def get_all_singers(self, include_disabled: bool = True) -> List[Singer]:
        """获取所有演唱者

        Args:
            include_disabled: 是否包含禁用的演唱者

        Returns:
            演唱者列表
        """
        if include_disabled:
            return self._project.singers.copy()
        else:
            return [s for s in self._project.singers if s.enabled]

    def _assign_color(self) -> str:
        """自动分配颜色

        根据现有演唱者数量分配颜色。

        Returns:
            颜色代码
        """
        colors = [
            "#FF6B6B",  # 红色
            "#4ECDC4",  # 青色
            "#95E1D3",  # 绿色
            "#FCE38A",  # 黄色
            "#F38181",  # 粉红
            "#AA96DA",  # 紫色
            "#FCBAD3",  # 浅粉
            "#FFFFD2",  # 浅黄
            "#A8E6CF",  # 薄荷绿
            "#DCEDC1",  # 浅绿
        ]

        idx = len(self._project.singers) % len(colors)
        return colors[idx]
