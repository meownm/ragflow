# Regression lanes

Run each lane separately: the live API conftest selects priority markers and
must not change the selection of unit tests or P0 document requirements.

| Lane | Command from repository root | Boundary |
| --- | --- | --- |
| Python unit | `python run_tests.py -i` | In-process dependencies and fixtures |
| Standalone route units | `python -m pytest -o "pythonpath=. test/testcases" --confcutdir=test/testcases/restful_api test/testcases/restful_api/test_user_tenant_routes_unit.py test/testcases/restful_api/test_connector_routes_unit.py` | Only these reviewed in-process modules; excludes the parent live-model setup, keeps local fixtures and all test cases |
| Browser result assertions | `python -m pytest test/unit_test/playwright test/unit_test/api/apps/services/test_search_consistency_assertions.py` | Reject missing requests, empty/error/truncated answers, and invalid search results |
| Document coverage | `python -m pytest test/unit_test/business_documents test/unit_test/api/apps/business_documents test/unit_test/api/apps/restful_apis/test_business_document_api_contract.py --cov=api/apps/business_documents --cov=business_documents --cov-branch --cov-fail-under=79` | Pure domain/application, worker, evidence, export, authorization and API contracts across both Business Documents source roots; combined statement/branch coverage |
| Document requirements | `python -m pytest test/evals/business_documents/test_golden_dialogue_harness.py test/evals/business_documents/test_live_quality_scorer.py` | All 24 scripted golden cases and deterministic scorer checks |
| Document model quality | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <empty-QA-dir> --evidence-dir <outside-source> --business-documents-quality --skip-browser-tests` | Dedicated disposable tenant and project, project Ollama proxy, real LLM, controlled retrieval and SQLite; separate `business-documents-quality.json` report; not live search, browser or tenant-authorization evidence |
| Source Workbench retrieval scorer | `python -m pytest test/evals/source_workbench/test_evaluate.py test/evals/source_workbench/test_live_provisioning.py` | Offline schema, ranking metrics and disposable fixture checks; no assertion about live retrieval quality |
| Source Workbench retrieval quality | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <built-SPA> --evidence-dir <outside-source> test/playwright/e2e/test_source_workbench_quality_live.py` | Opt-in disposable stack; uploads exactly four golden documents, waits for indexing, calls real workspace search, writes `source-workbench-quality.json`, then removes its dataset and the runner removes its project |
| PostgreSQL and export-storage races | `python -m pytest test/integration/test_business_document_postgres.py` | Requires disposable `BUSINESS_DOCUMENT_TEST_POSTGRES_DSN` plus `BUSINESS_DOCUMENT_TEST_MINIO_ENDPOINT`, `BUSINESS_DOCUMENT_TEST_MINIO_USER`, and `BUSINESS_DOCUMENT_TEST_MINIO_PASSWORD`; separate DB connections, temporary schema, real MinIO `PUT` interruption and verified reconciliation |
| Previous-release data | `python -m pytest test/integration/test_previous_release_data_upgrade.py` | Explicit disposable PostgreSQL cluster; real v1.12.0/current initializers, dump/restore, repeated initialization and injected DDL failure; no deployment-script acceptance |
| MinIO backup/restore | `python -m pytest test/integration/test_minio_backup_restore.py` | Requires `RAGFLOW_MINIO_BACKUP_TEST=1`; creates its own containers/volumes, archives raw `/data`, restores and compares object bytes/metadata, verifies cleanup |
| Coordinated PostgreSQL+MinIO restore | `$env:RAGFLOW_COORDINATED_BACKUP_TEST='1'; uv run pytest -q test/integration/test_postgres_minio_coordinated_restore.py` | Creates source and restore containers/volumes, freezes writes, restores a linked manifest/object pair, runs a missing-object negative control and verifies cleanup |
| Local Docker EVA/OpenMetadata | Copy `test/integration/test_local_docker_connectors_live.py` into the application container, then run it there with `RAGFLOW_LOCAL_DOCKER_CONNECTOR_TEST=1` | Uses existing non-production connector rows without serializing credentials; real read/write/retry/recovery and cleanup |
| Real T-One inference and cancel | From `services/asr-online-service`: `python tests/integration/test_tone_real_inference.py` | Requires `ASR_REAL_TONE_AUDIO` and `ASR_REAL_TONE_LONG_AUDIO`, installed Tone runtime and FFmpeg; actual endpoint/worker/inference with a predeclared transcript and mid-inference cancellation, no inference mocks |
| MRZ real image | Copy `test/integration/test_mrz_document_reader_live.py` and a generated PNG into the application container, then run with `RAGFLOW_MRZ_TEST_IMAGE=<container-path>` | Executes the deployed Canvas template with the tenant's existing Image2Text setting and deterministic checksum validator; does not change a model or prompt |
| Saved Canvas DSL | Copy `test/integration/test_saved_canvas_dsl_live.py` into the application container, then run with `RAGFLOW_SAVED_DSL_TEST=1` | Synthetic current/two-version roundtrip with cleanup plus read-only audit of current and attached historical records; reports failure classes without provider secrets |
| Frontend | `cd web` then `pnpm test --runInBand` | Jest; measured global floors for statements, lines, functions and branches |
| Local browser server | `python -m pytest test/unit_test/playwright/test_browser_server.py` | Actual HTTP server, connection reuse, SPA fallback and unmocked API rejection; no browser or backend |
| Isolated browser | `python test/run_browser_regression.py --browser chromium` | Built SPA, intercepted API, no live backend; also accepts `firefox` and `webkit` |
| Live browser | `python -m pytest test/playwright/e2e/test_dataset_upload_parse.py test/playwright/e2e/test_next_apps_chat.py test/playwright/e2e/test_next_apps_search.py` | Disposable complete stack, configured model provider and `RAGFLOW_BASE_URL` |
| Disposable live stack | `python -B test/integration/live_ragflow/runner.py --source-root <source> --dist-root <built-SPA> --frontend-archive <matching-archive-with-receipt> --evidence-dir <outside-source>` | Creates its own PostgreSQL/Redis/MinIO/Elasticsearch/app stack and synthetic admin; verifies frontend provenance, real ingestion, search, chat, document intake and resource cleanup |
| Persistent Ubuntu QA smoke | On `192.168.1.175`, from `/opt/ragflow-qa`: `docker compose --env-file .env -f compose.yml exec -T app /ragflow/.venv/bin/python - < smoke.py` | Own project and volumes survive restarts; the test creates and removes only its dataset, checks real indexing and `/retrieval`; source image SHA is in `setup.json` and may differ from the current checkout |
| Web/admin API | `python -m pytest test/testcases/test_web_api` and separately `python -m pytest test/testcases/test_admin_api` | Disposable stack; SDK installed, provider configuration and mapped admin endpoint |
| Go | `bash test/run_go_regression.sh` | Linux CI after native build; all packages, disposable MinIO and tokenizer dictionaries |

