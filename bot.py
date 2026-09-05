"""
PivotTrader Pro — Terminal Trading Console (MEXC + 2-Stage DCA)
===============================================================
Institutional-grade, pure terminal autotrading console.
Features:
- Live MEXC Global REST API v3 candle feed
- Mathematical fidelity LuxAlgo Pivot Points indicator (Pine Script exact match)
- $100 starting equity portfolio management
- 2-Stage DCA Position Sizing (75% Stage 1 Initial + 25% Stage 2 Dip Buy)
- Dynamic Blended Average Entry calculation & TP/SL re-anchoring
- Full remote control & notifications via Telegram (@pivotraderbot)
- Non-blocking interactive keyboard hotkeys in terminal ([s], [b], [c], [p], [r], [q])
"""

import sys
import time
import os
import signal
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

try:
    import colorama
    colorama.init(autoreset=True)
except ImportError:
    pass

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Terminal Styling & ANSI Colors
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
WHITE = "\033[37m"

from config_manager import load_config, update_config_value
from paper_broker import PaperBroker
from telegram_bot import TelegramManager
from mexc_client import fetch_mexc_candles, fetch_mexc_ticker, MexcClient
from python_algorithm import pivot_points_high_low_missed, PivotResult, RegularPivot, MissedPivot


class TerminalPaperBot:
    def __init__(self):
        self.running = True
        self._shutting_down = False
        self.config = load_config()

        # Initialize paper broker with $100 starting equity
        self.broker = PaperBroker(initial_balance=float(self.config.get("initial_balance", 100.0)))

        # Initialize MEXC API client
        self.mexc = MexcClient(
            api_key=self.config.get("mexc_api_key"),
            secret_key=self.config.get("mexc_secret_key"),
        )

        # Market & indicator state
        self.latest_price: float = 0.0
        self.last_candle_time: int = 0
        self.last_checked_bar: int = -1
        self.last_heartbeat_time: float = 0.0
        self.latest_pivot_res: Optional[PivotResult] = None
        self.force_refresh_flag = False

        # Last signal tracker for clear status reporting
        self.last_signal_info: Dict[str, Any] = {
            "type": "NONE",
            "price": 0.0,
            "bar": 0,
            "time": 0,
            "action": "WAITING",
            "reason": "Bot initialized with $100.00 USDT equity. Waiting for first confirmed pivot event",
        }

        # Initialize Telegram Manager
        self.telegram = TelegramManager(
            broker=self.broker,
            on_config_changed=self.on_config_changed,
            get_current_price=lambda: self.latest_price,
            manual_sell_callback=self.manual_market_sell,
            manual_buy_callback=self.manual_market_buy,
            get_pivot_summary=self.get_pivot_summary,
            get_last_signal_info=lambda: self.last_signal_info,
            mexc_client=self.mexc,
        )

        # Start non-blocking terminal hotkey listener
        self.keyboard_thread = threading.Thread(target=self._keyboard_listener, daemon=True)
        self.keyboard_thread.start()

        # Graceful shutdown handler
        signal.signal(signal.SIGINT, self.handle_exit)
        signal.signal(signal.SIGTERM, self.handle_exit)

    def handle_exit(self, signum=None, frame=None):
        if self._shutting_down:
            return
        self._shutting_down = True
        print(f"\n{YELLOW}[SYSTEM] Shutting down PivotTrader terminal bot gracefully...{RESET}")
        self.running = False
        try:
            self.broker.save_state()
            if self.telegram and self.telegram.is_connected:
                self.telegram.send_alert("🛑 Bot Offline", "PivotTrader terminal bot has been stopped.")
        except Exception:
            pass
        os._exit(0)

    def on_config_changed(self, new_cfg: Dict[str, Any]):
        """Callback invoked when settings are modified via Telegram."""
        old_sym = self.config.get("symbol")
        old_tf = self.config.get("timeframe")
        self.config = new_cfg
        self.mexc.api_key = new_cfg.get("mexc_api_key", "")
        self.mexc.secret_key = new_cfg.get("mexc_secret_key", "")
        print(f"\n{CYAN}⚡ [CONFIG UPDATE VIA TELEGRAM]{RESET} Pair: {new_cfg['symbol']} | TF: {new_cfg['timeframe']} | Pivot Len: {new_cfg['pivot_length']} | Trading: {'ENABLED' if new_cfg['trading_enabled'] else 'PAUSED'}")
        if old_sym != new_cfg["symbol"] or old_tf != new_cfg["timeframe"]:
            self.last_checked_bar = -1
            self.force_refresh_flag = True

    def _keyboard_listener(self):
        """Monitors keyboard input on Windows/POSIX without blocking autotrading."""
        if sys.platform == "win32":
            import msvcrt
            while self.running:
                try:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch().decode("utf-8", errors="ignore").lower()
                        self.handle_hotkey(ch)
                    time.sleep(0.08)
                except Exception:
                    time.sleep(0.2)
        else:
            while self.running:
                time.sleep(1)

    def handle_hotkey(self, key: str):
        """Processes single-key hotkey presses from terminal."""
        if key == "s":
            self.print_status_card()
        elif key in ["o", "t"]:
            self.print_portfolio_report()
        elif key == "b":
            print(f"\n{CYAN}[HOTKEY] Manual Buy (Stage 1) triggered...{RESET}")
            self.manual_market_buy()
        elif key == "c":
            print(f"\n{CYAN}[HOTKEY] Manual Close Position triggered...{RESET}")
            self.manual_market_sell()
        elif key == "p":
            self.print_pivots_table()
        elif key == "r":
            print(f"\n{CYAN}[HOTKEY] Forcing market data refresh...{RESET}")
            self.force_refresh_flag = True
        elif key == "h":
            self.print_hotkeys_help()
        elif key == "q":
            self.handle_exit()

    def manual_market_buy(self) -> Optional[Dict[str, Any]]:
        """Executes manual spot buy (Stage 1: 75% allocation)."""
        if self.broker.position is not None or self.latest_price <= 0:
            print(f"{YELLOW}⚠️ Cannot buy: already holding an open position or price not loaded.{RESET}")
            return None
        cfg = self.config
        sl = self.latest_price * (1.0 - float(cfg.get("sl_buffer_pct", 0.002)) * 2)
        risk = self.latest_price - sl
        tp = self.latest_price + (risk * float(cfg.get("risk_reward_ratio", 1.5)))
        pos_size = float(cfg.get("initial_position_size_pct", 0.75))

        buy_order = self.broker.buy_initial(
            symbol=cfg["symbol"],
            price=self.latest_price,
            stop_loss=sl,
            take_profit=tp,
            reason="MANUAL_BUY_STAGE_1 (HotKey / Telegram)",
            position_size_pct=pos_size,
        )
        if buy_order:
            self.last_signal_info = {
                "type": "MANUAL BUY (Stage 1 - 75%)",
                "price": buy_order["price"],
                "bar": self.last_checked_bar,
                "time": int(time.time()),
                "action": f"BOUGHT {buy_order['quantity']} {self.broker.get_base_asset(cfg['symbol'])}",
                "reason": "Executed via manual terminal hotkey or Telegram",
            }
            print(f"\n{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
            print(f"{GREEN}{BOLD}⚡ [MANUAL BUY EXECUTED (STAGE 1: 75%)]{RESET}")
            print(f"   Bought: {buy_order['quantity']} {self.broker.get_base_asset(cfg['symbol'])} @ ${buy_order['price']:,.2f} (${buy_order['cost_usdt']:,.2f} USDT)")
            print(f"   Take Profit: ${buy_order['take_profit']:,.2f} | Stop Loss: ${buy_order['stop_loss']:,.2f}")
            print(f"   Reserve Cash for Dip: ${buy_order['remaining_usdt']:,.2f} USDT (25%)")
            print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
            print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
            self.telegram.send_buy_notification(buy_order)
        return buy_order

    def manual_market_sell(self) -> Optional[Dict[str, Any]]:
        """Executes manual spot market sell (cashes out 100% to USDT)."""
        if not self.broker.position or self.latest_price <= 0:
            print(f"{YELLOW}⚠️ Cannot sell: no active position to close.{RESET}")
            return None
        trade = self.broker.sell(price=self.latest_price, reason="MANUAL_CLOSE (HotKey / Telegram)")
        if trade:
            pnl_col = GREEN if trade["pnl"] >= 0 else RED
            self.last_signal_info = {
                "type": "MANUAL SELL",
                "price": trade["exit_price"],
                "bar": self.last_checked_bar,
                "time": int(time.time()),
                "action": f"SOLD (Cashed out ${trade['net_proceeds']:,.2f} USDT)",
                "reason": f"Manual sell | PnL: ${trade['pnl']:,.2f}",
            }
            print(f"\n{YELLOW}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
            print(f"{YELLOW}{BOLD}⚠️ [MANUAL SELL EXECUTED - 100% CASHED OUT]{RESET}")
            print(f"   Sold @ ${trade['exit_price']:,.2f} | Realized PnL: {pnl_col}${trade['pnl']:,.2f} ({trade['pnl_pct']:+.2f}%){RESET}")
            print(f"   New USDT Balance: ${self.broker.usdt_balance:,.2f} USDT")
            print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
            print(f"{YELLOW}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
            self.telegram.send_sell_notification(trade)
        return trade

    def get_pivot_summary(self) -> Dict[str, Any]:
        """Telemetry provider for indicator swings."""
        if not self.latest_pivot_res:
            return {}
        reg = self.latest_pivot_res.regular_pivots
        miss = self.latest_pivot_res.missed_pivots
        latest_p = reg[-1] if reg else None

        trend_bias = "NEUTRAL"
        if latest_p:
            trend_bias = "BULLISH (Higher Low Swing)" if latest_p.type == "low" else "BEARISH (Lower High Swing)"

        return {
            "regular_pivots": reg,
            "missed_pivots": miss,
            "latest_pivot": latest_p,
            "trend_bias": trend_bias,
            "projection_point": self.latest_pivot_res.projection_point,
        }

    def fetch_candles(self, symbol: str, timeframe: str, limit: int = 200) -> List[Dict[str, Any]]:
        """Fetches candles from official MEXC v3 API with retry."""
        try:
            return fetch_mexc_candles(symbol, timeframe, limit=limit)
        except Exception as e:
            time.sleep(1.5)
            try:
                return fetch_mexc_candles(symbol, timeframe, limit=limit)
            except Exception:
                return []

    def print_banner(self):
        """Displays startup header and connection status."""
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            os.system("cls" if os.name == "nt" else "clear")

        cfg = self.config
        tg_status = f"{GREEN}ONLINE (@pivotraderbot | Admin: {cfg.get('telegram_admin_id')}){RESET}" if self.telegram.is_connected else f"{YELLOW}LOCAL ONLY{RESET}"
        mexc_status = f"{GREEN}CONNECTED (Keys Authenticated){RESET}" if self.mexc.has_credentials else f"{YELLOW}PUBLIC DATA ONLY{RESET}"

        print(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════════════════╗{RESET}")
        print(f"{CYAN}{BOLD}║         PIVOTTRADER PRO — INSTITUTIONAL TERMINAL TRADING CONSOLE             ║{RESET}")
        print(f"{CYAN}{BOLD}║         LuxAlgo Pivot Points Indicator & 2-Stage DCA Autotrading             ║{RESET}")
        print(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════════════════╝{RESET}")
        print(f" • {BOLD}Market Data Feed:{RESET}   MEXC Global REST API v3 (Live Candles)")
        print(f" • {BOLD}Trading Pair:{RESET}       {BOLD}{cfg['symbol']}{RESET}  |  Timeframe: {BOLD}{cfg['timeframe']}{RESET}  |  Pivot Length: {BOLD}{cfg['pivot_length']}{RESET}")
        print(f" • {BOLD}Position Sizing:{RESET}    75% Stage 1 Entry ($75)  +  25% Stage 2 Dip DCA ($25)")
        print(f" • {BOLD}Portfolio Capital:{RESET}  ${self.broker.usdt_balance:,.2f} USDT  (Paper Spot Account)")
        print(f" • {BOLD}MEXC Private API:{RESET}   {mexc_status}")
        print(f" • {BOLD}Telegram Remote:{RESET}    {tg_status}")
        print(f"{CYAN}────────────────────────────────────────────────────────────────────────────────{RESET}")
        print(f" {BOLD}HOTKEYS:{RESET} [S] Status  [O] Portfolio  [B] Manual Buy  [C] Close Pos  [P] Pivots  [R] Refresh  [Q] Quit")
        print(f"{CYAN}────────────────────────────────────────────────────────────────────────────────{RESET}\n")

    def print_status_card(self):
        """Renders comprehensive status panel in the console."""
        cfg = self.config
        symbol = cfg["symbol"]
        curr_price = self.latest_price
        summary = self.broker.get_account_summary(curr_price)
        pos = summary["position"]

        print(f"\n{CYAN}{BOLD}┌─────────────────── [LIVE ACCOUNT & TRADING STATUS] ───────────────────┐{RESET}")
        print(f"│ {BOLD}Market:{RESET} {symbol} on MEXC  |  {BOLD}Price:{RESET} ${curr_price:,.2f}  |  {BOLD}Time:{RESET} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"│ {BOLD}Portfolio Equity:{RESET} ${summary['total_equity']:,.2f} USDT  |  {BOLD}Cash Available:{RESET} ${summary['usdt_balance']:,.2f} USDT")
        print(f"├────────────────────────────────────────────────────────────────────────┤{RESET}")

        if pos:
            unrealized = summary["unrealized_pnl"]
            unrealized_pct = summary["unrealized_pnl_pct"]
            pnl_col = GREEN if unrealized >= 0 else RED
            pnl_sign = "+" if unrealized >= 0 else ""
            stage_num = pos["stage"]

            if stage_num == 1:
                dip_trigger = pos["entry_price"] * (1.0 - float(cfg.get("dip_trigger_pct", 0.015)))
                print(f"│ {GREEN}{BOLD}STATUS: BOUGHT (STAGE 1 — 75% ALLOCATED){RESET}")
                print(f"│ • Holding:      {pos['quantity']} {pos['base_asset']}  (Cost: ${pos['cost_usdt']:,.2f} USDT)")
                print(f"│ • Entry Price:  ${pos['entry_price']:,.2f}")
                print(f"│ • Profit/Loss:  {pnl_col}{BOLD}{pnl_sign}${unrealized:,.2f} ({pnl_sign}{unrealized_pct:.2f}%){RESET}")
                print(f"│ • Profit Target:${pos['take_profit']:,.2f}  |  Stop Loss: ${pos['stop_loss']:,.2f}")
                print(f"│ ──────────────────────────────────────────────────────────────────────")
                print(f"│ • {YELLOW}DIP DCA WATCHER:{RESET} Reserve: ${summary['usdt_balance']:,.2f} USDT (25%)")
                print(f"│ • Dip Trigger:  Below ${dip_trigger:,.2f} (-{float(cfg.get('dip_trigger_pct', 0.015))*100:.1f}%) -> will buy 25% DCA")
            else:
                print(f"│ {GREEN}{BOLD}STATUS: BOUGHT (STAGE 2 — 100% FULLY ALLOCATED AFTER DIP BUY){RESET}")
                print(f"│ • Total Size:   {pos['quantity']} {pos['base_asset']}  (Total Cost: ${pos['cost_usdt']:,.2f} USDT)")
                print(f"│ • 1st Entry:    ${pos.get('stage1_price', 0):,.2f}  |  Dip Entry: ${pos.get('stage2_price', 0):,.2f}")
                print(f"│ • Blended Avg:  ${pos['entry_price']:,.2f}")
                print(f"│ • Profit/Loss:  {pnl_col}{BOLD}{pnl_sign}${unrealized:,.2f} ({pnl_sign}{unrealized_pct:.2f}%){RESET}")
                print(f"│ • Adjusted TP:  ${pos['take_profit']:,.2f}  |  Adjusted SL: ${pos['stop_loss']:,.2f}")
        else:
            print(f"│ {YELLOW}{BOLD}STATUS: SOLD / 100% IN CASH WAITING FOR BUY SIGNAL{RESET}")
            print(f"│ • State:        Resting in USDT (Ready to deploy 75% on next Pivot Low)")
            print(f"│ • Total Trades: {summary['total_trades']}  |  Realized PnL: ${summary['realized_pnl']:,.2f} USDT")

        print(f"├────────────────────────────────────────────────────────────────────────┤{RESET}")
        p_sum = self.get_pivot_summary()
        lp = p_sum.get("latest_pivot")
        if lp:
            p_type = "🟢 PIVOT LOW (Support)" if lp.type == "low" else "🔴 PIVOT HIGH (Resistance)"
            print(f"│ {BOLD}Indicator Swings:{RESET} Latest Confirmed {p_type} @ ${lp.price:,.2f} (Bar {lp.bar})")
            print(f"│ {BOLD}Trend Bias:{RESET}       {p_sum.get('trend_bias', 'NEUTRAL')}")
        print(f"└────────────────────────────────────────────────────────────────────────┘{RESET}\n")

    def print_portfolio_report(self):
        """Displays formatted portfolio breakdown in terminal."""
        curr_price = self.latest_price
        summary = self.broker.get_account_summary(curr_price)
        init_bal = summary.get("initial_balance", 100.0)
        usdt_bal = summary.get("usdt_balance", 100.0)
        equity = summary.get("total_equity", 100.0)
        trades = self.broker.trade_history
        total_trades = len(trades)
        winning = [t for t in trades if t.get("pnl", 0) > 0]
        losing = [t for t in trades if t.get("pnl", 0) < 0]
        net_pnl = sum(t.get("pnl", 0) for t in trades)
        pnl_col = GREEN if net_pnl >= 0 else RED
        pnl_sign = "+" if net_pnl >= 0 else ""

        print(f"\n{CYAN}{BOLD}┌─────────────────── [PORTFOLIO PERFORMANCE REPORT] ───────────────────┐{RESET}")
        print(f"│ • Starting Capital:       ${init_bal:,.2f} USDT")
        print(f"│ • Current Cash Left:      ${usdt_bal:,.2f} USDT")
        print(f"│ • Total Current Equity:   ${equity:,.2f} USDT")
        print(f"├────────────────────────────────────────────────────────────────────────┤{RESET}")
        print(f"│ • Total Closed Trades:    {total_trades}  (Win Rate: {summary.get('win_rate', 0):.1f}%)")
        print(f"│ • Winning Trades:         {GREEN}{len(winning)}{RESET}  |  Losing Trades: {RED}{len(losing)}{RESET}")
        print(f"│ • Net Realized Result:    {pnl_col}{BOLD}{pnl_sign}${net_pnl:,.2f}{RESET}")
        print(f"└────────────────────────────────────────────────────────────────────────┘{RESET}\n")

    def print_pivots_table(self):
        """Prints formatted table of confirmed swing pivots."""
        p_sum = self.get_pivot_summary()
        reg = p_sum.get("regular_pivots", [])
        if not reg:
            print(f"\n{YELLOW}No confirmed pivots loaded yet.{RESET}\n")
            return

        print(f"\n{CYAN}{BOLD}┌────────────────── [RECENT CONFIRMED PIVOTS - MEXC] ──────────────────┐{RESET}")
        print(f"│ {'TYPE':<12} │ {'PRICE':<12} │ {'SWING BAR':<11} │ {'CONFIRMED AT':<14} │ {'OFFSET':<6} │")
        print(f"├──────────────┼──────────────┼─────────────┼────────────────┼────────┤{RESET}")
        for p in reg[-8:]:
            arrow = f"{RED}SWING HIGH{RESET}" if p.type == "high" else f"{GREEN}SWING LOW {RESET}"
            confirmed_bar = str(p.confirmed_at) if p.confirmed_at is not None else "--"
            print(f"│ {arrow:<21} │ ${p.price:<11,.2f} │ Bar {p.bar:<7} │ Bar {confirmed_bar:<10} │ {p.confirmed_offset:<6} │")
        print(f"└──────────────┴──────────────┴─────────────┴────────────────┴────────┘{RESET}\n")

    def print_hotkeys_help(self):
        print(f"\n{CYAN}{BOLD}[TERMINAL KEYBOARD HOTKEYS]{RESET}")
        print(" [S] - Display full account & position status card")
        print(" [O] - Display portfolio breakdown & trade performance")
        print(" [B] - Execute manual Stage 1 Buy (75% allocation)")
        print(" [C] - Execute manual Close Position (100% to USDT)")
        print(" [P] - Print recent indicator swing pivots table")
        print(" [R] - Force immediate market refresh")
        print(" [H] - Display this hotkey guide")
        print(" [Q] - Gracefully shut down the bot\n")

    def run(self):
        """Main automated trading loop."""
        self.print_banner()

        while self.running:
            try:
                cfg = self.config
                symbol = cfg["symbol"]
                tf = cfg["timeframe"]
                length = int(cfg["pivot_length"])
                rr = float(cfg["risk_reward_ratio"])
                sl_buffer = float(cfg["sl_buffer_pct"])
                pos_size_pct = float(cfg.get("initial_position_size_pct", 0.75))
                dip_trigger_pct = float(cfg.get("dip_trigger_pct", 0.015))
                poll_interval = int(cfg.get("poll_interval_seconds", 10))

                candles = self.fetch_candles(symbol, tf, limit=200)
                if not candles or len(candles) < (2 * length + 5):
                    time.sleep(poll_interval)
                    continue

                open_candle = candles[-1]
                curr_price = open_candle["close"]
                self.latest_price = curr_price

                # 1. Stop Loss & Take Profit Checks
                if self.broker.position is not None:
                    exit_trade = self.broker.check_position_limits(
                        current_high=open_candle["high"],
                        current_low=open_candle["low"],
                        current_close=curr_price,
                        timestamp=open_candle["time"],
                    )
                    if exit_trade:
                        pnl_col = GREEN if exit_trade["pnl"] >= 0 else RED
                        self.last_signal_info = {
                            "type": "SELL (Target / Stop Hit)",
                            "price": exit_trade["exit_price"],
                            "bar": self.last_checked_bar,
                            "time": open_candle["time"],
                            "action": f"SOLD ({'PROFIT' if exit_trade['pnl'] >= 0 else 'LOSS'})",
                            "reason": f"{exit_trade['exit_reason']} -> Realized: ${exit_trade['pnl']:,.2f}",
                        }
                        print(f"\n{pnl_col}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
                        print(f"{pnl_col}{BOLD}🎯 [POSITION CLOSED]{RESET} {exit_trade['exit_reason']}")
                        print(f"   Exit Price: ${exit_trade['exit_price']:,.2f}")
                        print(f"   Realized PnL: {pnl_col}${exit_trade['pnl']:,.2f} ({exit_trade['pnl_pct']:+.2f}%){RESET}")
                        print(f"   Cash Balance: ${self.broker.usdt_balance:,.2f} USDT")
                        print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
                        print(f"{pnl_col}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
                        self.telegram.send_sell_notification(exit_trade)

                # 2. Stage 2 Dip DCA Check (if holding Stage 1)
                if (
                    self.broker.position is not None
                    and self.broker.position.stage == 1
                    and cfg.get("trading_enabled", True)
                ):
                    initial_entry = self.broker.position.stage1_price
                    dip_target_price = initial_entry * (1.0 - dip_trigger_pct)

                    if curr_price <= dip_target_price and self.broker.usdt_balance >= 2.0:
                        dip_reason = f"Price dipped {((initial_entry - curr_price)/initial_entry)*100:.1f}% below initial entry (${initial_entry:,.2f})"
                        dip_order = self.broker.buy_dip(
                            price=curr_price,
                            reason=dip_reason,
                            risk_reward_ratio=rr,
                            sl_buffer_pct=sl_buffer,
                            timestamp=open_candle["time"],
                        )
                        if dip_order:
                            self.last_signal_info = {
                                "type": "DIP BUY (Stage 2 - 25%)",
                                "price": curr_price,
                                "bar": self.last_checked_bar,
                                "time": open_candle["time"],
                                "action": f"BOUGHT DIP ({dip_order['dip_quantity']} {self.broker.get_base_asset(symbol)})",
                                "reason": f"Averaged down entry to ${dip_order['blended_entry_price']:,.2f}",
                            }
                            print(f"\n{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
                            print(f"{GREEN}{BOLD}🟢 [STAGE 2 DIP BUY EXECUTED (25%)]{RESET}")
                            print(f"   Added: {dip_order['dip_quantity']} @ ${curr_price:,.2f} (${dip_order['dip_cost']:,.2f} USDT)")
                            print(f"   New Blended Entry: ${dip_order['blended_entry_price']:,.2f}")
                            print(f"   New Take Profit: ${dip_order['new_take_profit']:,.2f} | New Stop Loss: ${dip_order['new_stop_loss']:,.2f}")
                            print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
                            print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
                            self.telegram.send_dip_buy_notification(dip_order)

                # 3. Compute LuxAlgo Pivot Indicator on closed bars
                closed_candles = candles[:-1]
                highs = [c["high"] for c in closed_candles]
                lows = [c["low"] for c in closed_candles]
                timestamps = [c["time"] for c in closed_candles]

                pivot_res = pivot_points_high_low_missed(
                    high=highs,
                    low=lows,
                    length=length,
                    show_reg=True,
                    show_miss=True,
                    timestamps=timestamps,
                )
                self.latest_pivot_res = pivot_res

                if self.last_signal_info["type"] == "NONE" and pivot_res.regular_pivots:
                    lp = pivot_res.regular_pivots[-1]
                    self.last_signal_info = {
                        "type": "BUY (Pivot Low)" if lp.type == "low" else "SELL (Pivot High)",
                        "price": lp.price,
                        "bar": lp.bar,
                        "time": lp.confirmed_time or lp.time or int(time.time()),
                        "action": "MONITORING",
                        "reason": f"Historical confirmed {lp.type.upper()} @ ${lp.price:,.2f}",
                    }

                n_closed = len(closed_candles)
                current_closed_bar = n_closed - 1

                # 4. Check for pivot confirmed on the latest closed bar
                if current_closed_bar > self.last_checked_bar and current_closed_bar >= 0:
                    self.last_checked_bar = current_closed_bar
                    confirmed_pivots = [p for p in pivot_res.regular_pivots if p.confirmed_at == current_closed_bar]

                    for p in confirmed_pivots:
                        if p.type == "low":
                            print(f"\n{GREEN}{BOLD}🟢 [BUY SIGNAL - PIVOT LOW CONFIRMED]{RESET} Bar {p.bar} @ ${p.price:,.2f} (confirmed on bar {current_closed_bar})")

                            # Case A: 100% Cash -> Execute Stage 1 Buy (75%)
                            if self.broker.position is None and cfg.get("trading_enabled", True):
                                sl = p.price * (1.0 - sl_buffer)
                                risk = curr_price - sl
                                if risk > 0:
                                    tp = curr_price + (risk * rr)
                                    buy_order = self.broker.buy_initial(
                                        symbol=symbol,
                                        price=curr_price,
                                        stop_loss=sl,
                                        take_profit=tp,
                                        reason=f"Confirmed Pivot Low @ ${p.price:,.2f}",
                                        position_size_pct=pos_size_pct,
                                        timestamp=open_candle["time"],
                                    )
                                    if buy_order:
                                        self.last_signal_info = {
                                            "type": "BUY (Stage 1 - 75%)",
                                            "price": curr_price,
                                            "bar": p.bar,
                                            "time": open_candle["time"],
                                            "action": f"BOUGHT {buy_order['quantity']} {self.broker.get_base_asset(symbol)}",
                                            "reason": f"Confirmed Pivot Low @ ${p.price:,.2f}",
                                        }
                                        print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
                                        print(f"{GREEN}{BOLD}🚀 [STAGE 1 BUY EXECUTED (75%)]{RESET}")
                                        print(f"   Bought: {buy_order['quantity']} {self.broker.get_base_asset(symbol)} @ ${buy_order['price']:,.2f}")
                                        print(f"   Cost: ${buy_order['cost_usdt']:,.2f} USDT  (Reserve Cash: ${self.broker.usdt_balance:,.2f} USDT)")
                                        print(f"   Take Profit: ${buy_order['take_profit']:,.2f} | Stop Loss: ${buy_order['stop_loss']:,.2f}")
                                        print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
                                        print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
                                        self.telegram.send_buy_notification(buy_order)

                            # Case B: Already in Stage 1 & new Pivot Low formed at lower price -> Secondary Dip Buy (25%)
                            elif (
                                self.broker.position is not None
                                and self.broker.position.stage == 1
                                and p.price < self.broker.position.stage1_price
                                and cfg.get("trading_enabled", True)
                            ):
                                dip_reason = f"Secondary Pivot Low formed @ ${p.price:,.2f} (below Stage 1: ${self.broker.position.stage1_price:,.2f})"
                                dip_order = self.broker.buy_dip(
                                    price=curr_price,
                                    reason=dip_reason,
                                    risk_reward_ratio=rr,
                                    sl_buffer_pct=sl_buffer,
                                    timestamp=open_candle["time"],
                                )
                                if dip_order:
                                    self.last_signal_info = {
                                        "type": "DIP BUY (Stage 2 - 25%)",
                                        "price": curr_price,
                                        "bar": p.bar,
                                        "time": open_candle["time"],
                                        "action": f"BOUGHT DIP ({dip_order['dip_quantity']} {self.broker.get_base_asset(symbol)})",
                                        "reason": dip_reason,
                                    }
                                    print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
                                    print(f"{GREEN}{BOLD}🟢 [SECONDARY PIVOT LOW DIP BUY (25%)]{RESET}")
                                    print(f"   Added: {dip_order['dip_quantity']} @ ${curr_price:,.2f}")
                                    print(f"   New Blended Entry: ${dip_order['blended_entry_price']:,.2f}")
                                    print(f"   New Take Profit: ${dip_order['new_take_profit']:,.2f} | New Stop Loss: ${dip_order['new_stop_loss']:,.2f}")
                                    print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
                                    print(f"{GREEN}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
                                    self.telegram.send_dip_buy_notification(dip_order)

                        elif p.type == "high":
                            print(f"\n{RED}{BOLD}🔴 [SELL SIGNAL - PIVOT HIGH CONFIRMED]{RESET} Bar {p.bar} @ ${p.price:,.2f} (confirmed on bar {current_closed_bar})")

                            if self.broker.position is not None and cfg.get("trading_enabled", True):
                                sell_order = self.broker.sell(
                                    price=curr_price,
                                    reason=f"Confirmed Pivot High swing top (${p.price:,.2f})",
                                    timestamp=open_candle["time"],
                                )
                                if sell_order:
                                    pnl_col = GREEN if sell_order["pnl"] >= 0 else RED
                                    self.last_signal_info = {
                                        "type": "SELL (Pivot High)",
                                        "price": curr_price,
                                        "bar": p.bar,
                                        "time": open_candle["time"],
                                        "action": f"SOLD ALL (Cashed out ${sell_order['net_proceeds']:,.2f} USDT)",
                                        "reason": f"Confirmed Pivot High @ ${p.price:,.2f} | PnL: ${sell_order['pnl']:,.2f}",
                                    }
                                    print(f"{RED}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}")
                                    print(f"{RED}{BOLD}🛑 [SPOT SELL EXECUTED - 100% CASHED OUT]{RESET}")
                                    print(f"   Sold @ ${sell_order['exit_price']:,.2f}")
                                    print(f"   Realized PnL: {pnl_col}${sell_order['pnl']:,.2f} ({sell_order['pnl_pct']:+.2f}%){RESET}")
                                    print(f"   New USDT Balance: ${self.broker.usdt_balance:,.2f} USDT")
                                    print(f"   📨 Push notification sent to Telegram (@pivotraderbot)!")
                                    print(f"{RED}{BOLD}══════════════════════════════════════════════════════════════════════════════{RESET}\n")
                                    self.telegram.send_sell_notification(sell_order)

                # Periodic non-spammy heartbeat logging (every 60 seconds)
                now_ts = time.time()
                if now_ts - self.last_heartbeat_time >= 60.0:
                    self.last_heartbeat_time = now_ts
                    now_str = datetime.now().strftime("%H:%M:%S")
                    summary = self.broker.get_account_summary(curr_price)
                    if summary["has_position"]:
                        pos = summary["position"]
                        pnl = summary["unrealized_pnl"]
                        pnl_sign = "+" if pnl >= 0 else ""
                        pnl_col = GREEN if pnl >= 0 else RED
                        stage_lbl = "Stage 1 (75%)" if pos["stage"] == 1 else "Stage 2 (100% DCA)"
                        print(f"{DIM}[{now_str}]{RESET} 🟢 {BOLD}{symbol}{RESET}: ${curr_price:,.2f} | BOUGHT ({stage_lbl}) | Avg: ${pos['entry_price']:,.2f} | PnL: {pnl_col}{pnl_sign}${pnl:,.2f}{RESET} | TP: ${pos['take_profit']:,.2f}")
                    else:
                        print(f"{DIM}[{now_str}]{RESET} ⚪ {BOLD}{symbol}{RESET}: ${curr_price:,.2f} | SOLD (100% Cash: ${summary['usdt_balance']:,.2f} USDT) | Waiting for Pivot Low")

                # Sleep cycle with force refresh interruption check
                for _ in range(poll_interval * 5):
                    if not self.running or self.force_refresh_flag:
                        self.force_refresh_flag = False
                        break
                    time.sleep(0.2)

            except Exception as e:
                print(f"\n{RED}[BOT LOOP ERROR] {e}{RESET}")
                time.sleep(4)


if __name__ == "__main__":
    bot = TerminalPaperBot()
    bot.run()
