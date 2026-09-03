---
sidebar_position: 5
slug: /linux-sources-runbook-ru
---

# Установка RAGFlow с PostgreSQL и T-One из отдельного Git

Этот runbook описывает установку текущего кастомного RAGFlow с нуля на Linux.
Источник поставки — отдельный deployment Git, подготовленный из рабочего
репозитория. Tar bundle не используется.

Документ рассчитан на Ubuntu Server 24.04 LTS x86_64. Debian 12 поддерживается
установщиком, если доступная версия Docker Compose понимает теги `!override` и
`!reset`; для предсказуемого production-развёртывания базовой платформой
считается Ubuntu 24.04.

## 1. Что будет установлено

Один Compose-проект `ragflow-pg` содержит:

| Сервис | Назначение | Публикация на host |
| --- | --- | --- |
| `ragflow-cpu` | UI, API и текущий кастомный backend | `127.0.0.1:9380` |
| `postgres` | метаданные RAGFlow | не публикуется |
| `es01` | документы, полнотекстовый и векторный индекс | не публикуется |
| `redis` | Valkey, очередь и кэш | не публикуется |
| `minio` | внутреннее объектное хранилище | не публикуется |
| `plantuml-server` | рендеринг диаграмм | не публикуется |
| `t-one-asr` | OpenAI-compatible распознавание речи | не публикуется |

MySQL отсутствует в итоговом Compose. PostgreSQL хранит метаданные приложения,
а Elasticsearch — документы и векторы, поэтому `pgvector` этому профилю не
нужен.

Первый пользователь создаётся через штатный `init_superuser()` с
`is_superuser=true` и становится владельцем своего tenant. Для этого tenant
автоматически регистрируются:

```text
Provider: New API
Instance: t-one-local
Model: t-one
Type: speech2text
Base URL: http://t-one-asr:9011/v1
Tenant default: t-one@t-one-local@New API
```

Регистрация новых пользователей по паролю выключена:
`REGISTER_ENABLED=0`.

## 2. Как гарантируется установка актуального кода

Deployment Git содержит полное текущее дерево исходников, включая встроенный
`services/asr-online-service`. Frontend собирается на целевом сервере из этого
же clean Git checkout в контейнере `node:20-bookworm-slim`.

Runtime RAGFlow состоит из закреплённого upstream-образа и локального overlay:

1. `docker/docker-compose.yml`;
2. `docker/docker-compose.local.yml`;
3. `docker/docker-compose.linux.local.yml`;
4. `deployment/linux-pg/docker-compose.release.yml`.

Второй файл монтирует текущие backend-модули и собранный `web/dist`, третий
отключает Docker Desktop bootstrap на Linux, четвёртый убирает MySQL и
ограничивает публикацию RAGFlow loopback-адресом. Запуск только первого
Compose-файла означает потерю PostgreSQL-профиля, T-One и кастомного кода.

Установщик принимает только clean Git checkout, записывает фактические
`SOURCE_COMMIT` и `SOURCE_REF` в `/etc/ragflow-pg/deployed-source.env` и
отказывается устанавливать поверх уже существующего `/opt/ragflow-pg`.

## 3. Что ещё нужно решить до production

В исходном списке не хватает как минимум LLM и embedding-модели. T-One только
распознаёт речь: без chat-модели RAGFlow не отвечает, а без embedding-модели не
может полноценно индексировать и искать знания.

До production-приёмки зафиксировать:

1. Chat/Vision LLM, embedding и, при необходимости, reranker: локальный Ollama,
   Ollama Proxy или внешний API, точные имена моделей и ключи.
2. Нужен ли CodeExec sandbox. Текущий release-профиль его намеренно выключает;
   для Code-компонентов потребуются gVisor/runsc и отдельная приёмка изоляции.
3. DNS, reverse proxy, TLS и список административных IP.
4. Backup и проверяемое восстановление PostgreSQL, MinIO, конфигурации и
   секретов; политика Elasticsearch snapshots или полного переиндексирования.
5. Мониторинг диска, RAM/OOM, restart count, health, очередей и срока TLS.
6. Политика пользователей: локальные учётные записи или SSO/OIDC, SMTP,
   второй аварийный суперадмин и порядок отзыва доступа.
7. Производственная ёмкость, GPU/CPU-профиль для ASR и моделей, место под
   индексы и объекты, нагрузочный тест.
