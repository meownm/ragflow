import { Authorization } from '@/constants/authorization';
import { ReadableStream, TextDecoderStream, TransformStream } from 'stream/web';
import type { SourceWorkspace } from './source-workbench-service';

let processSourceWorkspaceStream: typeof import('./source-workbench-service').processSourceWorkspaceStream;

const workspace: SourceWorkspace = {
  id: 'workspace-1',
  title: 'Sources',
  dataset_ids: ['kb-1'],
  selected_documents: [{ dataset_id: 'kb-1', document_id: 'doc-1' }],
  search_queries: [],
  version: 4,
  created_at: '',
  updated_at: '',
};

const originalFetch = globalThis.fetch;
const originalTextDecoderStream = globalThis.TextDecoderStream;
const originalTransformStream = globalThis.TransformStream;

beforeAll(() => {
  Object.assign(globalThis, { TextDecoderStream, TransformStream });
  ({ processSourceWorkspaceStream } = jest.requireActual<
    typeof import('./source-workbench-service')
  >('./source-workbench-service'));
});

beforeEach(() => {
  Object.assign(globalThis, { TextDecoderStream, TransformStream });
  localStorage.setItem(Authorization, 'Bearer test');
});

afterEach(() => {
  globalThis.fetch = originalFetch;
  Object.assign(globalThis, {
    TextDecoderStream: originalTextDecoderStream,
    TransformStream: originalTransformStream,
  });
  localStorage.removeItem(Authorization);
});

function streamedResponse(frames: string) {
  const bytes = new TextEncoder().encode(frames);
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(bytes.slice(0, 19));
      controller.enqueue(bytes.slice(19, 47));
      controller.enqueue(bytes.slice(47));
      controller.close();
    },
  });
  return { ok: true, body } as unknown as Response;
}

test('parses split SSE frames, ignores heartbeats, and requires done', async () => {
  const frames =
    ': heartbeat\n\nevent: status\ndata: {"event":"status","stage":"loading","message":"Loading","current":0,"total":1}\n\nevent: delta\ndata: {"event":"delta","text":"Привет"}\n\nevent: done\ndata: {"event":"done","text":"Привет","processed":1,"total":1,"version":4}\n\n';
  const fetchMock = jest.fn().mockResolvedValue(streamedResponse(frames));
  globalThis.fetch = fetchMock;
  const received: string[] = [];
  await processSourceWorkspaceStream(
    workspace,
    { prompt: 'Create', draft: '', mode: 'all' },
    (event) => received.push(event.event),
    new AbortController().signal,
  );
  expect(received).toEqual(['status', 'delta', 'done']);
  expect(fetchMock.mock.calls[0][1].headers[Authorization]).toBe('Bearer test');
  expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
    prompt: 'Create',
    draft: '',
    mode: 'all',
    expected_version: 4,
  });
});

test('reports a stream that closes without completion', async () => {
  globalThis.fetch = jest
    .fn()
    .mockResolvedValue(
      streamedResponse(
        'event: delta\ndata: {"event":"delta","text":"Partial"}\n\n',
      ),
    );
  await expect(
    processSourceWorkspaceStream(
      workspace,
      { prompt: 'Create', draft: '', mode: 'all' },
      () => {},
      new AbortController().signal,
    ),
  ).rejects.toThrow('Поток прервался');
});
