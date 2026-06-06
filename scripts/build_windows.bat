@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0\.."

set "PYTHON_BIN=python"
set "BUILD_NAME=KaraokeStudio"
set "APP_NAME=Karaoke Studio"
set "DIST_PATH=dist\windows"
set "WORK_PATH=build\pyinstaller-windows"
set "SPEC_PATH=build\spec-windows"
set "APP_DIST=%DIST_PATH%\%APP_NAME%"
set "BUILD_DIST=%DIST_PATH%\%BUILD_NAME%"
set "SUG_SRC=%CD%\krok_helper\lyrics_timing\src"
set "SUG_PACKAGE=%SUG_SRC%\strange_uta_game"
set "IS_CI="
if defined CI set "IS_CI=1"

echo Checking Python...
where %PYTHON_BIN% >nul 2>&1
if errorlevel 1 (
    echo Python not found. Please install Python 3.10+ first.
    if not defined IS_CI pause
    exit /b 1
)

call :ensure_pkg PyInstaller pyinstaller || exit /b 1
call :ensure_pkg PyQt6 PyQt6 || exit /b 1
call :ensure_pkg qfluentwidgets "PyQt6-Fluent-Widgets" || exit /b 1
call :ensure_pkg yt_dlp yt-dlp || exit /b 1
call :ensure_pkg requests requests || exit /b 1
call :ensure_pkg psutil psutil || exit /b 1
call :ensure_pkg sounddevice sounddevice || exit /b 1
call :ensure_pkg soundfile soundfile || exit /b 1
call :ensure_pkg pedalboard pedalboard || exit /b 1
call :ensure_pkg numpy numpy || exit /b 1
call :ensure_pkg pykakasi pykakasi || exit /b 1
call :ensure_pkg jaconv jaconv || exit /b 1
call :ensure_pkg winrt.windows.globalization winrt-Windows.Globalization || exit /b 1
call :ensure_pkg winrt.windows.foundation winrt-Windows.Foundation || exit /b 1
call :ensure_pkg winrt.windows.foundation.collections winrt-Windows.Foundation.Collections || exit /b 1

echo Checking bundled SUG source path...
%PYTHON_BIN% -c "import sys; from pathlib import Path; src=Path(r'%SUG_SRC%').resolve(); sys.path.insert(0, str(src)); import strange_uta_game; actual=Path(strange_uta_game.__file__).resolve(); expected=src/'strange_uta_game'/'__init__.py'; print('  strange_uta_game:', actual); raise SystemExit(0 if actual == expected else f'Expected {expected}, got {actual}')" || exit /b 1

if not exist "krok_helper\updater_app\dist\Updater.exe" (
    echo Building Updater.exe...
    %PYTHON_BIN% krok_helper\updater_app\build_updater.py
    if errorlevel 1 (
        echo.
        echo Updater build failed.
        if not defined IS_CI pause
        exit /b 1
    )
)

if not exist "%DIST_PATH%" mkdir "%DIST_PATH%"
if not exist "%WORK_PATH%" mkdir "%WORK_PATH%"
if not exist "%SPEC_PATH%" mkdir "%SPEC_PATH%"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "Get-ChildItem -LiteralPath '%SPEC_PATH%' -Filter '*.spec' -File -ErrorAction SilentlyContinue | Remove-Item -Force"

