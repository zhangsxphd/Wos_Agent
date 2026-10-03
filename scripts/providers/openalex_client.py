"""OpenAlex DOI batch lookup, with legacy singleton/cache compatibility."""

import json
from pathlib import Path
from urllib.parse import quote

import requests

from .base_client import BaseClient, ProviderError, canonical_doi, cache_key
from ..normalize_record import normalize_doi
from ..pipeline_utils import assert_safe, display_path, new_run_id, utc_now, write_json


RATE_LIMIT_HEADERS = ("X-RateLimit-Limit", "X-RateLimit-Remaining",
                      "X-RateLimit-Credits-Used", "X-RateLimit-Reset")
SELECT_FIELDS = "id,doi,title,publication_year,abstract_inverted_index"


def reconstruct_abstract(index):
    if not index:
        return None
    if not isinstance(index, dict):
        raise ValueError("Invalid inverted index")
    positions = {}
    for word, offsets in index.items():
        if not isinstance(word, str) or not word or not isinstance(offsets, list):
            raise ValueError("Invalid inverted index")
        for offset in offsets:
            if type(offset) is not int or offset < 0 or offset in positions:
                raise ValueError("Duplicate/invalid abstract position")
            positions[offset] = word
    if not positions:
        return None
    if sorted(positions) != list(range(len(positions))):
        raise ValueError("Incomplete inverted index; refusing to invent missing words")
    return " ".join(positions[position] for position in range(len(positions)))


