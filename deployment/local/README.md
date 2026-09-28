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

`Feature` принимает только digest из Nexus. Перед изменением локального Docker
Desktop он сверяет хеш снимка, метки dev-образа, архитектуру, `VERSION`,
`SOURCE_REVISION=unverified` и импорт приложения. Затем применяет тот же
проверяемый backup, recreate и health checks, что и Candidate. `-CheckOnly`
проверяет параметры и Compose без pull и без переключения контейнеров; полная
проверка образа происходит при выполнении деплоя. В `deployment.json` dev-образ
помечается полями `feature_*` и не получает статус релизного кандидата.
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

Скрипт не создаёт и не переключает ветки, worktree, commit или tag. Для Fast-режима
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

5. Перед подготовкой релиза вручную запустить workflow `tests` на `main`;
   после прохождения проверок он публикует CI-кандидат. Затем развернуть его:

   ```powershell
   .\deployment\local\deploy.ps1 -Mode Candidate -CandidateRevision <full-40-char-sha>
   ```

6. Для локального Release скачать `candidate-receipt.json` из artifact
   успешного `publish_candidate` того же CI run и указать его явно:

   ```powershell
   .\deployment\local\deploy.ps1 -Mode Release -CandidateRevision <full-40-char-sha> -CandidateReceipt <candidate-receipt.json>
   ```

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

Commit и annotated tag создаются после успешного деплоя, только когда это требуется
для оформления релиза. Для этого не нужна новая ветка. Отдельная integration-ветка
или worktree остаётся обязательной только для обновления upstream, конфликтующей
интеграции либо когда текущий checkout нельзя безопасно считать одним кандидатом.

Удалённая поставка использует отдельный документированный процесс
`deployment/linux-pg/`; она запускается только по явному запросу на remote deploy.
