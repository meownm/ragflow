#!/usr/bin/env bash
set -Eeuo pipefail

# Run after installing this increment's Python logger and Loki configuration.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR=${INSTALL_DIR:-/opt/ragflow-pg}
PROJECT_NAME=${PROJECT_NAME:-ragflow-pg}
BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/ragflow-pg}
SECRETS_DIR=${SECRETS_DIR:-/etc/ragflow-pg}
[[ ${INSTALL_DIR} == /* && ${INSTALL_DIR} != / && -d ${INSTALL_DIR}/docker/ragflow-logs ]]
sudo -n install -d -m 0755 /usr/local/lib/ragflow-maintenance
sudo -n install -m 0755 "${SCRIPT_DIR}/maintenance.py" /usr/local/lib/ragflow-maintenance/maintenance.py
sudo -n install -m 0755 "${SCRIPT_DIR}/retention.py" /usr/local/lib/ragflow-maintenance/retention.py
for action in report elastic; do
  sudo -n tee "/etc/systemd/system/ragflow-maintenance-${action}.service" >/dev/null <<EOF
[Unit]
Description=RAGFlow maintenance (${action})
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/ragflow-maintenance/maintenance.py ${action} --logs ${INSTALL_DIR}/docker/ragflow-logs
Nice=15
IOSchedulingClass=idle
TimeoutStartSec=infinity
UMask=0027
EOF
done
sudo -n tee /etc/systemd/system/ragflow-maintenance-report.timer >/dev/null <<'EOF'
[Unit]
Description=Refresh RAGFlow maintenance report and expire old log archives
[Timer]
OnCalendar=hourly
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo -n tee /etc/systemd/system/ragflow-maintenance-elastic.timer >/dev/null <<'EOF'
[Unit]
Description=Weekly safe RAGFlow Elasticsearch maintenance
[Timer]
OnCalendar=Sun *-*-* 03:30:00 Europe/Moscow
Persistent=true
[Install]
WantedBy=timers.target
EOF
sudo -n tee /etc/systemd/system/ragflow-retention.service >/dev/null <<EOF
[Unit]
Description=Retain two verified RAGFlow rollback versions
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /usr/local/lib/ragflow-maintenance/retention.py --apply --install-dir "${INSTALL_DIR}" --backup-root "${BACKUP_ROOT}" --secrets-dir "${SECRETS_DIR}" --project "${PROJECT_NAME}"
Nice=15
IOSchedulingClass=idle
TimeoutStartSec=15min
UMask=0027
EOF
if sudo -n systemctl cat ragflow-retention.timer >/dev/null 2>&1; then
  sudo -n systemctl disable --now ragflow-retention.timer
  sudo -n rm -f -- /etc/systemd/system/ragflow-retention.timer
fi
sudo -n systemd-analyze verify /etc/systemd/system/ragflow-maintenance-{report,elastic}.{service,timer}
sudo -n systemd-analyze verify /etc/systemd/system/ragflow-retention.service
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now ragflow-maintenance-report.timer ragflow-maintenance-elastic.timer
sudo -n systemctl start ragflow-maintenance-report.service
