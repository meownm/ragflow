# T3: selector и aggregate архитектурной политики

Дата обновления: 2026-09-14. Статус до интеграции настоящего control receipt: `IN_PROGRESS`; целевой solo-maintainer статус — `VERIFIED_WITH_ACCEPTED_RISK`. PR #4, #12, #31, #33, #36 и #37 последовательно слиты в `main`; current trusted base — `30a8123fb1f483934dc094d6cef2135ac125a3c6`. Functional schema-v3 acceptance получена на draft PR #35, который остаётся открытым и не предназначен для merge. Active repository ruleset `main-solo-maintainer-gate-v1` (`23306934`) защищает `main` без bypass и требует strict canonical `architecture-policy` от GitHub Actions App `15368`. Protected diagnostic PR #38/#39 закрыты без merge: positive, stale-head, cancellation, control-deletion, user-status spoof, same-name/same-App duplicate и skipped-duplicate сценарии дали ожидаемые mergeability verdicts. Этот инкремент добавляет `.github/CODEOWNERS` с единственным владельцем `@meownm`, регистрирует его как protected policy source и фиксирует принятые topology/governance риски. Машинный aggregate не меняется и честно остаётся `REPORT_ONLY_COMPLETE / NOT_ENABLED`; внешний enforcement подтверждает отдельный live receipt. Deploy не выполнялся.

## Контракт

`tools/quality/check_architecture_policy.py` ничего не исправляет, не выполняет выбранные анализаторы и не изменяет Git index. Действие `select` строит план по явному base commit и текущему T0 snapshot. Оно учитывает committed, staged, unstaged и nonignored untracked paths; `--no-renames` сохраняет обе стороны rename/delete. Текущий путь получает owner/origin из свежего T0, удалённый или возвращённый к upstream путь — owner из `module-map.yaml` базового commit. Неизвестный путь, недоступный base, неполная provenance или изменение дерева во время capture дают код 2. План фиксирует hashes evaluator, base/candidate policy и candidate checker, exact source bundle каждого lane, ожидаемые producer/config digests отчётов и hashes fixture sources; внешний policy-файл принимается только при полном совпадении с содержимым candidate или comparison base.

Машинная политика `tools/quality/architecture-policy.json` определяет пять lanes:

| Lane | Выход | Значение успешного evidence |
| --- | --- | --- |
| `provenance` | встроенный свежий T0 | `OBSERVED`, не behavioral PASS |
| `python-architecture` | `architecture-python.json` | точный настроенный `PASS` |
| `runtime-graph` | `runtime-graph.json` | `CLASSIFIED`; policy остаётся `REPORT_ONLY` |
| `go-build-plan` | `go-build-plan.json` | `PLANNED`; native build остаётся `NOT_EVALUATED` |
| `policy-fixtures` | identity-bound JSON attestation со встроенным JUnit | authoritative checker выполнил точные trusted `path::nodeid` fixtures без failure/error/skip на том же T0 fingerprint |

Действие `compare` проверяет candidate policy относительно authoritative policy: запрещены удаление существующего lane/rule/required fixture, смена tool/contract, снятие `always` и сужение exact paths/prefixes/suffixes. Candidate plan обязан охватывать каждый lane, выбранный authoritative plan на том же HEAD/T0 fingerprint. При `authority_source=base` отчёт получает `compatibility_status=COMPATIBLE` и доказывает только несужение текущей base policy. При `authority_source=candidate-bootstrap` тот же механизм является исключительно self-check целостности candidate policy/plan и получает отдельный `compatibility_status=BOOTSTRAP_SELF_CHECK`; trusted base coverage в этом режиме не оценена. Расширение разрешено, но новое candidate-only правило не считается обязательным до появления в base.

Действие `materialize` извлекает каждый protected source выбранных lanes из comparison base, а в bootstrap — из совпадающего candidate bundle, перепроверяет digest и создаёт новый отдельный source tree рядом с plan. Статические producers запускаются из этого дерева через `run_isolated_python.py` с `python -I`: candidate working directory не участвует в import path, а локальные imports разрешаются только из materialized `tools/quality`. Настроенный Python architecture producer запускается отдельно через materialized `run_architecture_sandbox.py` в одноразовой Docker-boundary: candidate, trusted sources и root filesystem доступны только для чтения, сеть и IPC отключены, а supervisor предоставляет только заранее созданный файл `architecture-python.json` с mode `0620`. Producer имеет UID 0, GID группы runner и только `SETUID`/`SETGID`, необходимые для запуска runtime children как `65534:65534`; у children нет supplementary groups и effective capabilities, включён `no_new_privileges`. Перед producer отдельный unprivileged probe обязан подтвердить все 12 отрицательных проверок доступа, identity, environment, Git config, network, capabilities и NNP. В режиме `authority_source=base` обычный изменённый analyzer, config, fixture, harness dependency или dependency lock закрывает lane как `INCOMPLETE`. Единственный base-governed control source с режимом `BASE_FIXTURE_REVIEW` — `tools/quality/architecture-policy.json`: authoritative plan и materialization используют его base-версию, candidate policy проходит монотонное base/candidate comparison, base fixtures проверяют её контракт, а plan требует ручного review. Candidate checker и workflow остаются `MUST_MATCH`; их обновление требует отдельного аудированного protocol/bootstrap flow. В bootstrap все sources принадлежат candidate self-check и сравнение их bytes с comparison base не выполняется.

Действие `fixtures` само запускает перечисленные policy `path::nodeid` между двумя T0 captures. При `source=base` fixture files извлекаются из comparison base и получают candidate root только через `ARCHITECTURE_CANDIDATE_ROOT`; candidate не может заменить их одноимёнными no-op tests. Pytest запускается с `--noconftest`, отключённой автоматической загрузкой plugins и очищенным от GitHub command-file/secret variables окружением. Прямые test-harness dependencies exact runtime contracts также входят в protected source closure. Внутри Docker-boundary exact pytest runtime получает только заранее созданный write-only scratch-файл JUnit: child может заполнить его, но не прочитать и не заменить, а trusted parent разбирает результат. Attestation фиксирует source hashes, exact node IDs, HEAD, upstream base, T0 fingerprint, selection digest, SHA-256 и base64 исходного JUnit; aggregate повторно разбирает встроенный JUnit и отклоняет несовпадение names/counts/digest.

Действие `aggregate` повторно строит authoritative и candidate selector plans и сравнивает их с обоими сохранёнными файлами. Поэтому ручная смена `selected=true` на `false` не превращает применимую проверку в `NOT_APPLICABLE`, а произвольный синтаксически корректный candidate-plan digest не подменяет результат `compare`. Все передаваемые в aggregate JSON reports, включая fixture attestation, обязаны иметь тот же comparison base, HEAD, upstream base, T0 fingerprint, selection digest и ожидаемые producer/config hashes своего lane. Промежуточные TypeScript/Go observer reports не являются самостоятельным aggregate evidence: их hashes входят в итоговый runtime-graph report. При `authority_source=base` изменение ordinary protected analyzer/config/fixture/dependency source относительно base закрывает выбранный lane как `INCOMPLETE`, даже если отчёт сам объявляет правильное tool name и `PASS`; исключение `BASE_FIXTURE_REVIEW` ограничено candidate policy и не меняет base authority. Bootstrap проверяет только согласованность candidate bundle и не делает такой вывод относительно comparison base. Evidence принимается только из свежего каталога рядом с plan; отсутствующий, stale, malformed или созданный другим producer отчёт даёт `INCOMPLETE`. При одновременном finding и неполноте итоговый код 2 сохраняет приоритет `INCOMPLETE`; чистый finding без неполноты даёт код 1.

Успешный текущий aggregate возвращает `REPORT_ONLY_COMPLETE` с `enforcement_status=NOT_ENABLED`. Совместимость с реальной base policy отображается как `PASS`, а bootstrap self-check — только как `OBSERVED`; подмена пары authority/status даёт `INCOMPLETE`. Статусы `OBSERVED`, `CLASSIFIED` и `PLANNED` намеренно не называются `PASS`.

## Локальная диагностика

Локальный запуск пригоден для review selector/compare, focused tests и анализа отчётов, но не заменяет Linux base-mode приёмку. Прямой host-запуск `check_architecture.py` не создаёт Docker-attestation `os_isolation` и поэтому не должен передаваться в aggregate как эквивалент CI evidence. Точный исполняемый сценарий полного цикла находится в `.github/workflows/architecture.yml`; он требует Linux Docker semantics, отдельного candidate clone, materialized base bundle и supervisor-owned evidence directory.

Для локальной проверки coverage использовать новый пустой каталог и точный commit comparison base. Если base содержит protocol v1, checker и policy извлекаются именно из неё; `candidate-bootstrap` допустим только для исторической базы без protocol и доказывает лишь self-consistency кандидата.

