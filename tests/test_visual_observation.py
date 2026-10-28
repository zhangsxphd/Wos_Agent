import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.quality.pdf_anomalies import validate_observation


class VisualObservationTests(unittest.TestCase):
    def test_observation_is_bound_to_raw_and_preview_and_never_human_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'preview.png').write_bytes(b'preview')
            record={'uid':'U1','doi':'10.1234/a'}
            obs={'uid':'U1','doi':'10.1234/a','raw_sha256':'abc','preview_file':'preview.png',
                 'preview_sha256':hashlib.sha256(b'preview').hexdigest(),
                 'review_type':'assistant_visual_first_page','reviewer':'Codex assistant',
                 'reviewed_at':'2026-10-28T00:00:00+00:00','conclusion':'title visible'}
            self.assertEqual(validate_observation(obs,record,'abc',root)['status'],'assistant_observation_valid')
            self.assertEqual(validate_observation(obs,record,'different',root)['status'],'stale_or_invalid')
            (root/'preview.png').write_bytes(b'changed')
            self.assertEqual(validate_observation(obs,record,'abc',root)['status'],'stale_or_invalid')


if __name__=='__main__':unittest.main()
