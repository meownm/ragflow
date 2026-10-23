"""Promote a verified packet; mutable GitHub channels are updated last."""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from tools.quality.release_evidence import bundle_identity, require_publishable, validate_publication, verify_files
from tools.quality.release_runtime import verify_qualification_origin, write_json


def command(*args, allow_missing=False, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=600, **kwargs)
    if result.returncode:
        if allow_missing and ("404" in result.stderr or "MANIFEST_UNKNOWN" in result.stderr or "manifest unknown" in result.stderr or result.stderr.strip() == "release not found"):
            return None
        raise RuntimeError(f"{args[0]} operation failed (exit {result.returncode})")
    return result.stdout


def manifest_digest(reference: str, authfile: Path) -> str | None:
    # Hash the original manifest bytes, including index manifests and final newline.
    result = subprocess.run(["skopeo", "inspect", "--authfile", str(authfile), "--raw", "docker://" + reference], capture_output=True, timeout=600)
    if result.returncode:
        error = result.stderr.decode(errors="replace")
        if "MANIFEST_UNKNOWN" in error or "manifest unknown" in error:
            return None
        raise RuntimeError("registry manifest inspection failed")
    return "sha256:" + hashlib.sha256(result.stdout).hexdigest()


def promote_image(receipt: dict, authfile: Path) -> None:
    image = receipt["image"]
    expected = image["staging_digest"].split("@", 1)[1]
    if manifest_digest(image["staging_digest"], authfile) != expected:
        raise ValueError("staging image manifest differs from qualified digest")
    existing = manifest_digest(image["reference"], authfile)
    if existing is not None and existing != expected:
        raise ValueError("immutable SHA tag already identifies different content")
    if existing is None:
        command("skopeo", "copy", "--authfile", str(authfile), "--preserve-digests", "docker://" + image["staging_digest"], "docker://" + image["reference"])
    if manifest_digest(image["reference"], authfile) != expected:
        raise ValueError("published manifest digest mismatch")


def channel_is_newer(channel: dict | None, run_id: str) -> bool:
    if not channel:
        return False
    return int(channel["ci_run_id"]) > int(run_id)


def archive_packet(packet: Path, destination: Path) -> None:
    """Canonical metadata makes retries produce the same immutable asset."""
    with destination.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in sorted(packet.rglob("*")):
                if not path.is_file() or path.is_symlink():
                    continue
                info = archive.gettarinfo(str(path), "qualification/" + path.relative_to(packet).as_posix())
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o644
                info.pax_headers = {}
                with path.open("rb") as source:
                    archive.addfile(info, source)


def verify_assets(tag: str, repository: str, assets: Path, destination: Path) -> None:
    destination.mkdir()
    command("gh", "release", "download", tag, "--repo", repository, "--dir", str(destination))
    if {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in destination.iterdir()} != {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in assets.iterdir()}:
        raise ValueError("GitHub published asset verification failed")


def _github_pages(endpoint: str):
    """Read every page; malformed or repeated API identities cannot hide evidence."""
    seen = set()
    page = 1
    while True:
        values = json.loads(command("gh", "api", f"{endpoint}?per_page=100&page={page}"))
        if not isinstance(values, list):
            raise ValueError("invalid GitHub publication history")
        for value in values:
            if not isinstance(value, dict) or type(value.get("id")) is not int or value["id"] <= 0 or value["id"] in seen:
                raise ValueError("invalid or repeated GitHub publication identity")
            seen.add(value["id"])
            yield value
        if len(values) < 100:
            return
        page += 1


