"""Run selected source-cell/PDF-location extraction through the existing evidence pipeline."""
import argparse, hashlib, json, re, sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from urllib.request import urlopen, Request
import pdfplumber

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'scripts/research_planning'))
import convert_historical_candidates as convert
import evidence_synthesis as synth

def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def obtain(path,url,expected):
    if not path.exists():
        if not url: raise FileNotFoundError(f'本地全文不存在且未提供公开地址: {path}')
        path.parent.mkdir(parents=True,exist_ok=True)
        with urlopen(Request(url,headers={'User-Agent':'curl/8.7.1'}),timeout=45) as response: data=response.read()
        if expected and hashlib.sha256(data).hexdigest()!=expected: raise ValueError('下载文件哈希与核查版本不一致')
        path.write_bytes(data)
    if expected and sha(path)!=expected: raise ValueError('本地来源哈希与核查版本不一致')

def discover(paths,out):
    """Read local files into source-located suggestions, never confirmed evidence."""
    suggestions=[]
    for path in paths:
        p=Path(path)
        try:
            item={'file':str(p),'sha256':sha(p),'status':'candidate_requires_manual_confirmation','hits':[]}
            if p.suffix.lower()=='.pdf':
                text_head=''
                with pdfplumber.open(p) as doc:
                    for i,page in enumerate(doc.pages):
                        text=page.extract_text() or ''
                        if i<2:text_head+=' '+text
                        for line in text.splitlines():
                            if re.search(r'irrigation|flood|wetting|drying|N\s*2?\s*O|nitrous|replica|treatment|kg.*ha',line,re.I):
                                item['hits'].append({'page':i+1,'text':line[:350],'status':'unverified_program_excerpt'})
                ids=re.findall(r'10\.\d{4,9}/[-._;()/:A-Z0-9]+',text_head,re.I)
                item['doi_candidates']=list(dict.fromkeys(x.rstrip('.,;') for x in ids))[:8]
            elif p.suffix.lower()=='.xlsx':
                with ZipFile(p) as z: sheets=[x for x in z.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml',x)]
                for sheet in sheets:
                    for cell,value in cells(p,sheet).items():
                        if re.search(r'N\s*2?\s*O|nitrous|AWD|flood|treatment|replica|kg.*ha|±',str(value),re.I):
                            item['hits'].append({'sheet':sheet,'cell':cell,'text':str(value)[:350],'status':'unverified_program_excerpt'})
            else: raise ValueError('仅支持 PDF 和 XLSX')
            patterns={'experiment':r'experiment|replica|random|plot|lysimeter',
                      'treatment':r'AWD|wetting|drying|intermittent',
                      'comparator':r'\bCF\b|continuous|flood',
                      'outcome':r'N\s*2?\s*O|nitrous|cumulative|seasonal',
                      'unit':r'kg|ha−|ha-|mg|μg|µg'}
            item['candidate_fields']={name:[hit for hit in item['hits'] if re.search(pattern,hit['text'],re.I)][:12] for name,pattern in patterns.items()}
            item['reason']='需从以上片段确认实验、组别、指标、单位及原文位置；不得自动当作核查事实。'
            suggestions.append(item)
        except (OSError,ValueError,KeyError) as e:suggestions.append({'file':str(p),'status':'failed','reason':str(e)})
    out.mkdir(parents=True,exist_ok=True);(out/'intake_candidates.json').write_text(json.dumps(suggestions,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'files':len(suggestions),'successful':sum(x['status'].startswith('candidate') for x in suggestions)}))
def cells(path,sheet):
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as z:
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
        result={}
        for c in ET.fromstring(z.read(sheet)).findall('.//x:c',ns):
            v=c.find('x:v',ns)
            if v is not None: result[c.get('r')]=strings[int(v.text)] if c.get('t')=='s' else v.text
        return result

