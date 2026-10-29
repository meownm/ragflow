"""Maintenance admission remains bound to actual reviewed sources and preserved recovery."""

import copy

import pytest

from deployment.sql_assistant_maintenance import CHECKS, RECIPE, TABLES, PROBE, checksum, metadata_network, validate_contracts, validate_receipt


def test_metadata_network_is_not_the_first_web_network():
    def info(*names):
        return {"NetworkSettings": {"Networks": dict.fromkeys(names, {})}}

    assert metadata_network(info("diagram", "metadata"), info("metadata")) == "metadata"
    with pytest.raises(ValueError, match="unambiguous"):
        metadata_network(info("diagram"), info("metadata"))
    with pytest.raises(ValueError, match="unambiguous"):
        metadata_network(info("one", "two"), info("one", "two"))


@pytest.fixture
def receipt(tmp_path):
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"recovery fixture")
    rehearsal = tmp_path / "rehearsal.json"
    rehearsal.write_text("{}")
    value = {
        "schema": 1,
        "recipe": RECIPE,
        "status": "PASS",
        "old": {"image_id": "sha256:" + "a" * 64, "schema_sha256": "b" * 64},
        "new": {"image_id": "sha256:" + "c" * 64, "schema_sha256": "d" * 64},
        "checks": {key: "PASS" for key in CHECKS},
        "probe_sha256": checksum(PROBE),
        "backup": {"path": str(backup), "sha256": checksum(backup)},
        "rehearsal": {"path": str(rehearsal), "sha256": checksum(rehearsal)},
    }
    previous = {"web_image_id": value["old"]["image_id"], "schema_sha256": value["old"]["schema_sha256"]}
    return value, previous


def test_receipt_accepts_only_the_checked_source_pair_and_complete_recovery(receipt):
    value, previous = receipt
    validate_receipt(value, previous, value["new"]["image_id"], value["new"]["schema_sha256"])
    for key in ("image_id", "schema_sha256"):
        changed = copy.deepcopy(value)
        changed["old"][key] = "unrelated"
        with pytest.raises(ValueError, match="another installed"):
            validate_receipt(changed, previous, value["new"]["image_id"], value["new"]["schema_sha256"])
        changed = copy.deepcopy(value)
        changed["new"][key] = "unrelated"
        with pytest.raises(ValueError, match="another candidate"):
            validate_receipt(changed, previous, value["new"]["image_id"], value["new"]["schema_sha256"])
    changed = copy.deepcopy(value)
    changed["checks"]["restore"] = "INCOMPLETE"
    with pytest.raises(ValueError, match="incomplete"):
        validate_receipt(changed, previous, value["new"]["image_id"], value["new"]["schema_sha256"])
    with open(value["backup"]["path"], "ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ValueError, match="backup"):
        validate_receipt(value, previous, value["new"]["image_id"], value["new"]["schema_sha256"])


def test_schema_recipe_refuses_other_models_and_initializer_changes():
    old = {"files": {"api/db/db_models.py": "old", "api/db/init_data.py": "same"}, "ddl": {"existing": ["CREATE TABLE existing (...) ", []]}}
    new = copy.deepcopy(old)
    new["files"]["api/db/db_models.py"] = "new"
    new["ddl"].update({table: ["CREATE TABLE ...", []] for table in TABLES})
    validate_contracts(old, new)
    changed = copy.deepcopy(new)
    changed["files"]["api/db/init_data.py"] = "other"
    with pytest.raises(ValueError, match="Unrelated"):
        validate_contracts(old, changed)
    changed = copy.deepcopy(new)
    changed["ddl"]["existing"] = ["ALTERED", []]
    with pytest.raises(ValueError, match="Existing model"):
        validate_contracts(old, changed)
    # Byte changes in comments/format/recoverable mojibake do not authorize new logic.
    old["canonical_ast"] = {"api/db/init_data.py": "same-ast"}
    changed = copy.deepcopy(new)
    changed["files"]["api/db/init_data.py"] = "encoding-repaired"
    changed["canonical_ast"] = {"api/db/init_data.py": "same-ast"}
    validate_contracts(old, changed)
    changed["canonical_ast"]["api/db/init_data.py"] = "changed-logic"
    with pytest.raises(ValueError, match="Unrelated"):
        validate_contracts(old, changed)