echo Building Windows package...
%PYTHON_BIN% -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --name "%BUILD_NAME%" ^
    --distpath "%DIST_PATH%" ^
    --workpath "%WORK_PATH%" ^
    --specpath "%SPEC_PATH%" ^
    --paths "%SUG_SRC%" ^
    --add-data "%CD%\krok_helper\assets;krok_helper\assets" ^
    --add-data "%SUG_PACKAGE%\config;strange_uta_game\config" ^
    --add-data "%SUG_PACKAGE%\resource;strange_uta_game\resource" ^
    --add-data "%SUG_PACKAGE%\bass;strange_uta_game\bass" ^
    --collect-all qfluentwidgets ^
    --collect-all yt_dlp ^
    --collect-all sounddevice ^
    --collect-all soundfile ^
    --collect-all pedalboard ^
    --collect-all pykakasi ^
    --collect-all winrt ^
    --collect-binaries soundfile ^
    --collect-submodules strange_uta_game ^
    --hidden-import sounddevice ^
    --hidden-import soundfile ^
    --hidden-import pedalboard ^
    --hidden-import pedalboard.io ^
    --hidden-import pedalboard.io.AudioFile ^
    --hidden-import pedalboard.io.StreamResampler ^
    --hidden-import pedalboard.time_stretch ^
    --hidden-import numpy ^
    --hidden-import pykakasi ^
    --hidden-import pykakasi.kakasi ^
    --hidden-import jaconv ^
    --hidden-import PyQt6.sip ^
    --hidden-import encodings.idna ^
    --hidden-import colorsys ^
    --hidden-import winrt.windows.globalization ^
    --hidden-import winrt.windows.foundation ^
    --hidden-import winrt.windows.foundation.collections ^
    --exclude-module sudachipy ^
    --exclude-module sudachidict_small ^
    --exclude-module sudachidict_core ^
    --exclude-module sudachidict_full ^
    --exclude-module scipy ^
    --exclude-module matplotlib ^
    --exclude-module pandas ^
    --exclude-module pytest ^
    --exclude-module PIL ^
    --exclude-module PySide6 ^
    --exclude-module PyQt5 ^
    --exclude-module PyQt6.Qt3DAnimation ^
    --exclude-module PyQt6.Qt3DCore ^
    --exclude-module PyQt6.Qt3DExtras ^
    --exclude-module PyQt6.Qt3DInput ^
    --exclude-module PyQt6.Qt3DLogic ^
    --exclude-module PyQt6.Qt3DRender ^
    --exclude-module PyQt6.QtCharts ^
    --exclude-module PyQt6.QtDataVisualization ^
    --exclude-module PyQt6.QtDesigner ^
    --exclude-module PyQt6.QtMultimedia ^
    --exclude-module PyQt6.QtMultimediaWidgets ^
    --exclude-module PyQt6.QtNetworkAuth ^
    --exclude-module PyQt6.QtPdf ^
    --exclude-module PyQt6.QtPdfWidgets ^
    --exclude-module PyQt6.QtPositioning ^
    --exclude-module PyQt6.QtQml ^
    --exclude-module PyQt6.QtQuick ^
    --exclude-module PyQt6.QtQuick3D ^
    --exclude-module PyQt6.QtQuickControls2 ^
    --exclude-module PyQt6.QtQuickTest ^
    --exclude-module PyQt6.QtQuickWidgets ^
    --exclude-module PyQt6.QtRemoteObjects ^
    --exclude-module PyQt6.QtScxml ^
    --exclude-module PyQt6.QtSensors ^
    --exclude-module PyQt6.QtSerialPort ^
    --exclude-module PyQt6.QtSql ^
    --exclude-module PyQt6.QtStateMachine ^
    --exclude-module PyQt6.QtTest ^
    --exclude-module PyQt6.QtTextToSpeech ^
    --exclude-module PyQt6.QtWebChannel ^
    --exclude-module PyQt6.QtWebEngineCore ^
    --exclude-module PyQt6.QtWebEngineQuick ^
    --exclude-module PyQt6.QtWebEngineWidgets ^
    --exclude-module PyQt6.QtWebSockets ^
    --exclude-module PyQt6.QtWebView ^
    app.py

if errorlevel 1 (
    echo.
    echo Build failed.
    if not defined IS_CI pause
    exit /b 1
)

echo Trimming Windows package...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$root = Resolve-Path '%BUILD_DIST%\_internal';" ^
    "$qtRoot = @(Get-ChildItem -LiteralPath $root -Directory -Filter 'PyQt6' -ErrorAction SilentlyContinue) + @(Get-ChildItem -LiteralPath $root -Directory -Filter 'PySide6' -ErrorAction SilentlyContinue) | Select-Object -First 1;" ^
    "if (-not $qtRoot) { Write-Host 'PyQt6 directory not found, skipping trim.'; exit 0 };" ^
    "$qt6 = Join-Path $qtRoot.FullName 'Qt6';" ^
    "if (-not (Test-Path $qt6)) { $qt6 = $qtRoot.FullName };" ^
    "$translations = Join-Path $qt6 'translations';" ^
    "if (Test-Path $translations) { Get-ChildItem $translations -File | Where-Object { $_.Name -notin @('qtbase_zh_CN.qm','qtbase_zh_TW.qm','qtbase_ja.qm','qt_zh_CN.qm','qt_zh_TW.qm','qt_ja.qm') } | Remove-Item -Force };" ^
    "$plugins = Join-Path $qt6 'plugins';" ^
    "$removeFiles = @('platforms\qdirect2d.dll','platforms\qminimal.dll','platforms\qoffscreen.dll','imageformats\qwebp.dll','imageformats\qtiff.dll','imageformats\qicns.dll','imageformats\qgif.dll','imageformats\qpdf.dll','imageformats\qtga.dll','imageformats\qwbmp.dll','iconengines\qsvgicon.dll','tls\qopensslbackend.dll','tls\qcertonlybackend.dll','generic\qtuiotouchplugin.dll','networkinformation\qnetworklistmanager.dll','platforminputcontexts\qtvirtualkeyboardplugin.dll');" ^
    "foreach ($rel in $removeFiles) { $path = Join-Path $plugins $rel; if (Test-Path $path) { Remove-Item -LiteralPath $path -Force } };" ^
    "$removeDirs = @('generic','networkinformation','platforminputcontexts');" ^
    "foreach ($rel in $removeDirs) { $path = Join-Path $plugins $rel; if ((Test-Path $path -PathType Container) -and -not (Get-ChildItem $path -Force)) { Remove-Item -LiteralPath $path -Force } };" ^
    "$dlls = @('Qt6Pdf.dll','Qt6VirtualKeyboard.dll','Qt6Multimedia.dll','Qt6Quick.dll','Qt6Qml.dll');" ^
    "foreach ($base in @($qt6, (Join-Path $qt6 'bin'), $root)) { if (-not (Test-Path $base)) { continue }; foreach ($name in $dlls) { $path = Join-Path $base $name; if (Test-Path $path) { Remove-Item -LiteralPath $path -Force } } }"
