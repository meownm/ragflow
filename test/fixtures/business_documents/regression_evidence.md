# Business requirements agent: regression evidence

Base run date: 2026-08-26; deterministic golden gate refreshed 2026-09-06;
five-case live-model golden suite refreshed 2026-09-21.
Environment: local Windows checkout `S:\ragflow`,
in-memory SQLite, injected AI/RAGFlow dataset-search/object-storage adapters,
mocked frontend HTTP. No production credentials or customer data were used.

## Verdict

`pass` for the deterministic scripted state-machine gate and the recorded
representative real-model quality lane. All 24 current golden cases
and all 78 hard assertions execute through the composed Business Documents application scenarios, the
leased worker, injected `BusinessDocumentAI`, pinned Evidence, or the concrete
export service. The live-quality verdict is limited to the exact model, source
snapshot and controlled Evidence recorded below.

## Executed lanes

| Lane | Command/evidence | Result |
| --- | --- | --- |
| Deterministic scripted state-machine gate | `uv run pytest -q test/evals/business_documents/test_golden_dialogue_harness.py` | P0 19/19 and 64/64 assertions; P1 5/5 and 14/14 assertions; all 24/24 and 78/78 |
| Live-quality scorer unit/config | `uv run pytest -q test/evals/business_documents/test_live_quality_scorer.py` | 8 passed together with the golden harness; weighted rubric, controlled-reference precision, canonical PlantUML/BPMN fixture shape, hard-failure subset, honest config gating |
| Real-model golden quality | isolated opt-in execution of `test_live_model_quality.py::test_live_model_golden_suite` | 5/5 P0 cases passed in 522.33 s with `qwen3.8:latest`; weighted score 3.95, grounded precision and semantic coverage 1.0, no duplication, misplacement, contradictions or hard failures; report source is the explicitly recorded dirty revision |
| Domain/worker/evidence/export/API/assets | focused coverage plus full Python unit regression | 171 passed at 79.94% focused coverage; full unit run 2814 passed, 35 skipped and 167 subtests passed |
| Frontend Workbench and client | full Jest run over current frontend | 23 suites and 170 tests passed |
| Mocked production-build browser journeys | `test/run_browser_regression.py` | Chromium 52/52, Firefox 52/52 and WebKit 52/52 passed sequentially |
| Frontend production build | `pnpm build` | passed; 13,226 modules transformed |
| Static checks | Ruff for affected Python, ESLint and TypeScript gates | passed; ESLint retained 138 pre-existing warnings and reported no errors |
| Wheel package data | `uv build --wheel` plus archive inventory | passed; representative policy/template/schema/prompt/golden assets present without package-data ambiguity |

## Coverage state

- API: `covered` for nine authenticated routes: create/list/get, commands,
  revisions list/get, jobs, export list and export download.
- Worker: `covered` for lease fencing, stale recovery, retry/backoff,
  dead-letter, restart after failure, singleton start and wake.
- Evidence: `covered` deterministically for dataset ACL, bounded retrieval,
  immutable snapshot/hash/source refs, prompt-injection-as-data and conflicting
  sources. G20/G21 use the real evidence component and captured AI input.
- Export: `covered` for agreed revision gating, durable write/readback,
  idempotency, list/download ownership, content hash, Markdown, DOCX ZIP and
  exact EvaWiki rendering with safe URLs and protocol exclusion.
- UI: `covered_by_lower_level` for create/resume, read-only body,
  allowed-command gating, loading/empty/error/conflict/busy states and artifact
  links. A running-stack Playwright journey is outside this deterministic gate.
- Golden requirements: P0 `19/19` and `64/64` assertions; P1 `5/5` and
  `14/14` assertions; all cases `24/24` and `78/78` assertions.
- Live quality: the separate opt-in suite runs the real tenant chat model
  through incomplete intake, complete draft, confirmed review change, hostile
  evidence and conflicting sources. It validates the published template,
  protocol separation, monitoring, question bounds, grounding and controlled
  semantic facts from pinned Evidence snapshots. The 2026-09-21 run completed
  all five cases with Qwen3.8 and met the configured thresholds; its report is
  bound to the dirty source revision, suite hash, ordered case IDs and prompt
  hashes and is not evidence for a later committed candidate.

## Stop points and residual risk

- Live LLM quality requires both `BUSINESS_DOCUMENT_LIVE_LLM=1` and
  `BUSINESS_DOCUMENT_LIVE_TENANT_ID=<tenant>`. It skips by default, fails
  explicitly when enabled without a tenant, and is never counted as passed
  unless the real-model test actually completes.
- The exact pass used `qwen2.5:14b-instruct`, digest
  `7cdf5a0187d5c58cc5d369b255592f7841d1c4696d45a8c8a9489440385b22f6`.
  The smaller installed Qwen2.5 7B produced incomplete/invalid drafts in the
  same lane and is not represented as a pass.
- The live intake-to-draft scorer covers only draft-local hard failures
  (`UNSUPPORTED_SECTION_INVENTED`, `REQUIRED_MONITORING_MISSING`,
  `EVIDENCE_INSTRUCTION_EXECUTED`). Lifecycle hard failures continue to be
  covered by deterministic state-machine tests; this one representative run
  does not claim the rubric's P0/all-case live-suite rates.
- No authenticated live HTTP/browser stack was provisioned in this lane.
- The golden fixture currently contains 19 P0 cases, not 17; counts are derived
  from the fixture at runtime to prevent stale release denominators.
