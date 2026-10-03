"""Read a human-reviewed response identified by the exact extraction payload."""

import json
from pathlib import Path

from .base import BaseExtractor, ExtractorError, payload_key


class ManualExtractor(BaseExtractor):
    name = "manual"

    def __init__(self, response_dir):
        self.response_dir = Path(response_dir)
        self.last_raw_response = None

    def extract(self, record):
        path = self.response_dir / (payload_key(record) + ".json")
        if not path.is_file():
            raise ExtractorError("manual_response_missing")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise ExtractorError("manual_response_invalid_json") from None
        self.last_raw_response = raw
        return raw
