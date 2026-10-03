"""Resolve TEI, OpenAlex cached PDF, best OA PDF, other OA PDF, then unavailable."""
import hashlib
import json
from pathlib import Path

from .openalex_content import OpenAlexContentClient, location_metadata
from .provenance import FulltextError, safe_url, save_raw, manifest
from .tei_parser import parse_tei
from .pdf_metadata import parse_pdf
from .oa_landing import pdf_links
from ..pipeline_utils import ROOT, assert_safe, write_json


def candidates(work, secrets=()):
    content=work.get('content_urls') or {}
    has=work.get('has_content') or {}
    choices=[]
    for key,fmt in [('grobid_xml','tei_xml'),('pdf','pdf')]:
        url=content.get(key) or (content.get('tei_xml') if key=='grobid_xml' else None)
        if url and has.get(key) is not False:
            try: safe=safe_url(url,secrets)
            except (FulltextError,ValueError): continue
            if not safe.startswith('https://content.openalex.org/works/'): continue
            # Cached open text may exist even when current OA status is closed.
            metadata=location_metadata(work)
            # Best OA location is not proof of the archive file's origin.
            metadata.update(version=None,license=None)
            choices.append(dict(source='openalex_content',format=fmt,url=safe,**metadata))
    locations=[work.get('best_oa_location'),*(work.get('locations') or [])]
    seen={c['url'] for c in choices}
    for location in locations:
        if not isinstance(location,dict) or location.get('is_oa') is not True or not location.get('pdf_url'): continue
        try: url=safe_url(location['pdf_url'],secrets)
        except (FulltextError,ValueError): continue
        if url in seen: continue
        seen.add(url)
        choices.append(dict(source='oa_location',format='pdf',url=url,**location_metadata(work,location)))
    # No guessed publisher URL: only an explicitly marked OA location and an
    # authoritative PDF link advertised by that one landing page.
    landings=0
    for location in locations:
        if not isinstance(location,dict) or location.get('is_oa') is not True or not location.get('landing_page_url'): continue
        try: url=safe_url(location['landing_page_url'],secrets)
        except (ValueError,FulltextError): continue
        if url in seen: continue
        seen.add(url)
        choices.append(dict(source='oa_landing',format='oa_landing',url=url,**location_metadata(work,location)))
        landings+=1
        if landings>=2: break
    return choices


class FulltextResolver:
    def __init__(self, root=ROOT, client=None):
        self.root=Path(root)
        self.client=client or OpenAlexContentClient(root=root)
        self.secrets=self.client.secrets
        self.cache_hits=0

    def resolve(self, record, work=None, refresh=False):
        identity=record.get('doi') or record.get('openalex_id')
        if not identity: return manifest(record,'unavailable',reason='missing_identifier')
        key=hashlib.sha256(str(identity).lower().encode()).hexdigest()
        directory=self.root/'data/fulltext'
        if not refresh:
            prior=sorted((directory/'manifests'/key).glob('*.json'))
            for path in reversed(prior):
                entry=json.loads(path.read_text())
                assert_safe(entry,self.secrets)
                if entry.get('status')=='available':
                    raw_path=self.root/entry['raw_file']
                    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest()==entry['sha256']:
                        self.cache_hits+=1
                        result=dict(entry,cache_hit=True,uid=record.get('uid'),doi=record.get('doi'))
                        if entry.get('format')=='tei_xml':
                            current=parse_tei(raw_path.read_bytes())
                            parsed_path=directory/'parsed'/(entry['sha256']+'.tei-v2.json')
                            if not parsed_path.exists(): write_json(parsed_path,current,self.secrets)
                            result.update(parsed_file=str(parsed_path.relative_to(self.root)),parse_status='parsed' if current['body_paragraph_count'] else 'no_body_text')
                        return result
        work=work or self.client.lookup(identity)
        if not work: return self._save(manifest(record,'unavailable',reason='work_not_found'),key)
        choices=candidates(work,self.secrets)
        base={'openalex_id':work['id'],'work_metadata':{k:work.get(k) for k in ('open_access','best_oa_location','locations','has_content','content_urls')},'availability':location_metadata(work),'attempts':[],'cache_hit':False}
        for choice in choices:
            try:
                raw,final_url=self.client.get(choice['url'],content=True)
                if choice['format']=='oa_landing':
                    links=pdf_links(raw,final_url,self.secrets)
                    if not links: raise FulltextError('no_advertised_pdf_link')
                    # Follow at most two advertised alternatives, never scrape
                    # login pages, execute JS, or defeat access restrictions.
                    for pdf_url in links:
                        try:
                            candidate,redirected=self.client.get(pdf_url,content=True)
                            parse_pdf(candidate)
                            raw,final_url=candidate,redirected
                            choice=dict(choice,format='pdf',url=pdf_url)
                            break
                        except FulltextError:
                            continue
                    else: raise FulltextError('advertised_pdf_unavailable')
                parser=parse_tei if choice['format']=='tei_xml' else parse_pdf
                # Validate format before storing a challenge/login page as content.
                parsed=parser(raw)
                raw_path,digest=save_raw(raw,directory/'raw',choice['format'],self.secrets)
                parsed_path=directory/'parsed'/(digest+'.'+parsed['parser']+'.json')
                if not parsed_path.exists(): write_json(parsed_path,parsed,self.secrets)
                base['attempts'].append({'source':choice['source'],'format':choice['format'],'source_url':choice['url'],'status':'available'})
                result=manifest(record,'available',**base,source=choice['source'],format=choice['format'],version=choice.get('version'),license=choice.get('license'),source_url=choice['url'],final_url=final_url,sha256=digest,raw_file=str(raw_path.relative_to(self.root)),parsed_file=str(parsed_path.relative_to(self.root)),parse_status='parsed' if parsed['body_paragraph_count'] else 'no_body_text')
                return self._save(result,key)
            except (FulltextError,ValueError,OSError) as exc:
                base['attempts'].append({'source':choice['source'],'format':choice['format'],'source_url':choice['url'],'status':'error','error_code':exc.code if isinstance(exc,FulltextError) else 'invalid_or_unsafe_content','http_status':getattr(exc,'http_status',None)})
        status='error' if choices else 'unavailable'
        return self._save(manifest(record,status,**base,reason='all_candidates_failed' if choices else 'no_public_content_location'),key)

    def _save(self,result,key):
        from ..pipeline_utils import new_run_id
        target=self.root/'data/fulltext/manifests'/key/(new_run_id('fulltext')+'.json')
        result['manifest_file']=str(target.relative_to(self.root))
        write_json(target,result,self.secrets)
        return result
