from .base import SubSettingInterface
from .playback import PlaybackSubInterface
from .timing import TimingSubInterface
from .auto_save import AutoSaveSubInterface
from .auto_check import AutoCheckSubInterface
from .dictionary import DictionarySubInterface
from .ui_settings import UISubInterface
from .export import ExportSubInterface
from .shortcut import ShortcutSubInterface
from .network import NetworkSubInterface
from .about import AboutSubInterface

__all__ = [
    "SubSettingInterface",
    "PlaybackSubInterface",
    "TimingSubInterface",
    "AutoSaveSubInterface",
    "AutoCheckSubInterface",
    "DictionarySubInterface",
    "UISubInterface",
    "ExportSubInterface",
    "ShortcutSubInterface",
    "NetworkSubInterface",
    "AboutSubInterface",
]
