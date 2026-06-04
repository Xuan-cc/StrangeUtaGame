from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class MediaInfo:
    path: Path
    duration: float
    video_streams: int
    audio_streams: int
    subtitle_streams: int
    sample_rate: int | None = None
    channels: int | None = None
