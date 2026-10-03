"""Offline OA resolution, screening, exact deduplication and fulltext provenance."""
import copy
import gzip
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock,patch

import requests
from scripts.pipeline_utils import ROOT,write_json,write_jsonl,read_jsonl
from scripts.extractors import empty_record
from scripts.extractors.schema_validator import EvidenceValidator,EvidenceValidationError
from scripts.fulltext.openalex_content import OpenAlexContentClient,work_identifier
from scripts.fulltext.provenance import FulltextError,safe_url,save_raw
from scripts.fulltext.tei_parser import parse_tei,section_chunks
from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.fulltext.oa_landing import pdf_links
from scripts.fulltext.resolver import FulltextResolver,candidates
from scripts.fulltext.evidence import empty_fulltext,validate_fulltext,combine,upgrade_abstract,anchor_text,prepare_sections,FACT_FIELDS
from scripts.screening_profile import load_profile,classify
from scripts.build_fulltext_evidence import validate_chunk,merge_chunks,chunk_key,ManualSectionExtractor,build_fulltext
from tests.test_v04 import source,valid

XML=(ROOT/'tests/fixtures/fulltext_tei.xml').read_bytes()
SHA=hashlib.sha256(XML).hexdigest()


def response(status=200,raw=None,content=None,headers=None):
    r=Mock(status_code=status,headers=headers or {})
    r.json.return_value=raw
    r.iter_content.return_value=[content or b'']
    return r


def work(**changes):
    return dict({'id':'https://openalex.org/W123','doi':'https://doi.org/10.1000/evidence','open_access':{'is_oa':True,'oa_status':'gold'},'has_content':{'grobid_xml':True,'pdf':True},'content_urls':{'grobid_xml':'https://content.openalex.org/works/W123.grobid-xml','pdf':'https://content.openalex.org/works/W123.pdf'},'best_oa_location':{'is_oa':True,'pdf_url':'https://publisher.example/paper.pdf','landing_page_url':'https://publisher.example/paper','license':'cc-by','version':'publishedVersion'},'locations':[]},**changes)


def fulltext_evidence():
    parsed=parse_tei(XML);r=empty_fulltext()
    def a(pid,fragment):
        section,p=next((s,p) for s in parsed['sections'] for p in s['paragraphs'] if p['paragraph_id']==pid)
        start=p['text'].index(fragment)
        return {'source_scope':'fulltext','document_sha256':SHA,'section_id':section['section_id'],'paragraph_id':pid,'char_start':start,'char_end':start+len(fragment)}
    for field,pid,text in [('methods','p1','pot experiment'),('treatments','p1','flooded irrigation'),('measurements','p2','Soil nitrogen'),('limitations_explicit','p4','A single season limits generalization.')]:
        r[field]=[{'text':text,'anchor':a(pid,text),'evidence_source':'fulltext'}]
    text='Grain yield increased by 12%.'
    r['findings']=[{'claim':text,'evidence_text':text,'source':'fulltext','anchor':a('p3',text),'evidence_source':'fulltext'}]
    text='Yield was associated with soil nitrogen.'
    r['author_interpretations']=[{'text':text,'claim_type':'association','source':'fulltext','anchor':a('p4',text)}]
    return r,parsed


