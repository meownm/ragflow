---
sidebar_position: 4
slug: /local-windows-sources-runbook-ru
---

# Runbook: установка локального RAGFlow с нуля и подключение рабочих источников

## 1. Назначение и границы

Этот документ предназначен для системного администратора, который разворачивает на Windows текущую локальную конфигурацию RAGFlow и подключает используемые источники данных:

- MinIO, bucket `kbase`;
- EVA Wiki;
- OpenMetadata с каталогом PostgreSQL-баз;
- локальные Ollama-модели, reranker и T-One ASR.

Целевая схема рассчитана на закрытую рабочую станцию или сервер без внешней публикации RAGFlow. Web-интерфейс RAGFlow слушает только `127.0.0.1:9380`; PostgreSQL, Elasticsearch, Redis, внутренний MinIO и sandbox наружу не публикуются.

Если нужен доступ с других машин, домен или HTTPS, это отдельная задача: сначала должны быть определены reverse proxy, TLS, SSO, межсетевые правила и резервное копирование. Нельзя просто заменить `127.0.0.1` на `0.0.0.0`.

## 2. Критические особенности текущей сборки

Это не чистая upstream-инсталляция RAGFlow.

- Runtime-образ: `infiniflow/ragflow:v0.26.4`.
- Релизный tag `v0.28.0` фиксирует локальные backend-модули и исходники frontend; собранный `web/dist` подменяет frontend образа через `docker/docker-compose.local.yml`.
- Коннекторы EVA Wiki и OpenMetadata, OpenMetadata Copilot, исправление PostgreSQL, обработка видео/ASR, startup gate и восстановление gVisor находятся в локальном checkout.

До передачи установки администратору владелец релиза должен предоставить один из вариантов:

1. предпочтительно — проверенный tag `v0.28.0`; `web/dist` собирается из этого checkout по разделу 11;
2. временно — полный неизменяемый архив рабочего дерева плюс файл контрольных сумм SHA-256.

Администратор должен остановить установку, если ему выдали только upstream tag `v0.26.4`: в нём нет локальных источников и исправлений.

## 3. Текущая целевая топология

| Компонент                | Версия/роль                         | Адрес с хоста            | Адрес из RAGFlow-контейнера            |
| ------------------------ | ----------------------------------- | ------------------------ | -------------------------------------- |
| RAGFlow                  | image `v0.26.4` + локальные mounts  | `http://127.0.0.1:9380`  | внутренний                             |
| PostgreSQL               | 16, метаданные RAGFlow              | не опубликован           | `postgres:5432`                        |
| Elasticsearch            | 8.11.3, поисковый индекс            | не опубликован           | `es01:9200`                            |
| Valkey/Redis             | 8, очередь и cache                  | не опубликован           | `redis:6379`                           |
| Внутренний MinIO         | хранилище самого RAGFlow            | не опубликован           | `minio:9000`                           |
| Внешний MinIO из `infra` | источник документов `kbase`         | `http://127.0.0.1:9000`  | `http://host.docker.internal:9000`     |
| EVA Wiki                 | рабочая wiki                        | `http://127.0.0.1:8084`  | `http://host.docker.internal:8084`     |
| OpenMetadata             | каталог данных                      | `http://127.0.0.1:8585`  | `http://host.docker.internal:8585`     |
| Ollama Proxy             | LLM/embedding/VLM                   | `http://127.0.0.1:11435` | `http://host.docker.internal:11435`    |
| Reranker                 | CUDA host service                   | `http://127.0.0.1:8013`  | `http://host.docker.internal:8013/v1`  |
| T-One ASR                | OpenAI-compatible ASR               | `http://127.0.0.1:9011`  | `http://host.docker.internal:9011/v1`  |
| Sandbox manager          | CodeExec, pool 3 Python + 3 Node.js | не опубликован           | `http://sandbox-executor-manager:9385` |

Не путать два MinIO. `ragflow-local-minio-1` — внутреннее хранилище RAGFlow. Источник `kbase` находится в контейнере `minio` Compose-проекта `infra`.

## 4. Требования к серверу

Минимум самого RAGFlow: x86-64, 4 CPU, 16 GB RAM и 50 GB свободного места. Для всей используемой локальной схемы с OpenMetadata, Elasticsearch, sandbox и локальными моделями закладывать не менее:

- 16 CPU threads;
- 32 GB RAM, доступных Docker Desktop;
- 600 GB свободного места в Docker data-root и отдельный запас под backup;
- NVIDIA GPU и подходящий драйвер — если reranker и Ollama должны работать на CUDA;
- Windows 11/Windows workstation с WSL 2 и Docker Desktop в Linux-container mode.

Проверка ресурсов:

```powershell
docker info --format 'CPUs={{.NCPU}} MemoryBytes={{.MemTotal}} OSType={{.OSType}} Architecture={{.Architecture}}'
docker system df
nvidia-smi
```

