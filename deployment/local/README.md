# Локальный деплой без служебных веток

Обычный локальный деплой выполняется прямо из текущего checkout одной командой:

```powershell
.\deployment\local\deploy.ps1
```

Скрипт не создаёт и не переключает ветки, worktree, commit или tag. Ветка записывается
только как диагностическая метка. Источником кандидата служит текущее содержимое
рабочего каталога; незакоммиченные изменения разрешены и фиксируются в evidence.

По умолчанию цель — локальный Docker Desktop Compose project `ragflow-local`, а
пересоздаётся только `ragflow-cpu`. Удалённый сервер этим entrypoint недоступен.

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

4. Если менялся frontend, собрать `web/dist` в том же процессе:

   ```powershell
   .\deployment\local\deploy.ps1 -BuildFrontend
   ```

5. Если менялся ASR-сервис, пересобрать его и затем приложение:

   ```powershell
   .\deployment\local\deploy.ps1 -Services t-one-asr,ragflow-cpu -Build
   ```

До пересоздания контейнеров скрипт проверяет итоговый Compose config и делает
проверяемый PostgreSQL dump. После переключения он ждёт container health и
`/api/v1/system/healthz`, записывает restart count, image identity и итоговый health.
Evidence сохраняется в `output/local-deploy/<timestamp>/`.

`-SkipBackup` допустим только для осознанной первой установки без работающего
PostgreSQL. Он не должен использоваться как способ обойти ошибку backup.

Commit и annotated tag создаются после успешного деплоя, только когда это требуется
для оформления релиза. Для этого не нужна новая ветка. Отдельная integration-ветка
или worktree остаётся обязательной только для обновления upstream, конфликтующей
интеграции либо когда текущий checkout нельзя безопасно считать одним кандидатом.

Удалённая поставка использует отдельный документированный процесс
`deployment/linux-pg/`; она запускается только по явному запросу на remote deploy.