class V04Compatibility(unittest.TestCase):
    def test_exact_findings_dedup_and_anchor_remap(self):
        row=valid();row['evidence']['findings'].append(copy.deepcopy(row['evidence']['findings'][0]))
        row['inference']['transferable_idea']=[{'kind':'inference','statement':'Interpretation','evidence_anchors':['/evidence/findings/1']}]
        normalized=EvidenceValidator().normalize(row,source())
        self.assertEqual(len(normalized['evidence']['findings']),1)
        self.assertEqual(normalized['inference']['transferable_idea'][0]['evidence_anchors'],['/evidence/findings/0'])
        self.assertEqual(len(row['evidence']['findings']),2)

    def test_findings_no_fuzzy_dedup(self):
        record=source(abstract='Yield increased. Yield increased slightly.')
        row=empty_record(record)
        for text in ['Yield increased.','Yield increased slightly.']:
            start=record['abstract'].index(text)
            row['evidence']['findings'].append({'source':'abstract','evidence_text':text,'claim':text,'certainty':'explicit','start':start,'end':start+len(text)})
        self.assertEqual(len(EvidenceValidator().normalize(row,record)['evidence']['findings']),2)

    def test_old_records_validate_without_new_taxonomy(self):
        row=valid();self.assertNotIn('author_interpretations',row['evidence'])
        EvidenceValidator().validate(row,source())

    def test_author_taxonomy_preserves_old_mechanism(self):
        row=valid();quote='Grain yield increased by 12%.';a=row['evidence']['findings'][0]
        row['evidence']['mechanisms_explicit']=[quote]
        row['evidence_support']['/evidence/mechanisms_explicit/0']={k:a[k] for k in ('source','evidence_text','start','end')}
        upgraded=upgrade_abstract(row,source())
        self.assertEqual(upgraded['evidence']['mechanisms_explicit'],[quote])
        self.assertEqual(upgraded['evidence']['author_interpretations'][0]['claim_type'],'other')

    def test_author_taxonomy_anchor_is_validated(self):
        row=valid();row['evidence']['author_interpretations']=[{'text':'Invented mechanism','claim_type':'causal','source':'abstract','anchor':{'source':'abstract','evidence_text':'rice','start':24,'end':28}}]
        with self.assertRaises(EvidenceValidationError): EvidenceValidator().validate(row,source())


