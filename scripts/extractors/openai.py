"""Reserved adapter: no model API client, credentials or network calls yet."""

from .base import BaseExtractor, ExtractorError


class OpenAIExtractor(BaseExtractor):
    name = "openai_reserved"

    def extract(self, record):
        raise ExtractorError("openai_extractor_not_configured")
