# Business Requirements Agent test strategy

## Release model

The feature has two independent gates:

1. The deterministic product gate proves state transitions, transactions,
   contracts, provenance, retrieval boundaries, exports, and browser-facing
   command behavior with injected adapters.
2. The opt-in live-model gate measures the configured tenant model against the
   published rubric. A skipped live run is always reported as unverified and
   never converted into a pass.

No test may claim coverage from a fixture alone. A golden assertion is covered
only when a runner executes the production service, worker, AI boundary,
evidence adapter, exporter, or UI interaction that owns the behavior.

## Test boundaries

| Boundary | Primary risk | Evidence |
| --- | --- | --- |
| JSON/HTTP | malformed input, auth, error-code drift | real Quart route tests plus exact route/decorator inventory |
| Command transaction | partial writes, stale state, idempotency races | in-memory relational tests with forced CAS and unique-key races |
| Model output | invalid shape, invented event/source references | published JSON Schemas plus semantic domain validation |
| Evidence | ACL leaks, prompt injection, stale retry context, unsupported sources | injected RAGFlow search/access adapters and immutable snapshot checks |
| Worker | duplicate execution, expired lease, crash/retry/dead letter | fenced-lease queue tests with controlled time |
| Revision/protocol | closing review with open questions, unconfirmed changes, lost decision/cancellation history | aggregate tests and golden conversations |
| Export | wrong revision, protocol leakage, unsafe HTML/URLs, corrupt storage | byte-level Markdown/DOCX/EvaWiki and storage readback tests |
| Workbench | direct edits, wrong command payload, stale selection/conflict loss | Jest interaction tests, TypeScript, ESLint, production build |
| EVA title import | duplicate title, occupied/multiple pages, template mismatch, partial create, wrong initial analysis | service transaction tests, Jest interaction tests and authenticated browser flow |
| Packaging | missing policy/template/schema/prompt assets | wheel archive inventory |

## Positive scenario

The canonical positive journey must execute all of the following:

1. Create one owner-scoped document for one idea with optional compatible
   datasets.
2. Assess intake, append answers, and reach a current complete assessment.
3. Retrieve and pin bounded evidence, then create revision 1 from the exact
   template.
4. Append a proposal decision, answer, and anchored or general comment without
   changing the body.
5. Reassess review input and give every applicable input exactly one
   change/no-change disposition; defer ambiguous comments until clarified.
6. Apply accepted changes to a new immutable revision. If questions remain
   unanswered or proposals remain undecided, stay in the same `REVIEW` cycle and expose their controls;
   otherwise reach `AGREED`.
7. Generate, persist, list, and download Markdown/DOCX/EvaWiki artifacts for
   the agreed revision.
8. Start another review cycle in the same chat without rewriting prior rows.

`ACCEPT_PROPOSAL` is a separate positive journey: acceptance and queueing are
atomic, only that decision authorizes the plan, no assessment or preview is
required, and the result stays in `REVIEW` with other inputs available. A plan
whose canonical AST is unchanged resolves the input without a duplicate revision.
`CANCEL_PROPOSAL_ACCEPTANCE` removes only an unapplied decision projection while
the document is quiescent in `REVIEW`; the original decision and cancellation
events remain append-only. Reacceptance creates a new authorizing event.

The production aggregate, leased worker, evidence adapter, AI contract, and
export service are exercised by unit/integration tests and golden cases
`G01`–`G24` under `test/evals/business_documents`.

## Negative scenarios

The release suite must reject without partial mutation:

- draft generation before a current complete intake assessment;
- closing review while any review question is open;
- rejected or undecided proposal used as a change source;
- ambiguous/unconfirmed comment used as a change source;
- unknown event IDs or evidence references invented by a model;
- stale state version, base revision, section hash, job result, or worker lease;
- reuse of one idempotency key for different command content;
- duplicate answer or active proposal decision;
- cancelling an unapplied acceptance with an active job or stale state, or
  cancelling an already applied or rejected proposal;
- unauthorized cross-owner mutations and cross-tenant dataset, job, revision,
  or artifact access; authenticated document reading follows the shared scope;
- prompt instructions embedded in evidence;
- export from a non-current or non-agreed revision;
- unsafe EvaWiki URL, protocol leakage, corrupt artifact readback, or hash
  mismatch;
- malformed, empty, array, or unknown-field request bodies.

