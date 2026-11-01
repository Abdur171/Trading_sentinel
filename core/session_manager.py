"""
core/session_manager.py - Session Clock, Quiet Hours & Daily Briefings Manager.
Governs market sessions (Nordic, Stockholm Derivatives Curfew, US Equity, Weekend Dormant),
enforces Quiet Hours (22:30 - 07:30 CEST, muting P0/P1 while permitting P2 emergency alerts),
and generates structured Morning Briefings (08:30 CEST) and Evening Debriefs (22:15 CEST).
"""

from __future__ import annotations
import logging
from datetime import datetime, time, date, timezone
from typing import Dict, List, Optional, Any, Tuple
import pytz

from core.models import AlertEvent, AlertSeverity, Holding, MarketQuote
from core.state_manager import StateManager
from core.macro_sentinel import MacroSentinel
from feeds.base_feed import BaseFeed

logger = logging.getLogger(__name__)


class SessionManager:
    """
    Institutional Session Clock and Notification Gating Engine.
    Coordinates:
    - Nordic Session: 09:00 - 17:30 CEST (Mon-Fri)
    - Stockholm Derivatives Curfew Window: 17:30 - 19:45 CEST (Mon-Fri)
    - US Equity Session: 15:30 - 22:00 CEST (Mon-Fri)
    - Overnight Quiet Hours (DND): 22:30 - 07:30 CEST (Daily)
    - Weekend Dormant: Friday 22:00 - Monday 07:30 CEST
    - Scheduled Morning Briefing: 08:30 CEST
    - Scheduled Evening Debrief: 22:15 CEST
    """

    def __init__(
        self,
        timezone_str: str = "Europe/Stockholm",
        quiet_hours_start: str = "23:00",
        quiet_hours_end: str = "06:45",
        morning_briefing_time: str = "08:30",
        evening_debrief_time: str = "22:15"
    ):
        self.tz = pytz.timezone(timezone_str)
        self.qh_start = self._parse_time(quiet_hours_start)
        self.qh_end = self._parse_time(quiet_hours_end)
        self.briefing_time = self._parse_time(morning_briefing_time)
        self.debrief_time = self._parse_time(evening_debrief_time)
        self.last_briefing_date: Optional[date] = None
        self.last_debrief_date: Optional[date] = None

    def reset_briefing_tracker(self) -> None:
        """Resets briefing date tracking (for testing and new session cycles)."""
        self.last_briefing_date = None
        self.last_debrief_date = None

    def _parse_time(self, t_str: str) -> time:
        parts = t_str.split(":")
        return time(int(parts[0]), int(parts[1]), 0)

    def get_localized_time(self, current_dt: Optional[datetime] = None) -> datetime:
        """Returns timezone-aware datetime localized to Europe/Stockholm with DST safety."""
        if current_dt is None:
            now_utc = datetime.now(pytz.utc)
            return now_utc.astimezone(self.tz)
        if current_dt.tzinfo is None:
            try:
                return self.tz.localize(current_dt, is_dst=None)
            except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError):
                return self.tz.localize(current_dt, is_dst=False)
        return current_dt.astimezone(self.tz)

    def is_in_quiet_hours(self, current_dt: Optional[datetime] = None) -> bool:
        """
        Determines if the current time falls inside Overnight Quiet Hours (22:30 - 07:30 CEST).
        Handles midnight rollover correctly.
        """
        loc_now = self.get_localized_time(current_dt)
        curr_t = loc_now.time()

        # Crossing midnight: e.g. 22:30 to 07:30
        if self.qh_start > self.qh_end:
            return curr_t >= self.qh_start or curr_t < self.qh_end
        else:
            return self.qh_start <= curr_t < self.qh_end

    def should_suppress_alert(self, alert: AlertEvent, current_dt: Optional[datetime] = None) -> bool:
        """
        Gating filter for outbound notifications.
        During quiet hours (22:30 - 07:30 CEST):
        - Non-emergency P0 and P1 notifications are suppressed / muted.
        - P2 EMERGENCY alerts (e.g. hard stop breaches or derivative curfew) ALWAYS pass through.
        Outside quiet hours:
        - Nothing is suppressed by this filter.
        """
        if not self.is_in_quiet_hours(current_dt):
            return False

        # In quiet hours: Emergency P2 passes through!
        if alert.severity == AlertSeverity.P2:
            return False

        # P0 and P1 are suppressed
        return True

    def get_active_sessions(self, current_dt: Optional[datetime] = None) -> List[str]:
        """
        Returns list of active market session identifiers.
        """
        loc_now = self.get_localized_time(current_dt)
        weekday = loc_now.weekday()  # Monday is 0, Sunday is 6
        curr_t = loc_now.time()

        # Check Weekend
        if weekday == 4 and curr_t >= time(22, 0):  # Friday night after 22:00
            return ["WEEKEND_DORMANT"]
        if weekday in (5, 6):  # Saturday, Sunday
            return ["WEEKEND_DORMANT"]
        if weekday == 0 and curr_t < time(7, 30):  # Monday before 07:30
            return ["WEEKEND_DORMANT"]

        sessions: List[str] = []

        # Quiet Hours
        if self.is_in_quiet_hours(loc_now):
            sessions.append("OVERNIGHT_QUIET_HOURS")

        # Nordic Session: 09:00 - 17:30 CEST
        if time(9, 0) <= curr_t < time(17, 30):
            sessions.append("NORDIC_EQUITY")

        # Stockholm Derivatives Curfew Window: 17:30 - 19:45 CEST
        if time(17, 30) <= curr_t < time(19, 45):
            sessions.append("STOCKHOLM_CURFEW_WINDOW")

        # US Equity Session: 15:30 - 22:00 CEST
        if time(15, 30) <= curr_t < time(22, 0):
            sessions.append("US_EQUITY")

        if not sessions:
            sessions.append("INTER_SESSION_IDLE")

        return sessions

    def get_recommended_gear(
        self,
        current_dt: Optional[datetime] = None,
        has_watch_target: bool = False,
        in_curfew_with_derivatives: bool = False
    ) -> str:
        """
        Recommends surveillance transmission gear:
        - Gear 2 ('attack'): 15-30s cycle for focused watch targets or derivative curfew window.
        - Gear 1 ('cruise'): 60-90s cycle during active trading sessions.
        - Gear 0 ('dormant'): 15-30m cycle during quiet hours or weekend.
        """
        if has_watch_target or in_curfew_with_derivatives:
            return "attack"

        sessions = self.get_active_sessions(current_dt)
        if "WEEKEND_DORMANT" in sessions or "OVERNIGHT_QUIET_HOURS" in sessions:
            return "dormant"

        if any(s in sessions for s in ["NORDIC_EQUITY", "US_EQUITY", "STOCKHOLM_CURFEW_WINDOW"]):
            return "cruise"

        return "dormant"

    def check_scheduled_briefings(
        self,
        state_mgr: StateManager,
        feed: BaseFeed,
        macro_sentinel: Optional[MacroSentinel] = None,
        current_dt: Optional[datetime] = None
    ) -> List[AlertEvent]:
        """
        Audits clock for scheduled Morning Briefing (08:30 CEST) and Evening Debrief (22:15 CEST).
        Generates alert events exactly once per day per scheduled window on weekdays.
        """
        alerts: List[AlertEvent] = []
        loc_now = self.get_localized_time(current_dt)
        curr_d = loc_now.date()
        curr_t = loc_now.time()

        # Scheduled briefings dispatch on weekdays (Monday=0 to Friday=4)
        if loc_now.weekday() < 5:
            # 1. Morning Briefing: scheduled at 08:30 CEST (delivery window: 08:30 - 10:00)
            if curr_t >= self.briefing_time and curr_t < time(10, 0):
                if self.last_briefing_date != curr_d:
                    self.last_briefing_date = curr_d
                    logger.info(f"Dispatching scheduled morning briefing for {curr_d} (08:30 CEST)")
                    alerts.append(self.generate_morning_briefing(state_mgr, feed, macro_sentinel, current_dt=loc_now))

            # 2. Evening Debrief: scheduled at 22:15 CEST (delivery window: 22:15 - 23:59)
            if curr_t >= self.debrief_time and curr_t < time(23, 59):
                if self.last_debrief_date != curr_d:
                    self.last_debrief_date = curr_d
                    logger.info(f"Dispatching scheduled evening debrief for {curr_d} (22:15 CEST)")
                    alerts.append(self.generate_evening_debrief(state_mgr, feed, current_dt=loc_now))

        return alerts

    def generate_morning_briefing(
        self,
        state_mgr: StateManager,
        feed: BaseFeed,
        macro_sentinel: Optional[MacroSentinel] = None,
        current_dt: Optional[datetime] = None
    ) -> AlertEvent:
        """
        Generates the institutional Morning Briefing (08:30 CEST).
        Summarizes overnight macro shifts, active stop audits, catalysts, and day risk plan.
        """
        loc_now = self.get_localized_time(current_dt)
        recon = state_mgr.reconcile_capital()
        stops = state_mgr.get_active_stops()

        # Fetch quotes for stops
        symbols = [s["symbol"] for s in stops]
        quotes = feed.get_batch_quotes(symbols)

        # Macro state
        macro_lines = []
        if macro_sentinel:
            for sym in ["^TNX", "BZ=F", "GC=F"]:
                q = feed.get_quote(sym)
                if q:
                    m, _ = macro_sentinel.update_quote(sym, q.price)
                    macro_lines.append(f"• **{m.name}:** `{m.current_value:.2f} {m.unit}` ({m.status.replace('_', ' ')})")
        if not macro_lines:
            # Fallback direct quote check
            for sym, name in [("^TNX", "US 10Y"), ("BZ=F", "Brent"), ("GC=F", "Gold")]:
                q = feed.get_quote(sym)
                if q:
                    macro_lines.append(f"• **{name}:** `{q.price:.2f}`")

        # Stop loss buffers
        stop_lines = []
        for s in stops[:6]:
            sym = s["symbol"]
            q = quotes.get(sym)
            if not q and hasattr(feed, "normalize_symbol"):
                q = quotes.get(feed.normalize_symbol(sym))
            stop_pr = s.get("stop_price")
            curr_pr = f"{q.price:.2f}" if q else "N/A"
            buf_str = ""
            if q and stop_pr:
                buf = ((q.price - stop_pr) / q.price) * 100
                buf_str = f" [Buffer: {buf:+.1f}%]"
            stop_lines.append(f"• `{sym}` ({s['points']} pts): Stop `{stop_pr or s.get('percentage')}` | Px `{curr_pr}`{buf_str}")

        # Calendar events
        catalyst_lines = []
        for ev in state_mgr.state.time_sensitive_events:
            if getattr(ev, "status", "active") != "inactive" and ev.event_date != "daily":
                catalyst_lines.append(f"• **{ev.symbol}:** {ev.description} ({ev.event_date})")
        catalyst_str = "\n".join(catalyst_lines) if catalyst_lines else "None approaching within 7 days."

        lines = [
            f"🌅 **PRE-MARKET MORNING BRIEFING (08:30 CEST)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💼 **Mandate Capital Ledger:**",
            f"• Live NAV: `{recon['calculated_total_points']:.2f} pts` (Benchmark HWM: `{recon.get('benchmark_hwm', 696.50):.2f} pts`) | Parity: {'RECONCILED ✅' if recon['parity_reconciled'] else 'MISMATCH ⚠️'}",
            f"• Invested: `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%) | Cash: `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)",
            f"",
            f"🌐 **Overnight Macro Transmission:**",
            "\n".join(macro_lines) if macro_lines else "• Macro indicators stable.",
            f"",
            f"🛑 **Active Stop-Loss Audits:**",
            "\n".join(stop_lines) if stop_lines else "• No active stops.",
            f"",
            f"📅 **Upcoming Catalysts & Binary Prints:**",
            catalyst_str,
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"📋 *Desk Plan: Nordic session opens 09:00 CEST. Single-trade risk limit <= 3.44 pts.*"
        ]

        return AlertEvent(
            id=f"briefing_morning_{loc_now.strftime('%Y%m%d_%H%M%S')}",
            severity=AlertSeverity.P0,
            symbol="MANDATE",
            title=f"🌅 MORNING BRIEFING: {loc_now.strftime('%Y-%m-%d')} (08:30 CEST)",
            message="\n".join(lines),
            source="session_manager",
            metadata={"type": "morning_briefing"}
        )

    def generate_evening_debrief(
        self,
        state_mgr: StateManager,
        feed: BaseFeed,
        current_dt: Optional[datetime] = None
    ) -> AlertEvent:
        """
        Generates the institutional Evening Debrief (22:15 CEST).
        Summarizes daily NAV performance, top movers, cash verification, and next-day watchlist.
        """
        loc_now = self.get_localized_time(current_dt)
        recon = state_mgr.reconcile_capital()
        cash = state_mgr.get_cash_summary()
        holdings = state_mgr.get_all_active_holdings()

        # Batch quotes for movers
        symbols = [h.symbol for h in holdings]
        quotes = feed.get_batch_quotes(symbols)

        # Track movers
        movers = []
        for h in holdings:
            q = quotes.get(h.symbol)
            if not q and hasattr(feed, "normalize_symbol"):
                q = quotes.get(feed.normalize_symbol(h.symbol))
            if q:
                movers.append((h.symbol, q.change_pct, q.price, h.points))

        movers.sort(key=lambda x: x[1], reverse=True)
        top_gainers = [m for m in movers if m[1] > 0][:3]
        top_losers = sorted([m for m in movers if m[1] < 0], key=lambda x: x[1])[:3]

        movers_lines = []
        if top_gainers:
            movers_lines.append("🟢 **Leaders:** " + ", ".join([f"`{m[0]}` ({m[1]:+.2f}%)" for m in top_gainers]))
        if top_losers:
            movers_lines.append("🔴 **Laggards:** " + ", ".join([f"`{m[0]}` ({m[1]:+.2f}%)" for m in top_losers]))
        if not movers_lines:
            movers_lines.append("• Holdings quotes neutral.")

        # Cash allocation
        cash_lines = []
        for acc_id, d in cash.items():
            cash_lines.append(f"• {d['name']}: `{d['points']:.2f} pts` ({d['currency']})")

        # Resting orders
        ro_lines = []
        for ro in state_mgr.state.resting_orders:
            ro_lines.append(f"• `{ro.symbol}`: {ro.points} pts @ {ro.target_price} ({ro.order_type})")
        ro_str = "\n".join(ro_lines) if ro_lines else "None resting."

        lines = [
            f"🌙 **POST-MARKET EVENING DEBRIEF (22:15 CEST)**",
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"💼 **Mandate Capital Ledger & Parity Audit:**",
            f"• Target NAV: `{recon['calculated_total_points']:.2f} pts` (Benchmark HWM: `{recon.get('benchmark_hwm', 696.50):.2f} pts`) | Calculated: `{recon['calculated_total_points']:.2f} pts` {'✅' if recon['parity_reconciled'] else '⚠️'}",
            f"• Invested: `{recon['invested_points']:.2f} pts` ({recon['invested_pct']}%) | Liquid Cash: `{recon['cash_points']:.2f} pts` ({recon['cash_pct']}%)",
            f"",
            f"📊 **Market Movers Today:**",
            "\n".join(movers_lines),
            f"",
            f"💵 **Cash Partitioning Verification:**",
            "\n".join(cash_lines),
            f"",
            f"🎯 **Active Resting Orders & Next-Day Pipeline:**",
            ro_str,
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"🌙 *Quiet hours begin at 23:00 CEST. All non-emergency alerts muted until 06:45 CEST.*"
        ]

        return AlertEvent(
            id=f"debrief_evening_{loc_now.strftime('%Y%m%d_%H%M%S')}",
            severity=AlertSeverity.P0,
            symbol="MANDATE",
            title=f"🌙 EVENING DEBRIEF: {loc_now.strftime('%Y-%m-%d')} (22:15 CEST)",
            message="\n".join(lines),
            source="session_manager",
            metadata={"type": "evening_debrief"}
        )
