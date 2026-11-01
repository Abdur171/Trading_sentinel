"""
tests/test_empirical_challenge.py - Adversarial Empirical Stress Test Suite.
Author: Autonomous Trading Sentinel Testing Suite

Challenges:
1. Capital Pot Parity (688.30 pts):
   - Invariant validation across portfolio mutations, rebalances, and liquidations.
   - Monte Carlo stress harness with 500 randomized rebalance cycles.
   - Profit harvesting sweep impact on NAV and reconciliation.
2. Gate 4 Risk Curbs:
   - Spot trades boundary: risk = 3.48 pts vs 3.49 pts.
   - Derivative trades boundary: allocation = 5.00 pts vs 5.01 pts.
   - Derivative downside stop boundary: -40% vs -41% (loss cap <= 2.00 pts).
3. Curfew Enforcement:
   - Timestamp progression at 19:29, 19:35, 19:44, and 19:46 CEST.
   - Escalating alert tiers (None -> P0 Advisory -> P1 Urgent Warning -> P2 Emergency Breach).
   - Weekend and closed/flat position suppression.
"""

import copy
import random
from datetime import datetime
import pytest
import pytz

from core.state_manager import StateManager
from core.executive_desk import ExecutiveDesk
from core.swing_desk import SwingDesk
from core.derivatives_desk import DerivativesDesk
from core.gate_auditor import GateAuditor
from core.curfew_manager import CurfewManager
from core.models import Holding, CurfewConfig, AlertSeverity


# =============================================================================
# 1. CAPITAL POT PARITY (696.50 pts) STRESS TESTS
# =============================================================================

class TestCapitalPotParityStress:
    """Stress tests for the 696.50 pt capital pot parity invariant."""

    @pytest.fixture
    def setup_desk(self, tmp_path):
        """Creates an isolated StateManager and ExecutiveDesk instance."""
        import shutil
        from pathlib import Path
        src = Path("config/portfolio_state.yaml")
        dst = tmp_path / "portfolio_state.yaml"
        shutil.copy(src, dst)

        state_mgr = StateManager(config_path=str(dst))
        desk = ExecutiveDesk(state_mgr=state_mgr)
        return desk, state_mgr

    def test_baseline_parity(self, setup_desk):
        """Test baseline portfolio state strictly reconciles to 688.30 pts."""
        desk, state_mgr = setup_desk
        summary = desk.recalculate_pots()

        assert summary["total_points"] == 688.30, f"Expected 688.30, got {summary['total_points']}"
        assert summary["target_points"] == 688.30
        assert summary["reconciled"] is True
        assert abs(summary["delta"]) < 0.001

        pots = summary["pots"]
        p1 = pots["pot_core_compounders"]["current_points"]
        p2 = pots["pot_swing_trading"]["current_points"]
        p3 = pots["pot_high_risk_derivatives"]["current_points"]
        p4 = pots["pot_cash_preservation"]["current_points"]

        assert round(p1 + p2 + p3 + p4, 2) == 688.30

    def test_internal_rebalance_transfer(self, setup_desk):
        """Test reallocating capital between cash and a holding preserves 688.30 pts."""
        desk, state_mgr = setup_desk

        # Reallocate 10.00 pts from Avanza AF cash to Pot 2 holding (SINCH.ST)
        state_mgr.state.cash_accounts["avanza_af"].points -= 10.00
        sinch = state_mgr.get_holding("SINCH.ST")
        assert sinch is not None
        sinch.points += 10.00

        summary = desk.recalculate_pots()
        assert summary["total_points"] == 688.30
        assert summary["reconciled"] is True
        assert abs(summary["delta"]) < 0.001

    def test_position_liquidation_to_cash(self, setup_desk):
        """Test closing/liquidating an active holding into cash preserves 688.30 pts."""
        desk, state_mgr = setup_desk

        # Liquidate ORCL (34.70 pts) into revolut_invest cash
        orcl = state_mgr.get_holding("ORCL")
        assert orcl is not None
        liquidated_pts = orcl.points
        orcl.points = 0.00
        orcl.status = "liquidated"
        state_mgr.state.cash_accounts["revolut_invest"].points += liquidated_pts

        summary = desk.recalculate_pots()
        assert summary["total_points"] == 688.30
        assert summary["reconciled"] is True
        assert abs(summary["delta"]) < 0.001

    def test_monte_carlo_500_random_rebalances(self, setup_desk):
        """Stress-test 500 consecutive random mutations to ensure no floating point drift."""
        desk, state_mgr = setup_desk
        random.seed(42)

        active_holdings = [h for h in state_mgr.state.holdings if h.status == "active"]
        cash_keys = list(state_mgr.state.cash_accounts.keys())

        for step in range(500):
            # Pick a random source and target
            source_holding = random.choice(active_holdings)
            target_cash = state_mgr.state.cash_accounts[random.choice(cash_keys)]

            # Transfer a random amount (up to 2.00 pts)
            transfer_amount = round(random.uniform(0.01, min(2.00, max(0.01, source_holding.points * 0.5))), 2)

            if random.random() < 0.5 and source_holding.points > transfer_amount:
                # Holding -> Cash
                source_holding.points = round(source_holding.points - transfer_amount, 2)
                target_cash.points = round(target_cash.points + transfer_amount, 2)
            else:
                # Cash -> Holding
                if target_cash.points > transfer_amount:
                    target_cash.points = round(target_cash.points - transfer_amount, 2)
                    source_holding.points = round(source_holding.points + transfer_amount, 2)

            summary = desk.recalculate_pots()
            assert abs(summary["total_points"] - 688.30) < 0.05, (
                f"Step {step} drifted: {summary['total_points']} vs 688.30"
            )

    def test_profit_harvesting_sweep_nav_impact(self, setup_desk):
        """
        Challenge Profit Harvesting Sweep:
        When ExecutiveDesk.harvest_profits() is called, observe whether mandate NAV expands
        from 688.30 pts to 698.30 pts.
        """
        desk, state_mgr = setup_desk
        initial_nav = state_mgr.state.mandate.total_points
        assert initial_nav == 688.30

        # Harvest 10.00 pts profit from Pot 2 into Pot 4
        result = desk.harvest_profits("pot_swing_trading", 10.00, "pot_cash_preservation")
        assert result.success is True

        summary = desk.recalculate_pots()
        # Verify: harvest_profits increases cash account and updates mandate total
        assert summary["total_points"] == 698.30
        assert summary["target_points"] == 698.30
        assert summary["reconciled"] is True


