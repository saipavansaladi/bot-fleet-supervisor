"""
Bot Fleet Supervisor & Watchdog Process
=======================================

The central control process responsible for managing the lifecycle, health,
and safety of all bots in the fleet.

Core Responsibilities:
----------------------
1. Heartbeat Tracking: Monitors health beacons emitted by each active bot.
2. Watchdog Recovery: Automatically restarts dead or frozen bots with backoff
   and a circuit breaker (max restart limit) to prevent cascading loops.
3. Global Kill Switch: Continuously checks for an emergency trigger (flag file
   or programmatic command) and instantly halts all bots and cancels all open
   orders within milliseconds.
4. Central Status Reporting: Aggregates fleet health, restart counts, and
   broker positions for logging and monitoring dashboards.
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
import time
from typing import Any, Dict, List, Optional
from bot_base import BaseBot, BotStatus
from fake_broker import FakeBroker

logger = logging.getLogger("supervisor")


class BotFleetSupervisor:
    """
    Production supervisor managing bot lifecycle, watchdog recovery,
    and emergency kill switch enforcement.
    """

    def __init__(
        self,
        broker: FakeBroker,
        heartbeat_timeout: float = 4.0,
        max_restarts: int = 3,
        kill_switch_file: str = "KILL_SWITCH",
        check_interval: float = 1.0,
    ) -> None:
        self.broker = broker
        self.heartbeat_timeout = heartbeat_timeout
        self.max_restarts = max_restarts
        self.kill_switch_path = Path(kill_switch_file)
        self.check_interval = check_interval

        self._lock = threading.RLock()
        self._bots: Dict[str, BaseBot] = {}
        self._restart_counts: Dict[str, int] = {}
        self._kill_switch_active: bool = False
        self._running: bool = False
        self._watchdog_thread: Optional[threading.Thread] = None

    # -------------------------------------------------------------------------
    # Bot Registration & Fleet Management
    # -------------------------------------------------------------------------
    def register_bot(self, bot: BaseBot) -> None:
        """Register a bot instance with the supervisor."""
        with self._lock:
            self._bots[bot.bot_id] = bot
            self._restart_counts[bot.bot_id] = 0
            logger.info(f"[Supervisor] Registered bot '{bot.bot_id}' ({bot.name}).")

    def deregister_bot(self, bot_id: str) -> None:
        """Remove a bot from management."""
        with self._lock:
            bot = self._bots.pop(bot_id, None)
            if bot:
                bot.stop(reason="deregistered")
                logger.info(f"[Supervisor] Deregistered bot '{bot_id}'.")

    def start(self) -> None:
        """Start the supervisor watchdog loop and launch all registered bots."""
        with self._lock:
            if self._running:
                return
            self._running = True

            logger.info("[Supervisor] Starting fleet supervisor...")

            # 1. Check for pre-existing kill switch flag
            if self.is_kill_switch_active():
                logger.warning("[Supervisor] Pre-existing KILL_SWITCH flag found! Fleet starting in HALTED state.")
                self._kill_switch_active = True
            else:
                # 2. Start all bots
                for bot in self._bots.values():
                    bot.start()

            # 3. Launch Watchdog Daemon Thread
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                daemon=True,
                name="SupervisorWatchdog",
            )
            self._watchdog_thread.start()
            logger.info("[Supervisor] Watchdog thread active.")

    def stop(self) -> None:
        """Gracefully shut down the supervisor and all managed bots."""
        with self._lock:
            self._running = False
            logger.info("[Supervisor] Stopping all bots in fleet...")
            for bot in self._bots.values():
                bot.stop(reason="supervisor_shutdown")
            logger.info("[Supervisor] Fleet shutdown complete.")

    # -------------------------------------------------------------------------
    # Kill Switch Mechanism
    # -------------------------------------------------------------------------
    def is_kill_switch_active(self) -> bool:
        """Check whether the kill switch is currently active via memory or file flag."""
        return self._kill_switch_active or self.kill_switch_path.exists()

    def activate_kill_switch(self, reason: str = "operator_emergency_stop") -> None:
        """
        Engage the global kill switch:
        1. Writes the physical KILL_SWITCH flag file.
        2. Sets in-memory kill switch flag.
        3. Calls halt() across all managed bots.
        4. Cancels all open orders at the broker.
        """
        with self._lock:
            logger.critical(f"[Supervisor] *** GLOBAL KILL SWITCH ACTIVATED *** (Reason: {reason})")
            self._kill_switch_active = True

            # Create physical file flag
            try:
                self.kill_switch_path.write_text(f"ACTIVATED: {reason} at {time.time()}\n")
            except Exception as e:
                logger.error(f"[Supervisor] Could not write kill switch file: {e}")

            # Emergency halt all bots
            for bot in self._bots.values():
                bot.halt(reason=reason)

            # Emergency cancel all orders across the broker
            cancelled = self.broker.cancel_all_orders()
            logger.critical(f"[Supervisor] All trading halted. Cancelled {cancelled} open broker order(s).")

    def deactivate_kill_switch(self) -> None:
        """
        Disengage the global kill switch:
        Removes physical flag file and allows bots to be restarted by operator.
        """
        with self._lock:
            logger.info("[Supervisor] Disengaging global kill switch...")
            self._kill_switch_active = False
            if self.kill_switch_path.exists():
                try:
                    self.kill_switch_path.unlink()
                except Exception as e:
                    logger.error(f"[Supervisor] Could not remove kill switch file: {e}")

            logger.info("[Supervisor] Kill switch cleared. Bots may now be manually restarted.")

    # -------------------------------------------------------------------------
    # Watchdog Health Monitor
    # -------------------------------------------------------------------------
    def _watchdog_loop(self) -> None:
        """Internal background loop checking heartbeats and kill switches."""
        while self._running:
            try:
                self._check_fleet_health()
            except Exception as e:
                logger.error(f"[Supervisor] Error in watchdog loop: {e}", exc_info=True)

            time.sleep(self.check_interval)

    def _check_fleet_health(self) -> None:
        with self._lock:
            # 1. Kill Switch Polling
            if self.is_kill_switch_active():
                if not self._kill_switch_active:
                    # File appeared externally (e.g., via touch KILL_SWITCH in terminal)
                    self.activate_kill_switch(reason="external_flag_file_detected")
                return  # Do not attempt to restart bots while kill switch is active

            now = time.time()

            # 2. Heartbeat & Liveness Check for Each Bot
            for bot_id, bot in list(self._bots.items()):
                if bot.status not in (BotStatus.RUNNING, BotStatus.INITIALIZING, BotStatus.RECONCILING):
                    continue

                heartbeat_age = now - bot.last_heartbeat
                if heartbeat_age > self.heartbeat_timeout:
                    logger.warning(
                        f"[Supervisor] Bot '{bot_id}' MISSED HEARTBEAT! "
                        f"Age: {heartbeat_age:.1f}s (Threshold: {self.heartbeat_timeout:.1f}s)."
                    )
                    self._handle_dead_bot(bot)

    def _handle_dead_bot(self, bot: BaseBot) -> None:
        """Handle a dead or unresponsive bot with restart backoff & circuit breaker."""
        bot_id = bot.bot_id
        restarts = self._restart_counts.get(bot_id, 0)

        if restarts >= self.max_restarts:
            logger.critical(
                f"[Supervisor] Bot '{bot_id}' exceeded max restarts ({self.max_restarts}). "
                f"CIRCUIT BREAKER TRIPPED. Bot permanently HALTED."
            )
            bot.status = BotStatus.ERROR
            return

        self._restart_counts[bot_id] = restarts + 1
        logger.warning(
            f"[Supervisor] Attempting recovery for bot '{bot_id}' "
            f"(Attempt {self._restart_counts[bot_id]}/{self.max_restarts})..."
        )

        # Force stop old instance cleanly
        try:
            bot.stop(reason="watchdog_restart")
        except Exception:
            pass

        # Re-initialize and reconcile with broker
        try:
            bot.start()
            logger.info(f"[Supervisor] Successfully recovered bot '{bot_id}'.")
        except Exception as e:
            logger.error(f"[Supervisor] Failed to restart bot '{bot_id}': {e}")
            bot.status = BotStatus.ERROR

    # -------------------------------------------------------------------------
    # Status Aggregation
    # -------------------------------------------------------------------------
    def get_fleet_status(self) -> Dict[str, Any]:
        """Generate a complete telemetry snapshot of all bots and broker state."""
        with self._lock:
            bot_summaries = []
            for b in self._bots.values():
                summary = b.get_status_summary()
                summary["restarts"] = self._restart_counts.get(b.bot_id, 0)
                bot_summaries.append(summary)

            account = self.broker.get_account()
            positions = {
                sym: {
                    "qty": p.qty,
                    "avg_price": round(p.avg_entry_price, 2),
                    "current_price": round(p.current_price, 2),
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                }
                for sym, p in self.broker.get_positions().items()
            }

            return {
                "timestamp": round(time.time(), 2),
                "supervisor_running": self._running,
                "kill_switch_active": self.is_kill_switch_active(),
                "total_bots": len(self._bots),
                "active_bots": sum(1 for b in self._bots.values() if b.status == BotStatus.RUNNING),
                "halted_bots": sum(1 for b in self._bots.values() if b.status == BotStatus.HALTED),
                "error_bots": sum(1 for b in self._bots.values() if b.status == BotStatus.ERROR),
                "account": {
                    "cash": account.cash,
                    "portfolio_value": account.portfolio_value,
                },
                "broker_positions": positions,
                "bots": bot_summaries,
            }

