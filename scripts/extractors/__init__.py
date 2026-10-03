"""Source-only evidence extraction; no provider or model requests by default."""

from .base import BaseExtractor, ExtractorError, build_payload, payload_key, empty_record
from .manual import ManualExtractor
from .cached import CachedExtractor
from .openai import OpenAIExtractor
