# Bot Fleet Supervisor

A production-grade lifecycle supervisor, watchdog process, state reconciliation engine, and emergency kill switch for autonomous trading bots.

---

> [!IMPORTANT]
> **DISCLAIMER & PURPOSE:**  
> This repository is a **software architecture blueprint** designed for systems engineers, quantitative developers, and platform teams. It contains **100% synthetic in-memory infrastructure, zero real broker endpoints, no credentials, and no real strategy alpha**. All trading logic is encapsulated in a trivial dummy scheduler clearly marked as a placeholder.

---

## 1. Why Trading Bots Need Supervision (A Systems Engineering Perspective)

In conventional web development, a failed microservice returns an HTTP 500, a load balancer retries, and the database rolls back the transaction. 

In automated trading systems, **failures carry irreversible real-world financial side effects**:
1. **Irreversible State Transitions:** You cannot `ROLLBACK` an exchange-executed fill. If a bot places duplicate orders due to a retry loop, real capital is depleted.
2. **Zombie Bots & Silent Freezes:** An unhandled exception or hung socket doesn't just crash a process—it leaves active orders open in the market without stop-loss management or position monitoring.
3. **State Drift:** If a human trader modifies a position on mobile, or an exchange partially fills an order while the bot is restarting, the bot's internal memory becomes a hallucination.

To operate bots safely in production, you must treat them like mission-critical distributed workers with **active watchdog supervision, single-source-of-truth reconciliation, and fail-safe circuit breakers**.

---

## 2. Architecture Overview

```
                        +---------------------------------------------+
                        |          Operator / Monitoring View         |
                        |      (Web Dashboard / REST API :8080)       |
                        +----------------------+----------------------+
                                               |
                                     GET /api/status | POST /api/kill
                                               v
+-----------------------------------------------------------------------------------------+
|                                 BOT FLEET SUPERVISOR                                    |
|                                                                                         |
|  +--------------------+        +-----------------------+        +--------------------+  |
|  | Watchdog Monitor   |        | Global Kill Switch    |        | State Reconciler   |  |
|  | - Heartbeat age    |        | - Flag file check     |        | - Broker = Truth   |  |
|  | - Auto-restart     |        | - Emergency halt      |        | - Ghost detection  |  |
|  | - Circuit breaker  |        | - Mass order cancel   |        | - Diff audit log   |  |
|  +---------+----------+        +-----------+-----------+        +---------+----------+  |
|            |                               |                              |             |
+------------|-------------------------------|------------------------------|-------------+
             | Heartbeat beacons             | Emergency halt commands      | Position sync
             v                               v                              v
+-------------------------+      +-------------------------+      +-----------------------+
|  Bot Alpha (BaseBot)    |      |  Bot Beta (BaseBot)     |      |  Fake Broker Adapter  |
|  - Lifecycle: RUNNING   |      |  - Lifecycle: RUNNING   |      |  - In-memory balances |
|  - Ticks: on_tick()     |      |  - Ticks: on_tick()     | <--> |  - Simulated fills    |
|  - Strategy: Scheduled  |      |  - Strategy: Scheduled  |      |  - Thread-safe RLock  |
+-------------------------+      +-------------------------+      +-----------------------+
```

---

## 3. The Four Production Pillars

### Pillar 1: Single Source of Truth (Broker Reconciliation)
> *"The broker's database is physical reality; local memory or disk cache is merely an untrusted draft."*

When a bot starts or reconnects, it **never assumes its local state is valid**. The `Reconciler` engine queries the broker and classifies state drift into three distinct audit categories:
- **`GHOST_POSITION`:** Local memory claims the bot owns shares, but the broker reports 0 (e.g., an external risk desk closed the position).
- **`UNTRACKED_POSITION`:** The broker shows open positions that local state has no record of (e.g., a crash occurred between execution and disk write).
- **`QTY_MISMATCH`:** Both agree a position exists, but quantities disagree (e.g., partial fills or unhandled corporate splits).

The reconciler logs the exact delta and **forces the bot's local state to converge with the broker**.

### Pillar 2: Active Watchdog & Heartbeat Beacons
Bots inherit from `BaseBot` and emit heartbeats on every market tick. The supervisor runs a background watchdog thread checking:
$$\text{Heartbeat Age} = t_{\text{current}} - t_{\text{last\_heartbeat}}$$
- If $\text{Heartbeat Age} > \text{Timeout}$, the bot is flagged as `DEAD`.
- The supervisor forces a clean teardown, re-runs broker reconciliation, and boots a fresh instance.
- **Circuit Breaker:** If a bot restarts more than `max_restarts` (e.g., 3 times), the supervisor halts it permanently with an alert, preventing infinite restart loops.

### Pillar 3: Global Emergency Kill Switch
When an unexpected market anomaly, exchange latency spike, or logic bug occurs, humans need a zero-latency kill mechanism.
- Triggered either via REST (`POST /api/kill`) or by touching a flag file (`touch KILL_SWITCH`).
- Within milliseconds, the supervisor:
  1. Broadcasts an immediate `halt()` to every bot in the fleet.
  2. Issues a batch order cancellation (`broker.cancel_all_orders()`).
  3. Transitions all fleet states to `HALTED`.

