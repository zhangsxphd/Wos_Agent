"""Deterministic, non-LLM metrics and taxonomy for v0.6 evidence benchmarks."""

import re
import unicodedata
from collections import Counter

from scripts.extractors.base import TREATMENTS, MEASUREMENTS


LIST_PATHS = [
    "/evidence/study_system/crop", "/evidence/study_system/soil_type",
    "/evidence/study_system/salinity_context", "/evidence/treatments/irrigation",
    *[f"/evidence/treatments/{name}" for name in TREATMENTS if name != "irrigation"],
    *[f"/evidence/measurements/{name}" for name in MEASUREMENTS],
    "/evidence/methods",
]
KEY_FIELDS = ["/evidence/study_system/crop", "/evidence/study_system/soil_type",
              "/evidence/study_system/salinity_context", "/evidence/study_system/experimental_scale",
              *[f"/evidence/treatments/{name}" for name in TREATMENTS],
              *[f"/evidence/measurements/{name}" for name in MEASUREMENTS], "/evidence/methods"]
ERROR_TAXONOMY = (
    "false_positive_fact", "missed_fact", "wrong_category", "background_as_treatment",
    "review_as_experiment", "observational_as_treatment", "unsupported_method", "wrong_scale",
    "wrong_crop", "wrong_salinity_context", "finding_overextract", "finding_missed",
    "offset_error", "schema_error",
)


def normalize_item(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"\s+", " ", value).strip()


def item_metrics(gold, predicted):
    g = {normalize_item(v) for v in (gold or []) if normalize_item(v)}
    p = {normalize_item(v) for v in (predicted or []) if normalize_item(v)}
    tp = len(g & p); fp = len(p - g); fn = len(g - p)
    precision = tp / (tp + fp) if tp + fp else (1.0 if not g else 0.0)
    recall = tp / (tp + fn) if tp + fn else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1,
            "gold_count": len(g), "predicted_count": len(p)}


def pointer_value(record, pointer):
    value = record
    for segment in pointer.split("/")[1:]:
        segment = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(segment)] if isinstance(value, list) else value.get(segment)
        if value is None: break
    return value


def finding_metrics(gold, predicted):
    g = list((gold or {}).get("evidence", {}).get("findings", []))
    p = list((predicted or {}).get("evidence", {}).get("findings", []))
    remaining = set(range(len(g))); exact = overlap = 0
    for item in p:
        text = normalize_item(item.get("evidence_text"))
        match = next((i for i in sorted(remaining) if normalize_item(g[i].get("evidence_text")) == text), None)
        if match is not None:
            exact += 1; overlap += 1; remaining.remove(match); continue
        match = next((i for i in sorted(remaining) if text and
                      (text in normalize_item(g[i].get("evidence_text")) or normalize_item(g[i].get("evidence_text")) in text)), None)
        if match is not None:
            overlap += 1; remaining.remove(match)
    precision = overlap / len(p) if p else (1.0 if not g else 0.0)
    recall = overlap / len(g) if g else 1.0
    return {"gold_count": len(g), "predicted_count": len(p), "exact_evidence_text_match": exact,
            "overlap_match": overlap, "precision": precision, "recall": recall,
            "exact_precision": exact / len(p) if p else (1.0 if not g else 0.0),
            "exact_recall": exact / len(g) if g else 1.0}


def _empty_taxonomy():
    return {name: [] for name in ERROR_TAXONOMY}


