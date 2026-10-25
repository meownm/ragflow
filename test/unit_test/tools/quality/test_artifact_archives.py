"""Archive bytes are never written before their extraction graph is validated."""

import io
import json
import tarfile

import pytest

from tools.quality import artifact_blobs
from tools.quality import artifact_docker as docker


def volume_archive(tmp_path, entries):
    path = tmp_path / "data.tar.gz"
    with tarfile.open(path, "w:gz") as bundle:
        for name, kind, target in entries:
            item = tarfile.TarInfo(name)
            item.type = kind
            item.linkname = target
            item.size = 4 if kind == tarfile.REGTYPE else 0
            bundle.addfile(item, io.BytesIO(b"data") if item.isfile() else None)
    return path


@pytest.mark.parametrize("name", ["../outside", "/absolute", "safe/../../outside", "C:/outside", "C:\\outside"])
def test_unsafe_member_path_is_rejected_before_volume_io(tmp_path, name):
    path = volume_archive(tmp_path, [(name, tarfile.REGTYPE, "")])
    calls = []
    engine = docker.Engine.__new__(docker.Engine)
    engine.pipe_archive = lambda *args: calls.append(args)
    with pytest.raises(ValueError):
        engine.volume_io("owned-pin", "restore", path)
    assert not calls


@pytest.mark.parametrize("reverse", [False, True])
def test_symlink_bridge_is_rejected_independently_of_header_order(tmp_path, reverse):
    entries = [("bridge", tarfile.SYMTYPE, "../../outside"), ("bridge/working-data", tarfile.REGTYPE, "")]
    with pytest.raises(ValueError, match="non-directory"):
        docker.validate_volume_archive(volume_archive(tmp_path, list(reversed(entries)) if reverse else entries))


@pytest.mark.parametrize(
    "entries",
    [
        [("file", tarfile.REGTYPE, ""), ("./file", tarfile.REGTYPE, "")],
        [("file", tarfile.REGTYPE, ""), ("file/child", tarfile.REGTYPE, "")],
        [("hard", tarfile.LNKTYPE, "../outside")],
        [("hard", tarfile.LNKTYPE, "missing")],
        [("one", tarfile.LNKTYPE, "two"), ("two", tarfile.LNKTYPE, "one")],
        [("link", tarfile.SYMTYPE, "outside"), ("hard", tarfile.LNKTYPE, "link")],
        [("device", tarfile.CHRTYPE, "")],
        [("pipe", tarfile.FIFOTYPE, "")],
    ],
)
def test_ambiguous_duplicate_or_special_members_are_rejected(tmp_path, entries):
    with pytest.raises(ValueError):
        docker.validate_volume_archive(volume_archive(tmp_path, entries))


def test_regular_data_modes_unicode_and_links_remain_supported(tmp_path):
    path = volume_archive(
        tmp_path,
        [
            (".", tarfile.DIRTYPE, ""),
            ("данные", tarfile.REGTYPE, ""),
            ("hard", tarfile.LNKTYPE, "./данные"),
            ("relative", tarfile.SYMTYPE, "данные"),
            ("external-link-metadata", tarfile.SYMTYPE, "/models/external"),
        ],
    )
    docker.validate_volume_archive(path)


def test_truncated_stream_is_not_a_valid_restore_archive(tmp_path):
    path = volume_archive(tmp_path, [("data", tarfile.REGTYPE, "")])
    path.write_bytes(path.read_bytes()[:12])
    with pytest.raises((ValueError, OSError)):
        docker.validate_volume_archive(path)


@pytest.mark.parametrize("tamper", ["path", "duplicate", "size", "identity"])
def test_valid_checksum_does_not_authorize_malformed_image_manifest(tmp_path, tamper):
    blob = artifact_blobs.put(io.BytesIO(b"image data"), tmp_path)
    manifest = tmp_path / "images/image.image.json"
    manifest.parent.mkdir()
    item = {"header": {"name": "layer.tar", "size": 10}, "blob": blob}
    value = {"schema": 1, "image_id": "sha256:expected", "members": [item]}
    if tamper == "path":
        item["header"]["name"] = "../outside"
    elif tamper == "duplicate":
        value["members"].append({**item, "header": {"name": "./layer.tar", "size": 10}})
    elif tamper == "size":
        item["header"]["size"] = 100
    else:
        value["image_id"] = "sha256:foreign"
    manifest.write_text(json.dumps(value))
    packet = docker.archive_info(manifest, kind="image-cas", image_id="sha256:expected")
    with pytest.raises(ValueError):
        docker.Engine.__new__(docker.Engine).verify_image(packet)
