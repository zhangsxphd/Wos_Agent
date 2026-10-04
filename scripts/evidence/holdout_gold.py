"""Prepare and validate isolated, prompt-blind holdout Gold annotations."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path

from jsonschema import Draft202012Validator
from scripts.extractors.schema_validator import EvidenceValidator, factual_leaves
from scripts.pipeline_utils import ROOT

HOLDOUT = Path("data/evidence_benchmarks/v06_holdout20")
DEV_IDENTITIES = Path("data/evidence_benchmarks/gold_v04/identities.json")
CORPUS = Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl")
SCHEMA = Path("schemas/evidence_matrix.schema.json")
CONTRACT = Path("docs/evidence_field_contract_v1.md")
FROZEN_PROMPT_SHA = "f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1"
FIELDS = (
    "/evidence/study_system", "/evidence/treatments", "/evidence/measurements",
    "/evidence/methods", "/evidence/findings", "/evidence/mechanisms_explicit",
    "/evidence/limitations_explicit", "/evidence/author_interpretations",
)

INSTRUCTIONS = """# Independent holdout Gold annotation

This is a reference-annotation task, not model extraction. Annotate all 20 records
independently from their abstracts, using only this bundle's schema and Evidence
Field Contract v1. Do not search for, open, or use any other project files, prompts,
predictions, previous annotations, benchmark results, or user research context.

Return exactly one JSON object per input row in `annotations.jsonl`, preserving
input order. Each object must conform to `schema.json` and
copy `schema_version`, `uid`, `doi`, and `title` exactly. Populate the eight
contract fields: study_system, treatments, measurements, methods, findings,
mechanisms_explicit, limitations_explicit, and author_interpretations. Use
`screening.status="maybe"`; screening is not being performed. All inference arrays
must be empty. Omit optional provenance and completeness fields.

Every populated fact needs exact verbatim abstract support. Include integer
half-open character offsets into the exact abstract for every support and finding.
The value string itself must be a verbatim substring of its evidence_text; do not
prefix values with attribution labels or explanatory wrappers. Keep review
attribution in the source wording and in the annotation context. Put each factual
leaf's support only at its matching `/evidence/...` evidence_support pointer;
author_interpretations must keep their anchor nested in the item and must not be
duplicated in evidence_support.
For experimental_scale, do not infer `field` from the word `field`, `field-scale`,
`paddy field`, or a statement that work occurred in fields. Use `field` only for
an explicit field study/experiment/trial, field-based study, or in-situ monitoring;
use `pot`, `greenhouse`, `lab`, or `model` only when directly stated as the study
setting. Contract v1 maps an incubation experiment to `lab`, while the frozen
grounding validator does not accept incubation wording. In that specific conflict
use `unknown` in the annotation; the controller will preserve the conflict for
human review rather than changing the contract or validator.
Do not infer missing details. Use [] / null / `unknown` when unsupported. For
findings preserve the author's actual reported claim and attribution; do not add
background, objectives, methods, recommendations, or implications as findings.
For Review articles, describe reviewed scope and attribute synthesis; do not
represent reviewed interventions as treatments applied by the review authors.