```powershell
$trustedEvidence = "output/quality/t3-policy-review-$(Get-Date -Format yyyyMMdd-HHmmss)"
$base = (git rev-parse "origin/main^{commit}").Trim()
$trustedQuality = Join-Path $trustedEvidence "trusted-base/tools/quality"
New-Item -ItemType Directory -Force -Path $trustedQuality | Out-Null
git cat-file blob "${base}:tools/quality/check_architecture_policy.py" > "$trustedQuality/check_architecture_policy.py"
git cat-file blob "${base}:tools/quality/capture_inventory.py" > "$trustedQuality/capture_inventory.py"
git cat-file blob "${base}:tools/quality/architecture-policy.json" > "$trustedQuality/architecture-policy.json"

$baseRunner = Join-Path $trustedQuality "check_architecture_policy.py"
$basePolicy = Join-Path $trustedQuality "architecture-policy.json"
& .\.venv\Scripts\python.exe -B $baseRunner --root (Get-Location) --policy $basePolicy select --base $base --output "$trustedEvidence/base-selection.json"
& .\.venv\Scripts\python.exe -B $baseRunner --root (Get-Location) --policy tools/quality/architecture-policy.json select --base $base --output "$trustedEvidence/candidate-policy-selection.json"
& .\.venv\Scripts\python.exe -B $baseRunner --root (Get-Location) --policy $basePolicy compare --base-plan "$trustedEvidence/base-selection.json" --candidate-plan "$trustedEvidence/candidate-policy-selection.json" --candidate-policy tools/quality/architecture-policy.json --output "$trustedEvidence/policy-compatibility.json"
```

Запускать reports нужно только по `required_reports` authoritative `selection.json`; лишний отчёт отклоняется так же, как отсутствующий выбранный. Промежуточный code 2 от `inspect_typescript.py` допустим только для уже известного computed-import gap и только если следующий `check_runtime_graph_policy.py` выдаёт полную exact-классификацию. ASR lock регенерируется только при осознанном изменении direct pins по [инструкции T2](t2-python-analysis-ru.md); workflow сам lock не обновляет.

## Workflow

`.github/workflows/architecture.yml` запускается для `opened`, `synchronize`, `reopened`, `ready_for_review` и `edited` событий `pull_request_target`, для `merge_group/checks_requested` и при push в `main`, без метки `ci`, ручного candidate-ref dispatch и path filters. На PR workflow definition берётся из trusted default branch. Для `pull_request_target` resolver принимает только PR в default branch того же base repository и связывает authoritative base с `github.sha == github.workflow_sha`; `pull_request.base.sha` сохраняется в receipt только как advisory `MATCH/DIFFERENT`. Base-owned `tools/quality/resolve_pr_merge_ref.py` в новом resolver repository выполняет stable exact `read/fetch/read`, проверяет реально fetched commit и его точные authoritative-base/head parents, после чего переносит именно этот объект в plan checkout. Plan публикует candidate SHA, resolved base SHA и identity receipt digests; analysis и final извлекают resolver из resolved base, сверяют source digest, exact parents и receipt, а final требует побайтового совпадения plan/analysis receipt. Последующий network checkout в analysis/final остаётся явным fail-closed exact-SHA refetch: исчезновение объекта завершает job, а временное окно записано в receipt как `exact_sha_refetch_fail_closed`. Merge queue отдельно связывает `merge_group.head_sha` с event SHA, а push использует event SHA. Один принятый immutable SHA передаётся plan → analysis → final, каждый checkout закреплён на нём и выполняется с `persist-credentials: false`; несовпадение фактического checkout закрывает job кодом 2. Comparison base берётся соответственно из resolved default-branch `github.sha`, `merge_group.base_sha` или предыдущего push SHA. Concurrency group не содержит `cancel-in-progress`, чтобы выполняющийся required run нельзя было заменить отменой. В workflow три job: `architecture-policy-plan` без установки candidate dependencies и запуска candidate analyzers/tests; `architecture-policy-analysis` для выбранных environments и отчётов; финальный `architecture-policy` на новом runner. Только финальный job имеет стабильный display name `architecture-policy`.

Plan job извлекает `check_architecture_policy.py`, `capture_inventory.py` и `architecture-policy.json` из comparison base, строит authoritative base plan, независимо оценивает candidate policy и выпускает обязательный `policy-compatibility.json`. Только изменение candidate policy получает `BASE_FIXTURE_REVIEW`: base evaluator и source bundle остаются неизменными, а candidate policy обязана сохранить coverage. Изменённые checker или workflow закрывают затронутые lanes как обычные protected sources. Analysis job получает plan artifact и сначала отклоняет любой выбранный, но неисполняемый из-за source integrity lane; поэтому оставшийся runnable lane не запускает candidate lifecycle после неполного соседнего lane. Затем job материализует protected sources, создаёт отдельный detached candidate clone без remotes и переносит в него только подготовленные locked environments. Trusted fixtures остаются обязательным `success()`-предусловием. Статические TypeScript/Go producers используют isolated safe-path runner; только configured Python architecture producer и его 19 exact runtime probes выполняются через Docker OS-boundary. Все artifact upload явно включают hidden files, поэтому защищённые `.github/workflows/architecture.yml` и `web/.npmrc` не исчезают из evidence bundle. Дочерние Python/pytest и Node subprocesses protected producers не наследуют GitHub command files, evidence paths, tokens, `NODE_OPTIONS`/`NODE_PATH` или ambient pytest/Python injection variables; conftest discovery отключён. Финальный job не выполняет `uv sync`, pnpm, pytest или candidate analyzers: он заново checkout-ит candidate tree, повторно извлекает authority из base, проверяет неизменность base/source и selection, повторяет candidate-policy comparison, требует точного совпадения compatibility с plan artifact, загружает отчёты и передаёт compatibility в aggregate. Отсутствующий, stale или не соответствующий plan compatibility закрывает final fail-closed. Поэтому candidate lifecycle из analysis job не может изменить checker/policy, реально исполняемые финальным aggregate.

Если comparison base содержит `TRUSTED_BASE_PROTOCOL = 1`, authority в plan и final берётся из base, а проверенная совместимость может получить `PASS`. Если protocol отсутствует или bundle неполон, оба job явно используют `candidate-bootstrap`; compatibility остаётся `OBSERVED`, весь результат сохраняет `enforcement_status=NOT_ENABLED` и не заявляет non-bypassable gate.

Workflow готовит только выбранные environments, выполняет reports без `--write`/`--fix`/`git add` и сохраняет раздельные plan, analysis и final artifacts. Финальный job имеет `if: always()` и остаётся видимым required context даже при failure/skip предыдущего job; он отдельно требует `result == success` и для plan, и для analysis, поэтому поздний setup/post-action failure нельзя скрыть уже созданным report artifact. Отсутствие plan или выбранного report также закрывает проверку ошибкой.

Все job используют `ubuntu-latest`; plan/final имеют 15-минутный, analysis — 45-минутный timeout. Все `uses:` закреплены полными 40-символьными commit SHA: `actions/checkout` `d23441a48e516b6c34aea4fa41551a30e30af803`, `actions/upload-artifact` `ea165f8d65b6e75b540449e92b4886f43607fa02`, `actions/download-artifact` `d3f86a106a0bac45b974a628896c90dbdf5c8093`, `actions/setup-node` `49933ea5288caeca8642d1e84afbd3f7d6820020`, `astral-sh/setup-uv` `d0cc045d04ccac9d8b7881df0226f9e82c39688e`, `pnpm/action-setup` `b906affcce14559ad1aafd4ab0e942779e9f58b1`; 2026-09-12 эти SHA повторно сверены с официальными GitHub refs соответствующих major tags. Node 22, pnpm 10 и dependency synchronization существуют только в analysis, Python 3.13 для policy runner устанавливается через закреплённый `astral-sh/setup-uv`. Корневое Python-окружение создаётся из tracked hash-locked requirements; выделенное ASR contract-окружение — командой `uv pip sync --require-hashes --strict`. TypeScript parser устанавливается не напрямую из candidate `web`: authoritative `package.json`, `pnpm-lock.yaml` и `.npmrc` копируются из materialized bundle в новый каталог `RUNNER_TEMP`, где pnpm запускается с `--ignore-scripts --ignore-pnpmfile --ignore-workspace`. В реальном `authority_source=base` эти inputs закреплены base; изменённый manifest/lock/config делает каждый Node-consuming lane неисполняемым и analysis не запускает candidate package-manager inputs. В `candidate-bootstrap` materialized inputs принадлежат candidate self-check и не являются доверенной supply-chain границей. Свежий candidate clone обязан не содержать `web/node_modules`; в него через dereference копируется только пакет `typescript`, после чего проверяется его реальный `lib/typescript.js`. В analysis artifact временный полный `node_modules` не попадает. Fixture-only lane не поднимает Node. Package manager и dependency preparation не входят в Docker-boundary; защита здесь основана на base-pinned inputs, отключённых scripts/hooks/workspace и отсутствии application secrets. Полный успешный base-mode цикл старой trigger-версии подтверждён runs `34691682512` и `34692464102`; post-integration `pull_request_target` цикл подтверждён run `34706026252`, но первичный `opened` path остаётся непринятым до live-проверки исправления merge-ref race. Реальная `merge_group` проба недоступна в текущем personal-account topology.

