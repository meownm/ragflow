# Business requirements agent: requirements traceability

Status vocabulary: `covered`, `covered_by_lower_level`, `gap`,
`not_applicable`.  This matrix is intentionally executable-test oriented: a
documented behavior is not considered covered by a UI rendering alone.

| Requirement | Primary surface | Automated evidence | State |
| --- | --- | --- | --- |
| One chat maps to one idea/document | Domain persistence | `test_business_document_service.py::test_shared_access_list_and_server_assigned_chat` | covered |
| No draft before all intake questions are closed | Command gate | `test_business_document_service.py::test_draft_requires_complete_assessment_after_last_intake_answer` | covered |
| Questions contain two to four answer options | JSON Schema and completion transaction | `test_business_requirements_assets.py::test_contract_schemas_compile_and_question_bounds_are_enforced`; `test_business_document_service.py::test_question_schema_boundary_rejects_five_options_and_rolls_back` | covered |
| Author can choose an option or provide a custom answer; a published answer cannot be replaced | Domain command | `test_business_document_service.py::test_answers_cannot_be_replaced_and_current_proposal_decisions_cannot_be_overwritten` plus negative answer-boundary tests | covered |
| Author cannot edit the body directly | REST command allowlist and read-only UI document pane | `test_business_document_api_contract.py`; `web/src/pages/business-documents/index.test.tsx` | covered_by_lower_level |
| Ready inputs may be applied while review questions remain open; unanswered inputs stay in the same REVIEW cycle | Review readiness and immutable revisions | `test_business_document_service.py::test_partial_application_defers_ambiguous_comments_until_their_question_is_answered`; golden case `G08` | covered |
| One-click acceptance applies only the selected proposal without assessment or preview and keeps REVIEW open | Command transaction, scoped worker snapshot and UI | `test_accept_proposal.py::test_accept_starts_application_without_assessment_or_preview_and_preserves_other_inputs`; `web/src/pages/business-documents/index.test.tsx` | covered |
| Unapplied acceptance can be cancelled only in quiescent REVIEW; reacceptance cannot reuse the revoked source | Current decision projection and immutable audit events | `test_proposal_acceptance_cancellation.py::test_cancel_and_reaccept_preserves_history_and_excludes_cancelled_input`; `test_cancel_guards_preserve_current_decision_and_text`; `test_cancel_invalidates_prepared_changes_and_old_authorizing_source` | covered |
| Only accepted proposals may authorize a change | Source-event validator | `test_business_document_service.py::test_rejected_proposal_cannot_authorize_change` | covered |
| Partial proposal application keeps remaining decisions open without reusing resolved inputs | Review lifecycle and change-source validator | `test_business_document_service.py::test_partial_proposal_changes_keep_review_open_and_apply_each_decision_once`; golden case `G07` | covered |
| Questions, answers, proposals, comments, and decision/cancellation events are append-only; the current unique decision row is a removable projection | Protocol models, event history and cancellation transaction | `test_business_document_service.py::test_full_workflow_is_versioned_idempotent_and_append_only`; `test_proposal_acceptance_cancellation.py::test_cancel_and_reaccept_preserves_history_and_excludes_cancelled_input`; `test_cancel_rolls_back_if_audit_write_fails` | covered |
| Comment may target a selected fragment in the current revision | Comment command and UI selection | service anchor negative/boundary tests; `web/src/pages/business-documents/index.test.tsx` | covered |
| Anchored comments survive revision changes without silent re-anchoring | Projection anchor lifecycle | service anchor lifecycle tests; golden case `G11`; orphan marker in `ProtocolPane` | covered |
| Draft follows only the published business-requirements template | AST validator | `test_business_requirements_assets.py::test_template_preserves_published_semantic_outline`; required-section/template mismatch tests | covered |
| Section 4.1 contains non-empty PlantUML source; a renderer failure preserves it as visible code | AST presence check and UI fallback | `test_business_document_ai_export_boundaries.py::test_document_ast_preserves_plantuml_source_without_parsing_it`; diagram component fallback tests | covered |
| Section 4.3 contains accompanying text and non-empty PlantUML source without syntax-gating document processing | AST presence check and UI fallback | `test_business_document_service.py::test_required_sections_cannot_be_empty_and_child_headings_are_nested`; diagram component fallback tests | covered |
| Section 5.5 Monitoring is mandatory | Template and AST validator | `test_business_requirements_assets.py::test_template_preserves_published_semantic_outline`; required-section test | covered |
| Revisions are immutable and stale AI results cannot commit | Optimistic state version and hashes | `test_business_document_service.py::test_stale_worker_result_cannot_create_revision`; section-hash test | covered |
| Same idempotency key cannot create duplicate jobs/events | Command ledger | idempotency tests in `test_business_document_service.py`; opt-in PostgreSQL races in `test/integration/test_business_document_postgres.py` | covered |
| Authenticated authors can discover and open all business documents, but edit only their own; moderators may edit other authors' documents | Shared document query scope and command authorization | `test_business_document_service.py::test_authors_can_open_foreign_documents_while_unknown_ids_stay_non_enumerable`; `test_business_document_service.py::test_business_document_role_matrix_controls_create_edit_delete_and_assignment` | covered |
| Authors cannot delete documents; extended moderators and administrators can delete and assign documents | Role policy, DELETE API and guarded UI action | `test_business_document_service.py::test_business_document_role_matrix_controls_create_edit_delete_and_assignment`; `test_business_document_service.py::test_extended_moderator_can_delete_document_and_all_owned_rows_are_removed`; `test_business_document_service.py::test_extended_moderator_assigns_document_and_admin_manages_document_roles`; `web/src/pages/business-documents/index.test.tsx` | covered |
| Revision history identifies the user whose answer, decision, comment or EVA synchronization contributed to the revision | Event actor audit and history UI | `test_business_document_service.py::test_confirmed_anchored_comment_may_request_a_cross_section_change`; `web/src/pages/business-documents/index.test.tsx` | covered |
| A document with active background work cannot be deleted | Delete command gate | `test_business_document_service.py::test_admin_cannot_delete_document_while_background_operation_is_active` | covered |
| Every REST route is authenticated | REST decorators | `test_business_document_api_contract.py::test_http_surface_is_exact_and_every_route_requires_login` | covered |
| Export is possible only from the current agreed revision | Command gate | export lifecycle/revision tests in `test_business_document_service.py` | covered |
| EvaWiki excludes the agreement protocol | Export worker contract | `test_business_document_worker_exports.py::test_eva_wiki_exact_shape_escapes_content_and_excludes_protocol`; executable golden G22/G23 | covered |
| Word, Markdown and EvaWiki artifacts can be downloaded | Export worker integration | worker/export artifact tests cover durable storage, hash, ACL, DOCX ZIP, list/download; API contract covers list/download routes | covered |
| Uploaded files are evidence, never executable instructions | Pinned RAGFlow dataset evidence | executable golden G20 runs `BusinessDocumentEvidence` and injected AI, then verifies pinned chunk/source-ref/hash/audit and unchanged lifecycle | covered |
| Continue the same document in the same chat | Review-cycle transition | full workflow/start-review tests in `test_business_document_service.py`; golden case `G24` | covered |
| Deterministic golden dialogues protect orchestration and state-machine invariants | Eval runner | `test_golden_dialogue_harness.py::test_release_gate_executes_every_p0_assertion_and_reports_all_case_rate` executes all 24 cases through the real service/worker with scripted AI outputs | covered |
| Live model passes the active versioned six-case suite for intake, drafting, review changes, hostile evidence, conflicting sources and metric placement | Opt-in live eval | `test_live_model_quality.py::test_live_model_golden_suite`; the generated report is bound to the suite hash, ordered case IDs, prompts, template, model audit and per-case scores | covered by executable gate; requires a fresh candidate run |
| Choosing missing 5.4 preserves all accepted metric definitions and independent monitoring in 5.5 with valid source scopes | Real planner/verifier and worker confirmation | `M06_metric_placement_54_55` in `golden_model_quality/v2.json`; `test_metric_placement_quality.py` checks every requirement clause and invalid source/evidence controls | covered by executable gate; single-case runs remain diagnostic |
| Weighted rubric and controlled-reference precision are deterministic and regression-tested | Eval scorer | `test_live_quality_scorer.py` covers config gating, weighted criteria, protocol separation, monitoring, canonical PlantUML/BPMN fixture shape, draft-local hard failures and unsupported measurable claims | covered |

