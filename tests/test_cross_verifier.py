"""
tests/test_cross_verifier.py - Tests for Avanza Feed Parser, CrossVerifier, and CLI Tool.
Verifies Avanza JSON quote/price parsing, warrant mapping, 15s in-memory cache TTL,
divergence threshold <= 0.5% vs > 0.5%, staleness > 180s detection, and verify_live_feeds CLI.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest

from core.models import MarketQuote
from feeds.avanza_feed import AvanzaFeed
from feeds.mock_feed import MockFeed
from feeds.cross_verifier import CrossVerifier


def test_avanza_feed_parse_quote_structure():
    """Verify AvanzaFeed correctly extracts fields from modern 'quote' response payload."""
    feed = AvanzaFeed()

    mock_raw = {
        "orderbookId": "5247",
        "name": "Investor B",
        "listing": {"shortName": "INVE B", "tickerSymbol": "INVE B", "currency": "SEK"},
        "quote": {
            "buy": 412.5,
            "sell": 412.6,
            "last": 412.05,
            "highest": 413.35,
            "lowest": 409.15,
            "change": 5.5,
            "changePercent": 1.35,
            "timeOfLast": 1791559774000,
            "updated": 1791561600411,
            "totalVolumeTraded": 2340458.0,
            "isRealTime": False,
        },
        "type": "STOCK",
    }

    with patch.object(feed, "_fetch_orderbook_data", return_value=mock_raw):
        quote = feed.get_quote("INVE B")
        assert quote is not None
        assert quote.symbol == "INVE B"
        assert quote.price == 412.05
        assert quote.bid == 412.5
        assert quote.ask == 412.6
        assert quote.high == 413.35
        assert quote.low == 409.15
        assert quote.change == 5.5
        assert quote.change_pct == 1.35
        assert quote.volume == 2340458
        assert quote.currency == "SEK"
        assert quote.is_live is True
        assert quote.timestamp == datetime.fromtimestamp(1791559774.0, tz=timezone.utc)


def test_avanza_feed_parse_price_fallback():
    """Verify AvanzaFeed backwards compatibility with legacy 'price' payload structure."""
    feed = AvanzaFeed()

    mock_raw = {
        "orderbookId": "5265",
        "listing": {"currency": "SEK"},
        "price": {
            "lastPrice": 540.0,
            "buyPrice": 539.5,
            "sellPrice": 540.5,
            "highestPrice": 542.0,
            "lowestPrice": 537.0,
            "change": -2.0,
            "changePercent": -0.37,
            "totalVolumeTraded": 120000,
        },
    }

    with patch.object(feed, "_fetch_orderbook_data", return_value=mock_raw):
        quote = feed.get_quote("SAAB B")
        assert quote is not None
        assert quote.price == 540.0
        assert quote.bid == 539.5
        assert quote.ask == 540.5
        assert quote.high == 542.0
        assert quote.low == 537.0
        assert quote.change == -2.0
        assert quote.change_pct == -0.37
        assert quote.volume == 120000


def test_avanza_feed_cache_ttl():
    """Verify AvanzaFeed 15s in-memory cache hit prevents redundant network requests."""
    feed = AvanzaFeed(cache_ttl_seconds=15.0)

    mock_raw = {
        "orderbookId": "5247",
        "listing": {"currency": "SEK"},
        "quote": {"last": 412.05, "change": 0.0, "changePercent": 0.0},
    }

    with patch.object(feed, "_fetch_orderbook_data", return_value=mock_raw) as mock_fetch:
        # First call fetches from source
        q1 = feed.get_quote("INVE B")
        assert q1 is not None
        assert mock_fetch.call_count == 1

        # Second immediate call serves from cache
        q2 = feed.get_quote("INVE B")
        assert q2 is not None
        assert mock_fetch.call_count == 1

        # Clearing cache triggers re-fetch
        feed.clear_cache()
        q3 = feed.get_quote("INVE B")
        assert q3 is not None
        assert mock_fetch.call_count == 2


def test_avanza_feed_warrant_orderbook_mapping():
    """Verify ORDERBOOK_MAP includes high-risk warrant MINI L OMX AVA 1615."""
    feed = AvanzaFeed()
    assert "MINI L OMX AVA 1615" in feed.ORDERBOOK_MAP
    assert feed.ORDERBOOK_MAP["MINI L OMX AVA 1615"] == "1615000"
    assert feed._resolve_orderbook_id("MINI L OMX AVA 1615") == "1615000"


def test_cross_verifier_divergence_below_threshold():
    """Verify CrossVerifier returns PASS when price divergence <= 0.5%."""
    feed_av = MockFeed({"INVE B": 412.05})
    feed_yf = MockFeed({"INVE B": 412.00})

    verifier = CrossVerifier(avanza_feed=feed_av, yfinance_feed=feed_yf, threshold=0.5)
    res = verifier.verify_symbol("INVE B")

    assert res["symbol"] == "INVE B"
    assert res["avanza_price"] == 412.05
    assert res["yahoo_price"] == 412.00
    assert res["is_divergent"] is False
    assert res["divergence_pct"] == pytest.approx(0.0121, abs=1e-3)
    assert res["status"] == "PASS"


def test_cross_verifier_divergence_above_threshold():
    """Verify CrossVerifier alerts when price divergence > 0.5%."""
    feed_av = MockFeed({"INVE B": 400.00})
    feed_yf = MockFeed({"INVE B": 405.00})  # 1.25% divergence

    verifier = CrossVerifier(avanza_feed=feed_av, yfinance_feed=feed_yf, threshold=0.5)
    res = verifier.verify_symbol("INVE B")

    assert res["is_divergent"] is True
    assert res["divergence_pct"] == 1.25
    assert res["status"] == "ALERT_DIVERGENCE"
    assert "exceeds 0.5%" in res["details"]


def test_cross_verifier_stale_quote_flagged():
    """Verify CrossVerifier flags quotes older than 180 seconds as stale."""
    now = datetime.now(timezone.utc)
    stale_timestamp = now - timedelta(seconds=250)  # > 180s

    quote_stale = MarketQuote(
        symbol="INVE B",
        price=412.00,
        currency="SEK",
        timestamp=stale_timestamp,
    )
    quote_fresh = MarketQuote(
        symbol="INVE B",
        price=412.05,
        currency="SEK",
        timestamp=now,
    )

    verifier = CrossVerifier(max_age_seconds=180.0)
    assert verifier.is_stale(quote_stale) is True
    assert verifier.is_stale(quote_fresh) is False

    mock_av = MagicMock()
    mock_av.get_quote.return_value = quote_fresh
    mock_yf = MagicMock()
    mock_yf.get_quote.return_value = quote_stale

    verifier_dual = CrossVerifier(avanza_feed=mock_av, yfinance_feed=mock_yf)
    res = verifier_dual.verify_symbol("INVE B")

    assert res["is_stale"] is True
    assert res["status"] == "ALERT_STALE"


def test_cross_verifier_divergent_and_stale_alert():
    """Verify CrossVerifier flags combined divergence and staleness."""
    now = datetime.now(timezone.utc)
    quote_av = MarketQuote(symbol="INVE B", price=400.00, currency="SEK", timestamp=now - timedelta(seconds=300))
    quote_yf = MarketQuote(symbol="INVE B", price=410.00, currency="SEK", timestamp=now)

    mock_av = MagicMock()
    mock_av.get_quote.return_value = quote_av
    mock_yf = MagicMock()
    mock_yf.get_quote.return_value = quote_yf

    verifier = CrossVerifier(avanza_feed=mock_av, yfinance_feed=mock_yf, threshold=0.5)
    res = verifier.verify_symbol("INVE B")

    assert res["is_divergent"] is True
    assert res["is_stale"] is True
    assert res["status"] == "ALERT_DIVERGENT_AND_STALE"


def test_cross_verifier_missing_feed_graceful():
    """Verify CrossVerifier gracefully handles missing quotes from either feed."""
    mock_av = MagicMock()
    mock_av.get_quote.return_value = None
    mock_yf = MagicMock()
    mock_yf.get_quote.return_value = MarketQuote(symbol="UNKNOWN", price=100.0, currency="SEK")

    verifier = CrossVerifier(avanza_feed=mock_av, yfinance_feed=mock_yf)
    res = verifier.verify_symbol("UNKNOWN")

    assert res["status"] == "MISSING_AVANZA"
    assert res["avanza_price"] is None
    assert res["yahoo_price"] == 100.0


def test_cross_verifier_portfolio_audit():
    """Verify CrossVerifier can audit a full portfolio of symbols."""
    feed_av = MockFeed({"INVE B": 412.05, "SAAB B": 540.00, "ATCO B": 178.50})
    feed_yf = MockFeed({"INVE B": 412.00, "SAAB B": 539.50, "ATCO B": 182.00})  # ATCO B divergent (1.96%)

    verifier = CrossVerifier(avanza_feed=feed_av, yfinance_feed=feed_yf, threshold=0.5)
    results = verifier.verify_portfolio(["INVE B", "SAAB B", "ATCO B"])

    assert len(results) == 3
    assert results[0]["status"] == "PASS"
    assert results[1]["status"] == "PASS"
    assert results[2]["status"] == "ALERT_DIVERGENCE"


def test_verify_live_feeds_cli_mock_mode():
    """Verify verify_live_feeds.py executes cleanly in mock mode and exits 0."""
    result = subprocess.run(
        [sys.executable, "verify_live_feeds.py", "--mock"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "DUAL-ENGINE DATA PIPELINE CROSS-VERIFICATION REPORT" in result.stdout
    assert "INVE B" in result.stdout
    assert "SAAB B" in result.stdout
    assert "HEALTHY" in result.stdout


def test_verify_live_feeds_cli_json_mode():
    """Verify verify_live_feeds.py produces structured JSON in mock mode."""
    result = subprocess.run(
        [sys.executable, "verify_live_feeds.py", "--mock", "--json"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["mode"] == "mock"
    assert "results" in data
    assert data["summary"]["total"] == 6
    assert data["summary"]["passed"] == 6
    assert data["summary"]["divergent"] == 0
