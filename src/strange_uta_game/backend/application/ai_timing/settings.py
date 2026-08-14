"""AI 打轴设置（阶段 D）。

standalone 与 embedded 共用同一份字段结构；读取/写入通过 getter/setter
适配器完成，SUG 前端（AppSettings 的 ``ai_timing.*`` 节）与工作台宿主
（阶段 G 的 AiTimingHost）各自接线，避免应用层直接依赖前端模块。

模型根目录默认值不在 ``.cache`` 下——``.cache`` 会被自动清理，模型
权重绝不放入自动清理范围（§7.1）。
"""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from strange_uta_game.app_dirs import config_dir

SETTINGS_SECTION = "ai_timing"


@dataclass
class AiTimingSettings:
    """AI 打轴的用户可配置项。"""

    model_root: str = ""
    """对齐模型根目录；空串 = 默认目录（config_dir/ai_models）。"""

    provider: str = "wav2vec2"
    """对齐 provider：wav2vec2（默认效果优先）/ mms_fa（备选）。"""

    wav2vec2_model_id: str = ""
    """微调模型 ID（Hugging Face repo id 或本地路径）；空 = 内置默认。"""

    device: str = "auto"
    """推理设备：auto / cpu / cuda。"""

    download_mirror: str = ""
    """Hugging Face 镜像端点（如 https://hf-mirror.com）；空 = 官方源。"""

    runtime_python: str = ""
    """对齐 Runtime 的 python.exe 路径（standalone 自管；空 = 使用当前解释器）。"""

    ai_cache_root: str = ""
    """AI 缓存根目录（standalone 自定义位置；空 = 默认 .cache/ai_timing；
    embedded 由宿主注入，此字段不生效）。"""

    tail_snap: bool = True
    """尾音修正：token 终点吸附到下一 token 起点。"""

    audio_speed: float = 1.0
    """音频倍速预处理（预留，阶段 C worker options 消费）。"""


def default_model_root() -> Path:
    """默认模型根目录（不在自动清理的 .cache 内）。"""
    return config_dir() / "ai_models"


def resolve_model_root(settings: AiTimingSettings) -> Path:
    return Path(settings.model_root) if settings.model_root else default_model_root()


Getter = Callable[[str, object], object]
"""``getter(path, default) -> value`` 适配器。"""


def load_ai_timing_settings(getter: Getter) -> AiTimingSettings:
    """从设置存储读取 ai_timing 节（缺省字段回落默认值）。"""
    base = AiTimingSettings()
    return AiTimingSettings(
        model_root=str(getter(f"{SETTINGS_SECTION}.model_root", base.model_root)),
        provider=str(getter(f"{SETTINGS_SECTION}.provider", base.provider)),
        wav2vec2_model_id=str(
            getter(f"{SETTINGS_SECTION}.wav2vec2_model_id", base.wav2vec2_model_id)
        ),
        device=str(getter(f"{SETTINGS_SECTION}.device", base.device)),
        download_mirror=str(
            getter(f"{SETTINGS_SECTION}.download_mirror", base.download_mirror)
        ),
        runtime_python=str(
            getter(f"{SETTINGS_SECTION}.runtime_python", base.runtime_python)
        ),
        ai_cache_root=str(
            getter(f"{SETTINGS_SECTION}.ai_cache_root", base.ai_cache_root)
        ),
        tail_snap=bool(getter(f"{SETTINGS_SECTION}.tail_snap", base.tail_snap)),
        audio_speed=float(
            getter(f"{SETTINGS_SECTION}.audio_speed", base.audio_speed)
        ),
    )


def save_ai_timing_settings(
    setter: Callable[[str, object], None], settings: AiTimingSettings
) -> None:
    """把全部字段写回设置存储（由调用方负责持久化/保存触发）。"""
    for key, value in asdict(settings).items():
        setter(f"{SETTINGS_SECTION}.{key}", value)


__all__ = [
    "SETTINGS_SECTION",
    "AiTimingSettings",
    "default_model_root",
    "resolve_model_root",
    "load_ai_timing_settings",
    "save_ai_timing_settings",
]
