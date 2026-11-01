"""
run_sentinel.py - Main Autonomous Daemon Runner.
Starts the Organiser daemon, background workers, and Telegram remote control listener.
"""

import sys
import os
import argparse
import asyncio
import logging
from pathlib import Path

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
from feeds.yfinance_feed import YFinanceFeed
from feeds.mock_feed import MockFeed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("sentinel_daemon")

# Silence noisy external library loggers
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.CRITICAL)


async def main():
    parser = argparse.ArgumentParser(description="Autonomous Trading Sentinel Daemon")
    parser.add_argument("--test", action="store_true", help="Run in mock/test mode without live API calls")
    parser.add_argument("--bot-token", type=str, default=None, help="Telegram Bot Token (or via TELEGRAM_BOT_TOKEN env)")
    parser.add_argument("--chat-id", type=str, default=None, help="Telegram Chat ID (or via TELEGRAM_CHAT_ID env)")
    parser.add_argument("--config", type=str, default=None, help="Path to portfolio_state.yaml")
    args = parser.parse_args()

    token = args.bot_token or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = args.chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    # Fallback to config/sentinel_config.yaml
    sentinel_cfg_path = PROJECT_ROOT / "config" / "sentinel_config.yaml"
    if sentinel_cfg_path.exists():
        try:
            import yaml
            with open(sentinel_cfg_path, "r", encoding="utf-8") as f:
                scfg = yaml.safe_load(f) or {}
            tg_cfg = scfg.get("telegram", {})
            if not token and tg_cfg.get("bot_token"):
                token = str(tg_cfg["bot_token"]).strip()
            if not chat_id and tg_cfg.get("chat_id"):
                chat_id = str(tg_cfg["chat_id"]).strip()
        except Exception as e:
            logger.debug(f"Could not load sentinel_config.yaml: {e}")

    logger.info("Initializing Autonomous Trading Sentinel (Version 3.0)...")
    logger.info(f"Target Directory: {PROJECT_ROOT}")
    logger.info(f"Test Mode: {args.test}")

    feed = MockFeed() if args.test else YFinanceFeed()
    organiser = Organiser(
        config_path=args.config,
        feed=feed,
        bot_token=token,
        chat_id=chat_id,
        is_test_mode=args.test
    )
    logger.info("Institutional Desks Initialized: ExecutiveDesk, SignalsDesk, SwingDesk, DerivativesDesk, IntelligenceDesk, AIReasoner, CrossVerifier.")

    # Initial Mandate Parity & Capital Audit
    recon = organiser.state_mgr.reconcile_capital()
    logger.info(
        f"Capital Parity Check: {'RECONCILED' if recon['parity_reconciled'] else 'MISMATCH'} "
        f"(Total: {recon['calculated_total_points']:.2f} pts / Target: {recon['mandate_target_points']:.2f} pts | "
        f"Invested: {recon['invested_points']:.2f} pts | Cash: {recon['cash_points']:.2f} pts)"
    )

    # Run single pre-flight check pass
    logger.info("Executing Pre-Flight Risk & Stops Audit...")
    initial_alerts = organiser.risk_worker.run_single_audit_cycle()
    logger.info(f"Initial audit complete: {len(initial_alerts)} active alerts triggered.")

    if args.test:
        logger.info("Test mode execution completed successfully. Exiting.")
        return

    # Start background tasks
    logger.info("Launching asynchronous background workers and Telegram listener...")
    tasks = [
        asyncio.create_task(organiser.risk_worker.start_loop()),
        asyncio.create_task(organiser.scanner_worker.start_loop()),
        asyncio.create_task(organiser.telegram_bot.poll_updates_loop())
    ]

    try:
        await asyncio.gather(*tasks)
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Shutdown signal received. Stopping workers...")
        organiser.risk_worker.stop()
        organiser.scanner_worker.stop()
        organiser.telegram_bot.stop_polling()
        if hasattr(organiser, "power_mgr"):
            organiser.power_mgr.disable_sleep_prevention()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nSentinel daemon halted cleanly.")
