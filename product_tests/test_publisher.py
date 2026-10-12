"""Real local Git verification; every remote action uses an injected transport."""

import json

import pytest

from autoresearch.engine import git
from autoresearch.publisher import (
    GitHubPublisher,
    PublicationError,
    PublicationUncertain,
    prepare_draft,
)


@pytest.fixture
def draft(tmp_path):
    repository = tmp_path / "candidate"
    repository.mkdir()
    git(repository, "init")
    (repository / "Demo.java").write_text("confirmed code\n")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "confirmed candidate")
    campaign = {
        "id": "campaign1",
        "goal": "Reduce allocation",
        "protocol_hash": "protocol1",
        "approved_hash": "protocol1",
    }
    candidate = {
        "id": "candidate1",
        "campaign_id": "campaign1",
        "status": "succeeded",
        "verdict": "confirmed",
        "hypothesis": "Reuse reduces copying",
        "strategy": "buffer reuse",
        "protocol_hash": "protocol1",
        "workspace": str(repository),
        "revision": git(repository, "rev-parse", "HEAD"),
        "tree": git(repository, "rev-parse", "HEAD^{tree}"),
        "confirmation": {
            "verdict": "promising",
            "baseline": {"valid": True},
            "candidate": {"valid": True},
        },
    }
    campaign["base_revision"] = candidate["revision"]
    return (
        campaign,
        candidate,
        "Recorded before/after results and reproduction command.",
        repository,
    )


class Remote:
    def __init__(self, payload, *, lose_create=False, visible=True):
        self.payload = payload
        self.calls = []
        self.created = False
        self.lose_create = lose_create
        self.visible = visible
        self.base_revision = payload["expected_base_revision"]

    def __call__(self, argv, *, stdin=None, cwd=None):
        self.calls.append((argv, stdin))
        if argv[:3] == ["gh", "pr", "list"]:
            return json.dumps([self.pr()] if self.created and self.visible else [])
        if argv[0] == "git":
            assert "push" in argv
            assert "--force" not in argv
            return ""
        if any("/git/ref/heads/" in part for part in argv):
            return json.dumps(
                {
                    "object": {
                        "sha": self.base_revision
                        if argv[-1].endswith("/heads/main")
                        else self.payload["revision"]
                    }
                }
            )
        if "POST" in argv:
            self.created = True
            body = json.loads(stdin)
            assert body["draft"] is True
            assert body["body"] == self.payload["body"]
            if self.lose_create:
                raise PublicationUncertain("Response lost after server created PR")
            return json.dumps(
                {
                    "html_url": "https://github.com/example/project/pull/1",
                    "number": 1,
                    "head": {"sha": self.payload["revision"]},
                    "base": {"sha": self.base_revision},
                    "draft": True,
                    "state": "open",
                }
            )
        pytest.fail("Unexpected remote command: " + repr(argv))

    def pr(self):
        return {
            "number": 1,
            "url": "https://github.com/example/project/pull/1",
            "body": self.payload["body"],
            "headRefOid": self.payload["revision"],
            "isDraft": True,
            "baseRefName": "main",
            "headRefName": "autoresearch/candidate1",
            "state": "OPEN",
        }


def publish(publisher, payload, **changes):
    return publisher.publish_draft(
        payload,
        **{
            "selected_candidate_id": "candidate1",
            "remote_repository": "example/project",
            "base_branch": "main",
            "head_branch": "autoresearch/candidate1",
            **changes,
        },
    )


def test_prepare_is_deterministic_local_and_reviewable(draft):
    campaign, candidate, report, _ = draft
    first = prepare_draft(campaign, candidate, report)
    assert first == prepare_draft(campaign, candidate, report)
    assert first["draft"] is True
    assert candidate["tree"] in first["body"]
    assert report in first["body"]


@pytest.mark.parametrize(
    "change",
    [
        {"verdict": "promising"},
        {"status": "running"},
        {"protocol_hash": "stale"},
        {"campaign_id": "different"},
        {"confirmation": {}},
    ],
)
def test_unconfirmed_or_incompatible_candidates_rejected(draft, change):
    campaign, candidate, report, _ = draft
    with pytest.raises(PublicationError):
        prepare_draft(campaign, {**candidate, **change}, report)


