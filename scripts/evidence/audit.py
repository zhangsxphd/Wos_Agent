"""Add review flags for local abstract context; never changes validation."""

import re

BACKGROUND = re.compile(r"\b(background|previous studies|recent studies|has been reported|for example|such as)\b", re.I)
REVIEW = re.compile(r"\b(review|recent studies|previous studies|literature|meta-analysis)\b", re.I)
FUTURE = re.compile(r"\b(future studies|future research|should be investigated|warrants further|is recommended|we recommend)\b", re.I)
ASSOCIATION = re.compile(r"\b(associated with|correlated with|relationship between|driver[s]? of|primary control[s]?|affected by|influenced by|predictor[s]?)\b", re.I)


def audit_evidence(evidence, record):
    """Return non-blocking flags keyed to populated abstract-supported claims."""
    flags=[]; abstract=record.get("abstract") or ""
    def add(flag, field, value, span):
        flags.append({"flag":flag,"field":field,"value":value,"support_span":span})
    for pointer, support in (evidence.get("evidence_support") or {}).items():
        span=support.get("evidence_text","")
        context=_sentence_context(abstract,support.get("start"),support.get("end"),span)
        value=_pointer_value(evidence,pointer)
        if len(span.strip()) < 15: add("support_too_short",pointer,value,span)
        if BACKGROUND.search(context): add("background_language",pointer,value,span)
        if REVIEW.search(context) or any("review" in str(v).casefold() for v in record.get("document_types",[])):
            add("review_language",pointer,value,span)
        if FUTURE.search(context): add("future_recommendation_language",pointer,value,span)
        if pointer.startswith("/evidence/treatments/") and ASSOCIATION.search(context):
            add("association_vs_treatment_risk",pointer,value,span)
    for idx, finding in enumerate(evidence.get("evidence",{}).get("findings",[])):
        span=finding.get("evidence_text","")
        field=f"/evidence/findings/{idx}"
        if len(span.strip()) < 15: add("support_too_short",field,finding.get("claim"),span)
        if BACKGROUND.search(span): add("background_language",field,finding.get("claim"),span)
        if REVIEW.search(span) or any("review" in str(v).casefold() for v in record.get("document_types",[])):
            add("review_language",field,finding.get("claim"),span)
        if FUTURE.search(span): add("future_recommendation_language",field,finding.get("claim"),span)
    return flags


def _sentence_context(abstract,start,end,quote):
    if type(start) is int and type(end) is int and 0<=start<end<=len(abstract):
        left=max(abstract.rfind('.',0,start),abstract.rfind('?',0,start),abstract.rfind('!',0,start))+1
        stops=[p for p in (abstract.find('.',end),abstract.find('?',end),abstract.find('!',end)) if p>=0]
        right=min(stops)+1 if stops else len(abstract)
        return abstract[left:right]
    at=abstract.find(quote) if quote else -1
    if at>=0: return _sentence_context(abstract,at,at+len(quote),quote)
    return quote


def _pointer_value(evidence,pointer):
    value=evidence
    for segment in pointer.split("/")[1:]:
        segment=segment.replace("~1","/").replace("~0","~")
        value=value[int(segment)] if isinstance(value,list) else value.get(segment) if isinstance(value,dict) else None
        if value is None: break
    return value
