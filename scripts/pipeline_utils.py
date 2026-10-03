"""Shared v0.2 file handling; never serialize known API credentials."""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from urllib.parse import quote, quote_plus

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parent.parent
SECRET_ENV_NAMES = ("WOS_STARTER_API_KEY", "SEMANTIC_SCHOLAR_API_KEY", "OPENALEX_API_KEY")


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def new_run_id(name):
    timestamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y%m%d_%H%M%S_%f")
    return f"{timestamp}_{name}_{uuid4().hex[:8]}"


def known_secrets(root=ROOT, extra=()):
    values = [os.getenv(name) for name in SECRET_ENV_NAMES]
    env_path = Path(root) / ".env"
    if env_path.is_file():
        local = dotenv_values(env_path)
        values.extend(local.get(name) for name in SECRET_ENV_NAMES)
    values.extend(extra)
    return tuple(dict.fromkeys(v.strip() for v in values if isinstance(v, str) and v.strip()))


def secret_patterns(secrets):
    return tuple(dict.fromkeys(pattern for secret in secrets for pattern in
                              (secret, quote(secret, safe=""), quote_plus(secret), json.dumps(secret)[1:-1]) if pattern))


def redact(text, secrets):
    for secret in sorted(secret_patterns(secrets), key=len, reverse=True):
        text = str(text).replace(secret, "[REDACTED]")
    return str(text)


def assert_safe(value, secrets):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    if any(secret in text for secret in secret_patterns(secrets)):
        raise ValueError("Refusing to write an API key to an output file")


def display_path(path, root=ROOT):
    path, root = Path(path).resolve(), Path(root).resolve()
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def project_path(path, root=ROOT):
    path = Path(path)
    return path if path.is_absolute() else Path(root) / path


def write_json(path, value, secrets=(), overwrite=False):
    assert_safe(value, secrets)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    if overwrite:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(path)
    else:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(text)


def write_jsonl(path, records, secrets=()):
    assert_safe(records, secrets)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path):
    records = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                raise ValueError(f"Invalid JSONL at {path}, line {line_number}") from None
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record must be an object at line {line_number}")
            records.append(record)
    return records
