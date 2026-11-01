# Autonomous Trading Sentinel: Institutional Quantitative Governance Daemon

[![Language](https://img.shields.io/badge/Language-Python%203.11%2B-3776AB.svg?logo=python&logoColor=white)](https://www.python.org/)
[![Concurrency](https://img.shields.io/badge/Architecture-Asyncio%20Event%20Loop-00599C.svg)](https://docs.python.org/3/library/asyncio.html)
[![Validation](https://img.shields.io/badge/Data%20Validation-Pydantic%20v2-E92063.svg?logo=pydantic&logoColor=white)](https://docs.pydantic.dev/)
[![LLM Intelligence](https://img.shields.io/badge/AI%20Catalyst-Google%20Gemini%201.5-4285F4.svg?logo=google&logoColor=white)](https://ai.google.dev/)
[![Remote Control](https://img.shields.io/badge/Interface-Telegram%20Bot%20API-26A5E4.svg?logo=telegram&logoColor=white)](https://core.telegram.org/bots)
[![Test Suite](https://img.shields.io/badge/Unit%20Tests-176%2F176%20Passing-brightgreen.svg)](#test-verification)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

> **DISCLAIMER: NOT FINANCIAL ADVICE. FOR RESEARCH AND EDUCATIONAL PURPOSES ONLY.**  
> Trading equities, swing strategies, derivatives, warrants, and leveraged financial instruments involves substantial risk of loss. This software is provided strictly for technical demonstration, research, and educational purposes. Neither the authors, maintainers, nor contributors accept any liability or accountability for any financial losses, trading errors, missed orders, software bugs, API outages, or data discrepancies incurred while using this system. Use entirely at your own risk.

---

## 1. Overview & Institutional Architecture

**Trading Sentinel** is an institutional-grade, multi-desk quantitative trading and risk governance daemon designed to operate continuously on a local workstation while providing bi-directional remote monitoring and command execution via a private Telegram bot.

Operating on a barbell asset allocation model, Trading Sentinel coordinates market intelligence, signal generation, trade lifecycle management, derivative risk containment, and macro regime surveillance across **5 specialized desks**, overseen by an executive governing body.

```
                              ┌────────────────────────────────────────┐
                              │             EXECUTIVE DESK             │
                              │  - Sovereign Capital Parity & NAV      │
                              │  - 4 Dynamic Capital Risk Pots         │
                              │  - Gate 4 Risk Allocation & Caps       │
                              │  - Automated Profit Harvesting         │
                              └──────────────────┬─────────────────────┘
                                                 │
         ┌────────────────────────┬──────────────┴──────────────┬────────────────────────┐
         ▼                        ▼                             ▼                        ▼
┌──────────────────┐   ┌──────────────────┐          ┌──────────────────┐     ┌──────────────────┐
│   SIGNALS DESK   │   │    SWING DESK    │          │ DERIVATIVES DESK │     │INTELLIGENCE DESK │
│ - Breakout Mom.  │   │ - TP1 50% Trim   │          │ - 5.00 pt Cap    │     │ - 08:30 Briefing │
│ - Mean Reversion │   │ - Breakeven Stop │          │ - -40% Stop Loss │     │ - Catalyst RSS   │
│ - Multi-Factor   │   │ - Trailing ATR   │          │ - Curfew (19:45) │     │ - Impact Scoring │
└────────┬─────────┘   └────────┬─────────┘          └────────┬─────────┘     └────────┬─────────┘
         │                      │                             │                        │
         └──────────────────────┼─────────────────────────────┼────────────────────────┘
                                │                             │
                                ▼                             ▼
                    ┌──────────────────────┐      ┌──────────────────────┐
                    │     AI REASONER      │      │ DUAL-ENGINE PIPELINE │
                    │ - 5-Line Dossier     │      │ - Avanza 0-Delay Feed│
                    │ - Gemini 1.5 Flash   │      │ - Yahoo Finance Feed │
                    │ - Deterministic Fall │      │ - Cross-Verification │
                    └──────────┬───────────┘      └──────────┬───────────┘
                               │                             │
                               └──────────────┬──────────────┘
                                              ▼
                               ┌─────────────────────────────┐
                               │     CENTRAL ORGANISER       │
                               │  - Background Workers       │
                               │  - Telegram Remote Control  │
                               │  - Two-Way Conversational   │
                               └─────────────────────────────┘
```

---

## 2. Desk Structure & Quantitative Capabilities

### Desk 1: Executive Governing Desk (`ExecutiveDesk`)
- **Capital Parity & NAV:** Maintains strict ledger accounting across all invested holdings and liquid cash accounts.
- **4 Dynamic Capital Pots:**
  - **Pot 1: Core Long-Term Compounders:** Multi-year high-ROIC quality assets.
  - **Pot 2: Tactical Swing Sleeve:** Medium-term momentum and mean-reversion swings.
  - **Pot 3: Asymmetric Derivatives Sleeve:** Small-stake, capped-risk leverage.
  - **Pot 4: Liquid Strategic Dry Powder:** Cash buffer for high-probability dislocations.
- **Profit Harvesting Engine:** Automatically flags trim thresholds (e.g., $+20\%$) and sweeps harvested capital back into dry powder.
- **Gate 4 Pre-Trade Allocation Guard:** Rejects any position request exceeding predefined single-asset concentration limits or max portfolio risk thresholds.

### Desk 2: Swing Execution Desk (`SwingDesk`)
- **Two-Stage Profit-Lock Protocol:**
  - **TP1 Trigger ($+5\%$):** Trims 50% of the position, instantly ratchets the stop-loss to entry price ($\text{Breakeven}$).
  - **Trailing Phase:** Ratchets remaining 50% using an adaptive $2.5 \times \text{ATR}$ trailing cushion.
- **Position Health Audits:** Flags stale positions ($>15$ sessions without forward momentum) and detects regime shifts against benchmark indices.

### Desk 3: Derivatives & Risk Desk (`DerivativesDesk`)
- **Absolute Risk Containment:**
  - **Single Warrant Stake Ceiling:** Hard maximum stake limit ($5.00$ points / currency equivalent) per instrument.
  - **Hard Stop-Loss Rule:** Enforces mandatory liquidation if a derivative position falls $-40\%$.
  - **Curfew Rule (19:45 CEST):** Zero overnight warrant risk: forces full closure of intraday derivative exposure before market close.

### Desk 4: Signals & Scanning Desk (`SignalsDesk`)
- **Strategy A: Dynamic Breakout Momentum:** Detects consolidation compression breakouts verified by $1.5\times$ volume confirmation.
- **Strategy B: Oversold Mean Reversion:** Identifies $\text{RSI}(14) < 32$ oversold dislocations in liquid, investment-grade equities.
- **Strategy C: Multi-Factor Composite:** Scores momentum, earnings yield, ROIC, and volatility rank.

### Desk 5: Market Intelligence Desk (`IntelligenceDesk`)
- **08:30 CEST Morning Battle Plan:** Synthesizes overnight global macro, treasury yields, commodities, and calendar earnings.
- **Autonomous Catalyst Scraper:** Monitors regulatory filings, breaking corporate news, and earnings calendars.
- **Gemini 1.5 Flash AI Reasoner:** Performs qualitative catalyst stress-testing and contrarian risk analysis with deterministic quantitative fallbacks.

---

## 3. Remote Control & Command Reference

Communicate directly with Sentinel via Telegram bot:

| Command | Action / Description |
| :--- | :--- |
| `/battleplan` | Generates the 08:30 CEST 5-section Morning Battle Plan briefing card. |
| `/signals` | Evaluates and displays qualified swing setups across the 3 mathematical strategies. |
| `/pots` | Displays the ExecutiveDesk 4-pot dynamic capital allocation matrix and parity status. |
| `/swing <SYM>` | Displays SwingDesk 2-stage status, trailing ATR ratchets, and profit lock levels. |
| `/prompt <SYM>` | Exports dense 5-line prompt dossier formatted for mobile LLM applications. |
| `/reason <SYM>` | Runs Gemini 1.5 Flash risk critique with deterministic quantitative fallback. |
| `/verify_feeds` | Cross-checks live market quotes between Avanza and Yahoo Finance for divergence. |
| `/status` | Returns total NAV, invested vs cash breakdown, and capital parity audit. |
| `/stops` | Displays all active stop-loss levels, current market prices, and buffer distances. |
| `/cash` | Shows balances across all configured cash accounts. |
| `/curfew` | Displays time remaining until mandatory 19:45 CEST flat-book derivative curfew. |
| `/macro` | Renders the live transmission matrix (10Y, 30Y, 2Y yields, Brent crude, Gold). |
| `/analyze <SYM>` | Runs the full 10-Gate quantitative audit report on any equity symbol. |
| `/news <SYM>` | Summarizes breaking news and regulatory disclosures for the specified ticker. |
| `/gear <idle\|cruise\|active>` | Adjusts background polling frequency and worker intervals. |

---

## 4. Test Verification & Code Quality

The system includes a 176-test automated regression suite covering every calculation, state machine, and desk logic:

```bash
pytest tests/ -v
# ============================== 176 passed in 1.42s ==============================
```

---

## 5. Security & Privacy Architecture

- **No Inbound Open Ports:** Telegram communication uses secure outbound HTTPS long-polling (`getUpdates`).
- **Sender Whitelisting:** Unauthorized Telegram user IDs are silently dropped.
- **Local Ledger Sovereignty:** All trade logs, configuration, and state remain on the local disk.
- **Zero Paid Subscriptions:** Operates entirely on free public endpoints and Google AI Studio free tier.
