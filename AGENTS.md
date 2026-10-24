# RAGFlow Instructions

This is the shared operating guide for agents working in this repository. Follow the user's current scope and authorization; use current code and these instructions rather than older conventions or memory.

## Read the applicable guide

- Before working in `web/`, including a task started at the repository root, read [web/AGENTS.md](web/AGENTS.md).
- For user-facing frontend scenarios, copy and visual consistency, read [the product and visual guide](docs/develop/frontend-product-design-ru.md). It also applies to embedded ASR/translator frontends; use the shared `frontend-product-design` skill when available.
- For RAGFlow Figma tasks, use [the project skill](.agents/skills/ragflow-figma/SKILL.md) and [the Figma workflow guide](docs/develop/figma-workflow-ru.md) for file structure, code/design sources and acceptance checks.
- Select checks and read their prerequisites in [test/REGRESSION.md](test/REGRESSION.md). Run only lanes justified by affected behavior and consumers; full matrices belong to releases, upstream updates or explicitly broad work.
- For requested Trivy, Syft, Gitleaks or Grype checks, follow [the on-demand security guide](tools/security/README.md). Select the requested source/history/image scope, record input identity, tool/database versions and exit codes, redact secrets, and report incomplete coverage explicitly. Do not run these scans automatically on ordinary edits or deployment; a scan request alone does not authorize fixes or publication.
- Before deployment or creating disposable workspaces, read [deployment/local/README.md](deployment/local/README.md). Deployment authorization is defined below; the README owns commands, modes and workspace mechanics.
- For article processing and linked documents, apply the architecture and technical specification standards described in [the document standards guide](docs/develop/article-document-standards-ru.md). Keep their requirements in `api/source_workbench/document_standards.py`; apply them to the requested output type, preserve source evidence and approval boundaries, and do not impose these outlines on ordinary articles.
- For Go naming, use [the naming skill](.agents/skills/go-naming/SKILL.md).

## Upstream and extension policy

- Preserve the ability to merge upstream RAGFlow while retaining local features and data. Keep the standard directory layout and runtime paths; do not reorganize upstream code to enforce a local layering preference.
- Before editing, identify whether the affected code is a local extension, a necessary change to upstream code, or unchanged upstream. Use Git history and the actual diff when that distinction matters; do not maintain a separate generated provenance registry.
- Apply strict architecture and dead-code rules to owned modules and local integration changes. Unused-in-this-deployment upstream backends and runtimes are not automatically dead code.
- Keep local business logic in its owning module. Put calls to upstream behind narrow adapters and keep core registration/wiring changes small, justified, and tested. Do not create a plugin framework or duplicate the upstream implementation for isolation.
- Do not add mass moves, unrelated formatting, or a package-manager switch to a feature or cleanup task. A necessary core fix is allowed and must have a reason and focused verification.

## Current stack

- Backend: Python 3.13+, Quart-based API server, Peewee ORM, async workers.
- Frontend: React + TypeScript + Vite in `web/`.
- Go: the repository also has a substantial Go module for servers, ingestion, parser/runtime, CLI, and supporting services.
- Runtime services commonly include MySQL/PostgreSQL, Redis, MinIO, and Elasticsearch/Infinity/OpenSearch depending on configuration.

Backend entrypoints/services live in `api/`, retrieval and parsing in `rag/` and `deepdoc/`, workflow components in `agent/`. Go entrypoints live in `cmd/` and application/runtime/parser code in `internal/`. Frontend, Compose configurations, SDKs and tests live in `web/`, `docker/`, `sdk/` and `test/`. Inspect the actual owning module rather than assuming a generic layout.


## Task scope and ownership

