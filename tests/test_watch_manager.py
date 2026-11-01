"""
tests/test_watch_manager.py - Unit Tests for Focused Watch Mode & Auto-Reset Logic.
"""

import pytest
from datetime import datetime, timedelta
import pytz

from core.watch_manager import WatchManager
from core.models import AlertSeverity


@pytest.fixture
def watch_mgr():
    return WatchManager(timezone_str="Europe/Stockholm")


def test_start_watch_target(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    start_t = datetime(2026, 10, 9, 14, 0, 0, tzinfo=tz)

    target = watch_mgr.start_watch(
        symbol="ARM",
        baseline_price=140.00,
        duration_minutes=60,
        alert_threshold_pct=0.015,
        start_time=start_t
    )

    assert target.symbol == "ARM"
    assert target.baseline_price == 140.00
    assert target.duration_minutes == 60
    assert target.alert_threshold_pct == 0.015
    assert target.status == "active"
    assert watch_mgr.is_watching("ARM") is True
    assert len(watch_mgr.get_active_targets()) == 1


def test_price_deviation_alert(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    start_t = datetime(2026, 10, 9, 14, 0, 0, tzinfo=tz)

    watch_mgr.start_watch(
        symbol="ORCL",
        baseline_price=160.00,
        duration_minutes=60,
        alert_threshold_pct=0.015,
        start_time=start_t
    )

    # 1. Price move within threshold (+1.0%) -> No alert
    no_alerts = watch_mgr.evaluate_quote("ORCL", 161.60, current_dt=start_t + timedelta(minutes=5))
    assert len(no_alerts) == 0

    # 2. Price move exceeding threshold (+1.875% to 163.00) -> Alert triggered
    alerts = watch_mgr.evaluate_quote("ORCL", 163.00, current_dt=start_t + timedelta(minutes=10))
    assert len(alerts) == 1
    a = alerts[0]
    assert a.severity == AlertSeverity.P1
    assert "FOCUSED WATCH SURGE" in a.title
    assert "ORCL" in a.title
    assert a.metadata["deviation_pct"] > 1.5

    # 3. Small jitter without further significant change (< 0.5% from 163.00) -> Suppressed
    jitter_alerts = watch_mgr.evaluate_quote("ORCL", 163.20, current_dt=start_t + timedelta(minutes=12))
    assert len(jitter_alerts) == 0

    # 4. Significant drop (-2.5% from baseline to 156.00) -> Alert triggered
    drop_alerts = watch_mgr.evaluate_quote("ORCL", 156.00, current_dt=start_t + timedelta(minutes=15))
    assert len(drop_alerts) == 1
    assert "FOCUSED WATCH DROP" in drop_alerts[0].title


def test_ttl_auto_reset(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    start_t = datetime(2026, 10, 9, 14, 0, 0, tzinfo=tz)

    watch_mgr.start_watch(
        symbol="CEG",
        baseline_price=290.00,
        duration_minutes=60,
        start_time=start_t
    )
    assert watch_mgr.is_watching("CEG") is True

    # 59 minutes: still active
    t_59 = start_t + timedelta(minutes=59)
    alerts_59 = watch_mgr.check_expirations(current_dt=t_59)
    assert len(alerts_59) == 0
    assert watch_mgr.is_watching("CEG") is True

    # 61 minutes: TTL expired
    t_61 = start_t + timedelta(minutes=61)
    alerts_61 = watch_mgr.check_expirations(current_dt=t_61)
    assert len(alerts_61) == 1
    assert "WATCH MODE AUTO-RESET: CEG" in alerts_61[0].title
    assert "TTL expired" in alerts_61[0].message
    assert alerts_61[0].severity == AlertSeverity.P0
    assert watch_mgr.is_watching("CEG") is False


def test_manual_unfocus(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    start_t = datetime(2026, 10, 9, 14, 0, 0, tzinfo=tz)

    watch_mgr.start_watch(
        symbol="ARM",
        baseline_price=140.00,
        duration_minutes=60,
        start_time=start_t
    )
    assert watch_mgr.is_watching("ARM") is True

    # Stop watch
    alerts = watch_mgr.stop_watch("ARM", current_dt=start_t + timedelta(minutes=10))
    assert len(alerts) == 1
    assert "WATCH MODE AUTO-RESET: ARM" in alerts[0].title
    assert "User cancellation" in alerts[0].message
    assert watch_mgr.is_watching("ARM") is False


def test_market_close_auto_reset(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    # Friday 17:00 CEST (before Nordic market close at 17:30)
    start_t = datetime(2026, 10, 9, 17, 0, 0, tzinfo=tz)

    watch_mgr.start_watch(
        symbol="INVE-B.ST",
        baseline_price=405.00,
        duration_minutes=60,
        start_time=start_t
    )
    assert watch_mgr.is_watching("INVE-B.ST") is True

    # At 17:35 CEST, market close is reached
    t_after_close = datetime(2026, 10, 9, 17, 35, 0, tzinfo=tz)
    alerts = watch_mgr.check_expirations(current_dt=t_after_close)
    assert len(alerts) == 1
    assert "WATCH MODE AUTO-RESET: INVE-B.ST" in alerts[0].title
    assert "Market close reached" in alerts[0].message
    assert watch_mgr.is_watching("INVE-B.ST") is False


def test_watch_manager_alias_matching(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    start_t = datetime(2026, 10, 9, 14, 0, 0, tzinfo=tz)

    # Start watch using "INVE-B.ST"
    watch_mgr.start_watch("INVE-B.ST", baseline_price=405.0, start_time=start_t)

    # Alias "INVE B" should match
    assert watch_mgr.is_watching("INVE B") is True
    assert watch_mgr.get_target("INVE B") is not None
    assert watch_mgr.get_target("INVE B").symbol == "INVE-B.ST"

    # Stop watch using alias "INVE B"
    alerts = watch_mgr.stop_watch("INVE B", current_dt=start_t + timedelta(minutes=5))
    assert len(alerts) == 1
    assert "INVE-B.ST" in alerts[0].title
    assert watch_mgr.is_watching("INVE-B.ST") is False
    assert watch_mgr.is_watching("INVE B") is False


def test_market_close_auto_reset_on_overnight_rollover(watch_mgr):
    tz = pytz.timezone("Europe/Stockholm")
    # Started Monday 21:50 CEST (US close is 22:00 CEST)
    start_t = datetime(2026, 10, 5, 21, 50, 0, tzinfo=tz)

    watch_mgr.start_watch("ARM", baseline_price=140.0, duration_minutes=120, start_time=start_t)
    assert watch_mgr.is_watching("ARM") is True

    # Next morning: Tuesday 08:00 CEST (date rolled over past start_t date)
    t_next_morning = datetime(2026, 10, 6, 8, 0, 0, tzinfo=tz)
    alerts = watch_mgr.check_expirations(current_dt=t_next_morning)
    assert len(alerts) == 1
    assert "WATCH MODE AUTO-RESET: ARM" in alerts[0].title
    assert "Market close reached" in alerts[0].message
    assert watch_mgr.is_watching("ARM") is False


def test_dst_timezone_transition_safety(watch_mgr):
    # Test naive datetime during autumn DST switch (ambiguous 02:30 in Stockholm)
    naive_dst_dt = datetime(2026, 10, 25, 2, 30, 0)
    localized = watch_mgr._get_localized_time(naive_dst_dt)
    assert localized is not None
    assert localized.tzinfo is not None


def test_nordic_exchange_close_detection(watch_mgr):
    assert watch_mgr._determine_exchange_close("INVE B") == "17:30"
    assert watch_mgr._determine_exchange_close("INVE-B.ST") == "17:30"
    assert watch_mgr._determine_exchange_close("LUN.ST") == "17:30"
    assert watch_mgr._determine_exchange_close("LUMI") == "17:30"
    assert watch_mgr._determine_exchange_close("SAAB B") == "17:30"
    assert watch_mgr._determine_exchange_close("ORCL") == "22:00"
    assert watch_mgr._determine_exchange_close("ARM") == "22:00"
