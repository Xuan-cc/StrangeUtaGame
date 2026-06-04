"""演唱者管理服务。

管理演唱者的配置和分配。
"""

from typing import Optional, List, Callable
from dataclasses import dataclass

from strange_uta_game.backend.domain import Project, Singer, ValidationError


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

    def add_singer(
        self,
        name: str = None,
        color: str = None,
        color_mode: str = "solid",
        split_colors: list = None,
        group: str = "",
    ) -> Singer:
        """添加演唱者

        Args:
            name: 演唱者名称（如果为 None 则自动生成 "未命名N"）
            color: 颜色（如果为 None 则自动分配）
            color_mode: 颜色模式（"solid" 或 "split"）
            split_colors: 分色模式的额外颜色列表
            group: 分组名称（空字符串表示默认分组）

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

        # 项目内名称唯一性校验
        existing_names = {s.name for s in self._project.singers}
        if name in existing_names:
            raise ValidationError(f"演唱者名称「{name}」已存在，项目内不允许重名")

        singer = Singer(
            name=name,
            color=color,
            color_mode=color_mode,
            split_colors=split_colors or [],
            backend_number=next_number,
            group=group,
        )
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

        # 名称唯一性校验（排除自身）
        existing_names = {s.name for s in self._project.singers if s.id != singer_id}
        if new_name in existing_names:
            raise ValidationError(f"演唱者名称「{new_name}」已存在，项目内不允许重名")

        singer.rename(new_name)

        if self._callbacks.on_singer_updated:
            self._callbacks.on_singer_updated(singer)

        return True

    def change_singer_color(
        self,
        singer_id: str,
        new_color: str,
        color_mode: str = None,
        split_colors: list = None,
    ) -> bool:
        """修改演唱者颜色

        Args:
            singer_id: 演唱者ID
            new_color: 新主色
            color_mode: 颜色模式（可选，None 表示不修改）
            split_colors: 分色列表（可选，None 表示不修改）

        Returns:
            是否成功
        """
        singer = self._project.get_singer(singer_id)
        if not singer:
            return False

        singer.change_color(new_color, color_mode=color_mode, split_colors=split_colors)

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

    def change_singer_group(self, singer_id: str, group: str) -> bool:
        """修改演唱者分组

        Args:
            singer_id: 演唱者ID
            group: 新分组名称（空字符串表示默认分组）

        Returns:
            是否成功
        """
        singer = self._project.get_singer(singer_id)
        if not singer:
            return False

        singer.group = group

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

    # ==================== 顺序与批量操作 ====================

    def reorder_singers(self, ordered_ids: List[str]) -> bool:
        """按 ID 列表重排演唱者。

        Args:
            ordered_ids: 新顺序下的完整 ID 列表

        Returns:
            是否成功
        """
        try:
            self._project.reorder_singers(ordered_ids)
        except Exception:
            return False
        return True

    def move_singers(self, ids: List[str], direction: str) -> bool:
        """批量移动多个演唱者，保持选中项之间的相对顺序。

        语义（与用户对齐）：
        - up:     每个选中项依次向上滑动一位，撞到顶部或另一个选中项时停下。
        - down:   每个选中项依次向下滑动一位，撞到底部或另一个选中项时停下。
        - top:    选中项整体置顶，保持选中项之间的相对顺序。
        - bottom: 选中项整体置底，保持选中项之间的相对顺序。

        Args:
            ids: 待移动的演唱者 ID 集合（顺序无关，按当前列表中的位置处理）
            direction: 'up' | 'down' | 'top' | 'bottom'

        Returns:
            是否成功（顺序未变也算成功；非法方向或空 ids 返回 False）
        """
        if not ids or direction not in ("up", "down", "top", "bottom"):
            return False

        singers = self._project.singers
        all_ids = [s.id for s in singers]
        selected_set = set(ids)
        if not selected_set.issubset(set(all_ids)):
            return False

        if direction in ("top", "bottom"):
            # 保持选中项在原列表中的相对顺序
            ordered_selected = [sid for sid in all_ids if sid in selected_set]
            others = [sid for sid in all_ids if sid not in selected_set]
            new_order = (
                ordered_selected + others
                if direction == "top"
                else others + ordered_selected
            )
        else:
            new_order = list(all_ids)
            # 逐项滑动：up 从前往后扫，down 从后往前扫；
            # 选中项之间因为先后顺序的约束，自然形成"撞墙保间隔"。
            if direction == "up":
                indices = range(len(new_order))
            else:  # down
                indices = range(len(new_order) - 1, -1, -1)

            moved_positions: set = set()  # 已落定的选中项位置（防止被后续覆盖）
            for i in indices:
                if new_order[i] not in selected_set:
                    continue
                if direction == "up":
                    j = i - 1
                    if j < 0 or j in moved_positions or new_order[j] in selected_set:
                        moved_positions.add(i)
                        continue
                    new_order[i], new_order[j] = new_order[j], new_order[i]
                    moved_positions.add(j)
                else:  # down
                    j = i + 1
                    if (
                        j >= len(new_order)
                        or j in moved_positions
                        or new_order[j] in selected_set
                    ):
                        moved_positions.add(i)
                        continue
                    new_order[i], new_order[j] = new_order[j], new_order[i]
                    moved_positions.add(j)

        try:
            self._project.reorder_singers(new_order)
        except Exception:
            return False
        return True

    def batch_remove_singers(
        self, ids: List[str], transfer_to: Optional[str]
    ) -> bool:
        """批量删除演唱者。

        所有被删除演唱者所属的句子都会转移到 ``transfer_to`` 指向的演唱者；
        若 ``transfer_to`` 为 None，则级联删除其句子。

        若被删除集合中包含当前默认演唱者，会自动将 ``transfer_to``（若存在）
        设为新默认演唱者，避免项目失去默认演唱者。

        Args:
            ids: 要删除的演唱者 ID 列表
            transfer_to: 接收句子的演唱者 ID；必须不在 ids 中

        Returns:
            是否成功
        """
        if not ids:
            return False

        ids_set = set(ids)
        if transfer_to is not None and transfer_to in ids_set:
            return False

        # 校验：不能删空（必须至少保留一个演唱者）
        if len(self._project.singers) - len(ids_set) < 1:
            return False

        # 是否需要在删除后重新指定默认演唱者
        default = self._project.get_default_singer()
        need_reassign_default = default.id in ids_set

        try:
            for sid in ids:
                # 复用单删（已含句子转移/级联逻辑）。当 transfer_to 也是默认演唱者
                # 但该演唱者并未被删除时，安全无副作用。
                self._project.remove_singer(sid, transfer_to)
        except Exception:
            return False

        if need_reassign_default and transfer_to is not None:
            new_default = self._project.get_singer(transfer_to)
            if new_default is not None:
                for s in self._project.singers:
                    s.is_default = s.id == transfer_to

        if self._callbacks.on_singer_removed:
            for sid in ids:
                self._callbacks.on_singer_removed(sid)

        return True

    def batch_set_enabled(self, ids: List[str], enabled: bool) -> bool:
        """批量设置演唱者启用/禁用状态。

        Args:
            ids: 演唱者 ID 列表
            enabled: True 启用，False 禁用

        Returns:
            是否全部成功
        """
        if not ids:
            return False
        ok = True
        for sid in ids:
            singer = self._project.get_singer(sid)
            if not singer:
                ok = False
                continue
            singer.set_enabled(enabled)
            if self._callbacks.on_singer_updated:
                self._callbacks.on_singer_updated(singer)
        return ok

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
