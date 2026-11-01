"""Small, checkpointed public search adapter using existing Crossref and OA resolver."""
import hashlib
import json
import signal
from pathlib import Path

from scripts.fulltext.openalex_content import OpenAlexContentClient
from scripts.fulltext.resolver import FulltextResolver
from scripts.fulltext.provenance import FulltextError
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, utc_now
from scripts.providers.crossref_client import CrossrefClient, clean_markup
from scripts.research_planning.connected import dump, run, intake_module
from scripts.research_planning.evidence_synthesis import synthesize_converted
from scripts.research_planning.project import initialize


class DownloadDeadline(Exception):
    pass


def _deadline(signum, frame):
    raise DownloadDeadline()


def public_run(project, topic, settings_path, confirmation=None, review=None):
    project = Path(project).resolve()
    settings_path = Path(settings_path).resolve()
    settings = json.loads(settings_path.read_text())
    settings_hash = hashlib.sha256(settings_path.read_bytes()).hexdigest()
    if not project.exists():
        initialize(project, topic)
    if json.loads((project / 'project.json').read_text())['topic'] != topic:
        raise ValueError('主题与项目不一致')
    public = project / 'public_search'
    public.mkdir(exist_ok=True)
    raw_path = public / 'crossref_response.json'
    audit_path = public / 'search_manifest.json'
    if audit_path.exists() and json.loads(audit_path.read_text())['settings_sha256'] != settings_hash:
        raise ValueError('检索配置已改变；请建立新项目')
    if not raw_path.exists():
        client = CrossrefClient(root=ROOT, timeout=25, retries=0)
        try:
            raw = client.search_works(settings['query'], settings.get('rows', 20))
        finally:
            client.close()
        dump(raw_path, raw)
        msg = raw['message']
        dump(audit_path, {'provider':'Crossref public works API', 'query':settings['query'],
            'filter':'type:journal-article', 'retrieved_at':utc_now(), 'settings_sha256':settings_hash,
            'total_hits':msg.get('total-results'), 'returned':len(msg['items']),
            'limit':settings.get('rows',20), 'truncated':msg.get('total-results',0)>len(msg['items']),
            'status':'bounded_partial_search', 'inclusion_criteria':settings['inclusion_criteria']})
    raw = json.loads(raw_path.read_text())
    rows = []
    for item in raw['message']['items']:
        doi = normalize_doi(item.get('DOI'))
        if not doi: continue
        title = clean_markup((item.get('title') or [''])[0]) or ''
        text = title.casefold().translate(str.maketrans({**{c:'-' for c in '‐‑‒–—−'}, '₂':'2'}))
        is_review = any(t in text for t in ('meta-analysis','meta analysis','systematic review','a review'))
        relevant = ('rice' in text or 'paddy' in text) and ('nitrous' in text or 'n2o' in text) and any(t in text for t in ('wetting','drying','irrigation','water management'))
        status = 'review_or_meta_candidate' if is_review else 'potential_original_experiment' if relevant else 'uncertain_not_excluded'
        rows.append({'uid':'DOI:'+doi,'doi':doi,'title':title,'abstract':None,
                     'publication_year':((item.get('published') or {}).get('date-parts') or [[None]])[0][0],
                     'provenance':{'provider':'Crossref','query':settings['query'],'batch':'public_search'},
                     'screening_status':status,'screening_reason':'题名规则仅排序，不确认研究设计或排除缺信息题录'})
    # Preserve provider order; selection is by title relevance and document type, never results.
    selected=[];seen=set()
    for row in rows:
        if row['screening_status']=='potential_original_experiment' and row['doi'] not in seen:
            selected.append(row);seen.add(row['doi'])
    selected=selected[:settings.get('max_fulltext_attempts',6)]
    records=public/'records.jsonl'
    current_records=''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows)
    if records.exists() and records.read_text()!=current_records and not (public/'connected_config.json').exists():
        previous_hash=hashlib.sha256(records.read_bytes()).hexdigest()[:12]
        records.rename(public/f'records.previous_{previous_hash}.jsonl')
    if not records.exists(): records.write_text(current_records)
    if (public/'connected_config.json').exists():
        # The completed retrieval batch is frozen; resume without expanding it.
        selected=[]
    oa=public/'oa_attempts';oa.mkdir(exist_ok=True)
    client=OpenAlexContentClient(root=ROOT,timeout=15,retries=0)
    # Public, unauthenticated metadata and OA URLs only. Never use a configured paid key.
    client.api_key=None
    resolver=FulltextResolver(ROOT,client,include_archive=False,max_candidates=2)
    try:
        for record in selected:
            path=oa/(hashlib.sha256(record['doi'].encode()).hexdigest()+'.json')
            if path.exists(): continue
            try:
                # The existing streaming read timeout is per socket read, not a
                # total deadline. Bound a slow-drip public server as a whole.
                previous=signal.signal(signal.SIGALRM, _deadline)
                signal.alarm(30)
                work=client.lookup(record['doi'])
                result=resolver.resolve(record,work=work) if work else {'status':'unavailable','reason':'work_not_found'}
            except DownloadDeadline:
                result={'status':'error','reason':'total_attempt_deadline_exceeded'}
            except (FulltextError,ValueError,OSError,KeyError) as exc:
                result={'status':'error','reason':getattr(exc,'code','invalid_or_network_data'), 'http_status':getattr(exc,'http_status',None)}
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM,previous)
            result['doi']=record['doi'];result['attempted_at']=utc_now()
            dump(path,result)
    finally:
        client.close()
    attempts=[json.loads(p.read_text()) for p in sorted(oa.glob('*.json'))]
    fulltexts=[]
    for item in attempts:
        if item.get('status')=='available' and item.get('format')=='pdf' and item.get('raw_file') and item.get('source_url'):
            fulltexts.append({'doi':item['doi'],'file':item['raw_file'],'source_url':item['source_url'], 'auxiliary':[]})
    generated=public/'connected_config.json'
    if not generated.exists(): dump(generated,{'records':str(records),'terms':settings['terms'],'fulltexts':fulltexts})
    previous_state=json.loads((project/'run_manifest.json').read_text()) if (project/'run_manifest.json').exists() else {}
    if previous_state.get('status')=='completed_with_pending' and not (confirmation and review):
        return previous_state
    state=run(project,topic,generated,confirmation=confirmation,review=review)
    for row in rows:
        paper=state['papers'].get(row['doi'])
        if paper:
            paper['public_title_triage']={'status':row['screening_status'],'reason':row['screening_reason']}
    for item in attempts:
        paper=state['papers'].get(item['doi'])
        if paper:
            paper['open_access_attempt']={'status':item.get('status'),'reason':item.get('reason'),
                'attempts':item.get('attempts',[]),'version':item.get('version'),'license':item.get('license'),
                'source_url':item.get('source_url'),'cache_hit':item.get('cache_hit')}
    state['public_search']={'manifest':'public_search/search_manifest.json','raw':'public_search/crossref_response.json',
                            'attempted':len(attempts),'oa_attempts':'public_search/oa_attempts',
                            'authorization':'public_unauthenticated_no_paid_key'}
    if state['status']=='awaiting_confirmation':
        # Actual synthesis of the empty confirmed set, never of unreviewed
        # snippets. This is an explicit interim result rather than a finding.
        normalized={'topic_profile':{'topic':topic,'primary_question':topic},'papers':[],'comparisons':[]}
        summary=synthesize_converted(normalized)
        out=project/'analysis';out.mkdir(exist_ok=True)
        dump(out/'normalized_evidence.json',normalized);dump(out/'synthesis.json',summary)
        report=intake_module().report(normalized,summary,[],[]).replace('工程运行：已完成','工程运行：暂停等待人工确认')
        (out/'analysis.md').write_text(report,encoding='utf8')
        state['analysis']={'status':'interim_no_confirmed_comparisons', 'normalized_evidence':'analysis/normalized_evidence.json',
                           'synthesis':'analysis/synthesis.json','report':'analysis/analysis.md'}
    dump(project/'run_manifest.json',state)
    return state
