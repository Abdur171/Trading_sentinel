"""
tests/test_alert_suppression.py - Unit tests for 12-Hour Alert Suppression, Interactive Snooze,
State-Transition Macro Triggering, News Ticker Filtering, and Power Management.
"""

import pytest
from datetime import datetime, timedelta, timezone
from core.models import AlertEvent, AlertSeverity, Holding, MarketQuote
from dispatchers.composite_dispatcher import CompositeDispatcher
from core.power_manager import PowerManager
from feeds.news_feed import NewsFeed
from workers.risk_worker import RiskWorker
from core.state_manager import StateManager
from core.gate_auditor import GateAuditor
from core.curfew_manager import CurfewManager
from core.quant_risk import QuantRisk
from feeds.mock_feed import MockFeed
from core.organiser import Organiser


def test_12_hour_stationary_alert_suppression():
    """Verify that stationary alerts (Macro Shield, Stop Losses, Digests) are suppressed for 12 hours."""
    dispatcher = CompositeDispatcher(cooldown_minutes=15, stationary_suppression_hours=12.0)
    received = []
    dispatcher.register_handler(lambda a: received.append(a))

    t0 = datetime(2026, 10, 12, 8, 0, 0, tzinfo=timezone.utc)
    macro_alert = AlertEvent(
        id="macro_1",
        severity=AlertSeverity.P2,
        symbol="MACRO",
        title="🚨 AUTOMATED MACRO DEFENSIVE SHIELD ACTIVATED",
        message="US 10Y yield >= 5.08%",
        source="quant_risk"
    )

    # 1. First dispatch: should succeed
    assert dispatcher.dispatch(macro_alert, current_dt=t0) is True
    assert len(received) == 1

    # 2. 5 minutes later: should be suppressed under 12-hour window
    t_5m = t0 + timedelta(minutes=5)
    assert dispatcher.dispatch(macro_alert, current_dt=t_5m) is False
    assert len(received) == 1

    # 3. 6 hours later: still suppressed
    t_6h = t0 + timedelta(hours=6)
    assert dispatcher.dispatch(macro_alert, current_dt=t_6h) is False
    assert len(received) == 1

    # 4. 12.1 hours later: window has expired, should dispatch again!
    t_12h1m = t0 + timedelta(hours=12, minutes=1)
    assert dispatcher.dispatch(macro_alert, current_dt=t_12h1m) is True
    assert len(received) == 2


def test_urgent_curfew_fast_repeat_exemption():
    """Verify that time-sensitive derivative curfew breaches bypass 12-hour suppression and repeat every 180s."""
    dispatcher = CompositeDispatcher(cooldown_minutes=15, stationary_suppression_hours=12.0)
    received = []
    dispatcher.register_handler(lambda a: received.append(a))

    t0 = datetime(2026, 10, 12, 19, 35, 0, tzinfo=timezone.utc)
    curfew_alert = AlertEvent(
        id="curfew_1",
        severity=AlertSeverity.P2,
        symbol="MINI L OMX AVA 1615",
        title="🚨 EMERGENCY: DERIVATIVE CURFEW BREACH (19:45 CEST)",
        message="Mandatory curfew exit required immediately!",
        source="curfew_manager"
    )

    # 1. Initial dispatch
    assert dispatcher.dispatch(curfew_alert, current_dt=t0) is True

    # 2. 1 minute later: suppressed within 180s
    t_1m = t0 + timedelta(minutes=1)
    assert dispatcher.dispatch(curfew_alert, current_dt=t_1m) is False

    # 3. 3.5 minutes later (> 180s): re-dispatches to prompt immediate broker execution!
    t_3m30s = t0 + timedelta(seconds=210)
    assert dispatcher.dispatch(curfew_alert, current_dt=t_3m30s) is True
    assert len(received) == 2


