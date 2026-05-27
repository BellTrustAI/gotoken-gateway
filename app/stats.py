"""In-memory usage stats per provider. Not persisted — resets on restart."""

import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")


def _empty_counter() -> dict:
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0}


class StatsCollector:
    def __init__(self):
        self._lock = threading.Lock()
        # _daily: date_str -> provider -> counter
        self._daily: dict[str, dict[str, dict]] = defaultdict(dict)
        self._last_request: dict[str, int] = {}  # provider -> timestamp

    def record(self, provider: str, model: str, input_tokens: int, output_tokens: int):
        date_key = _today_str()
        with self._lock:
            daily = self._daily[date_key]
            if provider not in daily:
                daily[provider] = _empty_counter()
            c = daily[provider]
            c["requests"] += 1
            c["input_tokens"] += input_tokens
            c["output_tokens"] += output_tokens
            self._last_request[provider] = int(time.time())

    def snapshot(self) -> dict:
        today = _today_str()
        yesterday = _yesterday_str()
        with self._lock:
            result = {}
            # Collect all provider names
            all_providers: set[str] = set()
            for date_data in self._daily.values():
                all_providers.update(date_data.keys())

            for provider in all_providers:
                today_data = self._daily.get(today, {}).get(provider, _empty_counter())
                yesterday_data = self._daily.get(yesterday, {}).get(provider, _empty_counter())

                # Total from all dates
                total = _empty_counter().copy()
                for date_data in self._daily.values():
                    if provider in date_data:
                        for k in ("requests", "input_tokens", "output_tokens"):
                            total[k] += date_data[provider][k]

                result[provider] = {
                    "today": today_data,
                    "yesterday": yesterday_data,
                    "total": total,
                    "last_request": self._last_request.get(provider, 0),
                }
            return result


_collector = StatsCollector()


def get_collector() -> StatsCollector:
    return _collector