On Windows, use `.venv/Scripts/python.exe` for `python`. Build the SPA with
`pnpm build` in `web` before the isolated browser lane, and install the selected
browser with `python -m playwright install chromium` (or Firefox/WebKit).

Browser journeys execute all their steps in one test and use independent page
and state fixtures. Missing prerequisites fail rather than count as skipped
steps. Unhandled page exceptions fail the test. Failure evidence is written
under `test/playwright/artifacts/<browser>`.

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
pwsh -File test/integration/fixtures/New-LocalRegressionFixtures.ps1 -OutputDirectory .codex_tmp/local-regression-fixtures
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
The CI `publish_candidate` job uploads `candidate-receipt.json` only after the
preflight and both engine jobs succeed. Local `Release` requires this receipt
before changing containers and verifies its revision, registry digest and the
pulled image ID. Run `python tools/quality/release_evidence.py verify --receipt
<candidate-receipt.json> --deployment <deployment.json> --source-workbench-report
<source-workbench-quality.json> --business-documents-report <business-quality.json>
--output <release-evidence.json>` to collect a separate pass/fail/incomplete
report. Quality thresholds are baseline observations until two reproducible
unchanged-source/model/corpus runs establish a reviewed gate. Missing reports
or prerequisites yield `incomplete`. The receipt is trusted only when obtained
from the named CI run; the verifier checks its contents and file hash, not CI
artifact authenticity. `source_id` is recorded only for a separately verified
source snapshot and is never inferred from the Git SHA.

`browser-regression.yml` runs the isolated browser matrix on pull requests.
`tests.yml` and `sep-tests.yml` run requirements, PostgreSQL races, the document
coverage gate, Web/admin APIs and live browser journeys alongside their existing
unit/SDK/REST lanes. `tests.yml` runs the full Go suite on non-PR events. For a
PR, changes unrelated to Go skip Go tests; Go changes run their packages and
in-module consumers on both document engines. Unknown inputs fall back to the
full suite. `sep-tests.yml` selects
changed lanes from the complete push or PR diff and runs every lane for tags,
schedules and unclassified inputs. Candidate
publication always rebuilds native and Go binaries from the tested revision.

The coverage floors prevent regression from the measured baseline; they are
not a claim that every application route or every negative case is covered.
