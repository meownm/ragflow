# RAGFlow Helm Chart

A Helm chart to deploy RAGFlow and its dependencies on Kubernetes.

- Components: RAGFlow (web/api) and optional dependencies (Infinity/Elasticsearch/OpenSearch, PostgreSQL, MinIO, Redis)
- Requirements: Kubernetes >= 1.24, Helm >= 3.10

## Install

```bash
helm upgrade --install ragflow ./ \
  --namespace ragflow --create-namespace
```

Uninstall:
```bash
helm uninstall ragflow -n ragflow
```

## Global Settings

- `global.repo`: Prepend a global image registry prefix for all images.
  - Behavior: Replaces the registry part and keeps the image path (e.g., `quay.io/minio/minio` -> `registry.example.com/myproj/minio/minio`).
  - Example: `global.repo: "registry.example.com/myproj"`
- `global.imagePullSecrets`: List of image pull secrets applied to all Pods.
  - Example:
    ```yaml
    global:
      imagePullSecrets:
        - name: regcred
    ```

## External Services (PostgreSQL / MinIO / Redis)

The chart can deploy in-cluster services or connect to external ones. Toggle with `*.enabled`. When disabled, provide host/port via `env.*`.

- PostgreSQL
  - `postgres.enabled`: default `true`
  - If `false`, set:
    - `env.POSTGRES_HOST` (required), `env.POSTGRES_PORT` (default `5432`)
    - `env.POSTGRES_DBNAME` (default `rag_flow`), `env.POSTGRES_PASSWORD` (required)
    - `env.POSTGRES_USER` (default `rag_flow` if omitted)
- MinIO
  - `minio.enabled`: default `true`
  - Configure:
    - `env.MINIO_HOST` (optional external host), `env.MINIO_PORT` (default `9000`)
    - `env.MINIO_ROOT_USER` (default `rag_flow`), `env.MINIO_PASSWORD` (optional)
- Redis (Valkey)
  - `redis.enabled`: default `true`
  - If `false`, set:
    - `env.REDIS_HOST` (required), `env.REDIS_PORT` (default `6379`)
    - `env.REDIS_PASSWORD` (optional; empty disables auth if server allows)

Notes:
- When `*.enabled=true`, the chart renders in-cluster resources and injects corresponding `*_HOST`/`*_PORT` automatically.
- Sensitive variables like `POSTGRES_PASSWORD` are required; `MINIO_PASSWORD` and `REDIS_PASSWORD` are optional. All secrets are stored in a Secret.

### Example: use external PostgreSQL, MinIO, and Redis

```yaml
# values.override.yaml
postgres:
  enabled: false  # use external PostgreSQL
minio:
  enabled: false  # use external MinIO (S3 compatible)
redis:
  enabled: false  # use external Redis/Valkey

env:
  # PostgreSQL
  POSTGRES_HOST: mydb.example.com
  POSTGRES_PORT: "5432"
  POSTGRES_USER: rag_flow
  POSTGRES_DBNAME: rag_flow
  POSTGRES_PASSWORD: "<your-postgres-password>"

  # MinIO
  MINIO_HOST: s3.example.com
  MINIO_PORT: "9000"
  MINIO_ROOT_USER: rag_flow
  MINIO_PASSWORD: "<your-minio-secret>"

  # Redis
  REDIS_HOST: redis.example.com
  REDIS_PORT: "6379"
  REDIS_PASSWORD: "<your-redis-pass>"
```

Apply:
```bash
helm upgrade --install ragflow ./helm -n ragflow -f values.override.yaml
```

## Document Engine Selection

Choose one of `infinity` (default), `elasticsearch`, or `opensearch` via `env.DOC_ENGINE`. The chart renders only the selected engine and sets the appropriate host variables.

```yaml
env:
  DOC_ENGINE: infinity   # or: elasticsearch | opensearch
  # For elasticsearch
  ELASTIC_PASSWORD: "<es-pass>"
  # For opensearch
  OPENSEARCH_PASSWORD: "<os-pass>"
```

## Ingress

Expose the web UI via Ingress:

```yaml
ingress:
  enabled: true
  className: nginx
  hosts:
    - host: ragflow.example.com
      paths:
        - path: /
          pathType: Prefix
```

## Validate the Chart

```bash
helm lint ./helm
helm template ragflow ./helm > rendered.yaml
```

## Notes

- By default, the chart uses `DOC_ENGINE: infinity` and deploys in-cluster PostgreSQL, MinIO, and Redis.
- The chart injects derived `*_HOST`/`*_PORT` and required secrets into a single Secret (`<release>-ragflow-env-config`).
- `global.repo` and `global.imagePullSecrets` apply to all Pods; per-component `*.image.pullSecrets` still work and are merged with global settings.
