"""Deterministic TEI anchors; captions and references never become body facts."""
import gzip
import io
import re
from pathlib import Path
import xml.etree.ElementTree as ET

from .provenance import FulltextError

NS = {"t": "http://www.tei-c.org/ns/1.0"}
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def text(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "section"


def section_scope(heading):
    h = heading.casefold()
    if re.search(r"\b(method|methods|materials|methodology|experimental|experiment|statistical|statistic)\b",h): return "methods"
    if "result" in h: return "results"
    if "discussion" in h: return "discussion"
    if "conclusion" in h or "concluding" in h: return "conclusion"
    if "limitation" in h: return "discussion"
    return "other"


MAX_XML_BYTES = 30_000_000


def load_tei(raw):
    if len(raw) > MAX_XML_BYTES:
        raise FulltextError("unsafe_xml")
    if raw.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                raw = stream.read(MAX_XML_BYTES + 1)
        except (OSError, EOFError):
            raise FulltextError("invalid_tei_gzip") from None
    if len(raw) > MAX_XML_BYTES or b'\x00' in raw or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise FulltextError("unsafe_xml")
    try: root = ET.fromstring(raw)
    except ET.ParseError: raise FulltextError("invalid_tei_xml") from None
    if root.tag != "{"+NS['t']+"}TEI": raise FulltextError("not_tei_xml")
    return root


def tei_identity(raw, expected_doi):
    """Inspect article identifiers in the header, never body or references."""
    from ..providers.base_client import canonical_doi
    root = load_tei(raw)
    header = root.find('t:teiHeader', NS)
    if header is None:
        return {'status': 'header_missing', 'article_dois': []}
    paths = ('t:fileDesc/t:sourceDesc/t:biblStruct/t:idno',
             't:fileDesc/t:sourceDesc/t:biblStruct/t:analytic/t:idno',
             't:fileDesc/t:publicationStmt/t:idno')
    values = []
    for path in paths:
        for node in header.findall(path, NS):
            if (node.get('type') or '').casefold() != 'doi':
                continue
            try: value = canonical_doi(text(node))
            except ValueError: return {'status': 'invalid_header_doi', 'article_dois': values}
            if value not in values: values.append(value)
    if len(values) > 1: status = 'conflicting_header_dois'
    elif not values: status = 'header_doi_missing'
    else:
        try: expected = canonical_doi(expected_doi)
        except ValueError: return {'status': 'expected_doi_missing', 'article_dois': values}
        status = 'doi_match' if values[0] == expected else 'doi_mismatch'
    return {'status': status, 'article_dois': values}


def parse_tei(raw):
    root = load_tei(raw)
    title = text(root.find(".//t:teiHeader/t:fileDesc/t:titleStmt/t:title",NS)) or text(root.find(".//t:sourceDesc//t:analytic/t:title",NS))
    abstract = text(root.find(".//t:profileDesc/t:abstract",NS))
    sections, used, count = [], set(), 0
    body = root.find(".//t:text/t:body",NS)
    def walk(node, inherited_scope="other"):
        nonlocal count
        heading = text(node.find("t:head",NS))
        scope = section_scope(heading)
        if scope == "other": scope = inherited_scope
        sid = node.get(XML_ID) or slug(heading) if heading else node.get(XML_ID) or "body"
        base, n = sid, 2
        while sid in used: sid, n = base+"-"+str(n), n+1
        used.add(sid)
        paragraphs = []
        for p in node.findall("t:p",NS):
            value = text(p)
            if not value: continue
            count += 1
            paragraphs.append({"paragraph_id":"p"+str(count),"text":value,"xml_id":p.get(XML_ID)})
        if heading or paragraphs: sections.append({"section_id":sid,"heading":heading or None,"scope":scope,"paragraphs":paragraphs})
        active_scope=scope
        for child in node.findall("t:div",NS):
            child_scope=section_scope(text(child.find('t:head',NS)))
            # Grobid often flattens the numbered outline to sibling divs.
            # Carry the explicit Methods/Results/Discussion section forward.
            heading=text(child.find('t:head',NS)).casefold()
            if child_scope!='other': active_scope=child_scope
            elif heading.startswith(('introduction','references','acknowledg','funding','appendix')): active_scope='other'
            walk(child,active_scope)
    if body is not None: walk(body)
    captions=[]
    for index,figure in enumerate(root.findall(".//t:body//t:figure",NS),1):
        captions.append({"caption_id":figure.get(XML_ID) or "caption"+str(index),"kind":"table" if figure.get('type')=='table' else 'figure',"heading":text(figure.find('t:head',NS)),"text":text(figure.find('t:figDesc',NS))})
    references=[{"reference_id":r.get(XML_ID) or 'ref'+str(i),"text":text(r)} for i,r in enumerate(root.findall('.//t:back//t:listBibl/*',NS),1)]
    return {"parser":"tei-v2","title":title or None,"abstract":abstract or None,"sections":sections,"captions":captions,"references":references,"body_paragraph_count":count}


def section_chunks(parsed, max_chars=12000, scopes=None):
    if max_chars<=0: raise ValueError('Positive chunk limit required')
    scopes=scopes or {'methods','results','discussion','conclusion'}
    chunks=[]
    for section in parsed['sections']:
        if section['scope'] not in scopes: continue
        # Split long paragraphs into exact character spans rather than truncating.
        batch, size=[],0
        for p in section['paragraphs']:
            for start in range(0,len(p['text']),max_chars):
                segment={'paragraph_id':p['paragraph_id'],'char_start':start,'char_end':min(start+max_chars,len(p['text'])),'text':p['text'][start:start+max_chars]}
                if batch and size+len(segment['text'])>max_chars:
                    chunks.append({'section_id':section['section_id'],'heading':section['heading'],'scope':section['scope'],'paragraphs':batch});batch=[];size=0
                batch.append(segment);size+=len(segment['text'])
        if batch: chunks.append({'section_id':section['section_id'],'heading':section['heading'],'scope':section['scope'],'paragraphs':batch})
    return chunks
