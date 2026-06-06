#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="Karaoke Studio"
DIST_PATH="$PROJECT_ROOT/dist/macos"
WORK_PATH="$PROJECT_ROOT/build/pyinstaller-macos"
SPEC_PATH="$PROJECT_ROOT/build/spec-macos"
APP_DIST="$DIST_PATH/$APP_NAME.app"
SUG_SRC="$PROJECT_ROOT/krok_helper/lyrics_timing/src"
SUG_PACKAGE="$SUG_SRC/strange_uta_game"
SUG_VERSION_FILE="$SUG_PACKAGE/__version__.py"
SUG_VERSION_BACKUP=""

EXCLUDED_MODULES=(
  PySide6
  PyQt5
  PyQt6.Qt3DAnimation
  PyQt6.Qt3DCore
  PyQt6.Qt3DExtras
  PyQt6.Qt3DInput
  PyQt6.Qt3DLogic
  PyQt6.Qt3DRender
  PyQt6.QtCharts
  PyQt6.QtDataVisualization
  PyQt6.QtDesigner
  PyQt6.QtMultimedia
  PyQt6.QtMultimediaWidgets
  PyQt6.QtNetworkAuth
  PyQt6.QtPdf
  PyQt6.QtPdfWidgets
  PyQt6.QtPositioning
  PyQt6.QtQml
  PyQt6.QtQuick
  PyQt6.QtQuick3D
  PyQt6.QtQuickControls2
  PyQt6.QtQuickTest
  PyQt6.QtQuickWidgets
  PyQt6.QtRemoteObjects
  PyQt6.QtScxml
  PyQt6.QtSensors
  PyQt6.QtSerialPort
  PyQt6.QtSql
  PyQt6.QtStateMachine
  PyQt6.QtTest
  PyQt6.QtTextToSpeech
  PyQt6.QtWebChannel
  PyQt6.QtWebEngineCore
  PyQt6.QtWebEngineQuick
  PyQt6.QtWebEngineWidgets
  PyQt6.QtWebSockets
  PyQt6.QtWebView
  winrt
  winrt.windows.globalization
  winrt.windows.foundation
  sudachidict_core
  sudachidict_full
  scipy
  matplotlib
  pandas
  pytest
  PIL
)

KEEP_TRANSLATIONS=(
  qtbase_zh_CN.qm
  qtbase_zh_TW.qm
  qtbase_ja.qm
  qt_zh_CN.qm
  qt_zh_TW.qm
  qt_ja.qm
)

REMOVE_PLUGIN_FILES=(
  "platforms/libqminimal.dylib"
  "platforms/libqoffscreen.dylib"
  "imageformats/libqgif.dylib"
  "imageformats/libqicns.dylib"
  "imageformats/libqpdf.dylib"
  "imageformats/libqtga.dylib"
  "imageformats/libqtiff.dylib"
  "imageformats/libqwbmp.dylib"
  "imageformats/libqwebp.dylib"
  "iconengines/libqsvgicon.dylib"
  "tls/libqcertonlybackend.dylib"
  "tls/libqopensslbackend.dylib"
  "generic/libqtuiotouchplugin.dylib"
  "networkinformation/libqnetworklistmanager.dylib"
  "platforminputcontexts/libqtvirtualkeyboardplugin.dylib"
)

REMOVE_PLUGIN_DIRS=(
  generic
  networkinformation
  platforminputcontexts
)

REMOVE_QT_LIBS=(
  "QtPdf.framework"
  "QtVirtualKeyboard.framework"
  "QtMultimedia.framework"
  "QtQml.framework"
  "QtQuick.framework"
)

ensure_pkg() {
  local module="$1"
  local pip_name="$2"
  echo "Checking $module..."
  if ! "$PYTHON_BIN" -c "import $module" >/dev/null 2>&1; then
    echo "$module not found, installing $pip_name..."
    if ! "$PYTHON_BIN" -m pip install "$pip_name"; then
      echo "Failed to install $pip_name."
      if [ -z "${CI:-}" ]; then
        read -r -p "Press Enter to close..."
      fi
      exit 1
    fi
  fi
}

