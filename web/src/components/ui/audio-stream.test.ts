import { consumeAsrStream } from './audio-stream';

function streamResponse(chunks: string[]) {
  const encoder = new TextEncoder();
  const values = chunks.map((chunk) => encoder.encode(chunk));
  let index = 0;
  return {
    body: {
      getReader() {
        return {
          async read() {
            if (index >= values.length) return { value: undefined, done: true };
            return { value: values[index++], done: false };
          },
        };
      },
    },
  } as unknown as Response;
}

describe('consumeAsrStream', () => {
  it('parses SSE frames split across network chunks', async () => {
    const onTranscript = jest.fn();
    const response = streamResponse([
      'event: delta\ndata: {"event":"delta","text":"При","transcript":"При"}\n',
      '\nevent: delta\ndata: {"event":"delta","text":"вет","transcript":"Привет"}\n\n',
      'event: final\ndata: {"event":"final","text":"Привет","transcript":"Привет"}\n\n',
    ]);

    await expect(consumeAsrStream(response, onTranscript)).resolves.toBe(
      'Привет',
    );
    expect(onTranscript).toHaveBeenLastCalledWith('Привет');
  });

  it('surfaces an ASR error event', async () => {
    const response = streamResponse([
      'event: error\ndata: {"event":"error","text":"model failed"}\n\n',
    ]);

    await expect(consumeAsrStream(response, jest.fn())).rejects.toThrow(
      'model failed',
    );
  });
});
