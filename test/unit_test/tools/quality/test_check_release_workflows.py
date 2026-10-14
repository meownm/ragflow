"""A queue compatibility exception must never weaken release workflow linting."""

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from tools.quality import check_release_workflows as workflows


RELEASE = """name: release
'on': workflow_dispatch
jobs:
  publish:
    runs-on: ubuntu-24.04
    concurrency:
      group: release-publication
      cancel-in-progress: false
      queue: max
    steps:
      - run: echo ready
"""


@pytest.fixture
def root(tmp_path):
    repository = tmp_path / "repository"
    for relative in workflows.WORKFLOWS:
        path = repository / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(RELEASE if path.name == "release.yml" else "name: other\n", encoding="utf-8")
    return repository


def diagnostic(location, **overrides):
    return {
        "filepath": ".github/workflows/release.yml",
        "kind": "syntax-check",
        "message": workflows.QUEUE_DIAGNOSTIC,
        "line": location.line,
        "column": location.column,
        **overrides,
    }


def test_valid_queue_is_bound_to_actual_source_location(root):
    assert workflows.validate_queue(root) == workflows.QueueLocation(9, 7)


@pytest.mark.parametrize(
    "old,new",
    [
        ("queue: max", "queue: single"),
        ("queue: max", "queue: ${{ vars.QUEUE }}"),
        ("queue: max", "queue: max\n      queue: max"),
        ("      queue: max\n", ""),
        ("cancel-in-progress: false", "cancel-in-progress: true"),
        ("cancel-in-progress: false", 'cancel-in-progress: "false"'),
        ("cancel-in-progress: false", "cancel-in-progress: ${{ vars.CANCEL }}"),
        ("      cancel-in-progress: false\n", ""),
        ("group: release-publication", "group: release-${{ github.run_id }}"),
        ("publish:", "another-job:"),
    ],
)
def test_invalid_publication_queue_fails_before_tool_download(root, old, new, monkeypatch):
    (root / ".github/workflows/release.yml").write_text(RELEASE.replace(old, new), encoding="utf-8")
    monkeypatch.setattr(workflows, "actionlint_binary", lambda *_: pytest.fail("invalid queue downloaded a tool"))
    with pytest.raises(ValueError):
        workflows.check(root, root.parent / "cache")


def test_queue_on_other_workflow_is_not_granted_compatibility_exception(root):
    (root / workflows.WORKFLOWS[0]).write_text(RELEASE, encoding="utf-8")
    with pytest.raises(ValueError, match="only permitted"):
        workflows.validate_queue(root)


@pytest.mark.parametrize(
    "override",
    [
        {"filepath": ".github/workflows/tests.yml"},
        {"kind": "expression"},
        {"message": workflows.QUEUE_DIAGNOSTIC + "."},
        {"line": 8},
        {"column": 8},
    ],
)
def test_compatibility_exception_matches_only_exact_diagnostic(root, override):
    location = workflows.validate_queue(root)
    assert workflows._queue_compatibility_diagnostic(diagnostic(location), location)
    assert not workflows._queue_compatibility_diagnostic(diagnostic(location, **override), location)


def test_other_actionlint_errors_remain_blocking_and_sources_are_not_rewritten(root, monkeypatch, capsys):
    import json

    before = {relative: (root / relative).read_bytes() for relative in workflows.WORKFLOWS}
    location = workflows.validate_queue(root)
    monkeypatch.setattr(workflows, "actionlint_binary", lambda *_: Path("verified-actionlint"))

    def run(command, **options):
        assert command == ["verified-actionlint", "-format", "{{json .}}", *workflows.WORKFLOWS]
        assert options["cwd"] == root
        return SimpleNamespace(
            returncode=1,
            stderr="",
            stdout=json.dumps(
                [
                    diagnostic(location),
                    diagnostic(location, message="unexpected key typo", line=10),
                ]
            ),
        )

    monkeypatch.setattr(workflows.subprocess, "run", run)
    assert workflows.check(root, root.parent / "cache") == 1
    assert "unexpected key typo" in capsys.readouterr().out
    assert before == {relative: (root / relative).read_bytes() for relative in workflows.WORKFLOWS}


def test_sole_verified_queue_diagnostic_passes(root, monkeypatch):
    import json

    location = workflows.validate_queue(root)
    monkeypatch.setattr(workflows, "actionlint_binary", lambda *_: Path("verified-actionlint"))
    monkeypatch.setattr(
        workflows.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stderr="",
            stdout=json.dumps([diagnostic(location)]),
        ),
    )
    assert workflows.check(root, root.parent / "cache") == 0


def test_cache_must_be_outside_workspace(root, monkeypatch):
    monkeypatch.setattr(workflows, "_download", lambda *_: pytest.fail("unsafe cache downloaded a tool"))
    with pytest.raises(ValueError, match="outside the workspace"):
        workflows.actionlint_binary(root, root / ".cache")


@pytest.fixture
def verified_cache(root, monkeypatch):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("actionlint.exe", b"verified executable")
    archive = stream.getvalue()
    archive_name = f"actionlint_{workflows.VERSION}_windows_amd64.zip"
    checksums = f"{hashlib.sha256(archive).hexdigest()}  {archive_name}\n".encode()
    checksums_name = f"actionlint_{workflows.VERSION}_checksums.txt"
    assets = {archive_name: archive, checksums_name: checksums}
    monkeypatch.setattr(workflows, "CHECKSUMS_SHA256", hashlib.sha256(checksums).hexdigest())
    monkeypatch.setattr(workflows.platform, "system", lambda: "Windows")
    monkeypatch.setattr(workflows.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(workflows, "_download", lambda name, _limit: assets[name])
    cache = root.parent / "cache"
    binary = workflows.actionlint_binary(root, cache)
    assert binary.read_bytes() == b"verified executable"
    return cache, binary


@pytest.mark.parametrize("filename", ["actionlint.exe", "archive", "checksums"])
def test_tampered_cached_files_are_rejected_before_execution(root, verified_cache, filename):
    cache, binary = verified_cache
    names = {"archive": f"actionlint_{workflows.VERSION}_windows_amd64.zip", "checksums": f"actionlint_{workflows.VERSION}_checksums.txt"}
    (binary.parent / names.get(filename, filename)).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="differs from verified archive|checksum mismatch"):
        workflows.actionlint_binary(root, cache)


def test_download_checksum_mismatch_does_not_cache_unverified_content(tmp_path, monkeypatch):
    destination = tmp_path / "checksums.txt"
    monkeypatch.setattr(workflows, "_download", lambda *_: b"wrong content")
    with pytest.raises(ValueError, match="checksum mismatch"):
        workflows._checked_file(destination, "0" * 64, 256)
    assert not destination.exists()


def test_version_cache_symlink_is_rejected(root, tmp_path, monkeypatch):
    cache = tmp_path / "external-cache"
    cache.mkdir()
    monkeypatch.setattr(workflows.platform, "system", lambda: "Windows")
    monkeypatch.setattr(workflows.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(workflows, "_download", lambda *_: pytest.fail("unsafe cache downloaded a tool"))
    try:
        (cache / f"v{workflows.VERSION}").symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("creating symlinks requires host permission")
    with pytest.raises(ValueError, match="must not be a symlink"):
        workflows.actionlint_binary(root, cache)
