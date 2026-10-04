"""Offline tests for applying the eleven explicit human decisions."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence.finalize_development_gold import DECISIONS, finalize
from scripts.extractors.base import empty_record
from scripts.extractors.schema_validator import EvidenceValidator


U1 = "WOS:001487641900001"
U2 = "WOS:001602265100001"
U3 = "WOS:001713822800001"
U4 = "WOS:001798349200001"
U5 = "WOS:001831944400001"
U6 = "WOS:001859510400001"
U7 = "WOS:DEV_UNUSED"
ROWS = {
    U1: ("10.1016/j.agwat.2025.109503", [
        ("/evidence/study_system/soil_type", ["soda saline-alkali land"]),
        ("/evidence/study_system/salinity_context", ["soda saline-alkali land"])]),
    U2: ("10.3390/w17202974", [
        ("/evidence/study_system/soil_type", ["saline-affected paddy fields", "saline-affected upland fields"]),
        ("/evidence/study_system/salinity_context", ["saline-affected paddy fields", "saline-affected upland fields"])]),
    U3: ("10.3390/su18052185", [
        ("/evidence/study_system/soil_type", ["saline–alkali paddy fields"]),
        ("/evidence/study_system/salinity_context", ["saline–alkali paddy fields"])]),
    U4: ("10.1016/j.agwat.2026.110526", [
        ("/evidence/study_system/soil_type", ["saline-alkali soils"])]),
    U5: ("10.3390/gels12070592", [
        ("/evidence/study_system/soil_type", ["saline–alkali soils"])]),
    U6: ("10.3390/agriculture16161786", [
        ("/evidence/measurements/yield", ["yield components"])]),
    U7: ("10.1000/unused", []),
}


def pointer_parent(row, pointer):
    parts = pointer.strip("/").split("/")
    parent = row
    for part in parts[:-1]:
        parent = parent[part]
    return parent, parts[-1]


def fixtures(root):
    ids = list(ROWS)
    (root / "data/evidence_benchmarks/gold_v04").mkdir(parents=True)
    (root / "data/evidence_benchmarks/gold_v04/identities.json").write_text(json.dumps(ids))
    batch = root / "data/evidence_batches/v06_gold_blind_iteration1_20261004/batch_001"
    requests_dir = batch / "requests"
    requests_dir.mkdir(parents=True)
    requests, candidates = [], []
    source_rows = {}
    for uid, (doi, fields) in ROWS.items():
        quote = " | ".join(value for _, values in fields for value in values) or "Development abstract."
        source = {"uid": uid, "doi": doi, "title": f"Title {uid}", "abstract": quote,
                  "authors": [], "source_title": "Test journal", "publish_year": 2025,
                  "document_types": ["Article"], "matched_queries": [], "query_match_count": 0,
                  "provenance_history": []}
        source_rows[uid] = source
        request_path = requests_dir / f"{len(requests)}.json"
        request_path.write_text(json.dumps(source, ensure_ascii=False))
        requests.append({"uid": uid, "request_file": str(request_path.relative_to(root))})
        row = empty_record(source)
        for pointer, values in fields:
            parent, key = pointer_parent(row, pointer)
            parent[key] = values
            for index, value in enumerate(values):
                start = quote.index(value)
                row["evidence_support"][f"{pointer}/{index}"] = {
                    "source": "abstract", "evidence_text": value,
                    "start": start, "end": start + len(value)}
        candidates.append(row)
    (batch / "batch_manifest.json").write_text(json.dumps({"requests": requests}))
    source_dir = root / "v061b"
    source_dir.mkdir()
    source_file = source_dir / "candidate_gold.jsonl"
    source_file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in candidates))
    gold = root / "original_gold.jsonl"
    gold.write_text("frozen fixture\n")
    return source_dir, source_file, gold, source_rows


class FinalDevelopmentGoldTests(unittest.TestCase):
    def test_all_decisions_apply_and_log_human_approval_without_mutating_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source_dir, source_file, original_gold, sources = fixtures(root)
            frozen = {path: hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in (source_file, original_gold)}
            out = root / "final"
            validation, log = finalize(root, source_dir, out, original_gold,
                                       Path("schemas/evidence_matrix.schema.json").resolve(),
                                       approved_at="2026-10-04T08:27:10Z")
            self.assertEqual(len(DECISIONS), len(log))
            self.assertTrue(all(item["human_reviewed"] and item["approved_by"] == "human" for item in log))
            self.assertEqual({item["decision"] for item in log}, {"ACCEPT", "REJECT"})
            self.assertEqual(validation, {"record_count": 7, "schema_errors": 0,
                "grounding_errors": 0, "invalid_offsets": 0, "orphan_anchors": 0, "identity_errors": 0})
            for path, digest in frozen.items():
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

            final = {row["uid"]: row for row in map(json.loads,
                (out / "candidate_gold.jsonl").read_text().splitlines())}
            self.assertEqual(final[U1]["evidence"]["study_system"]["soil_type"], [])
            self.assertEqual(final[U1]["evidence"]["study_system"]["salinity_context"], ["soda saline-alkali land"])
            self.assertEqual(final[U2]["evidence"]["study_system"]["soil_type"], [])
            self.assertEqual(final[U2]["evidence"]["study_system"]["salinity_context"],
                             ["saline-affected paddy fields", "saline-affected upland fields"])
            self.assertEqual(final[U3]["evidence"]["study_system"]["soil_type"], [])
            self.assertEqual(final[U3]["evidence"]["study_system"]["salinity_context"], [])
            self.assertEqual(final[U4]["evidence"]["study_system"]["soil_type"], [])
            self.assertEqual(final[U4]["evidence"]["study_system"]["salinity_context"], [])
            self.assertEqual(final[U5]["evidence"]["study_system"]["soil_type"], [])
            self.assertEqual(final[U6]["evidence"]["measurements"]["yield"], [])
            self.assertEqual(final[U6]["evidence_support"], {})
            for row in final.values():
                EvidenceValidator().validate(row, sources[row["uid"]])


if __name__ == "__main__":
    unittest.main()
