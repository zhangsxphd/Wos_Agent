"""Semantic Scholar Graph API: DOI identifier only, optional API key."""

from urllib.parse import quote

from .base_client import BaseClient
from ..normalize_record import normalize_doi


class SemanticScholarClient(BaseClient):
    provider = "semantic_scholar"
    credential_name = "SEMANTIC_SCHOLAR_API_KEY"

    def configure_auth(self):
        if self.api_key:
            self.session.headers.update({"x-api-key": self.api_key})

    def request_spec(self, doi):
        return ("https://api.semanticscholar.org/graph/v1/paper/" + quote("DOI:" + doi, safe=""),
                {"fields": "paperId,title,year,abstract,externalIds,url"})

    def parse(self, raw, retrieved_at):
        if not raw:
            return None
        return self.unified(raw, retrieved_at, provider_id=raw.get("paperId"),
                            doi=normalize_doi((raw.get("externalIds") or {}).get("DOI")),
                            title=raw.get("title"), year=raw.get("year"),
                            abstract=raw.get("abstract") or None, url=raw.get("url"))
