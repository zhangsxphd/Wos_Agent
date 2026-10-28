import unittest

from scripts.quality.pilot import select_pilot


class PilotSelectionTests(unittest.TestCase):
    def test_dev_overlap_excluded_by_uid_or_doi_and_unready_excluded(self):
        records=[{'uid':'U1','doi':'10.1234/a','title':'Rice saline water'},
                 {'uid':'U2','doi':'10.1234/b','title':'Wheat biochar'},
                 {'uid':'U3','doi':'10.1234/c','title':'Microbial soil'},
                 {'uid':'U4','doi':'10.1234/d','title':'Field rice'}]
        q={r['uid']:{'evidence_admission':'technical_ready','abstract_state':'sourced_not_individually_verified','issues':[]} for r in records}
        q['U4']['evidence_admission']='needs_review'
        selected=select_pilot(records,q,[{'uid':'U1','doi':'10.1234/c'}],count=10)
        self.assertEqual([r['uid'] for r in selected],['U2'])

    def test_selection_is_deterministic(self):
        rows=[{'uid':'U1','doi':'10.1234/a','title':'Rice saline water'},
              {'uid':'U2','doi':'10.4567/b','title':'Wheat microbial'}]
        q={r['uid']:{'evidence_admission':'technical_ready','abstract_state':'sourced_not_individually_verified','issues':[]} for r in rows}
        self.assertEqual(select_pilot(rows,q,[],2),select_pilot(list(reversed(rows)),q,[],2))


if __name__=='__main__':unittest.main()
