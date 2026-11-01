"""
dispatchers/composite_dispatcher.py - Alert Deduplication, 12-Hour Stationary Suppression & Multi-Tier Routing Manager.
Enforces quiet hours, a 12-hour suppression window for stationary alerts (Macro Shield, Stop Losses, Digests),
interactive /snooze mutes with auto-reset, and tight 3-minute repeats only for urgent curfew violations.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Callable, Optional, Any, Tuple
from core.models import AlertEvent, AlertSeverity

logger = logging.getLogger(__name__)


class CompositeDispatcher:
    """
    Coordinates outbound alert delivery across multiple channels (Telegram, Pushover, Console).
    Maintains:
    - 12-Hour stationary suppression window (default 43,200s) for persistent macro & stop alerts.
    - Interactive snooze store (/snooze /mute) with 12-hour auto-reset.
    - Quiet hours gating (22:30 - 07:30 CEST).
    - Emergency fast repeat (180s) strictly for urgent derivative curfew breaches.
    """

    def __init__(
        self,
        cooldown_minutes: int = 15,
        stationary_suppression_hours: float = 12.0,
        session_manager: Optional[Any] = None
    ):
        self.cooldown_seconds: float = cooldown_minutes * 60.0
        self.stationary_suppression_seconds: float = stationary_suppression_hours * 3600.0
        self.last_dispatched: Dict[str, datetime] = {}
        self.last_alerted_prices: Dict[str, float] = {}
        self.snoozed_targets: Dict[str, datetime] = {}
        self.handlers: List[Callable[[AlertEvent], None]] = []
        self.alert_history: List[AlertEvent] = []
        self.session_manager = session_manager
        self.quiet_hours_enabled: bool = True

    def set_session_manager(self, session_manager: Any) -> None:
        """Injects SessionManager for quiet hours gating."""
        self.session_manager = session_manager

    def register_handler(self, handler: Callable[[AlertEvent], None]) -> None:
        """Registers an outbound notification sink (e.g. Telegram client)."""
        self.handlers.append(handler)

    def generate_dedup_key(self, alert: AlertEvent) -> str:
        """Creates a stable deduplication key for an alert event."""
        sym = alert.symbol or "GLOBAL"
        title_core = alert.title.split(":")[0].strip()
        return f"{sym}_{alert.severity}_{title_core}"

    def snooze(self, target: str, hours: float = 12.0, current_dt: Optional[datetime] = None) -> datetime:
        """
        Snoozes alerts matching target (ALL, MACRO, symbol, or title keyword) for specified hours.
        Defaults to 12.0 hours per user mandate.
        """
        target_norm = target.strip().upper()
        now_utc = self._get_utc_now(current_dt)
        expire_at = now_utc + timedelta(hours=hours)
        self.snoozed_targets[target_norm] = expire_at
        logger.info(f"Snoozed target '{target_norm}' for {hours:.1f}h until {expire_at.isoformat()}")
        return expire_at

    def unsnooze(self, target: str) -> bool:
        """Cancels an active snooze for a target. If target is 'ALL', clears all active snoozes."""
        target_norm = target.strip().upper()
        if target_norm == "ALL":
            if self.snoozed_targets:
                self.snoozed_targets.clear()
                logger.info("Unsnoozed ALL targets")
                return True
            return False

        if target_norm in self.snoozed_targets:
            del self.snoozed_targets[target_norm]
            logger.info(f"Unsnoozed target '{target_norm}'")
            return True
        return False

    def clear_snoozes(self) -> None:
        """Clears all active snoozes."""
        self.snoozed_targets.clear()

    def get_active_snoozes(self, current_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Returns all currently active snoozes, pruning expired entries."""
        now_utc = self._get_utc_now(current_dt)
        expired = [k for k, v in self.snoozed_targets.items() if v <= now_utc]
        for k in expired:
            del self.snoozed_targets[k]

        active = []
        for k, exp in self.snoozed_targets.items():
            rem_secs = max(0.0, (exp - now_utc).total_seconds())
            active.append({
                "target": k,
                "expires_at": exp,
                "remaining_hours": round(rem_secs / 3600.0, 1),
                "remaining_minutes": int(round(rem_secs / 60.0))
            })
        return active

    def is_snoozed(self, alert: AlertEvent, current_dt: Optional[datetime] = None) -> Tuple[bool, Optional[str]]:
        """
        Checks if an alert is silenced by an active snooze.
        Automatically cleans up snoozes after 12 hours (expiry).
        """
        now_utc = self._get_utc_now(current_dt)
        expired = [k for k, v in self.snoozed_targets.items() if v <= now_utc]
        for k in expired:
            del self.snoozed_targets[k]

        if not self.snoozed_targets:
            return False, None

        if "ALL" in self.snoozed_targets:
            return True, "ALL"

        sym = (alert.symbol or "").upper()
        if sym and sym in self.snoozed_targets:
            return True, sym

        title_upper = alert.title.upper()
        for target in self.snoozed_targets:
            if target in title_upper or (alert.source and target == alert.source.upper()):
                return True, target

        return False, None

    def _get_utc_now(self, dt: Optional[datetime] = None) -> datetime:
        """Returns timezone-aware UTC datetime."""
        if dt is None:
            return datetime.now(timezone.utc)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def should_suppress(self, alert: AlertEvent, current_dt: Optional[datetime] = None) -> bool:
        """
        Determines whether an alert should be suppressed:
        1. Interactive Snooze / Mute check (auto-resets after 12h).
        2. Quiet Hours (22:30 - 07:30 CEST): Mutes P0/P1 alerts; emergency P2 alerts pass through!
        3. Deduplication window:
           - Urgent derivative curfew breach: 3-minute repeat window.
           - Stationary conditions (Macro Shield, un-worsened Stop Losses, Digests): 12-hour window.
           - Transient P0/P1 alerts: standard 15-minute cooldown.
        """
        # 1. Snooze Check
        is_snz, snz_key = self.is_snoozed(alert, current_dt=current_dt)
        if is_snz:
            logger.info(f"Suppressed snoozed alert: {alert.title} (Muted under '{snz_key}')")
            return True

        # 2. Quiet Hours Gating
        if self.quiet_hours_enabled and self.session_manager is not None:
            if self.session_manager.should_suppress_alert(alert, current_dt=current_dt):
                logger.info(f"Suppressed alert during quiet hours: {alert.title} (Severity: {alert.severity})")
                return True

        # 3. Deduplication Cooldown
        key = self.generate_dedup_key(alert)
        last_time = self.last_dispatched.get(key)
        if not last_time:
            return False

        now_utc = self._get_utc_now(current_dt)
        last_utc = self._get_utc_now(last_time)
        elapsed = (now_utc - last_utc).total_seconds()

        # Urgent curfew breaches: require fast 3-minute repeats to prompt broker action
        is_urgent_curfew = (alert.severity == AlertSeverity.P2 and "CURFEW" in alert.title.upper())
        if is_urgent_curfew:
            return elapsed < 180.0

        # Stationary conditions: 12-hour suppression window
        is_macro_shield = ("MACRO DEFENSIVE SHIELD" in alert.title.upper() or alert.symbol == "MACRO")
        is_stop_breach = ("STOP LOSS BREACH" in alert.title.upper() or "CRITICAL STOP HIT" in alert.message)
        is_digest_or_gap = (
            "MARKET GAP" in alert.title.upper() or
            "CATALYST" in alert.title.upper() or
            "DIGEST" in alert.title.upper()
        )

        if is_stop_breach and alert.symbol:
            curr_px = alert.metadata.get("current_price")
            last_px = self.last_alerted_prices.get(alert.symbol)
            if curr_px is not None and last_px is not None and last_px > 0:
                # If price deteriorated by >= 2.0% below last alerted price, allow through!
                price_drop = (last_px - curr_px) / last_px
                if price_drop >= 0.02:
                    return False

        if is_macro_shield or is_stop_breach or is_digest_or_gap:
            return elapsed < self.stationary_suppression_seconds

        # P2 non-stationary emergency alerts: 3-minute cooldown
        if alert.severity == AlertSeverity.P2:
            return elapsed < 180.0

        # P0 and P1 standard cooldown
        return elapsed < self.cooldown_seconds

    def dispatch(self, alert: AlertEvent, current_dt: Optional[datetime] = None) -> bool:
        """
        Dispatches an alert to all registered handlers if not suppressed.
        Returns True if dispatched, False if suppressed.
        """
        if self.should_suppress(alert, current_dt=current_dt):
            logger.info(f"Suppressed repeating alert: {alert.title} (within cooldown or quiet hours)")
            return False

        key = self.generate_dedup_key(alert)
        now_utc = self._get_utc_now(current_dt)
        self.last_dispatched[key] = now_utc

        if alert.symbol and alert.metadata.get("current_price") is not None:
            try:
                self.last_alerted_prices[alert.symbol] = float(alert.metadata["current_price"])
            except (ValueError, TypeError):
                pass

        self.alert_history.append(alert)
        logger.info(f"[{alert.severity}] Dispatching: {alert.title}")

        for handler in self.handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error(f"Error in alert handler {handler}: {e}")

        return True

    def dispatch_batch(self, alerts: List[AlertEvent], current_dt: Optional[datetime] = None) -> int:
        """
        Dispatches a collection of alerts.
        Aggregates multiple market gaps or news items into consolidated digest cards.
        Emergency P2 alerts are always dispatched individually.
        """
        if not alerts:
            return 0

        p2_alerts = [a for a in alerts if a.severity == AlertSeverity.P2]
        other_alerts = [a for a in alerts if a.severity != AlertSeverity.P2]

        dispatched_count = 0

        # 1. P2 Emergency alerts always dispatch individually
        for alert in p2_alerts:
            if self.dispatch(alert, current_dt=current_dt):
                dispatched_count += 1

        # 2. Non-P2 alerts
        if len(other_alerts) <= 2:
            for alert in other_alerts:
                if self.dispatch(alert, current_dt=current_dt):
                    dispatched_count += 1
        else:
            gap_alerts = [a for a in other_alerts if "MARKET GAP" in a.title]
            news_alerts = [a for a in other_alerts if "CATALYST DETECTED" in a.title]
            rest_alerts = [a for a in other_alerts if a not in gap_alerts and a not in news_alerts]

            if gap_alerts:
                if len(gap_alerts) >= 2:
                    digest = self._build_gap_digest(gap_alerts)
                    if self.dispatch(digest, current_dt=current_dt):
                        dispatched_count += 1
                else:
                    for a in gap_alerts:
                        if self.dispatch(a, current_dt=current_dt):
                            dispatched_count += 1

            if news_alerts:
                if len(news_alerts) >= 2:
                    digest = self._build_news_digest(news_alerts)
                    if self.dispatch(digest, current_dt=current_dt):
                        dispatched_count += 1
                else:
                    for a in news_alerts:
                        if self.dispatch(a, current_dt=current_dt):
                            dispatched_count += 1

            for a in rest_alerts:
                if self.dispatch(a, current_dt=current_dt):
                    dispatched_count += 1

        return dispatched_count

    def _build_gap_digest(self, gap_alerts: List[AlertEvent]) -> AlertEvent:
        """Aggregates multiple individual market gap alerts into a single digest card."""
        plunges = []
        surges = []
        for a in gap_alerts:
            chg = a.metadata.get("change_pct", 0.0)
            px = a.metadata.get("price", 0.0)
            sym = a.symbol or "UNKNOWN"
            if chg < 0:
                plunges.append((sym, chg, px))
            else:
                surges.append((sym, chg, px))

        plunges.sort(key=lambda x: x[1])
        surges.sort(key=lambda x: x[1], reverse=True)

        lines = [f"📊 **MARKET GAP & DISLOCATION DIGEST ({len(gap_alerts)} Events)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        if plunges:
            lines.append(f"🔴 **Significant Plunges ({len(plunges)}):**")
            for sym, chg, px in plunges:
                lines.append(f"• `{sym}`: **{chg:+.2f}%** ({px:.2f})")
        if surges:
            if plunges:
                lines.append("")
            lines.append(f"🟢 **Significant Surges ({len(surges)}):**")
            for sym, chg, px in surges:
                lines.append(f"• `{sym}`: **{chg:+.2f}%** ({px:.2f})")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append("📋 *Consolidated scan digest. Review watchlist limit bands.*")

        severity = AlertSeverity.P1 if any(a.severity == AlertSeverity.P1 for a in gap_alerts) else AlertSeverity.P0
        return AlertEvent(
            id=f"digest_gap_{datetime.now(timezone.utc).timestamp()}",
            severity=severity,
            symbol="MARKET",
            title=f"📊 MARKET GAP DIGEST: {len(plunges)} Plunges, {len(surges)} Surges",
            message="\n".join(lines),
            source="composite_dispatcher",
            metadata={"plunges_count": len(plunges), "surges_count": len(surges)}
        )

    def _build_news_digest(self, news_alerts: List[AlertEvent]) -> AlertEvent:
        """Aggregates multiple news catalyst alerts into a single digest card."""
        lines = [f"📰 **MARKET CATALYSTS DIGEST ({len(news_alerts)} Items)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"]
        for a in news_alerts:
            sym = a.symbol or "MARKET"
            keywords = a.metadata.get("keywords", [])
            kw_str = f" [{', '.join(keywords)}]" if keywords else ""
            headline = a.message.splitlines()[0].replace("Headline: ", "").strip()
            lines.append(f"• `{sym}`{kw_str}: {headline[:80]}...")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        return AlertEvent(
            id=f"digest_news_{datetime.now(timezone.utc).timestamp()}",
            severity=AlertSeverity.P0,
            symbol="MARKET",
            title=f"📰 MARKET CATALYSTS DIGEST ({len(news_alerts)} Items)",
            message="\n".join(lines),
            source="composite_dispatcher",
            metadata={"item_count": len(news_alerts)}
        )

    def clear_cooldown(self, symbol: Optional[str] = None) -> None:
        """Clears cooldown records (for testing)."""
        if symbol:
            self.last_dispatched = {k: v for k, v in self.last_dispatched.items() if not k.startswith(symbol)}
            self.last_alerted_prices = {k: v for k, v in self.last_alerted_prices.items() if k != symbol}
        else:
            self.last_dispatched.clear()
            self.last_alerted_prices.clear()
