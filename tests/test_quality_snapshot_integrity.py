import json
import tempfile
import unittest
from pathlib import Path

from scripts.quality.snapshot import context,digest,fingerprints,verify_snapshot
from scripts.pipeline_utils import write_jsonl


class SnapshotIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.canonical=self.root/'canonical.jsonl';self.inventory=self.root/'inventory.jsonl';self.rows_path=self.root/'rows.jsonl'
        write_jsonl(self.canonical,[{'uid':'U1'}]);write_jsonl(self.inventory,[{'uid':'U1'}])
        (self.root/'raw.pdf').write_bytes(b'original');(self.root/'parsed.json').write_text('{}')
        self.row={'uid':'U1','raw_file':'raw.pdf','parsed_file':'parsed.json'}
        self.row['observed_file_sha256']=fingerprints(self.root,self.row);write_jsonl(self.rows_path,[self.row])
        self.snapshot={'inputs':{'canonical_sha256':digest(self.canonical),'inventory_sha256':digest(self.inventory)},
            'snapshot_integrity':{'version':'quality-snapshot-v2','quality_rows_sha256':digest(self.rows_path),'validation_context':context()}}

    def verify(self):return verify_snapshot(self.snapshot,self.rows_path,self.canonical,self.inventory,[self.row],self.root)

    def test_valid_then_raw_or_parsed_change_is_rejected(self):
        self.assertTrue(self.verify());(self.root/'raw.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'snapshot_source_changed'):self.verify()

    def test_quality_rows_and_context_changes_are_rejected(self):
        (self.root/'rows.jsonl').write_text('{}\n')
        with self.assertRaisesRegex(ValueError,'quality_rows_hash_mismatch'):self.verify()
        self.rows_path.write_text(json.dumps(self.row)+'\n');self.snapshot['snapshot_integrity']['quality_rows_sha256']=digest(self.rows_path)
        self.snapshot['snapshot_integrity']['validation_context']={}
        with self.assertRaisesRegex(ValueError,'snapshot_validation_context_changed'):self.verify()

    def test_legacy_snapshot_and_missing_inventory_are_not_trusted(self):
        with self.assertRaisesRegex(ValueError,'snapshot_unbound'):verify_snapshot({},self.rows_path,self.canonical,self.inventory,[self.row],self.root)
        with self.assertRaisesRegex(ValueError,'inventory_hash_mismatch'):verify_snapshot(self.snapshot,self.rows_path,self.canonical,None,[self.row],self.root)


if __name__=='__main__':unittest.main()
