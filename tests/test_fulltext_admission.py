import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from scripts.fulltext.admission import inspect_fulltext, validate_record_evidence
from scripts.fulltext.evidence import empty_fulltext, prepare_sections
from scripts.fulltext.tei_parser import parse_tei
from scripts.fulltext.pdf_metadata import parse_pdf
from scripts.pipeline_utils import write_json
from tests.test_tei_identity import tei


class FulltextAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.record={'uid':'U1','doi':'10.1234/example','title':'Study'}
        raw=tei(['10.1234/example']); (self.root/'paper.xml').write_bytes(raw)
        self.sha=hashlib.sha256(raw).hexdigest(); parsed=parse_tei(raw)
        write_json(self.root/'parsed.json',parsed)
        self.manifest={'uid':'U1','doi':self.record['doi'],'status':'available','format':'tei_xml',
                       'sha256':self.sha,'raw_file':'paper.xml','parsed_file':'parsed.json'}
        self.evidence=empty_fulltext()
        self.evidence['methods']=[{'text':'Rice','evidence_source':'fulltext','anchor':{
            'source_scope':'fulltext','document_sha256':self.sha,'section_id':parsed['sections'][0]['section_id'],
            'paragraph_id':parsed['sections'][0]['paragraphs'][0]['paragraph_id'],'char_start':0,'char_end':4}}]

    def check(self,manifest=None,evidence=None):
        return validate_record_evidence(self.record,{'fulltext_manifest':self.manifest if manifest is None else manifest,
            'fulltext_evidence':self.evidence if evidence is None else evidence},self.root)

    def test_verified_source_and_anchor_pass(self):
        self.assertEqual(self.check()['admission'],'technical_pass')

    def test_missing_manifest_fields_or_inconsistent_identifiers_fail(self):
        for changes in ({'parsed_file':None},{'parsed_file':'absent.json'},{'sha256':None},
                        {'sha256':'0'*64},{'doi':'10.1234/other'},{'uid':'U2'}, {'raw_file':None}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): self.check({**self.manifest,**changes})
        with self.assertRaises(ValueError): self.check({})

    def test_parsed_json_tampering_and_wrong_raw_are_rejected(self):
        parsed=json.loads((self.root/'parsed.json').read_text());parsed['sections'][0]['paragraphs'][0]['text']='Rice bogus'
        (self.root/'parsed.json').write_text(json.dumps(parsed))
        with self.assertRaisesRegex(ValueError,'parsed_content_not_reproduced'): self.check()

    def test_recorded_parsed_hash_and_parser_are_enforced(self):
        for changes in ({'parsed_sha256':'0'*64},{'parser':'different-parser'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError): self.check({**self.manifest,**changes})

    def test_request_preparation_cannot_use_unchecked_parsed_snapshot(self):
        parsed, result=inspect_fulltext(self.record,self.manifest,self.root)
        parsed['sections'][0]['paragraphs'][0]['text']='Modified after validation'
        with self.assertRaisesRegex(ValueError,'fulltext_snapshot_changed'):
            prepare_sections(self.record,parsed,{**self.manifest,'parsed_sha256':result['parsed_sha256']},
                             self.root/'requests',root=self.root)

    def test_anchor_paragraph_offsets_and_quote_are_checked(self):
        for change in ({'paragraph_id':'missing'},{'char_end':999},{'char_start':2,'char_end':4}):
            e=copy.deepcopy(self.evidence);e['methods'][0]['anchor'].update(change)
            with self.subTest(change=change):
                with self.assertRaises(ValueError): self.check(evidence=e)

    def test_missing_evidence_and_empty_evidence_do_not_block_abstract(self):
        self.assertIsNone(validate_record_evidence(self.record,{},self.root))
        self.assertIsNone(validate_record_evidence(self.record,{'fulltext_evidence':empty_fulltext()},self.root))

    def test_absolute_traversal_and_outside_symlink_fail(self):
        with tempfile.TemporaryDirectory() as outside:
            external=Path(outside)/'paper.xml';external.write_bytes((self.root/'paper.xml').read_bytes())
            (self.root/'link.xml').symlink_to(external)
            for name in (str(external),'../'+Path(outside).name+'/paper.xml','link.xml'):
                with self.subTest(name=name):
                    with self.assertRaises(ValueError): self.check({**self.manifest,'raw_file':name})

    def test_no_doi_in_header_or_no_body_never_admitted(self):
        for raw in (tei(reference_doi=self.record['doi']),tei([self.record['doi']],body=False)):
            (self.root/'paper.xml').write_bytes(raw);sha=hashlib.sha256(raw).hexdigest()
            write=(self.root/'parsed.json');write.write_text(json.dumps(parse_tei(raw)))
            with self.assertRaises(ValueError): inspect_fulltext(self.record,{**self.manifest,'sha256':sha},self.root)

    def test_real_pdf_is_reparsed_and_identity_checked(self):
        writer=PdfWriter(); page=writer.add_blank_page(width=400,height=300)
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({
            NameObject('/F1'):DictionaryObject({NameObject('/Type'):NameObject('/Font'),
                NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})})})
        content=DecodedStreamObject(); content.set_data(b'BT /F1 12 Tf 30 200 Td (DOI 10.1234/example) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(content)
        path=self.root/'paper.pdf'
        with path.open('wb') as stream: writer.write(stream)
        raw=path.read_bytes(); sha=hashlib.sha256(raw).hexdigest(); parsed=parse_pdf(raw)
        (self.root/'parsed.json').write_text(json.dumps(parsed))
        manifest={**self.manifest,'format':'pdf','raw_file':'paper.pdf','sha256':sha}
        _,result=inspect_fulltext(self.record,manifest,self.root)
        self.assertEqual(result['identity']['status'],'doi_match')
        with self.assertRaisesRegex(ValueError,'pdf_identity_requires_review'):
            inspect_fulltext({**self.record,'doi':'10.1234/example-extra'},
                             {**manifest,'doi':'10.1234/example-extra'},self.root)


if __name__ == '__main__': unittest.main()
