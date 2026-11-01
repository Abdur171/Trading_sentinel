"""
simulate_breach.py - CLI Simulation Tool for Immediate Mobile Alert Verification.
Allows triggering simulated breaches to verify formatting, sound, and phone delivery.
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime

# Configure Windows UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.organiser import Organiser
from feeds.mock_feed import MockFeed


def run_scenario(scenario_name: str, bot_token: str = None, chat_id: str = None):
    print(f"\n=======================================================")
    print(f"🎬 SIMULATING BREACH SCENARIO: {scenario_name.upper()}")
    print(f"=======================================================\n")

    token = bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    cid = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    sentinel_cfg_path = PROJECT_ROOT / "config" / "sentinel_config.yaml"
    if sentinel_cfg_path.exists():
        try:
            import yaml
            with open(sentinel_cfg_path, "r", encoding="utf-8") as f:
                scfg = yaml.safe_load(f) or {}
            tg_cfg = scfg.get("telegram", {})
            if not token and tg_cfg.get("bot_token"):
                token = str(tg_cfg["bot_token"]).strip()
            if not cid and tg_cfg.get("chat_id"):
                cid = str(tg_cfg["chat_id"]).strip()
        except Exception:
            pass

    mock_feed = MockFeed()
    organiser = Organiser(
        feed=mock_feed,
        bot_token=token,
        chat_id=cid,
        is_test_mode=not bool(token)
    )

    now_override = None

    if scenario_name == "aehr_stop":
        print("📉 Injecting AEHR price drop to $71.50 (Hard Stop @ $72.00)...")
        mock_feed.set_quote("AEHR", 71.50)

    elif scenario_name == "xe_stop":
        print("📉 Injecting XE price drop to $11.50 (Hard Stop @ $11.80)...")
        mock_feed.set_quote("XE", 11.50)

    elif scenario_name == "curfew":
        print("⏱️ Setting clock to 19:45:05 CEST with MINI L OMX AVA 1615 open...")
        # Simulate 19:45:05 on current date
        now_override = datetime.now().replace(hour=19, minute=45, second=5)

    elif scenario_name == "inve_limit":
        print("⚡ Injecting INVE B price drop to 402.00 SEK (Resting Limit @ 402.50 SEK)...")
        mock_feed.set_quote("INVE-B.ST", 402.00)

    elif scenario_name == "orcl_trim":
        print("🎯 Injecting ORCL price surge to $161.00 (Profit Trim @ $160.00)...")
        mock_feed.set_quote("ORCL", 161.00)

    elif scenario_name == "ceg_trim":
        print("🎯 Injecting CEG price surge to $309.00 (Profit Trim @ $308.00)...")
        mock_feed.set_quote("CEG", 309.00)

    elif scenario_name == "macro_10y":
        print("📈 Injecting US 10Y Yield surge to 5.12% (Bearish Barrier @ 5.08%)...")
        metric, alert = organiser.macro_sentinel.update_quote("^TNX", 5.12)
        if alert:
            organiser.dispatcher.dispatch(alert)
            print(f"\n📊 Results: Generated 1 alerts.")
            print(f"\n--- Alert 1 [{alert.severity}] ---")
            print(organiser.telegram_bot.format_alert_card(alert))
            return

    elif scenario_name == "gate4_excess":
        print("🧮 Testing Gate 4 single-trade risk budget on 15.0 pt position with 30% stop...")
        pts, pct, passed = organiser.auditor.calculate_trade_risk(15.0, 0.30)
        print(f"Result: Risk = {pts:.2f} pts ({pct:.2f}% of mandate). Gate 4 Passed: {passed}")
        print(f"Max allowable size at 30% stop: {organiser.auditor.max_position_points_for_stop(0.30):.2f} pts")
        return

    else:
        print(f"Unknown scenario: {scenario_name}")
        return

    # Execute audit pass
    alerts = organiser.risk_worker.run_single_audit_cycle(override_dt=now_override)
    print(f"\n📊 Results: Generated {len(alerts)} alerts.")
    for i, a in enumerate(alerts, 1):
        print(f"\n--- Alert {i} [{a.severity}] ---")
        card = organiser.telegram_bot.format_alert_card(a)
        print(card)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Simulate breach scenarios for live alerts")
    parser.add_argument(
        "--scenario",
        type=str,
        default="aehr_stop",
        choices=["aehr_stop", "xe_stop", "curfew", "inve_limit", "orcl_trim", "ceg_trim", "macro_10y", "gate4_excess", "all"],
        help="Scenario to simulate"
    )
    parser.add_argument("--bot-token", type=str, default=None, help="Telegram Bot Token")
    parser.add_argument("--chat-id", type=str, default=None, help="Telegram Chat ID")
    args = parser.parse_args()

    if args.scenario == "all":
        for sc in ["aehr_stop", "xe_stop", "curfew", "inve_limit", "orcl_trim", "ceg_trim", "macro_10y", "gate4_excess"]:
            run_scenario(sc, args.bot_token, args.chat_id)
    else:
        run_scenario(args.scenario, args.bot_token, args.chat_id)
