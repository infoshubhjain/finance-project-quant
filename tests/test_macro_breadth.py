"""Tests for Phase 11d: RBI, World Bank, the macro calendar, and the
region-aware macro context.

The calendar tests carry the most weight here. It is the only component in the
engine that can *only* reduce confidence, and a sign error would turn a caution
mechanism into a confidence amplifier pointed at exactly the moments when the
model knows least.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from alpha_engine.analyzers.macro_calendar import (
    calendar_note,
    calendar_scalar,
    upcoming_events,
)
from alpha_engine.analyzers.macro_context import analyze_macro
from alpha_engine.cache.interface import Cache, LocalStore
from alpha_engine.cache.models import EventItem, MacroObservation
from alpha_engine.ingestion import rbi, worldbank
from alpha_engine.schema.signal import Direction

NOW = datetime(2025, 6, 1, tzinfo=timezone.utc)


def _event(name: str, days: float, importance: str = "high", region: str = "us") -> EventItem:
    return EventItem(
        ts=NOW + timedelta(days=days),
        name=name,
        region=region,
        importance=importance,
    )


def _macro(series_id: str, values: list[float], source: str = "test") -> list[MacroObservation]:
    return [
        MacroObservation(
            series_id=series_id,
            ts=NOW - timedelta(days=30 * (len(values) - i)),
            value=v,
            source=source,
        )
        for i, v in enumerate(values)
    ]


# ---------------------------------------------------------------------------
# Macro calendar — defensive only
# ---------------------------------------------------------------------------


def test_no_events_means_no_dampening():
    assert calendar_scalar([], "us_equity", now=NOW) == 1.0


def test_scalar_never_exceeds_one():
    """The calendar can only ever reduce confidence. If this fails, a caution
    mechanism has become a confidence amplifier."""
    events = [_event(n, 1.0, imp) for n, imp in [("a", "high"), ("b", "low"), ("c", "medium")]]
    assert calendar_scalar(events, "us_equity", now=NOW) <= 1.0


def test_scalar_is_always_positive():
    events = [_event(f"e{i}", 0.5, "high") for i in range(20)]
    assert calendar_scalar(events, "us_equity", now=NOW) > 0.0


def test_high_importance_dampens_more_than_low():
    high = calendar_scalar([_event("FOMC", 1.0, "high")], "us_equity", now=NOW)
    low = calendar_scalar([_event("Minor", 1.0, "low")], "us_equity", now=NOW)
    assert high < low


def test_events_beyond_the_horizon_are_ignored():
    assert calendar_scalar([_event("FOMC", 30.0, "high")], "us_equity", now=NOW) == 1.0


def test_past_events_are_ignored():
    """An event that already happened is priced in; dampening for it forever
    would be permanent timidity."""
    assert calendar_scalar([_event("FOMC", -1.0, "high")], "us_equity", now=NOW) == 1.0


def test_multiple_events_do_not_compound():
    """A busy week must not multiply down to near-zero confidence."""
    one = calendar_scalar([_event("a", 1.0, "high")], "us_equity", now=NOW)
    many = calendar_scalar([_event(f"e{i}", 1.0, "high") for i in range(5)], "us_equity", now=NOW)
    assert one == many


def test_indian_market_sees_both_rbi_and_fed_events():
    """DXY and Fed policy transmit into Indian markets; ignoring them would be
    a modelling error, not a simplification."""
    assert calendar_scalar([_event("RBI MPC", 1.0, "high", "in")], "in_equity", now=NOW) < 1.0
    assert calendar_scalar([_event("FOMC", 1.0, "high", "us")], "in_equity", now=NOW) < 1.0


def test_us_market_ignores_indian_events():
    assert calendar_scalar([_event("RBI MPC", 1.0, "high", "in")], "us_equity", now=NOW) == 1.0


def test_global_events_reach_every_market():
    for market in ("us_equity", "in_equity", "crypto", "forex"):
        assert calendar_scalar([_event("G20", 1.0, "high", "global")], market, now=NOW) < 1.0


def test_unknown_market_falls_back_to_global_only():
    assert calendar_scalar([_event("G20", 1.0, "high", "global")], "unknown", now=NOW) < 1.0
    assert calendar_scalar([_event("FOMC", 1.0, "high", "us")], "unknown", now=NOW) == 1.0


def test_upcoming_events_are_sorted_soonest_first():
    events = [_event("later", 2.0), _event("sooner", 0.5)]
    assert [e.name for e in upcoming_events(events, "us_equity", now=NOW)] == ["sooner", "later"]


def test_calendar_note_is_empty_without_events():
    assert calendar_note([], "us_equity", now=NOW) == ""


def test_calendar_note_names_the_event():
    note = calendar_note([_event("FOMC rate decision", 1.0, "high")], "us_equity", now=NOW)
    assert "FOMC rate decision" in note and "high" in note


def test_calendar_is_deterministic():
    events = [_event("FOMC", 1.0, "high")]
    assert calendar_scalar(events, "us_equity", now=NOW) == calendar_scalar(
        events, "us_equity", now=NOW
    )


# ---------------------------------------------------------------------------
# Region-aware macro context
# ---------------------------------------------------------------------------


def test_us_region_unchanged_by_indian_data():
    """Backwards compatibility: the default path must behave exactly as before."""
    data = {"FEDFUNDS": _macro("FEDFUNDS", [5.5, 5.5, 5.5, 5.5, 5.5, 5.5, 5.0])}
    with_rbi = dict(data, RBI_REPO_RATE=_macro("RBI_REPO_RATE", [6.5, 6.0]))
    assert analyze_macro(data, region="us").weight == analyze_macro(with_rbi, region="us").weight


def test_indian_region_reads_repo_rate_cuts_as_supportive():
    data = {"RBI_REPO_RATE": _macro("RBI_REPO_RATE", [6.5, 6.0])}
    assert analyze_macro(data, region="in").direction is Direction.BULLISH


def test_indian_region_reads_repo_hikes_as_restrictive():
    data = {"RBI_REPO_RATE": _macro("RBI_REPO_RATE", [6.0, 6.5])}
    assert analyze_macro(data, region="in").direction is Direction.BEARISH


def test_single_repo_reading_abstains():
    """One observation has no direction. It must not be read as 'unchanged'."""
    data = {"RBI_REPO_RATE": _macro("RBI_REPO_RATE", [6.5])}
    assert analyze_macro(data, region="in").weight == 0.0


def test_sustained_fii_selling_is_bearish():
    data = {"FII_NET": _macro("FII_NET", [-1000.0] * 5)}
    assert analyze_macro(data, region="in").direction is Direction.BEARISH


def test_sustained_fii_buying_is_bullish():
    data = {"FII_NET": _macro("FII_NET", [1000.0] * 5)}
    assert analyze_macro(data, region="in").direction is Direction.BULLISH


def test_choppy_fii_flows_abstain():
    data = {"FII_NET": _macro("FII_NET", [1000.0, -1000.0, 900.0, -900.0, 100.0])}
    assert analyze_macro(data, region="in").direction is Direction.NEUTRAL


def test_local_policy_outweighs_the_fed_for_indian_assets():
    """RBI easing must beat Fed tightening for an Indian equity — that is what
    region-awareness has to mean to be worth building."""
    data = {
        "FEDFUNDS": _macro("FEDFUNDS", [5.0, 5.0, 5.0, 5.0, 5.0, 5.0, 5.75]),  # tightening
        "RBI_REPO_RATE": _macro("RBI_REPO_RATE", [6.5, 6.0]),  # easing
    }
    assert analyze_macro(data, region="in").direction is Direction.BULLISH


def test_no_data_is_zero_weight_in_either_region():
    for region in ("us", "in"):
        assert analyze_macro({}, region=region).weight == 0.0


def test_region_appears_in_the_audit_trail():
    data = {"RBI_REPO_RATE": _macro("RBI_REPO_RATE", [6.5, 6.0])}
    assert "region=in" in analyze_macro(data, region="in").detail


# ---------------------------------------------------------------------------
# RBI scraper — bounds checking is the point
# ---------------------------------------------------------------------------


def test_rbi_parses_policy_rates():
    html = "<table><tr><td>Policy Repo Rate</td><td>6.50%</td></tr></table>"
    obs = rbi.parse_rates(html, now=NOW)
    assert len(obs) == 1
    assert obs[0].series_id == "RBI_REPO_RATE"
    assert obs[0].value == pytest.approx(6.5)


def test_rbi_parses_several_rates():
    html = """
      <div>Policy Repo Rate : 6.50%</div>
      <div>Cash Reserve Ratio : 4.50%</div>
      <div>Statutory Liquidity Ratio : 18.00%</div>
    """
    assert {o.series_id for o in rbi.parse_rates(html, now=NOW)} == {
        "RBI_REPO_RATE",
        "RBI_CRR",
        "RBI_SLR",
    }


def test_rbi_rejects_implausible_values_loudly(capsys):
    """The realistic scraping failure is a regex latching onto the wrong number.
    An 87% repo rate is a broken parser, not a surprising rate."""
    html = "<div>Policy Repo Rate</div><div>87.5</div>"
    assert rbi.parse_rates(html, now=NOW) == []
    assert "CONTRACT BROKEN" in capsys.readouterr().err


def test_rbi_unrecognized_page_is_loud(capsys):
    assert rbi.parse_rates("<html><body>Site under maintenance</body></html>", now=NOW) == []
    assert "CONTRACT BROKEN" in capsys.readouterr().err


def test_rbi_empty_page_is_quiet(capsys):
    assert rbi.parse_rates("", now=NOW) == []
    assert "CONTRACT BROKEN" not in capsys.readouterr().err


def test_rbi_fetch_survives_network_failure(monkeypatch):
    def boom(*a, **kw):
        raise OSError("dns failure")

    monkeypatch.setattr(rbi.net, "get", boom)
    assert rbi.fetch_rates() == []


# ---------------------------------------------------------------------------
# World Bank
# ---------------------------------------------------------------------------


def test_worldbank_rejects_unknown_indicator():
    with pytest.raises(ValueError, match="unknown indicator"):
        worldbank.fetch_indicator("NOT.A.REAL.CODE")


def test_worldbank_rejects_unknown_region():
    with pytest.raises(ValueError, match="unknown region"):
        worldbank.fetch_region("atlantis")


def test_worldbank_parses_rows(monkeypatch, tmp_path):
    class FakeResp:
        status_code = 200

        def json(self):
            return [
                {"page": 1},
                [
                    {"date": "2024", "value": 2.5},
                    {"date": "2023", "value": None},  # missing year
                    {"date": "2022", "value": 3.1},
                ],
            ]

    monkeypatch.setattr(worldbank.net, "get", lambda *a, **kw: FakeResp())
    obs = worldbank.fetch_indicator(
        "NY.GDP.MKTP.KD.ZG", country="US", cache=Cache(LocalStore(tmp_path))
    )
    assert len(obs) == 2  # the null year is skipped, not zero-filled
    assert obs[0].series_id == "GDP_GROWTH_US"


def test_worldbank_unexpected_shape_returns_empty(monkeypatch):
    class FakeResp:
        status_code = 200

        def json(self):
            return {"message": "invalid query"}

    monkeypatch.setattr(worldbank.net, "get", lambda *a, **kw: FakeResp())
    assert worldbank.fetch_indicator("NY.GDP.MKTP.KD.ZG") == []


def test_worldbank_http_error_returns_empty(monkeypatch):
    class FakeResp:
        status_code = 503

        def json(self):
            return {}

    monkeypatch.setattr(worldbank.net, "get", lambda *a, **kw: FakeResp())
    assert worldbank.fetch_indicator("FP.CPI.TOTL.ZG") == []


# ---------------------------------------------------------------------------
# Calendar file loader (the thing that makes macro_calendar not-dead)
# ---------------------------------------------------------------------------


def test_calendar_parses_valid_rows():
    from alpha_engine.ingestion.calendar_file import parse_calendar

    events = parse_calendar(
        [
            {"ts": "2026-09-17T18:00:00Z", "name": "FOMC", "region": "us", "importance": "high"},
            {"ts": "2026-10-01T05:00:00Z", "name": "RBI MPC", "region": "in"},
        ]
    )
    assert len(events) == 2
    assert events[0].importance == "high"
    assert events[1].importance == "medium"  # default


def test_calendar_rejects_unknown_region(capsys):
    """An event filed under the wrong region dampens the wrong market, so a bad
    region is skipped rather than defaulted."""
    from alpha_engine.ingestion.calendar_file import parse_calendar

    assert parse_calendar([{"ts": "2026-09-17T18:00:00Z", "name": "X", "region": "mars"}]) == []
    assert "expected one of" in capsys.readouterr().err


def test_calendar_skips_unparseable_dates(capsys):
    from alpha_engine.ingestion.calendar_file import parse_calendar

    assert parse_calendar([{"ts": "not a date", "name": "X", "region": "us"}]) == []
    assert "unparseable date" in capsys.readouterr().err


def test_calendar_skips_rows_missing_fields():
    from alpha_engine.ingestion.calendar_file import parse_calendar

    assert parse_calendar([{"region": "us"}, {"name": "X", "region": "us"}]) == []


def test_calendar_rejects_non_list_payload(capsys):
    from alpha_engine.ingestion.calendar_file import parse_calendar

    assert parse_calendar({"events": []}) == []
    assert "expected a JSON list" in capsys.readouterr().err


def test_calendar_missing_file_is_not_an_error(tmp_path):
    from alpha_engine.ingestion.calendar_file import load_calendar

    assert load_calendar(tmp_path / "nope.json") == []


def test_calendar_loads_and_caches_by_region(tmp_path):
    """The end-to-end gap this closes: without a populated event cache,
    calendar_scalar() is permanently 1.0 and the defensive layer is dead."""
    import json as _json

    from alpha_engine.ingestion.calendar_file import load_calendar

    path = tmp_path / "calendar.json"
    path.write_text(
        _json.dumps(
            [
                {
                    "ts": "2026-09-17T18:00:00Z",
                    "name": "FOMC",
                    "region": "us",
                    "importance": "high",
                },
                {"ts": "2026-10-01T05:00:00Z", "name": "RBI MPC", "region": "in"},
            ]
        )
    )
    cache = Cache(LocalStore(tmp_path / "cache"))
    load_calendar(path, cache=cache)

    us_events, _ = cache.get_events("us")
    assert [e.name for e in us_events] == ["FOMC"]
    all_events, _ = cache.get_events()
    assert len(all_events) == 2


def test_cached_calendar_actually_dampens_a_signal(tmp_path):
    """The whole point, end to end: a loaded calendar must change the scalar."""
    import json as _json

    from alpha_engine.ingestion.calendar_file import load_calendar

    soon = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    path = tmp_path / "calendar.json"
    path.write_text(
        _json.dumps([{"ts": soon, "name": "FOMC", "region": "us", "importance": "high"}])
    )
    cache = Cache(LocalStore(tmp_path / "cache"))
    load_calendar(path, cache=cache)

    events, _ = cache.get_events()
    assert calendar_scalar(events, "us_equity") < 1.0


# ---------------------------------------------------------------------------
# FOMC calendar scraper
#
# The fixtures below are trimmed from the real federalreserve.gov page. Two of
# them encode bugs found only by running against the live page: rows with
# projections carry an extra CSS class (splitting on the row div dropped half
# the year), and "notation vote" rows are not rate decisions at all.
# ---------------------------------------------------------------------------

FOMC_HTML = """
<h4><a id="1">2026 FOMC Meetings</a></h4>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>January</strong></div>
  <div class="fomc-meeting__date col-lg-1">27-28</div>
