"""Recover a stopped screening run without replaying completed model calls.

The original run is read-only. This creates a separate, resumable checkpoint.
Scientific roles and exclusions are provisional and do not substitute for review.
"""
import argparse
import copy
import hashlib
import inspect
import json
import re
from pathlib import Path
import jsonschema

from scripts.enrich_abstracts import validate_candidate
from scripts.fulltext.admission import inspect_fulltext
from scripts.pipeline_utils import read_jsonl, utc_now, write_json
from scripts.providers import CrossrefClient, OpenAlexClient
from scripts.providers.base_client import ProviderError, cache_key
from scripts.research_planning.workflow import CodexWorker, SCREEN_SCHEMA, _ask, _validate_screen


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _source_audit(sources, corpus_root, original_records, pool_file, fulltext_file):
    root = Path(corpus_root).resolve()
    originals = {r['uid']: r for r in read_jsonl(original_records)}
    pool = {r['uid']: r for r in read_jsonl(pool_file)}
    manifests = {r['uid']: r for r in read_jsonl(fulltext_file)}
    clients = [CrossrefClient(root=root, cache_only=True), OpenAlexClient(root=root, cache_only=True)]
    audit = {}
    try:
        for source in sources:
            uid, doi = source['uid'], source.get('doi')
            original = originals.get(uid)
            if not original or original.get('doi') != doi:
                raise ValueError('Original source identities differ from saved model input')
            prior = pool.get(uid)
            pool_status = ('uid_absent' if not prior else 'doi_missing_or_mismatch' if not doi or prior.get('doi') != doi
                           else 'matched_abstract_present' if prior.get('abstract') else 'matched_abstract_missing')
            row = {'original_record_abstract': 'present' if original.get('abstract') else 'missing',
                   'old_pool': pool_status, 'cache': {}, 'fulltext': {'admission': 'not_available'}}
            for client in clients:
                if not doi:
                    row['cache'][client.provider] = {'status': 'no_doi'}; continue
                try:
                    cache_path = client.cache_dir / (cache_key(doi) + '.json')
                    if not cache_path.is_file():
                        row['cache'][client.provider] = {'status': 'not_previously_cached'}; continue
                    cand = client.get_by_doi(doi)
                    if not cand or not cand.get('abstract'):
                        row['cache'][client.provider] = {'status': 'cached_abstract_missing'}; continue
                    test_record = {**source, 'publish_year': source.get('year')}
                    verdict = validate_candidate(test_record, cand)
                    row['cache'][client.provider] = {'status': 'validated_candidate' if verdict['accepted'] else 'rejected_candidate',
                            'rejection_reasons': verdict['rejection_reasons'], 'title_similarity': verdict['title_similarity'],
                            'cache_sha256': digest(cache_path)}
                except (ProviderError, ValueError, OSError) as exc:
                    row['cache'][client.provider] = {'status': 'cache_error', 'error_type': type(exc).__name__}
            ft = manifests.get(uid)
            if ft and ft.get('status') == 'available':
                try:
                    _, admission = inspect_fulltext(original, ft, root)
                    row['fulltext'] = admission
                except (OSError, ValueError) as exc:
                    row['fulltext'] = {'admission': 'needs_review', 'reason': type(exc).__name__}
            else:
                row['fulltext'] = {'admission': 'not_available',
                                   'manifest_status': ft.get('status', 'unknown') if ft else 'not_in_manifest',
                                   'reason': '已有清单未提供可准入全文；未发起获取请求。'}
            audit[source['source_id']] = row
        if any(c.stats['requests'] for c in clients):
            raise RuntimeError('Unexpected network request in cache-only audit')
    finally:
        for client in clients: client.close()
    return audit