def _github_asset_bytes(repository: str, asset_id: int) -> bytes:
    # Binary mode preserves the digest across publisher OS newline conventions.
    result = subprocess.run(["gh", "api", f"repos/{repository}/releases/assets/{asset_id}", "--header", "Accept: application/octet-stream"], capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError("GitHub publication evidence readback failed")
    return result.stdout


def _guard_initial_runtime_bootstrap(receipt: dict, packet: Path, tag: str, qualification_hash: str) -> bool:
    """Bootstrap is consumed by the first published qualified bundle, under the publisher queue."""
    upgrade_path = packet / "upgrade/qualified-upgrade.json"
    if not upgrade_path.exists():
        return False
    upgrade = json.loads(upgrade_path.read_text(encoding="utf-8"))
    if not isinstance(upgrade, dict):
        raise ValueError("invalid runtime upgrade evidence")
    if upgrade.get("purpose") != "initial-runtime-baseline-upgrade":
        return False
    repository = receipt["ci"]["repository"]
    expected_bundle = bundle_identity(receipt)
    evidence_names = {"candidate-receipt.json", "publication-receipt.json"}
    for release in _github_pages(f"repos/{repository}/releases"):
        if type(release.get("draft")) is not bool or not isinstance(release.get("tag_name"), str):
            raise ValueError("invalid GitHub release history")
        if release["draft"]:
            continue
        assets = {}
        for asset in _github_pages(f"repos/{repository}/releases/{release['id']}/assets"):
            name = asset.get("name")
            if not isinstance(name, str) or name in assets:
                raise ValueError("invalid or duplicate GitHub release assets")
            assets[name] = asset
        present = evidence_names & assets.keys()
        if not present:
            continue
        if present != evidence_names:
            raise ValueError("published release has incomplete qualification evidence")
        decoded = {}
        hashes = {}
        for name in sorted(evidence_names):
            raw = _github_asset_bytes(repository, assets[name]["id"])
            hashes[name] = hashlib.sha256(raw).hexdigest()
            if assets[name].get("digest") != "sha256:" + hashes[name]:
                raise ValueError("published qualification asset digest mismatch")
            decoded[name] = json.loads(raw)
        prior = decoded["candidate-receipt.json"]
        proof = decoded["publication-receipt.json"]
        if not isinstance(prior, dict) or not isinstance(proof, dict):
            raise ValueError("invalid published qualification evidence")
        validate_publication(prior, proof, hashes["candidate-receipt.json"])
        if prior["ci"]["repository"] != repository or proof["release_tag"] != release["tag_name"]:
            raise ValueError("foreign published qualification evidence")
        if release["tag_name"] != tag or proof["qualification_sha256"] != qualification_hash or proof["bundle_id"] != expected_bundle:
            raise ValueError("initial runtime bootstrap already consumed by a published release")
    return True


class Journal:
    """Append-only GitHub draft assets survive process and publisher VM loss."""

    def __init__(self, repository: str, tag: str, receipt: dict, workspace: Path):
        self.repository = repository
        self.tag = "publication-" + tag
        self.workspace = workspace
        self.identity = {"bundle_id": bundle_identity(receipt), "release_tag": tag, "qualification_sha256": os.environ["QUALIFICATION_SHA256"]}
        existing = command("gh", "release", "view", self.tag, "--repo", repository, "--json", "assets,isDraft", allow_missing=True)
        self.sequence = 0
        self.state = dict(self.identity)
        if existing is None:
            command(
                "gh",
                "release",
                "create",
                self.tag,
                "--repo",
                repository,
                "--draft",
                "--prerelease",
                "--latest=false",
                "--target",
                receipt["source_revision"],
                "--title",
                self.tag,
                "--notes",
                "Durable publication journal; this is not a product release.",
            )
        else:
            value = json.loads(existing)
            if not value["isDraft"]:
                raise ValueError("publication journal must remain a draft")
            names = sorted(item["name"] for item in value["assets"])
            if names:
                name = names[-1]
                if not all(__import__("re").fullmatch(r"journal-\d{6}\.json", item) for item in names):
                    raise ValueError("invalid publication journal assets")
                self.sequence = int(name[8:14])
                saved = workspace / "journal-prior"
                saved.mkdir()
                command("gh", "release", "download", self.tag, "--repo", repository, "--pattern", name, "--dir", str(saved))
                self.state = json.loads((saved / name).read_text())
                if any(self.state.get(key) != value for key, value in self.identity.items()):
                    raise ValueError("publication journal bundle conflict")

    def save(self, **state):
        self.state.update(state)
        self.sequence += 1
        path = self.workspace / f"journal-{self.sequence:06d}.json"
        write_json(path, self.state)
        # Never clobber: an interrupted upload is reconciled by reading the last asset.
        command("gh", "release", "upload", self.tag, str(path), "--repo", self.repository)
        check = self.workspace / f"journal-check-{self.sequence}"
        check.mkdir()
        command("gh", "release", "download", self.tag, "--repo", self.repository, "--pattern", path.name, "--dir", str(check))
        if (check / path.name).read_bytes() != path.read_bytes():
            raise ValueError("publication journal readback mismatch")


def publish(receipt: dict, packet: Path, tag: str, nightly: bool) -> None:
    require_publishable(receipt)
    verify_files(packet, receipt["artifacts"]["files"])
    repository = receipt["ci"]["repository"]
    if repository != os.environ["GITHUB_REPOSITORY"]:
        raise ValueError("publication repository mismatch")
    if not __import__("re").fullmatch(r"(?:v\d+\.\d+\.\d+|nightly-\d+-\d+)", tag):
        raise ValueError("invalid immutable release tag")
    with tempfile.TemporaryDirectory(prefix="ragflow-release-") as directory:
        workspace = Path(directory)
        canonical_receipt = workspace / "candidate-receipt.json"
        write_json(canonical_receipt, receipt)
        qualification_hash = hashlib.sha256(canonical_receipt.read_bytes()).hexdigest()
        if qualification_hash != os.environ["QUALIFICATION_SHA256"]:
            raise ValueError("qualification receipt changed before publication")
        proof = {
            "schema": 2,
            "status": "PASS",
            "repository": repository,
            "release_tag": tag,
            "qualification_sha256": qualification_hash,
            "bundle_id": bundle_identity(receipt),
            "image_digest": receipt["image"]["digest"],
            "qualification_artifact": json.loads(os.environ["QUALIFICATION_ARTIFACT_ORIGIN"]),
        }
        validate_publication(receipt, proof, qualification_hash)
        bootstrap = _guard_initial_runtime_bootstrap(receipt, packet, tag, qualification_hash)
        journal = Journal(repository, tag, receipt, workspace)
        journal.save(stage="image-pending")
        authfile = workspace / "auth.json"
        registry = receipt["image"]["reference"].split("/", 1)[0]
        credential = base64.b64encode((os.environ["NEXUS_RELEASE_USER"] + ":" + os.environ["NEXUS_RELEASE_PASSWORD"]).encode()).decode()
        staging = receipt["image"]["staging_digest"].split("/", 1)[0]
        if staging == registry:
            raise ValueError("staging and publication need distinct credential endpoints")
        staging_credential = base64.b64encode((os.environ["NEXUS_STAGING_USER"] + ":" + os.environ["NEXUS_STAGING_PASSWORD"]).encode()).decode()
        write_json(authfile, {"auths": {registry: {"auth": credential}, staging: {"auth": staging_credential}}})
        authfile.chmod(0o600)
        promote_image(receipt, authfile)
        journal.save(stage="image-verified", image_digest=receipt["image"]["digest"])
        assets = workspace / "assets"
        assets.mkdir()
        for source in (packet / "cli").iterdir():
            name = source.name.replace("ragflow-cli-", f"ragflow-cli-{tag}-", 1)
            shutil.copy2(source, assets / name)
        for source in (packet / "python").iterdir():
            shutil.copy2(source, assets / source.name)
        archive_packet(packet, assets / "qualification.tar.gz")
        for name in ("install.sh", "install.ps1"):
            shutil.copy2(packet / "installers" / name, assets / name)
        shutil.copy2(canonical_receipt, assets / "candidate-receipt.json")
        write_json(assets / "publication-receipt.json", proof)
        hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(assets.iterdir())}
        manifest = {
            "schema": 2,
            "release_tag": tag,
            "source_revision": receipt["source_revision"],
            "qualification_sha256": qualification_hash,
            "bundle_id": bundle_identity(receipt),
            "image_digest": receipt["image"]["digest"],
            "ci_run_id": receipt["ci"]["run_id"],
            "ci_attempt": receipt["ci"]["attempt"],
            "files": hashes,
            "cli_profile": "portable-no-cgo",
            "python_packages": receipt["artifacts"]["python_packages"],
        }
        write_json(assets / "release-manifest.json", manifest)
        sums = "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in sorted(assets.iterdir()))
        (assets / "SHA256SUMS").write_text(sums)
        notes = workspace / "notes.md"
        notes.write_text(
            f"Source: `{receipt['source_revision']}`\n\nImage: `{receipt['image']['digest']}`\n\n"
            f"CI: https://github.com/{repository}/actions/runs/{receipt['ci']['run_id']}\n\n"
            "Portable CLI: Linux/macOS/Windows amd64/arm64, without native Office/PDF parsing. "
            "Native parsing is qualified in the Linux image.\n\n"
            f"Install this fork with `GITHUB_REPO={repository}` or `-GitHubRepo {repository}`.\n"
        )
        if bootstrap:
            with notes.open("a", encoding="utf-8") as output:
                output.write(
                    "\nInitial runtime recovery baseline: historical-unqualified. "
                    "Recovery qualification covers the explicitly approved historical image and its disposable database snapshot; "
                    "it does not establish a previous qualified release. This bootstrap is permitted once.\n"
                )
        journal.save(stage="github-draft-pending")
        existing = command("gh", "release", "view", tag, "--repo", repository, "--json", "isDraft", allow_missing=True)
        if existing is None:
            args = ["gh", "release", "create", tag, "--repo", repository, "--target", receipt["source_revision"], "--draft", "--latest=false", "--title", tag, "--notes-file", str(notes)]
            if nightly:
                args.append("--prerelease")
            command(*args)
        else:
            # Resume a draft, or verify an already completed immutable release.
            prior = workspace / "prior"
            prior.mkdir()
            command("gh", "release", "download", tag, "--repo", repository, "--pattern", "release-manifest.json", "--dir", str(prior), allow_missing=True)
            old = prior / "release-manifest.json"
            if old.exists():
                try:
                    prior_manifest = json.loads(old.read_text())
                except (ValueError, UnicodeError) as exc:
                    raise ValueError("immutable GitHub release content conflict: invalid manifest") from exc
                if prior_manifest != manifest:
                    raise ValueError("immutable GitHub release content conflict")
            if not json.loads(existing)["isDraft"]:
                if not old.exists():
                    raise ValueError("completed release lacks trusted manifest")
                verify_assets(tag, repository, assets, workspace / "verify")
                if not nightly:
                    command("gh", "release", "edit", tag, "--repo", repository, "--latest=true")
                if nightly:
                    move_nightly(repository, receipt, tag, workspace, journal)
                journal.save(stage="PASS")
                return
        journal.save(stage="assets-pending")
        # A draft may have a partial first upload. Verify existing assets rather than overwrite.
        metadata = json.loads(command("gh", "release", "view", tag, "--repo", repository, "--json", "assets"))
        present = {item["name"]: item for item in metadata["assets"]}
        if set(present) - {path.name for path in assets.iterdir()}:
            raise ValueError("foreign release asset")
        for path in assets.iterdir():
            if path.name in present:
                if present[path.name].get("digest") != "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest():
                    raise ValueError("immutable release asset conflict")
            else:
                command("gh", "release", "upload", tag, str(path), "--repo", repository)
        verify_assets(tag, repository, assets, workspace / "verify")
        journal.save(stage="visibility-pending")
        command("gh", "release", "edit", tag, "--repo", repository, "--draft=false", "--latest=" + ("false" if nightly else "true"))
        if nightly:
            move_nightly(repository, receipt, tag, workspace, journal)
        journal.save(stage="PASS")


