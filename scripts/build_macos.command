#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
APP_NAME="Karaoke Helper"
DIST_PATH="$PROJECT_ROOT/dist/macos"
WORK_PATH="$PROJECT_ROOT/build/pyinstaller-macos"
SPEC_PATH="$PROJECT_ROOT/build/spec-macos"
APP_DIST="$DIST_PATH/$APP_NAME.app"

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
  --add-data "$PROJECT_ROOT/krok_helper/assets:krok_helper/assets"
  --collect-all qfluentwidgets
  --collect-all yt_dlp
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

echo
echo "Build complete:"
echo "$APP_DIST"
if [ -z "${CI:-}" ]; then
  read -r -p "Press Enter to close..."
fi
