"""
tests/test_feeds.py - Tests for Data Feeds and LMVP Ticker Normalization.
"""

import pytest
from feeds.mock_feed import MockFeed
from feeds.yfinance_feed import YFinanceFeed


def test_mock_feed_crud():
    feed = MockFeed()
    q = feed.get_quote("AEHR")
    assert q is not None
    assert q.price == 78.50

    feed.set_quote("AEHR", 71.50)
    q_updated = feed.get_quote("AEHR")
    assert q_updated.price == 71.50

    batch = feed.get_batch_quotes(["AEHR", "XE", "ORCL"])
    assert len(batch) == 3
    assert "XE" in batch


def test_yfinance_ticker_normalization():
    yf_feed = YFinanceFeed()
    assert yf_feed.normalize_symbol("GOLD") == "GC=F"
    assert yf_feed.normalize_symbol("INVE B") == "INVE-B.ST"
    assert yf_feed.normalize_symbol("ATCO B") == "ATCO-B.ST"
    assert yf_feed.normalize_symbol("SAAB B") == "SAAB-B.ST"
    assert yf_feed.normalize_symbol("MINI L OMX AVA 1615") == "^OMX"
    assert yf_feed.normalize_symbol("GOOGL") == "GOOGL"


def test_mock_feed_financials_has_ten_gates_metrics():
    feed = MockFeed()
    fin = feed.get_financials("HIGHQ")
    # Verify Gates 1-5 metrics
    assert "roic" in fin
    assert "altman_z" in fin
    assert "piotroski_f" in fin
    # Verify Gates 6-10 metrics
    assert "sloan_accrual" in fin
    assert "beneish_m" in fin
    assert "fcf_to_net_income" in fin
    assert "croic" in fin
    assert "net_debt_ebitda" in fin
    assert "interest_coverage" in fin
    assert "share_dilution_yoy" in fin
    assert "shareholder_yield" in fin
    assert "short_interest_pct" in fin
    assert "adv_usd_millions" in fin


def test_yfinance_feed_cache_ttl():
    import time
    from core.models import MarketQuote

    yf_feed = YFinanceFeed(cache_ttl_seconds=1.0)
    # Populate cache manually
    q = MarketQuote(symbol="ORCL", price=158.0, currency="USD")
    yf_feed._cache["ORCL"] = (q, time.time())

    # Immediate retrieval serves from cache without network query
    res = yf_feed.get_batch_quotes(["ORCL"])
    assert "ORCL" in res
    assert res["ORCL"].price == 158.0

    # Clear cache
    yf_feed.clear_cache("ORCL")
    assert "ORCL" not in yf_feed._cache
