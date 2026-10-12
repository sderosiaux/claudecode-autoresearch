"""No real network requests: exercise admission against adversarial transports."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from autoresearch.provider import (
    ClaudeProvider,
    DuplicateProviderCall,
    MODEL,
    ProviderContractViolation,
    ProviderError,
    StrictBudgetUnavailable,
)


PROPOSAL = {
    "hypothesis": "Repeated copying causes avoidable allocations",
    "strategy": "Reuse an existing immutable buffer",
    "patch": "--- a/Demo.java\n+++ b/Demo.java\n@@ -1 +1 @@\n-old\n+new\n",
    "expected_effect": "Allocation rate may decrease; verify throughput and correctness",
}


class Ledger:
    """Small thread-safe contract double; shared by fresh provider instances."""

    def __init__(self, limit=1_000_000):
        self.limit = limit
        self.rows = {}
        self.events = set()
        self.lock = threading.Lock()

    def budget(self, campaign_id):
        return {"enforcement": "estimated"}

    def reserve(self, campaign_id, key, amount_micro_usd, purpose="exploration"):
        with self.lock:
            identity = (campaign_id, key)
            if identity in self.rows:
                return {**self.rows[identity], "created": False}
            if (
                sum(r["amount_micro_usd"] for r in self.rows.values())
                + amount_micro_usd
                > self.limit
            ):
                raise RuntimeError("Insufficient budget")
            row = {
                "amount_micro_usd": amount_micro_usd,
                "status": "reserved",
                "purpose": purpose,
            }
            self.rows[identity] = row
            return {**row, "created": True}

    def settle(self, campaign_id, key, actual_micro_usd, event_id):
        with self.lock:
            if event_id in self.events:
                return
            self.events.add(event_id)
            self.rows[(campaign_id, key)]["status"] = "settled"
            self.rows[(campaign_id, key)]["actual_micro_usd"] = actual_micro_usd


@pytest.fixture(autouse=True)
def fake_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-noncredential")


def response(**changes):
    body = {
        "id": "msg_unit_test",
        "model": MODEL,
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 100, "output_tokens": 100},
        "content": [{"type": "text", "text": json.dumps(PROPOSAL)}],
    }
    body.update(changes)
    return httpx.Response(200, json=body)


def provider(ledger, handler=None, **config):
    return ClaudeProvider(
        {"enforcement": "estimated", **config},
        ledger,
        transport=httpx.MockTransport(handler or (lambda request: response())),
    )


def test_strict_default_refuses_without_spending():
    ledger = Ledger()
    instance = ClaudeProvider(
        {}, ledger, transport=httpx.MockTransport(lambda r: pytest.fail("Sent"))
    )
    assert instance.capabilities()["strict_budget"] is False
    assert instance.capabilities()["implemented"] is True
    with pytest.raises(StrictBudgetUnavailable):
        instance.propose("campaign", "request", {})
    assert not ledger.rows


def test_success_reserves_before_call_and_keeps_conservative_cost():
    ledger = Ledger()
    seen = []

    def handler(request):
        assert ledger.rows[("campaign", "request")]["status"] == "reserved"
        body = json.loads(request.content)
        assert str(request.url) == "https://api.anthropic.com/v1/messages"
        assert body["model"] == MODEL
        assert body["max_tokens"] == 4096
        assert body["service_tier"] == "standard_only"
        assert body["thinking"] == {"type": "disabled"}
        assert (
            not {"tools", "mcp_servers", "container", "cache_control", "betas"}
            & body.keys()
        )
        assert request.headers["x-api-key"] == "unit-test-noncredential"
        seen.append(body)
        return response()

    instance = provider(ledger, handler)
    assert instance.propose("campaign", "request", {"profile": "copies"}) == PROPOSAL
    row = ledger.rows[("campaign", "request")]
    assert row["actual_micro_usd"] == row["amount_micro_usd"] == 220_480
    assert row["status"] == "settled"
    assert len(seen) == 1


def test_duplicate_success_never_resends_even_after_provider_restart():
    ledger = Ledger()
    assert provider(ledger).propose("campaign", "key", {}) == PROPOSAL
    with pytest.raises(DuplicateProviderCall):
        provider(ledger, lambda r: pytest.fail("Duplicate sent")).propose(
            "campaign", "key", {}
        )
    assert len(ledger.events) == 1


def test_timeout_retains_reservation_and_retry_requires_new_identity():
    ledger = Ledger()
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout(
            "unit-test-noncredential should never be leaked", request=request
        )

    instance = provider(ledger, handler)
    with pytest.raises(ProviderError) as error:
        instance.propose("campaign", "key", {})
    assert "unit-test-noncredential" not in str(error.value)
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"
    with pytest.raises(DuplicateProviderCall):
        provider(ledger, handler).propose("campaign", "key", {})
    assert len(calls) == 1
    assert provider(ledger).propose("campaign", "key-retry-1", {}) == PROPOSAL
    assert sum(row["amount_micro_usd"] for row in ledger.rows.values()) == 440_960


@pytest.mark.parametrize("status", [301, 400, 401, 408, 429, 500, 529])
def test_errors_never_follow_redirects_retry_or_refund(status):
    ledger = Ledger()
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, headers={"location": "https://evil.invalid/"}, text="secret"
        )

    with pytest.raises(ProviderError):
        provider(ledger, handler).propose("campaign", "key", {})
    assert len(calls) == 1
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"
    assert not ledger.events


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"input_tokens": -1, "output_tokens": 1},
        {"input_tokens": True, "output_tokens": 1},
        {"input_tokens": 1, "output_tokens": 1.2},
    ],
)
def test_unknown_or_malformed_usage_never_releases_money(usage):
    ledger = Ledger()
    with pytest.raises(ProviderError):
        provider(ledger, lambda r: response(usage=usage)).propose("campaign", "key", {})
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"


@pytest.mark.parametrize(
    "changes",
    [
        {"model": "unknown-model"},
        {"usage": {"input_tokens": 200_001, "output_tokens": 1}},
        {"usage": {"input_tokens": 1, "output_tokens": 4097}},
        {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 1,
            }
        },
        {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "server_tool_use": {"web_search_requests": 1},
            }
        },
    ],
)
def test_bound_violation_disables_instance_and_preserves_reservation(changes):
    ledger = Ledger()
    instance = provider(ledger, lambda r: response(**changes))
    with pytest.raises(ProviderContractViolation):
        instance.propose("campaign", "key", {})
    assert instance.capabilities()["disabled_after_contract_violation"]
    with pytest.raises(ProviderContractViolation):
        instance.propose("campaign", "another-key", {})
    assert len(ledger.rows) == 1
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"


@pytest.mark.parametrize(
    "changes",
    [
        {"stop_reason": "max_tokens"},
        {"content": [{"type": "tool_use", "name": "bash", "input": "bad"}]},
        {"content": [{"type": "text", "text": "not json"}]},
        {"content": [{"type": "text", "text": "{}"}]},
        {
            "content": [
                {"type": "text", "text": json.dumps({**PROPOSAL, "shell": "bad"})}
            ]
        },
    ],
)
def test_invalid_proposals_do_not_trigger_repair_calls(changes):
    ledger = Ledger()
    with pytest.raises(ProviderError):
        provider(ledger, lambda r: response(**changes)).propose("campaign", "key", {})
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"


def test_preparation_proposal_preserves_protocol_for_supervisor_validation():
    ledger = Ledger()
    proposal = {**PROPOSAL, "protocol": {"requires_human_approval": True}}
    instance = provider(
        ledger,
        lambda r: response(content=[{"type": "text", "text": json.dumps(proposal)}]),
    )
    assert instance.propose("campaign", "key", {}, purpose="preparation") == proposal
    assert ledger.rows[("campaign", "key")]["purpose"] == "preparation"


def test_concurrent_admission_cannot_exceed_budget():
    ledger = Ledger(limit=300_000)
    sent = []

    def handler(request):
        sent.append(request)
        return response()

    def call(index):
        try:
            return provider(ledger, handler).propose("campaign", str(index), {})
        except RuntimeError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(call, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert len(sent) == 1


def test_concurrent_duplicate_identity_is_sent_once():
    ledger = Ledger()
    sent = []

    def handler(request):
        sent.append(request)
        return response()

    def call(_):
        try:
            return provider(ledger, handler).propose("campaign", "same-key", {})
        except DuplicateProviderCall:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(call, [1, 2]))
    assert sum(result is not None for result in results) == 1
    assert len(sent) == 1


def test_missing_key_and_oversized_context_fail_before_reservation(monkeypatch):
    ledger = Ledger()
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ProviderError, match="credential"):
        provider(ledger).propose("campaign", "key", {})
    with pytest.raises(ProviderError, match="byte limit"):
        provider(ledger).propose("campaign", "key", {"data": "x" * 512_001})
    assert not ledger.rows


@pytest.mark.parametrize(
    "config",
    [
        {"model": "claude-haiku-4-5"},
        {"enforcement": "auto"},
        {"max_tokens": True},
        {"max_tokens": 8193},
        {"timeout_seconds": float("inf")},
        {"max_retries": 1},
        {"base_url": "https://evil.invalid"},
        {"api_key": "never accept persisted keys"},
    ],
)
def test_unsafe_or_unpriced_configuration_is_rejected(config):
    with pytest.raises(ProviderError):
        provider(Ledger(), **config)


def test_ledger_failure_prevents_request():
    class FailedLedger(Ledger):
        def reserve(self, *args, **kwargs):
            raise OSError("Database unavailable")

    with pytest.raises(OSError):
        provider(FailedLedger(), lambda r: pytest.fail("sent")).propose(
            "campaign", "key", {}
        )


def test_unproven_reservation_contract_prevents_request():
    class LegacyLedger(Ledger):
        def reserve(self, *args, **kwargs):
            return {"created": 1}

    with pytest.raises(DuplicateProviderCall):
        provider(LegacyLedger(), lambda r: pytest.fail("sent")).propose(
            "campaign", "key", {}
        )


def test_estimated_provider_cannot_downgrade_strict_campaign():
    class StrictLedger(Ledger):
        def budget(self, campaign_id):
            return {"enforcement": "strict"}

    ledger = StrictLedger()
    with pytest.raises(StrictBudgetUnavailable):
        provider(ledger, lambda r: pytest.fail("sent")).propose("campaign", "key", {})
    assert not ledger.rows


def test_sqlite_reservation_survives_restart_after_lost_response(tmp_path):
    from autoresearch.store import Store

    path = tmp_path / "provider.sqlite"
    ledger = Store(path)
    campaign = ledger.create(
        {"name": "test", "agent": {"enforcement": "estimated"}}, 1_000_000, 0
    )

    def timeout(request):
        raise httpx.ReadTimeout("lost", request=request)

    with pytest.raises(ProviderError):
        provider(ledger, timeout).propose(campaign["id"], "same", {})
    recovered = Store(path)
    with pytest.raises(DuplicateProviderCall):
        provider(recovered, lambda r: pytest.fail("resent")).propose(
            campaign["id"], "same", {}
        )
    assert recovered.budget(campaign["id"])["engaged"] == 220_480
    assert recovered.budget(campaign["id"])["consumed"] == 0


def test_settlement_failure_after_response_does_not_admit_duplicate():
    class FailingSettlement(Ledger):
        def settle(self, *args, **kwargs):
            raise OSError("Database unavailable after response")

    ledger = FailingSettlement()
    with pytest.raises(OSError):
        provider(ledger).propose("campaign", "key", {})
    with pytest.raises(DuplicateProviderCall):
        provider(ledger, lambda r: pytest.fail("resent")).propose("campaign", "key", {})
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"


def test_excessive_response_keeps_reservation():
    ledger = Ledger()
    with pytest.raises(ProviderError, match="size"):
        provider(
            ledger, lambda r: httpx.Response(200, content=b"x" * 2_000_001)
        ).propose("campaign", "key", {})
    assert ledger.rows[("campaign", "key")]["status"] == "reserved"
