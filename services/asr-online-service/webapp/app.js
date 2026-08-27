async function init() {
  const languageSelect = document.getElementById('language');
  const modelSelect = document.getElementById('model');
  const output = document.getElementById('output');
  const progress = document.getElementById('progress');
  const artifactJson = document.getElementById('artifactJson');
  const artifactTxt = document.getElementById('artifactTxt');

  const languages = await fetch('/v1/asr/languages').then((r) => r.json());
  languages.languages.forEach((lang) => {
    const option = document.createElement('option');
    option.value = lang;
    option.textContent = lang;
    languageSelect.appendChild(option);
  });

  const models = await fetch('/v1/asr/models').then((r) => r.json());
  models.models.forEach((model) => {
    const option = document.createElement('option');
    option.value = model.key;
    option.textContent = `${model.key} (${model.available ? 'available' : 'unavailable'})`;
    option.disabled = !model.available;
    modelSelect.appendChild(option);
  });

  document.getElementById('createJob').onclick = async () => {
    artifactJson.style.display = 'none';
    artifactTxt.style.display = 'none';

    const fileInput = document.getElementById('audioFile');
    let sourceUri = 'memory://sample.wav';
    if (fileInput.files.length > 0) {
      const form = new FormData();
      form.append('file', fileInput.files[0]);
      const uploadResponse = await fetch('/v1/asr/uploads', { method: 'POST', body: form });
      const uploadPayload = await uploadResponse.json();
      sourceUri = uploadPayload.source_uri;
    }

    const artifactFormats = ['result_json'];
    if (document.getElementById('wantTxt').checked) {
      artifactFormats.push('txt');
    }

    const createBody = {
      model_key: modelSelect.value,
      language: languageSelect.value,
      source_uri: sourceUri,
      options: {
        output: { artifact_formats: artifactFormats },
        enrich: { enabled: false },
      },
    };

    const created = await fetch('/v1/asr/jobs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(createBody),
    }).then((r) => r.json());

    if (!created.job_id) {
      output.textContent = JSON.stringify(created, null, 2);
      return;
    }

    const jobId = created.job_id;
    progress.textContent = 'Статус: queued';

    for (let i = 0; i < 60; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, 300));
      const statusPayload = await fetch(`/v1/asr/jobs/${jobId}`).then((r) => r.json());
      progress.textContent = `Статус: ${statusPayload.status}, ${statusPayload.percent}%`;
      if (['done', 'error', 'canceled', 'expired'].includes(statusPayload.status)) {
        break;
      }
    }

    const resultResponse = await fetch(`/v1/asr/jobs/${jobId}/result`);
    const result = await resultResponse.json();
    output.textContent = JSON.stringify(result, null, 2);

    if (!resultResponse.ok || !result.artifacts) {
      return;
    }

    if (result.artifacts.result_json) {
      artifactJson.href = `/v1/asr/jobs/${jobId}/artifacts/result_json`;
      artifactJson.style.display = 'inline';
    }

    if (result.artifacts.txt) {
      artifactTxt.href = `/v1/asr/jobs/${jobId}/artifacts/txt`;
      artifactTxt.style.display = 'inline';
    }
  };
}

init();
