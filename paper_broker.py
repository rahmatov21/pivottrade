"""
Spot Paper Broker for PivotTrader
==================================
Simulates a real spot trading exchange account (long-only, no leverage).
Supports $100 starting equity, 2-Stage DCA Position Sizing (75% Stage 1 + 25% Stage 2 Dip Buy),
blended average entry pricing, active TP/SL triggers, spot fees (0.1%),
and state persistence to disk (paper_state.json).
"""

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict, Any

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")
SPOT_FEE_RATE = 0.001  # 0.1% spot fee


@dataclass
class SpotPosition:
    symbol: str
    base_asset: str
    quantity: float
    entry_price: float  # Blended average entry price
    cost_usdt: float    # Total USDT invested
    stop_loss: float
    take_profit: float
    entry_time: int
    entry_reason: str
    stage: int = 1      # 1 = 75% Initial Entry, 2 = 25% Dip DCA added
    stage1_price: float = 0.0
    stage1_cost: float = 0.0
    stage1_qty: float = 0.0
    stage2_price: Optional[float] = None
    stage2_cost: Optional[float] = None
    stage2_qty: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PaperBroker:
    def __init__(self, initial_balance: float = 100.0, state_file: Optional[str] = None):
        self.state_file = state_file or STATE_FILE
        self.initial_balance = initial_balance
        self.usdt_balance = initial_balance
        self.position: Optional[SpotPosition] = None
        self.trade_history: List[Dict[str, Any]] = []

        self.load_state()

    @property
    def balance(self) -> float:
        return self.usdt_balance

    def get_base_asset(self, symbol: str) -> str:
        """Extracts base asset from symbol (e.g. BTC from BTCUSDT)."""
        if symbol.endswith("USDT"):
            return symbol[:-4]
        return symbol

    def buy_initial(
        self,
        symbol: str,
        price: float,
        stop_loss: float,
        take_profit: float,
        reason: str,
        position_size_pct: float = 0.75,
        timestamp: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Stage 1: Executes initial spot buy using 75% of available USDT cash.
        Leaves 25% cash reserved for dip buying.
        """
        if self.position is not None:
            return None  # Already in position

        total_portfolio = self.usdt_balance
        allocated_cash = round(total_portfolio * position_size_pct, 2)
        if allocated_cash < 5.0:  # Minimum order threshold
            return None

        fee = allocated_cash * SPOT_FEE_RATE
        net_cash = allocated_cash - fee
        qty = net_cash / price

        base_asset = self.get_base_asset(symbol)
        ts = timestamp or int(time.time())

        self.usdt_balance -= allocated_cash
        self.position = SpotPosition(
            symbol=symbol,
            base_asset=base_asset,
            quantity=round(qty, 6),
            entry_price=round(price, 4),
            cost_usdt=round(allocated_cash, 2),
            stop_loss=round(stop_loss, 4),
            take_profit=round(take_profit, 4),
            entry_time=ts,
            entry_reason=reason,
            stage=1,
            stage1_price=round(price, 4),
            stage1_cost=round(allocated_cash, 2),
            stage1_qty=round(qty, 6),
        )

        self.save_state()

        return {
            "action": "BUY_STAGE_1 (75%)",
            "symbol": symbol,
            "stage": 1,
            "quantity": self.position.quantity,
            "price": self.position.entry_price,
            "cost_usdt": self.position.cost_usdt,
            "fee_usdt": round(fee, 2),
            "stop_loss": self.position.stop_loss,
            "take_profit": self.position.take_profit,
            "remaining_usdt": round(self.usdt_balance, 2),
            "reason": reason,
            "timestamp": ts,
        }

    # Backward compatibility alias
    buy = buy_initial

    def buy_dip(
        self,
        price: float,
        reason: str,
        risk_reward_ratio: float = 1.5,
        sl_buffer_pct: float = 0.002,
        timestamp: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Stage 2: Deploys the remaining 25% cash on a price dip or secondary pivot low.
        Averages down entry price and recalculates Take Profit from lower average.
        """
        if self.position is None or self.position.stage != 1:
            return None  # Only callable if in Stage 1

        dip_cash = round(self.usdt_balance, 2)
        if dip_cash < 2.0:
            return None

        fee = dip_cash * SPOT_FEE_RATE
        net_cash = dip_cash - fee
        dip_qty = net_cash / price

        ts = timestamp or int(time.time())

        # Update position with blended average price
        prev_cost = self.position.cost_usdt
        prev_qty = self.position.quantity

        new_total_cost = round(prev_cost + dip_cash, 2)
        new_total_qty = round(prev_qty + dip_qty, 6)
        blended_avg_price = round(new_total_cost / new_total_qty, 4)

        # Update Stop Loss & Take Profit from the new lower blended average
        new_sl = round(min(self.position.stop_loss, price * (1.0 - sl_buffer_pct)), 4)
        risk = blended_avg_price - new_sl
        new_tp = round(blended_avg_price + (risk * risk_reward_ratio), 4)

        self.usdt_balance -= dip_cash

        self.position.quantity = new_total_qty
        self.position.entry_price = blended_avg_price
        self.position.cost_usdt = new_total_cost
        self.position.stop_loss = new_sl
        self.position.take_profit = new_tp
        self.position.stage = 2
        self.position.stage2_price = round(price, 4)
        self.position.stage2_cost = round(dip_cash, 2)
        self.position.stage2_qty = round(dip_qty, 6)

        self.save_state()

        return {
            "action": "BUY_STAGE_2_DIP (25%)",
            "symbol": self.position.symbol,
            "stage": 2,
            "dip_price": round(price, 4),
            "dip_quantity": round(dip_qty, 6),
            "dip_cost": round(dip_cash, 2),
            "blended_entry_price": self.position.entry_price,
            "total_quantity": self.position.quantity,
            "total_cost_usdt": self.position.cost_usdt,
            "new_take_profit": self.position.take_profit,
            "new_stop_loss": self.position.stop_loss,
            "remaining_usdt": round(self.usdt_balance, 2),
            "reason": reason,
            "timestamp": ts,
        }

    def sell(
        self,
        price: float,
        reason: str,
        timestamp: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Executes a spot sell order of the entire coin holding back to USDT.
        """
        if self.position is None:
            return None

        ts = timestamp or int(time.time())
        gross_proceeds = self.position.quantity * price
        fee = gross_proceeds * SPOT_FEE_RATE
        net_proceeds = gross_proceeds - fee

        realized_pnl = net_proceeds - self.position.cost_usdt
        realized_pnl_pct = (realized_pnl / self.position.cost_usdt) * 100.0

        self.usdt_balance += net_proceeds

        trade_record = {
            "id": f"TRADE-{len(self.trade_history) + 1}",
            "symbol": self.position.symbol,
            "stage": self.position.stage,
            "quantity": self.position.quantity,
            "entry_price": self.position.entry_price,
            "exit_price": round(price, 4),
            "cost_usdt": self.position.cost_usdt,
            "net_proceeds": round(net_proceeds, 2),
            "pnl": round(realized_pnl, 2),
            "pnl_pct": round(realized_pnl_pct, 2),
            "entry_reason": self.position.entry_reason,
            "exit_reason": reason,
            "entry_time": self.position.entry_time,
            "exit_time": ts,
            "hold_duration_sec": ts - self.position.entry_time,
        }

        self.trade_history.append(trade_record)
        self.position = None
        self.save_state()

        return trade_record

    def check_position_limits(
        self,
        current_high: float,
        current_low: float,
        current_close: float,
        timestamp: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Checks if the active spot position hit Stop Loss or Take Profit.
        """
        if self.position is None:
            return None

        # Check Stop Loss first
        if current_low <= self.position.stop_loss:
            return self.sell(
                price=self.position.stop_loss,
                reason=f"STOP LOSS triggered (target: ${self.position.stop_loss:.2f})",
                timestamp=timestamp,
            )

        # Check Take Profit
        if current_high >= self.position.take_profit:
            return self.sell(
                price=self.position.take_profit,
                reason=f"TAKE PROFIT triggered (target: ${self.position.take_profit:.2f})",
                timestamp=timestamp,
            )

        return None

    def get_equity(self, current_price: float) -> float:
        """Calculates total portfolio value in USDT."""
        if self.position is not None:
            return self.usdt_balance + (self.position.quantity * current_price)
        return self.usdt_balance

    def get_unrealized_pnl(self, current_price: float) -> Dict[str, float]:
        """Calculates unrealized PnL of open position."""
        if self.position is None:
            return {"pnl": 0.0, "pnl_pct": 0.0}
        current_val = self.position.quantity * current_price
        pnl = current_val - self.position.cost_usdt
        pnl_pct = (pnl / self.position.cost_usdt) * 100.0
        return {"pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2)}

    def get_account_summary(self, current_price: float = 0.0) -> Dict[str, Any]:
        """Returns high-level account status with DCA stage information."""
        equity = self.get_equity(current_price) if current_price > 0 else self.usdt_balance
        unrealized = self.get_unrealized_pnl(current_price) if current_price > 0 else {"pnl": 0.0, "pnl_pct": 0.0}

        winning = [t for t in self.trade_history if t.get("pnl", 0) > 0]
        losing = [t for t in self.trade_history if t.get("pnl", 0) < 0]
        total_pnl = sum(t.get("pnl", 0) for t in self.trade_history)

        win_rate = (len(winning) / len(self.trade_history) * 100.0) if self.trade_history else 0.0

        stage_desc = "SOLD (In Cash - 100% USDT)"
        if self.position:
            stage_desc = "BOUGHT (Stage 1 - 75% Initial Allocation)" if self.position.stage == 1 else "BOUGHT (Stage 2 - 100% Fully Allocated after Dip Buy)"

        return {
            "initial_balance": round(self.initial_balance, 2),
            "usdt_balance": round(self.usdt_balance, 2),
            "total_equity": round(equity, 2),
            "unrealized_pnl": unrealized["pnl"],
            "unrealized_pnl_pct": unrealized["pnl_pct"],
            "has_position": self.position is not None,
            "stage": self.position.stage if self.position else 0,
            "stage_desc": stage_desc,
            "position": self.position.to_dict() if self.position else None,
            "total_trades": len(self.trade_history),
            "win_rate": round(win_rate, 1),
            "realized_pnl": round(total_pnl, 2),
        }

    def save_state(self) -> None:
        """Persists broker state to disk."""
        state = {
            "usdt_balance": self.usdt_balance,
            "initial_balance": self.initial_balance,
            "position": self.position.to_dict() if self.position else None,
            "trade_history": self.trade_history,
        }
        try:
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            print(f"[BROKER ERROR] Failed to save {self.state_file}: {e}")

    def load_state(self) -> None:
        """Loads broker state from disk if available."""
        if not os.path.exists(self.state_file):
            return

        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            file_initial = float(data.get("initial_balance", self.initial_balance))
            file_usdt = float(data.get("usdt_balance", self.initial_balance))

            # Strictly sanitize legacy $10,000 values back to $100.00
            if file_initial > 500.0:
                file_initial = 100.0
            if file_usdt > 500.0:
                file_usdt = 100.0

            self.initial_balance = file_initial
            self.usdt_balance = file_usdt

            pos_data = data.get("position")
            if pos_data:
                self.position = SpotPosition(**pos_data)
            else:
                self.position = None
            self.trade_history = data.get("trade_history", [])
        except Exception as e:
            print(f"[BROKER ERROR] Failed to load {self.state_file}: {e}. Starting fresh.")
