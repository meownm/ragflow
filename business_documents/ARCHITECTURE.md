# Governed document scenarios

The detailed design, existing implementation state and verification boundaries are
maintained in [SERVICE_REFACTOR_DESIGN.md](S:/ragflow/business_documents/SERVICE_REFACTOR_DESIGN.md).
The application scenarios now own reads, commands, job completion, shared change
application, create/delete, role administration and EVA synchronization. The old
service and its forwarding surface have been removed. Deterministic rules live
in domain; runtime explicitly composes the existing database, asset, EVA and
export adapters. REST and worker share application behavior.

## Scope and acceptance

This design covers the local Business Documents command service and its direct
REST, worker, assignment, evidence, export and test consumers. It preserves the
upstream directory layout, database tables and published HTTP/event contracts.
Persisted jobs remain readable; new snapshots omit only derived UI section_texts,
which the AI adapter already discarded before constructing model input.

An author creates or imports a document, answers immutable questions, reviews
proposals and comments, prepares or applies a change plan, and exports an agreed
revision. Moderators retain their current cross-owner permissions; the shared
readable catalog must not acquire an accidental tenant filter. Jobs retain the
document's tenant identity, version and lease fence.

Acceptance requires one implementation of each workflow rule, database-free
domain/application imports, bounded batch reads instead of per-item SQL,
unchanged replay/rollback and snapshot behavior, and removal of the superseded
service after its consumers are switched. A new EVA pull invalidates the current
review assessment for both the command response and direct command execution.

## Ownership and dependencies

* `business_documents/domain` owns workflow eligibility, review facts, source
  authorization, plan disposition, document content and comment anchors. Its
  inputs and outputs contain values, never Peewee rows or request objects.
* `business_documents/application` owns create/delete, command execution, job
  completion, EVA synchronization and query assembly. Scenarios receive narrow
  persistence/external-operation ports. The existing owner-assignment scenario
  remains the owner of assignment.
* `api/apps/business_documents/adapters` implements database locks, queries,
  writes, current asset loading and external system access. Composition occurs
  at the runtime boundary, not while importing a pure package.
* REST authenticates, validates the wire envelope and maps errors. The worker
  owns claiming, heartbeat and execution; both use the same application rules
  when committing changes. Query assembly does not invoke command execution.

The target scenario modules are `commands`, `job_completion`, `change_application`, `documents`,
`eva_sync` and `queries`. Domain rules are grouped by `workflow`, `review`,
`change_plan` and `comment_anchor`. Persistence separates aggregate writes from
batch reads. No generic handler registry, mixin hierarchy or pass-through
replacement for `BusinessDocumentService` is part of the target design.

The infrastructure-free `document_exchange.identity` module owns canonical EVA
page keys shared by BT, TZ and persistence adapters. Its algorithm and saved-key
format are common; reservations, transactions and migrations remain in
`api/document_exchange`. Application imports never load that API package.

## Review facts and algorithms

A review context belongs to one document state version and transaction/read
operation. Batch-load current-cycle questions, answers, proposals, decisions and
comments, plus the full event stream, then build identity maps once. Historical
entity bodies are unnecessary for current protocol and stale-source rejection;
all event IDs and payloads remain available to snapshots and audit. Traverse ordered events once to obtain
protocol references, latest assessments/mutations, current comment dispositions,
the latest EVA pull and already resolved change inputs. Preserve the most recent
assessment even if its outcome is incomplete.

Active inputs are current-review answers, accepted proposal events, comments
and the most recent current-cycle EVA pull, minus previously applied or
acknowledged inputs. Pending proposal order remains creation order. A resolved
input may not authorize a second change. One active input may authorize several
section operations; it may not also be acknowledged as unchanged.

Open review questions permit partial application after a current assessment.
New feedback still requires reassessment. Ambiguous comments with NEEDS_QUESTION
remain unresolved and are excluded from applicable inputs and pinned change sources.
Applying ready inputs preserves the same review cycle and unanswered questions;
only a review without open questions or pending proposals can become AGREED.

ACCEPT_PROPOSAL records the acceptance and schedules a durable per-proposal work
request atomically. The UI's Apply action uses this command; a repeat retries a
failed request without creating a second decision. DECIDE_PROPOSAL retains its
published decision-only contract for existing clients and explicit aggregate
assessment/application. Published preview commands retain their contract.

Requests are ordered per document. Admission locks the document and builds the
immutable job snapshot against its latest saved revision. The worker and API
use the same admission implementation. A background job owns active_job_id;
feedback may advance state_version but cannot change that cursor, content,
owner or cycle. Completion checks all these fences and the worker lease. An
analysis superseded by newer feedback publishes no entities; coalesced follow-up
analysis uses the newer snapshot. Only the exact accepted decision authorizes
per-proposal application. Failures retain acceptance and do not block later work.