8. Регламент обновления и отката. `install.sh` предназначен только для первой
   установки и не является upgrade-скриптом.
9. Какие внешние источники и connectors действительно нужны, их ACL и
   отдельные read-only credentials.

Без пунктов 1, 3, 4 и 5 контур можно считать установленным, но не готовым к
production.

## 4. Требования

### 4.1. Машина подготовки deployment Git

Нужны:

- текущий рабочий checkout `S:\ragflow`;
- Git и PowerShell 7;
- достаточно места для второго полного дерева исходников;
- адрес новой закрытой Git-репозитории для hosted-варианта либо PuTTY/PSCP для
  однофайловой передачи.

Для однофайловой передачи без Git-хостинга достаточно установленного комплекта
PuTTY с `pscp.exe`; на этой машине он находится в `C:\Program Files\PuTTY`.

### 4.2. Linux-сервер

Базовый профиль:

- Ubuntu Server 24.04 LTS, `x86_64`, `systemd`;
- доступ `sudo`;
- минимум 8 CPU, 16 GB RAM и 100 GB SSD для smoke;
- ориентир 16 CPU, 32 GB RAM и 300+ GB SSD для рабочего контура;
- исходящий HTTPS к APT и registry образов;
- свободный loopback-порт `9380` или другой выбранный порт.

Итоговые требования определяются объёмом данных и моделями. До загрузки
production-данных подтвердить их нагрузочным тестом и ростом индекса.

Проверка сервера:

```bash
uname -m
cat /etc/os-release
lscpu
free -h
df -hT
timedatectl status
sudo ss -ltnp | grep ':9380 ' || true
```

Ожидаются `x86_64`, поддерживаемая ОС и свободный порт.

## 5. Подготовить самостоятельную папку

На Windows открыть PowerShell в `S:\ragflow` и проверить текущий источник:

```powershell
git status --short --branch
git log -1 --oneline --decorate
git tag --points-at HEAD
```

Для обычного release worktree должен быть чистым. Создать соседний каталог:

```powershell
./deployment/linux-pg/export_git_tree.ps1 `
  -TargetDirectory 'S:\ragflow-linux-pg'
```

Экспортируются tracked и новые non-ignored файлы текущего рабочего дерева.
Не копируются исходная `.git`, игнорируемые `.env`, кэши, данные контейнеров,
`node_modules`, `web/dist` и старые архивы. `web/dist` не нужен в Git: installer
соберёт его из исходников на Linux.

Если формируется самая первая deployment-репозитория и release-изменения ещё
не закоммичены в исходном checkout, допускается разовый экспорт:

```powershell
./deployment/linux-pg/export_git_tree.ps1 `
  -TargetDirectory 'S:\ragflow-linux-pg' `
  -AllowDirty
```

Перед production-публикацией итоговая папка всё равно должна пройти review и
быть закоммичена уже в новой репозитории.

Проверить состав:

```powershell
Set-Location 'S:\ragflow-linux-pg'
Get-Content .\DEPLOYMENT-SOURCE.env
Test-Path .\deployment\linux-pg\install.sh
Test-Path .\deployment\linux-pg\docker-compose.release.yml
Test-Path .\deployment\linux-pg\seed_admin_asr.py
Test-Path .\services\asr-online-service\Dockerfile
Get-ChildItem -Recurse -Force -Filter '.env' | Select-Object FullName
Get-ChildItem -Recurse -Force -Include '*.tar.gz','*.key','*.pem' |
  Select-Object FullName