Нельзя выполнять `docker system prune`, удалять volumes или очищать Docker data-root в рамках обычного обслуживания.

## 5. Необходимое ПО

- Git for Windows;
- Docker Desktop с WSL 2 backend;
- Docker Engine 24+ и Compose 2.26.1+;
- Node.js 18.20.4+ и npm для сборки локального frontend;
- PowerShell 7 рекомендуется;
- Python/Poetry — для host CUDA reranker и связанных проверок.

Проверка:

```powershell
git --version
docker version
docker compose version
node --version
npm --version
```

При clone или распаковке сохранить `.gitattributes`: репозиторий принудительно задаёт LF для `*.sh` и `docker/entrypoint.sh`. Не пропускать эти правила архиватором или редактором, который массово переводит файлы в CRLF.

## 6. Каталоги установки

В текущей системе используются пути:

```powershell
$RagflowRoot = 'S:\ragflow'
$InfraRoot = 'C:\git\infra'
$AsrRoot = 'C:\git\речь\services\asr-online-service'
$RerankerRoot = 'C:\git\bot_ready_kb\reranker_service'
```

Пути можно изменить, потому что RAGFlow overlay использует относительные mounts. После изменения необходимо выполнять команды из правильных каталогов.

Проверить наличие поставляемых локальных файлов:

```powershell
$required = @(
  "$RagflowRoot\docker\docker-compose.local.yml",
  "$RagflowRoot\docker\ensure_runsc_runtime.sh",
  "$RagflowRoot\common\data_source\eva_wiki_connector.py",
  "$RagflowRoot\common\data_source\openmetadata_connector.py",
  "$RagflowRoot\api\apps\restful_apis\openmetadata_api.py",
  "$RagflowRoot\docs\openmetadata_copilot.md"
)
$required | ForEach-Object {
  if (-not (Test-Path -LiteralPath $_)) { throw "Missing deployment artifact: $_" }
}
```

Зафиксировать происхождение поставки в журнале установки:

```powershell
Set-Location $RagflowRoot
git rev-parse HEAD
git status --short --branch
```

Если используется архив, дополнительно сверить все SHA-256 с манифестом владельца релиза.

## 7. Секреты и сетевой контур

До запуска заменить все значения по умолчанию. Как минимум это пароли PostgreSQL, Elasticsearch, Redis, обоих MinIO, OpenMetadata, Keycloak, EVA service account, API-ключ reranker и административные учётные записи.

Правила:

- не записывать рабочие секреты в отслеживаемый `docker/.env`; локальные переопределения хранить только в игнорируемом `docker/.env.local`;
- выдавать `docker/.env.local` только администратору и `SYSTEM` через NTFS ACL;
- хранить backup PostgreSQL и volumes как секретные данные;
- не использовать root-учётную запись внешнего MinIO в RAGFlow;
- bucket `kbase` сделать private; RAGFlow выдать отдельную read-only учётную запись с `ListBucket` и `GetObject`;
- EVA Wiki и OpenMetadata подключать отдельными service accounts;
- `ALLOW_ANY_HOST=0` оставить включённым; разрешать только нужные private DB hosts через `SSRF_ALLOWED_PRIVATE_DB_HOSTS`;
- порт reranker `8013` не публиковать в Интернет: endpoint не имеет собственного пользовательского ACL.

Часть базового `infra` compose использует привязки вида `9000:9000`, `8084:80`, `11435:8080`, а host CUDA reranker слушает `0.0.0.0:8013`, чтобы Docker Desktop мог обращаться через `host.docker.internal`. На закрытой установке Windows Firewall должен разрешать эти порты только локальному хосту и Docker/WSL bridge и запрещать доступ из LAN/WAN. После запуска обязательно проверить фактические bindings через `docker ps` и `Get-NetTCPConnection`; `0.0.0.0` без firewall-правила является блокером приёмки.

Для первоначального локального запуска допускается `REGISTER_ENABLED=1`. Сразу после создания нужных пользователей установить `REGISTER_ENABLED=0` и пересоздать только RAGFlow-контейнер.

## 8. Подготовка Docker Desktop

Запустить Docker Desktop и установить `vm.max_map_count`:

```powershell
docker run --rm --privileged --pid=host alpine sysctl -w vm.max_map_count=262144
docker run --rm --privileged --pid=host alpine sysctl vm.max_map_count
```

Ожидаемое значение — не ниже `262144`. После перезапуска Docker Desktop проверку повторить; при необходимости оформить её как административную startup-задачу.

## 9. Запуск внешних зависимостей

### 9.1. Infra: MinIO, EVA Wiki, Ollama Proxy и Keycloak

Получить проверенный `C:\git\infra`, заполнить его секреты из secret manager и запустить штатный entrypoint:

```powershell
$env:KEYCLOAK_BOOTSTRAP_ADMIN_USERNAME = '<from-secret-manager>'
$env:KEYCLOAK_BOOTSTRAP_ADMIN_PASSWORD = '<from-secret-manager>'
Set-Location $InfraRoot
.\install_infra.bat
```