def test_dirty_or_changed_tree_cannot_be_prepared(draft):
    campaign, candidate, report, repository = draft
    (repository / "Demo.java").write_text("unconfirmed\n")
    with pytest.raises(PublicationError):
        prepare_draft(campaign, candidate, report)


def test_explicit_selection_and_payload_integrity_before_remote(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload)
    publisher = GitHubPublisher(tmp_path / "state", transport=remote)
    with pytest.raises(PublicationError, match="selection"):
        publish(publisher, payload, selected_candidate_id="different")
    with pytest.raises(PublicationError, match="changed"):
        publish(publisher, {**payload, "body": "unreviewed body"})
    assert remote.calls == []


def test_publishes_draft_exact_tree_and_repeated_call_only_looks_up(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload)
    publisher = GitHubPublisher(tmp_path / "state", transport=remote)
    result = publish(publisher, payload)
    assert result["draft"] is True and result["existing"] is False
    assert len(remote.calls) == 5
    resumed = GitHubPublisher(tmp_path / "state", transport=remote)
    assert publish(resumed, payload)["existing"] is True
    assert len(remote.calls) == 7
    assert sum("POST" in argv for argv, _ in remote.calls) == 1


def test_lost_response_reconciles_existing_pr_after_restart(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload, lose_create=True)
    with pytest.raises(PublicationUncertain):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    recovered = publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    assert recovered["existing"] is True
    assert sum("POST" in argv for argv, _ in remote.calls) == 1


def test_uncertain_creation_with_no_lookup_match_never_repeats_post(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload, lose_create=True, visible=False)
    for _ in range(2):
        with pytest.raises(PublicationUncertain):
            publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    assert sum("POST" in argv for argv, _ in remote.calls) == 1


def test_report_edit_cannot_bypass_unresolved_publication_identity(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload, lose_create=True, visible=False)
    with pytest.raises(PublicationUncertain):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    edited = prepare_draft(
        campaign, candidate, report + "\nAdditional reproducibility note."
    )
    with pytest.raises(PublicationUncertain):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), edited)
    assert sum("POST" in argv for argv, _ in remote.calls) == 1


def test_local_tree_revalidated_after_review_before_any_remote_action(draft, tmp_path):
    campaign, candidate, report, repository = draft
    payload = prepare_draft(campaign, candidate, report)
    (repository / "Demo.java").write_text("changed after review\n")
    remote = Remote(payload)
    with pytest.raises(PublicationError):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    assert not remote.calls


def test_existing_pr_with_changed_head_is_not_returned_as_confirmed(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload)
    remote.created = True
    original_pr = remote.pr
    remote.pr = lambda: {**original_pr(), "headRefOid": "0" * 40}
    with pytest.raises(PublicationError, match="no longer matches"):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    assert len(remote.calls) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"remote_repository": "https://evil.invalid/repo"},
        {"head_branch": "main"},
        {"head_branch": "--force"},
        {"head_branch": "someone-elses-branch"},
    ],
)
def test_invalid_destination_never_uses_remote(draft, tmp_path, changes):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload)
    with pytest.raises(PublicationError):
        publish(
            GitHubPublisher(tmp_path / "state", transport=remote), payload, **changes
        )
    assert not remote.calls


def test_remote_base_advance_refuses_before_push_or_creation(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload)
    remote.base_revision = "0" * 40
    with pytest.raises(PublicationError, match="Remote base changed"):
        publish(GitHubPublisher(tmp_path / "state", transport=remote), payload)
    assert all(argv[0] != "git" and "POST" not in argv for argv, _ in remote.calls)


def test_base_change_during_creation_retains_uncertain_state(draft, tmp_path):
    campaign, candidate, report, _ = draft
    payload = prepare_draft(campaign, candidate, report)
    remote = Remote(payload, visible=False)

    def transport(argv, **kwargs):
        if "POST" in argv:
            remote.base_revision = "0" * 40
        return remote(argv, **kwargs)

    publisher = GitHubPublisher(tmp_path / "state", transport=transport)
    with pytest.raises(PublicationUncertain):
        publish(publisher, payload)
    remote.base_revision = payload["expected_base_revision"]
    with pytest.raises(PublicationUncertain, match="will not be repeated"):
        publish(publisher, payload)
    assert sum("POST" in argv for argv, _ in remote.calls) == 1
