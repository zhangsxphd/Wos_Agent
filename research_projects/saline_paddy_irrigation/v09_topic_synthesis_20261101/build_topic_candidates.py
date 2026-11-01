"""Build targeted per-paper candidates from existing records and specific PDF pages.

No model, reference synthesis, prefilled comparison values, or API calls.
PDF layout and treatment labels are checked explicitly; unexpected format stops.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path
import pdfplumber


def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def line_json(path, rows):
    Path(path).write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf8')


def main():
    p=argparse.ArgumentParser();p.add_argument('--history',type=Path,required=True);p.add_argument('--tang',type=Path,required=True);p.add_argument('--pot',type=Path,required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    doi18='10.3390/su10020475'; doi26='10.3390/su18052185'
    rows={}
    for name in ('papers','fields','results','issues','historic_claims','supplement_files'):
        rows[name]=[r for r in (json.loads(x) for x in (a.history/(name+'.jsonl')).read_text().splitlines()) if r.get('doi','').lower()==doi26]
    if len(rows['papers'])!=1 or rows['papers'][0]['raw_sha256']!=sha(a.pot):raise ValueError('盆栽全文与历史哈希不一致')
    with pdfplumber.open(a.tang) as pdf:
        page4=pdf.pages[3].extract_text(layout=True)
        page6=pdf.pages[5].extract_text(layout=True)
    if not all(x in re.sub(r'\s+','',page4).lower() for x in ('experimentiwasoperatedin2012','continuous','intermittent')):
        raise ValueError('水制度与实验年份原文未匹配')
    table=page6[page6.find('Table 2.'):]
    pattern=r'Total\s+([\d.]+)±([\d.]+)\s+([\d.]+)±([\d.]+)\s+([\d.]+)±([\d.]+)\s+([\d.]+)±([\d.]+)\s+(\*+|NS)\s+(\*+|NS)\s+(\*+|NS)'
    totals=list(re.finditer(pattern,table,re.I))
    if len(totals)!=3 or 'N2O' not in table or 'CH4' not in table or 'CO2' not in table:raise ValueError('气体表的Total行或顺序不完整')
    source_hash=sha(a.tang)
    rows['papers'].append({'doi':doi18,'title':'Effects of Irrigation Regime and Nitrogen Fertilizer Management on CH4, N2O and CO2 Emissions from Saline–Alkaline Paddy Fields in Northeast China',
        'raw_sha256':source_hash,'raw_file':str(a.tang),'source_file':'targeted_pdf_page_extraction','extraction_status':'candidate_targeted_pdf_not_gold'})
    def f(name,value,page):
        return {'doi':doi18,'field':name,'value':value,'status':'candidate_targeted_pdf_not_gold',
                'record_class':'candidate_pending_review','source_sha256':source_hash,
                'evidence':[{'page':page,'source':'targeted_pdf_visual_and_text','scientific_adjudication':'pending'}]}
    rows['fields'] += [f('study_design','2012 field experiment in saline-alkaline rice paddies; 1-year and 57-year reclamation; two irrigation treatments per field.',4),
        f('research_object','Saline-alkaline paddy fields in northeastern China.',3),
        f('treatments_levels','IF=intermittent irrigation, drainage and rewetting; CF=continuous flooding irrigation',4),
        f('independent_replicates','Each paddy divided into two 20 m × 20 m irrigation areas. Table reports n=3; independent irrigation plot replication not established.',4),
        f('sampling_replicates','Chamber measurements; treatment allocation and chamber replication hierarchy unresolved.',4)]
    for gas,m in zip(('CH4','N2O','CO2'),totals):
        vals=m.groups(); pairs=[(vals[i],vals[i+1]) for i in range(0,8,2)]
        for j,year in enumerate(('1-year','57-year')):
            cf,ir=pairs[2*j],pairs[2*j+1]
            unit='unresolved: table g/m²; results text mg/m²' if gas=='N2O' else 'g/m²'
            rows['results'].append({'doi':doi18,'index':100+len(rows['results']), 'indicator':'seasonal_'+gas.lower(),
                'treatment':'IF','comparator':'CF', 'time':'2012 rice-growing season; '+year+' reclaimed field',
                'value':f'{ir[0]} ± {ir[1]} versus {cf[0]} ± {cf[1]}','unit':unit,
                'significance':f'Table 2 ANOVA irrigation main effect {vals[9]}; interaction {vals[10]}; not a separate within-field pairwise test',
                'limitations':'Irrigation applied to two areas per field; independent water-management replication unknown. '+('N2O text mg/m² versus Table 2 g/m² remains unresolved.' if gas=='N2O' else 'Mean flux is distinct from seasonal cumulative emission.'),
                'reporting_origin':'program_pdf_table_extraction',
                'experiment_id':'2012_experiment_I', 'evidence':[{'page':6,'table_figure':'Table 2','row':gas+' Total','column':year+' CF/IF',
                'quote':m.group(0),'footnote':'Mean Value ± Standard Error'}],
                'record_class':'candidate_pending_review','source_sha256':source_hash})
    with pdfplumber.open(a.pot) as pdf: text=pdf.pages[13].extract_text(layout=True)
    if 'Table2.' not in text or '10cm' not in text or '20cm' not in text:raise ValueError('未定位盆栽土壤表')
    section=text[text.index('10cm'):text.index('20cm')];lines=section.splitlines();groups={}
    for i,line in enumerate(lines[:-1]):
        vals=re.findall(r'([\d.]+)±([\d.]+)([a-z]+)',line,re.I)
        if len(vals)!=5:continue
        sub=re.sub(r'\s+','',lines[i+1])
        if sub in ('FW','SW'):groups[sub]=(vals,line+' '+lines[i+1])
    if set(groups)!={'FW','SW'}:raise ValueError('不能可靠定位淹水/浅湿且加藻的表格行')
    for indicator,column,unit in [('soil_total_nitrogen',2,'mg/kg'),('soil_organic_carbon',4,'g/kg')]:
        if indicator=='soil_total_nitrogen' and 'TN(mgkg−1)' not in text:raise ValueError('TN单位不匹配')
        if indicator=='soil_organic_carbon' and 'SOC(gkg−1)' not in text:raise ValueError('SOC单位不匹配')
        v1,v0=groups['SW'][0][column],groups['FW'][0][column]
        rows['results'].append({'doi':doi26,'index':1000+column,'indicator':indicator,'treatment':'ISCW','comparator':'IFCW',
           'time':'10 cm layer, end of experiment','value':f'{v1[0]} ± {v1[1]} versus {v0[0]} ± {v0[1]}','unit':unit,
           'significance':f'Table 2 letters {v1[2]} vs {v0[2]}; interpretation pending independent review',
           'limitations':'Concentration at one depth, not a carbon or nitrogen transformation rate.',
           'reporting_origin':'program_pdf_table_extraction',
           'evidence':[{'page':14,'table_figure':'Table 2','row':'ISCW and IFCW, 10 cm','column':indicator,
                        'quote':groups['SW'][1]+' / '+groups['FW'][1], 'footnote':'standard deviation'}],
           'record_class':'candidate_pending_review','source_sha256':sha(a.pot)})
    for name,data in rows.items():line_json(a.out/(name+'.jsonl'),data)
    print(json.dumps({'tang_pdf_sha256':source_hash,'gas_total_rows':len(totals),'pot_soil_pairs':2},ensure_ascii=False))

if __name__=='__main__':main()
