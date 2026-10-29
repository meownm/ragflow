# Жизненный цикл артефактов RAGFlow

Сборщик и QA получают отдельный run_id, назначение, срок хранения и lease.
Завершение запуска не означает завершение продуктовой задачи: failed, paused и
waiting защищены до подтверждённого закрытия. Охват — только RAGFlow.
Новый run начинает с `task_status=open`; sweeper требует отдельно подтверждённое
`completed`/`superseded`. Отсутствующий, неизвестный или повреждённый task status
старого run блокирует фоновое удаление. Успешный exit команды не закрывает эту
границу. Именованный Complete закрывает только свою workspace; владелец QA может
сразу выполнить teardown собственных явно disposable ресурсов под своей lease.

## Все классы участвуют в жизненном цикле

Защита от удаления не исключает объект из учёта. Каждая запись inventory и ресурс
run содержит `lifetime`: класс, назначение, подтверждённого владельца либо роль,
которая должна его установить, зависимости, условия хранения/вывода, дату
пересмотра и следующее действие. `review_queue` содержит точные locators и роли.
Сканирование не продлевает deadline: сохраняется first observation или явный
пересмотр владельцем; новое поколение по прежнему пути получает отдельную запись.

| Класс | Пересмотр | Условие вывода из эксплуатации |
| --- | --- | --- |
| Workspace | 2 дня | Запуск закрыт, нет lease/process/mount, уникальные результаты сохранены |
| Кэш, включая shared BuildKit | 7 дней | Нет потребителей/pins, известен рецепт пересборки; shared cache требует учёта всех проектов |
| Сборки и образы | 14 дней | Заменён кандидат/закрыта задача, нет runtime/build consumers, сохранены source и recovery |
| Release/rollback | 30 дней | Не текущий и не выбранный fallback, замена принята и окно восстановления закрыто |
| Резервная копия | 30 дней | Известен data scope, подтверждена пригодная замена того же scope, нет restore/rollback dependents, есть явный release |
| Архивы, evidence и recovery | 30 дней | Закрыта задача/диагностика, уникальные данные освобождены/заменены; для recovery release + 90 дней и отсутствие shared dependents |
| Рабочие данные, runtime, конфигурации | 30 дней | Сервис/поколение выведено, проверен экспорт/преемник, нет действующих потребителей |
| Модели и корпуса | 30 дней | Больше не нужны поддерживаемым inference/QA, сохранена точная версия либо проверено восстановление |
| Логи | 14 дней | Закрыта диагностика, сохранено необходимое итоговое доказательство |
| Инструменты и служебные receipts | 30 дней | Не нужны рецептам, активным jobs, audit/recovery/cache; есть принятая замена |
| Неизвестное | 7 дней | Установить владельца, назначение и зависимости до решения о списании |

Это сроки **пересмотра**, не blanket TTL удаления. Executors остаются у владельцев
классов; inventory не выдаёт разрешений. Для отсутствующего владельца очередь
появляется сразу. Неполный artifact имеет diagnosis; `pg_restore --list` и checksum
не выдаются за полную проверку восстановления.

Local deploy регистрирует новые и повторно используемые PostgreSQL backups до
изменения runtime: path generation/SHA, data scope, назначение и deployment receipt.
При сбое копирования сохранившийся partial file регистрируется как failed/incomplete.
Старые backups дополнительно обнаруживаются по существующим deployment receipts;
отсутствующие/внешние источники остаются отдельными reference records. Исторические
копии без доказанного владельца поступают в review, а не исчезают из учёта.