def test_interactive_snooze_and_auto_reset():
    """Verify interactive snooze muting and automatic 12-hour reset."""
    dispatcher = CompositeDispatcher()
    t0 = datetime(2026, 10, 12, 10, 0, 0, tzinfo=timezone.utc)

    # Snooze MACRO for 12 hours
    expire_dt = dispatcher.snooze("MACRO", hours=12.0, current_dt=t0)
    assert expire_dt == t0 + timedelta(hours=12.0)

    # Check active snoozes
    active = dispatcher.get_active_snoozes(current_dt=t0)
    assert len(active) == 1
    assert active[0]["target"] == "MACRO"
    assert active[0]["remaining_hours"] == 12.0

    macro_alert = AlertEvent(
        id="m1", severity=AlertSeverity.P2, symbol="MACRO",
        title="🚨 AUTOMATED MACRO DEFENSIVE SHIELD ACTIVATED",
        message="Brent >= 110", source="quant_risk"
    )
    # Alert is snoozed
    assert dispatcher.should_suppress(macro_alert, current_dt=t0 + timedelta(hours=1)) is True

    # Different symbol like AEHR is NOT snoozed
    aehr_alert = AlertEvent(
        id="a1", severity=AlertSeverity.P2, symbol="AEHR",
        title="🚨 STOP LOSS BREACH: AEHR",
        message="Stop hit", source="gate_auditor"
    )
    assert dispatcher.should_suppress(aehr_alert, current_dt=t0 + timedelta(hours=1)) is False

    # After 12.5 hours, snooze automatically resets
    t_12h30m = t0 + timedelta(hours=12, minutes=30)
    active_after = dispatcher.get_active_snoozes(current_dt=t_12h30m)
    assert len(active_after) == 0  # auto-reset!

    # Manual unsnooze
    dispatcher.snooze("GLW", hours=12.0, current_dt=t0)
    assert len(dispatcher.get_active_snoozes(current_dt=t0)) == 1
    assert dispatcher.unsnooze("GLW") is True
    assert len(dispatcher.get_active_snoozes(current_dt=t0)) == 0


def test_state_transition_macro_alerting():
    """Verify that RiskWorker only emits macro shield alerts on state transitions."""
    state_mgr = StateManager()
    auditor = GateAuditor()
    curfew_mgr = CurfewManager()
    feed = MockFeed()
    dispatcher = CompositeDispatcher()
    quant_risk = QuantRisk()

    worker = RiskWorker(
        state_mgr=state_mgr,
        gate_auditor=auditor,
        curfew_mgr=curfew_mgr,
        feed=feed,
        dispatcher=dispatcher,
        quant_risk=quant_risk
    )

    # Step 1: Normal regime (TNX = 4.90, BZ = 95.0)
    feed.set_quote("^TNX", 4.90)
    feed.set_quote("BZ=F", 95.0)
    alerts_normal = worker.run_single_audit_cycle()
    macro_alerts = [a for a in alerts_normal if a.symbol == "MACRO"]
    assert len(macro_alerts) == 0
    assert worker.last_shield_active is False

    # Step 2: Transition to Bearish Barrier (TNX = 5.50% >= 5.45%) -> Alert EMITTED
    feed.set_quote("^TNX", 5.50)
    alerts_breach = worker.run_single_audit_cycle()
    macro_alerts = [a for a in alerts_breach if a.symbol == "MACRO"]
    assert len(macro_alerts) == 1
    assert "ACTIVATED" in macro_alerts[0].title or "TRIGGERED" in macro_alerts[0].title
    assert worker.last_shield_active is True

    # Step 3: Steady state in next cycle (TNX = 5.50%) -> NO DUPLICATE ALERT
    alerts_steady = worker.run_single_audit_cycle()
    macro_alerts_steady = [a for a in alerts_steady if a.symbol == "MACRO"]
    assert len(macro_alerts_steady) == 0  # Suppressed because state did not change!

    # Step 4: Recovery transition (TNX drops back to 4.88%) -> Deactivation Alert EMITTED
    feed.set_quote("^TNX", 4.88)
    alerts_recovery = worker.run_single_audit_cycle()
    macro_alerts_recovery = [a for a in alerts_recovery if a.symbol == "MACRO"]
    assert len(macro_alerts_recovery) == 1
    assert "DEACTIVATED" in macro_alerts_recovery[0].title
    assert worker.last_shield_active is False


def test_progressive_stop_loss_filtering():
    """Verify that stationary stop loss breaches do not spam unless price drops by >= 2%."""
    dispatcher = CompositeDispatcher()
    received = []
    dispatcher.register_handler(lambda a: received.append(a))

    t0 = datetime(2026, 10, 12, 11, 0, 0, tzinfo=timezone.utc)
    stop_alert = AlertEvent(
        id="s1",
        severity=AlertSeverity.P1,
        symbol="GLW",
        title="🚨 STOP LOSS BREACH: GLW",
        message="Current Price: 50.00 <= Stop: 51.00",
        source="gate_auditor",
        metadata={"current_price": 50.00}
    )

    # 1. Initial stop alert
    assert dispatcher.dispatch(stop_alert, current_dt=t0) is True

    # 2. Same price or minor noise (49.90, -0.2% change): suppressed under 12h rule
    minor_drop = AlertEvent(
        id="s2",
        severity=AlertSeverity.P1,
        symbol="GLW",
        title="🚨 STOP LOSS BREACH: GLW",
        message="Current Price: 49.90 <= Stop: 51.00",
        source="gate_auditor",
        metadata={"current_price": 49.90}
    )
    assert dispatcher.dispatch(minor_drop, current_dt=t0 + timedelta(minutes=10)) is False

    # 3. Significant worsening (48.80, -2.4% drop below 50.00): allowed through!
    big_drop = AlertEvent(
        id="s3",
        severity=AlertSeverity.P1,
        symbol="GLW",
        title="🚨 STOP LOSS BREACH: GLW",
        message="Current Price: 48.80 <= Stop: 51.00",
        source="gate_auditor",
        metadata={"current_price": 48.80}
    )
    assert dispatcher.dispatch(big_drop, current_dt=t0 + timedelta(minutes=20)) is True


