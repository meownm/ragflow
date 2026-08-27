@echo off
setlocal

pushd "%~dp0\.."
if errorlevel 1 (
  echo Failed to switch to project root
  pause
  exit /b 1
)

poetry install
if errorlevel 1 (
  echo poetry install failed
  popd
  pause
  exit /b 1
)

poetry run ruff check src tests
if errorlevel 1 (
  echo ruff check failed
  popd
  pause
  exit /b 1
)

poetry run vulture src tests vulture_allowlist.py --min-confidence 80
if errorlevel 1 (
  echo vulture check failed
  popd
  pause
  exit /b 1
)

popd
if errorlevel 1 (
  echo Failed to restore previous directory
  pause
  exit /b 1
)

endlocal