def standardize(c):
    v=c['numeric_values'];unit=v.get('unit'); factors={'kg N2O/ha':1.0,'kg N-N2O/ha':44.0/28.0}
    factor=factors.get(unit)
    if c['outcome_family']!='n2o' or factor is None or v.get('comparator_unit')!=unit:
        a,b=v.get('intervention'),v.get('comparator')
        return {'status':'not_standardized_unit_or_indicator_unresolved','original':v,
                'relative_change_percent':(a/b-1)*100 if unit and unit==v.get('comparator_unit') and isinstance(a,(int,float)) and isinstance(b,(int,float)) and b!=0 else None,
                'relative_change_origin':'program_calculated_not_significance'}
    def scaled(value): return None if value is None else value*factor
    a,b=v.get('intervention'),v.get('comparator')
    return {'status':'standardized','original':v,'unit':'kg N2O/ha',
            'intervention':scaled(a),'comparator':scaled(b),
            'intervention_error':scaled(v.get('intervention_error')),'comparator_error':scaled(v.get('comparator_error')),
            'basis':'N2O-N to N2O: molecular mass ratio 44/28' if factor!=1 else 'original already N2O mass',
            'factor':factor,'relative_change_percent':None if a is None or b in (None,0) else (a/b-1)*100,
            'relative_change_origin':'program_calculated_not_significance'}

