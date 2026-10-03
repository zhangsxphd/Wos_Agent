"""Elsevier Article Retrieval API client for entitled non-commercial research access."""
import os
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import dotenv_values

from .provenance import FulltextError, safe_url
from .elsevier_xml import parse_elsevier_xml
from ..pipeline_utils import ROOT, assert_safe, known_secrets
from ..providers.base_client import canonical_doi

RATE_HEADERS = (
    "X-RateLimit-Limit",
    "X-RateLimit-Remaining",
    "X-RateLimit-Credits-Used",
    "X-RateLimit-Reset",
)


class ElsevierArticleClient:
    """Retrieve FULL-view article XML by DOI using an Elsevier API key.

    Institutional entitlement may be resolved by Elsevier from the caller IP.
    X-ELS-Insttoken is supported when an institution explicitly supplies one.
    """

    def __init__(
        self,
        root=ROOT,
        session=None,
        timeout=30,
        retries=2,
        min_interval=1.0,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.root = Path(root)
        config = dotenv_values(self.root / ".env")
        self.api_key = os.getenv("ELSEVIER_API_KEY", config.get("ELSEVIER_API_KEY"))
        self.insttoken = os.getenv("ELSEVIER_INSTTOKEN", config.get("ELSEVIER_INSTTOKEN"))
        self.secrets = known_secrets(self.root, extra=(self.api_key, self.insttoken))
        self.session = session or requests.Session()
        self.timeout = timeout
        self.retries = retries
        self.min_interval = min_interval
        self.sleep = sleep
        self.clock = clock
        self.last_request_end = None
        self.stats = {
            "requests": 0,
            "retrieved": 0,
            "not_found": 0,
            "access_denied": 0,
            "rate_limited": 0,
            "errors": 0,
        }
        self.rate_limit_history = []

    @property
    def configured(self):
        return bool(self.api_key)

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
            raise FulltextError("elsevier_retry_later", getattr(response, "status_code", None))
        return max(0, delay)

    def retrieve_xml(self, doi):
        if not self.api_key:
            raise FulltextError("missing_elsevier_api_key")
        doi = canonical_doi(doi)
        # Elsevier documents the formatted DOI directly after /doi/.
        url = "https://api.elsevier.com/content/article/doi/" + quote(doi, safe="/:;()")
        safe_url(url, self.secrets)
        params = {"view": "FULL"}
        assert_safe(params, self.secrets)
        headers = {
            "X-ELS-APIKey": self.api_key,
            "Accept": "text/xml",
            "User-Agent": "Wos_Agent/0.5.5 (non-commercial research full-text resolver)",
        }
        if self.insttoken:
            headers["X-ELS-Insttoken"] = self.insttoken

        for attempt in range(self.retries + 1):
            if self.last_request_end is not None:
                self.sleep(max(0, self.min_interval - (self.clock() - self.last_request_end)))
            response = None
            self.stats["requests"] += 1
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.retries:
                    self.stats["errors"] += 1
                    raise FulltextError("elsevier_network_error") from None
                self.sleep(2 ** attempt)
                continue
            except requests.RequestException:
                if attempt < self.retries:
                    self.sleep(2 ** attempt)
                    continue
                self.stats["errors"] += 1
                raise FulltextError("elsevier_request_error") from None
            finally:
                self.last_request_end = self.clock()

            status = response.status_code
            rates = {k: response.headers.get(k) for k in RATE_HEADERS}
            assert_safe(rates, self.secrets)
            self.rate_limit_history.append({"status": status, "headers": rates})

            if status == 200:
                raw = response.content
                response.close()
                if any(secret.encode() in raw for secret in self.secrets):
                    self.stats["errors"] += 1
                    raise FulltextError("credential_in_response")
                # FULL is requested explicitly; reject metadata/abstract-only responses.
                parsed = parse_elsevier_xml(raw)
                self.stats["retrieved"] += 1
                return raw, {
                    "doi": doi,
                    "source_url": safe_url(url, self.secrets),
                    "http_status": 200,
                    "parsed": parsed,
                }

            response.close()
            if status == 404:
                self.stats["not_found"] += 1
                raise FulltextError("elsevier_not_found", status)
            if status in {401, 403}:
                self.stats["access_denied"] += 1
                raise FulltextError("elsevier_access_denied", status)
            if status == 429:
                self.stats["rate_limited"] += 1
                if attempt == self.retries:
                    raise FulltextError("elsevier_rate_limited", status)
                self.sleep(self._retry_delay(response, attempt))
                continue
            if 500 <= status < 600:
                if attempt == self.retries:
                    self.stats["errors"] += 1
                    raise FulltextError("elsevier_server_error", status)
                self.sleep(self._retry_delay(response, attempt))
                continue

            self.stats["errors"] += 1
            raise FulltextError("elsevier_http_error", status)

        raise FulltextError("elsevier_request_error")

    def close(self):
        self.session.close()
