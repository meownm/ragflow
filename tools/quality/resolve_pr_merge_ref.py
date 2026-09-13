#!/usr/bin/env python3
"""Resolve and attest the immutable merge commit for a pull_request_target event."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

SCHEMA_VERSION = 1
SUPPORTED_EVENT = "pull_request_target"
SUPPORTED_SERVER_URL = "https://github.com"
DEFAULT_DEADLINE_SECONDS = 120.0
DEFAULT_BACKOFF_SECONDS = 2.0
OBJECT_FORMAT = "sha1"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
REPOSITORY_PATTERN = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
PR_NUMBER_PATTERN = re.compile(r"[1-9][0-9]*")
DESTINATION_REF = "refs/ragflow-resolver/candidate"
SUPPORTED_ACTIONS = frozenset({"opened", "synchronize", "reopened", "ready_for_review", "edited"})


class ResolverState(StrEnum):
    ABSENT = "ABSENT"
    MALFORMED = "MALFORMED"
    STALE = "STALE"
    CHANGING = "CHANGING"
    PAYLOAD_MISMATCH = "PAYLOAD_MISMATCH"
    STABLE_CURRENT = "STABLE_CURRENT"
    UNAVAILABLE = "UNAVAILABLE"


class FailureReason(StrEnum):
    INVALID_EVENT = "INVALID_EVENT"
    MALFORMED = "MALFORMED"
    TIMEOUT = "TIMEOUT"
    REF_CHANGED = "REF_CHANGED"
    PARENT_MISMATCH = "PARENT_MISMATCH"
    PAYLOAD_MISMATCH = "PAYLOAD_MISMATCH"
    AUTH_UNSUPPORTED = "AUTH_UNSUPPORTED"
    NETWORK_UNAVAILABLE = "NETWORK_UNAVAILABLE"
    CHECKOUT_UNAVAILABLE = "CHECKOUT_UNAVAILABLE"


@dataclass(frozen=True)
class EventIdentity:
    repository: str
    server_url: str
    event_name: str
    event_action: str
    pr_number: str
    base_sha: str
    head_sha: str
    payload_merge_sha: str
    run_id: str
    run_attempt: str

    @property
    def merge_ref(self) -> str:
        return f"refs/pull/{self.pr_number}/merge"

    @property
    def remote_url(self) -> str:
        return f"{self.server_url}/{self.repository}.git"


@dataclass(frozen=True)
class FetchedObject:
    sha: str
    object_type: str
    parents: tuple[str, ...]


@dataclass(frozen=True)
class AttemptEvidence:
    number: int
    timestamp: str
    elapsed_seconds: float
    state: str
    r1: str | None = None
    fetched: str | None = None
    r2: str | None = None
    object_type: str | None = None
    parents: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResolutionOutcome:
    state: ResolverState
    reason: FailureReason | None
    candidate_sha: str | None
    attempts: tuple[AttemptEvidence, ...]
    elapsed_seconds: float

    @property
    def accepted(self) -> bool:
        return self.state is ResolverState.STABLE_CURRENT and self.reason is None and self.candidate_sha is not None


class ResolverTransport(Protocol):
    def read_exact_ref(self, exact_ref: str, timeout_seconds: float) -> str | None: ...

    def fetch_exact_ref(self, exact_ref: str, timeout_seconds: float) -> FetchedObject: ...


class TransportFailure(RuntimeError):
    def __init__(self, state: ResolverState, reason: FailureReason) -> None:
        super().__init__(reason.value)
        self.state = state
        self.reason = reason


class RefChangedDuringFetch(TransportFailure):
    def __init__(self) -> None:
        super().__init__(ResolverState.CHANGING, FailureReason.REF_CHANGED)


def _is_valid_sha(value: str, *, allow_empty: bool = False) -> bool:
    if allow_empty and not value:
        return True
    return bool(SHA_PATTERN.fullmatch(value)) and value != "0" * 40


def validate_identity(identity: EventIdentity) -> FailureReason | None:
    if identity.event_name != SUPPORTED_EVENT:
        return FailureReason.INVALID_EVENT
    if not PR_NUMBER_PATTERN.fullmatch(identity.pr_number):
        return FailureReason.INVALID_EVENT
    if not REPOSITORY_PATTERN.fullmatch(identity.repository):
        return FailureReason.INVALID_EVENT
    if identity.server_url != SUPPORTED_SERVER_URL:
        return FailureReason.AUTH_UNSUPPORTED
    if not _is_valid_sha(identity.base_sha) or not _is_valid_sha(identity.head_sha):
        return FailureReason.INVALID_EVENT
    if not _is_valid_sha(identity.payload_merge_sha, allow_empty=True):
        return FailureReason.MALFORMED
    if identity.event_action not in SUPPORTED_ACTIONS:
        return FailureReason.INVALID_EVENT
    if not PR_NUMBER_PATTERN.fullmatch(identity.run_id) or not PR_NUMBER_PATTERN.fullmatch(identity.run_attempt):
        return FailureReason.INVALID_EVENT
    return None


def parse_exact_remote_ref(stdout: str, exact_ref: str) -> str:
    lines = stdout.splitlines()
    if len(lines) != 1:
        raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
    match = re.fullmatch(r"([0-9a-f]{40})\t([^\s]+)", lines[0])
    if match is None or match.group(2) != exact_ref or not _is_valid_sha(match.group(1)):
        raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
    return match.group(1)


def parse_commit_parents(commit_text: str) -> tuple[str, ...]:
    parents: list[str] = []
    for line in commit_text.splitlines():
        if not line:
            break
        if not line.startswith("parent "):
            continue
        parent = line.removeprefix("parent ")
        if not _is_valid_sha(parent):
            raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
        parents.append(parent)
    return tuple(parents)


def sanitized_git_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    environment = {key: source[key] for key in ("PATH", "SYSTEMROOT", "WINDIR") if source.get(key)}
    environment.update(
        {
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_COUNT": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    return environment


class GitTransport:
    """Run Git against one resolver-owned repository with a minimal environment."""

    def __init__(
        self,
        repository_dir: Path,
        remote_url: str,
        *,
        source_environment: Mapping[str, str] | None = None,
        allow_file_remote: bool = False,
    ) -> None:
        self.repository_dir = repository_dir.absolute()
        self.remote_url = remote_url
        self.allow_file_remote = allow_file_remote
        self.environment = sanitized_git_environment(source_environment)
        git_path = shutil.which("git", path=self.environment.get("PATH"))
        if git_path is None:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)
        self.git_path = git_path
        self.hooks_dir = self.repository_dir / ".git" / "resolver-hooks"

    @classmethod
    def initialize(
        cls,
        repository_dir: Path,
        remote_url: str,
        *,
        source_environment: Mapping[str, str] | None = None,
        allow_file_remote: bool = False,
        timeout_seconds: float = 10.0,
    ) -> GitTransport:
        started = time.monotonic()

        def remaining() -> float:
            return timeout_seconds - (time.monotonic() - started)

        repository_dir = repository_dir.absolute()
        if repository_dir.exists() or repository_dir.is_symlink():
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)
        template_dir = repository_dir.with_name(f"{repository_dir.name}-empty-template")
        if template_dir.exists() or template_dir.is_symlink():
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)
        try:
            repository_dir.parent.mkdir(parents=True, exist_ok=True)
            template_dir.mkdir()
            repository_dir.mkdir()
        except OSError as error:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE) from error
        transport = cls(
            repository_dir,
            remote_url,
            source_environment=source_environment,
            allow_file_remote=allow_file_remote,
        )
        try:
            transport._run(
                [
                    "init",
                    "--quiet",
                    "--initial-branch=resolver",
                    f"--object-format={OBJECT_FORMAT}",
                    f"--template={template_dir}",
                    str(repository_dir),
                ],
                timeout_seconds=remaining(),
                repository_required=False,
            )
        finally:
            try:
                template_dir.rmdir()
            except OSError:
                pass
        try:
            transport.hooks_dir.mkdir(mode=0o700)
        except OSError as error:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE) from error
        object_format = transport._run(
            ["rev-parse", "--show-object-format"],
            timeout_seconds=remaining(),
        ).stdout.strip()
        if object_format != OBJECT_FORMAT:
            raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
        transport._assert_isolated_repository()
        return transport

    def _base_command(self, *, repository_required: bool = True, allow_file: bool = False) -> list[str]:
        command = [self.git_path, "--no-replace-objects"]
        if repository_required:
            command.extend(["-C", str(self.repository_dir)])
        command.extend(
            [
                "-c",
                "credential.helper=",
                "-c",
                "core.askPass=",
                "-c",
                f"core.hooksPath={self.hooks_dir}",
                "-c",
                f"protocol.file.allow={'always' if allow_file else 'never'}",
                "-c",
                "protocol.ext.allow=never",
                "-c",
                "submodule.recurse=false",
                "-c",
                "fetch.fsckObjects=true",
                "-c",
                "transfer.fsckObjects=true",
                "-c",
                "http.sslVerify=true",
            ]
        )
        return command

    def _run(
        self,
        arguments: Sequence[str],
        *,
        timeout_seconds: float,
        repository_required: bool = True,
        allow_file: bool = False,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        if timeout_seconds <= 0:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
        try:
            completed = subprocess.run(
                [*self._base_command(repository_required=repository_required, allow_file=allow_file), *arguments],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.environment,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT) from error
        if check and completed.returncode != 0:
            raise self._classify_failure(completed)
        return completed

    @staticmethod
    def _classify_failure(completed: subprocess.CompletedProcess[str]) -> TransportFailure:
        message = completed.stderr.lower()
        if "couldn't find remote ref" in message or "not our ref" in message:
            return RefChangedDuringFetch()
        if any(fragment in message for fragment in ("authentication failed", "could not read username", "repository not found")):
            return TransportFailure(ResolverState.UNAVAILABLE, FailureReason.AUTH_UNSUPPORTED)
        return TransportFailure(ResolverState.UNAVAILABLE, FailureReason.NETWORK_UNAVAILABLE)

    def _assert_isolated_repository(self) -> None:
        git_dir = self.repository_dir / ".git"
        forbidden = [
            git_dir / "objects" / "info" / "alternates",
            git_dir / "objects" / "info" / "http-alternates",
            git_dir / "info" / "grafts",
        ]
        if any(path.exists() or path.is_symlink() for path in forbidden):
            raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
        replace_dir = git_dir / "refs" / "replace"
        if replace_dir.is_symlink() or (replace_dir.exists() and any(replace_dir.iterdir())):
            raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)

    def read_exact_ref(self, exact_ref: str, timeout_seconds: float) -> str | None:
        completed = self._run(
            ["ls-remote", "--exit-code", self.remote_url, exact_ref],
            timeout_seconds=timeout_seconds,
            allow_file=self.allow_file_remote,
            check=False,
        )
        if completed.returncode == 2 and not completed.stdout:
            return None
        if completed.returncode != 0:
            raise self._classify_failure(completed)
        return parse_exact_remote_ref(completed.stdout, exact_ref)

    def fetch_exact_ref(self, exact_ref: str, timeout_seconds: float) -> FetchedObject:
        self._assert_isolated_repository()
        started = time.monotonic()

        def remaining() -> float:
            return timeout_seconds - (time.monotonic() - started)

        self._run(
            [
                "fetch",
                "--quiet",
                "--force",
                "--depth=1",
                "--no-tags",
                "--no-write-fetch-head",
                "--no-recurse-submodules",
                self.remote_url,
                f"+{exact_ref}:{DESTINATION_REF}",
            ],
            timeout_seconds=remaining(),
            allow_file=self.allow_file_remote,
        )
        fetched = self._run(
            ["rev-parse", "--verify", DESTINATION_REF],
            timeout_seconds=remaining(),
        ).stdout.strip()
        if not _is_valid_sha(fetched):
            raise TransportFailure(ResolverState.MALFORMED, FailureReason.MALFORMED)
        object_type = self._run(["cat-file", "-t", fetched], timeout_seconds=remaining()).stdout.strip()
        commit_text = self._run(["cat-file", "-p", fetched], timeout_seconds=remaining()).stdout
        parents = parse_commit_parents(commit_text)
        return FetchedObject(sha=fetched, object_type=object_type, parents=parents)

    def materialize_checkout(
        self,
        checkout_repository: Path,
        candidate_sha: str,
        expected_parents: tuple[str, str],
        timeout_seconds: float,
    ) -> None:
        try:
            checkout_repository = checkout_repository.resolve(strict=True)
        except OSError as error:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE) from error
        if not (checkout_repository / ".git").exists():
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)
        destination = GitTransport(checkout_repository, self.remote_url, source_environment=self.environment)
        started = time.monotonic()

        def remaining() -> float:
            return timeout_seconds - (time.monotonic() - started)

        checked_base = destination._run(["rev-parse", "HEAD"], timeout_seconds=remaining()).stdout.strip()
        if checked_base != expected_parents[0]:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)
        try:
            destination._run(
                [
                    "fetch",
                    "--quiet",
                    "--force",
                    "--no-tags",
                    "--no-write-fetch-head",
                    "--no-recurse-submodules",
                    str(self.repository_dir),
                    candidate_sha,
                ],
                timeout_seconds=remaining(),
                allow_file=True,
            )
            destination._run(
                ["checkout", "--quiet", "--detach", "--force", candidate_sha],
                timeout_seconds=remaining(),
            )
            checked_out = destination._run(["rev-parse", "HEAD"], timeout_seconds=remaining()).stdout.strip()
            commit_text = destination._run(["cat-file", "-p", candidate_sha], timeout_seconds=remaining()).stdout
        except TransportFailure as error:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE) from error
        if checked_out != candidate_sha or parse_commit_parents(commit_text) != expected_parents:
            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.CHECKOUT_UNAVAILABLE)


def _timestamp(utc_now: Callable[[], datetime]) -> str:
    value = utc_now().astimezone(UTC)
    return value.isoformat().replace("+00:00", "Z")


def _elapsed(clock: Callable[[], float], started: float) -> float:
    return round(max(0.0, clock() - started), 6)


def _timeout_reason(state: ResolverState) -> FailureReason:
    return {
        ResolverState.ABSENT: FailureReason.TIMEOUT,
        ResolverState.STALE: FailureReason.PARENT_MISMATCH,
        ResolverState.CHANGING: FailureReason.REF_CHANGED,
        ResolverState.PAYLOAD_MISMATCH: FailureReason.PAYLOAD_MISMATCH,
    }.get(state, FailureReason.TIMEOUT)


def _valid_polling_controls(deadline_seconds: float, backoff_seconds: float) -> bool:
    return math.isfinite(deadline_seconds) and math.isfinite(backoff_seconds) and deadline_seconds > 0 and backoff_seconds > 0


def resolve_pr_merge_ref(
    identity: EventIdentity,
    transport: ResolverTransport,
    *,
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    utc_now: Callable[[], datetime] = lambda: datetime.now(UTC),
    started_at: float | None = None,
) -> ResolutionOutcome:
    validation_error = validate_identity(identity)
    if validation_error is not None or not _valid_polling_controls(deadline_seconds, backoff_seconds):
        reason = validation_error or FailureReason.MALFORMED
        return ResolutionOutcome(ResolverState.MALFORMED, reason, None, (), 0.0)

    started = monotonic() if started_at is None else started_at
    if not math.isfinite(started):
        return ResolutionOutcome(ResolverState.MALFORMED, FailureReason.MALFORMED, None, (), 0.0)
    deadline = started + deadline_seconds
    attempts: list[AttemptEvidence] = []
    last_state = ResolverState.ABSENT
    expected_parents = (identity.base_sha, identity.head_sha)

    while True:
        if monotonic() >= deadline:
            return ResolutionOutcome(last_state, _timeout_reason(last_state), None, tuple(attempts), _elapsed(monotonic, started))
        number = len(attempts) + 1
        timestamp = _timestamp(utc_now)
        r1: str | None = None
        fetched: FetchedObject | None = None
        r2: str | None = None
        state = ResolverState.ABSENT
        try:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
            r1 = transport.read_exact_ref(identity.merge_ref, remaining)
            if r1 is not None:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
                try:
                    fetched = transport.fetch_exact_ref(identity.merge_ref, remaining)
                except RefChangedDuringFetch:
                    state = ResolverState.CHANGING
                else:
                    if fetched.object_type != "commit" or not _is_valid_sha(fetched.sha):
                        state = ResolverState.MALFORMED
                    else:
                        remaining = deadline - monotonic()
                        if remaining <= 0:
                            raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
                        r2 = transport.read_exact_ref(identity.merge_ref, remaining)
                        if r2 is None or r1 != fetched.sha or fetched.sha != r2:
                            state = ResolverState.CHANGING
                        elif fetched.parents != expected_parents:
                            state = ResolverState.STALE
                        elif identity.payload_merge_sha and identity.payload_merge_sha != fetched.sha:
                            state = ResolverState.PAYLOAD_MISMATCH
                        else:
                            state = ResolverState.STABLE_CURRENT
            evidence = AttemptEvidence(
                number=number,
                timestamp=timestamp,
                elapsed_seconds=_elapsed(monotonic, started),
                state=state.value,
                r1=r1,
                fetched=fetched.sha if fetched else None,
                r2=r2,
                object_type=fetched.object_type if fetched else None,
                parents=fetched.parents if fetched else (),
            )
            attempts.append(evidence)
        except TransportFailure as error:
            evidence = AttemptEvidence(
                number=number,
                timestamp=timestamp,
                elapsed_seconds=_elapsed(monotonic, started),
                state=error.state.value,
                r1=r1,
                fetched=fetched.sha if fetched else None,
                r2=r2,
                object_type=fetched.object_type if fetched else None,
                parents=fetched.parents if fetched else (),
            )
            attempts.append(evidence)
            return ResolutionOutcome(error.state, error.reason, None, tuple(attempts), _elapsed(monotonic, started))

        if state is ResolverState.STABLE_CURRENT:
            if monotonic() >= deadline:
                return ResolutionOutcome(
                    ResolverState.UNAVAILABLE,
                    FailureReason.TIMEOUT,
                    None,
                    tuple(attempts),
                    _elapsed(monotonic, started),
                )
            return ResolutionOutcome(state, None, fetched.sha, tuple(attempts), _elapsed(monotonic, started))
        if state is ResolverState.MALFORMED:
            return ResolutionOutcome(state, FailureReason.MALFORMED, None, tuple(attempts), _elapsed(monotonic, started))

        last_state = state
        remaining = deadline - monotonic()
        if remaining <= 0:
            return ResolutionOutcome(state, _timeout_reason(state), None, tuple(attempts), _elapsed(monotonic, started))
        sleep(min(backoff_seconds, remaining))


def _canonical_json(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_receipt(
    identity: EventIdentity,
    outcome: ResolutionOutcome,
    *,
    resolver_source_sha256: str,
    deadline_seconds: float,
    backoff_seconds: float,
    checkout_status: str,
) -> dict[str, object]:
    failure_status = (
        "failure"
        if outcome.reason
        in {
            FailureReason.INVALID_EVENT,
            FailureReason.MALFORMED,
            FailureReason.AUTH_UNSUPPORTED,
        }
        else "incomplete"
    )
    receipt: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "profile": {
            "server": SUPPORTED_SERVER_URL,
            "auth": "anonymous_https_public",
            "object_format": OBJECT_FORMAT,
        },
        "identity": asdict(identity),
        "timing": {
            "deadline_seconds": deadline_seconds,
            "backoff": {"kind": "fixed", "seconds": backoff_seconds, "max_seconds": backoff_seconds},
            "attempt_count": len(outcome.attempts),
            "elapsed_seconds": outcome.elapsed_seconds,
        },
        "attempts": [asdict(attempt) for attempt in outcome.attempts],
        "result": {
            "status": "accepted" if outcome.accepted and checkout_status == "verified" else failure_status,
            "state": outcome.state.value,
            "reason": outcome.reason.value if outcome.reason else None,
            "accepted_sha": outcome.candidate_sha if checkout_status == "verified" else None,
        },
        "checkout": {
            "plan": checkout_status,
            "downstream": "exact_sha_refetch_fail_closed",
        },
        "resolver_source_sha256": resolver_source_sha256,
    }
    receipt["receipt_payload_sha256"] = _sha256(_canonical_json(receipt))
    return receipt


def write_receipt_atomic(path: Path, receipt: Mapping[str, object]) -> str:
    path = path.absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(receipt, ensure_ascii=True, indent=2, sort_keys=True).encode() + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return _sha256(data)


def verify_receipt(
    path: Path,
    *,
    identity: EventIdentity,
    candidate_sha: str,
    resolver_source_sha256: str,
    receipt_payload_sha256: str,
    receipt_file_sha256: str,
) -> None:
    data = path.read_bytes()
    if _sha256(data) != receipt_file_sha256:
        raise ValueError("receipt file digest mismatch")
    receipt = json.loads(data)
    if not isinstance(receipt, dict) or receipt.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("receipt schema mismatch")
    payload_digest = receipt.pop("receipt_payload_sha256", None)
    if payload_digest != receipt_payload_sha256 or payload_digest != _sha256(_canonical_json(receipt)):
        raise ValueError("receipt payload digest mismatch")
    if validate_identity(identity) is not None:
        raise ValueError("receipt identity is invalid")
    if identity.payload_merge_sha and identity.payload_merge_sha != candidate_sha:
        raise ValueError("receipt payload binding mismatch")
    if receipt.get("identity") != asdict(identity):
        raise ValueError("receipt identity mismatch")
    if receipt.get("resolver_source_sha256") != resolver_source_sha256:
        raise ValueError("resolver source digest mismatch")
    result = receipt.get("result")
    if result != {
        "status": "accepted",
        "state": ResolverState.STABLE_CURRENT.value,
        "reason": None,
        "accepted_sha": candidate_sha,
    }:
        raise ValueError("receipt result mismatch")
    checkout = receipt.get("checkout")
    if checkout != {"plan": "verified", "downstream": "exact_sha_refetch_fail_closed"}:
        raise ValueError("receipt checkout binding mismatch")
    attempts = receipt.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("receipt attempts missing")
    accepted_attempt = attempts[-1]
    if not isinstance(accepted_attempt, dict):
        raise TypeError("receipt accepted attempt malformed")
    if (
        accepted_attempt.get("state") != ResolverState.STABLE_CURRENT.value
        or accepted_attempt.get("r1") != candidate_sha
        or accepted_attempt.get("fetched") != candidate_sha
        or accepted_attempt.get("r2") != candidate_sha
        or accepted_attempt.get("object_type") != "commit"
        or accepted_attempt.get("parents") != [identity.base_sha, identity.head_sha]
    ):
        raise ValueError("receipt stable binding mismatch")


def _source_digest() -> str:
    return _sha256(Path(__file__).read_bytes())


def _write_github_outputs(path: Path, outputs: Mapping[str, str]) -> None:
    block = "".join(f"{key}={value}\n" for key, value in outputs.items())
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(block)
        handle.flush()
        os.fsync(handle.fileno())


def _identity_from_args(args: argparse.Namespace) -> EventIdentity:
    return EventIdentity(
        repository=args.repository,
        server_url=args.server_url,
        event_name=args.event_name,
        event_action=args.event_action,
        pr_number=args.pr_number,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        payload_merge_sha=args.payload_merge_sha,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )


def _resolve_command(args: argparse.Namespace) -> int:
    command_started = time.monotonic()
    identity = _identity_from_args(args)
    source_digest = _source_digest()
    validation_error = validate_identity(identity)
    transport: GitTransport | None = None
    controls_valid = _valid_polling_controls(args.deadline_seconds, args.backoff_seconds)
    if validation_error is None and controls_valid:
        try:
            remaining = args.deadline_seconds - (time.monotonic() - command_started)
            if remaining <= 0:
                raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
            transport = GitTransport.initialize(
                args.repository_dir,
                identity.remote_url,
                timeout_seconds=min(10.0, remaining),
            )
        except TransportFailure as error:
            outcome = ResolutionOutcome(
                error.state,
                error.reason,
                None,
                (),
                _elapsed(time.monotonic, command_started),
            )
        else:
            outcome = resolve_pr_merge_ref(
                identity,
                transport,
                deadline_seconds=args.deadline_seconds,
                backoff_seconds=args.backoff_seconds,
                started_at=command_started,
            )
    else:
        outcome = ResolutionOutcome(
            ResolverState.MALFORMED,
            validation_error or FailureReason.MALFORMED,
            None,
            (),
            _elapsed(time.monotonic, command_started),
        )

    checkout_status = "not_attempted"
    if outcome.accepted and transport is not None:
        remaining = args.deadline_seconds - (time.monotonic() - command_started)
        try:
            if remaining <= 0:
                raise TransportFailure(ResolverState.UNAVAILABLE, FailureReason.TIMEOUT)
            transport.materialize_checkout(
                args.checkout_repository,
                outcome.candidate_sha,
                (identity.base_sha, identity.head_sha),
                remaining,
            )
        except TransportFailure as error:
            outcome = ResolutionOutcome(
                ResolverState.UNAVAILABLE,
                error.reason,
                None,
                outcome.attempts,
                _elapsed(time.monotonic, command_started),
            )
            checkout_status = "failed"
        else:
            checkout_status = "verified"
            outcome = replace(outcome, elapsed_seconds=_elapsed(time.monotonic, command_started))
        if checkout_status == "verified" and outcome.elapsed_seconds >= args.deadline_seconds:
            outcome = ResolutionOutcome(
                ResolverState.UNAVAILABLE,
                FailureReason.TIMEOUT,
                None,
                outcome.attempts,
                outcome.elapsed_seconds,
            )
            checkout_status = "failed"

    receipt = build_receipt(
        identity,
        outcome,
        resolver_source_sha256=source_digest,
        deadline_seconds=args.deadline_seconds,
        backoff_seconds=args.backoff_seconds,
        checkout_status=checkout_status,
    )
    receipt_file_sha256 = write_receipt_atomic(args.receipt, receipt)
    if not outcome.accepted or checkout_status != "verified":
        reason = outcome.reason.value if outcome.reason else FailureReason.CHECKOUT_UNAVAILABLE.value
        print(f"PR merge-ref resolution failed closed: {reason}", file=sys.stderr)
        return 2
    _write_github_outputs(
        args.github_output,
        {
            "sha": outcome.candidate_sha,
            "receipt": str(args.receipt.absolute()),
            "receipt_sha256": receipt["receipt_payload_sha256"],
            "receipt_file_sha256": receipt_file_sha256,
            "resolver_sha256": source_digest,
        },
    )
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    identity = _identity_from_args(args)
    try:
        verify_receipt(
            args.receipt,
            identity=identity,
            candidate_sha=args.candidate_sha,
            resolver_source_sha256=args.resolver_sha256,
            receipt_payload_sha256=args.receipt_sha256,
            receipt_file_sha256=args.receipt_file_sha256,
        )
    except (OSError, TypeError, ValueError):
        print("PR merge-ref identity receipt verification failed closed", file=sys.stderr)
        return 2
    return 0


def _add_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repository", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--event-action", required=True)
    parser.add_argument("--pr-number", required=True)
    parser.add_argument("--base-sha", required=True)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--payload-merge-sha", default="")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    resolve_parser = subparsers.add_parser("resolve")
    _add_identity_arguments(resolve_parser)
    resolve_parser.add_argument("--repository-dir", type=Path, required=True)
    resolve_parser.add_argument("--checkout-repository", type=Path, required=True)
    resolve_parser.add_argument("--receipt", type=Path, required=True)
    resolve_parser.add_argument("--github-output", type=Path, required=True)
    resolve_parser.add_argument("--deadline-seconds", type=float, default=DEFAULT_DEADLINE_SECONDS)
    resolve_parser.add_argument("--backoff-seconds", type=float, default=DEFAULT_BACKOFF_SECONDS)
    resolve_parser.set_defaults(handler=_resolve_command)

    verify_parser = subparsers.add_parser("verify-receipt")
    _add_identity_arguments(verify_parser)
    verify_parser.add_argument("--receipt", type=Path, required=True)
    verify_parser.add_argument("--candidate-sha", required=True)
    verify_parser.add_argument("--resolver-sha256", required=True)
    verify_parser.add_argument("--receipt-sha256", required=True)
    verify_parser.add_argument("--receipt-file-sha256", required=True)
    verify_parser.set_defaults(handler=_verify_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
