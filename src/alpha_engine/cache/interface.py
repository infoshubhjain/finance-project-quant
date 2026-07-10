"""The cache interface. This is the seam the plan insists on: analyzers read from
HERE, never from the network. An ingestion service (Phase 1, separate process or
scheduled job) keeps the store fresh; consumers just read.

The default backend is a local Parquet/JSON store so a freshly cloned repo runs
with zero infrastructure. Swapping in Postgres/Timescale later means implementing
the same Store protocol, and nothing upstream changes.

Freshness: every kind has a TTL. A quote goes stale in seconds, a CPI print in a
month. `get_price`/`get_macro` return data plus whether it's stale, so a consumer
can decide whether to trigger a refresh. The cache never silently serves rot.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from alpha_engine.cache.models import MacroObservation, OptionsChain, PriceSeries

# TTL budget per data kind. Tune as you learn each source's update cadence.
TTL: dict[str, timedelta] = {
    "price:1m": timedelta(minutes=2),
    "price:1h": timedelta(hours=1),
    "price:1d": timedelta(hours=12),
    "macro": timedelta(days=1),
    "chain": timedelta(minutes=15),  # OI moves intraday; chains rot fast
}


def _ttl_for(kind: str, interval: str = "") -> timedelta:
    return TTL.get(f"{kind}:{interval}", TTL.get(kind, timedelta(hours=1)))


def is_stale(fetched_at: datetime, kind: str, interval: str = "") -> bool:
    age = datetime.now(timezone.utc) - fetched_at
    return age > _ttl_for(kind, interval)


class Store(Protocol):
    """Backend contract. LocalStore implements this; a future PostgresStore would
    too. Consumers depend on this protocol, not the concrete backend."""

    def write_price(self, series: PriceSeries) -> None: ...
    def read_price(self, asset: str, interval: str) -> PriceSeries | None: ...
    def write_macro(self, obs: list[MacroObservation]) -> None: ...
    def read_macro(self, series_id: str) -> list[MacroObservation]: ...
    def write_chain(self, chain: OptionsChain) -> None: ...
    def read_chain(self, underlying: str) -> OptionsChain | None: ...


def _tmp_path(p: Path) -> Path:
    """Writer-private temp name. The PID suffix keeps two processes writing the
    same asset (cron batch + a manual scan) from interleaving into one temp
    file; each rename is atomic, last writer wins cleanly."""
    return p.with_name(f"{p.name}.{os.getpid()}.tmp")


def _warn_corrupt(p: Path, e: Exception) -> None:
    """A cache file that fails to parse is treated as absent (the caller will
    refetch), never as a crash: the cache is a convenience copy of remote
    data, so the honest recovery is a refetch, loudly."""
    print(
        f"[cache] WARNING: corrupt cache file {p} ({type(e).__name__}); refetching", file=sys.stderr
    )


class LocalStore:
    """Zero-dependency file-backed store. JSON for simplicity at this stage;
    swap the serialization for Parquet once series get large. Lives under data/
    so a cloner can inspect exactly what's cached."""

    def __init__(self, root: str | Path = "data/cache") -> None:
        self.root = Path(root)
        (self.root / "price").mkdir(parents=True, exist_ok=True)
        (self.root / "macro").mkdir(parents=True, exist_ok=True)
        (self.root / "chain").mkdir(parents=True, exist_ok=True)

    def _price_path(self, asset: str, interval: str) -> Path:
        return self.root / "price" / f"{asset.upper()}_{interval}.json"

    def _macro_path(self, series_id: str) -> Path:
        return self.root / "macro" / f"{series_id}.json"

    def _chain_path(self, underlying: str) -> Path:
        return self.root / "chain" / f"{underlying.upper()}.json"

    def write_price(self, series: PriceSeries) -> None:
        p = self._price_path(series.asset, series.interval.value)
        tmp = _tmp_path(p)
        tmp.write_text(series.model_dump_json(indent=2))
        tmp.rename(p)

    def read_price(self, asset: str, interval: str) -> PriceSeries | None:
        p = self._price_path(asset, interval)
        if not p.exists():
            return None
        try:
            return PriceSeries.model_validate_json(p.read_text())
        except Exception as e:  # noqa: BLE001 - corrupt cache = missing cache
            _warn_corrupt(p, e)
            return None

    def write_macro(self, obs: list[MacroObservation]) -> None:
        """Write macro observations, merging with any existing cached data.

        Observations are keyed by (series_id, timestamp). New observations
        replace any existing observation with the same key; older cached
        observations are preserved. This prevents silent data loss when a
        caller passes only a subset of the full series.
        """
        by_series: dict[str, list[MacroObservation]] = {}
        for o in obs:
            by_series.setdefault(o.series_id, []).append(o)
        for series_id, new_items in by_series.items():
            p = self._macro_path(series_id)
            existing: list[MacroObservation] = []
            if p.exists():
                try:
                    raw = json.loads(p.read_text())
                    existing = [MacroObservation.model_validate(r) for r in raw]
                except Exception:  # noqa: BLE001 - corrupted cache, start fresh
                    existing = []
            # Merge: new observations override by timestamp
            merged_by_ts: dict[datetime, MacroObservation] = {}
            for item in existing:
                merged_by_ts[item.ts] = item
            for item in new_items:
                merged_by_ts[item.ts] = item
            merged = sorted(merged_by_ts.values(), key=lambda o: o.ts)
            tmp = _tmp_path(p)
            tmp.write_text(json.dumps([i.model_dump(mode="json") for i in merged], indent=2))
            tmp.rename(p)

    def read_macro(self, series_id: str) -> list[MacroObservation]:
        p = self._macro_path(series_id)
        if not p.exists():
            return []
        try:
            raw = json.loads(p.read_text())
            return [MacroObservation.model_validate(r) for r in raw]
        except Exception as e:  # noqa: BLE001 - corrupt cache = missing cache
            _warn_corrupt(p, e)
            return []

    def write_chain(self, chain: OptionsChain) -> None:
        p = self._chain_path(chain.underlying)
        tmp = _tmp_path(p)
        tmp.write_text(chain.model_dump_json(indent=2))
        tmp.rename(p)

    def read_chain(self, underlying: str) -> OptionsChain | None:
        p = self._chain_path(underlying)
        if not p.exists():
            return None
        try:
            return OptionsChain.model_validate_json(p.read_text())
        except Exception as e:  # noqa: BLE001 - corrupt cache = missing cache
            _warn_corrupt(p, e)
            return None


