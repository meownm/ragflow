---
sidebar_position: 5
slug: /linux-sources-runbook-ru
---

# Установка локального RAGFlow с нуля в Linux

Этот runbook предназначен для системного администратора, который разворачивает
локальный контур RAGFlow на чистом сервере Linux и подключает используемые в
этом контуре источники данных и модели.

Документ рассчитан на:

- Ubuntu Server 24.04 LTS x86_64 с `systemd`;
- Docker Engine и Docker Compose plugin;
- установку в `/opt`;
- доступ к интерфейсу RAGFlow только через loopback или отдельный reverse proxy;
- PostgreSQL, Elasticsearch, Valkey, MinIO и sandbox в Docker;
- внешние MinIO, EVA Wiki, OpenMetadata, Ollama Proxy, T-One ASR и reranker.

Для Debian 12 последовательность та же, но репозитории Docker, PowerShell,
NVIDIA Container Toolkit и gVisor надо подключать по инструкции для Debian.

> Важно: текущий локальный стенд использует не только опубликованный образ
> RAGFlow, но и примонтированный frontend и локальные изменения backend. Для
> точного воспроизведения администратор должен получить утвержденный release
> bundle с контрольными суммами. Клонирование произвольного `main` не считается
> восстановлением текущего контура.

## 1. Результат установки

После выполнения runbook должны работать:

| Компонент             | Адрес из RAGFlow                      | Назначение            |
| --------------------- | ------------------------------------- | --------------------- |
| RAGFlow UI/API        | `http://127.0.0.1:9380`               | интерфейс и API       |
| внутренний PostgreSQL | `postgres:5432`                       | метаданные RAGFlow    |
| Elasticsearch         | `es01:9200`                           | индекс документов     |
| Valkey                | `redis:6379`                          | очередь и кэш         |
| внутренний MinIO      | `minio:9000`                          | файлы RAGFlow         |
| sandbox manager       | `sandbox-executor-manager:9385`       | выполнение кода       |
| внешний MinIO         | `http://host.docker.internal:9000`    | bucket `kbase`        |
| EVA Wiki              | `http://host.docker.internal:8084`    | статьи и вложения EVA |
| OpenMetadata          | `http://host.docker.internal:8585`    | каталог PostgreSQL    |
| Ollama Proxy          | `http://host.docker.internal:11435`   | LLM и embeddings      |
| reranker              | `http://host.docker.internal:8013/v1` | переранжирование      |
| T-One ASR             | `http://host.docker.internal:9011/v1` | ASR аудио и видео     |

`host.docker.internal` в Linux создается уже имеющейся записью
`host-gateway` в основном Compose-файле. Внешние сервисы должны слушать адрес,
доступный с Docker bridge, а firewall должен запрещать доступ к ним из
неразрешенных сетей.

## 2. Что заранее получить у владельца системы

До установки запросить:

1. Release bundle RAGFlow с файлом `SHA256SUMS`.
2. Release bundle репозитория `infra`.
3. Release bundle `asr-online-service`.
4. Release bundle `bot_ready_kb` с `reranker_service`.
5. Образы Docker либо разрешение на их загрузку из registry.
6. Резервные копии баз и объектных хранилищ, если выполняется миграция.
7. Секреты:
   - пароли внутренних PostgreSQL, MinIO и Elasticsearch;
   - учетная запись только для чтения bucket `kbase`;
   - API key EVA Wiki;
   - OpenMetadata JWT и учетные записи девяти PostgreSQL-сервисов;
   - API key Ollama Proxy, если он включен;
   - секреты reverse proxy и TLS.
8. Утвержденный список моделей. Модели нельзя скачивать автоматически без
   отдельного согласования.
9. DNS-имена, сертификаты и список разрешенных административных IP.

Секреты не должны попадать в shell history, Git, тикет или текст runbook.

## 3. Требования к серверу