if errorlevel 1 (
    echo.
    echo Package trimming failed.
    if not defined IS_CI pause
    exit /b 1
)

echo Renaming Windows package...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$distRoot = Resolve-Path '%DIST_PATH%';" ^
    "$buildDir = Join-Path $distRoot '%BUILD_NAME%';" ^
    "$targetDir = Join-Path $distRoot '%APP_NAME%';" ^
    "if (-not (Test-Path $buildDir -PathType Container)) { throw 'Build output directory not found.' };" ^
    "if (Test-Path $targetDir) { Remove-Item -LiteralPath $targetDir -Recurse -Force };" ^
    "if (Test-Path $targetDir) { throw 'Existing target directory could not be removed.' };" ^
    "$buildExe = Join-Path $buildDir ('%BUILD_NAME%' + '.exe');" ^
    "function Invoke-WithRetry($block, $label) { for ($i = 1; $i -le 8; $i++) { try { & $block; return } catch { if ($i -eq 8) { throw } ; Write-Host (\"  ${label}: locked, retrying ($i/7)...\"); Start-Sleep -Milliseconds 800 } } };" ^
    "if (Test-Path $buildExe -PathType Leaf) { Invoke-WithRetry { Rename-Item -LiteralPath $buildExe -NewName ('%APP_NAME%' + '.exe') -Force -ErrorAction Stop } 'exe' };" ^
    "Invoke-WithRetry { Rename-Item -LiteralPath $buildDir -NewName '%APP_NAME%' -Force -ErrorAction Stop } 'dir'"
if errorlevel 1 (
    echo.
    echo Package rename failed.
    if not defined IS_CI pause
    exit /b 1
)

echo Copying Updater.exe...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$targetDir = Resolve-Path '%APP_DIST%';" ^
    "$updater = Resolve-Path 'krok_helper\updater_app\dist\Updater.exe';" ^
    "Copy-Item -LiteralPath $updater -Destination (Join-Path $targetDir 'Updater.exe') -Force"
if errorlevel 1 (
    echo.
    echo Failed to copy Updater.exe.
    if not defined IS_CI pause
    exit /b 1
)

echo Validating Windows package contents...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$targetDir = Resolve-Path '%APP_DIST%';" ^
    "$internal = Join-Path $targetDir '_internal';" ^
    "$required = @(" ^
    "  'krok_helper\assets\logo\logo.jpg'," ^
    "  'krok_helper\assets\platforms\youtube.svg'," ^
    "  'strange_uta_game\config\config.json'," ^
    "  'strange_uta_game\config\dictionary.json'," ^
    "  'strange_uta_game\config\cmudict-0.7b'," ^
    "  'strange_uta_game\config\kanji_readings.json'," ^
    "  'strange_uta_game\resource\icon.ico'," ^
    "  'strange_uta_game\resource\sounds\press.wav'," ^
    "  'strange_uta_game\bass\x64\bass.dll'," ^
    "  'strange_uta_game\bass\x64\bass_fx.dll'," ^
    "  'Updater.exe'" ^
    ");" ^
    "$missing = @();" ^
    "foreach ($rel in $required) { $base = if ($rel -eq 'Updater.exe') { $targetDir } else { $internal }; $path = Join-Path $base $rel; if (-not (Test-Path $path -PathType Leaf)) { $missing += $path } };" ^
    "if ($missing.Count) { Write-Host 'Missing package files:'; $missing | ForEach-Object { Write-Host ('  ' + $_) }; exit 1 };" ^
    "$warnRoot = Join-Path '%WORK_PATH%' '%BUILD_NAME%';" ^
    "$warn = if (Test-Path $warnRoot) { Get-ChildItem -LiteralPath $warnRoot -Recurse -Filter 'warn-*.txt' -File -ErrorAction SilentlyContinue | Select-Object -First 1 } else { $null };" ^
    "if ($warn) { Write-Host ('PyInstaller warnings were written to: ' + $warn.FullName) };" ^
    "Write-Host 'Package content validation passed.'"
if errorlevel 1 (
    echo.
    echo Package validation failed.
    if not defined IS_CI pause
    exit /b 1
)

echo.
echo Build complete:
echo %CD%\%APP_DIST%
if not defined IS_CI pause
exit /b 0

:ensure_pkg
echo Checking %~1...
%PYTHON_BIN% -c "import %~1" >nul 2>&1
if errorlevel 1 (
    echo %~1 not found, installing %~2...
    %PYTHON_BIN% -m pip install %~2
    if errorlevel 1 (
        echo Failed to install %~2.
        if not defined IS_CI pause
        exit /b 1
    )
)
exit /b 0
