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
search result. It also does not implement document comparison or compilation
pipelines yet.
