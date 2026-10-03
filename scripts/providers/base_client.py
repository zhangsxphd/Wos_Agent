"""Rate-limited HTTP and persistent DOI cache shared by enrichment providers."""

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import requests
from dotenv import dotenv_values

from ..normalize_record import normalize_doi
from ..pipeline_utils import ROOT, assert_safe, known_secrets, utc_now, write_json


class ProviderError(RuntimeError):
    """Only fixed, credential-free error codes cross the provider boundary."""

    def __init__(self, provider, code, http_status=None):
        self.provider, self.code, self.http_status = provider, code, http_status
        super().__init__(f"{provider}: {code}" + (f" (HTTP {http_status})" if http_status else ""))


def canonical_doi(value):
    doi = normalize_doi(value) if isinstance(value, str) else None
    if not doi or not re.fullmatch(r"10\.\d{4,9}/\S+", doi):
        raise ValueError("Invalid DOI; a DOI-only lookup requires 10.<registrant>/<suffix>")
    return doi


def cache_key(doi):
    return hashlib.sha256(canonical_doi(doi).encode("utf-8")).hexdigest()


class BaseClient:
    provider = None
    credential_name = None

    def __init__(self, root=ROOT, cache_dir=None, session=None, refresh=False,
                 cache_only=False, timeout=30, retries=2, min_interval=1.0,
                 sleep=time.sleep, clock=time.monotonic):
        if refresh and cache_only:
            raise ValueError("--refresh and --cache-only cannot be combined")
        if timeout <= 0 or retries < 0 or min_interval < 0:
            raise ValueError("Invalid HTTP timeout/retry/rate configuration")
        self.root = Path(root)
        self.cache_dir = Path(cache_dir) if cache_dir else self.root / "data/cache" / self.provider
        self.refresh, self.cache_only = refresh, cache_only
        self.timeout, self.retries, self.min_interval = timeout, retries, min_interval
        self.sleep, self.clock, self.last_request_end = sleep, clock, None
        self.blocked_until = 0
        import os
        config = dotenv_values(self.root / ".env")
        self.api_key = (os.getenv(self.credential_name, config.get(self.credential_name))
                        if self.credential_name else None)
        self.secrets = known_secrets(self.root, extra=(self.api_key,))
        self.session = session if session is not None else requests.Session()
        self.session.headers.update({"User-Agent": "wos-research-agent/0.3 (DOI abstract enrichment)",
                                     "Accept": "application/json"})
        self.configure_auth()
        self.stats = {"requests": 0, "cache_hits": 0, "not_found": 0, "errors": 0}
        self.error_counts = {}
        self.last_lookup = {}

    def configure_auth(self):
        pass

    def request_spec(self, doi):
        raise NotImplementedError

    def parse(self, raw, retrieved_at):
        raise NotImplementedError

    def close(self):
        self.session.close()

    def _retry_delay(self, response, attempt):
        delay = 2 ** attempt
        value = response.headers.get("Retry-After") if response is not None else None
        if value:
            try:
                delay = max(delay, float(value))
            except (ValueError, TypeError):
                try:
                    stamp = parsedate_to_datetime(value)
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    delay = max(delay, (stamp - datetime.now(timezone.utc)).total_seconds())
                except (ValueError, TypeError, OverflowError):
                    pass
        if delay > 60:
            self.blocked_until = self.clock() + delay
            raise ProviderError(self.provider, "retry_after_exceeds_60s", getattr(response, "status_code", None))
        return max(0, delay)

    def _request(self, doi):
        if self.clock() < self.blocked_until:
            raise ProviderError(self.provider, "provider_cooldown", 429)
        url, params = self.request_spec(doi)
        for attempt in range(self.retries + 1):
            if self.last_request_end is not None:
                self.sleep(max(0, self.min_interval - (self.clock() - self.last_request_end)))
            self.stats["requests"] += 1
            response = None
            try:
                response = self.session.get(url, params=params, timeout=self.timeout, allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.retries:
                    raise ProviderError(self.provider, "network_error") from None
            except requests.RequestException:
                raise ProviderError(self.provider, "request_error") from None
            finally:
                self.last_request_end = self.clock()
            if response is not None:
                status = response.status_code
                if status in {200, 404}:
                    try:
                        raw = response.json()
                    except ValueError:
                        if status == 404:
                            raw = None
                        else:
                            raise ProviderError(self.provider, "invalid_json", status) from None
                    return status, raw
                if status != 429 and not 500 <= status < 600:
                    raise ProviderError(self.provider, "http_error", status)
                if attempt == self.retries:
                    raise ProviderError(self.provider, "rate_limited" if status == 429 else "server_error", status)
            self.sleep(self._retry_delay(response, attempt))
        raise ProviderError(self.provider, "request_error")

    def get_by_doi(self, doi):
        try:
            return self._get_by_doi(canonical_doi(doi))
        except ProviderError as exc:
            self.stats["errors"] += 1
            self.error_counts[exc.code] = self.error_counts.get(exc.code, 0) + 1
            raise
        except (ValueError, TypeError, KeyError, AttributeError, OSError):
            exc = ProviderError(self.provider, "invalid_data_or_cache")
            self.stats["errors"] += 1
            self.error_counts[exc.code] = self.error_counts.get(exc.code, 0) + 1
            raise exc from None

    def _get_by_doi(self, doi):
        path = self.cache_dir / (cache_key(doi) + ".json")
        self.last_lookup = {"cache_hit": False, "cache_file": str(path)}
        if path.is_file() and not self.refresh:
            entry = json.loads(path.read_text(encoding="utf-8"))
            assert_safe(entry, self.secrets)
            if (entry.get("provider") != self.provider or entry.get("lookup_doi") != doi
                    or entry.get("status") not in {"ok", "not_found"} or entry.get("cache_version") != 1):
                raise ProviderError(self.provider, "invalid_cache")
            self.stats["cache_hits"] += 1
            self.last_lookup["cache_hit"] = True
        else:
            if self.cache_only:
                raise ProviderError(self.provider, "cache_miss")
            status, raw = self._request(doi)
            # Reject credential echoes before they can reach either cache or canonical output.
            assert_safe(raw, self.secrets)
            entry = {"cache_version": 1, "provider": self.provider, "lookup_doi": doi,
                     "retrieved_at": utc_now(), "http_status": status,
                     "status": "not_found" if status == 404 else "ok", "raw": raw}
            write_json(path, entry, self.secrets, overwrite=self.refresh and path.exists())
        if entry["status"] == "not_found":
            self.stats["not_found"] += 1
            return None
        result = self.parse(entry["raw"], entry["retrieved_at"])
        if result is None:
            # A successful empty response is also a negative cache entry.
            entry["status"] = "not_found"
            write_json(path, entry, self.secrets, overwrite=True)
            self.stats["not_found"] += 1
        else:
            assert_safe(result, self.secrets)
            if any(result.get(field) is not None and not isinstance(result[field], str)
                   for field in ("provider_id", "doi", "title", "abstract", "url")):
                raise ProviderError(self.provider, "invalid_schema")
        return result

    def unified(self, raw, retrieved_at, **values):
        return dict(provider=self.provider, retrieved_at=retrieved_at, raw=raw, **values)
