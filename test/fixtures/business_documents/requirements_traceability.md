# Business requirements agent: requirements traceability

Status vocabulary: `covered`, `covered_by_lower_level`, `gap`,
`not_applicable`.  This matrix is intentionally executable-test oriented: a
documented behavior is not considered covered by a UI rendering alone.

| Requirement | Primary surface | Automated evidence | State |
| --- | --- | --- | --- |
| One chat maps to one idea/document | Domain persistence | `test_business_document_service.py::test_owner_access_list_and_server_assigned_chat` | covered |
| No draft before all intake questions are closed | Command gate | `test_business_document_service.py::test_draft_requires_complete_assessment_after_last_intake_answer` | covered |
| Questions contain two to four answer options | JSON Schema and completion transaction | `test_business_requirements_assets.py::test_contract_schemas_compile_and_question_bounds_are_enforced`; `test_business_document_service.py::test_question_schema_boundary_rejects_five_options_and_rolls_back` | covered |
| Author can choose an option or provide a custom answer | Domain command | `test_business_document_service.py::test_question_answers_and_proposal_decisions_are_immutable` plus negative answer-boundary tests | covered |
| Author cannot edit the body directly | REST command allowlist and read-only UI document pane | `test_business_document_api_contract.py`; `web/src/pages/business-documents/index.test.tsx` | covered_by_lower_level |
| Body remains unchanged while review questions are open | Command gate and immutable revisions | `test_business_document_service.py::test_apply_is_rejected_while_review_question_is_open_without_mutation_or_job` | covered |
| Only accepted proposals may authorize a change | Source-event validator | `test_business_document_service.py::test_rejected_proposal_cannot_authorize_change` | covered |
| Questions, answers, proposals, decisions, and comments are append-only | Separate persistence models | `test_business_document_service.py::test_full_workflow_is_versioned_idempotent_and_append_only`; immutable-answer test | covered |
| Comment may target a selected fragment in the current revision | Comment command and UI selection | service anchor negative/boundary tests; `web/src/pages/business-documents/index.test.tsx` | covered |
| Anchored comments survive revision changes without silent re-anchoring | Projection anchor lifecycle | service anchor lifecycle tests; golden case `G11`; orphan marker in `ProtocolPane` | covered |
| Draft follows only the published business-requirements template | AST validator | `test_business_requirements_assets.py::test_template_preserves_published_semantic_outline`; required-section/template mismatch tests | covered |
| Section 4.1 contains a bounded PlantUML conceptual diagram | AST semantic validator | `test_business_document_service.py::test_required_sections_cannot_be_empty_and_child_headings_are_nested`; canonical live/scorer fixture assertions | covered |
| Section 4.3 contains BPMN 2.0, accompanying text and an explicit negative alternative | AST semantic validator | `test_business_document_service.py::test_required_sections_cannot_be_empty_and_child_headings_are_nested`; canonical live/scorer fixture assertions | covered |
| Section 5.5 Monitoring is mandatory | Template and AST validator | `test_business_requirements_assets.py::test_template_preserves_published_semantic_outline`; required-section test | covered |
| Revisions are immutable and stale AI results cannot commit | Optimistic state version and hashes | `test_business_document_service.py::test_stale_worker_result_cannot_create_revision`; section-hash test | covered |
| Same idempotency key cannot create duplicate jobs/events | Command ledger | idempotency tests in `test_business_document_service.py` | covered |
| Tenant boundary is non-enumerable | Query scope | `test_business_document_service.py::test_tenant_access_is_non_enumerable` | covered |
| Every REST route is authenticated | REST decorators | `test_business_document_api_contract.py::test_http_surface_is_exact_and_every_route_requires_login` | covered |
| Export is possible only from the current agreed revision | Command gate | export lifecycle/revision tests in `test_business_document_service.py` | covered |
| EvaWiki excludes the agreement protocol | Export worker contract | `test_business_document_worker_exports.py::test_eva_wiki_exact_shape_escapes_content_and_excludes_protocol`; executable golden G22/G23 | covered |
| Word, Markdown and EvaWiki artifacts can be downloaded | Export worker integration | worker/export artifact tests cover durable storage, hash, ACL, DOCX ZIP, list/download; API contract covers list/download routes | covered |
| Uploaded files are evidence, never executable instructions | Pinned RAGFlow dataset evidence | executable golden G20 runs `BusinessDocumentEvidence` and injected AI, then verifies pinned chunk/source-ref/hash/audit and unchanged lifecycle | covered |
| Continue the same document in the same chat | Review-cycle transition | full workflow/start-review tests in `test_business_document_service.py`; golden case `G24` | covered |
| Deterministic golden dialogues protect orchestration and state-machine invariants | Eval runner | `test_golden_dialogue_harness.py::test_release_gate_executes_every_p0_assertion_and_reports_all_case_rate` executes all 24 cases through the real service/worker with scripted AI outputs | covered |
| Live model completes representative intake and draft with rubric score and grounded references | Opt-in live eval | `test_live_model_quality.py::test_live_model_intake_draft_rubric_and_grounding` uses the tenant's real chat model plus a controlled pinned Evidence snapshot; implementation is present but the live run was not executed in this evidence snapshot | gap |
| Weighted rubric and controlled-reference precision are deterministic and regression-tested | Eval scorer | `test_live_quality_scorer.py` covers config gating, weighted criteria, protocol separation, monitoring, canonical PlantUML/BPMN fixture shape, draft-local hard failures and unsupported measurable claims | covered |

## Coverage boundaries

- API inventory: nine protected routes under `/api/v1/business-documents`,
  including paginated owned-document list, jobs, export list and owner-checked download.
- UI inventory: create screen plus protected workbench, document pane,
  protocol pane, loading/error/conflict/busy states.
- Persona inventory: author and cross-tenant caller are automated at the
  domain boundary; unauthenticated access is enforced by the shared
  `login_required` decorator and statically guarded for every new route.
- Deterministic worker, pinned dataset retrieval, Markdown/DOCX/EvaWiki and
  object-storage contracts are covered with injected adapters. The scripted
  golden gate is a state-machine gate, not a model-quality claim.
- The separate live lane is enabled only by
  `BUSINESS_DOCUMENT_LIVE_LLM=1` together with an explicit
  `BUSINESS_DOCUMENT_LIVE_TENANT_ID`. Flag `1` without a tenant fails instead
  of silently skipping. With the flag disabled, the test honestly skips.
- The intake-to-draft live scorer evaluates template fidelity, completeness,
  controlled-fact citation precision, scenario quality, monitoring, language
  and protocol separation. Draft-local hard failures are checked there;
  lifecycle hard failures remain owned by the deterministic state-machine gate.

## Golden coverage matrix

- P0: 19/19 cases, 100% hard assertions; release gate.
- P1: 5/5 cases and 14/14 hard assertions; release gate.
- All cases: 24/24 and 73/73 hard assertions. No known deterministic golden
  gaps remain.
- These counts describe scripted deterministic cases only. They do not satisfy
  the rubric's live-model gate or its 95% grounded-reference threshold.