Регистрация других постоянных наборов использует тот же ledger:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle register --category backup --producer manual-backup --task-ref <task-or-report> --purpose <reason> --data-scope <project/database> --evidence <backup-file> --related-receipt <report>
```

Поддержаны backup/archive/model/data/dataset/config/log/tool/metadata и остальные
классы. Уникальные материалы этих классов в temp сохраняются перед Complete;
persistent class не может получить disposable labels. Реестр также читает известные
домашние RAGFlow Go/Feature caches/native toolchain и общую метрику BuildKit; ни
скан домашнего каталога целиком, ни очистка чужого shared cache не выполняются.

## Реестр и подключённые процессы

Один запуск использует один `Run` и существующий отчёт своего процесса. Создание
идёт через `docker_args`/`artifact-shell.sh`, файлы — через `note`; дочерний процесс
добавляет события тому же владельцу. Отдельные реестры для сборок, QA и очистки
не нужны. Повторные проверки и восстановление используют те же receipts.

Для QA штатный путь завершения: результат проверок → общий `retire_owned()` →
запись результата потребления входных образов. Pytest, live и blue-green runners
делают это сами. CI продолжает тот же run между шагами: `cleanup-engine.sh`
передаёт существующий `engine-*.json` в `finish --evidence` и вызывает один общий
teardown. Повторный `compose down` не нужен. Неверный отчёт не препятствует
очистке собственных ресурсов, но оставляет приёмку открытой для повторения.

Успешный QA освобождает только свою зависимость от входных образов. Его отчёт
проверяется для точных source/run/attempt и поколений до и после teardown,
затем сохраняется в существующий CAS. Удаление временного output не аннулирует
результат; потеря архивного доказательства вновь защищает зависимость.
Закрытие продуктовой задачи (`complete`) и освобождение восстановления (`release`)
остаются отдельными решениями владельца; QA не принимает их автоматически.
Live runner до создания стека записывает `teardown_request`: точные run/source/
engine/recovery epoch и поколение каталога evidence. Если владелец прерван или
Docker недоступен при finalizer, существующий weekly cleanup под освободившейся
lease повторяет только это поручение. Он сначала сохраняет временные evidence,
затем архивирует и удаляет остановленные собственные disposable контейнеры,
тома и сети. Images, filesystem paths, runtime и продуктовый task status этим
поручением не закрываются. Живые контейнеры не останавливаются; изменённые
поколения и отсутствующие evidence блокируют повтор. Незавершённый повтор
попадает в `pending_teardowns` и даёт ненулевой exit вместо зелёного отчёта с
оставшимися ресурсами. Активная lease откладывает работу до следующего запуска.
Обычный `complete`, включая смену task status и отзыв прежнего `release`, сохраняет
действительное подтверждение QA. Recovery epoch меняется при реальном `restore`;
новое потребление, изменение source/input/generation или потеря witness вновь
защищают входные образы.

Production blue-green наблюдает сервисы и их зависимости без присвоения права
удаления. Старые и новые файлы конфигурации сохраняются под существующим
deployment lock. Восстановление возвращает проверенные input snapshots и
не перезаписывает действующую конфигурацию.

Frontend producer удаляет только пустые package placeholders `.keep` внутри
своего установленного `node_modules/.pnpm` сразу после завершения install,
включая неуспешный install. Непустые pins, `.keep` задачи и внешние links
сохраняются. Общий workspace cleaner продолжает защищать вложенные pins и backups.

`tools.quality.artifact_registry` атомарно заменяет
`.local/maintenance/artifacts/inventory.json`. Раздел lifecycle содержит
запуски, ресурсы, receipts, cleanup и recovery. Ownership хранится в
`.local/maintenance/artifacts/runs` и дочерних events; индекс можно пересоздать.
RAGFLOW_ARTIFACT_STATE задаёт другой постоянный store. В GitHub Actions default —
`~/.local/state/ragflow-artifacts`, вне очищаемого checkout.

| Процесс | Учёт |
| --- | --- |
| temp.ps1 / workspace.py | Поколение workspace, lease/process job, именованный Complete |
| check_cache.py | Детерминированные receipts/cache и Docker-команды check |
| frontend_artifact.py | Frontend archive, receipt, логи, pnpm cache |
| build-feature.py / feature_image.py | Точный dirty snapshot, image ID, build/import receipts, входные образы |
| go_builder.py / native_artifacts.py / benchmark | Builder, native entry/manifest, recipe/toolchain, результаты |
| build-qualified-image.sh | Образ, disposable проверки/извлечение SPA, CI receipts |
| Blue-green / qualified-upgrade / live runner | Контейнеры, тома, сети, входные образы, отчёты и teardown |
| Prepare/test/cleanup-engine | Одна CI-generation на несколько шагов, скрытая capability |
| test-native-image.sh | Контейнер, fixtures/test binary, отчёт native проверки |
| Browser regression | Используемый dist, browser artifacts |
| Pytest disposable suites | Plugin при явном disposable opt-in |

Pytest plugin активен только при RAGFLOW_ARTIFACT_MANAGED_QA=1,
RAGFLOW_TEST_DISPOSABLE=1, RAGFLOW_MINIO_BACKUP_TEST=1,
RAGFLOW_COORDINATED_BACKUP_TEST=1 или RAGFLOW_SANDBOX_IMAGE_TEST=1.
Обычные unit-тесты и приложение не перехватываются. Живые дочерние процессы
блокируют teardown. Новый Python producer использует Run/managed, docker_args и
note; Bash — `deployment/runner/artifact-shell.sh`. Произвольный raw Docker
внутри стороннего скрипта не перехватывается только от внешнего exec.

Native builder при прямом запуске открывает собственный managed run. При запуске
в Linux builder-контейнере используется `RAGFLOW_ARTIFACT_TRANSPORT=receipt`:
журнал Windows-хоста не открывается из контейнера. Host заранее сохраняет
INCOMPLETE receipt с run ID, immutable toolchain ID и уникальной invocation,
передаёт invocation дочернему процессу и учитывает partial outputs в finalizer.
Успешная команда требует свежего PASS того же run/invocation/toolchain, полного
набора native outputs и совпадения их фактических SHA. Receipt предыдущего
запуска не подтверждает результат нового. Receipt transport без host run
отклоняется до создания файлов.

Labels: org.ragflow.artifact.project=ragflow, .run=uuid, .object=generation uuid,
.class=disposable|cache|runtime. Evidence — файловый класс. Engine ID, object
token и immutable ID проверяются отдельно от имён. CI dry-run fixture поддержан
узким адаптером его UUID label + image ID. Старые объекты не усыновляются по имени.

## Очистка

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_registry --measure
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle cleanup
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle cleanup --apply
```

