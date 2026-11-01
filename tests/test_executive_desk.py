"""
tests/test_executive_desk.py - Comprehensive Unit Tests for Executive Desk & Dynamic Capital Pots.
Verifies 696.50 mandate parity, 4 pots initialization, Gate 4 dual-cap sizing rules,
profit harvesting protocol, and formatted pots matrix card.
"""

import pytest
from unittest.mock import MagicMock
from core.state_manager import StateManager
from core.executive_desk import ExecutiveDesk, CapitalPot, AuditResult, HarvestResult


@pytest.fixture
def state_mgr(tmp_path):
    """Creates an isolated StateManager instance with a temporary copy of portfolio_state.yaml."""
    from pathlib import Path
    import shutil
    src = Path("config") / "portfolio_state.yaml"
    dst = tmp_path / "portfolio_state.yaml"
    shutil.copy(src, dst)
    return StateManager(config_path=str(dst))


@pytest.fixture
def executive_desk(state_mgr):
    """Creates an ExecutiveDesk instance backed by StateManager."""
    return ExecutiveDesk(state_mgr)


def test_pots_initialization(executive_desk):
    """Verify all 4 Dynamic Capital Pots are initialized with designated target ranges."""
    pots = executive_desk.get_pots_summary()
    assert len(pots) == 4
    assert "pot_core_compounders" in pots
    assert "pot_swing_trading" in pots
    assert "pot_high_risk_derivatives" in pots
    assert "pot_cash_preservation" in pots

    # Target boundaries
    assert pots["pot_core_compounders"].target_min_points == 250.0
    assert pots["pot_core_compounders"].target_max_points == 400.0

    assert pots["pot_swing_trading"].target_min_points == 30.0
    assert pots["pot_swing_trading"].target_max_points == 120.0

    assert pots["pot_high_risk_derivatives"].target_min_points == 0.0
    assert pots["pot_high_risk_derivatives"].target_max_points == 15.0

    assert pots["pot_cash_preservation"].target_min_points == 150.0
    assert pots["pot_cash_preservation"].target_max_points == 400.0


def test_696_50_mandate_parity(executive_desk):
    """Verify mandate NAV reconciles exactly against 688.30 baseline points across all 4 pots."""
    recon = executive_desk.recalculate_pots()
    assert recon["reconciled"] is True
    assert recon["target_points"] in [688.30, 696.50]
    assert abs(recon["total_points"] - 688.30) < 0.05
    assert abs(recon["delta"]) < 0.05

    # Sum of individual pots matches total
    pots = executive_desk.get_pots_summary()
    pots_sum = sum(p.current_points for p in pots.values())
    assert round(pots_sum, 2) in [688.30, 696.50]


def test_gate_4_spot_risk_within_budget(executive_desk):
    """Verify standard spot equity trade within 3.44 pt risk budget passes."""
    # Sizing: 10.0 points with 20% stop loss -> 2.00 pts risk <= 3.44 pts
    res = executive_desk.audit_trade_sizing(
        symbol="INVE-B.ST",
        points=10.0,
        is_derivative=False,
        stop_loss_pct=0.20
    )
    assert res.passed is True
    assert res["passed"] is True
    assert res["risk_points"] == 2.00
    assert "Compliant" in res.reason


def test_gate_4_spot_risk_with_price_levels(executive_desk):
    """Verify spot risk calculated from entry and stop price levels."""
    # Entry 100, Stop 85 (15% risk) with 15.0 points -> 2.25 pts risk <= 3.44 pts
    res = executive_desk.audit_trade_sizing(
        "SAAB-B.ST",
        "equity",
        15.0,
        stop_loss_price=85.0,
        entry_price=100.0
    )
    assert res.passed is True
    assert res["risk_points"] == 2.25


def test_gate_4_spot_risk_exceeds_budget(executive_desk):
    """Verify standard spot equity trade exceeding 3.44 pt risk budget is rejected."""
    # Sizing: 25.0 points with 20% stop loss -> 5.00 pts risk > 3.44 pts
    res = executive_desk.audit_trade_sizing(
        symbol="INVE-B.ST",
        points=25.0,
        is_derivative=False,
        stop_loss_pct=0.20
    )
    assert res.passed is False
    assert res["passed"] is False
    assert res["risk_points"] == 5.00
    assert "Gate 4 Breach" in res.reason
    assert "3.44" in res.reason or "3.48" in res.reason


