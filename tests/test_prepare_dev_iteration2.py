"""The iteration-2 batch is built only from seven manifested dev requests."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence.batch_prepare import canonical_json
from scripts.evidence.contract_reconcile_v061a import DEV_IDS, DEV_REQUEST_MANIFEST
from scripts.evidence.prepare_dev_iteration2 import prepare


DEV_UIDS = [
    "WOS:001631731300001", "WOS:001859510400001", "WOS:001831944400001",
    "WOS:001798349200001", "WOS:001713822800001", "WOS:001602265100001",
    "WOS:001487641900001",
]


class IterationTwoPreparationTests(unittest.TestCase):
    def test_batch_contains_only_seven_development_requests_and_new_prompt_hash(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / DEV_IDS).parent.mkdir(parents=True)
            (root / DEV_IDS).write_text(json.dumps(DEV_UIDS))
            batch = root / DEV_REQUEST_MANIFEST
            request_dir = batch.parent / "requests"
            request_dir.mkdir(parents=True)
            items = []
            for index, uid in enumerate(DEV_UIDS):
                request = {"uid": uid, "doi": f"10.1000/dev{index}", "title": f"Dev {index}",
                    "journal": "J", "year": 2025, "authors": [], "keywords": [],
                    "document_types": ["Article"], "abstract": f"Development abstract {index}."}
                path = request_dir / f"{index}.json"
                path.write_text(json.dumps(request))
                items.append({"uid": uid, "request_file": str(path.relative_to(root))})
            batch.write_text(json.dumps({"requests": items}))
            prompt = root / "prompts/evidence_extraction.md"
            prompt.parent.mkdir()
            prompt.write_bytes(Path("prompts/evidence_extraction.md").read_bytes())

            result = prepare(root, Path("data/evidence_batches/i2"))
            run = json.loads((root / "data/evidence_batches/i2/manifest.json").read_text())
            worker_batch = json.loads((root / run["batches"][0]["batch_manifest"]).read_text())
            self.assertEqual(result["request_count"], 7)
            self.assertFalse(result["holdout_loaded"])
            self.assertEqual(run["prompt_iteration"], 2)
            self.assertEqual({row["uid"] for row in worker_batch["requests"]}, set(DEV_UIDS))
            self.assertEqual(len(list((root / "data/evidence_batches/i2/batch_001/requests").glob("*.json"))), 7)
            self.assertNotEqual(result["prompt_sha256"], "7bff1857e5609df79163d363665466c1ba04f46047bfa8321042f44140c1072f")
            for row in worker_batch["requests"]:
                request = json.loads((root / row["request_file"]).read_text())
                unsigned = dict(request); digest = unsigned.pop("request_sha256")
                self.assertEqual(hashlib.sha256(canonical_json(unsigned)).hexdigest(), digest)
                self.assertNotIn("gold", request)


if __name__ == "__main__":
    unittest.main()
