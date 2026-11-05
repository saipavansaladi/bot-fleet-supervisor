"""
Bot Fleet Supervisor Application Runner
=======================================

Entrypoint script that instantiates the fake broker, registers a fleet of
bots, boots the supervisor watchdog, starts the web monitoring dashboard,
and feeds simulated market ticks.

Usage:
------
    python src/main.py --port 8080 --ticks 50
    python src/main.py --demo-watchdog   # Simulates a frozen bot to demonstrate watchdog recovery
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from fake_broker import FakeBroker
from supervisor import BotFleetSupervisor
from dashboard import start_dashboard_server
from strategies.dummy_scheduler_bot import DummySchedulerBot

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("main")


def run_fleet(
    ticks: int = 40,
    port: int = 8080,
    demo_watchdog: bool = False,
    demo_discrepancy: bool = False,
) -> None:
    """Run simulated trading fleet with supervisor and dashboard."""
    print("\n" + "=" * 70)
    print("           BOT FLEET SUPERVISOR: PRODUCTION LIFECYCLE RUNNER")
    print("=" * 70)

    # 1. Instantiate Fake Broker
    broker = FakeBroker(initial_cash=50_000.0)

    # 2. Optionally inject an out-of-band discrepancy to showcase startup reconciliation
    if demo_discrepancy:
        logger.info("[Demo] Injecting un-tracked broker position (10 shares of SYNTH_A)...")
        broker.inject_discrepancy("SYNTH_A", qty=10.0, price=150.0)

    # 3. Instantiate Supervisor
    supervisor = BotFleetSupervisor(
        broker=broker,
        heartbeat_timeout=3.0,
        max_restarts=3,
        kill_switch_file="KILL_SWITCH",
        check_interval=0.5,
    )

    # 4. Instantiate and Register Fleet Bots
    bot1 = DummySchedulerBot(
        bot_id="bot_alpha",
        name="Alpha Scheduled Buyer",
        symbol="SYNTH_A",
        broker=broker,
        buy_interval_ticks=4,
        order_qty=2.0,
    )
    bot2 = DummySchedulerBot(
        bot_id="bot_beta",
        name="Beta Scheduled Buyer",
        symbol="SYNTH_B",
        broker=broker,
        buy_interval_ticks=6,
        order_qty=1.0,
    )

    supervisor.register_bot(bot1)
    supervisor.register_bot(bot2)

    # 5. Start Dashboard Server
    try:
        start_dashboard_server(supervisor, host="127.0.0.1", port=port)
        print(f"[*] Live web dashboard: http://127.0.0.1:{port}")
    except OSError:
        print(f"[*] Port {port} in use; dashboard web server disabled for this run.")

    # 6. Start Supervisor & Bots
    supervisor.start()

    # 7. Feed Simulated Market Data Ticks
    print("[*] Feeding market data stream to fleet...")
    price_a = 150.0
    price_b = 250.0

    try:
        for t in range(1, ticks + 1):
            if supervisor.is_kill_switch_active():
                print(f"[!] Tick {t}: Global kill switch is ACTIVE. Bots halted.")
                time.sleep(1.0)
                continue

            # Simulate price random walk
            price_a = round(price_a * (1.0 + random.uniform(-0.005, 0.005)), 2)
            price_b = round(price_b * (1.0 + random.uniform(-0.005, 0.005)), 2)

            broker.simulate_tick("SYNTH_A", price_a)
            broker.simulate_tick("SYNTH_B", price_b)

            # Deliver ticks to bots
            bot1.process_tick({"timestamp": time.time(), "price": price_a})
            bot2.process_tick({"timestamp": time.time(), "price": price_b})

            # Watchdog demo: freeze bot2 at tick 12
            if demo_watchdog and t == 12:
                print("\n" + "!" * 70)
                print(" [FAULT INJECTION] Freezing bot_beta to trigger watchdog restart!")
                print("!" * 70)
                bot2.simulate_hang()

            time.sleep(0.5)

    except KeyboardInterrupt:
        print("\n[!] User interrupted session.")

    finally:
        print("\n[*] Shutting down supervisor and fleet...")
        supervisor.stop()
        print("[*] Done.\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run production bot supervisor demonstration with fake broker."
    )
    parser.add_argument("--ticks", type=int, default=30, help="Number of market ticks to run (default: 30)")
    parser.add_argument("--port", type=int, default=8080, help="Dashboard port (default: 8080)")
    parser.add_argument("--demo-watchdog", action="store_true", help="Simulate a frozen bot to demonstrate watchdog recovery")
    parser.add_argument("--demo-discrepancy", action="store_true", help="Inject discrepancy to demonstrate broker reconciliation")

    args = parser.parse_args()
    run_fleet(
        ticks=args.ticks,
        port=args.port,
        demo_watchdog=args.demo_watchdog,
        demo_discrepancy=args.demo_discrepancy,
    )


if __name__ == "__main__":
    main()

