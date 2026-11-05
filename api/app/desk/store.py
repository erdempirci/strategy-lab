"""Storage for the desk journal.

SupabaseStore talks to Supabase's REST API (PostgREST) with the service-role key, server
side only. MemoryStore has the same interface and is used in tests and when Supabase is
not configured (local dev). On Vercel a MemoryStore forgets everything between requests,
which `/api/desk/status` reports.

Uniqueness is enforced by the store (and by the SQL schema), so a cron run and a manual
run at the same time cannot record the same decision or trade twice.
"""

from __future__ import annotations

import copy
import math
import os
import uuid
from datetime import UTC, datetime
from typing import Protocol

import httpx
import numpy as np
import pandas as pd


def iso(ts) -> str:
    """Canonical UTC ISO string used for every stored timestamp."""
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.isoformat()


def plain(v):
    """Numpy scalars → Python types, recursively, so rows serialize as JSON everywhere."""
    if isinstance(v, dict):
        return {k: plain(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [plain(x) for x in v]
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return f if math.isfinite(f) else None
    if isinstance(v, float) and not math.isfinite(v):
        return None
    return v


def now_iso() -> str:
    return iso(datetime.now(UTC))


class StoreError(RuntimeError):
    pass


class DeskStore(Protocol):
    name: str
    persistent: bool

    def get_setting(self, key: str) -> dict | None: ...
    def set_setting(self, key: str, value: dict) -> None: ...
    def list_strategies(self, active: bool | None = None) -> list[dict]: ...
    def add_strategy(self, row: dict) -> dict: ...
    def update_strategy(self, strategy_id: str, patch: dict) -> dict | None: ...
    def list_decisions(self, strategy_id: str | None = None, since: str | None = None,
                       limit: int | None = None) -> list[dict]: ...
    def add_decision(self, row: dict) -> bool: ...
    def list_trades(self, strategy_id: str | None = None, kind: str | None = None) -> list[dict]: ...
    def add_trades(self, rows: list[dict]) -> int: ...
    def add_event(self, row: dict) -> bool: ...
    def list_events(self, kind: str | None = None, limit: int = 50) -> list[dict]: ...


def _ts(v) -> pd.Timestamp:
    return pd.Timestamp(v)


class MemoryStore:
    name = "memory"
    persistent = False

    def __init__(self):
        self.settings: dict[str, dict] = {}
        self.strategies: list[dict] = []
        self.decisions: list[dict] = []
        self.trades: list[dict] = []
        self.events: list[dict] = []

    def get_setting(self, key):
        v = self.settings.get(key)
        return copy.deepcopy(v) if v is not None else None

    def set_setting(self, key, value):
        self.settings[key] = plain(copy.deepcopy(value))

    def list_strategies(self, active=None):
        rows = [s for s in self.strategies if active is None or s["active"] == active]
        return copy.deepcopy(sorted(rows, key=lambda s: s["created_at"]))

    def add_strategy(self, row):
        row = plain({"id": str(uuid.uuid4()), "created_at": now_iso(), "active": True, **row})
        self.strategies.append(copy.deepcopy(row))
        return row

    def update_strategy(self, strategy_id, patch):
        for s in self.strategies:
            if s["id"] == strategy_id:
                s.update(copy.deepcopy(patch))
                return copy.deepcopy(s)
        return None

    def list_decisions(self, strategy_id=None, since=None, limit=None):
        rows = [d for d in self.decisions
                if (strategy_id is None or d["strategy_id"] == strategy_id)
                and (since is None or _ts(d["signal_time"]) >= _ts(since))]
        rows.sort(key=lambda d: _ts(d["signal_time"]), reverse=True)
        return copy.deepcopy(rows[:limit] if limit else rows)

    def add_decision(self, row):
        key = (row["strategy_id"], iso(row["signal_time"]))
        if any((d["strategy_id"], iso(d["signal_time"])) == key for d in self.decisions):
            return False
        self.decisions.append(plain({"id": str(uuid.uuid4()), "decided_at": now_iso(), **row}))
        return True

    def list_trades(self, strategy_id=None, kind=None):
        rows = [t for t in self.trades
                if (strategy_id is None or t["strategy_id"] == strategy_id)
                and (kind is None or t["kind"] == kind)]
        return copy.deepcopy(sorted(rows, key=lambda t: _ts(t["entry_time"])))

    def add_trades(self, rows):
        seen = {(t["strategy_id"], t["kind"], iso(t["entry_time"])) for t in self.trades}
        added = 0
        for r in rows:
            k = (r["strategy_id"], r["kind"], iso(r["entry_time"]))
            if k not in seen:
                self.trades.append(plain({"id": str(uuid.uuid4()), **r}))
                seen.add(k)
                added += 1
        return added

    def add_event(self, row):
        dedupe = row.get("dedupe_key")
        if dedupe and any(e.get("dedupe_key") == dedupe for e in self.events):
            return False
        self.events.append(plain({"id": str(uuid.uuid4()), "created_at": now_iso(), **row}))
        return True

    def list_events(self, kind=None, limit=50):
        rows = [e for e in self.events if kind is None or e["kind"] == kind]
        rows.sort(key=lambda e: e["created_at"], reverse=True)
        return copy.deepcopy(rows[:limit])


class SupabaseStore:
    """PostgREST client. Tables are created by supabase/migrations/001_desk.sql."""
    name = "supabase"
    persistent = True

    def __init__(self, url: str, key: str, client: httpx.Client | None = None):
        # New-style keys (sb_secret_...) are not JWTs: send them only as `apikey`.
        # Legacy service_role keys are JWTs and also go in Authorization.
        headers = {"apikey": key, "Content-Type": "application/json"}
        if not key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {key}"
        self.client = client or httpx.Client(base_url=url.rstrip("/") + "/rest/v1",
                                             timeout=15.0, headers=headers)

    def _req(self, method: str, table: str, params: dict | None = None, json=None,
             prefer: str | None = None) -> list:
        headers = {"Prefer": prefer} if prefer else {}
        try:
            resp = self.client.request(method, f"/{table}", params=params, json=plain(json),
                                       headers=headers)
        except httpx.HTTPError as exc:
            raise StoreError(f"Supabase bağlantı hatası: {exc}") from exc
        if resp.status_code >= 400:
            raise StoreError(f"Supabase HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.content else []

    def get_setting(self, key):
        rows = self._req("GET", "desk_settings", {"select": "value", "key": f"eq.{key}"})
        return rows[0]["value"] if rows else None

    def set_setting(self, key, value):
        self._req("POST", "desk_settings", {"on_conflict": "key"},
                  [{"key": key, "value": value, "updated_at": now_iso()}],
                  prefer="resolution=merge-duplicates,return=minimal")

    def list_strategies(self, active=None):
        params = {"select": "*", "order": "created_at.asc"}
        if active is not None:
            params["active"] = f"eq.{str(active).lower()}"
        return self._req("GET", "desk_strategies", params)

    def add_strategy(self, row):
        return self._req("POST", "desk_strategies", json=[row], prefer="return=representation")[0]

    def update_strategy(self, strategy_id, patch):
        rows = self._req("PATCH", "desk_strategies", {"id": f"eq.{strategy_id}"}, patch,
                         prefer="return=representation")
        return rows[0] if rows else None

    def list_decisions(self, strategy_id=None, since=None, limit=None):
        params = {"select": "*", "order": "signal_time.desc"}
        if strategy_id:
            params["strategy_id"] = f"eq.{strategy_id}"
        if since:
            params["signal_time"] = f"gte.{iso(since)}"
        if limit:
            params["limit"] = str(limit)
        return self._req("GET", "desk_decisions", params)

    def add_decision(self, row):
        out = self._req("POST", "desk_decisions", {"on_conflict": "strategy_id,signal_time"},
                        [row], prefer="resolution=ignore-duplicates,return=representation")
        return bool(out)

    def list_trades(self, strategy_id=None, kind=None):
        params = {"select": "*", "order": "entry_time.asc"}
        if strategy_id:
            params["strategy_id"] = f"eq.{strategy_id}"
        if kind:
            params["kind"] = f"eq.{kind}"
        return self._req("GET", "desk_trades", params)

    def add_trades(self, rows):
        if not rows:
            return 0
        out = self._req("POST", "desk_trades", {"on_conflict": "strategy_id,kind,entry_time"},
                        rows, prefer="resolution=ignore-duplicates,return=representation")
        return len(out)

    def add_event(self, row):
        if row.get("dedupe_key"):
            out = self._req("POST", "desk_events", {"on_conflict": "dedupe_key"}, [row],
                            prefer="resolution=ignore-duplicates,return=representation")
            return bool(out)
        self._req("POST", "desk_events", json=[row], prefer="return=minimal")
        return True

    def list_events(self, kind=None, limit=50):
        params = {"select": "*", "order": "created_at.desc", "limit": str(limit)}
        if kind:
            params["kind"] = f"eq.{kind}"
        return self._req("GET", "desk_events", params)


_memory: MemoryStore | None = None


def get_store() -> DeskStore:
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if url and key:
        return SupabaseStore(url, key)
    global _memory
    if _memory is None:
        _memory = MemoryStore()
    return _memory