def compare_record(gold, predicted, valid=True, validation_error=None):
    errors = _empty_taxonomy(); fields = {}
    uid = (gold or predicted or {}).get("uid")
    doi = (gold or predicted or {}).get("doi")
    if predicted is None:
        for pointer in KEY_FIELDS:
            value=pointer_value(gold or {},pointer)
            vals=[] if value in (None,"unknown") else [value] if pointer.endswith("experimental_scale") else (value or [])
            metric=item_metrics(vals,[]); fields[pointer]=metric
            for item in (vals if isinstance(vals,list) else [vals]):
                errors['missed_fact'].append({"uid":uid,"doi":doi,"field":pointer,"predicted":None,"gold":item,"support_span":None})
        fm=finding_metrics(gold,None)
        if fm['gold_count']:
            errors['finding_missed'].append({"uid":uid,"doi":doi,"field":"/evidence/findings","predicted":0,"gold":fm['gold_count'],"support_span":None})
        return fields, fm, errors
    for pointer in KEY_FIELDS:
        gvalue, pvalue = pointer_value(gold or {}, pointer), pointer_value(predicted, pointer)
        if pointer.endswith("experimental_scale"):
            gitems = [] if gvalue in (None, "unknown") else [gvalue]
            pitems = [] if pvalue in (None, "unknown") else [pvalue]
        else:
            gitems, pitems = gvalue or [], pvalue or []
        metric = item_metrics(gitems, pitems); fields[pointer] = metric
        support_map = predicted.get("evidence_support", {})
        norm_g = {normalize_item(x) for x in gitems}; norm_p = {normalize_item(x) for x in pitems}
        for value in sorted(norm_p - norm_g):
            pointer_idx = next((i for i,x in enumerate(pitems) if normalize_item(x)==value), None)
            support = support_map.get(pointer + (f"/{pointer_idx}" if pointer_idx is not None and not pointer.endswith("experimental_scale") else ""), {})
            error = {"uid": uid, "doi": doi, "field": pointer, "predicted": value, "gold": None,
                     "support_span": support.get("evidence_text")}
            errors["false_positive_fact"].append(error)
            if pointer.endswith("/methods") and not valid:
                errors["unsupported_method"].append(error)
            if pointer.endswith("/crop"): errors["wrong_crop"].append(error)
            if pointer.endswith("/salinity_context"): errors["wrong_salinity_context"].append(error)
            if pointer.endswith("experimental_scale"): errors["wrong_scale"].append(error)
            other_gold={normalize_item(x) for other in KEY_FIELDS if other!=pointer
                        for x in ((pointer_value(gold or {},other) or []) if not other.endswith('experimental_scale')
                                  else [pointer_value(gold or {},other)]) if x not in (None,'unknown')}
            if value in other_gold: errors['wrong_category'].append(error)
        for value in sorted(norm_g - norm_p):
            errors["missed_fact"].append({"uid":uid,"doi":doi,"field":pointer,"predicted":None,"gold":value,"support_span":None})
    findings = finding_metrics(gold, predicted)
    if findings["predicted_count"] > findings["overlap_match"]:
        errors["finding_overextract"].append({"uid":uid,"doi":doi,"field":"/evidence/findings","predicted":findings["predicted_count"],"gold":findings["gold_count"],"support_span":None})
    if findings["gold_count"] > findings["overlap_match"]:
        errors["finding_missed"].append({"uid":uid,"doi":doi,"field":"/evidence/findings","predicted":findings["predicted_count"],"gold":findings["gold_count"],"support_span":None})
    if not valid:
        category = "offset_error" if validation_error == "offset" else "schema_error"
        errors[category].append({"uid":uid,"doi":doi,"field":"record","predicted":"rejected","gold":"valid extraction",
                                 "support_span":None})
    return fields, findings, errors


