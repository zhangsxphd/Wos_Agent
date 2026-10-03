"""Small, rate-limited client for the official WoS Starter API."""

import os
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://api.clarivate.com/apis/wos-starter/v1"


class WosApiError(RuntimeError):
    pass


class WosStarterClient:
    def __init__(self, api_key=None, min_interval=0.25, timeout=30):
        load_dotenv(ROOT / ".env")
        self.api_key = api_key or os.getenv("WOS_STARTER_API_KEY")
        if not self.api_key or not self.api_key.strip():
            raise WosApiError("WOS_STARTER_API_KEY not found")
        if min_interval < 0.25 or timeout <= 0:
            raise ValueError("min_interval must be >= 0.25; timeout must be positive")
        self.api_key = self.api_key.strip()
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_request_time = None
        self.request_count = 0
        self.session = requests.Session()
        self.session.headers.update({"X-ApiKey": self.api_key})

    def redact(self, text):
        return str(text).replace(self.api_key, "[REDACTED]")

    def close(self):
        self.session.close()

    def _wait_for_rate_limit(self):
        if self._last_request_time is not None:
            elapsed = time.monotonic() - self._last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)

    @staticmethod
    def _retry_delay(response, attempt):
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    retry_at = parsedate_to_datetime(value)
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=timezone.utc)
                    return max(0.0, (retry_at - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        return float(2 ** attempt)

    def _get(self, endpoint, params=None, retries=4):
        if retries < 1:
            raise ValueError("retries must be positive")
        url = f"{BASE_URL}{endpoint}"
        for attempt in range(retries):
            self._wait_for_rate_limit()
            self.request_count += 1
            try:
                response = self.session.get(
                    url, params=params, timeout=self.timeout, allow_redirects=False
                )
            except (requests.ConnectionError, requests.Timeout) as exc:
                self._last_request_time = time.monotonic()
                if attempt == retries - 1:
                    raise WosApiError(self.redact(f"WoS connection failed: {exc}")) from None
                time.sleep(2 ** attempt)
                continue
            except requests.RequestException as exc:
                raise WosApiError(self.redact(f"WoS request failed: {exc}")) from None
            self._last_request_time = time.monotonic()
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    raise WosApiError("WoS returned HTTP 200 with invalid JSON") from None
                if not isinstance(data, dict):
                    raise WosApiError("WoS response must be a JSON object")
                return data
            error = self.redact(
                f"WoS API error {response.status_code}: {response.text[:1000]}"
            )
            retryable = response.status_code == 429 or 500 <= response.status_code < 600
            if not retryable or attempt == retries - 1:
                raise WosApiError(error)
            delay = self._retry_delay(response, attempt)
            if delay > 60:
                raise WosApiError(
                    f"{error}; Retry-After is {delay:.0f}s; retry this search later"
                )
            time.sleep(delay)
        raise WosApiError("WoS request failed after retries")

    def search_documents(
        self, query, db="WOS", limit=50, page=1, detail="full", sort_field=None
    ):
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must not be empty")
        if not 1 <= limit <= 50 or page < 1:
            raise ValueError("limit must be 1-50; page must be positive")
        params = {"db": db, "q": query, "limit": limit, "page": page, "detail": detail}
        if sort_field:
            params["sortField"] = sort_field
        return self._get("/documents", params=params)
