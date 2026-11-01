"""
tests/test_ai_reasoner.py - Unit and Integration Tests for AIReasoner and verify_ai.py CLI.
Audits dense 5-line prompt format, deterministic quantitative critique engine,
Gemini fallback mechanism, and standalone CLI verification.
"""

import sys
import subprocess
import pytest
from pathlib import Path
from core.ai_reasoner import AIReasoner
from core.state_manager import StateManager
from feeds.mock_feed import MockFeed

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def test_reasoner():
    state_mgr = StateManager()
    feed = MockFeed()
    return AIReasoner(state_mgr=state_mgr, feed=feed)


def test_dense_prompt_dossier_exactly_five_lines(test_reasoner):
    dossier = test_reasoner.generate_prompt_dossier("AEHR")
    lines = dossier.strip().split("\n")

    assert len(lines) == 5, f"Expected exactly 5 lines, but got {len(lines)}"

    # Line 1: Identity & Mandate
    assert lines[0].startswith("L1 [IDENTITY]:")
    assert "AEHR" in lines[0]
    assert ("688.30 pts" in lines[0] or "696.50 pts" in lines[0])

    # Line 2: Fundamentals (Gate 1 ROIC, Gate 2 Z-score, Gate 3 Margin)
    assert lines[1].startswith("L2 [FUNDAMENTALS]:")
    assert "Gate 1 ROIC:" in lines[1]
    assert "Gate 2 Altman Z-Score:" in lines[1]
    assert "Gate 3 Oper Margin:" in lines[1]

    # Line 3: Technicals (Gate 5 50/200 EMA, RSI, MACD, ATR)
    assert lines[2].startswith("L3 [TECHNICALS]:")
    assert "Gate 5 Trend:" in lines[2]
    assert "EMA50" in lines[2]
    assert "EMA200" in lines[2]
    assert "RSI(14):" in lines[2]
    assert "MACD:" in lines[2]
    assert "ATR(14):" in lines[2]

    # Line 4: Catalysts & Sentiment
    assert lines[3].startswith("L4 [CATALYSTS]:")
    assert "Catalyst Event:" in lines[3]
    assert "News Sentiment:" in lines[3]
    assert "Thematic Cluster:" in lines[3]

    # Line 5: Trade Proposal & Stop Levels
    assert lines[4].startswith("L5 [TRADE PROPOSAL]:")
    assert "Stop:" in lines[4]
    assert "TP1:" in lines[4]
    assert "TP2:" in lines[4]
    assert "R:R:" in lines[4]
    assert "Gate 4 Cap" in lines[4]


def test_dense_prompt_dossier_swedish_ticker(test_reasoner):
    dossier = test_reasoner.generate_prompt_dossier("INVE B")
    lines = dossier.strip().split("\n")

    assert len(lines) == 5
    assert "INVE B" in lines[0]
    assert "SEK" in lines[0]
    assert "Nordic Core Compounders" in lines[3]


def test_deterministic_critique_approved_verdict(test_reasoner):
    critique = test_reasoner.critique("AEHR", mock=True)

    assert "AI REASONER — QUANTITATIVE CRITIQUE: `AEHR`" in critique
    assert "APPROVED FOR ACCUMULATION" in critique
    assert "Gate 1 (ROIC >= 15%):" in critique
    assert "Gate 2 (Altman Z-Score >= 3.0):" in critique
    assert ("3.44 pts" in critique or "3.48 pts" in critique)
    assert "Asymmetric Reward-to-Risk (>= 2.0:1):" in critique
    assert "Embedded 5-Line Dossier:" in critique


def test_deterministic_critique_handles_unconfigured_api_key(test_reasoner):
    # Ensure no API key is present
    test_reasoner.api_key = None
    critique = test_reasoner.critique("VST")

    assert "AI REASONER — QUANTITATIVE CRITIQUE: `VST`" in critique
    assert "Deterministic Quantitative Risk Engine" in critique
    assert "Executive Verdict:" in critique


def test_setup_instructions(test_reasoner):
    instr = test_reasoner.get_setup_instructions()
    assert "Google AI Studio" in instr
    assert "GEMINI_API_KEY" in instr
    assert "https://aistudio.google.com/" in instr


def test_verify_ai_cli_mock_mode():
    cmd = [sys.executable, str(PROJECT_ROOT / "verify_ai.py"), "--mock", "--symbol", "AEHR"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    assert result.returncode == 0
    assert "TRADING SENTINEL — AI REASONER & EXPORT VERIFICATION TOOL" in result.stdout
    assert "L1 [IDENTITY]:" in result.stdout
    assert "L5 [TRADE PROPOSAL]:" in result.stdout
    assert "APPROVED FOR ACCUMULATION" in result.stdout
    assert "VERIFICATION RESULT: SUCCESS (Code 0)" in result.stdout


def test_verify_ai_cli_prompt_only():
    cmd = [sys.executable, str(PROJECT_ROOT / "verify_ai.py"), "--prompt-only", "--symbol", "INVE B"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    assert result.returncode == 0
    assert "DENSE 5-LINE PROMPT DOSSIER: INVE B (Lines: 5)" in result.stdout
    lines = [ln for ln in result.stdout.strip().split("\n") if ln.startswith("L")]
    assert len(lines) == 5


def test_verify_ai_cli_json_mode():
    cmd = [sys.executable, str(PROJECT_ROOT / "verify_ai.py"), "--mock", "--json", "--symbol", "AEHR"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    assert result.returncode == 0
    import json
    data = json.loads(result.stdout)
    assert data["symbol"] == "AEHR"
    assert data["prompt_lines_count"] == 5
    assert data["status"] == "PASS"
    assert "critique" in data
