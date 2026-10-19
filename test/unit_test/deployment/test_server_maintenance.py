import gzip
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("server_maintenance", Path(__file__).parents[3] / "deployment/linux-pg/maintenance.py")
maintenance = importlib.util.module_from_spec(spec)
spec.loader.exec_module(maintenance)


def index(**updates):
    return {"index": "ragflow_" + "a" * 32, "health": "green", "status": "open", "docs.count": "80", "docs.deleted": "20", "store.size": "1000", **updates}


@pytest.mark.parametrize(
    "item,settings,free,used,expected",
    [
        (index(), {}, 4 * 1024**3, 70, "SKIPPED_WRITABLE"),
        (index(), {"index.blocks.read_only_allow_delete": "true"}, 4 * 1024**3, 70, "SKIPPED_WRITABLE"),
        (index(), {"index.blocks.write": "true"}, 4 * 1024**3, 70, "ELIGIBLE"),
        (index(), {"index.blocks.read_only": "true"}, 4 * 1024**3, 70, "ELIGIBLE"),
        (index(**{"docs.deleted": "0"}), {"index.blocks.write": "true"}, 4 * 1024**3, 70, "SKIPPED_NO_DELETIONS"),
        (index(), {"index.blocks.write": "true"}, 4 * 1024**3, 92, "SKIPPED_DISK_SPACE"),
        (index(), {"index.blocks.write": "true"}, 1000, 70, "SKIPPED_DISK_SPACE"),
        (index(health="red"), {"index.blocks.write": "true"}, 4 * 1024**3, 70, "SKIPPED_HEALTH"),
        (index(index="other_index"), {"index.blocks.write": "true"}, 4 * 1024**3, 70, "SKIPPED_FOREIGN_INDEX"),
    ],
)
def test_merge_guards(item, settings, free, used, expected):
    assert maintenance.merge_decision(item, settings, free, used) == expected


def test_expiration_preserves_full_sixty_days_and_unrelated_files(tmp_path):
    for name in ["server.log.2026-08-19", "server.log.2026-08-20", "server.log.2026-10-18", "server.log", "notes.txt", "server.log.1"]:
        (tmp_path / name).write_bytes(b"preserved log")
    result = maintenance.clean_logs(tmp_path, datetime(2026, 10, 19, tzinfo=timezone.utc))
    assert result == {"compressed": 2, "expired": 1}
    assert (tmp_path / "server.log.2026-08-20.gz").exists()
    assert gzip.decompress((tmp_path / "server.log.2026-10-18.gz").read_bytes()) == b"preserved log"
    assert maintenance.clean_logs(tmp_path, datetime(2026, 10, 19, tzinfo=timezone.utc)) == {"compressed": 0, "expired": 0}
    assert maintenance.clean_logs(tmp_path, datetime(2026, 10, 20, tzinfo=timezone.utc)) == {"compressed": 0, "expired": 1}
    for name in ["server.log", "notes.txt", "server.log.1"]:
        assert (tmp_path / name).read_bytes() == b"preserved log"


def test_weekly_run_never_merges_active_indices_and_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "elastic_disk_path", lambda: tmp_path)
    calls = []

    def fake_elastic(path, method="GET"):
        calls.append((path, method))
        if path.startswith("/_cat/"):
            return [index()]
        if path.startswith("/_tasks"):
            return {"nodes": {}}
        return {}

    monkeypatch.setattr(maintenance, "elastic", fake_elastic)
    monkeypatch.setattr(maintenance, "next_run", lambda _unit: "Sunday")
    result = maintenance.run(tmp_path, True)
    assert result["elastic"]["last_run"]["indices"][0]["outcome"] == "SKIPPED_WRITABLE"
    assert not any(method == "POST" for _, method in calls)
    assert json.loads((tmp_path / "maintenance/status.json").read_text())["configured"]


def test_weekly_run_targets_only_eligible_index_and_persists_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(maintenance, "elastic_disk_path", lambda: tmp_path)
    calls = []

    def fake_elastic(path, method="GET"):
        calls.append((path, method))
        if path.startswith("/_cat/"):
            return [index(), index(index="foreign")]
        if path.startswith("/_tasks"):
            return {"nodes": {}}
        if path.startswith("/_all/"):
            return {index()["index"]: {"settings": {"index.blocks.write": "true"}}}
        return {"_shards": {"failed": 1}}

    monkeypatch.setattr(maintenance, "elastic", fake_elastic)
    monkeypatch.setattr(maintenance.shutil, "disk_usage", lambda _: SimpleNamespace(total=10 * 1024**3, free=5 * 1024**3, used=5 * 1024**3))
    with pytest.raises(RuntimeError, match="shard failure"):
        maintenance.run(tmp_path, True)
    assert calls[-1] == (f"/{index()['index']}/_forcemerge?only_expunge_deletes=true&flush=true", "POST")
    assert json.loads((tmp_path / "maintenance/status.json").read_text())["error"] == "MAINTENANCE_FAILED"
    assert json.loads((tmp_path / "maintenance/elastic-last-run.json").read_text())["outcome"] == "ERROR"
