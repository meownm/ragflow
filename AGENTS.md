# RAGFlow Instructions

Use this file as the local operating guide for the current codebase. Prefer the code and the current CLAUDE.md over any older convention or remembered project shape.

## Upstream and extension policy
- Preserve the ability to merge upstream RAGFlow while retaining local features and data. Keep the standard directory layout and runtime paths; do not reorganize upstream code to enforce a local layering preference.
- Before editing, identify whether the affected code is a local extension, a necessary change to upstream code, or unchanged upstream. Use Git history and the actual diff when that distinction matters; do not maintain a separate generated provenance registry.
- Apply strict architecture and dead-code rules to owned modules and local integration changes. Unused-in-this-deployment upstream backends and runtimes are not automatically dead code.
- Keep local business logic in its owning module. Put calls to upstream behind narrow adapters and keep core registration/wiring changes small, justified, and tested. Do not create a plugin framework or duplicate the upstream implementation for isolation.
- Do not add mass moves, unrelated formatting, or a package-manager switch to a feature or cleanup task. A necessary core fix is allowed and must have a reason and focused verification.
- Use [the regression lane guide](test/REGRESSION.md) to select checks. Run only the checks justified by the affected behavior and consumers; reserve full matrices for releases, upstream updates, or explicitly broad changes.

## Core stance
- Treat obsolete owned code as liability; verify current consumers, dynamic registrations, persisted DSL and external contracts before removal.
- Prefer deletion of obsolete owned paths over shims, deprecated branches, wrapper APIs, and dual-track migration notes. A narrow adapter to a real upstream boundary is not an obsolete wrapper.
- If old and new owned implementations coexist, converge to one path unless an external contract requires both. Do not collapse supported upstream alternatives merely because this deployment selects one.
- Remove dead tests, commented-out code, stale docs, and "move later" notes instead of preserving them.
- Reduce public surface area when a helper can be made private or internal.
- Keep refactors centered on the owning abstraction, not on adjacent compatibility layers.

## Current stack
- Backend: Python 3.13+, Quart-based API server, Peewee ORM, async workers.
- Frontend: React + TypeScript + Vite in `web/`.
- Go: the repository also has a substantial Go module for servers, ingestion, parser/runtime, CLI, and supporting services.
- Runtime services commonly include MySQL/PostgreSQL, Redis, MinIO, and Elasticsearch/Infinity/OpenSearch depending on configuration.

## Code layout to expect
- `api/`: Python API server entrypoints, blueprints, services, and database code.
- `rag/`: ingestion, retrieval, LLM integration, and graph RAG logic.
- `deepdoc/`: parsing and OCR.
- `agent/`: workflow canvas, components, tools, and templates.
- `cmd/`: Go entrypoints. `ragflow_main` is the main server/admin/ingestor binary surface; `ragflow-cli` is the CLI entrypoint.
- `internal/`: main Go application code. Important subtrees:
- `internal/agent/`: Go agent runtime, canvas execution, components, tool bindings, workflow helpers.
- `internal/cli/`: CLI parsing, HTTP transport, command execution, response formatting.
- `internal/dao/`: Go data-access layer and persistence-facing helpers.
- `internal/deepdoc/`: Go DeepDOC integrations, especially native-backed PDF/DOCX parsing.
- `internal/engine/`: search/index backends such as Elasticsearch and Infinity.
- `internal/entity/`: shared Go entities and model definitions.
- `internal/handler/`: HTTP handlers and route-facing request logic.
- `internal/ingestion/`: Go ingestion pipeline, canvas adapter, components, wiring, service orchestration.
- `internal/ingestion/component/`: stage implementations such as file/parser/chunker/tokenizer/extractor.
- `internal/ingestion/pipeline/`: DSL translation, canvas-driven execution, checkpoints, resume/run logic.
- `internal/parser/`: parser and chunk libraries used by ingestion and other Go paths.
- `internal/parser/parser/`: typed parse-result parsers for markdown/html/pdf/docx/xlsx/text and related families.
- `internal/parser/chunk/`: chunk operator library and DSL/typed execution helpers.
- `internal/service/`: higher-level business services used by handlers and server flows.
- `internal/storage/`: storage backends and in-memory test doubles.
- `internal/router/`: HTTP route registration.
- `internal/server/`: server bootstrap/config wiring.
- `internal/cpp/`: C++ sources used by native-backed Go features.
- `web/`: frontend application.
- `docker/`: local and production compose files.
- `sdk/` and `test/`: SDK and automated tests.