class Cache:
    """The public read interface. Analyzers get one of these and ask it for data.
    They never know or care where it came from."""

    def __init__(self, store: Store | None = None) -> None:
        self.store: Store = store or LocalStore()

    def get_price(self, asset: str, interval: str) -> tuple[PriceSeries | None, bool]:
        """Returns (series, stale). series is None if nothing cached yet.
        stale=True means it exists but exceeded its TTL; caller may refresh."""
        series = self.store.read_price(asset, interval)
        if series is None:
            return None, True
        return series, is_stale(series.fetched_at, "price", interval)

    def get_macro(self, series_id: str) -> tuple[list[MacroObservation], bool]:
        obs = self.store.read_macro(series_id)
        if not obs:
            return [], True
        newest = max(o.ts for o in obs)
        return obs, is_stale(newest, "macro")

    def get_chain(self, underlying: str) -> tuple[OptionsChain | None, bool]:
        """Returns (chain, stale). Same contract as get_price: None means
        nothing cached; stale=True means it exists but exceeded its TTL."""
        chain = self.store.read_chain(underlying)
        if chain is None:
            return None, True
        return chain, is_stale(chain.fetched_at, "chain")

    def put_price(self, series: PriceSeries) -> None:
        self.store.write_price(series)

    def put_macro(self, obs: list[MacroObservation]) -> None:
        self.store.write_macro(obs)

    def put_chain(self, chain: OptionsChain) -> None:
        self.store.write_chain(chain)