Comments validate against their own immutable revision, including a prior saved
revision. The original quote and revision remain explicit after autosave.
Questions, answers, proposals, comments and decision/cancellation events remain
append-only. CANCEL_PROPOSAL_ACCEPTANCE may remove a queued acceptance, but not
one whose application is already running. Applied events cannot authorize
another change. Assignment, deletion and structural commands wait for queued
work as well as running jobs.

For applicable inputs A, used inputs U, no-change acknowledgements N and active
accepted proposals P, require U and N to be subsets of A, U intersect N to be
empty, A to equal U union N, and P to be a subset of U. Validate snapshot
membership, event kind, review cycle and comment disposition
before these completeness checks, retaining stable error priority/details.
Confirmed comments, author answers and accepted proposals may coordinate changes
across sections. Source section IDs describe context, not authorization limits.
Build snapshot/evidence membership sets once per result.

EVA pull text is review context; accepted proposals and author answers determine
which external changes become requirements. Generation and verification retain
separate model audits. Semantic verification complements deterministic source
authorization; it is not a formal proof of equivalence.
The full verification input is sent without truncation. The provider adapter
counts the serialized input once, reserves 256 tokens for message framing and
assigns all remaining context to the response for every task, including full
document generation, EVA changes, assessment, SQL and semantic verification.
There is no fixed output-token ceiling in the adapter or execution audit.
Native Ollama resolves its model window through `/api/show` before each request,
including quality model overrides, and passes it through `num_ctx` in ordinary
and streaming requests. A stale catalog limit does not override model metadata;
the saved tenant configuration is unchanged. For Ollama behind an
OpenAI-compatible endpoint, `/api/ps` supplies the loaded model's actual context
before each call, replacing a possibly generic 8192 catalog value. Cold models
and other compatible servers retain their configured window. Optional metadata
discovery never requires unrelated providers to implement Ollama endpoints.
Model-family request options merge with configured provider body fields so
thinking controls cannot discard the native context in non-streaming calls.
The shared tokenizer estimates input size; provider capacity errors still fail
the attempt. Qwen3 compatible planning and verification explicitly request
`reasoning_effort=none`, which survives transport filtering and is audited.

Generation and verification use published section roles and confirmed author
placement decisions, independently of section numbers. A confirmed relocation
retains the full definition at its destination and removes its former copy,
while preserving references, explicitly permitted repeats and independent rules.
Pure domain comparison supplies candidate section pairs with similar content,
including unchanged result sections paired with a changed section. Similarity
alone never authorizes deletion or rejects a plan: the single semantic review
checks each candidate against the actual confirmed decision. This remains a
model judgment, not a deterministic proof of free-text relocation intent.
An absent optional section is included through the existing parent-section
operation. Verification receives section titles, including absent template
sections. A semantic rejection of misplaced or missing requirements terminates
the job with concrete source-scoped feedback; it does not authorize rewriting
requirements automatically.
The single semantic check evaluates whether each change follows confirmed inputs,
including changes to a parent together with new subsections. Source validation
does not traverse the AST or compare parent content. New sections
are included in the before/after preview before author confirmation.

The validator returns a change decision. Persistence applies one of three
results: a prepared preview, an audited no-op, or a new immutable revision.
ApplyChanges takes one ChangeMode (APPLY, PREVIEW or CONFIRM), with no independent
flags that permit contradictory modes. Persisted preview_only is translated at
job completion and retains its existing meaning.
Pending proposals keep the review open. Confirmation revalidates the current
version, revision, review cycle, preview age and inputs. No-op finalization must
not manufacture a duplicate revision, including a nonempty plan whose canonical
AST equals the base. Source events are resolved in the no-change event even when
the current revision is retained. Evidence-only AST changes still create revisions.

The generator receives a copy of the complete immutable snapshot: the current
AST, protocol with question options and answers, event history and pinned evidence.
Duplicate revision markdown and flattened section texts are omitted. Active events
are also listed in `active_change_inputs`; `confirmed_requirement_fragments`
summarizes active confirmed text and selected/custom answers with question,
section-purpose and author-selection context.
Historical context does not authorize operations: only active input event IDs do.
The semantic check receives the same complete fragments as individual `sources`,
with the full protocol retained, in one model call. Fragment diagnostics map back
to their original author event before persistence. Active EVA update IDs point to
the complete remote text in the event journal; even a no-op plan is checked against
that comparison input. Imported text does not confirm rejected proposals or
authorize the wholesale replacement of local requirements.
Both the reviewer and planner explicitly identify active EVA imports; an empty
comment list does not cancel the comparison task. Protocol projection excludes
historical structural warnings because the current AST owns those facts;
immutable events and source advisories are retained.
Transport normalization resolves entity aliases and server-owned section hashes;
it never relocates sources, invents an omitted source or declares a no-change
disposition on the model's behalf. Domain validation rejects these omissions.

