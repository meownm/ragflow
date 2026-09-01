# RAGFlow Linux PostgreSQL bundle

Автономная поставка текущего рабочего дерева RAGFlow для чистой Ubuntu 24.04
или Debian 12 x86_64.

Состав и свойства:

- PostgreSQL 16 вместо MySQL;
- Elasticsearch как документный и векторный движок, поэтому pgvector не нужен;
- внутренние Valkey, MinIO и PlantUML;
- T-One ASR собирается из `services/asr-online-service`, проверяется через
  `/health/ready` и регистрируется у первого tenant как модель по умолчанию;
- первый пользователь создаётся штатными сервисами RAGFlow с
  `is_superuser=true`;
- RAGFlow публикуется только на loopback;
- локальные `.env.local`, контейнерные данные, логи, кэши и Git-метаданные в
  архив не включаются.

## Сборка на Windows

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./deployment/linux-pg/build_bundle.ps1
```

Результат создаётся рядом с установочными скриптами в
`deployment/linux-pg/`:

- `ragflow-pg-linux-<timestamp>.tar.gz`;
- файл контрольной суммы `.sha256`.

## Установка на чистом Linux

```bash
sha256sum -c ragflow-pg-linux-<timestamp>.tar.gz.sha256
tar -xzf ragflow-pg-linux-<timestamp>.tar.gz
sudo ADMIN_EMAIL=admin@example.org \
  ADMIN_PASSWORD='replace-with-a-strong-password' \
  bash ragflow-pg/deployment/linux-pg/install.sh
```

Если `ADMIN_PASSWORD` не задан, скрипт создаёт случайный пароль. Итоговые
учётные данные сохраняются в `/etc/ragflow-pg/admin.env` с правами `0600`.

По умолчанию интерфейс доступен только на `http://127.0.0.1:9380`. Для
публикации используйте отдельный reverse proxy с TLS или SSH-туннель.