# =============================================================================
# 2. GATE 4 RISK CURBS BOUNDARY STRESS TESTS
# =============================================================================

class TestGate4RiskCurbsStress:
    """Stress tests for Gate 4 spot equity and derivative boundary conditions."""

    @pytest.fixture
    def desk(self, tmp_path):
        import shutil
        from pathlib import Path
        src = Path("config/portfolio_state.yaml")
        dst = tmp_path / "portfolio_state.yaml"
        shutil.copy(src, dst)
        return ExecutiveDesk(state_mgr=StateManager(config_path=str(dst)))

    # --- Spot Equities: 3.44 pts vs 3.45 pts ---

    def test_spot_risk_boundary_exact_348(self, desk):
        """Spot trade risk at exactly 3.44 pts MUST pass."""
        # 17.20 pts position * 20% stop loss = 3.44 pts risk
        res_exec = desk.audit_trade_sizing("ERIC-B.ST", 17.20, stop_loss_pct=0.20, is_derivative=False)
        assert res_exec.passed is True
        assert res_exec["risk_points"] == 3.44

        swing = SwingDesk()
        res_swing = swing.audit_spot_risk("ERIC-B.ST", points=17.20, entry_price=100.0, stop_price=80.0)
        assert res_swing["passed"] is True
        assert res_swing["risk_points"] == 3.44

        gate = GateAuditor()
        risk_pts, risk_pct, passed = gate.calculate_trade_risk(17.20, 0.20, is_derivative=False)
        assert passed is True
        assert round(risk_pts, 2) == 3.44

    def test_spot_risk_boundary_breach_349(self, desk):
        """Spot trade risk at 3.45 pts MUST fail."""
        # 17.25 pts position * 20% stop loss = 3.45 pts risk
        res_exec = desk.audit_trade_sizing("ERIC-B.ST", 17.25, stop_loss_pct=0.20, is_derivative=False)
        assert res_exec.passed is False
        assert res_exec["risk_points"] == 3.45
        assert "Gate 4 Breach" in res_exec.reason

        swing = SwingDesk()
        res_swing = swing.audit_spot_risk("ERIC-B.ST", points=17.25, entry_price=100.0, stop_price=80.0)
        assert res_swing["passed"] is False
        assert res_swing["risk_points"] == 3.45
        assert "Gate 4 Risk Breach" in res_swing["reason"]

        gate = GateAuditor()
        risk_pts, risk_pct, passed = gate.calculate_trade_risk(17.25, 0.20, is_derivative=False)
        assert passed is False
        assert round(risk_pts, 2) == 3.45

    # --- Derivatives: Allocation Cap 5.00 pts vs 5.01 pts ---

    def test_derivative_allocation_exact_500(self, desk):
        """Derivative single-trade allocation at exactly 5.00 pts MUST pass."""
        res_exec = desk.audit_trade_sizing("MINI L OMX AVA 1615", points=5.00, is_derivative=True)
        assert res_exec.passed is True
        assert res_exec["points"] == 5.00

        deriv = DerivativesDesk()
        res_deriv = deriv.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.00)
        assert res_deriv["passed"] is True
        assert res_deriv["points"] == 5.00

    def test_derivative_allocation_breach_501(self, desk):
        """Derivative single-trade allocation at 5.01 pts MUST fail."""
        res_exec = desk.audit_trade_sizing("MINI L OMX AVA 1615", points=5.01, is_derivative=True)
        assert res_exec.passed is False
        assert "exceeds 5.00 pt Gate 4 allocation cap" in res_exec.reason

        deriv = DerivativesDesk()
        res_deriv = deriv.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.01)
        assert res_deriv["passed"] is False
        assert "exceeds the strict 5.00 pt allocation cap" in res_deriv["reason"]

    # --- Derivatives: Downside Stop -40% vs -41% & Loss Cap <= 2.00 pts ---

    def test_derivative_downside_stop_40_pct_loss_cap(self, desk):
        """
        Derivative trade with 5.00 pts allocation and -40% downside stop:
        Max loss = 5.00 * 0.40 = 2.00 pts.
        MUST satisfy loss cap <= 2.00 pts.
        """
        deriv = DerivativesDesk()
        res_deriv = deriv.audit_derivative_sizing("MINI L OMX AVA 1615", points=5.00)
        assert res_deriv["passed"] is True
        assert res_deriv["max_loss_pts"] == 2.00
        assert res_deriv["loss_capped_at_2pt"] is True

        metrics = deriv.evaluate_warrant(
            symbol="MINI L OMX AVA 1615",
            underlying_price=2600.0,
            warrant_price=10.0,
            financing_level=2470.0,
            parity_ratio=0.05,
            current_points=5.00,
            entry_price=10.0
        )
        assert metrics.hard_stop_price == 6.00  # 10.0 * (1 - 0.40)
        assert metrics.max_loss_pts == 2.00
        assert metrics.passed_cap is True

    def test_derivative_downside_stop_41_pct_loss_cap_challenge(self, desk):
        """
        Adversarial Oracle:
        A derivative trade with 5.00 pts allocation and a -41% downside stop yields
        max loss = 5.00 * 0.41 = 2.05 pts (> 2.00 pt cap).
        Requirement R1 & R5: High-risk derivatives strictly capped at -40% stop (<= 2.00 pts loss).
        This trade MUST be rejected (passed == False).
        """
        res = desk.audit_trade_sizing(
            "MINI L OMX AVA 1615",
            points=5.00,
            is_derivative=True,
            stop_loss_pct=0.41
        )
        assert res["max_loss_pts"] == 2.05
        # Oracle assertion: must fail Gate 4 audit
        assert res.passed is False, (
            f"VULNERABILITY DETECTED: ExecutiveDesk permitted derivative trade with "
            f"-41% downside stop and {res['max_loss_pts']:.2f} pts max loss to pass! "
            f"Reason reported: {res.reason}"
        )


