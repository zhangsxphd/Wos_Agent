"""Deterministic v0.6.1 evaluation matching, separate from strict v1 metrics."""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from scripts.evidence.metrics import KEY_FIELDS, normalize_item


EXACT_NORMALIZED = "EXACT_NORMALIZED"
BOUNDARY_EQUIVALENT = "BOUNDARY_EQUIVALENT"
SCALE_FIELD = "/evidence/study_system/experimental_scale"
SCALE_ENUM = {"field", "pot", "greenhouse", "lab", "model", "unknown"}


def _get_pointer(document, pointer):
    value = document
    for part in pointer.split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            value = value[int(part)]
        elif isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _field_items(record, pointer):
    evidence = (record or {}).get("evidence", {})
    value = _get_pointer(evidence, pointer.removeprefix("/evidence"))
    support = (record or {}).get("evidence_support", {}) or {}
    if pointer == SCALE_FIELD:
        if value in (None, "unknown", ""):
            return []
        return [{"value": value, "support": support.get(pointer)}]
    if not isinstance(value, list):
        return []
    return [{"value": item, "support": support.get(f"{pointer}/{index}")}
            for index, item in enumerate(value)]


def _valid_support(item, abstract):
    support = item.get("support") or {}
    start, end = support.get("start"), support.get("end")
    quote = support.get("evidence_text", "")
    return (support.get("source") == "abstract" and type(start) is int and type(end) is int
            and 0 <= start < end <= len(abstract) and abstract[start:end] == quote)


def _intervals_overlap(a, b):
    sa, ea = a["support"]["start"], a["support"]["end"]
    sb, eb = b["support"]["start"], b["support"]["end"]
    return max(sa, sb) < min(ea, eb)


def _ordered_token_subsequence(shorter, longer):
    tokens = re.findall(r"\w+", normalize_item(shorter), flags=re.UNICODE)
    sequence = re.findall(r"\w+", normalize_item(longer), flags=re.UNICODE)
    if not tokens or len(tokens) >= len(sequence):
        return False
    cursor = 0
    for token in sequence:
        if cursor < len(tokens) and tokens[cursor] == token:
            cursor += 1
    return cursor == len(tokens)


def _source_value_interval(value, abstract, start, end):
    value_tokens = re.findall(r"\w+", normalize_item(value), flags=re.UNICODE)
    source = abstract[start:end]
    source_tokens = list(re.finditer(r"\w+", source, flags=re.UNICODE))
    wanted = 0
    first = last = None
    for token in source_tokens:
        if wanted < len(value_tokens) and normalize_item(token.group()) == value_tokens[wanted]:
            if first is None:
                first = start + token.start()
            last = start + token.end()
            wanted += 1
    if wanted != len(value_tokens) or first is None or last is None:
        return None
    return first, last


def boundary_equivalent(a, b, abstract, *, scale=False):
    """Return whether two same-field items are a deterministic source-boundary pair."""
    if scale or not _valid_support(a, abstract) or not _valid_support(b, abstract):
        return False
    if not _intervals_overlap(a, b):
        return False
    av, bv = normalize_item(a.get("value")), normalize_item(b.get("value"))
    if not av or not bv:
        return False
    shared_start = max(a["support"]["start"], b["support"]["start"])
    shared_end = min(a["support"]["end"], b["support"]["end"])
    if av in bv or bv in av:
        shorter = av if len(av) <= len(bv) else bv
        return _source_value_interval(shorter, abstract, shared_start, shared_end) is not None
    a_value_span = _source_value_interval(av, abstract, shared_start, shared_end)
    b_value_span = _source_value_interval(bv, abstract, shared_start, shared_end)
    if not a_value_span or not b_value_span or max(a_value_span[0], b_value_span[0]) >= min(a_value_span[1], b_value_span[1]):
        return False
    # A literal nested value plus overlapping, verified source spans is the
    # primary safe boundary case (e.g. a phrase with a dropped qualifier).
    if _ordered_token_subsequence(av, bv) or _ordered_token_subsequence(bv, av):
        return True

    # When value strings differ, accept only proper nested support quotations
    # whose shared source region literally contains both extracted values.
    aq = normalize_item(a["support"].get("evidence_text"))
    bq = normalize_item(b["support"].get("evidence_text"))
    if not aq or not bq or aq == bq or not (aq in bq or bq in aq):
        return False
    shared = normalize_item(abstract[shared_start:shared_end])
    return av in shared and bv in shared


def one_to_one_match(gold_items, predicted_items, abstract, *, scale=False):
    """Match exact normalized items first, then source-boundary equivalents."""
    remaining_gold = set(range(len(gold_items)))
    remaining_pred = set(range(len(predicted_items)))
    pairs = []
    for pi, predicted in enumerate(predicted_items):
        pv = predicted.get("value")
        match = next((gi for gi in sorted(remaining_gold)
                      if ((gold_items[gi].get("value") == pv) if scale else
                          (normalize_item(gold_items[gi].get("value")) == normalize_item(pv)))), None)
        if match is not None:
            pairs.append({"gold_index": match, "prediction_index": pi, "level": EXACT_NORMALIZED})
            remaining_gold.remove(match)
            remaining_pred.remove(pi)
    if not scale:
        for pi in sorted(remaining_pred):
            match = next((gi for gi in sorted(remaining_gold)
                          if boundary_equivalent(gold_items[gi], predicted_items[pi], abstract)), None)
            if match is not None:
                pairs.append({"gold_index": match, "prediction_index": pi, "level": BOUNDARY_EQUIVALENT})
                remaining_gold.remove(match)
                remaining_pred.remove(pi)
    return pairs, sorted(remaining_pred), sorted(remaining_gold)