- Before a substantive change, record the user scenario, a few explicit observable acceptance outcomes, error and authorization cases, affected consumers, and the check intended for each outcome in the existing task. Preserve those outcomes through implementation; explain requirement changes rather than silently narrowing acceptance to what was built. Keep the brief proportional; routine work needs no separate document or approval stage.
- For work spanning sessions, maintain one compact continuation checkpoint in the existing task/issue or report: source identity, accepted outcomes and current evidence/gaps, pending decisions and next action. Update it before handoff or interruption and verify it against current source when resuming. Link existing reports and receipts rather than copying their contents or creating parallel progress logs.
- One agent owns the integrated diff and its verification report. Give delegated agents bounded, independent questions or file ownership and a required result; the owner checks their findings and runs the relevant checks on the combined source.
- In a shared checkout, parallel agents may inspect independently but must not edit overlapping files or mutate shared test/runtime state. Parallel editing needs an independently justified isolated checkout and an explicit integration owner. Do not run concurrent deployments against the same environment.
- Keep additional roles selective. For the pilot beginning 2026-10-24, measure 10–15 substantive tasks once; count samples from existing task reports rather than restarting each session. Record a short line there: scope/risk and roles, elapsed time to the accepted result, observed human interventions/time, rework and integration time/conflicts, available model usage/cost, and acceptance or later repair/rollback outcome. Mark unavailable values unknown, distinguish cached usage, and do not interrupt the user to collect metrics. Tiny edits need no measurement line or separate tracker.
- Use that pilot to compare similar single-agent and multi-agent tasks at the same acceptance boundary; report sample size and missing measurements before claiming a speed or cost benefit. Reuse task reports for the comparison, then retain only measurements that help choose the workflow. Do not make parallel agents the default without evidence.
- Before editing, inspect the nearest code path that actually owns the behavior.
- Keep changes small and local unless the task is explicitly a broader refactor.
- Domain/application code in owned modules must not depend on concrete ORM, network clients, request/session objects or application bootstrap. Check parent-package import side effects before claiming a module is isolated.
- Preserve tenant authorization, transaction boundaries, idempotency and cancellation when extracting a scenario. API and worker must use the same business implementation.

## Deployment and publication

- Ordinary local deployment runs from the current checkout through `deployment/local/deploy.ps1`; do not create or switch a branch/worktree merely to deploy. The script records dirty state and source identity, validates Compose, backs up PostgreSQL, recreates only selected services, and captures health evidence. A branch/worktree is reserved for upstream integration, conflicting candidate assembly, or another independently justified isolation boundary.
- The default target is local Docker Desktop; remote deployment requires an explicit remote request. A clean checkout can still need deployment of a committed revision: compare the intended source with runtime rather than stopping solely because there is no diff.
- A deployment request authorizes commit and push without another confirmation. After every successful Fast, Feature, Candidate or Release deployment, commit the reviewed deployed source, tests and documentation in the current branch and push that branch and any deployment version tag to its configured remote. Preserve unrelated work; image qualification does not exempt publication.
- If already committed, reuse its SHA and push unpublished deployment refs; do not create an empty commit. Verify remote branch/tag targets against the intended commit before reporting completion. Report commit SHA, deployed source identity and push result. If push fails, report the concrete blocker and unfinished publication without asking for authorization again.
- Failed deployment and `-CheckOnly` must not trigger commit/push. Explicit task-level no-commit/no-push instructions still apply.
- For an upstream update, use a separate integration branch/worktree from a complete committed local snapshot. Merge a verified upstream commit; do not rewrite published fork history or use blanket ours/theirs conflict resolution. Test migrations and recovery before release. A new worktree does not include uncommitted work automatically.
- Keep `origin.fetch` on `+refs/heads/*:refs/remotes/origin/*`. Remove completed branches only after proving their tips are included in published `main`, checking open PRs and worktrees, and saving recoverable refs. Preserve dirty or active worktrees; use exact-SHA leases for remote deletion.
- Before removing a worktree, preserve unique ignored configuration, logs and reports and check runtime mounts/processes. On Windows use `git -c core.longpaths=true worktree remove`; detach shared dependency junctions as links only. If removal is partial, preserve the remaining directory in a recovery archive on the same volume instead of forcing recursive deletion through links.

## Checks and evidence

