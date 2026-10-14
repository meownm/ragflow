import assert from 'node:assert/strict';
import test from 'node:test';
import { readSse } from '../webapp/app.js';

test('reads split UTF-8 SSE progress, cumulative text and final events', async () => {
  const bytes = new TextEncoder().encode(
    'event: progress\ndata: {"event":"progress","percent":25}\n\n' +
    'event: delta\ndata: {"event":"delta","transcript":"Привет"}\n\n' +
    'event: final\ndata: {"event":"final","transcript":"Привет мир"}\n\n',
  );
  const chunks = [bytes.slice(0, 18), bytes.slice(18, 73), bytes.slice(73, 81), bytes.slice(81)];
  const stream = new ReadableStream({
    pull(controller) {
      if (chunks.length) controller.enqueue(chunks.shift());
      else controller.close();
    },
  });
  const events = [];
  for await (const event of readSse(stream)) events.push(event);
  assert.deepEqual(events.map((event) => event.event), ['progress', 'delta', 'final']);
  assert.equal(events[0].percent, 25);
  assert.equal(events[1].transcript, 'Привет');
  assert.equal(events[2].transcript, 'Привет мир');
});
