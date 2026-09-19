export type AsrStreamEvent = {
  event: 'progress' | 'heartbeat' | 'delta' | 'final' | 'error';
  text?: string;
  transcript?: string;
  code?: string;
  percent?: number;
};

export async function consumeAsrStream(
  response: Response,
  onTranscript: (transcript: string) => void,
): Promise<string> {
  if (!response.body) {
    throw new Error('ASR-сервис не вернул поток данных');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let transcript = '';

  const consumeFrame = (frame: string) => {
    const data = frame
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
      .join('\n');
    if (!data || data === '[DONE]') return;

    const event = JSON.parse(data) as AsrStreamEvent;
    if (event.event === 'error') {
      throw new Error(event.text || event.code || 'Ошибка потокового распознавания');
    }
    if (event.event !== 'delta' && event.event !== 'final') return;

    transcript =
      event.transcript ??
      (event.event === 'delta'
        ? `${transcript}${event.text ?? ''}`
        : event.text ?? transcript);
    onTranscript(transcript);
  };

  let reading = true;
  while (reading) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, '\n');
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    frames.forEach(consumeFrame);
    reading = !done;
  }
  if (buffer.trim()) consumeFrame(buffer);

  return transcript.trim();
}