class ParserAndEvidence(unittest.TestCase):
    def test_tei_title_abstract_sections_captions_references(self):
        p=parse_tei(XML)
        self.assertEqual(p['title'],'Rice irrigation fixture')
        self.assertEqual(p['body_paragraph_count'],5)
        self.assertEqual([c['kind'] for c in p['captions']],['figure','table'])
        self.assertEqual(len(p['references']),1)
        self.assertTrue(p['abstract'])

    def test_tei_stable_anchors_and_flat_outline_scopes(self):
        self.assertEqual(parse_tei(XML),parse_tei(XML))
        p=parse_tei(XML);mapped={s['heading']:s['scope'] for s in p['sections']}
        self.assertEqual(mapped['Design'],'methods');self.assertEqual(mapped['Yield'],'results')
        ids=[x['paragraph_id'] for s in p['sections'] for x in s['paragraphs']]
        self.assertEqual(len(ids),len(set(ids)))

    def test_gzip_tei(self): self.assertEqual(parse_tei(gzip.compress(XML)),parse_tei(XML))

    def test_xml_entities_and_html_rejected(self):
        for raw in [b'<!DOCTYPE TEI [<!ENTITY x SYSTEM "file:///etc/passwd">]><TEI/>',b'<html>Challenge</html>']:
            with self.assertRaises(FulltextError): parse_tei(raw)

    def test_section_chunks_bounded_and_offsets_preserved(self):
        p={'sections':[{'section_id':'results','heading':'Results','scope':'results','paragraphs':[{'paragraph_id':'p1','text':'x'*37}]}]}
        chunks=section_chunks(p,max_chars=10)
        self.assertEqual(len(chunks),4)
        self.assertEqual(''.join(c['paragraphs'][0]['text'] for c in chunks),'x'*37)
        self.assertEqual(chunks[-1]['paragraphs'][0]['char_start'],30)

    def test_fulltext_valid_evidence(self):
        row,parsed=fulltext_evidence();validate_fulltext(row,parsed,SHA)

    def test_hallucinated_fulltext_number_rejected(self):
        row,parsed=fulltext_evidence();row['findings'][0]['claim']='Yield increased by 99%.'
        with self.assertRaises(EvidenceValidationError): validate_fulltext(row,parsed,SHA)

    def test_fulltext_anchor_hash_and_paragraph_rejected(self):
        row,parsed=fulltext_evidence()
        for bad in [dict(row['findings'][0]['anchor'],paragraph_id='missing'),dict(row['findings'][0]['anchor'],document_sha256='0'*64)]:
            with self.assertRaises(EvidenceValidationError): anchor_text(bad,parsed,SHA)

    def test_association_not_promoted_to_causal(self):
        row,parsed=fulltext_evidence();row['author_interpretations'][0]['claim_type']='causal'
        with self.assertRaises(EvidenceValidationError): validate_fulltext(row,parsed,SHA)

    def test_source_layers_preserved_and_conflicts_anchored(self):
        row,parsed=fulltext_evidence();record=source();a=EvidenceValidator().normalize(valid(),record);before=copy.deepcopy(a)
        conflict={'description':'Demonstration of a reviewed conflict','abstract_anchor':'/evidence/findings/0','fulltext_anchor':row['findings'][0]['anchor']}
        combined=combine(record,a,row,parsed,{'sha256':SHA},conflicts=[conflict])
        self.assertTrue(combined['evidence_conflict'])
        self.assertEqual(combined['abstract_evidence']['evidence']['findings'],a['evidence']['findings'])
        self.assertEqual(a,before)

    def test_unanchored_conflict_rejected(self):
        row,parsed=fulltext_evidence();r=source();a=EvidenceValidator().normalize(valid(),r)
        with self.assertRaises(EvidenceValidationError): combine(r,a,row,parsed,{'sha256':SHA},conflicts=[{'description':'unanchored'}])

    def test_metadata_title_is_not_a_conflict_evidence_anchor(self):
        row,parsed=fulltext_evidence();r=source();a=EvidenceValidator().normalize(valid(),r)
        conflict={'description':'unsupported title inference','abstract_anchor':'/title','fulltext_anchor':row['findings'][0]['anchor']}
        with self.assertRaises(EvidenceValidationError): combine(r,a,row,parsed,{'sha256':SHA},conflicts=[conflict])

    def test_completeness_recovers_missing_methods_and_results(self):
        row,parsed=fulltext_evidence();r=source();a=EvidenceValidator().normalize(empty_record(r),r)
        combined=combine(r,a,row,parsed,{'sha256':SHA})
        self.assertTrue(combined['completeness_before']['needs_fulltext'])
        self.assertFalse(combined['completeness_after']['needs_fulltext'])
        self.assertTrue(combined['completeness_after']['has_limitations'])
        self.assertFalse(combined['completeness_after']['has_mechanism'])  # association alone

    def test_no_fulltext_does_not_infer_new_content(self):
        r=source(abstract=None);a=EvidenceValidator().normalize(empty_record(r),r)
        combined=combine(r,a)
        self.assertTrue(combined['completeness_after']['needs_fulltext'])
        self.assertFalse(combined['fulltext_evidence']['findings'])
        self.assertEqual(combined['conflict_check_status'],'not_assessed')

    def test_chunk_cannot_anchor_to_other_section(self):
        row,parsed=fulltext_evidence();payload={'section':{'section_id':'results','paragraphs':[{'paragraph_id':'p3','char_start':0,'char_end':100}]}}
        with self.assertRaises(EvidenceValidationError): validate_chunk(row,payload,parsed,SHA)

    def test_pdf_challenge_and_encryption_rejected(self):
        with self.assertRaises(FulltextError): parse_pdf(b'<html>login</html>')
        with patch('pypdf.PdfReader') as reader:
            reader.return_value.is_encrypted=True
            with self.assertRaises(FulltextError): parse_pdf(b'%PDF-1.4')

    def test_pdf_page_anchors_and_metadata(self):
        with patch('pypdf.PdfReader') as factory:
            reader=factory.return_value;reader.is_encrypted=False;reader.metadata={'/Title':'PDF fixture'}
            page=Mock();page.extract_text.return_value='First paragraph.\n\nSecond paragraph.';reader.pages=[page]
            parsed=parse_pdf(b'%PDF-1.4')
        self.assertEqual(parsed['title'],'PDF fixture');self.assertEqual(parsed['body_paragraph_count'],2)
        self.assertEqual(parsed['sections'][0]['paragraphs'][1]['paragraph_id'],'page1-p2')
        self.assertTrue(parsed['section_routing_requires_review'])