def field_metrics_v2(gold, predicted, abstract):
    totals = Counter()
    per_field = {}
    for pointer in KEY_FIELDS:
        g, p = _field_items(gold, pointer), _field_items(predicted, pointer)
        is_scale = pointer == SCALE_FIELD
        pairs, unmatched_p, unmatched_g = one_to_one_match(g, p, abstract, scale=is_scale)
        exact = sum(x["level"] == EXACT_NORMALIZED for x in pairs)
        boundary = sum(x["level"] == BOUNDARY_EQUIVALENT for x in pairs)
        per_field[pointer] = {"exact_tp": exact, "boundary_tp": boundary,
                              "fp": len(unmatched_p), "fn": len(unmatched_g),
                              "gold_count": len(g), "predicted_count": len(p),
                              "matches": [{**pair, "gold": g[pair["gold_index"]]["value"],
                                           "prediction": p[pair["prediction_index"]]["value"]} for pair in pairs]}
        totals.update({"exact_tp": exact, "boundary_tp": boundary,
                       "fp": len(unmatched_p), "fn": len(unmatched_g),
                       "gold_count": len(g), "predicted_count": len(p)})
    tp = totals["exact_tp"] + totals["boundary_tp"]
    precision = tp / (tp + totals["fp"]) if tp + totals["fp"] else (1.0 if not totals["gold_count"] else 0.0)
    recall = tp / (tp + totals["fn"]) if tp + totals["fn"] else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {**dict(totals), "precision": precision, "recall": recall, "f1": f1,
            "field_metrics": per_field}


def _finding_items(record):
    return list(((record or {}).get("evidence") or {}).get("findings") or [])


def _finding_anchor(item):
    return {"value": item.get("evidence_text"), "support": {
        "source": item.get("source"), "evidence_text": item.get("evidence_text"),
        "start": item.get("start"), "end": item.get("end")}}


def finding_metrics_v2(gold, predicted, abstract):
    g, p = _finding_items(gold), _finding_items(predicted)
    g_anchors, p_anchors = [_finding_anchor(x) for x in g], [_finding_anchor(x) for x in p]
    pairs, unmatched_p, unmatched_g = one_to_one_match(g_anchors, p_anchors, abstract)
    exact = sum(x["level"] == EXACT_NORMALIZED for x in pairs)
    boundary = sum(x["level"] == BOUNDARY_EQUIVALENT for x in pairs)
    tp = exact + boundary
    precision = tp / len(p) if p else (1.0 if not g else 0.0)
    recall = tp / len(g) if g else 1.0
    return {"exact_match": exact, "boundary_match": boundary,
            "unmatched_prediction": len(unmatched_p), "unmatched_gold": len(unmatched_g),
            "gold_count": len(g), "predicted_count": len(p),
            "precision": precision, "recall": recall,
            "matches": [{**pair, "gold": g[pair["gold_index"]],
                         "prediction": p[pair["prediction_index"]]} for pair in pairs],
            "unmatched_predictions": [p[i] for i in unmatched_p],
            "unmatched_gold_items": [g[i] for i in unmatched_g]}


def corpus_metrics_v2(papers):
    fields = Counter()
    findings = Counter()
    per_record = {}
    for uid, gold, predicted, abstract in papers:
        fm = field_metrics_v2(gold, predicted, abstract)
        dm = finding_metrics_v2(gold, predicted, abstract)
        fields.update({k: fm[k] for k in ("exact_tp", "boundary_tp", "fp", "fn", "gold_count", "predicted_count")})
        findings.update({k: dm[k] for k in ("exact_match", "boundary_match", "unmatched_prediction", "unmatched_gold", "gold_count", "predicted_count")})
        per_record[uid] = {"fields": fm, "findings": dm}
    tp = fields["exact_tp"] + fields["boundary_tp"]
    field_precision = tp / (tp + fields["fp"]) if tp + fields["fp"] else 1.0
    field_recall = tp / (tp + fields["fn"]) if tp + fields["fn"] else 1.0
    field_f1 = 2 * field_precision * field_recall / (field_precision + field_recall) if field_precision + field_recall else 0.0
    finding_precision = findings["exact_match"] + findings["boundary_match"]
    finding_precision = finding_precision / findings["predicted_count"] if findings["predicted_count"] else 1.0
    finding_recall = (findings["exact_match"] + findings["boundary_match"]) / findings["gold_count"] if findings["gold_count"] else 1.0
    return {"fields": {**dict(fields), "precision": field_precision, "recall": field_recall, "f1": field_f1},
            "findings": {**dict(findings), "precision": finding_precision, "recall": finding_recall},
            "per_record": per_record}