```

Из `.env` ожидается только tracked `web/.env` с публичными build-настройками.
Из PEM ожидается upstream-пара `conf/private.pem`/`conf/public.pem`, используемая
RAGFlow для шифрования password payload; это не production TLS/SSH key. Иных
локальных секретов и старых bundles быть не должно. Файл
`deployment/linux-pg/env.template` секретов не содержит: реальные пароли
генерируются на сервере.

## 6. Создать отдельный Git

В подготовленной папке:

```powershell
git init -b main
git add --all
git status --short
git diff --cached --check -- deployment/linux-pg DEPLOYMENT-SOURCE.env
git commit -m "Initial RAGFlow PostgreSQL Linux deployment"
git tag -a v1.0.0 -m "Initial Linux deployment repository"
git remote add origin <DEPLOYMENT_GIT_URL>
git push -u origin main --follow-tags
```

Whitespace-проверка ограничена файлами deployment-контура: полный upstream
snapshot содержит исторические файлы с завершающими пробелами, а его целостность
задаётся clean tagged source commit и фиксируется в `DEPLOYMENT-SOURCE.env`.

Название версии deployment Git независимо от upstream RAGFlow. В описании
релиза хранить оба идентификатора: commit deployment Git и значения из
`DEPLOYMENT-SOURCE.env`.

Репозиторий должен быть закрытым. Не коммитить production `.env`, admin password,
API keys, TLS private keys, database dumps и model caches.

### 6.1. Собрать и передать однофайловый Git bundle через PuTTY

Для сервера без доступа к закрытому Git-хостингу собрать bundle из clean
tagged checkout:

```powershell
Set-Location 'S:\ragflow'
./deployment/linux-pg/build_git_bundle.ps1
```

Скрипт создаёт рядом с собой `.bundle` и `.bundle.sha256`, сохраняет executable
mode исходных shell-скриптов, проверяет bundle через отдельный clone и откажется
работать с dirty source. Передать оба файла программой `pscp.exe` из комплекта
PuTTY; имя пользователя и адрес сервера заменить на реальные:

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -P 22 `
  'S:\ragflow\deployment\linux-pg\ragflow-linux-pg-v1.5.0.bundle' `
  'admin@ragflow-server:/tmp/'
& 'C:\Program Files\PuTTY\pscp.exe' -P 22 `
  'S:\ragflow\deployment\linux-pg\ragflow-linux-pg-v1.5.0.bundle.sha256' `
  'admin@ragflow-server:/tmp/'
```

Пароль не указывать в командной строке: `pscp.exe` запросит его сам. Для PPK-ключа
добавить `-i 'C:\path\to\key.ppk'`.

## 7. Получить код на чистом Linux

Сначала установить только Git и CA certificates:

```bash
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates git
```

Клонировать в отдельный source-каталог, не в runtime-каталог:

```bash
sudo install -d -m 0755 /srv/ragflow-linux-pg
sudo chown "$USER:$USER" /srv/ragflow-linux-pg
git clone <DEPLOYMENT_GIT_URL> /srv/ragflow-linux-pg
cd /srv/ragflow-linux-pg
git checkout <APPROVED_TAG_OR_COMMIT>
git status --short
git rev-parse HEAD
git describe --tags --exact-match HEAD
```

Если файлы переданы через `pscp.exe`, заменить `git clone` на:

```bash
cd /tmp
sha256sum -c ragflow-linux-pg-v1.5.0.bundle.sha256
sudo install -d -m 0755 /srv/ragflow-linux-pg
sudo chown "$USER:$USER" /srv/ragflow-linux-pg
git clone /tmp/ragflow-linux-pg-v1.5.0.bundle /srv/ragflow-linux-pg
cd /srv/ragflow-linux-pg
git checkout v1.5.0
git status --short
git rev-parse HEAD
git describe --tags --exact-match HEAD
```

`git status --short` должен быть пустым. Не устанавливать из плавающего branch
без сохранённого commit SHA.

## 8. Запустить установку

Рекомендуется не передавать пароль в командной строке: если
`ADMIN_PASSWORD` отсутствует, installer создаст случайный 64-символьный hex
пароль и сохранит его в root-only файле.

```bash
cd /srv/ragflow-linux-pg

sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_NICKNAME='RAGFlow Administrator' \
  INSTALL_DIR=/opt/ragflow-pg \
  PROJECT_NAME=ragflow-pg \
  RAGFLOW_PORT=9380 \
  bash deployment/linux-pg/install.sh
```

Установщик:

1. Проверяет ОС, архитектуру, clean Git checkout и новый `INSTALL_DIR`.
2. Устанавливает Docker Engine/Compose из репозитория ОС, `curl`, `jq`,
   `openssl`, `rsync` и системные зависимости.
3. Включает Docker и сохраняет `vm.max_map_count=262144` для Elasticsearch.
4. Копирует source tree в `/opt/ragflow-pg`, не копируя `.git`, кэши и данные.
5. Собирает frontend командой `npm ci && npm run build` в одноразовом
   `node:20-bookworm-slim`; `node_modules` остаётся во временном Docker volume,
   затем volume удаляется.
6. Создаёт `docker/.env` с независимыми случайными паролями PostgreSQL,
   Elasticsearch, MinIO и Valkey, а также постоянным ключом шифрования
   персональных EVA-токенов.
