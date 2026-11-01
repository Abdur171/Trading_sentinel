# Autonomous Trading Sentinel

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
  - **Pot 3: High-Risk Derivative Sleeve:** Tactical warrants and leveraged options (strictly capped).
  - **Pot 4: Macro Capital Preservation:** Unencumbered dry powder held for macro dislocations.
- **Risk Budgeting (Gate 4):** Standard spot equities capped at $\le 0.50\%$ of NAV risk budget. High-risk derivatives capped at $\le 5.00$ points total allocation.
- **Automated Profit Harvesting:** Systematically sweeps realized swing and derivative gains into capital preservation cash and core compounders.

### Desk 2: Quantitative Signals Desk (`SignalsDesk`)
Evaluates three independent mathematical trading setups:
1. **Breakout Momentum:** 20-day high channel breakout, Relative Volume (RVOL) $\ge 1.3\times$, and expanding positive MACD histogram.
2. **Mean-Reversion Dips:** High-ROIC ($\ge 12\%$) quality pullbacks to the 50/200 EMA or lower Bollinger Band with RSI $\le 38$ turning upwards.
3. **Multi-Factor Composite Swings:** Trend alignment (EMA 50 > EMA 200, Price > EMA 50), RVOL $\ge 1.1\times$, RSI sweet-spot ($40 \le \text{RSI} \le 68$), and ATR-calibrated stop loss with Reward-to-Risk ratio $\ge 2.0:1$.

### Desk 3: Tactical Swing Desk (`SwingDesk`)
Oversees active swing positions with an automated multi-stage profit protection protocol:
- **Target 1 (TP1):** Reaching target generates an automated alert to trim 50% of the position and ratchets the stop loss to the exact entry price (breakeven).
- **Target 2 (TP2):** The remaining 50% runs with a dynamic trailing Average True Range (ATR) / Chandelier stop ratchet.
- **Stop-Loss Sentinel:** Monitors real-time quotes and immediately emits liquidation warnings if active stops are breached.

### Desk 4: Derivatives & Warrants Desk (`DerivativesDesk`)
Specialized risk controller for high-leverage warrants, mini-futures, and options:
- **Strict Capital Allocation Cap:** Maximum 5.00 points single-position allocation; maximum 15.00 points aggregate derivative sleeve.
- **Mandatory -40% Hard Stop:** Enforces a $-40\%$ stop loss on warrant price, capping absolute downside loss at $\le 2.00$ points ($5.00 \times 0.40 = 2.00\text{ pts}$).
- **Leverage Gearing & Barrier Tracking:** Computes real-time effective leverage multiplier and distance to financing/knock-out barrier.
- **Curfew Enforcement:** Enforces mandatory flat-book curfew before market close (19:30 advisory, 19:40 warning, 19:45 mandatory flat-book exit).

### Desk 5: Intelligence & Catalyst Desk (`IntelligenceDesk`)
- **08:30 CEST Morning Battle Plan:** Dispatches a structured 5-section daily briefing card: Overnight Macro Tone, Top 3 Qualified Swing Setups, Derivatives Tactical Focus, Active Stops & Cash Allocation, and AI-Ready Prompt Dossier.
- **Real-Time Catalyst Parser:** Categorizes company disclosures and regulatory RSS feeds into institutional categories (Earnings, Guidance, M&A, Insider Trades, Macro/Regulatory) with sentiment and impact scoring (1.0 to 10.0).

### AI Reasoning & Export Engine (`AIReasoner`)
- **Dense 5-Line Prompt Dossier (`/prompt <SYM>`):** Outputs a pre-formatted, 5-line dossier summarizing asset identity, fundamental scores (ROIC, Altman Z-score, margins), technicals (50/200 EMA, RSI, MACD, ATR), catalysts, and proposed stop levels—ready to paste into mobile LLM assistants.
- **Automated Gemini 1.5 Flash Critiques (`/reason <SYM>`):** Queries Google Gemini for an institutional second opinion, with automatic fallback to deterministic quantitative rules when offline.

### Dual-Engine Real-Time Data Pipeline (`CrossVerifier`)
- Combines zero-delay Nordic equity and warrant data (Avanza public market endpoint) with international equity data (Yahoo Finance with 15s in-memory caching).
- Alerts if price quotes diverge by $>0.5\%$ or if data becomes stale ($>180$ seconds).

---

## 3. Quick Start & Setup Instructions

### Prerequisites
- **Python 3.10+** (tested on Python 3.10, 3.11, 3.12, and 3.13)
- Internet connection (for market feeds and Telegram polling)

