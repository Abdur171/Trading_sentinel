"""
feeds/base_feed.py - Abstract Base Class for Market Data Feeds.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from core.models import MarketQuote


class BaseFeed(ABC):
    """
    Abstract interface for market data feeds.
    """

    @abstractmethod
    def get_quote(self, symbol: str) -> Optional[MarketQuote]:
        """Fetch real-time or delayed quote for a single symbol."""
        pass

    @abstractmethod
    def get_batch_quotes(self, symbols: List[str]) -> Dict[str, MarketQuote]:
        """Fetch quotes for multiple symbols efficiently in batch."""
        pass

    @abstractmethod
    def get_financials(self, symbol: str) -> Dict[str, Any]:
        """Fetch fundamental data for 10-Gate quantitative auditing."""
        pass
