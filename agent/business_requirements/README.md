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

The Workbench calls the document API directly. Do not route calls back into the
same deployment through the Canvas `Invoke` component: its SSRF controls must
continue to reject private and loopback targets. If a Canvas adapter is added,
it must call the domain service in-process and keep JSON Schema validation at
the domain boundary.
