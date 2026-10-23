# Проверки безопасности по запросу

Эти инструменты запускаются по явному запросу пользователя. Они не подключены
к деплою, Git hooks, CI или расписанию. Это локальные инструменты разработчика;
они не входят в образ приложения и не меняют его зависимости.

| Инструмент | Назначение | Закреплённая версия Windows x64 |
| --- | --- | --- |
| Trivy | Уязвимости зависимостей/образов, ошибки конфигурации | 0.76.1 |
| Syft | Инвентаризация пакетов, SBOM | 1.55.0 |
| Gitleaks | Секреты в файлах и Git-истории | 8.30.1 |
| Grype | Уязвимости пакетов по SBOM Syft | 0.121.0 |

## Установка

Из корня репозитория, PowerShell 7, без прав администратора:

```powershell
$securityTask = .\deployment\local\temp.ps1 -Name security-tools-install
pwsh -NoProfile -File tools/security/install.ps1 -WorkDirectory $securityTask
if ($LASTEXITCODE -ne 0) { throw 'Security tool installation failed' }
trivy --version
syft version
gitleaks version
grype version
```

Установщик загружает закреплённые официальные архивы GitHub, проверяет SHA-256
до распаковки/исполнения, затем версию каждого CLI. Исполняемые файлы сохраняются
в `%USERPROFILE%\.local\bin` (другой каталог задаётся через `-Destination`).
Если каталог отсутствует в пользовательском `PATH`, добавьте его или используйте
полный путь к CLI. Версия, URL и хеши записываются в `security-tools-install.json`.
Сохраните квитанцию в `.local/archives`, затем завершите временную задачу:

```powershell
New-Item -ItemType Directory -Force .local/archives | Out-Null
$installReceipt = '.local/archives/security-tools-install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json'
Copy-Item (Join-Path $securityTask 'security-tools-install.json') $installReceipt
.\deployment\local\temp.ps1 -Name security-tools-install -Complete
```

