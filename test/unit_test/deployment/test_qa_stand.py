"""The permanent Ubuntu QA stack must stay isolated and preserve its credentials."""

from pathlib import Path

import yaml

from deployment.qa import setup_ubuntu


ROOT = Path(__file__).resolve().parents[3]


def test_qa_stack_has_private_ports_and_own_persistent_volumes():
    compose = yaml.safe_load((ROOT / "deployment/qa/compose.yml").read_text(encoding="utf-8"))
    assert compose["name"] == "ragflow-qa"
    assert compose["services"]["app"]["ports"] == ["127.0.0.1:19382:80", "127.0.0.1:19383:9381"]
    assert all(service["restart"] == "unless-stopped" for service in compose["services"].values())
    assert set(compose["volumes"]) == {"qa_pgdata", "qa_redis", "qa_objects", "qa_index"}
    assert not any("ragflow-local" in str(service) for service in compose["services"].values())


def test_qa_image_update_preserves_generated_passwords(tmp_path, monkeypatch):
    monkeypatch.setattr(setup_ubuntu, "ROOT", tmp_path)
    original = setup_ubuntu.settings("registry/ragflow:" + "a" * 40, "http://192.168.1.125:11435")
    updated = setup_ubuntu.settings("registry/ragflow:" + "b" * 40, "http://192.168.1.125:11435")
    for name in ("QA_STORAGE_PASSWORD", "QA_ADMIN_EMAIL", "QA_ADMIN_PASSWORD"):
        assert updated[name] == original[name]
    assert updated["RAGFLOW_IMAGE"].endswith("b" * 40)
    assert (tmp_path / ".env").is_file()
