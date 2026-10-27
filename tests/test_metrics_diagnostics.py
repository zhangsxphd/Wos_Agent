import unittest

from scripts.evidence.metrics_diagnostics import demonstrate


class FrozenMetricDiagnosticTests(unittest.TestCase):
    def test_synthetic_counterexamples_remain_visible_without_changing_frozen_metric(self):
        result = demonstrate()
        self.assertEqual(result['forward_matches'], 1)
        self.assertEqual(result['reversed_matches'], 2)
        self.assertTrue(result['negation_pair_matched'])


if __name__ == '__main__':
    unittest.main()
