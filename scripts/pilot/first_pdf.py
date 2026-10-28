"""Paper-bound experimental PDF extraction. Not a general admission parser."""
import ctypes, hashlib, importlib.metadata, io, json, platform, re
from pathlib import Path
from pypdf import PdfReader
from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.fulltext.resolver import verify_pdf_identity

DOI='10.3390/w16182598'
UID='WOS:001323597100001'
RAW_SHA='da4b840de18c6966df14b7fc301203cc7e471eff93ee7c8e86677f4f4f917470'
# In this exact PDF these Form XObjects contain figure graphics AND clipped,
# otherwise invisible full-page draft text. Their actual graphics remain in raw PDF.
FORMS={3:('/Im5',),5:('/Im6',),6:('/Im7','/Im8')}
VERSION='pilot-pypdf-filter-figure-forms-v1'

def build(raw):
 if hashlib.sha256(raw).hexdigest()!=RAW_SHA:raise ValueError('paper_raw_hash_mismatch')
 reader=PdfReader(io.BytesIO(raw),strict=True)
 if reader.is_encrypted or len(reader.pages)!=11:raise ValueError('unexpected_pdf_layout')
 verify_pdf_identity(parse_pdf(raw),{'uid':UID,'doi':DOI})
 sections=[];excluded=[]
 for number,page in enumerate(reader.pages,1):
  names=set(FORMS.get(number,()));xobjects=page['/Resources'].get('/XObject',{})
  for name in names:
   obj=xobjects.get(name)
   if not obj or obj.get_object().get('/Subtype')!='/Form' or not obj.get_object().get('/BBox'):raise ValueError('expected_form_missing:'+name)
  state={'form':None};seen=[];out=[]
  def before(op,args,cm,tm):
   if state['form']=='pending':state['form']='inside'
   if op==b'Do' and args and str(args[0]) in names and state['form'] is None:
    state['form']='pending';seen.append(str(args[0]))
  def after(op,args,cm,tm):
   if op==b'Do' and args and str(args[0]) in names and state['form'] in ('pending','inside'):state['form']=None
  def visitor(text,cm,tm,font,size):
   if state['form']!='inside':out.append(text)
  page.extract_text(visitor_operand_before=before,visitor_operand_after=after,visitor_text=visitor)
  if set(seen)!=names or len(seen)!=len(names) or state['form'] is not None:raise ValueError('unexpected_form_invocations')
  text=''.join(out).strip();blocks=[x for x in re.split(r'\n\s*\n',text) if x]
  sections.append({'section_id':f'page-{number}','heading':f'PDF page {number}','scope':'other',
                   'paragraphs':[{'paragraph_id':f'page{number}-p{i}','text':b,'page':number} for i,b in enumerate(blocks,1)]})
  for name in seen:excluded.append({'page':number,'form':name,'bbox':list(xobjects[name].get_object()['/BBox'])})
 return {'parser':VERSION,'uid':UID,'doi':DOI,'raw_sha256':RAW_SHA,'python':platform.python_version(),
         'pypdf_version':importlib.metadata.version('pypdf'),'fonttools_version':importlib.metadata.version('fonttools'),'sections':sections,'pages':len(sections),
         'body_paragraph_count':sum(len(x['paragraphs']) for x in sections),'excluded_figure_forms':excluded,
         'limitations':['Text from excluded figures is omitted, not evidence of absent text.',
                        'No scientific interpretation or formal fulltext admission is granted.',
                        'Page boundaries, symbols, table alignment and other invisible text remain reviewable.'],
         'human_review_status':'not_assessed','formal_admission':'not_granted'}

# Boundaries are calibrated for the specific, visually inspected version of this
# document, in PDF points from the top-left. A mismatch fails instead of guessing.
T1_COLS=[('item',35,76),('total_nitrogen',76,120),('total_phosphorus',120,165),('suspended_matter',165,208),('Zn',208,235),('Ni',235,261),('Se',261,289),('As',289,317),('Hg',317,345),('Cd',345,373),('Pb',373,400),('Cr',400,435),('BOD',435,474),('COD',474,520),('anion_surface_active_dose',520,560)]
T3_COLS=[('item',35,105),('As',105,175),('Hg',175,255),('Cd',255,325),('Pb',325,400),('Cr',400,475),('yield',475,560)]

