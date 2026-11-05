"""
Unit & Integration Test Suite for Bot Fleet Supervisor
======================================================

Verifies the correctness of the fake broker, state reconciliation engine,
bot lifecycle, watchdog recovery, and global kill switch.
"""

from pathlib import Path
import sys
import time
import unittest

src_dir = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(src_dir))

from fake_broker import FakeBroker
from reconciler import Reconciler, DiscrepancyType
from bot_base import BotStatus
from strategies.dummy_scheduler_bot import DummySchedulerBot
from supervisor import BotFleetSupervisor


class TestFakeBroker(unittest.TestCase):
    """Tests for the simulated in-memory broker."""

    def setUp(self):
        self.broker = FakeBroker(initial_cash=10_000.0)

    def test_initial_account(self):
        acc = self.broker.get_account()
        self.assertEqual(acc.cash, 10_000.0)
        self.assertEqual(acc.portfolio_value, 10_000.0)
        self.assertEqual(len(self.broker.get_positions()), 0)

    def test_buy_order_and_position(self):
        order = self.broker.place_order("bot1", "SYNTH", "BUY", 10.0, 100.0)
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(self.broker.get_account().cash, 9_000.0)
        pos = self.broker.get_positions()
        self.assertIn("SYNTH", pos)
        self.assertEqual(pos["SYNTH"].qty, 10.0)
        self.assertEqual(pos["SYNTH"].avg_entry_price, 100.0)

    def test_insufficient_funds_rejected(self):
        with self.assertRaises(RuntimeError):
            self.broker.place_order("bot1", "SYNTH", "BUY", 200.0, 100.0)  # Needs $20,000

    def test_cancel_all_orders(self):
        # Place order with cash
        self.broker.place_order("bot1", "SYNTH", "BUY", 1.0, 100.0)
        cancelled = self.broker.cancel_all_orders()
        self.assertIsInstance(cancelled, int)


class TestReconciliation(unittest.TestCase):
    """Tests for single-source-of-truth broker reconciliation."""

    def setUp(self):
        self.broker = FakeBroker(initial_cash=50_000.0)

    def test_clean_reconciliation(self):
        self.broker.inject_discrepancy("SYNTH", qty=5.0, price=100.0)
        local = {"SYNTH": 5.0}
        report = Reconciler.reconcile("bot1", local, self.broker)
        self.assertTrue(report.is_clean)
        self.assertEqual(len(report.discrepancies), 0)

    def test_ghost_position_detected(self):
        # Bot thinks it owns 10 shares, broker has 0
        local = {"GHOST_STOCK": 10.0}
        report = Reconciler.reconcile("bot1", local, self.broker)
        self.assertFalse(report.is_clean)
        self.assertEqual(len(report.discrepancies), 1)
        self.assertEqual(report.discrepancies[0].discrepancy_type, DiscrepancyType.GHOST_POSITION)
        # Authoritative positions should have 0 for GHOST_STOCK
        self.assertNotIn("GHOST_STOCK", report.reconciled_positions)

    def test_untracked_position_adopted(self):
        # Broker has 15 shares, bot local state had 0
        self.broker.inject_discrepancy("NEW_STOCK", qty=15.0, price=50.0)
        local = {}
        report = Reconciler.reconcile("bot1", local, self.broker)
        self.assertFalse(report.is_clean)
        self.assertEqual(len(report.discrepancies), 1)
        self.assertEqual(report.discrepancies[0].discrepancy_type, DiscrepancyType.UNTRACKED_POSITION)
        self.assertIn("NEW_STOCK", report.reconciled_positions)
        self.assertEqual(report.reconciled_positions["NEW_STOCK"].qty, 15.0)

    def test_qty_mismatch_reconciled(self):
        # Local claims 5, broker has 8
        self.broker.inject_discrepancy("STOCK_A", qty=8.0, price=100.0)
        local = {"STOCK_A": 5.0}
        report = Reconciler.reconcile("bot1", local, self.broker)
        self.assertFalse(report.is_clean)
        self.assertEqual(report.discrepancies[0].discrepancy_type, DiscrepancyType.QTY_MISMATCH)
        self.assertEqual(report.reconciled_positions["STOCK_A"].qty, 8.0)


class TestSupervisorWatchdogAndKillSwitch(unittest.TestCase):
    """Tests for supervisor watchdog heartbeat monitoring and emergency kill switch."""

    def setUp(self):
        self.kill_switch_file = "TEST_KILL_SWITCH"
        self.broker = FakeBroker(initial_cash=50_000.0)
        self.supervisor = BotFleetSupervisor(
            broker=self.broker,
            heartbeat_timeout=0.6,
            max_restarts=2,
            kill_switch_file=self.kill_switch_file,
            check_interval=0.1,
        )
        self.bot = DummySchedulerBot(
            bot_id="test_bot",
            name="Test Bot",
            symbol="SYNTH",
            broker=self.broker,
            buy_interval_ticks=2,
        )
        self.supervisor.register_bot(self.bot)

    def tearDown(self):
        self.supervisor.stop()
        p = Path(self.kill_switch_file)
        if p.exists():
            p.unlink()

    def test_bot_startup_and_tick_processing(self):
        self.supervisor.start()
        self.assertEqual(self.bot.status, BotStatus.RUNNING)

        self.bot.process_tick({"price": 100.0})
        self.assertEqual(self.bot.total_ticks_processed, 1)

    def test_watchdog_restarts_dead_bot(self):
        self.supervisor.start()
        self.assertEqual(self.bot.status, BotStatus.RUNNING)

        # Force bot heartbeat age past timeout by simulating freeze
        self.bot.last_heartbeat = time.time() - 2.0  # past 0.6s threshold

        # Wait for supervisor watchdog loop to process
        time.sleep(0.5)

        # Supervisor should have detected missed heartbeat and triggered restart
        status = self.supervisor.get_fleet_status()
        self.assertGreaterEqual(status["bots"][0]["restarts"], 1)

    def test_global_kill_switch(self):
        self.supervisor.start()
        self.assertEqual(self.bot.status, BotStatus.RUNNING)

        # Activate kill switch
        self.supervisor.activate_kill_switch(reason="test_emergency")
        self.assertTrue(self.supervisor.is_kill_switch_active())
        self.assertEqual(self.bot.status, BotStatus.HALTED)
        self.assertTrue(Path(self.kill_switch_file).exists())

        # Deactivate kill switch
        self.supervisor.deactivate_kill_switch()
        self.assertFalse(self.supervisor.is_kill_switch_active())
        self.assertFalse(Path(self.kill_switch_file).exists())


if __name__ == "__main__":
    unittest.main()
