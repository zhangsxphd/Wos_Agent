"""Safe URLs, byte-level provenance and immutable content-addressed storage."""
import hashlib
import ipaddress
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from ..pipeline_utils import assert_safe, redact, utc_now


class FulltextError(RuntimeError):
    def __init__(self, code, status=None):
        self.code, self.http_status = code, status
        super().__init__(code)


def safe_url(url, secrets=()):
    if not isinstance(url, str):
        raise FulltextError("invalid_url")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise FulltextError("non_public_url")
    host = parsed.hostname.lower()
    if host in {"localhost", "metadata.google.internal"} or host.endswith((".local", ".internal")):
        raise FulltextError("non_public_url")
    try:
        if not ipaddress.ip_address(host).is_global:
            raise FulltextError("non_public_url")
    except ValueError:
        pass
    if any(x in host for x in ("sci-hub", "scihub", "libgen", "annas-archive")):
        raise FulltextError("disallowed_source")
    params = [(k,v) for k,v in parse_qsl(parsed.query, keep_blank_values=True)
              if k.lower() not in {"api_key", "apikey", "access_token", "token", "authorization"}]
    result = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(params), ""))
    assert_safe(result, secrets)
    return result


def save_raw(raw, directory, fmt, secrets=()):
    for secret in secrets:
        if secret.encode() in raw:
            raise FulltextError("credential_in_response")
    digest = hashlib.sha256(raw).hexdigest()
    path = Path(directory) / (digest + (".tei.xml" if fmt == "tei_xml" else ".pdf"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != raw:
            raise FulltextError("raw_hash_mismatch")
    else:
        path.write_bytes(raw)
    return path, digest


def manifest(record, status, **values):
    result = {"uid": record.get("uid"), "doi": record.get("doi"), "source": None,
              "format": None, "version": None, "license": None, "retrieved_at": utc_now(),
              "source_url": None, "sha256": None, "status": status}
    result.update(values)
    return result