</div>
<div class="fomc-meeting--shaded row fomc-meeting" ">
  <div class="fomc-meeting--shaded fomc-meeting__month col-md-2"><strong>March</strong></div>
  <div class="fomc-meeting__date col-lg-1">17-18*</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>April</strong></div>
  <div class="fomc-meeting__date col-lg-1">28-29</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>June</strong></div>
  <div class="fomc-meeting__date col-lg-1">16-17*</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>July</strong></div>
  <div class="fomc-meeting__date col-lg-1">28-29</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>September</strong></div>
  <div class="fomc-meeting__date col-lg-1">15-16*</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>Oct/Nov</strong></div>
  <div class="fomc-meeting__date col-lg-1">31-1</div>
</div>
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>December</strong></div>
  <div class="fomc-meeting__date col-lg-1">8-9*</div>
</div>
"""


def test_fomc_parses_all_eight_meetings():
    """Projection meetings carry an extra CSS class. Splitting on the row div
    drops exactly those four, which looks like a working parser."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    assert len(parse_fomc_calendar(FOMC_HTML)) == 8


def test_fomc_decision_lands_on_the_last_day():
    """A two-day meeting decides on day two; that is when the statement lands."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    first = parse_fomc_calendar(FOMC_HTML)[0]
    assert first.ts.date().isoformat() == "2026-01-28"


def test_fomc_handles_a_cross_month_meeting():
    """'Oct/Nov' + '31-1' is Oct 31 to Nov 1 — the decision is in November."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    dates = [e.ts.date().isoformat() for e in parse_fomc_calendar(FOMC_HTML)]
    assert "2026-11-01" in dates
    assert "2026-10-01" not in dates  # the naive reading of the same row