Без --apply удаления нет. Disposable run хранится 7 суток после completed;
одноразовый QA выполняет собственный teardown сразу. Builder/image/cache —
30 суток. Срок не снимает защиту потребителя, lease, deployment receipt, другого
открытого запуска, изменившейся конфигурации, нового tag или runtime mount.
Последняя проверка удаления сериализована с публикацией managed зависимостей.
Частичная ошибка сохраняет точные IDs, архив и возможность повторения.

Существующая задача **RAGFlow Temporary Cleanup** выполняет temp/pnpm cleanup,
managed Docker/cache retirement, recovery expiry и refresh индекса.
`.local/maintenance/temp-cleanup/last-run.json` заменяется; ошибки дают ненулевой
результат. Дополнительного расписания и общего prune нет.
Установщик `deployment/local/install-temp-cleanup.ps1` запускается однократно из
повышенного PowerShell для регистрации S4U. Само задание работает под тем же
пользователем с `Limited` privileges, без сохранённого пароля и без зависимости
от подключённой интерактивной сессии. Docker Desktop должен работать для этого
пользователя; S4U не предоставляет доступ к сетевым ресурсам и EFS-файлам.
Refresh запускает `tools.quality.artifact_registry` как модуль из корня checkout,
поэтому не зависит от текущего каталога вызывающего процесса или `PYTHONPATH`.