Workflow использует read-only permissions и не получает application secrets. Docker-boundary защищает configured Python runtime probes, но не является общей sandbox для package manager, trusted policy-fixture harness или статических analyzers; эти поверхности ограничены отдельными base-authority и environment contracts. `BASE_FIXTURE_REVIEW` и запись в `manual_review_required` являются только evidence для ревью, а не технически обеспеченным approval. Candidate checker/workflow поэтому не входят в исключение и должны совпадать с base; их изменение, изменение protected fixtures либо сужение controls требует отдельного аудированного обновления протокола/bootstrap. Текущий report-only evidence не объявляется non-bypassable до проверенного Required Workflow и обязательного control-owner approval. Наличие YAML и успешных PR-проб не доказывает, что job обязателен для merge.

## Нормативный контракт PR merge-ref resolver

Этот контракт относится только к определению immutable candidate revision для `pull_request_target`. Он не доказывает корректность candidate-кода, не заменяет architecture lanes, не включает repository enforcement и не разрешает merge, deploy или изменение ruleset. Resolver работает до выполнения candidate-кода и обязан fail-closed отделять недоступность GitHub ref от принятой identity.

### Граница доверия и входы

Доверенными входами являются только поля текущего base-owned event и константы trusted workflow: `event_name`, PR number, immutable `github.sha`, `github.workflow_sha`, `pull_request.base.repo.full_name`, `pull_request.base.ref`, repository default branch, `pull_request.head.sha`, advisory `pull_request.base.sha`/`pull_request.merge_commit_sha`, `github.repository` и `github.server_url`. Resolver требует равенства base repository текущему repository, base ref — default branch, а `github.sha` — commit с workflow definition; несоответствие отклоняется до network access. Название candidate-ветки, candidate-файлы, candidate environment/config, локальные remotes и произвольный URL не участвуют в выборе revision. Для текущего public repository разрешён анонимный HTTPS read exact base-repository merge ref; private repository, PR не в default branch, иной Git transport и GitHub Enterprise требуют отдельного спроектированного профиля и до этого остаются `NOT_SUPPORTED`, а не молчаливым fallback.

Live `synchronize` run `34818108224` после продвижения `main` с `312c9ce8…` на `bd509205…` доказал необходимость этой границы: GitHub передал `github.sha=bd509205…`, но payload `pull_request.base.sha=312c9ce8…`; stable synthetic merge `b384c3fc…` имел родителей `[bd509205…, 7a1e0f04…]`. Прежний resolver 46 раз классифицировал объект как `STALE` и через 120 секунд завершился `PARENT_MISMATCH`; analysis не запускался, canonical final был `failure`. Это корректный fail-closed negative, но payload base не может быть authority после продвижения default branch. Новый receipt schema v3 сохраняет оба значения и отклоняет подмену authority, workflow SHA, base repository/ref, downstream base и ordered parents. До отдельной интеграции control update и post-integration live-повтора этот candidate не является GitHub PASS.

Перед сетевым обращением resolver обязан:

1. Проверить event type, положительный десятичный PR number, точные repository/server values и полный Git object ID ожидаемого формата. Для текущего GitHub SHA-1 профиля это 40 lowercase hex; переход на иной object format требует отдельного изменения контракта и fixtures.
2. Создать новый каталог под `RUNNER_TEMP` с уникальной identity run/attempt. Существующий каталог является ошибкой. Работа из candidate checkout и повторное использование `.git` запрещены.
3. Инициализировать repository без template/hooks и очистить влияющие Git variables: system/global/local config discovery, credential helpers, askpass, interactive prompt, `GIT_DIR`, `GIT_WORK_TREE`, object/alternate directories и environment-injected config. TLS verification не отключается; URL строится без `eval` только из проверенных trusted полей.
4. Задать явный монотонный wall-clock deadline и ограниченный backoff. Число попыток само по себе не является deadline. Выбранное значение, фактическая длительность и максимальная задержка должны быть записаны; первоначальный предел обосновывается измеренными live materialization latencies и запасом, а не объявляется гарантией GitHub.

### Модель состояний

| Состояние | Наблюдение | Действие |
| --- | --- | --- |
| `ABSENT` | exact ref не опубликован | ждать до deadline |
| `MALFORMED` | не одна exact строка, неверный ref name/SHA или объект не commit | fail-closed; не извлекать SHA из частичного вывода |
| `STALE` | commit существует, но родители не равны event base/head | повторить до deadline; затем `INCOMPLETE` |
| `CHANGING` | SHA до fetch, fetched object и SHA после fetch не совпадают | отбросить попытку и повторить |
| `STABLE_CURRENT` | один ref, стабильный fetched commit и точные event base/head parents | единственное принимаемое состояние; отношение payload записывается отдельно |
| `UNAVAILABLE` | DNS/TLS/network/auth/checkout не позволяют получить доказуемый объект | `INCOMPLETE`, ненулевой exit |

`ABSENT`, `STALE` и `CHANGING` являются ожидаемыми промежуточными состояниями, а не немедленным PASS или окончательным finding. Ни одно состояние кроме `STABLE_CURRENT` не выпускает `candidate_sha`.

`pull_request.merge_commit_sha` из immutable event payload не является текущим ref: live `synchronize` доказал, что он может остаться SHA предыдущего test merge commit. Resolver валидирует его формат, сохраняет raw value внутри подписанной event identity и отдельно аттестует отношение `ABSENT`, `MATCH`, `DIFFERENT` либо `UNRESOLVED`, но никогда не выбирает по нему объект. `DIFFERENT` допустим только как advisory-наблюдение уже независимо доказанного `STABLE_CURRENT`; malformed payload по-прежнему завершает run до network access.

Resolver не пытается вывести направление обновления по `refs/pull/<N>/head`. Стабильные merge/head refs с родителем, отличным от event head, могут означать как действительно более новый PR head, так и ещё не догнавшие новый event старые refs. Поэтому такое наблюдение остаётся `STALE` и повторяется до exact event parents либо deadline; отдельный терминальный `SUPERSEDED` из одного снимка refs недоказуем.

### Алгоритм одной попытки и цикла

Для каждой попытки до монотонного deadline выполняется одна и та же последовательность:

1. Выполнить exact `ls-remote` и получить `R1`. Ноль строк означает `ABSENT`; не одна строка или несовпадающее имя — `MALFORMED`.
2. Fetch именно exact ref в изолированный repository/namespace без tags, submodules, hooks и credential persistence. Получить `F` из реально загруженного объекта, а не из ранее разобранной строки.
3. Доказать `F` как commit через object database и прочитать родителей с отключёнными replace refs; свежий repository не должен содержать grafts или alternates.
4. Повторить exact `ls-remote` и получить `R2`. При `R1 != F`, `F != R2` или `R1 != R2` классифицировать попытку как `CHANGING` и не использовать объект.
5. Потребовать ровно двух родителей и точное упорядоченное равенство `parents == [event_base_sha, event_head_sha]`. Иное значение означает `STALE`, даже если SHA синтаксически корректен.
6. Сопоставить валидированный event payload merge SHA с `F` только как advisory-наблюдение и записать `ABSENT`, `MATCH` или `DIFFERENT`; значение payload не участвует в выборе или принятии объекта.
7. Только после всех проверок атомарно записать `candidate_sha=F` и identity receipt schema v2. При промежуточном состоянии дождаться backoff, не превышая deadline, и начать новую попытку с нового `R1`.

Проверенный fetched object должен быть источником последующего checkout. Допустимы два варианта: resolver работает в repository, который после проверки становится workspace, либо передаёт проверенный object database/bundle с digest в checkout step. Повторное независимое получение raw SHA из сети оставляет TOCTOU-окно; если оно временно сохраняется, исчезновение объекта или checkout failure обязаны завершить run как `INCOMPLETE`, а это ограничение записывается до устранения.

После checkout plan, analysis и final независимо требуют `HEAD == candidate_sha`; каждый PR job также проверяет exact event base/head parents. Plan публикует identity receipt, analysis принимает только этот SHA/receipt, а final заново сверяет SHA, parents, comparison base и receipt digests. Последующий новый PR event не меняет identity уже запущенного event: старый run может завершиться только для своего head, а новый head получает отдельный run/check.

