# asr-online-service

Batch/job ASR сервис для обработки аудио через задания.

## Назначение
- Принимает ASR-задачи в очередь и обрабатывает их воркером.
- Отдаёт статусы и результаты задач через HTTP API.
- Даёт OpenAI-совместимые `GET /v1/models` и `POST /v1/audio/transcriptions` для подключения к RAGFlow.

## Запуск
### Windows
1. `install.bat` (скрипт удаляет `poetry.lock` перед `poetry install`)
2. `run_local.bat`

### Linux/macOS
1. `poetry install`
2. `poetry run python -m asr_service.dev_runner`

Swagger: `http://localhost:9011/docs`
UI: `http://localhost:9011/`

## Документация
- `docs/architecture.md`
- `docs/deployment.md`
- `docs/contracts/domain_objects.md`
- OpenAPI: `openapi/asr.yaml`


## Установка зависимостей

### Windows

- `install.bat`
- Скрипт завершится с `pause` и `exit /b 1` при ошибке.

### Linux/macOS

- `rm -f poetry.lock`
- `poetry install`

## Интеграционный smoke

- `poetry run python -c "import asr_service.main"`
- `poetry run pytest`
- Проверить startup Swagger URL: `http://localhost:9011/docs`

## Optional engines
- Whisper: устанавливается вместе с базовыми зависимостями (`torch`, `transformers`).
- Tone backend: `poetry install -E tone`.
- Docker image extends the official `tinkoffcreditsystems/t-one:0.1.0` runtime and exposes its bundled `t-tech/T-one` model as `t-one`.
- GigaAM backend: `poetry install -E gigaam`.

Если optional backend не установлен, модель помечается `available=false`, а создание job возвращает `409 Q-ASR-ENGINE-NOT-AVAILABLE`.


## Dependency notes
Core runtime dependencies include `fastapi`, `pydantic`, `pydantic-settings`, `numpy`, `torch`, `transformers`.
These are required for app startup and Whisper/Tone/GigaAM engine code paths.


## Windows smoke test

Запуск из `services/asr-online-service`:

- `poetry run pytest -q tests/smoke/test_windows_smoke_no_ffmpeg.py`
- `poetry run pytest -q tests/smoke/test_windows_smoke_job_ffmpeg_fail.py`


## Dead-code policy gate

Проверки (из `services/asr-online-service`):

- Windows CMD: `scripts\check_dead_code.bat`
- Windows PowerShell: `./scripts/check_dead_code.ps1`
- Linux/macOS: `poetry run ruff check src tests && poetry run vulture src tests vulture_allowlist.py --min-confidence 80`

Если `vulture` отсутствует в текущем окружении, выполните `poetry install` в среде с доступом к package index и повторите запуск гейта.

Как добавить исключение в `vulture_allowlist.py`:

1. Добавьте только реально подтверждённый false positive.
2. Оставьте короткий комментарий с причиной, почему символ используется косвенно.
3. Не добавляйте живой неиспользуемый код в allowlist вместо удаления/рефакторинга.