def report(normalized,summary,log,excluded):
    reviewed=sum(1 for c in normalized['comparisons'] if c['assessment']['source_verification']=='assistant_checked_candidate_not_human_gold')
    lines=['# 研究证据分析', '', '研究问题：'+normalized['topic_profile']['primary_question'], '',
           '工程运行：'+('部分记录失败' if any(x['status']!='parsed' for x in log) else '已完成')+f'；来源核查：{reviewed}/{len(normalized["comparisons"])} 条接入独立核查记录；配置或人工阅读本身不自动升级状态，均为 Candidate，未升级人工 Gold。','']
    papers={p['study_id']:p for p in normalized['papers']}
    for c in normalized['comparisons']:
        p=papers[c['study_id']]; e=p['experiments'][0];v=c['numeric_values'];n=c['standardized_n2o'];loc=c['raw_evidence'][0]
        checked=loc.get('pdf_verified') or {};anchor=(loc.get('original') or {}).get('anchor') or []
        hint=anchor[0] if anchor and isinstance(anchor[0],dict) else {}
        source=(checked.get('scope') or ('程序定位：'+str(hint.get('table_figure') or hint.get('section') or '未标章节')+' '+str(hint.get('cells') or '') if hint else '原文位置未核'))+'；PDF 物理页 '+str(checked.get('pages') or [x.get('page') for x in anchor if isinstance(x,dict)] or '未知')
        def show(x): return '未报告' if x is None else f'{x:.3f}'.rstrip('0').rstrip('.') if isinstance(x,(int,float)) else str(x)
        def group(x): return '；'.join(str(item.get('description') or item.get('code')) for item in x.get('levels',[])) or '未确认'
        rep=c['independent_replicates'] or {}; unit=(c['experimental_unit'] or {}).get('value') or '未知'
        design_status='处理层重复不足或未知（伪重复）' if re.search(r'pseudo.replicat|伪重复',str(rep.get('original') or ''),re.I) else '记录了独立重复，仍需核查随机化和实施细节' if (rep.get('value') or 0)>1 else '独立处理重复不足或未知'
        change=show(n.get('relative_change_percent'))
        standard=f"{show(n.get('intervention'))} ± {show(n.get('intervention_error'))} vs {show(n.get('comparator'))} ± {show(n.get('comparator_error'))} {n.get('unit','未知')}；处理相对对照变化 {change}%（程序计算）" if n['status']=='standardized' else '无法标准化：单位或指标未明确'
        if n['status']!='standardized' and n.get('relative_change_percent') is not None: standard+=f"；原口径相对变化 {change}%（程序计算）"
        lines += [f"## {p['title']}",f"[{p['doi']}]({p['source_url']})；比较 `{c['comparison_id']}`；{source}。",
                  f"研究对象与设计：{e.get('research_object_candidate')}；{e.get('design_candidate')}；观察时间：{c.get('observation_time') or '未知'}。",
                  f"处理：{c['intervention']}（{group(c['treatment_details'])}）；对照：{c['comparator']}（{group(c['comparator_details'])}）。",
                  f"原始值：{show(v['intervention'])} ± {show(v['intervention_error'])} vs {show(v['comparator'])} ± {show(v['comparator_error'])} {v['unit']}；误差类型 {v['intervention_error_type']}/{v['comparator_error_type']}；显著性：{c['significance']}。",
                  f"主题纳入：{'符合主题的候选，待来源核查' if c['eligibility']=='primary_candidate_requires_source_review' else c['eligibility']}；实验单位：{unit}，每处理 {show(rep.get('value'))}；设计有效性：{design_status}。{rep.get('original') or ''}",
                  f"标准化值：{standard}。换算依据：{n.get('basis','未知')}；研究内变化不代表统计显著性或因果效应。",
                  f"未解决事项：{'；'.join(c['conflicts']) if c['conflicts'] else '无已记录事项'}。",'']
    status='证据不足，不能形成跨研究共同结论' if not summary['findings'] else '存在有限条件下的候选共同观察，仍需人工核查'
    lines+=['## 跨研究判断',f"科研结论：{status}；共同观察数：{len(summary['findings'])}（状态 `{summary['primary_conclusion']}`）。"]
    for p in summary['pairwise_comparability']:
        quantitative='可考虑，仍待来源和独立性核查' if p['B_conditional_quantitative']=='possible_pending_source_and_independence_review' else '尚不能建立'
        lines.append(f"- {p['study_ids']}，比较 {p['comparison_ids']}：可附条件描述性并列；条件性定量比较{quantitative}；统计推断条件未建立，未实现统计合并。原因：{'；'.join(p['reasons'])}。")
        if p.get('unit_normalization')=='same_n2o_mass_basis': lines.append('  已统一为 N₂O 质量口径：仅 N₂O-N 记录使用 44/28，原为 N₂O 的记录保持不变；这不解决处理、设计或统计可比性。')
    lines += ['','设计有效性、来源完整性和主题相关性分别判断；伪重复或重复未知不推断有效因果效应。未报告误差不推断显著性，不进行 Meta 分析。']
    for x in log:
        if x['status']!='parsed': lines.append(f"- 解析失败 {x['doi']}：{x['reason']}。")
    for x in excluded: lines.append(f"- 新研究未纳入 [{x['doi']}](https://doi.org/{x['doi']})：{x['reason']}。")
    return '\n'.join(lines)+'\n'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path);ap.add_argument('--sources',type=Path);ap.add_argument('--corrections',type=Path);ap.add_argument('--discover',type=Path,nargs='+');ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    if a.discover: discover(a.discover,a.out);return
    if not a.manifest or not a.sources: ap.error('综合需要 --manifest 和 --sources；候选提取使用 --discover')
    config=json.loads(a.manifest.read_text());out=a.out;rows={k:[] for k in ('papers','fields','results','issues','historic_claims','supplement_files')};checks=[];log=[]
    changes=[]
    if a.corrections:
        for edit in json.loads(a.corrections.read_text()):
            matched=[p for p in config['papers'] if p['doi'].lower()==edit['doi'].lower()]
            if len(matched)!=1 or not edit.get('reason') or not edit.get('actor'): raise ValueError('人工修改必须唯一指定 DOI、原因和操作者')
            paper=matched[0];key=edit['field'];changes.append({'doi':edit['doi'],'field':key,'before':paper.get(key),'after':edit['value'],'reason':edit['reason'],'actor':edit['actor'],'status':'manual_edit_not_gold'})
            paper[key]=edit['value']
    for p in config['papers']:
        try:
            source=a.sources/p['source'];pdf=a.sources/p['pdf']
            obtain(source,p.get('source_url'),p.get('source_sha256'));obtain(pdf,p.get('pdf_url'),p.get('pdf_sha256'))
            sh=sha(source);ph=sha(pdf);doi=p['doi'];eid=p['experiment_id']
            with pdfplumber.open(pdf) as document:
                pg=document.pages[p['pdf_page']-1]
                if p.get('font_filter'): pg=pg.filter(lambda o:o.get('object_type')=='char' and re.search(p['font_filter'],o.get('fontname','')))
                if p.get('columns')=='two_columns_left_then_right':
                    page=(pg.crop((0,0,pg.width/2,pg.height)).extract_text() or '')+'\n'+(pg.crop((pg.width/2,0,pg.width,pg.height)).extract_text() or '')
                else: page=pg.extract_text(layout=True)
            if p['source_type']=='xlsx':
                values=cells(source,p['sheet']);aa,bb=values[p['a_cell']],values[p['b_cell']]
                if not p.get('cell_checks') or not p.get('unit') or any(values.get(cell)!=expected for cell,expected in p['cell_checks'].items()):
                    raise ValueError('表格处理标签与人工指定位置不一致')
                patt=r'\s*([\d.]+)\s*±\s*([\d.]+)\s*([a-z]+)\s*'
                ma,mb=re.fullmatch(patt,aa),re.fullmatch(patt,bb)
                if not ma or not mb:raise ValueError('无法读取处理均值、误差与字母')
                value=f'{ma[1]} ± {ma[2]} versus {mb[1]} ± {mb[2]}'
                significance=f"{p.get('table_label','table')} letters {ma[3]} vs {mb[3]}; {p.get('significance_method','method unknown')}"
                ev={'page':p['pdf_page'],'table_figure':p.get('table_label'),'cells':[p['a_cell'],p['b_cell']],'quote':f"{p['a_label']} {aa}; {p['b_label']} {bb}",'footnote':p.get('error_type','unknown')}
                treatment=p['a_label']; comparator=p['b_label']
            elif p['source_type']=='pdf_regex':
                assertions=p.get('context_assertions') or {}
                source_order=p.get('source_order',[p['a_label'],p['b_label']])
                if assertions.get('outcome')!='N2O' or assertions.get('unit')!=p['unit'] or assertions.get('treatment_order')!=source_order or set(source_order)!={p['a_label'],p['b_label']} or not assertions.get('year') or assertions['year'] not in p['period']:
                    raise ValueError('PDF 指标、年份、单位或处理顺序缺少一致的上下文声明')
                matches=list(re.finditer(p['pattern'],page,re.I|re.S))
                if len(matches)!=1: raise ValueError(f'PDF 上下文匹配数为 {len(matches)}，需人工重新核对位置')
                m=matches[0]
                requirements=p.get('context_patterns') or {}
                context_text=m.group(0)
                if p.get('additional_context_page'):
                    with pdfplumber.open(pdf) as document: context_text+='\n'+(document.pages[p['additional_context_page']-1].extract_text() or '')
                if set(requirements)!={'outcome','year','unit','order'} or any(not re.search(pattern,context_text,re.I|re.S) for pattern in requirements.values()):
                    raise ValueError('PDF 匹配区域未同时确认指标、年份、单位及处理顺序')
                va=m.group('a') if 'a' in m.groupdict() else m[1];vb=m.group('b') if 'b' in m.groupdict() else m[2]
                treatment=p['a_label'];comparator=p['a_label']+' vs '+p['b_label'];value={p['a_label']:float(va),p['b_label']:float(vb)}
                sig_a=m.groupdict().get('a_sig');sig_b=m.groupdict().get('b_sig')
                significance=(f'原表标记：处理 {sig_a or "未标"}；对照 {sig_b or "未标"}；含义须按表注核查' if sig_a or sig_b else 'unknown: separate cumulative comparison significance not present in selected passage')
                ev={'page':p['pdf_page'],'section':p.get('table_label','N2O emissions'),'quote':page[max(m.start(),m.start(1)-100):min(len(page),m.end(2)+100)],'context_assertions':assertions,'note':'Group errors not extracted; significance only when table markers captured.'}
            else:raise ValueError('未知来源格式')
            rows['papers'].append({'doi':doi,'title':p['title'],'raw_sha256':ph,'raw_file':str(pdf)})
            for field,val in [('study_design',p['design']),('research_object',p['object']),('independent_replicates',p['replicates'])]:
                rows['fields'].append({'doi':doi,'field':field,'value':val,'status':'candidate_manual_source_review','source_sha256':ph,'evidence':[{'page':2,'section':'methods'}]})
            rows['results'].append({'doi':doi,'index':1,'indicator':'seasonal_n2o','treatment':treatment,'comparator':comparator,'time':p['period'],'value':value,'unit':p['unit'],'significance':significance,'experiment_id':eid,'source_sha256':sh,'reporting_origin':'program_source_table_extraction','record_class':'candidate_not_human_gold','evidence':[ev],
              'treatment_levels':[{'code':'AWD','description':p['treatment_description']}], 'comparator_levels':[{'code':'CF','description':p['control_description']}],
              'limitations':p.get('source_conflict') or 'Source selection and design manually checked; independent treatment replication and cross-study context must be verified.'})
            log.append({'doi':doi,'source_sha256':sh,'pdf_sha256':ph,'source_type':p['source_type'],'selection':p['scope'],'status':'parsed'})
        except (ValueError,KeyError,IndexError,OSError) as exc:
            log.append({'doi':p.get('doi'),'status':'failed_not_confirmed','reason':str(exc)})
    d=out/'candidates';d.mkdir(parents=True,exist_ok=True)
    for name,data in rows.items():(d/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data))
    converted=convert.run(d,None,{p['doi'].lower() for p in config['papers']}); normalized=synth.adapt_converted(converted,config['topic']);synth.apply_source_checks(normalized,{'checks':checks})
    for c in normalized['comparisons']:
        c['standardized_n2o']=standardize(c)
        rep=c.get('independent_replicates') or {}
        c['assessment']={'topic_eligibility':c['eligibility'],
                         'program_parse':'success', 'source_location':'program_reported_not_independently_confirmed',
                         'manual_check':'not_recorded_in_pipeline', 'gold':'not_gold',
                         'source_verification':'assistant_checked_candidate_not_human_gold' if any(e.get('pdf_verified') for e in c['raw_evidence']) else 'not_checked',
                         'design_validity':'insufficient_replicated_treatment' if re.search(r'pseudo.replicat|伪重复',str(rep.get('original') or ''),re.I) or rep.get('value')==1 else 'replication_reported_requires_review' if (rep.get('value') or 0)>1 else 'unknown'}
    summary=synth.synthesize_converted(normalized)
    by_id={c['comparison_id']:c for c in normalized['comparisons']}
    for pair in summary['pairwise_comparability']:
        members=[by_id[x] for group in pair['comparison_ids'] for x in group]
        if members and all(x['standardized_n2o'].get('status')=='standardized' and x['standardized_n2o'].get('unit')=='kg N2O/ha' for x in members):
            pair['unit_normalization']='same_n2o_mass_basis'
            pair['reasons']=[('原始单位不同，已统一为 N₂O 质量；其他可比性条件仍需核查' if x=='结果数值或报告效应的单位和口径不同或未知' else x) for x in pair['reasons']]
    summary['acceptance']={'engineering':'partial' if any(x['status']!='parsed' for x in log) else 'completed',
        'data_review':'see_per_comparison_source_verification',
        'design_validity':'assessed_per_comparison_not_automatically_causal',
        'cross_study_comparability':'see_pairwise_comparability',
        'scientific_conclusion':summary['primary_conclusion'],
        'new_paper':'not_included_full_text_unavailable' if config.get('selection_attempts') else 'not_assessed'}
    for name,data in [('conversion',converted),('normalized_evidence',normalized),('synthesis',summary),('run_log',{'source_runs':log,'selection_attempts':config.get('selection_attempts',[]),'manual_corrections':changes})]:
        (out/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    (out/'analysis.md').write_text(report(normalized,summary,log,config.get('selection_attempts',[])),encoding='utf8')
    print(json.dumps({'papers':len(normalized['papers']),'comparisons':len(normalized['comparisons']),'findings':len(summary['findings']),'pairs':len(summary['pairwise_comparability'])}))
if __name__=='__main__':main()
