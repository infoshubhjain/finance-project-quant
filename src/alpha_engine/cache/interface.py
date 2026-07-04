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
        p.write_text(series.model_dump_json(indent=2))

    def read_price(self, asset: str, interval: str) -> PriceSeries | None:
        p = self._price_path(asset, interval)
        if not p.exists():
            return None
        return PriceSeries.model_validate_json(p.read_text())

    def write_macro(self, obs: list[MacroObservation]) -> None:
        by_series: dict[str, list[MacroObservation]] = {}
        for o in obs:
            by_series.setdefault(o.series_id, []).append(o)
        for series_id, items in by_series.items():
            p = self._macro_path(series_id)
            p.write_text(json.dumps([i.model_dump(mode="json") for i in items], indent=2))

    def read_macro(self, series_id: str) -> list[MacroObservation]:
        p = self._macro_path(series_id)
        if not p.exists():
            return []
        raw = json.loads(p.read_text())
        return [MacroObservation.model_validate(r) for r in raw]

    def write_chain(self, chain: OptionsChain) -> None:
        p = self._chain_path(chain.underlying)
        p.write_text(chain.model_dump_json(indent=2))

    def read_chain(self, underlying: str) -> OptionsChain | None:
        p = self._chain_path(underlying)
        if not p.exists():
            return None
        return OptionsChain.model_validate_json(p.read_text())


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
