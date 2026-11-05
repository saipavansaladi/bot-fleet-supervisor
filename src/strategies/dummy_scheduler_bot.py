"""
Dummy Scheduled Buyer Bot (Architectural Example)
=================================================

A trivial placeholder trading bot used strictly to illustrate the bot lifecycle,
supervisor watchdog recovery, and broker reconciliation.

DISCLAIMER:
-----------
THIS IS A DUMMY TOY STRATEGY. IT CONTAINS NO REAL TRADING ALPHA OR STRATEGY LOGIC.
IT BUYS A FIXED 1.0 UNIT ON A SCHEDULE FOR ARCHITECTURAL TESTING PURPOSES ONLY.

Features:
---------
- Executes a simulated buy order every `buy_interval_ticks` ticks.
- Features `simulate_hang()` and `simulate_crash()` methods to test the
  supervisor's automated dead-bot detection and restart mechanisms.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict
from bot_base import BaseBot
from fake_broker import FakeBroker

logger = logging.getLogger("dummy_bot")


class DummySchedulerBot(BaseBot):
    """
    Toy bot that submits simulated orders on a periodic tick interval.
    """

    def __init__(
        self,
        bot_id: str,
        name: str,
        symbol: str,
        broker: FakeBroker,
        buy_interval_ticks: int = 5,
        order_qty: float = 1.0,
    ) -> None:
        super().__init__(
            bot_id=bot_id,
            name=name,
            symbol=symbol,
            broker=broker,
            heartbeat_interval=1.0,
        )
        self.buy_interval_ticks = buy_interval_ticks
        self.order_qty = order_qty
        self._simulate_hang = False

    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        Placeholder strategy logic:
        Every N ticks, places a small simulated buy order.
        """
        if self._simulate_hang:
            # Emulates an infinite loop or frozen thread (watchdog will catch this)
            time.sleep(30.0)
            return

        price = float(tick_data.get("price", 100.0))

        # Check schedule trigger
        if self.total_ticks_processed % self.buy_interval_ticks == 0:
            logger.info(
                f"[{self.bot_id}] Scheduled interval reached (Tick #{self.total_ticks_processed}). "
                f"Submitting buy order for {self.order_qty} {self.symbol} @ ${price:.2f}..."
            )
            try:
                order = self.broker.place_order(
                    bot_id=self.bot_id,
                    symbol=self.symbol,
                    side="BUY",
                    qty=self.order_qty,
                    price=price,
                )
                self.local_positions[self.symbol] = self.local_positions.get(self.symbol, 0.0) + self.order_qty
                logger.info(f"[{self.bot_id}] Order {order.order_id} filled. Position: {self.local_positions[self.symbol]}")
            except RuntimeError as e:
                logger.warning(f"[{self.bot_id}] Order skipped: {e}")

    # -------------------------------------------------------------------------
    # Fault Injection Helpers (For Supervisor Testing)
    # -------------------------------------------------------------------------
    def simulate_hang(self) -> None:
        """Intentionally stops emitting heartbeats to trigger watchdog restart."""
        logger.warning(f"[{self.bot_id}] FAULT INJECTED: Bot hanging (halting heartbeats)...")
        self._simulate_hang = True

    def simulate_crash(self) -> None:
        """Intentionally crashes the bot into an ERROR state."""
        logger.warning(f"[{self.bot_id}] FAULT INJECTED: Simulating unhandled exception...")
        raise RuntimeError("Injected fault: Simulated process failure!")