echo "Checking Python..."
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 not found. Please install Python 3.10+ first."
  if [ -z "${CI:-}" ]; then
    read -r -p "Press Enter to close..."
  fi
  exit 1
fi

ensure_pkg PyInstaller pyinstaller
ensure_pkg PyQt6 PyQt6
ensure_pkg qfluentwidgets "PyQt6-Fluent-Widgets"
ensure_pkg yt_dlp yt-dlp
ensure_pkg requests requests
ensure_pkg psutil psutil
ensure_pkg sounddevice sounddevice
ensure_pkg soundfile soundfile
ensure_pkg pedalboard pedalboard
ensure_pkg numpy numpy
ensure_pkg pykakasi pykakasi
ensure_pkg jaconv jaconv
ensure_pkg sudachipy sudachipy
ensure_pkg sudachidict_small sudachidict_small

echo "Checking bundled SUG source path..."
"$PYTHON_BIN" - <<PY
import sys
from pathlib import Path
src = Path(r"$SUG_SRC").resolve()
sys.path.insert(0, str(src))
import strange_uta_game
actual = Path(strange_uta_game.__file__).resolve()
expected = src / "strange_uta_game" / "__init__.py"
print(f"  strange_uta_game: {actual}")
raise SystemExit(0 if actual == expected else f"Expected {expected}, got {actual}")
PY

SUG_VERSION_BACKUP="$(mktemp)"
cp "$SUG_VERSION_FILE" "$SUG_VERSION_BACKUP"
restore_sug_version() {
  if [ -n "$SUG_VERSION_BACKUP" ] && [ -f "$SUG_VERSION_BACKUP" ]; then
    cp "$SUG_VERSION_BACKUP" "$SUG_VERSION_FILE"
    rm -f "$SUG_VERSION_BACKUP"
  fi
}
trap restore_sug_version EXIT

echo "Setting SUG package variant to mac for this build..."
"$PYTHON_BIN" - <<PY
from pathlib import Path
import re
path = Path(r"$SUG_VERSION_FILE")
text = path.read_text(encoding="utf-8")
patched = re.sub(r'^(VARIANT\s*=\s*)"[^"]*"', r'\1"mac"', text, flags=re.MULTILINE)
if patched == text:
    raise SystemExit("Could not patch VARIANT in strange_uta_game/__version__.py")
path.write_text(patched, encoding="utf-8")
PY

mkdir -p "$DIST_PATH" "$WORK_PATH" "$SPEC_PATH"

PYINSTALLER_ARGS=(
  --noconfirm
  --clean
  --windowed
  --onedir
  --name "$APP_NAME"
  --distpath "$DIST_PATH"
  --workpath "$WORK_PATH"
  --specpath "$SPEC_PATH"
  --paths "$SUG_SRC"
  --add-data "$PROJECT_ROOT/krok_helper/assets:krok_helper/assets"
  --add-data "$SUG_PACKAGE/config:strange_uta_game/config"
  --add-data "$SUG_PACKAGE/resource:strange_uta_game/resource"
  --add-data "$SUG_PACKAGE/bass:strange_uta_game/bass"
  --collect-all qfluentwidgets
  --collect-all yt_dlp
  --collect-all sounddevice
  --collect-all soundfile
  --collect-all pedalboard
  --collect-all pykakasi
  --collect-all sudachipy
  --collect-data sudachidict_small
  --collect-binaries soundfile
  --collect-submodules strange_uta_game
  --hidden-import sounddevice
  --hidden-import soundfile
  --hidden-import pedalboard
  --hidden-import pedalboard.io
  --hidden-import pedalboard.io.AudioFile
  --hidden-import pedalboard.io.StreamResampler
  --hidden-import pedalboard.time_stretch
  --hidden-import numpy
  --hidden-import pykakasi
  --hidden-import pykakasi.kakasi
  --hidden-import jaconv
  --hidden-import sudachipy
  --hidden-import sudachidict_small
  --hidden-import PyQt6.sip
  --hidden-import encodings.idna
  --hidden-import colorsys
)

