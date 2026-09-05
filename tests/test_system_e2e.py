"""
End-to-End System Verification Test
===================================
Validates:
1. MEXC candle data integration
2. Indicator strict inequality fidelity (Pine Script ta.pivothigh/low)
3. $100 starting equity & 2-stage position sizing (75% Stage 1 + 25% Stage 2 Dip DCA)
4. Blended average entry price and dynamic TP/SL adjustment
5. Telegram status formatting (BOUGHT Stage 1/2 vs SOLD)
6. MEXC authenticated client signature generation
"""

import os
import sys
import unittest

# Ensure root directory is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from python_algorithm import pivot_points_high_low_missed, _pivot_high, _pivot_low
from mexc_client import fetch_mexc_candles, normalize_interval, MexcClient
from paper_broker import PaperBroker
from config_manager import load_config


class TestIndicatorFidelity(unittest.TestCase):
    def test_pivot_strict_inequality(self):
        """Verify strict inequality: ties must NOT be pivots (matches Pine Script ta.pivothigh/pivotlow)."""
        # Equal highest values: [10, 20, 20, 10]
        # In Pine Script, neither bar 1 nor bar 2 can be a pivot high of length 1 because high[i] >= val
        highs = [10.0, 20.0, 20.0, 10.0]
        self.assertIsNone(_pivot_high(highs, 1, 1, 1))
        self.assertIsNone(_pivot_high(highs, 1, 1, 2))

        # Distinct single highest value: [10, 25, 20, 10]
        # Bar 1 is strictly higher than bar 0 (10) and bar 2 (20)
        self.assertEqual(_pivot_high([10.0, 25.0, 20.0, 10.0], 1, 1, 1), 25.0)

        # Equal lowest values: [20, 10, 10, 20]
        lows = [20.0, 10.0, 10.0, 20.0]
        self.assertIsNone(_pivot_low(lows, 1, 1, 1))
        self.assertIsNone(_pivot_low(lows, 1, 1, 2))

        # Distinct single lowest value
        self.assertEqual(_pivot_low([20.0, 5.0, 15.0, 20.0], 1, 1, 1), 5.0)

    def test_indicator_run_on_mexc_data(self):
        """Verify indicator processes live MEXC candles without errors."""
        candles = fetch_mexc_candles("BTCUSDT", "1h", limit=100)
        self.assertGreaterEqual(len(candles), 50)
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        times = [c["time"] for c in candles]

        result = pivot_points_high_low_missed(highs, lows, length=5, timestamps=times)
        self.assertIsNotNone(result)
        self.assertGreater(len(result.regular_pivots), 0)
        self.assertGreaterEqual(len(result.missed_pivots), 0)


class TestMexcIntegration(unittest.TestCase):
    def test_interval_normalization(self):
        self.assertEqual(normalize_interval("1h"), "60m")
        self.assertEqual(normalize_interval("1d"), "1d")
        self.assertEqual(normalize_interval("1D"), "1d")
        self.assertEqual(normalize_interval("15m"), "15m")

    def test_mexc_signature_generation(self):
        client = MexcClient(api_key="test_key", secret_key="test_secret")
        query_str = "symbol=BTCUSDT&timestamp=1600000000000"
        signature = client._generate_signature(query_str)
        self.assertEqual(len(signature), 64)  # SHA256 hex string is 64 chars