Обновление версий — отдельное явное изменение: замените версию, имя архива и
SHA-256 вместе, затем повторите проверку. Другие платформы устанавливаются
по официальным инструкциям: [Trivy](https://github.com/aquasecurity/trivy),
[Syft](https://oss.anchore.com/docs/installation/syft/),
[Gitleaks](https://github.com/gitleaks/gitleaks#installing),
[Grype](https://oss.anchore.com/docs/installation/grype/).

## Перед проверкой

Зафиксируйте область запроса: каталог/снимок, Git-история либо конкретный образ.
Выберите нужные инструменты, а не все проверки автоматически. Для повторяемого
исследования исходников используйте снимок по `test/REGRESSION.md`; текущий
checkout с незакоммиченными изменениями обозначайте как изменяемый вход.
Не сканируйте `.local`, резервные копии, runtime-данные, `.venv`, `node_modules`
и скачанные модели вместо исходников. Для отдельной проверки установленных
зависимостей или данных такая область должна быть явно выбрана и указана в отчёте.

Рабочие файлы сохраняйте в выделенную задачу. Примеры ниже запускаются из корня
репозитория; `$scanRoot` должен указывать на выбранный каталог исходников:

```powershell
$securityTask = .\deployment\local\temp.ps1 -Name security-scan
$scanRoot = (Resolve-Path '<source-directory>').Path
git rev-parse HEAD | Set-Content (Join-Path $securityTask 'git-head.txt')
git status --short | Set-Content (Join-Path $securityTask 'git-status.txt')
trivy --version > (Join-Path $securityTask 'trivy-version.txt')
syft version > (Join-Path $securityTask 'syft-version.txt')
gitleaks version > (Join-Path $securityTask 'gitleaks-version.txt')
grype version > (Join-Path $securityTask 'grype-version.txt')
```

Сохраняйте `$LASTEXITCODE` сразу после **каждого** запуска, до следующей команды.
Примеры задают код `10` для находок Trivy/Gitleaks; Grype возвращает `2` при
достижении `--fail-on high`. Другие ненулевые коды — ошибка выполнения, а не
отсутствие находок. При необходимости дополнительно сохраняйте stderr в отдельный
лог. Не публикуйте сырые логи/отчёты без проверки чувствительных данных.

## Исходники и Git-история

```powershell
# Зависимости и конфигурация. Secret scanning выполняет отдельный Gitleaks.
trivy fs --scanners vuln,misconfig --severity HIGH,CRITICAL --exit-code 10 `
  --skip-dirs .git --skip-dirs .local --skip-dirs .venv --skip-dirs node_modules `
  --format json --output (Join-Path $securityTask 'trivy-source.json') $scanRoot
$trivyExit = $LASTEXITCODE

# Каталог исходников: включает незакоммиченные файлы выбранного снимка.
gitleaks dir $scanRoot --redact=100 --exit-code 10 --report-format json `
  --report-path (Join-Path $securityTask 'gitleaks-source.json')
$gitleaksExit = $LASTEXITCODE

# SBOM — перечень пакетов, а не заключение об отсутствии уязвимостей.
syft "dir:$scanRoot" --exclude '**/.git/**' --exclude '**/.local/**' `
  --exclude '**/.venv/**' --exclude '**/node_modules/**' `
  -o "syft-json=$(Join-Path $securityTask 'sbom.syft.json')"
$syftExit = $LASTEXITCODE
if ($syftExit -eq 0) {
  grype "sbom:$(Join-Path $securityTask 'sbom.syft.json')" --fail-on high `
    -o json --file (Join-Path $securityTask 'grype.json')
  $grypeExit = $LASTEXITCODE
}
```

Gitleaks `dir` не проверяет Git-историю. Для неё отдельно запустите:

```powershell
gitleaks git . --redact=100 --exit-code 10 --report-format json `
  --report-path (Join-Path $securityTask 'gitleaks-history.json')
$historyExit = $LASTEXITCODE
```

Укажите фактически доступные refs, shallow-состояние и диапазон истории в отчёте.
Если запрошен диапазон, передайте его через `--log-opts` (например, `--log-opts='main..HEAD'`).
Gitleaks `dir` не использует `.gitignore` как границу области: выбирайте чистый
снимок исходников, без секретов runtime и деревьев зависимостей. Не добавляйте
широкие allowlist/baseline/ignore, чтобы получить зелёный результат.

## Локальный Docker-образ

Сначала разрешите имя контейнера в полный неизменяемый image ID. Укажите нужный
контейнер вместо `<container>`; выполнение не пересоздаёт и не останавливает его.

```powershell
$imageId = docker inspect --format '{{.Image}}' '<container>'
if ($LASTEXITCODE -ne 0 -or $imageId -notmatch '^sha256:[0-9a-f]{64}$') {
  throw 'Cannot resolve container image identity'
}
$imageId | Set-Content (Join-Path $securityTask 'image-id.txt')
trivy image --image-src docker --scanners vuln --severity HIGH,CRITICAL `
  --exit-code 10 --format json --output (Join-Path $securityTask 'trivy-image.json') $imageId
$trivyExit = $LASTEXITCODE
syft "docker:$imageId" -o "syft-json=$(Join-Path $securityTask 'sbom.syft.json')"
$syftExit = $LASTEXITCODE
if ($syftExit -eq 0) {
  grype "sbom:$(Join-Path $securityTask 'sbom.syft.json')" --fail-on high `
    -o json --file (Join-Path $securityTask 'grype.json')
  $grypeExit = $LASTEXITCODE
}
```

Это анализ содержимого образа, а не writable-layer контейнера и не проверка
запущенного приложения. Для удалённого registry используйте явно выбранный
digest/платформу; не подменяйте недоступный локальный image ID изменяемым тегом.

## Результат и границы

Для каждого выбранного запуска сохраните команду, версию CLI, код завершения,
идентичность входа, настройки severity, данные о базах из отчётов/логов и статус:
`COMPLETE` (с находками или без) либо `INCOMPLETE` (инструмент/база/вход недоступен,
таймаут, частичное сканирование). Если Syft завершился ошибкой, Grype по его
отсутствующему или частичному SBOM не запускается и остаётся `INCOMPLETE`.
В примерах Trivy фильтрует отчёт до HIGH/CRITICAL. Grype сохраняет все уровни,
а `--fail-on high` задаёт только порог ненулевого кода завершения.

Trivy и Grype требуют загружаемые базы уязвимостей; Trivy также может загружать
Java DB и проверки конфигурации. Сбой загрузки, устаревшая база или нехватка памяти
не доказывают отсутствие уязвимостей. Не отключайте проверки актуальности базы
ради успешного статуса. Запуск Syft/Gitleaks сам по себе не проверяет CVE.
Для lock-файлов и SBOM укажите фактически обнаруженные экосистемы/пакеты;
нулевой список пакетов не означает полного покрытия зависимостей.

Разбирайте находки по достижимости, назначению и затронутому потребителю.
Совпадения Trivy/Grype группируйте по advisory и пакету, сохраняя все места
вхождения. Сканеры не заменяют ревью авторизации, границ модулей и бизнес-логики.
Запрос на проверку не разрешает исправления, коммит, push или деплой.

Сохраните уникальные отчёты и итог с кодами завершения в новом каталоге
`.local/archives`, затем выполните
`deployment/local/temp.ps1 -Name security-scan -Complete`.
