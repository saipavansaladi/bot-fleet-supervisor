"""
In-Memory Simulated Broker Adapter
==================================

A thread-safe, simulated broker providing order management, position tracking,
and account balance calculations for testing and architectural demonstration.

Key Features:
-------------
- In-memory execution: Zero external API dependencies or credentials.
- Thread-safe: Protected by internal re-entrant locks (`threading.RLock`).
- Simulated fills: Instantaneous market order fills and price updating.
- Fault & Discrepancy Injection: Allows external state mutation to verify
  that bot reconciliation modules correctly detect and recover from drift.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import threading
import time
from typing import Dict, List, Optional
import uuid


@dataclass
class Position:
    """Represents an open asset position reported by the broker."""
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float

    @property
    def market_value(self) -> float:
        return self.qty * self.current_price

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_price - self.avg_entry_price) * self.qty


@dataclass
class Order:
    """Represents an order tracked by the broker."""
    order_id: str
    bot_id: str
    symbol: str
    side: str  # 'BUY' or 'SELL'
    qty: float
    price: float
    status: str  # 'OPEN', 'FILLED', 'CANCELLED'
    created_at: float = field(default_factory=time.time)


@dataclass
class Account:
    """Represents broker account balance and equity."""
    cash: float
    portfolio_value: float


class FakeBroker:
    """
    Simulated in-memory broker adapter.
    """

    def __init__(self, initial_cash: float = 100_000.0) -> None:
        self._lock = threading.RLock()
        self._cash = float(initial_cash)
        self._positions: Dict[str, Position] = {}
        self._orders: Dict[str, Order] = {}

    def get_account(self) -> Account:
        """Fetch current cash balance and total portfolio valuation."""
        with self._lock:
            pos_val = sum(p.market_value for p in self._positions.values())
            return Account(
                cash=round(self._cash, 2),
                portfolio_value=round(self._cash + pos_val, 2),
            )

    def get_positions(self) -> Dict[str, Position]:
        """Fetch all currently open positions from the broker."""
        with self._lock:
            # Return shallow copies to prevent external mutation
            return {
                sym: Position(
                    symbol=pos.symbol,
                    qty=pos.qty,
                    avg_entry_price=pos.avg_entry_price,
                    current_price=pos.current_price,
                )
                for sym, pos in self._positions.items()
            }

    def get_open_orders(self, bot_id: Optional[str] = None) -> List[Order]:
        """Fetch open orders, optionally filtered by bot_id."""
        with self._lock:
            orders = [o for o in self._orders.values() if o.status == "OPEN"]
            if bot_id:
                orders = [o for o in orders if o.bot_id == bot_id]
            return orders

    def place_order(
        self,
        bot_id: str,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> Order:
        """
        Place an order with simulated execution.

        Market orders fill immediately if sufficient cash/shares are available.
        """
        side = side.upper()
        if side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side '{side}'. Must be 'BUY' or 'SELL'.")
        if qty <= 0:
            raise ValueError("Order quantity must be positive.")

        with self._lock:
            order_id = f"ord_{uuid.uuid4().hex[:8]}"
            order = Order(
                order_id=order_id,
                bot_id=bot_id,
                symbol=symbol,
                side=side,
                qty=qty,
                price=price,
                status="OPEN",
            )

            # Simulated Instant Fill
            cost = qty * price
            if side == "BUY":
                if self._cash < cost:
                    order.status = "CANCELLED"
                    self._orders[order_id] = order
                    raise RuntimeError(
                        f"Order rejected: Insufficient cash (${self._cash:.2f} < ${cost:.2f})"
                    )

                self._cash -= cost
                if symbol in self._positions:
                    current_pos = self._positions[symbol]
                    new_qty = current_pos.qty + qty
                    new_avg = ((current_pos.qty * current_pos.avg_entry_price) + cost) / new_qty
                    current_pos.qty = new_qty
                    current_pos.avg_entry_price = new_avg
                    current_pos.current_price = price
                else:
                    self._positions[symbol] = Position(
                        symbol=symbol,
                        qty=qty,
                        avg_entry_price=price,
                        current_price=price,
                    )
                order.status = "FILLED"

            elif side == "SELL":
                current_pos = self._positions.get(symbol)
                if not current_pos or current_pos.qty < qty:
                    order.status = "CANCELLED"
                    self._orders[order_id] = order
                    raise RuntimeError(
                        f"Order rejected: Insufficient position in {symbol}"
                    )

                proceeds = qty * price
                self._cash += proceeds
                current_pos.qty -= qty
                current_pos.current_price = price
                if current_pos.qty <= 0.0001:
                    del self._positions[symbol]
                order.status = "FILLED"

            self._orders[order_id] = order
            return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        with self._lock:
            order = self._orders.get(order_id)
            if order and order.status == "OPEN":
                order.status = "CANCELLED"
                return True
            return False

    def cancel_all_orders(self, bot_id: Optional[str] = None) -> int:
        """Emergency action: cancel all open orders."""
        with self._lock:
            cancelled_count = 0
            for order in self._orders.values():
                if order.status == "OPEN":
                    if bot_id is None or order.bot_id == bot_id:
                        order.status = "CANCELLED"
                        cancelled_count += 1
            return cancelled_count

    def simulate_tick(self, symbol: str, price: float) -> None:
        """Update market price for an asset and revalue open positions."""
        with self._lock:
            if symbol in self._positions:
                self._positions[symbol].current_price = price

    def inject_discrepancy(
        self,
        symbol: str,
        qty: float,
        price: float = 100.0,
    ) -> None:
        """
        Testing utility: Mutate broker positions out-of-band to simulate
        an external execution (e.g., human intervention, exchange liquidation)
        and test whether the bot's reconciliation logic detects the drift.
        """
        with self._lock:
            if qty <= 0:
                self._positions.pop(symbol, None)
            else:
                self._positions[symbol] = Position(
                    symbol=symbol,
                    qty=qty,
                    avg_entry_price=price,
                    current_price=price,
                )

