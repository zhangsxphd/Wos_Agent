"""Model-independent extraction contract and metadata-only payload builder."""

import copy
import hashlib
import json
from abc import ABC, abstractmethod


TREATMENTS = ("irrigation", "water_regime", "amendments", "fertilization", "biological_treatments", "other_treatments")
MEASUREMENTS = ("soil_physical", "soil_chemical", "carbon", "nitrogen", "microbial", "greenhouse_gases", "plant_growth", "yield", "water_use", "other")
INFERENCE_FIELDS = ("mechanistic_interpretation", "connection_to_user_research", "possible_gap", "transferable_idea", "needs_fulltext_for")


class ExtractorError(RuntimeError):
    """Fixed error codes, without source text or credentials."""


class BaseExtractor(ABC):
    name = "base"

    @abstractmethod
    def extract(self, record):
        """Return a source-derived EvidenceMatrixRecord JSON object."""
        raise NotImplementedError


def build_payload(record):
    # Explicit whitelist excludes user background, HTTP headers, API credentials,
    # previous inference and provider responses.
    return copy.deepcopy({
        "uid": record.get("uid"), "doi": record.get("doi"), "title": record.get("title"),
        "journal": record.get("source_title"), "year": record.get("publish_year"),
        "authors": record.get("authors") or [], "keywords": record.get("author_keywords") or [],
        "document_types": record.get("document_types") or [], "abstract": record.get("abstract"),
    })


def payload_key(record):
    payload = json.dumps(build_payload(record), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def empty_record(record, status=None, reason=None):
    has_abstract = bool(record.get("abstract"))
    return {
        "schema_version": "0.4", "uid": record.get("uid"), "doi": record.get("doi"), "title": record.get("title"),
        "screening": {"status": status or ("maybe" if has_abstract else "needs_fulltext"),
                      "reason": reason or ("Abstract available; eligibility criteria have not been supplied." if has_abstract else "No abstract is available; no content extraction or inference performed."),
                      "confidence": "low"},
        "evidence": {
            "study_system": {"crop": [], "soil_type": [], "salinity_context": [], "location": None, "experimental_scale": "unknown"},
            "treatments": {key: [] for key in TREATMENTS}, "measurements": {key: [] for key in MEASUREMENTS},
            "methods": [], "findings": [], "mechanisms_explicit": [], "limitations_explicit": [],
        },
        "evidence_support": {}, "inference": {key: [] for key in INFERENCE_FIELDS},
    }
