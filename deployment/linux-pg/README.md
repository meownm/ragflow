# RAGFlow Linux PostgreSQL release archive

Этот каталог собирает RAGFlow в один `tar.gz`, который передаётся через
`pscp.exe` и устанавливается на Rocky Linux 9.x, Ubuntu 24.04 или Debian 12
`x86_64` без Git на сервере. Целостность поставки подтверждается отдельным
SHA-256 файлом, а версия и происхождение записаны внутри
`DEPLOYMENT-SOURCE.env`.

Полный пошаговый документ:
[`docs/administrator/linux_sources_runbook_ru.md`](../../docs/administrator/linux_sources_runbook_ru.md).

## Компактная поставка через registry Цифры

Этот вариант не включает `docker-images.tar` и не собирает образы на сервере.
Шесть стандартных образов заранее публикуются в доступный серверу OCI registry,
а в архив входят только исходники, готовый `web/dist`, список образов и
контрольные суммы. T-One ASR в серверную поставку не входит.

Сначала авторизоваться в registry через `docker login`, подготовить и
опубликовать образы:

```powershell
./deployment/linux-pg/publish_registry_images.ps1 `
  -RegistryPrefix registry.example.org/cifra `
  -ReleaseVersion v1.6.0 `
  -Push
```

Затем собрать компактный архив с точными ссылками на опубликованные образы:

```powershell
./deployment/linux-pg/build_registry_archive.ps1 `
  -ReleaseVersion v1.6.0 `
  -ImagesEnvPath ./deployment/linux-pg/registry-images-v1.6.0.env
```

Повторную упаковку уже проверенного `web/dist` можно ускорить явным параметром
`-UseExistingFrontend`. Без него production frontend всегда собирается заново.

Если `ImagesEnvPath` не указан, сборщик положит в архив шаблон. Тогда при
установке нужно передать `REGISTRY_PREFIX`, а registry должен сохранять
стандартную структуру путей из шаблона.

Все адреса `*.example` ниже являются только примерами и намеренно не работают.
Нужно получить реальный DNS-адрес OCI registry у администратора инфраструктуры.

Загрузка через PuTTY с PPK-ключом:

```powershell
./deployment/linux-pg/upload_registry_release.ps1 `
  -PackagePath ./deployment/linux-pg/ragflow-linux-pg-v1.6.0-registry.tar.gz `
  -HostName ragflow.example.org `
  -UserName deploy `
  -PrivateKeyPath C:\Keys\ragflow.ppk `
  -HostKey 'ssh-ed25519 255 SHA256:REPLACE_WITH_PIN'
```

На сервере:

```bash
cd /tmp/ragflow-registry-release
sha256sum -c ragflow-linux-pg-v1.6.0-registry.tar.gz.sha256
tar -xzf ragflow-linux-pg-v1.6.0-registry.tar.gz

sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_NICKNAME='RAGFlow Administrator' \
  REGISTRY_PREFIX=registry.example.org/cifra \
  bash install_registry.sh
```

До установки комплект можно проверить без изменений системы. Проверка требует,
чтобы реальный registry разрешался через DNS, а его HTTPS `/v2/` отвечал `200`
или `401`:

```bash
REGISTRY_PREFIX=registry.example.org/cifra bash install_registry.sh --check
```

Для закрытого registry добавить `REGISTRY_USERNAME` и
`REGISTRY_PASSWORD_FILE`. Пароль передаётся Docker через stdin и не должен
входить в архив поставки. Если архив собран с `ImagesEnvPath`,
`REGISTRY_PREFIX` при установке не требуется.

## Офлайн-поставка образов для Rocky Linux 9.x x86_64

Рекомендуемый вариант для текущего Rocky-сервера без доступа к Docker Hub. На
Windows из `S:\ragflow` выполнить:

```powershell
./deployment/linux-pg/build_offline_archive.ps1 -ReleaseVersion v1.6.0
```

Сборщик не вызывает Git. Он создаёт один архив и checksum, содержащие:

- source snapshot и готовый `web/dist`;
- шесть Linux/amd64 Docker-образов: RAGFlow, PostgreSQL, Elasticsearch, Valkey,
  MinIO и PlantUML;
- внутренний `SHA256SUMS` для каждого payload-файла.

Результат:

- `ragflow-linux-pg-v1.6.0-offline.tar.gz`;
- `ragflow-linux-pg-v1.6.0-offline.tar.gz.sha256`.

Передать оба файла через `pscp.exe`, затем в PuTTY:

```bash
cd /tmp
sha256sum -c ragflow-linux-pg-v1.6.0-offline.tar.gz.sha256
sudo install -d -m 0755 /srv/ragflow-offline-v1.6.0
sudo tar -xzf ragflow-linux-pg-v1.6.0-offline.tar.gz \
  -C /srv/ragflow-offline-v1.6.0
cd /srv/ragflow-offline-v1.6.0

sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_NICKNAME='RAGFlow Administrator' \
  bash install_offline.sh
```

