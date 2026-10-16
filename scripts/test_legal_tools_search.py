"""Offline tests for the standalone LegalToolsSearcher skill.

Run from the skill directory (or its scripts/ folder):
    python -m unittest discover -p 'test_*.py' -v

No test performs a network request; every HTTP call is faked.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import legal_tools_adapter as adapter  # noqa: E402
import legal_tools_search as cli  # noqa: E402


def response(
    body: str | bytes,
    status: int = 200,
    headers: dict[str, str] | None = None,
    url: str = '',
) -> adapter.Response:
    if isinstance(body, str):
        body = body.encode('utf-8')
    return adapter.Response(status, headers or {}, body, url)


def hit(
    external_id: str = 'ICC-01/04-01/12-1-Red',
    slug: str = 'ecfae0',
    title: str = 'Decision on the Prosecutor Application under Article 58',
) -> dict:
    return {
        'hit': {
            'title': title,
            'externalId': external_id,
            'slug': slug,
            'documentUrl': f'http://www.legal-tools.org/doc/{slug}/',
            'pdfURL': f'http://www.legal-tools.org/doc/{slug}/pdf/',
            'orignalPdfURL': 'https://ltd-docs.example/doc.pdf',
            'caseName_en': 'The Prosecutor v. Sylvestre Mudacumura',
            'caseNumber': 'ICC-01/04-01/12',
            'organisation': 'International Criminal Court (ICC)',
            'source': 'Pre-Trial Chamber',
            'judicialDocumentType': 'decision',
            'documentTypeEN': 'Decision',
            'contentType': 'judicial_document',
            'confidentiality': 'public',
            'numberOfPages': 31,
            'isCourtRecord': False,
            'isOriginalLanguage': False,
            'isAuthoritativeLanguage': False,
            'languageIds': ['opaque-language-id'],
            'requireDownloadPermission': False,
            'abstract': 'To be added.',
            'dateCreated': '2012-07-13T00:00:00.000Z',
            'updated': '2026-10-16T00:51:56.243Z',
        },
        'highlight': {
            'caseName_en': ['Sylvestre <span>Mudacumura</span>'],
            'docPages.text': [
                'Decision on the <span>confirmation</span> of charges.'
            ],
        },
    }


class FakeHttp:
    """Route by URL substring; an Exception value means the call failed."""

    def __init__(self, routes: dict[str, object]):
        self.routes = routes
        self.calls: list[tuple[str, str, dict | None]] = []

    def get(self, url, **kwargs):
        self.calls.append(('GET', url, kwargs.get('params')))
        for marker, value in self.routes.items():
            if marker in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise adapter.RetrievalError(f'unexpected URL: {url}')

    def head(self, url, **kwargs):
        self.calls.append(('HEAD', url, None))
        for marker, value in self.routes.items():
            if marker in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise adapter.RetrievalError(f'unexpected URL: {url}')


class FilterTests(unittest.TestCase):
    def test_aliases_and_exact_filters(self):
        where = adapter.build_where(
            organisation='icc',
            case_number='ICC-01/04-01/06',
            document_type='judgement',
        )
        self.assertEqual(
            where['organisation'], 'International Criminal Court (ICC)'
        )
        self.assertEqual(where['caseNumber'], 'ICC-01/04-01/06')
        self.assertEqual(where['judicialDocumentType'], 'judgement')
        self.assertFalse(where['deleted'])

    def test_extra_where_json_is_merged(self):
        where = adapter.build_where(
            where_json='{"collection":"International Criminal Court Documents"}'
        )
        self.assertEqual(
            where['collection'], 'International Criminal Court Documents'
        )

    def test_deleted_true_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Deleted records'):
            adapter.build_where(where_json='{"deleted":true}')

    def test_where_json_must_be_an_object(self):
        with self.assertRaisesRegex(ValueError, 'JSON object'):
            adapter.build_where(where_json='[]')


class SearchTests(unittest.TestCase):
    def test_search_normalizes_urls_and_keeps_metadata_separate_from_text(self):
        payload = {'results': [hit()], 'total': 1}
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    json.dumps(payload), url=adapter.SEARCH_URL
                )
            }
        )
        out = adapter.search(
            'Mudacumura confirmation of charges',
            10,
            0,
            http=http,
        )
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['total'], 1)
        self.assertEqual(out['pagination']['next_offset'], None)
        row = out['results'][0]
        self.assertEqual(row['external_id'], 'ICC-01/04-01/12-1-Red')
        self.assertEqual(row['slug'], 'ecfae0')
        self.assertEqual(
            row['url'], 'https://www.legal-tools.org/doc/ecfae0/'
        )
        self.assertEqual(
            row['pdf_url'], 'https://www.legal-tools.org/doc/ecfae0/pdf/'
        )
        self.assertEqual(row['result_type'], 'retrieved_metadata')
        self.assertIn('confirmation of charges', row['snippet'])
        self.assertNotIn('<span>', row['snippet'])

    def test_search_passes_encoded_filter_and_term(self):
        payload = {'results': [], 'total': 0}
        url = adapter.SEARCH_URL
        http = FakeHttp({url: response(json.dumps(payload), url=url)})
        adapter.search(
            'command responsibility',
            5,
            10,
            organisation='icc',
            http=http,
        )
        method, called_url, params = http.calls[0]
        self.assertEqual(method, 'GET')
        self.assertEqual(called_url, url)
        self.assertEqual(params['term'], 'command responsibility')
        where = json.loads(params['filter'])['where']
        self.assertEqual(where['organisation'], 'International Criminal Court (ICC)')
        self.assertEqual(json.loads(params['filter'])['from'], 10)

    def test_next_offset_advances_when_results_remain(self):
        payload = {
            'results': [hit(external_id=f'ID-{i}') for i in range(2)],
            'total': 3,
        }
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    json.dumps(payload), url=adapter.SEARCH_URL
                )
            }
        )
        out = adapter.search('x', 2, 0, http=http)
        self.assertEqual(out['pagination']['next_offset'], 2)

    def test_final_page_does_not_advertise_an_offset_past_total(self):
        payload = {'results': [hit()], 'total': 3}
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    json.dumps(payload), url=adapter.SEARCH_URL
                )
            }
        )
        out = adapter.search('x', 1, 2, http=http)
        self.assertIsNone(out['pagination']['next_offset'])

    def test_confirmed_zero_is_ok(self):
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    json.dumps({'results': [], 'total': 0}),
                    url=adapter.SEARCH_URL,
                )
            }
        )
        out = adapter.search('zzzxnonexistent785132904', 5, 0, http=http)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['total'], 0)
        self.assertEqual(out['results'], [])
        self.assertIsNone(out['pagination']['next_offset'])

    def test_schema_change_is_not_reported_as_zero_hits(self):
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    '{"message":"changed"}', url=adapter.SEARCH_URL
                )
            }
        )
        with self.assertRaisesRegex(adapter.RetrievalError, 'results array missing'):
            adapter.search('x', 5, 0, http=http)


class DocumentTests(unittest.TestCase):
    def test_document_resolution_returns_metadata_and_pdf_url(self):
        http = FakeHttp(
            {
                adapter.FIND_ONE_URL: response(
                    json.dumps(hit()['hit']), url=adapter.FIND_ONE_URL
                )
            }
        )
        out = adapter.document('ICC-01/04-01/12-1-Red', http=http)
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['document']['external_id'], 'ICC-01/04-01/12-1-Red')
        self.assertEqual(
            out['document']['pdf_url'],
            'https://www.legal-tools.org/doc/ecfae0/pdf/',
        )
        params = http.calls[0][2]
        where = json.loads(params['filter'])['where']
        self.assertEqual(where['externalId'], 'ICC-01/04-01/12-1-Red')

    def test_document_not_found_is_distinct_from_transport_failure(self):
        http = FakeHttp(
            {
                adapter.FIND_ONE_URL: adapter.NotFound(
                    f'HTTP 404: {adapter.FIND_ONE_URL}'
                )
            }
        )
        out = adapter.document('zzz-nonexistent', http=http)
        self.assertEqual(out['status'], 'not-found')
        self.assertIn('does not prove', out['warnings'][0])


class LocatorTests(unittest.TestCase):
    def test_url_and_bare_slug(self):
        self.assertEqual(
            adapter.slug_from_locator(
                'https://www.legal-tools.org/doc/ecfae0/pdf/'
            ),
            'ecfae0',
        )
        self.assertEqual(adapter.slug_from_locator('ECFAE0'), 'ecfae0')

    def test_external_id_is_not_treated_as_a_slug(self):
        self.assertEqual(
            adapter.slug_from_locator('ICC-01/04-01/12-1-Red'), ''
        )

    def test_foreign_host_is_rejected(self):
        with self.assertRaisesRegex(adapter.RetrievalError, 'Not a Legal Tools URL'):
            adapter.slug_from_locator('https://example.org/doc/ecfae0/')


class FetchTests(unittest.TestCase):
    def test_fetch_by_external_id_downloads_and_extracts_pdf(self):
        pdf = b'%PDF-1.3\nfixture'
        find_payload = hit()['hit']
        http = FakeHttp(
            {
                adapter.FIND_ONE_URL: response(
                    json.dumps(find_payload), url=adapter.FIND_ONE_URL
                ),
                'https://www.legal-tools.org/doc/ecfae0/pdf/': response(
                    pdf,
                    headers={'Content-Type': 'application/pdf'},
                    url='https://www.legal-tools.org/doc/ecfae0/pdf/',
                ),
            }
        )
        with mock.patch.object(
            adapter,
            'pdf_pages',
            return_value=[{'page': 1, 'text': 'Paragraph 1.'}],
        ):
            out = adapter.fetch(
                'ICC-01/04-01/12-1-Red', http=http, timeout=60
            )
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(out['external_id'], 'ICC-01/04-01/12-1-Red')
        self.assertEqual(out['pdf']['pages'], 1)
        self.assertFalse(out['pdf']['needs_ocr'])
        self.assertEqual(out['pages'][0]['text'], 'Paragraph 1.')
        self.assertEqual(len(out['evidence']), 2)

    def test_fetch_by_slug_writes_pdf_and_page_marked_text(self):
        pdf = b'%PDF-1.3\nfixture'
        http = FakeHttp(
            {
                'https://www.legal-tools.org/doc/ecfae0/pdf/': response(
                    pdf,
                    headers={'Content-Type': 'application/pdf'},
                    url='https://www.legal-tools.org/doc/ecfae0/pdf/',
                )
            }
        )
        with tempfile.TemporaryDirectory() as folder:
            pdf_path = Path(folder) / 'document.pdf'
            text_path = Path(folder) / 'document.txt'
            with mock.patch.object(
                adapter,
                'pdf_pages',
                return_value=[{'page': 1, 'text': 'First page.'}],
            ):
                out = adapter.fetch(
                    'ecfae0',
                    http=http,
                    pdf_out=str(pdf_path),
                    text_out=str(text_path),
                )
            self.assertEqual(pdf_path.read_bytes(), pdf)
            self.assertEqual(
                text_path.read_text(encoding='utf-8'),
                '@@PAGE 1@@\nFirst page.\n',
            )
            self.assertEqual(out['external_id'], '')
            self.assertEqual(out['pdf']['pdf_out'], str(pdf_path.resolve()))

    def test_fetch_not_found_returns_status(self):
        http = FakeHttp(
            {
                adapter.FIND_ONE_URL: adapter.NotFound(
                    f'HTTP 404: {adapter.FIND_ONE_URL}'
                )
            }
        )
        out = adapter.fetch('zzz-nonexistent', http=http)
        self.assertEqual(out['status'], 'not-found')
        self.assertEqual(out['evidence'][0]['http_status'], 404)

    def test_non_pdf_response_is_an_error(self):
        http = FakeHttp(
            {
                'https://www.legal-tools.org/doc/ecfae0/pdf/': response(
                    '<html>access denied</html>',
                    headers={'Content-Type': 'text/html'},
                    url='https://www.legal-tools.org/doc/ecfae0/pdf/',
                )
            }
        )
        with self.assertRaisesRegex(adapter.RetrievalError, 'did not return a PDF'):
            adapter.fetch('ecfae0', http=http)


class PdfTests(unittest.TestCase):
    def test_pdf_pages_requires_a_backend_when_none_is_importable(self):
        with mock.patch.dict(
            sys.modules, {'pymupdf': None, 'pypdf': None}
        ):
            with self.assertRaisesRegex(
                adapter.RetrievalError, 'requires PyMuPDF or pypdf'
            ):
                adapter.pdf_pages(b'%PDF-1.3\nfixture')


class DoctorTests(unittest.TestCase):
    def test_doctor_without_probe_checks_dependencies(self):
        out = adapter.doctor(probe=False)
        self.assertIn(out['status'], ('ok', 'degraded'))
        self.assertTrue(out['checks']['python']['ok'])
        self.assertTrue(out['checks']['pdf_backend']['ok'])

    def test_doctor_probes_search_and_pdf_route(self):
        search_payload = {'results': [hit()], 'total': 1}
        http = FakeHttp(
            {
                adapter.SEARCH_URL: response(
                    json.dumps(search_payload), url=adapter.SEARCH_URL
                ),
                'pdf': response(
                    b'',
                    headers={'Content-Type': 'application/pdf'},
                    url='https://www.legal-tools.org/doc/ecfae0/pdf/',
                ),
            }
        )
        out = adapter.doctor(probe=True, http=http, timeout=10)
        self.assertTrue(out['checks']['search_api']['ok'])
        self.assertTrue(out['checks']['pdf_route']['ok'])
        self.assertTrue(out['ok'])


class CliTests(unittest.TestCase):
    def test_search_writes_report_and_prints_summary(self):
        payload = {
            'skill': adapter.SKILL_NAME,
            'command': 'search',
            'status': 'ok',
            'total': 13,
            'pagination': {'next_offset': 10},
            'warnings': [],
            'results': [{'title': 'Decision'}],
        }
        with tempfile.TemporaryDirectory() as folder:
            out_path = Path(folder) / 'search.json'
            captured = io.StringIO()
            with mock.patch.object(cli.adapter, 'search', return_value=payload):
                with contextlib.redirect_stdout(captured):
                    code = cli.main(
                        ['search', 'Mudacumura', '--out', str(out_path)]
                    )
            self.assertEqual(code, 0)
            written = json.loads(out_path.read_text(encoding='utf-8'))
            self.assertEqual(written['total'], 13)
            summary = json.loads(captured.getvalue().strip())
            self.assertEqual(summary['results'], 1)
            self.assertEqual(summary['next_offset'], 10)

    def test_filter_only_search_requires_all(self):
        captured = io.StringIO()
        with contextlib.redirect_stderr(captured):
            with self.assertRaises(SystemExit) as raised:
                cli.main(
                    ['search', '--organisation', 'icc', '--limit', '1']
                )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('--all', captured.getvalue())

    def test_fetch_error_report_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as folder:
            out_path = Path(folder) / 'error.json'
            captured = io.StringIO()
            with mock.patch.object(
                cli.adapter,
                'fetch',
                side_effect=adapter.RetrievalError('HTTP 403: blocked'),
            ):
                with contextlib.redirect_stdout(captured):
                    code = cli.main(
                        ['fetch', 'ecfae0', '--out', str(out_path)]
                    )
            self.assertEqual(code, 1)
            written = json.loads(out_path.read_text(encoding='utf-8'))
            self.assertEqual(written['status'], 'error')
            self.assertIn('HTTP 403', written['error'])

    def test_plain_output_is_json(self):
        payload = {
            'skill': adapter.SKILL_NAME,
            'command': 'document',
            'status': 'not-found',
            'external_id': 'zzz',
            'warnings': [],
        }
        captured = io.StringIO()
        with mock.patch.object(cli.adapter, 'document', return_value=payload):
            with contextlib.redirect_stdout(captured):
                code = cli.main(['document', 'zzz'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(captured.getvalue())['status'], 'not-found')


if __name__ == '__main__':
    unittest.main()
