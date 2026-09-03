# RAGFlow Linux PostgreSQL deployment repository

Этот каталог превращает текущий рабочий код в самостоятельную папку, которую
можно сохранить в отдельном Git и устанавливать на чистую Ubuntu 24.04 или
Debian 12 x86_64 непосредственно из clone.

Если на рабочей станции есть только комплект PuTTY, используйте однофайловый
Git bundle: его можно передать штатным `pscp.exe`, проверить по SHA-256 и
клонировать на сервере без отдельного Git-хостинга.

Полный пошаговый документ:
[`docs/administrator/linux_sources_runbook_ru.md`](../../docs/administrator/linux_sources_runbook_ru.md).

Установка разворачивает один изолированный Compose-проект:

- весь исходный код RAGFlow из deployment Git checkout;
- frontend, собираемый из этого же checkout в контейнере Node.js 20;
- закреплённый upstream-образ RAGFlow с актуальными backend-монтированиями;
- PostgreSQL 16 вместо MySQL;
- Elasticsearch как документный и векторный движок;
- Valkey, MinIO и PlantUML;
- T-One ASR из `services/asr-online-service` во внутренней сети;
- первого пользователя с `is_superuser=true` и T-One как ASR по умолчанию.

MySQL и sandbox в этом профиле выключены. `pgvector` не нужен: PostgreSQL
хранит метаданные RAGFlow, а документы и векторы хранит Elasticsearch.

## 1. Подготовить папку для отдельного Git

Из корня рабочего RAGFlow в PowerShell:

```powershell
./deployment/linux-pg/export_git_tree.ps1 `
  -TargetDirectory 'S:\ragflow-linux-pg'
```

Экспортируются все tracked и новые non-ignored файлы текущего дерева. `.git`,
кэши, локальные `.env`, данные контейнеров и старые архивы не копируются.
`DEPLOYMENT-SOURCE.env` фиксирует исходный commit/tag и состояние worktree.

По умолчанию dirty worktree запрещён. `-AllowDirty` допустим только при первом
формировании новой deployment-репозитории, когда в экспорт должны войти ещё не
закоммиченные изменения самого release-пути. До production-установки уже
экспортированная папка должна быть проверена и закоммичена.

## 2. Создать отдельный Git

```powershell
Set-Location 'S:\ragflow-linux-pg'
git init -b main
git add --all
git commit -m "Initial RAGFlow PostgreSQL Linux deployment"
git remote add origin <DEPLOYMENT_GIT_URL>
git push -u origin main
```

Перед push убедитесь, что в staged diff нет `.env`, паролей, ключей, локальных
данных и `*.tar.gz`.

Для поставки одним файлом из clean tagged checkout вместо Git-хостинга:

```powershell
./deployment/linux-pg/build_git_bundle.ps1
```

Скрипт создаёт рядом с собой `ragflow-linux-pg-<tag>.bundle` и файл
`ragflow-linux-pg-<tag>.bundle.sha256`, проверяет bundle через контрольный
clone и не перезаписывает существующий артефакт.

## 3. Установить на чистом Linux из clone

```bash
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates git
sudo install -d -m 0755 /srv/ragflow-linux-pg
sudo chown "$USER:$USER" /srv/ragflow-linux-pg
git clone <DEPLOYMENT_GIT_URL> /srv/ragflow-linux-pg
cd /srv/ragflow-linux-pg
git checkout <APPROVED_TAG_OR_COMMIT>
git status --short
git rev-parse HEAD

sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_NICKNAME='RAGFlow Administrator' \
  INSTALL_DIR=/opt/ragflow-pg \
  PROJECT_NAME=ragflow-pg \
  RAGFLOW_PORT=9380 \
  bash deployment/linux-pg/install.sh
```

Если передан Git bundle, вместо `git clone <DEPLOYMENT_GIT_URL> ...` выполнить:

```bash
cd /tmp
sha256sum -c ragflow-linux-pg-<tag>.bundle.sha256
git clone /tmp/ragflow-linux-pg-<tag>.bundle /srv/ragflow-linux-pg
cd /srv/ragflow-linux-pg
git checkout <tag>
git status --short
```

Если `ADMIN_PASSWORD` не задан, установщик создаёт случайный пароль и сохраняет
его в `/etc/ragflow-pg/admin.env` с правами `0600`. Сам installer принимает
только clean Git checkout, собирает frontend, копирует runtime в `/opt`,
генерирует инфраструктурные секреты, запрещает MySQL в итоговом Compose и
проверяет RAGFlow, PostgreSQL, T-One, superuser и tenant default ASR.

Интерфейс публикуется только на `http://127.0.0.1:9380`. Для удалённого доступа
используйте SSH-туннель или отдельный reverse proxy с TLS.
