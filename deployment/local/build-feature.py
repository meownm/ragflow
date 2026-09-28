"""Build a Nexus developer image from this exact local checkout, including selected new files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def run(*args: str) -> None:
    subprocess.run(args, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include", action="append", default=[], metavar="UNTRACKED_PATH",
                        help="Exact non-ignored new source file to include; repeat for each file")
    parser.add_argument("--host", default="apt@192.168.1.175")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "ragflow_nuc8_ed25519")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if not args.ssh_key.is_file():
        parser.error(f"SSH key does not exist: {args.ssh_key}")
    untracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "--others",
                                         "--exclude-standard", "-z"]).decode().strip("\0").split("\0")
    omitted = sorted(set(filter(None, untracked)) - set(args.include))
    if omitted:
        raise ValueError("Untracked files need explicit --include or removal: " + ", ".join(omitted))
    profile = {"platform": "linux/amd64", "mode": "feature"}
    with tempfile.TemporaryDirectory(prefix="ragflow-feature-") as directory:
        temporary = Path(directory)
        candidate = temporary / "snapshot"
        profile_file = temporary / "profile.json"
        profile_file.write_text(json.dumps(profile), encoding="utf-8")
        command = [sys.executable, str(root / "tools/quality/candidate.py"), "snapshot",
                   "--candidate", str(candidate), "--root", str(root),
                   "--allow-dirty", "--build-profile", str(profile_file)]
        for name in args.include:
            command.extend(["--include", name])
        run(*command)
        source_id = json.loads((candidate / "candidate.json").read_text(encoding="utf-8"))["source_id"]
        archive = temporary / "snapshot.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(candidate, arcname="snapshot")
        ssh = ["ssh", "-i", str(args.ssh_key), "-o", "BatchMode=yes", args.host]
        scp = ["scp", "-i", str(args.ssh_key), "-o", "BatchMode=yes"]
        remote = subprocess.check_output([*ssh, "mktemp -d /tmp/ragflow-feature-XXXXXXXX"], text=True).strip()
        if not remote.startswith("/tmp/ragflow-feature-") or not remote.rsplit("-", 1)[-1].isalnum():
            raise ValueError("Unexpected remote temporary directory")
        try:
            run(*scp, str(archive), f"{args.host}:{remote}/snapshot.tar.gz")
            run(*ssh, "tar -xzf " + remote + "/snapshot.tar.gz -C " + remote +
                " && bash " + remote + "/snapshot/source/deployment/runner/build-feature-image.sh " +
                remote + "/snapshot")
        finally:
            run(*ssh, "sudo rm -rf -- " + remote)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Feature image build failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
