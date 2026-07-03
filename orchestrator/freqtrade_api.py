"""Minimal Freqtrade REST API client used by the brain service.

Auth via HTTP basic (username/password from env). All methods raise
``requests.RequestException`` subclasses on transport errors — callers
decide how to degrade.
"""

from __future__ import annotations

import os

import requests

DEFAULT_TIMEOUT = 15


class FreqtradeAPI:
    def __init__(
        self,
        base_url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
    ):
        self.base_url = (base_url or os.environ.get("FT_API_URL",
                                                    "http://127.0.0.1:8080")).rstrip("/")
        self.auth = (
            username or os.environ.get("FT_API_USERNAME", "santinela"),
            password or os.environ.get("FT_API_PASSWORD", ""),
        )
        self.timeout = timeout

    def _get(self, path: str):
        resp = requests.get(f"{self.base_url}/api/v1/{path}", auth=self.auth,
                            timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict | None = None):
        resp = requests.post(f"{self.base_url}/api/v1/{path}", json=payload or {},
                             auth=self.auth, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    # --- read ---------------------------------------------------------
    def ping(self) -> bool:
        try:
            resp = requests.get(f"{self.base_url}/api/v1/ping", timeout=self.timeout)
            return resp.ok
        except requests.RequestException:
            return False

    def show_config(self) -> dict:
        return self._get("show_config")

    def balance(self) -> dict:
        return self._get("balance")

    def status(self) -> list:
        return self._get("status")

    def profit(self) -> dict:
        return self._get("profit")

    def whitelist(self) -> dict:
        return self._get("whitelist")

    def pair_candles(self, pair: str, timeframe: str, limit: int = 200) -> dict:
        return self._get(
            f"pair_candles?pair={requests.utils.quote(pair, safe='')}"
            f"&timeframe={timeframe}&limit={limit}"
        )

    def equity(self) -> float:
        """Total account value in stake currency."""
        data = self.balance()
        return float(data.get("total_bot", data.get("total", 0.0)))

    # --- control ------------------------------------------------------
    def reload_config(self) -> dict:
        return self._post("reload_config")

    def stopentry(self) -> dict:
        return self._post("stopentry")

    def start(self) -> dict:
        return self._post("start")

    def stop(self) -> dict:
        return self._post("stop")

    def forceexit_all(self) -> dict:
        return self._post("forceexit", {"tradeid": "all"})