def test_fomc_marks_projection_meetings():
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    events = parse_fomc_calendar(FOMC_HTML)
    projections = [e for e in events if "projections" in e.name]
    assert len(projections) == 4


def test_fomc_events_are_high_importance_and_us_region():
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    for e in parse_fomc_calendar(FOMC_HTML):
        assert e.importance == "high"
        assert e.region == "us"


def test_fomc_rejects_notation_votes(capsys):
    """'22 (notation vote)' is a written policy-strategy vote, not a rate
    decision. Treating it as a meeting dampens confidence on a quiet day."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    html = (
        FOMC_HTML
        + """
<div class="row fomc-meeting" ">
  <div class="fomc-meeting__month col-md-2"><strong>August</strong></div>
  <div class="fomc-meeting__date col-lg-2">22 (notation vote)</div>
</div>
"""
    )
    events = parse_fomc_calendar(html)
    assert len(events) == 8
    assert "2026-08-22" not in [e.ts.date().isoformat() for e in events]
    assert "non-decision" in capsys.readouterr().err


def test_fomc_missing_year_headings_is_loud(capsys):
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    assert parse_fomc_calendar("<html><body>maintenance</body></html>") == []
    assert "CONTRACT BROKEN" in capsys.readouterr().err


def test_fomc_partial_year_is_loud(capsys):
    """A year parsing to two meetings means the layout moved. A half-read
    calendar is worse than none — the gaps look like 'nothing scheduled'."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    html = """
