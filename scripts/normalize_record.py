"""Normalize observed Starter fields without inventing missing metadata."""

import re
import unicodedata
from datetime import datetime, timezone


def normalize_doi(doi):
    if not doi:
        return None
    doi = doi.strip().lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", doi)
    return doi.strip() or None


def normalize_title(title):
    if not title:
        return ""
    title = unicodedata.normalize("NFKC", title).casefold()
    title = re.sub(r"[^\w\s]", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def get_wos_citation_count(citations):
    for item in citations or []:
        if item.get("db") == "WOS":
            return item.get("count")
    return None


def normalize_record(hit, query=None, db="WOS", retrieved_at=None):
    source = hit.get("source") or {}
    names = hit.get("names") or {}
    identifiers = hit.get("identifiers") or {}
    keywords = hit.get("keywords") or {}
    links = hit.get("links") or {}
    authors = [
        {
            "display_name": author.get("displayName"),
            "wos_standard": author.get("wosStandard"),
            "researcher_id": author.get("researcherId"),
        }
        for author in names.get("authors") or []
    ]
    return {
        "uid": hit.get("uid"),
        "title": hit.get("title"),
        "document_types": hit.get("types") or [],
        "source_types": hit.get("sourceTypes") or [],
        "source_title": source.get("sourceTitle"),
        "publish_year": source.get("publishYear"),
        "publication_date_raw": source.get("publishMonth"),
        "volume": source.get("volume"),
        "issue": source.get("issue"),
        "article_number": source.get("articleNumber"),
        "page_count": (source.get("pages") or {}).get("count"),
        "authors": authors,
        "author_names": [a["display_name"] for a in authors if a["display_name"]],
        "researcher_ids": [a["researcher_id"] for a in authors if a["researcher_id"]],
        "doi": normalize_doi(identifiers.get("doi")),
        "issn": identifiers.get("issn"),
        "eissn": identifiers.get("eissn"),
        "isbn": identifiers.get("isbn"),
        "pmid": identifiers.get("pmid"),
        "author_keywords": keywords.get("authorKeywords") or [],
        "times_cited_wos": get_wos_citation_count(hit.get("citations")),
        "record_url": links.get("record"),
        "references_url": links.get("references"),
        "related_url": links.get("related"),
        "abstract": None,
        "abstract_source": None,
        "abstract_retrieved_at": None,
        "provenance": {
            "provider": "Clarivate",
            "api": "Web of Science Starter API",
            "database": db,
            "query": query,
            "retrieved_at": retrieved_at or datetime.now(timezone.utc).isoformat(),
        },
    }


def _identity_keys(record):
    keys = []
    doi = normalize_doi(record.get("doi"))
    uid = (record.get("uid") or "").strip().upper()
    if doi:
        keys.append(f"doi:{doi}")
    if uid:
        keys.append(f"uid:{uid}")
    if keys:
        return keys
    title = normalize_title(record.get("title"))
    year = record.get("publish_year")
    return [f"titleyear:{title}|{year}"] if title and year is not None else []


def dedup_key(record):
    """DOI > UID > title/year; records without usable identifiers return None."""
    keys = _identity_keys(record)
    return keys[0] if keys else None


def deduplicate_records(records):
    """Match DOI/UID aliases even if one copy lacks a DOI; keep first-seen order.

    Title/year is a fallback only for records without DOI and UID. Keep the first
    nonempty field on conflict; fill missing fields from actual duplicate records.
    All original versions remain in the archived API pages.
    """
    parents = list(range(len(records)))

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    owners = {}
    for index, record in enumerate(records):
        for key in _identity_keys(record):
            if key in owners:
                left, right = find(index), find(owners[key])
                parents[max(left, right)] = min(left, right)
            else:
                owners[key] = index
    groups = {}
    for index, record in enumerate(records):
        root = find(index)
        if root not in groups:
            groups[root] = dict(record)
            continue
        kept = groups[root]
        for field, value in record.items():
            if (kept.get(field) is None or kept.get(field) == []) and value is not None:
                kept[field] = value
    return list(groups.values())
