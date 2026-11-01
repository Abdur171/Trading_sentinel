"""
tests/test_quant_risk.py - Unit Tests for Hedge Fund Quantitative Risk Architecture.
"""

import pytest
from pathlib import Path
import shutil

from core.quant_risk import QuantRisk
from core.state_manager import StateManager
from core.models import Holding, CashAccount, StopLossConfig
from feeds.mock_feed import MockFeed


@pytest.fixture
def quant_risk():
    return QuantRisk(initial_hwm=696.50)


@pytest.fixture
def state_mgr(tmp_path):
    orig_config = Path(__file__).resolve().parent.parent / "config" / "portfolio_state.yaml"
    test_config = tmp_path / "test_portfolio_state.yaml"
    shutil.copy(orig_config, test_config)
    return StateManager(str(test_config))


def test_atr_stop_noise_bands(quant_risk):
    # 1. k = 1.5x ATR (< 2.0x noise band) -> TOO_TIGHT
    res_tight = quant_risk.evaluate_atr_stop(
        symbol="TEST",
        current_price=100.00,
        stop_price=98.50,
        atr_14=1.00
    )
    assert res_tight["status"] == "TOO_TIGHT"
    assert res_tight["k_atr"] == 1.50
    assert "vulnerable to noise knock-out" in res_tight["recommendation"]

    # 2. k = 2.5x ATR (within 2.0x - 3.5x band) -> CALIBRATED
    res_calibrated = quant_risk.evaluate_atr_stop(
        symbol="TEST",
        current_price=100.00,
        stop_price=97.50,
        atr_14=1.00
    )
    assert res_calibrated["status"] == "CALIBRATED"
    assert res_calibrated["k_atr"] == 2.50
    assert "well-calibrated" in res_calibrated["recommendation"]

    # 3. k = 4.5x ATR (> 3.5x band) -> LOOSE
    res_loose = quant_risk.evaluate_atr_stop(
        symbol="TEST",
        current_price=100.00,
        stop_price=95.50,
        atr_14=1.00
    )
    assert res_loose["status"] == "LOOSE"
    assert res_loose["k_atr"] == 4.50

    # 4. Stop breached
    res_breach = quant_risk.evaluate_atr_stop(
        symbol="TEST",
        current_price=95.00,
        stop_price=96.00,
        atr_14=1.00
    )
    assert res_breach["status"] == "STOP_BREACHED"


def test_atr_14_calculation():
    highs = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 11.0, 10.9, 11.3, 11.8, 12.0, 11.9, 12.2, 12.5, 12.4]
    lows = [9.5, 9.8, 10.2, 10.1, 10.5, 10.8, 10.4, 10.2, 10.7, 11.1, 11.4, 11.2, 11.5, 11.8, 11.7]
    closes = [9.8, 10.3, 10.7, 10.5, 11.0, 11.2, 10.7, 10.8, 11.2, 11.6, 11.8, 11.5, 12.0, 12.1, 12.0]

    atr = QuantRisk.calculate_atr_14(highs, lows, closes, period=14)
    assert atr > 0.5
    assert atr < 1.5


def test_thematic_factor_clusters(quant_risk, state_mgr):
    holdings = state_mgr.get_all_active_holdings()
    cash = state_mgr.state.cash_accounts

    res = quant_risk.calculate_cluster_breakdown(holdings, cash, total_nav=696.50)

    assert "total_nav" in res
    clusters = res["clusters"]

    # Verify all 5 thematic clusters exist
    assert "cluster_a" in clusters  # AI Compute & Power
    assert "cluster_b" in clusters  # Nordic Industrials
    assert "cluster_c" in clusters  # Defensive Yield & Preservation
    assert "cluster_d" in clusters  # Turnarounds & Satellites
    assert "cluster_e" in clusters  # Materials & Spinoffs

    # Cluster C must include cash from Avanza AF (290.0 pts) and Avanza KF (4.5 pts) + Gold (18.7 pts) + RSG (22.3 pts)
    assert clusters["cluster_c"]["points"] >= 335.0
    assert clusters["cluster_c"]["is_high_beta"] is False

    # In standard mandate state, Cluster A (AI Power) is well diversified
    assert clusters["cluster_a"]["is_high_beta"] is True
    assert clusters["cluster_a"]["points"] > 50.0


