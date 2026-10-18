# PostgreSQL backup and recovery

Use a verified logical PostgreSQL dump before schema or application upgrades.
The [local deployment script](../../../deployment/local/README.md) records the
dump, checksum and runtime identity before recreating selected services.
For Linux installations, follow the backup and restore commands in
[deployment/linux-pg](../../../deployment/linux-pg/README.md).

Restoring a database is an explicit recovery action. Stop application writers,
validate the selected dump and its checksum, restore into PostgreSQL, then
verify schema initialization and application health. An image rollback alone
does not roll back database changes. Preserve later records separately when
their loss would exceed the accepted recovery point.

MinIO objects and search indexes have their own storage lifecycles. Use the
coordinated backup/restore procedure from the Linux guide when a consistent
database and object-storage recovery point is required. Do not copy the files
of a running PostgreSQL data volume as a database backup.
