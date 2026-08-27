@echo off
title asr-online-service
setlocal EnableExtensions EnableDelayedExpansion

pushd "%~dp0"
if errorlevel 1 (
  echo Failed to switch to service directory
  pause
  exit /b 1
)

set "TOOLS_DIR=%CD%\tools"

set "FFMPEG_DIR=%TOOLS_DIR%\ffmpeg"
set "FFMPEG_BIN_DIR=%FFMPEG_DIR%\bin"
set "FFMPEG_EXE=%FFMPEG_BIN_DIR%\ffmpeg.exe"
set "FFMPEG_ZIP=%TOOLS_DIR%\ffmpeg.zip"
set "FFMPEG_TMP=%TOOLS_DIR%\_tmp_ffmpeg"

set "SOX_DIR=%TOOLS_DIR%\sox"
set "SOX_EXE=%SOX_DIR%\sox.exe"
set "SOX_ZIP=%TOOLS_DIR%\sox.zip"
set "SOX_TMP=%TOOLS_DIR%\_tmp_sox"

set "FFMPEG_URL=https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
set "SOX_URL=https://downloads.sourceforge.net/project/sox/sox/14.4.2/sox-14.4.2-win32.zip"

if not exist ".env" (
  echo Creating .env from .env.example
  copy /y ".env.example" ".env" >nul
  if errorlevel 1 (
    echo Failed to create .env from .env.example
    popd
    pause
    exit /b 1
  )
)

if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

if exist poetry.lock del /f /q poetry.lock
if errorlevel 1 (
  echo Failed to remove poetry.lock
  popd
  pause
  exit /b 1
)

if not exist "%FFMPEG_EXE%" (
  echo ffmpeg not found, downloading and extracting...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_extract.ps1" -Url "%FFMPEG_URL%" -OutZip "%FFMPEG_ZIP%" -TmpDir "%FFMPEG_TMP%"
  if errorlevel 1 (
    echo ffmpeg download/extract failed
    popd
    pause
    exit /b 1
  )

  if not exist "%FFMPEG_BIN_DIR%" mkdir "%FFMPEG_BIN_DIR%"

  set "FOUND_FFMPEG="
  for /f "usebackq delims=" %%F in (`powershell -NoProfile -Command "Get-ChildItem -Recurse -Path '%FFMPEG_TMP%' -Filter ffmpeg.exe | Select-Object -First 1 -ExpandProperty FullName"`) do set "FOUND_FFMPEG=%%F"

  if not defined FOUND_FFMPEG (
    echo ffmpeg.exe not found after extraction in %FFMPEG_TMP%
    popd
    pause
    exit /b 1
  )

  powershell -NoProfile -ExecutionPolicy Bypass -Command "$src=$env:FOUND_FFMPEG; $dst=$env:FFMPEG_EXE; $dstDir=Split-Path -Parent $dst; New-Item -ItemType Directory -Path $dstDir -Force | Out-Null; if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }; Copy-Item -LiteralPath $src -Destination $dst -Force; if (-not (Test-Path -LiteralPath $dst)) { exit 1 }"
  if errorlevel 1 (
    echo Failed to copy ffmpeg.exe into %FFMPEG_EXE%
    popd
    pause
    exit /b 1
  )
) else (
  echo ffmpeg already exists: %FFMPEG_EXE%
)

if not exist "%SOX_EXE%" (
  echo sox not found, downloading and extracting...
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download_extract.ps1" -Url "%SOX_URL%" -OutZip "%SOX_ZIP%" -TmpDir "%SOX_TMP%"
  if errorlevel 1 (
    echo sox download/extract failed
    popd
    pause
    exit /b 1
  )

  if not exist "%SOX_DIR%" mkdir "%SOX_DIR%"

  set "FOUND_SOX="
  for /f "usebackq delims=" %%S in (`powershell -NoProfile -Command "Get-ChildItem -Recurse -Path '%SOX_TMP%' -Filter sox.exe | Select-Object -First 1 -ExpandProperty FullName"`) do set "FOUND_SOX=%%S"

  if not defined FOUND_SOX (
    echo sox.exe not found after extraction in %SOX_TMP%
    popd
    pause
    exit /b 1
  )

  powershell -NoProfile -ExecutionPolicy Bypass -Command "$src=$env:FOUND_SOX; $dst=$env:SOX_EXE; $dstDir=Split-Path -Parent $dst; New-Item -ItemType Directory -Path $dstDir -Force | Out-Null; if (Test-Path -LiteralPath $dst) { Remove-Item -LiteralPath $dst -Force }; Copy-Item -LiteralPath $src -Destination $dst -Force; if (-not (Test-Path -LiteralPath $dst)) { exit 1 }"
  if errorlevel 1 (
    echo Failed to copy sox.exe into %SOX_EXE%
    popd
    pause
    exit /b 1
  )
) else (
  echo sox already exists: %SOX_EXE%
)

if not exist "%FFMPEG_EXE%" (
  echo ffmpeg executable missing: %FFMPEG_EXE%
  popd
  pause
  exit /b 1
)
if not exist "%SOX_EXE%" (
  echo sox executable missing: %SOX_EXE%
  popd
  pause
  exit /b 1
)

"%FFMPEG_EXE%" -version >nul
if errorlevel 1 (
  echo ffmpeg sanity-check failed
  popd
  pause
  exit /b 1
)

"%SOX_EXE%" --version >nul
if errorlevel 1 (
  echo sox sanity-check failed
  popd
  pause
  exit /b 1
)

echo Installing Python dependencies with all extras...
poetry install --all-extras
if errorlevel 1 (
  echo poetry install --all-extras failed, falling back to core dependencies only.
  echo Optional engines tone/gigaam may remain unavailable.
  poetry install
  if errorlevel 1 (
    echo poetry install failed
    popd
    pause
    exit /b 1
  )
)

echo Install completed.
popd
if errorlevel 1 (
  echo Failed to restore previous directory
  pause
  exit /b 1
)
pause
endlocal