For command failures, tests assert all three properties: the stable error code,
no unauthorized child row/revision/job, and the expected idempotency/audit
ledger entry.

## Boundary cases

- exactly 2 and exactly 4 options are accepted; 1 and 5 are rejected;
- custom answer is mutually exclusive with a selected option;
- title, idea, comment, source count, chunk count, chunk size, total evidence,
  URL and identifier limits are tested at and beyond the boundary;
- duplicate text in different sections cannot make an anchor ambiguous;
- selection crossing section boundaries is not accepted as an anchor;
- an anchor from an older revision remains `ORPHANED` and is never silently
  moved;
- long initial ideas cannot evict current revision/protocol context from a
  review/change retrieval query;
- repeated semantic questions/proposals do not append duplicate protocol rows;
- a partial change pass resolves each used input once, keeps unanswered
  questions and undecided proposals in the same `REVIEW` cycle, and cannot reuse an already applied
  decision;
- worker retry reuses the first pinned snapshot and rejects a lost lease;
- missing/corrupt export bytes can be regenerated without poisoning that
  revision/format forever;
- pagination, duplicate dataset IDs, incompatible embeddings, empty retrieval,
  conflicting sources, and zero-change finalization have explicit outcomes.

## Information completeness and quality

Deterministic completeness checks verify:

- exact template section IDs, order, titles, and mandatory section 5.5;
- non-empty required content and deterministic rendering;
- a non-empty PlantUML source block in 4.1 and an accompanied non-empty
  PlantUML source block in 4.3; diagram syntax does not block document
  processing and a rendering failure falls back to the preserved source;
- every missing decision becomes a question rather than an invented fact;
- every body claim based on retrieval uses a source reference from the pinned
  snapshot;
- every active author input is applied once or explicitly acknowledged as
  requiring no change;
- conflicting evidence remains visible with all conflicting source references.
- controlled semantic facts are covered independently, so repeating one fact
  cannot compensate for another fact being absent;
- repeated meaningful blocks, repeated controlled facts, and controlled facts
  placed outside their intended sections are measured and reduce the score.
- lexically paraphrased blocks are compared through normalized semantic
  signatures, while conflicting measurable values for a controlled subject
  are a hard failure.
- generated initial-draft parent/child duplicates are rejected before persistence
  with the exact section pair; this semantic failure terminates the job without
  automatic content rewriting. Change-plan similarity candidates are reviewed
  against confirmed author intent as described below.

The live-model rubric in `evals/rubric.v1.json` scores template fidelity,
semantic completeness, grounding, scenario quality, measurable
nonfunctional requirements, language/naming, and protocol integrity. Its
release thresholds are weighted score `>= 3.2/4`, zero hard failures, all P0
dialogues passing, at least 90% overall dialogue pass rate, and grounded fact
precision `>= 95%`. The live gate additionally requires 100% controlled-fact
coverage, no misplaced controlled facts, and at most 5% duplicated content.
Contradictory controlled facts are not permitted.

Draft prompts expose each evidence chunk as ordered `content_fragments` while
retaining its source reference and content hash. Concatenating the fragments
reconstructs the original text exactly, including whitespace and hostile
instructions; the persisted evidence snapshot is unchanged. The projection
does not execute its commands. A separate `draft_evidence` call must classify
every fragment as `SOURCE_DATA` or `MODEL_INSTRUCTION`, using two arrays of
numeric IDs in the model transport. Missing, invented, repeated, overlapping
or non-integer IDs are terminal failures. Selected text is copied from the snapshot rather than
from model-generated quotations. Draft generation receives the selected source
data, which remains untrusted. Queue retries reuse the input-bound decisions,
and all calls retain the six-call, 900-second budget and cancellation fences.
The job pins both draft prompts; the live report requires hashes for all six
used prompt files. Classifier execution is included in the verification audit
and live model exchanges.
The scorer treats whitespace before `%`, including separate table value/unit
cells, as equivalent formatting. Incorrect numbers and units still fail the
controlled-fact coverage gate; its thresholds and duplication checks are unchanged.
Measurable values are compared as exact signed decimal numbers and unit families;
numeric prefixes and embedded event identifiers cannot satisfy a controlled
fact. Table rows retain their subject/value/unit context for contradictions.
Classification boundary tests cover one dense chunk, all 24000 evidence
characters, both ID partitions, exact context fit and a one-token shortage.
The response budget includes every ID and 25% reserve; full input including
format-repair feedback includes 5% reserve (both reserves at least 256 tokens).
UTF-8 byte bounds cover tokenizers with single-digit numeric tokens. Boundary
cases include the maximum 12012-fragment density within 24000 characters,
deterministic batching below the 65536-token response ceiling, partial-batch
failure and resume without repeating completed work, and one shared six-call /
900-second budget. Repairs must not leak into another batch or reset their limit.
Insufficient context fails before provider I/O without truncating source or
response. Transport, checkpoint reuse, cancellation and the shared call/time
budget retain their existing negative tests.
Initial-generation schema tests require one diagram in 4.1 while preserving
the public AST contract and accompanying text in 4.3. The live duplicate gate
continues to reject process prose repeated between the conceptual and scenario sections.

