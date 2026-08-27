@echo off
title asr-online-service-deploy
setlocal

docker compose up --build -d
if errorlevel 1 (
  echo docker compose failed
  pause
  exit /b 1
)

echo Deploy completed.
endlocal
