from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Any

from flask import Flask, jsonify, request

app = Flask(__name__)

BASE = Path(__file__).resolve().parent
CONFIG_FILE = BASE / "user_config.json"
PID_FILE = BASE / "bot_process.pid"

VALID_LICENSES = {
    "DEMO-1234": {"name": "Demo User", "enabled": True},
    "RTB-TEST-2026": {"name": "Test User", "enabled": True},
}

def load_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {
        "symbol": "EURUSD",
        "risk_per_trade_pct": 0.5,
        "reward_to_risk": 2.0,
        "fast_ema": 50,
        "slow_ema": 200,
        "rsi_buy_threshold": 40,
        "rsi_sell_threshold": 60,
    }

def save_config(data: Dict[str, Any]) -> None:
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")

def license_ok(key: str) -> bool:
    return key in VALID_LICENSES and VALID_LICENSES[key]["enabled"]

@app.post("/activate")
def activate():
    data = request.get_json(force=True)
    key = data.get("license_key", "")
    return jsonify({"ok": license_ok(key)})

@app.get("/status")
def status():
    key = request.args.get("license_key", "")
    if not license_ok(key):
        return jsonify({"enabled": False, "message": "Invalid license"}), 403
    running = PID_FILE.exists()
    return jsonify({
        "enabled": running,
        "message": "Bot process running" if running else "Bot is stopped",
        "config": load_config(),
    })

@app.post("/config")
def config():
    data = request.get_json(force=True)
    key = data.get("license_key", "")
    if not license_ok(key):
      return jsonify({"message": "Invalid license"}), 403
    cfg = data.get("config", {})
    save_config(cfg)
    return jsonify({"message": "Configuration saved", "config": cfg})

@app.post("/start")
def start():
    data = request.get_json(force=True)
    key = data.get("license_key", "")
    if not license_ok(key):
        return jsonify({"message": "Invalid license"}), 403
    if PID_FILE.exists():
        return jsonify({"message": "Bot already running"})
    script = BASE / "mt5_trading_bot.py"
    if not script.exists():
        return jsonify({"message": "mt5_trading_bot.py not found"}), 500

    process = subprocess.Popen([sys.executable, str(script)])
    PID_FILE.write_text(str(process.pid), encoding="utf-8")
    return jsonify({"message": f"Bot started with pid {process.pid}"})

@app.post("/stop")
def stop():
    data = request.get_json(force=True)
    key = data.get("license_key", "")
    if not license_ok(key):
        return jsonify({"message": "Invalid license"}), 403
    if not PID_FILE.exists():
        return jsonify({"message": "Bot is not running"})
    pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/PID", str(pid)])
        else:
            os.kill(pid, 9)
    except Exception:
        pass
    PID_FILE.unlink(missing_ok=True)
    return jsonify({"message": "Bot stopped"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
