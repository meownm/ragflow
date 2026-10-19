#!/usr/bin/env python3
"""Host maintenance for RAGFlow. Uses Docker's API; never edits Docker data files."""

import argparse
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


RETENTION_DAYS = 60
INDEX_NAME = re.compile(r"ragflow_(?:doc_meta_)?[a-f0-9]{32}\Z")
DATED_LOG = re.compile(r".+\.log\.(\d{4}-\d{2}-\d{2})(?:\.gz)?\Z")


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def command(*args, timeout=30):
    return subprocess.check_output(args, text=True, timeout=timeout, stderr=subprocess.PIPE).strip()


def elastic(path, method="GET"):
    # The password is expanded only inside the existing Elasticsearch container.
    return json.loads(
        command(
            "docker",
            "exec",
            "ragflow-pg-es01-1",
            "bash",
            "-c",
            'curl -fsS --max-time 3600 -u "elastic:$ELASTIC_PASSWORD" -X "$1" "http://localhost:9200$2"',
            "maintenance",
            method,
            path,
            timeout=3620,
        )
    )


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".maintenance-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def clean_logs(logs, current=None):
    """Compress closed daily logs; expire only recognized RAGFlow log files."""
    current = current or datetime.now(timezone.utc)
    cutoff = current - timedelta(days=RETENTION_DAYS)
    result = {"compressed": 0, "expired": 0}
    for path in logs.iterdir():
        if path.is_symlink() or not path.is_file():
            continue
        match = DATED_LOG.fullmatch(path.name)
        if match:
            day = datetime.strptime(match[1], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            # A suffix denotes the START of the logging interval.
            if day + timedelta(days=1) <= cutoff:
                path.unlink()
                result["expired"] += 1
            elif day + timedelta(days=1) <= current and path.suffix != ".gz":
                target = path.with_name(path.name + ".gz")
                temporary = target.with_suffix(".gz.tmp")
                if target.exists():
                    raise ValueError("Duplicate daily log archive")
                try:
                    with path.open("rb") as source, gzip.open(temporary, "wb") as output:
                        shutil.copyfileobj(source, output)
                    os.chmod(temporary, 0o640)
                    os.replace(temporary, target)
                    path.unlink()
                    result["compressed"] += 1
                finally:
                    temporary.unlink(missing_ok=True)
    return result


def merge_decision(index, settings, free_bytes, used_percent):
    if not INDEX_NAME.fullmatch(index["index"]):
        return "SKIPPED_FOREIGN_INDEX"
    if index["status"] != "open" or index["health"] != "green":
        return "SKIPPED_HEALTH"
    deleted = int(index.get("docs.deleted") or 0)
    live = int(index.get("docs.count") or 0)
    if not deleted or deleted / max(1, live + deleted) <= 0.1:
        return "SKIPPED_NO_DELETIONS"
    blocks = settings.get("index.blocks.write") == "true" or settings.get("index.blocks.read_only") == "true"
    if not blocks:
        return "SKIPPED_WRITABLE"
    if used_percent >= 85 or free_bytes < max(1024**3, 3 * int(index.get("store.size") or 0)):
        return "SKIPPED_DISK_SPACE"
    return "ELIGIBLE"


def next_run(unit):
    return command("systemctl", "show", unit, "--property=NextElapseUSecRealtime", "--value")


def elastic_disk_path():
    mounts = json.loads(command("docker", "inspect", "ragflow-pg-es01-1", "--format", "{{json .Mounts}}"))
    return Path(next(mount["Source"] for mount in mounts if mount["Destination"] == "/usr/share/elasticsearch/data"))


def run(logs, do_merge=False):
    report = {"schema_version": 1, "configured": True, "generated_at": now()}
    previous = logs / "maintenance" / "elastic-last-run.json"
    try:
        data_path = elastic_disk_path()
        disk = shutil.disk_usage(data_path)
        used = round(100 * disk.used / disk.total, 1)
        report["disk"] = {"total_bytes": disk.total, "free_bytes": disk.free, "used_percent": used}
        report["logs"] = {
            "retention_days": RETENTION_DAYS,
            "loki_retention_hours": 1440,
            "scope": ["ragflow_files", "loki"],
            **clean_logs(logs),
        }
        indices = elastic("/_cat/indices?format=json&bytes=b&h=index,health,status,docs.count,docs.deleted,store.size")
        owned = [index for index in indices if INDEX_NAME.fullmatch(index["index"])]
        report["elastic"] = {
            "store_bytes": sum(int(index.get("store.size") or 0) for index in owned),
            "deleted_documents": sum(int(index.get("docs.deleted") or 0) for index in owned),
        }
        if do_merge:
            run_result = {"started_at": now(), "indices": [], "outcome": "NO_WORK"}
            atomic_json(previous, {**run_result, "finished_at": None, "outcome": "RUNNING"})
            try:
                tasks = elastic("/_tasks?actions=indices:admin/forcemerge*&detailed=false")
                if any(node.get("tasks") for node in tasks.get("nodes", {}).values()):
                    run_result["outcome"] = "SKIPPED_RUNNING_TASK"
                else:
                    settings = elastic("/_all/_settings?flat_settings=true&filter_path=*.settings.index.blocks.*")
                    for index in owned:
                        disk = shutil.disk_usage(data_path)
                        decision = merge_decision(index, settings.get(index["index"], {}).get("settings", {}), disk.free, 100 * disk.used / disk.total)
                        if decision == "ELIGIBLE":
                            response = elastic(f"/{index['index']}/_forcemerge?only_expunge_deletes=true&flush=true", "POST")
                            if response.get("_shards", {}).get("failed", 0):
                                raise RuntimeError("Elasticsearch merge shard failure")
                            decision = "MERGED"
                            run_result["outcome"] = "COMPLETE"
                        run_result["indices"].append({"index": index["index"], "outcome": decision})
                    if run_result["outcome"] == "NO_WORK" and any(item["outcome"] != "SKIPPED_NO_DELETIONS" for item in run_result["indices"]):
                        run_result["outcome"] = "SKIPPED"
            except Exception:
                run_result["outcome"] = "ERROR"
                raise
            finally:
                run_result["finished_at"] = now()
                atomic_json(previous, run_result)
        if previous.exists():
            report["elastic"]["last_run"] = json.loads(previous.read_text())
        report["next_run"] = {
            "report": next_run("ragflow-maintenance-report.timer"),
            "elastic": next_run("ragflow-maintenance-elastic.timer"),
        }
    except Exception:
        report["error"] = "MAINTENANCE_FAILED"
        raise
    finally:
        report["generated_at"] = now()
        atomic_json(logs / "maintenance" / "status.json", report)
    return report


def main():
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["report", "elastic"])
    parser.add_argument("--logs", type=Path, default=Path("/opt/ragflow-pg/docker/ragflow-logs"))
    args = parser.parse_args()
    # A common lock prevents report/merge timers from replacing each other's result.
    with open("/run/lock/ragflow-maintenance.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        print(json.dumps(run(args.logs, args.action == "elastic")))


if __name__ == "__main__":
    main()
