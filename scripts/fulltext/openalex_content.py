"""Supplementary Work lookup + bounded downloads with host-scoped credentials."""
import hashlib
import json
import re
import time
from pathlib import Path
from urllib.parse import quote, urlsplit, urljoin

import requests
from dotenv import dotenv_values

from .provenance import FulltextError, safe_url
from ..pipeline_utils import ROOT, assert_safe, known_secrets, utc_now, write_json
from ..providers.base_client import canonical_doi

WORK_FIELDS = "id,doi,title,publication_year,type,open_access,best_oa_location,locations,has_content,content_urls"
RATE_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Credits-Used", "X-RateLimit-Reset")


def work_identifier(value):
    if not isinstance(value, str):
        raise ValueError("DOI or OpenAlex Work ID required")
    cleaned=value.strip().rstrip('/')
    if re.fullmatch(r"W\d+",cleaned,re.IGNORECASE): return cleaned.upper()
    if re.fullmatch(r'https://openalex\.org/W\d+',cleaned,re.IGNORECASE): return cleaned.rsplit('/',1)[-1].upper()
    return "https://doi.org/" + canonical_doi(value)


class OpenAlexContentClient:
    def __init__(self, root=ROOT, session=None, timeout=25, retries=1, min_interval=1,
                 sleep=time.sleep, clock=time.monotonic, cache_only=False, max_bytes=30_000_000):
        self.root, self.session = Path(root), session or requests.Session()
        self.api_key = dotenv_values(self.root / ".env").get("OPENALEX_API_KEY")
        self.secrets = known_secrets(self.root, extra=(self.api_key,))
        self.timeout, self.retries, self.min_interval = timeout, retries, min_interval
        self.sleep, self.clock, self.last_request = sleep, clock, None
        self.cache_only, self.max_bytes = cache_only, max_bytes
        self.stats = {"requests": 0, "work_cache_hits": 0, "download_requests": 0}
        self.rate_limit_history = []

    def get(self, url, params=None, content=False):
        if self.cache_only:
            raise FulltextError("cache_miss")
        original = safe_url(url, self.secrets)
        params = params or {}
        assert_safe(params, self.secrets)
        current, redirects = original, 0
        for attempt in range(self.retries + 1):
            while True:
                if self.last_request is not None:
                    self.sleep(max(0, self.min_interval - (self.clock() - self.last_request)))
                host = urlsplit(current).hostname
                headers = {"User-Agent": "wos-research-agent/0.5 (OA-only resolver)", "Accept": "*/*" if content else "application/json"}
                # Fresh per-request headers; third-party hosts never receive Key.
                if host in {"api.openalex.org", "content.openalex.org"} and self.api_key:
                    headers["Authorization"] = "Bearer " + self.api_key
                self.stats["requests"] += 1
                if content: self.stats["download_requests"] += 1
                response = None
                try:
                    response = self.session.get(current, params=params if current == original else {}, headers=headers,
                                                timeout=self.timeout, allow_redirects=False, stream=content)
                except (requests.Timeout, requests.ConnectionError):
                    code = "transient_network_error"
                except requests.RequestException:
                    raise FulltextError("request_error") from None
                finally:
                    self.last_request = self.clock()
                if response is None:
                    break
                status = response.status_code
                if host in {"api.openalex.org", "content.openalex.org"}:
                    rates = {k: response.headers.get(k) for k in RATE_HEADERS}
                    assert_safe(rates, self.secrets)
                    self.rate_limit_history.append({"host": host, "status": status, "headers": rates})
                if status in {301,302,303,307,308}:
                    if redirects >= 5:
                        response.close()
                        raise FulltextError("too_many_redirects", status)
                    target = safe_url(urljoin(current, response.headers.get("Location", "")), self.secrets)
                    response.close()
                    redirects += 1
                    current = target
                    continue
                if status == 429 or 500 <= status < 600:
                    code = "transient_http_error"
                    retry_after = response.headers.get("Retry-After", "0")
                    response.close()
                    try: delay = max(2 ** attempt, float(retry_after))
                    except (ValueError, TypeError): delay = 2 ** attempt
                    if delay > 60: raise FulltextError("retry_later", status)
                    break
                if status != 200:
                    response.close()
                    raise FulltextError("not_found" if status == 404 else "access_denied" if status in {401,403} else "http_error", status)
                try:
                    if content:
                        chunks, size = [], 0
                        for chunk in response.iter_content(65536):
                            size += len(chunk)
                            if size > self.max_bytes: raise FulltextError("content_too_large")
                            chunks.append(chunk)
                        return b"".join(chunks), safe_url(current, self.secrets)
                    data = response.json()
                    assert_safe(data, self.secrets)
                    return data, safe_url(current, self.secrets)
                except ValueError:
                    raise FulltextError("invalid_or_unsafe_response") from None
                finally:
                    response.close()
            if attempt == self.retries:
                raise FulltextError(code, getattr(response, "status_code", None))
            self.sleep(delay if response is not None else 2 ** attempt)
        raise FulltextError("request_error")

    def lookup(self, value, refresh=False):
        identifier = work_identifier(value)
        key = hashlib.sha256(identifier.encode()).hexdigest()
        path = self.root / "data/cache/openalex_work" / (key + ".json")
        if path.exists() and not refresh:
            entry = json.loads(path.read_text())
            assert_safe(entry, self.secrets)
            if entry.get("identifier") != identifier: raise FulltextError("invalid_work_cache")
            self.stats["work_cache_hits"] += 1
            return entry["work"]
        try:
            raw, _ = self.get("https://api.openalex.org/works/" + quote(identifier, safe=""), {"select": WORK_FIELDS})
        except FulltextError as exc:
            if exc.code != "not_found": raise
            raw = None
        if raw is not None:
            if not isinstance(raw, dict) or not re.fullmatch(r"https://openalex.org/W\d+", raw.get("id", "")):
                raise FulltextError("invalid_work_response")
            if identifier.startswith("https://doi.org/") and canonical_doi(raw.get("doi")) != canonical_doi(identifier):
                raise FulltextError("doi_mismatch")
            if identifier.startswith("W") and raw["id"].rsplit("/",1)[-1] != identifier:
                raise FulltextError("work_id_mismatch")
        write_json(path, {"identifier": identifier, "retrieved_at": utc_now(), "work": raw}, self.secrets, overwrite=path.exists())
        return raw

    def close(self):
        self.session.close()


def location_metadata(work, location=None):
    location = location or work.get("best_oa_location") or {}
    oa = work.get("open_access") or {}
    return {"is_oa": oa.get("is_oa"), "oa_status": oa.get("oa_status"), "license": location.get("license"),
            "version": location.get("version"), "host_type": (location.get("source") or {}).get("type"),
            "landing_page_url": location.get("landing_page_url"), "pdf_url": location.get("pdf_url"),
            "openalex_content_url": work.get("content_urls") or {}}
