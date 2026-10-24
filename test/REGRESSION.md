# Regression lanes

Run each lane separately: the live API conftest selects priority markers and
must not change the selection of unit tests or P0 document requirements.

Before selecting lanes, use the task's acceptance outcomes from
[AGENTS.md](../AGENTS.md#task-scope-and-ownership). In the existing task/PR report,
associate each outcome with an existing check, a necessary new check or an
explicit acceptance gap; a test count alone does not establish completeness.
Check fixture expectations against the original requirement or authoritative
schema/template, rather than deriving both input and expected output from the
implementation. Preserve the required content and ordering in representative
fixtures: a one-section document cannot prove that a complete template survives
editing or history navigation. Reuse existing traceability and evidence files;
small changes need no separate matrix. If requirements or tested source change,
reassess the affected checks before reusing their results.

| Lane | Command from repository root | Boundary |
| --- | --- | --- |
| On-demand security | See [Trivy, Syft, Gitleaks and Grype commands](../tools/security/README.md) | Explicit request only; source/image vulnerability and configuration scans, SBOM, redacted file/history secret checks. Missing tools/databases and partial scans are INCOMPLETE; no runtime or authorization acceptance |
| Python unit | `python run_tests.py -i` | In-process dependencies and fixtures |
| Technical Specifications requirements | `python -B -m pytest -q test/unit_test/technical_specifications test/unit_test/document_exchange test/evals/technical_specifications/test_live_quality.py` | TZ-01–TZ-40 in `api/technical_specifications/REQUIREMENTS.md` and `test/fixtures/technical_specifications/requirements_traceability.md`: format/limits, real anonymous auth functions, role/tenant isolation, drafts, chat leases/replay, comment identity, no-op revisions, shared Eva import/binding/publish and failure preservation. Disposable SQLite and controlled model/Eva; browser, PostgreSQL and real-LLM evidence remain separate lanes |
| Resource names | `python -m pytest test/unit_test/test_resource_names.py test/unit_test/test_team_resource_visibility.py` | Owner/category namespaces, private-name non-disclosure, validation and team/session access; disposable SQLite |
| Resource-name races | `python -m pytest test/integration/test_resource_name_postgres.py` | Requires loopback-only disposable `RESOURCE_NAME_TEST_POSTGRES_DSN`; temporary schemas, actual indexes, separate concurrent connections and bounded dataset suffix retries; no shared application data |
| Document exchange races | `python -m pytest test/integration/test_document_exchange_postgres.py` | Requires loopback-only disposable `DOCUMENT_EXCHANGE_TEST_POSTGRES_DSN`; temporary schemas and separate concurrent connections on the runtime pooled/retrying driver verify one active Eva page owner across BT and TZ, one import claim per request, atomic cancellation versus completion, rollback after connection loss inside the import transaction, reconnect outside a transaction, and TZ no-op preservation with full/compact proposals followed by a real revision; no shared application data |
| Document quality migrations and races | `python -B -m pytest test/integration/test_document_quality_postgres.py` | Requires `DOCUMENT_QUALITY_TEST_DISPOSABLE=1` and loopback-only `DOCUMENT_QUALITY_TEST_POSTGRES_DSN`; random schemas and separate pooled connections verify atomic/repeatable product migration, repair after interrupted backfill, BT/TZ history isolation, stale reclassifier versus committed PASS/new generation/baseline, and schema cleanup; no model or shared application data |
| Installed document quality acceptance | `DOCUMENT_QUALITY_LIVE=1 python -B -m pytest test/integration/test_document_quality_live.py` | Read-only loopback runtime; dedicated QA admin/reader credentials in environment, 401/403, BT/TZ isolation, 1/7/30-day API and real Chromium desktop/mobile without intercepted APIs. Requires `DOCUMENT_QUALITY_LIVE_EVIDENCE`; does not enqueue inference or change documents |
| Independent night watchdog | `python -B -m pytest test/unit_test/tools/quality/test_document_quality_night_monitor.py` | Real loopback HTTP, no backend or credentials: both products, exact due slot across 02:15 Moscow grace, overdue even on a server OK, stale/future timestamps, auth failures, wrong product, duplicate fields, bounded body and redirect rejection. Run `tools/quality/check_document_quality_night.py` as a separate process with the pure `common/document_quality_schedule.py` policy and an admin token environment variable; unit success does not prove an external monitor is installed |
| Alignment nightly and holdout contracts | `python -B -m pytest test/unit_test/technical_specifications/test_alignment_nightly.py test/unit_test/technical_specifications/test_alignment_holdout.py test/unit_test/technical_specifications/test_alignment_experiments.py test/unit_test/admin/test_technical_specification_quality.py` | Synthetic contracts only: ordered bounded series, retained debt, independent annotations excluded from model inputs, post-inference scoring, no qualification from incomplete reports or unverified experiments. NIGHTLY/MONTHLY collect REPEATS, ADMISSION, REASONING, then FULL; actual scheduled execution remains a separate acceptance gate |
| Alignment operational observations | `python -B -m pytest test/unit_test/technical_specifications/test_alignment_telemetry.py test/unit_test/technical_specifications/test_alignment_transport.py test/unit_test/admin/test_technical_specification_operations.py test/unit_test/admin/test_document_quality_routes.py` | Durable reservation before dispatch, document/observation atomicity, cancellation and expiry debt, idempotent replay, admin-only exact 1/7/30-day cohorts, full-cohort p95 and unknown usage. The disposable PostgreSQL quality lane additionally verifies server-error rollback and a repeatable snapshot under concurrent completion; no production data or inference |
| Business Documents load harness | `python -B -m pytest -q test/load/test_business_documents.py` | 50 distinct simulated HTTP users, OpenAI-compatible LLM emulator, current intake response contract, failure/timeout/cleanup controls; fake API, not application or GPU capacity evidence. See `test/load/README.md` for the opt-in isolated API run with an estimated single-H200 Qwen3.8-27B profile |
| Standalone route units | `python -m pytest -o "pythonpath=. test/testcases" --confcutdir=test/testcases/restful_api test/testcases/restful_api/test_user_tenant_routes_unit.py test/testcases/restful_api/test_connector_routes_unit.py` | Only these reviewed in-process modules; excludes the parent live-model setup, keeps local fixtures and all test cases |
| Browser result assertions | `python -m pytest test/unit_test/playwright test/unit_test/api/apps/services/test_search_consistency_assertions.py` | Reject missing requests, empty/error/truncated answers, and invalid search results |
| Document coverage | `python -m pytest test/unit_test/business_documents test/unit_test/api/apps/business_documents test/unit_test/api/apps/restful_apis/test_business_document_api_contract.py --cov=api/apps/business_documents --cov=business_documents --cov-branch --cov-fail-under=79` | Pure domain/application, worker, evidence, export, authorization and API contracts across both Business Documents source roots; combined statement/branch coverage |
| Document requirements | `python -m pytest test/evals/business_documents/test_golden_dialogue_harness.py test/evals/business_documents/test_live_quality_scorer.py` | All 24 scripted golden cases and deterministic scorer checks |
| Document model quality | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <empty-QA-dir> --evidence-dir <outside-source> --business-documents-quality --skip-browser-tests` | Dedicated disposable tenant and project, project Ollama proxy, real LLM, controlled retrieval and SQLite; separate `business-documents-quality.json` report; not live search, browser or tenant-authorization evidence |
| Technical Specifications model quality | `python -B test/evals/technical_specifications/run_live.py --evidence-dir <new-outside-source-dir>` | Existing local application runtime and dedicated QA Ollama profile; frozen 47-case corpus over three specifications: 45 model scenarios, two context guards, 57 fresh upstream calls with proxy metadata and actual thinking/format/temperature/context/output-budget checks. Checks scope, history/proposal isolation, units, authorization requirements, cancellation, idempotency, data instructions and both merge choices; no document writes or browser/deployment acceptance. See `test/evals/technical_specifications/README.md` |
| Source Workbench retrieval scorer | `python -m pytest test/evals/source_workbench/test_evaluate.py test/evals/source_workbench/test_live_provisioning.py` | Offline schema, ranking metrics and disposable fixture checks; no assertion about live retrieval quality |
| BT/TZ alignment | `python -B -m pytest test/unit_test/technical_specifications/test_alignment.py test/unit_test/technical_specifications/test_alignment_application.py test/unit_test/technical_specifications/test_alignment_transport.py test/unit_test/technical_specifications/test_alignment_http.py test/unit_test/technical_specifications/test_alignment_evaluator.py test/unit_test/technical_specifications/test_alignment_proxy_evidence.py` | Disposable persistence and loopback HTTP; complete inventories, independent coverage/consistency, source ACL/freshness, shared token/call/time budgets, incomplete/truncated output and provider evidence. Real inference: `python -B test/evals/technical_specifications/alignment/run_live.py --evidence-dir <new-outside-source-dir>`. See `test/evals/technical_specifications/alignment/README.md`; unverified backend context cannot qualify |
| Source Workbench retrieval quality | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <built-SPA> --evidence-dir <outside-source> test/playwright/e2e/test_source_workbench_quality_live.py` | Opt-in disposable stack; uploads exactly four golden documents, waits for indexing, calls real workspace search, writes `source-workbench-quality.json`, then removes its dataset and the runner removes its project |
| PostgreSQL and export-storage races | `python -m pytest test/integration/test_business_document_postgres.py` | Requires disposable `BUSINESS_DOCUMENT_TEST_POSTGRES_DSN` plus `BUSINESS_DOCUMENT_TEST_MINIO_ENDPOINT`, `BUSINESS_DOCUMENT_TEST_MINIO_USER`, and `BUSINESS_DOCUMENT_TEST_MINIO_PASSWORD`; separate DB connections, temporary schema, real MinIO `PUT` interruption and verified reconciliation |
| Previous-release data | `python -m pytest test/integration/test_previous_release_data_upgrade.py` | Explicit disposable PostgreSQL cluster; real v1.12.0/current initializers, dump/restore, repeated initialization and injected DDL failure; no deployment-script acceptance |
| Blue/green HTTP update | `RAGFLOW_TEST_DISPOSABLE=1 python -B deployment/runner/test-blue-green.py --image <immutable-candidate-digest> --output <outside-source-dir>` | Own Compose project, random loopback port, synthetic HTTP/worker based on candidate image and real nginx; concurrent requests, SSE drain, old lazy assets, rejected readiness, post-switch rollback and interrupted recovery. No shared DB or live business/LLM acceptance; source/image-bound report required by release collector and publisher |
| Nginx profiles and HTTPS | `python -B -m pytest test/integration/test_nginx_configuration.py` | Requires `RAGFLOW_NGINX_TEST_IMAGE=<immutable-local-image-id-or-digest>`; disposable containers without network or published ports, current source configs and startup selector, Python/Go/hybrid syntax, read-only HTTPS mount preserved across startup selections, self-signed local TLS, gzip, static 404 and SPA fallback. No application/DB/model acceptance |
| MinIO backup/restore | `python -m pytest test/integration/test_minio_backup_restore.py` | Requires `RAGFLOW_MINIO_BACKUP_TEST=1`; creates its own containers/volumes, archives raw `/data`, restores and compares object bytes/metadata, verifies cleanup |
| Sandbox image health | `python -m pytest test/integration/test_sandbox_image_healthcheck.py` | Requires `RAGFLOW_SANDBOX_IMAGE_TEST=1` and `RAGFLOW_SANDBOX_TEST_IMAGE`; checks Compose and Linux install/upgrade healthchecks against the real manager, HTTP 503 and a closed port in disposable containers without network or Docker socket; verifies image overrides in both Compose profiles |
| Coordinated PostgreSQL+MinIO restore | `$env:RAGFLOW_COORDINATED_BACKUP_TEST='1'; uv run pytest -q test/integration/test_postgres_minio_coordinated_restore.py` | Creates source and restore containers/volumes, freezes writes, restores a linked manifest/object pair, runs a missing-object negative control and verifies cleanup |
| Local Docker EVA/OpenMetadata | Copy `test/integration/test_local_docker_connectors_live.py` into the application container, then run it there with `RAGFLOW_LOCAL_DOCKER_CONNECTOR_TEST=1` | Uses existing non-production connector rows without serializing credentials; real read/write/retry/recovery and cleanup |
| Real T-One inference and cancel | From `services/asr-online-service`: `python tests/integration/test_tone_real_inference.py` | Requires `ASR_REAL_TONE_AUDIO` and `ASR_REAL_TONE_LONG_AUDIO`, installed Tone runtime and FFmpeg; actual endpoint/worker/inference with a predeclared transcript and mid-inference cancellation, no inference mocks |
| Cohere/Jina reranker interoperability | `python -m pytest test/integration/test_reranker_contract.py` | Set `RERANKER_SERVICE_TEST_ROOT` to the sibling `reranker_service` source and `RERANKER_SERVICE_TEST_PYTHON` to its test Python; starts and stops an isolated loopback server, exercises real RAGFlow HTTP clients and Cohere SDK against current service routes with deterministic stub inference; no saved settings or shared services are changed |
| MRZ real image | Copy `test/integration/test_mrz_document_reader_live.py` and a generated PNG into the application container, then run with `RAGFLOW_MRZ_TEST_IMAGE=<container-path>` | Executes the deployed Canvas template with the tenant's existing Image2Text setting and deterministic checksum validator; does not change a model or prompt |
| Saved Canvas DSL | Copy `test/integration/test_saved_canvas_dsl_live.py` into the application container, then run with `RAGFLOW_SAVED_DSL_TEST=1` | Synthetic current/two-version roundtrip with cleanup plus read-only audit of current and attached historical records; reports failure classes without provider secrets |
| Frontend | `cd web` then `pnpm test --runInBand` | Jest; measured global floors for statements, lines, functions and branches |
| Local browser server | `python -m pytest test/unit_test/playwright/test_browser_server.py` | Actual HTTP server, connection reuse, SPA fallback and unmocked API rejection; no browser or backend |
| Isolated browser | `python test/run_browser_regression.py --browser chromium` | Built SPA, intercepted API, no live backend; also accepts `firefox` and `webkit` |
| SQL workspace browser | `python test/run_browser_regression.py --browser chromium --suite test/playwright/e2e/test_sql_workspace_ui.py --suite test/playwright/e2e/test_sql_connection_ui.py` | Real ER/workbench on built SPA; actual graph projector, controlled API and model responses. Connection selection/reload, late save, revoked profile, reader denial and immutable result source. Additional candidate, dialogue, race, personal-view continuity and cancellation suites are listed in `test/evals/sql_projects/README.md`; repeat on Firefox/WebKit. Does not prove live API/worker/model integration |
| SQL project goldens | `python -B -m pytest -q test/evals/sql_projects test/integration/test_sql_projects_postgres.py test/integration/test_sql_result_retention_postgres.py` | 80 authored single/multi-turn cases and strict scorer controls; actual SQL execution needs `SQL_PROJECT_TEST_DISPOSABLE=1` plus its own loopback `SQL_PROJECT_TEST_POSTGRES_DSN`. Two independent datasets, exact rows/types/NULL/decimals/order and production guard. Real metadata retention checks cascade, late publication, tenant scope and atomic rollback; disposable databases are removed. Real model qualification is a separate incomplete lane; see `test/evals/sql_projects/README.md` |
| Live browser | `python -m pytest test/playwright/e2e/test_dataset_upload_parse.py test/playwright/e2e/test_next_apps_chat.py test/playwright/e2e/test_next_apps_search.py` | Disposable complete stack, configured model provider and `RAGFLOW_BASE_URL` |
| Disposable live stack | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <built-SPA> --frontend-archive <matching-archive-with-receipt> --evidence-dir <outside-source>` | Creates its own PostgreSQL/Redis/MinIO/Elasticsearch/app stack and synthetic admin; verifies frontend provenance, real ingestion, search, chat, document intake and resource cleanup |
| Persistent Ubuntu QA smoke | On `192.168.1.175`, from `/opt/ragflow-qa`: `docker compose --env-file .env -f compose.yml exec -T app /ragflow/.venv/bin/python - < smoke.py` | Own project and volumes survive restarts; the test creates and removes only its dataset, checks real indexing and `/retrieval`; source image SHA is in `setup.json` and may differ from the current checkout |
| Web/admin API | `python -m pytest test/testcases/test_web_api` and separately `python -m pytest test/testcases/test_admin_api` | Disposable stack; SDK installed, provider configuration and mapped admin endpoint |
| Go | `bash test/run_go_regression.sh` | Linux CI after native build; all packages, disposable MinIO and tokenizer dictionaries |

On Windows, use `.venv/Scripts/python.exe` for `python`. Before running local
lanes, reserve a disposable task and redirect generated evidence/cache outside
the reviewed source (the examples below run from the repository root):

```powershell
$regressionTask = .\deployment\local\temp.ps1 -Name local-regression
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTEST_ADDOPTS = "$env:PYTEST_ADDOPTS -o cache_dir=$regressionTask/pytest-cache".Trim()
$env:PW_ARTIFACTS_DIR = Join-Path $regressionTask 'browser-evidence'
```

Use a dedicated PowerShell session so these environment overrides end with the
session. Store each browser's evidence in its own child of `$regressionTask`.
Build the SPA in a managed disposable workspace using the frontend artifact
process in [the local guide](../deployment/local/README.md), then pass its
extracted build directory to `test/run_browser_regression.py --dist-root <built-SPA>`.
An existing verified matching build may be reused. Install the selected browser
with `python -m playwright install chromium` (or Firefox/WebKit) when needed.

Browser journeys execute all their steps in one test and use independent page
and state fixtures. Missing prerequisites fail rather than count as skipped
steps. Unhandled page exceptions fail the test. Failure evidence is written
under `PW_ARTIFACTS_DIR` (CI configures its own temporary evidence directory).
Before completing the task, preserve unique results under `.local/archives` and
verified backups under `.local/backups`, then run
`deployment/local/temp.ps1 -Name local-regression -Complete`. Preserve incomplete
diagnostics until they have been reviewed; do not pin completed workspaces.

The isolated browser lane covers document role controls and conflict handling,
navigation permissions, wrong password, HTTP/envelope session expiry, logout,
empty upload selection, removal/cancel, rejection and retry. Its upload 400/413
responses are mocked: these prove frontend behavior, not backend file parsing
or size enforcement. Cancel is dismissal before submission, not an in-flight
abort. Mocked roles do not replace server authorization tests.

The live search journey inserts known content and requires its document and
snippet in both the response and UI, then removes its document. The chat
journey requires a correlated completed request, successful stream events,
nonempty answer and a newly rendered assistant message.

Never run live suites against shared customer data. Existing live API fixtures
can delete tenant datasets/chats. CI uses disposable Compose projects; the
PostgreSQL and MinIO race/storage lanes isolate and clean their own data.
The Business Documents race lane fails closed when its PostgreSQL DSN is set
without all three MinIO settings. CI starts a disposable MinIO container for
the lane. Its storage fault is a deterministic `SystemExit` immediately after
a successful real `PUT`; this verifies the durable recovery boundary but is
not an OS-level kill/restart or deployment-supervisor test.

The standalone route-unit command is restricted to the two explicitly listed
mock-backed modules. Do not apply its `--confcutdir` to live API suites: their
provider and disposable-stack prerequisites still apply. Record any remaining
acceptance gaps in the change or release notes that produced them.

The previous-release lane requires `RAGFLOW_UPGRADE_TEST_DISPOSABLE=1`,
`RAGFLOW_UPGRADE_TEST_POSTGRES_DSN` (loopback-only disposable cluster), and
`RAGFLOW_UPGRADE_TEST_POSTGRES_CONTAINER` (that cluster's Docker container).
It creates and drops four randomly named databases. It must never receive a
shared application cluster. When executing a source snapshot without Git or
downloaded dependencies, set `RAGFLOW_UPGRADE_TEST_HISTORY_REPO` to the history
checkout, `RAGFLOW_UPGRADE_TEST_CANDIDATE_COMMIT` to the verified source commit,
and `RAGFLOW_UPGRADE_TEST_TOKENIZER` to provisioned `cl100k_base.tiktoken`.
`RAGFLOW_UPGRADE_TEST_EVIDENCE_DIR` stores synthetic row snapshots and dumps.
This checks actual model compatibility using the installed dependency set;
it does not reproduce all dependencies or the installer of the old release.
The failure probe uses a PostgreSQL event trigger that rejects creation of
`public.access_group`; a nontransactional sequence proves the server executed
the trigger despite rollback. It is deterministic server fault injection,
not actual disk exhaustion or a hardware-failure test.

For real ASR, mount the service source and audio read-only in a disposable
runtime. Set `LOAD_FROM_FOLDER` to the installed T-One weights and use separate
temporary `ASR_UPLOAD_DIR` and `ASR_ARTIFACTS_DIR`. The expected Russian phrase
is declared in the test before inference; a match on one synthetic recording
is not a general recognition-quality benchmark. Cancellation is cooperative at
safe worker stage boundaries: a synchronous engine call may finish computing,
but a requested cancel must prevent `done`, result and artifacts. Missing opt-in
prerequisites are skips, never evidence that this lane passed.

Generate the local audio and MRZ fixtures from PowerShell with:

```powershell
pwsh -File test/integration/fixtures/New-LocalRegressionFixtures.ps1 `
  -OutputDirectory (Join-Path $regressionTask 'fixtures')
```

The generator requires a local Russian SAPI voice and FFmpeg. Fixtures are
synthetic and may be recreated; do not commit the generated audio or image.
For the local connector lane, use only the dedicated non-production rows in the
Docker database. The test restores the OpenMetadata field exactly and deletes
the EVA page it creates. A failed cleanup or missing record is a failure, not an
acceptable residue. Do not export connector credentials into a command, log or
evidence file.

The coordinated storage lane is deliberately independent of the running
application data. It creates unique Docker resources and validates a shared
snapshot identifier and object digest after restore. Its negative control must
detect a PostgreSQL row whose referenced MinIO object is absent. Full installer
and old-release recovery remain separate release work even when this data-consistency lane is
green.

The saved-DSL lane has two different purposes. Its synthetic test proves a
current record and two historical revisions can round-trip and be removed. The
other two tests audit existing contour data read-only; their failures are
baseline compatibility findings and must not be hidden by recreating providers
or modifying model settings. The MRZ lane follows the same rule: use the
configured Image2Text boundary unchanged and preserve any real failure for
classification.

Use `tools/quality/candidate.py` to create an explicit source snapshot before
cross-lane acceptance. A dirty snapshot is a non-publishable test input. Keep
the manifest source untouched: `PYTHONDONTWRITEBYTECODE=1`, external pytest
cache/evidence, read-only container mounts. Build in a separate copy, provision
ignored runtime dependencies there, and compare every manifest input hash
before and after execution. Record generated dependencies and actual tool
versions separately. A changed test fixture requires a new source snapshot;
do not transfer a failed or stale result to the new identity.

For each substantive change, report the tested commit or candidate `source_id`,
the affected behavior and consumers, the selected lanes with exact commands and
results, and any lane that could not run with its missing prerequisite. Keep a
focused pass scoped to its lane. For release acceptance, attach the existing
candidate manifest, quality reports where applicable, and deployment evidence
for the same source revision; check the deployed image identity, backup and
health in `deployment.json`. Record any remaining acceptance gap explicitly.
`candidate-receipt.json` schema 2 records qualification results and the required
lane set. Release requires successful prerequisites and every mandatory lane;
targeted skips never establish full qualification. Publication separately checks
GitHub job results, artifact origin and producer bytes, using inventories from the
qualified source SHA. Local `Release` requires the published proof before changing
containers and verifies GitHub asset digests, the registry digest and pulled image.
The `release_evidence.py verify` command collects deployment and quality reports;
it does not itself authorize publication. New quality thresholds require two
reproducible unchanged-source/model/corpus runs and review. Existing approved gates
remain mandatory. Missing reports or prerequisites yield `INCOMPLETE`.
`source_id` is recorded only for a separately verified source snapshot and is never
inferred from the Git SHA. Schema 1 is historical and requires the explicit local
restore flag; it cannot authorize a new release.
`evidence_status` and `quality_status` are separate: complete release identity
evidence never turns a failed model-quality baseline into a quality pass.

For the three-task parallel-agent pilot, pair each bounded delegated task with
a similar single-agent task. Record start/finish time, conflicts, integration
fixes, selected checks and outcomes, and available token/compute usage in the
task notes. After three pairs, compare median elapsed time; make parallel
editing routine only if it saves at least 20% with no conflicts or verification
regressions. An incomplete sample is not evidence of that result. Review the
first five substantive changes for CI duration, failures and post-check defects
before changing required branch rules.

The live Business Documents runner accepts `--business-documents-case-id` for
one-case diagnosis. Its report remains `INCOMPLETE`; a full active-suite run is
needed to assess the baseline. Source Workbench uses a local similarity
threshold of 0.4, selected from a disposable `bge-m3` corpus where positive
top hits scored at least 0.450 and no-answer hits scored at most 0.364.
Re-evaluate the threshold for other embedding models; do not lower the oracle
or infer a quality gate from one run.

`browser-regression.yml` runs the isolated browser matrix on pull requests.
`pr-smoke.yml` runs on pushes to `main` and pull requests without the `ci` label.
It compares the complete push range from `event.before`, or the PR base; a missing
base selects all lanes and checks the entire tree. It checks changed-path selection,
patch whitespace, applicable small Python boundary/tooling checks, changed-file
lint, frontend type-checking, and changed Go formatting. It does not replace
the engine, live-model, or browser lanes and is not yet a required branch rule.
`tests.yml` is the single qualification owner. The `targeted` profile selects
engine lanes from the verified successful baseline or complete PR diff. Unknown
inputs select the full appropriate set. It never grants publication eligibility.
The `release` profile always runs p3 on Elasticsearch and Infinity, the complete
browser inventory on Chromium/Firefox/WebKit, native image probes, six portable
CLI and both Python wheels. Version tags and full manual candidates use this
profile regardless of changed paths. Full release qualification and the runner
queue watchdog have no periodic schedules; the watchdog is manual, and release
dry-runs remain available before publication. `sep-tests.yml` remains a temporary
manual entry during the five-run observation period; it calls the same owner.

Release API evidence is checked against `test/mandatory-api-cases.json`, not a
runtime-generated expected count. The collector records nodeids and setup/call/
teardown outcomes. Model fixtures use the disposable tenant with
`RAGFLOW_TEST_OLLAMA_URL` and require `RAGFLOW_TEST_DISPOSABLE=1`. Generic model tests
execute on Ollama; only explicitly reviewed provider-specific cases are N/A.
Bug skips, missing runtimes/models, xfail, missing/extra/duplicate cases fail the
release gate. The API suites have independent inventories for both engines.

```bash
python -m tools.quality.check_release_workflows
pytest test/testcases/restful_api --level=p3 --model-profile=local \
  --api-case-suite restful_api --api-case-evidence /tmp/rest-cases.json
python -m tools.quality.api_case_evidence /tmp/rest-cases.json \
  --suite restful_api --engine infinity --repository meownm/ragflow \
  --sha "$SOURCE_SHA" --run-id "$GITHUB_RUN_ID" --run-attempt "$GITHUB_RUN_ATTEMPT" \
  --image-digest "$RAGFLOW_TEST_IMAGE_DIGEST" --output /tmp/rest-qualified.json
```

`release_evidence` schema 2 records immutable qualification. Publication has a
separate proof, and local Release pulls the promoted image by digest. Schema 1 is
historical evidence only. Two complete qualifications of the same product bytes
use `release.yml` dry-run then the first qualification artifact's
`replay_artifact_id/replay_run_id/replay_attempt`. A replay uses the same image,
CLI and wheels and must keep the same bundle ID.

Native qualification executes six mandatory CGO parser cases and the actual CLI
inside the final image. The parser test binary is separate instrumentation;
PDF layout mock results do not prove real OCR. Compile through:

```bash
bash deployment/runner/build-go-linux.sh --test -c \
  -o output/ci-native/parser.test ./internal/parser/parser
bash deployment/runner/test-native-image.sh output/ci-native/parser.test output/ci-image/native
```

The existing `--cov-branch --cov-fail-under=79` gate measures combined coverage,
not a separate 79% branch rate. Release additionally checks XML branch counts
against the plan's strict 79% branch requirement. Report both numbers. Do not
change coverage sources/exclusions to pass it. A below-floor report blocks release.

Previous qualified runtime upgrade is checked with
`deployment/runner/test-qualified-upgrade.py`: real old/new image dependencies,
synthetic PostgreSQL rows, repeat initializer, backup/restore and own-resource
cleanup. It requires a trusted previous published receipt and digest-pinned
PostgreSQL. Missing baseline is INCOMPLETE. Historical v1.12.0 source tests remain
useful focused evidence and do not substitute for this check.
The same probe accepts `--historical-image/--historical-sha` for explicit diagnostic
assessment. It verifies repository ancestry and actual image source identity, then
runs the same disposable upgrade/restore sequence. Its overall status remains
INCOMPLETE even when `diagnostic_status=PASS`; it never establishes publication
eligibility or a previous qualified release.
The explicitly approved initial bootstrap is a separate one-time policy with a
fixed historical image/SHA. `--bootstrap` requires its exact bytes and passing
migration/restore/cleanup; the historical image remains unqualified. Publication
rejects another bootstrap after the first qualified bundle and allows only an
idempotent retry of that exact publication. This exception never reduces the new
bundle's required lanes, API inventory, models or coverage gates.

See [release architecture](../docs/develop/release-architecture-ru.md) for events,
proof contracts, publication recovery and rollout criteria.

The coverage floors prevent regression from the measured baseline; they are
not a claim that every application route or every negative case is covered.
