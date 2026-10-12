import concurrent.futures
import pytest

from autoresearch.store import Store, BudgetExceeded, Conflict


def campaign(store, limit=100_000_000, protected=15_000_000):
    return store.create(
        {"name": "test", "agent": {"enforcement": "strict"}}, limit, protected
    )["id"]


def test_concurrent_reservation_and_restart(tmp_path):
    path = tmp_path / "state.sqlite"
    store = Store(path)
    cid = campaign(store)
    store.reserve(cid, "spent", 61_000_000)
    store.settle(cid, "spent", 61_000_000, "bill1")
    store.reserve(cid, "inflight", 16_000_000)

    def attempt(key):
        try:
            return Store(path).reserve(cid, key, 6_000_000)["created"]
        except BudgetExceeded:
            return False

    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(attempt, ["one", "two"])) == [False, True]
    assert Store(path).budget(cid)["available"] == 2_000_000


def test_idempotence_unknown_and_conflicting_settlement(tmp_path):
    store = Store(tmp_path / "db")
    cid = campaign(store)
    assert store.reserve(cid, "unknown", 5_000_000)["created"]
    assert not store.reserve(cid, "unknown", 5_000_000)["created"]
    assert store.budget(cid)["engaged"] == 5_000_000
    with pytest.raises(Conflict):
        store.reserve(cid, "unknown", 6_000_000)
    store.settle(cid, "unknown", 4_000_000, "bill")
    assert not store.settle(cid, "unknown", 4_000_000, "bill")["created"]
    with pytest.raises(Conflict):
        store.settle(cid, "unknown", 3_000_000, "bill")
    assert store.budget(cid)["consumed"] == 4_000_000


def test_protected_conversion_pause_and_overrun(tmp_path):
    store = Store(tmp_path / "db")
    cid = campaign(store, 100, 20)
    store.reserve(cid, "explore", 80)
    with pytest.raises(BudgetExceeded):
        store.reserve(cid, "more", 1)
    store.reserve(cid, "confirm", 10, "confirmation")
    assert store.budget(cid)["protected"] == 10
    assert store.budget(cid)["available"] == 0
    store.update(cid, {"state": "paused"})
    with pytest.raises(Conflict):
        store.reserve(cid, "paused", 0)
    with pytest.raises(BudgetExceeded):
        store.settle(cid, "explore", 101, "invoice")
    assert store.get(cid)["budget_incident"]
    assert store.budget(cid)["consumed"] == 101
