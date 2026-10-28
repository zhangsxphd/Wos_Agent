"""Create a new inventory with reproducible optional PDF parses; preserve inputs."""
import argparse
import copy
import hashlib
from pathlib import Path

from scripts.fulltext.admission import inspect_fulltext,project_file
from scripts.fulltext.pdf_metadata import parse_pdf,parse_pdfplumber
from scripts.fulltext.provenance import save_parsed
from scripts.fulltext.resolver import verify_pdf_identity
from scripts.normalize_record import normalize_doi
from scripts.pipeline_utils import ROOT,known_secrets,read_jsonl,write_json,write_jsonl,utc_now


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reprocess(canonical,inventory,uids,output,root=ROOT):
    root=Path(root).resolve();source=Path(canonical);inventory_path=Path(inventory)
    records=read_jsonl(source);rows=read_jsonl(inventory_path)
    by_record={r['uid']:r for r in records};by_row={r['uid']:r for r in rows}
    if len(by_record)!=len(records) or len(by_row)!=len(rows) or set(by_record)!=set(by_row):raise ValueError('inventory_identity_coverage_mismatch')
    if len(uids)!=len(set(uids)) or not set(uids).issubset(by_record):raise ValueError('invalid_requested_uid')
    target=(root/Path(output)).resolve()
    try:target.relative_to(root)
    except ValueError:raise ValueError('unsafe_output_path') from None
    target.mkdir(parents=True,exist_ok=False);secrets=known_secrets(root);results=[]
    for uid in uids:
        record=by_record[uid];old=by_row[uid]
        if normalize_doi(record.get('doi'))!=normalize_doi(old.get('doi')):raise ValueError('doi_mismatch')
        raw_path=project_file(root,old.get('raw_file'));raw=raw_path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=old.get('sha256'):raise ValueError('raw_hash_mismatch')
        verify_pdf_identity(parse_pdf(raw),record)
        parsed=parse_pdfplumber(raw)
        parsed_file,parsed_sha=save_parsed(parsed,target/'parsed',old['sha256'],secrets)
        fresh={**old,'previous_parsed_file':old.get('parsed_file'),'parsed_file':str(parsed_file.relative_to(root)),
               'parsed_sha256':parsed_sha,'parser':parsed['parser'],'parse_status':'parsed',
               'identity_status':'doi_match','identity_check':'doi_on_first_page',
               'reading_order_review':'required','reprocessed_at':utc_now()}
        _,check=inspect_fulltext(record,fresh,root)
        by_row[uid]=fresh;results.append({'uid':uid,'doi':record['doi'],'raw_sha256':old['sha256'],
            'parsed_sha256':parsed_sha,'technical_admission':check['admission'],
            'reading_order_review':'required','human_review_status':'pending'})
    write_jsonl(target/'fulltexts.jsonl',[by_row[r['uid']] for r in records],secrets)
    manifest={'created_at':utc_now(),'canonical_sha256':digest(source),'old_inventory_sha256':digest(inventory_path),
              'new_inventory_sha256':digest(target/'fulltexts.jsonl'),'results':results,
              'technical_admission_does_not_certify_reading_order':True}
    write_json(target/'manifest.json',manifest,secrets);return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('canonical');p.add_argument('--inventory',required=True)
    p.add_argument('--uids',nargs='+',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(reprocess(a.canonical,a.inventory,a.uids,a.output)['results'])
