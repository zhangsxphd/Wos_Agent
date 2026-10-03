"""Route full-text acquisition through public OA first, then entitled Elsevier API access."""
import hashlib
import json
from pathlib import Path

from .elsevier_content import ElsevierArticleClient
from .provenance import FulltextError, manifest
from ..pipeline_utils import ROOT, assert_safe, known_secrets, new_run_id, write_json


def _write_raw(path, raw, secrets):
    if any(secret.encode() in raw for secret in secrets):
        raise FulltextError("credential_in_response")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise FulltextError("raw_hash_mismatch")
    else:
        path.write_bytes(raw)


class AcquisitionRouter:
    """Preserve v0.5 OA behavior and use Elsevier only as an entitlement fallback."""

    def __init__(self, root=ROOT, oa_resolver=None, elsevier_client=None):
        self.root = Path(root)
        self.oa_resolver = oa_resolver
        self.elsevier = elsevier_client or ElsevierArticleClient(root=root)
        self.secrets = known_secrets(
            self.root, extra=(self.elsevier.api_key, self.elsevier.insttoken)
        )

    def resolve(self, record, work=None, refresh=False):
        attempts = []
        oa_result = None
        if self.oa_resolver is not None:
            oa_result = self.oa_resolver.resolve(record, work=work, refresh=refresh)
            attempts.append({
                "route": "openalex_oa",
                "status": oa_result.get("status"),
                "source": oa_result.get("source"),
                "format": oa_result.get("format"),
                "reason": oa_result.get("reason"),
            })
            if oa_result.get("status") == "available":
                result = dict(oa_result)
                result["acquisition_route"] = "openalex_oa"
                result["router_attempts"] = attempts
                return result

        doi = record.get("doi")
        if not doi:
            return self._fallback(record, oa_result, attempts, "missing_doi")

        if not self.elsevier.configured:
            attempts.append({"route": "elsevier_api", "status": "skipped", "reason": "missing_api_key"})
            return self._fallback(record, oa_result, attempts, "elsevier_not_configured")

        try:
            raw, meta = self.elsevier.retrieve_xml(doi)
            parsed = meta.pop("parsed")
            digest = hashlib.sha256(raw).hexdigest()
            raw_path = self.root / "data/fulltext/raw" / (digest + ".elsevier.xml")
            parsed_path = self.root / "data/fulltext/parsed" / (digest + ".elsevier-xml-v1.json")
            _write_raw(raw_path, raw, self.secrets)
            if not parsed_path.exists():
                write_json(parsed_path, parsed, self.secrets)
            attempts.append({
                "route": "elsevier_api",
                "status": "available",
                "format": "elsevier_xml",
                "http_status": meta["http_status"],
            })
            result = manifest(
                record,
                "available",
                source="elsevier_api",
                format="elsevier_xml",
                source_url=meta["source_url"],
                final_url=meta["source_url"],
                sha256=digest,
                raw_file=str(raw_path.relative_to(self.root)),
                parsed_file=str(parsed_path.relative_to(self.root)),
                parse_status="parsed",
                entitlement="FULL",
                acquisition_route="elsevier_api",
                router_attempts=attempts,
            )
            return self._save(result, record)
        except FulltextError as exc:
            attempts.append({
                "route": "elsevier_api",
                "status": "error",
                "error_code": exc.code,
                "http_status": exc.http_status,
            })
            hard_error = exc.code in {
                "elsevier_network_error",
                "elsevier_request_error",
                "elsevier_rate_limited",
                "elsevier_retry_later",
                "elsevier_server_error",
            }
            result = self._fallback(
                record,
                oa_result,
                attempts,
                "all_routes_failed" if hard_error else "no_entitled_fulltext",
            )
            if oa_result is None:
                result["status"] = "error" if hard_error else "unavailable"
            return result

    def _fallback(self, record, oa_result, attempts, reason):
        if oa_result is not None:
            result = dict(oa_result)
            result["router_attempts"] = attempts
            result["acquisition_route"] = None
            if result.get("status") == "available":
                result["acquisition_route"] = "openalex_oa"
            result["router_reason"] = reason
            return result
        return manifest(
            record,
            "unavailable",
            reason=reason,
            acquisition_route=None,
            router_attempts=attempts,
        )

    def _save(self, result, record):
        identity = record.get("doi") or record.get("uid") or "unknown"
        key = hashlib.sha256(str(identity).lower().encode()).hexdigest()
        target = self.root / "data/fulltext/manifests" / key / (new_run_id("publisher") + ".json")
        result["manifest_file"] = str(target.relative_to(self.root))
        assert_safe(result, self.secrets)
        write_json(target, result, self.secrets)
        return result

    def close(self):
        self.elsevier.close()