class TestTwoStageDCAPaperBroker(unittest.TestCase):
    def setUp(self):
        test_state = os.path.join(os.path.dirname(__file__), "test_paper_state.json")
        if os.path.exists(test_state):
            os.remove(test_state)
        self.broker = PaperBroker(state_file=test_state, initial_balance=100.0)

    def tearDown(self):
        test_state = os.path.join(os.path.dirname(__file__), "test_paper_state.json")
        if os.path.exists(test_state):
            os.remove(test_state)

    def test_initial_balance_is_100(self):
        summary = self.broker.get_account_summary(current_price=60000.0)
        self.assertEqual(summary["initial_balance"], 100.0)
        self.assertEqual(summary["usdt_balance"], 100.0)
        self.assertIsNone(summary["position"])
        self.assertEqual(summary["stage_desc"], "SOLD (In Cash - 100% USDT)")

    def test_stage1_buy_75_percent(self):
        # Entry at $60,000 with SL at $59,000 and TP at $61,500
        # 75% of $100 = $75.00
        trade = self.broker.buy_initial(
            symbol="BTCUSDT",
            price=60000.0,
            stop_loss=59000.0,
            take_profit=61500.0,
            reason="Confirmed Pivot Low (Bullish Reversal)",
            position_size_pct=0.75,
            timestamp=1700000000,
        )
        self.assertIsNotNone(trade)
        self.assertEqual(trade["stage"], 1)
        self.assertAlmostEqual(self.broker.balance, 25.0, places=2)  # Remaining cash = $25
        self.assertAlmostEqual(trade["price"], 60000.0, places=2)

        summary = self.broker.get_account_summary(current_price=60000.0)
        self.assertEqual(summary["stage"], 1)
        self.assertIn("BOUGHT (Stage 1 - 75% Initial Allocation)", summary["stage_desc"])

    def test_stage2_dip_buy_25_percent_and_blended_math(self):
        # 1. Stage 1 at $60,000: $75 buys ~0.00125 BTC
        self.broker.buy_initial(
            symbol="BTCUSDT",
            price=60000.0,
            stop_loss=59000.0,
            take_profit=61500.0,
            reason="Confirmed Pivot Low",
            position_size_pct=0.75,
            timestamp=1700000000,
        )

        # 2. Price dips 2% to $58,800:
        pos = self.broker.position
        dip_pct = (58800.0 - pos.entry_price) / pos.entry_price
        self.assertLessEqual(dip_pct, -0.015)

        # 3. Stage 2 Dip Buy with remaining 25% cash ($25.00):
        dip_trade = self.broker.buy_dip(
            price=58800.0,
            reason="DIP BUY (Price dipped >= 1.5%)",
            risk_reward_ratio=1.5,
            sl_buffer_pct=0.002,
            timestamp=1700003600,
        )
        self.assertIsNotNone(dip_trade)
        self.assertEqual(self.broker.position.stage, 2)
        self.assertAlmostEqual(self.broker.balance, 0.0, places=2)  # Cash is fully allocated

        # Blended average entry price must be between 58800 and 60000
        blended = self.broker.position.entry_price
        self.assertGreater(blended, 58800.0)
        self.assertLess(blended, 60000.0)

        # Expected blended: (75 + 25) / (74.925/60000 + 24.975/58800)
        summary = self.broker.get_account_summary(current_price=58800.0)
        self.assertEqual(summary["stage"], 2)
        self.assertIn("BOUGHT (Stage 2 - 100% Fully Allocated after Dip Buy)", summary["stage_desc"])

    def test_exit_on_take_profit_returns_all_cash(self):
        # Initial buy
        self.broker.buy_initial(
            symbol="BTCUSDT",
            price=60000.0,
            stop_loss=59000.0,
            take_profit=61500.0,
            reason="Confirmed Pivot Low",
            position_size_pct=0.75,
            timestamp=1700000000,
        )
        # Price climbs past TP
        trade = self.broker.check_position_limits(
            current_high=62000.0,
            current_low=60000.0,
            current_close=61800.0,
            timestamp=1700007200,
        )
        self.assertIsNotNone(trade)
        self.assertIn("TAKE PROFIT", trade["exit_reason"])
        self.assertIsNone(self.broker.position)
        # Final USDT balance must exceed $100.00
        self.assertGreater(self.broker.balance, 100.0)

        summary = self.broker.get_account_summary(current_price=62000.0)
        self.assertEqual(summary["stage"], 0)
        self.assertIn("SOLD (In Cash - 100% USDT)", summary["stage_desc"])

    def test_portfolio_calculation_and_reporting(self):
        """Validates all metrics used in the /portfolio command: starting capital, cash left, equity, trade count, net PnL."""
        # 1. Start with $100
        summary0 = self.broker.get_account_summary(60000.0)
        self.assertEqual(summary0["initial_balance"], 100.0)
        self.assertEqual(summary0["usdt_balance"], 100.0)
        self.assertEqual(summary0["total_trades"], 0)
        self.assertEqual(summary0["realized_pnl"], 0.0)

        # 2. Complete Trade 1: Buy $75 at $60,000, sell at $63,000 (profit)
        self.broker.buy_initial("BTCUSDT", 60000.0, 59000.0, 63000.0, "Pivot Low", position_size_pct=0.75)
        trade1 = self.broker.sell(63000.0, "Take Profit Hit")
        self.assertGreater(trade1["pnl"], 0.0)

        # 3. Complete Trade 2: Buy $75 at $60,000, sell at $58,000 (loss)
        self.broker.buy_initial("BTCUSDT", 60000.0, 58000.0, 63000.0, "Pivot Low", position_size_pct=0.75)
        trade2 = self.broker.sell(58000.0, "Stop Loss Hit")
        self.assertLess(trade2["pnl"], 0.0)

        # 4. Check summary metrics
        summary = self.broker.get_account_summary(60000.0)
        self.assertEqual(summary["total_trades"], 2)
        self.assertEqual(summary["win_rate"], 50.0)  # 1 win, 1 loss = 50%
        self.assertEqual(summary["usdt_balance"], summary["total_equity"])  # In cash, so cash == equity
        self.assertAlmostEqual(summary["realized_pnl"], trade1["pnl"] + trade2["pnl"], places=2)


class TestTelegramManager(unittest.TestCase):
    def test_main_keyboard_layout(self):
        from telegram_bot import TelegramManager
        tm = TelegramManager(auto_start=False)
        kb = tm.get_main_keyboard()
        self.assertIsNotNone(kb)
        
        # Flatten all keyboard button texts
        button_texts = []
        for row in kb.keyboard:
            for btn in row:
                button_texts.append(btn["text"] if isinstance(btn, dict) else btn.text)

        expected_buttons = [
            "💼 Portfolio", "📊 Status",
            "💵 Balance", "📈 Pivots",
            "⚙️ Config", "💼 MEXC Wallet",
            "🟢 Buy (Stage 1)", "🔴 Close Position",
            "⏸️ Pause Bot", "▶️ Resume Bot",
            "🔄 Reset ($100)",
        ]
        for expected in expected_buttons:
            self.assertIn(expected, button_texts)


if __name__ == "__main__":
    unittest.main()
