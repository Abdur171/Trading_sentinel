"""
tests/test_gate_auditor.py - Tests for Gate 4 Risk Budget, Stop Breaches, Trims, and Ten Gates.
"""

import pytest
from datetime import datetime, timezone
from core.gate_auditor import GateAuditor
from core.models import Holding, MarketQuote, RestingOrder, WatchlistTarget, WatchlistTranche, AlertSeverity, StopLossConfig, LimitSellConfig


def test_gate4_single_trade_risk_ceiling():
    auditor = GateAuditor(total_points=696.50)
    assert auditor.max_risk_pts == 3.48

    # Case 1: 10.0 pt position with 20% stop loss -> 2.00 pts loss <= 3.48 pts -> PASS
    risk_pts, risk_pct, passed = auditor.calculate_trade_risk(10.0, 0.20)
    assert risk_pts == 2.00
    assert passed is True

    # Case 2: 15.0 pt position with 30% stop loss -> 4.50 pts loss > 3.48 pts -> FAIL
    risk_pts, risk_pct, passed = auditor.calculate_trade_risk(15.0, 0.30)
    assert risk_pts == 4.50
    assert passed is False

    # Case 3: Derivative warrant MINI L OMX AVA 1615 (5.0 pts) with -40% stop loss order -> 2.00 pts -> PASS
    risk_pts, risk_pct, passed = auditor.calculate_trade_risk(
        position_points=5.0,
        stop_loss_pct=0.40,
        is_derivative=True,
        derivative_stop_pct=0.40
    )
    assert risk_pts == 2.00
    assert passed is True

    # Case 4: Derivative warrant without stop loss -> assumes 100% loss (5.0 pts) -> FAIL
    risk_pts, risk_pct, passed = auditor.calculate_trade_risk(
        position_points=5.0,
        stop_loss_pct=0.0,
        is_derivative=True,
        derivative_stop_pct=None
    )
    assert risk_pts == 5.00
    assert passed is False


def test_max_position_points_calculation():
    auditor = GateAuditor(total_points=696.50)
    # At 20% stop loss, max allowable points = 3.48 / 0.20 = 17.40 points
    max_pts = auditor.max_position_points_for_stop(0.20)
    assert max_pts == 17.40

    # At 10% stop loss, max allowable points = 3.48 / 0.10 = 34.80 points
    max_pts = auditor.max_position_points_for_stop(0.10)
    assert max_pts == 34.80


def test_stop_loss_breach_detection():
    auditor = GateAuditor(total_points=696.50)

    # 1. AEHR hard stop @ $72.00 (Emergency P2)
    aehr = Holding(
        symbol="AEHR",
        name="Aehr Test Systems",
        points=9.50,
        account="revolut_invest",
        classification="High-Beta Satellite",
        stop_loss=StopLossConfig(price=72.00, type="emergency_hard_stop", action="exit_immediately")
    )
    quote_breach = MarketQuote(symbol="AEHR", price=71.50, timestamp=datetime.now(timezone.utc))
    alert = auditor.audit_holding_stop_loss(aehr, quote_breach)
    assert alert is not None
    assert alert.severity == AlertSeverity.P2
    assert "STOP LOSS BREACH" in alert.title

    quote_safe = MarketQuote(symbol="AEHR", price=73.50, timestamp=datetime.now(timezone.utc))
    assert auditor.audit_holding_stop_loss(aehr, quote_safe) is None

    # 2. XE hard stop @ $11.80 (Emergency P2)
    xe = Holding(
        symbol="XE",
        name="X-Energy",
        points=10.00,
        account="revolut_invest",
        classification="SMR Nuclear",
        stop_loss=StopLossConfig(price=11.80, type="hard_stop")
    )
    quote_xe_breach = MarketQuote(symbol="XE", price=11.75, timestamp=datetime.now(timezone.utc))
    alert_xe = auditor.audit_holding_stop_loss(xe, quote_xe_breach)
    assert alert_xe is not None
    assert alert_xe.severity == AlertSeverity.P2


def test_profit_trim_audit():
    auditor = GateAuditor(total_points=696.50)

    # Oracle trim @ $160.00
    orcl = Holding(
        symbol="ORCL",
        name="Oracle Corp",
        points=37.50,
        account="revolut_invest",
        classification="Core Tech",
        limit_sell=LimitSellConfig(target_price=160.00, points_to_trim=7.50, target_core_points=30.00)
    )
    quote_below = MarketQuote(symbol="ORCL", price=159.00, timestamp=datetime.now(timezone.utc))
    assert auditor.audit_holding_profit_trims(orcl, quote_below) == []

    quote_hit = MarketQuote(symbol="ORCL", price=160.25, timestamp=datetime.now(timezone.utc))
    alerts = auditor.audit_holding_profit_trims(orcl, quote_hit)
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.P1
    assert "PROFIT TRIM TARGET REACHED" in alerts[0].title