class TransportResolver(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'.env').write_text('OPENALEX_API_KEY=fixture-private-key')
        self.session=Mock();self.client=OpenAlexContentClient(root=self.root,session=self.session,retries=0,min_interval=0,sleep=lambda _:None)

    def test_doi_and_work_id_normalization(self):
        self.assertEqual(work_identifier('https://doi.org/10.1000/EVIDENCE'),'https://doi.org/10.1000/evidence')
        self.assertEqual(work_identifier('https://openalex.org/W123'),'W123')
        self.assertEqual(work_identifier('10.1000/W123'),'https://doi.org/10.1000/w123')

    def test_work_metadata_cache_and_no_canonical_change(self):
        self.session.get.return_value=response(raw=work())
        result=self.client.lookup('10.1000/evidence');cached=self.client.lookup('10.1000/evidence')
        self.assertEqual(result,cached);self.session.get.assert_called_once()
        self.assertEqual(self.client.stats['work_cache_hits'],1)
        for path in (self.root/'data').rglob('*.json'): self.assertNotIn('fixture-private-key',path.read_text())

    def test_key_env_only_and_not_forwarded_on_redirect(self):
        self.session.get.side_effect=[response(302,headers={'Location':'https://cdn.example/paper.xml'}),response(content=XML)]
        raw,_=self.client.get('https://content.openalex.org/works/W123.grobid-xml',content=True)
        self.assertEqual(raw,XML)
        calls=self.session.get.call_args_list
        self.assertEqual(calls[0].kwargs['headers']['Authorization'],'Bearer fixture-private-key')
        self.assertNotIn('Authorization',calls[1].kwargs['headers'])
        self.assertNotIn('fixture-private-key',calls[0].args[0])

    def test_transient_429_timeout_5xx_no_negative_cache(self):
        for value in [response(429),requests.Timeout(),response(503)]:
            self.session.get.side_effect=None;self.session.get.return_value=value
            if isinstance(value,Exception): self.session.get.side_effect=value
            with self.assertRaises(FulltextError): self.client.lookup('10.1000/evidence',refresh=True)
            self.assertFalse(list((self.root/'data/cache/openalex_work').glob('*.json')))

    def test_404_is_not_found_cache(self):
        self.session.get.return_value=response(404)
        self.assertIsNone(self.client.lookup('10.1000/evidence'))
        self.assertIsNone(self.client.lookup('10.1000/evidence'))
        self.session.get.assert_called_once()

    def test_reflected_key_response_not_cached(self):
        self.session.get.return_value=response(raw=work(title='fixture-private-key'))
        with self.assertRaises(FulltextError): self.client.lookup('10.1000/evidence')
        self.assertFalse(list((self.root/'data').rglob('*.json')))

    def test_no_network_in_cache_only_mode(self):
        self.client.cache_only=True
        with self.assertRaises(FulltextError): self.client.lookup('10.1000/evidence')
        self.session.get.assert_not_called()

    def test_public_https_only_and_no_shadow_library(self):
        for url in ['http://example.org/p.pdf','https://localhost/a','https://127.0.0.1/a','https://sci-hub.example/a','https://libgen.example/a','https://annas-archive.example/a']:
            with self.assertRaises(FulltextError): safe_url(url)

    def test_priority_and_non_oa_locations_skipped(self):
        w=work(locations=[{'is_oa':False,'pdf_url':'https://closed.example/paywall.pdf'}])
        c=candidates(w)
        self.assertEqual([x['format'] for x in c[:3]],['tei_xml','pdf','pdf'])
        self.assertFalse(any('closed.example' in x['url'] for x in c))
        self.assertIsNone(c[0]['license']);self.assertEqual(c[2]['license'],'cc-by')

    def test_tei_failure_falls_back_to_oa_pdf(self):
        c=Mock(secrets=());c.get.side_effect=[FulltextError('not_found',404),(b'%PDF-fixture','https://content.openalex.org/works/W123.pdf')]
        resolver=FulltextResolver(self.root,c)
        parsed={'parser':'pdf-fixture','sections':[],'body_paragraph_count':1}
        with patch('scripts.fulltext.resolver.parse_pdf',return_value=parsed): result=resolver.resolve(source(),work())
        self.assertEqual(result['format'],'pdf');self.assertEqual(len(result['attempts']),2)
        self.assertTrue((self.root/result['raw_file']).exists())

    def test_download_and_content_hash_cache_hit(self):
        c=Mock(secrets=());c.get.return_value=(XML,'https://content.openalex.org/works/W123.grobid-xml')
        resolver=FulltextResolver(self.root,c)
        first=resolver.resolve(source(),work());second=resolver.resolve(source(),work())
        self.assertEqual(first['sha256'],SHA);self.assertTrue(second['cache_hit']);c.get.assert_called_once()

    def test_unavailable_and_errors_retry_next_run(self):
        c=Mock(secrets=());c.get.side_effect=FulltextError('transient_http_error',429)
        resolver=FulltextResolver(self.root,c)
        first=resolver.resolve(source(),work());calls=c.get.call_count
        self.assertEqual(first['status'],'error');resolver.resolve(source(),work())
        self.assertGreater(c.get.call_count,calls)
        self.assertEqual(resolver.resolve(source(),work(has_content={},content_urls={},best_oa_location=None))['status'],'unavailable')

    def test_landing_only_advertised_links_no_url_guess(self):
        raw=b'<meta name="citation_pdf_url" content="/paper.pdf"><a href="/login">Login</a>'
        self.assertEqual(pdf_links(raw,'https://oa.example/article'),['https://oa.example/paper.pdf'])
        self.assertEqual(pdf_links(b'<html>No file</html>','https://oa.example/article'),[])

    def test_non_pdf_challenge_not_saved(self):
        c=Mock(secrets=());c.get.return_value=(b'<html>Captcha</html>','https://cdn.example/challenge')
        result=FulltextResolver(self.root,c).resolve(source(),work())
        self.assertEqual(result['status'],'error')
        self.assertFalse(list((self.root/'data/fulltext/raw').glob('*')))

    def test_stream_size_limit(self):
        self.client.max_bytes=3;self.session.get.return_value=response(content=b'too large')
        with self.assertRaises(FulltextError): self.client.get('https://oa.example/paper.pdf',content=True)