Минимум для smoke test: 8 CPU, 16 GB RAM и 100 GB SSD. Для полного рабочего
контура рекомендуется 16 CPU, 32 GB RAM и не менее 600 GB SSD, отдельный
filesystem для `/var/lib/docker` и NVIDIA GPU, если локальные модели используют
CUDA.

Проверить платформу и ресурсы:

```bash
uname -m
cat /etc/os-release
lscpu
free -h
df -hT
lsblk -f
timedatectl status
```

Ожидается `x86_64`. Настроить часовой пояс:

```bash
sudo timedatectl set-timezone Europe/Moscow
```

## 4. Подготовка операционной системы

```bash
sudo apt-get update
sudo apt-get full-upgrade -y
sudo apt-get install -y \
  ca-certificates curl gnupg git jq openssl rsync tar unzip wget
sudo reboot
```

После перезагрузки создать каталоги:

```bash
sudo install -d -m 0755 /opt/ragflow /opt/infra
sudo install -d -m 0755 /opt/asr-online-service /opt/bot_ready_kb
sudo install -d -m 0750 /etc/ragflow /var/backups/ragflow
id -u ragflow >/dev/null 2>&1 || \
  sudo useradd --system --home-dir /opt/ragflow \
    --shell /usr/sbin/nologin ragflow
sudo chown -R ragflow:ragflow /opt/ragflow
```

Не добавлять обычных пользователей в группу `docker`: доступ к Docker socket
эквивалентен root-доступу. Команды управления контейнерами ниже выполняются
через `sudo`.

Создать постоянную настройку Elasticsearch:

```bash
echo 'vm.max_map_count=262144' | \
  sudo tee /etc/sysctl.d/99-ragflow-elasticsearch.conf
sudo sysctl --system
sysctl vm.max_map_count
```

Ожидается значение не меньше `262144`.

## 5. Установка Docker Engine

```bash
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
  sudo apt-get remove -y "$pkg" || true
done

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

source /etc/os-release
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${UBUNTU_CODENAME:-$VERSION_CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF

sudo apt-get update
sudo apt-get install -y \
  docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
```

Проверить установку:

```bash
sudo docker version
sudo docker compose version
sudo docker run --rm hello-world
sudo systemctl is-enabled docker
sudo systemctl is-active docker
```

До загрузки данных убедиться, что `/var/lib/docker` расположен на нужном диске.

## 6. Установка gVisor для sandbox

В Linux `runsc` устанавливается на хосте. Docker Desktop bootstrap использовать
нельзя.

```bash
sudo curl -fsSL https://gvisor.dev/archive.key \
  -o /usr/share/keyrings/gvisor-archive-keyring.asc
echo "deb [arch=$(dpkg --print-architecture) \
signed-by=/usr/share/keyrings/gvisor-archive-keyring.asc] \
https://storage.googleapis.com/gvisor/releases release main" | \
  sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
sudo apt-get update
sudo apt-get install -y runsc
sudo docker info --format '{{json .Runtimes}}' | jq
```

Если ключа `runsc` нет:

```bash
sudo runsc install
sudo systemctl restart docker
```

Обязательный gate:

```bash
sudo docker run --rm --runtime=runsc hello-world
```

Не продолжать установку sandbox, пока этот тест не проходит.

## 7. Опционально: NVIDIA GPU

Раздел нужен только для GPU-профиля RAGFlow или локального CUDA reranker.

1. Установить одобренный драйвер NVIDIA для GPU и текущего ядра.
2. Перезагрузить сервер и проверить `nvidia-smi`.
3. Подключить официальный репозиторий и установить NVIDIA Container Toolkit:

```bash
curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
  sudo gpg --dearmor -o \
    /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -sL \
  https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
  sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
  sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list >/dev/null
sudo apt-get update
sudo apt-get install -y nvidia-container-toolkit
```