### Результат и диагностический receipt

Resolver не публикует `skipped`, `neutral`, `OBSERVED` или `NOT_EVALUATED` как успешный required result. Ошибка входа/контракта, `MALFORMED` и доказанное нарушение trust boundary дают failure; отсутствие доказуемой current identity до deadline даёт fail-closed `INCOMPLETE` с ненулевым exit. Final canonical job обязан оставаться failure, если plan не выпустил `STABLE_CURRENT`.

Receipt сохраняется как read-only artifact текущего run и содержит без credentials/URL query secrets:

- schema version, repository, event name/action, PR number, run ID/attempt;
- event base/head, raw payload merge SHA и его явно advisory-отношение к принятому candidate;
- deadline, backoff policy, число попыток, elapsed time;
- для каждой попытки timestamp, state, `R1/F/R2`, object type и parents, если они доказуемы;
- принятый SHA либо конечную причину `INVALID_EVENT`, `MALFORMED`, `TIMEOUT`, `REF_CHANGED`, `PARENT_MISMATCH`, `AUTH_UNSUPPORTED`, `NETWORK_UNAVAILABLE` или `CHECKOUT_UNAVAILABLE`;
- digest resolver source из trusted base и digest самого receipt.

Логи не должны содержать token, authorization header, credential helper output или весь environment. Успешный receipt не является архитектурным PASS и не доказывает repository enforcement.

### DoR инкремента resolver

Работа над кодом resolver начинается только когда:

- зафиксированы comparison base, candidate head и чистый отдельный worktree;
- изменение классифицировано как локальное расширение `quality-governance`, application core не затрагивается;
- сохранены live receipts минимум одного раннего `opened` failure и одного позднего успешного ref для одной event identity;
- определены поддерживаемые repository/auth/object-format профили и все остальные объявлены ограничениями;
- тестовый harness может детерминированно управлять ответами remote/fetch/time без обращения к production data;
- согласованы exact deadline/backoff constants и инъекция monotonic clock/sleep для быстрых tests;
- не требуется merge, deploy, ruleset, collaborator или organization change для локальной реализации и review.

### DoR solo-maintainer enforcement

Переход к solo-профилю начинается только при одновременном выполнении следующих условий:

- владелец явно принял профиль `VERIFIED_WITH_ACCEPTED_RISK`, отсутствие второго reviewer и personal-account ограничения;
- live API подтвердил единственного write/admin actor, exact default branch, текущий trusted workflow и GitHub App identity required check;
- функциональная post-integration матрица T3 завершена без `FAIL`, `INCOMPLETE` или засчитанных skip/neutral результатов;
- заранее определены ruleset target, required context/App, strictness, PR requirement, deletion/non-fast-forward controls и пустой bypass list;
- подготовлены disposable positive/negative PR, команды проверки mergeability и cleanup без merge;
- CODEOWNERS owner существует и имеет доступ; файл регистрируется в T0 и policy-fixture protected sources до интеграции;
- merge control-инкремента, deploy и любые дальнейшие repository settings остаются отдельными действиями в пределах явного разрешения.

DoR не является DoD: наличие плана, прав администратора или созданного ruleset не даёт терминальный статус без live negative probes и post-change readback.

### Обязательная автоматическая матрица

Положительные fixtures:

1. Ref сразу `STABLE_CURRENT`, payload пуст.
2. Ref сразу `STABLE_CURRENT`, payload совпадает.
3. Ref сразу `STABLE_CURRENT`, payload содержит SHA предыдущего test merge; receipt фиксирует `DIFFERENT`, но candidate выводится только из ref/object/parents.
4. `ABSENT → STABLE_CURRENT`.
5. `STALE → STABLE_CURRENT` после одного и нескольких наблюдений.
6. `CHANGING → STABLE_CURRENT`, включая смену между первым read и fetch и между fetch и вторым read.
7. Повторный `synchronize` получает отдельную event identity и не переиспользует receipt предыдущего head.

Отрицательные fixtures:

1. Ref отсутствует до deadline.
2. Ref остаётся stale либо постоянно меняется.
3. Payload malformed или all-zero для SHA-1 профиля; расхождение валидного payload отдельно проверяется как advisory и само по себе не заменяет negative ref/parent verdict.
4. Remote возвращает malformed SHA, неверное имя ref, лишнюю/вторую строку или tag/non-commit object.
5. Commit имеет один, больше двух, переставленные либо неверные base/head parents.
6. Fetch возвращает объект, отличный от `R1`, либо второй read отличается от `F`.
7. Network, DNS, TLS или auth недоступны; private repository не получает неявный anonymous fallback.
8. PR становится conflict/closed, head удалён, base изменён либо event identity больше не появляется до deadline; resolver не переключается на новую identity и не угадывает направление изменения refs.
9. Проверенный ref исчезает до checkout; checkout получает другой HEAD или object unavailable.
10. Candidate пытается влиять через local Git config, hooks, credential helper, askpass, alternates, replace refs, grafts или environment-injected config.
11. Analysis/final получают иной SHA, parents, comparison base, receipt или digest.
12. Timeout, error и отменённый prerequisite не превращаются в `success`, `skipped` или `neutral` canonical check.

Fixtures обязаны исполнять реальную resolver-логику на Linux, а не искать отдельные строки в YAML. Если реализация остаётся inline shell, harness извлекает и запускает exact step body с fake/local remote; предпочтительный вариант — один небольшой trusted resolver source, который напрямую вызывают workflow и tests. Mutation tests отдельно удаляют stable double-read, fetch binding, parent check, sanitized environment и deadline; каждая такая мутация должна быть отвергнута. Receipt mutations отдельно подменяют raw event identity, advisory authority/relation, candidate, parents, checkout и source digest.

### DoD локального control update

Инкремент готов к повторному review, когда одновременно выполнено следующее:

- resolver принимает только `STABLE_CURRENT` и не завершает polling на первом stale SHA;
- проверенный fetched object связан с checkout либо остаточное second-fetch окно явно закрывается failure и записано как временное ограничение;
- вся положительная и отрицательная матрица проходит на Linux без skip;
- существующие workflow/policy/sandbox/provenance suites проходят без ослабления, новых ignores или снижения coverage;
- YAML parse, pinned `actionlint`, Ruff для затронутых Python sources, `git diff --check` и read-only T0 capture успешны;
- exact changed paths остаются классифицированными, `unclassified=0`, provenance обновляется только после стабильного снимка;
- review не содержит открытого P1/P2 по identity/trust path; команды, версии, exit codes, counts, mutations и ограничения записаны;
- PR остаётся Draft/не сливается до отдельного разрешения на интеграцию control path; deploy и ruleset не выполняются.

### Статус повторного review control update 2026-09-13

Commit `35283256f97bc2d22548a0156ec3dbd5df490a3e` реализовал отдельный trusted resolver, verified plan checkout и cross-job receipt, однако последующее review обнаружило P1 в определении `SUPERSEDED`: совпадение стабильного merge parent с текущим head ref не различает старое событие и временно отстающие refs нового `synchronize`. Такой исход преждевременно завершал current event вместо `STALE → STABLE_CURRENT`. В исправлении `3a10f8a92dcd2803f4d41ddb7206b96d579bc940` ранний исход и дополнительный head-ref read удалены; regression fixture требует `STALE → STABLE_CURRENT` и запрещает вывод event supersession из одного ref snapshot. Финальный снимок прошёл полный non-root Linux quality-suite и повторное review без новых P1/P2, поэтому локальный DoD исправления закрыт; commit опубликован в PR #31.

Изменение остаётся локальным расширением `quality-governance`; application core и upstream layout не меняются. Production-path находится в `tools/quality/resolve_pr_merge_ref.py`; resolver поддерживает только public `https://github.com`, anonymous HTTPS и SHA-1, создаёт новый repository без template/config/credentials/object indirections, принимает только `STABLE_CURRENT`, атомарно пишет receipt и не создаёт candidate outputs при failure. Проверенный object непосредственно материализуется в plan checkout; downstream exact-SHA refetch остаётся fail-closed ограничением, а не доказательством отсутствия второго network window.

Фактические проверки локального resolver source digest `c936a473b0e2ef29549c8e88cc5045e1dfe84116755c33dd44f366687de7536b`:

