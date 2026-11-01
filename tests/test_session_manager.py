"""
tests/test_session_manager.py - Unit Tests for Session Clock, Quiet Hours, and Briefings.
"""

import pytest
from datetime import datetime
import pytz
from pathlib import Path
import shutil

from core.session_manager import SessionManager
from core.models import AlertEvent, AlertSeverity
from core.state_manager import StateManager
from feeds.mock_feed import MockFeed


@pytest.fixture
def session_mgr():
    return SessionManager(timezone_str="Europe/Stockholm")


@pytest.fixture
def state_mgr(tmp_path):
    orig_config = Path(__file__).resolve().parent.parent / "config" / "portfolio_state.yaml"
    test_config = tmp_path / "test_portfolio_state.yaml"
    shutil.copy(orig_config, test_config)
    return StateManager(str(test_config))


def test_session_detection(session_mgr):
    tz = pytz.timezone("Europe/Stockholm")

    # 1. Tuesday 10:00 CEST -> Nordic Equity session
    t1 = datetime(2026, 10, 6, 10, 0, 0, tzinfo=tz)
    sessions1 = session_mgr.get_active_sessions(current_dt=t1)
    assert "NORDIC_EQUITY" in sessions1
    assert "US_EQUITY" not in sessions1

    # 2. Tuesday 16:30 CEST -> Both Nordic Equity and US Equity
    t2 = datetime(2026, 10, 6, 16, 30, 0, tzinfo=tz)
    sessions2 = session_mgr.get_active_sessions(current_dt=t2)
    assert "NORDIC_EQUITY" in sessions2
    assert "US_EQUITY" in sessions2

    # 3. Tuesday 18:30 CEST -> Stockholm Curfew Window & US Equity
    t3 = datetime(2026, 10, 6, 18, 30, 0, tzinfo=tz)
    sessions3 = session_mgr.get_active_sessions(current_dt=t3)
    assert "STOCKHOLM_CURFEW_WINDOW" in sessions3
    assert "US_EQUITY" in sessions3

    # 4. Tuesday 23:00 CEST -> Overnight Quiet Hours
    t4 = datetime(2026, 10, 6, 23, 0, 0, tzinfo=tz)
    sessions4 = session_mgr.get_active_sessions(current_dt=t4)
    assert "OVERNIGHT_QUIET_HOURS" in sessions4

    # 5. Saturday 12:00 CEST -> Weekend Dormant
    t5 = datetime(2026, 10, 10, 12, 0, 0, tzinfo=tz)
    sessions5 = session_mgr.get_active_sessions(current_dt=t5)
    assert "WEEKEND_DORMANT" in sessions5


def test_quiet_hours_filtering(session_mgr):
    tz = pytz.timezone("Europe/Stockholm")

    # Time inside quiet hours: 23:00 CEST
    night_t = datetime(2026, 10, 6, 23, 0, 0, tzinfo=tz)
    assert session_mgr.is_in_quiet_hours(current_dt=night_t) is True

    p0_alert = AlertEvent(id="1", severity=AlertSeverity.P0, title="Digest", message="Routine info")
    p1_alert = AlertEvent(id="2", severity=AlertSeverity.P1, title="Approaching stop", message="Warning")
    p2_alert = AlertEvent(id="3", severity=AlertSeverity.P2, title="Hard stop breach", message="Emergency exit")

    # P0 and P1 must be suppressed during quiet hours
    assert session_mgr.should_suppress_alert(p0_alert, current_dt=night_t) is True
    assert session_mgr.should_suppress_alert(p1_alert, current_dt=night_t) is True

    # P2 EMERGENCY alert MUST pass through!
    assert session_mgr.should_suppress_alert(p2_alert, current_dt=night_t) is False

    # Time outside quiet hours: 14:00 CEST
    day_t = datetime(2026, 10, 6, 14, 0, 0, tzinfo=tz)
    assert session_mgr.is_in_quiet_hours(current_dt=day_t) is False
    assert session_mgr.should_suppress_alert(p0_alert, current_dt=day_t) is False
    assert session_mgr.should_suppress_alert(p1_alert, current_dt=day_t) is False
    assert session_mgr.should_suppress_alert(p2_alert, current_dt=day_t) is False


def test_morning_briefing_generation(session_mgr, state_mgr):
    feed = MockFeed()
    tz = pytz.timezone("Europe/Stockholm")
    briefing_dt = datetime(2026, 10, 9, 8, 30, 0, tzinfo=tz)

    briefing = session_mgr.generate_morning_briefing(state_mgr, feed, current_dt=briefing_dt)

    assert briefing.severity == AlertSeverity.P0
    assert "MORNING BRIEFING" in briefing.title
    assert "696.50 pts" in briefing.message
    assert "Mandate Capital Ledger:" in briefing.message
    assert "Active Stop-Loss Audits:" in briefing.message
    assert "Upcoming Catalysts" in briefing.message


