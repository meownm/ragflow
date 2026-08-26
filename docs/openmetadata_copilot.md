# OpenMetadata Copilot

OpenMetadata Copilot exposes the current OpenMetadata catalog inside RAGFlow at
`/openmetadata`. OpenMetadata stays the source of truth, while a private RAGFlow
Dataset is its searchable semantic projection. RAGFlow provides authenticated
search, intent routing, typed relationship and quality answers, starter questions, and
governed changes.

## Runtime design

- `catalog_copilot` routes a question to discovery, impact, quality, or recent
  catalog activity, carries entity IDs only from the most recent non-empty
  answer, and returns the evidence sources used for the answer. Referential
  follow-ups such as "which of those" are evaluated only against the current
  ACL-filtered projection of those entities.
- `dataset_retrieval` runs semantic retrieval over the linked private RAGFlow
  Dataset and resolves every hit back to the current allowed OpenMetadata
  projection. The indexed and current OpenMetadata `updatedAt` values must match;
  stale, deleted, or unauthorized Dataset documents are discarded. If the
  Dataset is not configured or is unavailable, the agent reports the fallback
  and uses live OpenMetadata search.
- `discovery` combines Dataset retrieval, OpenMetadata search results, and the
  current catalog projection using reciprocal-rank fusion.
- `impact_quality` combines only registered OpenMetadata evidence: table and
  column lineage, PK/FK table constraints, and RDF glossary mappings. These
  remain distinct relationship types; missing edges are never inferred from
  names. When domain scoping is configured, table nodes outside the user's
  current catalog projection and their edges are removed. Table and column test
  cases are loaded by the selected table entity link; zero matching test cases
  are reported as `not_configured`.
- `starter_questions` emits only questions supported by the current catalog
  capabilities in the user's current domain scope. Counts that cannot be safely
  scoped are reported as unknown. Each question includes a structured action, so missing
  descriptions, recent updates, domain discovery, lineage, and quality do not
  depend on free-text classification.
- `governance` supports a two-step preview/confirm flow for `description` and
  `displayName` only. It emits JSON Patch `add`, `replace`, or `remove` according
  to the exact field state read immediately before preview.

The catalog projection uses OpenMetadata's `table_search_index` in bounded pages.
Exact entity state is read again from the entity API before every governed write.
The Catalog UI exposes the projection as a paged, filterable entity browser and
keeps all Copilot turns visible. Ambiguous lineage or quality questions return
selectable candidates; selecting one repeats the question with its entity UUID.

## OpenMetadata Dataset

Create an `OpenMetadata` data-source connection under **Data source**, test the
connection, then link it to a private Dataset. One Markdown document is generated
per OpenMetadata table. Its stable document identity is the OpenMetadata entity
UUID; its metadata contains the fully-qualified name, service, database, schema,
owners, domains, tags, glossary concepts, entity type, and source URL. PK/FK
constraints and registered lineage are included in a Relationships section.
Column names, types, descriptions, and column-level lineage mappings are included
when available and `include_columns` is enabled.

Incremental runs compare a stable content fingerprint and upload only new or
changed tables. A separate prune pass deletes Dataset documents whose entity UUID
no longer exists in OpenMetadata. The connector supports service, domain, and tag
filters, bounded batch and entity counts, request timeouts, and retries. It can use
either OpenMetadata username/password authentication or a JWT; stored secrets are
never returned by the connector API.

The Dataset must remain private. RAGFlow rejects both linking this source to a
team Dataset and changing a linked Dataset from private to team visibility,
because OpenMetadata domain ACLs cannot be represented at chunk level. At query
time the service applies the user's current domain scope again and fails open to
live OpenMetadata search if Dataset retrieval is unavailable.

## Configuration

The local compose overlay `docker/docker-compose.local.yml` mounts the backend
module, REST routes, and the built `web/dist` into `ragflow-cpu`. Keep public
defaults in `docker/.env` and put credentials plus the local Dataset ID in the
ignored `docker/.env.local` file:

| Variable | Purpose |
| --- | --- |
| `OPENMETADATA_URL` | Fixed internal OpenMetadata origin reachable from RAGFlow |
| `OPENMETADATA_PUBLIC_URL` | Browser-facing OpenMetadata origin |
| `OPENMETADATA_USERNAME`, `OPENMETADATA_PASSWORD` | Basic-login credentials |
| `OPENMETADATA_JWT_TOKEN` | Optional JWT alternative to basic login |
| `OPENMETADATA_WRITE_ENABLED` | Enables preview/confirm for RAGFlow superusers |
| `OPENMETADATA_CACHE_TTL_SECONDS` | Catalog projection cache lifetime |
| `OPENMETADATA_STALE_AFTER_HOURS` | Freshness threshold shown in UX and answers |
| `OPENMETADATA_USER_DOMAIN_MAP` | JSON map of RAGFlow user IDs to allowed domains |
| `OPENMETADATA_ALLOWED_DOMAINS` | Global comma-separated domain allowlist |
| `OPENMETADATA_DATASET_ID` | Private Dataset used by `dataset_retrieval` |
| `OPENMETADATA_DATASET_TOP_N` | Maximum Dataset chunks considered per question |
| `OPENMETADATA_DATASET_SIMILARITY_THRESHOLD` | Minimum Dataset similarity |
| `OPENMETADATA_DATASET_VECTOR_WEIGHT` | Dense-vector weight in Dataset retrieval |

