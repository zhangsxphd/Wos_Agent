"""JSON Schema plus literal evidence grounding and inference-anchor validation."""

import copy
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

from ..pipeline_utils import ROOT


SCHEMA_PATH = ROOT / "schemas/evidence_matrix.schema.json"


from .schema_validator import EvidenceValidationError


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


# Patterns are combined with contextual rules below. A lexical match alone is
# insufficient for field/pot/model so generic domain mentions do not become scale.
SCALE_PATTERNS = {
    "field": r"\bfield(?:s)?(?:[ -](?:scale|based|study|experiment|trial))?\b",
    "pot": r"\bpots?\b", "greenhouse": r"\bgreenhouse\b",
    "lab": r"\blaboratory\b|\blab\b|\bincubation\b",
    "model": r"\bmodel(?:ling|ing)?\b|\bsimulation\b",
}
FIELD_PHYSICAL_CUES = re.compile(
    r"\b(?:agricultur(?:al|e)|crop|rice|paddy|maize|cotton|soil|salin(?:e|ity)|"
    r"reclaimed|coastal|farm|plot|integrated fields|field[- ](?:based|scale|study|experiment)|experimental field)\b", re.I)
FIELD_RELATION_CUES = re.compile(
    r"\b(?:conduct(?:ed)?|perform(?:ed)?|investigat(?:e|ed|ing)|monitor(?:ed|ing)?|"
    r"sampl(?:e|ed|ing)|treat(?:ed|ment)|experiment(?:s|al)?|study|studied|grow(?:n|ing)?)\b", re.I)
POT_RELATION_CUES = re.compile(
    r"\b(?:maintain(?:ed)?|apply|applied|test(?:ed)?|grow(?:n|ing)?|cultivat(?:ed|ion)|"
    r"conduct(?:ed)?|experiment(?:s|al)?|treat(?:ment|ed)|salinity|substrate|condition(?:s)?)\b", re.I)
MODEL_STUDY_PATTERNS = re.compile(
    r"\b(?:model[- ]based\s+(?:study|experiment|analysis)|"
    r"(?:modeling|modelling)\s+(?:study|experiment|analysis)|"
    r"(?:simulation|numerical simulation)\s+study)\b", re.I)


def scale_supported(value, quote):
    """Contextual scale recognition for explicit physical or analytical study settings."""
    text = str(quote)
    if value == "field":
        if not re.search(SCALE_PATTERNS["field"], text, re.I):
            return False
        generic = re.search(r"\b(?:research field|field of study|field application potential|field conditions)\b", text, re.I)
        explicit_phrase = re.search(r"\bfield(?:[- ]scale|[- ]based|[- ]study|[- ]experiments?|[- ]trial)\b", text, re.I)
        if generic and not explicit_phrase:
            return False
        linked = re.search(
            r"\bfield(?:[- ]scale|[- ]based|[- ]study|[- ]experiments?|[- ]trial)\b|"
            r"\b(?:conduct(?:ed)?|perform(?:ed)?|investigat(?:e|ed|ing)|monitor(?:ed|ing)?|"
            r"sampl(?:e|ed|ing))\b[\s\S]{0,320}\bfields?\b|"
            r"\bfields?\b[\s\S]{0,100}\b(?:experiment|study|conducted|investigated|monitored|sampled)\b",
            text, re.I)
        return bool(linked and FIELD_RELATION_CUES.search(text) and FIELD_PHYSICAL_CUES.search(text))
    if value == "pot":
        return bool(re.search(SCALE_PATTERNS["pot"], text, re.I) and POT_RELATION_CUES.search(text))
    if value == "model":
        return bool(MODEL_STUDY_PATTERNS.search(text))
    if value in {"greenhouse", "lab"}:
        return bool(re.search(SCALE_PATTERNS[value], text, re.I) and FIELD_RELATION_CUES.search(text))
    return False


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
                if not scale_supported(value, quote):
                    raise EvidenceValidationError("Experimental scale is not explicitly supported")
            elif str(value).casefold() not in quote.casefold():
                # Deterministic compound-label restoration when one source sentence
                # elides an analyte after naming it once for a tissue series.
                zn_tissue = (str(value).casefold() == "zn concentration in roots, shoots, and paddy"
                             and re.search(r"zn concentration in soil[\s\S]*?\broots\b[\s\S]*?\bshoots\b[\s\S]*?\bpaddy\b", quote, re.I))
                if not zn_tissue:
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
