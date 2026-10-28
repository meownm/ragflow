# Disposable Python RAGFlow regression

Run from the repository's Python environment with Docker Desktop and installed
proxy model `qwen3.8:latest`. The runner validates only the selected chat models
through RAGFlow; retrieval scenarios additionally require `bge-m3:latest`.
Other chat models require explicit selection and user authorization.
Ollama proxy must be reachable at `127.0.0.1:11435` from the host and
`host.docker.internal:11435` from containers. It neither downloads
models nor selects cloud models.

```powershell
.venv/Scripts/python.exe -B test/integration/live_ragflow/runner.py `
  --source-root S:/candidate/source `
  --dist-root S:/frontend-work/source/web/dist `
  --frontend-archive S:/artifacts/frontend-candidate.tar.gz `
  --evidence-dir S:/ragflow/output/live-proof
```

Use a frontend build made from the same frozen candidate. The runner requires
and verifies its archive/receipt, matches the mounted dist, records complete hashes and verifies
the candidate before and after execution. Without `candidate.json`, evidence is
explicitly marked as a development run. Container dependencies come from the
recorded existing RAGFlow image; this lane does not certify a rebuilt release image.

Each run uses a random Compose project, new PostgreSQL/MinIO/Elasticsearch
volumes, fresh generated administrator credentials, and loopback ports 19382 and
19383. Ports must be free. Never point these tests at shared application data.
Catalog initialization only accepts an empty disposable database; catalog,
mapping and tracked parser-resource bytes are mounted from the selected source
and checked inside the container. Downloaded parser resources remain external
image dependencies. Passwords and tokens remain in memory/environment; logs are
sanitized. A `finally` block removes all resources belonging to this exact run
and checks that no containers, volumes or networks remain.

Default scenarios perform real browser upload, worker parsing, indexed retrieval,
chat, search, and business-document intake. Chat checks default to qwen3.8; use
`--chat-model` for an explicitly requested alternative. Two-model UI acceptance
requires explicitly selecting `test_live_document_roundtrip.py` and repeating
`--chat-model` for both authorized models. The shared
chat/search fixture uploads a synthetic text document, requires `DONE` with
nonempty chunks, and verifies deletion. The three-PDF upload scenario generates
one-page synthetic text PDFs, retains settings/save/upload/parse/delete checks,
and uses Plain Text parsing. It does not certify complex PDF layout, OCR, tables,
or the benchmark corpus. Business intake requires completed work, nonempty
clarification questions and 2–4 options per question; it is not a document quality benchmark.

The evidence directory contains command logs, JUnit results, browser artifacts,
source/image/assets/dist identities, positive scenario proofs, and cleanup
results. Failed prerequisites or scenarios are failures; an interrupted run is
not passing evidence. Additional positional arguments select native pytest files
for a focused replay, and its narrower scope must be reported.

For the four workbench sections only, explicitly select
`test/playwright/e2e/test_workbench_integrations_live.py` with
`--chat-model qwen3.8:latest --worker-service --openmetadata-network openmetadata_app_net`.
This uses a separate unpublished document/SQL worker, disables the unrelated
dataset-ingestion executor, and bounds the disposable services' memory. The
OpenMetadata network must already exist. Supply its non-production integration
credentials through `QA_OPENMETADATA_USERNAME` / `QA_OPENMETADATA_PASSWORD` or
`QA_OPENMETADATA_JWT_TOKEN`; never place those values in files or command logs.
The suite creates and deletes its own OpenMetadata service/table and uses its
own PostgreSQL schema and read-only role. No API or inference responses are
substituted. A failed environment prerequisite is incomplete acceptance.
The BT generation/publication journey additionally requires
`QA_EVA_CONNECTOR_CONFIG` in the test process environment: a real non-production
EVA connection with `credentials.eva_api_token`. Do not put the token in CLI
arguments or files. The test configures only its private RAGFlow database and
synthetic user's personal credential, creates its own EVA page and verifies its
removal. Its M02-derived authored facts are supplied as ordinary idea input;
this is not the source-grounding model-quality golden. A REVIEW draft must
reject an AGREED-only Markdown export; that rejection is not an export pass.
The TZ copy-import journey reads real external HTML. It compares the complete
rendered content and structure of all nine sections because HTML decoding may
normalize Markdown escapes and line breaks. The original document's source
must remain byte-for-byte unchanged, and the copy must retain its own saved source.
If Docker's automatic network pools are exhausted, `--network-subnet <unused-CIDR>`
sets only this disposable project's IPv4 subnet; verify it does not overlap
existing Docker networks or host routes. It does not alter Docker's global pools.
If the default pair 19382/19383 is in use, `--base-port 19482` selects the
reserved alternative pair 19482/19483. Leave other tasks' services running.

For the two AI quality baselines, select the Source Workbench live file and add
`--business-documents-quality`. The runner mounts the test corpus only into
its disposable app, installs pytest there, uses the synthetic tenant's real
model, and writes separate `source-workbench-quality.json` and
`business-documents-quality.json` files. A threshold failure stays in the
report and makes that test fail; it does not delete either report. The model
report records source dirty state and the proxy model digest.
For a backend-only Business Documents model run, add `--skip-browser-tests`
and point `--dist-root` to an empty directory outside the source snapshot; this
mode does not provide browser or retrieval evidence.