for module in "${EXCLUDED_MODULES[@]}"; do
  PYINSTALLER_ARGS+=(--exclude-module "$module")
done

echo "Building macOS package..."
if ! "$PYTHON_BIN" -m PyInstaller "${PYINSTALLER_ARGS[@]}" app.py; then
  echo
  echo "Build failed."
  if [ -z "${CI:-}" ]; then
    read -r -p "Press Enter to close..."
  fi
  exit 1
fi

echo "Trimming macOS package..."
PYQT_DIR="$(find "$APP_DIST" -type d \( -name PyQt6 -o -name PySide6 \) -print -quit || true)"
if [ -z "$PYQT_DIR" ] || [ ! -d "$PYQT_DIR" ]; then
  echo "PyQt6 directory not found inside $APP_DIST, skipping trim."
else
  TRANSLATIONS_DIR="$(find "$PYQT_DIR" -type d -name translations -print -quit || true)"
  if [ -n "$TRANSLATIONS_DIR" ] && [ -d "$TRANSLATIONS_DIR" ]; then
    while IFS= read -r -d '' file; do
      keep_file=0
      for keep in "${KEEP_TRANSLATIONS[@]}"; do
        if [ "$(basename "$file")" = "$keep" ]; then
          keep_file=1
          break
        fi
      done
      if [ "$keep_file" -eq 0 ]; then
        rm -f "$file"
      fi
    done < <(find "$TRANSLATIONS_DIR" -type f -print0)
  fi

  PLUGINS_DIR="$(find "$PYQT_DIR" -type d -name plugins -print -quit || true)"
  if [ -n "$PLUGINS_DIR" ] && [ -d "$PLUGINS_DIR" ]; then
    for rel in "${REMOVE_PLUGIN_FILES[@]}"; do
      target="$PLUGINS_DIR/$rel"
      if [ -e "$target" ]; then
        rm -f "$target"
      fi
    done

    for rel in "${REMOVE_PLUGIN_DIRS[@]}"; do
      target="$PLUGINS_DIR/$rel"
      if [ -d "$target" ] && [ -z "$(find "$target" -mindepth 1 -print -quit)" ]; then
        rmdir "$target"
      fi
    done
  fi
fi

for rel in "${REMOVE_QT_LIBS[@]}"; do
  while IFS= read -r -d '' target; do
    rm -rf "$target"
  done < <(find "$APP_DIST" -name "$rel" -print0)
done

echo "Validating macOS package contents..."
APP_INTERNAL="$APP_DIST/Contents/Frameworks"
if [ ! -d "$APP_INTERNAL" ]; then
  APP_INTERNAL="$APP_DIST/Contents/Resources"
fi
if [ ! -d "$APP_INTERNAL" ]; then
  echo "Could not locate PyInstaller internal directory in $APP_DIST"
  exit 1
fi

REQUIRED_FILES=(
  "krok_helper/assets/logo/logo.jpg"
  "krok_helper/assets/platforms/youtube.svg"
  "strange_uta_game/config/config.json"
  "strange_uta_game/config/dictionary.json"
  "strange_uta_game/config/cmudict-0.7b"
  "strange_uta_game/config/kanji_readings.json"
  "strange_uta_game/resource/icon.ico"
  "strange_uta_game/resource/sounds/press.wav"
)
missing=0
for rel in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$APP_INTERNAL/$rel" ]; then
    echo "Missing package file: $APP_INTERNAL/$rel"
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  exit 1
fi
warn_file="$(find "$WORK_PATH" -name 'warn-*.txt' -type f -print -quit || true)"
if [ -n "$warn_file" ]; then
  echo "PyInstaller warnings were written to: $warn_file"
fi
echo "Package content validation passed."

echo
echo "Build complete:"
echo "$APP_DIST"
if [ -z "${CI:-}" ]; then
  read -r -p "Press Enter to close..."
fi
