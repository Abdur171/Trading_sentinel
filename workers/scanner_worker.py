"""
workers/scanner_worker.py - Market Gap & Material News Scanner Worker.
Scans price dislocations (>3% moves) and in-flight public RSS catalysts.
"""

from __future__ import annotations
import logging
import asyncio
from typing import Dict, List, Optional
from datetime import datetime, timezone
from core.models import AlertEvent, AlertSeverity, MarketQuote
from core.state_manager import StateManager
from feeds.base_feed import BaseFeed
from feeds.news_feed import NewsFeed
from dispatchers.composite_dispatcher import CompositeDispatcher

logger = logging.getLogger(__name__)


class ScannerWorker:
    """
    Screens for market dislocations (>3% price jumps/drops) and material RSS catalysts.
    """

    def __init__(
        self,
        state_mgr: StateManager,
        feed: BaseFeed,
        news_feed: NewsFeed,
        dispatcher: CompositeDispatcher,
        interval_seconds: int = 300,
        gap_threshold_pct: float = 3.0
    ):
        self.state_mgr = state_mgr
        self.feed = feed
        self.news_feed = news_feed
        self.dispatcher = dispatcher
        self.interval = interval_seconds
        self.gap_threshold_pct = gap_threshold_pct
        self._running = False

    def scan_price_gaps(self) -> List[AlertEvent]:
        """Scans all active holdings and watchlist for intraday price gaps exceeding threshold."""
        alerts: List[AlertEvent] = []
        symbols = [h.symbol for h in self.state_mgr.get_all_active_holdings()]
        for w in self.state_mgr.state.watchlist:
            symbols.append(w.symbol)

        quotes = self.feed.get_batch_quotes(list(set(symbols)))

        for sym, q in quotes.items():
            if abs(q.change_pct) >= self.gap_threshold_pct:
                direction = "SURGE" if q.change_pct > 0 else "PLUNGE"
                icon = "🟢" if q.change_pct > 0 else "🔴"
                severity = AlertSeverity.P1 if q.change_pct < -5.0 else AlertSeverity.P0

                alerts.append(AlertEvent(
                    id=f"gap_{sym}_{q.timestamp.timestamp()}",
                    severity=severity,
                    symbol=sym,
                    title=f"{icon} MARKET GAP {direction}: {sym} ({q.change_pct:+.2f}%)",
                    message=(
                        f"Significant price move detected on {sym}!\n"
                        f"Current Price: {q.price:.2f} {q.currency} | Move: {q.change_pct:+.2f}%\n"
                        f"Previous Close: {q.previous_close:.2f} | Volume: {q.volume:,} shares."
                    ),
                    source="scanner_worker",
                    metadata={"change_pct": q.change_pct, "price": q.price}
                ))

        if alerts:
            self.dispatcher.dispatch_batch(alerts)

        return alerts

    def scan_news_catalysts(self) -> List[AlertEvent]:
        """Scans public financial RSS feeds for material catalysts related to mandate assets."""
        alerts: List[AlertEvent] = []
        symbols = [h.symbol for h in self.state_mgr.get_all_active_holdings()]

        news_items = self.news_feed.scan_recent_material_news(symbols)
        for item in news_items:
            sym = item.get("matched_symbol") or "MARKET"
            keywords_str = ", ".join(item.get("keywords", []))
            alerts.append(AlertEvent(
                id=f"news_{sym}_{datetime.now(timezone.utc).timestamp()}",
                severity=AlertSeverity.P0,
                symbol=sym,
                title=f"📰 CATALYST DETECTED: {sym} [{keywords_str}]",
                message=(
                    f"Headline: {item.get('title')}\n\n"
                    f"Summary: {item.get('summary', '')[:250]}...\n"
                    f"Source Link: {item.get('link')}"
                ),
                source="scanner_worker",
                metadata={"url": item.get("link"), "keywords": item.get("keywords")}
            ))

        if alerts:
            self.dispatcher.dispatch_batch(alerts)

        return alerts

    def run_single_scan_cycle(self) -> List[AlertEvent]:
        """Runs both price gap and news scans in one synchronous pass."""
        gap_alerts = self.scan_price_gaps()
        news_alerts = self.scan_news_catalysts()
        return gap_alerts + news_alerts

    async def start_loop(self) -> None:
        """Asynchronous execution loop for the scanner worker."""
        self._running = True
        logger.info(f"ScannerWorker launched (polling cycle: {self.interval}s).")
        while self._running:
            try:
                self.run_single_scan_cycle()
            except Exception as e:
                logger.error(f"Error during scan cycle: {e}")
            await asyncio.sleep(self.interval)

    def stop(self) -> None:
        self._running = False