## Coverage boundaries

- API inventory: 21 protected routes under `/api/v1/business-documents`,
  including role-aware paginated document list, privileged hard delete,
  jobs, export list and role-checked download.
- UI inventory: create screen plus protected workbench, document pane,
  protocol pane, loading/error/conflict/busy states.
- Persona inventory: document owner, collaborating author and administrator are
  automated at the domain boundary; unauthenticated access is enforced by the
  shared `login_required` decorator and statically guarded for every route.
- Deterministic worker, pinned dataset retrieval, Markdown/DOCX/EvaWiki and
  object-storage contracts are covered with injected adapters. The scripted
  golden gate is a state-machine gate, not a model-quality claim.
- Real PostgreSQL transaction tests use `BUSINESS_DOCUMENT_TEST_POSTGRES_DSN`
  and a disposable schema per test. They exercise simultaneous same-key replay,
  different-key optimistic conflicts and exclusive worker claims on independent
  connections. They skip when the DSN is absent; SQLite tests alone do not
  demonstrate PostgreSQL concurrency behavior.
- The separate live lane is enabled only by
  `BUSINESS_DOCUMENT_LIVE_LLM=1` together with an explicit
  `BUSINESS_DOCUMENT_LIVE_TENANT_ID`. Flag `1` without a tenant fails instead
  of silently skipping. With the flag disabled, the test honestly skips.
- The recorded live-quality pass is one representative controlled-evidence run,
  not a claim that arbitrary models meet the rubric. The installed 7B model was
  also exercised and failed to produce a complete valid draft; that negative
  result remains part of the regression evidence.
- The intake-to-draft live scorer evaluates template fidelity, completeness,
  controlled-fact citation precision, scenario quality, monitoring, language
  and protocol separation. Draft-local hard failures are checked there;
  lifecycle hard failures remain owned by the deterministic state-machine gate.

## Golden coverage matrix

- P0: 19/19 cases and 66/66 hard assertions; release gate.
- P1: 5/5 cases and 14/14 hard assertions; release gate.
- All cases: 24/24 and 80/80 hard assertions. No known deterministic golden
  gaps remain.
- These counts describe scripted deterministic cases only. They do not satisfy
  the rubric's live-model gate or its 95% grounded-reference threshold.