class Profiles(unittest.TestCase):
    def setUp(self): self.profile=load_profile(ROOT/'profiles/saline_paddy.yaml')
    def test_yaml_profile_and_roles(self): self.assertIn('regional_context',self.profile['evidence_roles'])
    def test_review_not_excluded(self):
        row=EvidenceValidator().normalize(valid(),source());r=source(document_types=['Review'])
        result=classify(r,row,self.profile)
        self.assertEqual(result['evidence_role'],'review_synthesis');self.assertEqual(result['eligibility_status'],'include')
    def test_regional_model_not_excluded(self):
        row=valid();row['evidence']['study_system']['experimental_scale']='model'
        result=classify(source(),row,self.profile)
        self.assertEqual(result['evidence_role'],'regional_context');self.assertNotEqual(result['eligibility_status'],'exclude')
    def test_no_abstract_unresolved(self):
        r=source(abstract=None);row=EvidenceValidator().normalize(empty_record(r),r)
        result=classify(r,row,self.profile)
        self.assertEqual(result['eligibility_status'],'needs_fulltext');self.assertEqual(result['evidence_role'],'unresolved')
    def test_core_requires_supported_context_not_title(self):
        row=valid();r=source(title='Rice in saline soil')
        result=classify(r,row,self.profile)
        self.assertNotEqual(result['evidence_role'],'core_experimental')
        row['evidence']['study_system']['soil_type']=['saline soil']
        self.assertEqual(classify(r,row,self.profile)['evidence_role'],'core_experimental')


