"""Retrieve explicitly linked supplemental files from the official PMC Cloud.

Uses the free PMC ID Converter and documented public PMC Cloud metadata/JATS.
Never treats a filename guess as a supplement. Does not extract archives, call
models, modify existing sources, or use any API key.
"""
import argparse,concurrent.futures,datetime,hashlib,json,re,time,urllib.parse,urllib.request,xml.etree.ElementTree as ET
from pathlib import Path
from scripts.pipeline_utils import ROOT

BASE=ROOT/'data/pilot/v08_pmc_supplement_batch_20261030_v1'
QUEUE=ROOT/'data/pilot/v08_codex_fulltext_20261030_v2/queue.json'
OLD=ROOT/'data/pilot/v08_supplements_20261030_v1/manifest_20261030.jsonl'
BUCKET='https://pmc-oa-opendata.s3.amazonaws.com'

def read_url(url,limit=100_000_000):
 err=None
 for i in range(3):
  try:
   request=urllib.request.Request(url,headers={'User-Agent':'WosAgent-research/1.0'})
   with urllib.request.urlopen(request,timeout=35) as r:
    if int(r.headers.get('Content-Length','0'))>limit:raise ValueError('declared_size_over_limit')
    b=r.read(limit+1)
   if len(b)>limit:raise ValueError('download_size_over_limit')
   return b
  except (OSError,ValueError) as e:
   err=e
   if i<2:time.sleep(1+i)
 raise err

def digest(data):return hashlib.sha256(data).hexdigest()
def write_immutable(p,b):
 if p.exists():
  if p.read_bytes()!=b:raise ValueError('immutable_path_conflict:'+str(p))
 else:
  p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp');tmp.write_bytes(b);tmp.replace(p)

def ids_from_dois(dois):
 out={}
 for i in range(0,len(dois),25):
  ids=dois[i:i+25];params=urllib.parse.urlencode({'ids':','.join(ids),'format':'json','tool':'WosAgent'})
  d=json.loads(read_url('https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/?'+params,limit=2_000_000))
  if d.get('status')!='ok':raise ValueError('id_converter_non_ok')
  for item in d.get('records',[]):out[item['requested-id'].lower()]=item
 return out

