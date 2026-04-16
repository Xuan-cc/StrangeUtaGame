"""音频引擎接口定义。

抽象音频播放能力，屏蔽具体音频库差异。
"""

from abc import ABC, abstractmethod
from typing import Callable, Optional
from dataclasses import dataclass
from enum import Enum, auto


class AudioError(Exception):
    """音频错误基类"""

    pass


class AudioLoadError(AudioError):
    """音频加载错误"""

    pass


class AudioPlaybackError(AudioError):
    """音频播放错误"""

    pass


class PlaybackState(Enum):
    """播放状态"""

    STOPPED = auto()
    PLAYING = auto()
    PAUSED = auto()


@dataclass
class AudioInfo:
    """音频文件信息"""

    file_path: str
    duration_ms: int
    sample_rate: int
    channels: int


class IAudioEngine(ABC):
    """音频引擎接口

    抽象音频播放能力，屏蔽具体音频库差异。

    实现类需要提供：
    - 异步播放（非阻塞 API）
    - 位置回调频率：~60fps
    - 时间精度：毫秒
    - 变速范围：0.5x ~ 2.0x
    """

    @abstractmethod
    def load(self, file_path: str) -> None:
        """加载音频文件

        Args:
            file_path: 音频文件路径

        Raises:
            AudioLoadError: 加载失败（文件不存在、格式不支持等）
        """
        pass

    @abstractmethod
    def play(self) -> None:
        """开始播放"""
        pass

    @abstractmethod
    def pause(self) -> None:
        """暂停播放"""
        pass

    @abstractmethod
    def stop(self) -> None:
        """停止播放（位置重置到开头）"""
        pass

    @abstractmethod
    def get_position_ms(self) -> int:
        """获取当前播放位置（毫秒）

        Returns:
            当前位置（毫秒）
        """
        pass

    @abstractmethod
    def set_position_ms(self, position_ms: int) -> None:
        """设置播放位置（毫秒）

        Args:
            position_ms: 目标位置（毫秒）
        """
        pass

    @abstractmethod
    def get_duration_ms(self) -> int:
        """获取音频总时长（毫秒）

        Returns:
            总时长（毫秒）
        """
        pass

    @abstractmethod
    def get_playback_state(self) -> PlaybackState:
        """获取播放状态

        Returns:
            当前播放状态
        """
        pass

    @abstractmethod
    def is_playing(self) -> bool:
        """是否正在播放"""
        pass

    @abstractmethod
    def set_speed(self, speed: float) -> None:
        """设置播放速度

        Args:
            speed: 速度倍率（0.5 ~ 2.0）

        Raises:
            ValueError: 速度超出范围
        """
        pass

    @abstractmethod
    def get_speed(self) -> float:
        """获取当前播放速度

        Returns:
            速度倍率
        """
        pass

    @abstractmethod
    def set_volume(self, volume: float) -> None:
        """设置音量

        Args:
            volume: 音量（0.0 ~ 1.0）
        """
        pass

    @abstractmethod
    def get_volume(self) -> float:
        """获取当前音量"""
        pass

    @abstractmethod
    def set_position_callback(self, callback: Callable[[int], None]) -> None:
        """设置位置变化回调

        回调函数将在播放位置变化时被调用，频率约为 60fps。

        Args:
            callback: 回调函数，参数为当前位置（毫秒）
        """
        pass

    @abstractmethod
    def clear_position_callback(self) -> None:
        """清除位置变化回调"""
        pass

    @abstractmethod
    def get_audio_info(self) -> Optional[AudioInfo]:
        """获取音频文件信息

        Returns:
            音频信息，如果没有加载音频则返回 None
        """
        pass

    @abstractmethod
    def release(self) -> None:
        """释放资源

        在不再需要音频引擎时调用，释放相关资源。
        """
        pass
