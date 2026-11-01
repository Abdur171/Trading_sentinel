"""
Feeds package for market quotes, fundamentals, and news ingestion.
"""
from core.models import MarketQuote
from feeds.base_feed import BaseFeed
from feeds.avanza_feed import AvanzaFeed
from feeds.yfinance_feed import YFinanceFeed
from feeds.mock_feed import MockFeed
from feeds.news_feed import NewsFeed
from feeds.cross_verifier import CrossVerifier

__all__ = [
    "BaseFeed",
    "AvanzaFeed",
    "YFinanceFeed",
    "MockFeed",
    "NewsFeed",
    "CrossVerifier",
]
