"""A green build cannot replace complete HTTP handover evidence."""

import pytest

from tools.quality.release_evidence import verify_blue_green


@pytest.mark.parametrize(
    "mutation",
    [
        {},
        {"status": "FAIL"},
        {"cleanup": "FAIL"},
        {"source_sha": "f" * 40},
        {"image_digest": "registry/foreign@sha256:" + "f" * 64},
        {"requests": 0},
        {"failed_requests": 1},
        {"failed_requests": False},
        {"sse_drain": False},
        {"old_assets": False},
        {"unready_rejected": False},
        {"gateway_unchanged": False},
        {"post_switch_rollback": False},
        {"interrupted_recovery": False},
        {"controller_sha256": ""},
    ],
)
def test_release_requires_its_own_complete_transition(mutation):
    revision = "a" * 40
    image = "registry/staging@sha256:" + "b" * 64
    report = dict(
        schema=1,
        purpose="blue-green-http-transition",
        status="PASS",
        cleanup="PASS",
        source_sha=revision,
        image_digest=image,
        requests=20,
        failed_requests=0,
        sse_drain=True,
        old_assets=True,
        unready_rejected=True,
        gateway_unchanged=True,
        post_switch_rollback=True,
        interrupted_recovery=True,
        controller_sha256="c" * 64,
    )
    report.update(mutation)
    if mutation:
        with pytest.raises(ValueError, match="transition qualification"):
            verify_blue_green(report, revision, image)
    else:
        verify_blue_green(report, revision, image)
