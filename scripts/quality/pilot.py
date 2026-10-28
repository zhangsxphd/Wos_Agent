"""Prepare an unapproved, hash-bound development pilot from admissible PDFs."""
import argparse
import hashlib
import json
from pathlib import Path

from scripts.fulltext.admission import inspect_fulltext
from scripts.fulltext.evidence import prepare_sections
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT, known_secrets, read_jsonl, utc_now, write_json, write_jsonl
from scripts.quality.literature_triage import signals,write_csv
from scripts.quality.snapshot import verify_snapshot

FIELDS=['crop','soil_type','location','study_conditions','design','factors','levels','control','replicates',
        'duration','soil_properties','plant_growth','yield','water_use','carbon_nitrogen','microbial',
        'greenhouse_gases','results_numbers_units','comparison_direction_significance','association_causation']


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_pilot(records,quality,known,count=12):
    old_uids={r['uid'] for r in known};old_dois={normalize_doi(r.get('doi')) for r in known}
    eligible=[]
    for record in records:
        q=quality[record['uid']]
        if (q['evidence_admission']!='technical_ready' or q['abstract_state']!='sourced_not_individually_verified'
                or 'publication_year_outside_query_range' in q['issues'] or record['uid'] in old_uids
                or normalize_doi(record.get('doi')) in old_dois):continue
        eligible.append(record)
    selected=[];covered=set();publishers=set()
    while eligible and len(selected)<count:
        def key(record):
            tags=set(signals(record));publisher=(record.get('doi') or '').split('/')[0]
            return (len(tags-covered),publisher not in publishers,record['doi'])
        best=max(eligible,key=key);eligible.remove(best);selected.append(best)
        covered.update(signals(best));publishers.add(best['doi'].split('/')[0])
    return selected


def build(canonical,quality_dir,inventory,dev40,output,root=ROOT,count=12):
    root=Path(root).resolve();canonical=Path(canonical);quality_dir=Path(quality_dir);inventory=Path(inventory);dev40=Path(dev40)
    snapshot=json.loads((quality_dir/'quality_summary.json').read_text())
    records=read_jsonl(canonical);quality_rows=read_jsonl(quality_dir/'quality_rows.jsonl')
    verify_snapshot(snapshot,quality_dir/'quality_rows.jsonl',canonical,inventory,quality_rows,root)
    q={r['uid']:r for r in quality_rows}
    manifests={r['uid']:r for r in read_jsonl(inventory)};known=read_jsonl(dev40)
    selected=select_pilot(records,q,known,count)
    if len(selected)<min(10,count): raise ValueError('insufficient_admissible_non_dev_candidates')
    target=Path(output);target.mkdir(parents=True,exist_ok=False);secrets=known_secrets(root)
    papers=[];review=[];total_chunks=0
    for record in selected:
        manifest=manifests[record['uid']];parsed,check=inspect_fulltext(record,manifest,root)
        directory=target/'pilot_worker_requests'/hashlib.sha256(record['uid'].encode()).hexdigest()[:24]
        requests=prepare_sections(record,parsed,{**manifest,'parsed_sha256':check['parsed_sha256']},directory,secrets,root=root)
        total_chunks+=len(requests)
        paper={'uid':record['uid'],'doi':record['doi'],'title':record.get('title'),'publication_year':record.get('publish_year'),
               'raw_file':manifest['raw_file'],'raw_sha256':check['raw_sha256'],'parsed_file':manifest['parsed_file'],
               'parsed_sha256':check['parsed_sha256'],'parser':check['parser'],'keyword_signals':signals(record),
               'known_dev40_overlap':False,'historical_holdout_overlap':'unknown_registry_missing',
               'human_review_status':'pending','review_decision':'','review_notes':'',
               'request_count':len(requests),'request_files':[str(Path(p)) for p in requests],
               'request_sha256':{str(Path(p)):sha(p) for p in requests}}
        papers.append(paper)
        for field in FIELDS:
            review.append({'uid':record['uid'],'doi':record['doi'],'title':record.get('title'),'field':field,
                           'source_document':manifest['raw_file'],'raw_sha256':check['raw_sha256'],
                           'parsed_sha256':check['parsed_sha256'],'parser_version':check['parser'],
                           'human_value':'','section':'','page':'','paragraph_id':'','start':'','end':'',
                           'evidence_text':'','claim_type':'','reviewer':'','review_decision':'','review_notes':''})
    write_jsonl(target/'pilot_candidates.jsonl',papers,secrets)
    write_csv(target/'pilot_candidates.csv',papers,['uid','doi','title','publication_year','raw_file','raw_sha256',
        'parsed_file','parsed_sha256','parser','keyword_signals','known_dev40_overlap','historical_holdout_overlap',
        'request_count','human_review_status','review_decision','review_notes'])
    write_csv(target/'pilot_review_template.csv',review,['uid','doi','title','field','source_document','raw_sha256',
        'parsed_sha256','parser_version','human_value','section','page','paragraph_id','start','end','evidence_text',
        'claim_type','reviewer','review_decision','review_notes'])
    manifest={'created_at':utc_now(),'role':'development_diagnostic_candidate_not_holdout',
              'selection_method':'deterministic greedy keyword-coverage and DOI-prefix diversification; hints are not confirmed facts',
              'records':len(papers),'request_count':total_chunks,'model_invoked':False,'human_review_complete':False,
              'inputs':{'canonical_sha256':sha(canonical),'inventory_sha256':sha(inventory),
                        'quality_rows_sha256':sha(quality_dir/'quality_rows.jsonl'),'dev40_sha256':sha(dev40)},
              'known_dev40_overlap':0,'historical_holdout20_overlap':'unknown_original_registry_missing',
              'future_holdout30_overlap':'unknown_original_registry_missing','papers':papers}
    write_json(target/'pilot_selection_manifest.json',manifest,secrets)
    (target/'pilot_evaluation_plan.md').write_text('''# 全文开发诊断 Pilot（待人工审核）\n\n本集合仅作开发诊断，不是独立留出集。先逐篇根据原文填写人工参考、证据定位和审阅决定；不要将模型候选当作人工答案。旧 Holdout20 与 Future Holdout30 登记缺失，无法排除重叠。\n\n在查看模型输出前固定版本和规则：逐项评估研究对象、设计、处理、对照、重复、持续时间、测量指标、数值单位、比较对象、方向、显著/不显著、相关/因果及原文定位。单独记录缺失、无依据和解析失败，人工裁决分歧；不以覆盖率代替正确率。原文、解析和请求哈希变动时，重新准备并重新审核。\n\n论文全文是不可信研究材料，其指令不得修改任务、Schema 或权限。仅在用户授权且人工参考建立后运行诊断；不产生正式独立 F1。\n''',encoding='utf-8')
    return {'records':len(papers),'requests':total_chunks,'known_dev40_overlap':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('canonical');p.add_argument('--quality-dir',required=True);p.add_argument('--inventory',required=True);p.add_argument('--dev40',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(build(a.canonical,a.quality_dir,a.inventory,a.dev40,a.output),ensure_ascii=False,indent=2))