def move_nightly(repository: str, receipt: dict, tag: str, workspace: Path, journal: Journal):
    """One atomic annotated-tag ref is the authoritative channel pointer."""
    endpoint = f"repos/{repository}/git"
    raw = command("gh", "api", endpoint + "/ref/tags/nightly", allow_missing=True)
    current = json.loads(raw)["object"] if raw else None
    previous = None
    if current:
        if current["type"] != "tag":
            raise ValueError("nightly pointer must be an annotated bundle tag")
        previous = json.loads(command("gh", "api", endpoint + "/tags/" + current["sha"]))
        previous = json.loads(previous["message"])
    if channel_is_newer(previous, receipt["ci"]["run_id"]):
        journal.save(stage="channel-superseded")
        return
    channel = {
        "schema": 2,
        "release_tag": tag,
        "source_revision": receipt["source_revision"],
        "image_digest": receipt["image"]["digest"],
        "ci_run_id": receipt["ci"]["run_id"],
        "bundle_id": bundle_identity(receipt),
        "qualification_sha256": os.environ["QUALIFICATION_SHA256"],
    }
    if previous == channel:
        return
    # After a crash, a pending tag object either is already visible or can be reused.
    old = journal.state.get("channel_old")
    new = journal.state.get("channel_new")
    if new and current and current["sha"] == new:
        journal.save(stage="channel-verified")
        return
    current_sha = current["sha"] if current else None
    if new and current_sha != old:
        raise ValueError("channel changed outside serialized publisher; reconciliation required")
    if not new:
        payload = {"tag": "nightly", "message": json.dumps(channel, sort_keys=True), "object": receipt["source_revision"], "type": "commit"}
        new = json.loads(command("gh", "api", endpoint + "/tags", "--input", "-", input=json.dumps(payload)))["sha"]
        old = current_sha
        journal.save(stage="channel-pending", channel_old=old, channel_new=new)
    if current:
        command("gh", "api", "--method", "PATCH", endpoint + "/refs/tags/nightly", "--input", "-", input=json.dumps({"sha": new, "force": True}))
    else:
        command("gh", "api", endpoint + "/refs", "--input", "-", input=json.dumps({"ref": "refs/tags/nightly", "sha": new}))
    actual = json.loads(command("gh", "api", endpoint + "/ref/tags/nightly"))["object"]["sha"]
    if actual != new:
        raise ValueError("nightly atomic pointer readback failed")
    journal.save(stage="channel-verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--packet", type=Path, required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--nightly", action="store_true")
    args = parser.parse_args()
    report = {"status": "INCOMPLETE", "tag": args.tag}
    try:
        raw = args.receipt.read_bytes()
        if hashlib.sha256(raw).hexdigest() != os.environ["QUALIFICATION_SHA256"]:
            raise ValueError("qualification receipt changed before publication")
        receipt = json.loads(raw)
        verify_qualification_origin(receipt, args.packet)
        publish(receipt, args.packet, args.tag, args.nightly)
        report["status"] = "PASS"
    finally:
        write_json(Path("publication-result.json"), report)


if __name__ == "__main__":
    main()