Текущий `infra` compose содержит лабораторные значения по умолчанию. Перед production-like установкой их необходимо переопределить. После старта через MinIO Console создать private bucket `kbase`, отдельного пользователя `ragflow-kbase-reader` и read-only policy только на этот bucket.

Проверить:

```powershell
Invoke-WebRequest http://127.0.0.1:9000/minio/health/live -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:11435/api/tags -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8084/ -UseBasicParsing -MaximumRedirection 0 -SkipHttpErrorCheck
```

Для EVA HTTP `302` на `/` допустим; реальная проверка авторизации выполняется кнопкой **Test connection** при создании коннектора.

### 9.2. OpenMetadata

OpenMetadata разворачивается отдельным Compose-проектом и присоединяется к сети `infra_default`:

```powershell
Set-Location "$InfraRoot\containers\openmetadata"
docker network inspect infra_default | Out-Null
docker compose up -d
docker compose ps --all
```

Обязательны healthy-состояния `openmetadata_mysql`, `openmetadata_elasticsearch`, `openmetadata_fuseki` и `openmetadata_server`. Контейнер миграции должен завершиться с кодом `0`.

```powershell
Invoke-RestMethod http://127.0.0.1:8585/api/v1/system/version
Invoke-WebRequest http://127.0.0.1:8586/healthcheck -UseBasicParsing
```

Перед импортом PostgreSQL явно передать секреты скриптам, не использовать встроенные demo-пароли:

```powershell
$env:OPENMETADATA_URL = 'http://127.0.0.1:8585'
$env:OPENMETADATA_EMAIL = '<openmetadata-service-account>'
$env:OPENMETADATA_PASSWORD = '<from-secret-manager>'
$env:INFRA_POSTGRES_USER = '<metadata-reader>'
$env:INFRA_POSTGRES_PASSWORD = '<from-secret-manager>'
$env:KEYCLOAK_POSTGRES_USER = '<metadata-reader>'
$env:KEYCLOAK_POSTGRES_PASSWORD = '<from-secret-manager>'
```

Скрипты импорта используют уже запущенный контейнер `openmetadata_ingestion`. Перед их вызовом убедиться, что он не завершился после старта:

```powershell
Set-Location "$InfraRoot\containers\openmetadata"
docker compose up -d ingestion
$ingestionRunning = docker inspect -f '{{.State.Running}}' openmetadata_ingestion
if ($ingestionRunning -ne 'true') {
  docker logs --tail 200 openmetadata_ingestion
  throw 'OpenMetadata ingestion container is not running'
}
```

Импортировать только metadata, затем контролируемую классификацию и разрешённый profiling:

```powershell
Set-Location "$InfraRoot\containers\openmetadata"
.\run-postgres-metadata.ps1
.\run-openmetadata-glossaries.ps1
.\run-openmetadata-governance.ps1
.\run-safe-profiling-and-samples.ps1
```

Ожидаемый scope текущей конфигурации — девять PostgreSQL services:

| OpenMetadata service            | Database        |
| ------------------------------- | --------------- |
| `docker_postgres_asterisk`      | `asterisk`      |
| `docker_postgres_bot`           | `bot`           |
| `docker_postgres_docs`          | `docs`          |
| `docker_postgres_eva`           | `eva`           |
| `docker_postgres_meets`         | `meets`         |
| `docker_postgres_multilang_asr` | `multilang_asr` |
| `docker_postgres_ollama_proxy`  | `ollama_proxy`  |
| `docker_postgres_pdf_ocr`       | `pdf-ocr`       |
| `docker_postgres_keycloak`      | `keycloak`      |

Не включать test/system databases. Broad profiling и sample-data запрещены; разрешён только reviewed allowlist из `run-safe-profiling-and-samples.ps1`.

### 9.3. T-One ASR

```powershell
Set-Location $AsrRoot
docker compose up -d --build
Invoke-RestMethod http://127.0.0.1:9011/v1/models
```

До приёмки подготовить короткий WAV/MP3 с реальной русской речью. Silent fixture не доказывает работу ASR.

### 9.4. CUDA reranker

На Windows-хосте:

```powershell
Set-Location $RerankerRoot
.\install_cuda.bat
.\install_autostart.bat -StartNow
Invoke-RestMethod http://127.0.0.1:8013/health
Invoke-RestMethod http://127.0.0.1:8013/v1/metadata
```

Ожидаемая модель: `mixedbread-ai/mxbai-rerank-large-v1`. Загрузка модели — отдельная контролируемая операция; runbook не разрешает автоматически скачивать отсутствующие модели.

### 9.5. Ollama Proxy

В proxy заранее должны существовать:

- `qwen3.8:latest` — chat и image-to-text;
- `bge-m3:latest` — основная embedding-модель документов;
- `qwen3-embedding:0.6b` — embedding OpenMetadata Dataset.