7. Валидирует объединённый Compose, требует `postgres` и `t-one-asr` и аварийно
   завершает работу, если активен сервис `mysql`.
8. Собирает T-One, запускает все сервисы и ждёт RAGFlow health до 10 минут.
9. Создаёт superuser, регистрирует T-One и проверяет tenant default через
   фактические сервисы RAGFlow.

Получить сгенерированные credentials:

```bash
sudo stat -c '%U %G %a %n' /etc/ragflow-pg/admin.env
sudo cat /etc/ragflow-pg/admin.env
sudo cat /etc/ragflow-pg/deployed-source.env
```

Ожидаемый режим `admin.env` — `root root 600`. После помещения пароля в
утверждённый secret manager не создавать его копии с более широкими правами.

Если пароль должен быть задан заранее, считать его интерактивно:

```bash
read -rsp 'Initial admin password: ' ADMIN_PASSWORD
echo
sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_PASSWORD="$ADMIN_PASSWORD" \
  INSTALL_DIR=/opt/ragflow-pg \
  PROJECT_NAME=ragflow-pg \
  RAGFLOW_PORT=9380 \
  bash deployment/linux-pg/install.sh
unset ADMIN_PASSWORD
```

## 9. Единая команда Compose

Для ручных операций использовать те же четыре слоя:

```bash
cd /opt/ragflow-pg/docker

rf_compose() {
  sudo docker compose \
    --env-file .env \
    -p ragflow-pg \
    -f docker-compose.yml \
    -f docker-compose.local.yml \
    -f docker-compose.linux.local.yml \
    -f ../deployment/linux-pg/docker-compose.release.yml \
    "$@"
}
```

Не заменять эту команду на `docker compose up` без overlays.

## 10. Приёмка инфраструктуры

### 10.1. Source и frontend

```bash
sudo cat /etc/ragflow-pg/deployed-source.env
test -s /opt/ragflow-pg/web/dist/index.html
grep -n '^RAGFLOW_IMAGE=' /opt/ragflow-pg/docker/.env
sudo grep -Eq '^RAGFLOW_CREDENTIALS_KEY=[0-9a-f]{64}$' \
  /opt/ragflow-pg/docker/.env && echo 'credential key: ok'
```

`SOURCE_COMMIT` должен совпадать с утверждённым commit deployment Git.
`RAGFLOW_IMAGE` — это закреплённая upstream-основа; версию кастомного релиза
определяет Git commit и локальные монтирования, а не имя base image.

### 10.2. Сервисы и отсутствие MySQL

```bash
rf_compose config --quiet
rf_compose config --services | sort
rf_compose ps

if rf_compose config --services | grep -qx mysql; then
  echo 'ERROR: MySQL is active' >&2
  exit 1
else
  echo 'OK: MySQL is absent'
fi

sudo docker ps -a \
  --filter label=com.docker.compose.project=ragflow-pg \
  --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
```

Ожидаются ровно `es01`, `minio`, `plantuml-server`, `postgres`, `ragflow-cpu`,
`redis` и `t-one-asr`. Не должно быть контейнера MySQL.

### 10.3. Health RAGFlow и PostgreSQL

```bash
curl -fsS http://127.0.0.1:9380/api/v1/system/healthz | jq
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:9380/

ragflow_id=$(rf_compose ps -q ragflow-cpu)
postgres_id=$(rf_compose ps -q postgres)
test -n "$ragflow_id"
test -n "$postgres_id"

sudo docker exec "$ragflow_id" printenv DB_TYPE
sudo docker exec "$postgres_id" sh -lc \
  'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc \
  "select current_database(), count(*) from information_schema.tables where table_schema='"'"'public'"'"' group by current_database();"'
```

Ожидаются HTTP `200`, health `status=ok`, `DB_TYPE=postgres`, база `rag_flow` и
ненулевое число таблиц.

### 10.4. Закрытая регистрация

```bash
curl -fsS http://127.0.0.1:9380/api/v1/system/config | jq -e '
  (.data.registerEnabled // .registerEnabled) as $enabled |
  ($enabled == 0 or $enabled == false)
'
```

### 10.5. Superuser и tenant default ASR