Offline installer проверяет все суммы, устанавливает Docker Engine/Compose и
системные утилиты из включённого корпоративного `cifra-docker`, загружает образы
через `docker load` и запускает Compose с `--pull never --no-build`. Доступ к
Docker Hub на сервере не используется. Требуются Rocky Linux 9.x x86_64,
доступные корпоративные DNF-репозитории, `sudo`, `tar` и `sha256sum`.

Chat LLM, embedding-модели и T-One ASR не входят в базовую поставку. RAGFlow
запускается без них, а нужные модели и ASR добавляются отдельно.

## Компактная поставка с загрузкой зависимостей на сервере

На Rocky Linux 9.x установщик использует включённый корпоративный DNF-репозиторий
`cifra-docker` и пакеты `docker-ce`, `docker-ce-cli`, `containerd.io`,
`docker-buildx-plugin`, `docker-compose-plugin`. Другой repo id можно передать
через `DOCKER_DNF_REPO`.

Установка разворачивает изолированный Compose-проект:

- полный source tree RAGFlow из проверенного архива;
- frontend, собранный из этого же дерева в контейнере Node.js 20;
- PostgreSQL 16 вместо MySQL;
- Elasticsearch, Valkey, MinIO и PlantUML;
- первого пользователя с `is_superuser=true`.

MySQL и sandbox в этом профиле выключены. Интерфейс публикуется на
`0.0.0.0:80` и доступен по IP или DNS-имени сервера. Сетевой доступ должен быть
ограничен корпоративным firewall или TLS reverse proxy.

## 1. Собрать файл поставки на Windows

Из `S:\ragflow` в PowerShell:

```powershell
./deployment/linux-pg/build_archive.ps1 -ReleaseVersion v1.6.0
```

Скрипт не вызывает Git и упаковывает текущее состояние файлов. В архив не
попадают `.git`, локальные `.env`, кэши, данные контейнеров, `node_modules`,
`web/dist`, локальные build/test-артефакты, скачанные `ragflow_deps`, `output` и
старые архивы. Перед сборкой нужно завершить проверку всех изменений, которые
должны войти в поставку.

Результат:

- `deployment/linux-pg/ragflow-linux-pg-v1.6.0.tar.gz`;
- `deployment/linux-pg/ragflow-linux-pg-v1.6.0.tar.gz.sha256`.

Скрипт сам проверяет чтение архива, выполняет контрольную распаковку и убеждается,
что обязательные deployment-файлы присутствуют, а `.git` отсутствует.

## 2. Передать оба файла через PuTTY

На Windows заменить пользователя и адрес сервера:

```powershell
& 'C:\Program Files\PuTTY\pscp.exe' -P 22 `
  'S:\ragflow\deployment\linux-pg\ragflow-linux-pg-v1.6.0.tar.gz' `
  'admin@ragflow-server:/tmp/'

& 'C:\Program Files\PuTTY\pscp.exe' -P 22 `
  'S:\ragflow\deployment\linux-pg\ragflow-linux-pg-v1.6.0.tar.gz.sha256' `
  'admin@ragflow-server:/tmp/'
```

Пароль не указывать в командной строке: `pscp.exe` запросит его. Для PPK-ключа
добавить `-i 'C:\path\to\key.ppk'`.

## 3. Проверить и распаковать через PuTTY

```bash
cd /tmp
sha256sum -c ragflow-linux-pg-v1.6.0.tar.gz.sha256

sudo install -d -m 0755 /srv/ragflow-linux-pg
sudo tar -xzf ragflow-linux-pg-v1.6.0.tar.gz -C /srv/ragflow-linux-pg
cd /srv/ragflow-linux-pg

cat DEPLOYMENT-SOURCE.env
test -s deployment/linux-pg/install.sh
test -s deployment/linux-pg/docker-compose.release.yml
```

Ожидаемый результат `sha256sum` — `OK`, а в manifest должны быть ожидаемые
`RELEASE_VERSION` и `PACKAGE_FORMAT=tar.gz`. Git для этих операций не нужен.

## 4. Запустить первую установку

```bash
cd /srv/ragflow-linux-pg

sudo env \
  ADMIN_EMAIL=admin@example.org \
  ADMIN_NICKNAME='RAGFlow Administrator' \
  INSTALL_DIR=/opt/ragflow-pg \
  PROJECT_NAME=ragflow-pg \
  RAGFLOW_PORT=80 \
  DOCKER_DNF_REPO=cifra-docker \
  bash deployment/linux-pg/install.sh
```

Если `ADMIN_PASSWORD` не задан, установщик создаст случайный пароль и сохранит
его в `/etc/ragflow-pg/admin.env` с правами `0600`. Реальные инфраструктурные
секреты генерируются на сервере и не входят в архив.

`install.sh` предназначен только для первой установки в пустой `INSTALL_DIR`.
Он собирает frontend, запускает Compose и проверяет health RAGFlow, PostgreSQL и
superuser. Версия установленного архива сохраняется в
`/etc/ragflow-pg/deployed-source.env`. Размер диска и число CPU установщик
намеренно не используют как блокирующие проверки; ёмкость контролируется
эксплуатационным мониторингом.