- Reserve disposable tasks with `deployment/local/temp.ps1 -Name <task>` under `S:\tmp\ragflow` and finish with `-Complete`. Save unique results and verified backups under ignored `.local/archives` and `.local/backups`; do not pin completed workspaces. Preserve unmarked directories, active leases/process jobs, runtime mounts and backups. Build workspace/cache protection, expiry/quota rules, cleanup recovery and weekly installation are maintained in [the local guide](deployment/local/README.md). Persistent runtime/watchdog/quality data belong under `.local/runtime` and `.local/quality`; source snapshots, dependency trees and build caches do not.
- Run the narrowest relevant test, lint, or build command after a change.
- Choose test levels by the changed behavior and realistic failure modes; do not add unit tests automatically for every change. Use the smallest set of checks that reliably covers the risks; both unit and integration tests are not mandatory for every change.
- Add unit tests for independent business rules, calculations, state transitions, and meaningful error or boundary cases, especially when those variants are expensive to exercise through integration. Assert observable contracts that survive behavior-preserving refactoring rather than mirroring implementation or mock call sequences.
- Integration tests are sufficient when they cover the relevant behavior and failures at component boundaries, such as database queries, transactions, authorization, serialization, or handler registration. Do not duplicate that coverage with mock-based unit tests for pass-through code unless they catch a distinct realistic failure. Use API/UI checks when the risk concerns the user journey; simple styling or documentation changes usually do not need new unit tests.
- Before adding a test, identify the real failure it catches, whether existing checks already catch it, and what additional confidence it provides. Keep required regression gates and meaningful existing coverage; passing unit tests alone does not prove integration or end-to-end behavior.
- Use prepared environments; on Windows use `.venv/Scripts/python.exe` for Python. Backend validation uses `python -B -m pytest <paths>`, `ruff check <paths>` and `ruff format --check <paths>`. Formatting is an edit, not a read-only check.
- For frontend checks and dependency preparation, follow `web/AGENTS.md` and `web/package.json`.
- For Go changes, start with package-scoped `bash build.sh --test ./path/to/package/...`. Use `--go` or `--all` only when necessary; provision dependencies with `ragflow_deps/download_deps.py` when required.
- Do not default to raw `go test`, `go build`, or IDE Run/Debug for Go in this repo. They often miss the required CGO flags and native static libraries (`office_oxide`, `pdfium-static`, `pdf_oxide`) that `build.sh` wires correctly.
- If Go native builds fail, inspect `build.sh` and `internal/development.md` before changing code. Common environment issues are missing downloaded native deps and missing `lld` on Linux.
- Missing tools or prerequisites mean incomplete verification, not a pass. Do not run live suites against shared production data. Keep generated test artifacts in the disposable workspace configured according to the regression guide.
- In the existing completion report, connect each accepted outcome to its exact verification command/check and result or explicit gap; include source identity, affected consumers and missing prerequisites. When publication or deployment is in scope, link the commit, image digest/source ID, existing CI/deployment receipt and runtime acceptance result as applicable. State verified, failed, incomplete or not requested for the relevant stages; tests, publication and deployment do not establish each other. A focused pass is evidence only for its stated boundary; no duplicate evidence registry is needed.

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
- Verification: check applicable behavior and data boundaries, report the limits of evidence, and never reduce coverage scope, disable required checks or extend exceptions to hide a regression. For authorization, persisted-data/migration or other high-impact changes, make a distinct review pass starting from the original requirements and failure cases before consulting the implementation's explanation. Seek a concrete counterexample; use an independent reviewer when authorized and useful, without automatically spawning another agent or treating self-review as independent acceptance.

## Continuous cleanup and algorithm simplification

- After a coherent change, inspect changed symbols, their module, direct callers, consumers and dynamic entrypoints for newly obsolete owned code. Use repository search and focused tests; no generated architecture report is required. Apply proven cleanup within the authorized task; uncertain usage is a finding to investigate, not permission to delete.
- Converge duplicate owned implementations to one path unless a supported external contract requires both. Prefer deletion over obsolete shims, deprecated APIs (including Go), compatibility-only wrappers and dual-track migration notes. A narrow adapter to an actual upstream boundary remains valid. Verify supported consumers and saved data before removal.
- In `internal/ingestion`, `internal/parser` and `internal/deepdoc`, keep changes centered on their owner; collapse confirmed local duplicate paths while preserving upstream layout and supported profiles.
- Remove dead tests, commented-out code, stale docs and "move later" notes; keep focused tests for behavior that remains valid. Reduce public surface when helpers can be private/internal. Refactor the owning abstraction rather than adjacent compatibility layers.
- Comments, including Go package/doc comments, describe current behavior, runtime paths and invariants rather than migration history.
- Do not equate an unused binding with a removable computation. Check registration imports, decorators, namespace reflection, resource lifetime, persisted DSL and external contracts before removing code.
- Before applying an automated patch, verify that its source snapshot is still current. On regression, undo only that patch without overwriting other edits; do not reset the shared tree.
- Treat complexity, nesting, repeated scans and I/O amplification as investigation signals. For a significant new signal, simplify the owning scenario or document why the complexity expresses required behavior. Do not split code merely to lower a metric.
- Before simplifying an algorithm, state its outputs, errors, ordering, side effects and invariants. Verify before/after behavior; measure runtime, query counts and memory when traversal, data structures or I/O change. Keep one production implementation.
- CI checks and reports only. Planned cleanup automation does not authorize automatic commits, merges, deploys, product retirement or unrelated upstream refactors.
- Apply the same maintenance to the development process: after a repeated agent failure, improve the owning instruction or existing executable check instead of adding another overlapping rule. Review process overhead at the pilot's end or when it causes friction; remove obsolete guidance and unjustified steps while preserving required gates. Extend mechanical checks to the affected owned boundary when warranted, without imposing a new layout or blanket rules on upstream code.