# =============================================================================
# 3. CURFEW ENFORCEMENT TIME PROGRESSION STRESS TESTS
# =============================================================================

class TestCurfewEnforcementStress:
    """Simulates time progression at 19:29, 19:35, 19:44, and 19:46 CEST."""

    @pytest.fixture
    def warrant_holding(self):
        return Holding(
            symbol="MINI L OMX AVA 1615",
            name="MINI L OMX AVA 1615",
            points=5.00,
            account="avanza_isk",
            classification="Derivative Warrant",
            instrument_type="derivative_warrant",
            curfew=CurfewConfig(
                enforced=True,
                cutoff_time="19:45",
                warning_1="19:30",
                warning_2="19:40"
            )
        )

    @pytest.fixture
    def curfew_mgr(self):
        return CurfewManager(timezone_str="Europe/Stockholm")

    @pytest.fixture
    def deriv_desk(self, curfew_mgr):
        return DerivativesDesk(curfew_mgr=curfew_mgr)

    def test_curfew_at_1929_cest_normal(self, warrant_holding, curfew_mgr, deriv_desk):
        """19:29 CEST (1 min before advisory): MUST be Normal, no alerts."""
        st_tz = pytz.timezone("Europe/Stockholm")
        # Weekday: Thursday Oct 8, 2026
        dt_1929 = st_tz.localize(datetime(2026, 10, 8, 19, 29, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_1929)
        assert alert is None, f"Expected no alert at 19:29 CEST, got {alert}"

        desk_status = deriv_desk.check_curfew(current_dt=dt_1929, holding=warrant_holding)
        assert desk_status["status"] == "NORMAL"
        assert desk_status["is_breached"] is False
        assert desk_status["severity"] == AlertSeverity.P0

    def test_curfew_at_1935_cest_advisory(self, warrant_holding, curfew_mgr, deriv_desk):
        """19:35 CEST: Advisory stage (P0)."""
        st_tz = pytz.timezone("Europe/Stockholm")
        dt_1935 = st_tz.localize(datetime(2026, 10, 8, 19, 35, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_1935)
        assert alert is not None
        assert alert.severity == AlertSeverity.P0
        assert "CURFEW ADVISORY" in alert.title
        assert alert.metadata["minutes_left"] == 10

        desk_status = deriv_desk.check_curfew(current_dt=dt_1935, holding=warrant_holding)
        assert desk_status["status"] == "ADVISORY"
        assert desk_status["is_breached"] is False

    def test_curfew_at_1944_cest_urgent_warning(self, warrant_holding, curfew_mgr, deriv_desk):
        """19:44 CEST (1 min before cutoff): High Warning stage (P1)."""
        st_tz = pytz.timezone("Europe/Stockholm")
        dt_1944 = st_tz.localize(datetime(2026, 10, 8, 19, 44, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_1944)
        assert alert is not None
        assert alert.severity == AlertSeverity.P1
        assert "CURFEW CLOSING IN" in alert.title
        assert alert.metadata["minutes_left"] == 1

        desk_status = deriv_desk.check_curfew(current_dt=dt_1944, holding=warrant_holding)
        assert desk_status["status"] == "WARNING"
        assert desk_status["is_breached"] is False

    def test_curfew_at_1946_cest_emergency_breach(self, warrant_holding, curfew_mgr, deriv_desk):
        """19:46 CEST (past 19:45 cutoff): Emergency Flat-Book Breach (P2)."""
        st_tz = pytz.timezone("Europe/Stockholm")
        dt_1946 = st_tz.localize(datetime(2026, 10, 8, 19, 46, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_1946)
        assert alert is not None
        assert alert.severity == AlertSeverity.P2
        assert "EMERGENCY: DERIVATIVE CURFEW BREACH" in alert.title
        assert "MANDATORY FLAT-BOOK PROTOCOL VIOLATION" in alert.message

        desk_status = deriv_desk.check_curfew(current_dt=dt_1946, holding=warrant_holding)
        assert desk_status["status"] == "BREACH"
        assert desk_status["is_breached"] is True
        assert desk_status["severity"] == AlertSeverity.P2

    def test_curfew_suppression_on_weekend(self, warrant_holding, curfew_mgr, deriv_desk):
        """Saturday at 19:46 CEST: Curfew must be suppressed (markets closed)."""
        st_tz = pytz.timezone("Europe/Stockholm")
        dt_sat = st_tz.localize(datetime(2026, 10, 10, 19, 46, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_sat)
        assert alert is None

        desk_status = deriv_desk.check_curfew(current_dt=dt_sat, holding=warrant_holding)
        assert desk_status["status"] == "WEEKEND"
        assert desk_status["is_breached"] is False

    def test_curfew_suppression_on_flat_position(self, warrant_holding, curfew_mgr, deriv_desk):
        """Flat/liquidated position (0.00 pts) at 19:46 CEST must NOT trigger alert."""
        warrant_holding.points = 0.00
        warrant_holding.status = "flat"
        st_tz = pytz.timezone("Europe/Stockholm")
        dt_1946 = st_tz.localize(datetime(2026, 10, 8, 19, 46, 0))

        alert = curfew_mgr.check_derivative_curfew(warrant_holding, current_dt=dt_1946)
        assert alert is None

        desk_status = deriv_desk.check_curfew(current_dt=dt_1946, holding=warrant_holding)
        assert desk_status["is_breached"] is False
