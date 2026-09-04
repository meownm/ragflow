# RAGFlow observability

This optional Compose layer starts OpenTelemetry Collector, Tempo, Loki,
Prometheus, and Grafana. Audit rows and logs are retained for 30 days; traces
are retained for 14 days. Services are bound to localhost by default.

Set a non-default Grafana password in `docker/.env.local`:

```env
GRAFANA_ADMIN_PASSWORD=replace-me
```

Start the local CPU stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.observability.yml --profile cpu up -d
```

Open Grafana at <http://localhost:3001> (or the `GRAFANA_PORT` value). The data sources are provisioned
automatically. For any environment reachable by other users, put Grafana
behind the existing SSO/reverse proxy and do not expose ports 3000, 9090,
4317, or 4318 publicly.
