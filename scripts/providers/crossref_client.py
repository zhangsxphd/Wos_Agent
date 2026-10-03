"""Crossref exact DOI lookup with deterministic JATS/HTML text extraction."""

import re
from html.parser import HTMLParser
from urllib.parse import quote

from .base_client import BaseClient
from ..normalize_record import normalize_doi


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.hidden = [], 0

    def handle_starttag(self, tag, attrs):
        tag = tag.split(":")[-1]
        if tag in {"script", "style"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "title", "br", "sec", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        tag = tag.split(":")[-1]
        if tag in {"script", "style"}:
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in {"p", "title", "sec", "div"}:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def clean_markup(value):
    if not isinstance(value, str) or not value.strip():
        return None
    parser = _TextParser()
    parser.feed(value)
    parser.close()
    return re.sub(r"\s+", " ", "".join(parser.parts)).strip() or None


class CrossrefClient(BaseClient):
    provider = "crossref"

    def request_spec(self, doi):
        return "https://api.crossref.org/works/" + quote(doi, safe=""), {}

    def parse(self, raw, retrieved_at):
        message = raw["message"]
        if not message:
            return None
        year = None
        for name in ("published-print", "issued", "published", "published-online"):
            parts = (message.get(name) or {}).get("date-parts") or []
            if parts and parts[0]:
                year = parts[0][0]
                break
        title = message.get("title") or []
        return self.unified(raw, retrieved_at, provider_id=message.get("DOI"),
                            doi=normalize_doi(message.get("DOI")),
                            title=clean_markup(title[0] if isinstance(title, list) and title else title),
                            year=year, abstract=clean_markup(message.get("abstract")), url=message.get("URL"))
