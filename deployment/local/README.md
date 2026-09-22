# Локальный деплой без служебных веток

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

5. Развернуть прошедший CI кандидат:

   ```powershell
   .\deployment\local\deploy.ps1 -Mode Candidate -CandidateRevision <full-40-char-sha>
   ```

До пересоздания контейнеров скрипт проверяет итоговый Compose config и делает
проверяемый PostgreSQL dump. После переключения он ждёт container health и
`/api/v1/system/healthz`, записывает restart count, image identity и итоговый health.
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
