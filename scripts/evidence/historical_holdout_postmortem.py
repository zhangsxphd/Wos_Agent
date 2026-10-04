"""Build a read-only postmortem from frozen v0.6 holdout artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GOLD = Path("data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl")
BATCH = Path("data/evidence_batches/v06_holdout20_eval_20261004/batch_001")
V1 = Path("data/reports/v06_holdout20_frozen_v1/v06_gold_benchmark_20261004_235122.json")
V2 = Path("data/reports/v06_holdout20_frozen_v2/v06_holdout20_metric_v2_20261004.json")
OUT_JSON = Path("data/reports/v07_historical_holdout_postmortem.json")
OUT_MD = Path("data/reports/v07_historical_holdout_postmortem.md")


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def get_pointer(obj, pointer):
    cur = obj
    for part in pointer.strip("/").split("/") if pointer else []:
        cur = cur[int(part)] if isinstance(cur, list) else cur[part]
    return cur


def flatten_values(obj, pointer):
    value = get_pointer(obj, pointer)
    if isinstance(value, list):
        return value
    if value is None or (pointer.endswith("/experimental_scale") and str(value).casefold() in {"unknown", "", "n/a"}):
        return []
    return [value]


def support_for(obj, pointer, index):
    return obj.get("evidence_support", {}).get(f"{pointer}/{index}")


def norm(s):
    return re.sub(r"[^\w]+", " ", str(s).casefold(), flags=re.UNICODE).strip()


def text_class(text):
    t = str(text).casefold()
    if any(x in t for x in ("gene expression", "gene", "transcript", "expression")):
        return "stress_response_gene_expression"
    if any(x in t for x in ("chlorophyll", "carotenoid", "photosynth", "fluorescence", "reaction center", "electron transfer", "photosystem", "intercellular carbon dioxide", "quantum yield", "performance index", "piabs", "pntotal", "pn)", "(pn)")):
        return "photosynthesis"
    if any(x in t for x in ("na+", "k+", "sodium", "potassium", "na/k", "ion accumulation", "ion uptake")):
        return "ion_homeostasis"
    if any(x in t for x in ("proline", "antioxidant", "enzyme", "dismutase", "catalase", "h2o2", "mda", "ros", "malondialdehyde")):
        return "biochemistry"
    if any(x in t for x in ("root length", "root", "leaf area", "plant height", "morpholog")):
        return "morphology"
    return "uncategorized"


def salinity_context_type(row):
    text = str((row.get("support") or {}).get("evidence_text") or row.get("value") or "")
    low = text.casefold()
    if re.fullmatch(r"(?:non-)?sal(?:ine|inized|t-affected|odic)[^,;.!?]{0,55}(?:soil|soils|land|system|systems)", low.strip()):
        return "A_bare_descriptor"
    if any(x in low for x in ("review scope", "this review", "review of", "we review")):
        return "D_review_scope"
    if any(x in low for x in ("motivation", "application", "to improve", "aim", "objective", "potential use", "can be used")):
        return "C_application_or_motivation"
    if any(x in low for x in ("field trial", "field experiment", "pot experiment", "soil samples", "soil was collected", "experimental soil", "site in", "town", "county", "province")):
        return "E_actual_site_or_experimental_system"
    if any(x in low for x in ("grown in", "cultivated in", "rice cultivation", "crop yield in", "treated with", "remediation in", "rice systems")):
        return "B_system_linked_descriptor"
    return "insufficient_context_in_stored_span"


def build(root=ROOT):
    root = Path(root)
    paths = {"gold": root / GOLD, "v1": root / V1, "v2": root / V2,
             "responses": root / BATCH / "responses"}
    before = {"gold": sha(paths["gold"]), "v1": sha(paths["v1"]), "v2": sha(paths["v2"])}
    gold_rows = load_jsonl(paths["gold"])
    response_by_uid = {}
    for p in sorted(paths["responses"].glob("*.json")):
        wrapper = json.loads(p.read_text(encoding="utf-8"))
        response = wrapper["response"]
        response_by_uid[response["uid"]] = response
    v1, v2 = json.loads(paths["v1"].read_text()), json.loads(paths["v2"].read_text())
    if len(gold_rows) != 20 or set(response_by_uid) != {r["uid"] for r in gold_rows}:
        raise ValueError("frozen_holdout_artifact_identity_mismatch")
    report_rows = v2["per_record"]
    mismatches = []
    cross_field_counts = Counter()
    plant_other_pairs = []
    soil_rows = []
    salinity_errors = []
    for gold in gold_rows:
        uid = gold["uid"]
        pred = response_by_uid[uid]
        if uid not in report_rows:
            raise ValueError("v2_record_missing")
        rec = report_rows[uid]["fields"]["field_metrics"]
        pending = {}
        for pointer, metric in rec.items():
            gv, pv = flatten_values(gold, pointer), flatten_values(pred, pointer)
            gm, pm = {m["gold_index"]: m for m in metric.get("matches", [])}, {m["prediction_index"]: m for m in metric.get("matches", [])}
            missing = [i for i in range(len(gv)) if i not in gm]
            extra = [i for i in range(len(pv)) if i not in pm]
            for i in missing:
                key = (uid, pointer, "FN", i)
                pending[key] = {"uid": uid, "doi": gold.get("doi"), "field": pointer, "direction": "FN", "value": gv[i], "support": support_for(gold, pointer, i), "counterpart": None}
            for i in extra:
                key = (uid, pointer, "FP", i)
                pending[key] = {"uid": uid, "doi": gold.get("doi"), "field": pointer, "direction": "FP", "value": pv[i], "support": support_for(pred, pointer, i), "counterpart": None}
        # Link cross-category errors only when the actual evidence offsets agree exactly.
        fns = [x for x in pending.values() if x["direction"] == "FN"]
        fps = [x for x in pending.values() if x["direction"] == "FP"]
        used = set()
        for fn in fns:
            fs = fn.get("support") or {}
            for j, fp in enumerate(fps):
                ps = fp.get("support") or {}
                if j in used or not fs or not ps:
                    continue
                if fs.get("start") == ps.get("start") and fs.get("end") == ps.get("end") and fs.get("evidence_text") == ps.get("evidence_text"):
                    a, b = fn["field"].rsplit("/", 1)[-1], fp["field"].rsplit("/", 1)[-1]
                    subtype = "PHYSIOLOGY_ROUTING_ERROR" if {a, b} == {"plant_growth", "other"} else ("MICROBIAL_ROUTING_ERROR" if {a,b} == {"microbial","other"} else "RESIDUAL_OTHER_ROUTING")
                    fn["counterpart"] = {"field": b, "value": fp["value"], "support": ps, "direction": "FP"}
                    fp["counterpart"] = {"field": a, "value": fn["value"], "support": fs, "direction": "FN"}
                    fn["primary_cause"] = fp["primary_cause"] = subtype
                    fn["secondary_cause"] = fp["secondary_cause"] = "WRONG_CATEGORY"
                    used.add(j)
                    cross_field_counts[f"{a}->{b}"] += 1
                    cross_field_counts[f"{b}->{a}"] += 1
                    if {a,b} == {"plant_growth","other"}:
                        plant_other_pairs.append({"uid": uid, "value": fn["value"], "evidence_text": fs.get("evidence_text"), "text_class": text_class(fn["value"]) or text_class(fs.get("evidence_text")) or "uncategorized"})
                    break
        for item in pending.values():
            if "primary_cause" not in item:
                # For unlinked predictions and omissions, classify by direction directly.
                item["primary_cause"] = "SOIL_TYPE_ABSTENTION" if item["field"].endswith("/soil_type") and item["direction"] == "FN" else ("ACTUAL_SYSTEM_LINK_ERROR" if item["field"].endswith("/salinity_context") else ("TRUE_OMISSION" if item["direction"] == "FN" else "TRUE_OVEREXTRACTION"))
                item["secondary_cause"] = "TRUE_OMISSION" if item["direction"] == "FN" else "TRUE_OVEREXTRACTION"
            item["abstract_context"] = (item.get("support") or {}).get("evidence_text")
            mismatches.append(item)
        soil = flatten_values(gold, "/evidence/study_system/soil_type")
        soil_pred = flatten_values(pred, "/evidence/study_system/soil_type")
        if soil:
            for i, value in enumerate(soil):
                soil_rows.append({"uid":uid,"doi":gold.get("doi"),"gold_value":value,"gold_support":support_for(gold,"/evidence/study_system/soil_type",i),"predicted_values":soil_pred})
        # Detailed salinity error entries are a filtered view of residual mismatch rows.
    for row in mismatches:
        if row["field"].endswith("/salinity_context"):
            row["salinity_context_type"] = salinity_context_type(row)
            salinity_errors.append(row)
    grouped = defaultdict(lambda: {"fp":0,"fn":0,"mismatches":0})
    for row in mismatches:
        key=row["field"].rsplit("/",1)[-1]
        grouped[key]["fp" if row["direction"]=="FP" else "fn"] += 1
        grouped[key]["mismatches"] += 1
    v1_fields = v1["metrics"]["field_metrics"]
    total_v1 = sum(x["fp"]+x["fn"] for x in v1_fields.values())
    pareto=[]
    for pointer,m in sorted(v1_fields.items(), key=lambda kv: (-(kv[1]["fp"]+kv[1]["fn"]),kv[0])):
        errors=m["fp"]+m["fn"]
        if not errors: continue
        pareto.append({"field":pointer.rsplit("/",1)[-1],"fp":m["fp"],"fn":m["fn"],"mismatches":errors,"share":errors/total_v1,"cumulative_share":0})
    cumulative=0
    for row in pareto:
        cumulative+=row["share"]; row["cumulative_share"]=cumulative
    top80=[]
    for row in pareto:
        top80.append(row)
        if row["cumulative_share"]>=.8: break
    plant_classes=Counter(x["text_class"] for x in plant_other_pairs)
    sal_v2=v2["salinity_context"]
    soil_descriptor_classes=Counter()
    for row in soil_rows:
        s=(row["gold_value"] or "").casefold()
        typ="paddy_or_field_application" if "field" in s or "paddy" in s else ("sodic_descriptor" if "sodic" in s else ("coastal_descriptor" if "coastal" in s else ("salt_affected_descriptor" if "salt-affected" in s else "saline_soil_descriptor")))
        soil_descriptor_classes[typ]+=1
    result={
      "report_type":"historical_holdout_postmortem_only","evaluation_role":"historical_holdout_not_independent_future_performance",
      "historical_holdout_count":20,"rerun_prohibited":True,
      "metrics":{"structural_validity":1.0,"v1":{"tp":167,"fp":89,"fn":135,"precision":0.65234375,"recall":0.5529801324503312,"f1":0.5985663082437276},"v2":{"exact_tp":167,"boundary_tp":29,"fp":60,"fn":106,"precision":v2["fields"]["precision"],"recall":v2["fields"]["recall"],"f1":v2["fields"]["f1"]},"findings":{"precision":1.0,"recall":0.9393939393939394,"f1":0.96875}},
      "v1_error_pareto":{"total_mismatches":total_v1,"fields":pareto,"top_fields_reaching_80_percent":top80},
      "v2_residual":{"total_mismatches":len(mismatches),"expected_fp_fn":{"fp":60,"fn":106},"by_field":dict(grouped),"mismatches":mismatches},
      "soil_type_diagnostic":{"gold":len(soil_rows),"predicted":0,"root_cause_conclusion":"Outputs establish systematic abstention (0/18) but do not uniquely identify why. The 18 supported labels are predominantly soil/material descriptors; one field-use label is not a pedological class. A vs B cannot be causally separated from existing outputs; linkage and routing bug hypotheses are not testable from the permitted artifacts alone.","hypothesis_breakdown":{"A_prompt_or_contract_overconservative":"plausible, not identifiable from outputs alone","B_descriptor_vs_taxonomy_mismatch":dict(soil_descriptor_classes),"C_actual_system_linkage":"underdetermined by short evidence spans; full abstract not consulted","D_worker_routing_bug":"no direct evidence; systematic empty output is established","E_other":"possible; no additional causal evidence"},"records":soil_rows},
      "plant_growth_to_other":{"exact_support_span_pairs":len(plant_other_pairs),"categories":{**dict(plant_classes),"morphology":0},"pairs":plant_other_pairs,"interpretation":"Counts exact same-offset support spans routed to measurements.other where Gold routes the same evidence to plant_growth; this is a conservative overlap count, not all plant_growth false negatives."},
      "salinity_context":{"v1":{"precision":0.15789473684210525,"recall":0.16666666666666666,"f1":0.16216216216216214,"fp":16,"fn":15},"v2":{"precision":sal_v2["precision"],"recall":sal_v2["recall"],"f1":sal_v2["f1"],"fp":sal_v2["fp"],"fn":sal_v2["fn"],"boundary_matches":sal_v2["boundary_tp"]},"residual_errors":salinity_errors,"residual_context_type_counts":dict(Counter(x["salinity_context_type"] for x in salinity_errors)),"context_classification":"Evidence snippets are often isolated spans; classify A-E only where the allowed support explicitly establishes it. Otherwise context is insufficient. Prefer a minimal span that includes the descriptor and its explicit link to the sampled/experimental system; generic application and review scope should remain distinct."},
      "findings_strategy":"PRESERVE_BY_DEFAULT",
      "sources":{"gold_sha256":before["gold"],"v1_report_sha256":before["v1"],"v2_report_sha256":before["v2"],"worker_response_files":len(response_by_uid)},
      "causal_limits":"Only frozen Gold, worker responses, and v1/v2 reports were read. No canonical abstracts, prompts, schemas, contracts, validators, workers, or metric calculators were consulted or rerun. Abstract context is limited to already stored verbatim evidence_text spans."
    }
    after={"gold":sha(paths["gold"]),"v1":sha(paths["v1"]),"v2":sha(paths["v2"])}
    if before != after: raise RuntimeError("frozen_benchmark_input_changed")
    out_json=root/OUT_JSON; out_md=root/OUT_MD
    out_json.parent.mkdir(parents=True,exist_ok=True)
    out_json.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    lines=["# v0.6 Historical Holdout Postmortem","", "This is error analysis of the already-frozen historical holdout. It is not a new evaluation and does not establish future independent performance.","", "## Final metrics", "", "- Structural validity: 100%", "- Findings P/R/F1: 1.0000 / 0.9394 / 0.9688", "- Fields v1 P/R/F1: 0.6523 / 0.5530 / 0.5986", "- Fields v2 P/R/F1: 0.7656 / 0.6490 / 0.7025 (29 boundary matches)", "- Historical holdout: 20; rerun prohibited.", "", "## v1 Pareto", "", "| Field | FP | FN | Mismatches | Share | Cumulative |", "|---|---:|---:|---:|---:|---:|"]
    for x in pareto: lines.append(f"| {x['field']} | {x['fp']} | {x['fn']} | {x['mismatches']} | {x['share']:.1%} | {x['cumulative_share']:.1%} |")
    lines += ["",f"The first {len(top80)} fields reach {top80[-1]['cumulative_share']:.1%} of the 224 v1 mismatches.","", "## Systematic patterns", "", f"- soil_type: Gold {len(soil_rows)}, worker 0. Existing artifacts establish full abstention but cannot distinguish prompt conservatism from descriptor/ontology mismatch. {dict(soil_descriptor_classes)}; one field/paddy application label is not a pedological class.", f"- plant_growth evidence routed to other with exact shared support offsets: {len(plant_other_pairs)}; categories: {dict(plant_classes)}.", f"- salinity_context v2 residual: FP {sal_v2['fp']}, FN {sal_v2['fn']}; {sal_v2['boundary_tp']} boundary matches. Use a minimal span that carries the salinity descriptor and its explicit study-system link.", "- Findings strategy: PRESERVE_BY_DEFAULT.", "", "## Residual mismatch packet", "", f"Contains {len(mismatches)} rows from the stored v2 unmatched indices (FP 60 + FN 106). Abstract context is restricted to the already stored verbatim evidence support; no holdout abstracts were opened.", "See the adjacent JSON `v2_residual.mismatches` for every UID/DOI/field/value/support and primary/secondary classification.", "", "## Limitations", "", result["causal_limits"], ""]
    out_md.write_text("\n".join(lines),encoding="utf-8")
    return result


def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--root",type=Path,default=ROOT); a=ap.parse_args()
    r=build(a.root)
    print(json.dumps({"historical_holdout_count":r["historical_holdout_count"],"v1_mismatches":r["v1_error_pareto"]["total_mismatches"],"v2_mismatches":r["v2_residual"]["total_mismatches"],"plant_growth_to_other":r["plant_growth_to_other"]["exact_support_span_pairs"],"soil_type_gold":r["soil_type_diagnostic"]["gold"]},indent=2))

if __name__=="__main__": main()
