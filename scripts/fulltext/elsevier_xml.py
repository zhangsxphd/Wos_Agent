"""Parse Elsevier FULL-view XML into the same anchored section shape used by v0.5."""
import re
import xml.etree.ElementTree as ET

from .provenance import FulltextError
from .tei_parser import section_scope, slug


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _text(element):
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _first(root, names):
    names = {n.lower() for n in names}
    for element in root.iter():
        if _local(element.tag) in names:
            value = _text(element)
            if value:
                return value
    return ""


def _direct_paragraphs(section):
    values = []
    for element in list(section):
        name = _local(element.tag)
        if name in {"para", "simple-para"}:
            value = _text(element)
            if value:
                values.append(value)
        elif name not in {"section", "sec"}:
            for child in element.iter():
                if child is element:
                    continue
                if _local(child.tag) in {"para", "simple-para"}:
                    value = _text(child)
                    if value:
                        values.append(value)
    return values


def parse_elsevier_xml(raw):
    if not isinstance(raw, (bytes, bytearray)):
        raise FulltextError("invalid_elsevier_xml")
    raw = bytes(raw)
    if len(raw) > 30_000_000 or b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
        raise FulltextError("unsafe_xml")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise FulltextError("invalid_elsevier_xml") from None

    title = _first(root, {"title", "article-title", "dc:title"})
    abstract = _first(root, {"abstract"})
    sections = []
    used = set()
    paragraph_count = 0

    section_nodes = [e for e in root.iter() if _local(e.tag) in {"section", "sec"}]
    for index, section in enumerate(section_nodes, 1):
        heading = ""
        for child in list(section):
            if _local(child.tag) in {"section-title", "title", "label"}:
                heading = _text(child)
                if heading:
                    break
        sid = slug(heading) if heading else f"section-{index}"
        base, n = sid, 2
        while sid in used:
            sid, n = f"{base}-{n}", n + 1
        used.add(sid)
        paragraphs = []
        for value in _direct_paragraphs(section):
            paragraph_count += 1
            paragraphs.append({"paragraph_id": f"p{paragraph_count}", "text": value})
        if heading or paragraphs:
            sections.append({
                "section_id": sid,
                "heading": heading or None,
                "scope": section_scope(heading),
                "paragraphs": paragraphs,
            })

    if not sections:
        body_nodes = [e for e in root.iter() if _local(e.tag) in {"body", "sections"}]
        body = body_nodes[0] if body_nodes else root
        paragraphs = []
        for element in body.iter():
            if _local(element.tag) in {"para", "simple-para"}:
                value = _text(element)
                if value:
                    paragraph_count += 1
                    paragraphs.append({"paragraph_id": f"p{paragraph_count}", "text": value})
        if paragraphs:
            sections.append({
                "section_id": "body",
                "heading": None,
                "scope": "other",
                "paragraphs": paragraphs,
            })

    if paragraph_count == 0:
        raise FulltextError("elsevier_fulltext_body_missing")

    references = []
    for index, element in enumerate(
        [e for e in root.iter() if _local(e.tag) in {"bib-reference", "reference"}], 1
    ):
        value = _text(element)
        if value:
            references.append({"reference_id": f"ref{index}", "text": value})

    return {
        "parser": "elsevier-xml-v1",
        "title": title or None,
        "abstract": abstract or None,
        "sections": sections,
        "captions": [],
        "references": references,
        "body_paragraph_count": paragraph_count,
    }
