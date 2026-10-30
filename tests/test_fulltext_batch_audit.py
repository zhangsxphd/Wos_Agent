from scripts.pilot.fulltext_batch_audit import audit

def response(e):
 return {'fields':{'study_design':{'status':'reported','value':'field','evidence':[e]}},'results':[], 'fewer_than_five_reason':'no numerical result in fixture','conflicts':[],'visual_pages_reviewed':[]}

def anchor(page=1,quote='Treatment A 12.5 mg/L'):
 return dict(page=page,paragraph_id='p1',quote=quote,table_figure='',row='',column='',footnote='')

def test_wrong_page_gets_sidecar_not_mutated():
 e=anchor(9);r=response(e)
 out=audit(r,[dict(page=1,paragraph_id='p1',text='Treatment A 12.5 mg/L')])
 assert out['automatic_corrections'][0]['proposed']['page']==1
 assert e['page']==9

def test_ambiguous_quote_never_relocated():
 r=response(anchor(9))
 out=audit(r,[dict(page=p,paragraph_id='p1',text='Treatment A 12.5 mg/L') for p in [1,2]])
 assert not out['automatic_corrections']
 assert any(x['type']=='unverified_quote_or_page' for x in out['issues'])

def test_invented_value_flagged_even_with_valid_anchor():
 e=anchor();r=response(e)
 r['results']=[dict(value='99.1',treatment='A',comparator='CK',unit='mg/L',significance='not_reported',reporting_origin='author',evidence=[e])]
 out=audit(r,[dict(page=1,paragraph_id='p1',text='Treatment A 12.5 mg/L')])
 assert any(x['type']=='numbers_not_in_bound_paragraphs' for x in out['issues'])
 assert r['results'][0]['value']=='99.1'

def test_quota_message_is_not_treated_as_a_paper_failure():
 from scripts.pilot.codex_fulltext_queue import failure_details
 event={'type':'turn.failed','error':{'message':'You’ve hit your usage limit. Try again at 5:42 AM.'}}
 kind,msg=failure_details([event],'')
 assert kind=='usage_limit'
 assert '5:42 AM' in msg

def test_quota_reset_uses_future_local_time():
 from datetime import datetime,timezone
 from scripts.pilot.codex_fulltext_queue import quota_resume
 now=datetime(2026,10,30,3,0,tzinfo=timezone.utc)
 assert quota_resume('Try again at 5:42 AM.',now).isoformat()=='2026-10-30T05:42:20+00:00'
 assert quota_resume('usage limit',now).hour==4