Проверить каталог до регистрации:

```powershell
$models = (Invoke-RestMethod http://127.0.0.1:11435/api/tags).models.name
$requiredModels = @('qwen3.8:latest', 'bge-m3:latest', 'qwen3-embedding:0.6b')
$missing = $requiredModels | Where-Object { $_ -notin $models }
if ($missing) { throw "Missing approved Ollama models: $($missing -join ', ')" }
```

Не выполнять `ollama pull` без отдельного согласования.

## 10. Настройка `docker/.env.local`

Не изменять безопасные значения по умолчанию в отслеживаемом `docker/.env`. Создать игнорируемый файл `docker/.env.local` и записать в него локальные секреты и необходимые переопределения. Минимально проверить следующие значения:

```dotenv
DOC_ENGINE=elasticsearch
DB_TYPE=postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DBNAME=rag_flow
POSTGRES_USER=rag_flow
POSTGRES_PASSWORD=<strong-secret>

RAGFLOW_IMAGE=infiniflow/ragflow:v0.26.4
DEVICE=cpu
COMPOSE_PROFILES=elasticsearch,cpu,sandbox
TZ=Europe/Moscow
REGISTER_ENABLED=1
ALLOW_ANY_HOST=0
SSRF_ALLOWED_PRIVATE_DB_HOSTS=host.docker.internal
SANDBOX_ENABLED=1
LLM_TIMEOUT_SECONDS=300
OLLAMA_KEEP_ALIVE=300

OPENMETADATA_URL=http://host.docker.internal:8585
OPENMETADATA_PUBLIC_URL=http://127.0.0.1:8585
OPENMETADATA_USERNAME=<service-account>
OPENMETADATA_PASSWORD=<strong-secret>
OPENMETADATA_WRITE_ENABLED=false
OPENMETADATA_CACHE_TTL_SECONDS=900
OPENMETADATA_STALE_AFTER_HOURS=168
OPENMETADATA_DATASET_ID=
OPENMETADATA_DATASET_TOP_N=20
OPENMETADATA_DATASET_SIMILARITY_THRESHOLD=0.05
OPENMETADATA_DATASET_VECTOR_WEIGHT=0.3
```

Также переопределить `ELASTIC_PASSWORD`, `REDIS_PASSWORD` и пароль внутреннего MinIO. Проверить согласованность `docker/.env`, `docker/.env.local` и `docker/service_conf.yaml.template`.

`OPENMETADATA_WRITE_ENABLED` оставить `false`, пока read-only поиск, ACL и audit не приняты. Разрешение изменений описаний/названий включать только отдельным change request.

Для доменного ограничения OpenMetadata рекомендуется явная fail-closed карта:

```dotenv
OPENMETADATA_USER_DOMAIN_MAP={"<ragflow-user-id>":["<allowed-domain>"]}
```

Не добавлять wildcard `*` без письменного решения владельца данных. Если карта задана, пользователь без собственной записи получает пустой каталог.

## 11. Сборка frontend

`docker-compose.local.yml` монтирует локальный `web/dist`. Без `web/dist/index.html` nginx отвечает `500` с циклом rewrite.

```powershell
Set-Location "$RagflowRoot\web"
$env:NODE_OPTIONS = '--max-old-space-size=8192'
$env:VITE_BUILD_SOURCEMAP = 'false'
npm ci
npm run build
if (-not (Test-Path -LiteralPath "$RagflowRoot\web\dist\index.html")) {
  throw 'web/dist/index.html was not built'
}
```

Не запускать повторную сборку во время работы без backup `web/dist`: Vite сначала очищает каталог, и при ENOMEM оставляет UI неработоспособным.

## 12. Проверка Compose и первый запуск RAGFlow

```powershell
Set-Location "$RagflowRoot\docker"
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml config --quiet

docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml up -d

docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml ps
```

Не использовать `down -v`: команда удалит PostgreSQL, индексы, очередь и внутренние файлы RAGFlow.

Проверка основной готовности:

```powershell
$health = Invoke-RestMethod http://127.0.0.1:9380/api/v1/system/healthz
$health | Format-List
if ($health.status -ne 'ok' -or $health.db -ne 'ok' -or
    $health.doc_engine -ne 'ok' -or $health.redis -ne 'ok' -or
    $health.storage -ne 'ok') {
  throw 'RAGFlow health gate failed'
}
```

Ожидаются `status`, `db`, `doc_engine`, `redis`, `storage` со значением `ok`.

Проверить доступность зависимостей именно из контейнера RAGFlow:

```powershell
$urls = @(
  'http://host.docker.internal:9000/minio/health/live',
  'http://host.docker.internal:8084/',
  'http://host.docker.internal:8585/api/v1/system/version',
  'http://host.docker.internal:11435/api/tags',
  'http://host.docker.internal:8013/health',
  'http://t-one-asr:9011/v1/models'
)
foreach ($url in $urls) {
  docker exec ragflow-local-ragflow-cpu-1 `
    curl -sS -o /dev/null -w "$url -> %{http_code}`n" --max-time 10 $url
}
```

Для EVA `/` ожидается `200` или `302`; для остальных health/version endpoints — `200`.

## 13. Первичная административная настройка

1. Открыть `http://127.0.0.1:9380/admin`.
2. Войти встроенной административной учётной записью и немедленно заменить пароль по умолчанию.
3. Создать персональные учётные записи; не использовать общий admin для повседневной работы.
4. Отключить открытую регистрацию:

```powershell
Set-Location "$RagflowRoot\docker"
# Сначала установить REGISTER_ENABLED=0 в docker/.env.
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml `
  up -d --force-recreate --no-deps ragflow-cpu
```

5. Проверить, что RAGFlow по-прежнему доступен только на `127.0.0.1:9380`:

```powershell
docker port ragflow-local-ragflow-cpu-1
```

## 14. Регистрация моделей

Открыть **Settings → Model providers**. Использовать только уже существующие модели.

| Provider/instance                               | Base URL                              | Модель                                | Тип           | Max tokens |
| ----------------------------------------------- | ------------------------------------- | ------------------------------------- | ------------- | ---------: |
| Ollama / `ollama-proxy`                         | `http://host.docker.internal:11435`   | `qwen3.8:latest`                      | `chat`        |     262144 |
| Ollama / `ollama-proxy`                         | тот же                                | `qwen3.8:latest`                      | `image2text`  |     262144 |
| Ollama / `ollama-proxy`                         | тот же                                | `bge-m3:latest`                       | `embedding`   |       8192 |
| Ollama / `ollama-proxy`                         | тот же                                | `qwen3-embedding:0.6b`                | `embedding`   |      32768 |
| New API / `t-one-local`                         | `http://host.docker.internal:9011/v1` | `t-one`                               | `speech2text` |       8192 |
| OpenAI-API-Compatible / `bot-ready-kb-reranker` | `http://host.docker.internal:8013/v1` | `mixedbread-ai/mxbai-rerank-large-v1` | `rerank`      |        512 |

Установить системные defaults:

- Chat: `qwen3.8:latest@ollama-proxy@Ollama`;
- Embedding: `bge-m3:latest@ollama-proxy@Ollama`;
- Image-to-text: `qwen3.8:latest@ollama-proxy@Ollama`;
- Speech-to-text: `t-one@t-one-local@New API`;
- Rerank: `mixedbread-ai/mxbai-rerank-large-v1@bot-ready-kb-reranker@OpenAI-API-Compatible`.

Embedding-модель Dataset нельзя менять после первой индексации. Проверить модель до загрузки данных.

## 15. Настройка источников данных

### 15.1. Общий порядок

Для каждого источника:

1. **Settings → Data source → Add data source**.
2. Заполнить endpoint и service-account credentials.
3. Нажать **Test connection**.
4. Создать Dataset с заранее выбранной embedding-моделью.
5. Связать источник с Dataset и включить auto-parse.
6. Запустить первичный sync вручную.
7. Дождаться разбора документов и выполнить Retrieval test.
8. Только после приёмки включить расписание и prune.

EVA Wiki и OpenMetadata должны использовать отдельные private Datasets (`permission=me`). Текущий код намеренно блокирует их связь с team Dataset, потому что RAGFlow не может перенести ACL источника на отдельные chunks.

### 15.2. MinIO `kbase`

Создать источник типа **S3**:

| Поле               | Значение                           |
| ------------------ | ---------------------------------- |
| Name               | `kbase`                            |
| Mode               | `S3 Compatible`                    |
| Bucket Name        | `kbase`                            |
| Prefix             | пусто, если нужен весь bucket      |
| Endpoint URL       | `http://host.docker.internal:9000` |
| Addressing Style   | `path`                             |
| Access Key ID      | read-only service account          |
| Secret Access Key  | из secret manager                  |
| Sync deleted files | enabled                            |

Создать Dataset `kbase`:

- permission: `team` допустим только если весь bucket предназначен всей команде;
- embedding: `bge-m3:latest`;
- parser: `naive`, если для конкретного класса файлов не утверждён другой parser;
- auto-parse: enabled;
- рекомендуемый poll/prune interval: 5 минут;
- timeout первичного полного sync: 1740 секунд или больше по фактическому объёму.

Приёмка: количество документов должно совпасть с актуальным manifest bucket за вычетом явно неподдерживаемых файлов. Значение `3072` было локальным snapshot на 2026-08-26 и не является постоянным нормативом.

### 15.3. EVA Wiki

Создать источник **EVA Wiki**:

| Поле                   | Значение                               |
| ---------------------- | -------------------------------------- |
| EVA API Base URL       | `http://host.docker.internal:8084`     |
| EVA Web Base URL       | `https://eva.meowmeow.crazedns.ru`     |
| EVA API Token          | read-only token одного service account |
| EVA Project            | выбрать production project в UI        |
| Include Attachments    | enabled                                |
| Verify SSL Certificate | enabled                                |
| Include Archived Pages | disabled                               |
| Batch Size             | `2` для первого запуска                |
| Attachment Size Limit  | `10485760`                             |
| Page Size Limit        | `26214400`                             |
| Retry Count            | `3`                                    |

Создать отдельный private Dataset `eva-wiki`, embedding `bge-m3:latest`, auto-parse enabled. Project ID нельзя менять после создания коннектора: для другого проекта создать новый connector.

Не переносить тестовый connector `EVA Wiki native smoke` и Dataset `eva-wiki-native-smoke` в чистую установку.

Приёмка должна включать:

- одну обычную страницу;
- страницу с attachment;
- исключение archived page;
- корректную browser-ссылку в citation;
- удаление тестовой страницы из Dataset после prune;
- отсутствие token в GET/логах/UI после сохранения.

### 15.4. OpenMetadata Catalog Dataset

Создать источник **OpenMetadata**:

| Поле                  | Значение                                                                   |
| --------------------- | -------------------------------------------------------------------------- |
| API Base URL          | `http://host.docker.internal:8585`                                         |
| Public URL            | `http://127.0.0.1:8585`                                                    |
| Username/Password     | отдельный read-only service account                                        |
| JWT Token             | альтернатива username/password; не заполнять оба способа без необходимости |
| Services/Domains/Tags | явные allowlists; пусто только для утверждённого полного каталога          |
| Include Columns       | enabled                                                                    |
| Batch Size            | `20`                                                                       |
| Maximum Tables        | `5000`                                                                     |
| Request Timeout       | `12` секунд                                                                |
| Retry Count           | `2`                                                                        |

Создать private Dataset `OpenMetadata Catalog`:

- permission: строго `me`;
- embedding: `qwen3-embedding:0.6b`;
- parser: `naive`;
- auto-parse: enabled;
- sync deleted files: enabled;
- poll/prune: сначала 60 минут, уменьшать только после измерения нагрузки.

После создания Dataset получить его ID из URL/UI или PostgreSQL и записать в `OPENMETADATA_DATASET_ID`:

```powershell
docker exec ragflow-local-postgres-1 psql -U rag_flow -d rag_flow -P pager=off `
  -c "SELECT id,name,permission,embd_id FROM knowledgebase WHERE name='OpenMetadata Catalog';"
```

Затем пересоздать только `ragflow-cpu`, как в разделе 13.

Важно: OpenMetadata настраивается дважды для разных путей выполнения:

- credentials внутри Data Source используются фоновым connector sync;
- `OPENMETADATA_*` в `docker/.env` используются `/openmetadata`, Copilot, live ACL и governance.

Оба пути должны иметь совместимый scope. Dataset является только поисковой проекцией; источником истины остаётся OpenMetadata.

### 15.5. Проверка приватности source Datasets

Запрос должен вернуть ноль строк:

```powershell
$sql = @'
SELECT c.name AS connector, c.source, k.name AS dataset, k.permission
FROM connector c
JOIN connector2kb c2 ON c2.connector_id = c.id
JOIN knowledgebase k ON k.id = c2.kb_id
WHERE c.source IN ('eva_wiki', 'openmetadata')
  AND k.permission <> 'me';
'@
docker exec ragflow-local-postgres-1 psql -U rag_flow -d rag_flow -P pager=off -c $sql
```

На старой локальной БД существуют legacy team-привязки. Их нельзя копировать в новую установку; перенос данных должен выполняться через новый private Dataset и повторный sync.

## 16. Проверка синхронизации

Статусы connector tasks: `0=UNSTART`, `1=RUNNING`, `2=CANCEL`, `3=DONE`, `4=FAIL`, `5=SCHEDULE`.

```powershell
$sql = @'
WITH latest AS (
  SELECT s.*,
         row_number() OVER (
           PARTITION BY s.connector_id, s.kb_id, s.task_type
           ORDER BY s.create_time DESC
         ) AS rn
  FROM sync_logs s
)
SELECT c.name, c.source, k.name AS dataset, l.task_type, l.status,
       l.time_started, l.new_docs_indexed, l.total_docs_indexed,
       l.docs_removed_from_index, l.error_count,
       left(coalesce(l.error_msg, ''), 200) AS error_summary
FROM connector c
JOIN connector2kb c2 ON c2.connector_id = c.id
JOIN knowledgebase k ON k.id = c2.kb_id
LEFT JOIN latest l ON l.connector_id = c.id
                  AND l.kb_id = k.id
                  AND l.rn = 1
