# Source workspaces

A source workspace is a user-owned, reusable set of RAGFlow documents. The
module keeps article discovery, selection, and scoped retrieval together so a
chat or a future document workflow can consume the same source set.

## Contract

- Create a workspace with a title and one or more dataset IDs.
- Search any number of times. Results are grouped by document; EVA Wiki paths
  are read from the indexed document name and shown as a hierarchy.
- Save selected `{dataset_id, document_id}` pairs with `expected_version`. The
  server records each indexed document's revision; a supplied revision is ignored.
- Call `retrieve`, `load_selected_documents`, or `chat` with the workspace ID.
  They check dataset access, document membership, the selection version, and
  document revisions. A changed source returns `SOURCE_CHANGED` (409) and must
  be selected again. Retrieval searches only selected IDs and filters unexpected
  chunks. The full-document loader returns ordered indexed text and source
  metadata for workflows; it rejects unavailable or oversized content instead
  of silently truncating it. The text is the indexed representation, not the
  original file bytes.
- `chat` accepts an optional `previous_question` (up to 500 characters) and
  requires the current selection version. It returns an answer with source and
  citation numbers. Chat turns remain in the client.
- `POST /source-workspaces/{id}/process` emits authenticated SSE with `status`,
  `delta`, `step_done`, `done`, and terminal `error` events plus 15-second
  heartbeats. `all` passes complete indexed texts to one model call only when
  the serialized request fits the configured context. `sequential` processes
  articles in selection order and carries each completed result into the next
  call. An article that does not fit is split at major Markdown/Setext sections
  first, then at paragraphs and whole list/table/code blocks where they fit.
  Only an oversized individual block is divided at item, row, sentence, or
  line boundaries as necessary; no characters are dropped. Task-relevant notes
  are extracted from every token-budgeted part. Continuation parts carry their
  section path and, within a table, column names; this context is included in
  the token budget. Notes are reduced if needed and used for the final article
  step. This condensation can omit details and is
  disclosed in the UI. A draft that cannot fit both input and full-answer
  budgets is rejected before article loading. Prompts and drafts are bounded
  at 20,000 and 100,000 characters. An answer near the model output cap is
  reported as incomplete rather than committed as a finished step.
  Source numbers remain stable across steps. The workflow checks ownership,
  selection version, and source revisions before loading and after each final
  article step. Tokens already streamed before a later source change may remain
  visible in the browser, but that step does not complete.
  The browser may abort the stream; completed steps stay visible there.
  Completed results can be saved as editable workspace drafts after review.
- `GET/POST /source-workspaces/{id}/drafts` lists or saves drafts, and
  `GET/PUT /source-workspaces/{id}/drafts/{draft_id}` opens or edits one.
  The list returns metadata; it loads full text only for the opened draft.
  Saving a new draft pins the current selection, document revisions, prompt,
  mode, and source version on the server. A stale selection is rejected; edits
  use a draft version to prevent overwriting another update. Drafts are owned
  by the workspace user. They are not published into a knowledge base.
- The budget uses the model's configured `max_tokens` context, reserves output
  and safety space, and estimates input with the repository's `cl100k_base`
  tokenizer plus 25% headroom. The upstream stream has no finish-reason signal,
  so the output-cap check is conservative and cannot prove semantic completeness.
  Missing context uses a disclosed 8192-token
  planning fallback. This is an estimate for non-cl100k providers; a provider
  can still reject an overlong request. Model stages have a five-minute limit;
  the interactive stream has a 30-minute deadline and retains completed steps
  in the browser if that deadline is reached.
  The indexed-text loader retains its existing 2-million-character-per-document
  and 10-million-character-per-selection operational bounds. It keeps adjacent
  table rows and list items together across indexed chunk boundaries when
  their Markdown structure is detectable; missing source markup cannot be
  reconstructed from the index.

The pure application service is in `service.py`; `adapters.py` contains Peewee,
RAGFlow search, document-text, and model adapters. HTTP endpoints live in
`api/apps/restful_apis/source_workbench_api.py`. Other backend features can
create the service with `build_source_workspace_service()` and call
`retrieve(owner_id, workspace_id, query, expected_version)` for ranked evidence
or `load_selected_documents(owner_id, workspace_id, expected_version)` for full
indexed text. The frontend picker lives in
`web/src/components/source-workbench/source-picker.tsx`
and accepts a workspace plus an `onChange` callback.

Search pages through ranked chunks in groups of 100, up to ten pages. A workspace
stores the selected documents and search query history; it does not store every
search result. The server-side `SOURCE_WORKBENCH_SIMILARITY_THRESHOLD` setting
controls the minimum search similarity. It defaults to `0.4` and accepts a finite
number from `0` to `1`; invalid values fail the search with a configuration
error. Set `SOURCE_WORKBENCH_SIMILARITY_THRESHOLD=0.4` in the application
container environment when tuning retrieval (`docker/.env`, or
`docker/.env.local` for the local Compose overlay). Recreate the application
container after changing it. The ordinary user interface does not expose it.
It does not implement document comparison or publication yet.
