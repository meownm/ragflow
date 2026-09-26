# Source Workbench retrieval quality pilot

This pilot scores **document discovery** through the real
`POST /api/v1/source-workspaces/{workspace_id}/search` response. It measures
MRR@5 and recall@5 on positive queries, plus the proportion of no-answer
queries that return any candidate. The labels are fixed in `golden.json`; the
runner never invents search results or substitutes a mock retriever. The
scorer unit tests check the mathematics only, not retrieval quality.

## Prepare a disposable stack

The preferred one-shot command uses the existing disposable live runner. It
creates a new Compose project, installs the four documents below, waits for
indexing, calls the production `/search` route, saves
`source-workbench-quality.json` even on a threshold failure, and tears down
its own project. The runner requires the locally installed Ollama models and a
built SPA described in `test/REGRESSION.md`:

```powershell
.venv\Scripts\python.exe -B test/integration/live_ragflow/runner.py --source-root . --dist-root web/dist --evidence-dir <outside-source> test/playwright/e2e/test_source_workbench_quality_live.py
```

The manual steps below remain useful for inspecting a disposable stack that
already exists. They are not required by the one-shot runner.

1. Start a separate, disposable RAGFlow stack reachable on `localhost` or
   `127.0.0.1`. Never point this runner at a shared tenant or production data.
2. Create one dataset with only the four documents in `golden.json`. Use each
   document's `title` as the filename/title and `text` as its entire content.
   Wait until all four are fully indexed and searchable.
3. Create a Source Workbench workspace for that dataset, titled
   `eval-source-workbench-<unique-run-id>`. Record its workspace ID and the
   four **actual indexed document IDs**. Do not use guessed IDs or copied
   results. The search route writes query history to this workspace.
4. Create a bindings JSON file outside the repository, for example:

   ```json
   {
    "disposable": true,
    "workspace_id": "actual-workspace-id",
    "workspace_title": "eval-source-workbench-unique-run-id",
    "dataset_id": "actual-dedicated-dataset-id",
     "document_ids": {
       "incident": "actual-document-id-1",
       "vacation": "actual-document-id-2",
       "travel": "actual-document-id-3",
       "equipment": "actual-document-id-4"
     }
   }
   ```

Run from the repository root in PowerShell with an authenticated **test** user:

```powershell
$env:SOURCE_WORKBENCH_EVAL_DISPOSABLE = '1'
$env:SOURCE_WORKBENCH_EVAL_AUTHORIZATION = 'Bearer <test-token>'
.venv\Scripts\python.exe test/evals/source_workbench/evaluate.py --base-url http://127.0.0.1:<port> --bindings <path-to-bindings.json> --output <path-to-report.json>
```

An authenticated session cookie can be supplied in
`SOURCE_WORKBENCH_EVAL_COOKIE` instead. Do not store either credential in the
bindings or report. Exit code `0` means the declared thresholds passed, `1`
means the measured retrieval missed a threshold, and `2` means the live
evaluation was incomplete. Missing opt-in, non-loopback URL, missing bindings,
unexpected API envelope and missing observations fail closed. The runner checks
the dedicated workspace title before issuing queries.

The no-answer metric is deliberately strict: any returned document counts as
a false positive. A semantic retriever without a calibrated rejection
threshold may fail that gate. Keep the failure as baseline evidence; adjust
the product or review labels and thresholds explicitly, never relax them just
to make a run green. This tiny synthetic corpus does not establish quality
across real tenant data, languages or embedding models. Record the backend,
embedding model, source revision and index state next to each live report.

Focused offline scorer check:

```powershell
.venv\Scripts\python.exe -m pytest -q test/evals/source_workbench/test_evaluate.py test/evals/source_workbench/test_live_provisioning.py
```