def test_resting_limit_buy_audit():
    auditor = GateAuditor(total_points=696.50)

    # INVE B Tranche 2 limit @ 402.50 SEK
    order = RestingOrder(
        symbol="INVE-B.ST",
        name="Investor AB Tranche 2",
        account="avanza_isk",
        order_type="limit_buy",
        target_price=402.50,
        points=7.50,
        currency="SEK"
    )
    quote_not_reached = MarketQuote(symbol="INVE-B.ST", price=405.00, currency="SEK", timestamp=datetime.now(timezone.utc))
    assert auditor.audit_resting_order(order, quote_not_reached) is None

    quote_filled = MarketQuote(symbol="INVE-B.ST", price=402.00, currency="SEK", timestamp=datetime.now(timezone.utc))
    alert = auditor.audit_resting_order(order, quote_filled)
    assert alert is not None
    assert alert.severity == AlertSeverity.P1
    assert "RESTING LIMIT BUY REACHED" in alert.title


def test_ten_gates_full_evaluation():
    auditor = GateAuditor(total_points=696.50)
    financials = {
        "beta": 1.10,
        "roic": 0.20,
        "wacc": 0.08,
        "altman_z": 3.80,
        "piotroski_f": 8,
        "intrinsic_value": 150.00,
        "annual_volatility": 0.24,
        "z_quality": 1.2,
        "z_value": 0.8,
        "z_momentum": 0.6,
        "z_growth": 0.7,
        "z_volatility": 0.2,
        "sloan_accrual": -0.05,
        "beneish_m": -2.60,
        "fcf_to_net_income": 0.95,
        "croic": 0.16,
        "net_debt_ebitda": 1.10,
        "interest_coverage": 9.0,
        "share_dilution_yoy": 0.005,
        "shareholder_yield": 0.03,
        "short_interest_pct": 2.0,
        "adv_usd_millions": 60.0
    }
    result = auditor.run_full_ten_gates("HIGHQ", "High Quality Corp", current_price=100.00, currency="USD", financials=financials)
    assert result.overall_passed is True
    assert len(result.gate_results) == 10
    assert result.margin_of_safety_pct == 33.33  # (150 - 100) / 150
    assert result.gate_results[0].passed is True  # Gate 1
    assert result.gate_results[1].passed is True  # Gate 2
    assert result.gate_results[2].passed is True  # Gate 3
    assert result.gate_results[3].passed is True  # Gate 4
    assert result.gate_results[4].passed is True  # Gate 5
    assert result.gate_results[5].passed is True  # Gate 6
    assert result.gate_results[6].passed is True  # Gate 7
    assert result.gate_results[7].passed is True  # Gate 8
    assert result.gate_results[8].passed is True  # Gate 9
    assert result.gate_results[9].passed is True  # Gate 10


def test_trailing_volatility_stop_arm():
    auditor = GateAuditor(total_points=696.50)
    arm = Holding(
        symbol="ARM",
        name="ARM Holdings",
        points=8.50,
        account="avanza_kf",
        classification="Semiconductor Architecture",
        cost_basis=145.00,
        stop_loss=StopLossConfig(type="volatility_pct", trailing_pct=0.15, action="exit_position")
    )
    # Case 1: Drop from 145 to 135 (6.9% drop < 15%) -> No alert
    quote_safe = MarketQuote(symbol="ARM", price=135.00, previous_close=145.00, timestamp=datetime.now(timezone.utc))
    assert auditor.audit_holding_stop_loss(arm, quote_safe) is None

    # Case 2: Drop from 145 to 120 (17.2% drop >= 15%) -> Alert triggered
    quote_breach = MarketQuote(symbol="ARM", price=120.00, previous_close=145.00, timestamp=datetime.now(timezone.utc))
    alert = auditor.audit_holding_stop_loss(arm, quote_breach)
    assert alert is not None
    assert alert.severity == AlertSeverity.P1
    assert "TRAILING VOLATILITY STOP BREACH" in alert.title


def test_percentage_from_fill_axti():
    auditor = GateAuditor(total_points=696.50)
    axti = Holding(
        symbol="AXTI",
        name="AXT Inc",
        points=4.00,
        account="revolut_invest",
        classification="Optical Substrates",
        cost_basis=2.45,
        stop_loss=StopLossConfig(type="percentage_from_fill", percentage=0.20, action="exit_position")
    )
    # Safe quote at $2.20 (-10.2% < 20%)
    q_safe = MarketQuote(symbol="AXTI", price=2.20, timestamp=datetime.now(timezone.utc))
    assert auditor.audit_holding_stop_loss(axti, q_safe) is None

    # Breach quote at $1.90 (-22.4% >= 20%)
    q_breach = MarketQuote(symbol="AXTI", price=1.90, timestamp=datetime.now(timezone.utc))
    alert = auditor.audit_holding_stop_loss(axti, q_breach)
    assert alert is not None
    assert alert.severity == AlertSeverity.P2
    assert "DRAWDOWN STOP BREACH" in alert.title
