"""
tests/test_derivatives_desk.py - Comprehensive Unit Tests for DerivativesDesk.

Verifies:
1. 5.00 pt capital allocation cap enforcement.
2. -40% hard stop on warrant price (capping downside loss <= 2.00 pts).
3. Gearing calculation: (underlying_price / warrant_price) * parity_ratio.
4. Barrier distance tracking: abs(underlying_price - financing_level) / underlying_price * 100.
5. Stockholm 19:45 CEST mandatory flat-book curfew escalations.
6. Master warrant evaluation and Telegram card formatting.
"""

from datetime import datetime
import pytz
import pytest

from core.models import Holding, AlertSeverity
from core.derivatives_desk import DerivativesDesk, WarrantMetrics


def test_derivatives_sizing_cap_5pt():
    desk = DerivativesDesk()

    # Case 1: Exactly 5.00 pts allocation -> PASS
    res1 = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.00)
    assert res1["passed"] is True
    assert res1["max_loss_pts"] == 2.00
    assert res1["loss_capped_at_2pt"] is True

    # Case 2: 3.50 pts allocation -> PASS
    res2 = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=3.50)
    assert res2["passed"] is True
    assert res2["max_loss_pts"] == 1.40

    # Case 3: 5.50 pts allocation -> FAIL (exceeds 5.00 pt cap)
    res3 = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.50)
    assert res3["passed"] is False
    assert "Gate 4 Breach" in res3["reason"]


def test_hard_stop_minus_40_pct_and_max_loss_cap():
    desk = DerivativesDesk()

    entry_price = 12.50
    stop_price = desk.calculate_hard_stop(entry_price=entry_price, stop_pct=0.40)
    # 12.50 * 0.60 = 7.50
    assert stop_price == 7.50

    # When 5.00 pts is allocated, maximum loss is 5.00 * 0.40 = 2.00 pts
    # This strictly honors the Gate 4 <= 3.48 pt single-trade risk budget
    res = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.00)
    assert res["max_loss_pts"] == 2.00
    assert res["max_loss_pts"] <= desk.MAX_DOWNSIDE_LOSS_PTS


def test_gearing_calculation():
    desk = DerivativesDesk()

    # Underlying = 2600.0, Warrant = 13.0, Parity = 0.05
    # Gearing = (2600.0 / 13.0) * 0.05 = 200 * 0.05 = 10.0x
    gearing = desk.calculate_gearing(
        underlying_price=2600.0,
        warrant_price=13.0,
        parity_ratio=0.05
    )
    assert gearing == 10.00

    # Under 1:1 parity (parity_ratio = 1.0)
    gearing_unit = desk.calculate_gearing(
        underlying_price=100.0,
        warrant_price=20.0,
        parity_ratio=1.0
    )
    assert gearing_unit == 5.00

    # Zero price edge case
    assert desk.calculate_gearing(2600.0, 0.0, 0.05) == 0.0


def test_barrier_distance_calculation():
    desk = DerivativesDesk()

    # Underlying = 2600.0, Financing Level = 2470.0
    # Distance = |2600 - 2470| / 2600 * 100 = 130 / 2600 * 100 = 5.00%
    dist = desk.calculate_barrier_distance(
        underlying_price=2600.0,
        financing_level=2470.0
    )
    assert dist == 5.00

    # Tight barrier: Underlying = 2500.0, Financing = 2475.0 -> 1.00% buffer
    tight_dist = desk.calculate_barrier_distance(2500.0, 2475.0)
    assert tight_dist == 1.00


