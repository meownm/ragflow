# Локальный деплой без служебных веток

## Быстрый образ из текущего checkout

Когда для проверки фичи нужен полный Linux-образ, а не bind-mount цикл, запустите
сборку из текущего checkout без commit и push:

```powershell
.venv\Scripts\python.exe deployment\local\build-feature.py
```

Новые файлы нужно перечислить по одному: `--include admin/server/new_module.py`.
Скрипт откажется собирать образ, если остались неучтённые untracked-файлы. Он
создаёт проверяемый снимок отслеживаемых файлов и явно выбранных новых файлов,
передаёт его по SSH на `192.168.1.175`, собирает C++/Go и Docker-образ, проверяет
метаданные и импорт приложения, затем публикует образ
`192.168.1.175:8443/docker-hosted/ragflow:dev-<source_id>` в Nexus. В выводе есть
полный digest для точного повторного pull. Образ имеет метку
`org.ragflow.validation=feature-build-only` и `SOURCE_REVISION=unverified`;
он не является релизным кандидатом. При ошибке сборка или публикация завершается
без успешного digest. Требуются SSH-ключ
`~/.ssh/ragflow_nuc8_ed25519`, подготовленные native/Go-кэши на Ubuntu и доступный
Nexus. Полная регрессия выполняется релизным workflow; вручную её можно запустить
через `workflow_dispatch`.

Для локальной проверки опубликованного dev-образа используйте оба значения из
вывода сборки — `DIGEST` и `SOURCE_ID`:

```powershell
.\deployment\local\deploy.ps1 -Mode Feature `
  -FeatureImageReference '<DIGEST>' `
  -FeatureSourceId '<SOURCE_ID>' `
  -CheckOnly
.\deployment\local\deploy.ps1 -Mode Feature `
  -FeatureImageReference '<DIGEST>' `
  -FeatureSourceId '<SOURCE_ID>'
```

`Feature` принимает digest из Nexus или полный локальный image ID вида
`sha256:<64 hex>` из `docker image inspect --format '{{.Id}}' <образ>`.
Локальный образ должен быть уже собран и загружен в Docker Desktop; для него
pull и публикация в registry не выполняются. Метки и `SOURCE_ID` проверяются
так же, а image ID должен точно совпасть с результатом inspect.
Перед изменением локального Docker
Desktop он сверяет хеш снимка, метки dev-образа, архитектуру, `VERSION`,
`SOURCE_REVISION=unverified` и импорт приложения. Затем применяет тот же
проверяемый backup, recreate и health checks, что и Candidate. `-CheckOnly`
проверяет параметры и Compose без pull и без переключения контейнеров; полная
проверка образа происходит при выполнении деплоя. В `deployment.json` dev-образ
помечается полями `feature_*` и не получает статус релизного кандидата.
Для `Feature`, `Candidate` и `Release` скрипт копирует шаблон конфигурации по
SHA-256 и хранит его вместе с логами в соседнем с checkout каталоге
`ragflow-local-runtime` на том же диске.
Контейнер монтирует эти постоянные пути, поэтому удаление временного checkout
после деплоя не лишает его конфигурации и каталога логов. Скрипт откажется
использовать ранее сохранённый шаблон, если его содержимое изменилось.
`Auto` не выбирает `Feature`: для каждого нового dev-образа передайте его digest
и `SOURCE_ID` явно.
На этой Windows-машине Docker уже авторизован в Nexus отдельным пользователем
с правами только на чтение `docker-hosted`; на другом компьютере потребуется
собственный `docker login 192.168.1.175:8443`.

Обычный локальный деплой автоматически выбирает быстрый bind-mount цикл или
неизменяемый CI-кандидат:

```powershell
.\deployment\local\deploy.ps1
```

Скрипт отвечает за применение и проверку runtime; после его успешного завершения
исполнитель деплоя обязательно создаёт коммит проверенных изменений поставки в
текущей ветке. Это правило одинаково для Fast, Feature, Candidate и Release:
режим сборки и квалификация образа не являются причиной оставлять изменения без
коммита. В коммит входят проверенные исходники, тесты и документация этой поставки;
посторонние изменения сохраняются отдельно. Если поставка уже закоммичена,
исполнитель сообщает существующий SHA, не создавая пустой коммит. При ошибке
деплоя или `-CheckOnly` коммит не создаётся. Push выполняется только по отдельному
указанию. Скрипт не создаёт и не переключает ветки, worktree или tag. Для Fast-режима
источником служит текущий checkout. Candidate/Release получает готовый образ из
`192.168.1.175:5443` по полному Git SHA и отключает development bind mounts.

По умолчанию цель — локальный Docker Desktop Compose project `ragflow-local`.
Изменённые пути определяют требуемый сервис и действие автоматически: frontend
собирается без пересоздания контейнера, смонтированный Python-код получает restart,
ASR пересобирается отдельно, а несмонтированный runtime-код требует CI-образ.

## Стандартный цикл

1. Просмотреть текущий diff и выполнить узкие тесты изменённого поведения.
2. Проверить конфигурацию без изменения runtime:

   ```powershell
   .\deployment\local\deploy.ps1 -CheckOnly
   ```

3. Выполнить локальный деплой:

   ```powershell
   .\deployment\local\deploy.ps1
   ```

4. Принудительно проверить или применить быстрый режим:

   ```powershell
   .\deployment\local\deploy.ps1 -Mode Fast
   ```

5. Для диагностики запустить `tests` с профилем `targeted`. Полный candidate
   квалифицируется через `release` с `dry_run=true`; он не публикуется автоматически.
   Проверенный image digest указан в schema 2 receipt.

6. Для локального Release скачать из **неизменяемого опубликованного** комплекта
   форка `meownm/ragflow` три файла в одну папку:
   `candidate-receipt.json`, `publication-receipt.json`, `release-manifest.json`.
   Скрипт проверит их digest через GitHub API доверенного repository, SHA и image;
   самодекларации receipt недостаточно. Нужны `gh` и Python.

   ```powershell
   gh release download <immutable-release-tag> --repo meownm/ragflow `
     --pattern candidate-receipt.json --pattern publication-receipt.json `
     --pattern release-manifest.json --dir C:\ragflow-release
   .\deployment\local\deploy.ps1 -Mode Release -CandidateRevision <full-40-char-sha> `
     -CandidateReceipt C:\ragflow-release\candidate-receipt.json
   ```

   Runtime загружается по digest. Nightly разрешается через annotated `nightly`
   Git tag с JSON message, который указывает конкретный immutable release tag.
   `latest` относится только к stable. Новые выпуски требуют schema 2; исторический
   schema 1 допускается только для явного восстановления с `-AllowHistoricalReceipt`.

