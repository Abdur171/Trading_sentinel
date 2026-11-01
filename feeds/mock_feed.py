"""
feeds/mock_feed.py - Deterministic Synthetic Feed for Testing & Scenario Simulations.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from core.models import MarketQuote
from feeds.base_feed import BaseFeed


class MockFeed(BaseFeed):
    """
    Mock market data feed that returns controlled quotes and fundamentals for automated testing.
    """

    DEFAULT_PRICES = {
        # Core Holdings
        "ORCL": 158.20,
        "MCAP.ST": 498.00,
        "GOLD": 4310.00,
        "GC=F": 4310.00,
        "EQT.ST": 340.00,
        "GOOGL": 352.00,
        "CEG": 298.00,
        "NOW": 1050.00,
        "SINCH.ST": 45.20,
        "LRCX": 315.00,
        "GRAB": 4.85,
        "RSG": 210.00,
        "ATCO-B.ST": 178.50,
        "SAAB-B.ST": 540.00,
        "LUN.ST": 160.00,
        "CCJ": 58.00,
        "VRT": 125.00,
        "ADBE": 225.00,
        "BKNG": 165.00,
        "MP": 18.50,
        "XE": 13.50,
        "GLW": 155.00,
        "AEHR": 78.50,
        "ARM": 142.00,
        "VYLR": 28.00,
        "INVE-B.ST": 408.00,
        "Z": 62.00,
        "MINI L OMX AVA 1615": 12.50,
        "^OMX": 2600.00,
        "LINJE": 110.00,
        "AXTI": 2.45,
        "AMZN": 185.00,
        "CRWV": 85.00,
        "ETN": 320.00,
        "GOOG": 165.00,
        "AMD": 155.00,
        "MU": 105.00,
        "MRVL": 75.00,
        "NBIS": 24.00,
        "MINI L AMD AVA 117": 8.50,
        # Watchlist
        "VST": 165.00,
        # Macro Series
        "^TNX": 4.90,
        "^TYX": 4.98,
        "^IRX": 4.60,
        "BZ=F": 94.50,
    }

    def __init__(self, initial_quotes: Optional[Dict[str, float]] = None):
        self.quotes: Dict[str, MarketQuote] = {}
        prices = dict(self.DEFAULT_PRICES)
        if initial_quotes:
            prices.update(initial_quotes)

        for sym, pr in prices.items():
            self.set_quote(sym, pr)

    def set_quote(self, symbol: str, price: float, prev_close: Optional[float] = None) -> None:
        """Sets or overrides a quote in the mock feed."""
        pc = prev_close or price
        chg = price - pc
        chg_pct = (chg / pc * 100.0) if pc > 0 else 0.0
        curr = "SEK" if (".ST" in symbol or "OMX" in symbol or symbol == "LINJE") else "USD"
        
        self.quotes[symbol] = MarketQuote(
            symbol=symbol,
            price=price,
            change=round(chg, 4),
            change_pct=round(chg_pct, 2),
            volume=500000,
            previous_close=pc,
            timestamp=datetime.now(timezone.utc),
            currency=curr,
            is_live=True,
            delay_seconds=0.0
        )

    def get_quote(self, symbol: str) -> Optional[MarketQuote]:
        return self.quotes.get(symbol)

    def get_batch_quotes(self, symbols: List[str]) -> Dict[str, MarketQuote]:
        return {sym: self.quotes[sym] for sym in symbols if sym in self.quotes}

    def get_atr_14(self, symbol: str) -> float:
        """Returns deterministic synthetic ATR for testing (3% of price)."""
        q = self.get_quote(symbol)
        px = q.price if q and q.price > 0 else 100.0
        return round(px * 0.03, 2)

    def get_financials(self, symbol: str) -> Dict[str, Any]:
        """Returns deterministic fundamental metrics for testing 10 Gates."""
        current_price = self.quotes[symbol].price if symbol in self.quotes else 100.0
        return {
            "symbol": symbol,
            "beta": 1.15,
            "roic": 0.18,
            "wacc": 0.08,
            "altman_z": 3.40,
            "piotroski_f": 8,
            "z_quality": 0.90,
            "z_value": 0.60,
            "z_momentum": 0.70,
            "z_growth": 0.75,
            "z_volatility": 0.15,
            "intrinsic_value": round(current_price * 1.30, 2),  # 30% margin of safety
            "annual_volatility": 0.22,
            "dividend_yield": 0.015,
            "is_swing_trade": False,
            # Gates 6-10 Metrics
            "sloan_accrual": -0.04,
            "beneish_m": -2.45,
            "fcf_to_net_income": 0.95,
            "croic": 0.14,
            "net_debt_ebitda": 1.20,
            "interest_coverage": 8.50,
            "share_dilution_yoy": 0.005,
            "shareholder_yield": 0.025,
            "short_interest_pct": 2.5,
            "adv_usd_millions": 45.0
        }