def test_stockholm_curfew_escalation():
    desk = DerivativesDesk()
    st_tz = pytz.timezone("Europe/Stockholm")

    # Fixed weekday: Wednesday 2026-10-14
    # 1. 14:00 CEST -> NORMAL
    dt_normal = st_tz.localize(datetime(2026, 10, 14, 14, 0, 0))
    res_normal = desk.check_curfew(current_dt=dt_normal)
    assert res_normal["status"] == "NORMAL"
    assert res_normal["severity"] == AlertSeverity.P0

    # 2. 19:30 CEST -> ADVISORY (P0)
    dt_w1 = st_tz.localize(datetime(2026, 10, 14, 19, 31, 0))
    res_w1 = desk.check_curfew(current_dt=dt_w1)
    assert res_w1["status"] == "ADVISORY"
    assert res_w1["severity"] == AlertSeverity.P0

    # 3. 19:40 CEST -> WARNING (P1)
    dt_w2 = st_tz.localize(datetime(2026, 10, 14, 19, 41, 0))
    res_w2 = desk.check_curfew(current_dt=dt_w2)
    assert res_w2["status"] == "WARNING"
    assert res_w2["severity"] == AlertSeverity.P1

    # 4. 19:45 CEST -> MANDATORY FLAT-BOOK BREACH (P2)
    dt_breach = st_tz.localize(datetime(2026, 10, 14, 19, 46, 0))
    res_breach = desk.check_curfew(current_dt=dt_breach)
    assert res_breach["status"] == "BREACH"
    assert res_breach["severity"] == AlertSeverity.P2
    assert "EMERGENCY CURFEW BREACH" in res_breach["message"]


def test_master_warrant_evaluation_and_card_formatting():
    desk = DerivativesDesk()
    st_tz = pytz.timezone("Europe/Stockholm")

    # Evaluate normal compliant position
    dt_normal = st_tz.localize(datetime(2026, 10, 14, 11, 0, 0))
    metrics = desk.evaluate_warrant(
        symbol="MINI L OMX AVA 1615",
        underlying_price=2600.0,
        warrant_price=13.0,
        financing_level=2470.0,
        parity_ratio=0.05,
        current_points=5.00,
        entry_price=13.0,
        current_time=dt_normal
    )

    assert metrics.gearing == 10.00
    assert metrics.barrier_distance_pct == 5.00
    assert metrics.hard_stop_price == 7.80  # 13.0 * 0.60
    assert metrics.max_loss_pts == 2.00
    assert metrics.passed_cap is True
    assert metrics.is_stop_breached is False
    assert metrics.curfew_status == "NORMAL"

    # Test breach condition when warrant price drops to 7.00 (< 7.80 hard stop)
    metrics_breached = desk.evaluate_warrant(
        symbol="MINI L OMX AVA 1615",
        underlying_price=2500.0,
        warrant_price=7.00,
        financing_level=2470.0,
        parity_ratio=0.05,
        current_points=5.00,
        entry_price=13.0,
        current_time=dt_normal
    )
    assert metrics_breached.is_stop_breached is True
    assert "HARD STOP BREACH" in metrics_breached.directive

    # Test card output
    card = desk.format_derivatives_card(metrics)
    assert "DERIVATIVES DESK" in card
    assert "Effective Gearing:" in card
    assert "10.00x" in card
    assert "5.00 pts" in card
    assert "2.00 pts" in card
    assert "19:45 CEST Curfew:" in card


def test_aggregate_sleeve_limit():
    desk = DerivativesDesk()
    # If active sleeve is 12.00 pts, adding 4.00 pts pushes total to 16.00 > 15.00 pts
    res = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=4.00, current_sleeve_points=12.00)
    assert res["passed"] is False
    assert "Gate 4 Sleeve Breach" in res["reason"]

    # Adding 2.50 pts keeps total at 14.50 <= 15.00 pts
    res_ok = desk.audit_derivative_sizing("MINI L OMX AVA 1615", points=2.50, current_sleeve_points=12.00)
    assert res_ok["passed"] is True


def test_weekend_curfew():
    desk = DerivativesDesk()
    st_tz = pytz.timezone("Europe/Stockholm")
    # Saturday 2026-10-17
    dt_weekend = st_tz.localize(datetime(2026, 10, 17, 19, 45, 0))
    res = desk.check_curfew(current_dt=dt_weekend)
    assert res["status"] == "WEEKEND"
    assert res["is_breached"] is False


def test_tight_barrier_alert():
    desk = DerivativesDesk()
    metrics = desk.evaluate_warrant(
        symbol="MINI L OMX AVA 1615",
        underlying_price=2500.0,
        warrant_price=5.00,
        financing_level=2475.0,  # 1.00% barrier buffer
        parity_ratio=0.05,
        current_points=3.00,
        entry_price=5.00
    )
    assert metrics.barrier_distance_pct == 1.00
    assert "High Knockout Risk" in metrics.directive

