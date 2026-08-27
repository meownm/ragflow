# Deployment

## Docker Desktop

1. Скопировать `.env.example` в `.env`.
2. При необходимости изменить `ASR_SERVICE_PORT`.
3. Выполнить `deploy_docker_desktop.bat`.
4. Проверить:
   - `http://localhost:<ASR_SERVICE_PORT>/health/live`
   - `http://localhost:<ASR_SERVICE_PORT>/docs`

`docker-compose.yml` использует порт из `.env` через `${ASR_SERVICE_PORT:-9011}`.

## Windows install script

`install.bat`:

- удаляет `poetry.lock` перед установкой зависимостей;
- проверяет наличие `tools/ffmpeg/bin/ffmpeg.exe` и `tools/sox/sox.exe`;
- при отсутствии скачивает ZIP и распаковывает в `tools/`;
- повторно не скачивает при уже существующих бинарниках;
- при ошибках делает `pause` и завершает с ошибкой.