def recover_screening(run_dir, output_dir, corpus_root, original_records, pool_file, fulltext_file,
                      retry_missing=False, worker=None, expected_calls=None):
    run, out = Path(run_dir).resolve(), Path(output_dir).resolve()
    sources_file = run / 'sources.json'; sources = read(sources_file)
    rule_sha = hashlib.sha256(inspect.getsource(_validate_screen).encode()).hexdigest()
    by_id = {r['source_id']: r for r in sources}
    if len(by_id) != len(sources): raise ValueError('Duplicate source IDs')
    originals_path = Path(original_records).resolve()
    search = read(run / 'literature_search.json')
    if digest(originals_path) != search['reuse']['sha256']:
        raise ValueError('Original record hash differs from the completed run')
    expected = {x['call']: x for x in read(expected_calls)['calls']} if expected_calls else {}
    # Verify the historical input and response identities before creating output.
    existing, calls, failed = {}, [], []
    for directory in sorted((run / 'model_calls').glob('*_screen')):
        inp = read(directory / 'input.json'); meta = read(directory / 'call.json')
        ids = [x['source_id'] for x in inp['sources']]
        if len(ids) != len(set(ids)) or any(x not in by_id for x in ids): raise ValueError('Invalid historical input IDs')
        if inp['topic'] != read(run / 'workflow_status.json')['topic'] or any(by_id[x['source_id']] != x for x in inp['sources']):
            raise ValueError('Historical batch input differs from saved sources')
        response = directory / 'response.txt'
        if directory.name in expected:
            ref = expected[directory.name]
            for key, path in (('input_sha256', directory/'input.json'), ('schema_sha256', directory/'schema.json'), ('call_log_sha256', directory/'call.json')):
                if ref.get(key) != digest(path): raise ValueError('Historical call hash mismatch: ' + directory.name)
            if response.exists() and ref.get('response_sha256') != digest(response): raise ValueError('Historical response hash mismatch')
        err = meta.get('stderr', '')
        model = re.search(r'^model: (.+)$', err, re.M)
        prefix = err.split('user\n', 1)[-1].split('INPUT DATA:\n', 1)[0]
        call = {'call': directory.name, 'input_ids': ids, 'input_sha256': digest(directory/'input.json'),
                'schema_sha256': digest(directory/'schema.json'), 'call_sha256': digest(directory/'call.json'),
                'model_reported_by_cli': model.group(1) if model else 'unknown',
                'resolved_service_model': 'unknown',
                'prompt_sha256': hashlib.sha256(prefix.encode()).hexdigest() if 'INPUT DATA:\n' in err else None,
                'prompt_version': 'historical_exact_prompt_hash' if 'INPUT DATA:\n' in err else 'unknown',
                'returncode': meta.get('returncode')}
        if meta.get('returncode') == 0 and response.is_file():
            part = read(response)['decisions']
            _validate_screen(part, inp['sources'], inp['topic'])
            for row in part:
                sid = row['source_id']
                if sid in existing: raise ValueError('Overlapping completed batches')
                existing[sid] = row
            call['response_sha256'] = digest(response)
        else:
            call['failure'] = 'usage_limit' if 'usage limit' in err.lower() else 'model_failed_or_no_output'
            failed.append({'ids': ids, 'call': directory.name, 'failure': call['failure']})
        calls.append(call)
    if not existing: raise ValueError('No completed model decisions to recover')
    if out.exists():
        checkpoint = read(out/'checkpoint.json')
        if (checkpoint['original_sources_sha256'] != digest(sources_file) or checkpoint['original_records_sha256'] != digest(originals_path)
                or checkpoint['old_pool_sha256'] != digest(pool_file) or checkpoint['fulltext_manifest_sha256'] != digest(fulltext_file)
                or checkpoint['source_audit_sha256'] != digest(out/'source_audit.json')
                or checkpoint['screening_sha256'] != digest(out/'screening_recovered.json')
                or checkpoint['screening_rule_sha256'] != rule_sha
                or checkpoint['calls'] != calls):
            raise ValueError('Resume inputs changed')
        audit = read(out/'source_audit.json')
        if set(audit) != set(by_id): raise ValueError('Source audit identities differ')
        for failure in checkpoint.get('previous_failures', []):
            if failure not in failed: failed.append(failure)
        prior = read(out/'screening_recovered.json')['decisions']
        for row in prior:
            if row.get('decision_origin') == 'retry_model': existing[row['source_id']] = row
    else:
        audit = _source_audit(sources, corpus_root, original_records, pool_file, fulltext_file)
        out.mkdir(parents=True, exist_ok=False)
        write_json(out/'source_audit.json', audit)
    fallback_path = run/'screening_with_manual_fallback.json'
    fallback_by_id = ({r['source_id']: r for r in read(fallback_path)['decisions'] if r.get('decision_origin')}
                      if fallback_path.is_file() else {})
    def persist():
        rows=[]
        for source in sources:
            sid=source['source_id']; fresh=copy.deepcopy(source); fresh['fulltext']=audit[sid]['fulltext']
            if sid in existing:
                # Recompute current rule outcome from immutable raw judgment, not a prior adjusted decision.
                original = existing[sid].get('model_decision', existing[sid])
                row = {k: original[k] for k in ('source_id','status','reason','quote')}
                _validate_screen([row], [fresh], read(run/'workflow_status.json')['topic'])
                row['decision_origin'] = existing[sid].get('decision_origin', 'historical_model')
                row['model_status'] = 'completed'
            else:
                fb=fallback_by_id.get(sid, {'status':'uncertain','reason':'模型尚未完成；保留待核。','quote':''})
                row={'source_id':sid,'status':fb['status'],'reason':fb['reason'],'quote':fb.get('quote',''),
                     'model_decision':None,'evidence_use':'unclassified_pending_review',
                     'information_sufficiency':'fulltext_available_not_scientifically_reviewed' if fresh['fulltext'].get('admission')=='technical_pass' else ('title_and_abstract' if source.get('abstract') else 'title_only'),
                     'adjustment_reasons':['模型调用失败；兜底结果不等同模型输出。'],'decision_origin':'assistant_fallback',
                     'model_status': next((f['failure'] for f in reversed(failed) if sid in f['ids']), 'not_run'),
                     'next_action':'review_provisional_decision'}
            row['source_availability'] = audit[sid]
            rows.append(row)
        todo=[r['source_id'] for r in rows if r['model_decision'] is None]
        cp={'schema_version':1,'created_at':utc_now(),'original_sources_sha256':digest(sources_file),
            'original_records_sha256':digest(originals_path),'old_pool_sha256':digest(pool_file),
            'screening_rule_version':'v09_title_roles_20261031_1','screening_rule_sha256':rule_sha,
            'fulltext_manifest_sha256':digest(fulltext_file),'source_audit_sha256':digest(out/'source_audit.json'),'model_completed':len(existing),
            'pending_model_ids':todo,'previous_failures':failed,'calls':calls,
            'status':'partial_evidence_allowed' if todo else 'screening_complete_pending_review',
            'novelty_status':'not_checked','scientific_verification':'not_inferred_from_model'}
        pending_fulltext=[{'source_id':r['source_id'], 'decision':r['status'], 'evidence_use':r['evidence_use'],
                           'availability':r['source_availability']['fulltext']}
                          for r in rows if r['status'] != 'candidate_exclude'
                          and r['source_availability']['fulltext'].get('admission') != 'technical_pass']
        write_json(out/'pending_fulltext.json', {'items':pending_fulltext,
                   'limitation':'按已有清单生成；未执行新的全文获取。'}, overwrite=True)
        cp['pending_fulltext_count']=len(pending_fulltext)
        retry_calls=[]
        for folder in sorted((out/'resume_model_calls').glob('*_screen')):
            inp=folder/'input.json'; log=folder/'call.json'; response=folder/'response.txt'
            if not inp.is_file(): continue
            payload=read(inp); event=read(log) if log.is_file() else {}
            err=event.get('stderr',''); model=re.search(r'^model: (.+)$', err, re.M)
            prefix=err.split('user\n',1)[-1].split('INPUT DATA:\n',1)[0]
            retry_calls.append({'call':folder.name,'input_ids':[s['source_id'] for s in payload['sources']],
                'input_sha256':digest(inp),'schema_sha256':digest(folder/'schema.json'),
                'call_sha256':digest(log) if log.is_file() else None,'response_sha256':digest(response) if response.is_file() else None,
                'model_reported_by_cli':model.group(1) if model else 'unknown',
                'resolved_service_model':'unknown',
                'prompt_sha256':hashlib.sha256(prefix.encode()).hexdigest() if 'INPUT DATA:\n' in err else None,
                'prompt_version':'exact_prompt_hash' if 'INPUT DATA:\n' in err else 'unknown',
                'returncode':event.get('returncode')})
        cp['retry_calls']=retry_calls
        write_json(out/'screening_recovered.json', {'decisions':rows}, overwrite=True)
        cp['screening_sha256'] = digest(out/'screening_recovered.json')
        write_json(out/'checkpoint.json', cp, overwrite=True)
        return cp
    checkpoint = persist()
    if retry_missing:
        if worker is None: raise ValueError('A worker is required to retry pending records')
        for sid in list(checkpoint['pending_model_ids']):
            try:
                result = _ask(worker, 'screen', {'topic': search.get('topic') or read(run/'workflow_status.json')['topic'], 'sources':[by_id[sid]]}, SCREEN_SCHEMA)
                _validate_screen(result['decisions'], [by_id[sid]], read(run/'workflow_status.json')['topic'])
                result['decisions'][0]['decision_origin']='retry_model'; existing[sid]=result['decisions'][0]
            except (OSError, RuntimeError, ValueError, jsonschema.ValidationError) as exc:
                failed.append({'ids':[sid], 'failure':type(exc).__name__ + ': ' + str(exc)})
                checkpoint = persist(); break
            checkpoint = persist()
    return checkpoint