```bash
sudo docker exec -i \
  -e CHECK_ADMIN_EMAIL=admin@example.org \
  "$ragflow_id" python - <<'PY'
import os
from common import settings

settings.init_settings()

from api.db.services import UserService
from api.db.services.user_service import TenantService

email = os.environ["CHECK_ADMIN_EMAIL"]
users = list(UserService.query(email=email))
assert len(users) == 1
assert users[0].is_superuser
exists, tenant = TenantService.get_by_id(users[0].id)
assert exists
assert tenant.asr_id == "t-one@t-one-local@New API"
print("superuser=ok")
print(f"tenant_asr={tenant.asr_id}")
PY
```

В `CHECK_ADMIN_EMAIL` указать тот же адрес, что использовался при установке.

### 10.6. T-One из сети RAGFlow

```bash
asr_id=$(rf_compose ps -q t-one-asr)
test -n "$asr_id"

sudo docker exec "$asr_id" python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:9011/health/ready', timeout=10).status)"

sudo docker exec "$ragflow_id" \
  curl -fsS http://t-one-asr:9011/v1/models | jq -e \
  '.data | map(.id) | index("t-one") != null'
```

Health и список моделей ещё не доказывают распознавание. Взять короткий WAV с
разборчивой русской речью:

```bash
sudo docker cp /path/to/spoken-russian.wav \
  "$ragflow_id:/tmp/asr-smoke.wav"

sudo docker exec "$ragflow_id" curl -fsS \
  -F model=t-one \
  -F file=@/tmp/asr-smoke.wav \
  http://t-one-asr:9011/v1/audio/transcriptions | \
  jq -e '.text | type == "string" and length > 0'

sudo docker exec "$ragflow_id" rm -f /tmp/asr-smoke.wav
```

Финальная сквозная проверка выполняется в UI: записать речь в голосовом поле,
получить непустой текст и убедиться по логам, что вызван `t-one`, а не fallback.
Для используемого сценария загрузки повторить тест коротким видео с речью.

## 11. Доступ к UI

По умолчанию RAGFlow слушает только loopback Linux-сервера. Для первичной
приёмки открыть SSH-туннель с рабочей станции:

```bash
ssh -L 19380:127.0.0.1:9380 admin@ragflow-server
```

Открыть `http://127.0.0.1:19380/` и войти созданным superuser.

Для production поставить отдельный reverse proxy с TLS перед
`127.0.0.1:9380`. Не публиковать PostgreSQL, Elasticsearch, Valkey, MinIO и
T-One в LAN/WAN. Docker published ports могут обходить UFW, поэтому проверять
политику также через `DOCKER-USER`, nftables или внешний firewall.

## 12. Настроить модели

После базовой установки выбрать реальный model provider. Минимальный набор:

| Назначение | Обязательно | Проверка |
| --- | --- | --- |
| Chat LLM | да | непустой ответ на контрольный prompt |
| Embedding | да | индексация и retrieval тестового документа |
| Vision | по сценариям | распознавание контрольного изображения |
| Reranker | рекомендуется | rerank возвращает оценки и улучшает golden set |
| ASR T-One | уже установлен | непустой transcript через RAGFlow |

Имена моделей, endpoint и API keys должны храниться в утверждённом secret
контуре. Health провайдера не заменяет реальный inference. Если модели локальные,
их сервис, GPU runtime и model cache — отдельный deployment-контур, которого в
этом Git пока нет.

## 13. Автозапуск и reboot test

Compose использует restart policy `unless-stopped`, отдельный systemd unit для
тех же контейнеров не нужен.

```bash
sudo systemctl enable docker
rf_compose ps
sudo reboot
```

После возврата сервера заново определить функцию `rf_compose` и проверить:

```bash
sudo systemctl is-active docker
rf_compose ps
curl -fsS http://127.0.0.1:9380/api/v1/system/healthz | jq -e \
  '.status == "ok"'
```

## 14. Backup

Резервировать независимо:

- PostgreSQL RAGFlow;
- MinIO volume с загруженными файлами;
- `/opt/ragflow-pg/docker/.env`;
- `/etc/ragflow-pg/admin.env` и `deployed-source.env`;
- commit/tag deployment Git;
- Elasticsearch snapshots либо гарантированно воспроизводимый индекс;
- reverse proxy, TLS и firewall-конфигурацию.

Пример логического backup PostgreSQL:

```bash
backup_dir="/var/backups/ragflow/$(date +%F_%H%M%S)"
sudo install -d -m 0700 "$backup_dir"
postgres_id=$(rf_compose ps -q postgres)

sudo docker exec "$postgres_id" sh -lc \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc -f /tmp/rag_flow.dump'
sudo docker cp "$postgres_id:/tmp/rag_flow.dump" \
  "$backup_dir/rag_flow.dump"
sudo docker exec "$postgres_id" rm -f /tmp/rag_flow.dump
sudo sha256sum "$backup_dir/rag_flow.dump" | \
  sudo tee "$backup_dir/SHA256SUMS"
```

