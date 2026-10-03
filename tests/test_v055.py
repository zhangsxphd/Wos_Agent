import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.fulltext.acquisition_router import AcquisitionRouter
from scripts.fulltext.elsevier_content import ElsevierArticleClient
from scripts.fulltext.elsevier_xml import parse_elsevier_xml
from scripts.fulltext.provenance import FulltextError


FULL_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<full-text-retrieval-response xmlns:ce="http://www.elsevier.com/xml/common/dtd">
  <title>Example saline rice study</title>
  <abstract><ce:para>Abstract text.</ce:para></abstract>
  <ce:sections>
    <ce:section>
      <ce:section-title>Methods</ce:section-title>
      <ce:para>Three treatments were tested.</ce:para>
    </ce:section>
    <ce:section>
      <ce:section-title>Results</ce:section-title>
      <ce:para>Yield increased under treatment A.</ce:para>
    </ce:section>
  </ce:sections>
</full-text-retrieval-response>
"""


class FakeResponse:
    def __init__(self, status=200, content=FULL_XML, headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}
    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)
    def close(self):
        pass


class NullOAResolver:
    def resolve(self, record, work=None, refresh=False):
        return {
            "uid": record.get("uid"),
            "doi": record.get("doi"),
            "status": "unavailable",
            "source": None,
            "format": None,
            "reason": "no_public_content_location",
        }


class TestElsevierV055(unittest.TestCase):
    def test_parse_full_xml(self):
        parsed = parse_elsevier_xml(FULL_XML)
        self.assertEqual(parsed["parser"], "elsevier-xml-v1")
        self.assertEqual(parsed["body_paragraph_count"], 2)
        self.assertEqual(parsed["sections"][0]["scope"], "methods")
        self.assertEqual(parsed["sections"][1]["scope"], "results")

    def test_reject_abstract_only_xml(self):
        with self.assertRaises(FulltextError) as ctx:
            parse_elsevier_xml(b"<root><abstract>Only metadata</abstract></root>")
        self.assertEqual(ctx.exception.code, "elsevier_fulltext_body_missing")

    def test_external_doctype_is_stripped_safely(self):
        xml = FULL_XML.replace(
            b"<full-text-retrieval-response",
            b'<!DOCTYPE article SYSTEM "https://example.invalid/article.dtd"><full-text-retrieval-response',
            1,
        )
        self.assertEqual(parse_elsevier_xml(xml)["body_paragraph_count"], 2)

    def test_internal_entity_is_rejected(self):
        xml = b'<!DOCTYPE article [<!ENTITY x "unsafe">]>' + FULL_XML
        with self.assertRaises(FulltextError) as ctx:
            parse_elsevier_xml(xml)
        self.assertEqual(ctx.exception.code, "unsafe_xml")

    def test_abstract_para_is_not_treated_as_full_text(self):
        xml = b"<root><abstract><para>Abstract only.</para></abstract></root>"
        with self.assertRaises(FulltextError) as ctx:
            parse_elsevier_xml(xml)
        self.assertEqual(ctx.exception.code, "elsevier_fulltext_body_missing")

    def test_client_uses_header_not_query_key(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([FakeResponse()])
            client = ElsevierArticleClient(root=tmp, session=session, sleep=lambda _: None)
            raw, meta = client.retrieve_xml("10.1016/j.example.2026.1")
            self.assertEqual(raw, FULL_XML)
            url, kwargs = session.calls[0]
            self.assertNotIn("unit-test-elsevier-key", url)
            self.assertEqual(kwargs["headers"]["X-ELS-APIKey"], "unit-test-elsevier-key")
            self.assertEqual(kwargs["params"], {"view": "FULL"})
            self.assertNotIn("unit-test-elsevier-key", str(meta))
            client.close()

    def test_access_denied_is_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([FakeResponse(status=403)])
            client = ElsevierArticleClient(root=tmp, session=session, sleep=lambda _: None)
            with self.assertRaises(FulltextError) as ctx:
                client.retrieve_xml("10.1016/j.example.2026.1")
            self.assertEqual(ctx.exception.code, "elsevier_access_denied")
            self.assertEqual(len(session.calls), 1)

    def test_429_retries_then_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([
                FakeResponse(status=429, headers={"Retry-After": "0"}),
                FakeResponse(status=200),
            ])
            client = ElsevierArticleClient(
                root=tmp, session=session, sleep=lambda _: None, retries=1, min_interval=0
            )
            raw, _ = client.retrieve_xml("10.1016/j.example.2026.1")
            self.assertEqual(raw, FULL_XML)
            self.assertEqual(len(session.calls), 2)

    def test_rate_limit_headers_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            headers = {
                "X-RateLimit-Limit": "100",
                "X-RateLimit-Remaining": "98",
                "X-RateLimit-Credits-Used": "2",
                "X-RateLimit-Reset": "2030-01-01T00:00:00Z",
            }
            client = ElsevierArticleClient(
                root=tmp, session=FakeSession([FakeResponse(headers=headers)]), sleep=lambda _: None
            )
            client.retrieve_xml("10.1016/j.example.2026.1")
            self.assertEqual(client.rate_limit_history[0]["headers"], headers)

    def test_router_falls_back_to_elsevier(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([FakeResponse(status=200)])
            client = ElsevierArticleClient(root=tmp, session=session, sleep=lambda _: None)
            router = AcquisitionRouter(
                root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client
            )
            result = router.resolve({
                "uid": "WOS:TEST",
                "doi": "10.1016/j.example.2026.1",
            })
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["acquisition_route"], "elsevier_api")
            self.assertEqual(result["format"], "elsevier_xml")
            self.assertTrue((Path(tmp) / result["raw_file"]).exists())
            self.assertTrue((Path(tmp) / result["parsed_file"]).exists())
            self.assertNotIn("unit-test-elsevier-key", str(result))

    def test_router_rejects_a_title_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            client = ElsevierArticleClient(
                root=tmp, session=FakeSession([FakeResponse(status=200)]), sleep=lambda _: None
            )
            router = AcquisitionRouter(root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client)
            result = router.resolve({
                "uid": "WOS:TEST",
                "doi": "10.1016/j.example.2026.1",
                "title": "A completely different article title",
            })
            self.assertEqual(result["reason"], "elsevier_record_mismatch")
            self.assertEqual(result["http_status"], 200)
            self.assertFalse(list(Path(tmp, "data/fulltext/raw").glob("*")))

    def test_successful_elsevier_result_replays_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([FakeResponse(status=200)])
            client = ElsevierArticleClient(root=tmp, session=session, sleep=lambda _: None)
            router = AcquisitionRouter(
                root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client
            )
            record = {"uid": "WOS:TEST", "doi": "10.1016/j.example.2026.1"}
            first = router.resolve(record)
            first_parsed = Path(tmp, first["parsed_file"]).read_bytes()
            replay = router.resolve(record)
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(replay["cache_hit"])
            self.assertEqual(Path(tmp, replay["parsed_file"]).read_bytes(), first_parsed)
            self.assertEqual(router.stats["cache_hits"], 1)

    def test_cache_replay_checks_the_title_against_the_record(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "unit-test-elsevier-key"}, clear=False
        ):
            session = FakeSession([FakeResponse(), FakeResponse(status=403)])
            client = ElsevierArticleClient(root=tmp, session=session, sleep=lambda _: None)
            router = AcquisitionRouter(root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client)
            record = {
                "uid": "WOS:TEST",
                "doi": "10.1016/j.example.2026.1",
                "title": "Example saline rice study",
            }
            router.resolve(record)
            changed = dict(record, title="Different title")
            replay = router.resolve(changed)
            self.assertEqual(len(session.calls), 2)
            self.assertEqual(replay["reason"], "elsevier_access_denied")
            self.assertEqual(router.stats["cache_hits"], 0)

    def test_credentials_do_not_enter_cache_manifest_or_logs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ,
            {
                "ELSEVIER_API_KEY": "unit-test-elsevier-key",
                "ELSEVIER_INSTTOKEN": "unit-test-inst-token",
            },
            clear=False,
        ):
            client = ElsevierArticleClient(root=tmp, session=FakeSession([FakeResponse()]), sleep=lambda _: None)
            router = AcquisitionRouter(root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client)
            output = StringIO()
            with redirect_stdout(output):
                router.resolve({"uid": "WOS:TEST", "doi": "10.1016/j.example.2026.1"})
            contents = []
            for path in Path(tmp).rglob("*"):
                if path.is_file():
                    contents.append(path.read_bytes().decode("utf-8", errors="ignore"))
            joined = "\n".join(contents) + output.getvalue()
            self.assertNotIn("unit-test-elsevier-key", joined)
            self.assertNotIn("unit-test-inst-token", joined)

    def test_missing_key_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"ELSEVIER_API_KEY": "", "ELSEVIER_INSTTOKEN": ""}, clear=False
        ):
            client = ElsevierArticleClient(root=tmp, session=FakeSession([]))
            router = AcquisitionRouter(
                root=tmp, oa_resolver=NullOAResolver(), elsevier_client=client
            )
            result = router.resolve({"uid": "WOS:TEST", "doi": "10.1016/j.example.2026.1"})
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["router_attempts"][-1]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