- Windows Python 3.13.12: resolver + workflow `96 passed`; policy + workflow `34 passed`;
- Linux Python 3.13.11 в standalone clone и `infiniflow/ragflow@sha256:16d24d1968ab59e2715a85d2590f1569c9539e0362344a42f3a23e8be06a655b`: resolver + workflow `96 passed`, без skip;
- Ruff 0.15.5 check/format-check, JSON/YAML parse, `git diff --check` и `rhysd/actionlint` 1.7.10 с digest `sha256:ef8299f97635c4c30e2298f48f30763ab782a4ad2c95b744649439a039421e36` завершились с exit 0;
- Windows full quality-suite не засчитан как PASS: `477 passed, 2 skipped, 3 failed` из-за воспроизводимого 15-second timeout системного `C:\Windows\System32\bash.exe` в неизменённых release fixtures; с Git Bash два image-publish cases прошли, но один nested-bash negative case остался timeout. Это host-specific ограничение сохранено, tests/ignores не менялись;
- полный Linux quality-suite в standalone clone, pinned image, Python 3.13.11, UID/GID `1000:1000`, read-only repository и lockfile TypeScript 5.9.3 завершился `482 passed` за `110.63 s`, без skip. Первый bare run (`467 passed, 5 skipped, 10 failed`) доказал отсутствие parser dependency; первый non-root retry (`481 passed, 1 failed`) отдельно выявил `dubious ownership`. Финальный запуск добавил только одноразовый `/workspace` safe-directory в container-local `/tmp` Git config и не изменял тесты, registry, пользовательский Git config или repository;
- после публикации исправления штатный `capture_inventory.py --write` выполнен без network/supporting refresh; сохранённый pre-commit capture для head `3a10f8a92dcd2803f4d41ddb7206b96d579bc940` содержит 751 запись, `unstaged_delta=5` для трёх evidence-документов и двух self-recording registry files, `unclassified=0`. Post-commit HEAD/fingerprint проверяется отдельным read-only запуском.

Read-only local-to-remote probe `S:\ragflow-t3-resolver-live-evidence-v12` на текущем удалённом head PR #31 принял `d47e76ba238ece8be16dc3d8abfc226d23651838` как `STABLE_CURRENT` за одну попытку: `R1 == F == R2`, родители `88c7a44f001705f0e7ab08b3c22f140fc866ba74 35283256f97bc2d22548a0156ec3dbd5df490a3e`, elapsed `46.574706 s`; независимый `verify-receipt` успешен, payload/file digests `8aa28b21175b04697fd163e167e6934c12d3f8ec217bd6237d70e6e8d6ef6a6d` / `66f87228574f16b1a41a32d510373e2e692cb82dec15289fdeae969fdc067012`. Отдельная 8-second проба той же live ref с прежним event head `028c61ff984b1a058106bdada04ea4c4355a988e` дважды зафиксировала `STALE` и завершилась `PARENT_MISMATCH`, `candidate=null`; ложного `SUPERSEDED` больше нет.

Историческая v11-проба старой реализации завершалась `SUPERSEDED / EVENT_SUPERSEDED`; review показало, что это недоказуемая классификация, поэтому она сохраняется только как evidence найденного дефекта, а не как успешная отрицательная приёмка. До интеграции PR #31 runs `34757291101`, `34772641303` и `34775831907` ожидаемо остановились base-owned `MUST_MATCH`; последний одновременно воспроизвёл старую base race. После явного разрешения PR #31 слит в `main` merge-коммитом `7843651969329f34accf537e902fa77c7720a4ff`; source branch сохранена. Push run `34777112223` сравнивал control merge с первым родителем `88c7a44f001705f0e7ab08b3c22f140fc866ba74` и ожидаемо fail-closed остановил изменённые trusted sources, поэтому он не является post-integration приёмкой.

Первое настоящее post-integration событие `opened` проверено disposable Ready PR #32 на base `7843651969329f34accf537e902fa77c7720a4ff` и head `b5841cbfb6e7c37162fc3ca25fe2194df2c02c49`. Plan run `34777290557` при пустом `payload_merge_sha` за одну попытку получил `R1 == fetched == R2 == 7d24b626030517461d81d45de3292474f64b6bf3`, проверил родителей `[7843651969329f34accf537e902fa77c7720a4ff, b5841cbfb6e7c37162fc3ca25fe2194df2c02c49]`, выпустил `STABLE_CURRENT` receipt и завершился успешно. Это закрывает только primary resolver identity case. Analysis корректно не стал зелёным: base-owned fixture run дал `6 failed, 112 passed, 118 subtests passed`, потому что materialized fixture tree не содержал protected dependency `tools/quality/resolve_pr_merge_ref.py`. Final отверг незавершённый analysis. PR #32 закрыт без merge; artifact bundle сохранён в `S:\ragflow-t3-acceptance-evidence\pr32-run34777290557`.

Исправление fixture closure реализовано в clean worktree `S:\ragflow-t3-fixture-closure-fix`, branch `codex/t3-fixture-closure-fix`, commit `2eba9b2c69204da713a26b5acaaa96c6d7f64512`. `_base_fixture_paths` требует exact set `source_bundle == protected_sources`, повторно использует digest-checking materializer и запускает fixture paths из дерева со всей protected dependency closure. Положительный contract доказывает наличие fixture и dependency; отрицательный удаляет dependency из plan и требует fail-closed `ValueError`. Ruff 0.15.5 check/format-check успешны; Windows policy/resolver/workflow subset завершился `130 passed, 1 skipped`, где skip относится к POSIX-only semantics и не считается Linux proof. Полный Linux `test/unit_test/tools/quality` в standalone clone и pinned image `infiniflow/ragflow@sha256:16d24d1968ab59e2715a85d2590f1569c9539e0362344a42f3a23e8be06a655b` использовал Python 3.13.11, UID/GID `1000:1000`, read-only repository/rootfs, `--network none`, `--ipc none`, `--cap-drop ALL` и `no-new-privileges`; итог `483 passed` за `90.82 s`, без skip. Диагностические неполные прогоны с отсутствующими `quart`/`peewee` и без project dependencies не засчитаны; одноразовый Docker volume после проверки удалён.

Точные host-команды принятого candidate snapshot:

```powershell
uvx --from ruff==0.15.5 ruff check tools/quality/check_architecture_policy.py test/unit_test/tools/quality/test_check_architecture_policy.py
uvx --from ruff==0.15.5 ruff format --check tools/quality/check_architecture_policy.py test/unit_test/tools/quality/test_check_architecture_policy.py
& 'S:\ragflow-t3-os-isolation\.venv\Scripts\python.exe' -m pytest --noconftest -p no:cacheprovider -q test/unit_test/tools/quality/test_check_architecture_policy.py test/unit_test/tools/quality/test_architecture_workflow.py test/unit_test/tools/quality/test_resolve_pr_merge_ref.py test/unit_test/tools/quality/test_architecture_sandbox.py
& 'S:\ragflow-t3-os-isolation\.venv\Scripts\python.exe' tools/quality/capture_inventory.py --write
```

Финальный Linux runner запускал тот же commit из read-only standalone clone командой `/t3/venv/bin/python -m pytest --noconftest -p no:cacheprovider -q test/unit_test/tools/quality` внутри контейнера с `--user 1000:1000 --network none --ipc none --cap-drop ALL --security-opt no-new-privileges --read-only`; `/tmp` был отдельным `rw,exec,nosuid,nodev` tmpfs, test venv — read-only volume. Venv содержал Python 3.13.11, pytest 9.0.2, pytest-asyncio 1.3.0, PyYAML 6.0.3 и lock-версии Quart 0.20.0/Peewee 3.19.0; остальные project dependencies читались из pinned image. Это доказывает candidate unit/fixture behavior, но не заменяет base-owned GitHub run после интеграции. Receipt PR #32 имеет `receipt_payload_sha256=07dfa35836ca2e7795efd7440cf5b819706f43dde25c3d5087420ea121ec2ab2` и resolver source digest `c936a473b0e2ef29549c8e88cc5045e1dfe84116755c33dd44f366687de7536b`.

Локальная post-integration имитация на base `54bc3640a4d128b71beea2c19fe37be79915637e` и обычном non-control test delta сначала подтвердила materialization полного 16-file protected closure и выполнение `119` policy cases, но выявила второй fail-closed blocker. При полностью read-only candidate root pytest завершал все тесты точками, затем возвращал exit `1`, потому что не мог создать `.pytest_cache`; при отдельном writable cache mount сами `119 passed in 11.24s`, но post-capture правильно остановился на `Unclassified paths: .pytest_cache/v/cache/nodeids`. Оба результата являются `INCOMPLETE` и не засчитаны как PASS.

