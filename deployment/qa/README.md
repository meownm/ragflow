# Постоянный QA-стенд на Ubuntu

Это отдельный Compose project `ragflow-qa` на `192.168.1.175`. Его PostgreSQL,
Redis, MinIO и Elasticsearch используют только тома с префиксом project; данные
сохраняются при перезапуске. Контейнеры имеют `restart: unless-stopped`.
Рабочий RAGFlow, CI runner и registry не входят в этот project.

Внешние порты привязаны к loopback Ubuntu: API/UI `127.0.0.1:19382`, admin
`127.0.0.1:19383`. Для просмотра с Windows: `ssh -L 19382:127.0.0.1:19382
apt@192.168.1.175`. Не открывайте тестовый контур в LAN без отдельной настройки
доступа.

На хосте Compose и файл `.env` размещаются в `/opt/ragflow-qa`. `.env`
принадлежит пользователю `apt` и имеет режим `0600`; в репозиторий он не
попадает. Переменные: `RAGFLOW_IMAGE` с полным SHA-тегом образа,
`QA_STORAGE_PASSWORD`, `QA_ADMIN_EMAIL`, `QA_ADMIN_PASSWORD`,
`QA_OLLAMA_URL` с адресом проектного Ollama proxy, доступным из контейнера.

Установка и обновление выполняются только с проверкой `SOURCE_REVISION` образа
до `docker compose up -d`. Обычное обновление не применяет `down --volumes` и
не меняет пароли. Проверка:

```bash
cd /opt/ragflow-qa
python3 setup_ubuntu.py \
  --image 192.168.1.175:5443/ragflow:<full-git-sha> \
  --ollama-url http://192.168.1.125:11435
```

`setup_ubuntu.py` проверяет полный SHA в теге и внутри образа, наличие
`business_documents`, модели на proxy, здоровье API и конфигурацию моделей в
выделенном tenant. Файлы `compose.yml`, `setup_ubuntu.py`,
`configure_models.py`, `smoke.py` и `test/integration/live_ragflow/seed_catalog.py`
(под именем `seed_catalog.py`) нужно разместить в `/opt/ragflow-qa` перед
первым запуском. Адрес proxy обновляйте, если меняется IP его хоста.
Эта проверка образа не заменяет CI receipt и release evidence.

Проверка после установки:

```bash
cd /opt/ragflow-qa
docker compose --env-file .env -f compose.yml config --quiet
docker compose --env-file .env -f compose.yml ps
curl --noproxy '*' -fsS http://127.0.0.1:19382/api/v1/system/healthz
docker compose --env-file .env -f compose.yml exec -T app /ragflow/.venv/bin/python - < smoke.py
```

`smoke.py` создаёт ровно один синтетический dataset, дожидается индексации,
проверяет реальный `/retrieval` и удаляет свой dataset. Вывод содержит статус
проверки и очистки без учётных данных.

Тестовые tenant и datasets должны быть явно помечены и очищены по собственным
ID после прогона. Живые оценки запускаются по отдельности, с фиксированными
SHA исходников, модели и корпуса; сбой порога сохраняется как baseline.
Остановить приложение без удаления данных: `docker compose --env-file .env -f
compose.yml stop`. Удаление томов не входит в процедуру обслуживания.