Backup не принят, пока не выполнено тестовое восстановление в изолированный
Compose-проект.

## 15. Обновление и откат

Не запускать `install.sh` поверх существующего контура: он генерирует новые
инфраструктурные секреты и предназначен только для чистой установки.

Для обновления требуется отдельный `upgrade.sh`, который должен:

1. Проверить clean approved commit новой deployment-репозитории.
2. Сделать и проверить backup.
3. Сохранить существующий `/opt/ragflow-pg/docker/.env`.
4. Обновить код и пересобрать frontend/T-One без удаления volumes.
5. Выполнить schema migrations и health/inference regression.
6. Записать новый deployed commit.

До появления проверенного `upgrade.sh` каждое обновление проводить как
отдельную change-процедуру. Откат образа допустим только при совместимой схеме;
после необратимой миграции нужен restore полного backup.

## 16. Приёмочный лист

- [ ] Deployment Git private, clean, tagged и зафиксирован commit SHA.
- [ ] В Git нет `.env`, ключей, dump, model cache и старых bundles.
- [ ] `deployed-source.env` совпадает с утверждённым commit.
- [ ] Frontend собран из того же checkout.
- [ ] Постоянный `RAGFLOW_CREDENTIALS_KEY` сгенерирован и входит в защищённый backup.
- [ ] Compose config валиден и содержит семь ожидаемых сервисов.
- [ ] Сервис и контейнер MySQL отсутствуют.
- [ ] RAGFlow health возвращает `db/doc_engine/redis/storage=ok`.
- [ ] PostgreSQL содержит схему RAGFlow; `DB_TYPE=postgres`.
- [ ] UI доступен только через loopback/tunnel либо TLS reverse proxy.
- [ ] Первый пользователь активен и `is_superuser=true`.
- [ ] `REGISTER_ENABLED=0` подтверждён runtime API.
- [ ] T-One healthy, виден из RAGFlow и даёт непустой русский transcript.
- [ ] Tenant default равен `t-one@t-one-local@New API`.
- [ ] Выбраны и проверены chat LLM и embedding; reranker/vision — по сценарию.
- [ ] После reboot все сервисы восстанавливаются.
- [ ] Настроены мониторинг и алерты.
- [ ] Backup имеет checksum и тестово восстановлен.
- [ ] Принято решение по sandbox/gVisor.
- [ ] Есть второй аварийный администратор и порядок отзыва доступа.

## 17. Типовые неисправности

### Installer сообщает `dirty Git checkout`

```bash
cd /srv/ragflow-linux-pg
git status --short
git diff
git diff --cached
```

Не обходить gate в production. Либо убрать локальные изменения безопасным
способом, либо создать новый approved commit.

### Compose не понимает `!override` или `!reset`

```bash
sudo docker compose version
```

Установить актуальный Docker Compose plugin по утверждённой процедуре ОС и
повторить `rf_compose config --quiet`. Не удалять теги из overlays: они
гарантируют замену upstream depends/ports/profiles.

### Frontend build не проходит

Проверить доступ к `node:20-bookworm-slim`, свободное место, `web/package-lock.json`
и полный вывод `npm ci`/`npm run build`. Не подменять сборку старым `web/dist`.

### RAGFlow не стал healthy за 10 минут

```bash
rf_compose ps
rf_compose logs --tail=300 ragflow-cpu postgres es01 redis minio t-one-asr
df -hT
free -h
sysctl vm.max_map_count
```

### Появился MySQL

Убедиться, что используется `deployment/linux-pg/docker-compose.release.yml`,
а `COMPOSE_PROFILES` не переопределён вручную. Installer обязан остановиться
ещё на `config --services`.

### ASR healthy, но transcript пустой

Использовать файл с реальной разборчивой речью, проверить аудиодорожку и формат,
логи `t-one-asr`, прямой OpenAI-compatible вызов из `ragflow-cpu`, tenant
`asr_id` и затем UI. Silent fixture не подтверждает интеграцию.

### Порт 9380 занят

Выбрать другой host port при первой установке, например
`RAGFLOW_PORT=19380`. Внутренние адреса контейнеров при этом не меняются.