Follow-up commit `4ae9c53a0` отключает встроенный `cacheprovider` только в изолированном дочернем pytest и добавляет обязательный case `test_policy_fixture_runner_disables_pytest_cache_writes`. Это не добавляет ignore и не ослабляет post-capture. Windows Python 3.13.12 subset дал `131 passed, 1 skipped` за `94.62 s`; skip относится к POSIX-only test и не является Linux-доказательством. В pinned image `infiniflow/ragflow@sha256:16d24d1968ab59e2715a85d2590f1569c9539e0362344a42f3a23e8be06a655b` с Python 3.13.11, UID/GID `1000:1000`, read-only repository/rootfs, `--network none`, `--ipc none`, `--cap-drop ALL` и NNP целевой suite дал `132 passed in 9.95s`, а полный `test/unit_test/tools/quality` с exact TypeScript 5.9.3 parser — `484 passed in 80.28s`, оба без skip. Диагностический широкий запуск без locked TypeScript parser и с унаследованным Windows `core.autocrlf=true` дал `11 failed, 469 passed, 4 skipped` и не засчитан; повтор с exact parser и без глобального EOL override закрыл все эти инфраструктурные failures.

Следующая имитация на base `625fa83fc079e9224d74443091e371957c817c8f` и ordinary test candidate `a333569b81c4745aadbfe957e4058fc30baf733b` подтвердила отсутствие cache writes: pytest завершил `120 passed in 10.30s`, `pytest_exit_code=0`, post-capture сохранил исходный snapshot. Но runner всё равно вернул `INCOMPLETE`, потому что 15 параметризованных resolver tests записаны в JUnit как `name[param]`, тогда как прежняя проверка ошибочно искала только exact logical `name`; этот результат также не засчитан.

Commit `0296ac478` вводит единый logical-name extractor только для корректно закрытого суффикса `[...]`, требует exact set equality между `required_cases` и logical names всех `required_nodeids`, разрешает несколько параметров одного обязательного case и отклоняет missing, незакрытые и unexpected logical names. Убрано неверное равенство `case_count == len(required_nodeids)`, но raw JUnit digest, полные уникальные case names, exact executed node IDs и case count продолжают аттестоваться. Позитивный и три отрицательных варианта входят в обязательный case `test_parameterized_junit_cases_preserve_exact_logical_inventory`. Windows subset дал `132 passed, 1 skipped in 92.90s`; полный Linux quality-suite в прежней изоляции и с TypeScript 5.9.3 дал `485 passed in 78.16s`, без skip.

После публикации exact PR #33 head `02bbe07842bf2d6e53e34e966dcc604bec75465f` событие `synchronize` запустило run `34797814944`. Event identity содержала текущие base/head, но `payload_merge_sha=2f321e7d8e40b2760b157fb6ea61ed8fc46415a0` от предыдущего test merge; прежний resolver 120 секунд наблюдал `PAYLOAD_MISMATCH` и завершился exit 2. Независимая read/fetch/read-проба после run получила `R1 == F == R2 == 21d15986f4ad58a410662c05fedaa196ca13f206` и exact parents `[7843651969329f34accf537e902fa77c7720a4ff, 02bbe07842bf2d6e53e34e966dcc604bec75465f]`. Следовательно, hard equality с immutable payload не могла стать истинной polling-ом и блокировала корректный current ref. Functional follow-up `7b8bb6c21241453a99169fb61d4263abb72d51df` удаляет `PAYLOAD_MISMATCH` как состояние выбора, повышает receipt schema до v2 и аттестует raw payload как advisory `ABSENT/MATCH/DIFFERENT/UNRESOLVED`; malformed/all-zero payload всё ещё отклоняется до transport, а ref double-read, fetched-object и ordered-parent bindings не ослаблены. Python 3.13.12 focused matrix — `35 passed`; отдельные receipt mutations отклоняют подмену advisory authority и relation. Первый полный Linux запуск не засчитан: временный pytest-venv был связан с pinned site-packages только через очищаемый `PYTHONPATH`, поэтому два runtime-probe tests корректно вернули `INCOMPLETE` (`485 passed, 2 failed`). После добавления read-only `.pth` к тем же site-packages образа ровно эти два теста прошли `2 passed`, а полный повтор на exact SHA завершился `487 passed in 85.80 s`, без skip. Финальный контейнер использовал pinned image `sha256:16d24d1968ab59e2715a85d2590f1569c9539e0362344a42f3a23e8be06a655b`, Python 3.13.11, UID/GID `1000:1000`, read-only repository/rootfs, tmpfs `/tmp`, disabled network/IPC, no capabilities и `no_new_privileges`. Это локальный PASS candidate, но не post-integration GitHub PASS.

### DoD post-integration live-приёмки

Интеграция кода сама не закрывает T3. После отдельного разрешения и появления resolver в trusted `main` новый disposable PR должен доказать:

1. Самое первое событие `opened` с пустым payload проходит без `edited`/manual retrigger и принимает точный synthetic merge commit.
2. `opened` с уже заполненным payload и `ready_for_review`/`reopened` сохраняют ту же identity semantics.
3. Один `synchronize` и серия быстрых push не позволяют старому ref удовлетворить новый event; каждый check привязан к своему exact head SHA.
4. Controlled stale/changing/missing/wrong-parent scenarios завершаются fail-closed; ни один отрицательный run не становится green из-за duplicate context, skip, neutral или отсутствия job.
5. Plan, analysis и final используют один candidate SHA и comparison base; check-run API подтверждает exact head SHA, event, workflow и GitHub App identity.
6. Artifact содержит полный resolver receipt и digests, а job logs не содержат credentials.
7. Диагностические PR закрываются без merge после сохранения run URLs и результатов.

Даже полный PASS этой матрицы доказывает только trigger/identity path. Обычный `VERIFIED` T3 дополнительно требует независимого control-owner approval, защищённого CODEOWNERS/control path, active no-bypass enforcement, duplicate-context/API negative probe и merge-group acceptance в topology, где merge queue доступна. Явно принятый solo-профиль может закрыть зависимость как `VERIFIED_WITH_ACCEPTED_RISK` по отдельному DoD ниже; `enforcement_status=NOT_ENABLED` внутри report-only aggregate при этом не переписывается.

## Ревью и GitHub-приёмка 2026-09-12

Все затронутые checker/policy/workflow/test/docs/provenance пути отсутствуют в принятом upstream commit `cb93883f3f8c975eecb2fed81210effeb3bdb06f` и относятся к локальному расширению `quality-governance`; стандартное application core этими инкрементами не меняется. Перед объединением PR #12 его retarget-diff сохранил те же 29 путей и binary patch hash `6e2cb077f9ae1acc96b81f0cf62e14f299c1bcf3`; полный локальный quality-suite завершился как `392 passed, 2 skipped, 288 subtests passed`. Обе skip относятся к POSIX permission semantics на Windows и потому не засчитаны как приёмка. Exact-head browser run `34698416418` успешно завершил frontend, Chromium, Firefox, WebKit и browser-gate; skipped label lanes не засчитаны. Source branches и пользовательское рабочее дерево сохранены; deploy и GitHub ruleset не изменялись.

