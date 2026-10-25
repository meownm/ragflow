"""Build a Nexus developer image from this exact local checkout, including selected new files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path


def run(*args: str, capture: bool = False) -> str:
    from tools.quality.workspace import start_process

    with start_process(args, stdout=subprocess.PIPE if capture else None, text=True) as process:
        output, _ = process.communicate()
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode, args, output)
        return output or ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include", action="append", default=[], metavar="UNTRACKED_PATH", help="Exact non-ignored new source file to include; repeat for each file")
    parser.add_argument("--host", default="apt@192.168.1.175")
    parser.add_argument("--ssh-key", type=Path, default=Path.home() / ".ssh" / "ragflow_nuc8_ed25519")
    parser.add_argument("--local", action="store_true", help="Build in Docker Desktop with persistent local caches; no registry push")
    parser.add_argument("--force", action="store_true", help="Rerun the image build and import check")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from tools.quality.workspace import disposable_workspace
    from tools.quality.artifact_lifecycle import managed

    # The producer owns one long-lived run; its subprocess builders record their own runs.
    return managed("feature-snapshot", category="cache", days=30)(_build_run)(args, root, disposable_workspace)


def _build_run(args, root, disposable_workspace):
    if not args.local and not args.ssh_key.is_file():
        raise ValueError(f"SSH key does not exist: {args.ssh_key}")
    untracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "--others", "--exclude-standard", "-z"]).decode().strip("\0").split("\0")
    omitted = sorted(set(filter(None, untracked)) - set(args.include))
    if omitted:
        raise ValueError("Untracked files need explicit --include or removal: " + ", ".join(omitted))
    profile = {"platform": "linux/amd64", "mode": "feature"}
    task_root = Path(root.anchor) / "tmp/ragflow"
    task_root.mkdir(parents=True, exist_ok=True)
    with disposable_workspace(task_root / f"feature-{uuid.uuid4().hex}") as temporary:
        candidate = temporary / "snapshot"
        profile_file = temporary / "profile.json"
        profile_file.write_text(json.dumps(profile), encoding="utf-8")
        command = [sys.executable, str(root / "tools/quality/candidate.py"), "snapshot", "--candidate", str(candidate), "--root", str(root), "--allow-dirty", "--build-profile", str(profile_file)]
        for name in args.include:
            command.extend(["--include", name])
        run(*command)
        from tools.quality.artifact_lifecycle import note

        # Native compilation adds generated files to the working snapshot. Keep
        # its immutable input separately so normal teardown can retire that tree.
        archive = temporary / "snapshot.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            stream.add(candidate, arcname="snapshot")
        note(archive, category="archive")
        note(candidate, category="cache")
        if args.local:
            command = [sys.executable, "-B", str(root / "deployment/runner/feature_image.py"), "--snapshot", str(candidate), "--local", "--cache", str(root / ".local/cache/deploy")]
            if args.force:
                command.append("--force")
            run(*command)
            return 0
        ssh = ["ssh", "-i", str(args.ssh_key), "-o", "BatchMode=yes", args.host]
        scp = ["scp", "-i", str(args.ssh_key), "-o", "BatchMode=yes"]
        remote = run(*ssh, "mktemp -d /tmp/ragflow-feature-XXXXXXXX", capture=True).strip()
        if not remote.startswith("/tmp/ragflow-feature-") or not remote.rsplit("-", 1)[-1].isalnum():
            raise ValueError("Unexpected remote temporary directory")
        try:
            run(*scp, str(archive), f"{args.host}:{remote}/snapshot.tar.gz")
            run(
                *ssh,
                "tar -xzf "
                + remote
                + "/snapshot.tar.gz -C "
                + remote
                + ' && RAGFLOW_ARTIFACT_STATE="${RAGFLOW_ARTIFACT_STATE:-$HOME/.local/state/ragflow-artifacts}" python3 -B '
                + remote
                + "/snapshot/source/deployment/runner/feature_image.py --publish --snapshot "
                + remote
                + "/snapshot"
                + " --output "
                + remote
                + "/feature-result.json"
                + (" --force" if args.force else ""),
            )
        finally:
            run(*ssh, "sudo rm -rf -- " + remote)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Feature image build failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
