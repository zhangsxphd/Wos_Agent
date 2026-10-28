"""Read-only PDF layout probe; optionally run using a pdfplumber runtime.

Input JSONL contains uid, raw_file and sha256. Output is new JSONL with first
two pages of extracted text and word coordinates for review, never a judgment
that an abstract is correct. Licensed PDF bytes stay in the local project.
"""
import argparse
import hashlib
import json
from pathlib import Path

import pdfplumber


def run(jobs, root, output):
    root=Path(root).resolve(); target=Path(output)
    if target.exists(): raise FileExistsError('probe_output_exists')
    results=[]
    for item in jobs:
        row={'uid':item['uid'],'sha256':item['sha256'],'parser':'pdfplumber',
             'parser_version':pdfplumber.__version__,'pages':[],'status':'error'}
        try:
            name=Path(item['raw_file'])
            if name.is_absolute() or '..' in name.parts: raise ValueError('unsafe_path')
            path=(root/name).resolve();path.relative_to(root)
            if hashlib.sha256(path.read_bytes()).hexdigest()!=item['sha256']: raise ValueError('hash_mismatch')
            with pdfplumber.open(path) as doc:
                row['page_count']=len(doc.pages)
                for number,page in enumerate(doc.pages[:2],1):
                    words=page.extract_words() or []
                    row['pages'].append({'page':number,'width':page.width,'height':page.height,
                        'text':page.extract_text() or '',
                        'words':[[w['text'],round(w['x0'],2),round(w['x1'],2),round(w['top'],2),round(w['bottom'],2)] for w in words]})
            row['status']='readable' if row['pages'] else 'no_pages'
        except Exception as exc:
            row['error_type']=type(exc).__name__
        results.append(row)
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('x',encoding='utf-8') as stream:
        for row in results: stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    return results


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--jobs',required=True);parser.add_argument('--root',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args()
    with open(args.jobs,encoding='utf-8') as stream: jobs=[json.loads(x) for x in stream if x.strip()]
    rows=run(jobs,args.root,args.output)
    print(json.dumps({'papers':len(rows),'readable':sum(x['status']=='readable' for x in rows)}))
