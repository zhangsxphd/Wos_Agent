"""V2 audit semantics; preserves the historical audit.py flags unchanged."""

import re

from scripts.evidence.audit import _pointer_value, _sentence_context


REVIEW_CONTEXT = re.compile(r"\b(review|reviewed literature|recent studies|previous studies|meta-analysis)\b", re.I)
PRIOR_WORK = re.compile(r"\b(recent|previous|prior|other) studies\b|\bliterature reports?\b|\bstudies have shown\b", re.I)
EXPERIMENTAL_FIELDS = ("/evidence/study_system/experimental_scale", "/evidence/treatments/",
                       "/evidence/measurements/", "/evidence/methods")


def audit_evidence_v2(evidence, record):
    """Separate review-context awareness from explicit review experiment errors.

    A Review document type creates context flags for populated, abstract-grounded
    facts. A populated fact field does not encode who performed the experiment.
    Emit an error only when the extraction carries an explicit
    ``attributed_to=review_author`` assertion and the abstract explicitly
    attributes the fact to prior work. Otherwise ownership is unknown and must
    not be inferred from field placement alone.
    """
    flags = []
    abstract = record.get("abstract") or ""
    is_review = any("review" in str(value).casefold() for value in record.get("document_types", []))
    evidence_support = evidence.get("evidence_support") or {}
    for pointer, support in evidence_support.items():
        quote = support.get("evidence_text", "")
        context = _sentence_context(abstract, support.get("start"), support.get("end"), quote)
        if not (is_review or REVIEW_CONTEXT.search(context)):
            continue
        value = _pointer_value(evidence, pointer)
        flags.append({"flag": "review_context_flag", "field": pointer, "value": value,
                      "support_span": quote})
        experiment_fact = any(pointer.startswith(prefix) for prefix in EXPERIMENTAL_FIELDS)
        if (is_review and experiment_fact and support.get("attributed_to") == "review_author"
                and PRIOR_WORK.search(context)):
            flags.append({"flag": "review_as_experiment_error", "field": pointer,
                          "value": value, "support_span": quote})
    for index, finding in enumerate((evidence.get("evidence") or {}).get("findings", [])):
        quote = finding.get("evidence_text", "")
        if is_review or REVIEW_CONTEXT.search(quote):
            flags.append({"flag": "review_context_flag", "field": f"/evidence/findings/{index}",
                          "value": finding.get("claim"), "support_span": quote})
    return flags
