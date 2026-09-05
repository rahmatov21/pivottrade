"""
Telegram Remote Controller & Notification Center for PivotTrader
================================================================
Handles administrative command processing, dynamic configuration updates,
and crystal-clear push notifications for spot trading executions.
Strictly enforces admin user ID authentication.
"""

import threading
import time
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List
import telebot
from telebot import types

from config_manager import load_config, update_config_value


class TelegramManager:
    def __init__(
        self,
        broker=None,
        on_config_changed: Optional[Callable[[Dict[str, Any]], None]] = None,
        get_current_price: Optional[Callable[[], float]] = None,
        manual_sell_callback: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        manual_buy_callback: Optional[Callable[[], Optional[Dict[str, Any]]]] = None,
        get_pivot_summary: Optional[Callable[[], Dict[str, Any]]] = None,
        get_last_signal_info: Optional[Callable[[], Dict[str, Any]]] = None,
        mexc_client=None,
        auto_start: bool = True,
    ):
        self.broker = broker
        self.on_config_changed = on_config_changed
        self.get_current_price = get_current_price
        self.manual_sell_callback = manual_sell_callback
        self.manual_buy_callback = manual_buy_callback
        self.get_pivot_summary = get_pivot_summary
        self.get_last_signal_info = get_last_signal_info
        self.mexc_client = mexc_client

        self.bot: Optional[telebot.TeleBot] = None
        self.admin_id: Optional[str] = None
        self.is_connected = False
        self.polling_thread: Optional[threading.Thread] = None
        self._is_polling = False

        if auto_start:
            self.init_bot()

    def init_bot(self) -> bool:
        """Initializes the Telegram bot instance with credentials from .env/config."""
        cfg = load_config()
        token = cfg.get("telegram_bot_token", "").strip()
        admin_id = str(cfg.get("telegram_admin_id", "")).strip()

        if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
            print("[TELEGRAM] Bot token not configured. Running in terminal-only mode.")
            print("           Set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_ID in .env or config.json")
            self.is_connected = False
            return False

        try:
            self.bot = telebot.TeleBot(token, parse_mode="Markdown")
            self.admin_id = admin_id
            self.register_handlers()
            self.is_connected = True
            self._is_polling = True

            # Start polling in background daemon thread
            self.polling_thread = threading.Thread(target=self._run_polling, daemon=True)
            self.polling_thread.start()

            print(f"[TELEGRAM] Connected to Telegram! Admin ID authorized: {self.admin_id}")
            self.send_alert(
                "🚀 Bot Online (MEXC Mode)",
                f"PivotTrader Pro terminal is online!\n"
                f"• Exchange: `{cfg.get('exchange', 'MEXC')}`\n"
                f"• Pair: `{cfg['symbol']}` | TF: `{cfg['timeframe']}` | Length: `{cfg['pivot_length']}`\n"
                f"• Strategy: 75% Initial Entry + 25% Dip DCA\n"
                f"• Type /status or /help to interact."
            )
            return True
        except Exception as e:
            print(f"[TELEGRAM ERROR] Failed to connect to Telegram: {e}")
            self.is_connected = False
            return False

    def stop(self) -> None:
        """Stops Telegram polling cleanly."""
        self._is_polling = False
        if self.bot and self.is_connected:
            try:
                self.bot.stop_polling()
            except Exception:
                pass
            self.is_connected = False

    def _run_polling(self) -> None:
        """Runs Telegram long polling with automatic reconnection and conflict avoidance."""
        if not self.bot:
            return
        while self._is_polling:
            try:
                self.bot.infinity_polling(timeout=15, long_polling_timeout=10)
            except Exception as e:
                err_str = str(e)
                if "409" in err_str:
                    print(f"[TELEGRAM WARNING] Another bot instance is already active. Retrying in 10s...")
                    time.sleep(10)
                else:
                    time.sleep(5)

    def is_admin(self, message) -> bool:
        """Verifies that the message sender strictly matches telegram_admin_id."""
        if not self.admin_id or self.admin_id == "YOUR_ADMIN_ID":
            return True
        sender_id = str(message.from_user.id).strip()
        if sender_id != str(self.admin_id).strip():
            print(f"[TELEGRAM WARNING] Unauthorized message from User ID: {sender_id}")
            try:
                self.bot.reply_to(message, "⛔ *Access Denied*\nThis bot is private and restricted to the admin.")
            except Exception:
                pass
            return False
        return True

    def get_main_keyboard(self) -> types.ReplyKeyboardMarkup:
        """Constructs interactive persistent quick-action keyboard."""
        markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
        markup.add(
            types.KeyboardButton("💼 Portfolio"),
            types.KeyboardButton("📊 Status"),
        )
        markup.add(
            types.KeyboardButton("💵 Balance"),
            types.KeyboardButton("📈 Pivots"),
        )
        markup.add(
            types.KeyboardButton("⚙️ Config"),
            types.KeyboardButton("💼 MEXC Wallet"),
        )
        markup.add(
            types.KeyboardButton("🟢 Buy (Stage 1)"),
            types.KeyboardButton("🔴 Close Position"),
        )
        markup.add(
            types.KeyboardButton("⏸️ Pause Bot"),
            types.KeyboardButton("▶️ Resume Bot"),
        )
        markup.add(
            types.KeyboardButton("🔄 Reset ($100)"),
        )
        return markup

    def register_handlers(self) -> None:
        """Registers all Telegram slash commands and message handlers."""
        bot = self.bot
        if not bot:
            return

        @bot.message_handler(commands=["start", "help"])
        def cmd_help(message):
            if not self.is_admin(message):
                return
            help_text = (
                "🤖 *PivotTrader Pro — Terminal Controller*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "📊 *Monitoring Commands:*\n"
                "• `/portfolio` — *Full Profit & Loss Breakdown*, money left after trades & history\n"
                "• `/status` — *Bought or Sold?* Live DCA Stage, PnL & last signal\n"
                "• `/balance` — $100 paper account equity & metrics\n"
                "• `/pivots` — Recent indicator swings (pivots & levels)\n"
                "• `/mexc_wallet` — View real MEXC spot balances\n"
                "• `/config` — View current trading settings\n\n"
                "⚙️ *Configuration Commands:*\n"
                "• `/set_symbol <SYM>` — Switch pair (e.g. `/set_symbol ETHUSDT`)\n"
                "• `/set_timeframe <TF>` — Change TF (e.g. `/set_timeframe 5m`)\n"
                "• `/set_length <N>` — Pivot strength (e.g. `/set_length 10`)\n"
                "• `/set_risk <R>` — Risk:Reward ratio (e.g. `/set_risk 1.5`)\n\n"
                "🎮 *Trade Execution Commands:*\n"
                "• `/pause` — Pause automated buying\n"
                "• `/resume` — Resume automated buying\n"
                "• `/buy` — Market spot BUY now (Stage 1: 75%)\n"
                "• `/close_position` — Market spot SELL to cash out\n"
                "• `/reset_equity` — Reset paper balance back to $100.00\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💡 _Tap the quick buttons below for 1-click execution!_"
            )
            bot.reply_to(message, help_text, reply_markup=self.get_main_keyboard())

        @bot.message_handler(commands=["status"])
        def cmd_status(message):
            if not self.is_admin(message):
                return
            cfg = load_config()
            symbol = cfg["symbol"]
            tf = cfg["timeframe"]
            exchange = cfg.get("exchange", "MEXC")
            trading_state = "🟢 ACTIVE (Auto-buying ON)" if cfg["trading_enabled"] else "⏸️ PAUSED (Auto-buying OFF)"

            curr_price = self.get_current_price() if self.get_current_price else 0.0
            price_str = f"${curr_price:,.2f}" if curr_price > 0 else "Loading..."

            summary = self.broker.get_account_summary(curr_price) if self.broker else {}
            has_pos = summary.get("has_position", False)
            pos = summary.get("position")

            # 1. Clear BOUGHT (Stage 1 / Stage 2) or SOLD Header
            if has_pos and pos:
                unrealized = summary.get("unrealized_pnl", 0.0)
                unrealized_pct = summary.get("unrealized_pnl_pct", 0.0)
                pnl_sign = "+" if unrealized >= 0 else ""
                pnl_emoji = "🟢" if unrealized >= 0 else "🔴"

                stage_num = pos.get("stage", 1)
                stage_badge = "STAGE 1 (75% Invested)" if stage_num == 1 else "STAGE 2 (100% Invested after Dip DCA)"
                dip_info = ""

                if stage_num == 1:
                    dip_trigger_price = pos['entry_price'] * (1.0 - float(cfg.get("dip_trigger_pct", 0.015)))
                    dip_info = (
                        f"💵 *Cash in Reserve for Dip Buy:* `${summary.get('usdt_balance', 0):,.2f} USDT (25%)`\n"
                        f"📉 *Dip Buy Trigger Level:* Below `${dip_trigger_price:,.2f}` (-{float(cfg.get('dip_trigger_pct', 0.015))*100:.1f}%)\n"
                    )
                else:
                    dip_info = (
                        f"🛒 *1st Entry Price (75%):* `${pos.get('stage1_price', 0):,.2f}`\n"
                        f"🛒 *Dip Buy Price (25%):* `${pos.get('stage2_price', 0):,.2f}`\n"
                        f"⚖️ *Blended Average Entry:* `${pos['entry_price']:,.2f}`\n"
                    )

                position_header = (
                    f"🟢 *CURRENT STATUS: BOUGHT ({stage_badge})*\n"
                    f"👉 *You own:* `{pos['quantity']} {pos['base_asset']}`\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💰 *Current {exchange} Price:* `{price_str}`\n"
                    f"🛒 *Entry Price:* `${pos['entry_price']:,.2f}`\n"
                    f"{pnl_emoji} *Your Profit / Loss:* `{pnl_sign}${unrealized:,.2f} ({pnl_sign}{unrealized_pct:.2f}%)`\n"
                    f"💵 *Total Money Invested:* `${pos['cost_usdt']:,.2f} USDT`\n\n"
                    f"{dip_info}"
                    f"🎯 *Auto-Sell Target (Take Profit):* `${pos['take_profit']:,.2f}`\n"
                    f"🛑 *Safety Exit (Stop Loss):* `${pos['stop_loss']:,.2f}`\n"
                    f"💡 *Reason:* {pos['entry_reason']}\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "👀 *WHAT THE BOT IS DOING NOW:*\n"
                    + ("Watching for DIP BUY (25%) or Take Profit exit." if stage_num == 1 else "Monitoring price to SELL at profit target or swing top.")
                )
            else:
                last_trade = self.broker.trade_history[-1] if (self.broker and self.broker.trade_history) else None
                last_trade_text = ""
                if last_trade:
                    pnl_sign = "+" if last_trade.get("pnl", 0) >= 0 else ""
                    pnl_col = "🟢" if last_trade.get("pnl", 0) >= 0 else "🔴"
                    last_trade_text = (
                        f"📝 *Last Closed Trade:* Sold at `${last_trade.get('exit_price', 0):,.2f}`\n"
                        f"   {pnl_col} Result: `{pnl_sign}${last_trade.get('pnl', 0):,.2f} ({pnl_sign}{last_trade.get('pnl_pct', 0):.2f}%)`\n"
                        f"   Reason: {last_trade.get('exit_reason', 'N/A')}\n"
                    )

                position_header = (
                    "⚪ *CURRENT STATUS: SOLD / IN CASH (WAITING)*\n"
                    "👉 *You are 100% in USDT Cash (Ready for next trade)*\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"💵 *Available USDT Cash:* `${summary.get('usdt_balance', 0):,.2f} USDT` (Portfolio: $100)\n"
                    f"💰 *Current {symbol} Price:* `{price_str}` ({exchange})\n"
                    f"{last_trade_text}"
                    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    "⏳ *WHAT THE BOT IS DOING NOW:*\n"
                    "Waiting for the next *Pivot Low (BUY signal)* to automatically buy 75%."
                )

            # 2. Last Signal Information
            last_sig_text = ""
            if self.get_last_signal_info:
                sig_info = self.get_last_signal_info()
                if sig_info and sig_info.get("type") != "NONE":
                    sig_type = sig_info.get("type", "UNKNOWN")
                    sig_emoji = "🟢" if "BUY" in sig_type else "🔴"
                    action_taken = sig_info.get("action", "UNKNOWN")
                    price_val = sig_info.get("price", 0.0)
                    reason_val = sig_info.get("reason", "")
                    time_str = datetime.fromtimestamp(sig_info["time"]).strftime("%H:%M:%S") if sig_info.get("time") else "Recently"

                    last_sig_text = (
                        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📡 *LAST SIGNAL DETECTED:*\n"
                        f"• *Signal:* {sig_emoji} *{sig_type}*\n"
                        f"• *Price at Signal:* `${price_val:,.2f}`\n"
                        f"• *Action Taken by Bot:* *{action_taken}*\n"
                        f"• *Details:* {reason_val} ({time_str})\n"
                    )

            # 3. Market Structure / Trend Telemetry
            trend_text = ""
            if self.get_pivot_summary:
                p_sum = self.get_pivot_summary()
                latest_p = p_sum.get("latest_pivot")
                if latest_p:
                    arrow = "🟢 Pivot Low" if latest_p.type == "low" else "🔴 Pivot High"
                    trend_text = (
                        f"• *Market Structure:* `{p_sum.get('trend_bias', 'NEUTRAL')}`\n"
                        f"• *Latest Swing Point:* {arrow} at `${latest_p.price:,.2f}`\n"
                    )

            full_status = (
                f"{position_header}\n"
                f"{last_sig_text}"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💼 *MARKET & ACCOUNT SUMMARY:*\n"
                f"• *Exchange:* `{exchange}` | *Pair:* `{symbol}` | *TF:* `{tf}`\n"
                f"• *Total Portfolio Equity:* `${summary.get('total_equity', 0):,.2f} USDT`\n"
                f"• *Total Realized Profit:* `${summary.get('realized_pnl', 0):,.2f}` ({summary.get('win_rate', 0)}% Win Rate)\n"
                f"{trend_text}"
                f"• *Bot Automation:* {trading_state}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            bot.reply_to(message, full_status)

        @bot.message_handler(commands=["balance"])
        def cmd_balance(message):
            if not self.is_admin(message):
                return
            curr_price = self.get_current_price() if self.get_current_price else 0.0
            summary = self.broker.get_account_summary(curr_price) if self.broker else {}

            total_pnl = summary.get("realized_pnl", 0.0)
            pnl_sign = "+" if total_pnl >= 0 else ""
            pnl_col = "🟢" if total_pnl >= 0 else "🔴"

            pos_desc = "100% Cash (USDT)"
            if summary.get("has_position") and summary.get("position"):
                p = summary["position"]
                pos_desc = f"{summary.get('stage_desc', 'In Position')} | {p['quantity']} {p['base_asset']} (${(p['quantity'] * curr_price):,.2f})"

            text = (
                "💼 *ACCOUNT BALANCE & METRICS ($100 Starting Portfolio)*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 *USDT Cash Balance:* `${summary.get('usdt_balance', 0):,.2f}`\n"
                f"💎 *Total Portfolio Equity:* `${summary.get('total_equity', 0):,.2f}`\n"
                f"📦 *Position State:* `{pos_desc}`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{pnl_col} *Total Realized PnL:* `{pnl_sign}${total_pnl:,.2f}`\n"
                f"📊 *Trades Completed:* `{summary.get('total_trades', 0)}`\n"
                f"🏆 *Win Rate:* `{summary.get('win_rate', 0)}%`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            bot.reply_to(message, text)

        @bot.message_handler(commands=["portfolio"])
        def cmd_portfolio(message):
            if not self.is_admin(message):
                return
            curr_price = self.get_current_price() if self.get_current_price else 0.0
            summary = self.broker.get_account_summary(curr_price) if self.broker else {}

            init_bal = summary.get("initial_balance", 100.0)
            usdt_bal = summary.get("usdt_balance", 100.0)
            equity = summary.get("total_equity", 100.0)
            trades = self.broker.trade_history if self.broker else []
            total_trades = len(trades)

            winning = [t for t in trades if t.get("pnl", 0) > 0]
            losing = [t for t in trades if t.get("pnl", 0) < 0]
            net_pnl = sum(t.get("pnl", 0) for t in trades)
            net_pnl_pct = ((equity - init_bal) / init_bal) * 100.0 if init_bal > 0 else 0.0

            pnl_sign = "+" if net_pnl >= 0 else ""
            pnl_emoji = "🟢 PROFIT" if net_pnl >= 0 else "🔴 LOSS / MINUS"

            # Open Position Details
            open_pos = summary.get("position")
            if open_pos:
                unrealized = summary.get("unrealized_pnl", 0.0)
                unrealized_pct = summary.get("unrealized_pnl_pct", 0.0)
                u_sign = "+" if unrealized >= 0 else ""
                pos_text = (
                    f"🟢 *ACTIVE POSITION:* `{open_pos['quantity']} {open_pos['base_asset']}` (Stage {open_pos.get('stage', 1)})\n"
                    f"• *Entry Price:* `${open_pos['entry_price']:,.2f}`  |  *Cost:* `${open_pos['cost_usdt']:,.2f} USDT`\n"
                    f"• *Live MEXC Price:* `${curr_price:,.2f}`\n"
                    f"• *Open PnL:* `{u_sign}${unrealized:,.2f} ({u_sign}{unrealized_pct:.2f}%)`\n"
                )
            else:
                pos_text = "⚪ *ACTIVE POSITION:* None (100% in USDT Cash waiting to buy)\n"

            # History of closed trades
            if total_trades == 0:
                history_text = "_No closed trades yet. Bot is resting in cash waiting for first buy signal._"
            else:
                history_lines = []
                for i, t in enumerate(trades[-10:], 1):
                    t_sign = "+" if t.get("pnl", 0) >= 0 else ""
                    t_col = "🟢" if t.get("pnl", 0) >= 0 else "🔴"
                    history_lines.append(
                        f"*{i}.* {t['symbol']} | Sold @ `${t.get('exit_price', 0):,.2f}`\n"
                        f"   {t_col} PnL: `{t_sign}${t.get('pnl', 0):,.2f} ({t_sign}{t.get('pnl_pct', 0):.2f}%)`\n"
                        f"   Reason: _{t.get('exit_reason', 'N/A')}_"
                    )
                history_text = "\n".join(history_lines)

            report = (
                "💼 *PORTFOLIO PERFORMANCE & BALANCE REPORT*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💰 *Starting Capital:* `${init_bal:,.2f} USDT`\n"
                f"💵 *Current Cash Left:* `${usdt_bal:,.2f} USDT`\n"
                f"💎 *Current Total Equity:* `${equity:,.2f} USDT`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 *TRADING SUMMARY:*\n"
                f"• *Total Trades Executed:* `{total_trades}`\n"
                f"• *Winning Trades:* `{len(winning)}` 🟢  |  *Losing Trades:* `{len(losing)}` 🔴\n"
                f"• *Win Rate:* `{summary.get('win_rate', 0):.1f}%`\n"
                f"• *Total Net Result:* {pnl_emoji} `{pnl_sign}${net_pnl:,.2f} ({pnl_sign}{net_pnl_pct:.2f}%)`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"{pos_text}"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📜 *RECENT COMPLETED TRADES:*\n"
                f"{history_text}\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━"
            )
            bot.reply_to(message, report)

        @bot.message_handler(commands=["mexc_wallet"])
        def cmd_mexc_wallet(message):
            if not self.is_admin(message):
                return
            if not self.mexc_client or not self.mexc_client.has_credentials:
                bot.reply_to(
                    message,
                    "⚠️ *MEXC API Keys Not Configured*\n"
                    "Add `MEXC_API_KEY` and `MEXC_SECRET_KEY` in `.env` to query your real MEXC wallet.",
                )
                return

            res = self.mexc_client.get_account_balances()
            if "error" in res:
                bot.reply_to(message, f"❌ *MEXC API Error:* `{res['error']}`")
                return

            balances = res.get("balances", {})
            if not balances:
                bot.reply_to(message, "💼 *MEXC Spot Wallet:* No non-zero balances found.")
                return

            lines = ["💼 *REAL MEXC SPOT BALANCES*", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            for asset, b in balances.items():
                lines.append(f"• *{asset}:* Free: `{b['free']}` | Locked: `{b['locked']}`")
            lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━")
            bot.reply_to(message, "\n".join(lines))

        @bot.message_handler(commands=["pivots"])
        def cmd_pivots(message):
            if not self.is_admin(message):
                return
            if not self.get_pivot_summary:
                bot.reply_to(message, "Indicator data loading...")
                return
            p_sum = self.get_pivot_summary()
            reg = p_sum.get("regular_pivots", [])
            miss = p_sum.get("missed_pivots", [])

            lines = ["📊 *INDICATOR SWING POINTS (LuxAlgo on MEXC)*", "━━━━━━━━━━━━━━━━━━━━━━━━━━"]
            lines.append(f"• Total Confirmed Pivots: *{len(reg)}*")
            lines.append(f"• Total Missed (Ghost) Levels: *{len(miss)}*")
            lines.append("\n*Recent Swings (Oldest -> Newest):*")
            for p in reg[-5:]:
                arrow = "🔴 SWING HIGH (Resistance)" if p.type == "high" else "🟢 SWING LOW (Support)"
                lines.append(f"• {arrow} at `${p.price:,.2f}` (confirmed on bar {p.confirmed_at})")

            if p_sum.get("projection_point"):
                pp = p_sum["projection_point"]
                lines.append(f"\n🔮 *Developing Live Swing:* {pp.type.upper()} at `${pp.price:,.2f}`")

            bot.reply_to(message, "\n".join(lines))

        @bot.message_handler(commands=["config"])
        def cmd_config(message):
            if not self.is_admin(message):
                return
            cfg = load_config()
            text = (
                "⚙️ *BOT CONFIGURATION*\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"• *Exchange Feed:* `{cfg.get('exchange', 'MEXC')}`\n"
                f"• *Symbol:* `{cfg['symbol']}`\n"
                f"• *Timeframe:* `{cfg['timeframe']}`\n"
                f"• *Pivot Length:* `{cfg['pivot_length']}`\n"
                f"• *Strategy:* 75% Initial Entry + 25% Dip DCA\n"
                f"• *Dip Trigger:* `{float(cfg.get('dip_trigger_pct', 0.015)) * 100}%` dip\n"
                f"• *Risk:Reward Ratio:* `{cfg['risk_reward_ratio']}R`\n"
                f"• *Stop-Loss Buffer:* `{cfg['sl_buffer_pct'] * 100}%`\n"
                f"• *Auto-Trading:* `{'Enabled 🟢' if cfg['trading_enabled'] else 'Paused ⏸️'}`\n"
                f"• *Poll Interval:* `{cfg['poll_interval_seconds']}s`\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "_Commands to change:_\n"
                "`/set_symbol <SYM>` | `/set_timeframe <TF>` | `/set_length <N>` | `/set_risk <R>`"
            )
            bot.reply_to(message, text)

        @bot.message_handler(commands=["set_symbol"])
        def cmd_set_symbol(message):
            if not self.is_admin(message):
                return
            args = message.text.strip().split()
            if len(args) < 2:
                bot.reply_to(message, "Usage: `/set_symbol <SYMBOL>` (e.g. `/set_symbol ETHUSDT`)")
                return
            new_sym = args[1].upper().strip()
            cfg = update_config_value("symbol", new_sym)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, f"✅ Symbol switched to *{new_sym}* on MEXC.")

        @bot.message_handler(commands=["set_timeframe"])
        def cmd_set_timeframe(message):
            if not self.is_admin(message):
                return
            args = message.text.strip().split()
            valid_tfs = ["1m", "5m", "15m", "30m", "1h", "60m", "4h", "1d"]
            if len(args) < 2 or args[1].lower() not in valid_tfs:
                bot.reply_to(message, f"Usage: `/set_timeframe <TF>`\nValid options: {', '.join(valid_tfs)}")
                return
            new_tf = args[1].lower().strip()
            cfg = update_config_value("timeframe", new_tf)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, f"✅ Timeframe updated to *{new_tf}*.")

        @bot.message_handler(commands=["set_length"])
        def cmd_set_length(message):
            if not self.is_admin(message):
                return
            args = message.text.strip().split()
            try:
                length = int(args[1])
                if length < 3 or length > 50:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "Usage: `/set_length <3-50>` (e.g. `/set_length 10`)")
                return
            cfg = update_config_value("pivot_length", length)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, f"✅ Pivot Length set to *{length}*.")

        @bot.message_handler(commands=["set_risk"])
        def cmd_set_risk(message):
            if not self.is_admin(message):
                return
            args = message.text.strip().split()
            try:
                rr = float(args[1])
                if rr <= 0.5 or rr > 10.0:
                    raise ValueError()
            except Exception:
                bot.reply_to(message, "Usage: `/set_risk <0.5-10.0>` (e.g. `/set_risk 1.5`)")
                return
            cfg = update_config_value("risk_reward_ratio", rr)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, f"✅ Risk:Reward Ratio set to *{rr}R*.")

        @bot.message_handler(commands=["pause"])
        def cmd_pause(message):
            if not self.is_admin(message):
                return
            cfg = update_config_value("trading_enabled", False)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, "⏸️ Auto-trading *PAUSED*. Bot will NOT open new buy orders.")

        @bot.message_handler(commands=["resume"])
        def cmd_resume(message):
            if not self.is_admin(message):
                return
            cfg = update_config_value("trading_enabled", True)
            if self.on_config_changed:
                self.on_config_changed(cfg)
            bot.reply_to(message, "▶️ Auto-trading *RESUMED*. Confirmed buy signals will execute.")

        @bot.message_handler(commands=["buy"])
        def cmd_buy(message):
            if not self.is_admin(message):
                return
            if not self.manual_buy_callback:
                bot.reply_to(message, "⚠️ Manual buy not available.")
                return
            trade = self.manual_buy_callback()
            if trade:
                bot.reply_to(message, f"✅ Spot buy executed at ${trade['price']:,.2f}.")
            else:
                bot.reply_to(message, "⚠️ Could not execute buy (already holding position or insufficient cash).")

        @bot.message_handler(commands=["close_position"])
        def cmd_close_position(message):
            if not self.is_admin(message):
                return
            if not self.broker or not self.broker.position:
                bot.reply_to(message, "⚠️ No active position to close.")
                return
            if self.manual_sell_callback:
                trade = self.manual_sell_callback()
                if trade:
                    bot.reply_to(message, f"✅ Position closed at ${trade['exit_price']:,.2f} | PnL: ${trade['pnl']:,.2f}.", reply_markup=self.get_main_keyboard())
                else:
                    bot.reply_to(message, "⚠️ Failed to close position.", reply_markup=self.get_main_keyboard())

        @bot.message_handler(commands=["reset_equity"])
        def cmd_reset_equity(message):
            if not self.is_admin(message):
                return
            if self.broker:
                self.broker.position = None
                self.broker.usdt_balance = 100.0
                self.broker.initial_balance = 100.0
                self.broker.trade_history = []
                self.broker.save_state()
                bot.reply_to(
                    message,
                    "🔄 *Paper Account Reset to $100.00 USDT*\n"
                    "• Position: Cleared (100% Cash)\n"
                    "• Cash Balance: $100.00 USDT\n"
                    "• Ready for fresh automated trades!",
                    reply_markup=self.get_main_keyboard(),
                )

        @bot.message_handler(func=lambda msg: True)
        def handle_text_buttons(message):
            if not self.is_admin(message):
                return
            text = (message.text or "").strip()
            if text in ["💼 Portfolio", "/portfolio"]:
                cmd_portfolio(message)
            elif text == "📊 Status":
                cmd_status(message)
            elif text == "💵 Balance":
                cmd_balance(message)
            elif text == "📈 Pivots":
                cmd_pivots(message)
            elif text == "⚙️ Config":
                cmd_config(message)
            elif text == "🟢 Buy (Stage 1)":
                cmd_buy(message)
            elif text == "🔴 Close Position":
                cmd_close_position(message)
            elif text == "⏸️ Pause Bot":
                cmd_pause(message)
            elif text == "▶️ Resume Bot":
                cmd_resume(message)
            elif text == "💼 MEXC Wallet":
                cmd_mexc_wallet(message)
            elif text == "🔄 Reset ($100)":
                cmd_reset_equity(message)

    def send_buy_notification(self, trade_info: Dict[str, Any]) -> None:
        """Sends crystal-clear notification when Stage 1 spot buy is executed."""
        if not self.bot or not self.admin_id:
            return
        msg = (
            "🟢 *[BOT ACTION: STAGE 1 BUY (75%)]*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Action:* BOUGHT {trade_info['symbol']} with 75% Portfolio\n"
            f"💰 *Buy Price:* `${trade_info['price']:,.2f}`\n"
            f"📦 *Amount Bought:* `{trade_info['quantity']}`\n"
            f"💵 *Total Cost:* `${trade_info['cost_usdt']:,.2f} USDT`\n\n"
            f"🎯 *Auto-Sell Target (Take Profit):* `${trade_info['take_profit']:,.2f}`\n"
            f"🛑 *Safety Exit (Stop Loss):* `${trade_info['stop_loss']:,.2f}`\n"
            f"💡 *Why:* {trade_info['reason']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *Reserve Cash for Dip:* `${trade_info['remaining_usdt']:,.2f} USDT (25%)`\n"
            "📌 *Status:* STAGE 1 (75% Invested).\n"
            "If price dips, the bot will deploy the remaining 25% to average down entry!"
        )
        try:
            self.bot.send_message(self.admin_id, msg)
        except Exception as e:
            print(f"[TELEGRAM ERROR] Failed to send buy alert: {e}")

    def send_dip_buy_notification(self, dip_info: Dict[str, Any]) -> None:
        """Sends notification when Stage 2 Dip Buy (25%) is executed."""
        if not self.bot or not self.admin_id:
            return
        msg = (
            "🟢 *[BOT ACTION: STAGE 2 DIP BUY (25%)]*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Action:* BOUGHT DIP with remaining 25% cash\n"
            f"💰 *Dip Price:* `${dip_info['dip_price']:,.2f}`\n"
            f"💵 *Dip Cash Deployed:* `${dip_info['dip_cost']:,.2f} USDT`\n"
            f"📦 *Added Quantity:* `{dip_info['dip_quantity']}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚖️ *NEW AVERAGE ENTRY PRICE:* `${dip_info['blended_entry_price']:,.2f}`\n"
            f"🎯 *New Take Profit:* `${dip_info['new_take_profit']:,.2f}`\n"
            f"🛑 *New Stop Loss:* `${dip_info['new_stop_loss']:,.2f}`\n"
            f"💡 *Why:* {dip_info['reason']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "📌 *Status:* STAGE 2 (100% Invested).\n"
            "Average price has been averaged down, making profit target closer on the bounce!"
        )
        try:
            self.bot.send_message(self.admin_id, msg)
        except Exception as e:
            print(f"[TELEGRAM ERROR] Failed to send dip buy alert: {e}")

    def send_sell_notification(self, trade_info: Dict[str, Any]) -> None:
        """Sends crystal-clear notification when a spot sell is executed."""
        if not self.bot or not self.admin_id:
            return
        pnl_val = trade_info["pnl"]
        pnl_pct = trade_info["pnl_pct"]
        pnl_emoji = "🟢 PROFIT" if pnl_val >= 0 else "🔴 LOSS"
        pnl_sign = "+" if pnl_val >= 0 else ""

        msg = (
            "🔴 *[BOT ACTION: JUST SOLD ALL COIN]*\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Action:* SOLD ALL {trade_info['symbol']} -> CASHED OUT\n"
            f"💰 *Sell Price:* `${trade_info['exit_price']:,.2f}`\n"
            f"💵 *Net Money Received:* `${trade_info['net_proceeds']:,.2f} USDT`\n"
            f"{pnl_emoji}: *{pnl_sign}${pnl_val:,.2f} ({pnl_sign}{pnl_pct:.2f}%)*\n"
            f"💡 *Why:* {trade_info['exit_reason']}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💵 *New Total USDT Balance:* `${self.broker.usdt_balance:,.2f} USDT`\n"
            "📌 *Current Status:* SOLD (100% CASH).\n"
            "The bot is now waiting in cash for the next Pivot Low to start Stage 1."
        )
        try:
            self.bot.send_message(self.admin_id, msg)
        except Exception as e:
            print(f"[TELEGRAM ERROR] Failed to send sell alert: {e}")

    def send_alert(self, title: str, text: str) -> None:
        """Sends arbitrary admin alert message."""
        if not self.bot or not self.admin_id or self.admin_id == "YOUR_ADMIN_ID":
            return
        msg = f"📢 *{title}*\n{text}"
        try:
            self.bot.send_message(self.admin_id, msg)
        except Exception as e:
            print(f"[TELEGRAM ERROR] Failed to send alert: {e}")
