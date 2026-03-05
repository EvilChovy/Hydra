#!/usr/bin/env python3
"""
HYDRA LOG VIEWER — Real-time dashboard for monitoring bot logs.

Run alongside the bot in a separate terminal:
    .venv\\Scripts\\python log_viewer.py

Then open http://localhost:8777 in your browser.
"""

import os
import json
import html
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
PORT = 8777

# ── Colors for log levels ──
LEVEL_COLORS = {
    "CRITICAL": "#ff2d55",
    "ERROR": "#ff453a",
    "WARNING": "#ffd60a",
    "INFO": "#30d158",
    "DEBUG": "#64d2ff",
}

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>HYDRA Log Viewer</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Space+Grotesk:wght@500;700&display=swap" rel="stylesheet">
<style>
  :root {
    --bg: #0a0a0f;
    --surface: #12121a;
    --surface2: #1a1a26;
    --border: #2a2a3a;
    --text: #e0e0e8;
    --text-dim: #6a6a80;
    --green: #30d158;
    --red: #ff453a;
    --yellow: #ffd60a;
    --blue: #64d2ff;
    --pink: #ff2d55;
    --accent: #bf5af2;
  }

  * { margin: 0; padding: 0; box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'JetBrains Mono', monospace;
    font-size: 12px;
    height: 100vh;
    overflow: hidden;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }

  .header-left {
    display: flex;
    align-items: center;
    gap: 14px;
  }

  .logo {
    font-family: 'Space Grotesk', sans-serif;
    font-size: 18px;
    font-weight: 700;
    background: linear-gradient(135deg, var(--green), var(--blue));
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    letter-spacing: 2px;
  }

  .status-dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s ease-in-out infinite;
  }

  .status-dot.paused { background: var(--yellow); animation: none; }

  @keyframes pulse {
    0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(48,209,88,0.4); }
    50% { opacity: 0.8; box-shadow: 0 0 0 6px rgba(48,209,88,0); }
  }

  .header-right {
    display: flex;
    align-items: center;
    gap: 16px;
  }

  .refresh-info {
    color: var(--text-dim);
    font-size: 11px;
  }

  .btn {
    padding: 5px 14px;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--surface2);
    color: var(--text);
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
    transition: all 0.15s;
  }

  .btn:hover { border-color: var(--accent); color: var(--accent); }
  .btn.active { border-color: var(--green); color: var(--green); background: rgba(48,209,88,0.08); }

  /* ── Tabs ── */
  .tabs {
    display: flex;
    gap: 0;
    padding: 0 20px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
  }

  .tab {
    padding: 10px 20px;
    font-family: 'Space Grotesk', sans-serif;
    font-size: 13px;
    font-weight: 500;
    color: var(--text-dim);
    cursor: pointer;
    border-bottom: 2px solid transparent;
    transition: all 0.15s;
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .tab:hover { color: var(--text); }
  .tab.active { color: var(--green); border-bottom-color: var(--green); }
  .tab.active.trades { color: var(--blue); border-bottom-color: var(--blue); }
  .tab.active.errors { color: var(--red); border-bottom-color: var(--red); }

  .tab-badge {
    font-size: 10px;
    padding: 1px 6px;
    border-radius: 8px;
    font-family: 'JetBrains Mono', monospace;
  }

  .tab.active .tab-badge { background: rgba(48,209,88,0.15); color: var(--green); }
  .tab.active.trades .tab-badge { background: rgba(100,210,255,0.15); color: var(--blue); }
  .tab.active.errors .tab-badge { background: rgba(255,69,58,0.15); color: var(--red); }

  /* ── Stats bar ── */
  .stats-bar {
    display: flex;
    gap: 24px;
    padding: 8px 20px;
    background: var(--surface2);
    border-bottom: 1px solid var(--border);
    font-size: 11px;
  }

  .stat { display: flex; gap: 6px; }
  .stat-label { color: var(--text-dim); }
  .stat-value { font-weight: 600; }
  .stat-value.up { color: var(--green); }
  .stat-value.down { color: var(--red); }
  .stat-value.neutral { color: var(--text); }

  /* ── Filter bar ── */
  .filter-bar {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 20px;
    border-bottom: 1px solid var(--border);
  }

  .filter-input {
    flex: 1;
    padding: 5px 10px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 5px;
    color: var(--text);
    font-family: inherit;
    font-size: 12px;
    outline: none;
  }

  .filter-input:focus { border-color: var(--accent); }
  .filter-input::placeholder { color: var(--text-dim); }

  .level-filter {
    display: flex;
    gap: 4px;
  }

  .level-btn {
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 600;
    cursor: pointer;
    border: 1px solid var(--border);
    background: transparent;
    transition: all 0.15s;
  }

  .level-btn.on { border-color: currentColor; }

  /* ── Log viewer ── */
  .log-container {
    height: calc(100vh - 180px);
    overflow-y: auto;
    padding: 0;
    scroll-behavior: smooth;
  }

  .log-line {
    display: flex;
    padding: 2px 20px;
    border-bottom: 1px solid rgba(42,42,58,0.3);
    font-size: 11.5px;
    line-height: 1.7;
    transition: background 0.1s;
  }

  .log-line:hover { background: var(--surface2); }
  .log-line.highlight { background: rgba(191,90,242,0.08); }

  .log-time { color: var(--text-dim); min-width: 160px; flex-shrink: 0; }
  .log-source { color: var(--accent); min-width: 160px; flex-shrink: 0; }
  .log-level {
    min-width: 70px;
    flex-shrink: 0;
    font-weight: 700;
    text-transform: uppercase;
  }
  .log-msg { color: var(--text); white-space: pre-wrap; word-break: break-word; }

  .level-INFO .log-level { color: var(--green); }
  .level-ERROR .log-level { color: var(--red); }
  .level-WARNING .log-level { color: var(--yellow); }
  .level-CRITICAL .log-level { color: var(--pink); }
  .level-DEBUG .log-level { color: var(--blue); }

  .log-line.trade-open { border-left: 3px solid var(--green); }
  .log-line.trade-close-win { border-left: 3px solid var(--green); background: rgba(48,209,88,0.03); }
  .log-line.trade-close-loss { border-left: 3px solid var(--red); background: rgba(255,69,58,0.03); }
  .log-line.signal { border-left: 3px solid var(--blue); }

  /* ── Empty state ── */
  .empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    height: 60%;
    color: var(--text-dim);
    gap: 12px;
  }

  .empty-icon { font-size: 40px; opacity: 0.3; }
  .empty-text { font-family: 'Space Grotesk', sans-serif; font-size: 15px; }

  /* ── Scrollbar ── */
  ::-webkit-scrollbar { width: 8px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
  ::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <div class="logo">HYDRA</div>
    <div class="status-dot" id="statusDot"></div>
    <span class="refresh-info" id="lastUpdate">Connecting...</span>
  </div>
  <div class="header-right">
    <label style="display:flex;align-items:center;gap:6px;color:var(--text-dim);font-size:11px;cursor:pointer">
      <input type="checkbox" id="autoScroll" checked style="accent-color:var(--green)"> Auto-scroll
    </label>
    <select id="refreshRate" class="btn" style="padding:4px 8px">
      <option value="2000">2s</option>
      <option value="5000" selected>5s</option>
      <option value="10000">10s</option>
      <option value="30000">30s</option>
    </select>
    <button class="btn" onclick="fetchLogs()">Refresh</button>
  </div>
</div>

<div class="tabs">
  <div class="tab active" data-file="hydra.log" onclick="switchTab(this)">
    General
    <span class="tab-badge" id="badge-general">0</span>
  </div>
  <div class="tab trades" data-file="hydra_trades.log" onclick="switchTab(this)">
    Trades
    <span class="tab-badge" id="badge-trades">0</span>
  </div>
  <div class="tab errors" data-file="hydra_errors.log" onclick="switchTab(this)">
    Errors
    <span class="tab-badge" id="badge-errors">0</span>
  </div>
</div>

<div class="filter-bar">
  <input class="filter-input" id="filterInput" placeholder="Filter logs... (e.g. SIGNAL, TRADE, MACRO, TP1)" oninput="applyFilter()">
  <div class="level-filter">
    <button class="level-btn on" style="color:var(--blue)" data-level="DEBUG" onclick="toggleLevel(this)">DBG</button>
    <button class="level-btn on" style="color:var(--green)" data-level="INFO" onclick="toggleLevel(this)">INF</button>
    <button class="level-btn on" style="color:var(--yellow)" data-level="WARNING" onclick="toggleLevel(this)">WRN</button>
    <button class="level-btn on" style="color:var(--red)" data-level="ERROR" onclick="toggleLevel(this)">ERR</button>
    <button class="level-btn on" style="color:var(--pink)" data-level="CRITICAL" onclick="toggleLevel(this)">CRT</button>
  </div>
</div>

<div class="log-container" id="logContainer"></div>

<script>
let currentFile = 'hydra.log';
let logCache = {};
let activeLevels = new Set(['DEBUG','INFO','WARNING','ERROR','CRITICAL']);
let refreshTimer = null;

const LOG_RE = /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*\|\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(.*)$/;

function switchTab(el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  currentFile = el.dataset.file;
  renderLogs();
}

function toggleLevel(btn) {
  const level = btn.dataset.level;
  btn.classList.toggle('on');
  if (activeLevels.has(level)) activeLevels.delete(level);
  else activeLevels.add(level);
  renderLogs();
}

function parseLine(raw) {
  const m = raw.match(LOG_RE);
  if (m) return { time: m[1], source: m[2], level: m[3], msg: m[4], raw };
  return { time: '', source: '', level: '', msg: raw, raw };
}

function classifyLine(parsed) {
  const m = parsed.msg;
  if (m.includes('TRADE OPENED')) return 'trade-open';
  if (m.includes('TRADE CLOSED') && m.includes('+')) return 'trade-close-win';
  if (m.includes('TRADE CLOSED')) return 'trade-close-loss';
  if (m.includes('SIGNAL') || m.includes('ENTRY SIGNAL')) return 'signal';
  return '';
}

function renderLogs() {
  const container = document.getElementById('logContainer');
  const lines = logCache[currentFile] || [];
  const filter = document.getElementById('filterInput').value.toLowerCase();

  if (lines.length === 0) {
    container.innerHTML = '<div class="empty"><div class="empty-icon">&#9776;</div><div class="empty-text">No logs yet</div><div style="color:var(--text-dim);font-size:11px">Waiting for bot output...</div></div>';
    return;
  }

  const parsed = lines.map(parseLine);
  const filtered = parsed.filter(p => {
    if (p.level && !activeLevels.has(p.level)) return false;
    if (filter && !p.raw.toLowerCase().includes(filter)) return false;
    return true;
  });

  const html = filtered.map(p => {
    const extra = classifyLine(p);
    const levelClass = p.level ? 'level-' + p.level : '';
    return '<div class="log-line ' + levelClass + ' ' + extra + '">' +
      '<span class="log-time">' + esc(p.time) + '</span>' +
      '<span class="log-source">' + esc(p.source) + '</span>' +
      '<span class="log-level">' + esc(p.level) + '</span>' +
      '<span class="log-msg">' + esc(p.msg) + '</span>' +
    '</div>';
  }).join('');

  container.innerHTML = html;

  if (document.getElementById('autoScroll').checked) {
    container.scrollTop = container.scrollHeight;
  }
}

function esc(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function applyFilter() { renderLogs(); }

async function fetchLogs() {
  try {
    const res = await fetch('/api/logs');
    const data = await res.json();
    logCache = data.files;

    // Update badges
    const gl = (logCache['hydra.log'] || []).length;
    const tl = (logCache['hydra_trades.log'] || []).length;
    const el = (logCache['hydra_errors.log'] || []).length;
    document.getElementById('badge-general').textContent = gl;
    document.getElementById('badge-trades').textContent = tl;
    document.getElementById('badge-errors').textContent = el;

    // Status
    const dot = document.getElementById('statusDot');
    const now = new Date();
    document.getElementById('lastUpdate').textContent = 'Updated ' + now.toLocaleTimeString();

    if (data.bot_alive) {
      dot.className = 'status-dot';
    } else {
      dot.className = 'status-dot paused';
    }

    renderLogs();
  } catch (e) {
    document.getElementById('statusDot').className = 'status-dot paused';
    document.getElementById('lastUpdate').textContent = 'Connection lost';
  }
}

function startAutoRefresh() {
  if (refreshTimer) clearInterval(refreshTimer);
  const rate = parseInt(document.getElementById('refreshRate').value);
  refreshTimer = setInterval(fetchLogs, rate);
}

document.getElementById('refreshRate').addEventListener('change', startAutoRefresh);

// Init
fetchLogs();
startAutoRefresh();
</script>

</body>
</html>"""


class LogViewerHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves the dashboard and log data."""

    def log_message(self, format, *args):
        pass  # Suppress default logging

    def do_GET(self):
        if self.path == "/":
            self._serve_html()
        elif self.path == "/api/logs":
            self._serve_logs()
        else:
            self.send_error(404)

    def _serve_html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML_PAGE.encode("utf-8"))

    def _serve_logs(self):
        files = {}
        log_files = ["hydra.log", "hydra_trades.log", "hydra_errors.log"]
        bot_alive = False

        for fname in log_files:
            fpath = LOG_DIR / fname
            lines = []
            if fpath.exists():
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        # Read last 500 lines
                        all_lines = f.readlines()
                        lines = [l.rstrip() for l in all_lines[-500:] if l.strip()]
                except Exception:
                    lines = ["[Error reading log file]"]
            files[fname] = lines

        # Check if bot is alive (log modified in last 60s)
        main_log = LOG_DIR / "hydra.log"
        if main_log.exists():
            try:
                import time as _t
                age = _t.time() - main_log.stat().st_mtime
                bot_alive = age < 60
            except Exception:
                bot_alive = False

        data = json.dumps({"files": files, "bot_alive": bot_alive})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(data.encode("utf-8"))


def main():
    if not LOG_DIR.exists():
        LOG_DIR.mkdir(exist_ok=True)
        print(f"Created logs directory: {LOG_DIR}")

    server = HTTPServer(("0.0.0.0", PORT), LogViewerHandler)
    print(f"")
    print(f"  ====  HYDRA LOG VIEWER  ====")
    print(f"")
    print(f"  Dashboard:  http://localhost:{PORT}")
    print(f"  Log dir:    {LOG_DIR.resolve()}")
    print(f"")
    print(f"  Open the URL above in your browser.")
    print(f"  Press Ctrl+C to stop.")
    print(f"")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLog viewer stopped.")
        server.server_close()


if __name__ == "__main__":
    main()
