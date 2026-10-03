"""Validated extraction cache keyed by input, schema and prompt content."""

import hashlib
import json
from pathlib import Path

from .base import BaseExtractor, ExtractorError, payload_key
from .schema_validator import EvidenceValidator, SCHEMA_PATH
from ..pipeline_utils import ROOT, assert_safe, known_secrets, utc_now, write_json


PROMPT_PATH = ROOT / "prompts/evidence_extraction.md"


class CachedExtractor(BaseExtractor):
    name = "cached"

    def __init__(self, fallback=None, root=ROOT, refresh=False, cache_dir=None,
                 schema_path=SCHEMA_PATH, prompt_path=PROMPT_PATH):
        self.root, self.fallback, self.refresh = Path(root), fallback, refresh
        self.cache_dir = Path(cache_dir) if cache_dir else self.root / "data/cache/evidence"
        self.secrets, self.validator = known_secrets(self.root), EvidenceValidator(schema_path)
        self.schema_sha256 = hashlib.sha256(Path(schema_path).read_bytes()).hexdigest()
        self.prompt_sha256 = hashlib.sha256(Path(prompt_path).read_bytes()).hexdigest()
        self.stats = {"cache_hits": 0, "extractions": 0}
        self.last_raw_response, self.last_metadata = None, {}

    def cache_key(self, record):
        return hashlib.sha256((payload_key(record) + self.schema_sha256 + self.prompt_sha256).encode()).hexdigest()

    def extract(self, record):
        self.last_raw_response = None
        key = self.cache_key(record)
        path = self.cache_dir / (key + ".json")
        self.last_metadata = {"cache_hit": False, "cache_key": key, "cache_file": str(path),
                              "payload_sha256": payload_key(record), "schema_sha256": self.schema_sha256,
                              "prompt_sha256": self.prompt_sha256}
        if path.is_file() and not self.refresh:
            try:
                entry = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                raise ExtractorError("extraction_cache_invalid_json") from None
            assert_safe(entry, self.secrets)
            if entry.get("cache_version") != 1 or entry.get("cache_key") != key:
                raise ExtractorError("extraction_cache_mismatch")
            raw = entry["raw_response"]
            self.last_raw_response = raw
            self.validator.validate(raw, record)
            self.stats["cache_hits"] += 1
            self.last_metadata.update(cache_hit=True, extractor=entry["extractor"], retrieved_at=entry["retrieved_at"])
        else:
            if self.fallback is None:
                raise ExtractorError("extraction_cache_miss")
            raw = self.fallback.extract(record)
            self.last_raw_response = raw
            assert_safe(raw, self.secrets)
            self.validator.validate(raw, record)
            self.stats["extractions"] += 1
            entry = {"cache_version": 1, "cache_key": key, "payload_sha256": payload_key(record),
                     "schema_sha256": self.schema_sha256, "prompt_sha256": self.prompt_sha256,
                     "extractor": self.fallback.name, "retrieved_at": utc_now(), "raw_response": raw}
            write_json(path, entry, self.secrets, overwrite=path.exists())
            self.last_metadata.update(extractor=entry["extractor"], retrieved_at=entry["retrieved_at"])
        self.last_raw_response = raw
        return raw