| Сценарий | PR / run | Фактический результат |
| --- | --- | --- |
| Разрешённое узкое изменение | #21 / [34691682512](https://github.com/meownm/ragflow/actions/runs/34691682512) | plan, analysis и final успешны; `REPORT_ONLY_COMPLETE / NOT_ENABLED`, base compatibility `PASS`, Python `PASS`, fixtures 28/28, Docker negative controls 12/12 |
| Монотонное расширение policy, все четыре lanes | #22 / [34692464102](https://github.com/meownm/ragflow/actions/runs/34692464102) | base evaluator/policy, `COMPATIBLE`, `BASE_FIXTURE_REVIEW`; Python `PASS`, 19/19 runtime probes и 51/51 tests; fixtures 28/28; OS 12/12; runtime graph только `CLASSIFIED / REPORT_ONLY`, Go только `PLANNED / NOT_EVALUATED` |
| Сужение policy | #23 / [34692465518](https://github.com/meownm/ragflow/actions/runs/34692465518) | plan code 2: удаление `AGENTS.md` из selector отвергнуто; analysis не запущен, final fail-closed |
| Отсутствующий analyzer | #24 / [34692466560](https://github.com/meownm/ragflow/actions/runs/34692466560) | plan code 2: `Stale registry paths: tools/quality/check_architecture.py`; analysis не запущен, final fail-closed |
| Провал trusted fixture | #25 / [34692467687](https://github.com/meownm/ragflow/actions/runs/34692467687) | base fixtures: 1 failed, 27 passed, 0 skipped; analysis и final не стали успешными |
| Запрещённая domain dependency | #26 / [34692469616](https://github.com/meownm/ragflow/actions/runs/34692469616) | Python policy `FAIL`, 18 runtime probes `PASS` и целевая boundary `FAIL`; OS attestation и 12/12 negative controls сохранились, final fail-closed |
| Неклассифицированный core path | #27 / [34692471425](https://github.com/meownm/ragflow/actions/runs/34692471425) | plan code 2: `Unclassified paths: api/t3_unclassified_core_probe.py`; analysis не запущен, final fail-closed |
| Exact tip старого trigger-контракта | #28 / [34693499337](https://github.com/meownm/ragflow/actions/runs/34693499337) | commit `d6025f428dbdf0420bc14228f440a7df1b91f5ef`: plan/analysis/final успешны, Python 19/19, fixtures 28/28, OS 12/12; PR закрыт без merge |
| Первый post-integration `opened`, draft | #29 / [34705977822](https://github.com/meownm/ragflow/actions/runs/34705977822) | plan code 2 до checkout: payload `PR_MERGE_SHA` пуст; analysis не запущен, final fail-closed |
| Повторное trusted событие того же PR | #29 / [34706026252](https://github.com/meownm/ragflow/actions/runs/34706026252) | plan, analysis и final успешны; exact merge ref `0375459926b7942f513848d172f73486b9391759`, base compatibility `PASS`, Python `PASS`, 19/19 runtime probes, fixtures 30/30, OS controls 12/12; runtime graph и Go plan корректно `NOT_APPLICABLE` |
| Первый post-integration `opened`, Ready | #30 / [34706264276](https://github.com/meownm/ragflow/actions/runs/34706264276) | тот же пустой `PR_MERGE_SHA` и plan code 2; подтверждено, что гонка не зависит от draft-состояния |
| Интеграция resolver control update | #31 / [34777112223](https://github.com/meownm/ragflow/actions/runs/34777112223) | PR слит exact merge-коммитом `7843651969329f34accf537e902fa77c7720a4ff`; push run ожидаемо заблокировал delta к старому first parent по `MUST_MATCH` и не засчитан как acceptance |
| Первый `opened` после resolver integration | #32 / [34777290557](https://github.com/meownm/ragflow/actions/runs/34777290557) | plan успешно разрешил пустой payload в exact merge `7d24b626…` и проверил родителей; fixtures затем честно выявили неполную materialization dependency closure: 6 failed, 112 passed, 118 subtests; final fail-closed, PR закрыт без merge |
| Первый `synchronize` обновлённого PR #33 | #33 / [34797814944](https://github.com/meownm/ragflow/actions/runs/34797814944) | base/head в event актуальны, но payload сохранил предыдущий test merge `2f321e7d…`; plan через 120 s завершился `PAYLOAD_MISMATCH`, analysis не запущен, final fail-closed. Независимый stable ref `21d15986…` имел exact текущие parents; результат выявил новый blocker и не засчитан |

Первые восемь приёмочных PR закрыты без merge; диагностические PR #29 и #30 остаются не предназначенными для merge. PR #31 интегрирован только после явного разрешения, а PR #32 закрыт без merge после сохранения evidence. Results `CLASSIFIED`, `PLANNED`, `OBSERVED` и `NOT_EVALUATED` не засчитаны как `PASS`; полный Go native build, широкий dead-code verdict и поведение исторических saved DSL этими T3 runs не доказаны. Run #32 доказал primary `opened` resolver identity, но одновременно обнаружил отдельный fixture-harness regression. Run #33 `34797814944` затем доказал, что event payload merge SHA нельзя использовать как current-authority на `synchronize`; его fail-closed результат также не является приёмкой. Весь post-integration gate остаётся незавершённым.

## Enforcement-preflight 2026-09-12 (исторический снимок)