4. Настроить runtime:

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
nvidia-smi
sudo docker info | grep -i runtime
```

Не включать GPU-профиль до успешного контейнерного CUDA smoke test с
разрешенным образом.

## 8. PowerShell 7 для OpenMetadata

Текущая автоматизация OpenMetadata использует PowerShell:

```bash
source /etc/os-release
wget -q \
  "https://packages.microsoft.com/config/ubuntu/$VERSION_ID/packages-microsoft-prod.deb" \
  -O /tmp/packages-microsoft-prod.deb
sudo dpkg -i /tmp/packages-microsoft-prod.deb
rm /tmp/packages-microsoft-prod.deb
sudo apt-get update
sudo apt-get install -y powershell
pwsh --version
```

## 9. Развертывание release bundle

Целевая структура:

```text
/opt/
├── ragflow/
│   ├── docker/
│   ├── web/dist/
│   ├── api/
│   ├── rag/
│   └── SHA256SUMS
├── infra/
├── asr-online-service/
└── bot_ready_kb/
```

Проверить архив до распаковки:

```bash
cd /path/to/release-bundle
sha256sum --check SHA256SUMS
```

В `/opt/ragflow/docker` должны присутствовать:

```text
docker-compose.yml
docker-compose-base.yml
docker-compose.local.yml
docker-compose.linux.local.yml
.env
nginx/
```

```bash
sudo chown -R ragflow:ragflow /opt/ragflow
sudo find /opt/ragflow -type d -exec chmod 0755 {} +
sudo find /opt/ragflow -type f -exec chmod 0644 {} +
```

В bundle находится только шаблон `.env` с демонстрационными значениями. До
внесения реальных секретов ограничить его права, как указано в разделе 11.

## 10. Сборка frontend

Если bundle уже содержит проверенный `/opt/ragflow/web/dist`, шаг пропустить.
Иначе установить поддерживаемую Node.js не ниже `18.20.4` и выполнить:

```bash
cd /opt/ragflow/web
npm ci
npm run build
test -s dist/index.html
```

## 11. Базовый `.env` RAGFlow

Сгенерировать независимые секреты командой `openssl rand -base64 36`. Взять
`/opt/ragflow/docker/.env` из утвержденного bundle, сразу заменить все
демонстрационные секреты и задать как минимум:

```dotenv
SVR_HTTP_PORT=9380
TZ=Europe/Moscow

DOC_ENGINE=elasticsearch
STACK_VERSION=8.11.3
ES_PORT=1200
ELASTIC_PASSWORD=<STRONG_ELASTIC_PASSWORD>

DB_TYPE=postgres
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DBNAME=rag_flow
POSTGRES_USER=rag_flow
POSTGRES_PASSWORD=<STRONG_POSTGRES_PASSWORD>

REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD=<STRONG_REDIS_PASSWORD>

MINIO_HOST=minio
MINIO_PORT=9000
MINIO_USER=rag_flow
MINIO_PASSWORD=<STRONG_INTERNAL_MINIO_PASSWORD>

