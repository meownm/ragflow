# Disposable Python RAGFlow regression

Run from the repository's Python environment with Docker Desktop and installed
proxy models `qwen3.8:latest`, `qwen3.6:27b`, and
`bge-m3:latest`. The runner validates all three through RAGFlow. The project
uses `qwen3.8:latest` as the default chat model; T-lite is available for the
two-model chat comparison scenario.
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
chat, search, two-model UI behavior, and business-document intake. The shared
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
