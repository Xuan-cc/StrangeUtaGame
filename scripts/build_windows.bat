@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0\.."

set "PYTHON_BIN=python"
set "BUILD_NAME=KaraokeHelper"
set "APP_NAME=Karaoke Helper"
set "DIST_PATH=dist\windows"
set "WORK_PATH=build\pyinstaller-windows"
set "SPEC_PATH=build\spec-windows"
set "APP_DIST=%DIST_PATH%\%APP_NAME%"
set "BUILD_DIST=%DIST_PATH%\%BUILD_NAME%"
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
    --add-data "%CD%\krok_helper\assets;krok_helper\assets" ^
    --collect-all qfluentwidgets ^
    --collect-all yt_dlp ^
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