If a user-domain map is configured and a user has no direct or wildcard entry,
the catalog scope is empty (fail closed).

The local verified configuration uses the existing Ollama embedding model
`qwen3-embedding:0.6b`; the setup must not pull a new model implicitly.

## API

All routes require a RAGFlow login:

- `GET /api/v1/openmetadata/status`
- `GET /api/v1/openmetadata/starter-questions`
- `POST /api/v1/openmetadata/query`
- `GET /api/v1/openmetadata/entities`
- `GET /api/v1/openmetadata/entities/<id>/relationships`
- `POST /api/v1/openmetadata/governance/preview`
- `POST /api/v1/openmetadata/governance/confirm`

Status, capability counts, starter questions, catalog results, and Copilot
answers are scoped to the authenticated user's current OpenMetadata domains.

`GET /entities` accepts `q`, `owner`, `domain`, `service`, `tag`,
`has_description`, `limit`, `offset`, and `sort=relevance|updated_at|fqn`.
It uses OpenMetadata search plus the in-memory catalog projection directly;
Dataset retrieval is reserved for Copilot questions so list filtering does not
wait for semantic chunk search.
`POST /query` accepts the displayed `question`, optional filters, locale, a
structured starter `action`, a bounded conversation `context` containing entity
IDs, and `selected_entity_id` when resolving a clarification.
Structured actions and relationship, quality, or capability questions bypass
Dataset retrieval because their answers come entirely from current OMD evidence.

## OpenMetadata RDF knowledge graph

OpenMetadata's RDF module and its Apache Jena Fuseki store must be enabled for
semantic table-to-concept-to-table traversal. Existing catalog entities are
backfilled with the built-in `RdfIndexApp`; later entity and glossary changes are
written to RDF by OpenMetadata. The Copilot reads `/api/v1/rdf/graph/explore`
through the fixed configured OMD origin, applies the current table-domain scope,
and falls back to PK/FK constraints and lineage if RDF is temporarily unavailable.

Governance routes additionally require a RAGFlow superuser. Preview returns a
short-lived signed confirmation token and diff. Confirm consumes a one-time Redis
nonce, checks the expected OpenMetadata entity version, applies JSON Patch, reads
the entity back for verification, writes an audit log, and invalidates the cache.
The UI preserves nullable `displayName` and `description` values and sends only
dirty fields, so an untouched form cannot create a confirmation token. Once a
signed preview exists, both fields remain locked until the user returns to
editing; the confirmed values therefore cannot diverge from the preview token.

## Local deploy and verification

```powershell
Set-Location S:\ragflow\web
$env:NODE_OPTIONS = '--max-old-space-size=8192'
$env:VITE_BUILD_SOURCEMAP = 'false'
npm run build

Set-Location S:\ragflow\docker
docker compose -p ragflow-local --env-file .env --env-file .env.local `
  -f docker-compose.yml -f docker-compose.local.yml `
  up -d --force-recreate --no-deps ragflow-cpu
```

Verify `http://127.0.0.1:9380/api/v1/system/healthz`, then sign in and open
`http://127.0.0.1:9380/openmetadata`. If OpenMetadata system status is healthy but
catalog queries return an Elasticsearch error, verify the OpenMetadata search
container separately; the server health endpoint alone does not prove that search
is ready.

## Automated regression

```powershell
uv run pytest -q `
  test/unit_test/data_source/test_openmetadata_connector.py `
  test/unit_test/api/apps/services/test_openmetadata_copilot_service.py `
  test/unit_test/api/apps/restful_apis/test_openmetadata_routes_unit.py
uv run pytest -q test/unit_test/rag/test_sync_data_source.py -k openmetadata
uv run ruff check `
  api/apps/services/openmetadata_copilot_service.py `
  api/apps/restful_apis/openmetadata_api.py

Set-Location web
npm test -- --runInBand --coverage=false src/pages/openmetadata/index.test.tsx
npx eslint vite.config.ts src/pages/openmetadata `
  src/services/openmetadata-service.ts src/routes.tsx `
  src/layouts/components/global-navbar.tsx src/utils/api.ts
```

Live governance validation must stop after preview unless the target entity is a
disposable QA entity. Never confirm a mutation against shared catalog data only to
prove that the button works.