REGISTER_ENABLED=0
RAGFLOW_IMAGE=infiniflow/ragflow:v0.26.4
COMPOSE_PROFILES=elasticsearch,cpu,sandbox
```

Версия образа должна совпадать с release manifest; не использовать `latest`.

```bash
sudo chown root:ragflow /opt/ragflow/docker/.env
sudo chmod 0640 /opt/ragflow/docker/.env
```

## 12. Linux Compose overlay

Все команды RAGFlow выполняются с тремя Compose-файлами:

```bash
cd /opt/ragflow/docker
sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  config --quiet
```

Linux overlay снимает зависимость sandbox manager от Docker Desktop bootstrap и
отключает этот bootstrap через неактивный профиль.

```bash
sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  config --services
```

В списке должен быть `sandbox-executor-manager` и не должно быть
`sandbox-runtime-bootstrap`.

## 13. Сеть и firewall

Обязательная политика:

- `9380/tcp` публикуется только на `127.0.0.1`;
- внешний доступ к RAGFlow идет через reverse proxy с TLS;
- PostgreSQL, Elasticsearch, Valkey, внутренний MinIO и sandbox не публикуются
  в LAN;
- порты источников `9000`, `8084`, `8585`, `11435`, `8013`, `9011` доступны
  из Docker bridge RAGFlow, но закрыты от неразрешенной LAN/WAN;
- SSH разрешен только из административной сети.

Docker published ports могут обходить правила UFW. Политику для них реализовать
через `DOCKER-USER`, nftables или внешний firewall и проверить с другого хоста.
До создания правил определить фактические CIDR:

```bash
sudo docker network ls
sudo docker network inspect bridge | jq '.[0].IPAM.Config'
sudo ss -ltnp
```

Не подключать RAGFlow к общей сети всего проекта `infra`: в обоих стеках может
существовать DNS-имя `postgres`, что создает риск подключения не к той базе.
Для источников используется явный host gateway.

## 14. Развертывание внешнего `infra`

Внешний MinIO, EVA Wiki, OpenMetadata, Ollama Proxy и PostgreSQL-сервисы
находятся в отдельном bundle `/opt/infra`.

```bash
sudo install -m 0600 /dev/null /etc/ragflow/infra.env
sudoedit /etc/ragflow/infra.env
```

Не использовать демонстрационные пароли из Compose. Запускать точные
Compose-файлы, перечисленные в release manifest:

```bash
cd /opt/infra/containers
sudo docker compose --env-file /etc/ragflow/infra.env config --quiet
sudo docker compose --env-file /etc/ragflow/infra.env up -d --build
sudo docker compose --env-file /etc/ragflow/infra.env ps
```

Так Compose берет переменные только из защищенного файла. Альтернатива —
root-owned Compose `env_file`.

```bash
curl -fsS http://127.0.0.1:9000/minio/health/live
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8084
curl -fsS http://127.0.0.1:8585/api/v1/system/version
curl -fsS http://127.0.0.1:11435/api/tags | jq
```

EVA без сессии может вернуть redirect или `401`; timeout и `5xx` недопустимы.

## 15. T-One ASR

```bash
cd /opt/asr-online-service
cp -n .env.example .env
sudoedit .env
sudo docker compose config --quiet
sudo docker compose up -d --build
sudo docker compose ps
curl -fsS http://127.0.0.1:9011/health/ready | jq
```

Исходный Compose публикует ASR только на loopback. Контейнер RAGFlow не может
достичь loopback хоста через `host.docker.internal`. Создать root-owned
`/etc/ragflow/asr-linux.override.yml`:

```yaml
services:
  asr-online-service:
    ports: !override
      - "0.0.0.0:9011:9011"
```

```bash
sudo chmod 0644 /etc/ragflow/asr-linux.override.yml
cd /opt/asr-online-service
sudo docker compose \
  -f docker-compose.yml \
  -f /etc/ragflow/asr-linux.override.yml \
  up -d --build
```

Firewall обязан разрешать `9011/tcp` только с loopback и фактических Docker
bridge-сетей. Не копировать CIDR с другого сервера.

## 16. Reranker

Используется модель `mixedbread-ai/mxbai-rerank-large-v1`. Установка выполняется
по сопровождаемому документу:

```text
/opt/bot_ready_kb/reranker_service/docs/linux_deployment.md
```

```bash
sudo systemctl enable --now bot-ready-kb-reranker.service
sudo systemctl status bot-ready-kb-reranker.service --no-pager
curl -fsS http://127.0.0.1:8013/health | jq
curl -fsS http://127.0.0.1:8013/v1/metadata | jq
```

Сервис должен быть достижим из Docker bridge, но закрыт firewall от LAN/WAN.
Модель не скачивать автоматически: при отсутствии кеша получить разрешение или
офлайн-архив модели.

## 17. Проверка моделей Ollama Proxy

| Модель                 | Роль                   |
| ---------------------- | ---------------------- |
| `qwen3.8:latest`       | chat и image-to-text   |
| `bge-m3:latest`        | embedding по умолчанию |
| `qwen3-embedding:0.6b` | embedding OpenMetadata |

```bash
curl -fsS http://127.0.0.1:11435/api/tags | \
  jq -r '.models[]?.name' | sort
