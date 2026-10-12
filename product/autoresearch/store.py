"""Durable domain and atomic admission ledger, shared by every execution path."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(obj):
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


class Conflict(ValueError):
    pass


class BudgetExceeded(Conflict):
    pass


class Store:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS campaigns (
              id TEXT PRIMARY KEY, payload TEXT NOT NULL, limit_usd INTEGER NOT NULL,
              protected INTEGER NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS reservations (
              campaign_id TEXT NOT NULL, key TEXT NOT NULL, amount INTEGER NOT NULL,
              purpose TEXT NOT NULL, status TEXT NOT NULL, actual INTEGER,
              event_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(campaign_id,key));
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
              type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS experiments (
              id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS commands (
              key TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, status TEXT NOT NULL,
              response TEXT, http_status INTEGER, content_type TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS settlement_event ON reservations(campaign_id,event_id)
              WHERE event_id IS NOT NULL;
            PRAGMA user_version=1;
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=20, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def transaction(self):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _event(db, cid, event, data):
        db.execute(
            "INSERT INTO events(campaign_id,type,data,created_at) VALUES(?,?,?,?)",
            (cid, event, encode(data), now()),
        )

    def event(self, cid, event, data):
        with self.transaction() as db:
            self._get(db, cid)
            self._event(db, cid, event, data)

    @staticmethod
    def _get(db, cid):
        row = db.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
        if row is None:
            raise KeyError(cid)
        return json.loads(row["payload"])

    def create(self, payload, limit, protected):
        if not 0 <= protected < limit:
            raise ValueError("Invalid budget")
        cid = uuid.uuid4().hex
        obj = {
            **payload,
            "id": cid,
            "state": "draft",
            "outcome": None,
            "termination_reason": None,
            "created_at": now(),
            "active_seconds": 0,
        }
        with self.transaction() as db:
            db.execute(
                "INSERT INTO campaigns VALUES(?,?,?,?,?)",
                (cid, encode(obj), limit, protected, obj["created_at"]),
            )
            self._event(db, cid, "campaign.created", {"name": obj["name"]})
        return obj

    def get(self, cid):
        with self.connection() as db:
            return self._get(db, cid)

    def campaigns(self):
        with self.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM campaigns ORDER BY created_at DESC"
                )
            ]

    def update(self, cid, changes, *, expected_states=None, event="campaign.updated"):
        with self.transaction() as db:
            obj = self._get(db, cid)
            if expected_states is not None and obj["state"] not in expected_states:
                raise Conflict(
                    f"Cannot perform action while campaign is {obj['state']}"
                )
            obj.update(changes)
            db.execute("UPDATE campaigns SET payload=? WHERE id=?", (encode(obj), cid))
            self._event(db, cid, event, changes)
            return obj

    def events(self, cid, after=0):
        with self.connection() as db:
            self._get(db, cid)
            return [
                {**dict(r), "data": json.loads(r["data"])}
                for r in db.execute(
                    "SELECT * FROM events WHERE campaign_id=? AND seq>? ORDER BY seq",
                    (cid, after),
                )
            ]

    def admit_command(self, key, fingerprint):
        with self.transaction() as db:
            old = db.execute("SELECT * FROM commands WHERE key=?", (key,)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise Conflict("Idempotency key reused for a different request")
                if old["status"] != "done":
                    raise Conflict(
                        "Previous command outcome is unknown or still running; inspect campaign state before a new action"
                    )
                return dict(old)
            db.execute(
                "INSERT INTO commands(key,fingerprint,status) VALUES(?,?,'pending')",
                (key, fingerprint),
            )
            return None

    def finish_command(self, key, response, http_status, content_type):
        with self.transaction() as db:
            db.execute(
                "UPDATE commands SET status='done',response=?,http_status=?,content_type=? WHERE key=?",
                (response, http_status, content_type, key),
            )

    def add_experiment(self, cid, payload):
        eid = uuid.uuid4().hex
        obj = {
            **payload,
            "id": eid,
            "campaign_id": cid,
            "created_at": now(),
            "status": "queued",
            "verdict": None,
            "runs": [],
        }
        with self.transaction() as db:
            self._get(db, cid)
            db.execute("INSERT INTO experiments VALUES(?,?,?)", (eid, cid, encode(obj)))
            self._event(
                db,
                cid,
                "experiment.created",
                {"id": eid, "hypothesis": obj["hypothesis"]},
            )
        return obj

    def experiments(self, cid):
        with self.connection() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM experiments WHERE campaign_id=? ORDER BY rowid",
                    (cid,),
                )
            ]

    def experiment(self, cid, eid):
        with self.connection() as db:
            row = db.execute(
                "SELECT payload FROM experiments WHERE campaign_id=? AND id=?",
                (cid, eid),
            ).fetchone()
            if row is None:
                raise KeyError(eid)
            return json.loads(row[0])

    def update_experiment(self, cid, eid, changes):
        with self.transaction() as db:
            row = db.execute(
                "SELECT payload FROM experiments WHERE campaign_id=? AND id=?",
                (cid, eid),
            ).fetchone()
            if row is None:
                raise KeyError(eid)
            obj = json.loads(row[0])
            obj.update(changes)
            db.execute(
                "UPDATE experiments SET payload=? WHERE id=?", (encode(obj), eid)
            )
            self._event(db, cid, "experiment.updated", {"id": eid, **changes})
            return obj

    @staticmethod
    def _budget(db, cid):
        row = db.execute(
            "SELECT limit_usd,protected FROM campaigns WHERE id=?", (cid,)
        ).fetchone()
        if row is None:
            raise KeyError(cid)
        total = db.execute(
            "SELECT COALESCE(SUM(CASE WHEN status='settled' THEN actual ELSE 0 END),0), COALESCE(SUM(CASE WHEN status!='settled' THEN amount ELSE 0 END),0) FROM reservations WHERE campaign_id=?",
            (cid,),
        ).fetchone()
        consumed, engaged = total
        return {
            "limit": row[0],
            "protected": row[1],
            "consumed": consumed,
            "engaged": engaged,
            "available": row[0] - row[1] - consumed - engaged,
            "currency": "USD",
            "scope": "model API and controlled paid tools; existing compute excluded",
        }

    def budget(self, campaign_id):
        with self.connection() as db:
            budget = self._budget(db, campaign_id)
            campaign = self._get(db, campaign_id)
            budget["enforcement"] = campaign.get("agent", {}).get(
                "enforcement", "strict"
            )
            return budget

    def reserve(self, campaign_id, key, amount_micro_usd, purpose="exploration"):
        if type(amount_micro_usd) is not int or amount_micro_usd < 0:
            raise ValueError("Cost must be nonnegative integer micro-USD")
        with self.transaction() as db:
            obj = self._get(db, campaign_id)
            old = db.execute(
                "SELECT * FROM reservations WHERE campaign_id=? AND key=?",
                (campaign_id, key),
            ).fetchone()
            if old:
                if old["amount"] != amount_micro_usd or old["purpose"] != purpose:
                    raise Conflict("Reservation key reused with different parameters")
                return {
                    **dict(old),
                    "created": False,
                    "amount_micro_usd": old["amount"],
                }
            if obj.get("budget_incident"):
                raise Conflict("Campaign has an unresolved budget incident")
            if obj["state"] in {
                "paused",
                "stopping",
                "stopped",
                "failed",
                "reviewable",
            }:
                raise Conflict("Campaign does not admit new spending")
            b = self._budget(db, campaign_id)
            protected = (
                min(amount_micro_usd, b["protected"])
                if purpose in {"confirmation", "reporting", "cleanup"}
                else 0
            )
            if amount_micro_usd > b["available"] + protected:
                raise BudgetExceeded("Insufficient uncommitted campaign budget")
            db.execute(
                "UPDATE campaigns SET protected=protected-? WHERE id=?",
                (protected, campaign_id),
            )
            db.execute(
                "INSERT INTO reservations VALUES(?,?,?,?,?,?,?,?)",
                (
                    campaign_id,
                    key,
                    amount_micro_usd,
                    purpose,
                    "reserved",
                    None,
                    None,
                    now(),
                ),
            )
            self._event(
                db,
                campaign_id,
                "budget.reserved",
                {"key": key, "amount": amount_micro_usd, "purpose": purpose},
            )
            return {
                "created": True,
                "key": key,
                "amount_micro_usd": amount_micro_usd,
                "status": "reserved",
            }

    def settle(self, campaign_id, key, actual_micro_usd, event_id):
        if type(actual_micro_usd) is not int or actual_micro_usd < 0:
            raise ValueError("Actual cost must be nonnegative integer micro-USD")
        incident = False
        with self.transaction() as db:
            row = db.execute(
                "SELECT * FROM reservations WHERE campaign_id=? AND key=?",
                (campaign_id, key),
            ).fetchone()
            if row is None:
                raise KeyError(key)
            if row["status"] == "settled":
                if row["actual"] != actual_micro_usd or row["event_id"] != event_id:
                    raise Conflict("Conflicting settlement")
                return {"created": False, "actual_micro_usd": actual_micro_usd}
            if db.execute(
                "SELECT 1 FROM reservations WHERE campaign_id=? AND event_id=?",
                (campaign_id, event_id),
            ).fetchone():
                raise Conflict("Settlement event already used")
            db.execute(
                "UPDATE reservations SET status='settled',actual=?,event_id=? WHERE campaign_id=? AND key=?",
                (actual_micro_usd, event_id, campaign_id, key),
            )
            self._event(
                db,
                campaign_id,
                "budget.settled",
                {"key": key, "actual": actual_micro_usd, "event_id": event_id},
            )
            if actual_micro_usd > row["amount"]:
                incident = True
                obj = self._get(db, campaign_id)
                obj.update(
                    state="failed", termination_reason="error", budget_incident=True
                )
                db.execute(
                    "UPDATE campaigns SET payload=? WHERE id=?",
                    (encode(obj), campaign_id),
                )
                self._event(
                    db,
                    campaign_id,
                    "budget.incident",
                    {"key": key, "reserved": row["amount"], "actual": actual_micro_usd},
                )
        if incident:
            raise BudgetExceeded(
                "Reported cost exceeded reserved bound; campaign disabled"
            )
        return {"created": True, "actual_micro_usd": actual_micro_usd}