class FulltextPipeline(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.record=source();self.original=EvidenceValidator().normalize(valid(),self.record)
        write_jsonl(self.root/'canonical.jsonl',[self.record]);write_jsonl(self.root/'abstract.jsonl',[self.original])
        raw,digest=save_raw(XML,self.root/'data/fulltext/raw','tei_xml')
        parsed=self.root/'data/fulltext/parsed/source.json';write_json(parsed,parse_tei(XML))
        self.manifest={'uid':self.record['uid'],'doi':self.record['doi'],'status':'available','format':'tei_xml','sha256':digest,'raw_file':str(raw.relative_to(self.root)),'parsed_file':str(parsed.relative_to(self.root))}
        write_jsonl(self.root/'fulltext.jsonl',[self.manifest])
        self.args=('canonical.jsonl','abstract.jsonl','fulltext.jsonl')

    def build(self,**kwargs):
        with redirect_stdout(io.StringIO()): return build_fulltext(*self.args,root=self.root,profile_file=ROOT/'profiles/saline_paddy.yaml',**kwargs)

    def prepare_manual(self,bad=False,secret=False):
        prepared=self.build(output='prepared.jsonl',prepare_only=True)
        item=read_jsonl(self.root/'prepared.jsonl')[0]
        reviewed,parsed=fulltext_evidence()
        for path in item['extraction_provenance']['requests']:
            payload=json.loads(Path(path).read_text());response=empty_fulltext()
            allowed={p['paragraph_id'] for p in payload['section']['paragraphs']}
            for field in (*FACT_FIELDS,'findings','author_interpretations'):
                response[field]=[copy.deepcopy(i) for i in reviewed[field] if i['anchor']['paragraph_id'] in allowed and i['anchor']['section_id']==payload['section']['section_id']]
            if bad and response['findings']: response['findings'][0]['claim']='unsupported 99%'
            if secret and response['findings']: response['findings'][0]['claim']='fixture-private-key'
            write_json(self.root/'manual'/SHA/(chunk_key(payload)+'.json'),response)

    def test_end_to_end_manual_chunks_preserve_all_inputs(self):
        self.prepare_manual()
        before={p:(self.root/p).read_bytes() for p in self.args}
        result=self.build(output='final.jsonl',manual_dir='manual')
        self.assertTrue(result['fulltext_methods_records']);self.assertEqual(result['fulltext_finding_records'],1)
        self.assertFalse(result['results'][0]['errors'])
        self.assertEqual(read_jsonl(self.root/'final.jsonl')[0]['conflict_check_status'],'not_assessed')
        self.assertTrue(all((self.root/p).read_bytes()==value for p,value in before.items()))

    def test_bad_chunk_isolated_safe_response_kept(self):
        self.prepare_manual(bad=True);result=self.build(output='bad.jsonl',manual_dir='manual')
        self.assertTrue(result['results'][0]['errors'])
        row=read_jsonl(self.root/'bad.jsonl')[0]
        self.assertEqual(len(row['abstract_evidence']['evidence']['findings']),1)
        self.assertEqual(len(row['fulltext_evidence']['findings']),0)
        self.assertTrue(row['fulltext_evidence']['methods'])

    def test_api_key_not_in_fulltext_evidence_raw_output_or_log(self):
        self.prepare_manual(secret=True)
        (self.root/'.env').write_text('OPENALEX_API_KEY=fixture-private-key')
        log=io.StringIO()
        with redirect_stdout(log): build_fulltext(*self.args,root=self.root,profile_file=ROOT/'profiles/saline_paddy.yaml',output='safe.jsonl',manual_dir='manual')
        self.assertNotIn('fixture-private-key',log.getvalue())
        for directory in ('data/raw','data/evidence'):
            for path in (self.root/directory).rglob('*.json'): self.assertNotIn('fixture-private-key',path.read_text())
        self.assertNotIn('fixture-private-key',(self.root/'safe.jsonl').read_text())

    def test_raw_sha_mismatch_and_existing_output_refused(self):
        before=(self.root/'canonical.jsonl').read_bytes()
        with self.assertRaises(FileExistsError): self.build(output='canonical.jsonl')
        (self.root/self.manifest['raw_file']).write_bytes(b'tampered')
        with self.assertRaises(ValueError): self.build(output='tampered.jsonl')
        self.assertEqual(before,(self.root/'canonical.jsonl').read_bytes())

    def test_merge_conflicting_fulltext_system_is_not_silent(self):
        a=empty_fulltext();b=empty_fulltext()
        a['study_system']['location']='Site A';b['study_system']['location']='Site B'
        a['system_support']['/study_system/location']={};b['system_support']['/study_system/location']={}
        with self.assertRaises(EvidenceValidationError): merge_chunks([a,b])