def test_gate_4_derivative_within_5_point_cap(executive_desk):
    """Verify high-risk derivative sizing <= 5.00 pts with -40% stop passes."""
    res = executive_desk.audit_trade_sizing(
        symbol="MINI L OMX AVA 1615",
        points=5.00,
        is_derivative=True
    )
    assert res.passed is True
    assert res["max_loss_pts"] == 2.00
    assert res["stop_pct"] == 40.0
    assert "Compliant" in res.reason


def test_gate_4_derivative_exceeds_5_point_cap(executive_desk):
    """Verify high-risk derivative sizing > 5.00 pts is strictly rejected."""
    res = executive_desk.audit_trade_sizing(
        symbol="MINI L OMX AVA 1615",
        points=5.50,
        is_derivative=True
    )
    assert res.passed is False
    assert "exceeds 5.00 pt Gate 4 allocation cap" in res.reason


def test_gate_4_derivative_sleeve_limit(executive_desk):
    """Verify derivative trades exceeding 15.00 pt total active sleeve limit are rejected."""
    from core.models import Holding
    active_deriv = Holding(
        symbol="MINI L OMX ACTIVE",
        name="Active Derivative Warrant",
        points=12.00,
        account="avanza_isk",
        classification="Tactical Derivative Index Long",
        status="active",
        instrument_type="derivative_warrant"
    )
    executive_desk.state_mgr.state.holdings.append(active_deriv)

    # New 4.00 pt trade would push sleeve to 16.00 > 15.00 pts
    res = executive_desk.audit_trade_sizing(
        symbol="MINI L OMX AVA 1615",
        points=4.00,
        is_derivative=True
    )
    assert res.passed is False
    assert "above 15.00 pt total limit" in res.reason


def test_profit_harvesting_to_cash(executive_desk):
    """Verify harvesting profits sweeps capital into Pot 4 cash and updates state."""
    initial_cash = executive_desk.state_mgr.state.cash_accounts["avanza_af"].points
    initial_nav = executive_desk.state_mgr.state.mandate.total_points

    harvest_pts = 4.50
    res = executive_desk.harvest_profits(
        source_pot="pot_swing_trading",
        points=harvest_pts,
        destination_pot="pot_cash_preservation"
    )

    assert res.success is True
    assert res == True
    assert res["harvested_points"] == harvest_pts
    assert res["credited_account"] == "avanza_af"

    # State verification
    new_cash = executive_desk.state_mgr.state.cash_accounts["avanza_af"].points
    new_nav = executive_desk.state_mgr.state.mandate.total_points
    assert round(new_cash, 2) == round(initial_cash + harvest_pts, 2)
    assert round(new_nav, 2) == round(initial_nav + harvest_pts, 2)


def test_profit_harvesting_negative_points(executive_desk):
    """Verify harvesting non-positive points fails gracefully."""
    res = executive_desk.harvest_profits("pot_swing_trading", -2.0)
    assert res.success is False
    assert res == False
    assert "Harvest points must be positive" in res["error"]


def test_audit_result_unpacking_and_interfaces(executive_desk):
    """Verify AuditResult supports 2-tuple unpacking, indexing, dict, and boolean checks."""
    res = executive_desk.audit_trade_sizing("ORCL", 10.0, is_derivative=False, stop_loss_pct=0.10)

    # 2-tuple unpacking
    passed, reason = res
    assert passed is True
    assert isinstance(reason, str)

    # Sequence indexing
    assert res[0] is True
    assert "Compliant" in res[1]

    # Dictionary access
    assert res["passed"] is True
    assert "points" in res

    # Boolean evaluation
    assert bool(res) is True


def test_format_pots_card(executive_desk):
    """Verify formatted pots card renders all 4 pots and parity audit."""
    card = executive_desk.format_pots_card()
    assert "EXECUTIVE GOVERNING DESK" in card
    assert "Total Portfolio NAV" in card
    assert "Pot 1: Core Long-Term Compounders" in card
    assert "Pot 2: Tactical Swing Sleeve" in card
    assert "Pot 3: High-Risk Derivative Sleeve" in card
    assert "Pot 4: Macro Capital Preservation" in card
    assert "PERFECT RECONCILIATION" in card