```

Если модели нет, зафиксировать блокер. Не выполнять `ollama pull` и не менять
каталог прокси без отдельного разрешения.

## 18. Первый запуск RAGFlow

```bash
cd /opt/ragflow/docker
sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  pull

sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  up -d
```

Если образы доставлены офлайн, `pull` пропустить и сверить локальные digest с
release manifest.

```bash
sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  ps

sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  logs --tail=200 ragflow-cpu sandbox-executor-manager
```

Проверить API:

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9380/
curl -fsS http://127.0.0.1:9380/api/v1/system/status | jq
```

Допустимый ответ корня — `200` или redirect на страницу входа. Контейнеры
должны стабилизироваться без restart loop.

## 19. Проверка источников из контейнера

```bash
RAGFLOW_CONTAINER=$(
  sudo docker ps --filter name=ragflow-cpu --format '{{.Names}}' | head -n1
)
test -n "$RAGFLOW_CONTAINER"
sudo docker exec "$RAGFLOW_CONTAINER" getent hosts host.docker.internal

for endpoint in \
  http://host.docker.internal:9000/minio/health/live \
  http://host.docker.internal:8084 \
  http://host.docker.internal:8585/api/v1/system/version \
  http://host.docker.internal:11435/api/tags \
  http://host.docker.internal:8013/health \
  http://host.docker.internal:9011/health/ready
do
  echo "=== $endpoint"
  sudo docker exec "$RAGFLOW_CONTAINER" \
    curl -sS -o /dev/null -w '%{http_code}\n' "$endpoint"
done
```

Для защищенных API допустимы `401` или `403`: это проверка сети, а не
авторизации. `000`, timeout и connection refused являются блокерами.

## 20. Первичная настройка RAGFlow

1. Открыть `http://127.0.0.1:9380` через SSH tunnel либо HTTPS reverse proxy.
2. Создать первого администратора и сразу задать уникальный длинный пароль.
3. Оставить `REGISTER_ENABLED=0`.
4. Создать рабочий tenant.
5. Не переносить тестовых пользователей, smoke-агентов и старые общие dataset
   bindings.

SSH tunnel с рабочей станции:

```bash
ssh -L 9380:127.0.0.1:9380 admin@ragflow-server
```

## 21. Регистрация моделей

В `Settings -> Model providers` добавить совместимый провайдер:

```text
http://host.docker.internal:11435
```

Зарегистрировать только существующие модели:

- `qwen3.8:latest` как chat и image-to-text;
- `bge-m3:latest` как embedding;
- `qwen3-embedding:0.6b` как embedding для OpenMetadata.

Reranker:

```text
Base URL: http://host.docker.internal:8013/v1
Model: mixedbread-ai/mxbai-rerank-large-v1
```

ASR:

```text
Base URL: http://host.docker.internal:9011/v1
Model: t-one
```

Целевые значения по умолчанию:

| Назначение | Модель                                |
| ---------- | ------------------------------------- |
| Chat       | `qwen3.8:latest`                      |
| Vision     | `qwen3.8:latest`                      |
| Embedding  | `bge-m3:latest`                       |
| ASR        | `t-one`                               |
| Reranker   | `mixedbread-ai/mxbai-rerank-large-v1` |

После сохранения выполнить реальный inference каждой модели. HTTP health
провайдера не заменяет этот тест.

## 22. Источник S3/MinIO `kbase`

Создать source connector:

| Поле       | Значение                                 |
| ---------- | ---------------------------------------- |
| Type       | S3-compatible / MinIO                    |
| Endpoint   | `http://host.docker.internal:9000`       |
| Bucket     | `kbase`                                  |
| Addressing | path style                               |
| Region     | значение, ожидаемое connector            |
| Access key | отдельная read-only учетная запись       |
| Secret key | из secret store                          |
| TLS verify | включить при HTTPS; для HTTP неприменимо |

