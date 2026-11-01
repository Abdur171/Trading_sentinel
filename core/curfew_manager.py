"""
core/curfew_manager.py - Generalized High Time-Sensitivity & Derivative Curfew Watchdog.
Monitors all derivative instruments, warrants, options, and calendar-sensitive binary events
(such as earnings prints and catalyst dates per Pre-Flight Protocol 4).
"""

from __future__ import annotations
import logging
from datetime import datetime, date, time
from typing import Optional, List, Dict, Any
import pytz

from core.models import Holding, AlertEvent, AlertSeverity, TimeSensitiveEvent

logger = logging.getLogger(__name__)


class CurfewManager:
    """
    Generalized Time-Sensitivity & Derivative Curfew Watchdog.
    - Inspects any holding classified as derivative, warrant, option, or time-sensitive.
    - Enforces intraday curfews (such as 19:45 CEST for Stockholm derivatives) with escalating warnings:
        * 19:30 CEST: Advisory (P0)
        * 19:40 CEST: High Warning (P1)
        * 19:45 CEST: Emergency Flat-Book Breach (P2)
    - Monitors calendar events (earnings prints, option expirations, binary regulatory catalysts).
    """

    DERIVATIVE_TYPES = {"derivative_warrant", "derivative", "warrant", "option", "future"}

    def __init__(
        self,
        timezone_str: str = "Europe/Stockholm",
        default_cutoff_time: str = "19:45",
        default_warning_1: str = "19:30",
        default_warning_2: str = "19:40"
    ):
        self.tz = pytz.timezone(timezone_str)
        self.default_cutoff = self._parse_time_str(default_cutoff_time)
        self.default_w1 = self._parse_time_str(default_warning_1)
        self.default_w2 = self._parse_time_str(default_warning_2)

    def _parse_time_str(self, t_str: str) -> time:
        parts = t_str.split(":")
        return time(int(parts[0]), int(parts[1]), 0)

    def get_localized_time(self, current_dt: Optional[datetime] = None) -> datetime:
        """Returns localized current datetime."""
        if current_dt is None:
            now_utc = datetime.now(pytz.utc)
            return now_utc.astimezone(self.tz)
        if current_dt.tzinfo is None:
            return self.tz.localize(current_dt)
        return current_dt.astimezone(self.tz)

    def is_derivative_or_time_sensitive(self, holding: Holding) -> bool:
        """Determines if a holding is subject to time-sensitivity or derivative curfew rules."""
        if holding.instrument_type and holding.instrument_type.lower() in self.DERIVATIVE_TYPES:
            return True
        if holding.curfew and holding.curfew.enforced:
            return True
        if holding.time_sensitive:
            return True
        if "MINI" in holding.symbol.upper() or "WARRANT" in holding.classification.lower():
            return True
        return False

    def check_derivative_curfew(
        self,
        holding: Holding,
        current_dt: Optional[datetime] = None
    ) -> Optional[AlertEvent]:
        """
        Audits an individual holding against intraday curfew constraints.
        Applies to ANY derivative, warrant, or time-sensitive instrument in the portfolio.
        """
        # If position is flat or closed, no curfew breach
        if holding.points <= 0 or holding.status.lower() in ["flat", "closed", "liquidated"]:
            return None

        if not self.is_derivative_or_time_sensitive(holding):
            return None

        # Determine cutoff times from holding config or defaults
        cutoff = self.default_cutoff
        w1 = self.default_w1
        w2 = self.default_w2

        if holding.curfew:
            if holding.curfew.cutoff_time:
                cutoff = self._parse_time_str(holding.curfew.cutoff_time)
            if holding.curfew.warning_1:
                w1 = self._parse_time_str(holding.curfew.warning_1)
            if holding.curfew.warning_2:
                w2 = self._parse_time_str(holding.curfew.warning_2)

        loc_now = self.get_localized_time(current_dt)
        # Curfew constraints apply on trading days (Monday-Friday)
        if loc_now.weekday() >= 5:
            return None

        curr_t = loc_now.time()

        # Before warning window (e.g. before 19:30) or after overnight post-session (21:00)
        if curr_t < w1 or curr_t >= time(21, 0):
            return None

        # 1. Emergency Curfew Breach (at or after cutoff)
        if curr_t >= cutoff:
            return AlertEvent(
                id=f"curfew_emergency_{holding.symbol}_{loc_now.strftime('%Y%m%d_%H%M%S')}",
                severity=AlertSeverity.P2,
                symbol=holding.symbol,
                title=f"🚨 EMERGENCY: DERIVATIVE CURFEW BREACH ({cutoff.strftime('%H:%M')} CEST)",
                message=(
                    f"MANDATORY FLAT-BOOK PROTOCOL VIOLATION!\n"
                    f"Derivative instrument {holding.name} ({holding.symbol}) remains OPEN ({holding.points:.2f} pts) "
                    f"past the {cutoff.strftime('%H:%M')} CEST cutoff time in {holding.account}.\n"
                    f"Current Time: {loc_now.strftime('%H:%M:%S CEST')}.\n"
                    f"DIRECTIVE: Close out position IMMEDIATELY to prevent overnight gap risk!"
                ),
                source="curfew_manager",
                metadata={
                    "localized_time": loc_now.isoformat(),
                    "points": holding.points,
                    "cutoff": cutoff.strftime('%H:%M'),
                    "severity": "EMERGENCY_P2"
                }
            )

        # 2. Warning 2 (near cutoff)
        if curr_t >= w2:
            minutes_left = (cutoff.hour * 60 + cutoff.minute) - (curr_t.hour * 60 + curr_t.minute)
            return AlertEvent(
                id=f"curfew_warning_{holding.symbol}_{loc_now.strftime('%Y%m%d_%H%M%S')}",
                severity=AlertSeverity.P1,
                symbol=holding.symbol,
                title=f"⚠️ URGENT: CURFEW CLOSING IN ({minutes_left} MIN REMAINING)",
                message=(
                    f"Derivative position {holding.name} ({holding.symbol}) is OPEN ({holding.points:.2f} pts).\n"
                    f"Only {minutes_left} minutes until {cutoff.strftime('%H:%M')} CEST mandatory cutoff!\n"
                    f"Current Time: {loc_now.strftime('%H:%M:%S CEST')}.\n"
                    f"Action: Prepare exit order in {holding.account}."
                ),
                source="curfew_manager",
                metadata={"points": holding.points, "minutes_left": minutes_left}
            )

        # 3. Advisory (Warning 1)
        if curr_t >= w1:
            minutes_left = (cutoff.hour * 60 + cutoff.minute) - (curr_t.hour * 60 + curr_t.minute)
            return AlertEvent(
                id=f"curfew_advisory_{holding.symbol}_{loc_now.strftime('%Y%m%d_%H%M%S')}",
                severity=AlertSeverity.P0,
                symbol=holding.symbol,
                title=f"⏰ CURFEW ADVISORY: {holding.symbol} ({minutes_left} MIN REMAINING)",
                message=(
                    f"Time-sensitive asset {holding.name} ({holding.symbol}) holds {holding.points:.2f} pts.\n"
                    f"{minutes_left} minutes remaining until {cutoff.strftime('%H:%M')} CEST mandatory flat book.\n"
                    f"Current Time: {loc_now.strftime('%H:%M:%S CEST')}."
                ),
                source="curfew_manager",
                metadata={"points": holding.points, "minutes_left": minutes_left}
            )

        return None

    def check_calendar_event(
        self,
        event: TimeSensitiveEvent,
        current_dt: Optional[datetime] = None
    ) -> Optional[AlertEvent]:
        """
        Monitors calendar events such as earnings dates (e.g. Sinch Nov 5) or binary catalysts.
        Enforces Pre-Flight Protocol 4 (Earnings Event Protocol).
        """
        if event.event_date.lower() == "daily":
            return None

        loc_now = self.get_localized_time(current_dt)
        today = loc_now.date()

        try:
            target_date = datetime.strptime(event.event_date, "%Y-%m-%d").date()
        except ValueError:
            return None

        days_diff = (target_date - today).days

        if 0 <= days_diff <= event.days_warning_threshold:
            severity = AlertSeverity.P1 if days_diff <= 2 else AlertSeverity.P0
            return AlertEvent(
                id=f"event_{event.symbol}_{event.event_type}_{target_date.isoformat()}",
                severity=severity,
                symbol=event.symbol,
                title=f"📅 TIME-SENSITIVE EVENT APPROACHING: {event.symbol} ({days_diff}d)",
                message=(
                    f"Calendar Event: {event.description} on {event.event_date} ({days_diff} days away).\n"
                    f"Directive: {event.directive or 'Audit position exposure against Pre-Flight Protocol 4.'}"
                ),
                source="curfew_manager",
                metadata={"days_left": days_diff, "event_type": event.event_type, "date": event.event_date}
            )

        return None

    def audit_all_time_sensitivities(
        self,
        holdings: List[Holding],
        events: List[TimeSensitiveEvent],
        current_dt: Optional[datetime] = None
    ) -> List[AlertEvent]:
        """Comprehensive audit across all holdings and calendar events."""
        alerts: List[AlertEvent] = []

        # 1. Curfew checks on holdings
        for h in holdings:
            a = self.check_derivative_curfew(h, current_dt=current_dt)
            if a:
                alerts.append(a)

        # 2. Calendar events checks
        for ev in events:
            a = self.check_calendar_event(ev, current_dt=current_dt)
            if a:
                alerts.append(a)

        return alerts