def test_cluster_concentration_warning(quant_risk):
    # Simulate an overweight Cluster A holding of 260 points (> 25% or 35% of NAV)
    mock_holdings = [
        Holding(
            symbol="CEG",
            name="Constellation Energy",
            points=130.0,
            account="avanza_kf",
            classification="AI Power",
            status="active"
        ),
        Holding(
            symbol="ORCL",
            name="Oracle Corp",
            points=130.0,
            account="revolut_invest",
            classification="AI Cloud",
            status="active"
        )
    ]
    cash = {"avanza_af": CashAccount(account_name="AF", points=436.50, purpose="Cash")}

    res = quant_risk.calculate_cluster_breakdown(mock_holdings, cash, total_nav=696.50)
    assert res["has_warnings"] is True
    assert any("exceeding the" in w for w in res["concentration_warnings"])


def test_macro_defensive_shield(quant_risk):
    # 1. Normal conditions: no trigger
    res_normal = quant_risk.evaluate_macro_shield({"^TNX": 4.90, "BZ=F": 95.0})
    assert res_normal["shield_active"] is False
    assert res_normal["status"] == "NORMAL_REGIME"

    # 2. US 10Y breach: >= 5.45%
    res_yield = quant_risk.evaluate_macro_shield({"^TNX": 5.50, "BZ=F": 95.0})
    assert res_yield["shield_active"] is True
    assert res_yield["status"] == "DEFENSIVE_SHIELD_ACTIVE"
    assert any("US 10-Year Treasury Yield" in b for b in res_yield["breaches"])
    assert len(res_yield["directives"]) > 0

    # 3. Brent breach: >= $110.00
    res_brent = quant_risk.evaluate_macro_shield({"^TNX": 4.90, "BZ=F": 112.50})
    assert res_brent["shield_active"] is True
    assert any("Brent Crude Oil" in b for b in res_brent["breaches"])


def test_convexity_scorecard(quant_risk, state_mgr):
    feed = MockFeed()
    holdings = state_mgr.get_all_active_holdings()
    symbols = [h.symbol for h in holdings]
    quotes = feed.get_batch_quotes(symbols)

    scorecard = quant_risk.calculate_convexity_scorecard(holdings, quotes)

    assert len(scorecard) > 0
    # Scorecard must be sorted descending by convexity_ratio
    ratios = [s["convexity_ratio"] for s in scorecard]
    assert ratios == sorted(ratios, reverse=True)
    assert "symbol" in scorecard[0]
    assert "convexity_ratio" in scorecard[0]
    assert "margin_of_safety_pct" in scorecard[0]


def test_portfolio_high_water_mark_and_drawdown(quant_risk):
    # Initial HWM is 696.50
    h1 = quant_risk.update_high_water_mark(700.00)
    assert h1["high_water_mark"] == 700.00
    assert h1["drawdown_pct"] == 0.0
    assert h1["alert_level"] is None

    # Drawdown of 6% (700 -> 658) -> ADVISORY_P0
    h2 = quant_risk.update_high_water_mark(658.00)
    assert h2["drawdown_pct"] == 6.00
    assert h2["alert_level"] == "ADVISORY_P0"

    # Drawdown of 12% (700 -> 616) -> WARNING_P1
    h3 = quant_risk.update_high_water_mark(616.00)
    assert h3["drawdown_pct"] == 12.00
    assert h3["alert_level"] == "WARNING_P1"

    # Drawdown of 16% (700 -> 588) -> EMERGENCY_P2
    h4 = quant_risk.update_high_water_mark(588.00)
    assert h4["drawdown_pct"] == 16.00
    assert h4["alert_level"] == "EMERGENCY_P2"
