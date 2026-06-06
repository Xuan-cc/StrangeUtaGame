from __future__ import annotations

import sys

from krok_helper.lyrics_timing.updater_app import main as updater_main


def main(argv: list[str] | None = None) -> int:
    updater_main.TMP_DIR_NAME = "KaraokeStudioUpdater"
    updater_main.PRODUCT_NAME = "Karaoke Studio"
    updater_main.DEFAULT_USER_AGENT = "KaraokeStudio-Updater/standalone"
    if argv is None:
        return updater_main.main()
    old_argv = sys.argv[:]
    try:
        sys.argv = [old_argv[0], *argv]
        return updater_main.main()
    finally:
        sys.argv = old_argv

if __name__ == "__main__":
    raise SystemExit(main())
