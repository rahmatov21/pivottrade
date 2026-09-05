"""
Configuration Manager for PivotTrader
====================================
Loads settings from .env and config.json with full bidirectional persistence.
Environment variables from .env take top priority.
"""

import json
import os
from typing import Dict, Any

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Try importing dotenv, or fallback to simple parser
try:
    from dotenv import load_dotenv
    load_dotenv(ENV_FILE, override=True)
except ImportError:
    pass

DEFAULT_CONFIG: Dict[str, Any] = {
    "exchange": "MEXC",
    "symbol": "BTCUSDT",
    "timeframe": "15m",
    "pivot_length": 10,
    "risk_reward_ratio": 1.5,
    "sl_buffer_pct": 0.002,
    "initial_position_size_pct": 0.75,
    "dip_position_size_pct": 0.25,
    "dip_trigger_pct": 0.015,
    "initial_balance": 100.0,
    "poll_interval_seconds": 10,
    "trading_enabled": True,
    "telegram_bot_token": "YOUR_TELEGRAM_BOT_TOKEN",
    "telegram_admin_id": "YOUR_TELEGRAM_ADMIN_ID",
    "mexc_api_key": "YOUR_MEXC_API_KEY",
    "mexc_secret_key": "YOUR_MEXC_SECRET_KEY",
}


def parse_env_file() -> Dict[str, str]:
    """Simple parser for .env file if dotenv isn't loaded."""
    env_vars = {}
    if os.path.exists(ENV_FILE):
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    env_vars[key.strip().upper()] = val.strip().strip('"').strip("'")
    return env_vars


def load_config() -> Dict[str, Any]:
    """
    Loads configuration.
    Priority:
      1. .env file & os.environ
      2. config.json
      3. DEFAULT_CONFIG
    """
    cfg = DEFAULT_CONFIG.copy()

    # Load from config.json if exists
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg.update(data)
        except Exception as e:
            print(f"[CONFIG ERROR] Could not read {CONFIG_FILE}: {e}")

    # Override from .env / os.environ
    env_vars = parse_env_file()
    for k, v in os.environ.items():
        env_vars[k.upper()] = v

    if "EXCHANGE" in env_vars and env_vars["EXCHANGE"]:
        cfg["exchange"] = env_vars["EXCHANGE"].upper()
    if "MEXC_API_KEY" in env_vars and env_vars["MEXC_API_KEY"]:
        cfg["mexc_api_key"] = env_vars["MEXC_API_KEY"]
    if "MEXC_SECRET_KEY" in env_vars and env_vars["MEXC_SECRET_KEY"]:
        cfg["mexc_secret_key"] = env_vars["MEXC_SECRET_KEY"]
    if "TELEGRAM_BOT_TOKEN" in env_vars and env_vars["TELEGRAM_BOT_TOKEN"]:
        cfg["telegram_bot_token"] = env_vars["TELEGRAM_BOT_TOKEN"]
    if "TELEGRAM_ADMIN_ID" in env_vars and env_vars["TELEGRAM_ADMIN_ID"]:
        cfg["telegram_admin_id"] = env_vars["TELEGRAM_ADMIN_ID"]
    if "SYMBOL" in env_vars and env_vars["SYMBOL"]:
        cfg["symbol"] = env_vars["SYMBOL"].upper()
    if "TIMEFRAME" in env_vars and env_vars["TIMEFRAME"]:
        cfg["timeframe"] = env_vars["TIMEFRAME"]
    if "PIVOT_LENGTH" in env_vars:
        try:
            cfg["pivot_length"] = int(env_vars["PIVOT_LENGTH"])
        except ValueError:
            pass
    if "RISK_REWARD_RATIO" in env_vars:
        try:
            cfg["risk_reward_ratio"] = float(env_vars["RISK_REWARD_RATIO"])
        except ValueError:
            pass
    if "SL_BUFFER_PCT" in env_vars:
        try:
            cfg["sl_buffer_pct"] = float(env_vars["SL_BUFFER_PCT"])
        except ValueError:
            pass
    if "INITIAL_POSITION_SIZE_PCT" in env_vars:
        try:
            cfg["initial_position_size_pct"] = float(env_vars["INITIAL_POSITION_SIZE_PCT"])
        except ValueError:
            pass
    if "DIP_POSITION_SIZE_PCT" in env_vars:
        try:
            cfg["dip_position_size_pct"] = float(env_vars["DIP_POSITION_SIZE_PCT"])
        except ValueError:
            pass
    if "DIP_TRIGGER_PCT" in env_vars:
        try:
            cfg["dip_trigger_pct"] = float(env_vars["DIP_TRIGGER_PCT"])
        except ValueError:
            pass
    if "INITIAL_BALANCE" in env_vars:
        try:
            cfg["initial_balance"] = float(env_vars["INITIAL_BALANCE"])
        except ValueError:
            pass
    if "POLL_INTERVAL_SECONDS" in env_vars:
        try:
            cfg["poll_interval_seconds"] = int(env_vars["POLL_INTERVAL_SECONDS"])
        except ValueError:
            pass
    if "TRADING_ENABLED" in env_vars:
        cfg["trading_enabled"] = env_vars["TRADING_ENABLED"].lower() in ("true", "1", "yes")

    return cfg


def save_config(config: Dict[str, Any]) -> None:
    """Saves dictionary to config.json while protecting sensitive credentials."""
    try:
        to_save = config.copy()
        # Ensure credentials stay protected as placeholders in tracked config.json
        if to_save.get("telegram_bot_token") and to_save["telegram_bot_token"] != "YOUR_TELEGRAM_BOT_TOKEN":
            to_save["telegram_bot_token"] = "YOUR_TELEGRAM_BOT_TOKEN"
        if to_save.get("telegram_admin_id") and to_save["telegram_admin_id"] != "YOUR_TELEGRAM_ADMIN_ID":
            to_save["telegram_admin_id"] = "YOUR_TELEGRAM_ADMIN_ID"
        if to_save.get("mexc_api_key") and to_save["mexc_api_key"] != "YOUR_MEXC_API_KEY":
            to_save["mexc_api_key"] = "YOUR_MEXC_API_KEY"
        if to_save.get("mexc_secret_key") and to_save["mexc_secret_key"] != "YOUR_MEXC_SECRET_KEY":
            to_save["mexc_secret_key"] = "YOUR_MEXC_SECRET_KEY"

        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(to_save, f, indent=2)
    except Exception as e:
        print(f"[CONFIG ERROR] Failed to save {CONFIG_FILE}: {e}")


def update_config_value(key: str, value: Any) -> Dict[str, Any]:
    """Updates key in config.json and returns updated config."""
    cfg = load_config()
    cfg[key] = value
    save_config(cfg)
    return cfg