Do not write any output other than `annotations.jsonl`. A controller will validate
schema, identity, grounding, offsets, support anchors, and empty inference.
"""


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity_rows(path: Path):
    data = read_json(path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rows = data.get("identities", data.get("uids"))
        if isinstance(rows, list):
            return rows
    raise ValueError("identity_registry_shape")


def _uid(row):
    return row["uid"] if isinstance(row, dict) else str(row)


def validate_registry(root: Path = ROOT):
    ids = identity_rows(root / HOLDOUT / "identities.json")
    dev = identity_rows(root / DEV_IDENTITIES)
    selection = read_json(root / HOLDOUT / "selection_manifest.json")
    hold_uids = [_uid(row) for row in ids]
    dev_uids = {_uid(row) for row in dev}
    if len(hold_uids) != 20 or len(set(hold_uids)) != 20:
        raise ValueError("holdout_identity_count")
    if set(hold_uids) & dev_uids:
        raise ValueError("development_overlap")
    if selection.get("sample_size") != 20 or selection.get("HOLDOUT_FROZEN_BEFORE_PROMPT_ITERATION_2") is not True:
        raise ValueError("selection_manifest_freeze")
    if selection.get("holdout_abstracts_included") is not False or selection.get("holdout_evidence_created") is not False or selection.get("holdout_responses_created") is not False:
        raise ValueError("selection_manifest_initial_state")
    return ids


def prepare(root: Path = ROOT, output_dir: Path | None = None):
    ids = validate_registry(root)
    prompt_bytes = (root / "prompts/evidence_extraction.md").read_bytes()
    if sha(prompt_bytes) != FROZEN_PROMPT_SHA:
        raise ValueError("frozen_prompt_sha_mismatch")
    source_rows = read_jsonl(root / CORPUS)
    by_uid = {}
    hold_uids = {_uid(item) for item in ids}
    for row in source_rows:
        if row.get("uid") in hold_uids:
            if row["uid"] in by_uid:
                raise ValueError("duplicate_canonical_uid")
            by_uid[row["uid"]] = row
    if set(by_uid) != hold_uids:
        raise ValueError("canonical_identity_mismatch")
    output_dir = output_dir or root / HOLDOUT / "../v06_holdout20_gold_work"
    output_dir = output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(output_dir)
    for who in ("annotator_A", "annotator_B"):
        bundle = output_dir / who
        bundle.mkdir(parents=True)
        inputs = []
        for identity in ids:
            record = by_uid[_uid(identity)]
            if not record.get("abstract") or record.get("doi") != identity.get("doi"):
                raise ValueError("canonical_abstract_or_doi_mismatch")
            inputs.append({"uid": record["uid"], "doi": record.get("doi"), "title": record.get("title"),
                           "journal": record.get("source_title"), "year": record.get("publish_year"),
                           "document_types": record.get("document_types", []), "abstract": record["abstract"]})
        write_jsonl(bundle / "input.jsonl", inputs)
        (bundle / "ANNOTATION_INSTRUCTIONS.md").write_text(INSTRUCTIONS, encoding="utf-8")
        shutil.copyfile(root / SCHEMA, bundle / "schema.json")
        shutil.copyfile(root / CONTRACT, bundle / "evidence_field_contract_v1.md")
        write_json(bundle / "bundle_manifest.json", {
            "dataset_role": "independent_holdout_gold_annotation_input",
            "annotator_id": who,
            "session_type": "fresh_ephemeral_codex_session",
            "record_count": len(inputs),
            "uids": [r["uid"] for r in inputs],
            "input_sha256": sha((bundle / "input.jsonl").read_bytes()),
            "schema_sha256": sha((bundle / "schema.json").read_bytes()),
            "contract_sha256": sha((bundle / "evidence_field_contract_v1.md").read_bytes()),
            "extraction_prompt_included": False,
            "model_predictions_included": False,
            "development_materials_included": False,
        })
    return {"output_dir": str(output_dir), "record_count": 20,
            "development_overlap": 0, "prompt_sha256_unchanged": True}


def repair_unique_offsets(annotation, record):
    """Fill only absent/wrong offsets when exact support text has one unique location."""
    abstract = record.get("abstract") or ""
    repairs = 0
    for _, fact in factual_leaves(annotation.get("evidence", {})):
        support = annotation.get("evidence_support", {}).get(_)
        if support and support.get("source") == "abstract":
            text = support.get("evidence_text", "")
            positions = [m.start() for m in re.finditer(re.escape(text), abstract)]
            if len(positions) == 1 and (support.get("start") != positions[0] or support.get("end") != positions[0] + len(text)):
                support["start"], support["end"] = positions[0], positions[0] + len(text)
                repairs += 1
    for finding in annotation.get("evidence", {}).get("findings", []):
        if finding.get("source") == "abstract":
            text = finding.get("evidence_text", "")
            positions = [m.start() for m in re.finditer(re.escape(text), abstract)]
            if len(positions) == 1 and (finding.get("start") != positions[0] or finding.get("end") != positions[0] + len(text)):
                finding["start"], finding["end"] = positions[0], positions[0] + len(text)
                repairs += 1
    return repairs


def validate_annotations(annotation_path: Path, input_path: Path, schema_path: Path):
    annotations, inputs = read_jsonl(annotation_path), read_jsonl(input_path)
    if len(annotations) != 20 or len(inputs) != 20:
        raise ValueError("annotation_record_count")
    validator = EvidenceValidator(schema_path)
    schema = read_json(schema_path)
    json_validator = Draft202012Validator(schema)
    errors = []
    counts = {"schema_errors": 0, "grounding_errors": 0, "invalid_offsets": 0,
              "orphan_anchors": 0, "identity_errors": 0, "unsupported": 0}
    for i, (ann, record) in enumerate(zip(annotations, inputs)):
        schema_errors = list(json_validator.iter_errors(ann))
        counts["schema_errors"] += len(schema_errors)
        if schema_errors:
            errors.append({"index": i, "uid": record["uid"], "error": "schema"}); continue
        if any(ann.get(k) != record.get(k) for k in ("uid", "doi", "title")):
            counts["identity_errors"] += 1
            errors.append({"index": i, "error": "identity"}); continue
        if ann.get("screening", {}).get("status") != "maybe":
            errors.append({"index": i, "error": "screening"}); continue
        if any(ann.get("inference", {}).values()):
            errors.append({"index": i, "error": "inference_not_empty"}); continue
        factual = dict(factual_leaves(ann.get("evidence", {})))
        support = ann.get("evidence_support", {})
        expected = set(factual)
        counts["orphan_anchors"] += len(set(support) - expected)
        counts["orphan_anchors"] += len(expected - set(support))
        for pointer, value in factual.items():
            source = support.get(pointer, {})
            quote = source.get("evidence_text", "")
            if quote and str(value).casefold() not in quote.casefold():
                counts["unsupported"] += 1
            start, end = source.get("start"), source.get("end")
            if source.get("source") == "abstract" and (type(start) is not int or type(end) is not int or (record.get("abstract") or "")[start:end] != quote):
                counts["invalid_offsets"] += 1
        for finding in ann.get("evidence", {}).get("findings", []):
            start, end, quote = finding.get("start"), finding.get("end"), finding.get("evidence_text", "")
            if type(start) is not int or type(end) is not int or (record.get("abstract") or "")[start:end] != quote:
                counts["invalid_offsets"] += 1
        try:
            validator.validate(ann, record)
        except Exception as exc:
            counts["grounding_errors"] += 1
            errors.append({"index": i, "uid": record["uid"], "error": str(exc)})
    return {"records": len(annotations), "valid": len(annotations) - len(errors),
            **counts,
            "schema_grounding_offsets_or_anchor_errors": errors}


def _norm(value):
    return " ".join(str(value).casefold().split())


def _category(pointer):
    parts = pointer.split("/")
    if len(parts) > 2 and parts[1] == "evidence":
        if parts[2] in {"findings", "author_interpretations"}:
            return "/evidence/" + parts[2]
        return "/evidence/" + parts[2]
    return pointer


def _items(annotation):
    out = []
    for pointer, value in factual_leaves(annotation.get("evidence", {})):
        support = annotation.get("evidence_support", {}).get(pointer, {})
        out.append({"field": pointer, "value": value, "text": support.get("evidence_text", ""),
                    "start": support.get("start"), "end": support.get("end")})
    for f in annotation.get("evidence", {}).get("findings", []):
        out.append({"field": "/evidence/findings", "value": f.get("claim"),
                    "text": f.get("evidence_text", ""), "start": f.get("start"), "end": f.get("end"),
                    "certainty": f.get("certainty")})
    for f in annotation.get("evidence", {}).get("author_interpretations", []):
        a = f.get("anchor", {})
        out.append({"field": "/evidence/author_interpretations", "value": f.get("text"),
                    "text": a.get("evidence_text", ""), "start": a.get("start"), "end": a.get("end"),
                    "claim_type": f.get("claim_type")})
    return out


def _boundary_equivalent(x, y):
    if _category(x["field"]) != _category(y["field"]):
        return False
    tx, ty = _norm(x.get("text", "")), _norm(y.get("text", ""))
    vx, vy = _norm(x.get("value", "")), _norm(y.get("value", ""))
    if not tx or not ty or not (tx in ty or ty in tx):
        return False
    if x["field"] == "/evidence/findings":
        return vx == vy
    # For ordinary facts, different spans are considered equivalent only when
    # each reported value is exactly its own verbatim span and one span contains
    # the other. This avoids semantic/fuzzy matching and protects claim changes.
    return vx == tx and vy == ty


def compare_pair(a, b):
    ai, bi = _items(a), _items(b)
    result = {field: {"exact": 0, "boundary_equivalent": 0, "A_only": 0, "B_only": 0,
                      "category_disagreement": 0} for field in FIELDS}
    unmatched_a, unmatched_b = set(range(len(ai))), set(range(len(bi)))
    agreements = []
    for i in list(unmatched_a):
        for j in list(unmatched_b):
            if _category(ai[i]["field"]) == _category(bi[j]["field"]) and _norm(ai[i]["value"]) == _norm(bi[j]["value"]) and _norm(ai[i]["text"]) == _norm(bi[j]["text"]):
                result[_category(ai[i]["field"])]["exact"] += 1; agreements.append((ai[i], bi[j], "exact")); unmatched_a.remove(i); unmatched_b.remove(j); break
    for i in list(unmatched_a):
        for j in list(unmatched_b):
            x, y = ai[i], bi[j]
            if _boundary_equivalent(x, y):
                result[_category(x["field"])]["boundary_equivalent"] += 1; agreements.append((x, y, "boundary_equivalent")); unmatched_a.remove(i); unmatched_b.remove(j); break
    categories = []
    for i in list(unmatched_a):
        for j in list(unmatched_b):
            x, y = ai[i], bi[j]
            if _category(x["field"]) != _category(y["field"]) and _norm(x["value"]) == _norm(y["value"]) and _norm(x["text"]) == _norm(y["text"]):
                result[_category(x["field"])]["category_disagreement"] += 1
                result[_category(y["field"])]["category_disagreement"] += 1
                categories.append((x, y))
                unmatched_a.remove(i); unmatched_b.remove(j); break
    for i in unmatched_a: result[_category(ai[i]["field"])]["A_only"] += 1
    for i in unmatched_b: result[_category(bi[i]["field"])]["B_only"] += 1
    return result, [ai[i] for i in sorted(unmatched_a)], [bi[i] for i in sorted(unmatched_b)], categories, agreements


def _abstract_context(abstract, items, sentences_each_side=1):
    bounds = [0]
    bounds.extend(m.end() for m in re.finditer(r"(?<=[.!?])\s+", abstract))
    bounds.append(len(abstract))
    spans = []
    for item in items:
        start, end = item.get("start"), item.get("end")
        if type(start) is int and type(end) is int:
            ix = next((i for i in range(len(bounds)-1) if bounds[i] <= start < bounds[i+1]), None)
            if ix is not None:
                lo, hi = max(0, ix-sentences_each_side), min(len(bounds)-2, ix+sentences_each_side)
                spans.append((bounds[lo], bounds[hi+1]))
    if not spans:
        return {"text": abstract[:1200], "start": 0}
    start, end = min(s for s, _ in spans), max(e for _, e in spans)
    return {"text": abstract[start:end], "start": start}


def _contract_rule(contract_text, item):
    parts = item.get("field", "").split("/")
    if len(parts) > 2 and parts[2] in ("study_system", "treatments", "measurements") and len(parts) > 3:
        term = parts[2] + "." + parts[3]
    elif len(parts) > 2:
        term = parts[2]
    else:
        return "Use explicit abstract evidence only; otherwise leave empty or unknown."
    for line in contract_text.splitlines():
        if f"`{term}`" in line:
            return line
    return "Use explicit abstract evidence only; otherwise leave empty or unknown."


def disagreement_items(a_rows, b_rows, input_rows, contract_text):
    """Deterministically create only A/B mismatches; no extraction predictions are read."""
    packet = []
    for a, b, source in zip(a_rows, b_rows, input_rows):
        stats, a_only, b_only, category_pairs, _ = compare_pair(a, b)
        grouped = {}
        for side, items in (("A", a_only), ("B", b_only)):
            for item in items:
                grouped.setdefault(_category(item["field"]), {"A": [], "B": []})[side].append(item)
        for cat, sides in grouped.items():
            sample = (sides["A"] or sides["B"])[0]
            packet.append({"item_id": f"{source['uid']}:{len(packet)+1:03d}", "uid": source["uid"],
                           "doi": source.get("doi"), "field": cat, "issue_type": "presence_or_value_disagreement",
                           "abstract_context": _abstract_context(source["abstract"], sides["A"] + sides["B"]),
                           "annotator_A": sides["A"], "annotator_B": sides["B"],
                           "contract_rule": _contract_rule(contract_text, sample)})
        for x, y in category_pairs:
            packet.append({"item_id": f"{source['uid']}:{len(packet)+1:03d}", "uid": source["uid"],
                           "doi": source.get("doi"), "field": f"{x['field']} <> {y['field']}",
                           "issue_type": "category_disagreement",
                           "abstract_context": _abstract_context(source["abstract"], [x, y]),
                           "annotator_A": [x], "annotator_B": [y],
                           "contract_rule": _contract_rule(contract_text, x) + "\n" + _contract_rule(contract_text, y)})
    return packet


def agreement_summary(a_rows, b_rows):
    totals = {field: {"exact": 0, "boundary_equivalent": 0, "category_disagreement": 0,
                      "A_only": 0, "B_only": 0} for field in FIELDS}
    for a, b in zip(a_rows, b_rows):
        per, _a, _b, _c, _agree = compare_pair(a, b)
        for field in FIELDS:
            for metric in totals[field]:
                totals[field][metric] += per[field][metric]
    return totals


def _empty_annotation(base):
    return {"schema_version": base["schema_version"], "uid": base["uid"], "doi": base.get("doi"),
            "title": base.get("title"), "screening": base["screening"],
            "evidence": {
                "study_system": {"crop": [], "soil_type": [], "salinity_context": [], "location": None, "experimental_scale": "unknown"},
                "treatments": {k: [] for k in ("irrigation", "water_regime", "amendments", "fertilization", "biological_treatments", "other_treatments")},
                "measurements": {k: [] for k in ("soil_physical", "soil_chemical", "carbon", "nitrogen", "microbial", "greenhouse_gases", "plant_growth", "yield", "water_use", "other")},
                "methods": [], "findings": [], "mechanisms_explicit": [], "limitations_explicit": [], "author_interpretations": [],
            }, "evidence_support": {}, "inference": base["inference"]}


def _add_resolved_item(out, item):
    parts = item["field"].split("/")
    if len(parts) < 3 or parts[1] != "evidence":
        raise ValueError("resolved_item_field")
    group = parts[2]
    name = parts[3] if len(parts) > 3 else None
    text, start, end = item.get("text", ""), item.get("start"), item.get("end")
    support = {"source": "abstract", "evidence_text": text, "start": start, "end": end}
    if group == "findings":
        out["evidence"]["findings"].append({"source": "abstract", "evidence_text": text, "start": start, "end": end,
                                             "claim": item["value"], "certainty": item.get("certainty", "explicit")})
    elif group == "author_interpretations":
        out["evidence"]["author_interpretations"].append({"text": item["value"], "claim_type": item.get("claim_type", "other"),
                                                           "source": "abstract", "anchor": support})
    elif group in {"methods", "mechanisms_explicit", "limitations_explicit"}:
        values = out["evidence"][group]
        if item["value"] not in values:
            values.append(item["value"])
            out["evidence_support"][f"/evidence/{group}/{len(values)-1}"] = support
    elif group == "study_system" and name in ("location", "experimental_scale"):
        out["evidence"][group][name] = item["value"]
        if item["value"] not in (None, "", "unknown"):
            out["evidence_support"][f"/evidence/{group}/{name}"] = support
    else:
        values = out["evidence"][group][name]
        if item["value"] not in values:
            values.append(item["value"])
            out["evidence_support"][f"/evidence/{group}/{name}/{len(values)-1}"] = support


def assemble_candidate(a_rows, b_rows, input_rows, issues, c_rows, schema_path: Path | None = None):
    """Merge A/B agreement plus explicit C decisions into schema records."""
    decisions = {r["item_id"]: r for r in c_rows}
    item_validator = EvidenceValidator(schema_path) if schema_path else None
    out_rows, audit, review = [], [], []
    issues_by_uid = {}
    for issue in issues: issues_by_uid.setdefault(issue["uid"], []).append(issue)
    for a, b, source in zip(a_rows, b_rows, input_rows):
        stats, a_only, b_only, category_pairs, agreements = compare_pair(a, b)
        resolved = [x for x, _y, _kind in agreements]
        uid_issues = issues_by_uid.get(source["uid"], [])
        for issue in uid_issues:
            decision = decisions.get(issue["item_id"], {})
            choice = decision.get("decision")
            if choice in ("A", "B"):
                selected = issue[f"annotator_{choice}"]
                resolved.extend(selected)
                audit.append({"uid": issue["uid"], "doi": issue.get("doi"), "field": issue["field"],
                              "item_id": issue["item_id"], "decision": choice,
                              "adjudicator_consensus": True, "reason": decision.get("reason", "")})
            elif choice == "MODIFY" and isinstance(decision.get("resolved_items"), list):
                resolved.extend(decision["resolved_items"])
                audit.append({"uid": issue["uid"], "doi": issue.get("doi"), "field": issue["field"],
                              "item_id": issue["item_id"], "decision": "MODIFY",
                              "adjudicator_consensus": True, "reason": decision.get("reason", "")})
            else:
                review.append({**issue, "adjudicator_C": decision or {"decision": "NEEDS_HUMAN_REVIEW", "reason": "No valid C resolution."}})
        accepted = []
        for item in resolved:
            if item_validator:
                probe = _empty_annotation(a)
                _add_resolved_item(probe, item)
                try:
                    item_validator.validate(probe, source)
                except Exception as exc:
                    review.append({"uid": source["uid"], "doi": source.get("doi"), "field": item["field"],
                                   "issue_type": "agreed_item_rejected_by_frozen_validator",
                                   "abstract_context": _abstract_context(source["abstract"], [item]),
                                   "annotator_A": [item], "annotator_B": [item],
                                   "adjudicator_C": {"decision": "NEEDS_HUMAN_REVIEW", "reason": str(exc)},
                                   "contract_rule": _contract_rule((ROOT / CONTRACT).read_text(encoding="utf-8"), item)})
                    continue
            accepted.append(item)
        out = _empty_annotation(a)
        for item in accepted: _add_resolved_item(out, item)
        # Ensure all accepted source spans and offsets correspond to this exact abstract.
        for item in resolved:
            start, end, text = item.get("start"), item.get("end"), item.get("text", "")
            if type(start) is not int or type(end) is not int or source["abstract"][start:end] != text:
                raise ValueError(f"candidate_invalid_anchor:{source['uid']}")
        out_rows.append(out)
    return out_rows, audit, review


def normalize_c_decisions(issues, c_rows):
    allowed = {"A", "B", "MODIFY", "NEEDS_HUMAN_REVIEW"}
    by_id = {r.get("item_id"): r for r in c_rows if isinstance(r, dict)}
    normalized = []
    for issue in issues:
        row = by_id.get(issue["item_id"], {})
        valid_modify = (row.get("decision") != "MODIFY" or
                        (isinstance(row.get("resolved_items"), list) and bool(row["resolved_items"])))
        if row.get("decision") not in allowed or not str(row.get("reason", "")).strip() or not valid_modify:
            normalized.append({"item_id": issue["item_id"], "decision": "NEEDS_HUMAN_REVIEW",
                               "reason": "C output was missing or did not satisfy the adjudication contract."})
        else:
            normalized.append(row)
    return normalized


def write_candidate(root: Path = ROOT):
    work = root / HOLDOUT.parent / "v06_holdout20_gold_work"
    A, B, I = (read_jsonl(work / "annotator_A/annotations.jsonl"),
               read_jsonl(work / "annotator_B/annotations.jsonl"),
               read_jsonl(work / "annotator_A/input.jsonl"))
    issues = read_jsonl(work / "disagreement_items.jsonl")
    c_rows = read_jsonl(work / "adjudicator_C/adjudications.jsonl")
    decisions = normalize_c_decisions(issues, c_rows)
    records, c_audit, review = assemble_candidate(A, B, I, issues, decisions,
                                                    root / SCHEMA)
    # Full ordered identity match is mandatory before writing the candidate.
    frozen = identity_rows(root / HOLDOUT / "identities.json")
    if [r.get("uid") for r in records] != [_uid(x) for x in frozen]:
        raise ValueError("candidate_identity_order_mismatch")
    candidate_dir = work / "holdout_gold_candidate"
    candidate_dir.mkdir(exist_ok=False)
    candidate_path = candidate_dir / "candidate_gold.jsonl"
    write_jsonl(candidate_path, records)
    validation = validate_annotations(candidate_path, work / "annotator_A/input.jsonl",
                                      root / SCHEMA)
    write_jsonl(candidate_dir / "adjudicator_decisions.jsonl", c_audit)
    write_jsonl(candidate_dir / "needs_human_review.jsonl", review)
    manifest = {
        "dataset_role": "holdout_gold_candidate",
        "record_count": len(records), "identity_count": len(frozen),
        "human_approved_gold": False, "independent_holdout": True,
        "adjudicator_consensus_count": len(c_audit),
        "needs_human_review_count": len(review),
        "validation": {k: validation[k] for k in ("records", "valid", "schema_errors", "grounding_errors", "invalid_offsets", "orphan_anchors", "identity_errors", "unsupported")},
        "schema_sha256": sha((root / SCHEMA).read_bytes()),
        "contract_sha256": sha((root / CONTRACT).read_bytes()),
        "annotator_A_input_sha256": sha((work / "annotator_A/input.jsonl").read_bytes()),
        "annotator_B_input_sha256": sha((work / "annotator_B/input.jsonl").read_bytes()),
        "annotator_A_output_sha256": sha((work / "annotator_A/annotations.jsonl").read_bytes()),
        "annotator_B_output_sha256": sha((work / "annotator_B/annotations.jsonl").read_bytes()),
        "adjudicator_C_input_sha256": sha((work / "adjudicator_C/disagreement_items.jsonl").read_bytes()),
        "adjudicator_C_output_sha256": sha((work / "adjudicator_C/adjudications.jsonl").read_bytes()),
        "prediction_provenance_present": False,
    }
    write_json(candidate_dir / "manifest.json", manifest)
    return manifest, review


def write_human_review(review, output: Path):
    lines = ["# Holdout 20 Gold — Human Review", "",
             "Only unresolved items are listed, grouped by paper to keep the user review packet compact. The records remain a `holdout_gold_candidate`, not a human-approved Gold.", ""]
    groups = {}
    for row in review: groups.setdefault(row["uid"], []).append(row)
    for i, (uid, group) in enumerate(groups.items(), 1):
        first = group[0]
        contexts = []
        for row in group:
            for sentence in re.split(r"(?<=[.!?])\s+", row["abstract_context"]["text"].strip()):
                if sentence and sentence not in contexts: contexts.append(sentence)
        lines.extend([f"## #H{i:02d}", f"UID: {uid}", f"DOI: {first.get('doi') or ''}",
                      f"Field: {'; '.join(dict.fromkeys(r['field'] for r in group))}",
                      "Abstract context: " + " ".join(contexts[:3])])
        for side in ("A", "B"):
            lines.append(f"Annotator {side}:")
            for row in group:
                lines.append(f"- {row['field']}: {json.dumps(row.get('annotator_' + side, []), ensure_ascii=False)}")
        lines.append("Adjudicator C:")
        for row in group:
            c = row.get("adjudicator_C", {})
            lines.append(f"- {row['field']}: {c.get('decision', 'NOT_REVIEWED_BY_C')} — {c.get('reason', '')}")
        lines.append("Contract rule:")
        for rule in dict.fromkeys(r.get("contract_rule", "") for r in group):
            lines.append(f"- {rule}")
        lines.append("Recommended action:")
        for row in group:
            decision = row.get("adjudicator_C", {}).get("decision")
            recommended = {"A": "ACCEPT_A", "B": "ACCEPT_B", "MODIFY": "ACCEPT_C"}.get(decision, "MODIFY")
            lines.append(f"- {row['field']}: {recommended}")
        lines.append("")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("prepare")
    v = sub.add_parser("validate"); v.add_argument("--annotator", required=True, choices=("annotator_A", "annotator_B"))
    args = ap.parse_args()
    if args.cmd == "prepare": print(json.dumps(prepare(), ensure_ascii=False))
    elif args.cmd == "validate":
        base = ROOT / HOLDOUT.parent / "v06_holdout20_gold_work" / args.annotator
        print(json.dumps(validate_annotations(base / "annotations.jsonl", base / "input.jsonl", base / "schema.json"), ensure_ascii=False))


if __name__ == "__main__":
    main()