Read-only GitHub API подтвердил фактический контекст: `meownm/ragflow` — public repository владельца типа `User`; единственный collaborator `meownm` имеет admin, rulesets отсутствуют, `main` не защищён, workflow `architecture-policy` активен с id `354200183`. Файл `CODEOWNERS` отсутствует. GitHub разрешает repository rulesets для public personal repositories, но правило [Require workflows to pass before merging](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-workflows-to-pass-before-merging) настраивается только на уровне organization/enterprise. [Merge queue](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/merging-a-pull-request-with-a-merge-queue#who-can-use-this-feature) также доступна только organization-owned repositories. Поэтому Required Workflow и реальную `merge_group` acceptance невозможно включить в текущем personal-account topology.

Отдельный read-only снимок Actions settings показывает `default_workflow_permissions=read`, `can_approve_pull_request_reviews=false`, `enabled=true`, `allowed_actions=all` и `sha_pinning_required=false`; endpoint selected actions закономерно возвращает `409`, потому что действует режим `all`. Canonical architecture workflow сам ограничивает token до `contents: read` и закрепляет собственные Actions SHA, но repository settings не навязывают SHA pinning остальным workflows. Restricted default также не является максимальным permission ceiling: пользователь с write-доступом может изменить `permissions` в workflow. Поэтому этот снимок не закрывает candidate/API spoof и не заменяет соответствующую live negative probe.

У аккаунта есть активное членство с ролью `member`, не `owner`, в организации `Hypothesis-Lab`; read-only API показывает план `free` и разрешение участникам создавать public/private repositories. Это делает [перенос repository](https://docs.github.com/en/enterprise-cloud@latest/repositories/creating-and-managing-repositories/transferring-a-repository#repository-transfers-and-organizations) технически возможным после отдельного разрешения пользователя и проверки transfer warnings. Public organization-owned repository на Free получает repository ruleset и merge queue, но [organization-level rulesets доступны только GitHub Team/Enterprise](https://docs.github.com/en/enterprise-cloud@latest/organizations/managing-organization-settings/creating-rulesets-for-repositories-in-your-organization#introduction); следовательно, `Hypothesis-Lab` на текущем Free-плане не даёт Required Workflow. Нужны либо перенос плюс согласованное повышение плана/действие organization owner, либо доказанный эквивалент с отдельным pinned GitHub App. Ни перенос, ни изменение плана, ни ruleset здесь не выполнялись.

Обычный required status check можно создать в repository ruleset, но он фиксирует context/app, а не trusted workflow contents: GitHub [не учитывает workflow, matrix и event trigger types](https://docs.github.com/en/enterprise-cloud@latest/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/troubleshooting-rules#troubleshooting-required-status-checks). Интегрированные control commits используют [pull_request_target](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target), immutable PR merge SHA и trusted fixture selector для всего `.github/workflows/`; единственным статическим effective job name `architecture-policy` остаётся final job канонического workflow, а прямой duplicate и динамическое конструирование имени входят в отрицательный fixture. Live-пробы выявили две payload-границы: на `opened` GitHub передавал пустой `pull_request.merge_commit_sha`, а на `synchronize` — SHA предыдущего test merge, хотя current synthetic ref уже имел exact event parents. Исправляющий candidate получает SHA из доверенного base-repository ref только после stable `read/fetch/read` и exact parent binding, сохраняет payload как signed advisory observation, материализует проверенный object в plan checkout и повторяет identity/receipt checks в downstream jobs. Это не доказывает невозможность создания одноимённого check через другой GitHub App/API path; всё ещё нужен live negative duplicate-context probe и затем отдельный pinned App либо organization Required Workflow.

`check_architecture_policy.py` намеренно не может сам подтвердить repository enforcement и оставляет `enforcement_status=NOT_ENABLED`: редактируемый candidate-флаг был бы ложным доказательством внешней настройки. Финальная приёмка T3 должна сохранить отдельный read-only receipt с identity активного ruleset/Required Workflow или pinned App, точными PR/merge-group SHA, check run/app identity, review decision и результатами всех отрицательных проб. Только после сверки receipt с live API документация этапа может получить `VERIFIED` либо явно ограниченный `VERIFIED_WITH_ACCEPTED_RISK`; одна зелёная job или вручную изменённое поле отчёта недостаточны.

Технически обязательное approval также сейчас невозможно проверить: автор PR и единственный eligible reviewer — один аккаунт, а [автор не может одобрить собственный PR](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/reviewing-proposed-changes-in-a-pull-request#submitting-your-review). До ruleset требуется указанный пользователем второй trusted collaborator либо organization team, затем добавить и защитить `.github/CODEOWNERS`; имя владельца не выдумывается.

Локальная проверка интегрированного enforcement-hardening: `test_architecture_workflow.py` — 8/8, включая новые отрицательные мутации против payload-only SHA и отсутствующей parent binding; расширенный workflow/policy/sandbox subset — 41 passed, 1 Windows-only POSIX skip, 122 subtests; полный `test/unit_test/tools/quality` после установки физического lockfile-окружения — 392 passed, 2 Windows-only POSIX skips за 285.38 s. Первый полный запуск без TypeScript parser дал 10 failures и 6 skips и не засчитан; временная junction отдельно была отвергнута `safe_path`, после чего отказавшиеся тесты прошли 24/24 на собственном `pnpm --frozen-lockfile --ignore-scripts` tree. YAML parse, `git diff --check`, Ruff check 0.16.7 и scoped `rhysd/actionlint` 1.7.10 из image digest `sha256:ef8299f97635c4c30e2298f48f30763ab782a4ad2c95b744649439a039421e36` успешны. Ручная live-симуляция для PR #30 разрешила `refs/pull/30/merge` в `8db838cb1df53beab50c8d4ecbe7fd4aea1743a8` и подтвердила точные родители `88c7a44f001705f0e7ab08b3c22f140fc866ba74 f138a6c0021b70b9328b1ff8fe8624cbbe948719`. Форматирование Ruff 0.16.7 не применялось: эта неприкреплённая версия требует массового форматирования существующего тестового файла и не является допустимым focused increment. Linux live run исправленного primary trigger возможен только после его отдельной интеграции в trusted `main`.

Дополнительная exact YAML-body проверка из пустого Linux-каталога с пустым payload SHA разрешила PR #30 в `8db838cb1df53beab50c8d4ecbe7fd4aea1743a8`; malformed payload `candidate-branch` был отклонён до network access с exit 2. Попытка использовать внешнюю junction для TypeScript parser также завершилась ожидаемым `safe_path` failure и не стала обходом provenance boundary.

## Solo-maintainer enforcement receipt 2026-09-14

Владелец явно выбрал personal-repository solo-профиль и принял терминальный статус `VERIFIED_WITH_ACCEPTED_RISK`. Live API подтвердил единственного collaborator/admin `meownm`. В `main` включён active repository ruleset [main-solo-maintainer-gate-v1](https://github.com/meownm/ragflow/rules/23306934), id `23306934`, target `refs/heads/main`, `bypass_actors=[]`, `current_user_can_bypass=never`. Ruleset требует pull request, strict актуальность ветки и status context `architecture-policy`, связанный с GitHub Actions integration id `15368`; deletion и non-fast-forward запрещены. Поскольку автор не может одобрить собственный PR и второго reviewer нет, `required_approving_review_count=0` и `require_code_owner_review=false`. `.github/CODEOWNERS` назначает `* @meownm` как ownership metadata и protected policy source, но не изображает независимое approval.

Функциональный prerequisite доказан draft PR #35 без его merge. Stale schema-v3 run `34834573688` завершился `PARENT_MISMATCH`, не выпустил candidate и не был засчитан. Fresh runs `34835069719`, `34836369666` и body-edit run `34837242457` завершили plan/analysis/canonical final успешно; exact-head check-run создан GitHub Actions App `15368`. PR #35 остаётся draft, `BEHIND`, `merged=false` и не входит в control increment.

Live enforcement-пробы выполнены после активации ruleset:

| Сценарий | Evidence | Ожидаемый и фактический verdict |
| --- | --- | --- |
| Новый неклассифицированный path | PR #38, run `34849275993` | plan `INCOMPLETE`, canonical failure, PR `BLOCKED` |
| Разрешённый зарегистрированный path без approval | PR #38, run `34849465366`, check `103993893027` | canonical success от App `15368`; required gate удовлетворён при approvals `0`; optional browser не входит в ruleset |
| Новый head после старого green | PR #38, head `313353b0650b5bd7710ec22d04605b1285165270` | старый success не принят для нового head; PR `BLOCKED` |
| Отмена analysis | PR #38, run `34849779431` | analysis `cancelled`, canonical final `failure`, PR `BLOCKED` |
| Удаление candidate workflow | PR #39, run `34850208159` | base-owned workflow всё равно запущен; stale registry path, PR `BLOCKED` |
| User Commit Status с тем же context | PR #39, status `54118450620` | creator `meownm`/User не удовлетворил App-bound rule; PR `BLOCKED` |
| Поздний same-name/same-App green после trusted failure | PR #39, trusted run `34850423306`, duplicate `34850423467` | поздний duplicate success не перекрыл canonical failure; PR `BLOCKED` |
| `skipped` duplicate | PR #39, trusted run `34850677558`, duplicate `34850681025` | skipped не стал успехом; PR `BLOCKED` |

Основные команды receipt, выполняемые с явным `--repo meownm/ragflow` либо exact REST path:

```powershell
gh api repos/meownm/ragflow/rulesets/23306934
gh api repos/meownm/ragflow/rules/branches/main
gh api repos/meownm/ragflow/pulls/35
gh api repos/meownm/ragflow/pulls/38
gh api repos/meownm/ragflow/pulls/39
gh run view 34849465366 --repo meownm/ragflow --json databaseId,event,headSha,status,conclusion,jobs,url
gh run view 34849779431 --repo meownm/ragflow --json databaseId,event,headSha,status,conclusion,jobs,url
gh run view 34850208159 --repo meownm/ragflow --json databaseId,event,headSha,status,conclusion,jobs,url
gh run view 34850423306 --repo meownm/ragflow --json databaseId,event,headSha,status,conclusion,jobs,url
gh run view 34850423467 --repo meownm/ragflow --json databaseId,event,headSha,status,conclusion,jobs,url
gh api repos/meownm/ragflow/commits/313353b0650b5bd7710ec22d04605b1285165270/check-runs
gh api repos/meownm/ragflow/commits/87634f17e935231a76c53c383f8d6f0947550174/check-runs
gh api repos/meownm/ragflow/commits/87634f17e935231a76c53c383f8d6f0947550174/status
```

Ruleset создан отдельным `POST repos/meownm/ragflow/rulesets` с payload, эквивалентным полям текущего readback; после создания решение принималось только по повторному `GET`, а не по exit code записи. Для cancellation использован `gh run cancel 34849779431 --repo meownm/ragflow`; терминальный `cancelled` analysis и `failure` canonical final проверены отдельным `gh run view`.

PR #38 и #39 закрыты без merge; `merged_at=null`. Отмена optional browser jobs при cleanup не засчитана как положительное доказательство и не влияет на required gate. Deploy не выполнялся. Пользовательское dirty worktree `S:\ragflow` не изменялось; проверки и control change выполнялись в отдельных worktree.

### DoD `VERIFIED_WITH_ACCEPTED_RISK`

T3 получает этот статус только после выполнения всех пунктов:

1. Control-код, functional acceptance и exact negative fixtures находятся в trusted `main`; применимые автоматические lanes не имеют `FAIL`/`INCOMPLETE`, а `OBSERVED`, `CLASSIFIED`, `PLANNED` и `NOT_EVALUATED` не названы PASS.
2. Active ruleset после изменения прочитан через API и точно соответствует target, PR/deletion/non-fast-forward rules, strict App-bound check и пустому bypass list.
3. CODEOWNERS находится в `main`, назначает существующего owner и сам входит в protected selector/source bundle.
4. Positive PR доказывает, что exact fresh head становится mergeable только после canonical success; старый success не переносится на новый head.
5. Negative PR доказывает fail-closed для failure, cancellation, control removal, user status spoof, позднего duplicate success и skipped duplicate; все disposable PR закрыты без merge.
6. Сохранены ruleset id, run/check/status ids, exact SHA, mergeability verdicts, команды, ограничения и cleanup result; PR #35 не слит, deploy отсутствует.
7. Владелец явно принимает перечисленные ниже риски, а документация не сокращает статус до `VERIFIED`.

После интеграции настоящего CODEOWNERS/receipt-инкремента и повторного live readback пункты 1–7 закрывают T3 только в solo-профиле. До этого статус остаётся `IN_PROGRESS`.

### Принятые риски и границы

- Независимого reviewer/control owner нет; CODEOWNERS не создаёт независимое approval, а semantic/manual review выполняет тот же владелец.
- Единственный repository admin технически может изменить или удалить ruleset. Пустой bypass list запрещает bypass merge, но не делает governance неизменяемым для администратора.
- Personal-account repository не предоставляет organization Required Workflow и merge queue; `merge_group` не исполняется и не получает PASS. Эта недоступность принимается только для solo-профиля.
- Required status check привязан к context и App, а не криптографически к одному workflow definition. Live same-App duplicate negative уменьшает риск, но не эквивалентен organization-owned immutable Required Workflow.
- Aggregate `REPORT_ONLY_COMPLETE / NOT_ENABLED` не переписывается вручную: external receipt доказывает enforcement отдельно и не расширяет T2 до полного architecture/dead-code/native-build verdict.
- Исторические saved DSL, неохваченные adapters, полный Go native build и существующие T2 `INCOMPLETE`/`NOT_EVALUATED` остаются за пределами T3 gate и не объявляются успешными.

### Условия обязательного пересмотра

Повторная T3-приёмка обязательна при изменении write/admin/collaborator состава, owner в CODEOWNERS, default branch, ruleset/enforcement/bypass, required context или App id, architecture workflow/checker/policy/protected sources, GitHub semantics/plan/topology, появлении доступного Required Workflow/merge queue, а также перед заявлением обычного `VERIFIED`. В этих случаях downstream T4–T7 сохраняют унаследованный risk receipt до успешного пересмотра.

До полного широкого verdict запрещено создавать пустой `baseline.json`: действующие exact classifications остаются в policy files и не превращаются в разрешения.
