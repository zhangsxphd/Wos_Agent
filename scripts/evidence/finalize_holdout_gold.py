"""Apply the user's final 14 grouped decisions to a new frozen holdout Gold."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.evidence.holdout_gold import validate_annotations
from scripts.extractors.schema_validator import factual_leaves

ROOT = Path(__file__).resolve().parents[2]
WORK = Path("data/evidence_benchmarks/v06_holdout20_gold_work")
SOURCE = WORK / "holdout_gold_candidate/candidate_gold.jsonl"
INPUT = WORK / "annotator_A/input.jsonl"
FINAL = Path("data/evidence_benchmarks/v06_holdout20_gold_final")
PROMPT_SHA = "f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1"
FROZEN = {
    "prompt": Path("prompts/evidence_extraction.md"),
    "schema": Path("schemas/evidence_matrix.schema.json"),
    "contract": Path("docs/evidence_field_contract_v1.md"),
    "metric_v1": Path("scripts/evidence/metrics.py"),
    "metric_v2": Path("scripts/evidence/metrics_v2.py"),
}

DECISIONS = {
    "WOS:000855107300001": ("ACCEPT_B", "study_system.soil_type; study_system.salinity_context", "Move the Review-scope descriptor to salinity_context; it is not soil classification."),
    "WOS:000744299800001": ("ACCEPT_B", "author_interpretations", "Retain the thermodynamic interpretation with its original abstract anchor and claim_type other."),
    "WOS:000933785000001": ("KEEP_UNKNOWN", "study_system.experimental_scale", "The abstract states field-scale, but frozen contract/validator cannot represent its support; preserve the known limitation."),
    "WOS:001067866000001": ("ACCEPT_B", "author_interpretations", "Retain the pyroligneous-vinegar hypothesis with its original abstract anchor."),
    "WOS:001019336000001": ("ACCEPT_B", "findings", "Use four atomic findings for flux comparisons and the CH4/mcrA association; preserve unrelated candidate findings."),
    "WOS:001047845400001": ("ACCEPT_B", "author_interpretations", "Retain the correlation-analysis interpretation with claim_type association."),
    "WOS:001276727600001": ("REJECT_B", "author_interpretations", "The summary is already represented as a result; avoid duplicating it as interpretation."),
    "WOS:001284298400001": ("MODIFY", "findings; author_interpretations", "Split the two exact abstract statements into atomic findings and retain the second as causal interpretation."),
    "WOS:001287055900001": ("REJECT_B", "author_interpretations", "The sentence precedes 'In this study' and is background/motivation."),
    "WOS:001421695800001": ("ACCEPT_B", "author_interpretations", "Retain both interpretations with claim_type other and original spans."),
    "WOS:001612769700001": ("ACCEPT_B", "author_interpretations", "Retain the soil-management interpretation with its original span."),
    "WOS:001834354200001": ("REJECT_A_AND_B", "study_system.salinity_context", "Motivation/application language is not linked to the Shanghai three-season field experiment."),
    "WOS:001751780600001": ("ACCEPT_B", "findings", "Use two separate findings for mechanism and evidence, preserving exact original spans."),
    "WOS:001790642700001": ("ACCEPT_A", "mechanisms_explicit", "Retain the coordinated mechanism summary; keep Na/K and antioxidant details among results/evidence."),
}


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _finding_from_source(source: dict, phrase: str):
    abstract = source["abstract"]
    start = abstract.index(phrase)
    return {"source": "abstract", "evidence_text": phrase, "start": start,
            "end": start + len(phrase), "claim": phrase, "certainty": "explicit"}


def _copy_items_for_claims(source: dict, key: str, phrases: list[str]):
    values = source["evidence"][key]
    selected = []
    for phrase in phrases:
        match = next((copy.deepcopy(item) for item in values
                      if phrase in (item if isinstance(item, str) else item.get("text", item.get("claim", "")))), None)
        if match is None:
            raise ValueError(f"source_item_missing:{source['uid']}:{key}:{phrase}")
        selected.append(match)
    return selected


def _replace_support_for_leaf(gold: dict, old_pointer: str, new_pointer: str | None,
                              source: dict, source_pointer: str | None = None):
    supports = gold.setdefault("evidence_support", {})
    old = supports.pop(old_pointer, None)
    if new_pointer is not None:
        value = (source or {}).get("evidence_support", {}).get(source_pointer or old_pointer, old)
        if value is None:
            raise ValueError(f"support_missing:{gold['uid']}:{source_pointer or old_pointer}")
        supports[new_pointer] = copy.deepcopy(value)


def apply_decisions(root: Path = ROOT, output: Path | None = None):
    root = Path(root).resolve()
    output = (root / (output or FINAL)).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite final Gold directory: {output}")

    source_dir = root / WORK
    candidates = _read_jsonl(root / SOURCE)
    a_rows = {row["uid"]: row for row in _read_jsonl(source_dir / "annotator_A/annotations.jsonl")}
    b_rows = {row["uid"]: row for row in _read_jsonl(source_dir / "annotator_B/annotations.jsonl")}
    canonical = {row["uid"]: row for row in _read_jsonl(root / Path("data/processed/saline_paddy_v056_full_enriched_20261003.jsonl"))}
    if len(candidates) != 20 or set(DECISIONS) - set(canonical):
        raise ValueError("holdout_candidate_or_canonical_identity_count")

    before = {row["uid"]: copy.deepcopy(row) for row in candidates}
    by_uid = {row["uid"]: row for row in candidates}
    logs = []

    def log(uid, before_value, after_value):
        decision, field, reason = DECISIONS[uid]
        logs.append({"uid": uid, "doi": by_uid[uid].get("doi"), "field": field,
                     "decision": decision, "before": before_value, "after": after_value,
                     "reason": reason, "approved_by": "human",
                     "approved_at": datetime.now(timezone.utc).isoformat(),
                     "human_reviewed": True})

    # H01: Review scope belongs in salinity_context, not soil_type.
    uid = "WOS:000855107300001"; row = by_uid[uid]; ev = row["evidence"]
    b_ev = b_rows[uid]["evidence"]
    old_soil = copy.deepcopy(ev["study_system"]["soil_type"])
    ev["study_system"]["soil_type"] = [x for x in old_soil if x != "salt-affected soil (SAS)"]
    ev["study_system"]["salinity_context"] = ["salt-affected soil (SAS)"]
    _replace_support_for_leaf(row, "/evidence/study_system/soil_type/0", None, None)
    row["evidence_support"]["/evidence/study_system/salinity_context/0"] = copy.deepcopy(
        b_ev and a_rows[uid]["evidence_support"]["/evidence/study_system/soil_type/0"])
    log(uid, {"soil_type": old_soil, "salinity_context": before[uid]["evidence"]["study_system"]["salinity_context"]},
        {"soil_type": ev["study_system"]["soil_type"], "salinity_context": ev["study_system"]["salinity_context"]})

    # H02 and H04/H06/H10/H11: select the user-approved B interpretation(s).
    for uid, phrases in {
        "WOS:000744299800001": ["thermodynamic parameters"],
        "WOS:001067866000001": ["Pyroligneous vinegar increased the ratio of nosZ/(nirS + nirK)"],
        "WOS:001047845400001": ["Furthermore, correlation analysis demonstrated"],
        "WOS:001421695800001": ["The increases in the fractal dimension", "These findings confirm"],
        "WOS:001612769700001": ["Our results indicate that implementing appropriate soil management measures"],
    }.items():
        row = by_uid[uid]
        old = copy.deepcopy(row["evidence"].get("author_interpretations", []))
        row["evidence"]["author_interpretations"] = _copy_items_for_claims(b_rows[uid], "author_interpretations", phrases)
        log(uid, old, row["evidence"]["author_interpretations"])

    # H03: Keep the frozen contract's representable value and record the limitation.
    uid = "WOS:000933785000001"; row = by_uid[uid]
    old = row["evidence"]["study_system"]["experimental_scale"]
    row["evidence"]["study_system"]["experimental_scale"] = "unknown"
    log(uid, old, "unknown")

    # H05: Keep unrelated candidate findings, add the four exact B atomic spans.
    uid = "WOS:001019336000001"; row = by_uid[uid]
    phrases = [
        "The results demonstrated that both the cumulative CH4 and NH3 fluxes in H treatment were significantly (p < 0.05) higher than L.",
        "While, the increasing saline‐alkali levels reduced the cumulative CO2 and N2O fluxes, respectively.",
        "Cumulative CH4 flux and the mcrA gene copy numbers showed a significant (p < 0.05) negative correlation.",
        "The gene copy number in H treatment was lower than M and L, respectively.",
    ]
    old = copy.deepcopy(row["evidence"]["findings"])
    extras = [x for x in old if not any(x.get("start") == b_rows[uid]["evidence"]["findings"][i]["start"] for i in (0, 1, 4, 5))]
    additions = [_finding_from_source(canonical[uid], phrase) for phrase in phrases]
    row["evidence"]["findings"] = sorted(extras + additions, key=lambda x: x["start"])
    log(uid, old, row["evidence"]["findings"])

    # H07 and H09: explicitly reject author interpretations.
    for uid in ("WOS:001276727600001", "WOS:001287055900001"):
        row = by_uid[uid]
        old = copy.deepcopy(row["evidence"].get("author_interpretations", []))
        row["evidence"]["author_interpretations"] = []
        log(uid, old, [])

    # H08: split two exact abstract findings; second also remains causal interpretation.
    uid = "WOS:001284298400001"; row = by_uid[uid]
    phrases = [
        "Soil carbon fixation was mainly realized by the reaction of exogenous calcium with CO2 generated by mineralization and converting it into calcium carbonate.",
        "pH and soil CO2 emission are the major controlling factors for soil inorganic carbon sequestration.",
    ]
    old_findings = copy.deepcopy(row["evidence"]["findings"])
    row["evidence"]["findings"] = [x for x in old_findings if x.get("start") not in {1103, 1260}]
    row["evidence"]["findings"].extend(_finding_from_source(canonical[uid], phrase) for phrase in phrases)
    row["evidence"]["findings"].sort(key=lambda x: x["start"])
    old_interp = copy.deepcopy(row["evidence"].get("author_interpretations", []))
    interp = next(x for x in row["evidence"]["findings"] if x["claim"] == phrases[1])
    row["evidence"]["author_interpretations"] = [{"text": interp["claim"], "claim_type": "causal",
        "source": "abstract", "anchor": {k: interp[k] for k in ("source", "evidence_text", "start", "end")}}]
    log(uid, {"findings": old_findings, "author_interpretations": old_interp},
        {"findings": row["evidence"]["findings"], "author_interpretations": row["evidence"]["author_interpretations"]})

    # H12: no study-linked salinity context is supported.
    uid = "WOS:001834354200001"; row = by_uid[uid]
    old = copy.deepcopy(row["evidence"]["study_system"]["salinity_context"])
    for index in range(len(old)):
        _replace_support_for_leaf(row, f"/evidence/study_system/salinity_context/{index}", None, None)
    row["evidence"]["study_system"]["salinity_context"] = []
    log(uid, old, [])

    # H13: two independent B findings replace the combined version(s), preserve unrelated results.
    uid = "WOS:001751780600001"; row = by_uid[uid]
    phrases = [
        "More importantly, PGPB alleviated salt-induced damage to the photosynthetic apparatus by stabilizing the photosystems and optimizing electron transport processes.",
        "This was evidenced by increases in the density of reaction centers per cross-section (RC/CSm) and the efficiencies of electron transfer to photosystem I (δRo and ΦRo).",
    ]
    old = copy.deepcopy(row["evidence"]["findings"])
    row["evidence"]["findings"] = [x for x in old if x.get("start") not in {698, 861}]
    row["evidence"]["findings"].extend(_finding_from_source(canonical[uid], phrase) for phrase in phrases)
    row["evidence"]["findings"].sort(key=lambda x: x["start"])
    log(uid, old, row["evidence"]["findings"])

    # H14: retain A's mechanism summary, not the separate measurement/result sentence.
    uid = "WOS:001790642700001"; row = by_uid[uid]
    old = copy.deepcopy(row["evidence"]["mechanisms_explicit"])
    selected = _copy_items_for_claims(a_rows[uid], "mechanisms_explicit", ["These results indicate that E. asburiae LL-1 enhances maize salt tolerance"])
    row["evidence"]["mechanisms_explicit"] = [x for x in selected if "Na+ accumulation" not in x]
    row["evidence_support"]["/evidence/mechanisms_explicit/0"] = copy.deepcopy(
        a_rows[uid]["evidence_support"]["/evidence/mechanisms_explicit/0"])
    log(uid, old, row["evidence"]["mechanisms_explicit"])

    # Re-index support for any list edits. H01 was explicitly remapped above; H12 is empty.
    # Preserve all untouched leaves and require a one-to-one support map before validation.
    rows_by_uid = {r["uid"]: r for r in _read_jsonl(root / INPUT)}
    for row in candidates:
        abstract = rows_by_uid[row["uid"]]["abstract"]
        support = row.get("evidence_support", {})
        expected = dict(factual_leaves(row.get("evidence", {})))
        # Only H01/H12 alter support-keyed list positions. Rebuild those pointers from exact source spans.
        if row["uid"] == "WOS:000855107300001":
            support["/evidence/study_system/salinity_context/0"] = {
                "source": "abstract", "evidence_text": "salt-affected soil (SAS)",
                "start": abstract.index("salt-affected soil (SAS)"),
                "end": abstract.index("salt-affected soil (SAS)") + len("salt-affected soil (SAS)"),
            }
        if row["uid"] == "WOS:001834354200001":
            support = {k: v for k, v in support.items() if not k.startswith("/evidence/study_system/salinity_context/")}
            row["evidence_support"] = support

    output.mkdir(parents=True)
    from scripts.evidence.holdout_gold import write_json, write_jsonl
    gold_path = output / "candidate_gold.jsonl"
    log_path = output / "human_decision_log.jsonl"
    write_jsonl(gold_path, candidates)
    write_jsonl(log_path, logs)
    validation = validate_annotations(gold_path, root / INPUT, root / "schemas/evidence_matrix.schema.json")
    if (validation["records"] != 20 or validation["schema_errors"] or validation["grounding_errors"]
            or validation["invalid_offsets"] or validation["orphan_anchors"] or validation["identity_errors"]
            or validation["unsupported"] or len(logs) != 14):
        raise ValueError("final_gold_validation_failed:" + json.dumps(validation, ensure_ascii=False))
    hashes = {key: _sha(root / path) for key, path in FROZEN.items()}
    if hashes["prompt"] != PROMPT_SHA:
        raise ValueError("frozen_prompt_hash_mismatch")
    manifest = {
        "gold_status": "human_approved_holdout_gold", "dataset_role": "historical_independent_holdout_gold",
        "holdout_count": 20, "human_review_groups": 14, "human_review_applied": True,
        "human_reviewed_groups": 14, "HOLDOUT_GOLD_FROZEN": True,
        "frozen_prompt_sha": PROMPT_SHA, "known_representation_limitation": True,
        "representation_limitations": [{"uid": "WOS:000933785000001", "doi": "10.3390/app13031436",
          "field": "experimental_scale", "value": "unknown", "reason": DECISIONS["WOS:000933785000001"][2]}],
        "validation": validation, "candidate_sha256": _sha(gold_path), "decision_log_sha256": _sha(log_path),
        "source_candidate_sha256": _sha(root / SOURCE), "frozen_hashes": hashes,
        "human_decision_log": "human_decision_log.jsonl",
    }
    write_json(output / "gold_freeze_manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=FINAL)
    args = parser.parse_args()
    print(json.dumps(apply_decisions(output=args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
