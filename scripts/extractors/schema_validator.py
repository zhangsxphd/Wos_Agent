"""JSON Schema plus literal evidence grounding and inference-anchor validation."""

import copy
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from ..pipeline_utils import ROOT


SCHEMA_PATH = ROOT / "schemas/evidence_matrix.schema.json"


class EvidenceValidationError(ValueError):
    pass


def resolve_pointer(value, pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise EvidenceValidationError("Invalid evidence anchor")
    try:
        for segment in pointer[1:].split("/"):
            segment = segment.replace("~1", "/").replace("~0", "~")
            value = value[int(segment)] if isinstance(value, list) else value[segment]
        return value
    except (KeyError, ValueError, TypeError, IndexError):
        raise EvidenceValidationError("Evidence anchor does not resolve") from None


def factual_leaves(value, prefix="/evidence"):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"findings", "author_interpretations"}:
                continue
            yield from factual_leaves(child, prefix + "/" + key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from factual_leaves(child, prefix + "/" + str(index))
    elif value not in (None, "", "unknown"):
        yield prefix, value


def compute_completeness(result, record):
    evidence = result["evidence"]
    flags = {"has_abstract": bool(record.get("abstract")),
             "has_treatment": any(evidence["treatments"].values()),
             "has_methods": bool(evidence["methods"]), "has_findings": bool(evidence["findings"])}
    flags["needs_fulltext"] = not all(flags.values()) or result["screening"]["status"] == "needs_fulltext"
    label = "high" if all(flags[key] for key in ("has_abstract", "has_treatment", "has_methods", "has_findings")) else \
            "medium" if flags["has_abstract"] and flags["has_findings"] and (flags["has_treatment"] or flags["has_methods"]) else "low"
    return flags, label


def _source_quote(support, record, allow_metadata=False):
    text = support["evidence_text"]
    if support["source"] == "abstract":
        abstract = record.get("abstract") or ""
        if not text or text not in abstract:
            raise EvidenceValidationError("Evidence text is not an abstract substring")
        start, end = support.get("start"), support.get("end")
        if type(start) is not int or type(end) is not int or start < 0 or end <= start or abstract[start:end] != text:
            raise EvidenceValidationError("Evidence offsets do not match the original abstract")
    else:
        if not allow_metadata:
            raise EvidenceValidationError("Experimental facts must be supported by the abstract, not title or keywords")
        value = resolve_pointer(record, support.get("metadata_path"))
        if text != str(value):
            raise EvidenceValidationError("Metadata evidence does not match its source field")
    return text


SCALE_PATTERNS = {
    "field": r"\bfield (?:study|experiment|soil column experiment|trial)\b|\bfield-based\b|\bin situ monitoring\b",
    "pot": r"\bpot (?:experiment|study|trial)s?\b", "greenhouse": r"\bgreenhouse\b",
    "lab": r"\blaboratory\b|\blab (?:experiment|study|trial)\b",
    "model": r"\bmodels?\b|\bmodelling\b|\bmodeling\b",
}


class EvidenceValidator:
    def __init__(self, schema_path=SCHEMA_PATH):
        self.schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(self.schema)
        self.validator = Draft202012Validator(self.schema)

    def validate(self, result, record):
        # Never expose jsonschema's message: it can contain arbitrary submitted
        # values, including a reflected credential.
        error = next(self.validator.iter_errors(result), None)
        if error:
            raise EvidenceValidationError("Evidence JSON does not conform to the schema")
        if any(result.get(key) != record.get(key) for key in ("uid", "doi", "title")):
            raise EvidenceValidationError("Evidence identifiers differ from the canonical record")
        abstract = record.get("abstract")
        if not abstract:
            if result["screening"]["status"] != "needs_fulltext":
                raise EvidenceValidationError("Missing abstract requires needs_fulltext")
            if list(factual_leaves(result["evidence"])) or result["evidence"]["findings"] or any(result["inference"].values()):
                raise EvidenceValidationError("No content evidence or inference is allowed without an abstract")
        expected_paths = set()
        for pointer, value in factual_leaves(result["evidence"]):
            expected_paths.add(pointer)
            support = result["evidence_support"].get(pointer)
            if support is None:
                raise EvidenceValidationError("A populated evidence field has no source support")
            quote = _source_quote(support, record)
            if pointer.endswith("/experimental_scale"):
                if not re.search(SCALE_PATTERNS[value], quote, re.IGNORECASE):
                    raise EvidenceValidationError("Experimental scale is not explicitly supported")
            elif str(value).casefold() not in quote.casefold():
                raise EvidenceValidationError("Evidence value or numerical value is not a source span")
        if set(result["evidence_support"]) != expected_paths:
            raise EvidenceValidationError("Evidence support contains missing or orphan anchors")
        for finding in result["evidence"]["findings"]:
            # User explicitly requires finding evidence_text from abstract,
            # even though the general schema reserves metadata as a source kind.
            quote = _source_quote(finding, record)
            if finding["claim"] not in quote:
                raise EvidenceValidationError("Finding claim or numerical value is not a verbatim supported span")
        for interpretation in result["evidence"].get("author_interpretations", []):
            if interpretation["source"] != "abstract":
                raise EvidenceValidationError("Fulltext interpretations require the fulltext validator")
            quote = _source_quote(interpretation["anchor"], record)
            if interpretation["text"] not in quote:
                raise EvidenceValidationError("Author interpretation has no verbatim support")
        for items in result["inference"].values():
            for item in items:
                for pointer in item["evidence_anchors"]:
                    if not pointer.startswith("/evidence/"):
                        raise EvidenceValidationError("Inference must anchor to existing evidence")
                    target = resolve_pointer(result, pointer)
                    if target in (None, "", "unknown", [], {}):
                        raise EvidenceValidationError("Inference anchor points to empty evidence")
                    if pointer not in expected_paths and not re.fullmatch(r"/evidence/findings/\d+", pointer):
                        raise EvidenceValidationError("Inference anchor must identify a supported fact or finding")
        flags, label = compute_completeness(result, record)
        if "completeness" in result and result["completeness"] != flags:
            raise EvidenceValidationError("Reported completeness does not match the extracted evidence")
        if "evidence_completeness" in result and result["evidence_completeness"] != label:
            raise EvidenceValidationError("Reported completeness label does not match the evidence")
        return result

    def normalize(self, result, record):
        self.validate(result, record)
        normalized = copy.deepcopy(result)
        # Exact source + text only; remap existing finding anchors so removal
        # cannot silently change the meaning of a downstream interpretation.
        findings, indices, seen = [], {}, {}
        for old_index, finding in enumerate(normalized["evidence"]["findings"]):
            key = (finding["source"], finding["evidence_text"])
            if key not in seen:
                seen[key] = len(findings)
                findings.append(finding)
            indices[old_index] = seen[key]
        normalized["evidence"]["findings"] = findings
        for items in normalized["inference"].values():
            for item in items:
                item["evidence_anchors"] = list(dict.fromkeys(
                    "/evidence/findings/" + str(indices[int(p.rsplit("/", 1)[1])])
                    if re.fullmatch(r"/evidence/findings/\d+", p) else p for p in item["evidence_anchors"]))
        normalized["evidence"].setdefault("author_interpretations", [])
        flags, label = compute_completeness(normalized, record)
        normalized.update(completeness=flags, evidence_completeness=label)
        self.validate(normalized, record)
        return normalized