### 1. Clone or Download Repository
```bash
git clone https://github.com/your-username/trading-sentinel.git
cd trading-sentinel
```

### 2. Create and Activate Virtual Environment
```bash
# On Linux/macOS:
python3 -m venv venv
source venv/bin/activate

# On Windows (PowerShell):
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Configure Environment & State Files
Copy the provided generic template files to their active runtime locations:

```bash
# Environment variables (Telegram token, Gemini key)
cp .env.example .env

# Sentinel runtime schedule and macro parameters
cp config/sentinel_config.yaml.example config/sentinel_config.yaml

# Portfolio state ledger and account allocations
cp config/portfolio_state.yaml.example config/portfolio_state.yaml
```

Edit `.env` or `config/sentinel_config.yaml` to insert your Telegram credentials:
```ini
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
TELEGRAM_CHAT_ID="your_telegram_chat_id"
GEMINI_API_KEY="your_optional_free_google_ai_studio_key"
```

---

## 4. Running the Project

### Running in Test / Offline Mode
Execute a complete pre-flight check without requiring API keys or making external network requests:
```bash
python run_sentinel.py --test
```

### Starting the Live Background Daemon
Once credentials and configuration are in place:
```bash
python run_sentinel.py
```

### Running Automated Test Suite
Run the 170+ unit and integration tests:
```bash
pytest tests/
```

### Standalone CLI Verification Tools

1. **Dual-Engine Feed Cross-Verifier:**
   ```bash
   # Offline deterministic verification
   python verify_live_feeds.py --mock

   # Live market quote verification
   python verify_live_feeds.py --symbols "INVE B,SAAB B,ATCO B"
   ```

2. **AI Reasoner & Prompt Dossier CLI:**
   ```bash
   # Generate dense 5-line prompt
   python verify_ai.py --symbol AAPL --prompt-only

   # Run offline deterministic critique
   python verify_ai.py --symbol MSFT --mock
   ```

3. **Live Breach Simulation:**
   ```bash
   # Test alert formatting and Telegram delivery
   python simulate_breach.py --scenario all
   ```

---

## 5. Mobile Telegram Remote Control Grammar

Send commands directly to your private Telegram bot:

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
| `/scan [SECTOR]` | Scans for market price dislocations $>3\%$ and breaking news catalysts. |
| `/gear <idle\|cruise\|active>` | Adjusts background polling frequency and worker intervals. |
| `/update_stop <SYM> <PRICE>` | Dynamically ratchets or updates a stop loss in the ledger. |
| `/add_holding <SYM> <PTS> <ACCT> [STOP] [DESC]` | Adds a new position, enforcing Gate 4 risk budget limits. |

### Two-Way Conversational Agent
You can chat with the Sentinel in plain English directly in Telegram:
- *"What is our current cash balance?"*
- *"Show me our active stops."*
- *"If I buy 5 points with an 8% stop, does it pass Gate 4?"*
- *"Evaluate INVE B."*

---

## 6. Security, Privacy & Network Architecture

- **No Open Ports:** Telegram communication uses secure outbound HTTPS long-polling (`getUpdates`). No port forwarding, public IP, or dynamic DNS required.
- **Sender Whitelisting:** Unauthorized Telegram user IDs are automatically dropped.
- **Local-First Architecture:** All configuration, state, and ledgers reside on your local filesystem.
- **Zero Paid Subscriptions:** Utilizes free public data endpoints and Google AI Studio free tier.

---

## 7. Disclaimer & Limitation of Liability

**PLEASE READ CAREFULLY:**

1. **No Financial Advice:** The materials and code contained in this repository are for educational, research, and technical prototyping purposes only. Nothing contained herein constitutes investment advice, financial advice, trading recommendations, or an offer or solicitation to buy or sell any security, derivative, or financial product.
2. **No Warranties:** This software is provided "AS IS", without warranty of any kind, express or implied, including but not limited to the warranties of merchantability, fitness for a particular purpose, and non-infringement.
3. **Assumption of Risk:** Financial markets carry inherent financial risks. Algorithmic and quantitative trading models may fail, produce incorrect calculations, encounter latency, experience connectivity loss, or suffer unexpected losses due to market volatility, exchange halts, or broker execution issues. You assume full and exclusive responsibility for all trading and investment decisions.
4. **No Accountability:** Under no circumstances shall the authors, maintainers, copyright holders, or contributors be liable for any claim, damages, losses, lost profits, or other liability, whether in an action of contract, tort, or otherwise, arising from, out of, or in connection with the software or the use or other dealings in the software.