## Go-specific rules
- In `internal/ingestion`, `internal/parser`, and `internal/deepdoc`, keep local changes centered on their owner. Collapse confirmed local duplicate paths; preserve the upstream layout and supported profiles.
- Do not add or preserve deprecated Go APIs just to ease migration inside the repo.
- Remove commented-out Go code instead of leaving recovery notes in place.
- Keep package comments and doc comments aligned with the current runtime path, not with migration history.

## Working rules
- Before a substantive change, record the user scenario, observable acceptance result, error and authorization cases, affected consumers, and intended checks in the task. Keep the brief proportional to the change; do not create a separate architecture document for routine work.
- One agent owns the integrated diff and its verification report. Give delegated agents bounded, independent questions or file ownership and a required result; the owner checks their findings and runs the relevant checks on the combined source.
- In a shared checkout, parallel agents may inspect independently but must not edit overlapping files or mutate shared test/runtime state. Parallel editing needs an independently justified isolated checkout and an explicit integration owner. Do not run concurrent deployments against the same environment.
- Use parallel agents for a measured pilot before treating them as a default: compare elapsed time, integration rework, conflicts, verification outcomes, and available usage against similar single-agent work.
- Report the source identity, changed behavior and consumers, exact checks and outcomes, missing prerequisites, and remaining acceptance gaps. A focused pass is evidence only for its stated boundary.
- Before editing, inspect the nearest code path that actually owns the behavior.
- Keep changes small and local unless the task is explicitly a broader refactor.
- Prefer one implementation path instead of preserving old and new versions side by side.
- Preserve behavior with focused tests when the behavior is still valid; do not keep tests that protect obsolete behavior.
- Remove owned compatibility-only surfaces after checking supported consumers and saved data. Preserve supported external contracts and upstream integration boundaries.
- Comments describe the current path and invariants, not superseded migration history.
- Domain/application code in owned modules must not depend on concrete ORM, network clients, request/session objects or application bootstrap. Check parent-package import side effects before claiming a module is isolated.
- Preserve tenant authorization, transaction boundaries, idempotency and cancellation when extracting a scenario. API and worker must use the same business implementation.
- Select checks by affected behavior and consumers. Missing tools or prerequisites mean incomplete verification, not a pass. Do not run live suites against shared production data.
- During review, describe affected boundaries, consumers, behavior and verification directly. Do not widen coverage or exclusions to hide a regression.
- Ordinary local deployment runs from the current checkout through `deployment/local/deploy.ps1`; do not create or switch a branch/worktree merely to deploy. The script records dirty state and source identity, validates Compose, backs up PostgreSQL, recreates only selected services, and captures health evidence. A branch/worktree is reserved for upstream integration, conflicting candidate assembly, or another independently justified isolation boundary.
- For an upstream update, use a separate integration branch/worktree from a complete committed local snapshot. Merge a verified upstream commit; do not rewrite published fork history or use blanket ours/theirs conflict resolution. Test migrations and recovery before release. A new worktree does not include uncommitted work automatically.

## Commands
### Backend
```bash
uv sync --python 3.13 --all-extras
uv run python3 ragflow_deps/download_deps.py
docker compose -f docker/docker-compose-base.yml up -d
source .venv/bin/activate
export PYTHONPATH=$(pwd)
bash docker/launch_backend_service.sh
uv run pytest
ruff check
ruff format
```

### Frontend
```bash
cd web
npm install
npm run dev
npm run build
npm run lint
npm run test
npm run test:focused -- path/to/test.tsx
npm run type-check
```

### Go
```bash
uv run ragflow_deps/download_deps.py
bash build.sh --test ./path/to/package/...
bash build.sh --go
# or build specific binaries:
bash build.sh --all
```

## Validation preference
- Run the narrowest relevant test, lint, or build command after a change.
- For backend changes, prefer targeted pytest or ruff checks over full-suite runs.
- For frontend changes, prefer the touched-package lint, type-check, or test command.
- For Go changes, prefer package-scoped `bash build.sh --test ...` first.
- Do not default to raw `go test`, `go build`, or IDE Run/Debug for Go in this repo. They often miss the required CGO flags and native static libraries (`office_oxide`, `pdfium-static`, `pdf_oxide`) that `build.sh` wires correctly.
- If Go native builds fail, inspect `build.sh` and `internal/development.md` before changing code. Common environment issues are missing downloaded native deps and missing `lld` on Linux.