Политика учетной записи должна разрешать только необходимые `ListBucket` и
`GetObject` для bucket `kbase`. Запись и удаление запрещены.

Создать отдельный dataset и выполнить preview, импорт малого префикса, сверку
имен и размеров, затем полную синхронизацию и сравнение с актуальным source
manifest. Не смешивать внешний `kbase` с внутренним MinIO RAGFlow.

## 23. Источник EVA Wiki

| Поле              | Значение                                                      |
| ----------------- | ------------------------------------------------------------- |
| Internal base URL | `http://host.docker.internal:8084`                            |
| Public URL        | `https://eva.meowmeow.crazedns.ru` или утвержденный новый URL |
| Project           | production-проект, выбранный явно                             |
| Attachments       | enabled                                                       |
| Archived pages    | disabled                                                      |
| API key           | из secret store                                               |

Перед сохранением выполнить preview. В ответе должны быть статьи выбранного
production-проекта, а ссылки должны строиться через публичный HTTPS URL.

Dataset EVA Wiki должен быть приватным: `permission = me`. Не воспроизводить
старые team bindings. Проверить ACL владельцем и вторым обычным пользователем.

## 24. OpenMetadata и девять PostgreSQL-источников

OpenMetadata должен каталогизировать:

1. `asterisk`;
2. `bot`;
3. `docs`;
4. `eva`;
5. `meets`;
6. `multilang_asr`;
7. `ollama_proxy`;
8. `pdf-ocr`;
9. `keycloak`.

Учетные записи PostgreSQL должны быть read-only и видеть только необходимые
схемы и metadata views.

### 24.1. Фоновая синхронизация

Подготовить `/etc/ragflow/openmetadata-postgres.env` с режимом `0600`, затем:

```bash
cd /opt/infra/containers/openmetadata
sudo bash -c '
  set -a
  source /etc/ragflow/openmetadata-postgres.env
  set +a
  exec pwsh -NoProfile -File ./run-postgres-metadata.ps1
'
```

Скрипт должен успешно обработать каждый из девяти сервисов. Проверить логи
ingestion-контейнера и итоговое число services/entities в UI.

### 24.2. Connector RAGFlow

Создать отдельный приватный dataset:

```text
permission = me
embedding = qwen3-embedding:0.6b
```

| Поле               | Значение                                        |
| ------------------ | ----------------------------------------------- |
| Internal API URL   | `http://host.docker.internal:8585`              |
| Browser/Public URL | утвержденный HTTPS URL либо локальный admin URL |
| Auth               | OpenMetadata JWT                                |
| Scope              | девять утвержденных PostgreSQL services         |
| Write mode         | disabled                                        |

Credentials фоновой синхронизации и `OPENMETADATA_*` переменные Copilot имеют
разные роли. Не подменять один набор другим.

До отдельной приемки оставить:

```dotenv
OPENMETADATA_WRITE_ENABLED=false
OPENMETADATA_USER_DOMAIN_MAP=<EXPLICIT_APPROVED_MAPPING>
```

Live ACL должен работать fail-closed: если соответствие пользователя не
найдено, запрос запрещается, а не выполняется сервисной учетной записью.

## 25. Sandbox

В RAGFlow настроить CodeExec provider:

```text
http://sandbox-executor-manager:9385
```

```bash
sudo docker logs --tail=200 sandbox-executor-manager
sudo docker ps --format 'table {{.Names}}\t{{.Status}}' | grep -E 'sandbox|ragflow'
```

В UI выполнить:

```python
print(6 * 7)
```

Критерии:

- результат `42`;
- pool заполнен `6/6`;
- executor использует `runsc`;
- у manager нет опубликованного host-порта;
- после перезапуска Docker pool восстанавливается.

Проверить runtime executor:

