# Короткая проверка инфраструктуры CI

Ручной workflow **CI infrastructure dry-run** (`ci-dry-run.yml`) проверяет
механику доставки и обновления без полной продуктовой регрессии. Он не требует
self-hosted publisher, registry credentials, моделей или запущенного приложения.

Первая job проверяет YAML через закреплённый actionlint и узкие тесты CI,
строит небольшой образ Python/nginx и экспортирует его вместе с manifest.
Вторая job получает artifact по неизменяемому ID, сверяет независимый hash
manifest, repository/SHA/run/attempt, hashes исходников и архива, затем
импортирует образ и сверяет его Docker image ID и SOURCE_REVISION.

Образ содержит реальные startup/schema-файлы для контракта контроллера.
Приложение, БД и модели в нём не запускаются. HTTP-ответы и worker синтетические;
Docker, nginx и текущий `deployment/blue_green.py` настоящие. Используется
существующая проверка SSE drain, непрерывных HTTP-запросов, старых assets,
отклонения неготового кандидата, rollback и восстановления после прерывания.
Все контейнеры, сети и тома создаются под уникальным тестовым именем. Рабочий
Compose project и runtime не используются.

Проверки также отклоняют чужой SHA/run/attempt, изменённый manifest/архив,
чужие исходники и подменённую identity образа. Пакет имеет отдельный purpose
`ci-infrastructure-dry-run`, `publishable=false` и не принимается publisher.
Workflow имеет только read permissions, не содержит публикации, расписания
или переключения рабочего контура. Существующий `release.yml dry_run=true`
сохраняет полную релизную квалификацию и её обязательные gates.

Запуск в GitHub: Actions → **CI infrastructure dry-run** → **Run workflow**.
Для локального повторения нужны Docker Linux containers и Python; команды
выполняются из корня того же снимка исходников:

```bash
python -B -m tools.quality.check_release_workflows
python -B -m pytest -q --confcutdir=test/unit_test/tools/quality \
  test/unit_test/tools/quality/test_check_release_workflows.py \
  test/unit_test/tools/quality/test_release_workflow.py \
  test/unit_test/tools/quality/test_ci_dry_run.py
python -B -m tools.quality.ci_dry_run package --output "$EVIDENCE/packet" --work "$TASK/build"
# MANIFEST_SHA256 — output producer; передайте его отдельно от скачанного packet.
python -B -m tools.quality.ci_dry_run verify --packet "$EVIDENCE/packet" \
  --manifest-sha256 "$MANIFEST_SHA256" --output "$EVIDENCE/result"
```

`packet`, `build` и `result` должны быть новыми каталогами. На Windows Python —
`.venv/Scripts/python.exe`. Временную задачу резервируют через
`deployment/local/temp.ps1`; результаты сохраняют в `.local/archives`, после
проверки завершают задачу через `-Complete`. Итог — `result/ci-dry-run.json`.
Локальная передача файлов подтверждает тот же producer/consumer, но не является
доказательством работы сервиса GitHub Artifact: это подтверждает запуск workflow.