Sweeper не останавливает контейнеры. Только владелец QA в teardown останавливает
собственные явно disposable контейнеры. Остановленный чужой контейнер тоже
защищает image/volume. Images удаляются с --no-prune, без force; тома и сети —
только точного поколения без потребителей. Внешние драйверы/driver options томов
защищены. Bind sources через Docker cleanup не удаляются. Compose down заменён
тем же retirement без распространения down -v на чужие/external volumes.

Workspace очищает существующий Complete, pnpm — существующий lease/TTL/quota.
Hash-addressed check receipts и проверенные native entries удаляются под cache
lock после повторной проверки использования. Native cache воспроизводится из
manifest/recipe, без архива бинарников. Прочие файловые классы остаются retained.
Runtime, backups, release/rollback и произвольные архивы не удаляются.

Аварийный CI attempt автоматически закрывается только при совпадении GitHub
repository/run/attempt/head SHA и статусе completed. Недоступный GitHub или
несовпадение proof сохраняет защиту. Даже этот proof не разрешает остановить живой
контейнер. Инвентаризация сама не обновляет время использования.
Такой CI proof подтверждает terminal execution; отдельное подтверждение task
closure и остальные guards для фонового retirement по-прежнему обязательны.

## Восстановление

До удаления сохраняются inspect и проверяемый packet в
`.local/archives/artifact-lifecycle` (RAGFLOW_ARTIFACT_ARCHIVES для другого store).
**Inspect может содержать секреты: ignored recovery store нельзя публиковать
вместе с публичными QA-отчётами.** Сам inventory не содержит environment/.env.

Образы сохраняются по ID; общие сжатые blobs дедуплицируются по SHA-256.
Повторный OCI blob читается и сверяется по исходному SHA/размеру; существующий
сжатый packet проверяется перед повторным использованием без нового сжатия.
Неизменённому контейнеру достаточно Config/HostConfig и immutable base ID;
перед удалением base image сохраняется его packet. Уникальный writable layer
сохраняется снимком. Тома архивируются без запуска приложения: GNU tar сравнивает
данные/metadata с оригиналом и проверяет распаковку в отдельный probe volume.
Нужен установленный helper по digest из artifact_docker.py либо
RAGFLOW_ARTIFACT_TAR_IMAGE; CI bootstrap его устанавливает. Без helper удаление
тома блокируется. Объявленные уникальные outputs из temp/CI сохраняются с
manifest; изменение после архивирования блокирует Complete.

До распаковки volume packet проверяются имена tar members, их уникальность,
типы, пути предков и разрешимость hardlinks. Traversal, absolute paths,
неоднозначные aliases, special files и запись через symlink блокируются.
Symlink может сохранять внешний target как metadata; переход по нему при
восстановлении запрещён. Эта проверка формата дополняет сравнение реальных
данных и metadata, а не заменяет его.

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle restore --run <run_id>
```

Проверяются SHA и занятость имён. Volume/config/network восстанавливаются,
контейнеры создаются **остановленными**, актуальные tags обычных образов не
заменяются. Для временного alias проверяется конфликт tag. Отсутствующий bind
source предварительно возвращают из evidence: пустой каталог вместо данных не
создаётся. Recovery run получает paused. Это не запуск продукта и не проверка
совместимости миграций.

Закрытие failed/paused запуска требует конкретного подтверждающего файла:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle complete --run <run_id> --evidence <report>
```