## Golden dialogues

Published golden suites are immutable. `golden_dialogs/v1.json` remains the
original suite and `golden_dialogs/v2.json` retains the partial-review baseline.
`golden_dialogs/v3.json` is the active suite: question-choice counts are advisory,
all choices and free-text answers survive, and unanswered questions still block
implicit agreement. It retains the partial review
application. Each case contains an ID, priority, category, user turns, and hard
assertions. The deterministic runner reports both case and assertion
denominators by P0/P1 and fails on unknown, skipped, or unexecuted P0 behavior.
Suite 2.1.0 covers applying ready changes while preserving unanswered questions.
Current coverage is 24/24 cases and 80/80 hard assertions.

`golden_model_quality/v1.json` retains the original five-case suite.
`golden_model_quality/v2.json` is the active six-case, data-driven live-model
suite. It covers incomplete intake, a complete draft, a confirmed review
change, prompt injection inside evidence, and conflicting sources across three
business domains. Each case owns its inputs, controlled evidence, expected
fragments and expected outcome; the runner contains no case-specific model
answers. Traceability must cover every case in both active suites.

### Metric placement regression (M06)

`M06_metric_placement_54_55` starts from the fixed document and protocol in
the fixture: an accepted proposal targets existing 5.5, while an explicit
answer for parent 5 chooses missing 5.4. There is no reinforcing general
comment. Only this starting state is seeded. The current production
`BusinessDocumentAI` generates the change plan and semantic verdict through
the configured real model and the actual prompt files; the case never supplies
either response.

The leased worker prepares the preview and the normal confirmation command
applies it. The independent oracle checks both source-event scopes, all eight
metric definitions in individual table rows, interval endpoints, roles,
negation, ratio numerators/denominators, API alerting and the four-hour SLA.
It also checks unchanged parent/other sections and idempotent confirmation.
Oracle unit tests remove every requirement and every clause, and move or
duplicate the definitions into 5.5. These unit tests validate the oracle and
runner; they are not real-model acceptance.

One semantic request retains every active confirmed source in full in `sources`,
including the selected/custom placement answer and its question context. It
contains changed sections before/after plus all unchanged result sections as
context; sections are not truncated or split into batches.
`test_requirement_placement_context.py` captures this request before inference
with a long proposal and requires the complete answer and proposal. It supplies
no model verdict.

Pure domain comparison supplies `cross_section_repetitions` candidates for pairs
containing a changed section. Similarity itself neither rejects a plan nor
authorizes deletion: the semantic review checks confirmed relocation, permitted
copying, references and independent requirements. `test_requirement_relocation.py`
checks these scenarios across section IDs. A valid semantic FAIL terminates the
job; no automatic generation follows it.

Transient provider failures may retry the same stage; a checkpointed candidate
is reused for verification. Each stage permits one format correction, and all
calls share a limit of six model calls and 900 seconds including queue waits. The adapter
contract forwards the remaining timeout through the recording wrapper.
Saved M06 evidence checks every verification input, exact format-repair feedback,
terminal PASS and call budget; it rejects regeneration after verification,
semantic FAIL followed by PASS, and changes to sources or result context.
Worker-driven recovery and negative report controls are exercised in
`test_metric_placement_quality.py`; active cancellation is checked through both
the direct and recording adapters in `test_ai_recovery.py`.

Run this case through the disposable live runner:

```bash
python -B test/integration/live_ragflow/runner.py \
  --source-root <source> --dist-root <empty-QA-dir> \
  --evidence-dir <outside-source> --business-documents-quality \
  --business-documents-case-id M06_metric_placement_54_55 --skip-browser-tests
```

