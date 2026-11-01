"""
tests/test_telegram_commands.py - Tests for Telegram Command Grammar, Dispatcher, and Conversational Agent.
"""

import pytest
import shutil
from pathlib import Path
from core.organiser import Organiser
from feeds.mock_feed import MockFeed


@pytest.fixture
def test_organiser(tmp_path):
    orig_config = Path(__file__).resolve().parent.parent / "config" / "portfolio_state.yaml"
    test_config = tmp_path / "test_portfolio_state.yaml"
    shutil.copy(orig_config, test_config)

    feed = MockFeed()
    org = Organiser(config_path=str(test_config), feed=feed, is_test_mode=True)
    org.telegram_bot.allowed_user_ids = ["12345678"]
    return org


def test_command_status(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/status", user_id="12345678")
    assert "Mandate Capital Ledger Status" in resp
    assert "696.50 pts" in resp
    assert "Invested Capital:" in resp
    assert "Liquid Cash Reserves:" in resp


def test_command_stops(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/stops", user_id="12345678")
    assert "Active Stop-Loss Audits" in resp
    assert "MCAP.ST" in resp
    assert "ATCO-B.ST" in resp
    assert "SAAB-B.ST" in resp
    assert "INVE-B.ST" in resp


def test_command_cash(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/cash", user_id="12345678")
    assert "Cash Partitioning" in resp
    assert "Avanza AF:" in resp
    assert "290.00 pts" in resp
    assert "Avanza ISK:" in resp
    assert "31.00 pts" in resp
    assert "Avanza KF:" in resp
    assert "4.50 pts" in resp
    assert "Revolut Invest:" in resp
    assert "56.90 pts" in resp


def test_command_curfew(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/curfew", user_id="12345678")
    assert "Curfew & Time-Sensitivity Watchdog" in resp
    assert "MINI L AMD AVA 117" in resp


def test_command_close_and_sell(test_organiser):
    # Add temporary test position to test /sell and /close
    test_organiser.process_incoming_telegram_message(
        "/add_holding TESTPOS 5.0 avanza_isk 100.0 Test Holding",
        user_id="12345678"
    )
    h = test_organiser.state_mgr.get_holding("TESTPOS")
    assert h is not None
    assert h.status == "active"

    # Close via /sell
    resp_sell = test_organiser.process_incoming_telegram_message("/sell TESTPOS", user_id="12345678")
    assert "Trade Registered: Position Exited" in resp_sell
    assert "TESTPOS" in resp_sell
    assert "5.00 pts" in resp_sell

    # Verify holding is closed and points is 0.0
    h_after = test_organiser.state_mgr.get_holding("TESTPOS")
    assert h_after.status == "closed"
    assert h_after.points == 0.0

    # Verify capital parity is strictly maintained
    recon = test_organiser.state_mgr.reconcile_capital()
    assert recon["parity_reconciled"] is True
    assert abs(recon["calculated_total_points"] - 688.30) < 0.05


def test_natural_language_sell_registration(test_organiser):
    # Add a derivative holding to simulate active state
    from core.models import Holding
    test_deriv = Holding(
        symbol="MINI L OMX ACTIVE",
        name="MINI L OMX ACTIVE",
        points=5.0,
        account="avanza_isk",
        classification="Tactical Derivative",
        instrument_type="derivative_warrant",
        underlying="OMXS30",
        status="active"
    )
    test_organiser.state_mgr.state.holdings.append(test_deriv)
    test_organiser.state_mgr.state.mandate.invested_points += 5.0
    test_organiser.state_mgr.state.cash_accounts["avanza_isk"].points -= 5.0

    # User message from Telegram exactly as sent by user
    msg = "Ok great, you can also register that I sold my derivative position on OMXS on a profit today"
    resp = test_organiser.process_incoming_telegram_message(msg, user_id="12345678")

    assert "Trade Registered: Position Exited" in resp
    assert "Capital Released" in resp
    assert "5.00 pts" in resp
    assert "Avanza ISK" in resp

    # Repeating should inform that it's already closed
    resp_repeat = test_organiser.process_incoming_telegram_message(msg, user_id="12345678")
    assert "Position Already Closed" in resp_repeat


def test_command_trim_holding(test_organiser):
    # Add a test holding
    from core.models import Holding, StopLossConfig
    test_holding = Holding(
        symbol="TRIMTEST",
        name="Trim Test Holding",
        points=10.0,
        account="avanza_isk",
        classification="Tactical Swing",
        status="active",
        cost_basis=100.0,
        stop_loss=StopLossConfig(price=90.0, type="hard_stop", action="exit_position", max_risk_pts=1.0)
    )
    test_organiser.state_mgr.state.holdings.append(test_holding)
    test_organiser.state_mgr.state.mandate.invested_points += 10.0
    initial_isk_cash = test_organiser.state_mgr.state.cash_accounts["avanza_isk"].points
    test_organiser.state_mgr.state.cash_accounts["avanza_isk"].points -= 10.0

    # 1. Partial trim 4.0 pts
    resp = test_organiser.process_incoming_telegram_message("/trim TRIMTEST 4.0", user_id="12345678")
    assert "Trade Registered: Position Trimmed" in resp
    assert "4.00 pts" in resp
    assert "6.00 pts" in resp  # Remaining

    h = test_organiser.state_mgr.get_holding("TRIMTEST")
    assert h.status == "active"
    assert h.points == 6.0
    # Updated cash
    current_isk_cash = test_organiser.state_mgr.state.cash_accounts["avanza_isk"].points
    assert current_isk_cash == (initial_isk_cash - 10.0 + 4.0)

    # Reconciled capital
    recon = test_organiser.state_mgr.reconcile_capital()
    assert recon["parity_reconciled"] is True

    # 2. Trim remaining 6.0 pts (triggers full exit)
    resp2 = test_organiser.process_incoming_telegram_message("/trim TRIMTEST 6.0", user_id="12345678")
    assert "Position Exited (Full Trim)" in resp2
    h_closed = test_organiser.state_mgr.get_holding("TRIMTEST")
    assert h_closed.status == "closed"
    assert h_closed.points == 0.0

    # 3. Error cases
    err1 = test_organiser.process_incoming_telegram_message("/trim NONEXISTENT 2.0", user_id="12345678")
    assert "not found" in err1.lower()


def test_natural_language_trim(test_organiser):
    from core.models import Holding, StopLossConfig
    test_holding = Holding(
        symbol="NLTRIM",
        name="Natural Language Trim Asset",
        points=8.0,
        account="revolut_invest",
        classification="Tactical Swing",
        status="active",
        cost_basis=50.0,
        stop_loss=StopLossConfig(price=45.0, type="hard_stop", action="exit_position", max_risk_pts=0.8)
    )
    test_organiser.state_mgr.state.holdings.append(test_holding)
    test_organiser.state_mgr.state.mandate.invested_points += 8.0
    test_organiser.state_mgr.state.cash_accounts["revolut_invest"].points -= 8.0

    # Natural language trim
    resp = test_organiser.process_incoming_telegram_message("Please trim 3 points of NLTRIM today", user_id="12345678")
    assert "Trade Registered: Position Trimmed" in resp
    assert "3.00 pts" in resp
    assert "5.00 pts" in resp

    h = test_organiser.state_mgr.get_holding("NLTRIM")
    assert h.status == "active"
    assert h.points == 5.0


def test_command_macro(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/macro", user_id="12345678")
    assert "Macroeconomic Transmission Matrix" in resp
    assert "US 10-Year Treasury Yield" in resp


def test_command_analyze(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/analyze VST", user_id="12345678")
    assert "QUANTITATIVE INVESTMENT MEMORANDUM" in resp
    assert "VST" in resp
    assert "Margin of Safety" in resp
    assert "Gate 4" in resp


def test_command_gear(test_organiser):
    resp_active = test_organiser.process_incoming_telegram_message("/gear active", user_id="12345678")
    assert "HIGH GEAR" in resp_active
    assert test_organiser.current_gear == "active"

    resp_idle = test_organiser.process_incoming_telegram_message("/gear idle", user_id="12345678")
    assert "LOW GEAR" in resp_idle
    assert test_organiser.current_gear == "idle"


def test_command_update_stop(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/update_stop AMD 145.00", user_id="12345678")
    assert "Updated stop loss for `AMD` to `145.00`" in resp
    h = test_organiser.state_mgr.get_holding("AMD")
    assert h.stop_loss.price == 145.00


def test_command_update_stop_multi_word_ticker(test_organiser):
    # Tests Swedish multi-word ticker "INVE B" matching "INVE-B.ST"
    resp = test_organiser.process_incoming_telegram_message("/update_stop INVE B 365.00", user_id="12345678")
    assert "Updated stop loss for `INVE B` to `365.00`" in resp
    h = test_organiser.state_mgr.get_holding("INVE B")
    assert h is not None
    assert h.stop_loss.price == 365.00


def test_command_add_holding(test_organiser):
    # Ensure VST is clean in isolated test fixture if already in portfolio
    test_organiser.state_mgr.state.holdings = [h for h in test_organiser.state_mgr.state.holdings if h.symbol != "VST"]
    test_organiser.state_mgr.state.cash_accounts["avanza_kf"].points += 4.6
    test_organiser.state_mgr.state.mandate.invested_points -= 4.6
    test_organiser.state_mgr.state.mandate.cash_points += 4.6

    # Add VST (7.5 pts in KF, stop at 135.00)
    resp = test_organiser.process_incoming_telegram_message(
        "/add_holding VST 3.0 avanza_kf 135.00 Nuclear IPP Infrastructure",
        user_id="12345678"
    )
    assert "New Holding Added to Ledger" in resp
    assert "VST" in resp
    h = test_organiser.state_mgr.get_holding("VST")
    assert h is not None
    assert h.points == 3.0
    assert h.account == "avanza_kf"
    assert h.stop_loss.price == 135.00

    # Verify capital parity is preserved
    recon = test_organiser.state_mgr.reconcile_capital()
    assert recon["parity_reconciled"] is True
    assert abs(recon["calculated_total_points"] - 688.30) < 0.05


def test_command_add_holding_rejections(test_organiser):
    # 1. Insufficient cash
    resp_cash = test_organiser.process_incoming_telegram_message(
        "/add_holding HUGE 100.0 avanza_kf",
        user_id="12345678"
    )
    assert "Insufficient free cash" in resp_cash

    # 2. Gate 4 risk budget exceeded (20 pts with 30% stop -> 6.0 pts risk > 3.48 pts limit)
    resp_gate4 = test_organiser.process_incoming_telegram_message(
        "/add_holding RISK 20.0 avanza_af 50.0",
        user_id="12345678"
    )
    assert "Gate 4 Risk Limit Exceeded" in resp_gate4


def test_command_audit_mandate(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/audit_mandate", user_id="12345678")
    assert "Continuous Mandate Health Audit" in resp
    assert "Parity Reconciled:" in resp


def test_conversational_agent_chat(test_organiser):
    # Natural language query without slash
    resp = test_organiser.process_incoming_telegram_message("How much cash do I have in Avanza AF?", user_id="12345678")
    assert "Avanza AF:" in resp
    assert "290.00 pts" in resp

    # Query for a holding not in original 10-hardcoded list (e.g. Cameco CCJ or ARM)
    resp_ccj = test_organiser.process_incoming_telegram_message("Tell me about Cameco", user_id="12345678")
    assert "Cameco" in resp_ccj or "CCJ" in resp_ccj

    # Gate 4 trade question
    resp_trade = test_organiser.process_incoming_telegram_message("Can I buy 10 points with a 15% stop loss?", user_id="12345678")
    assert "Gate 4 Pre-Trade Risk Audit" in resp_trade
    assert "PASSES GATE 4" in resp_trade


def test_security_authorization_check(test_organiser):
    resp = test_organiser.telegram_bot.handle_incoming_text("/status", user_id="99999999")
    assert "Access Denied" in resp


def test_command_focus_and_unfocus(test_organiser):
    # 1. /focus ARM 60
    resp_focus = test_organiser.process_incoming_telegram_message("/focus ARM 60", user_id="12345678")
    assert "Focused Watch Mode Activated" in resp_focus
    assert "ARM" in resp_focus
    assert "60 minutes" in resp_focus
    assert test_organiser.watch_mgr.is_watching("ARM") is True
    assert test_organiser.current_gear == "active"

    # 2. /unfocus ARM
    resp_unfocus = test_organiser.process_incoming_telegram_message("/unfocus ARM", user_id="12345678")
    assert "Focused Watch Mode Deactivated" in resp_unfocus
    assert "ARM" in resp_unfocus
    assert test_organiser.watch_mgr.is_watching("ARM") is False


def test_command_briefing_and_debrief(test_organiser):
    # /briefing
    resp_briefing = test_organiser.process_incoming_telegram_message("/briefing", user_id="12345678")
    assert "PRE-MARKET MORNING BRIEFING" in resp_briefing
    assert "688.30 pts" in resp_briefing
    assert "Mandate Capital Ledger:" in resp_briefing

    # /debrief
    resp_debrief = test_organiser.process_incoming_telegram_message("/debrief", user_id="12345678")
    assert "POST-MARKET EVENING DEBRIEF" in resp_debrief
    assert "688.30 pts" in resp_debrief
    assert "Cash Partitioning Verification:" in resp_debrief


def test_command_clusters_and_convexity(test_organiser):
    # /clusters
    resp_clusters = test_organiser.process_incoming_telegram_message("/clusters", user_id="12345678")
    assert "Thematic Factor Cluster Allocation Matrix" in resp_clusters
    assert "Cluster A" in resp_clusters
    assert "Cluster B" in resp_clusters
    assert "Cluster C" in resp_clusters

    # /convexity
    resp_conv = test_organiser.process_incoming_telegram_message("/convexity", user_id="12345678")
    assert "Portfolio Convexity & Asymmetry Scorecard" in resp_conv
    assert "Convexity Ratio" in resp_conv


def test_command_gear_query(test_organiser):
    # /gear with no args returns gearbox status
    resp = test_organiser.process_incoming_telegram_message("/gear", user_id="12345678")
    assert "Transmission Gearbox Status" in resp
    assert "Current Operational Gear:" in resp


def test_natural_language_watch_and_quant_queries(test_organiser):
    # 1. Natural language watch command: "Watch ARM closely"
    resp_watch = test_organiser.process_incoming_telegram_message("Watch ARM closely", user_id="12345678")
    assert "Focused Watch Mode Activated" in resp_watch
    assert "ARM" in resp_watch
    assert test_organiser.watch_mgr.is_watching("ARM") is True

    # 2. Natural language reset: "Stop watching"
    resp_stop = test_organiser.process_incoming_telegram_message("Stop watching", user_id="12345678")
    assert "Focused Watch Mode Reset" in resp_stop
    assert test_organiser.watch_mgr.is_watching("ARM") is False

    # 3. Natural language cluster inquiry: "Show me cluster risk"
    resp_clust = test_organiser.process_incoming_telegram_message("Show me cluster risk", user_id="12345678")
    assert "Thematic Factor Cluster Analysis" in resp_clust
    assert "Cluster A:" in resp_clust

    # 4. Natural language ATR stop inquiry: "Are my stops too tight?"
    resp_stops = test_organiser.process_incoming_telegram_message("Are my stops too tight?", user_id="12345678")
    assert "ATR-Calibrated Stop Loss Volatility Audit" in resp_stops


def test_command_focus_multi_word_ticker(test_organiser):
    # 1. Focus multi-word Swedish ticker "INVE B" with explicit minutes
    resp = test_organiser.process_incoming_telegram_message("/focus INVE B 45", user_id="12345678")
    assert "Focused Watch Mode Activated" in resp
    assert "INVE B" in resp
    assert "45 minutes" in resp
    assert test_organiser.watch_mgr.is_watching("INVE B") is True

    # 2. Unfocus multi-word ticker
    resp_unfocus = test_organiser.process_incoming_telegram_message("/unfocus INVE B", user_id="12345678")
    assert "Focused Watch Mode Deactivated" in resp_unfocus
    assert "INVE B" in resp_unfocus
    assert test_organiser.watch_mgr.is_watching("INVE B") is False


def test_natural_language_multi_ticker_watch(test_organiser):
    # Simultaneous natural language activation for multiple tickers in one sentence
    msg = "Watch ARM and CEG closely for 30 minutes"
    resp = test_organiser.process_incoming_telegram_message(msg, user_id="12345678")

    assert "Focused Watch Mode Activated" in resp
    assert "ARM" in resp
    assert "CEG" in resp
    assert "30 minutes" in resp
    assert test_organiser.watch_mgr.is_watching("ARM") is True
    assert test_organiser.watch_mgr.is_watching("CEG") is True

    # Targeted unfocus for only ARM
    resp_stop_arm = test_organiser.process_incoming_telegram_message("Stop watching ARM", user_id="12345678")
    assert "Focused Watch Mode Reset" in resp_stop_arm
    assert "ARM" in resp_stop_arm
    assert test_organiser.watch_mgr.is_watching("ARM") is False
    assert test_organiser.watch_mgr.is_watching("CEG") is True  # CEG remains active!

    # Stop all remaining
    resp_stop_all = test_organiser.process_incoming_telegram_message("Stop watching", user_id="12345678")
    assert "Focused Watch Mode Reset" in resp_stop_all
    assert test_organiser.watch_mgr.is_watching("CEG") is False


def test_macro_command_displays_defensive_shield(test_organiser):
    # 1. Normal conditions
    resp_normal = test_organiser.process_incoming_telegram_message("/macro", user_id="12345678")
    assert "Macro Defensive Shield: NORMAL REGIME" in resp_normal

    # 2. Simulate 10Y breach >= 5.45%
    test_organiser.feed.set_quote("^TNX", 5.50)
    resp_breach = test_organiser.process_incoming_telegram_message("/macro", user_id="12345678")
    assert "MACRO DEFENSIVE SHIELD: CRISIS REGIME (ACTIVE)" in resp_breach
    assert "Freeze all new high-beta limit buys" in resp_breach

    # Restore
    test_organiser.feed.set_quote("^TNX", 4.90)


def test_risk_worker_audit_macro_shield_and_drawdown(test_organiser):
    # Test that risk worker audit pass detects macro shield trigger (>= 5.45%)
    test_organiser.feed.set_quote("^TNX", 5.50)
    alerts = test_organiser.risk_worker.run_single_audit_cycle()
    assert any("AUTOMATED MACRO DEFENSIVE SHIELD TRIGGERED" in a.title for a in alerts)
    test_organiser.feed.set_quote("^TNX", 4.90)


# =============================================================================
# Institutional Desks Commands & Natural Language Routing Tests
# =============================================================================

def test_command_battleplan(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/battleplan", user_id="12345678")
    assert "08:30 CEST MORNING BATTLE PLAN" in resp
    assert "SECTION 1: OVERNIGHT MACRO TONE" in resp
    assert "SECTION 2: TOP 3 QUALIFIED SWING SETUPS" in resp
    assert "SECTION 3: DERIVATIVES TACTICAL FOCUS" in resp
    assert "SECTION 4: ACTIVE PORTFOLIO STOPS & CASH ALLOCATION" in resp
    assert "SECTION 5: AI-READY PROMPT DOSSIER" in resp


def test_command_signals(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/signals", user_id="12345678")
    assert "SIGNALS DESK" in resp
    assert "3-STRATEGY SWING SCANNER" in resp
    assert "Breakout" in resp
    assert "Mean-Reversion" in resp
    assert "Composite" in resp


def test_command_pots(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/pots", user_id="12345678")
    assert "CAPITAL ALLOCATION MATRIX" in resp
    assert "Pot 1: Core Long-Term Compounders" in resp
    assert "Pot 2: Tactical Swing Sleeve" in resp
    assert "Pot 3: High-Risk Derivative Sleeve" in resp
    assert "Pot 4: Macro Capital Preservation" in resp
    assert "688.30 pts" in resp


def test_command_swing(test_organiser):
    test_organiser.swing_desk.register_swing_trade("AEHR", entry_price=20.0, stop_price=18.0, points=10.0)
    resp_aehr = test_organiser.process_incoming_telegram_message("/swing AEHR", user_id="12345678")
    assert "TACTICAL SWING DESK" in resp_aehr
    assert "AEHR" in resp_aehr
    assert "TP1" in resp_aehr
    assert "TP2" in resp_aehr

    # Multi-word ticker
    resp_inve = test_organiser.process_incoming_telegram_message("/swing INVE B", user_id="12345678")
    assert "TACTICAL SWING DESK" in resp_inve
    assert "INVE B" in resp_inve


def test_command_prompt(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/prompt AEHR", user_id="12345678")
    lines = [ln for ln in resp.strip().split("\n") if ln.startswith("L")]
    assert len(lines) == 5
    assert "L1 [IDENTITY]:" in lines[0]
    assert "L2 [FUNDAMENTALS]:" in lines[1]
    assert "L3 [TECHNICALS]:" in lines[2]
    assert "L4 [CATALYSTS]:" in lines[3]
    assert "L5 [TRADE PROPOSAL]:" in lines[4]


def test_command_reason(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/reason AEHR", user_id="12345678")
    assert "AI REASONER — QUANTITATIVE CRITIQUE: `AEHR`" in resp
    assert "Executive Verdict:" in resp
    assert "Gate 1 (ROIC >= 15%):" in resp
    assert "Gate 4 (Single-Trade Risk <= 3.44 pts):" in resp


def test_command_verify_feeds(test_organiser):
    resp = test_organiser.process_incoming_telegram_message("/verify_feeds", user_id="12345678")
    assert "Dual-Engine Market Data Feed Verification" in resp
    assert "Avanza" in resp
    assert "Yahoo" in resp
    assert "Summary:" in resp


def test_natural_language_institutional_intents(test_organiser):
    # 1. Battle plan
    resp_bp = test_organiser.process_incoming_telegram_message("Show battle plan", user_id="12345678")
    assert "08:30 CEST MORNING BATTLE PLAN" in resp_bp

    # 2. Signals
    resp_sig = test_organiser.process_incoming_telegram_message("What are the swing signals?", user_id="12345678")
    assert "SIGNALS DESK" in resp_sig

    # 3. Capital Pots
    resp_pots = test_organiser.process_incoming_telegram_message("Show me the capital pots", user_id="12345678")
    assert "CAPITAL ALLOCATION MATRIX" in resp_pots

    # 4. Swing on symbol
    resp_sw = test_organiser.process_incoming_telegram_message("How is the swing on AEHR?", user_id="12345678")
    assert "TACTICAL SWING DESK" in resp_sw
    assert "AEHR" in resp_sw

    # 5. Prompt for symbol
    resp_pr = test_organiser.process_incoming_telegram_message("Generate AI prompt for AEHR", user_id="12345678")
    assert "L1 [IDENTITY]:" in resp_pr
    assert "L5 [TRADE PROPOSAL]:" in resp_pr

    # 6. Reason / Critique on symbol
    resp_rs = test_organiser.process_incoming_telegram_message("AI critique for AEHR", user_id="12345678")
    assert "AI REASONER — QUANTITATIVE CRITIQUE: `AEHR`" in resp_rs

    # 7. Feed verification
    resp_fd = test_organiser.process_incoming_telegram_message("Verify feeds", user_id="12345678")
    assert "Dual-Engine Market Data Feed Verification" in resp_fd