class OpenAlexClient(BaseClient):
    provider = "openalex"
    credential_name = "OPENALEX_API_KEY"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stats["no_abstract"] = 0
        self.rate_limit_history = []
        self.lookup_metadata = {}

    def configure_auth(self):
        if self.api_key:
            # Officially supported header avoids query-string credential exposure.
            self.session.headers.update({"Authorization": "Bearer " + self.api_key})

    def request_spec(self, doi):
        # Existing get_by_doi callers retain their singleton interface. The
        # enrichment entry point uses get_many_by_doi by default, before mapping.
        return "https://api.openalex.org/works/" + quote("https://doi.org/" + doi, safe=""), {}

    def batch_request_spec(self, dois):
        if not 1 <= len(dois) <= 100:
            raise ValueError("OpenAlex batch must contain 1 to 100 DOI values")
        return "https://api.openalex.org/works", {
            "filter": "doi:" + "|".join("https://doi.org/" + canonical_doi(doi) for doi in dois),
            "per_page": 100, "select": SELECT_FIELDS,
        }

    def _cache_path(self, doi):
        return self.cache_dir / (cache_key(doi) + ".json")

    def _record_error(self, error):
        self.stats["errors"] += 1
        self.error_counts[error.code] = self.error_counts.get(error.code, 0) + 1

    def _load_cached(self, doi):
        path = self._cache_path(doi)
        entry = json.loads(path.read_text(encoding="utf-8"))
        assert_safe(entry, self.secrets)
        if (entry.get("cache_version") != 1 or entry.get("provider") != self.provider
                or entry.get("lookup_doi") != doi or entry.get("status") not in {"ok", "no_abstract", "not_found"}):
            raise ProviderError(self.provider, "invalid_cache")
        self.stats["cache_hits"] += 1
        if entry["status"] == "not_found":
            self.stats["not_found"] += 1
            value, status = None, "not_found"
        else:
            value = self.parse(entry["raw"], entry["retrieved_at"])
            if value is None or not value.get("provider_id"):
                raise ProviderError(self.provider, "invalid_cache_record")
            self._validate_result(value)
            status = "found" if value.get("abstract") else "no_abstract"
            if status == "no_abstract":
                self.stats["no_abstract"] += 1
        self.lookup_metadata[doi] = {"cache_hit": True, "cache_file": str(path), "status": status,
                                     "rate_limit_headers": entry.get("rate_limit_headers", {})}
        return value

    def _get_by_doi(self, doi):
        # Also read new no_abstract entries through the v0.3 singleton API.
        if self._cache_path(doi).is_file() and not self.refresh:
            value = self._load_cached(doi)
            self.last_lookup = self.lookup_metadata[doi]
            return value
        value = super()._get_by_doi(doi)
        status = "not_found" if value is None else "found" if value.get("abstract") else "no_abstract"
        self.last_lookup["status"] = status
        if status == "no_abstract":
            self.stats["no_abstract"] += 1
            path = self._cache_path(doi)
            entry = json.loads(path.read_text(encoding="utf-8"))
            entry["status"] = "no_abstract"
            write_json(path, entry, self.secrets, overwrite=True)
        return value

    def _validate_result(self, value):
        assert_safe(value, self.secrets)
        if any(value.get(field) is not None and not isinstance(value[field], str)
               for field in ("provider_id", "doi", "title", "abstract", "url")):
            raise ProviderError(self.provider, "invalid_schema")

    def _request_batch(self, dois):
        if self.clock() < self.blocked_until:
            raise ProviderError(self.provider, "provider_cooldown", 429)
        url, params = self.batch_request_spec(dois)
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
                # Only this explicit response-header whitelist is retained.
                headers = {name: response.headers.get(name) for name in RATE_LIMIT_HEADERS}
                assert_safe(headers, self.secrets)
                self.rate_limit_history.append({"request_number": self.stats["requests"],
                                                "doi_count": len(dois), "http_status": status,
                                                "headers": headers})
                if status in {200, 404}:
                    try:
                        raw = response.json()
                    except ValueError:
                        if status == 404:
                            raw = None
                        else:
                            raise ProviderError(self.provider, "invalid_json", status) from None
                    assert_safe(raw, self.secrets)
                    return status, raw, headers
                if status != 429 and not 500 <= status < 600:
                    raise ProviderError(self.provider, "http_error", status)
                if attempt == self.retries:
                    raise ProviderError(self.provider, "rate_limited" if status == 429 else "server_error", status)
            self.sleep(self._retry_delay(response, attempt))
        raise ProviderError(self.provider, "request_error")

    def _fetch_batch(self, dois):
        status, raw, headers = self._request_batch(dois)
        retrieved_at = utc_now()
        results = {} if status == 404 else self._map_response(raw, dois)
        archive = self.cache_dir / "batches" / (new_run_id("doi_batch") + ".json")
        write_json(archive, {"provider": self.provider, "lookup_dois": dois, "http_status": status,
                             "retrieved_at": retrieved_at, "rate_limit_headers": headers, "raw": raw}, self.secrets)
        values = {}
        for doi in dois:
            path = self._cache_path(doi)
            item = results.get(doi)
            if isinstance(item, ProviderError):
                values[doi] = item
                self._record_error(item)
                self.lookup_metadata[doi] = {"cache_hit": False, "status": "error"}
                continue
            value = self.parse(item, retrieved_at) if item is not None else None
            if value is not None:
                self._validate_result(value)
            cache_status = "not_found" if value is None else "ok" if value.get("abstract") else "no_abstract"
            entry = {"cache_version": 1, "provider": self.provider, "lookup_doi": doi,
                     "status": cache_status, "http_status": status, "retrieved_at": retrieved_at,
                     "raw": item if status == 200 else raw, "lookup_mode": "batch",
                     "batch_response_file": display_path(archive, self.root), "rate_limit_headers": headers,
                     "not_found_evidence": "HTTP 404" if status == 404 else "Absent from complete exact DOI-filter results" if value is None else None}
            write_json(path, entry, self.secrets, overwrite=path.exists())
            if value is None:
                self.stats["not_found"] += 1
            elif not value.get("abstract"):
                self.stats["no_abstract"] += 1
            self.lookup_metadata[doi] = {"cache_hit": False, "cache_file": str(path),
                                         "status": "not_found" if value is None else "found" if value.get("abstract") else "no_abstract",
                                         "rate_limit_headers": headers}
            values[doi] = value
        return values

    def _map_response(self, raw, dois):
        if not isinstance(raw, dict) or not isinstance(raw.get("results"), list):
            raise ProviderError(self.provider, "invalid_batch_response")
        count = (raw.get("meta") or {}).get("count")
        complete = (type(count) is int and 0 <= count <= len(raw["results"])) if count is not None else len(raw["results"]) < 100
        requested, mapped = set(dois), {}
        for item in raw["results"]:
            if not isinstance(item, dict) or not item.get("id"):
                raise ProviderError(self.provider, "invalid_batch_record")
            try:
                doi = canonical_doi(item.get("doi"))
            except ValueError:
                raise ProviderError(self.provider, "invalid_batch_doi") from None
            if doi not in requested:
                raise ProviderError(self.provider, "unexpected_batch_doi")
            if doi in mapped and mapped[doi] != item:
                mapped[doi] = ProviderError(self.provider, "ambiguous_doi")
            else:
                mapped[doi] = item
        if not complete:
            # Do not turn pagination/truncation ambiguity into negative cache.
            for doi in dois:
                if doi not in mapped:
                    mapped[doi] = ProviderError(self.provider, "incomplete_batch_response")
        return mapped

    def get_many_by_doi(self, dois, refresh=None):
        """Return normalized DOI -> unified record, None, or retryable ProviderError.

        All cache hits are removed before forming batches. Each uncached unique
        DOI is requested once, except explicit HTTP retries, in chunks <= 100.
        """
        normalized = list(dict.fromkeys(canonical_doi(doi) for doi in dois))
        refresh = self.refresh if refresh is None else refresh
        if refresh and self.cache_only:
            raise ValueError("Cannot refresh OpenAlex in cache-only mode")
        values, missing = {}, []
        self.lookup_metadata = {}
        for doi in normalized:
            try:
                if self._cache_path(doi).is_file() and not refresh:
                    values[doi] = self._load_cached(doi)
                elif self.cache_only:
                    raise ProviderError(self.provider, "cache_miss")
                else:
                    missing.append(doi)
            except (ProviderError, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError(self.provider, "invalid_data_or_cache")
                values[doi] = error
                self.lookup_metadata[doi] = {"cache_hit": False, "status": "error"}
                self._record_error(error)
        for start in range(0, len(missing), 100):
            batch = missing[start:start + 100]
            try:
                values.update(self._fetch_batch(batch))
            except (ProviderError, ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
                error = exc if isinstance(exc, ProviderError) else ProviderError(self.provider, "invalid_data_or_cache")
                for doi in batch:
                    values[doi] = error
                    self.lookup_metadata[doi] = {"cache_hit": False, "status": "error"}
                    self._record_error(error)
        return {doi: values[doi] for doi in normalized}

    def parse(self, raw, retrieved_at):
        if not raw:
            return None
        warning = None
        try:
            abstract = reconstruct_abstract(raw.get("abstract_inverted_index"))
        except ValueError:
            abstract, warning = None, "invalid_inverted_index"
        result = self.unified(raw, retrieved_at, provider_id=raw.get("id"),
                              doi=normalize_doi(raw.get("doi")), title=raw.get("title") or raw.get("display_name"),
                              year=raw.get("publication_year"), abstract=abstract, url=raw.get("id"))
        if warning:
            result["parse_warning"] = warning
        return result