## Review mode
- Requests such as `ревью`, `проведи ревью` or `review` include correctness, architecture, module boundaries and simplicity unless the user explicitly narrows the scope. Passing tests do not replace these checks.
- Honor an explicit PR, commit, base branch or path. Otherwise review the current task's increment when identifiable; if not, use tracked changes against `HEAD` plus relevant untracked files. State the scope, comparison base and source identity before reviewing. If the tree is clean and the target cannot be inferred from the task, ask for the target rather than choosing an arbitrary commit. Review the whole repository only when requested.
- A review-only request authorizes analysis and relevant checks, not source edits, cleanup, staging, commits or deployment. Report proposed removals and simplifications as findings. Use checks without auto-fixes; Lefthook requires `LEFTHOOK_CHECK_ONLY=1`. Keep generated test artifacts outside the reviewed source and use disposable state for checks that write data. When fixes are already authorized, separate review and repair stages within that scope.
- Inspect the owning scenario and follow its callers, consumers, registrations and persisted contracts beyond the diff where needed to assess impact. Select focused checks from `test/REGRESSION.md`; do not turn ordinary review into a full regression or deployment run.
- Order findings by impact. Each finding needs a file/line, concrete evidence, affected scenario or dependency, and consequence. For unnecessary complexity, explain the maintenance or runtime cost and a simpler alternative that preserves required behavior and boundaries. Distinguish introduced defects, existing issues, unverified risks and optional design preferences; do not present preferences as defects.
- Report correctness findings, architecture/boundary/simplicity findings, and verification gaps separately but concisely. Include actual checks and results; a report of "no findings" does not imply complete verification. After authorized fixes, recheck the findings and affected consumers against the updated source; do not reuse stale test evidence.

## Default review checklist
- Correctness: trace the user scenario, expected outputs, errors, ordering and side effects through the actual implementation and its consumers.
- Architecture: verify that business decisions live in their owning module, API and worker paths share the same business implementation, and dependency direction follows the existing ownership rules. Check concrete infrastructure leaks, import side effects, cycles and access to another module's internals.
- Boundaries: check external contracts and saved data/DSL, tenant authorization, transaction ownership, resource lifetime, idempotency, retries and cancellation wherever the scenario crosses a module or runtime boundary.
- Simplicity: require a current scenario, consumer or invariant to justify added layers, abstractions, wrappers, factories, configuration switches and dependencies. Look for speculative extensibility, pass-through layers and indirection that could be removed without losing a real boundary. A single consumer alone does not make a boundary unnecessary.
- Algorithms and state: investigate avoidable branching, nesting, repeated scans or I/O, duplicated state and overlapping execution paths. Prefer one clear implementation; do not split code merely to reduce a metric or merge distinct responsibilities merely to reduce lines.
- Upstream integration: explain each local core change and assess the smallest adequate integration. Preserve supported upstream alternatives and layout; apply strict cleanup rules to owned code and local integration changes.
- Cleanup: identify confirmed obsolete code, duplicate business rules, unused public APIs, stale comments/docs, registrations, assets and dependencies. Verify consumers and dynamic or persisted usage before recommending removal; names such as `legacy` alone are not proof.
- Verification: check applicable behavior and data boundaries, report the limits of evidence, and never reduce coverage scope, disable required checks or extend exceptions to hide a regression.

## Continuous cleanup and algorithm simplification
- After a coherent change, inspect the changed symbols and their direct callers for newly obsolete owned code. Use repository search and focused tests; no generated architecture report is required.
- Inspect changed symbols, their module, callers and dynamic entrypoints for newly dead owned code. Apply a proven, scoped cleanup autonomously within the authorized task; uncertain usage is a finding to investigate, not permission to delete.
- Do not equate an unused binding with a removable computation. Check registration imports, decorators, namespace reflection, resource lifetime, persisted DSL and external contracts before removing code.
- Before applying an automated patch, verify that its source snapshot is still current. On regression, undo only that patch without overwriting other edits; do not reset the shared tree.
- Treat complexity, nesting, repeated scans and I/O amplification as investigation signals. For a significant new signal, simplify the owning scenario or document why the complexity expresses required behavior. Do not split code merely to lower a metric.
- Before simplifying an algorithm, state its outputs, errors, ordering, side effects and invariants. Verify before/after behavior; measure runtime, query counts and memory when traversal, data structures or I/O change. Keep one production implementation.
- CI checks and reports only. Planned cleanup automation does not authorize automatic commits, merges, deploys, product retirement or unrelated upstream refactors.
