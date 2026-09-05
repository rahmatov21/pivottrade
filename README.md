# PivotTrader Pro: Terminal Autotrading Console (MEXC + 2-Stage DCA)

A pure, institutional-grade **Terminal Autotrading Console** powered by the **LuxAlgo "Pivot Points High Low & Missed Reversal Levels"** indicator algorithm, connected to live **MEXC Global** market data and remotely controlled via **Telegram**.

---

## 🌟 Key Architecture & Highlights

1. **100% Terminal-Based**: Completely standalone CLI application with rich Unicode panels, ANSI colors, and non-blocking keyboard hotkeys.
2. **MEXC Global Integration (`mexc_client.py`)**: Official REST API v3 public market data (live klines & ticker) and authenticated private endpoints (`/api/v3/account` balance queries via HMAC-SHA256).
3. **Indicator Mathematical Precision (`python_algorithm.py`)**: Strict inequality Pine Script replication (`ta.pivothigh` and `ta.pivotlow`), eliminating phantom pivots and strictly matching TradingView.
4. **$100 Portfolio Equity & 2-Stage DCA (`paper_broker.py`)**:
   - Starting Capital: **$100.00 USDT**.
   - **Stage 1 (75% Allocation)**: Deploys $75.00 USDT on a confirmed Pivot Low swing bottom. Leaves $25.00 USDT cash in reserve.
   - **Stage 2 Dip DCA (25% Allocation)**: If price dips >= 1.5% below initial entry or if a secondary lower Pivot Low confirms, the bot deploys the remaining $25.00 USDT.
   - **Blended Weighted Average Entry**: Averages down the entry price and dynamically recalculates Take Profit closer to current price for high-probability bounce exits.
   - **Exits**: 100% liquidated back to USDT on Take Profit, Stop Loss, or confirmed Pivot High top.
5. **Telegram Remote Controller (`telegram_bot.py`)**:
   - Strict **Admin ID authentication** (`6635545559`).
   - Interactive persistent reply keyboard (1-tap quick buttons).
   - Instant push notifications on Stage 1 Buy, Stage 2 Dip Buy, Take Profit, Stop Loss, or manual orders.

---

## 🚀 Quick Start

### 1. Configure Environment (`.env`)

Add your credentials in `.env`:

```ini
TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_ADMIN_ID="YOUR_TELEGRAM_ADMIN_ID"

# Your MEXC API Keys for balance monitoring (/mexc_wallet)
MEXC_API_KEY="YOUR_MEXC_API_KEY"
MEXC_SECRET_KEY="YOUR_MEXC_SECRET_KEY"
```

### 2. Launch the Terminal Console

```bash
python bot.py
```

The terminal prints the status banner, launches background Telegram polling, and begins monitoring the MEXC market feed.

---

## ⌨️ Terminal Keyboard Hotkeys

While `bot.py` is running, press any of these keys in your terminal for immediate actions:

| Key | Action | Description |
| :---: | :--- | :--- |
| **`[S]`** | **Status Card** | Prints full account status card, DCA stage, open position, TP/SL, and PnL |
| **`[O]`** | **Portfolio Report** | Prints detailed portfolio breakdown: initial cash, cash left, equity, win rate, net PnL |
| **`[B]`** | **Manual Buy** | Executes manual Stage 1 Buy (75% allocation) at current market price |
| **`[C]`** | **Close Position** | Manually market sells active spot position back to USDT |
| **`[P]`** | **Pivots Table** | Prints formatted table of recent confirmed swing highs and lows |
| **`[R]`** | **Force Refresh** | Forces immediate candle fetch and indicator recalculation |
| **`[H]`** | **Help** | Displays hotkey reference guide |
| **`[Q]`** | **Quit** | Gracefully shuts down bot, saves paper state, and notifies Telegram |

---

## 📱 Telegram Remote Commands (`@pivotraderbot`)

Send commands or tap the persistent keyboard buttons:

| Command | Quick Button | Description |
| :--- | :---: | :--- |
| `/portfolio` | `💼 Portfolio` | Full Profit & Loss breakdown, money left after trades, win rate, and trade history |
| `/status` | `📊 Status` | View live status: BOUGHT (Stage 1 or 2) or SOLD, live PnL, targets, dip watcher |
| `/balance` | `💵 Balance` | View USDT cash, total equity, win rate, realized PnL, total trades |
| `/pivots` | `📈 Pivots` | View recent confirmed swing highs/lows and trend bias from MEXC |
| `/config` | `⚙️ Config` | View current trading parameters |
| `/buy` | `🟢 Buy (Stage 1)` | Trigger manual spot buy using 75% of available cash |
| `/close_position` | `🔴 Close Position` | Trigger manual spot sell to liquidate 100% back to USDT cash |
| `/pause` | `⏸️ Pause Bot` | Pause automated buying |
| `/resume` | `▶️ Resume Bot` | Resume automated trading |
| `/mexc_wallet` | `💼 MEXC Wallet` | Query real MEXC spot wallet balances via private API |
| `/reset_equity` | `🔄 Reset ($100)` | Reset paper balance back to $100.00 USDT and clear position |
| `/set_symbol <SYM>` | — | Switch trading pair on MEXC (e.g. `/set_symbol ETHUSDT`) |
| `/set_timeframe <TF>`| — | Switch candle interval (`1m`, `5m`, `15m`, `30m`, `1h`, `4h`, `1d`) |
| `/set_length <N>` | — | Change indicator pivot length strength (e.g. `/set_length 10`) |
| `/set_risk <R>` | — | Change Risk:Reward ratio (e.g. `/set_risk 2.0`) |

---

## 🧪 Testing & Verification

Run the comprehensive unit test suite:

```bash
python tests/test_system_e2e.py
```

Validates:
- MEXC candle normalization and signature generation.
- Strict inequality indicator fidelity (matches Pine Script `ta.pivothigh`/`pivotlow`).
- 2-Stage DCA allocation ($100 starting equity, 75% Stage 1, 25% Stage 2 Dip Buy, blended entry math, TP/SL adjustment).
- Telegram interactive keyboard and status formatting.