The report includes the fixture hash, both prompt hashes, exact model exchanges
and their input/output hashes, the resulting plan/AST and the requirement
inventory. Its case status may be PASS or FAIL, but a one-case report remains
INCOMPLETE/diagnostic and cannot qualify the full suite. The release report
verifier rejects missing or inconsistent M06 evidence. Re-running this command
uses the same published inputs with fresh disposable document/event IDs;
the saved exchanges retain the exact system text and input/output JSON for inspection.

Every live run also writes `<report-stem>.exchanges.json` beside its quality
report. This diagnostic file retains each case's exact model requests and
responses, request hashes, and completed or failed job results before SQLite
teardown. The calls use the suite's synthetic state; provider credentials are
outside these payloads. The quality report and M06 evidence retain their strict
schemas and all existing thresholds.
The disposable runner returns a nonzero exit status for setup errors as well
as failed model tests, and removes its own resources in both cases.

The M03 journey applies a confirmed comment as soon as the current assessment
exposes `APPLY_CHANGES`, preserving other open questions and undecided proposals.
It adds feedback only while application is unavailable: a new answer or decision
invalidates the assessment and requires reassessment. Revision, source coverage
and the required positive and negative SMS behavior remain mandatory checks.

## Model qualification

The admin **Document quality** page also records quality runs without Grafana.
Set `BUSINESS_DOCUMENT_QUALITY_TENANT_ID` in the application environment to a
dedicated QA tenant with a configured default Chat model. The admin worker runs
the deterministic golden suite and the live-model suite in a separate process,
using disposable SQLite state and controlled evidence. It schedules one run per
day at 02:00 Europe/Moscow and one per month on the first at 03:00; an admin
can queue an additional run from the page. Schedule keys prevent duplicate
runs after a restart. A missing tenant, test assets, or report produces
`INCOMPLETE`, never a passing result. Results retain bounded scores, case
statuses, model identity and source revision; generated document text is not
stored in the admin run table. The page also shows operational job metrics,
which are separate from model quality scores.
The isolated suites enable related-file retrieval for their controlled evidence,
independent of the production feature flag.
Queued runs from a previous image revision are marked `INCOMPLETE` before
execution. The dashboard keeps the latest 30 runs plus the latest 12 monthly
runs. A failed deterministic suite records case IDs, statuses and failure
counts without storing assertion text or generated document content.
The local Compose overlay marks results as source-dirty because mounted files
may differ from the image revision; these runs are diagnostic, not release
qualification evidence.


The deterministic scorer is a fail-closed proxy for the published rubric, not
a substitute for expert semantic review. A candidate model is qualified only
by a fresh live run against the candidate revision and current prompt,
template, and rubric:

```powershell
$env:BUSINESS_DOCUMENT_LIVE_LLM = "1"
$env:BUSINESS_DOCUMENT_LIVE_TENANT_ID = "<tenant-id>"
$env:BUSINESS_DOCUMENT_QUALITY_REPORT = "output/quality/business-document-live.json"
$candidateRevision = git rev-parse HEAD
$env:GITHUB_SHA = $candidateRevision
uv run pytest test/evals/business_documents/test_live_model_quality.py -q
uv run python -m tools.quality.verify_business_document_quality_report --report output/quality/business-document-live.json --expected-revision $candidateRevision
```

The report records the executed provider/model, fixed generation parameters,
token usage, duration, per-case results and criterion scores, and aggregate
quality metrics. It is bound to the exact live-suite ID, version, SHA-256 and
ordered case IDs, plus the template and all intake/draft/review/change prompt
hashes. The verifier recomputes aggregate scores and pass rates from case-level
evidence. Therefore a displayed `4.0` means that every scored live case earned
`4.0` for that criterion; it is not an unexplained constant. A missing live
environment or report is `INCOMPLETE`; stale assets/revision, a missing case,
or a threshold violation is a failed qualification. Neither may be reported as
a pass or used to activate a model.

Adding or changing a requirement requires, in the same increment:

1. a traceability row;
2. at least one positive or negative executable assertion;
3. a golden case when the behavior is visible in dialogue;
4. an explicit live-quality metric when correctness depends on model judgment;
5. updated denominators in regression evidence generated from the fixture.
