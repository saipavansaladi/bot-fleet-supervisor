"""
Minimal Monitoring Dashboard & REST API
=======================================

A zero-dependency HTTP server built with Python's standard `http.server` module.

Endpoints:
----------
- GET  /            : Responsive, dark-mode single-page operational web UI.
- GET  /api/status  : JSON telemetry (bot states, heartbeats, broker positions).
- POST /api/kill    : Operator endpoint to trigger the emergency kill switch.
- POST /api/resume  : Operator endpoint to clear the kill switch.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
from socketserver import ThreadingMixIn
import threading
from typing import Any, Dict
from supervisor import BotFleetSupervisor

logger = logging.getLogger("dashboard")

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Bot Fleet Supervisor | Live Mission Control</title>
  <style>
    :root {
      --bg: #0d1117;
      --card-bg: #161b22;
      --border: #30363d;
      --text: #c9d1d9;
      --heading: #f0f6fc;
      --green: #238636;
      --red: #da3633;
      --yellow: #d29922;
      --blue: #58a6ff;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
    body { background-color: var(--bg); color: var(--text); padding: 24px; }
    .container { max-width: 1100px; margin: 0 auto; }
    header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; border-bottom: 1px solid var(--border); padding-bottom: 16px; }
    h1 { color: var(--heading); font-size: 22px; font-weight: 600; }
    .status-banner { display: none; padding: 12px 18px; border-radius: 6px; margin-bottom: 20px; font-weight: 600; text-align: center; }
    .banner-danger { background-color: rgba(218, 54, 51, 0.2); border: 1px solid var(--red); color: #ff7b72; display: block; }
    .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-bottom: 24px; }
    .card { background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 18px; }
    .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: #8b949e; margin-bottom: 8px; }
    .card-value { font-size: 24px; font-weight: 600; color: var(--heading); }
    table { width: 100%; border-collapse: collapse; background-color: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; overflow: hidden; margin-bottom: 24px; }
    th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid var(--border); font-size: 14px; }
    th { background-color: #21262d; color: var(--heading); font-weight: 600; }
    .badge { display: inline-block; padding: 3px 8px; border-radius: 12px; font-size: 11px; font-weight: 700; text-transform: uppercase; }
    .badge-running { background-color: rgba(35, 134, 54, 0.2); color: #3fb950; border: 1px solid var(--green); }
    .badge-halted, .badge-error { background-color: rgba(218, 54, 51, 0.2); color: #ff7b72; border: 1px solid var(--red); }
    .badge-stopped, .badge-initializing { background-color: rgba(210, 153, 34, 0.2); color: #d29922; border: 1px solid var(--yellow); }
    .btn { padding: 8px 16px; border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; border: 1px solid transparent; transition: 0.2s; }
    .btn-kill { background-color: var(--red); color: #ffffff; }
    .btn-kill:hover { background-color: #b62324; }
    .btn-resume { background-color: var(--green); color: #ffffff; }
    .btn-resume:hover { background-color: #2ea043; }
    .timestamp { font-size: 12px; color: #8b949e; }
  </style>
</head>
<body>
  <div class="container">
    <header>
      <div>
        <h1>Bot Fleet Supervisor</h1>
        <div class="timestamp">Live Health & Watchdog Dashboard</div>
      </div>
      <div id="kill-controls">
        <button id="btn-kill" class="btn btn-kill" onclick="triggerKill()">HALT FLEET (KILL SWITCH)</button>
        <button id="btn-resume" class="btn btn-resume" style="display:none;" onclick="triggerResume()">RESUME FLEET</button>
      </div>
    </header>

    <div id="danger-banner" class="status-banner banner-danger" style="display:none;">
      GLOBAL KILL SWITCH ACTIVATED — ALL TRADING BOTS HALTED & OPEN ORDERS CANCELLED
    </div>

    <div class="grid">
      <div class="card">
        <div class="card-title">Fleet Status</div>
        <div class="card-value" id="val-fleet-status">--</div>
      </div>
      <div class="card">
        <div class="card-title">Active Bots</div>
        <div class="card-value" id="val-active-bots">0 / 0</div>
      </div>
      <div class="card">
        <div class="card-title">Broker Cash</div>
        <div class="card-value" id="val-broker-cash">$0.00</div>
      </div>
      <div class="card">
        <div class="card-title">Portfolio Equity</div>
        <div class="card-value" id="val-broker-equity">$0.00</div>
      </div>
    </div>

    <h2 style="font-size: 16px; color: var(--heading); margin-bottom: 12px;">Managed Bot Fleet</h2>
    <table>
      <thead>
        <tr>
          <th>Bot ID</th>
          <th>Strategy Name</th>
          <th>Symbol</th>
          <th>State</th>
          <th>Last Heartbeat</th>
          <th>Ticks</th>
          <th>Restarts</th>
          <th>Local Position</th>
        </tr>
      </thead>
      <tbody id="bot-table-body">
        <tr><td colspan="8" style="text-align:center; color:#8b949e;">Connecting to supervisor...</td></tr>
      </tbody>
    </table>
  </div>

  <script>
    async function updateDashboard() {
      try {
        const res = await fetch('/api/status');
        const data = await res.json();

        // Kill switch banner & buttons
        const isKill = data.kill_switch_active;
        document.getElementById('danger-banner').style.display = isKill ? 'block' : 'none';
        document.getElementById('btn-kill').style.display = isKill ? 'none' : 'inline-block';
        document.getElementById('btn-resume').style.display = isKill ? 'inline-block' : 'none';

        // Cards
        document.getElementById('val-fleet-status').innerText = isKill ? 'HALTED' : (data.supervisor_running ? 'HEALTHY' : 'IDLE');
        document.getElementById('val-fleet-status').style.color = isKill ? 'var(--red)' : 'var(--green)';
        document.getElementById('val-active-bots').innerText = `${data.active_bots} / ${data.total_bots}`;
        document.getElementById('val-broker-cash').innerText = `$${data.account.cash.toLocaleString()}`;
        document.getElementById('val-broker-equity').innerText = `$${data.account.portfolio_value.toLocaleString()}`;

        // Table
        const tbody = document.getElementById('bot-table-body');
        if (data.bots.length === 0) {
          tbody.innerHTML = '<tr><td colspan="8" style="text-align:center;">No bots registered.</td></tr>';
          return;
        }

        tbody.innerHTML = data.bots.map(b => {
          let badgeClass = 'badge-stopped';
          if (b.status === 'RUNNING') badgeClass = 'badge-running';
          else if (b.status === 'HALTED' || b.status === 'ERROR') badgeClass = 'badge-halted';

          const posStr = Object.entries(b.local_positions).map(([k,v]) => `${k}: ${v}`).join(', ') || 'Flat (0)';

          return `
            <tr>
              <td><strong>${b.bot_id}</strong></td>
              <td>${b.name}</td>
              <td><code>${b.symbol}</code></td>
              <td><span class="badge ${badgeClass}">${b.status}</span></td>
              <td>${b.heartbeat_age_sec}s ago</td>
              <td>${b.ticks_processed}</td>
              <td>${b.restarts}</td>
              <td>${posStr}</td>
            </tr>
          `;
        }).join('');
      } catch (err) {
        console.error('Failed to poll status:', err);
      }
    }

    async function triggerKill() {
      if (!confirm('Activate Global Kill Switch? All bots will be halted immediately.')) return;
      await fetch('/api/kill', { method: 'POST' });
      updateDashboard();
    }

    async function triggerResume() {
      await fetch('/api/resume', { method: 'POST' });
      updateDashboard();
    }

    setInterval(updateDashboard, 1500);
    updateDashboard();
  </script>
</body>
</html>
"""


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """Multi-threaded standard library HTTP server."""
    daemon_threads = True


class DashboardHandler(BaseHTTPRequestHandler):
    """Request router for monitoring UI and REST API."""

    supervisor: BotFleetSupervisor  # attached at server instantiation

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard logging to keep terminal output clean."""
        return

    def do_GET(self) -> None:
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))

        elif self.path == "/api/status":
            status = self.supervisor.get_fleet_status()
            payload = json.dumps(status).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/api/kill":
            self.supervisor.activate_kill_switch(reason="dashboard_web_operator")
            self._send_json({"status": "ok", "message": "Kill switch engaged"})

        elif self.path == "/api/resume":
            self.supervisor.deactivate_kill_switch()
            self._send_json({"status": "ok", "message": "Kill switch cleared"})

        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, data: Dict[str, Any]) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_dashboard_server(
    supervisor: BotFleetSupervisor,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadedHTTPServer:
    """
    Launch the monitoring dashboard HTTP server in a background thread.
    """
    handler = DashboardHandler
    handler.supervisor = supervisor
    server = ThreadedHTTPServer((host, port), handler)

    server_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="DashboardServer",
    )
    server_thread.start()
    logger.info(f"[Dashboard] Monitoring dashboard online at http://{host}:{port}")
    return server

