const apiUrl = (path) => new URL(path, document.baseURI).toString();

export async function* readSse(stream) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
      let match;
      while ((match = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, match.index);
        buffer = buffer.slice(match.index + match[0].length);
        const data = [];
        let eventName = 'message';
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) eventName = line.slice(6).trim();
          if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
        }
        if (data.length) yield { event: eventName, ...JSON.parse(data.join('\n')) };
      }
      if (done) break;
    }
  } finally {
    reader.releaseLock();
  }
}

function errorMessage(payload, fallback) {
  const detail = payload?.detail ?? payload;
  if ((detail?.error?.code ?? detail?.error_code) === 'Q-ASR-CAPACITY-REACHED') {
    return 'Сервис занят: достигнут лимит одновременных заданий. Повторите попытку позже.';
  }
  return detail?.error?.message ?? detail?.message ?? fallback;
}

async function requestJson(path) {
  const response = await fetch(apiUrl(path));
  const payload = await response.json();
  if (!response.ok) throw new Error(errorMessage(payload, `HTTP ${response.status}`));
  return payload;
}

function setup() {
  const form = document.getElementById('transcriptionForm');
  if (!form) return;
  const fileInput = document.getElementById('audioFile');
  const fileName = document.getElementById('fileName');
  const modelSelect = document.getElementById('model');
  const languageSelect = document.getElementById('language');
  const startButton = document.getElementById('startButton');
  const output = document.getElementById('output');
  const status = document.getElementById('status');
  const error = document.getElementById('error');
  const copyButton = document.getElementById('copyButton');
  const progressBar = document.querySelector('.progress-line');
  const progressFill = document.getElementById('progressFill');
  let models = [];
  let transcript = '';

  function setProgress(percent) {
    const safePercent = Math.max(0, Math.min(100, Number(percent) || 0));
    progressBar.setAttribute('aria-valuenow', String(safePercent));
    progressFill.style.width = `${safePercent}%`;
  }

  function showText(text) {
    transcript = text;
    output.textContent = text || 'Ожидание текста…';
    output.classList.toggle('placeholder', !text);
    copyButton.disabled = !text;
  }

  function updateLanguages() {
    const selected = models.find((model) => model.key === modelSelect.value);
    languageSelect.replaceChildren();
    for (const language of selected?.supported_languages || []) {
      languageSelect.add(new Option(language.toUpperCase(), language));
    }
  }

  fileInput.addEventListener('change', () => {
    fileName.textContent = fileInput.files?.[0]?.name || 'Выберите файл';
  });
  modelSelect.addEventListener('change', updateLanguages);
  copyButton.addEventListener('click', async () => {
    await navigator.clipboard.writeText(transcript);
    copyButton.textContent = 'Скопировано';
    setTimeout(() => { copyButton.textContent = 'Скопировать'; }, 1500);
  });

  requestJson('v1/asr/models').then((payload) => {
    models = payload.models.filter((model) => model.available);
    if (!models.length) throw new Error('Нет доступных моделей распознавания.');
    for (const model of models) modelSelect.add(new Option(model.key, model.key));
    updateLanguages();
    startButton.disabled = false;
  }).catch((cause) => {
    error.hidden = false;
    error.textContent = cause.message;
    status.textContent = 'Сервис недоступен';
  });

  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const file = fileInput.files?.[0];
    if (!file || !modelSelect.value || !languageSelect.value) return;
    startButton.disabled = true;
    error.hidden = true;
    error.textContent = '';
    showText('');
    setProgress(0);
    status.textContent = 'Загрузка и подготовка файла…';
    const body = new FormData();
    body.append('file', file);
    body.append('model', modelSelect.value);
    body.append('language', languageSelect.value);
    body.append('stream', 'true');
    let finished = false;
    try {
      const response = await fetch(apiUrl('v1/audio/transcriptions'), { method: 'POST', body });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(errorMessage(payload, `HTTP ${response.status}`));
      }
      if (!response.body) throw new Error('Браузер не поддерживает потоковый ответ.');
      for await (const item of readSse(response.body)) {
        if (item.event === 'progress') {
          setProgress(item.percent);
          status.textContent = `${item.stage || item.status || 'Обработка'} · ${item.percent ?? 0}%`;
        } else if (item.event === 'delta') {
          showText(item.transcript ?? transcript + (item.text || ''));
          setProgress(item.percent);
        } else if (item.event === 'final') {
          showText(item.transcript ?? item.text ?? transcript);
          setProgress(100);
          status.textContent = 'Готово';
          finished = true;
        } else if (item.event === 'error') {
          throw new Error(item.text || item.code || 'Ошибка распознавания.');
        }
      }
      if (!finished) throw new Error('Поток завершился до получения результата.');
    } catch (cause) {
      status.textContent = 'Ошибка';
      error.hidden = false;
      error.textContent = cause.message || 'Не удалось распознать файл.';
    } finally {
      startButton.disabled = false;
    }
  });
}

if (typeof document !== 'undefined') setup();
