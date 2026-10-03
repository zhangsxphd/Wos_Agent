"""Resolve an explicit sample to independent OA-only fulltext sidecars."""
import argparse
import hashlib
import json
import sys
from pathlib import Path

if not __package__: sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from scripts.pipeline_utils import ROOT, read_jsonl, write_jsonl, write_json, known_secrets, assert_safe, new_run_id
from scripts.fulltext.openalex_content import OpenAlexContentClient
from scripts.fulltext.resolver import FulltextResolver
from scripts.fulltext.provenance import FulltextError, manifest
from scripts.providers.base_client import canonical_doi,cache_key


def resolve_file(input_file, evidence_file=None, root=ROOT, client=None, output=None, refresh=False, metadata_only=False):
    root=Path(root)
    source=Path(input_file)
    if not source.is_absolute(): source=root/source
    records=read_jsonl(source)
    secrets=known_secrets(root)
    assert_safe(records,secrets)
    before=hashlib.sha256(source.read_bytes()).hexdigest()
    evidence={r['uid']:r for r in read_jsonl(root/evidence_file)} if evidence_file else {}
    prioritized=sorted(enumerate(records),key=lambda x:(bool(x[1].get('abstract')), not evidence.get(x[1].get('uid'),{}).get('completeness',{}).get('needs_fulltext',True), x[0]))
    own_client=client is None
    client=client or OpenAlexContentClient(root=root)
    resolver=FulltextResolver(root,client)
    run=new_run_id('fulltext')
    target=Path(output) if output else root/'data/fulltext/runs'/(run+'.jsonl')
    if not target.is_absolute(): target=root/target
    assert_safe([str(source),str(target),str(evidence_file or '')],secrets)
    if target.exists() or target.resolve()==source.resolve(): raise FileExistsError('New sidecar output required')
    results={}
    try:
        for index,record in prioritized:
            try:
                lookup_method='doi' if record.get('doi') else 'openalex_work_id'
                work=client.lookup(record.get('doi') or record.get('openalex_id'),refresh=refresh)
                if work is None and record.get('doi'):
                    # Reuse only a prior exact-DOI identity, never a title match.
                    old=root/'data/cache/openalex'/(cache_key(record['doi'])+'.json')
                    if old.exists():
                        previous=json.loads(old.read_text());assert_safe(previous,secrets)
                        candidate=previous.get('raw') or {}
                        if candidate.get('id') and candidate.get('doi') and canonical_doi(candidate['doi'])==canonical_doi(record['doi']):
                            found=client.lookup(candidate['id'],refresh=refresh)
                            if found and canonical_doi(found.get('doi'))==canonical_doi(record['doi']):
                                work=found;lookup_method='prior_exact_doi_work_id'
                if metadata_only:
                    result=manifest(record,'unavailable',reason='metadata_only',openalex_id=(work or {}).get('id'),work_metadata=work)
                else: result=resolver.resolve(record,work=work,refresh=refresh)
                result['openalex_work_matched']=bool(work)
                result['work_lookup_method']=lookup_method
            except (FulltextError,ValueError,OSError,KeyError) as exc:
                result=manifest(record,'error',reason=exc.code if isinstance(exc,FulltextError) else 'invalid_record_or_cache',openalex_work_matched=False)
            results[index]=result
            print(f"Fulltext {index+1}/{len(records)}: {result['status']} ({result.get('format') or 'none'})",flush=True)
    finally:
        if own_client: client.close()
    if hashlib.sha256(source.read_bytes()).hexdigest()!=before: raise RuntimeError('Canonical input changed')
    rows=[results[i] for i in range(len(records))]
    report={'run_id':run,'canonical_input':str(source),'canonical_sha256':before,'records':len(rows),'openalex_work_matched':sum(r['openalex_work_matched'] for r in rows),'retrieved':sum(r['status']=='available' for r in rows),'parsed':sum(r.get('parse_status')=='parsed' for r in rows),'tei_retrieved':sum(r.get('format')=='tei_xml' and r['status']=='available' for r in rows),'pdf_retrieved':sum(r.get('format')=='pdf' and r['status']=='available' for r in rows),'http_stats':client.stats,'rate_limit_history':client.rate_limit_history,'fulltext_cache_hits':resolver.cache_hits,'sidecar':str(target)}
    write_jsonl(target,rows,secrets)
    write_json(target.with_suffix('.manifest.json'),report,secrets)
    print(json.dumps({k:report[k] for k in ('openalex_work_matched','retrieved','parsed','tei_retrieved','pdf_retrieved','http_stats')}))
    print('Fulltext sidecar:',target)
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('input');p.add_argument('--evidence');p.add_argument('--output');p.add_argument('--refresh',action='store_true');p.add_argument('--cache-only',action='store_true');p.add_argument('--metadata-only',action='store_true')
    a=p.parse_args()
    if a.refresh and a.cache_only: p.error('Refresh and cache-only cannot be combined')
    client=OpenAlexContentClient(cache_only=a.cache_only)
    try: resolve_file(a.input,a.evidence,client=client,output=a.output,refresh=a.refresh,metadata_only=a.metadata_only)
    except (ValueError,OSError,RuntimeError): print('Fulltext run failed; inspect safe sidecars.',file=sys.stderr);return 1
    finally: client.close()
    return 0

if __name__=='__main__': raise SystemExit(main())
