"""
tests/test_intelligence_desk.py - Unit and Integration Tests for IntelligenceDesk.
Audits 08:30 Morning Battle Plan format, catalyst parsing, disclosure categorization,
and institutional multi-desk synthesis.
"""

import pytest
from datetime import datetime, timezone
from core.intelligence_desk import IntelligenceDesk, CatalystEvent
from core.state_manager import StateManager
from core.signals_desk import SignalsDesk, SignalCandidate
from feeds.mock_feed import MockFeed


@pytest.fixture
def test_intel_desk():
    state_mgr = StateManager()
    feed = MockFeed()
    desk = IntelligenceDesk(state_mgr=state_mgr, feed=feed)
    return desk


def test_catalyst_classification_earnings(test_intel_desk):
    cat, sent, impact = test_intel_desk.classify_catalyst(
        title="Aehr Test Systems Reports Record Q1 Revenue and Beats EPS Estimates",
        summary="Net income surged 45% year-over-year as operating profit expanded."
    )
    assert cat == "EARNINGS"
    assert sent == "BULLISH"
    assert impact >= 8.0


def test_catalyst_classification_guidance(test_intel_desk):
    cat, sent, impact = test_intel_desk.classify_catalyst(
        title="Company Lowers Full-Year Revenue Guidance Amid Market Slowdown",
        summary="Management issued a profit warning and cut full-year outlook by 15%."
    )
    assert cat == "GUIDANCE"
    assert sent == "BEARISH"
    assert impact >= 8.5


def test_catalyst_classification_mergers(test_intel_desk):
    cat, sent, impact = test_intel_desk.classify_catalyst(
        title="Saab Announces Acquisition of Tactical Drone Manufacturer in Europe",
        summary="Strategic takeover deal valued at 1.2B SEK to expand defense backlog."
    )
    assert cat == "MERGERS_ACQUISITIONS"
    assert sent in ["BULLISH", "NEUTRAL"]
    assert impact >= 7.5


def test_catalyst_classification_insider_trades(test_intel_desk):
    cat, sent, impact = test_intel_desk.classify_catalyst(
        title="CEO Disclosed Form 4 Insider Purchase of 50,000 Common Shares",
        summary="Director bought shares in open market transaction at 410 SEK."
    )
    assert cat == "INSIDER_TRADES"
    assert sent == "BULLISH"
    assert impact >= 6.0


def test_catalyst_classification_macro(test_intel_desk):
    cat, sent, impact = test_intel_desk.classify_catalyst(
        title="Fed Signals Interest Rate Cut as CPI Inflation Moderates",
        summary="Treasury yields declined following central bank FOMC statement."
    )
    assert cat == "MACRO_REGULATORY"
    assert impact >= 6.5


def test_parse_catalysts_filtering(test_intel_desk):
    raw_news = [
        {"title": "Q3 Earnings Beat", "summary": "Record revenue growth", "symbol": "AEHR"},
        {"title": "Director Insider Buy", "summary": "CEO bought 10k shares", "symbol": "INVE B"},
        {"title": "Routine Industry Commentary", "summary": "Market observed flat action", "symbol": "MKT"},
        {"title": "Company Lowers Guidance", "summary": "Full year forecast cut", "symbol": "XYZ"},
    ]

    # Parse all
    all_events = test_intel_desk.parse_catalysts(raw_news)
    assert len(all_events) == 4
    assert all_events[0].impact_score >= all_events[-1].impact_score  # Sorted descending

    # Filter by category
    earnings_only = test_intel_desk.parse_catalysts(raw_news, categories=["EARNINGS"])
    assert len(earnings_only) == 1
    assert earnings_only[0].symbol == "AEHR"
    assert earnings_only[0].category == "EARNINGS"

    # Filter by min impact
    high_impact = test_intel_desk.parse_catalysts(raw_news, min_impact=8.0)
    assert len(high_impact) >= 2
    for ev in high_impact:
        assert ev.impact_score >= 8.0


def test_morning_battle_plan_5_sections_present(test_intel_desk):
    plan = test_intel_desk.generate_morning_battle_plan()

    # Verify all 5 institutional sections are present
    assert "SECTION 1: OVERNIGHT MACRO TONE" in plan
    assert "SECTION 2: TOP 3 QUALIFIED SWING SETUPS" in plan
    assert "SECTION 3: DERIVATIVES TACTICAL FOCUS" in plan
    assert "SECTION 4: ACTIVE PORTFOLIO STOPS & CASH ALLOCATION" in plan
    assert "SECTION 5: AI-READY PROMPT DOSSIER" in plan

    # Verify key metadata
    assert "08:30 CEST MORNING BATTLE PLAN" in plan
    assert "696.50" in plan


def test_morning_battle_plan_with_qualified_signals(test_intel_desk):
    # Inject synthetic signals candidate
    candidate = SignalCandidate(
        symbol="AEHR",
        strategy_name="Breakout Momentum",
        entry_price=80.0,
        stop_loss=74.0,
        target_price=95.0,
        rr_ratio=2.50,
        metrics={"rvol": 1.65, "rsi": 58.2}
    )
    test_intel_desk.signals_desk.scan_signals = lambda symbols: [candidate]

    plan = test_intel_desk.generate_morning_battle_plan(top_symbol="AEHR")

    assert "AEHR" in plan
    assert "Breakout Momentum" in plan
    assert "2.50:1" in plan
    assert "SECTION 5: AI-READY PROMPT DOSSIER (`AEHR`)" in plan


def test_morning_battle_plan_derivatives_curfew(test_intel_desk):
    plan = test_intel_desk.generate_morning_battle_plan()
    assert "MINI L OMX AVA 1615" in plan
    assert "19:45 CEST Curfew" in plan
    assert "Hard Stop" in plan


def test_morning_battle_plan_pots_and_reconciliation(test_intel_desk):
    plan = test_intel_desk.generate_morning_battle_plan()
    assert "Mandate Parity:" in plan
    assert "Pot 1 (Core Compounders)" in plan
    assert "Pot 2 (Tactical Swings)" in plan
    assert "Pot 3 (High-Risk Derivatives)" in plan
    assert "Pot 4 (Preservation Cash)" in plan


def test_get_breaking_catalysts(test_intel_desk):
    events = test_intel_desk.get_breaking_catalysts(symbols=["AEHR", "INVE B"], limit=3)
    assert isinstance(events, list)
    for ev in events:
        assert isinstance(ev, CatalystEvent)
        assert ev.symbol in ["AEHR", "INVE B", "MKT"]