Change generation requests block operations in `change_plan`: stable block IDs,
insert/replace/delete operations, explicit additions of absent sections,
server-bound base hashes and authorizing source IDs. Untouched blocks are copied
from the immutable base, rather than regenerated. Block identity survives replacements;
insert identity is assigned by the server. Legacy revisions receive deterministic
IDs in their read projection without updating saved history. Persisted section
replacement plans retain their reader. The inline preview aligns changed blocks
by ID and shows their exact before/after text. One
semantic model call reviews all changed sections against active confirmed inputs
and their previous requirements. All unchanged result sections are supplied as
context, including mixed plans that acknowledge an already-satisfied answer. Each
result section appears once, either in the diff or in context; none is truncated. The bounded
PASS/FAIL report contains at most four source/section-scoped issues, not a report
per sentence. The response can use the full remaining model context even though
the requested verdict is compact. The resolved model context is preserved. Oversized input
fails explicitly without truncation or batching.

The worker retries known temporary provider/retrieval/storage failures only,
respecting Retry-After from the original transport error, including SDK exception
chains and provider error headers. Original HTTP status takes precedence over
a generic SDK wrapper status. Access, configuration, corrupt evidence, stale results
and semantic rejection terminate without another model generation. SQL job
retry policy is independent. Document model calls share a durable limit of six
calls and 900 seconds, including queue waits and format repair. Each generation
or verification stage permits one targeted format correction; the original
input and validation diagnostics accompany the response being corrected.
The domain validators and final authorization checks remain mandatory.

The worker holds a 60-second fenced lease and renews it every 20 seconds, independently of the 900-second AI budget. Each renewal opens and releases its database connection inside the heartbeat thread, including rejected leases and SQL errors. The adapter port carries the remaining timeout through wrappers. A valid Retry-After beyond the remaining budget terminates recovery.
SQL model operations have a 30-minute deadline, including the provider transport.
Schema resolution allows 32 minutes for catalog access plus interpretation;
its catalog lookup deadlines remain separate. Synchronous SQL browser requests
allow 33 minutes so they do not expire before the server's model operation.

Before semantic review, the validated candidate and call/audit/repair state are
checkpointed in the existing job.result, without changing immutable job.payload.
A retry of the verifier reuses that candidate, checks the snapshot fingerprint,
document version and worker lease, and never publishes it before PASS. Invalid
verifier JSON or report shape permits one format repair; a valid FAIL does not.
Recovery checkpoints are internal and absent from job/preview HTTP projections.
Lease loss and cancellation stop subsequent calls and publication. A stale job
failure does not overwrite a newer document state. Network calls stay outside
database transactions. The opt-in raise_on_error provider boundary retains
typed failures for this queue; ordinary chat keeps its existing behavior.
Saved M06 qualification evidence replays the saved plan once, binds the final
generator response to that plan after transport normalization, and checks that
the terminal semantic PASS reviews the same sources, before/after text, context
and titles. A transient failure may repeat verification of that same candidate;
one invalid-format verdict may receive exact recorded repair feedback. Every
verification call retains the original input. Semantic FAIL, regeneration after
verification, a second format repair and call-budget overruns invalidate the
proof. Empty, failed or unrelated terminal exchanges cannot substantiate PASS.
The AI assembles the candidate once for semantic verification; completion applies
the diff against the locked current revision and retains final authorization,
version and lease guards. Streamed previews index the base sections once and
hash only the target of each operation. They validate only the current section
shape; they neither assemble the whole AST nor authorize a write. Old prepared
previews must be prepared again when their pinned prompt policy changes.

Preview source labels use one snapshot index for all operations. For K
operations, H snapshot entries and R references this changes repeated
O(K * H + R) indexing to O(H + R). List and revision readers load related records
in batches; they must not fetch all historical jobs merely to find the newest.
Job snapshot assembly uses a revision projection without UI section rendering;
AST, Markdown, hashes and provenance remain compatible with AI/evidence consumers.
Single-document latest-job reads use ordered LIMIT 1; page reads retain ranked batching.

