#!/bin/sh
set -eu

check_interval_seconds="${RUNSC_CHECK_INTERVAL_SECONDS:-30}"

runtime_is_registered() {
    docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'
}

register_runtime() {
    dockerd_pid="$(pidof dockerd | awk '{print $1}')"
    if [ -z "${dockerd_pid}" ]; then
        echo "runsc bootstrap: dockerd PID is unavailable" >&2
        return 1
    fi

    /var/lib/docker/gvisor/runsc install \
        -config_file /run/config/docker/daemon.json \
        -download-sidecars=NEVER \
        -require-sidecars=ALWAYS
    kill -HUP "${dockerd_pid}"

    attempt=0
    while [ "${attempt}" -lt 30 ]; do
        if runtime_is_registered; then
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done

    echo "runsc bootstrap: Docker did not load the runtime after SIGHUP" >&2
    return 1
}

restart_running_manager() {
    manager_id="$(docker ps -q --filter label=com.docker.compose.service=sandbox-executor-manager | head -n 1)"
    if [ -n "${manager_id}" ]; then
        docker restart "${manager_id}" >/dev/null
    fi
}

while true; do
    if runtime_is_registered; then
        sleep "${check_interval_seconds}"
        continue
    fi

    echo "runsc bootstrap: registering gVisor runtime"
    if register_runtime; then
        echo "runsc bootstrap: runtime registered"
        restart_running_manager
    fi
    sleep "${check_interval_seconds}"
done
