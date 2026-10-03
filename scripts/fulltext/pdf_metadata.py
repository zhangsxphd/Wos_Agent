"""PDF metadata and page-anchored text; no OCR or invented section boundaries."""
import io
import re
from .provenance import FulltextError
from .tei_parser import section_scope


def parse_pdf(raw):
    if not raw.lstrip().startswith(b'%PDF-'): raise FulltextError('not_pdf')
    from pypdf import PdfReader
    try:
        reader=PdfReader(io.BytesIO(raw))
        if reader.is_encrypted: raise FulltextError('encrypted_pdf')
        sections=[]
        scope='other'
        for number,page in enumerate(reader.pages,1):
            value=(page.extract_text() or '').strip()
            # Heading recognition is structural routing only. Every text block
            # remains exact extracted text, with page number and stable IDs.
            blocks=re.split(r'\n\s*\n',value)
            paragraphs=[]
            for index,block in enumerate(blocks,1):
                if block: paragraphs.append({'paragraph_id':f'page{number}-p{index}','text':block,'page':number})
            sections.append({'section_id':f'page-{number}','heading':f'PDF page {number}','scope':'other','paragraphs':paragraphs})
        return {'parser':'pypdf-page-v1','title':(reader.metadata or {}).get('/Title'),'abstract':None,'sections':sections,'captions':[], 'references':[], 'pages':len(reader.pages),'body_paragraph_count':sum(len(s['paragraphs']) for s in sections),'section_routing_requires_review':True}
    except FulltextError: raise
    except Exception: raise FulltextError('pdf_parse_error') from None