def test_evening_debrief_generation(session_mgr, state_mgr):
    feed = MockFeed()
    tz = pytz.timezone("Europe/Stockholm")
    debrief_dt = datetime(2026, 10, 9, 22, 15, 0, tzinfo=tz)

    debrief = session_mgr.generate_evening_debrief(state_mgr, feed, current_dt=debrief_dt)

    assert debrief.severity == AlertSeverity.P0
    assert "EVENING DEBRIEF" in debrief.title
    assert "696.50 pts" in debrief.message
    assert "Cash Partitioning Verification:" in debrief.message
    assert "Market Movers Today:" in debrief.message


def test_recommended_gear(session_mgr):
    tz = pytz.timezone("Europe/Stockholm")

    # Daytime trading hours: cruise
    day_t = datetime(2026, 10, 6, 14, 0, 0, tzinfo=tz)
    assert session_mgr.get_recommended_gear(current_dt=day_t) == "cruise"

    # Overnight quiet hours: dormant
    night_t = datetime(2026, 10, 6, 23, 0, 0, tzinfo=tz)
    assert session_mgr.get_recommended_gear(current_dt=night_t) == "dormant"

    # When watch target active: attack
    assert session_mgr.get_recommended_gear(current_dt=day_t, has_watch_target=True) == "attack"
    assert session_mgr.get_recommended_gear(current_dt=night_t, has_watch_target=True) == "attack"


def test_composite_dispatcher_quiet_hours_integration(session_mgr):
    from dispatchers.composite_dispatcher import CompositeDispatcher

    dispatcher = CompositeDispatcher(cooldown_minutes=15, session_manager=session_mgr)
    dispatched = []
    dispatcher.register_handler(lambda a: dispatched.append(a))

    tz = pytz.timezone("Europe/Stockholm")
    night_t = datetime(2026, 10, 6, 23, 0, 0, tzinfo=tz)

    p0 = AlertEvent(id="p0", severity=AlertSeverity.P0, title="Digest", message="Routine")
    p2 = AlertEvent(id="p2", severity=AlertSeverity.P2, title="Hard stop", message="Emergency")

    # In quiet hours: should_suppress on p0 is True
    assert dispatcher.should_suppress(p0, current_dt=night_t) is True

    # In quiet hours: should_suppress on p2 is False (emergency bypass)
    assert dispatcher.should_suppress(p2, current_dt=night_t) is False


def test_check_scheduled_briefings_morning_and_evening(session_mgr, state_mgr):
    feed = MockFeed()
    tz = pytz.timezone("Europe/Stockholm")

    # 1. Before 08:30 (e.g. 08:15 CEST) -> No briefing
    t_before = datetime(2026, 10, 6, 8, 15, 0, tzinfo=tz)
    assert len(session_mgr.check_scheduled_briefings(state_mgr, feed, current_dt=t_before)) == 0

    # 2. At 08:30 CEST -> Dispatches morning briefing
    t_morning = datetime(2026, 10, 6, 8, 30, 0, tzinfo=tz)
    morning_alerts = session_mgr.check_scheduled_briefings(state_mgr, feed, current_dt=t_morning)
    assert len(morning_alerts) == 1
    assert "MORNING BRIEFING" in morning_alerts[0].title

    # 3. Repeat at 08:35 CEST on same day -> Deduplicated / already sent today
    t_morning_repeat = datetime(2026, 10, 6, 8, 35, 0, tzinfo=tz)
    assert len(session_mgr.check_scheduled_briefings(state_mgr, feed, current_dt=t_morning_repeat)) == 0

    # 4. At 22:15 CEST -> Dispatches evening debrief
    t_evening = datetime(2026, 10, 6, 22, 15, 0, tzinfo=tz)
    evening_alerts = session_mgr.check_scheduled_briefings(state_mgr, feed, current_dt=t_evening)
    assert len(evening_alerts) == 1
    assert "EVENING DEBRIEF" in evening_alerts[0].title

    # 5. Repeat at 22:20 CEST on same day -> Deduplicated
    t_evening_repeat = datetime(2026, 10, 6, 22, 20, 0, tzinfo=tz)
    assert len(session_mgr.check_scheduled_briefings(state_mgr, feed, current_dt=t_evening_repeat)) == 0


def test_session_manager_dst_safety(session_mgr):
    # Test naive datetime during autumn DST fallback
    naive_dst_dt = datetime(2026, 10, 25, 2, 30, 0)
    localized = session_mgr.get_localized_time(naive_dst_dt)
    assert localized is not None
    assert localized.tzinfo is not None
