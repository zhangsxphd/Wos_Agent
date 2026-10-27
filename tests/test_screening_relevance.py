import unittest

from scripts.pipeline_utils import ROOT
from scripts.screening_profile import classify, load_profile


class ScreeningRelevanceTests(unittest.TestCase):
    def test_unrelated_review_and_model_are_not_automatically_included(self):
        profile = load_profile(ROOT / 'profiles/saline_paddy.yaml')
        base = {'abstract': 'An abstract exists.', 'document_types': ['Review']}
        evidence = {'evidence': {'study_system': {'experimental_scale': 'unknown'}}}
        review = classify(base, evidence, profile)
        self.assertEqual(review['eligibility_status'], 'uncertain')
        self.assertEqual(review['evidence_role'], 'review_synthesis')
        evidence['evidence']['study_system']['experimental_scale'] = 'model'
        model = classify(dict(base, document_types=['Article']), evidence, profile)
        self.assertEqual(model['eligibility_status'], 'uncertain')
        self.assertEqual(model['evidence_role'], 'regional_context')


if __name__ == '__main__':
    unittest.main()
