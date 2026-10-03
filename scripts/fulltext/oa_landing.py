"""One OA landing page, authoritative PDF metadata only; no recursive crawling."""
from html.parser import HTMLParser
from urllib.parse import urljoin
from .provenance import safe_url,FulltextError


class PDFLinks(HTMLParser):
    def __init__(self): super().__init__();self.links=[]
    def handle_starttag(self,tag,attrs):
        a={k.lower():v for k,v in attrs}
        if tag=='meta' and (a.get('name') or '').casefold()=='citation_pdf_url' and a.get('content'):
            self.links.append(a['content'])
        if tag=='link' and (a.get('type') or '').casefold()=='application/pdf' and a.get('href'):
            self.links.append(a['href'])


def pdf_links(raw,base,secrets=()):
    if len(raw)>5_000_000: raise FulltextError('landing_page_too_large')
    parser=PDFLinks();parser.feed(raw.decode('utf-8',errors='replace'))
    links=[]
    for url in parser.links:
        try: result=safe_url(urljoin(base,url),secrets)
        except (ValueError,FulltextError): continue
        if result not in links: links.append(result)
    return links[:2]
