"""DOI-only enrichment providers. WoS bibliographic metadata is never mutated."""

from .crossref_client import CrossrefClient
from .semantic_scholar_client import SemanticScholarClient
from .openalex_client import OpenAlexClient

PROVIDER_NAMES = ("crossref", "semantic_scholar", "openalex")