def calculate_benchmark(gold_by_uid, predictions_by_uid, validation_by_uid, audits_by_uid=None):
    audits_by_uid = audits_by_uid or {}
    field_counts = {path:{"tp":0,"fp":0,"fn":0,"gold_count":0,"predicted_count":0} for path in KEY_FIELDS}
    taxonomy = _empty_taxonomy(); finding = Counter(); unsupported_fields = unsupported_findings = invalid_offsets = orphan_anchors = rejected = 0
    finding_claims_checked=finding_claims_grounded=0
    schema_valid = grounding_valid = identifier_matches = contract_valid = 0
    records=[]
    for uid, gold in gold_by_uid.items():
        predicted=predictions_by_uid.get(uid); val=validation_by_uid.get(uid,{})
        schema_valid += bool(val.get("schema_valid")); grounding_valid += bool(val.get("grounding_valid"))
        identifier_matches += bool(val.get("identifier_match")); rejected += predicted is not None and not bool(val.get("accepted"))
        contract_valid += bool(val.get("response_contract_valid"))
        unsupported_fields += int(val.get("unsupported_field_count",0)); unsupported_findings += int(val.get("unsupported_finding_count",0))
        invalid_offsets += int(val.get("invalid_offset_count",0)); orphan_anchors += int(val.get("orphan_anchor_count",0))
        field_metrics, finding_result, row_errors = compare_record(gold,predicted,val.get("accepted",False),val.get("error_kind"))
        claim_count=len((predicted or {}).get('evidence',{}).get('findings',[]))
        finding_claims_checked += claim_count
        if val.get('grounding_valid'): finding_claims_grounded += claim_count
        for path,metric in field_metrics.items():
            for k in ("tp","fp","fn","gold_count","predicted_count"): field_counts[path][k]+=metric[k]
        for k,v in finding_result.items():
            if k.endswith('count') or k in ('exact_evidence_text_match','overlap_match'): finding[k]+=v
        for name,items in row_errors.items(): taxonomy[name].extend(items)
        for flag in audits_by_uid.get(uid,[]):
            if flag["flag"] == "background_language" and str(flag.get("field","")).startswith("/evidence/treatments/"):
                taxonomy["background_as_treatment"].append({"uid":uid,"doi":gold.get("doi"),"field":flag.get("field"),"predicted":flag.get("value"),"gold":None,"support_span":flag.get("support_span")})
            if flag["flag"] == "association_vs_treatment_risk":
                taxonomy["observational_as_treatment"].append({"uid":uid,"doi":gold.get("doi"),"field":flag.get("field"),"predicted":flag.get("value"),"gold":None,"support_span":flag.get("support_span")})
            if flag["flag"] == "review_language":
                taxonomy["review_as_experiment"].append({"uid":uid,"doi":gold.get("doi"),"field":flag.get("field"),"predicted":flag.get("value"),"gold":None,"support_span":flag.get("support_span")})
        records.append({"uid":uid,"schema_valid":bool(val.get("schema_valid")),"grounding_valid":bool(val.get("grounding_valid")),
                        "identifier_match":bool(val.get("identifier_match")),"accepted":bool(val.get("accepted")),
                        "audit_flags":audits_by_uid.get(uid,[])})
    n=len(gold_by_uid)
    for metric in field_counts.values():
        tp,fp,fn=metric['tp'],metric['fp'],metric['fn']
        metric['precision']=tp/(tp+fp) if tp+fp else (1.0 if metric['gold_count']==0 else 0.0)
        metric['recall']=tp/(tp+fn) if tp+fn else 1.0
        metric['f1']=2*metric['precision']*metric['recall']/(metric['precision']+metric['recall']) if metric['precision']+metric['recall'] else 0.0
    tp,fp,fn=finding['overlap_match'],finding['predicted_count']-finding['overlap_match'],finding['gold_count']-finding['overlap_match']
    finding_precision=tp/(tp+fp) if tp+fp else (1.0 if finding['gold_count']==0 else 0.0)
    finding_recall=tp/(tp+fn) if tp+fn else 1.0
    finding_f1=2*finding_precision*finding_recall/(finding_precision+finding_recall) if finding_precision+finding_recall else 0.0
    micro_tp=sum(v['tp'] for v in field_counts.values()); micro_fp=sum(v['fp'] for v in field_counts.values()); micro_fn=sum(v['fn'] for v in field_counts.values())
    micro_p=micro_tp/(micro_tp+micro_fp) if micro_tp+micro_fp else (1.0 if sum(v['gold_count'] for v in field_counts.values())==0 else 0.0)
    micro_r=micro_tp/(micro_tp+micro_fn) if micro_tp+micro_fn else 1.0
    micro_f1=2*micro_p*micro_r/(micro_p+micro_r) if micro_p+micro_r else 0.0
    return {"records":n,"missing_responses":sum(predictions_by_uid.get(uid) is None for uid in gold_by_uid),
            "structural_validity":{"schema_valid_rate":schema_valid/n if n else 0,
            "grounding_valid_rate":grounding_valid/n if n else 0,"identifier_match_rate":identifier_matches/n if n else 0,
            "response_contract_valid_rate":contract_valid/n if n else 0},
            "field_metrics":field_counts,"field_micro":{"precision":micro_p,"recall":micro_r,"f1":micro_f1,"tp":micro_tp,"fp":micro_fp,"fn":micro_fn},
            "finding_metrics":{"gold_finding_count":finding['gold_count'],"predicted_finding_count":finding['predicted_count'],
                "exact_evidence_text_match":finding['exact_evidence_text_match'],"overlap_match":finding['overlap_match'],
                "precision":finding_precision,"recall":finding_recall,"f1":finding_f1},
            "claim_grounding":{"claims_checked":finding_claims_checked,"claims_grounded":finding_claims_grounded,
                "validity_rate":finding_claims_grounded/finding_claims_checked if finding_claims_checked else (1.0 if finding['gold_count']==0 else 0.0)},
            "rejected_extractions":rejected,"unsupported_field_count":unsupported_fields,"unsupported_finding_count":unsupported_findings,
            "invalid_offset_count":invalid_offsets,"orphan_anchor_count":orphan_anchors,"error_taxonomy":taxonomy,"record_results":records}


def quality_gate(metrics):
    structural=metrics['structural_validity']; finding=metrics['finding_metrics']; field=metrics['field_micro']
    checks={"missing_worker_responses":metrics.get('missing_responses',0)==0,
        "schema_valid_rate":structural['schema_valid_rate']==1,
        "grounding_valid_rate":structural['grounding_valid_rate']==1,
        "identifier_match_rate":structural['identifier_match_rate']==1,
        "response_contract_valid_rate":structural.get('response_contract_valid_rate',0)==1,
        "unsupported_evidence":metrics['unsupported_field_count']==0 and metrics['unsupported_finding_count']==0,
        "invalid_offsets":metrics['invalid_offset_count']==0,"orphan_anchors":metrics['orphan_anchor_count']==0,
        "field_micro_f1":field['f1']>=0.90,"finding_precision":finding['precision']>=0.95,
        "finding_recall":finding['recall']>=0.75}
    ok=all(checks.values())
    return {"status":"PASS" if ok else "FAIL","criteria":{"schema_valid_rate":1.0,"grounding_valid_rate":1.0,
            "identifier_match_rate":1.0,"unsupported_evidence":0,"invalid_offsets":0,"orphan_anchors":0,
            "response_contract_valid_rate":1.0,"field_micro_f1_min":0.90,"finding_precision_min":0.95,"finding_recall_min":0.75,
            "missing_worker_responses":0},"failed_checks":[k for k,v in checks.items() if not v]}