def continue_with_confirmed(checkpoint_dir, evidence_file):
    """Continue a limited analysis using explicitly source-checked comparisons.

    A shared outcome does not establish comparable designs or justify pooling.
    """
    out = Path(checkpoint_dir).resolve(); checkpoint = read(out/'checkpoint.json')
    if checkpoint['screening_sha256'] != digest(out/'screening_recovered.json'):
        raise ValueError('Screening checkpoint changed')
    evidence = read(evidence_file)
    studies = {x['study_id']: x for x in evidence['studies']}
    if len(studies) != len(evidence['studies']): raise ValueError('Duplicate study ID')
    comparisons = {}
    required = ('comparison_id','study_id','outcome','intervention','comparator','result','unit',
                'significance','observation_period','source_location','limitations','verification_status')
    for item in evidence['comparisons']:
        if any(not isinstance(item.get(k), str) or not item[k].strip() for k in required):
            raise ValueError('Each comparison needs explicit source, values and limitations')
        if item['study_id'] not in studies or item['verification_status'] != 'source_checked':
            raise ValueError('Comparison requires a source-checked study')
        if item['comparison_id'] in comparisons: raise ValueError('Duplicate comparison ID')
        comparisons[item['comparison_id']] = item
    for study in studies.values():
        if not study.get('source_url','').startswith(('https://','http://')) or not study.get('source_access'):
            raise ValueError('Each study needs an accessible source and access status')
    for group in evidence['cross_study_comparisons']:
        ids=group['comparison_ids']
        if len(ids)<2 or any(x not in comparisons for x in ids):raise ValueError('Cross-study comparison needs at least two existing comparisons')
        selected=[comparisons[x] for x in ids]
        if len({x['study_id'] for x in selected}) < 2 or len({x['outcome'] for x in selected}) != 1:
            raise ValueError('Cross-study comparison requires one common outcome from different studies')
        if not group.get('interpretation') or not group.get('comparability_limitations'):
            raise ValueError('Cross-study comparison needs an interpretation and limitations')
    target=out/'confirmed_evidence.json'
    if target.exists() and digest(target) != digest(evidence_file):
        raise FileExistsError('Different confirmed evidence already exists')
    report=out/'limited_synthesis.md'; receipt_path=out/'limited_analysis_receipt.json'
    if report.exists():
        if not receipt_path.exists(): raise FileExistsError('Synthesis exists without its receipt')
        previous=read(receipt_path)
        if previous.get('evidence_sha256') != digest(evidence_file) or previous.get('report_sha256') != digest(report):
            raise ValueError('Existing limited analysis changed')
        return previous
    if not target.exists(): target.write_bytes(Path(evidence_file).read_bytes())
    lines=['# 有限范围：已核来源的跨研究比较','',
           '范围有限；本报告只整理已明确核查的比较，不自动推断原创性或合并效应。',
           '来源核查状态由证据整理者提供；程序检查引用和结构，不能自动证明原文解释正确。',
           '未完成模型筛选：'+', '.join(checkpoint['pending_model_ids']) if checkpoint['pending_model_ids'] else '模型筛选无待处理记录；仍需科学核查。','']
    lines += ['## 纳入的来源及证据用途','']
    for study in studies.values():
        fields = study.get('fields', {})
        lines += [f"- [{study['study_id']}]({study['source_url']})（{study.get('evidence_role','未分类')}）："
                  f"{fields.get('研究对象','未知')}；处理：{fields.get('处理','未知')}；"
                  f"对照：{fields.get('对照','未知')}；独立重复：{fields.get('独立重复','未知')}；"
                  f"盐碱条件：{fields.get('环境及盐度','未知')}；指标：{fields.get('指标','未知')}。"
                  f"来源位置：{study.get('location','未知')}。"]
    lines += ['']
    for group in evidence['cross_study_comparisons']:
        lines += ['## '+group['title'],'',group['interpretation'],'',
                  '可比性限制：'+group['comparability_limitations'],'']
        for cid in group['comparison_ids']:
            item=comparisons[cid]; study=studies[item['study_id']]
            lines += [f"- [{item['study_id']}]({study['source_url']})，{item['source_location']}：{item['intervention']} 对 {item['comparator']}；{item['outcome']}：{item['result']}；单位：{item['unit']}；{item['significance']}。{item['limitations']}"]
        lines += ['']
    grouped={cid for group in evidence['cross_study_comparisons'] for cid in group['comparison_ids']}
    ungrouped=[item for cid,item in comparisons.items() if cid not in grouped]
    if ungrouped:
        lines += ['## 其他单篇研究比较','']
        for item in ungrouped:
            study=studies[item['study_id']]
            lines += [f"- [{item['study_id']}]({study['source_url']})，{item['source_location']}：{item['intervention']} 对 {item['comparator']}；{item['outcome']}：{item['result']}；单位：{item['unit']}；{item['significance']}。{item['limitations']}"]
        lines += ['']
    lines += ['## 设计限制与原文疑点','']
    for study in studies.values():
        limitation=study.get('fields', {}).get('限制')
        if limitation:
            lines += [f"- [{study['study_id']}]({study['source_url']})：{limitation}"]
    lines += ['']
    lines += ['## 未完成','', *['- '+x for x in evidence['limitations']],
              '- 创新性未完成系统核查，保持未知。',
              '- 处理归属与统计解释仍应由研究人员独立复核。','']
    report.write_text('\n'.join(lines),encoding='utf-8')
    receipt={'evidence_sha256':digest(target),'report_sha256':digest(report),'studies':len(studies),'source_checked_comparisons':len(comparisons),
             'cross_study_comparisons':len(evidence['cross_study_comparisons']),
             'pending_model_ids':checkpoint['pending_model_ids'],'novelty':'unknown',
             'verification':'source_check_declared_by_curator_not_independent','status':'limited_analysis_not_final'}
    write_json(out/'limited_analysis_receipt.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('run');parser.add_argument('output');parser.add_argument('--corpus-root',required=True)
    parser.add_argument('--records',required=True);parser.add_argument('--pool',required=True);parser.add_argument('--fulltext-manifest',required=True)
    parser.add_argument('--expected-calls');parser.add_argument('--retry-missing',action='store_true')
    parser.add_argument('--confirmed-evidence', help='Source-checked comparisons to continue without waiting for pending model calls')
    args=parser.parse_args()
    worker=CodexWorker(audit_dir=Path(args.output)/'resume_model_calls') if args.retry_missing else None
    result=recover_screening(args.run,args.output,args.corpus_root,args.records,args.pool,args.fulltext_manifest,args.retry_missing,worker,args.expected_calls)
    print(json.dumps({k:v for k,v in result.items() if k not in ('calls',)},ensure_ascii=False,indent=2))
    if args.confirmed_evidence:
        print(json.dumps(continue_with_confirmed(args.output,args.confirmed_evidence),ensure_ascii=False,indent=2))

if __name__ == '__main__': main()
