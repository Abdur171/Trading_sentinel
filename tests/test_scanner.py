"""
tests/test_scanner.py - Tests for Price Gap Scanner and News Catalyst Detection.
"""

import pytest
from core.state_manager import StateManager
from feeds.mock_feed import MockFeed
from feeds.news_feed import NewsFeed
from dispatchers.composite_dispatcher import CompositeDispatcher
from workers.scanner_worker import ScannerWorker


def test_price_gap_scanner_detection():
    state_mgr = StateManager()
    feed = MockFeed()
    news_feed = NewsFeed()
    dispatcher = CompositeDispatcher()

    worker = ScannerWorker(
        state_mgr=state_mgr,
        feed=feed,
        news_feed=news_feed,
        dispatcher=dispatcher,
        gap_threshold_pct=3.0
    )

    # Inject +5.0% surge on CEG
    feed.set_quote("CEG", price=315.00, prev_close=300.00)
    # Inject -4.5% plunge on VRT
    feed.set_quote("VRT", price=114.60, prev_close=120.00)

    alerts = worker.scan_price_gaps()
    symbols_alerted = [a.symbol for a in alerts]

    assert "CEG" in symbols_alerted
    assert "VRT" in symbols_alerted
    assert any("MARKET GAP SURGE" in a.title for a in alerts if a.symbol == "CEG")
    assert any("MARKET GAP PLUNGE" in a.title for a in alerts if a.symbol == "VRT")


def test_news_material_keyword_filtering():
    news_feed = NewsFeed()
    symbols = ["CEG", "VST", "ORCL"]

    # Test keyword matching logic
    test_keywords = news_feed.MATERIAL_KEYWORDS
    assert "guidance" in test_keywords
    assert "acquisition" in test_keywords
    assert "ppa" in test_keywords
    assert "earnings" in test_keywords
    assert "8-k" in test_keywords


def test_sec_edgar_rss_configuration():
    news_feed = NewsFeed()
    assert any("sec.gov" in url for url in news_feed.DEFAULT_RSS_FEEDS)
    assert hasattr(news_feed, "fetch_sec_edgar_filings")
    filings = news_feed.fetch_sec_edgar_filings(symbol="ORCL", limit=2)
    assert isinstance(filings, list)


def test_cumulative_gap_digest_aggregation():
    state_mgr = StateManager()
    feed = MockFeed()
    news_feed = NewsFeed()
    dispatcher = CompositeDispatcher()

    received_cards = []
    dispatcher.register_handler(lambda a: received_cards.append(a))

    worker = ScannerWorker(
        state_mgr=state_mgr,
        feed=feed,
        news_feed=news_feed,
        dispatcher=dispatcher,
        gap_threshold_pct=3.0
    )

    # Inject 4 gaps: 2 plunges and 2 surges
    feed.set_quote("ORCL", price=140.0, prev_close=150.0)
    feed.set_quote("CEG", price=270.0, prev_close=290.0)
    feed.set_quote("NOW", price=1150.0, prev_close=1050.0)
    feed.set_quote("MCAP.ST", price=540.0, prev_close=500.0)

    alerts = worker.scan_price_gaps()
    assert len(alerts) == 4

    # Dispatched exactly 1 bundled digest card instead of 4 separate messages!
    assert len(received_cards) == 1
    digest = received_cards[0]
    assert "MARKET GAP DIGEST" in digest.title
    assert "Plunges" in digest.message
    assert "Surges" in digest.message
    assert "ORCL" in digest.message
    assert "CEG" in digest.message
    assert "NOW" in digest.message
    assert "MCAP.ST" in digest.message

