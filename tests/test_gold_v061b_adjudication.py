"""Offline regressions for safe legacy finding migration to v0.6.1b."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from scripts.evidence.adjudicate_gold_v061b import (
    build_candidate,
    migrate_legacy_findings,
    validate_candidate,
)
from scripts.extractors.base import empty_record
from scripts.extractors.schema_validator import EvidenceValidator


def source(uid="WOS:DEV1", doi="10.1000/dev1", text="The treatment increased rice yield."):
    return {"uid": uid, "doi": doi, "title": "Development item", "abstract": text,
            "authors": [], "source_title": "Test Journal", "publish_year": 2025,
            "document_types": ["Article"], "matched_queries": [], "query_match_count": 0,
            "provenance_history": []}


def legacy_row(record):
    row = empty_record(record)
    text = record["abstract"]
    start = text.index(text)
    row["evidence"]["findings"] = [{"source": "abstract", "evidence_text": text,
        "start": start, "end": start + len(text), "claim": text}]
    return row


class LegacyFindingMigrationTests(unittest.TestCase):
    def test_legacy_finding_migration_adds_only_deterministic_certainty(self):
        record = source()
        before = legacy_row(record)
        after, changes = migrate_legacy_findings([before], {record["uid"]: record}, {record["uid"]: [0]})
        old_finding = before["evidence"]["findings"][0]
        new_finding = after[0]["evidence"]["findings"][0]
        self.assertEqual({k: v for k, v in new_finding.items() if k != "certainty"}, old_finding)
        self.assertEqual(new_finding["certainty"], "explicit")
        self.assertEqual(changes[0]["repair_type"], "LEGACY_EXPLICIT_CERTAINTY_MIGRATION")
        self.assertEqual(changes[0]["finding_index"], 0)

    def test_migration_fails_closed_if_claim_is_not_directly_supported(self):
        record = source()
        row = legacy_row(record)
        row["evidence"]["findings"][0]["claim"] = "A stronger unsupported result."
        with self.assertRaisesRegex(ValueError, "not eligible"):
            migrate_legacy_findings([row], {record["uid"]: record}, {record["uid"]: [0]})

    def test_repaired_finding_is_schema_valid(self):
        record = source()
        repaired, _ = migrate_legacy_findings([legacy_row(record)], {record["uid"]: record}, {record["uid"]: [0]})
        schema_path = Path("schemas/evidence_matrix.schema.json")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(repaired[0])), [])

    def test_repaired_finding_grounding_is_unchanged(self):
        record = source()
        before = legacy_row(record)
        repaired, _ = migrate_legacy_findings([before], {record["uid"]: record}, {record["uid"]: [0]})
        old = copy.deepcopy(before["evidence"]["findings"][0])
        new = repaired[0]["evidence"]["findings"][0]
        self.assertEqual(old["evidence_text"], new["evidence_text"])
        self.assertEqual(old["claim"], new["claim"])
        self.assertEqual((old["start"], old["end"]), (new["start"], new["end"]))
        self.assertIs(EvidenceValidator().validate(repaired[0], record), repaired[0])

    def test_full_candidate_schema_and_all_validation_gates_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            uids = ["WOS:DEV1", "WOS:DEV2", "WOS:DEV3"]
            (root / "data/evidence_benchmarks/gold_v04").mkdir(parents=True)
            (root / "data/evidence_benchmarks/gold_v04/identities.json").write_text(json.dumps(uids))
            manifest_path = root / "data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001/batch_manifest.json"
            manifest_path.parent.mkdir(parents=True)
            requests_dir = manifest_path.parent / "requests"
            requests_dir.mkdir()
            requests, rows = [], []
            for i, uid in enumerate(uids, 1):
                item = source(uid, f"10.1000/dev{i}")
                request_path = requests_dir / f"{uid}.json"
                request_path.write_text(json.dumps(item))
                requests.append({"uid": uid, "request_file": str(request_path.relative_to(root))})
                rows.append(legacy_row(item))
            manifest_path.write_text(json.dumps({"requests": requests}))

            source_dir = root / "v061a"
            source_dir.mkdir()
            source_file = source_dir / "candidate_gold.jsonl"
            source_file.write_text("".join(json.dumps(row) + "\n" for row in rows))
            original_gold = root / "original_gold.jsonl"
            strict_v1 = root / "strict_v1.json"
            metric_v2 = root / "metric_v2.json"
            for path in (original_gold, strict_v1, metric_v2):
                path.write_text("immutable fixture\n")
            frozen_hashes = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in (source_file, original_gold, strict_v1, metric_v2)}
            schema_path = Path("schemas/evidence_matrix.schema.json").resolve()
            output = root / "v061b"
            changes, report = build_candidate(root, source_dir, output, original_gold,
                strict_v1, metric_v2, schema_path, {uid: [0] for uid in uids})

            self.assertEqual(len(changes), 3)
            self.assertEqual(report, {"record_count": 3, "schema_errors": 0, "grounding_errors": 0,
                "invalid_offsets": 0, "orphan_anchors": 0, "identity_errors": 0})
            for path, digest in frozen_hashes.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            candidate = [json.loads(line) for line in (output / "candidate_gold.jsonl").read_text().splitlines()]
            self.assertEqual(validate_candidate(candidate,
                {uid: json.loads((requests_dir / f"{uid}.json").read_text()) for uid in uids}, schema_path), report)


if __name__ == "__main__":
    unittest.main()
