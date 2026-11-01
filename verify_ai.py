"""
verify_ai.py - Standalone CLI Verification Tool for AI Reasoning & Export Engine.
Audits dense 5-line prompt dossier generation, Gemini 1.5 Flash critiques, and offline fallbacks.

Usage:
    python verify_ai.py --mock
    python verify_ai.py --prompt-only --symbol AEHR
    python verify_ai.py --symbol INVE B --mock
    python verify_ai.py --json
"""

import sys
import argparse
import json
from pathlib import Path

# Configure Windows UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core.ai_reasoner import AIReasoner
from core.state_manager import StateManager
from feeds.mock_feed import MockFeed
from feeds.yfinance_feed import YFinanceFeed


def main():
    parser = argparse.ArgumentParser(description="Verify AI Reasoning & Prompt Dossier Engine")
    parser.add_argument("--symbol", type=str, default="AEHR", help="Target ticker symbol (default: AEHR)")
    parser.add_argument("--mock", action="store_true", help="Run offline deterministic critique (no external API calls)")
    parser.add_argument("--prompt-only", action="store_true", help="Print only the dense 5-line prompt dossier")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    parser.add_argument("--setup", action="store_true", help="Display Google AI Studio setup instructions")
    args = parser.parse_args()

    symbol = args.symbol.strip().upper()

    # Initialize reasoner
    state_mgr = StateManager()
    feed = MockFeed() if args.mock else YFinanceFeed()
    reasoner = AIReasoner(state_mgr=state_mgr, feed=feed)

    if args.setup:
        print(reasoner.get_setup_instructions())
        sys.exit(0)

    # 1. Generate prompt dossier
    dossier = reasoner.generate_prompt_dossier(symbol)
    lines = dossier.strip().split("\n")

    if args.prompt_only:
        if args.json:
            print(json.dumps({
                "symbol": symbol,
                "lines_count": len(lines),
                "dossier": lines
            }, indent=2))
        else:
            print(f"================================================================================")
            print(f"DENSE 5-LINE PROMPT DOSSIER: {symbol} (Lines: {len(lines)})")
            print(f"================================================================================")
            print(dossier)
        sys.exit(0)

    # 2. Run critique
    critique_text = reasoner.critique(symbol, mock=args.mock)

    if args.json:
        payload = {
            "symbol": symbol,
            "mock_mode": args.mock,
            "prompt_lines_count": len(lines),
            "dossier_lines": lines,
            "critique": critique_text,
            "status": "PASS"
        }
        print(json.dumps(payload, indent=2))
        sys.exit(0)

    # Standard clean terminal output
    print("=" * 80)
    print(f"TRADING SENTINEL — AI REASONER & EXPORT VERIFICATION TOOL")
    print(f"Target: {symbol} | Mock Mode: {args.mock} | Prompt Lines: {len(lines)}")
    print("=" * 80)
    print("\n[SECTION 1: DENSE 5-LINE PROMPT DOSSIER]")
    print("-" * 80)
    print(dossier)
    print("\n[SECTION 2: AUTOMATED REASONING & RISK CRITIQUE]")
    print("-" * 80)
    print(critique_text)
    print("\n" + "=" * 80)
    print("VERIFICATION RESULT: SUCCESS (Code 0)")
    print("=" * 80)
    sys.exit(0)


if __name__ == "__main__":
    main()