`complete`/`release` принимают JSON-решение (до 4 МиБ), а не произвольный отчёт
с корректным SHA. Получить subject без изменения ledger:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle subject --run <run_id>
```

В решении задаются `schema: 1`, `project: "ragflow"`, соответствующий `action`,
объект `subject` из этой команды, `execution_status: "completed"`, непустой
`approved_by` и `task_status: "open"|"completed"|"superseded"`. Subject включает
точные generation/source/scope, состояния и хэши recovery/preserved packets;
Config/Env и owner capability в публичный subject не входят. После изменения
ресурсов нужен новый subject. Решение проверяется под lease и mutation lock.
Живого владельца нельзя закрыть; `complete` с открытой продуктовой задачей не
освобождает recovery и не объявляет продукт принятой поставкой.
Явный `task_status: "open"` также блокирует автоматический retirement и удерживает
зависимые images независимо от TTL. Новый подтверждённый статус хранится отдельно
от исторического completion receipt. Повтор неизменённого решения идемпотентен:
не сдвигает completed/release time и не создаёт новый recovery epoch.

Generation completion не освобождает recovery: продуктовая задача может
продолжаться. Когда восстановление больше не требуется, владелец явно
освобождает packet с сохранением SHA подтверждения:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle release --run <run_id> --evidence <closure-report>
.venv\Scripts\python.exe -B -m tools.quality.artifact_lifecycle gc
```

Release требует completed и удалённых Docker-ресурсов. Затем действует ещё
90 суток. Weekly gc --apply удаляет только точные проверенные файлы своего
recovery store, сохраняя общие packets/blobs других запусков. При running
producer GC откладывается. Неосвобождённые packets и произвольные архивы защищены.

Для release дополнительно нужны закрытый `task_status` и явное
`recovery_needed: false`; окно 90 дней отсчитывается от фактического выполнения
release, а не заявленной задним числом даты в JSON. Это подтверждение владельца,
не замена проверке зависимостей и целостности в GC. Старые текстовые отчёты
остаются доказательствами; автоматически превращать их в release нельзя.
GC повторно проверяет SHA approval, актуальный subject и отсутствие present
Docker-ресурсов. Один старый timestamp без bound approval не разрешает expiry.
Любое возобновление recovery отменяет прежний release и повышает recovery epoch;
исторические packets остаются учтены. Новое освобождение требует свежего subject
и нового 90-дневного окна. Частичный GC связывает expiry checkpoint с subject,
чтобы другой generation не наследовал список удаления.

## Разбор исторической очереди

