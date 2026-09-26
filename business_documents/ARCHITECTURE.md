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

For active inputs A, used inputs U, no-change acknowledgements N and active
accepted proposals P, require U and N to be subsets of A, U intersect N to be
empty, A to equal U union N, and P to be a subset of U. Validate snapshot
membership, event kind, review cycle, section restriction and comment disposition
before these completeness checks, retaining stable error priority/details.
Confirmed comments may coordinate changes across sections; question/proposal
targets remain strict. Build snapshot/evidence membership sets once per result.

The validator returns a change decision. Persistence applies one of three
results: a prepared preview, an audited no-op, or a new immutable revision.
ApplyChanges takes one ChangeMode (APPLY, PREVIEW or CONFIRM), with no independent
flags that permit contradictory modes. Persisted preview_only is translated at
job completion and retains its existing meaning.
Pending proposals keep the review open. Confirmation revalidates the current
version, revision, review cycle, preview age and inputs. No-op finalization must
not manufacture a duplicate revision.

Preview source labels use one snapshot index for all operations. For K
operations, H snapshot entries and R references this changes repeated
O(K * H + R) indexing to O(H + R). List and revision readers load related records
in batches; they must not fetch all historical jobs merely to find the newest.
Job snapshot assembly uses a revision projection without UI section rendering;
AST, Markdown, hashes and provenance remain compatible with AI/evidence consumers.
Single-document latest-job reads use ordered LIMIT 1; page reads retain ranked batching.

Content normalization owns one AST copy and indexes the first substantive block
of each type under each template ancestor. Required empty parents select the
earliest allowed block by its original position, before section sorting. An
inherited block is copied so a parent and child do not share mutable content.
Template membership uses a set; schema validation selects its first error with
a minimum instead of sorting every error. Section hashes retain canonical JSON.

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
