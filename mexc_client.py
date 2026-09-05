"""
MEXC REST API Client (v3)
==========================
Handles public market data fetching (klines, ticker) and authenticated
private account requests (HMAC-SHA256 signature) for MEXC exchange.
"""

import time
import hmac
import hashlib
import json
import urllib.request
import urllib.parse
from typing import List, Dict, Any, Optional

MEXC_BASE_URL = "https://api.mexc.com"

# MEXC expects specific interval codes (e.g. 60m instead of 1h)
TIMEFRAME_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "60m": "60m",
    "4h": "4h",
    "1d": "1d",
    "1D": "1d",
    "1w": "1W",
    "1W": "1W",
}


def normalize_interval(interval: str) -> str:
    """Normalizes interval to MEXC API requirements."""
    return TIMEFRAME_MAP.get(interval.strip(), interval.strip())


def fetch_mexc_candles(symbol: str, interval: str, limit: int = 200) -> List[Dict[str, Any]]:
    """
    Fetches official candlestick data from MEXC v3 API.
    Returns: List of {"time": int, "open": float, "high": float, "low": float, "close": float, "volume": float}
    """
    mexc_interval = normalize_interval(interval)
    url = f"{MEXC_BASE_URL}/api/v3/klines?symbol={symbol.upper()}&interval={mexc_interval}&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})

    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode())

    candles = []
    for item in data:
        candles.append({
            "time": int(item[0] // 1000),  # open time in unix seconds
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        })
    return candles


def fetch_mexc_ticker(symbol: str) -> float:
    """Fetches real-time price from MEXC."""
    url = f"{MEXC_BASE_URL}/api/v3/ticker/price?symbol={symbol.upper()}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode())
    return float(data["price"])


class MexcClient:
    """MEXC API Client supporting both public market data and private signed endpoints."""

    def __init__(self, api_key: Optional[str] = None, secret_key: Optional[str] = None):
        self.api_key = (api_key or "").strip()
        self.secret_key = (secret_key or "").strip()

    @property
    def has_credentials(self) -> bool:
        return bool(
            self.api_key
            and self.secret_key
            and self.api_key != "YOUR_MEXC_API_KEY"
            and self.secret_key != "YOUR_MEXC_SECRET_KEY"
        )

    def _generate_signature(self, query_string: str) -> str:
        """Generates HMAC-SHA256 signature for private endpoints."""
        return hmac.new(
            self.secret_key.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def get_klines(self, symbol: str, interval: str, limit: int = 200) -> List[Dict[str, Any]]:
        return fetch_mexc_candles(symbol, interval, limit)

    def get_ticker_price(self, symbol: str) -> float:
        return fetch_mexc_ticker(symbol)

    def get_account_balances(self) -> Dict[str, Any]:
        """
        Fetches real spot balances from MEXC account using private API.
        Requires valid api_key and secret_key.
        """
        if not self.has_credentials:
            return {"error": "MEXC credentials not configured in .env or config.json"}

        timestamp = int(time.time() * 1000)
        params = {"timestamp": timestamp, "recvWindow": 5000}
        query_str = urllib.parse.urlencode(params)
        signature = self._generate_signature(query_str)
        full_query = f"{query_str}&signature={signature}"

        url = f"{MEXC_BASE_URL}/api/v3/account?{full_query}"
        req = urllib.request.Request(
            url,
            headers={
                "X-MEXC-APIKEY": self.api_key,
                "Content-Type": "application/json",
                "User-Agent": "PivotTrader/2.0",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
            balances = {}
            for b in data.get("balances", []):
                free = float(b.get("free", 0))
                locked = float(b.get("locked", 0))
                if free > 0 or locked > 0:
                    balances[b["asset"]] = {"free": free, "locked": locked, "total": free + locked}
            return {"success": True, "balances": balances}
        except Exception as e:
            return {"error": str(e)}
