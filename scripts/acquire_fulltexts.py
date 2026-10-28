"""Acquire cached-link OA full texts and import user-authorized browser PDFs.

Results are independent sidecars; canonical records are never overwritten.
Each finished record is checkpointed before attempting the next download.
"""

import argparse
import csv
import hashlib
import json
import sys
from pathlib import Path

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fulltext.openalex_content import OpenAlexContentClient
from scripts.fulltext.resolver import FulltextResolver, verify_pdf_identity
from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.fulltext.provenance import FulltextError, manifest, safe_url, save_raw, save_parsed
from scripts.pipeline_utils import ROOT, assert_safe, known_secrets, read_jsonl, utc_now, write_json, write_jsonl
from scripts.normalize_record import normalize_doi


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_browser_pdf(record, item, root=ROOT):
    root=Path(root).resolve(); secrets=known_secrets(root)
    name=Path(item['file'])
    if name.is_absolute() or '..' in name.parts:
        raise FulltextError('unsafe_local_pdf_path')
    source=(root/name).resolve()
    try: source.relative_to(root)
    except ValueError: raise FulltextError('unsafe_local_pdf_path') from None
    raw=source.read_bytes(); parsed=parse_pdf(raw)
    verify_pdf_identity(parsed,record)
    if not parsed['body_paragraph_count']: raise FulltextError('pdf_requires_ocr')
    source_url=safe_url(item['source_url'],secrets)
    raw_file,digest=save_raw(raw,root/'data/fulltext/raw','pdf',secrets)
    parsed_file,parsed_sha=save_parsed(parsed,root/'data/fulltext/parsed',digest,secrets)
    public=item.get('access_basis')=='public_publisher_download'
    result=manifest(record,'available',source='publisher_browser' if public else 'campus_browser',format='pdf',license=None,version=None,
                    source_url=source_url,sha256=digest,raw_file=str(raw_file.relative_to(root)),
                    parsed_file=str(parsed_file.relative_to(root)),parsed_sha256=parsed_sha,parser=parsed['parser'],parse_status='parsed',pages=parsed['pages'],
                    access_basis='public_publisher_download' if public else 'user_authorized_institutional_session',identity_check='doi_on_first_page',
                    acquisition_method=item.get('acquisition_method','browser_download'),
                    browser_saved_at=item.get('retrieved_at'),local_original=str(source.relative_to(root)))
    key=hashlib.sha256(str(record['doi']).lower().encode()).hexdigest()
    path=root/'data/fulltext/manifests'/key/('browser_'+digest+'.json')
    result['manifest_file']=str(path.relative_to(root))
    if not path.exists(): write_json(path,result,secrets)
    return result