```bash
EXECUTOR_ID=$(
  sudo docker ps --filter name=sandbox --format '{{.ID}}' | head -n1
)
test -n "$EXECUTOR_ID"
sudo docker inspect "$EXECUTOR_ID" --format '{{.HostConfig.Runtime}}'
```

Ожидается `runsc`.

## 26. Сквозная проверка ASR

Health endpoint T-One недостаточен. Загрузить в RAGFlow короткий утвержденный
аудиофайл с разборчивой русской речью и проверить:

1. файл принят;
2. RAGFlow вызвал `t-one`;
3. создан непустой transcript;
4. текст попал в chunks и доступен в поиске;
5. в логах нет fallback на другой ASR.

Повторить тест для короткого видео с аудиодорожкой. Успех сырого HTTP-запроса к
`9011` не считается сквозной приемкой.

## 27. Автозапуск после перезагрузки

Основной механизм — Docker restart policy `unless-stopped`, уже заданный в
Compose. Не добавлять второй process manager для тех же контейнеров.

```bash
sudo systemctl enable docker
sudo docker inspect postgres es01 redis minio \
  --format '{{.Name}} {{.HostConfig.RestartPolicy.Name}}'
sudo docker ps --format 'table {{.Names}}\t{{.Status}}'
sudo reboot
```

После возврата сервера проверить Docker, оба Compose-стека, ASR, reranker,
RAGFlow UI, источники и sandbox. Внешние сервисы должны подняться раньше первой
плановой синхронизации RAGFlow.

## 28. Резервное копирование

Резервировать независимо:

- PostgreSQL RAGFlow;
- PostgreSQL/OpenMetadata и базы внешнего `infra`;
- внутренний MinIO RAGFlow;
- внешний MinIO `kbase`, если это master storage;
- Elasticsearch snapshots либо воспроизводимый индекс;
- `.env` и connector secrets через secret manager;
- release bundle и `SHA256SUMS`;
- reverse proxy и firewall.

Пример PostgreSQL backup:

```bash
BACKUP_DIR="/var/backups/ragflow/$(date +%F_%H%M%S)"
sudo install -d -m 0700 "$BACKUP_DIR"

POSTGRES_CONTAINER=$(
  sudo docker ps --filter name=postgres --format '{{.Names}}' | \
    grep ragflow | head -n1
)
test -n "$POSTGRES_CONTAINER"

sudo docker exec "$POSTGRES_CONTAINER" \
  pg_dump -U ragflow -Fc rag_flow -f /tmp/rag_flow.dump
sudo docker cp "$POSTGRES_CONTAINER:/tmp/rag_flow.dump" \
  "$BACKUP_DIR/rag_flow.dump"
sudo docker exec "$POSTGRES_CONTAINER" rm /tmp/rag_flow.dump
sudo sha256sum "$BACKUP_DIR/rag_flow.dump" | \
  sudo tee "$BACKUP_DIR/SHA256SUMS"
```

Имя контейнера и пользователя сверить с фактическим Compose config. Backup не
считается годным без тестового восстановления в изолированном окружении. Не
восстанавливать тест поверх production volumes.

## 29. Мониторинг

Настроить алерты на свободное место, RAM/OOM, Docker daemon, restart count,
health основных сервисов, ошибки ingestion, очередь Valkey, срок TLS,
давность backup и заполнение sandbox pool.

```bash
sudo systemctl status docker --no-pager
sudo journalctl -u docker --since '-30 min' --no-pager
sudo docker stats --no-stream
sudo docker ps -a --format \
  'table {{.Names}}\t{{.Status}}\t{{.RunningFor}}'
sudo docker system df
df -hT
free -h
```

## 30. Обновление и откат

Перед обновлением получить проверенный bundle, прочитать migration notes,
сделать backup, сохранить image digest и итоговый Compose config, проверить
свободное место и согласовать окно работ.

```bash
cd /opt/ragflow/docker
sudo docker compose \
  --env-file .env \
  -f docker-compose.yml \
  -f docker-compose.local.yml \
  -f docker-compose.linux.local.yml \
  config > /var/backups/ragflow/compose-before-upgrade.yml
```

