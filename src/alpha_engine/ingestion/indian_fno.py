"""Helpers for Indian options-chain ingestion.

The live broker fetchers still belong in separate, credential-gated adapters.
What we can pin safely today is the normalization step: take either already-
normalized `OptionsChain` JSON or a common broker-export shape and turn it into
the one cache model the analyzers consume.

This keeps the rest of the system broker-agnostic and gives us an offline
fixture path for Phase 3 before any network integration exists.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from alpha_engine.cache.models import OptionQuote, OptionRight, OptionsChain


def _parse_datetime(raw: object) -> datetime:
    """Parse a broker-export datetime or date into a UTC timestamp."""
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc) if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        # Accept Unix seconds or milliseconds from very coarse exports.
        ts = float(raw)
        if ts > 10_000_000_000:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    if isinstance(raw, str):
        value = raw.strip()
        if not value:
            raise ValueError("empty datetime value")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(f"unsupported datetime value: {raw!r}") from e
        return (
            parsed.astimezone(timezone.utc)
            if parsed.tzinfo
            else parsed.replace(tzinfo=timezone.utc)
        )
    raise ValueError(f"unsupported datetime value: {raw!r}")


def _number(raw: object, field: str) -> float:
    if raw is None:
        raise ValueError(f"{field} is missing")
    if isinstance(raw, bool):
        raise ValueError(f"{field} must be numeric")
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            raise ValueError(f"{field} must be numeric, got {raw!r}")
        try:
            return float(stripped)
        except ValueError as e:
            raise ValueError(f"{field} must be numeric, got {raw!r}") from e
    raise ValueError(f"{field} must be numeric, got {raw!r}")


def _option_quote(row: dict, side: str) -> OptionQuote | None:
    """Parse one CE/PE-like structure into a normalized option quote."""
    payload = row.get(side) or row.get(side.lower())
    if not isinstance(payload, dict):
        return None

    strike = row.get("strikePrice", row.get("strike"))
    if strike is None:
        return None

    oi_raw = payload.get("openInterest", payload.get("oi"))
    if oi_raw is None:
        return None

    right = OptionRight.CALL if side.upper() in {"CE", "CALL"} else OptionRight.PUT
    return OptionQuote(
        strike=_number(strike, "strike"),
        right=right,
        oi=_number(oi_raw, "openInterest"),
        oi_change=(
            _number(payload.get("changeinOpenInterest"), "changeinOpenInterest")
            if payload.get("changeinOpenInterest") is not None
            else _number(payload.get("oi_change"), "oi_change")
            if payload.get("oi_change") is not None
            else None
        ),
        volume=(
            _number(payload.get("totalTradedVolume"), "totalTradedVolume")
            if payload.get("totalTradedVolume") is not None
            else _number(payload.get("volume"), "volume")
            if payload.get("volume") is not None
            else None
        ),
        last_price=(
            _number(payload.get("lastPrice"), "lastPrice")
            if payload.get("lastPrice") is not None
            else _number(payload.get("ltp"), "ltp")
            if payload.get("ltp") is not None
            else None
        ),
    )


def parse_indian_chain_payload(payload: dict, underlying: str | None = None) -> OptionsChain:
    """Normalize a broker-export payload into the shared OptionsChain model.

    Supported shapes are intentionally loose: top-level normalized chains,
    common broker exports with `records` or `data`, and rows carrying CE/PE or
    CALL/PUT substructures. This keeps the loader resilient to slightly different
    broker schemas without hard-coding network access.
    """
    if "quotes" in payload and isinstance(payload.get("quotes"), list) and "expiry" in payload:
        return OptionsChain.model_validate(payload)

    asset = underlying or payload.get("underlying") or payload.get("symbol") or payload.get("name")
    if not asset:
        raise ValueError("payload is missing an underlying symbol")

    expiry_raw = payload.get("expiry") or payload.get("expiryDate") or payload.get("expiry_dt")
    if expiry_raw is None:
        rows = payload.get("records") or payload.get("data") or payload.get("options") or []
        if rows:
            expiry_raw = rows[0].get("expiry") or rows[0].get("expiryDate")
    if expiry_raw is None:
        raise ValueError("payload is missing an expiry")

    spot_raw = payload.get("spot")
    if spot_raw is None:
        spot_raw = payload.get("underlyingValue") or payload.get("ltp") or payload.get("spotPrice")
    spot = _number(spot_raw, "spot") if spot_raw is not None else None

    rows = (
        payload.get("records")
        or payload.get("data")
        or payload.get("options")
        or payload.get("chain")
    )
    if not isinstance(rows, list):
        raise ValueError("payload does not contain an options row list")

    quotes: list[OptionQuote] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        for side in ("CE", "PE", "CALL", "PUT"):
            quote = _option_quote(row, side)
            if quote is not None:
                quotes.append(quote)

    return OptionsChain(
        underlying=str(asset).upper(),
        expiry=_parse_datetime(expiry_raw),
        spot=spot,
        quotes=quotes,
    )


def load_indian_chain(path: str | Path, underlying: str | None = None) -> OptionsChain:
    """Load either a normalized chain or a raw broker-export payload from disk."""
    raw = json.loads(Path(path).read_text())
    try:
        if "quotes" in raw and isinstance(raw.get("quotes"), list) and "expiry" in raw:
            return OptionsChain.model_validate(raw)
    except ValidationError:
        pass
    return parse_indian_chain_payload(raw, underlying=underlying)
