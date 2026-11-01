"""Folder intake using the existing PDF parser and candidate extractor."""
import csv
import hashlib
import json
import re
from pathlib import Path

from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.fulltext.resolver import verify_pdf_identity
from scripts.fulltext.provenance import FulltextError
from scripts.research_planning.evidence_synthesis import synthesize_converted


def run_folder(folder, topic, out, discover, review_path=None):
    folder, out = Path(folder), Path(out)
    files=sorted(folder.glob('*.pdf'))+sorted(folder.glob('*.xlsx'))
    if not files: raise ValueError('文件夹没有 PDF 或 XLSX')
    out.mkdir(parents=True,exist_ok=True)
    pdfs=[]; inventory=[]; pages_by_hash={}
    for p in files:
        raw=p.read_bytes(); sha=hashlib.sha256(raw).hexdigest()
        if p.suffix.lower()=='.xlsx':
            inventory.append({'file':str(p),'sha256':sha,'status':'supplement_unlinked','doi':None,'reason':'没有可靠 DOI 关联，须人工指定'});continue
        try:
            parsed=parse_pdf(raw)
            first=' '.join(x['text'] for x in parsed['sections'][0]['paragraphs'])
            ids=list(dict.fromkeys(x.rstrip('.,;:)]}').lower() for x in re.findall(r'10\.\d{4,9}/[^\s<>"\'\[\]{}]+',first,re.I)))
            ids=[x for x in ids if not x.endswith('-')]
            valid=[]
            for doi in ids:
                try: verify_pdf_identity(parsed,{'doi':doi});valid.append(doi)
                except FulltextError: pass
            pages_by_hash[sha]=[' '.join(x['text'] for x in s['paragraphs']) for s in parsed['sections']]
            status='body_parsed_doi_self_reported' if len(valid)==1 and parsed['body_paragraph_count'] else 'identity_or_body_needs_review'
            inventory.append({'file':str(p),'sha256':sha,'doi':valid[0] if len(valid)==1 else None,'doi_candidates':ids,'status':status,'pages':parsed['pages'],
                              'reason':'PDF 内 DOI 自洽，未与外部题录独立比对' if status.startswith('body') else 'DOI 不唯一或正文不足'})
            if parsed['body_paragraph_count']:pdfs.append(p)
        except (FulltextError,ValueError,OSError,KeyError) as exc:
            inventory.append({'file':str(p),'sha256':sha,'status':'failed','reason':str(exc),'doi':None})
    # Existing candidate extraction is run on real sources, not prewritten comparisons.
    cache_key=hashlib.sha256(''.join(hashlib.sha256(p.read_bytes()).hexdigest() for p in pdfs).encode()).hexdigest()
    cache=out/'candidates'/'input_hash.txt'
    if pdfs and (not cache.exists() or cache.read_text()!=cache_key):
        if cache.exists(): raise ValueError('输入文件已变化，请使用新输出目录以保留原核查记录')
        discover(pdfs,out/'candidates');cache.write_text(cache_key)
    reviews=json.loads(Path(review_path).read_text()) if review_path else []
    accepted=[]
    for item in reviews:
        record=next((x for x in inventory if x['sha256']==item.get('pdf_sha256')),None)
        if not record or not item.get('reviewer') or not item.get('page') or not item.get('quote') or not item.get('field'):
            raise ValueError('审阅记录需匹配文件哈希、审阅者、页码、字段与原句')
        page=item['page']
        if not isinstance(page,int) or page<1 or page>len(pages_by_hash[item['pdf_sha256']]): raise ValueError('审阅页码无效')
        canon=lambda x:re.sub(r'\s+',' ',x).strip().casefold()
        if canon(item['quote']) not in canon(pages_by_hash[item['pdf_sha256']][page-1]):
            raise ValueError('审阅原句未在所选页找到；请人工检查断词和页码')
        accepted.append({**item,'doi':record['doi'],'status':'reviewer_assertion_quote_located_not_gold',
                         'identity':'reviewer_self_declared','evidence_type':item.get('evidence_type','author_reported_candidate')})
    (out/'inventory.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    (out/'reviewed_fields.json').write_text(json.dumps(accepted,ensure_ascii=False,indent=2)+'\n')
    # Free-form reviewer observations are not converted into treatment pairs.
    # Only the established converter may create quantitative comparisons.
    normalized={'topic_profile':{'topic':topic,'primary_question':topic},'papers':[],
                'comparisons':[],'source_observations':accepted}
    summary=synthesize_converted(normalized)
    (out/'normalized_evidence.json').write_text(json.dumps(normalized,ensure_ascii=False,indent=2)+'\n')
    (out/'synthesis.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    with (out/'evidence.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=['doi','field','value','unit','error','significance','page','quote','reviewer','status','evidence_type']);writer.writeheader()
        for row in accepted:writer.writerow({k:row.get(k,'') for k in writer.fieldnames})
    def rows(items): return '\n'.join('| '+' | '.join(str(x).replace('|','/').replace('\n',' ') for x in row)+' |' for row in items)
    text=['# PDF 科研证据整理','',f'研究问题：{topic}','','## 论文及解析状态','',
          '| DOI | 状态 | 文件哈希 |','|---|---|---|',rows([(x.get('doi') or '未知',x['status'],x['sha256']) for x in inventory]),
          '','## 已定位的研究设计、处理及对照','', '| DOI | 字段 | 审阅记录 | 原文页 | 类型 |','|---|---|---|---|---|',
          rows([(x['doi'],x['field'],x.get('value','未知'),x['page'],x['evidence_type']) for x in accepted if x['field'] in ('design','treatments','control','replicates','population')]) or '| 未核查 | 未知 | 未知 | 未知 | 未知 |',
          '','## 结果及统计信息','', '| DOI | 指标 | 值和单位 | 误差/显著性 | 原文页 | 类型 |','|---|---|---|---|---|---|',
          rows([(x['doi'],x['field'],str(x.get('value','未知'))+' '+str(x.get('unit','')),str(x.get('error','未知'))+' / '+str(x.get('significance','未知')),x['page'],x['evidence_type']) for x in accepted if x['field'] not in ('design','treatments','control','replicates','population')]) or '| 未核查 | 未知 | 未知 | 未知 | 未知 | 未知 |',
          '','## 来源、可比性与待核','', '原文定位及逐条引句见 evidence.csv；人工输入的审阅者身份未独立认证，均未升级 Gold。',
          '目前未建立可靠的跨论文同一处理与结局比较；综合状态：'+summary['primary_conclusion']+'；证据不足，不推断共同发现。',
          '候选与附件关联须核查；缺少明确处理、独立重复、数值、单位和统计比较的记录不能作定量结论。',
          '作者解释和系统推断不能替代实验事实；本报告不将未验证机制列为事实。']
    (out/'analysis.md').write_text('\n'.join(text)+'\n',encoding='utf8')
    return {'files':len(files),'pdf_parsed':len(pdfs),'quote_located':len(accepted),'status':'candidate_review_required'}
