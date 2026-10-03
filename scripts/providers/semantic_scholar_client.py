"""Authenticated Semantic Scholar DOI lookup with a serial one-RPS batch path."""

import requests
from urllib.parse import quote

from .base_client import BaseClient, ProviderError, canonical_doi, cache_key
from ..normalize_record import normalize_doi
from ..pipeline_utils import assert_safe, new_run_id, utc_now, write_json


SINGLE_FIELDS = "paperId,title,year,abstract,externalIds,url"
BATCH_FIELDS = "paperId,title,year,abstract,externalIds,url,isOpenAccess,openAccessPdf"
BATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/batch"


class SemanticScholarClient(BaseClient):
    provider = "semantic_scholar"
    credential_name = "SEMANTIC_SCHOLAR_API_KEY"

    def __init__(self, *args, chunk_size=100, max_batch_size=500, **kwargs):
        if not 1 <= chunk_size <= 500 or max_batch_size != 500:
            raise ValueError("Semantic Scholar batch size must be 1..500")
        kwargs["min_interval"] = max(float(kwargs.get("min_interval", 1.10)), 1.05)
        super().__init__(*args, **kwargs)
        self.chunk_size = chunk_size
        self.max_batch_size = max_batch_size
        self.stats.update({"batches": 0, "found": 0, "no_abstract": 0,
                           "rate_limited": 0, "transient_errors": 0})
        self.lookup_metadata = {}
        self.rate_limit_history = []

    def configure_auth(self):
        if self.api_key:
            self.session.headers.update({"x-api-key": self.api_key})

    def request_spec(self, doi):
        return ("https://api.semanticscholar.org/graph/v1/paper/" + quote("DOI:" + doi, safe=""),
                {"fields": SINGLE_FIELDS})

    def parse(self, raw, retrieved_at):
        if not isinstance(raw, dict) or not raw:
            return None
        external = raw.get("externalIds") or {}
        return self.unified(raw, retrieved_at, provider_id=raw.get("paperId"),
                            doi=normalize_doi(external.get("DOI")), title=raw.get("title"),
                            year=raw.get("year"), abstract=raw.get("abstract") or None,
                            url=raw.get("url"), is_open_access=raw.get("isOpenAccess"),
                            open_access_pdf=raw.get("openAccessPdf"))

    def _cache_path(self, doi):
        return self.cache_dir / (cache_key(doi) + ".json")

    def _validate_result(self, value):
        assert_safe(value, self.secrets)
        if any(value.get(field) is not None and not isinstance(value[field], str)
               for field in ("provider_id", "doi", "title", "abstract", "url")):
            raise ProviderError(self.provider, "invalid_schema")
        try:
            canonical = canonical_doi(value.get("doi"))
        except (ValueError, TypeError):
            raise ProviderError(self.provider, "doi_mismatch") from None
        return canonical

    def _write_cache(self, doi, status, raw, retrieved_at, http_status=200):
        path = self._cache_path(doi)
        entry = {"cache_version": 2, "provider": self.provider, "lookup_doi": doi,
                 "retrieved_at": retrieved_at, "http_status": http_status,
                 "status": status, "raw": raw}
        assert_safe(entry, self.secrets)
        write_json(path, entry, self.secrets, overwrite=self.refresh and path.exists())
        return path

    def _record_error(self, error):
        self.stats["errors"] += 1
        self.error_counts[error.code] = self.error_counts.get(error.code, 0) + 1

    def _load_cache(self, doi):
        path = self._cache_path(doi)
        import json
        entry = json.loads(path.read_text(encoding="utf-8"))
        assert_safe(entry, self.secrets)
        if (entry.get("cache_version") != 2 or entry.get("provider") != self.provider
                or entry.get("lookup_doi") != doi
                or entry.get("status") not in {"success", "ok", "no_abstract", "not_found"}):
            raise ProviderError(self.provider, "invalid_cache")
        status = entry["status"]
        if status == "not_found":
            value = None
            self.stats["not_found"] += 1
        else:
            value = self.parse(entry.get("raw"), entry.get("retrieved_at"))
            if value is None or self._validate_result(value) != doi:
                raise ProviderError(self.provider, "doi_mismatch")
            self.stats["found"] += 1
            if not value.get("abstract"):
                self.stats["no_abstract"] += 1
        self.stats["cache_hits"] += 1
        metadata = {"cache_hit": True, "cache_file": str(path), "status": status}
        self.lookup_metadata[doi] = metadata
        self.last_lookup = metadata
        return value

    def _get_by_doi(self, doi):
        path = self._cache_path(doi)
        if path.is_file() and not self.refresh:
            try:
                return self._load_cache(doi)
            except ProviderError as exc:
                if exc.code != "invalid_cache":
                    raise
        if self.cache_only:
            raise ProviderError(self.provider, "cache_miss")
        status, raw = self._request(doi)
        retrieved_at = utc_now()
        if status == 404:
            self._write_cache(doi, "not_found", raw, retrieved_at, status)
            self.stats["not_found"] += 1
            self.last_lookup = {"cache_hit": False, "cache_file": str(path), "status": "not_found"}
            return None
        value = self.parse(raw, retrieved_at)
        if value is None:
            self._write_cache(doi, "not_found", raw, retrieved_at, status)
            self.stats["not_found"] += 1
            self.last_lookup = {"cache_hit": False, "cache_file": str(path), "status": "not_found"}
            return None
        try:
            if self._validate_result(value) != doi:
                raise ProviderError(self.provider, "doi_mismatch", status)
        except ProviderError as exc:
            if exc.code == "invalid_schema":
                self._write_cache(doi, "error", raw, retrieved_at, status)
            raise
        cache_status = "success" if value.get("abstract") else "no_abstract"
        self._write_cache(doi, cache_status, raw, retrieved_at, status)
        self.stats["found"] += 1
        if cache_status == "no_abstract":
            self.stats["no_abstract"] += 1
        self.last_lookup = {"cache_hit": False, "cache_file": str(path), "status": cache_status}
        return value

    def get_many_by_doi(self, dois):
        normalized = list(dict.fromkeys(canonical_doi(doi) for doi in dois))
        values, missing = {}, []
        for doi in normalized:
            path = self._cache_path(doi)
            if path.is_file() and not self.refresh:
                try:
                    values[doi] = self._load_cache(doi)
                except ProviderError as exc:
                    if exc.code == "invalid_cache" and not self.cache_only:
                        missing.append(doi)
                    else:
                        values[doi] = exc
                        self._record_error(exc)
            elif self.cache_only:
                error = ProviderError(self.provider, "cache_miss")
                values[doi] = error
                self.lookup_metadata[doi] = {"cache_hit": False, "status": "error"}
                self._record_error(error)
            else:
                missing.append(doi)
        for start in range(0, len(missing), self.chunk_size):
            batch = missing[start:start + self.chunk_size]
            try:
                values.update(self._fetch_batch(batch))
            except (ProviderError, ValueError, TypeError, KeyError, OSError) as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError(self.provider, "invalid_data_or_cache")
                for doi in batch:
                    values[doi] = error
                    self.lookup_metadata[doi] = {"cache_hit": False, "status": "error"}
                    self._record_error(error)
        return {doi: values[doi] for doi in normalized}

    def _batch_request(self, dois):
        if not 1 <= len(dois) <= 500:
            raise ValueError("Semantic Scholar batch must contain 1..500 DOI values")
        params = {"fields": BATCH_FIELDS}
        body = {"ids": ["DOI:" + doi for doi in dois]}
        for attempt in range(self.retries + 1):
            if self.last_request_end is not None:
                self.sleep(max(0, self.min_interval - (self.clock() - self.last_request_end)))
            self.stats["requests"] += 1
            response = None
            try:
                response = self.session.post(BATCH_URL, params=params, json=body,
                                             timeout=self.timeout, allow_redirects=False)
            except (requests.Timeout, requests.ConnectionError):
                if attempt == self.retries:
                    self.stats["transient_errors"] += 1
                    raise ProviderError(self.provider, "network_error") from None
            except requests.RequestException:
                if attempt == self.retries:
                    self.stats["transient_errors"] += 1
                    raise ProviderError(self.provider, "request_error") from None
            finally:
                self.last_request_end = self.clock()
            if response is not None:
                status = response.status_code
                self.rate_limit_history.append({"request_number": self.stats["requests"],
                                                "http_status": status,
                                                "headers": {"Retry-After": response.headers.get("Retry-After")}})
                if status == 200:
                    try:
                        raw = response.json()
                    except ValueError:
                        raise ProviderError(self.provider, "invalid_json", status) from None
                    assert_safe(raw, self.secrets)
                    return raw, status
                if status == 429:
                    self.stats["rate_limited"] += 1
                    if attempt == self.retries:
                        raise ProviderError(self.provider, "rate_limited", status)
                elif 500 <= status < 600:
                    if attempt == self.retries:
                        self.stats["transient_errors"] += 1
                        raise ProviderError(self.provider, "server_error", status)
                else:
                    raise ProviderError(self.provider, "http_error", status)
            self.sleep(self._retry_delay(response, attempt))
        raise ProviderError(self.provider, "request_error")

    def _fetch_batch(self, dois):
        self.stats["batches"] += 1
        raw, status = self._batch_request(dois)
        retrieved_at = utc_now()
        write_json(self.cache_dir / "batches" / (new_run_id("doi_batch") + ".json"),
                   {"provider": self.provider, "lookup_dois": dois, "http_status": status,
                    "retrieved_at": retrieved_at, "raw": raw}, self.secrets)
        if not isinstance(raw, list):
            raise ProviderError(self.provider, "invalid_batch_response", status)
        values = {}
        for index, doi in enumerate(dois):
            item = raw[index] if index < len(raw) else None
            if item is None:
                self._write_cache(doi, "not_found", None, retrieved_at, status)
                self.stats["not_found"] += 1
                self.lookup_metadata[doi] = {"cache_hit": False, "cache_file": str(self._cache_path(doi)), "status": "not_found"}
                values[doi] = None
                continue
            if not isinstance(item, dict):
                error = ProviderError(self.provider, "invalid_batch_record", status)
                self._record_error(error)
                values[doi] = error
                continue
            try:
                returned = canonical_doi((item.get("externalIds") or {}).get("DOI"))
            except (ValueError, TypeError):
                returned = None
            if returned != doi:
                error = ProviderError(self.provider, "doi_mismatch", status)
                self._record_error(error)
                values[doi] = error
                continue
            value = self.parse(item, retrieved_at)
            if value is None or self._validate_result(value) != doi:
                error = ProviderError(self.provider, "doi_mismatch", status)
                self._record_error(error)
                values[doi] = error
                continue
            cache_status = "success" if value.get("abstract") else "no_abstract"
            path = self._write_cache(doi, cache_status, item, retrieved_at, status)
            self.stats["found"] += 1
            if cache_status == "no_abstract":
                self.stats["no_abstract"] += 1
            self.lookup_metadata[doi] = {"cache_hit": False, "cache_file": str(path), "status": cache_status}
            values[doi] = value
        return values