### Pillar 4: Zero-Dependency Observability
Built strictly using Python's standard `http.server` (zero external dependencies like Flask or FastAPI). Serves a live, dark-mode control room showing active bots, heartbeat latencies, restart counters, and live kill switch controls.

---

## 4. Repository Structure

```
bot-fleet-supervisor/
├── requirements.txt            # Standard library only (pytest optional)
├── .gitignore                  # Ignores temp files, kill flags, and virtualenvs
├── README.md                   # Systems engineering architectural documentation
├── src/
│   ├── __init__.py
│   ├── fake_broker.py          # In-memory thread-safe broker adapter & fill engine
│   ├── reconciler.py           # Single-source-of-truth state reconciliation engine
│   ├── bot_base.py             # Abstract BaseBot lifecycle & self-registration interface
│   ├── supervisor.py           # Watchdog monitor, heartbeat tracker, and kill switch coordinator
│   ├── dashboard.py            # Zero-dependency HTTP monitoring server & web UI
│   ├── main.py                 # Runnable production simulation script
│   └── strategies/
│       ├── __init__.py
│       └── dummy_scheduler_bot.py # Trivial scheduled buyer placeholder bot
└── tests/
    ├── __init__.py
    └── test_supervisor.py      # Automated test suite (11 unit & integration tests)
```

---

## 5. Quickstart & How to Run

### 1. Requirements
This project uses **100% Python Standard Library** (`threading`, `http.server`, `dataclasses`, `time`, `json`, `unittest`). No external packages are required to run the service.

### 2. Run the Simulated Fleet
Run the complete fleet with live dashboard and 40 simulated market ticks:
```bash
python src/main.py --port 8080 --ticks 40
```
Open your browser to: **[http://127.0.0.1:8080](http://127.0.0.1:8080)**

### 3. Demonstrate State Reconciliation
Inject an untracked position into the broker before startup to see the reconciler automatically catch and heal the drift:
```bash
python src/main.py --demo-discrepancy
```
*Output:*
```
[WARNING] (reconciler) [bot_alpha] UNTRACKED POSITION: Broker reports 10.0 shares of SYNTH_A, but local state had 0. (Adopting broker: 10.0 shares).
[WARNING] (reconciler) [bot_alpha] State reconciliation completed with 1 discrepancy(s). All local state updated to match broker ground-truth.
```

### 4. Demonstrate Watchdog Auto-Recovery
Simulate a frozen bot (unresponsive thread) and watch the supervisor detect the missed heartbeat and automatically recover it:
```bash
python src/main.py --demo-watchdog
```
*Output:*
```
[WARNING] (supervisor) [Supervisor] Bot 'bot_beta' MISSED HEARTBEAT! Age: 4.1s (Threshold: 3.0s).
[WARNING] (supervisor) [Supervisor] Attempting recovery for bot 'bot_beta' (Attempt 1/3)...
[INFO]    (supervisor) [Supervisor] Successfully recovered bot 'bot_beta'.
```

### 5. Trigger the Global Kill Switch
While the fleet is running, trigger an emergency stop via either:
- **Web UI:** Click the red **"HALT FLEET (KILL SWITCH)"** button in the dashboard.
- **Terminal:** Create the flag file:
  ```bash
  touch KILL_SWITCH
  ```
All bots immediately transition to `HALTED`, open orders are cancelled, and trading ceases. Remove the file or click **"RESUME FLEET"** to clear.

### 6. Run the Test Suite
```bash
python -m unittest discover tests
```
Runs 11 automated unit and integration tests covering order execution, ghost position recovery, quantity mismatches, watchdog recovery, and emergency halts.

---

## 6. How to Build a Production Strategy Plugin

To add a new strategy bot to the supervisor, subclass `BaseBot` and implement `on_tick()`:

```python
from bot_base import BaseBot
from typing import Dict, Any

class MyCustomStrategy(BaseBot):
    def on_start(self) -> None:
        # Runs AFTER broker state reconciliation
        print(f"Strategy {self.name} initialized with positions: {self.local_positions}")

    def on_tick(self, tick_data: Dict[str, Any]) -> None:
        price = tick_data["price"]
        
        # Example: Simple threshold rule
        if price < 100.0 and self.local_positions.get(self.symbol, 0) == 0:
            self.broker.place_order(
                bot_id=self.bot_id,
                symbol=self.symbol,
                side="BUY",
                qty=1.0,
                price=price
            )
            # Reconcile or update local position
            self.local_positions[self.symbol] = 1.0

    def on_stop(self) -> None:
        # Graceful cleanup on shutdown or kill switch
        print("Cleaning up strategy resources...")
```

---

## 7. License

MIT License. Free for academic, educational, and institutional engineering purposes.

