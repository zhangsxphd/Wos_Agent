"""Connect existing local search, PDF admission, candidate intake and synthesis.

Checkpoints contain only candidate/review state. No model or paid API is called.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.normalize_record import normalize_doi, deduplicate_records
from scripts.pipeline_utils import ROOT, read_jsonl, utc_now
from scripts.acquire_fulltexts import import_browser_pdf
from scripts.fulltext.admission import inspect_fulltext
from scripts.research_planning.project import initialize, local_search, search


def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dump(path, value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf8');tmp.replace(path)


def intake_module():
    path=ROOT/'research_projects/awd_n2o_mvp/run.py'
    spec=importlib.util.spec_from_file_location('existing_research_intake',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def run(project,topic,config_path,confirmation=None,review=None,wos_plan=None,authorize_wos=False):
    project=Path(project).resolve(); config_path=Path(config_path).resolve();config=json.loads(config_path.read_text())
    if not project.exists(): initialize(project,topic)
    metadata=json.loads((project/'project.json').read_text())
    if metadata['topic']!=topic:raise ValueError('主题与现有项目不一致')
    cfg_hash=digest(config_path); statefile=project/'run_manifest.json'
    state=json.loads(statefile.read_text()) if statefile.exists() else {'topic':topic,'created_at':utc_now(),'configuration_sha256':cfg_hash,'papers':{},'events':[]}
    if state['configuration_sha256']!=cfg_hash:raise ValueError('输入配置已改变；请建立新项目，避免覆盖旧检索记录')
    state['updated_at']=utc_now();state['wos']={'status':'authorization_unavailable','reason':'未明确授权和配置 WoS；未调用付费接口'}
    if authorize_wos:
        if not wos_plan:raise ValueError('授权 WoS 检索时必须指定检索计划')
        if (project/'local_search.json').exists() and not (project/'wos_search_manifest.json').exists():
            raise ValueError('现有项目已冻结检索批次；请使用新项目执行 WoS 检索')
        if not (project/'wos_search_manifest.json').exists(): search(project,wos_plan,max_records=10,max_pages=1)
        state['wos']={'status':'existing_search_manifest','file':'wos_search_manifest.json'}
    records_path=ROOT/config['records']
    if state.get('search') and state['search']['source_sha256']!=digest(records_path):
        raise ValueError('原始题录已改变；请建立新项目以保留原检索批次')
    original=read_jsonl(records_path)
    if (project/'wos_records.jsonl').exists():
        original += read_jsonl(project/'wos_records.jsonl')
        state['wos']={'status':'existing_search_manifest','file':'wos_search_manifest.json'}
    records=deduplicate_records(original)
    clean=project/'bibliography.jsonl'
    if not clean.exists(): clean.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf8')
    if not (project/'local_search.json').exists():local_search(project,clean,config['terms'])
    matching={normalize_doi(x['record'].get('doi')) for x in read_jsonl(project/'local_candidates.jsonl')}
    state['search']={'source':str(records_path),'source_sha256':digest(records_path),'input_records':len(original),'deduplicated_records':len(records),
                     'local_search':'local_search.json','coverage':'controlled_local_subset_not_complete','duplicate_records_removed':len(original)-len(records)}
    by_doi={normalize_doi(x['doi']):x for x in config['fulltexts']}
    pdfs=[];intake_inputs=[]
    for record in records:
        doi=normalize_doi(record.get('doi'))
        if not doi:continue
        record['doi']=doi;record['uid']='DOI:'+doi
        item=state['papers'].setdefault(doi,{'doi':doi,'record_id':'doi:'+doi,'history':[]})
        item.update(title=record.get('title'),bibliography_source=record.get('provenance'),retrieval_batch=(record.get('provenance') or {}).get('batch'),
                    screening={'status':'title_candidate_pending_review' if doi in matching else 'information_insufficient_pending_review',
                               'reason':'本地关键词召回；非全文纳排结论' if doi in matching else '标题未命中关键词，不据此排除'},
                    human_source_review=item.get('human_source_review',{'status':'not_assessed'}),independent_gold={'status':'not_assessed'})
        info=by_doi.get(doi)
        if not info:
            item['fulltext']={'status':'not_available','admission':'not_assessed'};continue
        source=ROOT/info['file']
        if not source.is_file():item['fulltext']={'status':'unavailable','admission':'not_assessed','reason':'local_file_missing'};continue
        sh=digest(source);local=project/'source_files'/f'{sh}.pdf';local.parent.mkdir(exist_ok=True)
        if not local.exists():shutil.copy2(source,local)
        elif digest(local)!=sh:raise ValueError('缓存全文哈希发生变化')
        cache=project/'admission'/f'{hashlib.sha256(doi.encode()).hexdigest()}.json'
        try:
            if cache.exists(): full=json.loads(cache.read_text())
            else:
                full=import_browser_pdf(record,{'file':str(local.relative_to(project)),'source_url':info['source_url'],'access_basis':'public_publisher_download'},root=project)
                dump(cache,full)
            _,admission=inspect_fulltext(record,full,project)
            if admission.get('raw_sha256')!=sh or admission.get('admission')!='technical_pass':
                raise ValueError('缓存全文与当前文件不一致或未通过技术准入')
            item['fulltext']={'status':'available','sha256':sh,'admission':admission,'local_input':info['file'],'source_url':info['source_url'],'manifest':str(cache.relative_to(project))}
            pdfs.append(doi);intake_inputs.append(source)
            for aux in info.get('auxiliary',[]):
                auxpath=ROOT/aux
                if auxpath.is_file():intake_inputs.append(auxpath)
        except Exception as exc:
            item['fulltext']={'status':'available','sha256':sh,'admission':{'admission':'needs_review','reason':str(exc)},'local_input':info['file']}
    intake=project/'intake';intake_key=hashlib.sha256(''.join(digest(p) for p in intake_inputs).encode()).hexdigest()
    if (intake/'input_hash.txt').exists() and (intake/'input_hash.txt').read_text()!=intake_key:
        raise ValueError('全文输入已改变；请建立新项目以保留原候选与确认记录')
    if intake_inputs and not (intake/'input_hash.txt').exists():
        intake_module().discover(intake_inputs,intake);(intake/'input_hash.txt').write_text(intake_key)
    for doi in pdfs:state['papers'][doi]['candidate_extraction']={'status':'program_excerpts_candidate_not_gold','intake':'intake/manifest_draft.json'}
    state['events'].append({'at':utc_now(),'stage':'local_search_admission_and_intake','status':'completed','admitted_dois':pdfs})
    pending=[]
    for doi,item in state['papers'].items():
        item.setdefault('candidate_extraction',{'status':'not_started'})
        item['synthesis_eligibility']={'status':'pending_human_confirmation','reason':'来源核查和可比处理尚未完整确认'}
        pending.append({'doi':doi,'next':'确认处理对照及原文位置；无合格对照时记录原因','gold':'not_assessed'})
    if not confirmation or not review:
        state['status']='awaiting_confirmation';state['stage']='human_review';dump(project/'pending.json',pending);dump(statefile,state);return state
    confirm=Path(confirmation).resolve();reviewfile=Path(review).resolve();att=json.loads(reviewfile.read_text());data=json.loads(confirm.read_text())
    accepted={normalize_doi(x['doi']):x for x in att.get('decisions',[]) if x.get('decision')=='confirm' and x.get('actor')=='user'}
    if not accepted:raise ValueError('没有带用户确认的配对；不会将助手自审标作人工确认')
    for decision in att.get('decisions',[]):
        doi=normalize_doi(decision.get('doi'));item=state['papers'].get(doi)
        if item and decision.get('decision')=='insufficient_comparison' and decision.get('pdf_sha256')==item['fulltext'].get('sha256'):
            item['scientific_assessment']={'status':'assistant_candidate_not_independent','reason':decision.get('reason'),'location':decision.get('location'),'review_file':str(reviewfile),'review_sha256':digest(reviewfile)}
            item['synthesis_eligibility']={'status':'not_eligible_pending_review','reason':decision.get('reason')}
            for todo in pending:
                if todo['doi']==doi:todo['next']=decision.get('reason')
    selected=[]
    for entry in data['papers']:
        if not entry.get('include'):continue
        p=dict(entry['candidate'])
        for edit in entry.get('edits',[]):p[edit['field']]=edit.get('value')
        doi=normalize_doi(p.get('doi'))
        if doi not in accepted or doi not in pdfs:raise ValueError('确认记录未匹配已技术准入的全文与用户确认')
        item=state['papers'][doi];decision=accepted[doi]
        if decision.get('pdf_sha256')!=item['fulltext']['sha256'] or p.get('pdf_sha256')!=item['fulltext']['sha256']:
            raise ValueError('用户核查或候选全文哈希与准入文件不一致')
        if digest(ROOT/p['pdf'])!=p['pdf_sha256'] or digest(ROOT/p['source'])!=p['source_sha256']:
            raise ValueError('确认后原始 PDF 或表格已改变；请重新核查')
        for key in ('a_label','b_label','indicator'):
            if p.get(key)!=decision.get(key):raise ValueError('用户核查与候选处理/指标不一致: '+key)
        selected.append(doi)
    if not selected:raise ValueError('未选择已确认论文')
    analysis=project/'analysis';analysis.mkdir(exist_ok=True)
    cmd=[sys.executable,str(ROOT/'research_projects/awd_n2o_mvp/run.py'),'--confirmation',str(confirm),'--sources',str(ROOT),'--out',str(analysis)]
    key=hashlib.sha256((digest(confirm)+digest(reviewfile)+digest(ROOT/'research_projects/awd_n2o_mvp/run.py')+
                       digest(ROOT/'scripts/research_planning/evidence_synthesis.py')).encode()).hexdigest()
    log=project/'analysis_execution.json'
    prior=json.loads(log.read_text()) if log.exists() else {}
    outputs=('normalized_evidence.json','synthesis.json','analysis.md')
    cached=(prior.get('cache_key')==key and prior.get('returncode')==0 and
            all((analysis/name).is_file() and digest(analysis/name)==prior.get('output_hashes',{}).get(name) for name in outputs))
    if not cached:
        result=subprocess.run(cmd,cwd=ROOT,text=True,capture_output=True)
        output_hashes={name:digest(analysis/name) for name in outputs if (analysis/name).is_file()}
        dump(log,{'command':cmd,'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,'cache_key':key,'output_hashes':output_hashes})
        if result.returncode:state['status']='analysis_failed';state['stage']='analysis';dump(statefile,state);return state
    normalized=json.loads((analysis/'normalized_evidence.json').read_text());comparisons=normalized['comparisons']
    if {x['study_id'] for x in comparisons}!=set(selected):raise ValueError('实际综合论文与确认论文不一致')
    for c in comparisons:
        dec=accepted[c['study_id']];v=c['numeric_values']
        for field,key in [('intervention','a_value'),('comparator','b_value'),('intervention_error','a_error'),('comparator_error','b_error'),('unit','unit')]:
            if v.get(field)!=dec.get(key):raise ValueError('用户确认值与实际转换结果不一致: '+field)
        if c['independent_replicates'].get('value')!=dec.get('replicates') or c['experimental_unit'].get('value')!=dec.get('experimental_unit'):
            raise ValueError('用户确认的实验单位或独立重复与转换结果不一致')
        item=state['papers'][c['study_id']];item['human_source_review']={'status':'user_confirmed_comparison_not_gold','review_file':str(reviewfile),'review_sha256':digest(reviewfile),'comparison_id':c['comparison_id']}
        item['synthesis_eligibility']={'status':'candidate_allowed_not_gold','reason':'技术准入和用户确认配对；科学解释与独立实验有效性仍需核查'}
        item['comparison']={'id':c['comparison_id'],'indicator':c['outcome'],'experimental_unit':c['experimental_unit'],'independent_replicates':c['independent_replicates']}
    pending=[p for p in pending if p['doi'] not in selected]
    state['confirmation']={'file':str(confirm),'sha256':digest(confirm),'human_review_file':str(reviewfile),'review_sha256':digest(reviewfile)}
    state['analysis']={'normalized_evidence':'analysis/normalized_evidence.json','synthesis':'analysis/synthesis.json','report':'analysis/analysis.md','resume_cache':'本地检索、全文及候选按哈希复用'}
    state['events'].append({'at':utc_now(),'stage':'analysis','status':'cache_reused' if cached else 'completed','selected_dois':selected})
    state['status']='completed_with_pending';state['stage']='finished';dump(project/'pending.json',pending);dump(statefile,state);return state