Для bounded поиска происхождения по явно перечисленным JSON/JSONL/JSONL.zst
источникам используется read-only locator:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_provenance <session.jsonl> <session.jsonl.zst> --id <exact-task-or-run-id>
```

Результат содержит locator, line и SHA записи/источника, без исходного текста.
Corruption, усечение, изменение источника и превышение бюджета дают incomplete
и ненулевой exit. Даже полный поиск только находит кандидатов evidence:
он не подтверждает закрытие, не назначает владельца и не разрешает удаление.

Подтверждённые решения сохраняются в том же lifecycle ledger:

```powershell
.venv\Scripts\python.exe -B -m tools.quality.artifact_review <decisions.json>
.venv\Scripts\python.exe -B -m tools.quality.artifact_registry --repo S:/ragflow --temp-root S:/tmp/ragflow
```

План — JSON-массив с `id` из полного текущего inventory, `owner` (task/report или
ответственный хранитель), `purpose`, `reason`, `outcome` и `evidence` (пути к
подтверждениям). Допустимы active/retained/open/completed/superseded/unresolved;
superseded требует явный `successor`. Для неизвестного исходного владельца
сохраняйте `origin_owner: null`; хранитель не подменяет доказательство происхождения.
Если основание актуальности также не подтверждено, используйте outcome unresolved.
У открытых задач сохраняются материалы продолжения.

Запись привязывается к точной identity и SHA подтверждений. Изменение evidence
или поколения возвращает объект на проверку. Повторный scan не продлевает срок
пересмотра. Такой review не усыновляет Docker-ресурс, не создаёт cleanup ownership
и никогда не даёт разрешения удаления. Фактическая очистка требует отдельной
проверки закрытия/замены, потребителей и восстановления по условиям класса.

Для `completed`/`superseded` дополнительно обязательны `origin_owner` и `closure`
(путь к JSON-подтверждению, не больше 1 МиБ). Это явная декларация принятого
результата, привязанная к объекту, а не автоматическое доказательство внешнего
статуса задачи. Потребители, пригодность recovery и право конкретного executor
проверяются независимо. SHA произвольного отчёта не заменяет такую привязку.

| Поле closure | Требование |
| --- | --- |
| `schema`, `project` | `1`, `ragflow` |
| `artifact` | Точный объект из `tools.quality.artifact_review.subjects(inventory)[id]`: identity, kind, engine, scope/source и все совпавшие по поколению producer declarations |
| `task_ref` | Исходная задача `origin_owner`; при наличии producer должна совпасть с его task_ref |
| `outcome`, `accepted` | Тот же completed/superseded и явное `true` |
| `successor` | Для superseded — точный subject существующей принятой замены того же data scope/engine |

Перед подготовкой subject обновите inventory; используйте текущие sealed runs
своего store как `inventory.lifecycle`. Пустой source/scope в filesystem index
не отменяет source/scope producer: они включаются в subject из ledger. Изменение
декларации возвращает closure на проверку, даже если путь и байты proof прежние.
Self/missing successors и циклы замены отвергаются. Старые completed/superseded
reviews без bound closure сохраняются как custody evidence, но требуют повторной
проверки; автоматического повышения их квалификации нет.

Очередность новых reviews задаётся sequence под общей mutation lock, а не
настенными часами. Более новый invalid/unresolved review не откатывается к
прежнему valid. Неоднозначные legacy записи одинакового порядка остаются
unresolved. В inventory различаются producer/origin_owner и custodian;
хранитель не переписывает установленного producer и не получает delete authority.

Recovery контейнера удерживает явный CAS packet базового образа и identities
его volume mounts. Восстановление допускает исходное поколение тома либо
проверенное новое поколение из того же recovery; чужое совпавшее имя отклоняется.
Bind/volume guards повторяются непосредственно до и после container create.
Том на время export/restore/compare удерживает отдельный собственный helper
container; поколение проверяется после создания helper и до I/O. Helper/probe
имеют parent run, durable intent и create/remove events; их аварийные остатки
учитываются при следующем capture. Восстановленные image/alias удерживаются
зависимостью recovery run до его завершения.
Сам recovery run удерживает все использованные input packets, включая случай
image-only restore без собственных контейнеров. Перед восстановлением source
journal поглощает старые create/remove events; проверенное восстановление immutable
image явно возвращает его в present. Старое удаление snapshot не может перекрыть
новое восстановление. Необъяснённо появившееся retired generation даёт blocker,
вместо ложного сообщения об успешной уборке.

Временные snapshot images также получают durable intent и create/remove events,
включая отказ экспорта. Успешный `SystemExit(0)` означает успешное завершение
процесса; явный failed-флаг по-прежнему имеет приоритет. Эти execution states
не подменяют приёмку продукта и решение о release.

Целостность inspect/CAS/base/helper пакетов проверяется перед удалением,
включая финальную mutation boundary. Неизвестная транзитивная зависимость или
отсутствующий retained CAS manifest блокирует GC. За секунду до release + 90
суток packet сохраняется; ровно на границе становится eligible только при
остальных проверках. Невалидные/будущие даты не разрешают раннее удаление.

## Границы

Inventory включает известные .local группы, temp, build outputs и Docker
RAGFlow/зависимости. Имя RAGFlow — только needs-review. Дополнительный checkout
RAGFlow добавляется --repo. Глобальные Docker consumers читаются для защиты общих
зависимостей. Битый receipt/ошибка чтения означает incomplete. Размер образа
логический; сумма размеров не равна освобождённым блокам.

BuildKit учтён как shared cache текущего engine; его exclusive ownership не
доказан, поэтому общей очистки нет. Внешние registry artifacts и необъявленные
remote/home каталоги требуют подключения соответствующего источника и полномочий.
Известные домашние RAGFlow cache/toolchain roots уже включены; старые неизвестные
entries не усыновляются автоматически и имеют условия пересмотра.
Новый класс требует producer, completion, consumer/lease check и проверяемое
восстановление до включения удаления.
