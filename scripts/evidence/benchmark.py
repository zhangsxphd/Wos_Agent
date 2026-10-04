"""Compare blind Codex responses with human-reviewed v0.4 evidence."""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

if not __package__: sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from scripts.evidence.audit import audit_evidence
from scripts.evidence.batch_ingest import read_batch_responses
from scripts.evidence.metrics import calculate_benchmark, quality_gate
from scripts.extractors.schema_validator import EvidenceValidator
from scripts.pipeline_utils import ROOT, display_path, project_path, read_jsonl, write_json


def match_gold_records(canonical_records,gold_rows):
    canonical_by_uid={r.get('uid'):r for r in canonical_records}; matched={}
    for gold in gold_rows:
        if gold.get('screening',{}).get('status')!='maybe':
            continue
        record=canonical_by_uid.get(gold.get('uid'))
        if record is None or not record.get('abstract'): continue
        if record.get('doi')!=gold.get('doi') or record.get('title')!=gold.get('title'):
            raise ValueError('Gold identity does not match canonical corpus')
        matched[gold['uid']]=gold
    return matched


def _path_errors(predicted, record):
    if not isinstance(predicted,dict): return 0,0,0,0
    support=predicted.get('evidence_support',{}); expected=set(); unsupported=0; invalid_offsets=0
    from scripts.extractors.schema_validator import factual_leaves
    for pointer,value in factual_leaves(predicted.get('evidence',{})):
        expected.add(pointer); item=support.get(pointer)
        if not item: unsupported+=1; continue
        quote=item.get('evidence_text',''); abstract=record.get('abstract') or ''
        start,end=item.get('start'),item.get('end')
        if type(start) is not int or type(end) is not int or start<0 or end<=start or abstract[start:end]!=quote:
            invalid_offsets+=1
        if item.get('source')!='abstract' or not quote or quote not in abstract:
            unsupported+=1
        elif pointer.endswith('/experimental_scale'):
            pass
        elif str(value).casefold() not in quote.casefold(): unsupported+=1
    orphan=max(0,len(set(support)-expected))
    unsupported_findings=0
    for finding in predicted.get('evidence',{}).get('findings',[]):
        text=finding.get('evidence_text',''); claim=finding.get('claim',''); abstract=record.get('abstract') or ''
        if not text or text not in abstract or claim not in text: unsupported_findings+=1
        start,end=finding.get('start'),finding.get('end')
        if finding.get('source')=='abstract' and (type(start) is not int or type(end) is not int or start<0 or end<=start or abstract[start:end]!=text): invalid_offsets+=1
    return unsupported,unsupported_findings,invalid_offsets,orphan


