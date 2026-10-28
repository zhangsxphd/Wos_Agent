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
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    signed = {"x-amz-security-token", "x-amz-signature", "x-amz-credential",
              "x-goog-signature", "x-goog-credential", "awsaccesskeyid", "signature", "sig"}
    if any(key.casefold() in signed for key, _ in parameters):
        # Signed URLs are temporary bearer credentials. Refuse the request
        # rather than persist them or strip fields needed for authentication.
        raise FulltextError("signed_url_not_persisted")
    params = [(k,v) for k,v in parameters
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
    try:
        with path.open('xb') as stream: stream.write(raw)
    except FileExistsError:
        if path.read_bytes() != raw: raise FulltextError("raw_hash_mismatch")
    return path, digest


def save_parsed(parsed, directory, raw_sha256, secrets=()):
    """Store a new immutable parser result; never overwrite older parse files."""
    import json
    assert_safe(parsed, secrets)
    content=(json.dumps(parsed,indent=2,ensure_ascii=False)+'\n').encode('utf-8')
    digest=hashlib.sha256(content).hexdigest()
    path=Path(directory)/(raw_sha256+'.'+parsed['parser']+'.'+digest+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        with path.open('xb') as stream: stream.write(content)
    except FileExistsError:
        if path.read_bytes()!=content: raise FulltextError('parsed_hash_mismatch')
    return path,digest


def manifest(record, status, **values):
    result = {"uid": record.get("uid"), "doi": record.get("doi"), "source": None,
              "format": None, "version": None, "license": None, "retrieved_at": utc_now(),
              "source_url": None, "sha256": None, "status": status}
    result.update(values)
    return result