Откат к старому image digest допустим только при совместимой схеме БД. После
необратимой миграции откат выполняется восстановлением полного backup.

## 31. Приемочный лист

- [ ] Docker Engine и Compose plugin включены в `systemd`.
- [ ] `vm.max_map_count >= 262144` сохраняется после reboot.
- [ ] `runsc` зарегистрирован и smoke test проходит.
- [ ] Linux Compose config валиден и не содержит Docker Desktop bootstrap.
- [ ] RAGFlow слушает только `127.0.0.1:9380` либо закрыт reverse proxy.
- [ ] Внутренние PostgreSQL, Elasticsearch, Valkey и MinIO healthy.
- [ ] Sandbox возвращает `42`, pool `6/6`, runtime `runsc`.
- [ ] Внешний MinIO `kbase` доступен read-only.
- [ ] EVA Wiki показывает production-проект и импортирует вложения.
- [ ] OpenMetadata содержит девять утвержденных PostgreSQL services.
- [ ] EVA Wiki и OpenMetadata datasets имеют `permission=me`.
- [ ] Нет унаследованных team bindings и smoke connectors.
- [ ] `OPENMETADATA_WRITE_ENABLED=false` до отдельной приемки записи.
- [ ] `OPENMETADATA_USER_DOMAIN_MAP` задан и работает fail-closed.
- [ ] В Ollama Proxy уже существуют три утвержденные модели.
- [ ] Chat, vision, embedding и reranker проходят inference tests.
- [ ] T-One проходит сквозной тест аудио и видео с transcript.
- [ ] Источники доступны из RAGFlow, но закрыты от посторонней сети.
- [ ] После reboot все сервисы и синхронизации восстанавливаются.
- [ ] Backup имеет checksum и тестово восстановлен.

## 32. Типовые неисправности

### `host.docker.internal` не разрешается

```bash
sudo docker inspect "$RAGFLOW_CONTAINER" \
  --format '{{json .HostConfig.ExtraHosts}}' | jq
```

Ожидается `host.docker.internal:host-gateway`. После исправления пересоздать
контейнер: обычного restart недостаточно.

### Источник доступен с хоста, но не из RAGFlow

Обычно сервис слушает только `127.0.0.1`. Проверить `ss -ltnp`, bind address и
firewall. Открыть его только для Docker bridge, а не для всей LAN.

### Elasticsearch не стартует

```bash
sysctl vm.max_map_count
sudo docker logs --tail=200 es01
free -h
df -h /var/lib/docker
```

### Sandbox executor не создается

```bash
sudo docker info --format '{{json .Runtimes}}' | jq
sudo docker run --rm --runtime=runsc hello-world
sudo docker logs --tail=300 sandbox-executor-manager
sudo ls -l /var/run/docker.sock
```

Не возвращать Docker Desktop bootstrap на Linux.

### ASR health зеленый, transcript пустой

Проверить формат файла, аудиодорожку, ffmpeg/sox внутри контейнера, логи T-One
и вызов модели `t-one`. Повторить тест через RAGFlow.

### OpenMetadata sync работает, Copilot не видит metadata

Проверить раздельно ingestion credentials, `OPENMETADATA_*`, user/domain map,
scope JWT, сеть до `8585`, приватность dataset и ACL пользователя.

## 33. Официальные материалы

- [Установка Docker Engine в Ubuntu](https://docs.docker.com/engine/install/ubuntu/)
- [Политики автозапуска Docker](https://docs.docker.com/engine/containers/start-containers-automatically/)
- [Установка gVisor](https://gvisor.dev/docs/user_guide/install/)
- [gVisor и Docker runtime](https://gvisor.dev/docs/user_guide/quick_start/docker/)
- [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- [PowerShell в Ubuntu](https://learn.microsoft.com/powershell/scripting/install/install-ubuntu)