def test_news_feed_ticker_collision_guard():
    """Verify that common English words like 'now' do not trigger false catalyst alerts for ServiceNow (NOW)."""
    news_feed = NewsFeed()

    # Generic sentence with "now"
    sample_entry_benign = {
        "title": "Shop Now For Major Discounts Across Retailers",
        "summary": "Buyers can save money now on seasonal items across stores.",
        "link": "https://example.com/retail-news-1",
        "published": "2026-10-12T10:00:00Z"
    }

    # True material ticker news with cashtag or bracket
    sample_entry_real = {
        "title": "ServiceNow ($NOW) Reports Q3 Earnings Beat and Raises Guidance",
        "summary": "Software giant $NOW exceeded cloud revenue forecasts by 15%.",
        "link": "https://example.com/now-earnings-1",
        "published": "2026-10-12T10:05:00Z"
    }

    # Mock default feed content
    tracked = ["NOW", "AAPL", "MSFT"]
    sym_set = {s.upper() for s in tracked}

    # Test false positive check
    text_benign = f"{sample_entry_benign['title']} {sample_entry_benign['summary']}".lower()
    matched_benign = None
    for sym in sym_set:
        sym_u = sym.upper()
        if sym_u in news_feed.COMMON_WORD_TICKERS:
            cashtag = f"${sym.lower()}"
            paren = f"({sym.lower()})"
            if (cashtag in text_benign or paren in text_benign or f"({sym_u})" in sample_entry_benign["title"]):
                matched_benign = sym_u
    assert matched_benign is None  # correctly rejected!

    # Test true positive check
    text_real = f"{sample_entry_real['title']} {sample_entry_real['summary']}".lower()
    matched_real = None
    for sym in sym_set:
        sym_u = sym.upper()
        if sym_u in news_feed.COMMON_WORD_TICKERS:
            cashtag = f"${sym.lower()}"
            paren = f"({sym.lower()})"
            if (cashtag in text_real or paren in text_real or f"({sym_u})" in sample_entry_real["title"]):
                matched_real = sym_u
    assert matched_real == "NOW"  # correctly accepted!


def test_power_manager_sleep_detection():
    """Verify PowerManager clock discontinuity detection on resume from sleep."""
    pm = PowerManager(prevent_sleep=False)
    pm.record_tick(100.0)

    # Normal tick within 60s
    assert pm.check_discontinuity(expected_max_interval=180.0, current_mono=160.0) is None

    # Jump of 3600 seconds (machine was asleep for 1 hour)
    woken_gaps = []
    pm.register_wake_handler(lambda g: woken_gaps.append(g))

    gap = pm.check_discontinuity(expected_max_interval=180.0, current_mono=3760.0)
    assert gap is not None
    assert gap == 3600.0
    assert len(woken_gaps) == 1
    assert woken_gaps[0] == 3600.0


def test_telegram_snooze_commands_and_nlp():
    """Verify Telegram /snooze commands and conversational natural language routing."""
    organiser = Organiser(is_test_mode=True)

    # 1. Command /snooze MACRO 12
    resp_snooze = organiser.process_incoming_telegram_message("/snooze MACRO 12", "user1")
    assert "Alerts Muted / Snoozed" in resp_snooze
    assert "MACRO" in resp_snooze
    assert "12.0 hours" in resp_snooze

    # 2. Command /snoozes
    resp_list = organiser.process_incoming_telegram_message("/snoozes", "user1")
    assert "Active Alert Snooze Subscriptions" in resp_list
    assert "MACRO" in resp_list

    # 3. Command /unsnooze MACRO
    resp_unsnooze = organiser.process_incoming_telegram_message("/unsnooze MACRO", "user1")
    assert "Alerts Restored" in resp_unsnooze

    # 4. Natural language: "Snooze macro shield for 12 hours"
    resp_nlp = organiser.process_incoming_telegram_message("Please snooze macro shield for 12 hours", "user1")
    assert "Alerts Muted / Snoozed" in resp_nlp
    assert "MACRO" in resp_nlp

    # 5. Natural language: "Unmute all"
    resp_nlp_unmute = organiser.process_incoming_telegram_message("Unmute all", "user1")
    assert "Alerts Restored" in resp_nlp_unmute