def acquire(input_file, metadata_file, output, local_map=None, root=ROOT, client=None,
            skip_archive=False, max_candidates=3, limit=None):
    root=Path(root).resolve(); target=root/Path(output)
    source=root/Path(input_file); metadata_path=root/Path(metadata_file)
    if target.exists(): raise FileExistsError('Fulltext run needs a new directory')
    records=read_jsonl(source); metadata=read_jsonl(metadata_path)
    by_meta={r['uid']:r for r in metadata}
    if len(by_meta)!=len(metadata) or set(by_meta)!={r['uid'] for r in records}:
        raise ValueError('Fulltext metadata identities differ from canonical input')
    for record in records:
        entry=by_meta[record['uid']]
        if normalize_doi(entry.get('doi'))!=normalize_doi(record.get('doi')):
            raise ValueError('Metadata DOI differs from canonical DOI')
        if entry.get('work') and normalize_doi(entry['work'].get('doi'))!=normalize_doi(record.get('doi')):
            raise ValueError('OpenAlex Work DOI differs from canonical DOI')
    local=json.loads((root/Path(local_map)).read_text()) if local_map else {}
    if not set(local).issubset(by_meta): raise ValueError('Local PDF identity is outside input')
    before=sha(source); metadata_before=sha(metadata_path); secrets=known_secrets(root)
    assert_safe([records,metadata,local],secrets)
    own_client=client is None
    client=client or OpenAlexContentClient(root=root,timeout=12,retries=0)
    resolver=FulltextResolver(root,client,include_archive=not skip_archive,max_candidates=max_candidates)
    target.mkdir(parents=True)
    prioritized=sorted(records,key=lambda r:(r['uid'] not in local,bool(r.get('abstract'))))
    selected={r['uid'] for r in prioritized[:limit]} if limit is not None else set(by_meta)
    results={}
    try:
        with (target/'checkpoint.jsonl').open('x',encoding='utf-8') as stream:
            for number,record in enumerate(prioritized,1):
                entry=by_meta[record['uid']];work=entry.get('work')
                try:
                    if record['uid'] not in selected:
                        result=manifest(record,'pending',reason='run_record_budget')
                    elif record['uid'] in local:
                        result=import_browser_pdf(record,local[record['uid']],root)
                    elif work is None:
                        result=manifest(record,'unavailable',reason='work_not_found')
                    else:
                        result=resolver.resolve(record,work=work)
                except (FulltextError,ValueError,OSError,KeyError) as exc:
                    result=manifest(record,'error',reason=exc.code if isinstance(exc,FulltextError) else 'invalid_or_unsafe_content')
                result.update(title=record.get('title'),had_abstract=bool(record.get('abstract')),
                              oa_status=(work or {}).get('open_access',{}).get('oa_status'),
                              doi_url='https://doi.org/'+record['doi'],metadata_source=str(metadata_path.relative_to(root)))
                if result.get('parsed_file'):
                    parsed=json.loads((root/result['parsed_file']).read_text())
                    txt='\n\n'.join(p['text'] for s in parsed['sections'] for p in s['paragraphs'])
                    assert_safe(txt,secrets)
                    text_path=target/(record['uid'].replace(':','_')+'.txt')
                    text_path.write_text(txt,encoding='utf-8')
                    result['text_file']=str(text_path.relative_to(root))
                    result['text_characters']=len(txt)
                    result['pages']=parsed.get('pages')
                assert_safe(result,secrets)
                stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush()
                results[record['uid']]=result
                print(f"Fulltext {number}/{len(records)}: {result['status']} {record['uid']}",flush=True)
    finally:
        if own_client: client.close()
    if sha(source)!=before or sha(metadata_path)!=metadata_before: raise RuntimeError('Protected input changed during acquisition')
    rows=[results[r['uid']] for r in records]
    write_jsonl(target/'fulltexts.jsonl',rows,secrets)
    fields=['uid','doi','title','had_abstract','oa_status','status','reason','source','pages','raw_file','parsed_file','doi_url']
    for name,chosen in [('inventory.csv',rows),('browser_queue.csv',[r for r in rows if r['status']!='available'])]:
        with (target/name).open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore');writer.writeheader();writer.writerows(chosen)
    summary={'canonical_input':str(source.relative_to(root)),'canonical_sha256':before,'metadata_sha256':metadata_before,
             'records':len(rows),'attempted_records':len(selected),'downloaded':sum(r['status']=='available' for r in rows),
             'downloaded_without_prior_abstract':sum(r['status']=='available' and not r['had_abstract'] for r in rows),
             'parsed':sum(r.get('parse_status')=='parsed' for r in rows),'archive_enabled':not skip_archive,
             'candidate_budget':max_candidates,'http_stats':client.stats,'created_at':utc_now(),
             'browser_queue':str((target/'browser_queue.csv').relative_to(root)),
             'status_counts':{s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})}}
    write_json(target/'manifest.json',summary,secrets)
    return summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input');parser.add_argument('--metadata',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--local-map');parser.add_argument('--skip-archive',action='store_true')
    parser.add_argument('--max-candidates',type=int,default=3);parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    if args.limit is not None and args.limit<1: parser.error('limit must be positive')
    try:
        result=acquire(args.input,args.metadata,args.output,args.local_map,skip_archive=args.skip_archive,
                       max_candidates=args.max_candidates,limit=args.limit)
        print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    except (ValueError,OSError,RuntimeError):
        print('Fulltext acquisition failed; inspect checkpoint and safe manifests.',file=sys.stderr);return 1


if __name__=='__main__': raise SystemExit(main())
