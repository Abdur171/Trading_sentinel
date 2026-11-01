"""
feeds/cross_verifier.py - Dual-Engine Market Data Cross-Verification Pipeline.
Cross-audits real-time Nordic quotes from Avanza against Yahoo Finance to prevent
erroneous prints, detect price divergence > 0.5%, and flag stale quotes (> 180s / 3 min).
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any, Tuple

from core.models import MarketQuote
from feeds.base_feed import BaseFeed
from feeds.avanza_feed import AvanzaFeed
from feeds.yfinance_feed import YFinanceFeed

logger = logging.getLogger(__name__)


class CrossVerifier:
    """
    Cross-verifies real-time Nordic market quotes between Avanza and Yahoo Finance.
    Enforces the Live Market Verification Protocol (LMVP):
    - Divergence Alert: abs(p_avanza - p_yf) / p_avanza * 100 > 0.5%
    - Staleness Alert: quote age > 180.0 seconds (3 minutes)
    """

    DEFAULT_THRESHOLD_PCT = 0.5
    DEFAULT_MAX_STALENESS_SEC = 180.0  # 3 minutes

    DEFAULT_NORDIC_SYMBOLS: List[str] = [
        "INVE B",
        "SAAB B",
        "ATCO B",
        "SINCH",
        "MCAP",
        "EQT",
    ]

    def __init__(
        self,
        avanza_feed: Optional[BaseFeed] = None,
        yfinance_feed: Optional[BaseFeed] = None,
        threshold: float = DEFAULT_THRESHOLD_PCT,
        max_age_seconds: float = DEFAULT_MAX_STALENESS_SEC,
    ):
        self.threshold = threshold
        self.max_age_seconds = max_age_seconds
        self.avanza_feed = avanza_feed if avanza_feed is not None else AvanzaFeed()
        self.yfinance_feed = yfinance_feed if yfinance_feed is not None else YFinanceFeed()

    def is_divergent(
        self,
        arg1: float,
        arg2: Optional[float] = None,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        Evaluates whether a quote pair or divergence percentage exceeds the allowed threshold.
        Supports both signatures:
        - is_divergent(divergence_pct: float, threshold: float = 0.5)
        - is_divergent(p_avanza: float, p_yf: float, threshold: float = 0.5)
        """
        thresh = threshold if threshold is not None else self.threshold
        if arg2 is None:
            # arg1 is divergence_pct
            return abs(arg1) > thresh
        else:
            p_avanza, p_yf = arg1, arg2
            if p_avanza <= 0:
                return False
            div_pct = (abs(p_avanza - p_yf) / p_avanza) * 100.0
            return div_pct > thresh

    @staticmethod
    def _calculate_age_seconds(ts: Optional[datetime], now_dt: Optional[datetime] = None) -> Optional[float]:
        """Calculates age in seconds safely handling naive and aware UTC datetimes."""
        if ts is None:
            return None
        if now_dt is None:
            now_dt = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now_dt - ts).total_seconds()

    def is_stale(
        self,
        quote: Optional[MarketQuote],
        max_age_seconds: Optional[float] = None,
    ) -> bool:
        """
        Evaluates whether a market quote is stale (> max_age_seconds, default 180s).
        """
        if quote is None:
            return True

        max_age = max_age_seconds if max_age_seconds is not None else self.max_age_seconds
        now = datetime.now(timezone.utc)

        if quote.timestamp is not None:
            age = self._calculate_age_seconds(quote.timestamp, now)
            if age is not None and age > max_age:
                return True

        if getattr(quote, "delay_seconds", 0.0) > max_age:
            return True

        return False

    def verify_symbol(
        self,
        symbol: str,
        threshold: Optional[float] = None,
        max_age_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Cross-checks a single symbol across Avanza and Yahoo Finance.
        Returns detailed verification metrics including divergence %, staleness, and status.
        """
        thresh = threshold if threshold is not None else self.threshold
        max_age = max_age_seconds if max_age_seconds is not None else self.max_age_seconds

        q_av = self.avanza_feed.get_quote(symbol)
        q_yf = self.yfinance_feed.get_quote(symbol)

        now = datetime.now(timezone.utc)

        # Handle missing feeds
        if q_av is None and q_yf is None:
            return {
                "symbol": symbol,
                "avanza_price": None,
                "yahoo_price": None,
                "divergence_pct": 0.0,
                "is_divergent": False,
                "is_stale": True,
                "status": "MISSING_BOTH",
                "details": f"Symbol {symbol} unavailable on both Avanza and Yahoo Finance.",
                "avanza_quote": None,
                "yahoo_quote": None,
                "avanza_age_seconds": None,
                "yahoo_age_seconds": None,
            }

        if q_av is None:
            p_yf = q_yf.price if q_yf else None
            stale_yf = self.is_stale(q_yf, max_age)
            return {
                "symbol": symbol,
                "avanza_price": None,
                "yahoo_price": p_yf,
                "divergence_pct": 0.0,
                "is_divergent": False,
                "is_stale": stale_yf,
                "status": "MISSING_AVANZA",
                "details": f"Symbol {symbol} missing on Avanza; Yahoo quote: {p_yf} SEK.",
                "avanza_quote": None,
                "yahoo_quote": q_yf,
                "avanza_age_seconds": None,
                "yahoo_age_seconds": self._calculate_age_seconds(q_yf.timestamp, now) if q_yf and q_yf.timestamp else None,
            }

        if q_yf is None:
            p_av = q_av.price
            stale_av = self.is_stale(q_av, max_age)
            return {
                "symbol": symbol,
                "avanza_price": p_av,
                "yahoo_price": None,
                "divergence_pct": 0.0,
                "is_divergent": False,
                "is_stale": stale_av,
                "status": "MISSING_YAHOO",
                "details": f"Symbol {symbol} missing on Yahoo; Avanza quote: {p_av} SEK.",
                "avanza_quote": q_av,
                "yahoo_quote": None,
                "avanza_age_seconds": self._calculate_age_seconds(q_av.timestamp, now) if q_av and q_av.timestamp else None,
                "yahoo_age_seconds": None,
            }

        # Both quotes present
        p_av = float(q_av.price)
        p_yf = float(q_yf.price)

        div_pct = round((abs(p_av - p_yf) / p_av * 100.0), 4) if p_av > 0 else 0.0
        divergent = div_pct > thresh

        stale_av = self.is_stale(q_av, max_age)
        stale_yf = self.is_stale(q_yf, max_age)
        stale = stale_av or stale_yf

        av_age = self._calculate_age_seconds(q_av.timestamp, now) if (q_av and q_av.timestamp) else 0.0
        yf_age = self._calculate_age_seconds(q_yf.timestamp, now) if (q_yf and q_yf.timestamp) else 0.0

        if divergent and stale:
            status = "ALERT_DIVERGENT_AND_STALE"
            details = f"Divergence {div_pct:.2f}% exceeds {thresh}% and quotes stale (Avanza: {av_age:.0f}s, Yahoo: {yf_age:.0f}s)."
        elif divergent:
            status = "ALERT_DIVERGENCE"
            details = f"Divergence {div_pct:.2f}% exceeds {thresh}% (Avanza: {p_av:.2f} SEK vs Yahoo: {p_yf:.2f} SEK)."
        elif stale:
            status = "ALERT_STALE"
            details = f"Quote age exceeds {max_age:.0f}s (Avanza: {av_age:.0f}s, Yahoo: {yf_age:.0f}s)."
        else:
            status = "PASS"
            details = f"Reconciled: Divergence {div_pct:.3f}% <= {thresh}%, feeds fresh."

        return {
            "symbol": symbol,
            "avanza_price": p_av,
            "yahoo_price": p_yf,
            "divergence_pct": div_pct,
            "is_divergent": divergent,
            "is_stale": stale,
            "status": status,
            "details": details,
            "avanza_quote": q_av,
            "yahoo_quote": q_yf,
            "avanza_age_seconds": round(av_age, 1),
            "yahoo_age_seconds": round(yf_age, 1),
        }

    def verify_portfolio(
        self,
        symbols: Optional[List[str]] = None,
        threshold: Optional[float] = None,
        max_age_seconds: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """
        Cross-audits a list of symbols across both feeds.
        Defaults to the primary Nordic mandate holdings.
        """
        sym_list = symbols if symbols is not None else self.DEFAULT_NORDIC_SYMBOLS
        results = []
        for sym in sym_list:
            res = self.verify_symbol(sym, threshold=threshold, max_age_seconds=max_age_seconds)
            results.append(res)
        return results
