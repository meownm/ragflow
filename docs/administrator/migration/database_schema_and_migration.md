# PostgreSQL schema and migrations

PostgreSQL is the metadata database. `DB_TYPE` defaults to `postgres`; any other
value is rejected before database initialization. Configure the `postgres`
section in `conf/service_conf.yaml` or the runtime template and environment.

Application startup calls `init_database_tables()` in `api/db/db_models.py`.
The initializer creates missing tables, runs the idempotent schema migrations,
and upgrades saved Source Workbench drafts into versioned linked documents.
Historical source text that is unavailable remains explicitly unresolved.

Before updating, create and verify a PostgreSQL dump. Local deployment performs
this through [deploy.ps1](../../../deployment/local/README.md); Linux procedures
are in [deployment/linux-pg](../../../deployment/linux-pg/README.md).
Changing the database engine does not migrate existing database contents.

The disposable PostgreSQL tests in `test/integration/test_source_document_migrations.py`
cover old drafts, repeated migration, interrupted backfill recovery and source
updates. They require an explicitly disposable database and must not target the
application database.
