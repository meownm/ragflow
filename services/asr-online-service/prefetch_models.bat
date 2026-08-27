@echo off
title asr-online-service prefetch models
setlocal EnableExtensions EnableDelayedExpansion

pushd "%~dp0"
if errorlevel 1 (
  echo Failed to switch to service directory
  pause
  exit /b 1
)

poetry run python -m asr_service.tools.prefetch_models %*
if errorlevel 1 (
  echo prefetch_models failed
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
