# Database utilities

RAGFlow metadata is stored in PostgreSQL. Application startup calls
`api.db.db_models.init_database_tables()` and applies the idempotent migrations
from that module, including the linked-document upgrade.

Local deployment and its verified database backup are described in
[the deployment guide](../../deployment/local/README.md). Linux backup and
restore procedures are in [deployment/linux-pg](../../deployment/linux-pg/README.md).
Use disposable PostgreSQL databases for migration tests; never run test
initializers against a shared application database.