Content normalization owns one AST copy and restores the pinned outline without
copying requirements from children. All published template sections are required,
including in title-only and required-scope documents. Validation fills empty
required sections with `Требования не определены`. Saved revisions are not rewritten.
EVA import accepts the persisted v2 required-scope outline and restores all
sections with the same placeholder, preserving imported requirements.
Draft and review completion treat this placeholder as missing information and
add questions when the model omitted a question; the author supplies information
or confirms absence/applicability.
An existing question with the same gap tag is not repeated. Prompts require the
whole outline and explicit authorization for additional subsections.
Persisted answers retain their section association across intake and review
cycles. For the exact undefined-requirements placeholder, a prior answer prevents
another structural fallback question and removes its structural reminders; the
server does not classify answer text or discard substantive model follow-ups.
Open questions and pending proposals still prevent agreement. Advisory warnings
remain in the saved revision and exports; APPLY_CHANGES is the author's decision
to apply the result and does not require a separate warning confirmation.
Both assessment and review loading use one document-scoped DISTINCT projection
of answered section IDs; historical question/answer bodies remain excluded from
the current-cycle view. This adds one bounded query to each loader. Structural
reminders are derived from the current AST and never replayed from a job's older
protocol when applying changes.
The same section identities are pinned in job snapshots for model context;
they prevent repeated generic completeness questions without claiming that
every answer establishes absence or authorizes a new change.
Template membership uses a set; section hashes retain canonical JSON.

EVA draft preparation converts PlantUML code blocks to native editable macros
using EVA's authenticated SVG renderer. SVG is restricted to inert geometry and
text; rendering failure preserves the code. Reading a native macro restores its
UML source before Markdown conversion, so preview labels and editor chrome do not
affect content comparisons. The prepared HTML is retained for guarded publication
and retry; existing personal-token, source-version and draft checks still apply.

## Transaction and external boundaries

1. Authenticate/authorize before replaying a stored command. Lock the document,
   use its tenant for the ledger, compare the request hash, and replay the exact
   accepted or rejected response.
2. Keep the command savepoint inside the ledger transaction. On a domain
   rejection roll back all child writes/CAS, then persist the rejection in the
   outer transaction. Re-raise unrelated integrity failures.
3. Job completion locks document before job, validates the immutable source
   version and current lease, commits domain changes, then performs the final
   fenced lease update in the same transaction. A lost lease rolls everything
   back. Preserve completed/dead replay behavior.
4. EVA network calls stay outside the database transaction. Recheck permissions,
   version, state and binding under the document lock before either committing
   the fetched snapshot or returning NoChange. NoChange uses the same locked recheck as a new pull. Existing EVA
   publish approval remains a separate workflow.
5. Keep export reservation/read-back/commit and durable cleanup stages. No
   simplification may turn cross-store failure into a leaked or missing artifact.
6. Contexts are request-local. Reload or update facts after a mutation; do not
   cache mutable state across requests. Preserve old revision-author/EVA-binding
   reconstruction until stored-data compatibility is proven.

## Verification

Baseline source: `d1a7fd286bae`; the service blob is identical at current HEAD
`a99d1748a0c25e249fba30b21c6a7816d65de78a`. Git-blob-content SHA-256:
`7bc0ef80a750df0802d23318ee20083da235b97147442ae0866de243d43207a0`.
The baseline had 3221 lines and 96 class methods. The service is now deleted;
no permanent facade or second EVA path remains.
Recorded baseline: the original service unit module passed 90 tests. Isolated SQLite measurements:
list sizes 1/10/20 require 6/33/63 SELECTs for intake documents with one owner and
no EVA link; an empty-review GET requires 21 SELECTs; a job snapshot requires 19;
source validation for 1/5/10 one-comment operations requires 3/15/30 SELECTs.
These are in-process baselines, not deployed performance measurements.

Verification includes pure review/plan/anchor cases, import isolation, query
counts/time/memory on reproducible synthetic data, existing command/API/worker
and golden-dialogue tests, and disposable PostgreSQL/MinIO concurrency tests.
Required negatives include cross-document/cycle sources, stale section hashes,
omitted/duplicate dispositions, partial review, stale/expired preview, lost
lease, replay conflicts, rollback, shared reads and forbidden mutations. Preserve
source-event ordering, error envelopes and persisted job payload contracts.

Current verification: 469 unit/API/golden checks passed with 82.97% combined
coverage; all 50 disposable PostgreSQL/MinIO checks passed, including stale-export
delete/recovery and EVA network-window owner/version/rebind races. Pure imports
and preserved rollback/source-order contracts are covered by the focused lanes.
After correcting only timestamp fixtures to account for ORM clock overrides,
6 SQLite query tests and 6 PostgreSQL query/review tests were rerun on the final fixtures.

Preview reads only its scoped base revision and requires three SELECTs instead
of five. Apply makes one full AST copy instead of two for canonical inputs;
300 differential cases preserve results and error details. Occupancy needs one
LEFT JOIN instead of two reads. Current measurements, source hashes and exact
commands are recorded in the detailed design. No deployment or external live
EVA/LLM/browser acceptance is implied by these results.
