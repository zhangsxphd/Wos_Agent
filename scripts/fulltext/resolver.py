"""Resolve TEI, OpenAlex cached PDF, best OA PDF, other OA PDF, then unavailable."""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .openalex_content import OpenAlexContentClient, location_metadata
from .provenance import FulltextError, safe_url, save_raw, save_parsed, manifest
from .tei_parser import parse_tei, tei_identity
from .pdf_metadata import parse_pdf
from .oa_landing import pdf_links
from ..pipeline_utils import ROOT, assert_safe, write_json
from ..normalize_record import normalize_doi


def verify_pdf_identity(parsed, record):
    """Require the expected DOI as a complete identifier on PDF page one."""
    expected = normalize_doi(record.get('doi'))
    try:
        first = ' '.join(p['text'] for p in parsed['sections'][0]['paragraphs'])
    except (KeyError, TypeError, IndexError):
        raise FulltextError('pdf_identity_requires_review') from None
    first = unicodedata.normalize('NFKC', first).casefold()
    # Common PDF font encodings render numerals as glyph names and replace
    # ASCII DOI hyphens with typographic dashes. Normalize only these observed
    # representations; compare the complete DOI, never a substring.
    digits = dict(zip(('zero','one','two','three','four','five','six','seven','eight','nine'), '0123456789'))
    first = re.sub(r'/(zero|one|two|three|four|five|six|seven|eight|nine)\.tnum',
                   lambda match: digits[match.group(1)], first)
    first = first.translate(str.maketrans({c:'-' for c in '‐‑‒–−'}))
    found = {normalize_doi(match.rstrip('.,;:)]}')) for match in
             re.findall(r'\b10\.\d{4,9}/[^\s<>"\'\[\]{}]+', first)}
    if not expected or expected not in found:
        raise FulltextError('pdf_identity_requires_review')


def candidates(work, secrets=(), include_archive=True):
    content=work.get('content_urls') or {}
    has=work.get('has_content') or {}
    choices=[]
    for key,fmt in [('grobid_xml','tei_xml'),('pdf','pdf')]:
        if not include_archive: continue
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
    def __init__(self, root=ROOT, client=None, include_archive=True, max_candidates=None):
        self.root=Path(root)
        self.client=client or OpenAlexContentClient(root=root)
        self.secrets=self.client.secrets
        self.cache_hits=0
        self.include_archive=include_archive
        if max_candidates is not None and max_candidates < 1: raise ValueError('Positive candidate budget required')
        self.max_candidates=max_candidates

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
                    raw_path=(self.root/entry['raw_file']).resolve()
                    try: raw_path.relative_to(self.root.resolve())
                    except ValueError: continue
                    if raw_path.exists() and hashlib.sha256(raw_path.read_bytes()).hexdigest()==entry['sha256']:
                        if entry.get('format') == 'pdf':
                            try: verify_pdf_identity(parse_pdf(raw_path.read_bytes()), record)
                            except FulltextError: continue
                        self.cache_hits+=1
                        result=dict(entry,cache_hit=True,uid=record.get('uid'),doi=record.get('doi'))
                        result['identity_check'] = ('doi_on_first_page' if entry.get('format')=='pdf'
                                                    else 'tei_identity_not_independently_verified')
                        if entry.get('format')=='tei_xml':
                            current=parse_tei(raw_path.read_bytes())
                            identity_status=tei_identity(raw_path.read_bytes(),record.get('doi'))['status']
                            result['identity_check']='tei_header_'+identity_status
                            result['identity_status']=identity_status
                            if identity_status != 'doi_match': result['status']='needs_review'
                            parsed_path,parsed_sha=save_parsed(current,directory/'parsed',entry['sha256'],self.secrets)
                            result.update(parsed_file=str(parsed_path.relative_to(self.root)),parsed_sha256=parsed_sha,
                                          parser=current['parser'],parse_status='parsed' if current['body_paragraph_count'] else 'no_body_text')
                        return result
        work=work or self.client.lookup(identity)
        if not work: return self._save(manifest(record,'unavailable',reason='work_not_found'),key)
        if record.get('doi') and work.get('doi') and normalize_doi(record['doi']) != normalize_doi(work['doi']):
            return self._save(manifest(record,'error',reason='work_doi_mismatch'),key)
        choices=candidates(work,self.secrets,include_archive=self.include_archive)
        total_choices=len(choices)
        if self.max_candidates is not None: choices=choices[:self.max_candidates]
        base={'openalex_id':work['id'],'work_metadata':{k:work.get(k) for k in ('open_access','best_oa_location','locations','has_content','content_urls')},'availability':location_metadata(work),'attempts':[],'cache_hit':False,'archive_enabled':self.include_archive,'candidate_count':total_choices,'candidate_budget':self.max_candidates}
        review_candidate=None
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
                if choice['format']=='pdf':
                    try: verify_pdf_identity(parsed,record); identity_status='doi_match'
                    except FulltextError: identity_status='needs_review'
                else: identity_status=tei_identity(raw,record.get('doi'))['status']
                raw_path,digest=save_raw(raw,directory/'raw',choice['format'],self.secrets)
                parsed_path,parsed_sha=save_parsed(parsed,directory/'parsed',digest,self.secrets)
                accepted=identity_status=='doi_match' and bool(parsed['body_paragraph_count'])
                base['attempts'].append({'source':choice['source'],'format':choice['format'],'source_url':choice['url'],'status':'available' if accepted else 'needs_review','identity_status':identity_status})
                result=manifest(record,'available' if accepted else 'needs_review',**base,source=choice['source'],format=choice['format'],version=choice.get('version'),license=choice.get('license'),source_url=choice['url'],final_url=final_url,sha256=digest,raw_file=str(raw_path.relative_to(self.root)),parsed_file=str(parsed_path.relative_to(self.root)),parsed_sha256=parsed_sha,parser=parsed['parser'],parse_status='parsed' if parsed['body_paragraph_count'] else 'no_body_text',identity_status=identity_status,identity_check='doi_on_first_page' if choice['format']=='pdf' else 'tei_header_'+identity_status)
                if not accepted:
                    if review_candidate is None: review_candidate=result
                    continue
                return self._save(result,key)
            except (FulltextError,ValueError,OSError) as exc:
                base['attempts'].append({'source':choice['source'],'format':choice['format'],'source_url':choice['url'],'status':'error','error_code':exc.code if isinstance(exc,FulltextError) else 'invalid_or_unsafe_content','http_status':getattr(exc,'http_status',None)})
        if review_candidate:
            review_candidate['attempts']=base['attempts']
            review_candidate['reason']='identity_or_parse_requires_review'
            return self._save(review_candidate,key)
        status='error' if choices else 'unavailable'
        reason='candidate_budget_reached' if len(choices)<total_choices else 'all_candidates_failed' if choices else 'no_public_content_location'
        return self._save(manifest(record,status,**base,reason=reason),key)

    def _save(self,result,key):
        from ..pipeline_utils import new_run_id
        target=self.root/'data/fulltext/manifests'/key/(new_run_id('fulltext')+'.json')
        result['manifest_file']=str(target.relative_to(self.root))
        write_json(target,result,self.secrets)
        return result
