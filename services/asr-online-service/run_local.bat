@echo off
title asr-online-service
setlocal

pushd "%~dp0"
if errorlevel 1 (
  echo Failed to switch to service directory
  pause
  exit /b 1
)

poetry run python -m asr_service.dev_runner
if errorlevel 1 (
  echo run_local failed
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
