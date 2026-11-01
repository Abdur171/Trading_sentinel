"""
tests/test_curfew.py - Tests for the Generic High Time-Sensitivity & 19:45 CEST Derivative Curfew Watchdog.
"""

import pytest
from datetime import datetime, date
import pytz
from core.curfew_manager import CurfewManager
from core.models import Holding, CurfewConfig, AlertSeverity, TimeSensitiveEvent


def test_curfew_warning_stages():
    mgr = CurfewManager(timezone_str="Europe/Stockholm")
    st_tz = pytz.timezone("Europe/Stockholm")

    warrant = Holding(
        symbol="MINI L OMX AVA 1615",
        name="MINI L OMX AVA 1615",
        points=5.00,
        account="avanza_isk",
        classification="Derivative Warrant",
        instrument_type="derivative_warrant",
        curfew=CurfewConfig(enforced=True, cutoff_time="19:45", warning_1="19:30", warning_2="19:40")
    )

    # 1. Before 19:30 (e.g. 19:20 CEST) -> Safe, None
    dt_1920 = st_tz.localize(datetime(2026, 10, 8, 19, 20, 0))
    assert mgr.check_derivative_curfew(warrant, current_dt=dt_1920) is None

    # 2. At 19:32 CEST (Warning 1 Advisory -> P0)
    dt_1932 = st_tz.localize(datetime(2026, 10, 8, 19, 32, 0))
    alert_w1 = mgr.check_derivative_curfew(warrant, current_dt=dt_1932)
    assert alert_w1 is not None
    assert alert_w1.severity == AlertSeverity.P0
    assert "CURFEW ADVISORY" in alert_w1.title

    # 3. At 19:41 CEST (Warning 2 Urgent -> P1)
    dt_1941 = st_tz.localize(datetime(2026, 10, 8, 19, 41, 0))
    alert_w2 = mgr.check_derivative_curfew(warrant, current_dt=dt_1941)
    assert alert_w2 is not None
    assert alert_w2.severity == AlertSeverity.P1
    assert "CURFEW CLOSING IN" in alert_w2.title

    # 4. At 19:45:01 CEST (Cutoff reached -> Emergency P2)
    dt_1945 = st_tz.localize(datetime(2026, 10, 8, 19, 45, 1))
    alert_p2 = mgr.check_derivative_curfew(warrant, current_dt=dt_1945)
    assert alert_p2 is not None
    assert alert_p2.severity == AlertSeverity.P2
    assert "EMERGENCY: DERIVATIVE CURFEW BREACH" in alert_p2.title


def test_closed_warrant_triggers_no_curfew():
    mgr = CurfewManager(timezone_str="Europe/Stockholm")
    st_tz = pytz.timezone("Europe/Stockholm")

    # Closed / flat warrant (0.00 pts)
    warrant_closed = Holding(
        symbol="MINI L OMX AVA 1615",
        name="MINI L OMX AVA 1615",
        points=0.00,
        account="avanza_isk",
        classification="Derivative Warrant",
        instrument_type="derivative_warrant",
        status="flat"
    )

    dt_1950 = st_tz.localize(datetime(2026, 10, 8, 19, 50, 0))
    assert mgr.check_derivative_curfew(warrant_closed, current_dt=dt_1950) is None


def test_calendar_event_monitoring():
    mgr = CurfewManager(timezone_str="Europe/Stockholm")
    st_tz = pytz.timezone("Europe/Stockholm")

    sinch_earnings = TimeSensitiveEvent(
        symbol="SINCH.ST",
        event_type="earnings",
        event_date="2026-11-05",
        description="Sinch AB Q3 Earnings Report",
        directive="Hold 10 pt core; audit implied move vs MoS before binary print.",
        days_warning_threshold=7
    )

    # 10 days before -> No alert
    dt_early = st_tz.localize(datetime(2026, 10, 20, 10, 0, 0))
    assert mgr.check_calendar_event(sinch_earnings, current_dt=dt_early) is None

    # 4 days before -> Alert generated
    dt_near = st_tz.localize(datetime(2026, 11, 1, 10, 0, 0))
    alert = mgr.check_calendar_event(sinch_earnings, current_dt=dt_near)
    assert alert is not None
    assert "TIME-SENSITIVE EVENT APPROACHING" in alert.title
    assert "4 days away" in alert.message


def test_weekend_and_overnight_curfew_suppression():
    mgr = CurfewManager(timezone_str="Europe/Stockholm")
    st_tz = pytz.timezone("Europe/Stockholm")

    warrant = Holding(
        symbol="MINI L OMX AVA 1615",
        name="MINI L OMX AVA 1615",
        points=5.00,
        account="avanza_isk",
        classification="Derivative Warrant",
        instrument_type="derivative_warrant"
    )

    # 1. Saturday Oct 10, 2026 at 19:46 CEST -> Suppressed (Weekend)
    sat_dt = st_tz.localize(datetime(2026, 10, 10, 19, 46, 0))
    assert mgr.check_derivative_curfew(warrant, current_dt=sat_dt) is None

    # 2. Weekday Thursday Oct 8, 2026 at 22:30 CEST -> Suppressed (Post-session overnight)
    overnight_dt = st_tz.localize(datetime(2026, 10, 8, 22, 30, 0))
    assert mgr.check_derivative_curfew(warrant, current_dt=overnight_dt) is None
