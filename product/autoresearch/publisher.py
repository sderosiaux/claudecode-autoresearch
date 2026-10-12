"""Reviewable draft payloads and explicit, resumable GitHub publication.

Remote operations occur only in publish_draft(), never prepare_draft(). The
caller owns user authorization for the selected candidate and destination.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Callable


class PublicationError(RuntimeError):
    pass


class PublicationUncertain(PublicationError):
    """A create may have succeeded. Lookup is safe; repeating create is not."""


def _hash(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _run(argv: list[str], *, cwd: Path | None = None, stdin: str | None = None) -> str:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        raise PublicationUncertain(
            "Publication command outcome is unknown; inspect publication state before retrying"
        ) from None
    if result.returncode:
        # Upstream output can contain credentials or repository secrets.
        raise PublicationError("Publication command failed; no automatic retry")
    return result.stdout.strip()


def _git(repository: Path, *args: str) -> str:
    return _run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(repository), *args]
    )


def _verify_tree(repository: Path, revision: str, tree: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40,64}", revision) or not re.fullmatch(
        r"[0-9a-f]{40,64}", tree
    ):
        raise PublicationError(
            "Candidate requires full immutable revision and tree identifiers"
        )
    if _git(repository, "rev-parse", "HEAD") != revision:
        raise PublicationError(
            "Candidate checkout no longer matches its confirmed revision"
        )
    if _git(repository, "rev-parse", "HEAD^{tree}") != tree:
        raise PublicationError("Candidate tree differs from confirmed content")
    if _git(repository, "status", "--porcelain", "--untracked-files=all"):
        raise PublicationError(
            "Candidate checkout contains changes not included in confirmation"
        )


def prepare_draft(
    campaign: dict,
    candidate: dict,
    report: str,
    *,
    repository: Path | str | None = None,
) -> dict:
    """Produce a deterministic review payload. Never contacts a remote service."""
    if candidate.get("campaign_id") != campaign.get("id"):
        raise PublicationError("Candidate belongs to another campaign")
    if (
        candidate.get("status") != "succeeded"
        or candidate.get("verdict") != "confirmed"
    ):
        raise PublicationError(
            "Only a completed independently confirmed candidate can be published"
        )
    if (
        not campaign.get("protocol_hash")
        or candidate.get("protocol_hash") != campaign["protocol_hash"]
    ):
        raise PublicationError("Candidate was not confirmed under the current protocol")
    if campaign.get("approved_hash") != campaign["protocol_hash"]:
        raise PublicationError("Current protocol must still be approved")
    confirmation = candidate.get("confirmation")
    if (
        not isinstance(confirmation, dict)
        or confirmation.get("verdict") != "promising"
        or not confirmation.get("baseline", {}).get("valid")
        or not confirmation.get("candidate", {}).get("valid")
    ):
        raise PublicationError(
            "Independent confirmation evidence is missing or invalid"
        )
    path = Path(repository or candidate.get("workspace", "")).resolve()
    if not path.is_dir() or not candidate.get("workspace"):
        raise PublicationError("Candidate workspace is unavailable")
    revision, tree = candidate.get("revision", ""), candidate.get("tree", "")
    _verify_tree(path, revision, tree)
    base_revision = campaign.get("base_revision", "")
    if not isinstance(base_revision, str) or not re.fullmatch(
        r"[0-9a-f]{40,64}", base_revision
    ):
        raise PublicationError("Campaign requires an immutable original base revision")
    identity = _hash(
        {
            "campaign": campaign["id"],
            "candidate": candidate["id"],
            "tree": tree,
            "protocol": campaign["protocol_hash"],
            "base": base_revision,
        }
    )
    marker = "<!-- autoresearch-publication:" + identity + " -->"
    title = (
        "Performance: "
        + " ".join(str(candidate.get("hypothesis", "Confirmed candidate")).split())[
            :180
        ]
    )
    body = "\n".join(
        [
            marker,
            "",
            str(campaign.get("goal", "")),
            "",
            "## Confirmed candidate",
            "",
            str(candidate.get("hypothesis", "")),
            "",
            "Strategy: " + str(candidate.get("strategy", "")),
            "",
            "Campaign: `" + campaign["id"] + "`; candidate: `" + candidate["id"] + "`.",
            "Confirmed revision: `" + revision + "`; tree: `" + tree + "`.",
            "Approved protocol: `" + campaign["protocol_hash"] + "`.",
            "",
            "## Evidence and reproduction",
            "",
            report.strip(),
            "",
            "Results apply only to the recorded workload, environment, and correctness checks.",
            "This is a draft for human review; it does not authorize merging.",
        ]
    )
    payload = {
        "schema_version": 1,
        "campaign_id": campaign["id"],
        "candidate_id": candidate["id"],
        "repository": str(path),
        "revision": revision,
        "tree": tree,
        "expected_base_revision": base_revision,
        "protocol_hash": campaign["protocol_hash"],
        "idempotency_key": identity,
        "title": title,
        "body": body,
        "draft": True,
    }
    payload["review_hash"] = _hash(payload)
    return payload


class GitHubPublisher:
    """Explicit publication with an injectable command transport and durable intent.

    transport(argv, *, cwd=None, stdin=None) returns stdout or raises. It is a
    trusted administrator extension, never data supplied by a model proposal.
    """

    def __init__(self, state_dir: Path | str, *, transport: Callable | None = None):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.transport = transport or _run

    def _json(self, argv: list[str], *, stdin=None):
        try:
            return json.loads(self.transport(argv, stdin=stdin))
        except (ValueError, TypeError):
            raise PublicationUncertain(
                "GitHub response was malformed; publication state remains retained"
            ) from None

    def _write(self, path: Path, record: dict) -> None:
        temporary = path.with_suffix(".tmp")
        with temporary.open("w") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(self.state_dir, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def publish_draft(
        self,
        payload: dict,
        *,
        selected_candidate_id: str,
        remote_repository: str,
        base_branch: str,
        head_branch: str,
    ) -> dict:
        """Publish the reviewed selection; caller must have explicit authorization."""
        if payload.get("review_hash") != _hash(
            {k: v for k, v in payload.items() if k != "review_hash"}
        ):
            raise PublicationError(
                "Draft payload changed since it was prepared for review"
            )
        if (
            selected_candidate_id != payload.get("candidate_id")
            or payload.get("draft") is not True
        ):
            raise PublicationError(
                "Explicit candidate selection and draft mode are required"
            )
        if not re.fullmatch(
            r"[A-Za-z0-9_-][A-Za-z0-9_.-]*/[A-Za-z0-9_-][A-Za-z0-9_.-]*",
            remote_repository,
        ):
            raise PublicationError("Destination must be a GitHub owner/repository")
        for branch in (base_branch, head_branch):
            if not isinstance(branch, str) or branch.startswith("-"):
                raise PublicationError("Invalid publication branch")
            _run(["git", "check-ref-format", "--branch", branch])
        if head_branch == base_branch or not head_branch.startswith("autoresearch/"):
            raise PublicationError(
                "Publication requires a separate autoresearch/ head branch"
            )
        repository = Path(payload["repository"])
        _verify_tree(repository, payload["revision"], payload["tree"])
        destination = {
            "repository": remote_repository,
            "base": base_branch,
            "head": head_branch,
        }
        # Report edits must not bypass an unresolved create for the same candidate.
        identity = _hash(
            {"candidate": payload["idempotency_key"], "destination": destination}
        )
        state_path = self.state_dir / (identity + ".json")
        marker = "<!-- autoresearch-publication:" + payload["idempotency_key"] + " -->"
        with (self.state_dir / "publisher.lock").open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            previous = json.loads(state_path.read_text()) if state_path.exists() else {}
            prs = self._json(
                [
                    "gh",
                    "pr",
                    "list",
                    "--repo",
                    remote_repository,
                    "--state",
                    "all",
                    "--head",
                    head_branch,
                    "--limit",
                    "100",
                    "--json",
                    "number,url,body,headRefOid,isDraft,baseRefName,headRefName,state",
                ]
            )
            if not isinstance(prs, list):
                raise PublicationUncertain("GitHub PR lookup did not return a list")
            if len(prs) >= 100:
                raise PublicationUncertain(
                    "PR lookup may be truncated; reconcile without creating another PR"
                )
            base = self._json(
                [
                    "gh",
                    "api",
                    "repos/" + remote_repository + "/git/ref/heads/" + base_branch,
                ]
            )
            if (
                not isinstance(base, dict)
                or base.get("object", {}).get("sha")
                != payload["expected_base_revision"]
            ):
                raise PublicationError(
                    "Remote base changed since evaluation; reconfirm against the current base before publishing"
                )
            matching = [
                pr
                for pr in prs
                if isinstance(pr, dict) and marker in pr.get("body", "")
            ]
            if len(matching) > 1:
                raise PublicationError(
                    "Multiple PRs match publication identity; human reconciliation required"
                )
            if matching:
                pr = matching[0]
                if (
                    pr.get("headRefOid") != payload["revision"]
                    or pr.get("baseRefName") != base_branch
                    or pr.get("headRefName") != head_branch
                ):
                    raise PublicationError(
                        "Existing PR no longer matches the confirmed content or destination"
                    )
                result = {
                    "url": pr["url"],
                    "number": pr["number"],
                    "state": pr.get("state"),
                    "draft": pr.get("isDraft"),
                    "existing": True,
                    "review_hash": payload["review_hash"],
                }
                self._write(state_path, {"status": "published", "result": result})
                return result
            if prs:
                raise PublicationError(
                    "Head branch already has a different PR; choose another branch"
                )
            if previous.get("status") in {"creating", "published"}:
                raise PublicationUncertain(
                    "Previous PR creation may have succeeded; lookup found none, so creation will not be repeated"
                )
            # No force push, hooks or local branch mutation. An existing divergent
            # branch is rejected by git rather than overwritten.
            self._write(state_path, {"status": "pushing", "destination": destination})
            self.transport(
                [
                    "git",
                    "-c",
                    "core.hooksPath=" + os.devnull,
                    "-C",
                    str(repository),
                    "push",
                    "--",
                    "https://github.com/" + remote_repository + ".git",
                    payload["revision"] + ":refs/heads/" + head_branch,
                ]
            )
            remote = self._json(
                [
                    "gh",
                    "api",
                    "repos/" + remote_repository + "/git/ref/heads/" + head_branch,
                ]
            )
            if (
                not isinstance(remote, dict)
                or remote.get("object", {}).get("sha") != payload["revision"]
            ):
                raise PublicationError(
                    "Remote branch does not match the confirmed revision"
                )
            # Crash or timeout after this durable write must never repeat POST.
            self._write(state_path, {"status": "creating", "destination": destination})
            created = self._json(
                [
                    "gh",
                    "api",
                    "--method",
                    "POST",
                    "repos/" + remote_repository + "/pulls",
                    "--input",
                    "-",
                ],
                stdin=json.dumps(
                    {
                        "title": payload["title"],
                        "body": payload["body"],
                        "head": head_branch,
                        "base": base_branch,
                        "draft": True,
                    }
                ),
            )
            if (
                not isinstance(created, dict)
                or created.get("head", {}).get("sha") != payload["revision"]
                or created.get("base", {}).get("sha")
                != payload["expected_base_revision"]
                or created.get("draft") is not True
                or not created.get("html_url")
                or not created.get("number")
            ):
                raise PublicationUncertain(
                    "PR creation response is incomplete or differs from confirmed content; reconcile by lookup"
                )
            result = {
                "url": created["html_url"],
                "number": created["number"],
                "draft": True,
                "state": created.get("state"),
                "existing": False,
                "review_hash": payload["review_hash"],
            }
            self._write(state_path, {"status": "published", "result": result})
            return result