def benchmark(batch_dir,canonical_input,gold_evidence_file,root=ROOT,report_dir=None):
    root=Path(root).resolve(); source=project_path(canonical_input,root); canonical=read_jsonl(source)
    gold_rows=read_jsonl(project_path(gold_evidence_file,root))
    canonical_by_uid={r.get('uid'):r for r in canonical}
    gold_by_uid=match_gold_records(canonical,gold_rows)
    manifest=json.loads((project_path(batch_dir,root)/'manifest.json').read_text())
    if manifest.get('gold_or_previous_evidence_included') is not False:
        raise ValueError('Worker batch manifest is not marked blind')
    predictions,validation,audits=read_batch_responses(batch_dir,canonical,root)
    validator=EvidenceValidator()
    by_uid={}; audit_by_uid={}; val_out={}
    for uid,gold in gold_by_uid.items():
        response=predictions.get(uid,{}).get('evidence')
        val=validation.get(uid,{})
        record=canonical_by_uid[uid]
        if response is None and val.get('response_present'):
            try:
                response=json.loads(project_path(val['response_file'],root).read_text(encoding='utf-8')).get('response')
            except (OSError,ValueError,TypeError,KeyError):
                response=None
        extra=_path_errors(response,record)
        val.update(dict(zip(('unsupported_field_count','unsupported_finding_count','invalid_offset_count','orphan_anchor_count'),extra)))
        if response is not None:
            val['schema_valid']=validator.validator.is_valid(response)
            try: validator.validate(response,record); val['grounding_valid']=True
            except Exception as exc:
                val['grounding_valid']=False
                text=str(exc).casefold(); val['error_kind']='offset' if 'offset' in text else 'grounding'
            val['identifier_match']=all(response.get(k)==record.get(k) for k in ('uid','doi','title'))
            val['accepted']=val['schema_valid'] and val['grounding_valid'] and val['identifier_match']
            audit_by_uid[uid]=audit_evidence(response,record)
        else:
            val.setdefault('schema_valid',False); val.setdefault('grounding_valid',False); val.setdefault('identifier_match',False); val.setdefault('accepted',False)
        by_uid[uid]=response; val_out[uid]=val
    metrics=calculate_benchmark(gold_by_uid,by_uid,val_out,audit_by_uid)
    gate=quality_gate(metrics)
    run_id=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y%m%d_%H%M%S')
    report_dir=project_path(report_dir,root) if report_dir else root/'data/reports'
    json_path=report_dir/f'v06_gold_benchmark_{run_id}.json'; md_path=report_dir/f'v06_gold_benchmark_{run_id}.md'
    body={"pipeline_version":"0.6","benchmark_id":run_id,"records":len(gold_by_uid),
          "gold_file":display_path(project_path(gold_evidence_file,root),root),"worker_batch_dir":display_path(project_path(batch_dir,root),root),
          "prompt_sha256":manifest['prompt_sha256'],"schema_sha256":manifest['schema_sha256'],
          "blind_worker_responses_received":sum(v.get('accepted',False) for v in val_out.values()),
          "metrics":metrics,"quality_gate":gate}
    write_json(json_path,body)
    md=[f"# v0.6 Gold Benchmark {run_id}","",f"Records: {len(gold_by_uid)}",f"Blind worker responses accepted: {body['blind_worker_responses_received']}",
        f"Missing worker responses: {metrics['missing_responses']}",
        f"Schema valid rate: {metrics['structural_validity']['schema_valid_rate']:.3f}",
        f"Grounding valid rate: {metrics['structural_validity']['grounding_valid_rate']:.3f}",
        f"Identifier match rate: {metrics['structural_validity']['identifier_match_rate']:.3f}",
        f"Response contract valid rate: {metrics['structural_validity']['response_contract_valid_rate']:.3f}",
        f"Field micro precision/recall/F1: {metrics['field_micro']['precision']:.3f}/{metrics['field_micro']['recall']:.3f}/{metrics['field_micro']['f1']:.3f}",
        f"Finding precision/recall: {metrics['finding_metrics']['precision']:.3f}/{metrics['finding_metrics']['recall']:.3f}",
        f"Finding claim grounding validity: {metrics['claim_grounding']['validity_rate']:.3f} ({metrics['claim_grounding']['claims_grounded']}/{metrics['claim_grounding']['claims_checked']})",
        f"Rejected: {metrics['rejected_extractions']}",
        f"Unsupported evidence: fields={metrics['unsupported_field_count']}, findings={metrics['unsupported_finding_count']}",
        f"Invalid offsets: {metrics['invalid_offset_count']}; orphan anchors: {metrics['orphan_anchor_count']}",
        f"Audit flags: {sum(len(x) for x in audit_by_uid.values())}","",f"Quality gate: **{gate['status']}**","",
        "## Field metrics","","| Field | Precision | Recall | F1 | Gold | Predicted |","|---|---:|---:|---:|---:|---:|"]
    for field,m in metrics['field_metrics'].items(): md.append(f"| `{field}` | {m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} | {m['gold_count']} | {m['predicted_count']} |")
    md.extend(["","## Error taxonomy",""])
    for category,items in metrics['error_taxonomy'].items():
        md.append(f"- {category}: {len(items)}")
        for item in items: md.append(f"  - `{item.get('uid')}` `{item.get('field')}` predicted={item.get('predicted')!r} gold={item.get('gold')!r} support={item.get('support_span')!r}")
    md.extend(["", "Failed checks: " + ", ".join(gate['failed_checks']),
               "", "No 20-record pilot is authorized by this benchmark unless the gate passes."])
    md_path.parent.mkdir(parents=True,exist_ok=True); md_path.write_text('\n'.join(md)+'\n',encoding='utf-8')
    return {"json":display_path(json_path,root),"markdown":display_path(md_path,root),**body}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('batch_dir');p.add_argument('--canonical-input',required=True);p.add_argument('--gold-evidence',required=True);p.add_argument('--report-dir');a=p.parse_args()
    try:
        result=benchmark(a.batch_dir,a.canonical_input,a.gold_evidence,report_dir=a.report_dir)
        print(json.dumps({k:result[k] for k in ('json','markdown','records','blind_worker_responses_received','quality_gate')},ensure_ascii=False))
    except (ValueError,OSError,TypeError,KeyError) as exc: print(f'Benchmark failed: {exc}',file=sys.stderr);return 1
    return 0


if __name__=='__main__': raise SystemExit(main())
