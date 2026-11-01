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
        path.parent.mkdir(parents=True,exist_ok=True)
        with urlopen(Request(url,headers={'User-Agent':'curl/8.7.1'}),timeout=45) as response: data=response.read()
        if hashlib.sha256(data).hexdigest()!=expected: raise ValueError('下载文件哈希与核查版本不一致')
        path.write_bytes(data)
    if sha(path)!=expected: raise ValueError('本地来源哈希与核查版本不一致')
def cells(path,sheet):
    ns={'x':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with ZipFile(path) as z:
        strings=[''.join(x.itertext()) for x in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('x:si',ns)] if 'xl/sharedStrings.xml' in z.namelist() else []
        result={}
        for c in ET.fromstring(z.read(sheet)).findall('.//x:c',ns):
            v=c.find('x:v',ns)
            if v is not None: result[c.get('r')]=strings[int(v.text)] if c.get('t')=='s' else v.text
        return result

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest',type=Path,required=True);ap.add_argument('--sources',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args()
    config=json.loads(a.manifest.read_text());out=a.out;rows={k:[] for k in ('papers','fields','results','issues','historic_claims','supplement_files')};checks=[];log=[]
    for p in config['papers']:
        source=a.sources/p['source'];pdf=a.sources/p['pdf']
        obtain(source,p['source_url'],p['source_sha256']);obtain(pdf,p['pdf_url'],p['pdf_sha256'])
        sh=sha(source);ph=sha(pdf);doi=p['doi'];eid=p['experiment_id']
        with pdfplumber.open(pdf) as document:
            page=document.pages[p['pdf_page']-1].extract_text(layout=True)
        if p['source_type']=='xlsx':
            values=cells(source,p['sheet']);aa,bb=values[p['a_cell']],values[p['b_cell']]
            if any(values.get(cell)!=expected for cell,expected in p.get('cell_checks',{}).items()):
                raise ValueError('表格处理标签与人工指定位置不一致')
            patt=r'\s*([\d.]+)\s*±\s*([\d.]+)\s*([a-z]+)\s*'
            ma,mb=re.fullmatch(patt,aa),re.fullmatch(patt,bb)
            if not ma or not mb:raise ValueError('无法读取处理均值、误差与字母')
            value=f'{ma[1]} ± {ma[2]} versus {mb[1]} ± {mb[2]}'
            significance=f'Table 1 letters {ma[3]} vs {mb[3]}; Duncan p<0.05, original table'
            ev={'page':p['pdf_page'],'table_figure':'Table 1','cells':[p['a_cell'],p['b_cell']],'quote':f"{p['a_label']} {aa}; {p['b_label']} {bb}",'footnote':'standard error'}
            treatment=p['a_label']; comparator=p['b_label']
        elif p['source_type']=='pdf_regex':
            matches=list(re.finditer(p['pattern'],page)); m=matches[p['match_index']]
            treatment=p['a_label'];comparator=p['a_label']+' vs '+p['b_label'];value={p['a_label']:float(m[1]),p['b_label']:float(m[2])}
            significance='unknown: separate cumulative comparison significance not present in selected passage'
            ev={'page':p['pdf_page'],'section':'N2O emissions','quote':m.group(0),'note':'error and within-season significance not reported in selected passage'}
        else:raise ValueError('未知来源格式')
        rows['papers'].append({'doi':doi,'title':p['title'],'raw_sha256':ph,'raw_file':str(pdf)})
        for field,val in [('study_design',p['design']),('research_object',p['object']),('independent_replicates',p['replicates'])]:
            rows['fields'].append({'doi':doi,'field':field,'value':val,'status':'candidate_manual_source_review','source_sha256':ph,'evidence':[{'page':2,'section':'methods'}]})
        rows['results'].append({'doi':doi,'index':1,'indicator':'seasonal_n2o','treatment':treatment,'comparator':comparator,'time':p['period'],'value':value,'unit':p['unit'],'significance':significance,'experiment_id':eid,'source_sha256':sh,'reporting_origin':'program_source_table_extraction','record_class':'candidate_not_human_gold','evidence':[ev],
          'treatment_levels':[{'code':'AWD','description':p['treatment_description']}], 'comparator_levels':[{'code':'CF','description':p['control_description']}],
          'limitations':'Source selection and design manually checked; independent treatment replication and cross-study context must be verified.'})
        checks.append({'doi':doi,'comparison_id':doi+'#result1:0','pdf_sha256':ph,'status':'assistant_source_checked_not_human_gold','pages':[2,3,p['pdf_page']],'scope':p['scope']})
        log.append({'doi':doi,'source_sha256':sh,'pdf_sha256':ph,'source_type':p['source_type'],'selection':p['scope'],'status':'parsed'})
    d=out/'candidates';d.mkdir(parents=True,exist_ok=True)
    for name,data in rows.items():(d/(name+'.jsonl')).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in data))
    converted=convert.run(d,None,{p['doi'].lower() for p in config['papers']}); normalized=synth.adapt_converted(converted,config['topic']);synth.apply_source_checks(normalized,{'checks':checks});summary=synth.synthesize_converted(normalized)
    for name,data in [('conversion',converted),('normalized_evidence',normalized),('synthesis',summary),('run_log',log)]:
        (out/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'papers':len(normalized['papers']),'comparisons':len(normalized['comparisons']),'findings':len(summary['findings']),'pairs':len(summary['pairwise_comparability'])}))
if __name__=='__main__':main()