ORDER BY c.name, l.task_type;
'@
docker exec ragflow-local-postgres-1 psql -U rag_flow -d rag_flow -P pager=off -c $sql
```

Для первичной приёмки нужны завершённые `sync` и `prune` без ошибок. `SCHEDULE` допустим только пока задача ожидает исполнения, `RUNNING` — только в пределах настроенного timeout.

Проверить Dataset summary:

```powershell
docker exec ragflow-local-postgres-1 psql -U rag_flow -d rag_flow -P pager=off `
  -c "SELECT name,permission,doc_num,chunk_num,token_num,embd_id,parser_id FROM knowledgebase ORDER BY name;"
```

Затем в каждом Dataset выполнить Retrieval test минимум по трём известным фактам и проверить, что citations ведут в правильный источник.

## 17. Приёмка ASR, моделей и sandbox

### ASR

1. Отправить spoken fixture напрямую в `POST /v1/audio/transcriptions` T-One.
2. Загрузить тот же audio/video в RAGFlow.
3. Убедиться, что RAGFlow получил непустой русский transcript и основной LLM использовал его.

Успешный raw HTTP T-One без непустого результата внутри RAGFlow не считается end-to-end приёмкой.

### Reranker

```powershell
$body = @{
  model = 'mixedbread-ai/mxbai-rerank-large-v1'
  query = 'проверка релевантности'
  documents = @('проверка релевантности документа', 'несвязанный текст')
  top_n = 2
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8013/v1/rerank `
  -ContentType application/json -Body $body
```

### Sandbox

```powershell
docker compose -p ragflow-local `
  --env-file "$RagflowRoot\docker\.env" `
  --env-file "$RagflowRoot\docker\.env.local" `
  -f "$RagflowRoot\docker\docker-compose.yml" `
  -f "$RagflowRoot\docker\docker-compose.local.yml" ps

docker logs ragflow-local-sandbox-executor-manager-1 2>&1 |
  Select-String 'Container pool initialization complete'
```

Ожидается `6/6 available`: три Python и три Node.js runtime containers. Дополнительно выполнить реальный CodeExec `def main(): return 42` и получить structured result `42`, exit code `0`. Один `/healthz` manager без готового pool не считается приёмкой.

## 18. Definition of Done

Установка завершена только если выполнено всё:

- использован проверенный локальный deployment artifact, а не чистый upstream tag;
- `docker compose config --quiet` проходит;
- все RAGFlow containers healthy, restart count не растёт;
- health RAGFlow возвращает пять `ok`;
- наружу опубликован только `127.0.0.1:9380` для RAGFlow;
- три утверждённые Ollama-модели уже существуют и зарегистрированы с правильными типами;
- reranker и T-One доступны из RAGFlow container;
- MinIO `kbase`, EVA Wiki и OpenMetadata прошли **Test connection**;
- первый sync и prune каждого production connector завершились без ошибок;
- EVA Wiki и OpenMetadata связаны только с private Datasets;
- Retrieval tests возвращают ожидаемые chunks и корректные citations;
- spoken audio/video дал непустой transcript end-to-end;
- sandbox имеет pool `6/6` и выполнил реальный CodeExec;
- открытая регистрация отключена, пароли по умолчанию заменены;
- сделан первый backup и проверено его чтение/контрольная сумма;
- журнал установки содержит commit/archive hash, версии образов и фактические endpoints.

## 19. Повседневные операции

Запуск:

```powershell
Set-Location "$RagflowRoot\docker"
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml up -d
```

Остановка без удаления данных:

```powershell
Set-Location "$RagflowRoot\docker"
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml stop
```

Логи:

```powershell
Set-Location "$RagflowRoot\docker"
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml logs --tail 200 ragflow-cpu
```

Recreate только RAGFlow после конфигурационного изменения:

```powershell
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml `
  up -d --force-recreate --no-deps ragflow-cpu
```

## 20. Резервное копирование

Минимальный backup включает:

- PostgreSQL `rag_flow`;
- volume `ragflow-local_minio_data`;
- зашифрованную копию runtime-конфигурации и secret inventory;
- точный deployment artifact/commit;
- отдельно — backup источников `infra` и OpenMetadata по их runbook.

Безопасный PostgreSQL dump без бинарного перенаправления PowerShell:

```powershell
$BackupRoot = 'D:\Backups\ragflow'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null

docker exec ragflow-local-postgres-1 `
  pg_dump -U rag_flow -d rag_flow -Fc -f /tmp/rag_flow.dump
docker cp "ragflow-local-postgres-1:/tmp/rag_flow.dump" `
  "$BackupRoot\rag_flow-$Stamp.dump"
docker exec ragflow-local-postgres-1 rm -f /tmp/rag_flow.dump
Get-FileHash "$BackupRoot\rag_flow-$Stamp.dump" -Algorithm SHA256
```

Для filesystem-consistent копии внутреннего MinIO использовать maintenance window:

```powershell
Set-Location "$RagflowRoot\docker"
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml stop ragflow-cpu minio

docker run --rm `
  -v ragflow-local_minio_data:/data:ro `
  -v "${BackupRoot}:/backup" `
  alpine tar -czf "/backup/ragflow-minio-$Stamp.tgz" -C /data .

docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml up -d minio ragflow-cpu
Get-FileHash "$BackupRoot\ragflow-minio-$Stamp.tgz" -Algorithm SHA256
```

Restore сначала репетировать в отдельном Compose project и на копиях volumes. Не восстанавливать поверх рабочей БД без утверждённого change/incident plan.

## 21. Обновление

1. Сделать и проверить backup.
2. Зафиксировать текущие image digests, commit и `docker compose config`.
3. Развернуть обновление в отдельном Compose project.
4. Проверить совместимость локальных bind-mounted файлов с новой версией image.
5. Выполнить полный source sync/retrieval/ASR/sandbox regression.
6. Только после приёмки переключать рабочий экземпляр.

Нельзя менять только `RAGFLOW_IMAGE`: локальный checkout и image сейчас разных версионных линий, поэтому такой upgrade может сломать импорты, schema и frontend/backend contracts.

## 22. Диагностика типовых сбоев

| Симптом                                                                           | Проверить                                                     | Действие                                                                                               |
| --------------------------------------------------------------------------------- | ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| Startup пытается подключиться к `mysql` при `DB_TYPE=postgres`                    | локальный `docker/entrypoint.sh` и его bind mount             | восстановить проверенную версию с условным запуском MySQL migration; не добавлять MySQL                |
| `/` или SPA route отвечает `500`, в nginx `rewrite or internal redirection cycle` | наличие `web/dist/index.html`                                 | восстановить matching artifacts или успешно пересобрать frontend                                       |
| После recreate временный `502`                                                    | `WAIT_FOR_BACKEND_BEFORE_NGINX=true`, backend health          | проверить startup gate и backend logs, не считать это отказом БД без evidence                          |
| S3 Test connection не проходит                                                    | endpoint и network context                                    | из Docker использовать `host.docker.internal:9000`, не `localhost` и не console port `9001`            |
| Попали во внутренний MinIO RAGFlow                                                | Compose labels/name/network                                   | источник должен указывать на контейнер `minio` проекта `infra`                                         |
| EVA connector не даёт сменить project                                             | это текущий safety contract                                   | создать новый connector для другого project                                                            |
| EVA/OpenMetadata не связывается с Dataset                                         | Dataset имеет `permission=team`                               | создать private Dataset; не обходить проверку через SQL                                                |
| OpenMetadata server healthy, но catalog search падает                             | `openmetadata_elasticsearch`                                  | отдельно проверить search container и OpenMetadata logs                                                |
| OpenMetadata Dataset ищет, но Copilot не видит данные                             | `OPENMETADATA_DATASET_ID`, domain map, два набора credentials | выровнять connector и Copilot scope, пересоздать `ragflow-cpu`                                         |
| Reranker возвращает `422`                                                         | request contract                                              | использовать `{model, query, documents, top_n}` и `results[].relevance_score`                          |
| T-One raw API работает, RAGFlow получает пустой transcript                        | model default, conversion, container-to-host path             | тестировать spoken fixture через полный RAGFlow path; не принимать только raw smoke                    |
| Sandbox manager healthy, pool `0/6`                                               | profile, endpoint, `runsc` runtime                            | проверить bootstrap, `http://sandbox-executor-manager:9385` и дождаться `6/6`                          |
| После restart Docker пропал `runsc`                                               | `sandbox-runtime-bootstrap`                                   | проверить `docker/ensure_runsc_runtime.sh`, не ограничиваться ручной одноразовой правкой daemon config |
| Connector долго в `RUNNING`                                                       | timeout, sync logs, task executor                             | проверить latest `sync_logs`, backend/task logs и доступность источника из контейнера                  |

## 23. Аварийная остановка и эскалация

При утечке credential:

1. остановить соответствующий connector или RAGFlow;
2. отозвать credential в источнике;
3. выпустить новый service-account credential;
4. обновить RAGFlow/`.env`;
5. проверить logs, DB dumps и backups на наличие секрета;
6. задокументировать период и затронутые источники.

При повреждении данных не выполнять `down -v`, ручное удаление volumes или массовое удаление Dataset. Снять логи, `docker inspect`, health, свежий PostgreSQL dump и эскалировать владельцу приложения.

## 24. Что приложить к акту установки

- дата, сервер и администратор;
- SHA/tag deployment artifact;
- вывод версий Docker/Compose/Node;
- список image digests;
- `docker compose ps`;
- RAGFlow health JSON;
- результаты container-to-host reachability;
- список production connectors без секретов;
- Dataset permission/embedding/doc/chunk counts;
- результаты Retrieval, ASR, reranker и CodeExec smoke;
- SHA-256 первого backup;
- перечень отклонений от runbook и ответственный за устранение.
