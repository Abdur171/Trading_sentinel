"""
core/watch_manager.py - Focused Watch Mode & Dynamic Surveillance Manager.
Tracks temporary high-frequency surveillance states requested by user (/focus <SYM> or "Watch X closely").
Evaluates +-1.5% deviations from baseline price and manages auto-resets on TTL (default 60m), market close, or /unfocus.
"""

from __future__ import annotations
import logging
from datetime import datetime, timezone, time
from typing import Dict, List, Optional, Any
import pytz
from pydantic import BaseModel, Field

from core.models import AlertEvent, AlertSeverity

logger = logging.getLogger(__name__)


class WatchTarget(BaseModel):
    """Configuration and state for an active watch target."""
    symbol: str
    baseline_price: float
    start_time: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_minutes: int = 60
    alert_threshold_pct: float = 0.015  # 1.5%
    reason: Optional[str] = None
    last_alert_price: Optional[float] = None
    status: str = "active"  # "active", "expired", "stopped", "market_closed"
    exchange_close_time: str = "22:00"  # CEST close time: 17:30 for Nordic, 22:00 for US


class WatchManager:
    """
    Manages Focused Watch Mode:
    - Tracks targeted assets under accelerated surveillance (15-30s cycle).
    - Detects price deviations >= +-1.5% from established baseline.
    - Automatically resets to normal cruise mode upon:
        1. TTL expiration (default 60 minutes).
        2. Asset exchange session close (17:30 CEST Nordic, 22:00 CEST US).
        3. User cancellation (/unfocus or "stop watching").
    - Dispatches reset notifications when watch mode terminates.
    """

    NORDIC_IDENTIFIERS = [
        ".ST", "LINJE", "OMXS", "INVE", "ATCO", "SAAB", "MCAP", "SINCH", "EQT", "LUN", "LUMI", "EVO"
    ]

    def __init__(self, timezone_str: str = "Europe/Stockholm"):
        self.tz = pytz.timezone(timezone_str)
        self.targets: Dict[str, WatchTarget] = {}

    def _determine_exchange_close(self, symbol: str) -> str:
        sym_upper = symbol.upper()
        if any(nord in sym_upper for nord in self.NORDIC_IDENTIFIERS):
            return "17:30"
        return "22:00"

    def _get_localized_time(self, dt: Optional[datetime] = None) -> datetime:
        if dt is None:
            now_utc = datetime.now(pytz.utc)
            return now_utc.astimezone(self.tz)
        if dt.tzinfo is None:
            try:
                return self.tz.localize(dt, is_dst=None)
            except (pytz.AmbiguousTimeError, pytz.NonExistentTimeError):
                return self.tz.localize(dt, is_dst=False)
        return dt.astimezone(self.tz)

    def start_watch(
        self,
        symbol: str,
        baseline_price: float,
        duration_minutes: int = 60,
        alert_threshold_pct: float = 0.015,
        reason: Optional[str] = None,
        start_time: Optional[datetime] = None
    ) -> WatchTarget:
        """
        Activates focused watch mode for a specific symbol.
        """
        sym = symbol.strip().upper()
        st = start_time or datetime.now(timezone.utc)
        close_time = self._determine_exchange_close(sym)

        target = WatchTarget(
            symbol=sym,
            baseline_price=baseline_price,
            start_time=st,
            duration_minutes=duration_minutes,
            alert_threshold_pct=alert_threshold_pct,
            reason=reason or f"User requested focused surveillance ({duration_minutes}m)",
            exchange_close_time=close_time,
            status="active"
        )
        self.targets[sym] = target
        logger.info(f"Focused watch started for {sym} @ {baseline_price:.2f} (TTL: {duration_minutes}m, threshold: {alert_threshold_pct*100:.1f}%)")
        return target

    def stop_watch(
        self,
        symbol: Optional[str] = None,
        current_dt: Optional[datetime] = None
    ) -> List[AlertEvent]:
        """
        Manually cancels watch mode for a symbol or all active targets.
        Generates notification alert(s) upon reset.
        """
        alerts: List[AlertEvent] = []
        loc_now = self._get_localized_time(current_dt)

        to_stop = []
        if symbol:
            sym_clean = symbol.strip().upper()
            sym_norm = sym_clean.replace("-B.ST", " B").replace(".ST", "")
            found = False
            for k, t in self.targets.items():
                k_norm = k.replace("-B.ST", " B").replace(".ST", "")
                if t.status == "active" and (k == sym_clean or k_norm == sym_norm):
                    to_stop.append(t)
                    found = True
        else:
            to_stop = [t for t in self.targets.values() if t.status == "active"]

        for target in to_stop:
            target.status = "stopped"
            alerts.append(self._create_reset_alert(target, "User cancellation (/unfocus)", loc_now))
            logger.info(f"Focused watch stopped manually for {target.symbol}")

        return alerts

    def is_watching(self, symbol: str) -> bool:
        """Checks if a symbol is currently under active focused watch."""
        sym_clean = symbol.strip().upper()
        if sym_clean in self.targets and self.targets[sym_clean].status == "active":
            return True
        sym_norm = sym_clean.replace("-B.ST", " B").replace(".ST", "")
        for k, t in self.targets.items():
            if t.status == "active":
                if k.replace("-B.ST", " B").replace(".ST", "") == sym_norm:
                    return True
        return False

    def get_active_targets(self) -> List[WatchTarget]:
        """Returns all currently active watch targets."""
        return [t for t in self.targets.values() if t.status == "active"]

    def get_target(self, symbol: str) -> Optional[WatchTarget]:
        """Returns watch target for a symbol if present."""
        sym_clean = symbol.strip().upper()
        if sym_clean in self.targets:
            return self.targets[sym_clean]
        sym_norm = sym_clean.replace("-B.ST", " B").replace(".ST", "")
        for k, t in self.targets.items():
            if k.replace("-B.ST", " B").replace(".ST", "") == sym_norm:
                return t
        return None

    def evaluate_quote(
        self,
        symbol: str,
        current_price: float,
        current_dt: Optional[datetime] = None
    ) -> List[AlertEvent]:
        """
        Evaluates a live quote against the active watch baseline.
        Checks for:
        1. TTL or Market Close expirations.
        2. Deviation >= alert_threshold_pct from baseline price.
        """
        sym_clean = symbol.strip().upper()
        target = self.targets.get(sym_clean)
        if not target or target.status != "active":
            return []

        alerts: List[AlertEvent] = []
        loc_now = self._get_localized_time(current_dt)

        # 1. Check expiration first
        exp_alert = self._check_single_target_expiration(target, loc_now)
        if exp_alert:
            alerts.append(exp_alert)
            return alerts

        # 2. Check price deviation
        if target.baseline_price <= 0:
            return alerts

        deviation = (current_price - target.baseline_price) / target.baseline_price
        abs_dev = abs(deviation)

        if abs_dev >= target.alert_threshold_pct:
            # Prevent spamming identical alerts on the same price move
            should_alert = True
            if target.last_alert_price is not None:
                change_since_last = abs(current_price - target.last_alert_price) / target.baseline_price
                if change_since_last < 0.005:  # Require at least another 0.5% move before re-alerting
                    should_alert = False

            if should_alert:
                target.last_alert_price = current_price
                direction = "SURGE" if deviation > 0 else "DROP"
                icon = "🟢" if deviation > 0 else "🔴"
                alerts.append(
                    AlertEvent(
                        id=f"watch_delta_{target.symbol}_{loc_now.strftime('%Y%m%d_%H%M%S')}",
                        severity=AlertSeverity.P1,
                        symbol=target.symbol,
                        title=f"{icon} FOCUSED WATCH {direction}: {target.symbol} ({deviation*100:+.2f}%)",
                        message=(
                            f"Focused surveillance target {target.symbol} has deviated {deviation*100:+.2f}% from baseline.\n"
                            f"• Baseline Price: `{target.baseline_price:.2f}`\n"
                            f"• Current Price: `{current_price:.2f}`\n"
                            f"• Reason: {target.reason}\n"
                            f"• Time: `{loc_now.strftime('%H:%M:%S CEST')}`\n"
                            f"Action: Review order placement or trailing stop."
                        ),
                        source="watch_manager",
                        metadata={
                            "baseline_price": target.baseline_price,
                            "current_price": current_price,
                            "deviation_pct": deviation * 100,
                            "symbol": target.symbol
                        }
                    )
                )

        return alerts

    def check_expirations(self, current_dt: Optional[datetime] = None) -> List[AlertEvent]:
        """
        Audits all active targets for TTL expiration and market close.
        Returns reset notifications for all expired targets.
        """
        alerts: List[AlertEvent] = []
        loc_now = self._get_localized_time(current_dt)

        for target in list(self.targets.values()):
            if target.status != "active":
                continue
            exp_alert = self._check_single_target_expiration(target, loc_now)
            if exp_alert:
                alerts.append(exp_alert)

        return alerts

    def _check_single_target_expiration(self, target: WatchTarget, loc_now: datetime) -> Optional[AlertEvent]:
        target_start = self._get_localized_time(target.start_time)
        elapsed_minutes = (loc_now - target_start).total_seconds() / 60.0

        close_parts = [int(p) for p in target.exchange_close_time.split(":")]
        close_t = time(close_parts[0], close_parts[1], 0)

        is_weekend = loc_now.weekday() >= 5
        curr_t = loc_now.time()
        has_day_rolled_over = loc_now.date() > target_start.date()

        # A. Check Market Close Reset (Session ended or rolled over overnight)
        if has_day_rolled_over or is_weekend or (curr_t >= close_t and target_start.time() < close_t):
            target.status = "market_closed"
            reason = "Market close reached" if not is_weekend else "Weekend market closure"
            logger.info(f"Watch mode reset for {target.symbol}: {reason} ({target.exchange_close_time} CEST)")
            return self._create_reset_alert(target, f"{reason} ({target.exchange_close_time} CEST)", loc_now)

        # B. Check TTL Expiry
        if elapsed_minutes >= target.duration_minutes:
            target.status = "expired"
            logger.info(f"Watch mode expired for {target.symbol} after {elapsed_minutes:.1f}m (TTL: {target.duration_minutes}m)")
            return self._create_reset_alert(target, f"TTL expired ({target.duration_minutes}m duration completed)", loc_now)

        return None

    def _create_reset_alert(self, target: WatchTarget, reason: str, loc_now: datetime) -> AlertEvent:
        return AlertEvent(
            id=f"watch_reset_{target.symbol}_{loc_now.strftime('%Y%m%d_%H%M%S')}",
            severity=AlertSeverity.P0,
            symbol=target.symbol,
            title=f"🔄 WATCH MODE AUTO-RESET: {target.symbol}",
            message=(
                f"Focused watch mode for **{target.symbol}** has concluded.\n"
                f"• Reset Reason: {reason}\n"
                f"• Baseline Price: `{target.baseline_price:.2f}`\n"
                f"• Monitored Duration: `{target.duration_minutes} minutes`\n"
                f"• Cadence: Normal cruise mode restored."
            ),
            source="watch_manager",
            metadata={
                "symbol": target.symbol,
                "reset_reason": reason,
                "status": target.status
            }
        )