def table_candidates(raw):
 if hashlib.sha256(raw).hexdigest()!=RAW_SHA:raise ValueError('paper_raw_hash_mismatch')
 import pypdfium2 as pdfium
 document=pdfium.PdfDocument(raw);parsed=build(raw);tables=[]
 for n,number,columns,rows in [(5,1,T1_COLS,[('ditch_water',520,534),('threshold',535,551)]),(8,3,T3_COLS,[('T1',141,152),('T2',152,163),('T3',163,174),('CK',174,185),('standard',185,198)])]:
  page=document[n-1];tp=page.get_textpage();ids={ctypes.cast(o.raw,ctypes.c_void_p).value for o in page.get_objects(max_depth=1) if o.type==pdfium.raw.FPDF_PAGEOBJ_TEXT};chars=[]
  for i in range(tp.count_chars()):
   obj=tp.get_textobj(i)
   if not obj or ctypes.cast(obj.raw,ctypes.c_void_p).value not in ids:continue
   try:left,bottom,right,top=tp.get_charbox(i);code=pdfium.raw.FPDFText_GetUnicode(tp,i);char=chr(code);y=page.get_height()-top
   except (ValueError,OverflowError):continue
   chars.append({'index':i,'x':left,'top':y,'char':char})
  cells=[]
  for row,lo,hi in rows:
   for column,xlo,xhi in columns:
    chosen=[c for c in chars if xlo<=c['x']<xhi and lo<=c['top']<hi]
    # Table 1 has a two-line row label and threshold class labels below values.
    split=(528 if number==1 and row=='ditch_water' and column=='item' else
           543 if number==1 and row=='threshold' and column in ('total_nitrogen','total_phosphorus','item') else None)
    if split:
     chosen=sorted(chosen,key=lambda c:(c['top']>=split,c['x']))
    else:chosen=sorted(chosen,key=lambda c:c['x'])
    value=''.join(c['char'] for c in chosen).replace(' ','').strip()
    cells.append({'row':row,'column':column,'text':value,'bbox_top_left':[xlo,lo,xhi,hi],
                  'pdfium_char_indices':[c['index'] for c in chosen]})
  if number==1:
   values={(x['row'],x['column']):x['text'] for x in cells}
   for key,expected in [(('ditch_water','total_nitrogen'),'1.8'),(('ditch_water','total_phosphorus'),'0.042'),(('ditch_water','suspended_matter'),'2.5')]:
    if values.get(key)!=expected:raise ValueError('table1_layout_changed:'+str(key))
  else:
   values={(x['row'],x['column']):x['text'] for x in cells}
   for key,expected in [(('T1','yield'),'8318.4±134.7c'),(('T2','yield'),'9182.1±128.9b'),(('T3','yield'),'9016.2±157.3b'),(('CK','yield'),'10,437.5±187.5a'),(('T1','As'),'0.23±0.09a'),(('CK','As'),'0.08±0.02b')]:
    if values.get(key)!=expected:raise ValueError('table3_layout_changed:'+str(key))
  section=parsed['sections'][n-1];note_start='Note: Except for the threshold values' if number==1 else 'Note: Different letters indicate'
  caption=('Table 1. Water quality physical and chemical properties of ditch water mg/L.' if number==1 else 'Table 3. Heavy metal contents and yields of paddies.')
  caption_p=next(p for p in section['paragraphs'] if caption in p['text']);caption_start=caption_p['text'].index(caption)
  paragraph=next(p for p in section['paragraphs'] if note_start in p['text']);start=paragraph['text'].index(note_start)
  # Keep the table footnote as an exact source span; stop before next section.
  marker='3.2.' if number==1 else '4. Discussion'
  end=paragraph['text'].find(marker,start);end=len(paragraph['text']) if end<0 else end
  note=paragraph['text'][start:end].strip();end=start+len(note)
  tables.append({'table':number,'page':n,'raw_sha256':RAW_SHA,'pdfium_version':importlib.metadata.version('pypdfium2'),
                 'method':'top-level PDF text objects, fixed visual-verified cell bounding boxes',
                 'columns':[x[0] for x in columns],'cells':cells,
                 'caption':caption,'caption_anchor':{'section_id':section['section_id'],'paragraph_id':caption_p['paragraph_id'],'char_start':caption_start,'char_end':caption_start+len(caption)},
                 'units':({x[0]:'mg/L' for x in columns if x[0]!='item'} if number==1 else {**{x[0]:'mg/kg' for x in columns if x[0] not in ('item','yield')},'yield':'kg/hm²'}),
                 'unit_notes':'mg/L' if number==1 else 'heavy metals mg/kg; yield kg/hm²',
                 'table_note':note,'table_note_anchor':{'section_id':section['section_id'],'paragraph_id':paragraph['paragraph_id'],'char_start':start,'char_end':end},
                 'status':'structured_candidate_visual_reviewed_not_general_table_parser'})
 return tables
