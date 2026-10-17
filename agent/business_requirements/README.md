# Business Requirements Agent

This package contains the versioned, environment-neutral assets used by the
Business Requirements workflow. Runtime document state belongs to the backend
domain module; RAGFlow sessions and canvases are interaction channels, not the
source of truth.

See `ARCHITECTURE.md` for process boundaries, component ownership, state axes,
and interaction contracts. `TEST_STRATEGY.md` defines the positive, negative,
boundary, information-quality, and golden-dialogue release gates.

## Layout

- `policies/` — workflow and approval invariants.
- `templates/` — the semantic document outline.
- `contracts/` — structured-output JSON Schemas for model calls.
- `prompts/` — contract-first prompts with explicit evidence boundaries.
- `golden_dialogs/` — replayable quality and safety conversations.
- `evals/` — weighted quality rubric and release gate.

Published asset versions are immutable. A document pins the exact template and
policy versions used when it is created.

Create-document v4 accepts a catalog title without an idea. Without imported
EVA content it creates revision 1 with empty top-level sections (including
section 2, normative legal acts) and mandatory subsections, using document AST
v2 (`section_scope: required`), and opens review without an AI job. Optional
subsections are added when needed. Review
changes may fill this structure incrementally. Missing content, diagrams,
format repairs and semantic review issues are saved as advisory `warnings`
with the revision or prepared changes and shown in the Workbench. A usable
document is retained when its semantic reviewer is unavailable. Documents with
warnings remain in review until the author explicitly confirms the prepared
changes. Access, source provenance, revision freshness,
transaction and worker-lease checks still reject unsafe writes. Optional
`warnings` metadata is supported by document AST v1 and v2.

The Workbench calls the document API directly. Do not route calls back into the
same deployment through the Canvas `Invoke` component: its SSRF controls must
continue to reject private and loopback targets. If a Canvas adapter is added,
it must call the domain service in-process and keep JSON Schema validation at
the domain boundary.
