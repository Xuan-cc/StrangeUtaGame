"""Embedding helpers for the bundled StrangeUtaGame sources."""

from __future__ import annotations

import sys
from pathlib import Path

_SRC_DIR = Path(__file__).resolve().parent / "src"
_SRC_DIR_STR = str(_SRC_DIR)
if _SRC_DIR_STR not in sys.path:
    sys.path.insert(0, _SRC_DIR_STR)