7. Для возврата получить три assets предыдущего квалифицированного комплекта,
   проверить совместимость его runtime с данными и указать его receipt/SHA.
   Возврат image не восстанавливает PostgreSQL автоматически. Если новая версия
   несовместимо изменила схему, сначала остановить записи и восстановить проверенный
   pre-upgrade backup по процедуре PostgreSQL; сохранить SHA256 и RPO. Новые записи
   после backup потеряются при таком восстановлении. До выпуска это проверяется
   `deployment/runner/test-qualified-upgrade.py` на disposable копии.

До пересоздания контейнеров скрипт проверяет итоговый Compose config и делает
проверяемый PostgreSQL dump. После переключения он ждёт container health и
`/api/v1/system/healthz`, записывает restart count, image identity и итоговый health.
Для Candidate/Release он сверяет image ID запущенного `ragflow-cpu` с только что
проверенным образом и записывает `candidate_revision`, `candidate_image_id` и
доступные registry digests в `deployment.json`.
`Release` до переключения контейнера сверяет SHA, успешность обязательных CI jobs,
registry digest загруженного образа и затем image ID контейнера. `Candidate`
разрешает исследовательский запуск без receipt и помечает доказательства как
неполные. После выпуска отдельный `tools/quality/release_evidence.py verify`
собирает `release-evidence.json` из receipt, применимых живых отчётов качества и
`deployment.json`; отсутствие отчёта, backup или здоровья не считается успехом.
Evidence сохраняется в `output/local-deploy/<timestamp>/`.

`-SkipBackup` не отключает защиту безусловно: он разрешён только при наличии свежего
backup с повторно проверенным SHA-256. Для Release и database-sensitive изменений
backup всегда обязателен.

Observability выключен по умолчанию. Для явного запуска Compose с Grafana, Loki,
Tempo, Prometheus и OpenTelemetry используйте `-Observability`; все эти сервисы
дополнительно защищены профилем `observability`.

Однократно установите CA локального TLS registry и перезапустите Docker Desktop:

```powershell
.\deployment\local\install-candidate-registry-ca.ps1
docker desktop restart
```

Для stable выпуска commit и версионный tag предшествуют CI квалификации:
`release.yml` проверяет этот source SHA и публикует проверенный комплект. Локальный
Release устанавливает уже опубликованный комплект и записывает отдельный результат
deployment. Незакоммиченные изменения проверяются как Feature с явно неполной
квалификацией и точным SOURCE_ID. Candidate использует committed source SHA;
ограниченная диагностика не является опубликованным выпуском. Отдельная integration-ветка или worktree нужны для обновления
upstream, конфликтующей интеграции или отдельно обоснованной изоляции кандидата.

Удалённая поставка использует отдельный документированный процесс
`deployment/linux-pg/`; она запускается только по явному запросу на remote deploy.
