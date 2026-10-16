# Подготовка обновлённых зависимостей

Эти команды собирают образы, не пересоздавая работающие сервисы. Проверки и
остаточные CVE нужно оценивать по конкретному digest: новый тег поставщика
может всё ещё содержать уязвимые Go-библиотеки или системные пакеты.

Из корня репозитория:

```powershell
docker build -t local/ragflow:security-review .

docker build --target docker-cli -t local/ragflow-docker-cli:security-20261017 agent/sandbox/executor_manager
docker build --pull --build-arg NEED_MIRROR=0 -t local/ragflow-sandbox-python:security-20261016 agent/sandbox/sandbox_base_image/python
docker build --pull --build-arg NEED_MIRROR=0 -t local/ragflow-sandbox-nodejs:security-20261016 agent/sandbox/sandbox_base_image/nodejs
docker build --pull --build-arg NEED_MIRROR=0 -t local/ragflow-sandbox-manager:security-20261017 agent/sandbox/executor_manager
docker build -f docker/security/Dockerfile.valkey -t local/ragflow-valkey:security-20261016 docker/security
docker build -f docker/security/Dockerfile.plantuml -t local/ragflow-plantuml:security-20261016 docker/security
docker build -f docker/security/Dockerfile.postgres -t local/ragflow-postgres:security-20261016 docker/security
```

Перед сборкой основного образа нужно собрать Go-бинарники через `build.sh`
в Linux с актуальными `go.mod`/`go.sum` и проверенными native-зависимостями.
`COPY bin` использует готовые бинарники и самостоятельно их не обновляет.
Каталог `bin` должен соответствовать проверяемому исходному состоянию.

Основная сборка использует опубликованный `infiniflow/ragflow_deps:latest`
для моделей, токенизаторов и uv. Tika/Chrome/ChromeDriver загружаются отдельной
стадией с проверкой SHA-256; `NEED_MIRROR=1` выбирает зеркала этих архивов.
Собственный образ моделей можно задать через `RAGFLOW_DEPS_IMAGE`.

Для ресурсов оставлен совместимый Tika 3.3.3. Tika 4 требует другой упаковки
и миграции серверного протокола; подстановка одного JAR вместо 3.x некорректна.
Chrome и ChromeDriver обновляются вместе до 155.0.8059.84.

В локальном Compose manager использует подготовленные Python/Node.js sandbox
образы. Valkey и PlantUML собираются с системными обновлениями поверх выбранных
образов поставщиков. Тома и пользовательские данные эти сборки не затрагивают.
В PostgreSQL тот же `gosu` 1.19 пересобирается с Go 1.26.9 и исправленным
`golang.org/x/sys`; исходный архив закреплён SHA-256. Поведение смены UID/GID
проверяется отдельно от SQL и восстановления базы.
Docker CLI 29.9.0 для manager/bootstrap пересобирается с обновлёнными Go-модулями;
его исходники также закреплены SHA-256. Обе цели находятся в Dockerfile manager:
обычная сборка manager сама собирает CLI, а цель `docker-cli` используется
bootstrap. Предварительная сборка локального CLI-образа для manager не нужна.
Healthcheck manager использует Python и не требует curl. Переменные
`SANDBOX_BASE_PYTHON_IMAGE` и `SANDBOX_BASE_NODEJS_IMAGE` позволяют выбрать
свои sandbox-образы вместо локальных значений по умолчанию.

Проверка healthcheck на готовом образе, включая HTTP 503 и закрытый порт:

```powershell
$env:RAGFLOW_SANDBOX_IMAGE_TEST='1'
$env:RAGFLOW_SANDBOX_TEST_IMAGE='local/ragflow-sandbox-manager:security-20261017'
.venv/Scripts/python.exe -m pytest test/integration/test_sandbox_image_healthcheck.py
```

Тест проверяет healthcheck Compose и Linux install/upgrade, создавая собственные
контейнеры без сети, Docker socket и пулов исполнения; отдельно проверяет
переопределение sandbox-образов в обоих профилях Compose.
Встроенные в npm 12.2.0 пакеты Undici, brace-expansion, ip-address и
postcss-selector-parser обновлены отдельно в пределах совместимых веток.

Полный запуск сервисов и проверка на реальных данных выполняются отдельно.
