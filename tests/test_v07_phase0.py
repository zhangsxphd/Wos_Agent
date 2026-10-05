import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.evidence.freeze_v07_datasets import metadata_only_row, stratified_select
from scripts.evidence.historical_holdout_postmortem import build as build_postmortem

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


class V07Phase0Tests(unittest.TestCase):
    def _row(self, i):
        return {'uid': f'WOS:{i:015d}', 'doi': f'10.1000/{i}', 'title': f'Title {i}',
                'publish_year': 2010 + i % 10, 'document_types': ['Article' if i % 3 else 'Review'],
                'source_title': f'Journal {i % 12}', 'matched_queries': [f'Q{i % 4}'], 'query_match_count': 1}

    def test_metadata_parser_skips_abstract_and_emits_allowlisted_fields(self):
        row = self._row(1)
        body = json.dumps({**row, 'abstract': 'PRIVATE SENTINEL ABSTRACT', 'nested': {'text': 'ignored'}}).encode()
        parsed, has_abstract = metadata_only_row(body)
        self.assertTrue(has_abstract)
        self.assertEqual(parsed, row)
        self.assertNotIn('abstract', parsed)
        self.assertNotIn('PRIVATE SENTINEL', repr(parsed))

    def test_deterministic_stratified_selection_excludes_reserved_identities(self):
        records = [self._row(i) for i in range(100)]
        reserved = {records[i]['uid'] for i in range(7)} | {records[i]['uid'] for i in range(20, 40)}
        eligible = [r for r in records if r['uid'] not in reserved]
        self.assertEqual(len(eligible), 73)
        dev1, _ = stratified_select(eligible, 40, 20261005)
        dev2, _ = stratified_select(eligible, 40, 20261005)
        future, _ = stratified_select([r for r in eligible if r['uid'] not in {x['uid'] for x in dev1}], 30, 20261005)
        dev_ids = {r['uid'] for r in dev1}
        future_ids = {r['uid'] for r in future}
        self.assertEqual(dev_ids, {r['uid'] for r in dev2})
        self.assertFalse(dev_ids & reserved)
        self.assertFalse(future_ids & reserved)
        self.assertEqual(len(dev_ids), 40)
        self.assertEqual(len(future_ids), 30)
        self.assertFalse(dev_ids & future_ids)

    def test_frozen_v06_hashes_are_unchanged(self):
        expected = {
            'prompts/evidence_extraction.md': 'f808bc64703e5d07cddebaefc5eafe5fe5aa24d123146a0924dd182fac98a0d1',
            'schemas/evidence_matrix.schema.json': '33e289f1b2911024f00748ac2580ab7d2926369e7f4ac2ef48b28da8573dae36',
            'docs/evidence_field_contract_v1.md': 'b667329e16edcf2e6ed0cc300518b45c39f9b8178f4257900762b084686e9e7b',
            'scripts/evidence/metrics.py': '4083eb1b15f2c11194b4266fe30fbea8535eb77ecf89f3acb3aad209e26ea11a',
            'scripts/evidence/metrics_v2.py': '54262802b4970429c1a218272145e229f44b9553f0dc5ba7cfceb070dfad4720',
            'data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl': 'fb58a888e06ee0efb7cba5de9d533b91582fb412697af2bf051ffb361fdf65c4',
        }
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(sha(ROOT / relative), digest)

    def test_postmortem_does_not_rewrite_frozen_benchmark_or_responses(self):
        gold = ROOT / 'data/evidence_benchmarks/v06_holdout20_gold_final/candidate_gold.jsonl'
        v1 = ROOT / 'data/reports/v06_holdout20_frozen_v1/v06_gold_benchmark_20261004_235122.json'
        v2 = ROOT / 'data/reports/v06_holdout20_frozen_v2/v06_holdout20_metric_v2_20261004.json'
        responses = sorted((ROOT / 'data/evidence_batches/v06_holdout20_eval_20261004/batch_001/responses').glob('*.json'))
        before = {p: sha(p) for p in [gold, v1, v2, *responses]}
        result = build_postmortem(ROOT)
        after = {p: sha(p) for p in before}
        self.assertEqual(before, after)
        self.assertEqual(result['v2_residual']['total_mismatches'], 166)
        self.assertEqual(result['soil_type_diagnostic']['gold'], 18)

    def test_future_dataset_identity_schema_has_no_abstract_field_when_frozen(self):
        identity_path = ROOT / 'data/evidence_benchmarks/v07_future_holdout30/identities.json'
        if not identity_path.exists():
            self.skipTest('dataset freeze has not run yet')
        identities = json.loads(identity_path.read_text())
        self.assertEqual(len(identities), 30)
        self.assertTrue(all('abstract' not in item for item in identities))
        dev = json.loads((ROOT / 'data/evidence_benchmarks/v07_dev40/identities.json').read_text())
        old = json.loads((ROOT / 'data/evidence_benchmarks/gold_v04/identities.json').read_text())
        historical = json.loads((ROOT / 'data/evidence_benchmarks/v06_holdout20/identities.json').read_text())
        def as_uids(value):
            if isinstance(value, dict): value = value.get('identities', value.get('uids'))
            return {row.get('uid') if isinstance(row, dict) else str(row) for row in value}
        future_ids = {x['uid'] for x in identities}
        dev_ids = {x['uid'] for x in dev}
        self.assertFalse(future_ids & dev_ids)
        self.assertFalse(future_ids & as_uids(old))
        self.assertFalse(future_ids & as_uids(historical))
        manifest = json.loads((ROOT / 'data/evidence_benchmarks/v07_future_holdout30/selection_manifest.json').read_text())
        self.assertTrue(manifest['V07_FUTURE_HOLDOUT_FROZEN_BEFORE_TUNING'])
        self.assertTrue(manifest['future_holdout_abstracts_accessed'])
        self.assertTrue(manifest['abstracts_materialized'])
        self.assertTrue(manifest['gold_created'])
        self.assertEqual(manifest['excluded_identity_count'], 67)


if __name__ == '__main__':
    unittest.main()
