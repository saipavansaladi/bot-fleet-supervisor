"""
Bot Template & Lifecycle Interface
==================================

Defines the abstract base class and standardized lifecycle protocol for all
bots in the fleet.

Any trading strategy or worker bot must subclass `BaseBot` and implement:
- `on_tick(tick_data)`: Strategy execution handler called on market data updates.
- `on_start()`: Optional strategy initialization hook.
- `on_stop()`: Optional graceful teardown hook.

Lifecycle States:
-----------------
INITIALIZING -> RECONCILING -> RUNNING -> STOPPED / HALTED / ERROR
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
import logging
import time
from typing import Any, Dict, Optional
from fake_broker import FakeBroker, Position
from reconciler import Reconciler, ReconciliationReport

logger = logging.getLogger("bot_base")


class BotStatus(str, Enum):
    """Lifecycle states of an individual bot."""
    INITIALIZING = "INITIALIZING"
    RECONCILING = "RECONCILING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    HALTED = "HALTED"        # Emergency halted via global kill switch
    ERROR = "ERROR"


class BaseBot(ABC):
    """
    Standard interface for all autonomous trading bots managed by the supervisor.
    """

    def __init__(
        self,
        bot_id: str,
        name: str,
        symbol: str,
        broker: FakeBroker,
        heartbeat_interval: float = 2.0,
    ) -> None:
        self.bot_id = bot_id
        self.name = name
        self.symbol = symbol
        self.broker = broker
        self.heartbeat_interval = heartbeat_interval

        self.status = BotStatus.INITIALIZING
        self.last_heartbeat: float = time.time()
        self.local_positions: Dict[str, float] = {}
        self.total_ticks_processed: int = 0
        self.last_reconciliation_report: Optional[ReconciliationReport] = None
        self._is_active: bool = False

    def start(self) -> None:
        """
        Start the bot lifecycle:
        1. Transitions to RECONCILING and syncs with broker ground-truth.
        2. Calls on_start() strategy hook.
        3. Transitions to RUNNING and emits initial heartbeat.
        """
        logger.info(f"[{self.bot_id}] Starting bot '{self.name}' on {self.symbol}...")
        self.status = BotStatus.RECONCILING

        # 1. State Reconciliation with Broker
        self.reconcile()

        # 2. Strategy Hook
        self.on_start()

        # 3. Transition to Running
        self._is_active = True
        self.status = BotStatus.RUNNING
        self.heartbeat()
        logger.info(f"[{self.bot_id}] Bot '{self.name}' is now RUNNING.")

    def stop(self, reason: str = "normal_shutdown") -> None:
        """Gracefully stop the bot, cancel pending orders, and mark STOPPED."""
        logger.info(f"[{self.bot_id}] Stopping bot (Reason: {reason})...")
        self._is_active = False
        self.status = BotStatus.STOPPED
        try:
            self.broker.cancel_all_orders(bot_id=self.bot_id)
            self.on_stop()
        except Exception as e:
            logger.error(f"[{self.bot_id}] Error during shutdown: {e}")
        logger.info(f"[{self.bot_id}] Bot stopped.")

    def halt(self, reason: str = "kill_switch_activated") -> None:
        """
        Emergency Halt: Immediately stop trading, cancel orders, transition to HALTED.
        Triggered by the supervisor when a global kill switch is engaged.
        """
        logger.warning(f"[{self.bot_id}] EMERGENCY HALT triggered (Reason: {reason})!")
        self._is_active = False
        self.status = BotStatus.HALTED
        try:
            cancelled = self.broker.cancel_all_orders(bot_id=self.bot_id)
            logger.warning(f"[{self.bot_id}] Emergency cancelled {cancelled} open order(s).")
            self.on_stop()
        except Exception as e:
            logger.error(f"[{self.bot_id}] Error during emergency halt: {e}")

    def heartbeat(self) -> None:
        """Update last heartbeat timestamp for supervisor watchdog monitoring."""
        self.last_heartbeat = time.time()

    def reconcile(self) -> ReconciliationReport:
        """
        Reconcile local position cache against broker positions.
        Adopts broker ground-truth as canonical reality.
        """
        report = Reconciler.reconcile(
            bot_id=self.bot_id,
            local_positions=self.local_positions,
            broker=self.broker,
        )
        self.last_reconciliation_report = report

        # Update local position cache to match broker
        self.local_positions = {
            sym: pos.qty for sym, pos in report.reconciled_positions.items()
        }
        return report

    def process_tick(self, tick_data: Dict[str, Any]) -> None:
        """
        External tick delivery wrapper.
        Validates bot state, refreshes heartbeat, and calls on_tick().
        """
        if self.status != BotStatus.RUNNING or not self._is_active:
            return

        self.heartbeat()
        self.total_ticks_processed += 1
        try:
            self.on_tick(tick_data)
        except Exception as e:
            logger.error(f"[{self.bot_id}] Error in strategy on_tick(): {e}", exc_info=True)
            self.status = BotStatus.ERROR

    def get_status_summary(self) -> Dict[str, Any]:
        """Return structured dictionary representing current bot health and state."""
        return {
            "bot_id": self.bot_id,
            "name": self.name,
            "symbol": self.symbol,
            "status": self.status.value,
            "is_active": self._is_active,
            "last_heartbeat": round(self.last_heartbeat, 2),
            "heartbeat_age_sec": round(time.time() - self.last_heartbeat, 2),
            "ticks_processed": self.total_ticks_processed,
            "local_positions": self.local_positions.copy(),
            "reconciliation_clean": (
                self.last_reconciliation_report.is_clean
                if self.last_reconciliation_report
                else True
            ),
        }

    # -------------------------------------------------------------------------
    # Abstract & Optional Hooks for Bot Implementations
    # -------------------------------------------------------------------------
    @abstractmethod
    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        """Strategy logic executed on each incoming price tick."""
        pass

    def on_start(self) -> None:
        """Hook called when bot is started, after state reconciliation."""
        pass

    def on_stop(self) -> None:
        """Hook called when bot is stopped or halted."""
        pass

