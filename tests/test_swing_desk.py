"""
tests/test_swing_desk.py - Comprehensive Unit Tests for SwingDesk.

Verifies:
1. Sizing audit: standard spot equity trades strictly capped at <= 3.48 points single-trade risk budget.
2. 2-Stage profit management:
   - Target 1 (TP1): trim 50% + automatically ratchet stop-loss to entry price (breakeven).
   - Target 2 (TP2): trailing ATR ratchet for remaining 50%.
3. Stop loss breach detection and full liquidation.
4. Telegram formatted output for /swing <SYM>.
"""

import pytest
from core.models import Holding, StopLossConfig, LimitSellConfig, StopLossType
from core.swing_desk import SwingDesk, SwingActionType


def test_spot_risk_budget_sizing_audit():
    desk = SwingDesk()

    # Case 1: 20.0 pts position with a 10% stop -> Risk = 2.00 pts <= 3.48 pts -> PASS
    res1 = desk.audit_spot_risk(symbol="AXTI", points=20.0, entry_price=10.0, stop_price=9.0)
    assert res1["passed"] is True
    assert res1["risk_points"] == 2.00

    # Case 2: Exactly 3.44 pts risk -> PASS
    res2 = desk.audit_spot_risk(symbol="AEHR", points=34.4, entry_price=10.0, stop_price=9.0)
    assert res2["passed"] is True
    assert res2["risk_points"] == 3.44

    # Case 3: 35.0 pts position with a 10% stop -> Risk = 3.50 pts > 3.48 pts -> FAIL
    res3 = desk.audit_spot_risk(symbol="SINCH.ST", points=35.0, entry_price=10.0, stop_price=9.0)
    assert res3["passed"] is False
    assert res3["risk_points"] == 3.50
    assert "Gate 4 Risk Breach" in res3["reason"]


def test_tp1_execution_50_percent_trim_and_breakeven_ratchet():
    desk = SwingDesk()

    holding = Holding(
        symbol="GRAB",
        name="Grab Holdings",
        points=10.0,
        account="revolut_invest",
        classification="Tactical Swing",
        cost_basis=4.00,
        current_price=4.00,
        stop_loss=StopLossConfig(price=3.60, type="hard_stop"),
        limit_sell=LimitSellConfig(target_price=4.40, points_to_trim=5.0)
    )

    # Initial state check: holding price at 4.20 (between entry and TP1)
    act_hold = desk.evaluate_holding(holding, current_price=4.20)
    assert act_hold.action_type == SwingActionType.HOLD

    # Price advances to TP1 (4.40)
    act_tp1 = desk.evaluate_holding(holding, current_price=4.45)
    assert act_tp1.action_type == SwingActionType.TRIM_TP1
    assert act_tp1.points_to_trim == 5.00  # 50% of 10.0 pts trimmed
    assert act_tp1.new_stop_price == 4.00  # Stop ratcheted to entry price (breakeven)
    assert act_tp1.stop_ratcheted is True
    assert "Target 1 (TP1) hit" in act_tp1.reason

    # Holding's stop loss should now be updated to breakeven
    assert holding.stop_loss.price == 4.00
    assert holding.stop_loss.type == StopLossType.BREAKEVEN.value


def test_tp2_trailing_atr_ratchet():
    desk = SwingDesk()

    holding = Holding(
        symbol="BKNG",
        name="Booking Holdings",
        points=6.0,
        account="revolut_invest",
        classification="Tactical Swing",
        cost_basis=100.0,
        current_price=100.0,
        stop_loss=StopLossConfig(price=92.0, type="hard_stop"),
        limit_sell=LimitSellConfig(target_price=110.0, points_to_trim=3.0)
    )

    # 1. Trigger TP1 at 110.0
    act_tp1 = desk.evaluate_holding(holding, current_price=110.0, atr_14=3.0)
    assert act_tp1.action_type == SwingActionType.TRIM_TP1
    assert act_tp1.new_stop_price == 100.0  # Breakeven

    # 2. Price surges further to 120.0 with ATR = 3.0
    # Highest price = 120.0. Trailing stop = 120.0 - (2.0 * 3.0) = 114.0
    act_trail = desk.evaluate_holding(holding, current_price=120.0, atr_14=3.0)
    assert act_trail.action_type == SwingActionType.TRAIL_TP2
    assert act_trail.new_stop_price == 114.0  # Ratcheted from 100.0 to 114.0
    assert act_trail.stop_ratcheted is True

    # 3. Pullback to 116.0 (still above 114.0 stop)
    act_pullback = desk.evaluate_holding(holding, current_price=116.0, atr_14=3.0)
    # Stop should NOT move down
    assert act_pullback.action_type == SwingActionType.HOLD
    assert act_pullback.new_stop_price == 114.0


def test_stop_breach_detection():
    desk = SwingDesk()

    holding = Holding(
        symbol="AEHR",
        name="Aehr Test Systems",
        points=4.0,
        account="revolut_invest",
        classification="Tactical Swing",
        cost_basis=20.0,
        current_price=20.0,
        stop_loss=StopLossConfig(price=18.0, type="hard_stop")
    )

    # Price breaches stop loss (drops to 17.50 <= 18.00)
    act_breach = desk.evaluate_holding(holding, current_price=17.50)
    assert act_breach.action_type == SwingActionType.STOP_BREACH
    assert act_breach.points_to_trim == 4.0  # Full exit
    assert "Stop Loss Breached" in act_breach.reason


def test_format_swing_card():
    desk = SwingDesk()

    holding = Holding(
        symbol="AXTI",
        name="AXT Inc",
        points=4.00,
        account="revolut_invest",
        classification="Tactical Swing",
        cost_basis=2.45,
        current_price=2.65,
        stop_loss=StopLossConfig(price=2.25, type="hard_stop"),
        limit_sell=LimitSellConfig(target_price=2.90, points_to_trim=2.0)
    )

    card = desk.format_swing_card("AXTI", holding=holding, current_price=2.65)
    assert "TACTICAL SWING DESK — AXTI" in card
    assert "Market Price:" in card
    assert "Active Stop:" in card
    assert "Target 1 (TP1):" in card
    assert "Target 2 (TP2):" in card
    assert "3.44 pts" in card
    assert "Risk Exposure:" in card


def test_swing_trade_registration_and_state():
    desk = SwingDesk()
    state = desk.register_swing_trade(
        symbol="GRAB",
        entry_price=4.00,
        stop_price=3.60,
        points=10.0,
        tp1_price=4.60,
        tp2_price=5.20,
        atr_14=0.20
    )
    assert state.symbol == "GRAB"
    assert state.tp1_price == 4.60
    assert state.tp2_price == 5.20
    assert state.remaining_points == 10.0
    assert desk.get_trade_state("GRAB") is not None


def test_format_swing_card_no_holding():
    desk = SwingDesk()
    card = desk.format_swing_card("UNKNOWN")
    assert "TACTICAL SWING DESK — UNKNOWN" in card
    assert "No active swing trade or holding found" in card


def test_swing_desk_default_tp_prices():
    desk = SwingDesk()
    state = desk.register_swing_trade(
        symbol="TEST",
        entry_price=100.0,
        stop_price=90.0,
        points=5.0
    )
    # Default TP1 = entry + 1.5 * risk = 100 + 15 = 115.0
    assert state.tp1_price == 115.0
    # Default TP2 = entry + 3.0 * risk = 100 + 30 = 130.0
    assert state.tp2_price == 130.0