<h4><a id="1">2026 FOMC Meetings</a></h4>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January</strong></div>
<div class="fomc-meeting__date">27-28</div></div>
<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>March</strong></div>
<div class="fomc-meeting__date">17-18</div></div>
"""
    assert parse_fomc_calendar(html) == []
    assert "CONTRACT BROKEN" in capsys.readouterr().err


def test_fomc_impossible_date_is_skipped(capsys):
    """February 31 cannot be a meeting date. That single row is dropped and
    reported; the remaining seven still parse, because 7 is a plausible count
    for a partial year and refusing everything over one bad row would throw away
    good data."""
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    html = FOMC_HTML.replace("<strong>January</strong>", "<strong>February</strong>").replace(
        ">27-28<", ">30-31<"
    )
    events = parse_fomc_calendar(html)
    assert len(events) == 7
    assert "2026-2-31" in capsys.readouterr().err


def test_fomc_events_are_sorted():
    from alpha_engine.ingestion.fomc_calendar import parse_fomc_calendar

    events = parse_fomc_calendar(FOMC_HTML)
    assert events == sorted(events, key=lambda e: e.ts)


def test_fomc_fetch_survives_network_failure(monkeypatch):
    from alpha_engine.ingestion import fomc_calendar

    def boom(*a, **kw):
        raise OSError("dns failure")

    monkeypatch.setattr(fomc_calendar.net, "get", boom)
    assert fomc_calendar.fetch_fomc_calendar() == []


def test_fomc_fetch_caches_and_dampens(tmp_path, monkeypatch):
    """End to end: scraped FOMC dates must actually reach calendar_scalar."""
    from alpha_engine.ingestion import fomc_calendar

    soon = datetime.now(timezone.utc) + timedelta(days=1)
    html = (
        f"""
<h4><a id="1">{soon.year} FOMC Meetings</a></h4>
"""
        + "".join(
            f'<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>'
            f"{soon.strftime('%B')}</strong></div>"
            f'<div class="fomc-meeting__date">{soon.day}</div></div>'
            for _ in range(1)
        )
        + "".join(
            f'<div class="row fomc-meeting"><div class="fomc-meeting__month"><strong>January'
            f'</strong></div><div class="fomc-meeting__date">{d}</div></div>'
            for d in range(1, 8)
        )
    )

    class FakeResp:
        status_code = 200
        text = html

    monkeypatch.setattr(fomc_calendar.net, "get", lambda *a, **kw: FakeResp())
    cache = Cache(LocalStore(tmp_path))
    fomc_calendar.fetch_fomc_calendar(cache=cache)

    events, _ = cache.get_events()
    assert events
    assert calendar_scalar(events, "us_equity") < 1.0
