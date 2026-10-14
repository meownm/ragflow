"""Reuse qualified product bytes while collecting a new complete qualification."""

import argparse
import json
from pathlib import Path
import os

from tools.quality.release_evidence import bundle_identity, require_publishable, verify_files
from tools.quality.release_origin import acquire, api_items, verify_producer_packet, verify_source_inventories
from tools.quality.release_runtime import github
from tools.quality.release_evidence import verify_ci_origin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repository, revision = os.environ["GITHUB_REPOSITORY"], os.environ["SOURCE_SHA"]
    acquire(repository, args.run_id, args.attempt, args.artifact_id, f"qualification-{args.run_id}-{args.attempt}", revision, args.output)
    receipt = json.loads((args.output / "candidate-receipt.json").read_text())
    require_publishable(receipt)
    verify_files(args.output / "qualification", receipt["artifacts"]["files"])
    verify_source_inventories(receipt, args.output / "qualification")
    verify_producer_packet(receipt, args.output / "qualification")
    verify_ci_origin(
        receipt, github(f"repos/{repository}/actions/runs/{args.run_id}/attempts/{args.attempt}"), api_items(f"repos/{repository}/actions/runs/{args.run_id}/attempts/{args.attempt}/jobs", "jobs")
    )
    with open(os.environ["GITHUB_OUTPUT"], "a") as output:
        output.write("digest=" + receipt["image"]["staging_digest"] + "\n")
        output.write("bundle_id=" + bundle_identity(receipt) + "\n")


if __name__ == "__main__":
    main()