def process(row,converter,old):
 doi=row['doi'];ident=converter.get(doi.lower(),{});pmc=ident.get('pmcid');result={'doi':doi,'pmcid':pmc,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':'NIH NLM NCBI PubMed Central Article Datasets - PMC Cloud Service'}
 if not pmc:return {**result,'status':'pmc_id_unavailable','files':[]}
 if ident.get('doi','').lower()!=doi.lower():return {**result,'status':'id_converter_mismatch','files':[]}
 try:
  params=urllib.parse.urlencode({'list-type':'2','prefix':pmc+'.','delimiter':'/'})
  listing=ET.fromstring(read_url(BUCKET+'/?'+params,limit=2_000_000))
  prefixes=[el.text for el in listing.iter() if el.tag.endswith('Prefix') and el.text and re.fullmatch(re.escape(pmc)+r'\.\d+/',el.text)]
  choices=[]
  for prefix in sorted(set(prefixes)):
   key=prefix[:-1];b=read_url(BUCKET+'/metadata/'+urllib.parse.quote(key)+'.json',limit=2_000_000);meta=json.loads(b)
   if meta.get('doi','').lower()==doi.lower() and not meta.get('is_manuscript') and not meta.get('is_retracted') and meta.get('is_pmc_openaccess'):
    choices.append((key,meta,b))
  if len(choices)!=1:return {**result,'status':'version_unavailable_or_ambiguous','candidate_versions':len(choices),'files':[]}
  key,meta,mb=choices[0];xmlurl=meta.get('xml_url','');xp=urllib.parse.urlparse(xmlurl)
  if xp.scheme!='s3' or xp.netloc!='pmc-oa-opendata' or not xp.path.startswith('/'+key+'/'):raise ValueError('untrusted_xml_url')
  xb=read_url(BUCKET+xp.path,limit=20_000_000);md5=urllib.parse.parse_qs(xp.query).get('md5',[''])[0]
  if md5 and hashlib.md5(xb).hexdigest()!=md5:raise ValueError('xml_checksum_mismatch')
  xml=ET.fromstring(xb);hrefs=set()
  for x in xml.iter():
   if x.tag.split('}')[-1]=='supplementary-material':
    for sub in x.iter():
     for k,v in sub.attrib.items():
      if k.split('}')[-1]=='href':hrefs.add(urllib.parse.unquote(v).split('/')[-1])
  media={}
  for u in meta.get('media_urls',[]):
   name=urllib.parse.unquote(urllib.parse.urlparse(u).path.rsplit('/',1)[-1]);media.setdefault(name,[]).append(u)
  root=BASE/hashlib.sha256(doi.encode()).hexdigest()[:16];write_immutable(root/('metadata-'+digest(mb)[:12]+'.json'),mb);write_immutable(root/('source-'+digest(xb)[:12]+'.xml'),xb)
  files=[]
  for name in sorted(hrefs):
   if name not in media:files.append({'filename':name,'status':'not_in_cloud_media'});continue
   if Path(name).name!=name or name in ('.','..') or not re.search(r'\.(pdf|zip|xlsx|xls|docx|doc|csv|tsv|txt|png|jpg|jpeg)$',name,re.I):files.append({'filename':name,'status':'unsupported_or_unsafe_filename'});continue
   if len(media[name])!=1:files.append({'filename':name,'status':'ambiguous_cloud_filename'});continue
   u=media[name][0];pu=urllib.parse.urlparse(u)
   if pu.scheme!='s3' or pu.netloc!='pmc-oa-opendata' or not pu.path.startswith('/'+key+'/'):raise ValueError('untrusted_media_url')
   md5=urllib.parse.parse_qs(pu.query).get('md5',[''])[0]
   if not re.fullmatch(r'[a-f0-9]{32}',md5):files.append({'filename':name,'status':'missing_metadata_checksum'});continue
   try:
    existing=old.get((doi,name));source='downloaded'
    if existing and (ROOT/existing['local_file']).is_file() and hashlib.md5((ROOT/existing['local_file']).read_bytes()).hexdigest()==md5:
     target=(ROOT/existing['local_file']).resolve();target.relative_to(ROOT);b=target.read_bytes();source='reused_previously_downloaded'
    else:
     b=read_url(BUCKET+urllib.parse.quote(urllib.parse.unquote(pu.path),safe='/'),limit=100_000_000)
     if hashlib.md5(b).hexdigest()!=md5:raise ValueError('media_checksum_mismatch:'+name)
     if name.lower().endswith('.pdf') and not b.startswith(b'%PDF-'):raise ValueError('not_pdf:'+name)
     if re.search(r'\.(zip|xlsx|docx)$',name,re.I) and not b.startswith(b'PK'):raise ValueError('not_zip_container:'+name)
     target=root/(digest(b)[:12]+'__'+name);write_immutable(target,b)
    files.append({'filename':name,'status':source,'local_file':str(target.relative_to(ROOT)),'sha256':digest(b),'md5_matches_pmc':True,'bytes':len(b),'source_url':BUCKET+pu.path})
   except Exception as e:
    files.append({'filename':name,'status':'download_error','error':type(e).__name__+': '+str(e)[:200]})
  return {**result,'status':'checked','version':key,'license':meta.get('license_code'),'jats_supplement_refs':sorted(hrefs),'files':files,'metadata_sha256':digest(mb),'xml_sha256':digest(xb),'caveat':'A JATS file list may be incomplete; absence does not establish that no publisher supplements exist.'}
 except Exception as e:return {**result,'status':'retrieval_error','error':type(e).__name__+': '+str(e)[:200],'files':[]}

def run(dois=None):
 rows=json.loads(QUEUE.read_text());rows=[x for x in rows if not dois or x['doi'] in dois]
 if dois and {x['doi'] for x in rows}!=set(dois):raise ValueError('doi_not_in_existing_queue')
 converter=ids_from_dois([x['doi'] for x in rows]);old={}
 if OLD.exists():
  for line in OLD.read_text().splitlines():
   r=json.loads(line)
   if r.get('status')=='downloaded_unreviewed':old[(r['doi'],r['filename'])]=r
 for inventory in sorted(BASE.glob('inventory_*.jsonl')):
  for line in inventory.read_text().splitlines():
   rec=json.loads(line)
   for f in rec.get('files',[]):
    if f.get('status') in ('downloaded','reused_previously_downloaded'):old[(rec['doi'],f['filename'])]=f
 BASE.mkdir(exist_ok=True,parents=True)
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(lambda r:process(r,converter,old),rows))
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ');path=BASE/('inventory_'+stamp+'.jsonl');write_immutable(path,''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in records).encode())
 from collections import Counter
 print(json.dumps({'inventory':str(path),'papers':len(records),'statuses':dict(Counter(x['status'] for x in records)),'files_downloaded':sum(y['status']=='downloaded' for x in records for y in x['files']),'files_reused':sum(y['status']=='reused_previously_downloaded' for x in records for y in x['files'])}))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--doi',action='append');args=p.parse_args();run(set(args.doi or []))
