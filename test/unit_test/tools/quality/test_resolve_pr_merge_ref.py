"""Behavioral contract for the trusted pull_request_target merge-ref resolver."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

from tools.quality import resolve_pr_merge_ref as resolver_module
from tools.quality.resolve_pr_merge_ref import (
    DEFAULT_BACKOFF_SECONDS,
    DEFAULT_DEADLINE_SECONDS,
    AttemptEvidence,
    EventIdentity,
    FailureReason,
    FetchedObject,
    GitTransport,
    RefChangedDuringFetch,
    ResolutionOutcome,
    ResolverState,
    TransportFailure,
    build_receipt,
    main,
    parse_commit_parents,
    parse_exact_remote_ref,
    resolve_pr_merge_ref,
    sanitized_git_environment,
    verify_receipt,
    write_receipt_atomic,
)

BASE_SHA = "1" * 40
HEAD_SHA = "2" * 40
MERGE_SHA = "3" * 40
OTHER_SHA = "4" * 40
NEWER_SHA = "5" * 40
MERGE_REF = "refs/pull/31/merge"
RESOLVER_SOURCE = Path(__file__).resolve().parents[4] / "tools/quality/resolve_pr_merge_ref.py"


def _identity(**overrides: str) -> EventIdentity:
    values = {
        "repository": "meownm/ragflow",
        "server_url": "https://github.com",
        "event_name": "pull_request_target",
        "event_action": "opened",
        "pr_number": "31",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "payload_merge_sha": "",
        "run_id": "34705977822",
        "run_attempt": "1",
    }
    values.update(overrides)
    return EventIdentity(**values)


def _commit(sha: str = MERGE_SHA, parents: tuple[str, ...] = (BASE_SHA, HEAD_SHA)) -> FetchedObject:
    return FetchedObject(sha=sha, object_type="commit", parents=parents)


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.origin = datetime(2026, 9, 13, tzinfo=UTC)

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        assert 0 < seconds <= DEFAULT_BACKOFF_SECONDS
        self.value += seconds

    def utc_now(self) -> datetime:
        return self.origin + timedelta(seconds=self.value)


class ScriptedTransport:
    def __init__(
        self,
        *,
        reads: list[str | None | Exception],
        fetches: list[FetchedObject | Exception],
        repeat_last_read: bool = False,
        repeat_last_fetch: bool = False,
    ) -> None:
        self.reads = deque(reads)
        self.fetches = deque(fetches)
        self.repeat_last_read = repeat_last_read
        self.repeat_last_fetch = repeat_last_fetch
        self.last_read: str | None | Exception = None
        self.last_fetch: FetchedObject | Exception | None = None
        self.calls: list[tuple[str, str, float]] = []

    def read_exact_ref(self, exact_ref: str, timeout_seconds: float) -> str | None:
        self.calls.append(("read", exact_ref, timeout_seconds))
        if self.reads:
            value = self.reads.popleft()
            self.last_read = value
        elif self.repeat_last_read:
            value = self.last_read
        else:
            raise AssertionError("unexpected remote read")
        if isinstance(value, Exception):
            raise value
        return value

    def fetch_exact_ref(self, exact_ref: str, timeout_seconds: float) -> FetchedObject:
        self.calls.append(("fetch", exact_ref, timeout_seconds))
        if self.fetches:
            value = self.fetches.popleft()
            self.last_fetch = value
        elif self.repeat_last_fetch:
            value = self.last_fetch
        else:
            raise AssertionError("unexpected fetch")
        if isinstance(value, Exception):
            raise value
        assert value is not None
        return value


def _resolve(
    transport: ScriptedTransport,
    *,
    identity: EventIdentity | None = None,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
) -> ResolutionOutcome:
    clock = FakeClock()
    return resolve_pr_merge_ref(
        identity or _identity(),
        transport,
        deadline_seconds=deadline_seconds,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )


@pytest.mark.parametrize("payload", ["", MERGE_SHA, OTHER_SHA])
def test_stable_current_is_the_only_immediate_success(payload: str) -> None:
    transport = ScriptedTransport(reads=[MERGE_SHA, MERGE_SHA], fetches=[_commit()])

    outcome = _resolve(transport, identity=_identity(payload_merge_sha=payload))

    assert outcome.accepted
    assert outcome.candidate_sha == MERGE_SHA
    assert [attempt.state for attempt in outcome.attempts] == [ResolverState.STABLE_CURRENT.value]
    assert [call[0] for call in transport.calls] == ["read", "fetch", "read"]
    assert all(call[1] == MERGE_REF for call in transport.calls)


def test_absent_then_stable_current_retries_with_monotonic_backoff() -> None:
    transport = ScriptedTransport(reads=[None, MERGE_SHA, MERGE_SHA], fetches=[_commit()])

    outcome = _resolve(transport)

    assert outcome.accepted
    assert [attempt.state for attempt in outcome.attempts] == [
        ResolverState.ABSENT.value,
        ResolverState.STABLE_CURRENT.value,
    ]
    assert outcome.elapsed_seconds == DEFAULT_BACKOFF_SECONDS


@pytest.mark.parametrize("stale_attempts", [1, 3])
def test_stale_parent_identity_keeps_polling_until_current(stale_attempts: int) -> None:
    reads: list[str | None | Exception] = []
    fetches: list[FetchedObject | Exception] = []
    for _ in range(stale_attempts):
        reads.extend([OTHER_SHA, OTHER_SHA])
        fetches.append(_commit(OTHER_SHA, (BASE_SHA, "6" * 40)))
    reads.extend([MERGE_SHA, MERGE_SHA])
    fetches.append(_commit())
    transport = ScriptedTransport(reads=reads, fetches=fetches)

    outcome = _resolve(transport)

    assert outcome.accepted
    assert [attempt.state for attempt in outcome.attempts] == [ResolverState.STALE.value] * stale_attempts + [ResolverState.STABLE_CURRENT.value]


@pytest.mark.parametrize(
    ("reads", "fetches"),
    [
        ([OTHER_SHA, NEWER_SHA, MERGE_SHA, MERGE_SHA], [_commit(NEWER_SHA), _commit()]),
        ([OTHER_SHA, NEWER_SHA, MERGE_SHA, MERGE_SHA], [_commit(OTHER_SHA), _commit()]),
        ([OTHER_SHA, MERGE_SHA, MERGE_SHA], [RefChangedDuringFetch(), _commit()]),
    ],
    ids=["changed-during-fetch", "changed-before-second-read", "ref-disappeared-during-fetch"],
)
def test_changing_ref_is_discarded_before_a_stable_retry(reads: list[str | None | Exception], fetches: list[FetchedObject | Exception]) -> None:
    transport = ScriptedTransport(reads=reads, fetches=fetches)

    outcome = _resolve(transport)

    assert outcome.accepted
    assert [attempt.state for attempt in outcome.attempts] == [
        ResolverState.CHANGING.value,
        ResolverState.STABLE_CURRENT.value,
    ]


def test_synchronize_identity_cannot_reuse_an_older_receipt(tmp_path: Path) -> None:
    first_identity = _identity(event_action="opened", head_sha=HEAD_SHA, run_id="1")
    second_identity = _identity(event_action="synchronize", head_sha=NEWER_SHA, run_id="2")
    first_outcome = _resolve(
        ScriptedTransport(reads=[MERGE_SHA, MERGE_SHA], fetches=[_commit()]),
        identity=first_identity,
    )
    receipt = build_receipt(
        first_identity,
        first_outcome,
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="verified",
    )
    path = tmp_path / "identity-receipt.json"
    file_digest = write_receipt_atomic(path, receipt)

    with pytest.raises(ValueError, match="identity mismatch"):
        verify_receipt(
            path,
            identity=second_identity,
            candidate_sha=MERGE_SHA,
            resolver_source_sha256="a" * 64,
            receipt_payload_sha256=receipt["receipt_payload_sha256"],
            receipt_file_sha256=file_digest,
        )


def test_absent_until_deadline_is_incomplete_without_real_sleep() -> None:
    transport = ScriptedTransport(reads=[None], fetches=[], repeat_last_read=True)

    outcome = _resolve(transport, deadline_seconds=6.0)

    assert not outcome.accepted
    assert outcome.state is ResolverState.ABSENT
    assert outcome.reason is FailureReason.TIMEOUT
    assert outcome.elapsed_seconds == 6.0
    assert len(outcome.attempts) == 3


@pytest.mark.parametrize(
    ("deadline_seconds", "backoff_seconds"),
    [(0.0, 2.0), (-1.0, 2.0), (float("nan"), 2.0), (float("inf"), 2.0), (120.0, 0.0), (120.0, float("nan"))],
)
def test_non_finite_or_non_positive_polling_controls_are_rejected(deadline_seconds: float, backoff_seconds: float) -> None:
    transport = ScriptedTransport(reads=[], fetches=[])
    clock = FakeClock()

    outcome = resolve_pr_merge_ref(
        _identity(),
        transport,
        deadline_seconds=deadline_seconds,
        backoff_seconds=backoff_seconds,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )

    assert not outcome.accepted
    assert outcome.reason is FailureReason.MALFORMED
    assert not transport.calls


def test_pre_attempt_setup_time_counts_toward_the_global_deadline() -> None:
    transport = ScriptedTransport(reads=[MERGE_SHA], fetches=[_commit()])
    clock = FakeClock()
    clock.value = DEFAULT_DEADLINE_SECONDS

    outcome = resolve_pr_merge_ref(
        _identity(),
        transport,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
        started_at=0.0,
    )

    assert not outcome.accepted
    assert outcome.state is ResolverState.ABSENT
    assert outcome.reason is FailureReason.TIMEOUT
    assert outcome.elapsed_seconds == DEFAULT_DEADLINE_SECONDS
    assert not outcome.attempts
    assert not transport.calls


@pytest.mark.parametrize("overrun_operation", ["read", "fetch"])
def test_one_attempt_cannot_overrun_the_global_deadline(overrun_operation: str) -> None:
    clock = FakeClock()

    class SlowTransport:
        def read_exact_ref(self, _exact_ref: str, _timeout_seconds: float) -> str:
            if overrun_operation == "read" or clock.value > 0:
                clock.value = DEFAULT_DEADLINE_SECONDS
            return MERGE_SHA

        def fetch_exact_ref(self, _exact_ref: str, _timeout_seconds: float) -> FetchedObject:
            if overrun_operation == "fetch":
                clock.value = DEFAULT_DEADLINE_SECONDS
            return _commit()

    outcome = resolve_pr_merge_ref(
        _identity(),
        SlowTransport(),
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )

    assert not outcome.accepted
    assert outcome.state is ResolverState.UNAVAILABLE
    assert outcome.reason is FailureReason.TIMEOUT


@pytest.mark.parametrize(
    ("fetched", "expected_reason"),
    [
        (_commit(OTHER_SHA, (BASE_SHA, OTHER_SHA)), FailureReason.PARENT_MISMATCH),
        (_commit(OTHER_SHA, (OTHER_SHA, HEAD_SHA)), FailureReason.PARENT_MISMATCH),
    ],
)
def test_persistent_stale_event_never_releases_a_candidate(fetched: FetchedObject, expected_reason: FailureReason) -> None:
    transport = ScriptedTransport(
        reads=[fetched.sha],
        fetches=[fetched],
        repeat_last_read=True,
        repeat_last_fetch=True,
    )

    outcome = _resolve(transport, deadline_seconds=4.0)

    assert not outcome.accepted
    assert outcome.reason is expected_reason
    assert {attempt.state for attempt in outcome.attempts} == {ResolverState.STALE.value}


def test_stale_merge_ref_cannot_prove_event_supersession() -> None:
    stale = _commit(NEWER_SHA, (BASE_SHA, OTHER_SHA))
    transport = ScriptedTransport(
        reads=[stale.sha, stale.sha, MERGE_SHA, MERGE_SHA],
        fetches=[stale, _commit()],
    )

    outcome = _resolve(transport)

    assert outcome.accepted
    assert outcome.candidate_sha == MERGE_SHA
    assert [attempt.state for attempt in outcome.attempts] == [
        ResolverState.STALE.value,
        ResolverState.STABLE_CURRENT.value,
    ]
    assert all(call[1] == MERGE_REF for call in transport.calls)


def test_continuously_changing_ref_times_out_as_ref_changed() -> None:
    transport = ScriptedTransport(
        reads=[OTHER_SHA, NEWER_SHA],
        fetches=[_commit(OTHER_SHA)],
        repeat_last_read=True,
        repeat_last_fetch=True,
    )

    outcome = _resolve(transport, deadline_seconds=4.0)

    assert not outcome.accepted
    assert outcome.reason is FailureReason.REF_CHANGED
    assert {attempt.state for attempt in outcome.attempts} == {ResolverState.CHANGING.value}


@pytest.mark.parametrize("payload", ["not-a-sha", "0" * 40, "A" * 40])
def test_malformed_payload_fails_before_transport(payload: str) -> None:
    transport = ScriptedTransport(reads=[], fetches=[])

    outcome = _resolve(transport, identity=_identity(payload_merge_sha=payload))

    assert not outcome.accepted
    assert outcome.reason is FailureReason.MALFORMED
    assert not transport.calls


def test_stable_ref_with_stale_payload_is_accepted_and_recorded_as_advisory(tmp_path: Path) -> None:
    identity = _identity(event_action="synchronize", payload_merge_sha=OTHER_SHA)
    transport = ScriptedTransport(reads=[MERGE_SHA, MERGE_SHA], fetches=[_commit()])

    outcome = _resolve(transport, identity=identity)

    assert outcome.accepted
    receipt = build_receipt(
        identity,
        outcome,
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="verified",
    )
    assert receipt["payload_observation"] == {
        "authority": "advisory",
        "relation_to_candidate": "DIFFERENT",
    }
    path = tmp_path / "identity-receipt.json"
    file_digest = write_receipt_atomic(path, receipt)
    verify_receipt(
        path,
        identity=identity,
        candidate_sha=MERGE_SHA,
        resolver_source_sha256="a" * 64,
        receipt_payload_sha256=receipt["receipt_payload_sha256"],
        receipt_file_sha256=file_digest,
    )


@pytest.mark.parametrize(
    "remote_output",
    [
        "",
        f"{MERGE_SHA}\t{MERGE_REF}\n{OTHER_SHA}\t{MERGE_REF}\n",
        f"not-a-sha\t{MERGE_REF}\n",
        f"{'0' * 40}\t{MERGE_REF}\n",
        f"{MERGE_SHA}\trefs/pull/30/merge\n",
        f"{MERGE_SHA} {MERGE_REF}\n",
    ],
)
def test_remote_output_must_be_one_exact_sha_tab_ref_row(remote_output: str) -> None:
    with pytest.raises(TransportFailure) as caught:
        parse_exact_remote_ref(remote_output, MERGE_REF)

    assert caught.value.state is ResolverState.MALFORMED
    assert caught.value.reason is FailureReason.MALFORMED


def test_exact_remote_row_is_accepted() -> None:
    assert parse_exact_remote_ref(f"{MERGE_SHA}\t{MERGE_REF}\n", MERGE_REF) == MERGE_SHA


def test_commit_parents_are_read_from_raw_object_headers() -> None:
    commit_text = f"tree {'6' * 40}\nparent {BASE_SHA}\nparent {HEAD_SHA}\nauthor Test <test@example.invalid> 0 +0000\n\nmerge\n"

    assert parse_commit_parents(commit_text) == (BASE_SHA, HEAD_SHA)


@pytest.mark.parametrize(
    "fetched",
    [
        FetchedObject(MERGE_SHA, "tag", (BASE_SHA, HEAD_SHA)),
        _commit(MERGE_SHA, (BASE_SHA,)),
        _commit(MERGE_SHA, (BASE_SHA, HEAD_SHA, OTHER_SHA)),
        _commit(MERGE_SHA, (HEAD_SHA, BASE_SHA)),
        _commit(MERGE_SHA, (OTHER_SHA, HEAD_SHA)),
    ],
    ids=["tag", "one-parent", "three-parents", "swapped-parents", "wrong-parent"],
)
def test_non_commit_or_wrong_parent_objects_never_release_candidate(fetched: FetchedObject) -> None:
    transport = ScriptedTransport(
        reads=[MERGE_SHA],
        fetches=[fetched],
        repeat_last_read=True,
        repeat_last_fetch=True,
    )

    outcome = _resolve(transport, deadline_seconds=4.0)

    assert not outcome.accepted
    if fetched.object_type != "commit":
        assert outcome.reason is FailureReason.MALFORMED
    else:
        assert outcome.reason is FailureReason.PARENT_MISMATCH


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (TransportFailure(ResolverState.UNAVAILABLE, FailureReason.NETWORK_UNAVAILABLE), FailureReason.NETWORK_UNAVAILABLE),
        (TransportFailure(ResolverState.UNAVAILABLE, FailureReason.AUTH_UNSUPPORTED), FailureReason.AUTH_UNSUPPORTED),
        (TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT), FailureReason.TIMEOUT),
    ],
)
def test_network_auth_and_command_timeouts_fail_closed(error: TransportFailure, reason: FailureReason) -> None:
    transport = ScriptedTransport(reads=[error], fetches=[])

    outcome = _resolve(transport)

    assert not outcome.accepted
    assert outcome.state is ResolverState.UNAVAILABLE
    assert outcome.reason is reason


@pytest.mark.parametrize(
    ("stderr", "error_type", "reason"),
    [
        ("fatal: unable to access: Could not resolve host: github.com", TransportFailure, FailureReason.NETWORK_UNAVAILABLE),
        ("fatal: unable to access: SSL certificate problem", TransportFailure, FailureReason.NETWORK_UNAVAILABLE),
        ("fatal: Authentication failed", TransportFailure, FailureReason.AUTH_UNSUPPORTED),
        ("fatal: could not read Username for 'https://github.com'", TransportFailure, FailureReason.AUTH_UNSUPPORTED),
        ("remote: Repository not found", TransportFailure, FailureReason.AUTH_UNSUPPORTED),
        ("fatal: couldn't find remote ref refs/pull/31/merge", RefChangedDuringFetch, FailureReason.REF_CHANGED),
    ],
    ids=["dns", "tls", "authentication", "credential-prompt", "private-or-missing", "ref-disappeared"],
)
def test_git_failure_classification_is_fail_closed(stderr: str, error_type: type[TransportFailure], reason: FailureReason) -> None:
    completed = subprocess.CompletedProcess(["git", "fetch"], 128, "", stderr)

    error = GitTransport._classify_failure(completed)

    assert type(error) is error_type
    assert error.reason is reason


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        ("event_name", "pull_request", FailureReason.INVALID_EVENT),
        ("event_action", "closed", FailureReason.INVALID_EVENT),
        ("pr_number", "0", FailureReason.INVALID_EVENT),
        ("pr_number", "31/merge", FailureReason.INVALID_EVENT),
        ("repository", "https://attacker.invalid/repo", FailureReason.INVALID_EVENT),
        ("server_url", "http://github.com", FailureReason.AUTH_UNSUPPORTED),
        ("server_url", "https://github.example", FailureReason.AUTH_UNSUPPORTED),
        ("base_sha", "0" * 40, FailureReason.INVALID_EVENT),
        ("head_sha", "short", FailureReason.INVALID_EVENT),
        ("run_id", "0", FailureReason.INVALID_EVENT),
        ("run_attempt", "latest", FailureReason.INVALID_EVENT),
    ],
)
def test_untrusted_identity_inputs_fail_before_network(field: str, value: str, reason: FailureReason) -> None:
    transport = ScriptedTransport(reads=[], fetches=[])

    outcome = _resolve(transport, identity=_identity(**{field: value}))

    assert not outcome.accepted
    assert outcome.reason is reason
    assert not transport.calls


def test_git_environment_drops_candidate_injection_surfaces(tmp_path: Path) -> None:
    hostile = {
        "PATH": "trusted-path",
        "LANG": "C.UTF-8",
        "HOME": str(tmp_path / "attacker-home"),
        "GIT_DIR": str(tmp_path / "attacker-git"),
        "GIT_WORK_TREE": str(tmp_path / "attacker-tree"),
        "GIT_OBJECT_DIRECTORY": str(tmp_path / "objects"),
        "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(tmp_path / "alternates"),
        "GIT_CONFIG_SYSTEM": str(tmp_path / "system-config"),
        "GIT_CONFIG_GLOBAL": str(tmp_path / "global-config"),
        "GIT_CONFIG_COUNT": "2",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "attacker-helper",
        "GIT_ASKPASS": "attacker-askpass",
        "SSH_ASKPASS": "attacker-ssh-askpass",
        "HTTP_PROXY": "http://attacker.invalid",
        "HTTPS_PROXY": "http://attacker.invalid",
        "SSL_CERT_FILE": str(tmp_path / "attacker.pem"),
    }

    environment = sanitized_git_environment(hostile)

    assert environment == {
        "PATH": "trusted-path",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_COUNT": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
    }
    assert not ({"HOME", "GIT_DIR", "GIT_WORK_TREE", "GIT_ASKPASS", "SSH_ASKPASS", "HTTPS_PROXY"} & environment.keys())


def test_git_commands_pin_hooks_credentials_transports_and_object_checks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tools.quality.resolve_pr_merge_ref.shutil.which", lambda *_args, **_kwargs: "/usr/bin/git")
    transport = GitTransport(tmp_path / "resolver", "https://github.com/meownm/ragflow.git", source_environment={"PATH": "/bin"})

    command = transport._base_command()

    rendered = " ".join(command)
    for required in (
        "--no-replace-objects",
        "credential.helper=",
        "core.askPass=",
        "core.hooksPath=",
        "protocol.file.allow=never",
        "protocol.ext.allow=never",
        "submodule.recurse=false",
        "fetch.fsckObjects=true",
        "transfer.fsckObjects=true",
        "http.sslVerify=true",
    ):
        assert required in rendered


def _load_mutant(tmp_path: Path, mutation: str, source: str) -> ModuleType:
    path = tmp_path / f"resolver-mutant-{mutation.replace(' ', '-')}.py"
    path.write_text(source, encoding="utf-8", newline="\n")
    module_name = f"resolver_mutant_{mutation.replace(' ', '_')}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _mutant_identity(module: ModuleType, **overrides: str):
    values = {
        "repository": "meownm/ragflow",
        "server_url": "https://github.com",
        "event_name": "pull_request_target",
        "event_action": "opened",
        "pr_number": "31",
        "base_sha": BASE_SHA,
        "head_sha": HEAD_SHA,
        "payload_merge_sha": "",
        "run_id": "34705977822",
        "run_attempt": "1",
    }
    values.update(overrides)
    return module.EventIdentity(**values)


def _assert_mutation_contract(module: ModuleType, mutation: str) -> None:
    if mutation == "sanitized environment":
        environment = module.sanitized_git_environment({"PATH": "/bin", "HOME": "/attacker"})
        assert "HOME" not in environment
        return

    clock = FakeClock()
    identity = _mutant_identity(module)
    deadline_seconds = 4.0
    if mutation == "stable double read":
        transport = ScriptedTransport(
            reads=[MERGE_SHA, OTHER_SHA],
            fetches=[module.FetchedObject(MERGE_SHA, "commit", (BASE_SHA, HEAD_SHA))],
            repeat_last_read=True,
            repeat_last_fetch=True,
        )
    elif mutation == "fetch binding":
        transport = ScriptedTransport(
            reads=[MERGE_SHA, MERGE_SHA],
            fetches=[module.FetchedObject(OTHER_SHA, "commit", (BASE_SHA, HEAD_SHA))],
            repeat_last_read=True,
            repeat_last_fetch=True,
        )
    elif mutation == "parent binding":
        transport = ScriptedTransport(
            reads=[MERGE_SHA, MERGE_SHA],
            fetches=[module.FetchedObject(MERGE_SHA, "commit", (HEAD_SHA, BASE_SHA))],
            repeat_last_read=True,
            repeat_last_fetch=True,
        )
    else:
        assert mutation == "monotonic deadline"
        deadline_seconds = 2.0
        transport = ScriptedTransport(reads=[None], fetches=[], repeat_last_read=True)

    outcome = module.resolve_pr_merge_ref(
        identity,
        transport,
        deadline_seconds=deadline_seconds,
        backoff_seconds=2.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        utc_now=clock.utc_now,
    )
    assert not outcome.accepted
    assert outcome.candidate_sha is None
    assert outcome.elapsed_seconds <= deadline_seconds


@pytest.mark.parametrize(
    ("mutation", "original", "replacement"),
    [
        (
            "stable double read",
            "r2 = transport.read_exact_ref(identity.merge_ref, remaining)",
            "r2 = r1",
        ),
        ("fetch binding", "r1 != fetched.sha or fetched.sha != r2", "r1 != r2"),
        ("parent binding", "fetched.parents != expected_parents", "False"),
        (
            "sanitized environment",
            'for key in ("PATH", "SYSTEMROOT", "WINDIR")',
            'for key in ("PATH", "SYSTEMROOT", "WINDIR", "HOME")',
        ),
        (
            "monotonic deadline",
            "deadline = started + deadline_seconds",
            "deadline = started + deadline_seconds * 2",
        ),
    ],
)
def test_resolver_source_mutations_are_rejected(
    tmp_path: Path,
    mutation: str,
    original: str,
    replacement: str,
) -> None:
    source = RESOLVER_SOURCE.read_text(encoding="utf-8")
    assert source.count(original) == 1
    _assert_mutation_contract(resolver_module, mutation)
    mutant = _load_mutant(tmp_path, mutation, source.replace(original, replacement, 1))
    try:
        with pytest.raises(AssertionError):
            _assert_mutation_contract(mutant, mutation)
    finally:
        sys.modules.pop(mutant.__name__, None)


def _accepted_outcome() -> ResolutionOutcome:
    return ResolutionOutcome(
        state=ResolverState.STABLE_CURRENT,
        reason=None,
        candidate_sha=MERGE_SHA,
        attempts=(
            AttemptEvidence(
                number=1,
                timestamp="2026-09-13T00:00:00Z",
                elapsed_seconds=0.5,
                state=ResolverState.STABLE_CURRENT.value,
                r1=MERGE_SHA,
                fetched=MERGE_SHA,
                r2=MERGE_SHA,
                object_type="commit",
                parents=(BASE_SHA, HEAD_SHA),
            ),
        ),
        elapsed_seconds=0.5,
    )


def _write_valid_receipt(tmp_path: Path) -> tuple[Path, str, dict[str, object]]:
    receipt = build_receipt(
        _identity(),
        _accepted_outcome(),
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="verified",
    )
    path = tmp_path / "identity-receipt.json"
    file_digest = write_receipt_atomic(path, receipt)
    return path, file_digest, receipt


def test_receipt_binds_identity_attempts_source_and_checkout(tmp_path: Path) -> None:
    path, file_digest, receipt = _write_valid_receipt(tmp_path)

    verify_receipt(
        path,
        identity=_identity(),
        candidate_sha=MERGE_SHA,
        resolver_source_sha256="a" * 64,
        receipt_payload_sha256=receipt["receipt_payload_sha256"],
        receipt_file_sha256=file_digest,
    )

    assert receipt["timing"] == {
        "deadline_seconds": DEFAULT_DEADLINE_SECONDS,
        "backoff": {"kind": "fixed", "seconds": DEFAULT_BACKOFF_SECONDS, "max_seconds": DEFAULT_BACKOFF_SECONDS},
        "attempt_count": 1,
        "elapsed_seconds": 0.5,
    }
    assert receipt["checkout"] == {"plan": "verified", "downstream": "exact_sha_refetch_fail_closed"}
    assert receipt["payload_observation"] == {
        "authority": "advisory",
        "relation_to_candidate": "ABSENT",
    }


def test_receipt_distinguishes_contract_failure_from_incomplete_availability() -> None:
    malformed = ResolutionOutcome(ResolverState.MALFORMED, FailureReason.MALFORMED, None, (), 0.0)
    unavailable = ResolutionOutcome(ResolverState.ABSENT, FailureReason.TIMEOUT, None, (), 120.0)

    failure_receipt = build_receipt(
        _identity(),
        malformed,
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="not_attempted",
    )
    incomplete_receipt = build_receipt(
        _identity(),
        unavailable,
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="not_attempted",
    )

    assert failure_receipt["result"]["status"] == "failure"
    assert incomplete_receipt["result"]["status"] == "incomplete"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda receipt: receipt["identity"].__setitem__("head_sha", NEWER_SHA),
        lambda receipt: receipt["result"].__setitem__("accepted_sha", OTHER_SHA),
        lambda receipt: receipt["attempts"][-1].__setitem__("r2", OTHER_SHA),
        lambda receipt: receipt["attempts"][-1].__setitem__("parents", [HEAD_SHA, BASE_SHA]),
        lambda receipt: receipt["checkout"].__setitem__("plan", "not_attempted"),
        lambda receipt: receipt["payload_observation"].__setitem__("authority", "trusted"),
        lambda receipt: receipt["payload_observation"].__setitem__("relation_to_candidate", "MATCH"),
        lambda receipt: receipt.__setitem__("resolver_source_sha256", "b" * 64),
    ],
    ids=[
        "identity",
        "candidate",
        "double-read",
        "parents",
        "checkout",
        "payload-authority",
        "payload-relation",
        "source",
    ],
)
def test_analysis_and_final_receipt_mutations_are_rejected(tmp_path: Path, mutation) -> None:
    _path, _file_digest, receipt = _write_valid_receipt(tmp_path)
    mutated = copy.deepcopy(receipt)
    mutation(mutated)
    mutated.pop("receipt_payload_sha256")
    canonical = (json.dumps(mutated, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    mutated["receipt_payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    mutated_digest = write_receipt_atomic(tmp_path / "mutated.json", mutated)

    with pytest.raises(ValueError):
        verify_receipt(
            tmp_path / "mutated.json",
            identity=_identity(),
            candidate_sha=MERGE_SHA,
            resolver_source_sha256="a" * 64,
            receipt_payload_sha256=mutated["receipt_payload_sha256"],
            receipt_file_sha256=mutated_digest,
        )


def test_existing_receipt_is_replaced_atomically_without_temporary_files(tmp_path: Path) -> None:
    path = tmp_path / "identity-receipt.json"
    path.write_text("stale", encoding="utf-8")
    receipt = build_receipt(
        _identity(),
        _accepted_outcome(),
        resolver_source_sha256="a" * 64,
        deadline_seconds=DEFAULT_DEADLINE_SECONDS,
        backoff_seconds=DEFAULT_BACKOFF_SECONDS,
        checkout_status="verified",
    )

    digest = write_receipt_atomic(path, receipt)

    assert digest
    assert not list(tmp_path.glob(".*.tmp"))
    assert path.read_text(encoding="utf-8").startswith("{")


def _git(repository: Path, *arguments: str) -> str:
    git = shutil.which("git")
    assert git is not None, "Git is required by the resolver contract"
    completed = subprocess.run(
        [git, "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


def _local_merge_remote(tmp_path: Path) -> tuple[Path, Path, EventIdentity, str]:
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "--quiet", "--initial-branch=main")
    _git(source, "config", "user.name", "resolver-test")
    _git(source, "config", "user.email", "resolver-test@example.invalid")
    _git(source, "config", "core.autocrlf", "false")
    (source / "base.txt").write_text("base\n", encoding="utf-8", newline="\n")
    _git(source, "add", "base.txt")
    _git(source, "commit", "--quiet", "-m", "base")
    base_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "--quiet", "-b", "candidate")
    (source / "candidate.txt").write_text("candidate\n", encoding="utf-8", newline="\n")
    _git(source, "add", "candidate.txt")
    _git(source, "commit", "--quiet", "-m", "candidate")
    head_sha = _git(source, "rev-parse", "HEAD")
    _git(source, "checkout", "--quiet", "main")
    _git(source, "merge", "--quiet", "--no-ff", "--no-edit", "candidate")
    merge_sha = _git(source, "rev-parse", "HEAD")
    assert _git(source, "show", "-s", "--format=%P", merge_sha) == f"{base_sha} {head_sha}"

    remote = tmp_path / "remote.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(remote))
    _git(source, "remote", "add", "test-origin", str(remote))
    _git(
        source,
        "push",
        "--quiet",
        "test-origin",
        f"{base_sha}:refs/heads/main",
        f"{head_sha}:refs/pull/31/head",
        f"{merge_sha}:{MERGE_REF}",
    )
    _git(remote, "symbolic-ref", "HEAD", "refs/heads/main")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "--quiet", str(remote), str(checkout))
    identity = _identity(base_sha=base_sha, head_sha=head_sha, payload_merge_sha=merge_sha)
    return remote, checkout, identity, merge_sha


def test_real_git_transport_fetches_and_materializes_the_verified_object(tmp_path: Path) -> None:
    remote, checkout, identity, merge_sha = _local_merge_remote(tmp_path)
    shallow_path = Path(_git(checkout, "rev-parse", "--path-format=absolute", "--git-path", "shallow"))
    shallow_before = shallow_path.read_bytes() if shallow_path.exists() else None
    transport = GitTransport.initialize(
        tmp_path / "resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )

    outcome = resolve_pr_merge_ref(identity, transport, deadline_seconds=60.0, backoff_seconds=0.01)
    assert outcome.accepted
    assert outcome.candidate_sha == merge_sha
    assert _git(transport.repository_dir, "rev-parse", "--show-object-format") == "sha1"

    transport.materialize_checkout(
        checkout,
        merge_sha,
        (identity.base_sha, identity.head_sha),
        timeout_seconds=60.0,
    )

    assert _git(checkout, "rev-parse", "HEAD") == merge_sha
    assert _git(checkout, "show", "-s", "--format=%P", "HEAD") == f"{identity.base_sha} {identity.head_sha}"
    assert all(_git(checkout, "cat-file", "-t", parent) == "commit" for parent in (identity.base_sha, identity.head_sha))
    assert (shallow_path.read_bytes() if shallow_path.exists() else None) == shallow_before


def test_materialize_checkout_accepts_a_linked_git_worktree(tmp_path: Path) -> None:
    remote, checkout, identity, merge_sha = _local_merge_remote(tmp_path)
    linked_checkout = tmp_path / "linked-checkout"
    _git(checkout, "worktree", "add", "--quiet", "--detach", str(linked_checkout), identity.base_sha)
    assert (linked_checkout / ".git").is_file()
    transport = GitTransport.initialize(
        tmp_path / "resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )
    outcome = resolve_pr_merge_ref(identity, transport, deadline_seconds=60.0, backoff_seconds=0.01)
    assert outcome.accepted

    transport.materialize_checkout(
        linked_checkout,
        merge_sha,
        (identity.base_sha, identity.head_sha),
        timeout_seconds=60.0,
    )

    assert _git(linked_checkout, "rev-parse", "HEAD") == merge_sha


def test_existing_resolver_repository_is_rejected_before_git_runs(tmp_path: Path) -> None:
    repository = tmp_path / "already-present"
    repository.mkdir()

    with pytest.raises(TransportFailure) as caught:
        GitTransport.initialize(repository, "https://github.com/meownm/ragflow.git")

    assert caught.value.reason is FailureReason.CHECKOUT_UNAVAILABLE


@pytest.mark.parametrize(
    "relative_path",
    [
        ".git/objects/info/alternates",
        ".git/objects/info/http-alternates",
        ".git/info/grafts",
        f".git/refs/replace/{MERGE_SHA}",
    ],
    ids=["alternates", "http-alternates", "grafts", "replace-ref"],
)
def test_object_database_indirection_files_are_rejected(tmp_path: Path, relative_path: str) -> None:
    remote, _checkout, identity, _merge_sha = _local_merge_remote(tmp_path)
    transport = GitTransport.initialize(
        tmp_path / "resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )
    indirection = transport.repository_dir / relative_path
    indirection.parent.mkdir(parents=True, exist_ok=True)
    indirection.write_text(str(tmp_path / "attacker-objects"), encoding="utf-8")

    outcome = resolve_pr_merge_ref(identity, transport, deadline_seconds=10.0, backoff_seconds=0.01)

    assert not outcome.accepted
    assert outcome.state is ResolverState.MALFORMED
    assert outcome.reason is FailureReason.MALFORMED


def test_verified_object_missing_from_checkout_source_fails_closed(tmp_path: Path) -> None:
    remote, checkout, identity, _merge_sha = _local_merge_remote(tmp_path)
    transport = GitTransport.initialize(
        tmp_path / "resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )

    with pytest.raises(TransportFailure) as caught:
        transport.materialize_checkout(
            checkout,
            OTHER_SHA,
            (identity.base_sha, identity.head_sha),
            timeout_seconds=10.0,
        )

    assert caught.value.reason is FailureReason.CHECKOUT_UNAVAILABLE


def test_checkout_reporting_another_head_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote, checkout, identity, merge_sha = _local_merge_remote(tmp_path)
    transport = GitTransport.initialize(
        tmp_path / "resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )
    outcome = resolve_pr_merge_ref(identity, transport, deadline_seconds=60.0, backoff_seconds=0.01)
    assert outcome.accepted

    original_run = GitTransport._run
    checkout_repository = checkout.resolve()
    head_reads = 0

    def report_another_head(
        self,
        arguments,
        *,
        timeout_seconds,
        repository_required=True,
        allow_file=False,
        check=True,
    ):
        nonlocal head_reads
        if self.repository_dir == checkout_repository and arguments == ["rev-parse", "HEAD"]:
            head_reads += 1
            if head_reads == 2:
                return subprocess.CompletedProcess(arguments, 0, f"{OTHER_SHA}\n", "")
        return original_run(
            self,
            arguments,
            timeout_seconds=timeout_seconds,
            repository_required=repository_required,
            allow_file=allow_file,
            check=check,
        )

    monkeypatch.setattr(GitTransport, "_run", report_another_head)

    with pytest.raises(TransportFailure) as caught:
        transport.materialize_checkout(
            checkout,
            merge_sha,
            (identity.base_sha, identity.head_sha),
            timeout_seconds=60.0,
        )

    assert head_reads == 2
    assert caught.value.reason is FailureReason.CHECKOUT_UNAVAILABLE


def _cli_identity_arguments(identity: EventIdentity) -> list[str]:
    return [
        "--repository",
        identity.repository,
        "--server-url",
        identity.server_url,
        "--event-name",
        identity.event_name,
        "--event-action",
        identity.event_action,
        "--pr-number",
        identity.pr_number,
        "--base-sha",
        identity.base_sha,
        "--head-sha",
        identity.head_sha,
        "--payload-merge-sha",
        identity.payload_merge_sha,
        "--run-id",
        identity.run_id,
        "--run-attempt",
        identity.run_attempt,
    ]


def test_cli_publishes_only_a_verified_checkout_and_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote, checkout, identity, merge_sha = _local_merge_remote(tmp_path)
    transport = GitTransport.initialize(
        tmp_path / "real-resolver-repository",
        remote.as_uri(),
        allow_file_remote=True,
    )
    monkeypatch.setattr(GitTransport, "initialize", lambda *_args, **_kwargs: transport)
    receipt = tmp_path / "identity-receipt.json"
    github_output = tmp_path / "github-output.txt"

    exit_code = main(
        [
            "resolve",
            *_cli_identity_arguments(identity),
            "--repository-dir",
            str(tmp_path / "unused-cli-repository"),
            "--checkout-repository",
            str(checkout),
            "--receipt",
            str(receipt),
            "--github-output",
            str(github_output),
            "--deadline-seconds",
            "60",
            "--backoff-seconds",
            "0.01",
        ]
    )

    assert exit_code == 0
    outputs = dict(line.split("=", 1) for line in github_output.read_text(encoding="utf-8").splitlines())
    assert outputs["sha"] == merge_sha
    assert _git(checkout, "rev-parse", "HEAD") == merge_sha
    assert (
        main(
            [
                "verify-receipt",
                *_cli_identity_arguments(identity),
                "--receipt",
                str(receipt),
                "--candidate-sha",
                merge_sha,
                "--resolver-sha256",
                outputs["resolver_sha256"],
                "--receipt-sha256",
                outputs["receipt_sha256"],
                "--receipt-file-sha256",
                outputs["receipt_file_sha256"],
            ]
        )
        == 0
    )

    malformed_receipt = json.loads(receipt.read_text(encoding="utf-8"))
    malformed_receipt["attempts"][-1] = "malformed"
    malformed_receipt.pop("receipt_payload_sha256")
    canonical = (json.dumps(malformed_receipt, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    malformed_receipt["receipt_payload_sha256"] = hashlib.sha256(canonical).hexdigest()
    malformed_path = tmp_path / "malformed-receipt.json"
    malformed_file_digest = write_receipt_atomic(malformed_path, malformed_receipt)

    assert (
        main(
            [
                "verify-receipt",
                *_cli_identity_arguments(identity),
                "--receipt",
                str(malformed_path),
                "--candidate-sha",
                merge_sha,
                "--resolver-sha256",
                outputs["resolver_sha256"],
                "--receipt-sha256",
                malformed_receipt["receipt_payload_sha256"],
                "--receipt-file-sha256",
                malformed_file_digest,
            ]
        )
        == 2
    )


def test_cli_malformed_identity_writes_failure_receipt_without_outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    identity = _identity(payload_merge_sha="not-a-sha")
    repository_dir = tmp_path / "resolver-repository"
    receipt = tmp_path / "identity-receipt.json"
    github_output = tmp_path / "github-output.txt"
    monkeypatch.setattr(
        GitTransport,
        "initialize",
        lambda *_args, **_kwargs: pytest.fail("invalid identity must fail before Git initialization"),
    )

    exit_code = main(
        [
            "resolve",
            *_cli_identity_arguments(identity),
            "--repository-dir",
            str(repository_dir),
            "--checkout-repository",
            str(tmp_path / "checkout"),
            "--receipt",
            str(receipt),
            "--github-output",
            str(github_output),
        ]
    )

    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert payload["result"] == {
        "status": "failure",
        "state": ResolverState.MALFORMED.value,
        "reason": FailureReason.MALFORMED.value,
        "accepted_sha": None,
    }
    assert not repository_dir.exists()
    assert not github_output.exists()
