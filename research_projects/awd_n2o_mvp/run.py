"""Run selected source-cell/PDF-location extraction through the existing evidence pipeline."""
import argparse, hashlib, json, re, sys
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from urllib.request import urlopen, Request

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
    import pdfplumber
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
                item['table_rows']=[]
                for sheet in sheets:
                    table=cells(p,sheet)
                    for cell,value in table.items():
                        if re.search(r'N\s*2?\s*O|nitrous|AWD|\bCF\b|\bPF\b|flood|treatment|replica|kg.*ha|±',str(value),re.I):
                            item['hits'].append({'sheet':sheet,'cell':cell,'text':str(value)[:350],'status':'unverified_program_excerpt'})
                    grouped={}
                    for cell,value in table.items():
                        m=re.fullmatch(r'([A-Z]+)(\d+)',cell)
                        if m:grouped.setdefault(m[2],[]).append({'cell':cell,'value':value})
                    group_hint=None
                    for row,entries in sorted(grouped.items(),key=lambda x:int(x[0])):
                        for entry in entries:
                            if re.fullmatch(r'A\d+',entry['cell']) and re.fullmatch(r'(CF|PF|AWD\d*)',str(entry['value']).strip(),re.I):
                                group_hint={'label':str(entry['value']),'source_cell':entry['cell']}
                        if any('±' in str(e['value']) for e in entries):
                            label=next((e for e in entries if re.fullmatch(r'B\d+',e['cell'])),None)
                            item['table_rows'].append({'sheet':sheet,'row':int(row),'group_candidate':group_hint,'row_label_candidate':label,'cells':entries,'status':'same_row_candidate_not_control_pair'})
            else: raise ValueError('仅支持 PDF 和 XLSX')
            patterns={'experiment':r'experiment|replica|random|plot|lysimeter',
                      'treatment':r'AWD|wetting|drying|intermittent',
                      'comparator':r'\bCF\b|continuous|flood',
                      'outcome':r'N\s*2?\s*O|nitrous|cumulative|seasonal',
                      'unit':r'kg|ha−|ha-|mg|μg|µg'}
            patterns['other_factors']=r'fertili[sz]er|nitrogen|straw|urea'
            item['candidate_fields']={name:[hit for hit in item['hits'] if re.search(pattern,hit['text'],re.I)][:12] for name,pattern in patterns.items()}
            item['numeric_candidates']=[]
            for hit in item['hits']:
                units=re.findall(r'(?:kg|mg|μg|µg|g)\s*(?:N2O(?:-N)?|N-N2O|CO2eq)?\s*(?:/\s*ha|ha\s*[−-]?1|m\s*[−-]?2)',hit['text'],re.I)
                unit_candidate=units[0] if len(set(units))==1 else None
                for match in re.finditer(r'(?<![\w.])(-?\d+(?:\.\d+)?)\s*(?:±\s*(\d+(?:\.\d+)?))?',hit['text']):
                    item['numeric_candidates'].append({'value_candidate':float(match[1]),'error_candidate':float(match[2]) if match[2] else None,
                        'unit_candidate':unit_candidate,'outcome_candidate':None,'source':hit,'status':'unlinked_candidate_do_not_assume_result'})
                    if len(item['numeric_candidates'])>=50:break
                if len(item['numeric_candidates'])>=50:break
            item['reason']='需从以上片段确认实验、组别、指标、单位及原文位置；不得自动当作核查事实。'
            suggestions.append(item)
        except (OSError,ValueError,KeyError) as e:suggestions.append({'file':str(p),'status':'failed','reason':str(e)})
    out.mkdir(parents=True,exist_ok=True);(out/'intake_candidates.json').write_text(json.dumps(suggestions,ensure_ascii=False,indent=2)+'\n')
    draft={'topic':{'topic':None,'population_requirement':'rice','primary_question':None,'primary_eligibility':[]},'papers':[]}
    for item in suggestions:
        if not item['status'].startswith('candidate'): continue
        p=Path(item['file']);ids=item.get('doi_candidates',[])
        def rank(hit):
            t=hit['text'];return sum(bool(re.search(p,t,re.I)) for p in (r'N\s*2\s*O|nitrous',r'AWD|wetting|drying',r'\bCF\b|continuous|flood',r'table|figure|±',r'replica|random'))
        compact={k:[{'source':{a:h[a] for a in ('page','sheet','cell') if a in h},'text':h['text'][:180]} for h in sorted(v,key=rank,reverse=True)[:3]] for k,v in item['candidate_fields'].items()}
        names=[]
        for h in item['hits']:
            for name in re.findall(r'\b(?:AWD\d*|CF|PF)\b',h['text'],re.I):
                if name.upper() not in [x['name'] for x in names]:names.append({'name':name.upper(),'source':{a:h[a] for a in ('page','sheet','cell') if a in h}})
        draft['papers'].append({'include':False,'candidate':{'doi':ids[0] if len(ids)==1 else None,'source':str(p),'source_sha256':item['sha256'],
            'source_type':'xlsx' if p.suffix.lower()=='.xlsx' else 'pdf_regex','pdf':str(p) if p.suffix.lower()=='.pdf' else None,
            'pdf_sha256':item['sha256'] if p.suffix.lower()=='.pdf' else None},'doi_candidates':ids,
            'treatment_name_candidates':names[:8], 'field_suggestions':compact,'table_rows':item.get('table_rows',[])[:12],
            'numeric_candidates':[{'value_candidate':v['value_candidate'],'error_candidate':v['error_candidate'],'unit_candidate':v['unit_candidate'],
                'source':{a:v['source'][a] for a in ('page','sheet','cell') if a in v['source']},'status':'unlinked_not_confirmed'} for v in sorted(item['numeric_candidates'],key=lambda z:z['error_candidate'] is not None,reverse=True)[:3]],
            'required_fields':['title','pdf','pdf_page','indicator','unit','a_label','b_label','experiment_id','design','object','replicates','period','treatment_description','control_description','scope','source-specific locator'],
            'edits':[],'draft_status':'requires_human_confirmation_not_runnable'})
    for entry in draft['papers']:
        entry['candidate_digest']=hashlib.sha256(json.dumps(entry['candidate'],sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    (out/'manifest_draft.json').write_text(json.dumps(draft,ensure_ascii=False,indent=2)+'\n')
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
    conversion=c.get('gwp_conversion')
    if c['outcome_family']=='n2o_gwp' and unit=='kg CO2eq/ha' and conversion and conversion.get('source_verified_by_program') and conversion.get('factor',0)>0:
        factor=1.0/conversion['factor']
    elif c['outcome_family'] not in ('n2o_mass','n2o_n_mass'): factor=None
    if factor is None or v.get('comparator_unit')!=unit:
        a,b=v.get('intervention'),v.get('comparator')
        return {'status':'not_standardized_unit_or_indicator_unresolved','original':v,
                'relative_change_percent':(a/b-1)*100 if unit and unit==v.get('comparator_unit') and isinstance(a,(int,float)) and isinstance(b,(int,float)) and b!=0 else None,
                'relative_change_origin':'program_calculated_not_significance'}
    def scaled(value): return None if value is None else value*factor
    a,b=v.get('intervention'),v.get('comparator')
    return {'status':'standardized','original':v,'unit':'kg N2O/ha',
            'intervention':scaled(a),'comparator':scaled(b),
            'intervention_error':scaled(v.get('intervention_error')),'comparator_error':scaled(v.get('comparator_error')),
            'basis':('N2O-specific GWP divided by the paper factor; estimate' if c['outcome_family']=='n2o_gwp' else 'N2O-N to N2O: molecular mass ratio 44/28' if factor!=1 else 'original already N2O mass'),
            'conversion_source':conversion if c['outcome_family']=='n2o_gwp' else None,
            'derived_status':'program_estimate_not_directly_reported_mass' if c['outcome_family']=='n2o_gwp' else 'standardized_not_gold',
            'factor':factor,'relative_change_percent':None if a is None or b in (None,0) else (a/b-1)*100,
            'relative_change_origin':'program_calculated_not_significance'}

def report(normalized,summary,log,excluded):
    reviewed=sum(1 for c in normalized['comparisons'] if c['assessment']['source_verification']=='assistant_checked_candidate_not_human_gold')
    lines=['# 研究证据分析', '', '研究问题：'+normalized['topic_profile']['primary_question'], '',
           '工程运行：'+('部分记录失败' if any(x['status']=='failed_not_confirmed' for x in log) else '已完成')+f'；来源核查：{reviewed}/{len(normalized["comparisons"])} 条接入独立核查记录；配置或人工阅读本身不自动升级状态，均为 Candidate，未升级人工 Gold。','']
    papers={p['study_id']:p for p in normalized['papers']}
    for c in normalized['comparisons']:
        p=papers[c['study_id']]; e=p['experiments'][0];v=c['numeric_values'];n=c['standardized_n2o'];loc=c['raw_evidence'][0]
        checked=loc.get('pdf_verified') or {};anchor=(loc.get('original') or {}).get('anchor') or []
        hint=anchor[0] if anchor and isinstance(anchor[0],dict) else {}
        location_origin='人工读图定位：' if loc.get('manual_reported') is not None else '程序定位：'
        source=(checked.get('scope') or (location_origin+str(hint.get('table_figure') or hint.get('section') or '未标章节')+' '+str(hint.get('cells') or '') if hint else '原文位置未核'))+'；PDF 物理页 '+str(checked.get('pages') or [x.get('page') for x in anchor if isinstance(x,dict)] or '未知')
        def show(x): return '未报告' if x is None else f'{x:.3f}'.rstrip('0').rstrip('.') if isinstance(x,(int,float)) else str(x)
        def group(x): return '；'.join(str(item.get('description') or item.get('code')) for item in x.get('levels',[])) or '未确认'
        rep=c['independent_replicates'] or {}; unit=(c['experimental_unit'] or {}).get('value') or '未知'
        design_status='处理层重复不足或未知（伪重复）' if re.search(r'pseudo.replicat|伪重复',str(rep.get('original') or ''),re.I) else '记录了独立重复，仍需核查随机化和实施细节' if (rep.get('value') or 0)>1 else '独立处理重复不足或未知'
        change=show(n.get('relative_change_percent'))
        change_text=f'{change}%（程序计算）' if n.get('relative_change_percent') is not None else '未计算'
        standard=f"{show(n.get('intervention'))} ± {show(n.get('intervention_error'))} vs {show(n.get('comparator'))} ± {show(n.get('comparator_error'))} {n.get('unit','未知')}；处理相对对照变化 {change_text}" if n['status']=='standardized' else '无法标准化：单位或指标未明确'
        if n['status']!='standardized' and n.get('relative_change_percent') is not None: standard+=f"；原口径相对变化 {change}%（程序计算）"
        lines += [f"## {p['title']}",f"[{p['doi']}]({p['source_url']})；比较 `{c['comparison_id']}`；{source}。",
                  f"研究对象与设计：{e.get('research_object_candidate')}；{e.get('design_candidate')}；场景 {p.get('research_scene','unknown')}、设施 {p.get('experimental_facility','unknown')}；观察时间：{c.get('observation_time') or '未知'}。",
                  f"处理：{c['intervention']}（{group(c['treatment_details'])}）；对照：{c['comparator']}（{group(c['comparator_details'])}）。",
                  f"原始指标：{c['outcome_family']}（{c['outcome']}），证据值类型 {c.get('evidence_value_kind','unknown')}；{'人工读图候选值' if c['assessment']['program_parse']=='manual_figure_estimate' else '原始报告值'}：{show(v['intervention'])} ± {show(v['intervention_error'])} vs {show(v['comparator'])} ± {show(v['comparator_error'])} {v['unit']}；误差类型 {v['intervention_error_type']}/{v['comparator_error_type']}；显著性：{c['significance']}。",
                  f"主题纳入：{'符合主题的候选，待来源核查' if c['eligibility']=='primary_candidate_requires_source_review' else c['eligibility']}；实验单位：{unit}，每处理 {show(rep.get('value'))}；设计有效性：{design_status}。{rep.get('original') or ''}",
                  f"标准化值：{standard}。换算依据：{n.get('basis','未知')}；研究内变化不代表统计显著性或因果效应。",
                  f"未解决事项：{'；'.join(c['conflicts']) if c['conflicts'] else '无已记录事项'}。",'']
        if c.get('gwp_conversion'):
            lines.append(f"GWP 换算系数来源：PDF 第 {c['gwp_conversion']['source_page']} 页，{c['gwp_conversion']['source_quote']}；质量排放为程序推算，非原表直接报告。")
        if c.get('reported_percent'):
            lines.append(f"作者表格报告百分比 {c['reported_percent']['value']}%；程序以对照为分母计算 {change}%，两者口径待核。")
        if c.get('author_reported_range'):
            v_range=c['author_reported_range'];lines.append(f"作者正文报告的两季相对变化范围：{v_range['low']}%–{v_range['high']}%（PDF 第 {v_range['page']} 页）；这是作者报告范围，不反推精确组均值。")
        if c['assessment']['program_parse']=='manual_figure_estimate':
            fig=(hint.get('manual_figure_estimate') or {});lines.append(f"数值来源为人工目视读图，非原文精确数值；估计精度：{fig.get('uncertainty','未知')}；{fig.get('method','')}。")
    status='证据不足，不能形成跨研究共同结论' if not summary['findings'] else '存在有限条件下的候选共同观察，仍需人工核查'
    lines+=['## 跨研究判断',f"科研结论：{status}；共同观察数：{len(summary['findings'])}（状态 `{summary['primary_conclusion']}`）。"]
    for p in summary['pairwise_comparability']:
        quantitative='可考虑，仍待来源和独立性核查' if p['B_conditional_quantitative']=='possible_pending_source_and_independence_review' else '尚不能建立'
        lines.append(f"- {p['study_ids']}，比较 {p['comparison_ids']}：可附条件描述性并列；条件性定量比较{quantitative}；统计推断条件未建立，未实现统计合并。原因：{'；'.join(p['reasons'])}。")
        if p.get('unit_normalization')=='same_n2o_mass_basis': lines.append('  已按各研究列明的依据换到 N₂O 质量口径；推算值与直接报告值仍须区分。量纲统一不解决处理、设计或统计可比性。')
    lines += ['','设计有效性、来源完整性和主题相关性分别判断；伪重复或重复未知不推断有效因果效应。未报告误差不推断显著性，不进行 Meta 分析。']
    for x in log:
        if x['status']=='failed_not_confirmed': lines.append(f"- 解析失败 {x['doi']}：{x['reason']}。")
    for x in excluded: lines.append(f"- 新研究未纳入 [{x['doi']}](https://doi.org/{x['doi']})：{x['reason']}。")
    return '\n'.join(lines)+'\n'

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path);ap.add_argument('--confirmation',type=Path);ap.add_argument('--sources',type=Path);ap.add_argument('--corrections',type=Path);ap.add_argument('--discover',type=Path,nargs='+');ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    if a.discover: discover(a.discover,a.out);return
    if bool(a.manifest)==bool(a.confirmation) or not a.sources: ap.error('综合需要 --manifest 或 --confirmation 二选一及 --sources；候选提取使用 --discover')
    import pdfplumber
    changes=[]
    if a.confirmation:
        answer=json.loads(a.confirmation.read_text());config={'topic':answer['topic'],'papers':[]}
        for entry in answer['papers']:
            if not entry.get('include'):continue
            paper=dict(entry['candidate'])
            expected=entry.get('candidate_digest')
            actual=hashlib.sha256(json.dumps(paper,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
            if not expected or expected!=actual:raise ValueError('候选基础数据被直接修改；请在 edits 中记录修改及原因')
            for edit in entry.get('edits',[]):
                if not edit.get('reason') or not edit.get('actor') or 'field' not in edit:raise ValueError('人工确认必须包含字段、值、原因与操作者')
                key=edit['field'];changes.append({'doi':paper.get('doi'),'field':key,'before':paper.get(key),'after':edit.get('value'),'reason':edit['reason'],'actor':edit['actor'],'status':'manual_edit_not_gold'});paper[key]=edit.get('value')
            if not paper.get('doi'):raise ValueError('纳入论文必须确认 DOI')
            config['papers'].append(paper)
        if not config['papers']:raise ValueError('未确认任何论文；请将待分析条目的 include 设置为 true')
    else:config=json.loads(a.manifest.read_text())
    out=a.out;rows={k:[] for k in ('papers','fields','results','issues','historic_claims','supplement_files')};checks=[];log=[];gwp_info={};reported_percent={};author_ranges={}
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
                if m.groupdict().get('reported_percent'): reported_percent[doi.lower()]={'value':float(m.group('reported_percent')),'unit':'%', 'source_page':p['pdf_page'],'description':'作者表格所列百分比，分母未由程序确定'}
                significance=(f'原表标记：处理 {sig_a or "未标"}；对照 {sig_b or "未标"}；含义须按表注核查' if sig_a or sig_b else 'unknown: separate cumulative comparison significance not present in selected passage')
                ev={'page':p['pdf_page'],'section':p.get('table_label','N2O emissions'),'quote':page[max(m.start(),m.start(1)-100):min(len(page),m.end(2)+100)],'context_assertions':assertions,'note':'Group errors not extracted; significance only when table markers captured.'}
            elif p['source_type']=='manual_figure':
                fig=p['figure_estimate']
                if not fig.get('method') or not fig.get('uncertainty') or not fig.get('actor') or not re.search(fig['figure_locator'],page,re.I):
                    raise ValueError('人工图值缺少图位置、估计方法、精度或操作者')
                treatment=p['a_label'];comparator=p['a_label']+' vs '+p['b_label']
                value={p['a_label']:float(fig['a']),p['b_label']:float(fig['b'])}
                significance=fig.get('reported_significance','unknown')
                ev={'page':p['pdf_page'],'section':fig['figure_locator'],'manual_figure_estimate':fig,
                    'note':'人工视觉近似，非原文数字；误差条未读出，非 Gold'}
                changes.append({'doi':doi,'field':'figure_estimate','before':None,'after':fig,
                                'reason':'原文图像未提供表格数值，人工近似读图','actor':fig['actor'],'status':'manual_estimate_not_gold'})
            else:raise ValueError('未知来源格式')
            rows['papers'].append({'doi':doi,'title':p['title'],'raw_sha256':ph,'raw_file':str(pdf)})
            for field,val in [('study_design',p['design']),('research_object',p['object']),('independent_replicates',p['replicates'])]:
                rows['fields'].append({'doi':doi,'field':field,'value':val,'status':'candidate_manual_source_review','source_sha256':ph,'evidence':[{'page':2,'section':'methods'}]})
            indicator=p.get('indicator') or ({'kg N2O/ha':'seasonal_n2o_mass','kg N-N2O/ha':'seasonal_n2o_n_mass'}.get(p['unit'],'unknown_indicator') if re.search(r'season|growing|生育季',p.get('period',''),re.I) else 'unknown_indicator')
            if p.get('reported_range_source'):
                spec=p['reported_range_source']
                with pdfplumber.open(pdf) as doc: range_text=doc.pages[spec['page']-1].extract_text() or ''
                found=list(re.finditer(spec['pattern'],range_text,re.I|re.S))
                if len(found)==1:
                    author_ranges[doi.lower()]={'low':float(found[0][1]),'high':float(found[0][2]),'page':spec['page'],'pdf_sha256':ph,'quote':found[0][0], 'status':'author_reported_program_located_not_gold'}
            if indicator=='seasonal_n2o_gwp' and p.get('gwp_factor_source'):
                spec=p['gwp_factor_source']
                with pdfplumber.open(pdf) as doc: factor_text=doc.pages[spec['page']-1].extract_text() or ''
                found=list(re.finditer(spec['pattern'],factor_text,re.I))
                if len(found)==1 and float(found[0][1])>0:
                    gwp_info[doi.lower()]={'factor':float(found[0][1]),'source_page':spec['page'],'pdf_sha256':ph,'source_quote':found[0][0],'source_verified_by_program':True,'review_status':'program_parsed_not_gold'}
            rows['results'].append({'doi':doi,'index':1,'indicator':indicator,'treatment':treatment,'comparator':comparator,'time':p['period'],'value':value,'unit':p['unit'],'significance':significance,'experiment_id':eid,'source_sha256':sh,'reporting_origin':'manual_figure_estimate' if p['source_type']=='manual_figure' else 'program_source_table_extraction','record_class':'candidate_not_human_gold','evidence':[ev],
              'treatment_levels':[{'code':'AWD','description':p['treatment_description']}], 'comparator_levels':[{'code':'CF','description':p['control_description']}],
              'limitations':p.get('source_conflict') or 'Source selection and design manually checked; independent treatment replication and cross-study context must be verified.'})
            log.append({'doi':doi,'source_sha256':sh,'pdf_sha256':ph,'source_type':p['source_type'],'selection':p['scope'],'status':'manual_estimate' if p['source_type']=='manual_figure' else 'parsed'})
        except (ValueError,KeyError,IndexError,OSError) as exc:
            log.append({'doi':p.get('doi'),'status':'failed_not_confirmed','reason':str(exc)})
    d=out/'candidates';d.mkdir(parents=True,exist_ok=True)
    for name,data in rows.items():(d/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data))
    converted=convert.run(d,None,{p['doi'].lower() for p in config['papers']}); normalized=synth.adapt_converted(converted,config['topic']);synth.apply_source_checks(normalized,{'checks':checks})
    for c in normalized['comparisons']:
        if c['study_id'] in gwp_info:c['gwp_conversion']=gwp_info[c['study_id']]
        if c['study_id'] in author_ranges:c['author_reported_range']=author_ranges[c['study_id']]
        c['standardized_n2o']=standardize(c)
        if c['raw_evidence'][0].get('manual_reported') is not None:
            c['standardized_n2o']['derived_status']='manual_graph_approximation_not_reported_numeric_value'
            c['standardized_n2o']['relative_change_percent']=None
            c['standardized_n2o']['relative_change_origin']='not_calculated_due_to_graph_reading_uncertainty'
        c['evidence_value_kind']=('manual_figure_approximation' if c['raw_evidence'][0].get('manual_reported') is not None else
                                   'inferred_from_n2o_specific_gwp' if c['outcome_family']=='n2o_gwp' and c['standardized_n2o']['status']=='standardized' else
                                   'reported_n2o_mass' if c['outcome_family'] in ('n2o_mass','n2o_n_mass') else 'other_or_unknown')
        c['comparison_family']='n2o_mass' if c['standardized_n2o']['status']=='standardized' else c['outcome_family']
        if c['study_id'] in reported_percent:
            c['reported_percent']=reported_percent[c['study_id']]
            calculated=c['standardized_n2o'].get('relative_change_percent')
            if calculated is not None and abs(calculated-c['reported_percent']['value'])>0.5:
                c['conflicts'].append('作者表格百分比与程序按对照为分母计算值不同；可能比较口径不同，未裁定。')
        rep=c.get('independent_replicates') or {}
        manual=(c['raw_evidence'][0].get('manual_reported') is not None)
        c['assessment']={'topic_eligibility':c['eligibility'],
                         'program_parse':'manual_figure_estimate' if manual else 'success', 'source_location':'manual_figure_location_not_independently_confirmed' if manual else 'program_reported_not_independently_confirmed',
                         'manual_check':'assistant_visual_estimate_not_independent' if manual else 'not_recorded_in_pipeline', 'gold':'not_gold',
                         'source_verification':'assistant_checked_candidate_not_human_gold' if any(e.get('pdf_verified') for e in c['raw_evidence']) else 'not_checked',
                         'design_validity':'insufficient_replicated_treatment' if re.search(r'pseudo.replicat|伪重复',str(rep.get('original') or ''),re.I) or rep.get('value')==1 else 'replication_reported_requires_review' if (rep.get('value') or 0)>1 else 'unknown'}
    summary=synth.synthesize_converted(normalized)
    by_id={c['comparison_id']:c for c in normalized['comparisons']}
    for pair in summary['pairwise_comparability']:
        members=[by_id[x] for group in pair['comparison_ids'] for x in group]
        if members and all(x['standardized_n2o'].get('status')=='standardized' and x['standardized_n2o'].get('unit')=='kg N2O/ha' for x in members):
            pair['unit_normalization']='same_n2o_mass_basis'
            pair['reasons']=[('原始单位不同，已统一为 N₂O 质量；其他可比性条件仍需核查' if x=='结果数值或报告效应的单位和口径不同或未知' else x) for x in pair['reasons']]
    summary['acceptance']={'engineering':'partial' if any(x['status']=='failed_not_confirmed' for x in log) else 'completed',
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
