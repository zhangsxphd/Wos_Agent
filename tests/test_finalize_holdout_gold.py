import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence.finalize_holdout_gold import DECISIONS, apply_decisions


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/evidence_benchmarks/v06_holdout20_gold_work"
SOURCE = WORK / "holdout_gold_candidate/candidate_gold.jsonl"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FinalizeHoldoutGoldTests(unittest.TestCase):
    def test_user_decisions_apply_to_new_gold_and_leave_sources_immutable(self):
        immutable = [SOURCE, WORK / "annotator_A/annotations.jsonl", WORK / "annotator_B/annotations.jsonl",
                     ROOT / "data/evidence_benchmarks/v06_holdout20/identities.json"]
        before_hashes = {path: digest(path) for path in immutable}
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "gold"
            manifest = apply_decisions(ROOT, out)
            gold = {row["uid"]: row for row in rows(out / "candidate_gold.jsonl")}
            decisions = rows(out / "human_decision_log.jsonl")

            self.assertEqual(len(gold), 20)
            self.assertEqual(len(decisions), 14)
            self.assertEqual({row["uid"] for row in decisions}, set(DECISIONS))
            self.assertTrue(all(row["human_reviewed"] and row["approved_by"] == "human" for row in decisions))
            self.assertTrue(all(row["approved_at"] and row["decision"] for row in decisions))
            self.assertEqual(manifest["gold_status"], "human_approved_holdout_gold")
            self.assertTrue(manifest["HOLDOUT_GOLD_FROZEN"])
            self.assertEqual(manifest["validation"]["valid"], 20)
            self.assertEqual(manifest["validation"]["schema_errors"], 0)
            self.assertEqual(manifest["validation"]["grounding_errors"], 0)
            self.assertEqual(manifest["validation"]["invalid_offsets"], 0)
            self.assertEqual(manifest["validation"]["orphan_anchors"], 0)
            self.assertEqual(manifest["validation"]["identity_errors"], 0)
            self.assertEqual(manifest["validation"]["unsupported"], 0)
            self.assertEqual(before_hashes, {path: digest(path) for path in immutable})

            h01 = gold["WOS:000855107300001"]["evidence"]["study_system"]
            self.assertNotIn("salt-affected soil (SAS)", h01["soil_type"])
            self.assertEqual(h01["salinity_context"], ["salt-affected soil (SAS)"])
            h03 = gold["WOS:000933785000001"]["evidence"]["study_system"]
            self.assertEqual(h03["experimental_scale"], "unknown")
            h05 = gold["WOS:001019336000001"]["evidence"]["findings"]
            self.assertTrue(any("both the cumulative CH4 and NH3 fluxes" in x["claim"] and x["end"] == 730 for x in h05))
            self.assertTrue(any("reduced the cumulative CO2 and N2O fluxes" in x["claim"] and x["start"] == 731 for x in h05))
            h08 = gold["WOS:001284298400001"]["evidence"]
            self.assertTrue(any(x["start"] == 1103 for x in h08["findings"]))
            self.assertTrue(any(x["start"] == 1260 for x in h08["findings"]))
            self.assertTrue(any(x["claim_type"] == "causal" and x["anchor"]["start"] == 1260 for x in h08["author_interpretations"]))
            h13 = gold["WOS:001751780600001"]["evidence"]["findings"]
            self.assertTrue(any(x["start"] == 698 and x["end"] == 860 for x in h13))
            self.assertTrue(any(x["start"] == 861 and x["end"] == 1028 for x in h13))

    def test_refuses_to_overwrite_existing_final_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "gold"
            out.mkdir()
            with self.assertRaises(FileExistsError):
                apply_decisions(ROOT, out)


if __name__ == "__main__":
    unittest.main()
